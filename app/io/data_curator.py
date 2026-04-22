"""
Data Curation module — reads illuminated J-V xlsx files, applies thresholds,
and exports a curated workbook with Raw_Data, Curated_[param], Statistics,
and individual per-cell J-V sheets.

Column positions verified against app/io/exporter.py save_data():
  xlsxwriter 0-indexed col → openpyxl 1-indexed col (row 2 = scalar params)
  Voc_fwd H(8), Jsc_fwd I(9), FF_fwd J(10), PCE_fwd K(11), Rs_fwd L(12), Rsh_fwd M(13)
  Voc_rev N(14), Jsc_rev O(15), FF_rev P(16), PCE_rev Q(17), Rs_rev R(18), Rsh_rev S(19)
  HI T(20)
  Raw J-V data (rows 3+): col A=v_fwd, B=j_fwd, C=p_fwd, D=v_rev, E=j_rev, F=p_rev

Grouping convention: files should be named <prefix><cell>_<timestamp>.xlsx
  e.g. D1C1_20260412.xlsx, D1C2_20260412.xlsx, D2C1_20260412.xlsx
  Prefix = first letter+digit group (regex [A-Za-z]+\\d+): D1, D2, etc.
"""

import os
import re
from pathlib import Path
from datetime import datetime

import numpy as np
import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.chart import ScatterChart, Reference, Series

_SHEET = "MeasurementData"

_COL = {
    "Voc_fwd": 8,  "Jsc_fwd": 9,  "FF_fwd": 10, "PCE_fwd": 11,
    "Rs_fwd":  12, "Rsh_fwd": 13,
    "Voc_rev": 14, "Jsc_rev": 15, "FF_rev": 16, "PCE_rev": 17,
    "Rs_rev":  18, "Rsh_rev": 19,
    "HI":      20,
}

PARAMS    = ["Voc", "Jsc", "FF", "PCE", "HI", "Rs", "Rsh"]
P_HEADERS = ["Voc (V)", "Jsc (mA/cm²)", "FF (%)", "PCE (%)",
             "HI (%)", "Rs (Ω·cm²)", "Rsh (Ω·cm²)"]
# Number formats per parameter (Voc:2dp, Jsc:2dp, FF:0dp, PCE:2dp,
#                                HI:0dp, Rs:2dp, Rsh:2dp)
P_FMT = ["0.00", "0.00", "0", "0.00", "0", "0.00", "0.00"]

# ── Shared style constants ────────────────────────────────────────────────────
_FONT_NAME = "Calibri"
_FONT_SIZE = 11

def _font(bold=False, color="000000", size=None):
    return Font(name=_FONT_NAME, size=size or _FONT_SIZE,
                bold=bold, color=color)

_C_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=False)
_L_ALIGN = Alignment(horizontal="left",   vertical="center")

_H_FILL  = PatternFill("solid", fgColor="1565C0")   # dark blue — header
_SH_FILL = PatternFill("solid", fgColor="BBDEFB")   # light blue — section
_PASS_F  = PatternFill("solid", fgColor="C8E6C9")   # green — passing rows
_AVG_F   = PatternFill("solid", fgColor="FFF9C4")   # yellow — average
_STD_F   = PatternFill("solid", fgColor="F3E5F5")   # purple — std dev
_SUB_F   = PatternFill("solid", fgColor="EFEBE9")   # brown-grey — substrate label

# Fixed column widths for the summary sheets
_COL_WIDTHS = {
    "device": 18,
    "param":  12,   # most parameter columns
    "pass":    9,
    "gap":     3,
}


class CellData:
    """All PV parameters for one illuminated J-V measurement file."""

    def __init__(self, filepath):
        self.filepath = filepath
        self.filename = Path(filepath).name
        self.device   = self._extract_device()

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

    def get_val(self, param, scan):
        """Return value for param at the given scan direction ('fwd' or 'rev')."""
        if param == "HI":
            return self.HI
        return getattr(self, f"{param}_{scan}", 0.0)

    def passes(self, param, mn, scan="rev"):
        """Return True if value >= mn.

        scan='both' requires fwd AND rev each >= mn.
        """
        if scan == "both":
            return (self.get_val(param, "fwd") >= mn and
                    self.get_val(param, "rev") >= mn)
        return self.get_val(param, scan) >= mn


def load_cell_data(filepath):
    """Read a MeasurementData xlsx file. Returns CellData or None on failure."""
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
        c.Voc_fwd = _v(_COL["Voc_fwd"]); c.Jsc_fwd = _v(_COL["Jsc_fwd"])
        c.FF_fwd  = _v(_COL["FF_fwd"]);  c.PCE_fwd = _v(_COL["PCE_fwd"])
        c.Rs_fwd  = _v(_COL["Rs_fwd"]);  c.Rsh_fwd = _v(_COL["Rsh_fwd"])
        c.Voc_rev = _v(_COL["Voc_rev"]); c.Jsc_rev = _v(_COL["Jsc_rev"])
        c.FF_rev  = _v(_COL["FF_rev"]);  c.PCE_rev = _v(_COL["PCE_rev"])
        c.Rs_rev  = _v(_COL["Rs_rev"]);  c.Rsh_rev = _v(_COL["Rsh_rev"])
        c.HI      = _v(_COL["HI"])
        wb.close()
        return c
    except Exception as exc:
        print(f"Warning: could not load {filepath}: {exc}")
        return None


