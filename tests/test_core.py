import numpy as np

from bass_tempo_trainer import scoring
from bass_tempo_trainer.clicks import make_clicks
from bass_tempo_trainer.core import Core
from bass_tempo_trainer.lessons import (CalibrationLesson, LadderLesson, LessonSettings,
                                        PracticeLesson)
from bass_tempo_trainer.onset import OnsetDetector
from bass_tempo_trainer.sim import SimPlayer

SR = 48000
BLOCK = 480


def run(core, player, seconds, until_finished=False):
    out = np.zeros((BLOCK, 2), np.float32)
    clicks = 0.0
    for k in range(int(seconds * SR / BLOCK)):
        f0 = k * BLOCK
        x = player.render(core, f0, BLOCK)
        out[:] = 0
        core.process(x, out)
        clicks += float(np.abs(out).sum())
        if until_finished and core.finished:
            break
    events = []
    while not core.events.empty():
        events.append(core.events.get())
    return events, clicks


def make(lesson, latency_s, **kw):
    return Core(SR, lesson, OnsetDetector(SR), make_clicks(SR), latency_s=latency_s, **kw)


def test_calibration_recovers_the_round_trip():
    lesson = CalibrationLesson()
    core = make(lesson, 0.0, close_margin_s=0.45)
    player = SimPlayer(SR, round_trip_s=0.071, bias_ms=0.0, base_jitter_ms=3.0)
    events, clicks = run(core, player, 20, until_finished=True)
    assert clicks > 0
    assert any(e[0] == "calibration" for e in events)
    assert lesson.latency_s is not None and lesson.matched >= 14
    assert abs(lesson.latency_s - 0.071) < 0.004


def test_practice_scores_a_steady_player_high_and_reports_its_bias():
    s = LessonSettings(start_bpm=90, subdiv=2, bars_per_trial=2)
    core = make(PracticeLesson(s), 0.054)
    player = SimPlayer(SR, round_trip_s=0.054, bias_ms=12.0, base_jitter_ms=2.0)
    events, _ = run(core, player, 28)
    trials = [e[1] for e in events if e[0] == "trial"]
    assert len(trials) >= 4
    for r in trials:
        assert r.misses == 0 and r.extras == 0
        assert 9 < r.bias_ms < 15                  # 12 ms late, give or take
        assert any("dragging" in v for v in r.verdict)
        assert r.score > 70


def test_ladder_climbs_then_stops_where_the_player_falls_apart():
    s = LessonSettings(start_bpm=100, subdiv=2, bars_per_trial=2, step_bpm=20,
                       pass_score=75, attempts=2)
    lesson = LadderLesson(s)
    core = make(lesson, 0.054)
    player = SimPlayer(SR, round_trip_s=0.054, bias_ms=0.0, base_jitter_ms=3.0,
                       comfort_nps=7.0, seed=3)
    events, _ = run(core, player, 120, until_finished=True)
    trials = [e[1] for e in events if e[0] == "trial"]
    assert core.finished
    assert trials[0].passed and trials[0].bpm == 100
    assert lesson.best_bpm is not None and lesson.best_bpm >= 160
    # It never tried a tempo past the first one that failed twice in a row.
    bpms = [r.bpm for r in trials]
    assert bpms == sorted(bpms)
    assert not trials[-1].passed and not trials[-2].passed
    # Tempo only changes between trials, never inside one.
    for r in trials:
        bars = [b for b in core.bars if b.trial == r.trial]
        assert {b.bpm for b in bars} == {r.bpm}


def test_match_and_extras():
    slots = np.array([0.0, 100.0, 200.0, 300.0])
    onsets = np.array([3.0, 97.0, 102.0, 290.0])
    got, extras = scoring.match(slots, onsets, 50.0)
    assert got == [0, 2, None, 3] and extras == 1


def test_offset_sees_a_lead_past_the_hit_window():
    # 100 BPM quarters: the hit window is +-60 ms, so a player 75 ms early
    # lands outside it. bias_ms (hits only) can't see that; offset_ms must.
    s = LessonSettings(start_bpm=100, subdiv=1, bars_per_trial=2)
    core = make(PracticeLesson(s), 0.054)
    player = SimPlayer(SR, round_trip_s=0.054, bias_ms=-75.0, base_jitter_ms=3.0)
    events, _ = run(core, player, 14)
    trials = [e[1] for e in events if e[0] == "trial"]
    assert trials
    for r in trials:
        assert r.hits <= 2
        assert -85 < r.offset_ms < -65
        assert any(v.startswith("rushing 7") for v in r.verdict)


def _practice(bias_ms, jitter_ms, ignore, seconds=14, pass_score=80):
    s = LessonSettings(start_bpm=100, subdiv=1, bars_per_trial=2, ignore_offset=ignore,
                       pass_score=pass_score)
    core = make(PracticeLesson(s), 0.054)
    player = SimPlayer(SR, round_trip_s=0.054, bias_ms=bias_ms, base_jitter_ms=jitter_ms)
    events, _ = run(core, player, seconds)
    return [e[1] for e in events if e[0] == "trial"]


def test_evenness_only_passes_a_steady_lead():
    # 75 ms ahead, otherwise tight: fails against the click, passes on evenness.
    assert not any(r.passed for r in _practice(-75.0, 3.0, ignore=False))
    trials = _practice(-75.0, 3.0, ignore=True)
    # (The simulated player drops its very first note, so trial 1 is one short.)
    assert trials and all(r.passed and r.hits >= r.n_slots - 1 for r in trials)
    for r in trials:
        assert -85 < r.offset_ms < -65              # still reported against the click
        assert not any(v.startswith(("rushing", "dragging")) for v in r.verdict)


def test_evenness_only_still_fails_uneven_playing():
    # Same timing windows as normal scoring, so the pass mark sets how even
    # is even enough: 30 ms of scatter is 'unsteady' and fails a 95 mark.
    trials = _practice(-75.0, 30.0, ignore=True, pass_score=95)
    assert trials and not any(r.passed for r in trials)
    assert all("unsteady" in r.verdict for r in trials)


def test_evenness_only_still_counts_extra_and_missing_notes():
    sr, bpm = SR, 120.0
    iv = sr * 60 / bpm
    slots = np.arange(8) * iv + sr
    steady = slots - 0.075 * sr                     # every note, 75 ms early
    def score(onsets):
        return scoring.evaluate(1, slots, onsets, sr, bpm=bpm, subdiv=1, pass_score=95,
                                ignore_offset=True)
    r = score(steady)
    assert r.passed and r.score == 100 and abs(r.offset_ms + 75) < 1
    extra = score(np.sort(np.append(steady, steady[3] + iv / 2)))   # a note in between
    assert extra.extras == 1 and not extra.passed
    missing = score(np.delete(steady, 5))
    assert missing.misses == 1 and not missing.passed
