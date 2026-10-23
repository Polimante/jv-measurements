"""Ill-conditioned / degenerate inputs to fit_single_diode.

The single-diode fit is exercised on hard inputs (no zero-crossing, tiny shunt
resistance, very few points, heavy noise). The contract under stress is:
*never raise* — return a SingleDiodeFit and signal trouble through ``converged``
/ ``bounded_parameters`` / NaN metrics instead. These guard the GUI's auto-fit
and the Data Viewer refit, which both call this directly on user data.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.measurement.analysis import SingleDiodeFit, fit_single_diode
from app.simulation.dummy_cell import DummyCell

_T = 298.15


def _reverse_scan(cell: DummyCell, v_start=0.0, v_end=1.25, n_pts=60):
    v = np.linspace(v_end, v_start, n_pts)
    j = cell.scan(v, scan_direction="reverse")
    return v, j


def test_dark_curve_no_zero_crossing_does_not_raise():
    """A dark-like curve (no photocurrent) must not blow up the fit."""
    cell = DummyCell(mode="ideal_stable", j_ph=20.0)
    cell.set_illumination_factor(1e-6)   # shutter closed → no zero-crossing
    v, j = _reverse_scan(cell)
    result = fit_single_diode(v, j, T=_T)
    assert isinstance(result, SingleDiodeFit)
    assert isinstance(result.converged, bool)
    assert isinstance(result.bounded_parameters, list)


def test_tiny_shunt_resistance_is_flagged_not_raised():
    """Rsh far below the default lower bound (10 Ω·cm²) should bound-flag, not crash."""
    cell = DummyCell(mode="ideal_stable", j_ph=20.0, rsh=2.0)
    v, j = _reverse_scan(cell)
    result = fit_single_diode(v, j, T=_T)
    assert isinstance(result, SingleDiodeFit)
    # Either the optimiser pins Rsh to its lower bound (flagged) or it returns a
    # finite small value — both are acceptable; a raised exception is not.
    assert np.isfinite(result.parameters["Rsh"])


def test_too_few_points_returns_gracefully():
    """Fewer data points than free parameters must not raise."""
    v = np.array([1.2, 0.6, 0.0])
    j = np.array([0.0, 10.0, 20.0])
    result = fit_single_diode(v, j, T=_T)
    assert isinstance(result, SingleDiodeFit)
    assert isinstance(result.converged, bool)


def test_heavy_noise_still_returns_finite_or_nan_metrics():
    rng = np.random.default_rng(7)
    cell = DummyCell(mode="noisy", j_ph=20.0, n=1.8, rs=3.0, rsh=2000.0,
                     noise_std=3.0, rng=rng)
    cell.set_time_offset(0.0)
    v, j = _reverse_scan(cell)
    result = fit_single_diode(v, j, T=_T)
    assert isinstance(result, SingleDiodeFit)
    # r_squared is either a real number or NaN — never an exception.
    assert isinstance(result.r_squared, float)


def test_all_nan_input_does_not_raise():
    v = np.array([0.0, 0.5, 1.0, 1.2])
    j = np.full_like(v, np.nan)
    result = fit_single_diode(v, j, T=_T)
    assert isinstance(result, SingleDiodeFit)
    assert result.converged is False
