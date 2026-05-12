"""
Input validation tests — Part F of the bug-hunt suite.

Tests call the pure functions in app/gui/input_validation.py directly
without launching any Tkinter window.  Each test asserts on the error
code (stable string) and checks that parsed_value is None on error or a
correctly-typed float on success.
"""
from __future__ import annotations

import pytest

from app.gui.input_validation import (
    validate_cell_area,
    validate_delay_ms,
    validate_nplc,
    validate_voltage_range,
    validate_illumination,
    validate_filename,
    validate_scan_rate,
    validate_threshold_range,
)


# ── Cell area ─────────────────────────────────────────────────────────────────

class TestValidateCellArea:

    def test_empty_returns_area_required(self):
        res = validate_cell_area("")
        assert res.error is not None
        assert res.error.code == "area_required"
        assert res.parsed_value is None

    def test_whitespace_only_treated_as_empty(self):
        res = validate_cell_area("   ")
        assert res.error.code == "area_required"

    def test_negative_returns_area_must_be_positive(self):
        res = validate_cell_area("-0.07")
        assert res.error is not None
        assert res.error.code == "area_must_be_positive"
        assert res.parsed_value is None

    def test_zero_returns_area_must_be_positive(self):
        res = validate_cell_area("0")
        assert res.error.code == "area_must_be_positive"

    def test_nonnumeric_returns_area_not_a_number(self):
        res = validate_cell_area("abc")
        assert res.error is not None
        assert res.error.code == "area_not_a_number"
        assert res.parsed_value is None

    def test_valid_area_no_error(self):
        res = validate_cell_area("0.07")
        assert res.error is None
        assert res.parsed_value == pytest.approx(0.07)

    def test_valid_area_large(self):
        res = validate_cell_area("100")
        assert res.error is None
        assert res.parsed_value == pytest.approx(100.0)


# ── Delay ─────────────────────────────────────────────────────────────────────

class TestValidateDelayMs:

    def test_empty_returns_zero(self):
        res = validate_delay_ms("")
        assert res.error is None
        assert res.parsed_value == pytest.approx(0.0)

    def test_negative_returns_delay_negative(self):
        res = validate_delay_ms("-1")
        assert res.error is not None
        assert res.error.code == "delay_negative"
        assert res.parsed_value is None

    def test_zero_delay_valid(self):
        res = validate_delay_ms("0")
        assert res.error is None
        assert res.parsed_value == pytest.approx(0.0)

    def test_valid_delay_returns_value(self):
        res = validate_delay_ms("100")
        assert res.error is None
        assert res.parsed_value == pytest.approx(100.0)


# ── NPLC ─────────────────────────────────────────────────────────────────────

class TestValidateNplc:

    def test_below_min_returns_out_of_range(self):
        res = validate_nplc("0.005")
        assert res.error is not None
        assert res.error.code == "nplc_out_of_range"
        assert res.parsed_value is None

    def test_above_max_returns_out_of_range(self):
        """NPLC > 25 (Keithley 2400 limit) must be rejected."""
        res = validate_nplc("26")
        assert res.error is not None
        assert res.error.code == "nplc_out_of_range"

    def test_at_lower_bound_is_valid(self):
        res = validate_nplc("0.01")
        assert res.error is None
        assert res.parsed_value == pytest.approx(0.01)

    def test_at_upper_bound_is_valid(self):
        """NPLC = 25 (exact Keithley limit) must be accepted."""
        res = validate_nplc("25")
        assert res.error is None
        assert res.parsed_value == pytest.approx(25.0)

    def test_typical_value_valid(self):
        res = validate_nplc("1.0")
        assert res.error is None

    def test_nonnumeric_returns_error(self):
        res = validate_nplc("fast")
        assert res.error is not None
        assert res.error.code == "nplc_not_a_number"


# ── Voltage range ─────────────────────────────────────────────────────────────

class TestValidateVoltageRange:

    def test_start_equals_end_returns_error(self):
        res = validate_voltage_range(1.0, 1.0)
        assert res.error is not None
        assert res.error.code == "voltage_range_zero"
        assert res.parsed_value is None

    def test_start_greater_than_end_returns_error(self):
        """start > end is an error (current behavior locked)."""
        res = validate_voltage_range(1.2, 0.5)
        assert res.error is not None
        assert res.error.code == "voltage_range_zero"

    def test_valid_range_returns_span(self):
        res = validate_voltage_range(-0.1, 1.1)
        assert res.error is None
        assert res.parsed_value == pytest.approx(1.2)

    def test_small_positive_span_valid(self):
        res = validate_voltage_range(0.0, 0.001)
        assert res.error is None


