"""
Bench tool — find which Keithley 2600 digital I/O line drives the ABET Sun 2000
shutter, and with which polarity, without touching the instrument's front panel.

Background
----------
The Sun 2000 shutter has two remote-control behaviours, selected by the
SHUTTER CONTROL switch on the simulator (Abet M-SS manual, "Shutter"):

  * EXTERNAL — the SHUTTER INPUT BNC is *level* sensitive: "shutter will remain
    open as long as the signal is present" (TTL +5 V). This is the mode we want.
  * INTERNAL/TIMED — the same BNC is an *edge* trigger (switch closure or a low
    TTL state) that fires one exposure of the programmed timer duration.

On the Keithley side, each of the 14 digital I/O lines is an open-drain output
with a 5.1 kOhm pull-up to +5 V, so a line idles HIGH. It can only source about
960 uA but can sink several mA, which is why an interface box (opto-isolator or
relay fed from the port's own +5 V pin) usually sits between the DB-25 and the
simulator — and that box may or may not invert the logic. So the polarity has to
be measured, not assumed.

Two facts make this probe safe to run mid-session:

  * "The reset function does not affect the present state of the digital I/O
    lines" (Series 2600A Reference Manual, digio.writebit) — so probing does not
    disturb, and is not disturbed by, the SMU configuration.
  * Only lines 1-14 are touched. Pin 24 (the 2611/2612 INTERLOCK) is not a
    digital I/O line and is never written.

What it does
------------
Reads the current state of the port, then perturbs ONE line at a time (driving
it to the opposite of its resting level for a moment, then restoring it) and
asks whether the shutter changed state. The line that responds is the answer;
combining "was it open at rest?" with "which level did we drive?" gives the
polarity. The resting port value is always restored on exit, including on Ctrl+C.

Detection can be done two ways:

  * manual (default) — you watch the red SHUTTER LED on the simulator panel and
    answer y/n. Works with the lamp OFF, so nothing gets illuminated.
  * --auto — the SMU itself watches, by measuring the cell current at 0 V. Needs
    a cell wired up and the lamp ON, but needs no operator input and can also
    time the shutter's mechanical settling.

Usage
-----
    python probe_shutter.py                      # pick instrument, manual scan
    python probe_shutter.py --address GPIB0::26::INSTR
    python probe_shutter.py --read               # just show the port, change nothing
    python probe_shutter.py --poke 3 0           # hold line 3 low until you press Enter
    python probe_shutter.py --lines 1-8          # restrict the scan range
    python probe_shutter.py --auto --channel a   # SMU-based detection + settle time

Before running, set the simulator's SHUTTER CONTROL switch to EXTERNAL.
"""

from __future__ import annotations

import argparse
import sys
import time

N_LINES = 14
ALL_HIGH = (1 << N_LINES) - 1          # 16383 — every line released (high)


# ── Pure helpers (hardware-free; covered by tests/test_shutter_probe.py) ──────

def parse_tsp_number(reply: str) -> float:
    """Parse a single number out of a TSP ``print(...)`` reply."""
    text = (reply or "").strip()
    if not text:
        raise ValueError("empty reply from the instrument")
    return float(text.split(",")[0].strip())


def parse_port_reply(reply: str) -> int:
    """Parse ``print(digio.readport())`` into an int in 0..16383."""
    value = int(round(parse_tsp_number(reply)))
    if not 0 <= value <= ALL_HIGH:
        raise ValueError(f"port value {value} out of range 0..{ALL_HIGH}")
    return value


def _validate_line(line: int) -> None:
    if not 1 <= int(line) <= N_LINES:
        raise ValueError(f"digital I/O line must be 1..{N_LINES}, got {line!r}")


def bit_of(port: int, line: int) -> int:
    """Resting level (0 or 1) of *line* (1-14) within a port word."""
    _validate_line(line)
    return (port >> (line - 1)) & 1


def with_bit(port: int, line: int, level: int) -> int:
    """Return *port* with *line* forced to *level*."""
    _validate_line(line)
    if level not in (0, 1):
        raise ValueError(f"level must be 0 or 1, got {level!r}")
    mask = 1 << (line - 1)
    return (port | mask) if level else (port & ~mask)


