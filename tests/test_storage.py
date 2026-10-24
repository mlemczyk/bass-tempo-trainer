import json

from bass_tempo_trainer import storage
from bass_tempo_trainer.lessons import LadderLesson, LessonSettings
from bass_tempo_trainer.sim import SimPlayer

from test_core import SR, make, run


def _log(tmp_path):
    lines = (tmp_path / "BassTempoTrainer" / "practice_log.jsonl").read_text(encoding="utf-8")
    return [json.loads(l) for l in lines.splitlines()]


def test_saved_lesson_appends_a_log_line(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    s = LessonSettings(start_bpm=100, subdiv=2, bars_per_trial=2, step_bpm=20,
                       pass_score=75, attempts=2)
    core = make(LadderLesson(s), 0.054)
    player = SimPlayer(SR, round_trip_s=0.054, bias_ms=0.0, base_jitter_ms=3.0,
                       comfort_nps=7.0, seed=3)
    run(core, player, 120, until_finished=True)
    path = storage.save_session(core, storage.Settings(subdiv=2), "train", 0.054)

    [entry] = _log(tmp_path)
    assert entry["session"] == path.name and entry["mode"] == "train"
    best = core.lesson.best_bpm
    assert entry["best_bpm"] == best
    assert abs(entry["best_notes_per_s"] - best * 2 / 60) < 1e-3
    # Played time runs from the first downbeat to the end of play, and the
    # lesson started that long (plus the start delay) before it was saved.
    assert 0 < entry["played_s"] <= core.frame / SR
    assert entry["start"] < entry["end"]
    assert entry["trials"] == len(core.lesson.results)

    # Backfill finds nothing new; a session file without a log line gets one,
    # with played_s estimated from its bars.
    assert storage.backfill_practice_log() == 0
    doc = json.loads(path.read_text(encoding="utf-8"))
    del doc["played_s"], doc["lesson_s"]
    old = path.with_name("20260101_120000_practice.json")
    old.write_text(json.dumps(doc), encoding="utf-8")
    assert storage.backfill_practice_log() == 1
    assert storage.backfill_practice_log() == 0
    entries = _log(tmp_path)
    assert entries[-1]["session"] == old.name
    assert abs(entries[-1]["played_s"] - entry["played_s"]) < 3.0


def test_calibration_is_saved_but_not_logged(tmp_path, monkeypatch):
    from bass_tempo_trainer.lessons import CalibrationLesson
    monkeypatch.setenv("APPDATA", str(tmp_path))
    lesson = CalibrationLesson()
    core = make(lesson, 0.0, close_margin_s=0.45)
    player = SimPlayer(SR, round_trip_s=0.071, bias_ms=0.0, base_jitter_ms=3.0)
    run(core, player, 20, until_finished=True)
    path = storage.save_session(core, storage.Settings(), "calibrate", 0.0, log=False)

    doc = json.loads(path.read_text(encoding="utf-8"))
    assert path.name.endswith("_calibrate.json")
    assert abs(doc["calibration"]["measured_latency_s"] - 0.071) < 0.004
    assert doc["calibration"]["matched"] >= 14 and len(doc["onsets_s"]) >= 14
    # Neither the save nor a later backfill puts it in the practice log.
    assert not (tmp_path / "BassTempoTrainer" / "practice_log.jsonl").exists()
    assert storage.backfill_practice_log() == 0
