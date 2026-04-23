# Changelog — JV Measurements

All notable changes to this project are documented here.

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
