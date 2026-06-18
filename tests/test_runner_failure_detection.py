"""Failure-detection threshold tests for ContinuousISOSRunner."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from app.measurement.isos_continuous_runner import ContinuousISOSRunner


def _runner(thresholds):
    return ContinuousISOSRunner(
        MagicMock(), {"failure_thresholds": thresholds}, {},
        threading.Event())


def test_pce_drop_triggers_failure():
    runner = _runner({"pce_norm": 0.20})
    runner.pce_0 = 20.0
    failed, reason = runner._check_failure({"PCE_rev": 3.0})   # 0.15 < 0.20
    assert failed is True
    assert "PCE_norm" in reason


def test_healthy_pce_does_not_trigger():
    runner = _runner({"pce_norm": 0.20})
    runner.pce_0 = 20.0
    failed, reason = runner._check_failure({"PCE_rev": 18.0})  # 0.90 > 0.20
    assert failed is False
    assert reason is None


def test_rsh_dark_threshold():
    runner = _runner({"rsh_dark": 100.0})
    failed, reason = runner._check_failure({"r_sh_dark_rev": 50.0})
    assert failed is True
    assert "R_sh_dark" in reason


def test_idark_ratio_threshold():
    runner = _runner({"idark_ratio": 5.0})
    runner._baseline_idark = 1.0
    failed, reason = runner._check_failure({"i_dark": 10.0})    # 10 > 5×1
    assert failed is True
    assert "I_dark" in reason


def test_no_thresholds_never_fails():
    runner = _runner({})
    runner.pce_0 = 20.0
    failed, _ = runner._check_failure({"PCE_rev": 0.0})
    assert failed is False


def test_failure_fields_written_to_meas():
    runner = _runner({"pce_norm": 0.20})
    runner.pce_0 = 20.0
    meas = {"PCE_rev": 1.0}
    runner._check_failure(meas)
    assert meas["failure_detected"] is True
    assert meas["failure_reason"]
