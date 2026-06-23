"""Generate the bundled ``Samples/`` folder of example measurement files.

Run from the project root::

    python docs/_gen_samples.py

Every file is produced from the project's own simulation + analysis + export
code (DummyCell physics, ``app.measurement.analysis`` and the real ``save_*`` /
ISOS exporters), so each sample is byte-for-byte the same kind of workbook a real
measurement would write — and therefore opens correctly in the application
(File -> Open Data File, the ISOS panel, Data Curation, etc.).

This is a build helper for the documentation/examples only. It is NOT imported by
the application. It is deterministic (fixed RNG seeds and a fixed virtual clock)
so re-running it produces stable, diff-friendly output.

The ISOS continuous protocols (L-1 / V-1) are produced by actually running the
production ``ContinuousISOSRunner`` against a SCPI-style ``FakeKeithley`` driven
by a ``VirtualClock`` — exactly the harness the test-suite uses — so the sample
workbooks contain authentic Stress / SPO / Metrics / dark-J-V sheets. A small
``datetime`` patch maps wall-clock timestamps onto virtual time so the run, which
completes in milliseconds, still records a realistic multi-hundred-hour timeline.
"""
from __future__ import annotations

import os
import sys
import shutil
import tempfile
import threading
from datetime import datetime as _real_datetime, timedelta
from unittest.mock import patch

import numpy as np

# Make the project (and the tests/ helpers) importable when run from anywhere.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.simulation.dummy_cell import DummyCell                       # noqa: E402
from app.measurement.analysis import (                                # noqa: E402
    calculate_jv_parameters,
    calculate_resistances,
    calculate_hysteresis_index,
    fit_single_diode,
)
from app.measurement.light_intensity_jv import IntensityResult        # noqa: E402
from app.io.exporter import (                                         # noqa: E402
    save_data,
    save_dark_jv_data,
    save_stab_current_data,
    save_pure_mpp_data,
    save_mpp_data,
)
from app.io.light_intensity_exporter import (                         # noqa: E402
    save_light_intensity_jv_consolidated,
)
from app.io.isos_tracking import (                                    # noqa: E402
    create_isos_d1_tracking_file,
    append_isos_measurement,
)
from app.measurement.isos_continuous_runner import ContinuousISOSRunner  # noqa: E402

# Test-suite fakes (SCPI-style instrument + deterministic clock + base configs).
sys.path.insert(0, os.path.join(ROOT, "tests"))
from _isos_runner_helpers import (                                    # noqa: E402
    FakeKeithley,
    VirtualClock,
    make_runner_config,
    make_v1_runner_config,
)

SAMPLES_DIR = os.path.join(ROOT, "Samples")
ILLUM = 100.0                      # reference illumination (mW/cm²) = 1 sun
IDN = "Simulated DummyCell SMU (sample data — not a real instrument)"
AREA = 0.10                        # cm²

# Fixed reference voltage grid for illuminated scans.
V_GRID = np.round(np.arange(-0.10, 1.20 + 1e-9, 0.02), 4)


# ── helpers ────────────────────────────────────────────────────────────────────

def _display_path(path: str) -> str:
    """Path relative to ROOT for tidy logs, falling back to the absolute path.

    os.path.relpath raises ValueError on Windows when the two paths are on
    different drives (e.g. a temp dir on C: vs. a repo checkout on D:), so this
    must never be allowed to break generation.
    """
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:
        return path


def _rename(fp: str, dest_dir: str, name: str) -> str:
    """Move an exporter's timestamped output to a clean, stable filename."""
    dst = os.path.join(dest_dir, name)
    if os.path.exists(dst):
        os.remove(dst)
    shutil.move(fp, dst)        # shutil.move copes with a cross-volume temp dir
    print("  wrote", _display_path(dst))
    return dst


def _jv_params(v, j, illum):
    """Return (Voc, Jsc, FF, PCE, Rs, Rsh) for one scan."""
    Voc, Jsc, FF, PCE = calculate_jv_parameters(v, j, illum)
    Rs, Rsh = calculate_resistances(v, j, Voc=Voc)
    return Voc, Jsc, FF, PCE, Rs, Rsh


def _patched_isos_datetime(now_fn):
    """A drop-in for ``app.io.isos_tracking.datetime`` whose ``now()`` is driven
    by *now_fn* (so timestamps/elapsed track a virtual timeline). ``strptime``
    still delegates to the real implementation."""

    class _DT:
        @staticmethod
        def now():
            return now_fn()

        @staticmethod
        def strptime(s, fmt):
            return _real_datetime.strptime(s, fmt)

    return _DT


