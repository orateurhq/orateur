"""Main application loop for Orateur."""

from . import _cuda_env  # noqa: F401 - sets LD_LIBRARY_PATH for CUDA/ROCm

import logging
import os
import signal
import sys
import threading
import time

from .audio_capture import AudioCapture
from .audio_utils import audio_to_levels
from . import ui_mirror
from . import quickshell_spawn

log = logging.getLogger(__name__)
from .stt import get_stt_backend
from .tts import get_tts_backend
from .llm import get_llm_backend
from .shortcuts import ShortcutManager
from .text_injector import TextInjector
from .config import ConfigManager
from .sts_pipeline import run_sts
from .desktop_notify import notify as desktop_notify


def _get_text_from_selection(config) -> str:
    """Get text from primary selection or clipboard."""
    import subprocess
    for cmd in [["wl-paste", "-p"], ["wl-paste"], ["xclip", "-selection", "primary", "-o"], ["xclip", "-selection", "clipboard", "-o"]]:
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=2)
            if r.returncode == 0 and r.stdout:
                return r.stdout.decode("utf-8", errors="replace").strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    try:
        import pyperclip
        return pyperclip.paste() or ""
    except ImportError:
        return ""


def run(config: ConfigManager | None = None) -> None:
    """Run the main loop (used by systemd)."""
    config = config or ConfigManager()

    ui_mirror.reset_ui_events_file()

    log.info("Loading STT...")
    stt = get_stt_backend(config.get_setting("stt_backend", "pywhispercpp"), config)
    if not stt or not stt.is_ready():
        log.error("STT failed to initialize")
        sys.exit(1)

    log.info("Loading TTS...")
    tts = get_tts_backend(config.get_setting("tts_backend", "pocket_tts"), config)
    if not tts or not tts.is_ready():
        log.warning("TTS not ready - speak/sts will be limited")

    log.info("Loading LLM...")
    llm = get_llm_backend(config.get_setting("llm_backend", "ollama"), config)
    if not llm or not llm.is_ready():
        log.warning("LLM not ready - sts will be limited")

    audio = AudioCapture(config=config)
    injector = TextInjector(config)

    recording_for = [None]  # "stt" | "stt_secondary" | "sts" | None
    tts_active = [False]
    tts_lock = threading.Lock()

    def m(event: str, **payload) -> None:
        ui_mirror.send(config, event, **payload)

    def on_primary():
        if recording_for[0] == "stt":
            recording_for[0] = None
            data = audio.stop_recording()
            if data is None:
                m("error", message="No audio recorded")
                return
            m("recording_stopped", levels=audio_to_levels(data, 60))
            m("transcribing")
            try:
                text = stt.transcribe(data)
            except Exception as e:
                log.exception("Transcription failed")
                m("error", message=str(e))
                return
            m("transcribed", text=text or "")
            if text:
                injector.inject_text(text)
        else:
            recording_for[0] = "stt"

            def on_level(rms: float) -> None:
                m("recording", level=rms)

            if audio.start_recording(level_callback=on_level):
                m("recording_started", mode="stt")
            else:
                m("error", message="Failed to start recording")

    def on_secondary():
        if recording_for[0] == "stt_secondary":
            recording_for[0] = None
            data = audio.stop_recording()
            if data is None:
                m("error", message="No audio recorded")
                return
            m("recording_stopped", levels=audio_to_levels(data, 60))
            m("transcribing")
            lang = config.get_setting("stt_language_secondary")
            prompt = config.get_setting("stt_whisper_prompt_secondary")
            try:
                text = stt.transcribe(data, language_override=lang, prompt_override=prompt)
            except Exception as e:
                log.exception("Transcription failed")
                m("error", message=str(e))
                return
            m("transcribed", text=text or "")
            if text:
                injector.inject_text(text)
        else:
            recording_for[0] = "stt_secondary"

            def on_level_sec(rms: float) -> None:
                m("recording", level=rms)

            if audio.start_recording(level_callback=on_level_sec):
                m("recording_started", mode="stt")
            else:
                m("error", message="Failed to start recording")

    def on_sts():
        if recording_for[0] == "sts":
            recording_for[0] = None
            data = audio.stop_recording()
            if data is None:
                m("error", message="No audio recorded")
                return
            m("recording_stopped", levels=audio_to_levels(data, 60))

            def ui_m(ev: str, **kw) -> None:
                ui_mirror.send(config, ev, **kw)

            run_sts(config, data, stt=stt, tts=tts, llm=llm, ui_mirror=ui_m)
        else:
            recording_for[0] = "sts"

            def on_level_sts(rms: float) -> None:
                m("recording", level=rms)

            if audio.start_recording(level_callback=on_level_sts):
                m("recording_started", mode="sts")
            else:
                m("error", message="Failed to start recording")

    def on_tts():
        if not tts or not tts.is_ready():
            return
        with tts_lock:
            if tts_active[0]:
                tts.stop_playback()
                return
            text = _get_text_from_selection(config)
            if not text:
                return
            tts_active[0] = True

        duration_sec = tts.estimate_duration(text)
        m("tts_estimate", duration_sec=duration_sec)
        m("tts_playing")

        def on_lvl(level: float) -> None:
            m("tts_level", level=level)

        ok = False
        try:
            try:
                ok = tts.synthesize_and_play(text, level_callback=on_lvl)
            except TypeError:
                ok = tts.synthesize_and_play(text)
        finally:
            with tts_lock:
                tts_active[0] = False
            m("tts_done", success=bool(ok))

    shortcuts = ShortcutManager(config)
    shortcuts.register("primary", config.get_setting("primary_shortcut"), on_primary)
    shortcuts.register("secondary", config.get_setting("secondary_shortcut"), on_secondary)
    shortcuts.register("tts", config.get_setting("tts_shortcut"), on_tts)
    shortcuts.register("sts", config.get_setting("sts_shortcut"), on_sts)

    if not shortcuts.start():
        sys.exit(1)

    log.info("Orateur ready. Shortcuts active.")
    if config.get_setting("desktop_notifications", True):
        desktop_notify("Orateur started", "Speech shortcuts are active.", urgency="low")

    quickshell_proc = [None]
    if config.get_setting("quickshell_autostart", False):
        quickshell_proc[0] = quickshell_spawn.start_quickshell()

    shutdown_requested = [False]

    def shutdown(sig, frame):
        if shutdown_requested[0]:
            os._exit(0)
        shutdown_requested[0] = True

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        while not shutdown_requested[0]:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        log.info("Shutting down...")
        if config.get_setting("desktop_notifications", True):
            desktop_notify("Orateur stopped", "Speech shortcuts are inactive.", urgency="low")
        quickshell_spawn.stop_quickshell(quickshell_proc[0])
        shortcuts.stop()
        # Bypass Python interpreter shutdown to avoid C++ destructor crashes
        # (pywhispercpp/ggml and PyTorch can crash when daemon threads are
        # abruptly terminated during normal exit)
        os._exit(0)
