"""Ollama LLM backend."""

import logging
from typing import Optional

from .base import LLMBackend

log = logging.getLogger(__name__)


class OllamaBackend(LLMBackend):
    """Ollama local LLM."""

    def __init__(self, config):
        self.config = config
        self.ready = False

    def initialize(self, config) -> bool:
        self.config = config
        try:
            import ollama
            # Test connection
            ollama.list()
            self.ready = True
            log.info("Ollama ready")
            return True
        except ImportError as e:
            log.warning("ollama not installed: %s", e)
            return False
        except Exception as e:
            log.warning("Ollama init failed (is ollama running?): %s", e)
            return False

    def generate(
        self,
        user_text: str,
        system_prompt: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> str:
        if not self.ready or not user_text or not user_text.strip():
            return ""

        import ollama

        model = model_override or self.config.get_setting("llm_model", "llama3.2")
        base_url = self.config.get_setting("llm_base_url", "http://localhost:11434")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_text})

        try:
            client = ollama.Client(host=base_url)
            response = client.chat(model=model, messages=messages)
            if hasattr(response, "message") and response.message:
                return (response.message.content or "").strip()
            return ""
        except Exception as e:
            log.warning("Ollama generate failed: %s", e)
            return ""

    def is_ready(self) -> bool:
        return self.ready

    def get_available_models(self) -> list[str]:
        try:
            import ollama
            base_url = self.config.get_setting("llm_base_url", "http://localhost:11434")
            client = ollama.Client(host=base_url)
            resp = client.list()
            if hasattr(resp, "models") and resp.models:
                return [m.model for m in resp.models]
            return []
        except Exception:
            return []
