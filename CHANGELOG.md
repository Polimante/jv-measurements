# Changelog — JV Measurements

All notable changes to this project are documented here.

## [2.7.0] — 2026-10-23

> **Upgrading from 2.6.x or earlier: run the new installer.** This version needs a
> new Python library (scipy), which an in-place update cannot install. The update
> prompt opens the release page for you.

### Added
- **ISOS-L-1** (continuous light soaking, open-circuit or MPP load) and **ISOS-V-1** (dark electrical-bias stress) protocols, alongside ISOS-D-1
- **Stability metrics** — T80, T95, burn-in end T_S, PCE_S, Ts80 and n_1000, with a live panel, plot overlay and a per-scan `ISOS_Metrics` sheet
- **Crash recovery** for continuous ISOS runs — every stress sample is streamed to a durable CSV, and orphaned runs are offered for recovery at startup
- **Light-Intensity J-V** mode (Alt+F3) with ideality-factor analysis
- **Single-diode model fit** (automatic after each J-V, or interactive in the Data Viewer) and **dark ideality fit**
- **Open-contact warning** in J-V mode — an illuminated scan whose current falls to the SMU noise floor (< 1 nA for 3+ points) now warns instead of reporting meaningless parameters. A new `SMU_Readback` sheet records the programmed voltage, the measured voltage and the raw current of every point
- **"Measure from REV to FWD"** option (Scan Parameters, off by default) — runs the reverse scan first, for the dark and the illuminated J-V. Pre-bias stays right before the reverse scan
- **Illumination is saved in every J-V file** — new columns AB Scan Order, AC Illumination (mW/cm²), AD Detector Power, AE Detector Area. The Data Viewer shows them in a new "Illumination" section. Columns A–AA are unchanged
- Bundled `Samples/` — an example workbook for every measurement mode
- Deterministic `validation/` suite with synthetic ground truth
- **Shutter control** — the software now opens and closes the solar simulator's shutter itself, in every measurement mode. Configure it in **Instrument → Shutter Control…** (menu bar, not a measurement panel: one configuration serves J-V, Light-Intensity, MPP, Pure MPP, Stabilized Current and every ISOS protocol)
- Shutter backends are pluggable. Three ship with the software:
  - **Manual** (the default) — prompts the operator, exactly as before
  - **Keithley 2600 digital I/O** — drives one of the 14 lines on the SourceMeter's DB-25
  - **Serial / Arduino** — configurable text commands, so an Arduino or most commercial controllers (Thorlabs SC10, Uniblitz, USB relay boards) work with no code change. Example sketch in `docs/arduino_shutter.ino`. Needs `pip install pyserial`
- Adding a fourth backend is one module plus one registry entry and **no GUI code**: the settings form is generated from the backend's declared fields
- Shutter state is asserted rather than assumed — closed on connect, after every measurement, on disconnect and on exit. Backends that need no SourceMeter are closed at application startup
- Optional verification of the light state with an SMU current reading, which catches a "dark" scan that silently ran illuminated
- `probe_shutter.py` — bench tool that finds which digital I/O line drives the shutter, with which polarity, and how long the blade takes to travel
- `probe_smu.py` — bench tool that reports what the SourceMeter is actually measuring, for diagnosing a J-V curve with no zero-crossing
- Keithley **2600-series (TSP)** support via a translating transport, so the existing SCPI measurement code drives a 2612 unchanged. A 2400 is never wrapped
- **Connect to Simulator** — run every measurement mode end to end with no hardware
- User preferences persist in `~/.jv-measurement/settings.json` (atomic write, corrupt file falls back to defaults)
- Documentation: new **Section 10 — Shutter Control** in the user guide, covering wiring, both automatic backends, commercial controllers, and twelve troubleshooting entries

### Fixed
- The in-place updater could copy a version whose new libraries were not installed, leaving an application that did not start. It now checks first and sends you to the installer instead; it also checks that the installation folder is writable before copying anything, and shows its dialogs on the interface thread (they could freeze the application before)
- The installer did not recognise Python 3.13 or 3.14 and would download Python 3.11 again
- J-V plot axis limits were hard-coded, clipping any sweep that ran past 1.2 V — a high-Voc cell lost its zero-crossing off the right edge. The upper edges now follow the data. The lower X edge stays pinned at 0 V on purpose, so sub-zero conditioning points are measured and saved but not drawn
- On a 2600 the front panel kept displaying whatever it was last left on, so an operator could watch it read volts through an entire current sweep. `:SENS:FUNC` now points the display at the quantity being sensed

## [2.6.0] — 2026-04-23

### Added
- Data Curation — Composition Groups: define named groups of devices (e.g. Composition X → D1, D2, D3) for per-group statistical comparison
- Data Curation — Auto-detect groups from loaded filenames (prefix extraction)
- Data Curation — Raw Data Preview tab: click any file in the list to see its PV parameters instantly
- Data Curation — 4 statistical sheets in export (Fwd/Rev × All/Filtered): one column per composition group per parameter, ready for Origin box plots
- Data Curation — Redesigned Statistics sheet: per-group Mean/Std Dev/CV%/Max tables for Rev and Fwd scans, champion cell per group (Fwd+Rev), per-group hysteresis summary (Mean HI, Std, CV%, HI<10%, HI<5%), threshold yield table
- Auto-update now applies in-place: downloads and extracts the release zip over the installation directory without needing to reinstall

### Changed
- Data Curation thresholds: min-only filter (removed Max field); default scan mode changed to "Both scans independently"
- Data Curation grouping: prefix-based via regex ([A-Za-z]+\d+), no underscore required — "D1C1" and "D1_C1" both yield prefix "D1"
- Excel export: Calibri 11, centered alignment, Excel AVERAGE/STDEV formulas for stats rows, native ScatterChart per cell with power column

## [2.5.0] — 2025-04-17

### Added
- J-V measurement with forward and reverse scan
- Pre-bias conditioning before reverse scan
- Extraction of Voc, Jsc, FF, PCE, Rs, Rsh, HI (hysteresis index)
- MPP Tracking — JV-SPO cycling protocol (ISOS-L compatible, Alt+F5)
- Pure MPP Tracking — Perturb-and-Observe continuous tracking (Alt+F6)
- Stabilized Current measurement (Alt+F4)
- Interactive plot with hover tooltips (mplcursors) and session history
- Scan rate direct input mode (mV/s) — auto-calculates delay per point
- Clickable legend to show/hide individual curves
- Window menu with mode switching
- Incremental Excel export (.xlsx) with full parameter table
- Dark J-V measurement option
- Light soaking timer
- Auto-update checker (Help → Check for Updates)

### Instrument support
- Keithley 2400 SourceMeter via GPIB (KUSB-488B adapter)
- 4-wire (Kelvin) measurement support
