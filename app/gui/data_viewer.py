"""
Data Viewer — read-only Toplevel window for opening saved measurement Excel files.

Supports:
  - Illuminated J-V      (sheet: MeasurementData)
  - Dark J-V             (sheet: Dark_JV_Data)
  - Stabilized Current   (sheet: StabCurrent_Data  or legacy: StabilizedCurrent)
  - Pure MPP Tracking    (sheet: PureMPP_Data — new and legacy formats)
  - MPP Tracking JV-SPO  (sheets: MPP_SPO + MPP_JV  or legacy: JV_Parameters)
"""

import csv
import math
import os
import threading

import numpy as np
import openpyxl
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from tkinter import (
    Toplevel, Frame, LabelFrame, Label, Button, Canvas, Entry,
    StringVar, DoubleVar, BooleanVar,
    filedialog, messagebox,
    LEFT, RIGHT, BOTH, X, Y, W,
    NORMAL, DISABLED,
)
from tkinter import ttk

from app.gui.theme import PLOT_COLORS as _COLORS

_ILLUMINATED_SHEET = "MeasurementData"
_DARK_SHEET        = "Dark_JV_Data"
_STAB_SHEET        = "StabCurrent_Data"
_STAB_SHEET_OLD    = "StabilizedCurrent"
_PMPP_SHEET        = "PureMPP_Data"
_MPP_SPO_SHEET     = "MPP_SPO"
_MPP_JV_PARAMS     = "MPP_JV"
_MPP_OLD_SHEET     = "JV_Parameters"
_FIT_SHEET              = "SingleDiodeFit"
_DARK_IDEALITY_SHEET    = "DarkIdealityFit"
_LIGHT_JV_CONFIG_SHEET  = "Config"
_LIGHT_JV_SUMMARY_SHEET = "Summary"
_LIGHT_JV_IDEALITY_SHEET = "LightIdealityAnalysis"

_FIT_PARAM_NAMES = ("J_ph", "J0", "n", "Rs", "Rsh")
_FIT_PARAM_UNITS = {
    "J_ph": "mA/cm²",
    "J0":   "mA/cm²",
    "n":    "—",
    "Rs":   "Ω·cm²",
    "Rsh":  "Ω·cm²",
}


# ── Excel parsing helpers ──────────────────────────────────────────────────────

def _cell(ws, row_1idx, col_1idx):
    return ws.cell(row=row_1idx, column=col_1idx).value


def _read_illuminated(ws):
    """MeasurementData sheet — row1=headers, row2=scalars, row3+=arrays."""
    def p(col):
        return _cell(ws, 2, col)

    meta = {
        "type":       "illuminated",
        "cell_area":  p(7),
        "Voc_f":      p(8),   "Jsc_f": p(9),   "FF_f": p(10),
        "PCE_f":      p(11),  "Rs_f":  p(12),  "Rsh_f": p(13),
        "Voc_r":      p(14),  "Jsc_r": p(15),  "FF_r": p(16),
        "PCE_r":      p(17),  "Rs_r":  p(18),  "Rsh_r": p(19),
        "HI":         p(20),
        "delay_ms":   p(21),  "nplc": p(22),  "four_wire": p(23),
        "prebias_v":  p(24),  "prebias_t": p(25),
        "instrument": p(26),
        "scan_rate":  p(27),
    }

    v_f, j_f, p_f = [], [], []
    v_r, j_r, p_r = [], [], []
    for row in ws.iter_rows(min_row=3, values_only=True):
        if row[0] is not None:
            v_f.append(float(row[0]))
            j_f.append(float(row[1]))
            p_f.append(float(row[2]))
        if len(row) > 3 and row[3] is not None:
            v_r.append(float(row[3]))
            j_r.append(float(row[4]))
            p_r.append(float(row[5]))

    meta["v_f"] = np.array(v_f);  meta["j_f"] = np.array(j_f);  meta["p_f"] = np.array(p_f)
    meta["v_r"] = np.array(v_r);  meta["j_r"] = np.array(j_r);  meta["p_r"] = np.array(p_r)
    return meta


def _read_single_diode_fit(ws):
    """SingleDiodeFit sheet — parameters rows 2-6, metrics 7-9, metadata row 12."""
    def _sf(v):
        try:
            return float(v) if v is not None else float("nan")
        except (TypeError, ValueError):
            return float("nan")

    params = {}
    errors = {}
    for i, name in enumerate(_FIT_PARAM_NAMES):
        r = i + 2  # 1-indexed rows 2-6
        val = _cell(ws, r, 2)
        err = _cell(ws, r, 3)
        if val is not None:
            try:
                params[name] = float(val)
            except (TypeError, ValueError):
                pass
        if err is not None:
            try:
                errors[name] = float(err)
            except (TypeError, ValueError):
                pass

    r_squared = _sf(_cell(ws, 7, 2))
    rmse      = _sf(_cell(ws, 8, 2))
    rmse_log  = _sf(_cell(ws, 9, 2))

    method    = _cell(ws, 12, 1) or ""
    temp_k    = _sf(_cell(ws, 12, 2))
    timestamp = str(_cell(ws, 12, 3) or "")
    bounded_raw = str(_cell(ws, 12, 4) or "")
    bounded = [
        b.strip() for b in bounded_raw.split(",")
        if b.strip() and b.strip().lower() != "none"
    ]

    return {
        "parameters":         params,
        "std_errors":         errors,
        "r_squared":          r_squared,
        "rmse":               rmse,
        "rmse_log":           rmse_log,
        "method":             method,
        "temperature_K":      temp_k if not math.isnan(temp_k) else 298.15,
        "timestamp":          timestamp,
        "bounded_parameters": bounded,
    }


def _read_dark_ideality_fit(ws):
    """DarkIdealityFit sheet — rows 2-11 contain parameter/value/error/note rows."""
    def _sf(v):
        try:
            return float(v) if v is not None else float("nan")
        except (TypeError, ValueError):
            return float("nan")

    # Row index mapping (1-indexed): param rows are 2-11
    _PARAM_ROWS = {
        "n_idd":      2,
        "J_s":        3,
        "r_squared":  4,
        "rmse_log":   5,
        "v_min":      6,
        "v_max":      7,
        "n_points":   8,
        "temperature_K": 9,
        "method":    10,
        "timestamp": 11,
    }
    result: dict = {}
    for key, row in _PARAM_ROWS.items():
        raw = _cell(ws, row, 2)
        if key in ("method", "timestamp"):
            result[key] = str(raw) if raw is not None else ""
        elif key == "n_points":
            try:
                result[key] = int(raw) if raw is not None else 0
            except (TypeError, ValueError):
                result[key] = 0
        else:
            result[key] = _sf(raw)

    for err_key, row in [("n_idd_err", 2), ("J_s_err", 3)]:
        raw = _cell(ws, row, 3)
        result[err_key] = _sf(raw) if not isinstance(raw, str) else float("nan")

    return result


def _read_dark(ws):
    """Dark_JV_Data sheet — row1=headers, row2=scalars, row3+=arrays."""
    def p(col):
        return _cell(ws, 2, col)

    meta = {
        "type":       "dark",
        "cell_area":  p(5),
        "delay_ms":   p(6),
        "nplc":       p(7),
        "four_wire":  p(8),
        "instrument": p(9),
    }

    v_f, j_f, v_r, j_r = [], [], [], []
    for row in ws.iter_rows(min_row=3, values_only=True):
        if row[0] is not None:
            v_f.append(float(row[0]))
            j_f.append(float(row[1]))
        if len(row) > 2 and row[2] is not None:
            v_r.append(float(row[2]))
            j_r.append(float(row[3]))

    meta["v_f"] = np.array(v_f);  meta["j_f"] = np.array(j_f)
    meta["v_r"] = np.array(v_r);  meta["j_r"] = np.array(j_r)
    return meta


def _read_stab(ws):
    """StabCurrent_Data sheet — new standardised format."""
    def p(col):
        return _cell(ws, 2, col)

    meta = {
        "type":        "stab",
        "cell_area":   p(4),
        "v_applied":   p(5),
        "illumination": p(6),
        "duration_s":  p(7),
        "interval_ms": p(8),
        "threshold":   p(9),
        "stabilized_at": p(10),
    }

    times, j_vals, pce_vals = [], [], []
    for row in ws.iter_rows(min_row=3, values_only=True):
        if row[0] is not None:
            times.append(float(row[0]))
            j_vals.append(float(row[1]))
            pce_vals.append(float(row[2]))

    meta["times"]   = np.array(times)
    meta["j_vals"]  = np.array(j_vals)
    meta["pce_vals"] = np.array(pce_vals)
    return meta


def _read_stab_old(ws):
    """StabilizedCurrent sheet — legacy openpyxl format (key-value rows)."""
    kv = {}
    for row in ws.iter_rows(min_row=2, max_row=10, values_only=True):
        if row and row[0] and row[1] is not None:
            kv[str(row[0])] = row[1]

    stab_raw = kv.get("Stabilized at (s)", None)
    stab_val = None if (stab_raw == "N/A" or stab_raw is None) else float(stab_raw)

    meta = {
        "type":         "stab",
        "cell_area":    None,
        "v_applied":    kv.get("Applied Voltage (V)"),
        "illumination": kv.get("Illumination (mW/cm²)"),
        "duration_s":   kv.get("Duration (s)"),
        "interval_ms":  kv.get("Interval (ms)"),
        "threshold":    kv.get("Threshold (%/min)"),
        "stabilized_at": stab_val,
    }

    times, j_vals, pce_vals = [], [], []
    data_started = False
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not data_started:
            if row and str(row[0]).strip().lower() == "time (s)":
                data_started = True
            continue
        if row[0] is not None:
            try:
                times.append(float(row[0]))
                j_vals.append(float(row[1]))
                pce_vals.append(float(row[2]))
            except (TypeError, ValueError):
                pass

    meta["times"]    = np.array(times)
    meta["j_vals"]   = np.array(j_vals)
    meta["pce_vals"] = np.array(pce_vals)
    return meta


def _read_pure_mpp(ws):
    """PureMPP_Data sheet — new standardised format (row1=headers, row2=meta)."""
    def p(col):
        return _cell(ws, 2, col)

    meta = {
        "type":         "pure_mpp",
        "cell_area":    p(6),
        "illumination": p(7),
        "nplc":         p(8),
        "delay_ms":     p(9),
    }

    times, times_min, v_track, j_track, pce_track = [], [], [], [], []
    for row in ws.iter_rows(min_row=3, values_only=True):
        if row[0] is not None:
            times.append(float(row[0]))
            times_min.append(float(row[1]))
            v_track.append(float(row[2]))
            j_track.append(float(row[3]))
            pce_track.append(float(row[4]))

    meta["times"]     = np.array(times)
    meta["times_min"] = np.array(times_min)
    meta["v_track"]   = np.array(v_track)
    meta["j_track"]   = np.array(j_track)
    meta["pce_track"] = np.array(pce_track)
    return meta


def _read_pure_mpp_old(ws):
    """PureMPP_Data sheet — legacy openpyxl key-value format."""
    kv = {}
    for row in ws.iter_rows(min_row=1, max_row=6, values_only=True):
        if row and row[0] and row[1] is not None:
            kv[str(row[0])] = row[1]

    meta = {
        "type":         "pure_mpp",
        "cell_area":    kv.get("Cell Area"),
        "illumination": kv.get("Illumination"),
        "nplc":         None,
        "delay_ms":     None,
    }

    times, times_min, v_track, j_track, pce_track = [], [], [], [], []
    data_started = False
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not data_started:
            if row and str(row[0]).strip().lower() == "time (s)":
                data_started = True
            continue
        if row[0] is not None:
            try:
                t = float(row[0])
                times.append(t)
                times_min.append(float(row[1]))
                v_track.append(float(row[2]))
                j_track.append(float(row[3]))
                pce_track.append(float(row[4]))
            except (TypeError, ValueError):
                pass

    meta["times"]     = np.array(times)
    meta["times_min"] = np.array(times_min)
    meta["v_track"]   = np.array(v_track)
    meta["j_track"]   = np.array(j_track)
    meta["pce_track"] = np.array(pce_track)
    return meta


def _read_mpp(wb):
    """MPP_SPO + MPP_JV sheets — new standardised format."""
    ws_spo = wb[_MPP_SPO_SHEET]

    def p(col):
        return _cell(ws_spo, 2, col)

    meta = {
        "type":              "mpp",
        "cell_area":         p(5),
        "illumination":      p(6),
        "nplc":              p(7),
        "delay_ms":          p(8),
        "spo_duration_s":    p(9),
        "total_duration_min": p(10),
    }

    times_min, j_spo, pce_spo, cycle_idx = [], [], [], []
    for row in ws_spo.iter_rows(min_row=3, values_only=True):
        if row[0] is not None:
            times_min.append(float(row[0]))
            j_spo.append(float(row[1]))
            pce_spo.append(float(row[2]))
            cycle_idx.append(int(row[3]) if row[3] is not None else 0)

    meta["times_min"] = np.array(times_min)
    meta["j_spo"]     = np.array(j_spo)
    meta["pce_spo"]   = np.array(pce_spo)
    meta["cycle_idx"] = np.array(cycle_idx, dtype=int)

    cycles = []
    if _MPP_JV_PARAMS in wb.sheetnames:
        ws_jv = wb[_MPP_JV_PARAMS]
        for row in ws_jv.iter_rows(min_row=2, values_only=True):
            if row[0] is not None:
                try:
                    cycles.append({
                        "cycle":  int(row[0]),
                        "t_min":  float(row[1]),
                        "Vmpp":   float(row[2]),
                        "Jmpp":   float(row[3]),
                        "Voc":    float(row[4]),
                        "Jsc":    float(row[5]),
                        "FF":     float(row[6]),
                        "PCE":    float(row[7]),
                        "HI":     float(row[8]) if row[8] is not None else None,
                    })
                except (TypeError, ValueError):
                    pass
    meta["cycles"] = cycles
    return meta


