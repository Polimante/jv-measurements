"""
OC-mode tests for PureMPPTracker.

With ``load_mode="OC"`` the tracker holds the cell at open circuit and records
Voc on each tick instead of running the P&O algorithm.  ``j`` and ``pce`` are
reported as 0.
"""

import threading
from unittest.mock import MagicMock, patch

import pytest

from app.measurement.pure_mpp_tracker import PureMPPTracker


def _oc_instrument(voc=0.95):
    """SCPI mock: :READ? field 0 = Voc, field 1 = small current (for scans)."""
    inst = MagicMock()

    def _query(cmd):
        if cmd == ":READ?":
            return f"{voc:.6f},-0.0001000,+0,+0,+0"
        return "0,0,0,0,0"

    inst.query.side_effect = _query
    return inst


def _oc_params(total_min=60.0, skip_scan=True):
    return {
        "total_duration_min":  total_min,
        "interval_ms":         10,
        "nplc":                1.0,
        "delay_ms":            0,
        "oc_skip_initial_scan": skip_scan,
        "scan_start_v":        -0.05,
        "scan_end_v":          1.10,
        "scan_step_v":         0.05,
        "perturb_step_mv":     10.0,
        "v_min":               0.0,
        "v_max":               1.20,
        "pin":                 100.0,
        "cell_area":           0.07,
    }


def _callbacks():
    return {
        "on_status":   MagicMock(),
        "on_update":   MagicMock(),
        "on_jv_done":  MagicMock(),
        "on_complete": MagicMock(),
    }


def _run_oc(params, voc=0.95, n_updates=5):
    stop = threading.Event()
    inst = _oc_instrument(voc)
    cbs = _callbacks()

    counter = {"n": 0}

    def _count(t, v, j, pce):
        counter["n"] += 1
        if counter["n"] >= n_updates:
            stop.set()

    cbs["on_update"].side_effect = _count

    tracker = PureMPPTracker(inst, params, cbs, stop, load_mode="OC")
    with patch("app.measurement.pure_mpp_tracker.time.sleep"):
        t = threading.Thread(target=tracker.run, daemon=True)
        t.start()
        t.join(timeout=10.0)
    stop.set()
    return tracker, cbs


def test_records_nonzero_voc():
    tracker, _ = _run_oc(_oc_params(), voc=0.95)
    assert tracker.v_track, "no Voc values recorded"
    assert all(v == pytest.approx(0.95) for v in tracker.v_track)


def test_j_and_pce_are_zero():
    tracker, _ = _run_oc(_oc_params(), voc=0.95)
    assert all(j == 0.0 for j in tracker.j_track)
    assert all(p == 0.0 for p in tracker.pce_track)


def test_skip_initial_scan_skips_jv_done():
    _, cbs = _run_oc(_oc_params(skip_scan=True))
    cbs["on_jv_done"].assert_not_called()


def test_baseline_scan_runs_when_not_skipped():
    _, cbs = _run_oc(_oc_params(skip_scan=False))
    cbs["on_jv_done"].assert_called_once()


def test_output_turned_off_on_completion():
    tracker, _ = _run_oc(_oc_params())
    write_calls = [str(c) for c in tracker.inst.write.call_args_list]
    assert any("OUTP OFF" in c for c in write_calls)


def test_default_mode_is_mpp():
    tracker = PureMPPTracker(MagicMock(), _oc_params(), _callbacks(),
                             threading.Event())
    assert tracker.load_mode == "MPP"
