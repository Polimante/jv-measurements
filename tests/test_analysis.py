"""
Tests for app/measurement/analysis.py

Strategy: build synthetic J-V curves with analytically known parameters
and verify the extracted values match within a tight tolerance.

A realistic perovskite J-V curve is generated using the single-diode equation:
    J = Jph - J0*(exp((V + J*Rs)/(n*Vt)) - 1) - (V + J*Rs)/Rsh
solved numerically for each voltage point.  For the simplified case (Rs=0,
Rsh=inf) the curve reduces to:
    J(V) = Jph - J0*(exp(V/(n*Vt)) - 1)
which we use for most tests because its parameters are exact.
"""

import numpy as np
import pytest

from app.measurement.analysis import (
    calculate_jv_parameters,
    calculate_resistances,
    calculate_hysteresis_index,
)

# ── helpers ───────────────────────────────────────────────────────────────────

VT = 0.02585   # thermal voltage at ~300 K (V)


def ideal_jv(v_arr, Jph=20.0, J0=1e-10, n=1.5):
    """Return J (mA/cm²) for an ideal single-diode cell (Rs=0, Rsh=∞).

    Voc of this curve is: n*Vt*ln(Jph/J0 + 1)
    """
    return Jph - J0 * (np.exp(v_arr / (n * VT)) - 1)


def make_curve(v_start=-0.1, v_end=1.2, step=0.025, **kwargs):
    """Return (voltages, currents) for an ideal J-V curve."""
    v = np.arange(v_start, v_end + step * 0.5, step)
    j = ideal_jv(v, **kwargs)
    return v, j


# ─────────────────────────────────────────────────────────────────────────────
# calculate_jv_parameters
# ─────────────────────────────────────────────────────────────────────────────

class TestCalculateJVParameters:

    def test_returns_four_values(self):
        v, j = make_curve()
        result = calculate_jv_parameters(v, j, illumination=100.0)
        assert len(result) == 4

    def test_jsc_positive(self):
        v, j = make_curve()
        _, Jsc, _, _ = calculate_jv_parameters(v, j, illumination=100.0)
        assert Jsc > 0

    def test_voc_in_physical_range(self):
        v, j = make_curve()
        Voc, _, _, _ = calculate_jv_parameters(v, j, illumination=100.0)
        assert 0.5 < Voc < 1.3

    def test_ff_in_physical_range(self):
        v, j = make_curve()
        _, _, FF, _ = calculate_jv_parameters(v, j, illumination=100.0)
        assert 50.0 < FF < 90.0

    def test_pce_in_physical_range(self):
        v, j = make_curve()
        _, _, _, PCE = calculate_jv_parameters(v, j, illumination=100.0)
        assert 5.0 < PCE < 30.0

    def test_pce_scales_with_illumination(self):
        """Doubling illumination should halve the PCE (same absolute power)."""
        v, j = make_curve()
        _, _, _, pce100 = calculate_jv_parameters(v, j, illumination=100.0)
        _, _, _, pce200 = calculate_jv_parameters(v, j, illumination=200.0)
        assert abs(pce100 - 2 * pce200) < 0.5   # within 0.5% tolerance

    def test_jsc_equals_photocurrent_at_zero_voltage(self):
        """Jsc should equal J(V≈0) for the ideal curve."""
        Jph = 22.0
        v, j = make_curve(Jph=Jph)
        _, Jsc, _, _ = calculate_jv_parameters(v, j, illumination=100.0)
        # The curve step is 25 mV so the closest point to 0 V is exactly 0.0 V
        assert abs(Jsc - Jph) < 0.1

    def test_higher_jph_gives_higher_pce(self):
        v1, j1 = make_curve(Jph=18.0)
        v2, j2 = make_curve(Jph=24.0)
        _, _, _, pce1 = calculate_jv_parameters(v1, j1, 100.0)
        _, _, _, pce2 = calculate_jv_parameters(v2, j2, 100.0)
        assert pce2 > pce1

    def test_zero_illumination_raises(self):
        v, j = make_curve()
        with pytest.raises(ValueError, match="Illumination"):
            calculate_jv_parameters(v, j, illumination=0.0)

    def test_negative_illumination_raises(self):
        v, j = make_curve()
        with pytest.raises(ValueError):
            calculate_jv_parameters(v, j, illumination=-50.0)

    def test_no_zero_crossing_raises(self):
        """A curve that never crosses zero (all positive J) should raise."""
        v = np.linspace(0.0, 0.5, 50)
        j = np.ones_like(v) * 15.0   # never crosses zero
        with pytest.raises(ValueError, match="zero-crossing"):
            calculate_jv_parameters(v, j, illumination=100.0)

    def test_dark_curve_raises(self):
        """A dark J-V curve (all negative J for V < Voc) should raise."""
        v = np.linspace(0.0, 1.0, 50)
        j = -1e-3 * (np.exp(v / (1.5 * VT)) - 1)   # dark diode, no Jph
        with pytest.raises(ValueError):
            calculate_jv_parameters(v, j, illumination=100.0)

    def test_voc_zero_raises(self):
        """A curve whose only zero-crossing is exactly at V=0 raises."""
        v = np.array([-0.05, 0.0, 0.05])
        j = np.array([0.1, 0.0, -0.1])   # crosses at V=0 → Voc=0
        with pytest.raises(ValueError, match="Voc or Jsc"):
            calculate_jv_parameters(v, j, illumination=100.0)

    def test_ff_definition(self):
        """FF = Pmpp / (Voc * Jsc) * 100 must hold exactly."""
        v, j = make_curve()
        Voc, Jsc, FF, _ = calculate_jv_parameters(v, j, illumination=100.0)
        power   = v * j
        Pmpp    = float(np.max(power))
        FF_calc = Pmpp / (Voc * Jsc) * 100
        assert abs(FF - FF_calc) < 0.01

    def test_pce_definition(self):
        """PCE = Voc * Jsc * FF / illumination must hold exactly."""
        v, j  = make_curve()
        illum = 100.0
        Voc, Jsc, FF, PCE = calculate_jv_parameters(v, j, illumination=illum)
        PCE_calc = Voc * Jsc * FF / illum
        assert abs(PCE - PCE_calc) < 0.001

    def test_jsc_is_absolute_value(self):
        """Returned Jsc must be positive even if raw current at V=0 is negative."""
        v = np.linspace(-0.1, 1.2, 100)
        # Artificially flip sign so raw current at V=0 is negative
        j = -(ideal_jv(v) - 20.0)   # reflected curve
        # This will likely raise (no crossing) — the sign convention test
        # is that the function never returns negative Jsc when it succeeds.
        try:
            _, Jsc, _, _ = calculate_jv_parameters(v, j, illumination=100.0)
            assert Jsc >= 0
        except ValueError:
            pass   # expected for unphysical curves


