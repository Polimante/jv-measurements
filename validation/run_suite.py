"""Validation suite orchestrator.

Usage::

    python -m validation.run_suite --config validation/config.yaml

Outputs:
    validation/results/results.json    -machine-readable full results
    validation/results/report.md       -human-readable Markdown summary
    validation/results/figures/*.png   -300 dpi PNG figures
    validation/results/figures/*.pdf   -vector PDF figures

Exits with code 0 if all tests pass, 1 if any test fails.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import yaml


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _ensure_dirs(config: dict) -> None:
    Path(config["output"]["results_dir"]).mkdir(parents=True, exist_ok=True)
    Path(config["output"]["figures_dir"]).mkdir(parents=True, exist_ok=True)


class _JSONEncoder(json.JSONEncoder):
    """Handle numpy scalars and non-finite floats in JSON output."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            if np.isnan(obj) or np.isinf(obj):
                return None
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)

    def encode(self, o: Any) -> str:
        # Replace float nan/inf at the Python level too
        if isinstance(o, float) and (np.isnan(o) or np.isinf(o)):
            return "null"
        return super().encode(o)


def _sanitise(obj: Any) -> Any:
    """Recursively replace nan/inf with None for JSON serialisation."""
    if isinstance(obj, float):
        return None if (np.isnan(obj) or np.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitise(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitise(v) for v in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        v = float(obj)
        return None if (np.isnan(v) or np.isinf(v)) else v
    if isinstance(obj, np.ndarray):
        return _sanitise(obj.tolist())
    return obj


# ── Report generation ─────────────────────────────────────────────────────────

def _pass_symbol(p: bool | None) -> str:
    return "PASS" if p else "FAIL"


def _fmt(v: float | None, digits: int = 4) -> str:
    if v is None:
        return "N/A"
    return f"{v:.{digits}f}"


def _write_report(results: list[dict], config: dict, elapsed_s: float) -> str:
    """Generate a Markdown report string from all test results.

    Args:
        results: List of result dicts from each test module.
        config: Top-level config dict.
        elapsed_s: Total wall-clock time (seconds).

    Returns:
        Markdown string.
    """
    lines: list[str] = []
    lines.append("# Validation Suite Report\n")
    lines.append(f"**Seed**: `{config['seed']}`  ")
    lines.append(f"**Total runtime**: {elapsed_s:.1f} s\n")
    lines.append("---\n")

    # ── Summary table ─────────────────────────────────────────────────────────
    lines.append("## Summary\n")
    lines.append("| Test | Overall |")
    lines.append("|------|---------|")
    for r in results:
        lines.append(f"| {r['name']} | {_pass_symbol(r.get('overall_pass'))} |")
    lines.append("")

    all_pass = all(r.get("overall_pass", False) for r in results)
    lines.append(f"\n**Suite result: {_pass_symbol(all_pass)}**\n")
    lines.append("---\n")

    # ── Test A: JV extraction ─────────────────────────────────────────────────
    r_jv = next((r for r in results if "Extraction" in r.get("name", "")), None)
    if r_jv:
        lines.append("## Test A -JV Parameter Extraction\n")
        lines.append(f"Cells: {r_jv.get('n_cells')}  |  "
                     f"Noise levels: {r_jv.get('noise_levels_tested')}\n")
        lines.append("| Noise | Parameter | Mean rel. error | Max rel. error | Threshold | Result |")
        lines.append("|-------|-----------|----------------|----------------|-----------|--------|")
        for noise, param_dict in r_jv.get("per_noise_results", {}).items():
            for param, stats in param_dict.items():
                mean_e = stats.get("mean_rel_error")
                max_e  = stats.get("max_rel_error")
                thr    = stats.get("threshold")
                lines.append(
                    f"| {float(noise)*100:.1f} % | {param} "
                    f"| {_fmt(mean_e*100 if mean_e else None, 3)} % "
                    f"| {_fmt(max_e*100 if max_e else None, 3)} % "
                    f"| {_fmt(thr*100 if thr else None, 1)} % "
                    f"| {_pass_symbol(stats.get('pass'))} |"
                )
        lines.append("")

    # ── Test B: Single-diode fit ──────────────────────────────────────────────
    r_sd = next((r for r in results if "Single-Diode" in r.get("name", "")), None)
    if r_sd:
        lines.append("## Test B -Single-Diode Model Fitting\n")
        lines.append(f"Cells: {r_sd.get('n_cells')}  |  "
                     f"Noise levels: {r_sd.get('noise_levels_tested')}\n")
        lines.append("| Noise | Parameter | Mean rel. error | SD | Threshold | Space | Result |")
        lines.append("|-------|-----------|----------------|-----|-----------|-------|--------|")
        for noise, param_dict in r_sd.get("per_noise_results", {}).items():
            for param, stats in param_dict.items():
                mean_e = stats.get("mean_rel_error")
                sd_e   = stats.get("sd_rel_error")
                thr    = stats.get("threshold")
                space  = "log10" if stats.get("log_space") else "linear"
                # Only mark result at 1% noise level (acceptance criterion)
                show_result = abs(float(noise) - 0.01) < 1e-9
                result_str  = _pass_symbol(stats.get("pass")) if show_result else "—"
                lines.append(
                    f"| {float(noise)*100:.1f} % | {param} "
                    f"| {_fmt(mean_e, 4) if mean_e is not None else 'N/A'} "
                    f"| {_fmt(sd_e, 4) if sd_e is not None else 'N/A'} "
                    f"| {_fmt(thr, 2) if thr is not None else 'N/A'} "
                    f"| {space} | {result_str} |"
                )
        lines.append("")

    # ── Test C: MPP tracking ──────────────────────────────────────────────────
    r_mpp = next((r for r in results if "MPP" in r.get("name", "")), None)
    if r_mpp:
        lines.append("## Test C -MPP Tracking Accuracy\n")
        for sub_name, sub in r_mpp.get("per_subtest_results", {}).items():
            lines.append(f"### {sub_name.replace('_', ' ').title()}\n")
            for k, v in sub.items():
                if k == "pass":
                    continue
                lines.append(f"- **{k}**: {_fmt(v) if isinstance(v, float) else v}")
            lines.append(f"- **Result**: {_pass_symbol(sub.get('pass'))}\n")

    # ── Test D: Repeatability ─────────────────────────────────────────────────
    r_rep = next((r for r in results if "Repeat" in r.get("name", "")), None)
    if r_rep:
        lines.append("## Test D -Repeatability\n")
        lines.append(f"Repeats: {r_rep.get('n_repeats')}\n")
        lines.append("| Parameter | Mean | SD | CV | Threshold | Result |")
        lines.append("|-----------|------|-----|-----|-----------|--------|")
        for param, stats in r_rep.get("param_results", {}).items():
            cv  = stats.get("cv")
            thr = stats.get("threshold")
            lines.append(
                f"| {param} "
                f"| {_fmt(stats.get('mean'), 4)} "
                f"| {_fmt(stats.get('sd'), 6)} "
                f"| {_fmt(cv*100 if cv else None, 6)} % "
                f"| {_fmt(thr*100 if thr else None, 1)} % "
                f"| {_pass_symbol(stats.get('pass'))} |"
            )
        lines.append("")

    # ── Test E: ISOS-D-1 ─────────────────────────────────────────────────────
    r_isos = next((r for r in results if "ISOS" in r.get("name", "")), None)
    if r_isos:
        lines.append("## Test E -ISOS-D-1 Stability Simulation\n")
        lines.append(f"τ = {r_isos.get('tau_h')} h\n")
        for sub_name, sub in r_isos.get("per_subtest_results", {}).items():
            lines.append(f"### {sub_name.replace('_', ' ').title()}\n")
            for k, v in sub.items():
                if k == "pass":
                    continue
                lines.append(f"- **{k}**: {_fmt(v) if isinstance(v, float) else v}")
            lines.append(f"- **Result**: {_pass_symbol(sub.get('pass'))}\n")

    lines.append("---\n")
    lines.append("*Generated by `python -m validation.run_suite`*\n")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    """Run the full validation suite and write all artifacts.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code: 0 = all pass, 1 = at least one failure.
    """
    parser = argparse.ArgumentParser(
        description="Run the JV-Measurements validation suite."
    )
    parser.add_argument(
        "--config", default="validation/config.yaml",
        help="Path to config.yaml (default: validation/config.yaml)"
    )
    args = parser.parse_args(argv)

    config = _load_config(args.config)
    _ensure_dirs(config)

    # Import here so matplotlib backend is not loaded at module level
    from validation import (
        test_jv_extraction,
        test_single_diode_fit,
        test_mpp_tracking,
        test_repeatability,
        test_isos_d1,
        reporting,
    )

    reporting.setup_style(config)

    tests = [
        ("Test A: JV Extraction",       test_jv_extraction),
        ("Test B: Single-Diode Fit",    test_single_diode_fit),
        ("Test C: MPP Tracking",        test_mpp_tracking),
        ("Test D: Repeatability",       test_repeatability),
        ("Test E: ISOS-D-1",            test_isos_d1),
    ]

    results: list[dict] = []
    t0_wall = time.monotonic()

    for label, module in tests:
        print(f"\n{'-'*60}")
        print(f"  {label}")
        print(f"{'-'*60}")
        t_start = time.monotonic()
        try:
            result = module.run(config)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            result = {
                "name":         label,
                "overall_pass": False,
                "error":        str(exc),
            }
        elapsed = time.monotonic() - t_start
        symbol  = "[PASS]" if result.get("overall_pass") else "[FAIL]"
        print(f"  {symbol}  {result.get('name', label)}  ({elapsed:.1f} s)")
        results.append(result)

    total_elapsed = time.monotonic() - t0_wall

    # ── Print pass/fail table to stdout ──────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"{'VALIDATION SUITE RESULTS':^60}")
    print(f"{'='*60}")
    col_w = 42
    print(f"  {'Test':<{col_w}} {'Result'}")
    print(f"  {'-'*col_w} {'------'}")
    for r in results:
        sym = "PASS" if r.get("overall_pass") else "FAIL"
        print(f"  {r.get('name', '?'):<{col_w}} {sym}")
    all_pass = all(r.get("overall_pass", False) for r in results)
    print(f"{'='*60}")
    print(f"  Total: {'ALL PASS' if all_pass else 'FAILURES DETECTED'}  "
          f"({total_elapsed:.1f} s)")
    print(f"{'='*60}\n")

    # ── Write results.json ────────────────────────────────────────────────────
    results_dir  = Path(config["output"]["results_dir"])
    results_path = results_dir / "results.json"
    payload = {
        "seed":            config["seed"],
        "total_elapsed_s": total_elapsed,
        "all_pass":        all_pass,
        "tests":           _sanitise(results),
    }
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, cls=_JSONEncoder)
    print(f"Results written -> {results_path}")

    # ── Write report.md ───────────────────────────────────────────────────────
    report_path = results_dir / "report.md"
    report_text = _write_report(results, config, total_elapsed)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)
    print(f"Report  written -> {report_path}")

    # ── Generate figures ──────────────────────────────────────────────────────
    print("\nGenerating figures...")
    r_map = {r.get("name", ""): r for r in results}

    r_jv = next((r for r in results if "Extraction" in r.get("name", "")), None)
    if r_jv:
        fig = reporting.plot_jv_scatter(r_jv)
        reporting.save_figure(fig, "jv_scatter", config)
        import matplotlib.pyplot as plt
        plt.close(fig)
        print("  -> jv_scatter.png / .pdf")

    r_sd = next((r for r in results if "Single-Diode" in r.get("name", "")), None)
    if r_sd:
        fig = reporting.plot_bland_altman(r_sd)
        reporting.save_figure(fig, "bland_altman_sdm", config)
        plt.close(fig)
        print("  -> bland_altman_sdm.png / .pdf")

        fig = reporting.plot_error_heatmap(r_sd, "Single-Diode Fit: Mean Relative Error (%)")
        reporting.save_figure(fig, "sdm_error_heatmap", config)
        plt.close(fig)
        print("  -> sdm_error_heatmap.png / .pdf")

    r_mpp = next((r for r in results if "MPP" in r.get("name", "")), None)
    if r_mpp:
        fig = reporting.plot_mpp_tracking(r_mpp)
        reporting.save_figure(fig, "mpp_tracking", config)
        plt.close(fig)
        print("  -> mpp_tracking.png / .pdf")

    r_rep = next((r for r in results if "Repeat" in r.get("name", "")), None)
    if r_rep:
        fig = reporting.plot_repeatability(r_rep)
        reporting.save_figure(fig, "repeatability", config)
        plt.close(fig)
        print("  -> repeatability.png / .pdf")

    r_isos = next((r for r in results if "ISOS" in r.get("name", "")), None)
    if r_isos:
        fig = reporting.plot_isos_d1(r_isos)
        reporting.save_figure(fig, "isos_d1_stability", config)
        plt.close(fig)
        print("  -> isos_d1_stability.png / .pdf")

    print("\nAll artifacts written.")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
