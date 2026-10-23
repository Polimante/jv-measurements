"""Tiered-schedule advancement for ISOS-V-1.

The community-standard V-1 cadence grows the measurement interval as stress
progresses (hourly 0–5 h, 4-hourly 5–24 h, daily 24 h+). The runner's
``next_scan_time`` consumes a ``tiered_schedule`` list of
``(start_h, end_h, interval_min)`` tuples; this test pins the advancement and
the 5 h / 24 h tier boundaries.
"""

from __future__ import annotations

import threading

from app.measurement.isos_continuous_runner import ContinuousISOSRunner

_TIERS = [
    (0.0, 5.0, 60.0),       # hourly
    (5.0, 24.0, 240.0),     # 4-hourly
    (24.0, float("inf"), 1440.0),  # daily
]


def _runner():
    cfg = {"tiered_schedule": _TIERS}
    return ContinuousISOSRunner(instrument=None, config=cfg,
                                callbacks={}, stop_event=threading.Event())


def test_tier1_hourly_before_5h():
    r = _runner()
    assert r.next_scan_time(0.0) == 1.0
    assert r.next_scan_time(4.0) == 5.0
    assert abs(r.next_scan_time(4.5) - 5.5) < 1e-9


def test_tier2_four_hourly_between_5_and_24h():
    r = _runner()
    # 5 h boundary belongs to tier 2 (start inclusive).
    assert r.next_scan_time(5.0) == 9.0
    assert r.next_scan_time(20.0) == 24.0


def test_tier3_daily_at_and_after_24h():
    r = _runner()
    # 24 h boundary belongs to tier 3.
    assert r.next_scan_time(24.0) == 48.0
    assert r.next_scan_time(30.0) == 54.0


def test_fixed_interval_when_no_tiers():
    cfg = {"scan_interval_min": 30.0}
    r = ContinuousISOSRunner(None, cfg, {}, threading.Event())
    assert abs(r.next_scan_time(1.0) - 1.5) < 1e-9
