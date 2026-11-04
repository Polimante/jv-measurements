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


# ── Scan order ("Measure from REV to FWD") ───────────────────────────────────

def test_rev_first_box_is_off_by_default(jv_app_defaults):
    assert jv_app_defaults["rev_first"] is False


def _record_scans(monkeypatch):
    """Wrap main_window's measure_scan / apply_prebias to log the sequence."""
    import app.gui.main_window as mw

    calls = []
    real_scan = mw.measure_scan

    def scan(inst, start, end, step, *a, **k):
        calls.append(("fwd" if step > 0 else "rev", start, end))
        return real_scan(inst, start, end, step, *a, **k)

    monkeypatch.setattr(mw, "measure_scan", scan)
    monkeypatch.setattr(mw, "apply_prebias",
                        lambda *a, **k: calls.append(("prebias",)))
    return calls


def _configure_run(app, tmp_path, *, rev_first, dark=False, prebias=False):
    app.sample_name_var.set("OrderCell")
    app.export_folder_var.set(str(tmp_path))
    app.dark_jv_var.set(dark)
    app.light_soaking_var.set(False)
    app.prebias_enabled_var.set(prebias)
    app.auto_single_diode_fit_var.set(False)
    app.start_voltage_var.set(0.0)
    app.end_voltage_var.set(1.05)
    app.step_voltage_var.set(0.05)
    app.illumination_var.set("100.0")
    app.rev_first_var.set(rev_first)


@pytest.mark.parametrize("rev_first, expected", [
    (False, ["fwd", "prebias", "rev"]),
    (True,  ["prebias", "rev", "fwd"]),
])
def test_scan_order_follows_the_checkbox(app, monkeypatch, tmp_path,
                                         rev_first, expected):
    """REV->FWD runs End->Start first; pre-bias stays right before the reverse."""
    monkeypatch.setattr(app, "_ask_simulator_mode", lambda: "ideal_stable")
    app.open_simulator_dialog()
    calls = _record_scans(monkeypatch)
    _configure_run(app, tmp_path, rev_first=rev_first, prebias=True)
    try:
        app.run_measurement()
    finally:
        app.rev_first_var.set(False)

    assert [c[0] for c in calls] == expected
    rev = next(c for c in calls if c[0] == "rev")
    assert rev[1:] == (1.05, 0.0)               # End -> Start

    # The fwd / rev arrays are still the right way round, whatever the order.
    entry = app.session_data[-1]
    assert entry["v_fwd"][0] < entry["v_fwd"][-1]
    assert entry["v_rev"][0] > entry["v_rev"][-1]
    assert entry["scan_order"] == ("REV->FWD" if rev_first else "FWD->REV")

    wb = openpyxl.load_workbook(next(tmp_path.glob("OrderCell_2*.xlsx")))
    ws = wb["MeasurementData"]
    assert ws.cell(1, 28).value == "Scan Order"
    assert ws.cell(2, 28).value == entry["scan_order"]


def test_rev_first_also_orders_the_dark_scan(app, monkeypatch, tmp_path):
    monkeypatch.setattr(app, "_ask_simulator_mode", lambda: "ideal_stable")
    app.open_simulator_dialog()
    calls = _record_scans(monkeypatch)
    _configure_run(app, tmp_path, rev_first=True, dark=True)
    try:
        app.run_measurement()
    finally:
        app.rev_first_var.set(False)

    assert [c[0] for c in calls] == ["rev", "fwd", "rev", "fwd"]
    ws = openpyxl.load_workbook(next(tmp_path.glob("*_Dark_JV_*.xlsx")))["Dark_JV_Data"]
    assert ws.cell(1, 10).value == "Scan Order"
    assert ws.cell(2, 10).value == "REV->FWD"


# ── Line frequency (NPLC lasts 20 ms at 50 Hz, 16.7 ms at 60 Hz) ─────────────

def _connect_at(app, monkeypatch, tmp_path, hz):
    import app.gui.main_window as mw

    monkeypatch.setattr(mw, "read_line_frequency", lambda _inst: hz)
    monkeypatch.setattr(app, "_ask_simulator_mode", lambda: "ideal_stable")
    app.open_simulator_dialog()
    _configure_run(app, tmp_path, rev_first=False)
    app.step_voltage_var.set(0.025)
    app.nplc_var.set(5.0)


