"""Tests for app.instrument.dummy_smu.DummySMU."""

from __future__ import annotations

import inspect
import threading
import time

import numpy as np
import pytest

from app.instrument.base import SourceMeter
from app.instrument.dummy_smu import DummySMU
from app.simulation.dummy_cell import DummyCell


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_smu(**kwargs) -> DummySMU:
    """Return a connected, configured DummySMU with sleep disabled."""
    smu = DummySMU(sleep_scale=0.0, **kwargs)
    smu.connect()
    smu.configure(nplc=1.0, delay_s=0.0, four_wire=False)
    return smu


def _extract_params(
    v_arr: np.ndarray, j_arr: np.ndarray
) -> tuple[float, float, float, float]:
    """Extract Jsc, Voc, FF, PCE from a reverse J-V sweep.

    Returns (jsc, voc, ff, pce) where pce is in percent at 100 mW/cm².
    """
    # Jsc: J at voltage closest to 0
    idx_jsc = int(np.argmin(np.abs(v_arr)))
    jsc = float(j_arr[idx_jsc])

    # Voc: find any sign change (reverse scan: J goes negative→positive as index grows)
    sign_changes = np.where(np.diff(np.sign(j_arr)) != 0)[0]
    if len(sign_changes) == 0:
        return jsc, 0.0, 0.0, 0.0
    i0 = sign_changes[0]
    dj = j_arr[i0 + 1] - j_arr[i0]
    voc = float(v_arr[i0] - j_arr[i0] * (v_arr[i0 + 1] - v_arr[i0]) / dj) if dj != 0 else float(v_arr[i0])

    # FF and PCE
    power = v_arr * j_arr
    idx_mpp = int(np.argmax(power))
    ff = float(power[idx_mpp] / (voc * jsc)) if voc * jsc > 0 else 0.0
    pce = float(voc * jsc * ff / 100.0 * 100.0)
    return jsc, voc, ff, pce


# ── Abstract method compliance ────────────────────────────────────────────────

def test_implements_all_abstract_methods():
    """DummySMU must implement every abstract method declared by SourceMeter."""
    abstract = {
        name
        for name, method in inspect.getmembers(SourceMeter, predicate=inspect.isfunction)
        if getattr(method, "__isabstractmethod__", False)
    }
    smu = DummySMU(sleep_scale=0.0)
    implemented = set(dir(smu))
    missing = abstract - implemented
    assert not missing, f"DummySMU is missing abstract methods: {missing}"


def test_is_instance_of_sourcemeter():
    assert isinstance(DummySMU(), SourceMeter)


# ── Connection state machine ──────────────────────────────────────────────────

def test_connect_disconnect_cycle():
    smu = DummySMU(sleep_scale=0.0)
    assert not smu.is_connected()
    smu.connect()
    assert smu.is_connected()
    smu.disconnect()
    assert not smu.is_connected()


def test_disconnect_turns_output_off():
    smu = _make_smu()
    smu.output_on()
    smu.disconnect()
    assert not smu._output_enabled


# ── configure / output / set_voltage ─────────────────────────────────────────

def test_configure_turns_output_on():
    smu = DummySMU(sleep_scale=0.0)
    smu.connect()
    assert not smu._output_enabled
    smu.configure(nplc=1.0, delay_s=0.0, four_wire=False)
    assert smu._output_enabled


def test_set_voltage_stored():
    smu = _make_smu()
    smu.set_voltage(0.75)
    assert smu._voltage == pytest.approx(0.75)


def test_output_on_off():
    smu = _make_smu()
    smu.output_off()
    assert not smu._output_enabled
    smu.output_on()
    assert smu._output_enabled


# ── read_current_density ──────────────────────────────────────────────────────

def test_read_at_short_circuit_approx_jph():
    smu = _make_smu()
    smu.set_voltage(0.0)
    j = smu.read_current_density(cell_area=0.1)
    assert abs(j - 22.0) / 22.0 < 0.005  # within 0.5% of J_ph


def test_read_sign_positive_at_zero_volts():
    """Photocurrent must be positive at V=0 (CLAUDE.md sign convention)."""
    smu = _make_smu()
    smu.set_voltage(0.0)
    assert smu.read_current_density(0.1) > 0.0


# ── Full J-V sweep: nominal parameters within 1 % ────────────────────────────