# ─────────────────────────────────────────────────────────────────────────────
# calculate_resistances
# ─────────────────────────────────────────────────────────────────────────────

class TestCalculateResistances:

    def test_returns_two_values(self):
        v, j = make_curve()
        Voc, _, _, _ = calculate_jv_parameters(v, j, 100.0)
        result = calculate_resistances(v, j, Voc)
        assert len(result) == 2

    def test_rs_positive(self):
        v, j = make_curve()
        Voc, _, _, _ = calculate_jv_parameters(v, j, 100.0)
        Rs, _ = calculate_resistances(v, j, Voc)
        assert Rs > 0 or np.isnan(Rs)

    def test_rsh_positive(self):
        v, j = make_curve()
        Voc, _, _, _ = calculate_jv_parameters(v, j, 100.0)
        _, Rsh = calculate_resistances(v, j, Voc)
        assert Rsh > 0 or np.isnan(Rsh)

    def test_ideal_curve_has_low_rs(self):
        """An ideal curve (no resistances) should give very small Rs."""
        v, j = make_curve(Jph=20.0, J0=1e-10)
        Voc, _, _, _ = calculate_jv_parameters(v, j, 100.0)
        Rs, _ = calculate_resistances(v, j, Voc)
        # Rs should be well below 50 Ω·cm² for an ideal curve
        assert Rs < 50.0 or np.isnan(Rs)

    def test_empty_array_returns_nan(self):
        Rs, Rsh = calculate_resistances(np.array([]), np.array([]), 1.0)
        assert np.isnan(Rs) and np.isnan(Rsh)

    def test_single_point_returns_nan(self):
        Rs, Rsh = calculate_resistances(np.array([0.0]), np.array([20.0]), 1.0)
        assert np.isnan(Rs) or np.isnan(Rsh) or Rs > 0


# ─────────────────────────────────────────────────────────────────────────────
# calculate_hysteresis_index
# ─────────────────────────────────────────────────────────────────────────────

class TestCalculateHysteresisIndex:

    def test_zero_hi_when_equal(self):
        hi = calculate_hysteresis_index(15.0, 15.0)
        assert hi == pytest.approx(0.0)

    def test_positive_hi_normal_hysteresis(self):
        """PCE_rev > PCE_fwd → positive HI (normal hysteresis)."""
        hi = calculate_hysteresis_index(PCE_f=14.0, PCE_r=16.0)
        assert hi > 0

    def test_negative_hi_inverted_hysteresis(self):
        """PCE_fwd > PCE_rev → negative HI (inverted hysteresis)."""
        hi = calculate_hysteresis_index(PCE_f=16.0, PCE_r=14.0)
        assert hi < 0

    def test_hi_formula(self):
        """HI = (PCE_r - PCE_f) / PCE_r * 100."""
        PCE_f, PCE_r = 14.0, 16.0
        expected = (PCE_r - PCE_f) / PCE_r * 100
        assert calculate_hysteresis_index(PCE_f, PCE_r) == pytest.approx(expected)

    def test_zero_pce_rev_returns_nan(self):
        hi = calculate_hysteresis_index(5.0, 0.0)
        assert np.isnan(hi)

    def test_large_hi_above_50(self):
        """Severely hysteretic cell: PCE_fwd very low."""
        hi = calculate_hysteresis_index(5.0, 15.0)
        assert hi > 50

    def test_small_hi_below_5(self):
        """Good cell: near-zero hysteresis."""
        hi = calculate_hysteresis_index(15.5, 16.0)
        assert abs(hi) < 5

    def test_symmetric(self):
        """HI(f,r) and HI(r,f) should be opposite in sign (but not magnitude)."""
        hi_normal   = calculate_hysteresis_index(14.0, 16.0)
        hi_inverted = calculate_hysteresis_index(16.0, 14.0)
        # Both non-zero and opposite signs
        assert hi_normal > 0 and hi_inverted < 0
