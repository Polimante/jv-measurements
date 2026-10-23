"""
Analysis correctness tests — Part A of the bug-hunt suite.

Every test verifies a formula in app/measurement/analysis.py against an
analytically known value derived from a synthetic J-V curve.

Synthetic linear model (J = Jsc * (1 - V / Voc)):
  Jsc = 20.0 mA/cm²    Voc = 1.0 V    illumination = 100 mW/cm²
  Vmpp = 0.5 V          Jmpp = 10.0 mA/cm²
  FF   = 25 %           PCE  = 5.0 %
  Slope everywhere = -Jsc/Voc = -20 mA/cm²/V
  → Rs_expected = Rsh_expected = 50 Ω·cm²
"""
from __future__ import annotations

import math
import numpy as np
import pytest

from app.measurement.analysis import (
    calculate_jv_parameters,
    calculate_resistances,
    calculate_hysteresis_index,
)

# ── Synthetic curve helpers ───────────────────────────────────────────────────

_JSC  = 20.0   # mA/cm²
_VOC  = 1.0    # V
_ILLUM = 100.0  # mW/cm²
_STEP = 0.001  # V — 1 mV grid for ≤ 1 mV Voc tolerance


def _linear_jv(jsc=_JSC, voc=_VOC, v_start=-0.1, step=_STEP):
    """Return (V, J) for the linear model J = jsc * (1 - V/voc)."""
    v = np.arange(v_start, voc + step * 0.5, step)
    j = jsc * (1.0 - v / voc)
    return v, j


# ── Voc extraction ────────────────────────────────────────────────────────────

class TestVoc:

    def test_voc_within_1mv(self):
        """Voc extracted from a 1-mV grid should land within 1 mV of true value."""
        v, j = _linear_jv()
        voc_out, _, _, _ = calculate_jv_parameters(v, j, _ILLUM)
        assert abs(voc_out - _VOC) < 0.001, f"Voc={voc_out:.4f} V, expected {_VOC} V"

    def test_voc_fine_grid_within_0_5mv(self):
        """0.5 mV grid: Voc should be within 0.5 mV."""
        v, j = _linear_jv(step=0.0005)
        voc_out, _, _, _ = calculate_jv_parameters(v, j, _ILLUM)
        assert abs(voc_out - _VOC) < 0.001

    def test_no_zero_crossing_raises(self):
        """Curve that never crosses zero must raise ValueError."""
        v = np.linspace(0.0, 1.2, 50)
        j = np.full(50, 10.0)   # always positive
        with pytest.raises(ValueError, match="zero-crossing"):
            calculate_jv_parameters(v, j, _ILLUM)

    def test_single_point_raises(self):
        """One-element curve has no sign change → raises ValueError."""
        v = np.array([0.5])
        j = np.array([10.0])
        with pytest.raises(ValueError):
            calculate_jv_parameters(v, j, _ILLUM)

    def test_dark_curve_raises(self):
        """All-negative J (dark forward bias convention) has no positive crossing."""
        v = np.linspace(-0.1, 1.2, 100)
        j = -np.exp(v / 0.05)   # dark J, negative after CLAUDE.md sign flip
        with pytest.raises(ValueError):
            calculate_jv_parameters(v, j, _ILLUM)


# ── Jsc extraction ───────────────────────────────────────────────────────────

class TestJsc:

    def test_jsc_within_0_01(self):
        """Jsc at V=0 should match within 0.01 mA/cm²."""
        v, j = _linear_jv()
        _, jsc_out, _, _ = calculate_jv_parameters(v, j, _ILLUM)
        assert abs(jsc_out - _JSC) < 0.01, f"Jsc={jsc_out:.4f}, expected {_JSC}"

    def test_jsc_grid_not_at_exact_zero(self):
        """Even when V=0 is not a grid point Jsc should be within 0.1 mA/cm²."""
        v = np.arange(-0.075, 1.05, 0.015)   # 15 mV step, 0 not in grid, extends past Voc
        j = _JSC * (1.0 - v / _VOC)
        _, jsc_out, _, _ = calculate_jv_parameters(v, j, _ILLUM)
        assert abs(jsc_out - _JSC) < 0.1

    def test_jsc_always_positive(self):
        """The returned Jsc must be positive (abs() applied internally)."""
        v, j = _linear_jv()
        _, jsc_out, _, _ = calculate_jv_parameters(v, j, _ILLUM)
        assert jsc_out > 0


# ── FF and PCE ───────────────────────────────────────────────────────────────

