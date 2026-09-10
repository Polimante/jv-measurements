"""End-to-end GUI smoke tests driven by the simulated instrument.

These exercise the real ``JVMeasurementApp`` window against a ``FakeKeithley``
(no hardware): mode switching, the "Connect to Simulator" injection path, and a
full J-V measurement that must append to ``session_data`` and write a reloadable
xlsx. Marked ``gui`` so they auto-skip in CI / headless environments (see
tests/conftest.py) and run locally on a machine with a display.

The Tk root and the JVMeasurementApp are shared across the whole test
session (see tests/conftest.py): repeated Tk() / destroy() cycles corrupt
the Tcl interpreter state, which used to make these tests pass, skip or
error depending on which GUI module ran first.
"""

from __future__ import annotations

import openpyxl
import pytest

pytestmark = pytest.mark.gui


@pytest.fixture
def app(jv_app):
    """The session-wide JVMeasurementApp (see tests/conftest.py)."""
    return jv_app


@pytest.fixture(autouse=True)
def _clean_app(app):
    """Reset the shared app to a known state before each test."""
    app.disconnect_instrument()
    app.session_data.clear()
    app.switch_mode("jv")
    app.root.update_idletasks()
    yield


ALL_MODES = ["jv", "mpp", "stab", "pure_mpp", "curation", "isos"]


def test_switch_mode_cycles_through_all_panels(app):
    """Every mode panel must show/hide without raising."""
    for mode in ALL_MODES:
        app.switch_mode(mode)
        app.root.update_idletasks()
        assert app.current_mode == mode
    # back to J-V cleanly
    app.switch_mode("jv")
    assert app.current_mode == "jv"


def test_connect_simulator_enables_start_buttons(app, monkeypatch):
    """The simulator injection flips the UI into the connected state."""
    monkeypatch.setattr(app, "_ask_simulator_mode", lambda: "ideal_stable")
    app.open_simulator_dialog()

    assert app._simulated is True
    assert "SIMULATED" in (app.instrument_idn or "")
    assert str(app.start_btn["state"]) == "normal"

    # Disconnect resets the simulated flag and the live resource.
    app.disconnect_instrument()
    assert app._simulated is False
    assert app.instrument is None


def test_full_jv_run_appends_session_and_writes_reloadable_file(app, monkeypatch, tmp_path):
    """A complete J-V run on the simulator yields one session entry + an xlsx."""
    monkeypatch.setattr(app, "_ask_simulator_mode", lambda: "ideal_stable")
    app.open_simulator_dialog()

    # Configure a fast, dialog-free illuminated-only run.
    app.sample_name_var.set("SmokeCell")
    app.export_folder_var.set(str(tmp_path))
    app.dark_jv_var.set(False)
    app.light_soaking_var.set(False)
    app.prebias_enabled_var.set(False)
    app.auto_single_diode_fit_var.set(False)
    app.start_voltage_var.set(0.0)
    app.end_voltage_var.set(1.05)   # the app default — simulated Voc must fit inside
    app.step_voltage_var.set(0.05)
    app.illumination_var.set("100.0")

    app.run_measurement()           # run synchronously in the test thread

    assert len(app.session_data) == 1
    entry = app.session_data[-1]
    assert entry["sample_name"] == "SmokeCell"
    assert entry["PCE_rev"] > 0
    assert entry["Voc_rev"] > 0

    files = list(tmp_path.glob("*.xlsx"))
    assert files, "no xlsx written by the measurement"
    wb = openpyxl.load_workbook(files[0])
    assert "MeasurementData" in wb.sheetnames


def test_auto_single_diode_fit_attaches_fit(app, monkeypatch, tmp_path):
    """With auto-fit ON the run stores a SingleDiodeFit on the session entry."""
    from app.measurement.analysis import SingleDiodeFit

    monkeypatch.setattr(app, "_ask_simulator_mode", lambda: "ideal_stable")
    app.open_simulator_dialog()

    app.sample_name_var.set("FitCell")
    app.export_folder_var.set(str(tmp_path))
    app.dark_jv_var.set(False)
    app.light_soaking_var.set(False)
    app.prebias_enabled_var.set(False)
    app.auto_single_diode_fit_var.set(True)
    app.start_voltage_var.set(0.0)
    app.end_voltage_var.set(1.25)
    app.step_voltage_var.set(0.05)
    app.illumination_var.set("100.0")

    app.run_measurement()

    fit = app.session_data[-1]["single_diode_fit"]
    assert isinstance(fit, SingleDiodeFit)


def test_stab_plot_pce_axis_stays_on_the_right(app):
    """Regression: the PCE twin axis label must stay on the right after redraw.

    ``Axes.clear()`` resets a twinx axis' ticks/label to the left; the redraw
    must restore the right side.
    """
    app.switch_mode("stab")
    app._stab_update_plot(
        times=[0, 1, 2, 3],
        j_vals=[20.0, 19.8, 19.7, 19.7],
        pce_vals=[15.0, 14.9, 14.8, 14.8],
        stabilized_at=2,
    )
    assert app.stab_ax_pce.yaxis.get_label_position() == "right"
    assert app.stab_ax_pce.get_ylabel() == "PCE (%)"


def test_dark_jv_run_on_the_simulator_needs_no_dialogs(app, monkeypatch, tmp_path):
    """A dark J-V used to stop and wait for someone to click "shutter open".

    On the simulator the shutter is a property of the fake cell, so the run must
    now go start to finish untouched — and the dark scan must actually be dark,
    which is what proves the light state followed the boundary.
    """
    import app.gui.main_window as mw
    import numpy as np

    monkeypatch.setattr(app, "_ask_simulator_mode", lambda: "ideal_stable")
    app.open_simulator_dialog()

    def no_modals(*_a, **_k):
        raise AssertionError("a dialog was shown during an unattended run")

    monkeypatch.setattr(mw.messagebox, "showinfo", no_modals)
    monkeypatch.setattr(mw.messagebox, "askokcancel", no_modals)

    app.sample_name_var.set("DarkSmoke")
    app.export_folder_var.set(str(tmp_path))
    app.dark_jv_var.set(True)
    app.light_soaking_var.set(False)
    app.prebias_enabled_var.set(False)
    app.auto_single_diode_fit_var.set(False)
    app.start_voltage_var.set(0.0)
    app.end_voltage_var.set(1.05)
    app.step_voltage_var.set(0.05)
    app.illumination_var.set("100.0")

    app.run_measurement()

    entry = app.session_data[-1]
    assert entry["v_dark_rev"] is not None, "no dark scan was recorded"

    # Compare the current at 0 V, not the peak: at 1.05 V forward bias a dark
    # diode passes as much current as a lit cell delivers, so |J|max cannot
    # tell the two apart. Jsc can — it is photocurrent by definition.
    def at_zero_volts(v, j):
        return float(abs(j[int(np.argmin(np.abs(v)))]))

    dark_jsc = at_zero_volts(entry["v_dark_rev"], entry["j_dark_rev"])
    lit_jsc = at_zero_volts(entry["v_rev"], entry["j_rev"])
    assert lit_jsc > 10 * dark_jsc, (
        f"the 'dark' scan was not dark: {dark_jsc=} vs {lit_jsc=}")

    assert any("Dark_JV" in f.name for f in tmp_path.glob("*.xlsx"))
