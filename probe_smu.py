"""
Bench tool — find out what the SourceMeter is actually measuring.

Written for one specific confusion: a J-V run finishes with "No current
zero-crossing found", and the instrument's front panel appears to be showing
volts rather than amps. Those two symptoms together have a small number of
causes, and this script separates them in about thirty seconds.

It configures a channel exactly the way ``app.instrument.keithley.init_instrument``
does, steps through a few voltages, and prints the raw reading at each one --
both what the SMU returns and what the application would record after its sign
convention. Then it says which of these it looks like:

  * the cell is on the OTHER SMU channel     -> currents are ~nA on both scans,
                                                and the other channel is alive
  * nothing is connected at all              -> currents are ~nA on both channels
  * the cell is in the dark                  -> current has one sign throughout
                                                and grows steeply near the top
  * the sweep never reaches Voc              -> current is large and one sign,
                                                still climbing at the last point
  * everything is fine                       -> current crosses zero

Both channels are probed by default, because "the cell is wired to B while the
software is driving A" produces exactly the reported symptoms and is invisible
from the software side.

Usage
-----
    python probe_smu.py                        # pick instrument, probe A and B
    python probe_smu.py --address GPIB0::26::INSTR
    python probe_smu.py --channel a            # just one channel
    python probe_smu.py --max-voltage 0.8      # for a low-Voc cell
    python probe_smu.py --state                # report settings only, measure nothing

Run it with the lamp on and the shutter open, with the cell connected as it is
for a normal measurement.
"""

from __future__ import annotations

import argparse
import sys
import time

# Anything below this at 0 V means the terminals are effectively open.
_OPEN_CIRCUIT_A = 1e-9


def fmt_current(amps: float) -> str:
    a = abs(amps)
    if a >= 1e-3:
        return f"{amps * 1e3:+9.4f} mA"
    if a >= 1e-6:
        return f"{amps * 1e6:+9.3f} uA"
    if a >= 1e-9:
        return f"{amps * 1e9:+9.2f} nA"
    return f"{amps * 1e12:+9.1f} pA"


def parse_tsp_number(reply: str) -> float:
    text = (reply or "").strip()
    if not text:
        raise ValueError("empty reply from the instrument")
    return float(text.split(",")[0].strip())


class SMUProbe:
    """Raw TSP access to one channel of a 2600-series instrument."""

    def __init__(self, inst, channel: str, verbose: bool = False) -> None:
        self.inst = inst
        self.ch = f"smu{channel.strip().lower()}"
        self.verbose = verbose

    def _write(self, tsp: str) -> None:
        if self.verbose:
            print(f"    TSP > {tsp}")
        self.inst.write(tsp)

    def _query(self, tsp: str) -> str:
        if self.verbose:
            print(f"    TSP ? {tsp}")
        return self.inst.query(tsp)

    # ── State ────────────────────────────────────────────────────────────

    def report_state(self) -> dict:
        """Read back how this channel is configured, changing nothing."""
        ch = self.ch
        fields = {
            "source function": f"{ch}.source.func",
            "output":          f"{ch}.source.output",
            "source level V":  f"{ch}.source.levelv",
            "current limit":   f"{ch}.source.limiti",
            "sense (4-wire)":  f"{ch}.sense",
            "measure NPLC":    f"{ch}.measure.nplc",
            "display shows":   f"display.{ch}.measure.func",
        }
        state = {}
        for label, attr in fields.items():
            try:
                state[label] = parse_tsp_number(self._query(f"print({attr})"))
            except Exception as exc:
                state[label] = f"(unreadable: {type(exc).__name__})"
        return state

    # ── Measurement ──────────────────────────────────────────────────────

    def configure_like_the_app(self, nplc: float = 1.0) -> None:
        """The exact sequence init_instrument() produces for this channel."""
        ch = self.ch
        for tsp in (
            f"{ch}.reset()",
            f"{ch}.measure.autorangei = {ch}.AUTORANGE_ON",
            f"{ch}.measure.autorangev = {ch}.AUTORANGE_ON",
            f"{ch}.source.autorangev = {ch}.AUTORANGE_ON",
            f"{ch}.source.autorangei = {ch}.AUTORANGE_ON",
            f"{ch}.measure.delay = 0",
            f"{ch}.source.func = {ch}.OUTPUT_DCVOLTS",
            f"display.{ch}.measure.func = display.MEASURE_DCAMPS",
            f"{ch}.source.limiti = 0.1",
            f"{ch}.measure.nplc = {nplc:.3f}",
            f"{ch}.sense = {ch}.SENSE_LOCAL",
            f"{ch}.source.output = {ch}.OUTPUT_ON",
        ):
            self._write(tsp)

    def read_at(self, voltage: float) -> tuple[float, float]:
        """Set the source and return the measured (volts, amps)."""
        self._write(f"{self.ch}.source.levelv = {voltage:.4f}")
        time.sleep(0.05)
        reply = self._query(
            f"tsp_i, tsp_v = {self.ch}.measure.iv() "
            'print(string.format("%.8e,%.8e", tsp_v, tsp_i))')
        volts, amps = (float(x) for x in str(reply).strip().split(",")[:2])
        return volts, amps

    def output_off(self) -> None:
        try:
            self._write(f"{self.ch}.source.levelv = 0")
            self._write(f"{self.ch}.source.output = {self.ch}.OUTPUT_OFF")
        except Exception:
            pass


