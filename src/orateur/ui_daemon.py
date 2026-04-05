"""
Orateur UI daemon: JSON-RPC over FIFO (commands) and stdout (events).

Reads commands from ~/.cache/orateur/cmd.fifo, writes events to stdout.
Used by the Quickshell OrateurWidget.

Use ``orateur ui --events-only`` with Quickshell when ``orateur run`` handles STT/TTS
(one model load). Full ``orateur ui`` loads STT/TTS/LLM for FIFO-driven recording.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
from typing import Any

from .paths import CACHE_DIR, CMD_FIFO

log = logging.getLogger(__name__)


def _emit(event: str, payload: dict[str, Any] | None = None) -> None:
    """Emit a JSON event to stdout (line-delimited). Keep stdout free of other text."""
    msg: dict[str, Any] = {"event": event}
    if payload:
        msg.update(payload)
    print(json.dumps(msg), flush=True)


def _emit_error(message: str) -> None:
    _emit("error", {"message": message})


def _apply_ui_mirror(cmd: dict) -> None:
    ev = cmd.get("event")
    if not ev or not isinstance(ev, str):
        log.warning("ui_mirror: missing event")
        return
    payload = {k: v for k, v in cmd.items() if k not in ("cmd", "action", "event")}
    _emit(ev, payload if payload else None)


def _run_ui_daemon(*, events_only: bool = False) -> None:
    cmd_queue: queue.Queue[dict] = queue.Queue()

    def fifo_reader() -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if CMD_FIFO.exists():
            CMD_FIFO.unlink()
        try:
            os.mkfifo(str(CMD_FIFO))
        except OSError as e:
            log.error("Failed to create FIFO: %s", e)
            _emit_error(f"Failed to create FIFO: {e}")
            return
        while True:
            try:
                with open(CMD_FIFO, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            cmd_queue.put(json.loads(line))
                        except json.JSONDecodeError as e:
                            log.warning("Invalid JSON: %s", e)
            except (BrokenPipeError, EOFError):
                pass
            except Exception as e:
                log.error("FIFO read error: %s", e)

    threading.Thread(target=fifo_reader, daemon=True).start()

    if events_only:
        log.info("orateur ui: events-only (FIFO relay for Quickshell; use orateur run for STT/TTS)")
    else:
        from .audio_capture import AudioCapture
        from .audio_utils import audio_to_levels
        from .config import ConfigManager
        from .llm import get_llm_backend, is_llm_disabled
        from .stt import get_stt_backend
        from .tts import get_tts_backend

        config = ConfigManager()
        stt = get_stt_backend(config.get_setting("stt_backend", "pywhispercpp"), config)
        tts = get_tts_backend(config.get_setting("tts_backend", "pocket_tts"), config)
        llm_name = config.get_setting("llm_backend", "ollama")
        llm = None if is_llm_disabled(llm_name) else get_llm_backend(llm_name, config)

        if not stt or not stt.is_ready():
            _emit_error("STT not ready")
            return
        if not tts or not tts.is_ready():
            _emit_error("TTS not ready")
            return

        audio = AudioCapture(config=config)
        recording_mode: str | None = None

        def on_recording_level(rms: float) -> None:
            _emit("recording", {"level": rms})

        def on_tts_level(level: float) -> None:
            _emit("tts_level", {"level": level})

        while True:
            try:
                cmd = cmd_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            action = cmd.get("cmd") or cmd.get("action")
            if not action:
                _emit_error("Missing cmd")
                continue

            if action == "ui_mirror":
                _apply_ui_mirror(cmd)
                continue

            if action == "quit" or action == "exit":
                break

            if action == "start_recording":
                mode = cmd.get("mode", "stt")
                if mode not in ("stt", "sts"):
                    mode = "stt"
                recording_mode = mode
                if audio.start_recording(level_callback=on_recording_level):
                    _emit("recording_started", {"mode": mode})
                else:
                    _emit_error("Failed to start recording")

            elif action == "stop_recording":
                recording_mode_snap = recording_mode
                recording_mode = None
                data = audio.stop_recording()

                if data is None:
                    _emit_error("No audio recorded")
                    continue

                levels = audio_to_levels(data, 60)
                _emit("recording_stopped", {"levels": levels})

                if recording_mode_snap == "stt":
                    _emit("transcribing")
                    try:
                        text = stt.transcribe(data)
                        _emit("transcribed", {"text": text or ""})
                    except Exception as e:
                        log.exception("Transcription failed")
                        _emit_error(str(e))

                elif recording_mode_snap == "sts":
                    if not llm or not llm.is_ready():
                        _emit_error("LLM not ready")
                        continue
                    _emit("transcribing")
                    try:
                        text = stt.transcribe(data)
                        if not text or not text.strip():
                            _emit_error("No transcription")
                            continue
                        _emit("transcribed", {"text": text})
                        system_prompt = config.get_setting(
                            "llm_system_prompt",
                            "You are a helpful assistant. Respond concisely.",
                        )
                        response = llm.generate(text, system_prompt=system_prompt)
                        if not response or not response.strip():
                            _emit_error("No LLM response")
                            continue
                        duration_sec = tts.estimate_duration(response)
                        _emit("tts_estimate", {"duration_sec": duration_sec})
                        _emit("tts_playing")
                        ok = tts.synthesize_and_play(
                            response,
                            level_callback=on_tts_level,
                        )
                        _emit("tts_done", {"success": ok})
                    except Exception as e:
                        log.exception("STS failed")
                        _emit_error(str(e))

            elif action == "speak":
                text = cmd.get("text", "").strip()
                if not text:
                    _emit_error("No text to speak")
                    continue
                duration_sec = tts.estimate_duration(text)
                _emit("tts_estimate", {"duration_sec": duration_sec})
                _emit("tts_playing")
                try:
                    ok = tts.synthesize_and_play(text, level_callback=on_tts_level)
                    _emit("tts_done", {"success": ok})
                except Exception as e:
                    log.exception("TTS failed")
                    _emit_error(str(e))

            else:
                _emit_error(f"Unknown command: {action}")

        return

    # events_only main loop
    while True:
        try:
            cmd = cmd_queue.get(timeout=0.5)
        except queue.Empty:
            continue

        action = cmd.get("cmd") or cmd.get("action")
        if not action:
            _emit_error("Missing cmd")
            continue

        if action == "ui_mirror":
            _apply_ui_mirror(cmd)
            continue

        if action == "quit" or action == "exit":
            break

        if action in ("start_recording", "stop_recording", "speak"):
            _emit_error("Events-only UI: use orateur run + shortcuts, or run `orateur ui` without --events-only")
            continue

        _emit_error(f"Unknown command: {action}")