def _extract_prefix(filepath):
    """Extract substrate prefix from filename using first letter+digit group.

    'D1C1_20260412.xlsx' → 'D1'
    'D1_C1_20260412.xlsx' → 'D1'
    'D2C4_20260412.xlsx' → 'D2'
    Falls back to full stem if no match.
    """
    stem  = Path(filepath).stem
    match = re.match(r'([A-Za-z]+\d+)', stem)
    return match.group(1) if match else stem


def group_by_substrate(cells, n_per_substrate=None):
    """Group cells by filename prefix (first letter+digit group).

    e.g. D1C1, D1C2, D1C3 → prefix 'D1'; D2C1, D2C2 → prefix 'D2'.
    The n_per_substrate parameter is kept for API compatibility but ignored.
    """
    groups = {}
    for cell in cells:
        prefix = _extract_prefix(cell.filepath)
        groups.setdefault(prefix, []).append(cell)
    return dict(sorted(groups.items()))


# ── Per-cell individual sheet ─────────────────────────────────────────────────

def _add_individual_sheet(wb, cell_data, used_names):
    """Add one sheet with PV parameters, full J-V data (incl. power), and a
    native Excel ScatterChart. No matplotlib image is used."""
    base_name  = cell_data.device[:28]
    sheet_name = base_name
    counter    = 2
    while sheet_name in used_names:
        sheet_name = f"{base_name}_{counter}"
        counter   += 1
    used_names.add(sheet_name)

    ws = wb.create_sheet(sheet_name)

    # ── Row 1: device title ───────────────────────────────────────────────────
    ws["A1"] = cell_data.device
    ws["A1"].font = Font(name=_FONT_NAME, bold=True, size=14, color="1565C0")
    ws["A1"].alignment = _L_ALIGN

    # ── Rows 2-6: PV parameters table ────────────────────────────────────────
    ws["A2"] = "PV Parameters"
    ws["A2"].font = _font(bold=True, color="37474F")

    param_order = ["Voc", "Jsc", "FF", "PCE", "Rs", "Rsh"]
    param_units = {"Voc": "V", "Jsc": "mA/cm²", "FF": "%",
                   "PCE": "%", "Rs": "Ω·cm²", "Rsh": "Ω·cm²"}

    # Header row (row 3)
    ws.cell(3, 1, "Scan").fill = _H_FILL
    ws.cell(3, 1).font = _font(bold=True, color="FFFFFF")
    ws.cell(3, 1).alignment = _C_ALIGN
    for k, p in enumerate(param_order):
        hdr = f"{p} ({param_units[p]})"
        c = ws.cell(3, k + 2, hdr)
        c.fill = _H_FILL
        c.font = _font(bold=True, color="FFFFFF")
        c.alignment = _C_ALIGN
    # HI column
    c = ws.cell(3, len(param_order) + 2, "HI (%)")
    c.fill = _H_FILL
    c.font = _font(bold=True, color="FFFFFF")
    c.alignment = _C_ALIGN

    # Fwd row (row 4), Rev row (row 5)
    fwd_fmt = {"Voc": "0.00", "Jsc": "0.00", "FF": "0", "PCE": "0.00",
               "Rs": "0.00", "Rsh": "0.00"}
    for row_i, (scan_label, scan_key) in enumerate([("Forward", "fwd"),
                                                    ("Reverse", "rev")]):
        r = 4 + row_i
        ws.cell(r, 1, scan_label).font = _font(bold=True)
        ws.cell(r, 1).alignment = _C_ALIGN
        for k, p in enumerate(param_order):
            val = cell_data.get_val(p, scan_key)
            c = ws.cell(r, k + 2, round(float(val), 6))
            c.font = _font()
            c.alignment = _C_ALIGN
            c.number_format = fwd_fmt[p]
        # HI (same for both scans)
        c = ws.cell(r, len(param_order) + 2, round(float(cell_data.HI), 6))
        c.font = _font()
        c.alignment = _C_ALIGN
        c.number_format = "0"

    # Column widths for parameters section
    ws.column_dimensions["A"].width = 12
    for k in range(len(param_order) + 1):
        ws.column_dimensions[get_column_letter(k + 2)].width = 14

    # ── Row 8: J-V data headers ───────────────────────────────────────────────
    # Layout: V_fwd(A) J_fwd(B) P_fwd(C) [gap D] V_rev(E) J_rev(F) P_rev(G)
    DATA_HEADER_ROW = 8
    jv_headers = [
        (1, "V_fwd (V)",       "1565C0"),
        (2, "J_fwd (mA/cm²)", "1565C0"),
        (3, "P_fwd (mW/cm²)", "1565C0"),
        (5, "V_rev (V)",       "B71C1C"),
        (6, "J_rev (mA/cm²)", "B71C1C"),
        (7, "P_rev (mW/cm²)", "B71C1C"),
    ]
    for col, txt, color in jv_headers:
        c = ws.cell(DATA_HEADER_ROW, col, txt)
        c.fill = PatternFill("solid", fgColor=color)
        c.font = _font(bold=True, color="FFFFFF")
        c.alignment = _C_ALIGN

    # ── Rows 9+: J-V data ────────────────────────────────────────────────────
    DATA_START = DATA_HEADER_ROW + 1
    data_end   = DATA_START - 1   # will be updated in the loop

    try:
        wb_src = openpyxl.load_workbook(cell_data.filepath,
                                         read_only=True, data_only=True)
        ws_src = wb_src[_SHEET]
        row    = DATA_START

        for src_row in ws_src.iter_rows(min_row=3, values_only=True):
            # Source layout: A(0)=Vfwd, B(1)=Jfwd, C(2)=Pfwd,
            #                D(3)=Vrev, E(4)=Jrev, F(5)=Prev
            vf = src_row[0] if len(src_row) > 0 else None
            jf = src_row[1] if len(src_row) > 1 else None
            pf = src_row[2] if len(src_row) > 2 else None
            vr = src_row[3] if len(src_row) > 3 else None
            jr = src_row[4] if len(src_row) > 4 else None
            pr = src_row[5] if len(src_row) > 5 else None

            if vf is None or not isinstance(vf, (int, float)):
                continue

            for col, val, fmt in [
                (1, vf, "0.00000"), (2, jf, "0.00000"), (3, pf, "0.00000"),
                (5, vr, "0.00000"), (6, jr, "0.00000"), (7, pr, "0.00000"),
            ]:
                if val is not None and isinstance(val, (int, float)):
                    c = ws.cell(row, col, round(float(val), 6))
                    c.font = _font()
                    c.alignment = _C_ALIGN
                    c.number_format = fmt

            data_end = row
            row += 1

        wb_src.close()

    except Exception as e:
        ws["A9"] = f"Could not read source data: {e}"
        ws["A9"].font = _font(color="C62828")

    # ── Native Excel ScatterChart ─────────────────────────────────────────────
    if data_end >= DATA_START:
        chart = ScatterChart()
        chart.style = 10
        chart.y_axis.title = "J (mA/cm²)"
        chart.x_axis.title = "V (V)"
        chart.y_axis.scaling.min = 0
        chart.y_axis.scaling.max = 30
        chart.x_axis.scaling.min = 0
        chart.x_axis.scaling.max = 1.2
        chart.width  = 14
        chart.height = 9
        try:
            chart.legend.position = "tr"
        except Exception:
            pass

        # Forward: V=col1, J=col2
        xf = Reference(ws, min_col=1, min_row=DATA_START, max_row=data_end)
        yf = Reference(ws, min_col=2, min_row=DATA_START, max_row=data_end)
        s1 = Series(yf, xf, title="Forward")
        s1.graphicalProperties.line.solidFill = "1565C0"
        s1.graphicalProperties.line.width = 20000
        chart.series.append(s1)

        # Reverse: V=col5, J=col6
        xr = Reference(ws, min_col=5, min_row=DATA_START, max_row=data_end)
        yr = Reference(ws, min_col=6, min_row=DATA_START, max_row=data_end)
        s2 = Series(yr, xr, title="Reverse")
        s2.graphicalProperties.line.solidFill = "B71C1C"
        s2.graphicalProperties.line.width = 20000
        chart.series.append(s2)

        ws.add_chart(chart, "I2")

    # J-V data column widths
    ws.column_dimensions["A"].width = 13
    ws.column_dimensions["B"].width = 17
    ws.column_dimensions["C"].width = 17
    ws.column_dimensions["D"].width = 3
    ws.column_dimensions["E"].width = 13
    ws.column_dimensions["F"].width = 17
    ws.column_dimensions["G"].width = 17


