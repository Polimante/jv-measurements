"""Dark J-V → ideality-fit pipeline check for ISOS-V-1.

Feeds synthetic dark J-V data generated from a known single-diode dark curve
(known n and J_s) through the same ``fit_dark_ideality`` call the V-1 runner
uses, and verifies the extracted ideality factor and saturation current match
within tolerance. Also checks ``calculate_resistances(..., Voc=None)`` returns a
finite dark R_s from the high-forward-bias slope.
"""

from __future__ import annotations

import numpy as np

from app.measurement.analysis import fit_dark_ideality, calculate_resistances

# Physical constants (match analysis.py).
_KB = 1.380649e-23
_Q = 1.602176634e-19


def _synthetic_dark_jv(n_true, js_true, T=298.15, rsh=1e7, vmax=0.9):
    """Dark J-V from J = J_s·(exp(V/(n·Vt)) − 1) + V·1e3/Rsh, mA/cm².

    Returned with the CLAUDE.md sign convention (dark forward current negative).
    """
    vt = _KB * T / _Q
    v = np.arange(-0.1, vmax + 1e-9, 0.025)
    j_diode = js_true * (np.exp(v / (n_true * vt)) - 1.0)
    j_shunt = v * 1e3 / rsh
    j_dark = -(j_diode + j_shunt)        # negative in forward bias
    return v, j_dark


def test_dark_fit_recovers_known_n_and_js():
    n_true, js_true = 1.8, 5e-9
    v, j = _synthetic_dark_jv(n_true, js_true)

    fit = fit_dark_ideality(v, j, v_min=0.5, v_max=0.8)

    assert fit.r_squared > 0.99
    assert abs(fit.n_idd - n_true) / n_true < 0.10
    # J_s should be the right order of magnitude.
    assert 0.2 * js_true < fit.J_s < 5.0 * js_true


def test_dark_resistances_voc_none_is_finite():
    v, j = _synthetic_dark_jv(1.5, 1e-9)
    rs, rsh = calculate_resistances(v, j, Voc=None)
    assert np.isfinite(rs)
    assert np.isfinite(rsh)
    assert rs > 0
