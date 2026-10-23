"""Unit tests for the SCPI-to-TSP translating transport (Keithley 2600 support).

Hardware-free: a tiny fake pyvisa resource records every write and returns a
canned query response. These tests pin the translation of the *entire* SCPI
vocabulary the code base emits to real hardware, so an un-mapped command is
caught in CI rather than as a -285 error mid-measurement.
"""

import pytest

from app.instrument.tsp_transport import (
    TSPTransport,
    UnsupportedSCPICommand,
    is_tsp_instrument,
)


class FakeResource:
    """Minimal stand-in for a pyvisa resource."""

    def __init__(self, query_reply="1.5e-01,-2.0e-03,0,0,0"):
        self.writes = []
        self.queries = []
        self.closed = False
        self.timeout = 2000
        self._reply = query_reply

    def write(self, cmd):
        self.writes.append(cmd)

    def query(self, cmd):
        self.queries.append(cmd)
        return self._reply

    def close(self):
        self.closed = True


# ── Instrument identification ──────────────────────────────────────────────

@pytest.mark.parametrize("idn, expected", [
    ("Keithley Instruments Inc., Model 2612, 1234567, 1.4.2", True),
    ("KEITHLEY INSTRUMENTS INC.,MODEL 2636B,4088990,3.2.1", True),
    ("Keithley Instruments, Model 2651A, 1, 1.0", True),
    ("Keithley Instruments Inc., Model 2602, x, y", True),
    ("KEITHLEY INSTRUMENTS INC.,MODEL 2400,1234,C34", False),
    ("KEITHLEY INSTRUMENTS,MODEL 2450,04, 1.7", False),
    ("", False),
    ("Some other vendor SMU", False),
])
def test_is_tsp_instrument(idn, expected):
    assert is_tsp_instrument(idn) is expected


# ── Full write-command translation table (channel A) ───────────────────────

@pytest.mark.parametrize("scpi, tsp", [
    (":SOUR:FUNC VOLT",        "smua.source.func = smua.OUTPUT_DCVOLTS"),
    (":SOUR:FUNC CURR",        "smua.source.func = smua.OUTPUT_DCAMPS"),
    (":SOUR:VOLT 0.500",       "smua.source.levelv = 0.500"),
    (":SOUR:VOLT -0.1234",     "smua.source.levelv = -0.1234"),
    (":SOUR:CURR 0",           "smua.source.leveli = 0"),
    (":SENS:CURR:PROT 0.1",    "smua.source.limiti = 0.1"),
    (":SENS:VOLT:PROT 2",      "smua.source.limitv = 2"),
    (":SENS:CURR:NPLC 1.000",  "smua.measure.nplc = 1.000"),
    (":SENS:VOLT:NPLC 1.000",  "smua.measure.nplc = 1.000"),
    (":SYST:RSEN ON",          "smua.sense = smua.SENSE_REMOTE"),
    (":SYST:RSEN OFF",         "smua.sense = smua.SENSE_LOCAL"),
    (":OUTP ON",               "smua.source.output = smua.OUTPUT_ON"),
    (":OUTP OFF",              "smua.source.output = smua.OUTPUT_OFF"),
])
def test_write_translation_channel_a(scpi, tsp):
    res = FakeResource()
    t = TSPTransport(res, channel="A")
    t.write(scpi)
    assert res.writes == [tsp]


def test_source_delay_emits_no_write_and_is_applied_host_side(monkeypatch):
    """Regression: the per-point delay must actually slow the scan down.

    The 2600 firmware delay attributes are ignored by the immediate
    measure.iv() this code uses, so :SOUR:DEL is delivered as a host-side wait
    before each read. Setting a delay must (a) emit no instrument write and
    (b) cause query(":READ?") to sleep for that duration.
    """
    slept = []
    monkeypatch.setattr("app.instrument.tsp_transport.time.sleep", slept.append)

    res = FakeResource()
    t = TSPTransport(res, channel="A")
    t.write(":SOUR:DEL 0.3330")
    assert res.writes == []            # no firmware write for the delay

    t.query(":READ?")
    assert slept == [pytest.approx(0.333)]


def test_read_does_not_sleep_when_no_delay_set(monkeypatch):
    slept = []
    monkeypatch.setattr("app.instrument.tsp_transport.time.sleep", slept.append)
    TSPTransport(FakeResource(), channel="A").query(":READ?")
    assert slept == []


def test_rst_clears_pending_settling_delay(monkeypatch):
    """*RST must clear the captured delay, matching the 2400 (whose *RST
    resets the source delay to 0). Otherwise a JV delay would leak into a
    later OC/stabilized read that sends no :SOUR:DEL."""
    slept = []
    monkeypatch.setattr("app.instrument.tsp_transport.time.sleep", slept.append)
    t = TSPTransport(FakeResource(), channel="A")
    t.write(":SOUR:DEL 0.5")
    t.write("*RST")
    t.query(":READ?")
    assert slept == []


def test_rst_resets_channel_and_enables_autorange():
    res = FakeResource()
    TSPTransport(res, channel="A").write("*RST")
    sent = res.writes[0]
    assert "smua.reset()" in sent
    assert "smua.measure.autorangei = smua.AUTORANGE_ON" in sent
    assert "smua.measure.autorangev = smua.AUTORANGE_ON" in sent
    assert "smua.measure.delay = 0" in sent


