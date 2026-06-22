# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the application

```bash
python main.py
```

Tests live in `tests/` and run hardware-free. The application itself is Windows-only (`winsound`, VISA drivers).

```bash
python -m pytest tests/          # run all tests
python -m pytest tests/test_isos_tracking.py -v   # example: single file
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
    isos_protocols.py          # ProtocolSpec registry (PROTOCOLS, get_protocol, list_protocol_*)
  io/
    exporter.py                # save_data() and save_dark_jv_data() → xlsxwriter
    isos_tracking.py           # Generic ISOS tracking workbook (create / append / read) + D-1 shims
    isos_autosave.py           # CSVStream durable autosave + consolidate_csv_into_xlsx()
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

### Two instrument abstractions (deliberate split)

The project intentionally keeps **two** ways of talking to the source-measure unit:

1. **Raw SCPI** (`app/instrument/keithley.py` + `inst.write`/`inst.query`) — used by
   **all real-measurement code**: `run_measurement`, `MPPTracker`, `PureMPPTracker`,
   the ISOS-D-1 routine, and `ContinuousISOSRunner`. `self.instrument` is a live
   pyvisa resource. This is the only path that ever drives physical hardware.
2. **`SourceMeter` ABC** (`app/instrument/base.py`, implemented by
   `app/instrument/dummy_smu.py`) — a higher-level interface (`set_voltage`,
   `read_current_density`, `measure_scan`…). Used **only** by tests and the
   synthetic-data / simulation workflows. There is no `SourceMeter`-based path to
   real hardware.

**These two never meet in production, and that is on purpose.** Converging the
measurement code onto the ABC would mean rewriting the most safety-critical code
(it controls a real instrument wired to real cells) with no way to regression-test
it in CI. The payoff is only consistency, so we do not do it.

Consequence for tests: SCPI-based components are tested with SCPI-shaped fakes
(`MagicMock` with a `.query` side-effect, or `tests/_isos_runner_helpers.FakeKeithley`),
**not** with `DummySMU`. Revisit this split only if a concrete need arises to run a
real measurement mode (e.g. ISOS-L-1/V-1) through `DummySMU` in the live GUI.

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

### Single-diode model fit — `SingleDiodeFit`

`fit_single_diode(v, j, T=298.15)` in `app/measurement/analysis.py` fits the 5-parameter implicit equation:

    J = J_ph - J0·(exp((V + J·Rs·1e-3)/(n·Vt)) - 1) - (V + J·Rs·1e-3)·1e3/Rsh

Units follow CLAUDE.md convention (mA/cm², Ω·cm²). The solver uses `scipy.optimize.brentq` inside `scipy.optimize.curve_fit`. Returns a `SingleDiodeFit` dataclass with:

| Field | Description |
|---|---|
| `parameters` | dict: J_ph, J0, n, Rs, Rsh |
| `std_errors` | dict: standard errors from covariance matrix |
| `r_squared` | goodness-of-fit R² |
| `rmse` | root mean square error (mA/cm²) |
| `rmse_log` | RMSE in log10\|J\| space (robust across dynamic range) |
| `converged` | bool — False if curve_fit raised |
| `bounded_parameters` | list of parameter names that hit a bound (potential warning) |
| `temperature_K` | T used for Vt = kT/q |

**Auto-fit (post-measurement)**: enabled by the "Auto single-diode fit" checkbox (Instrument tab, default OFF). Runs on the reverse scan in the measurement thread. Result is stored in `session_data[-1]["single_diode_fit"]` and written as a `SingleDiodeFit` sheet in the xlsx file.

**Interactive fit (Data Viewer)**: the "Single-Diode Fit" button (top-right bar, enabled only for illuminated J-V files) opens `SingleDiodeFitWindow` — an overlay plot, residuals plot, editable temperature and bounds, Refit, Export CSV, and Save-to-file buttons.

When adding new code that uses J0 or n, update both the local-derivative methods and the fit's `_estimate_initial_guess` helper in `analysis.py`.

### Measurement modes and keyboard shortcuts

| Mode | Shortcut | Class / entry point |
|---|---|---|
| J-V Measurement | Alt+F2 | `run_measurement()` in `main_window.py` |
| JV Light Intensity | Alt+F3 | `LightIntensityJVMeasurement` in `light_intensity_jv.py` |
| MPP Tracking (JV-SPO) | Alt+F5 | `MPPTracker` in `mpp_tracker.py` |
| Stabilized Current | Alt+F6 | inline in `main_window.py` |
| Pure MPP Tracking (P&O) | Alt+F7 | `PureMPPTracker` in `pure_mpp_tracker.py` |
| Data Curation | Alt+F8 | `switch_mode("curation")` in `main_window.py` |
| ISOS Protocols (D-1 / L-1 / V-1) | Alt+F9 | `run_isos_d1_measurement()` (D-1) / `ContinuousISOSRunner` (L-1, V-1) in `main_window.py` |

Mode panels are shown/hidden by `switch_mode()` using `pack()`/`pack_forget()`. The active panel name is stored in `self._current_mode` (`"jv"`, `"mpp"`, `"stab"`, `"pure_mpp"`).

### MPP Tracking (JV-SPO cycling) — `mpp_tracker.py`

Each cycle: optional forward scan → optional pre-bias → reverse scan → SPO phase at Vmpp. The instrument output stays ON for the entire experiment (turned off only in the `finally` block). Voltage is set to Vmpp **inside** `_run_spo()` with a 100 ms settling sleep before the first reading.

SPO plot uses **accumulated SPO-only time** on the X-axis (J-V scan time is excluded). When `cycle_n` changes, a `None` is inserted in the plot arrays to break the line, and a dashed vertical axvline marks the cycle boundary. This prevents false connecting lines across J-V scan gaps.

### Pure MPP Tracking (P&O) — `pure_mpp_tracker.py`

Runs one initial reverse J-V scan (using `scan_start_v`, `scan_end_v`, `scan_step_v`) to find the real Vmpp, then enters a continuous P&O loop. `t_start` is set after the initial scan so t=0 on the plot corresponds to the start of P&O. Direction reverses whenever power drops. The inter-step sleep is broken into 50 ms chunks so `stop_event` is checked frequently. Callbacks: `on_status`, `on_update(t, v, j, pce)`, `on_jv_done(v_arr, j_arr, vmpp)`, `on_complete`.

### ISOS Protocols — Architecture

The ISOS Protocols mode (Alt+F9) supports 3 variants registered in `app/measurement/isos_protocols.py` (a frozen `ProtocolSpec` dataclass + `PROTOCOLS` list + `get_protocol(id)` / `list_protocol_ids()` / `list_protocol_labels()`; the GUI dropdown is populated from `list_protocol_labels()`):
- **ISOS-D-1** — manual-trigger dark storage. Cell kept at open-circuit in the dark between periodic illuminated J-V characterisations. Runs via `run_isos_d1_measurement()` in `main_window.py` (same sequence as J-V mode), appending one row per trigger to the tracking workbook.
- **ISOS-L-1** — continuous light soaking at ambient T, OC or MPP load.
- **ISOS-V-1** — dark electrical-bias stress, with periodic dark + illuminated J-V.

Continuous protocols (L-1 / V-1) run via `ContinuousISOSRunner` (`isos_continuous_runner.py`), which drives the raw-SCPI interface like the other trackers and orchestrates: optional dark equilibration → baseline conditioning (N scans, first discarded, median PCE₀) → main loop of [stress → pre-scan relaxation → (optional dark J-V) → illuminated J-V → SPO → append → failure check]. Time is read through an injectable clock so tests run instantly. L-1 stresses with light (OC `read_voc` or MPP perturb-and-observe); V-1 stresses in the dark (hold setpoint) and fires shutter prompts around each illuminated scan.

### Tracking File Format

`isos_tracking.py` holds the generic, protocol-agnostic API — `create_isos_tracking_file`, `append_isos_measurement`, `append_curve_sheet`, `append_dark_jv_sheet`, `append_log_sheet_row`, `append_metrics_row`, and the `read_isos_*` readers — plus thin `*_isos_d1_*` shims that forward with `protocol_id="ISOS-D-1"`. The config sheet carries `protocol_id` in column 1; readers fall back to the legacy `ISOS_D1_*` sheet names so old D-1 files still open. Sheets (openpyxl):
- `ISOS_Config` — experiment metadata (header row + one data row). `_CFG_KEYS` order is fixed; trailing columns are nullable.
- `ISOS_Log` — one row per illuminated scan; columns in `_LOG_HEADERS` (`_LOG_HEADERS_CORE` positions 1-22 are fixed, `_LOG_EXTRA` appends the continuous-protocol columns).
- `JV_#NNN_tXXXXh` — illuminated per-scan sheet (cols A–F = V/J/P fwd & rev + scalars + embedded chart), MeasurementData-compatible.
- `JV_Dark_#NNN_tXXXXh` — dark per-scan sheet (V-1) with dark diagnostics (`n_dark_rev`, `J_s_dark_rev`, `R_s/R_sh_dark`, `HI_dark`, `I_dark`).
- `SPO_#NNN_tXXXXh` — stabilized-power-output trace (Time, PCE) after each scan.
- `Baseline_#NN` — each baseline-conditioning scan.
- `Equilibration_log` — one row per dark-equilibration phase.
- `Stress_<HHMMSS>` — the consolidated stress autosave (see below).
- `ISOS_Metrics` — one row per scan, the rolling-best stability metrics (T80, T95, T_S, PCE_S, Ts80, n_1000…). Written by `append_isos_measurement()` when the meas dict carries a `stability_metrics` object; `append_metrics_row()` / `read_isos_metrics()` are standalone helpers.

