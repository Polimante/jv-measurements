# Validation Suite

This directory contains a deterministic, publication-grade validation suite
for the **JV-Measurements** software.  It provides synthetic ground-truth
evidence that the software correctly extracts solar-cell parameters, fits
physical models, tracks the maximum power point, and records long-term
stability data.

**This suite is separate from `tests/`**: the unit and integration tests in
`tests/` run on every commit to catch regressions; this validation suite runs
deliberately (weekly in CI, or on demand) to generate figures and tables for
the accompanying manuscript.

---

## How to reproduce

```bash
# From the repository root
python -m validation.run_suite --config validation/config.yaml
```

All outputs go to `validation/results/`:

| File | Description |
|------|-------------|
| `results.json` | Machine-readable full results (all metrics, all cells) |
| `report.md` | Human-readable Markdown summary (pass/fail table) |
| `figures/jv_scatter.png / .pdf` | Extracted vs ground-truth scatter (Test A) |
| `figures/bland_altman_sdm.png / .pdf` | Bland-Altman for 5 SDM parameters (Test B) |
| `figures/sdm_error_heatmap.png / .pdf` | Relative error heatmap across noise levels (Test B) |
| `figures/mpp_tracking.png / .pdf` | Tracked PCE vs true MPP (Test C) |
| `figures/repeatability.png / .pdf` | 20 repeated measurements (Test D) |
| `figures/isos_d1_stability.png / .pdf` | PCE_norm vs time over 240 h (Test E) |

Two runs with the same `config.yaml` produce **byte-identical** `results.json`
(the `seed` key controls all random draws; DummySMU uses `sleep_scale=0` so
no wall-clock jitter enters the physics).

---

## Methodology

Every test uses only synthetic cells — no real hardware is required.

### Cell model

All cells are instances of `app.simulation.dummy_cell.DummyCell` backed by the
single-diode implicit equation:

```
J = J_ph - J0·(exp((V + J·Rs·1e-3)/(n·Vt)) - 1) - (V + J·Rs·1e-3)·1e3/Rsh
```

Units: J in mA/cm², V in V, Rs/Rsh in Ω·cm², Vt = kT/q at T = 298.15 K.
Sign convention: photocurrent positive (CLAUDE.md).

### Random cell sampling

50 cells are sampled from realistic perovskite parameter distributions:

| Parameter | Distribution | Range |
|-----------|-------------|-------|
| J_ph (mA/cm²) | Uniform | 18 – 24 |
| J0 (mA/cm²) | Log-uniform | 10⁻¹⁴ – 10⁻¹⁰ |
| n | Uniform | 1.1 – 2.0 |
| Rs (Ω·cm²) | Uniform | 1 – 15 |
| Rsh (Ω·cm²) | Log-uniform | ~500 – 50 000 |

All draws use `numpy.random.default_rng(seed)` from `config.yaml`.

### Noise model

Gaussian noise with σ = noise_level × J_sc is added to each current-density
point after scanning.  J_sc is the measured short-circuit current density of
the noiseless scan.

---

## Acceptance criteria

All thresholds are configurable in `config.yaml`.

### Test A — J-V Parameter Extraction

| Noise level | Parameter | Criterion | Metric |
|-------------|-----------|-----------|--------|
| 0 % | Voc, Jsc, FF, PCE | Mean relative error < **0.5 %** | (extracted − GT) / GT |
| 1 % | Voc, Jsc, FF, PCE | Mean relative error < **2.0 %** | (extracted − GT) / GT |

Ground truth is computed from a 0.5 mV-resolution grid of the same DummyCell
(no SMU overhead, no rounding), giving a benchmark independent of the
extraction code.

### Test B — Single-Diode Model Fitting

Thresholds reflect the known parameter identifiability limits of single-illumination
JV fitting (De Soto et al. 2006; Laudani et al. 2014; Boguslawski et al. 2022):

