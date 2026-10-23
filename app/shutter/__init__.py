"""
Shutter backend registry.

**Adding a backend is one file plus one entry in ``BACKENDS`` — no GUI code.**
The settings panel builds its form from the backend's ``fields`` tuple, so a
contributor who can write 60 lines of Python can add support for their own
hardware without touching tkinter.

Factories import their module lazily, so a backend's dependency (pyvisa,
pyserial, a vendor SDK) is only needed by people who actually select it.
"""

from __future__ import annotations

from app.shutter.base import (
    CLOSED,
    OPEN,
    UNKNOWN,
    BackendSpec,
    ConfigField,
    Shutter,
    ShutterError,
)

__all__ = [
    "BACKENDS", "DEFAULT_BACKEND", "VERIFY_DEFAULTS", "OPEN", "CLOSED", "UNKNOWN",
    "BackendSpec", "ConfigField", "Shutter", "ShutterError",
    "available_backends", "get_backend", "create_shutter", "default_config",
]

DEFAULT_BACKEND = "manual"


# ── Factories (lazy imports keep optional dependencies optional) ─────────────

def _make_manual(options: dict, prompt=None) -> Shutter:
    from app.shutter.manual import ManualShutter
    return ManualShutter(prompt=prompt)


def _make_keithley_digio(options: dict, prompt=None) -> Shutter:
    from app.shutter.keithley_digio import KeithleyDigioShutter
    return KeithleyDigioShutter(
        line=options.get("line", 1),
        active_high=options.get("active_high", True),
        settle_ms=options.get("settle_ms", 300),
        verify=options.get("verify", True),
    )


def _make_serial_command(options: dict, prompt=None) -> Shutter:
    from app.shutter.serial_command import SerialCommandShutter
    return SerialCommandShutter(**options)


# ── The registry ─────────────────────────────────────────────────────────────

