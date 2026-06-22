"""Round-trip persistence tests for the ISOS_Metrics sheet.

compute_stability_metrics → append_metrics_row → read_isos_metrics, verifying the
written values survive the workbook round-trip.
"""

from __future__ import annotations

import numpy as np

from app.io.isos_tracking import (
    create_isos_tracking_file,
    append_metrics_row,
    read_isos_metrics,
)
from app.measurement.stability_metrics import compute_stability_metrics


def _make_metrics():
    t = np.linspace(0, 300, 150)
    pce = 20.0 * np.exp(-0.003 * t)
    return compute_stability_metrics(t, pce, pce_0=20.0, pce_column_used="PCE_rev")


def test_round_trip_single_row(tmp_path):
    path = create_isos_tracking_file(str(tmp_path), {"protocol_id": "ISOS-L-1",
                                                     "sample_name": "MX"})
    m = _make_metrics()
    append_metrics_row(path, scan_n=1, elapsed_h=300.0, metrics=m)

    rows = read_isos_metrics(path)
    assert len(rows) == 1
    r = rows[0]
    assert r["scan_#"] == 1
    assert abs(float(r["elapsed_h"]) - 300.0) < 1e-6
    assert abs(float(r["T80_h"]) - m.t80_h) < 1e-3
    assert bool(r["T80_reached"]) == m.t80_reached
    assert r["burn_in_method"] == m.burn_in_method
    assert r["pce_column_used"] == "PCE_rev"


def test_round_trip_multiple_rows_appends(tmp_path):
    path = create_isos_tracking_file(str(tmp_path), {"protocol_id": "ISOS-L-1",
                                                     "sample_name": "MX"})
    m = _make_metrics()
    for i in range(1, 4):
        append_metrics_row(path, scan_n=i, elapsed_h=float(i) * 100.0, metrics=m)

    rows = read_isos_metrics(path)
    assert len(rows) == 3
    assert [r["scan_#"] for r in rows] == [1, 2, 3]


def test_not_reached_metric_persists_as_none(tmp_path):
    path = create_isos_tracking_file(str(tmp_path), {"protocol_id": "ISOS-L-1",
                                                     "sample_name": "MX"})
    t = np.linspace(0, 50, 30)
    pce = np.full_like(t, 18.0)          # flat → T80 never reached
    m = compute_stability_metrics(t, pce, pce_0=18.0)
    append_metrics_row(path, scan_n=1, elapsed_h=50.0, metrics=m)

    r = read_isos_metrics(path)[0]
    assert r["T80_h"] is None
    assert not r["T80_reached"]


def test_read_metrics_empty_when_sheet_absent(tmp_path):
    path = create_isos_tracking_file(str(tmp_path), {"protocol_id": "ISOS-L-1",
                                                     "sample_name": "MX"})
    assert read_isos_metrics(path) == []
