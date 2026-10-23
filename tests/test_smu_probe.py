"""Hardware-free tests for probe_smu.py — mostly the diagnosis logic.

The point of the script is that it names a cause instead of dumping numbers, so
what needs pinning is that each characteristic shape maps to the right cause.
"""

import re

import pytest

from probe_smu import SMUProbe, fmt_current, parse_tsp_number, sweep, verdict


class FakeSMU:
    """A 2600 channel with a configurable cell behind it."""

    def __init__(self, mode="lit", channel="a") -> None:
        self.mode = mode
        self.channel = channel
        self.level = 0.0
        self.output = False
        self.writes: list[str] = []

    # Single-diode cell, in the SMU's own sign convention: current flowing OUT
    # of the cell reads negative. J0 and nVt put Voc just under 1 V.
    _J0 = 1e-12
    _NVT = 0.0385
    _IPH = 22e-3

    def _diode(self, v: float) -> float:
        import math
        return self._J0 * (math.exp(v / self._NVT) - 1.0)

    def _current(self) -> float:
        if not self.output or self.mode == "disconnected":
            return 3e-13
        v = self.level
        if self.mode == "lit":
            return self._diode(v) - self._IPH        # crosses zero near 0.95 V
        if self.mode == "dark":
            return self._diode(v)
        if self.mode == "lit_high_voc":
            return -self._IPH                        # never turns over in range
        raise AssertionError(self.mode)

    def write(self, tsp: str) -> None:
        self.writes.append(tsp)
        m = re.search(r"source\.levelv = ([-\d.]+)", tsp)
        if m:
            self.level = float(m.group(1))
        if "OUTPUT_ON" in tsp:
            self.output = True
        elif "OUTPUT_OFF" in tsp:
            self.output = False

    def query(self, tsp: str) -> str:
        if "measure.iv()" in tsp:
            return f"{self.level:.8e},{self._current():.8e}"
        if tsp.startswith("print("):
            return "0.00000000e+00"
        raise AssertionError(f"unexpected query {tsp!r}")


def _rows(mode, max_v=1.0, points=6):
    inst = FakeSMU(mode)
    probe = SMUProbe(inst, "a")
    probe.configure_like_the_app()
    return sweep(probe, max_v, points), inst


# ── Formatting and parsing ───────────────────────────────────────────────────

@pytest.mark.parametrize("amps, expect", [
    (2.4e-3, "mA"), (5.0e-6, "uA"), (2.0e-9, "nA"), (3e-13, "pA"),
])
def test_fmt_current_picks_a_readable_unit(amps, expect):
    assert expect in fmt_current(amps)


def test_fmt_current_keeps_the_sign():
    assert fmt_current(-2.4e-3).strip().startswith("-")
    assert fmt_current(2.4e-3).strip().startswith("+")


def test_parse_tsp_number():
    assert parse_tsp_number("1.00000000e+00\n") == 1.0
    with pytest.raises(ValueError):
        parse_tsp_number("  ")


# ── The sweep itself ─────────────────────────────────────────────────────────

def test_sweep_steps_from_zero_to_the_maximum():
    rows, _ = _rows("lit", max_v=1.0, points=5)
    assert [round(r[0], 3) for r in rows] == [0.0, 0.25, 0.5, 0.75, 1.0]


def test_sweep_records_the_application_sign_convention():
    """measure_scan() negates, so a source-of-current cell is recorded positive."""
    rows, _ = _rows("lit")
    raw_at_zero, app_at_zero = rows[0][2], rows[0][3]
    assert raw_at_zero < 0 and app_at_zero > 0
    assert app_at_zero == -raw_at_zero


def test_the_probe_turns_the_output_on_and_then_off():
    inst = FakeSMU("lit")
    probe = SMUProbe(inst, "a")
    probe.configure_like_the_app()
    assert inst.output is True
    probe.output_off()
    assert inst.output is False
    assert inst.level == 0.0


def test_the_probe_configures_the_display_to_show_amps():
    inst = FakeSMU("lit")
    SMUProbe(inst, "a").configure_like_the_app()
    assert "display.smua.measure.func = display.MEASURE_DCAMPS" in inst.writes


def test_the_probe_follows_the_channel():
    inst = FakeSMU("lit", channel="b")
    SMUProbe(inst, "b").configure_like_the_app()
    assert all("smub" in w or "display.smub" in w
               for w in inst.writes if "smu" in w)


# ── The diagnosis ────────────────────────────────────────────────────────────

def test_a_working_lit_cell_is_reported_as_working():
    rows, _ = _rows("lit")
    text = " ".join(verdict(rows, "a"))
    assert "crosses zero" in text
    assert "without complaint" in text


def test_an_unconnected_channel_is_named_as_such():
    rows, _ = _rows("disconnected")
    text = " ".join(verdict(rows, "b"))
    assert "open-circuit" in text
    assert "Nothing is connected" in text


def test_a_dark_curve_is_distinguished_from_a_missing_cell():
    """Real current, one sign, nothing at 0 V — that is darkness, not a fault."""
    rows, _ = _rows("dark")
    text = " ".join(verdict(rows, "a"))
    assert "DARK curve" in text
    assert "shutter open" in text
    assert "Nothing is connected" not in text


def test_a_sweep_that_stops_below_voc_says_so():
    rows, _ = _rows("lit_high_voc")
    text = " ".join(verdict(rows, "a"))
    assert "never reaches Voc" in text
    assert "End Voltage" in text
    # It must not blame the shutter when there is obvious photocurrent.
    assert "DARK curve" not in text


def test_the_verdict_names_the_channel_it_is_talking_about():
    rows, _ = _rows("lit")
    assert "SMU B" in " ".join(verdict(rows, "b"))
