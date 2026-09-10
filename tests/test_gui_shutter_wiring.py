"""How the measurement code drives the shutter.

Two things are pinned here. First, the rule that keeps this change safe for
existing users: a rig left on the **manual** backend sees exactly the dialogs it
always saw — no more, no fewer — while an automatic backend drives the shutter
at every boundary. Second, that every measurement ends with the shutter closed,
including when the work raises.

Marked ``gui``: these drive the real JVMeasurementApp.
"""

from __future__ import annotations

import re

import pytest

from app.shutter import CLOSED, OPEN, ShutterError, default_config
from app.utils.constants import stop_event

pytestmark = pytest.mark.gui


class FakeTSP:
    """A 2600 stand-in whose digital I/O lines idle high."""

    _WRITEBIT = re.compile(r"digio\.writebit\(\s*(\d+)\s*,\s*(\d+)\s*\)")

    def __init__(self, fail=False) -> None:
        self.levels = {n: 1 for n in range(1, 15)}
        self.fail = fail
        self.scpi: list[str] = []
        self.current = 0.0

    def write_raw(self, tsp: str) -> None:
        m = self._WRITEBIT.search(tsp)
        if m is None:
            return
        if self.fail:
            raise OSError("VISA timeout")
        self.levels[int(m.group(1))] = int(m.group(2))

    def query_raw(self, cmd: str) -> str:
        m = re.search(r"digio\.readbit\(\s*(\d+)\s*\)", cmd)
        return f"{self.levels[int(m.group(1))]:.8e}\n"

    def write(self, cmd):
        self.scpi.append(cmd)

    def query(self, cmd):
        if cmd.strip() == ":READ?":
            return f"0.0,{self.current:.8e},0,0,0"
        raise AssertionError(f"unexpected query {cmd!r}")

    def close(self):
        pass


@pytest.fixture
def app(jv_app):
    """The session-wide JVMeasurementApp (see tests/conftest.py)."""
    return jv_app


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    from app.io import app_settings
    monkeypatch.setattr(app_settings, "settings_dir", lambda: str(tmp_path))
    monkeypatch.setattr(app_settings, "settings_path",
                        lambda: str(tmp_path / "settings.json"))


@pytest.fixture(autouse=True)
def _reset(app):
    app.shutter.detach_instrument()
    app.shutter.configure(default_config())
    app._simulated = False
    app.instrument = None
    app._measurement_running = False
    stop_event.clear()
    yield
    stop_event.clear()


@pytest.fixture
def prompts(monkeypatch):
    """Capture every modal the app would show."""
    seen = {"info": [], "okcancel": [], "error": [], "warning": []}
    monkeypatch.setattr("app.gui.main_window.messagebox.showinfo",
                        lambda *a, **k: seen["info"].append(a))
    monkeypatch.setattr("app.gui.main_window.messagebox.showerror",
                        lambda *a, **k: seen["error"].append(a))
    monkeypatch.setattr("app.gui.main_window.messagebox.showwarning",
                        lambda *a, **k: seen["warning"].append(a))
    return seen


def _use_keithley(app, inst=None, **overrides):
    cfg = default_config()
    cfg["backend"] = "keithley_digio"
    cfg["options"]["keithley_digio"].update({"settle_ms": 0, **overrides})
    app.shutter.configure(cfg)
    if inst is not None:
        app.instrument = inst
        app.shutter.attach_instrument(inst)
    return app.shutter


# ── The manual backend keeps its old behaviour, exactly ─────────────────────

def test_manual_backend_is_silent_where_it_never_prompted(app, prompts):
    """MPP, Stabilized Current and friends never asked; they still do not."""
    assert app._shutter_set(True, prompt_if_manual=False) is True
    assert prompts["info"] == []


def test_manual_backend_still_prompts_where_it_always_did(app, prompts):
    """The dark/illuminated handover in J-V, D-1 and V-1."""
    assert app._shutter_set(True, prompt_if_manual=True) is True
    assert len(prompts["info"]) == 1
    assert "Open Shutter" in prompts["info"][0][0]
    assert app.shutter.state == OPEN


def test_a_refused_manual_prompt_aborts_the_measurement(app, monkeypatch):
    def refuse(_action):
        raise ShutterError("operator cancelled")

    monkeypatch.setattr(app.shutter, "_prompt", refuse, raising=False)
    app.shutter.configure(default_config())      # rebuild with the refusing prompt

    assert app._shutter_set(True, prompt_if_manual=True) is False
    assert stop_event.is_set()


# ── The simulator never asks anyone to move a physical blade ────────────────

def test_the_simulator_follows_the_shutter_without_dialogs(app, prompts):
    class FakeCell:
        def __init__(self):
            self.lit = None

        def set_light(self, on):
            self.lit = on

    app._simulated = True
    app.instrument = FakeCell()

    assert app._shutter_set(False, prompt_if_manual=True) is True
    assert app.instrument.lit is False
    assert app._shutter_set(True, prompt_if_manual=True) is True
    assert app.instrument.lit is True
    assert prompts["info"] == []


# ── An automatic backend drives everything ──────────────────────────────────

def test_an_automatic_backend_acts_at_every_boundary(app, prompts):
    inst = FakeTSP()
    _use_keithley(app, inst)

    assert app._shutter_set(True, prompt_if_manual=False) is True
    assert inst.levels[1] == 1
    assert app._shutter_set(False, prompt_if_manual=False) is True
    assert inst.levels[1] == 0
    assert prompts["info"] == []


