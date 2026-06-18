# ISOS Protocols — Implementation Sessions

This directory contains the planned breakdown of work to extend the
**jv-measurement** software with ISOS-L-1 and ISOS-V-1 protocols (per
Khenkin et al., Nat Energy 5, 35 (2020)), full stability metrics, and
publication-ready reporting.

Each session is intended for a fresh Claude Code session (Sonnet 4.6
recommended) and is self-contained — the prompt explains the goal,
the files to read, the tasks, what NOT to do, and acceptance criteria.

## Recommended order

| # | File | Topic | Status |
|---|---|---|---|
| 1 | [session_1_tracking_refactor.md](session_1_tracking_refactor.md) | Generic tracking module + protocol registry | Foundational |
| 2 | [session_2_oc_mode.md](session_2_oc_mode.md) | OC mode in PureMPPTracker / MPPTracker + shared MPP settings frame | |
| 3 | [session_3_continuous_runner.md](session_3_continuous_runner.md) | Continuous runner + 3 plot layouts + helpers (relaxation, baseline, SPO, failure detection, tiered schedule) | |
| 4 | [session_4_isos_l1.md](session_4_isos_l1.md) | ISOS-L-1 GUI + integration | |
| 5 | [session_5_isos_v1.md](session_5_isos_v1.md) | ISOS-V-1 with dark JV analysis (R_s, R_sh, n, J_0) + tiered schedule + scan rate warning | |
| 6 | [session_6_stability_metrics.md](session_6_stability_metrics.md) | T80, T95, T_S, Ts80, n_1000 — computation, plot overlays, GUI panel, persistence | |
| 7 | [session_7_polish.md](session_7_polish.md) | Crash recovery dialog, UX polish, integration tests, CLAUDE.md docs | |
| 8 | [session_8_report_export.md](session_8_report_export.md) | One-click ISOS Report Export (PDF + xlsx) | |

## Dependencies between sessions

```
Session 1  ──┐
Session 2  ──┤
             ├─→ Session 3 ──┬─→ Session 4 ──┐
             │               │                ├─→ Session 6 ──→ Session 7 ──→ Session 8
             │               └─→ Session 5 ──┘
```

- Sessions 1 and 2 can run in any order (independent).
- Session 3 requires both 1 and 2.
- Sessions 4 and 5 require 3 (and ideally each other for reuse, but can be done in parallel if necessary).
- Session 6 requires 4 and 5 to consume actual measurement data.
- Sessions 7 and 8 should run last.

## Features per session

### Tier-1 robustness features (Khenkin §3, §4)

| Feature | Implemented in |
|---|---|
| Pre-scan relaxation (60s OC dark) | Session 3 helper, used by 4 and 5 |
| Baseline conditioning (4 scans median) | Session 3 helper, used by 4 and 5 |
| Failure detection + auto-stop | Session 3 helper, GUI in 4 and 5 |

### Tier-2 robustness features

| Feature | Implemented in |
|---|---|
| Stabilized Power Output (20s SPO post-scan) | Session 3 helper, used by 4 and 5 |
| Dark equilibration (12-24h initial) | Session 3 helper, V-1 GUI in 5 |
| Hysteresis Index in dark (HI_dark) | Session 5 |

### Tier-3 polish

| Feature | Implemented in |
|---|---|
| ISOS Report Export (PDF/xlsx) | Session 8 |

## Future protocols (not in this plan)

Marked unimplemented in CLAUDE.md and the protocol registry — require
additional hardware:

- ISOS-D-2, D-3 — high-T storage, damp heat (need thermal chamber)
- ISOS-L-2, L-3 — T-controlled / RH-controlled light soaking
- ISOS-LC-1, LC-2, LC-3 — light cycling (need motorized shutter for automation)
- ISOS-T-1, T-2, T-3 — thermal cycling (need T-controlled enclosure)
- ISOS-V-2, V-3 — alternative bias loads (Eg/q, Jsc, negative bias)
- ISOS-O-1, O-2, O-3 — outdoor (need portable rig + GPS + weather sensing)

An **optional Session 9** ("V-1 Advanced Diagnostics") can be drafted
later to add post-stress relaxation transient measurement, continuous
leakage current monitoring under reverse bias, and pre/post dynamic
hysteresis comparison — none required for ISOS-V-1 baseline compliance.
