"""Excel export for J-V measurement results."""

import os
import xlsxwriter
import openpyxl
from datetime import datetime

# Sheet name used for the single-diode model fit results
FIT_SHEET = "SingleDiodeFit"

_FIT_PARAM_NAMES = ("J_ph", "J0", "n", "Rs", "Rsh")
_FIT_PARAM_UNITS = {
    "J_ph": "mA/cm²",
    "J0":   "mA/cm²",
    "n":    "—",
    "Rs":   "Ω·cm²",
    "Rsh":  "Ω·cm²",
}


# ── Shared format factory ─────────────────────────────────────────────────────

def _fmt(wb):
    hf = wb.add_format({
        "bold":       True,
        "border":     1,
        "align":      "center",
        "valign":     "vcenter",
        "bg_color":   "#1565C0",
        "font_color": "#FFFFFF",
        "font_name":  "Calibri",
        "font_size":  11,
    })
    pf = wb.add_format({
        "num_format": "0.0000",
        "bold":       True,
        "border":     1,
        "align":      "center",
        "valign":     "vcenter",
        "font_name":  "Calibri",
        "font_size":  11,
    })
    nf = wb.add_format({
        "num_format": "0.000000",
        "align":      "center",
        "font_name":  "Calibri",
        "font_size":  10,
    })
    return hf, pf, nf


# ── SingleDiodeFit sheet writer (xlsxwriter) ─────────────────────────────────

def _write_fit_sheet(wb, hf, pf, nf, fit):
    """Write a SingleDiodeFit to a new sheet in an xlsxwriter Workbook."""
    ws = wb.add_worksheet(FIT_SHEET)
    ws.set_column("A:A", 20)
    ws.set_column("B:C", 18)
    ws.set_column("D:D", 16)

    # Row 0: column headers
    for c, h in enumerate(["Parameter", "Value", "±Std Error", "Unit"]):
        ws.write(0, c, h, hf)

    # Rows 1-5: fitted parameters
    for i, name in enumerate(_FIT_PARAM_NAMES):
        r = i + 1
        ws.write(r, 0, name, pf)
        ws.write(r, 1, fit.parameters.get(name, float("nan")), nf)
        ws.write(r, 2, fit.std_errors.get(name, float("nan")), nf)
        ws.write(r, 3, _FIT_PARAM_UNITS.get(name, ""), nf)

    # Rows 6-8: goodness-of-fit metrics
    for i, (label, val, unit) in enumerate([
        ("R²",       fit.r_squared, ""),
        ("RMSE",          fit.rmse,      "mA/cm²"),
        ("RMSE_log",      fit.rmse_log,  "log₁₀ scale"),
    ]):
        r = 6 + i
        ws.write(r, 0, label, pf)
        ws.write(r, 1, val, nf)
        ws.write(r, 3, unit, nf)

    # Row 10: metadata headers; Row 11: values
    for c, h in enumerate(
        ["Method", "Temperature (K)", "Fit Timestamp", "Bounded Parameters"]
    ):
        ws.write(10, c, h, hf)
    ws.write(11, 0, fit.method, nf)
    ws.write(11, 1, fit.temperature_K, nf)
    ws.write(11, 2, fit.timestamp, nf)
    ws.write(
        11, 3,
        ", ".join(fit.bounded_parameters) if fit.bounded_parameters else "none",
        nf,
    )


# ── append_single_diode_fit_to_file (openpyxl) ───────────────────────────────

def append_single_diode_fit_to_file(filepath: str, fit) -> None:
    """Write or overwrite the SingleDiodeFit sheet in an existing xlsx file.

    Uses openpyxl so it can append to a file already closed by xlsxwriter.

    Args:
        filepath: Absolute path to an existing .xlsx file.
        fit: SingleDiodeFit instance from app.measurement.analysis.
    """
    wb = openpyxl.load_workbook(filepath)
    if FIT_SHEET in wb.sheetnames:
        del wb[FIT_SHEET]
    ws = wb.create_sheet(FIT_SHEET)

    # Headers
    ws.cell(1, 1, "Parameter")
    ws.cell(1, 2, "Value")
    ws.cell(1, 3, "±Std Error")
    ws.cell(1, 4, "Unit")

    # Parameters (rows 2-6)
    for i, name in enumerate(_FIT_PARAM_NAMES):
        r = i + 2
        ws.cell(r, 1, name)
        ws.cell(r, 2, fit.parameters.get(name))
        ws.cell(r, 3, fit.std_errors.get(name))
        ws.cell(r, 4, _FIT_PARAM_UNITS.get(name, ""))

    # Metrics (rows 7-9)
    for i, (label, val, unit) in enumerate([
        ("R²",   fit.r_squared, ""),
        ("RMSE",      fit.rmse,      "mA/cm²"),
        ("RMSE_log",  fit.rmse_log,  "log₁₀ scale"),
    ]):
        r = 7 + i
        ws.cell(r, 1, label)
        ws.cell(r, 2, val)
        ws.cell(r, 4, unit)

    # Metadata (row 11 header, row 12 values)
    for c, h in enumerate(
        ["Method", "Temperature (K)", "Fit Timestamp", "Bounded Parameters"], 1
    ):
        ws.cell(11, c, h)
    ws.cell(12, 1, fit.method)
    ws.cell(12, 2, fit.temperature_K)
    ws.cell(12, 3, fit.timestamp)
    ws.cell(12, 4,
            ", ".join(fit.bounded_parameters) if fit.bounded_parameters else "none")

    wb.save(filepath)
    wb.close()


