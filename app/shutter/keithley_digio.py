"""
Shutter driven by a digital I/O line of a Keithley 2600-series SourceMeter.

This is how the ABET Sun 2000 is wired in the UFABC lab: the simulator's
SHUTTER CONTROL switch sits in EXTERNAL, where its SHUTTER INPUT BNC is level
sensitive ("shutter will remain open as long as the signal is present"), and a
small interface box on the 2612's DB-25 drives that BNC from one digital I/O
line. Measured on 2026-09-09 with ``probe_shutter.py``: **line 1, active HIGH,
68 ms of blade travel**.

Two properties of the instrument shape this backend:

* Each line is open-drain with a 5.1 kOhm pull-up, so it **idles high**. With a
  non-inverting interface box that means the shutter idles OPEN — closing is an
  action the software must take, not merely something it refrains from undoing.
* "The reset function does not affect the present state of the digital I/O
  lines" (Series 2600A Reference Manual). The ``*RST`` that ``init_instrument``
  sends before every scan therefore leaves the shutter exactly where it was,
  which is what makes this integration safe.

The backend shares the SourceMeter's VISA session; serialising access is
:class:`~app.shutter.manager.ShutterManager`'s job.
"""

from __future__ import annotations

import time

from app.shutter.base import CLOSED, OPEN, Shutter, ShutterError

N_LINES = 14


class KeithleyDigioShutter(Shutter):
    """Drive a shutter from one of the 14 digital I/O lines of a 2600.

    Args:
        line: digital I/O line, 1-14 (DB-25 pins 1-14).
        active_high: True when a HIGH level opens the shutter. False for an
            inverting interface box, where the line must be pulled low to open.
        settle_ms: how long to wait after commanding, for the blade to travel.
        verify: read the line back after writing and fail if it did not take
            the commanded level — catches a write-protected line and a line
            being held by something else.
    """

    spec_id = "keithley_digio"

    def __init__(self, line: int = 1, active_high: bool = True,
                 settle_ms: float = 300.0, verify: bool = True) -> None:
        super().__init__()
        line = int(line)
        if not 1 <= line <= N_LINES:
            raise ValueError(f"digital I/O line must be 1..{N_LINES}, got {line}")
        self.line = line
        self.active_high = bool(active_high)
        self.settle_s = max(0.0, float(settle_ms)) / 1000.0
        self.verify = bool(verify)
        self._inst = None

    # ── Levels ───────────────────────────────────────────────────────────
    @property
    def open_level(self) -> int:
        return 1 if self.active_high else 0

    @property
    def close_level(self) -> int:
        return 0 if self.active_high else 1

    # ── Lifecycle ────────────────────────────────────────────────────────
    def attach_instrument(self, inst) -> None:
        self._inst = inst
        if inst is not None:
            # 2600A/B firmware honours writebit only in TRIG_BYPASS (its
            # default); the original 2600 has no digio.trigger[N].mode at all.
            # The nil check must happen in Lua: assigning a nil constant is not
            # a catchable error, it just logs -104 "Data type error" per call.
            #
            # Best-effort on purpose: a shutter problem must never stop the
            # SourceMeter from connecting. If the port really is unreachable,
            # the first open()/close() reports it properly.
            try:
                self._write(
                    "if digio.trigger ~= nil and digio.TRIG_BYPASS ~= nil then "
                    f"digio.trigger[{self.line}].mode = digio.TRIG_BYPASS end"
                )
            except ShutterError:
                pass

    def detach_instrument(self) -> None:
        # The state is deliberately left as-is: a digital I/O line holds its
        # level after the VISA session closes, so the last commanded state is
        # still the best thing we know about the hardware.
        self._inst = None

    @property
    def ready(self) -> bool:
        return self._inst is not None

    # ── Transport ────────────────────────────────────────────────────────
    def _write(self, tsp: str) -> None:
        """Send raw TSP, bypassing the SCPI translation table.

        ``TSPTransport`` exists to translate the application's SCPI vocabulary
        and deliberately has no digital I/O commands, so it exposes
        ``write_raw`` for exactly this. A bare pyvisa resource (no transport)
        takes the same string through ``write``.
        """
        inst = self._inst
        if inst is None:
            raise ShutterError(
                "No instrument is connected, so the digital I/O shutter cannot "
                "be commanded. Connect the SourceMeter first.")
        try:
            writer = getattr(inst, "write_raw", None) or inst.write
            writer(tsp)
        except Exception as exc:
            raise ShutterError(f"Writing to the digital I/O port failed: {exc}") from exc

    def _read_line(self) -> int:
        """Read the line back, again bypassing the SCPI translation table.

        ``TSPTransport.query`` only understands ``*IDN?`` and ``:READ?``, so
        this must go through ``query_raw`` for the same reason ``_write`` uses
        ``write_raw``.
        """
        inst = self._inst
        try:
            reader = getattr(inst, "query_raw", None) or inst.query
            reply = reader(f"print(digio.readbit({self.line}))")
            return int(round(float(str(reply).strip().split(",")[0])))
        except Exception as exc:
            raise ShutterError(
                f"Reading digital I/O line {self.line} back failed: {exc}") from exc

    # ── Actuation ────────────────────────────────────────────────────────
    def _drive(self, level: int, new_state: str) -> None:
        self._write(f"digio.writebit({self.line}, {level})")
        if self.settle_s:
            time.sleep(self.settle_s)
        if self.verify:
            actual = self._read_line()
            if actual != level:
                raise ShutterError(
                    f"Digital I/O line {self.line} was set to {level} but reads "
                    f"back as {actual}. The line may be write-protected "
                    f"(digio.writeprotect) or held by another device.")
        self._state = new_state

    def open(self) -> None:
        self._drive(self.open_level, OPEN)

    def close(self) -> None:
        self._drive(self.close_level, CLOSED)

    def describe(self) -> str:
        polarity = "active HIGH" if self.active_high else "active LOW"
        return (f"Keithley 2600 digital I/O — line {self.line}, {polarity}, "
                f"{self.settle_s * 1000:.0f} ms settle")
