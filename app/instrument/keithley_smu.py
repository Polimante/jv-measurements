"""Thin SourceMeter adapter that wraps a pyvisa Keithley 2400 resource.

Allows LightIntensityJVMeasurement (and future orchestrators) to accept a
SourceMeter interface while the existing GUI still holds a raw pyvisa resource.
"""

from __future__ import annotations

import threading
from typing import Callable

import numpy as np

from app.instrument.base import SourceMeter
from app.instrument.keithley import (
    init_instrument,
    apply_prebias,
    apply_light_soaking,
    measure_scan,
)


class Keithley2400SourceMeter(SourceMeter):
    """Adapts a raw pyvisa resource to the SourceMeter interface.

    Args:
        resource: An open pyvisa resource object connected to a Keithley 2400.
    """

    def __init__(self, resource) -> None:
        self._inst = resource

    def configure(self, nplc: float, delay_s: float, four_wire: bool) -> None:
        init_instrument(self._inst, nplc, delay_s, four_wire)

    def set_voltage(self, v: float) -> None:
        self._inst.write(f":SOUR:VOLT {v:.3f}")

    def output_on(self) -> None:
        self._inst.write(":OUTP ON")

    def output_off(self) -> None:
        try:
            self._inst.write(":OUTP OFF")
        except Exception:
            pass

    def read_current_density(self, cell_area: float) -> float:
        current = -float(self._inst.query(":READ?").split(",")[1])
        return (current * 1000.0) / cell_area

    def apply_prebias(
        self,
        v_pb: float,
        t_pb: float,
        stop_event: threading.Event,
        status_callback: Callable[[str], None],
    ) -> None:
        apply_prebias(self._inst, v_pb, t_pb, stop_event, status_callback)

    def apply_light_soaking(
        self,
        duration_s: float,
        stop_event: threading.Event,
        status_callback: Callable[[str], None],
    ) -> None:
        apply_light_soaking(self._inst, duration_s, stop_event, status_callback)

    def measure_scan(
        self,
        start: float,
        end: float,
        step: float,
        cell_area: float,
        stop_event: threading.Event,
    ) -> tuple[np.ndarray | None, np.ndarray | None]:
        return measure_scan(self._inst, start, end, step, cell_area, stop_event)
