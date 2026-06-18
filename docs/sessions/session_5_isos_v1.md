# Session 5 — ISOS-V-1 (dark stress + dark JV analysis + tiered schedule)

## Context

You are working on the **jv-measurement** project. Sessions 1–4 are complete: protocol registry, generalized tracking, OC mode, continuous runner with all shared helpers, plot factory, and ISOS-L-1 fully integrated. This session adds ISOS-V-1 with the correct (more complex) data model.

**Read first, end-to-end:**
- `CLAUDE.md`
- `app/measurement/isos_protocols.py`
- `app/measurement/isos_continuous_runner.py`
- `app/measurement/analysis.py` — focus on `fit_dark_ideality()` (line 488) and `calculate_resistances()` and `fit_single_diode()` (line 192)
- `app/io/isos_tracking.py`
- `app/gui/main_window.py` — ISOS panel, especially the L-1 sub-frame and `run_isos_l1_measurement` (added in session 4)
- `app/gui/mpp_plot_factory.py`

## Conceptual foundation — read carefully

ISOS-V-1 stresses the device with **constant electrical bias (+Vmpp or +Voc) IN THE DARK**. The cell does **not** generate power during stress, so PCE cannot be measured continuously. Instead, each scan cycle performs **TWO J-V scans** — one dark, one illuminated — to separately characterize:

**Dark J-V parameters (degradation mechanisms):**
- R_sh ↓ → shunt formation (metal diffusion, pinholes)
- R_s ↑ → contact / transport layer degradation
- J_0 ↑ → non-radiative recombination from trap states
- n → signals trap-assisted recombination regime shifts

**Illuminated J-V parameters (device performance):**
- PCE, Voc, Jsc, FF — for T80 / stability tracking
- HI (hysteresis index) — for ion migration impact
- R_s, R_sh under illumination — for comparison

**Reuse, don't duplicate:**
- `app/measurement/analysis.py::fit_dark_ideality(v, j, v_min, v_max)` → returns `DarkIdealityFit(n_idd, J_s, r_squared, …)`
- `app/measurement/analysis.py::calculate_resistances(v, j, Voc=None)` — for dark, pass `Voc=None` so R_s is extracted from high-fwd-bias slope.
- `app/io/exporter.py::save_dark_jv_data()` — for backup files
- `app/instrument/keithley.py::measure_scan()` — same primitive for both scans

## Khenkin / community-standard cadence

V-1 typically uses a **tiered measurement schedule** (interval grows as stress progresses):
- Hours 0–5: every 60 min
- Hours 5–24: every 240 min (4 h)
- Hours 24+: every 1440 min (24 h) until PCE_norm < 0.80

**Light exposure during illuminated scan must be minimized** (a few seconds — too slow = contaminates V-1 with L-1/T effects). Warn if scan rate too low.

## Tasks

### 1. Register ISOS-V-1

```python
ProtocolSpec(
    id="ISOS-V-1",
    label="ISOS-V-1",
    is_continuous=True,
    supports_oc=True,
    supports_mpp=True,
    supports_bias_v1=False,
    requires_dark_jv=True,
    requires_illuminated_jv=True,
    default_interval_min=60.0,
    default_total_duration_h=24.0,
    stress_with_light=False,
    notes="Electrical bias stress in dark. Periodic dark + illum. J-V.",
)
```

### 2. V-1 sub-frame (Conditions tab)

Mirrors L-1 layout but with extras:

- Header label (yellow background): "Stress is performed in DARK. Shutter opens only briefly for periodic illuminated J-V scans."
- Total duration (h), Stress load: OC (hold at last Voc, dark) / MPP (hold at last Vmpp, dark)
- Measurement schedule selector:
  - ○ Fixed interval (every [___] min)
  - ● Tiered (recommended for V-1, default values below)
  - ○ Custom (CSV import)
  - If Tiered: editable 3-row table:
    - Phase 1: Start [0] h, End [5] h, Interval [60] min
    - Phase 2: Start [5] h, End [24] h, Interval [240] min
    - Phase 3: Start [24] h, End [inf] h, Interval [1440] min
- Dark J-V scan range: Start [-0.2] V / End [+1.1] V / Step [0.025] V
- Dark ideality fit window: V_min [0.5] V / V_max [0.8] V
- Illuminated J-V scan rate warning panel (real-time):
  - Compute rate = (V_end − V_start) / scan_duration
  - If rate < 50 mV/s when illuminated → red label "Slow illuminated scan may contaminate V-1 (light exposure)"
  - If rate < 25 mV/s when illuminated → block + disable Run button
- "Log I_dark during stress" checkbox (default ON, sampled every interval_ms)
- Pre-scan relaxation (s) — default 60
- SPO duration (s) — default 20
- Equilibration before start (h) — default 12, checkbox enabled by default for V-1
- Baseline conditioning: # scans (default 4), rest (s, default 60)
- Failure detection thresholds (PCE %, I_dark multiplier, R_sh_dark Ω·cm²)
- All MPP-settings reused via `build_mpp_settings_frame` (helper from session 2). Note: V-1 in MPP mode means **hold last Vmpp** (constant V output, dark), NOT active P&O — the engine selector for V-1 reads "Hold last Vmpp from scan / Hold last Voc from scan".

