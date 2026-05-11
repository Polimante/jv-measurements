"""J-V parameter extraction and single-diode model fitting."""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
from scipy.optimize import brentq, curve_fit
from scipy.stats import linregress

# ── Physical constants ────────────────────────────────────────────────────────
_KB: float = 1.380649e-23   # J/K
_Q: float  = 1.602176634e-19  # C
_LN10: float = math.log(10)

# Ordered parameter names for the single-diode model
_PARAM_NAMES: tuple[str, ...] = ("J_ph", "J0", "n", "Rs", "Rsh")

# Parameters that are optimised in log10 space for better numerical conditioning.
# The optimizer works on log10(J0) and log10(Rsh); back-transformation is applied
# before returning results to the caller.
_LOG_PARAMS: frozenset[str] = frozenset({"J0", "Rsh"})


# ── Existing local-derivative methods ─────────────────────────────────────────

def calculate_jv_parameters(voltages, currents, illumination):
    if illumination <= 0:
        raise ValueError(
            "Illumination is 0 mW/cm² — PCE cannot be calculated.\n"
            "Enter the detector power in the Illumination Settings panel."
        )

    crossings = np.where(np.diff(np.sign(currents)))[0]
    if len(crossings) == 0:
        raise ValueError(
            "No current zero-crossing found in the J-V curve.\n"
            "Voc cannot be determined.\n\n"
            "Possible causes:\n"
            "  • Solar simulator shutter is closed (dark measurement)\n"
            "  • End Voltage is set below the cell's Voc\n"
            "  • Cell is not illuminated or not connected correctly"
        )

    power  = voltages * currents
    idx_mp = np.argmax(power)
    Vm, Jm = voltages[idx_mp], currents[idx_mp]
    Voc    = voltages[crossings[0]]
    Jsc    = currents[np.argmin(np.abs(voltages))]

    if Voc == 0 or Jsc == 0:
        raise ValueError(
            "Voc or Jsc is zero — FF and PCE cannot be calculated.\n"
            "Check cell connections and illumination."
        )

    FF  = (Vm * Jm) / (Voc * Jsc) * 100
    PCE = (Voc * Jsc * FF) / illumination
    return Voc, abs(Jsc), FF, PCE


def calculate_resistances(voltages, currents, Voc, n=5):
    try:
        zi       = np.argmin(np.abs(voltages))
        s0, e0   = max(0, zi - n//2), min(len(voltages), zi - n//2 + n)
        m_rsh, _ = np.polyfit(voltages[s0:e0], currents[s0:e0], 1)
        Rsh      = abs(1000 / m_rsh) if m_rsh else float("inf")
        vi       = np.argmin(np.abs(voltages - Voc))
        s1, e1   = max(0, vi - n), min(len(voltages), vi + 1)
        m_rs, _  = np.polyfit(voltages[s1:e1], currents[s1:e1], 1)
        Rs       = (1 / abs(m_rs)) * 1000 if m_rs else float("inf")
        return Rs, Rsh
    except Exception:
        return float("nan"), float("nan")


def calculate_hysteresis_index(PCE_f, PCE_r):
    """Returns HI in % following Kim & Park (2014): (PCE_RS - PCE_FS) / PCE_RS × 100."""
    return (PCE_r - PCE_f) / PCE_r * 100 if PCE_r else float("nan")


# ── Single-diode model fitting ─────────────────────────────────────────────────

@dataclass
class SingleDiodeFit:
    """Result of a 5-parameter single-diode model fit to a J-V curve.

    Units (same as DummyCell):
        J_ph, J0  — mA/cm²
        n         — dimensionless
        Rs        — Ω·cm²
        Rsh       — Ω·cm²
        rmse      — mA/cm²

    Sign convention: photocurrent positive (CLAUDE.md).
    Implicit equation:
        J = J_ph - J0·(exp((V + J·Rs·1e-3)/(n·Vt)) - 1) - (V + J·Rs·1e-3)·1e3/Rsh
    """

    parameters: dict[str, float]
    std_errors: dict[str, float]
    r_squared: float
    rmse: float
    rmse_log: float
    converged: bool
    method: str
    bounded_parameters: list[str]
    temperature_K: float
    timestamp: str = field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )


def _solve_j_sdm(
    v: float,
    j_ph: float,
    j0: float,
    n: float,
    rs: float,
    rsh: float,
    vt: float,
) -> float:
    """Solve the implicit single-diode equation for J at one voltage (brentq)."""
    def residual(j: float) -> float:
        vj = v + j * rs * 1e-3
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            return j_ph - j0 * (np.exp(vj / (n * vt)) - 1.0) - vj * 1e3 / rsh - j

    try:
        return brentq(residual, -1e4, j_ph * 1.1 + 1.0, xtol=1e-9, rtol=1e-9)
    except (ValueError, RuntimeError):
        return float("nan")


def _estimate_initial_guess(
    v: np.ndarray, j: np.ndarray, T: float
) -> dict[str, float]:
    """Derive starting SDM parameters from measured Voc, Jsc, and local slopes."""
    vt = _KB * T / _Q

    # J_ph ≈ J at V closest to zero (short-circuit current density)
    zi = int(np.argmin(np.abs(v)))
    j_sc = float(abs(j[zi]))

    # Voc from first sign change in J
    crossings = np.where(np.diff(np.sign(j)))[0]
    if len(crossings) > 0:
        i = int(crossings[0])
        dj = float(j[i + 1] - j[i])
        voc = (float(v[i]) - float(j[i]) * (float(v[i + 1]) - float(v[i])) / dj
               if dj != 0 else float(v[i]))
    else:
        voc = float(v[np.argmin(np.abs(j))])

    # Rs ≈ inverse local slope near Voc (in Ω·cm²)
    vi = int(np.argmin(np.abs(v - voc)))
    s1, e1 = max(0, vi - 3), min(len(v), vi + 4)
    if e1 - s1 >= 2:
        m_rs = float(np.polyfit(v[s1:e1], j[s1:e1], 1)[0])
        rs_init = float(np.clip(abs(1000.0 / m_rs) if m_rs != 0 else 5.0, 0.5, 50.0))
    else:
        rs_init = 5.0

    # Rsh ≈ inverse local slope near V=0 (in Ω·cm²)
    s0, e0 = max(0, zi - 3), min(len(v), zi + 4)
    if e0 - s0 >= 2:
        m_rsh = float(np.polyfit(v[s0:e0], j[s0:e0], 1)[0])
        rsh_init = float(np.clip(abs(1000.0 / m_rsh) if m_rsh != 0 else 5000.0,
                                 100.0, 1e7))
    else:
        rsh_init = 5000.0

    # n, J0 from open-circuit boundary: J_ph ≈ J0·exp(Voc/(n·Vt))
    n_init = 1.5
    j0_raw = j_sc * np.exp(-voc / (n_init * vt)) if voc > 0 else 1e-12
    j0_init = float(np.clip(j0_raw, 1e-14, 1e-7))

    return {
        "J_ph": j_sc,
        "J0":   j0_init,
        "n":    n_init,
        "Rs":   rs_init,
        "Rsh":  rsh_init,
    }


