# Session 6 — Stability metrics: T80, T95, T_S, Ts80, n_1000

## Context

You are working on the **jv-measurement** project. Sessions 1–5 implemented ISOS-D-1, L-1, and V-1 with full data acquisition + persistence. This session adds the standard stability metrics: their computation, plot overlays, GUI panel, and persistence to a new sheet.

**Read first, end-to-end:**
- `CLAUDE.md`
- `app/measurement/isos_continuous_runner.py`
- `app/io/isos_tracking.py`
- `app/gui/mpp_plot_factory.py`
- `app/gui/main_window.py` — ISOS panel and the History treeview

## References (consult before implementing)

- **Khenkin et al., Nat Energy 5, 35 (2020)** — ISOS consensus. Defines T80, T_S, Ts80 conceptually. Does NOT prescribe a burn-in detection algorithm.
- **Fluxim blog**: https://www.fluxim.com/research-blogs/isos-protocols-stability-perovskite-solar-cells — defines η₁₀₀₀ and mentions T95 as IEC-aligned emerging standard.

## Metric definitions (DO NOT CONFUSE)

| Metric | Definition |
|---|---|
| **T80** | First time `PCE(t) / PCE(0) ≤ 0.80`. PCE(0) = baseline median from session 3's `run_baseline_conditioning`. |
| **T95** | First time `PCE(t) / PCE(0) ≤ 0.95`. Same canonical PCE column. IEC-aligned. |
| **T_S** | End of burn-in (stabilization time). Transition from fast initial decay to slower stable decay. |
| **PCE_S** | PCE at t = T_S (or mean of PCE in a short window centered on T_S). |
| **Ts80** | Time, measured **from T_S**, at which PCE drops to `0.80 × PCE_S`. Absolute endpoint time = T_S + Ts80. |
| **n_1000** | `PCE(1000 h) / PCE(0)`. If experiment shorter than 1000 h, **extrapolate** using slope of stable region and flag as extrapolated with uncertainty. |

**Canonical PCE column**: prefer `PCE_SPO` (stabilized power output, from session 3 helper). Fall back to `PCE_rev` if SPO disabled or unavailable.

If never reached, report metric as `None` with `*_reached=False`.

## Tasks

### 1. stability_metrics module

Create `app/measurement/stability_metrics.py`:

```python
from dataclasses import dataclass

@dataclass
class StabilityMetrics:
    t80_h: float | None
    t80_reached: bool
    t95_h: float | None
    t95_reached: bool
    t_s_h: float | None
    pce_s: float | None
    ts80_h: float | None         # relative to T_S
    ts80_reached: bool
    n_1000: float | None
    n_1000_extrapolated: bool
    n_1000_confidence: float | None   # std error from regression
    burn_in_method: str          # "auto" | "manual" | "fallback"
    pce_column_used: str         # "PCE_SPO" or "PCE_rev"

def compute_stability_metrics(
    times_h,
    pce_values,
    pce_0,                        # explicit baseline (median from baseline conditioning)
    burn_in_mode: str = "auto",   # "auto" | "manual" | "fallback"
    manual_burn_in_h: float | None = None,
    extrapolate_n1000: bool = True,
    pce_column_used: str = "PCE_SPO",
) -> StabilityMetrics:
    ...
```

### 2. Implementation details

**Input validation**: sorted by time, no NaNs, len ≥ 2.

**T80 / T95**: compute `pce_norm = pce_values / pce_0`. Find first time where `pce_norm <= threshold` via linear interpolation between adjacent samples for sub-sample precision. If never crossed, set `*_reached=False`.

**T_S detection (mode="auto")**:
1. Smooth `pce_norm` with Savitzky-Golay filter, window = `min(11, max(5, len(t)//5))`, polynomial order 2.
2. Compute d²(pce_smoothed) / dt² via central differences.
3. T_S = time of the second derivative's most positive value (transition from concave-down fast decay → concave-up stable region).
4. **Fallback**: if T_S < 5% of total_duration or > 50% of total_duration → reject auto, use `T_S = 0.20 × total_duration`, set `burn_in_method="fallback"`.
5. If `mode="manual"`: use `manual_burn_in_h` directly, set `burn_in_method="manual"`.

**PCE_S**: mean of pce in window `[T_S − w, T_S + w]` where `w = max(0.5, 0.02 × total_duration)` hours.

**Ts80**: from T_S onwards, find first time where `pce / PCE_S <= 0.80`, return time relative to T_S.