def test_connecting_recomputes_the_delay_for_the_grid(app, monkeypatch, tmp_path):
    """Scan-rate mode: 250 ms per point - NPLC 5 - 50 ms of overhead."""
    app.scan_rate_mode.set("scan_rate")
    app.target_scan_rate_var.set(100.0)
    try:
        _connect_at(app, monkeypatch, tmp_path, 50.0)
        assert app.delay_ms_var.get() == pytest.approx(100.0)     # 5 x 20 ms
        assert app._line_frequency_note() == "Line: 50 Hz"
        app.disconnect_instrument()
        assert app.delay_ms_var.get() == pytest.approx(116.7)     # 5 x 16.7 ms
    finally:
        app.scan_rate_mode.set("advanced")
        app.nplc_var.set(1.0)


def test_detected_frequency_reaches_the_saved_file(app, monkeypatch, tmp_path):
    _connect_at(app, monkeypatch, tmp_path, 50.0)
    app.delay_ms_var.set(100.0)
    try:
        app.run_measurement()
    finally:
        app.nplc_var.set(1.0)
    ws = openpyxl.load_workbook(next(tmp_path.glob("OrderCell_2*.xlsx")))["MeasurementData"]
    assert ws.cell(2, 32).value == 50
    assert ws.cell(2, 27).value == pytest.approx(100.0)       # mV/s, at 50 Hz


def test_unread_frequency_warns_and_stays_out_of_the_file(app, monkeypatch, tmp_path):
    _connect_at(app, monkeypatch, tmp_path, None)
    assert "assuming 60 Hz" in app._line_frequency_note()
    try:
        app.run_measurement()
    finally:
        app.nplc_var.set(1.0)
    ws = openpyxl.load_workbook(next(tmp_path.glob("OrderCell_2*.xlsx")))["MeasurementData"]
    assert ws.cell(2, 32).value is None


def test_clamped_scan_rate_does_not_stop_the_file_being_saved(app, monkeypatch, tmp_path):
    """Regression: the saved rate was parsed from text ending in a warning sign."""
    app.scan_rate_mode.set("scan_rate")
    app.target_scan_rate_var.set(10000.0)
    try:
        _connect_at(app, monkeypatch, tmp_path, 50.0)
        assert "\u26a0" in app.scan_rate_var.get()
        app.run_measurement()
    finally:
        app.scan_rate_mode.set("advanced")
        app.nplc_var.set(1.0)
    assert list(tmp_path.glob("OrderCell_2*.xlsx"))


# ── Measured scan time ───────────────────────────────────────────────────────

def test_measured_times_reach_status_bar_log_and_file(app, monkeypatch, tmp_path):
    monkeypatch.setattr(app, "_ask_simulator_mode", lambda: "ideal_stable")
    app.open_simulator_dialog()
    _configure_run(app, tmp_path, rev_first=False)
    app.run_measurement()

    timing = app.session_data[-1]["timing"]
    assert timing["fwd_s"] > 0 and timing["rev_s"] > 0
    assert timing["total_s"] >= timing["fwd_s"] + timing["rev_s"]
    assert timing["fwd_rate"] > 0

    status = app.status_var.get()
    assert status.startswith("Measurement complete")
    assert "FWD" in status and "REV" in status and "total" in status
    assert "Scan time: FWD" in app.log_text.get("1.0", "end")

    ws = openpyxl.load_workbook(next(tmp_path.glob("OrderCell_2*.xlsx")))["MeasurementData"]
    assert ws.cell(2, 33).value == pytest.approx(timing["fwd_s"])
    assert ws.cell(2, 34).value == pytest.approx(timing["rev_s"])
    assert ws.cell(2, 35).value == pytest.approx(timing["total_s"])


def test_scan_clock_excludes_the_pre_bias(app, monkeypatch, tmp_path):
    import app.gui.main_window as mw
    import time

    monkeypatch.setattr(app, "_ask_simulator_mode", lambda: "ideal_stable")
    app.open_simulator_dialog()
    _configure_run(app, tmp_path, rev_first=False, prebias=True)
    monkeypatch.setattr(mw, "apply_prebias", lambda *a, **k: time.sleep(1.0))
    app.run_measurement()

    timing = app.session_data[-1]["timing"]
    assert timing["rev_s"] < 1.0                 # the sweep alone
    assert timing["total_s"] > 1.0               # the whole run


