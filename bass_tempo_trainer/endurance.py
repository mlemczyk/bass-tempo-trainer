"""The stamina score of an endurance lesson, and how the player tired.

The score is the time held: from the first trial's first note to the last
note of the last trial before the two failures that ended it (or to the
stop, if the player stopped first; `ended` says which). A single failed
trial followed by passes is part of the held time.

Three fatigue signs, each as the first quarter of the held trials against
the last quarter, so a short test still has a before and after:

- spread: the timing spread of the hits (ms). It grows as the hands tire.
- m_minus_i: the mean timing of m minus i, after each trial's own steady
  offset is taken out (ms, + = m late), whichever finger starts.
  On the author's runs one finger pulled ahead as speed rose (3 -> 10 ms).
- level: the median pluck level (dB, low band, from the saved input
  audio). A tiring pluck gets softer. None without audio.
"""
from __future__ import annotations

import numpy as np
from scipy import signal


def _quarters(n: int) -> tuple[slice, slice]:
    q = max(1, n // 4)
    return slice(0, q), slice(n - q, n)


def _note_number(bars, frame: float) -> int | None:
    for b in bars:
        if b.start - 0.5 * b.interval <= frame < b.end - 0.5 * b.interval:
            return b.first_note + int(round((frame - b.start) / b.interval))
    return None


def summarize(core, lesson, start_finger: str = "i") -> dict:
    sr = core.sr
    res = list(lesson.results)
    if getattr(lesson, "ended_on_fails", False):
        held = res[:-lesson.fails_to_end]
        ended = "failed"
    else:
        held = res
        ended = "stopped"
    bpm = lesson.s.start_bpm
    nps = bpm * lesson.s.subdiv / 60.0
    out = {
        "tempo_bpm": bpm, "subdiv": lesson.s.subdiv, "notes_per_s": round(nps, 3),
        "record_nps": lesson.record_nps, "pct_of_record": lesson.pct,
        "ended": ended, "trials_held": len(held),
        "held_s": 0.0, "held_notes": 0, "fatigue": None,
    }
    if not held:
        return out
    interval = 60.0 / (bpm * lesson.s.subdiv)
    out["held_s"] = round(held[-1].end_s - held[0].start_s + interval, 1)
    out["held_notes"] = int(sum(r.n_slots for r in held))

    # Per trial: hit spread, finger gap, pluck level.
    rec = None
    if core.recording:
        x = np.concatenate(core.recording).astype(np.float64) / 32767
        lo = signal.sosfiltfilt(signal.butter(4, 400.0, "lowpass", fs=sr, output="sos"), x)
        rec = lo
    spread, gap, level = [], [], []
    for r in held:
        spread.append(r.jitter_ms)
        errs = [e for e in r.errors_ms if e is not None]
        if not errs:
            gap.append(np.nan)
            level.append(np.nan)
            continue
        off = float(np.median(errs))
        f1, f2, lv = [], [], []
        for s, e in zip(r.slot_s, r.errors_ms):
            if e is None:
                continue
            n = _note_number(core.bars, s * sr)
            if n is not None:
                (f1 if n % 2 == 0 else f2).append(e - off)
            if rec is not None:
                k = int(s * sr + e / 1000 * sr + core.latency)
                seg = rec[max(0, k): k + int(0.04 * sr)]
                if len(seg):
                    lv.append(20 * np.log10(np.abs(seg).max() + 1e-9))
        # f1 holds the downbeat finger's notes; report m minus i either way.
        d = float(np.mean(f2) - np.mean(f1)) if f1 and f2 else np.nan
        gap.append(d if start_finger == "i" else -d)
        level.append(float(np.median(lv)) if lv else np.nan)

    a, b = _quarters(len(held))
    mean = lambda v, s: (round(float(np.nanmean(v[s])), 1)  # noqa: E731
                         if np.isfinite(np.asarray(v[s], float)).any() else None)
    spread, gap, level = np.array(spread, float), np.array(gap, float), np.array(level, float)
    fat = {
        "spread_ms_start": mean(spread, a), "spread_ms_end": mean(spread, b),
        "m_minus_i_ms_start": mean(gap, a), "m_minus_i_ms_end": mean(gap, b),
        "level_db_start": mean(level, a), "level_db_end": mean(level, b),
    }
    if fat["level_db_start"] is not None and fat["level_db_end"] is not None:
        fat["level_change_db"] = round(fat["level_db_end"] - fat["level_db_start"], 1)
    else:
        fat["level_change_db"] = None
    if len(held) >= 3:
        t = np.array([(r.start_s + r.end_s) / 2 for r in held]) / 60.0
        fat["spread_growth_ms_per_min"] = round(float(np.polyfit(t - t[0], spread, 1)[0]), 2)
    else:
        fat["spread_growth_ms_per_min"] = None
    out["fatigue"] = fat
    return out