# ── 1. Illuminated J-V (with hysteresis) ────────────────────────────────────────

def gen_illuminated_jv(out_dir: str) -> None:
    cell = DummyCell(mode="with_hysteresis", j_ph=22.5, j0=2e-12, n=1.45,
                     rs=4.0, rsh=6000.0, hi_fraction=0.10,
                     rng=np.random.default_rng(1))
    v = V_GRID
    j_rev = cell.scan(v, "reverse")
    j_fwd = cell.scan(v, "forward")

    Voc_r, Jsc_r, FF_r, PCE_r, Rs_r, Rsh_r = _jv_params(v, j_rev, ILLUM)
    Voc_f, Jsc_f, FF_f, PCE_f, Rs_f, Rsh_f = _jv_params(v, j_fwd, ILLUM)
    HI = calculate_hysteresis_index(PCE_f, PCE_r)

    try:
        fit = fit_single_diode(v, j_rev)
    except Exception:
        fit = None

    fp = save_data(
        out_dir, "01_Illuminated_JV",
        v, j_fwd, v, j_rev, ILLUM,
        Voc_f, Jsc_f, FF_f, PCE_f, Rs_f, Rsh_f,
        Voc_r, Jsc_r, FF_r, PCE_r, Rs_r, Rsh_r, HI,
        AREA, 20.0, 1.0, False,
        False, 0.0, 0.0,
        IDN, scan_rate=None, fit_result=fit,
    )
    _rename(fp, out_dir, "01_Illuminated_JV.xlsx")


# ── 2. Dark J-V (isolated diode) ────────────────────────────────────────────────

def gen_dark_jv(out_dir: str) -> None:
    cell = DummyCell(mode="ideal_stable", j_ph=0.0, j0=1e-9, n=1.8,
                     rs=5.0, rsh=1.0e6)
    v = np.round(np.arange(-0.20, 1.10 + 1e-9, 0.02), 4)
    j_rev = cell.scan(v, "reverse")
    j_fwd = cell.scan(v, "forward")

    fp = save_dark_jv_data(
        out_dir, "02_Dark_JV",
        v, j_fwd, v, j_rev,
        AREA, 20.0, 1.0, False, IDN,
    )
    _rename(fp, out_dir, "02_Dark_JV.xlsx")


# ── 3. Light-intensity dependent J-V ────────────────────────────────────────────

def gen_light_intensity(out_dir: str) -> None:
    intensities = [0.1, 0.25, 0.5, 0.75, 1.0]            # suns
    cell = DummyCell(mode="ideal_stable", j_ph=22.0, j0=1.5e-12, n=1.40,
                     rs=4.5, rsh=8000.0)
    v = V_GRID
    results = []
    for idx, suns in enumerate(intensities):
        cell.set_illumination_factor(suns)
        illum_abs = suns * ILLUM
        j_rev = cell.scan(v, "reverse")
        j_fwd = cell.scan(v, "forward")
        Voc_r, Jsc_r, FF_r, PCE_r, Rs_r, Rsh_r = _jv_params(v, j_rev, illum_abs)
        Voc_f, Jsc_f, FF_f, PCE_f, Rs_f, Rsh_f = _jv_params(v, j_fwd, illum_abs)
        try:
            sdf = fit_single_diode(v, j_rev)
        except Exception:
            sdf = None
        results.append(IntensityResult(
            index=idx, intensity=suns, status="completed",
            v_fwd=v, j_fwd=j_fwd, v_rev=v, j_rev=j_rev,
            Voc_fwd=Voc_f, Jsc_fwd=Jsc_f, FF_fwd=FF_f, PCE_fwd=PCE_f,
            Rs_fwd=Rs_f, Rsh_fwd=Rsh_f,
            Voc_rev=Voc_r, Jsc_rev=Jsc_r, FF_rev=FF_r, PCE_rev=PCE_r,
            Rs_rev=Rs_r, Rsh_rev=Rsh_r,
            HI=calculate_hysteresis_index(PCE_f, PCE_r),
            single_diode_fit=sdf,
        ))

    scan_params = {
        "start_v": float(v[0]), "end_v": float(v[-1]), "step_v": 0.02,
        "delay_ms": 20.0, "nplc": 1.0, "cell_area": AREA, "four_wire": False,
        "reference_illumination_mw_cm2": ILLUM,
    }
    fp = save_light_intensity_jv_consolidated(
        out_dir, "03_LightIntensity_JV", intensities, results, scan_params,
        stabilization_s=2.0, auto_fit=True, aborted_at=None, instrument_idn=IDN,
    )
    _rename(str(fp), out_dir, "03_LightIntensity_JV.xlsx")


