"""Tests for fit_light_ideality() and LightIdealityFit in analysis.py.

Covers:
  - Exact recovery of known n_idL from noiseless synthetic data
  - Approximate recovery with added noise (within 5%)
  - ValueError on fewer than 3 points
  - ValueError on identical intensities
  - UserWarning (not ValueError) on all-identical Voc values
  - Result is deterministic for identical inputs
  - Unit independence: multiplying all intensities by a constant leaves n_idL unchanged
"""

from __future__ import annotations

import math
import warnings

import numpy as np
import pytest

from app.measurement.analysis import LightIdealityFit, fit_light_ideality, _KB, _Q


# ── Synthetic data generator ───────────────────────────────────────────────────

_VT_298 = _KB * 298.15 / _Q   # ≈ 0.025693 V


def _synthetic_voc(
    intensities: np.ndarray,
    n_idL: float,
    voc_at_max: float = 1.0,
    T: float = 298.15,
) -> np.ndarray:
    """Voc(L) = n_idL * Vt * ln(L / max(L)) + voc_at_max (ideal linear model)."""
    vt      = _KB * T / _Q
    l_norm  = intensities / np.max(intensities)
    return n_idL * vt * np.log(l_norm) + voc_at_max


# ── Recovery tests ─────────────────────────────────────────────────────────────

class TestExactRecovery:
    def test_recovers_n_idL_1p5(self):
        """Five noiseless points should recover n_idL=1.5 to within 0.01%."""
        L    = np.array([0.1, 0.25, 0.5, 0.75, 1.0])
        n_ref = 1.5
        voc  = _synthetic_voc(L, n_ref)
        fit  = fit_light_ideality(L, voc)
        assert isinstance(fit, LightIdealityFit)
        assert abs(fit.n_idL - n_ref) / n_ref < 1e-4, (
            f"n_idL={fit.n_idL:.6f}, expected {n_ref}"
        )

    def test_recovers_n_idL_2p0(self):
        n_ref = 2.0
        L    = np.linspace(0.05, 1.0, 10)
        voc  = _synthetic_voc(L, n_ref)
        fit  = fit_light_ideality(L, voc)
        assert abs(fit.n_idL - n_ref) / n_ref < 1e-4

    def test_r_squared_unity_on_noiseless_data(self):
        L   = np.array([0.2, 0.4, 0.6, 0.8, 1.0])
        voc = _synthetic_voc(L, n_idL=1.8)
        fit = fit_light_ideality(L, voc)
        assert fit.r_squared > 0.99999

    def test_slope_equals_n_idL_times_Vt(self):
        n_ref = 1.3
        L    = np.array([0.1, 0.3, 0.5, 0.7, 1.0])
        voc  = _synthetic_voc(L, n_ref)
        fit  = fit_light_ideality(L, voc)
        vt   = _KB * 298.15 / _Q
        assert abs(fit.slope - n_ref * vt) / (n_ref * vt) < 1e-4

    def test_n_points_equals_input_length(self):
        L   = np.array([0.2, 0.5, 1.0])
        voc = _synthetic_voc(L, 1.5)
        fit = fit_light_ideality(L, voc)
        assert fit.n_points == 3

    def test_temperature_stored(self):
        T   = 310.0
        L   = np.array([0.2, 0.5, 1.0])
        voc = _synthetic_voc(L, 1.5, T=T)
        fit = fit_light_ideality(L, voc, T=T)
        assert abs(fit.temperature_K - T) < 1e-9

    def test_intensities_normalized_max_is_one(self):
        L   = np.array([0.1, 0.4, 0.9, 1.0])
        voc = _synthetic_voc(L, 1.5)
        fit = fit_light_ideality(L, voc)
        assert abs(float(np.max(fit.intensities_normalized)) - 1.0) < 1e-12

    def test_voc_values_echoed(self):
        L   = np.array([0.25, 0.5, 1.0])
        voc = _synthetic_voc(L, 1.5)
        fit = fit_light_ideality(L, voc)
        np.testing.assert_array_almost_equal(fit.voc_values, voc)


