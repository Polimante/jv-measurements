"""End-to-end ISOS-L-1 runner test.

Session 4 specifies driving the L-1 path with a hardware-free SMU. Per CLAUDE.md
the continuous runner talks raw SCPI (``inst.write`` / ``inst.query``), so it is
exercised with the SCPI-shaped ``FakeKeithley`` rather than the ``SourceMeter``
ABC — the same pattern as the other ContinuousISOSRunner tests. This stands in
for ``run_isos_l1_measurement``, which only assembles this config from the GUI
vars and hands it to the runner.

A virtual clock drives scheduling deterministically; the keithley helpers'
``time.sleep`` is patched out so the J-V scans run instantly.
"""

from __future__ import annotations

import threading
from unittest.mock import patch

import openpyxl

from app.measurement.isos_continuous_runner import ContinuousISOSRunner
from app.io.isos_tracking import read_isos_log

from tests._isos_runner_helpers import FakeKeithley, make_runner_config


def _l1_config(tmp_path, **overrides):
    cfg = make_runner_config(
        tmp_path,
        protocol_id="ISOS-L-1",
        total_duration_h=0.1,
        scan_interval_min=1.2,        # 0.02 h → 5 scans across 0.1 h
        baseline_n_scans=4,
        baseline_rest_s=0.0,
        spo_duration_s=1.0,
        pre_scan_relaxation_s=0.0,
        load_mode="OC",
    )
    cfg.update(overrides)
    return cfg


def _run(cfg):
    stop = threading.Event()
    runner = ContinuousISOSRunner(FakeKeithley(area=cfg["cell_area"]),
                                  cfg, {}, stop)
    with patch("app.instrument.keithley.time.sleep"):
        runner.run()
    return runner


def _sheet_counts(path):
    wb = openpyxl.load_workbook(path)
    names = wb.sheetnames
    wb.close()
    return names


def test_l1_run_produces_expected_sheets_and_rows(tmp_path):
    runner = _run(_l1_config(tmp_path))
    path = runner._tracking_path

    rows = read_isos_log(path)
    assert len(rows) == 5

    names = _sheet_counts(path)
    assert len([s for s in names if s.startswith("JV_#")]) == 5
    assert len([s for s in names if s.startswith("Baseline_#")]) == 4
    assert len([s for s in names if s.startswith("SPO_#")]) == 5


def test_l1_csv_consolidation_creates_stress_sheet(tmp_path):
    runner = _run(_l1_config(tmp_path))
    names = _sheet_counts(runner._tracking_path)
    assert any(s.startswith("Stress_") for s in names)


def test_l1_baseline_and_spo_columns_populated(tmp_path):
    runner = _run(_l1_config(tmp_path))
    rows = read_isos_log(runner._tracking_path)

    # Baseline PCE_0 median is written onto every measurement row.
    assert all(r.get("PCE_0_baseline_median") not in (None, "") for r in rows)
    # SPO was enabled (1 s) so PCE_SPO is populated on every row.
    assert all(r.get("PCE_SPO") not in (None, "") for r in rows)


def test_l1_mpp_load_also_runs(tmp_path):
    runner = _run(_l1_config(tmp_path, load_mode="MPP", mpp_engine="PO"))
    rows = read_isos_log(runner._tracking_path)
    assert len(rows) == 5