def _read_mpp_old(wb):
    """JV_Parameters + SPO_Data sheets — legacy openpyxl format."""
    ws_params = wb[_MPP_OLD_SHEET]

    cycles = []
    for row in ws_params.iter_rows(min_row=2, values_only=True):
        if row[0] is not None:
            try:
                cycles.append({
                    "cycle": int(row[0]),
                    "t_min": float(row[1]),
                    "Vmpp":  float(row[2]),
                    "Jmpp":  float(row[3]),
                    "Voc":   float(row[4]),
                    "Jsc":   float(row[5]),
                    "FF":    float(row[6]),
                    "PCE":   float(row[7]),
                    "HI":    float(row[8]) if (len(row) > 8 and row[8] not in (None, "")) else None,
                })
            except (TypeError, ValueError):
                pass

    times_min, j_spo, pce_spo, cycle_idx = [], [], [], []
    if "SPO_Data" in wb.sheetnames:
        ws_spo = wb["SPO_Data"]
        for row in ws_spo.iter_rows(min_row=2, values_only=True):
            if row[0] is not None:
                try:
                    times_min.append(float(row[0]))
                    j_spo.append(float(row[1]))
                    pce_spo.append(float(row[2]))
                    cycle_idx.append(int(row[3]) if (len(row) > 3 and row[3] is not None) else 0)
                except (TypeError, ValueError):
                    pass

    meta = {
        "type":              "mpp",
        "cell_area":         None,
        "illumination":      None,
        "nplc":              None,
        "delay_ms":          None,
        "spo_duration_s":    None,
        "total_duration_min": None,
        "cycles":            cycles,
        "times_min":         np.array(times_min),
        "j_spo":             np.array(j_spo),
        "pce_spo":           np.array(pce_spo),
        "cycle_idx":         np.array(cycle_idx, dtype=int),
    }
    return meta


def _read_light_ideality_analysis(ws):
    """LightIdealityAnalysis sheet — rows 2-9 are key-value table, then raw data."""
    def _sf(v):
        try:
            return float(v) if v is not None else float("nan")
        except (TypeError, ValueError):
            return float("nan")

    kv: dict = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row and row[0] is not None:
            kv[str(row[0])] = (row[1], row[2] if len(row) > 2 else None)

    def _get(key, idx):
        pair = kv.get(key, (None, None))
        val  = pair[idx] if pair and len(pair) > idx else None
        return _sf(val)

    n_pts_pair = kv.get("n_points", (None, None))
    try:
        n_pts_fwd = int(n_pts_pair[0]) if n_pts_pair and n_pts_pair[0] is not None else 0
    except (TypeError, ValueError):
        n_pts_fwd = 0
    try:
        n_pts_rev = int(n_pts_pair[1]) if n_pts_pair and n_pts_pair[1] is not None else 0
    except (TypeError, ValueError):
        n_pts_rev = 0

    warn_pair = kv.get("Warning", ("", ""))

    intensities: list = []
    l_norm: list = []
    voc_fwd_list: list = []
    voc_rev_list: list = []
    data_section = False
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row and row[0] == "Intensity (user units)":
            data_section = True
            continue
        if data_section and row and row[0] is not None:
            try:
                intensities.append(float(row[0]))
                l_norm.append(float(row[1]))
                voc_fwd_list.append(float(row[2]))
                voc_rev_list.append(float(row[3]))
            except (TypeError, ValueError, IndexError):
                pass

    return {
        "n_idL_fwd":     _get("n_idL",     0),
        "n_idL_rev":     _get("n_idL",     1),
        "n_idL_fwd_err": _get("n_idL_err", 0),
        "n_idL_rev_err": _get("n_idL_err", 1),
        "slope_fwd":     _get("slope (V)", 0),
        "slope_rev":     _get("slope (V)", 1),
        "r2_fwd":        _get("R²",        0),
        "r2_rev":        _get("R²",        1),
        "n_points_fwd":  n_pts_fwd,
        "n_points_rev":  n_pts_rev,
        "warning_fwd":   str(warn_pair[0] or ""),
        "warning_rev":   str(warn_pair[1] or ""),
        "intensities":   np.array(intensities, dtype=float),
        "l_norm":        np.array(l_norm,       dtype=float),
        "voc_fwd":       np.array(voc_fwd_list, dtype=float),
        "voc_rev":       np.array(voc_rev_list, dtype=float),
    }


def _read_light_intensity_jv(wb):
    """Config + Summary + LightIdealityAnalysis + JV_* sheets → metadata dict."""
    def _sf(v):
        try:
            return float(v) if v is not None else float("nan")
        except (TypeError, ValueError):
            return float("nan")

    # Config: key/value pairs starting from row 2
    ws_cfg = wb[_LIGHT_JV_CONFIG_SHEET]
    cfg: dict = {}
    for row in ws_cfg.iter_rows(min_row=2, values_only=True):
        if row and row[0] is not None:
            cfg[str(row[0])] = row[1]

    # Summary: one row per intensity
    ws_sum = wb[_LIGHT_JV_SUMMARY_SHEET]
    results: list = []
    for row in ws_sum.iter_rows(min_row=2, values_only=True):
        if row and row[0] is not None:
            try:
                results.append({
                    "index":     int(row[0]) - 1,
                    "intensity": _sf(row[1]),
                    "Voc_fwd":   _sf(row[2]),
                    "Voc_rev":   _sf(row[3]),
                    "Jsc_fwd":   _sf(row[4]),
                    "Jsc_rev":   _sf(row[5]),
                    "FF_fwd":    _sf(row[6]),
                    "FF_rev":    _sf(row[7]),
                    "PCE_fwd":   _sf(row[8]),
                    "PCE_rev":   _sf(row[9]),
                    "HI":        _sf(row[10]),
                    "backup":    str(row[11]) if row[11] else "",
                    "status":    str(row[12]) if len(row) > 12 and row[12] else "unknown",
                })
            except (TypeError, ValueError, IndexError):
                pass

    # LightIdealityAnalysis (optional)
    ideality = None
    if _LIGHT_JV_IDEALITY_SHEET in wb.sheetnames:
        try:
            ideality = _read_light_ideality_analysis(wb[_LIGHT_JV_IDEALITY_SHEET])
        except Exception:
            pass

    # Per-intensity JV sheets: JV_{tag}sun_Fwd / JV_{tag}sun_Rev
    jv_data: dict = {}
    for sheet_name in wb.sheetnames:
        if not sheet_name.startswith("JV_"):
            continue
        if sheet_name.endswith("_Fwd"):
            inner = sheet_name[3:-4]
            scan  = "fwd"
        elif sheet_name.endswith("_Rev"):
            inner = sheet_name[3:-4]
            scan  = "rev"
        else:
            continue
        if not inner.endswith("sun"):
            continue
        tag = inner[:-3]
        intensity_key = tag.replace("_", ".", 1)

        ws = wb[sheet_name]
        v_arr, j_arr, p_arr = [], [], []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row and row[0] is not None:
                try:
                    v_arr.append(float(row[0]))
                    j_arr.append(float(row[1]))
                    p_arr.append(float(row[2]))
                except (TypeError, ValueError, IndexError):
                    pass

        if intensity_key not in jv_data:
            jv_data[intensity_key] = {}
        jv_data[intensity_key][f"v_{scan}"] = np.array(v_arr, dtype=np.float64)
        jv_data[intensity_key][f"j_{scan}"] = np.array(j_arr, dtype=np.float64)
        jv_data[intensity_key][f"p_{scan}"] = np.array(p_arr, dtype=np.float64)

    return {
        "type":            "light_jv",
        "sample_name":     cfg.get("Sample Name", ""),
        "timestamp":       str(cfg.get("t0 Timestamp", "") or ""),
        "instrument":      str(cfg.get("Instrument IDN", "") or ""),
        "n_intensities":   cfg.get("Total Intensities Planned"),
        "intensities_str": str(cfg.get("Intensity Values (suns)", "") or ""),
        "ref_illum":       _sf(cfg.get("Reference Illumination (mW/cm²)")),
        "stabilization_s": _sf(cfg.get("Stabilization Time (s)")),
        "auto_fit":        cfg.get("Auto Single-Diode Fit", "OFF") == "ON",
        "start_v":         _sf(cfg.get("Start Voltage (V)")),
        "end_v":           _sf(cfg.get("End Voltage (V)")),
        "step_v":          _sf(cfg.get("Step Voltage (V)")),
        "delay_ms":        _sf(cfg.get("Delay per Point (ms)")),
        "nplc":            _sf(cfg.get("NPLC")),
        "cell_area":       _sf(cfg.get("Cell Area (cm²)")),
        "four_wire":       cfg.get("4-Wire Sensing", "OFF") == "ON",
        "prebias_enabled": cfg.get("Pre-bias Enabled", "OFF") == "ON",
        "prebias_v":       _sf(cfg.get("Pre-bias Voltage (V)")),
        "prebias_t":       _sf(cfg.get("Pre-bias Duration (s)")),
        "ls_enabled":      cfg.get("Light Soaking Enabled", "OFF") == "ON",
        "ls_dur":          _sf(cfg.get("Light Soaking Duration (s)")),
        "abort_note":      str(cfg.get("Note", "") or ""),
        "results":         results,
        "ideality":        ideality,
        "jv_data":         jv_data,
    }


def load_jv_file(filepath):
    """
    Open an xlsx file and return a parsed metadata dict.
    Raises ValueError for unrecognised formats.
    Also reads the SingleDiodeFit sheet when present in illuminated files.
    """
    wb = openpyxl.load_workbook(filepath, data_only=True)
    try:
        sheets = wb.sheetnames
        if _LIGHT_JV_CONFIG_SHEET in sheets and _LIGHT_JV_SUMMARY_SHEET in sheets:
            ws_cfg = wb[_LIGHT_JV_CONFIG_SHEET]
            _is_lijv = any(
                row[0] == "Total Intensities Planned"
                for row in ws_cfg.iter_rows(min_row=2, max_row=25, values_only=True)
                if row and row[0] is not None
            )
            if _is_lijv:
                return _read_light_intensity_jv(wb)
        if _ILLUMINATED_SHEET in sheets:
            meta = _read_illuminated(wb[_ILLUMINATED_SHEET])
            if _FIT_SHEET in sheets:
                meta["single_diode_fit"] = _read_single_diode_fit(wb[_FIT_SHEET])
            return meta
        elif _DARK_SHEET in sheets:
            meta = _read_dark(wb[_DARK_SHEET])
            if _DARK_IDEALITY_SHEET in sheets:
                meta["dark_ideality_fit"] = _read_dark_ideality_fit(wb[_DARK_IDEALITY_SHEET])
            return meta
        elif _STAB_SHEET in sheets:
            return _read_stab(wb[_STAB_SHEET])
        elif _STAB_SHEET_OLD in sheets:
            return _read_stab_old(wb[_STAB_SHEET_OLD])
        elif _PMPP_SHEET in sheets:
            first = _cell(wb[_PMPP_SHEET], 1, 1)
            if first and str(first).strip().lower() == "time (s)":
                return _read_pure_mpp(wb[_PMPP_SHEET])
            else:
                return _read_pure_mpp_old(wb[_PMPP_SHEET])
        elif _MPP_SPO_SHEET in sheets:
            return _read_mpp(wb)
        elif _MPP_OLD_SHEET in sheets:
            return _read_mpp_old(wb)
        else:
            raise ValueError(
                f"Unrecognised file format.\n"
                f"Found sheets: {', '.join(sheets)}"
            )
    finally:
        wb.close()


# ── Formatting helpers ────────────────────────────────────────────────────────

def _fmt(val, fmt_str, fallback="—"):
    if val is None:
        return fallback
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return fallback
        return fmt_str % f
    except (TypeError, ValueError):
        return str(val) if val else fallback


# ── DataViewerWindow ──────────────────────────────────────────────────────────

