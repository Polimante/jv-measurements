"""
Tests for app/instrument/base.py and app/instrument/__init__.py.

Hardware-free: no pyvisa connection is opened.
"""

from __future__ import annotations

import importlib
import inspect
import sys
import threading
from typing import Callable

import numpy as np
import pytest


# ── ABC instantiation ──────────────────────────────────────────────────────────

def test_sourcemeter_cannot_be_instantiated():
    """SourceMeter must raise TypeError when instantiated directly."""
    from app.instrument.base import SourceMeter

    with pytest.raises(TypeError):
        SourceMeter()  # type: ignore[abstract]


# ── Factory ────────────────────────────────────────────────────────────────────

def test_factory_raises_for_unknown_model():
    """create_sourcemeter must raise ValueError for an unrecognised model."""
    from app.instrument import create_sourcemeter

    with pytest.raises(ValueError, match="Unknown instrument model"):
        create_sourcemeter("nonexistent_smu")


def test_factory_raises_includes_model_name_in_message():
    """The ValueError message should echo the bad model name."""
    from app.instrument import create_sourcemeter

    with pytest.raises(ValueError, match="'bad_model'"):
        create_sourcemeter("bad_model")


# ── Abstract method signatures ─────────────────────────────────────────────────

_EXPECTED_SIGNATURES: dict[str, list[str]] = {
    "configure":            ["self", "nplc", "delay_s", "four_wire"],
    "set_voltage":          ["self", "v"],
    "output_on":            ["self"],
    "output_off":           ["self"],
    "read_current_density": ["self", "cell_area"],
    "apply_prebias":        ["self", "v_pb", "t_pb", "stop_event", "status_callback"],
    "apply_light_soaking":  ["self", "duration_s", "stop_event", "status_callback"],
    "measure_scan":         ["self", "start", "end", "step", "cell_area", "stop_event"],
}


@pytest.mark.parametrize("method_name,param_names", _EXPECTED_SIGNATURES.items())
def test_abstract_method_signature(method_name: str, param_names: list[str]):
    """Each abstract method must exist and have the declared parameter names."""
    from app.instrument.base import SourceMeter

    assert hasattr(SourceMeter, method_name), f"Missing method: {method_name}"
    sig = inspect.signature(getattr(SourceMeter, method_name))
    assert list(sig.parameters) == param_names, (
        f"{method_name}: expected params {param_names}, "
        f"got {list(sig.parameters)}"
    )


def test_all_expected_methods_are_abstract():
    """Every method in the expected set must be declared abstract."""
    from app.instrument.base import SourceMeter

    assert set(SourceMeter.__abstractmethods__) == set(_EXPECTED_SIGNATURES)


# ── Import laziness ────────────────────────────────────────────────────────────

def test_importing_app_instrument_does_not_load_pyvisa():
    """import app.instrument (and app.instrument.base) must not pull in pyvisa."""
    # Remove any cached pyvisa modules so the check is not a false negative.
    pyvisa_keys = [k for k in sys.modules if "pyvisa" in k]
    saved = {k: sys.modules.pop(k) for k in pyvisa_keys}

    try:
        import app.instrument as ai
        import app.instrument.base

        importlib.reload(ai)
        importlib.reload(app.instrument.base)

        assert not any("pyvisa" in k for k in sys.modules), (
            "pyvisa was imported as a side-effect of loading app.instrument"
        )
    finally:
        sys.modules.update(saved)
