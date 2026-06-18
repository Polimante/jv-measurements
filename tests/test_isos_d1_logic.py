"""
ISOS-D-1 tracking logic tests — Part E of the bug-hunt suite.

Strategy:
  • Use DummyCell(mode="exponential_decay", tau_h=100) to generate realistic
    J-V data at known elapsed times.
  • Mock datetime.now() so elapsed_h values are deterministic.
  • Verify the ISOS_D1_Log sheet, config roundtrip, per-measurement sheets,
    and the PCE_norm column against the analytical exp(-t/100) decay.

No instrument or GUI required.
"""
from __future__ import annotations

import math
from datetime import datetime as _RealDatetime, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np
import openpyxl
import pytest

from app.io.isos_tracking import (
    create_isos_d1_tracking_file,
    append_isos_d1_measurement,
    read_isos_d1_config,
    read_isos_d1_log,
    read_isos_d1_jv_curves,
    _LOG_SHEET,
    _CFG_SHEET,
)
from app.measurement.analysis import (
    calculate_jv_parameters,
    calculate_resistances,
    calculate_hysteresis_index,
)
from app.simulation.dummy_cell import DummyCell


# ── Helpers ───────────────────────────────────────────────────────────────────

_ILLUM = 100.0
_AREA  = 0.07
_V_ARR = np.linspace(-0.05, 1.25, 150)

_SAMPLE_CONFIG = {
    "sample_name":          "ISOSTest",
    "cell_area":            _AREA,
    "detector_power":       110.0,
    "detector_area":        1.13,
    "start_voltage":        -0.05,
    "end_voltage":           1.25,
    "step_voltage":          0.01,
    "delay_ms":              100.0,
    "nplc":                  1.0,
    "four_wire":             False,
    "prebias_enabled":       False,
    "prebias_voltage":       0.0,
    "prebias_duration":      0.0,
    "light_soaking_enabled": False,
    "light_soaking_duration": 0.0,
    "dark_jv":               False,
    "electrical_load":       "open_circuit",
    "scan_rate_mode":        "advanced",
    "target_scan_rate":      100.0,
}

_T0 = _RealDatetime(2026, 1, 1, 0, 0, 0)


def _make_meas(cell: DummyCell, t_h: float) -> dict:
    """Return a meas dict from DummyCell at simulated elapsed time t_h hours."""
    cell.set_time_offset(t_h * 3600.0)
    j_rev = cell.scan(_V_ARR, scan_direction="reverse")
    j_fwd = cell.scan(_V_ARR, scan_direction="forward")
    try:
        Voc_r, Jsc_r, FF_r, PCE_r = calculate_jv_parameters(_V_ARR, j_rev, _ILLUM)
        Rs_r,  Rsh_r = calculate_resistances(_V_ARR, j_rev, Voc_r)
    except ValueError:
        Voc_r = Jsc_r = FF_r = PCE_r = Rs_r = Rsh_r = 0.0
    try:
        Voc_f, Jsc_f, FF_f, PCE_f = calculate_jv_parameters(_V_ARR, j_fwd, _ILLUM)
        Rs_f,  Rsh_f = calculate_resistances(_V_ARR, j_fwd, Voc_f)
    except ValueError:
        Voc_f = Jsc_f = FF_f = PCE_f = Rs_f = Rsh_f = 0.0
    HI = calculate_hysteresis_index(PCE_f, PCE_r)
    return {
        "v_fwd": _V_ARR, "j_fwd": j_fwd,
        "v_rev": _V_ARR, "j_rev": j_rev,
        "Voc_fwd": Voc_f, "Jsc_fwd": Jsc_f, "FF_fwd": FF_f, "PCE_fwd": PCE_f,
        "Rs_fwd":  Rs_f,  "Rsh_fwd": Rsh_f,
        "Voc_rev": Voc_r, "Jsc_rev": Jsc_r, "FF_rev": FF_r, "PCE_rev": PCE_r,
        "Rs_rev":  Rs_r,  "Rsh_rev": Rsh_r,
        "HI": HI if math.isfinite(HI) else 0.0,
        "illumination": _ILLUM,
        "scan_rate": 100.0,
        "cell_area": _AREA,
    }


def _build_tracking_file(tmp_path, n_points: int = 10, tau_h: float = 100.0,
                          times_h=None):
    """Create a tracking file with n_points measurements.

    Returns (path, cell, times_h_used, pce_rev_t0).
    datetime.now() is mocked so elapsed_h is deterministic.
    """
    if times_h is None:
        times_h = list(range(n_points))   # [0, 1, 2, ..., n-1] hours

    cell = DummyCell(mode="exponential_decay", tau_h=tau_h)

    # 2 datetime.now() calls inside create_isos_d1_tracking_file,
    # then 1 per append_isos_d1_measurement.
    mock_times = [_T0, _T0] + [_T0 + timedelta(hours=h) for h in times_h]
    measurements = [_make_meas(cell, h) for h in times_h]

    with patch("app.io.isos_tracking.datetime") as mock_dt:
        mock_dt.now.side_effect = mock_times
        mock_dt.strptime.side_effect = _RealDatetime.strptime
        mock_dt.now.return_value = _T0   # fallback
        fp = create_isos_d1_tracking_file(str(tmp_path), _SAMPLE_CONFIG)
        for meas in measurements:
            append_isos_d1_measurement(fp, meas)

    return fp, cell, times_h, measurements[0]["PCE_rev"]


