"""Unit tests for app.measurement.stability_metrics.

Synthetic PCE(t) curves with analytically known degradation behaviour exercise
T80/T95 crossing, burn-in (T_S) detection, the constant-PCE edge case, n_1000
extrapolation confidence, and graceful handling of a single data point.
"""

from __future__ import annotations

import math

import numpy as np

from app.measurement.stability_metrics import (
    StabilityMetrics,
    compute_stability_metrics,
)


def test_exponential_decay_t80_matches_analytical():
    rate = 0.002                       # per hour
    t = np.linspace(0, 500, 250)
    pce0 = 20.0
    pce = pce0 * np.exp(-rate * t)
    m = compute_stability_metrics(t, pce, pce_0=pce0)

    expected = -math.log(0.80) / rate  # ≈ 111.6 h
    assert m.t80_reached
    assert abs(m.t80_h - expected) / expected < 0.05


def test_exponential_decay_t95_before_t80():
    rate = 0.002
    t = np.linspace(0, 500, 250)
    pce = 20.0 * np.exp(-rate * t)
    m = compute_stability_metrics(t, pce, pce_0=20.0)
    assert m.t95_reached and m.t80_reached
    assert m.t95_h < m.t80_h


def test_burn_in_detection_lands_near_knee():
    # Sigmoidal burn-in: a smooth ~5% drop centred at t=20 h, then a slow
    # linear decline. The most-positive curvature sits near the knee.
    t = np.linspace(0, 200, 400)
    knee = 20.0
    burn = 1.0 / (1.0 + np.exp((t - knee) / 2.0))   # 1 → 0 step down
    pce = 20.0 * (0.95 + 0.05 * burn) - 0.005 * t
    m = compute_stability_metrics(t, pce, pce_0=pce[0], burn_in_mode="auto")

    assert m.burn_in_method == "auto"
    assert abs(m.t_s_h - knee) / knee < 0.30


def test_manual_burn_in_is_respected():
    t = np.linspace(0, 100, 100)
    pce = 18.0 - 0.01 * t
    m = compute_stability_metrics(t, pce, pce_0=18.0,
                                  burn_in_mode="manual", manual_burn_in_h=12.5)
    assert m.burn_in_method == "manual"
    assert m.t_s_h == 12.5


def test_constant_pce_never_reaches_t80():
    t = np.linspace(0, 200, 50)
    pce = np.full_like(t, 18.0)
    m = compute_stability_metrics(t, pce, pce_0=18.0)

    assert m.t80_h is None and m.t80_reached is False
    assert m.t95_h is None and m.t95_reached is False
    # Flat series extrapolates to ~baseline at 1000 h.
    assert m.n_1000 is not None
    assert abs(m.n_1000 - 1.0) < 0.02


def test_n1000_confidence_grows_with_extrapolation_distance():
    rate = 0.0005
    rng = np.random.default_rng(0)

    def _conf(t_end):
        t = np.linspace(0, t_end, 200)
        pce = 20.0 * np.exp(-rate * t) + rng.normal(0, 0.05, size=t.shape)
        return compute_stability_metrics(t, pce, pce_0=20.0).n_1000_confidence

    conf_short = _conf(100.0)
    conf_long  = _conf(900.0)
    assert conf_short is not None and conf_long is not None
    # Shorter experiment → longer extrapolation → larger (worse) confidence value.
    assert conf_short > conf_long


def test_single_data_point_returns_all_none():
    m = compute_stability_metrics([0.0], [18.0], pce_0=18.0)
    assert isinstance(m, StabilityMetrics)
    assert m.t80_h is None and m.t80_reached is False
    assert m.t_s_h is None and m.pce_s is None
    assert m.ts80_h is None and m.n_1000 is None


def test_empty_input_returns_all_none():
    m = compute_stability_metrics([], [], pce_0=18.0)
    assert m.t80_h is None and m.t_s_h is None


def test_ts80_measured_from_t_s():
    # Stable plateau then a clear drop after the (manual) burn-in point.
    t = np.linspace(0, 100, 201)
    pce = np.where(t < 10, 20.0, 20.0 * np.exp(-0.01 * (t - 10)))
    m = compute_stability_metrics(t, pce, pce_0=20.0,
                                  burn_in_mode="manual", manual_burn_in_h=10.0)
    assert m.pce_s is not None
    assert m.ts80_reached
    # PCE_S ≈ 20; 0.8·PCE_S reached when exp(-0.01·Δ)=0.8 → Δ ≈ 22.3 h from T_S.
    expected = -math.log(0.80) / 0.01
    assert abs(m.ts80_h - expected) / expected < 0.10


def test_compute_burn_in_false_omits_burn_in_but_keeps_t80_and_n1000():
    # Dark-storage style: T80 + n_1000 are meaningful, burn-in is not.
    rate = 0.002
    t = np.linspace(0, 500, 250)
    pce = 20.0 * np.exp(-rate * t)
    m = compute_stability_metrics(t, pce, pce_0=20.0,
                                  pce_column_used="PCE_rev",
                                  compute_burn_in=False)

    assert m.burn_in_method == "none"
    assert m.t_s_h is None
    assert m.pce_s is None
    assert m.ts80_h is None and m.ts80_reached is False
    # Light-independent metrics still computed.
    assert m.t80_reached
    expected = -math.log(0.80) / rate
    assert abs(m.t80_h - expected) / expected < 0.05
    assert m.n_1000 is not None and m.n_1000_extrapolated is True


def test_n1000_interpolated_when_run_exceeds_1000h():
    t = np.linspace(0, 1200, 300)
    pce = 20.0 * np.exp(-0.0002 * t)
    m = compute_stability_metrics(t, pce, pce_0=20.0)
    assert m.n_1000_extrapolated is False
    expected = math.exp(-0.0002 * 1000.0)
    assert abs(m.n_1000 - expected) < 0.01
