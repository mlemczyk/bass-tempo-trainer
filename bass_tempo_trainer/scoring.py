"""Matching plucks to the metronome grid, and scoring a trial.

All times here are output-frame positions on the metronome's own clock;
onsets have already had the measured round-trip latency taken off.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np

GRADES = ("tight", "ok", "off", "miss")


def windows(interval_s: float) -> tuple[float, float]:
    """(tight, hit) half-widths in seconds for a note interval. Both scale
    with tempo, within bounds a player can actually perceive and control."""
    tight = float(np.clip(0.06 * interval_s, 0.008, 0.020))
    hit = float(np.clip(0.30 * interval_s, 0.025, 0.060))
    return tight, hit


def grade(err_s: float, interval_s: float) -> tuple[str, float]:
    """Grade one pluck: 100 points inside the tight window, falling linearly
    to 0 at the edge of the hit window."""
    tight, hit = windows(interval_s)
    e = abs(err_s)
    if e <= tight:
        return "tight", 100.0
    if e <= hit:
        return "ok", 100.0 * (hit - e) / (hit - tight)
    return "off", 0.0


def match(slots: np.ndarray, onsets: np.ndarray, radius: float) -> tuple[list[int | None], int]:
    """Pair each slot with at most one onset (nearest first, within `radius`).
    Returns, per slot, the index of its onset or None, and the count of
    onsets inside the slots' span that matched nothing (extra plucks)."""
    pairs = []
    for i, s in enumerate(slots):
        for j in np.where(np.abs(onsets - s) <= radius)[0]:
            pairs.append((abs(onsets[j] - s), i, int(j)))
    pairs.sort()
    got: list[int | None] = [None] * len(slots)
    used = set()
    for _, i, j in pairs:
        if got[i] is None and j not in used:
            got[i] = j
            used.add(j)
    if len(slots):
        lo, hi = slots[0] - radius, slots[-1] + radius
        extras = sum(1 for j, o in enumerate(onsets) if lo <= o <= hi and j not in used)
    else:
        extras = 0
    return got, extras


@dataclass
class TrialResult:
    trial: int
    bpm: float
    subdiv: int
    notes_per_sec: float
    start_s: float
    end_s: float
    n_slots: int
    hits: int
    misses: int
    extras: int
    score: float
    bias_ms: float        # + late (dragging), - early (rushing)
    jitter_ms: float      # spread of timing errors
    drift_ms: float       # change in error from start to end of the trial
    passed: bool
    # Median error of every matched pluck, hit window or not: a steady lead
    # past the window shows here while bias_ms (hits only) can't see it.
    offset_ms: float | None = None
    verdict: list[str] = field(default_factory=list)
    errors_ms: list[float | None] = field(default_factory=list)
    slot_s: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def steady_offset(slots: np.ndarray, onsets: np.ndarray, interval_frames: float) -> float:
    """The player's steady lead (-) or lag (+) against the grid, in frames:
    the circular mean of every onset's phase within its note interval, so it
    works for any offset short of half an interval and ignores which slot a
    pluck belongs to."""
    if not len(onsets):
        return 0.0
    ph = 2 * np.pi * (onsets - slots[0]) / interval_frames
    ang = float(np.angle(np.mean(np.exp(1j * ph))))
    return ang / (2 * np.pi) * interval_frames


def evaluate(trial: int, slots: np.ndarray, onsets: np.ndarray, sr: int, *,
             bpm: float, subdiv: int, pass_score: float,
             ignore_offset: bool = False) -> TrialResult:
    """Score one trial: `slots` are the note slots it covered, `onsets` every
    (latency-corrected) onset near them, both in frames.

    `ignore_offset` scores evenness only: the grid is moved onto the
    player's own steady lead or lag first, so every note, a steady interval
    and no notes in between is a pass however far ahead of the click it sits.
    Errors, bias and offset are still reported against the click."""
    interval = 60.0 / (bpm * subdiv)
    radius = 0.5 * interval * sr
    shift = steady_offset(slots, onsets, interval * sr) if ignore_offset else 0.0
    onsets = onsets - shift
    got, extras = match(slots, onsets, radius)
    errs: list[float | None] = []
    points = []
    hit_t, hit_e = [], []
    for s, j in zip(slots, got):
        if j is None:
            errs.append(None)
            points.append(0.0)
            continue
        e = (onsets[j] - s) / sr
        g, p = grade(e, interval)
        errs.append(round((e + shift / sr) * 1000, 2))
        points.append(p)
        if g != "off":
            hit_t.append(s / sr)
            hit_e.append(e)
    n = len(slots)
    hits = len(hit_e)
    misses = sum(1 for e in errs if e is None)
    score = max(0.0, (sum(points) - 50.0 * extras) / n) if n else 0.0
    bias = float(np.mean(hit_e)) + shift / sr if hit_e else 0.0
    matched = [e / 1000 for e in errs if e is not None]
    offset = float(np.median(matched)) if matched else None
    jitter = float(np.std(hit_e)) if len(hit_e) > 1 else 0.0
    drift = 0.0
    if len(hit_e) >= 4:
        slope = np.polyfit(np.array(hit_t) - hit_t[0], hit_e, 1)[0]
        drift = float(slope * (hit_t[-1] - hit_t[0]))

    tight, _ = windows(interval)
    tol = float(np.clip(0.04 * interval, 0.006, 0.010))  # a steady lean past 10 ms is audible
    verdict = []
    lean = offset if offset is not None else bias
    if ignore_offset:
        pass  # a steady lead or lag is not judged; the panel still shows it
    elif lean < -tol:
        verdict.append(f"rushing {abs(lean) * 1000:.0f} ms")
    elif lean > tol:
        verdict.append(f"dragging {lean * 1000:.0f} ms")
    if drift < -2 * tol:
        verdict.append("speeding up")
    elif drift > 2 * tol:
        verdict.append("slowing down")
    if jitter > tight:
        verdict.append("unsteady")
    if misses:
        verdict.append(f"{misses} missed")
    if extras:
        verdict.append(f"{extras} extra")
    if not verdict:
        verdict.append("locked in")

    return TrialResult(
        trial=trial, bpm=bpm, subdiv=subdiv, notes_per_sec=bpm * subdiv / 60.0,
        start_s=float(slots[0] / sr) if n else 0.0, end_s=float(slots[-1] / sr) if n else 0.0,
        n_slots=n, hits=hits, misses=misses, extras=extras, score=round(score, 1),
        bias_ms=round(bias * 1000, 1), jitter_ms=round(jitter * 1000, 1),
        drift_ms=round(drift * 1000, 1), passed=bool(score >= pass_score),
        offset_ms=round(offset * 1000, 1) if offset is not None else None, verdict=verdict,
        errors_ms=errs, slot_s=[round(float(s) / sr, 4) for s in slots])
