import os
import threading
import winsound
import tempfile
import webbrowser
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from tkinter import (
    ttk, Menu, filedialog, Label, Entry, Button,
    StringVar, DoubleVar, Text, messagebox,
    Frame, LabelFrame, Checkbutton, BooleanVar,
    LEFT, RIGHT, BOTH, X, Y, W, END, DISABLED, NORMAL,
)
from datetime import datetime
import pyvisa

PLOT_COLORS = [
    "#1565C0",  # deep blue
    "#B71C1C",  # deep red
    "#1B5E20",  # deep green
    "#E65100",  # deep orange
    "#4A148C",  # deep purple
    "#006064",  # deep cyan
    "#3E2723",  # deep brown
    "#37474F",  # blue-grey
    "#880E4F",  # deep pink
    "#004D40",  # deep teal
]

from app.utils.constants import APP_NAME, stop_event
from app.utils.sound import play_completion_sound
from app.gui.tooltip import ToolTip
from app.gui.instrument_dialog import InstrumentDialog
from app.measurement.analysis import (
    calculate_jv_parameters,
    calculate_resistances,
    calculate_hysteresis_index,
)
from app.instrument.keithley import (
    init_instrument, apply_prebias, apply_light_soaking, measure_scan,
)
from app.io.exporter import save_data, save_dark_jv_data
from app.gui.data_viewer import DataViewerWindow


