"""End-to-end ISOS-L-1 run with a decaying cell → ISOS_Metrics sheet.

Drives the shared ``ContinuousISOSRunner`` against a SCPI-shaped fake whose
DummyCell decays exponentially in virtual time. Verifies that the ISOS_Metrics
sheet gets one row per scan and that the final persisted T80 matches an
independent crossing computation within 5%.
"""

from __future__ import annotations

import threading
from unittest.mock import patch

import numpy as np

from app.measurement.isos_continuous_runner import ContinuousISOSRunner
from app.io.isos_tracking import read_isos_log, read_isos_metrics
from app.simulation.dummy_cell import DummyCell

from tests._isos_runner_helpers import (
    FakeKeithley,
    VirtualClock,
    make_runner_config,
)


class DecayingFake(FakeKeithley):
    """FakeKeithley whose cell ages with the runner's virtual clock.

    Before each voltage-source read the cell's elapsed time is set to the
    virtual clock, so the exponential_decay mode produces a reproducible,
    time-dependent J-V curve without depending on wall-clock time.
    """

    def __init__(self, clock, tau_h, area=0.07):
        cell = DummyCell(mode="exponential_decay", tau_h=tau_h, area=area)
        super().__init__(cell=cell, area=area)
        self._clock = clock

    def query(self, cmd):
        if cmd == ":READ?" and self.func != "CURR":
            self.cell.set_time_offset(self._clock.time())
        return super().query(cmd)


def _crossing(t, y, thr):
    """Independent first-crossing-below interpolation (cross-check helper)."""
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    norm = y / y[0]
    for i in range(1, len(norm)):
        if norm[i] <= thr:
            frac = (thr - norm[i - 1]) / (norm[i] - norm[i - 1])
            return t[i - 1] + frac * (t[i] - t[i - 1])
    return None


def _run(tmp_path):
    clock = VirtualClock()
    cfg = make_runner_config(
        tmp_path,
        clock=clock,
        total_duration_h=0.08,      # 288 s
        scan_interval_min=0.3,      # 18 s → ~16 scans
        stress_log_interval_s=6.0,
    )
    fake = DecayingFake(clock, tau_h=0.02, area=cfg["cell_area"])  # τ = 72 s
    stop = threading.Event()
    runner = ContinuousISOSRunner(fake, cfg, {}, stop)
    with patch("app.instrument.keithley.time.sleep"):
        runner.run()
    return runner


def test_metrics_sheet_has_one_row_per_scan(tmp_path):
    runner = _run(tmp_path)
    log_rows     = read_isos_log(runner._tracking_path)
    metrics_rows = read_isos_metrics(runner._tracking_path)
    assert len(log_rows) >= 3
    assert len(metrics_rows) == len(log_rows)


def test_final_t80_matches_independent_crossing(tmp_path):
    runner = _run(tmp_path)
    metrics_rows = read_isos_metrics(runner._tracking_path)

    # The runner's cumulative canonical series drives both the metric and the
    # independent cross-check.
    expected = _crossing(runner._metric_times, runner._metric_pce, 0.80)
    assert expected is not None, "decay should cross T80 within the run"

    final = metrics_rows[-1]
    assert final["T80_reached"]
    assert abs(float(final["T80_h"]) - expected) / expected < 0.05
