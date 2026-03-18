"""LLM backend registry."""

from typing import Optional, Type

from .base import LLMBackend
from .mcp import MCPLLMBackend
from .ollama import OllamaBackend

_BACKENDS: dict[str, Type[LLMBackend]] = {
    "ollama": OllamaBackend,
    "mcp": MCPLLMBackend,
}


def get_llm_backend(name: str, config) -> Optional[LLMBackend]:
    """Get and initialize an LLM backend by name."""
    cls = _BACKENDS.get(name)
    if cls is None:
        return None
    backend = cls(config)
    if backend.initialize(config):
        return backend
    return None


def list_llm_backends() -> list[str]:
    """List registered LLM backend names."""
    return list(_BACKENDS.keys())


def register_llm_backend(name: str, backend_cls: Type[LLMBackend]) -> None:
    """Register a new LLM backend."""
    _BACKENDS[name] = backend_cls
