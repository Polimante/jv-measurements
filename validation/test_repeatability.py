"""Validation Test D: Measurement repeatability.

Protocol:
    1. Create a single ideal_stable DummyCell with default parameters.
    2. Measure it 20 times in succession (same settings, no noise).
    3. For each repeat: scan via DummySMU (sleep_scale=0), run
       ``calculate_jv_parameters``.
    4. Compute CV = SD / mean × 100 % for Voc, Jsc, FF, PCE.

Acceptance (CV thresholds):
    - Voc: CV < 1 %
    - Jsc: CV < 1 %
    - FF:  CV < 2 %
    - PCE: CV < 2 %

Note: With sleep_scale=0 and no injected noise, the DummySMU returns
deterministic values (brentq is deterministic given identical inputs).
Observed CV should be < 1e-10 % for all parameters — effectively zero.
This test exercises the pipeline for a real-hardware-like scenario and
documents the theoretical reproducibility floor.
"""

from __future__ import annotations

import threading
from typing import Any

import numpy as np

from app.instrument.dummy_smu import DummySMU
from app.measurement.analysis import calculate_jv_parameters
from app.simulation.dummy_cell import DummyCell


def run(config: dict) -> dict[str, Any]:
    """Run repeatability validation.

    Args:
        config: Top-level config dict loaded from config.yaml.

    Returns:
        Structured result dict.
    """
    cfg_rep  = config["repeatability"]
    scan_cfg = config["scan"]
    n_rep    = int(cfg_rep["n_repeats"])
    illumination = float(scan_cfg["illumination"])
    thresholds   = cfg_rep["thresholds"]   # {voc, jsc, ff, pce}

    # Fixed cell — same constructor args every run
    cell = DummyCell(mode="ideal_stable")
    cell.set_time_offset(0)

    param_lists: dict[str, list[float]] = {"Voc": [], "Jsc": [], "FF": [], "PCE": []}
    stop = threading.Event()

    for _ in range(n_rep):
        # Re-create SMU each repeat so no internal state bleeds through
        smu = DummySMU(cell=cell, sleep_scale=0.0)
        smu.connect()
        smu.configure(nplc=scan_cfg["nplc"], delay_s=scan_cfg["delay_s"],
                      four_wire=False)

        v_arr, j_arr = smu.measure_scan(
            start      = scan_cfg["v_start"],
            end        = scan_cfg["v_end"],
            step       = scan_cfg["v_step"],
            cell_area  = scan_cfg["cell_area"],
            stop_event = stop,
        )
        if v_arr is None:
            continue
        try:
            voc, jsc, ff, pce = calculate_jv_parameters(v_arr, j_arr, illumination)
        except ValueError:
            continue

        param_lists["Voc"].append(float(voc))
        param_lists["Jsc"].append(float(jsc))
        param_lists["FF"].append(float(ff))
        param_lists["PCE"].append(float(pce))

    param_results: dict[str, dict] = {}
    for param in ("Voc", "Jsc", "FF", "PCE"):
        vals = np.array(param_lists[param])
        if not len(vals):
            param_results[param] = {
                "mean": float("nan"), "sd": float("nan"),
                "cv":   float("nan"), "threshold": thresholds[param.lower()],
                "pass": False, "n": 0,
            }
            continue

        mean = float(np.mean(vals))
        sd   = float(np.std(vals, ddof=1)) if len(vals) > 1 else 0.0
        cv   = (sd / mean) if mean != 0 else float("nan")
        thr  = float(thresholds[param.lower()])

        param_results[param] = {
            "mean":      mean,
            "sd":        sd,
            "cv":        cv,
            "threshold": thr,
            "pass":      cv <= thr,
            "n":         int(len(vals)),
        }

    overall_pass = all(v.get("pass", False) for v in param_results.values())

    return {
        "name":           "Measurement Repeatability",
        "n_repeats":      n_rep,
        "param_results":  param_results,
        "overall_pass":   overall_pass,
        "error_metric":   {p: param_results[p].get("cv") for p in param_results},
        "threshold":      {p: param_results[p].get("threshold") for p in param_results},
        "pass_or_fail":   {p: param_results[p].get("pass") for p in param_results},
        "details":        {p: param_lists[p] for p in param_lists},
    }
