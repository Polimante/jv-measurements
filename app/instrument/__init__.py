from __future__ import annotations

from app.instrument.base import SourceMeter


def create_sourcemeter(model: str, address: str | None = None, **kwargs) -> SourceMeter:
    """Open and return a SourceMeter for the requested instrument model.

    Parameters
    ----------
    model:
        Instrument model key.  Supported values:

        * ``"keithley_2400"`` — real hardware via pyvisa.
        * ``"dummy"`` — simulated cell; no hardware required.  Extra keyword
          arguments are forwarded to :class:`~app.instrument.dummy_smu.DummySMU`
          (and from there to :class:`~app.simulation.dummy_cell.DummyCell`).
          Useful kwargs: ``cell_mode``, ``j_ph``, ``rs``, ``rsh``, ``area``,
          ``hi_fraction``, ``tau_h``, ``noise_std``, ``sleep_scale``.
    address:
        VISA resource string (e.g. ``"GPIB0::24::INSTR"``).  Required when
        connecting to real hardware; ignored for ``"dummy"``.

    Raises
    ------
    ValueError
        If *model* is not a recognised instrument model.
    """
    if model == "keithley_2400":
        import pyvisa  # lazy — keeps bare import of this package pyvisa-free
        from app.instrument.keithley_adapter import Keithley2400
        rm = pyvisa.ResourceManager()
        inst = rm.open_resource(address)
        return Keithley2400(inst)

    if model == "dummy":
        from app.instrument.dummy_smu import DummySMU
        return DummySMU(**kwargs)

    raise ValueError(
        f"Unknown instrument model {model!r}. "
        "Supported models: 'keithley_2400', 'dummy'."
    )
