"""Settings and lesson history, under %APPDATA%\\BassTempoTrainer."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path

import numpy as np


def data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    d = Path(base) / "BassTempoTrainer"
    (d / "sessions").mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class Settings:
    input_device: str = "ZEDi-10 M1"
    input_channel: int = 0
    output_device: str = "ZEDi-10 ST2"
    # Round trip per (input, output) pair, measured by Calibrate.
    latencies: dict[str, float] = field(default_factory=dict)
    start_bpm: int = 60
    subdiv: int = 2
    beats: int = 4
    bars_per_trial: int = 4
    step_bpm: int = 4
    pass_score: int = 80
    attempts: int = 3
    settle_bars: int = 1
    click_volume: float = 0.8
    click_subdivisions: bool = False
    start_finger: str = "i"
    sensitivity: float = 0.5
    save_audio: bool = True
    ignore_offset: bool = False
    endurance_pct: int = 85   # endurance tempo, % of the all-time record

    @property
    def device_key(self) -> str:
        return f"{self.input_device} | {self.output_device}"

    @classmethod
    def load(cls) -> "Settings":
        p = data_dir() / "settings.json"
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in names})

    def save(self) -> None:
        (data_dir() / "settings.json").write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


def save_session(core, settings: Settings, mode: str, latency_s: float, log: bool = True) -> Path:
    """One JSON per lesson (plus the input audio, if enabled). Returns the JSON
    path. `log=False` (calibration runs) keeps it out of practice_log.jsonl."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = data_dir() / "sessions" / f"{stamp}_{mode}"
    sr = core.sr
    lesson = core.lesson
    # Time actually played: first bar's downbeat to the last bar's end, or to
    # the stop if that came first (the core schedules bars a little ahead).
    played_s = None
    if core.bars:
        played_s = round((min(core.frame, core.bars[-1].end) - core.bars[0].start) / sr, 2)
    doc = {
        "app": "bass-tempo-trainer",
        "saved": datetime.now().isoformat(timespec="seconds"),
        "mode": mode,
        "sample_rate": sr,
        "latency_s": latency_s,
        "settings": asdict(settings),
        "played_s": played_s,
        "lesson_s": round(core.frame / sr, 2),
        "best_bpm": lesson.best_bpm,
        "trials": [r.to_dict() for r in lesson.results],
        "bars": [{"start_s": round(b.start / sr, 4), "bpm": b.bpm, "subdiv": b.subdiv,
                  "kind": b.kind, "trial": b.trial} for b in core.bars],
        "onsets_s": [round(o / sr, 4) for o in core.onsets],
    }
    if mode == "calibrate":
        # latency_s above is what the run subtracted (0); this is what it measured.
        doc["calibration"] = {"measured_latency_s": getattr(lesson, "latency_s", None),
                              "matched": getattr(lesson, "matched", None),
                              "spread_ms": getattr(lesson, "spread_ms", None)}
    if mode == "endurance":
        from .endurance import summarize
        doc["endurance"] = summarize(core, lesson, settings.start_finger)
    if core.recording:
        from scipy.io import wavfile
        wav = base.with_suffix(".wav")
        wavfile.write(wav, sr, np.concatenate(core.recording))
        doc["audio"] = wav.name
        doc["audio_note"] = ("Input as recorded. onsets_s and bars are on the click clock: "
                             "an onset in this file sits latency_s later.")
    path = base.with_suffix(".json")
    path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    if log:
        append_practice_log(practice_entry(doc, path.name))
    return path


# -- practice log ----------------------------------------------------------
# practice_log.jsonl: one line per saved lesson, append-only. A small, stable
# summary for other programs to read (a practice diary, a dashboard) without
# parsing the session files. Format is documented in README.md; bump
# LOG_VERSION on any incompatible change.

LOG_VERSION = 1


def practice_log_path() -> Path:
    return data_dir() / "practice_log.jsonl"


def practice_entry(doc: dict, session: str) -> dict:
    """The log line for one session document (a fresh one or an old file)."""
    subdiv = doc.get("settings", {}).get("subdiv") or 1
    bars = doc.get("bars") or []
    played = doc.get("played_s")
    if played is None and bars:
        # Sessions saved before played_s existed: assume the last bar ran out.
        last = bars[-1]
        beats = doc.get("settings", {}).get("beats") or 4
        played = round(last["start_s"] + beats * 60.0 / last["bpm"] - bars[0]["start_s"], 2)
    end = datetime.fromisoformat(doc["saved"]).astimezone()
    span = doc.get("lesson_s") or played or 0.0
    start = end.timestamp() - span
    best = doc.get("best_bpm")
    trials = doc.get("trials") or []
    return {
        "v": LOG_VERSION,
        "session": session,
        "start": datetime.fromtimestamp(start).astimezone().isoformat(timespec="seconds"),
        "end": end.isoformat(timespec="seconds"),
        "played_s": played or 0.0,
        "mode": doc.get("mode"),
        "subdiv": subdiv,
        "best_bpm": best,
        "best_notes_per_s": round(best * subdiv / 60.0, 3) if best else None,
        "trials": len(trials),
        "passed": sum(1 for t in trials if t.get("passed")),
        "evenness_only": bool(doc.get("settings", {}).get("ignore_offset", False))
        or doc.get("mode") == "endurance",
        # Endurance lessons only: the stamina score and how the player tired.
        **({"endurance": doc["endurance"]} if doc.get("endurance") else {}),
    }


def append_practice_log(entry: dict) -> None:
    with open(practice_log_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush()
        os.fsync(f.fileno())


def best_record() -> float | None:
    """The fastest passed trial ever, in notes/s, from the practice log."""
    best = None
    try:
        lines = practice_log_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        try:
            v = json.loads(line).get("best_notes_per_s")
        except (ValueError, AttributeError):
            continue
        if isinstance(v, (int, float)) and (best is None or v > best):
            best = float(v)
    return best


def backfill_practice_log() -> int:
    """Add a line for every session file the log doesn't name yet (all of
    them, the first time). Returns how many were added."""
    have = set()
    try:
        for line in practice_log_path().read_text(encoding="utf-8").splitlines():
            try:
                have.add(json.loads(line)["session"])
            except (ValueError, KeyError, TypeError):
                pass
    except OSError:
        pass
    added = 0
    for p in sorted((data_dir() / "sessions").glob("*.json")):
        if p.name in have:
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            if doc.get("mode") == "calibrate":
                continue
            entry = practice_entry(doc, p.name)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        append_practice_log(entry)
        added += 1
    return added
