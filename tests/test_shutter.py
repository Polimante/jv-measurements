"""Hardware-free tests for the shutter abstraction (app/shutter/)."""

import re

import pytest

from app.shutter import (
    BACKENDS,
    CLOSED,
    DEFAULT_BACKEND,
    OPEN,
    UNKNOWN,
    ConfigField,
    ShutterError,
    available_backends,
    create_shutter,
    default_config,
    get_backend,
)
from app.shutter.keithley_digio import KeithleyDigioShutter
from app.shutter.manager import ShutterManager
from app.shutter.manual import ManualShutter


# ── Fakes ────────────────────────────────────────────────────────────────────

class FakeTSP:
    """A 2600 whose shutter follows one digital I/O line."""

    _WRITEBIT = re.compile(r"digio\.writebit\(\s*(\d+)\s*,\s*(\d+)\s*\)")

    def __init__(self, stuck_level=None, fail_writes=0) -> None:
        self.levels = {n: 1 for n in range(1, 15)}   # lines idle high
        self.raw_writes: list[str] = []
        self.scpi_writes: list[str] = []
        self.stuck_level = stuck_level               # simulates write-protect
        # Transient actuation failures to inject. Scoped to writebit so the
        # best-effort bypass write at attach time does not consume them.
        self.fail_writes = fail_writes

    def write_raw(self, tsp: str) -> None:
        self.raw_writes.append(tsp)
        m = self._WRITEBIT.search(tsp)
        if m is None:
            return
        if self.fail_writes > 0:
            self.fail_writes -= 1
            raise OSError("VISA timeout")
        if self.stuck_level is None:
            self.levels[int(m.group(1))] = int(m.group(2))

    def write(self, cmd: str) -> None:
        self.scpi_writes.append(cmd)

    def query(self, cmd: str) -> str:
        m = re.search(r"digio\.readbit\(\s*(\d+)\s*\)", cmd)
        if m:
            line = int(m.group(1))
            level = self.stuck_level if self.stuck_level is not None \
                else self.levels[line]
            return f"{level:.8e}\n"
        raise AssertionError(f"unexpected query {cmd!r}")


class RecordingPrompt:
    """Stands in for the GUI's modal shutter dialog."""

    def __init__(self, refuse=False) -> None:
        self.calls: list[str] = []
        self.refuse = refuse

    def __call__(self, action: str) -> None:
        self.calls.append(action)
        if self.refuse:
            raise ShutterError("operator cancelled")


@pytest.fixture(autouse=True)
def _no_settle(monkeypatch):
    monkeypatch.setattr("app.shutter.keithley_digio.time.sleep", lambda *_: None)


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    """Keep every test off the real ~/.jv-measurement/settings.json."""
    monkeypatch.setattr("app.io.app_settings.settings_dir",
                        lambda: str(tmp_path))
    monkeypatch.setattr("app.io.app_settings.settings_path",
                        lambda: str(tmp_path / "settings.json"))


# ── ConfigField ──────────────────────────────────────────────────────────────

def test_config_field_coerces_ints_within_bounds():
    f = ConfigField("line", "Line", "int", 1, minimum=1, maximum=14)
    assert f.coerce("7") == 7
    with pytest.raises(ValueError, match="at least"):
        f.coerce(0)
    with pytest.raises(ValueError, match="at most"):
        f.coerce(15)


def test_config_field_rejects_non_numeric_with_a_readable_message():
    f = ConfigField("settle_ms", "Settling time", "int", 300)
    with pytest.raises(ValueError, match="Settling time: expected a whole number"):
        f.coerce("soon")


def test_config_field_bool_accepts_gui_strings():
    f = ConfigField("active_high", "HIGH opens", "bool", True)
    assert f.coerce(True) is True
    assert f.coerce("1") is True
    assert f.coerce("false") is False
    assert f.coerce(0) is False


def test_config_field_choice_validates_membership():
    f = ConfigField("mode", "Mode", "choice", "a", choices=("a", "b"))
    assert f.coerce("b") == "b"
    with pytest.raises(ValueError):
        f.coerce("c")


# ── Registry ─────────────────────────────────────────────────────────────────

def test_manual_is_the_default_and_comes_first():
    assert DEFAULT_BACKEND == "manual"
    assert available_backends()[0].id == "manual"
    assert {s.id for s in available_backends()} == set(BACKENDS)


