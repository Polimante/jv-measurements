"""
ISOS stability metrics: T80, T95, T_S (burn-in), PCE_S, Ts80, and n_1000.

Pure, side-effect-free math computed from a cumulative ``(time_h, PCE)`` series.
The continuous runner calls :func:`compute_stability_metrics` after every J-V
scan (in the runner thread, never the GUI thread) and forwards the result to the
GUI and the ``ISOS_Metrics`` sheet.

Metric definitions (see Khenkin et al., Nat Energy 5, 35 (2020) and the Fluxim
ISOS blog):

* **T80 / T95** — first time ``PCE(t) / PCE(0)`` drops to ``0.80`` / ``0.95``.
  ``PCE(0)`` is the explicit baseline (median from baseline conditioning).
* **T_S** — end of burn-in: the transition from fast initial decay to the slow
  stable region. Detected from the most-positive second derivative of the
  smoothed normalized PCE, with sanity fallbacks.
* **PCE_S** — PCE at ``t = T_S`` (mean over a short window centred on T_S).
* **Ts80** — time, measured *from* T_S, at which PCE drops to ``0.80 × PCE_S``.
* **n_1000** — ``PCE(1000 h) / PCE(0)``. Extrapolated from the stable-region
  slope when the experiment is shorter than 1000 h (flagged + uncertainty).

If a metric is never reached it is reported as ``None`` with ``*_reached=False``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

try:  # SciPy is a hard dependency elsewhere (analysis.py); guard defensively.
    from scipy.signal import savgol_filter
    from scipy.stats import linregress
    _HAVE_SCIPY = True
except Exception:  # pragma: no cover - scipy is always present in CI
    savgol_filter = None
    linregress = None
    _HAVE_SCIPY = False


@dataclass
class StabilityMetrics:
    t80_h: float | None
    t80_reached: bool
    t95_h: float | None
    t95_reached: bool
    t_s_h: float | None
    pce_s: float | None
    ts80_h: float | None         # relative to T_S
    ts80_reached: bool
    n_1000: float | None
    n_1000_extrapolated: bool
    n_1000_confidence: float | None   # std error from regression
    burn_in_method: str          # "auto" | "manual" | "fallback"
    pce_column_used: str         # "PCE_SPO" or "PCE_rev"
    # Baseline used for normalization — not persisted, but needed by the plot
    # overlay to place the 0.80·PCE_S reference line on a normalized axis.
    pce_0: float | None = None


def _empty_metrics(burn_in_method="auto", pce_column_used="PCE_SPO",
                   pce_0=None) -> StabilityMetrics:
    """A fully-None result for degenerate inputs (e.g. a single data point)."""
    return StabilityMetrics(
        t80_h=None, t80_reached=False,
        t95_h=None, t95_reached=False,
        t_s_h=None, pce_s=None,
        ts80_h=None, ts80_reached=False,
        n_1000=None, n_1000_extrapolated=False, n_1000_confidence=None,
        burn_in_method=burn_in_method, pce_column_used=pce_column_used,
        pce_0=pce_0,
    )


def _first_crossing_below(t, y, thr):
    """First time ``y`` falls to ``<= thr``, linearly interpolated.

    Returns ``(t_cross, True)`` or ``(None, False)`` if never crossed. Assumes a
    generally decreasing series; the interpolation is between the last sample
    above the threshold and the first sample at/below it.
    """
    if len(y) == 0:
        return None, False
    if y[0] <= thr:
        return float(t[0]), True
    for i in range(1, len(y)):
        if y[i] <= thr:
            y0, y1 = y[i - 1], y[i]
            if y1 == y0:
                return float(t[i]), True
            frac = (thr - y0) / (y1 - y0)
            return float(t[i - 1] + frac * (t[i] - t[i - 1])), True
    return None, False


def _detect_t_s_auto(t, pce_norm, total_duration):
    """Auto burn-in detection. Returns ``(t_s, method)``.

    Smooths the normalized PCE with a Savitzky-Golay filter, takes the second
    derivative, and locates its most-positive value (concave-down fast decay →
    concave-up stable region). Falls back to ``0.20 × total_duration`` if the
    detected knee is implausibly early/late or the series is too short.
    """
    t0 = float(t[0])
    fallback = t0 + 0.20 * total_duration
    n = len(t)
    if not _HAVE_SCIPY or n < 5 or total_duration <= 0:
        return fallback, "fallback"

    window = min(11, max(5, n // 5))
    if window % 2 == 0:
        window += 1
    if window > n:                       # window must not exceed sample count
        window = n if n % 2 == 1 else n - 1
    if window < 5:                       # too few points for a 2nd-order fit
        return fallback, "fallback"

    try:
        smooth = savgol_filter(pce_norm, window, 2)
        d1 = np.gradient(smooth, t)
        d2 = np.gradient(d1, t)
        t_s = float(t[int(np.argmax(d2))])
    except Exception:
        return fallback, "fallback"

    low  = t0 + 0.05 * total_duration
    high = t0 + 0.50 * total_duration
    if t_s < low or t_s > high:
        return fallback, "fallback"
    return t_s, "auto"


def compute_stability_metrics(
    times_h,
    pce_values,
    pce_0,
    burn_in_mode: str = "auto",
    manual_burn_in_h: float | None = None,
    extrapolate_n1000: bool = True,
    pce_column_used: str = "PCE_SPO",
    compute_burn_in: bool = True,
) -> StabilityMetrics:
    """Compute the standard ISOS stability metrics from a PCE-vs-time series.

    Args:
        times_h: elapsed time per sample (hours).
        pce_values: canonical PCE per sample (same column throughout).
        pce_0: explicit baseline PCE (median from baseline conditioning). When
            falsy, the first valid PCE sample is used instead.
        burn_in_mode: ``"auto"`` | ``"manual"`` | ``"fallback"``.
        manual_burn_in_h: T_S to use when ``burn_in_mode == "manual"``.
        extrapolate_n1000: extrapolate n_1000 when the run is shorter than 1000 h.
        pce_column_used: label recorded on the result (``"PCE_SPO"``/``"PCE_rev"``).
        compute_burn_in: when ``False``, skip burn-in entirely — T_S/PCE_S/Ts80
            are reported as ``None`` and ``burn_in_method="none"``, and n_1000 is
            extrapolated from the full series. Use this for light-independent
            protocols (e.g. ISOS-D dark storage), where a burn-in is not
            physically expected and an auto-detected T_S would be spurious.

    Returns:
        A :class:`StabilityMetrics`. Degenerate inputs (fewer than two finite
        samples) yield an all-None result.
    """
    t   = np.asarray(times_h, dtype=float)
    pce = np.asarray(pce_values, dtype=float)

    # ── Input validation: finite, paired, sorted by time, length >= 2 ──────────
    if t.shape != pce.shape or t.ndim != 1:
        return _empty_metrics(burn_in_mode, pce_column_used)
    mask = np.isfinite(t) & np.isfinite(pce)
    t, pce = t[mask], pce[mask]
    if len(t) < 2:
        return _empty_metrics(burn_in_mode, pce_column_used,
                              pce_0 if pce_0 else None)
    order = np.argsort(t)
    t, pce = t[order], pce[order]

    base = float(pce_0) if pce_0 else float(pce[0])
    if base <= 0:
        return _empty_metrics(burn_in_mode, pce_column_used, base)

    pce_norm = pce / base
    total_duration = float(t[-1] - t[0])

    # ── T80 / T95 ──────────────────────────────────────────────────────────────
    t80, t80_reached = _first_crossing_below(t, pce_norm, 0.80)
    t95, t95_reached = _first_crossing_below(t, pce_norm, 0.95)

    # ── T_S (burn-in end) ──────────────────────────────────────────────────────
    if not compute_burn_in:
        t_s, burn_in_method = None, "none"
    elif burn_in_mode == "manual" and manual_burn_in_h is not None:
        t_s, burn_in_method = float(manual_burn_in_h), "manual"
    elif burn_in_mode == "fallback":
        t_s, burn_in_method = float(t[0] + 0.20 * total_duration), "fallback"
    else:
        t_s, burn_in_method = _detect_t_s_auto(t, pce_norm, total_duration)

    # ── PCE_S — mean over a window centred on T_S ──────────────────────────────
    if t_s is None:
        pce_s = None
    else:
        w = max(0.5, 0.02 * total_duration)
        win_mask = np.abs(t - t_s) <= w
        if np.any(win_mask):
            pce_s = float(np.mean(pce[win_mask]))
        else:
            pce_s = float(np.interp(t_s, t, pce))

    # ── Ts80 — first drop to 0.80·PCE_S measured from T_S ──────────────────────
    ts80_h, ts80_reached = None, False
    if t_s is not None and pce_s and pce_s > 0:
        after = t >= t_s
        t_after   = t[after]
        pce_after = pce[after]
        # Anchor the search at exactly T_S so the time is measured from there.
        pce_at_ts = float(np.interp(t_s, t, pce))
        t_seg   = np.concatenate(([t_s], t_after))
        pce_seg = np.concatenate(([pce_at_ts], pce_after))
        t_cross, reached = _first_crossing_below(t_seg, pce_seg, 0.80 * pce_s)
        if reached and t_cross is not None:
            ts80_h, ts80_reached = float(t_cross - t_s), True

    # ── n_1000 ─────────────────────────────────────────────────────────────────
    n_1000, n_1000_extrapolated, n_1000_confidence = None, False, None
    if t[-1] >= 1000.0:
        n_1000 = float(np.interp(1000.0, t, pce) / base)
    elif extrapolate_n1000 and _HAVE_SCIPY:
        if t_s is None:                  # no burn-in → regress the whole series
            xs, ys = t, pce
        else:
            stable = t > t_s
            xs, ys = t[stable], pce[stable]
        if len(xs) < 2:                  # not enough stable points — use all
            xs, ys = t, pce
        try:
            reg = linregress(xs, ys)
            pred = reg.slope * 1000.0 + reg.intercept
            n_1000 = float(pred / base)
            n_1000_extrapolated = True
            stderr = float(reg.stderr) if reg.stderr is not None else 0.0
            n_1000_confidence = abs(stderr * (1000.0 - float(t[-1])) / base)
        except Exception:
            pass

    return StabilityMetrics(
        t80_h=t80, t80_reached=t80_reached,
        t95_h=t95, t95_reached=t95_reached,
        t_s_h=t_s, pce_s=pce_s,
        ts80_h=ts80_h, ts80_reached=ts80_reached,
        n_1000=n_1000, n_1000_extrapolated=n_1000_extrapolated,
        n_1000_confidence=n_1000_confidence,
        burn_in_method=burn_in_method, pce_column_used=pce_column_used,
        pce_0=base,
    )
