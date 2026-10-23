"""Hardware-free tests for probe_shutter.py.

The probe's decision logic (which line, which polarity, is the resting state
fail-safe) is pure and therefore fully testable; the VISA layer is exercised
against a fake 2600 that models a shutter wired to one digital I/O line.
"""

import re

import pytest

from probe_shutter import (
    ALL_HIGH,
    DigioProbe,
    SMUCurrentSensor,
    bit_of,
    conclude,
    describe_port,
    format_conclusion,
    measure_settle,
    parse_line_spec,
    parse_port_reply,
    parse_tsp_number,
    scan_lines,
    with_bit,
)


# ── Fake instrument ──────────────────────────────────────────────────────────

class FakeKeithleyDigio:
    """A 2600 whose shutter is wired to one digital I/O line.

    ``open_level`` is the level on ``shutter_line`` that opens the shutter, so
    the fake can model both the inverting (active-low) and non-inverting
    interface boxes.
    """

    _WRITEBIT = re.compile(r"digio\.writebit\(\s*(\d+)\s*,\s*(\d+)\s*\)")
    _WRITEPORT = re.compile(r"digio\.writeport\(\s*(\d+)\s*\)")

    def __init__(self, shutter_line: int, open_level: int, port: int = ALL_HIGH,
                 dark: float = 1.2e-10, lit: float = 2.4e-3,
                 travel_reads: int = 0) -> None:
        self.shutter_line = shutter_line
        self.open_level = open_level
        self.port = port
        self.dark = dark
        self.lit = lit
        # Number of readings after an open command during which the blade is
        # still in the way — a stand-in for the shutter's mechanical travel.
        self.travel_reads = travel_reads
        self._reads_since_open = 0
        self.writes: list[str] = []
        self.output_on = False

    @property
    def shutter_open(self) -> bool:
        return bit_of(self.port, self.shutter_line) == self.open_level

    def _set_port(self, value: int) -> None:
        was_open = self.shutter_open
        self.port = value
        if self.shutter_open and not was_open:
            self._reads_since_open = 0

    def write(self, cmd: str) -> None:
        self.writes.append(cmd)
        m = self._WRITEBIT.search(cmd)
        if m:
            self._set_port(with_bit(self.port, int(m.group(1)), int(m.group(2))))
            return
        m = self._WRITEPORT.search(cmd)
        if m:
            self._set_port(int(m.group(1)))
            return
        if "OUTPUT_ON" in cmd:
            self.output_on = True
        elif "OUTPUT_OFF" in cmd:
            self.output_on = False

    def query(self, cmd: str) -> str:
        if "digio.readport" in cmd:
            return f"{self.port:.8e}\n"
        if "digio.writeprotect" in cmd:
            return "0.00000000e+00\n"
        if "errorqueue.count" in cmd:
            return "0.00000000e+00\n"
        if "measure.i()" in cmd:
            # The SMU only sees photocurrent when the output is on.
            lit = self.shutter_open and self.output_on
            if lit and self._reads_since_open < self.travel_reads:
                self._reads_since_open += 1
                lit = False
            return f"{(self.lit if lit else self.dark):.8e}\n"
        raise AssertionError(f"unexpected query: {cmd!r}")

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Make the probe's dwell times instant."""
    monkeypatch.setattr("probe_shutter.time.sleep", lambda *_: None)


# ── Reply parsing ────────────────────────────────────────────────────────────

def test_parse_tsp_number_handles_scientific_notation():
    assert parse_tsp_number("1.63830000e+04\n") == pytest.approx(16383.0)
    assert parse_tsp_number(" -2.5e-03 ") == pytest.approx(-0.0025)


def test_parse_tsp_number_takes_first_field():
    assert parse_tsp_number("3.0,junk,more") == pytest.approx(3.0)


def test_parse_tsp_number_rejects_empty_reply():
    with pytest.raises(ValueError):
        parse_tsp_number("   ")


def test_parse_port_reply_rounds_to_int():
    assert parse_port_reply("1.63830000e+04") == 16383
    assert parse_port_reply("0.00000000e+00") == 0


def test_parse_port_reply_rejects_out_of_range():
    with pytest.raises(ValueError):
        parse_port_reply("1.00000000e+05")


# ── Bit arithmetic ───────────────────────────────────────────────────────────

def test_bit_of_matches_manual_bit_weighting():
    # Series 2600A Table 8-1: line N has decimal weight 2**(N-1).
    for line in range(1, 15):
        assert bit_of(1 << (line - 1), line) == 1
        assert bit_of(ALL_HIGH ^ (1 << (line - 1)), line) == 0


