"""Pure input-validation functions for the J-V measurement GUI.

Every function accepts raw Python values (strings or floats) as they arrive
from the Entry widgets and returns a ValidationResult.  No Tkinter dependency
— all functions are testable without a running display.

Validation codes are stable strings; GUI callbacks show the human-readable
message while tests assert on the code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ── Hardware limits ───────────────────────────────────────────────────────────

_NPLC_MIN: float = 0.01
_NPLC_MAX: float = 25.0          # Keithley 2400 upper limit
_GPIB_OVERHEAD_MS: float = 50.0  # matches _recalculate_delay_from_scan_rate
_PLC_HZ: float = 60.0            # power-line frequency (Brazil / North America)

# Windows-forbidden filename characters
_WINDOWS_FORBIDDEN: frozenset[str] = frozenset('<>:"/\\|?*')


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class ValidationError:
    """Hard error: the input cannot be used as-is."""
    code: str
    message: str


@dataclass
class ValidationWarning:
    """Soft warning: the input is accepted but with a caveat."""
    code: str
    message: str


@dataclass
class ValidationResult:
    """Outcome of one validation call.

    Attributes:
        parsed_value: The converted value on success, or None on error.
        error: Set when the input is invalid.
        warning: Set when the input is accepted but sub-optimal.
    """
    parsed_value: float | None
    error: Optional[ValidationError] = field(default=None)
    warning: Optional[ValidationWarning] = field(default=None)


# ── Validators ────────────────────────────────────────────────────────────────

def validate_cell_area(raw: str) -> ValidationResult:
    """Validate the cell active area entry.

    Args:
        raw: String as it appears in the Entry widget (e.g. "0.07").

    Returns:
        ValidationResult with parsed_value = area (cm²) on success.
    """
    raw = str(raw).strip()
    if not raw:
        return ValidationResult(
            None,
            error=ValidationError("area_required", "Cell area is required."),
        )
    try:
        val = float(raw)
    except ValueError:
        return ValidationResult(
            None,
            error=ValidationError(
                "area_not_a_number",
                f"Cell area must be a number; got {raw!r}.",
            ),
        )
    if val <= 0:
        return ValidationResult(
            None,
            error=ValidationError(
                "area_must_be_positive",
                "Cell area must be positive (cm²).",
            ),
        )
    return ValidationResult(val)


def validate_delay_ms(raw: str) -> ValidationResult:
    """Validate the per-point delay entry (ms).

    Args:
        raw: String from the Entry widget (e.g. "100").  An empty string is
            interpreted as 0 ms (no delay).

    Returns:
        ValidationResult with parsed_value = delay (ms).
    """
    raw = str(raw).strip()
    if not raw:
        return ValidationResult(0.0)
    try:
        val = float(raw)
    except ValueError:
        return ValidationResult(
            None,
            error=ValidationError(
                "delay_not_a_number",
                f"Delay must be a number; got {raw!r}.",
            ),
        )
    if val < 0:
        return ValidationResult(
            None,
            error=ValidationError("delay_negative", "Delay cannot be negative."),
        )
    return ValidationResult(val)


def validate_nplc(raw: str) -> ValidationResult:
    """Validate the NPLC (Number of Power Line Cycles) entry.

    Valid range: 0.01 – 25.0, which are the hardware limits of the
    Keithley 2400 source-measure unit.

    Args:
        raw: String from the Entry widget.

    Returns:
        ValidationResult with parsed_value = NPLC on success.
    """
    raw = str(raw).strip()
    try:
        val = float(raw)
    except ValueError:
        return ValidationResult(
            None,
            error=ValidationError(
                "nplc_not_a_number",
                f"NPLC must be a number; got {raw!r}.",
            ),
        )
    if not (_NPLC_MIN <= val <= _NPLC_MAX):
        return ValidationResult(
            None,
            error=ValidationError(
                "nplc_out_of_range",
                f"NPLC must be between {_NPLC_MIN} and {_NPLC_MAX} "
                "(Keithley 2400 limit).",
            ),
        )
    return ValidationResult(val)


def validate_voltage_range(start: float, end: float) -> ValidationResult:
    """Validate that start voltage is strictly less than end voltage.

    Args:
        start: Start voltage (V).
        end:   End voltage (V).

    Returns:
        ValidationResult with parsed_value = (end − start) on success.
    """
    if start >= end:
        return ValidationResult(
            None,
            error=ValidationError(
                "voltage_range_zero",
                "Start voltage must be less than end voltage.",
            ),
        )
    return ValidationResult(end - start)


def validate_illumination(raw: str) -> ValidationResult:
    """Validate the illumination intensity (mW/cm²).

    Args:
        raw: String from the illumination StringVar (e.g. "100.0").

    Returns:
        ValidationResult with parsed_value = illumination (mW/cm²).
    """
    raw = str(raw).strip()
    if not raw:
        return ValidationResult(
            None,
            error=ValidationError(
                "illumination_invalid",
                "Illumination value is required.",
            ),
        )
    try:
        val = float(raw)
    except ValueError:
        return ValidationResult(
            None,
            error=ValidationError(
                "illumination_not_a_number",
                f"Illumination must be a number; got {raw!r}.",
            ),
        )
    if val <= 0:
        return ValidationResult(
            None,
            error=ValidationError(
                "illumination_invalid",
                "Illumination must be positive (mW/cm²).",
            ),
        )
    return ValidationResult(val)


def validate_filename(raw: str) -> ValidationResult:
    """Validate a Windows-compatible sample name used as a filename stem.

    Checks for empty string and Windows-forbidden characters
    (``< > : " / \\ | ? *``).

    Args:
        raw: Sample name string from the Entry widget.

    Returns:
        ValidationResult with parsed_value = 0.0 (success sentinel).
        Non-None parsed_value signals success; error signals failure.
    """
    raw = str(raw).strip()
    if not raw:
        return ValidationResult(
            None,
            error=ValidationError("filename_required", "Sample name is required."),
        )
    found = sorted({c for c in raw if c in _WINDOWS_FORBIDDEN})
    if found:
        return ValidationResult(
            None,
            error=ValidationError(
                "filename_forbidden_chars",
                f"Sample name contains forbidden character(s): {''.join(found)}",
            ),
        )
    return ValidationResult(0.0)


def validate_scan_rate(
    target_rate_mv_per_s: float,
    step_mv: float,
    nplc: float,
) -> ValidationResult:
    """Validate a target scan rate; warn when delay would clamp to 0 ms.

    Applies the formula documented in CLAUDE.md::

        delay = (step_mV / rate × 1000) − NPLC × 16.7 − 50 ms  (clamped ≥ 0)

    A ``⚠`` warning is issued when the computed delay is forced to zero,
    meaning the actual achieved rate will be lower than the target.

    Args:
        target_rate_mv_per_s: Desired scan rate (mV/s).
        step_mv:              Voltage step size (mV/point).
        nplc:                 Integration time (power-line cycles).

    Returns:
        ValidationResult with parsed_value = target_rate_mv_per_s on success.
        Attaches ValidationWarning(code="scan_rate_clamped") when delay = 0.
    """
    if target_rate_mv_per_s <= 0:
        return ValidationResult(
            None,
            error=ValidationError("scan_rate_invalid", "Scan rate must be positive."),
        )
    nplc_ms = nplc * (1000.0 / _PLC_HZ)
    time_per_point_ms = step_mv / target_rate_mv_per_s * 1000.0
    delay_ms = max(0.0, time_per_point_ms - nplc_ms - _GPIB_OVERHEAD_MS)
    if delay_ms == 0.0:
        return ValidationResult(
            target_rate_mv_per_s,
            warning=ValidationWarning(
                "scan_rate_clamped",
                "Scan rate too high — delay clamped to 0 ms. "
                "Actual rate will be lower. ⚠",
            ),
        )
    return ValidationResult(target_rate_mv_per_s)


def validate_threshold_range(min_val: float, max_val: float) -> ValidationResult:
    """Validate that the minimum threshold does not exceed the maximum.

    Args:
        min_val: Lower bound of the acceptable range.
        max_val: Upper bound of the acceptable range.

    Returns:
        ValidationResult with parsed_value = (max − min) on success.
    """
    if min_val > max_val:
        return ValidationResult(
            None,
            error=ValidationError(
                "threshold_inverted",
                "Minimum threshold cannot exceed maximum.",
            ),
        )
    return ValidationResult(max_val - min_val)
