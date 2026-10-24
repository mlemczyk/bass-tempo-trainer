"""Synthetic bass plucks and a simulated player, for tests and `--simulate`."""
from __future__ import annotations

import numpy as np


def pluck(sr: int, f0: float = 55.0, dur: float = 1.5, seed: int = 0) -> np.ndarray:
    """One fingerstyle-ish pluck: a short noisy attack into a decaying,
    slightly inharmonic harmonic series whose upper partials die first."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(sr * dur)) / sr
    y = np.zeros_like(t)
    for h in range(1, 25):
        f = f0 * h * np.sqrt(1 + 1e-4 * h * h)
        if f > sr / 2.2:
            break
        amp = 1.0 / h ** 1.1
        tau = 1.2 / (1 + 0.35 * h)
        # Near-aligned phases: a raw pickup signal is a pulse train, not a hum.
        y += amp * np.exp(-t / tau) * np.cos(2 * np.pi * f * t + rng.normal(0, 0.3))
    attack = rng.normal(0, 1, len(t)) * np.exp(-t / 0.004) * 0.6
    y = y + attack
    y *= np.minimum(1.0, t / 0.0015)  # 1.5 ms rise
    # Fade the tail: a hard cut is a broadband click, i.e. a fake pluck.
    y *= np.minimum(1.0, (t[-1] - t) / 0.3)
    return (0.25 * y / np.max(np.abs(y))).astype(np.float32)


def render_plucks(sr: int, times_s, n: int, *, f0: float = 55.0, noise_db: float = -80.0,
                  seed: int = 0, level_jitter_db: float = 3.0) -> np.ndarray:
    """A signal of `n` samples with a pluck at each time. The finger for the
    next pluck lands on the string shortly before it, damping the note."""
    rng = np.random.default_rng(seed)
    p = pluck(sr, f0, seed=seed)
    out = rng.normal(0, 10 ** (noise_db / 20), n).astype(np.float32)
    times = sorted(times_s)
    for j, t0 in enumerate(times):
        a = int(round(t0 * sr))
        if j + 1 < len(times):
            gap = times[j + 1] - t0
            stop = int(round((times[j + 1] - min(0.02, 0.25 * gap)) * sr))
        else:
            stop = n
        seg = p[: max(0, min(len(p), n - a))].copy()
        if a + len(seg) > stop:  # damp to 20 % over 8 ms once the finger lands
            k = stop - a
            fade = np.full(len(seg) - k, 0.2, np.float32)
            ramp = min(len(fade), int(0.008 * sr))
            fade[:ramp] = np.linspace(1.0, 0.2, ramp)
            seg[k:] *= fade
        out[a: a + len(seg)] += seg * 10 ** (rng.normal(0, level_jitter_db) / 20)
    return out


class SimPlayer:
    """Plays along with a Core: plucks near every scheduled note slot, with a
    timing error whose spread grows with speed, and misses once it is too fast."""

    def __init__(self, sr: int, *, round_trip_s: float = 0.054, bias_ms: float = 4.0,
                 base_jitter_ms: float = 4.0, comfort_nps: float = 7.0, seed: int = 1,
                 tire_jitter_ms_per_min: float = 0.0, tire_m_late_ms_per_min: float = 0.0,
                 tire_level_db_per_min: float = 0.0):
        self.sr = sr
        # Fatigue, for endurance tests: per minute played, the scatter grows,
        # the second finger (odd notes) drifts late and plucks get softer.
        self.tire = (tire_jitter_ms_per_min / 1000, tire_m_late_ms_per_min / 1000,
                     tire_level_db_per_min)
        self.rt = round_trip_s * sr
        self.bias = bias_ms / 1000
        self.base_jitter = base_jitter_ms / 1000
        self.comfort = comfort_nps
        self.rng = np.random.default_rng(seed)
        self.p = pluck(sr)
        self._voices: list[list] = []   # [start, gain, damp_at or None], input frames
        self._planned = set()
        self.planned_errors_ms: list[float] = []

    def _plan(self, core, upto: int) -> None:
        for bar in list(core.bars):
            if bar.start + self.rt > upto + self.sr:
                break
            nps = bar.notes_per_sec
            strain = max(0.0, nps / self.comfort - 1.0)
            jitter = self.base_jitter * (1 + 6 * strain ** 1.5)
            minutes = bar.start / self.sr / 60
            jitter += self.tire[0] * minutes
            for i, s in enumerate(bar.slot_frames()):
                key = (bar.index, i)
                if key in self._planned:
                    continue
                self._planned.add(key)
                if self.rng.random() < min(0.6, 0.5 * strain ** 2):
                    continue  # missed note
                err = self.rng.normal(self.bias * (1 + strain), jitter)
                if (bar.first_note + i) % 2:
                    err += self.tire[1] * minutes
                start = s + self.rt + err * self.sr
                if self._voices and self._voices[-1][2] is None:
                    gap = start - self._voices[-1][0]
                    self._voices[-1][2] = start - min(0.02 * self.sr, 0.25 * gap)
                self._voices.append([start, 10 ** ((self.rng.normal(0, 2) - self.tire[2] * minutes) / 20),
                                     None])
                self.planned_errors_ms.append(err * 1000)

    def render(self, core, frame0: int, n: int) -> np.ndarray:
        self._plan(core, frame0 + n)
        out = self.rng.normal(0, 1e-4, n).astype(np.float32)
        live = []
        for v in self._voices:
            start, g, damp = v
            a = int(round(start)) - frame0
            if a + len(self.p) <= 0:
                continue
            live.append(v)
            if a >= n:
                continue
            lo, hi = max(0, -a), min(len(self.p), n - a)
            seg = self.p[lo:hi] * g
            if damp is not None:  # the next finger has landed: down to 20 %
                cut = int(round(damp)) - frame0 - a - lo
                if cut < len(seg):
                    seg[max(0, cut):] *= 0.2
            out[a + lo: a + hi] += seg
        self._voices = live
        return out