def parse_line_spec(spec: str) -> list[int]:
    """Parse ``"1-8"`` / ``"3"`` / ``"1,4,7-9"`` into a sorted list of lines."""
    lines: set[int] = set()
    for chunk in str(spec).split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            lo_s, _, hi_s = chunk.partition("-")
            lo, hi = int(lo_s), int(hi_s)
            if lo > hi:
                raise ValueError(f"empty range {chunk!r}")
            for n in range(lo, hi + 1):
                _validate_line(n)
                lines.add(n)
        else:
            n = int(chunk)
            _validate_line(n)
            lines.add(n)
    if not lines:
        raise ValueError(f"no digital I/O lines selected by {spec!r}")
    return sorted(lines)


def describe_port(port: int) -> str:
    """Human-readable port summary, e.g. ``16383  0b11111111111111  (all high)``."""
    bits = format(port, f"0{N_LINES}b")
    low = [n for n in range(1, N_LINES + 1) if not bit_of(port, n)]
    state = "all lines high" if not low else "low: " + ", ".join(f"#{n}" for n in low)
    return f"{port:5d}  0b{bits}  ({state})"


def conclude(baseline_open: bool, driven_level: int) -> dict:
    """Work out the shutter polarity from one successful perturbation.

    Args:
        baseline_open: was the shutter open with the port in its resting state?
        driven_level: the level (0/1) we drove the responding line to, which is
            by construction the opposite of its resting level.

    Returns a dict with ``open_level``, ``close_level``, ``active_low`` and
    ``fail_safe`` (True when the resting state of the port leaves the shutter
    closed — i.e. a crashed or powered-down controller cannot cook the cell).
    """
    if driven_level not in (0, 1):
        raise ValueError(f"driven_level must be 0 or 1, got {driven_level!r}")
    # Driving the line changed the shutter state. If it was closed at rest, the
    # level we drove is the one that opens it; if it was open at rest, the level
    # we drove is the one that closes it.
    open_level = driven_level if not baseline_open else 1 - driven_level
    return {
        "open_level": open_level,
        "close_level": 1 - open_level,
        "active_low": open_level == 0,
        "fail_safe": not baseline_open,
    }


def format_conclusion(line: int, verdict: dict, settle_ms: float | None = None) -> str:
    """Render the final report, including the config values for the app."""
    open_l = verdict["open_level"]
    close_l = verdict["close_level"]
    settle_txt = (f"{int(round(settle_ms))}" if settle_ms is not None
                  else "?  (measure with --auto)")
    out = [
        "",
        "=" * 72,
        "  RESULT",
        "=" * 72,
        f"  Digital I/O line ......... #{line}  (DB-25 pin {line})",
        f"  Opens the shutter ........ digio.writebit({line}, {open_l})",
        f"  Closes the shutter ....... digio.writebit({line}, {close_l})",
        f"  Polarity ................. "
        f"{'ACTIVE LOW' if verdict['active_low'] else 'ACTIVE HIGH'}",
    ]
    if verdict["fail_safe"]:
        out.append("  Resting state ............ CLOSED  -> fail-safe: a power-down "
                   "or crash leaves the cell dark.")
    else:
        out.append("  Resting state ............ OPEN    -> NOT fail-safe: with the "
                   "Keithley idle the shutter")
        out.append("                                       stays open. Close it "
                   "explicitly before leaving the bench.")
    if settle_ms is not None:
        out.append(f"  Mechanical settling ...... {settle_ms:.0f} ms "
                   "(time to 95 % of the illuminated current)")
    out += [
        "",
        "  Configuration for the shutter controller:",
        f"      line       = {line}",
        f"      active_low = {verdict['active_low']}",
        f"      settle_ms  = {settle_txt}",
        "=" * 72,
        "",
    ]
    return "\n".join(out)


# ── Instrument layer ─────────────────────────────────────────────────────────

