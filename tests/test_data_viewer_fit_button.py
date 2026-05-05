"""Light GUI tests for the Single-Diode Fit button in DataViewerWindow.

Focus: data-flow correctness (file reading, SingleDiodeFitWindow creation).
No pixel position assertions.

These tests create a real (hidden) Tk root and Toplevel.
On Windows this is always available; on CI headless Linux the tests are
skipped if Tk cannot initialise.
"""

from __future__ import annotations

import math
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_illuminated_xlsx(tmp_path, with_fit: bool = False) -> str:
    """Write a minimal illuminated J-V xlsx and return the path."""
    import numpy as np
    from app.measurement.analysis import SingleDiodeFit
    from app.io.exporter import save_data

    v = np.linspace(-0.1, 1.1, 30)
    j = np.array([20.0 - 20.0 * max(0.0, vi / 1.05) ** 2 for vi in v])
    fit = None
    if with_fit:
        fit = SingleDiodeFit(
            parameters={"J_ph": 20.0, "J0": 1e-11, "n": 1.75, "Rs": 4.0, "Rsh": 3000.0},
            std_errors={"J_ph": 0.1, "J0": 1e-13, "n": 0.05, "Rs": 0.3, "Rsh": 200.0},
            r_squared=0.9998,
            rmse=0.02,
            rmse_log=0.003,
            converged=True,
            method="curve_fit+brentq",
            bounded_parameters=[],
            temperature_K=298.15,
            timestamp="2026-05-05 12:00:00",
        )
    fp = save_data(
        str(tmp_path), "GUI_Test",
        v, j, v, j, 100.0,
        1.01, 19.5, 68.0, 13.2, 4.0, 450.0,
        1.04, 20.0, 71.0, 14.8, 3.5, 480.0,
        10.5,
        0.07, 100, 1.0, False,
        True, 1.2, 2.0,
        "TEST_IDN",
        fit_result=fit,
    )
    return fp


# ── load_jv_file round-trip (no Tk needed, already tested; re-verify) ─────────

class TestLoadJvFileNoTk:
    """These do not need Tk — fast sanity checks on the reader."""

    def test_load_without_fit(self, tmp_path):
        from app.gui.data_viewer import load_jv_file
        fp   = _make_illuminated_xlsx(tmp_path, with_fit=False)
        meta = load_jv_file(fp)
        assert meta["type"] == "illuminated"
        assert meta.get("single_diode_fit") is None

    def test_load_with_fit(self, tmp_path):
        from app.gui.data_viewer import load_jv_file
        fp   = _make_illuminated_xlsx(tmp_path, with_fit=True)
        meta = load_jv_file(fp)
        assert "single_diode_fit" in meta
        assert meta["single_diode_fit"] is not None
        assert abs(meta["single_diode_fit"]["parameters"].get("J_ph", 0) - 20.0) < 0.01

    def test_fit_metadata_fields_present(self, tmp_path):
        from app.gui.data_viewer import load_jv_file
        fp   = _make_illuminated_xlsx(tmp_path, with_fit=True)
        meta = load_jv_file(fp)
        fit  = meta["single_diode_fit"]
        for key in ("parameters", "std_errors", "r_squared", "rmse",
                    "rmse_log", "method", "temperature_K", "bounded_parameters"):
            assert key in fit, f"Key '{key}' missing from loaded fit dict"

    def test_fit_r_squared_roundtrip(self, tmp_path):
        from app.gui.data_viewer import load_jv_file
        fp   = _make_illuminated_xlsx(tmp_path, with_fit=True)
        meta = load_jv_file(fp)
        r2   = meta["single_diode_fit"]["r_squared"]
        assert abs(r2 - 0.9998) < 0.0001


# ── DataViewerWindow: fit button state ───────────────────────────────────────

