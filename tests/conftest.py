import os
import sys
import pytest
import numpy as np

from app.instrument.dummy_smu import DummySMU


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "windows_only: skip on non-Windows platforms"
    )
    config.addinivalue_line(
        "markers", "gui: requires a live Tk display; skipped in headless environments and CI"
    )


def pytest_runtest_setup(item):
    for _ in item.iter_markers(name="windows_only"):
        if not sys.platform.startswith("win"):
            pytest.skip("windows_only: requires Windows")

    for _ in item.iter_markers(name="gui"):
        in_ci = os.environ.get("CI", "").lower() in ("true", "1", "yes")
        if sys.platform.startswith("win") and in_ci:
            pytest.skip("gui: skipped in CI on Windows (no display)")
        elif not sys.platform.startswith("win") and not os.environ.get("DISPLAY"):
            pytest.skip("gui: no DISPLAY variable set (headless Linux)")


# ── Shared fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def synthetic_jv_curve():
    """Factory fixture: build a linear (V, J) array pair with known parameters.

    The linear model J = Jsc * (1 - V/Voc) gives analytically exact values
    for Voc, Jsc, FF (25 %), and PCE — useful for formula correctness tests.

    Usage::

        V, J = synthetic_jv_curve({"Jsc": 20.0, "Voc": 1.0, "v_step": 0.001})
    """
    def _make(params: dict):
        jsc    = float(params.get("Jsc", 20.0))
        voc    = float(params.get("Voc", 1.0))
        v_start = float(params.get("v_start", -0.1))
        v_step  = float(params.get("v_step", 0.001))
        v = np.arange(v_start, voc + v_step * 0.5, v_step)
        j = jsc * (1.0 - v / voc)
        return v, j
    return _make


@pytest.fixture
def dummy_smu_in_mode():
    """Factory fixture: return a connected, sleep-disabled DummySMU for a cell mode.

    Usage::

        smu = dummy_smu_in_mode("ideal_stable")
        smu = dummy_smu_in_mode("exponential_decay", tau_h=50.0)
    """
    def _make(mode: str, **cell_kwargs) -> DummySMU:
        smu = DummySMU(cell_mode=mode, sleep_scale=0.0, **cell_kwargs)
        smu.connect()
        smu.configure(nplc=1.0, delay_s=0.0, four_wire=False)
        return smu
    return _make


@pytest.fixture
def tmp_excel_file(tmp_path):
    """Return a temporary .xlsx path that does not yet exist.

    The parent directory exists; the file itself is created by the test.
    The temp directory is cleaned up automatically after the test session.
    """
    return str(tmp_path / "test_output.xlsx")


# ── Shared Tk root ────────────────────────────────────────────────────────────
#
# Every GUI test shares ONE Tk root, created on first use and destroyed only at
# the end of the session. Repeated Tk() / destroy() cycles corrupt the Tcl
# interpreter state ("invalid command name tcl_findLibrary" on Tcl 9 / Windows),
# which surfaced as order-dependent flakiness: the same suite would pass, skip
# or error depending on which GUI module happened to run first.
#
# This also replaces the per-module _tk_available() probes, which each created
# and destroyed a root at *collection* time — the churn that made the corruption
# likely before any test had even run.

@pytest.fixture(scope="session")
def tk_root():
    """The one hidden Tk root for the whole test session."""
    try:
        import tkinter as tk
        root = tk.Tk()
    except Exception as exc:                      # no display, no Tcl, no Tk
        pytest.skip(f"Tk is not available in this environment: {exc}")
    root.withdraw()
    yield root
    try:
        root.destroy()
    except Exception:
        pass


@pytest.fixture(scope="session")
def jv_app(tk_root):
    """One JVMeasurementApp on the shared root, for the whole session.

    Shared rather than built per module: a second instance would put a second
    menubar on the same root and pack a second set of panels into it.

    The consequence is that a module using this must establish its own
    preconditions rather than assume pristine state. Values that were true
    before any test touched the app are captured in ``jv_app_defaults``.
    """
    import app.gui.main_window as mw

    mw.play_completion_sound = lambda: None       # keep the suite quiet
    application = mw.JVMeasurementApp(tk_root)
    # Captured here, at construction, so they cannot be polluted by whichever
    # test happens to request jv_app_defaults first.
    application._pristine_defaults = {
        "isos_protocol": application.isos_protocol_var.get(),
        "current_mode": application.current_mode,
        "rev_first": application.rev_first_var.get(),
    }
    tk_root.update_idletasks()
    return application


@pytest.fixture(scope="session")
def jv_app_defaults(jv_app):
    """State captured from a freshly-built app, before any test ran."""
    return jv_app._pristine_defaults
