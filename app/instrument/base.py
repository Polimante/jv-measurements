"""
Abstract SourceMeter interface.

Concrete implementations (Keithley2400, DummySMU, …) inherit from this
class and must implement every abstract method.  No pyvisa types are
exposed here — implementations hold hardware handles privately.

Sign convention (CLAUDE.md): photocurrent is *positive* in the
power-generating quadrant.  Implementations must negate the raw
instrument reading so that convention holds.

Units:
  voltages       V (volts)
  current density mA/cm²
  time           s (seconds), unless noted
"""

from __future__ import annotations

import abc
import threading
from typing import Callable

import numpy as np


class SourceMeter(abc.ABC):
    """Abstract base for a voltage-source / current-measure instrument."""

    # ── Configuration ──────────────────────────────────────────────────────

    @abc.abstractmethod
    def configure(self, nplc: float, delay_s: float, four_wire: bool) -> None:
        """Reset and configure the instrument for a J-V sweep.

        Sends *RST, sets voltage-source / current-sense mode, applies
        *nplc* integration time (power-line cycles) and *delay_s* source
        delay (s), enables remote-sense if *four_wire*, and turns output ON.
        The instrument is ready to source voltage and read current when this
        returns.
        """

    # ── Voltage / output control ───────────────────────────────────────────

    @abc.abstractmethod
    def set_voltage(self, v: float) -> None:
        """Set source voltage to *v* V immediately (no internal delay)."""

    @abc.abstractmethod
    def output_on(self) -> None:
        """Enable the instrument output."""

    @abc.abstractmethod
    def output_off(self) -> None:
        """Disable the instrument output (open-circuit terminals)."""

    # ── Measurement ────────────────────────────────────────────────────────

    @abc.abstractmethod
    def read_current_density(self, cell_area: float) -> float:
        """Trigger one reading and return current density in mA/cm².

        *cell_area* in cm².  The raw instrument current is negated so
        photocurrent is positive (CLAUDE.md sign convention).
        """

    # ── Compound protocols ─────────────────────────────────────────────────

    @abc.abstractmethod
    def apply_prebias(
        self,
        v_pb: float,
        t_pb: float,
        stop_event: threading.Event,
        status_callback: Callable[[str], None],
    ) -> None:
        """Hold source at *v_pb* V for *t_pb* s.

        Checks *stop_event* every 100 ms so the user can abort.
        *status_callback* receives progress strings ("Pre-bias: …").
        Returns immediately if *stop_event* is already set or fires during
        the hold; the voltage is left at v_pb in that case.
        """

    @abc.abstractmethod
    def apply_light_soaking(
        self,
        duration_s: float,
        stop_event: threading.Event,
        status_callback: Callable[[str], None],
    ) -> None:
        """Hold cell at open circuit under illumination for *duration_s* s.

        Turns output OFF (true open-circuit), counts down, then restores
        output ON so the next scan can begin immediately.  Checks
        *stop_event* each second.  If stopped before completion the output
        is left OFF.
        """

    @abc.abstractmethod
    def measure_scan(
        self,
        start: float,
        end: float,
        step: float,
        cell_area: float,
        stop_event: threading.Event,
    ) -> tuple[np.ndarray | None, np.ndarray | None]:
        """Step voltage from *start* to *end* by *step* (all in V).

        Reads current density (mA/cm²) at each point.  Returns
        ``(V_arr, J_arr)`` as NumPy float64 arrays, or ``(None, None)``
        if *stop_event* fires mid-scan.
        """
