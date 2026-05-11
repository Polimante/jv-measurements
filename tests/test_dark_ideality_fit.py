"""Tests for fit_dark_ideality() in app/measurement/analysis.py.

Covers:
  - Recovery of known n_idd and J_s from a synthetic dark curve
  - Reduced R² when fit window includes the Rsh-dominated (low-V) region
  - ValueError when n_points < 5
  - Filtering of zero and negative |J| values
  - Automatic swap when v_min > v_max
  - Determinism: identical inputs produce identical outputs
"""

from __future__ import annotations

import math
import warnings

import numpy as np
import pytest

from app.measurement.analysis import fit_dark_ideality, DarkIdealityFit, _KB, _Q, _LN10


# ── Synthetic curve helpers ────────────────────────────────────────────────────

def _synthetic_dark_curve(
    v: np.ndarray,
    n_idd: float,
    J_s: float,
    T: float = 298.15,
    rsh: float = float("inf"),
) -> np.ndarray:
    """Return |J| for a synthetic dark diode curve (positive values).

    J = J_s * (exp(qV/(n*kT)) - 1) + V/Rsh
    """
    vt = _KB * T / _Q
    j_diode = J_s * (np.exp(v / (n_idd * vt)) - 1.0)
    j_shunt = v / rsh if not math.isinf(rsh) else np.zeros_like(v)
    return j_diode + j_shunt


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestFitRecovery:
    def test_fit_recovers_known_n_and_Js(self):
        """Wide exponential window recovers n_idd and J_s within 1% relative error."""
        T     = 298.15
        n_ref = 1.5
        Js_ref = 1e-12   # mA/cm²

        v = np.linspace(0.3, 1.1, 100)
        j = _synthetic_dark_curve(v, n_ref, Js_ref, T=T)

        result = fit_dark_ideality(v, j, v_min=0.4, v_max=1.0, T=T)

        assert isinstance(result, DarkIdealityFit)
        assert abs(result.n_idd - n_ref) / n_ref < 0.01, (
            f"n_idd={result.n_idd:.4f}, expected {n_ref}"
        )
        assert abs(result.J_s - Js_ref) / Js_ref < 0.01, (
            f"J_s={result.J_s:.3e}, expected {Js_ref:.3e}"
        )

    def test_fit_r_squared_close_to_1_in_exponential_region(self):
        """A clean synthetic exponential should give R² > 0.9999."""
        v = np.linspace(0.4, 1.0, 60)
        j = _synthetic_dark_curve(v, n_idd=1.8, J_s=5e-13, T=298.15)
        result = fit_dark_ideality(v, j, v_min=0.4, v_max=1.0)
        assert result.r_squared > 0.9999


class TestFitQualityWithShunt:
    def test_fit_quality_drops_when_window_includes_shunt_region(self):
        """Shunt-dominated window gives lower R² than the clean exponential window.

        Js=1e-7 mA/cm², Rsh=200 Ω·cm² → crossover near V=0.35 V.
        Window [0.0, 0.3] is Rsh-dominated: log|J| is not linear in V → poor R².
        Window [0.5, 0.9] is diode-dominated: log|J| is linear in V → good R².
        """
        T   = 298.15
        Rsh = 200.0   # Ω·cm²
        Js  = 1e-7    # mA/cm² — large enough to dominate above 0.4 V
        v   = np.linspace(0.01, 1.0, 100)   # avoid V=0 (J=0 there)
        j   = _synthetic_dark_curve(v, n_idd=1.5, J_s=Js, T=T, rsh=Rsh)

        # Shunt-dominated region: log|J| ≈ log(V/Rsh), not linear in V
        r_shunt = fit_dark_ideality(v, j, v_min=0.01, v_max=0.30, T=T)
        # Exponential region: log|J| is linear in V
        r_exp   = fit_dark_ideality(v, j, v_min=0.50, v_max=0.90, T=T)

        assert r_shunt.r_squared < r_exp.r_squared, (
            f"Expected shunt R²={r_shunt.r_squared:.4f} < exp R²={r_exp.r_squared:.4f}"
        )


