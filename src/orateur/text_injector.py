"""Text injection via clipboard + synthetic paste (ydotool on Linux, pynput on macOS/Windows)."""

import logging
import shutil
import subprocess
import sys
import time

log = logging.getLogger(__name__)


def _paste_hotkey_pynput() -> bool:
    """Cmd+V (macOS) or Ctrl+V (Windows). Requires Accessibility (mac) / similar for synthetic input."""
    try:
        from pynput.keyboard import Controller, Key

        ctrl = Controller()
        mod = Key.cmd if sys.platform == "darwin" else Key.ctrl
        with ctrl.pressed(mod):
            ctrl.tap("v")
        return True
    except Exception as e:
        log.warning("Paste hotkey (pynput): %s", e)
        return False


class TextInjector:
    """Inject text into focused app via clipboard + paste hotkey."""

    def __init__(self, config=None):
        self.config = config
        self.ydotool_available = shutil.which("ydotool") is not None

    def inject_text(self, text: str) -> bool:
        if not text or not text.strip():
            return True
        text = text.strip()
        try:
            if shutil.which("wl-copy"):
                subprocess.run(["wl-copy"], input=text.encode("utf-8"), check=True)
            else:
                try:
                    import pyperclip

                    pyperclip.copy(text)
                except ImportError:
                    if shutil.which("xclip"):
                        subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode("utf-8"), check=True)
                    else:
                        log.warning("No clipboard tool (wl-copy/xclip/pyperclip)")
                        return False
            time.sleep(0.12)
            if sys.platform in ("darwin", "win32"):
                if not _paste_hotkey_pynput():
                    return False
            elif self.ydotool_available:
                mode = self.config.get_setting("paste_mode", "ctrl_shift") if self.config else "ctrl_shift"
                keycode = self.config.get_setting("paste_keycode", 47) if self.config else 47
                kp, kr = f"{keycode}:1", f"{keycode}:0"
                if mode == "super":
                    subprocess.run(["ydotool", "key", "125:1", kp, kr, "125:0"], capture_output=True, timeout=5)
                elif mode == "ctrl_shift":
                    subprocess.run(
                        ["ydotool", "key", "29:1", "42:1", kp, kr, "42:0", "29:0"],
                        capture_output=True,
                        timeout=5,
                    )
                else:
                    subprocess.run(["ydotool", "key", "29:1", kp, kr, "29:0"], capture_output=True, timeout=5)
            else:
                log.warning("No paste simulator (install ydotool on Linux, or run on macOS/Windows with pynput)")
                return False
            return True
        except Exception as e:
            log.warning("Failed: %s", e)
            return False
