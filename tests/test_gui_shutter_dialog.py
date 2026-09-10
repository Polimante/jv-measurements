"""GUI tests for the Shutter Control window (Instrument → Shutter Control…).

The window builds itself from the selected backend's ``BackendSpec.fields``, so
these tests double as the proof that a backend needs no GUI code: switching the
dropdown to the Keithley backend must produce its four settings widgets without
this file (or shutter_dialog.py) naming any of them.

Marked ``gui`` so they auto-skip in CI / headless environments.
"""

from __future__ import annotations

import re

import pytest

from app.gui.shutter_dialog import ShutterDialog
from app.shutter import CLOSED, OPEN, UNKNOWN, default_config

pytestmark = pytest.mark.gui


class FakeTSP:
    """A 2600 stand-in whose digital I/O lines idle high."""

    _WRITEBIT = re.compile(r"digio\.writebit\(\s*(\d+)\s*,\s*(\d+)\s*\)")

    def __init__(self) -> None:
        self.levels = {n: 1 for n in range(1, 15)}
        self.raw_writes: list[str] = []

    def write_raw(self, tsp: str) -> None:
        self.raw_writes.append(tsp)
        m = self._WRITEBIT.search(tsp)
        if m:
            self.levels[int(m.group(1))] = int(m.group(2))

    def write(self, cmd):
        pass

    def query_raw(self, cmd: str) -> str:
        m = re.search(r"digio\.readbit\(\s*(\d+)\s*\)", cmd)
        if m:
            return f"{self.levels[int(m.group(1))]:.8e}\n"
        raise AssertionError(f"unexpected query {cmd!r}")

    def close(self):
        pass


@pytest.fixture(scope="module")
def app():
    import tkinter as tk
    import app.gui.main_window as mw

    mw.play_completion_sound = lambda: None  # type: ignore[assignment]
    root = tk.Tk()
    root.withdraw()
    application = mw.JVMeasurementApp(root)
    try:
        yield application
    finally:
        root.update_idletasks()
        root.destroy()


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    """Never touch the developer's real ~/.jv-measurement/settings.json."""
    from app.io import app_settings
    monkeypatch.setattr(app_settings, "settings_dir", lambda: str(tmp_path))
    monkeypatch.setattr(app_settings, "settings_path",
                        lambda: str(tmp_path / "settings.json"))


@pytest.fixture(autouse=True)
def _reset(app):
    """Put the manager back on the manual backend and close any window."""
    existing = getattr(app, "_shutter_dialog", None)
    if existing is not None and existing.winfo_exists():
        existing.destroy()
    app._shutter_dialog = None
    app.shutter.detach_instrument()
    app.shutter.configure(default_config())
    app._measurement_running = False
    app.root.update_idletasks()
    yield


@pytest.fixture
def dlg(app):
    window = app.open_shutter_dialog()
    app.root.update_idletasks()
    yield window
    if window.winfo_exists():
        window.destroy()


def _select_backend(app, dlg, label_fragment):
    label = next(l for l in dlg._specs if label_fragment in l)
    dlg.backend_var.set(label)
    dlg._on_backend_selected()
    app.root.update_idletasks()


def _configure_keithley(app, dlg, **overrides):
    _select_backend(app, dlg, "Keithley")
    dlg.field_vars["settle_ms"][1].set("0")
    for name, value in overrides.items():
        dlg.field_vars[name][1].set(value)
    assert dlg.apply() is True


# ── The window itself ────────────────────────────────────────────────────────

def test_the_window_opens_from_the_app_and_is_a_single_instance(app):
    first = app.open_shutter_dialog()
    app.root.update_idletasks()
    assert isinstance(first, ShutterDialog)

    second = app.open_shutter_dialog()
    assert second is first          # raised, not duplicated
    first.destroy()


def test_the_instrument_menu_carries_the_entry(app):
    labels = [app.instrument_menu.entrycget(i, "label")
              for i in range(app.instrument_menu.index("end") + 1)
              if app.instrument_menu.type(i) == "command"]
    assert any("Shutter Control" in l for l in labels)


