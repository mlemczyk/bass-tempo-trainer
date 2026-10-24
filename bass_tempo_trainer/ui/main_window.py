"""The main window: controls, live view, progress graphs."""
from __future__ import annotations

import dataclasses

import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QMainWindow, QMessageBox, QProgressBar, QPushButton, QSlider,
                               QSpinBox, QVBoxLayout, QWidget)

from .. import engine as eng
from .. import scoring, storage
from ..clicks import make_clicks
from ..core import Core
from ..lessons import (CalibrationLesson, EnduranceLesson, LadderLesson, LessonSettings,
                       PracticeLesson)
from ..onset import OnsetDetector
from . import theme as T
from .widgets import PluckBoxes, Speedometer, TimingStrip

SUBDIVS = [(1, "1 – quarters"), (2, "2 – eighths"), (3, "3 – triplets"), (4, "4 – sixteenths")]
CAPTIONS = {"countin": "COUNT-IN", "settle": "SETTLE IN", "bridge": "KEEP GOING"}


def panel() -> QFrame:
    f = QFrame()
    f.setObjectName("panel")
    return f


class SettingsDialog(QDialog):
    def __init__(self, s: storage.Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.s = s
        form = QFormLayout(self)

        self.in_dev, self.out_dev = QComboBox(), QComboBox()
        for combo, inputs, current in [(self.in_dev, True, s.input_device),
                                       (self.out_dev, False, s.output_device)]:
            combo.setEditable(True)
            try:
                for name, ch in eng.list_devices(inputs):
                    combo.addItem(name)
            except Exception:
                pass
            combo.setCurrentText(current)
        self.in_ch = QSpinBox(minimum=1, maximum=8, value=s.input_channel + 1)
        form.addRow("Input device (M1)", self.in_dev)
        form.addRow("Input channel", self.in_ch)
        form.addRow("Click output", self.out_dev)

        self.beats = QSpinBox(minimum=2, maximum=8, value=s.beats)
        self.bars = QSpinBox(minimum=1, maximum=16, value=s.bars_per_trial)
        self.step = QSpinBox(minimum=1, maximum=40, value=s.step_bpm, suffix=" BPM")
        self.pass_score = QSpinBox(minimum=30, maximum=100, value=s.pass_score)
        self.attempts = QSpinBox(minimum=1, maximum=10, value=s.attempts)
        self.settle = QSpinBox(minimum=0, maximum=4, value=s.settle_bars)
        form.addRow("Beats per bar", self.beats)
        form.addRow("Bars per trial", self.bars)
        form.addRow("Speed-up step", self.step)
        form.addRow("Pass score", self.pass_score)
        form.addRow("Attempts at a tempo", self.attempts)
        form.addRow("Settle-in bars after a speed-up", self.settle)
        self.ignore_offset = QCheckBox("Score evenness only: a steady lead or lag behind the "
                                       "click doesn't fail a trial")
        self.ignore_offset.setChecked(s.ignore_offset)
        form.addRow("", self.ignore_offset)
        self.endurance_pct = QSpinBox(minimum=40, maximum=100, value=s.endurance_pct, suffix=" % of record")
        form.addRow("Endurance tempo", self.endurance_pct)

        self.volume = QSlider(Qt.Horizontal, minimum=0, maximum=100, value=int(s.click_volume * 100))
        self.subclicks = QCheckBox("Click every note, not just the beats")
        self.subclicks.setChecked(s.click_subdivisions)
        self.finger = QComboBox()
        self.finger.addItems(["i", "m"])
        self.finger.setCurrentText(s.start_finger)
        self.sens = QSlider(Qt.Horizontal, minimum=0, maximum=100, value=int(s.sensitivity * 100))
        self.audio = QCheckBox("Save each lesson's input audio (for checking the detector)")
        self.audio.setChecked(s.save_audio)
        form.addRow("Click volume", self.volume)
        form.addRow("", self.subclicks)
        form.addRow("Downbeat finger", self.finger)
        form.addRow("Pluck sensitivity", self.sens)
        form.addRow("", self.audio)

        lat = s.latencies.get(s.device_key)
        default = f"{eng.DEFAULT_ROUND_TRIP_S * 1000:.0f} ms (default)"
        self.lat_label = QLabel(f"{lat * 1000:.1f} ms (calibrated)" if lat else default)
        reset = QPushButton("Forget calibration")
        reset.clicked.connect(self._forget)
        row = QHBoxLayout()
        row.addWidget(self.lat_label)
        row.addWidget(reset)
        form.addRow("Round-trip latency", row)
        form.addRow(QLabel(f"Lesson history: {storage.data_dir() / 'sessions'}"))

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _forget(self):
        self.s.latencies.pop(self.s.device_key, None)
        self.lat_label.setText(f"{eng.DEFAULT_ROUND_TRIP_S * 1000:.0f} ms (default)")

    def apply(self) -> None:
        s = self.s
        s.input_device, s.output_device = self.in_dev.currentText(), self.out_dev.currentText()
        s.input_channel = self.in_ch.value() - 1
        s.beats, s.bars_per_trial = self.beats.value(), self.bars.value()
        s.step_bpm, s.pass_score = self.step.value(), self.pass_score.value()
        s.attempts, s.settle_bars = self.attempts.value(), self.settle.value()
        s.ignore_offset = self.ignore_offset.isChecked()
        s.endurance_pct = self.endurance_pct.value()
        s.click_volume = self.volume.value() / 100
        s.click_subdivisions = self.subclicks.isChecked()
        s.start_finger = self.finger.currentText()
        s.sensitivity = self.sens.value() / 100
        s.save_audio = self.audio.isChecked()


class MainWindow(QMainWindow):
    def __init__(self, simulate: bool = False):
        super().__init__()
        self.setWindowTitle("Bass Tempo Trainer" + ("  [simulated player]" if simulate else ""))
        self.simulate = simulate
        self.settings = storage.Settings.load()
        if not simulate:
            storage.backfill_practice_log()
        self.engine = None
        self.core: Core | None = None
        self.mode = ""
        self.latency_s = 0.0
        self.t0 = 0.0
        self.bars = []
        self._build()
        self.timer = QTimer(self, interval=16)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._idle()

    # -- layout ------------------------------------------------------------
    def _build(self):
        s = self.settings
        root = QWidget()
        v = QVBoxLayout(root)
        v.setContentsMargins(16, 16, 16, 8)
        v.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(QLabel("Start BPM"))
        self.bpm = QSpinBox(minimum=20, maximum=400, value=s.start_bpm)
        self.bpm.setObjectName("bpm")
        top.addWidget(self.bpm)
        top.addWidget(QLabel("Notes per beat"))
        self.subdiv = QComboBox()
        for n, label in SUBDIVS:
            self.subdiv.addItem(label, n)
        self.subdiv.setCurrentIndex(max(0, s.subdiv - 1))
        top.addWidget(self.subdiv)
        self.nps = QLabel()
        self.nps.setStyleSheet(f"color: {T.MUTED};")
        top.addWidget(self.nps)
        top.addStretch(1)
        self.btn_practice = QPushButton("Practice")
        self.btn_practice.setToolTip("Fixed tempo; every few bars are scored")
        self.btn_arm = QPushButton("Arm trainer")
        self.btn_arm.setObjectName("primary")
        self.btn_arm.setToolTip("Speeds up each time you hold the tempo")
        self.btn_endure = QPushButton("Endurance")
        self.btn_endure.setToolTip("Stamina test: a fixed tempo at a share of your record, "
                                   "until evenness fails twice in a row")
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setObjectName("danger")
        self.btn_cal = QPushButton("Calibrate")
        self.btn_cal.setToolTip("Measure the click-to-pluck round trip")
        self.btn_set = QPushButton("Settings")
        for b in (self.btn_practice, self.btn_arm, self.btn_endure, self.btn_stop, self.btn_cal, self.btn_set):
            top.addWidget(b)
        v.addLayout(top)
        self.bpm.valueChanged.connect(self._update_nps)
        self.subdiv.currentIndexChanged.connect(self._update_nps)
        self.btn_practice.clicked.connect(lambda: self._start_lesson("practice"))
        self.btn_arm.clicked.connect(lambda: self._start_lesson("train"))
        self.btn_endure.clicked.connect(lambda: self._start_lesson("endurance"))
        self.btn_stop.clicked.connect(self._stop)
        self.btn_cal.clicked.connect(self._calibrate)
        self.btn_set.clicked.connect(self._open_settings)

        mid = QHBoxLayout()
        mid.setSpacing(12)
        left = panel()
        lv = QVBoxLayout(left)
        self.boxes = PluckBoxes()
        self.boxes.set_start_finger(s.start_finger)
        self.strip = TimingStrip()
        lv.addWidget(self.boxes, 1)
        lv.addWidget(self.strip)
        mid.addWidget(left, 5)
        gauge = panel()
        gv = QVBoxLayout(gauge)
        self.gauge = Speedometer()
        if not self.simulate:
            self.gauge.set_record(storage.best_record())
        gv.addWidget(self.gauge)
        mid.addWidget(gauge, 4)
        mid.addWidget(self._score_panel(), 4)
        v.addLayout(mid, 5)

        pg.setConfigOptions(antialias=True, background=T.PANEL, foreground=T.MUTED)
        plots = pg.GraphicsLayoutWidget()
        plots.setMinimumHeight(260)
        self.p_tempo = plots.addPlot(row=0, col=0, title="Tempo (BPM)")
        self.p_score = plots.addPlot(row=1, col=0, title="Trial score")
        self.p_err = plots.addPlot(row=2, col=0, title="Timing (ms, + late)")
        for p in (self.p_score, self.p_err):
            p.setXLink(self.p_tempo)
        for p in (self.p_tempo, self.p_score, self.p_err):
            p.showGrid(x=True, y=True, alpha=0.15)
            p.setMouseEnabled(x=True, y=False)
        self.p_err.setLabel("bottom", "lesson time", units="s")
        self.p_score.setYRange(0, 100)
        self.p_err.setYRange(-60, 60)
        self.c_tempo = self.p_tempo.plot(stepMode="center", pen=pg.mkPen(T.ACCENT, width=2))
        self.c_trials = pg.ScatterPlotItem(size=13)
        self.p_tempo.addItem(self.c_trials)
        self.l_best = pg.InfiniteLine(angle=0, pen=pg.mkPen(T.GOLD, width=1, style=Qt.DashLine))
        self.p_tempo.addItem(self.l_best)
        self.c_score = self.p_score.plot(pen=pg.mkPen(T.MUTED, width=1), symbol="o", symbolSize=10)
        self.l_pass = pg.InfiniteLine(angle=0, pen=pg.mkPen(T.GOLD, width=1, style=Qt.DashLine))
        self.p_score.addItem(self.l_pass)
        self.p_err.addItem(pg.InfiniteLine(angle=0, pen=pg.mkPen(T.MUTED, width=1)))
        self.c_err = pg.ScatterPlotItem(size=6, pen=None)
        self.p_err.addItem(self.c_err)
        v.addWidget(plots, 4)

        self.setCentralWidget(root)
        sb = self.statusBar()
        sb.addWidget(QLabel("Input"))
        self.meter = QProgressBar(minimum=-60, maximum=0, textVisible=False)
        self.meter.setFixedWidth(220)
        sb.addWidget(self.meter)
        self.lat_label = QLabel()
        sb.addWidget(self.lat_label)
        self.msg = QLabel()
        sb.addWidget(self.msg, 1)
        self._update_nps()
        self._update_latency_label()

    def _score_panel(self) -> QFrame:
        f = panel()
        g = QGridLayout(f)
        g.setContentsMargins(20, 16, 20, 16)
        self.score_big = QLabel("–")
        self.score_big.setAlignment(Qt.AlignCenter)
        self.score_big.setStyleSheet("font-size: 84px; font-weight: 800;")
        self.score_title = QLabel("Last trial")
        self.score_title.setAlignment(Qt.AlignCenter)
        self.score_title.setStyleSheet(f"color: {T.MUTED}; font-size: 17px;")
        self.verdict = QLabel("")
        self.verdict.setAlignment(Qt.AlignCenter)
        self.verdict.setWordWrap(True)
        self.verdict.setStyleSheet("font-size: 20px; font-weight: 600;")
        g.addWidget(self.score_title, 0, 0, 1, 2)
        g.addWidget(self.score_big, 1, 0, 1, 2)
        g.addWidget(self.verdict, 2, 0, 1, 2)
        self.stats = {}
        for k, (key, label) in enumerate([("bias", "Median, all plucks"), ("jitter", "Spread of hits"),
                                          ("hits", "Notes hit"), ("best", "Best passed")]):
            name = QLabel(label)
            name.setStyleSheet(f"color: {T.MUTED};")
            val = QLabel("–")
            val.setAlignment(Qt.AlignRight)
            val.setStyleSheet("font-size: 19px; font-weight: 600;")
            g.addWidget(name, 3 + k, 0)
            g.addWidget(val, 3 + k, 1)
            self.stats[key] = val
        g.setRowStretch(7, 1)
        return f

    # -- helpers -------------------------------------------------------------
    def _update_nps(self):
        n = self.subdiv.currentData()
        self.nps.setText(f"= {self.bpm.value() * n / 60:.2f} notes/s")
        if self.core is None:
            self.gauge.set_speed(self.bpm.value() * n / 60, self.bpm.value())
            self.strip.set_windows(*(1000 * w for w in scoring.windows(60 / (self.bpm.value() * n))))

    def _update_latency_label(self):
        lat = self.settings.latencies.get(self.settings.device_key)
        if self.simulate:
            self.lat_label.setText("simulated input")
        elif lat:
            self.lat_label.setText(f"Latency {lat * 1000:.1f} ms")
        else:
            self.lat_label.setText(f"Latency {eng.DEFAULT_ROUND_TRIP_S * 1000:.0f} ms (default)")

    def _idle(self):
        running = self.core is not None
        for w in (self.btn_practice, self.btn_arm, self.btn_endure, self.btn_cal, self.btn_set,
                  self.bpm, self.subdiv):
            w.setEnabled(not running)
        self.btn_stop.setEnabled(running)

    def _lesson_settings(self) -> LessonSettings:
        s = self.settings
        s.start_bpm, s.subdiv = self.bpm.value(), self.subdiv.currentData()
        s.save()
        return LessonSettings(start_bpm=s.start_bpm, subdiv=s.subdiv, beats=s.beats,
                              bars_per_trial=s.bars_per_trial, step_bpm=s.step_bpm,
                              pass_score=s.pass_score, attempts=s.attempts,
                              settle_bars=s.settle_bars, ignore_offset=s.ignore_offset)

    def _make_engine(self):
        if self.simulate:
            from ..sim import SimPlayer
            return eng.SimEngine(SimPlayer(eng.SR, bias_ms=5.0, base_jitter_ms=5.0, comfort_nps=6.0))
        s = self.settings
        return eng.AudioEngine(s.input_device, s.input_channel, s.output_device)

    # -- lesson flow -----------------------------------------------------------
    def _start_lesson(self, mode: str, lesson=None):
        s = self.settings
        engine = self._make_engine()
        if mode == "calibrate":
            latency = 0.0
        elif self.simulate:
            latency = engine.open_latency()
        else:
            latency = s.latencies.get(s.device_key)
            if latency is None:
                latency = eng.DEFAULT_ROUND_TRIP_S
                self.msg.setText(f"Not calibrated for these devices: using the default "
                                 f"{latency * 1000:.0f} ms round trip.")
        if lesson is None:
            ls = self._lesson_settings()
            if mode == "endurance":
                lesson = self._endurance_lesson(ls)
            else:
                lesson = LadderLesson(ls) if mode == "train" else PracticeLesson(ls)
        det = OnsetDetector(eng.SR, sensitivity=s.sensitivity)
        core = Core(eng.SR, lesson, det, make_clicks(eng.SR), latency_s=latency,
                    click_subdivisions=s.click_subdivisions, volume=s.click_volume,
                    record=s.save_audio and not self.simulate,
                    close_margin_s=0.45 if mode == "calibrate" else 0.0)
        try:
            engine.start(core)
        except Exception as e:
            QMessageBox.critical(self, "Audio", f"Could not open the audio devices:\n\n{e}")
            return
        self.engine, self.core, self.mode, self.latency_s = engine, core, mode, latency
        self.bars = []
        self.t0 = 0.0
        self._clear_plots()
        self.gauge.reset()
        self.boxes.set_start_finger(s.start_finger)
        self.l_pass.setValue(s.pass_score)
        self.score_big.setText("–")
        self.score_big.setStyleSheet("font-size: 84px; font-weight: 800;")
        self.verdict.setText({"calibrate": "Play one note on every click",
                              "train": "Hold the tempo and it will speed up",
                              "practice": "Play along",
                              "endurance": f"Hold {lesson.s.start_bpm * lesson.s.subdiv / 60:.1f} notes/s "
                                           f"evenly for as long as you can"}[mode])
        for v in self.stats.values():
            v.setText("–")
        if mode != "calibrate" and not self.msg.text().startswith("Not calibrated"):
            self.msg.setText("")
        self._idle()

    def _stop(self):
        if self.core is not None:
            self.core.stop()
            self._finish(stopped=True)

    def _finish(self, stopped: bool = False):
        core, engine, mode = self.core, self.engine, self.mode
        if core is None:
            return
        engine.stop()
        self.core = self.engine = None
        self._idle()
        lesson = core.lesson
        if mode == "calibrate":
            # Kept like a lesson (audio, onsets, bars) so a played calibration
            # can be checked against lessons, but it is not practice time.
            if core.bars and not self.simulate:
                path = storage.save_session(core, self.settings, mode, self.latency_s, log=False)
                self.msg.setText(f"Saved {path.name}")
            return
        if lesson.results and not self.simulate:
            path = storage.save_session(core, self.settings, mode, self.latency_s)
            self.msg.setText(f"Saved {path.name}")
        if mode == "endurance":
            self._endurance_result(core, lesson)
            return
        best = lesson.best_bpm
        if mode == "train":
            if best:
                n = best * lesson.s.subdiv / 60
                self.score_title.setText("Lesson over")
                self.verdict.setText(f"Top tempo held: {best:.0f} BPM ({n:.1f} notes/s)")
            elif not stopped:
                self.verdict.setText("No trial passed – try a slower start")

    def _endurance_lesson(self, ls: LessonSettings) -> EnduranceLesson:
        """Tempo = endurance_pct of the all-time record, at the chosen notes
        per beat; Start BPM until there is a record."""
        s = self.settings
        record = None if self.simulate else self.gauge.record
        if record:
            bpm = max(20, round(record * s.endurance_pct / 100 * 60 / ls.subdiv))
            ls = dataclasses.replace(ls, start_bpm=bpm)
        else:
            self.msg.setText("No record yet: the endurance test uses Start BPM.")
        return EnduranceLesson(ls, record_nps=record, pct=s.endurance_pct if record else None)

    def _endurance_result(self, core, lesson: EnduranceLesson):
        from ..endurance import summarize
        e = summarize(core, lesson, self.settings.start_finger)
        m, sec = divmod(int(round(e["held_s"])), 60)
        self.score_title.setText(f"Endurance  ·  {e['notes_per_s']:.1f} notes/s"
                                 + (f"  ·  {e['pct_of_record']}% of record" if e["pct_of_record"] else ""))
        self.score_big.setText(f"{m}:{sec:02d}")
        self.score_big.setStyleSheet(f"font-size: 84px; font-weight: 800; color: {T.GOLD};")
        lines = [f"Held {e['held_notes']} notes" + ("" if e["ended"] == "failed" else " (stopped)")]
        f = e["fatigue"]
        if f:
            if f["spread_ms_start"] is not None:
                lines.append(f"spread {f['spread_ms_start']:.0f} → {f['spread_ms_end']:.0f} ms")
            if f["m_minus_i_ms_start"] is not None:
                lines.append(f"m − i {f['m_minus_i_ms_start']:+.0f} → {f['m_minus_i_ms_end']:+.0f} ms")
            if f["level_change_db"] is not None:
                lines.append(f"pluck level {f['level_change_db']:+.1f} dB")
        self.verdict.setText(" · ".join(lines))

    def _calibrate(self):
        box = QMessageBox(self)
        box.setWindowTitle("Calibrate latency")
        box.setText("Recommended: patch the click output into this input with a cable and "
                    "don't play. That measures the hardware alone.\n\n"
                    "Without a cable, play one note exactly on each of 16 clicks after a "
                    "4-click count-in. But any lead or lag you have against the click is then "
                    "built into the latency, and the trainer can no longer show it. Most "
                    "players land ahead of a slow click without hearing it.")
        box.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        if box.exec() == QMessageBox.Ok:
            self._start_lesson("calibrate", CalibrationLesson())

    def _calibrated(self, lesson: CalibrationLesson):
        s = self.settings
        if lesson.latency_s is None:
            self.verdict.setText(f"Calibration failed: only {lesson.matched} of 16 plucks heard. "
                                 "Check the input level and try again.")
            return
        floor = getattr(self.engine, "reported_round_trip_s", None)
        if floor is not None and lesson.latency_s < floor:
            # Shorter than the stream itself can be: the plucks came before
            # the click could have been heard, so this is the player's lead,
            # not the hardware. Keep the previous calibration.
            self.score_title.setText("Calibration not saved")
            self.score_big.setText(f"{lesson.latency_s * 1000:.0f} ms")
            self.score_big.setStyleSheet("font-size: 64px; font-weight: 800;")
            self.verdict.setText(
                f"Below this stream's minimum of {floor * 1000:.0f} ms, so it includes you playing "
                f"ahead of the click. Use the loopback cable (click output into this input, "
                f"don't play). The previous calibration is kept.")
            return
        if not self.simulate:  # a simulated round trip must not replace the real one
            s.latencies[s.device_key] = lesson.latency_s
            s.save()
            self._update_latency_label()
        self.score_title.setText("Calibrated")
        self.score_big.setText(f"{lesson.latency_s * 1000:.0f} ms")
        self.score_big.setStyleSheet("font-size: 64px; font-weight: 800;")
        self.verdict.setText(f"{lesson.matched}/16 plucks, spread {lesson.spread_ms:.1f} ms")

    def _open_settings(self):
        d = SettingsDialog(self.settings, self)
        if d.exec() == QDialog.Accepted:
            d.apply()
            self.settings.save()
            self.boxes.set_start_finger(self.settings.start_finger)
            self._update_latency_label()

    # -- live updates ------------------------------------------------------------
    def _clear_plots(self):
        self._tempo_x, self._tempo_y = [], []
        self._trial_pts = []
        self._score_x, self._score_y = [], []
        self._err_pts = []
        self.c_tempo.setData([], [])
        self.c_trials.setData([])
        self.c_score.setData([], [])
        self.c_err.setData([])
        self.l_best.setValue(-1000)
        self.strip.clear()

    def _x(self, frame: float) -> float:
        return (frame - self.t0) / eng.SR

    def _tick(self):
        core = self.core
        if core is None:
            self.gauge.tick()
            return
        while not core.events.empty():
            self._event(core.events.get())
            if self.core is None:
                return
        self.meter.setValue(int(max(-60, min(0, core.level_db))))
        hf = self.engine.heard_frame()
        bar = None
        for b in reversed(self.bars[-3:]):
            if b.start <= hf < b.end:
                bar = b
                break
        if bar is not None:
            slot = int((hf - bar.start) // bar.interval)
            phase = (hf - bar.start) / bar.interval - slot
            side = (bar.first_note + slot) % 2
            caption = CAPTIONS.get(bar.kind, f"TRIAL {bar.trial}" if bar.trial else "")
            self.boxes.set_state(side, phase, slot // bar.subdiv, bar.beats, caption)
            self.gauge.set_speed(bar.notes_per_sec, bar.bpm)
            self.strip.set_windows(*(1000 * w for w in scoring.windows(bar.interval / eng.SR)))
        else:
            self.boxes.set_state(-1, 0, -1, self.settings.beats, "")
        self.gauge.tick()

    def _event(self, ev):
        kind = ev[0]
        if kind == "bar":
            b = ev[1]
            if not self.bars:
                self.t0 = b.start
            self.bars.append(b)
            self._tempo_x = [self._x(x.start) for x in self.bars] + [self._x(b.end)]
            self._tempo_y = [x.bpm for x in self.bars]
            self.c_tempo.setData(self._tempo_x, self._tempo_y)
            lo = min(self._tempo_y)
            hi = max(self._tempo_y)
            self.p_tempo.setYRange(lo - 10, hi + 10)
        elif kind == "pluck":
            _, g, note, err, grade = ev
            if note is None:
                return
            self.boxes.flash(note % 2, QColor(T.GRADE[grade]))
            self.strip.add(err, grade)
            self._err_pts.append({"pos": (self._x(g - err * eng.SR / 1000), max(-60, min(60, err))),
                                  "brush": pg.mkBrush(T.GRADE[grade])})
            self.c_err.setData(self._err_pts)
        elif kind == "trial":
            self._trial(ev[1])
        elif kind == "calibration":
            self._calibrated(ev[1])
        elif kind == "finished":
            self._finish()
        elif kind == "error":
            self.msg.setText(f"Audio error: {ev[1]}")

    def _trial(self, r: scoring.TrialResult):
        mid = self._x((r.start_s + r.end_s) / 2 * eng.SR)
        col = T.TIGHT if r.passed else T.OFF
        self._trial_pts.append({"pos": (mid, r.bpm), "brush": pg.mkBrush(col), "pen": None})
        self.c_trials.setData(self._trial_pts)
        self._score_x.append(mid)
        self._score_y.append(r.score)
        self.c_score.setData(self._score_x, self._score_y)
        self.score_title.setText(f"Trial {r.trial}  ·  {r.bpm:.0f} BPM  ·  {r.notes_per_sec:.1f} notes/s")
        self.score_big.setText(f"{r.score:.0f}")
        self.score_big.setStyleSheet(f"font-size: 84px; font-weight: 800; color: {col};")
        tag = "PASS" if r.passed else "again" if self.mode == "train" else ""
        self.verdict.setText((f"{tag} – " if tag else "") + ", ".join(r.verdict))
        off = r.offset_ms if r.offset_ms is not None else r.bias_ms
        self.stats["bias"].setText(f"{abs(off):.0f} ms {'late' if off > 0 else 'early'}")
        self.stats["jitter"].setText(f"± {r.jitter_ms:.0f} ms")
        self.stats["hits"].setText(f"{r.hits}/{r.n_slots}" + (f"  (+{r.extras} extra)" if r.extras else ""))
        best = self.core.lesson.best_bpm if self.core else None
        if best:
            n = best * r.subdiv / 60
            self.stats["best"].setText(f"{best:.0f} BPM · {n:.1f}/s")
            self.gauge.set_best(n)
            if not self.simulate and r.passed and n > (self.gauge.record or 0):
                self.gauge.set_record(n)  # a new all-time record, live
            self.l_best.setValue(best)
