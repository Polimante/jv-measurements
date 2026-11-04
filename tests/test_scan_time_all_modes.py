"""Every mode that sweeps records how long the sweep really took.

Each test drives the real orchestrator against a fake instrument and reads the
workbook it writes. The column positions matter because existing readers index
these sheets by position, so the new columns must only ever be trailing ones.
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import openpyxl
import pytest

from app.gui.data_viewer import load_jv_file
from app.instrument.dummy_smu import DummySMU
from app.io.exporter import save_mpp_data, save_pure_mpp_data
from app.io.isos_tracking import (
    _LOG_HEADERS,
    append_isos_d1_measurement,
    create_isos_d1_tracking_file,
)
from app.measurement.isos_continuous_runner import ContinuousISOSRunner
from app.measurement.light_intensity_jv import LightIntensityJVMeasurement
from app.measurement.mpp_tracker import MPPTracker
from app.measurement.pure_mpp_tracker import PureMPPTracker
from app.simulation.fake_keithley import FakeKeithley
from app.utils.scan_timing import timed_scan
from tests._isos_runner_helpers import make_runner_config


def _log_column(tracking_path, header):
    ws = openpyxl.load_workbook(tracking_path, data_only=True)["ISOS_Log"]
    col = [c.value for c in ws[1]].index(header) + 1
    return [ws.cell(r, col).value for r in range(2, ws.max_row + 1)]


class TestTimedScan:
    def test_records_duration_and_rate(self):
        timing = {}
        v = np.linspace(0, 1, 11)
        out = timed_scan(timing, "fwd", lambda: (v, v * 2))
        assert out[0] is v
        assert timing["fwd_s"] >= 0
        assert "fwd_rate" in timing

    def test_stopped_scan_records_nothing(self):
        timing = {}
        assert timed_scan(timing, "rev", lambda: (None, None)) == (None, None)
        assert timing == {}

    def test_no_dict_is_fine(self):
        v = np.linspace(0, 1, 3)
        assert timed_scan(None, "fwd", lambda: (v, v))[0] is v


class TestContinuousIsos:
    def _run(self, config):
        runner = ContinuousISOSRunner(FakeKeithley(area=config["cell_area"]),
                                      config, {}, threading.Event())
        with patch("app.instrument.keithley.time.sleep"):
            runner.run()
        return runner

    def test_log_and_backup_carry_the_scan_times(self, tmp_path):
        runner = self._run(make_runner_config(tmp_path, load_mode="OC"))
        for header in ("Fwd_Scan_Time_s", "Rev_Scan_Time_s",
                       "Measured_Fwd_Rate_mV_s", "Measured_Rev_Rate_mV_s"):
            values = _log_column(runner._tracking_path, header)
            assert values and all(v is not None and v > 0 for v in values), header

        backups = [p for p in Path(tmp_path).glob("L1Test_2*.xlsx")
                   if "MeasurementData" in openpyxl.load_workbook(p, read_only=True).sheetnames]
        assert backups
        meta = load_jv_file(str(backups[0]))
        assert meta["fwd_time_s"] > 0 and meta["rev_time_s"] > 0

    def test_new_columns_are_trailing(self):
        assert _LOG_HEADERS[-4:] == ["Fwd_Scan_Time_s", "Rev_Scan_Time_s",
                                     "Measured_Fwd_Rate_mV_s", "Measured_Rev_Rate_mV_s"]

    def test_older_workbook_gains_the_missing_headers(self, tmp_path):
        fp = create_isos_d1_tracking_file(str(tmp_path), {"sample_name": "Old"})
        wb = openpyxl.load_workbook(fp)
        wb["ISOS_Log"].delete_cols(len(_LOG_HEADERS) - 3, 4)
        wb.save(fp)

        v = np.linspace(0.0, 1.0, 5)
        append_isos_d1_measurement(fp, {
            "v_fwd": v, "j_fwd": 1 - v, "v_rev": v[::-1], "j_rev": (1 - v)[::-1],
            **{f"{k}_{d}": 1.0 for k in ("Voc", "Jsc", "FF", "PCE", "Rs", "Rsh")
               for d in ("fwd", "rev")},
            "HI": 0.0, "fwd_s": 12.0, "rev_s": 13.0, "fwd_rate": 97.0, "rev_rate": 96.0,
        })
        assert _log_column(fp, "Fwd_Scan_Time_s") == [12.0]


class TestLightIntensity:
    def _measurement(self, tmp_path, intensities):
        smu = DummySMU(sleep_scale=0.0)
        smu.connect()
        messages = []

        def prompt(_msg, intensity):
            smu.set_illumination_factor(intensity)
            return True

        meas = LightIntensityJVMeasurement(
            sourcemeter=smu, intensities=intensities,
            scan_params={"start_v": -0.05, "end_v": 1.3, "step_v": 0.05, "delay_ms": 0,
                         "nplc": 1.0, "cell_area": 0.07, "four_wire": False,
                         "reference_illumination_mw_cm2": 100.0},
            stabilization_time_s=0,
            pre_bias_settings={"enabled": False, "voltage": 0.0, "duration": 0.0},
            light_soaking_settings={"enabled": False, "duration": 0.0},
            sample_name="LI", output_folder=tmp_path, auto_single_diode_fit=False,
            on_status=messages.append, on_prompt_user=prompt,
            on_progress=lambda i, n, s: None, on_measurement_done=lambda i, r: None,
            on_complete=lambda p: None, stop_event=threading.Event(),
        )
        return meas, messages

    def test_each_intensity_is_timed_and_reported(self, tmp_path):
        meas, messages = self._measurement(tmp_path, [1.0, 0.5])
        meas.run()
        assert all(r.timing["fwd_s"] > 0 and r.timing["rev_s"] > 0 for r in meas.results)
        assert any("Done" in m and "FWD" in m for m in messages)

        consolidated = next(p for p in Path(tmp_path).glob("*.xlsx")
                            if "Summary" in openpyxl.load_workbook(p, read_only=True).sheetnames)
        ws = openpyxl.load_workbook(consolidated)["Summary"]
        assert ws.cell(1, 13).value == "Status"          # readers index by position
        assert ws.cell(1, 14).value == "Forward Scan Time (s)"
        assert ws.cell(2, 14).value > 0

        backups = list(Path(tmp_path).glob("LI_LD_1sun_*.xlsx"))
        assert load_jv_file(str(backups[0]))["fwd_time_s"] > 0

        # The Data Viewer still reads the consolidated workbook.
        assert load_jv_file(str(consolidated))["type"] == "light_jv"


def _scpi_mock(area=0.07):
    fake = FakeKeithley.from_mode("ideal_stable", area=area)
    inst = MagicMock()
    inst.write.side_effect = fake.write
    inst.query.side_effect = fake.query
    return inst


class TestMppTracker:
    def _params(self):
        return {
            "total_duration_min": 60.0, "start_v": -0.05, "end_v": 1.3, "step_v": 0.05,
            "cell_area": 0.07, "pin": 100.0, "include_forward": True,
            "prebias_enabled": False, "nplc": 1.0, "delay_ms": 0,
            "spo_duration_s": 0.2, "spo_interval_ms": 200, "stab_threshold": 0.1,
        }

    def test_cycle_timing_reaches_log_status_and_file(self, tmp_path):
        stop = threading.Event()
        cbs = {k: MagicMock() for k in ("on_status", "on_log", "on_jv_update",
                                        "on_spo_update", "on_complete")}
        cbs["on_spo_update"].side_effect = lambda *a: stop.set()
        tracker = MPPTracker(_scpi_mock(), self._params(), cbs, stop)
        with patch("app.measurement.mpp_tracker.time.sleep"):
            tracker.run()

        cycle = tracker.cycles[0]
        assert cycle["timing"]["fwd_s"] > 0 and cycle["timing"]["rev_s"] > 0
        assert any("Scan time: FWD" in str(c) for c in cbs["on_log"].call_args_list)
        assert any("J-V: FWD" in str(c) for c in cbs["on_status"].call_args_list)

        fp = save_mpp_data(str(tmp_path), "M", tracker.cycles, [0.0], [1.0], [1.0], [1],
                           0.07, 100.0, 1.0, 0, 1.0, 1.0)
        ws = openpyxl.load_workbook(fp)["MPP_JV"]
        assert ws.cell(1, 9).value == "HI (%)"           # readers index by position
        assert ws.cell(1, 10).value == "Forward Scan Time (s)"
        assert ws.cell(2, 10).value == pytest.approx(cycle["timing"]["fwd_s"])
        assert load_jv_file(fp)["type"] == "mpp"


class TestPureMpp:
    def _params(self):
        return {
            "total_duration_min": 0.001, "perturb_step_mv": 5.0, "interval_ms": 100,
            "cell_area": 0.07, "pin": 100.0, "nplc": 1.0, "delay_ms": 0,
            "scan_start_v": -0.05, "scan_end_v": 1.3, "scan_step_v": 0.05,
            "v_min": 0.0, "v_max": 1.3,
        }

    def test_initial_scan_is_timed_and_saved(self, tmp_path):
        stop = threading.Event()
        cbs = {k: MagicMock() for k in ("on_status", "on_update", "on_jv_done", "on_complete")}
        cbs["on_jv_done"].side_effect = lambda *a: stop.set()
        tracker = PureMPPTracker(_scpi_mock(), self._params(), cbs, stop)
        with patch("app.measurement.pure_mpp_tracker.time.sleep"):
            tracker.run()

        assert tracker.initial_timing["rev_s"] > 0
        assert any("J-V: REV" in str(c) for c in cbs["on_status"].call_args_list)

        fp = save_pure_mpp_data(str(tmp_path), "P", [0.0, 1.0], [0.8, 0.8], [15.0, 15.0],
                                [12.0, 12.0], 0.07, 100.0, 1.0, 0,
                                initial_timing=tracker.initial_timing)
        meta = load_jv_file(fp)
        assert meta["delay_ms"] == 0                     # earlier columns unmoved
        assert meta["initial_scan_s"] == pytest.approx(tracker.initial_timing["rev_s"])

    def test_file_without_initial_scan_still_opens(self, tmp_path):
        fp = save_pure_mpp_data(str(tmp_path), "P", [0.0, 1.0], [0.8, 0.8], [15.0, 15.0],
                                [12.0, 12.0], 0.07, 100.0, 1.0, 0)
        assert load_jv_file(fp)["initial_scan_s"] is None


@pytest.mark.gui
def test_isos_d1_records_scan_times(jv_app, monkeypatch, tmp_path):
    app = jv_app
    app.disconnect_instrument()
    monkeypatch.setattr(app, "_ask_simulator_mode", lambda: "ideal_stable")
    app.open_simulator_dialog()
    app.isos_tracking_file = create_isos_d1_tracking_file(
        str(tmp_path), {"sample_name": "D1"})
    app.isos_export_folder_var.set(str(tmp_path))
    app.isos_sample_name_var.set("D1")
    app.isos_start_v_var.set(0.0)
    app.isos_end_v_var.set(1.05)
    app.isos_step_v_var.set(0.05)
    app.isos_dark_jv_var.set(False)
    app.isos_prebias_enabled_var.set(False)
    app.isos_ls_enabled_var.set(False)
    app.isos_detector_power_var.set(113.0)
    app.isos_detector_area_var.set(1.13)
    try:
        app.run_isos_d1_measurement()
        assert _log_column(app.isos_tracking_file, "Fwd_Scan_Time_s")[0] > 0
        backup = next(p for p in tmp_path.glob("D1_2*.xlsx")
                      if p != Path(app.isos_tracking_file))
        assert load_jv_file(str(backup))["total_time_s"] > 0
    finally:
        app.isos_tracking_file = None
        app.disconnect_instrument()
