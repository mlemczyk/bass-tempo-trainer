"""What the metronome plays next, bar by bar.

A lesson hands the core one Bar at a time, when the previous one is about to
run out. Trial bars are scored as a group once the last of them has been
played and its plucks can no longer arrive. In training, the tempo can only
change once that result is in, so at fast tempos a "bridge" bar (played,
not scored) may sit between a trial and the next one.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import numpy as np

from . import scoring


@dataclass
class Bar:
    index: int
    start: float          # output frame of the downbeat (fractional)
    bpm: float
    beats: int
    subdiv: int
    kind: str             # countin | trial | bridge | settle
    trial: int | None
    closes_trial: bool
    first_note: int       # running note number of slot 0 (for the i/m alternation)
    sr: int

    @property
    def interval(self) -> float:
        """Frames between notes."""
        return self.sr * 60.0 / (self.bpm * self.subdiv)

    @property
    def n_slots(self) -> int:
        return self.beats * self.subdiv

    @property
    def end(self) -> float:
        return self.start + self.n_slots * self.interval

    @property
    def notes_per_sec(self) -> float:
        return self.bpm * self.subdiv / 60.0

    def slot_frames(self) -> np.ndarray:
        return self.start + np.arange(self.n_slots) * self.interval


@dataclass
class LessonSettings:
    start_bpm: float = 60.0
    subdiv: int = 2
    beats: int = 4
    bars_per_trial: int = 4
    step_bpm: float = 4.0
    pass_score: float = 80.0
    attempts: int = 3          # failed trials at one tempo before the lesson ends
    settle_bars: int = 1       # unscored bars at a new tempo before its trial
    countin_bars: int = 1
    max_bpm: float = 400.0
    ignore_offset: bool = False  # score evenness only, not lead/lag vs the click


class Lesson:
    """Base: count-in, then back-to-back trials at one tempo, forever."""

    name = "practice"

    def __init__(self, s: LessonSettings):
        self.s = s
        self.bpm = float(s.start_bpm)
        self.finished = False
        self._countin = s.countin_bars
        self._settle = 0
        self._trial = 0
        self._bars_left = 0
        self._awaiting: int | None = None
        self.results: list[scoring.TrialResult] = []

    def next_bar(self, start: float, index: int, first_note: int, sr: int) -> Bar | None:
        if self.finished:
            return None

        def bar(kind, trial=None, closes=False):
            return Bar(index, start, self.bpm, self.s.beats, self.s.subdiv, kind,
                       trial, closes, first_note, sr)

        if self._countin > 0:
            self._countin -= 1
            return bar("countin")
        if self._bars_left == 0:
            if self._awaiting is not None:
                return bar("bridge")
            if self._settle > 0:
                self._settle -= 1
                return bar("settle")
            self._trial += 1
            self._bars_left = self.s.bars_per_trial
            if self.waits_for_results:
                self._awaiting = self._trial
        self._bars_left -= 1
        return bar("trial", self._trial, self._bars_left == 0)

    waits_for_results = False

    def evaluate(self, trial: int, slots: np.ndarray, onsets: np.ndarray, sr: int, bpm: float):
        return scoring.evaluate(trial, slots, onsets, sr, bpm=bpm, subdiv=self.s.subdiv,
                                pass_score=self.s.pass_score,
                                ignore_offset=self.s.ignore_offset)

    def on_result(self, r: scoring.TrialResult) -> None:
        self.results.append(r)

    @property
    def best_bpm(self) -> float | None:
        ok = [r.bpm for r in self.results if r.passed]
        return max(ok) if ok else None


class PracticeLesson(Lesson):
    """Fixed tempo; each run of `bars_per_trial` bars is scored. Runs until stopped."""
    name = "practice"


class LadderLesson(Lesson):
    """Training: pass a trial and the tempo steps up; fail `attempts` times
    in a row at one tempo and the lesson ends."""

    name = "train"
    waits_for_results = True

    def __init__(self, s: LessonSettings):
        super().__init__(s)
        self.fails = 0

    def on_result(self, r: scoring.TrialResult) -> None:
        super().on_result(r)
        self._awaiting = None
        if r.passed:
            self.fails = 0
            if self.bpm + self.s.step_bpm > self.s.max_bpm:
                self.finished = True
                return
            self.bpm += self.s.step_bpm
            self._settle = self.s.settle_bars
        else:
            self.fails += 1
            if self.fails >= self.s.attempts:
                self.finished = True


class EnduranceLesson(Lesson):
    """Stamina test: one fixed tempo (a share of the player's record), back-to-
    back trials scored on evenness only, until two in a row fail. The score is
    how long it held (see endurance.summarize)."""

    name = "endurance"
    fails_to_end = 2

    def __init__(self, s: LessonSettings, record_nps: float | None = None, pct: float | None = None):
        super().__init__(dataclasses.replace(s, ignore_offset=True))
        self.record_nps, self.pct = record_nps, pct
        self.fails = 0
        self.ended_on_fails = False

    def on_result(self, r: scoring.TrialResult) -> None:
        super().on_result(r)
        self.fails = 0 if r.passed else self.fails + 1
        if self.fails >= self.fails_to_end:
            self.finished = self.ended_on_fails = True


class CalibrationLesson(Lesson):
    """Measures the round trip, click out -> pluck in. Play along with 16 slow
    clicks (or loop the click output back into the input with a cable): the
    median offset of the plucks from the clicks becomes the latency."""

    name = "calibrate"
    waits_for_results = True

    def __init__(self, bpm: float = 80.0, beats: int = 4, bars: int = 4):
        super().__init__(LessonSettings(start_bpm=bpm, subdiv=1, beats=beats,
                                        bars_per_trial=bars, countin_bars=1))
        self.latency_s: float | None = None
        self.spread_ms: float | None = None
        self.matched = 0

    def next_bar(self, start, index, first_note, sr):
        if self._trial >= 1 and self._bars_left == 0:
            return None  # one trial only
        return super().next_bar(start, index, first_note, sr)

    def evaluate(self, trial, slots, onsets, sr, bpm):
        # Raw onsets (latency not yet removed) land late; look from -100 ms to +400 ms.
        offs = []
        for s in slots:
            near = onsets[(onsets >= s - 0.1 * sr) & (onsets <= s + 0.4 * sr)]
            if len(near):
                offs.append((near[np.argmin(np.abs(near - s - 0.05 * sr))] - s) / sr)
        offs = np.array(offs)
        self.matched = len(offs)
        if len(offs) >= len(slots) // 2:
            med = float(np.median(offs))
            keep = offs[np.abs(offs - med) < 0.06]
            self.latency_s = float(np.median(keep))
            self.spread_ms = float(np.std(keep) * 1000)
        return None

    def on_result(self, r) -> None:
        self._awaiting = None
        self.finished = True