`PCE_fwd_norm` / `PCE_rev_norm` are ratios relative to the **first** measurement (1.0 = baseline; 0.80 means T80). `elapsed_h` is wall-clock since `t0_timestamp`. Individual backup files are also saved via `save_data()` / `save_dark_jv_data()`.

The GUI panel has Experiment / Measurement-Parameters / Conditions tabs plus protocol sub-frames (L-1, V-1). It shows a live treeview of the log, a Stability Metrics panel (live readout + Auto/Manual/Fallback burn-in radios + "Show overlay on plot" toggle + "Copy metrics to clipboard"), an ETA + autosave-dot status bar, and a protocol-specific plot panel (`mpp_plot_factory.py`).

### Autosave & Crash Recovery

During continuous stress, every sample is streamed to a durable `*_stress_inprogress_*.csv` (`isos_autosave.CSVStream`, fsync'd per row). On a clean finish the runner's `_cleanup()` folds the **whole** CSV into the workbook as a single `Stress_<HHMMSS>` sheet via `consolidate_csv_into_xlsx()` and renames the CSV `.consumed`. Consolidation is **end-of-run / on-recovery**, not per scan boundary — one CSV ⇒ one Stress sheet (per-segment consolidation is a deferred idea, intentionally not implemented, because the one-CSV-per-run model is what makes recovery tractable). The runner emits `on_autosave(ok, error)` per flush so the GUI can show a green/red/gray health dot.

On startup `JVMeasurementApp._scan_for_inprogress_isos` scans every folder in `~/.jv-measurement/recent_folders.json` (updated by `_isos_new_experiment` / `_isos_load_experiment`) for orphan in-progress CSVs whose paired tracking xlsx still exists, and offers a recovery dialog (Consolidate all / Discard all / Decide individually). Detection lives in the Tk-free `app/io/isos_recovery.py` (`load_recent_folders`, `add_recent_folder`, `find_orphan_stress_csvs`, `consolidate_orphan`, `discard_orphan`) so it is unit-testable; "Discard" renames the CSV `.discarded` (never deletes).

### Stability Metrics — `stability_metrics.py`

`app/measurement/stability_metrics.py` is pure math: `compute_stability_metrics(times_h, pce_values, pce_0, …)` returns a `StabilityMetrics` dataclass with **T80/T95** (first time `PCE/PCE₀ ≤ 0.80 / 0.95`, sub-sample interpolated), **T_S** (burn-in end — most-positive 2nd derivative of Savitzky-Golay-smoothed normalized PCE, with auto/manual/fallback modes), **PCE_S** (mean PCE in a window around T_S), **Ts80** (time *from* T_S to `0.80·PCE_S`), and **n_1000** (`PCE(1000 h)/PCE(0)`, linearly extrapolated from the stable region with a confidence std-error when the run is < 1000 h). Unreached metrics are `None` with `*_reached=False`.

The canonical PCE column is `PCE_SPO` (stabilized power output) when available, else `PCE_rev`. `ContinuousISOSRunner` accumulates the per-scan canonical PCE, recomputes after every scan (runner thread only — never the GUI thread), stuffs the result into the meas dict for the `ISOS_Metrics` sheet, and forwards it via the `on_metrics_updated(metrics)` callback. The GUI's Stability Metrics panel (live ✓/—/`> experiment` readout + Auto/Manual/Fallback burn-in radios) calls `runner.recompute_metrics()` on a burn-in-mode change for an instant recompute + redraw. `mpp_plot_factory.overlay_stability_metrics(ax_pce, metrics, total_h)` draws the threshold/marker lines on a normalized PCE axis; the "Show overlay on plot" toggle gates `_isos_overlay_metrics` for clean screenshots, and "Copy metrics to clipboard" exports a formatted text block.

### Future ISOS Protocols

D-2/D-3 (high T, damp heat), L-2/L-3 (T-controlled light soaking), LC-* (light cycling), T-* (thermal cycling), V-2/V-3 (alternative bias loads), and O-* (outdoor) are all unimplemented — they require additional hardware (thermal chamber, motorized shutter, outdoor enclosure, etc.). Do not register them in `isos_protocols.py` without the corresponding runner support.

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

### Continuous Integration

Two GitHub Actions workflows live in `.github/workflows/`:

| Workflow file | Runner | Python | Marker filter |
|---|---|---|---|
| `tests.yml` | `ubuntu-latest` | 3.10, 3.11, 3.12 (matrix) | `not windows_only and not gui` |
| `tests-windows.yml` | `windows-latest` | 3.11 | `not gui` |

Both workflows trigger on every `push` and `pull_request` to any branch.

**Pytest markers** (defined in `tests/conftest.py` and registered in `pyproject.toml`):

- `windows_only` — test requires Windows; auto-skipped on Linux/macOS.
- `gui` — test requires a live Tk display; auto-skipped in CI and on headless Linux (no `DISPLAY`).

Currently no tests carry `windows_only` (all hardware is mocked). Two tests carry `gui`:
`test_data_viewer_fit_button.py` and `test_data_viewer_dark_ideality_button.py`.

**Run the same checks locally before pushing:**

```bash
pytest tests/ -v -m "not windows_only and not gui"
```

Coverage XML is uploaded as a workflow artifact on each Linux CI run (one artifact per Python version). No external coverage service is configured.

### Matplotlib layout note

`mpp_fig` and `pmpp_fig` use `constrained_layout=True` (set at figure creation) because they contain `GridSpec` subplots with column-spanning axes. Do **not** call `tight_layout()` on these figures — it conflicts with `constrained_layout` and produces warnings. `stab_fig` does not use `constrained_layout` and still uses `tight_layout()` normally.