### 3. V-1 plot layout

Extend `app/gui/mpp_plot_factory.py` with a richer V-1 panel:

```python
def build_v1_diagnostic_panel(parent_frame) -> dict:
    """GridSpec 4×2:
      row 0, span:  PCE(t) markers (from illuminated scans)
      row 1, col 0: R_s_dark(t) and R_s_illum(t) overlaid
      row 1, col 1: R_sh_dark(t) and R_sh_illum(t) — log scale
      row 2, col 0: n_dark(t)
      row 2, col 1: J_s_dark(t) — log scale
      row 3, col 0: HI(t) and HI_dark(t) overlaid
      row 3, col 1: latest illuminated J-V (fwd+rev) — top
                    latest dark J-V (fwd+rev) — bottom (split axis)
      Optional: separate window or status indicator for I_dark(t)
        during stress (continuous trace, opened on demand).
    """

def update_v1_plot(axes, log_rows, latest_illum_jv, latest_dark_jv):
    ...
```

### 4. V-1 runner — extend ContinuousISOSRunner

Per cycle (the runner already supports this via `requires_dark_jv=True`):

```
1. _apply_dark_stress_until(t_next_scan)
   - Hold at last_Vmpp or last_Voc (dark, no MPP tracking)
   - If log_i_dark: append I to CSV every interval_ms
2. pre_scan_relaxation(60s)            [SMU in OC mode, dark]
3. _perform_dark_jv_scan():
     v_dark_fwd, j_dark_fwd = measure_scan(start_dark, end_dark, +step)
     v_dark_rev, j_dark_rev = measure_scan(end_dark, start_dark, -step)
     R_s_dark_*, R_sh_dark_* = calculate_resistances(...)
     fit = fit_dark_ideality(v_dark_rev, j_dark_rev, v_min_fit, v_max_fit)
     n_dark, J_s_dark, n_dark_R2 = fit.n_idd, fit.J_s, fit.r_squared
     HI_dark = calculate_hysteresis_index from dark fwd vs rev
     save JV_Dark_#NNN_tXXXXh sheet (same column layout as MeasurementData
       but with dark data + scalar params)
4. _prompt_open_shutter()              [modal]
5. _perform_illuminated_jv_scan()      [as fast as configured]
6. perform_spo_after_scan(vmpp, 20s)
7. _prompt_close_shutter()             [modal]
8. append_isos_measurement() — both dark and illuminated params go
   into the same log row for that cycle
9. Update last_Vmpp / last_Voc from the latest illuminated scan
10. failure_detection check
```

### 5. Dark JV save format

Per-cycle dark sheets: `JV_Dark_#NNN_tXXXXh` with same column layout as illuminated `JV_#NNN_tXXXXh` (MeasurementData-compatible, V/J columns + scalar params row 2) but with dark data. This means the existing Data Viewer can open them if needed.

### 6. GUI wiring

`run_isos_v1_measurement()` is essentially `run_isos_l1_measurement()` from session 4 with:
- `stress_with_light=False`
- `requires_dark_jv=True`
- Schedule from tiered table
- All shutter prompt callbacks wired
- Scan rate validation before Run

### 7. Tests

- `tests/test_isos_v1_runner.py` — 3-cycle dummy SMU run, verify:
  - Tracking file has 3 illuminated + 3 dark JV sheets
  - Log has correct dark params (n_dark, J_s, R_s_dark, R_sh_dark, HI_dark) populated
  - Shutter callbacks fire 3× open and 3× close
  - `Stress_With_Light = False` in config
- `tests/test_v1_dark_fit_pipeline.py` — feed synthetic dark J-V data with known n and J_s, verify the extracted values match within tolerance.
- `tests/test_tiered_schedule_v1.py` — schedule advances correctly at the 5h and 24h boundaries.
- `tests/test_v1_scan_rate_warning.py` (`@pytest.mark.gui`) — verify warning appears at < 50 mV/s and Run button disables at < 25 mV/s.

## Do NOT

- Refactor the runner — if you find you need to, that means session 3 missed something; raise it instead of patching here.
- Add LC-1 or any other protocol (deferred to a future session).
- Run active P&O in the dark — meaningless without generation.

## Acceptance

- A 5-minute V-1 dry-run with the dummy SMU + 1-min scan_interval produces exactly 5 PCE points + 5 dark JV sheets + 5 illuminated JV sheets + I_dark CSV stream consolidated as Stress_#NNN sheets.
- The PCE(t) plot shows 5 markers (no continuous line between them — sparse data).
- R_sh_dark(t), n_dark(t), J_s_dark(t) traces are populated with markers.
- I_dark(t) plot shows continuous samples during the 4 stress intervals.
- `Stress_With_Light` column in Config sheet is False.
- Scan rate < 25 mV/s blocks Run; 25–50 mV/s warns.