def test_with_bit_only_touches_the_named_line():
    assert with_bit(ALL_HIGH, 3, 0) == ALL_HIGH - 4
    assert with_bit(0, 14, 1) == 8192
    assert with_bit(ALL_HIGH, 3, 1) == ALL_HIGH


@pytest.mark.parametrize("line", [0, 15, -1])
def test_bit_helpers_reject_out_of_range_lines(line):
    with pytest.raises(ValueError):
        bit_of(ALL_HIGH, line)
    with pytest.raises(ValueError):
        with_bit(ALL_HIGH, line, 0)


def test_with_bit_rejects_non_binary_level():
    with pytest.raises(ValueError):
        with_bit(ALL_HIGH, 3, 2)


# ── Line spec parsing ────────────────────────────────────────────────────────

def test_parse_line_spec_forms():
    assert parse_line_spec("1-14") == list(range(1, 15))
    assert parse_line_spec("3") == [3]
    assert parse_line_spec("1,4,7-9") == [1, 4, 7, 8, 9]
    assert parse_line_spec("5,5,5") == [5]          # deduplicated
    assert parse_line_spec(" 2 , 1 ") == [1, 2]     # sorted, whitespace tolerant


@pytest.mark.parametrize("spec", ["", "0-3", "12-20", "9-4", "abc"])
def test_parse_line_spec_rejects_bad_input(spec):
    with pytest.raises(ValueError):
        parse_line_spec(spec)


def test_describe_port_names_the_low_lines():
    assert "all lines high" in describe_port(ALL_HIGH)
    text = describe_port(with_bit(ALL_HIGH, 3, 0))
    assert "low: #3" in text
    assert "0b11111111111011" in text


# ── Polarity logic ───────────────────────────────────────────────────────────

def test_conclude_closed_at_rest_driven_low_is_active_low_and_fail_safe():
    v = conclude(baseline_open=False, driven_level=0)
    assert v == {"open_level": 0, "close_level": 1,
                 "active_low": True, "fail_safe": True}


def test_conclude_closed_at_rest_driven_high_is_active_high_and_fail_safe():
    v = conclude(baseline_open=False, driven_level=1)
    assert v == {"open_level": 1, "close_level": 0,
                 "active_low": False, "fail_safe": True}


def test_conclude_open_at_rest_driven_low_means_high_opens_and_is_not_fail_safe():
    # Driving low CLOSED it, so high is the level that opens it.
    v = conclude(baseline_open=True, driven_level=0)
    assert v == {"open_level": 1, "close_level": 0,
                 "active_low": False, "fail_safe": False}


def test_conclude_open_at_rest_driven_high_means_low_opens():
    v = conclude(baseline_open=True, driven_level=1)
    assert v == {"open_level": 0, "close_level": 1,
                 "active_low": True, "fail_safe": False}


def test_conclude_rejects_bad_level():
    with pytest.raises(ValueError):
        conclude(baseline_open=False, driven_level=2)


def test_format_conclusion_reports_usable_commands():
    text = format_conclusion(3, conclude(False, 0), settle_ms=180.0)
    assert "digio.writebit(3, 0)" in text     # open
    assert "digio.writebit(3, 1)" in text     # close
    assert "ACTIVE LOW" in text
    assert "fail-safe" in text
    assert "180 ms" in text
    assert "active_low = True" in text


def test_format_conclusion_flags_a_non_fail_safe_wiring():
    text = format_conclusion(7, conclude(True, 0))
    assert "NOT fail-safe" in text
    assert "ACTIVE HIGH" in text
    assert "measure with --auto" in text      # settle time unknown


# ── DigioProbe over the fake instrument ──────────────────────────────────────

def test_probe_emits_the_documented_tsp():
    inst = FakeKeithleyDigio(shutter_line=3, open_level=0)
    probe = DigioProbe(inst)
    probe.write_bit(3, 0)
    assert "digio.writebit(3, 0)" in inst.writes
    assert probe.read_port() == with_bit(ALL_HIGH, 3, 0)


def test_probe_bypass_is_nil_guarded_for_original_2600_firmware():
    # A 2612 on firmware 1.3.x has no digio.TRIG_BYPASS. Assigning nil there is
    # not a catchable Lua error — it logs -104 "Data type error" per call — so
    # the guard has to be a Lua nil check, not a pcall.
    inst = FakeKeithleyDigio(shutter_line=1, open_level=0)
    DigioProbe(inst).ensure_bypass(5)
    assert inst.writes == [
        "if digio.trigger ~= nil and digio.TRIG_BYPASS ~= nil then "
        "digio.trigger[5].mode = digio.TRIG_BYPASS end"
    ]
    assert "pcall" not in inst.writes[0]


