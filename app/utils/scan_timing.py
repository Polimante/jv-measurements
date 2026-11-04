"""Scan timing that depends on the power-line frequency.

NPLC counts power-line cycles, so its duration depends on the grid: one NPLC
is 16.7 ms at 60 Hz (Brazil, North America) and 20 ms at 50 Hz (Europe). The
instrument integrates correctly either way, but the software's scan-rate and
delay arithmetic has to use the same frequency the instrument does, so that
frequency is read from the instrument when it connects.

Every NPLC-to-time conversion goes through :func:`nplc_duration_ms`. Tk-free.
"""

from __future__ import annotations

import time

# Used before an instrument is connected, and when one cannot report its
# line frequency. The GUI says so whenever a connected instrument did not.
DEFAULT_LINE_HZ: float = 60.0

_VALID_LINE_HZ = (50, 60)
_QUERY_TIMEOUT_MS = 3000


def nplc_duration_ms(nplc: float, line_hz: float | None = None) -> float:
    """Duration of *nplc* power-line cycles, in ms (*None* = the default grid)."""
    return float(nplc) * 1000.0 / float(line_hz or DEFAULT_LINE_HZ)


def scan_rate_mv_s(step_mv: float, delay_ms: float, nplc: float,
                   line_hz: float | None = None,
                   overhead_ms: float = 50.0) -> float | None:
    """Effective sweep rate in mV/s, or None when it cannot be computed."""
    time_per_point_ms = delay_ms + nplc_duration_ms(nplc, line_hz) + overhead_ms
    if time_per_point_ms <= 0 or step_mv <= 0:
        return None
    return step_mv / (time_per_point_ms / 1000.0)


def measured_scan_rate_mv_s(voltages, duration_s: float) -> float | None:
    """Sweep rate a scan actually achieved, from its measured duration.

    Each of the N points takes one step's worth of time, so the rate is the
    step divided by duration / N. Unlike :func:`scan_rate_mv_s` this needs no
    overhead estimate.
    """
    n = len(voltages)
    if n < 2 or duration_s <= 0:
        return None
    step_mv = abs(float(voltages[-1]) - float(voltages[0])) / (n - 1) * 1000.0
    return step_mv * n / duration_s


def timed_scan(timing: dict | None, name: str, scan):
    """Run ``scan()`` -> ``(v, j)``, recording how long it really took.

    *timing*, when not None, receives ``{name}_s`` and ``{name}_rate``. This
    is a real stopwatch on purpose, even where the caller has a virtual clock
    for its schedule: it reports what the hardware did. A stopped scan
    (``v is None``) records nothing.
    """
    t0 = time.perf_counter()
    v, j = scan()
    if timing is not None and v is not None:
        seconds = time.perf_counter() - t0
        timing[f"{name}_s"] = seconds
        timing[f"{name}_rate"] = measured_scan_rate_mv_s(v, seconds)
    return v, j


def format_scan_times(timing: dict) -> str:
    """One-line summary of a ``timing`` dict, for the status bar and the log."""
    def part(label, key):
        seconds = timing.get(f"{key}_s")
        if seconds is None:
            return None
        rate = timing.get(f"{key}_rate")
        return (f"{label} {seconds:.1f} s"
                + (f" ({rate:.1f} mV/s)" if rate is not None else ""))
    parts = [part("FWD", "fwd"), part("REV", "rev")]
    if timing.get("total_s") is not None:
        parts.append(f"total {timing['total_s']:.1f} s")
    return "  |  ".join(p for p in parts if p)


def read_line_frequency(inst) -> float | None:
    """The line frequency *inst* uses for NPLC (50.0 or 60.0), or None.

    Asks ``:SYST:LFR?``, which ``TSPTransport`` translates for a 2600. Never
    raises: None means "could not tell", whatever the reason, because a missing
    reading must not stop the instrument from connecting. The timeout is
    shortened for this one query, since firmware that does not answer would
    otherwise hold the GUI for the full session timeout.
    """
    try:
        previous_timeout = inst.timeout
        inst.timeout = _QUERY_TIMEOUT_MS
    except Exception:
        previous_timeout = None
    try:
        hz = float(str(inst.query(":SYST:LFR?")).strip().split(",")[0])
    except Exception:
        return None
    finally:
        if previous_timeout is not None:
            try:
                inst.timeout = previous_timeout
            except Exception:
                pass
    return float(round(hz)) if round(hz) in _VALID_LINE_HZ else None
