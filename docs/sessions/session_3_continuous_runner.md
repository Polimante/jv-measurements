# Session 3 — Continuous runner + plot factory + shared helpers

## Context

You are working on the **jv-measurement** project. Sessions 1–2 added the protocol registry, generalized tracking module, OC mode in MPP trackers, and the shared MPP settings frame. This session builds the protocol-agnostic continuous-experiment runner and the reusable plot factory, plus all the cross-protocol helpers (relaxation, baseline, SPO, equilibration, failure detection, tiered schedule).

**Read first, end-to-end:**
- `CLAUDE.md`
- `app/measurement/pure_mpp_tracker.py` (post-session-2)
- `app/measurement/mpp_tracker.py` (post-session-2)
- `app/measurement/isos_protocols.py`
- `app/io/isos_tracking.py`
- `app/io/isos_autosave.py`
- `app/gui/main_window.py` — MPP plot construction (`pmpp_fig` ~line 3331, `mpp_fig` ~line 2400)

## Goal

Build the runner + plot factory + shared helpers. **No GUI for L-1/V-1 yet** — that lands in sessions 4/5.

**Architecture key insight**: L-1 and V-1 share the same `stress → scan → stress` loop. They differ in:
- L-1: `stress_with_light=True`, no shutter prompts
- V-1: `stress_with_light=False`, shutter prompts at each scan + has dark JV scan

## Tasks

### 1. Continuous runner

Create `app/measurement/isos_continuous_runner.py`:

```python
class ContinuousISOSRunner:
    def __init__(self, instrument, config, callbacks, stop_event):
        # config keys: protocol_id, sample_name, folder, total_duration_h,
        #   scan_interval_min OR tiered_schedule, load_mode (OC/MPP),
        #   mpp_engine (PO/JVSPO/HoldLast), stress_with_light (bool),
        #   start_v, end_v, step_v, cell_area, pin, delay_ms, nplc,
        #   four_wire, prebias_*, light_soaking_*, tracking_file_path,
        #   all MPP params (perturb_step_mv, etc.),
        #   pre_scan_relaxation_s, spo_duration_s,
        #   equilibration_duration_h, baseline_n_scans, baseline_rest_s,
        #   failure_thresholds (pce_norm, idark_ratio, rsh_dark),
        #   requires_dark_jv (bool)
        # callbacks: on_status, on_stress_update(t, v, j, pce),
        #   on_scan_done(meas), on_log(msg), on_failure_detected(reason),
        #   on_baseline_done(baseline_dict), on_complete

    def run(self):
        # 1. Optional dark equilibration
        # 2. Baseline conditioning (4 scans, save PCE_0_median)
        # 3. Main loop:
        #    while not stop_event and elapsed < total_duration:
        #        self._apply_stress_until(next_scan_time, csv_stream)
        #        self._pre_scan_relaxation()
        #        if requires_dark_jv: meas_dark = self._perform_dark_jv_scan()
        #        if not stress_with_light: self._prompt_open_shutter()
        #        meas = self._perform_illuminated_jv_scan()
        #        spo = self._perform_spo_after_scan(vmpp, spo_duration_s)
        #        if not stress_with_light: self._prompt_close_shutter()
        #        self._append_to_tracking_file(meas, meas_dark, spo)
        #        consolidate_csv_into_xlsx(...)
        #        self._check_failure(meas)
        #        if failure and auto_stop: break
        # 4. Cleanup: close CSV, OUTP OFF, on_complete callback
```

### 2. Shared helpers (methods on ContinuousISOSRunner)

```python
def run_baseline_conditioning(self, n_scans=4, rest_s=60) -> dict:
    """Performs n illuminated J-V scans with rest_s OC-dark rest
    between each. Discards scan #1 (first-scan artifact). Returns
    {PCE_0_median, PCE_0_std, all_scans: [...], method: 'median_of_N-1'}.
    Saves each as Baseline_#NN sheet in tracking file."""

def apply_dark_equilibration(self, duration_h):
    """Holds cell at OC in dark for duration_h hours, checking
    stop_event every 10s. Records to Equilibration_log sheet."""

def pre_scan_relaxation(self, relaxation_s=60.0):
    """Switches SMU to OC mode (configure_oc_mode from session 2),
    sleeps for relaxation_s with stop_event polling. Called
    immediately before every J-V scan."""

def perform_spo_after_scan(self, vmpp, duration_s=20.0) -> dict:
    """Hold at vmpp for duration_s, sample J at 5 Hz. Returns
    {PCE_SPO: mean of last 1/3, PCE_SPO_std, time_s, pce_trace}.
    Persists trace as SPO_#NNN_tXXXXh sheet."""

def _check_failure(self, meas):
    """Triggers on_failure_detected callback if any threshold
    crossed: PCE_norm < pce_threshold OR I_dark > baseline_idark *
    multiplier OR R_sh_dark < rsh_threshold."""

def next_scan_time(self, elapsed_h):
    """Returns next absolute time in h. If tiered_schedule is set,
    uses the schedule list of (start_h, end_h, interval_min); else
    uses scan_interval_min constant."""
```

