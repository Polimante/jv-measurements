"""Tests for app.simulation.dummy_cell.DummyCell."""

from __future__ import annotations

import numpy as np
import pytest

from app.simulation.dummy_cell import DummyCell, VALID_MODES


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_voc(cell: DummyCell, v_lo: float = 0.8, v_hi: float = 1.5) -> float:
    """Binary-search Voc (J = 0) in [v_lo, v_hi]."""
    from scipy.optimize import brentq
    return brentq(lambda v: cell.current_density(v), v_lo, v_hi, xtol=1e-6)


def _sweep(cell: DummyCell, n: int = 200) -> tuple[np.ndarray, np.ndarray]:
    """Reverse sweep from 1.4 V to 0 V."""
    v = np.linspace(1.4, 0.0, n)
    return v, cell.scan(v, scan_direction="reverse")


def _pce(cell: DummyCell, illumination: float = 100.0) -> float:
    """Rough PCE estimate (%) from a 200-point sweep."""
    v_arr, j_arr = _sweep(cell)
    power = v_arr * j_arr
    idx = np.argmax(power)
    voc = _find_voc(cell)
    jsc = cell.current_density(0.0)
    ff = power[idx] / (voc * jsc) if voc * jsc > 0 else 0.0
    return voc * jsc * ff / illumination * 100.0


# ── Basic physics (ideal_stable) ──────────────────────────────────────────────

class TestIdealStable:
    def test_jsc_within_half_percent_of_jph(self):
        cell = DummyCell(mode="ideal_stable")
        j_sc = cell.current_density(0.0)
        assert abs(j_sc - cell._j_ph) / cell._j_ph < 0.005

    def test_j_at_voc_within_1_micro_a(self):
        cell = DummyCell(mode="ideal_stable")
        voc = _find_voc(cell)
        j_at_voc = cell.current_density(voc)
        assert abs(j_at_voc) < 1e-3  # 1 µA/cm² = 0.001 mA/cm²

    def test_scalar_and_vectorised_match(self):
        cell = DummyCell(mode="ideal_stable")
        v_arr = np.linspace(0.0, 1.2, 50)
        j_scalar = np.array([cell.current_density(float(v)) for v in v_arr])
        j_vector = cell.scan(v_arr)
        np.testing.assert_allclose(j_scalar, j_vector, rtol=0.0, atol=0.0)

    def test_jv_monotonic_decreasing_in_power_quadrant(self):
        cell = DummyCell(mode="ideal_stable")
        voc = _find_voc(cell)
        v_arr = np.linspace(0.0, voc * 0.99, 100)
        j_arr = cell.scan(v_arr)
        assert np.all(np.diff(j_arr) < 0), "J-V not monotonic in power-generating quadrant"

    def test_pce_approx_20_percent(self):
        """Default parameters target ~19–21 % PCE."""
        pce = _pce(DummyCell(mode="ideal_stable"))
        assert 18.0 < pce < 22.0

    def test_positive_photocurrent_sign_convention(self):
        """Photocurrent must be positive at V=0 (CLAUDE.md)."""
        cell = DummyCell(mode="ideal_stable")
        assert cell.current_density(0.0) > 0.0


# ── All six modes instantiate and produce monotonic J-V ───────────────────────

@pytest.mark.parametrize("mode", sorted(VALID_MODES - {"noisy"}))
def test_all_modes_instantiate_and_monotonic(mode: str):
    # "noisy" is excluded: Gaussian noise dominates the tiny dJ per step at low
    # voltages (where the curve is nearly flat), making strict monotonicity
    # impossible without averaging many sweeps.
    cell = DummyCell(mode=mode, rng=np.random.default_rng(0))
    cell.set_time_offset(0.0)
    # Short scan 0 → 0.8 V stays safely within the power quadrant for all modes
    v_arr = np.linspace(0.0, 0.8, 50)
    j_arr = cell.scan(v_arr, scan_direction="reverse")
    assert np.all(np.diff(j_arr) < 0), f"Non-monotonic J-V for mode={mode!r}"


