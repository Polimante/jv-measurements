"""
The shutter abstraction: one tiny ABC plus the declarative config metadata that
lets the GUI build a backend's settings form without knowing anything about it.

Design contract
---------------
* ``open()`` and ``close()`` **block until the shutter has settled**, so every
  call site in the measurement code is a single line with no follow-up sleep.
* Both are **idempotent** — closing an already-closed shutter is a no-op that
  still leaves the state definitely CLOSED.
* A backend declares its configuration as a tuple of :class:`ConfigField`. The
  GUI renders that tuple; adding a backend therefore needs no GUI code at all.
* Nothing here imports tkinter, pyvisa or pyserial. Backends import their own
  dependencies lazily so an unused one never becomes a hard requirement.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable

# Shutter states. UNKNOWN is the honest answer before anything has been
# commanded — notably at application startup, when a line that idles high may
# well be holding the shutter open and no software can yet tell.
OPEN = "open"
CLOSED = "closed"
UNKNOWN = "unknown"


class ShutterError(RuntimeError):
    """A shutter operation could not be completed.

    Raised loudly rather than swallowed: continuing a measurement with an
    unknown illumination state produces plausible-looking, wrong data.
    """


# ── Declarative configuration ────────────────────────────────────────────────

@dataclass(frozen=True)
class ConfigField:
    """One user-settable option of a backend, rendered by the GUI as one row."""

    name: str
    label: str
    kind: str                     # "int" | "float" | "bool" | "str" | "choice"
    default: Any
    tooltip: str = ""
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple = ()
    unit: str = ""

    def coerce(self, raw: Any) -> Any:
        """Validate and convert a raw (usually string) value from the GUI.

        Raises ValueError with a message meant to be shown to the user.
        """
        if self.kind == "bool":
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in ("1", "true", "yes", "on")

        if self.kind == "choice":
            value = str(raw).strip()
            if value not in self.choices:
                raise ValueError(
                    f"{self.label}: {value!r} is not one of "
                    f"{', '.join(map(str, self.choices))}")
            return value

        if self.kind == "str":
            return str(raw)

        try:
            value = int(raw) if self.kind == "int" else float(raw)
        except (TypeError, ValueError):
            expected = "a whole number" if self.kind == "int" else "a number"
            raise ValueError(f"{self.label}: expected {expected}, got {raw!r}")

        if self.minimum is not None and value < self.minimum:
            raise ValueError(f"{self.label}: must be at least {self.minimum}")
        if self.maximum is not None and value > self.maximum:
            raise ValueError(f"{self.label}: must be at most {self.maximum}")
        return value


@dataclass(frozen=True)
class BackendSpec:
    """Everything the application needs to know about a backend up front.

    Attributes:
        id: stable key stored in settings.json — never rename one in place.
        label: what the dropdown shows.
        description: one paragraph shown under the dropdown.
        factory: ``(options, prompt) -> Shutter``; imports its module lazily.
        fields: the configuration form, in display order.
        requires_instrument: the backend can only act once a SourceMeter is
            connected (it shares that VISA session).
        is_automatic: the backend actuates the shutter by itself. False for the
            manual backend, which needs a human — the application uses this to
            decide whether it may silently close the shutter on connect.
    """

    id: str
    label: str
    description: str
    factory: Callable[..., "Shutter"]
    fields: tuple[ConfigField, ...] = ()
    requires_instrument: bool = False
    is_automatic: bool = True

    def defaults(self) -> dict:
        """Return this backend's default option dict."""
        return {f.name: f.default for f in self.fields}

    def coerce(self, options: dict | None) -> dict:
        """Validate *options*, filling in defaults for anything missing."""
        options = options or {}
        result = {}
        for f in self.fields:
            result[f.name] = f.coerce(options[f.name]) if f.name in options \
                else f.default
        return result


# ── The abstraction ──────────────────────────────────────────────────────────

class Shutter(ABC):
    """Base class for every shutter backend."""

    spec_id: str = "base"

    def __init__(self) -> None:
        self._state = UNKNOWN

    # -- state ------------------------------------------------------------
    @property
    def state(self) -> str:
        """OPEN, CLOSED or UNKNOWN — what this backend last established."""
        return self._state

    # -- lifecycle --------------------------------------------------------
    def attach_instrument(self, inst) -> None:
        """Hand over the live SourceMeter session (no-op unless needed)."""

    def detach_instrument(self) -> None:
        """Forget the SourceMeter session; the shutter becomes uncommandable."""

    @property
    def ready(self) -> bool:
        """True when open()/close() can actually be attempted right now."""
        return True

    # -- actuation --------------------------------------------------------
    @abstractmethod
    def open(self) -> None:
        """Open the shutter and return only once it has settled."""

    @abstractmethod
    def close(self) -> None:
        """Close the shutter and return only once it has settled."""

    @abstractmethod
    def describe(self) -> str:
        """One line naming the backend and its settings, for logs."""

    # -- diagnostics ------------------------------------------------------
    def self_test(self) -> str:
        """Close, open, close — timing each step. Returns a report string.

        Raises ShutterError if any step fails.
        """
        report = []
        for label, action in (("close", self.close),
                              ("open", self.open),
                              ("close", self.close)):
            t0 = time.perf_counter()
            action()
            report.append(f"{label} ok ({(time.perf_counter() - t0) * 1e3:.0f} ms)")
        return " → ".join(report)
