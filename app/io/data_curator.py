"""
Data Curation module — reads illuminated J-V xlsx files, applies thresholds,
and exports a curated workbook with Raw_Data, Curated_[param], and Statistics
sheets.

Column positions verified against app/io/exporter.py save_data():
  xlsxwriter 0-indexed col → openpyxl 1-indexed col (row 2 = scalar params)
  Voc_fwd H(8), Jsc_fwd I(9), FF_fwd J(10), PCE_fwd K(11), Rs_fwd L(12), Rsh_fwd M(13)
  Voc_rev N(14), Jsc_rev O(15), FF_rev P(16), PCE_rev Q(17), Rs_rev R(18), Rsh_rev S(19)
  HI T(20)
"""

import os
import re
from pathlib import Path
from datetime import datetime

import numpy as np
import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment
from openpyxl.utils import get_column_letter

_SHEET = "MeasurementData"

_COL = {
    "Voc_fwd": 8,
    "Jsc_fwd": 9,
    "FF_fwd":  10,
    "PCE_fwd": 11,
    "Rs_fwd":  12,
    "Rsh_fwd": 13,
    "Voc_rev": 14,
    "Jsc_rev": 15,
    "FF_rev":  16,
    "PCE_rev": 17,
    "Rs_rev":  18,
    "Rsh_rev": 19,
    "HI":      20,
}

PARAMS      = ["Voc", "Jsc", "FF", "PCE", "HI", "Rs", "Rsh"]
P_HEADERS   = ["Voc (V)", "Jsc (mA/cm²)", "FF (%)", "PCE (%)",
               "HI (%)", "Rs (Ω·cm²)", "Rsh (Ω·cm²)"]
P_FMT       = ["0.0000", "0.0000", "0.00", "0.0000",
               "0.00",   "0.0000", "0.0"]


class CellData:
    """All PV parameters for one illuminated J-V measurement file."""

    def __init__(self, filepath):
        self.filepath  = filepath
        self.filename  = Path(filepath).name
        self.device    = self._extract_device()
        self.substrate = self._extract_substrate()

        self.Voc_fwd = self.Jsc_fwd = self.FF_fwd = self.PCE_fwd = 0.0
        self.Rs_fwd  = self.Rsh_fwd = 0.0
        self.Voc_rev = self.Jsc_rev = self.FF_rev = self.PCE_rev = 0.0
        self.Rs_rev  = self.Rsh_rev = 0.0
        self.HI      = 0.0

    def _extract_device(self):
        stem  = Path(self.filepath).stem
        parts = stem.split("_")
        for i, p in enumerate(parts):
            if len(p) == 8 and p.isdigit():
                return "_".join(parts[:i]) or stem
        return parts[0] if parts else stem

    def _extract_substrate(self):
        cleaned = re.sub(r"C\d+$", "", self.device)
        return cleaned if cleaned else self.device

    def get_val(self, param, scan):
        """Return value for param at the given scan direction ('fwd' or 'rev')."""
        if param == "HI":
            return self.HI
        return getattr(self, f"{param}_{scan}", 0.0)

    def passes(self, param, mn, mx, scan="rev"):
        """Return True if this cell passes [mn, mx] for param.

        scan='both' requires fwd AND rev each to be within [mn, mx].
        """
        if scan == "both":
            return (mn <= self.get_val(param, "fwd") <= mx and
                    mn <= self.get_val(param, "rev") <= mx)
        return mn <= self.get_val(param, scan) <= mx