def test_every_backend_declares_a_usable_spec():
    # This is the contract that lets the GUI render a form it knows nothing
    # about: a new backend that violates it fails here, not at runtime.
    for spec in available_backends():
        assert spec.id and spec.label and spec.description
        assert isinstance(spec.requires_instrument, bool)
        assert isinstance(spec.is_automatic, bool)
        for f in spec.fields:
            assert f.kind in ("int", "float", "bool", "str", "choice")
            assert f.coerce(f.default) == f.default
            if f.kind == "choice":
                assert f.choices


def test_every_backend_builds_from_its_own_defaults():
    for spec in available_backends():
        shutter = create_shutter(spec.id, spec.defaults(),
                                 prompt=RecordingPrompt())
        assert shutter.describe()
        assert shutter.state == UNKNOWN


def test_unknown_backend_id_falls_back_to_manual():
    assert get_backend("does_not_exist").id == "manual"
    assert isinstance(create_shutter("does_not_exist", {}), ManualShutter)


def test_default_config_carries_options_for_every_backend():
    cfg = default_config()
    assert cfg["backend"] == "manual"
    assert set(cfg["options"]) == set(BACKENDS)
    assert cfg["options"]["keithley_digio"]["line"] == 1


def test_spec_coerce_fills_missing_options_with_defaults():
    spec = get_backend("keithley_digio")
    assert spec.coerce({"line": 5}) == {
        "line": 5, "active_high": True, "settle_ms": 300, "verify": True}


# ── Manual backend ───────────────────────────────────────────────────────────

def test_manual_shutter_prompts_and_tracks_state():
    prompt = RecordingPrompt()
    s = ManualShutter(prompt)
    assert s.state == UNKNOWN
    s.open()
    assert s.state == OPEN
    s.close()
    assert s.state == CLOSED
    assert prompt.calls == ["open", "close"]


def test_manual_shutter_keeps_state_unknown_when_the_operator_refuses():
    s = ManualShutter(RecordingPrompt(refuse=True))
    with pytest.raises(ShutterError):
        s.open()
    assert s.state == UNKNOWN


def test_manual_shutter_without_a_prompt_is_not_ready():
    s = ManualShutter(None)
    assert s.ready is False
    with pytest.raises(ShutterError, match="needs the GUI"):
        s.open()


# ── Keithley digital I/O backend ─────────────────────────────────────────────

def test_digio_active_high_writes_the_measured_polarity():
    inst = FakeTSP()
    s = KeithleyDigioShutter(line=1, active_high=True, settle_ms=0)
    s.attach_instrument(inst)
    s.open()
    assert "digio.writebit(1, 1)" in inst.raw_writes
    assert s.state == OPEN
    s.close()
    assert "digio.writebit(1, 0)" in inst.raw_writes
    assert s.state == CLOSED


def test_digio_active_low_inverts_both_levels():
    inst = FakeTSP()
    s = KeithleyDigioShutter(line=4, active_high=False, settle_ms=0)
    s.attach_instrument(inst)
    s.open()
    s.close()
    writes = [w for w in inst.raw_writes if "writebit" in w]
    assert writes == ["digio.writebit(4, 0)", "digio.writebit(4, 1)"]


def test_digio_uses_write_raw_not_the_scpi_translation_path():
    # Going through TSPTransport.write would raise UnsupportedSCPICommand.
    inst = FakeTSP()
    s = KeithleyDigioShutter(line=1, settle_ms=0)
    s.attach_instrument(inst)
    s.close()
    assert inst.scpi_writes == []
    assert any("writebit" in w for w in inst.raw_writes)


def test_digio_sends_a_nil_guarded_bypass_once_on_attach():
    # Firmware 1.3.x has no digio.TRIG_BYPASS; assigning nil logs -104 rather
    # than raising, so the guard has to be a Lua nil check.
    inst = FakeTSP()
    KeithleyDigioShutter(line=2, settle_ms=0).attach_instrument(inst)
    assert inst.raw_writes == [
        "if digio.trigger ~= nil and digio.TRIG_BYPASS ~= nil then "
        "digio.trigger[2].mode = digio.TRIG_BYPASS end"
    ]


def test_digio_verification_catches_a_line_that_will_not_move():
    inst = FakeTSP(stuck_level=1)      # write-protected, or held by something
    s = KeithleyDigioShutter(line=1, active_high=True, settle_ms=0, verify=True)
    s.attach_instrument(inst)
    with pytest.raises(ShutterError, match="reads back as"):
        s.close()
    assert s.state == UNKNOWN


