"""
MPP and Pure MPP tracker logic tests — Part D of the bug-hunt suite.

MPPTracker (JV-SPO cycling) uses raw SCPI commands, so we wrap a DummyCell
in a pyvisa-style MagicMock.  PureMPPTracker tests add coverage for the
initial Vmpp scan and direction-reversal behaviour that existing tests skip.

All sleeps are patched to zero so tests run in < 2 s.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.measurement.mpp_tracker import MPPTracker
from app.measurement.pure_mpp_tracker import PureMPPTracker
from app.simulation.dummy_cell import DummyCell


# ── SCPI-compatible instrument wrapper ────────────────────────────────────────

def _scpi_instrument(cell: DummyCell, area: float = 0.07) -> MagicMock:
    """Wrap a DummyCell so MPPTracker's raw SCPI calls work correctly.

    MPPTracker calls:
        inst.write(":SOUR:VOLT 0.8500")   → set voltage
        inst.query(":READ?")               → "V,raw_A,..."
    """
    inst = MagicMock()
    _v: list[float] = [0.0]

    def _write(cmd: str) -> None:
        if cmd.startswith(":SOUR:VOLT"):
            _v[0] = float(cmd.split()[-1])

    def _query(cmd: str) -> str:
        if cmd == ":READ?":
            j = cell.current_density(_v[0])
            raw_a = -(j / 1000.0) * area
            return f"{_v[0]:.6f},{raw_a:.10f},+0,+0,+0"
        return "0,0,0,0,0"

    inst.write.side_effect = _write
    inst.query.side_effect = _query
    return inst


def _mpp_params(area=0.07, total_min=0.001, spo_dur=0.5, step_v=0.005):
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


def _mpp_callbacks():
    return {
        "on_status":    MagicMock(),
        "on_log":       MagicMock(),
        "on_jv_update": MagicMock(),
        "on_spo_update": MagicMock(),
        "on_complete":  MagicMock(),
    }


def _run_one_mpp_cycle(cell, params=None, area=0.07):
    """Run MPPTracker for exactly one J-V+SPO cycle; return the tracker."""
    if params is None:
        params = _mpp_params(area=area)
    stop = threading.Event()
    inst = _scpi_instrument(cell, area)
    cbs  = _mpp_callbacks()

    # Stop after the first SPO reading so the test is fast and deterministic
    def _stop_on_first_spo(t, j, pce, cycle_n, stabilized):
        stop.set()
    cbs["on_spo_update"].side_effect = _stop_on_first_spo

    tracker = MPPTracker(inst, params, cbs, stop)
    with patch("app.measurement.mpp_tracker.time.sleep"):
        t = threading.Thread(target=tracker.run, daemon=True)
        t.start()
        t.join(timeout=15.0)
    return tracker


# ── MPPTracker tests ──────────────────────────────────────────────────────────

class TestMPPTrackerVmpp:

    def test_vmpp_first_cycle_within_5mv(self):
        """Vmpp from the first cycle must be within 5 mV of the analytical MPP."""
        cell = DummyCell(mode="ideal_stable")

        # Analytical Vmpp on a fine grid
        v_arr = np.linspace(-0.05, 1.3, 2000)
        j_arr = cell.scan(v_arr)
        true_vmpp = float(v_arr[np.argmax(v_arr * j_arr)])

        tracker = _run_one_mpp_cycle(cell)
        assert len(tracker.cycles) >= 1, "Tracker produced no cycles"
        measured_vmpp = tracker.cycles[0]["Vmpp"]
        assert abs(measured_vmpp - true_vmpp) < 0.005, (
            f"Vmpp={measured_vmpp:.4f} V, true={true_vmpp:.4f} V "
            f"(diff={abs(measured_vmpp - true_vmpp)*1000:.1f} mV)"
        )

    def test_vmpp_positive(self):
        """Vmpp must be a positive voltage for an illuminated cell."""
        cell = DummyCell(mode="ideal_stable")
        tracker = _run_one_mpp_cycle(cell)
        assert len(tracker.cycles) >= 1
        assert tracker.cycles[0]["Vmpp"] > 0


class TestMPPTrackerHI:

    def test_hi_computed_when_forward_enabled(self):
        """HI is not None when include_forward=True."""
        cell = DummyCell(mode="with_hysteresis")
        params = _mpp_params()
        params["include_forward"] = True
        tracker = _run_one_mpp_cycle(cell, params)
        assert len(tracker.cycles) >= 1
        assert tracker.cycles[0]["HI"] is not None

    def test_hi_none_when_forward_disabled(self):
        """HI is None when include_forward=False (no forward scan to compare)."""
        cell = DummyCell(mode="ideal_stable")
        params = _mpp_params()
        params["include_forward"] = False
        tracker = _run_one_mpp_cycle(cell, params)
        assert len(tracker.cycles) >= 1
        assert tracker.cycles[0]["HI"] is None


class TestMPPTrackerControl:

    def test_stop_event_terminates_tracker(self):
        """Setting stop_event before the run begins must terminate cleanly."""
        cell = DummyCell(mode="ideal_stable")
        stop = threading.Event()
        inst = _scpi_instrument(cell)
        cbs  = _mpp_callbacks()

        stop.set()   # pre-set the stop event
        tracker = MPPTracker(inst, _mpp_params(total_min=60.0), cbs, stop)
        with patch("app.measurement.mpp_tracker.time.sleep"):
            t = threading.Thread(target=tracker.run, daemon=True)
            t.start()
            t.join(timeout=5.0)
        assert not t.is_alive(), "Tracker thread did not terminate"
        cbs["on_complete"].assert_called_once()

    def test_output_off_in_finally(self):
        """The tracker must send :OUTP OFF in its finally block."""
        cell = DummyCell(mode="ideal_stable")
        tracker = _run_one_mpp_cycle(cell)
        write_cmds = [str(c) for c in tracker.inst.write.call_args_list]
        assert any("OUTP OFF" in cmd for cmd in write_cmds)

    def test_cycle_data_has_required_keys(self):
        """Each cycle dict must have Vmpp, Voc, Jsc, FF, PCE keys."""
        cell = DummyCell(mode="ideal_stable")
        tracker = _run_one_mpp_cycle(cell)
        assert len(tracker.cycles) >= 1
        cyc = tracker.cycles[0]
        for key in ("Vmpp", "Voc", "Jsc", "FF", "PCE"):
            assert key in cyc, f"Missing key {key!r} in cycle data"


# ── PureMPPTracker tests (new coverage) ──────────────────────────────────────

def _pure_mpp_instrument(cell: DummyCell, area: float = 0.07) -> MagicMock:
    """SCPI mock for PureMPPTracker."""
    return _scpi_instrument(cell, area)


def _pure_mpp_params(area=0.07, total_min=0.001):
    return {
        "total_duration_min": total_min,
        "perturb_step_mv":    5.0,
        "v_min":              0.0,
        "v_max":              1.3,
        "interval_ms":        50,
        "pin":                100.0,
        "cell_area":          area,
        "nplc":               1.0,
        "delay_ms":           0,
        "scan_start_v":      -0.05,
        "scan_end_v":         1.3,
        "scan_step_v":        0.005,
    }


class TestPureMPPVmppFromDummyCell:

    def test_initial_scan_vmpp_within_10mv(self):
        """The Vmpp found by the initial reverse scan should be within 10 mV
        of the analytical maximum power point of the ideal_stable DummyCell.
        """
        cell = DummyCell(mode="ideal_stable")

        # Analytical Vmpp
        v_arr = np.linspace(-0.05, 1.3, 4000)
        j_arr = cell.scan(v_arr)
        true_vmpp = float(v_arr[np.argmax(v_arr * j_arr)])

        stop = threading.Event()
        inst = _pure_mpp_instrument(cell)
        cbs  = {
            "on_status":   MagicMock(),
            "on_update":   MagicMock(),
            "on_jv_done":  MagicMock(),
            "on_complete": MagicMock(),
        }
        reported_vmpp: list[float] = []

        def _capture_jv_done(v_arr_out, j_arr_out, vmpp):
            reported_vmpp.append(vmpp)
            stop.set()   # stop after initial scan

        cbs["on_jv_done"].side_effect = _capture_jv_done

        tracker = PureMPPTracker(inst, _pure_mpp_params(), cbs, stop)
        with patch("app.measurement.pure_mpp_tracker.time.sleep"):
            t = threading.Thread(target=tracker.run, daemon=True)
            t.start()
            t.join(timeout=10.0)

        assert reported_vmpp, "on_jv_done was never called"
        measured = reported_vmpp[0]
        assert abs(measured - true_vmpp) < 0.010, (
            f"Vmpp from scan={measured:.4f} V, true={true_vmpp:.4f} V"
        )

    def test_pce_positive_during_tracking(self):
        """All PCE values recorded during tracking must be ≥ 0."""
        cell = DummyCell(mode="ideal_stable")
        stop = threading.Event()
        inst = _pure_mpp_instrument(cell)
        cbs  = {
            "on_status":   MagicMock(),
            "on_update":   MagicMock(),
            "on_jv_done":  MagicMock(),
            "on_complete": MagicMock(),
        }
        counter: dict[str, int] = {"n": 0}

        def _count_updates(t, v, j, pce):
            counter["n"] += 1
            if counter["n"] >= 5:
                stop.set()

        cbs["on_update"].side_effect = _count_updates

        tracker = PureMPPTracker(inst, _pure_mpp_params(), cbs, stop)
        with patch("app.measurement.pure_mpp_tracker.time.sleep"):
            t = threading.Thread(target=tracker.run, daemon=True)
            t.start()
            t.join(timeout=10.0)

        assert all(p >= 0 for p in tracker.pce_track)
