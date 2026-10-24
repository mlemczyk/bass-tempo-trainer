"""Metronome click sounds."""
from __future__ import annotations

import numpy as np


def _click(sr: int, freq: float, gain: float, ms: float = 25.0) -> np.ndarray:
    t = np.arange(int(sr * ms / 1000)) / sr
    env = np.exp(-t / (ms / 1000 / 5)) * np.minimum(1.0, t / 0.0005)
    return (gain * env * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def make_clicks(sr: int) -> dict[str, np.ndarray]:
    """accent: the downbeat; beat: other beats; sub: notes between beats."""
    return {
        "accent": _click(sr, 1760.0, 0.9),
        "beat": _click(sr, 1175.0, 0.6),
        "sub": _click(sr, 880.0, 0.25, ms=15.0),
    }
