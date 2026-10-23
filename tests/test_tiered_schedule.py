"""Tiered-schedule / interval scheduling tests for next_scan_time."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from app.measurement.isos_continuous_runner import ContinuousISOSRunner

_SCHED = [(0, 1, 10), (1, 5, 30), (5, 24, 60)]   # (start_h, end_h, interval_min)


def _runner(**cfg):
    return ContinuousISOSRunner(MagicMock(), cfg, {}, threading.Event())


def test_first_tier_interval():
    r = _runner(tiered_schedule=_SCHED)
    assert r.next_scan_time(0.0) == pytest.approx(10 / 60.0)
    assert r.next_scan_time(0.5) == pytest.approx(0.5 + 10 / 60.0)


def test_second_tier_interval():
    r = _runner(tiered_schedule=_SCHED)
    assert r.next_scan_time(1.0) == pytest.approx(1.0 + 30 / 60.0)
    assert r.next_scan_time(4.9) == pytest.approx(4.9 + 30 / 60.0)


def test_third_tier_interval():
    r = _runner(tiered_schedule=_SCHED)
    assert r.next_scan_time(6.0) == pytest.approx(6.0 + 1.0)


def test_past_all_tiers_uses_last_interval():
    r = _runner(tiered_schedule=_SCHED)
    assert r.next_scan_time(30.0) == pytest.approx(30.0 + 1.0)


def test_constant_interval_fallback():
    r = _runner(scan_interval_min=15.0)
    assert r.next_scan_time(2.0) == pytest.approx(2.0 + 0.25)


def test_boundary_is_half_open():
    """At exactly end_h of a tier, the next tier's interval applies."""
    r = _runner(tiered_schedule=_SCHED)
    # elapsed=1.0 is the start of tier 2 (1,5,30) → 30 min, not tier 1's 10.
    assert r.next_scan_time(1.0) == pytest.approx(1.0 + 0.5)
