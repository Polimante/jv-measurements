"""Validation Test E: ISOS-D-1 stability tracking simulation.

Two sub-tests:

Physics accuracy (240 virtual hours):
    Simulate 240 equally-spaced measurements (1 h apart) on a DummyCell
    with mode ``exponential_decay`` (τ = 100 h).  At each time step,
    advance the cell's virtual clock with ``set_time_offset``, perform
    forward + reverse J-V scans (DummySMU, sleep_scale=0), and compute
    PCE_norm = PCE(t) / PCE(0).  Compare to exp(−t/τ).
    Acceptance: RMSE of (PCE_norm_tracked − exp(−t/τ)) < 3 %.

File integrity (10 synthetic measurements):
    Create an ISOS-D-1 tracking workbook via ``create_isos_d1_tracking_file``,
    append 10 measurements with mocked timestamps (spaced 1 h apart), then
    read back with ``read_isos_d1_log`` and verify:
      - Correct row count
      - PCE_rev_norm of measurement k = PCE_rev_k / PCE_rev_0
      - Elapsed_h values match the mocked times
"""

from __future__ import annotations

import tempfile
import threading
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch

import numpy as np

from app.instrument.dummy_smu import DummySMU
from app.io.isos_tracking import (
    create_isos_d1_tracking_file,
    append_isos_d1_measurement,
    read_isos_d1_log,
)
from app.measurement.analysis import calculate_jv_parameters, calculate_resistances
from app.simulation.dummy_cell import DummyCell
import app.io.isos_tracking as _isos_mod


_BASE_TIME = datetime(2026, 1, 1, 0, 0, 0)


def _scan_and_analyse(cell: DummyCell, direction: str,
                      scan_cfg: dict, illumination: float) -> dict:
    """Perform one scan and return a dict of JV parameters.

    Args:
        cell: DummyCell (virtual clock already set externally).
        direction: ``"forward"`` or ``"reverse"``.
        scan_cfg: ``scan`` sub-dict.
        illumination: Incident power (mW/cm²).

    Returns:
        Dict with v_arr, j_arr, Voc, Jsc, FF, PCE, Rs, Rsh.
    """
    stop = threading.Event()
    smu  = DummySMU(cell=cell, sleep_scale=0.0)
    smu.connect()
    smu.configure(nplc=scan_cfg["nplc"], delay_s=scan_cfg["delay_s"], four_wire=False)

    if direction == "forward":
        start, end, step = scan_cfg["v_start"], scan_cfg["v_end"], scan_cfg["v_step"]
    else:
        start, end, step = scan_cfg["v_end"], scan_cfg["v_start"], -scan_cfg["v_step"]

    v_arr, j_arr = smu.measure_scan(
        start=start, end=end, step=step,
        cell_area=scan_cfg["cell_area"], stop_event=stop,
    )
    if v_arr is None:
        return {}

    # Sort so analysis functions receive a consistent order
    order  = np.argsort(v_arr)
    v_arr  = v_arr[order]
    j_arr  = j_arr[order]

    try:
        voc, jsc, ff, pce = calculate_jv_parameters(v_arr, j_arr, illumination)
        rs, rsh = calculate_resistances(v_arr, j_arr, voc)
    except (ValueError, Exception):
        return {}

    return {
        "v_arr": v_arr, "j_arr": j_arr,
        "Voc": voc, "Jsc": jsc, "FF": ff, "PCE": pce,
        "Rs": float(rs), "Rsh": float(rsh),
    }


