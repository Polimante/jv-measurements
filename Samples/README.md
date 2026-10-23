# Sample data files

These are **example measurement files** so you can explore the software without
running real hardware. Every file was produced by a physics-based simulation
(the project's single-diode `DummyCell` model) and written by the *same* export
code a real measurement uses — so they open and plot exactly like genuine data.

Open any file two ways:

- **As a spreadsheet** (Excel / LibreOffice) to see the raw data layout, headers
  and per-scan sheets.
- **In the software** to see how it is plotted and interpreted.

> These are synthetic demonstration files, not real device measurements.

## How to open each file in the app

| File | What it shows | How to open in the app |
|---|---|---|
| `01_Illuminated_JV.xlsx` | Illuminated J-V with hysteresis + auto single-diode fit | **File → Open Data File…** Then try the **Single-Diode Fit** button. |
| `02_Dark_JV.xlsx` | Dark J-V (isolated diode) | **File → Open Data File…** Then try the **Dark Ideality Fit** button. |
| `03_LightIntensity_JV.xlsx` | J-V at 0.1–1 sun + light-ideality (n_idL) analysis | **File → Open Data File…** |
| `04_StabilizedCurrent.xlsx` | Stabilized current/PCE vs time | **File → Open Data File…** |
| `05_MPP_Tracking_stable.xlsx` | JV-SPO cycling, stable device | **File → Open Data File…** |
| `05_MPP_Tracking_burnin.xlsx` | JV-SPO cycling, efficiency rises then plateaus (burn-in) | **File → Open Data File…** |
| `06_PureMPP.xlsx` | Continuous perturb-and-observe MPP tracking | **File → Open Data File…** |
| `07_ISOS_D1.xlsx` | ISOS-D-1 dark storage, ~28 days of periodic J-V | ISOS panel (**Alt+F9**) → **Load Experiment** |
| `08_ISOS_L1_stable.xlsx` | ISOS-L-1 light soak, stable device (no degradation) | ISOS panel (**Alt+F9**) → **Load Experiment** |
| `08_ISOS_L1_degrading.xlsx` | ISOS-L-1 light soak, efficiency decays through T80 | ISOS panel (**Alt+F9**) → **Load Experiment** |
| `09_ISOS_V1_degrading.xlsx` | ISOS-V-1 dark bias stress with periodic dark + illuminated J-V | ISOS panel (**Alt+F9**) → **Load Experiment** |

The two **degrading** ISOS files cross the T80/T95 stability thresholds, so the
ISOS **Stability Metrics** panel will show real T80 / T95 / T_S values; the
**stable** files show those metrics as "not reached".

> **Note on ISOS-L-1 / V-1 plots.** For the continuous protocols (L-1, V-1),
> **Load Experiment** restores the experiment configuration and the measurement
> **log table**, but the plot panel stays blank: those live plots are drawn
> incrementally while an experiment runs, and the app does not rebuild them from
> a saved file (only ISOS-D-1 reconstructs its plot on load). The full data is
> still there — open the file in Excel to see every per-scan J-V sheet, the SPO
> traces, the stress autosave and the `ISOS_Metrics` sheet. ISOS-D-1
> (`07_ISOS_D1.xlsx`) **does** repaint its plot on load.

## Regenerating these files

Run from the project root:

```bash
python docs/_gen_samples.py
```

The generator (`docs/_gen_samples.py`) is deterministic — re-running it
reproduces the same files.
