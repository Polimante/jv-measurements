"""Excel export for J-V measurement results."""

import os
import xlsxwriter
from datetime import datetime


# ── Shared format factory ─────────────────────────────────────────────────────
# All exported workbooks use the same three cell styles so every file produced
# by this application has an identical visual identity.
#
#   hf  — header row  : blue fill (#1565C0), white bold Calibri 11, thin border
#   pf  — scalar row  : bold Calibri 11, centred, thin border, 4-decimal format
#   nf  — data rows   : Calibri 10, centred, 6-decimal format

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


def save_data(
    export_folder, sample_name,
    v_f, j_f, v_r, j_r, illum,
    Voc_f, Jsc_f, FF_f, PCE_f, Rs_f, Rsh_f,
    Voc_r, Jsc_r, FF_r, PCE_r, Rs_r, Rsh_r, HI,
    cell_area, delay_ms, nplc, four_wire,
    prebias_enabled, prebias_voltage, prebias_duration,
    instrument_idn,
    scan_rate=None,
):
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

    # Calculate scan rate if not provided explicitly
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
