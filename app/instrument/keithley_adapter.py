"""
Keithley 2400 adapter.

Wraps the stateless helpers in keithley.py behind the SourceMeter ABC.
The pyvisa resource handle is held privately; no pyvisa types leak out.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

import numpy as np

from app.instrument import keithley
from app.instrument.base import SourceMeter


class Keithley2400(SourceMeter):
    """SourceMeter backed by a Keithley 2400 via pyvisa."""

    def __init__(self, inst: Any) -> None:
        """*inst* is an already-open pyvisa Resource object."""
        self._inst = inst

    # ── Configuration ──────────────────────────────────────────────────────

    def configure(self, nplc: float, delay_s: float, four_wire: bool) -> None:
        keithley.init_instrument(self._inst, nplc, delay_s, four_wire)

    # ── Voltage / output control ───────────────────────────────────────────

    def set_voltage(self, v: float) -> None:
        self._inst.write(f":SOUR:VOLT {v:.4f}")

    def output_on(self) -> None:
        self._inst.write(":OUTP ON")

    def output_off(self) -> None:
        self._inst.write(":OUTP OFF")

    # ── Measurement ────────────────────────────────────────────────────────

    def read_current_density(self, cell_area: float) -> float:
        raw = self._inst.query(":READ?").split(",")
        return -(float(raw[1]) * 1000.0) / cell_area

    # ── Compound protocols ─────────────────────────────────────────────────

    def apply_prebias(
        self,
        v_pb: float,
        t_pb: float,
        stop_event: threading.Event,
        status_callback: Callable[[str], None],
    ) -> None:
        keithley.apply_prebias(self._inst, v_pb, t_pb, stop_event, status_callback)

    def apply_light_soaking(
        self,
        duration_s: float,
        stop_event: threading.Event,
        status_callback: Callable[[str], None],
    ) -> None:
        keithley.apply_light_soaking(self._inst, duration_s, stop_event, status_callback)

    def measure_scan(
        self,
        start: float,
        end: float,
        step: float,
        cell_area: float,
        stop_event: threading.Event,
    ) -> tuple[np.ndarray | None, np.ndarray | None]:
        return keithley.measure_scan(self._inst, start, end, step, cell_area, stop_event)