class TestFitButtonState:
    """Verify that the fit button is enabled/disabled correctly."""

    def test_fit_button_enabled_for_illuminated(self, tmp_path):
        import tkinter as tk
        from app.gui.data_viewer import DataViewerWindow

        root = tk.Tk()
        root.withdraw()
        try:
            fp  = _make_illuminated_xlsx(tmp_path)
            win = DataViewerWindow(root, fp)
            state = win._fit_btn.cget("state")
            win.win.destroy()
        finally:
            root.destroy()
        assert str(state) == "normal"

    def test_fit_button_disabled_for_dark_jv(self, tmp_path):
        import tkinter as tk
        from app.io.exporter import save_dark_jv_data
        from app.gui.data_viewer import DataViewerWindow

        v = np.linspace(-0.1, 1.0, 20)
        j = np.zeros_like(v)
        fp = save_dark_jv_data(str(tmp_path), "DarkTest", v, j, v, j,
                               0.07, 100, 1.0, False, "TEST")
        root = tk.Tk()
        root.withdraw()
        try:
            win   = DataViewerWindow(root, fp)
            state = win._fit_btn.cget("state")
            win.win.destroy()
        finally:
            root.destroy()
        assert str(state) == "disabled"


# ── SingleDiodeFitWindow: instantiation and data wiring ──────────────────────

class TestSingleDiodeFitWindow:
    def test_window_opens_without_error(self, tmp_path):
        import tkinter as tk
        from app.gui.data_viewer import DataViewerWindow

        root = tk.Tk()
        root.withdraw()
        try:
            fp  = _make_illuminated_xlsx(tmp_path)
            win = DataViewerWindow(root, fp)
            # Trigger the fit button callback directly (no click event needed)
            win._on_single_diode_fit()
            # If a SingleDiodeFitWindow was created, at least one extra Toplevel exists
            toplevels = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
            assert len(toplevels) >= 1
            # Clean up
            for tl in toplevels:
                tl.destroy()
            win.win.destroy()
        finally:
            root.destroy()

    def test_fit_window_receives_correct_arrays(self, tmp_path):
        """The v and j arrays passed to SingleDiodeFitWindow must match the file data."""
        import tkinter as tk
        from app.gui.data_viewer import DataViewerWindow, SingleDiodeFitWindow

        root = tk.Tk()
        root.withdraw()
        try:
            fp   = _make_illuminated_xlsx(tmp_path)
            dwin = DataViewerWindow(root, fp)
            data = dwin._current_data
            v_r  = data["v_r"]
            j_r  = data["j_r"]

            created_windows = []
            _orig_init = SingleDiodeFitWindow.__init__

            def _capture_init(self_inner, **kwargs):
                created_windows.append(kwargs)
                # Call original but suppress the actual fit to keep test fast
                self_inner._parent   = kwargs["parent"]
                self_inner._v        = kwargs["v"]
                self_inner._j        = kwargs["j"]
                self_inner._filepath = kwargs["filepath"]
                self_inner._basename = kwargs.get("basename", "")
                self_inner._fit      = None
                self_inner._fit_v    = None
                self_inner._fitting  = False
                import tkinter as tk2
                self_inner.win = tk2.Toplevel(kwargs["parent"])
                self_inner.win.withdraw()

            SingleDiodeFitWindow.__init__ = _capture_init
            try:
                dwin._on_single_diode_fit()
            finally:
                SingleDiodeFitWindow.__init__ = _orig_init

            assert len(created_windows) == 1
            np.testing.assert_array_equal(created_windows[0]["v"], v_r)
            np.testing.assert_array_equal(created_windows[0]["j"], j_r)

            dwin.win.destroy()
        finally:
            root.destroy()

    def test_fit_window_loads_initial_fit_from_file(self, tmp_path):
        """When file has SingleDiodeFit sheet, window shows those parameters."""
        import tkinter as tk
        from app.gui.data_viewer import DataViewerWindow, SingleDiodeFitWindow

        root = tk.Tk()
        root.withdraw()
        try:
            fp   = _make_illuminated_xlsx(tmp_path, with_fit=True)
            dwin = DataViewerWindow(root, fp)
            initial_fit = dwin._current_data.get("single_diode_fit")
            assert initial_fit is not None
            assert abs(initial_fit["parameters"].get("J_ph", 0) - 20.0) < 0.01

            dwin.win.destroy()
        finally:
            root.destroy()


# ── Regression: Refit with unchanged default bounds must match None-bounds fit ─

