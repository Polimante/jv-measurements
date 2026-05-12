"""Publication-quality figure generation for the validation suite.

All figures are saved as 300 dpi PNG and vector PDF.
Color palette: Okabe-Ito (colorblind-safe, 8 colors).
Font: Arial (with DejaVu Sans fallback for CI environments).

Usage::

    from validation.reporting import setup_style, save_figure
    setup_style(config)          # call once at the top of run_suite.py
    fig = plot_jv_scatter(result)
    save_figure(fig, "jv_scatter", config)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import numpy as np

# ── Okabe-Ito colorblind-safe palette ─────────────────────────────────────────
# Source: Okabe & Ito (2008), "Color Universal Design"
PALETTE: list[str] = [
    "#0072B2",  # blue
    "#E69F00",  # orange
    "#009E73",  # bluish green
    "#D55E00",  # vermillion
    "#CC79A7",  # reddish purple
    "#56B4E9",  # sky blue
    "#F0E442",  # yellow
    "#000000",  # black
]


def setup_style(config: dict) -> None:
    """Apply global rcParams for publication-quality figures.

    Args:
        config: Top-level config dict (reads ``output.font_family``).
    """
    requested = config.get("output", {}).get("font_family", "Arial")
    available = {f.name for f in fm.fontManager.ttflist}
    font_family = requested if requested in available else "DejaVu Sans"

    matplotlib.rcParams.update({
        "font.family":           font_family,
        "font.size":             10,
        "axes.titlesize":        11,
        "axes.labelsize":        10,
        "xtick.labelsize":        9,
        "ytick.labelsize":        9,
        "legend.fontsize":        9,
        "figure.dpi":           150,
        "savefig.dpi":          300,
        "axes.spines.top":      False,
        "axes.spines.right":    False,
        "axes.linewidth":        0.8,
        "xtick.major.width":     0.8,
        "ytick.major.width":     0.8,
        "lines.linewidth":       1.5,
        "lines.markersize":      5,
        "errorbar.capsize":      3,
        "figure.constrained_layout.use": True,
    })


def save_figure(fig: matplotlib.figure.Figure, name: str, config: dict) -> None:
    """Save *fig* as PNG and PDF in the configured figures directory.

    Args:
        fig: Matplotlib figure to save.
        name: Base filename (no extension).
        config: Top-level config dict.
    """
    figures_dir = Path(config.get("output", {}).get("figures_dir", "validation/results/figures"))
    figures_dir.mkdir(parents=True, exist_ok=True)

    dpi = int(config.get("output", {}).get("dpi", 300))
    for ext in ("png", "pdf"):
        fig.savefig(figures_dir / f"{name}.{ext}", dpi=dpi, bbox_inches="tight")


# ── Figure generators ─────────────────────────────────────────────────────────

def plot_jv_scatter(result: dict) -> matplotlib.figure.Figure:
    """2×2 scatter grid: extracted vs ground-truth Voc, Jsc, FF, PCE.

    Shows results for both noise levels on the same axes (different markers).
    A y = x reference line and 95 % confidence band are overlaid.

    Args:
        result: Dict returned by ``test_jv_extraction.run()``.

    Returns:
        Matplotlib figure.
    """
    params = ["Voc", "Jsc", "FF", "PCE"]
    units  = ["V", "mA cm$^{-2}$", "%", "%"]
    labels = [f"$V_{{\\rm oc}}$ ({u})" for u in ["V"]] + \
             [f"$J_{{\\rm sc}}$ (mA cm$^{-2}$)"] + \
             [f"FF (%)"] + \
             [f"PCE (%)"]
    labels = [f"$V_{{\\rm oc}}$ (V)", f"$J_{{\\rm sc}}$ (mA cm$^{-2}$)", "FF (%)", "PCE (%)"]

    fig, axes = plt.subplots(2, 2, figsize=(7, 6))
    axes = axes.flatten()

    noise_styles = [
        (0.0,  "o", PALETTE[0], "No noise"),
        (0.01, "s", PALETTE[1], "1 % Gaussian noise"),
    ]

    for ax_i, (param, label) in enumerate(zip(params, labels)):
        ax = axes[ax_i]
        all_gt: list[float] = []

        for noise_level, marker, color, legend_label in noise_styles:
            key = noise_level
            per_noise = result.get("per_noise_results", {}).get(key, {})
            details = result.get("details", {}).get(key, {})
            gt_arr  = details.get("ground_truth", {}).get(param, [])
            meas_arr = details.get("measured", {}).get(param, [])
            if not gt_arr:
                continue
            gt   = np.array(gt_arr)
            meas = np.array(meas_arr)
            all_gt.extend(gt.tolist())
            ax.scatter(gt, meas, marker=marker, color=color, alpha=0.7,
                       s=20, label=legend_label, zorder=3, linewidths=0)

        if all_gt:
            lo, hi = min(all_gt), max(all_gt)
            margin = (hi - lo) * 0.05
            ref = np.array([lo - margin, hi + margin])
            ax.plot(ref, ref, "k--", lw=1.0, label="y = x", zorder=2)

            # 2 % confidence band (covers the with-noise threshold)
            ax.fill_between(ref, ref * 0.98, ref * 1.02,
                            color="gray", alpha=0.15, label="±2 %", zorder=1)

        ax.set_xlabel(f"Ground truth {label}", labelpad=4)
        ax.set_ylabel(f"Extracted {label}", labelpad=4)
        ax.set_title(label)
        if ax_i == 0:
            ax.legend(fontsize=8, framealpha=0.8)

    return fig


def plot_bland_altman(result: dict) -> matplotlib.figure.Figure:
    """Bland-Altman plots for the 5 single-diode model parameters.

    Rows = parameters (J_ph, J0, n, Rs, Rsh).  Columns = noise levels.
    J0 and Rsh are plotted in log10 space.

    Args:
        result: Dict returned by ``test_single_diode_fit.run()``.

    Returns:
        Matplotlib figure.
    """
    params    = ["J_ph", "J0", "n", "Rs", "Rsh"]
    log_set   = {"J0", "Rsh"}
    ylabels   = [
        r"$J_{\rm ph}$ (mA cm$^{-2}$)",
        r"$J_0$ (mA cm$^{-2}$) — log space",
        r"$n$",
        r"$R_s$ (Ohm cm2)",
        r"$R_{\rm sh}$ (Ohm cm2) — log space",
    ]
    noise_levels = result.get("noise_levels_tested", [0.0, 0.005, 0.01, 0.02, 0.05])
    n_noise = len(noise_levels)
    n_params = len(params)

    fig, axes = plt.subplots(n_params, n_noise,
                             figsize=(2.5 * n_noise, 2.2 * n_params),
                             squeeze=False)

    for pi, (param, ylabel) in enumerate(zip(params, ylabels)):
        use_log = param in log_set
        for ni, noise in enumerate(noise_levels):
            ax = axes[pi][ni]
            cell_results = result.get("details", {}).get(noise, {}).get("cells", [])
            means, diffs = [], []
            for cell in cell_results:
                gt   = cell.get("ground_truth", {}).get(param, None)
                meas = cell.get("measured", {}).get(param, None)
                if gt is None or meas is None:
                    continue
                if use_log:
                    if gt > 0 and meas > 0:
                        g = np.log10(gt)
                        m = np.log10(meas)
                        means.append((g + m) / 2)
                        diffs.append(m - g)
                else:
                    means.append((gt + meas) / 2)
                    diffs.append(meas - gt)

            if means:
                means = np.array(means)
                diffs = np.array(diffs)
                bias  = np.mean(diffs)
                loa   = 1.96 * np.std(diffs, ddof=1)

                ax.scatter(means, diffs, s=8, color=PALETTE[ni % len(PALETTE)],
                           alpha=0.6, linewidths=0, zorder=3)
                ax.axhline(bias,       color="k",        lw=1.0, zorder=2)
                ax.axhline(bias + loa, color=PALETTE[3], lw=0.8, ls="--", zorder=2)
                ax.axhline(bias - loa, color=PALETTE[3], lw=0.8, ls="--", zorder=2)
                ax.axhline(0,          color="gray",     lw=0.6, ls=":", zorder=1)

            if pi == 0:
                ax.set_title(f"σ = {noise*100:.1f} %", fontsize=9)
            if ni == 0:
                ax.set_ylabel(ylabel, fontsize=8)
            if pi == n_params - 1:
                ax.set_xlabel("Mean (gt + fit)/2", fontsize=8)

    fig.suptitle("Bland-Altman: single-diode fit vs ground truth", fontsize=11, y=1.01)
    return fig


def plot_mpp_tracking(result: dict) -> matplotlib.figure.Figure:
    """Time series of tracked PCE with true MPP overlaid as a dashed line.

    Left panel: stable cell (flat true MPP).
    Right panel: decaying cell (exponential true MPP).

    Args:
        result: Dict returned by ``test_mpp_tracking.run()``.

    Returns:
        Matplotlib figure.
    """
    fig, (ax_s, ax_d) = plt.subplots(1, 2, figsize=(8, 3.5))

    # ── Stable cell ──────────────────────────────────────────────────────────
    stable = result.get("details", {}).get("stable", {})
    t_s    = np.array(stable.get("time_min", []))
    pce_s  = np.array(stable.get("pce_tracked", []))
    mpp_s  = stable.get("true_mpp_pce", None)
    if len(t_s):
        ax_s.plot(t_s, pce_s, color=PALETTE[0], lw=1.5, label="Tracked PCE")
    if mpp_s is not None:
        ax_s.axhline(mpp_s, color=PALETTE[3], lw=1.2, ls="--", label="True MPP")
    ax_s.set_xlabel("Time (min)")
    ax_s.set_ylabel("PCE (%)")
    ax_s.set_title("Stable cell — MPP tracking")
    ax_s.legend(fontsize=8)

    # ── Decaying cell ─────────────────────────────────────────────────────────
    decay = result.get("details", {}).get("decay", {})
    t_d   = np.array(decay.get("time_min", []))
    norm_d = np.array(decay.get("pce_norm_tracked", []))
    true_d = np.array(decay.get("pce_norm_true", []))
    if len(t_d):
        ax_d.plot(t_d, norm_d, color=PALETTE[0], lw=1.5, label="Tracked PCE$_{norm}$")
        ax_d.plot(t_d, true_d, color=PALETTE[3], lw=1.2, ls="--",
                  label=r"exp(−t/τ), τ = 100 h")
    ax_d.set_xlabel("Time (min)")
    ax_d.set_ylabel("Normalized PCE")
    ax_d.set_title("Decaying cell — PCE$_{norm}$ tracking")
    ax_d.legend(fontsize=8)

    return fig


def plot_repeatability(result: dict) -> matplotlib.figure.Figure:
    """2×2 histograms of 20 repeated measurements with mean ± SD annotations.

    Args:
        result: Dict returned by ``test_repeatability.run()``.

    Returns:
        Matplotlib figure.
    """
    params = ["Voc", "Jsc", "FF", "PCE"]
    labels = [f"$V_{{\\rm oc}}$ (V)", f"$J_{{\\rm sc}}$ (mA cm$^{-2}$)", "FF (%)", "PCE (%)"]
    fig, axes = plt.subplots(2, 2, figsize=(7, 5))
    axes = axes.flatten()

    details = result.get("details", {})
    for ax, param, label in zip(axes, params, labels):
        vals = np.array(details.get(param, []))
        if not len(vals):
            continue
        mean = np.mean(vals)
        sd   = np.std(vals, ddof=1)
        cv   = sd / mean * 100

        ax.hist(vals, bins=8, color=PALETTE[0], alpha=0.8, edgecolor="white",
                linewidth=0.5)
        ax.axvline(mean,      color="k",        lw=1.2, ls="-",  label=f"Mean = {mean:.4f}")
        ax.axvline(mean - sd, color=PALETTE[3], lw=0.9, ls="--", label=f"SD = {sd:.4f}")
        ax.axvline(mean + sd, color=PALETTE[3], lw=0.9, ls="--")

        ax.set_xlabel(label)
        ax.set_ylabel("Count")
        ax.set_title(f"{label}  CV = {cv:.3f} %")
        ax.legend(fontsize=7.5, framealpha=0.8)

    return fig


def plot_isos_d1(result: dict) -> matplotlib.figure.Figure:
    """PCE_norm vs time for the 240 h ISOS-D-1 simulation.

    Shows tracked PCE_norm (open circles) and the theoretical exponential
    decay curve (dashed line) with T80 annotated.

    Args:
        result: Dict returned by ``test_isos_d1.run()``.

    Returns:
        Matplotlib figure.
    """
    fig, ax = plt.subplots(figsize=(7, 4))

    details  = result.get("details", {})
    t_h      = np.array(details.get("time_h", []))
    norm_meas = np.array(details.get("pce_norm_measured", []))
    norm_true = np.array(details.get("pce_norm_true", []))
    tau_h    = result.get("tau_h", 100.0)

    if len(t_h):
        ax.plot(t_h, norm_meas, "o", color=PALETTE[0], ms=3, alpha=0.7,
                label="Tracked PCE$_{norm}$", zorder=3)
        ax.plot(t_h, norm_true, "--", color=PALETTE[3], lw=1.5,
                label=r"exp(−t/τ), τ = " + f"{tau_h:.0f} h", zorder=2)

        # Annotate T80
        t80 = -tau_h * np.log(0.8)
        ax.axhline(0.8, color="gray", lw=0.7, ls=":", zorder=1)
        ax.axvline(t80, color="gray", lw=0.7, ls=":", zorder=1)
        ax.annotate(f"T80 = {t80:.1f} h",
                    xy=(t80, 0.8), xytext=(t80 + 5, 0.83),
                    fontsize=8, arrowprops=dict(arrowstyle="->", lw=0.7))

    ax.set_xlabel("Elapsed time (h)")
    ax.set_ylabel("Normalized PCE (PCE$_{norm}$)")
    ax.set_title("ISOS-D-1 simulation: 240 h stability tracking")
    ax.set_ylim(bottom=0)
    ax.legend(fontsize=9)

    return fig


def plot_error_heatmap(result: dict, test_name: str) -> matplotlib.figure.Figure:
    """Heatmap of mean relative errors for the single-diode fit across noise levels.

    Args:
        result: Dict returned by ``test_single_diode_fit.run()``.
        test_name: Title string.

    Returns:
        Matplotlib figure.
    """
    params       = ["J_ph", "J0", "n", "Rs", "Rsh"]
    noise_levels = result.get("noise_levels_tested", [])
    noise_labels = [f"{n*100:.1f} %" for n in noise_levels]

    # Build matrix: rows = noise levels, cols = parameters
    matrix = np.full((len(noise_levels), len(params)), np.nan)
    for ni, noise in enumerate(noise_levels):
        per_noise = result.get("per_noise_results", {}).get(noise, {})
        for pi, param in enumerate(params):
            val = per_noise.get(param, {}).get("mean_rel_error", None)
            if val is not None:
                matrix[ni, pi] = float(val) * 100   # convert to %

    fig, ax = plt.subplots(figsize=(6, 3.5))
    im = ax.imshow(matrix, aspect="auto", cmap="YlOrRd", vmin=0, vmax=20)
    fig.colorbar(im, ax=ax, label="Mean relative error (%)")

    ax.set_xticks(range(len(params)))
    ax.set_xticklabels(params)
    ax.set_yticks(range(len(noise_levels)))
    ax.set_yticklabels(noise_labels)
    ax.set_xlabel("Parameter")
    ax.set_ylabel("Noise level")
    ax.set_title(test_name)

    # Annotate cells
    for ni in range(len(noise_levels)):
        for pi in range(len(params)):
            v = matrix[ni, pi]
            if np.isfinite(v):
                color = "white" if v > 10 else "black"
                ax.text(pi, ni, f"{v:.1f}", ha="center", va="center",
                        fontsize=8, color=color)

    return fig
