"""Light GUI tests for the ISOS-L-1 panel wiring.

Focus: protocol/load switching reconfigures the sub-frame and swaps the plot
layout. No pixel assertions. A real (hidden) Tk root is created; the test is
skipped where Tk cannot initialise (headless CI).
"""

from __future__ import annotations

import pytest


def _tk_available() -> bool:
    try:
        import tkinter as tk
        r = tk.Tk()
        r.destroy()
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.gui,
    pytest.mark.skipif(not _tk_available(), reason="Tk not available in this environment"),
]


@pytest.fixture
def app():
    import tkinter as tk
    from app.gui.main_window import JVMeasurementApp

    root = tk.Tk()
    root.withdraw()
    application = JVMeasurementApp(root)
    application.switch_mode("isos")
    root.update_idletasks()
    yield application
    try:
        root.destroy()
    except Exception:
        pass


def test_d1_default_layout_is_d1_and_subframe_hidden(app):
    assert app.isos_protocol_var.get() == "ISOS-D-1"
    assert app._isos_plot_layout == "d1"
    assert app.isos_l1_subframe.winfo_manager() == ""   # not packed


def test_select_l1_oc_shows_subframe_and_oc_panel(app):
    app.isos_protocol_var.set("ISOS-L-1")
    app._isos_on_protocol_change()
    app.root.update_idletasks()

    assert app.isos_l1_subframe.winfo_manager() == "pack"
    assert app._isos_plot_layout == "oc"
    assert "ax_voc" in app.isos_l1_plot_axes
    assert len(app.isos_fig.axes) == 2          # Voc(t) + latest J-V
    # OC settings revealed, MPP settings hidden.
    assert app.isos_l1_oc_frame.winfo_manager() == "grid"
    assert app.isos_l1_mpp_frame.winfo_manager() == ""


def test_toggle_oc_to_mpp_swaps_plot_layout(app):
    app.isos_protocol_var.set("ISOS-L-1")
    app._isos_on_protocol_change()
    app.root.update_idletasks()

    app.isos_load_var.set("MPP")               # fires the load trace
    app.root.update_idletasks()

    assert app._isos_plot_layout == "mpp"
    axes = app.isos_l1_plot_axes
    assert {"ax_pce", "ax_j", "ax_v", "ax_jv"} <= set(axes)
    assert len(app.isos_fig.axes) == 4
    assert app.isos_l1_mpp_frame.winfo_manager() == "grid"
    assert app.isos_l1_oc_frame.winfo_manager() == ""

    # ...and back to OC swaps the layout cleanly.
    app.isos_load_var.set("OC")
    app.root.update_idletasks()
    assert app._isos_plot_layout == "oc"
    assert len(app.isos_fig.axes) == 2


def test_returning_to_d1_restores_d1_layout(app):
    app.isos_protocol_var.set("ISOS-L-1")
    app._isos_on_protocol_change()
    app.root.update_idletasks()
    assert app._isos_plot_layout in ("oc", "mpp")

    app.isos_protocol_var.set("ISOS-D-1")
    app._isos_on_protocol_change()
    app.root.update_idletasks()

    assert app._isos_plot_layout == "d1"
    assert app.isos_l1_subframe.winfo_manager() == ""
    assert app.isos_load_var.get() == "OC"
    assert hasattr(app, "isos_ax_pce") and app.isos_ax_pce is not None