# ── Primary save functions ────────────────────────────────────────────────────

def save_data(
    export_folder, sample_name,
    v_f, j_f, v_r, j_r, illum,
    Voc_f, Jsc_f, FF_f, PCE_f, Rs_f, Rsh_f,
    Voc_r, Jsc_r, FF_r, PCE_r, Rs_r, Rsh_r, HI,
    cell_area, delay_ms, nplc, four_wire,
    prebias_enabled, prebias_voltage, prebias_duration,
    instrument_idn,
    scan_rate=None,
    fit_result=None,
):
    """Save an illuminated J-V measurement to xlsx.

    Args:
        fit_result: Optional SingleDiodeFit.  When provided a second sheet
            named ``SingleDiodeFit`` is appended to the workbook.
    """
    p_f = v_f * j_f
    p_r = v_r * j_r
    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    fp  = os.path.join(export_folder, f"{sample_name}_{ts}.xlsx")

    wb       = xlsxwriter.Workbook(fp)
    ws       = wb.add_worksheet("MeasurementData")
    hf, pf, nf = _fmt(wb)

    ws.set_column("A:F", 15)
    ws.set_column("G:Z", 14)

    headers = [
        "Forward Voltage (V)", "Forward Current Density (mA.cm-²)",
        "Forward Power Density (mW.cm-²)", "Reverse Voltage (V)",
        "Reverse Current Density (mA.cm-²)", "Reverse Power Density (mW.cm-²)",
        "Solar Cell Area (cm²)",
        "Forward Voc (V)", "Forward Jsc (mA.cm-²)",
        "Forward FF (%)", "Forward PCE (%)",
        "Forward Rs (Ω.cm²)", "Forward Rsh (Ω.cm²)",
        "Reverse Voc (V)", "Reverse Jsc (mA.cm-²)",
        "Reverse FF (%)", "Reverse PCE (%)",
        "Reverse Rs (Ω.cm²)", "Reverse Rsh (Ω.cm²)",
        "Hysteresis Index HI (%)",
        "Delay per point (ms)", "NPLC", "4-Wire Sensing",
        "Pre-bias Voltage (V)", "Pre-bias Duration (s)",
        "Instrument IDN", "Scan Rate (mV/s)",
    ]
    for c, h in enumerate(headers):
        ws.write(0, c, h, hf)

    pb_v = prebias_voltage if prebias_enabled else 0.0
    pb_t = prebias_duration if prebias_enabled else 0.0

    if scan_rate is None:
        _nplc_ms        = nplc * (1000.0 / 60.0)
        _time_per_point = (delay_ms + _nplc_ms + 20.0) / 1000.0
        _step_mv        = abs(v_f[-1] - v_f[0]) / max(len(v_f) - 1, 1) * 1000.0
        scan_rate = _step_mv / _time_per_point if _time_per_point > 0 else 0.0

    for col, val in enumerate([
        None, None, None, None, None, None,          # A-F: data columns
        cell_area,                                    # G
        Voc_f, Jsc_f, FF_f, PCE_f, Rs_f, Rsh_f,    # H-M
        Voc_r, Jsc_r, FF_r, PCE_r, Rs_r, Rsh_r,    # N-S
        HI,                                          # T
        delay_ms,                                    # U
        nplc,                                        # V
        1 if four_wire else 0,                       # W
        pb_v, pb_t,                                  # X, Y
        instrument_idn,                              # Z
        scan_rate,                                   # AA
    ]):
        if val is not None:
            ws.write(1, col, val, pf)

    for i in range(max(len(v_f), len(v_r))):
        r = i + 2
        if i < len(v_f):
            ws.write(r, 0, v_f[i], nf)
            ws.write(r, 1, j_f[i], nf)
            ws.write(r, 2, p_f[i], nf)
        if i < len(v_r):
            ws.write(r, 3, v_r[i], nf)
            ws.write(r, 4, j_r[i], nf)
            ws.write(r, 5, p_r[i], nf)

    if fit_result is not None:
        _write_fit_sheet(wb, hf, pf, nf, fit_result)

    wb.close()
    return fp