def test_digio_verification_can_be_switched_off():
    inst = FakeTSP(stuck_level=1)
    s = KeithleyDigioShutter(line=1, settle_ms=0, verify=False)
    s.attach_instrument(inst)
    s.close()
    assert s.state == CLOSED


def test_digio_without_an_instrument_is_not_ready_and_says_why():
    s = KeithleyDigioShutter(line=1, settle_ms=0)
    assert s.ready is False
    with pytest.raises(ShutterError, match="No instrument is connected"):
        s.open()


def test_digio_wraps_transport_failures_as_shutter_errors():
    inst = FakeTSP(fail_writes=99)
    s = KeithleyDigioShutter(line=1, settle_ms=0)
    s.attach_instrument(inst)
    with pytest.raises(ShutterError, match="digital I/O port failed"):
        s.close()


def test_digio_rejects_an_impossible_line():
    with pytest.raises(ValueError):
        KeithleyDigioShutter(line=15)
    with pytest.raises(ValueError):
        KeithleyDigioShutter(line=0)


def test_digio_describe_names_the_wiring():
    s = KeithleyDigioShutter(line=1, active_high=True, settle_ms=300)
    assert "line 1" in s.describe()
    assert "active HIGH" in s.describe()
    assert "300 ms" in s.describe()


def test_digio_self_test_runs_close_open_close():
    inst = FakeTSP()
    s = KeithleyDigioShutter(line=1, settle_ms=0)
    s.attach_instrument(inst)
    report = s.self_test()
    writes = [w for w in inst.raw_writes if "writebit" in w]
    assert writes == ["digio.writebit(1, 0)",
                      "digio.writebit(1, 1)",
                      "digio.writebit(1, 0)"]
    assert s.state == CLOSED
    assert report.count("ok") == 3


# ── Manager ──────────────────────────────────────────────────────────────────

def _digio_manager(inst=None, **options):
    cfg = default_config()
    cfg["backend"] = "keithley_digio"
    cfg["options"]["keithley_digio"].update({"settle_ms": 0, **options})
    mgr = ShutterManager(cfg)
    if inst is not None:
        mgr.attach_instrument(inst)
    return mgr


def test_manager_defaults_to_manual_so_nothing_changes_for_existing_users():
    mgr = ShutterManager(prompt=RecordingPrompt())
    assert mgr.backend_id == "manual"
    assert mgr.is_automatic is False
    assert mgr.state == UNKNOWN


def test_manager_actuates_through_the_configured_backend():
    inst = FakeTSP()
    mgr = _digio_manager(inst)
    mgr.close()
    assert mgr.state == CLOSED
    mgr.open()
    assert mgr.state == OPEN
    assert [w for w in inst.raw_writes if "writebit" in w] == [
        "digio.writebit(1, 0)", "digio.writebit(1, 1)"]


def test_manager_retries_once_before_giving_up():
    inst = FakeTSP(fail_writes=1)          # one transient GPIB hiccup
    mgr = _digio_manager(inst)
    mgr.close()                            # succeeds on the retry
    assert mgr.state == CLOSED


def test_manager_propagates_a_second_consecutive_failure():
    inst = FakeTSP(fail_writes=99)
    mgr = _digio_manager(inst)
    with pytest.raises(ShutterError):
        mgr.close()


def test_close_quietly_never_raises_and_reports_failure():
    mgr = _digio_manager()                 # no instrument attached
    assert mgr.close_quietly() is False
    assert mgr.state == UNKNOWN

    mgr.attach_instrument(FakeTSP())
    assert mgr.close_quietly() is True
    assert mgr.state == CLOSED


def test_manager_notifies_the_gui_on_every_state_change():
    seen = []
    inst = FakeTSP()
    cfg = default_config()
    cfg["backend"] = "keithley_digio"
    cfg["options"]["keithley_digio"]["settle_ms"] = 0
    mgr = ShutterManager(cfg, on_state_change=lambda m: seen.append(m.state))
    mgr.attach_instrument(inst)
    mgr.close()
    mgr.open()
    assert seen[-2:] == [CLOSED, OPEN]


def test_manager_notifies_even_when_an_actuation_fails():
    seen = []
    mgr = _digio_manager(FakeTSP(fail_writes=99),
                         )
    mgr._on_state_change = lambda m: seen.append(m.state)
    with pytest.raises(ShutterError):
        mgr.close()
    assert seen == [UNKNOWN]


