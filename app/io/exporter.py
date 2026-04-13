"""Excel export for J-V measurement results."""

import os
import xlsxwriter
from datetime import datetime


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

    wb  = xlsxwriter.Workbook(fp)
    ws  = wb.add_worksheet("MeasurementData")
    hf  = wb.add_format({"bold": True, "border": 1,
                          "align": "center", "valign": "vcenter"})
    nf  = wb.add_format({"num_format": "0.000000"})
    pf  = wb.add_format({"num_format": "0.0000", "bold": True})

    ws.set_column("A:F", 15)
    ws.set_column("G:Z", 14)

    headers = [
        "Forward Voltage (V)", "Forward Current Density (mA.cm-²)",
        "Forward Power Density (mW.cm-²)", "Reverse Voltage (V)",
        "Reverse Current Density (mA.cm-²)", "Reverse Power Density (mW.cm-²)",
        "Solar Cell Area (cm²)",
        "Forward Voc (V)", "Forward Jsc (mA.cm-²)",
        "Forward FF (%)", "Forward PCE (%)",
        "Forward Rs (\u03a9.cm\u00b2)", "Forward Rsh (\u03a9.cm\u00b2)",
        "Reverse Voc (V)", "Reverse Jsc (mA.cm-²)",
        "Reverse FF (%)", "Reverse PCE (%)",
        "Reverse Rs (\u03a9.cm\u00b2)", "Reverse Rsh (\u03a9.cm\u00b2)",
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

    wb  = xlsxwriter.Workbook(fp)
    ws  = wb.add_worksheet("Dark_JV_Data")
    hf  = wb.add_format({"bold": True, "border": 1,
                          "align": "center", "valign": "vcenter"})
    nf  = wb.add_format({"num_format": "0.000000"})
    mf  = wb.add_format({"num_format": "0.0000", "bold": True})

    ws.set_column("A:D", 15)
    ws.set_column("E:I", 14)

    headers = [
        "Forward Voltage (V)", "Forward Current Density (mA.cm\u207b\u00b2)",
        "Reverse Voltage (V)", "Reverse Current Density (mA.cm\u207b\u00b2)",
        "Solar Cell Area (cm\u00b2)", "Delay per point (ms)", "NPLC",
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
            ws.write(1, col, val, mf)

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