def test_probe_write_port_round_trips():
    inst = FakeKeithleyDigio(shutter_line=1, open_level=0)
    probe = DigioProbe(inst)
    probe.write_port(12345)
    assert probe.read_port() == 12345


def test_probe_rejects_out_of_range_line():
    probe = DigioProbe(FakeKeithleyDigio(shutter_line=1, open_level=0))
    with pytest.raises(ValueError):
        probe.write_bit(15, 0)


# ── End-to-end scan (auto mode, no operator input) ───────────────────────────

def _auto_scan(inst, lines=None):
    """Run a full auto-mode scan against *inst* and return (hits, verdict)."""
    probe = DigioProbe(inst)
    sensor = SMUCurrentSensor(inst, "a")
    sensor.start()
    baseline = probe.read_port()
    baseline_current = sensor.read()
    baseline_open = abs(baseline_current) > 1e-6
    hits = scan_lines(probe, baseline, lines or list(range(1, 15)),
                      dwell=0.0, sensor=sensor, threshold=1e-6,
                      baseline_current=baseline_current)
    verdict = conclude(baseline_open, hits[0][1]) if hits else None
    return hits, verdict, probe.read_port(), baseline


def test_auto_scan_finds_an_active_low_shutter_and_restores_the_port():
    inst = FakeKeithleyDigio(shutter_line=6, open_level=0)
    hits, verdict, final_port, baseline = _auto_scan(inst)

    assert hits == [(6, 0)]
    assert verdict["active_low"] is True
    assert verdict["fail_safe"] is True
    assert final_port == baseline          # every line put back as found
    assert not inst.shutter_open           # shutter left closed


def test_auto_scan_finds_a_non_inverting_box_and_calls_out_the_hazard():
    # Straight-through wiring: the line idles high, so the shutter idles OPEN.
    inst = FakeKeithleyDigio(shutter_line=2, open_level=1)
    hits, verdict, final_port, baseline = _auto_scan(inst)

    assert hits == [(2, 0)]                # driving low is what changed things
    assert verdict["open_level"] == 1
    assert verdict["fail_safe"] is False
    assert final_port == baseline


def test_auto_scan_reports_nothing_when_no_line_is_wired():
    inst = FakeKeithleyDigio(shutter_line=6, open_level=0)
    hits, verdict, final_port, baseline = _auto_scan(inst, lines=[1, 2, 3])

    assert hits == []
    assert verdict is None
    assert final_port == baseline


def test_auto_scan_respects_a_restricted_line_range():
    inst = FakeKeithleyDigio(shutter_line=11, open_level=0)
    hits, _verdict, _final, _baseline = _auto_scan(inst, lines=[9, 10, 11, 12])
    assert hits == [(11, 0)]


def test_scan_leaves_no_line_driven_after_a_hit():
    inst = FakeKeithleyDigio(shutter_line=4, open_level=0)
    _auto_scan(inst)
    assert inst.port == ALL_HIGH


# ── Settling-time measurement ────────────────────────────────────────────────

def _settle(inst, timeout_s=0.5):
    probe = DigioProbe(inst)
    sensor = SMUCurrentSensor(inst, "a")
    sensor.start()
    verdict = conclude(baseline_open=False, driven_level=0)
    return measure_settle(probe, sensor, inst.shutter_line,
                          verdict["open_level"], verdict["close_level"],
                          timeout_s=timeout_s), probe, sensor


def test_measure_settle_times_a_blade_that_takes_a_few_readings():
    inst = FakeKeithleyDigio(shutter_line=5, open_level=0, travel_reads=4)
    settle_ms, _probe, _sensor = _settle(inst)

    assert settle_ms is not None
    assert settle_ms > 0.0
    assert not inst.shutter_open          # left closed afterwards


def test_measure_settle_pins_the_range_to_the_lit_current():
    # A range set from the dark current would over-range the moment light
    # arrives, so the calibration must happen with the shutter OPEN.
    inst = FakeKeithleyDigio(shutter_line=5, open_level=0, lit=2.4e-3)
    _settle(inst)

    ranges = [w for w in inst.writes if "rangei" in w and "autorangei" not in w]
    assert ranges, "the fast configuration never pinned a current range"
    value = float(ranges[-1].split("=")[1])
    assert value >= 2.4e-3               # covers the illuminated current


def test_measure_settle_gives_up_when_the_light_never_changes():
    # Lamp off, or no cell connected: dark == lit, so there is nothing to time.
    inst = FakeKeithleyDigio(shutter_line=5, open_level=0,
                             dark=1e-10, lit=1e-10)
    settle_ms, _probe, _sensor = _settle(inst)
    assert settle_ms is None