def _physics_accuracy_test(tau_h: float, n_meas: int, interval_h: float,
                            scan_cfg: dict, illumination: float) -> dict:
    """Simulate 240 virtual hours and compare PCE_norm to exp(−t/τ).

    Args:
        tau_h: Decay time constant (hours).
        n_meas: Number of measurement points.
        interval_h: Interval between points (hours).
        scan_cfg: ``scan`` sub-dict.
        illumination: Incident power (mW/cm²).

    Returns:
        Dict with time_h, pce_norm_measured, pce_norm_true, rmse, pass.
    """
    cell = DummyCell(mode="exponential_decay", tau_h=tau_h)
    time_h       = np.array([i * interval_h for i in range(n_meas)])
    pce_tracked  = np.zeros(n_meas)

    for i, t_h in enumerate(time_h):
        cell.set_time_offset(t_h * 3600.0)
        res = _scan_and_analyse(cell, "reverse", scan_cfg, illumination)
        pce_tracked[i] = res.get("PCE", float("nan"))

    pce0 = pce_tracked[0]
    if pce0 > 0:
        pce_norm = pce_tracked / pce0
    else:
        pce_norm = np.ones(n_meas)

    pce_norm_true = np.exp(-time_h / tau_h)
    valid = np.isfinite(pce_norm)
    rmse  = float(np.sqrt(np.mean((pce_norm[valid] - pce_norm_true[valid]) ** 2)))

    return {
        "time_h":             time_h.tolist(),
        "pce_norm_measured":  pce_norm.tolist(),
        "pce_norm_true":      pce_norm_true.tolist(),
        "rmse":               rmse,
        "n_points":           int(valid.sum()),
    }


def _file_integrity_test(n_file_meas: int, tau_h: float,
                          sample_name: str, scan_cfg: dict,
                          illumination: float) -> dict:
    """Write n_file_meas measurements to a tracking file, then read back.

    Uses ``unittest.mock.patch`` to control ``datetime.now()`` inside
    ``isos_exporter`` so that elapsed_h values are deterministic.

    Args:
        n_file_meas: Number of measurements to append.
        tau_h: Decay constant — drives PCE values.
        sample_name: Written to the config sheet.
        scan_cfg: ``scan`` sub-dict.
        illumination: Incident power (mW/cm²).

    Returns:
        Dict with integrity check results.
    """
    cell = DummyCell(mode="exponential_decay", tau_h=tau_h)

    isos_config = {
        "sample_name":           sample_name,
        "cell_area":             scan_cfg["cell_area"],
        "detector_power":        illumination,
        "detector_area":         1.0,
        "start_voltage":         scan_cfg["v_start"],
        "end_voltage":           scan_cfg["v_end"],
        "step_voltage":          scan_cfg["v_step"],
        "delay_ms":              0.0,
        "nplc":                  scan_cfg["nplc"],
        "four_wire":             False,
        "prebias_enabled":       False,
        "prebias_voltage":       0.0,
        "prebias_duration":      0.0,
        "light_soaking_enabled": False,
        "light_soaking_duration": 0.0,
        "dark_jv":               False,
        "electrical_load":       "OC",
        "scan_rate_mode":        "advanced",
        "target_scan_rate":      50.0,
    }

    with tempfile.TemporaryDirectory() as tmp_dir:
        # Create tracking file with a mocked t0
        with patch.object(_isos_mod, "datetime") as mock_dt:
            mock_dt.now.return_value = _BASE_TIME
            mock_dt.strptime = datetime.strptime
            tracking_path = create_isos_d1_tracking_file(tmp_dir, isos_config)

        # Collect PCE values and append measurements
        pce_rev_values: list[float] = []
        elapsed_expected: list[float] = []

        for i in range(n_file_meas):
            t_h   = float(i)
            sim_t = _BASE_TIME + timedelta(hours=t_h)
            cell.set_time_offset(t_h * 3600.0)

            fwd = _scan_and_analyse(cell, "forward", scan_cfg, illumination)
            rev = _scan_and_analyse(cell, "reverse", scan_cfg, illumination)
            if not fwd or not rev:
                continue

            hi = ((rev["PCE"] - fwd["PCE"]) / rev["PCE"] * 100.0
                  if rev.get("PCE") else 0.0)

            meas = {
                "v_fwd": fwd["v_arr"].tolist(), "j_fwd": fwd["j_arr"].tolist(),
                "v_rev": rev["v_arr"].tolist(), "j_rev": rev["j_arr"].tolist(),
                "Voc_fwd": fwd["Voc"],  "Jsc_fwd": fwd["Jsc"],
                "FF_fwd":  fwd["FF"],   "PCE_fwd": fwd["PCE"],
                "Rs_fwd":  fwd["Rs"],   "Rsh_fwd": fwd["Rsh"],
                "Voc_rev": rev["Voc"],  "Jsc_rev": rev["Jsc"],
                "FF_rev":  rev["FF"],   "PCE_rev": rev["PCE"],
                "Rs_rev":  rev["Rs"],   "Rsh_rev": rev["Rsh"],
                "HI": hi, "illumination": illumination, "scan_rate": 50.0,
                "cell_area": scan_cfg["cell_area"],
                "delay_ms": 0.0, "nplc": scan_cfg["nplc"], "four_wire": False,
            }

            with patch.object(_isos_mod, "datetime") as mock_dt:
                mock_dt.now.return_value = sim_t
                mock_dt.strptime = datetime.strptime
                append_isos_d1_measurement(tracking_path, meas)

            pce_rev_values.append(rev["PCE"])
            elapsed_expected.append(t_h)

        # Read back and verify
        rows = read_isos_d1_log(tracking_path)
        n_rows = len(rows)

        # Check row count
        row_count_ok = n_rows == len(pce_rev_values)

        # Check PCE_rev_norm: ratio to first measurement
        norm_ok = True
        pce_rev_t0 = pce_rev_values[0] if pce_rev_values else 1.0
        for idx, row in enumerate(rows):
            expected_norm = round(pce_rev_values[idx] / pce_rev_t0, 4)
            stored_norm   = row.get("PCE_rev_norm", None)
            if stored_norm is None or abs(float(stored_norm) - expected_norm) > 0.002:
                norm_ok = False
                break

        # Check elapsed_h values (allow 0.01 h tolerance)
        elapsed_ok = True
        for idx, row in enumerate(rows):
            stored_h   = float(row.get("Elapsed_h", -999))
            expected_h = elapsed_expected[idx]
            if abs(stored_h - expected_h) > 0.01:
                elapsed_ok = False
                break

    return {
        "n_rows_expected": len(pce_rev_values),
        "n_rows_actual":   n_rows,
        "row_count_ok":    row_count_ok,
        "pce_norm_ok":     norm_ok,
        "elapsed_h_ok":    elapsed_ok,
        "integrity_pass":  row_count_ok and norm_ok and elapsed_ok,
    }


