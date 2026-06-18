"""
End-to-end tests for ContinuousISOSRunner against a SCPI-style fake instrument.

A virtual clock drives scheduling deterministically; the keithley helpers'
``time.sleep`` is patched out so J-V scans are instant.
"""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest

from app.measurement.isos_continuous_runner import ContinuousISOSRunner
from app.io.isos_tracking import read_isos_log

from tests._isos_runner_helpers import FakeKeithley, make_runner_config


def _run(config) -> ContinuousISOSRunner:
    stop = threading.Event()
    runner = ContinuousISOSRunner(FakeKeithley(area=config["cell_area"]),
                                  config, {}, stop)
    with patch("app.instrument.keithley.time.sleep"):
        runner.run()
    return runner


def test_oc_run_produces_five_scans(tmp_path):
    cfg = make_runner_config(tmp_path, load_mode="OC")
    runner = _run(cfg)
    rows = read_isos_log(runner._tracking_path)
    assert len(rows) == 5


def test_mpp_po_run_produces_five_scans(tmp_path):
    cfg = make_runner_config(tmp_path, load_mode="MPP", mpp_engine="PO")
    runner = _run(cfg)
    rows = read_isos_log(runner._tracking_path)
    assert len(rows) == 5


def test_dark_stress_fires_shutter_twice_per_scan(tmp_path):
    counts = {"open": 0, "close": 0}

    def on_shutter(action):
        counts[action] += 1

    cfg = make_runner_config(tmp_path, stress_with_light=False,
                             load_mode="OC", setpoint_v=1.0)
    stop = threading.Event()
    runner = ContinuousISOSRunner(FakeKeithley(area=cfg["cell_area"]),
                                  cfg, {"on_shutter": on_shutter}, stop)
    with patch("app.instrument.keithley.time.sleep"):
        runner.run()

    n_scans = len(read_isos_log(runner._tracking_path))
    assert n_scans >= 1
    assert counts["open"] == n_scans
    assert counts["close"] == n_scans


def test_on_scan_done_called_per_scan(tmp_path):
    seen = []
    cfg = make_runner_config(tmp_path)
    stop = threading.Event()
    runner = ContinuousISOSRunner(FakeKeithley(area=cfg["cell_area"]),
                                  cfg, {"on_scan_done": seen.append}, stop)
    with patch("app.instrument.keithley.time.sleep"):
        runner.run()
    assert len(seen) == 5
    assert all("PCE_rev" in m for m in seen)


def test_on_complete_fires_once(tmp_path):
    calls = {"n": 0}
    cfg = make_runner_config(tmp_path)
    stop = threading.Event()
    runner = ContinuousISOSRunner(
        FakeKeithley(area=cfg["cell_area"]), cfg,
        {"on_complete": lambda *_: calls.__setitem__("n", calls["n"] + 1)},
        stop)
    with patch("app.instrument.keithley.time.sleep"):
        runner.run()
    assert calls["n"] == 1


def test_stop_event_halts_run(tmp_path):
    cfg = make_runner_config(tmp_path)
    stop = threading.Event()
    stop.set()                       # already stopped before start
    runner = ContinuousISOSRunner(FakeKeithley(area=cfg["cell_area"]),
                                  cfg, {}, stop)
    with patch("app.instrument.keithley.time.sleep"):
        runner.run()
    rows = read_isos_log(runner._tracking_path)
    assert rows == []
