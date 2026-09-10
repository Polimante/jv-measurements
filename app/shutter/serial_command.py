"""
Shutter driven by text commands over a serial port.

This is the generic backend, and the reason the abstraction exists: rather than
shipping "an Arduino backend", it ships a *configurable text-command* backend.
Anyone with an Arduino writes a twenty-line sketch that answers ``OPEN`` and
``CLOSE`` and types those words into the Shutter Control window — no Python, no
recompiling, no pull request. The same backend already speaks to most commercial
controllers (Thorlabs SC10, Uniblitz VMM/VCM, USB relay boards), which are all
ASCII over serial with different words.

An example sketch is in ``docs/arduino_shutter.ino``.

Two design points worth knowing:

* ``pyserial`` is imported at *connection* time, not at construction. A stored
  configuration naming this backend must never stop the application from
  starting on a machine where pyserial is not installed.
* The port is opened once and kept open. Most Arduino boards reset when the port
  opens (DTR toggles the reset line) and then need a second or two of bootloader
  time, so opening per command would make every shutter move painfully slow —
  hence ``boot_delay_s``.

Because it needs no SourceMeter, this backend can be closed at application
startup, before any instrument is connected: ``requires_instrument=False``.
"""

from __future__ import annotations

import time

from app.shutter.base import CLOSED, OPEN, UNKNOWN, Shutter, ShutterError


class SerialCommandShutter(Shutter):
    """Send a configured line of text to open, and another to close.

    Args:
        port: serial port name (``COM3`` on Windows, ``/dev/ttyUSB0`` on Linux).
        baudrate: must match the controller's.
        open_command / close_command: the text to send, without the line ending.
        query_command: optional — text that asks the controller for its state.
        open_reply: the substring in the reply that means "open". Only used when
            *query_command* is set.
        line_ending: appended to every command.
        settle_ms: wait after commanding, for the blade to travel.
        timeout_s: read timeout for replies.
        boot_delay_s: pause after opening the port, for boards that reset on DTR.
    """

    spec_id = "serial_command"

    _ENDINGS = {"LF": "\n", "CRLF": "\r\n", "CR": "\r", "none": ""}

    def __init__(self, port: str = "COM3", baudrate: int = 9600,
                 open_command: str = "OPEN", close_command: str = "CLOSE",
                 query_command: str = "", open_reply: str = "OPEN",
                 line_ending: str = "LF", settle_ms: float = 300.0,
                 timeout_s: float = 2.0, boot_delay_s: float = 2.0) -> None:
        super().__init__()
        self.port = str(port).strip()
        self.baudrate = int(baudrate)
        self.open_command = str(open_command)
        self.close_command = str(close_command)
        self.query_command = str(query_command).strip()
        self.open_reply = str(open_reply).strip()
        self.line_ending = self._ENDINGS.get(str(line_ending), "\n")
        self.settle_s = max(0.0, float(settle_ms)) / 1000.0
        self.timeout_s = max(0.1, float(timeout_s))
        self.boot_delay_s = max(0.0, float(boot_delay_s))
        self._port = None

    # ── Connection ───────────────────────────────────────────────────────

    def _connect(self):
        """Open the port on first use and keep it open."""
        if self._port is not None:
            return self._port

        # Configuration is checked before the dependency, deliberately. Telling
        # someone who has not chosen a port yet to install pyserial sends them
        # off to fix the wrong thing — and which message they saw would depend
        # on whether pyserial happened to be installed.
        if not self.port:
            raise ShutterError("No serial port is configured for the shutter.")

        try:
            import serial                       # pyserial, imported on demand
        except ImportError as exc:
            raise ShutterError(
                "This shutter backend needs pyserial, which is not installed.\n"
                "Install it with:  pip install pyserial") from exc
        try:
            self._port = serial.Serial(self.port, self.baudrate,
                                       timeout=self.timeout_s)
        except Exception as exc:
            raise ShutterError(
                f"Could not open serial port {self.port!r}: {exc}") from exc
        if self.boot_delay_s:
            time.sleep(self.boot_delay_s)       # let a resetting board boot
        try:
            self._port.reset_input_buffer()
        except Exception:
            pass
        return self._port

    def detach_instrument(self) -> None:
        """Release the port. The blade keeps whatever position it is in."""
        port, self._port = self._port, None
        if port is not None:
            try:
                port.close()
            except Exception:
                pass

    @property
    def ready(self) -> bool:
        # A port name is all we need; the port itself opens lazily, and a
        # failure to open is reported by open()/close() with the reason.
        return bool(self.port)

    # ── Actuation ────────────────────────────────────────────────────────

    def _send(self, command: str) -> None:
        port = self._connect()
        try:
            port.write((command + self.line_ending).encode("ascii",
                                                           errors="replace"))
            port.flush()
        except Exception as exc:
            self.detach_instrument()     # force a reconnect on the next attempt
            raise ShutterError(
                f"Writing {command!r} to {self.port} failed: {exc}") from exc

    def _verify(self, expect_open: bool) -> None:
        """Ask the controller what it did, when it can answer."""
        if not self.query_command:
            return
        port = self._connect()
        try:
            port.reset_input_buffer()
            port.write((self.query_command + self.line_ending).encode(
                "ascii", errors="replace"))
            port.flush()
            reply = port.readline().decode("ascii", errors="replace").strip()
        except Exception as exc:
            self.detach_instrument()
            raise ShutterError(
                f"Reading the shutter state from {self.port} failed: {exc}"
            ) from exc

        if not reply:
            raise ShutterError(
                f"The shutter controller on {self.port} did not answer "
                f"{self.query_command!r} within {self.timeout_s:.1f} s.")
        says_open = self.open_reply.lower() in reply.lower()
        if says_open != expect_open:
            wanted = "open" if expect_open else "closed"
            raise ShutterError(
                f"The shutter was commanded {wanted}, but the controller "
                f"reports {reply!r}.")

    def _drive(self, command: str, expect_open: bool, new_state: str) -> None:
        self._send(command)
        if self.settle_s:
            time.sleep(self.settle_s)
        self._verify(expect_open)
        self._state = new_state

    def open(self) -> None:
        self._drive(self.open_command, True, OPEN)

    def close(self) -> None:
        self._drive(self.close_command, False, CLOSED)

    def describe(self) -> str:
        return (f"Serial — {self.port or '(no port)'} @ {self.baudrate} baud, "
                f"{self.open_command!r}/{self.close_command!r}, "
                f"{self.settle_s * 1000:.0f} ms settle")