class TestErrorHandling:
    def test_fit_window_too_small_raises(self):
        """Window containing < 5 positive points must raise ValueError."""
        v = np.linspace(0.0, 2.0, 100)
        j = _synthetic_dark_curve(v, n_idd=1.5, J_s=1e-12)
        # Extremely tight window — at most 1-2 points
        with pytest.raises(ValueError, match="at least 5 data points"):
            fit_dark_ideality(v, j, v_min=0.500, v_max=0.502)

    def test_fit_window_too_small_message_contains_counts(self):
        """Error message must include the actual point count."""
        v = np.array([0.0, 0.5, 1.0, 1.5, 2.0])
        j = np.array([1e-10, 1e-8, 1e-6, 1e-4, 1e-2])
        with pytest.raises(ValueError, match=r"\d+ points"):
            # Window with only 1 point
            fit_dark_ideality(v, j, v_min=0.49, v_max=0.51)

    def test_fit_with_zero_currents_filtered(self):
        """Zero J values are filtered (log undefined); n_points counts only positive |J|."""
        T = 298.15
        v_clean = np.linspace(0.4, 1.0, 30)
        j_clean = _synthetic_dark_curve(v_clean, n_idd=1.5, J_s=1e-12, T=T)

        # Inject 5 zeros inside the window — these must be excluded
        v_with_zeros = np.concatenate([v_clean, [0.45, 0.50, 0.55, 0.60, 0.65]])
        j_with_zeros = np.concatenate([j_clean, [0.0, 0.0, 0.0, 0.0, 0.0]])

        result = fit_dark_ideality(v_with_zeros, j_with_zeros, v_min=0.4, v_max=1.0, T=T)

        # Only the 30 clean points have |J| > 0
        assert result.n_points == len(v_clean), (
            f"Expected {len(v_clean)} points after filtering, got {result.n_points}"
        )
        assert result.r_squared > 0.999

    def test_fit_handles_negative_j_via_abs(self):
        """Negative J (dark current sign convention) is treated as |J| — same fit result."""
        T = 298.15
        v = np.linspace(0.4, 1.0, 30)
        j_pos = _synthetic_dark_curve(v, n_idd=1.5, J_s=1e-12, T=T)
        j_neg = -j_pos   # sign convention: dark forward-bias current is negative

        result_neg = fit_dark_ideality(v, j_neg, v_min=0.4, v_max=1.0, T=T)
        result_pos = fit_dark_ideality(v, j_pos, v_min=0.4, v_max=1.0, T=T)

        assert result_neg.n_idd    == pytest.approx(result_pos.n_idd,    rel=1e-6)
        assert result_neg.n_points == result_pos.n_points


class TestRobustness:
    def test_fit_window_swap_when_inverted(self):
        """v_min > v_max is silently swapped; fit succeeds."""
        T = 298.15
        v = np.linspace(0.3, 1.1, 80)
        j = _synthetic_dark_curve(v, n_idd=1.5, J_s=1e-12, T=T)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = fit_dark_ideality(v, j, v_min=1.0, v_max=0.4, T=T)

        # Should warn
        assert any("swap" in str(w.message).lower() for w in caught), \
            "Expected a swap warning when v_min > v_max"
        # After swap the window is [0.4, 1.0] — should fit fine
        assert result.r_squared > 0.999
        assert result.v_min < result.v_max

    def test_fit_determinism(self):
        """Same inputs twice produce byte-identical DarkIdealityFit."""
        T = 298.15
        v = np.linspace(0.4, 1.0, 50)
        j = _synthetic_dark_curve(v, n_idd=1.7, J_s=3e-13, T=T)

        r1 = fit_dark_ideality(v, j, v_min=0.4, v_max=1.0, T=T)
        r2 = fit_dark_ideality(v, j, v_min=0.4, v_max=1.0, T=T)

        assert r1.n_idd    == r2.n_idd
        assert r1.J_s      == r2.J_s
        assert r1.r_squared == r2.r_squared
        assert r1.rmse_log  == r2.rmse_log
        assert r1.n_points  == r2.n_points

    def test_result_is_dataclass_with_all_fields(self):
        """DarkIdealityFit must have all documented fields."""
        v = np.linspace(0.4, 1.0, 30)
        j = _synthetic_dark_curve(v, n_idd=1.5, J_s=1e-12)
        result = fit_dark_ideality(v, j, v_min=0.4, v_max=1.0)

        assert isinstance(result, DarkIdealityFit)
        for field_name in (
            "n_idd", "n_idd_err", "J_s", "J_s_err",
            "r_squared", "rmse_log", "v_min", "v_max",
            "n_points", "temperature_K", "method", "timestamp",
        ):
            assert hasattr(result, field_name), f"Missing field: {field_name}"

        assert result.method == "log_linear_regression"
        assert result.temperature_K == pytest.approx(298.15)
