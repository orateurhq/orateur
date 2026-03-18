"""Abstract LLM backend interface."""

from abc import ABC, abstractmethod
from typing import Optional


class LLMBackend(ABC):
    """Abstract base class for LLM backends (used in STS pipeline)."""

    @abstractmethod
    def initialize(self, config) -> bool:
        """Initialize the backend. Returns True on success."""
        pass

    @abstractmethod
    def generate(
        self,
        user_text: str,
        system_prompt: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> str:
        """
        Generate a response from the LLM.

        Args:
            user_text: The user's input (transcribed speech)
            system_prompt: Optional system prompt
            model_override: Optional model name override

        Returns:
            Generated text response
        """
        pass

    def is_ready(self) -> bool:
        """Check if backend is ready."""
        return True

    def get_available_models(self) -> list[str]:
        """Return list of available model names (if applicable)."""
        return []
