"""Pocket TTS backend."""

import logging
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .base import TTSBackend

log = logging.getLogger(__name__)

POCKET_TTS_VOICES = [
    "alba",
    "marius",
    "javert",
    "jean",
    "fantine",
    "cosette",
    "eponine",
    "azelma",
]


class PocketTTSBackend(TTSBackend):
    """Pocket TTS text-to-speech."""

    def __init__(self, config):
        super().__init__(config)
        self.config = config
        self._model = None
        self._voice_state_cache = {}
        self.ready = False
        self.voice = config.get_setting("tts_voice", "alba")
        self.volume = max(0.1, min(1.0, float(config.get_setting("tts_volume", 1.0))))
        self._playback_lock = threading.Lock()
        self._playback_proc: Optional[subprocess.Popen] = None
        self._stop_event = threading.Event()

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
        model = self._model
        if model is None:
            raise RuntimeError("Pocket TTS model not loaded")
        if voice not in self._voice_state_cache:
            self._voice_state_cache[voice] = model.get_state_for_audio_prompt(voice)
        return self._voice_state_cache[voice]

    def stop_playback(self) -> None:
        self._stop_event.set()
        with self._playback_lock:
            proc = self._playback_proc
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass

    def _get_streaming_player_cmd(self, volume: float) -> Optional[list]:
        # ffplay (and similar) raw-PCM-over-stdin streaming often exits non-zero or breaks the
        # pipe on macOS when Homebrew ffplay is on PATH. WAV + afplay is reliable instead.
        if sys.platform == "darwin":
            return None
        model = self._model
        if model is None:
            return None
        vol = max(0.1, min(1.0, float(volume)))
        sr = str(model.sample_rate)
        for check, cmd in [
            (
                "pw-play",
                [
                    "pw-play",
                    "-a",
                    "-",
                    "--rate",
                    sr,
                    "--channels",
                    "1",
                    "--format",
                    "s16",
                    "--volume",
                    str(vol),
                ],
            ),
            ("paplay", ["paplay", "--raw", "--format=s16le", f"--rate={sr}", "--channels=1", "-"]),
            ("aplay", ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", sr, "-c", "1", "-"]),
            (
                "ffplay",
                [
                    "ffplay",
                    "-nodisp",
                    "-autoexit",
                    "-loglevel",
                    "error",
                    "-f",
                    "s16le",
                    "-ar",
                    sr,
                    "-ac",
                    "1",
                    "-volume",
                    str(int(vol * 100)),
                    "-i",
                    "pipe:0",
                ],
            ),
        ]:
            if shutil.which(check):
                return cmd
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
        level_callback: Optional[Callable[[float], None]] = None,
    ) -> bool:
        if not text or not text.strip():
            return False
        vol = volume if volume is not None else self.volume
        vol = max(0.1, min(1.0, float(vol)))
        if not self.ready or not self._model:
            return False
        self._stop_event.clear()
        cmd = self._get_streaming_player_cmd(vol)
        if not cmd:
            log.info(
                "Pocket TTS: using WAV file + system player (macOS skips stdin streaming; "
                "or no pw-play/paplay/aplay/ffplay for streaming)"
            )
            wav = self.synthesize(text, voice)
            if not wav:
                log.warning("Pocket TTS: synthesis produced no WAV file")
            ok = bool(wav and self._play_file(wav, vol))
            log.info("Pocket TTS: WAV playback finished ok=%s", ok)
            return ok
        log.info("Pocket TTS: streaming playback via %s", cmd[0])
        proc: Optional[subprocess.Popen] = None
        try:
            voice_state = self._get_voice_state(voice)
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            stdin = proc.stdin
            if stdin is None:
                log.warning("Pocket TTS: player has no stdin pipe")
                return False
            with self._playback_lock:
                self._playback_proc = proc
            interrupted = False
            try:
                for chunk in self._model.generate_audio_stream(voice_state, text):
                    if self._stop_event.is_set():
                        interrupted = True
                        break
                    arr = chunk
                    if hasattr(chunk, "numpy"):
                        arr = chunk.cpu().numpy() if hasattr(chunk, "cpu") else chunk.numpy()
                    arr = np.asarray(arr)
                    if arr.dtype.kind == "f":
                        arr = (np.clip(arr, -1.0, 1.0) * 32767).astype(np.int16)
                    if level_callback is not None and len(arr) > 0:
                        float_arr = arr.astype(np.float32) / 32768.0
                        rms = float(np.sqrt(np.mean(float_arr**2)))
                        try:
                            level_callback(rms)
                        except Exception as e:
                            log.debug("level_callback error: %s", e)
                    try:
                        stdin.write(arr.tobytes())
                        stdin.flush()
                    except BrokenPipeError:
                        interrupted = True
                        break
                try:
                    stdin.close()
                except Exception:
                    pass
                if interrupted or self._stop_event.is_set():
                    try:
                        proc.terminate()
                        proc.wait(timeout=2.0)
                    except Exception:
                        pass
                    return False
                proc.wait()
                time.sleep(0.5)
                ok_stream = proc.returncode == 0
                log.info("Pocket TTS: stream finished ok=%s (exit %s)", ok_stream, proc.returncode)
                return ok_stream
            finally:
                with self._playback_lock:
                    if self._playback_proc is proc:
                        self._playback_proc = None
        except Exception as e:
            log.warning("Streaming failed: %s", e)
            with self._playback_lock:
                if proc is not None and self._playback_proc is proc:
                    self._playback_proc = None
            wav = self.synthesize(text, voice)
            return bool(wav and self._play_file(wav, vol))

    def _play_file(self, wav_path: Path, volume: Optional[float] = None) -> bool:
        vol = 1.0 if volume is None else max(0.1, min(1.0, float(volume)))
        # macOS ships afplay; Linux/BSD typically use pipewire/pulse/alsa or ffplay.
        for player in ["pw-play", "paplay", "aplay", "ffplay", "afplay"]:
            if not shutil.which(player):
                continue
            log.info("Pocket TTS: playing WAV with %s", player)
            try:
                if player == "pw-play":
                    cmd = ["pw-play", "--volume", str(int(vol * 100)), str(wav_path)]
                elif player == "paplay":
                    cmd = ["paplay", str(wav_path)]
                elif player == "aplay":
                    cmd = ["aplay", "-q", str(wav_path)]
                elif player == "afplay":
                    cmd = ["afplay", "-v", str(vol), str(wav_path)]
                else:
                    cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", str(wav_path)]
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                with self._playback_lock:
                    self._playback_proc = proc
                try:
                    while True:
                        if self._stop_event.is_set():
                            proc.terminate()
                            try:
                                proc.wait(timeout=2.0)
                            except subprocess.TimeoutExpired:
                                proc.kill()
                            return False
                        ret = proc.poll()
                        if ret is not None:
                            return ret == 0
                        time.sleep(0.05)
                finally:
                    with self._playback_lock:
                        if self._playback_proc is proc:
                            self._playback_proc = None
            except (FileNotFoundError, OSError):
                continue
        log.warning(
            "No usable audio player found (tried pw-play, paplay, aplay, ffplay, afplay). "
            "On macOS, afplay should exist in /usr/bin — check PATH."
        )
        return False

    def is_ready(self) -> bool:
        return self.ready

    def get_available_voices(self) -> list[str]:
        return list(POCKET_TTS_VOICES)
