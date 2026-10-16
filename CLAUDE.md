# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the application

```bash
python main.py
```

Tests live in `tests/` and run hardware-free. The application itself is Windows-only (`winsound`, VISA drivers).

```bash
python -m pytest tests/                                   # everything the platform allows
python -m pytest tests/ -m "not windows_only and not gui" # what Linux CI runs
python -m pytest tests/ -m "gui"                          # GUI tests (Windows, real display)
python -m pytest tests/test_isos_tracking.py -v           # one file
python -m pytest tests/test_isos_tracking.py -k protocol_id -v      # one test (most are in classes)
```

The `validation/` suite is **separate from `tests/`** — deterministic, synthetic,
and run deliberately rather than on every commit (see "Validation suite"):

```bash
python -m validation.run_suite --config validation/config.yaml
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
    main_window.py             # JVMeasurementApp — entire GUI, measurement orchestration (~6.8k lines)
    instrument_dialog.py       # Modal VISA scanner dialog (Toplevel)
    shutter_dialog.py          # Shutter Control window (Instrument menu)
    plot_limits.py             # jv_plot_limits() - Tk-free axis maths for the J-V plot
    data_viewer.py             # DataViewerWindow — read-only viewer for saved xlsx files
    mpp_plot_factory.py        # Tk-free figure/axes builders for the MPP + ISOS panels
    mpp_settings_frame.py      # build_param_grid() — shared MPP / P&O parameter grid
    input_validation.py        # Tk-free validators for the Entry widgets (stable codes)
    theme.py                   # PLOT_COLORS + semantic colours — single source of truth
    tooltip.py                 # ToolTip hover widget
  instrument/
    keithley.py                # Stateless SCPI helpers (init, prebias, light_soaking, scan)
    tsp_transport.py           # SCPI→TSP translating transport for the 2600 series
    base.py                    # SourceMeter ABC
    keithley_smu.py            # Keithley2400SourceMeter — ABC over a live pyvisa resource
    keithley_adapter.py        # Keithley2400 — ABC adapter used by create_sourcemeter()
    dummy_smu.py               # DummySMU — ABC over a synthetic DummyCell
    __init__.py                # create_sourcemeter("keithley_2400" | "dummy")
  simulation/
    dummy_cell.py              # DummyCell — single-diode synthetic cell (the physics)
    fake_keithley.py           # FakeKeithley — SCPI-shaped fake wrapping a DummyCell
  shutter/                     # Pluggable shutter backends (see "Shutter control")
    base.py                    # Shutter ABC + ConfigField/BackendSpec metadata
    __init__.py                # BACKENDS registry, create_shutter, default_config
    manual.py                  # ManualShutter — prompts the operator (default)
    keithley_digio.py          # KeithleyDigioShutter — digio.writebit on a 2600
    serial_command.py          # SerialCommandShutter — text commands over serial
    manager.py                 # ShutterManager — lock, retry, persistence, state
  measurement/
    analysis.py                # Pure math: calculate_jv_parameters, calculate_resistances, HI
    mpp_tracker.py             # MPPTracker — JV-SPO cycling protocol (Alt+F5)
    pure_mpp_tracker.py        # PureMPPTracker — Perturb-and-Observe continuous tracking (Alt+F7)
    light_intensity_jv.py      # LightIntensityJVMeasurement — J-V vs intensity + n_idL (Alt+F3)
    isos_continuous_runner.py  # ContinuousISOSRunner — the L-1 / V-1 stress loop
    isos_protocols.py          # ProtocolSpec registry (PROTOCOLS, get_protocol, list_protocol_*)
    stability_metrics.py       # Pure math: T80 / T95 / T_S / PCE_S / Ts80 / n_1000
  io/
    app_settings.py            # ~/.jv-measurement/settings.json (atomic, corruption-tolerant)
    exporter.py                # save_data() and save_dark_jv_data() → xlsxwriter
    light_intensity_exporter.py# Consolidated Light-Intensity workbook (Config/Summary/fits)
    data_curator.py            # Data Curation mode (Alt+F8) — thresholds, groups, statistics
    isos_tracking.py           # Generic ISOS tracking workbook (create / append / read) + D-1 shims
    isos_autosave.py           # CSVStream durable autosave + consolidate_csv_into_xlsx()
    isos_recovery.py           # Tk-free orphan-CSV detection / consolidation for crash recovery
  utils/
    constants.py               # APP_NAME, APP_VERSION, global stop_event (threading.Event)
    sound.py                   # play_completion_sound() with playsound / winsound fallback
    updater.py                 # check_for_updates() — fetches version.json from GitHub
docs/
  help.html                    # Standalone HTML help opened via webbrowser.open()
  arduino_shutter.ino          # Example sketch for the serial shutter backend
  git-workflow.md              # Branch / commit / push routine for this repo
  release-vs-feature-branch.md # Branches, tags and releases — the rules
  _gen_*.py                    # Build helpers: samples, figures, GIFs, GUI screenshots
  sessions/                    # Per-session design notes (ISOS refactor history)
validation/                    # Deterministic publication-grade validation suite (see below)
Samples/                       # Bundled example workbooks, generated by docs/_gen_samples.py
tests/                         # Hardware-free test suite (see "Continuous Integration")
probe_shutter.py               # Bench tool: find the shutter's digio line, polarity, travel
probe_smu.py                   # Bench tool: report what the SourceMeter is actually measuring
build_installer/
  setup.iss                    # Inno Setup 6 script → dist/JV-Measurements-Setup-vX.X.X.exe
version.json                   # Machine-readable version + release notes (read by updater.py)
```