@pytest.mark.parametrize("mode", sorted(VALID_MODES))
def test_all_modes_positive_jsc(mode: str):
    cell = DummyCell(mode=mode, rng=np.random.default_rng(0))
    cell.set_time_offset(0.0)
    # noisy mode may have small noise; check mean over repeated calls
    values = [cell.current_density(0.0) for _ in range(5)]
    assert np.mean(values) > 0.0, f"Jsc not positive for mode={mode!r}"


# ── with_hysteresis ───────────────────────────────────────────────────────────

class TestHysteresis:
    def test_default_hi_approx_5_percent(self):
        cell = DummyCell(mode="with_hysteresis", hi_fraction=0.05)
        cell.set_time_offset(0.0)
        pce_rev = _pce_direction(cell, "reverse")
        pce_fwd = _pce_direction(cell, "forward")
        hi = (pce_rev - pce_fwd) / pce_rev * 100.0
        assert 3.0 < hi < 8.0, f"HI={hi:.1f}% outside expected 3–8% range"

    def test_custom_hi_fraction_higher(self):
        cell = DummyCell(mode="with_hysteresis", hi_fraction=0.20)
        cell.set_time_offset(0.0)
        pce_rev = _pce_direction(cell, "reverse")
        pce_fwd = _pce_direction(cell, "forward")
        hi = (pce_rev - pce_fwd) / pce_rev * 100.0
        assert hi > 10.0, f"High hi_fraction=0.20 should give HI > 10%, got {hi:.1f}%"

    def test_reverse_better_than_forward(self):
        cell = DummyCell(mode="with_hysteresis")
        cell.set_time_offset(0.0)
        pce_rev = _pce_direction(cell, "reverse")
        pce_fwd = _pce_direction(cell, "forward")
        assert pce_rev > pce_fwd


def _pce_direction(cell: DummyCell, direction: str) -> float:
    # High→low V sweep: J starts negative (past Voc) and ends positive (at 0 V).
    v_arr = np.linspace(1.4, 0.0, 200)
    j_arr = cell.scan(v_arr, scan_direction=direction)
    power = v_arr * j_arr
    idx = np.argmax(power)
    # Voc: any sign change (negative→positive as index increases for reverse sweep)
    idx_voc = np.where(np.diff(np.sign(j_arr)) != 0)[0]
    if len(idx_voc) == 0:
        return 0.0
    i0 = idx_voc[0]
    dj = j_arr[i0 + 1] - j_arr[i0]
    voc = v_arr[i0] - j_arr[i0] * (v_arr[i0 + 1] - v_arr[i0]) / dj if dj != 0 else v_arr[i0]
    jsc = cell.current_density(0.0, scan_direction=direction)
    ff = power[idx] / (voc * jsc) if voc * jsc > 0 else 0.0
    return voc * jsc * ff / 100.0 * 100.0


# ── exponential_decay ─────────────────────────────────────────────────────────

class TestExponentialDecay:
    def test_jsc_at_t0_matches_nominal(self):
        cell = DummyCell(mode="exponential_decay", tau_h=10.0)
        cell.set_time_offset(0.0)
        nominal = DummyCell(mode="ideal_stable").current_density(0.0)
        assert abs(cell.current_density(0.0) - nominal) / nominal < 0.01

    def test_jsc_at_tau_equals_nominal_over_e(self):
        tau_h = 2.0
        cell = DummyCell(mode="exponential_decay", tau_h=tau_h)
        cell.set_time_offset(tau_h * 3600.0)
        jsc_tau = cell.current_density(0.0)

        cell_ref = DummyCell(mode="exponential_decay", tau_h=tau_h)
        cell_ref.set_time_offset(0.0)
        jsc_0 = cell_ref.current_density(0.0)

        ratio = jsc_tau / jsc_0
        assert abs(ratio - 1.0 / np.e) < 0.001, f"ratio={ratio:.4f}, expected {1/np.e:.4f}"


