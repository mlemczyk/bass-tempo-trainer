from bass_tempo_trainer.endurance import summarize
from bass_tempo_trainer.lessons import EnduranceLesson, LessonSettings
from bass_tempo_trainer.sim import SimPlayer

from test_core import SR, make, run


def _endure(**tire):
    s = LessonSettings(start_bpm=150, subdiv=2, bars_per_trial=2, pass_score=90)
    lesson = EnduranceLesson(s, record_nps=5.9, pct=85)
    core = make(lesson, 0.054, record=True)
    player = SimPlayer(SR, round_trip_s=0.054, bias_ms=-40.0, base_jitter_ms=4.0, comfort_nps=9.0,
                       **tire)
    run(core, player, 150, until_finished=True)
    return core, lesson


def test_a_tiring_player_is_stopped_and_scored():
    core, lesson = _endure(tire_jitter_ms_per_min=20.0, tire_level_db_per_min=6.0)
    assert lesson.finished and lesson.ended_on_fails
    assert lesson.s.ignore_offset                   # endurance is scored on evenness
    assert not any(r.passed for r in lesson.results[-2:])
    e = summarize(core, lesson, "i")
    assert e["ended"] == "failed" and e["trials_held"] == len(lesson.results) - 2
    assert 20 < e["held_s"] < 140 and e["held_notes"] == 16 * e["trials_held"]
    f = e["fatigue"]
    assert f["spread_ms_end"] > f["spread_ms_start"] + 3
    assert f["spread_growth_ms_per_min"] > 5
    assert f["level_change_db"] < -2


def test_a_finger_drifting_late_shows_in_the_gap():
    core, lesson = _endure(tire_m_late_ms_per_min=30.0)
    f = summarize(core, lesson, "i")["fatigue"]
    assert f["m_minus_i_ms_end"] > f["m_minus_i_ms_start"] + 10      # m drifting late
    # Downbeat on m instead: the same notes, so the sign flips.
    g = summarize(core, lesson, "m")["fatigue"]
    assert g["m_minus_i_ms_end"] == -f["m_minus_i_ms_end"]


def test_a_fresh_player_holds_until_stopped():
    core, lesson = _endure()
    assert not lesson.finished                     # never failed twice in 150 s
    e = summarize(core, lesson, "i")
    assert e["ended"] == "stopped" and e["held_s"] > 120
    assert abs(e["fatigue"]["level_change_db"]) < 1.5
