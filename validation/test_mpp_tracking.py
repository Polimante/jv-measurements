"""Validation Test C: MPP tracking accuracy.

Two sub-tests:

Stable cell (ideal_stable DummyCell):
    Run MPPTracker for *n_cycles* JV+SPO cycles (SCPI mock, time.sleep
    patched to zero).  Collect all SPO PCE readings.  Verify that their
    mean is within 1 % of the analytically computed true MPP PCE.

Decaying cell (exponential_decay DummyCell, τ = 100 h):
    Simulate *n_decay_points* equally-spaced virtual time steps over
    *decay_span_min* minutes.  At each step, advance the cell's internal
    clock via ``set_time_offset`` and scan the cell directly (no tracker
    overhead).  Compute PCE_norm = PCE(t) / PCE(0).  Verify RMSE vs
    exp(−t/τ) < 3 %.
"""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np

from app.measurement.mpp_tracker import MPPTracker
from app.measurement.analysis import calculate_jv_parameters
from app.simulation.dummy_cell import DummyCell
from app.instrument.dummy_smu import DummySMU


# ── SCPI instrument wrapper (same pattern as tests/test_mpp_logic.py) ─────────

def _scpi_instrument(cell: DummyCell, area: float) -> MagicMock:
    """Wrap a DummyCell so MPPTracker's raw SCPI calls work correctly.

    Args:
        cell: Pre-constructed DummyCell.
        area: Cell area (cm²) — used to convert total current to density.

    Returns:
        MagicMock that responds to ``.write()`` and ``.query(":READ?")``.
    """
    inst = MagicMock()
    _v: list[float] = [0.0]

    def _write(cmd: str) -> None:
        if cmd.startswith(":SOUR:VOLT"):
            _v[0] = float(cmd.split()[-1])

    def _query(cmd: str) -> str:
        if cmd == ":READ?":
            j       = cell.current_density(_v[0])
            raw_a   = -(j / 1000.0) * area          # instrument convention: negated
            return f"{_v[0]:.6f},{raw_a:.10f},+0,+0,+0"
        return "0,0,0,0,0"

    inst.write.side_effect = _write
    inst.query.side_effect = _query
    return inst


def _true_mpp_pce(cell: DummyCell, v_start: float, v_end: float,
                  illumination: float) -> float:
    """Analytically compute PCE at the maximum power point.

    Args:
        cell: DummyCell to evaluate.
        v_start: Scan start voltage (V).
        v_end:   Scan end voltage (V).
        illumination: Incident power (mW/cm²).

    Returns:
        PCE (%) at the true MPP.
    """
    v_fine = np.linspace(v_start, v_end, 5000)
    j_fine = cell.scan(v_fine, scan_direction="reverse")
    power  = v_fine * j_fine
    pmpp   = float(np.max(power))
    return pmpp / illumination * 100.0


def _run_mpp_tracker_cycles(cell: DummyCell, cfg: dict, n_cycles: int,
                             n_spo_per_cycle: int) -> list[float]:
    """Run MPPTracker for *n_cycles* JV+SPO cycles; collect all SPO PCE values.

    Args:
        cell: Pre-constructed DummyCell (ideal_stable).
        cfg:  ``mpp_tracking`` sub-dict from config.
        n_cycles: Stop after this many JV+SPO cycles.
        n_spo_per_cycle: Stop the SPO phase after this many readings.

    Returns:
        List of PCE (%) values collected during all SPO phases.
    """
    area     = cfg["area"]
    pin      = cfg["pin"]
    v_start  = cfg["scan_v_start"]
    v_end    = cfg["scan_v_end"]
    v_step   = cfg["scan_v_step"]

    stop     = threading.Event()
    inst     = _scpi_instrument(cell, area)
    spo_pce_values: list[float] = []
    cycles_done = [0]

    callbacks = {
        "on_status":     MagicMock(),
        "on_log":        MagicMock(),
        "on_jv_update":  MagicMock(),
        "on_spo_update": MagicMock(),
        "on_complete":   MagicMock(),
    }

    def _on_spo(t, j, pce, cycle_n, stabilized):
        spo_pce_values.append(pce)
        # Stop the SPO phase after collecting enough readings
        spo_counts = sum(1 for _ in spo_pce_values if True)   # count all
        per_cycle  = n_spo_per_cycle
        if len(spo_pce_values) >= cycles_done[0] * per_cycle + per_cycle:
            # Trigger transition by checking cycle count
            pass
        # Stop tracking after n_cycles' worth of SPO readings
        if len(spo_pce_values) >= n_cycles * per_cycle:
            stop.set()

    callbacks["on_spo_update"].side_effect = _on_spo

    params = {
        "total_duration_min": 999.0,      # effectively unlimited
        "pin":               pin,
        "cell_area":         area,
        "start_v":           v_start,
        "end_v":             v_end,
        "step_v":            v_step,
        "include_forward":   False,
        "prebias_enabled":   False,
        "nplc":              1.0,
        "delay_ms":          0,
        "spo_duration_s":    float(cfg["spo_duration_s"]),
        "spo_interval_ms":   int(cfg["spo_interval_ms"]),
        "stab_threshold":    0.1,
    }

    tracker = MPPTracker(inst, params, callbacks, stop)
    with patch("app.measurement.mpp_tracker.time.sleep"):
        t = threading.Thread(target=tracker.run, daemon=True)
        t.start()
        t.join(timeout=60.0)

    return spo_pce_values