### 3. _apply_stress_until logic

- **L-1 OC**: configure_oc_mode + loop reading Voc, append to CSV each point.
- **L-1 MPP**: instantiate PureMPPTracker or MPPTracker (per `mpp_engine` config) with `load_mode="MPP"` and remaining duration. Wire tracker's `on_update` to also write into CSV stream. After first cycle, pass `last_known_vmpp` so tracker skips its internal initial scan.
- **V-1 OC**: hold at last known Voc (constant V output, dark). If `log_i_dark_during_stress`, sample I at interval_ms.
- **V-1 MPP (HoldLast)**: hold at last known Vmpp (constant V, dark). Do NOT run P&O in the dark — meaningless.

Catch all exceptions in the loop, log via callbacks, ensure CSV is flushed, instrument OFF, partial tracking file consolidated. The user must never lose data.

### 4. Plot factory — 3 layouts

Create `app/gui/mpp_plot_factory.py`:

```python
def build_mpp_continuous_panel(parent_frame) -> dict:
    """Returns {fig, canvas, ax_pce, ax_j, ax_v, ax_jv}.
    Same layout as the existing pmpp_fig (GridSpec(3,2), ratios [4,3,3])."""

def build_oc_continuous_panel(parent_frame) -> dict:
    """Returns {fig, canvas, ax_voc, ax_jv}.
    2-row layout, Voc(t) on top, latest J-V curve on bottom."""

def build_scan_only_panel(parent_frame) -> dict:
    """Returns {fig, canvas, ax_pce, ax_jv, ax_idark}.
    For V-1 with sparse PCE data:
      Top: PCE_rev and PCE_fwd vs elapsed_h (scatter+line markers)
      Bottom-left: J-V curve from latest scan (fwd+rev overlay)
      Bottom-right: I_dark vs t during stress (continuous trace)."""

def update_mpp_plot(axes, time_s, v, j, pce, jv_curves): ...
def update_oc_plot(axes, time_s, voc, jv_curves): ...
def update_scan_only_plot(axes, log_rows, latest_jv, i_dark_trace): ...
```

Refactor `_build_pure_mpp_panel` to call `build_mpp_continuous_panel`. Visual identity must be preserved — diff the screenshot mentally before/after.

### 5. Crash-recovery scaffolding (no GUI yet)

On `JVMeasurementApp` startup, scan the configured ISOS folder for files named `*_stress_inprogress_*.csv` and log to status bar. Recovery dialog comes in session 7.

### 6. Tests

- `tests/test_isos_continuous_runner.py` — drive `ContinuousISOSRunner` against `DummySMU` with 0.05h total, 0.01h interval, load=OC → verify 5 scans appear. Load=MPP+PO → same. `stress_with_light=False` → verify shutter-prompt callback fires 2× per scan (open/close).
- `tests/test_runner_baseline.py` — `run_baseline_conditioning(4, 60)` produces a median, 4 saved scans, discards #1.
- `tests/test_runner_spo.py` — `perform_spo_after_scan` returns sensible PCE_SPO from a synthetic flat current trace.
- `tests/test_runner_failure_detection.py` — feed a synthetic PCE drop below 0.20, verify callback fires.
- `tests/test_tiered_schedule.py` — verify `next_scan_time` returns correct intervals across schedule boundaries.
- `tests/test_mpp_plot_factory.py` — instantiate all 3 layouts, verify axes count, no crashes when update functions called with empty arrays.

## Do NOT

- Build the ISOS-L-1 or V-1 GUI yet (sessions 4 & 5).
- Wire the runner into the ISOS panel yet.
- Add new protocols to the registry (still only D-1).

## Acceptance

- All existing tests stay green; new runner tests pass.
- The Pure MPP mode in the live GUI uses `build_mpp_continuous_panel` and looks identical to before.
- Manual dry-run of `ContinuousISOSRunner` against `DummySMU` for L-1 (OC and MPP) and V-1 (HoldLast) completes without errors.