class JVMeasurementApp:

    def __init__(self, root):
        self.root            = root
        self.input_widgets   = []
        self.session_data    = []
        self._pick_cid       = None   # mpl pick_event connection id
        # Persistent instrument state — set by open_instrument_dialog(),
        # cleared by disconnect().  run_measurement() uses self.instrument
        # directly with no reconnect overhead.
        self.instrument      = None
        self.instrument_addr = None
        self.instrument_idn  = None
        self.current_mode         = "jv"
        self._measurement_running = False

        self.setup_gui()
        # J-V traces
        self.detector_power_var.trace("w", self.update_illumination)
        self.detector_area_var.trace("w",  self.update_illumination)
        self.step_voltage_var.trace("w",   self.update_scan_rate)
        self.delay_ms_var.trace("w",       self.update_scan_rate)
        self.nplc_var.trace("w",           self.update_scan_rate)
        self.update_scan_rate()
        # MPP traces
        self.mpp_detector_power_var.trace("w", self.update_mpp_illumination)
        self.mpp_detector_area_var.trace("w",  self.update_mpp_illumination)
        self.mpp_step_v_var.trace("w",  self.update_mpp_scan_rate)
        self.mpp_delay_var.trace("w",   self.update_mpp_scan_rate)
        self.mpp_nplc_var.trace("w",    self.update_mpp_scan_rate)
        self.update_mpp_scan_rate()
        # Stab traces
        self.stab_detector_power_var.trace("w", self.update_stab_illumination)
        self.stab_detector_area_var.trace("w",  self.update_stab_illumination)

    # ── GUI construction ──────────────────────────────────────────────────────

    def setup_gui(self):
        self.root.title(APP_NAME)
        self.root.state("zoomed")          # maximised on launch (Windows)
        self.root.minsize(1100, 640)

        self._build_menubar()

        # ── Instrument status banner ──────────────────────────────────────────
        banner = Frame(self.root, bg="#2c3e50", height=30)
        banner.pack(fill=X, side="top")
        banner.pack_propagate(False)
        self.instrument_label_var = StringVar(
            value="\u26a0  No instrument connected \u2014 use Instrument \u2192 Connect")
        Label(banner, textvariable=self.instrument_label_var,
              bg="#2c3e50", fg="#ecf0f1",
              font=("Arial", 9, "bold")).pack(side=LEFT, padx=12, pady=6)

        # ── Body layout: resizable PanedWindow ───────────────────────────────
        paned = ttk.PanedWindow(self.root, orient="horizontal")
        paned.pack(fill=BOTH, expand=True)

        left = Frame(paned, bg="#ececec")
        paned.add(left, weight=0)

        plot_frame = Frame(paned, bg="#ffffff")
        paned.add(plot_frame, weight=1)

        # Set initial sash position once the paned window has real dimensions
        _sash_set = [False]
        def _on_paned_configure(event):
            if not _sash_set[0] and paned.winfo_width() > 10:
                paned.sashpos(0, 480)
                _sash_set[0] = True
        paned.bind("<Configure>", _on_paned_configure)

        # ── Mode panel wrappers (one per mode, packed/forgotten on switch) ────
        self.jv_panel      = Frame(left,       bg="#ececec")
        self.jv_plot_frame = Frame(plot_frame, bg="#ffffff")
        self.jv_panel.pack(fill=BOTH, expand=True)
        self.jv_plot_frame.pack(fill=BOTH, expand=True)

        # ── Settings notebook (3 tabs) ────────────────────────────────────────
        style = ttk.Style()
        style.configure("TNotebook.Tab", padding=[10, 4], font=("Arial", 9))

        nb = ttk.Notebook(self.jv_panel)
        nb.pack(fill=X, padx=6, pady=(6, 4))

        # ─── Tab 1: Scan ──────────────────────────────────────────────────────
        tab_scan = Frame(nb, padx=6, pady=4, bg="#ececec")
        nb.add(tab_scan, text="  Scan  ")

        # Sample Information
        sf = LabelFrame(tab_scan, text="Sample Information", padx=6, pady=4)
        sf.pack(fill=X, pady=(0, 6))

        Label(sf, text="Sample Name:").grid(
            row=0, column=0, sticky=W, padx=4, pady=3)
        self.sample_name_var = StringVar()
        sample_entry = Entry(sf, textvariable=self.sample_name_var, width=22)
        sample_entry.grid(row=0, column=1, columnspan=2, sticky=W, padx=4, pady=3)
        self.input_widgets.append(sample_entry)
        ToolTip(sample_entry, "Enter sample identifier (e.g., D1C1)")

        Label(sf, text="Export Folder:").grid(
            row=1, column=0, sticky=W, padx=4, pady=3)
        self.export_folder_var = StringVar()
        self.export_entry = Entry(sf, textvariable=self.export_folder_var, width=18)
        self.export_entry.grid(row=1, column=1, sticky=W, padx=4, pady=3)
        Button(sf, text="Browse\u2026", command=self.browse_folder,
               font=("Arial", 8), pady=1
               ).grid(row=1, column=2, padx=(2, 4), pady=3)
        self.input_widgets.append(self.export_entry)
        ToolTip(self.export_entry, "Select output directory for measurement data")

        # Scan Parameters
        pf = LabelFrame(tab_scan, text="Scan Parameters", padx=6, pady=4)
        pf.pack(fill=X, pady=(0, 6))

        for row, (label, name, default, unit) in enumerate([
            ("Start Voltage:", "start_voltage", -0.1,  "V"),
            ("End Voltage:",   "end_voltage",    1.05,  "V"),
            ("Step Voltage:",  "step_voltage",   0.025, "V"),
            ("Cell Area:",     "cell_area",      0.07,  "cm\u00b2"),
        ]):
            Label(pf, text=label).grid(row=row, column=0, sticky=W, padx=4, pady=3)
            var   = DoubleVar(value=default)
            entry = Entry(pf, textvariable=var, width=10)
            entry.grid(row=row, column=1, sticky=W, padx=4, pady=3)
            Label(pf, text=unit, fg="#666666").grid(row=row, column=2, sticky=W)
            setattr(self, f"{name}_var", var)
            self.input_widgets.append(entry)
            ToolTip(entry, self._tooltip(name))

        # Illumination Settings
        ilf = LabelFrame(tab_scan, text="Illumination", padx=6, pady=4)
        ilf.pack(fill=X, pady=(0, 4))

        Label(ilf, text="Detector Power:").grid(
            row=0, column=0, sticky=W, padx=4, pady=3)
        self.detector_power_var = DoubleVar(value=0)
        pe = Entry(ilf, textvariable=self.detector_power_var, width=10)
        pe.grid(row=0, column=1, sticky=W, padx=4, pady=3)
        Label(ilf, text="mW", fg="#666666").grid(row=0, column=2, sticky=W)
        self.input_widgets.append(pe)

        Label(ilf, text="Detector Area:").grid(
            row=1, column=0, sticky=W, padx=4, pady=3)
        self.detector_area_var = DoubleVar(value=1.13)
        ae = Entry(ilf, textvariable=self.detector_area_var, width=10)
        ae.grid(row=1, column=1, sticky=W, padx=4, pady=3)
        Label(ilf, text="cm\u00b2", fg="#666666").grid(row=1, column=2, sticky=W)
        self.input_widgets.append(ae)

        Label(ilf, text="Illumination:").grid(
            row=2, column=0, sticky=W, padx=4, pady=3)
        self.illumination_var = StringVar(value="0.00")
        Label(ilf, textvariable=self.illumination_var,
              font=("Arial", 9, "bold"), fg="#1565C0").grid(
            row=2, column=1, sticky=W, padx=4, pady=3)
        Label(ilf, text="mW/cm\u00b2", fg="#666666").grid(row=2, column=2, sticky=W)

        # ─── Tab 2: Instrument ────────────────────────────────────────────────
        tab_inst = Frame(nb, padx=6, pady=4, bg="#ececec")
        nb.add(tab_inst, text="  Instrument  ")

        inf = LabelFrame(tab_inst, text="Keithley 2400 Settings", padx=6, pady=4)
        inf.pack(fill=X, pady=(0, 6))

        Label(inf, text="Delay per point:").grid(
            row=0, column=0, sticky=W, padx=4, pady=4)
        self.delay_ms_var = DoubleVar(value=100.0)
        delay_entry = Entry(inf, textvariable=self.delay_ms_var, width=10)
        delay_entry.grid(row=0, column=1, sticky=W, padx=4, pady=4)
        Label(inf, text="ms", fg="#666666").grid(row=0, column=2, sticky=W)
        self.input_widgets.append(delay_entry)
        ToolTip(delay_entry,
                "Wait time (ms) between applying each voltage step and reading current.\n"
                "Perovskites typically need 100\u2013500 ms. Corresponds to :SOUR:DEL.")

        Label(inf, text="NPLC:").grid(
            row=1, column=0, sticky=W, padx=4, pady=4)
        self.nplc_var = DoubleVar(value=1.0)
        nplc_entry = Entry(inf, textvariable=self.nplc_var, width=10)
        nplc_entry.grid(row=1, column=1, sticky=W, padx=4, pady=4)
        Label(inf, text="(0.01\u201325)", fg="#666666").grid(row=1, column=2, sticky=W)
        self.input_widgets.append(nplc_entry)
        ToolTip(nplc_entry,
                "Number of Power Line Cycles per reading (Brazil: 60 Hz \u2192 1 NPLC \u2248 16.7 ms).\n"
                "Higher = less noise, slower. Range: 0.01 \u2013 25.")

        self.four_wire_var = BooleanVar(value=False)
        cb4w = Checkbutton(inf, text="4-Wire (Kelvin) sensing",
                           variable=self.four_wire_var)
        cb4w.grid(row=2, column=0, columnspan=3, sticky=W, padx=4, pady=4)
        ToolTip(cb4w,
                "Enables :SYST:RSEN ON \u2014 eliminates cable resistance from the\n"
                "measurement. Requires Force HI/LO + Sense HI/LO connections.")

        srf = LabelFrame(tab_inst, text="Calculated Scan Rate", padx=6, pady=4)
        srf.pack(fill=X, pady=(0, 4))

        self.scan_rate_var = StringVar(value="\u2014")
        sr_lbl = Label(srf, textvariable=self.scan_rate_var,
                       font=("Arial", 11, "bold"), fg="#1565C0")
        sr_lbl.grid(row=0, column=0, sticky=W, padx=4, pady=4)
        Label(srf, text="mV/s", fg="#666666").grid(row=0, column=1, sticky=W)
        ToolTip(sr_lbl,
                "Effective voltage sweep rate, calculated automatically from:\n"
                "  Scan rate (mV/s) = Step Voltage (mV) \u00f7 Time per point (s)\n\n"
                "Time per point = Delay + NPLC \u00d7 16.7 ms + 20 ms (fixed margin)\n\n"
                "For perovskite solar cells, typical values in the literature:\n"
                "  Fast scan:   50\u2013200 mV/s  (maximises hysteresis)\n"
                "  Standard:    10\u2013100 mV/s  (good balance of speed and accuracy)\n"
                "  Slow scan:   1\u201310 mV/s    (approaches quasi-steady-state)\n\n"
                "Always report this value alongside Step Voltage and Delay when publishing.")

        # ─── Tab 3: Conditioning ──────────────────────────────────────────────
        tab_cond = Frame(nb, padx=6, pady=4, bg="#ececec")
        nb.add(tab_cond, text="  Conditioning  ")

        # Pre-bias
        pbf = LabelFrame(tab_cond, text="Pre-bias Conditioning", padx=6, pady=4)
        pbf.pack(fill=X, pady=(0, 6))

        self.prebias_enabled_var = BooleanVar(value=True)
        Checkbutton(pbf, text="Enable pre-bias before reverse scan",
                    variable=self.prebias_enabled_var,
                    command=self._toggle_prebias_entries
                    ).grid(row=0, column=0, columnspan=3, sticky=W, padx=4, pady=2)

        Label(pbf, text="Voltage:").grid(row=1, column=0, sticky=W, padx=4, pady=3)
        self.prebias_voltage_var = DoubleVar(value=1.2)
        self.prebias_voltage_entry = Entry(
            pbf, textvariable=self.prebias_voltage_var, width=10)
        self.prebias_voltage_entry.grid(row=1, column=1, sticky=W, padx=4, pady=3)
        Label(pbf, text="V", fg="#666666").grid(row=1, column=2, sticky=W)
        self.input_widgets.append(self.prebias_voltage_entry)

        Label(pbf, text="Duration:").grid(row=2, column=0, sticky=W, padx=4, pady=3)
        self.prebias_duration_var = DoubleVar(value=2.0)
        self.prebias_duration_entry = Entry(
            pbf, textvariable=self.prebias_duration_var, width=10)
        self.prebias_duration_entry.grid(row=2, column=1, sticky=W, padx=4, pady=3)
        Label(pbf, text="s", fg="#666666").grid(row=2, column=2, sticky=W)
        self.input_widgets.append(self.prebias_duration_entry)

        # Dark J-V
        dkf = LabelFrame(tab_cond, text="Dark J-V Measurement", padx=6, pady=4)
        dkf.pack(fill=X, pady=(0, 6))

        self.dark_jv_var = BooleanVar(value=False)
        dark_cb = Checkbutton(dkf,
                              text="Include Dark J-V before illuminated scan",
                              variable=self.dark_jv_var)
        dark_cb.pack(anchor=W, padx=4, pady=4)
        ToolTip(dark_cb,
                "If checked: measures forward + reverse J-V with the shutter CLOSED first,\n"
                "saves [SAMPLE]_Dark_JV_[timestamp].xlsx, then prompts you to open the\n"
                "shutter before the normal illuminated measurement.")

        # Light Soaking
        lsf = LabelFrame(tab_cond, text="Light Soaking", padx=6, pady=4)
        lsf.pack(fill=X, pady=(0, 4))

        self.light_soaking_var = BooleanVar(value=False)
        ls_cb = Checkbutton(lsf,
                            text="Enable light soaking before illuminated scan",
                            variable=self.light_soaking_var,
                            command=self._toggle_light_soaking_entry)
        ls_cb.grid(row=0, column=0, columnspan=3, sticky=W, padx=4, pady=2)
        ToolTip(ls_cb,
                "Holds the cell at open circuit under illumination for the set duration\n"
                "before the J-V scan. Allows mobile ions to reach a stabilised state.\n"
                "Recommended: 60 s (n-i-p) to 300 s (p-i-n). Default: 120 s.")

        Label(lsf, text="Duration:").grid(row=1, column=0, sticky=W, padx=4, pady=3)
        self.light_soaking_duration_var = DoubleVar(value=120.0)
        self.light_soaking_entry = Entry(
            lsf, textvariable=self.light_soaking_duration_var, width=10,
            state=DISABLED)
        self.light_soaking_entry.grid(row=1, column=1, sticky=W, padx=4, pady=3)
        Label(lsf, text="s", fg="#666666").grid(row=1, column=2, sticky=W)
        ToolTip(self.light_soaking_entry,
                "Soaking time in seconds.\n"
                "Literature: ~60 s for n-i-p TiO\u2082; 120\u2013300 s for p-i-n PEDOT:PSS.")

        # ── Control Buttons ───────────────────────────────────────────────────
        btn_frame = Frame(self.jv_panel, bg="#ececec")
        btn_frame.pack(fill=X, padx=8, pady=6)

        self.start_btn = Button(btn_frame, text="Start Measurement",
                                command=self.start_measurement,
                                bg="#388E3C", fg="white",
                                font=("Arial", 9, "bold"),
                                width=16, pady=4, state=DISABLED)
        self.start_btn.pack(side=LEFT, padx=(0, 6))

        self.stop_btn = Button(btn_frame, text="Stop Measurement",
                               command=self.stop_measurement,
                               bg="#C62828", fg="white",
                               font=("Arial", 9, "bold"),
                               width=16, pady=4, state=DISABLED)
        self.stop_btn.pack(side=LEFT)

        # ── Measurement Log ───────────────────────────────────────────────────
        log_frame = LabelFrame(self.jv_panel, text="Measurement Log", padx=4, pady=4)
        log_frame.pack(fill=BOTH, expand=True, padx=6, pady=(0, 0))

        hsb = ttk.Scrollbar(log_frame, orient="horizontal")
        vsb = ttk.Scrollbar(log_frame, orient="vertical")
        self.log_text = Text(log_frame, wrap="none", state=DISABLED,
                             font=("Consolas", 9),
                             yscrollcommand=vsb.set,
                             xscrollcommand=hsb.set)
        vsb.config(command=self.log_text.yview)
        hsb.config(command=self.log_text.xview)
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_text.tag_configure("header",
                                    foreground="#1565C0",
                                    font=("Consolas", 9, "bold"))
        self.log_text.tag_configure("dark_label",
                                    foreground="#5d4e75",
                                    font=("Consolas", 9, "bold"))
        self.log_text.tag_configure("separator", foreground="#aaaaaa")
        log_frame.grid_rowconfigure(0, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)

        # ── Status Bar ────────────────────────────────────────────────────────
        status_bar = Frame(self.jv_panel, bg="#37474F", height=24)
        status_bar.pack(fill=X, side="bottom")
        status_bar.pack_propagate(False)
        self.status_var = StringVar(value="Ready \u2014 connect an instrument to begin.")
        Label(status_bar, textvariable=self.status_var,
              bg="#37474F", fg="#CFD8DC",
              font=("Arial", 8), anchor=W,
              ).pack(side=LEFT, padx=10, fill=X, expand=True)

        # ── Plot ──────────────────────────────────────────────────────────────
        self.fig, self.ax = plt.subplots(figsize=(8, 5))
        self.ax2 = self.ax.twinx()
        self.ax2.yaxis.set_label_position("right")
        self.ax.set_xlabel("Voltage (V)", fontsize=12, labelpad=10)
        self.ax.set_ylabel("Current Density (mA/cm\u00b2)",
                           fontsize=12, labelpad=10, position=(0, 0.5))
        self.ax2.set_ylabel("Power Density (mW/cm\u00b2)",
                            fontsize=12, labelpad=10, position=(1, 0.5))
        self.canvas = FigureCanvasTkAgg(self.fig, self.jv_plot_frame)
        self.canvas.get_tk_widget().pack(fill=BOTH, expand=True)
        self.cursor = None   # mplcursors handle — kept to avoid garbage collection
        self.draw_placeholder_plot()

        # ── Build additional mode panels (hidden until mode is switched) ──────
        self._build_mpp_panel(left, plot_frame)
        self._build_stab_panel(left, plot_frame)

        # ── Keyboard shortcuts for mode switching ─────────────────────────────
        self.root.bind("<Alt-F2>", lambda e: self.switch_mode("jv"))
        self.root.bind("<Alt-F3>", lambda e: self.switch_mode("mpp"))
        self.root.bind("<Alt-F5>", lambda e: self.switch_mode("stab"))

    # ── Menu bar ──────────────────────────────────────────────────────────────

    def _build_menubar(self):
        menubar = Menu(self.root)

        file_menu = Menu(menubar, tearoff=0)
        file_menu.add_command(label="Set Export Folder\u2026",
                              command=self.browse_folder)
        file_menu.add_command(label="Export Measurement Log\u2026",
                              command=self._export_log)
        file_menu.add_separator()
        file_menu.add_command(label="Open Data File\u2026",
                              command=self._open_data_viewer)
        file_menu.add_separator()
        file_menu.add_command(label="Clear Session Data",
                              command=self._clear_session_data)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_exit)
        menubar.add_cascade(label="File", menu=file_menu)

        self.instrument_menu = Menu(menubar, tearoff=0)
        self.instrument_menu.add_command(
            label="Connect\u2026",
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

        self.window_menu = Menu(menubar, tearoff=0)
        self.window_menu.add_command(
            label="\u2713 J-V Measurement",
            accelerator="Alt+F2",
            command=lambda: self.switch_mode("jv"))
        self.window_menu.add_command(
            label="  MPP Tracking",
            accelerator="Alt+F3",
            command=lambda: self.switch_mode("mpp"))
        self.window_menu.add_command(
            label="  Stabilized Current",
            accelerator="Alt+F5",
            command=lambda: self.switch_mode("stab"))
        menubar.add_cascade(label="Window", menu=self.window_menu)

        help_menu = Menu(menubar, tearoff=0)
        help_menu.add_command(label="Documentation", command=self._show_help)
        help_menu.add_separator()
        help_menu.add_command(label="About\u2026", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)

    # ── Mode switching ────────────────────────────────────────────────────────

    def switch_mode(self, mode):
        if self.current_mode == mode:
            return
        if self._measurement_running:
            messagebox.showwarning(
                "Measurement in progress",
                "Cannot switch modes while a measurement is running.\n"
                "Stop the current measurement first."
            )
            return

        self.jv_panel.pack_forget()
        self.mpp_panel.pack_forget()
        self.stab_panel.pack_forget()
        self.jv_plot_frame.pack_forget()
        self.mpp_plot_frame.pack_forget()
        self.stab_plot_frame.pack_forget()

        if mode == "jv":
            self.jv_panel.pack(fill=BOTH, expand=True)
            self.jv_plot_frame.pack(fill=BOTH, expand=True)
        elif mode == "mpp":
            self.mpp_panel.pack(fill=BOTH, expand=True)
            self.mpp_plot_frame.pack(fill=BOTH, expand=True)
        elif mode == "stab":
            self.stab_panel.pack(fill=BOTH, expand=True)
            self.stab_plot_frame.pack(fill=BOTH, expand=True)

        self.current_mode = mode
        self._update_window_menu_checkmark(mode)

    def _update_window_menu_checkmark(self, active_mode):
        labels = {
            "jv":   ("\u2713 J-V Measurement", "  MPP Tracking",      "  Stabilized Current"),
            "mpp":  ("  J-V Measurement",      "\u2713 MPP Tracking",  "  Stabilized Current"),
            "stab": ("  J-V Measurement",      "  MPP Tracking",      "\u2713 Stabilized Current"),
        }
        for i, label in enumerate(labels[active_mode]):
            self.window_menu.entryconfig(i, label=label)

    # ── Instrument connection management ──────────────────────────────────────

    def open_instrument_dialog(self):
        dlg = InstrumentDialog(self.root)
        self.root.wait_window(dlg)

        if dlg.selected_resource is None:
            return

        addr = dlg.selected_resource
        idn  = dlg.selected_idn

        self._close_instrument()

        try:
            rm  = pyvisa.ResourceManager()
            res = rm.open_resource(addr)
            res.timeout = 10000
        except Exception as exc:
            messagebox.showerror("Connection Error",
                                 f"Could not open {addr}:\n{exc}")
            return

        self.instrument      = res
        self.instrument_addr = addr
        self.instrument_idn  = idn

        short_idn = idn[:60] + ("\u2026" if len(idn) > 60 else "")
        self.instrument_label_var.set(f"\u2714  Connected: {short_idn}  [{addr}]")
        self.status_var.set(f"Instrument connected: {addr}")
        self.start_btn.config(state=NORMAL)
        self.mpp_start_btn.config(state=NORMAL)
        self.stab_start_btn.config(state=NORMAL)

        self.instrument_menu.entryconfig("Connect\u2026",        state=NORMAL)
        self.instrument_menu.entryconfig("Disconnect",            state=NORMAL)
        self.instrument_menu.entryconfig("Send *IDN? query",      state=NORMAL)

    def disconnect_instrument(self):
        self._close_instrument()
        self.instrument_label_var.set(
            "\u26a0  No instrument connected \u2014 use Instrument \u2192 Connect")
        self.status_var.set("Instrument disconnected.")
        self.start_btn.config(state=DISABLED)
        self.mpp_start_btn.config(state=DISABLED)
        self.stab_start_btn.config(state=DISABLED)
        self.instrument_menu.entryconfig("Disconnect",        state=DISABLED)
        self.instrument_menu.entryconfig("Send *IDN? query",  state=DISABLED)

    def _close_instrument(self):
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
        if self.instrument is None:
            return
        try:
            idn = self.instrument.query("*IDN?").strip()
            messagebox.showinfo("*IDN? Response", idn)
        except Exception as exc:
            messagebox.showerror("Query Error", str(exc))

    # ── File menu actions ─────────────────────────────────────────────────────

    def _export_log(self):
        content = self.log_text.get("1.0", END).strip()
        if not content:
            messagebox.showinfo("Export Log", "The log is empty \u2014 nothing to export.")
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

    def _open_data_viewer(self):
        path = filedialog.askopenfilename(
            title="Open J-V Data File",
            filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")],
        )
        if path:
            DataViewerWindow(self.root, path)

    def _on_exit(self):
        if messagebox.askokcancel("Exit", "Close the application?"):
            self._close_instrument()
            self.root.destroy()

    def _show_help(self):
        import webbrowser, os
        help_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "docs", "help.html"
        )
        abs_path = os.path.abspath(help_path).replace("\\", "/")
        webbrowser.open(f"file:///{abs_path}")

    def _show_about(self):
        from app.utils.constants import APP_NAME as _APP_NAME
        messagebox.showinfo(
            "About",
            f"{_APP_NAME}\n\n"
            "Open-source J-V measurement software for perovskite solar cells.\n\n"
            "Designed for use with the Keithley 2400 SourceMeter.\n\n"
            "Author: Lucas Polimante\n"
            "License: MIT"
        )

    def _clear_session_data(self):
        if not self.session_data:
            messagebox.showinfo("Clear Session", "Session is already empty.")
            return
        n = len(self.session_data)
        if not messagebox.askokcancel(
            "Clear Session Data",
            f"This will remove {n} measurement(s) from the session view.\n"
            "Exported Excel files are not affected.\n\nContinue?"
        ):
            return
        self.session_data.clear()
        self.draw_placeholder_plot()
        self.status_var.set("Session data cleared.")


    # ── GUI helpers ───────────────────────────────────────────────────────────

    def _tooltip(self, field_name):
        return {
            "start_voltage": "Initial measurement voltage (V)",
            "end_voltage":   "Final measurement voltage (V)",
            "step_voltage":  "Voltage step size (positive value)",
            "cell_area":     "Active area of solar cell (cm\u00b2)",
        }.get(field_name, "")

    def _toggle_prebias_entries(self):
        state = NORMAL if self.prebias_enabled_var.get() else DISABLED
        self.prebias_voltage_entry.config(state=state)
        self.prebias_duration_entry.config(state=state)

    def _toggle_light_soaking_entry(self):
        state = NORMAL if self.light_soaking_var.get() else DISABLED
        self.light_soaking_entry.config(state=state)

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

    def update_scan_rate(self, *args):
        try:
            step_mv        = self.step_voltage_var.get() * 1000.0
            delay_ms       = self.delay_ms_var.get()
            nplc_ms        = self.nplc_var.get() * (1000.0 / 60.0)
            time_per_point = (delay_ms + nplc_ms + 20.0) / 1000.0
            if time_per_point <= 0 or step_mv <= 0:
                self.scan_rate_var.set("\u2014")
                return
            self.scan_rate_var.set(f"{step_mv / time_per_point:.1f}")
        except Exception:
            self.scan_rate_var.set("\u2014")

    def validate_inputs(self):
        try:
            if not self.sample_name_var.get():
                raise ValueError("Sample name is required")
            if not self.export_folder_var.get():
                self.export_entry.config(fg="red")
                raise ValueError("Select an export folder")
            if self.instrument is None:
                raise ValueError("No instrument connected.\nUse Instrument \u2192 Connect.")
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
            if self.light_soaking_var.get() and self.light_soaking_duration_var.get() <= 0:
                raise ValueError("Light soaking duration must be positive")
            try:
                illum = float(self.illumination_var.get())
            except ValueError:
                illum = 0.0
            if illum <= 0:
                raise ValueError(
                    "Illumination is 0 mW/cm\u00b2.\n\n"
                    "Enter the detector power in the Illumination Settings panel\n"
                    "so that PCE can be calculated correctly.\n\n"
                    "For a dark-only measurement, use the\n"
                    "\"Include Dark J-V\" checkbox instead."
                )
            return True
        except Exception as exc:
            messagebox.showerror("Input Error", str(exc))
            return False

    def toggle_controls(self, enable=True):
        state = NORMAL if enable else DISABLED
        for w in self.input_widgets:
            w.config(state=state)
        self.start_btn.config(
            state=NORMAL if (enable and self.instrument is not None) else DISABLED)
        self.stop_btn.config(state=NORMAL if not enable else DISABLED)
        if enable:
            self._toggle_prebias_entries()
            self._toggle_light_soaking_entry()

    def draw_placeholder_plot(self):
        self.ax.clear()
        self.ax2.clear()
        self.ax2.yaxis.set_label_position("right")
        self.ax.set_xlim(0, 1.1)
        self.ax.set_ylim(0, 30)
        self.ax2.set_ylim(0, 30)
        self.ax.set_xlabel("Voltage (V)", fontsize=12, labelpad=10)
        self.ax.set_ylabel("Current Density (mA/cm\u00b2)",
                           fontsize=12, labelpad=10, position=(0, 0.5))
        self.ax2.set_ylabel("Power Density (mW/cm\u00b2)",
                            fontsize=12, labelpad=10, position=(1, 0.5))
        self.ax.set_title("J-V Characteristics", fontsize=14,
                          fontweight="bold", pad=20)
        self.ax.text(0.5, 15,
                     'No measurement data\nClick "Start Measurement" to begin',
                     ha="center", va="center", fontsize=12, color="gray")
        self.fig.subplots_adjust(left=0.15, right=0.85, top=0.85)
        self.ax.grid(True)
        self.canvas.draw_idle()

    # ── Measurement flow ──────────────────────────────────────────────────────

    def start_measurement(self):
        if not self.validate_inputs():
            return

        if self.dark_jv_var.get():
            safety_msg = (
                "1. Verify all connections to the SourceMeter\n"
                "2. Confirm the solar simulator shutter is CLOSED\n\n"
                "The software will first measure J-V in the dark, save a\n"
                "separate Excel file, then prompt you to open the shutter\n"
                "before the illuminated measurement.\n\n"
                "Click OK to start."
            )
        else:
            safety_msg = (
                "1. Verify all connections to the SourceMeter\n"
                "2. Confirm solar simulator shutter is open\n\n"
                "Click OK to start measurement."
            )

        if not messagebox.askokcancel("Safety Check", safety_msg, icon="warning"):
            return

        self._measurement_running = True
        self.toggle_controls(False)
        self.status_var.set("Initializing instrument\u2026")
        threading.Thread(target=self.run_measurement, daemon=True).start()

    def stop_measurement(self):
        stop_event.set()
        self.status_var.set("Measurement aborted by user")
        self.toggle_controls(True)

    def add_to_log(self, results):
        self.log_text.config(state=NORMAL)
        if self.log_text.get("1.0", "end-1c"):
            self.log_text.insert(END, "\n" + "\u2500"*60 + "\n", "separator")
        self.log_text.insert(
            END,
            f"Measurement Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
            "header"
        )
        self.log_text.insert(END, results)
        self.log_text.see(END)
        self.log_text.config(state=DISABLED)

    def _ask_open_shutter(self, event):
        """Called on the main thread to show the open-shutter prompt."""
        messagebox.showinfo(
            "Dark J-V Complete \u2014 Open Shutter",
            "Dark J-V measurement complete.\n\n"
            "Please open the solar simulator shutter, then click OK\n"
            "to begin the illuminated J-V measurement."
        )
        event.set()

    def _log_dark_jv(self, v_df, j_df, v_dr, j_dr, filepath):
        """Append a Dark J-V entry to the measurement log."""
        self.log_text.config(state=NORMAL)
        if self.log_text.get("1.0", "end-1c"):
            self.log_text.insert(END, "\n" + "\u2500"*60 + "\n", "separator")
        self.log_text.insert(
            END,
            f"Measurement Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
            "dark_label"
        )
        self.log_text.insert(
            END,
            f"[DARK J-V]\n"
            f"Sample: {self.sample_name_var.get()}\n"
            f"Instrument: {self.instrument_idn or 'unknown'}\n"
            f"Delay/pt: {self.delay_ms_var.get():.0f} ms  |  "
            f"NPLC: {self.nplc_var.get():.2f}  |  "
            f"4-Wire: {'ON' if self.four_wire_var.get() else 'OFF'}\n"
            f"Forward: {len(v_df)} pts  |  Reverse: {len(v_dr)} pts\n"
            f"Saved: {os.path.basename(filepath)}\n"
        )
        self.log_text.see(END)
        self.log_text.config(state=DISABLED)

    def update_results_display(self, fwd, rev, HI, v_f, j_f, v_r, j_r,
                               v_df=None, j_df=None, v_dr=None, j_dr=None):
        Voc_f, Jsc_f, FF_f, PCE_f, Rs_f, Rsh_f = fwd
        Voc_r, Jsc_r, FF_r, PCE_r, Rs_r, Rsh_r = rev

        # ── Accumulate session data ───────────────────────────────────────────
        color = PLOT_COLORS[len(self.session_data) % len(PLOT_COLORS)]
        self.session_data.append({
            "sample_name": self.sample_name_var.get(),
            "timestamp":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "illumination": float(self.illumination_var.get()),
            "v_fwd": v_f, "j_fwd": j_f,
            "v_rev": v_r, "j_rev": j_r,
            "power_fwd": v_f * j_f,
            "power_rev": v_r * j_r,
            "v_dark_fwd": v_df, "j_dark_fwd": j_df,
            "v_dark_rev": v_dr, "j_dark_rev": j_dr,
            "Voc_fwd": Voc_f, "Jsc_fwd": Jsc_f, "FF_fwd": FF_f, "PCE_fwd": PCE_f,
            "Voc_rev": Voc_r, "Jsc_rev": Jsc_r, "FF_rev": FF_r, "PCE_rev": PCE_r,
            "HI": HI,
            "color": color,
        })
        pb = (f"Pre-bias: {self.prebias_voltage_var.get():.2f} V "
              f"for {self.prebias_duration_var.get():.1f} s\n"
              if self.prebias_enabled_var.get() else "")
        ls = (f"Light soaking: {self.light_soaking_duration_var.get():.0f} s\n"
              if self.light_soaking_var.get() else "")
        self.add_to_log(
            f"Sample: {self.sample_name_var.get()}\n"
            f"Instrument: {self.instrument_idn or 'unknown'}\n"
            f"Illumination: {self.illumination_var.get()} mW/cm\u00b2\n"
            f"Delay/pt: {self.delay_ms_var.get():.0f} ms  |  "
            f"NPLC: {self.nplc_var.get():.2f}  |  "
            f"4-Wire: {'ON' if self.four_wire_var.get() else 'OFF'}\n"
            f"{pb}{ls}\n"
            f"Forward Scan:\n"
            f"  Voc:  {Voc_f:.3f} V\n  Jsc:  {Jsc_f:.3f} mA/cm\u00b2\n"
            f"  FF:   {FF_f:.2f} %\n  PCE:  {PCE_f:.2f} %\n"
            f"  Rs:   {Rs_f:.2f} \u03a9\u00b7cm\u00b2\n  Rsh:  {Rsh_f:.2f} \u03a9\u00b7cm\u00b2\n\n"
            f"Reverse Scan:\n"
            f"  Voc:  {Voc_r:.3f} V\n  Jsc:  {Jsc_r:.3f} mA/cm\u00b2\n"
            f"  FF:   {FF_r:.2f} %\n  PCE:  {PCE_r:.2f} %\n"
            f"  Rs:   {Rs_r:.2f} \u03a9\u00b7cm\u00b2\n  Rsh:  {Rsh_r:.2f} \u03a9\u00b7cm\u00b2\n\n"
            f"Hysteresis Index (HI): {HI:.2f} %\n"
        )

    # ── Measurement thread ────────────────────────────────────────────────────

    def run_measurement(self):
        v_df = j_df = v_dr = j_dr = None   # dark J-V data (None if not measured)
        try:
            init_instrument(
                self.instrument,
                self.nplc_var.get(),
                self.delay_ms_var.get() / 1000.0,
                self.four_wire_var.get(),
            )

            # ── Dark J-V (optional) ───────────────────────────────────────
            if self.dark_jv_var.get():
                self.status_var.set("Dark J-V: measuring forward scan\u2026")
                v_df, j_df = measure_scan(
                    self.instrument,
                    self.start_voltage_var.get(),
                    self.end_voltage_var.get(),
                    self.step_voltage_var.get(),
                    self.cell_area_var.get(),
                    stop_event,
                )
                if stop_event.is_set():
                    return

                self.status_var.set("Dark J-V: measuring reverse scan\u2026")
                v_dr, j_dr = measure_scan(
                    self.instrument,
                    self.end_voltage_var.get(),
                    self.start_voltage_var.get(),
                    -self.step_voltage_var.get(),
                    self.cell_area_var.get(),
                    stop_event,
                )
                if stop_event.is_set():
                    return

                # Return to 0 V and turn output OFF before waiting for user
                try:
                    self.instrument.write(":SOUR:VOLT 0")
                    self.instrument.write(":OUTP OFF")
                except Exception:
                    pass

                dark_fp = save_dark_jv_data(
                    self.export_folder_var.get(),
                    self.sample_name_var.get(),
                    v_df, j_df, v_dr, j_dr,
                    self.cell_area_var.get(),
                    self.delay_ms_var.get(),
                    self.nplc_var.get(),
                    self.four_wire_var.get(),
                    self.instrument_idn or "",
                )
                self._log_dark_jv(v_df, j_df, v_dr, j_dr, dark_fp)
                self.status_var.set("Dark J-V complete \u2014 waiting for shutter\u2026")

                # Show open-shutter dialog on the main thread; block here until OK
                shutter_ready = threading.Event()
                self.root.after(0, lambda ev=shutter_ready: self._ask_open_shutter(ev))
                shutter_ready.wait()

                if stop_event.is_set():
                    return

                # Re-initialise for the illuminated run
                init_instrument(
                    self.instrument,
                    self.nplc_var.get(),
                    self.delay_ms_var.get() / 1000.0,
                    self.four_wire_var.get(),
                )

            # ── Light Soaking (optional) ──────────────────────────────────
            if self.light_soaking_var.get():
                apply_light_soaking(
                    self.instrument,
                    self.light_soaking_duration_var.get(),
                    stop_event,
                    self.status_var.set,
                )
                if stop_event.is_set():
                    return

            # ── Illuminated J-V ───────────────────────────────────────────
            self.status_var.set("Measuring forward scan\u2026")
            v_f, j_f = measure_scan(
                self.instrument,
                self.start_voltage_var.get(),
                self.end_voltage_var.get(),
                self.step_voltage_var.get(),
                self.cell_area_var.get(),
                stop_event,
            )
            if stop_event.is_set():
                return

            if self.prebias_enabled_var.get():
                apply_prebias(
                    self.instrument,
                    self.prebias_voltage_var.get(),
                    self.prebias_duration_var.get(),
                    stop_event,
                    self.status_var.set,
                )
                if stop_event.is_set():
                    return

            self.status_var.set("Measuring reverse scan\u2026")
            v_r, j_r = measure_scan(
                self.instrument,
                self.end_voltage_var.get(),
                self.start_voltage_var.get(),
                -self.step_voltage_var.get(),
                self.cell_area_var.get(),
                stop_event,
            )
            if stop_event.is_set():
                return

            illum = float(self.illumination_var.get())

            Voc_f, Jsc_f, FF_f, PCE_f = calculate_jv_parameters(v_f, j_f, illum)
            Voc_r, Jsc_r, FF_r, PCE_r = calculate_jv_parameters(v_r, j_r, illum)
            Rs_f, Rsh_f = calculate_resistances(v_f, j_f, Voc_f)
            Rs_r, Rsh_r = calculate_resistances(v_r, j_r, Voc_r)
            HI = calculate_hysteresis_index(PCE_f, PCE_r)

            fwd = (Voc_f, Jsc_f, FF_f, PCE_f, Rs_f, Rsh_f)
            rev = (Voc_r, Jsc_r, FF_r, PCE_r, Rs_r, Rsh_r)

            self.update_results_display(fwd, rev, HI, v_f, j_f, v_r, j_r,
                                        v_df=v_df, j_df=j_df,
                                        v_dr=v_dr, j_dr=j_dr)
            self.update_plot()
            save_data(
                self.export_folder_var.get(),
                self.sample_name_var.get(),
                v_f, j_f, v_r, j_r, illum,
                Voc_f, Jsc_f, FF_f, PCE_f, Rs_f, Rsh_f,
                Voc_r, Jsc_r, FF_r, PCE_r, Rs_r, Rsh_r, HI,
                self.cell_area_var.get(),
                self.delay_ms_var.get(),
                self.nplc_var.get(),
                self.four_wire_var.get(),
                self.prebias_enabled_var.get(),
                self.prebias_voltage_var.get(),
                self.prebias_duration_var.get(),
                self.instrument_idn or "",
                scan_rate=float(self.scan_rate_var.get()) if self.scan_rate_var.get() != "\u2014" else None,
            )

            self.status_var.set("Measurement complete.")
            play_completion_sound()

        except Exception as exc:
            messagebox.showerror("Measurement Error", str(exc))
            self.status_var.set("Measurement failed.")
            winsound.Beep(500, 500)
        finally:
            try:
                self.instrument.write(":OUTP OFF")
            except Exception:
                pass
            self._measurement_running = False
            self.toggle_controls(True)
            stop_event.clear()

    # ── Plot ──────────────────────────────────────────────────────────────────

    def update_plot(self):
        """
        Redraw the complete session plot from self.session_data.
        Each measurement gets its own color.
        Forward scan: solid line + filled circle markers.
        Reverse scan: dashed line + hollow circle markers.
        Dark J-V (when present): lighter dashed lines, no markers.
        Legend entries are clickable to toggle visibility.
        Hover tooltip shows J and V values at each data point.
        """
        import mplcursors

        self.ax.clear()
        self.ax2.clear()
        self.ax2.yaxis.set_label_position("right")

        if not self.session_data:
            self.draw_placeholder_plot()
            return

        # Check whether any entry has dark data
        has_any_dark = any(e["v_dark_fwd"] is not None for e in self.session_data)
        if has_any_dark:
            self.ax.set_ylim(-20, 30)
            self.ax2.set_ylim(-20, 30)
            self.ax.axhline(0, color="#aaaaaa", linewidth=0.8,
                            linestyle="--", zorder=1)
        else:
            self.ax.set_ylim(0, 30)
            self.ax2.set_ylim(0, 30)

        self.ax.set_xlim(left=0)   # X axis always starts at 0 V

        all_lines   = []   # every line object (for mplcursors + legend map)
        jv_lines    = []   # J-V lines only (hover attached here)
        legend_lines = []  # lines that go into the legend

        for entry in self.session_data:
            color  = entry["color"]
            label  = entry["sample_name"]
            v_f    = entry["v_fwd"]
            j_f    = entry["j_fwd"]
            v_r    = entry["v_rev"]
            j_r    = entry["j_rev"]
            p_f    = entry["power_fwd"]
            p_r    = entry["power_rev"]
            v_df   = entry["v_dark_fwd"]
            j_df   = entry["j_dark_fwd"]
            v_dr   = entry["v_dark_rev"]
            j_dr   = entry["j_dark_rev"]

            # ── Dark curves (plotted first, no markers, reduced opacity) ─────
            if v_df is not None:
                dark_col = color + "66"   # 40% opacity via hex alpha
                ld_f, = self.ax.plot(
                    v_df, j_df,
                    color=color, linestyle="--", linewidth=1.2,
                    alpha=0.4, label=f"{label} \u2014 Dark Fwd",
                    zorder=2,
                )
                ld_r, = self.ax.plot(
                    v_dr, j_dr,
                    color=color, linestyle=":", linewidth=1.2,
                    alpha=0.4, label=f"{label} \u2014 Dark Rev",
                    zorder=2,
                )
                all_lines  += [ld_f, ld_r]
                legend_lines += [ld_f, ld_r]

            # ── Forward J-V: solid line + filled circles ──────────────────────
            line_jf, = self.ax.plot(
                v_f, j_f,
                color=color, linestyle="-",
                marker="o", markersize=5,
                markerfacecolor=color, markeredgecolor=color,
                linewidth=1.8,
                label=f"{label} \u2014 Fwd J-V",
                zorder=3,
            )

            # ── Reverse J-V: dashed line + hollow circles ─────────────────────
            line_jr, = self.ax.plot(
                v_r, j_r,
                color=color, linestyle="--",
                marker="o", markersize=5,
                markerfacecolor="white", markeredgecolor=color,
                markeredgewidth=1.5,
                linewidth=1.8,
                label=f"{label} \u2014 Rev J-V",
                zorder=3,
            )

            # ── Forward Power on ax2 ──────────────────────────────────────────
            line_pf, = self.ax2.plot(
                v_f, p_f,
                color=color, linestyle="-",
                marker="s", markersize=4,
                markerfacecolor=color, markeredgecolor=color,
                linewidth=1.2, alpha=0.5,
                label=f"{label} \u2014 Fwd P",
                zorder=2,
            )

            # ── Reverse Power on ax2 ─────────────────────────────────────────
            line_pr, = self.ax2.plot(
                v_r, p_r,
                color=color, linestyle="--",
                marker="s", markersize=4,
                markerfacecolor="white", markeredgecolor=color,
                markeredgewidth=1.2,
                linewidth=1.2, alpha=0.5,
                label=f"{label} \u2014 Rev P",
                zorder=2,
            )

            all_lines    += [line_jf, line_jr, line_pf, line_pr]
            jv_lines     += [line_jf, line_jr]
            legend_lines += [line_jf, line_jr, line_pf, line_pr]

        # ── Axis labels and title ─────────────────────────────────────────────
        self.ax.set_xlabel("Voltage (V)", labelpad=8)
        self.ax.set_ylabel("Current Density (mA/cm\u00b2)", labelpad=8)
        self.ax2.set_ylabel("Power Density (mW/cm\u00b2)", labelpad=8)

        last  = self.session_data[-1]
        n     = len(self.session_data)
        suffix = f" (+{n - 1} more)" if n > 1 else ""
        self.ax.set_title(
            f"{last['sample_name']}{suffix} \u2014 J-V Characteristics",
            fontsize=12, fontweight="bold", pad=12,
        )

        # ── Combined legend from both axes ────────────────────────────────────
        handles_ax1, labels_ax1 = self.ax.get_legend_handles_labels()
        handles_ax2, labels_ax2 = self.ax2.get_legend_handles_labels()
        legend = self.ax.legend(
            handles_ax1 + handles_ax2,
            labels_ax1  + labels_ax2,
            loc="upper right", frameon=True, framealpha=0.9, fontsize=8,
        )

        # ── Clickable legend: left-click on any legend entry to toggle ────────
        # pick_event is unreliable in the Tkinter backend when mplcursors is
        # active, so we use button_press_event and do the hit-testing manually.
        _all_orig = handles_ax1 + handles_ax2
        _leg_ref  = legend

        def on_legend_click(event):
            if event.button != 1:          # left-click only
                return
            frame_bbox = _leg_ref.get_frame().get_bbox()
            if not frame_bbox.contains(event.x, event.y):
                return                     # click was outside the legend box
            texts    = _leg_ref.get_texts()
            leglines = _leg_ref.get_lines()
            for i, txt in enumerate(texts):
                # hit-test against the text label bounding box
                hit, _ = txt.contains(event)
                # also accept clicks on the short sample line to the left
                if not hit and i < len(leglines):
                    hit, _ = leglines[i].contains(event)
                if hit and i < len(_all_orig):
                    orig    = _all_orig[i]
                    visible = not orig.get_visible()
                    orig.set_visible(visible)
                    if i < len(leglines):
                        leglines[i].set_alpha(1.0 if visible else 0.25)
                    txt.set_alpha(1.0 if visible else 0.35)
                    self.canvas.draw_idle()
                    return

        if self._pick_cid is not None:
            self.fig.canvas.mpl_disconnect(self._pick_cid)
        self._pick_cid = self.fig.canvas.mpl_connect(
            "button_press_event", on_legend_click
        )

        # ── mplcursors: hover tooltip on J-V lines only ───────────────────────
        if self.cursor is not None:
            try:
                self.cursor.remove()
            except Exception:
                pass

        self.cursor = mplcursors.cursor(jv_lines, hover=True, highlight=True)

        @self.cursor.connect("add")
        def on_hover(sel):
            x_val = sel.target[0]
            y_val = sel.target[1]
            lbl   = sel.artist.get_label()
            sel.annotation.set_text(
                f"{lbl}\nV = {x_val:.3f} V\nJ = {y_val:.3f} mA/cm\u00b2"
            )
            sel.annotation.get_bbox_patch().set(
                facecolor="white",
                alpha=0.92,
                edgecolor="#90A4AE",
                linewidth=0.8,
                boxstyle="round,pad=0.4",
            )
            sel.annotation.set_fontsize(9)

        # ── Final layout (unchanged visual style) ─────────────────────────────
        self.fig.subplots_adjust(left=0.15, right=0.85, top=0.85)
        self.ax.grid(True)
        self.canvas.draw_idle()

    # ══════════════════════════════════════════════════════════════════════════
    # MPP TRACKING — panel builder
    # ══════════════════════════════════════════════════════════════════════════

    def _build_mpp_panel(self, left_container, right_container):
        """Build MPP Tracking left panel and right plot area (both hidden initially)."""
        import numpy as np
        from matplotlib.gridspec import GridSpec

        # ── Left panel ────────────────────────────────────────────────────────
        self.mpp_panel = Frame(left_container, bg="#ececec")

        style = ttk.Style()
        style.configure("TNotebook.Tab", padding=[10, 4], font=("Arial", 9))
        nb = ttk.Notebook(self.mpp_panel)
        nb.pack(fill=X, padx=6, pady=(6, 4))

        # ─── Tab 1: Scan ──────────────────────────────────────────────────────
        tab_scan = Frame(nb, padx=6, pady=4, bg="#ececec")
        nb.add(tab_scan, text="  Scan  ")

        # Sample Information
        sf = LabelFrame(tab_scan, text="Sample Information", padx=6, pady=4)
        sf.pack(fill=X, pady=(0, 6))

        Label(sf, text="Sample Name:").grid(row=0, column=0, sticky=W, padx=4, pady=3)
        self.mpp_sample_name_var = StringVar()
        Entry(sf, textvariable=self.mpp_sample_name_var, width=22).grid(
            row=0, column=1, columnspan=2, sticky=W, padx=4, pady=3)

        Label(sf, text="Export Folder:").grid(row=1, column=0, sticky=W, padx=4, pady=3)
        self.mpp_export_folder_var = StringVar()
        mpp_exp_entry = Entry(sf, textvariable=self.mpp_export_folder_var, width=18)
        mpp_exp_entry.grid(row=1, column=1, sticky=W, padx=4, pady=3)
        Button(sf, text="Browse\u2026",
               command=lambda: self._browse_folder_for(self.mpp_export_folder_var),
               font=("Arial", 8), pady=1
               ).grid(row=1, column=2, padx=(2, 4), pady=3)

        # J-V Scan Settings
        pf = LabelFrame(tab_scan, text="J-V Scan Settings", padx=6, pady=4)
        pf.pack(fill=X, pady=(0, 6))

        for row, (lbl, attr, default, unit) in enumerate([
            ("Start Voltage:", "mpp_start_v_var",  -0.1,  "V"),
            ("End Voltage:",   "mpp_end_v_var",     1.05, "V"),
            ("Step Voltage:",  "mpp_step_v_var",    0.025, "V"),
            ("Cell Area:",     "mpp_cell_area_var", 0.07, "cm\u00b2"),
            ("Delay/point:",   "mpp_delay_var",     200.0, "ms"),
            ("NPLC:",          "mpp_nplc_var",      1.0,  "(0.01\u201325)"),
        ]):
            Label(pf, text=lbl).grid(row=row, column=0, sticky=W, padx=4, pady=3)
            var = DoubleVar(value=default)
            setattr(self, attr, var)
            Entry(pf, textvariable=var, width=10).grid(
                row=row, column=1, sticky=W, padx=4, pady=3)
            Label(pf, text=unit, fg="#666666").grid(row=row, column=2, sticky=W)

        srf = LabelFrame(tab_scan, text="Calculated Scan Rate", padx=6, pady=4)
        srf.pack(fill=X, pady=(0, 6))
        self.mpp_scan_rate_var = StringVar(value="\u2014")
        Label(srf, textvariable=self.mpp_scan_rate_var,
              font=("Arial", 11, "bold"), fg="#1565C0").grid(
            row=0, column=0, sticky=W, padx=4, pady=4)
        Label(srf, text="mV/s", fg="#666666").grid(row=0, column=1, sticky=W)

        self.mpp_include_fwd_var = BooleanVar(value=False)
        cb = Checkbutton(tab_scan, text="Include forward scan",
                         variable=self.mpp_include_fwd_var)
        cb.pack(anchor=W, padx=4, pady=2)
        ToolTip(cb,
                "By default, only the reverse scan is used to determine Vmpp.\n"
                "Enable to also measure the forward scan and calculate HI\n"
                "at each cycle. Adds ~1 min per cycle.")

        # Illumination
        ilf = LabelFrame(tab_scan, text="Illumination", padx=6, pady=4)
        ilf.pack(fill=X, pady=(0, 4))
        Label(ilf, text="Detector Power:").grid(row=0, column=0, sticky=W, padx=4, pady=3)
        self.mpp_detector_power_var = DoubleVar(value=0)
        Entry(ilf, textvariable=self.mpp_detector_power_var, width=10).grid(
            row=0, column=1, sticky=W, padx=4, pady=3)
        Label(ilf, text="mW", fg="#666666").grid(row=0, column=2, sticky=W)
        Label(ilf, text="Detector Area:").grid(row=1, column=0, sticky=W, padx=4, pady=3)
        self.mpp_detector_area_var = DoubleVar(value=1.13)
        Entry(ilf, textvariable=self.mpp_detector_area_var, width=10).grid(
            row=1, column=1, sticky=W, padx=4, pady=3)
        Label(ilf, text="cm\u00b2", fg="#666666").grid(row=1, column=2, sticky=W)
        Label(ilf, text="Illumination:").grid(row=2, column=0, sticky=W, padx=4, pady=3)
        self.mpp_illumination_var = StringVar(value="0.00")
        Label(ilf, textvariable=self.mpp_illumination_var,
              font=("Arial", 9, "bold"), fg="#1565C0").grid(
            row=2, column=1, sticky=W, padx=4, pady=3)
        Label(ilf, text="mW/cm\u00b2", fg="#666666").grid(row=2, column=2, sticky=W)

        # ─── Tab 2: MPP Settings ──────────────────────────────────────────────
        tab_mpp = Frame(nb, padx=6, pady=4, bg="#ececec")
        nb.add(tab_mpp, text="  MPP Settings  ")

        # Pre-bias
        pbf = LabelFrame(tab_mpp, text="Pre-bias Conditioning", padx=6, pady=4)
        pbf.pack(fill=X, pady=(0, 6))
        self.mpp_prebias_enabled_var = BooleanVar(value=True)
        self.mpp_prebias_v_entry = None
        self.mpp_prebias_t_entry = None

        def _toggle_mpp_prebias():
            s = NORMAL if self.mpp_prebias_enabled_var.get() else DISABLED
            self.mpp_prebias_v_entry.config(state=s)
            self.mpp_prebias_t_entry.config(state=s)

        Checkbutton(pbf, text="Enable pre-bias",
                    variable=self.mpp_prebias_enabled_var,
                    command=_toggle_mpp_prebias
                    ).grid(row=0, column=0, columnspan=3, sticky=W, padx=4, pady=2)
        Label(pbf, text="Voltage:").grid(row=1, column=0, sticky=W, padx=4, pady=3)
        self.mpp_prebias_v_var = DoubleVar(value=1.2)
        self.mpp_prebias_v_entry = Entry(pbf, textvariable=self.mpp_prebias_v_var, width=10)
        self.mpp_prebias_v_entry.grid(row=1, column=1, sticky=W, padx=4, pady=3)
        Label(pbf, text="V", fg="#666666").grid(row=1, column=2, sticky=W)
        Label(pbf, text="Duration:").grid(row=2, column=0, sticky=W, padx=4, pady=3)
        self.mpp_prebias_t_var = DoubleVar(value=2.0)
        self.mpp_prebias_t_entry = Entry(pbf, textvariable=self.mpp_prebias_t_var, width=10)
        self.mpp_prebias_t_entry.grid(row=2, column=1, sticky=W, padx=4, pady=3)
        Label(pbf, text="s", fg="#666666").grid(row=2, column=2, sticky=W)

        # MPP Tracking Settings
        mf = LabelFrame(tab_mpp, text="MPP Tracking Settings", padx=6, pady=4)
        mf.pack(fill=X, pady=(0, 4))

        for row, (lbl, attr, default, unit, tip) in enumerate([
            ("Total duration:", "mpp_total_min_var",      60.0,  "min",
             "Total duration of the MPP tracking experiment in minutes.\n"
             "Minimum recommended: 5 min for stabilized PCE.\n"
             "For stability studies: 60\u20131000 min."),
            ("SPO duration:",   "mpp_spo_duration_var",  120.0,  "s",
             "Time spent at Vmpp between J-V scans (Stabilized Power Output phase).\n"
             "Literature recommendation: 60\u2013300 s.\n"
             "Longer SPO gives more stable readings but fewer J-V cycles."),
            ("SPO interval:",   "mpp_spo_interval_var",  500.0,  "ms",
             "How often current is read during the SPO phase.\n"
             "500 ms is a good balance of time resolution and noise."),
            ("Stab. threshold:", "mpp_stab_threshold_var", 0.1, "%/min",
             "Current change rate below which the device is considered stabilized.\n"
             "Criterion: |\u0394J/\u0394t| < threshold over a 60 s window.\n"
             "Literature standard: 0.05\u20130.1 %/min (NREL protocol)."),
        ]):
            Label(mf, text=lbl).grid(row=row, column=0, sticky=W, padx=4, pady=3)
            var = DoubleVar(value=default)
            setattr(self, attr, var)
            e = Entry(mf, textvariable=var, width=10)
            e.grid(row=row, column=1, sticky=W, padx=4, pady=3)
            Label(mf, text=unit, fg="#666666").grid(row=row, column=2, sticky=W)
            ToolTip(e, tip)

        # ── Control Buttons ───────────────────────────────────────────────────
        btn_frame = Frame(self.mpp_panel, bg="#ececec")
        btn_frame.pack(fill=X, padx=8, pady=6)

        self.mpp_start_btn = Button(btn_frame, text="Start MPP Tracking",
                                    command=self.start_mpp_tracking,
                                    bg="#388E3C", fg="white",
                                    font=("Arial", 9, "bold"),
                                    width=16, pady=4, state=DISABLED)
        self.mpp_start_btn.pack(side=LEFT, padx=(0, 6))

        self.mpp_stop_btn = Button(btn_frame, text="Stop",
                                   command=self.stop_mpp_tracking,
                                   bg="#C62828", fg="white",
                                   font=("Arial", 9, "bold"),
                                   width=10, pady=4, state=DISABLED)
        self.mpp_stop_btn.pack(side=LEFT)

        # ── Log ───────────────────────────────────────────────────────────────
        log_frame = LabelFrame(self.mpp_panel, text="MPP Log", padx=4, pady=4)
        log_frame.pack(fill=BOTH, expand=True, padx=6, pady=(0, 0))
        hsb = ttk.Scrollbar(log_frame, orient="horizontal")
        vsb = ttk.Scrollbar(log_frame, orient="vertical")
        self.mpp_log_text = Text(log_frame, wrap="none", state=DISABLED,
                                 font=("Consolas", 9),
                                 yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.config(command=self.mpp_log_text.yview)
        hsb.config(command=self.mpp_log_text.xview)
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        self.mpp_log_text.grid(row=0, column=0, sticky="nsew")
        log_frame.grid_rowconfigure(0, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)

        # ── Status Bar ────────────────────────────────────────────────────────
        sb = Frame(self.mpp_panel, bg="#37474F", height=24)
        sb.pack(fill=X, side="bottom")
        sb.pack_propagate(False)
        self.mpp_status_var = StringVar(value="Ready \u2014 connect an instrument to begin.")
        Label(sb, textvariable=self.mpp_status_var,
              bg="#37474F", fg="#CFD8DC",
              font=("Arial", 8), anchor=W
              ).pack(side=LEFT, padx=10, fill=X, expand=True)

        # ── Right plot area ───────────────────────────────────────────────────
        self.mpp_plot_frame = Frame(right_container, bg="#ffffff")

        self.mpp_fig = plt.figure(figsize=(8, 9))
        gs = GridSpec(3, 1, figure=self.mpp_fig,
                      height_ratios=[4, 3, 3], hspace=0.45)
        self.mpp_ax_pce = self.mpp_fig.add_subplot(gs[0])
        self.mpp_ax_j   = self.mpp_fig.add_subplot(gs[1])
        self.mpp_ax_jv  = self.mpp_fig.add_subplot(gs[2])

        for ax, title, xl, yl in [
            (self.mpp_ax_pce, "Power Conversion Efficiency — MPP Tracking",
             "Time (min)", "PCE (%)"),
            (self.mpp_ax_j,   "Current Density at Vmpp (SPO phase)",
             "Time (min)", "Current Density (mA/cm\u00b2)"),
            (self.mpp_ax_jv,  "J-V Curves (reverse scan per cycle)",
             "Voltage (V)", "Current Density (mA/cm\u00b2)"),
        ]:
            ax.set_title(title, fontsize=10, fontweight="bold")
            ax.set_xlabel(xl, fontsize=9)
            ax.set_ylabel(yl, fontsize=9)
            ax.grid(True)
            ax.text(0.5, 0.5, "No data yet", transform=ax.transAxes,
                    ha="center", va="center", color="gray", fontsize=10)

        self.mpp_fig.tight_layout()
        self.mpp_canvas = FigureCanvasTkAgg(self.mpp_fig, master=self.mpp_plot_frame)
        self.mpp_canvas.get_tk_widget().pack(fill=BOTH, expand=True)

        # Storage for live plot data (initialized in _init_mpp_plots)
        self._mpp_pce_times  = []
        self._mpp_pce_values = []
        self._mpp_j_times    = []
        self._mpp_j_values   = []
        self._mpp_jv_cycles  = []

    # ══════════════════════════════════════════════════════════════════════════
    # STABILIZED CURRENT — panel builder
    # ══════════════════════════════════════════════════════════════════════════

    def _build_stab_panel(self, left_container, right_container):
        """Build Stabilized Current left panel and right plot area (hidden initially)."""
        # ── Left panel ────────────────────────────────────────────────────────
        self.stab_panel = Frame(left_container, bg="#ececec")

        nb = ttk.Notebook(self.stab_panel)
        nb.pack(fill=X, padx=6, pady=(6, 4))

        tab = Frame(nb, padx=6, pady=4, bg="#ececec")
        nb.add(tab, text="  Settings  ")

        # Sample Information
        sf = LabelFrame(tab, text="Sample Information", padx=6, pady=4)
        sf.pack(fill=X, pady=(0, 6))
        Label(sf, text="Sample Name:").grid(row=0, column=0, sticky=W, padx=4, pady=3)
        self.stab_sample_name_var = StringVar()
        Entry(sf, textvariable=self.stab_sample_name_var, width=22).grid(
            row=0, column=1, columnspan=2, sticky=W, padx=4, pady=3)
        Label(sf, text="Export Folder:").grid(row=1, column=0, sticky=W, padx=4, pady=3)
        self.stab_export_folder_var = StringVar()
        stab_exp_entry = Entry(sf, textvariable=self.stab_export_folder_var, width=18)
        stab_exp_entry.grid(row=1, column=1, sticky=W, padx=4, pady=3)
        Button(sf, text="Browse\u2026",
               command=lambda: self._browse_folder_for(self.stab_export_folder_var),
               font=("Arial", 8), pady=1
               ).grid(row=1, column=2, padx=(2, 4), pady=3)

        # Measurement Settings
        mf = LabelFrame(tab, text="Measurement Settings", padx=6, pady=4)
        mf.pack(fill=X, pady=(0, 6))
        for row, (lbl, attr, default, unit, tip) in enumerate([
            ("Cell Area:",      "stab_area_var",     0.07, "cm\u00b2",
             "Active area of solar cell (cm\u00b2)"),
            ("Applied Voltage:", "stab_voltage_var", 0.8,  "V",
             "Voltage held constant during the measurement.\n"
             "Typically set to Vmpp from a previous J-V scan.\n"
             "Christians et al. 2015: hold at 0.75\u20130.80 V for 60\u2013120 s."),
            ("Duration:",        "stab_duration_var", 120.0, "s",
             "Total measurement duration in seconds.\n"
             "Minimum recommended: 60 s (NREL protocol).\n"
             "Standard for reporting stabilized PCE: 120\u2013300 s."),
            ("Reading interval:", "stab_interval_var", 500.0, "ms",
             "Time between consecutive current readings."),
            ("NPLC:",             "stab_nplc_var",     1.0,  "(0.01\u201325)",
             "Number of Power Line Cycles per reading."),
        ]):
            Label(mf, text=lbl).grid(row=row, column=0, sticky=W, padx=4, pady=3)
            var = DoubleVar(value=default)
            setattr(self, attr, var)
            e = Entry(mf, textvariable=var, width=10)
            e.grid(row=row, column=1, sticky=W, padx=4, pady=3)
            Label(mf, text=unit, fg="#666666").grid(row=row, column=2, sticky=W)
            ToolTip(e, tip)

        # Illumination
        ilf = LabelFrame(tab, text="Illumination", padx=6, pady=4)
        ilf.pack(fill=X, pady=(0, 6))
        Label(ilf, text="Detector Power:").grid(row=0, column=0, sticky=W, padx=4, pady=3)
        self.stab_detector_power_var = DoubleVar(value=0)
        Entry(ilf, textvariable=self.stab_detector_power_var, width=10).grid(
            row=0, column=1, sticky=W, padx=4, pady=3)
        Label(ilf, text="mW", fg="#666666").grid(row=0, column=2, sticky=W)
        Label(ilf, text="Detector Area:").grid(row=1, column=0, sticky=W, padx=4, pady=3)
        self.stab_detector_area_var = DoubleVar(value=1.13)
        Entry(ilf, textvariable=self.stab_detector_area_var, width=10).grid(
            row=1, column=1, sticky=W, padx=4, pady=3)
        Label(ilf, text="cm\u00b2", fg="#666666").grid(row=1, column=2, sticky=W)
        Label(ilf, text="Illumination:").grid(row=2, column=0, sticky=W, padx=4, pady=3)
        self.stab_illumination_var = StringVar(value="0.00")
        Label(ilf, textvariable=self.stab_illumination_var,
              font=("Arial", 9, "bold"), fg="#1565C0").grid(
            row=2, column=1, sticky=W, padx=4, pady=3)
        Label(ilf, text="mW/cm\u00b2", fg="#666666").grid(row=2, column=2, sticky=W)

        # Stabilization criterion
        cf = LabelFrame(tab, text="Stabilization Criterion", padx=6, pady=4)
        cf.pack(fill=X, pady=(0, 4))
        Label(cf, text="Threshold:").grid(row=0, column=0, sticky=W, padx=4, pady=3)
        self.stab_threshold_var = DoubleVar(value=0.1)
        e = Entry(cf, textvariable=self.stab_threshold_var, width=10)
        e.grid(row=0, column=1, sticky=W, padx=4, pady=3)
        Label(cf, text="%/min", fg="#666666").grid(row=0, column=2, sticky=W)
        ToolTip(e,
                "Current change rate below which the device is considered stabilized.\n"
                "Criterion: |\u0394J/\u0394t| < threshold over a 60 s window.\n"
                "Literature standard: 0.05\u20130.1 %/min (NREL protocol).")

        # ── Control Buttons ───────────────────────────────────────────────────
        btn_frame = Frame(self.stab_panel, bg="#ececec")
        btn_frame.pack(fill=X, padx=8, pady=6)
        self.stab_start_btn = Button(btn_frame, text="Start",
                                     command=self.start_stabilized_current,
                                     bg="#388E3C", fg="white",
                                     font=("Arial", 9, "bold"),
                                     width=16, pady=4, state=DISABLED)
        self.stab_start_btn.pack(side=LEFT, padx=(0, 6))
        self.stab_stop_btn = Button(btn_frame, text="Stop",
                                    command=self.stop_stabilized_current,
                                    bg="#C62828", fg="white",
                                    font=("Arial", 9, "bold"),
                                    width=10, pady=4, state=DISABLED)
        self.stab_stop_btn.pack(side=LEFT)

        # ── Log ───────────────────────────────────────────────────────────────
        log_frame = LabelFrame(self.stab_panel, text="Log", padx=4, pady=4)
        log_frame.pack(fill=BOTH, expand=True, padx=6, pady=(0, 0))
        hsb = ttk.Scrollbar(log_frame, orient="horizontal")
        vsb = ttk.Scrollbar(log_frame, orient="vertical")
        self.stab_log_text = Text(log_frame, wrap="none", state=DISABLED,
                                  font=("Consolas", 9),
                                  yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.config(command=self.stab_log_text.yview)
        hsb.config(command=self.stab_log_text.xview)
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        self.stab_log_text.grid(row=0, column=0, sticky="nsew")
        log_frame.grid_rowconfigure(0, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)

        # ── Status bar ────────────────────────────────────────────────────────
        sb = Frame(self.stab_panel, bg="#37474F", height=24)
        sb.pack(fill=X, side="bottom")
        sb.pack_propagate(False)
        self.stab_status_var = StringVar(value="Ready \u2014 connect an instrument to begin.")
        Label(sb, textvariable=self.stab_status_var,
              bg="#37474F", fg="#CFD8DC",
              font=("Arial", 8), anchor=W
              ).pack(side=LEFT, padx=10, fill=X, expand=True)

        # ── Right plot area ───────────────────────────────────────────────────
        self.stab_plot_frame = Frame(right_container, bg="#ffffff")

        self.stab_fig, self.stab_ax_j = plt.subplots(figsize=(8, 5))
        self.stab_ax_pce = self.stab_ax_j.twinx()
        self.stab_ax_j.set_xlabel("Time (s)", fontsize=11)
        self.stab_ax_j.set_ylabel("Current Density (mA/cm\u00b2)",
                                   fontsize=11, color="#1565C0")
        self.stab_ax_pce.set_ylabel("PCE (%)", fontsize=11, color="#E65100")
        self.stab_ax_j.set_title("Stabilized Current Measurement",
                                  fontsize=13, fontweight="bold")
        self.stab_ax_j.grid(True)
        self.stab_ax_j.text(0.5, 0.5, "No data yet",
                            transform=self.stab_ax_j.transAxes,
                            ha="center", va="center", color="gray", fontsize=12)
        self.stab_fig.tight_layout()
        self.stab_canvas = FigureCanvasTkAgg(self.stab_fig, master=self.stab_plot_frame)
        self.stab_canvas.get_tk_widget().pack(fill=BOTH, expand=True)

    # ══════════════════════════════════════════════════════════════════════════
    # MPP TRACKING — illumination / scan-rate helpers
    # ══════════════════════════════════════════════════════════════════════════

    def update_mpp_illumination(self, *args):
        try:
            self.mpp_illumination_var.set(
                f"{self.mpp_detector_power_var.get() / self.mpp_detector_area_var.get():.2f}")
        except Exception:
            self.mpp_illumination_var.set("Error")

    def update_mpp_scan_rate(self, *args):
        try:
            step_mv = self.mpp_step_v_var.get() * 1000.0
            delay_ms = self.mpp_delay_var.get()
            nplc_ms  = self.mpp_nplc_var.get() * (1000.0 / 60.0)
            t        = (delay_ms + nplc_ms + 20.0) / 1000.0
            self.mpp_scan_rate_var.set(f"{step_mv / t:.1f}" if t > 0 else "\u2014")
        except Exception:
            self.mpp_scan_rate_var.set("\u2014")

    # ══════════════════════════════════════════════════════════════════════════
    # STABILIZED CURRENT — illumination helper
    # ══════════════════════════════════════════════════════════════════════════

    def update_stab_illumination(self, *args):
        try:
            self.stab_illumination_var.set(
                f"{self.stab_detector_power_var.get() / self.stab_detector_area_var.get():.2f}")
        except Exception:
            self.stab_illumination_var.set("Error")

    # ══════════════════════════════════════════════════════════════════════════
    # SHARED HELPER
    # ══════════════════════════════════════════════════════════════════════════

    def _browse_folder_for(self, var):
        folder = filedialog.askdirectory()
        if folder:
            var.set(folder)

    # ══════════════════════════════════════════════════════════════════════════
    # MPP TRACKING — runner and plot-update methods
    # ══════════════════════════════════════════════════════════════════════════

    def validate_mpp_inputs(self):
        try:
            if not self.mpp_sample_name_var.get():
                raise ValueError("Sample name is required")
            if not self.mpp_export_folder_var.get():
                raise ValueError("Select an export folder")
            if self.mpp_total_min_var.get() <= 0:
                raise ValueError("Total duration must be positive")
            if self.mpp_spo_duration_var.get() <= 0:
                raise ValueError("SPO duration must be positive")
            pin = float(self.mpp_illumination_var.get())
            if pin <= 0:
                raise ValueError("Illumination must be > 0 mW/cm\u00b2")
            return True
        except Exception as exc:
            messagebox.showerror("Input Error", str(exc))
            return False

    def start_mpp_tracking(self):
        if not self.validate_mpp_inputs():
            return
        if self.instrument is None:
            messagebox.showerror("No instrument", "Connect an instrument first.")
            return
        if not messagebox.askokcancel(
            "Safety Check",
            "1. Verify all connections to the Keithley SourceMeter\n"
            "2. Confirm solar simulator shutter is open\n"
            "3. Confirm only ONE cell is connected\n\n"
            "Click OK to start MPP Tracking.",
            icon="warning"
        ):
            return

        self._measurement_running = True
        stop_event.clear()
        self.mpp_start_btn.config(state=DISABLED)
        self.mpp_stop_btn.config(state=NORMAL)
        self._init_mpp_plots()

        params = {
            "cell_area":          self.mpp_cell_area_var.get(),
            "start_v":            self.mpp_start_v_var.get(),
            "end_v":              self.mpp_end_v_var.get(),
            "step_v":             self.mpp_step_v_var.get(),
            "delay_ms":           self.mpp_delay_var.get(),
            "nplc":               self.mpp_nplc_var.get(),
            "include_forward":    self.mpp_include_fwd_var.get(),
            "prebias_enabled":    self.mpp_prebias_enabled_var.get(),
            "prebias_v":          self.mpp_prebias_v_var.get(),
            "prebias_t":          self.mpp_prebias_t_var.get(),
            "pin":                float(self.mpp_illumination_var.get()),
            "total_duration_min": self.mpp_total_min_var.get(),
            "spo_duration_s":     self.mpp_spo_duration_var.get(),
            "spo_interval_ms":    self.mpp_spo_interval_var.get(),
            "stab_threshold":     self.mpp_stab_threshold_var.get(),
        }
        callbacks = {
            "on_status":     lambda msg: self.root.after(0, self.mpp_status_var.set, msg),
            "on_log":        lambda msg: self.root.after(0, self._mpp_log, msg),
            "on_jv_update":  lambda data: self.root.after(0, self._mpp_update_jv_plot, data),
            "on_spo_update": lambda t, j, pce, cyc, stab: self.root.after(
                                 0, self._mpp_update_spo_plot, t, j, pce, cyc, stab),
            "on_complete":   lambda *a: self.root.after(0, self._mpp_on_complete, *a),
        }

        from app.measurement.mpp_tracker import MPPTracker
        tracker = MPPTracker(self.instrument, params, callbacks, stop_event)
        threading.Thread(target=tracker.run, daemon=True).start()

    def stop_mpp_tracking(self):
        stop_event.set()
        self.mpp_status_var.set("Stopping\u2026")
        self._measurement_running = False
        self.mpp_start_btn.config(state=NORMAL)
        self.mpp_stop_btn.config(state=DISABLED)

    def _mpp_log(self, msg):
        self.mpp_log_text.config(state=NORMAL)
        self.mpp_log_text.insert(END, msg + "\n")
        self.mpp_log_text.see(END)
        self.mpp_log_text.config(state=DISABLED)

    def _init_mpp_plots(self):
        for ax in [self.mpp_ax_pce, self.mpp_ax_j, self.mpp_ax_jv]:
            ax.clear()

        self.mpp_ax_pce.set_xlabel("Time (min)")
        self.mpp_ax_pce.set_ylabel("PCE (%)")
        self.mpp_ax_pce.set_title("Power Conversion Efficiency \u2014 MPP Tracking",
                                   fontweight="bold")
        self.mpp_ax_pce.grid(True)

        self.mpp_ax_j.set_xlabel("Time (min)")
        self.mpp_ax_j.set_ylabel("Current Density (mA/cm\u00b2)")
        self.mpp_ax_j.set_title("Current Density at Vmpp (SPO phase)")
        self.mpp_ax_j.grid(True)

        self.mpp_ax_jv.set_xlabel("Voltage (V)")
        self.mpp_ax_jv.set_ylabel("Current Density (mA/cm\u00b2)")
        self.mpp_ax_jv.set_title("J-V Curves (reverse scan, per cycle)")
        self.mpp_ax_jv.grid(True)

        self.mpp_fig.tight_layout()
        self.mpp_canvas.draw_idle()

        self._mpp_pce_times     = []
        self._mpp_pce_values    = []
        self._mpp_j_times       = []
        self._mpp_j_values      = []
        self._mpp_jv_cycles     = []
        self._mpp_cycle_times   = []  # minutes — one entry per cycle, for axvline
        self._mpp_last_cycle_n  = None

    def _mpp_update_spo_plot(self, t_abs, j, pce, cycle_n, stabilized):
        t_min = t_abs / 60.0

        # When a new cycle begins (after a J-V scan), insert None to break the
        # matplotlib line. Without this, matplotlib draws a diagonal from the
        # last SPO point of cycle N to the first SPO point of cycle N+1,
        # creating a false "falling" segment that looks like scan data.
        if self._mpp_last_cycle_n is not None and cycle_n != self._mpp_last_cycle_n:
            self._mpp_pce_times.append(None)
            self._mpp_pce_values.append(None)
            self._mpp_j_times.append(None)
            self._mpp_j_values.append(None)
        self._mpp_last_cycle_n = cycle_n

        self._mpp_pce_times.append(t_min)
        self._mpp_pce_values.append(pce)
        self._mpp_j_times.append(t_min)
        self._mpp_j_values.append(j)

        self.mpp_ax_pce.clear()
        self.mpp_ax_pce.plot(self._mpp_pce_times, self._mpp_pce_values,
                             "b-", linewidth=1.2)
        # Thin dashed vertical line at each cycle boundary (when new Vmpp applied)
        for t_cyc in self._mpp_cycle_times:
            self.mpp_ax_pce.axvline(x=t_cyc, color="#9E9E9E", linewidth=0.8,
                                    linestyle="--", alpha=0.7,
                                    label="_nolegend_")
        if stabilized:
            self.mpp_ax_pce.plot(t_min, pce, "go", markersize=5, alpha=0.7)
        self.mpp_ax_pce.set_xlabel("Time (min)")
        self.mpp_ax_pce.set_ylabel("PCE (%)")
        self.mpp_ax_pce.set_title("Power Conversion Efficiency \u2014 MPP Tracking",
                                   fontweight="bold")
        self.mpp_ax_pce.grid(True)

        self.mpp_ax_j.clear()
        self.mpp_ax_j.plot(self._mpp_j_times, self._mpp_j_values,
                           color="#E65100", linewidth=1.2)
        for t_cyc in self._mpp_cycle_times:
            self.mpp_ax_j.axvline(x=t_cyc, color="#9E9E9E", linewidth=0.8,
                                  linestyle="--", alpha=0.7,
                                  label="_nolegend_")
        self.mpp_ax_j.set_xlabel("Time (min)")
        self.mpp_ax_j.set_ylabel("Current Density (mA/cm\u00b2)")
        self.mpp_ax_j.set_title("Current Density at Vmpp (SPO phase)")
        self.mpp_ax_j.grid(True)

        self.mpp_fig.tight_layout()
        self.mpp_canvas.draw_idle()

    def _mpp_update_jv_plot(self, cycle_data):
        self._mpp_jv_cycles.append(cycle_data)

        # Record the time (min) when this cycle's Vmpp was applied so
        # _mpp_update_spo_plot can draw a thin dashed vertical line there.
        self._mpp_cycle_times.append(cycle_data["time_s"] / 60.0)

        # ── J-V curves subplot ────────────────────────────────────────────────
        self.mpp_ax_jv.clear()
        for i, cyc in enumerate(self._mpp_jv_cycles):
            color = PLOT_COLORS[i % len(PLOT_COLORS)]
            label = f"Cycle {cyc['cycle']} \u2014 PCE {cyc['PCE']:.1f}%"
            self.mpp_ax_jv.plot(cyc["v_rev"], cyc["j_rev"],
                                color=color, linewidth=1.5,
                                marker="o", markersize=3,
                                markerfacecolor=color, label=label)
            if cyc.get("v_fwd") is not None:
                self.mpp_ax_jv.plot(cyc["v_fwd"], cyc["j_fwd"],
                                    color=color, linewidth=1.2,
                                    linestyle="--",
                                    marker="o", markersize=3,
                                    markerfacecolor="white",
                                    markeredgecolor=color)

        self.mpp_ax_jv.set_xlabel("Voltage (V)")
        self.mpp_ax_jv.set_ylabel("Current Density (mA/cm\u00b2)")
        self.mpp_ax_jv.set_title("J-V Curves (reverse scan, per cycle)")
        self.mpp_ax_jv.legend(fontsize=7, loc="upper right")
        self.mpp_ax_jv.grid(True)

        self.mpp_fig.tight_layout()
        self.mpp_canvas.draw_idle()

    def _mpp_on_complete(self, cycles, spo_time, spo_j, spo_pce):
        self._measurement_running = False
        self.mpp_start_btn.config(state=NORMAL)
        self.mpp_stop_btn.config(state=DISABLED)
        self.mpp_status_var.set("MPP Tracking complete.")
        if not cycles:
            return
        try:
            import openpyxl
            from openpyxl import Workbook
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            name = self.mpp_sample_name_var.get() or "sample"
            path = os.path.join(self.mpp_export_folder_var.get(),
                                f"{name}_MPP_{ts}.xlsx")
            wb = Workbook()

            # ── Sheet 1: JV_Parameters ────────────────────────────────────
            ws_params = wb.active
            ws_params.title = "JV_Parameters"
            headers = ["Cycle", "Time (min)", "Vmpp (V)", "Jmpp (mA/cm²)",
                       "Voc (V)", "Jsc (mA/cm²)", "FF (%)", "PCE (%)", "HI (%)"]
            ws_params.append(headers)
            for cyc in cycles:
                row = [
                    cyc["cycle"],
                    round(cyc["time_s"] / 60.0, 4),
                    round(cyc["Vmpp"], 4),
                    round(cyc["Jmpp"], 4),
                    round(cyc["Voc"],  4),
                    round(cyc["Jsc"],  4),
                    round(cyc["FF"],   3),
                    round(cyc["PCE"],  3),
                    round(cyc["HI"],   3) if cyc["HI"] is not None else "",
                ]
                ws_params.append(row)

            # ── Sheet 2: SPO_Data ─────────────────────────────────────────
            ws_spo = wb.create_sheet("SPO_Data")
            ws_spo.append(["Time (min)", "J (mA/cm²)", "PCE (%)", "Cycle"])
            for t, j, pce, cyc in zip(
                [x / 60.0 for x in spo_time], spo_j, spo_pce,
                [c for c in (getattr(self, '_mpp_spo_cycle_idx', []) or [])]
            ):
                ws_spo.append([round(t, 4), round(j, 4), round(pce, 4), cyc])

            # ── Sheets: JV raw data per cycle ─────────────────────────────
            for cyc in cycles:
                ws = wb.create_sheet(f"JV_Cycle_{cyc['cycle']}")
                if cyc.get("v_fwd") is not None:
                    ws.append(["Voltage (V)", "J_reverse (mA/cm²)", "J_forward (mA/cm²)"])
                    for v, jr, jf in zip(cyc["v_rev"], cyc["j_rev"], cyc["j_fwd"]):
                        ws.append([round(float(v), 5), round(float(jr), 5), round(float(jf), 5)])
                else:
                    ws.append(["Voltage (V)", "J_reverse (mA/cm²)"])
                    for v, jr in zip(cyc["v_rev"], cyc["j_rev"]):
                        ws.append([round(float(v), 5), round(float(jr), 5)])

            wb.save(path)
            self._mpp_log(f"\nSaved: {os.path.basename(path)}")
            messagebox.showinfo("MPP Tracking Complete",
                                f"Data saved to:\n{path}")
        except Exception as exc:
            messagebox.showerror("Export Error", str(exc))

    # ══════════════════════════════════════════════════════════════════════════
    # STABILIZED CURRENT — runner and plot-update methods
    # ══════════════════════════════════════════════════════════════════════════

    def validate_stab_inputs(self):
        try:
            if not self.stab_sample_name_var.get():
                raise ValueError("Sample name is required")
            if not self.stab_export_folder_var.get():
                raise ValueError("Select an export folder")
            if self.stab_duration_var.get() <= 0:
                raise ValueError("Duration must be positive")
            pin = float(self.stab_illumination_var.get())
            if pin <= 0:
                raise ValueError("Illumination must be > 0 mW/cm\u00b2")
            return True
        except Exception as exc:
            messagebox.showerror("Input Error", str(exc))
            return False

    def start_stabilized_current(self):
        if not self.validate_stab_inputs():
            return
        if self.instrument is None:
            messagebox.showerror("No instrument", "Connect an instrument first.")
            return
        if not messagebox.askokcancel(
            "Safety Check",
            "1. Verify all connections to the Keithley SourceMeter\n"
            "2. Confirm solar simulator shutter is open\n\n"
            "Click OK to start.",
            icon="warning"
        ):
            return

        self._measurement_running = True
        stop_event.clear()
        self.stab_start_btn.config(state=DISABLED)
        self.stab_stop_btn.config(state=NORMAL)

        # Clear plot
        self.stab_ax_j.clear()
        self.stab_ax_pce.clear()
        self.stab_ax_j.set_xlabel("Time (s)", fontsize=11)
        self.stab_ax_j.set_ylabel("Current Density (mA/cm\u00b2)",
                                   fontsize=11, color="#1565C0")
        self.stab_ax_pce.set_ylabel("PCE (%)", fontsize=11, color="#E65100")
        self.stab_ax_j.set_title("Stabilized Current Measurement",
                                  fontsize=13, fontweight="bold")
        self.stab_ax_j.grid(True)
        self.stab_fig.tight_layout()
        self.stab_canvas.draw_idle()

        threading.Thread(target=self.run_stabilized_current, daemon=True).start()

    def stop_stabilized_current(self):
        stop_event.set()
        self.stab_status_var.set("Stopping\u2026")

    def run_stabilized_current(self):
        import numpy as np, time as _time
        try:
            inst = self.instrument
            inst.write("*RST")
            _time.sleep(0.3)
            inst.write(":SOUR:FUNC VOLT")
            inst.write(":SENS:FUNC 'CURR:DC'")
            inst.write(":SENS:CURR:PROT 0.1")
            inst.write(f":SENS:CURR:NPLC {self.stab_nplc_var.get():.3f}")
            inst.write(f":SOUR:VOLT {self.stab_voltage_var.get():.4f}")
            inst.write(":OUTP ON")

            area       = self.stab_area_var.get()
            pin        = float(self.stab_illumination_var.get())
            v_applied  = self.stab_voltage_var.get()
            duration   = self.stab_duration_var.get()
            interval   = self.stab_interval_var.get() / 1000.0
            threshold  = self.stab_threshold_var.get()

            times, j_vals, pce_vals = [], [], []
            j_window, t_window = [], []
            t_start = _time.time()
            stabilized_at = None

            while not stop_event.is_set():
                elapsed = _time.time() - t_start
                if elapsed >= duration:
                    break

                raw = inst.query(":READ?").split(",")
                j   = -(float(raw[1]) * 1000.0) / area
                pce = (v_applied * j) / pin * 100.0 if pin > 0 else 0.0

                times.append(elapsed)
                j_vals.append(j)
                pce_vals.append(pce)

                j_window.append(j)
                t_window.append(elapsed)
                cutoff = elapsed - 60.0
                while t_window and t_window[0] < cutoff:
                    t_window.pop(0)
                    j_window.pop(0)

                stabilized = False
                if stabilized_at is None and len(j_window) >= 2 and \
                        (t_window[-1] - t_window[0]) >= 55.0:
                    j_mean = np.mean(j_window) if np.mean(j_window) != 0 else 1e-9
                    rate   = abs((j_window[-1] - j_window[0]) / j_mean * 100.0
                                 / ((t_window[-1] - t_window[0]) / 60.0))
                    if rate < threshold:
                        stabilized    = True
                        stabilized_at = elapsed

                self.root.after(0, self._stab_update_plot,
                                list(times), list(j_vals), list(pce_vals),
                                stabilized_at)
                self.root.after(0, self.stab_status_var.set,
                                f"t = {elapsed:.0f} s  |  J = {j:.3f} mA/cm\u00b2"
                                f"  |  PCE = {pce:.2f}%")
                _time.sleep(interval)

            self.root.after(0, self._stab_save_data,
                            times, j_vals, pce_vals, v_applied, pin, stabilized_at)
            self.root.after(0, self.stab_status_var.set, "Measurement complete.")

        except Exception as exc:
            self.root.after(0, messagebox.showerror, "Error", str(exc))
        finally:
            try:
                self.instrument.write(":OUTP OFF")
            except Exception:
                pass
            self._measurement_running = False
            stop_event.clear()
            self.root.after(0, self.stab_start_btn.config, {"state": NORMAL})
            self.root.after(0, self.stab_stop_btn.config, {"state": DISABLED})

    def _stab_update_plot(self, times, j_vals, pce_vals, stabilized_at):
        self.stab_ax_j.clear()
        self.stab_ax_pce.clear()

        self.stab_ax_j.plot(times, j_vals, color="#1565C0", linewidth=1.4,
                             label="J (mA/cm\u00b2)")
        self.stab_ax_pce.plot(times, pce_vals, color="#E65100", linewidth=1.4,
                               linestyle="--", label="PCE (%)")

        if stabilized_at is not None:
            self.stab_ax_j.axvline(x=stabilized_at, color="#2E7D32",
                                    linestyle=":", linewidth=1.2)
            self.stab_ax_j.text(
                stabilized_at, max(j_vals) if j_vals else 0,
                f" \u2713 Stabilized\n   t = {stabilized_at:.0f} s",
                color="#2E7D32", fontsize=8, va="top"
            )

        self.stab_ax_j.set_xlabel("Time (s)", fontsize=11)
        self.stab_ax_j.set_ylabel("Current Density (mA/cm\u00b2)",
                                   fontsize=11, color="#1565C0")
        self.stab_ax_pce.set_ylabel("PCE (%)", fontsize=11, color="#E65100")
        self.stab_ax_j.set_title("Stabilized Current Measurement",
                                  fontsize=13, fontweight="bold")
        self.stab_ax_j.grid(True)
        self.stab_fig.tight_layout()
        self.stab_canvas.draw_idle()

    def _stab_save_data(self, times, j_vals, pce_vals, v_applied, pin, stabilized_at):
        try:
            import openpyxl
            from openpyxl import Workbook
            ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
            name = self.stab_sample_name_var.get() or "sample"
            path = os.path.join(self.stab_export_folder_var.get(),
                                f"{name}_StabCurrent_{ts}.xlsx")
            wb = Workbook()
            ws = wb.active
            ws.title = "StabilizedCurrent"

            # Parameters row
            ws.append(["Parameter", "Value"])
            ws.append(["Sample",          name])
            ws.append(["Applied Voltage (V)", v_applied])
            ws.append(["Illumination (mW/cm²)", pin])
            ws.append(["Duration (s)",    self.stab_duration_var.get()])
            ws.append(["Interval (ms)",   self.stab_interval_var.get()])
            ws.append(["Threshold (%/min)", self.stab_threshold_var.get()])
            ws.append(["Stabilized at (s)",
                       round(stabilized_at, 1) if stabilized_at is not None else "N/A"])
            ws.append([])

            # Data
            ws.append(["Time (s)", "J (mA/cm²)", "PCE (%)"])
            for t, j, pce in zip(times, j_vals, pce_vals):
                ws.append([round(t, 3), round(j, 4), round(pce, 4)])

            wb.save(path)
            self._stab_log(f"Saved: {os.path.basename(path)}")
            messagebox.showinfo("Measurement Complete",
                                f"Data saved to:\n{path}")
        except Exception as exc:
            messagebox.showerror("Export Error", str(exc))

    def _stab_log(self, msg):
        self.stab_log_text.config(state=NORMAL)
        self.stab_log_text.insert(END, msg + "\n")
        self.stab_log_text.see(END)
        self.stab_log_text.config(state=DISABLED)