# ── Rendering ────────────────────────────────────────────────────────────────

def test_defaults_to_manual_with_no_settings_fields(app, dlg):
    assert app.shutter.backend_id == "manual"
    assert app.shutter.is_automatic is False
    assert dlg.field_vars == {}
    assert "manual" in app.shutter_state_var.get().lower()


def test_selecting_a_backend_renders_its_declared_fields(app, dlg):
    _select_backend(app, dlg, "Keithley")
    # shutter_dialog.py never names these — they come from the BackendSpec.
    assert set(dlg.field_vars) == {"line", "active_high", "settle_ms", "verify"}
    assert dlg.field_vars["line"][1].get() == "1"
    assert dlg.field_vars["active_high"][1].get() is True


def test_switching_backends_swaps_the_form_and_description(app, dlg):
    _select_backend(app, dlg, "Keithley")
    assert dlg.field_vars
    keithley_desc = dlg.desc_var.get()

    _select_backend(app, dlg, "Manual")
    assert dlg.field_vars == {}
    assert dlg.desc_var.get() != keithley_desc


# ── Validation ───────────────────────────────────────────────────────────────

def test_an_out_of_range_line_is_refused_with_a_message(app, dlg, monkeypatch):
    _select_backend(app, dlg, "Keithley")
    shown = []
    monkeypatch.setattr("app.gui.shutter_dialog.messagebox.showerror",
                        lambda title, msg, **kw: shown.append(msg))

    dlg.field_vars["line"][1].set("99")
    assert dlg.apply() is False
    assert shown and "at most 14" in shown[0]
    assert app.shutter.config["options"]["keithley_digio"]["line"] == 1


def test_a_non_numeric_settle_time_is_refused(app, dlg, monkeypatch):
    _select_backend(app, dlg, "Keithley")
    shown = []
    monkeypatch.setattr("app.gui.shutter_dialog.messagebox.showerror",
                        lambda title, msg, **kw: shown.append(msg))

    dlg.field_vars["settle_ms"][1].set("soon")
    assert dlg.apply() is False
    assert shown and "expected a whole number" in shown[0]


# ── Persistence ──────────────────────────────────────────────────────────────

def test_apply_activates_and_persists_the_configuration(app, dlg):
    _configure_keithley(app, dlg, line="3", settle_ms="120", active_high=False)

    assert app.shutter.backend_id == "keithley_digio"
    assert "line 3" in app.shutter.describe()
    assert "active LOW" in app.shutter.describe()

    from app.shutter.manager import ShutterManager
    stored = ShutterManager.load_config()
    assert stored["backend"] == "keithley_digio"
    assert stored["options"]["keithley_digio"]["line"] == 3
    assert stored["options"]["keithley_digio"]["settle_ms"] == 120


def test_options_of_the_other_backend_survive_a_switch(app, dlg):
    _configure_keithley(app, dlg, line="6")
    _select_backend(app, dlg, "Manual")
    assert app.shutter.config["options"]["keithley_digio"]["line"] == 6


def test_the_configuration_outlives_the_window(app, dlg):
    _configure_keithley(app, dlg, line="4")
    dlg.destroy()
    app._shutter_dialog = None
    assert "line 4" in app.shutter.describe()

    reopened = app.open_shutter_dialog()
    assert reopened.field_vars["line"][1].get() == "4"
    reopened.destroy()


# ── Test buttons ─────────────────────────────────────────────────────────────

def test_test_buttons_move_the_shutter_and_update_the_badge(app, dlg):
    inst = FakeTSP()
    app.shutter.attach_instrument(inst)
    _configure_keithley(app, dlg)

    dlg.test("open")
    app.root.update_idletasks()
    assert inst.levels[1] == 1
    assert app.shutter.state == OPEN
    assert "OPEN" in app.shutter_state_var.get()
    assert dlg.badge.cget("fg") == "#E65100"

    dlg.test("close")
    app.root.update_idletasks()
    assert inst.levels[1] == 0
    assert app.shutter.state == CLOSED
    assert "CLOSED" in app.shutter_state_var.get()
    assert dlg.badge.cget("fg") == "#2E7D32"


