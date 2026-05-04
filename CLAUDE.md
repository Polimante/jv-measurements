# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the application

```bash
python main.py
```

Tests live in `tests/` and run hardware-free. The application itself is Windows-only (`winsound`, VISA drivers).

```bash
python -m pytest tests/          # run all tests
python -m pytest tests/test_isos_exporter.py -v   # example: single file
```

## Dependencies

```bash
pip install -r requirements.txt
```

`playsound` is optional (falls back to `winsound.Beep`). The MP3 path in `app/utils/sound.py` is hardcoded to a specific machine path — update it when deploying on a new computer.

## Architecture

```
main.py                        # Entry point — creates Tk root, instantiates JVMeasurementApp
app/
  gui/
    main_window.py             # JVMeasurementApp — entire GUI, measurement orchestration
    instrument_dialog.py       # Modal VISA scanner dialog (Toplevel)
    data_viewer.py             # DataViewerWindow — read-only viewer for saved xlsx files
    tooltip.py                 # ToolTip hover widget
  instrument/
    keithley.py                # Stateless SCPI helpers (init, prebias, light_soaking, scan)
  measurement/
    analysis.py                # Pure math: calculate_jv_parameters, calculate_resistances, HI
    mpp_tracker.py             # MPPTracker — JV-SPO cycling protocol (Alt+F5)
    pure_mpp_tracker.py        # PureMPPTracker — Perturb-and-Observe continuous tracking (Alt+F6)
  io/
    exporter.py                # save_data() and save_dark_jv_data() → xlsxwriter
    isos_exporter.py           # ISOS-D-1 tracking workbook (create / append / read)
  utils/
    constants.py               # APP_NAME, APP_VERSION, global stop_event (threading.Event)
    sound.py                   # play_completion_sound() with playsound / winsound fallback
    updater.py                 # check_for_updates() — fetches version.json from GitHub
docs/
  help.html                    # Standalone HTML help opened via webbrowser.open()
build_installer/
  setup.iss                    # Inno Setup 6 script → dist/JV-Measurements-Setup-vX.X.X.exe
version.json                   # Machine-readable version + release notes (read by updater.py)
```

### Key architectural patterns

**Measurement thread**: `run_measurement()` runs in a daemon thread. GUI updates go through `self.status_var.set()` (safe from any thread). Dialogs that must appear mid-measurement (e.g. "open shutter") are posted to the main thread via `root.after(0, callback)` + `threading.Event.wait()`.

**Stop mechanism**: `stop_event` (module-level `threading.Event` in `constants.py`) is shared between the GUI thread and measurement thread. All loops in `keithley.py` check it. Call `stop_event.clear()` in the `finally` block after every measurement.

**Session data**: `self.session_data` is a list of dicts accumulated during the application session. Each entry holds raw arrays (`v_fwd`, `j_fwd`, `v_rev`, `j_rev`, `power_fwd`, `power_rev`, optional dark arrays), all PV parameters, and a color from `PLOT_COLORS`. `update_plot()` redraws from this list on every call — it does not accept data arguments.

**Plot interactivity**: The Matplotlib canvas uses `button_press_event` (not `pick_event`) for legend toggling because `mplcursors` with `hover=True` interferes with `pick_event` in the Tkinter backend. The active connection id is stored in `self._pick_cid` and disconnected before re-registering on each redraw.

### Sign convention

```python
current = -float(inst.query(":READ?").split(",")[1])
```

The negation makes photocurrent positive in the power-generating quadrant (standard solar cell convention). Dark forward-bias current ends up negative after this transform.

### Excel file layout

Two file types, distinguished by sheet name:
- `MeasurementData` — illuminated J-V; row 1 = headers, row 2 = scalar params, row 3+ = V/J/P arrays
- `Dark_JV_Data` — dark J-V; same layout but only V/J (no power, no PV params)

Column mapping is documented in `data_viewer.py::_read_illuminated()` and `_read_dark()`. When adding new scalar columns to `exporter.py`, update the column indices in both reader functions and in `data_viewer.py::_populate_metadata()`.

### PV parameter definitions

- **Voc**: voltage at first zero-crossing of current
- **Jsc**: current at the voltage point closest to 0 V
- **FF**: `(Vm × Jm) / (Voc × Jsc) × 100`
- **PCE**: `(Voc × Jsc × FF) / illumination`
- **Rs**: inverse slope of J-V near Voc (in Ω·cm²)
- **Rsh**: `abs(1000 / slope)` near V=0 — always positive
- **HI**: `(PCE_rev − PCE_fwd) / PCE_rev × 100` (Kim & Park 2014, positive = normal hysteresis)

### Measurement modes and keyboard shortcuts

| Mode | Shortcut | Class / entry point |
|---|---|---|
| J-V Measurement | Alt+F2 | `run_measurement()` in `main_window.py` |
| MPP Tracking (JV-SPO) | Alt+F3 | `MPPTracker` in `mpp_tracker.py` |
| Stabilized Current | Alt+F5 | inline in `main_window.py` |
| Pure MPP Tracking (P&O) | Alt+F6 | `PureMPPTracker` in `pure_mpp_tracker.py` |
| Data Curation | Alt+F7 | `switch_mode("curation")` in `main_window.py` |
| ISOS Protocols (D-1) | Alt+F8 | `run_isos_d1_measurement()` in `main_window.py` |

