"""Global keyboard shortcuts: Linux uses evdev; macOS and Windows use pynput."""

from __future__ import annotations

import logging
import re
import sys
import threading
import time
import unicodedata
from typing import Callable, Optional

log = logging.getLogger(__name__)

KEY_ALIASES = {
    "ctrl": "KEY_LEFTCTRL",
    "control": "KEY_LEFTCTRL",
    "alt": "KEY_LEFTALT",
    "super": "KEY_LEFTMETA",
    "meta": "KEY_LEFTMETA",
    "shift": "KEY_LEFTSHIFT",
    "d": "KEY_D",
    "t": "KEY_T",
    "s": "KEY_S",
    "a": "KEY_A",
    "b": "KEY_B",
    "c": "KEY_C",
    "e": "KEY_E",
    "f": "KEY_F",
    "g": "KEY_G",
    "h": "KEY_H",
    "i": "KEY_I",
    "j": "KEY_J",
    "k": "KEY_K",
    "l": "KEY_L",
    "m": "KEY_M",
    "n": "KEY_N",
    "o": "KEY_O",
    "p": "KEY_P",
    "q": "KEY_Q",
    "r": "KEY_R",
    "u": "KEY_U",
    "v": "KEY_V",
    "w": "KEY_W",
    "x": "KEY_X",
    "y": "KEY_Y",
    "z": "KEY_Z",
    "esc": "KEY_ESC",
    "escape": "KEY_ESC",
    "enter": "KEY_ENTER",
    "return": "KEY_ENTER",
}

# Modifiers for pynput GlobalHotKeys strings (<cmd> = Super / ⌘ / Win).
_MODIFIER_PYNPUT = {
    "ctrl": "<ctrl>",
    "control": "<ctrl>",
    "alt": "<alt>",
    "shift": "<shift>",
    "super": "<cmd>",
    "meta": "<cmd>",
    "win": "<cmd>",
    "cmd": "<cmd>",
    "command": "<cmd>",
}

_SPECIAL_PYNPUT = {
    "esc": "<esc>",
    "escape": "<esc>",
    "enter": "<enter>",
    "return": "<enter>",
    "tab": "<tab>",
    "space": "<space>",
    "backspace": "<backspace>",
    "delete": "<delete>",
    "insert": "<insert>",
    "home": "<home>",
    "end": "<end>",
    "page_up": "<page_up>",
    "page_down": "<page_down>",
    "up": "<up>",
    "down": "<down>",
    "left": "<left>",
    "right": "<right>",
}


def _normalize_shortcut_token(p: str) -> str:
    """Normalize one key name from config (typos, smart quotes, stray punctuation)."""
    p = unicodedata.normalize("NFKC", p).strip().lower()
    # Stray marks after words (e.g. "space¡" breaks pynput's <space> parse)
    p = re.sub(r"[!¡?]+$", "", p)
    return p.strip("'\"")


def _shortcut_to_pynput(s: str) -> Optional[str]:
    s = s.lower().strip().replace("+", " ").replace("-", " ")
    parts = [_normalize_shortcut_token(p) for p in s.split() if p]
    parts = [p for p in parts if p]
    if not parts:
        return None
    chunks: list[str] = []
    for p in parts:
        if p in _MODIFIER_PYNPUT:
            chunks.append(_MODIFIER_PYNPUT[p])
        elif p in _SPECIAL_PYNPUT:
            chunks.append(_SPECIAL_PYNPUT[p])
        elif len(p) == 1 and p.isalpha():
            chunks.append(p.lower())
        elif p.startswith("f") and len(p) > 1 and p[1:].isdigit():
            chunks.append(f"<{p}>")
        else:
            chunks.append(f"<{p}>")
    return "+".join(chunks)


