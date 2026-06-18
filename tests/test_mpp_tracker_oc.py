"""
OC-mode tests for MPPTracker (JV-SPO cycling).

With ``load_mode="OC"`` each cycle still runs its J-V scan normally, but the
SPO phase is replaced by a Voc-hold phase: the instrument is switched to
open-circuit mode and Voc is recorded into ``spo_v`` while ``spo_j`` and
``spo_pce`` stay at 0.
"""

import threading
from unittest.mock import MagicMock, patch

import pytest

from app.measurement.mpp_tracker import MPPTracker
from app.simulation.dummy_cell import DummyCell


def _oc_scpi_instrument(cell, voc=0.98, area=0.07):
    """SCPI mock that tracks source mode.

    In voltage-source mode (J-V scan) :READ? returns the cell's current at the
    set voltage.  In current-source mode (OC) it returns the fixed *voc*.
    """
    inst = MagicMock()
    state = {"v": 0.0, "oc": False}

    def _write(cmd):
        if cmd.startswith(":SOUR:FUNC CURR"):
            state["oc"] = True
        elif cmd.startswith(":SOUR:FUNC VOLT"):
            state["oc"] = False
        elif cmd.startswith(":SOUR:VOLT"):
            state["v"] = float(cmd.split()[-1])

    def _query(cmd):
        if cmd == ":READ?":
            if state["oc"]:
                return f"{voc:.6f},0.000000,+0,+0,+0"
            j = cell.current_density(state["v"])
            raw_a = -(j / 1000.0) * area
            return f"{state['v']:.6f},{raw_a:.10f},+0,+0,+0"
        return "0,0,0,0,0"

    inst.write.side_effect = _write
    inst.query.side_effect = _query
    return inst


def _mpp_params(area=0.07, total_min=0.001, spo_dur=0.5, step_v=0.01):
    return {
        "total_duration_min": total_min,
        "pin":               100.0,
        "cell_area":         area,
        "start_v":          -0.05,
        "end_v":             1.3,
        "step_v":            step_v,
        "include_forward":   False,
        "prebias_enabled":   False,
        "nplc":              1.0,
        "delay_ms":          0,
        "spo_duration_s":    spo_dur,
        "spo_interval_ms":   200,
        "stab_threshold":    0.1,
    }


def _callbacks():
    return {
        "on_status":    MagicMock(),
        "on_log":       MagicMock(),
        "on_jv_update": MagicMock(),
        "on_spo_update": MagicMock(),
        "on_complete":  MagicMock(),
    }


def _run_one_oc_cycle(voc=0.98):
    cell = DummyCell(mode="ideal_stable")
    stop = threading.Event()
    inst = _oc_scpi_instrument(cell, voc=voc)
    cbs = _callbacks()

    # Stop after the first Voc-hold reading so the test is fast.
    def _stop_on_first(t, j, pce, cycle_n, stabilized):
        stop.set()

    cbs["on_spo_update"].side_effect = _stop_on_first

    tracker = MPPTracker(inst, _mpp_params(), cbs, stop, load_mode="OC")
    with patch("app.measurement.mpp_tracker.time.sleep"):
        t = threading.Thread(target=tracker.run, daemon=True)
        t.start()
        t.join(timeout=15.0)
    return tracker


def test_records_nonzero_voc():
    tracker = _run_one_oc_cycle(voc=0.98)
    assert tracker.spo_v, "no Voc values recorded during OC hold"
    assert all(v == pytest.approx(0.98) for v in tracker.spo_v)


def test_spo_j_and_pce_are_zero():
    tracker = _run_one_oc_cycle(voc=0.98)
    assert tracker.spo_j, "no SPO samples recorded"
    assert all(j == 0.0 for j in tracker.spo_j)
    assert all(p == 0.0 for p in tracker.spo_pce)


def test_jv_scan_still_runs():
    """The J-V scan portion of each cycle still produces a cycle entry."""
    tracker = _run_one_oc_cycle()
    assert len(tracker.cycles) >= 1


def test_output_off_in_finally():
    tracker = _run_one_oc_cycle()
    write_cmds = [str(c) for c in tracker.inst.write.call_args_list]
    assert any("OUTP OFF" in cmd for cmd in write_cmds)


def test_default_mode_is_mpp():
    tracker = MPPTracker(MagicMock(), _mpp_params(), _callbacks(),
                         threading.Event())
    assert tracker.load_mode == "MPP"