# ── Composition statistics sheets ────────────────────────────────────────────

def _export_composition_sheets(wb, cells, comp_groups, thresholds,
                                threshold_scan, progress_cb=None):
    """Add 4 sheets with one column per composition group, one block per param.

    Sheets: "Fwd – All", "Rev – All", "Fwd – Filtered", "Rev – Filtered"
    HI is included only in Rev sheets (it has no independent fwd/rev value).

    comp_groups : list of {"name": str, "prefixes": [str, ...]}
    """
    if not comp_groups:
        return

    def _cell_group(cell):
        prefix = _extract_prefix(cell.filepath)
        for grp in comp_groups:
            if prefix in grp["prefixes"]:
                return grp["name"]
        return None

    group_names = [g["name"] for g in comp_groups]

    def _is_passing(cell):
        for param, mn in thresholds.items():
            if not cell.passes(param, mn, scan=threshold_scan):
                return False
        return True

    passing_cells = [c for c in cells if _is_passing(c)]

    PARAMS_FWD = ["Voc", "Jsc", "FF", "PCE", "Rs", "Rsh"]
    PARAMS_REV = ["Voc", "Jsc", "FF", "PCE", "HI", "Rs", "Rsh"]
    P_HDR = {
        "Voc": "Voc (V)",     "Jsc": "Jsc (mA/cm²)", "FF":  "FF (%)",
        "PCE": "PCE (%)",     "HI":  "HI (%)",        "Rs":  "Rs (Ω·cm²)",
        "Rsh": "Rsh (Ω·cm²)",
    }
    P_FMTS = {
        "Voc": "0.00", "Jsc": "0.00", "FF": "0",
        "PCE": "0.00", "HI":  "0",    "Rs": "0.00", "Rsh": "0.00",
    }
    BLOCK_GAP = 1

    _H_GRP  = PatternFill("solid", fgColor="0D47A1")   # dark blue — param header
    _SH_GRP = PatternFill("solid", fgColor="BBDEFB")   # light blue — group name

    sheets = [
        ("Fwd \u2013 All",      "fwd", cells,         PARAMS_FWD),
        ("Rev \u2013 All",      "rev", cells,         PARAMS_REV),
        ("Fwd \u2013 Filtered", "fwd", passing_cells, PARAMS_FWD),
        ("Rev \u2013 Filtered", "rev", passing_cells, PARAMS_REV),
    ]

    for sheet_name, scan, cell_set, params in sheets:
        if progress_cb:
            progress_cb(f"Building {sheet_name} sheet…")

        ws = wb.create_sheet(sheet_name)
        n_groups = len(group_names)

        # Build data: group_data[param][group_name] = [val, ...]
        group_data = {}
        for param in params:
            group_data[param] = {name: [] for name in group_names}
            for cell in cell_set:
                gname = _cell_group(cell)
                if gname and gname in group_data[param]:
                    group_data[param][gname].append(cell.get_val(param, scan))

        col = 1
        for p_idx, param in enumerate(params):
            end_col = col + n_groups - 1

            # Row 1: param block header (merged across group columns)
            hdr = ws.cell(1, col, P_HDR[param])
            hdr.font      = _font(bold=True, color="FFFFFF")
            hdr.fill      = _H_GRP
            hdr.alignment = _C_ALIGN
            if n_groups > 1:
                ws.merge_cells(start_row=1, start_column=col,
                               end_row=1,   end_column=end_col)

            # Row 2: group name sub-headers
            for g_idx, gname in enumerate(group_names):
                gc = ws.cell(2, col + g_idx, gname)
                gc.font      = _font(bold=True, color="0D47A1")
                gc.fill      = _SH_GRP
                gc.alignment = _C_ALIGN

            # Row 3+: numeric data
            for g_idx, gname in enumerate(group_names):
                fmt  = P_FMTS[param]
                vals = group_data[param][gname]
                for r_idx, val in enumerate(vals):
                    dc = ws.cell(3 + r_idx, col + g_idx, round(float(val), 6))
                    dc.font         = _font()
                    dc.number_format = fmt
                    dc.alignment    = _C_ALIGN

            # Column widths for this block
            for g_idx in range(n_groups):
                ws.column_dimensions[get_column_letter(col + g_idx)].width = 12

            col += n_groups + BLOCK_GAP

            # Gap column width (between blocks)
            if p_idx < len(params) - 1:
                ws.column_dimensions[get_column_letter(col - 1)].width = 3

        ws.freeze_panes = "A3"


