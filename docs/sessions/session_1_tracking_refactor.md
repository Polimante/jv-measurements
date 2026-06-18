# Session 1 — Tracking module refactor + protocol registry

## Context

You are working on the **jv-measurement** project (Python/Tkinter, Keithley 2400 SCPI). The ISOS-D-1 stability protocol is already implemented. This session is the foundation for adding ISOS-L-1 and ISOS-V-1 protocols in later sessions.

**Read first, end-to-end, before changing anything:**
- `CLAUDE.md` (root) — architectural overview
- `app/io/isos_exporter.py`
- `tests/test_isos_exporter.py`
- `app/gui/main_window.py` (lines ~3971–4823 — the ISOS panel and runner)

## Goal

Generalize the tracking-file module and introduce a protocol registry. **No new measurement modes are added in this PR — only refactoring + scaffolding.** ISOS-D-1 must keep working identically.

## Tasks

### 1. Protocol registry

Create `app/measurement/isos_protocols.py` with a `ProtocolSpec` dataclass and a `PROTOCOLS` list. Initially registered: **ISOS-D-1 only** (L-1 and V-1 added in later sessions).

Fields per spec:
- `id` (str)
- `label` (str)
- `is_continuous` (bool)
- `supports_oc` (bool)
- `supports_mpp` (bool)
- `supports_bias_v1` (bool)
- `requires_dark_jv` (bool)
- `requires_illuminated_jv` (bool)
- `default_interval_min` (float)
- `default_total_duration_h` (float)
- `stress_with_light` (bool)
- `notes` (str)

Helpers:
- `get_protocol(id) -> ProtocolSpec`
- `list_protocol_ids() -> list[str]`
- `list_protocol_labels() -> list[str]`

### 2. Tracking module generalization

Rename `app/io/isos_exporter.py` → `app/io/isos_tracking.py`. Generalize:

- Sheet names become `ISOS_Config` and `ISOS_Log` (no D1 in the name).
- Add a `protocol_id` column at position 1 in the config sheet.
- Add the following Log columns (initially nullable — used by sessions 4–6):
  - `Load` (OC/MPP), `Setpoint_V`, `MPP_Engine` (PO/JVSPO/HoldLast)
  - `Stress_With_Light` (bool), `Stress_Elapsed_h`
  - `PCE_0_baseline_median`, `PCE_0_baseline_std`, `baseline_method`
  - `pre_scan_relaxation_s`
  - `PCE_SPO`, `PCE_SPO_std`, `SPO_duration_s`
  - `HI_dark`
  - `failure_detected`, `failure_reason`
  - `R_s_dark_fwd`, `R_sh_dark_fwd`, `R_s_dark_rev`, `R_sh_dark_rev`
  - `n_dark_rev`, `J_s_dark_rev`, `n_dark_rev_R2`
  - `Voc_setpoint`, `Vmpp_setpoint`, `I_dark_during_stress_mean`
- Add Config sheet columns:
  - `equilibration_duration_h`, `equilibration_done`
  - `baseline_n_scans`, `baseline_rest_s`
  - `tiered_schedule_json`
- Keep the JV_#NNN_tXXXXh per-measurement sheet format unchanged.

Generic functions:
- `create_isos_tracking_file(folder, config)`
- `append_isos_measurement(tracking_path, meas)`
- `read_isos_config(path)`
- `read_isos_log(path)`
- `read_isos_jv_curves(path)`

**Compatibility wrappers** — keep `create_isos_d1_tracking_file`, `append_isos_d1_measurement`, `read_isos_d1_config`, `read_isos_d1_log`, `read_isos_d1_jv_curves` as thin shims that forward to the generic functions with `protocol_id="ISOS-D-1"`. Old D-1 xlsx files must continue to open.

### 3. CSV autosave module

Create `app/io/isos_autosave.py` with:

```python
class CSVStream:
    def __init__(self, path: str, columns: list[str]): ...
    def open(self): ...            # writes header
    def append(self, row: dict): ...  # atomic write + fsync
    def close(self): ...

def consolidate_csv_into_xlsx(
    csv_path: str,
    xlsx_path: str,
    sheet_name: str,
) -> None:
    """Appends CSV rows as a new sheet in the xlsx, then renames
    the CSV to .consumed to mark it as archived."""
```

### 4. Wire the registry into the GUI

Update `app/gui/main_window.py`:
- Replace the hard-coded `["ISOS-D-1"]` combobox values at line ~3990 with `list_protocol_labels()`. Keep the rest of the ISOS panel untouched.
- Replace the import of `isos_exporter` with `isos_tracking`, but call the legacy D-1 wrapper names (since they still exist). Verify D-1 still runs end-to-end.

### 5. Tests (hardware-free)

Follow the patterns in `tests/test_isos_exporter.py`:
- `tests/test_isos_tracking.py` — round-trip create/append/read with `protocol_id="ISOS-D-1"`, `"ISOS-L-1"`, `"ISOS-V-1"`. Verify the old D-1 wrapper functions still work and that loading an existing D-1 xlsx file (use a fixture) still returns the same data.
- `tests/test_isos_autosave.py` — open CSVStream, append 100 rows, close, consolidate into a fresh xlsx, read back and verify all rows match.

## Do NOT

- Add new measurement modes or runner classes (that's session 3).
- Touch `app/measurement/pure_mpp_tracker.py` or `mpp_tracker.py`.
- Modify the ISOS-D-1 measurement flow in `run_isos_d1_measurement()`.

## Acceptance

- `pytest tests/ -v -m "not windows_only and not gui"` is green.
- Manually loading an existing D-1 xlsx file in the GUI still works (run the app, open Data Viewer if needed).
- The dropdown still shows only "ISOS-D-1" because the registry only contains D-1 — that's intentional.
