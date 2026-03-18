"""Main application loop for Orateur."""

from . import _cuda_env  # noqa: F401 - sets LD_LIBRARY_PATH for CUDA/ROCm

import os
import signal
import sys
import time

from .audio_capture import AudioCapture
from .stt import get_stt_backend
from .tts import get_tts_backend
from .llm import get_llm_backend
from .shortcuts import ShortcutManager
from .text_injector import TextInjector
from .config import ConfigManager
from .sts_pipeline import run_sts


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

    print("[INIT] Loading STT...")
    stt = get_stt_backend(config.get_setting("stt_backend", "pywhispercpp"), config)
    if not stt or not stt.is_ready():
        print("[ERROR] STT failed to initialize")
        sys.exit(1)

    print("[INIT] Loading TTS...")
    tts = get_tts_backend(config.get_setting("tts_backend", "pocket_tts"), config)
    if not tts or not tts.is_ready():
        print("[WARN] TTS not ready - speak/sts will be limited")

    print("[INIT] Loading LLM...")
    llm = get_llm_backend(config.get_setting("llm_backend", "ollama"), config)
    if not llm or not llm.is_ready():
        print("[WARN] LLM not ready - sts will be limited")

    audio = AudioCapture(config=config)
    injector = TextInjector(config)

    recording_for = [None]  # "stt" | "stt_secondary" | "sts" | None

    def on_primary():
        if recording_for[0] == "stt":
            recording_for[0] = None
            data = audio.stop_recording()
            if data is not None:
                text = stt.transcribe(data)
                if text:
                    injector.inject_text(text)
        else:
            recording_for[0] = "stt"
            audio.start_recording()

    def on_secondary():
        if recording_for[0] == "stt_secondary":
            recording_for[0] = None
            data = audio.stop_recording()
            if data is not None:
                lang = config.get_setting("stt_language_secondary")
                prompt = config.get_setting("stt_whisper_prompt_secondary")
                text = stt.transcribe(data, language_override=lang, prompt_override=prompt)
                if text:
                    injector.inject_text(text)
        else:
            recording_for[0] = "stt_secondary"
            audio.start_recording()

    def on_sts():
        if recording_for[0] == "sts":
            recording_for[0] = None
            data = audio.stop_recording()
            if data is not None:
                run_sts(config, data, stt=stt, tts=tts, llm=llm)
        else:
            recording_for[0] = "sts"
            audio.start_recording()

    def on_tts():
        text = _get_text_from_selection(config)
        if text and tts and tts.is_ready():
            tts.synthesize_and_play(text)

    shortcuts = ShortcutManager(config)
    shortcuts.register("primary", config.get_setting("primary_shortcut"), on_primary)
    shortcuts.register("secondary", config.get_setting("secondary_shortcut"), on_secondary)
    shortcuts.register("tts", config.get_setting("tts_shortcut"), on_tts)
    shortcuts.register("sts", config.get_setting("sts_shortcut"), on_sts)

    if not shortcuts.start():
        sys.exit(1)

    print("[INIT] Orateur ready. Shortcuts active.")

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
        print("[INIT] Shutting down...")
        shortcuts.stop()
        # Bypass Python interpreter shutdown to avoid C++ destructor crashes
        # (pywhispercpp/ggml and PyTorch can crash when daemon threads are
        # abruptly terminated during normal exit)
        os._exit(0)
