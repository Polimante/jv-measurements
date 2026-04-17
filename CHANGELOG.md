# Changelog — JV Measurements

All notable changes to this project are documented here.

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
