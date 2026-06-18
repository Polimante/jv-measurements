"""Baseline-conditioning tests for ContinuousISOSRunner."""

from __future__ import annotations

import threading
from unittest.mock import patch

import openpyxl

from app.measurement.isos_continuous_runner import ContinuousISOSRunner
from app.io.isos_tracking import create_isos_tracking_file

from tests._isos_runner_helpers import FakeKeithley, VirtualClock, make_runner_config


def _runner_with_file(tmp_path):
    cfg = make_runner_config(tmp_path, clock=VirtualClock())
    stop = threading.Event()
    runner = ContinuousISOSRunner(FakeKeithley(area=cfg["cell_area"]),
                                  cfg, {}, stop)
    runner.t0 = runner._clock.time()
    runner._tracking_path = create_isos_tracking_file(
        str(tmp_path), {"protocol_id": "ISOS-L-1", "sample_name": "Base"})
    return runner


def test_baseline_returns_median_and_method(tmp_path):
    runner = _runner_with_file(tmp_path)
    with patch("app.instrument.keithley.time.sleep"):
        result = runner.run_baseline_conditioning(4, 60)
    assert result["method"] == "median_of_N-1"
    assert result["PCE_0_median"] > 0
    assert len(result["all_scans"]) == 4


def test_baseline_saves_four_sheets(tmp_path):
    runner = _runner_with_file(tmp_path)
    with patch("app.instrument.keithley.time.sleep"):
        runner.run_baseline_conditioning(4, 60)
    wb = openpyxl.load_workbook(runner._tracking_path)
    sheets = [s for s in wb.sheetnames if s.startswith("Baseline_#")]
    assert len(sheets) == 4


def test_baseline_median_discards_first_scan(tmp_path):
    """Median is computed from scans 2..N (the first is discarded)."""
    import numpy as np
    runner = _runner_with_file(tmp_path)
    with patch("app.instrument.keithley.time.sleep"):
        result = runner.run_baseline_conditioning(4, 0)
    used = result["all_scans"][1:]
    expected = float(np.median([m["PCE_rev"] for m in used]))
    assert result["PCE_0_median"] == expected
