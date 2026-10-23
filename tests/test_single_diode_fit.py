"""Tests for app.measurement.analysis.fit_single_diode.

Strategy: generate synthetic J-V curves with DummyCell (known parameters),
fit with fit_single_diode, and verify recovery within stated tolerances.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.measurement.analysis import SingleDiodeFit, fit_single_diode
from app.simulation.dummy_cell import DummyCell

# Parameters chosen so that all 5 are recoverable within tight tolerances:
# moderate n (1.8), moderate Rs (3 Ω·cm²), moderate Rsh (2000 Ω·cm²),
# J0 large enough to be distinguishable (1e-10 mA/cm²).
_PARAMS = dict(j_ph=20.0, j0=1e-10, n=1.8, rs=3.0, rsh=2000.0)
_T = 298.15


def _make_curve(
    noise_std: float = 0.0,
    rng: np.random.Generator | None = None,
    n_pts: int = 80,
    v_start: float = 0.0,
    v_end: float = 1.25,
) -> tuple[np.ndarray, np.ndarray]:
    """Generate a reverse J-V scan (high→low voltage) from DummyCell."""
    mode = "noisy" if noise_std > 0 else "ideal_stable"
    cell = DummyCell(
        mode=mode,
        **_PARAMS,
        noise_std=noise_std,
        rng=rng if rng is not None else np.random.default_rng(0),
    )
    cell.set_time_offset(0.0)
    v = np.linspace(v_end, v_start, n_pts)
    j = cell.scan(v, scan_direction="reverse")
    return v, j


# ── Dataclass integrity ───────────────────────────────────────────────────────

class TestSingleDiodeFitDataclass:
    def test_fields_present(self):
        v, j = _make_curve()
        result = fit_single_diode(v, j, T=_T)
        assert isinstance(result, SingleDiodeFit)
        for field in ("parameters", "std_errors", "r_squared", "rmse",
                      "rmse_log", "converged", "method", "bounded_parameters",
                      "temperature_K", "timestamp"):
            assert hasattr(result, field), f"Missing field: {field}"

    def test_parameter_keys(self):
        v, j = _make_curve()
        result = fit_single_diode(v, j, T=_T)
        assert set(result.parameters.keys()) == {"J_ph", "J0", "n", "Rs", "Rsh"}
        assert set(result.std_errors.keys()) == {"J_ph", "J0", "n", "Rs", "Rsh"}

    def test_temperature_stored(self):
        v, j = _make_curve()
        result = fit_single_diode(v, j, T=310.0)
        assert result.temperature_K == pytest.approx(310.0)

    def test_method_string(self):
        v, j = _make_curve()
        result = fit_single_diode(v, j, T=_T)
        assert "brentq" in result.method


# ── Recovery on clean data ────────────────────────────────────────────────────

class TestParameterRecovery:
    def test_converges_on_clean_data(self):
        v, j = _make_curve()
        result = fit_single_diode(v, j, T=_T)
        assert result.converged

    def test_j_ph_within_1_percent_clean(self):
        v, j = _make_curve()
        result = fit_single_diode(v, j, T=_T)
        assert result.converged
        rel = abs(result.parameters["J_ph"] - _PARAMS["j_ph"]) / _PARAMS["j_ph"]
        assert rel < 0.01, f"J_ph rel error {rel:.2%}"

    def test_n_within_2_percent_clean(self):
        v, j = _make_curve()
        result = fit_single_diode(v, j, T=_T)
        assert result.converged
        rel = abs(result.parameters["n"] - _PARAMS["n"]) / _PARAMS["n"]
        assert rel < 0.02, f"n rel error {rel:.2%}"

    def test_rs_within_5_percent_clean(self):
        v, j = _make_curve()
        result = fit_single_diode(v, j, T=_T)
        assert result.converged
        rel = abs(result.parameters["Rs"] - _PARAMS["rs"]) / _PARAMS["rs"]
        assert rel < 0.05, f"Rs rel error {rel:.2%}"

    def test_rsh_within_5_percent_clean(self):
        v, j = _make_curve()
        result = fit_single_diode(v, j, T=_T)
        assert result.converged
        rel = abs(result.parameters["Rsh"] - _PARAMS["rsh"]) / _PARAMS["rsh"]
        assert rel < 0.05, f"Rsh rel error {rel:.2%}"

    def test_j0_within_order_of_magnitude(self):
        """J0 recovery within one order of magnitude (it is highly sensitive to n)."""
        v, j = _make_curve()
        result = fit_single_diode(v, j, T=_T)
        assert result.converged
        j0_got  = result.parameters["J0"]
        j0_true = _PARAMS["j0"]
        assert 0.1 * j0_true < j0_got < 10 * j0_true, \
            f"J0 {j0_got:.2e} not within 1 order of {j0_true:.2e}"


class TestRecoveryWithNoise:
    """1 % noise (noise_std ≈ 1% of Jsc=20) — loosen tolerances slightly."""

    def test_j_ph_within_5_percent_noisy(self):
        rng = np.random.default_rng(42)
        v, j = _make_curve(noise_std=0.20, rng=rng)
        result = fit_single_diode(v, j, T=_T)
        assert result.converged
        rel = abs(result.parameters["J_ph"] - _PARAMS["j_ph"]) / _PARAMS["j_ph"]
        assert rel < 0.05, f"J_ph rel error {rel:.2%} on noisy data"

    def test_n_within_5_percent_noisy(self):
        rng = np.random.default_rng(42)
        v, j = _make_curve(noise_std=0.20, rng=rng)
        result = fit_single_diode(v, j, T=_T)
        assert result.converged
        rel = abs(result.parameters["n"] - _PARAMS["n"]) / _PARAMS["n"]
        assert rel < 0.05, f"n rel error {rel:.2%} on noisy data"

    def test_rs_within_5_percent_noisy(self):
        rng = np.random.default_rng(42)
        v, j = _make_curve(noise_std=0.20, rng=rng)
        result = fit_single_diode(v, j, T=_T)
        assert result.converged
        rel = abs(result.parameters["Rs"] - _PARAMS["rs"]) / _PARAMS["rs"]
        assert rel < 0.05, f"Rs rel error {rel:.2%} on noisy data"


# ── Bounds behaviour ──────────────────────────────────────────────────────────

class TestBounds:
    def test_tight_n_bound_triggers_bounded_list(self):
        """Force n to hit a bound by setting a very narrow range."""
        v, j = _make_curve()
        tight_bounds = {
            "J_ph": (10.0, 30.0),
            "J0":   (1e-15, 1e-6),
            "n":    (1.5, 1.51),   # true n=1.8 is outside → will hit upper bound
            "Rs":   (0.01, 100.0),
            "Rsh":  (10.0, 1e8),
        }
        result = fit_single_diode(v, j, T=_T, bounds=tight_bounds)
        assert "n" in result.bounded_parameters, \
            f"Expected 'n' in bounded_parameters, got {result.bounded_parameters}"

    def test_custom_bounds_respected(self):
        """Result parameters must lie within the supplied bounds."""
        v, j = _make_curve()
        custom_bounds = {
            "J_ph": (15.0, 25.0),
            "J0":   (1e-15, 1e-6),
            "n":    (0.8, 5.0),
            "Rs":   (0.01, 100.0),
            "Rsh":  (10.0, 1e8),
        }
        result = fit_single_diode(v, j, T=_T, bounds=custom_bounds)
        for name in ("J_ph", "J0", "n", "Rs", "Rsh"):
            lo, hi = custom_bounds[name]
            got = result.parameters[name]
            assert lo <= got <= hi, \
                f"{name}={got:.4g} outside bounds [{lo}, {hi}]"

    def test_no_bounds_uses_defaults(self):
        """Omitting bounds should still produce a converged result."""
        v, j = _make_curve()
        result = fit_single_diode(v, j, T=_T, bounds=None)
        assert result.converged


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_high_rs_cell(self):
        """Cell with Rs=50 Ω·cm² — fit must converge and recover Rs within 15%."""
        cell = DummyCell(mode="ideal_stable",
                         j_ph=20.0, j0=1e-10, n=1.8, rs=50.0, rsh=2000.0)
        cell.set_time_offset(0.0)
        v = np.linspace(1.25, 0.0, 80)
        j = cell.scan(v, scan_direction="reverse")
        result = fit_single_diode(v, j, T=_T)
        assert result.converged
        rel = abs(result.parameters["Rs"] - 50.0) / 50.0
        assert rel < 0.15, f"Rs rel error {rel:.2%} for high-Rs cell"

    def test_near_ideal_low_rs(self):
        """Cell with Rs≈0 — fit converges and Rs lands near zero."""
        cell = DummyCell(mode="ideal_stable",
                         j_ph=20.0, j0=1e-10, n=1.8, rs=0.1, rsh=2000.0)
        cell.set_time_offset(0.0)
        v = np.linspace(1.25, 0.0, 80)
        j = cell.scan(v, scan_direction="reverse")
        result = fit_single_diode(v, j, T=_T)
        assert result.converged
        assert result.parameters["Rs"] < 2.0, \
            f"Rs={result.parameters['Rs']:.3f} should be near 0 for near-ideal cell"

    def test_flat_jv_no_crash(self):
        """A very flat / shunted J-V should not raise an exception."""
        v = np.linspace(1.0, 0.0, 40)
        # Constant current → no physically sensible Voc
        j = np.full_like(v, 10.0)
        try:
            result = fit_single_diode(v, j, T=_T)
            # If it "converges", result must at least be a SingleDiodeFit
            assert isinstance(result, SingleDiodeFit)
        except Exception as exc:
            pytest.fail(f"fit_single_diode raised on flat J-V: {exc}")


# ── Goodness-of-fit metrics ───────────────────────────────────────────────────

class TestMetrics:
    def test_r_squared_above_0999_clean(self):
        v, j = _make_curve()
        result = fit_single_diode(v, j, T=_T)
        assert result.converged
        assert result.r_squared > 0.999, \
            f"R²={result.r_squared:.6f} should be > 0.999 for clean data"

    def test_rmse_below_threshold_clean(self):
        v, j = _make_curve()
        result = fit_single_diode(v, j, T=_T)
        assert result.converged
        assert result.rmse < 0.05, \
            f"RMSE={result.rmse:.4f} mA/cm² should be < 0.05 for clean data"

    def test_rmse_log_below_threshold_clean(self):
        v, j = _make_curve()
        result = fit_single_diode(v, j, T=_T)
        assert result.converged
        assert result.rmse_log < 0.01, \
            f"RMSE_log={result.rmse_log:.4f} should be < 0.01 for clean data"


# ── Determinism ───────────────────────────────────────────────────────────────

class TestDeterminism:
    def test_same_input_same_output(self):
        """Two calls with identical data must produce identical results."""
        v, j = _make_curve()
        r1 = fit_single_diode(v, j, T=_T)
        r2 = fit_single_diode(v, j, T=_T)
        assert r1.parameters == pytest.approx(r2.parameters, rel=1e-9)
        assert r1.r_squared  == pytest.approx(r2.r_squared,  rel=1e-9)
        assert r1.rmse       == pytest.approx(r2.rmse,       rel=1e-9)

    def test_custom_initial_guess_accepted(self):
        """Passing a custom initial guess must not crash."""
        v, j = _make_curve()
        guess = {"J_ph": 20.0, "J0": 1e-10, "n": 1.8, "Rs": 3.0, "Rsh": 2000.0}
        result = fit_single_diode(v, j, T=_T, initial_guess=guess)
        assert isinstance(result, SingleDiodeFit)
