"""Streaming pluck-onset detector for a raw (unprocessed) bass signal.

Stage 1, candidates. A raw bass pickup signal is a pulse train: every
fundamental period carries a sharp edge, so any short-window level envelope
(at any frequency) pulses once per period and a plain "level rose" test fires
on the ripple. A pluck is different in kind: a broadband burst, up to 6 kHz
and beyond, that the steady note does not have. So the detection function is
spectral flux: the summed increase of log-magnitude (400 Hz - 10 kHz) between
a frame and the frame 10 ms earlier, with frames (2048 samples) longer than
the period of the lowest string, so the note itself reads as steady. Its
peaks are picked causally, against a threshold that follows both the
background and the height of recent plucks. Flux peaks once the burst has
just entered the frame, so a candidate is placed at the frame's newest sample
less a fixed lead (12.5 ms, measured on synthetic plucks).

Stage 2, plucks only. In two-finger playing the next finger LANDING on the
string is a broadband transient too, typically 30-50 ms before it releases
the string, and can out-score the pluck itself. On real M1 takes the two are
cleanly told apart by what the level does across them: a landing damps the
string (-2 to -7 dB), a pluck re-excites it (+1 to +20 dB). So when two
candidates fall within a pair window of each other, the one whose level rose
less is dropped. A lone candidate is kept whatever its level does: a soft
pluck after a loud one need not raise the level at all.

Stage 3, one note per attack (2026-10-23). On a low string the flux still
ripples: the open B (31 Hz, 32 ms period) barely fits once in a frame, and
on real takes at 192-216 BPM it fired a second onset 2, 3 or 4 periods
(65-130 ms) from a real pluck, 14 times in 33 trials. None had an attack in
the audio: the high-band (>800 Hz) level jump across the onset was -7 to
+4 dB, against 6-34 dB for the plucks. So each onset's attack is measured
that way, and one within `close_win` of a neighbour (half a note
interval, at most 150 ms) is kept only if it has the strongest attack of
its group or a fresh attack of its own (>= attack_min_db, 12 dB: on those
takes every ripple onset was <= 9.6 dB and the two real extra plucks were
13.0 and 21.4 dB; synthetic double plucks are >= 25 dB). A rule relative
to the neighbour's attack was tried and dropped: it also removed real
double plucks, which can be 15 dB softer than the note before. A lone onset is
kept whatever its attack, so a soft pluck still counts, and a real double
pluck still shows as an extra.

Any constant offset between a reported onset and "the moment the note
sounds" is absorbed by latency calibration. Scoring depends only on the
offset being the same from pluck to pluck.
"""
from __future__ import annotations

from collections import deque

import numpy as np
from scipy import ndimage, signal


