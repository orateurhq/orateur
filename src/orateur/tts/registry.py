"""TTS backend registry."""

from typing import Optional, Type

from .base import TTSBackend
from .pocket_tts import PocketTTSBackend

_BACKENDS: dict[str, Type[TTSBackend]] = {
    "pocket_tts": PocketTTSBackend,
}


def get_tts_backend(name: str, config) -> Optional[TTSBackend]:
    """Get and initialize a TTS backend by name."""
    cls = _BACKENDS.get(name)
    if cls is None:
        return None
    backend = cls(config)
    if backend.initialize(config):
        return backend
    return None


def list_tts_backends() -> list[str]:
    """List registered TTS backend names."""
    return list(_BACKENDS.keys())


def register_tts_backend(name: str, backend_cls: Type[TTSBackend]) -> None:
    """Register a new TTS backend."""
    _BACKENDS[name] = backend_cls
