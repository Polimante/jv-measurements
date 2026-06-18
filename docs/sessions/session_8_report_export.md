# Session 8 — ISOS Report Export (PDF + xlsx)

## Context

You are working on the **jv-measurement** project. Sessions 1–7 are complete: D-1, L-1, V-1 all implemented and tested, stability metrics computed, crash recovery and polish landed. This final session adds one-click export of a publication-ready ISOS report.

**Read first:**
- `CLAUDE.md` (post-session-7 version)
- `app/io/isos_tracking.py`
- `app/measurement/stability_metrics.py`
- `app/gui/main_window.py` (ISOS panel layout)

## Reference

**Khenkin et al. Nat Energy 2020, §5 "Reporting standards"** — defines the minimum content for ISOS-compliant reporting. The community-standard "stability scorecard" includes: protocol metadata, normalized PCE(t) plot with T80/Ts80 markers, parameter evolution plots, initial vs final J-V comparison, and a metrics summary table.

## Dependencies

Add to `requirements.txt`:
- `reportlab>=4.0` — PDF generation
- `matplotlib` (already present)
- `openpyxl` (already present)

## Tasks

### 1. Report generator module

Create `app/io/isos_report.py`:

```python
def generate_isos_report(
    tracking_path: str,
    output_path: str,
    format: str = "pdf",         # "pdf" or "xlsx"
    include_baseline: bool = True,
    include_spo_traces: bool = False,
    include_stress_traces: bool = False,
) -> None:
    """Generate a publication-ready ISOS report from a tracking file."""
```

### 2. PDF layout

Multi-page PDF using reportlab + matplotlib (render figures to BytesIO, embed):

**Page 1 — Cover**
- Title: `f"ISOS Stability Report — {protocol_id}"`
- Sample name, cell area
- Experiment dates (start → end), total duration_h
- Protocol ID, light source, T/RH metadata (if recorded)
- Software version + git commit hash (read from `APP_VERSION` constant + `subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'])`)

**Page 2 — Key metrics scorecard**
Large formatted table:
```
PCE_0  = X.XX ± X.XX %    (median of baseline scans, N = N_scans)
T80    = XXX h            [reached / not reached]
T95    = XXX h            [reached / not reached]
T_S    = XXX h            (burn-in: auto / manual / fallback)
PCE_S  = X.XX %
Ts80   = XXX h            (from T_S)
n_1000 = X.XX             (extrapolated ±X.XX) or (measured)
Total scans completed: N
Failure detected: yes/no  (reason: …)
```

**Page 3 — PCE(t) main plot**
- Normalized PCE_canonical vs elapsed_h
- Overlays: horizontal lines at 0.80, 0.95
- Vertical lines at T_S, T80, T95, T_S+Ts80
- Annotation showing PCE_S level

**Page 4 — Parameter evolution**
2×2 grid:
- Voc(t)
- Jsc(t)
- FF(t)
- R_s(t) / R_sh(t) overlaid (twin axis or two panels)

**Page 5 (V-1 only) — Dark parameter evolution**
2×2 grid:
- n_dark(t)
- J_s_dark(t) (log y)
- R_sh_dark(t) (log y)
- HI_dark(t)

**Page 6 — Initial vs final J-V comparison**
Two subplots:
- Illuminated J-V: scan #1 vs scan #N overlaid (fwd + rev)
- Dark J-V (V-1 only): same comparison

Parameter delta table below:
```
              Initial → Final    Δ%
Voc           1.10 V → 0.92 V    -16.4%
Jsc           22.1 → 21.4 mA/cm² -3.2%
FF            76.2% → 64.8%      -15.0%
PCE           18.5% → 12.7%      -31.4%
```

**Page 7 — Configuration appendix**
Full dump of Config sheet contents as a 2-column table (key | value).

### 3. xlsx format alternative

Same content but as new sheets appended to the tracking file:
- `Report_Cover`
- `Report_Metrics`
- `Report_Plots` (with embedded matplotlib charts saved as PNG and inserted via openpyxl.drawing.image.Image)
- `Report_Config`

### 4. GUI button

Add a "📊 Export ISOS Report…" button to the ISOS panel header (next to "Run ISOS" or in the Experiment tab).

Click flow:
1. Opens a small dialog:
   - Format: PDF (radio) / xlsx (radio)
   - Output path: file picker with default `{tracking_file_basename}_report.{pdf,xlsx}`
   - Checkboxes: include baseline scans, include SPO traces, include stress traces
2. On confirm: dispatches `generate_isos_report` on a background thread (heavy plot rendering).
3. Status bar shows: `"Generating report… (rendering plots)"` then `"Report saved to: {path}"` with a clickable link to open the file.

Use `root.after` to marshal status updates from the worker thread.

### 5. Tests

`tests/test_isos_report.py`:
- Generate a synthetic tracking file with 50 scans of known decay curve. Call `generate_isos_report(format="pdf")`. Verify the PDF exists, is non-empty, and contains expected text via `pdftotext` (fallback: just check size > 50 KB).
- Same for `format="xlsx"`: verify expected sheets exist with expected cell values.
- Edge cases:
  - Incomplete experiment (no T80 reached) → report still generates, shows "not reached" markers.
  - Failure detected mid-run → report shows failure reason on cover page.
  - Baseline disabled → report shows "PCE_0 from first scan (no baseline)".
  - V-1 vs L-1 → V-1 report includes Page 5 (dark params), L-1 skips it.

### 6. CLAUDE.md section

Add to the "ISOS Protocols" section:

```markdown
### ISOS Report Export

Click "📊 Export ISOS Report…" in the ISOS panel to generate a
publication-ready PDF or xlsx summary of any completed (or in-progress)
experiment. The PDF contains 6–7 pages: cover, metrics scorecard,
PCE(t) plot with T80/Ts80 markers, parameter evolution, dark J-V
evolution (V-1 only), initial-vs-final J-V comparison, and config
appendix. Generated via reportlab + matplotlib in a background thread.
```

## Do NOT

- Add new measurement protocols.
- Modify the tracking file format (sessions 1 and 5 set it).
- Block the GUI thread during report generation — always background.

## Acceptance

- For a completed L-1 or V-1 experiment, one click produces a publication-quality PDF in under 10 seconds.
- The report opens correctly in standard PDF viewers (Edge, Chrome, Adobe).
- All metrics in the report match what the live GUI displayed.
- xlsx format embeds plots as images and is openable in Excel.
- Tests pass with synthetic data covering edge cases.
