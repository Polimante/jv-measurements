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
    LEFT, RIGHT, BOTH, X, W, END, DISABLED, NORMAL,
)
from datetime import datetime
import pyvisa

PLOT_COLORS = [
    "#2196F3",  # blue
    "#F44336",  # red
    "#4CAF50",  # green
    "#FF9800",  # orange
    "#9C27B0",  # purple
    "#00BCD4",  # cyan
    "#795548",  # brown
    "#607D8B",  # blue-grey
    "#E91E63",  # pink
    "#009688",  # teal
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


class JVMeasurementApp:

    def __init__(self, root):
        self.root            = root
        self.input_widgets   = []
        self.session_data    = []
        # Persistent instrument state — set by open_instrument_dialog(),
        # cleared by disconnect().  run_measurement() uses self.instrument
        # directly with no reconnect overhead.
        self.instrument      = None
        self.instrument_addr = None
        self.instrument_idn  = None

        self.setup_gui()
        self.detector_power_var.trace("w", self.update_illumination)
        self.detector_area_var.trace("w",  self.update_illumination)

    # ── GUI construction ──────────────────────────────────────────────────────

    def setup_gui(self):
        self.root.title(APP_NAME)
        self.root.geometry("1200x880")

        self._build_menubar()

        banner = Frame(self.root, bg="#2c3e50", height=28)
        banner.pack(fill=X, side="top")
        self.instrument_label_var = StringVar(
            value="\u26a0  No instrument connected \u2014 use Instrument \u2192 Connect")
        Label(banner, textvariable=self.instrument_label_var,
              bg="#2c3e50", fg="#ecf0f1",
              font=("Arial", 9, "bold")).pack(side=LEFT, padx=10, pady=4)

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
            ("Cell Area (cm\u00b2):",  "cell_area",  0.07),
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
                "Perovskites typically need 100\u2013500 ms. Corresponds to :SOUR:DEL.")

        Label(inst_frame, text="NPLC:").grid(
            row=1, column=0, sticky=W, padx=5, pady=2)
        self.nplc_var = DoubleVar(value=1.0)
        nplc_entry = Entry(inst_frame, textvariable=self.nplc_var, width=10)
        nplc_entry.grid(row=1, column=1, sticky=W, padx=5, pady=2)
        self.input_widgets.append(nplc_entry)
        ToolTip(nplc_entry,
                "Number of Power Line Cycles per reading (Brazil: 60 Hz \u2192 1 NPLC \u2248 16.7 ms).\n"
                "Higher = less noise, slower. Range: 0.01 \u2013 25.")

        self.four_wire_var = BooleanVar(value=False)
        cb4w = Checkbutton(inst_frame, text="4-Wire (Kelvin) sensing",
                           variable=self.four_wire_var)
        cb4w.grid(row=2, column=0, columnspan=2, sticky=W, padx=5, pady=2)
        ToolTip(cb4w,
                "Enables :SYST:RSEN ON \u2014 eliminates cable resistance from the\n"
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

        Label(illum_frame, text="Detector Area (cm\u00b2):").grid(
            row=1, column=0, sticky=W, padx=5, pady=2)
        self.detector_area_var = DoubleVar(value=1.13)
        ae = Entry(illum_frame, textvariable=self.detector_area_var, width=25)
        ae.grid(row=1, column=1, padx=5, pady=2)
        self.input_widgets.append(ae)

        Label(illum_frame, text="Illumination (mW/cm\u00b2):").grid(
            row=2, column=0, sticky=W, padx=5, pady=2)
        self.illumination_var = StringVar(value="0.00")
        Label(illum_frame, textvariable=self.illumination_var).grid(
            row=2, column=1, sticky=W, padx=5, pady=2)

        # ── Dark J-V Measurement ──────────────────────────────────────────────
        dark_frame = LabelFrame(control_frame, text="Dark J-V Measurement",
                                padx=5, pady=5)
        dark_frame.pack(fill=X, padx=5, pady=5)

        self.dark_jv_var = BooleanVar(value=False)
        dark_cb = Checkbutton(dark_frame,
                              text="Include Dark J-V before illuminated scan",
                              variable=self.dark_jv_var)
        dark_cb.pack(anchor=W, padx=5, pady=2)
        ToolTip(dark_cb,
                "If checked: measures forward + reverse J-V with the shutter CLOSED first,\n"
                "saves [SAMPLE]_Dark_JV_[timestamp].xlsx, then prompts you to open the\n"
                "shutter before the normal illuminated measurement.")

        # ── Light Soaking ─────────────────────────────────────────────────────
        ls_frame = LabelFrame(control_frame, text="Light Soaking",
                              padx=5, pady=5)
        ls_frame.pack(fill=X, padx=5, pady=5)

        self.light_soaking_var = BooleanVar(value=False)
        ls_cb = Checkbutton(ls_frame,
                            text="Enable light soaking before illuminated scan",
                            variable=self.light_soaking_var,
                            command=self._toggle_light_soaking_entry)
        ls_cb.grid(row=0, column=0, columnspan=2, sticky=W, padx=5, pady=2)
        ToolTip(ls_cb,
                "Holds the cell at open circuit under illumination for the set duration\n"
                "before the J-V scan. Allows mobile ions to reach a stabilised state.\n"
                "Recommended: 60 s (n-i-p) to 300 s (p-i-n). Default: 120 s.")

        Label(ls_frame, text="Duration (s):").grid(
            row=1, column=0, sticky=W, padx=5, pady=2)
        self.light_soaking_duration_var = DoubleVar(value=120.0)
        self.light_soaking_entry = Entry(
            ls_frame, textvariable=self.light_soaking_duration_var, width=10,
            state=DISABLED)
        self.light_soaking_entry.grid(row=1, column=1, sticky=W, padx=5, pady=2)
        ToolTip(self.light_soaking_entry,
                "Soaking time in seconds.\n"
                "Literature: ~60 s for n-i-p TiO\u2082; 120\u2013300 s for p-i-n PEDOT:PSS.")

        # ── Control Buttons ───────────────────────────────────────────────────
        btn_frame = Frame(control_frame)
        btn_frame.pack(pady=10)

        self.start_btn = Button(btn_frame, text="Start Measurement",
                                command=self.start_measurement,
                                bg="#4CAF50", fg="white", width=15,
                                state=DISABLED)
        self.start_btn.pack(side=LEFT, padx=5)

        self.stop_btn = Button(btn_frame, text="Stop Measurement",
                               command=self.stop_measurement,
                               bg="#f44336", fg="white", width=15,
                               state=DISABLED)
        self.stop_btn.pack(side=LEFT, padx=5)

        self.plot_btn = Button(btn_frame, text="\U0001f4ca Open Interactive Plot",
                               command=self.open_interactive_plot,
                               bg="#1976D2", fg="white", width=22,
                               state=DISABLED)
        self.plot_btn.pack(side=LEFT, padx=5)

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
        self.log_text.tag_configure("header",     foreground="blue",
                                    font=("Arial", 10, "bold"))
        self.log_text.tag_configure("dark_label", foreground="#5d4e75",
                                    font=("Arial", 10, "bold"))
        self.log_text.tag_configure("separator",  foreground="gray")
        log_frame.grid_rowconfigure(0, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)

        # ── Status Bar ────────────────────────────────────────────────────────
        self.status_var = StringVar(value="Ready \u2014 connect an instrument to begin.")
        Label(control_frame, textvariable=self.status_var,
              bd=1, relief="sunken", anchor=W).pack(side="bottom", fill=X)

        # ── Plot ──────────────────────────────────────────────────────────────
        self.fig, self.ax = plt.subplots(figsize=(8, 5))
        self.ax2 = self.ax.twinx()
        self.ax2.yaxis.set_label_position("right")
        self.ax.set_xlabel("Voltage (V)", fontsize=12, labelpad=10)
        self.ax.set_ylabel("Current Density (mA/cm\u00b2)",
                           fontsize=12, labelpad=10, position=(0, 0.5))
        self.ax2.set_ylabel("Power Density (mW/cm\u00b2)",
                            fontsize=12, labelpad=10, position=(1, 0.5))
        self.canvas = FigureCanvasTkAgg(self.fig, plot_frame)
        self.canvas.get_tk_widget().pack(fill=BOTH, expand=True)
        self.draw_placeholder_plot()

    # ── Menu bar ──────────────────────────────────────────────────────────────

    def _build_menubar(self):
        menubar = Menu(self.root)

        file_menu = Menu(menubar, tearoff=0)
        file_menu.add_command(label="Set Export Folder\u2026",
                              command=self.browse_folder)
        file_menu.add_command(label="Export Measurement Log\u2026",
                              command=self._export_log)
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

        help_menu = Menu(menubar, tearoff=0)
        help_menu.add_command(label="Documentation", command=self._show_help)
        help_menu.add_separator()
        help_menu.add_command(label="About\u2026", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)

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

        self.instrument_menu.entryconfig("Connect\u2026",        state=NORMAL)
        self.instrument_menu.entryconfig("Disconnect",            state=NORMAL)
        self.instrument_menu.entryconfig("Send *IDN? query",      state=NORMAL)

    def disconnect_instrument(self):
        self._close_instrument()
        self.instrument_label_var.set(
            "\u26a0  No instrument connected \u2014 use Instrument \u2192 Connect")
        self.status_var.set("Instrument disconnected.")
        self.start_btn.config(state=DISABLED)
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
        if not messagebox.askokcancel(
            "Clear Session Data",
            "This will clear all measurement data from the current session.\n"
            "The exported Excel files are not affected.\n\n"
            "Continue?"
        ):
            return
        self.session_data = []
        self.plot_btn.config(state=DISABLED)

    def open_interactive_plot(self):
        try:
            import plotly.graph_objects as go
        except ImportError:
            messagebox.showerror(
                "Missing dependency",
                "plotly is not installed.\nRun: pip install plotly"
            )
            return

        if not self.session_data:
            return

        fig = go.Figure()

        for entry in self.session_data:
            col   = entry["color"]
            name  = entry["sample_name"]
            v_f   = entry["v_fwd"]
            j_f   = entry["j_fwd"]
            v_r   = entry["v_rev"]
            j_r   = entry["j_rev"]
            p_f   = v_f * j_f
            p_r   = v_r * j_r

            marker_fwd = dict(symbol="circle", size=6,
                              color=col, line=dict(color=col, width=1))
            marker_rev = dict(symbol="circle", size=6,
                              color=col, line=dict(color=col, width=1))

            jv_hover = (
                "<b>V:</b> %{x:.3f} V<br>"
                "<b>J:</b> %{y:.3f} mA/cm\u00b2"
                "<extra>%{fullData.name}</extra>"
            )
            pw_hover = (
                "<b>V:</b> %{x:.3f} V<br>"
                "<b>P:</b> %{y:.3f} mW/cm\u00b2"
                "<extra>%{fullData.name}</extra>"
            )

            # Forward J-V
            fig.add_trace(go.Scatter(
                x=v_f, y=j_f,
                mode="lines+markers",
                name=f"{name} \u2014 Fwd J-V",
                line=dict(color=col, dash="solid", width=2),
                marker=marker_fwd,
                hovertemplate=jv_hover,
                yaxis="y1",
            ))
            # Reverse J-V
            fig.add_trace(go.Scatter(
                x=v_r, y=j_r,
                mode="lines+markers",
                name=f"{name} \u2014 Rev J-V",
                line=dict(color=col, dash="dash", width=2),
                marker=marker_rev,
                hovertemplate=jv_hover,
                yaxis="y1",
            ))
            # Forward Power
            fig.add_trace(go.Scatter(
                x=v_f, y=p_f,
                mode="lines+markers",
                name=f"{name} \u2014 Fwd P",
                line=dict(color=col, dash="solid", width=1.5),
                marker=dict(symbol="circle", size=5,
                            color=col, line=dict(color=col, width=1)),
                hovertemplate=pw_hover,
                yaxis="y2",
                opacity=0.65,
            ))
            # Reverse Power
            fig.add_trace(go.Scatter(
                x=v_r, y=p_r,
                mode="lines+markers",
                name=f"{name} \u2014 Rev P",
                line=dict(color=col, dash="dash", width=1.5),
                marker=dict(symbol="circle", size=5,
                            color=col, line=dict(color=col, width=1)),
                hovertemplate=pw_hover,
                yaxis="y2",
                opacity=0.65,
            ))

        last = self.session_data[-1]
        annotation_text = (
            f"<b>Last: {last['sample_name']}</b><br>"
            f"Illumination: {last['illumination']:.1f} mW/cm\u00b2<br>"
            f"<b>Reverse scan:</b><br>"
            f"\u00a0\u00a0Voc: {last['Voc_rev']:.3f} V<br>"
            f"\u00a0\u00a0Jsc: {last['Jsc_rev']:.3f} mA/cm\u00b2<br>"
            f"\u00a0\u00a0FF: {last['FF_rev']:.1f} %<br>"
            f"\u00a0\u00a0PCE: {last['PCE_rev']:.2f} %<br>"
            f"<b>HI: {last['HI']:+.1f} %</b>"
        )

        fig.add_annotation(
            xref="paper", yref="paper",
            x=0.98, y=0.98,
            xanchor="right", yanchor="top",
            text=annotation_text,
            showarrow=False,
            font=dict(size=11, family="Arial"),
            bgcolor="rgba(255,255,255,0.92)",
            bordercolor="#BDBDBD",
            borderwidth=1,
            borderpad=8,
            align="left",
        )

        fig.update_layout(
            title=dict(
                text="J-V Characteristics \u2014 Session Overview",
                font=dict(size=16, family="Arial"),
            ),
            xaxis=dict(
                title="Voltage (V)",
                showgrid=True,
                gridcolor="#E0E0E0",
                gridwidth=1,
                zeroline=True,
                zerolinecolor="#BDBDBD",
                zerolinewidth=1.5,
                ticks="outside",
                mirror=True,
                showline=True,
                linecolor="#424242",
            ),
            yaxis=dict(
                title="Current Density (mA/cm\u00b2)",
                showgrid=True,
                gridcolor="#E0E0E0",
                gridwidth=1,
                ticks="outside",
                mirror=False,
                showline=True,
                linecolor="#424242",
            ),
            yaxis2=dict(
                title="Power Density (mW/cm\u00b2)",
                overlaying="y",
                side="right",
                showgrid=False,
                ticks="outside",
                showline=True,
                linecolor="#424242",
            ),
            plot_bgcolor="white",
            paper_bgcolor="white",
            legend=dict(
                bgcolor="rgba(255,255,255,0.9)",
                bordercolor="#E0E0E0",
                borderwidth=1,
                font=dict(size=11),
                itemclick="toggle",
                itemdoubleclick="toggleothers",
            ),
            hovermode="closest",
            font=dict(family="Arial, sans-serif", size=12, color="#212121"),
            margin=dict(l=70, r=80, t=60, b=60),
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False,
            prefix="jv_plot_", encoding="utf-8"
        ) as f:
            fig.write_html(f.name, include_plotlyjs="cdn")
            temp_path = f.name

        webbrowser.open(f"file:///{os.path.abspath(temp_path).replace(chr(92), '/')}")

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
        self.canvas.draw()

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

    def update_results_display(self, fwd, rev, HI, v_f, j_f, v_r, j_r):
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
            "Voc_fwd": Voc_f, "Jsc_fwd": Jsc_f, "FF_fwd": FF_f, "PCE_fwd": PCE_f,
            "Voc_rev": Voc_r, "Jsc_rev": Jsc_r, "FF_rev": FF_r, "PCE_rev": PCE_r,
            "HI": HI,
            "color": color,
        })
        self.plot_btn.config(state=NORMAL)

        pb = (f"Pre-bias: {self.prebias_voltage_var.get():.2f} V "
              f"for {self.prebias_duration_var.get():.1f} s\n"
              if self.prebias_enabled_var.get() else "")
        ls = (f"Light soaking: {self.light_soaking_duration_var.get():.0f} s\n"
              if self.light_soaking_var.get() else "")
        n = len(self.session_data)
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
            f"Session total: {n} measurement(s) \u2014 "
            f"click \"\U0001f4ca Open Interactive Plot\" to view all\n"
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

            self.update_results_display(fwd, rev, HI, v_f, j_f, v_r, j_r)
            self.update_plot(v_f, j_f, v_r, j_r, v_f * j_f, v_r * j_r,
                             v_df=v_df, j_df=j_df, v_dr=v_dr, j_dr=j_dr)
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
            self.toggle_controls(True)
            stop_event.clear()

    # ── Plot ──────────────────────────────────────────────────────────────────

    def update_plot(self, v_f, j_f, v_r, j_r, p_f, p_r,
                    v_df=None, j_df=None, v_dr=None, j_dr=None):
        has_dark = v_df is not None

        self.ax.clear()
        self.ax2.clear()
        self.ax2.yaxis.set_label_position("right")

        self.ax.set_xlim(0, 1.1)

        if has_dark:
            self.ax.set_ylim(-20, 30)
            self.ax2.set_ylim(-20, 30)
            # Reference line at J = 0 to separate dark and illuminated regions
            self.ax.axhline(0, color="#aaaaaa", linewidth=0.8, linestyle="--", zorder=1)
        else:
            self.ax.set_ylim(0, 30)
            self.ax2.set_ylim(0, 30)

        lines = []

        # ── Dark curves (plotted first so illuminated curves sit on top) ──────
        if has_dark:
            cdf, = self.ax.plot(v_df, j_df, color="#7b8cbe", linestyle="--",
                                linewidth=1.4, label="Dark Forward J")
            cdr, = self.ax.plot(v_dr, j_dr, color="#be7b7b", linestyle="--",
                                linewidth=1.4, label="Dark Reverse J")
            lines += [cdf, cdr]

        # ── Illuminated curves ────────────────────────────────────────────────
        cf, = self.ax.plot(v_f, j_f, "b-",  linewidth=1.8, label="Forward J (light)")
        cr, = self.ax.plot(v_r, j_r, "r-",  linewidth=1.8, label="Reverse J (light)")
        pf, = self.ax2.plot(v_f, p_f, "b--", linewidth=1.2, alpha=0.7,
                             label="Forward Power")
        pr, = self.ax2.plot(v_r, p_r, "r--", linewidth=1.2, alpha=0.7,
                             label="Reverse Power")
        lines += [cf, cr, pf, pr]

        name = self.sample_name_var.get() or "Unnamed Sample"
        self.ax.set_title(f"{name}\nJ-V Characteristics",
                          fontsize=14, fontweight="bold", pad=20)
        self.ax.legend(lines, [l.get_label() for l in lines],
                       loc="upper right", frameon=True, framealpha=0.9,
                       fontsize=9)
        self.ax.set_xlabel("Voltage (V)", fontsize=12, labelpad=10)
        self.ax.set_ylabel("Current Density (mA/cm\u00b2)",
                           fontsize=12, labelpad=10, position=(0, 0.5))
        self.ax2.set_ylabel("Power Density (mW/cm\u00b2)",
                            fontsize=12, labelpad=10, position=(1, 0.5))
        self.fig.subplots_adjust(left=0.15, right=0.85, top=0.85)
        self.ax.grid(True)
        self.canvas.draw()