def _simulate_decay_tracking(cell: DummyCell, cfg_mpp: dict,
                              cfg_scan: dict, illumination: float,
                              rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Simulate MPP tracking on a decaying cell by stepping virtual time.

    At each time step, ``set_time_offset`` advances the cell's clock,
    then a full J-V scan is performed to find the current MPP.

    Args:
        cell: DummyCell with mode ``"exponential_decay"``.
        cfg_mpp: ``mpp_tracking`` sub-dict.
        cfg_scan: ``scan`` sub-dict.
        illumination: Incident power (mW/cm²).
        rng: Unused (here for API consistency).

    Returns:
        Tuple of (time_min, pce_norm_tracked, pce_norm_true) arrays.
    """
    n_pts     = cfg_mpp["n_decay_points"]
    span_min  = cfg_mpp["decay_span_min"]
    tau_h     = cfg_mpp["tau_h"]
    area      = cfg_mpp["area"]
    v_start   = cfg_scan["v_start"]
    v_end     = cfg_scan["v_end"]
    v_step    = cfg_scan["v_step"]
    stop      = threading.Event()

    time_min  = np.linspace(0.0, span_min, n_pts)
    pce_track = np.zeros(n_pts)

    for i, t_min in enumerate(time_min):
        t_s = t_min * 60.0
        cell.set_time_offset(t_s)

        smu = DummySMU(cell=cell, sleep_scale=0.0)
        smu.connect()
        smu.configure(nplc=1.0, delay_s=0.0, four_wire=False)

        v_arr, j_arr = smu.measure_scan(
            start=v_start, end=v_end, step=v_step,
            cell_area=area, stop_event=stop,
        )
        if v_arr is None:
            pce_track[i] = float("nan")
            continue

        power    = v_arr * j_arr
        idx_mpp  = int(np.argmax(power))
        pmpp     = float(power[idx_mpp])
        pce_track[i] = pmpp / illumination * 100.0

    # Normalize to t=0
    pce0 = pce_track[0]
    if pce0 > 0:
        pce_norm_track = pce_track / pce0
    else:
        pce_norm_track = np.ones(n_pts)

    tau_min       = tau_h * 60.0
    pce_norm_true = np.exp(-time_min / tau_min)

    return time_min, pce_norm_track, pce_norm_true


def run(config: dict) -> dict[str, Any]:
    """Run MPP tracking validation.

    Args:
        config: Top-level config dict loaded from config.yaml.

    Returns:
        Structured result dict.
    """
    rng      = np.random.default_rng(config["seed"] + 20)
    cfg_mpp  = config["mpp_tracking"]
    scan_cfg = config["scan"]
    illumination = scan_cfg["illumination"]

    # ── Sub-test 1: Stable cell ───────────────────────────────────────────────
    stable_cell = DummyCell(mode="ideal_stable")
    stable_cell.set_time_offset(0)

    true_mpp = _true_mpp_pce(stable_cell, cfg_mpp["scan_v_start"],
                              cfg_mpp["scan_v_end"], illumination)

    spo_pce = _run_mpp_tracker_cycles(
        stable_cell, cfg_mpp,
        n_cycles       = int(cfg_mpp["n_cycles"]),
        n_spo_per_cycle= int(cfg_mpp["n_spo_readings"]),
    )

    if spo_pce:
        mean_spo = float(np.mean(spo_pce))
        rel_err_stable = abs(mean_spo - true_mpp) / abs(true_mpp) if true_mpp else float("nan")
    else:
        mean_spo = float("nan")
        rel_err_stable = float("nan")

    thr_stable   = float(cfg_mpp["threshold_stable"])
    pass_stable  = np.isfinite(rel_err_stable) and rel_err_stable <= thr_stable

    # ── Sub-test 2: Decaying cell ─────────────────────────────────────────────
    tau_h        = float(cfg_mpp["tau_h"])
    decay_cell   = DummyCell(mode="exponential_decay", tau_h=tau_h)

    time_min, pce_norm_track, pce_norm_true = _simulate_decay_tracking(
        decay_cell, cfg_mpp, scan_cfg, illumination, rng
    )

    valid = np.isfinite(pce_norm_track)
    if valid.any():
        rmse_decay = float(np.sqrt(np.mean((pce_norm_track[valid] - pce_norm_true[valid]) ** 2)))
    else:
        rmse_decay = float("nan")

    thr_decay  = float(cfg_mpp["threshold_decay"])
    pass_decay = np.isfinite(rmse_decay) and rmse_decay <= thr_decay

    overall_pass = pass_stable and pass_decay

    return {
        "name":         "MPP Tracking Accuracy",
        "tau_h":        tau_h,
        "per_subtest_results": {
            "stable_cell": {
                "true_mpp_pce":   true_mpp,
                "mean_spo_pce":   mean_spo,
                "n_spo_readings": len(spo_pce),
                "rel_error":      rel_err_stable,
                "threshold":      thr_stable,
                "pass":           pass_stable,
            },
            "decaying_cell": {
                "rmse_pce_norm": rmse_decay,
                "threshold":     thr_decay,
                "pass":          pass_decay,
                "n_points":      int(valid.sum()),
            },
        },
        "overall_pass": overall_pass,
        "details": {
            "stable": {
                "spo_pce":      spo_pce,
                "true_mpp_pce": true_mpp,
                # time axis: one point per SPO reading, interval = spo_interval_ms
                "time_min":     [
                    i * cfg_mpp["spo_interval_ms"] / 1000.0 / 60.0
                    for i in range(len(spo_pce))
                ],
                "pce_tracked": spo_pce,
            },
            "decay": {
                "time_min":        time_min.tolist(),
                "pce_norm_tracked": pce_norm_track.tolist(),
                "pce_norm_true":    pce_norm_true.tolist(),
            },
        },
    }