Mode panels are shown/hidden by `switch_mode()` using `pack()`/`pack_forget()`. The active panel name is stored in `self._current_mode` (`"jv"`, `"mpp"`, `"stab"`, `"pure_mpp"`).

### MPP Tracking (JV-SPO cycling) — `mpp_tracker.py`

Each cycle: optional forward scan → optional pre-bias → reverse scan → SPO phase at Vmpp. The instrument output stays ON for the entire experiment (turned off only in the `finally` block). Voltage is set to Vmpp **inside** `_run_spo()` with a 100 ms settling sleep before the first reading.

SPO plot uses **accumulated SPO-only time** on the X-axis (J-V scan time is excluded). When `cycle_n` changes, a `None` is inserted in the plot arrays to break the line, and a dashed vertical axvline marks the cycle boundary. This prevents false connecting lines across J-V scan gaps.

### Pure MPP Tracking (P&O) — `pure_mpp_tracker.py`

Runs one initial reverse J-V scan (using `scan_start_v`, `scan_end_v`, `scan_step_v`) to find the real Vmpp, then enters a continuous P&O loop. `t_start` is set after the initial scan so t=0 on the plot corresponds to the start of P&O. Direction reverses whenever power drops. The inter-step sleep is broken into 50 ms chunks so `stop_event` is checked frequently. Callbacks: `on_status`, `on_update(t, v, j, pce)`, `on_jv_done(v_arr, j_arr, vmpp)`, `on_complete`.

### ISOS Protocols (D-1) — `isos_exporter.py` + `main_window.py`

ISOS-D-1 is a light-stability protocol: the cell is measured periodically under illumination while kept at open-circuit between measurements. Each run performs the same sequence as J-V mode (optional dark J-V → shutter prompt → light soaking → fwd scan → pre-bias → rev scan) and then appends one row to a cumulative tracking workbook.

**Tracking file layout** (openpyxl):
- `ISOS_D1_Config` — experiment metadata (one header row + one data row)
- `ISOS_D1_Log` — one row per measurement; columns defined in `_LOG_HEADERS`
- `JV_Fwd_#NNN_tXXXXh` / `JV_Rev_#NNN_tXXXXh` — raw V/J data per measurement

`PCE_fwd_norm` and `PCE_rev_norm` are always relative to the **first** measurement (`t₀`) — stored as a **ratio** (1.0 = baseline, not percent; e.g. 0.80 means T80). `elapsed_h` is wall-clock time since `t0_timestamp` stored in the config sheet. Individual backup files are also saved via `save_data()` (standard illuminated J-V format).

The GUI panel (Alt+F8) has three tabs: Experiment (sample name, folder, "New / Load"), Measurement Parameters (voltage range, illumination, scan settings), and Conditions (pre-bias, light soaking). The panel shows a live treeview of the log and two plots: PCE stability (normalized, top) and the latest J-V curves (bottom).

### Measurement sequence (J-V mode)

```
[Dark J-V fwd + rev]  →  [prompt: open shutter]  →  [light soaking timer]
→  illuminated fwd scan  →  [pre-bias hold]  →  illuminated rev scan
```

Pre-bias is applied **between** the forward and reverse illuminated scans only.

### Scan rate mode

The Instrument Settings tab has a toggle between two modes:
- **Advanced** (default): delay entered manually; scan rate is a read-only calculated field.
- **Scan rate**: user enters target mV/s; delay is auto-calculated as `(step_mV / rate × 1000) − NPLC × 16.7 − 50 ms` (clamped ≥ 0). `⚠` is appended to the displayed rate when delay was clamped to 0.

Both modes use 50 ms as the GPIB overhead constant so they round-trip consistently.

### Auto-update system

`app/utils/updater.py` fetches `version.json` from the GitHub raw URL in a background daemon thread. Called silently 3 s after startup (`root.after(3000, ...)`), and explicitly via **Help → Check for Updates**. Network/import failures are silenced in the background check and reported in the explicit check.

### Distribution

- `launcher.vbs` — runs `pythonw.exe main.py` with window style 0 (no CMD window). Used by installer shortcuts.
- `launcher.bat` — runs with a visible CMD window; kept for developer use.
- `install.bat` — runs `pip install -r requirements.txt`; executed post-install by the Inno Setup wizard.
- To build the installer: open `build_installer/setup.iss` in Inno Setup 6 and compile. Output goes to `dist/`.

### Releasing a new version

1. Update `version.json` (version, release_date, release_notes)
2. Update `CHANGELOG.md`
3. Update `APP_VERSION` in `app/utils/constants.py`
4. Commit, push, create a GitHub Release tagged `vX.Y.Z`
5. Optionally attach the compiled installer `.exe` to the release

### Matplotlib layout note

`mpp_fig` and `pmpp_fig` use `constrained_layout=True` (set at figure creation) because they contain `GridSpec` subplots with column-spanning axes. Do **not** call `tight_layout()` on these figures — it conflicts with `constrained_layout` and produces warnings. `stab_fig` does not use `constrained_layout` and still uses `tight_layout()` normally.
