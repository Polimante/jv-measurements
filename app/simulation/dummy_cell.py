"""Synthetic perovskite solar cell — single-diode model.

Units throughout:
  current density  mA/cm²
  voltage          V
  resistance       Ω·cm²
  temperature      K
  time             s  (unless noted)

Sign convention (CLAUDE.md): photocurrent is *positive* in the power-generating
quadrant.  At V = 0, J ≈ +J_ph.

Implicit diode equation (units above):

    J = J_ph - J0*(exp((V + J·Rs·1e-3) / (n·Vt)) - 1) - (V + J·Rs·1e-3)·1e3 / Rsh

The 1e-3 factor converts J [mA/cm²] × Rs [Ω·cm²] → V.
The 1e3 factor converts V / Rsh [V/(Ω·cm²)] → mA/cm².
Solved pointwise with scipy.optimize.brentq.
"""

from __future__ import annotations

import time
import warnings
from typing import Literal

import numpy as np
from scipy.optimize import brentq

# ── Physical constants ────────────────────────────────────────────────────────
_KB: float = 1.380649e-23   # J/K
_Q: float = 1.602176634e-19  # C

VALID_MODES: frozenset[str] = frozenset({
    "ideal_stable",
    "with_hysteresis",
    "exponential_decay",
    "burn_in",
    "noisy",
    "catastrophic_failure",
})