# ── Illumination ──────────────────────────────────────────────────────────────

class TestValidateIllumination:

    def test_zero_returns_illumination_invalid(self):
        res = validate_illumination("0")
        assert res.error is not None
        assert res.error.code == "illumination_invalid"

    def test_negative_returns_illumination_invalid(self):
        res = validate_illumination("-100")
        assert res.error.code == "illumination_invalid"
        assert res.parsed_value is None

    def test_valid_illumination(self):
        res = validate_illumination("100.0")
        assert res.error is None
        assert res.parsed_value == pytest.approx(100.0)

    def test_empty_returns_error(self):
        res = validate_illumination("")
        assert res.error is not None

    def test_nonnumeric_returns_error(self):
        res = validate_illumination("sunny")
        assert res.error is not None


# ── Filename ──────────────────────────────────────────────────────────────────

class TestValidateFilename:

    def test_empty_returns_filename_required(self):
        res = validate_filename("")
        assert res.error is not None
        assert res.error.code == "filename_required"
        assert res.parsed_value is None

    def test_colon_returns_forbidden_chars(self):
        res = validate_filename("Sample:001")
        assert res.error is not None
        assert res.error.code == "filename_forbidden_chars"
        assert ":" in res.error.message

    def test_multiple_forbidden_chars(self):
        res = validate_filename("Sam<ple>001")
        assert res.error.code == "filename_forbidden_chars"

    def test_backslash_forbidden(self):
        res = validate_filename("path\\name")
        assert res.error.code == "filename_forbidden_chars"

    def test_valid_filename_no_error(self):
        res = validate_filename("TestCell_A1")
        assert res.error is None
        assert res.parsed_value is not None

    def test_spaces_allowed(self):
        """Spaces are not Windows-forbidden characters."""
        res = validate_filename("My Cell 01")
        assert res.error is None


# ── Scan rate ─────────────────────────────────────────────────────────────────

class TestValidateScanRate:

    # Reproduce the clamping formula from CLAUDE.md:
    # nplc_ms = 1.0 * (1000/60) ≈ 16.67 ms
    # time_per_point_ms = step_mv / rate * 1000
    # For step=25 mV, rate=1000 mV/s: time_per_point = 25 ms
    # delay = max(0, 25 - 16.67 - 50) = max(0, -41.67) = 0 → clamped

    def test_clamped_rate_returns_scan_rate_clamped_warning(self):
        """Rate that forces delay to 0 must trigger scan_rate_clamped warning."""
        res = validate_scan_rate(
            target_rate_mv_per_s=1000.0,   # fast rate
            step_mv=25.0,
            nplc=1.0,
        )
        assert res.error is None
        assert res.warning is not None
        assert res.warning.code == "scan_rate_clamped"
        assert "⚠" in res.warning.message

    def test_slow_rate_no_warning(self):
        """A slow scan rate that leaves delay > 0 must not warn."""
        res = validate_scan_rate(
            target_rate_mv_per_s=10.0,    # very slow
            step_mv=25.0,
            nplc=1.0,
        )
        assert res.error is None
        assert res.warning is None
        assert res.parsed_value == pytest.approx(10.0)

    def test_zero_rate_returns_error(self):
        res = validate_scan_rate(0.0, 25.0, 1.0)
        assert res.error is not None
        assert res.error.code == "scan_rate_invalid"


# ── Threshold range ───────────────────────────────────────────────────────────

class TestValidateThresholdRange:

    def test_min_greater_than_max_returns_threshold_inverted(self):
        res = validate_threshold_range(min_val=20.0, max_val=10.0)
        assert res.error is not None
        assert res.error.code == "threshold_inverted"
        assert res.parsed_value is None

    def test_min_equal_max_valid(self):
        """Equal bounds (zero-width range) is a degenerate but valid input."""
        res = validate_threshold_range(min_val=10.0, max_val=10.0)
        assert res.error is None
        assert res.parsed_value == pytest.approx(0.0)

    def test_valid_range_returns_span(self):
        res = validate_threshold_range(min_val=5.0, max_val=25.0)
        assert res.error is None
        assert res.parsed_value == pytest.approx(20.0)