def load_cell_data(filepath):
    """Read a MeasurementData xlsx file.  Returns CellData or None on failure."""
    try:
        wb = openpyxl.load_workbook(filepath, data_only=True)
        if _SHEET not in wb.sheetnames:
            wb.close()
            return None
        ws = wb[_SHEET]

        def _v(col):
            val = ws.cell(2, col).value
            return float(val) if val is not None else 0.0

        c = CellData(filepath)
        c.Voc_fwd = _v(_COL["Voc_fwd"])
        c.Jsc_fwd = _v(_COL["Jsc_fwd"])
        c.FF_fwd  = _v(_COL["FF_fwd"])
        c.PCE_fwd = _v(_COL["PCE_fwd"])
        c.Rs_fwd  = _v(_COL["Rs_fwd"])
        c.Rsh_fwd = _v(_COL["Rsh_fwd"])
        c.Voc_rev = _v(_COL["Voc_rev"])
        c.Jsc_rev = _v(_COL["Jsc_rev"])
        c.FF_rev  = _v(_COL["FF_rev"])
        c.PCE_rev = _v(_COL["PCE_rev"])
        c.Rs_rev  = _v(_COL["Rs_rev"])
        c.Rsh_rev = _v(_COL["Rsh_rev"])
        c.HI      = _v(_COL["HI"])
        wb.close()
        return c
    except Exception as exc:
        print(f"Warning: could not load {filepath}: {exc}")
        return None


def group_by_substrate(cells, mode="auto", n_per_substrate=4):
    """Group CellData objects by substrate prefix or sequentially."""
    groups = {}
    if mode == "auto":
        for c in cells:
            groups.setdefault(c.substrate, []).append(c)
    else:
        for i, c in enumerate(cells):
            key = f"Substrate_{i // n_per_substrate + 1}"
            groups.setdefault(key, []).append(c)
    return groups