BACKENDS: dict[str, BackendSpec] = {
    "manual": BackendSpec(
        id="manual",
        label="Manual (prompt the operator)",
        description=(
            "No automation. The application pauses and asks you to move the "
            "shutter by hand at each dark/illuminated boundary, exactly as it "
            "did before shutter control existed."
        ),
        factory=_make_manual,
        fields=(),
        requires_instrument=False,
        is_automatic=False,
    ),
    "keithley_digio": BackendSpec(
        id="keithley_digio",
        label="Keithley 2600 digital I/O",
        description=(
            "Drives one of the 14 digital I/O lines on the SourceMeter's DB-25 "
            "(pins 1-14). For an ABET Sun 2000, put the simulator's SHUTTER "
            "CONTROL switch in EXTERNAL first. Use probe_shutter.py to find "
            "the line, polarity and settling time of your wiring."
        ),
        factory=_make_keithley_digio,
        fields=(
            ConfigField(
                name="line", label="Digital I/O line", kind="int", default=1,
                minimum=1, maximum=14,
                tooltip="Which of the 14 digital I/O lines is wired to the "
                        "shutter.\nThe line number equals the DB-25 pin number "
                        "(1-14).\n\nFound with:  python probe_shutter.py",
            ),
            ConfigField(
                name="active_high", label="HIGH opens the shutter", kind="bool",
                default=True,
                tooltip="Checked: writing 1 opens, 0 closes (non-inverting "
                        "interface box).\nUnchecked: writing 0 opens "
                        "(inverting box / opto-isolator).\n\n"
                        "The 2600's lines idle HIGH through a 5.1 kOhm "
                        "pull-up, so with\nthis checked the shutter sits OPEN "
                        "whenever nothing is driving it —\nthe application "
                        "closes it explicitly on connect.",
            ),
            ConfigField(
                name="settle_ms", label="Settling time", kind="int", default=300,
                minimum=0, maximum=10000, unit="ms",
                tooltip="How long to wait after commanding, for the blade to "
                        "finish moving.\n\nMeasure it with:  python "
                        "probe_shutter.py --auto\nUse a comfortable multiple "
                        "of what you measure — this happens once or\ntwice per "
                        "measurement, not per point, so margin is free.\n"
                        "(ABET Sun 2000 here: 68 ms measured, 300 ms used.)",
            ),
            ConfigField(
                name="verify", label="Verify the line after writing",
                kind="bool", default=True,
                tooltip="Read the line back with digio.readbit and fail if it "
                        "did not take\nthe commanded level. Catches a "
                        "write-protected line or one being\nheld low by "
                        "another device. Costs one extra query.",
            ),
        ),
        requires_instrument=True,
        is_automatic=True,
    ),
    "serial_command": BackendSpec(
        id="serial_command",
        label="Serial / Arduino (text commands)",
        description=(
            "Sends a line of text over a serial port to open, and another to "
            "close. Works with an Arduino running the sketch in "
            "docs/arduino_shutter.ino, and with most commercial controllers "
            "(Thorlabs SC10, Uniblitz, USB relay boards) — only the words "
            "change. Needs pyserial:  pip install pyserial"
        ),
        factory=_make_serial_command,
        fields=(
            ConfigField(
                name="port", label="Serial port", kind="str", default="COM3",
                tooltip="COM3, COM4… on Windows; /dev/ttyUSB0 or "
                        "/dev/ttyACM0 on Linux.\n"
                        "Device Manager lists the port an Arduino enumerated as.",
            ),
            ConfigField(
                name="baudrate", label="Baud rate", kind="int", default=9600,
                minimum=300, maximum=1000000,
                tooltip="Must match the controller. The example sketch uses "
                        "9600.",
            ),
            ConfigField(
                name="open_command", label="Open command", kind="str",
                default="OPEN",
                tooltip="Text sent to open the shutter, without the line "
                        "ending.\nThorlabs SC10 uses 'ens', the example "
                        "sketch uses 'OPEN'.",
            ),
            ConfigField(
                name="close_command", label="Close command", kind="str",
                default="CLOSE",
                tooltip="Text sent to close the shutter, without the line "
                        "ending.",
            ),
            ConfigField(
                name="line_ending", label="Line ending", kind="choice",
                default="LF", choices=("LF", "CRLF", "CR", "none"),
                tooltip="Appended to every command. Arduino's readStringUntil"
                        "('\\n') wants LF.",
            ),
            ConfigField(
                name="settle_ms", label="Settling time", kind="int",
                default=300, minimum=0, maximum=10000, unit="ms",
                tooltip="Wait after commanding, for the blade to finish "
                        "moving.",
            ),
            ConfigField(
                name="query_command", label="State query (optional)",
                kind="str", default="",
                tooltip="Optional. Text that asks the controller for its "
                        "state.\nLeave empty if your controller cannot "
                        "answer — the shutter\nthen simply trusts "
                        "that the command was obeyed.",
            ),
            ConfigField(
                name="open_reply", label="Reply meaning 'open'", kind="str",
                default="OPEN",
                tooltip="Substring of the query reply that means the shutter "
                        "is open.\nOnly used when a state query is set.",
            ),
            ConfigField(
                name="timeout_s", label="Reply timeout", kind="float",
                default=2.0, minimum=0.1, maximum=60.0, unit="s",
                tooltip="How long to wait for an answer to the state query.",
            ),
            ConfigField(
                name="boot_delay_s", label="Delay after opening the port",
                kind="float", default=2.0, minimum=0.0, maximum=30.0, unit="s",
                tooltip="Most Arduino boards RESET when the serial port opens "
                        "(DTR),\nthen need a moment of bootloader time. "
                        "Set 0 for a controller\nthat does not reset.",
            ),
        ),
        requires_instrument=False,
        is_automatic=True,
    ),
}


# ── Public API ───────────────────────────────────────────────────────────────

def available_backends() -> list[BackendSpec]:
    """Every registered backend, manual first, then alphabetically by label."""
    others = sorted((s for s in BACKENDS.values() if s.id != DEFAULT_BACKEND),
                    key=lambda s: s.label)
    return [BACKENDS[DEFAULT_BACKEND]] + others


def get_backend(backend_id: str) -> BackendSpec:
    """Look up a backend spec, falling back to the manual backend."""
    return BACKENDS.get(str(backend_id), BACKENDS[DEFAULT_BACKEND])


def create_shutter(backend_id: str, options: dict | None = None,
                   prompt=None) -> Shutter:
    """Build a backend instance from its id and a (possibly partial) options dict."""
    spec = get_backend(backend_id)
    return spec.factory(spec.coerce(options), prompt)


# Backend-independent verification policy. The shutter can only report what it
# *commanded*; the SMU can report what the cell actually sees, which is the only
# closed-loop check available. Off by default: it needs a connected cell.
VERIFY_DEFAULTS = {
    "verify_with_current": False,
    "verify_threshold_a": 1e-6,
}


def default_config() -> dict:
    """The settings.json shape for a fresh installation.

    Options are stored per backend id, so switching back and forth in the GUI
    does not lose what you typed for the other one.
    """
    config = {
        "backend": DEFAULT_BACKEND,
        "options": {spec.id: spec.defaults() for spec in BACKENDS.values()},
    }
    config.update(VERIFY_DEFAULTS)
    return config