# ── Statistics sheet ─────────────────────────────────────────────────────────

def _build_statistics_sheet(wb, cells, thresholds, threshold_scan,
                             composition_groups=None):
    """Build a structured Statistics sheet with per-group summaries,
    champion cell, hysteresis analysis, and threshold yield.

    Grouping priority:
      1. composition_groups (user-defined) if non-empty
      2. auto-detected filename prefixes
    """
    ws = wb.create_sheet("Statistics")

    P_FMT_D  = dict(zip(PARAMS, P_FMT))
    P_HDR_D  = dict(zip(PARAMS, P_HEADERS))
    SUB_HDRS = ["Mean", "Std Dev", "CV%", "Max"]

    # Styles
    _T_FILL  = PatternFill("solid", fgColor="1A237E")  # deep indigo — title
    _P_FILL  = PatternFill("solid", fgColor="3949AB")  # indigo — param block header
    _S_FILL  = PatternFill("solid", fgColor="C5CAE9")  # light indigo — sub-header
    _S2_FILL = PatternFill("solid", fgColor="9FA8DA")  # alternate sub-header
    _ALL_F   = PatternFill("solid", fgColor="FFF9C4")  # yellow — aggregate row
    _GR_F    = [PatternFill("solid", fgColor="FFFFFF"),
                PatternFill("solid", fgColor="EEF0FB")]
    _CH_F    = PatternFill("solid", fgColor="FFE0B2")  # orange — champion

    # ── Grouping helpers ──────────────────────────────────────────────────────
    if composition_groups:
        group_names = [g["name"] for g in composition_groups]
        def _gcells(gname, pool):
            grp = next((g for g in composition_groups
                        if g["name"] == gname), None)
            if not grp:
                return []
            return [c for c in pool
                    if _extract_prefix(c.filepath) in grp["prefixes"]]
    else:
        group_names = sorted(set(_extract_prefix(c.filepath) for c in cells))
        def _gcells(gname, pool):
            return [c for c in pool
                    if _extract_prefix(c.filepath) == gname]

    def _is_pass(cell):
        return all(cell.passes(p, mn, scan=threshold_scan)
                   for p, mn in thresholds.items())

    passing = [c for c in cells if _is_pass(c)]

    # ── Stats helper ──────────────────────────────────────────────────────────
    def _stats(vals):
        """(mean, std_ddof1, cv_pct, maximum) — all None if vals is empty."""
        arr = np.array([v for v in vals if v is not None], dtype=float)
        if len(arr) == 0:
            return None, None, None, None
        mean = float(np.mean(arr))
        std  = float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0
        cv   = (std / abs(mean) * 100.0) if abs(mean) > 1e-10 else 0.0
        return mean, std, cv, float(np.max(arr))

    # ── Cell writers ──────────────────────────────────────────────────────────
    def _hc(row, col, val, fill, bold=False, color="000000",
            align=_C_ALIGN, size=None, fmt=None):
        c = ws.cell(row, col, val)
        c.font      = _font(bold=bold, color=color, size=size)
        c.fill      = fill
        c.alignment = align
        if fmt:
            c.number_format = fmt
        return c

    def _param_col(p_idx):
        return 3 + p_idx * 4   # Group=1, N=2, then 4 cols per param

    # ── Section builder ───────────────────────────────────────────────────────
    def _write_section(start_row, title, scan, pool, params):
        n_cols = 2 + len(params) * 4

        # Title row
        _hc(start_row, 1, title, _T_FILL, bold=True, color="FFFFFF", size=11)
        ws.merge_cells(start_row=start_row, start_column=1,
                       end_row=start_row,   end_column=n_cols)

        # Param block header row
        r = start_row + 1
        for col in [1, 2]:
            ws.cell(r, col).fill = _H_FILL
        for p_idx, param in enumerate(params):
            ps = _param_col(p_idx)
            _hc(r, ps, P_HDR_D[param], _P_FILL, bold=True, color="FFFFFF")
            ws.merge_cells(start_row=r, start_column=ps,
                           end_row=r,   end_column=ps + 3)

        # Sub-header row
        r += 1
        _hc(r, 1, "Group", _S_FILL, bold=True)
        _hc(r, 2, "N",     _S_FILL, bold=True)
        for p_idx in range(len(params)):
            ps   = _param_col(p_idx)
            sf   = _S_FILL if p_idx % 2 == 0 else _S2_FILL
            for k, sh in enumerate(SUB_HDRS):
                _hc(r, ps + k, sh, sf, bold=True)

        r += 1
        # Group data rows
        for g_idx, gname in enumerate(group_names):
            gcells = _gcells(gname, pool)
            gf = _GR_F[g_idx % 2]
            _write_stats_row(r, gname, gcells, params, scan, gf, bold=False)
            r += 1

        # "All cells" aggregate
        _write_stats_row(r, "All cells", pool, params, scan, _ALL_F, bold=True)
        r += 1

        return r + 1   # blank row gap

    def _write_stats_row(row, label, cell_list, params, scan, fill, bold):
        c = ws.cell(row, 1, label)
        c.font = _font(bold=bold); c.fill = fill; c.alignment = _L_ALIGN
        c = ws.cell(row, 2, len(cell_list))
        c.font = _font(bold=bold); c.fill = fill; c.alignment = _C_ALIGN

        for p_idx, param in enumerate(params):
            ps   = _param_col(p_idx)
            vals = [cl.get_val(param, scan) for cl in cell_list]
            mean, std, cv, mx = _stats(vals)
            fmt  = P_FMT_D.get(param, "0.00")
            for k, (val, num_fmt) in enumerate([(mean, fmt), (std, fmt),
                                                (cv,   "0.0"), (mx, fmt)]):
                dc = ws.cell(row, ps + k)
                dc.font      = _font(bold=bold)
                dc.fill      = fill
                dc.alignment = _C_ALIGN
                if val is not None:
                    dc.value        = round(val, 6)
                    dc.number_format = num_fmt
                else:
                    dc.value = "—"

    # ═════════════════════════════════════════════════════════════════════════
    # Sheet content
    # ═════════════════════════════════════════════════════════════════════════

    # Global title
    _hc(1, 1, "STATISTICAL SUMMARY", _T_FILL, bold=True, color="FFFFFF", size=13)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=30)
    ws.cell(2, 1, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}").font = _font(color="37474F")
    ws.cell(2, 3, f"Total cells: {len(cells)}   |   Filtered: {len(passing)}").font = _font(bold=True)

    PARAMS_REV = ["Voc", "Jsc", "FF", "PCE", "HI", "Rs", "Rsh"]
    PARAMS_FWD = ["Voc", "Jsc", "FF", "PCE", "Rs", "Rsh"]

    row = 4
    row = _write_section(row, "REVERSE SCAN — All Cells",
                         "rev", cells,   PARAMS_REV)
    row = _write_section(row, "REVERSE SCAN — Filtered Cells",
                         "rev", passing, PARAMS_REV)
    row = _write_section(row, "FORWARD SCAN — All Cells",
                         "fwd", cells,   PARAMS_FWD)
    row = _write_section(row, "FORWARD SCAN — Filtered Cells",
                         "fwd", passing, PARAMS_FWD)

    # ── Champion Cells (per group) ────────────────────────────────────────────
    _hc(row, 1, "CHAMPION CELLS  (highest PCE rev — per group)",
        _T_FILL, bold=True, color="FFFFFF", size=11)
    ch_params  = ["Voc", "Jsc", "FF", "PCE", "HI", "Rs", "Rsh"]
    ch_n_cols  = 3 + len(ch_params)
    ws.merge_cells(start_row=row, start_column=1,
                   end_row=row, end_column=ch_n_cols)
    row += 1

    if cells:
        for k, h in enumerate(["Group", "Device", "Scan"] +
                               [P_HDR_D[p] for p in ch_params], 1):
            _hc(row, k, h, _H_FILL, bold=True, color="FFFFFF")
        row += 1

        def _write_champion_rows(label, champ, fill):
            for i, (scan_label, scan_key) in enumerate(
                    [("Forward", "fwd"), ("Reverse", "rev")]):
                if i == 0:
                    c = ws.cell(row + i, 1, label)
                    c.font = _font(bold=True); c.fill = fill
                    c.alignment = _C_ALIGN
                    if True:   # always merge 2 rows for the group label
                        ws.merge_cells(start_row=row + i, start_column=1,
                                       end_row=row + i + 1, end_column=1)
                    c2 = ws.cell(row + i, 2, champ.device)
                    c2.font = _font(bold=True); c2.fill = fill
                    c2.alignment = _C_ALIGN
                    ws.merge_cells(start_row=row + i, start_column=2,
                                   end_row=row + i + 1, end_column=2)
                else:
                    ws.cell(row + i, 1).fill = fill
                    ws.cell(row + i, 2).fill = fill

                sc = ws.cell(row + i, 3, scan_label)
                sc.font = _font(); sc.fill = fill; sc.alignment = _C_ALIGN
                for j, param in enumerate(ch_params, 4):
                    val = champ.get_val(param, scan_key)
                    dc  = ws.cell(row + i, j, round(float(val), 4))
                    dc.font = _font(); dc.fill = fill; dc.alignment = _C_ALIGN
                    dc.number_format = P_FMT_D.get(param, "0.00")

        # One champion per group
        for g_idx, gname in enumerate(group_names):
            gcells = _gcells(gname, cells)
            if not gcells:
                continue
            champ = max(gcells, key=lambda c: c.PCE_rev)
            gf    = _GR_F[g_idx % 2]
            _write_champion_rows(gname, champ, gf)
            row += 2

        # Overall champion
        overall_champ = max(cells, key=lambda c: c.PCE_rev)
        _write_champion_rows("All cells", overall_champ, _ALL_F)
        row += 2

    row += 1

    # ── Hysteresis Summary (per group) ────────────────────────────────────────
    _hc(row, 1, "HYSTERESIS SUMMARY  (per group)",
        _T_FILL, bold=True, color="FFFFFF", size=11)
    hi_n_cols = 7
    ws.merge_cells(start_row=row, start_column=1,
                   end_row=row, end_column=hi_n_cols)
    row += 1

    for k, h in enumerate(["Group", "N", "Mean HI (%)",
                            "Std Dev (%)", "CV%",
                            "HI < 10%", "HI < 5%"], 1):
        _hc(row, k, h, _H_FILL, bold=True, color="FFFFFF")
    row += 1

    def _hi_row(label, pool, fill, bold=False):
        hi_vals = [c.HI for c in pool]
        n = len(hi_vals)
        mean = round(float(np.mean(hi_vals)),  2) if n > 0   else "—"
        std  = round(float(np.std(hi_vals, ddof=1)), 2) if n > 1 else ("0.00" if n == 1 else "—")
        cv   = round(std / abs(mean) * 100, 1) if isinstance(mean, float) and isinstance(std, float) and abs(mean) > 1e-10 else "—"
        lt10 = f"{sum(1 for v in hi_vals if v < 10)}/{n}" if n else "—"
        lt5  = f"{sum(1 for v in hi_vals if v < 5)}/{n}"  if n else "—"
        for k, val in enumerate([label, n, mean, std, cv, lt10, lt5], 1):
            c = ws.cell(row, k, val)
            c.font = _font(bold=bold); c.fill = fill; c.alignment = _C_ALIGN

    for g_idx, gname in enumerate(group_names):
        _hi_row(gname, _gcells(gname, cells), _GR_F[g_idx % 2])
        row += 1
    _hi_row("All cells", cells, _ALL_F, bold=True)
    row += 2

    # ── Threshold Yield ───────────────────────────────────────────────────────
    if thresholds:
        _hc(row, 1, "THRESHOLD YIELD", _T_FILL, bold=True, color="FFFFFF", size=11)
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        row += 1
        for k, h in enumerate(["Parameter", "Min ≥",
                                "Pass / All", "Pass / Filtered"], 1):
            _hc(row, k, h, _H_FILL, bold=True, color="FFFFFF")
        row += 1
        for i, (param, mn) in enumerate(thresholds.items()):
            n_a = sum(1 for c in cells   if c.passes(param, mn, scan=threshold_scan))
            n_f = sum(1 for c in passing if c.passes(param, mn, scan=threshold_scan))
            gf  = _GR_F[i % 2]
            for k, val in enumerate(
                [param, f"≥ {mn}",
                 f"{n_a}/{len(cells)}",
                 f"{n_f}/{len(passing)}" if passing else "—"], 1):
                c = ws.cell(row, k, val)
                c.font = _font(); c.fill = gf; c.alignment = _C_ALIGN
            row += 1

    # ── Column widths ─────────────────────────────────────────────────────────
    ws.column_dimensions["A"].width = 22   # Group / Metric
    ws.column_dimensions["B"].width = 8    # N / Device
    ws.column_dimensions["C"].width = 10   # Scan / stats
    for i in range(7 * 4):
        ws.column_dimensions[get_column_letter(4 + i)].width = 11

    ws.freeze_panes = "A4"