def fit_single_diode(
    v: np.ndarray,
    j: np.ndarray,
    T: float = 298.15,
    initial_guess: Optional[dict[str, float]] = None,
    bounds: Optional[dict[str, tuple[float, float]]] = None,
) -> SingleDiodeFit:
    """Fit the 5-parameter single-diode model to a measured J-V curve.

    Uses scipy.optimize.curve_fit with pointwise brentq solving of the implicit
    diode equation.  All units follow the CLAUDE.md convention (mA/cm², Ω·cm²).

    Args:
        v: Voltage array (V).  Any monotonic ordering is accepted.
        j: Current density array (mA/cm²).  Positive photocurrent convention.
        T: Cell temperature (K).
        initial_guess: Dict with keys J_ph, J0, n, Rs, Rsh.  Auto-estimated
            from the measured curve when None.
        bounds: Dict mapping each parameter name to a (lower, upper) tuple.
            Defaults to perovskite-appropriate bounds.

    Returns:
        SingleDiodeFit dataclass with parameters, uncertainties, and
        goodness-of-fit metrics.
    """
    vt = _KB * T / _Q
    v = np.asarray(v, dtype=float)
    j = np.asarray(j, dtype=float)

    if initial_guess is None:
        initial_guess = _estimate_initial_guess(v, j, T)

    j_sc = initial_guess.get("J_ph", float(abs(j[np.argmin(np.abs(v))])))
    if j_sc <= 0:
        j_sc = 20.0

    # Build default perovskite bounds
    if bounds is None:
        bounds = {
            "J_ph": (0.5 * j_sc, 2.0 * j_sc),
            "J0":   (1e-15, 1e-6),
            "n":    (0.8, 5.0),
            "Rs":   (0.01, 100.0),
            "Rsh":  (10.0, 1e8),
        }

    # Build optimizer-space bounds and initial guess.
    # J0 and Rsh are reparameterised as log10(value) for better numerical
    # conditioning — they span 9 and 7 orders of magnitude respectively.
    lb: list[float] = []
    ub: list[float] = []
    p0: list[float] = []
    for name in _PARAM_NAMES:
        lo, hi = bounds.get(name, (-np.inf, np.inf))
        val = float(initial_guess.get(name, (lo + hi) / 2.0))
        if name in _LOG_PARAMS:
            lo  = math.log10(lo)  if lo  > 0 else -30.0
            hi  = math.log10(hi)  if hi  > 0 else  0.0
            val = math.log10(max(val, 1e-300))
        val = float(np.clip(val, lo + 1e-9, hi - 1e-9))
        p0.append(val)
        lb.append(lo)
        ub.append(hi)

    # _LOG_PARAMS positions by index into _PARAM_NAMES
    _log_idx = {i for i, name in enumerate(_PARAM_NAMES) if name in _LOG_PARAMS}

    def _model(v_arr: np.ndarray, j_ph: float, j0_log: float,
                n: float, rs: float, rsh_log: float) -> np.ndarray:
        j0  = 10.0 ** j0_log
        rsh = 10.0 ** rsh_log
        return np.array(
            [_solve_j_sdm(float(vi), j_ph, j0, n, rs, rsh, vt) for vi in v_arr],
            dtype=float,
        )

    try:
        popt, pcov = curve_fit(
            _model, v, j,
            p0=p0,
            bounds=(lb, ub),
            maxfev=5000,
            ftol=1e-8,
            xtol=1e-8,
        )
        converged = True
        raw_std = np.sqrt(np.diag(np.abs(pcov)))
    except Exception:
        popt = np.array(p0, dtype=float)
        raw_std = np.full(len(_PARAM_NAMES), float("nan"))
        converged = False

    # Back-transform to physical space; propagate errors through log10 transform.
    popt_phys = popt.copy()
    std_phys  = raw_std.copy()
    for i in _log_idx:
        popt_phys[i] = 10.0 ** popt[i]
        std_phys[i]  = popt_phys[i] * _LN10 * raw_std[i]

    params     = dict(zip(_PARAM_NAMES, popt_phys))
    std_errors = dict(zip(_PARAM_NAMES, std_phys))

    # Fitted curve for metrics (evaluate model in physical space)
    j_fit = np.array(
        [_solve_j_sdm(float(vi), params["J_ph"], params["J0"],
                      params["n"], params["Rs"], params["Rsh"], vt) for vi in v],
        dtype=float,
    )
    valid = np.isfinite(j_fit)

    if valid.sum() > 1:
        res = j[valid] - j_fit[valid]
        ss_res = float(np.sum(res ** 2))
        ss_tot = float(np.sum((j[valid] - np.mean(j[valid])) ** 2))
        r_squared = float(1.0 - ss_res / ss_tot) if ss_tot > 0 else float("nan")
        rmse = float(np.sqrt(np.mean(res ** 2)))
    else:
        r_squared = float("nan")
        rmse = float("nan")

    # RMSE in log10|J| space — only on points with |J| > 1e-3 mA/cm²
    mask = (np.abs(j) > 1e-3) & (np.abs(j_fit) > 1e-3) & np.isfinite(j_fit)
    if mask.sum() > 1:
        log_res = np.log10(np.abs(j[mask])) - np.log10(np.abs(j_fit[mask]))
        rmse_log = float(np.sqrt(np.mean(log_res ** 2)))
    else:
        rmse_log = float("nan")

    # Bounded detection works in optimizer space (log10 for J0/Rsh),
    # so a 1% fraction correctly reflects proximity to a log-scale bound.
    bounded: list[str] = []
    for i, name in enumerate(_PARAM_NAMES):
        lo, hi = lb[i], ub[i]
        span = hi - lo
        if span > 0 and ((popt[i] - lo) / span < 0.01 or (hi - popt[i]) / span < 0.01):
            bounded.append(name)

    return SingleDiodeFit(
        parameters=params,
        std_errors=std_errors,
        r_squared=r_squared,
        rmse=rmse,
        rmse_log=rmse_log,
        converged=converged,
        method="curve_fit+brentq",
        bounded_parameters=bounded,
        temperature_K=float(T),
    )