| Parameter | Criterion at 1 % noise | Metric | Physical reason |
|-----------|----------------------|--------|-----------------|
| J_ph | Mean relative error < **5 %** | \|fit − GT\| / GT | Well-constrained by Jsc |
| n | Mean relative error < **25 %** | \|fit − GT\| / GT | Correlated with J0 |
| Rs | Mean relative error < **200 %** | \|fit − GT\| / GT | Correlated with n; dark JV needed |
| J0 | Mean log-error < **1.0** log₁₀ | \|log₁₀(fit/GT)\| | Spans 4 orders of magnitude |
| Rsh | Mean log-error < **0.5** log₁₀ | \|log₁₀(fit/GT)\| | Less correlated, still uncertain |

J0 and Rsh are evaluated in log₁₀ space because they span multiple orders of
magnitude and are internally optimised in log-space by `fit_single_diode`.
The Bland-Altman figures quantify the actual distribution of errors and are
the primary evidence for the manuscript; the pass/fail thresholds are a
secondary quality gate.

Bland-Altman plots are generated for all 5 parameters across all noise levels.

### Test C — MPP Tracking Accuracy

| Sub-test | Criterion | Metric |
|----------|-----------|--------|
| Stable cell (ideal_stable) | Mean SPO PCE within **1 %** of true MPP | \|mean_SPO − MPP_true\| / MPP_true |
| Decaying cell (τ = 100 h) | RMSE of PCE_norm vs exp(−t/τ) < **3 %** | RMSE over 30 time points |

The decaying cell is simulated by advancing the cell's virtual clock with
`DummyCell.set_time_offset()` at each measurement point.

### Test D — Repeatability

| Parameter | Criterion | Metric |
|-----------|-----------|--------|
| Voc, Jsc | CV < **1 %** | σ / mean |
| FF, PCE  | CV < **2 %** | σ / mean |

20 repeated scans of the same ideal_stable cell without noise.

### Test E — ISOS-D-1 Stability Simulation

| Sub-test | Criterion |
|----------|-----------|
| Physics: 240 h tracking | RMSE(PCE_norm vs exp(−t/τ)) < **3 %** |
| File integrity | Row count, PCE_norm values, and elapsed_h all read back correctly |

PCE_norm is the ratio PCE(t) / PCE(t=0), consistent with the ISOS consensus
(Khenkin et al., *Nature Energy* 2020, 5, 35).

---

## How results map to the manuscript

| Figure in paper | Validation output |
|----------------|-------------------|
| Fig. 2 — extraction accuracy | `figures/jv_scatter.png` |
| Fig. 3 — SDM fit Bland-Altman | `figures/bland_altman_sdm.png` |
| Fig. 4 — MPP tracking fidelity | `figures/mpp_tracking.png` |
| Fig. 5 — measurement noise floor | `figures/repeatability.png` |
| Fig. 6 — stability tracking | `figures/isos_d1_stability.png` |
| Table 1 — pass/fail summary | `report.md` summary table |
| Table 2 — SDM fit errors | `report.md` Test B section |

---

## Configuration

All tunable parameters live in `config.yaml`:

```yaml
seed: 42                          # reproducibility seed
jv_extraction:
  n_cells: 50                     # number of random cells
  noise_levels: [0.0, 0.01]       # Gaussian noise fractions
  thresholds:
    no_noise:   {voc: 0.005, ...}  # 0.5 %
    with_noise: {voc: 0.020, ...}  # 2.0 %
single_diode_fit:
  threshold_1pct_noise: 0.05      # 5 % relative error
  log_threshold_1pct_noise: 0.30  # for J0/Rsh in log10 space
isos_d1:
  tau_h:          100.0            # decay time constant (h)
  n_measurements: 240              # simulated hours
```

To reproduce with tighter or looser criteria, edit `config.yaml` and re-run.

---

## CI integration

See `.github/workflows/validation.yml`.  The suite runs:

- **Weekly** (every Monday at 06:00 UTC)
- **On every release tag** matching `v*.*.*`
- **On manual dispatch** (`workflow_dispatch`)

Results are uploaded as a GitHub Actions artifact named
`validation-results-<run-id>`.
