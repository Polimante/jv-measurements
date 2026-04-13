"""
Data Viewer — read-only Toplevel window for opening saved J-V Excel files.

Supports both illuminated J-V files (sheet: MeasurementData) and
dark J-V files (sheet: Dark_JV_Data) produced by app/io/exporter.py.
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

# Same palette as the main window
_COLORS = [
    "#1565C0", "#B71C1C", "#1B5E20", "#E65100", "#4A148C",
    "#006064", "#3E2723", "#37474F", "#880E4F", "#004D40",
]


# ── Excel parsing ─────────────────────────────────────────────────────────────

def _cell(ws, row_1idx, col_1idx):
    """Return a cell value; row/col are 1-indexed (openpyxl convention)."""
    return ws.cell(row=row_1idx, column=col_1idx).value


def _read_illuminated(ws):
    """
    Parse a MeasurementData worksheet.

    Layout (xlsxwriter 0-indexed → openpyxl 1-indexed):
      Row 1 : column headers
      Row 2 : scalar parameters
      Row 3+: V / J / P data arrays

    Columns (1-indexed):
      1-6   : V_f, J_f, P_f, V_r, J_r, P_r  (data rows only)
      7     : cell_area
      8-13  : Voc_f, Jsc_f, FF_f, PCE_f, Rs_f, Rsh_f
      14-19 : Voc_r, Jsc_r, FF_r, PCE_r, Rs_r, Rsh_r
      20    : HI
      21    : delay_ms
      22    : nplc
      23    : four_wire  (0 / 1)
      24    : prebias_v
      25    : prebias_t
      26    : instrument_idn
    """
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
    """
    Parse a Dark_JV_Data worksheet.

    Layout:
      Row 1 : column headers
      Row 2 : scalar parameters
      Row 3+: V / J data arrays

    Columns (1-indexed):
      1-4 : V_f, J_f, V_r, J_r  (data rows only)
      5   : cell_area
      6   : delay_ms
      7   : nplc
      8   : four_wire
      9   : instrument_idn
    """
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


def load_jv_file(filepath):
    """
    Open an xlsx file and return a parsed metadata dict.
    Raises ValueError for unrecognised formats.

    Note: read_only=True is intentionally NOT used here — mixing ws.cell()
    (random access for the params row) with iter_rows() in read-only mode
    causes openpyxl to return None values due to XML stream positioning.
    Files are small (<100 KB) so full load is fine.
    """
    wb = openpyxl.load_workbook(filepath, data_only=True)
    try:
        if _ILLUMINATED_SHEET in wb.sheetnames:
            return _read_illuminated(wb[_ILLUMINATED_SHEET])
        elif _DARK_SHEET in wb.sheetnames:
            return _read_dark(wb[_DARK_SHEET])
        else:
            raise ValueError(
                f"Unrecognised file format.\n"
                f"Expected sheet \"{_ILLUMINATED_SHEET}\" or \"{_DARK_SHEET}\".\n"
                f"Found: {', '.join(wb.sheetnames)}"
            )
    finally:
        wb.close()


# ── Formatting helpers ────────────────────────────────────────────────────────

def _fmt(val, fmt_str, fallback="\u2014"):
    """Format a numeric value; return fallback if None / NaN / error."""
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
    """
    Read-only Toplevel window that displays curves and metadata
    from a saved J-V or Dark J-V Excel file.

    Usage:
        DataViewerWindow(parent, filepath)
    """

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

    # ── UI skeleton (static, built once) ─────────────────────────────────────

    def _build_skeleton(self):
        # ── Top bar ───────────────────────────────────────────────────────────
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

        # ── Body ──────────────────────────────────────────────────────────────
        body = Frame(self.win)
        body.pack(fill=BOTH, expand=True)

        # Left: scrollable metadata panel (fixed width)
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

        # Mouse-wheel scrolling anywhere over the left panel
        for widget in (self._meta_canvas, self._meta_inner):
            widget.bind("<MouseWheel>", self._on_mousewheel)

        # Right: matplotlib plot
        right_frame = Frame(body)
        right_frame.pack(side=RIGHT, fill=BOTH, expand=True)

        self._fig = Figure(figsize=(8, 5.5))
        self._ax  = self._fig.add_subplot(111)
        self._ax2 = self._ax.twinx()
        self._ax2.yaxis.set_label_position("right")

        self._fig_canvas = FigureCanvasTkAgg(self._fig, right_frame)
        self._fig_canvas.get_tk_widget().pack(fill=BOTH, expand=True)

    def _on_inner_configure(self, _event):
        self._meta_canvas.configure(
            scrollregion=self._meta_canvas.bbox("all")
        )

    def _on_canvas_configure(self, event):
        self._meta_canvas.itemconfig(self._meta_win_id, width=event.width)

    def _on_mousewheel(self, event):
        self._meta_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    # ── Load / reload ─────────────────────────────────────────────────────────

    def _on_open_another(self):
        path = filedialog.askopenfilename(
            title="Open J-V Data File",
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
        type_str = "Illuminated J-V" if data["type"] == "illuminated" else "Dark J-V"

        self._title_lbl.config(
            text=f"\U0001f4c2  {filename}   \u2014   {type_str}"
        )
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
                bg=bg, font=("Arial", 9, "bold"),
                padx=6, pady=4,
            )
            lf.pack(fill=X, padx=6, pady=(4, 0))
            # propagate mousewheel to canvas
            lf.bind("<MouseWheel>", self._on_mousewheel)
            return lf

        def row(parent, label, value, fg="#2c3e50"):
            f = Frame(parent, bg=bg)
            f.pack(fill=X, pady=1)
            f.bind("<MouseWheel>", self._on_mousewheel)
            Label(
                f, text=label, bg=bg, fg="#607d8b",
                font=("Arial", 9), anchor=W, width=17,
            ).pack(side=LEFT, padx=(4, 0))
            Label(
                f, text=str(value), bg=bg, fg=fg,
                font=("Arial", 9, "bold"), anchor=W,
            ).pack(side=LEFT, padx=(2, 4))

        # ── File ──────────────────────────────────────────────────────────
        sf = section("File")
        # Truncate long filenames for display
        name_display = filename if len(filename) <= 34 else filename[:31] + "\u2026"
        row(sf, "Name:", name_display)
        type_label = "Illuminated J-V" if data["type"] == "illuminated" else "Dark J-V"
        type_color = "#1565C0" if data["type"] == "illuminated" else "#5d4e75"
        row(sf, "Type:", type_label, fg=type_color)

        # ── Measurement data (derived from arrays) ─────────────────────────
        v_f = data.get("v_f", np.array([]))
        v_r = data.get("v_r", np.array([]))

        sm = section("Measurement Data")
        row(sm, "Cell area:", _fmt(data.get("cell_area"), "%.4f cm\u00b2"))
        if len(v_f) >= 2:
            step = abs(float(np.mean(np.diff(v_f))))
            row(sm, "Fwd range:", f"{v_f[0]:.3f} \u2192 {v_f[-1]:.3f} V")
            row(sm, "Step \u2248:", f"{step:.4f} V")
        row(sm, "Fwd points:", str(len(v_f)))
        row(sm, "Rev points:", str(len(v_r)))

        # ── Instrument settings ────────────────────────────────────────────
        si = section("Instrument Settings")
        row(si, "Delay/pt:", _fmt(data.get("delay_ms"), "%.0f ms"))
        row(si, "NPLC:", _fmt(data.get("nplc"), "%.2f"))
        fw = data.get("four_wire")
        fw_str = ("ON" if int(fw) else "OFF") if fw is not None else "\u2014"
        row(si, "4-Wire:", fw_str)
        idn = str(data.get("instrument") or "\u2014")
        row(si, "Instrument:", (idn[:34] + "\u2026") if len(idn) > 34 else idn)

        if data["type"] == "illuminated":
            # ── Pre-bias ──────────────────────────────────────────────────
            sp = section("Pre-bias Conditioning")
            pb_v = data.get("prebias_v") or 0
            pb_t = data.get("prebias_t") or 0
            if pb_v == 0 and pb_t == 0:
                row(sp, "Status:", "Not used", fg="#9e9e9e")
            else:
                row(sp, "Voltage:", _fmt(pb_v, "%.3f V"))
                row(sp, "Duration:", _fmt(pb_t, "%.1f s"))

            # ── Forward scan ──────────────────────────────────────────────
            sf2 = section("Forward Scan")
            row(sf2, "Voc:",  _fmt(data.get("Voc_f"),  "%.4f V"),          "#1565C0")
            row(sf2, "Jsc:",  _fmt(data.get("Jsc_f"),  "%.4f mA/cm\u00b2"), "#1565C0")
            row(sf2, "FF:",   _fmt(data.get("FF_f"),   "%.2f %%"),          "#1565C0")
            row(sf2, "PCE:",  _fmt(data.get("PCE_f"),  "%.2f %%"),          "#1565C0")
            row(sf2, "Rs:",   _fmt(data.get("Rs_f"),   "%.2f \u03a9\u00b7cm\u00b2"))
            row(sf2, "Rsh:",  _fmt(data.get("Rsh_f"),  "%.2f \u03a9\u00b7cm\u00b2"))

            # ── Reverse scan ──────────────────────────────────────────────
            sr = section("Reverse Scan")
            row(sr, "Voc:",  _fmt(data.get("Voc_r"),  "%.4f V"),          "#B71C1C")
            row(sr, "Jsc:",  _fmt(data.get("Jsc_r"),  "%.4f mA/cm\u00b2"), "#B71C1C")
            row(sr, "FF:",   _fmt(data.get("FF_r"),   "%.2f %%"),          "#B71C1C")
            row(sr, "PCE:",  _fmt(data.get("PCE_r"),  "%.2f %%"),          "#B71C1C")
            row(sr, "Rs:",   _fmt(data.get("Rs_r"),   "%.2f \u03a9\u00b7cm\u00b2"))
            row(sr, "Rsh:",  _fmt(data.get("Rsh_r"),  "%.2f \u03a9\u00b7cm\u00b2"))

            # ── Hysteresis ────────────────────────────────────────────────
            sh = section("Hysteresis")
            hi = data.get("HI")
            try:
                hi_f = float(hi)
                hi_color = (
                    "#B71C1C" if hi_f > 10
                    else "#E65100" if hi_f > 5
                    else "#1B5E20"
                )
            except (TypeError, ValueError):
                hi_color = "#2c3e50"
            row(sh, "HI:", _fmt(hi, "%.2f %%"), hi_color)

        # Refresh scroll region after widget tree is built
        self._meta_inner.update_idletasks()
        self._meta_canvas.configure(
            scrollregion=self._meta_canvas.bbox("all")
        )
        self._meta_canvas.yview_moveto(0)   # scroll back to top on new file

    # ── Plot ──────────────────────────────────────────────────────────────────

    def _draw_plot(self, data, filename):
        import mplcursors

        # Disconnect previous callbacks before destroying axes
        if self._pick_cid is not None:
            self._fig.canvas.mpl_disconnect(self._pick_cid)
            self._pick_cid = None

        if self._cursor is not None:
            try:
                self._cursor.remove()
            except Exception:
                pass
            self._cursor = None

        # clf() + recreate avoids broken twinx state after clear()
        self._fig.clf()
        self._ax  = self._fig.add_subplot(111)
        self._ax2 = self._ax.twinx()
        self._ax2.yaxis.set_label_position("right")

        v_f = data["v_f"]
        j_f = data["j_f"]
        v_r = data["v_r"]
        j_r = data["j_r"]

        jv_lines  = []
        all_lines = []   # used for clickable legend

        if data["type"] == "illuminated":
            p_f = data["p_f"]
            p_r = data["p_r"]

            line_jf, = self._ax.plot(
                v_f, j_f,
                color=_COLORS[0], linestyle="-",
                marker="o", markersize=5,
                markerfacecolor=_COLORS[0], markeredgecolor=_COLORS[0],
                linewidth=1.8, label="Forward J-V", zorder=3,
            )
            line_jr, = self._ax.plot(
                v_r, j_r,
                color=_COLORS[1], linestyle="--",
                marker="o", markersize=5,
                markerfacecolor="white", markeredgecolor=_COLORS[1],
                markeredgewidth=1.5,
                linewidth=1.8, label="Reverse J-V", zorder=3,
            )
            line_pf, = self._ax2.plot(
                v_f, p_f,
                color=_COLORS[0], linestyle="-",
                marker="s", markersize=4,
                markerfacecolor=_COLORS[0], markeredgecolor=_COLORS[0],
                linewidth=1.2, alpha=0.5, label="Forward P", zorder=2,
            )
            line_pr, = self._ax2.plot(
                v_r, p_r,
                color=_COLORS[1], linestyle="--",
                marker="s", markersize=4,
                markerfacecolor="white", markeredgecolor=_COLORS[1],
                markeredgewidth=1.2,
                linewidth=1.2, alpha=0.5, label="Reverse P", zorder=2,
            )
            jv_lines  = [line_jf, line_jr]
            all_lines = [line_jf, line_jr, line_pf, line_pr]

            # Re-enable right axis
            self._ax2.tick_params(right=True, labelright=True)
            self._ax2.set_ylabel("Power Density (mW/cm\u00b2)", labelpad=8)
            self._fig.subplots_adjust(left=0.12, right=0.88, top=0.88)

            # PV params annotation (top-left of plot)
            ann_lines = []
            pce_f = data.get("PCE_f");  pce_r = data.get("PCE_r")
            voc_r = data.get("Voc_r");  jsc_r = data.get("Jsc_r")
            ff_r  = data.get("FF_r");   hi    = data.get("HI")
            if pce_f is not None:
                ann_lines.append(f"Fwd PCE = {_fmt(pce_f, '%.2f')} %")
            if pce_r is not None:
                ann_lines.append(f"Rev PCE = {_fmt(pce_r, '%.2f')} %")
            if voc_r is not None:
                ann_lines.append(f"Voc = {_fmt(voc_r, '%.4f')} V")
            if jsc_r is not None:
                ann_lines.append(f"Jsc = {_fmt(jsc_r, '%.3f')} mA/cm\u00b2")
            if ff_r is not None:
                ann_lines.append(f"FF  = {_fmt(ff_r, '%.2f')} %")
            if hi is not None:
                ann_lines.append(f"HI  = {_fmt(hi, '%.2f')} %")
            if ann_lines:
                self._ax.text(
                    0.02, 0.97, "\n".join(ann_lines),
                    transform=self._ax.transAxes,
                    va="top", ha="left", fontsize=8.5, color="#2c3e50",
                    bbox=dict(facecolor="white", alpha=0.85,
                              edgecolor="#90A4AE", boxstyle="round,pad=0.4"),
                )

        else:  # dark J-V
            line_jf, = self._ax.plot(
                v_f, j_f,
                color=_COLORS[4], linestyle="--", linewidth=1.6,
                alpha=0.85, label="Dark Forward J-V", zorder=3,
            )
            line_jr, = self._ax.plot(
                v_r, j_r,
                color=_COLORS[4], linestyle=":", linewidth=1.6,
                alpha=0.85, label="Dark Reverse J-V", zorder=3,
            )
            jv_lines  = [line_jf, line_jr]
            all_lines = [line_jf, line_jr]

            self._ax.axhline(0, color="#aaaaaa", linewidth=0.8,
                             linestyle="--", zorder=1)

            # Hide right axis — no power curve for dark measurements
            self._ax2.tick_params(right=False, labelright=False)
            self._ax2.set_ylabel("")
            self._fig.subplots_adjust(left=0.12, right=0.95, top=0.88)

        self._ax.set_xlabel("Voltage (V)", labelpad=8)
        self._ax.set_ylabel("Current Density (mA/cm\u00b2)", labelpad=8)
        title = os.path.splitext(filename)[0]
        self._ax.set_title(title, fontsize=12, fontweight="bold", pad=12)
        self._ax.set_xlim(0, 1.2)
        self._ax.set_ylim(-10, 30)
        if data["type"] == "illuminated":
            self._ax2.set_ylim(-10, 30)
        self._ax.grid(True)

        # Combined legend from both axes
        h1, l1 = self._ax.get_legend_handles_labels()
        h2, l2 = self._ax2.get_legend_handles_labels()
        legend = self._ax.legend(
            h1 + h2, l1 + l2,
            loc="upper right", frameon=True, framealpha=0.9, fontsize=9,
        )

        # ── Clickable legend (same logic as main window) ──────────────────
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

        # ── mplcursors hover tooltips ─────────────────────────────────────
        self._cursor = mplcursors.cursor(jv_lines, hover=True, highlight=True)

        @self._cursor.connect("add")
        def on_hover(sel):
            lbl = sel.artist.get_label()
            sel.annotation.set_text(
                f"{lbl}\nV = {sel.target[0]:.3f} V\nJ = {sel.target[1]:.3f} mA/cm\u00b2"
            )
            sel.annotation.get_bbox_patch().set(
                facecolor="white", alpha=0.92,
                edgecolor="#90A4AE", linewidth=0.8,
                boxstyle="round,pad=0.4",
            )
            sel.annotation.set_fontsize(9)

        self._fig_canvas.draw_idle()
