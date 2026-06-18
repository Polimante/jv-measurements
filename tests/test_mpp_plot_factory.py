"""Plot-factory tests — build all 3 layouts headlessly and update them safely."""

from __future__ import annotations

import matplotlib
matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt

from app.gui.mpp_plot_factory import (
    build_mpp_continuous_panel,
    build_oc_continuous_panel,
    build_scan_only_panel,
    update_mpp_plot,
    update_oc_plot,
    update_scan_only_plot,
)


def test_mpp_panel_has_four_axes():
    p = build_mpp_continuous_panel(None)
    assert p["canvas"] is None
    assert len(p["fig"].axes) == 4
    for key in ("ax_pce", "ax_j", "ax_v", "ax_jv"):
        assert key in p
    plt.close(p["fig"])


def test_oc_panel_has_two_axes():
    p = build_oc_continuous_panel(None)
    assert len(p["fig"].axes) == 2
    for key in ("ax_voc", "ax_jv"):
        assert key in p
    plt.close(p["fig"])


def test_scan_only_panel_has_three_axes():
    p = build_scan_only_panel(None)
    assert len(p["fig"].axes) == 3
    for key in ("ax_pce", "ax_jv", "ax_idark"):
        assert key in p
    plt.close(p["fig"])


def test_update_mpp_with_empty_arrays():
    p = build_mpp_continuous_panel(None)
    update_mpp_plot(p, [], [], [], [], None)
    plt.close(p["fig"])


def test_update_oc_with_empty_arrays():
    p = build_oc_continuous_panel(None)
    update_oc_plot(p, [], [], None)
    plt.close(p["fig"])


def test_update_scan_only_with_empty_rows():
    p = build_scan_only_panel(None)
    update_scan_only_plot(p, [], None, None)
    plt.close(p["fig"])


def test_update_mpp_with_data():
    p = build_mpp_continuous_panel(None)
    update_mpp_plot(p, [0, 1, 2], [0.8, 0.81, 0.79], [20, 19, 21],
                    [18, 17, 18], {"v": [0, 0.5, 1.0], "j": [20, 10, 0],
                                   "vmpp": 0.8})
    plt.close(p["fig"])


def test_update_scan_only_with_data():
    p = build_scan_only_panel(None)
    rows = [
        {"Elapsed_h": 0.0, "PCE_rev (%)": 18.0, "PCE_fwd (%)": 17.0},
        {"Elapsed_h": 1.0, "PCE_rev (%)": 17.5, "PCE_fwd (%)": 16.5},
    ]
    update_scan_only_plot(p, rows,
                          {"v": [0, 0.5, 1.0], "j": [20, 10, 0], "vmpp": 0.8},
                          [(0.0, 0.01), (3600.0, 0.02)])
    plt.close(p["fig"])
