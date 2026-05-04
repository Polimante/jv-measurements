from __future__ import annotations

from app.instrument.base import SourceMeter


def create_sourcemeter(model: str, address: str | None = None) -> SourceMeter:
    """Open and return a SourceMeter for the requested instrument model.

    Parameters
    ----------
    model:
        Instrument model key.  Currently only ``"keithley_2400"`` is supported.
    address:
        VISA resource string (e.g. ``"GPIB0::24::INSTR"``).  Required when
        connecting to real hardware.

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

    raise ValueError(
        f"Unknown instrument model {model!r}. "
        "Supported models: 'keithley_2400'."
    )
