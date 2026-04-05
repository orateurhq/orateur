"""Abstract STT backend interface."""

from abc import ABC, abstractmethod
from typing import Optional

import numpy as np


class STTBackend(ABC):
    """Abstract base class for Speech-to-Text backends."""

    def __init__(self, config: object) -> None:
        """Subclasses store ``config`` as needed."""

    @abstractmethod
    def initialize(self, config) -> bool:
        """Initialize the backend. Returns True on success."""
        pass

    @abstractmethod
    def transcribe(
        self,
        audio_data: np.ndarray,
        sample_rate: int = 16000,
        language_override: Optional[str] = None,
        prompt_override: Optional[str] = None,
    ) -> str:
        """
        Transcribe audio to text.

        Args:
            audio_data: NumPy array of float32 audio samples (mono)
            sample_rate: Sample rate (typically 16000)
            language_override: Optional language code (e.g. 'en', 'fr')
            prompt_override: Optional Whisper initial prompt override

        Returns:
            Transcribed text string
        """
        pass

    def is_ready(self) -> bool:
        """Check if backend is ready for transcription."""
        return True

    def get_available_models(self) -> list[str]:
        """Return list of available model names for this backend."""
        return []
