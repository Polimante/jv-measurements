"""Validation Test B: Single-diode model fitting accuracy.

Protocol:
    1. Sample 50 random perovskite cells (same distributions as Test A).
    2. For each of 5 noise levels (0 %, 0.5 %, 1 %, 2 %, 5 % of Jsc):
       a. Scan the cell with DummySMU (sleep_scale=0) using a 10 mV step.
       b. Add Gaussian noise to J (σ = noise_level × Jsc_gt).
       c. Run ``fit_single_diode`` on the noisy curve.
       d. Compute relative error for each parameter.
    3. Report mean ± SD of relative error per parameter and noise level.
    4. Collect Bland-Altman data.

Acceptance:
    At 1 % noise, mean relative error < 5 % for J_ph, n, Rs.
    J0 and Rsh use log-relative error (|log10(measured/true)|) with
    threshold < 0.30 log10-units (≈ factor 2 at most), because they span
    orders of magnitude and the optimizer works in log10 space internally.
"""

from __future__ import annotations

import threading
from typing import Any

import numpy as np

from app.instrument.dummy_smu import DummySMU
from app.measurement.analysis import fit_single_diode
from app.simulation.dummy_cell import DummyCell
from validation.test_jv_extraction import _sample_cells


# Parameters fitted in log10 space — different error metric
_LOG_PARAMS: frozenset[str] = frozenset({"J0", "Rsh"})


def _scan_for_fit(cell_params: dict, noise_fraction: float,
                  rng: np.random.Generator, cfg: dict) -> tuple[np.ndarray, np.ndarray]:
    """Scan a cell on the coarser fitting grid and add Gaussian noise.

    Args:
        cell_params: DummyCell constructor kwargs.
        noise_fraction: σ = noise_fraction × Jsc added to every J point.
        rng: Seeded Generator for noise.
        cfg: Top-level config dict.

    Returns:
        (V_arr, J_arr) sorted from v_start to v_end (forward direction).
    """
    scan_cfg = cfg["scan"]
    stop     = threading.Event()
    cell     = DummyCell(mode="ideal_stable", **cell_params)
    smu      = DummySMU(cell=cell, sleep_scale=0.0)
    smu.connect()
    smu.configure(nplc=scan_cfg["nplc"], delay_s=scan_cfg["delay_s"], four_wire=False)

    # Use the coarser fitting grid for speed
    v_step = scan_cfg["v_step_fit"]
    v_arr, j_arr = smu.measure_scan(
        start      = scan_cfg["v_start"],
        end        = scan_cfg["v_end"],
        step       = v_step,
        cell_area  = scan_cfg["cell_area"],
        stop_event = stop,
    )
    if v_arr is None:
        return np.array([]), np.array([])

    if noise_fraction > 0:
        idx_sc     = int(np.argmin(np.abs(v_arr)))
        jsc_approx = float(abs(j_arr[idx_sc]))
        sigma      = noise_fraction * jsc_approx
        j_arr      = j_arr + rng.normal(0.0, sigma, size=j_arr.shape)

    # Clip J so curve_fit doesn't see impossible values
    j_arr = np.clip(j_arr, -100.0, 100.0)
    return v_arr, j_arr


def _rel_error(true_val: float, fitted_val: float, log_space: bool) -> float:
    """Compute relative error, using log10 ratio for log-space parameters.

    Args:
        true_val: Ground-truth parameter value.
        fitted_val: Fitted parameter value.
        log_space: If True, returns |log10(fitted/true)|.

    Returns:
        Non-negative relative error (dimensionless).
    """
    if log_space:
        if true_val > 0 and fitted_val > 0:
            return abs(np.log10(fitted_val / true_val))
        return float("nan")
    if true_val == 0:
        return float("nan")
    return abs(fitted_val - true_val) / abs(true_val)


