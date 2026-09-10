"""
Manual shutter — the operator moves it, the software just asks and waits.

This is the default backend, and it reproduces exactly what the application did
before shutter support existed: a modal prompt at each dark/illuminated
boundary, blocking the measurement thread until the operator confirms. Anyone
who never opens the shutter settings therefore sees no change at all.

The prompt itself is injected (``prompt(action)``) so this module stays free of
tkinter — the same pattern ``ContinuousISOSRunner`` already uses for its
``on_shutter`` callback.
"""

from __future__ import annotations

from app.shutter.base import CLOSED, OPEN, Shutter, ShutterError


class ManualShutter(Shutter):
    """Asks a human to move the shutter and trusts the answer.

    Args:
        prompt: ``prompt(action) -> None`` where *action* is ``"open"`` or
            ``"close"``. Must block until the operator has confirmed, and raise
            to signal that they refused or cancelled.
    """

    spec_id = "manual"

    def __init__(self, prompt=None) -> None:
        super().__init__()
        self._prompt = prompt

    @property
    def ready(self) -> bool:
        return self._prompt is not None

    def _ask(self, action: str, new_state: str) -> None:
        if self._prompt is None:
            raise ShutterError(
                "No operator prompt is available, so the manual shutter cannot "
                "be commanded. This backend needs the GUI.")
        self._prompt(action)
        self._state = new_state

    def open(self) -> None:
        self._ask("open", OPEN)

    def close(self) -> None:
        self._ask("close", CLOSED)

    def describe(self) -> str:
        return "Manual — the operator opens and closes the shutter when prompted"