def sweep(probe: SMUProbe, max_voltage: float, points: int) -> list:
    """Step from 0 V to *max_voltage* and collect the readings."""
    rows = []
    for n in range(points):
        target = max_voltage * n / (points - 1)
        volts, amps = probe.read_at(target)
        # measure_scan() negates, so this is the sign the application records.
        rows.append((target, volts, amps, -amps))
    return rows


def verdict(rows: list, channel: str) -> list[str]:
    """Turn the readings into the diagnosis."""
    app_currents = [r[3] for r in rows]
    at_zero = abs(rows[0][2])
    biggest = max(abs(a) for a in app_currents)
    signs = {1 if a > 0 else (-1 if a < 0 else 0) for a in app_currents}
    crosses = len({s for s in signs if s}) > 1

    out = []
    if at_zero < _OPEN_CIRCUIT_A and biggest < _OPEN_CIRCUIT_A:
        out.append(f"  SMU {channel.upper()}: reads open-circuit at every point "
                   f"({fmt_current(at_zero)} at 0 V).")
        out.append("           Nothing is connected to this channel, or the "
                   "output is not reaching it.")
    elif crosses:
        out.append(f"  SMU {channel.upper()}: the current crosses zero — this is "
                   "a working illuminated J-V curve.")
        out.append("           A measurement on this channel should compute Voc "
                   "without complaint.")
    else:
        out.append(f"  SMU {channel.upper()}: current is measurable "
                   f"({fmt_current(rows[0][2])} at 0 V) but never crosses zero.")
        if at_zero < 1e-6:
            out.append("           Almost no current at 0 V: this looks like a "
                       "DARK curve. Is the shutter open,")
            out.append("           the lamp on, and the beam actually reaching "
                       "the cell?")
        else:
            out.append("           There is real photocurrent at 0 V, so the "
                       "cell is lit and connected —")
            out.append("           the sweep simply never reaches Voc. Raise "
                       "End Voltage above the last")
            out.append(f"           point tried here ({rows[-1][0]:.2f} V).")
    return out


def open_instrument(address: str | None):
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
    inst.timeout = 20000
    inst.write_termination = "\n"
    inst.read_termination = "\n"
    return inst


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Report what a Keithley 2600 is actually measuring, and "
                    "why a J-V curve might have no zero-crossing.")
    p.add_argument("--address", help="VISA resource (e.g. GPIB0::26::INSTR).")
    p.add_argument("--channel", default="both", choices=("a", "b", "both"),
                   help="Which SMU channel to probe (default: both).")
    p.add_argument("--max-voltage", type=float, default=1.0,
                   help="Highest voltage to step to (default 1.0 V).")
    p.add_argument("--points", type=int, default=6,
                   help="How many points from 0 V to --max-voltage (default 6).")
    p.add_argument("--nplc", type=float, default=1.0, help="Measurement NPLC.")
    p.add_argument("--state", action="store_true",
                   help="Only report the present settings; measure nothing.")
    p.add_argument("--verbose", action="store_true",
                   help="Echo every TSP command sent.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.points < 2:
        print("ERROR: --points must be at least 2")
        return 2

    inst = open_instrument(args.address)
    channels = ("a", "b") if args.channel == "both" else (args.channel,)
    probes = [SMUProbe(inst, ch, args.verbose) for ch in channels]

    try:
        idn = inst.query("*IDN?").strip()
        print(f"\nConnected: {idn}\n")

        print("Present settings, before anything is changed")
        print("=" * 72)
        for probe in probes:
            print(f"  {probe.ch}:")
            for label, value in probe.report_state().items():
                print(f"      {label:<16} {value}")
        print("  (source function 1 = DCVOLTS, output 1 = ON, "
              "display 0 = amps / 1 = volts)")

        if args.state:
            return 0

        print("\nStepping each channel the way a J-V scan does")
        print("=" * 72)
        diagnosis = []
        for probe, channel in zip(probes, channels):
            probe.configure_like_the_app(args.nplc)
            rows = sweep(probe, args.max_voltage, args.points)
            probe.output_off()

            print(f"\n  SMU {channel.upper()}")
            print(f"    {'set V':>7}  {'measured V':>11}  {'measured I':>13}  "
                  f"{'as recorded':>13}")
            for target, volts, amps, app_amps in rows:
                print(f"    {target:7.3f}  {volts:11.4f}  "
                      f"{fmt_current(amps):>13}  {fmt_current(app_amps):>13}")
            diagnosis += verdict(rows, channel)

        print("\nDiagnosis")
        print("=" * 72)
        for line in diagnosis:
            print(line)
        print()
        if len(channels) > 1:
            print("  If one channel reads open-circuit and the other does not, "
                  "the cell is on")
            print("  the second one — set 'SMU channel' in the Instrument tab "
                  "to match, then")
            print("  reconnect (the channel is read at connect time).\n")
        return 0

    except KeyboardInterrupt:
        print("\n\nInterrupted.")
        return 130
    finally:
        for probe in probes:
            probe.output_off()
        try:
            inst.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