def test_dark_file_records_its_scan_times(app, monkeypatch, tmp_path):
    monkeypatch.setattr(app, "_ask_simulator_mode", lambda: "ideal_stable")
    app.open_simulator_dialog()
    _configure_run(app, tmp_path, rev_first=False, dark=True)
    app.run_measurement()
    ws = openpyxl.load_workbook(next(tmp_path.glob("*_Dark_JV_*.xlsx")))["Dark_JV_Data"]
    assert ws.cell(1, 11).value == "Forward Scan Time (s)"
    assert ws.cell(2, 11).value > 0
    assert ws.cell(2, 12).value > 0


# ── Pixels per sample ────────────────────────────────────────────────────────

def _measure_pixels(app, monkeypatch, tmp_path, pixels, runs):
    """Run *runs* measurements; return (curves on the plot, first colour) after each."""
    monkeypatch.setattr(app, "_ask_simulator_mode", lambda: "ideal_stable")
    app.open_simulator_dialog()
    _configure_run(app, tmp_path, rev_first=False)
    app.pixels_per_sample_var.set(pixels)
    seen = []
    try:
        for _ in range(runs):
            app.run_measurement()
            # Each measurement draws one forward and one reverse J-V line.
            seen.append(len(app.ax.get_lines()) // 2)
    finally:
        app.pixels_per_sample_var.set("0")
    return seen


def test_plot_keeps_a_substrate_together_then_starts_over(app, monkeypatch, tmp_path):
    assert _measure_pixels(app, monkeypatch, tmp_path, "2", 5) == [1, 2, 1, 2, 1]
    assert len(app.session_data) == 5                 # only the view was cleared
    assert len(list(tmp_path.glob("OrderCell_2*.xlsx"))) >= 1
    # Colours restart on each substrate, so pixel 1 always looks the same.
    colours = [e["color"] for e in app.session_data]
    assert colours[0] == colours[2] == colours[4]
    assert colours[1] == colours[3] != colours[0]


def test_status_bar_shows_the_pixel_position(app, monkeypatch, tmp_path):
    monkeypatch.setattr(app, "_ask_simulator_mode", lambda: "ideal_stable")
    app.open_simulator_dialog()
    _configure_run(app, tmp_path, rev_first=False)
    app.pixels_per_sample_var.set("5")
    try:
        app.run_measurement()
        assert app.status_var.get().startswith("Pixel 1/5  |  Measurement complete")
        app.run_measurement()
        assert app.status_var.get().startswith("Pixel 2/5")
    finally:
        app.pixels_per_sample_var.set("0")


def test_default_keeps_every_curve(app, monkeypatch, tmp_path):
    assert _measure_pixels(app, monkeypatch, tmp_path, "0", 3) == [1, 2, 3]
    assert not app.status_var.get().startswith("Pixel")


def test_clear_session_restarts_the_count(app, monkeypatch, tmp_path):
    import app.gui.main_window as mw

    assert _measure_pixels(app, monkeypatch, tmp_path, "2", 3) == [1, 2, 1]
    monkeypatch.setattr(mw.messagebox, "askokcancel", lambda *a, **k: True)
    app._clear_session_data()
    assert app._plot_start_idx == 0
    assert _measure_pixels(app, monkeypatch, tmp_path, "2", 2) == [1, 2]


def test_invalid_pixel_count_is_refused_before_measuring(app, monkeypatch, tmp_path):
    import app.gui.main_window as mw

    errors = []
    monkeypatch.setattr(mw.messagebox, "showerror", lambda *a, **k: errors.append(a))
    monkeypatch.setattr(app, "_ask_simulator_mode", lambda: "ideal_stable")
    app.open_simulator_dialog()
    _configure_run(app, tmp_path, rev_first=False)
    app.pixels_per_sample_var.set("abc")
    try:
        assert not app.validate_inputs()
    finally:
        app.pixels_per_sample_var.set("0")
    assert errors