def test_sens_func_points_the_front_panel_at_what_is_being_sensed():
    """It does not change the data — it stops the display from lying."""
    res = FakeResource()
    t = TSPTransport(res, channel="A")
    t.write(":SENS:FUNC 'CURR:DC'")
    t.write(":SENS:FUNC 'VOLT'")
    assert res.writes == [
        "display.smua.measure.func = display.MEASURE_DCAMPS",
        "display.smua.measure.func = display.MEASURE_DCVOLTS",
    ]


def test_sens_func_follows_the_channel():
    res = FakeResource()
    TSPTransport(res, channel="B").write(":SENS:FUNC 'CURR:DC'")
    assert res.writes == ["display.smub.measure.func = display.MEASURE_DCAMPS"]


def test_channel_b_prefix():
    res = FakeResource()
    TSPTransport(res, channel="B").write(":SOUR:VOLT 0.5")
    assert res.writes == ["smub.source.levelv = 0.5"]


# ── :READ? round-trip ──────────────────────────────────────────────────────

def test_read_query_sends_iv_and_returns_2400_shape():
    res = FakeResource(query_reply="1.5e-01,-2.0e-03,0,0,0")
    t = TSPTransport(res, channel="A")
    reply = t.query(":READ?")

    # The command sent must be TSP that measures i,v and prints "volt,curr,…"
    assert len(res.queries) == 1
    sent = res.queries[0]
    assert "smua.measure.iv()" in sent
    assert "string.format" in sent

    # Downstream parsing must still work: [0] = voltage, [1] = current.
    fields = reply.split(",")
    assert float(fields[0]) == pytest.approx(0.15)
    assert float(fields[1]) == pytest.approx(-2.0e-3)


def test_read_query_channel_b_uses_smub():
    res = FakeResource()
    TSPTransport(res, channel="B").query(":READ?")
    assert "smub.measure.iv()" in res.queries[0]


def test_idn_query_passes_through_untranslated():
    res = FakeResource(query_reply="Keithley, Model 2612, x, y")
    t = TSPTransport(res, channel="A")
    assert t.query("*IDN?") == "Keithley, Model 2612, x, y"
    assert res.queries == ["*IDN?"]


# ── Failure modes ──────────────────────────────────────────────────────────

def test_unknown_write_raises():
    t = TSPTransport(FakeResource(), channel="A")
    with pytest.raises(UnsupportedSCPICommand):
        t.write(":FETC:BOGUS 1")


def test_unknown_query_raises():
    t = TSPTransport(FakeResource(), channel="A")
    with pytest.raises(UnsupportedSCPICommand):
        t.query(":MEAS:VOLT?")


def test_invalid_channel_raises():
    with pytest.raises(ValueError):
        TSPTransport(FakeResource(), channel="C")


# ── Proxying ───────────────────────────────────────────────────────────────

def test_close_proxies_to_resource():
    res = FakeResource()
    TSPTransport(res).close()
    assert res.closed is True


def test_attribute_access_proxies_to_resource():
    res = FakeResource()
    res.timeout = 9999
    t = TSPTransport(res)
    assert t.timeout == 9999


def test_full_jv_configure_sequence_translates_without_error():
    """The exact init_instrument() sequence must translate end-to-end."""
    res = FakeResource()
    t = TSPTransport(res, channel="A")
    for cmd in [
        "*RST",
        ":SOUR:FUNC VOLT",
        ":SENS:FUNC 'CURR:DC'",
        ":SENS:CURR:PROT 0.1",
        ":SENS:CURR:NPLC 1.000",
        ":SOUR:DEL 0.1000",
        ":SYST:RSEN OFF",
        ":OUTP ON",
    ]:
        t.write(cmd)          # must not raise
    # SOUR:DEL is the only no-op (the delay is applied host-side), so 7 of the
    # 8 commands produce an instrument write.
    assert len(res.writes) == 7
    assert "display.smua.measure.func = display.MEASURE_DCAMPS" in res.writes


# ── Raw TSP escape hatch (digital I/O, used by app.shutter) ─────────────────

def test_write_raw_passes_tsp_through_untranslated():
    res = FakeResource()
    t = TSPTransport(res, channel="A")
    t.write_raw("digio.writebit(1, 0)")
    assert res.writes == ["digio.writebit(1, 0)"]


def test_write_raw_accepts_what_write_would_reject():
    # The whole point: digital I/O has no SCPI equivalent to translate from.
    res = FakeResource()
    t = TSPTransport(res, channel="A")
    with pytest.raises(UnsupportedSCPICommand):
        t.write("digio.writebit(1, 0)")
    t.write_raw("digio.writebit(1, 0)")      # must not raise


def test_write_raw_does_not_weaken_the_translated_path():
    """Adding the escape hatch must not make write() start forwarding."""
    res = FakeResource()
    t = TSPTransport(res, channel="A")
    for cmd in (":FOO:BAR 1", "print(digio.readport())", "smua.reset()"):
        with pytest.raises(UnsupportedSCPICommand):
            t.write(cmd)
    assert res.writes == []


def test_query_raw_passes_tsp_through_untranslated():
    res = FakeResource(query_reply="1.00000000e+00")
    t = TSPTransport(res, channel="A")
    assert t.query_raw("print(digio.readbit(1))") == "1.00000000e+00"
    assert res.queries == ["print(digio.readbit(1))"]


def test_query_raw_accepts_what_query_would_reject():
    res = FakeResource()
    t = TSPTransport(res, channel="A")
    with pytest.raises(UnsupportedSCPICommand):
        t.query("print(digio.readbit(1))")
    t.query_raw("print(digio.readbit(1))")       # must not raise