# ── 4. Stabilized current ───────────────────────────────────────────────────────

def gen_stabilized_current(out_dir: str) -> None:
    rng = np.random.default_rng(4)
    t = np.round(np.arange(0.0, 120.0 + 1e-9, 0.5), 3)      # s, 500 ms interval
    v_applied = 0.85
    j_inf, j_0, tau = 19.4, 21.2, 14.0
    j = j_inf + (j_0 - j_inf) * np.exp(-t / tau) + rng.normal(0, 0.03, t.size)
    pce = v_applied * j / ILLUM * 100.0
    fp = save_stab_current_data(
        out_dir, "04_StabilizedCurrent",
        t.tolist(), j.tolist(), pce.tolist(),
        AREA, v_applied, ILLUM,
        duration_s=120.0, interval_ms=500.0, threshold_pct_per_min=1.0,
        stabilized_at=46.5,
    )
    _rename(fp, out_dir, "04_StabilizedCurrent.xlsx")


# ── 5. MPP tracking (J-V + SPO cycling) ─────────────────────────────────────────

def _build_mpp(out_dir, name, clean_name, j_ph_of_cycle, rng):
    n_cycles = 6
    cycle_period_s = 600.0          # one JV+SPO cycle every 10 min
    spo_duration_s = 480.0          # SPO phase length
    spo_dt = 10.0
    v = V_GRID
    cycles, spo_time, spo_j, spo_pce, spo_idx = [], [], [], [], []
    spo_clock = 0.0                 # accumulated SPO-only time (X-axis)

    for k in range(1, n_cycles + 1):
        t_cycle = (k - 1) * cycle_period_s
        cell = DummyCell(mode="ideal_stable", j_ph=j_ph_of_cycle(k),
                         j0=2e-12, n=1.45, rs=4.0, rsh=6000.0)
        j_rev = cell.scan(v, "reverse")
        j_fwd = cell.scan(v, "forward")
        Voc, Jsc, FF, PCE, _, _ = _jv_params(v, j_rev, ILLUM)
        idx_mp = int(np.argmax(v * j_rev))
        Vmpp, Jmpp = float(v[idx_mp]), float(j_rev[idx_mp])

        # SPO phase: hold Vmpp, small relaxation + noise.
        n_spo = int(spo_duration_s / spo_dt)
        for s in range(n_spo):
            jj = Jmpp * (1.0 - 0.01 * np.exp(-s * spo_dt / 120.0)) \
                + rng.normal(0, 0.02)
            spo_time.append(spo_clock)
            spo_j.append(jj)
            spo_pce.append(Vmpp * jj / ILLUM * 100.0)
            spo_idx.append(k)
            spo_clock += spo_dt

        cycles.append({
            "cycle": k, "time_s": t_cycle, "Vmpp": Vmpp, "Jmpp": Jmpp,
            "Voc": Voc, "Jsc": Jsc, "FF": FF, "PCE": PCE, "HI": None,
            "v_rev": v, "j_rev": j_rev, "v_fwd": v, "j_fwd": j_fwd,
        })

    total_min = (n_cycles * cycle_period_s) / 60.0
    fp = save_mpp_data(
        out_dir, name,
        cycles, spo_time, spo_j, spo_pce, spo_idx,
        AREA, ILLUM, 1.0, 20.0,
        spo_duration_s, total_min,
    )
    _rename(fp, out_dir, clean_name)


def gen_mpp_tracking(out_dir: str) -> None:
    # Stable device — flat MPP efficiency.
    _build_mpp(out_dir, "05_MPP_Tracking_stable", "05_MPP_Tracking_stable.xlsx",
               lambda k: 22.5, np.random.default_rng(51))
    # Burn-in device — efficiency rises over the first cycles, then plateaus.
    _build_mpp(out_dir, "05_MPP_Tracking_burnin", "05_MPP_Tracking_burnin.xlsx",
               lambda k: 22.5 * (1.0 - 0.15 * np.exp(-(k - 1) / 1.8)),
               np.random.default_rng(52))


# ── 6. Pure MPP tracking (P&O) ──────────────────────────────────────────────────

