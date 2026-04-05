"""Speech-to-Speech pipeline: STT -> LLM -> TTS."""

import logging
from collections.abc import Callable
from typing import Optional

from .stt import get_stt_backend

log = logging.getLogger(__name__)
from .llm import get_llm_backend
from .tts import get_tts_backend


def run_sts(
    config,
    audio_data,
    sample_rate: int = 16000,
    language_override: Optional[str] = None,
    stt=None,
    tts=None,
    llm=None,
    ui_mirror: Optional[Callable[..., None]] = None,
) -> bool:
    """
    Run Speech-to-Speech: transcribe -> LLM -> TTS.

    If stt, tts, or llm are provided and ready, they are reused.
    Otherwise new backends are created (for CLI compatibility).

    Returns True if audio was played successfully.
    """
    stt_name = config.get_setting("stt_backend", "pywhispercpp")
    tts_name = config.get_setting("tts_backend", "pocket_tts")
    llm_name = config.get_setting("llm_backend", "ollama")

    def _m(event: str, **kw) -> None:
        if ui_mirror:
            ui_mirror(event, **kw)

    if stt is None or not stt.is_ready():
        stt = get_stt_backend(stt_name, config)
    if tts is None or not tts.is_ready():
        tts = get_tts_backend(tts_name, config)
    if llm is None or not llm.is_ready():
        llm = get_llm_backend(llm_name, config)

    if not stt or not stt.is_ready():
        log.error("STT not ready")
        _m("error", message="STT not ready")
        return False
    if not tts or not tts.is_ready():
        log.error("TTS not ready")
        _m("error", message="TTS not ready")
        return False
    if not llm or not llm.is_ready():
        log.error("LLM not ready")
        _m("error", message="LLM not ready")
        return False

    _m("transcribing")
    text = stt.transcribe(audio_data, sample_rate, language_override)
    if not text or not text.strip():
        log.error("No transcription")
        _m("error", message="No transcription")
        return False

    _m("transcribed", text=text)
    system_prompt = config.get_setting("llm_system_prompt", "You are a helpful assistant. Respond concisely.")
    response = llm.generate(text, system_prompt=system_prompt)
    if not response or not response.strip():
        log.error("No LLM response")
        _m("error", message="No LLM response")
        return False

    duration_sec = tts.estimate_duration(response)
    _m("tts_estimate", duration_sec=duration_sec)
    _m("tts_playing")

    def on_tts_level(level: float) -> None:
        _m("tts_level", level=level)

    try:
        try:
            ok = tts.synthesize_and_play(
                response,
                level_callback=on_tts_level if ui_mirror else None,
            )
        except TypeError:
            ok = tts.synthesize_and_play(response)
    except Exception as e:
        log.exception("TTS playback failed")
        _m("error", message=str(e))
        return False
    _m("tts_done", success=ok)
    return bool(ok)
