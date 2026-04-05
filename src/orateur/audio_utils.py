"""Audio utility functions for waveform visualization."""

import numpy as np


def audio_to_levels(audio: np.ndarray, num_bars: int = 60) -> list[float]:
    """
    Split audio into segments and return RMS per segment normalized to 0-1.

    Args:
        audio: 1D float32 audio array
        num_bars: Number of bars/levels to return

    Returns:
        List of floats in [0, 1] representing normalized RMS per segment
    """
    if audio is None or len(audio) == 0:
        return [0.0] * num_bars

    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.flatten()

    n = len(audio)
    if n < num_bars:
        # Pad with zeros
        levels = np.zeros(num_bars, dtype=np.float32)
        seg_len = max(1, n // num_bars)
        for i in range(min(num_bars, (n + seg_len - 1) // seg_len)):
            start = i * seg_len
            end = min(start + seg_len, n)
            seg = audio[start:end]
            levels[i] = float(np.sqrt(np.mean(seg**2)))
    else:
        seg_len = n // num_bars
        levels = np.zeros(num_bars, dtype=np.float32)
        for i in range(num_bars):
            start = i * seg_len
            end = (i + 1) * seg_len if i < num_bars - 1 else n
            seg = audio[start:end]
            if len(seg) > 0:
                levels[i] = float(np.sqrt(np.mean(seg**2)))

    # Normalize to 0-1 (clip and scale by max)
    max_val = float(np.max(levels))
    if max_val > 0:
        levels = np.clip(levels / max_val, 0.0, 1.0)
    else:
        levels = np.zeros(num_bars, dtype=np.float32)

    return levels.tolist()
