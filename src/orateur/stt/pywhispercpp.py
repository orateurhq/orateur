"""pywhispercpp STT backend."""

import importlib
import logging
import sys
from pathlib import Path
from typing import Optional

import numpy as np

from .base import STTBackend

log = logging.getLogger(__name__)


def whisper_models_dir() -> Path:
    """Where pywhispercpp stores ggml weights (uses platformdirs; not ~/.local on macOS)."""
    try:
        from pywhispercpp.constants import MODELS_DIR

        return Path(MODELS_DIR)
    except ImportError:
        return Path.home() / ".local" / "share" / "pywhispercpp" / "models"


class PyWhisperCppBackend(STTBackend):
    """Whisper via pywhispercpp (local CPU, or GPU via CUDA / Metal / Vulkan per build)."""

    def __init__(self, config):
        super().__init__(config)
        self.config = config
        self._model = None
        self._current_model = None
        self.ready = False

    def initialize(self, config) -> bool:
        self.config = config
        self._current_model = config.get_setting("stt_model", "base")
        threads = config.get_setting("stt_threads", 4)

        try:
            try:
                _pwm = importlib.import_module("pywhispercpp.model")
            except ImportError:
                _pwm = importlib.import_module("pywhispercpp")
            Model = getattr(_pwm, "Model")
            from pywhispercpp.constants import MODELS_DIR

            # redirect_whispercpp_logs_to=sys.stderr to see GPU allocation logs (e.g. "CUDA0 total size")
            redirect_logs = sys.stderr if config.get_setting("stt_whisper_verbose", False) else None
            # Model() downloads missing ggml files into MODELS_DIR (platformdirs; e.g. ~/Library/... on macOS).
            self._model = Model(
                model=self._current_model,
                models_dir=str(MODELS_DIR),
                n_threads=threads,
                redirect_whispercpp_logs_to=redirect_logs,
            )
            self.ready = True
            log.info("pywhispercpp ready - model: %s", self._current_model)
            return True
        except ImportError as e:
            log.warning("pywhispercpp not installed: %s", e)
            return False
        except Exception as e:
            log.warning("pywhispercpp init failed: %s", e)
            log.info(
                "Ensure the model is available under %s (run: orateur setup)",
                whisper_models_dir(),
            )
            import traceback

            traceback.print_exc()
            return False

    def transcribe(
        self,
        audio_data: np.ndarray,
        sample_rate: int = 16000,
        language_override: Optional[str] = None,
        prompt_override: Optional[str] = None,
    ) -> str:
        if not self.ready or not self._model:
            return ""

        if audio_data is None or len(audio_data) == 0:
            return ""

        if audio_data.dtype != np.float32:
            audio_data = audio_data.astype(np.float32)
        if not audio_data.flags["C_CONTIGUOUS"]:
            audio_data = np.ascontiguousarray(audio_data, dtype=np.float32)

        language = language_override or self.config.get_setting("stt_language")
        prompt = prompt_override if prompt_override is not None else self.config.get_setting("stt_whisper_prompt")

        transcribe_kwargs = {}
        if language:
            transcribe_kwargs["language"] = language
        if prompt:
            transcribe_kwargs["initial_prompt"] = prompt

        try:
            segments = self._model.transcribe(audio_data, **transcribe_kwargs)
            return " ".join(seg.text for seg in segments).strip()
        except Exception as e:
            log.warning("Transcription failed: %s", e)
            return ""

    def is_ready(self) -> bool:
        return self.ready

    def get_available_models(self) -> list[str]:
        models_dir = whisper_models_dir()
        supported = ["tiny", "base", "small", "medium", "large"]
        available = []
        for name in supported:
            for suffix in ["", ".en"]:
                m = f"{name}{suffix}" if suffix else name
                if (models_dir / f"ggml-{m}.bin").exists():
                    available.append(m)
                    break
        return sorted(available)
