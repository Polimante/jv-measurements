"""GUI tests for the ISOS-V-1 illuminated scan-rate guard.

A slow illuminated scan exposes the cell to light long enough to contaminate
the dark-bias V-1 stress: warn below 50 mV/s, block (disable Run) below
25 mV/s. A real (hidden) Tk root is created; skipped where Tk is unavailable.
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
    application.isos_protocol_var.set("ISOS-V-1")
    application._isos_on_protocol_change()
    root.update_idletasks()
    yield application
    try:
        root.destroy()
    except Exception:
        pass


def test_v1_selected_shows_subframe_and_v1_panel(app):
    assert app.isos_v1_subframe.winfo_manager() == "pack"
    assert app._isos_plot_layout == "v1"
    assert "ax_pce" in app.isos_v1_plot_axes
    assert "ax_n" in app.isos_v1_plot_axes


def _set_scan(app, step_v=0.025, delay_ms=100.0, nplc=1.0):
    app.isos_scan_rate_mode.set("advanced")
    app.isos_step_v_var.set(step_v)
    app.isos_nplc_var.set(nplc)
    app.isos_delay_ms_var.set(delay_ms)        # fires the params-change trace
    app.root.update_idletasks()


def test_fast_scan_is_ok(app):
    _set_scan(app, delay_ms=100.0)             # ~150 mV/s
    assert app._isos_v1_scan_blocked is False
    assert "✔" in app.isos_v1_scan_warn_var.get()


def test_slow_scan_warns_but_not_blocked(app):
    _set_scan(app, delay_ms=450.0)             # ~48 mV/s
    assert app._isos_v1_scan_blocked is False
    assert "⚠" in app.isos_v1_scan_warn_var.get()


def test_very_slow_scan_blocks_run(app):
    _set_scan(app, delay_ms=1000.0)            # ~23 mV/s
    assert app._isos_v1_scan_blocked is True
    assert "too slow" in app.isos_v1_scan_warn_var.get().lower()