class TestNoisyRecovery:
    def test_recovers_n_idL_within_5_percent_with_small_noise(self):
        """0.5% Gaussian noise on Voc (realistic for lab conditions) gives n_idL within ±5%."""
        rng   = np.random.default_rng(42)
        n_ref = 1.7
        L     = np.linspace(0.05, 1.0, 20)
        voc   = _synthetic_voc(L, n_ref)
        # 0.5% noise relative to max Voc (~5 mV) is realistic Voc measurement precision
        noise = 0.005 * float(np.max(voc))
        voc   = voc + rng.normal(0, noise, size=len(L))
        fit   = fit_light_ideality(L, voc)
        assert abs(fit.n_idL - n_ref) / n_ref < 0.05, (
            f"n_idL={fit.n_idL:.4f}, expected ≈{n_ref}"
        )


# ── Unit independence ──────────────────────────────────────────────────────────

class TestUnitIndependence:
    def test_suns_vs_percent_same_result(self):
        """Scaling all intensities by 100 (suns→%) must not change n_idL."""
        L_suns = np.array([0.1, 0.25, 0.5, 0.75, 1.0])
        L_pct  = L_suns * 100.0
        n_ref  = 1.6
        voc    = _synthetic_voc(L_suns, n_ref)
        fit_suns = fit_light_ideality(L_suns, voc)
        fit_pct  = fit_light_ideality(L_pct,  voc)
        assert abs(fit_suns.n_idL - fit_pct.n_idL) < 1e-10

    def test_arbitrary_scale_factor(self):
        L      = np.array([0.2, 0.4, 0.8, 1.0])
        factor = 137.0
        n_ref  = 1.9
        voc    = _synthetic_voc(L, n_ref)
        fit_a  = fit_light_ideality(L,          voc)
        fit_b  = fit_light_ideality(L * factor, voc)
        assert abs(fit_a.n_idL - fit_b.n_idL) < 1e-10


# ── Determinism ────────────────────────────────────────────────────────────────

class TestDeterminism:
    def test_identical_inputs_identical_outputs(self):
        L   = np.array([0.1, 0.3, 0.6, 1.0])
        voc = _synthetic_voc(L, 1.5)
        f1  = fit_light_ideality(L, voc)
        f2  = fit_light_ideality(L, voc)
        assert f1.n_idL == f2.n_idL
        assert f1.r_squared == f2.r_squared


# ── Error cases ────────────────────────────────────────────────────────────────

class TestErrorCases:
    def test_fewer_than_3_points_raises(self):
        with pytest.raises(ValueError, match="at least 3"):
            fit_light_ideality(np.array([0.5, 1.0]), np.array([0.9, 1.0]))

    def test_single_point_raises(self):
        with pytest.raises(ValueError, match="at least 3"):
            fit_light_ideality(np.array([1.0]), np.array([1.0]))

    def test_all_identical_intensities_raises(self):
        with pytest.raises(ValueError, match="identical"):
            fit_light_ideality(
                np.array([1.0, 1.0, 1.0]),
                np.array([0.9, 0.91, 0.92]),
            )

    def test_mismatched_lengths_raises(self):
        with pytest.raises(ValueError):
            fit_light_ideality(
                np.array([0.5, 0.75, 1.0]),
                np.array([0.9, 1.0]),
            )

    def test_all_identical_voc_warns_not_raises(self):
        """Identical Voc → n_idL=0; should warn, not raise."""
        L   = np.array([0.2, 0.5, 1.0])
        voc = np.array([1.0, 1.0, 1.0])
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            fit = fit_light_ideality(L, voc)
        assert any(issubclass(warning.category, UserWarning) for warning in w)
        assert isinstance(fit, LightIdealityFit)
