"""Audio I/O: one full-duplex WASAPI stream, so clicks out and plucks in
share one sample clock. Devices are found by name, because Windows device
indices change across reboots and mixer power cycles."""
from __future__ import annotations

import threading
import time

import numpy as np

from .core import Core

SR = 48000
BLOCK = 480  # 10 ms
HOST_API = "Windows WASAPI"
# Round trip used until a device pair is calibrated: ZEDi-10 ST2 -> M2 measured
# by loopback cable on 2026-10-22 (94.9 ms). WASAPI's own figure for the same
# stream (54 ms) is well short of it.
DEFAULT_ROUND_TRIP_S = 0.095


def list_devices(inputs: bool) -> list[tuple[str, int]]:
    """(name, channel count) of every WASAPI input or output device."""
    import sounddevice as sd
    out = []
    for d in sd.query_devices():
        if sd.query_hostapis(d["hostapi"])["name"] != HOST_API:
            continue
        ch = d["max_input_channels"] if inputs else d["max_output_channels"]
        if ch > 0:
            out.append((d["name"], ch))
    return out


def find_device(name: str, inputs: bool) -> tuple[int, int]:
    """(index, channels) of the WASAPI device whose name contains `name`."""
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if sd.query_hostapis(d["hostapi"])["name"] != HOST_API:
            continue
        ch = d["max_input_channels"] if inputs else d["max_output_channels"]
        if ch > 0 and name.lower() in d["name"].lower():
            return i, ch
    kind = "input" if inputs else "output"
    raise RuntimeError(f"No WASAPI {kind} device matching '{name}'. Is the mixer on and connected?")


class AudioEngine:
    def __init__(self, in_name: str, in_channel: int, out_name: str):
        self.in_name, self.in_channel, self.out_name = in_name, in_channel, out_name
        self.stream = None
        self.core: Core | None = None
        self.out_latency_s = 0.0
        self.reported_round_trip_s: float | None = None
        self.xruns = 0
        self._cb_frame = 0
        self._cb_time = 0.0
        self._error: str | None = None

    def open_latency(self) -> float:
        """Nominal round trip (input + output) of the stream as WASAPI reports
        it; the starting guess until the user calibrates."""
        import sounddevice as sd
        i, ich = find_device(self.in_name, True)
        o, _ = find_device(self.out_name, False)
        with sd.Stream(device=(i, o), samplerate=SR, blocksize=BLOCK, channels=(ich, 2),
                       dtype="float32", latency="low", callback=lambda *a: a[1].fill(0)) as s:
            return float(s.latency[0] + s.latency[1])

    def start(self, core: Core) -> None:
        import sounddevice as sd
        i, ich = find_device(self.in_name, True)
        o, _ = find_device(self.out_name, False)
        ch = min(self.in_channel, ich - 1)
        self.core = core

        def cb(indata, outdata, frames, t, status):
            if status:
                self.xruns += 1
            outdata.fill(0)
            f0 = core.frame
            try:
                core.process(indata[:, ch], outdata)
            except Exception as e:  # never let the stream die silently
                if self._error is None:
                    self._error = repr(e)
                    core.events.put(("error", self._error))
            self._cb_frame, self._cb_time = f0, time.perf_counter()

        self.stream = sd.Stream(device=(i, o), samplerate=SR, blocksize=BLOCK,
                                channels=(ich, 2), dtype="float32", latency="low", callback=cb)
        self.out_latency_s = float(self.stream.latency[1])
        # WASAPI's own figure for this stream. The real round trip is never
        # shorter (on the owner's ZEDi: 54 ms reported, 94.9 ms by cable).
        self.reported_round_trip_s = float(self.stream.latency[0] + self.stream.latency[1])
        self.stream.start()

    def heard_frame(self) -> float:
        """The click-clock frame reaching the player's ears right now."""
        return self._cb_frame + (time.perf_counter() - self._cb_time - self.out_latency_s) * SR

    def stop(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None


class SimEngine:
    """Drives a Core in real time from a simulated player instead of the
    mixer, with no audio out. For trying the app without a bass."""

    def __init__(self, player):
        self.player = player
        self.out_latency_s = 0.02
        self.xruns = 0
        self._cb_frame = 0
        self._cb_time = time.perf_counter()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def open_latency(self) -> float:
        return self.player.rt / SR

    def start(self, core: Core) -> None:
        self._stop.clear()

        def run():
            t0 = time.perf_counter()
            out = np.zeros((BLOCK, 2), np.float32)
            k = 0
            while not self._stop.is_set():
                f0 = k * BLOCK
                x = self.player.render(core, f0, BLOCK)
                out[:] = 0
                core.process(x, out)
                self._cb_frame, self._cb_time = f0, time.perf_counter()
                k += 1
                delay = t0 + k * BLOCK / SR - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def heard_frame(self) -> float:
        return self._cb_frame + (time.perf_counter() - self._cb_time - self.out_latency_s) * SR

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
