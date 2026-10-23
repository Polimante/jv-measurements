"""Consolidated output workbook for Light-Intensity Dependent J-V measurements.

Sheet layout
------------
1. Config                — experiment metadata and settings
2. Summary               — one row per intensity with all PV parameters
3. LightIdealityAnalysis — n_idL from Voc_fwd and Voc_rev regressions
                           (only written when >= 3 successful measurements)
4. JV_{L}sun_Fwd / Rev  — raw V/J/P arrays per intensity
5. SingleDiodeFit_{L}sun — per-intensity fit results (when auto_fit is ON)

Individual backup files (one per intensity) are written by save_data() in
app/io/exporter.py and are NOT written here.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

from app.measurement.analysis import LightIdealityFit, fit_light_ideality

# ── Style helpers ─────────────────────────────────────────────────────────────

_BLUE   = "1565C0"
_WHITE  = "FFFFFF"
_GREEN  = "1B5E20"
_AMBER  = "E65100"
_RED    = "B71C1C"


def _hdr_fill(color: str = _BLUE) -> PatternFill:
    return PatternFill("solid", fgColor=color)


def _hdr_font(color: str = _WHITE) -> Font:
    return Font(name="Calibri", bold=True, color=color, size=11)


def _dat_font() -> Font:
    return Font(name="Calibri", size=10)


def _center() -> Alignment:
    return Alignment(horizontal="center", vertical="center")


def _hdr(ws, row: int, col: int, text: str, bg: str = _BLUE) -> None:
    c = ws.cell(row, col, text)
    c.fill      = _hdr_fill(bg)
    c.font      = _hdr_font()
    c.alignment = _center()


def _dat(ws, row: int, col: int, value) -> None:
    c = ws.cell(row, col, value)
    c.font      = _dat_font()
    c.alignment = _center()


def _sanitize_sheet_name(name: str) -> str:
    """Ensure the sheet name is valid for openpyxl (≤31 chars, no special chars)."""
    clean = re.sub(r"[\\/:*?\[\]]", "_", name)
    return clean[:31]


# ── Main save function ────────────────────────────────────────────────────────

def save_light_intensity_jv_consolidated(
    output_folder: Path,
    sample_name: str,
    intensities: list[float],
    results: list,                   # list[IntensityResult]
    scan_params: dict,
    stabilization_s: float,
    auto_fit: bool,
    aborted_at: Optional[int],
    instrument_idn: str = "",
) -> Path:
    """Write the consolidated LightIntensityJV workbook.

    Args:
        output_folder: Directory for the output file.
        sample_name: Sample identifier.
        intensities: All planned intensity values (including skipped ones).
        results: List of IntensityResult objects (may be shorter than intensities
                 if aborted).
        scan_params: dict with start_v, end_v, step_v, delay_ms, nplc, cell_area,
                     four_wire, reference_illumination_mw_cm2.
        stabilization_s: Wait time between prompt and scan.
        auto_fit: Whether single-diode fits were requested.
        aborted_at: Index where abort occurred (None if completed normally).
        instrument_idn: Instrument identification string.

    Returns:
        Path to the written workbook.
    """
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path(output_folder) / f"{sample_name}_LightIntensityJV_{ts}.xlsx"

    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # remove default empty sheet

    _write_config_sheet(wb, sample_name, intensities, scan_params,
                        stabilization_s, auto_fit, aborted_at,
                        instrument_idn, ts)
    _write_summary_sheet(wb, intensities, results)
    _write_jv_sheets(wb, results)
    if auto_fit:
        _write_fit_sheets(wb, results)
    _write_light_ideality_sheet(wb, results)

    wb.save(str(path))
    wb.close()
    return path


# ── Sheet: Config ─────────────────────────────────────────────────────────────

def _write_config_sheet(wb, sample_name, intensities, sp,
                        stab_s, auto_fit, aborted_at, idn, ts):
    ws = wb.create_sheet("Config")
    ws.column_dimensions["A"].width = 32
    ws.column_dimensions["B"].width = 30

    rows = [
        ("Sample Name",              sample_name),
        ("t0 Timestamp",             ts),
        ("Instrument IDN",           idn),
        ("Total Intensities Planned",len(intensities)),
        ("Intensity Values (suns)",  "; ".join(f"{x:.4g}" for x in intensities)),
        ("Reference Illumination (mW/cm²)", sp.get("reference_illumination_mw_cm2", 100.0)),
        ("Stabilization Time (s)",   stab_s),
        ("Auto Single-Diode Fit",    "ON" if auto_fit else "OFF"),
        ("Start Voltage (V)",        sp.get("start_v", "")),
        ("End Voltage (V)",          sp.get("end_v",   "")),
        ("Step Voltage (V)",         sp.get("step_v",  "")),
        ("Delay per Point (ms)",     sp.get("delay_ms", "")),
        ("NPLC",                     sp.get("nplc", "")),
        ("Cell Area (cm²)",          sp.get("cell_area", "")),
        ("4-Wire Sensing",           "ON" if sp.get("four_wire") else "OFF"),
        ("Pre-bias Enabled",         sp.get("prebias_enabled", "OFF")),
        ("Pre-bias Voltage (V)",     sp.get("prebias_voltage", "")),
        ("Pre-bias Duration (s)",    sp.get("prebias_duration", "")),
        ("Light Soaking Enabled",    sp.get("light_soaking_enabled", "OFF")),
        ("Light Soaking Duration (s)", sp.get("light_soaking_duration", "")),
    ]

    if aborted_at is not None:
        rows.append((
            "Note",
            f"Sequence aborted at intensity {intensities[aborted_at]:.4g} "
            f"after {aborted_at} of {len(intensities)} measurements."
        ))

    _hdr(ws, 1, 1, "Parameter")
    _hdr(ws, 1, 2, "Value")
    for r, (param, value) in enumerate(rows, start=2):
        _dat(ws, r, 1, param)
        _dat(ws, r, 2, value)


# ── Sheet: Summary ────────────────────────────────────────────────────────────

_SUMMARY_HEADERS = [
    "Index", "Intensity (suns)",
    "Voc_fwd (V)", "Voc_rev (V)",
    "Jsc_fwd (mA/cm²)", "Jsc_rev (mA/cm²)",
    "FF_fwd (%)", "FF_rev (%)",
    "PCE_fwd (%)", "PCE_rev (%)",
    "HI (%)",
    "Individual Backup File",
    "Status",
]


def _write_summary_sheet(wb, intensities, results):
    ws = wb.create_sheet("Summary")
    ws.column_dimensions["A"].width = 8
    for col in "BCDEFGHIJKLMN":
        ws.column_dimensions[col].width = 18
    ws.column_dimensions["M"].width = 40

    for c, h in enumerate(_SUMMARY_HEADERS, start=1):
        _hdr(ws, 1, c, h)

    result_map = {r.index: r for r in results}

    for idx, intensity in enumerate(intensities):
        r = result_map.get(idx)
        row = idx + 2
        status = r.status if r else "skipped"
        backup = r.individual_backup_path if r else ""

        # Color code the status cell
        status_color = {"completed": "1B5E20", "skipped": "E65100", "failed": "B71C1C"}
        bg = status_color.get(status, _BLUE)

        def _v(field):
            return getattr(r, field, float("nan")) if r else float("nan")

        for c, val in enumerate([
            idx + 1, intensity,
            _v("Voc_fwd"), _v("Voc_rev"),
            _v("Jsc_fwd"), _v("Jsc_rev"),
            _v("FF_fwd"),  _v("FF_rev"),
            _v("PCE_fwd"), _v("PCE_rev"),
            _v("HI"),
            backup,
        ], start=1):
            _dat(ws, row, c, val)

        cell = ws.cell(row, 13, status)
        cell.font      = Font(name="Calibri", size=10, color=_WHITE, bold=True)
        cell.fill      = PatternFill("solid", fgColor=bg)
        cell.alignment = _center()


# ── Sheet: LightIdealityAnalysis ──────────────────────────────────────────────

def _write_light_ideality_sheet(wb, results):
    completed = [r for r in results if r.status == "completed"
                 and not np.isnan(r.Voc_fwd) and not np.isnan(r.Voc_rev)]
    if len(completed) < 3:
        return

    intensities = np.array([r.intensity for r in completed])
    voc_fwd     = np.array([r.Voc_fwd   for r in completed])
    voc_rev     = np.array([r.Voc_rev   for r in completed])

    try:
        fit_fwd = fit_light_ideality(intensities, voc_fwd)
    except Exception as exc:
        fit_fwd = None
        fit_fwd_err = str(exc)
    else:
        fit_fwd_err = ""

    try:
        fit_rev = fit_light_ideality(intensities, voc_rev)
    except Exception as exc:
        fit_rev = None
        fit_rev_err = str(exc)
    else:
        fit_rev_err = ""

    ws = wb.create_sheet("LightIdealityAnalysis")
    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 18
    ws.column_dimensions["C"].width = 18
    ws.column_dimensions["D"].width = 36

    _hdr(ws, 1, 1, "Parameter")
    _hdr(ws, 1, 2, "Forward")
    _hdr(ws, 1, 3, "Reverse")
    _hdr(ws, 1, 4, "Notes")

    def _fv(fit, attr, fallback=""):
        if fit is None:
            return fallback
        return getattr(fit, attr, fallback)

    rows = [
        ("n_idL",     _fv(fit_fwd, "n_idL"),     _fv(fit_rev, "n_idL"),     "Light ideality factor"),
        ("n_idL_err", _fv(fit_fwd, "n_idL_err"),  _fv(fit_rev, "n_idL_err"),  "Standard error"),
        ("slope (V)", _fv(fit_fwd, "slope"),      _fv(fit_rev, "slope"),      "dVoc/d(ln L)"),
        ("slope_err", _fv(fit_fwd, "slope_err"),  _fv(fit_rev, "slope_err"),  "Slope standard error"),
        ("R²",        _fv(fit_fwd, "r_squared"),  _fv(fit_rev, "r_squared"),  ""),
        ("n_points",  _fv(fit_fwd, "n_points"),   _fv(fit_rev, "n_points"),   ""),
        ("T (K)",     _fv(fit_fwd, "temperature_K"), _fv(fit_rev, "temperature_K"), ""),
    ]

    # Warnings
    def _warn(fit):
        if fit is None:
            return "Fit failed"
        msgs = []
        if not np.isnan(float(fit.n_idL if fit.n_idL else float("nan"))):
            if not (0.9 <= fit.n_idL <= 2.5):
                msgs.append("Suspicious value (outside [0.9, 2.5])")
        if fit.r_squared < 0.98:
            msgs.append("Low fit quality (R² < 0.98)")
        return "; ".join(msgs) if msgs else "OK"

    rows.append(("Warning", _warn(fit_fwd), _warn(fit_rev), "Auto-generated"))

    for r_idx, (param, fwd_val, rev_val, note) in enumerate(rows, start=2):
        _dat(ws, r_idx, 1, param)
        _dat(ws, r_idx, 2, fwd_val)
        _dat(ws, r_idx, 3, rev_val)
        _dat(ws, r_idx, 4, note)

    # Raw data table
    data_start = len(rows) + 4
    _hdr(ws, data_start, 1, "Intensity (user units)")
    _hdr(ws, data_start, 2, "L_norm (fwd)")
    _hdr(ws, data_start, 3, "Voc_fwd (V)")
    _hdr(ws, data_start, 4, "Voc_rev (V)")

    l_norm = (fit_fwd.intensities_normalized if fit_fwd is not None
              else intensities / np.max(intensities))
    for i, (inten, ln, vf, vr) in enumerate(zip(intensities, l_norm, voc_fwd, voc_rev)):
        r = data_start + 1 + i
        _dat(ws, r, 1, inten)
        _dat(ws, r, 2, ln)
        _dat(ws, r, 3, vf)
        _dat(ws, r, 4, vr)


# ── Sheets: JV arrays per intensity ──────────────────────────────────────────

def _write_jv_sheets(wb, results):
    for r in results:
        if r.status != "completed" or r.v_fwd is None:
            continue
        intensity_tag = f"{r.intensity:.4g}".replace(".", "_")

        for scan, v_arr, j_arr in [
            ("Fwd", r.v_fwd, r.j_fwd),
            ("Rev", r.v_rev, r.j_rev),
        ]:
            sheet_name = _sanitize_sheet_name(f"JV_{intensity_tag}sun_{scan}")
            ws = wb.create_sheet(sheet_name)
            ws.column_dimensions["A"].width = 16
            ws.column_dimensions["B"].width = 22
            ws.column_dimensions["C"].width = 22

            _hdr(ws, 1, 1, "Voltage (V)")
            _hdr(ws, 1, 2, "Current Density (mA/cm²)")
            _hdr(ws, 1, 3, "Power Density (mW/cm²)")

            p_arr = v_arr * j_arr
            for row, (v, j, p) in enumerate(zip(v_arr, j_arr, p_arr), start=2):
                _dat(ws, row, 1, v)
                _dat(ws, row, 2, j)
                _dat(ws, row, 3, p)


# ── Sheets: SingleDiodeFit per intensity ──────────────────────────────────────

_FIT_PARAM_NAMES = ("J_ph", "J0", "n", "Rs", "Rsh")
_FIT_PARAM_UNITS = {
    "J_ph": "mA/cm²", "J0": "mA/cm²",
    "n": "—", "Rs": "Ω·cm²", "Rsh": "Ω·cm²",
}


def _write_fit_sheets(wb, results):
    for r in results:
        if r.status != "completed" or r.single_diode_fit is None:
            continue
        fit = r.single_diode_fit
        intensity_tag = f"{r.intensity:.4g}".replace(".", "_")
        sheet_name = _sanitize_sheet_name(f"SDFit_{intensity_tag}sun")
        ws = wb.create_sheet(sheet_name)
        ws.column_dimensions["A"].width = 18
        ws.column_dimensions["B"].width = 18
        ws.column_dimensions["C"].width = 18
        ws.column_dimensions["D"].width = 16

        for c, h in enumerate(["Parameter", "Value", "±Std Error", "Unit"], start=1):
            _hdr(ws, 1, c, h)

        for i, name in enumerate(_FIT_PARAM_NAMES):
            row = i + 2
            _dat(ws, row, 1, name)
            _dat(ws, row, 2, fit.parameters.get(name, float("nan")))
            _dat(ws, row, 3, fit.std_errors.get(name, float("nan")))
            _dat(ws, row, 4, _FIT_PARAM_UNITS.get(name, ""))

        for i, (label, val, unit) in enumerate([
            ("R²",       fit.r_squared, ""),
            ("RMSE",     fit.rmse,      "mA/cm²"),
            ("RMSE_log", fit.rmse_log,  "log₁₀ scale"),
        ]):
            row = 7 + i
            _dat(ws, row, 1, label)
            _dat(ws, row, 2, val)
            _dat(ws, row, 4, unit)

        for c, h in enumerate(
            ["Method", "Temperature (K)", "Timestamp", "Bounded Parameters"], start=1
        ):
            _hdr(ws, 11, c, h)
        _dat(ws, 12, 1, fit.method)
        _dat(ws, 12, 2, fit.temperature_K)
        _dat(ws, 12, 3, fit.timestamp)
        _dat(ws, 12, 4,
             ", ".join(fit.bounded_parameters) if fit.bounded_parameters else "none")
