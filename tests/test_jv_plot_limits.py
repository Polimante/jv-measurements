"""Tests for the J-V plot's axis limits.

The upper edges were hard-coded to 1.2 V and 30 mA/cm², which clipped a
high-Voc cell's zero-crossing off the right of the plot. They now follow the
data, with the old window kept as a floor so ordinary runs look unchanged.

The *lower* X edge stays pinned at 0 V, and that is a deliberate choice rather
than an oversight: sweeps are commonly started below zero as a conditioning
step, and those points carry a settling transient belonging to the treatment,
not to the device. They are measured and saved, just not drawn.

Pure arithmetic in app/gui/plot_limits.py, so these run on every platform:
importing main_window would drag in winsound and make them Windows-only.
"""

import numpy as np
import pytest

from app.gui.plot_limits import (
    _JV_XLIM_FLOOR,
    _JV_YLIM_FLOOR,
    _JV_YLIM_FLOOR_WITH_DARK,
    jv_plot_limits,
)


def entry(v_start=0.0, v_end=1.1, jsc=22.0, dark=False, n=23):
    """One session_data-shaped entry."""
    v = np.linspace(v_start, v_end, n)
    j = np.linspace(jsc, -5.0, n)
    e = {
        "v_fwd": v, "j_fwd": j, "v_rev": v, "j_rev": j,
        "power_fwd": np.linspace(0.0, 18.0, n),
        "power_rev": np.linspace(0.0, 18.0, n),
        "v_dark_fwd": None, "j_dark_fwd": None,
        "v_dark_rev": None, "j_dark_rev": None,
    }
    if dark:
        e["v_dark_fwd"] = e["v_dark_rev"] = v
        # A real dark curve: nothing until turn-on, then hundreds of mA/cm².
        e["j_dark_fwd"] = e["j_dark_rev"] = -np.exp(np.linspace(-12, 6.7, n))
    return e


# ── Nothing to plot ──────────────────────────────────────────────────────────

def test_an_empty_session_keeps_the_default_frame():
    assert jv_plot_limits([]) == (_JV_XLIM_FLOOR, _JV_YLIM_FLOOR)
    assert jv_plot_limits(None) == (_JV_XLIM_FLOOR, _JV_YLIM_FLOOR)


def test_entries_with_no_arrays_keep_the_default_frame():
    blank = {k: None for k in
             ("v_fwd", "j_fwd", "v_rev", "j_rev", "power_fwd", "power_rev",
              "v_dark_fwd", "j_dark_fwd", "v_dark_rev", "j_dark_rev")}
    assert jv_plot_limits([blank]) == (_JV_XLIM_FLOOR, _JV_YLIM_FLOOR)


def test_empty_arrays_are_ignored():
    e = entry()
    e["v_fwd"] = np.array([])
    e["j_fwd"] = np.array([])
    (x_lo, x_hi), _ = jv_plot_limits([e])
    assert x_lo == 0.0 and x_hi >= 1.1


# ── Horizontal framing ───────────────────────────────────────────────────────

def test_a_sweep_starting_below_zero_is_still_framed_from_zero():
    """Conditioning points below 0 V are deliberately left out of the frame."""
    (x_lo, _), _ = jv_plot_limits([entry(v_start=-0.1, v_end=1.2)])
    assert x_lo == 0.0


def test_a_sweep_past_the_old_ceiling_is_not_clipped():
    (_, x_hi), _ = jv_plot_limits([entry(v_end=1.6)])
    assert x_hi > 1.6


def test_an_ordinary_sweep_keeps_the_familiar_frame():
    """Nothing changes for a run that already fitted inside the old window."""
    (x_lo, x_hi), (y_lo, y_hi) = jv_plot_limits([entry(v_start=0.0, v_end=1.0)])
    assert (x_lo, x_hi) == _JV_XLIM_FLOOR
    assert y_hi == _JV_YLIM_FLOOR[1]
    assert y_lo <= _JV_YLIM_FLOOR[0]


def test_the_widest_entry_in_the_session_sets_the_upper_edge():
    (x_lo, x_hi), _ = jv_plot_limits([entry(v_start=0.0, v_end=1.0),
                                      entry(v_start=-0.2, v_end=1.4)])
    assert x_hi > 1.4
    assert x_lo == 0.0          # however far below zero any of them started


# ── Dark curves ──────────────────────────────────────────────────────────────

def test_dark_data_lowers_the_vertical_floor():
    _, (y_lo, _) = jv_plot_limits([entry(dark=True)])
    assert y_lo <= _JV_YLIM_FLOOR_WITH_DARK[0]


def test_a_dark_curve_does_not_dictate_the_vertical_frame():
    """It reaches ~800 mA/cm²; fitting it would flatten the photocurrent."""
    lit_only = jv_plot_limits([entry(dark=False)])[1]
    with_dark = jv_plot_limits([entry(dark=True)])[1]
    assert with_dark[0] >= -25.0, "the frame followed the dark curve off-scale"
    # The illuminated curve stays legible either way.
    assert with_dark[1] == lit_only[1]


def test_a_dark_run_still_widens_the_upper_horizontal_edge():
    """Excluding dark vertically must not exclude it horizontally too."""
    e = entry(v_start=0.0, v_end=1.0, dark=True)
    e["v_dark_fwd"] = e["v_dark_rev"] = np.linspace(-0.3, 1.5, 23)
    (x_lo, x_hi), _ = jv_plot_limits([e])
    assert x_hi > 1.5
    assert x_lo == 0.0


# ── Fitting the illuminated data ─────────────────────────────────────────────

def test_a_large_photocurrent_raises_the_ceiling():
    _, (_, y_hi) = jv_plot_limits([entry(jsc=45.0)])
    assert y_hi > 45.0


def test_a_deeply_negative_excursion_lowers_the_floor():
    e = entry()
    e["j_rev"] = np.linspace(22.0, -60.0, len(e["v_rev"]))
    _, (y_lo, _) = jv_plot_limits([e])
    assert y_lo < -60.0


def test_power_shares_the_axis_and_is_included_in_the_fit():
    """ax2 is a twinx sharing ax's limits, so power must not be clipped."""
    e = entry()
    e["power_rev"] = np.linspace(0.0, 55.0, len(e["v_rev"]))
    _, (_, y_hi) = jv_plot_limits([e])
    assert y_hi > 55.0


@pytest.mark.parametrize("case", [
    entry(),
    entry(dark=True),
    entry(v_start=-0.5, v_end=2.0),
    entry(v_start=0.0, v_end=0.3),          # a sweep far short of the frame
    entry(jsc=0.0),                          # a flat, dead cell
])
def test_limits_are_always_ordered(case):
    """Whatever the data, xmin < xmax and ymin < ymax."""
    (x_lo, x_hi), (y_lo, y_hi) = jv_plot_limits([case])
    assert x_lo < x_hi
    assert y_lo < y_hi
