"""
Data Viewer — read-only Toplevel window for opening saved measurement Excel files.

Supports:
  - Illuminated J-V      (sheet: MeasurementData)
  - Dark J-V             (sheet: Dark_JV_Data)
  - Stabilized Current   (sheet: StabCurrent_Data  or legacy: StabilizedCurrent)
  - Pure MPP Tracking    (sheet: PureMPP_Data — new and legacy formats)
  - MPP Tracking JV-SPO  (sheets: MPP_SPO + MPP_JV  or legacy: JV_Parameters)
"""

import math
import os

import numpy as np
import openpyxl
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from tkinter import (
    Toplevel, Frame, LabelFrame, Label, Button, Canvas,
    filedialog, messagebox,
    LEFT, RIGHT, BOTH, X, Y, W,
)
from tkinter import ttk

_ILLUMINATED_SHEET = "MeasurementData"
_DARK_SHEET        = "Dark_JV_Data"
_STAB_SHEET        = "StabCurrent_Data"
_STAB_SHEET_OLD    = "StabilizedCurrent"
_PMPP_SHEET        = "PureMPP_Data"
_MPP_SPO_SHEET     = "MPP_SPO"
_MPP_JV_PARAMS     = "MPP_JV"
_MPP_OLD_SHEET     = "JV_Parameters"

_COLORS = [
    "#1565C0", "#B71C1C", "#1B5E20", "#E65100", "#4A148C",
    "#006064", "#3E2723", "#37474F", "#880E4F", "#004D40",
]


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

    # JV parameters per cycle
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


def load_jv_file(filepath):
    """
    Open an xlsx file and return a parsed metadata dict.
    Raises ValueError for unrecognised formats.
    """
    wb = openpyxl.load_workbook(filepath, data_only=True)
    try:
        sheets = wb.sheetnames
        if _ILLUMINATED_SHEET in sheets:
            return _read_illuminated(wb[_ILLUMINATED_SHEET])
        elif _DARK_SHEET in sheets:
            return _read_dark(wb[_DARK_SHEET])
        elif _STAB_SHEET in sheets:
            return _read_stab(wb[_STAB_SHEET])
        elif _STAB_SHEET_OLD in sheets:
            return _read_stab_old(wb[_STAB_SHEET_OLD])
        elif _PMPP_SHEET in sheets:
            # Detect new vs old by first cell value
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

