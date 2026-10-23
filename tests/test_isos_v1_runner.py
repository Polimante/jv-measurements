"""End-to-end ISOS-V-1 (dark electrical bias) runner test.

V-1 stresses the cell at constant bias in the dark and, each cycle, performs a
dark J-V scan followed (shutter open) by an illuminated J-V scan. This drives
the shared ``ContinuousISOSRunner`` with the SCPI-shaped ``FakeKeithley`` whose
illumination is toggled by the runner's ``on_shutter`` callback — so dark scans
read a pure-diode dark curve and illuminated scans read the lit curve (same
fake-instrument approach as the L-1 / continuous-runner tests, per CLAUDE.md).
"""

from __future__ import annotations

import threading
from unittest.mock import patch

import openpyxl

from app.measurement.isos_continuous_runner import ContinuousISOSRunner
from app.io.isos_tracking import read_isos_log, read_isos_config

from tests._isos_runner_helpers import FakeKeithley, make_v1_runner_config


def _v1_config(tmp_path, **overrides):
    cfg = make_v1_runner_config(
        tmp_path,
        total_duration_h=0.05,        # 180 s
        scan_interval_min=1.0,        # 60 s → 3 cycles
        stress_log_interval_s=6.0,
    )
    cfg.update(overrides)
    return cfg


def _run(cfg):
    fake = FakeKeithley(area=cfg["cell_area"])
    fake.set_light(False)             # V-1 starts in the dark

    shutter = {"open": 0, "close": 0}

    def _on_shutter(action):
        shutter[action] = shutter.get(action, 0) + 1
        fake.set_light(action == "open")

    stop = threading.Event()
    runner = ContinuousISOSRunner(fake, cfg, {"on_shutter": _on_shutter}, stop)
    with patch("app.instrument.keithley.time.sleep"):
        runner.run()
    return runner, shutter


def _sheets(path):
    wb = openpyxl.load_workbook(path)
    names = wb.sheetnames
    wb.close()
    return names


def test_v1_run_produces_dark_and_illuminated_sheets(tmp_path):
    runner, _ = _run(_v1_config(tmp_path))
    rows = read_isos_log(runner._tracking_path)
    assert len(rows) == 3

    names = _sheets(runner._tracking_path)
    assert len([s for s in names if s.startswith("JV_#")]) == 3
    assert len([s for s in names if s.startswith("JV_Dark_#")]) == 3


def test_v1_dark_params_populated(tmp_path):
    runner, _ = _run(_v1_config(tmp_path))
    rows = read_isos_log(runner._tracking_path)
    for r in rows:
        for key in ("n_dark_rev", "J_s_dark_rev", "R_s_dark_rev",
                    "R_sh_dark_rev", "HI_dark"):
            assert r.get(key) not in (None, ""), f"{key} missing in {r['#']}"


def test_v1_shutter_callbacks_fire_each_cycle(tmp_path):
    _, shutter = _run(_v1_config(tmp_path))
    assert shutter["open"] == 3
    assert shutter["close"] == 3


def test_v1_stress_with_light_false_in_config(tmp_path):
    runner, _ = _run(_v1_config(tmp_path))
    cfg = read_isos_config(runner._tracking_path)
    assert cfg.get("stress_with_light") is False


def test_v1_idark_logged_during_stress(tmp_path):
    runner, _ = _run(_v1_config(tmp_path))
    # Dark current sampled throughout the 3 stress intervals.
    assert len(runner.i_dark_trace) > 0
    rows = read_isos_log(runner._tracking_path)
    assert any(r.get("I_dark_during_stress_mean") not in (None, "") for r in rows)