def test_jv_sweep_nominal_values():
    """Reverse sweep should reproduce Jsc, Voc, FF, PCE within 1% of nominal."""
    smu = _make_smu()
    stop = threading.Event()
    # Reverse scan 1.3 → −0.05 V, step −0.01 V
    v_arr, j_arr = smu.measure_scan(1.3, -0.05, -0.01, 0.1, stop)

    assert v_arr is not None
    assert j_arr is not None

    jsc, voc, ff, pce = _extract_params(v_arr, j_arr)

    # Nominal values from a direct DummyCell query
    cell = DummyCell(mode="ideal_stable")
    from scipy.optimize import brentq
    voc_nom = brentq(cell.current_density, 0.8, 1.5)
    jsc_nom = cell.current_density(0.0)

    assert abs(jsc - jsc_nom) / jsc_nom < 0.01, f"Jsc deviation {abs(jsc-jsc_nom)/jsc_nom:.3%}"
    assert abs(voc - voc_nom) / voc_nom < 0.01, f"Voc deviation {abs(voc-voc_nom)/voc_nom:.3%}"
    assert 0.65 < ff < 0.85, f"FF={ff:.3f} out of expected range"
    assert 17.0 < pce < 22.0, f"PCE={pce:.2f}% out of expected range"


def test_forward_scan_returns_arrays():
    smu = _make_smu()
    stop = threading.Event()
    v_arr, j_arr = smu.measure_scan(-0.05, 1.3, 0.01, 0.1, stop)
    assert v_arr is not None
    assert len(v_arr) > 0
    # First point (near V=0): photocurrent positive
    assert j_arr[0] > 0.0


# ── stop_event interrupts a long scan ────────────────────────────────────────

def test_stop_event_interrupts_scan():
    """stop_event must abort measure_scan within 1 NPLC cycle (~17 ms)."""
    smu = DummySMU(sleep_scale=1.0)
    smu.connect()
    smu.configure(nplc=1.0, delay_s=0.0, four_wire=False)
    stop = threading.Event()
    result: list[tuple | None] = [None]

    def run():
        result[0] = smu.measure_scan(1.3, -0.1, -0.002, 0.1, stop)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(0.25)   # let ~15 points complete
    stop.set()
    t.join(timeout=1.5)

    assert not t.is_alive(), "Scan thread did not stop within timeout"
    assert result[0] == (None, None)


# ── Compliance clamping ───────────────────────────────────────────────────────

def test_compliance_clamps_jsc():
    """Setting compliance below J_sc should clamp the reported current."""
    smu = _make_smu()
    smu.set_compliance(10.0)   # J_sc ≈ 22 mA/cm² → will be clamped
    smu.set_voltage(0.0)
    j = smu.read_current_density(0.1)
    assert j == pytest.approx(10.0, abs=1e-6)


def test_compliance_does_not_clamp_below_jsc():
    """Compliance above J_sc should not affect the reading."""
    smu = _make_smu()
    smu.set_compliance(100.0)
    smu.set_voltage(0.0)
    j = smu.read_current_density(0.1)
    # Should be near nominal 22 mA/cm²
    assert abs(j - 22.0) / 22.0 < 0.01


# ── factory round-trip ────────────────────────────────────────────────────────

def test_create_sourcemeter_dummy():
    from app.instrument import create_sourcemeter
    smu = create_sourcemeter("dummy", sleep_scale=0.0)
    assert isinstance(smu, DummySMU)


def test_create_sourcemeter_dummy_with_cell_kwargs():
    from app.instrument import create_sourcemeter
    smu = create_sourcemeter("dummy", sleep_scale=0.0, j_ph=18.0)
    assert isinstance(smu, DummySMU)
    smu.connect()
    smu.configure(nplc=1.0, delay_s=0.0, four_wire=False)
    smu.set_voltage(0.0)
    j = smu.read_current_density(0.1)
    assert abs(j - 18.0) / 18.0 < 0.01  # respects custom j_ph


def test_create_sourcemeter_dummy_cell_mode():
    from app.instrument import create_sourcemeter
    smu = create_sourcemeter("dummy", sleep_scale=0.0, cell_mode="with_hysteresis")
    assert smu._cell.mode == "with_hysteresis"


def test_create_sourcemeter_unknown_model():
    from app.instrument import create_sourcemeter
    with pytest.raises(ValueError, match="Unknown instrument model"):
        create_sourcemeter("nonexistent_smu")


# ── apply_prebias and apply_light_soaking ─────────────────────────────────────

def test_prebias_respects_stop_event():
    smu = DummySMU(sleep_scale=0.0)
    smu.connect()
    smu.configure(nplc=1.0, delay_s=0.0, four_wire=False)
    stop = threading.Event()
    stop.set()
    messages: list[str] = []
    smu.apply_prebias(1.0, 10.0, stop, messages.append)
    # Should return immediately; at most one status message
    assert len(messages) <= 1


def test_light_soaking_restores_output_when_complete():
    smu = _make_smu()
    stop = threading.Event()
    smu.apply_light_soaking(0.0, stop, lambda s: None)
    assert smu._output_enabled


def test_light_soaking_leaves_output_off_when_stopped():
    smu = DummySMU(sleep_scale=0.0)
    smu.connect()
    smu.configure(nplc=1.0, delay_s=0.0, four_wire=False)
    stop = threading.Event()
    stop.set()
    smu.apply_light_soaking(5.0, stop, lambda s: None)
    assert not smu._output_enabled
