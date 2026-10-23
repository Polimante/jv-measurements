"""Headless smoke test for overlay_stability_metrics on a PCE axis."""

from __future__ import annotations

import matplotlib
matplotlib.use("Agg")

import numpy as np

from app.gui.mpp_plot_factory import build_oc_continuous_panel, overlay_stability_metrics
from app.measurement.stability_metrics import compute_stability_metrics


def _metrics():
    t = np.linspace(0, 300, 150)
    pce = 20.0 * np.exp(-0.004 * t)
    return compute_stability_metrics(t, pce, pce_0=20.0)


def test_overlay_none_is_noop():
    panel = build_oc_continuous_panel(None)
    overlay_stability_metrics(panel["ax_voc"], None, 300.0)   # must not raise


def test_overlay_draws_threshold_and_marker_lines():
    panel = build_oc_continuous_panel(None)
    ax = panel["ax_voc"]
    before = len(ax.lines)
    overlay_stability_metrics(ax, _metrics(), 300.0)
    # At least the two horizontal thresholds + T_S + T80 + T95 lines were added.
    assert len(ax.lines) >= before + 4
