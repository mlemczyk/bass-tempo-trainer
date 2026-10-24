"""The trainer's heart: one block of input in, one block of clicks out.

Everything runs on a single sample clock, the duplex stream's frame counter.
Clicks are scheduled at exact output frames; plucks are detected at input
frames; the measured round trip (click leaves the computer -> you hear it ->
you pluck -> the pluck comes back in) converts one to the other. Because both
directions share one counter, there is nothing to drift.

`process()` runs inside the audio callback, so it only does bounded numpy
work and talks to the UI through a queue.
"""
from __future__ import annotations

import collections
import queue

import numpy as np

from .lessons import Bar, Lesson
from .onset import OnsetDetector
from . import scoring


class Core:
    def __init__(self, sr: int, lesson: Lesson, detector: OnsetDetector,
                 clicks: dict[str, np.ndarray], *, latency_s: float,
                 click_subdivisions: bool = False, volume: float = 0.8,
                 record: bool = False, start_delay_s: float = 0.4,
                 close_margin_s: float = 0.0):
        self.sr = sr
        self.lesson = lesson
        self.detector = detector
        self.clicks = clicks
        self.latency = latency_s * sr
        self.click_subdivisions = click_subdivisions
        self.volume = volume
        self.start_delay = int(start_delay_s * sr)
        self.close_margin = close_margin_s * sr

        self.events: queue.SimpleQueue = queue.SimpleQueue()
        self.frame = 0
        self.bars: list[Bar] = []
        self.onsets: list[float] = []      # latency-corrected, on the click clock
        self.level_db = -120.0
        self.recording: list[np.ndarray] | None = [] if record else None
        self.done_scheduling = False
        self.finished = False
        self._render_from = 0
        self._voices: list[tuple[np.ndarray, int, int]] = []
        self._open: dict[int, list[Bar]] = {}
        self._max_click = max(len(c) for c in clicks.values())
        self._recent_err: collections.deque = collections.deque(maxlen=8)

    # -- control (any thread) -------------------------------------------
    def stop(self) -> None:
        self.done_scheduling = True
        self._open.clear()

    # -- audio thread ----------------------------------------------------
    def process(self, x: np.ndarray, out: np.ndarray) -> None:
        n = len(x)
        f0 = self.frame
        if n:
            self.level_db = 20 * np.log10(float(np.max(np.abs(x))) + 1e-9)
        if self.recording is not None:
            self.recording.append(np.clip(x * 32767, -32768, 32767).astype(np.int16))
        for o in self.detector.process(x, f0):
            g = o - self.latency
            self.onsets.append(g)
            self._feedback(g)
        self._close_trials(f0 + n)
        self._schedule(f0 + n)
        self._render(out, f0, n)
        self.frame = f0 + n
        if self.done_scheduling and not self._open and not self.finished:
            last = self.bars[-1].end if self.bars else 0
            if self.frame > last + 0.3 * self.sr:
                self.finished = True
                self.events.put(("finished", self.frame))

    def _bar_at(self, g: float) -> Bar | None:
        for bar in reversed(self.bars[-4:]):
            if bar.start - 0.5 * bar.interval <= g < bar.end + 0.5 * bar.interval:
                return bar
        return None

    def _feedback(self, g: float) -> None:
        """Immediate per-pluck feedback: which note it was aimed at, how far off."""
        bar = self._bar_at(g)
        if bar is None:
            self.events.put(("pluck", g, None, None, "stray"))
            return
        i = int(round((g - bar.start) / bar.interval))
        i = min(max(i, 0), bar.n_slots - 1)
        err = (g - (bar.start + i * bar.interval)) / self.sr
        if getattr(self.lesson.s, "ignore_offset", False):
            # Evenness only: grade each pluck against the player's own steady
            # offset (median of the last few), not against the click.
            self._recent_err.append(err)
            err -= float(np.median(self._recent_err))
        g_name, _ = scoring.grade(err, bar.interval / self.sr)
        self.events.put(("pluck", g, bar.first_note + i, round(err * 1000, 1), g_name))

    def _schedule(self, upto: int) -> None:
        while not self.done_scheduling and (not self.bars or self.bars[-1].end <= upto):
            if self.bars:
                prev = self.bars[-1]
                start, first = prev.end, prev.first_note + prev.n_slots
            else:
                start, first = float(upto + self.start_delay), 0
            bar = self.lesson.next_bar(start, len(self.bars), first, self.sr)
            if bar is None:
                self.done_scheduling = True
                break
            self.bars.append(bar)
            self.detector.set_expected_interval(1000 * bar.interval / self.sr)
            if bar.trial is not None:
                self._open.setdefault(bar.trial, []).append(bar)
            self.events.put(("bar", bar))

    def _close_trials(self, upto: int) -> None:
        # Every onset at or before `safe` (click clock) has been reported.
        safe = upto - self.detector.lag_frames - self.latency
        for trial, bars in list(self._open.items()):
            last = bars[-1]
            if not last.closes_trial:
                continue
            slots = np.concatenate([b.slot_frames() for b in bars])
            if safe < slots[-1] + max(0.5 * last.interval, self.close_margin):
                continue
            del self._open[trial]
            on = np.array(self.onsets)
            near = on[(on >= slots[0] - last.interval) & (on <= slots[-1] + last.interval
                                                          + self.close_margin)]
            r = self.lesson.evaluate(trial, slots, near, self.sr, last.bpm)
            self.lesson.on_result(r)
            self.events.put(("trial", r) if r is not None else ("calibration", self.lesson))

    def _render(self, out: np.ndarray, f0: int, n: int) -> None:
        voices = [(a, k, 0) for a, k, _ in self._voices]
        for bi in range(self._render_from, len(self.bars)):
            bar = self.bars[bi]
            if bar.start >= f0 + n:
                break
            if bar.end < f0 and bi == self._render_from:
                self._render_from += 1
                continue
            for i, s in enumerate(bar.slot_frames()):
                p = int(round(s))
                if f0 <= p < f0 + n:
                    if i == 0:
                        c = self.clicks["accent"]
                    elif i % bar.subdiv == 0:
                        c = self.clicks["beat"]
                    elif self.click_subdivisions:
                        c = self.clicks["sub"]
                    else:
                        continue
                    voices.append((c, 0, p - f0))
        keep = []
        for arr, k, a in voices:
            m = min(len(arr) - k, n - a)
            if m > 0:
                out[a:a + m] += (arr[k:k + m] * self.volume)[:, None]
            if k + m < len(arr):
                keep.append((arr, k + m, 0))
        self._voices = keep