class DigioProbe:
    """Thin TSP wrapper around the Series 2600 digital I/O port.

    Speaks raw TSP straight to the pyvisa resource — deliberately NOT through
    ``app.instrument.tsp_transport.TSPTransport``, whose job is translating the
    application's SCPI vocabulary and which has no digital I/O commands.
    """

    def __init__(self, inst, verbose: bool = False) -> None:
        self.inst = inst
        self.verbose = verbose

    def _write(self, tsp: str) -> None:
        if self.verbose:
            print(f"    TSP > {tsp}")
        self.inst.write(tsp)

    def _query(self, tsp: str) -> str:
        if self.verbose:
            print(f"    TSP ? {tsp}")
        return self.inst.query(tsp)

    def read_port(self) -> int:
        return parse_port_reply(self._query("print(digio.readport())"))

    def write_port(self, value: int) -> None:
        self._write(f"digio.writeport({int(value)})")

    def write_bit(self, line: int, level: int) -> None:
        _validate_line(line)
        self._write(f"digio.writebit({int(line)}, {int(level)})")

    def write_protect_mask(self) -> int:
        return int(round(parse_tsp_number(self._query("print(digio.writeprotect)"))))

    def ensure_bypass(self, line: int) -> None:
        """Put a line in TRIG_BYPASS so writebit takes effect.

        Only 2600A/B firmware has ``digio.trigger[N].mode`` (and defaults it to
        TRIG_BYPASS anyway); the original 2600 — a 2612 on firmware 1.3.x, say —
        has no such attribute and honours writebit in every trigger mode.

        The nil check has to happen in Lua rather than in a ``pcall``: assigning
        a nil constant does not raise a catchable Lua error, it logs a -104
        "Data type error" into the instrument's error queue, one per call. With
        the guard, the assignment simply never executes on the older firmware.
        """
        _validate_line(line)
        self._write(
            "if digio.trigger ~= nil and digio.TRIG_BYPASS ~= nil then "
            f"digio.trigger[{int(line)}].mode = digio.TRIG_BYPASS end"
        )

    def clear_errors(self) -> None:
        self._write("errorqueue.clear()")

    def drain_errors(self) -> list[str]:
        """Pop and return everything in the instrument's error queue."""
        messages: list[str] = []
        try:
            count = int(round(parse_tsp_number(self._query("print(errorqueue.count)"))))
        except Exception:
            return messages
        for _ in range(min(count, 20)):
            try:
                messages.append(self._query("print(errorqueue.next())").strip())
            except Exception:
                break
        return messages


class SMUCurrentSensor:
    """Reads cell current at 0 V — an automatic 'is there light?' witness."""

    def __init__(self, inst, channel: str = "a", nplc: float = 1.0,
                 limit_i: float = 0.1) -> None:
        ch = str(channel).strip().lower()
        if ch not in ("a", "b"):
            raise ValueError(f"channel must be 'a' or 'b', got {channel!r}")
        self.inst = inst
        self.ch = f"smu{ch}"
        self.nplc = float(nplc)
        self.limit_i = float(limit_i)

    def start(self) -> None:
        ch = self.ch
        for cmd in (
            f"{ch}.reset()",
            f"{ch}.source.func = {ch}.OUTPUT_DCVOLTS",
            f"{ch}.source.levelv = 0",
            f"{ch}.source.limiti = {self.limit_i}",
            f"{ch}.measure.autorangei = {ch}.AUTORANGE_ON",
            f"{ch}.measure.nplc = {self.nplc}",
            f"{ch}.sense = {ch}.SENSE_LOCAL",
            f"{ch}.source.output = {ch}.OUTPUT_ON",
        ):
            self.inst.write(cmd)

    def read(self) -> float:
        return parse_tsp_number(self.inst.query(f"print({self.ch}.measure.i())"))

    def configure_fast(self, expected_i: float) -> None:
        """Trade accuracy for speed before timing the shutter's travel."""
        ch = self.ch
        rng = max(abs(expected_i) * 1.5, 1e-6)
        for cmd in (
            f"{ch}.measure.nplc = 0.01",
            f"{ch}.measure.autozero = {ch}.AUTOZERO_OFF",
            f"{ch}.measure.autorangei = {ch}.AUTORANGE_OFF",
            f"{ch}.measure.rangei = {rng:.6e}",
        ):
            self.inst.write(cmd)

    def stop(self) -> None:
        try:
            self.inst.write(f"{self.ch}.source.output = {self.ch}.OUTPUT_OFF")
        except Exception:
            pass


# ── Console helpers ──────────────────────────────────────────────────────────

def ask_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        try:
            answer = input(prompt + suffix).strip().lower()
        except EOFError:
            return default
        if not answer:
            return default
        if answer in ("y", "yes", "s", "sim"):
            return True
        if answer in ("n", "no", "nao"):
            return False
        print("  Please answer y or n.")


def fmt_current(amps: float) -> str:
    a = abs(amps)
    if a >= 1e-3:
        return f"{amps * 1e3:+.4f} mA"
    if a >= 1e-6:
        return f"{amps * 1e6:+.3f} uA"
    return f"{amps * 1e9:+.1f} nA"


# ── Connection ───────────────────────────────────────────────────────────────

