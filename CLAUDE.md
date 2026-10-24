# Bass Tempo Trainer — notes for agents

A standalone, public (Unlicense) Windows app: metronome + live pluck
detection + timing scores + a tempo ladder, for two-finger bass speed
practice. README.md describes it for users; this file holds what a
developer needs that the code does not say. Machine-specific paths on the
owner's PC are in `CLAUDE.local.md` (gitignored, not in the public repo).

## Keep it isolated

It was started from the owner's private bass-rig / tone-matching project
but **shares nothing with it**:
no imports from there, no reads of its data, no tone, preset or cover
material here. The two only share hardware: the Zedi-10 mixer, bass into
M1, click out of ST2.

## Run, test

- `.venv\` is this repo's own venv (from a Python 3.14 install). Plain `python`
  on PATH is a different interpreter without numpy.
- `.venv\Scripts\python -m pytest` runs everything; no audio hardware is
  needed.
- `--simulate` runs the full UI with a synthetic player. It must never
  write a calibration or a lesson to `%APPDATA%\BassTempoTrainer` (it
  doesn't; keep it that way).
- **Agents running inside the Claude desktop app see a sandboxed
  `%APPDATA%`** (`...\Packages\Claude_*\LocalCache\Roaming`), not the
  owner's. The real settings and lessons are in the owner's real
  `%APPDATA%\BassTempoTrainer` (full path in `CLAUDE.local.md`); read that
  path explicitly, or ask.
- Launch the GUI detached from the editor's process tree (on the owner's
  machine: WMI `Win32_Process.Create`), so it survives the editor closing.

## Design facts worth keeping

- **One duplex stream = one clock.** Click scheduling and pluck times are
  both in the stream's frame counter; calibration measures the single
  round-trip constant. Don't split input and output into two streams
  without replacing that.
- **The detector was designed against real raw M1 takes**, not just
  synthetic ones. A raw pickup is a pulse train, so level envelopes ripple
  per period (hence spectral flux on 2048-sample frames), and two-finger
  playing has a finger-landing transient 15–50 ms before each pluck
  (hence the level-rise pair test). Synthetic plucks in `sim.py` model
  both. Check any detector change on real audio. Lessons save their input
  WAV by default for exactly this.
- `report_lead_ms = 12.5` puts a synthetic onset at its true start;
  calibration absorbs whatever constant is left on real plucks.
- **Calibrate by loopback cable, not by playing.** A played calibration
  bakes the player's own lead into the latency. Measured 2026-10-23 on the
  owner's rig: loopback 94.9 ms, sample-exact in lessons too; the owner
  leads the heard click by ~100 ms at 60 BPM and ~50 ms at 80 BPM, but only
  anywhere from ~10 to ~100 ms over a 16-note played calibration (a played
  one later stored −4 ms, which is why `_calibrated` now rejects anything
  under the stream's WASAPI-reported round trip). So a "rushing" report at slow
  tempos is usually real, not a latency bug: run a loopback lesson (input
  looped, nobody playing, should score 100 at 0 ms) before suspecting the
  stream.
- `Core.process` runs in the audio callback: keep it bounded numpy work,
  talk to the UI only through `core.events`. Measured: 0.22 ms mean /
  1.1 ms max per 10 ms block.
