"""Light GUI tests for the Dark Ideality Fit button in DataViewerWindow.

These tests create a real (hidden) Tk root and Toplevel.
Skipped automatically when Tk is not available.

A module-scoped Tk root is shared across all tests so that Tk is created
and destroyed only once per test module.  This avoids a known Python 3.14 /
Tcl 9 issue on Windows where repeated Tk().destroy() cycles corrupt the
Tcl init path.
"""

from __future__ import annotations

import numpy as np
import pytest


# ── Tk availability guard ─────────────────────────────────────────────────────

def _tk_available() -> bool:
    try:
        import tkinter as tk
        r = tk.Tk()
        r.destroy()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _tk_available(), reason="Tk not available in this environment"
)


# ── Module-scoped Tk root (created once, shared by all tests) ─────────────────

@pytest.fixture(scope="module")
def tk_root():
    """Single hidden Tk root for the whole module."""
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


# ── File creation helpers ─────────────────────────────────────────────────────

def _make_dark_xlsx(tmp_path) -> str:
    """Save a dark J-V file with a realistic exponential curve."""
    from app.io.exporter import save_dark_jv_data
    from app.measurement.analysis import _KB, _Q
    vt = _KB * 298.15 / _Q
    v  = np.linspace(0.0, 1.2, 60)
    # Synthetic dark diode: sign convention → negative J after negation
    j = -(1e-10 * (np.exp(v / (1.5 * vt)) - 1.0))
    return save_dark_jv_data(
        str(tmp_path), "DarkTest",
        v, j, v, j,
        0.07, 100, 1.0, False, "TEST_IDN",
    )


def _make_illuminated_xlsx(tmp_path) -> str:
    from app.io.exporter import save_data
    v = np.linspace(-0.1, 1.1, 30)
    j = np.array([20.0 - 20.0 * max(0.0, vi / 1.05) ** 2 for vi in v])
    return save_data(
        str(tmp_path), "IllumTest",
        v, j, v, j, 100.0,
        1.01, 19.5, 68.0, 13.2, 4.0, 450.0,
        1.04, 20.0, 71.0, 14.8, 3.5, 480.0,
        10.5,
        0.07, 100, 1.0, False,
        True, 1.2, 2.0,
        "TEST_IDN",
    )


# ── Button state tests ────────────────────────────────────────────────────────

class TestDarkIdealityButtonState:
    def test_button_disabled_for_illuminated_file(self, tk_root, tmp_path):
        from app.gui.data_viewer import DataViewerWindow

        fp  = _make_illuminated_xlsx(tmp_path)
        win = DataViewerWindow(tk_root, fp)
        state = win._dark_ideality_btn.cget("state")
        win.win.destroy()
        assert str(state) == "disabled"

    def test_button_enabled_for_dark_jv_file(self, tk_root, tmp_path):
        from app.gui.data_viewer import DataViewerWindow

        fp  = _make_dark_xlsx(tmp_path)
        win = DataViewerWindow(tk_root, fp)
        state = win._dark_ideality_btn.cget("state")
        win.win.destroy()
        assert str(state) == "normal"

    def test_single_diode_fit_button_disabled_for_dark_file(self, tk_root, tmp_path):
        """Ensure the existing Single-Diode Fit button stays disabled for dark files."""
        from app.gui.data_viewer import DataViewerWindow

        fp  = _make_dark_xlsx(tmp_path)
        win = DataViewerWindow(tk_root, fp)
        state = win._fit_btn.cget("state")
        win.win.destroy()
        assert str(state) == "disabled"


# ── DarkIdealityFitWindow instantiation ──────────────────────────────────────

class TestDarkIdealityFitWindowOpens:
    def test_window_opens_without_error(self, tk_root, tmp_path):
        """Calling _on_dark_ideality_fit() must not raise and must create a Toplevel."""
        import tkinter as tk
        from app.gui.data_viewer import DataViewerWindow

        fp   = _make_dark_xlsx(tmp_path)
        dwin = DataViewerWindow(tk_root, fp)
        # Should not raise
        dwin._on_dark_ideality_fit()
        # DarkIdealityFitWindow.win is a child of dwin.win (not of tk_root)
        children = dwin.win.winfo_children()
        fit_toplevels = [w for w in children if isinstance(w, tk.Toplevel)]
        assert len(fit_toplevels) >= 1, (
            "Expected at least one Toplevel child of dwin.win after opening fit window"
        )
        for tl in fit_toplevels:
            tl.destroy()
        dwin.win.destroy()
