"""
Mirror UI events to ~/.cache/orateur/ui_events.jsonl when ``ui_events_mirror`` is enabled.

Any client can follow this file (e.g. Quickshell ``tail -F``, the Tauri desktop app). Lines are
NDJSON with the same shape as ``orateur ui`` stdout: ``{"event": "...", ...}``.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from .config import ConfigManager
from .paths import CACHE_DIR, UI_EVENTS_JSONL

log = logging.getLogger(__name__)

_lock = threading.Lock()


def _mirror_enabled(config: ConfigManager) -> bool:
    return bool(config.get_setting("ui_events_mirror", True))


def _json_default(obj: Any) -> Any:
    if hasattr(obj, "item") and callable(obj.item):
        try:
            return obj.item()
        except Exception:
            pass
    if isinstance(obj, (bytes, bytearray)):
        return obj.decode("utf-8", errors="replace")
    raise TypeError(f"not JSON serializable: {type(obj)!r}")


def reset_ui_events_file() -> None:
    """Clear the JSONL file (unlink + recreate) so tail -F clients see a fresh file."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if UI_EVENTS_JSONL.exists():
            UI_EVENTS_JSONL.unlink()
        UI_EVENTS_JSONL.write_text("", encoding="utf-8")
    except OSError as e:
        log.warning("Could not reset %s: %s", UI_EVENTS_JSONL, e)


def send(config: ConfigManager, event: str, **payload: Any) -> None:
    """Append one UI event line (non-blocking aside from a short file lock)."""
    if not _mirror_enabled(config):
        return
    msg: dict[str, Any] = {"event": event}
    msg.update(payload)
    try:
        line = json.dumps(msg, separators=(",", ":"), default=_json_default)
    except TypeError as e:
        log.warning("ui_mirror JSON skip (%s): %s", event, e)
        return
    with _lock:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with open(UI_EVENTS_JSONL, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
        except OSError as e:
            log.debug("ui_mirror append: %s", e)