def save_dark_jv_data(
    export_folder, sample_name,
    v_f, j_f, v_r, j_r,
    cell_area, delay_ms, nplc, four_wire,
    instrument_idn,
):
    """Save dark J-V raw data to a separate Excel file.

    No PCE/FF/Voc/Jsc are extracted — those parameters have no physical
    meaning without illumination.  The file contains raw V and J columns
    for forward and reverse scans plus measurement metadata.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fp = os.path.join(export_folder, f"{sample_name}_Dark_JV_{ts}.xlsx")

    wb       = xlsxwriter.Workbook(fp)
    ws       = wb.add_worksheet("Dark_JV_Data")
    hf, pf, nf = _fmt(wb)

    ws.set_column("A:D", 15)
    ws.set_column("E:I", 14)

    headers = [
        "Forward Voltage (V)", "Forward Current Density (mA.cm⁻²)",
        "Reverse Voltage (V)", "Reverse Current Density (mA.cm⁻²)",
        "Solar Cell Area (cm²)", "Delay per point (ms)", "NPLC",
        "4-Wire Sensing", "Instrument IDN",
    ]
    for c, h in enumerate(headers):
        ws.write(0, c, h, hf)

    for col, val in enumerate([
        None, None, None, None,       # A-D: data columns
        cell_area,                    # E
        delay_ms,                     # F
        nplc,                         # G
        1 if four_wire else 0,        # H
        instrument_idn,               # I
    ]):
        if val is not None:
            ws.write(1, col, val, pf)

    for i in range(max(len(v_f), len(v_r))):
        r = i + 2
        if i < len(v_f):
            ws.write(r, 0, v_f[i], nf)
            ws.write(r, 1, j_f[i], nf)
        if i < len(v_r):
            ws.write(r, 2, v_r[i], nf)
            ws.write(r, 3, j_r[i], nf)

    wb.close()
    return fp


def save_stab_current_data(
    export_folder, sample_name,
    times, j_vals, pce_vals,
    cell_area, v_applied, illumination,
    duration_s, interval_ms, threshold_pct_per_min,
    stabilized_at,
):
    """Save Stabilized Current measurement to xlsx (standardised layout)."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fp = os.path.join(export_folder, f"{sample_name}_StabCurrent_{ts}.xlsx")

    wb       = xlsxwriter.Workbook(fp)
    ws       = wb.add_worksheet("StabCurrent_Data")
    hf, pf, nf = _fmt(wb)

    ws.set_column("A:C", 15)
    ws.set_column("D:J", 18)

    headers = [
        "Time (s)", "J (mA/cm⁻²)", "PCE (%)",
        "Cell Area (cm²)", "Applied Voltage (V)",
        "Illumination (mW/cm²)", "Duration (s)",
        "Interval (ms)", "Threshold (%/min)", "Stabilized at (s)",
    ]
    for c, h in enumerate(headers):
        ws.write(0, c, h, hf)

    meta_vals = [
        cell_area, v_applied, illumination,
        duration_s, interval_ms, threshold_pct_per_min,
        round(stabilized_at, 1) if stabilized_at is not None else None,
    ]
    for i, val in enumerate(meta_vals):
        if val is not None:
            ws.write(1, i + 3, val, pf)

    for i, (t, j, pce) in enumerate(zip(times, j_vals, pce_vals)):
        r = i + 2
        ws.write(r, 0, t, nf)
        ws.write(r, 1, j, nf)
        ws.write(r, 2, pce, nf)

    wb.close()
    return fp