def _fmt(val, fmt_str, fallback="\u2014"):
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

        Button(
            top_bar, text="Open Another File\u2026",
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

        filename = os.path.basename(filepath)
        _TYPE_LABELS = {
            "illuminated": "Illuminated J-V",
            "dark":        "Dark J-V",
            "stab":        "Stabilized Current",
            "pure_mpp":    "Pure MPP Tracking",
            "mpp":         "MPP Tracking (JV-SPO)",
        }
        type_str = _TYPE_LABELS.get(data["type"], data["type"])

        self._title_lbl.config(text=f"\U0001f4c2  {filename}   \u2014   {type_str}")
        self.win.title(f"Data Viewer \u2014 {filename}")

        self._populate_metadata(data, filename)
        self._draw_plot(data, filename)

    # ── Metadata panel ────────────────────────────────────────────────────────

    def _populate_metadata(self, data, filename):
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
        }
        _TYPE_COLORS = {
            "illuminated": "#1565C0",
            "dark":        "#5d4e75",
            "stab":        "#1B5E20",
            "pure_mpp":    "#E65100",
            "mpp":         "#006064",
        }

        sf = section("File")
        name_display = filename if len(filename) <= 34 else filename[:31] + "\u2026"
        row(sf, "Name:", name_display)
        row(sf, "Type:", _TYPE_LABELS.get(data["type"], data["type"]),
            fg=_TYPE_COLORS.get(data["type"], "#2c3e50"))

        dtype = data["type"]

        # ── J-V (illuminated / dark) ───────────────────────────────────────
        if dtype in ("illuminated", "dark"):
            v_f = data.get("v_f", np.array([]))
            v_r = data.get("v_r", np.array([]))

            sm = section("Measurement Data")
            row(sm, "Cell area:", _fmt(data.get("cell_area"), "%.4f cm\u00b2"))
            if len(v_f) >= 2:
                row(sm, "Fwd range:", f"{v_f[0]:.3f} \u2192 {v_f[-1]:.3f} V")
                row(sm, "Step \u2248:", f"{abs(float(np.mean(np.diff(v_f)))):.4f} V")
            row(sm, "Fwd points:", str(len(v_f)))
            row(sm, "Rev points:", str(len(v_r)))

            si = section("Instrument Settings")
            row(si, "Delay/pt:", _fmt(data.get("delay_ms"), "%.0f ms"))
            row(si, "NPLC:", _fmt(data.get("nplc"), "%.2f"))
            fw = data.get("four_wire")
            fw_str = ("ON" if int(fw) else "OFF") if fw is not None else "\u2014"
            row(si, "4-Wire:", fw_str)
            row(si, "Scan rate:", _fmt(data.get("scan_rate"), "%.1f mV/s"))
            idn = str(data.get("instrument") or "\u2014")
            row(si, "Instrument:", (idn[:34] + "\u2026") if len(idn) > 34 else idn)

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
                row(sf2, "Jsc:",  _fmt(data.get("Jsc_f"),  "%.4f mA/cm\u00b2"), "#1565C0")
                row(sf2, "FF:",   _fmt(data.get("FF_f"),   "%.2f %%"),          "#1565C0")
                row(sf2, "PCE:",  _fmt(data.get("PCE_f"),  "%.2f %%"),          "#1565C0")
                row(sf2, "Rs:",   _fmt(data.get("Rs_f"),   "%.2f \u03a9\u00b7cm\u00b2"))
                row(sf2, "Rsh:",  _fmt(data.get("Rsh_f"),  "%.2f \u03a9\u00b7cm\u00b2"))

                sr = section("Reverse Scan")
                row(sr, "Voc:",  _fmt(data.get("Voc_r"),  "%.4f V"),          "#B71C1C")
                row(sr, "Jsc:",  _fmt(data.get("Jsc_r"),  "%.4f mA/cm\u00b2"), "#B71C1C")
                row(sr, "FF:",   _fmt(data.get("FF_r"),   "%.2f %%"),          "#B71C1C")
                row(sr, "PCE:",  _fmt(data.get("PCE_r"),  "%.2f %%"),          "#B71C1C")
                row(sr, "Rs:",   _fmt(data.get("Rs_r"),   "%.2f \u03a9\u00b7cm\u00b2"))
                row(sr, "Rsh:",  _fmt(data.get("Rsh_r"),  "%.2f \u03a9\u00b7cm\u00b2"))

                sh = section("Hysteresis")
                hi = data.get("HI")
                try:
                    hi_f = float(hi)
                    hi_color = "#B71C1C" if hi_f > 10 else "#E65100" if hi_f > 5 else "#1B5E20"
                except (TypeError, ValueError):
                    hi_color = "#2c3e50"
                row(sh, "HI:", _fmt(hi, "%.2f %%"), hi_color)

        # ── Stabilized Current ────────────────────────────────────────────
        elif dtype == "stab":
            times = data.get("times", np.array([]))
            sm = section("Measurement Data")
            row(sm, "Cell area:", _fmt(data.get("cell_area"), "%.4f cm\u00b2"))
            row(sm, "Applied V:", _fmt(data.get("v_applied"), "%.3f V"))
            row(sm, "Illumination:", _fmt(data.get("illumination"), "%.2f mW/cm\u00b2"))
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
                row(sr, "J range:",   f"{float(np.min(j_arr)):.3f} – {float(np.max(j_arr)):.3f} mA/cm\u00b2")
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
            row(sm, "Cell area:", _fmt(data.get("cell_area"), "%.4f cm\u00b2"))
            row(sm, "Illumination:", _fmt(data.get("illumination"), "%.2f mW/cm\u00b2"))
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
            row(sm, "Cell area:", _fmt(data.get("cell_area"), "%.4f cm\u00b2"))
            row(sm, "Illumination:", _fmt(data.get("illumination"), "%.2f mW/cm\u00b2"))
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

        self._meta_inner.update_idletasks()
        self._meta_canvas.configure(scrollregion=self._meta_canvas.bbox("all"))
        self._meta_canvas.yview_moveto(0)

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
        self._ax  = self._fig.add_subplot(111)
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
                ax_ylabel = sel.artist.axes.get_ylabel() or "Y"
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
            self._ax2.set_ylabel("Power Density (mW/cm\u00b2)", labelpad=8)
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
                            else " mA/cm\u00b2")
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
        self._ax.set_ylabel("Current Density (mA/cm\u00b2)", labelpad=8)
        self._ax.set_title(title, fontsize=12, fontweight="bold", pad=12)
        self._ax.set_xlim(0, 1.2)
        self._ax.set_ylim(-10, 30)
        self._ax.grid(True)

    def _draw_stab(self, data, title, all_lines):
        times = data.get("times", np.array([]))
        j_arr = data.get("j_vals", np.array([]))
        pce   = data.get("pce_vals", np.array([]))

        l1, = self._ax.plot(times, j_arr, color=_COLORS[0], linewidth=1.4,
                            label="J (mA/cm\u00b2)")
        l2, = self._ax2.plot(times, pce, color=_COLORS[3], linewidth=1.4,
                             linestyle="--", label="PCE (%)")
        all_lines += [l1, l2]

        stab = data.get("stabilized_at")
        if stab is not None and len(j_arr):
            self._ax.axvline(x=stab, color="#2E7D32", linestyle=":", linewidth=1.2)
            self._ax.text(
                stab, float(np.max(j_arr)),
                f" \u2713 {stab:.0f} s", color="#2E7D32", fontsize=8, va="top",
            )

        self._ax2.tick_params(right=True, labelright=True)
        self._ax2.set_ylabel("PCE (%)", labelpad=8, color=_COLORS[3])
        self._ax.set_xlabel("Time (s)", labelpad=8)
        self._ax.set_ylabel("Current Density (mA/cm\u00b2)", labelpad=8, color=_COLORS[0])
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

        # Insert None at cycle boundaries to break the connecting line
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
                             linestyle="--", alpha=0.7, label="J (mA/cm\u00b2)")
        all_lines += [l1, l2]

        # Cycle boundary markers
        if len(cycle_idx) > 1:
            boundaries = np.where(np.diff(cycle_idx.astype(int)) != 0)[0]
            for bi in boundaries:
                t_b = float(times_min[bi + 1]) if bi + 1 < len(times_min) else None
                if t_b is not None:
                    self._ax.axvline(x=t_b, color="#9e9e9e", linestyle=":",
                                     linewidth=0.8, zorder=1)

        self._ax2.tick_params(right=True, labelright=True)
        self._ax2.set_ylabel("J (mA/cm\u00b2)", labelpad=8, color=_COLORS[0])
        self._ax.set_xlabel("Time (min)", labelpad=8)
        self._ax.set_ylabel("PCE (%)", labelpad=8, color=_COLORS[5])
        self._ax.set_title(title, fontsize=12, fontweight="bold", pad=12)
        self._ax.grid(True)
        self._fig.subplots_adjust(left=0.12, right=0.88, top=0.88)
