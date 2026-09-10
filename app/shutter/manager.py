"""
``ShutterManager`` — the single object the rest of the application talks to.

It owns the configured backend, serialises access to it, persists the
configuration, and reports state changes to the GUI. Measurement code never
touches a backend directly; it calls ``open()``, ``close()`` or
``close_quietly()`` on the manager and gets a settled shutter back.

Why the lock: the Keithley backend shares the SourceMeter's VISA session, and
the settings panel's Test buttons run on the Tk thread while measurements run on
their own daemon thread. One re-entrant lock around every actuation removes that
whole class of interleaving bug. (The panel also disables its buttons during a
measurement — belt and braces, because a Test in the middle of a scan would be
wrong even if it were safe.)

Retries: an actuation that raises is attempted once more before giving up. A
GPIB hiccup should not end a 500-hour ISOS run, but a genuinely broken shutter
must not be silently tolerated either — so the second failure propagates as
:class:`~app.shutter.base.ShutterError`.
"""

from __future__ import annotations

import threading

from app.io import app_settings
from app.shutter import (
    BACKENDS,
    DEFAULT_BACKEND,
    VERIFY_DEFAULTS,
    UNKNOWN,
    create_shutter,
    default_config,
    get_backend,
)
from app.shutter.base import ShutterError

SETTINGS_SECTION = "shutter"


class ShutterManager:
    """Holds the active shutter backend and mediates every access to it.

    Args:
        config: a settings dict as produced by
            :func:`~app.shutter.default_config`. When None, it is loaded from
            ``settings.json`` (falling back to defaults).
        prompt: ``prompt(action)`` used by the manual backend; injected by the
            GUI so this module stays Tk-free.
        on_state_change: called with the manager after every state change, so
            the GUI can refresh its indicator. Exceptions from it are swallowed.
    """

    def __init__(self, config: dict | None = None, prompt=None,
                 on_state_change=None) -> None:
        self._lock = threading.RLock()
        self._prompt = prompt
        self._on_state_change = on_state_change
        self._instrument = None
        self._shutter = None
        self._config = {}
        self.configure(config if config is not None else self.load_config())

    # ── Configuration ────────────────────────────────────────────────────

    @staticmethod
    def load_config() -> dict:
        """Read the shutter section of settings.json, merged over the defaults."""
        stored = app_settings.load_section(SETTINGS_SECTION)
        config = default_config()
        if stored.get("backend") in BACKENDS:
            config["backend"] = stored["backend"]
        if isinstance(stored.get("verify_with_current"), bool):
            config["verify_with_current"] = stored["verify_with_current"]
        try:
            threshold = float(stored.get("verify_threshold_a"))
            if threshold > 0:
                config["verify_threshold_a"] = threshold
        except (TypeError, ValueError):
            pass
        options = stored.get("options")
        if isinstance(options, dict):
            for backend_id, opts in options.items():
                if isinstance(opts, dict) and backend_id in config["options"]:
                    merged = dict(config["options"][backend_id])
                    merged.update(opts)
                    try:
                        merged = get_backend(backend_id).coerce(merged)
                    except ValueError:
                        merged = get_backend(backend_id).defaults()
                    config["options"][backend_id] = merged
        return config

    def save_config(self) -> bool:
        """Persist the current configuration. Never raises."""
        return app_settings.save_section(SETTINGS_SECTION, self._config)

    def configure(self, config: dict) -> None:
        """Swap in a new configuration, rebuilding the backend."""
        with self._lock:
            config = dict(config or default_config())
            backend_id = config.get("backend") or DEFAULT_BACKEND
            spec = get_backend(backend_id)
            options = (config.get("options") or {}).get(spec.id, {})
            self._config = config
            self._config["backend"] = spec.id
            self._shutter = create_shutter(spec.id, options, prompt=self._prompt)
            if self._instrument is not None:
                self._shutter.attach_instrument(self._instrument)
        self._notify()

    @property
    def config(self) -> dict:
        return self._config

    @property
    def backend_id(self) -> str:
        return self._config.get("backend", DEFAULT_BACKEND)

    @property
    def spec(self):
        return get_backend(self.backend_id)

    @property
    def is_automatic(self) -> bool:
        """True when the backend can actuate without asking a human."""
        return self.spec.is_automatic

    @property
    def requires_instrument(self) -> bool:
        """True when the backend can only act once a SourceMeter is connected."""
        return self.spec.requires_instrument

    @property
    def verify_with_current(self) -> bool:
        """Whether to confirm the light state with an SMU reading."""
        return bool(self._config.get(
            "verify_with_current", VERIFY_DEFAULTS["verify_with_current"]))

    @property
    def verify_threshold_a(self) -> float:
        """Current above which the cell counts as illuminated, in amps."""
        try:
            value = float(self._config.get("verify_threshold_a"))
            return value if value > 0 else VERIFY_DEFAULTS["verify_threshold_a"]
        except (TypeError, ValueError):
            return VERIFY_DEFAULTS["verify_threshold_a"]

    # ── Instrument lifecycle ─────────────────────────────────────────────

    def attach_instrument(self, inst) -> None:
        """Hand the live SourceMeter session to the backend."""
        with self._lock:
            self._instrument = inst
            if self._shutter is not None:
                self._shutter.attach_instrument(inst)
        self._notify()

    def detach_instrument(self) -> None:
        with self._lock:
            self._instrument = None
            if self._shutter is not None:
                self._shutter.detach_instrument()
        self._notify()

    @property
    def ready(self) -> bool:
        """True when an actuation can be attempted right now."""
        with self._lock:
            return self._shutter is not None and self._shutter.ready

    @property
    def state(self) -> str:
        with self._lock:
            return self._shutter.state if self._shutter else UNKNOWN

    # ── Actuation ────────────────────────────────────────────────────────

    def _act(self, action: str) -> None:
        try:
            with self._lock:
                if self._shutter is None:
                    raise ShutterError("No shutter backend is configured.")
                method = getattr(self._shutter, action)
                try:
                    method()
                except ShutterError:
                    method()      # one retry; a second failure propagates
        finally:
            self._notify()        # the indicator must follow even on failure

    def open(self) -> None:
        """Open the shutter, blocking until it has settled."""
        self._act("open")

    def close(self) -> None:
        """Close the shutter, blocking until it has settled."""
        self._act("close")

    def close_quietly(self) -> bool:
        """Close without ever raising — for ``finally`` blocks and shutdown.

        Returns True when the shutter is known to be closed afterwards.
        """
        try:
            self.close()
            return True
        except Exception:
            self._notify()
            return False

    def self_test(self) -> str:
        """Run the backend's close/open/close self-test and return its report."""
        with self._lock:
            if self._shutter is None:
                raise ShutterError("No shutter backend is configured.")
            report = self._shutter.self_test()
        self._notify()
        return report

    def describe(self) -> str:
        with self._lock:
            return self._shutter.describe() if self._shutter else "no shutter"

    # ── Notification ─────────────────────────────────────────────────────

    def _notify(self) -> None:
        if self._on_state_change is None:
            return
        try:
            self._on_state_change(self)
        except Exception:
            pass          # a failing indicator must never break a measurement
