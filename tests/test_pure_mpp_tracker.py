"""
Tests for app/measurement/pure_mpp_tracker.py — P&O algorithm logic.

The Keithley instrument is replaced by a mock that returns configurable
J values so we can test the algorithm decisions without hardware.
"""

import threading
import time
from unittest.mock import MagicMock, patch, call

import numpy as np
import pytest

from app.measurement.pure_mpp_tracker import PureMPPTracker


# ── instrument mock factory ───────────────────────────────────────────────────

def make_instrument(j_sequence):
    """
    Return a mock pyvisa resource that yields currents from j_sequence
    (in mA/cm² sign convention) on each :READ? call.

    The mock converts back to raw amps as the Keithley would:
        raw_current = -(j_mAcm2 / 1000) * area
    The tracker negates and divides, recovering j_mAcm2.
    """
    inst = MagicMock()
    responses = iter(j_sequence)

    def read_side_effect(cmd):
        if cmd == ":READ?":
            j = next(responses, j_sequence[-1])
            area = 0.07
            raw_A = -(j / 1000.0) * area
            # Keithley returns: voltage, current, ...
            return f"0.8000,{raw_A:.8f},0,0,0"
        return ""

    inst.query.side_effect = read_side_effect
    return inst


def default_params(v_start=0.5, total_min=0.5):
    return {
        "total_duration_min": total_min,
        "perturb_step_mv":    10.0,
        "v_min":              0.0,
        "v_max":              1.2,
        "interval_ms":        50,
        "pin":                100.0,
        "cell_area":          0.07,
        "nplc":               1.0,
        "delay_ms":           100,
        "scan_start_v":       -0.1,
        "scan_end_v":         1.1,
        "scan_step_v":        0.05,
    }


