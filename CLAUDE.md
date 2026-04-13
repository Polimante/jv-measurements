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

### Measurement sequence

```
[Dark J-V fwd + rev]  →  [prompt: open shutter]  →  [light soaking timer]
→  illuminated fwd scan  →  [pre-bias hold]  →  illuminated rev scan
```

Pre-bias is applied **between** the forward and reverse illuminated scans only.
