"""Global keyboard shortcuts via evdev."""

import logging
import select
import threading
import time
from typing import Callable, Optional

import evdev

log = logging.getLogger(__name__)
from evdev import InputDevice, categorize, ecodes

KEY_ALIASES = {
    "ctrl": "KEY_LEFTCTRL", "control": "KEY_LEFTCTRL",
    "alt": "KEY_LEFTALT", "super": "KEY_LEFTMETA", "meta": "KEY_LEFTMETA",
    "shift": "KEY_LEFTSHIFT",
    "d": "KEY_D", "t": "KEY_T", "s": "KEY_S",
    "a": "KEY_A", "b": "KEY_B", "c": "KEY_C", "e": "KEY_E", "f": "KEY_F",
    "g": "KEY_G", "h": "KEY_H", "i": "KEY_I", "j": "KEY_J", "k": "KEY_K",
    "l": "KEY_L", "m": "KEY_M", "n": "KEY_N", "o": "KEY_O", "p": "KEY_P",
    "q": "KEY_Q", "r": "KEY_R", "u": "KEY_U", "v": "KEY_V", "w": "KEY_W",
    "x": "KEY_X", "y": "KEY_Y", "z": "KEY_Z",
    "esc": "KEY_ESC", "escape": "KEY_ESC",
    "enter": "KEY_ENTER", "return": "KEY_ENTER",
}

MODIFIER_KEYS = {
    ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL,
    ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT,
    ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT,
    ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA,
}


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


class ShortcutManager:
    """Listens for multiple shortcuts and invokes callbacks."""

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
