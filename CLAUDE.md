# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the application

```bash
python main.py
```

There are no tests and no build step. The application is Windows-only (`winsound`, VISA drivers).

## Dependencies

```bash
pip install pyvisa matplotlib numpy xlsxwriter mplcursors openpyxl
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
  utils/
    constants.py               # APP_NAME, APP_VERSION, global stop_event (threading.Event)
    sound.py                   # play_completion_sound() with playsound / winsound fallback
docs/
  help.html                    # Standalone HTML help opened via webbrowser.open()
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
| J-V Measurement | Alt+F3 | `run_measurement()` in `main_window.py` |
| MPP Tracking (JV-SPO) | Alt+F5 | `MPPTracker` in `mpp_tracker.py` |
| Stabilized Current | Alt+F4 | inline in `main_window.py` |
| Pure MPP Tracking (P&O) | Alt+F6 | `PureMPPTracker` in `pure_mpp_tracker.py` |

Mode panels are shown/hidden by `switch_mode()` using `pack()`/`pack_forget()`. The active panel name is stored in `self._current_mode` (`"jv"`, `"mpp"`, `"stab"`, `"pure_mpp"`).

### MPP Tracking (JV-SPO cycling) — `mpp_tracker.py`

Each cycle: optional forward scan → optional pre-bias → reverse scan → SPO phase at Vmpp. The instrument output stays ON for the entire experiment (turned off only in the `finally` block). Voltage is set to Vmpp **inside** `_run_spo()` with a 100 ms settling sleep before the first reading.

SPO plot uses **accumulated SPO-only time** on the X-axis (J-V scan time is excluded). When `cycle_n` changes, a `None` is inserted in the plot arrays to break the line, and a dashed vertical axvline marks the cycle boundary. This prevents false connecting lines across J-V scan gaps.

### Pure MPP Tracking (P&O) — `pure_mpp_tracker.py`

Runs one initial reverse J-V scan (using `scan_start_v`, `scan_end_v`, `scan_step_v`) to find the real Vmpp, then enters a continuous P&O loop. `t_start` is set after the initial scan so t=0 on the plot corresponds to the start of P&O. Direction reverses whenever power drops. The inter-step sleep is broken into 50 ms chunks so `stop_event` is checked frequently. Callbacks: `on_status`, `on_update(t, v, j, pce)`, `on_jv_done(v_arr, j_arr, vmpp)`, `on_complete`.

### Measurement sequence (J-V mode)

```
[Dark J-V fwd + rev]  →  [prompt: open shutter]  →  [light soaking timer]
→  illuminated fwd scan  →  [pre-bias hold]  →  illuminated rev scan
```

Pre-bias is applied **between** the forward and reverse illuminated scans only.