def run(config: dict) -> dict[str, Any]:
    """Run single-diode model fitting validation.

    Args:
        config: Top-level config dict loaded from config.yaml.

    Returns:
        Structured result dict.
    """
    rng      = np.random.default_rng(config["seed"] + 10)
    cfg_test = config["single_diode_fit"]
    n_cells  = cfg_test["n_cells"]
    noise_levels: list[float] = cfg_test["noise_levels"]
    # Per-parameter thresholds (evaluated at 1 % noise only)
    thr_per_param: dict[str, float] = {
        k: float(v) for k, v in cfg_test["thresholds_1pct_noise"].items()
    }

    cell_params_list = _sample_cells(n_cells, rng, config)
    params_all = ["J_ph", "J0", "n", "Rs", "Rsh"]

    per_noise_results: dict[float, dict] = {}
    all_details: dict[float, dict]       = {}

    for noise_level in noise_levels:
        # Per-cell records for Bland-Altman
        cell_records: list[dict] = []
        errors: dict[str, list[float]] = {p: [] for p in params_all}

        for cp in cell_params_list:
            # Ground-truth parameters ARE the DummyCell constructor args
            gt_params = {
                "J_ph": cp["j_ph"],
                "J0":   cp["j0"],
                "n":    cp["n"],
                "Rs":   cp["rs"],
                "Rsh":  cp["rsh"],
            }

            v_arr, j_arr = _scan_for_fit(cp, noise_level, rng, config)
            if not len(v_arr):
                continue

            fit = fit_single_diode(v_arr, j_arr, T=cp["T"])
            if not fit.converged:
                continue

            cell_gt   = {}
            cell_meas = {}
            for param in params_all:
                gt_val  = gt_params[param]
                fit_val = fit.parameters.get(param, float("nan"))
                cell_gt[param]   = gt_val
                cell_meas[param] = fit_val

                use_log = param in _LOG_PARAMS
                err = _rel_error(gt_val, fit_val, log_space=use_log)
                if np.isfinite(err):
                    errors[param].append(err)

            cell_records.append({
                "ground_truth": cell_gt,
                "measured":     cell_meas,
                "converged":    fit.converged,
                "r_squared":    fit.r_squared,
            })

        # Compute summary statistics per parameter
        param_results: dict[str, dict] = {}
        for param in params_all:
            errs = [e for e in errors[param] if np.isfinite(e)]
            thr  = thr_per_param.get(param, 1.0)
            if not errs:
                param_results[param] = {
                    "mean_rel_error": float("nan"),
                    "sd_rel_error":   float("nan"),
                    "threshold":      thr,
                    "pass":           False,
                    "n_converged":    0,
                    "log_space":      param in _LOG_PARAMS,
                }
                continue

            mean_e = float(np.mean(errs))
            sd_e   = float(np.std(errs, ddof=1)) if len(errs) > 1 else 0.0

            # Pass criterion only evaluated at 1 % noise level
            if abs(noise_level - 0.01) < 1e-9:
                passes = mean_e <= thr
            else:
                passes = True   # acceptance criterion defined only at 1 % noise

            param_results[param] = {
                "mean_rel_error": mean_e,
                "sd_rel_error":   sd_e,
                "threshold":      thr,
                "pass":           passes,
                "n_converged":    len(errs),
                "log_space":      param in _LOG_PARAMS,
                "n":              len(errs),
            }

        per_noise_results[noise_level] = param_results
        all_details[noise_level] = {"cells": cell_records}

    # Overall pass: all parameters pass at 1 % noise
    noise_1pct_results = per_noise_results.get(0.01, {})
    overall_pass = all(
        v.get("pass", False)
        for v in noise_1pct_results.values()
        if isinstance(v, dict)
    )

    return {
        "name":                "Single-Diode Model Fitting",
        "n_cells":             n_cells,
        "noise_levels_tested": noise_levels,
        "per_noise_results":   per_noise_results,
        "overall_pass":        overall_pass,
        "details":             all_details,
    }