def test_a_hardware_failure_offers_a_hand_move(app, monkeypatch):
    _use_keithley(app, FakeTSP(fail=True))
    asked = []
    monkeypatch.setattr("app.gui.main_window.messagebox.askokcancel",
                        lambda *a, **k: asked.append(a) or True)

    assert app._shutter_set(True, prompt_if_manual=False) is True
    assert len(asked) == 1
    assert "could not be opened automatically" in asked[0][1]
    assert "VISA timeout" in asked[0][1]          # says why, not just that
    assert not stop_event.is_set()


def test_declining_the_hand_move_aborts(app, monkeypatch):
    _use_keithley(app, FakeTSP(fail=True))
    monkeypatch.setattr("app.gui.main_window.messagebox.askokcancel",
                        lambda *a, **k: False)

    assert app._shutter_set(True, prompt_if_manual=False) is False
    assert stop_event.is_set()


# ── Every measurement ends closed ───────────────────────────────────────────

def test_run_illuminated_opens_then_closes(app):
    inst = FakeTSP()
    _use_keithley(app, inst)
    seen = []

    app._run_illuminated(lambda: seen.append(inst.levels[1]))()

    assert seen == [1]              # open while the work ran
    assert inst.levels[1] == 0      # closed afterwards


def test_run_illuminated_closes_even_when_the_work_raises(app):
    inst = FakeTSP()
    _use_keithley(app, inst)

    def boom():
        raise RuntimeError("scan blew up")

    with pytest.raises(RuntimeError):
        app._run_illuminated(boom)()
    assert inst.levels[1] == 0


def test_run_illuminated_does_not_run_the_work_if_the_shutter_aborts(
        app, monkeypatch):
    _use_keithley(app, FakeTSP(fail=True))
    monkeypatch.setattr("app.gui.main_window.messagebox.askokcancel",
                        lambda *a, **k: False)
    ran = []
    app._run_illuminated(lambda: ran.append(1))()
    assert ran == []


def test_close_if_automatic_skips_the_manual_backend(app, prompts):
    assert app._shutter_close_if_automatic() is None
    assert prompts["info"] == []


# ── Safety-check wording follows the backend ────────────────────────────────

def test_safety_line_asks_the_operator_on_a_manual_rig(app):
    assert app._shutter_safety_line() == \
        "2. Confirm the solar simulator shutter is open"
    assert "CLOSED" in app._shutter_safety_line(want_open=False)


def test_safety_line_stops_asking_once_it_is_automatic(app):
    _use_keithley(app, FakeTSP())
    assert app._shutter_safety_line() == \
        "2. The shutter will be opened automatically"
    assert app._shutter_safety_line(want_open=False) == \
        "2. The shutter will be closed automatically"


def test_safety_line_says_so_on_the_simulator(app):
    app._simulated = True
    assert "Simulated run" in app._shutter_safety_line()


# ── Verification by measurement (off by default) ────────────────────────────

def _use_verification(app, inst, threshold_a=1e-6):
    cfg = default_config()
    cfg["backend"] = "keithley_digio"
    cfg["options"]["keithley_digio"]["settle_ms"] = 0
    cfg["verify_with_current"] = True
    cfg["verify_threshold_a"] = threshold_a
    app.shutter.configure(cfg)
    app.instrument = inst
    app.shutter.attach_instrument(inst)


def test_verification_is_off_by_default(app):
    inst = FakeTSP()
    _use_keithley(app, inst)
    inst.current = 0.0               # would look dark
    assert app._shutter_set(True, prompt_if_manual=False) is True
    assert ":READ?" not in inst.scpi


def test_verification_passes_when_the_cell_sees_the_expected_light(app):
    inst = FakeTSP()
    _use_verification(app, inst)
    inst.current = 2.4e-3            # illuminated
    assert app._shutter_set(True, prompt_if_manual=False) is True
    assert not stop_event.is_set()


def test_a_dark_scan_that_is_secretly_lit_is_caught(app, monkeypatch):
    """The dangerous case on non-fail-safe wiring: it looks plausible."""
    inst = FakeTSP()
    _use_verification(app, inst)
    inst.current = 2.4e-3            # still lit, though we asked for dark
    asked = []
    monkeypatch.setattr("app.gui.main_window.messagebox.askokcancel",
                        lambda *a, **k: asked.append(a) or False)

    assert app._shutter_set(False, prompt_if_manual=False) is False
    assert stop_event.is_set()
    assert asked and "commanded DARK" in asked[0][1]


def test_the_operator_may_override_a_verification_mismatch(app, monkeypatch):
    inst = FakeTSP()
    _use_verification(app, inst)
    inst.current = 1e-11
    monkeypatch.setattr("app.gui.main_window.messagebox.askokcancel",
                        lambda *a, **k: True)

    assert app._shutter_set(True, prompt_if_manual=False) is True
    assert not stop_event.is_set()


def test_verification_never_fails_a_run_on_its_own_error(app):
    class Broken(FakeTSP):
        def query(self, cmd):
            raise OSError("GPIB gone")

    _use_verification(app, Broken())
    assert app._shutter_set(True, prompt_if_manual=False) is True
    assert not stop_event.is_set()