def gen_pure_mpp(out_dir: str) -> None:
    rng = np.random.default_rng(6)
    t = np.round(np.arange(0.0, 600.0 + 1e-9, 2.0), 3)      # s
    vmpp = 0.92 + 0.004 * np.sin(t / 7.0) + rng.normal(0, 0.001, t.size)  # dither
    j = 19.6 * (1.0 - 0.03 * (1.0 - np.exp(-t / 200.0))) + rng.normal(0, 0.03, t.size)
    pce = vmpp * j / ILLUM * 100.0
    fp = save_pure_mpp_data(
        out_dir, "06_PureMPP",
        t.tolist(), vmpp.tolist(), j.tolist(), pce.tolist(),
        AREA, ILLUM, 1.0, 20.0,
    )
    _rename(fp, out_dir, "06_PureMPP.xlsx")


# ── 7. ISOS-D-1 (manual-trigger dark storage) ──────────────────────────────────

def gen_isos_d1(out_dir: str) -> None:
    base = _real_datetime(2026, 1, 5, 9, 0, 0)
    virtual_now = [base]
    cfg = {
        "protocol_id": "ISOS-D-1",
        "sample_name": "07_ISOS_D1",
        "cell_area": AREA,
        "start_voltage": float(V_GRID[0]),
        "end_voltage": float(V_GRID[-1]),
        "step_voltage": 0.02,
        "delay_ms": 20.0, "nplc": 1.0, "four_wire": False,
        "prebias_enabled": False, "prebias_voltage": 0.0, "prebias_duration": 0.0,
        "light_soaking_enabled": False, "light_soaking_duration": 0.0,
        "dark_jv": False, "electrical_load": "Open-circuit (dark storage)",
        "scan_rate_mode": "Advanced", "target_scan_rate": "",
        "t0_timestamp": base.strftime("%Y-%m-%d %H:%M:%S"),
    }

    v = V_GRID
    elapsed_hours = [0, 24, 72, 168, 336, 504, 672]        # ~28 days of storage

    with patch("app.io.isos_tracking.datetime",
               _patched_isos_datetime(lambda: virtual_now[0])):
        path = create_isos_d1_tracking_file(out_dir, cfg)
        for elapsed_h in elapsed_hours:
            virtual_now[0] = base + timedelta(hours=elapsed_h)
            j_ph_t = 22.5 * (1.0 - 0.18 * (1.0 - np.exp(-elapsed_h / 250.0)))
            cell = DummyCell(mode="with_hysteresis", j_ph=j_ph_t, j0=2e-12,
                             n=1.45, rs=4.0, rsh=6000.0, hi_fraction=0.08,
                             rng=np.random.default_rng(70 + elapsed_h))
            j_rev = cell.scan(v, "reverse")
            j_fwd = cell.scan(v, "forward")
            Voc_r, Jsc_r, FF_r, PCE_r, Rs_r, Rsh_r = _jv_params(v, j_rev, ILLUM)
            Voc_f, Jsc_f, FF_f, PCE_f, Rs_f, Rsh_f = _jv_params(v, j_fwd, ILLUM)
            append_isos_measurement(path, {
                "v_fwd": v, "j_fwd": j_fwd, "v_rev": v, "j_rev": j_rev,
                "Voc_fwd": Voc_f, "Jsc_fwd": Jsc_f, "FF_fwd": FF_f,
                "PCE_fwd": PCE_f, "Rs_fwd": Rs_f, "Rsh_fwd": Rsh_f,
                "Voc_rev": Voc_r, "Jsc_rev": Jsc_r, "FF_rev": FF_r,
                "PCE_rev": PCE_r, "Rs_rev": Rs_r, "Rsh_rev": Rsh_r,
                "HI": calculate_hysteresis_index(PCE_f, PCE_r),
                "illumination": ILLUM, "scan_rate": 0.0,
                "cell_area": AREA, "delay_ms": 20.0, "nplc": 1.0,
                "four_wire": False, "instrument_idn": IDN,
                "notes": "Illuminated characterisation after dark storage",
            })

    _rename(path, out_dir, "07_ISOS_D1.xlsx")


# ── 8 & 9. ISOS-L-1 / V-1 (continuous, via the production runner) ───────────────