# ── burn_in ───────────────────────────────────────────────────────────────────

class TestBurnIn:
    def test_jsc_starts_at_80_percent(self):
        cell = DummyCell(mode="burn_in")
        cell.set_time_offset(0.0)
        j0 = cell.current_density(0.0)
        nominal = DummyCell(mode="ideal_stable").current_density(0.0)
        # At t=0: scale = 1 - 0.2*exp(0) = 0.80
        assert abs(j0 / nominal - 0.80) < 0.01

    def test_jsc_rises_toward_nominal(self):
        # At t = 5×t_burn (= 150 min), exp(-5) ≈ 0.0067 → scale ≈ 0.9987 ≈ 100%
        cell = DummyCell(mode="burn_in")
        cell.set_time_offset(5 * 30 * 60)
        j_late = cell.current_density(0.0)
        nominal = DummyCell(mode="ideal_stable").current_density(0.0)
        assert abs(j_late / nominal - 1.0) < 0.02


# ── noisy ─────────────────────────────────────────────────────────────────────

class TestNoisy:
    def test_deterministic_with_fixed_seed(self):
        v_arr = np.linspace(0.0, 1.0, 30)
        cell1 = DummyCell(mode="noisy", rng=np.random.default_rng(42))
        cell1.set_time_offset(0.0)
        j1 = cell1.scan(v_arr)

        cell2 = DummyCell(mode="noisy", rng=np.random.default_rng(42))
        cell2.set_time_offset(0.0)
        j2 = cell2.scan(v_arr)

        np.testing.assert_array_equal(j1, j2)

    def test_mean_close_to_ideal(self):
        """Mean of 200 noisy readings at V=0 should be near J_ph."""
        rng = np.random.default_rng(7)
        cell = DummyCell(mode="noisy", noise_std=0.5, rng=rng)
        cell.set_time_offset(0.0)
        readings = [cell.current_density(0.0) for _ in range(200)]
        mean_j = np.mean(readings)
        nominal = DummyCell(mode="ideal_stable").current_density(0.0)
        assert abs(mean_j - nominal) / nominal < 0.05  # within 5% (3σ ≈ 4.2σ @ σ=0.5)


# ── catastrophic_failure ──────────────────────────────────────────────────────

class TestCatastrophicFailure:
    def test_before_failure_like_ideal(self):
        cell = DummyCell(mode="catastrophic_failure", t_fail_h=1.0)
        cell.set_time_offset(0.0)
        nominal = DummyCell(mode="ideal_stable").current_density(0.0)
        assert abs(cell.current_density(0.0) - nominal) / nominal < 0.01

    def test_after_failure_voc_drops(self):
        cell = DummyCell(mode="catastrophic_failure", t_fail_h=1.0)
        cell.set_time_offset(1.0 * 3600.0)  # exactly at failure time
        # Rsh drops by 1000× → massive shunt → Voc drops substantially
        voc_failed = _find_voc(cell, v_lo=0.01, v_hi=0.9)
        cell_ideal = DummyCell(mode="ideal_stable")
        voc_ideal = _find_voc(cell_ideal)
        assert voc_failed < voc_ideal * 0.5, (
            f"Voc after failure ({voc_failed:.3f} V) should be <50% of ideal ({voc_ideal:.3f} V)"
        )


# ── Custom parameters ─────────────────────────────────────────────────────────

class TestCustomParameters:
    def test_higher_jph_gives_higher_jsc(self):
        j_low = DummyCell(mode="ideal_stable", j_ph=15.0).current_density(0.0)
        j_high = DummyCell(mode="ideal_stable", j_ph=25.0).current_density(0.0)
        assert j_high > j_low

    def test_different_area_does_not_change_density(self):
        j_small = DummyCell(mode="ideal_stable", area=0.07).current_density(0.0)
        j_large = DummyCell(mode="ideal_stable", area=0.089).current_density(0.0)
        assert abs(j_small - j_large) < 0.001  # density is area-independent

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Unknown mode"):
            DummyCell(mode="not_a_mode")