def run(config: dict) -> dict[str, Any]:
    """Run ISOS-D-1 simulation validation.

    Args:
        config: Top-level config dict loaded from config.yaml.

    Returns:
        Structured result dict.
    """
    cfg_isos   = config["isos_d1"]
    scan_cfg   = config["scan"]
    illumination = float(scan_cfg["illumination"])
    tau_h      = float(cfg_isos["tau_h"])
    n_meas     = int(cfg_isos["n_measurements"])
    interval_h = float(cfg_isos["interval_h"])
    threshold  = float(cfg_isos["threshold"])

    # ── Physics accuracy ──────────────────────────────────────────────────────
    physics = _physics_accuracy_test(tau_h, n_meas, interval_h, scan_cfg, illumination)
    rmse    = physics["rmse"]
    pass_phys = np.isfinite(rmse) and rmse <= threshold

    # ── File integrity ─────────────────────────────────────────────────────────
    integrity = _file_integrity_test(
        n_file_meas  = int(cfg_isos["n_file_integrity"]),
        tau_h        = tau_h,
        sample_name  = cfg_isos["sample_name"],
        scan_cfg     = scan_cfg,
        illumination = illumination,
    )
    pass_integrity = integrity["integrity_pass"]

    overall_pass = pass_phys and pass_integrity

    return {
        "name":    "ISOS-D-1 Stability Simulation",
        "tau_h":   tau_h,
        "per_subtest_results": {
            "physics_accuracy": {
                "rmse":       rmse,
                "threshold":  threshold,
                "pass":       pass_phys,
                "n_points":   physics["n_points"],
            },
            "file_integrity": {
                **integrity,
                "pass": pass_integrity,
            },
        },
        "overall_pass": overall_pass,
        "details": {
            "time_h":            physics["time_h"],
            "pce_norm_measured": physics["pce_norm_measured"],
            "pce_norm_true":     physics["pce_norm_true"],
        },
    }