class _AgingFakeKeithley(FakeKeithley):
    """FakeKeithley whose illuminated photocurrent decays with the *virtual*
    clock, so a runner driven by a VirtualClock produces a degrading device."""

    def __init__(self, *args, clock=None, tau_h=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._clock = clock
        self._tau_s = float(tau_h) * 3600.0 if tau_h else None

    def query(self, cmd):
        if self._tau_s and self._clock is not None and self._illuminated:
            self.cell.set_illumination_factor(
                float(np.exp(-self._clock.time() / self._tau_s)))
        return super().query(cmd)


def _run_continuous_sample(cfg, tau_h, dest_name):
    """Run one continuous ISOS experiment in an isolated temp folder and copy the
    main tracking workbook into Samples/ under *dest_name*."""
    clock = cfg["clock"]
    base = _real_datetime(2026, 2, 1, 8, 0, 0)
    now_fn = lambda: base + timedelta(seconds=clock.time())

    cell = DummyCell(
        mode="exponential_decay" if tau_h else "ideal_stable",
        j_ph=22.5, j0=2e-12, n=1.45, rs=4.0, rsh=6000.0,
        rng=np.random.default_rng(81),
    )
    inst = _AgingFakeKeithley(cell=cell, area=cfg["cell_area"],
                              clock=clock, tau_h=tau_h)
    runner = ContinuousISOSRunner(inst, cfg, {}, threading.Event())

    with patch("app.instrument.keithley.time.sleep"), \
         patch("app.io.isos_tracking.datetime",
               _patched_isos_datetime(now_fn)):
        runner.run()

    _rename(runner._tracking_path, SAMPLES_DIR, dest_name)


def gen_isos_l1(work_dir: str) -> None:
    # Stable light-soak device (efficiency holds → metrics show "not reached").
    # scan_interval_min = 40 h → 15 illuminated J-V scans across the 600 h run.
    cfg = make_runner_config(
        work_dir, sample_name="08_ISOS_L1_stable", load_mode="MPP",
        total_duration_h=600.0, scan_interval_min=40.0 * 60.0,
        baseline_n_scans=2, spo_duration_s=15.0,
        pre_scan_relaxation_s=0.0, stress_log_interval_s=7200.0,
        clock=VirtualClock(),
    )
    _run_continuous_sample(cfg, tau_h=None, dest_name="08_ISOS_L1_stable.xlsx")

    # Degrading light-soak device — efficiency decays through T80.
    cfg = make_runner_config(
        work_dir, sample_name="08_ISOS_L1_degrading", load_mode="MPP",
        total_duration_h=600.0, scan_interval_min=40.0 * 60.0,
        baseline_n_scans=2, spo_duration_s=15.0,
        pre_scan_relaxation_s=0.0, stress_log_interval_s=7200.0,
        clock=VirtualClock(),
    )
    _run_continuous_sample(cfg, tau_h=1500.0,
                           dest_name="08_ISOS_L1_degrading.xlsx")


def gen_isos_v1(work_dir: str) -> None:
    # Dark electrical-bias stress with periodic dark + illuminated J-V.
    # scan_interval_min = 50 h → 12 scan cycles (dark + illuminated) over 600 h.
    cfg = make_v1_runner_config(
        work_dir, sample_name="09_ISOS_V1_degrading",
        total_duration_h=600.0, scan_interval_min=50.0 * 60.0,
        spo_duration_s=0.0, pre_scan_relaxation_s=0.0,
        stress_log_interval_s=7200.0,
        clock=VirtualClock(),
    )
    _run_continuous_sample(cfg, tau_h=1500.0,
                           dest_name="09_ISOS_V1_degrading.xlsx")


# ── orchestration ───────────────────────────────────────────────────────────────

# Single-workbook samples written directly into Samples/.
_SIMPLE_GENERATORS = [
    gen_illuminated_jv,
    gen_dark_jv,
    gen_light_intensity,
    gen_stabilized_current,
    gen_mpp_tracking,
    gen_pure_mpp,
    gen_isos_d1,
]

# Continuous-runner samples (each runs in an isolated temp folder to avoid the
# per-scan backup files and in-progress CSVs cluttering Samples/).
_RUNNER_GENERATORS = [
    gen_isos_l1,
    gen_isos_v1,
]


def main() -> None:
    os.makedirs(SAMPLES_DIR, exist_ok=True)

    for gen in _SIMPLE_GENERATORS:
        print(gen.__name__)
        gen(SAMPLES_DIR)

    for gen in _RUNNER_GENERATORS:
        print(gen.__name__)
        # The runner writes its tracking file + per-scan backups + autosave CSV
        # into its config folder; run it in a throwaway temp dir so only the
        # final tracking workbook (copied out by _run_continuous_sample) lands in
        # Samples/. ignore_cleanup_errors keeps a Windows AV file-lock during
        # rmtree from aborting the run.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
            gen(work)

    print("\nSamples written to", _display_path(SAMPLES_DIR))


if __name__ == "__main__":
    main()

