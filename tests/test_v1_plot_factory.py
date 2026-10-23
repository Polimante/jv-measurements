"""Headless smoke tests for the ISOS-V-1 diagnostic plot panel.

``parent_frame=None`` skips the Tk canvas so the GridSpec layout and the
``update_v1_plot`` redraw can be exercised without a display (these run in CI,
unlike the gui-marked panel tests).
"""

from __future__ import annotations

import matplotlib
matplotlib.use("Agg")

from app.gui.mpp_plot_factory import build_v1_diagnostic_panel, update_v1_plot


def test_build_panel_has_all_axes():
    panel = build_v1_diagnostic_panel(None)
    for key in ("ax_pce", "ax_rs", "ax_rsh", "ax_n", "ax_js", "ax_hi",
                "ax_jv", "ax_jv_dark"):
        assert key in panel
    assert panel["canvas"] is None


def test_update_with_empty_rows_does_not_raise():
    panel = build_v1_diagnostic_panel(None)
    update_v1_plot(panel, [], None, None)


def test_update_with_log_rows_plots_markers():
    panel = build_v1_diagnostic_panel(None)
    rows = [
        {"Elapsed_h": 0.0, "PCE_rev (%)": 18.0, "PCE_fwd (%)": 17.0,
         "Rs_rev (Ω·cm²)": 5.0, "Rsh_rev (Ω·cm²)": 1e4,
         "R_s_dark_rev": 6.0, "R_sh_dark_rev": 9e3,
         "n_dark_rev": 1.6, "J_s_dark_rev": 1e-9, "HI (%)": 5.0, "HI_dark": 2.0},
        {"Elapsed_h": 1.0, "PCE_rev (%)": 16.0, "PCE_fwd (%)": 15.0,
         "Rs_rev (Ω·cm²)": 7.0, "Rsh_rev (Ω·cm²)": 8e3,
         "R_s_dark_rev": 9.0, "R_sh_dark_rev": 5e3,
         "n_dark_rev": 1.9, "J_s_dark_rev": 5e-9, "HI (%)": 6.0, "HI_dark": 3.0},
    ]
    illum = {"v": [0.0, 0.5, 1.0], "j": [20.0, 18.0, 0.0], "vmpp": 0.8}
    dark = {"v": [0.0, 0.5, 1.0], "j": [-0.01, -0.05, -2.0]}
    update_v1_plot(panel, rows, illum, dark)

    # PCE axis should have two marker lines (reverse + forward).
    assert len(panel["ax_pce"].lines) >= 2
    assert len(panel["ax_n"].lines) >= 1