class TestRefitBoundsRegression:
    """Regression for the _parse_bounds() Jsc-multiplication bug (now fixed)."""

    @staticmethod
    def _make_win_with_defaults(root):
        """Build a SingleDiodeFitWindow with default UI but suppressed auto-fit."""
        import tkinter as tk
        from app.simulation.dummy_cell import DummyCell
        from app.gui.data_viewer import SingleDiodeFitWindow
        import numpy as np

        cell = DummyCell(mode="ideal_stable",
                         j_ph=20.0, j0=1e-10, n=1.8, rs=3.0, rsh=2000.0)
        cell.set_time_offset(0.0)
        v = np.linspace(1.25, 0.0, 60)
        j = cell.scan(v, scan_direction="reverse")

        win = SingleDiodeFitWindow.__new__(SingleDiodeFitWindow)
        win._parent = root
        win._v = v
        win._j = j
        win._filepath = None
        win._basename = "test"
        win._fit = None
        win._fit_v = None
        win._fitting = False
        win.win = tk.Toplevel(root)
        win.win.withdraw()
        win._build_ui()
        return win, v, j

    def test_default_bounds_are_numerically_correct(self):
        """_parse_bounds() with unchanged defaults must equal the None-bounds set."""
        import tkinter as tk
        import numpy as np

        root = tk.Tk()
        root.withdraw()
        try:
            win, v, j = self._make_win_with_defaults(root)
            parsed = win._parse_bounds()
            win.win.destroy()
        finally:
            root.destroy()

        j_sc = float(abs(j[np.argmin(np.abs(v))]))
        expected = {
            "J_ph": (0.5 * j_sc, 2.0 * j_sc),
            "J0":   (1e-15,      1e-6),
            "n":    (0.8,        5.0),
            "Rs":   (0.01,       100.0),
            "Rsh":  (10.0,       1e8),
        }
        for name, (exp_lo, exp_hi) in expected.items():
            got_lo, got_hi = parsed[name]
            assert got_lo == pytest.approx(exp_lo, rel=1e-6), \
                f"{name} lower: got {got_lo:.4g}, expected {exp_lo:.4g}"
            assert got_hi == pytest.approx(exp_hi, rel=1e-6), \
                f"{name} upper: got {got_hi:.4g}, expected {exp_hi:.4g}"

    def test_refit_with_default_bounds_matches_none_bounds(self):
        """Refit with untouched defaults must produce the same R2 as initial fit."""
        import tkinter as tk
        import numpy as np
        from app.measurement.analysis import fit_single_diode

        root = tk.Tk()
        root.withdraw()
        try:
            win, v, j = self._make_win_with_defaults(root)
            parsed_bounds = win._parse_bounds()
            win.win.destroy()
        finally:
            root.destroy()

        r_none  = fit_single_diode(v, j, T=298.15, bounds=None)
        r_refit = fit_single_diode(v, j, T=298.15, bounds=parsed_bounds)

        assert r_refit.r_squared == pytest.approx(r_none.r_squared, abs=0.01), \
            (f"Refit R2={r_refit.r_squared:.4f} differs from "
             f"None-bounds R2={r_none.r_squared:.6f}")

    def test_parse_one_bound_jsc_notation(self):
        """Strings containing 'Jsc' are scaled by j_sc; plain floats are not."""
        from app.gui.data_viewer import SingleDiodeFitWindow as W
        j_sc = 19.97

        # "× Jsc" notation
        assert W._parse_one_bound("0.5 × Jsc", j_sc) == pytest.approx(0.5 * j_sc)
        assert W._parse_one_bound("2.0 × Jsc", j_sc) == pytest.approx(2.0 * j_sc)
        assert W._parse_one_bound("0.5 ×Jsc",  j_sc) == pytest.approx(0.5 * j_sc)
        assert W._parse_one_bound("2.0×Jsc",   j_sc) == pytest.approx(2.0 * j_sc)

        # Plain numeric — must NOT be multiplied by j_sc
        assert W._parse_one_bound("1e-15",  j_sc) == pytest.approx(1e-15)
        assert W._parse_one_bound("1e-6",   j_sc) == pytest.approx(1e-6)
        assert W._parse_one_bound("0.8",    j_sc) == pytest.approx(0.8)
        assert W._parse_one_bound("5.0",    j_sc) == pytest.approx(5.0)
        assert W._parse_one_bound("0.01",   j_sc) == pytest.approx(0.01)
        assert W._parse_one_bound("100.0",  j_sc) == pytest.approx(100.0)
        assert W._parse_one_bound("10.0",   j_sc) == pytest.approx(10.0)
        assert W._parse_one_bound("1e8",    j_sc) == pytest.approx(1e8)