def test_a_failing_state_callback_cannot_break_an_actuation():
    def boom(_mgr):
        raise RuntimeError("the indicator blew up")

    cfg = default_config()
    cfg["backend"] = "keithley_digio"
    cfg["options"]["keithley_digio"]["settle_ms"] = 0
    mgr = ShutterManager(cfg, on_state_change=boom)
    mgr.attach_instrument(FakeTSP())
    mgr.close()
    assert mgr.state == CLOSED


def test_manager_attaches_the_instrument_to_a_backend_configured_later():
    inst = FakeTSP()
    mgr = ShutterManager(prompt=RecordingPrompt())     # manual first
    mgr.attach_instrument(inst)

    cfg = default_config()
    cfg["backend"] = "keithley_digio"
    cfg["options"]["keithley_digio"]["settle_ms"] = 0
    mgr.configure(cfg)                                 # switch backend

    assert mgr.ready is True
    mgr.close()
    assert mgr.state == CLOSED


def test_detaching_the_instrument_makes_the_backend_not_ready():
    mgr = _digio_manager(FakeTSP())
    assert mgr.ready is True
    mgr.detach_instrument()
    assert mgr.ready is False


def test_manager_config_round_trips_through_settings_json():
    cfg = default_config()
    cfg["backend"] = "keithley_digio"
    cfg["options"]["keithley_digio"] = {
        "line": 7, "active_high": False, "settle_ms": 120, "verify": False}
    mgr = ShutterManager(cfg)
    assert mgr.save_config() is True

    restored = ShutterManager.load_config()
    assert restored["backend"] == "keithley_digio"
    assert restored["options"]["keithley_digio"]["line"] == 7
    assert restored["options"]["keithley_digio"]["active_high"] is False


def test_manager_survives_a_settings_file_with_nonsense_in_it():
    import app.io.app_settings as app_settings
    app_settings.save_section("shutter", {
        "backend": "no_such_backend",
        "options": {"keithley_digio": {"line": 99, "settle_ms": "soon"}},
    })
    cfg = ShutterManager.load_config()
    assert cfg["backend"] == "manual"                       # bad id ignored
    assert cfg["options"]["keithley_digio"]["line"] == 1    # bad options reset


def test_options_are_remembered_per_backend_when_switching():
    cfg = default_config()
    cfg["options"]["keithley_digio"]["line"] = 9
    mgr = ShutterManager(cfg)                    # manual is active
    assert mgr.backend_id == "manual"
    # The Keithley options survive even though that backend is not in use.
    assert mgr.config["options"]["keithley_digio"]["line"] == 9


# ── Against the real TSPTransport ────────────────────────────────────────────

def test_digio_drives_a_shutter_through_the_real_tsp_transport():
    """Regression: neither the write nor the read may hit the SCPI table.

    A fake with a permissive .query() hides this. The real TSPTransport rejects
    everything outside its enumerated SCPI vocabulary — which is what happened
    on hardware: "No TSP translation for query 'print(digio.readbit(1))'".
    """
    from app.instrument.tsp_transport import TSPTransport

    class BareResource:
        """A pyvisa resource with no knowledge of shutters."""

        def __init__(self):
            self.levels = {n: 1 for n in range(1, 15)}
            self.writes = []

        def write(self, cmd):
            self.writes.append(cmd)
            m = re.search(r"digio\.writebit\(\s*(\d+)\s*,\s*(\d+)\s*\)", cmd)
            if m:
                self.levels[int(m.group(1))] = int(m.group(2))

        def query(self, cmd):
            m = re.search(r"digio\.readbit\(\s*(\d+)\s*\)", cmd)
            if m:
                return f"{self.levels[int(m.group(1))]:.8e}\n"
            raise AssertionError(f"unexpected query {cmd!r}")

    res = BareResource()
    transport = TSPTransport(res, channel="A")
    s = KeithleyDigioShutter(line=1, active_high=True, settle_ms=0, verify=True)
    s.attach_instrument(transport)

    s.close()
    assert res.levels[1] == 0
    assert s.state == CLOSED

    s.open()
    assert res.levels[1] == 1
    assert s.state == OPEN


def test_the_transport_still_rejects_those_commands_on_the_translated_path():
    """The escape hatch must not have loosened write()/query()."""
    from app.instrument.tsp_transport import TSPTransport, UnsupportedSCPICommand

    class BareResource:
        def write(self, cmd):
            pass

        def query(self, cmd):
            return "0"

    t = TSPTransport(BareResource(), channel="A")
    with pytest.raises(UnsupportedSCPICommand):
        t.write("digio.writebit(1, 0)")
    with pytest.raises(UnsupportedSCPICommand):
        t.query("print(digio.readbit(1))")
