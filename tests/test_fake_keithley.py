"""Tests for the production simulated instrument (app/simulation/fake_keithley.py).

FakeKeithley is injected into the live GUI by "Connect to Simulator" and also
drives the runner tests. These pin the behaviours the GUI relies on: a valid
*IDN?, an illuminated Voc that fits inside the app's default scan range, a dark
branch, and scan-direction inference so ``with_hysteresis`` yields a real HI.
"""

from __future__ import annotations

import threading

import numpy as np
import pytest

from app.instrument.keithley import measure_scan
from app.measurement.analysis import (
    calculate_jv_parameters,
    calculate_hysteresis_index,
)
from app.simulation.fake_keithley import FakeKeithley, SIMULATED_IDN

# App default illuminated scan range (main_window param grids).
_START, _END, _STEP, _AREA = -0.1, 1.05, 0.05, 0.07


def _fwd_rev(k: FakeKeithley):
    ev = threading.Event()
    vf, jf = measure_scan(k, _START, _END, _STEP, _AREA, ev)
    vr, jr = measure_scan(k, _END, _START, -_STEP, _AREA, ev)
    return vf, jf, vr, jr


def test_idn_query():
    assert FakeKeithley.from_mode("ideal_stable").query("*IDN?") == SIMULATED_IDN
    assert "SIMULATED" in SIMULATED_IDN


@pytest.mark.parametrize(
    "mode",
    ["ideal_stable", "with_hysteresis", "exponential_decay",
     "burn_in", "noisy", "catastrophic_failure"],
)
def test_illuminated_voc_within_default_scan_range(mode):
    """Every mode must produce a zero-crossing inside the default 1.05 V scan."""
    k = FakeKeithley.from_mode(mode)
    vf, jf, vr, jr = _fwd_rev(k)
    # Must not raise "no zero-crossing".
    Voc, Jsc, FF, PCE = calculate_jv_parameters(vr, jr, 100.0)
    assert 0.0 < Voc < _END
    assert PCE > 0


def test_with_hysteresis_yields_nonzero_hi():
    """Scan-direction inference must make fwd and rev branches differ."""
    k = FakeKeithley.from_mode("with_hysteresis")
    vf, jf, vr, jr = _fwd_rev(k)
    _, _, _, PCE_f = calculate_jv_parameters(vf, jf, 100.0)
    _, _, _, PCE_r = calculate_jv_parameters(vr, jr, 100.0)
    HI = calculate_hysteresis_index(PCE_f, PCE_r)
    # Normal hysteresis: reverse PCE exceeds forward → positive, sizeable HI.
    assert HI > 5.0


def test_ideal_stable_has_negligible_hysteresis():
    k = FakeKeithley.from_mode("ideal_stable")
    vf, jf, vr, jr = _fwd_rev(k)
    _, _, _, PCE_f = calculate_jv_parameters(vf, jf, 100.0)
    _, _, _, PCE_r = calculate_jv_parameters(vr, jr, 100.0)
    assert abs(calculate_hysteresis_index(PCE_f, PCE_r)) < 1.0


def test_dark_branch_is_near_zero_photocurrent():
    """set_light(False) collapses the photocurrent (shutter-closed dark scan)."""
    k = FakeKeithley.from_mode("ideal_stable")
    k.write(":SOUR:VOLT 0.0")
    j_light = -float(k.query(":READ?").split(",")[1])
    k.set_light(False)
    j_dark = -float(k.query(":READ?").split(",")[1])
    assert abs(j_dark) < abs(j_light) * 1e-3


def test_oc_mode_returns_voc_in_field_zero():
    k = FakeKeithley.from_mode("ideal_stable", voc=0.95)
    k.write(":SOUR:FUNC CURR")
    assert float(k.query(":READ?").split(",")[0]) == pytest.approx(0.95)