def test_a_test_button_applies_the_form_before_acting(app, dlg):
    """No 'you forgot to click Apply' failure mode."""
    inst = FakeTSP()
    app.shutter.attach_instrument(inst)
    _configure_keithley(app, dlg)
    dlg.field_vars["line"][1].set("5")      # typed, never applied

    dlg.test("close")
    assert inst.levels[5] == 0              # acted on line 5, not 1


def test_self_test_ends_with_the_shutter_closed(app, dlg, monkeypatch):
    monkeypatch.setattr("app.gui.shutter_dialog.messagebox.showinfo",
                        lambda *a, **k: None)
    inst = FakeTSP()
    app.shutter.attach_instrument(inst)
    _configure_keithley(app, dlg)

    dlg.self_test()
    assert [w for w in inst.raw_writes if "writebit" in w] == [
        "digio.writebit(1, 0)", "digio.writebit(1, 1)", "digio.writebit(1, 0)"]
    assert app.shutter.state == CLOSED


def test_a_failing_test_reports_instead_of_raising(app, dlg, monkeypatch):
    shown = []
    monkeypatch.setattr("app.gui.shutter_dialog.messagebox.showerror",
                        lambda title, msg, **kw: shown.append(msg))
    _select_backend(app, dlg, "Keithley")      # no instrument attached
    dlg.test("close")
    assert shown and "No instrument is connected" in shown[0]
    assert "failed" in dlg.result_var.get()


def test_the_window_is_locked_while_a_measurement_runs(app, dlg, monkeypatch):
    told = []
    monkeypatch.setattr("app.gui.main_window.messagebox.showinfo",
                        lambda title, msg: told.append(msg))
    inst = FakeTSP()
    app.shutter.attach_instrument(inst)
    _configure_keithley(app, dlg)
    before = dict(inst.levels)

    app._measurement_running = True
    dlg.test("open")
    assert inst.levels == before                     # nothing moved
    assert told and "measurement is running" in told[0]


# ── Connect / disconnect (app level, independent of the window) ──────────────

def test_close_on_connect_asserts_a_known_state(app, dlg):
    """The shutter idles OPEN on non-fail-safe wiring; connecting must close it."""
    inst = FakeTSP()
    _configure_keithley(app, dlg)

    assert inst.levels[1] == 1                       # idles high == open
    app.shutter.attach_instrument(inst)
    app._shutter_close_on_connect()

    assert inst.levels[1] == 0
    assert app.shutter.state == CLOSED


def test_close_on_connect_is_skipped_for_the_manual_backend(app, monkeypatch):
    """It must not pop a dialog every time you connect the instrument."""
    prompts = []
    monkeypatch.setattr("app.gui.main_window.messagebox.showinfo",
                        lambda *a, **k: prompts.append(a))
    app._shutter_close_on_connect()
    assert prompts == []


def test_disconnecting_never_prompts_on_the_manual_backend(app, monkeypatch):
    """Regression: _close_instrument() also runs on application exit.

    A manual backend closing there would show a modal 'please close the
    shutter' box on every quit — and block a headless test run forever.
    """
    prompts = []
    monkeypatch.setattr("app.gui.main_window.messagebox.showinfo",
                        lambda *a, **k: prompts.append(a))
    app.instrument = FakeTSP()
    app._close_instrument()
    assert prompts == []
    assert app.instrument is None


def test_disconnecting_closes_an_automatic_shutter(app, dlg):
    inst = FakeTSP()
    _configure_keithley(app, dlg)
    app.instrument = inst
    app.shutter.attach_instrument(inst)
    app.shutter.open()
    assert inst.levels[1] == 1

    app._close_instrument()
    assert inst.levels[1] == 0          # closed before the session went away


def test_badge_reports_unknown_before_anything_is_commanded(app, dlg):
    _select_backend(app, dlg, "Keithley")
    app.shutter.attach_instrument(FakeTSP())
    app._shutter_refresh_badge(UNKNOWN, True, True)
    assert "unknown" in app.shutter_state_var.get().lower()