**n_1000**:
- If `total_duration >= 1000`: interpolate pce at 1000 h, return `pce(1000) / pce_0`.
- Else (extrapolation needed): linear regression on the stable region (t > T_S), extrapolate to 1000 h. Confidence = std_err_slope × (1000 − t_end). Set `n_1000_extrapolated=True`.

### 3. Runtime hook

After each J-V scan, the runner calls `compute_stability_metrics()` on the cumulative log's `(elapsed_h, PCE_SPO_or_PCE_rev)` arrays and passes the result via a new callback `on_metrics_updated(metrics)` to the GUI.

### 4. Plot overlays

Extend `app/gui/mpp_plot_factory.py`:

```python
def overlay_stability_metrics(ax_pce, metrics, total_duration_h):
    """Draws on a PCE-vs-time axis:
      - horizontal dashed lines at 0.80 (red) and 0.95 (light blue)
      - horizontal dashed line at 0.80 × pce_s_norm (orange) — Ts80 ref
      - vertical line at T_S (gray dashed) — burn-in end
      - vertical line at T80 (red), T95 (light blue) if reached
      - vertical line at T_S + Ts80 (orange) if reached
      - text annotations near each marker showing the value
    """
```

Called after each scan-update on whichever PCE axis is showing the cumulative trend (works for L-1 MPP, L-1 OC sparse markers, and V-1 sparse markers).

### 5. Persistence — ISOS_Metrics sheet

Extend `app/io/isos_tracking.py`:

Add a sheet `ISOS_Metrics` with one row per scan, containing the rolling-best estimate of all stability metrics computed at that scan time. Columns:
- `scan_#`, `elapsed_h`
- `T80_h`, `T80_reached`
- `T95_h`, `T95_reached`
- `T_S_h`, `PCE_S`
- `Ts80_h`, `Ts80_reached`
- `n_1000`, `n_1000_extrapolated`, `n_1000_confidence`
- `burn_in_method`
- `pce_column_used`

Helper:
```python
def append_metrics_row(tracking_path, scan_n, elapsed_h, metrics):
    """Append a new row to ISOS_Metrics sheet. Creates the sheet if absent."""
```

Update `append_isos_measurement()` to also write the latest metrics row when called.

### 6. GUI panel

Below the existing History treeview in the ISOS panel, add a "Stability Metrics" `LabelFrame` showing live values:

```
T80   = 142.3 h ✓     T_S   = 18.5 h
T95   = 38.2 h ✓      PCE_S = 16.8 %
Ts80  = > experiment   n_1000 = 0.72 (extrap. ±0.04)
                        Burn-in: auto    [Override…]
```

- Bold green ✓ for reached metrics
- Gray "—" or "> experiment" for not-reached
- Small "?" icon with tooltip explaining each metric
- "Burn-in detection" row with radio buttons (Auto / Manual / Fallback) and an entry field for manual T_S. Triggers recompute + redraw within 200 ms when changed.
- "Override" button opens a small dialog to set manual T_S precisely.

### 7. Tests

`tests/test_stability_metrics.py` — synthetic PCE(t) curves:
- Monotonic exponential decay → verify T80 ≈ `ln(0.8) / decay_rate`.
- Burn-in (fast 5% drop) + stable plateau → verify T_S detection lands near the inflection within ±10% tolerance.
- Constant PCE → verify T80 = None, n_1000 = 1.0.
- Short experiment + extrapolation → verify n_1000 confidence grows with extrapolation distance.
- Single data point → verify graceful return (all None).

`tests/test_isos_metrics_integration.py` — run L-1 against dummy SMU with a synthetic decay model. Verify ISOS_Metrics sheet contains the expected number of rows and the final T80 is within 5% of analytical value.

`tests/test_metrics_persistence.py` — round-trip: compute → append_metrics_row → re-read → values match.

## Do NOT

- Recompute metrics in the GUI thread — always compute in the runner thread and pass via callback.
- Change PCE_SPO computation — that lives in session 3's helper.
- Add new protocols or runners.

## Acceptance

- All tests green.
- The ISOS plot panel shows the metric overlays correctly (horizontal + vertical lines + annotations).
- Toggling Auto / Manual / Fallback burn-in recomputes and redraws within 200 ms.
- ISOS_Metrics sheet is written on every scan completion.
- For V-1 with sparse data: metrics still compute correctly with as few as 3 scan points.
- Live GUI panel shows metric values with ✓/—/> markers as appropriate.
