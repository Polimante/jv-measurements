"""
Solar Cell J-V Measurement Software
Version 2.3
Author: Lucas Polimante

Open-source software for perovskite solar cell characterisation.
Designed for use with the Keithley 2400 SourceMeter (GPIB or USB-GPIB).

Changelog v2.3
──────────────
- Added menu bar: File | Instrument | Help
- Added Instrument Connection dialog (scan → identify → connect → persist)
- Instrument object is now persistent across measurements (no reconnect overhead)
- Status bar now shows connected instrument model
- Start Measurement blocked until an instrument is connected
- Disconnect option added to Instrument menu
"""

import pyvisa
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import xlsxwriter
import os
import time
import threading
from datetime import datetime
from tkinter import (
    ttk, Tk, Menu, filedialog, Label, Entry, Button,
    StringVar, DoubleVar, Text, messagebox,
    Frame, LabelFrame, Toplevel, Checkbutton, BooleanVar,
    LEFT, RIGHT, BOTH, X, Y, W, END, DISABLED, NORMAL
)
from playsound import playsound
import winsound

# ── Global stop flag ──────────────────────────────────────────────────────────
stop_event = threading.Event()

APP_VERSION = "2.3"
APP_NAME    = f"Solar Cell J-V Measurement v{APP_VERSION}"


# ─────────────────────────────────────────────────────────────────────────────
# Tooltip helper
# ─────────────────────────────────────────────────────────────────────────────
class ToolTip:
    def __init__(self, widget, text):
        self.widget  = widget
        self.text    = text
        self.tooltip = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, event=None):
        x, y, _, _ = self.widget.bbox("insert")
        x += self.widget.winfo_rootx() + 25
        y += self.widget.winfo_rooty() + 25
        self.tooltip = Toplevel(self.widget)
        self.tooltip.wm_overrideredirect(True)
        self.tooltip.wm_geometry(f"+{x}+{y}")
        Label(self.tooltip, text=self.text, bg="lightyellow",
              relief="solid", borderwidth=1, justify=LEFT).pack()

    def hide(self, event=None):
        if self.tooltip:
            self.tooltip.destroy()
            self.tooltip = None