# ── Dark ideality factor fitting ───────────────────────────────────────────────

@dataclass
class DarkIdealityFit:
    """Result of a log-linear fit of dark J-V data to extract the ideality factor.

    Equation fitted:  log10(|J|) = log10(J_s) + V / (n_idd · Vt · ln(10))
    where Vt = k_B · T / q.

    Units:
        J_s   — mA/cm²
        n_idd — dimensionless
        rmse_log — log10(mA/cm²)
    """

    n_idd: float
    n_idd_err: float
    J_s: float
    J_s_err: float
    r_squared: float
    rmse_log: float
    v_min: float
    v_max: float
    n_points: int
    temperature_K: float
    method: str
    timestamp: str = field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )


# ── Light ideality factor fitting ─────────────────────────────────────────────

@dataclass
class LightIdealityFit:
    """Result of a linear regression of Voc vs ln(L_normalized).

    Computes the light ideality factor n_idL following Caprioglio et al.
    (Adv. Energy Mater. 2020) and the Fluxim DC measurement convention
    (equation 4).

    The regression is performed on L_norm = L / max(L) so that the absolute
    units of the intensity axis (suns, %, mW/cm²) are irrelevant — only the
    ratios between intensities matter.  n_idL is then:

        n_idL = (q / k_B / T) × slope,   slope = dVoc / d(ln(L_norm))

    This quantity is immune to series-resistance effects because Voc shifts
    only in response to the recombination rate change, making n_idL a cleaner
    indicator of the dominant recombination mechanism than the dark ideality
    factor n_idd.
    """

    n_idL: float
    n_idL_err: float
    slope: float
    slope_err: float
    intercept: float
    r_squared: float
    n_points: int
    intensities_normalized: np.ndarray
    voc_values: np.ndarray
    temperature_K: float


def fit_light_ideality(
    intensities: np.ndarray,
    voc_values: np.ndarray,
    T: float = 298.15,
) -> LightIdealityFit:
    """Fit the light ideality factor n_idL from Voc vs ln(L_normalized).

    Performs a linear regression of Voc (V) on ln(L/max(L)).  n_idL is
    derived from the slope via n_idL = q / (k_B × T) × slope.

    The normalization L_norm = L / max(L) means that the absolute units of
    *intensities* (suns, %, mW/cm², or any other proportional scale) do not
    affect the result — only the intensity ratios matter.

    Args:
        intensities: 1-D array of illumination intensities in any consistent
            unit.  Must have at least 3 distinct values.
        voc_values: 1-D array of Voc values (V), same length as *intensities*.
        T: Cell temperature (K).  Defaults to 298.15 K (25 °C).

    Returns:
        LightIdealityFit dataclass with fit parameters and echoed input arrays.

    Raises:
        ValueError: If fewer than 3 data points are provided, or if all
            intensities are identical (zero variation in ln(L)).
    """
    intensities = np.asarray(intensities, dtype=float)
    voc_values  = np.asarray(voc_values,  dtype=float)

    n = len(intensities)
    if n < 3:
        raise ValueError(
            f"fit_light_ideality requires at least 3 data points; got {n}."
        )
    if len(voc_values) != n:
        raise ValueError(
            f"intensities (length {n}) and voc_values (length {len(voc_values)}) "
            "must have the same length."
        )

    if np.all(intensities == intensities[0]):
        raise ValueError(
            "All intensities are identical — ln(L) has no variation; "
            "cannot determine slope."
        )

    l_norm = intensities / np.max(intensities)
    ln_l   = np.log(l_norm)

    if np.allclose(voc_values, voc_values[0], atol=1e-6):
        warnings.warn(
            "All Voc values are identical — n_idL will be 0 with a large error.",
            UserWarning,
            stacklevel=2,
        )

    result    = linregress(ln_l, voc_values)
    slope     = float(result.slope)
    slope_err = float(result.stderr)
    intercept = float(result.intercept)
    r_sq      = float(result.rvalue ** 2)

    vt     = _KB * T / _Q
    n_idL  = slope / vt
    n_idL_err = slope_err / vt

    return LightIdealityFit(
        n_idL=n_idL,
        n_idL_err=n_idL_err,
        slope=slope,
        slope_err=slope_err,
        intercept=intercept,
        r_squared=r_sq,
        n_points=n,
        intensities_normalized=l_norm,
        voc_values=voc_values.copy(),
        temperature_K=float(T),
    )