def open_instrument(address: str | None):
    """Open a pyvisa resource, prompting for one when *address* is None."""
    try:
        import pyvisa
    except ImportError:
        print("ERROR: pyvisa is not installed. Run: pip install pyvisa")
        sys.exit(1)

    rm = pyvisa.ResourceManager()

    if address is None:
        resources = list(rm.list_resources())
        if not resources:
            print("ERROR: no VISA resources found on this computer.")
            sys.exit(1)
        print("\nVISA resources found:\n")
        entries = []
        for addr in resources:
            try:
                scout = rm.open_resource(addr)
                scout.timeout = 2500
                idn = scout.query("*IDN?").strip()
                scout.close()
            except Exception as exc:
                idn = f"(no *IDN? response: {type(exc).__name__})"
            entries.append((addr, idn))
            print(f"  [{len(entries)}] {addr:<28} {idn}")
        print()
        while True:
            try:
                choice = input(f"Select instrument [1-{len(entries)}]: ").strip()
            except EOFError:
                sys.exit(1)
            if choice.isdigit() and 1 <= int(choice) <= len(entries):
                address = entries[int(choice) - 1][0]
                break
            print("  Invalid selection.")

    inst = rm.open_resource(address)
    inst.timeout = 10000
    inst.write_termination = "\n"
    inst.read_termination = "\n"
    return inst, address


def verify_tsp(inst, force: bool) -> str:
    """Confirm we are talking to a TSP 2600; abort otherwise unless forced."""
    idn = inst.query("*IDN?").strip()
    print(f"\nConnected: {idn}")
    try:
        from app.instrument.tsp_transport import is_tsp_instrument
        is_tsp = is_tsp_instrument(idn)
    except Exception:
        is_tsp = "26" in idn
    if not is_tsp:
        print("\nWARNING: this does not look like a Keithley 2600-series (TSP) unit.")
        print("The digital I/O commands used here are TSP-only; a 2400 has a")
        print("different digital I/O port and different commands.")
        if not force:
            print("Aborting. Re-run with --force if you are sure.")
            sys.exit(1)
    return idn


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_read(probe: DigioProbe) -> None:
    """Read-only: report the port state and change nothing."""
    port = probe.read_port()
    print("\nDigital I/O port (read-only, nothing was written):")
    print("  " + describe_port(port))
    mask = probe.write_protect_mask()
    if mask:
        print(f"  write-protect mask: {mask} — those lines IGNORE writebit.")
    else:
        print("  write-protect mask: 0 (no line is protected)")
    print("\n  A line reading low is either driven low by this instrument or")
    print("  pulled low by whatever is connected to it.\n")


def cmd_poke(probe: DigioProbe, line: int, level: int, baseline: int) -> None:
    """Hold one line at one level until the operator presses Enter."""
    resting = bit_of(baseline, line)
    print(f"\nLine #{line}: resting level {resting} -> driving to {level}.")
    probe.ensure_bypass(line)
    probe.write_bit(line, level)
    print("Watch the simulator's red SHUTTER LED.")
    try:
        input("Press Enter to restore the resting level... ")
    except EOFError:
        pass
    probe.write_bit(line, resting)
    print(f"Line #{line} restored to {resting}.\n")


def scan_lines(probe: DigioProbe, baseline: int, lines: list[int], dwell: float,
               sensor: SMUCurrentSensor | None, threshold: float,
               baseline_current: float | None) -> list[tuple[int, int]]:
    """Perturb each line in turn; return the (line, driven_level) hits."""
    hits: list[tuple[int, int]] = []
    for line in lines:
        resting = bit_of(baseline, line)
        driven = 1 - resting
        probe.ensure_bypass(line)
        probe.write_bit(line, driven)
        time.sleep(dwell)

        if sensor is None:
            changed = ask_yes_no(
                f"  line #{line:2d}  driven {resting} -> {driven}   "
                "did the shutter change state?"
            )
            probe.write_bit(line, resting)
        else:
            current = sensor.read()
            probe.write_bit(line, resting)
            delta = abs(current - (baseline_current or 0.0))
            changed = delta > threshold
            flag = "  <-- CHANGED" if changed else ""
            print(f"  line #{line:2d}  driven {resting} -> {driven}   "
                  f"I = {fmt_current(current):>12}   "
                  f"delta = {fmt_current(delta):>12}{flag}")

        time.sleep(min(dwell, 0.5))          # let the shutter settle back
        if changed:
            hits.append((line, driven))
            if sensor is None and not ask_yes_no(
                    "  Keep scanning the remaining lines "
                    "(useful to map the other cable)?"):
                break
    return hits


