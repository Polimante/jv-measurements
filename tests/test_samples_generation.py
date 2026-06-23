"""Guard tests for the bundled Samples/ example files (docs/_gen_samples.py).

Two layers:
  1. The *committed* Samples/ files must open through the real application
     readers (load_jv_file, read_isos_*). This is the guarantee users rely on —
     every shipped example loads in the app — and it is fast and platform-robust
     (no regeneration, no temp churn).
  2. A light smoke test that the generator module imports and its core
     (non-runner) export paths produce loadable files. The runner-based ISOS
     generation is intentionally NOT re-run here — it is heavy and already
     covered by the ISOS runner test suite — so CI stays fast and avoids
     spawning dozens of temp backup files.
"""
from __future__ import annotations

import importlib
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.gui.data_viewer import load_jv_file  # noqa: E402
from app.io.isos_tracking import (             # noqa: E402
    read_isos_config,
    read_isos_log,
    read_isos_jv_curves,
)

SAMPLES_DIR = os.path.join(ROOT, "Samples")


# ── 1. committed sample files open through the Data Viewer's universal loader ───

@pytest.mark.parametrize("filename,expected_type", [
    ("01_Illuminated_JV.xlsx",       "illuminated"),
    ("02_Dark_JV.xlsx",              "dark"),
    ("03_LightIntensity_JV.xlsx",    "light_jv"),
    ("04_StabilizedCurrent.xlsx",    "stab"),
    ("05_MPP_Tracking_stable.xlsx",  "mpp"),
    ("05_MPP_Tracking_burnin.xlsx",  "mpp"),
    ("06_PureMPP.xlsx",              "pure_mpp"),
])
def test_committed_sample_opens(filename, expected_type):
    path = os.path.join(SAMPLES_DIR, filename)
    assert os.path.exists(path), f"{filename} missing from Samples/"
    meta = load_jv_file(path)            # raises ValueError on unknown format
    assert isinstance(meta, dict)
    assert meta.get("type") == expected_type


def test_committed_illuminated_has_single_diode_fit():
    meta = load_jv_file(os.path.join(SAMPLES_DIR, "01_Illuminated_JV.xlsx"))
    # The auto-fit sheet must round-trip so the Single-Diode Fit button has data.
    assert meta.get("single_diode_fit") is not None


@pytest.mark.parametrize("filename,protocol", [
    ("07_ISOS_D1.xlsx",            "ISOS-D-1"),
    ("08_ISOS_L1_stable.xlsx",     "ISOS-L-1"),
    ("08_ISOS_L1_degrading.xlsx",  "ISOS-L-1"),
    ("09_ISOS_V1_degrading.xlsx",  "ISOS-V-1"),
])
def test_committed_isos_sample_loads(filename, protocol):
    path = os.path.join(SAMPLES_DIR, filename)
    assert os.path.exists(path), f"{filename} missing from Samples/"

    cfg = read_isos_config(path)
    assert cfg.get("protocol_id") == protocol

    log = read_isos_log(path)
    assert len(log) >= 2, "tracking log should hold several measurements"

    curves = read_isos_jv_curves(path)
    assert curves, "tracking file should contain per-scan J-V curves"


def test_committed_isos_d1_spans_real_time():
    """The D-1 sample must show a multi-day timeline (not all elapsed≈0)."""
    log = read_isos_log(os.path.join(SAMPLES_DIR, "07_ISOS_D1.xlsx"))
    elapsed = [float(r["Elapsed_h"]) for r in log if r.get("Elapsed_h") is not None]
    assert max(elapsed) > 100.0


def test_committed_isos_degrading_sample_drops():
    """The degrading L-1 sample's normalized PCE must fall meaningfully."""
    log = read_isos_log(os.path.join(SAMPLES_DIR, "08_ISOS_L1_degrading.xlsx"))
    norms = [float(r["PCE_rev_norm"]) for r in log
             if r.get("PCE_rev_norm") is not None]
    assert min(norms) < 0.95


# ── 2. the generator module imports and its core export paths are loadable ──────

def test_generator_core_paths_produce_loadable_files(tmp_path):
    # pytest's tmp_path cleanup is lenient (it never fails the test on a Windows
    # AV file-lock), unlike tempfile.TemporaryDirectory.
    gen = importlib.import_module("docs._gen_samples")
    out = str(tmp_path)

    gen.gen_illuminated_jv(out)
    gen.gen_dark_jv(out)

    illum = load_jv_file(os.path.join(out, "01_Illuminated_JV.xlsx"))
    assert illum.get("type") == "illuminated"
    dark = load_jv_file(os.path.join(out, "02_Dark_JV.xlsx"))
    assert dark.get("type") == "dark"