# ── ISOS D-1 log structure ────────────────────────────────────────────────────

class TestIsosD1LogStructure:

    def test_10_measurements_creates_10_log_rows(self, tmp_path):
        """After 10 appends the log sheet must have exactly 10 data rows."""
        fp, *_ = _build_tracking_file(tmp_path, n_points=10)
        rows = read_isos_d1_log(fp)
        assert len(rows) == 10

    def test_measurement_numbers_sequential(self, tmp_path):
        fp, *_ = _build_tracking_file(tmp_path, n_points=5)
        rows = read_isos_d1_log(fp)
        numbers = [row["#"] for row in rows]
        assert numbers == list(range(1, 6))

    def test_elapsed_h_increases_monotonically(self, tmp_path):
        fp, *_ = _build_tracking_file(tmp_path, n_points=6)
        rows = read_isos_d1_log(fp)
        elapsed = [float(row["Elapsed_h"]) for row in rows]
        for i in range(1, len(elapsed)):
            assert elapsed[i] >= elapsed[i - 1], (
                f"elapsed_h not monotonic at index {i}: {elapsed}"
            )


# ── PCE normalization ─────────────────────────────────────────────────────────

class TestIsosD1PceNorm:

    def test_pce_norm_first_row_is_unity(self, tmp_path):
        """First measurement PCE_norm must be 1.0 (baseline)."""
        fp, *_ = _build_tracking_file(tmp_path, n_points=5)
        rows = read_isos_d1_log(fp)
        assert float(rows[0]["PCE_rev_norm"]) == pytest.approx(1.0, abs=1e-4)

    def test_pce_norm_follows_exponential_decay(self, tmp_path):
        """PCE_rev_norm at each row must be within 3 % of exp(-t_h/tau)."""
        tau_h = 100.0
        times_h = [0, 2, 5, 8, 10]
        fp, *_ = _build_tracking_file(tmp_path, n_points=5, tau_h=tau_h,
                                      times_h=times_h)
        rows = read_isos_d1_log(fp)
        for i, (row, t_h) in enumerate(zip(rows, times_h)):
            expected_norm = math.exp(-t_h / tau_h)
            actual_norm   = float(row["PCE_rev_norm"])
            assert abs(actual_norm - expected_norm) < 0.03, (
                f"Row {i}: PCE_rev_norm={actual_norm:.4f}, "
                f"expected exp(-{t_h}/{tau_h})={expected_norm:.4f}"
            )

    def test_pce_fwd_norm_first_row_unity(self, tmp_path):
        fp, *_ = _build_tracking_file(tmp_path, n_points=3)
        rows = read_isos_d1_log(fp)
        assert float(rows[0]["PCE_fwd_norm"]) == pytest.approx(1.0, abs=1e-4)


# ── Config sheet ──────────────────────────────────────────────────────────────

class TestIsosD1Config:

    def test_config_preserved_on_load(self, tmp_path):
        fp, *_ = _build_tracking_file(tmp_path, n_points=2)
        cfg = read_isos_d1_config(fp)
        assert cfg.get("sample_name") == "ISOSTest"
        assert float(cfg.get("cell_area")) == pytest.approx(_AREA, abs=1e-6)

    def test_t0_timestamp_stored(self, tmp_path):
        fp, *_ = _build_tracking_file(tmp_path, n_points=2)
        cfg = read_isos_d1_config(fp)
        assert "t0_timestamp" in cfg
        t0_str = cfg["t0_timestamp"]
        # Must be parseable as datetime
        parsed = _RealDatetime.strptime(str(t0_str), "%Y-%m-%d %H:%M:%S")
        assert parsed is not None


# ── Per-measurement JV sheets ─────────────────────────────────────────────────

class TestIsosD1JVSheets:

    def test_per_measurement_sheets_exist(self, tmp_path):
        """A JV_#NNN_... sheet must exist for each measurement."""
        n = 5
        fp, *_ = _build_tracking_file(tmp_path, n_points=n)
        wb = openpyxl.load_workbook(fp)
        jv_sheets = [s for s in wb.sheetnames if s.startswith("JV_#")]
        assert len(jv_sheets) == n

    def test_jv_sheet_contains_v_arrays(self, tmp_path):
        """First JV sheet must have voltage values starting at row 3."""
        fp, *_ = _build_tracking_file(tmp_path, n_points=3)
        wb = openpyxl.load_workbook(fp)
        jv_sheets = sorted(s for s in wb.sheetnames if s.startswith("JV_#"))
        ws = wb[jv_sheets[0]]
        first_v = ws.cell(3, 1).value   # forward voltage, row 3
        assert first_v is not None
        assert abs(float(first_v) - float(_V_ARR[0])) < 1e-4


# ── Stop mid-experiment (atomic appends) ─────────────────────────────────────

class TestIsosD1AtomicAppend:

    def test_stop_after_5_leaves_exactly_5_rows(self, tmp_path):
        """Appending 5 of a planned 10 measurements leaves 5 complete rows."""
        fp, *_ = _build_tracking_file(tmp_path, n_points=5)
        rows = read_isos_d1_log(fp)
        assert len(rows) == 5

    def test_partial_experiment_file_is_readable(self, tmp_path):
        """A file written with only 3 measurements must be fully readable."""
        fp, *_ = _build_tracking_file(tmp_path, n_points=3)
        cfg  = read_isos_d1_config(fp)
        rows = read_isos_d1_log(fp)
        assert cfg  # non-empty
        assert len(rows) == 3