def measure_settle(probe: DigioProbe, sensor: SMUCurrentSensor, line: int,
                   open_level: int, close_level: int,
                   timeout_s: float = 5.0) -> float | None:
    """Time the shutter's travel: from the 'open' write to 95 % of final current."""
    print("\nTiming the shutter's mechanical travel...")

    # Calibrate first: the measurement range has to be pinned to the *lit*
    # current, because an autorange triggered mid-transient would show up as
    # settling time that belongs to the SMU, not to the shutter.
    probe.write_bit(line, open_level)
    time.sleep(1.5)
    lit = sensor.read()
    probe.write_bit(line, close_level)
    time.sleep(1.5)
    sensor.configure_fast(max(abs(lit), 1e-6))
    dark = sensor.read()          # re-read in the fast configuration

    probe.write_bit(line, open_level)
    samples: list[tuple[float, float]] = []
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout_s and len(samples) < 400:
        samples.append((time.perf_counter() - t0, sensor.read()))
    probe.write_bit(line, close_level)

    if len(samples) < 5:
        print("  Not enough samples to time the shutter.")
        return None

    tail = [i for _, i in samples[-max(3, len(samples) // 5):]]
    final = sum(tail) / len(tail)
    swing = final - dark
    if abs(swing) < 1e-9:
        print("  The current did not change — is the lamp on and the cell connected?")
        return None

    target = dark + 0.95 * swing
    span = samples[-1][0]
    for t, i in samples:
        if (swing > 0 and i >= target) or (swing < 0 and i <= target):
            print(f"  dark {fmt_current(dark)}  ->  lit {fmt_current(final)}   "
                  f"reached 95 % after {t * 1e3:.0f} ms")
            print(f"  ({len(samples)} samples over {span * 1e3:.0f} ms, "
                  f"~{span / len(samples) * 1e3:.1f} ms apart — that is the "
                  "resolution of this number)")
            return t * 1e3
    print("  Current never reached 95 % of its final value within the timeout.")
    return None


# ── Main ─────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Identify the Keithley 2600 digital I/O line that drives the "
                    "ABET Sun 2000 shutter, and its polarity.",
    )
    p.add_argument("--address", help="VISA resource (e.g. GPIB0::26::INSTR). "
                                     "Omit to pick from a list.")
    p.add_argument("--read", action="store_true",
                   help="Only read and print the port state; write nothing.")
    p.add_argument("--poke", nargs=2, metavar=("LINE", "LEVEL"), type=int,
                   help="Hold one line (1-14) at one level (0/1) until Enter.")
    p.add_argument("--lines", default="1-14",
                   help="Lines to scan, e.g. '1-8' or '3,5,9-11'. Default 1-14.")
    p.add_argument("--dwell", type=float, default=1.2,
                   help="Seconds to hold each line before judging (default 1.2).")
    p.add_argument("--auto", action="store_true",
                   help="Detect via the SMU's own current reading instead of "
                        "asking. Needs a cell connected and the lamp ON.")
    p.add_argument("--channel", default="a", choices=("a", "b", "A", "B"),
                   help="SMU channel the cell is wired to, for --auto (default a).")
    p.add_argument("--threshold", type=float, default=1e-6,
                   help="Current change counting as 'the light changed', in amps "
                        "(default 1e-6).")
    p.add_argument("--nplc", type=float, default=1.0,
                   help="Measurement NPLC for --auto (default 1.0).")
    p.add_argument("--no-settle", action="store_true",
                   help="Skip the settling-time measurement in --auto mode.")
    p.add_argument("--force", action="store_true",
                   help="Proceed even if the instrument is not a 2600-series.")
    p.add_argument("--verbose", action="store_true",
                   help="Echo every TSP command sent.")
    return p


def _print_briefing(args, lines: list[int]) -> None:
    print("Before starting, please confirm:")
    print("  1. The simulator's SHUTTER CONTROL switch is in the EXTERNAL")
    print("     position (in INTERNAL/TIMED the same BNC is only an edge")
    print("     trigger, and the shutter would close again on its own).")
    if args.auto:
        print("  2. The lamp is ON and warmed up, and a cell is connected to the")
        print(f"     SMU channel {args.channel.upper()} terminals.")
        print(f"  3. You accept the cell being illuminated for roughly "
              f"{len(lines) * args.dwell:.0f} s during the scan.")
    else:
        print("  2. The lamp is OFF (or the beam is blocked by a card), so")
        print("     nothing gets illuminated while we hunt for the line.")
        print("  3. You can see the red SHUTTER LED on the simulator panel.")
    print(f"  4. Lines {lines[0]}-{lines[-1]} will each be toggled once. If other")
    print("     equipment shares this port, it will be toggled too — use --lines")
    print("     to narrow the range if that is a problem.")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        lines = parse_line_spec(args.lines)
    except ValueError as exc:
        print(f"ERROR: --lines: {exc}")
        return 2
    if args.poke:
        try:
            _validate_line(args.poke[0])
            if args.poke[1] not in (0, 1):
                raise ValueError("level must be 0 or 1")
        except ValueError as exc:
            print(f"ERROR: --poke: {exc}")
            return 2

    inst, _address = open_instrument(args.address)
    verify_tsp(inst, args.force)
    probe = DigioProbe(inst, verbose=args.verbose)
    sensor: SMUCurrentSensor | None = None
    baseline: int | None = None

    try:
        probe.clear_errors()

        if args.read:
            cmd_read(probe)
            return 0

        baseline = probe.read_port()
        mask = probe.write_protect_mask()
        print("\nResting state of the digital I/O port:")
        print("  " + describe_port(baseline))
        if mask:
            protected = [n for n in lines if (mask >> (n - 1)) & 1]
            if protected:
                print(f"  WARNING: write-protect mask {mask} covers line(s) "
                      f"{protected} — writebit on those will be ignored.")
        print("  This value will be restored when the script exits.\n")

        if args.poke:
            cmd_poke(probe, args.poke[0], args.poke[1], baseline)
            return 0

        _print_briefing(args, lines)
        if not ask_yes_no("\nReady to start the scan?", default=False):
            print("Aborted; nothing was written.")
            return 0

        # ── Baseline shutter state ───────────────────────────────────────
        baseline_current: float | None = None
        if args.auto:
            sensor = SMUCurrentSensor(inst, args.channel, args.nplc)
            sensor.start()
            time.sleep(0.5)
            baseline_current = sensor.read()
            baseline_open = abs(baseline_current) > args.threshold
            print(f"\nBaseline current at 0 V: {fmt_current(baseline_current)}"
                  f"  ->  shutter appears "
                  f"{'OPEN (illuminated)' if baseline_open else 'CLOSED (dark)'}")
            if baseline_open:
                print("  NOTE: with the port at rest the cell is already lit, so this")
                print("        wiring is not fail-safe. Reported again at the end.")
        else:
            baseline_open = ask_yes_no(
                "\nRight now, with nothing driven, is the shutter OPEN "
                "(red LED lit)?")

        print("\nScanning. One line at a time; each is restored immediately.\n")
        hits = scan_lines(probe, baseline, lines, args.dwell,
                          sensor, args.threshold, baseline_current)

        # ── Verdict ──────────────────────────────────────────────────────
        if not hits:
            print("\nNo line changed the shutter state.")
            print("Things to check, in order of likelihood:")
            print("  * SHUTTER CONTROL switch not in the EXTERNAL position.")
            print("  * The interface box needs the port's +5 V pin (22/23/25) or a")
            print("    ground pin (15-21) that this cable does not actually carry.")
            print("  * The shutter line sits outside the range you scanned.")
            print("  * The box is powered from something that is switched off.")
            if not args.auto:
                print("  * Try --auto with the lamp on: a cell reading is a far more")
                print("    reliable witness than an LED at the edge of your vision.")
            return 1

        if len(hits) > 1:
            print(f"\nMore than one line responded: "
                  f"{', '.join('#%d' % n for n, _ in hits)}.")
            print("Reporting the first. Re-run with --lines to test them one by one.")

        line, driven = hits[0]
        verdict = conclude(baseline_open, driven)

        settle_ms = None
        if args.auto and sensor is not None and not args.no_settle:
            settle_ms = measure_settle(probe, sensor, line,
                                       verdict["open_level"], verdict["close_level"])

        print(format_conclusion(line, verdict, settle_ms))

        errors = probe.drain_errors()
        if errors:
            print("Instrument error queue (non-empty — worth a look):")
            for message in errors:
                print(f"  {message}")
        return 0

    except KeyboardInterrupt:
        print("\n\nInterrupted — restoring the port.")
        return 130
    finally:
        if sensor is not None:
            sensor.stop()
        if baseline is not None:
            try:
                probe.write_port(baseline)
                restored = probe.read_port()
                status = "OK" if restored == baseline else f"MISMATCH (now {restored})"
                print(f"Digital I/O port restored to {baseline}: {status}")
            except Exception as exc:
                print(f"WARNING: could not restore the digital I/O port: {exc}")
        try:
            inst.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