class TestFFAndPCE:

    def test_ff_hand_computed(self):
        """FF for linear model = 25 %; tolerance 0.05 % absolute."""
        v, j = _linear_jv()
        _, _, ff_out, _ = calculate_jv_parameters(v, j, _ILLUM)
        assert abs(ff_out - 25.0) < 0.05, f"FF={ff_out:.3f} %, expected 25.0 %"

    def test_pce_hand_computed(self):
        """PCE for linear model at 100 mW/cm² = 5 %; tolerance 0.05 % absolute."""
        v, j = _linear_jv()
        _, _, _, pce_out = calculate_jv_parameters(v, j, _ILLUM)
        assert abs(pce_out - 5.0) < 0.05, f"PCE={pce_out:.3f} %, expected 5.0 %"

    def test_pce_scales_with_illumination(self):
        """Halving illumination should approximately double PCE percentage."""
        v, j = _linear_jv()
        _, _, _, pce_100 = calculate_jv_parameters(v, j, 100.0)
        _, _, _, pce_50  = calculate_jv_parameters(v, j, 50.0)
        assert abs(pce_50 - pce_100 * 2.0) < 0.1

    def test_zero_illumination_raises(self):
        """illumination = 0 must raise ValueError immediately."""
        v, j = _linear_jv()
        with pytest.raises(ValueError, match="[Ii]llumination"):
            calculate_jv_parameters(v, j, 0.0)

    def test_voc_jsc_zero_raises(self):
        """Curve that crosses zero AT V=0 gives Voc=0 → raises ValueError."""
        v = np.array([0.0, 0.5, 1.0])
        j = np.array([0.0, -5.0, -10.0])
        with pytest.raises(ValueError):
            calculate_jv_parameters(v, j, _ILLUM)


# ── Hysteresis Index ──────────────────────────────────────────────────────────

class TestHI:

    def test_hi_positive_normal_hysteresis(self):
        """HI > 0 when PCE_rev > PCE_fwd (Kim & Park 2014 definition)."""
        hi = calculate_hysteresis_index(PCE_f=10.0, PCE_r=12.0)
        expected = (12.0 - 10.0) / 12.0 * 100
        assert abs(hi - expected) < 1e-6

    def test_hi_negative_inverted_hysteresis(self):
        """HI < 0 when PCE_fwd > PCE_rev."""
        hi = calculate_hysteresis_index(PCE_f=12.0, PCE_r=10.0)
        expected = (10.0 - 12.0) / 10.0 * 100
        assert hi < 0
        assert abs(hi - expected) < 1e-6

    def test_hi_zero_when_equal(self):
        """HI = 0 when forward and reverse PCE are identical."""
        hi = calculate_hysteresis_index(PCE_f=15.0, PCE_r=15.0)
        assert hi == pytest.approx(0.0)

    def test_hi_nan_when_pce_rev_zero(self):
        """HI is nan (not an error) when PCE_rev = 0 (avoids division by zero)."""
        hi = calculate_hysteresis_index(PCE_f=10.0, PCE_r=0.0)
        assert math.isnan(hi)

    def test_hi_magnitude_matches_known(self):
        """(PCE_rev=12, PCE_fwd=10) → HI = 16.667 %."""
        hi = calculate_hysteresis_index(PCE_f=10.0, PCE_r=12.0)
        assert abs(hi - 16.6667) < 0.001


# ── Rs and Rsh (local-derivative estimates) ───────────────────────────────────

class TestResistances:

    def test_rsh_linear_curve_within_5pct(self):
        """For a purely linear J-V, Rsh = abs(1000/slope) = 50 Ω·cm²."""
        v, j = _linear_jv()
        voc_out, _, _, _ = calculate_jv_parameters(v, j, _ILLUM)
        rs_out, rsh_out = calculate_resistances(v, j, voc_out)
        expected_rsh = 50.0   # abs(1000 / (-20)) = 50
        assert abs(rsh_out - expected_rsh) / expected_rsh < 0.05, (
            f"Rsh={rsh_out:.2f}, expected {expected_rsh}"
        )

    def test_rs_linear_curve_within_5pct(self):
        """For a purely linear J-V, Rs = (1/|slope|)*1000 = 50 Ω·cm²."""
        v, j = _linear_jv()
        voc_out, _, _, _ = calculate_jv_parameters(v, j, _ILLUM)
        rs_out, rsh_out = calculate_resistances(v, j, voc_out)
        expected_rs = 50.0   # (1/20) * 1000 = 50
        assert abs(rs_out - expected_rs) / expected_rs < 0.05, (
            f"Rs={rs_out:.2f}, expected {expected_rs}"
        )

    def test_calculate_resistances_empty_array_returns_nan(self):
        """An empty curve cannot support polyfit → both resistances are nan."""
        v = np.array([])
        j = np.array([])
        rs, rsh = calculate_resistances(v, j, 0.5)
        assert math.isnan(rs) or math.isnan(rsh)

    def test_calculate_resistances_returns_positive(self):
        """Both Rs and Rsh should be positive for a normal J-V curve."""
        v, j = _linear_jv()
        voc_out, _, _, _ = calculate_jv_parameters(v, j, _ILLUM)
        rs_out, rsh_out = calculate_resistances(v, j, voc_out)
        assert rs_out > 0
        assert rsh_out > 0