def fit_dark_ideality(
    v: np.ndarray,
    j: np.ndarray,
    v_min: float,
    v_max: float,
    T: float = 298.15,
) -> DarkIdealityFit:
    """Fit dark ideality factor from log10(|J|) vs V in a user-selected window.

    Performs a linear regression of log10(|J|) on V within [v_min, v_max].
    The slope converts to the ideality factor via:
        n_idd = q / (k_B · T · ln(10) · slope) = 1 / (Vt · ln(10) · slope)

    Args:
        v: Voltage array (V).  Need not be sorted.
        j: Current density array (mA/cm²).  Sign convention per CLAUDE.md
            (dark current is negative after instrument negation; absolute value
            is taken internally).
        v_min: Lower bound of the exponential region (V).
        v_max: Upper bound of the exponential region (V).
        T: Cell temperature (K).

    Returns:
        DarkIdealityFit dataclass.

    Raises:
        ValueError: If fewer than 5 positive-current points fall inside the window.
    """
    v = np.asarray(v, dtype=float)
    j = np.asarray(j, dtype=float)

    if v_min > v_max:
        warnings.warn(
            f"fit_dark_ideality: v_min ({v_min}) > v_max ({v_max}); swapping.",
            UserWarning,
            stacklevel=2,
        )
        v_min, v_max = v_max, v_min

    # Sort by voltage
    order = np.argsort(v)
    v = v[order]
    j = j[order]

    # Window mask
    window = (v >= v_min) & (v <= v_max)
    v_w = v[window]
    j_w = j[window]

    # Keep only points where |J| > 0 (filter zeros and negatives of |J|)
    pos_mask = np.abs(j_w) > 0.0
    v_w = v_w[pos_mask]
    j_w = j_w[pos_mask]

    n_points = int(len(v_w))
    if n_points < 5:
        raise ValueError(
            f"Fit window must contain at least 5 data points; window "
            f"[{v_min}, {v_max}] V contains only {n_points} points "
            f"(after filtering non-positive |J| values)."
        )

    log_j = np.log10(np.abs(j_w))
    result = linregress(v_w, log_j)

    slope     = float(result.slope)
    intercept = float(result.intercept)
    slope_se  = float(result.stderr)

    vt = _KB * T / _Q
    n_idd = 1.0 / (vt * _LN10 * slope) if slope > 0 else float("nan")

    # Error propagation: n_idd = 1/(Vt·ln10·slope) → δn = n_idd · (δslope/slope)
    if slope > 0 and not math.isnan(slope_se):
        n_idd_err = abs(n_idd) * (slope_se / abs(slope))
    else:
        n_idd_err = float("nan")

    J_s = 10.0 ** intercept
    intercept_se = float(getattr(result, "intercept_stderr", float("nan")))
    J_s_err = J_s * _LN10 * intercept_se if not math.isnan(intercept_se) else float("nan")

    r_squared = float(result.rvalue ** 2)

    log_j_fit = intercept + slope * v_w
    residuals  = log_j - log_j_fit
    rmse_log   = float(np.sqrt(np.mean(residuals ** 2)))

    return DarkIdealityFit(
        n_idd=n_idd,
        n_idd_err=n_idd_err,
        J_s=J_s,
        J_s_err=J_s_err,
        r_squared=r_squared,
        rmse_log=rmse_log,
        v_min=v_min,
        v_max=v_max,
        n_points=n_points,
        temperature_K=float(T),
        method="log_linear_regression",
    )
