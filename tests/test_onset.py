import numpy as np
import pytest

from bass_tempo_trainer.onset import OnsetDetector
from bass_tempo_trainer.sim import render_plucks

SR = 48000


def detect(x, block=480, **kw):
    det = OnsetDetector(SR, **kw)
    out = []
    for f0 in range(0, len(x), block):
        out += det.process(x[f0:f0 + block], f0)
    return np.array(out) / SR


@pytest.mark.parametrize("nps,f0", [(2, 41.2), (6, 55.0), (12, 55.0), (14, 98.0)])
def test_finds_every_pluck_with_a_consistent_offset(nps, f0):
    rng = np.random.default_rng(nps)
    times = 0.3 + np.arange(int(4 * nps)) / nps + rng.normal(0, 0.006, int(4 * nps))
    x = render_plucks(SR, times, int((times[-1] + 0.8) * SR), f0=f0, seed=nps)
    found = detect(x, interval_ms=1000 / nps)
    assert len(found) == len(times), (len(found), len(times))
    d = found - times
    assert abs(np.median(d)) < 0.010       # a constant offset; calibration removes it
    assert np.std(d) < 0.0015              # the spread is what scoring sees


def test_silence_and_noise_give_nothing():
    rng = np.random.default_rng(0)
    x = rng.normal(0, 10 ** (-70 / 20), SR * 3).astype(np.float32)
    assert len(detect(x)) == 0


def test_block_size_does_not_change_the_result():
    times = 0.2 + np.arange(20) / 8
    x = render_plucks(SR, times, int(3 * SR))
    a, b = detect(x, block=480), detect(x, block=137)
    assert len(a) == len(b) and np.allclose(a, b, atol=1e-6)


@pytest.mark.parametrize("gap_ms", [80, 100, 150])
@pytest.mark.parametrize("f0", [30.87, 41.2, 55.0, 98.0])
def test_a_real_double_pluck_still_counts(gap_ms, f0):
    # Stage 3 drops onsets with no attack of their own near a note (low-string
    # ripple); a second real pluck has one and must survive, so it scores as
    # an extra note.
    times = 0.3 + np.arange(24) / 3.0
    plucks = np.sort(np.concatenate([times, times[::4] + gap_ms / 1000]))
    x = render_plucks(SR, plucks, int((plucks[-1] + 0.8) * SR), f0=f0, seed=1)
    assert len(detect(x, interval_ms=333)) == len(plucks)