# ─────────────────────────────────────────────────────────────────────────────
# Instrument Connection Dialog
# ─────────────────────────────────────────────────────────────────────────────
class InstrumentDialog(Toplevel):
    """
    Modal dialog that:
      1. Scans all VISA resources available on the system.
      2. Queries each one for its *IDN? response (identification string).
      3. Displays the results in a table (resource address | model string).
      4. Lets the user select one and click Connect.

    The dialog sets self.selected_resource and self.selected_idn before
    closing, which the caller reads.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Connect Instrument")
        self.geometry("620x380")
        self.resizable(False, False)
        self.grab_set()          # modal

        self.selected_resource = None
        self.selected_idn      = None

        self._build_ui()
        self._scan()             # auto-scan on open

    def _build_ui(self):
        # ── Top instruction label ─────────────────────────────────────────────
        Label(self, text="Available VISA instruments on this computer:",
              font=("Arial", 10, "bold")).pack(anchor=W, padx=10, pady=(10, 2))

        # ── Treeview table ────────────────────────────────────────────────────
        table_frame = Frame(self)
        table_frame.pack(fill=BOTH, expand=True, padx=10, pady=4)

        columns = ("resource", "idn")
        self.tree = ttk.Treeview(table_frame, columns=columns,
                                 show="headings", selectmode="browse")
        self.tree.heading("resource", text="VISA Resource Address")
        self.tree.heading("idn",      text="Instrument Identification (*IDN?)")
        self.tree.column("resource",  width=200, anchor=W)
        self.tree.column("idn",       width=370, anchor=W)

        vsb = ttk.Scrollbar(table_frame, orient="vertical",
                             command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        vsb.pack(side=RIGHT, fill=Y)

        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # ── Status label ──────────────────────────────────────────────────────
        self.scan_status_var = StringVar(value="")
        Label(self, textvariable=self.scan_status_var,
              fg="gray").pack(anchor=W, padx=10)

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_frame = Frame(self)
        btn_frame.pack(fill=X, padx=10, pady=8)

        self.rescan_btn = Button(btn_frame, text="🔄  Rescan",
                                 command=self._scan, width=12)
        self.rescan_btn.pack(side=LEFT, padx=4)

        self.connect_btn = Button(btn_frame, text="✔  Connect",
                                  command=self._connect,
                                  bg="#4CAF50", fg="white",
                                  width=12, state=DISABLED)
        self.connect_btn.pack(side=LEFT, padx=4)

        Button(btn_frame, text="✖  Cancel",
               command=self.destroy,
               bg="#f44336", fg="white", width=12).pack(side=LEFT, padx=4)

    # ── Scan ──────────────────────────────────────────────────────────────────

    def _scan(self):
        """
        Enumerate VISA resources and query *IDN? for each.
        Runs synchronously (blocks the dialog briefly) because VISA
        timeouts are short (1 s) and user expects immediate feedback.
        """
        self.tree.delete(*self.tree.get_children())
        self.connect_btn.config(state=DISABLED)
        self.rescan_btn.config(state=DISABLED)
        self.scan_status_var.set("Scanning…  please wait.")
        self.update()

        try:
            rm        = pyvisa.ResourceManager()
            resources = rm.list_resources()
        except Exception as exc:
            self.scan_status_var.set(f"VISA error: {exc}")
            self.rescan_btn.config(state=NORMAL)
            return

        found = 0
        for addr in resources:
            idn = self._query_idn(rm, addr)
            self.tree.insert("", END, values=(addr, idn))
            found += 1

        if found == 0:
            self.scan_status_var.set(
                "No instruments found. Check GPIB/USB connections and drivers.")
        else:
            self.scan_status_var.set(
                f"{found} instrument(s) found. Select one and click Connect.")

        self.rescan_btn.config(state=NORMAL)

    @staticmethod
    def _query_idn(rm, addr):
        """Try to open a resource and query *IDN?. Return the response or an error string."""
        try:
            res = rm.open_resource(addr)
            res.timeout = 1500           # 1.5 s — enough for GPIB, not too slow
            idn = res.query("*IDN?").strip()
            res.close()
            return idn if idn else "(no response)"
        except Exception as exc:
            return f"(error: {exc})"

    def _on_select(self, event=None):
        if self.tree.selection():
            self.connect_btn.config(state=NORMAL)

    def _connect(self):
        sel = self.tree.selection()
        if not sel:
            return
        values = self.tree.item(sel[0], "values")
        self.selected_resource = values[0]
        self.selected_idn      = values[1]
        self.destroy()


# ─────────────────────────────────────────────────────────────────────────────
# Main Application
# ─────────────────────────────────────────────────────────────────────────────
class JVMeasurementApp:

    def __init__(self, root):
        self.root            = root
        self.input_widgets   = []
        # ── Persistent instrument state ───────────────────────────────────────
        # These are set by open_instrument_dialog() and cleared by disconnect().
        # run_measurement() uses self.instrument directly instead of opening
        # a new connection every time — zero reconnect overhead per measurement.
        self.instrument      = None   # pyvisa Resource object (open, persistent)
        self.instrument_addr = None   # VISA address string, e.g. "GPIB0::21::INSTR"
        self.instrument_idn  = None   # *IDN? string, e.g. "KEITHLEY,2400,…"

        self.setup_gui()
        self.detector_power_var.trace("w", self.update_illumination)
        self.detector_area_var.trace("w",  self.update_illumination)

    # ── GUI construction ──────────────────────────────────────────────────────

    def setup_gui(self):
        self.root.title(APP_NAME)
        self.root.geometry("1200x880")

        # ── Menu bar ──────────────────────────────────────────────────────────
        self._build_menubar()

        # ── Instrument status banner (below menu, above control panel) ────────
        banner = Frame(self.root, bg="#2c3e50", height=28)
        banner.pack(fill=X, side="top")
        self.instrument_label_var = StringVar(
            value="⚠  No instrument connected — use Instrument → Connect")
        Label(banner, textvariable=self.instrument_label_var,
              bg="#2c3e50", fg="#ecf0f1",
              font=("Arial", 9, "bold")).pack(side=LEFT, padx=10, pady=4)

        # ── Main body ─────────────────────────────────────────────────────────
        body = Frame(self.root)
        body.pack(fill=BOTH, expand=True)

        control_frame = LabelFrame(body, text="Control Panel", padx=10, pady=10)
        control_frame.pack(side=LEFT, fill=BOTH, expand=True)

        plot_frame = LabelFrame(body, text="J-V Curve")
        plot_frame.pack(side=RIGHT, fill=BOTH, expand=True)

        # ── Sample Information ────────────────────────────────────────────────
        sample_frame = LabelFrame(control_frame, text="Sample Information",
                                  padx=5, pady=5)
        sample_frame.pack(fill=X, padx=5, pady=5)

        Label(sample_frame, text="Sample Name:").grid(
            row=0, column=0, sticky=W, padx=5, pady=2)
        self.sample_name_var = StringVar()
        sample_entry = Entry(sample_frame, textvariable=self.sample_name_var,
                             width=25)
        sample_entry.grid(row=0, column=1, padx=5, pady=2)
        self.input_widgets.append(sample_entry)
        ToolTip(sample_entry, "Enter sample identifier (e.g., D1C1)")

        Label(sample_frame, text="Export Folder:").grid(
            row=1, column=0, sticky=W, padx=10, pady=2)
        self.export_folder_var = StringVar()
        self.export_entry = Entry(sample_frame,
                                  textvariable=self.export_folder_var, width=25)
        self.export_entry.grid(row=1, column=1, padx=5, pady=2)
        Button(sample_frame, text="Browse",
               command=self.browse_folder).grid(row=1, column=2, padx=5)
        self.input_widgets.append(self.export_entry)
        ToolTip(self.export_entry, "Select output directory for measurement data")

        # ── Measurement Parameters ────────────────────────────────────────────
        params_frame = LabelFrame(control_frame, text="Measurement Parameters",
                                  padx=5, pady=5)
        params_frame.pack(fill=X, padx=5, pady=5)

        for row, (label, name, default) in enumerate([
            ("Start Voltage (V):", "start_voltage", -0.1),
            ("End Voltage (V):",   "end_voltage",    1.05),
            ("Step Voltage (V):",  "step_voltage",   0.025),
            ("Cell Area (cm²):",   "cell_area",      0.07),
        ]):
            Label(params_frame, text=label).grid(
                row=row, column=0, sticky=W, padx=5, pady=2)
            var   = DoubleVar(value=default)
            entry = Entry(params_frame, textvariable=var, width=25)
            entry.grid(row=row, column=1, padx=5, pady=2)
            setattr(self, f"{name}_var", var)
            self.input_widgets.append(entry)
            ToolTip(entry, self._tooltip(name))

        # ── Instrument Settings ───────────────────────────────────────────────
        inst_frame = LabelFrame(control_frame, text="Instrument Settings",
                                padx=5, pady=5)
        inst_frame.pack(fill=X, padx=5, pady=5)

        Label(inst_frame, text="Delay per point (ms):").grid(
            row=0, column=0, sticky=W, padx=5, pady=2)
        self.delay_ms_var = DoubleVar(value=100.0)
        delay_entry = Entry(inst_frame, textvariable=self.delay_ms_var, width=10)
        delay_entry.grid(row=0, column=1, sticky=W, padx=5, pady=2)
        self.input_widgets.append(delay_entry)
        ToolTip(delay_entry,
                "Wait time (ms) between applying each voltage step and reading current.\n"
                "Perovskites typically need 100–500 ms. Corresponds to :SOUR:DEL.")

        Label(inst_frame, text="NPLC:").grid(
            row=1, column=0, sticky=W, padx=5, pady=2)
        self.nplc_var = DoubleVar(value=1.0)
        nplc_entry = Entry(inst_frame, textvariable=self.nplc_var, width=10)
        nplc_entry.grid(row=1, column=1, sticky=W, padx=5, pady=2)
        self.input_widgets.append(nplc_entry)
        ToolTip(nplc_entry,
                "Number of Power Line Cycles per reading (Brazil: 60 Hz → 1 NPLC ≈ 16.7 ms).\n"
                "Higher = less noise, slower. Range: 0.01 – 25.")

        self.four_wire_var = BooleanVar(value=False)
        cb4w = Checkbutton(inst_frame, text="4-Wire (Kelvin) sensing",
                           variable=self.four_wire_var)
        cb4w.grid(row=2, column=0, columnspan=2, sticky=W, padx=5, pady=2)
        ToolTip(cb4w,
                "Enables :SYST:RSEN ON — eliminates cable resistance from the\n"
                "measurement. Requires Force HI/LO + Sense HI/LO connections.")

        # ── Pre-bias Conditioning ─────────────────────────────────────────────
        prebias_frame = LabelFrame(control_frame, text="Pre-bias Conditioning",
                                   padx=5, pady=5)
        prebias_frame.pack(fill=X, padx=5, pady=5)

        self.prebias_enabled_var = BooleanVar(value=True)
        Checkbutton(prebias_frame,
                    text="Enable pre-bias before reverse scan",
                    variable=self.prebias_enabled_var,
                    command=self._toggle_prebias_entries
                    ).grid(row=0, column=0, columnspan=2, sticky=W, padx=5, pady=2)

        Label(prebias_frame, text="Pre-bias Voltage (V):").grid(
            row=1, column=0, sticky=W, padx=5, pady=2)
        self.prebias_voltage_var = DoubleVar(value=1.2)
        self.prebias_voltage_entry = Entry(
            prebias_frame, textvariable=self.prebias_voltage_var, width=10)
        self.prebias_voltage_entry.grid(row=1, column=1, sticky=W, padx=5, pady=2)
        self.input_widgets.append(self.prebias_voltage_entry)

        Label(prebias_frame, text="Pre-bias Duration (s):").grid(
            row=2, column=0, sticky=W, padx=5, pady=2)
        self.prebias_duration_var = DoubleVar(value=2.0)
        self.prebias_duration_entry = Entry(
            prebias_frame, textvariable=self.prebias_duration_var, width=10)
        self.prebias_duration_entry.grid(row=2, column=1, sticky=W, padx=5, pady=2)
        self.input_widgets.append(self.prebias_duration_entry)

        # ── Illumination Settings ─────────────────────────────────────────────
        illum_frame = LabelFrame(control_frame, text="Illumination Settings",
                                 padx=5, pady=5)
        illum_frame.pack(fill=X, padx=5, pady=5)

        Label(illum_frame, text="Detector Power (mW):").grid(
            row=0, column=0, sticky=W, padx=5, pady=2)
        self.detector_power_var = DoubleVar(value=0)
        pe = Entry(illum_frame, textvariable=self.detector_power_var, width=25)
        pe.grid(row=0, column=1, padx=5, pady=2)
        self.input_widgets.append(pe)

        Label(illum_frame, text="Detector Area (cm²):").grid(
            row=1, column=0, sticky=W, padx=5, pady=2)
        self.detector_area_var = DoubleVar(value=1.13)
        ae = Entry(illum_frame, textvariable=self.detector_area_var, width=25)
        ae.grid(row=1, column=1, padx=5, pady=2)
        self.input_widgets.append(ae)

        Label(illum_frame, text="Illumination (mW/cm²):").grid(
            row=2, column=0, sticky=W, padx=5, pady=2)
        self.illumination_var = StringVar(value="0.00")
        Label(illum_frame, textvariable=self.illumination_var).grid(
            row=2, column=1, sticky=W, padx=5, pady=2)

        # ── Control Buttons ───────────────────────────────────────────────────
        btn_frame = Frame(control_frame)
        btn_frame.pack(pady=10)

        self.start_btn = Button(btn_frame, text="Start Measurement",
                                command=self.start_measurement,
                                bg="#4CAF50", fg="white", width=15,
                                state=DISABLED)   # disabled until instrument connected
        self.start_btn.pack(side=LEFT, padx=5)

        self.stop_btn = Button(btn_frame, text="Stop Measurement",
                               command=self.stop_measurement,
                               bg="#f44336", fg="white", width=15,
                               state=DISABLED)
        self.stop_btn.pack(side=LEFT, padx=5)

        # ── Measurement Log ───────────────────────────────────────────────────
        log_frame = LabelFrame(control_frame, text="Measurement Log",
                               padx=5, pady=5)
        log_frame.pack(fill=BOTH, expand=True, padx=5, pady=5)

        self.log_text = Text(log_frame, wrap="none", height=10, state=DISABLED)
        vsb = ttk.Scrollbar(log_frame, orient="vertical",
                            command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=vsb.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self.log_text.tag_configure("header",    foreground="blue",
                                    font=("Arial", 10, "bold"))
        self.log_text.tag_configure("separator", foreground="gray")
        log_frame.grid_rowconfigure(0, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)

        # ── Status Bar ────────────────────────────────────────────────────────
        self.status_var = StringVar(value="Ready — connect an instrument to begin.")
        Label(control_frame, textvariable=self.status_var,
              bd=1, relief="sunken", anchor=W).pack(side="bottom", fill=X)

        # ── Plot ──────────────────────────────────────────────────────────────
        self.fig, self.ax = plt.subplots(figsize=(8, 5))
        self.ax2 = self.ax.twinx()
        self.ax2.yaxis.set_label_position("right")
        self.ax.set_xlabel("Voltage (V)", fontsize=12, labelpad=10)
        self.ax.set_ylabel("Current Density (mA/cm²)",
                           fontsize=12, labelpad=10, position=(0, 0.5))
        self.ax2.set_ylabel("Power Density (mW/cm²)",
                            fontsize=12, labelpad=10, position=(1, 0.5))
        self.canvas = FigureCanvasTkAgg(self.fig, plot_frame)
        self.canvas.get_tk_widget().pack(fill=BOTH, expand=True)
        self.draw_placeholder_plot()

    # ── Menu bar ──────────────────────────────────────────────────────────────

    def _build_menubar(self):
        menubar = Menu(self.root)

        # ── File ──────────────────────────────────────────────────────────────
        file_menu = Menu(menubar, tearoff=0)
        file_menu.add_command(label="Set Export Folder…",
                              command=self.browse_folder)
        file_menu.add_command(label="Export Measurement Log…",
                              command=self._export_log)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_exit)
        menubar.add_cascade(label="File", menu=file_menu)

        # ── Instrument ────────────────────────────────────────────────────────
        self.instrument_menu = Menu(menubar, tearoff=0)
        self.instrument_menu.add_command(
            label="Connect…",
            command=self.open_instrument_dialog)
        self.instrument_menu.add_command(
            label="Disconnect",
            command=self.disconnect_instrument,
            state=DISABLED)
        self.instrument_menu.add_separator()
        self.instrument_menu.add_command(
            label="Send *IDN? query",
            command=self._query_idn_live,
            state=DISABLED)
        menubar.add_cascade(label="Instrument", menu=self.instrument_menu)

        # ── Help ──────────────────────────────────────────────────────────────
        help_menu = Menu(menubar, tearoff=0)
        help_menu.add_command(label="About…", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)

    # ── Instrument connection management ──────────────────────────────────────

    def open_instrument_dialog(self):
        """
        Open the InstrumentDialog. If the user selects and connects an
        instrument, open a persistent pyvisa session and save it.

        The connection is opened here (once) and kept alive.
        run_measurement() uses self.instrument directly — no reconnect cost.
        """
        dlg = InstrumentDialog(self.root)
        self.root.wait_window(dlg)

        if dlg.selected_resource is None:
            return   # user cancelled

        addr = dlg.selected_resource
        idn  = dlg.selected_idn

        # Close any existing connection first
        self._close_instrument()

        try:
            rm  = pyvisa.ResourceManager()
            res = rm.open_resource(addr)
            res.timeout = 10000
        except Exception as exc:
            messagebox.showerror("Connection Error",
                                 f"Could not open {addr}:\n{exc}")
            return

        # Store persistent state
        self.instrument      = res
        self.instrument_addr = addr
        self.instrument_idn  = idn

        # Update UI
        short_idn = idn[:60] + ("…" if len(idn) > 60 else "")
        self.instrument_label_var.set(f"✔  Connected: {short_idn}  [{addr}]")
        self.status_var.set(f"Instrument connected: {addr}")
        self.start_btn.config(state=NORMAL)

        # Enable/disable menu items
        self.instrument_menu.entryconfig("Connect…",        state=NORMAL)
        self.instrument_menu.entryconfig("Disconnect",       state=NORMAL)
        self.instrument_menu.entryconfig("Send *IDN? query", state=NORMAL)

    def disconnect_instrument(self):
        """Close the persistent instrument connection and reset UI."""
        self._close_instrument()
        self.instrument_label_var.set(
            "⚠  No instrument connected — use Instrument → Connect")
        self.status_var.set("Instrument disconnected.")
        self.start_btn.config(state=DISABLED)
        self.instrument_menu.entryconfig("Disconnect",       state=DISABLED)
        self.instrument_menu.entryconfig("Send *IDN? query", state=DISABLED)

    def _close_instrument(self):
        """Safely close the pyvisa resource if open."""
        if self.instrument is not None:
            try:
                self.instrument.write(":OUTP OFF")
                self.instrument.close()
            except Exception:
                pass
            self.instrument      = None
            self.instrument_addr = None
            self.instrument_idn  = None

    def _query_idn_live(self):
        """Query *IDN? on the connected instrument and show the result."""
        if self.instrument is None:
            return
        try:
            idn = self.instrument.query("*IDN?").strip()
            messagebox.showinfo("*IDN? Response", idn)
        except Exception as exc:
            messagebox.showerror("Query Error", str(exc))

    # ── File menu actions ─────────────────────────────────────────────────────

    def _export_log(self):
        """Save the current Measurement Log contents to a .txt file."""
        content = self.log_text.get("1.0", END).strip()
        if not content:
            messagebox.showinfo("Export Log", "The log is empty — nothing to export.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            title="Export Measurement Log"
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            messagebox.showinfo("Export Log", f"Log saved to:\n{path}")

    def _on_exit(self):
        if messagebox.askokcancel("Exit", "Close the application?"):
            self._close_instrument()
            self.root.destroy()

    def _show_about(self):
        messagebox.showinfo(
            "About",
            f"{APP_NAME}\n\n"
            "Open-source J-V measurement software for perovskite solar cells.\n\n"
            "Designed for use with the Keithley 2400 SourceMeter.\n\n"
            "Author: Lucas Polimante\n"
            "License: MIT"
        )

    # ── GUI helpers ───────────────────────────────────────────────────────────

    def _tooltip(self, field_name):
        return {
            "start_voltage": "Initial measurement voltage (V)",
            "end_voltage":   "Final measurement voltage (V)",
            "step_voltage":  "Voltage step size (positive value)",
            "cell_area":     "Active area of solar cell (cm²)",
        }.get(field_name, "")

    def _toggle_prebias_entries(self):
        state = NORMAL if self.prebias_enabled_var.get() else DISABLED
        self.prebias_voltage_entry.config(state=state)
        self.prebias_duration_entry.config(state=state)

    def browse_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.export_folder_var.set(folder)
            self.export_entry.config(fg="black")

    def update_illumination(self, *args):
        try:
            self.illumination_var.set(
                f"{self.detector_power_var.get() / self.detector_area_var.get():.2f}")
        except Exception:
            self.illumination_var.set("Error")

    def validate_inputs(self):
        try:
            if not self.sample_name_var.get():
                raise ValueError("Sample name is required")
            if not self.export_folder_var.get():
                self.export_entry.config(fg="red")
                raise ValueError("Select an export folder")
            if self.instrument is None:
                raise ValueError("No instrument connected.\nUse Instrument → Connect.")
            if self.start_voltage_var.get() >= self.end_voltage_var.get():
                raise ValueError("Start voltage must be less than end voltage")
            if self.step_voltage_var.get() <= 0:
                raise ValueError("Step voltage must be positive")
            if self.cell_area_var.get() <= 0:
                raise ValueError("Cell area must be positive")
            if self.delay_ms_var.get() < 0:
                raise ValueError("Delay cannot be negative")
            if not (0.01 <= self.nplc_var.get() <= 25):
                raise ValueError("NPLC must be between 0.01 and 25")
            if self.prebias_enabled_var.get() and self.prebias_duration_var.get() <= 0:
                raise ValueError("Pre-bias duration must be positive")
            return True
        except Exception as exc:
            messagebox.showerror("Input Error", str(exc))
            return False

    def toggle_controls(self, enable=True):
        state = NORMAL if enable else DISABLED
        for w in self.input_widgets:
            w.config(state=state)
        # Start button: only enable if instrument is also connected
        self.start_btn.config(
            state=NORMAL if (enable and self.instrument is not None) else DISABLED)
        self.stop_btn.config(state=NORMAL if not enable else DISABLED)
        if enable:
            self._toggle_prebias_entries()

    def draw_placeholder_plot(self):
        self.ax.clear()
        self.ax2.clear()
        self.ax2.yaxis.set_label_position("right")
        self.ax.set_xlim(0, 1.1)
        self.ax.set_ylim(0, 30)
        self.ax2.set_ylim(0, 30)
        self.ax.set_xlabel("Voltage (V)", fontsize=12, labelpad=10)
        self.ax.set_ylabel("Current Density (mA/cm²)",
                           fontsize=12, labelpad=10, position=(0, 0.5))
        self.ax2.set_ylabel("Power Density (mW/cm²)",
                            fontsize=12, labelpad=10, position=(1, 0.5))
        self.ax.set_title("J-V Characteristics", fontsize=14,
                          fontweight="bold", pad=20)
        self.ax.text(0.5, 15,
                     'No measurement data\nClick "Start Measurement" to begin',
                     ha="center", va="center", fontsize=12, color="gray")
        self.fig.subplots_adjust(left=0.15, right=0.85, top=0.85)
        self.ax.grid(True)
        self.canvas.draw()

    # ── Measurement flow ──────────────────────────────────────────────────────

    def start_measurement(self):
        if not self.validate_inputs():
            return
        if not messagebox.askokcancel(
            "Safety Check",
            "1. Verify all connections to the SourceMeter\n"
            "2. Confirm solar simulator shutter is open\n\n"
            "Click OK to start measurement.",
            icon="warning"
        ):
            return
        self.toggle_controls(False)
        self.status_var.set("Initializing instrument…")
        threading.Thread(target=self.run_measurement, daemon=True).start()

    def stop_measurement(self):
        stop_event.set()
        self.status_var.set("Measurement aborted by user")
        self.toggle_controls(True)

    def add_to_log(self, results):
        self.log_text.config(state=NORMAL)
        if self.log_text.get("1.0", "end-1c"):
            self.log_text.insert(END, "\n" + "─"*60 + "\n", "separator")
        self.log_text.insert(
            END,
            f"Measurement Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
            "header"
        )
        self.log_text.insert(END, results)
        self.log_text.see(END)
        self.log_text.config(state=DISABLED)

    def update_results_display(self, fwd, rev, HI):
        Voc_f, Jsc_f, FF_f, PCE_f, Rs_f, Rsh_f = fwd
        Voc_r, Jsc_r, FF_r, PCE_r, Rs_r, Rsh_r = rev
        pb = (f"Pre-bias: {self.prebias_voltage_var.get():.2f} V "
              f"for {self.prebias_duration_var.get():.1f} s\n"
              if self.prebias_enabled_var.get() else "")
        self.add_to_log(
            f"Sample: {self.sample_name_var.get()}\n"
            f"Instrument: {self.instrument_idn or 'unknown'}\n"
            f"Illumination: {self.illumination_var.get()} mW/cm²\n"
            f"Delay/pt: {self.delay_ms_var.get():.0f} ms  |  "
            f"NPLC: {self.nplc_var.get():.2f}  |  "
            f"4-Wire: {'ON' if self.four_wire_var.get() else 'OFF'}\n"
            f"{pb}\n"
            f"Forward Scan:\n"
            f"  Voc:  {Voc_f:.3f} V\n  Jsc:  {Jsc_f:.3f} mA/cm²\n"
            f"  FF:   {FF_f:.2f} %\n  PCE:  {PCE_f:.2f} %\n"
            f"  Rs:   {Rs_f:.2f} Ω·cm²\n  Rsh:  {Rsh_f:.2f} Ω·cm²\n\n"
            f"Reverse Scan:\n"
            f"  Voc:  {Voc_r:.3f} V\n  Jsc:  {Jsc_r:.3f} mA/cm²\n"
            f"  FF:   {FF_r:.2f} %\n  PCE:  {PCE_r:.2f} %\n"
            f"  Rs:   {Rs_r:.2f} Ω·cm²\n  Rsh:  {Rsh_r:.2f} Ω·cm²\n\n"
            f"Hysteresis Index (HI): {HI:.4f}\n"
        )

    # ── Instrument configuration ──────────────────────────────────────────────

    def _init_instrument(self):
        """
        (Re-)configure the already-open self.instrument for a measurement run.
        This does NOT re-open the connection — it only sends SCPI commands.
        Takes < 50 ms.
        """
        inst      = self.instrument
        nplc      = self.nplc_var.get()
        delay_s   = self.delay_ms_var.get() / 1000.0

        inst.write("*RST")
        inst.write(":SOUR:FUNC VOLT")
        inst.write(":SENS:FUNC 'CURR:DC'")
        inst.write(":SENS:CURR:PROT 0.1")
        inst.write(f":SENS:CURR:NPLC {nplc:.3f}")
        inst.write(f":SOUR:DEL {delay_s:.4f}")
        inst.write(":SYST:RSEN ON" if self.four_wire_var.get() else ":SYST:RSEN OFF")
        inst.write(":OUTP ON")

    def _apply_prebias(self):
        v_pb = self.prebias_voltage_var.get()
        t_pb = self.prebias_duration_var.get()
        self.status_var.set(f"Pre-bias: {v_pb:.2f} V for {t_pb:.1f} s…")
        self.instrument.write(f":SOUR:VOLT {v_pb:.3f}")
        elapsed = 0.0
        while elapsed < t_pb:
            if stop_event.is_set():
                return
            time.sleep(0.1)
            elapsed += 0.1

    # ── Measurement thread ────────────────────────────────────────────────────

    def run_measurement(self):
        try:
            # Configure (no reconnect — uses persistent self.instrument)
            self._init_instrument()

            if self.prebias_enabled_var.get():
                self._apply_prebias()
                if stop_event.is_set():
                    return

            self.status_var.set("Measuring forward scan…")
            v_f, j_f = self.measure_scan(
                self.start_voltage_var.get(),
                self.end_voltage_var.get(),
                self.step_voltage_var.get()
            )
            if stop_event.is_set():
                return

            self.status_var.set("Measuring reverse scan…")
            v_r, j_r = self.measure_scan(
                self.end_voltage_var.get(),
                self.start_voltage_var.get(),
                -self.step_voltage_var.get()
            )
            if stop_event.is_set():
                return

            illum = float(self.illumination_var.get())
            Voc_f, Jsc_f, FF_f, PCE_f = self.calculate_jv_parameters(v_f, j_f, illum)
            Voc_r, Jsc_r, FF_r, PCE_r = self.calculate_jv_parameters(v_r, j_r, illum)
            Rs_f, Rsh_f = self.calculate_resistances(v_f, j_f, Voc_f)
            Rs_r, Rsh_r = self.calculate_resistances(v_r, j_r, Voc_r)
            HI = self.calculate_hysteresis_index(PCE_f, PCE_r)

            fwd = (Voc_f, Jsc_f, FF_f, PCE_f, Rs_f, Rsh_f)
            rev = (Voc_r, Jsc_r, FF_r, PCE_r, Rs_r, Rsh_r)

            self.update_results_display(fwd, rev, HI)
            self.update_plot(v_f, j_f, v_r, j_r, v_f*j_f, v_r*j_r)
            self.save_data(v_f, j_f, v_r, j_r, illum,
                           Voc_f, Jsc_f, FF_f, PCE_f, Rs_f, Rsh_f,
                           Voc_r, Jsc_r, FF_r, PCE_r, Rs_r, Rsh_r, HI)

            self.status_var.set("Measurement complete.")
            self.play_completion_sound()

        except Exception as exc:
            messagebox.showerror("Measurement Error", str(exc))
            self.status_var.set("Measurement failed.")
            winsound.Beep(500, 500)
        finally:
            # Turn output off but keep connection open for next measurement
            try:
                self.instrument.write(":OUTP OFF")
            except Exception:
                pass
            self.toggle_controls(True)
            stop_event.clear()

    # ── Scan loop ─────────────────────────────────────────────────────────────

    def measure_scan(self, start, end, step):
        safety = 0.02
        voltages, currents = [], []
        for v in np.arange(start, end + step, step):
            if stop_event.is_set():
                return None, None
            self.instrument.write(f":SOUR:VOLT {v:.3f}")
            time.sleep(safety)
            current = -float(self.instrument.query(":READ?").split(",")[1])
            currents.append((current * 1000) / self.cell_area_var.get())
            voltages.append(v)
        return np.array(voltages), np.array(currents)

    # ── Calculations ──────────────────────────────────────────────────────────

    def calculate_jv_parameters(self, voltages, currents, illumination):
        power         = voltages * currents
        idx_mp        = np.argmax(power)
        Vm, Jm        = voltages[idx_mp], currents[idx_mp]
        idx_cross     = np.where(np.diff(np.sign(currents)))[0][0]
        Voc           = voltages[idx_cross]
        Jsc           = currents[np.argmin(np.abs(voltages))]
        FF            = (Vm * Jm) / (Voc * Jsc) * 100
        PCE           = (Voc * Jsc * FF) / illumination
        return Voc, abs(Jsc), FF, PCE

    def calculate_resistances(self, voltages, currents, Voc, n=5):
        try:
            zi        = np.argmin(np.abs(voltages))
            s0, e0    = max(0, zi - n//2), min(len(voltages), zi - n//2 + n)
            m_rsh, _  = np.polyfit(voltages[s0:e0], currents[s0:e0], 1)
            Rsh       = (1/m_rsh)*1000 if m_rsh else float("inf")
            vi        = np.argmin(np.abs(voltages - Voc))
            s1, e1    = max(0, vi - n), min(len(voltages), vi + 1)
            m_rs, _   = np.polyfit(voltages[s1:e1], currents[s1:e1], 1)
            Rs        = (1/abs(m_rs))*1000 if m_rs else float("inf")
            return Rs, Rsh
        except Exception:
            return float("nan"), float("nan")

    def calculate_hysteresis_index(self, PCE_f, PCE_r):
        return (PCE_r - PCE_f) / PCE_r if PCE_r else float("nan")

    # ── Plot ──────────────────────────────────────────────────────────────────

    def update_plot(self, v_f, j_f, v_r, j_r, p_f, p_r):
        self.ax.clear()
        self.ax2.clear()
        self.ax2.yaxis.set_label_position("right")
        self.ax.set_xlim(0, 1.1)
        self.ax.set_ylim(0, 30)
        self.ax2.set_ylim(0, 30)
        cf, = self.ax.plot(v_f, j_f, "b-",  label="Forward Current")
        cr, = self.ax.plot(v_r, j_r, "r-",  label="Reverse Current")
        pf, = self.ax2.plot(v_f, p_f, "b--", label="Forward Power")
        pr, = self.ax2.plot(v_r, p_r, "r--", label="Reverse Power")
        name = self.sample_name_var.get() or "Unnamed Sample"
        self.ax.set_title(f"{name}\nJ-V Characteristics",
                          fontsize=14, fontweight="bold", pad=20)
        lines = [cf, cr, pf, pr]
        self.ax.legend(lines, [l.get_label() for l in lines],
                       loc="upper right", frameon=True, framealpha=0.9)
        self.ax.set_xlabel("Voltage (V)", fontsize=12, labelpad=10)
        self.ax.set_ylabel("Current Density (mA/cm²)",
                           fontsize=12, labelpad=10, position=(0, 0.5))
        self.ax2.set_ylabel("Power Density (mW/cm²)",
                            fontsize=12, labelpad=10, position=(1, 0.5))
        self.fig.subplots_adjust(left=0.15, right=0.85, top=0.85)
        self.ax.grid(True)
        self.canvas.draw()

    # ── Excel export ──────────────────────────────────────────────────────────

    def save_data(self, v_f, j_f, v_r, j_r, illum,
                  Voc_f, Jsc_f, FF_f, PCE_f, Rs_f, Rsh_f,
                  Voc_r, Jsc_r, FF_r, PCE_r, Rs_r, Rsh_r, HI):

        p_f = v_f * j_f
        p_r = v_r * j_r
        ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
        fp  = os.path.join(self.export_folder_var.get(),
                           f"{self.sample_name_var.get()}_{ts}.xlsx")

        wb  = xlsxwriter.Workbook(fp)
        ws  = wb.add_worksheet("MeasurementData")
        hf  = wb.add_format({"bold":True,"border":1,"align":"center","valign":"vcenter"})
        nf  = wb.add_format({"num_format":"0.000000"})
        pf  = wb.add_format({"num_format":"0.0000","bold":True})

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
            "Hysteresis Index (HI)",
            "Delay per point (ms)", "NPLC", "4-Wire Sensing",
            "Pre-bias Voltage (V)", "Pre-bias Duration (s)",
            "Instrument IDN",
        ]
        for c, h in enumerate(headers):
            ws.write(0, c, h, hf)

        pb_v = self.prebias_voltage_var.get() if self.prebias_enabled_var.get() else 0.0
        pb_t = self.prebias_duration_var.get() if self.prebias_enabled_var.get() else 0.0

        for col, val in enumerate([
            None, None, None, None, None, None,          # A-F: data, not params
            self.cell_area_var.get(),                    # G
            Voc_f, Jsc_f, FF_f, PCE_f, Rs_f, Rsh_f,    # H-M
            Voc_r, Jsc_r, FF_r, PCE_r, Rs_r, Rsh_r,    # N-S
            HI,                                          # T
            self.delay_ms_var.get(),                     # U
            self.nplc_var.get(),                         # V
            1 if self.four_wire_var.get() else 0,        # W
            pb_v, pb_t,                                  # X, Y
            self.instrument_idn or "",                   # Z
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

    # ── Sound ─────────────────────────────────────────────────────────────────

    def play_completion_sound(self):
        path = r"C:\Users\aspol\Desktop\JV measurements - Lucas Polimante - v0\sounds\success.mp3"
        try:
            playsound(path)
        except Exception:
            winsound.Beep(1000, 500)


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    root = Tk()
    app  = JVMeasurementApp(root)
    root.protocol("WM_DELETE_WINDOW", app._on_exit)
    root.mainloop()
