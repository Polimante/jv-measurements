"""SPO-after-scan tests for ContinuousISOSRunner."""

from __future__ import annotations

import threading

import openpyxl

from app.measurement.isos_continuous_runner import ContinuousISOSRunner
from app.io.isos_tracking import create_isos_tracking_file

from tests._isos_runner_helpers import FakeKeithley, VirtualClock


def _runner(tmp_path, tracking=True):
    cfg = {"cell_area": 0.07, "pin": 100.0, "clock": VirtualClock()}
    runner = ContinuousISOSRunner(FakeKeithley(area=0.07), cfg, {},
                                  threading.Event())
    runner.t0 = runner._clock.time()
    if tracking:
        runner._tracking_path = create_isos_tracking_file(
            str(tmp_path), {"protocol_id": "ISOS-L-1", "sample_name": "Spo"})
    else:
        runner._tracking_path = None
    return runner


def test_spo_returns_sensible_pce(tmp_path):
    runner = _runner(tmp_path, tracking=False)
    spo = runner.perform_spo_after_scan(vmpp=0.8, duration_s=1.0)
    assert spo["PCE_SPO"] is not None
    assert spo["PCE_SPO"] > 0
    assert spo["PCE_SPO_std"] >= 0
    assert len(spo["pce_trace"]) > 0
    assert len(spo["time_s"]) == len(spo["pce_trace"])


def test_spo_flat_trace_has_low_std(tmp_path):
    runner = _runner(tmp_path, tracking=False)
    spo = runner.perform_spo_after_scan(vmpp=0.8, duration_s=1.0)
    # ideal_stable cell at a fixed voltage → constant current → ~0 std
    assert spo["PCE_SPO_std"] < 1e-6


def test_spo_none_vmpp_returns_empty(tmp_path):
    runner = _runner(tmp_path, tracking=False)
    spo = runner.perform_spo_after_scan(vmpp=None, duration_s=1.0)
    assert spo["PCE_SPO"] is None
    assert spo["pce_trace"] == []


def test_spo_persists_sheet(tmp_path):
    runner = _runner(tmp_path, tracking=True)
    runner.perform_spo_after_scan(vmpp=0.8, duration_s=1.0)
    wb = openpyxl.load_workbook(runner._tracking_path)
    assert any(s.startswith("SPO_#") for s in wb.sheetnames)