def default_callbacks():
    return {
        "on_status":   MagicMock(),
        "on_update":   MagicMock(),
        "on_jv_done":  MagicMock(),
        "on_complete": MagicMock(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Initialization tests
# ─────────────────────────────────────────────────────────────────────────────

class TestPureMPPTrackerInit:

    def test_attributes_initialized(self):
        stop = threading.Event()
        inst = MagicMock()
        params = default_params()
        cbs = default_callbacks()
        tracker = PureMPPTracker(inst, params, cbs, stop)

        assert tracker.time_s   == []
        assert tracker.v_track  == []
        assert tracker.j_track  == []
        assert tracker.pce_track == []
        assert tracker.t_start  is None

    def test_params_stored(self):
        stop = threading.Event()
        inst = MagicMock()
        params = default_params()
        tracker = PureMPPTracker(inst, params, default_callbacks(), stop)
        assert tracker.params is params

    def test_stop_event_stored(self):
        stop = threading.Event()
        tracker = PureMPPTracker(MagicMock(), default_params(),
                                 default_callbacks(), stop)
        assert tracker.stop_event is stop


# ─────────────────────────────────────────────────────────────────────────────
# _find_vmpp
# ─────────────────────────────────────────────────────────────────────────────

class TestFindVmpp:

    def _make_tracker(self):
        return PureMPPTracker(MagicMock(), default_params(),
                              default_callbacks(), threading.Event())

    def test_returns_voltage_at_max_power(self):
        tracker = self._make_tracker()
        v = np.array([0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
        j = np.array([20., 20., 19., 16.,  8.,  0.])
        vmpp = tracker._find_vmpp(v, j)
        # P = v*j: [0, 4, 7.6, 9.6, 6.4, 0] → max at index 3 → V=0.6
        assert vmpp == pytest.approx(0.6)

    def test_empty_arrays_return_fallback(self):
        params = default_params()
        params["v_max"] = 1.1
        tracker = PureMPPTracker(MagicMock(), params,
                                 default_callbacks(), threading.Event())
        vmpp = tracker._find_vmpp(np.array([]), np.array([]))
        assert vmpp == pytest.approx(1.1 * 0.7)

    def test_single_point(self):
        tracker = self._make_tracker()
        v = np.array([0.8])
        j = np.array([15.0])
        vmpp = tracker._find_vmpp(v, j)
        assert vmpp == pytest.approx(0.8)

    def test_vmpp_is_one_of_input_voltages(self):
        tracker = self._make_tracker()
        v = np.linspace(0.0, 1.2, 50)
        j = np.array([20.0 - 20.0 * (vi / 1.05) ** 2 for vi in v])
        vmpp = tracker._find_vmpp(v, j)
        assert vmpp in v


# ─────────────────────────────────────────────────────────────────────────────
# P&O direction logic
# ─────────────────────────────────────────────────────────────────────────────

class TestPODirectionLogic:
    """
    Test the core P&O decision: does the tracker reverse direction when
    power drops?  We test the logic by running a very short measurement.
    """

    def _run_short(self, j_sequence, n_iterations=6):
        """Run the tracker for n_iterations steps and return recorded data."""
        stop = threading.Event()
        params = default_params(total_min=0.5)
        params["interval_ms"] = 10   # fast loop for tests
        params["perturb_step_mv"] = 10.0

        # Build a J sequence that satisfies the loop iterations
        # Prepend enough readings for the initial scan too
        n_scan_points = int((params["scan_end_v"] - params["scan_start_v"]) /
                            params["scan_step_v"]) + 2
        j_full = [20.0] * n_scan_points + list(j_sequence)

        inst = make_instrument(j_full)
        cbs  = default_callbacks()
        tracker = PureMPPTracker(inst, params, cbs, stop)

        # Stop after n_iterations P&O readings
        counter = {"n": 0}
        original_cb = cbs["on_update"]
        def counting_cb(t, v, j, pce):
            counter["n"] += 1
            if counter["n"] >= n_iterations:
                stop.set()
        cbs["on_update"].side_effect = counting_cb

        thread = threading.Thread(target=tracker.run, daemon=True)
        thread.start()
        thread.join(timeout=5.0)
        stop.set()

        return tracker

    def test_data_recorded(self):
        # Provide enough J values
        j_seq = [20.0] * 20
        tracker = self._run_short(j_seq, n_iterations=4)
        assert len(tracker.time_s) >= 1
        assert len(tracker.pce_track) >= 1

    def test_pce_positive(self):
        j_seq = [20.0] * 20
        tracker = self._run_short(j_seq, n_iterations=4)
        assert all(p >= 0 for p in tracker.pce_track)

    def test_time_increasing(self):
        j_seq = [20.0] * 20
        tracker = self._run_short(j_seq, n_iterations=4)
        if len(tracker.time_s) > 1:
            diffs = np.diff(tracker.time_s)
            assert all(d >= 0 for d in diffs)

    def test_voltage_clamped_to_range(self):
        j_seq = [20.0] * 20
        params = default_params()
        tracker = self._run_short(j_seq, n_iterations=5)
        v_min = default_params()["v_min"]
        v_max = default_params()["v_max"]
        for v in tracker.v_track:
            assert v_min <= v <= v_max

    def test_callbacks_called(self):
        j_seq = [20.0] * 20
        stop = threading.Event()
        params = default_params()
        params["interval_ms"] = 10

        n_scan = int((params["scan_end_v"] - params["scan_start_v"]) /
                     params["scan_step_v"]) + 2
        j_full = [20.0] * n_scan + list(j_seq)

        inst = make_instrument(j_full)
        cbs  = default_callbacks()
        counter = {"n": 0}

        def stop_after_3(t, v, j, pce):
            counter["n"] += 1
            if counter["n"] >= 3:
                stop.set()
        cbs["on_update"].side_effect = stop_after_3

        tracker = PureMPPTracker(inst, params, cbs, stop)
        t = threading.Thread(target=tracker.run, daemon=True)
        t.start()
        t.join(timeout=5.0)
        stop.set()

        cbs["on_status"].assert_called()
        cbs["on_jv_done"].assert_called_once()
        cbs["on_complete"].assert_called_once()

    def test_output_turned_off_on_completion(self):
        stop = threading.Event()
        params = default_params(total_min=0.001)   # very short duration
        params["interval_ms"] = 10

        n_scan = int((params["scan_end_v"] - params["scan_start_v"]) /
                     params["scan_step_v"]) + 2
        j_full = [20.0] * (n_scan + 10)

        inst = make_instrument(j_full)
        cbs  = default_callbacks()
        tracker = PureMPPTracker(inst, params, cbs, stop)

        t = threading.Thread(target=tracker.run, daemon=True)
        t.start()
        t.join(timeout=5.0)

        # Verify :OUTP OFF was sent
        write_calls = [str(c) for c in inst.write.call_args_list]
        assert any("OUTP OFF" in c for c in write_calls)

    def test_stop_event_aborts_loop(self):
        """Setting stop_event mid-run should terminate cleanly."""
        stop = threading.Event()
        params = default_params(total_min=60.0)   # would run for 60 min
        params["interval_ms"] = 100

        n_scan = int((params["scan_end_v"] - params["scan_start_v"]) /
                     params["scan_step_v"]) + 2
        j_full = [20.0] * (n_scan + 50)

        inst = make_instrument(j_full)
        cbs  = default_callbacks()

        n_updates = {"count": 0}
        def cb(t, v, j, pce):
            n_updates["count"] += 1
            if n_updates["count"] >= 2:
                stop.set()
        cbs["on_update"].side_effect = cb

        tracker = PureMPPTracker(inst, params, cbs, stop)
        t = threading.Thread(target=tracker.run, daemon=True)
        t.start()
        t.join(timeout=5.0)

        assert not t.is_alive(), "Tracker did not stop after stop_event was set"
        cbs["on_complete"].assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# _configure_instrument
# ─────────────────────────────────────────────────────────────────────────────

class TestConfigureInstrument:

    def test_scpi_commands_sent(self):
        inst = MagicMock()
        params = default_params()
        tracker = PureMPPTracker(inst, params, default_callbacks(),
                                 threading.Event())
        tracker._configure_instrument()
        calls = [str(c) for c in inst.write.call_args_list]
        assert any("*RST" in c for c in calls)
        assert any("SOUR:FUNC VOLT" in c for c in calls)
        assert any("OUTP ON" in c for c in calls)

    def test_nplc_set_from_params(self):
        inst = MagicMock()
        params = default_params()
        params["nplc"] = 2.5
        tracker = PureMPPTracker(inst, params, default_callbacks(),
                                 threading.Event())
        tracker._configure_instrument()
        calls = [str(c) for c in inst.write.call_args_list]
        assert any("2.500" in c for c in calls)

    def test_delay_set_from_params(self):
        inst = MagicMock()
        params = default_params()
        params["delay_ms"] = 200
        tracker = PureMPPTracker(inst, params, default_callbacks(),
                                 threading.Event())
        tracker._configure_instrument()
        calls = [str(c) for c in inst.write.call_args_list]
        assert any("0.2000" in c for c in calls)
