"""Audio capture for speech recognition."""

import threading
from typing import Optional

import numpy as np
import sounddevice as sd


class AudioCapture:
    """Handles audio recording for STT."""

    def __init__(self, device_id: Optional[int] = None, config=None):
        self.sample_rate = 16000
        self.channels = 1
        self.chunk_size = 1024
        self.dtype = np.float32

        self.preferred_device_id = device_id or (config.get_setting("audio_device_id") if config else None)
        self.config = config

        self.is_recording = False
        self.audio_data = []
        self.lock = threading.Lock()
        self.stream = None
        self.record_thread = None

        self._init_device()

    def _init_device(self) -> None:
        sd.default.samplerate = self.sample_rate
        sd.default.channels = self.channels
        sd.default.dtype = self.dtype
        if self.preferred_device_id is not None:
            try:
                info = sd.query_devices(device=self.preferred_device_id, kind="input")
                if info["max_input_channels"] > 0:
                    sd.default.device[0] = self.preferred_device_id
                    return
            except Exception:
                pass
            self.preferred_device_id = None
        # Use system default
        try:
            devs = sd.query_devices()
            for i, d in enumerate(devs):
                if d["max_input_channels"] > 0:
                    sd.default.device[0] = i
                    break
        except Exception:
            pass

    def start_recording(self) -> bool:
        if self.is_recording:
            return True
        try:
            with self.lock:
                self.audio_data = []
                self.is_recording = True
            self.record_thread = threading.Thread(target=self._record_audio, daemon=True)
            self.record_thread.start()
            return True
        except Exception as e:
            print(f"[AUDIO] Failed to start: {e}")
            with self.lock:
                self.is_recording = False
            return False

    def stop_recording(self) -> Optional[np.ndarray]:
        if not self.is_recording:
            return None
        with self.lock:
            self.is_recording = False
        if self.record_thread and self.record_thread.is_alive():
            self.record_thread.join(timeout=3.0)
        # Use timeout so we don't block indefinitely; record thread may hold lock in finally
        for _ in range(120):  # up to 60s
            if self.lock.acquire(timeout=0.5):
                try:
                    if not self.audio_data:
                        return None
                    audio = np.concatenate(self.audio_data, axis=0)
                    if audio.ndim > 1:
                        audio = audio.flatten()
                    if audio.dtype != np.float32:
                        audio = audio.astype(np.float32)
                    if not audio.flags["C_CONTIGUOUS"]:
                        audio = np.ascontiguousarray(audio, dtype=np.float32)
                    return audio
                except Exception as e:
                    print(f"[AUDIO] Failed to process: {e}")
                    return None
                finally:
                    self.lock.release()
                break
        return None

    def _record_audio(self) -> None:
        try:
            def callback(indata, frames, time_info, status):
                if status:
                    print(f"[AUDIO] {status}")
                with self.lock:
                    if self.is_recording:
                        chunk = indata[:, 0].copy()
                        self.audio_data.append(chunk)

            self.stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype=self.dtype,
                blocksize=self.chunk_size,
                callback=callback,
            )
            self.stream.start()
            while True:
                with self.lock:
                    if not self.is_recording:
                        break
                sd.sleep(100)
        except Exception as e:
            print(f"[AUDIO] Record error: {e}")
        finally:
            stream_to_close = None
            with self.lock:
                stream_to_close = self.stream
                self.stream = None
            if stream_to_close:
                try:
                    stream_to_close.stop()
                    stream_to_close.close()
                except Exception:
                    pass

    @staticmethod
    def get_available_devices() -> list[dict]:
        try:
            devs = sd.query_devices()
            result = []
            for i, d in enumerate(devs):
                if d["max_input_channels"] > 0:
                    result.append({"id": i, "name": d["name"]})
            return result
        except Exception:
            return []