class OnsetDetector:
    def __init__(self, sr: int, *, frame: int = 2048, hop_ms: float = 1.0,
                 lag_ms: float = 10.0, f_lo: float = 400.0, f_hi: float = 10000.0,
                 sensitivity: float = 0.5, interval_ms: float = 250.0,
                 report_lead_ms: float = 12.5, attack_min_db: float = 12.0):
        self.sr = sr
        self.attack_min_db = attack_min_db
        self._hp = signal.butter(4, 800.0, "highpass", fs=sr, output="sos")
        self.N = frame
        self.hop = max(1, int(round(sr * hop_ms / 1000)))
        self.lag = max(1, int(round(lag_ms / hop_ms)))
        f = np.fft.rfftfreq(frame, 1 / sr)
        self.band = np.where((f >= f_lo) & (f <= f_hi))[0]
        self.window = np.hanning(frame).astype(np.float32)
        # The log1p knee: magnitudes well above it are compared as ratios.
        self.gain = 1e4 * 2048 / frame
        self.report_lead = report_lead_ms * sr / 1000
        self.set_sensitivity(sensitivity)
        self.set_expected_interval(interval_ms)

        self._buf = np.zeros(frame, np.float32)  # the newest `frame` input samples
        self._buf_end = 0                        # input frame just after _buf[-1]
        self._next_end = frame                   # input frame at which the next analysis frame ends
        self._L: deque[np.ndarray] = deque(maxlen=self.lag + 1)
        self._d: deque[tuple[int, float]] = deque()  # (frame end, flux) for peak picking
        self._bg: deque[float] = deque(maxlen=int(1000 / hop_ms))  # ~1 s of flux
        self._peaks: deque[float] = deque(maxlen=8)  # heights of recent accepted plucks
        self._last_cand_end = -10**12
        self._cands: list[tuple[float, float]] = []  # (onset frame, flux height) awaiting stage 2
        self._raw = np.zeros(0, np.float32)          # recent input, for the level test
        self._raw_start = 0
        self._held: list[tuple[float, float]] = []   # (onset, attack dB) awaiting stage 3
        self._last_out: tuple[float, float] | None = None  # last onset stage 3 kept
        # The first frames see the noise floor appear out of zeros; ignore them.
        self._warm_until = frame + int(0.1 * sr)

    # -- tuning -----------------------------------------------------------
    def set_sensitivity(self, s: float) -> None:
        """0 = only strong, clear plucks ... 1 = catches soft ones (more false hits)."""
        s = float(np.clip(s, 0.0, 1.0))
        self.sensitivity = s
        self.peak_frac = 0.45 - 0.35 * s      # fraction of recent pluck height
        self.bg_mult = 4.0 - 2.5 * s          # multiple of the background median
        self.abs_min = 2.0 * (1.0 - s) + 0.3  # in summed-log units

    def set_expected_interval(self, ms: float) -> None:
        """Tell the detector how far apart notes should be. Sets the minimum
        spacing of two candidates (also the peak-picking half-window) and the
        width of the level windows either side of a candidate, so neither
        reaches the neighbouring notes at speed."""
        self.interval_ms = ms
        # Narrow on purpose: a finger landing 15-20 ms before its pluck must
        # survive as its own candidate, so stage 2 can choose between them.
        refr = float(np.clip(0.3 * ms, 6.0, 12.0))
        self.half = max(2, int(round(refr * self.sr / 1000 / self.hop)))
        self.level_win = int(np.clip(0.3 * ms, 8.0, 27.0) * self.sr / 1000)
        self.level_gap = int(0.003 * self.sr)
        self.pair_win = int(min(60.0, 0.45 * ms) * self.sr / 1000)
        self.close_win = int(min(150.0, 0.5 * ms) * self.sr / 1000)

    @property
    def _decide_after(self) -> float:
        """How long after a candidate both its own level window and any
        partner's (one within pair_win) are complete."""
        cand_delay = self.report_lead + (self.half + 1) * self.hop
        return self.pair_win + max(self.level_gap + self.level_win, cand_delay)

    @property
    def lag_frames(self) -> int:
        """How far behind the newest input an onset can still be reported."""
        return int(self._decide_after + self.close_win + 2 * self.hop)

    # -- processing -------------------------------------------------------
    def process(self, x: np.ndarray, frame0: int) -> list[float]:
        """Feed one block starting at input frame `frame0` (blocks must be
        contiguous). Returns onset positions in input frames (float)."""
        x = np.asarray(x, np.float32)
        joined = np.concatenate([self._buf, x])
        joined_start = self._buf_end - self.N
        self._buf_end = frame0 + len(x)
        self._buf = joined[-self.N:]
        keep = int(0.4 * self.sr)
        self._raw = np.concatenate([self._raw, x])
        if len(self._raw) > keep:
            self._raw_start += len(self._raw) - keep
            self._raw = self._raw[-keep:]

        ends = np.arange(self._next_end, self._buf_end + 1, self.hop)
        if len(ends):
            self._next_end = int(ends[-1]) + self.hop
            idx = (ends - joined_start)[:, None] + np.arange(-self.N, 0)[None, :]
            frames = joined[idx] * self.window
            mag = np.abs(np.fft.rfft(frames, axis=1))[:, self.band]
            L = np.log1p(self.gain * mag)
            Lmax = ndimage.maximum_filter1d(L, 3, axis=1)
            for e, l_now, l_max in zip(ends, L, Lmax):
                if len(self._L) == self._L.maxlen:
                    flux = float(np.maximum(0.0, l_now - self._L[0]).sum())
                else:
                    flux = 0.0
                self._L.append(l_max)
                self._d.append((int(e), flux))
                self._pick()
        return self._confirm()

    def _pick(self) -> None:
        """Stage 1: decide on the hop `half` back, now its look-ahead exists."""
        if len(self._d) < 2 * self.half + 1:
            return
        while len(self._d) > 2 * self.half + 1:
            self._bg.append(self._d.popleft()[1])
        c_end, c = self._d[self.half]
        if c <= 0 or c_end < self._warm_until or any(v > c for _, v in self._d) or \
                any(v == c for i, (_, v) in enumerate(self._d) if i < self.half):
            return
        bg = float(np.median(self._bg)) if self._bg else 0.0
        ref = float(np.median(self._peaks)) if self._peaks else 0.0
        thr = max(self.abs_min, self.bg_mult * bg, self.peak_frac * ref)
        if c < thr or c_end - self._last_cand_end < self.half * self.hop:
            return
        self._last_cand_end = c_end
        # Parabolic interpolation over the three hops around the peak.
        a, b = self._d[self.half - 1][1], self._d[self.half + 1][1]
        den = a - 2 * c + b
        off = 0.5 * (a - b) / den if den < 0 else 0.0
        self._cands.append((c_end + off * self.hop - self.report_lead, c))

    def _level(self, a: float, b: float) -> float | None:
        i, j = int(a) - self._raw_start, int(b) - self._raw_start
        if i < 0 or j > len(self._raw) or j <= i:
            return None
        return 10 * np.log10(float(np.mean(self._raw[i:j] ** 2)) + 1e-14)

    def _rise(self, t: float) -> float:
        pre = self._level(t - self.level_gap - self.level_win, t - self.level_gap)
        post = self._level(t + self.level_gap, t + self.level_gap + self.level_win)
        return 0.0 if pre is None or post is None else post - pre

    def _confirm(self) -> list[float]:
        """Stage 2: of two candidates within pair_win, keep the one whose
        level rose more (the pluck, not the finger landing before it)."""
        out = []
        while self._cands and self._cands[0][0] + self._decide_after <= self._buf_end:
            t, h = self._cands[0]
            if len(self._cands) > 1 and self._cands[1][0] - t <= self.pair_win:
                if self._rise(self._cands[1][0]) >= self._rise(t):
                    self._cands.pop(0)  # a landing; its pluck is next in line
                else:
                    del self._cands[1]
                continue
            self._cands.pop(0)
            self._peaks.append(h)
            self._held.append((t, self._attack(t)))
        return self._one_per_attack()

    def _attack(self, t: float) -> float:
        """High-band (>800 Hz) level jump across t, in dB: the peak of a 2 ms
        RMS envelope over the 6 ms after t against its mean 3-12 ms before.
        A pluck is a broadband burst; a low note's ripple is not."""
        sr = self.sr
        a, b = int(t - 0.025 * sr) - self._raw_start, int(t + 0.008 * sr) - self._raw_start
        if a < 0 or b > len(self._raw):
            return float("inf")  # can't tell: treat it as a clear attack
        h = signal.sosfilt(self._hp, self._raw[a:b].astype(np.float64))
        w = int(0.002 * sr)
        env = np.sqrt(np.convolve(h ** 2, np.ones(w) / w, "same"))
        i = int(t) - self._raw_start - a
        pre = env[i - int(0.012 * sr): i - int(0.003 * sr)]
        post = env[i: i + int(0.006 * sr)]
        return 20 * np.log10((post.max() + 1e-12) / (pre.mean() + 1e-12))

    def _one_per_attack(self) -> list[float]:
        """Stage 3: of onsets within close_win of each other, keep the
        strongest attack and any other with a fresh attack of its own."""
        out = []
        while self._held and self._held[0][0] + self._decide_after + self.close_win <= self._buf_end:
            t, a = self._held.pop(0)
            rivals = [x for x in self._held if x[0] - t <= self.close_win]
            if self._last_out and t - self._last_out[0] <= self.close_win:
                rivals.append(self._last_out)
            if a < self.attack_min_db and any(r[1] > a for r in rivals):
                continue  # a ripple of the note next to it
            self._last_out = (t, a)
            out.append(t)
        return out
