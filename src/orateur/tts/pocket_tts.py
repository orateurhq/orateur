"""Pocket TTS backend."""

import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

from .base import TTSBackend

log = logging.getLogger(__name__)

POCKET_TTS_VOICES = [
    "alba", "marius", "javert", "jean", "fantine",
    "cosette", "eponine", "azelma",
]


class PocketTTSBackend(TTSBackend):
    """Pocket TTS text-to-speech."""

    def __init__(self, config):
        self.config = config
        self._model = None
        self._voice_state_cache = {}
        self.ready = False
        self.voice = config.get_setting("tts_voice", "alba")
        self.volume = max(0.1, min(1.0, float(config.get_setting("tts_volume", 1.0))))

    def initialize(self, config) -> bool:
        self.config = config
        self.voice = config.get_setting("tts_voice", "alba")
        self.volume = max(0.1, min(1.0, float(config.get_setting("tts_volume", 1.0))))
        try:
            from pocket_tts import TTSModel
            self._model = TTSModel.load_model()
            self.ready = True
            log.info("Pocket TTS ready - voice: %s", self.voice)
            return True
        except ImportError as e:
            log.warning("pocket-tts not installed: %s", e)
            return False
        except Exception as e:
            log.warning("Pocket TTS init failed: %s", e)
            return False

    def _get_voice_state(self, voice: Optional[str] = None):
        voice = voice or self.voice
        if voice not in self._voice_state_cache:
            self._voice_state_cache[voice] = self._model.get_state_for_audio_prompt(voice)
        return self._voice_state_cache[voice]

    def _get_streaming_player_cmd(self, volume: float) -> Optional[list]:
        vol = max(0.1, min(1.0, float(volume)))
        sr = str(self._model.sample_rate)
        for check, cmd in [
            ("pw-play", ["pw-play", "-a", "-", "--rate", sr, "--channels", "1", "--format", "s16", "--volume", str(vol)]),
            ("paplay", ["paplay", "--raw", "--format=s16le", f"--rate={sr}", "--channels=1", "-"]),
            ("aplay", ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", sr, "-c", "1", "-"]),
            ("ffplay", ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", "-f", "s16le", "-ar", sr, "-ac", "1", "-volume", str(int(vol * 100)), "-i", "pipe:0"]),
        ]:
            try:
                r = subprocess.run(["which", check], capture_output=True, timeout=2)
                if r.returncode == 0:
                    return cmd
            except Exception:
                pass
        return None

    def synthesize(
        self,
        text: str,
        voice: Optional[str] = None,
    ) -> Optional[Path]:
        if not text or not text.strip():
            return None
        if not self.ready or not self._model:
            return None
        try:
            voice_state = self._get_voice_state(voice)
            audio = self._model.generate_audio(voice_state, text)
            import scipy.io.wavfile
            from ..paths import TEMP_DIR
            TEMP_DIR.mkdir(parents=True, exist_ok=True)
            out_path = TEMP_DIR / "tts_output.wav"
            arr = audio.numpy() if hasattr(audio, "numpy") else audio
            scipy.io.wavfile.write(str(out_path), self._model.sample_rate, arr)
            return out_path
        except Exception as e:
            log.warning("Synthesis failed: %s", e)
            return None

    def synthesize_and_play(
        self,
        text: str,
        voice: Optional[str] = None,
        volume: Optional[float] = None,
    ) -> bool:
        if not text or not text.strip():
            return False
        vol = volume if volume is not None else self.volume
        vol = max(0.1, min(1.0, float(vol)))
        if not self.ready or not self._model:
            return False
        cmd = self._get_streaming_player_cmd(vol)
        if not cmd:
            wav = self.synthesize(text, voice)
            return wav and self._play_file(wav, vol)
        try:
            import numpy as np
            voice_state = self._get_voice_state(voice)
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for chunk in self._model.generate_audio_stream(voice_state, text):
                arr = chunk
                if hasattr(chunk, "numpy"):
                    arr = chunk.cpu().numpy() if hasattr(chunk, "cpu") else chunk.numpy()
                arr = np.asarray(arr)
                if arr.dtype.kind == "f":
                    arr = (np.clip(arr, -1.0, 1.0) * 32767).astype(np.int16)
                proc.stdin.write(arr.tobytes())
                proc.stdin.flush()
            proc.stdin.close()
            proc.wait()
            time.sleep(0.5)
            return proc.returncode == 0
        except Exception as e:
            log.warning("Streaming failed: %s", e)
            wav = self.synthesize(text, voice)
            return wav and self._play_file(wav, vol)

    def _play_file(self, wav_path: Path, volume: Optional[float] = None) -> bool:
        vol = 1.0 if volume is None else max(0.1, min(1.0, float(volume)))
        for player in ["pw-play", "paplay", "aplay", "ffplay"]:
            try:
                r = subprocess.run(["which", player], capture_output=True, timeout=2)
                if r.returncode == 0:
                    if player == "pw-play":
                        subprocess.run(["pw-play", "--volume", str(int(vol * 100)), str(wav_path)], check=True, timeout=60)
                    elif player == "paplay":
                        subprocess.run(["paplay", str(wav_path)], check=True, timeout=60)
                    elif player == "aplay":
                        subprocess.run(["aplay", "-q", str(wav_path)], check=True, timeout=60)
                    else:
                        subprocess.run(["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", str(wav_path)], check=True, timeout=60)
                    return True
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
                continue
        return False

    def is_ready(self) -> bool:
        return self.ready

    def get_available_voices(self) -> list[str]:
        return list(POCKET_TTS_VOICES)