`jv_measurement_v2.3.py` in the project root is the pre-split monolith, kept for
reference only — nothing imports it. `create_test_isos_file.py`,
`diagnose_isos_file.py`, `simulate_isos_d1.py` and `take_screenshots.py` are
one-off developer scripts, not part of the application.

### Key architectural patterns

**Measurement thread**: `run_measurement()` runs in a daemon thread. GUI updates go through `self.status_var.set()` (safe from any thread). Dialogs that must appear mid-measurement (e.g. "open shutter") are posted to the main thread via `root.after(0, callback)` + `threading.Event.wait()`.

**Stop mechanism**: `stop_event` (module-level `threading.Event` in `constants.py`) is shared between the GUI thread and measurement thread. All loops in `keithley.py` check it. Call `stop_event.clear()` in the `finally` block after every measurement.

**Session data**: `self.session_data` is a list of dicts accumulated during the application session. Each entry holds raw arrays (`v_fwd`, `j_fwd`, `v_rev`, `j_rev`, `power_fwd`, `power_rev`, optional dark arrays), all PV parameters, and a color from `PLOT_COLORS`. `update_plot()` redraws from this list on every call — it does not accept data arguments.

**J-V plot axis limits** (`jv_plot_limits` in `app/gui/plot_limits.py`, kept out of `main_window` so the tests are not Windows-only — `main_window` imports `winsound`): the upper edges follow the data, with the historical 0-1.2 V / 0-30 mA/cm2 window kept as a floor so ordinary runs look unchanged. Two deliberate exclusions: the **lower X edge stays pinned at 0 V** (sweeps are often started below zero as a conditioning step, and those points carry a settling transient belonging to the treatment, not the device — they are measured and saved, just not drawn), and **dark current is excluded from the vertical fit** (it is exponential and reaches hundreds of mA/cm2, so fitting it would squeeze the photocurrent into a flat line on zero; one linear axis cannot show both, and the semi-log |J| view in the Data Viewer's "Dark Ideality Fit" is the tool for reading a dark diode).

**Plot interactivity**: The Matplotlib canvas uses `button_press_event` (not `pick_event`) for legend toggling because `mplcursors` with `hover=True` interferes with `pick_event` in the Tkinter backend. The active connection id is stored in `self._pick_cid` and disconnected before re-registering on each redraw.

### Two instrument abstractions (deliberate split)

The project intentionally keeps **two** ways of talking to the source-measure unit:

1. **Raw SCPI** (`app/instrument/keithley.py` + `inst.write`/`inst.query`) — used by
   the **older, safety-critical measurement code**: `run_measurement`, `MPPTracker`,
   `PureMPPTracker`, the ISOS-D-1 routine, and `ContinuousISOSRunner`.
   `self.instrument` is a live pyvisa resource.
2. **`SourceMeter` ABC** (`app/instrument/base.py`) — a higher-level interface
   (`set_voltage`, `read_current_density`, `measure_scan`…) with three
   implementations: `DummySMU` (synthetic, used by tests, `validation/` and the
   sample generator), `Keithley2400SourceMeter` (`keithley_smu.py`, wraps a live
   pyvisa resource) and `Keithley2400` (`keithley_adapter.py`, returned by
   `create_sourcemeter("keithley_2400")`).

**The ABC does reach real hardware, in exactly one mode.** Light-Intensity J-V
(Alt+F3) is the one production path built on it: `main_window` wraps
`self.instrument` in a `Keithley2400SourceMeter` and hands it to
`LightIntensityJVMeasurement`. Everything older stays on raw SCPI. Converging the
rest onto the ABC would mean rewriting the most safety-critical code (it controls
a real instrument wired to real cells) with no way to regression-test it in CI.
The payoff is only consistency, so we do not do it — **new** orchestrators,
however, should be written against the ABC, as Light-Intensity J-V was.

`keithley_smu.Keithley2400SourceMeter` and `keithley_adapter.Keithley2400` are two
adapters over the same helpers; only the former is reachable from the GUI, the
latter only via `create_sourcemeter()` (tests). Do not add a third — extend one.

Consequence for tests: SCPI-based components are tested with SCPI-shaped fakes
(`MagicMock` with a `.query` side-effect, or `app.simulation.fake_keithley.FakeKeithley`),
**not** with `DummySMU`. Revisit this split only if a concrete need arises to run a
real measurement mode (e.g. ISOS-L-1/V-1) through `DummySMU` in the live GUI.

**Keithley 2600-series (TSP) support — `tsp_transport.py`.** The raw-SCPI path is
the native language of the Keithley **2400**. The Keithley **2600 series**
(2601/2602/2611/2612/2634/2635/2636, 2650 family) speaks **TSP** instead and
rejects colon-commands with error `-285 (TSP Syntax error … near ':')`. Rather
than rewrite the ~50 SCPI call sites, `app/instrument/tsp_transport.py` inserts a
translating transport *below* all of them: `TSPTransport` wraps the pyvisa
resource and presents the same `.write(cmd)` / `.query(cmd)` interface, mapping
the finite SCPI vocabulary the code emits into TSP (`smua`/`smub`) and reshaping
`:READ?` replies back into the 2400 `"volt,curr,…"` comma format so
`.split(",")[…]` parsing is unaffected. **A 2400 is never wrapped** — it stays a
bare pyvisa resource, byte-for-byte identical to before, so the tested SCPI path
is unchanged; the transport only ever sits in front of a 2600. `open_instrument_dialog`
auto-selects via `is_tsp_instrument(idn)` (matches `MODEL 26xx`); the SMU channel
(A/B) comes from the "SMU channel" dropdown in the Instrument Settings tab
(`self.tsp_channel_var`, default A, read at connect). Unmapped commands raise
`UnsupportedSCPICommand` (fail loud, not a silent `-285` mid-run). Fully covered by
`tests/test_tsp_transport.py` (hardware-free). This does **not** touch the
`SourceMeter` ABC — the two-instrument split above is preserved.

### Shutter control — `app/shutter/`

Pluggable shutter backends, so the software works with whatever hardware a lab
has. **Adding a backend is one module plus one entry in `app/shutter/__init__.py`
— no GUI code.** The **Instrument → Shutter Control…** window
(`app/gui/shutter_dialog.py`) renders the selected backend's settings form
from its `BackendSpec.fields` tuple (`ConfigField` declares
name/kind/default/bounds/tooltip and does its own coercion), so no GUI file
names a backend's options.

The window hangs off the menu bar rather than a measurement panel on
purpose: the shutter is a property of the rig, so one configuration serves
J-V, Light Intensity, MPP, Stabilized Current, Pure MPP and every ISOS
protocol. `JVMeasurementApp.open_shutter_dialog()` keeps it single-instance;
the app owns `shutter_state_var` and pushes the badge colour into the window
when one is open, so state changes from a measurement thread still show.

| Piece | Role |
|---|---|
| `base.py` | `Shutter` ABC, `ConfigField`, `BackendSpec`, `ShutterError`, the `OPEN`/`CLOSED`/`UNKNOWN` states |
| `__init__.py` | The `BACKENDS` registry + `create_shutter` / `default_config`. Factories import lazily so a backend's dependency stays optional |
| `manual.py` | `ManualShutter` — prompts the operator. **The default**, and byte-identical to the pre-shutter behaviour |
| `keithley_digio.py` | `KeithleyDigioShutter` — `digio.writebit` on a 2600 line |
| `serial_command.py` | `SerialCommandShutter` — configurable text commands over a serial port. Covers an Arduino (see `docs/arduino_shutter.ino`), Thorlabs SC10, Uniblitz, USB relay boards: only the words change, never the code. `pyserial` is imported at connect time, never at construction, so a stored config naming it cannot stop the app from starting |
| `manager.py` | `ShutterManager` — the only object the rest of the app touches |

Contract: `open()`/`close()` **block until settled** (the settle delay is inside
them) and are **idempotent**, so every call site is a one-liner. Nothing in the
package imports tkinter — the manual backend's dialog is injected as a
`prompt(action)` callable, the same pattern `ContinuousISOSRunner` already uses
for `on_shutter`.

`ShutterManager` owns the backend, serialises access behind an `RLock` (the
Keithley backend shares the SourceMeter's VISA session, and the panel's Test
buttons run on the Tk thread while measurements run on their own), retries a
failed actuation once, persists config via `app/io/app_settings.py`
(`~/.jv-measurement/settings.json`, atomic write, corrupt file → defaults), and
reports state through `on_state_change` so the panel's badge can follow.

**The `keithley_digio` backend and `TSPTransport`.** Digital I/O has no SCPI
equivalent to translate *from*, so `TSPTransport.write_raw()` / `query_raw()`
pass TSP straight through. They are separate methods on purpose: the guarantee
that every `write()`/`query()` is a checked, enumerated translation is what
keeps the SCPI path trustworthy. **Both** are needed — the backend's readback
verification (`print(digio.readbit(N))`) is a query, and routing it through
`query()` raises `UnsupportedSCPICommand`.

**Startup state is asserted, not assumed.** A 2600's digital I/O lines idle HIGH
(5.1 kΩ pull-up) and `reset()` does not touch them, so with a non-inverting
interface box the shutter sits OPEN whenever nothing is driving it. The
application therefore closes the shutter as soon as it can reach the hardware —
in `open_instrument_dialog` via `_shutter_close_on_connect()` — and again in
`_close_instrument()` *before* the VISA session goes away (which also covers
application exit). It cannot close it any earlier than connect-time, and the
badge reads "unknown (connect instrument)" until then. Both paths go through
`_shutter_close_if_automatic()`, which skips non-automatic backends
(`BackendSpec.is_automatic`): a manual backend closing there would pop a modal
prompt on every connect, disconnect and quit.

A backend that needs **no** SourceMeter (`requires_instrument=False` — the
serial one) has nothing to wait for, so `_shutter_close_at_startup()` closes it
200 ms after launch. That is the strongest form of the guarantee, and it is
available to any rig whose shutter is not wired through the SMU.

The lab's ABET Sun 2000 is wired to **digital I/O line 1, active HIGH, 68 ms of
blade travel** (measured with `probe_shutter.py`; 300 ms is used as the settle
delay). The simulator's SHUTTER CONTROL switch must be in EXTERNAL, where its
SHUTTER INPUT BNC is level-sensitive.

**`probe_shutter.py`** (project root) is the bench tool that finds the line,
polarity and settling time of an unknown wiring, without touching the
instrument's front panel. Manual mode watches the simulator's LED; `--auto` uses
the SMU's own current reading and also times the blade.

**How measurements drive it — `_shutter_set(open_, prompt_if_manual=...)`.**
Every measurement mode goes through this one method on `JVMeasurementApp`, and
the `prompt_if_manual` flag is what keeps the change safe for existing users:

* `True` only at the boundaries that **already** showed a dialog before shutter
  automation existed — the dark/illuminated handover in J-V and ISOS-D-1, and
  the ISOS-V-1 scan boundaries.
* `False` everywhere else (MPP, Pure MPP, Stabilized Current, Light Intensity,
  the ISOS stress phases, and every `finally`).

So a rig left on the **manual** backend sees exactly the dialogs it always saw —
no more, no fewer — while an automatic backend drives the shutter everywhere.
On the **simulator** the method toggles the fake cell's illumination and never
shows a dialog at all, so an unattended dark-J-V run goes start to finish
untouched (`tests/test_gui_smoke.py`).

It returns False when the operator aborts, having set `stop_event` — so the
callers' existing stop checks unwind the run exactly as the Stop button does.
When an automatic backend fails twice (the manager already retried), the
operator is offered a hand-move via `_shutter_manual_fallback`: OK continues,
Cancel aborts. Continuing with an unknown illumination state is never an option.

Closing is bracketed in the `finally` of all six measurement threads;
MPP and Pure MPP hand their run loop straight to a daemon thread, so
`_run_illuminated(work)` supplies the open/close bracket for those two.
`ContinuousISOSRunner` is **untouched** — it has always emitted `on_shutter` and
always blocked on it; only what happens inside `_isos_v1_shutter_prompt` changed.

The pre-flight Safety Check dialogs are generated through
`_shutter_safety_line()`, so they stop telling the operator to "confirm the
shutter is open" once the software is the one opening it.

**Verification by measurement (optional, off by default).** The shutter can only
report what it *commanded*; the SMU can report what the cell actually sees. With
"Confirm the light state with an SMU reading" ticked in the Shutter Control
window, `_shutter_set` reads the cell current at 0 V after each move and checks
it against the requested state (`verify_with_current` / `verify_threshold_a`, in
the shutter settings section, not per backend). This is what catches the
dangerous case on non-fail-safe wiring: a "dark" scan that silently ran
illuminated looks perfectly plausible in the data. A mismatch asks whether to
continue or abort; a failure of the *check itself* never fails a run. It reads
through the plain SCPI vocabulary, so it works on a 2400 and, via
`TSPTransport`, on a 2600 — and it leaves the instrument reset with the output
off, which is why it only runs at phase boundaries where a re-initialisation
follows.

**Simulator mode (live GUI, no hardware).** `Instrument → Connect to Simulator…`
(`open_simulator_dialog`) injects an `app.simulation.fake_keithley.FakeKeithley`
(SCPI-shaped, backed by a `DummyCell` in one of `VALID_MODES`) directly into
`self.instrument`. This drives the **raw-SCPI path unchanged** — it does *not*
converge onto the `SourceMeter` ABC, so the split above is preserved. The fake is
the same class the runner tests use (`FakeKeithley` is re-exported from
`tests/_isos_runner_helpers.py`). `self._simulated` gates the simulator-only
helpers; `_sim_set_light(on)` toggles the cell's illumination at dark/illuminated
scan boundaries (and the V-1 shutter prompt) and is a no-op with real hardware.
GUI smoke tests (`tests/test_gui_smoke.py`, `@pytest.mark.gui`) exercise full runs
through this path.

### Sign convention

```python
current = -float(inst.query(":READ?").split(",")[1])
```

The negation makes photocurrent positive in the power-generating quadrant (standard solar cell convention). Dark forward-bias current ends up negative after this transform.

### Excel file layout

`data_viewer.load_jv_file()` dispatches on sheet name; the two single-measurement
layouts are:
- `MeasurementData` — illuminated J-V; row 1 = headers, row 2 = scalar params, row 3+ = V/J/P arrays
- `Dark_JV_Data` — dark J-V; same layout but only V/J (no power, no PV params)

Optional sidecar sheets on these files: `SingleDiodeFit`, `DarkIdealityFit`,
`SMU_Readback` (J-V mode: per-point programmed V, measured V and raw current of
both illuminated scans, plus any open-contact warning — readers ignore it). The
other workbook families have their own writers and readers — Stabilized Current
and MPP/Pure-MPP traces (`exporter.py`), the Light-Intensity consolidated
workbook (`light_intensity_exporter.py`, detected by its `Config` + `Summary`
sheets), the ISOS tracking workbook (`isos_tracking.py`) and the Data Curation
output (`data_curator.py`).

Column mapping is documented in `data_viewer.py::_read_illuminated()` and `_read_dark()`. When adding new scalar columns to `exporter.py`, update the column indices in both reader functions and in `data_viewer.py::_populate_metadata()`.

### Open-contact detection (J-V mode)

`detect_open_circuit(v, j, cell_area)` in `analysis.py` flags an illuminated
scan with ≥ 3 consecutive points below 1 nA. An illuminated cell never sits
there (even at Voc the current moves tens of µA per step); a lost probe or
cable reads the SMU noise floor, ~1e-10 A. Seen on the bench on 2026-10-16:
the current dropped to that floor ~10–17 s into each run and stayed there even
at V < 0, so it is wiring, not device physics. `run_measurement` checks both
illuminated scans before extracting parameters, shows a non-blocking warning,
and writes the notes into `SMU_Readback`. Dark scans are deliberately not
checked — a good diode passes sub-nA currents near 0 V in the dark.

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

Mode panels are shown/hidden by `switch_mode()` using `pack()`/`pack_forget()`. The active panel name is stored in `self.current_mode` — one of `"jv"`, `"light_jv"`, `"mpp"`, `"stab"`, `"pure_mpp"`, `"curation"`, `"isos"`. `switch_mode()` refuses to switch while `self._measurement_running` is set.

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

D-2/D-3 (high T, damp heat), L-2/L-3 (T-controlled light soaking), LC-* (light cycling), T-* (thermal cycling), V-2/V-3 (alternative bias loads), and O-* (outdoor) are all unimplemented — they require additional hardware (thermal chamber, outdoor enclosure, etc.). The motorized shutter that LC-* needs now exists — see "Shutter control". Do not register them in `isos_protocols.py` without the corresponding runner support.

### Measurement sequence (J-V mode)

```
[Dark J-V fwd + rev]  →  [shutter opens]  →  [light soaking timer]
→  illuminated fwd scan  →  [pre-bias hold]  →  illuminated rev scan
```

Pre-bias is applied **between** the forward and reverse illuminated scans only.

### Scan rate mode

The Instrument Settings tab has a toggle between two modes:
- **Advanced** (default): delay entered manually; scan rate is a read-only calculated field.
- **Scan rate**: user enters target mV/s; delay is auto-calculated as `(step_mV / rate × 1000) − NPLC × 16.7 − 50 ms` (clamped ≥ 0). `⚠` is appended to the displayed rate when delay was clamped to 0.

Both modes use 50 ms as the GPIB overhead constant so they round-trip consistently.

### Validation suite — `validation/`

A **separate** deterministic suite from `tests/`: `tests/` guards against
regressions on every commit; `validation/` generates publication-grade evidence
(figures + tables for the manuscript) and runs weekly, on release tags, or on
demand.

```bash
python -m validation.run_suite --config validation/config.yaml   # → validation/results/
```

Everything is synthetic — `DummyCell` physics through `DummySMU` with
`sleep_scale=0`, all randomness seeded from `config.yaml` — so two runs produce
byte-identical `results.json`. Five tests: J-V extraction (A), single-diode fit
(B), MPP tracking (C), repeatability (D), ISOS-D-1 stability (E).
`validation/README.md` holds the methodology and acceptance criteria.

### Sample data and documentation assets

`Samples/` ships example workbooks so the app can be explored with no hardware.
They are **generated**, not hand-made:

```bash
python docs/_gen_samples.py        # regenerate Samples/ (deterministic)
python docs/_gen_doc_figures.py    # figures for docs/help.html
python docs/_gen_gui_shots.py      # real GUI screenshots, driven by FakeKeithley
```

The generator uses the production exporters and, for ISOS L-1/V-1, the real
`ContinuousISOSRunner` against a `FakeKeithley` + virtual clock — so a sample is
the same kind of workbook a real run writes. `tests/test_samples_generation.py`
asserts every committed sample still opens through the app's own readers; it does
**not** regenerate them. Regenerate and commit `Samples/` whenever a writer's
layout changes.

### Bench diagnostic scripts

Both are standalone, run against real hardware, and are not imported by the app:
`probe_shutter.py` (find the shutter's digital I/O line, polarity and blade
travel) and `probe_smu.py` (report what the SourceMeter is actually measuring —
for diagnosing a J-V curve with no zero-crossing). Their pure logic is unit
tested in `tests/test_shutter_probe.py` / `tests/test_smu_probe.py`.

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
4. Bump the version in `build_installer/setup.iss` as well
5. Commit, push, create a GitHub Release tagged `vX.Y.Z`
6. Optionally attach the compiled installer `.exe` to the release

`version.json` and `APP_VERSION` must agree — the updater compares them.

### Branch discipline

Never commit directly to `main`: it is the stable branch users run, and it only
advances by merge from a feature branch. Work happens on a feature branch and is
tagged from `main` at known-good points. Commits use conventional prefixes
(`feat:`, `fix:`, `docs:`, `test:`, `refactor:`). Full routine in
`docs/git-workflow.md` and `docs/release-vs-feature-branch.md`.

### Continuous Integration

Two GitHub Actions workflows live in `.github/workflows/`:

| Workflow file | Runner | Python | Marker filter |
|---|---|---|---|
| `tests.yml` | `ubuntu-latest` | 3.10, 3.11, 3.12 (matrix) | `not windows_only and not gui` |
| `tests-windows.yml` | `windows-latest` | 3.11 | `not gui` |

Both trigger on every `push` and `pull_request` to any branch. A third workflow,
`validation.yml`, runs the `validation/` suite on a weekly cron, on `v*.*.*`
tags, and on manual dispatch — never on an ordinary push.

**Pytest markers** (defined in `tests/conftest.py` and registered in `pyproject.toml`):

- `windows_only` — test requires Windows; auto-skipped on Linux/macOS.
- `gui` — test requires a live Tk display; auto-skipped in CI and on headless Linux (no `DISPLAY`).

`windows_only` is carried only by the crash-recovery test that imports
`main_window` (which imports `winsound`). `gui` is carried by seven modules:
the two data-viewer button tests, `test_isos_l1_gui`, `test_v1_scan_rate_warning`,
`test_gui_smoke`, `test_gui_shutter_dialog` and `test_gui_shutter_wiring`.

**All GUI tests share one Tk root and one `JVMeasurementApp`**, created by the
session-scoped `tk_root` / `jv_app` fixtures in `tests/conftest.py` and destroyed
only at the end of the session. Repeated `Tk()` / `destroy()` cycles corrupt the
Tcl interpreter state (`invalid command name "tcl_findLibrary"` on Tcl 9), which
made the suite pass, skip or error depending on which GUI module ran first. Do
not create a `Tk()` in a test — ask for `tk_root`. Because the app is shared, a
module must establish its own preconditions rather than assume pristine state;
`jv_app_defaults` holds the values captured before any test ran.

**Run the same checks locally before pushing:**

```bash
pytest tests/ -v -m "not windows_only and not gui"
```

Coverage XML is uploaded as a workflow artifact on each Linux CI run (one artifact per Python version). No external coverage service is configured.

### Matplotlib layout note

`mpp_fig` and `pmpp_fig` use `constrained_layout=True` (set at figure creation) because they contain `GridSpec` subplots with column-spanning axes. Do **not** call `tight_layout()` on these figures — it conflicts with `constrained_layout` and produces warnings. `stab_fig` does not use `constrained_layout` and still uses `tight_layout()` normally.
