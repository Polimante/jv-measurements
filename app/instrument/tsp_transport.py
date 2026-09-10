"""
SCPI-to-TSP translating transport for Keithley 2600-series SourceMeters.

The whole measurement code base speaks SCPI (``:SOUR:VOLT``, ``:READ?`` …),
which is the native command language of the Keithley 2400.  The Keithley 2600
series (2601/2602/2611/2612/2634/2635/2636 and the 2650 family) instead speaks
**TSP** (a Lua dialect) and rejects colon-prefixed SCPI with

    -285  TSP Syntax error at line 1: unexpected symbol near ':'

``TSPTransport`` wraps an open pyvisa resource and presents the *exact same*
``.write(cmd)`` / ``.query(cmd)`` interface every call site already uses on
``self.instrument`` / ``self.inst``.  When the connected instrument is a
2600-series unit, it translates the finite set of SCPI commands the code emits
into TSP, and reshapes the ``:READ?`` reply back into the 2400's
``"volt,curr,res,time,status"`` comma format so downstream ``.split(",")[…]``
parsing is unaffected.

**A Keithley 2400 is never wrapped in this class** — it keeps talking to the raw
pyvisa resource, byte-for-byte identical to before.  This transport only ever
sits in front of a 2600-series instrument.  That is what keeps the tested,
safety-critical SCPI path unchanged (CLAUDE.md two-instrument split): the new
code activates *only* for TSP hardware.

Design notes
------------
* The SCPI vocabulary emitted by the code base is small and fully enumerable
  (see ``tests/test_tsp_transport.py``).  Any command not in the table raises
  :class:`UnsupportedSCPICommand` — we fail loudly rather than forward an
  untranslated colon-command that would reproduce the -285 error at a random
  point mid-run.
* ``:SENS:FUNC`` does not change what is measured: ``:READ?`` always measures
  *both* V and I via ``smuX.measure.iv()`` and formats them as
  ``"volt,curr,0,0,0"``.  Index 0 is voltage (OC mode reads ``[0]``) and index 1
  is current (J-V reads ``[1]``) for both configurations.  It is translated
  anyway, into ``display.smuX.measure.func``, so the instrument's own front
  panel shows the quantity the software is sensing — otherwise a 2600 keeps
  displaying whatever it was last left on, and an operator can watch it read
  volts through an entire current sweep.
* Dual-channel instruments (e.g. 2612) select SMU A or B via ``channel``.
"""

from __future__ import annotations

import re
import time


class UnsupportedSCPICommand(ValueError):
    """Raised when a SCPI command has no TSP translation in the table."""


# Matches "MODEL 2601", "MODEL 2612", "MODEL 2636B", "MODEL 2651A" … i.e. the
# 2600 and 2650 TSP families.  The 2400 family ("MODEL 2400"/"2410"/…) and the
# touchscreen 24xx SMUs (2450/2461/…) default to SCPI and are NOT matched.
_TSP_IDN_RE = re.compile(r"MODEL\s*26\d\d", re.IGNORECASE)


def is_tsp_instrument(idn: str) -> bool:
    """Return True if *idn* (an ``*IDN?`` response) is a TSP-native 2600/2650.

    A raw 2600-series instrument answers ``*IDN?`` even in TSP mode, so the
    connect flow can identify it before any colon-command is sent.
    """
    return bool(idn) and _TSP_IDN_RE.search(idn) is not None


