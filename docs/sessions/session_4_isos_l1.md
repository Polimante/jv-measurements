# Session 4 — ISOS-L-1 (GUI + runner integration)

## Context

You are working on the **jv-measurement** project. Sessions 1–3 built the foundation: protocol registry, generalized tracking, OC mode in MPP trackers, continuous runner, plot factory, and all shared helpers (baseline conditioning, pre-scan relaxation, SPO, failure detection, tiered schedule). This session wires ISOS-L-1 end-to-end.

**Read first, end-to-end:**
- `CLAUDE.md`
- `app/measurement/isos_protocols.py`
- `app/measurement/isos_continuous_runner.py`
- `app/gui/mpp_plot_factory.py`
- `app/gui/mpp_settings_frame.py`
- `app/gui/main_window.py` (ISOS panel ~line 3971+)
- `app/io/isos_tracking.py`

## Reference

**Khenkin et al. Nat Energy 2020 — ISOS-L-1**: simulated sunlight, ambient T, ambient air, load = OC or MPP. Cell stays under continuous illumination; J-V scans happen at regular intervals (or via a tiered schedule).

## Data model

Two distinct acquisition modes:

**L-1 / OC load**
- During stress: read Voc continuously (every `interval_ms`). Plot `Voc(t)` as continuous line. PCE is undefined during stress.
- At scan boundaries: full illuminated J-V → extract PCE, Voc, Jsc, FF, etc.
- Plot layout: `build_oc_continuous_panel` from session 3.

**L-1 / MPP load**
- During stress: P&O or JV-SPO tracker runs continuously. PCE(t), J(t), V(t) are dense continuous traces.
- At scan boundaries: full illuminated J-V → extract PCE, etc. (sanity check vs. continuous trace).
- Plot layout: `build_mpp_continuous_panel` from session 3.

## Tasks

### 1. Register ISOS-L-1

In `app/measurement/isos_protocols.py`:

```python
ProtocolSpec(
    id="ISOS-L-1",
    label="ISOS-L-1",
    is_continuous=True,
    supports_oc=True,
    supports_mpp=True,
    supports_bias_v1=False,
    requires_dark_jv=False,
    requires_illuminated_jv=True,
    default_interval_min=15.0,
    default_total_duration_h=24.0,
    stress_with_light=True,
    notes="Light soaking at ambient T, OC or MPP load.",
)
```

### 2. GUI — L-1 sub-frame

In `main_window.py`, extend `_isos_on_protocol_change()`:
- D-1: existing OC-only locked behavior.
- L-1:
  - Enable both OC and MPP radios.
  - Show the new `isos_l1_subframe` inside the Conditions tab.
  - Hide D-1-only widgets.

Build `isos_l1_subframe` with:
- Total duration (h), Scan interval (min)
- Load = OC / MPP (radios)
- Pre-scan relaxation (s) — default 60
- SPO duration after each scan (s) — default 20 (0 to disable)
- Equilibration before start (h) — default 0 (disabled); checkbox to enable
- Baseline conditioning: # scans (default 4), rest between (s, default 60)
- Failure detection group:
  - PCE threshold (% of PCE_0, default 20)
  - Auto-stop on failure (checkbox, default ON)
- If MPP selected → reveal:
  - Engine: P&O / JV-SPO (radios)
  - Shared MPP parameters frame (`build_mpp_settings_frame` from session 2), bound to `self.isos_l1_*` vars.
- If OC selected → reveal:
  - Interval between Voc reads (ms) — single field, default 1000.

### 3. Dynamic plot area

When L-1 is the active protocol AND load is MPP: destroy current `isos_fig` and replace with the result of `build_mpp_continuous_panel(self.isos_plot_frame)`. When load is OC: use `build_oc_continuous_panel`. Store axes references on `self.isos_l1_plot_axes`. Switching load mode mid-experiment is disabled (only between experiments).

### 4. Runner integration

When the user clicks "Run ISOS" and L-1 is selected, `start_isos_measurement()` dispatches to a new method `run_isos_l1_measurement()`:

1. Build config dict from L-1 sub-frame vars + cell area + illumination + JV params.
2. Instantiate `ContinuousISOSRunner` with `stress_with_light=True`, correct `load_mode` / `mpp_engine`.
3. Wire callbacks:
   - `on_stress_update` → throttled plot update (max 5 Hz to keep GUI snappy)
   - `on_scan_done` → append tree row + redraw J-V plot + `play_completion_sound`
   - `on_status` → `isos_status_var.set`
   - `on_log` → `_isos_add_to_log`
   - `on_failure_detected` → modal "Cell failure detected: <reason>. Continue / Stop"
   - `on_baseline_done` → log baseline PCE_0_median in status + tree
   - `on_complete` → re-enable Start button
4. Run in a daemon thread, same lifecycle pattern as D-1.

### 5. Per-cycle flow inside the runner (for L-1)

Already implemented in session 3's `ContinuousISOSRunner.run()`:

```
1. apply_dark_equilibration(eq_h)         [if enabled]
2. run_baseline_conditioning(4, 60s)      → set PCE_0_median
3. for each cycle:
     apply_stress_until(next_scan_time)   [L-1 stress: light on]
     pre_scan_relaxation(60s)             [shutter still open;
                                           SMU in OC mode in dark]
     perform_illuminated_jv_scan()
     perform_spo_after_scan(vmpp, 20s)
     append log row with PCE_rev + PCE_SPO + standard params
     check failure_detection
     resume stress
```

Note: for L-1, the shutter never closes — `pre_scan_relaxation` just puts the SMU in OC mode; light is still on. This is acceptable per Khenkin §4.1 (the relaxation is electrical, not optical).

### 6. Tracking file creation

`_isos_new_experiment()` must pass the right `protocol_id` from the dropdown. `_isos_build_config()` must include all L-1-specific fields so they end up in the Config sheet.

### 7. Tests

- `tests/test_isos_l1_runner.py` — call `run_isos_l1_measurement` against `DummySMU` with 0.1h total, 0.02h interval. Verify:
  - Tracking file has 5 log rows + 5 JV sheets + 4 Baseline sheets + 5 SPO sheets.
  - CSV consolidation produces correctly-named `Stress_#NNN` sheets.
  - PCE_0_baseline_median populated.
  - PCE_SPO column populated.
- `tests/test_isos_l1_gui.py` (`@pytest.mark.gui`) — instantiate `JVMeasurementApp`, switch to ISOS mode, select L-1, toggle OC ↔ MPP. Verify subframe widgets and plot axes count change as expected.

## Do NOT

- Add V-1 yet (session 5).
- Modify D-1 behavior — D-1 path must remain bit-identical.
- Touch the existing Pure MPP / MPP / Stab / Curation modes.

## Acceptance

- A 2-minute L-1 dry-run with the dummy SMU produces a valid tracking file with baseline scans, multiple stress sheets, multiple JV sheets, SPO sheets, and updated metrics.
- Stopping mid-experiment leaves a consistent file (last completed scan + last completed stress block, no half-written rows).
- Toggling load OC↔MPP swaps the plot layout cleanly between experiments.
- Failure detection fires on synthetic PCE drop below threshold and pauses the loop.
