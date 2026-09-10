"""
Shared fakes for the ContinuousISOSRunner test suite.

The runner drives the raw-SCPI instrument interface (``inst.write`` /
``inst.query``), exactly like the existing trackers, so these helpers provide a
SCPI-style fake backed by a :class:`DummyCell`, plus a deterministic virtual
clock so time-driven scheduling runs instantly.

This module is intentionally NOT named ``test_*`` so pytest does not collect it.
"""

from __future__ import annotations

from app.simulation.dummy_cell import DummyCell
# FakeKeithley now lives in production (app/simulation) so the live GUI can inject
# it as a simulated instrument. Re-exported here so the runner tests keep importing
# it from this helper module unchanged.
from app.simulation.fake_keithley import FakeKeithley  # noqa: F401


class VirtualClock:
    """Deterministic clock: ``sleep`` advances virtual time; ``time`` reads it."""

    def __init__(self, start: float = 0.0):
        self.t = float(start)

    def time(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        if seconds and seconds > 0:
            self.t += seconds


def make_runner_config(tmp_path, **overrides) -> dict:
    """Base config for a fast, deterministic continuous run.

    end_v is above the DummyCell's ~1.18 V Voc so the J-V curve has a real
    zero-crossing (calculate_jv_parameters requires one).
    """
    cfg = {
        "protocol_id":            "ISOS-L-1",
        "sample_name":            "L1Test",
        "folder":                 str(tmp_path),
        "total_duration_h":       0.05,    # 180 s
        "scan_interval_min":      0.6,     # 36 s  → 5 scans
        "load_mode":              "OC",
        "stress_with_light":      True,
        "start_v":                -0.05,
        "end_v":                  1.30,
        "step_v":                 0.05,
        "cell_area":              0.07,
        "pin":                    100.0,
        "delay_ms":               20.0,
        "nplc":                   1.0,
        "four_wire":              False,
        "v_min":                  0.0,
        "v_max":                  1.30,
        "perturb_step_mv":        10.0,
        "pre_scan_relaxation_s":  0.0,
        "spo_duration_s":         0.0,
        "stress_log_interval_s":  6.0,
        "clock":                  VirtualClock(),
    }
    cfg.update(overrides)
    return cfg


def make_v1_runner_config(tmp_path, **overrides) -> dict:
    """Base config for a fast, deterministic ISOS-V-1 (dark bias) run.

    Dark stress with periodic dark + illuminated J-V. Baseline / equilibration
    are off by default so the cell's light state is governed purely by the
    shutter callbacks. A wider dark scan range and the standard 0.5–0.8 V fit
    window let the dark ideality fit recover finite parameters.
    """
    cfg = make_runner_config(
        tmp_path,
        protocol_id="ISOS-V-1",
        sample_name="V1Test",
        stress_with_light=False,
        requires_dark_jv=True,
        load_mode="OC",
        baseline_n_scans=0,
        equilibration_duration_h=0.0,
        spo_duration_s=0.0,
        pre_scan_relaxation_s=0.0,
        log_i_dark_during_stress=True,
        dark_start_v=-0.2,
        dark_end_v=1.10,
        dark_step_v=0.025,
        dark_fit_v_min=0.5,
        dark_fit_v_max=0.8,
    )
    cfg.update(overrides)
    return cfg