# ── Main export function ──────────────────────────────────────────────────────

def export_curated_excel(cells, thresholds, threshold_scan,
                         substrate_groups, output_path,
                         composition_groups=None,
                         progress_callback=None):
    """Build and save the curated Excel workbook.

    Parameters
    ----------
    cells : list[CellData]
    thresholds : dict {param: min_val}  — only enabled params (min-only filter)
    threshold_scan : 'rev' or 'both'
    substrate_groups : dict returned by group_by_substrate()
    output_path : full .xlsx path
    composition_groups : list of {"name": str, "prefixes": [str]} — optional
    progress_callback : optional callable(str)
    """
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    PASS_HDRS = [f"≥{mn:.4g} {p}" for p, mn in thresholds.items()]

    # Column layout (1-indexed):
    # FWD: Device | 7 params | pass cols | GAP | REV: Device | 7 params | pass cols
    FWD_DEV  = 1
    FWD_P    = list(range(2, 9))                           # cols 2-8
    FWD_PASS = list(range(9, 9 + len(thresholds)))
    GAP      = 9 + len(thresholds)
    REV_DEV  = GAP + 1
    REV_P    = list(range(REV_DEV + 1, REV_DEV + 8))
    REV_PASS = list(range(REV_DEV + 8, REV_DEV + 8 + len(thresholds)))

    def _hcell(ws, row, col, txt, fill=_H_FILL):
        c = ws.cell(row, col, txt)
        c.fill = fill
        c.font = _font(bold=True, color="FFFFFF")
        c.alignment = _C_ALIGN
        return c

    def _write_headers(ws, start_row):
        end_fwd = FWD_PASS[-1] if FWD_PASS else FWD_P[-1]
        end_rev = REV_PASS[-1] if REV_PASS else REV_P[-1]

        _hcell(ws, start_row, FWD_DEV, "FORWARD SCAN", _SH_FILL).font = _font(bold=True, color="0D47A1")
        ws.merge_cells(start_row=start_row, start_column=FWD_DEV,
                       end_row=start_row,   end_column=end_fwd)
        _hcell(ws, start_row, REV_DEV, "REVERSE SCAN", _SH_FILL).font = _font(bold=True, color="0D47A1")
        ws.merge_cells(start_row=start_row, start_column=REV_DEV,
                       end_row=start_row,   end_column=end_rev)

        r = start_row + 1
        _hcell(ws, r, FWD_DEV, "Device")
        for col, txt in zip(FWD_P, P_HEADERS):
            _hcell(ws, r, col, txt)
        for k, ph in enumerate(PASS_HDRS):
            _hcell(ws, r, FWD_PASS[k], ph)
        _hcell(ws, r, REV_DEV, "Device")
        for col, txt in zip(REV_P, P_HEADERS):
            _hcell(ws, r, col, txt)
        for k, ph in enumerate(PASS_HDRS):
            _hcell(ws, r, REV_PASS[k], ph)
        return r + 1

    def _write_cell_row(ws, row, cell, highlight=True):
        for side, dev_col, p_cols, pass_cols in [
            ("fwd", FWD_DEV, FWD_P, FWD_PASS),
            ("rev", REV_DEV, REV_P, REV_PASS),
        ]:
            c = ws.cell(row, dev_col, cell.device)
            c.font = _font()
            c.alignment = _L_ALIGN
            all_pass = True
            for k, (p, fmt) in enumerate(zip(PARAMS, P_FMT)):
                v  = cell.get_val(p, side)
                dc = ws.cell(row, p_cols[k], round(float(v), 6))
                dc.font = _font()
                dc.number_format = fmt
                dc.alignment = _C_ALIGN
            for k, (param, mn) in enumerate(thresholds.items()):
                scan_for_check = side if threshold_scan == "both" else "rev"
                passed = cell.passes(param, mn, scan=scan_for_check)
                sym = "✓" if passed else "✗"
                pc = ws.cell(row, pass_cols[k], sym)
                pc.font = Font(name=_FONT_NAME, size=_FONT_SIZE,
                               color="2E7D32" if passed else "C62828", bold=True)
                pc.alignment = _C_ALIGN
                if not passed:
                    all_pass = False
            if highlight and all_pass and thresholds:
                for col in [dev_col] + p_cols + list(pass_cols):
                    ws.cell(row, col).fill = _PASS_F

    def _write_stats_both(ws, row, data_start_row, data_end_row):
        """Write Average and Std Dev rows using Excel formulas."""
        for label, func, fill in [
            ("Average", "AVERAGE", _AVG_F),
            ("Std Dev", "STDEV",   _STD_F),
        ]:
            for dev_col, p_cols in [(FWD_DEV, FWD_P), (REV_DEV, REV_P)]:
                lc = ws.cell(row, dev_col, label)
                lc.font = _font(bold=True)
                lc.fill = fill
                lc.alignment = _C_ALIGN
                for k, fmt in enumerate(P_FMT):
                    col        = p_cols[k]
                    col_letter = get_column_letter(col)
                    formula    = f"={func}({col_letter}{data_start_row}:{col_letter}{data_end_row})"
                    fc = ws.cell(row, col, formula)
                    fc.font          = _font(bold=True)
                    fc.fill          = fill
                    fc.number_format = fmt
                    fc.alignment     = _C_ALIGN
            row += 1
        return row

    def _set_col_widths(ws):
        ws.column_dimensions[get_column_letter(FWD_DEV)].width = _COL_WIDTHS["device"]
        for col in FWD_P:
            ws.column_dimensions[get_column_letter(col)].width = _COL_WIDTHS["param"]
        for col in FWD_PASS:
            ws.column_dimensions[get_column_letter(col)].width = _COL_WIDTHS["pass"]
        ws.column_dimensions[get_column_letter(GAP)].width = _COL_WIDTHS["gap"]
        ws.column_dimensions[get_column_letter(REV_DEV)].width = _COL_WIDTHS["device"]
        for col in REV_P:
            ws.column_dimensions[get_column_letter(col)].width = _COL_WIDTHS["param"]
        for col in REV_PASS:
            ws.column_dimensions[get_column_letter(col)].width = _COL_WIDTHS["pass"]

    # ── Sheet: Raw_Data ───────────────────────────────────────────────────────
    if progress_callback:
        progress_callback("Building Raw_Data sheet…")
    ws_raw = wb.create_sheet("Raw_Data")
    row = _write_headers(ws_raw, 1)
    for sub_name, sub_cells in substrate_groups.items():
        sub_lbl = ws_raw.cell(row, FWD_DEV, f"── {sub_name} ──")
        sub_lbl.font = _font(bold=True, color="5D4037")
        sub_lbl.fill = _SUB_F
        ws_raw.merge_cells(
            start_row=row, start_column=FWD_DEV,
            end_row=row, end_column=REV_PASS[-1] if REV_PASS else REV_P[-1])
        row += 1
        data_start = row
        for cell in sub_cells:
            _write_cell_row(ws_raw, row, cell)
            row += 1
        data_end = row - 1
        row = _write_stats_both(ws_raw, row, data_start, data_end)
        row += 1
    _set_col_widths(ws_raw)

    # ── Sheets: Curated_[param] ───────────────────────────────────────────────
    for param, mn in thresholds.items():
        if progress_callback:
            progress_callback(f"Building Curated_{param} sheet…")
        passing = [c for c in cells if c.passes(param, mn, scan=threshold_scan)]
        ws_cur  = wb.create_sheet(f"Curated_{param}")

        ws_cur.cell(1, 1, f"Threshold: {param}  ≥ {mn}").font = _font(bold=True, color="1565C0", size=12)
        ws_cur.cell(2, 1, f"Scan filter: {'Reverse only' if threshold_scan == 'rev' else 'Both scans'}").font = _font(color="37474F")
        ws_cur.cell(3, 1, f"Cells passing: {len(passing)} / {len(cells)}").font = _font(bold=True)

        row = _write_headers(ws_cur, 5)
        p_grps = group_by_substrate(passing)
        for sub_name, sub_cells in p_grps.items():
            sub_lbl = ws_cur.cell(row, FWD_DEV, f"── {sub_name} ──")
            sub_lbl.font = _font(bold=True, color="5D4037")
            sub_lbl.fill = _SUB_F
            ws_cur.merge_cells(
                start_row=row, start_column=FWD_DEV,
                end_row=row, end_column=REV_PASS[-1] if REV_PASS else REV_P[-1])
            row += 1
            data_start = row
            for cell in sub_cells:
                _write_cell_row(ws_cur, row, cell, highlight=False)
                row += 1
            data_end = row - 1
            row = _write_stats_both(ws_cur, row, data_start, data_end)
            row += 1
        _set_col_widths(ws_cur)

    # ── Sheet: Statistics ─────────────────────────────────────────────────────
    if progress_callback:
        progress_callback("Building Statistics sheet…")
    _build_statistics_sheet(wb, cells, thresholds, threshold_scan,
                             composition_groups=composition_groups)

    # ── Composition statistics sheets ─────────────────────────────────────────
    if composition_groups:
        _export_composition_sheets(
            wb, cells, composition_groups, thresholds, threshold_scan,
            progress_cb=progress_callback)

    # ── Individual per-cell J-V sheets ────────────────────────────────────────
    used_sheet_names = set(wb.sheetnames)
    for i, cell in enumerate(cells):
        if progress_callback:
            progress_callback(f"Adding sheet {i+1}/{len(cells)}: {cell.device}")
        _add_individual_sheet(wb, cell, used_sheet_names)

    wb.save(output_path)
    return output_path