def save_pure_mpp_data(
    export_folder, sample_name,
    time_s, v_track, j_track, pce_track,
    cell_area, illumination, nplc, delay_ms,
):
    """Save Pure MPP Tracking measurement to xlsx (standardised layout)."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fp = os.path.join(export_folder, f"{sample_name}_PureMPP_{ts}.xlsx")

    wb       = xlsxwriter.Workbook(fp)
    ws       = wb.add_worksheet("PureMPP_Data")
    hf, pf, nf = _fmt(wb)

    ws.set_column("A:E", 14)
    ws.set_column("F:I", 18)

    headers = [
        "Time (s)", "Time (min)", "Vmpp (V)",
        "J (mA/cm⁻²)", "PCE (%)",
        "Cell Area (cm²)", "Illumination (mW/cm²)",
        "NPLC", "Delay/point (ms)",
    ]
    for c, h in enumerate(headers):
        ws.write(0, c, h, hf)

    for col, val in enumerate([cell_area, illumination, nplc, delay_ms]):
        ws.write(1, col + 5, val, pf)

    for i, (t, v, j, pce) in enumerate(zip(time_s, v_track, j_track, pce_track)):
        r = i + 2
        ws.write(r, 0, t, nf)
        ws.write(r, 1, t / 60.0, nf)
        ws.write(r, 2, v, nf)
        ws.write(r, 3, j, nf)
        ws.write(r, 4, pce, nf)

    wb.close()
    return fp


def save_mpp_data(
    export_folder, sample_name,
    cycles, spo_time, spo_j, spo_pce, spo_cycle_idx,
    cell_area, illumination, nplc, delay_ms,
    spo_duration_s, total_duration_min,
):
    """Save MPP Tracking (J-V + SPO cycling) measurement to xlsx (standardised layout)."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fp = os.path.join(export_folder, f"{sample_name}_MPP_{ts}.xlsx")

    wb       = xlsxwriter.Workbook(fp)
    hf, pf, nf = _fmt(wb)

    # ── Sheet 1: SPO time-series data ─────────────────────────────────────────
    ws_spo = wb.add_worksheet("MPP_SPO")
    ws_spo.set_column("A:D", 14)
    ws_spo.set_column("E:J", 18)

    spo_headers = [
        "Time (min)", "J (mA/cm⁻²)", "PCE (%)", "Cycle",
        "Cell Area (cm²)", "Illumination (mW/cm²)",
        "NPLC", "Delay/point (ms)",
        "SPO Duration (s)", "Total Duration (min)",
    ]
    for c, h in enumerate(spo_headers):
        ws_spo.write(0, c, h, hf)

    for col, val in enumerate([
        cell_area, illumination, nplc, delay_ms,
        spo_duration_s, total_duration_min,
    ]):
        ws_spo.write(1, col + 4, val, pf)

    spo_cycle_idx = list(spo_cycle_idx) if spo_cycle_idx else []
    for i, (t, j, pce, cyc) in enumerate(
        zip([x / 60.0 for x in spo_time], spo_j, spo_pce, spo_cycle_idx)
    ):
        r = i + 2
        ws_spo.write(r, 0, t, nf)
        ws_spo.write(r, 1, j, nf)
        ws_spo.write(r, 2, pce, nf)
        ws_spo.write(r, 3, int(cyc), nf)

    # ── Sheet 2: JV parameters per cycle ─────────────────────────────────────
    ws_jv = wb.add_worksheet("MPP_JV")
    ws_jv.set_column("A:I", 14)

    jv_headers = [
        "Cycle", "Time (min)", "Vmpp (V)", "Jmpp (mA/cm⁻²)",
        "Voc (V)", "Jsc (mA/cm⁻²)", "FF (%)", "PCE (%)", "HI (%)",
    ]
    for c, h in enumerate(jv_headers):
        ws_jv.write(0, c, h, hf)

    for i, cyc in enumerate(cycles):
        r = i + 1
        ws_jv.write(r, 0, cyc["cycle"])
        ws_jv.write(r, 1, cyc["time_s"] / 60.0, nf)
        ws_jv.write(r, 2, cyc["Vmpp"], nf)
        ws_jv.write(r, 3, cyc["Jmpp"], nf)
        ws_jv.write(r, 4, cyc["Voc"],  nf)
        ws_jv.write(r, 5, cyc["Jsc"],  nf)
        ws_jv.write(r, 6, cyc["FF"],   nf)
        ws_jv.write(r, 7, cyc["PCE"],  nf)
        hi_val = cyc.get("HI")
        if hi_val is not None:
            ws_jv.write(r, 8, hi_val, nf)

    # ── Sheets 3+: raw J-V data per cycle ─────────────────────────────────────
    for cyc in cycles:
        ws = wb.add_worksheet(f"JV_Cycle_{cyc['cycle']}")
        if cyc.get("v_fwd") is not None:
            for c, h in enumerate([
                "Voltage (V)", "J reverse (mA/cm⁻²)",
                "J forward (mA/cm⁻²)"
            ]):
                ws.write(0, c, h, hf)
            for r, (v, jr, jf) in enumerate(
                zip(cyc["v_rev"], cyc["j_rev"], cyc["j_fwd"])
            ):
                ws.write(r + 1, 0, float(v), nf)
                ws.write(r + 1, 1, float(jr), nf)
                ws.write(r + 1, 2, float(jf), nf)
        else:
            for c, h in enumerate([
                "Voltage (V)", "J reverse (mA/cm⁻²)"
            ]):
                ws.write(0, c, h, hf)
            for r, (v, jr) in enumerate(zip(cyc["v_rev"], cyc["j_rev"])):
                ws.write(r + 1, 0, float(v), nf)
                ws.write(r + 1, 1, float(jr), nf)

    wb.close()
    return fp
