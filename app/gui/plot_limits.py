"""
Axis limits for the J-V plot.

Kept out of ``main_window`` deliberately: this is pure arithmetic with no Tk in
it, and ``main_window`` imports ``winsound``, which would make these tests
Windows-only for no reason. Living here they run on every CI platform.
"""

from __future__ import annotations

# Default frame for the J-V plot. Kept as a floor rather than a hard limit so
# ordinary runs look exactly as they always have, while an unusual sweep range
# still fits inside the axes.
_JV_XLIM_FLOOR = (0.0, 1.2)
_JV_YLIM_FLOOR = (0.0, 30.0)
_JV_YLIM_FLOOR_WITH_DARK = (-20.0, 30.0)


def _span(arrays):
    """Return (min, max) across every non-empty array, or None."""
    lows, highs = [], []
    for a in arrays:
        if a is None or len(a) == 0:
            continue
        lows.append(float(min(a)))
        highs.append(float(max(a)))
    return (min(lows), max(highs)) if lows else None


def jv_plot_limits(session_data):
    """Axis limits for the J-V plot: the default frame, grown to fit the data.

    These used to be hard-coded to 0-1.2 V, which clipped the top of a sweep
    that ran past 1.2 V — a high-Voc cell lost its zero-crossing off the right
    edge. The upper edge now follows the data.

    The lower edge is deliberately still pinned at 0 V: see below.

    Dark current is deliberately excluded from the **vertical** fit. A dark
    curve is exponential and reaches hundreds of mA/cm2 at high forward bias,
    so fitting it would squeeze a 25 mA/cm2 photocurrent into a flat line on
    zero. One linear axis cannot show both; the semi-log |J| view (Data Viewer
    -> "Dark Ideality Fit") is the tool for reading a dark diode. Dark curves
    are still drawn here — they simply leave the frame once they turn on, which
    is the honest picture on a linear axis.

    Returns ``((xmin, xmax), (ymin, ymax))``.
    """
    entries = list(session_data or [])
    has_dark = any(e.get("v_dark_fwd") is not None for e in entries)

    voltages = []
    verticals = []          # everything drawn against the left/right y axes
    for e in entries:
        voltages += [e.get("v_fwd"), e.get("v_rev"),
                     e.get("v_dark_fwd"), e.get("v_dark_rev")]
        verticals += [e.get("j_fwd"), e.get("j_rev"),
                      e.get("power_fwd"), e.get("power_rev")]

    def _grow(floor, span, min_pad, fraction):
        """Widen *floor* only on the side the data actually overflows.

        Padding just the overflowing edge keeps a run that already fitted
        inside the default window looking exactly as it always did.
        """
        lo, hi = floor
        if span is None:
            return lo, hi
        pad = max(min_pad, fraction * (span[1] - span[0]))
        if span[0] < lo:
            lo = span[0] - pad
        if span[1] > hi:
            hi = span[1] + pad
        return lo, hi

    # The left edge stays pinned at 0 V. Sweeps are commonly started below zero
    # as a conditioning step, and those points carry a settling transient that
    # belongs to the treatment rather than to the device — drawing them puts a
    # spurious decay at the left of every curve. They are still measured and
    # saved; they simply sit outside the frame. The right edge does follow the
    # data, so a high-Voc cell is never clipped.
    x_lo = _JV_XLIM_FLOOR[0]
    _, x_hi = _grow(_JV_XLIM_FLOOR, _span(voltages), 0.02, 0.03)

    y_floor = _JV_YLIM_FLOOR_WITH_DARK if has_dark else _JV_YLIM_FLOOR
    y_lo, y_hi = _grow(y_floor, _span(verticals), 1.0, 0.08)

    return (x_lo, x_hi), (y_lo, y_hi)
