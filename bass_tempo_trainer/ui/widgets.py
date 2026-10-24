"""Custom-painted widgets: the i/m boxes, the timing strip, the speedometer."""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import theme as T


def _font(px: float, bold: bool = False) -> QFont:
    f = QFont()
    f.setPixelSize(max(6, int(px)))
    f.setBold(bold)
    return f


class PluckBoxes(QWidget):
    """Two boxes, index and middle, lighting in turn with the metronome, and
    flashing the grade of each pluck. Beat dots underneath."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(360, 240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.labels = ("i", "m")
        self.active = -1
        self.phase = 0.0
        self.beats = 4
        self.beat = -1
        self.caption = ""
        self._flash: list[tuple[QColor, float] | None] = [None, None]

    def set_start_finger(self, f: str) -> None:
        self.labels = ("i", "m") if f == "i" else ("m", "i")
        self.update()

    def set_state(self, active: int, phase: float, beat: int, beats: int, caption: str) -> None:
        self.active, self.phase, self.beat, self.beats, self.caption = active, phase, beat, beats, caption
        self.update()

    def flash(self, side: int, color: QColor) -> None:
        self._flash[side] = (color, time.perf_counter())

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cap_h = h * 0.12
        dots_h = h * 0.16
        gap = w * 0.05
        bw = (w - 3 * gap) / 2
        bh = h - cap_h - dots_h - gap
        p.setPen(QColor(T.MUTED))
        p.setFont(_font(cap_h * 0.6, True))
        p.drawText(QRectF(0, 0, w, cap_h), Qt.AlignCenter, self.caption)
        now = time.perf_counter()
        for side in (0, 1):
            r = QRectF(gap + side * (bw + gap), cap_h, bw, bh)
            fill = QColor(T.PANEL_HI)
            if side == self.active:
                c = QColor(T.ACCENT)
                c.setAlpha(int(70 + 185 * max(0.0, 1.0 - self.phase)))
                fill = c
            p.setBrush(fill)
            pen = QPen(QColor(T.BORDER), 2)
            fl = self._flash[side]
            if fl is not None:
                age = now - fl[1]
                if age < 0.35:
                    pen = QPen(fl[0], max(4.0, bw * 0.05 * (1 - age / 0.35)))
                else:
                    self._flash[side] = None
            p.setPen(pen)
            p.drawRoundedRect(r, bw * 0.08, bw * 0.08)
            p.setPen(QColor(T.TEXT))
            p.setFont(_font(bh * 0.5, True))
            p.drawText(r, Qt.AlignCenter, self.labels[side])
        # Beat dots
        n = max(1, self.beats)
        rad = min(dots_h * 0.3, w / (n * 4))
        cy = h - dots_h / 2
        for b in range(n):
            cx = w / 2 + (b - (n - 1) / 2) * rad * 3.2
            on = b == self.beat
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T.ACCENT2 if (on and b == 0) else T.ACCENT if on else T.PANEL_HI))
            p.drawEllipse(QPointF(cx, cy), rad, rad)


class TimingStrip(QWidget):
    """The last plucks as ticks on an early/late axis, newest brightest."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(90)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.span_ms = 60.0
        self.tight_ms = 10.0
        self.ticks: list[tuple[float, str]] = []

    def set_windows(self, tight_ms: float, hit_ms: float) -> None:
        self.tight_ms = tight_ms
        self.span_ms = max(40.0, hit_ms * 1.2)

    def add(self, err_ms: float, grade: str) -> None:
        self.ticks.append((err_ms, grade))
        self.ticks = self.ticks[-16:]
        self.update()

    def clear(self) -> None:
        self.ticks = []
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        lab = h * 0.3
        top, bot = 4, h - lab
        x0 = lambda ms: w / 2 + (w / 2 - 10) * max(-1, min(1, ms / self.span_ms))
        p.fillRect(QRectF(0, top, w, bot - top), QColor(T.PANEL_HI))
        zone = QColor(T.TIGHT)
        zone.setAlpha(45)
        p.fillRect(QRectF(x0(-self.tight_ms), top, x0(self.tight_ms) - x0(-self.tight_ms), bot - top), zone)
        p.setPen(QPen(QColor(T.MUTED), 1))
        p.drawLine(QPointF(w / 2, top), QPointF(w / 2, bot))
        n = len(self.ticks)
        for k, (e, g) in enumerate(self.ticks):
            c = QColor(T.GRADE.get(g, T.MUTED))
            c.setAlpha(int(60 + 195 * (k + 1) / n))
            p.setPen(QPen(c, 4 if k == n - 1 else 3))
            p.drawLine(QPointF(x0(e), top + 6), QPointF(x0(e), bot - 6))
        p.setPen(QColor(T.MUTED))
        p.setFont(_font(lab * 0.55))
        p.drawText(QRectF(8, bot, w / 2, lab), Qt.AlignLeft | Qt.AlignVCenter, f"◀ early   −{self.span_ms:.0f} ms")
        p.drawText(QRectF(w / 2, bot, w / 2 - 8, lab), Qt.AlignRight | Qt.AlignVCenter, f"+{self.span_ms:.0f} ms   late ▶")
        if self.ticks:
            p.setPen(QColor(T.TEXT))
            p.drawText(QRectF(0, bot, w, lab), Qt.AlignCenter, f"{self.ticks[-1][0]:+.0f} ms")