class DummyCell:
    """Synthetic perovskite solar cell using the single-diode model.

    All six degradation/behaviour modes share the same physics engine; only
    the effective parameters fed into the solver change.

    Args:
        mode: One of ``VALID_MODES``.  See module docstring for descriptions.
        j_ph: Photocurrent density (mA/cm²).
        j0: Dark saturation current density (mA/cm²).
        n: Ideality factor (dimensionless).
        rs: Series resistance (Ω·cm²).
        rsh: Shunt resistance (Ω·cm²).
        T: Cell temperature (K).
        area: Active cell area (cm²).  Does not affect current density values
            but is exposed so callers can compute total current if needed.
        hi_fraction: Fractional hysteresis for ``with_hysteresis`` mode.
            ``hi_fraction ≈ HI/100`` for small values (e.g. 0.05 → ~5 % HI,
            0.20 → ~18 % HI).  Both forward and reverse J_ph are scaled
            symmetrically around the nominal value.
        tau_h: Characteristic decay time (hours) for ``exponential_decay``.
        t_fail_h: Time to catastrophic shunt collapse (hours) for
            ``catastrophic_failure``.
        noise_std: Gaussian noise standard deviation (mA/cm²) added to every
            current reading in ``noisy`` mode.
        rng: NumPy random Generator for reproducible noise.  If *None* a
            default (unseeded) generator is created.
    """

    def __init__(
        self,
        mode: str = "ideal_stable",
        *,
        j_ph: float = 22.0,
        j0: float = 1e-12,
        n: float = 1.5,
        rs: float = 5.0,
        rsh: float = 1.0e4,
        T: float = 298.15,
        area: float = 0.1,
        hi_fraction: float = 0.05,
        tau_h: float = 100.0,
        t_fail_h: float = 50.0,
        noise_std: float = 0.05,
        rng: np.random.Generator | None = None,
    ) -> None:
        if mode not in VALID_MODES:
            raise ValueError(
                f"Unknown mode {mode!r}. Valid modes: {sorted(VALID_MODES)}"
            )
        self.mode = mode
        self._j_ph = float(j_ph)
        self._j0 = float(j0)
        self._n = float(n)
        self._rs = float(rs)
        self._rsh = float(rsh)
        self._T = float(T)
        self.area = float(area)
        self._hi_fraction = float(hi_fraction)
        self._tau_s = float(tau_h) * 3600.0
        self._t_fail_s = float(t_fail_h) * 3600.0
        self._noise_std = float(noise_std)
        self._rng: np.random.Generator = (
            rng if rng is not None else np.random.default_rng()
        )
        self._t_start: float = time.monotonic()
        self._illumination_factor: float = 1.0

    # ── Time control ──────────────────────────────────────────────────────────

    def set_illumination_factor(self, factor: float) -> None:
        """Scale photocurrent linearly with *factor*.

        factor=1.0 is the nominal J_ph given at construction time.
        factor=0.5 halves J_ph, simulating 0.5-sun illumination.
        Linear scaling only — no sub-linear corrections.
        Used for testing light-intensity-dependent measurements.

        Args:
            factor: Illumination scale (must be > 0).
        """
        if factor <= 0:
            raise ValueError(f"illumination_factor must be positive; got {factor!r}")
        self._illumination_factor = float(factor)

    def set_time_offset(self, seconds: float) -> None:
        """Set the simulated elapsed time to *seconds* as of right now.

        Calling ``set_time_offset(0)`` resets the clock to zero.
        Calling ``set_time_offset(3600)`` makes the cell behave as if it has
        been running for one hour.  Essential for deterministic tests.

        Args:
            seconds: Target elapsed time in seconds.
        """
        self._t_start = time.monotonic() - float(seconds)

    def _elapsed(self) -> float:
        """Return simulated elapsed time in seconds."""
        return time.monotonic() - self._t_start

    # ── Parameter resolution ──────────────────────────────────────────────────

    def _get_params(
        self, scan_direction: str
    ) -> tuple[float, float, float, float, float]:
        """Return (j_ph, j0, n, rs, rsh) adjusted for mode and elapsed time.

        Args:
            scan_direction: ``"forward"`` (V increasing) or ``"reverse"``
                (V decreasing, standard solar-cell convention).

        Returns:
            Tuple (j_ph, j0, n, rs, rsh) in module units.
        """
        t = self._elapsed()
        j_ph = self._j_ph * self._illumination_factor
        j0 = self._j0
        n = self._n
        rs = self._rs
        rsh = self._rsh

        if self.mode == "ideal_stable":
            pass

        elif self.mode == "with_hysteresis":
            # Symmetric scaling of J_ph around nominal value.
            # Reverse scan → higher J_ph (stabilised ionic configuration).
            # Forward scan → lower J_ph (ions not yet redistributed).
            delta = self._hi_fraction * 0.5
            if scan_direction == "forward":
                j_ph *= 1.0 - delta
            else:
                j_ph *= 1.0 + delta

        elif self.mode == "exponential_decay":
            j_ph *= np.exp(-t / self._tau_s)

        elif self.mode == "burn_in":
            # PCE rises from 80 % of nominal to 100 % with τ = 30 min.
            t_burn = 30.0 * 60.0  # s
            j_ph *= 1.0 - 0.2 * np.exp(-t / t_burn)

        elif self.mode == "noisy":
            pass  # noise is added in current_density() after the solve

        elif self.mode == "catastrophic_failure":
            if t >= self._t_fail_s:
                rsh /= 1000.0

        return j_ph, j0, n, rs, rsh

    # ── Core physics ──────────────────────────────────────────────────────────

    @staticmethod
    def _solve_j(
        v: float,
        j_ph: float,
        j0: float,
        n: float,
        rs: float,
        rsh: float,
        vt: float,
    ) -> float:
        """Solve the implicit single-diode equation for J at voltage V.

        Uses ``scipy.optimize.brentq`` with brackets that cover all physically
        reachable operating points (V from ~ −0.5 V to 2 V).

        Args:
            v: Applied voltage (V).
            j_ph: Photocurrent density (mA/cm²).
            j0: Dark saturation current density (mA/cm²).
            n: Ideality factor.
            rs: Series resistance (Ω·cm²).
            rsh: Shunt resistance (Ω·cm²).
            vt: Thermal voltage kBT/q (V).

        Returns:
            Current density J (mA/cm²).
        """
        def residual(j: float) -> float:
            vj = v + j * rs * 1e-3  # internal node voltage [V]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                diode = j0 * (np.exp(vj / (n * vt)) - 1.0)
            return j_ph - diode - vj * 1e3 / rsh - j

        # j_lo is safely below any physical solution for V up to ~50 V.
        # j_hi is slightly above the photocurrent (maximum possible J).
        j_lo = -1.0e4
        j_hi = j_ph * 1.1 + 1.0

        return brentq(residual, j_lo, j_hi, xtol=1e-9, rtol=1e-9)

    # ── Public API ────────────────────────────────────────────────────────────

    def current_density(
        self, v: float, scan_direction: str = "reverse"
    ) -> float:
        """Return current density J at voltage *v* (mA/cm²).

        Args:
            v: Applied voltage (V).
            scan_direction: ``"forward"`` or ``"reverse"``.  Only changes the
                result in ``with_hysteresis`` mode.

        Returns:
            Current density in mA/cm².  Positive in the power-generating
            quadrant (CLAUDE.md sign convention).
        """
        vt = _KB * self._T / _Q
        j_ph, j0, n, rs, rsh = self._get_params(scan_direction)
        j = self._solve_j(v, j_ph, j0, n, rs, rsh, vt)
        if self.mode == "noisy":
            j += float(self._rng.normal(0.0, self._noise_std))
        return j

    def scan(
        self, v_arr: np.ndarray, scan_direction: str = "reverse"
    ) -> np.ndarray:
        """Return a J array for every voltage in *v_arr*.

        Args:
            v_arr: 1-D array of voltages (V).
            scan_direction: ``"forward"`` or ``"reverse"``.

        Returns:
            NumPy float64 array of current densities (mA/cm²).
        """
        return np.array(
            [self.current_density(float(v), scan_direction) for v in v_arr],
            dtype=np.float64,
        )
