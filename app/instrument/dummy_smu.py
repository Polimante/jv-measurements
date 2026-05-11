"""Simulated Keithley / SourceMeter backed by a DummyCell.

Implements the full SourceMeter ABC with no pyvisa dependency.
Useful for hardware-free testing, CI, and synthetic-data workflows.

NPLC timing: each measurement sleeps for ``nplc * 16.7 ms * sleep_scale``.
Set ``sleep_scale=0.0`` in tests to make scans instantaneous.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

import numpy as np

from app.instrument.base import SourceMeter
from app.simulation.dummy_cell import DummyCell


class DummySMU(SourceMeter):
    """Simulated source-measure unit backed by a synthetic solar cell.

    Args:
        cell: A pre-constructed :class:`DummyCell`.  If *None*, one is
            created using any extra keyword arguments forwarded as cell
            constructor parameters (e.g. ``cell_mode``, ``j_ph``, ``rs``).
        sleep_scale: Multiplier applied to the NPLC sleep time.  Set to
            ``0.0`` in tests for instant scans; leave at ``1.0`` (default)
            for realistic timing behaviour.
        **cell_kwargs: Forwarded verbatim to :class:`DummyCell` when *cell*
            is *None*.  ``cell_mode`` maps to the ``mode`` parameter.
    """

    def __init__(
        self,
        cell: DummyCell | None = None,
        *,
        sleep_scale: float = 1.0,
        **cell_kwargs,
    ) -> None:
        # Allow the caller to pass cell_mode= instead of mode= for clarity.
        if "cell_mode" in cell_kwargs:
            cell_kwargs["mode"] = cell_kwargs.pop("cell_mode")

        self._cell: DummyCell = cell if cell is not None else DummyCell(**cell_kwargs)
        self._sleep_scale = float(sleep_scale)

        # Instrument state
        self._connected: bool = False
        self._output_enabled: bool = False
        self._voltage: float = 0.0
        self._nplc: float = 1.0
        self._delay_s: float = 0.0
        self._four_wire: bool = False
        self._compliance: float = 200.0  # mA/cm² — generous default

    # ── Illumination control ──────────────────────────────────────────────────

    def set_illumination_factor(self, factor: float) -> None:
        """Forward to the underlying DummyCell.  See DummyCell.set_illumination_factor."""
        self._cell.set_illumination_factor(factor)

    # ── Connection state machine ───────────────────────────────────────────────

    def connect(self) -> None:
        """Mark the instrument as connected (no-op for the dummy)."""
        self._connected = True

    def disconnect(self) -> None:
        """Mark the instrument as disconnected and turn output off."""
        self._output_enabled = False
        self._connected = False

    def is_connected(self) -> bool:
        """Return True if :meth:`connect` has been called."""
        return self._connected

    # ── Compliance ────────────────────────────────────────────────────────────

    def set_compliance(self, limit: float) -> None:
        """Set the current-density compliance limit (mA/cm²).

        Any reading whose absolute value exceeds *limit* is clamped to
        ``±limit``.  A value below J_sc simulates compliance-mode operation.

        Args:
            limit: Compliance ceiling in mA/cm².
        """
        self._compliance = float(limit)

    # ── SourceMeter interface ─────────────────────────────────────────────────

    def configure(self, nplc: float, delay_s: float, four_wire: bool) -> None:
        """Configure the dummy instrument and turn the output on.

        Args:
            nplc: Number of power-line cycles per measurement.
            delay_s: Source delay between setting voltage and reading (s).
            four_wire: Ignored for the dummy; stored for API parity.
        """
        self._nplc = float(nplc)
        self._delay_s = float(delay_s)
        self._four_wire = four_wire
        self._output_enabled = True

    def set_voltage(self, v: float) -> None:
        """Set the source voltage to *v* V.

        Args:
            v: Target voltage (V).
        """
        self._voltage = float(v)

    def output_on(self) -> None:
        """Enable the instrument output."""
        self._output_enabled = True

    def output_off(self) -> None:
        """Disable the instrument output."""
        self._output_enabled = False

    def read_current_density(self, cell_area: float) -> float:
        """Read current density at the current source voltage.

        Sleeps for ``nplc * 16.7 ms * sleep_scale`` to simulate integration
        time.  Returns a value clamped to the compliance limit.

        Args:
            cell_area: Cell area (cm²).  The dummy ignores area internally
                because DummyCell already works in mA/cm²; the parameter is
                accepted for interface parity.

        Returns:
            Current density (mA/cm²), positive in power-generating quadrant.
        """
        self._sleep(self._nplc * 16.7e-3)
        # Infer scan direction from the sign of the last voltage change would
        # require history; default to "reverse" for single-point calls.
        j = self._cell.current_density(self._voltage, scan_direction="reverse")
        return self._apply_compliance(j)

    def apply_prebias(
        self,
        v_pb: float,
        t_pb: float,
        stop_event: threading.Event,
        status_callback: Callable[[str], None],
    ) -> None:
        """Hold source at *v_pb* V for *t_pb* s, checking stop every 100 ms.

        Args:
            v_pb: Pre-bias voltage (V).
            t_pb: Hold duration (s).
            stop_event: Abort signal.
            status_callback: Called with progress strings.
        """
        status_callback(f"Pre-bias: {v_pb:.2f} V for {t_pb:.1f} s…")
        self.set_voltage(v_pb)
        elapsed = 0.0
        while elapsed < t_pb:
            if stop_event.is_set():
                return
            self._sleep(0.1)
            elapsed += 0.1

    def apply_light_soaking(
        self,
        duration_s: float,
        stop_event: threading.Event,
        status_callback: Callable[[str], None],
    ) -> None:
        """Simulate open-circuit light soaking for *duration_s* seconds.

        Turns output off, waits, then restores output on.

        Args:
            duration_s: Soak duration (s).
            stop_event: Abort signal.
            status_callback: Called with countdown strings.
        """
        self.output_off()
        for remaining in range(int(duration_s), 0, -1):
            if stop_event.is_set():
                return
            status_callback(f"Light soaking: {remaining} s remaining…")
            self._sleep(1.0)
        if not stop_event.is_set():
            self.output_on()

    def measure_scan(
        self,
        start: float,
        end: float,
        step: float,
        cell_area: float,
        stop_event: threading.Event,
    ) -> tuple[np.ndarray | None, np.ndarray | None]:
        """Step voltage from *start* to *end* by *step*, reading J at each point.

        Scan direction is inferred from start/end and used to select the
        correct hysteresis branch in :class:`DummyCell`.

        Args:
            start: First voltage (V).
            end: Last voltage (V) — inclusive to match keithley.py convention.
            step: Signed voltage step (V).  Negative for reverse scans.
            cell_area: Cell area (cm²).  Accepted for interface parity.
            stop_event: Returns ``(None, None)`` if set mid-scan.

        Returns:
            ``(V_arr, J_arr)`` as float64 arrays, or ``(None, None)`` if
            stopped.
        """
        scan_direction = "forward" if end > start else "reverse"
        voltages: list[float] = []
        currents: list[float] = []

        for v in np.arange(start, end + step, step):
            if stop_event.is_set():
                return None, None
            self.set_voltage(float(v))
            self._sleep(self._delay_s)
            self._sleep(self._nplc * 16.7e-3)
            j = self._cell.current_density(float(v), scan_direction=scan_direction)
            voltages.append(float(v))
            currents.append(self._apply_compliance(j))

        return np.array(voltages, dtype=np.float64), np.array(currents, dtype=np.float64)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _sleep(self, seconds: float) -> None:
        """Sleep for *seconds* scaled by sleep_scale."""
        duration = seconds * self._sleep_scale
        if duration > 0.0:
            time.sleep(duration)

    def _apply_compliance(self, j: float) -> float:
        """Clamp *j* to the compliance window [−compliance, +compliance]."""
        return float(np.clip(j, -self._compliance, self._compliance))
