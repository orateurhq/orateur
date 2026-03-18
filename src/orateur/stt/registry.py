"""STT backend registry - plug-n-play discovery."""

from typing import Optional, Type

from .base import STTBackend
from .pywhispercpp import PyWhisperCppBackend

_BACKENDS: dict[str, Type[STTBackend]] = {
    "pywhispercpp": PyWhisperCppBackend,
}


def get_stt_backend(name: str, config) -> Optional[STTBackend]:
    """Get and initialize an STT backend by name."""
    cls = _BACKENDS.get(name)
    if cls is None:
        return None
    backend = cls(config)
    if backend.initialize(config):
        return backend
    return None


def list_stt_backends() -> list[str]:
    """List registered STT backend names."""
    return list(_BACKENDS.keys())


def register_stt_backend(name: str, backend_cls: Type[STTBackend]) -> None:
    """Register a new STT backend."""
    _BACKENDS[name] = backend_cls
