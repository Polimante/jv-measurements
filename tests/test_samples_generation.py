"""Guard test for the bundled Samples/ generator (docs/_gen_samples.py).

Generates every sample into a temp folder and re-opens each file through the
*real* application readers, so a sample that the app could not load fails CI
rather than silently shipping. The generators reuse the production exporters and
the ISOS continuous runner, so this also smoke-tests that whole path.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile

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

gen = importlib.import_module("docs._gen_samples")


@pytest.fixture(scope="module")
def samples_dir():
    """Generate the full sample set once into a throwaway directory."""
    with tempfile.TemporaryDirectory() as out:
        # Redirect the generator's output folder to the temp dir.
        gen.SAMPLES_DIR = out
        for fn in gen._SIMPLE_GENERATORS:
            fn(out)
        for fn in gen._RUNNER_GENERATORS:
            with tempfile.TemporaryDirectory() as work:
                fn(work)
        yield out


# ── single-file samples open via the Data Viewer's universal loader ─────────────

@pytest.mark.parametrize("filename,expected_type", [
    ("01_Illuminated_JV.xlsx",       "illuminated"),
    ("02_Dark_JV.xlsx",              "dark"),
    ("03_LightIntensity_JV.xlsx",    "light_jv"),
    ("04_StabilizedCurrent.xlsx",    "stab"),
    ("05_MPP_Tracking_stable.xlsx",  "mpp"),
    ("05_MPP_Tracking_burnin.xlsx",  "mpp"),
    ("06_PureMPP.xlsx",              "pure_mpp"),
])
def test_sample_opens_in_data_viewer(samples_dir, filename, expected_type):
    path = os.path.join(samples_dir, filename)
    assert os.path.exists(path), f"{filename} was not generated"
    meta = load_jv_file(path)            # raises ValueError on unknown format
    assert isinstance(meta, dict)
    assert meta.get("type") == expected_type


def test_illuminated_has_single_diode_fit(samples_dir):
    meta = load_jv_file(os.path.join(samples_dir, "01_Illuminated_JV.xlsx"))
    # The auto-fit sheet must round-trip so the Single-Diode Fit button has data.
    assert meta.get("single_diode_fit") is not None


# ── ISOS tracking workbooks open via the ISOS readers ───────────────────────────

@pytest.mark.parametrize("filename,protocol", [
    ("07_ISOS_D1.xlsx",            "ISOS-D-1"),
    ("08_ISOS_L1_stable.xlsx",     "ISOS-L-1"),
    ("08_ISOS_L1_degrading.xlsx",  "ISOS-L-1"),
    ("09_ISOS_V1_degrading.xlsx",  "ISOS-V-1"),
])
def test_isos_sample_loads(samples_dir, filename, protocol):
    path = os.path.join(samples_dir, filename)
    assert os.path.exists(path), f"{filename} was not generated"

    cfg = read_isos_config(path)
    assert cfg.get("protocol_id") == protocol

    log = read_isos_log(path)
    assert len(log) >= 2, "tracking log should hold several measurements"

    curves = read_isos_jv_curves(path)
    assert curves, "tracking file should contain per-scan J-V curves"


def test_isos_d1_spans_real_time(samples_dir):
    """The D-1 sample must show a multi-day timeline (not all elapsed≈0)."""
    log = read_isos_log(os.path.join(samples_dir, "07_ISOS_D1.xlsx"))
    elapsed = [float(r["Elapsed_h"]) for r in log if r.get("Elapsed_h") is not None]
    assert max(elapsed) > 100.0


def test_isos_degrading_sample_drops(samples_dir):
    """The degrading L-1 sample's normalized PCE must fall meaningfully."""
    log = read_isos_log(os.path.join(samples_dir, "08_ISOS_L1_degrading.xlsx"))
    norms = [float(r["PCE_rev_norm"]) for r in log
             if r.get("PCE_rev_norm") is not None]
    assert min(norms) < 0.95
