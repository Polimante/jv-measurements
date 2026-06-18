# Session 7 — Polish: crash recovery + UX + docs + integration tests

## Context

You are working on the **jv-measurement** project. Sessions 1–6 are complete: ISOS-D-1, L-1, V-1 all integrated, stability metrics computed and visualized, autosave streams in place. This is the polish session — **DO NOT add new protocols here**. If you find a missing behavior, raise it instead of patching.

**Read first:**
- `CLAUDE.md`
- The full post-session-6 ISOS implementation across:
  - `app/measurement/isos_protocols.py`
  - `app/measurement/isos_continuous_runner.py`
  - `app/measurement/stability_metrics.py`
  - `app/io/isos_tracking.py`
  - `app/io/isos_autosave.py`
  - `app/gui/mpp_plot_factory.py`
  - `app/gui/main_window.py`

## Tasks

### 1. Crash recovery dialog

On `JVMeasurementApp` startup (after the auto-update check at line ~150–200 of `main_window.py`):

1. Persist the list of last-used ISOS folders in `~/.jv-measurement/recent_folders.json` (create the dir if absent). Update this list whenever `_isos_new_experiment` or `_isos_load_experiment` are called.

2. On startup, scan each recent folder for orphan `*_stress_inprogress_*.csv` files whose paired tracking xlsx still exists.

3. If any orphans found, show a Toplevel dialog:
   ```
   Found N unsaved stress logs from a previous run:
     • Sample_A_ISOS-L-1_20260512_142033.xlsx → 3 orphan CSVs
     • Sample_B_ISOS-V-1_20260513_091012.xlsx → 1 orphan CSV
   Consolidate them into the tracking files now?
   [Consolidate all] [Discard all] [Decide individually…]
   ```

4. Consolidation uses `consolidate_csv_into_xlsx` from session 1. "Decide individually" opens a per-file checkbox list. "Discard" renames the CSV with `.discarded` extension (don't delete — preserve in case).

### 2. UX polish — status bar enhancements

- **ETA in status bar** during continuous experiments:
  - Format: `"L-1 running — 3h 24m of 24h — next scan in 12m — ETA 2026-05-16 14:30"`
  - Update every 10s during stress, immediately at scan completion.
- **Autosave indicator**: small colored dot next to status text
  - Green dot when CSV is being flushed (tooltip: "Autosaving…")
  - Red dot if last flush raised an exception (tooltip: error message)
  - Gray when idle
- **Throttle** `on_stress_update` callback rate to 5 Hz max for smooth plotting (already mentioned in session 3 prompt, verify it's actually implemented).

### 3. Stability metrics panel polish

- Add a "Show overlay on plot" toggle (default ON) — when off, removes the T80/T95/T_S lines from the chart (useful for clean screenshots).
- Add a "Copy metrics to clipboard" button — copies a formatted text block:
  ```
  ISOS-L-1 Stability Metrics
  Sample: TEST_CELL
  PCE_0 = 16.84 ± 0.12 %
  T80   = 142.3 h
  T95   = 38.2 h
  T_S   = 18.5 h
  PCE_S = 16.21 %
  Ts80  = not reached
  n_1000 = 0.72 (extrapolated, ±0.04)
  ```

### 4. Integration tests

Create `tests/test_isos_end_to_end.py`:
- Full L-1 sequence: dummy SMU, 5-minute experiment, 1-min interval, MPP load, P&O engine. Verify final tracking file contains: 4 baseline scans + 5 log rows + 5 JV sheets + 5 SPO sheets + 4 Stress sheets + Metrics sheet with 5 rows. Verify consolidated stress data matches what was streamed to CSV.
- Full V-1 sequence: dummy SMU, 5-minute experiment, tiered schedule (every 60s for 5min). Verify: 5 dark JV sheets + 5 illuminated JV sheets + 5 SPO sheets + 4 Stress sheets + Metrics sheet. Verify dark params (n_dark, R_sh_dark, etc.) populated.

Create `tests/test_isos_crash_recovery.py`:
- Simulate a crash by killing a runner mid-stress (`stop_event.set()` + force-close + check the CSV is in `_stress_inprogress_` state).
- Restart `JVMeasurementApp` (in-test instantiation), verify orphan detection runs, recovery dialog method offered, consolidation succeeds.

### 5. CLAUDE.md updates

Update the "ISOS Protocols" section to reflect the final architecture. New subsections:

```markdown
### ISOS Protocols — Architecture

The ISOS Protocols mode supports 3 variants registered in
`app/measurement/isos_protocols.py`:
- ISOS-D-1: manual-trigger dark storage (existing).
- ISOS-L-1: continuous light soaking, OC or MPP load.
- ISOS-V-1: dark electrical bias stress, periodic dark + illuminated J-V.

Continuous protocols run via `ContinuousISOSRunner`, which orchestrates:
optional dark equilibration → baseline conditioning → main loop of
[stress → pre-scan relaxation → (optional dark JV) → illuminated JV →
SPO → append → failure check].

### Tracking File Format

… (describe ISOS_Config, ISOS_Log, JV_#NNN, JV_Dark_#NNN, SPO_#NNN,
Baseline_#NN, Stress_#NNN, Equilibration_log, ISOS_Metrics sheets)

### Autosave & Crash Recovery

CSV streams flush every stress data point; openpyxl consolidation happens
at scan boundaries. On startup, orphan CSV files in recent folders are
detected and a recovery dialog is offered.

### Stability Metrics

T80, T95, T_S, Ts80, n_1000 computed via `stability_metrics.py` using
Savitzky-Golay smoothing for burn-in detection (Auto / Manual / Fallback
modes). Persisted to ISOS_Metrics sheet, visualized as overlays on
the PCE-vs-time chart.

### Future ISOS Protocols

D-2/D-3 (high T, damp heat), L-2/L-3 (T-controlled light soaking),
LC-* (light cycling), T-* (thermal cycling), V-2/V-3 (alternative bias
loads), O-* (outdoor) — all unimplemented, require additional hardware
(thermal chamber, motorized shutter, outdoor enclosure, etc.).
```

### 6. Run the full test suite

```bash
pytest tests/ -v -m "not windows_only and not gui"
```

Confirm all green. Then run the GUI suite on Windows if available:

```bash
pytest tests/ -v -m "gui"
```

## Acceptance

- All tests green (both CI matrix and GUI suite).
- CLAUDE.md accurately describes the final architecture.
- A manual 5-minute L-1 run with the dummy SMU produces a clean tracking file with autosave indicator behaving correctly.
- Killing the app mid-run and restarting offers a recovery prompt that successfully consolidates the orphan CSV.
- ETA in status bar updates correctly during continuous experiments.
