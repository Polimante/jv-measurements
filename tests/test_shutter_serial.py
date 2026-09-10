"""Hardware-free tests for the serial / Arduino shutter backend.

A fake ``serial`` module is installed in ``sys.modules`` so the real pyserial
never has to be present — which is also the point of the last test here: a
stored configuration naming this backend must not stop the application from
starting on a machine without pyserial.
"""

import sys
import types

import pytest

from app.shutter import ShutterError, create_shutter, get_backend
from app.shutter.base import CLOSED, OPEN, UNKNOWN
from app.shutter.serial_command import SerialCommandShutter


class FakePort:
    """Stands in for ``serial.Serial``."""

    def __init__(self, port, baudrate, timeout=None, replies=None,
                 fail_write=False) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.written: list[bytes] = []
        self.closed = False
        self.fail_write = fail_write
        self._replies = list(replies or [])

    def write(self, data):
        if self.fail_write:
            raise OSError("device disconnected")
        self.written.append(data)

    def flush(self):
        pass

    def reset_input_buffer(self):
        pass

    def readline(self):
        return self._replies.pop(0) if self._replies else b""

    def close(self):
        self.closed = True

    @property
    def commands(self) -> list[str]:
        return [b.decode().rstrip("\r\n") for b in self.written]


@pytest.fixture
def fake_serial(monkeypatch):
    """Install a fake ``serial`` module and hand back the ports it creates."""
    created = []

    def make(port, baudrate, timeout=None):
        p = FakePort(port, baudrate, timeout,
                     replies=make.replies, fail_write=make.fail_write)
        created.append(p)
        return p

    make.replies = []
    make.fail_write = False

    module = types.ModuleType("serial")
    module.Serial = make
    monkeypatch.setitem(sys.modules, "serial", module)
    monkeypatch.setattr("app.shutter.serial_command.time.sleep", lambda *_: None)
    make.created = created
    return make


def _shutter(**kwargs):
    kwargs.setdefault("settle_ms", 0)
    kwargs.setdefault("boot_delay_s", 0)
    return SerialCommandShutter(**kwargs)


# ── Commands ─────────────────────────────────────────────────────────────────

def test_sends_the_configured_words(fake_serial):
    s = _shutter(port="COM7", open_command="OPEN", close_command="CLOSE")
    s.open()
    s.close()
    assert fake_serial.created[0].commands == ["OPEN", "CLOSE"]
    assert s.state == CLOSED


def test_a_controller_with_different_words_needs_no_code(fake_serial):
    # Thorlabs SC10 speaks 'ens'; the point of this backend is that only the
    # configuration changes.
    s = _shutter(port="COM1", open_command="ens", close_command="ens")
    s.open()
    assert fake_serial.created[0].commands == ["ens"]


@pytest.mark.parametrize("ending, suffix", [
    ("LF", b"\n"), ("CRLF", b"\r\n"), ("CR", b"\r"), ("none", b""),
])
def test_line_endings(fake_serial, ending, suffix):
    s = _shutter(port="COM3", line_ending=ending)
    s.open()
    assert fake_serial.created[0].written == [b"OPEN" + suffix]


def test_an_unknown_line_ending_falls_back_to_lf(fake_serial):
    s = _shutter(port="COM3", line_ending="nonsense")
    s.open()
    assert fake_serial.created[0].written == [b"OPEN\n"]


def test_port_is_opened_once_and_reused(fake_serial):
    s = _shutter(port="COM3")
    s.open()
    s.close()
    s.open()
    assert len(fake_serial.created) == 1     # a resetting board boots only once


def test_the_port_is_opened_with_the_configured_settings(fake_serial):
    _shutter(port="COM9", baudrate=115200, timeout_s=3.0).open()
    port = fake_serial.created[0]
    assert (port.port, port.baudrate, port.timeout) == ("COM9", 115200, 3.0)


# ── State query ──────────────────────────────────────────────────────────────

def test_a_matching_reply_confirms_the_state(fake_serial):
    fake_serial.replies = [b"OPEN\n"]
    s = _shutter(port="COM3", query_command="STATE", open_reply="OPEN")
    s.open()
    assert s.state == OPEN
    assert fake_serial.created[0].commands == ["OPEN", "STATE"]


def test_a_contradicting_reply_is_an_error(fake_serial):
    fake_serial.replies = [b"CLOSED\n"]
    s = _shutter(port="COM3", query_command="STATE", open_reply="OPEN")
    with pytest.raises(ShutterError, match="reports"):
        s.open()
    assert s.state == UNKNOWN


def test_a_silent_controller_is_an_error(fake_serial):
    fake_serial.replies = []                  # readline times out empty
    s = _shutter(port="COM3", query_command="STATE")
    with pytest.raises(ShutterError, match="did not answer"):
        s.open()


def test_without_a_query_command_the_command_is_trusted(fake_serial):
    s = _shutter(port="COM3", query_command="")
    s.open()
    assert s.state == OPEN
    assert fake_serial.created[0].commands == ["OPEN"]


# ── Failures ─────────────────────────────────────────────────────────────────

def test_a_write_failure_is_reported_and_drops_the_port(fake_serial):
    fake_serial.fail_write = True
    s = _shutter(port="COM3")
    with pytest.raises(ShutterError, match="Writing"):
        s.open()
    assert fake_serial.created[0].closed      # dropped, so the retry reconnects

    fake_serial.fail_write = False
    s.open()
    assert len(fake_serial.created) == 2
    assert s.state == OPEN


def test_a_port_that_will_not_open_is_reported_with_its_reason(monkeypatch):
    def boom(*_a, **_k):
        raise OSError("access denied")

    module = types.ModuleType("serial")
    module.Serial = boom
    monkeypatch.setitem(sys.modules, "serial", module)

    with pytest.raises(ShutterError, match="access denied"):
        _shutter(port="COM3").open()


def test_no_port_configured_is_not_ready():
    s = _shutter(port="")
    assert s.ready is False
    with pytest.raises(ShutterError, match="No serial port"):
        s.open()


def test_missing_pyserial_is_reported_only_when_used(monkeypatch):
    """Construction must never fail: it happens at application startup."""
    monkeypatch.setitem(sys.modules, "serial", None)
    s = _shutter(port="COM3")          # must not raise
    with pytest.raises(ShutterError, match="pip install pyserial"):
        s.open()


def test_detach_closes_the_port(fake_serial):
    s = _shutter(port="COM3")
    s.open()
    s.detach_instrument()
    assert fake_serial.created[0].closed
    assert s.state == OPEN             # the blade did not move, only the port


def test_describe_names_the_port_and_words():
    text = _shutter(port="COM5", baudrate=19200).describe()
    assert "COM5" in text and "19200" in text and "'OPEN'" in text


# ── Registry ─────────────────────────────────────────────────────────────────

def test_the_backend_is_registered_and_needs_no_instrument():
    spec = get_backend("serial_command")
    assert spec.id == "serial_command"
    assert spec.is_automatic is True
    # This is what lets the application close it at startup, before any
    # SourceMeter is connected.
    assert spec.requires_instrument is False


def test_the_registered_defaults_build_a_working_shutter(fake_serial):
    spec = get_backend("serial_command")
    s = create_shutter("serial_command", dict(spec.defaults(),
                                              settle_ms=0, boot_delay_s=0))
    s.open()
    assert fake_serial.created[0].commands == ["OPEN"]


def test_the_line_ending_field_offers_the_endings_the_backend_supports():
    field = next(f for f in get_backend("serial_command").fields
                 if f.name == "line_ending")
    assert set(field.choices) == set(SerialCommandShutter._ENDINGS)
