import os
import json
import time
import threading
import winsound
import tempfile
import webbrowser
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from tkinter import (
    ttk, Menu, filedialog, Label, Entry, Button,
    StringVar, DoubleVar, IntVar, Text, messagebox,
    Frame, LabelFrame, Checkbutton, BooleanVar, Radiobutton,
    Listbox, Scrollbar, Toplevel,
    LEFT, RIGHT, BOTH, X, Y, W, E, END, DISABLED, NORMAL, BOTTOM,
)
from datetime import datetime, timedelta
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
from app.utils.updater import check_for_updates, get_local_version
from app.gui.tooltip import ToolTip
from app.gui.mpp_settings_frame import build_mpp_settings_frame, build_param_grid
from app.gui.instrument_dialog import InstrumentDialog
from app.measurement.analysis import (
    calculate_jv_parameters,
    calculate_resistances,
    calculate_hysteresis_index,
    fit_single_diode,
)
from app.io.exporter import append_single_diode_fit_to_file
from app.instrument.keithley import (
    init_instrument, apply_prebias, apply_light_soaking, measure_scan,
)
from app.io.exporter import (
    save_data, save_dark_jv_data,
    save_stab_current_data, save_pure_mpp_data, save_mpp_data,
)
from app.io.isos_tracking import (
    create_isos_d1_tracking_file,
    create_isos_tracking_file,
    append_isos_d1_measurement,
    read_isos_d1_config,
    read_isos_d1_log,
    read_isos_d1_jv_curves,
    read_isos_config,
    read_isos_log,
    read_isos_jv_curves,
    append_metrics_row,
)
from app.measurement.isos_protocols import list_protocol_labels, get_protocol
from app.io.isos_recovery import (
    add_recent_folder, load_recent_folders, find_orphan_stress_csvs,
    consolidate_orphan, discard_orphan,
)
from app.gui.data_viewer import DataViewerWindow
from app.measurement.light_intensity_jv import LightIntensityJVMeasurement
from app.instrument.keithley_smu import Keithley2400SourceMeter


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
        self.isos_tracking_file   = None

        self.setup_gui()
        # J-V traces
        self.detector_power_var.trace("w", self.update_illumination)
        self.detector_area_var.trace("w",  self.update_illumination)
        self.step_voltage_var.trace("w",   self._on_measurement_params_change)
        self.delay_ms_var.trace("w",       self._on_measurement_params_change)
        self.nplc_var.trace("w",           self._on_measurement_params_change)
        self.target_scan_rate_var.trace("w", self._recalculate_delay_from_scan_rate)
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
        # Pure MPP traces
        self.pmpp_detector_power_var.trace("w", self.update_pmpp_illumination)
        self.pmpp_detector_area_var.trace("w",  self.update_pmpp_illumination)

        # Silent update check — runs 3 s after startup to avoid delaying launch
        self.root.after(3000, lambda: check_for_updates(self.root, silent=True))

        # Crash-recovery scaffolding — look for interrupted ISOS stress runs.
        # The recovery dialog itself lands in a later session; for now we only
        # surface the count in the status bar so unfinished runs are visible.
        self.root.after(1500, self._scan_for_inprogress_isos)

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

        # ── Scan rate input mode toggle ───────────────────────────────────────
        self.scan_rate_mode = StringVar(value="advanced")
        mode_frame = Frame(inf)
        mode_frame.grid(row=0, column=0, columnspan=3, sticky=W, padx=4, pady=(4, 2))
        Radiobutton(mode_frame, text="Advanced (manual delay)",
                    variable=self.scan_rate_mode, value="advanced",
                    command=self._on_scan_mode_change,
                    ).pack(side=LEFT)
        Radiobutton(mode_frame, text="Scan rate (mV/s)",
                    variable=self.scan_rate_mode, value="scan_rate",
                    command=self._on_scan_mode_change,
                    ).pack(side=LEFT, padx=(12, 0))

        Label(inf, text="Delay per point:").grid(
            row=1, column=0, sticky=W, padx=4, pady=4)
        self.delay_ms_var = DoubleVar(value=100.0)
        self.delay_entry = Entry(inf, textvariable=self.delay_ms_var, width=10)
        self.delay_entry.grid(row=1, column=1, sticky=W, padx=4, pady=4)
        Label(inf, text="ms", fg="#666666").grid(row=1, column=2, sticky=W)
        self.input_widgets.append(self.delay_entry)
        ToolTip(self.delay_entry,
                "Wait time (ms) between applying each voltage step and reading current.\n"
                "Perovskites typically need 100\u2013500 ms. Corresponds to :SOUR:DEL.")

        Label(inf, text="NPLC:").grid(
            row=2, column=0, sticky=W, padx=4, pady=4)
        self.nplc_var = DoubleVar(value=1.0)
        nplc_entry = Entry(inf, textvariable=self.nplc_var, width=10)
        nplc_entry.grid(row=2, column=1, sticky=W, padx=4, pady=4)
        Label(inf, text="(0.01\u201325)", fg="#666666").grid(row=2, column=2, sticky=W)
        self.input_widgets.append(nplc_entry)
        ToolTip(nplc_entry,
                "Number of Power Line Cycles per reading (Brazil: 60 Hz \u2192 1 NPLC \u2248 16.7 ms).\n"
                "Higher = less noise, slower. Range: 0.01 \u2013 25.")

        # ── Target scan rate row (visible only in "scan_rate" mode) ──────────
        self.target_scan_rate_var = DoubleVar(value=100.0)
        self._sr_input_label = Label(inf, text="Target scan rate:")
        self._sr_input_entry = Entry(inf, textvariable=self.target_scan_rate_var,
                                     width=10, state=DISABLED)
        self._sr_input_unit  = Label(inf, text="mV/s", fg="#666666")
        # Start hidden — only shown when mode == "scan_rate"
        # (grid_remove preserves grid info so grid() restores position)
        self._sr_input_label.grid(row=3, column=0, sticky=W, padx=4, pady=4)
        self._sr_input_entry.grid(row=3, column=1, sticky=W, padx=4, pady=4)
        self._sr_input_unit.grid( row=3, column=2, sticky=W)
        self._sr_input_label.grid_remove()
        self._sr_input_entry.grid_remove()
        self._sr_input_unit.grid_remove()
        ToolTip(self._sr_input_entry,
                "Target voltage sweep rate in mV/s.\n\n"
                "Delay per point is calculated automatically:\n"
                "  delay = (step_mV / scan_rate \u00d7 1000) \u2212 NPLC \u00d7 16.7 \u2212 50 ms\n\n"
                "The 50 ms accounts for GPIB + Python overhead.\n"
                "If the result is negative (rate too fast for step/NPLC),\n"
                "delay is clamped to 0 ms and the actual rate shown.\n\n"
                "Typical values for perovskites:\n"
                "  10\u201330 mV/s  \u2192 quasi-steady-state (publication standard)\n"
                "  50\u2013100 mV/s \u2192 standard lab measurement\n"
                "  500+ mV/s  \u2192 fast screening only")

        self.four_wire_var = BooleanVar(value=False)
        cb4w = Checkbutton(inf, text="4-Wire (Kelvin) sensing",
                           variable=self.four_wire_var)
        cb4w.grid(row=4, column=0, columnspan=3, sticky=W, padx=4, pady=4)
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
                "Time per point = Delay + NPLC \u00d7 16.7 ms + 50 ms (GPIB overhead)\n\n"
                "For perovskite solar cells, typical values in the literature:\n"
                "  Fast scan:   50\u2013200 mV/s  (maximises hysteresis)\n"
                "  Standard:    10\u2013100 mV/s  (good balance of speed and accuracy)\n"
                "  Slow scan:   1\u201310 mV/s    (approaches quasi-steady-state)\n\n"
                "Always report this value alongside Step Voltage and Delay when publishing.")

        # ── Auto single-diode fit ─────────────────────────────────────────────
        fitf = LabelFrame(tab_inst, text="Analysis", padx=6, pady=4)
        fitf.pack(fill=X, pady=(0, 4))

        self.auto_single_diode_fit_var = BooleanVar(value=False)
        auto_fit_cb = Checkbutton(
            fitf,
            text="Auto single-diode fit",
            variable=self.auto_single_diode_fit_var,
        )
        auto_fit_cb.grid(row=0, column=0, columnspan=3, sticky=W, padx=4, pady=4)
        ToolTip(
            auto_fit_cb,
            "After each J-V measurement, automatically fit the 5-parameter\n"
            "single-diode model to the reverse scan.\n\n"
            "Results are saved in the Excel file (SingleDiodeFit sheet) and\n"
            "stored with the session data for the current session.\n\n"
            "Adds approximately 1–3 seconds per measurement.",
        )

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
        self._build_light_intensity_jv_panel(left, plot_frame)
        self._build_mpp_panel(left, plot_frame)
        self._build_stab_panel(left, plot_frame)
        self._build_pure_mpp_panel(left, plot_frame)
        self._build_curation_panel(left, plot_frame)
        self._build_isos_panel(left, plot_frame)

        # ── Keyboard shortcuts for mode switching ─────────────────────────────
        self.root.bind("<Alt-F2>", lambda e: self.switch_mode("jv"))
        self.root.bind("<Alt-F3>", lambda e: self.switch_mode("light_jv"))
        self.root.bind("<Alt-F5>", lambda e: self.switch_mode("mpp"))
        self.root.bind("<Alt-F6>", lambda e: self.switch_mode("stab"))
        self.root.bind("<Alt-F7>", lambda e: self.switch_mode("pure_mpp"))
        self.root.bind("<Alt-F8>", lambda e: self.switch_mode("curation"))
        self.root.bind("<Alt-F9>", lambda e: self.switch_mode("isos"))

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

        self.procedures_menu = Menu(menubar, tearoff=0)
        self.procedures_menu.add_command(
            label="\u2713 J-V Measurement",
            accelerator="Alt+F2",
            command=lambda: self.switch_mode("jv"))
        self.procedures_menu.add_command(
            label="  Light-Intensity J-V",
            accelerator="Alt+F3",
            command=lambda: self.switch_mode("light_jv"))
        self.procedures_menu.add_command(
            label="  MPP Tracking",
            accelerator="Alt+F5",
            command=lambda: self.switch_mode("mpp"))
        self.procedures_menu.add_command(
            label="  Stabilized Current",
            accelerator="Alt+F6",
            command=lambda: self.switch_mode("stab"))
        self.procedures_menu.add_command(
            label="  Pure MPP Tracking",
            accelerator="Alt+F7",
            command=lambda: self.switch_mode("pure_mpp"))
        self.procedures_menu.add_separator()
        self.procedures_menu.add_command(
            label="  Data Curation",
            accelerator="Alt+F8",
            command=lambda: self.switch_mode("curation"))
        self.procedures_menu.add_separator()
        self.procedures_menu.add_command(
            label="  ISOS Protocols",
            accelerator="Alt+F9",
            command=lambda: self.switch_mode("isos"))
        menubar.add_cascade(label="Procedures", menu=self.procedures_menu)

        help_menu = Menu(menubar, tearoff=0)
        help_menu.add_command(label="Documentation", command=self._show_help)
        help_menu.add_separator()
        help_menu.add_command(
            label="Check for Updates",
            command=lambda: check_for_updates(self.root, silent=False))
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
        self.light_jv_panel.pack_forget()
        self.mpp_panel.pack_forget()
        self.stab_panel.pack_forget()
        self.pmpp_panel.pack_forget()
        self.curation_panel.pack_forget()
        self.isos_panel.pack_forget()
        self.jv_plot_frame.pack_forget()
        self.light_jv_plot_frame.pack_forget()
        self.mpp_plot_frame.pack_forget()
        self.stab_plot_frame.pack_forget()
        self.pmpp_plot_frame.pack_forget()
        self.curation_plot_frame.pack_forget()
        self.isos_plot_frame.pack_forget()

        if mode == "jv":
            self.jv_panel.pack(fill=BOTH, expand=True)
            self.jv_plot_frame.pack(fill=BOTH, expand=True)
        elif mode == "light_jv":
            self.light_jv_panel.pack(fill=BOTH, expand=True)
            self.light_jv_plot_frame.pack(fill=BOTH, expand=True)
        elif mode == "mpp":
            self.mpp_panel.pack(fill=BOTH, expand=True)
            self.mpp_plot_frame.pack(fill=BOTH, expand=True)
        elif mode == "stab":
            self.stab_panel.pack(fill=BOTH, expand=True)
            self.stab_plot_frame.pack(fill=BOTH, expand=True)
        elif mode == "pure_mpp":
            self.pmpp_panel.pack(fill=BOTH, expand=True)
            self.pmpp_plot_frame.pack(fill=BOTH, expand=True)
        elif mode == "curation":
            self.curation_panel.pack(fill=BOTH, expand=True)
            self.curation_plot_frame.pack(fill=BOTH, expand=True)
        elif mode == "isos":
            self.isos_panel.pack(fill=BOTH, expand=True)
            self.isos_plot_frame.pack(fill=BOTH, expand=True)

        self.current_mode = mode
        self._update_window_menu_checkmark(mode)

    def _update_window_menu_checkmark(self, active_mode):
        # Entry indices: 0=J-V, 1=LightJV, 2=MPP, 3=Stab, 4=PureMPP, 5=sep, 6=Curation, 7=sep, 8=ISOS
        all_entries = [
            (0, "  J-V Measurement",     "jv"),
            (1, "  Light-Intensity J-V", "light_jv"),
            (2, "  MPP Tracking",        "mpp"),
            (3, "  Stabilized Current",  "stab"),
            (4, "  Pure MPP Tracking",   "pure_mpp"),
            (6, "  Data Curation",       "curation"),
            (8, "  ISOS Protocols",      "isos"),
        ]
        for menu_i, label, mode_key in all_entries:
            display = ("✓" + label[1:]) if mode_key == active_mode else label
            self.procedures_menu.entryconfig(menu_i, label=display)

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
        self.light_jv_start_btn.config(state=NORMAL)
        self.mpp_start_btn.config(state=NORMAL)
        self.stab_start_btn.config(state=NORMAL)
        self.pmpp_start_btn.config(state=NORMAL)
        self.isos_start_btn.config(state=NORMAL)

        self.instrument_menu.entryconfig("Connect\u2026",        state=NORMAL)
        self.instrument_menu.entryconfig("Disconnect",            state=NORMAL)
        self.instrument_menu.entryconfig("Send *IDN? query",      state=NORMAL)

    def disconnect_instrument(self):
        self._close_instrument()
        self.instrument_label_var.set(
            "\u26a0  No instrument connected \u2014 use Instrument \u2192 Connect")
        self.status_var.set("Instrument disconnected.")
        self.start_btn.config(state=DISABLED)
        self.light_jv_start_btn.config(state=DISABLED)
        self.mpp_start_btn.config(state=DISABLED)
        self.stab_start_btn.config(state=DISABLED)
        self.isos_start_btn.config(state=DISABLED)
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
        version = get_local_version()
        messagebox.showinfo(
            "About JV Measurements",
            f"JV Measurements  v{version}\n\n"
            "Solar cell J-V characterization software\n"
            "for perovskite photovoltaics research.\n\n"
            "Developed at UFABC \u2014 Universidade Federal\n"
            "do ABC, Santo Andr\u00e9, SP, Brazil.\n\n"
            "Repository:\n"
            "github.com/Polimante/jv-measurements\n\n"
            "License: MIT",
            parent=self.root,
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
            time_per_point = (delay_ms + nplc_ms + 50.0) / 1000.0
            if time_per_point <= 0 or step_mv <= 0:
                self.scan_rate_var.set("\u2014")
                return
            self.scan_rate_var.set(f"{step_mv / time_per_point:.1f}")
        except Exception:
            self.scan_rate_var.set("\u2014")

    def _on_measurement_params_change(self, *args):
        """Dispatcher: routes step/delay/NPLC changes to the correct handler."""
        if self.scan_rate_mode.get() == "scan_rate":
            self._recalculate_delay_from_scan_rate()
        else:
            self.update_scan_rate()

    def _on_scan_mode_change(self):
        """Called when the user toggles between Advanced and Scan Rate modes."""
        mode = self.scan_rate_mode.get()
        if mode == "advanced":
            self.delay_entry.config(state=NORMAL)
            self._sr_input_entry.config(state=DISABLED)
            self._sr_input_label.grid_remove()
            self._sr_input_entry.grid_remove()
            self._sr_input_unit.grid_remove()
            self.update_scan_rate()
        else:  # "scan_rate"
            self.delay_entry.config(state="readonly")
            self._sr_input_entry.config(state=NORMAL)
            self._sr_input_label.grid()
            self._sr_input_entry.grid()
            self._sr_input_unit.grid()
            self._recalculate_delay_from_scan_rate()

    def _recalculate_delay_from_scan_rate(self, *args):
        """Calculate delay from target scan rate. Only acts in scan_rate mode."""
        if self.scan_rate_mode.get() != "scan_rate":
            return
        try:
            step_mv          = self.step_voltage_var.get() * 1000.0
            target_rate      = self.target_scan_rate_var.get()
            nplc_ms          = self.nplc_var.get() * (1000.0 / 60.0)
            gpib_overhead_ms = 50.0

            if step_mv <= 0 or target_rate <= 0:
                return

            time_per_point_ms = (step_mv / target_rate) * 1000.0
            delay_ms          = max(0.0, time_per_point_ms - nplc_ms - gpib_overhead_ms)

            self.delay_ms_var.set(round(delay_ms, 1))

            actual_time_ms   = delay_ms + nplc_ms + gpib_overhead_ms
            actual_rate      = step_mv / (actual_time_ms / 1000.0)
            warning          = " \u26a0" if delay_ms == 0.0 else ""
            self.scan_rate_var.set(f"{actual_rate:.1f}{warning}")
        except Exception:
            pass

    def validate_inputs(self):
        from app.gui.input_validation import (
            validate_filename, validate_voltage_range,
            validate_cell_area, validate_delay_ms, validate_nplc,
            validate_illumination,
        )

        def _sv(var, fallback=""):
            """Return the string value of a Tkinter Var; fallback on error."""
            try:
                return str(var.get())
            except Exception:
                return fallback

        try:
            res = validate_filename(self.sample_name_var.get())
            if res.error:
                raise ValueError(res.error.message)
            if not self.export_folder_var.get():
                self.export_entry.config(fg="red")
                raise ValueError("Select an export folder")
            if self.instrument is None:
                raise ValueError("No instrument connected.\nUse Instrument \u2192 Connect.")
            res = validate_voltage_range(
                self.start_voltage_var.get(), self.end_voltage_var.get()
            )
            if res.error:
                raise ValueError(res.error.message)
            if self.step_voltage_var.get() <= 0:
                raise ValueError("Step voltage must be positive")
            res = validate_cell_area(_sv(self.cell_area_var))
            if res.error:
                raise ValueError(res.error.message)
            res = validate_delay_ms(_sv(self.delay_ms_var, "0"))
            if res.error:
                raise ValueError(res.error.message)
            res = validate_nplc(_sv(self.nplc_var))
            if res.error:
                raise ValueError(res.error.message)
            if self.prebias_enabled_var.get() and self.prebias_duration_var.get() <= 0:
                raise ValueError("Pre-bias duration must be positive")
            if self.light_soaking_var.get() and self.light_soaking_duration_var.get() <= 0:
                raise ValueError("Light soaking duration must be positive")
            res = validate_illumination(self.illumination_var.get())
            if res.error:
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
            "single_diode_fit": None,   # populated later if auto-fit is enabled
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

            # \u2500\u2500 Auto single-diode fit (reverse scan) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
            fit_result = None
            if self.auto_single_diode_fit_var.get():
                self.status_var.set("Fitting single-diode model\u2026")
                try:
                    fit_result = fit_single_diode(v_r, j_r)
                    self.session_data[-1]["single_diode_fit"] = fit_result
                except Exception:
                    pass   # fit failure must not abort the measurement

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
                fit_result=fit_result,
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
    # LIGHT-INTENSITY J-V — panel builder
    # ══════════════════════════════════════════════════════════════════════════

    def _build_light_intensity_jv_panel(self, left_container, right_container):
        """Build Light-Intensity J-V left panel and right plot area."""
        # ── Left panel ────────────────────────────────────────────────────────
        self.light_jv_panel = Frame(left_container, bg="#ececec")

        # Title
        Label(self.light_jv_panel,
              text="Light-Intensity Dependent J-V",
              font=("Arial", 10, "bold"), bg="#ececec", fg="#1565C0",
              anchor="w").pack(fill=X, padx=8, pady=(6, 2))

        # ── Settings notebook ─────────────────────────────────────────────────
        nb = ttk.Notebook(self.light_jv_panel)
        nb.pack(fill=X, padx=6, pady=(0, 4))

        # ─── Tab 1: Scan ─────────────────────────────────────────────────────
        tab_scan = Frame(nb, padx=6, pady=4, bg="#ececec")
        nb.add(tab_scan, text="  Scan  ")

        sf = LabelFrame(tab_scan, text="Sample Information", padx=6, pady=4)
        sf.pack(fill=X, pady=(0, 6))
        Label(sf, text="Sample Name:").grid(row=0, column=0, sticky=W, padx=4, pady=3)
        self.lij_sample_name_var = StringVar()
        Entry(sf, textvariable=self.lij_sample_name_var, width=22).grid(
            row=0, column=1, columnspan=2, sticky=W, padx=4, pady=3)
        Label(sf, text="Export Folder:").grid(row=1, column=0, sticky=W, padx=4, pady=3)
        self.lij_export_folder_var = StringVar()
        lij_exp_entry = Entry(sf, textvariable=self.lij_export_folder_var, width=18)
        lij_exp_entry.grid(row=1, column=1, sticky=W, padx=4, pady=3)
        Button(sf, text="Browse…",
               command=lambda: self._browse_folder_for(self.lij_export_folder_var),
               font=("Arial", 8), pady=1
               ).grid(row=1, column=2, padx=(2, 4), pady=3)

        pf = LabelFrame(tab_scan, text="Scan Parameters", padx=6, pady=4)
        pf.pack(fill=X, pady=(0, 6))
        for row, (label, attr, default, unit) in enumerate([
            ("Start Voltage:", "lij_start_v_var",  -0.1,  "V"),
            ("End Voltage:",   "lij_end_v_var",    1.05,  "V"),
            ("Step Voltage:",  "lij_step_v_var",   0.025, "V"),
            ("Cell Area:",     "lij_cell_area_var", 0.07, "cm²"),
        ]):
            Label(pf, text=label).grid(row=row, column=0, sticky=W, padx=4, pady=3)
            var = DoubleVar(value=default)
            setattr(self, attr, var)
            Entry(pf, textvariable=var, width=10).grid(row=row, column=1, sticky=W, padx=4, pady=3)
            Label(pf, text=unit, fg="#666666").grid(row=row, column=2, sticky=W)

        # Illumination reference
        ilf = LabelFrame(tab_scan, text="Illumination Reference", padx=6, pady=4)
        ilf.pack(fill=X, pady=(0, 4))
        Label(ilf, text="1 sun =").grid(row=0, column=0, sticky=W, padx=4, pady=3)
        self.lij_ref_illum_var = DoubleVar(value=100.0)
        Entry(ilf, textvariable=self.lij_ref_illum_var, width=10).grid(
            row=0, column=1, sticky=W, padx=4, pady=3)
        Label(ilf, text="mW/cm²", fg="#666666").grid(row=0, column=2, sticky=W)
        ToolTip(ilf,
                "Reference illumination intensity for PCE calculation.\n"
                "Intensity values entered in the sequence are multiplied by\n"
                "this number to get mW/cm² for each measurement.\n"
                "Standard: 100 mW/cm² per 1 sun (AM1.5G).")

        # ─── Tab 2: Instrument ───────────────────────────────────────────────
        tab_inst = Frame(nb, padx=6, pady=4, bg="#ececec")
        nb.add(tab_inst, text="  Instrument  ")

        inf = LabelFrame(tab_inst, text="Keithley 2400 Settings", padx=6, pady=4)
        inf.pack(fill=X, pady=(0, 6))

        self.lij_scan_rate_mode = StringVar(value="advanced")
        mode_frame = Frame(inf)
        mode_frame.grid(row=0, column=0, columnspan=3, sticky=W, padx=4, pady=(4, 2))
        Radiobutton(mode_frame, text="Advanced (manual delay)",
                    variable=self.lij_scan_rate_mode, value="advanced",
                    command=self._lij_on_scan_mode_change).pack(side=LEFT)
        Radiobutton(mode_frame, text="Scan rate (mV/s)",
                    variable=self.lij_scan_rate_mode, value="scan_rate",
                    command=self._lij_on_scan_mode_change).pack(side=LEFT, padx=(12, 0))

        Label(inf, text="Delay per point:").grid(row=1, column=0, sticky=W, padx=4, pady=4)
        self.lij_delay_ms_var = DoubleVar(value=100.0)
        self.lij_delay_entry = Entry(inf, textvariable=self.lij_delay_ms_var, width=10)
        self.lij_delay_entry.grid(row=1, column=1, sticky=W, padx=4, pady=4)
        Label(inf, text="ms", fg="#666666").grid(row=1, column=2, sticky=W)

        Label(inf, text="NPLC:").grid(row=2, column=0, sticky=W, padx=4, pady=4)
        self.lij_nplc_var = DoubleVar(value=1.0)
        Entry(inf, textvariable=self.lij_nplc_var, width=10).grid(
            row=2, column=1, sticky=W, padx=4, pady=4)
        Label(inf, text="(0.01–25)", fg="#666666").grid(row=2, column=2, sticky=W)

        self.lij_target_sr_var = DoubleVar(value=100.0)
        self._lij_sr_label = Label(inf, text="Target scan rate:")
        self._lij_sr_entry = Entry(inf, textvariable=self.lij_target_sr_var,
                                   width=10, state=DISABLED)
        self._lij_sr_unit  = Label(inf, text="mV/s", fg="#666666")
        self._lij_sr_label.grid(row=3, column=0, sticky=W, padx=4, pady=4)
        self._lij_sr_entry.grid(row=3, column=1, sticky=W, padx=4, pady=4)
        self._lij_sr_unit.grid( row=3, column=2, sticky=W)
        self._lij_sr_label.grid_remove()
        self._lij_sr_entry.grid_remove()
        self._lij_sr_unit.grid_remove()

        srf = LabelFrame(tab_inst, text="Calculated Scan Rate", padx=6, pady=4)
        srf.pack(fill=X, pady=(0, 4))
        self.lij_scan_rate_var = StringVar(value="—")
        Label(srf, textvariable=self.lij_scan_rate_var,
              font=("Arial", 11, "bold"), fg="#1565C0").grid(
            row=0, column=0, sticky=W, padx=4, pady=4)
        Label(srf, text="mV/s", fg="#666666").grid(row=0, column=1, sticky=W)

        self.lij_four_wire_var = BooleanVar(value=False)
        Checkbutton(inf, text="4-Wire (Kelvin) sensing",
                    variable=self.lij_four_wire_var
                    ).grid(row=4, column=0, columnspan=3, sticky=W, padx=4, pady=4)

        # Trace variables for scan rate display
        self.lij_step_v_var.trace("w",   self._lij_params_changed)
        self.lij_delay_ms_var.trace("w",  self._lij_params_changed)
        self.lij_nplc_var.trace("w",      self._lij_params_changed)
        self.lij_target_sr_var.trace("w", self._lij_recalc_delay)
        self._lij_update_scan_rate()

        # ─── Tab 3: Conditioning ─────────────────────────────────────────────
        tab_cond = Frame(nb, padx=6, pady=4, bg="#ececec")
        nb.add(tab_cond, text="  Conditioning  ")

        pbf = LabelFrame(tab_cond, text="Pre-bias Conditioning", padx=6, pady=4)
        pbf.pack(fill=X, pady=(0, 6))
        self.lij_prebias_enabled_var = BooleanVar(value=False)
        Checkbutton(pbf, text="Enable pre-bias before reverse scan",
                    variable=self.lij_prebias_enabled_var,
                    command=self._lij_toggle_prebias).grid(
            row=0, column=0, columnspan=3, sticky=W, padx=4, pady=2)
        Label(pbf, text="Voltage:").grid(row=1, column=0, sticky=W, padx=4, pady=3)
        self.lij_prebias_v_var = DoubleVar(value=1.2)
        self.lij_prebias_v_entry = Entry(pbf, textvariable=self.lij_prebias_v_var, width=10,
                                         state=DISABLED)
        self.lij_prebias_v_entry.grid(row=1, column=1, sticky=W, padx=4, pady=3)
        Label(pbf, text="V", fg="#666666").grid(row=1, column=2, sticky=W)
        Label(pbf, text="Duration:").grid(row=2, column=0, sticky=W, padx=4, pady=3)
        self.lij_prebias_t_var = DoubleVar(value=2.0)
        self.lij_prebias_t_entry = Entry(pbf, textvariable=self.lij_prebias_t_var, width=10,
                                         state=DISABLED)
        self.lij_prebias_t_entry.grid(row=2, column=1, sticky=W, padx=4, pady=3)
        Label(pbf, text="s", fg="#666666").grid(row=2, column=2, sticky=W)

        lsf = LabelFrame(tab_cond, text="Light Soaking", padx=6, pady=4)
        lsf.pack(fill=X, pady=(0, 6))
        self.lij_ls_enabled_var = BooleanVar(value=False)
        Checkbutton(lsf, text="Enable light soaking before each scan",
                    variable=self.lij_ls_enabled_var,
                    command=self._lij_toggle_ls).grid(
            row=0, column=0, columnspan=3, sticky=W, padx=4, pady=2)
        Label(lsf, text="Duration:").grid(row=1, column=0, sticky=W, padx=4, pady=3)
        self.lij_ls_dur_var = DoubleVar(value=60.0)
        self.lij_ls_entry = Entry(lsf, textvariable=self.lij_ls_dur_var, width=10,
                                  state=DISABLED)
        self.lij_ls_entry.grid(row=1, column=1, sticky=W, padx=4, pady=3)
        Label(lsf, text="s", fg="#666666").grid(row=1, column=2, sticky=W)

        fitf = LabelFrame(tab_cond, text="Analysis", padx=6, pady=4)
        fitf.pack(fill=X, pady=(0, 4))
        self.lij_auto_fit_var = BooleanVar(value=True)
        Checkbutton(fitf, text="Auto single-diode fit",
                    variable=self.lij_auto_fit_var
                    ).grid(row=0, column=0, columnspan=3, sticky=W, padx=4, pady=4)

        # ── Intensity Sequence frame ──────────────────────────────────────────
        seq_frame = LabelFrame(self.light_jv_panel, text="Intensity Sequence",
                               padx=6, pady=4)
        seq_frame.pack(fill=X, padx=6, pady=(0, 4))

        # Add intensity row
        add_row = Frame(seq_frame)
        add_row.pack(fill=X, pady=(0, 4))
        Label(add_row, text="Add intensity (suns):").pack(side=LEFT, padx=(0, 4))
        self.lij_add_intensity_var = StringVar()
        add_entry = Entry(add_row, textvariable=self.lij_add_intensity_var, width=10)
        add_entry.pack(side=LEFT)
        add_entry.bind("<Return>", lambda e: self._lij_add_intensity())
        Button(add_row, text="Add", command=self._lij_add_intensity,
               font=("Arial", 8), pady=1).pack(side=LEFT, padx=(4, 0))
        self.lij_add_warn_var = StringVar()
        Label(add_row, textvariable=self.lij_add_warn_var,
              fg="#E65100", font=("Arial", 8)).pack(side=LEFT, padx=(6, 0))

        # Listbox
        lb_frame = Frame(seq_frame)
        lb_frame.pack(fill=X, pady=(0, 4))
        lb_vsb = ttk.Scrollbar(lb_frame, orient="vertical")
        self.lij_listbox = Listbox(lb_frame, height=6, font=("Consolas", 9),
                                   yscrollcommand=lb_vsb.set,
                                   exportselection=False)
        lb_vsb.config(command=self.lij_listbox.yview)
        self.lij_listbox.pack(side=LEFT, fill=X, expand=True)
        lb_vsb.pack(side=LEFT, fill=Y)

        # Listbox button row
        btn_row = Frame(seq_frame)
        btn_row.pack(fill=X, pady=(0, 6))
        for txt, cmd in [
            ("Remove",  self._lij_remove_selected),
            ("Up",      self._lij_move_up),
            ("Down",    self._lij_move_down),
            ("Clear",   self._lij_clear_all),
        ]:
            Button(btn_row, text=txt, command=cmd,
                   font=("Arial", 8), pady=1, width=8).pack(side=LEFT, padx=(0, 3))

        # Stabilization time
        stab_row = Frame(seq_frame)
        stab_row.pack(fill=X)
        Label(stab_row, text="Stabilization time (s):").pack(side=LEFT, padx=(0, 4))
        self.lij_stab_time_var = DoubleVar(value=180.0)
        Entry(stab_row, textvariable=self.lij_stab_time_var, width=8).pack(side=LEFT)

        # ── Control buttons ───────────────────────────────────────────────────
        btn_frame = Frame(self.light_jv_panel, bg="#ececec")
        btn_frame.pack(fill=X, padx=8, pady=6)

        self.light_jv_start_btn = Button(
            btn_frame, text="Start Measurements",
            command=self._lij_start_sequence,
            bg="#1565C0", fg="white",
            font=("Arial", 9, "bold"),
            pady=4, state=DISABLED)
        self.light_jv_start_btn.pack(side=LEFT, padx=(0, 6))

        self.light_jv_stop_btn = Button(
            btn_frame, text="Stop",
            command=self._lij_stop_sequence,
            bg="#C62828", fg="white",
            font=("Arial", 9, "bold"),
            width=8, pady=4)
        self.light_jv_stop_btn.pack(side=LEFT)
        self.light_jv_stop_btn.pack_forget()

        # ── Status bar ────────────────────────────────────────────────────────
        lij_status_bar = Frame(self.light_jv_panel, bg="#37474F", height=24)
        lij_status_bar.pack(fill=X, side="bottom")
        lij_status_bar.pack_propagate(False)
        self.lij_status_var = StringVar(value="Ready — add intensities and press Start Measurements.")
        Label(lij_status_bar, textvariable=self.lij_status_var,
              bg="#37474F", fg="#CFD8DC",
              font=("Arial", 8), anchor=W,
              ).pack(side=LEFT, padx=10, fill=X, expand=True)

        # ── Right plot frame ──────────────────────────────────────────────────
        self.light_jv_plot_frame = Frame(right_container, bg="#ffffff")

        self.lij_fig, (self.lij_ax_jv, self.lij_ax) = plt.subplots(
            2, 1, figsize=(8, 5.5),
            gridspec_kw={"height_ratios": [3, 2], "hspace": 0.55},
        )
        self.lij_ax_jv.set_xlabel("Voltage (V)", fontsize=10)
        self.lij_ax_jv.set_ylabel("Current Density (mA/cm²)", fontsize=10)
        self.lij_ax_jv.set_title("J-V Curves — by intensity (rev scan)",
                                  fontsize=11, fontweight="bold")
        self.lij_ax_jv.text(0.5, 0.5, "No data yet\nAdd intensities and start measurements",
                             ha="center", va="center", fontsize=10, color="gray",
                             transform=self.lij_ax_jv.transAxes)
        self.lij_ax_jv.axhline(0, color="#aaaaaa", linewidth=0.8, linestyle="--")
        self.lij_ax_jv.grid(True, alpha=0.3)
        self.lij_ax.set_xlabel("ln(L / L_max)", fontsize=10)
        self.lij_ax.set_ylabel("Voc (V)", fontsize=10)
        self.lij_ax.set_title("Voc vs ln(L/L_max) — Light Ideality Factor",
                               fontsize=11, fontweight="bold")
        self.lij_ax.text(0.5, 0.5, "Waiting for ≥ 2 measurements…",
                         ha="center", va="center", fontsize=10, color="gray",
                         transform=self.lij_ax.transAxes)
        self.lij_ax.grid(True, alpha=0.3)
        self.lij_canvas = FigureCanvasTkAgg(self.lij_fig, self.light_jv_plot_frame)
        self.lij_canvas.get_tk_widget().pack(fill=BOTH, expand=True)
        self.lij_canvas.draw_idle()

        # Internal state
        self._lij_running = False
        self._lij_results = []

    # ── Light-Intensity JV scan rate helpers ──────────────────────────────────

    def _lij_update_scan_rate(self, *_args):
        try:
            step_mv = self.lij_step_v_var.get() * 1000.0
            delay   = self.lij_delay_ms_var.get()
            nplc_ms = self.lij_nplc_var.get() * (1000.0 / 60.0)
            tpp     = (delay + nplc_ms + 50.0) / 1000.0
            self.lij_scan_rate_var.set(
                f"{step_mv / tpp:.1f}" if tpp > 0 and step_mv > 0 else "—"
            )
        except Exception:
            self.lij_scan_rate_var.set("—")

    def _lij_params_changed(self, *_args):
        if self.lij_scan_rate_mode.get() == "scan_rate":
            self._lij_recalc_delay()
        else:
            self._lij_update_scan_rate()

    def _lij_on_scan_mode_change(self):
        mode = self.lij_scan_rate_mode.get()
        if mode == "advanced":
            self.lij_delay_entry.config(state=NORMAL)
            self._lij_sr_entry.config(state=DISABLED)
            self._lij_sr_label.grid_remove()
            self._lij_sr_entry.grid_remove()
            self._lij_sr_unit.grid_remove()
            self._lij_update_scan_rate()
        else:
            self.lij_delay_entry.config(state="readonly")
            self._lij_sr_entry.config(state=NORMAL)
            self._lij_sr_label.grid()
            self._lij_sr_entry.grid()
            self._lij_sr_unit.grid()
            self._lij_recalc_delay()

    def _lij_recalc_delay(self, *_args):
        if self.lij_scan_rate_mode.get() != "scan_rate":
            return
        try:
            step_mv = self.lij_step_v_var.get() * 1000.0
            rate    = self.lij_target_sr_var.get()
            nplc_ms = self.lij_nplc_var.get() * (1000.0 / 60.0)
            if step_mv <= 0 or rate <= 0:
                return
            tpp_ms  = (step_mv / rate) * 1000.0
            delay   = max(0.0, tpp_ms - nplc_ms - 50.0)
            self.lij_delay_ms_var.set(round(delay, 1))
            actual  = step_mv / ((delay + nplc_ms + 50.0) / 1000.0)
            warning = " ⚠" if delay == 0.0 else ""
            self.lij_scan_rate_var.set(f"{actual:.1f}{warning}")
        except Exception:
            pass

    def _lij_toggle_prebias(self):
        state = NORMAL if self.lij_prebias_enabled_var.get() else DISABLED
        self.lij_prebias_v_entry.config(state=state)
        self.lij_prebias_t_entry.config(state=state)

    def _lij_toggle_ls(self):
        state = NORMAL if self.lij_ls_enabled_var.get() else DISABLED
        self.lij_ls_entry.config(state=state)

    # ── Intensity sequence list management ────────────────────────────────────

    def _lij_add_intensity(self):
        raw = self.lij_add_intensity_var.get().strip()
        self.lij_add_warn_var.set("")
        try:
            val = float(raw)
        except ValueError:
            self.lij_add_warn_var.set("Not a number")
            return
        if val <= 0 or val > 10.0:
            self.lij_add_warn_var.set("Must be 0.001–10.0")
            return
        # Warn on duplicate but still add
        existing = [self.lij_listbox.get(i) for i in range(self.lij_listbox.size())]
        formatted = f"[ ] {val:.4g}"
        if any(f"[ ] {val:.4g}" in e or f"[✔] {val:.4g}" in e for e in existing):
            self.lij_add_warn_var.set("Duplicate value added")
        self.lij_listbox.insert(END, formatted)
        self.lij_add_intensity_var.set("")

    def _lij_remove_selected(self):
        sel = self.lij_listbox.curselection()
        for idx in reversed(sel):
            self.lij_listbox.delete(idx)

    def _lij_move_up(self):
        sel = self.lij_listbox.curselection()
        if not sel or sel[0] == 0:
            return
        idx = sel[0]
        text = self.lij_listbox.get(idx)
        fg   = self.lij_listbox.itemcget(idx, "foreground")
        self.lij_listbox.delete(idx)
        self.lij_listbox.insert(idx - 1, text)
        if fg:
            self.lij_listbox.itemconfig(idx - 1, foreground=fg)
        self.lij_listbox.selection_set(idx - 1)

    def _lij_move_down(self):
        sel = self.lij_listbox.curselection()
        if not sel or sel[0] >= self.lij_listbox.size() - 1:
            return
        idx = sel[0]
        text = self.lij_listbox.get(idx)
        fg   = self.lij_listbox.itemcget(idx, "foreground")
        self.lij_listbox.delete(idx)
        self.lij_listbox.insert(idx + 1, text)
        if fg:
            self.lij_listbox.itemconfig(idx + 1, foreground=fg)
        self.lij_listbox.selection_set(idx + 1)

    def _lij_clear_all(self):
        self.lij_listbox.delete(0, END)

    def _lij_get_intensities(self):
        """Return list of float intensities from the listbox."""
        result = []
        for i in range(self.lij_listbox.size()):
            text = self.lij_listbox.get(i)
            # text is like "[ ] 1.00" or "[✔] 0.50" — value is the last token
            try:
                result.append(float(text.split()[-1]))
            except (ValueError, IndexError):
                pass
        return result

    # ── Start/Stop logic ─────────────────────────────────────────────────────

    def _lij_start_sequence(self):
        if not self.lij_sample_name_var.get():
            messagebox.showerror("Input Error", "Sample name is required.")
            return
        if not self.lij_export_folder_var.get():
            messagebox.showerror("Input Error", "Export folder is required.")
            return
        if self.instrument is None:
            messagebox.showerror("Input Error", "No instrument connected.")
            return
        intensities = self._lij_get_intensities()
        if not intensities:
            messagebox.showerror("Input Error",
                                 "Add at least one intensity to the sequence.")
            return
        try:
            stab = self.lij_stab_time_var.get()
            if stab <= 0:
                raise ValueError("Stabilization time must be positive.")
            ref_illum = self.lij_ref_illum_var.get()
            if ref_illum <= 0:
                raise ValueError("Reference illumination must be positive.")
        except Exception as exc:
            messagebox.showerror("Input Error", str(exc))
            return

        self._measurement_running = True
        self._lij_running         = True
        self._lij_results         = []
        self.light_jv_start_btn.config(state=DISABLED)
        self.light_jv_stop_btn.pack(side=LEFT)
        # Reset listbox status markers to pending
        for i in range(self.lij_listbox.size()):
            text = self.lij_listbox.get(i)
            val_str = text.split()[-1]
            self.lij_listbox.delete(i)
            self.lij_listbox.insert(i, f"[ ] {val_str}")
            self.lij_listbox.itemconfig(i, foreground="")
        stop_event.clear()
        threading.Thread(target=self._lij_run_thread,
                         args=(intensities,), daemon=True).start()

    def _lij_stop_sequence(self):
        stop_event.set()
        self.lij_status_var.set("Stopping…")

    def _lij_run_thread(self, intensities):
        from pathlib import Path
        sp = {
            "start_v":  self.lij_start_v_var.get(),
            "end_v":    self.lij_end_v_var.get(),
            "step_v":   self.lij_step_v_var.get(),
            "delay_ms": self.lij_delay_ms_var.get(),
            "nplc":     self.lij_nplc_var.get(),
            "cell_area":self.lij_cell_area_var.get(),
            "four_wire":self.lij_four_wire_var.get(),
            "reference_illumination_mw_cm2": self.lij_ref_illum_var.get(),
        }
        prebias = {
            "enabled":  self.lij_prebias_enabled_var.get(),
            "voltage":  self.lij_prebias_v_var.get(),
            "duration": self.lij_prebias_t_var.get(),
        }
        ls = {
            "enabled":  self.lij_ls_enabled_var.get(),
            "duration": self.lij_ls_dur_var.get(),
        }
        smu = Keithley2400SourceMeter(self.instrument)

        measurement = LightIntensityJVMeasurement(
            sourcemeter           = smu,
            intensities           = intensities,
            scan_params           = sp,
            stabilization_time_s  = self.lij_stab_time_var.get(),
            pre_bias_settings     = prebias,
            light_soaking_settings= ls,
            sample_name           = self.lij_sample_name_var.get(),
            output_folder         = Path(self.lij_export_folder_var.get()),
            auto_single_diode_fit = self.lij_auto_fit_var.get(),
            on_status             = self.lij_status_var.set,
            on_prompt_user        = self._lij_prompt_user,
            on_progress           = lambda cur, tot, status:
                                    self.lij_status_var.set(
                                        f"Step {cur}/{tot} — {status}"),
            on_measurement_done   = self._lij_on_step_done,
            on_complete           = self._lij_on_complete,
            stop_event            = stop_event,
            instrument_idn        = self.instrument_idn or "",
        )
        try:
            measurement.run()
        except Exception as exc:
            messagebox.showerror("Measurement Error", str(exc))
            self.lij_status_var.set("Sequence failed.")
        finally:
            self._measurement_running = False
            self._lij_running         = False
            self.root.after(0, self._lij_restore_controls)
            stop_event.clear()

    def _lij_restore_controls(self):
        if self.instrument is not None:
            self.light_jv_start_btn.config(state=NORMAL)
        self.light_jv_stop_btn.pack_forget()

    def _lij_on_step_done(self, index, result):
        """Called from measurement thread — schedules GUI update on main thread."""
        self.root.after(0, lambda: self._lij_update_listbox(index, result))
        self.root.after(0, lambda: self._lij_update_plot())

    def _lij_update_listbox(self, index, result):
        if index >= self.lij_listbox.size():
            return
        intensity = result.intensity
        if result.status == "completed":
            marker = "✔"
            color  = "green"
        elif result.status == "failed":
            marker = "✗"
            color  = "red"
        else:
            marker = "✗"
            color  = "#E65100"
        text = f"[{marker}] {intensity:.4g}"
        self.lij_listbox.delete(index)
        self.lij_listbox.insert(index, text)
        self.lij_listbox.itemconfig(index, foreground=color)
        self._lij_results = list(self._lij_results)
        if len(self._lij_results) <= index:
            self._lij_results.extend([None] * (index + 1 - len(self._lij_results)))
        self._lij_results[index] = result

    def _lij_update_plot(self):
        """Redraw JV curves (top) and Voc vs ln(L) (bottom) with completed results."""
        import math as _math
        import numpy as np
        from scipy.stats import linregress

        completed = [r for r in self._lij_results
                     if r is not None and r.status == "completed"]

        # ── Top: J-V curves ───────────────────────────────────────────────────
        self.lij_ax_jv.clear()
        self.lij_ax_jv.set_xlabel("Voltage (V)", fontsize=10)
        self.lij_ax_jv.set_ylabel("Current Density (mA/cm²)", fontsize=10)
        self.lij_ax_jv.set_title("J-V Curves — by intensity (rev scan)",
                                  fontsize=11, fontweight="bold")
        self.lij_ax_jv.axhline(0, color="#aaaaaa", linewidth=0.8, linestyle="--", zorder=1)
        self.lij_ax_jv.grid(True, alpha=0.3)

        if completed:
            for i, r in enumerate(completed):
                color = PLOT_COLORS[i % len(PLOT_COLORS)]
                v = r.v_rev if r.v_rev is not None else r.v_fwd
                j = r.j_rev if r.j_rev is not None else r.j_fwd
                if v is not None and j is not None and len(v) > 0:
                    self.lij_ax_jv.plot(v, j, color=color, linewidth=1.5,
                                        label=f"{r.intensity:.4g} sun", zorder=3)
            self.lij_ax_jv.legend(fontsize=8, loc="upper right")
        else:
            self.lij_ax_jv.text(0.5, 0.5, "No data yet",
                                 ha="center", va="center", fontsize=10,
                                 color="gray", transform=self.lij_ax_jv.transAxes)

        # ── Bottom: Voc vs ln(L/Lmax) ─────────────────────────────────────────
        self.lij_ax.clear()
        self.lij_ax.set_xlabel("ln(L / L_max)", fontsize=10)
        self.lij_ax.set_ylabel("Voc (V)", fontsize=10)
        self.lij_ax.set_title("Voc vs ln(L/L_max) — Light Ideality Factor",
                               fontsize=11, fontweight="bold")
        self.lij_ax.grid(True, alpha=0.3)

        if len(completed) >= 2:
            intensities = [r.intensity for r in completed]
            l_max  = max(intensities)
            ln_l   = np.array([_math.log(i / l_max) if i > 0 else float("nan")
                                for i in intensities])
            voc_f  = np.array([r.Voc_fwd for r in completed])
            voc_r  = np.array([r.Voc_rev for r in completed])
            valid  = np.isfinite(ln_l)
            self.lij_ax.plot(ln_l[valid], voc_f[valid], "o", color="#1565C0",
                             markersize=5, label="Fwd Voc", zorder=3)
            self.lij_ax.plot(ln_l[valid], voc_r[valid], "s", color="#B71C1C",
                             markersize=5, label="Rev Voc", zorder=3)
            if valid.sum() >= 3:
                vt = 1.380649e-23 * 298.15 / 1.602176634e-19
                x_line = np.linspace(ln_l[valid].min(), ln_l[valid].max(), 50)
                res_f = linregress(ln_l[valid], voc_f[valid])
                res_r = linregress(ln_l[valid], voc_r[valid])
                n_f = res_f.slope / vt
                n_r = res_r.slope / vt
                self.lij_ax.plot(x_line, res_f.intercept + res_f.slope * x_line,
                                 "--", color="#1565C0", alpha=0.75,
                                 label=f"Fwd fit  n_idL = {n_f:.2f}")
                self.lij_ax.plot(x_line, res_r.intercept + res_r.slope * x_line,
                                 "--", color="#B71C1C", alpha=0.75,
                                 label=f"Rev fit  n_idL = {n_r:.2f}")
            self.lij_ax.legend(fontsize=8, loc="upper left")
        else:
            self.lij_ax.text(0.5, 0.5, "Waiting for ≥ 2 measurements…",
                              ha="center", va="center", fontsize=10,
                              color="gray", transform=self.lij_ax.transAxes)

        self.lij_canvas.draw_idle()

    def _lij_on_complete(self, consolidated_path):
        self.root.after(0, lambda: self._lij_sequence_complete(consolidated_path))

    def _lij_sequence_complete(self, path):
        self.lij_status_var.set(
            f"Sequence complete — saved: {path.name}")
        from app.utils.sound import play_completion_sound
        play_completion_sound()

    # ── Illumination prompt modal ─────────────────────────────────────────────

    def _lij_prompt_user(self, message: str, intensity: float) -> bool:
        """Block the measurement thread until the user confirms or cancels.

        Opens a modal Toplevel on the main thread via root.after() + Event.wait().
        Returns True if the user clicked OK (checkbox ticked), False otherwise.
        """
        result = [False]
        ready  = threading.Event()

        def _show():
            from tkinter import Toplevel, BooleanVar, Checkbutton, WORD
            dlg = Toplevel(self.root)
            dlg.title("Set Illumination Intensity")
            dlg.resizable(False, False)
            dlg.grab_set()

            # Prevent window-close button and Esc from bypassing
            dlg.protocol("WM_DELETE_WINDOW", lambda: None)
            dlg.bind("<Escape>", lambda e: None)

            # Message
            msg_frame = Frame(dlg, padx=16, pady=12)
            msg_frame.pack(fill=X)
            from tkinter import Text as _Text
            msg_widget = _Text(msg_frame, wrap=WORD, relief="flat",
                               bg=dlg.cget("bg"), font=("Arial", 10),
                               height=5, width=50)
            msg_widget.insert("1.0", message)
            msg_widget.config(state=DISABLED)
            msg_widget.pack()

            # Checkbox
            ck_var = BooleanVar(value=False)
            ck_frame = Frame(dlg, padx=16, pady=4)
            ck_frame.pack(fill=X)
            ck = Checkbutton(
                ck_frame,
                text=f"I confirm the simulator is set to {intensity:.4g} sun",
                variable=ck_var,
                font=("Arial", 10),
            )
            ck.pack(anchor=W)

            # Buttons
            btn_frame = Frame(dlg, padx=16, pady=12)
            btn_frame.pack(fill=X)

            ok_btn = Button(btn_frame, text="OK", width=10,
                            bg="#1565C0", fg="white",
                            font=("Arial", 9, "bold"),
                            state=DISABLED,
                            command=lambda: _confirm(True))
            ok_btn.pack(side=RIGHT, padx=(6, 0))
            Button(btn_frame, text="Cancel", width=10,
                   font=("Arial", 9),
                   command=lambda: _confirm(False)).pack(side=RIGHT)

            def _on_ck_change(*_):
                ok_btn.config(state=NORMAL if ck_var.get() else DISABLED)
            ck_var.trace("w", _on_ck_change)

            def _confirm(ok: bool):
                result[0] = ok
                dlg.grab_release()
                dlg.destroy()
                ready.set()

            # Centre over main window
            self.root.update_idletasks()
            x = self.root.winfo_x() + (self.root.winfo_width()  - 450) // 2
            y = self.root.winfo_y() + (self.root.winfo_height() - 260) // 2
            dlg.geometry(f"450x260+{x}+{y}")

        self.root.after(0, _show)
        ready.wait()
        return result[0]

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
        pf, _ = build_mpp_settings_frame(tab_scan, "mpp", [
            ("Start Voltage:", "start_v",  -0.1,  "V"),
            ("End Voltage:",   "end_v",     1.05, "V"),
            ("Step Voltage:",  "step_v",    0.025, "V"),
            ("Cell Area:",     "cell_area", 0.07, "cm\u00b2"),
            ("Delay/point:",   "delay",     200.0, "ms"),
            ("NPLC:",          "nplc",      1.0,  "(0.01\u201325)"),
        ], self, title="J-V Scan Settings")
        pf.pack(fill=X, pady=(0, 6))

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
        mf, _ = build_mpp_settings_frame(tab_mpp, "mpp", [
            ("Total duration:", "total_min",      60.0,  "min",
             "Total duration of the MPP tracking experiment in minutes.\n"
             "Minimum recommended: 5 min for stabilized PCE.\n"
             "For stability studies: 60\u20131000 min."),
            ("SPO duration:",   "spo_duration",  120.0,  "s",
             "Time spent at Vmpp between J-V scans (Stabilized Power Output phase).\n"
             "Literature recommendation: 60\u2013300 s.\n"
             "Longer SPO gives more stable readings but fewer J-V cycles."),
            ("SPO interval:",   "spo_interval",  500.0,  "ms",
             "How often current is read during the SPO phase.\n"
             "500 ms is a good balance of time resolution and noise."),
            ("Stab. threshold:", "stab_threshold", 0.1, "%/min",
             "Current change rate below which the device is considered stabilized.\n"
             "Criterion: |\u0394J/\u0394t| < threshold over a 60 s window.\n"
             "Literature standard: 0.05\u20130.1 %/min (NREL protocol)."),
        ], self, title="MPP Tracking Settings")
        mf.pack(fill=X, pady=(0, 4))

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

        self.mpp_fig = plt.figure(figsize=(8, 9), constrained_layout=True)
        gs = GridSpec(3, 1, figure=self.mpp_fig, height_ratios=[4, 3, 3])
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
    # ════════════════════════════════════════════════════════════════════════════
    # DATA CURATION — panel builder, helpers, runner
    # ════════════════════════════════════════════════════════════════════════════

    def _build_curation_panel(self, left_container, right_container):
        """Build Data Curation left panel and right preview area."""
        self.curation_files  = []
        self.curation_groups = []

        self.curation_panel = Frame(left_container, bg="#ececec")

        nb = ttk.Notebook(self.curation_panel)
        nb.pack(fill=BOTH, expand=True, padx=6, pady=(6, 4))

        # ── Tab 1: Files ───────────────────────────────────────────────
        tab_files = Frame(nb, padx=6, pady=4, bg="#ececec")
        nb.add(tab_files, text="  Files  ")

        ff = LabelFrame(tab_files, text="Input Files", padx=6, pady=4)
        ff.pack(fill=BOTH, expand=True, pady=(0, 6))

        btn_row = Frame(ff)
        btn_row.pack(fill=X, pady=(0, 4))
        Button(btn_row, text="Add Files", font=("Arial", 8),
               command=self._curation_add_files).pack(side=LEFT, padx=2)
        Button(btn_row, text="Add Folder", font=("Arial", 8),
               command=self._curation_add_folder).pack(side=LEFT, padx=2)
        Button(btn_row, text="Clear All", font=("Arial", 8),
               command=self._curation_clear_files).pack(side=LEFT, padx=2)

        list_frame = Frame(ff)
        list_frame.pack(fill=BOTH, expand=True)
        sb_y = Scrollbar(list_frame, orient="vertical")
        sb_y.pack(side=RIGHT, fill=Y)
        self.curation_listbox = Listbox(
            list_frame, yscrollcommand=sb_y.set,
            height=12, selectmode="extended",
            font=("Arial", 8))
        self.curation_listbox.pack(side=LEFT, fill=BOTH, expand=True)
        sb_y.config(command=self.curation_listbox.yview)
        self.curation_listbox.bind("<<ListboxSelect>>", self._on_curation_file_select)

        bottom_row = Frame(ff)
        bottom_row.pack(fill=X, pady=(4, 0))
        Button(bottom_row, text="Remove Selected", font=("Arial", 8),
               command=self._curation_remove_selected).pack(side=LEFT, padx=2)
        self.curation_file_count_var = StringVar(value="0 files")
        Label(bottom_row, textvariable=self.curation_file_count_var,
              font=("Arial", 8), fg="#555555").pack(side=RIGHT, padx=4)

        sf = LabelFrame(tab_files, text="Grouping by Prefix", padx=6, pady=4)
        sf.pack(fill=X, pady=(0, 4))

        tip = (
            "Files are grouped by the first letter+digit prefix in the filename.\n\n"
            "Recommended naming:\n"
            "  D1C1_<timestamp>.xlsx  →  group D1\n"
            "  D1C2_<timestamp>.xlsx  →  group D1\n"
            "  D2C1_<timestamp>.xlsx  →  group D2\n\n"
            "The prefix is detected automatically — no underscores required.\n"
            "'D1C1' and 'D1_C1' both yield prefix 'D1'."
        )
        lbl = Label(sf, text="\u24d8  Name files as D1C1_..., D1C2_..., D2C1_...\n"
                             "   to group cells by device prefix automatically.",
                    anchor=W, justify=LEFT, fg="#37474F", font=("Arial", 8))
        lbl.pack(fill=X, pady=(0, 4))
        ToolTip(lbl, tip)

        # ── Tab 2: Groups ────────────────────────────────────────────────
        tab_groups = Frame(nb, padx=6, pady=4, bg="#ececec")
        nb.add(tab_groups, text="  Groups  ")

        gf = LabelFrame(tab_groups, text="Composition Groups", padx=6, pady=4)
        gf.pack(fill=BOTH, expand=True, pady=(0, 4))

        grp_tree_frame = Frame(gf)
        grp_tree_frame.pack(fill=BOTH, expand=True)
        sb_grp = Scrollbar(grp_tree_frame, orient="vertical")
        sb_grp.pack(side=RIGHT, fill=Y)
        self.groups_tree = ttk.Treeview(
            grp_tree_frame, columns=("name", "prefixes"),
            show="headings", height=6, yscrollcommand=sb_grp.set)
        self.groups_tree.heading("name",     text="Group Name")
        self.groups_tree.heading("prefixes", text="Prefixes (comma-sep.)")
        self.groups_tree.column("name",     width=90,  minwidth=60)
        self.groups_tree.column("prefixes", width=140, minwidth=80)
        self.groups_tree.pack(side=LEFT, fill=BOTH, expand=True)
        sb_grp.config(command=self.groups_tree.yview)

        add_row = Frame(gf, bg="#ececec")
        add_row.pack(fill=X, pady=(6, 2))
        Label(add_row, text="Name:",     font=("Arial", 8), bg="#ececec").grid(row=0, column=0, sticky=W, padx=(0, 2))
        self.new_group_name_var = StringVar()
        Entry(add_row, textvariable=self.new_group_name_var, width=11).grid(row=0, column=1, padx=2)
        Label(add_row, text="Prefixes:", font=("Arial", 8), bg="#ececec").grid(row=0, column=2, sticky=W, padx=(6, 2))
        self.new_group_prefixes_var = StringVar()
        Entry(add_row, textvariable=self.new_group_prefixes_var, width=14).grid(row=0, column=3, padx=2)
        Button(add_row, text="Add", font=("Arial", 8),
               command=self._curation_add_group).grid(row=0, column=4, padx=(4, 0))

        grp_btn_row = Frame(gf, bg="#ececec")
        grp_btn_row.pack(fill=X, pady=(2, 4))
        Button(grp_btn_row, text="Remove Selected", font=("Arial", 8),
               command=self._curation_remove_group).pack(side=LEFT, padx=2)
        Button(grp_btn_row, text="Auto-detect from files", font=("Arial", 8),
               command=self._curation_autodetect_groups).pack(side=LEFT, padx=4)

        Label(gf, text="\u24d8  Groups generate statistical comparison sheets in the export.\n"
                       "   Prefixes: e.g. D1, D2, D3  (one prefix per device).",
              font=("Arial", 8), fg="#555555", anchor=W, justify=LEFT,
              bg="#ececec").pack(anchor=W)

        # ── Tab 3: Thresholds ───────────────────────────────────────────
        tab_thresh = Frame(nb, padx=6, pady=4, bg="#ececec")
        nb.add(tab_thresh, text="  Thresholds  ")

        tf = LabelFrame(tab_thresh, text="Filter Parameters", padx=6, pady=4)
        tf.pack(fill=X, pady=(0, 6))

        self.threshold_params    = {}
        self._thresh_min_entries = {}

        param_defaults = [
            ("PCE",  5.0,  True),
            ("Voc",  0.8, False),
            ("Jsc", 10.0, False),
            ("FF",  40.0, False),
            ("HI",   0.0, False),
            ("Rs",   0.0, False),
            ("Rsh",  0.0, False),
        ]
        param_units = {
            "PCE": "(%)", "Voc": "(V)", "Jsc": "(mA/cm²)",
            "FF": "(%)", "HI": "(%)", "Rs": "(Ω·cm²)", "Rsh": "(Ω·cm²)",
        }

        for row_idx, (param, mn_def, en_def) in enumerate(param_defaults):
            enabled = BooleanVar(value=en_def)
            min_var = DoubleVar(value=mn_def)
            self.threshold_params[param] = {"enabled": enabled, "min": min_var}

            cb = Checkbutton(tf, text=f"{param} {param_units[param]}",
                             variable=enabled, width=14, anchor=W,
                             command=lambda p=param: self._toggle_threshold(p))
            cb.grid(row=row_idx, column=0, sticky=W, padx=4, pady=2)

            Label(tf, text="Min ≥").grid(row=row_idx, column=1, sticky=E, padx=(8, 2))
            min_e = Entry(tf, textvariable=min_var, width=8)
            min_e.grid(row=row_idx, column=2, sticky=W, padx=2)
            self._thresh_min_entries[param] = min_e

            if not en_def:
                min_e.config(state=DISABLED)

        scan_f = LabelFrame(tab_thresh, text="Apply threshold to", padx=6, pady=4)
        scan_f.pack(fill=X, pady=(0, 4))
        self.threshold_scan_mode = StringVar(value="both")
        Radiobutton(scan_f, text="Both scans independently",
                    variable=self.threshold_scan_mode, value="both",
                    bg="#ececec").pack(anchor=W)
        Radiobutton(scan_f, text="Reverse scan only",
                    variable=self.threshold_scan_mode, value="rev",
                    bg="#ececec").pack(anchor=W)

        # ── Tab 4: Output ───────────────────────────────────────────────
        tab_out = Frame(nb, padx=6, pady=4, bg="#ececec")
        nb.add(tab_out, text="  Output  ")

        out_f = LabelFrame(tab_out, text="Export Settings", padx=6, pady=4)
        out_f.pack(fill=X, pady=(0, 6))
        out_f.columnconfigure(0, weight=1)

        Label(out_f, text="Save to folder:").grid(
            row=0, column=0, sticky=W, padx=4, pady=(4, 2))
        self.curation_output_folder = StringVar()
        Entry(out_f, textvariable=self.curation_output_folder, width=22).grid(
            row=1, column=0, sticky="we", padx=4, pady=2)
        Button(out_f, text="Browse…", font=("Arial", 8),
               command=lambda: self.curation_output_folder.set(
                   filedialog.askdirectory() or self.curation_output_folder.get())
               ).grid(row=1, column=1, padx=(2, 4), pady=2)

        Label(out_f, text="Filename:").grid(
            row=2, column=0, sticky=W, padx=4, pady=(8, 2))
        ts_now = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.curation_filename_var = StringVar(
            value=f"data_curation_{ts_now}.xlsx")
        Entry(out_f, textvariable=self.curation_filename_var, width=28).grid(
            row=3, column=0, columnspan=2, sticky="we", padx=4, pady=2)

        # ── Action buttons ──────────────────────────────────────────────────
        btn_frame = Frame(self.curation_panel, bg="#ececec")
        btn_frame.pack(fill=X, padx=6, pady=(4, 4))

        self.curation_preview_btn = Button(
            btn_frame, text="Preview", font=("Arial", 9, "bold"),
            command=lambda: self._run_curation(preview_only=True), width=12)
        self.curation_preview_btn.pack(side=LEFT, padx=4)

        self.curation_run_btn = Button(
            btn_frame, text="Run & Export", font=("Arial", 9, "bold"),
            command=lambda: self._run_curation(preview_only=False),
            width=14, bg="#1565C0", fg="white")
        self.curation_run_btn.pack(side=LEFT, padx=4)

        # ── Status bar ────────────────────────────────────────────────────────────────────
        sb = Frame(self.curation_panel, bg="#37474F", height=24)
        sb.pack(fill=X, side="bottom")
        sb.pack_propagate(False)
        self.curation_status_var = StringVar(value="Ready — add files to begin.")
        Label(sb, textvariable=self.curation_status_var,
              bg="#37474F", fg="#CFD8DC",
              font=("Arial", 8), anchor=W
              ).pack(side=LEFT, padx=10, fill=X, expand=True)

        # ── Right preview frame ───────────────────────────────────────────────────────────
        self.curation_plot_frame = Frame(right_container, bg="#f8f8f8")

        right_nb = ttk.Notebook(self.curation_plot_frame)
        right_nb.pack(fill=BOTH, expand=True, padx=8, pady=8)

        # Tab 1 — Raw Data Preview (click a file to inspect)
        tab_raw_prev = Frame(right_nb, bg="#f8f8f8")
        right_nb.add(tab_raw_prev, text="  Raw Data Preview  ")

        self.curation_raw_frame = Frame(tab_raw_prev, bg="#f8f8f8")
        self.curation_raw_frame.pack(fill=BOTH, expand=True)
        Label(self.curation_raw_frame,
              text="Click a file in Input Files to preview its parameters.",
              font=("Arial", 11), fg="#9E9E9E").pack(expand=True)

        # Tab 2 — Data Preview (populated after clicking Preview)
        tab_data_prev = Frame(right_nb, bg="#f8f8f8")
        right_nb.add(tab_data_prev, text="  Data Preview  ")

        self.curation_tree_frame = Frame(tab_data_prev, bg="#f8f8f8")
        self.curation_tree_frame.pack(fill=BOTH, expand=True)

        self.curation_summary_var = StringVar(value="No data loaded.")
        Label(tab_data_prev, textvariable=self.curation_summary_var,
              font=("Arial", 9), anchor=W, fg="#37474F"
              ).pack(fill=X, pady=(4, 0))

        Label(self.curation_tree_frame,
              text="Run Preview to load data files.",
              font=("Arial", 11), fg="#9E9E9E").pack(expand=True)

    # ── Curation file management helpers ──────────────────────────────────────────────────────

    def _curation_add_files(self):
        paths = filedialog.askopenfilenames(
            title="Select J-V measurement files",
            filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")])
        for p in paths:
            if p not in self.curation_files:
                self.curation_files.append(p)
                self.curation_listbox.insert(END, os.path.basename(p))
        self._curation_update_count()

    def _curation_add_folder(self):
        folder = filedialog.askdirectory(
            title="Select folder containing xlsx files")
        if not folder:
            return
        import glob as _glob
        for p in sorted(_glob.glob(os.path.join(folder, "*.xlsx"))):
            if p not in self.curation_files:
                self.curation_files.append(p)
                self.curation_listbox.insert(END, os.path.basename(p))
        self._curation_update_count()

    def _curation_clear_files(self):
        self.curation_files.clear()
        self.curation_listbox.delete(0, END)
        self._curation_update_count()

    def _curation_remove_selected(self):
        for i in reversed(self.curation_listbox.curselection()):
            self.curation_listbox.delete(i)
            del self.curation_files[i]
        self._curation_update_count()

    def _curation_update_count(self):
        n = len(self.curation_files)
        self.curation_file_count_var.set(f"{n} file{'s' if n != 1 else ''}")

    def _curation_add_group(self):
        name = self.new_group_name_var.get().strip()
        prefixes_raw = self.new_group_prefixes_var.get().strip()
        if not name or not prefixes_raw:
            return
        prefixes = [p.strip() for p in prefixes_raw.split(",") if p.strip()]
        if not prefixes:
            return
        self.curation_groups.append({"name": name, "prefixes": prefixes})
        self.groups_tree.insert("", END, values=(name, ", ".join(prefixes)))
        self.new_group_name_var.set("")
        self.new_group_prefixes_var.set("")

    def _curation_remove_group(self):
        for iid in reversed(self.groups_tree.selection()):
            idx = self.groups_tree.index(iid)
            self.groups_tree.delete(iid)
            if idx < len(self.curation_groups):
                del self.curation_groups[idx]

    def _curation_autodetect_groups(self):
        if not self.curation_files:
            messagebox.showwarning("No Files",
                "Add input files first — prefixes are detected from filenames.")
            return
        from app.io.data_curator import _extract_prefix
        seen = {}
        for fp in self.curation_files:
            prefix = _extract_prefix(fp)
            seen.setdefault(prefix, prefix)
        self.curation_groups.clear()
        for iid in self.groups_tree.get_children():
            self.groups_tree.delete(iid)
        for prefix in sorted(seen.keys()):
            self.curation_groups.append({"name": prefix, "prefixes": [prefix]})
            self.groups_tree.insert("", END, values=(prefix, prefix))

    def _on_curation_file_select(self, event=None):
        sel = self.curation_listbox.curselection()
        if len(sel) != 1:
            return
        idx = sel[0]
        if idx >= len(self.curation_files):
            return
        fp = self.curation_files[idx]

        def _load():
            from app.io.data_curator import load_cell_data
            cell = load_cell_data(fp)
            self.root.after(0, self._show_raw_preview, cell, fp)

        threading.Thread(target=_load, daemon=True).start()

    def _show_raw_preview(self, cell, filepath):
        for w in self.curation_raw_frame.winfo_children():
            w.destroy()

        if cell is None:
            Label(self.curation_raw_frame,
                  text="Could not load file — not a valid J-V data file.",
                  font=("Arial", 9), fg="#B71C1C").pack(padx=8, pady=8)
            return

        Label(self.curation_raw_frame,
              text=os.path.basename(filepath),
              font=("Arial", 9, "bold")).pack(anchor=W, padx=8, pady=(6, 2))

        PARAMS = ["Voc", "Jsc", "FF", "PCE", "HI", "Rs", "Rsh"]
        headers = {
            "Voc": "Voc (V)", "Jsc": "Jsc (mA/cm²)", "FF": "FF (%)",
            "PCE": "PCE (%)", "HI": "HI (%)", "Rs": "Rs (Ω·cm²)", "Rsh": "Rsh (Ω·cm²)",
        }
        cols = ("param", "fwd", "rev")
        tree = ttk.Treeview(self.curation_raw_frame, columns=cols,
                            show="headings", height=len(PARAMS))
        tree.heading("param", text="Parameter"); tree.column("param", width=110, anchor=W)
        tree.heading("fwd",   text="Forward");   tree.column("fwd",   width=90, anchor="center")
        tree.heading("rev",   text="Reverse");   tree.column("rev",   width=90, anchor="center")

        for p in PARAMS:
            fv = cell.get_val(p, "fwd")
            rv = cell.get_val(p, "rev")
            fmt = lambda v: f"{v:.4g}" if v is not None and v == v else "—"
            tree.insert("", END, values=(headers[p], fmt(fv), fmt(rv)))

        tree.pack(fill=BOTH, expand=True, padx=6, pady=(0, 6))

    def _toggle_threshold(self, param):
        enabled = self.threshold_params[param]["enabled"].get()
        state = NORMAL if enabled else DISABLED
        self._thresh_min_entries[param].config(state=state)
        if not enabled:
            self._thresh_min_entries[param].config(bg="white")

    def _validate_threshold(self, param):
        try:
            self.threshold_params[param]["min"].get()
            invalid = False
        except Exception:
            invalid = True
        color = "#FFCDD2" if invalid else "white"
        self._thresh_min_entries[param].config(bg=color)

    # ── Curation runner ────────────────────────────────────────────────────────────────────────────────

    def _run_curation(self, preview_only=False):
        """Load files, apply thresholds, update treeview; optionally export."""
        from app.io.data_curator import (
            load_cell_data, group_by_substrate, export_curated_excel)

        if not self.curation_files:
            messagebox.showwarning("No Files", "Please add Excel files first.")
            return

        thresholds = {}
        for param, cfg in self.threshold_params.items():
            if cfg["enabled"].get():
                mn = cfg["min"].get()
                thresholds[param] = mn

        if not thresholds:
            messagebox.showwarning(
                "No Threshold",
                "Please enable at least one threshold parameter.")
            return

        files      = list(self.curation_files)
        comp_groups = list(self.curation_groups)
        scan_mode  = self.threshold_scan_mode.get()
        out_dir    = self.curation_output_folder.get()
        out_file   = self.curation_filename_var.get()

        def _worker():
            cells = []
            for i, fp in enumerate(files):
                self.root.after(0, self.curation_status_var.set,
                                f"Loading ({i+1}/{len(files)}): {os.path.basename(fp)}")
                cell = load_cell_data(fp)
                if cell:
                    cells.append(cell)

            if not cells:
                self.root.after(0, messagebox.showerror, "Error",
                    "No valid J-V data files could be loaded.\n"
                    "Only illuminated MeasurementData files are supported.")
                self.root.after(0, self.curation_status_var.set, "Load failed.")
                return

            groups = group_by_substrate(cells)
            self.root.after(0, self._update_curation_treeview, cells, thresholds)

            if not preview_only:
                if not out_dir:
                    self.root.after(0, messagebox.showerror, "No Output Folder",
                        "Please select an output folder in the Output tab.")
                    return
                out_path = os.path.join(out_dir, out_file)
                if os.path.exists(out_path):
                    stem = out_file.replace(".xlsx", "")
                    out_path = os.path.join(
                        out_dir,
                        f"{stem}_{datetime.now().strftime('%H%M%S')}.xlsx")
                try:
                    export_curated_excel(
                        cells, thresholds, scan_mode, groups, out_path,
                        composition_groups=comp_groups,
                        progress_callback=lambda msg: self.root.after(
                            0, self.curation_status_var.set, msg))
                    self.root.after(0, messagebox.showinfo, "Done",
                        f"Exported to:\n{out_path}")
                    self.root.after(0, self.curation_status_var.set,
                        f"Saved: {os.path.basename(out_path)}")
                except Exception as exc:
                    self.root.after(0, messagebox.showerror, "Export Error", str(exc))
                    self.root.after(0, self.curation_status_var.set, "Export failed.")
            else:
                n_pass_all = sum(
                    1 for c in cells
                    if all(c.passes(p, mn, scan=scan_mode)
                           for p, mn in thresholds.items()))
                self.root.after(0, self.curation_status_var.set,
                    f"Preview: {len(cells)} cells loaded, "
                    f"{n_pass_all} pass all thresholds.")

        threading.Thread(target=_worker, daemon=True).start()

    def _update_curation_treeview(self, cells, thresholds):
        """Rebuild the treeview with loaded cell data and pass/fail columns."""
        for w in self.curation_tree_frame.winfo_children():
            w.destroy()

        PARAMS     = ["Voc", "Jsc", "FF", "PCE", "HI", "Rs", "Rsh"]
        param_hdrs = {
            "Voc": "Voc (V)",         "Jsc": "Jsc (mA/cm²)",
            "FF":  "FF (%)",           "PCE": "PCE (%)",
            "HI":  "HI (%)",           "Rs":  "Rs (Ω·cm²)",
            "Rsh": "Rsh (Ω·cm²)",
        }
        pass_keys = list(thresholds.keys())
        pass_cols = [f"Pass_{p}" for p in pass_keys]
        cols      = ("file", "device", "scan") + tuple(PARAMS) + tuple(pass_cols)

        tree = ttk.Treeview(self.curation_tree_frame, columns=cols,
                            show="headings")
        tree.heading("file",   text="File");   tree.column("file",   width=140, minwidth=80)
        tree.heading("device", text="Device"); tree.column("device", width=75,  minwidth=50)
        tree.heading("scan",   text="Scan");   tree.column("scan",   width=45,  minwidth=40)
        for p in PARAMS:
            tree.heading(p, text=param_hdrs[p])
            tree.column(p, width=85, minwidth=60, anchor="center")
        for pc in pass_cols:
            tree.heading(pc, text=pc)
            tree.column(pc, width=65, minwidth=50, anchor="center")

        sb_y = ttk.Scrollbar(self.curation_tree_frame,
                             orient="vertical", command=tree.yview)
        sb_x = ttk.Scrollbar(self.curation_tree_frame,
                             orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
        sb_y.pack(side=RIGHT, fill=Y)
        sb_x.pack(side=BOTTOM, fill=X)
        tree.pack(fill=BOTH, expand=True)

        tree.tag_configure("pass",     background="#C8E6C9")
        tree.tag_configure("row_alt",  background="#F5F5F5")
        tree.tag_configure("row_norm", background="#FFFFFF")

        scan_mode   = self.threshold_scan_mode.get()
        pass_counts = {p: 0 for p in pass_keys}

        for i, cell in enumerate(cells):
            alt = i % 2 == 1
            for scan in ("fwd", "rev"):
                vals      = [cell.get_val(p, scan) for p in PARAMS]
                pass_syms = []
                all_pass  = True
                for param, mn in thresholds.items():
                    scan_chk = scan if scan_mode == "both" else "rev"
                    passed   = cell.passes(param, mn, scan=scan_chk)
                    pass_syms.append("✓" if passed else "✗")
                    if not passed:
                        all_pass = False
                    if scan == "rev" and passed:
                        pass_counts[param] += 1

                row_vals = (
                    os.path.basename(cell.filepath)[:35],
                    cell.device,
                    "Fwd" if scan == "fwd" else "Rev",
                ) + tuple(f"{v:.4g}" for v in vals) + tuple(pass_syms)

                tag = ("pass" if (all_pass and thresholds)
                       else ("row_alt" if alt else "row_norm"))
                tree.insert("", END, values=row_vals, tags=(tag,))

        parts = [f"Loaded: {len(cells)} cells"]
        for param in thresholds:
            parts.append(f"Pass {param}: {pass_counts[param]}/{len(cells)}")
        self.curation_summary_var.set("  |  ".join(parts))

    # PURE MPP TRACKING — panel builder
    # ══════════════════════════════════════════════════════════════════════════

    def _build_pure_mpp_panel(self, left_container, right_container):
        """Build Pure MPP Tracking (P&O) left panel and right plot area."""
        # ── Left panel ────────────────────────────────────────────────────────
        self.pmpp_panel = Frame(left_container, bg="#ececec")

        nb = ttk.Notebook(self.pmpp_panel)
        nb.pack(fill=X, padx=6, pady=(6, 4))

        # ─── Tab 1: Setup ─────────────────────────────────────────────────────
        tab_setup = Frame(nb, padx=6, pady=4, bg="#ececec")
        nb.add(tab_setup, text="  Setup  ")

        # Sample Information
        sf = LabelFrame(tab_setup, text="Sample Information", padx=6, pady=4)
        sf.pack(fill=X, pady=(0, 6))

        Label(sf, text="Sample Name:").grid(row=0, column=0, sticky=W, padx=4, pady=3)
        self.pmpp_sample_name_var = StringVar()
        Entry(sf, textvariable=self.pmpp_sample_name_var, width=22).grid(
            row=0, column=1, columnspan=2, sticky=W, padx=4, pady=3)

        Label(sf, text="Export Folder:").grid(row=1, column=0, sticky=W, padx=4, pady=3)
        self.pmpp_export_folder_var = StringVar()
        exp_entry = Entry(sf, textvariable=self.pmpp_export_folder_var, width=18)
        exp_entry.grid(row=1, column=1, sticky=W, padx=4, pady=3)
        Button(sf, text="Browse\u2026",
               command=lambda: self._browse_folder_for(self.pmpp_export_folder_var),
               font=("Arial", 8), pady=1
               ).grid(row=1, column=2, padx=(2, 4), pady=3)

        # Cell & Illumination
        cf = LabelFrame(tab_setup, text="Cell & Illumination", padx=6, pady=4)
        cf.pack(fill=X, pady=(0, 6))

        Label(cf, text="Cell Area:").grid(row=0, column=0, sticky=W, padx=4, pady=3)
        self.pmpp_cell_area_var = DoubleVar(value=0.07)
        Entry(cf, textvariable=self.pmpp_cell_area_var, width=10).grid(
            row=0, column=1, sticky=W, padx=4, pady=3)
        Label(cf, text="cm\u00b2", fg="#666666").grid(row=0, column=2, sticky=W)

        Label(cf, text="Detector Power:").grid(row=1, column=0, sticky=W, padx=4, pady=3)
        self.pmpp_detector_power_var = DoubleVar(value=0)
        Entry(cf, textvariable=self.pmpp_detector_power_var, width=10).grid(
            row=1, column=1, sticky=W, padx=4, pady=3)
        Label(cf, text="mW", fg="#666666").grid(row=1, column=2, sticky=W)

        Label(cf, text="Detector Area:").grid(row=2, column=0, sticky=W, padx=4, pady=3)
        self.pmpp_detector_area_var = DoubleVar(value=1.13)
        Entry(cf, textvariable=self.pmpp_detector_area_var, width=10).grid(
            row=2, column=1, sticky=W, padx=4, pady=3)
        Label(cf, text="cm\u00b2", fg="#666666").grid(row=2, column=2, sticky=W)

        Label(cf, text="Illumination:").grid(row=3, column=0, sticky=W, padx=4, pady=3)
        self.pmpp_illumination_var = StringVar(value="0.00")
        Label(cf, textvariable=self.pmpp_illumination_var,
              font=("Arial", 9, "bold"), fg="#1565C0").grid(
            row=3, column=1, sticky=W, padx=4, pady=3)
        Label(cf, text="mW/cm\u00b2", fg="#666666").grid(row=3, column=2, sticky=W)

        # Initial J-V Scan settings (in Setup tab, below illumination)
        jvsf = LabelFrame(tab_setup, text="Initial J-V Scan (finds Vmpp)", padx=6, pady=4)
        jvsf.pack(fill=X, pady=(0, 4))
        ToolTip(jvsf,
                "A reverse J-V scan is performed once at the start to locate the\n"
                "real Vmpp. The P&O algorithm then starts from that voltage.\n"
                "Use the same voltage range as your regular J-V measurements.")

        build_param_grid(jvsf, "pmpp", [
            ("Start Voltage:", "scan_start_v", -0.1,  "V"),
            ("End Voltage:",   "scan_end_v",    1.05, "V"),
            ("Step Voltage:",  "scan_step_v",   0.025, "V"),
        ], self)

        # ─── Tab 2: P&O Settings ──────────────────────────────────────────────
        tab_po = Frame(nb, padx=6, pady=4, bg="#ececec")
        nb.add(tab_po, text="  P&O Settings  ")

        pf, _ = build_mpp_settings_frame(tab_po, "pmpp", [
            ("Total duration:", "total_min",   60.0,  "min",
             "Total duration of the Pure MPP tracking experiment.\n"
             "Recommended: 60\u2013600 min for stability studies."),
            ("Perturb step:",   "step_mv",     10.0,  "mV",
             "Voltage perturbation step for the P&O algorithm.\n"
             "Smaller step = more accurate tracking, slower convergence.\n"
             "Larger step = faster convergence, more oscillation around MPP.\n"
             "Recommended: 5\u201320 mV."),
            ("Interval:",       "interval_ms", 500.0, "ms",
             "Time between consecutive P&O measurements.\n"
             "500 ms gives good time resolution without excessive noise."),
            ("V min:",          "v_min",        0.0,  "V",
             "Minimum allowed tracking voltage (safety clamp)."),
            ("V max:",          "v_max",        1.20, "V",
             "Maximum allowed tracking voltage (safety clamp)."),
            ("NPLC:",           "nplc",         1.0,  "(0.01\u201325)",
             "Number of power line cycles for each current reading.\n"
             "Higher = less noise, slower measurement."),
            ("Delay/point:",    "delay_ms",    20.0,  "ms",
             "Source delay after voltage change before reading current."),
        ], self, title="Tracking Parameters")
        pf.pack(fill=X, pady=(0, 6))

        # ── Control Buttons ───────────────────────────────────────────────────
        btn_frame = Frame(self.pmpp_panel, bg="#ececec")
        btn_frame.pack(fill=X, padx=8, pady=6)

        self.pmpp_start_btn = Button(
            btn_frame, text="Start Pure MPP",
            command=self.start_pure_mpp,
            bg="#00695C", fg="white",
            font=("Arial", 9, "bold"),
            width=16, pady=4, state=DISABLED)
        self.pmpp_start_btn.pack(side=LEFT, padx=(0, 6))

        self.pmpp_stop_btn = Button(
            btn_frame, text="Stop",
            command=self.stop_pure_mpp,
            bg="#C62828", fg="white",
            font=("Arial", 9, "bold"),
            width=10, pady=4, state=DISABLED)
        self.pmpp_stop_btn.pack(side=LEFT)

        # ── Status Bar ────────────────────────────────────────────────────────
        sb = Frame(self.pmpp_panel, bg="#37474F", height=24)
        sb.pack(fill=X, side="bottom")
        sb.pack_propagate(False)
        self.pmpp_status_var = StringVar(
            value="Ready \u2014 connect an instrument to begin.")
        Label(sb, textvariable=self.pmpp_status_var,
              bg="#37474F", fg="#CFD8DC",
              font=("Arial", 8), anchor=W
              ).pack(side=LEFT, padx=10, fill=X, expand=True)

        # ── Right plot area ───────────────────────────────────────────────────
        self.pmpp_plot_frame = Frame(right_container, bg="#ffffff")

        # Built via the shared plot factory so the L-1 continuous panel (later
        # sessions) and this Pure-MPP panel stay visually identical.
        from app.gui.mpp_plot_factory import build_mpp_continuous_panel

        panel = build_mpp_continuous_panel(self.pmpp_plot_frame)
        self.pmpp_fig    = panel["fig"]
        self.pmpp_canvas = panel["canvas"]
        self.pmpp_ax_pce = panel["ax_pce"]
        self.pmpp_ax_j   = panel["ax_j"]
        self.pmpp_ax_v   = panel["ax_v"]
        self.pmpp_ax_jv  = panel["ax_jv"]

    # ══════════════════════════════════════════════════════════════════════════
    # PURE MPP TRACKING — runner and plot-update methods
    # ══════════════════════════════════════════════════════════════════════════

    def update_pmpp_illumination(self, *args):
        try:
            self.pmpp_illumination_var.set(
                f"{self.pmpp_detector_power_var.get() / self.pmpp_detector_area_var.get():.2f}")
        except Exception:
            self.pmpp_illumination_var.set("Error")

    def start_pure_mpp(self):
        if not self.pmpp_sample_name_var.get().strip():
            messagebox.showerror("Missing input", "Enter a sample name.")
            return
        if not self.pmpp_export_folder_var.get().strip():
            messagebox.showerror("Missing input", "Select an export folder.")
            return
        if self.instrument is None:
            messagebox.showerror("No instrument", "Connect an instrument first.")
            return
        try:
            pin = float(self.pmpp_illumination_var.get())
            if pin <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Missing input",
                                 "Set Detector Power and Detector Area to calculate illumination.")
            return
        if not messagebox.askokcancel(
            "Safety Check",
            "1. Verify all connections to the Keithley SourceMeter\n"
            "2. Confirm solar simulator shutter is open\n"
            "3. Confirm only ONE cell is connected\n\n"
            "Click OK to start Pure MPP Tracking (P&O).",
            icon="warning"
        ):
            return

        self._measurement_running = True
        stop_event.clear()
        self.pmpp_start_btn.config(state=DISABLED)
        self.pmpp_stop_btn.config(state=NORMAL)
        self._init_pmpp_plots()

        params = {
            "cell_area":          self.pmpp_cell_area_var.get(),
            "pin":                pin,
            "total_duration_min": self.pmpp_total_min_var.get(),
            "scan_start_v":       self.pmpp_scan_start_v_var.get(),
            "scan_end_v":         self.pmpp_scan_end_v_var.get(),
            "scan_step_v":        self.pmpp_scan_step_v_var.get(),
            "perturb_step_mv":    self.pmpp_step_mv_var.get(),
            "interval_ms":        self.pmpp_interval_ms_var.get(),
            "v_min":              self.pmpp_v_min_var.get(),
            "v_max":              self.pmpp_v_max_var.get(),
            "nplc":               self.pmpp_nplc_var.get(),
            "delay_ms":           self.pmpp_delay_ms_var.get(),
        }
        callbacks = {
            "on_status":  lambda msg: self.root.after(0, self.pmpp_status_var.set, msg),
            "on_update":  lambda t, v, j, pce: self.root.after(
                              0, self._pmpp_update, t, v, j, pce),
            "on_jv_done": lambda v_arr, j_arr, vmpp: self.root.after(
                              0, self._pmpp_show_initial_jv, v_arr, j_arr, vmpp),
            "on_complete": lambda *a: self.root.after(0, self._pmpp_on_complete, *a),
        }

        from app.measurement.pure_mpp_tracker import PureMPPTracker
        tracker = PureMPPTracker(self.instrument, params, callbacks, stop_event)
        threading.Thread(target=tracker.run, daemon=True).start()

    def stop_pure_mpp(self):
        stop_event.set()
        self.pmpp_status_var.set("Stopping\u2026")
        self._measurement_running = False
        self.pmpp_start_btn.config(state=NORMAL)
        self.pmpp_stop_btn.config(state=DISABLED)

    def _init_pmpp_plots(self):
        for ax in [self.pmpp_ax_pce, self.pmpp_ax_j,
                   self.pmpp_ax_v, self.pmpp_ax_jv]:
            ax.clear()
        self.pmpp_ax_pce.set_xlabel("Time (min)")
        self.pmpp_ax_pce.set_ylabel("PCE (%)")
        self.pmpp_ax_pce.set_title("PCE(t) \u2014 Pure MPP Tracking (P&O)",
                                    fontweight="bold")
        self.pmpp_ax_pce.grid(True)
        self.pmpp_ax_j.set_xlabel("Time (min)")
        self.pmpp_ax_j.set_ylabel("J (mA/cm\u00b2)")
        self.pmpp_ax_j.set_title("Current Density at Vmpp")
        self.pmpp_ax_j.grid(True)
        self.pmpp_ax_v.set_xlabel("Time (min)")
        self.pmpp_ax_v.set_ylabel("Vmpp (V)")
        self.pmpp_ax_v.set_title("Tracked Vmpp")
        self.pmpp_ax_v.grid(True)
        self.pmpp_ax_jv.set_xlabel("Voltage (V)")
        self.pmpp_ax_jv.set_ylabel("J (mA/cm\u00b2)")
        self.pmpp_ax_jv.set_title("Initial J-V Scan")
        self.pmpp_ax_jv.text(0.5, 0.5, "Scanning\u2026",
                              transform=self.pmpp_ax_jv.transAxes,
                              ha="center", va="center",
                              color="gray", fontsize=9)
        self.pmpp_ax_jv.grid(True)
        self.pmpp_canvas.draw_idle()

        self._pmpp_t   = []
        self._pmpp_v   = []
        self._pmpp_j   = []
        self._pmpp_pce = []

    def _pmpp_show_initial_jv(self, v_arr, j_arr, vmpp):
        """Plot the initial J-V scan and mark Vmpp. Called once before P&O starts."""
        ax = self.pmpp_ax_jv
        ax.clear()
        ax.plot(v_arr, j_arr, color="#1B5E20", linewidth=1.5,
                marker="o", markersize=2, markerfacecolor="#1B5E20")
        ax.axvline(x=vmpp, color="#E65100", linewidth=1.0,
                   linestyle="--", label=f"Vmpp = {vmpp:.3f} V")
        ax.set_xlabel("Voltage (V)")
        ax.set_ylabel("J (mA/cm\u00b2)")
        ax.set_title("Initial J-V Scan")
        ax.legend(fontsize=7)
        ax.grid(True)
        self.pmpp_canvas.draw_idle()

    def _pmpp_update(self, t_abs, v, j, pce):
        t_min = t_abs / 60.0
        self._pmpp_t.append(t_min)
        self._pmpp_v.append(v)
        self._pmpp_j.append(j)
        self._pmpp_pce.append(pce)

        self.pmpp_ax_pce.clear()
        self.pmpp_ax_pce.plot(self._pmpp_t, self._pmpp_pce,
                              color="#1565C0", linewidth=1.2)
        self.pmpp_ax_pce.set_xlabel("Time (min)")
        self.pmpp_ax_pce.set_ylabel("PCE (%)")
        self.pmpp_ax_pce.set_title("PCE(t) \u2014 Pure MPP Tracking (P&O)",
                                    fontweight="bold")
        self.pmpp_ax_pce.grid(True)

        self.pmpp_ax_j.clear()
        self.pmpp_ax_j.plot(self._pmpp_t, self._pmpp_j,
                            color="#E65100", linewidth=1.2)
        self.pmpp_ax_j.set_xlabel("Time (min)")
        self.pmpp_ax_j.set_ylabel("J (mA/cm\u00b2)")
        self.pmpp_ax_j.set_title("Current Density at Vmpp")
        self.pmpp_ax_j.grid(True)

        self.pmpp_ax_v.clear()
        self.pmpp_ax_v.plot(self._pmpp_t, self._pmpp_v,
                            color="#4A148C", linewidth=1.0)
        self.pmpp_ax_v.set_xlabel("Time (min)")
        self.pmpp_ax_v.set_ylabel("Vmpp (V)")
        self.pmpp_ax_v.set_title("Tracked Vmpp")
        self.pmpp_ax_v.grid(True)

        self.pmpp_canvas.draw_idle()

    def _pmpp_on_complete(self, time_s, v_track, j_track, pce_track):
        self._measurement_running = False
        self.pmpp_start_btn.config(state=NORMAL)
        self.pmpp_stop_btn.config(state=DISABLED)
        self.pmpp_status_var.set("Pure MPP Tracking complete.")
        if not time_s:
            return
        try:
            path = save_pure_mpp_data(
                self.pmpp_export_folder_var.get(),
                self.pmpp_sample_name_var.get() or "sample",
                time_s, v_track, j_track, pce_track,
                cell_area=self.pmpp_cell_area_var.get(),
                illumination=float(self.pmpp_illumination_var.get()),
                nplc=self.pmpp_nplc_var.get(),
                delay_ms=self.pmpp_delay_ms_var.get(),
            )
            self.pmpp_status_var.set(
                f"Saved \u2192 {os.path.basename(path)}")
        except Exception as exc:
            messagebox.showerror("Export Error",
                                 f"Could not save data:\n{exc}")

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

        self.mpp_canvas.draw_idle()

        self._mpp_pce_times     = []
        self._mpp_pce_values    = []
        self._mpp_j_times       = []
        self._mpp_j_values      = []
        self._mpp_jv_cycles     = []
        self._mpp_cycle_bounds  = []   # accumulated SPO-time (min) at each cycle boundary
        self._mpp_last_cycle_n  = None
        self._mpp_last_t_abs    = None  # last t_abs seen, for computing dt
        self._mpp_spo_t_acc     = 0.0   # accumulated SPO seconds (J-V gaps excluded)

    def _mpp_update_spo_plot(self, t_abs, j, pce, cycle_n, stabilized):
        # ── Accumulate SPO-only time (J-V scan gaps are skipped) ──────────────
        if self._mpp_last_cycle_n is None:
            # Very first SPO point — start at t=0, no dt to add
            dt = 0.0
        elif cycle_n != self._mpp_last_cycle_n:
            # First point of a new cycle after a J-V scan.
            # Record the boundary position and insert a None break so
            # matplotlib does not draw a connecting line across the gap.
            self._mpp_cycle_bounds.append(self._mpp_spo_t_acc / 60.0)
            self._mpp_pce_times.append(None)
            self._mpp_pce_values.append(None)
            self._mpp_j_times.append(None)
            self._mpp_j_values.append(None)
            dt = 0.0  # do not add J-V scan time to the SPO clock
        else:
            # Normal SPO reading — accumulate real elapsed time
            dt = t_abs - self._mpp_last_t_abs

        self._mpp_spo_t_acc    += dt
        self._mpp_last_cycle_n  = cycle_n
        self._mpp_last_t_abs    = t_abs

        x_min = self._mpp_spo_t_acc / 60.0  # SPO-accumulated minutes

        self._mpp_pce_times.append(x_min)
        self._mpp_pce_values.append(pce)
        self._mpp_j_times.append(x_min)
        self._mpp_j_values.append(j)

        self.mpp_ax_pce.clear()
        self.mpp_ax_pce.plot(self._mpp_pce_times, self._mpp_pce_values,
                             "b-", linewidth=1.2)
        # Thin dashed line at each cycle boundary (= where next J-V scan ran)
        for t_bound in self._mpp_cycle_bounds:
            self.mpp_ax_pce.axvline(x=t_bound, color="#9E9E9E", linewidth=0.8,
                                    linestyle="--", alpha=0.7,
                                    label="_nolegend_")
        if stabilized:
            self.mpp_ax_pce.plot(x_min, pce, "go", markersize=5, alpha=0.7)
        self.mpp_ax_pce.set_xlabel("SPO Time (min)")
        self.mpp_ax_pce.set_ylabel("PCE (%)")
        self.mpp_ax_pce.set_title("Power Conversion Efficiency \u2014 MPP Tracking",
                                   fontweight="bold")
        self.mpp_ax_pce.grid(True)

        self.mpp_ax_j.clear()
        self.mpp_ax_j.plot(self._mpp_j_times, self._mpp_j_values,
                           color="#E65100", linewidth=1.2)
        for t_bound in self._mpp_cycle_bounds:
            self.mpp_ax_j.axvline(x=t_bound, color="#9E9E9E", linewidth=0.8,
                                  linestyle="--", alpha=0.7,
                                  label="_nolegend_")
        self.mpp_ax_j.set_xlabel("SPO Time (min)")
        self.mpp_ax_j.set_ylabel("Current Density (mA/cm\u00b2)")
        self.mpp_ax_j.set_title("Current Density at Vmpp (SPO phase)")
        self.mpp_ax_j.grid(True)

        self.mpp_canvas.draw_idle()

    def _mpp_update_jv_plot(self, cycle_data):
        self._mpp_jv_cycles.append(cycle_data)

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

        self.mpp_canvas.draw_idle()

    def _mpp_on_complete(self, cycles, spo_time, spo_j, spo_pce, spo_cycle_idx=None):
        self._measurement_running = False
        self.mpp_start_btn.config(state=NORMAL)
        self.mpp_stop_btn.config(state=DISABLED)
        self.mpp_status_var.set("MPP Tracking complete.")
        if not cycles:
            return
        try:
            path = save_mpp_data(
                self.mpp_export_folder_var.get(),
                self.mpp_sample_name_var.get() or "sample",
                cycles, spo_time, spo_j, spo_pce,
                spo_cycle_idx=spo_cycle_idx or [],
                cell_area=self.mpp_cell_area_var.get(),
                illumination=float(self.mpp_illumination_var.get()),
                nplc=self.mpp_nplc_var.get(),
                delay_ms=self.mpp_delay_var.get(),
                spo_duration_s=self.mpp_spo_duration_var.get(),
                total_duration_min=self.mpp_total_min_var.get(),
            )
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
            path = save_stab_current_data(
                self.stab_export_folder_var.get(),
                self.stab_sample_name_var.get() or "sample",
                times, j_vals, pce_vals,
                cell_area=self.stab_area_var.get(),
                v_applied=v_applied,
                illumination=pin,
                duration_s=self.stab_duration_var.get(),
                interval_ms=self.stab_interval_var.get(),
                threshold_pct_per_min=self.stab_threshold_var.get(),
                stabilized_at=stabilized_at,
            )
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

    # ── ISOS Protocols panel ──────────────────────────────────────────────────

    def _build_isos_panel(self, left_container, right_container):
        self.isos_panel      = Frame(left_container, bg="#ececec")
        self.isos_plot_frame = Frame(right_container, bg="#ffffff")

        # ── Notebook tabs ─────────────────────────────────────────────────────
        nb = ttk.Notebook(self.isos_panel)
        nb.pack(fill=X, padx=6, pady=(6, 4))
        self.isos_nb = nb
        # Fit the notebook to the active tab's height so short tabs (Experiment)
        # do not inherit the tall Conditioning tab's height (see _isos_fit_notebook).
        # Deferred to after_idle so the newly-shown page is laid out before measuring.
        nb.bind("<<NotebookTabChanged>>",
                lambda _e: self.root.after_idle(self._isos_fit_notebook))

        # Tab 1: Experiment
        tab_exp = Frame(nb, padx=6, pady=4, bg="#ececec")
        nb.add(tab_exp, text="  Experiment  ")

        exp_f = LabelFrame(tab_exp, text="Experiment", padx=6, pady=4)
        exp_f.pack(fill=X, pady=(0, 6))

        Label(exp_f, text="Protocol:").grid(row=0, column=0, sticky=W, padx=4, pady=3)
        self.isos_protocol_var = StringVar(value="ISOS-D-1")
        proto_cb = ttk.Combobox(exp_f, textvariable=self.isos_protocol_var,
                                values=list_protocol_labels(), state="readonly", width=12)
        proto_cb.grid(row=0, column=1, columnspan=2, sticky=W, padx=4, pady=3)
        proto_cb.bind("<<ComboboxSelected>>", self._isos_on_protocol_change)

        Label(exp_f, text="Sample Name:").grid(row=1, column=0, sticky=W, padx=4, pady=3)
        self.isos_sample_name_var = StringVar()
        isos_sample_entry = Entry(exp_f, textvariable=self.isos_sample_name_var, width=20)
        isos_sample_entry.grid(row=1, column=1, columnspan=2, sticky=W, padx=4, pady=3)
        ToolTip(isos_sample_entry, "Sample identifier used for file naming")

        Label(exp_f, text="Export Folder:").grid(row=2, column=0, sticky=W, padx=4, pady=3)
        self.isos_export_folder_var = StringVar()
        isos_folder_entry = Entry(exp_f, textvariable=self.isos_export_folder_var, width=14)
        isos_folder_entry.grid(row=2, column=1, sticky=W, padx=4, pady=3)
        Button(exp_f, text="Browse…", font=("Arial", 8), pady=1,
               command=self._isos_browse_folder
               ).grid(row=2, column=2, padx=(2, 4), pady=3)

        Label(exp_f, text="Electrical Load:").grid(row=3, column=0, sticky=W, padx=4, pady=3)
        self.isos_load_var = StringVar(value="OC")
        load_frame = Frame(exp_f, bg="#ececec")
        load_frame.grid(row=3, column=1, columnspan=2, sticky=W, padx=4, pady=2)
        self.isos_load_rb_oc = Radiobutton(load_frame, text="OC", variable=self.isos_load_var,
                                           value="OC", bg="#ececec")
        self.isos_load_rb_oc.pack(side=LEFT)
        self.isos_load_rb_mpp = Radiobutton(load_frame, text="MPP", variable=self.isos_load_var,
                                            value="MPP", bg="#ececec")
        self.isos_load_rb_mpp.pack(side=LEFT, padx=(8, 0))
        ToolTip(load_frame, "ISOS-D-1 is measured at Open Circuit (OC) only.\n"
                            "Other protocols (L-1, V-1) may use MPP.")

        btn_frame = Frame(tab_exp, bg="#ececec")
        btn_frame.pack(fill=X, pady=(2, 4))
        Button(btn_frame, text="New Experiment", font=("Arial", 9),
               command=self._isos_new_experiment
               ).pack(side=LEFT, padx=(0, 6))
        Button(btn_frame, text="Load Experiment…", font=("Arial", 9),
               command=self._isos_load_experiment
               ).pack(side=LEFT)

        self.isos_t0_var = StringVar(value="No experiment loaded")
        Label(tab_exp, textvariable=self.isos_t0_var,
              font=("Arial", 8), fg="#555555", bg="#ececec"
              ).pack(fill=X, padx=4, pady=(0, 4))

        # Tab 2: Scan
        tab_scan = Frame(nb, padx=6, pady=4, bg="#ececec")
        nb.add(tab_scan, text="  Scan  ")

        scan_f = LabelFrame(tab_scan, text="Scan Parameters", padx=6, pady=4)
        scan_f.pack(fill=X, pady=(0, 6))

        for row, (label, attr, default, unit) in enumerate([
            ("Start Voltage:", "isos_start_v",  -0.1,  "V"),
            ("End Voltage:",   "isos_end_v",     1.05,  "V"),
            ("Step Voltage:",  "isos_step_v",    0.025, "V"),
            ("Cell Area:",     "isos_cell_area", 0.07,  "cm²"),
        ]):
            Label(scan_f, text=label).grid(row=row, column=0, sticky=W, padx=4, pady=3)
            var   = DoubleVar(value=default)
            entry = Entry(scan_f, textvariable=var, width=10)
            entry.grid(row=row, column=1, sticky=W, padx=4, pady=3)
            Label(scan_f, text=unit, fg="#666666").grid(row=row, column=2, sticky=W)
            setattr(self, f"{attr}_var", var)

        ilf = LabelFrame(tab_scan, text="Illumination", padx=6, pady=4)
        ilf.pack(fill=X, pady=(0, 4))

        Label(ilf, text="Detector Power:").grid(row=0, column=0, sticky=W, padx=4, pady=3)
        self.isos_detector_power_var = DoubleVar(value=0)
        Entry(ilf, textvariable=self.isos_detector_power_var, width=10
              ).grid(row=0, column=1, sticky=W, padx=4, pady=3)
        Label(ilf, text="mW", fg="#666666").grid(row=0, column=2, sticky=W)

        Label(ilf, text="Detector Area:").grid(row=1, column=0, sticky=W, padx=4, pady=3)
        self.isos_detector_area_var = DoubleVar(value=1.13)
        Entry(ilf, textvariable=self.isos_detector_area_var, width=10
              ).grid(row=1, column=1, sticky=W, padx=4, pady=3)
        Label(ilf, text="cm²", fg="#666666").grid(row=1, column=2, sticky=W)

        Label(ilf, text="Illumination:").grid(row=2, column=0, sticky=W, padx=4, pady=3)
        self.isos_illumination_var = StringVar(value="0.00")
        Label(ilf, textvariable=self.isos_illumination_var,
              font=("Arial", 9, "bold"), fg="#1565C0"
              ).grid(row=2, column=1, sticky=W, padx=4, pady=3)
        Label(ilf, text="mW/cm²", fg="#666666").grid(row=2, column=2, sticky=W)

        self.isos_detector_power_var.trace("w", self._isos_update_illumination)
        self.isos_detector_area_var.trace("w",  self._isos_update_illumination)

        # Tab 3: Instrument
        tab_inst = Frame(nb, padx=6, pady=4, bg="#ececec")
        nb.add(tab_inst, text="  Instrument  ")

        inst_f = LabelFrame(tab_inst, text="Keithley 2400 Settings", padx=6, pady=4)
        inst_f.pack(fill=X, pady=(0, 6))

        # ── Scan rate input mode toggle ───────────────────────────────────────
        self.isos_scan_rate_mode = StringVar(value="advanced")
        isos_mode_frame = Frame(inst_f)
        isos_mode_frame.grid(row=0, column=0, columnspan=3, sticky=W, padx=4, pady=(4, 2))
        Radiobutton(isos_mode_frame, text="Advanced (manual delay)",
                    variable=self.isos_scan_rate_mode, value="advanced",
                    command=self._isos_on_scan_mode_change,
                    ).pack(side=LEFT)
        Radiobutton(isos_mode_frame, text="Scan rate (mV/s)",
                    variable=self.isos_scan_rate_mode, value="scan_rate",
                    command=self._isos_on_scan_mode_change,
                    ).pack(side=LEFT, padx=(12, 0))

        Label(inst_f, text="Delay per point:").grid(
            row=1, column=0, sticky=W, padx=4, pady=4)
        self.isos_delay_ms_var = DoubleVar(value=100.0)
        self.isos_delay_entry = Entry(inst_f, textvariable=self.isos_delay_ms_var, width=10)
        self.isos_delay_entry.grid(row=1, column=1, sticky=W, padx=4, pady=4)
        Label(inst_f, text="ms", fg="#666666").grid(row=1, column=2, sticky=W)
        ToolTip(self.isos_delay_entry,
                "Wait time (ms) between applying each voltage step and reading current.")

        Label(inst_f, text="NPLC:").grid(row=2, column=0, sticky=W, padx=4, pady=4)
        self.isos_nplc_var = DoubleVar(value=1.0)
        Entry(inst_f, textvariable=self.isos_nplc_var, width=10).grid(
            row=2, column=1, sticky=W, padx=4, pady=4)
        Label(inst_f, text="(0.01–25)", fg="#666666").grid(row=2, column=2, sticky=W)

        # ── Target scan rate row (hidden in advanced mode) ────────────────────
        self.isos_target_scan_rate_var = DoubleVar(value=100.0)
        self._isos_sr_label = Label(inst_f, text="Target scan rate:")
        self._isos_sr_entry = Entry(inst_f, textvariable=self.isos_target_scan_rate_var,
                                    width=10, state=DISABLED)
        self._isos_sr_unit  = Label(inst_f, text="mV/s", fg="#666666")
        self._isos_sr_label.grid(row=3, column=0, sticky=W, padx=4, pady=4)
        self._isos_sr_entry.grid(row=3, column=1, sticky=W, padx=4, pady=4)
        self._isos_sr_unit.grid( row=3, column=2, sticky=W)
        self._isos_sr_label.grid_remove()
        self._isos_sr_entry.grid_remove()
        self._isos_sr_unit.grid_remove()
        ToolTip(self._isos_sr_entry,
                "Target voltage sweep rate in mV/s.\n"
                "Delay is calculated automatically:\n"
                "  delay = (step_mV / scan_rate × 1000) − NPLC × 16.7 − 50 ms\n"
                "If the result would be negative, delay is clamped to 0 ms.")

        self.isos_four_wire_var = BooleanVar(value=False)
        Checkbutton(inst_f, text="4-Wire (Kelvin) sensing",
                    variable=self.isos_four_wire_var,
                    ).grid(row=4, column=0, columnspan=3, sticky=W, padx=4, pady=4)

        isos_srf = LabelFrame(tab_inst, text="Calculated Scan Rate", padx=6, pady=4)
        isos_srf.pack(fill=X, pady=(0, 4))

        self.isos_scan_rate_var = StringVar(value="—")
        isos_sr_lbl = Label(isos_srf, textvariable=self.isos_scan_rate_var,
                            font=("Arial", 11, "bold"), fg="#1565C0")
        isos_sr_lbl.grid(row=0, column=0, sticky=W, padx=4, pady=4)
        Label(isos_srf, text="mV/s", fg="#666666").grid(row=0, column=1, sticky=W)
        ToolTip(isos_sr_lbl,
                "Effective voltage sweep rate.\n"
                "Scan rate (mV/s) = Step Voltage (mV) ÷ Time per point (s)\n"
                "Time per point = Delay + NPLC × 16.7 ms + 50 ms (GPIB overhead)")

        self.isos_step_v_var.trace("w",           self._isos_on_measurement_params_change)
        self.isos_delay_ms_var.trace("w",         self._isos_on_measurement_params_change)
        self.isos_nplc_var.trace("w",             self._isos_on_measurement_params_change)
        self.isos_target_scan_rate_var.trace("w", self._isos_recalculate_delay_from_scan_rate)

        # Tab 4: Conditioning
        tab_cond = Frame(nb, padx=6, pady=4, bg="#ececec")
        nb.add(tab_cond, text="  Conditioning  ")

        dark_f = LabelFrame(tab_cond, text="Dark J-V (optional)", padx=6, pady=4)
        dark_f.pack(fill=X, pady=(0, 6))
        self.isos_dark_jv_var = BooleanVar(value=False)
        Checkbutton(dark_f, text="Measure dark J-V before illuminated scan",
                    variable=self.isos_dark_jv_var, bg="#ececec"
                    ).pack(anchor=W)

        pb_f = LabelFrame(tab_cond, text="Pre-bias", padx=6, pady=4)
        pb_f.pack(fill=X, pady=(0, 6))
        self.isos_prebias_enabled_var = BooleanVar(value=False)
        Checkbutton(pb_f, text="Enable pre-bias",
                    variable=self.isos_prebias_enabled_var, bg="#ececec"
                    ).grid(row=0, column=0, columnspan=3, sticky=W)
        Label(pb_f, text="Voltage:").grid(row=1, column=0, sticky=W, padx=4, pady=3)
        self.isos_prebias_voltage_var = DoubleVar(value=1.2)
        Entry(pb_f, textvariable=self.isos_prebias_voltage_var, width=10
              ).grid(row=1, column=1, sticky=W, padx=4, pady=3)
        Label(pb_f, text="V", fg="#666666").grid(row=1, column=2, sticky=W)
        Label(pb_f, text="Duration:").grid(row=2, column=0, sticky=W, padx=4, pady=3)
        self.isos_prebias_duration_var = DoubleVar(value=2.0)
        Entry(pb_f, textvariable=self.isos_prebias_duration_var, width=10
              ).grid(row=2, column=1, sticky=W, padx=4, pady=3)
        Label(pb_f, text="s", fg="#666666").grid(row=2, column=2, sticky=W)

        ls_f = LabelFrame(tab_cond, text="Light Soaking", padx=6, pady=4)
        ls_f.pack(fill=X, pady=(0, 4))
        self.isos_ls_enabled_var = BooleanVar(value=False)
        Checkbutton(ls_f, text="Enable light soaking",
                    variable=self.isos_ls_enabled_var, bg="#ececec"
                    ).grid(row=0, column=0, columnspan=3, sticky=W)
        Label(ls_f, text="Duration:").grid(row=1, column=0, sticky=W, padx=4, pady=3)
        self.isos_ls_duration_var = DoubleVar(value=300.0)
        Entry(ls_f, textvariable=self.isos_ls_duration_var, width=10
              ).grid(row=1, column=1, sticky=W, padx=4, pady=3)
        Label(ls_f, text="s", fg="#666666").grid(row=1, column=2, sticky=W)

        # ── ISOS-L-1 continuous-stress sub-frame (shown only for L-1) ───────────
        self._build_isos_l1_subframe(tab_cond)
        # ── ISOS-V-1 dark-bias sub-frame (shown only for V-1) ───────────────────
        self._build_isos_v1_subframe(tab_cond)

        # ── History Treeview (shown only for ISOS-D-1) ────────────────────────
        hist_f = LabelFrame(self.isos_panel, text="Measurement History", padx=4, pady=4)
        hist_f.pack(fill=X, padx=6, pady=(0, 4))
        self.isos_hist_frame = hist_f

        cols = ("#", "Date", "Elapsed_h", "PCE_rev", "PCE_norm")
        self.isos_tree = ttk.Treeview(hist_f, columns=cols, show="headings", height=5)
        for col, width in zip(cols, [30, 140, 70, 70, 70]):
            self.isos_tree.heading(col, text=col)
            self.isos_tree.column(col, width=width, anchor="center")
        vsb = ttk.Scrollbar(hist_f, orient="vertical", command=self.isos_tree.yview)
        self.isos_tree.configure(yscrollcommand=vsb.set)
        self.isos_tree.pack(side=LEFT, fill=BOTH, expand=True)
        vsb.pack(side=RIGHT, fill=Y)

        # ── Stability Metrics panel (T80 / T95 / Ts80 / T_S / PCE_S / n_1000) ───
        self._build_isos_metrics_panel(self.isos_panel)

        # ── Run/Stop buttons ──────────────────────────────────────────────────
        btn_ctrl = Frame(self.isos_panel, bg="#ececec")
        btn_ctrl.pack(fill=X, padx=8, pady=6)

        self.isos_start_btn = Button(btn_ctrl, text="Run ISOS",
                                     command=self.start_isos_measurement,
                                     bg="#388E3C", fg="white",
                                     font=("Arial", 9, "bold"),
                                     width=12, pady=4, state=DISABLED)
        self.isos_start_btn.pack(side=LEFT, padx=(0, 6))

        self.isos_stop_btn = Button(btn_ctrl, text="Stop Measurement",
                                    command=self.stop_isos_measurement,
                                    bg="#C62828", fg="white",
                                    font=("Arial", 9, "bold"),
                                    width=16, pady=4, state=DISABLED)
        self.isos_stop_btn.pack(side=LEFT)

        # ── Measurement Log ───────────────────────────────────────────────────
        isos_log_frame = LabelFrame(self.isos_panel, text="Measurement Log", padx=4, pady=4)
        isos_log_frame.pack(fill=BOTH, expand=True, padx=6, pady=(0, 0))

        isos_hsb = ttk.Scrollbar(isos_log_frame, orient="horizontal")
        isos_vsb = ttk.Scrollbar(isos_log_frame, orient="vertical")
        self.isos_log_text = Text(isos_log_frame, wrap="none", state=DISABLED,
                                  font=("Consolas", 9),
                                  yscrollcommand=isos_vsb.set,
                                  xscrollcommand=isos_hsb.set)
        isos_vsb.config(command=self.isos_log_text.yview)
        isos_hsb.config(command=self.isos_log_text.xview)
        isos_vsb.grid(row=0, column=1, sticky="ns")
        isos_hsb.grid(row=1, column=0, sticky="ew")
        self.isos_log_text.grid(row=0, column=0, sticky="nsew")
        self.isos_log_text.tag_configure("header",
                                         foreground="#1565C0",
                                         font=("Consolas", 9, "bold"))
        self.isos_log_text.tag_configure("separator", foreground="#aaaaaa")
        isos_log_frame.grid_rowconfigure(0, weight=1)
        isos_log_frame.grid_columnconfigure(0, weight=1)

        # ── Status bar ────────────────────────────────────────────────────────
        isos_sb = Frame(self.isos_panel, bg="#37474F", height=24)
        isos_sb.pack(fill=X, side="bottom")
        isos_sb.pack_propagate(False)
        # Autosave health dot (gray idle / green flushing / red on error).
        self.isos_autosave_dot = Label(isos_sb, text="●", bg="#37474F",
                                       fg="#607D8B", font=("Arial", 11))
        self.isos_autosave_dot.pack(side=RIGHT, padx=(0, 10))
        self._isos_autosave_tip = ToolTip(self.isos_autosave_dot, "Autosave idle")
        self._isos_autosave_reset_id = None
        self.isos_status_var = StringVar(value="Ready")
        Label(isos_sb, textvariable=self.isos_status_var,
              bg="#37474F", fg="#CFD8DC",
              font=("Arial", 8), anchor=W
              ).pack(side=LEFT, padx=10, fill=X, expand=True)

        # ── Matplotlib figure: PCE stability (top) + latest J-V (bottom) ───────
        # The active plot layout depends on the selected protocol/load — D-1 uses
        # this two-axis stability figure; L-1 swaps in a continuous OC / MPP panel
        # (see _isos_rebuild_plot_area). Live L-1 stress traces are buffered here.
        self._isos_plot_layout    = None
        self.isos_l1_plot_axes    = None
        self.isos_v1_plot_axes    = None
        self._isos_l1_last_draw   = 0.0
        self._isos_l1_reset_traces()
        self._isos_build_d1_figure()
        self._isos_plot_layout = "d1"

        self._isos_update_scan_rate()
        self._isos_on_protocol_change()

    def _isos_build_d1_figure(self):
        """Create the ISOS-D-1 two-axis figure (PCE stability + latest J-V)."""
        self.isos_fig, (self.isos_ax_pce, self.isos_ax_jv) = plt.subplots(
            2, 1, figsize=(7, 7), gridspec_kw={"height_ratios": [1, 1]})
        self.isos_fig.patch.set_facecolor("#ffffff")
        self.isos_ax_pce.set_xlabel("Time (h)", fontsize=11)
        self.isos_ax_pce.set_ylabel("Normalized PCE (%)", fontsize=11)
        self.isos_ax_pce.set_title("PCE Stability (ISOS-D-1)",
                                   fontsize=12, fontweight="bold")
        self.isos_ax_pce.grid(True)
        self.isos_ax_jv.set_xlabel("Voltage (V)", fontsize=11)
        self.isos_ax_jv.set_ylabel("Current Density (mA/cm²)", fontsize=11)
        self.isos_ax_jv.set_title("Latest J-V Measurement",
                                  fontsize=12, fontweight="bold")
        self.isos_ax_jv.grid(True)
        self.isos_fig.tight_layout()
        self.isos_canvas = FigureCanvasTkAgg(self.isos_fig, self.isos_plot_frame)
        self.isos_canvas.get_tk_widget().pack(fill=BOTH, expand=True)

    def _isos_l1_reset_traces(self):
        """Clear the buffered L-1 live-stress traces."""
        self._isos_l1_t    = []
        self._isos_l1_v    = []
        self._isos_l1_j    = []
        self._isos_l1_pce  = []
        self._isos_l1_voc  = []
        self._isos_l1_latest_jv = None

    # ── ISOS helper methods ───────────────────────────────────────────────────

    def _scan_for_inprogress_isos(self):
        """Offer to recover interrupted ISOS stress runs at startup.

        Continuous runs (L-1 / V-1) stream to ``*_stress_inprogress_*.csv`` files
        that are renamed to ``.consumed`` once consolidated. A leftover file means
        a run was interrupted. We scan every recent ISOS folder (plus the current
        export folder), pair each orphan with its tracking workbook, and offer a
        recovery dialog. Detection lives in ``app.io.isos_recovery`` so it is
        unit-testable without Tk; this method is just the GUI wrapper.
        """
        folders = list(load_recent_folders())
        try:
            cur = self.isos_export_folder_var.get().strip()
        except Exception:
            cur = ""
        if cur and cur not in folders:
            folders.append(cur)
        try:
            orphans = find_orphan_stress_csvs(folders)
        except Exception:
            orphans = []
        if orphans:
            self._show_recovery_dialog(orphans)

    def _show_recovery_dialog(self, orphans):
        """Modal recovery dialog for a list of ``(csv_path, xlsx_path)`` orphans."""
        # Group orphans by their tracking workbook for the summary list.
        by_xlsx = {}
        for csv_path, xlsx_path in orphans:
            by_xlsx.setdefault(xlsx_path, []).append(csv_path)

        win = Toplevel(self.root)
        win.title("Recover Unsaved Stress Logs")
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)

        Label(win, text=f"Found {len(orphans)} unsaved stress log(s) from a "
              "previous run:", font=("Arial", 10, "bold"),
              anchor=W, justify=LEFT).pack(fill=X, padx=14, pady=(14, 6))

        listbox = Frame(win)
        listbox.pack(fill=X, padx=20, pady=(0, 8))
        for xlsx_path, csvs in by_xlsx.items():
            name = os.path.basename(xlsx_path) if xlsx_path else "(no tracking file)"
            Label(listbox, text=f"• {name}  →  {len(csvs)} orphan CSV(s)",
                  anchor=W, justify=LEFT, font=("Consolas", 9)).pack(fill=X)

        Label(win, text="Consolidate them into the tracking files now?",
              anchor=W, justify=LEFT).pack(fill=X, padx=14, pady=(4, 10))

        btns = Frame(win)
        btns.pack(fill=X, padx=14, pady=(0, 14))

        def _finish(consolidated, discarded, errors):
            win.grab_release()
            win.destroy()
            parts = []
            if consolidated:
                parts.append(f"{consolidated} consolidated")
            if discarded:
                parts.append(f"{discarded} discarded")
            if errors:
                parts.append(f"{errors} failed")
            self.status_var.set("ISOS recovery: " + (", ".join(parts)
                                if parts else "nothing to do") + ".")

        def _consolidate_all():
            done = err = 0
            for csv_path, xlsx_path in orphans:
                if not xlsx_path:
                    continue
                try:
                    consolidate_orphan(csv_path, xlsx_path)
                    done += 1
                except Exception:
                    err += 1
            _finish(done, 0, err)

        def _discard_all():
            done = err = 0
            for csv_path, _ in orphans:
                try:
                    discard_orphan(csv_path)
                    done += 1
                except Exception:
                    err += 1
            _finish(0, done, err)

        def _decide_individually():
            win.grab_release()
            win.destroy()
            self._show_recovery_individual_dialog(orphans, _finish)

        Button(btns, text="Consolidate all", command=_consolidate_all,
               width=16).pack(side=LEFT, padx=4)
        Button(btns, text="Discard all", command=_discard_all,
               width=14).pack(side=LEFT, padx=4)
        Button(btns, text="Decide individually…",
               command=_decide_individually, width=18).pack(side=LEFT, padx=4)

    def _show_recovery_individual_dialog(self, orphans, finish_cb):
        """Per-file checkbox dialog: checked = consolidate, unchecked = discard."""
        win = Toplevel(self.root)
        win.title("Recover Stress Logs — Choose")
        win.transient(self.root)
        win.grab_set()

        Label(win, text="Check the logs to consolidate; unchecked logs are "
              "discarded (preserved as .discarded).", anchor=W, justify=LEFT
              ).pack(fill=X, padx=14, pady=(12, 8))

        rows = []
        body = Frame(win)
        body.pack(fill=BOTH, expand=True, padx=18, pady=(0, 8))
        for csv_path, xlsx_path in orphans:
            var = BooleanVar(value=bool(xlsx_path))   # only check if pairable
            label = os.path.basename(csv_path)
            if xlsx_path:
                label += f"  →  {os.path.basename(xlsx_path)}"
            else:
                label += "  (no tracking file — can only discard)"
            cb = Checkbutton(body, text=label, variable=var, anchor=W,
                             font=("Consolas", 9))
            cb.pack(fill=X)
            if not xlsx_path:
                cb.config(state=DISABLED)
            rows.append((csv_path, xlsx_path, var))

        def _apply():
            done = disc = err = 0
            for csv_path, xlsx_path, var in rows:
                try:
                    if var.get() and xlsx_path:
                        consolidate_orphan(csv_path, xlsx_path)
                        done += 1
                    else:
                        discard_orphan(csv_path)
                        disc += 1
                except Exception:
                    err += 1
            win.grab_release()
            win.destroy()
            finish_cb(done, disc, err)

        Button(win, text="Apply", command=_apply, width=12).pack(pady=(0, 14))

    def _build_isos_l1_subframe(self, parent):
        """Build the ISOS-L-1 continuous-stress controls (hidden until L-1)."""
        self.isos_l1_subframe = LabelFrame(
            parent, text="ISOS-L-1 Continuous Stress", padx=6, pady=4)
        # Not packed here — _isos_on_protocol_change shows/hides it.
        l1 = self.isos_l1_subframe

        def _num_row(r, label, attr, default, unit, vartype=DoubleVar):
            Label(l1, text=label).grid(row=r, column=0, sticky=W, padx=4, pady=2)
            var = vartype(value=default)
            Entry(l1, textvariable=var, width=8).grid(row=r, column=1, sticky=W, padx=2)
            Label(l1, text=unit, fg="#666666").grid(row=r, column=2, sticky=W)
            setattr(self, attr, var)

        _num_row(0, "Total duration:",     "isos_l1_total_h_var",        24.0, "h")
        _num_row(1, "Scan interval:",      "isos_l1_interval_min_var",   15.0, "min")
        _num_row(2, "Pre-scan relaxation:", "isos_l1_relax_s_var",       60.0, "s")
        _num_row(3, "SPO after scan:",     "isos_l1_spo_s_var",          20.0, "s (0=off)")

        self.isos_l1_equil_enabled_var = BooleanVar(value=False)
        Checkbutton(l1, text="Equilibration before start",
                    variable=self.isos_l1_equil_enabled_var
                    ).grid(row=4, column=0, columnspan=3, sticky=W, padx=4, pady=(6, 0))
        _num_row(5, "  Duration:", "isos_l1_equil_h_var", 0.0, "h")

        _num_row(6, "Baseline scans:", "isos_l1_baseline_n_var", 4, "", IntVar)
        _num_row(7, "Baseline rest:",  "isos_l1_baseline_rest_s_var", 60.0, "s")

        fail_f = LabelFrame(l1, text="Failure detection", padx=4, pady=2)
        fail_f.grid(row=8, column=0, columnspan=3, sticky="ew", padx=2, pady=(6, 2))
        Label(fail_f, text="PCE threshold:").grid(row=0, column=0, sticky=W, padx=2)
        self.isos_l1_fail_pct_var = DoubleVar(value=20.0)
        Entry(fail_f, textvariable=self.isos_l1_fail_pct_var, width=6
              ).grid(row=0, column=1, sticky=W, padx=2)
        Label(fail_f, text="% of PCE₀", fg="#666666").grid(row=0, column=2, sticky=W)
        self.isos_l1_auto_stop_var = BooleanVar(value=True)
        Checkbutton(fail_f, text="Auto-stop on failure",
                    variable=self.isos_l1_auto_stop_var
                    ).grid(row=1, column=0, columnspan=3, sticky=W)

        # MPP-only settings (revealed when load == MPP)
        self.isos_l1_mpp_frame = LabelFrame(l1, text="MPP load settings", padx=4, pady=2)
        Label(self.isos_l1_mpp_frame, text="Engine:").grid(row=0, column=0, sticky=W, padx=2)
        self.isos_l1_mpp_engine_var = StringVar(value="PO")
        Radiobutton(self.isos_l1_mpp_frame, text="P&O",
                    variable=self.isos_l1_mpp_engine_var, value="PO"
                    ).grid(row=0, column=1, sticky=W)
        Radiobutton(self.isos_l1_mpp_frame, text="JV-SPO",
                    variable=self.isos_l1_mpp_engine_var, value="SPO"
                    ).grid(row=0, column=2, sticky=W)
        mpp_params = Frame(self.isos_l1_mpp_frame)
        mpp_params.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(2, 0))
        build_param_grid(mpp_params, "isos_l1", [
            ("Perturb step:",     "perturb_step_mv",        10.0, "mV"),
            ("V min:",            "v_min",                  0.0,  "V"),
            ("V max:",            "v_max",                  1.2,  "V"),
            ("MPP log interval:", "stress_log_interval_s",  2.0,  "s"),
        ], self)

        # OC-only settings (revealed when load == OC)
        self.isos_l1_oc_frame = LabelFrame(l1, text="OC load settings", padx=4, pady=2)
        Label(self.isos_l1_oc_frame, text="Voc read interval:"
              ).grid(row=0, column=0, sticky=W, padx=2)
        self.isos_l1_voc_interval_ms_var = DoubleVar(value=1000.0)
        Entry(self.isos_l1_oc_frame, textvariable=self.isos_l1_voc_interval_ms_var,
              width=8).grid(row=0, column=1, sticky=W, padx=2)
        Label(self.isos_l1_oc_frame, text="ms", fg="#666666").grid(row=0, column=2, sticky=W)

        self._isos_l1_mpp_row = 9
        self._isos_l1_oc_row  = 10

        # Switching load reveals the matching sub-frame and swaps the plot layout.
        self.isos_load_var.trace("w", self._isos_on_load_change)

    # ── ISOS-V-1 sub-frame (dark electrical-bias stress) ────────────────────────

    _V1_TIER_DEFAULTS = [(0.0, 5.0, 60.0), (5.0, 24.0, 240.0), (24.0, 99999.0, 1440.0)]

    def _build_isos_v1_subframe(self, parent):
        """Build the ISOS-V-1 dark-bias controls (hidden until V-1 selected)."""
        self._isos_v1_custom_schedule = None
        self._isos_v1_scan_blocked    = False

        self.isos_v1_subframe = LabelFrame(
            parent, text="ISOS-V-1 Dark Electrical-Bias Stress", padx=6, pady=4)
        v1 = self.isos_v1_subframe

        r = 0
        Label(v1, text="Stress is performed in DARK. Shutter opens only briefly\n"
                       "for periodic illuminated J-V scans.",
              bg="#FFF59D", justify="left", font=("Arial", 8)
              ).grid(row=r, column=0, columnspan=3, sticky="ew", padx=2, pady=(0, 4))
        r += 1

        def _num_row(label, attr, default, unit, vartype=DoubleVar):
            nonlocal r
            Label(v1, text=label).grid(row=r, column=0, sticky=W, padx=4, pady=2)
            var = vartype(value=default)
            Entry(v1, textvariable=var, width=8).grid(row=r, column=1, sticky=W, padx=2)
            Label(v1, text=unit, fg="#666666").grid(row=r, column=2, sticky=W)
            setattr(self, attr, var)
            r += 1

        _num_row("Total duration:", "isos_v1_total_h_var", 24.0, "h")

        # ── Measurement schedule ───────────────────────────────────────────────
        sched_f = LabelFrame(v1, text="Measurement schedule", padx=4, pady=2)
        sched_f.grid(row=r, column=0, columnspan=3, sticky="ew", padx=2, pady=(4, 2))
        r += 1
        self.isos_v1_schedule_mode_var = StringVar(value="tiered")
        Radiobutton(sched_f, text="Fixed interval (min):",
                    variable=self.isos_v1_schedule_mode_var, value="fixed",
                    command=self._isos_v1_on_schedule_mode_change
                    ).grid(row=0, column=0, sticky=W)
        self.isos_v1_interval_min_var = DoubleVar(value=60.0)
        Entry(sched_f, textvariable=self.isos_v1_interval_min_var, width=7
              ).grid(row=0, column=1, sticky=W, padx=2)
        Radiobutton(sched_f, text="Tiered (recommended)",
                    variable=self.isos_v1_schedule_mode_var, value="tiered",
                    command=self._isos_v1_on_schedule_mode_change
                    ).grid(row=1, column=0, columnspan=2, sticky=W)

        # Editable 3-row tier table.
        self.isos_v1_tier_frame = Frame(sched_f)
        self.isos_v1_tier_frame.grid(row=2, column=0, columnspan=3, sticky="ew")
        for c, h in enumerate(("Start h", "End h", "Interval min")):
            Label(self.isos_v1_tier_frame, text=h, font=("Arial", 8),
                  fg="#666666").grid(row=0, column=c + 1, padx=2)
        self.isos_v1_tier_vars = []
        for i, (s0, e0, iv0) in enumerate(self._V1_TIER_DEFAULTS):
            Label(self.isos_v1_tier_frame, text=f"Phase {i + 1}:"
                  ).grid(row=i + 1, column=0, sticky=W, padx=2)
            sv, ev, ivv = DoubleVar(value=s0), DoubleVar(value=e0), DoubleVar(value=iv0)
            Entry(self.isos_v1_tier_frame, textvariable=sv, width=6
                  ).grid(row=i + 1, column=1, padx=2, pady=1)
            Entry(self.isos_v1_tier_frame, textvariable=ev, width=6
                  ).grid(row=i + 1, column=2, padx=2, pady=1)
            Entry(self.isos_v1_tier_frame, textvariable=ivv, width=7
                  ).grid(row=i + 1, column=3, padx=2, pady=1)
            self.isos_v1_tier_vars.append((sv, ev, ivv))

        self.isos_v1_custom_path_var = StringVar(value="")
        Radiobutton(sched_f, text="Custom (CSV)",
                    variable=self.isos_v1_schedule_mode_var, value="custom",
                    command=self._isos_v1_on_schedule_mode_change
                    ).grid(row=3, column=0, sticky=W)
        Button(sched_f, text="Import…", font=("Arial", 8), pady=0,
               command=self._isos_v1_import_schedule_csv
               ).grid(row=3, column=1, sticky=W, padx=2)

        # ── Dark J-V scan range + ideality fit window ──────────────────────────
        dark_f = LabelFrame(v1, text="Dark J-V scan", padx=4, pady=2)
        dark_f.grid(row=r, column=0, columnspan=3, sticky="ew", padx=2, pady=2)
        r += 1
        for c, (label, attr, default) in enumerate([
            ("Start (V):", "isos_v1_dark_start_v_var", -0.2),
            ("End (V):",   "isos_v1_dark_end_v_var",    1.1),
            ("Step (V):",  "isos_v1_dark_step_v_var",   0.025),
        ]):
            Label(dark_f, text=label).grid(row=0, column=c * 2, sticky=W, padx=2)
            var = DoubleVar(value=default)
            Entry(dark_f, textvariable=var, width=6
                  ).grid(row=0, column=c * 2 + 1, sticky=W, padx=2)
            setattr(self, attr, var)
        for c, (label, attr, default) in enumerate([
            ("Fit V_min:", "isos_v1_fit_vmin_var", 0.5),
            ("Fit V_max:", "isos_v1_fit_vmax_var", 0.8),
        ]):
            Label(dark_f, text=label).grid(row=1, column=c * 2, sticky=W, padx=2)
            var = DoubleVar(value=default)
            Entry(dark_f, textvariable=var, width=6
                  ).grid(row=1, column=c * 2 + 1, sticky=W, padx=2)
            setattr(self, attr, var)

        # ── Illuminated scan-rate warning (real-time) ──────────────────────────
        self.isos_v1_scan_warn_var = StringVar(value="")
        self._isos_v1_warn_lbl = Label(v1, textvariable=self.isos_v1_scan_warn_var,
                                       wraplength=240, justify="left",
                                       font=("Arial", 8), fg="#B71C1C")
        self._isos_v1_warn_lbl.grid(row=r, column=0, columnspan=3, sticky="ew",
                                    padx=2, pady=(2, 2))
        r += 1

        self.isos_v1_log_idark_var = BooleanVar(value=True)
        Checkbutton(v1, text="Log I_dark during stress",
                    variable=self.isos_v1_log_idark_var
                    ).grid(row=r, column=0, columnspan=3, sticky=W, padx=4)
        r += 1

        _num_row("Pre-scan relaxation:", "isos_v1_relax_s_var", 60.0, "s")
        _num_row("SPO after scan:",      "isos_v1_spo_s_var",   20.0, "s (0=off)")

        self.isos_v1_equil_enabled_var = BooleanVar(value=True)
        Checkbutton(v1, text="Equilibration before start",
                    variable=self.isos_v1_equil_enabled_var
                    ).grid(row=r, column=0, columnspan=3, sticky=W, padx=4, pady=(6, 0))
        r += 1
        _num_row("  Duration:", "isos_v1_equil_h_var", 12.0, "h")

        _num_row("Baseline scans:", "isos_v1_baseline_n_var", 4, "", IntVar)
        _num_row("Baseline rest:",  "isos_v1_baseline_rest_s_var", 60.0, "s")

        fail_f = LabelFrame(v1, text="Failure detection", padx=4, pady=2)
        fail_f.grid(row=r, column=0, columnspan=3, sticky="ew", padx=2, pady=(6, 2))
        r += 1
        Label(fail_f, text="PCE threshold (% of PCE₀):").grid(row=0, column=0, sticky=W)
        self.isos_v1_fail_pct_var = DoubleVar(value=20.0)
        Entry(fail_f, textvariable=self.isos_v1_fail_pct_var, width=6
              ).grid(row=0, column=1, sticky=W, padx=2)
        Label(fail_f, text="I_dark × baseline:").grid(row=1, column=0, sticky=W)
        self.isos_v1_fail_idark_mult_var = DoubleVar(value=5.0)
        Entry(fail_f, textvariable=self.isos_v1_fail_idark_mult_var, width=6
              ).grid(row=1, column=1, sticky=W, padx=2)
        Label(fail_f, text="R_sh_dark min (Ω·cm²):").grid(row=2, column=0, sticky=W)
        self.isos_v1_fail_rsh_dark_var = DoubleVar(value=0.0)
        Entry(fail_f, textvariable=self.isos_v1_fail_rsh_dark_var, width=6
              ).grid(row=2, column=1, sticky=W, padx=2)
        self.isos_v1_auto_stop_var = BooleanVar(value=True)
        Checkbutton(fail_f, text="Auto-stop on failure",
                    variable=self.isos_v1_auto_stop_var
                    ).grid(row=3, column=0, columnspan=2, sticky=W)

        # MPP hold settings (reused parameter grid; shown when load == MPP).
        self.isos_v1_mpp_frame = LabelFrame(v1, text="MPP hold settings", padx=4, pady=2)
        Label(self.isos_v1_mpp_frame,
              text="Hold last Vmpp from scan (constant V, dark).",
              font=("Arial", 8), fg="#666666"
              ).grid(row=0, column=0, columnspan=3, sticky=W)
        mpp_params = Frame(self.isos_v1_mpp_frame)
        mpp_params.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(2, 0))
        build_param_grid(mpp_params, "isos_v1", [
            ("V min:",            "v_min",                 0.0,  "V"),
            ("V max:",            "v_max",                 1.2,  "V"),
            ("I_dark log interval:", "stress_log_interval_s", 2.0, "s"),
        ], self)
        self._isos_v1_mpp_row = r
        r += 1

        self._isos_v1_on_schedule_mode_change()

    def _isos_v1_on_schedule_mode_change(self):
        # The fixed-interval entry and tier table stay visible in all modes; the
        # active schedule is resolved from the selected mode at run time by
        # _isos_v1_build_schedule(). This hook exists for the radiobuttons and
        # is a placeholder for future enable/disable styling.
        pass

    def _isos_v1_import_schedule_csv(self):
        path = filedialog.askopenfilename(
            title="Import V-1 schedule CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        sched = self._isos_v1_read_schedule_csv(path)
        if not sched:
            messagebox.showerror(
                "Invalid Schedule",
                "Could not parse a schedule from this CSV.\n"
                "Expected rows of: start_h, end_h, interval_min")
            return
        self._isos_v1_custom_schedule = sched
        self.isos_v1_custom_path_var.set(path)
        self.isos_v1_schedule_mode_var.set("custom")
        self.isos_status_var.set(
            f"Loaded {len(sched)} schedule tiers from {os.path.basename(path)}")

    @staticmethod
    def _isos_v1_read_schedule_csv(path):
        import csv
        sched = []
        try:
            with open(path, newline="") as fh:
                for row in csv.reader(fh):
                    cells = [c.strip() for c in row if c.strip() != ""]
                    if len(cells) < 3:
                        continue
                    try:
                        s0, e0, iv0 = float(cells[0]), float(cells[1]), float(cells[2])
                    except ValueError:
                        continue          # header or comment row
                    sched.append((s0, e0, iv0))
        except Exception:
            return []
        return sched

    def _isos_v1_build_schedule(self):
        """Return the active V-1 schedule as a list of (start_h, end_h, int_min).

        Fixed mode returns None (the runner falls back to scan_interval_min).
        """
        mode = self.isos_v1_schedule_mode_var.get()
        if mode == "fixed":
            return None
        if mode == "custom" and self._isos_v1_custom_schedule:
            return list(self._isos_v1_custom_schedule)
        # tiered
        tiers = []
        for sv, ev, ivv in self.isos_v1_tier_vars:
            try:
                tiers.append((float(sv.get()), float(ev.get()), float(ivv.get())))
            except Exception:
                continue
        return tiers or None

    def _isos_on_protocol_change(self, *_):
        protocol = self.isos_protocol_var.get()
        # ISOS-D-1 is OC-only; disable load selection and lock to OC
        if protocol == "ISOS-D-1":
            self.isos_load_var.set("OC")
            self.isos_load_rb_oc.config(state=DISABLED)
            self.isos_load_rb_mpp.config(state=DISABLED)
            self.isos_l1_subframe.pack_forget()
            self.isos_v1_subframe.pack_forget()
        elif protocol == "ISOS-V-1":
            self.isos_load_rb_oc.config(state=NORMAL)
            self.isos_load_rb_mpp.config(state=NORMAL)
            self.isos_l1_subframe.pack_forget()
            self.isos_v1_subframe.pack(fill=X, pady=(6, 0))
            self._isos_v1_reveal_load_frame()
            self._isos_v1_update_scan_warn()
        else:  # ISOS-L-1 (and future continuous protocols)
            self.isos_load_rb_oc.config(state=NORMAL)
            self.isos_load_rb_mpp.config(state=NORMAL)
            self.isos_v1_subframe.pack_forget()
            self.isos_l1_subframe.pack(fill=X, pady=(6, 0))
            self._isos_l1_reveal_load_frame()
        self._isos_toggle_history(protocol == "ISOS-D-1")
        self._isos_rebuild_plot_area()
        self.root.after_idle(self._isos_fit_notebook)

    def _isos_toggle_history(self, show: bool):
        """Show the Measurement History panel only for ISOS-D-1.

        The per-measurement history table is the primary readout for the sparse
        dark-storage D-1 campaign. For the continuous L-1 / V-1 protocols the
        live plots and the Stability Metrics panel carry the information, so the
        table is hidden to declutter the panel.
        """
        hist = getattr(self, "isos_hist_frame", None)
        if hist is None:
            return
        if show:
            if not hist.winfo_manager():     # currently hidden → restore order
                hist.pack(before=self.isos_metrics_frame, fill=X,
                          padx=6, pady=(0, 4))
        else:
            hist.pack_forget()

    def _isos_fit_notebook(self, *_):
        """Resize the ISOS notebook to the active tab's natural height.

        ``ttk.Notebook`` otherwise reserves the height of its tallest tab (the
        Conditioning tab with the L-1/V-1 sub-frames), leaving a large empty gap
        below shorter tabs such as Experiment. Fitting per tab removes that gap.
        """
        nb = getattr(self, "isos_nb", None)
        if nb is None:
            return
        try:
            nb.update_idletasks()
            page = nb.nametowidget(nb.select())
            nb.configure(height=max(page.winfo_reqheight(), 1))
        except Exception:
            pass

    def _isos_on_load_change(self, *_):
        """React to an OC ↔ MPP change while a continuous protocol is active."""
        protocol = self.isos_protocol_var.get()
        if protocol == "ISOS-D-1":
            return
        if self._measurement_running:
            return
        if protocol == "ISOS-V-1":
            self._isos_v1_reveal_load_frame()
        else:
            self._isos_l1_reveal_load_frame()
        self._isos_rebuild_plot_area()

    def _isos_v1_reveal_load_frame(self):
        """Show the MPP hold settings only when the V-1 load is MPP."""
        if self.isos_load_var.get() == "MPP":
            self.isos_v1_mpp_frame.grid(row=self._isos_v1_mpp_row, column=0,
                                        columnspan=3, sticky="ew", padx=2, pady=2)
        else:
            self.isos_v1_mpp_frame.grid_remove()

    def _isos_l1_reveal_load_frame(self):
        """Show the MPP- or OC-specific settings frame to match the load."""
        if self.isos_load_var.get() == "MPP":
            self.isos_l1_oc_frame.grid_remove()
            self.isos_l1_mpp_frame.grid(row=self._isos_l1_mpp_row, column=0,
                                        columnspan=3, sticky="ew", padx=2, pady=2)
        else:
            self.isos_l1_mpp_frame.grid_remove()
            self.isos_l1_oc_frame.grid(row=self._isos_l1_oc_row, column=0,
                                       columnspan=3, sticky="ew", padx=2, pady=2)

    # ── Dynamic plot area ──────────────────────────────────────────────────────

    def _isos_target_plot_layout(self):
        protocol = self.isos_protocol_var.get()
        if protocol == "ISOS-D-1":
            return "d1"
        if protocol == "ISOS-V-1":
            return "v1"
        return "mpp" if self.isos_load_var.get() == "MPP" else "oc"

    def _isos_rebuild_plot_area(self):
        """Swap the embedded figure to match the active protocol / load.

        D-1 keeps the two-axis stability figure; L-1 uses the continuous OC or
        MPP panel from the plot factory. No-ops when the layout is unchanged.
        """
        layout = self._isos_target_plot_layout()
        if layout == self._isos_plot_layout:
            return
        try:
            self.isos_canvas.get_tk_widget().destroy()
        except Exception:
            pass
        try:
            plt.close(self.isos_fig)
        except Exception:
            pass
        self.isos_l1_plot_axes = None
        self.isos_v1_plot_axes = None
        self.isos_ax_pce = None
        self.isos_ax_jv  = None

        if layout == "d1":
            self._isos_build_d1_figure()
        elif layout == "oc":
            from app.gui.mpp_plot_factory import build_oc_continuous_panel
            panel = build_oc_continuous_panel(self.isos_plot_frame)
            self.isos_fig    = panel["fig"]
            self.isos_canvas = panel["canvas"]
            self.isos_l1_plot_axes = panel
        elif layout == "v1":
            from app.gui.mpp_plot_factory import build_v1_diagnostic_panel
            panel = build_v1_diagnostic_panel(self.isos_plot_frame)
            self.isos_fig    = panel["fig"]
            self.isos_canvas = panel["canvas"]
            self.isos_v1_plot_axes = panel
        else:  # mpp
            from app.gui.mpp_plot_factory import build_mpp_continuous_panel
            panel = build_mpp_continuous_panel(self.isos_plot_frame)
            self.isos_fig    = panel["fig"]
            self.isos_canvas = panel["canvas"]
            self.isos_l1_plot_axes = panel
        self._isos_plot_layout = layout

    def _isos_add_to_log(self, text: str):
        self.isos_log_text.config(state=NORMAL)
        if self.isos_log_text.get("1.0", "end-1c"):
            self.isos_log_text.insert(END, "\n" + "─" * 60 + "\n", "separator")
        self.isos_log_text.insert(
            END,
            f"Measurement Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
            "header",
        )
        self.isos_log_text.insert(END, text)
        self.isos_log_text.see(END)
        self.isos_log_text.config(state=DISABLED)

    def _isos_browse_folder(self):
        folder = filedialog.askdirectory(title="Select ISOS Export Folder")
        if folder:
            self.isos_export_folder_var.set(folder)

    def _isos_update_illumination(self, *_):
        try:
            p = self.isos_detector_power_var.get()
            a = self.isos_detector_area_var.get()
            self.isos_illumination_var.set(f"{p / a:.2f}" if a else "0.00")
        except Exception:
            self.isos_illumination_var.set("0.00")

    def _isos_update_scan_rate(self, *_):
        try:
            step_mv     = abs(self.isos_step_v_var.get()) * 1000.0
            nplc_ms     = self.isos_nplc_var.get() * (1000.0 / 60.0)
            delay_ms    = self.isos_delay_ms_var.get()
            t_per_point = (delay_ms + nplc_ms + 50.0) / 1000.0
            if t_per_point <= 0 or step_mv <= 0:
                self.isos_scan_rate_var.set("—")
                return
            self.isos_scan_rate_var.set(f"{step_mv / t_per_point:.1f}")
        except Exception:
            self.isos_scan_rate_var.set("—")

    def _isos_on_measurement_params_change(self, *_):
        if self.isos_scan_rate_mode.get() == "scan_rate":
            self._isos_recalculate_delay_from_scan_rate()
        else:
            self._isos_update_scan_rate()
        self._isos_v1_update_scan_warn()

    def _isos_current_scan_rate_mvs(self):
        """Effective illuminated scan rate (mV/s), or None if not computable."""
        try:
            step_mv  = abs(self.isos_step_v_var.get()) * 1000.0
            nplc_ms  = self.isos_nplc_var.get() * (1000.0 / 60.0)
            delay_ms = self.isos_delay_ms_var.get()
            t_per_pt = (delay_ms + nplc_ms + 50.0) / 1000.0
            if t_per_pt <= 0 or step_mv <= 0:
                return None
            return step_mv / t_per_pt
        except Exception:
            return None

    def _isos_v1_update_scan_warn(self, *_):
        """Real-time illuminated scan-rate guard for V-1.

        A too-slow illuminated scan exposes the cell to light long enough to
        contaminate the dark-bias V-1 stress with L-1/T effects. Warn below
        50 mV/s; block (and disable Run) below 25 mV/s.
        """
        if not hasattr(self, "isos_v1_scan_warn_var"):
            return
        active = self.isos_protocol_var.get() == "ISOS-V-1"
        rate = self._isos_current_scan_rate_mvs()
        blocked = False
        if not active or rate is None:
            self.isos_v1_scan_warn_var.set("")
        elif rate < 25.0:
            blocked = True
            self.isos_v1_scan_warn_var.set(
                f"⛔ {rate:.0f} mV/s — too slow. Illuminated scan would "
                f"contaminate V-1 (light exposure). Run disabled until ≥ 25 mV/s.")
            self._isos_v1_warn_lbl.config(fg="#B71C1C")
        elif rate < 50.0:
            self.isos_v1_scan_warn_var.set(
                f"⚠ {rate:.0f} mV/s — slow illuminated scan may contaminate "
                f"V-1 (light exposure). Aim for ≥ 50 mV/s.")
            self._isos_v1_warn_lbl.config(fg="#E65100")
        else:
            self.isos_v1_scan_warn_var.set(f"✔ {rate:.0f} mV/s illuminated scan.")
            self._isos_v1_warn_lbl.config(fg="#2E7D32")
        self._isos_v1_scan_blocked = blocked
        # Reflect the block on the Run button when V-1 is active and the
        # instrument is connected (do not override the disconnected state).
        if active and getattr(self, "instrument", None) is not None \
                and not self._measurement_running:
            self.isos_start_btn.config(state=DISABLED if blocked else NORMAL)

    def _isos_on_scan_mode_change(self):
        mode = self.isos_scan_rate_mode.get()
        if mode == "advanced":
            self.isos_delay_entry.config(state=NORMAL)
            self._isos_sr_entry.config(state=DISABLED)
            self._isos_sr_label.grid_remove()
            self._isos_sr_entry.grid_remove()
            self._isos_sr_unit.grid_remove()
            self._isos_update_scan_rate()
        else:
            self.isos_delay_entry.config(state="readonly")
            self._isos_sr_entry.config(state=NORMAL)
            self._isos_sr_label.grid()
            self._isos_sr_entry.grid()
            self._isos_sr_unit.grid()
            self._isos_recalculate_delay_from_scan_rate()

    def _isos_recalculate_delay_from_scan_rate(self, *_):
        if self.isos_scan_rate_mode.get() != "scan_rate":
            return
        try:
            step_mv     = abs(self.isos_step_v_var.get()) * 1000.0
            target_rate = self.isos_target_scan_rate_var.get()
            nplc_ms     = self.isos_nplc_var.get() * (1000.0 / 60.0)
            if step_mv <= 0 or target_rate <= 0:
                return
            time_per_point_ms = (step_mv / target_rate) * 1000.0
            delay_ms          = max(0.0, time_per_point_ms - nplc_ms - 50.0)
            self.isos_delay_ms_var.set(round(delay_ms, 1))
            actual_time_ms = delay_ms + nplc_ms + 50.0
            actual_rate    = step_mv / (actual_time_ms / 1000.0)
            warning        = " ⚠" if delay_ms == 0.0 else ""
            self.isos_scan_rate_var.set(f"{actual_rate:.1f}{warning}")
        except Exception:
            pass

    def _isos_new_experiment(self):
        folder = self.isos_export_folder_var.get()
        if not folder:
            messagebox.showwarning("No Folder", "Please select an export folder first.")
            return
        if not os.path.isdir(folder):
            messagebox.showwarning("Invalid Folder", f"Folder does not exist:\n{folder}")
            return
        name     = self.isos_sample_name_var.get().strip() or "Sample"
        protocol = self.isos_protocol_var.get()
        if not messagebox.askyesno("New Experiment",
                                   f"Create a new {protocol} tracking file for '{name}'?\n\n"
                                   "A new file will be created — it will NOT overwrite "
                                   "any existing tracking file."):
            return
        config = self._isos_build_config()
        try:
            path = create_isos_tracking_file(folder, config)
        except Exception as exc:
            messagebox.showerror("Error", f"Could not create tracking file:\n{exc}")
            return
        self.isos_tracking_file = path
        add_recent_folder(folder)
        self.isos_t0_var.set(
            f"Tracking file: {os.path.basename(path)}\nt₀: {config.get('t0_timestamp', '')}")
        self.isos_status_var.set(f"New experiment: {os.path.basename(path)}")
        self._isos_update_treeview([])

    def _isos_load_experiment(self):
        path = filedialog.askopenfilename(
            title="Load ISOS Tracking File",
            filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")],
        )
        if not path:
            return
        cfg = read_isos_config(path)
        if not cfg:
            messagebox.showerror("Error", "Could not read config from this file.\n"
                                 "Make sure it is a valid ISOS tracking file.")
            return
        # Restore the protocol first so the panel reconfigures (load radios,
        # L-1 sub-frame, plot layout) before the rest of the config is applied.
        protocol = cfg.get("protocol_id") or "ISOS-D-1"
        if protocol in list_protocol_labels():
            self.isos_protocol_var.set(protocol)
        self._isos_on_protocol_change()
        self.isos_tracking_file = path
        self.isos_export_folder_var.set(os.path.dirname(path))
        add_recent_folder(os.path.dirname(path))
        self._isos_populate_from_config(cfg)
        self.root.update_idletasks()
        rows = read_isos_log(path)
        self._isos_update_treeview(rows)
        t0 = cfg.get("t0_timestamp", "")
        self.isos_t0_var.set(
            f"Tracking file: {os.path.basename(path)}\nt₀: {t0}")
        self.isos_status_var.set(
            f"Loaded: {os.path.basename(path)}  ({len(rows)} measurements)")
        if self._isos_plot_layout == "d1":
            try:
                self._isos_d1_update_metrics(rows, persist=False)
                self._isos_redraw_curves(path)
            except Exception:
                pass

    def _isos_populate_from_config(self, cfg: dict):
        def _set(var, key, cast=str):
            val = cfg.get(key)
            if val is not None:
                try:
                    var.set(cast(val))
                except Exception:
                    pass
        _set(self.isos_sample_name_var,          "sample_name")
        _set(self.isos_cell_area_var,            "cell_area",               float)
        _set(self.isos_detector_power_var,       "detector_power",          float)
        _set(self.isos_detector_area_var,        "detector_area",           float)
        _set(self.isos_start_v_var,              "start_voltage",           float)
        _set(self.isos_end_v_var,                "end_voltage",             float)
        _set(self.isos_step_v_var,               "step_voltage",            float)
        _set(self.isos_delay_ms_var,             "delay_ms",                float)
        _set(self.isos_nplc_var,                 "nplc",                    float)
        _set(self.isos_prebias_voltage_var,      "prebias_voltage",         float)
        _set(self.isos_prebias_duration_var,     "prebias_duration",        float)
        _set(self.isos_ls_duration_var,          "light_soaking_duration",  float)
        _set(self.isos_target_scan_rate_var,     "target_scan_rate",        float)
        # Booleans stored as True/False or 1/0
        for var, key in [
            (self.isos_four_wire_var,        "four_wire"),
            (self.isos_prebias_enabled_var,  "prebias_enabled"),
            (self.isos_ls_enabled_var,       "light_soaking_enabled"),
            (self.isos_dark_jv_var,          "dark_jv"),
        ]:
            val = cfg.get(key)
            if val is not None:
                try:
                    var.set(bool(val))
                except Exception:
                    pass
        if cfg.get("electrical_load"):
            self.isos_load_var.set(str(cfg["electrical_load"]))
        # ISOS-L-1 fields (nullable; blank in D-1 files)
        _set(self.isos_l1_total_h_var,            "total_duration_h",          float)
        _set(self.isos_l1_interval_min_var,       "scan_interval_min",         float)
        _set(self.isos_l1_relax_s_var,            "pre_scan_relaxation_s",     float)
        _set(self.isos_l1_spo_s_var,              "spo_duration_s",            float)
        _set(self.isos_l1_baseline_rest_s_var,    "baseline_rest_s",           float)
        _set(self.isos_l1_fail_pct_var,           "failure_pce_threshold_pct", float)
        _set(self.isos_l1_voc_interval_ms_var,    "voc_read_interval_ms",      float)
        _set(self.isos_l1_perturb_step_mv_var,    "perturb_step_mv",           float)
        _set(self.isos_l1_v_min_var,              "mpp_v_min",                 float)
        _set(self.isos_l1_v_max_var,              "mpp_v_max",                 float)
        _set(self.isos_l1_stress_log_interval_s_var, "mpp_log_interval_s",     float)
        _set(self.isos_l1_baseline_n_var,         "baseline_n_scans",          int)
        if cfg.get("mpp_engine") in ("PO", "SPO"):
            self.isos_l1_mpp_engine_var.set(str(cfg["mpp_engine"]))
        equil_h = cfg.get("equilibration_duration_h")
        if equil_h not in (None, ""):
            try:
                self.isos_l1_equil_h_var.set(float(equil_h))
                self.isos_l1_equil_enabled_var.set(float(equil_h) > 0)
            except Exception:
                pass
        auto_stop = cfg.get("auto_stop_on_failure")
        if auto_stop is not None and auto_stop != "":
            try:
                self.isos_l1_auto_stop_var.set(bool(auto_stop))
            except Exception:
                pass
        # ISOS-V-1 fields (nullable; blank in D-1 / L-1 files)
        _set(self.isos_v1_total_h_var,            "total_duration_h",          float)
        _set(self.isos_v1_interval_min_var,       "scan_interval_min",         float)
        _set(self.isos_v1_relax_s_var,            "pre_scan_relaxation_s",     float)
        _set(self.isos_v1_spo_s_var,              "spo_duration_s",            float)
        _set(self.isos_v1_baseline_rest_s_var,    "baseline_rest_s",           float)
        _set(self.isos_v1_baseline_n_var,         "baseline_n_scans",          int)
        _set(self.isos_v1_fail_pct_var,           "failure_pce_threshold_pct", float)
        _set(self.isos_v1_v_min_var,              "mpp_v_min",                 float)
        _set(self.isos_v1_v_max_var,              "mpp_v_max",                 float)
        _set(self.isos_v1_stress_log_interval_s_var, "mpp_log_interval_s",     float)
        _set(self.isos_v1_dark_start_v_var,       "dark_start_voltage",        float)
        _set(self.isos_v1_dark_end_v_var,         "dark_end_voltage",          float)
        _set(self.isos_v1_dark_step_v_var,        "dark_step_voltage",         float)
        _set(self.isos_v1_fit_vmin_var,           "dark_fit_v_min",            float)
        _set(self.isos_v1_fit_vmax_var,           "dark_fit_v_max",            float)
        v1_equil = cfg.get("equilibration_duration_h")
        if v1_equil not in (None, ""):
            try:
                self.isos_v1_equil_h_var.set(float(v1_equil))
                self.isos_v1_equil_enabled_var.set(float(v1_equil) > 0)
            except Exception:
                pass
        if auto_stop is not None and auto_stop != "":
            try:
                self.isos_v1_auto_stop_var.set(bool(auto_stop))
            except Exception:
                pass
        sched_mode = cfg.get("schedule_mode")
        if sched_mode in ("fixed", "tiered", "custom"):
            self.isos_v1_schedule_mode_var.set(sched_mode)
        log_idark = cfg.get("log_i_dark_during_stress")
        if log_idark is not None and log_idark != "":
            try:
                self.isos_v1_log_idark_var.set(bool(log_idark))
            except Exception:
                pass
        sched_json = cfg.get("tiered_schedule_json")
        if sched_json:
            try:
                tiers = json.loads(sched_json)
                for (sv, ev, ivv), tier in zip(self.isos_v1_tier_vars, tiers):
                    sv.set(float(tier[0])); ev.set(float(tier[1])); ivv.set(float(tier[2]))
            except Exception:
                pass
        # Restore scan rate mode last — triggers show/hide of the scan rate row
        mode = cfg.get("scan_rate_mode")
        if mode in ("advanced", "scan_rate"):
            try:
                self.isos_scan_rate_mode.set(mode)
                self._isos_on_scan_mode_change()
            except Exception:
                pass

    def _isos_build_config(self) -> dict:
        try:
            pin = float(self.isos_detector_power_var.get()) / float(self.isos_detector_area_var.get())
        except Exception:
            pin = 0.0
        cfg = {
            "protocol_id":             self.isos_protocol_var.get(),
            "sample_name":             self.isos_sample_name_var.get().strip() or "Sample",
            "cell_area":               self.isos_cell_area_var.get(),
            "detector_power":          self.isos_detector_power_var.get(),
            "detector_area":           self.isos_detector_area_var.get(),
            "start_voltage":           self.isos_start_v_var.get(),
            "end_voltage":             self.isos_end_v_var.get(),
            "step_voltage":            self.isos_step_v_var.get(),
            "delay_ms":                self.isos_delay_ms_var.get(),
            "nplc":                    self.isos_nplc_var.get(),
            "four_wire":               self.isos_four_wire_var.get(),
            "prebias_enabled":         self.isos_prebias_enabled_var.get(),
            "prebias_voltage":         self.isos_prebias_voltage_var.get(),
            "prebias_duration":        self.isos_prebias_duration_var.get(),
            "light_soaking_enabled":   self.isos_ls_enabled_var.get(),
            "light_soaking_duration":  self.isos_ls_duration_var.get(),
            "dark_jv":                 self.isos_dark_jv_var.get(),
            "electrical_load":         self.isos_load_var.get(),
            "scan_rate_mode":          self.isos_scan_rate_mode.get(),
            "target_scan_rate":        self.isos_target_scan_rate_var.get(),
        }
        # ISOS-L-1-specific config fields (persisted to the Config sheet).
        protocol = self.isos_protocol_var.get()
        if protocol == "ISOS-L-1":
            cfg.update({
                "total_duration_h":          self.isos_l1_total_h_var.get(),
                "scan_interval_min":         self.isos_l1_interval_min_var.get(),
                "pre_scan_relaxation_s":     self.isos_l1_relax_s_var.get(),
                "spo_duration_s":            self.isos_l1_spo_s_var.get(),
                "equilibration_duration_h":  (self.isos_l1_equil_h_var.get()
                                              if self.isos_l1_equil_enabled_var.get() else 0.0),
                "baseline_n_scans":          self.isos_l1_baseline_n_var.get(),
                "baseline_rest_s":           self.isos_l1_baseline_rest_s_var.get(),
                "mpp_engine":                self.isos_l1_mpp_engine_var.get(),
                "failure_pce_threshold_pct": self.isos_l1_fail_pct_var.get(),
                "auto_stop_on_failure":      self.isos_l1_auto_stop_var.get(),
                "voc_read_interval_ms":      self.isos_l1_voc_interval_ms_var.get(),
                "perturb_step_mv":           self.isos_l1_perturb_step_mv_var.get(),
                "mpp_v_min":                 self.isos_l1_v_min_var.get(),
                "mpp_v_max":                 self.isos_l1_v_max_var.get(),
                "mpp_log_interval_s":        self.isos_l1_stress_log_interval_s_var.get(),
            })
        elif protocol == "ISOS-V-1":
            sched = self._isos_v1_build_schedule()
            cfg.update({
                "total_duration_h":          self.isos_v1_total_h_var.get(),
                "scan_interval_min":         self.isos_v1_interval_min_var.get(),
                "pre_scan_relaxation_s":     self.isos_v1_relax_s_var.get(),
                "spo_duration_s":            self.isos_v1_spo_s_var.get(),
                "equilibration_duration_h":  (self.isos_v1_equil_h_var.get()
                                              if self.isos_v1_equil_enabled_var.get() else 0.0),
                "baseline_n_scans":          self.isos_v1_baseline_n_var.get(),
                "baseline_rest_s":           self.isos_v1_baseline_rest_s_var.get(),
                "mpp_engine":                "HOLD",
                "failure_pce_threshold_pct": self.isos_v1_fail_pct_var.get(),
                "auto_stop_on_failure":      self.isos_v1_auto_stop_var.get(),
                "mpp_v_min":                 self.isos_v1_v_min_var.get(),
                "mpp_v_max":                 self.isos_v1_v_max_var.get(),
                "mpp_log_interval_s":        self.isos_v1_stress_log_interval_s_var.get(),
                "stress_with_light":         False,
                "schedule_mode":             self.isos_v1_schedule_mode_var.get(),
                "log_i_dark_during_stress":  self.isos_v1_log_idark_var.get(),
                "dark_start_voltage":        self.isos_v1_dark_start_v_var.get(),
                "dark_end_voltage":          self.isos_v1_dark_end_v_var.get(),
                "dark_step_voltage":         self.isos_v1_dark_step_v_var.get(),
                "dark_fit_v_min":            self.isos_v1_fit_vmin_var.get(),
                "dark_fit_v_max":            self.isos_v1_fit_vmax_var.get(),
                "tiered_schedule_json":      json.dumps(sched) if sched else "",
            })
        return cfg

    def _isos_update_treeview(self, rows: list):
        for item in self.isos_tree.get_children():
            self.isos_tree.delete(item)
        for row in rows:
            self.isos_tree.insert("", END, values=(
                row.get("#", ""),
                row.get("Timestamp", ""),
                row.get("Elapsed_h", ""),
                row.get("PCE_rev (%)", ""),
                row.get("PCE_rev_norm", ""),
            ))
        if rows:
            last = self.isos_tree.get_children()[-1]
            self.isos_tree.see(last)

    # ── Stability metrics panel ────────────────────────────────────────────────

    def _build_isos_metrics_panel(self, parent):
        """Live T80/T95/Ts80/T_S/PCE_S/n_1000 readout + burn-in controls."""
        mf = LabelFrame(parent, text="Stability Metrics", padx=6, pady=4)
        mf.pack(fill=X, padx=6, pady=(0, 4))
        self.isos_metrics_frame = mf
        self.isos_metric_lbls = {}

        def _row(r, c, key, caption, tip):
            Label(mf, text=caption, font=("Arial", 9, "bold")
                  ).grid(row=r, column=c * 2, sticky=W, padx=(4, 2), pady=1)
            lbl = Label(mf, text="—", font=("Consolas", 9), fg="#999999",
                        anchor=W, width=20)
            lbl.grid(row=r, column=c * 2 + 1, sticky=W, padx=(0, 10), pady=1)
            self.isos_metric_lbls[key] = lbl
            ToolTip(lbl, tip)

        _row(0, 0, "t80",  "T80",   "First time PCE/PCE₀ ≤ 0.80.")
        _row(1, 0, "t95",  "T95",   "First time PCE/PCE₀ ≤ 0.95 (IEC-aligned).")
        _row(2, 0, "ts80", "Ts80",  "Time from T_S until PCE ≤ 0.80·PCE_S.")
        _row(0, 1, "ts",   "T_S",   "End of burn-in (stabilization time). "
             "Not applicable to dark-storage (ISOS-D) protocols.")
        _row(1, 1, "pces", "PCE_S", "PCE at the end of burn-in (t = T_S).")
        _row(2, 1, "n1000", "n_1000",
             "PCE(1000 h)/PCE(0); extrapolated when the run is < 1000 h.")

        ctrl = Frame(mf)
        ctrl.grid(row=3, column=0, columnspan=4, sticky=W, pady=(5, 0))
        Label(ctrl, text="Burn-in:", font=("Arial", 9, "bold")
              ).pack(side=LEFT, padx=(4, 4))
        self.isos_burnin_mode_var = StringVar(value="auto")
        for val, txt in (("auto", "Auto"), ("manual", "Manual"),
                         ("fallback", "Fallback")):
            Radiobutton(ctrl, text=txt, variable=self.isos_burnin_mode_var,
                        value=val, command=self._isos_on_burnin_change
                        ).pack(side=LEFT)
        Label(ctrl, text="T_S (h):").pack(side=LEFT, padx=(8, 2))
        self.isos_manual_ts_var = DoubleVar(value=0.0)
        ts_entry = Entry(ctrl, textvariable=self.isos_manual_ts_var, width=7)
        ts_entry.pack(side=LEFT)
        ts_entry.bind("<Return>", lambda *_: self._isos_on_burnin_change())
        Button(ctrl, text="Override…", command=self._isos_burnin_override
               ).pack(side=LEFT, padx=(6, 0))

        ctrl2 = Frame(mf)
        ctrl2.grid(row=4, column=0, columnspan=4, sticky=W, pady=(4, 0))
        self.isos_overlay_var = BooleanVar(value=True)
        Checkbutton(ctrl2, text="Show overlay on plot",
                    variable=self.isos_overlay_var,
                    command=self._isos_on_overlay_toggle).pack(side=LEFT, padx=(4, 8))
        Button(ctrl2, text="Copy metrics to clipboard",
               command=self._isos_copy_metrics).pack(side=LEFT)

    def _isos_on_overlay_toggle(self):
        """Redraw the active panel so the overlay appears/disappears at once."""
        self._isos_redraw_active_overlay()

    def _isos_copy_metrics(self):
        """Copy a formatted stability-metrics text block to the clipboard."""
        m = getattr(self, "_isos_last_metrics", None)
        if m is None:
            self.isos_status_var.set("No stability metrics to copy yet.")
            return
        proto  = self.isos_protocol_var.get()
        sample = self.isos_sample_name_var.get().strip() or "(unnamed)"

        def _h(value, reached):
            if value is None:
                return "not reached" if reached is False else "—"
            return f"{value:.1f} h"

        lines = [f"{proto} Stability Metrics", f"Sample: {sample}"]
        # PCE_0 ± std — std comes from the active runner's baseline when available.
        pce_0 = getattr(m, "pce_0", None)
        std = None
        runner = getattr(self, "_isos_active_runner", None)
        if runner is not None and getattr(runner, "baseline", None):
            std = runner.baseline.get("PCE_0_std")
        if pce_0 is not None:
            if std is not None:
                lines.append(f"PCE_0 = {pce_0:.2f} ± {std:.2f} %")
            else:
                lines.append(f"PCE_0 = {pce_0:.2f} %")
        lines.append(f"T80   = {_h(m.t80_h, m.t80_reached)}")
        lines.append(f"T95   = {_h(m.t95_h, m.t95_reached)}")
        if getattr(m, "burn_in_method", "") == "none":
            lines.append("T_S   = n/a (dark)")
            lines.append("PCE_S = n/a (dark)")
            lines.append("Ts80  = n/a (dark)")
        else:
            lines.append(f"T_S   = {_h(m.t_s_h, None)}")
            lines.append(f"PCE_S = {m.pce_s:.2f} %" if m.pce_s is not None
                         else "PCE_S = —")
            lines.append(f"Ts80  = {_h(m.ts80_h, m.ts80_reached)}")
        if m.n_1000 is not None:
            tag = ""
            if m.n_1000_extrapolated:
                conf = m.n_1000_confidence
                tag = (f" (extrapolated, ±{conf:.2f})" if conf is not None
                       else " (extrapolated)")
            lines.append(f"n_1000 = {m.n_1000:.2f}{tag}")
        else:
            lines.append("n_1000 = —")

        text = "\n".join(lines)
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.isos_status_var.set("Stability metrics copied to clipboard.")
        except Exception:
            self.isos_status_var.set("Could not access clipboard.")

    def _isos_reset_metrics_panel(self):
        for lbl in getattr(self, "isos_metric_lbls", {}).values():
            lbl.config(text="—", fg="#999999")

    def _isos_update_metrics_panel(self, m):
        lbls = getattr(self, "isos_metric_lbls", None)
        if not lbls or m is None:
            return

        def _set_time(key, value, reached):
            lbl = lbls.get(key)
            if lbl is None:
                return
            if value is None:
                lbl.config(text=("> experiment" if reached is False else "—"),
                           fg="#999999")
            else:
                mark = " ✓" if reached else ""
                lbl.config(text=f"{value:.1f} h{mark}",
                           fg="#2E7D32" if reached else "#333333")

        _set_time("t80",  m.t80_h,  m.t80_reached)
        _set_time("t95",  m.t95_h,  m.t95_reached)

        # Burn-in metrics (T_S / PCE_S / Ts80) do not apply to dark-storage
        # protocols, where the runner reports burn_in_method == "none".
        if getattr(m, "burn_in_method", "") == "none":
            for key in ("ts", "pces", "ts80"):
                lbls[key].config(text="n/a (dark)", fg="#999999")
        else:
            _set_time("ts80", m.ts80_h, m.ts80_reached)
            if m.t_s_h is not None:
                lbls["ts"].config(text=f"{m.t_s_h:.1f} h ({m.burn_in_method})",
                                  fg="#333333")
            else:
                lbls["ts"].config(text="—", fg="#999999")
            if m.pce_s is not None:
                lbls["pces"].config(text=f"{m.pce_s:.2f} %", fg="#333333")
            else:
                lbls["pces"].config(text="—", fg="#999999")

        if m.n_1000 is not None:
            txt = f"{m.n_1000:.2f}"
            if m.n_1000_extrapolated:
                conf = m.n_1000_confidence
                txt += (f" (extrap. ±{conf:.2f})" if conf is not None
                        else " (extrap.)")
            lbls["n1000"].config(text=txt, fg="#333333")
        else:
            lbls["n1000"].config(text="—", fg="#999999")

    def _isos_on_metrics_updated(self, metrics):
        """Runner-thread callback (posted to the GUI thread) for new metrics."""
        self._isos_last_metrics = metrics
        self._isos_update_metrics_panel(metrics)
        self._isos_redraw_active_overlay()

    def _isos_d1_update_metrics(self, rows, persist=True):
        """Light-independent metrics (T80 + n_1000) for ISOS-D dark storage.

        Burn-in (T_S / PCE_S / Ts80) is intentionally omitted — it is a
        light-driven phenomenon, so an auto-detected burn-in on a dark-storage
        curve would be spurious (see Khenkin et al., Nat. Energy 2020). Computed
        on the GUI thread from the already-read log (D-1 measurements are sparse,
        so this is cheap and there is no separate runner thread).
        """
        from app.measurement.stability_metrics import compute_stability_metrics
        times, pce = [], []
        for r in rows:
            t = r.get("Elapsed_h")
            p = r.get("PCE_rev (%)")
            if t is None or p in (None, ""):
                continue
            try:
                times.append(float(t))
                pce.append(float(p))
            except (TypeError, ValueError):
                continue
        if len(times) < 2:
            self._isos_last_metrics = None
            self._isos_reset_metrics_panel()
            return None

        metrics = compute_stability_metrics(
            times, pce, None,
            extrapolate_n1000=True,
            pce_column_used="PCE_rev",
            compute_burn_in=False,
        )
        self._isos_last_metrics = metrics
        self._isos_update_metrics_panel(metrics)
        if persist:
            try:
                append_metrics_row(self.isos_tracking_file, len(times),
                                   float(times[-1]), metrics)
            except Exception:
                pass
        return metrics

    def _isos_on_burnin_change(self, *_):
        """Recompute + redraw within ~200 ms when the burn-in mode/T_S changes."""
        runner = getattr(self, "_isos_active_runner", None)
        mode   = self.isos_burnin_mode_var.get()
        try:
            manual = float(self.isos_manual_ts_var.get())
        except Exception:
            manual = None
        if runner is not None:
            metrics = runner.recompute_metrics(burn_in_mode=mode,
                                               manual_burn_in_h=manual)
            if metrics is not None:
                self._isos_last_metrics = metrics
                self._isos_update_metrics_panel(metrics)
                self._isos_redraw_active_overlay()
        elif getattr(self, "_isos_last_metrics", None) is not None:
            self._isos_update_metrics_panel(self._isos_last_metrics)

    def _isos_burnin_override(self):
        from tkinter import simpledialog
        try:
            cur = float(self.isos_manual_ts_var.get())
        except Exception:
            cur = 0.0
        val = simpledialog.askfloat(
            "Manual Burn-in T_S", "Burn-in end T_S (hours):",
            initialvalue=cur, minvalue=0.0)
        if val is not None:
            self.isos_manual_ts_var.set(val)
            self.isos_burnin_mode_var.set("manual")
            self._isos_on_burnin_change()

    def _isos_redraw_active_overlay(self):
        """Redraw whichever continuous panel is live (applies the overlay)."""
        try:
            if getattr(self, "isos_v1_plot_axes", None):
                self._isos_v1_redraw()
            elif getattr(self, "isos_l1_plot_axes", None):
                self._isos_l1_redraw_stress()
        except Exception:
            pass

    def _isos_overlay_metrics(self, axes):
        """Overlay the latest stability metrics on a panel's PCE axis, if any."""
        overlay_var = getattr(self, "isos_overlay_var", None)
        if overlay_var is not None and not overlay_var.get():
            return
        m = getattr(self, "_isos_last_metrics", None)
        if m is None or not axes or "ax_pce" not in axes:
            return
        from app.gui.mpp_plot_factory import overlay_stability_metrics
        overlay_stability_metrics(axes["ax_pce"], m,
                                  getattr(self, "_isos_total_h", 0.0))
        canvas = axes.get("canvas")
        if canvas is not None:
            try:
                canvas.draw_idle()
            except Exception:
                pass

    def _isos_redraw_curves(self, tracking_path: str, new_measurement: bool = False):
        # ── Top: PCE_norm vs time ─────────────────────────────────────────────
        self.isos_ax_pce.cla()
        self.isos_ax_pce.set_xlabel("Time (h)", fontsize=11)
        self.isos_ax_pce.set_ylabel("Normalized PCE (ratio to t₀)", fontsize=11)
        self.isos_ax_pce.set_title("PCE Stability (ISOS-D-1)",
                                   fontsize=12, fontweight="bold")
        self.isos_ax_pce.grid(True)

        rows = read_isos_d1_log(tracking_path)
        if rows:
            elapsed = [float(r.get("Elapsed_h") or 0) for r in rows]
            fwd_norm = [r.get("PCE_fwd_norm") for r in rows]
            rev_norm = [r.get("PCE_rev_norm") for r in rows]

            valid_f = [(t, p) for t, p in zip(elapsed, fwd_norm) if p is not None]
            if valid_f:
                tf, pf = zip(*valid_f)
                self.isos_ax_pce.plot(tf, pf, "o--", color="#1565C0",
                                      linewidth=1.2, markersize=5, label="Fwd")

            valid_r = [(t, p) for t, p in zip(elapsed, rev_norm) if p is not None]
            if valid_r:
                tr, pr = zip(*valid_r)
                self.isos_ax_pce.plot(tr, pr, "o-", color="#B71C1C",
                                      linewidth=1.5, markersize=6, label="Rev")

            self.isos_ax_pce.axhline(y=1, color="#aaaaaa", linestyle=":", linewidth=1)
            if valid_f or valid_r:
                self.isos_ax_pce.legend(fontsize=9, loc="lower left")

        # ── Bottom: J-V curves ────────────────────────────────────────────────
        self.isos_ax_jv.cla()
        self.isos_ax_jv.set_xlabel("Voltage (V)", fontsize=11)
        self.isos_ax_jv.set_ylabel("Current Density (mA/cm²)", fontsize=11)
        self.isos_ax_jv.set_title("Latest J-V Measurement",
                                  fontsize=12, fontweight="bold")
        self.isos_ax_jv.grid(True)
        self.isos_ax_jv.axhline(y=0, color="#aaaaaa", linewidth=0.7)

        curves = read_isos_d1_jv_curves(tracking_path)
        fwd_curves = [c for c in curves if c["direction"] == "Fwd"]
        rev_curves = [c for c in curves if c["direction"] == "Rev"]

        if new_measurement:
            # After a new measurement: previous in grey, new (latest) in blue/red
            if len(fwd_curves) >= 2:
                p = fwd_curves[-2]
                self.isos_ax_jv.plot(p["v"], p["j"], "--", color="#aaaaaa",
                                     linewidth=1.0, alpha=0.7,
                                     label=f"Fwd  t = {p['elapsed_h']} h (prev)")
            if len(rev_curves) >= 2:
                p = rev_curves[-2]
                self.isos_ax_jv.plot(p["v"], p["j"], "-", color="#aaaaaa",
                                     linewidth=1.0, alpha=0.7,
                                     label=f"Rev  t = {p['elapsed_h']} h (prev)")
            if fwd_curves:
                c = fwd_curves[-1]
                self.isos_ax_jv.plot(c["v"], c["j"], "--", color="#1565C0",
                                     linewidth=1.5,
                                     label=f"Fwd  t = {c['elapsed_h']} h")
            if rev_curves:
                c = rev_curves[-1]
                self.isos_ax_jv.plot(c["v"], c["j"], "-", color="#B71C1C",
                                     linewidth=1.8,
                                     label=f"Rev  t = {c['elapsed_h']} h")
        else:
            # On load: show only the latest historical measurement in grey
            if fwd_curves:
                c = fwd_curves[-1]
                self.isos_ax_jv.plot(c["v"], c["j"], "--", color="#aaaaaa",
                                     linewidth=1.5,
                                     label=f"Fwd  t = {c['elapsed_h']} h")
            if rev_curves:
                c = rev_curves[-1]
                self.isos_ax_jv.plot(c["v"], c["j"], "-", color="#aaaaaa",
                                     linewidth=1.8,
                                     label=f"Rev  t = {c['elapsed_h']} h")

        if fwd_curves or rev_curves:
            self.isos_ax_jv.legend(fontsize=9, loc="lower left")

        # Overlay the light-independent stability markers (T80 / T95) on the
        # normalized PCE axis — burn-in markers are absent for dark storage.
        m = getattr(self, "_isos_last_metrics", None)
        if m is not None and rows:
            from app.gui.mpp_plot_factory import overlay_stability_metrics
            total = max((float(r.get("Elapsed_h") or 0) for r in rows), default=0.0)
            try:
                overlay_stability_metrics(self.isos_ax_pce, m, total)
            except Exception:
                pass

        # Fixed axes — same as J-V Measurement mode
        self.isos_ax_jv.set_xlim(0.0, 1.2)
        self.isos_ax_jv.set_ylim(0.0, 30.0)

        self.isos_fig.tight_layout()
        self.isos_canvas.draw_idle()

    # ── ISOS measurement methods ──────────────────────────────────────────────

    def start_isos_measurement(self):
        if self.isos_tracking_file is None:
            messagebox.showwarning(
                "No Experiment",
                "Please create or load an ISOS-D-1 tracking file first.\n"
                "Use 'New Experiment' or 'Load Experiment…' in the Experiment tab.")
            return
        if not os.path.isfile(self.isos_tracking_file):
            messagebox.showerror("Missing File",
                                 f"Tracking file not found:\n{self.isos_tracking_file}")
            return
        if self._measurement_running:
            return
        protocol = self.isos_protocol_var.get()
        # V-1: refuse to start while the illuminated scan rate is too slow.
        if protocol == "ISOS-V-1":
            self._isos_v1_update_scan_warn()
            if self._isos_v1_scan_blocked:
                rate = self._isos_current_scan_rate_mvs() or 0.0
                messagebox.showwarning(
                    "Scan Too Slow",
                    f"The illuminated J-V scan rate is {rate:.0f} mV/s.\n\n"
                    "ISOS-V-1 requires a fast illuminated scan (≥ 25 mV/s) so "
                    "light exposure does not contaminate the dark-bias stress.\n"
                    "Reduce the delay/NPLC or increase the step voltage.")
                return
        self._measurement_running = True
        stop_event.clear()
        self.isos_start_btn.config(state=DISABLED)
        self.isos_stop_btn.config(state=NORMAL)
        if protocol == "ISOS-D-1":
            target = self.run_isos_d1_measurement
        elif protocol == "ISOS-V-1":
            target = self.run_isos_v1_measurement
        else:
            target = self.run_isos_l1_measurement
        t = threading.Thread(target=target, daemon=True)
        t.start()

    def stop_isos_measurement(self):
        stop_event.set()
        self.isos_status_var.set("Stopping…")

    def run_isos_d1_measurement(self):
        import numpy as np

        def log(msg):
            self.root.after(0, lambda m=msg: self.isos_status_var.set(m))

        def _wait_shutter(title, body):
            ev = threading.Event()
            self.root.after(0, lambda: (messagebox.showinfo(title, body), ev.set()))
            ev.wait()

        try:
            self._isos_last_metrics = None
            self.root.after(0, self._isos_reset_metrics_panel)
            inst      = self.instrument
            folder    = self.isos_export_folder_var.get() or tempfile.gettempdir()
            name      = self.isos_sample_name_var.get().strip() or "Sample"
            cell_area = self.isos_cell_area_var.get()
            delay_ms  = self.isos_delay_ms_var.get()
            nplc      = self.isos_nplc_var.get()
            four_wire = self.isos_four_wire_var.get()
            start_v   = self.isos_start_v_var.get()
            end_v     = self.isos_end_v_var.get()
            step_v    = self.isos_step_v_var.get()
            pb_en     = self.isos_prebias_enabled_var.get()
            pb_v      = self.isos_prebias_voltage_var.get()
            pb_t      = self.isos_prebias_duration_var.get()
            ls_en     = self.isos_ls_enabled_var.get()
            ls_t      = self.isos_ls_duration_var.get()
            do_dark   = self.isos_dark_jv_var.get()
            try:
                pin = self.isos_detector_power_var.get() / self.isos_detector_area_var.get()
            except Exception:
                pin = 0.0

            # ── Optional dark J-V (instrument on during dark scans) ───────────
            if do_dark:
                init_instrument(inst, nplc, delay_ms / 1000.0, four_wire)
                log("Dark J-V: forward scan…")
                v_df, j_df = measure_scan(inst, start_v, end_v, step_v, cell_area, stop_event)
                if stop_event.is_set():
                    return
                log("Dark J-V: reverse scan…")
                v_dr, j_dr = measure_scan(inst, end_v, start_v, -step_v, cell_area, stop_event)
                if stop_event.is_set():
                    return
                try:
                    inst.write(":SOUR:VOLT 0")
                    inst.write(":OUTP OFF")
                except Exception:
                    pass
                save_dark_jv_data(
                    folder, name,
                    v_df, j_df, v_dr, j_dr,
                    cell_area, delay_ms, nplc, four_wire,
                    self.instrument_idn or "",
                )
                log("Dark J-V complete — waiting for shutter…")
                _wait_shutter(
                    "Dark J-V Complete — Open Shutter",
                    "Dark J-V measurement complete.\n\n"
                    "Please open the solar simulator shutter, then click OK\n"
                    "to begin the illuminated J-V measurement.",
                )
                if stop_event.is_set():
                    return
                init_instrument(inst, nplc, delay_ms / 1000.0, four_wire)
            else:
                # No dark J-V: confirm shutter BEFORE turning on the instrument
                _wait_shutter(
                    "Open Shutter",
                    "Please open the solar simulator shutter, then click OK\n"
                    "to begin the illuminated J-V measurement.",
                )
                if stop_event.is_set():
                    return
                init_instrument(inst, nplc, delay_ms / 1000.0, four_wire)

            # ── Light soaking ─────────────────────────────────────────────────
            if ls_en and not stop_event.is_set():
                apply_light_soaking(inst, ls_t, stop_event, self.isos_status_var.set)
                if stop_event.is_set():
                    return

            # ── Forward illuminated scan ──────────────────────────────────────
            log("Forward scan…")
            v_f, j_f = measure_scan(inst, start_v, end_v, step_v, cell_area, stop_event)
            if stop_event.is_set():
                return

            # ── Pre-bias between fwd and rev ──────────────────────────────────
            if pb_en and not stop_event.is_set():
                apply_prebias(inst, pb_v, pb_t, stop_event, self.isos_status_var.set)
                if stop_event.is_set():
                    return

            # ── Reverse illuminated scan ──────────────────────────────────────
            log("Reverse scan…")
            v_r, j_r = measure_scan(inst, end_v, start_v, -step_v, cell_area, stop_event)
            if stop_event.is_set():
                return

            inst.write(":OUTP OFF")

            # ── PV parameter extraction ───────────────────────────────────────
            log("Calculating parameters…")
            Voc_f, Jsc_f, FF_f, PCE_f = calculate_jv_parameters(v_f, j_f, pin)
            Voc_r, Jsc_r, FF_r, PCE_r = calculate_jv_parameters(v_r, j_r, pin)
            Rs_f,  Rsh_f               = calculate_resistances(v_f, j_f, Voc_f)
            Rs_r,  Rsh_r               = calculate_resistances(v_r, j_r, Voc_r)
            hi                         = calculate_hysteresis_index(PCE_f, PCE_r)

            nplc_ms       = nplc * (1000.0 / 60.0)
            t_per_point   = (delay_ms + nplc_ms + 50.0) / 1000.0
            scan_rate_val = (abs(step_v) * 1000.0) / t_per_point if t_per_point > 0 else 0.0

            # ── Save individual backup file ───────────────────────────────────
            log("Saving backup file…")
            save_data(
                folder, name,
                v_f, j_f, v_r, j_r, pin,
                Voc_f, Jsc_f, FF_f, PCE_f, Rs_f, Rsh_f,
                Voc_r, Jsc_r, FF_r, PCE_r, Rs_r, Rsh_r,
                hi,
                cell_area, delay_ms, nplc, four_wire,
                pb_en, pb_v, pb_t,
                self.instrument_idn or "",
                scan_rate=scan_rate_val,
            )

            # ── Append to cumulative tracking file ────────────────────────────
            log("Updating tracking file…")
            append_isos_d1_measurement(self.isos_tracking_file, {
                "v_fwd":   v_f,    "j_fwd":   j_f,
                "v_rev":   v_r,    "j_rev":   j_r,
                "Voc_fwd": Voc_f,  "Jsc_fwd": Jsc_f,
                "FF_fwd":  FF_f,   "PCE_fwd": PCE_f,
                "Rs_fwd":  Rs_f,   "Rsh_fwd": Rsh_f,
                "Voc_rev": Voc_r,  "Jsc_rev": Jsc_r,
                "FF_rev":  FF_r,   "PCE_rev": PCE_r,
                "Rs_rev":  Rs_r,   "Rsh_rev": Rsh_r,
                "HI":              hi,
                "illumination":    pin,
                "scan_rate":       scan_rate_val,
                "cell_area":       cell_area,
                "delay_ms":        delay_ms,
                "nplc":            nplc,
                "four_wire":       four_wire,
                "prebias_enabled": pb_en,
                "prebias_voltage": pb_v,
                "prebias_duration": pb_t,
                "instrument_idn":  self.instrument_idn or "",
            })

            # ── Refresh UI ────────────────────────────────────────────────────
            pb_str   = f"Pre-bias: {pb_v:.2f} V for {pb_t:.0f} s\n" if pb_en else ""
            log_text = (
                f"Sample: {name}\n"
                f"Instrument: {self.instrument_idn or 'unknown'}\n"
                f"Illumination: {pin:.2f} mW/cm²\n"
                f"Delay/pt: {delay_ms:.0f} ms  |  NPLC: {nplc:.2f}  |  "
                f"4-Wire: {'ON' if four_wire else 'OFF'}\n"
                f"{pb_str}"
                f"\nForward Scan:\n"
                f"  Voc:  {Voc_f:.3f} V\n  Jsc:  {Jsc_f:.3f} mA/cm²\n"
                f"  FF:   {FF_f:.2f} %\n  PCE:  {PCE_f:.2f} %\n"
                f"  Rs:   {Rs_f:.2f} Ω·cm²\n  Rsh:  {Rsh_f:.2f} Ω·cm²\n"
                f"\nReverse Scan:\n"
                f"  Voc:  {Voc_r:.3f} V\n  Jsc:  {Jsc_r:.3f} mA/cm²\n"
                f"  FF:   {FF_r:.2f} %\n  PCE:  {PCE_r:.2f} %\n"
                f"  Rs:   {Rs_r:.2f} Ω·cm²\n  Rsh:  {Rsh_r:.2f} Ω·cm²\n"
                f"\nHysteresis Index (HI): {hi:.2f} %\n"
            )

            def _refresh(lt=log_text):
                rows  = read_isos_d1_log(self.isos_tracking_file)
                last  = rows[-1] if rows else {}
                pce_r = last.get("PCE_rev (%)", "?")
                self._isos_update_treeview(rows)
                self._isos_d1_update_metrics(rows)
                self._isos_redraw_curves(self.isos_tracking_file, new_measurement=True)
                self._isos_add_to_log(lt)
                self.isos_status_var.set(f"Done — PCE_rev = {pce_r}%")
            self.root.after(0, _refresh)
            play_completion_sound()

        except Exception as exc:
            self.root.after(0, lambda e=exc: messagebox.showerror(
                "ISOS Measurement Error", str(e)))
            self.root.after(0, lambda: self.isos_status_var.set(f"Error: {exc}"))
        finally:
            self._measurement_running = False
            self._isos_active_runner = None
            stop_event.clear()
            self.root.after(0, lambda: self.isos_start_btn.config(state=NORMAL))
            self.root.after(0, lambda: self.isos_stop_btn.config(state=DISABLED))

    # ── ISOS-L-1 continuous-stress measurement ─────────────────────────────────

    def _isos_build_l1_runner_config(self) -> dict:
        """Assemble the ContinuousISOSRunner config from the L-1 sub-frame vars."""
        protocol = self.isos_protocol_var.get()
        spec     = get_protocol(protocol)
        try:
            pin = self.isos_detector_power_var.get() / self.isos_detector_area_var.get()
        except Exception:
            pin = 0.0
        load = self.isos_load_var.get()
        if load == "MPP":
            stress_log_interval_s = self.isos_l1_stress_log_interval_s_var.get()
        else:
            stress_log_interval_s = self.isos_l1_voc_interval_ms_var.get() / 1000.0
        return {
            "protocol_id":              protocol,
            "sample_name":              self.isos_sample_name_var.get().strip() or "Sample",
            "folder":                   self.isos_export_folder_var.get() or tempfile.gettempdir(),
            "tracking_file_path":       self.isos_tracking_file,
            "instrument_idn":           self.instrument_idn or "",
            "cell_area":                self.isos_cell_area_var.get(),
            "pin":                      pin,
            "start_v":                  self.isos_start_v_var.get(),
            "end_v":                    self.isos_end_v_var.get(),
            "step_v":                   self.isos_step_v_var.get(),
            "delay_ms":                 self.isos_delay_ms_var.get(),
            "nplc":                     self.isos_nplc_var.get(),
            "four_wire":                self.isos_four_wire_var.get(),
            "total_duration_h":         self.isos_l1_total_h_var.get(),
            "scan_interval_min":        self.isos_l1_interval_min_var.get(),
            "load_mode":                load,
            "mpp_engine":               self.isos_l1_mpp_engine_var.get(),
            "stress_with_light":        spec.stress_with_light,
            "requires_dark_jv":         spec.requires_dark_jv,
            "pre_scan_relaxation_s":    self.isos_l1_relax_s_var.get(),
            "spo_duration_s":           self.isos_l1_spo_s_var.get(),
            "equilibration_duration_h": (self.isos_l1_equil_h_var.get()
                                         if self.isos_l1_equil_enabled_var.get() else 0.0),
            "baseline_n_scans":         self.isos_l1_baseline_n_var.get(),
            "baseline_rest_s":          self.isos_l1_baseline_rest_s_var.get(),
            "auto_stop_on_failure":     self.isos_l1_auto_stop_var.get(),
            "failure_thresholds":       {"pce_norm": self.isos_l1_fail_pct_var.get() / 100.0},
            "stress_log_interval_s":    stress_log_interval_s,
            "perturb_step_mv":          self.isos_l1_perturb_step_mv_var.get(),
            "v_min":                    self.isos_l1_v_min_var.get(),
            "v_max":                    self.isos_l1_v_max_var.get(),
            "prebias_enabled":          self.isos_prebias_enabled_var.get(),
            "prebias_v":                self.isos_prebias_voltage_var.get(),
            "prebias_t":                self.isos_prebias_duration_var.get(),
            "light_soaking_enabled":    self.isos_ls_enabled_var.get(),
            "light_soaking_duration":   self.isos_ls_duration_var.get(),
            "burn_in_mode":             self._isos_burnin_mode(),
            "manual_burn_in_h":         self._isos_manual_burnin_h(),
        }

    def _isos_burnin_mode(self):
        return getattr(self, "isos_burnin_mode_var", None).get() \
            if getattr(self, "isos_burnin_mode_var", None) else "auto"

    def _isos_manual_burnin_h(self):
        if self._isos_burnin_mode() != "manual":
            return None
        try:
            return float(self.isos_manual_ts_var.get())
        except Exception:
            return None

    # ── Status-bar ETA + autosave indicator ────────────────────────────────────

    @staticmethod
    def _fmt_duration_h(hours):
        """Format a duration in hours as ``Xh Ym`` / ``Xh`` / ``Ym``."""
        total_min = int(round(max(0.0, float(hours)) * 60))
        h, m = divmod(total_min, 60)
        if h and m:
            return f"{h}h {m}m"
        if h:
            return f"{h}h"
        return f"{m}m"

    def _isos_format_eta(self, elapsed_h):
        """Build the continuous-experiment ETA status string, or ``None``.

        Example: ``"L-1 running — 3h 24m of 24h — next scan in 12m —
        ETA 2026-05-16 14:30"``.
        """
        total_h = float(getattr(self, "_isos_total_h", 0) or 0)
        if total_h <= 0:
            return None
        proto = self.isos_protocol_var.get().replace("ISOS-", "")
        next_txt = ""
        runner = getattr(self, "_isos_active_runner", None)
        if runner is not None:
            try:
                next_h = runner.next_scan_time(elapsed_h)
                next_txt = (" — next scan in "
                            f"{self._fmt_duration_h(max(0.0, next_h - elapsed_h))}")
            except Exception:
                next_txt = ""
        eta = datetime.now() + timedelta(hours=max(0.0, total_h - elapsed_h))
        return (f"{proto} running — {self._fmt_duration_h(elapsed_h)} of "
                f"{self._fmt_duration_h(total_h)}{next_txt} — "
                f"ETA {eta.strftime('%Y-%m-%d %H:%M')}")

    def _isos_update_eta(self, t_s, force=False):
        """Refresh the ETA status (at most once / 10 s unless ``force``)."""
        self._isos_last_stress_t_s = t_s
        now = time.time()
        if not force and (now - getattr(self, "_isos_eta_last", 0.0)) < 10.0:
            return
        self._isos_eta_last = now
        txt = self._isos_format_eta(t_s / 3600.0)
        if txt:
            self.isos_status_var.set(txt)

    def _isos_refresh_eta(self):
        """Force an ETA refresh (e.g. right after a scan completes)."""
        t_s = getattr(self, "_isos_last_stress_t_s", None)
        if t_s is not None:
            self._isos_update_eta(t_s, force=True)

    def _isos_on_autosave(self, ok, error):
        """Runner-thread autosave callback → flash the status-bar dot."""
        self.root.after(0, lambda: self._isos_set_autosave_dot(ok, error))

    def _isos_set_autosave_dot(self, ok, error):
        dot = getattr(self, "isos_autosave_dot", None)
        if dot is None:
            return
        if self._isos_autosave_reset_id is not None:
            try:
                self.root.after_cancel(self._isos_autosave_reset_id)
            except Exception:
                pass
            self._isos_autosave_reset_id = None
        if ok:
            dot.config(fg="#43A047")                 # green — flushing
            self._isos_autosave_tip.text = "Autosaving…"
            self._isos_autosave_reset_id = self.root.after(
                1200, self._isos_reset_autosave_dot)
        else:
            dot.config(fg="#E53935")                 # red — last flush failed
            self._isos_autosave_tip.text = f"Autosave error: {error}"

    def _isos_reset_autosave_dot(self):
        self._isos_autosave_reset_id = None
        dot = getattr(self, "isos_autosave_dot", None)
        if dot is not None:
            dot.config(fg="#607D8B")                 # gray — idle
            self._isos_autosave_tip.text = "Autosave idle"

    def run_isos_l1_measurement(self):
        from app.measurement.isos_continuous_runner import ContinuousISOSRunner

        def _after(fn, *a):
            self.root.after(0, lambda: fn(*a))

        try:
            cfg = self._isos_build_l1_runner_config()
            self._isos_l1_active_load = cfg.get("load_mode", "OC")
            self._isos_total_h = float(cfg.get("total_duration_h", 0) or 0)
            self._isos_last_metrics = None
            self.root.after(0, self._isos_reset_metrics_panel)
            # Fresh live buffers + a cleared plot for the new run.
            self._isos_l1_reset_traces()
            self._isos_l1_last_draw = 0.0
            self.root.after(0, self._isos_l1_redraw_stress)

            callbacks = {
                "on_status":           lambda m: _after(self.isos_status_var.set, m),
                "on_log":              lambda m: _after(self._isos_add_to_log, m),
                "on_stress_update":    self._isos_l1_stress_update,
                "on_autosave":         self._isos_on_autosave,
                "on_scan_done":        lambda meas: _after(self._isos_l1_on_scan_done, meas),
                "on_metrics_updated":  lambda mtr: _after(self._isos_on_metrics_updated, mtr),
                "on_baseline_done":    lambda b: _after(self._isos_l1_on_baseline_done, b),
                "on_failure_detected": lambda r: _after(self._isos_l1_on_failure, r),
                "on_complete":         lambda: _after(self.isos_status_var.set,
                                                      "ISOS-L-1 experiment complete."),
            }
            runner = ContinuousISOSRunner(self.instrument, cfg, callbacks, stop_event)
            self._isos_active_runner = runner
            runner.run()

        except Exception as exc:
            self.root.after(0, lambda e=exc: messagebox.showerror(
                "ISOS-L-1 Measurement Error", str(e)))
            self.root.after(0, lambda e=exc: self.isos_status_var.set(f"Error: {e}"))
        finally:
            self._measurement_running = False
            self._isos_active_runner = None
            stop_event.clear()
            self.root.after(0, lambda: self.isos_start_btn.config(state=NORMAL))
            self.root.after(0, lambda: self.isos_stop_btn.config(state=DISABLED))

    def _isos_l1_stress_update(self, t_s, v, j, pce):
        """Buffer one live stress sample and schedule a throttled (~5 Hz) redraw.

        Called from the runner thread — only appends to plain lists and posts a
        redraw onto the GUI thread via ``root.after``.
        """
        if getattr(self, "_isos_l1_active_load", "OC") == "MPP":
            self._isos_l1_t.append(t_s / 60.0)
            self._isos_l1_v.append(v)
            self._isos_l1_j.append(j)
            self._isos_l1_pce.append(pce)
        else:
            self._isos_l1_t.append(t_s / 3600.0)
            self._isos_l1_voc.append(v)
        # ETA refresh (self-throttled to 10 s on the GUI thread).
        self.root.after(0, lambda ts=t_s: self._isos_update_eta(ts))
        now = time.time()
        if now - self._isos_l1_last_draw < 0.2:
            return
        self._isos_l1_last_draw = now
        self.root.after(0, self._isos_l1_redraw_stress)

    def _isos_l1_redraw_stress(self):
        axes = self.isos_l1_plot_axes
        if not axes:
            return
        from app.gui.mpp_plot_factory import update_oc_plot, update_mpp_plot
        try:
            if "ax_voc" in axes:
                update_oc_plot(axes, list(self._isos_l1_t), list(self._isos_l1_voc),
                               self._isos_l1_latest_jv)
            else:
                update_mpp_plot(axes, list(self._isos_l1_t), list(self._isos_l1_v),
                                list(self._isos_l1_j), list(self._isos_l1_pce),
                                self._isos_l1_latest_jv)
                self._isos_overlay_metrics(axes)
        except Exception:
            pass

    def _isos_l1_on_scan_done(self, meas):
        v_r = meas.get("v_rev")
        j_r = meas.get("j_rev")
        if v_r is not None and j_r is not None:
            self._isos_l1_latest_jv = {"v": list(v_r), "j": list(j_r),
                                       "vmpp": meas.get("Vmpp")}
        try:
            rows = read_isos_log(self.isos_tracking_file)
            self._isos_update_treeview(rows)
        except Exception:
            pass
        self._isos_l1_redraw_stress()
        self._isos_refresh_eta()
        play_completion_sound()

    def _isos_l1_on_baseline_done(self, baseline):
        med = baseline.get("PCE_0_median") if baseline else None
        if med:
            self.isos_status_var.set(f"Baseline PCE₀ = {med:.3f}%")
            self.isos_tree.insert("", END, values=(
                "B", datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "0.0", f"{med:.3f}", "1.000"))
        else:
            self.isos_status_var.set("Baseline conditioning complete.")

    def _isos_l1_on_failure(self, reason):
        cont = messagebox.askyesno(
            "Cell Failure Detected",
            f"Cell failure detected: {reason}\n\nContinue the experiment?")
        if not cont:
            stop_event.set()

    # ── ISOS-V-1 dark-bias continuous measurement ──────────────────────────────

    def _isos_build_v1_runner_config(self) -> dict:
        """Assemble the ContinuousISOSRunner config from the V-1 sub-frame vars."""
        protocol = self.isos_protocol_var.get()
        spec     = get_protocol(protocol)
        try:
            pin = self.isos_detector_power_var.get() / self.isos_detector_area_var.get()
        except Exception:
            pin = 0.0
        load = self.isos_load_var.get()
        sched = self._isos_v1_build_schedule()
        return {
            "protocol_id":              protocol,
            "sample_name":              self.isos_sample_name_var.get().strip() or "Sample",
            "folder":                   self.isos_export_folder_var.get() or tempfile.gettempdir(),
            "tracking_file_path":       self.isos_tracking_file,
            "instrument_idn":           self.instrument_idn or "",
            "cell_area":                self.isos_cell_area_var.get(),
            "pin":                      pin,
            # Illuminated scan range (fast scan) — shared scan-parameter vars.
            "start_v":                  self.isos_start_v_var.get(),
            "end_v":                    self.isos_end_v_var.get(),
            "step_v":                   self.isos_step_v_var.get(),
            "delay_ms":                 self.isos_delay_ms_var.get(),
            "nplc":                     self.isos_nplc_var.get(),
            "four_wire":                self.isos_four_wire_var.get(),
            # Dark scan range + ideality fit window.
            "dark_start_v":             self.isos_v1_dark_start_v_var.get(),
            "dark_end_v":               self.isos_v1_dark_end_v_var.get(),
            "dark_step_v":              self.isos_v1_dark_step_v_var.get(),
            "dark_fit_v_min":           self.isos_v1_fit_vmin_var.get(),
            "dark_fit_v_max":           self.isos_v1_fit_vmax_var.get(),
            # Stress / scheduling.
            "total_duration_h":         self.isos_v1_total_h_var.get(),
            "scan_interval_min":        self.isos_v1_interval_min_var.get(),
            "tiered_schedule":          sched,
            "schedule_mode":            self.isos_v1_schedule_mode_var.get(),
            "load_mode":                load,
            "mpp_engine":               "HOLD",
            "stress_with_light":        spec.stress_with_light,   # False for V-1
            "requires_dark_jv":         spec.requires_dark_jv,    # True for V-1
            "log_i_dark_during_stress": self.isos_v1_log_idark_var.get(),
            "pre_scan_relaxation_s":    self.isos_v1_relax_s_var.get(),
            "spo_duration_s":           self.isos_v1_spo_s_var.get(),
            "equilibration_duration_h": (self.isos_v1_equil_h_var.get()
                                         if self.isos_v1_equil_enabled_var.get() else 0.0),
            "baseline_n_scans":         self.isos_v1_baseline_n_var.get(),
            "baseline_rest_s":          self.isos_v1_baseline_rest_s_var.get(),
            "auto_stop_on_failure":     self.isos_v1_auto_stop_var.get(),
            "failure_thresholds": {
                "pce_norm":    self.isos_v1_fail_pct_var.get() / 100.0,
                "idark_ratio": (self.isos_v1_fail_idark_mult_var.get()
                                if self.isos_v1_fail_idark_mult_var.get() > 0 else None),
                "rsh_dark":    (self.isos_v1_fail_rsh_dark_var.get()
                                if self.isos_v1_fail_rsh_dark_var.get() > 0 else None),
            },
            "stress_log_interval_s":    self.isos_v1_stress_log_interval_s_var.get(),
            "v_min":                    self.isos_v1_v_min_var.get(),
            "v_max":                    self.isos_v1_v_max_var.get(),
            "prebias_enabled":          self.isos_prebias_enabled_var.get(),
            "prebias_v":                self.isos_prebias_voltage_var.get(),
            "prebias_t":                self.isos_prebias_duration_var.get(),
            "light_soaking_enabled":    self.isos_ls_enabled_var.get(),
            "light_soaking_duration":   self.isos_ls_duration_var.get(),
            "burn_in_mode":             self._isos_burnin_mode(),
            "manual_burn_in_h":         self._isos_manual_burnin_h(),
        }

    def run_isos_v1_measurement(self):
        from app.measurement.isos_continuous_runner import ContinuousISOSRunner

        def _after(fn, *a):
            self.root.after(0, lambda: fn(*a))

        try:
            cfg = self._isos_build_v1_runner_config()
            self._isos_total_h = float(cfg.get("total_duration_h", 0) or 0)
            self._isos_last_metrics = None
            self.root.after(0, self._isos_reset_metrics_panel)
            self._isos_v1_latest_illum_jv = None
            self._isos_v1_latest_dark_jv  = None
            self.root.after(0, self._isos_v1_redraw)

            callbacks = {
                "on_status":           lambda m: _after(self.isos_status_var.set, m),
                "on_log":              lambda m: _after(self._isos_add_to_log, m),
                "on_shutter":          lambda a: self._isos_v1_shutter_prompt(a),
                "on_scan_done":        lambda meas: _after(self._isos_v1_on_scan_done, meas),
                "on_metrics_updated":  lambda mtr: _after(self._isos_on_metrics_updated, mtr),
                "on_stress_update":    self._isos_v1_stress_update,
                "on_autosave":         self._isos_on_autosave,
                "on_baseline_done":    lambda b: _after(self._isos_l1_on_baseline_done, b),
                "on_failure_detected": lambda r: _after(self._isos_l1_on_failure, r),
                "on_complete":         lambda: _after(self.isos_status_var.set,
                                                      "ISOS-V-1 experiment complete."),
            }
            runner = ContinuousISOSRunner(self.instrument, cfg, callbacks, stop_event)
            self._isos_active_runner = runner
            runner.run()

        except Exception as exc:
            self.root.after(0, lambda e=exc: messagebox.showerror(
                "ISOS-V-1 Measurement Error", str(e)))
            self.root.after(0, lambda e=exc: self.isos_status_var.set(f"Error: {e}"))
        finally:
            self._measurement_running = False
            self._isos_active_runner = None
            stop_event.clear()
            self.root.after(0, lambda: self.isos_start_btn.config(state=NORMAL))
            self.root.after(0, lambda: self.isos_stop_btn.config(state=DISABLED))

    def _isos_v1_shutter_prompt(self, action):
        """Modal shutter prompt fired from the runner thread (blocks until OK)."""
        ev = threading.Event()
        if action == "open":
            title, body = ("Open Shutter", "Open the solar-simulator shutter for "
                           "the illuminated J-V scan, then click OK.")
        else:
            title, body = ("Close Shutter", "Close the shutter — dark stress will "
                           "resume after you click OK.")
        self.root.after(0, lambda: (messagebox.showinfo(title, body), ev.set()))
        ev.wait()

    def _isos_v1_stress_update(self, t_s, v, j, pce):
        """Refresh the live ETA during dark stress (throttled to ≤ 5 Hz)."""
        now = time.time()
        if now - getattr(self, "_isos_v1_last_update", 0.0) < 0.2:
            return
        self._isos_v1_last_update = now
        self.root.after(0, lambda ts=t_s: self._isos_update_eta(ts))

    def _isos_v1_on_scan_done(self, meas):
        v_r, j_r = meas.get("v_rev"), meas.get("j_rev")
        if v_r is not None and j_r is not None:
            self._isos_v1_latest_illum_jv = {"v": list(v_r), "j": list(j_r),
                                             "vmpp": meas.get("Vmpp")}
        dv, dj = meas.get("v_rev_dark"), meas.get("j_rev_dark")
        if dv is not None and dj is not None:
            self._isos_v1_latest_dark_jv = {"v": list(dv), "j": list(dj)}
        try:
            rows = read_isos_log(self.isos_tracking_file)
            self._isos_update_treeview(rows)
        except Exception:
            pass
        self._isos_v1_redraw()
        self._isos_refresh_eta()
        play_completion_sound()

    def _isos_v1_redraw(self):
        axes = self.isos_v1_plot_axes
        if not axes:
            return
        from app.gui.mpp_plot_factory import update_v1_plot
        try:
            rows = read_isos_log(self.isos_tracking_file) if self.isos_tracking_file else []
        except Exception:
            rows = []
        try:
            update_v1_plot(axes, rows,
                           getattr(self, "_isos_v1_latest_illum_jv", None),
                           getattr(self, "_isos_v1_latest_dark_jv", None))
            self._isos_overlay_metrics(axes)
        except Exception:
            pass