class DataViewerWindow:
    def __init__(self, parent, filepath):
        self.parent    = parent
        self._pick_cid = None
        self._cursor   = None
        self._current_data     = None
        self._current_filepath = None
        self._lij_intensity_var = None
        self._lij_key_map: dict = {}
        self._voc_canvas        = None
        self._ax_voc            = None

        self.win = Toplevel(parent)
        self.win.title("Data Viewer")
        self.win.geometry("1200x780")
        self.win.minsize(900, 600)
        self.win.transient(parent)

        self._build_skeleton()
        self._load(filepath)

    # ── UI skeleton ───────────────────────────────────────────────────────────

    def _build_skeleton(self):
        top_bar = Frame(self.win, bg="#2c3e50", height=32)
        top_bar.pack(fill=X, side="top")
        top_bar.pack_propagate(False)

        self._title_lbl = Label(
            top_bar, text="", bg="#2c3e50", fg="#ecf0f1",
            font=("Arial", 9, "bold"), anchor=W,
        )
        self._title_lbl.pack(side=LEFT, padx=10, pady=6)

        self._fit_btn = Button(
            top_bar, text="Single-Diode Fit",
            command=self._on_single_diode_fit,
            bg="#4A148C", fg="white", relief="flat",
            font=("Arial", 9), padx=8, pady=2,
            state=DISABLED,
        )
        self._fit_btn.pack(side=RIGHT, padx=4, pady=4)

        self._dark_ideality_btn = Button(
            top_bar, text="Dark Ideality Fit",
            command=self._on_dark_ideality_fit,
            bg="#00695C", fg="white", relief="flat",
            font=("Arial", 9), padx=8, pady=2,
            state=DISABLED,
        )
        self._dark_ideality_btn.pack(side=RIGHT, padx=4, pady=4)

        Button(
            top_bar, text="Open Another File…",
            command=self._on_open_another,
            bg="#455a64", fg="white", relief="flat",
            font=("Arial", 9), padx=8, pady=2,
        ).pack(side=RIGHT, padx=10, pady=4)

        body = Frame(self.win)
        body.pack(fill=BOTH, expand=True)

        left_outer = Frame(body, width=290, bg="#f0f0f0")
        left_outer.pack(side=LEFT, fill=Y, expand=False)
        left_outer.pack_propagate(False)

        self._meta_canvas = Canvas(
            left_outer, highlightthickness=0, bg="#f0f0f0",
        )
        vsb = ttk.Scrollbar(
            left_outer, orient="vertical", command=self._meta_canvas.yview,
        )
        self._meta_canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=RIGHT, fill=Y)
        self._meta_canvas.pack(side=LEFT, fill=BOTH, expand=True)

        self._meta_inner = Frame(self._meta_canvas, bg="#f0f0f0")
        self._meta_win_id = self._meta_canvas.create_window(
            (0, 0), window=self._meta_inner, anchor="nw",
        )
        self._meta_inner.bind("<Configure>", self._on_inner_configure)
        self._meta_canvas.bind("<Configure>", self._on_canvas_configure)

        for widget in (self._meta_canvas, self._meta_inner):
            widget.bind("<MouseWheel>", self._on_mousewheel)

        right_frame = Frame(body)
        right_frame.pack(side=RIGHT, fill=BOTH, expand=True)

        self._fig = Figure(figsize=(8, 5.5))
        self._ax  = self._fig.add_subplot(111)
        self._ax2 = self._ax.twinx()
        self._ax2.yaxis.set_label_position("right")

        self._fig_canvas = FigureCanvasTkAgg(self._fig, right_frame)
        self._fig_canvas.get_tk_widget().pack(fill=BOTH, expand=True)

    def _on_inner_configure(self, _event):
        self._meta_canvas.configure(scrollregion=self._meta_canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self._meta_canvas.itemconfig(self._meta_win_id, width=event.width)

    def _on_mousewheel(self, event):
        self._meta_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    # ── Load / reload ─────────────────────────────────────────────────────────

    def _on_open_another(self):
        path = filedialog.askopenfilename(
            title="Open Data File",
            filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")],
            parent=self.win,
        )
        if path:
            self._load(path)

    def _load(self, filepath):
        try:
            data = load_jv_file(filepath)
        except Exception as exc:
            messagebox.showerror("File Error", str(exc), parent=self.win)
            return

        self._current_data     = data
        self._current_filepath = filepath

        filename = os.path.basename(filepath)
        _TYPE_LABELS = {
            "illuminated": "Illuminated J-V",
            "dark":        "Dark J-V",
            "stab":        "Stabilized Current",
            "pure_mpp":    "Pure MPP Tracking",
            "mpp":         "MPP Tracking (JV-SPO)",
            "light_jv":    "Light-Intensity J-V",
        }
        type_str = _TYPE_LABELS.get(data["type"], data["type"])

        self._title_lbl.config(text=f"\U0001f4c2  {filename}   —   {type_str}")
        self.win.title(f"Data Viewer — {filename}")

        # Enable each analysis button only for the relevant data type
        if data["type"] == "illuminated":
            self._fit_btn.config(state=NORMAL)
        else:
            self._fit_btn.config(state=DISABLED)

        if data["type"] == "dark":
            self._dark_ideality_btn.config(state=NORMAL)
        else:
            self._dark_ideality_btn.config(state=DISABLED)

        self._populate_metadata(data, filename)
        self._draw_plot(data, filename)

    # ── Metadata panel ────────────────────────────────────────────────────────

    def _populate_metadata(self, data, filename):
        if self._voc_canvas is not None:
            try:
                self._voc_canvas.get_tk_widget().destroy()
            except Exception:
                pass
            self._voc_canvas = None
        self._lij_intensity_var = None
        self._lij_key_map = {}

        for w in self._meta_inner.winfo_children():
            w.destroy()

        bg = "#f0f0f0"

        def section(title):
            lf = LabelFrame(
                self._meta_inner, text=title,
                bg=bg, font=("Arial", 9, "bold"), padx=6, pady=4,
            )
            lf.pack(fill=X, padx=6, pady=(4, 0))
            lf.bind("<MouseWheel>", self._on_mousewheel)
            return lf

        def row(parent, label, value, fg="#2c3e50"):
            f = Frame(parent, bg=bg)
            f.pack(fill=X, pady=1)
            f.bind("<MouseWheel>", self._on_mousewheel)
            Label(f, text=label, bg=bg, fg="#607d8b",
                  font=("Arial", 9), anchor=W, width=17).pack(side=LEFT, padx=(4, 0))
            Label(f, text=str(value), bg=bg, fg=fg,
                  font=("Arial", 9, "bold"), anchor=W).pack(side=LEFT, padx=(2, 4))

        _TYPE_LABELS = {
            "illuminated": "Illuminated J-V",
            "dark":        "Dark J-V",
            "stab":        "Stabilized Current",
            "pure_mpp":    "Pure MPP Tracking",
            "mpp":         "MPP Tracking (JV-SPO)",
            "light_jv":    "Light-Intensity J-V",
        }
        _TYPE_COLORS = {
            "illuminated": "#1565C0",
            "dark":        "#5d4e75",
            "stab":        "#1B5E20",
            "pure_mpp":    "#E65100",
            "mpp":         "#006064",
            "light_jv":    "#4A148C",
        }

        sf = section("File")
        name_display = filename if len(filename) <= 34 else filename[:31] + "…"
        row(sf, "Name:", name_display)
        row(sf, "Type:", _TYPE_LABELS.get(data["type"], data["type"]),
            fg=_TYPE_COLORS.get(data["type"], "#2c3e50"))

        dtype = data["type"]

        # ── J-V (illuminated / dark) ───────────────────────────────────────
        if dtype in ("illuminated", "dark"):
            v_f = data.get("v_f", np.array([]))
            v_r = data.get("v_r", np.array([]))

            sm = section("Measurement Data")
            row(sm, "Cell area:", _fmt(data.get("cell_area"), "%.4f cm²"))
            if len(v_f) >= 2:
                row(sm, "Fwd range:", f"{v_f[0]:.3f} → {v_f[-1]:.3f} V")
                row(sm, "Step ≈:", f"{abs(float(np.mean(np.diff(v_f)))):.4f} V")
            row(sm, "Fwd points:", str(len(v_f)))
            row(sm, "Rev points:", str(len(v_r)))

            si = section("Instrument Settings")
            row(si, "Delay/pt:", _fmt(data.get("delay_ms"), "%.0f ms"))
            row(si, "NPLC:", _fmt(data.get("nplc"), "%.2f"))
            fw = data.get("four_wire")
            fw_str = ("ON" if int(fw) else "OFF") if fw is not None else "—"
            row(si, "4-Wire:", fw_str)
            row(si, "Scan rate:", _fmt(data.get("scan_rate"), "%.1f mV/s"))
            idn = str(data.get("instrument") or "—")
            row(si, "Instrument:", (idn[:34] + "…") if len(idn) > 34 else idn)

            if dtype == "illuminated":
                sp = section("Pre-bias Conditioning")
                pb_v = data.get("prebias_v") or 0
                pb_t = data.get("prebias_t") or 0
                if pb_v == 0 and pb_t == 0:
                    row(sp, "Status:", "Not used", fg="#9e9e9e")
                else:
                    row(sp, "Voltage:", _fmt(pb_v, "%.3f V"))
                    row(sp, "Duration:", _fmt(pb_t, "%.1f s"))

                sf2 = section("Forward Scan")
                row(sf2, "Voc:",  _fmt(data.get("Voc_f"),  "%.4f V"),          "#1565C0")
                row(sf2, "Jsc:",  _fmt(data.get("Jsc_f"),  "%.4f mA/cm²"), "#1565C0")
                row(sf2, "FF:",   _fmt(data.get("FF_f"),   "%.2f %%"),          "#1565C0")
                row(sf2, "PCE:",  _fmt(data.get("PCE_f"),  "%.2f %%"),          "#1565C0")
                row(sf2, "Rs:",   _fmt(data.get("Rs_f"),   "%.2f Ω·cm²"))
                row(sf2, "Rsh:",  _fmt(data.get("Rsh_f"),  "%.2f Ω·cm²"))

                sr = section("Reverse Scan")
                row(sr, "Voc:",  _fmt(data.get("Voc_r"),  "%.4f V"),          "#B71C1C")
                row(sr, "Jsc:",  _fmt(data.get("Jsc_r"),  "%.4f mA/cm²"), "#B71C1C")
                row(sr, "FF:",   _fmt(data.get("FF_r"),   "%.2f %%"),          "#B71C1C")
                row(sr, "PCE:",  _fmt(data.get("PCE_r"),  "%.2f %%"),          "#B71C1C")
                row(sr, "Rs:",   _fmt(data.get("Rs_r"),   "%.2f Ω·cm²"))
                row(sr, "Rsh:",  _fmt(data.get("Rsh_r"),  "%.2f Ω·cm²"))

                sh = section("Hysteresis")
                hi = data.get("HI")
                try:
                    hi_f = float(hi)
                    hi_color = "#B71C1C" if hi_f > 10 else "#E65100" if hi_f > 5 else "#1B5E20"
                except (TypeError, ValueError):
                    hi_color = "#2c3e50"
                row(sh, "HI:", _fmt(hi, "%.2f %%"), hi_color)

                # ── Single-Diode Model Fit (if present) ───────────────────
                fit = data.get("single_diode_fit")
                if fit:
                    self._populate_fit_metadata_section(section, row, fit)

            # ── Dark Ideality Fit (dark files only, if present) ────────────
            if dtype == "dark":
                dfit = data.get("dark_ideality_fit")
                if dfit:
                    self._populate_dark_ideality_metadata_section(section, row, dfit)

        # ── Stabilized Current ────────────────────────────────────────────
        elif dtype == "stab":
            times = data.get("times", np.array([]))
            sm = section("Measurement Data")
            row(sm, "Cell area:", _fmt(data.get("cell_area"), "%.4f cm²"))
            row(sm, "Applied V:", _fmt(data.get("v_applied"), "%.3f V"))
            row(sm, "Illumination:", _fmt(data.get("illumination"), "%.2f mW/cm²"))
            row(sm, "Duration:", _fmt(data.get("duration_s"), "%.0f s"))
            row(sm, "Points:", str(len(times)))
            if len(times) > 1:
                row(sm, "Total time:", f"{times[-1]:.1f} s")

            stab = data.get("stabilized_at")
            row(sm, "Stabilized at:",
                _fmt(stab, "%.1f s") if stab is not None else "Not reached",
                fg="#1B5E20" if stab is not None else "#9e9e9e")

            si = section("Instrument Settings")
            row(si, "Interval:", _fmt(data.get("interval_ms"), "%.0f ms"))
            row(si, "Threshold:", _fmt(data.get("threshold"), "%.2f %%/min"))

            pce_arr = data.get("pce_vals", np.array([]))
            j_arr   = data.get("j_vals",   np.array([]))
            if len(pce_arr):
                sr = section("Results")
                row(sr, "PCE range:", f"{float(np.min(pce_arr)):.2f} – {float(np.max(pce_arr)):.2f} %")
                row(sr, "J range:",   f"{float(np.min(j_arr)):.3f} – {float(np.max(j_arr)):.3f} mA/cm²")
                if stab is not None and len(times):
                    idx = np.searchsorted(times, stab)
                    if idx < len(pce_arr):
                        row(sr, "PCE at stab:", _fmt(pce_arr[idx], "%.2f %%"), "#1B5E20")

        # ── Pure MPP Tracking ─────────────────────────────────────────────
        elif dtype == "pure_mpp":
            times_min = data.get("times_min", np.array([]))
            pce_track = data.get("pce_track", np.array([]))
            v_track   = data.get("v_track",   np.array([]))

            sm = section("Measurement Data")
            row(sm, "Cell area:", _fmt(data.get("cell_area"), "%.4f cm²"))
            row(sm, "Illumination:", _fmt(data.get("illumination"), "%.2f mW/cm²"))
            row(sm, "Points:", str(len(times_min)))
            if len(times_min) > 1:
                row(sm, "Duration:", f"{times_min[-1]:.2f} min")

            si = section("Instrument Settings")
            row(si, "NPLC:", _fmt(data.get("nplc"), "%.2f"))
            row(si, "Delay/pt:", _fmt(data.get("delay_ms"), "%.0f ms"))

            if len(pce_track):
                sr = section("Results")
                row(sr, "PCE max:", _fmt(float(np.max(pce_track)), "%.2f %%"), "#E65100")
                row(sr, "PCE mean:", _fmt(float(np.mean(pce_track)), "%.2f %%"))
                row(sr, "PCE final:", _fmt(float(pce_track[-1]), "%.2f %%"))
                if len(v_track):
                    row(sr, "Vmpp mean:", _fmt(float(np.mean(v_track)), "%.3f V"))
                    row(sr, "Vmpp final:", _fmt(float(v_track[-1]), "%.3f V"))

        # ── MPP Tracking (JV-SPO) ─────────────────────────────────────────
        elif dtype == "mpp":
            sm = section("Measurement Data")
            row(sm, "Cell area:", _fmt(data.get("cell_area"), "%.4f cm²"))
            row(sm, "Illumination:", _fmt(data.get("illumination"), "%.2f mW/cm²"))
            row(sm, "SPO duration:", _fmt(data.get("spo_duration_s"), "%.0f s/cycle"))
            row(sm, "Total duration:", _fmt(data.get("total_duration_min"), "%.1f min"))
            row(sm, "Cycles:", str(len(data.get("cycles", []))))

            si = section("Instrument Settings")
            row(si, "NPLC:", _fmt(data.get("nplc"), "%.2f"))
            row(si, "Delay/pt:", _fmt(data.get("delay_ms"), "%.0f ms"))

            cycles = data.get("cycles", [])
            pce_spo = data.get("pce_spo", np.array([]))
            if len(pce_spo):
                sr = section("SPO Results")
                row(sr, "PCE max:", _fmt(float(np.max(pce_spo)), "%.2f %%"), "#006064")
                row(sr, "PCE mean:", _fmt(float(np.mean(pce_spo)), "%.2f %%"))
                row(sr, "PCE final:", _fmt(float(pce_spo[-1]), "%.2f %%"))

            if cycles:
                sc = section("J-V Cycles")
                for cyc in cycles:
                    pce_v = _fmt(cyc.get("PCE"), "%.2f")
                    hi_v  = _fmt(cyc.get("HI"),  "%.1f")
                    row(sc, f"Cycle {cyc['cycle']}:",
                        f"PCE={pce_v}%  HI={hi_v}%")

        # ── Light-Intensity J-V ───────────────────────────────────────────
        elif dtype == "light_jv":
            results_list = data.get("results", [])
            n_planned    = data.get("n_intensities") or 0
            n_completed  = sum(1 for r in results_list if r.get("status") == "completed")

            sx = section("Experiment")
            sn = data.get("sample_name", "—") or "—"
            row(sx, "Sample:", (sn[:34] + "…") if len(sn) > 34 else sn)
            ts_raw = data.get("timestamp", "") or ""
            try:
                ts_disp = (f"{ts_raw[:4]}-{ts_raw[4:6]}-{ts_raw[6:8]} "
                           f"{ts_raw[9:11]}:{ts_raw[11:13]}:{ts_raw[13:15]}"
                           ) if len(ts_raw) >= 15 else ts_raw or "—"
            except Exception:
                ts_disp = ts_raw or "—"
            row(sx, "Timestamp:", ts_disp)
            idn = str(data.get("instrument") or "—")
            row(sx, "Instrument:", (idn[:34] + "…") if len(idn) > 34 else idn)
            abort_note = data.get("abort_note", "") or ""
            if abort_note:
                row(sx, "Note:",
                    (abort_note[:34] + "…") if len(abort_note) > 34 else abort_note,
                    fg="#E65100")

            ss = section("Scan Settings")
            sv = data.get("start_v"); ev = data.get("end_v")
            if sv is not None and ev is not None and not (math.isnan(sv) or math.isnan(ev)):
                row(ss, "V range:", f"{sv:.3f} → {ev:.3f} V")
            row(ss, "Step:",       _fmt(data.get("step_v"),   "%.4f V"))
            row(ss, "Delay:",      _fmt(data.get("delay_ms"), "%.0f ms"))
            row(ss, "NPLC:",       _fmt(data.get("nplc"),     "%.2f"))
            row(ss, "4-Wire:",     "ON" if data.get("four_wire") else "OFF")
            row(ss, "Cell area:",  _fmt(data.get("cell_area"), "%.4f cm²"))
            row(ss, "Ref. illum.", _fmt(data.get("ref_illum"), "%.1f mW/cm²"))

            sq = section("Sequence")
            row(sq, "Measured:", f"{n_completed} / {n_planned}")
            row(sq, "Stabiliz.:", _fmt(data.get("stabilization_s"), "%.0f s"))
            if data.get("prebias_enabled"):
                row(sq, "Pre-bias:",
                    f"{_fmt(data.get('prebias_v'), '%.2f')} V, "
                    f"{_fmt(data.get('prebias_t'), '%.1f')} s")
            else:
                row(sq, "Pre-bias:", "OFF", fg="#9e9e9e")
            if data.get("ls_enabled"):
                row(sq, "Light soak:", _fmt(data.get("ls_dur"), "%.0f s"))
            else:
                row(sq, "Light soak:", "OFF", fg="#9e9e9e")

            pce_revs = [
                r["PCE_rev"] for r in results_list
                if r.get("status") == "completed" and not math.isnan(r.get("PCE_rev", float("nan")))
            ]
            if pce_revs:
                sbp = section("Best Result")
                row(sbp, "Max PCE (rev):", _fmt(max(pce_revs), "%.2f %%"), "#1565C0")
                best = max(results_list,
                           key=lambda r: r.get("PCE_rev", float("nan"))
                           if not math.isnan(r.get("PCE_rev", float("nan"))) else -999)
                if not math.isnan(best.get("PCE_rev", float("nan"))):
                    row(sbp, "@ intensity:", f"{best['intensity']:.4g} sun")
                    row(sbp, "Voc (rev):", _fmt(best.get("Voc_rev"), "%.4f V"))
                    row(sbp, "Jsc (rev):", _fmt(best.get("Jsc_rev"), "%.4f mA/cm²"))
                    row(sbp, "FF (rev):",  _fmt(best.get("FF_rev"), "%.2f %%"))

            # ── Light ideality factor ─────────────────────────────────────
            ideality = data.get("ideality")
            if ideality is not None:
                sid = section("Light Ideality Factor")
                n_fwd    = ideality.get("n_idL_fwd")
                n_rev    = ideality.get("n_idL_rev")
                e_fwd    = ideality.get("n_idL_fwd_err")
                e_rev    = ideality.get("n_idL_rev_err")
                r2_fwd   = ideality.get("r2_fwd")
                r2_rev   = ideality.get("r2_rev")

                if n_fwd is not None and not math.isnan(n_fwd):
                    if e_fwd is not None and not math.isnan(e_fwd):
                        row(sid, "n_idL (fwd):", f"{n_fwd:.4f} ± {e_fwd:.4f}", "#1565C0")
                    else:
                        row(sid, "n_idL (fwd):", f"{n_fwd:.4f}", "#1565C0")
                if n_rev is not None and not math.isnan(n_rev):
                    if e_rev is not None and not math.isnan(e_rev):
                        row(sid, "n_idL (rev):", f"{n_rev:.4f} ± {e_rev:.4f}", "#B71C1C")
                    else:
                        row(sid, "n_idL (rev):", f"{n_rev:.4f}", "#B71C1C")
                if r2_fwd is not None and not math.isnan(r2_fwd):
                    row(sid, "R² (fwd):", f"{r2_fwd:.6f}")
                if r2_rev is not None and not math.isnan(r2_rev):
                    row(sid, "R² (rev):", f"{r2_rev:.6f}")

                w_fwd = ideality.get("warning_fwd", "")
                w_rev = ideality.get("warning_rev", "")
                if w_fwd and w_fwd.upper() not in ("OK", ""):
                    row(sid, "⚠ Fwd:", (w_fwd[:34] + "…") if len(w_fwd) > 34 else w_fwd,
                        fg="#E65100")
                if w_rev and w_rev.upper() not in ("OK", ""):
                    row(sid, "⚠ Rev:", (w_rev[:34] + "…") if len(w_rev) > 34 else w_rev,
                        fg="#E65100")

                # Embedded Voc vs ln(L_norm) plot
                l_norm_arr  = ideality.get("l_norm",   np.array([]))
                voc_fwd_arr = ideality.get("voc_fwd",  np.array([]))
                voc_rev_arr = ideality.get("voc_rev",  np.array([]))
                if len(l_norm_arr) > 1 and np.any(l_norm_arr > 0):
                    try:
                        voc_fig = Figure(figsize=(2.7, 2.0), dpi=90, tight_layout=True)
                        ax_voc  = voc_fig.add_subplot(111)

                        mask = l_norm_arr > 0
                        ln_l = np.log(l_norm_arr[mask])

                        if len(voc_fwd_arr) == len(l_norm_arr):
                            ax_voc.scatter(ln_l, voc_fwd_arr[mask], s=20,
                                           color="#1565C0", zorder=3, label="Fwd")
                            slope_fwd = ideality.get("slope_fwd")
                            if slope_fwd is not None and not math.isnan(slope_fwd):
                                ln_line = np.linspace(ln_l.min(), ln_l.max(), 60)
                                b = (float(np.mean(voc_fwd_arr[mask]))
                                     - slope_fwd * float(np.mean(ln_l)))
                                ax_voc.plot(ln_line, slope_fwd * ln_line + b,
                                            color="#1565C0", linewidth=1.2,
                                            linestyle="--", alpha=0.75)

                        if len(voc_rev_arr) == len(l_norm_arr):
                            ax_voc.scatter(ln_l, voc_rev_arr[mask], s=20,
                                           color="#B71C1C", zorder=3, label="Rev")
                            slope_rev = ideality.get("slope_rev")
                            if slope_rev is not None and not math.isnan(slope_rev):
                                ln_line = np.linspace(ln_l.min(), ln_l.max(), 60)
                                b = (float(np.mean(voc_rev_arr[mask]))
                                     - slope_rev * float(np.mean(ln_l)))
                                ax_voc.plot(ln_line, slope_rev * ln_line + b,
                                            color="#B71C1C", linewidth=1.2,
                                            linestyle="--", alpha=0.75)

                        ax_voc.set_xlabel("ln(L/L_max)", fontsize=7)
                        ax_voc.set_ylabel("Voc (V)", fontsize=7)
                        ax_voc.tick_params(labelsize=6)
                        ax_voc.grid(True, alpha=0.4)
                        ax_voc.legend(fontsize=6, loc="upper left",
                                      framealpha=0.7)

                        self._voc_canvas = FigureCanvasTkAgg(
                            voc_fig, master=self._meta_inner
                        )
                        self._voc_canvas.draw()
                        self._voc_canvas.get_tk_widget().pack(
                            fill=X, padx=6, pady=(4, 0)
                        )
                        self._voc_canvas.get_tk_widget().bind(
                            "<MouseWheel>", self._on_mousewheel
                        )
                    except Exception:
                        pass

            # ── Individual scan dropdown ──────────────────────────────────
            jv_data      = data.get("jv_data", {})
            jv_keys_avail = set(jv_data.keys())
            completed_with_jv = [
                r for r in results_list
                if r.get("status") == "completed"
                and f"{r['intensity']:.4g}" in jv_keys_avail
            ]
            if completed_with_jv:
                sc_int = section("Individual Scan")
                choices = ["— Overview —"] + [
                    f"{r['intensity']:.4g} sun" for r in completed_with_jv
                ]
                self._lij_key_map = {
                    "— Overview —": None,
                    **{
                        f"{r['intensity']:.4g} sun": f"{r['intensity']:.4g}"
                        for r in completed_with_jv
                    },
                }
                self._lij_intensity_var = StringVar(value="— Overview —")
                combo = ttk.Combobox(
                    sc_int,
                    textvariable=self._lij_intensity_var,
                    values=choices,
                    state="readonly",
                    width=22,
                )
                combo.pack(fill=X, padx=2, pady=4)
                combo.bind("<<ComboboxSelected>>",
                           lambda e: self._on_lij_intensity_select())

        self._meta_inner.update_idletasks()
        self._meta_canvas.configure(scrollregion=self._meta_canvas.bbox("all"))
        self._meta_canvas.yview_moveto(0)

    def _populate_fit_metadata_section(self, section, row, fit):
        """Append Single-Diode Model Fit sub-section to the metadata panel."""
        sf = section("Single-Diode Model Fit")
        params  = fit.get("parameters", {})
        errors  = fit.get("std_errors",  {})
        bounded = fit.get("bounded_parameters", [])

        for name in _FIT_PARAM_NAMES:
            val = params.get(name)
            err = errors.get(name)
            unit = _FIT_PARAM_UNITS.get(name, "")
            if val is not None and not math.isnan(val):
                if err is not None and not math.isnan(err):
                    display = f"{val:.4g} ± {err:.2g} {unit}"
                else:
                    display = f"{val:.4g} {unit}"
                fg = "#E65100" if name in bounded else "#2c3e50"
                row(sf, f"{name}:", display, fg=fg)

        r2 = fit.get("r_squared")
        if r2 is not None and not math.isnan(r2):
            row(sf, "R²:", f"{r2:.6f}")
        rmse = fit.get("rmse")
        if rmse is not None and not math.isnan(rmse):
            row(sf, "RMSE:", f"{rmse:.4f} mA/cm²")
        ts = fit.get("timestamp", "")
        if ts:
            row(sf, "Fitted:", ts[:16])
        if bounded:
            row(sf, "⚠ Bounded:", ", ".join(bounded), fg="#E65100")

    def _populate_dark_ideality_metadata_section(self, section, row, dfit):
        """Append Dark Ideality Fit sub-section to the metadata panel."""
        sf = section("Dark Ideality Fit")
        n_idd = dfit.get("n_idd")
        n_err = dfit.get("n_idd_err")
        j_s   = dfit.get("J_s")
        j_err = dfit.get("J_s_err")
        if n_idd is not None and not math.isnan(n_idd):
            if n_err is not None and not math.isnan(n_err):
                row(sf, "n_idd:", f"{n_idd:.4f} ± {n_err:.4f}", "#00695C")
            else:
                row(sf, "n_idd:", f"{n_idd:.4f}", "#00695C")
        if j_s is not None and not math.isnan(j_s):
            if j_err is not None and not math.isnan(j_err):
                row(sf, "J_s:", f"{j_s:.3e} ± {j_err:.1e} mA/cm²")
            else:
                row(sf, "J_s:", f"{j_s:.3e} mA/cm²")
        r2 = dfit.get("r_squared")
        if r2 is not None and not math.isnan(r2):
            row(sf, "R²:", f"{r2:.6f}")
        ts = dfit.get("timestamp", "")
        if ts:
            row(sf, "Fitted:", ts[:16])

    # ── Single-Diode Fit button ───────────────────────────────────────────────

    def _on_single_diode_fit(self):
        if self._current_data is None or self._current_data.get("type") != "illuminated":
            return
        v_r = self._current_data.get("v_r", np.array([]))
        j_r = self._current_data.get("j_r", np.array([]))
        if len(v_r) < 5:
            messagebox.showwarning(
                "Insufficient data",
                "The reverse scan has fewer than 5 points — cannot fit.",
                parent=self.win,
            )
            return

        initial_fit = self._current_data.get("single_diode_fit")
        basename = os.path.splitext(os.path.basename(self._current_filepath or ""))[0]

        SingleDiodeFitWindow(
            parent=self.win,
            v=v_r,
            j=j_r,
            filepath=self._current_filepath,
            initial_fit=initial_fit,
            basename=basename,
        )

    def _on_dark_ideality_fit(self):
        if self._current_data is None or self._current_data.get("type") != "dark":
            return
        # Use reverse scan if available, fall back to forward
        v = self._current_data.get("v_r", np.array([]))
        j = self._current_data.get("j_r", np.array([]))
        if len(v) < 5:
            v = self._current_data.get("v_f", np.array([]))
            j = self._current_data.get("j_f", np.array([]))
        if len(v) < 5:
            messagebox.showwarning(
                "Insufficient data",
                "The dark J-V scan has fewer than 5 points — cannot fit.",
                parent=self.win,
            )
            return
        basename = os.path.splitext(os.path.basename(self._current_filepath or ""))[0]
        DarkIdealityFitWindow(
            parent=self.win,
            v=v,
            j=j,
            filepath=self._current_filepath,
            basename=basename,
        )

    # ── Plot ──────────────────────────────────────────────────────────────────

    def _draw_plot(self, data, filename):
        import mplcursors

        if self._pick_cid is not None:
            self._fig.canvas.mpl_disconnect(self._pick_cid)
            self._pick_cid = None
        if self._cursor is not None:
            try:
                self._cursor.remove()
            except Exception:
                pass
            self._cursor = None

        self._fig.clf()
        _is_lijv_ov = (
            data.get("type") == "light_jv"
            and (self._lij_intensity_var is None
                 or self._lij_intensity_var.get() == "— Overview —")
        )
        if _is_lijv_ov:
            self._ax     = self._fig.add_subplot(211)
            self._ax_voc = self._fig.add_subplot(212)
        else:
            self._ax     = self._fig.add_subplot(111)
            self._ax_voc = None
        self._ax2 = self._ax.twinx()
        self._ax2.yaxis.set_label_position("right")

        dtype    = data["type"]
        title    = os.path.splitext(filename)[0]
        all_lines = []

        if dtype in ("illuminated", "dark"):
            self._draw_jv(data, title, all_lines)

        elif dtype == "stab":
            self._draw_stab(data, title, all_lines)

        elif dtype == "pure_mpp":
            self._draw_pure_mpp(data, title, all_lines)

        elif dtype == "mpp":
            self._draw_mpp(data, title, all_lines)

        elif dtype == "light_jv":
            self._draw_light_jv(data, title, all_lines)

        # ── Clickable legend ──────────────────────────────────────────────
        h1, l1 = self._ax.get_legend_handles_labels()
        h2, l2 = self._ax2.get_legend_handles_labels()
        if h1 or h2:
            legend = self._ax.legend(
                h1 + h2, l1 + l2,
                loc="upper right", frameon=True, framealpha=0.9, fontsize=9,
            )
            _orig = h1 + h2

            def on_legend_click(event):
                if event.button != 1:
                    return
                if not legend.get_frame().get_bbox().contains(event.x, event.y):
                    return
                texts    = legend.get_texts()
                leglines = legend.get_lines()
                for i, txt in enumerate(texts):
                    hit, _ = txt.contains(event)
                    if not hit and i < len(leglines):
                        hit, _ = leglines[i].contains(event)
                    if hit and i < len(_orig):
                        visible = not _orig[i].get_visible()
                        _orig[i].set_visible(visible)
                        if i < len(leglines):
                            leglines[i].set_alpha(1.0 if visible else 0.25)
                        txt.set_alpha(1.0 if visible else 0.35)
                        self._fig_canvas.draw_idle()
                        return

            self._pick_cid = self._fig.canvas.mpl_connect(
                "button_press_event", on_legend_click
            )

        if all_lines:
            self._cursor = mplcursors.cursor(all_lines, hover=True, highlight=True)

            @self._cursor.connect("add")
            def on_hover(sel):
                lbl = sel.artist.get_label()
                x, y = sel.target[0], sel.target[1]
                sel.annotation.set_text(f"{lbl}\n{x:.3f}, {y:.3f}")
                sel.annotation.get_bbox_patch().set(
                    facecolor="white", alpha=0.92,
                    edgecolor="#90A4AE", linewidth=0.8,
                    boxstyle="round,pad=0.4",
                )
                sel.annotation.set_fontsize(9)

        self._fig_canvas.draw_idle()

    # ── Plot helpers (one per measurement type) ────────────────────────────────

    def _draw_jv(self, data, title, all_lines):
        v_f = data["v_f"];  j_f = data["j_f"]
        v_r = data["v_r"];  j_r = data["j_r"]

        if data["type"] == "illuminated":
            p_f = data["p_f"];  p_r = data["p_r"]

            l1, = self._ax.plot(v_f, j_f, color=_COLORS[0], linestyle="-",
                                marker="o", markersize=5,
                                markerfacecolor=_COLORS[0], markeredgecolor=_COLORS[0],
                                linewidth=1.8, label="Forward J-V", zorder=3)
            l2, = self._ax.plot(v_r, j_r, color=_COLORS[1], linestyle="--",
                                marker="o", markersize=5,
                                markerfacecolor="white", markeredgecolor=_COLORS[1],
                                markeredgewidth=1.5, linewidth=1.8,
                                label="Reverse J-V", zorder=3)
            l3, = self._ax2.plot(v_f, p_f, color=_COLORS[0], linestyle="-",
                                 marker="s", markersize=4,
                                 markerfacecolor=_COLORS[0], markeredgecolor=_COLORS[0],
                                 linewidth=1.2, alpha=0.5, label="Forward P", zorder=2)
            l4, = self._ax2.plot(v_r, p_r, color=_COLORS[1], linestyle="--",
                                 marker="s", markersize=4,
                                 markerfacecolor="white", markeredgecolor=_COLORS[1],
                                 markeredgewidth=1.2, linewidth=1.2, alpha=0.5,
                                 label="Reverse P", zorder=2)
            all_lines += [l1, l2, l3, l4]

            self._ax2.tick_params(right=True, labelright=True)
            self._ax2.set_ylabel("Power Density (mW/cm²)", labelpad=8)
            self._ax2.set_ylim(-10, 30)
            self._fig.subplots_adjust(left=0.12, right=0.88, top=0.88)

            ann_lines = []
            for k, lbl in [("PCE_f", "Fwd PCE"), ("PCE_r", "Rev PCE"),
                            ("Voc_r", "Voc"), ("Jsc_r", "Jsc"),
                            ("FF_r",  "FF"),   ("HI",    "HI")]:
                v = data.get(k)
                if v is not None:
                    fmt = "%.2f" if k not in ("Voc_r", "Jsc_r") else "%.4f"
                    unit = ("%" if k in ("PCE_f","PCE_r","FF_r","HI")
                            else "V" if k == "Voc_r"
                            else " mA/cm²")
                    ann_lines.append(f"{lbl} = {_fmt(v, fmt)} {unit}")
            if ann_lines:
                self._ax.text(
                    0.02, 0.97, "\n".join(ann_lines),
                    transform=self._ax.transAxes, va="top", ha="left",
                    fontsize=8.5, color="#2c3e50",
                    bbox=dict(facecolor="white", alpha=0.85,
                              edgecolor="#90A4AE", boxstyle="round,pad=0.4"),
                )
        else:
            l1, = self._ax.plot(v_f, j_f, color=_COLORS[4], linestyle="--",
                                linewidth=1.6, alpha=0.85, label="Dark Forward J-V", zorder=3)
            l2, = self._ax.plot(v_r, j_r, color=_COLORS[4], linestyle=":",
                                linewidth=1.6, alpha=0.85, label="Dark Reverse J-V", zorder=3)
            all_lines += [l1, l2]
            self._ax.axhline(0, color="#aaaaaa", linewidth=0.8, linestyle="--", zorder=1)
            self._ax2.tick_params(right=False, labelright=False)
            self._ax2.set_ylabel("")
            self._fig.subplots_adjust(left=0.12, right=0.95, top=0.88)

        self._ax.set_xlabel("Voltage (V)", labelpad=8)
        self._ax.set_ylabel("Current Density (mA/cm²)", labelpad=8)
        self._ax.set_title(title, fontsize=12, fontweight="bold", pad=12)
        self._ax.set_xlim(0, 1.2)
        self._ax.set_ylim(-10, 30)
        self._ax.grid(True)

    def _draw_stab(self, data, title, all_lines):
        times = data.get("times", np.array([]))
        j_arr = data.get("j_vals", np.array([]))
        pce   = data.get("pce_vals", np.array([]))

        l1, = self._ax.plot(times, j_arr, color=_COLORS[0], linewidth=1.4,
                            label="J (mA/cm²)")
        l2, = self._ax2.plot(times, pce, color=_COLORS[3], linewidth=1.4,
                             linestyle="--", label="PCE (%)")
        all_lines += [l1, l2]

        stab = data.get("stabilized_at")
        if stab is not None and len(j_arr):
            self._ax.axvline(x=stab, color="#2E7D32", linestyle=":", linewidth=1.2)
            self._ax.text(
                stab, float(np.max(j_arr)),
                f" ✓ {stab:.0f} s", color="#2E7D32", fontsize=8, va="top",
            )

        self._ax2.tick_params(right=True, labelright=True)
        self._ax2.set_ylabel("PCE (%)", labelpad=8, color=_COLORS[3])
        self._ax.set_xlabel("Time (s)", labelpad=8)
        self._ax.set_ylabel("Current Density (mA/cm²)", labelpad=8, color=_COLORS[0])
        self._ax.set_title(title, fontsize=12, fontweight="bold", pad=12)
        self._ax.grid(True)
        self._fig.subplots_adjust(left=0.12, right=0.88, top=0.88)

    def _draw_pure_mpp(self, data, title, all_lines):
        times_min = data.get("times_min", np.array([]))
        pce_track = data.get("pce_track", np.array([]))
        v_track   = data.get("v_track",   np.array([]))

        l1, = self._ax.plot(times_min, pce_track, color=_COLORS[3], linewidth=1.4,
                            label="PCE (%)")
        l2, = self._ax2.plot(times_min, v_track, color=_COLORS[4], linewidth=1.0,
                             linestyle="--", label="Vmpp (V)")
        all_lines += [l1, l2]

        self._ax2.tick_params(right=True, labelright=True)
        self._ax2.set_ylabel("Vmpp (V)", labelpad=8, color=_COLORS[4])
        self._ax.set_xlabel("Time (min)", labelpad=8)
        self._ax.set_ylabel("PCE (%)", labelpad=8, color=_COLORS[3])
        self._ax.set_title(title, fontsize=12, fontweight="bold", pad=12)
        self._ax.grid(True)
        self._fig.subplots_adjust(left=0.12, right=0.88, top=0.88)

    def _draw_mpp(self, data, title, all_lines):
        times_min = data.get("times_min", np.array([]))
        pce_spo   = data.get("pce_spo",   np.array([]))
        j_spo     = data.get("j_spo",     np.array([]))
        cycle_idx = data.get("cycle_idx", np.array([], dtype=int))

        t_plot, pce_plot, j_plot = [], [], []
        prev_cyc = None
        for t, pce, j, cyc in zip(times_min, pce_spo, j_spo, cycle_idx):
            if prev_cyc is not None and cyc != prev_cyc:
                t_plot.append(None); pce_plot.append(None); j_plot.append(None)
            t_plot.append(t); pce_plot.append(pce); j_plot.append(j)
            prev_cyc = cyc

        l1, = self._ax.plot(t_plot, pce_plot, color=_COLORS[5], linewidth=1.4,
                            label="PCE (%)")
        l2, = self._ax2.plot(t_plot, j_plot, color=_COLORS[0], linewidth=1.0,
                             linestyle="--", alpha=0.7, label="J (mA/cm²)")
        all_lines += [l1, l2]

        if len(cycle_idx) > 1:
            boundaries = np.where(np.diff(cycle_idx.astype(int)) != 0)[0]
            for bi in boundaries:
                t_b = float(times_min[bi + 1]) if bi + 1 < len(times_min) else None
                if t_b is not None:
                    self._ax.axvline(x=t_b, color="#9e9e9e", linestyle=":",
                                     linewidth=0.8, zorder=1)

        self._ax2.tick_params(right=True, labelright=True)
        self._ax2.set_ylabel("J (mA/cm²)", labelpad=8, color=_COLORS[0])
        self._ax.set_xlabel("Time (min)", labelpad=8)
        self._ax.set_ylabel("PCE (%)", labelpad=8, color=_COLORS[5])
        self._ax.set_title(title, fontsize=12, fontweight="bold", pad=12)
        self._ax.grid(True)
        self._fig.subplots_adjust(left=0.12, right=0.88, top=0.88)


    def _on_lij_intensity_select(self):
        """Redraw main plot when the user picks a different intensity from the dropdown."""
        if self._current_data is None:
            return
        filename = os.path.basename(self._current_filepath or "")
        self._draw_plot(self._current_data, filename)

    def _draw_light_jv(self, data, title, all_lines):
        sel     = self._lij_intensity_var.get() if self._lij_intensity_var else "— Overview —"
        jv_key  = self._lij_key_map.get(sel) if self._lij_key_map else None
        jv_data = data.get("jv_data", {})
        if jv_key and jv_key in jv_data:
            self._draw_light_jv_single(data, jv_key, sel, title, all_lines)
        else:
            self._draw_light_jv_overview(data, title, all_lines)

    def _draw_light_jv_overview(self, data, title, all_lines):
        """All completed reverse (or forward) J-V curves overlaid, coloured by intensity."""
        results_list = data.get("results", [])
        jv_data      = data.get("jv_data", {})

        completed = [
            r for r in results_list
            if r.get("status") == "completed"
            and f"{r['intensity']:.4g}" in jv_data
        ]

        if not completed:
            self._ax.set_title(
                f"{title}\n(no completed measurements)", fontsize=10
            )
            self._ax.grid(True)
            self._ax2.tick_params(right=False, labelright=False)
            self._fig.subplots_adjust(left=0.12, right=0.95, top=0.88)
            return

        for i, r in enumerate(completed):
            key   = f"{r['intensity']:.4g}"
            jvd   = jv_data[key]
            v     = jvd.get("v_rev", np.array([]))
            j     = jvd.get("j_rev", np.array([]))
            if len(v) == 0:
                v = jvd.get("v_fwd", np.array([]))
                j = jvd.get("j_fwd", np.array([]))
            color = _COLORS[i % len(_COLORS)]
            l, = self._ax.plot(v, j, color=color, linewidth=1.5,
                               label=f"{key} sun", zorder=3)
            all_lines.append(l)

        self._ax.axhline(0, color="#aaaaaa", linewidth=0.8, linestyle="--", zorder=1)
        self._ax.set_xlabel("Voltage (V)", labelpad=8)
        self._ax.set_ylabel("Current Density (mA/cm²)", labelpad=8)
        self._ax.set_title(
            f"{title}  —  all intensities (rev scan)",
            fontsize=10, fontweight="bold", pad=8,
        )
        self._ax.grid(True)
        self._ax2.tick_params(right=False, labelright=False)
        self._ax2.set_ylabel("")

        ideality = data.get("ideality")
        if self._ax_voc is not None and ideality is not None:
            self._draw_voc_subplot(ideality)
        elif self._ax_voc is not None:
            self._ax_voc.set_visible(False)

        self._fig.subplots_adjust(left=0.12, right=0.95, top=0.93, hspace=0.55)

    def _draw_voc_subplot(self, ideality):
        """Draw Voc vs ln(L/Lmax) with linear regression fit on self._ax_voc."""
        ax          = self._ax_voc
        l_norm_arr  = ideality.get("l_norm",   np.array([]))
        voc_fwd_arr = ideality.get("voc_fwd",  np.array([]))
        voc_rev_arr = ideality.get("voc_rev",  np.array([]))
        n_fwd       = ideality.get("n_idL_fwd")
        n_rev       = ideality.get("n_idL_rev")
        slope_fwd   = ideality.get("slope_fwd")
        slope_rev   = ideality.get("slope_rev")

        mask = l_norm_arr > 0
        if not np.any(mask):
            ax.set_visible(False)
            return

        ln_l    = np.log(l_norm_arr[mask])
        ln_line = np.linspace(ln_l.min(), ln_l.max(), 60)

        has_data = False

        if len(voc_fwd_arr) == len(l_norm_arr) and np.any(np.isfinite(voc_fwd_arr[mask])):
            lbl_fwd = f"Fwd  (n_idL = {n_fwd:.3f})" if (n_fwd and not math.isnan(n_fwd)) else "Fwd"
            ax.plot(ln_l, voc_fwd_arr[mask], "o", color="#1565C0",
                    markersize=5, label=lbl_fwd, zorder=3)
            if slope_fwd is not None and not math.isnan(float(slope_fwd)):
                b = float(np.mean(voc_fwd_arr[mask])) - float(slope_fwd) * float(np.mean(ln_l))
                ax.plot(ln_line, float(slope_fwd) * ln_line + b,
                        color="#1565C0", linewidth=1.5, linestyle="--", alpha=0.75)
            has_data = True

        if len(voc_rev_arr) == len(l_norm_arr) and np.any(np.isfinite(voc_rev_arr[mask])):
            lbl_rev = f"Rev  (n_idL = {n_rev:.3f})" if (n_rev and not math.isnan(n_rev)) else "Rev"
            ax.plot(ln_l, voc_rev_arr[mask], "s", color="#C62828",
                    markersize=5, label=lbl_rev, zorder=3)
            if slope_rev is not None and not math.isnan(float(slope_rev)):
                b = float(np.mean(voc_rev_arr[mask])) - float(slope_rev) * float(np.mean(ln_l))
                ax.plot(ln_line, float(slope_rev) * ln_line + b,
                        color="#C62828", linewidth=1.5, linestyle="--", alpha=0.75)
            has_data = True

        if not has_data:
            ax.set_visible(False)
            return

        ax.set_xlabel("ln(L / L_max)", labelpad=6)
        ax.set_ylabel("Voc (V)", labelpad=6)
        ax.set_title("Voc vs ln(L/L_max) — Light Ideality Factor", fontsize=10, pad=6)
        ax.grid(True, alpha=0.5)
        ax.legend(loc="upper left", fontsize=8.5, framealpha=0.9)

    def _draw_light_jv_single(self, data, jv_key, label, title, all_lines):
        """Forward + reverse J-V with power for one selected intensity."""
        jvd = data["jv_data"][jv_key]
        v_f = jvd.get("v_fwd", np.array([])); j_f = jvd.get("j_fwd", np.array([]))
        p_f = jvd.get("p_fwd", np.array([]))
        v_r = jvd.get("v_rev", np.array([])); j_r = jvd.get("j_rev", np.array([]))
        p_r = jvd.get("p_rev", np.array([]))

        result = next(
            (r for r in data.get("results", [])
             if f"{r['intensity']:.4g}" == jv_key),
            None,
        )

        has_power = False
        if len(v_f) > 0:
            l1, = self._ax.plot(
                v_f, j_f, color=_COLORS[0], linestyle="-",
                marker="o", markersize=5,
                markerfacecolor=_COLORS[0], markeredgecolor=_COLORS[0],
                linewidth=1.8, label="Forward J-V", zorder=3,
            )
            all_lines.append(l1)
            if len(p_f) > 0:
                l3, = self._ax2.plot(
                    v_f, p_f, color=_COLORS[0], linestyle="-",
                    marker="s", markersize=4, linewidth=1.2,
                    alpha=0.5, label="Forward P", zorder=2,
                )
                all_lines.append(l3)
                has_power = True

        if len(v_r) > 0:
            l2, = self._ax.plot(
                v_r, j_r, color=_COLORS[1], linestyle="--",
                marker="o", markersize=5,
                markerfacecolor="white", markeredgecolor=_COLORS[1],
                markeredgewidth=1.5, linewidth=1.8,
                label="Reverse J-V", zorder=3,
            )
            all_lines.append(l2)
            if len(p_r) > 0:
                l4, = self._ax2.plot(
                    v_r, p_r, color=_COLORS[1], linestyle="--",
                    marker="s", markersize=4,
                    markerfacecolor="white", markeredgecolor=_COLORS[1],
                    markeredgewidth=1.2, linewidth=1.2, alpha=0.5,
                    label="Reverse P", zorder=2,
                )
                all_lines.append(l4)
                has_power = True

        if has_power:
            self._ax2.tick_params(right=True, labelright=True)
            self._ax2.set_ylabel("Power Density (mW/cm²)", labelpad=8)
            self._ax2.set_ylim(-10, 30)
            self._fig.subplots_adjust(left=0.12, right=0.88, top=0.88)
        else:
            self._ax2.tick_params(right=False, labelright=False)
            self._ax2.set_ylabel("")
            self._fig.subplots_adjust(left=0.12, right=0.95, top=0.88)

        if result is not None:
            ann = []
            for k, lbl_a in [("PCE_fwd", "Fwd PCE"), ("PCE_rev", "Rev PCE"),
                              ("Voc_rev", "Voc"),      ("Jsc_rev", "Jsc"),
                              ("FF_rev",  "FF"),       ("HI",      "HI")]:
                val = result.get(k)
                if val is not None and not math.isnan(val):
                    fmt  = "%.4f" if k in ("Voc_rev", "Jsc_rev") else "%.2f"
                    unit = ("%" if k in ("PCE_fwd", "PCE_rev", "FF_rev", "HI")
                            else "V" if k == "Voc_rev" else " mA/cm²")
                    ann.append(f"{lbl_a} = {_fmt(val, fmt)} {unit}")
            if ann:
                self._ax.text(
                    0.02, 0.97, "\n".join(ann),
                    transform=self._ax.transAxes, va="top", ha="left",
                    fontsize=8.5, color="#2c3e50",
                    bbox=dict(facecolor="white", alpha=0.85,
                              edgecolor="#90A4AE", boxstyle="round,pad=0.4"),
                )

        self._ax.axhline(0, color="#aaaaaa", linewidth=0.8, linestyle="--", zorder=1)
        self._ax.set_xlabel("Voltage (V)", labelpad=8)
        self._ax.set_ylabel("Current Density (mA/cm²)", labelpad=8)
        self._ax.set_title(
            f"{title}  —  {label}",
            fontsize=11, fontweight="bold", pad=12,
        )
        self._ax.set_xlim(0, 1.2)
        self._ax.set_ylim(-10, 30)
        self._ax.grid(True)


# ── SingleDiodeFitWindow ──────────────────────────────────────────────────────

class SingleDiodeFitWindow:
    """Interactive single-diode model fit window (Toplevel).

    Shows the measured J-V (reverse scan), the fitted curve, and residuals.
    Allows the user to edit temperature and bounds, refit, and export results.
    """

    _DEFAULT_BOUNDS = {
        "J_ph": ("0.5 × Jsc", "2.0 × Jsc"),
        "J0":   ("1e-15",     "1e-6"),
        "n":    ("0.8",       "5.0"),
        "Rs":   ("0.01",      "100.0"),
        "Rsh":  ("10.0",      "1e8"),
    }

    def __init__(
        self,
        parent,
        v: np.ndarray,
        j: np.ndarray,
        filepath: str | None,
        initial_fit=None,
        basename: str = "measurement",
    ):
        self._parent   = parent
        self._v        = v
        self._j        = j
        self._filepath = filepath
        self._basename = basename
        self._fit      = None
        self._fit_v    = None   # dense voltage array for plotting the fitted curve
        self._fitting  = False  # guard against double-click

        self.win = Toplevel(parent)
        self.win.title("Single-Diode Fit")
        self.win.geometry("1100x680")
        self.win.minsize(900, 580)
        self.win.transient(parent)
        self.win.grab_set()

        self._build_ui()

        # If file already contained a fit, display it immediately
        if initial_fit is not None:
            self._apply_fit_dict(initial_fit)
        else:
            self._run_fit_async()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        # Main layout: plots on left, controls on right
        paned = Frame(self.win)
        paned.pack(fill=BOTH, expand=True)

        # Left: two plots stacked
        plot_frame = Frame(paned)
        plot_frame.pack(side=LEFT, fill=BOTH, expand=True)

        self._fig = Figure(figsize=(7, 6), tight_layout=True)
        self._ax_jv  = self._fig.add_subplot(2, 1, 1)
        self._ax_res = self._fig.add_subplot(2, 1, 2)
        self._canvas = FigureCanvasTkAgg(self._fig, plot_frame)
        self._canvas.get_tk_widget().pack(fill=BOTH, expand=True)

        # Right: scrollable controls panel
        ctrl_outer = Frame(paned, width=300, bg="#f5f5f5")
        ctrl_outer.pack(side=RIGHT, fill=Y, expand=False)
        ctrl_outer.pack_propagate(False)

        ctrl_canvas = Canvas(ctrl_outer, bg="#f5f5f5", highlightthickness=0)
        vsb = ttk.Scrollbar(ctrl_outer, orient="vertical", command=ctrl_canvas.yview)
        ctrl_canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=RIGHT, fill=Y)
        ctrl_canvas.pack(side=LEFT, fill=BOTH, expand=True)

        self._ctrl_inner = Frame(ctrl_canvas, bg="#f5f5f5")
        _win_id = ctrl_canvas.create_window((0, 0), window=self._ctrl_inner, anchor="nw")
        self._ctrl_inner.bind(
            "<Configure>",
            lambda e: ctrl_canvas.configure(scrollregion=ctrl_canvas.bbox("all")),
        )
        ctrl_canvas.bind(
            "<Configure>",
            lambda e: ctrl_canvas.itemconfig(_win_id, width=e.width),
        )

        self._build_controls(self._ctrl_inner)

    def _build_controls(self, parent):
        bg = "#f5f5f5"
        pad = {"padx": 8, "pady": 4}

        def sec(title):
            lf = LabelFrame(parent, text=title, bg=bg,
                            font=("Arial", 9, "bold"), padx=6, pady=4)
            lf.pack(fill=X, **pad)
            return lf

        # ── Temperature ───────────────────────────────────────────────────────
        tf = sec("Temperature")
        row_t = Frame(tf, bg=bg)
        row_t.pack(fill=X)

        Label(row_t, text="T (K):", bg=bg, font=("Arial", 9)).pack(side=LEFT)
        self._temp_var = DoubleVar(value=298.15)
        temp_entry = Entry(row_t, textvariable=self._temp_var, width=9)
        temp_entry.pack(side=LEFT, padx=(4, 0))

        self._celsius_lbl = Label(row_t, text="= 25.00 °C", bg=bg,
                                  fg="#607d8b", font=("Arial", 9))
        self._celsius_lbl.pack(side=LEFT, padx=(6, 0))
        self._temp_var.trace("w", self._on_temp_change)
        self._on_temp_change()

        # ── Advanced bounds (collapsed) ───────────────────────────────────────
        self._bounds_visible = False
        bounds_hdr = Frame(parent, bg=bg)
        bounds_hdr.pack(fill=X, padx=8, pady=(4, 0))
        self._bounds_toggle_btn = Button(
            bounds_hdr, text="► Advanced bounds",
            command=self._toggle_bounds,
            relief="flat", bg="#e0e0e0", fg="#2c3e50",
            font=("Arial", 9), anchor=W,
        )
        self._bounds_toggle_btn.pack(fill=X)

        self._bounds_frame = LabelFrame(parent, text="Bounds", bg=bg,
                                        font=("Arial", 9, "bold"), padx=6, pady=4)
        # Hidden until user expands
        self._bound_lo: dict[str, Entry] = {}
        self._bound_hi: dict[str, Entry] = {}
        self._bound_lo_var: dict[str, StringVar] = {}
        self._bound_hi_var: dict[str, StringVar] = {}
        for name, (lo_def, hi_def) in self._DEFAULT_BOUNDS.items():
            rf = Frame(self._bounds_frame, bg=bg)
            rf.pack(fill=X, pady=2)
            Label(rf, text=f"{name}:", bg=bg, font=("Arial", 9), width=5,
                  anchor=W).pack(side=LEFT)
            lo_var = StringVar(value=lo_def)
            hi_var = StringVar(value=hi_def)
            self._bound_lo_var[name] = lo_var
            self._bound_hi_var[name] = hi_var
            Entry(rf, textvariable=lo_var, width=10).pack(side=LEFT, padx=2)
            Label(rf, text="–", bg=bg).pack(side=LEFT)
            Entry(rf, textvariable=hi_var, width=10).pack(side=LEFT, padx=2)

        # ── Action buttons ────────────────────────────────────────────────────
        bf = sec("Actions")
        self._refit_btn = Button(
            bf, text="Refit",
            command=self._run_fit_async,
            bg="#1565C0", fg="white", relief="flat",
            font=("Arial", 9, "bold"), pady=3,
        )
        self._refit_btn.pack(fill=X, pady=2)

        Button(
            bf, text="Export fit (CSV)",
            command=self._export_csv,
            bg="#455a64", fg="white", relief="flat",
            font=("Arial", 9), pady=3,
        ).pack(fill=X, pady=2)

        self._save_file_btn = Button(
            bf, text="Save fit to file",
            command=self._save_to_file,
            bg="#1B5E20", fg="white", relief="flat",
            font=("Arial", 9), pady=3,
            state=NORMAL if self._filepath else DISABLED,
        )
        self._save_file_btn.pack(fill=X, pady=2)

        # ── Parameter table ───────────────────────────────────────────────────
        self._param_frame = sec("Fitted Parameters")
        self._param_labels: dict[str, Label] = {}
        header = Frame(self._param_frame, bg=bg)
        header.pack(fill=X)
        for col, txt in enumerate(["Param", "Value", "± Error"]):
            Label(header, text=txt, bg="#dce3ea", fg="#2c3e50",
                  font=("Arial", 8, "bold"), width=9, anchor=W,
                  relief="flat").grid(row=0, column=col, padx=1)

        self._table_rows: dict[str, dict] = {}
        for name in _FIT_PARAM_NAMES:
            rf = Frame(self._param_frame, bg=bg)
            rf.pack(fill=X, pady=1)
            name_lbl = Label(rf, text=name, bg=bg, fg="#2c3e50",
                             font=("Arial", 9), width=5, anchor=W)
            val_lbl  = Label(rf, text="—", bg=bg, fg="#2c3e50",
                             font=("Arial", 9), width=11, anchor=W)
            err_lbl  = Label(rf, text="—", bg=bg, fg="#607d8b",
                             font=("Arial", 9), width=11, anchor=W)
            name_lbl.pack(side=LEFT, padx=(4, 0))
            val_lbl.pack(side=LEFT)
            err_lbl.pack(side=LEFT)
            self._table_rows[name] = {"name": name_lbl, "val": val_lbl, "err": err_lbl}

        # R², RMSE, RMSE_log
        self._metric_lbl = Label(self._param_frame, text="", bg=bg,
                                 fg="#607d8b", font=("Arial", 8),
                                 justify=LEFT, anchor=W)
        self._metric_lbl.pack(fill=X, padx=4, pady=(4, 0))

        # ── Warning indicator ─────────────────────────────────────────────────
        self._warn_lbl = Label(parent, text="", bg=bg, fg="#E65100",
                               font=("Arial", 9, "bold"), anchor=W, justify=LEFT)
        self._warn_lbl.pack(fill=X, padx=8, pady=(4, 0))

        # ── Status bar ────────────────────────────────────────────────────────
        self._status_lbl = Label(parent, text="Ready", bg="#37474F", fg="#CFD8DC",
                                 font=("Arial", 8), anchor=W)
        self._status_lbl.pack(fill=X, side="bottom", padx=0, pady=0)

    def _toggle_bounds(self):
        if self._bounds_visible:
            self._bounds_frame.pack_forget()
            self._bounds_toggle_btn.config(text="► Advanced bounds")
            self._bounds_visible = False
        else:
            self._bounds_frame.pack(fill=X, padx=8, pady=(0, 4))
            self._bounds_toggle_btn.config(text="▼ Advanced bounds")
            self._bounds_visible = True

    def _on_temp_change(self, *_):
        try:
            t_k = self._temp_var.get()
            t_c = t_k - 273.15
            self._celsius_lbl.config(text=f"= {t_c:.2f} °C")
        except Exception:
            self._celsius_lbl.config(text="")

    # ── Fitting ───────────────────────────────────────────────────────────────

    def _parse_bounds(self):
        """Parse user-editable bounds fields.  Returns None on error."""
        j_sc = float(abs(self._j[np.argmin(np.abs(self._v))]))
        bounds = {}
        for name in _FIT_PARAM_NAMES:
            try:
                lo = self._parse_one_bound(
                    self._bound_lo_var[name].get(), j_sc)
                hi = self._parse_one_bound(
                    self._bound_hi_var[name].get(), j_sc)
            except Exception:
                return None
            bounds[name] = (lo, hi)
        return bounds

    @staticmethod
    def _parse_one_bound(raw: str, j_sc: float) -> float:
        """Parse a single bound string.

        If the raw string contains "Jsc" (e.g. "0.5 × Jsc"), the numeric
        coefficient is extracted and multiplied by j_sc.  Otherwise the
        string is parsed as a plain float.  The "Jsc" check happens BEFORE
        any stripping so the detection is not defeated by the strip itself.
        """
        raw = raw.strip()
        if "Jsc" in raw:
            coeff = raw.replace("× Jsc", "").replace("×Jsc", "").replace("Jsc", "").strip()
            return float(coeff) * j_sc
        return float(raw)

    def _run_fit_async(self):
        if self._fitting:
            return
        self._fitting = True
        self._refit_btn.config(state=DISABLED, text="Fitting…")
        self._status_lbl.config(text="Running single-diode fit…")
        self.win.config(cursor="watch")

        T = 298.15
        try:
            T = self._temp_var.get()
        except Exception:
            pass

        bounds = self._parse_bounds()

        def _do_fit():
            from app.measurement.analysis import fit_single_diode
            try:
                result = fit_single_diode(
                    self._v, self._j, T=T,
                    bounds=bounds if bounds else None,
                )
            except Exception as exc:
                result = None
                self.win.after(0, lambda: self._on_fit_error(str(exc)))
                return
            self.win.after(0, lambda r=result: self._on_fit_done(r))

        threading.Thread(target=_do_fit, daemon=True).start()

    def _on_fit_done(self, result):
        from app.measurement.analysis import SingleDiodeFit
        self._fitting = False
        self._refit_btn.config(state=NORMAL, text="Refit")
        self.win.config(cursor="")
        if result is None:
            return
        self._fit = result
        self._status_lbl.config(
            text=f"Fit {'converged' if result.converged else 'did not converge'}"
            f"  |  R² = {result.r_squared:.6f}"
        )
        self._update_table(result)
        self._update_plots(result)

    def _on_fit_error(self, msg):
        self._fitting = False
        self._refit_btn.config(state=NORMAL, text="Refit")
        self.win.config(cursor="")
        self._status_lbl.config(text=f"Fit error: {msg[:80]}")

    def _apply_fit_dict(self, fit_dict: dict):
        """Populate the UI from a dict loaded from the Excel file."""
        from app.measurement.analysis import SingleDiodeFit
        try:
            temp_k = fit_dict.get("temperature_K", 298.15)
            self._temp_var.set(float(temp_k))
        except Exception:
            pass

        # Build a mock SingleDiodeFit from the dict
        fit = SingleDiodeFit(
            parameters=fit_dict.get("parameters", {}),
            std_errors=fit_dict.get("std_errors", {}),
            r_squared=fit_dict.get("r_squared", float("nan")),
            rmse=fit_dict.get("rmse", float("nan")),
            rmse_log=fit_dict.get("rmse_log", float("nan")),
            converged=True,
            method=fit_dict.get("method", ""),
            bounded_parameters=fit_dict.get("bounded_parameters", []),
            temperature_K=float(fit_dict.get("temperature_K", 298.15)),
            timestamp=fit_dict.get("timestamp", ""),
        )
        self._fit = fit
        self._status_lbl.config(
            text=f"Loaded from file  |  R² = {fit.r_squared:.6f}"
        )
        self._update_table(fit)
        self._update_plots(fit)

    # ── UI update helpers ─────────────────────────────────────────────────────

    def _update_table(self, fit):
        from app.measurement.analysis import SingleDiodeFit
        bounded = set(fit.bounded_parameters)
        for name in _FIT_PARAM_NAMES:
            val = fit.parameters.get(name)
            err = fit.std_errors.get(name)
            row = self._table_rows[name]
            if val is not None and not math.isnan(val):
                row["val"].config(text=f"{val:.4g}", fg="#E65100" if name in bounded else "#1565C0")
            else:
                row["val"].config(text="—", fg="#2c3e50")
            if err is not None and not math.isnan(err):
                row["err"].config(text=f"± {err:.2g}")
            else:
                row["err"].config(text="—")

        r2   = fit.r_squared
        rmse = fit.rmse
        rml  = fit.rmse_log
        self._metric_lbl.config(
            text=(
                f"R² = {r2:.6f}\n"
                f"RMSE = {rmse:.4f} mA/cm²\n"
                f"RMSE_log = {rml:.4f}"
                if all(not math.isnan(x) for x in [r2, rmse, rml]) else ""
            )
        )

        if bounded:
            self._warn_lbl.config(
                text="⚠ Bounded: " + ", ".join(sorted(bounded))
            )
        else:
            self._warn_lbl.config(text="")

    def _update_plots(self, fit):
        from app.measurement.analysis import _solve_j_sdm, _KB, _Q
        params = fit.parameters
        T      = fit.temperature_K
        vt     = _KB * T / _Q

        # Dense voltage grid for smooth fitted curve
        v_plot = np.linspace(float(np.min(self._v)), float(np.max(self._v)), 300)
        j_fit_plot = np.array([
            _solve_j_sdm(
                float(vi),
                params.get("J_ph", 0.0),
                params.get("J0",   1e-12),
                params.get("n",    1.5),
                params.get("Rs",   5.0),
                params.get("Rsh",  1e4),
                vt,
            )
            for vi in v_plot
        ])

        # J_fit at measured voltages for residuals
        j_fit_meas = np.array([
            _solve_j_sdm(
                float(vi),
                params.get("J_ph", 0.0),
                params.get("J0",   1e-12),
                params.get("n",    1.5),
                params.get("Rs",   5.0),
                params.get("Rsh",  1e4),
                vt,
            )
            for vi in self._v
        ])

        self._ax_jv.clear()
        self._ax_res.clear()

        # J-V overlay
        self._ax_jv.plot(self._v, self._j, "o", color=_COLORS[1],
                         markersize=5, label="Measured (Rev)", zorder=3)
        mask = np.isfinite(j_fit_plot)
        if mask.any():
            self._ax_jv.plot(v_plot[mask], j_fit_plot[mask],
                             "-", color=_COLORS[0], linewidth=2.0,
                             label="SDM fit", zorder=4)
        self._ax_jv.set_xlabel("Voltage (V)")
        self._ax_jv.set_ylabel("J (mA/cm²)")
        self._ax_jv.set_title("J-V  —  Single-Diode Model Fit", fontsize=10)
        self._ax_jv.legend(fontsize=8)
        self._ax_jv.grid(True, alpha=0.4)
        self._ax_jv.axhline(0, color="#aaaaaa", linewidth=0.7)

        # Residuals
        residuals = self._j - j_fit_meas
        self._ax_res.stem(
            self._v, residuals,
            linefmt="grey", markerfmt="o", basefmt=" ",
            label="J_meas - J_fit",
        )
        self._ax_res.axhline(0, color="#aaaaaa", linewidth=0.9)
        self._ax_res.set_xlabel("Voltage (V)")
        self._ax_res.set_ylabel("Residual (mA/cm²)")
        self._ax_res.set_title("Residuals", fontsize=10)
        self._ax_res.grid(True, alpha=0.4)

        self._fig.tight_layout()
        self._canvas.draw_idle()

    # ── Export helpers ────────────────────────────────────────────────────────

    def _export_csv(self):
        if self._fit is None:
            messagebox.showwarning("No fit", "Run a fit first.", parent=self.win)
            return
        suggested = f"{self._basename}_DiodeFit.csv"
        path = filedialog.asksaveasfilename(
            parent=self.win,
            title="Export fit parameters",
            initialfile=suggested,
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            fit = self._fit
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Parameter", "Value", "Std_Error", "Unit"])
                for name in _FIT_PARAM_NAMES:
                    writer.writerow([
                        name,
                        fit.parameters.get(name, ""),
                        fit.std_errors.get(name, ""),
                        _FIT_PARAM_UNITS.get(name, ""),
                    ])
                writer.writerow(["R2", fit.r_squared, "", ""])
                writer.writerow(["RMSE", fit.rmse, "", "mA/cm2"])
                writer.writerow(["RMSE_log", fit.rmse_log, "", "log10 scale"])
                writer.writerow([])
                writer.writerow(["Method",      fit.method])
                writer.writerow(["Temperature", fit.temperature_K, "", "K"])
                writer.writerow(["Timestamp",   fit.timestamp])
                writer.writerow(["Bounded",     ", ".join(fit.bounded_parameters)])
            self._status_lbl.config(text=f"Exported: {os.path.basename(path)}")
        except Exception as exc:
            messagebox.showerror("Export Error", str(exc), parent=self.win)

    def _save_to_file(self):
        if self._fit is None:
            messagebox.showwarning("No fit", "Run a fit first.", parent=self.win)
            return
        if not self._filepath:
            messagebox.showwarning("No file", "No source file path known.", parent=self.win)
            return
        try:
            from app.io.exporter import append_single_diode_fit_to_file
            append_single_diode_fit_to_file(self._filepath, self._fit)
            self._status_lbl.config(text="Saved to file.")
        except Exception as exc:
            messagebox.showerror("Save Error", str(exc), parent=self.win)


# ── DarkIdealityFitWindow ─────────────────────────────────────────────────────

class DarkIdealityFitWindow:
    """Interactive dark ideality factor extraction window (Toplevel).

    Shows |J| vs V on a semi-log scale with two draggable cursor lines
    delimiting the exponential region.  The fit updates live as cursors move.
    """

    _DEFAULT_VMIN = 0.4
    _DEFAULT_VMAX = 1.0
    _CURSOR_GRAB_TOL = 0.03   # V — how close a click must be to grab a cursor

    def __init__(
        self,
        parent,
        v: np.ndarray,
        j: np.ndarray,
        filepath: str | None,
        basename: str = "dark_jv",
    ):
        self._parent   = parent
        self._v        = np.asarray(v, dtype=float)
        self._j        = np.asarray(j, dtype=float)
        self._filepath = filepath
        self._basename = basename
        self._fit      = None
        self._dragging = None   # "left" | "right" | None

        # Clamp default window to data range
        v_data_min = float(np.min(self._v))
        v_data_max = float(np.max(self._v))
        self._vmin = max(self._DEFAULT_VMIN, v_data_min)
        self._vmax = min(self._DEFAULT_VMAX, v_data_max)
        if self._vmin >= self._vmax:
            self._vmin = v_data_min
            self._vmax = v_data_max

        self.win = Toplevel(parent)
        self.win.title("Dark Ideality Fit")
        self.win.geometry("1100x700")
        self.win.minsize(900, 580)
        self.win.transient(parent)
        self.win.grab_set()

        self._build_ui()
        self._run_fit()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        outer = Frame(self.win)
        outer.pack(fill=BOTH, expand=True)

        # Left: plots
        plot_frame = Frame(outer)
        plot_frame.pack(side=LEFT, fill=BOTH, expand=True)

        from matplotlib.gridspec import GridSpec
        self._fig = Figure(figsize=(7, 6), constrained_layout=True)
        gs = GridSpec(3, 1, figure=self._fig, hspace=0.45)
        self._ax_main = self._fig.add_subplot(gs[0:2, 0])
        self._ax_res  = self._fig.add_subplot(gs[2, 0])

        self._canvas = FigureCanvasTkAgg(self._fig, plot_frame)
        self._canvas.get_tk_widget().pack(fill=BOTH, expand=True)

        # Cursor drag events
        self._canvas.mpl_connect("button_press_event",   self._on_mouse_press)
        self._canvas.mpl_connect("motion_notify_event",  self._on_mouse_motion)
        self._canvas.mpl_connect("button_release_event", self._on_mouse_release)

        # Right: controls
        ctrl_outer = Frame(outer, width=290, bg="#f5f5f5")
        ctrl_outer.pack(side=RIGHT, fill=Y, expand=False)
        ctrl_outer.pack_propagate(False)

        ctrl_canvas = Canvas(ctrl_outer, bg="#f5f5f5", highlightthickness=0)
        vsb = ttk.Scrollbar(ctrl_outer, orient="vertical", command=ctrl_canvas.yview)
        ctrl_canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=RIGHT, fill=Y)
        ctrl_canvas.pack(side=LEFT, fill=BOTH, expand=True)

        self._ctrl_inner = Frame(ctrl_canvas, bg="#f5f5f5")
        _win_id = ctrl_canvas.create_window((0, 0), window=self._ctrl_inner, anchor="nw")
        self._ctrl_inner.bind(
            "<Configure>",
            lambda e: ctrl_canvas.configure(scrollregion=ctrl_canvas.bbox("all")),
        )
        ctrl_canvas.bind(
            "<Configure>",
            lambda e: ctrl_canvas.itemconfig(_win_id, width=e.width),
        )
        self._build_controls(self._ctrl_inner)

    def _build_controls(self, parent):
        bg  = "#f5f5f5"
        pad = {"padx": 8, "pady": 4}

        def sec(title):
            lf = LabelFrame(parent, text=title, bg=bg,
                            font=("Arial", 9, "bold"), padx=6, pady=4)
            lf.pack(fill=X, **pad)
            return lf

        # ── Temperature ───────────────────────────────────────────────────────
        tf = sec("Temperature")
        row_t = Frame(tf, bg=bg)
        row_t.pack(fill=X)
        Label(row_t, text="T (K):", bg=bg, font=("Arial", 9)).pack(side=LEFT)
        self._temp_var = DoubleVar(value=298.15)
        Entry(row_t, textvariable=self._temp_var, width=9).pack(side=LEFT, padx=(4, 0))
        self._celsius_lbl = Label(row_t, text="= 25.00 °C", bg=bg,
                                  fg="#607d8b", font=("Arial", 9))
        self._celsius_lbl.pack(side=LEFT, padx=(6, 0))
        self._temp_var.trace_add("write", lambda *_: self._on_temp_change())
        self._on_temp_change()

        # ── Fit Window ────────────────────────────────────────────────────────
        wf = sec("Fit Window")
        self._vmin_var = StringVar(value=f"{self._vmin:.3f}")
        self._vmax_var = StringVar(value=f"{self._vmax:.3f}")

        for label, var, attr in [
            ("V_min (V):", self._vmin_var, "_vmin"),
            ("V_max (V):", self._vmax_var, "_vmax"),
        ]:
            rf = Frame(wf, bg=bg)
            rf.pack(fill=X, pady=2)
            Label(rf, text=label, bg=bg, font=("Arial", 9), width=10,
                  anchor=W).pack(side=LEFT)
            e = Entry(rf, textvariable=var, width=9)
            e.pack(side=LEFT, padx=(4, 0))
            e.bind("<Return>", lambda _e, a=attr, sv=var: self._on_window_entry(a, sv))
            e.bind("<FocusOut>", lambda _e, a=attr, sv=var: self._on_window_entry(a, sv))

        # ── Actions ───────────────────────────────────────────────────────────
        af = sec("Actions")
        self._refit_btn = Button(
            af, text="Refit",
            command=self._run_fit,
            bg="#1565C0", fg="white", relief="flat",
            font=("Arial", 9, "bold"), pady=3,
        )
        self._refit_btn.pack(fill=X, pady=2)

        Button(
            af, text="Export fit (CSV)",
            command=self._export_csv,
            bg="#455a64", fg="white", relief="flat",
            font=("Arial", 9), pady=3,
        ).pack(fill=X, pady=2)

        self._save_btn = Button(
            af, text="Save fit to file",
            command=self._save_to_file,
            bg="#1B5E20", fg="white", relief="flat",
            font=("Arial", 9), pady=3,
            state=NORMAL if self._filepath else DISABLED,
        )
        self._save_btn.pack(fill=X, pady=2)

        # ── Fit Results ───────────────────────────────────────────────────────
        rf_sec = sec("Fit Results")
        self._result_lbl = Label(
            rf_sec, text="—", bg=bg, fg="#607d8b",
            font=("Arial", 9), justify=LEFT, anchor=W,
        )
        self._result_lbl.pack(fill=X, padx=4)

        self._warn_lbl = Label(
            parent, text="", bg=bg, fg="#E65100",
            font=("Arial", 9, "bold"), anchor=W, justify=LEFT,
        )
        self._warn_lbl.pack(fill=X, padx=8, pady=(4, 0))

        self._status_lbl = Label(
            parent, text="Ready", bg="#37474F", fg="#CFD8DC",
            font=("Arial", 8), anchor=W,
        )
        self._status_lbl.pack(fill=X, side="bottom", padx=0, pady=0)

    # ── Temperature display ───────────────────────────────────────────────────

    def _on_temp_change(self, *_):
        try:
            t_k = self._temp_var.get()
            self._celsius_lbl.config(text=f"= {t_k - 273.15:.2f} °C")
        except Exception:
            self._celsius_lbl.config(text="")

    # ── Window entry syncing ──────────────────────────────────────────────────

    def _on_window_entry(self, attr, sv):
        try:
            val = float(sv.get())
        except ValueError:
            return
        setattr(self, attr, val)
        self._run_fit()

    def _sync_entries(self):
        self._vmin_var.set(f"{self._vmin:.3f}")
        self._vmax_var.set(f"{self._vmax:.3f}")

    # ── Cursor drag ───────────────────────────────────────────────────────────

    def _on_mouse_press(self, event):
        if event.inaxes is not self._ax_main or event.button != 1:
            return
        x = event.xdata
        if x is None:
            return
        if abs(x - self._vmin) < self._CURSOR_GRAB_TOL:
            self._dragging = "left"
        elif abs(x - self._vmax) < self._CURSOR_GRAB_TOL:
            self._dragging = "right"

    def _on_mouse_motion(self, event):
        if self._dragging is None or event.inaxes is not self._ax_main:
            return
        x = event.xdata
        if x is None:
            return
        v_data_min = float(np.min(self._v))
        v_data_max = float(np.max(self._v))
        x = float(np.clip(x, v_data_min, v_data_max))
        if self._dragging == "left":
            self._vmin = min(x, self._vmax - 0.01)
        else:
            self._vmax = max(x, self._vmin + 0.01)
        self._sync_entries()
        self._run_fit()

    def _on_mouse_release(self, event):
        self._dragging = None

    # ── Fitting ───────────────────────────────────────────────────────────────

    def _run_fit(self):
        try:
            T = self._temp_var.get()
        except Exception:
            T = 298.15

        from app.measurement.analysis import fit_dark_ideality
        try:
            fit = fit_dark_ideality(
                self._v, self._j,
                v_min=self._vmin,
                v_max=self._vmax,
                T=T,
            )
        except ValueError as exc:
            self._fit = None
            self._status_lbl.config(text=f"Fit error: {str(exc)[:80]}")
            self._result_lbl.config(text="—")
            self._warn_lbl.config(text="")
            self._update_plots(None)
            return

        self._fit = fit
        self._status_lbl.config(
            text=f"n_idd = {fit.n_idd:.4f}  |  R² = {fit.r_squared:.6f}"
        )
        self._update_results_panel(fit)
        self._update_plots(fit)

    # ── UI update helpers ─────────────────────────────────────────────────────

    def _update_results_panel(self, fit):
        lines = [
            f"n_idd  = {fit.n_idd:.4f} ± {fit.n_idd_err:.4f}",
            f"J_s    = {fit.J_s:.3e} ± {fit.J_s_err:.1e} mA/cm²",
            f"R²     = {fit.r_squared:.6f}",
            f"RMSE_log = {fit.rmse_log:.5f}",
            f"Points: {fit.n_points}",
        ]
        self._result_lbl.config(text="\n".join(lines))

        low_quality = fit.r_squared < 0.995 or fit.n_points < 10
        self._warn_lbl.config(
            text="⚠ Low fit quality" if low_quality else "",
            fg="#E65100",
        )

    def _update_plots(self, fit):
        self._ax_main.clear()
        self._ax_res.clear()

        abs_j = np.abs(self._j)
        pos   = abs_j > 0
        v_pos = self._v[pos]
        j_pos = abs_j[pos]

        # ── Main semi-log plot ────────────────────────────────────────────────
        self._ax_main.semilogy(
            v_pos, j_pos, "o", color=_COLORS[4],
            markersize=4, alpha=0.8, label="|J| measured",
        )

        if fit is not None:
            # Fitted line over the window — straight line in log space
            v_line = np.linspace(self._vmin, self._vmax, 200)
            from app.measurement.analysis import _KB, _Q, _LN10
            vt = _KB * fit.temperature_K / _Q
            slope     = 1.0 / (fit.n_idd * vt * _LN10) if fit.n_idd > 0 else 0.0
            intercept = math.log10(fit.J_s) if fit.J_s > 0 else 0.0
            j_line    = 10.0 ** (intercept + slope * v_line)
            self._ax_main.semilogy(
                v_line, j_line, "-", color="#E65100", linewidth=2.0,
                label=f"Fit (n={fit.n_idd:.3f})", zorder=4,
            )

        # Cursor lines
        self._ax_main.axvline(
            self._vmin, color="#1565C0", linestyle="--", linewidth=1.5,
            label=f"V_min={self._vmin:.3f} V",
        )
        self._ax_main.axvline(
            self._vmax, color="#1565C0", linestyle="--", linewidth=1.5,
            label=f"V_max={self._vmax:.3f} V",
        )

        # Cursor V labels at top of axes
        y_top = self._ax_main.get_ylim()[1] if self._ax_main.get_ylim()[1] > 0 else 1.0
        for vx, lbl in [(self._vmin, f"{self._vmin:.3f}"), (self._vmax, f"{self._vmax:.3f}")]:
            self._ax_main.text(
                vx, y_top, lbl,
                ha="center", va="bottom", fontsize=7, color="#1565C0",
                transform=self._ax_main.get_xaxis_transform(),
            )

        self._ax_main.set_xlabel("Voltage (V)", fontsize=9)
        self._ax_main.set_ylabel("|J| (mA/cm²)", fontsize=9)
        self._ax_main.set_title("|J| vs V — Dark Ideality Fit", fontsize=10)
        self._ax_main.legend(fontsize=7, loc="upper left")
        self._ax_main.grid(True, which="both", alpha=0.3)

        # ── Residuals plot ────────────────────────────────────────────────────
        if fit is not None:
            window_mask = (self._v >= self._vmin) & (self._v <= self._vmax)
            v_w = self._v[window_mask]
            j_w = np.abs(self._j[window_mask])
            pos_w = j_w > 0
            v_w = v_w[pos_w]
            j_w = j_w[pos_w]

            if len(v_w) > 0:
                from app.measurement.analysis import _KB, _Q, _LN10
                vt = _KB * fit.temperature_K / _Q
                slope     = 1.0 / (fit.n_idd * vt * _LN10) if fit.n_idd > 0 else 0.0
                intercept = math.log10(fit.J_s) if fit.J_s > 0 else 0.0
                log_j_meas = np.log10(j_w)
                log_j_fit  = intercept + slope * v_w
                residuals  = log_j_meas - log_j_fit
                self._ax_res.stem(
                    v_w, residuals,
                    linefmt="grey", markerfmt="o", basefmt=" ",
                )

        self._ax_res.axhline(0, color="#aaaaaa", linewidth=0.9, linestyle="--")
        self._ax_res.set_xlabel("Voltage (V)", fontsize=9)
        self._ax_res.set_ylabel("Residual (log₁₀)", fontsize=9)
        self._ax_res.set_title("Residuals", fontsize=9)
        self._ax_res.grid(True, alpha=0.3)

        self._canvas.draw_idle()

    # ── Export helpers ────────────────────────────────────────────────────────

    def _export_csv(self):
        if self._fit is None:
            messagebox.showwarning("No fit", "Run a fit first.", parent=self.win)
            return
        suggested = f"{self._basename}_DarkIdealityFit.csv"
        path = filedialog.asksaveasfilename(
            parent=self.win,
            title="Export dark ideality fit",
            initialfile=suggested,
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            fit = self._fit
            # Windowed data for export
            window_mask = (self._v >= fit.v_min) & (self._v <= fit.v_max)
            v_w = self._v[window_mask]
            j_w = self._j[window_mask]

            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Parameter", "Value", "Std_Error", "Unit"])
                writer.writerow(["n_idd",     fit.n_idd,      fit.n_idd_err,  "—"])
                writer.writerow(["J_s",        fit.J_s,        fit.J_s_err,    "mA/cm2"])
                writer.writerow(["R2",          fit.r_squared,  "",             ""])
                writer.writerow(["RMSE_log",    fit.rmse_log,   "",             "log10 scale"])
                writer.writerow(["V_min",       fit.v_min,      "",             "V"])
                writer.writerow(["V_max",       fit.v_max,      "",             "V"])
                writer.writerow(["n_points",    fit.n_points,   "",             ""])
                writer.writerow(["Temperature", fit.temperature_K, "",          "K"])
                writer.writerow(["Method",      fit.method,     "",             ""])
                writer.writerow(["Timestamp",   fit.timestamp,  "",             ""])
                writer.writerow([])
                writer.writerow(["V (V)", "J (mA/cm2)", "|J| (mA/cm2)", "log10|J|"])
                for vi, ji in zip(v_w, j_w):
                    abs_ji = abs(ji)
                    log_ji = math.log10(abs_ji) if abs_ji > 0 else float("nan")
                    writer.writerow([vi, ji, abs_ji, log_ji])
            self._status_lbl.config(text=f"Exported: {os.path.basename(path)}")
        except Exception as exc:
            messagebox.showerror("Export Error", str(exc), parent=self.win)

    def _save_to_file(self):
        if self._fit is None:
            messagebox.showwarning("No fit", "Run a fit first.", parent=self.win)
            return
        if not self._filepath:
            messagebox.showwarning("No file", "No source file path known.", parent=self.win)
            return
        try:
            from app.io.exporter import append_dark_ideality_fit_to_file
            append_dark_ideality_fit_to_file(self._filepath, self._fit)
            self._status_lbl.config(text="Saved to file.")
        except Exception as exc:
            messagebox.showerror("Save Error", str(exc), parent=self.win)