def export_curated_excel(cells, thresholds, threshold_scan,
                         substrate_groups, output_path,
                         progress_callback=None):
    """Build and save the curated Excel workbook.

    Parameters
    ----------
    cells : list[CellData]
    thresholds : dict {param: (min_val, max_val)}  — only enabled params
    threshold_scan : 'rev' or 'both'
    substrate_groups : dict returned by group_by_substrate()
    output_path : full .xlsx path
    progress_callback : optional callable(str)
    """
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    # ── Styles ───────────────────────────────────────────────────────────────
    H_FILL  = PatternFill("solid", fgColor="1565C0")
    H_FONT  = Font(bold=True, color="FFFFFF", size=10)
    SH_FILL = PatternFill("solid", fgColor="BBDEFB")
    SH_FONT = Font(bold=True, color="0D47A1", size=10)
    PASS_F  = PatternFill("solid", fgColor="C8E6C9")
    AVG_F   = PatternFill("solid", fgColor="FFF9C4")
    B_FONT  = Font(bold=True, size=10)
    N_FONT  = Font(size=10)
    C_ALIGN = Alignment(horizontal="center", vertical="center")
    L_ALIGN = Alignment(horizontal="left",   vertical="center")

    PASS_HDRS = [f"Pass_{p}" for p in thresholds]

    # Column layout (1-indexed):
    # FWD: Device | 7 params | pass cols | GAP | REV: Device | 7 params | pass cols
    FWD_DEV  = 1
    FWD_P    = list(range(2, 9))
    FWD_PASS = list(range(9, 9 + len(thresholds)))
    GAP      = 9 + len(thresholds)
    REV_DEV  = GAP + 1
    REV_P    = list(range(REV_DEV + 1, REV_DEV + 8))
    REV_PASS = list(range(REV_DEV + 8, REV_DEV + 8 + len(thresholds)))

    def _write_headers(ws, start_row):
        end_fwd = (FWD_PASS[-1] if FWD_PASS else FWD_P[-1])
        end_rev = (REV_PASS[-1] if REV_PASS else REV_P[-1])

        ws.cell(start_row, FWD_DEV, "FORWARD SCAN").fill = SH_FILL
        ws.cell(start_row, FWD_DEV).font = SH_FONT
        ws.merge_cells(start_row=start_row, start_column=FWD_DEV,
                       end_row=start_row,   end_column=end_fwd)
        ws.cell(start_row, REV_DEV, "REVERSE SCAN").fill = SH_FILL
        ws.cell(start_row, REV_DEV).font = SH_FONT
        ws.merge_cells(start_row=start_row, start_column=REV_DEV,
                       end_row=start_row,   end_column=end_rev)
        r = start_row + 1
        for col, txt in [(FWD_DEV, "Device")] + list(zip(FWD_P, P_HEADERS)):
            ws.cell(r, col, txt).fill = H_FILL
            ws.cell(r, col).font = H_FONT
            ws.cell(r, col).alignment = C_ALIGN
        for k, ph in enumerate(PASS_HDRS):
            ws.cell(r, FWD_PASS[k], ph).fill = H_FILL
            ws.cell(r, FWD_PASS[k]).font = H_FONT
            ws.cell(r, FWD_PASS[k]).alignment = C_ALIGN
        for col, txt in [(REV_DEV, "Device")] + list(zip(REV_P, P_HEADERS)):
            ws.cell(r, col, txt).fill = H_FILL
            ws.cell(r, col).font = H_FONT
            ws.cell(r, col).alignment = C_ALIGN
        for k, ph in enumerate(PASS_HDRS):
            ws.cell(r, REV_PASS[k], ph).fill = H_FILL
            ws.cell(r, REV_PASS[k]).font = H_FONT
            ws.cell(r, REV_PASS[k]).alignment = C_ALIGN
        return r + 1

    def _write_cell_row(ws, row, cell, highlight=True):
        for side, dev_col, p_cols, pass_cols in [
            ("fwd", FWD_DEV, FWD_P, FWD_PASS),
            ("rev", REV_DEV, REV_P, REV_PASS),
        ]:
            ws.cell(row, dev_col, cell.device).font = N_FONT
            ws.cell(row, dev_col).alignment = L_ALIGN
            for k, (p, fmt) in enumerate(zip(PARAMS, P_FMT)):
                v = cell.get_val(p, side)
                ws.cell(row, p_cols[k], round(float(v), 6)).font = N_FONT
                ws.cell(row, p_cols[k]).number_format = fmt
            all_pass = True
            for k, (param, (mn, mx)) in enumerate(thresholds.items()):
                scan_for_check = side if threshold_scan == "both" else "rev"
                passed = cell.passes(param, mn, mx, scan=scan_for_check)
                sym = "✓" if passed else "✗"
                ws.cell(row, pass_cols[k], sym).font = Font(
                    color="2E7D32" if passed else "C62828", bold=True, size=10)
                ws.cell(row, pass_cols[k]).alignment = C_ALIGN
                if not passed:
                    all_pass = False
            if highlight and all_pass and thresholds:
                for col in [dev_col] + p_cols + list(pass_cols):
                    ws.cell(row, col).fill = PASS_F

    def _write_stats(ws, row, grp_cells, side, dev_col, p_cols):
        mat  = [[c.get_val(p, side) for p in PARAMS] for c in grp_cells]
        arr  = np.array(mat, dtype=float)
        avgs = np.mean(arr, axis=0)
        stds = np.std(arr, axis=0)
        for label, vals in [("Average", avgs), ("Std Dev", stds)]:
            ws.cell(row, dev_col, label).font = B_FONT
            ws.cell(row, dev_col).fill = AVG_F
            for k, (v, fmt) in enumerate(zip(vals, P_FMT)):
                ws.cell(row, p_cols[k], round(float(v), 4)).font = B_FONT
                ws.cell(row, p_cols[k]).fill = AVG_F
                ws.cell(row, p_cols[k]).number_format = fmt
            row += 1
        return row

    def _autofit(ws):
        for col in ws.columns:
            mx = max((len(str(c.value or "")) for c in col), default=6)
            ws.column_dimensions[get_column_letter(col[0].column)].width = max(mx + 2, 10)
        ws.column_dimensions[get_column_letter(GAP)].width = 3

    # ── Sheet: Raw_Data ──────────────────────────────────────────────────────
    if progress_callback:
        progress_callback("Building Raw_Data sheet...")
    ws_raw = wb.create_sheet("Raw_Data")
    row = _write_headers(ws_raw, 1)
    for sub_name, sub_cells in substrate_groups.items():
        ws_raw.cell(row, FWD_DEV, f"── {sub_name} ──").font = Font(
            bold=True, italic=True, size=9, color="5D4037")
        row += 1
        for cell in sub_cells:
            _write_cell_row(ws_raw, row, cell)
            row += 1
        row = _write_stats(ws_raw, row, sub_cells, "fwd", FWD_DEV, FWD_P)
        row = _write_stats(ws_raw, row, sub_cells, "rev", REV_DEV, REV_P)
        row += 1
    _autofit(ws_raw)

    # ── Sheets: Curated_[param] ───────────────────────────────────────────────
    for param, (mn, mx) in thresholds.items():
        if progress_callback:
            progress_callback(f"Building Curated_{param} sheet...")
        passing = [c for c in cells if c.passes(param, mn, mx, scan=threshold_scan)]
        ws_cur  = wb.create_sheet(f"Curated_{param}")
        ws_cur.cell(1, 1, f"Threshold: {param}  [{mn} – {mx}]").font = Font(
            bold=True, size=11, color="1565C0")
        ws_cur.cell(2, 1, f"Scan filter: {'Reverse only' if threshold_scan == 'rev' else 'Both scans'}").font = Font(
            italic=True, size=10)
        ws_cur.cell(3, 1, f"Cells passing: {len(passing)} / {len(cells)}").font = Font(
            bold=True, size=10)
        row = _write_headers(ws_cur, 5)
        p_grps = group_by_substrate(passing, mode="auto")
        for sub_name, sub_cells in p_grps.items():
            ws_cur.cell(row, FWD_DEV, f"── {sub_name} ──").font = Font(
                bold=True, italic=True, size=9, color="5D4037")
            row += 1
            for cell in sub_cells:
                _write_cell_row(ws_cur, row, cell, highlight=False)
                row += 1
            row = _write_stats(ws_cur, row, sub_cells, "fwd", FWD_DEV, FWD_P)
            row = _write_stats(ws_cur, row, sub_cells, "rev", REV_DEV, REV_P)
            row += 1
        _autofit(ws_cur)

    # ── Sheet: Statistics ────────────────────────────────────────────────────
    if progress_callback:
        progress_callback("Building Statistics sheet...")
    ws_stat = wb.create_sheet("Statistics")
    ws_stat.cell(1, 1, "Batch Statistics").font = Font(bold=True, size=13, color="1565C0")
    ws_stat.cell(2, 1, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}").font = Font(
        italic=True, size=10)
    ws_stat.cell(3, 1, f"Total cells: {len(cells)}").font = Font(size=10)

    row = 5
    for k, h in enumerate(["Parameter", "Scan", "N", "Mean", "Std Dev", "Min", "Max", "Median"], 1):
        ws_stat.cell(row, k, h).fill = H_FILL
        ws_stat.cell(row, k).font = H_FONT
        ws_stat.cell(row, k).alignment = C_ALIGN
    row += 1

    for param in PARAMS:
        scans = ["rev"] if param == "HI" else ["fwd", "rev"]
        for scan in scans:
            vals = [c.get_val(param, scan) for c in cells if c.get_val(param, scan) != 0.0]
            if not vals:
                continue
            arr = np.array(vals)
            for k, v in enumerate([
                param,
                "N/A" if param == "HI" else scan.upper(),
                len(vals),
                round(float(np.mean(arr)),   4),
                round(float(np.std(arr)),    4),
                round(float(np.min(arr)),    4),
                round(float(np.max(arr)),    4),
                round(float(np.median(arr)), 4),
            ], 1):
                ws_stat.cell(row, k, v).font = N_FONT
                if k >= 4:
                    ws_stat.cell(row, k).number_format = "0.0000"
            row += 1

    row += 1
    ws_stat.cell(row, 1, "Threshold Summary").font = Font(bold=True, size=11)
    row += 1
    for param, (mn, mx) in thresholds.items():
        n_pass = sum(1 for c in cells if c.passes(param, mn, mx, scan=threshold_scan))
        ws_stat.cell(row, 1, param).font = N_FONT
        ws_stat.cell(row, 2, f"{mn} – {mx}").font = N_FONT
        ws_stat.cell(row, 3, f"{n_pass}/{len(cells)} passing").font = N_FONT
        row += 1

    for col in ws_stat.columns:
        mx_w = max((len(str(c.value or "")) for c in col), default=8)
        ws_stat.column_dimensions[get_column_letter(col[0].column)].width = max(mx_w + 2, 12)

    wb.save(output_path)
    return output_path