# The dial's full scale: the fastest sustained plucking in the author's tab
# library (1,551 bass tabs, densest 4 s window): four tabs hold a steady
# 16 notes/s. One faster tab (24.5/s) is a 33/s tremolo roll, not plucking.
DIAL_MAX_NPS = 16


class Speedometer(QWidget):
    """Notes per second on a dial. The needle is the metronome's current
    speed; the gold mark is this lesson's fastest passed trial, and the gold
    star outside the dial the all-time record."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 260)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.value = 0.0
        self.shown = 0.0
        self.best: float | None = None
        self.record: float | None = None
        self.bpm = 0.0
        self.vmax = float(DIAL_MAX_NPS)

    def set_speed(self, nps: float, bpm: float) -> None:
        self.value, self.bpm = nps, bpm
        self.vmax = max(self.vmax, 2 * math.ceil(nps / 2))  # only past the library's fastest

    def set_best(self, nps: float | None) -> None:
        self.best = nps

    def set_record(self, nps: float | None) -> None:
        self.record = nps
        if nps:
            self.vmax = max(self.vmax, 2 * math.ceil(nps / 2))

    def reset(self) -> None:
        self.best = None
        self.vmax = float(DIAL_MAX_NPS)
        if self.record:
            self.vmax = max(self.vmax, 2 * math.ceil(self.record / 2))

    def tick(self) -> None:
        self.shown += (self.value - self.shown) * 0.15
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        side = min(w, h * 1.18)
        r = side * 0.42
        c = QPointF(w / 2, h * 0.55)
        arc = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        thick = r * 0.12
        ang = lambda v: 225.0 - 270.0 * max(0.0, min(1.0, v / self.vmax))
        p.setPen(QPen(QColor(T.PANEL_HI), thick, Qt.SolidLine, Qt.FlatCap))
        p.drawArc(arc, int(225 * 16), int(-270 * 16))
        p.setPen(QPen(QColor(T.ACCENT), thick, Qt.SolidLine, Qt.FlatCap))
        p.drawArc(arc, int(225 * 16), int((ang(self.shown) - 225) * 16))
        # ticks
        p.setFont(_font(r * 0.11))
        for v in range(0, int(self.vmax) + 1):
            a = math.radians(ang(v))
            major = v % 2 == 0
            r1, r2 = r - thick * 0.7, r - thick * (1.6 if major else 1.1)
            p.setPen(QPen(QColor(T.MUTED), 2 if major else 1))
            p.drawLine(QPointF(c.x() + r1 * math.cos(a), c.y() - r1 * math.sin(a)),
                       QPointF(c.x() + r2 * math.cos(a), c.y() - r2 * math.sin(a)))
            if major:
                rl = r - thick * 2.5
                p.drawText(QRectF(c.x() + rl * math.cos(a) - 20, c.y() - rl * math.sin(a) - 10, 40, 20),
                           Qt.AlignCenter, str(v))
        if self.best:
            a = math.radians(ang(self.best))
            p.setPen(QPen(QColor(T.GOLD), thick * 0.5, Qt.SolidLine, Qt.FlatCap))
            p.drawLine(QPointF(c.x() + (r + thick * 0.8) * math.cos(a), c.y() - (r + thick * 0.8) * math.sin(a)),
                       QPointF(c.x() + (r - thick * 0.8) * math.cos(a), c.y() - (r - thick * 0.8) * math.sin(a)))
        if self.record:
            a = math.radians(ang(self.record))
            rs = r + thick * 1.6
            centre = QPointF(c.x() + rs * math.cos(a), c.y() - rs * math.sin(a))
            outer, inner = thick * 0.75, thick * 0.3
            pts = [QPointF(centre.x() + (outer if k % 2 == 0 else inner) * math.sin(math.pi * k / 5),
                           centre.y() - (outer if k % 2 == 0 else inner) * math.cos(math.pi * k / 5))
                   for k in range(10)]
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T.GOLD))
            p.drawPolygon(QPolygonF(pts))
            p.setBrush(Qt.NoBrush)
        a = math.radians(ang(self.shown))
        p.setPen(QPen(QColor(T.TEXT), max(3.0, r * 0.03), Qt.SolidLine, Qt.RoundCap))
        p.drawLine(c, QPointF(c.x() + (r - thick * 1.8) * math.cos(a), c.y() - (r - thick * 1.8) * math.sin(a)))
        p.setBrush(QColor(T.TEXT))
        p.drawEllipse(c, r * 0.05, r * 0.05)
        p.setPen(QColor(T.TEXT))
        p.setFont(_font(r * 0.24, True))
        p.drawText(QRectF(c.x() - r, c.y() + r * 0.18, 2 * r, r * 0.32), Qt.AlignCenter, f"{self.bpm:.0f} BPM")
        p.setPen(QColor(T.MUTED))
        p.setFont(_font(r * 0.13))
        p.drawText(QRectF(c.x() - r, c.y() + r * 0.50, 2 * r, r * 0.2), Qt.AlignCenter,
                   f"{self.value:.1f} notes/s")
