"""SCPI-shaped simulated Keithley 2400, backed by a :class:`DummyCell`.

This is the production-safe sibling of a live pyvisa resource. It implements just
enough of the SCPI surface (``.write`` / ``.query``) that the raw-SCPI measurement
code drives it without modification: :func:`app.instrument.keithley.measure_scan`,
``init_instrument``, ``apply_prebias``, ``apply_light_soaking``, ``read_voc_now``,
plus the trackers and :class:`ContinuousISOSRunner`. The GUI injects an instance
into ``JVMeasurementApp.instrument`` (see "Connect to Simulator…") so the whole
application can be exercised end-to-end with no hardware.

It deliberately does **not** implement the ``SourceMeter`` ABC: it lives on the
raw-SCPI side of the two-instrument split documented in CLAUDE.md. Converging the
two is explicitly out of scope — this fake is the SCPI-shaped path, used only for
simulation and tests, exactly mirroring how the real resource is consumed.

Reading model:
  * In current-source (OC) mode (``:SOUR:FUNC CURR``), ``:READ?`` returns ``Voc``
    in field 0 — what ``read_voc_now`` parses.
  * In voltage-source mode (``:SOUR:FUNC VOLT``), ``:READ?`` returns the cell
    current (A) in field 1, pre-negated and scaled so the keithley/runner sign
    convention (``-field1`` × 1000 / area) recovers the DummyCell's positive
    photocurrent in mA/cm².
"""

from __future__ import annotations

from app.simulation.dummy_cell import DummyCell

#: ``*IDN?`` response. The "(SIMULATED)" tag keeps it unmistakable in the GUI.
SIMULATED_IDN: str = "KEITHLEY INSTRUMENTS,MODEL 2400 (SIMULATED),0,SIM-1.0"

#: Dark-saturation current for the GUI simulator's cell. Chosen so the cell's
#: Voc (~0.95 V) sits comfortably below the app's default scan end voltage
#: (1.05 V) — otherwise the illuminated J-V curve never crosses zero and every
#: parameter-extraction step raises "no zero-crossing". DummyCell's own default
#: (1e-12 → Voc ~1.18 V) is kept for the runner tests, which scan past 1.18 V.
_SIM_J0: float = 4.5e-10

#: Per-mode DummyCell overrides for the GUI simulator. These exaggerate the
#: behaviour so it is actually visible in a short, real-time session:
#:  * with_hysteresis — a clear ~15 % HI between fwd/rev branches (default 5 %);
#:  * exponential_decay / catastrophic_failure — minute-scale timescales so the
#:    degradation shows during a few-minute MPP/ISOS run instead of over 50–100 h.
#: Time-based modes still look like ``ideal_stable`` in a single J-V scan (t≈0),
#: which is physically correct — they only diverge over repeated measurements.
_MODE_DEFAULTS: dict[str, dict[str, float]] = {
    "with_hysteresis":      {"hi_fraction": 0.15},
    "exponential_decay":    {"tau_h": 0.10},     # ~6 min characteristic decay
    "catastrophic_failure": {"t_fail_h": 0.05},  # shunt collapse after ~3 min
}


class FakeKeithley:
    """SCPI-style fake backed by a :class:`DummyCell`.

    Args:
        cell: The synthetic cell. Defaults to an ``ideal_stable`` DummyCell.
        area: Active cell area (cm²) — must match the runner/scan area so the
            inverse sign/scale transform round-trips.
        voc: Open-circuit voltage returned in OC mode (V).
        dark_factor: Illumination factor applied when the shutter is closed
            (:meth:`set_light` ``False``) — a tiny value yields a pure dark curve.
    """

    def __init__(self, cell: DummyCell | None = None, area: float = 0.07,
                 voc: float = 1.0, dark_factor: float = 1e-6):
        self.cell = cell if cell is not None else DummyCell(mode="ideal_stable")
        self.area = area
        self.voc  = voc
        self.v    = 0.0
        self.func = "VOLT"
        self.writes: list[str] = []
        self._dark_factor = float(dark_factor)
        self._illuminated = True
        # Scan direction inferred from successive voltage setpoints, so
        # with_hysteresis reads the forward branch on the rising sweep and the
        # reverse branch on the falling sweep (real instruments behave likewise).
        self._scan_direction = "reverse"

    # ── Construction helpers ─────────────────────────────────────────────────

    @classmethod
    def from_mode(cls, mode: str = "ideal_stable", *, area: float = 0.07,
                  voc: float = 0.95, dark_factor: float = 1e-6,
                  **cell_kwargs) -> "FakeKeithley":
        """Build a fake whose cell runs in *mode*.

        *mode* must be one of :data:`app.simulation.dummy_cell.VALID_MODES`
        (``ideal_stable``, ``with_hysteresis``, ``exponential_decay``,
        ``burn_in``, ``noisy``, ``catastrophic_failure``). Extra keyword
        arguments are forwarded to :class:`DummyCell` (e.g. ``tau_h``,
        ``t_fail_h``, ``area``); a GUI-friendly ``j0`` (Voc ~0.95 V) and the
        per-mode exaggerations in :data:`_MODE_DEFAULTS` are applied unless the
        caller overrides them.
        """
        cell_kwargs.setdefault("j0", _SIM_J0)
        for key, val in _MODE_DEFAULTS.get(mode, {}).items():
            cell_kwargs.setdefault(key, val)
        cell = DummyCell(mode=mode, area=area, **cell_kwargs)
        return cls(cell=cell, area=area, voc=voc, dark_factor=dark_factor)

    # ── Shutter / illumination ───────────────────────────────────────────────

    def set_light(self, on: bool) -> None:
        """Toggle illumination (shutter). Off → near-zero photocurrent.

        Wire this to dark/illuminated scan boundaries and to the runner's
        ``on_shutter`` callback so dark scans read a pure-diode dark curve and
        illuminated scans read the lit curve.
        """
        self._illuminated = bool(on)
        self.cell.set_illumination_factor(1.0 if on else self._dark_factor)

    # ── SCPI surface ─────────────────────────────────────────────────────────

    def write(self, cmd: str) -> None:
        self.writes.append(cmd)
        if cmd.startswith(":SOUR:FUNC"):
            self.func = "CURR" if "CURR" in cmd else "VOLT"
        elif cmd.startswith(":SOUR:VOLT"):
            try:
                new_v = float(cmd.split()[1])
            except (IndexError, ValueError):
                return
            if new_v > self.v:
                self._scan_direction = "forward"
            elif new_v < self.v:
                self._scan_direction = "reverse"
            self.v = new_v

    def query(self, cmd: str) -> str:
        if cmd == "*IDN?":
            return SIMULATED_IDN
        if cmd == ":READ?":
            if self.func == "CURR":          # OC mode → Voc in field 0
                return f"{self.voc:.6f},0,0,0,0"
            j = self.cell.current_density(self.v, scan_direction=self._scan_direction)
            raw_a = -(j * self.area / 1000.0)  # invert runner's transform
            return f"{self.v:.6f},{raw_a:.9e},0,0,0"
        return "0,0,0,0,0"

    def close(self) -> None:
        """No-op close so the GUI's ``_close_instrument`` path is uniform."""
        return None