if sys.platform == "linux":
    import select

    import evdev
    from evdev import InputDevice, categorize, ecodes

    def _parse_shortcut(s: str) -> frozenset:
        s = s.lower().strip().replace("+", " ").replace("-", " ")
        parts = s.split()
        keys = set()
        for p in parts:
            name = KEY_ALIASES.get(p) or f"KEY_{p.upper()}"
            code = ecodes.ecodes.get(name)
            if code is not None:
                keys.add(code)
        return frozenset(keys) if keys else frozenset({ecodes.KEY_F12})

    MODIFIER_KEYS = {
        ecodes.KEY_LEFTCTRL,
        ecodes.KEY_RIGHTCTRL,
        ecodes.KEY_LEFTALT,
        ecodes.KEY_RIGHTALT,
        ecodes.KEY_LEFTSHIFT,
        ecodes.KEY_RIGHTSHIFT,
        ecodes.KEY_LEFTMETA,
        ecodes.KEY_RIGHTMETA,
    }

    class EvdevShortcutManager:
        """Listens for multiple shortcuts and invokes callbacks (Linux /dev/input)."""

        def __init__(self, config):
            self.config = config
            self.shortcuts: dict[str, tuple[frozenset, Callable]] = {}
            self.devices: list[InputDevice] = []
            self.pressed_keys: set[int] = set()
            self.active: dict[str, bool] = {}
            self.last_trigger: dict[str, float] = {}
            self.debounce = 0.1
            self.stop_event = threading.Event()
            self.thread: Optional[threading.Thread] = None

        def register(self, name: str, shortcut: Optional[str], callback: Callable[[], None]) -> None:
            if not shortcut:
                return
            keys = _parse_shortcut(shortcut)
            self.shortcuts[name] = (keys, callback)
            self.active[name] = False
            self.last_trigger[name] = 0.0

        def _discover(self) -> bool:
            all_keys = set()
            for keys, _ in self.shortcuts.values():
                all_keys.update(keys)
            self.devices = []
            try:
                for path in evdev.list_devices():
                    try:
                        dev = InputDevice(path)
                        if ecodes.EV_KEY not in dev.capabilities():
                            dev.close()
                            continue
                        avail = set(dev.capabilities()[ecodes.EV_KEY])
                        if not all_keys.issubset(avail):
                            dev.close()
                            continue
                        path_cfg = self.config.get_setting("selected_device_path")
                        name_cfg = self.config.get_setting("selected_device_name")
                        if path_cfg and dev.path != path_cfg:
                            dev.close()
                            continue
                        if name_cfg and name_cfg.lower() not in dev.name.lower():
                            dev.close()
                            continue
                        self.devices.append(dev)
                        if path_cfg or name_cfg:
                            break
                    except Exception:
                        pass
                return len(self.devices) > 0
            except Exception as e:
                log.error("Discover error: %s", e)
                return False

        def _event_loop(self) -> None:
            while not self.stop_event.is_set():
                if not self.devices:
                    time.sleep(0.1)
                    continue
                fds = [d.fd for d in self.devices]
                try:
                    r, _, _ = select.select(fds, [], [], 0.1)
                except Exception:
                    break
                for fd in r:
                    dev = next((d for d in self.devices if d.fd == fd), None)
                    if not dev:
                        continue
                    try:
                        for event in dev.read():
                            if event.type != ecodes.EV_KEY:
                                continue
                            try:
                                kev = categorize(event)
                            except KeyError:
                                continue
                            if kev.keystate == 1:
                                self.pressed_keys.add(event.code)
                                for name, (keys, cb) in list(self.shortcuts.items()):
                                    if keys.issubset(self.pressed_keys):
                                        extra = (self.pressed_keys - keys) & MODIFIER_KEYS
                                        if not extra:
                                            if not self.active.get(name, False):
                                                if time.time() - self.last_trigger.get(name, 0) > self.debounce:
                                                    self.last_trigger[name] = time.time()
                                                    self.active[name] = True
                                                    try:
                                                        threading.Thread(target=cb, daemon=True).start()
                                                    except Exception as e:
                                                        log.warning("%s: %s", name, e)
                            elif kev.keystate == 0:
                                self.pressed_keys.discard(event.code)
                                for name in self.shortcuts:
                                    if not self.shortcuts[name][0].issubset(self.pressed_keys):
                                        self.active[name] = False
                    except (OSError, IOError):
                        break

        def start(self) -> bool:
            if not self._discover():
                log.error("No keyboard devices found")
                return False
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._event_loop, daemon=True)
            self.thread.start()
            return True

        def stop(self) -> None:
            self.stop_event.set()
            if self.thread:
                self.thread.join(timeout=2.0)
            for d in self.devices:
                try:
                    d.close()
                except Exception:
                    pass
            self.devices = []

    ShortcutManager = EvdevShortcutManager

elif sys.platform in ("darwin", "win32"):

    class PynputShortcutManager:
        """Listens for global hotkeys via pynput (macOS / Windows)."""

        def __init__(self, config):
            self.config = config
            self._hotkey_map: dict[str, Callable[[], None]] = {}
            self.last_trigger: dict[str, float] = {}
            self.debounce = 0.1
            self._listener = None
            self._lock = threading.Lock()

        def register(self, name: str, shortcut: Optional[str], callback: Callable[[], None]) -> None:
            if not shortcut:
                log.warning("Shortcut %r is empty in config — not registered", name)
                return
            combo = _shortcut_to_pynput(shortcut)
            if not combo:
                log.error(
                    "Shortcut %r could not be parsed (check spelling): %r — not registered",
                    name,
                    shortcut,
                )
                return

            def make_handler(cb: Callable[[], None], nm: str) -> Callable[[], None]:
                def wrapped() -> None:
                    with self._lock:
                        now = time.time()
                        if now - self.last_trigger.get(nm, 0) <= self.debounce:
                            return
                        self.last_trigger[nm] = now
                    try:
                        threading.Thread(target=cb, daemon=True).start()
                    except Exception as e:
                        log.warning("%s: %s", nm, e)

                return wrapped

            self._hotkey_map[combo] = make_handler(callback, name)
            self.last_trigger[name] = 0.0

        def start(self) -> bool:
            if not self._hotkey_map:
                log.error("No shortcuts registered")
                return False
            try:
                from pynput import keyboard
            except ImportError:
                log.error("pynput is required for global shortcuts on this platform; install dependencies and retry.")
                return False
            try:
                self._listener = keyboard.GlobalHotKeys(self._hotkey_map)
                self._listener.start()
                log.info(
                    "pynput global hotkeys started (%d): %s",
                    len(self._hotkey_map),
                    ", ".join(sorted(self._hotkey_map.keys())),
                )
            except Exception as e:
                log.error("Could not start global hotkeys: %s", e)
                log.error(
                    "Registered pynput combos (fix typos in config.json if needed): %s",
                    list(self._hotkey_map.keys()),
                )
                self._listener = None
                return False
            return True

        def stop(self) -> None:
            if self._listener is not None:
                try:
                    self._listener.stop()
                except Exception:
                    pass
                try:
                    self._listener.join(timeout=2.0)
                except Exception:
                    pass
                self._listener = None

    ShortcutManager = PynputShortcutManager

else:

    class UnsupportedShortcutManager:
        """Placeholder when the platform has no backend."""

        def __init__(self, config):
            self.config = config

        def register(self, name: str, shortcut: Optional[str], callback: Callable[[], None]) -> None:
            pass

        def start(self) -> bool:
            log.error(
                "Global shortcuts are only supported on Linux (evdev), macOS, and Windows (pynput); "
                "this platform (%s) is not supported.",
                sys.platform,
            )
            return False

        def stop(self) -> None:
            pass

    ShortcutManager = UnsupportedShortcutManager
