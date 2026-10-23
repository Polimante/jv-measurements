"""
Tests for the OC (open-circuit) primitives in app/instrument/keithley.py.

``configure_oc_mode`` puts a Keithley 2400 into current-source / voltage-sense
mode with a 0 A setpoint so the measured voltage is the cell's Voc.
``read_voc_now`` parses that voltage from the :READ? response.
"""

from unittest.mock import MagicMock

import pytest

from app.instrument.keithley import configure_oc_mode, read_voc_now


def _oc_instrument(voc):
    """SCPI mock whose :READ? returns *voc* in the first field (OC mode)."""
    inst = MagicMock()

    def _query(cmd):
        if cmd == ":READ?":
            return f"{voc:.6f},0.000000,+0,+0,+0"
        return "0,0,0,0,0"

    inst.query.side_effect = _query
    return inst


def test_configure_oc_mode_sends_current_source_sequence():
    inst = MagicMock()
    configure_oc_mode(inst, nplc=2.0)
    calls = [c.args[0] for c in inst.write.call_args_list]
    assert "*RST" in calls
    assert ":SOUR:FUNC CURR" in calls
    assert ":SOUR:CURR 0" in calls
    assert ":SENS:FUNC 'VOLT'" in calls
    assert ":SENS:VOLT:PROT 2" in calls
    assert any("SENS:VOLT:NPLC 2.000" in c for c in calls)
    assert ":SYST:RSEN OFF" in calls
    assert ":OUTP ON" in calls


def test_configure_oc_mode_default_nplc():
    inst = MagicMock()
    configure_oc_mode(inst)
    calls = [c.args[0] for c in inst.write.call_args_list]
    assert any("SENS:VOLT:NPLC 1.000" in c for c in calls)


def test_read_voc_now_parses_first_field():
    inst = _oc_instrument(1.05)
    assert read_voc_now(inst) == pytest.approx(1.05)


def test_configure_then_read_round_trip():
    inst = _oc_instrument(0.92)
    configure_oc_mode(inst)
    voc = read_voc_now(inst)
    assert voc == pytest.approx(0.92)