class TSPTransport:
    """Wrap a pyvisa resource, translating SCPI writes/queries into TSP.

    Args:
        inst: An already-open pyvisa resource connected to a 2600-series SMU.
        channel: ``"A"`` or ``"B"`` — which SMU channel the cell is wired to.

    Every attribute other than ``write``/``query``/``close`` is proxied to the
    underlying resource, so ``.timeout``, ``.clear()`` etc. keep working.
    """

    def __init__(self, inst, channel: str = "A") -> None:
        self._inst = inst
        ch = str(channel).strip().lower()
        if ch not in ("a", "b"):
            raise ValueError(f"channel must be 'A' or 'B', got {channel!r}")
        self._ch = f"smu{ch}"
        # Per-point settling delay (seconds). On the 2400 this is delivered by
        # the firmware source delay before every :READ?. On the 2600 the
        # firmware delay attributes (source.delay / measure.delay) are NOT
        # honoured by the immediate measure.iv() this code uses — empirically
        # the scan time is identical for every scan-rate setting. So we deliver
        # the delay ourselves, as a host-side wait before each read (below).
        # Captured from :SOUR:DEL, cleared by *RST (matches the 2400, whose
        # *RST resets the source delay to 0).
        self._settle_s = 0.0

    # ── Public transport interface (mirrors pyvisa) ────────────────────────

    def write(self, cmd: str) -> None:
        tsp = self._translate_write(cmd)
        if tsp is not None:
            self._inst.write(tsp)

    def query(self, cmd: str) -> str:
        stripped = cmd.strip()
        upper = stripped.upper()
        if upper == "*IDN?":
            return self._inst.query(cmd)          # 2600 answers *IDN? natively
        if upper in (":READ?", "READ?"):
            if self._settle_s > 0.0:
                time.sleep(self._settle_s)        # per-point settling wait
            return self._inst.query(self._read_tsp())
        raise UnsupportedSCPICommand(f"No TSP translation for query {cmd!r}")

    def write_raw(self, tsp: str) -> None:
        """Send a TSP statement straight through, with no SCPI translation.

        The translation table above covers exactly the SCPI vocabulary the
        measurement code emits, and rejects anything else so an untranslated
        colon-command cannot produce a -285 mid-run. But some subsystems of the
        2600 have no SCPI equivalent to translate *from* — the digital I/O port
        (``digio.writebit``, used by ``app.shutter.keithley_digio``) is the
        first. Those callers speak TSP natively and use this door.

        Deliberately a separate method rather than a passthrough in ``write``:
        the guarantee that every ``write`` is a checked, enumerated translation
        is what keeps the safety-critical SCPI path trustworthy.
        """
        self._inst.write(tsp)

    def query_raw(self, tsp: str) -> str:
        """Query with raw TSP, bypassing the SCPI translation table.

        The read counterpart of :meth:`write_raw` — needed by the same callers,
        e.g. ``print(digio.readbit(N))`` when the shutter backend verifies that
        a line actually took the level it was given.
        """
        return self._inst.query(tsp)

    def close(self) -> None:
        self._inst.close()

    def __getattr__(self, name):
        # Proxy anything we don't override (timeout, clear, read_termination…)
        return getattr(self._inst, name)

    # ── Translation ────────────────────────────────────────────────────────

    def _read_tsp(self) -> str:
        """TSP that measures V and I once and prints them 2400-style.

        ``smuX.measure.iv()`` returns *current, then voltage* in a single
        reading; we print voltage first so ``[0]`` = V and ``[1]`` = I.
        """
        ch = self._ch
        return (
            f"tsp_i, tsp_v = {ch}.measure.iv() "
            f'print(string.format("%.8e,%.8e,0,0,0", tsp_v, tsp_i))'
        )

    def _translate_write(self, cmd: str) -> str | None:
        """Translate one SCPI write into TSP. ``None`` means a deliberate no-op."""
        ch = self._ch
        stripped = cmd.strip()
        # Split "HEAD arg" — head is case-insensitive, arg preserved verbatim.
        parts = stripped.split(None, 1)
        head = parts[0].upper()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if head == "*RST":
            self._settle_s = 0.0                  # 2400 *RST clears source delay
            return "\n".join([
                f"{ch}.reset()",
                f"{ch}.measure.autorangei = {ch}.AUTORANGE_ON",
                f"{ch}.measure.autorangev = {ch}.AUTORANGE_ON",
                f"{ch}.source.autorangev = {ch}.AUTORANGE_ON",
                f"{ch}.source.autorangei = {ch}.AUTORANGE_ON",
                # Disable firmware auto measure-delay so the host-side settling
                # wait (see query()) is the single, deterministic delay source.
                f"{ch}.measure.delay = 0",
            ])
        if head in ("*CLS",):
            return f"{ch}.nvbuffer1.clear()"

        if head == ":SOUR:FUNC":
            mode = "DCVOLTS" if arg.upper().startswith("VOLT") else "DCAMPS"
            return f"{ch}.source.func = {ch}.OUTPUT_{mode}"
        if head == ":SOUR:VOLT":
            return f"{ch}.source.levelv = {arg}"
        if head == ":SOUR:CURR":
            return f"{ch}.source.leveli = {arg}"
        if head == ":SOUR:DEL":
            # The 2600 firmware delay attributes are not honoured by the
            # immediate measure.iv() this code uses, so we capture the delay and
            # apply it host-side before each read (see query()). No instrument
            # write is emitted here.
            try:
                self._settle_s = max(0.0, float(arg))
            except ValueError:
                self._settle_s = 0.0
            return None

        if head == ":SENS:FUNC":
            # TSP measures whatever you ask for at read time, so this changes
            # nothing about the data. What it does change is the instrument's
            # own front panel: with no translation at all, a 2600 keeps showing
            # whatever it was last left on, so an operator watching the display
            # during a J-V sweep can see volts while the software is recording
            # amps. Point the display at the quantity the code is sensing.
            func = "DCAMPS" if "CURR" in arg.upper() else "DCVOLTS"
            return f"display.{ch}.measure.func = display.MEASURE_{func}"
        if head == ":SENS:CURR:PROT":
            return f"{ch}.source.limiti = {arg}"
        if head == ":SENS:VOLT:PROT":
            return f"{ch}.source.limitv = {arg}"
        if head in (":SENS:CURR:NPLC", ":SENS:VOLT:NPLC"):
            return f"{ch}.measure.nplc = {arg}"

        if head == ":SYST:RSEN":
            sense = "REMOTE" if arg.upper().startswith("ON") else "LOCAL"
            return f"{ch}.sense = {ch}.SENSE_{sense}"

        if head == ":OUTP":
            state = "ON" if arg.upper().startswith("ON") else "OFF"
            return f"{ch}.source.output = {ch}.OUTPUT_{state}"

        raise UnsupportedSCPICommand(f"No TSP translation for write {cmd!r}")
