"""Abstract TTS backend interface."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


class TTSBackend(ABC):
    """Abstract base class for Text-to-Speech backends."""

    @abstractmethod
    def initialize(self, config) -> bool:
        """Initialize the backend. Returns True on success."""
        pass

    @abstractmethod
    def synthesize(
        self,
        text: str,
        voice: Optional[str] = None,
    ) -> Optional[Path]:
        """
        Synthesize text to audio file.

        Args:
            text: Text to speak
            voice: Optional voice name (uses default if None)

        Returns:
            Path to WAV file, or None on failure
        """
        pass

    def synthesize_and_play(
        self,
        text: str,
        voice: Optional[str] = None,
        volume: Optional[float] = None,
    ) -> bool:
        """
        Synthesize and play audio. Default implementation: synthesize then play file.
        """
        wav = self.synthesize(text, voice)
        if wav:
            return self._play_file(wav, volume)
        return False

    def _play_file(self, wav_path: Path, volume: Optional[float] = None) -> bool:
        """Play a WAV file. Override for streaming playback."""
        import subprocess
        vol = 1.0 if volume is None else max(0.1, min(1.0, float(volume)))
        for player, cmd in [
            ("pw-play", ["pw-play", "--volume", str(int(vol * 100)), str(wav_path)]),
            ("paplay", ["paplay", str(wav_path)]),
            ("aplay", ["aplay", "-q", str(wav_path)]),
            ("ffplay", ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", str(wav_path)]),
        ]:
            try:
                r = subprocess.run(["which", player], capture_output=True, timeout=2)
                if r.returncode == 0:
                    subprocess.run(cmd, check=True, timeout=60)
                    return True
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
                continue
        return False

    def is_ready(self) -> bool:
        """Check if backend is ready."""
        return True

    def get_available_voices(self) -> list[str]:
        """Return list of available voice names."""
        return []
