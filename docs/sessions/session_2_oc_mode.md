# Session 2 — OC mode in MPP trackers + shared MPP settings frame

## Context

You are working on the **jv-measurement** project. Session 1 added the protocol registry and generalized the tracking-file module. This session adds **OC (open-circuit) support** to both existing MPP trackers so they can be reused for ISOS-L-1 stress phases.

**Read first, end-to-end:**
- `CLAUDE.md`
- `app/measurement/pure_mpp_tracker.py`
- `app/measurement/mpp_tracker.py`
- `app/instrument/keithley.py`
- `app/gui/main_window.py` — look for `_build_pure_mpp_panel` and `_build_mpp_panel`
- `tests/test_pure_mpp_tracker.py` and `tests/test_mpp_tracker.py`

## Goal

Add OC (open-circuit, held-at-Voc) support to both MPP tracker engines without changing their existing MPP behavior. Also extract duplicated MPP parameter widget code into a single reusable helper.

**Why**: ISOS-L-1 and ISOS-V-1 (Khenkin et al. Nat Energy 2020, Table 1) require the cell to be held at *either* OC or MPP between periodic J-V scans. Existing MPP code only handles MPP — OC must be added.

**Keithley 2400 in OC mode**: source = CURR with setpoint 0 A, sense = VOLT. The reading gives Voc directly. No perturbation applied.

## Tasks

### 1. OC primitives in keithley.py

Add to `app/instrument/keithley.py`:

```python
def configure_oc_mode(inst, nplc: float = 1.0):
    """Source 0 A, sense voltage. For Voc tracking under OC load.
    Sequence: *RST, :SOUR:FUNC CURR, :SOUR:CURR 0,
              :SENS:FUNC 'VOLT', :SENS:VOLT:PROT 2,
              :SENS:VOLT:NPLC <nplc>, :SYST:RSEN OFF, :OUTP ON
    """

def read_voc_now(inst) -> float:
    """Single Voc read in volts. Parses :READ? response."""
```

### 2. PureMPPTracker — add load_mode

Extend `PureMPPTracker` in `app/measurement/pure_mpp_tracker.py`:

- Add `load_mode` param to `__init__` (str: `"MPP"` or `"OC"`), default `"MPP"` → existing behavior unchanged.
- When `load_mode="OC"`:
  - If `params.get("oc_skip_initial_scan", False)` → skip the initial J-V scan. Otherwise run it for a baseline.
  - Replace P&O loop with a Voc-read loop. On each tick:
    - `v = read_voc_now(inst)`, `j = 0.0`, `pce = 0.0`
    - Append to `time_s`, `v_track`, `j_track`, `pce_track`.
  - Configure instrument via `configure_oc_mode()` instead of `_configure_instrument()`.
- Preserve the callback interface — `on_update(t, v, j, pce)` is called with `j=0`, `pce=0` in OC mode; the GUI will handle rendering.

### 3. MPPTracker — same treatment

In `app/measurement/mpp_tracker.py`, add `load_mode` param to `MPPTracker`:
- Default `"MPP"` → existing behavior.
- In `"OC"` mode, the SPO phase becomes a "Voc hold phase": `configure_oc_mode()`, read Voc at each interval, append to `spo_time/spo_j/spo_pce` with `j=0`, `pce=0`. The J-V scan portion of each cycle still runs normally.

### 4. Shared MPP settings frame

Create `app/gui/mpp_settings_frame.py`:

```python
def build_mpp_settings_frame(parent, prefix: str, defaults: dict) -> tuple:
    """Returns (frame, vars_dict).
    vars_dict maps the same keys as today (perturb_step_mv, interval_ms,
    v_min, v_max, nplc, delay_ms) to Tk vars.
    """
```

Refactor `_build_pure_mpp_panel` and `_build_mpp_panel` in `main_window.py` to call this helper instead of inlining the widget grid. Existing attribute names (e.g. `self.pmpp_step_mv_var`) must remain bound to the same Tk vars — i.e. the helper writes them into `self` via `setattr`, or the call site does. Verify the Pure MPP and MPP tabs look and behave identically.

### 5. Tests

- `tests/test_pure_mpp_tracker_oc.py` — drive `PureMPPTracker` against `DummySMU` with `load_mode="OC"`. Verify `v_track` contains non-zero Voc values; `j_track` and `pce_track` are all zero.
- `tests/test_mpp_tracker_oc.py` — same for `MPPTracker`.
- `tests/test_keithley_oc.py` — `DummySMU` + `configure_oc_mode` + `read_voc_now` round-trip.
- Existing tracker tests (MPP mode) must remain green.

## Do NOT

- Touch `app/io/isos_tracking.py` (session 1 shipped it).
- Add ISOS-L-1 or V-1 anywhere yet.
- Change existing MPP behavior when `load_mode` is left at default `"MPP"`.

## Acceptance

- All existing tests stay green.
- New OC tests pass.
- Pure MPP and MPP modes in the live GUI behave identically to before.
