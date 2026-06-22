"""
Protocol-agnostic continuous-experiment runner for ISOS-L-1 and ISOS-V-1.

L-1 and V-1 share one ``stress → scan → stress`` loop. They differ only in:
  * L-1 — ``stress_with_light=True``, no shutter prompts.
  * V-1 — ``stress_with_light=False``, shutter prompts at each scan and an
    optional dark J-V scan.

This runner drives the raw-SCPI instrument interface (``inst.write`` /
``inst.query``), reusing the helpers in ``app.instrument.keithley`` exactly like
``MPPTracker`` / ``PureMPPTracker`` and the ISOS-D-1 routine do. The MPP stress
phase is implemented inline (perturb-and-observe for L-1, hold-last for V-1)
rather than delegating to the tracker classes, because those own their full
output-ON/OFF lifecycle and an internal initial scan that does not compose with
sub-interval stress segments.

Time is read through an injectable clock so tests can run deterministically and
instantly. The default clock wraps ``time.time`` / ``time.sleep``.

Durability: every stress sample is streamed to a fsync'd CSV via
``CSVStream``. On completion (or any failure) the CSV is consolidated into the
tracking workbook and the instrument output is turned off. The user must never
lose data — every loop body is wrapped so a mid-experiment exception still
flushes and consolidates whatever was collected.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime

import numpy as np

from app.instrument.keithley import (
    init_instrument,
    measure_scan,
    apply_prebias,
    apply_light_soaking,
    configure_oc_mode,
    read_voc_now,
)
from app.measurement.analysis import (
    calculate_jv_parameters,
    calculate_resistances,
    calculate_hysteresis_index,
    fit_dark_ideality,
)
from app.io.isos_tracking import (
    create_isos_tracking_file,
    append_isos_measurement,
    append_curve_sheet,
    append_dark_jv_sheet,
    append_log_sheet_row,
)
from app.measurement.stability_metrics import compute_stability_metrics
from app.io.isos_autosave import CSVStream, consolidate_csv_into_xlsx

try:  # exporter is xlsxwriter-based and GUI-independent
    from app.io.exporter import save_data, save_dark_jv_data
except Exception:  # pragma: no cover - exporter import is best-effort
    save_data = None
    save_dark_jv_data = None


_STRESS_CSV_COLUMNS = ["elapsed_h", "t_s", "v", "j", "pce", "phase"]


class _RealClock:
    """Default clock — thin wrapper around the wall clock."""

    @staticmethod
    def time() -> float:
        return time.time()

    @staticmethod
    def sleep(seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class ContinuousISOSRunner:
    """Runs a continuous ISOS stress experiment (L-1 / V-1).

    See module docstring for the config / callback contract. ``config`` keys are
    read lazily with sensible defaults so partial configs still run.
    """

    def __init__(self, instrument, config, callbacks, stop_event):
        self.inst       = instrument
        self.cfg        = dict(config or {})
        self.callbacks  = dict(callbacks or {})
        self.stop_event = stop_event

        # Injectable clock (tests pass a virtual clock); default = wall clock.
        self._clock = self.cfg.get("clock") or _RealClock()

        # ── Runtime state ──────────────────────────────────────────────────
        self.t0              = None    # experiment start (clock seconds)
        self._tracking_path  = self.cfg.get("tracking_file_path")
        self._csv            = None
        self._csv_path       = None
        self.baseline        = None    # dict from run_baseline_conditioning
        self.pce_0           = None    # baseline median PCE for normalization
        self._baseline_idark = None    # baseline dark current for failure test
        self.last_vmpp       = None
        self.last_voc        = None
        self.scan_count      = 0
        self.i_dark_trace    = []      # [(elapsed_s, j)] for V-1 plotting
        self._idark_mark     = 0       # index into i_dark_trace at last scan
        # Cumulative canonical PCE(t) series for the stability metrics.
        self._metric_times   = []      # elapsed_h per scan
        self._metric_pce     = []      # canonical PCE per scan
        self._metric_pce_col = "PCE_SPO"
        self.last_metrics    = None    # latest StabilityMetrics

    # ── Callback / timing helpers ──────────────────────────────────────────

    def _emit(self, name, *args):
        cb = self.callbacks.get(name)
        if callable(cb):
            try:
                return cb(*args)
            except Exception:
                pass
        return None

    def _status(self, msg):
        self._emit("on_status", msg)

    def _status_cb(self, msg):
        # Adapter for keithley helpers that expect a callable(str).
        self._status(msg)

    def _elapsed_s(self):
        return self._clock.time() - self.t0

    def _elapsed_h(self):
        return self._elapsed_s() / 3600.0

    def _sleep_interruptible(self, seconds):
        """Sleep up to *seconds*, polling stop_event in <=50 ms chunks."""
        if seconds <= 0:
            return
        end = self._clock.time() + seconds
        while not self.stop_event.is_set():
            remaining = end - self._clock.time()
            if remaining <= 1e-9:
                break
            self._clock.sleep(min(0.05, remaining))

    # ── Public entry point ──────────────────────────────────────────────────

    def run(self):
        self.t0 = self._clock.time()
        try:
            self._ensure_tracking_file()
            self._open_csv()
            self._status("ISOS experiment started.")

            # 1. Optional dark equilibration ───────────────────────────────
            eq_h = float(self.cfg.get("equilibration_duration_h", 0) or 0)
            if eq_h > 0 and not self.stop_event.is_set():
                self.apply_dark_equilibration(eq_h)

            # 2. Baseline conditioning ─────────────────────────────────────
            n_base = int(self.cfg.get("baseline_n_scans", 0) or 0)
            if n_base > 0 and not self.stop_event.is_set():
                self.baseline = self.run_baseline_conditioning(
                    n_base, float(self.cfg.get("baseline_rest_s", 60)))
                self.pce_0 = self.baseline.get("PCE_0_median")
                self._emit("on_baseline_done", self.baseline)

            # 3. Main stress/scan loop ─────────────────────────────────────
            total_h    = float(self.cfg.get("total_duration_h", 0) or 0)
            auto_stop  = bool(self.cfg.get("auto_stop_on_failure", False))
            with_light = bool(self.cfg.get("stress_with_light", True))

            while not self.stop_event.is_set() and self._elapsed_h() < total_h - 1e-9:
                next_h = self.next_scan_time(self._elapsed_h())
                self._apply_stress_until(next_h)
                if self.stop_event.is_set():
                    break

                self.pre_scan_relaxation(
                    float(self.cfg.get("pre_scan_relaxation_s", 60.0)))
                if self.stop_event.is_set():
                    break

                meas_dark = None
                if self.cfg.get("requires_dark_jv"):
                    meas_dark = self._perform_dark_jv_scan()
                    if self.stop_event.is_set():
                        break

                if not with_light:
                    self._prompt_open_shutter()

                meas = self._perform_illuminated_jv_scan()
                if meas is None or self.stop_event.is_set():
                    break

                spo = self.perform_spo_after_scan(
                    meas.get("Vmpp"),
                    float(self.cfg.get("spo_duration_s", 20.0)))

                if not with_light:
                    self._prompt_close_shutter()

                metrics = self._update_stability_metrics(meas, spo)
                if metrics is not None:
                    meas["stability_metrics"] = metrics

                self._merge_dark(meas, meas_dark)
                self._append_to_tracking_file(meas, meas_dark, spo)
                self.scan_count += 1
                self._emit("on_scan_done", meas)
                if metrics is not None:
                    self._emit("on_metrics_updated", metrics)

                failed, reason = self._check_failure(meas)
                if failed:
                    self._emit("on_failure_detected", reason)
                    if auto_stop:
                        break

        except Exception as exc:  # never lose data on an unexpected error
            self._emit("on_log", f"Experiment error: {exc}")
        finally:
            self._cleanup()

    # ── Shared helpers (per session-3 spec) ─────────────────────────────────

    def run_baseline_conditioning(self, n_scans=4, rest_s=60) -> dict:
        """Run *n_scans* illuminated J-V scans with OC-dark rests between them.

        Discards scan #1 (first-scan artifact) and returns the median PCE_rev of
        the remaining scans. Each scan is saved as a ``Baseline_#NN`` sheet.
        """
        scans = []
        for i in range(1, int(n_scans) + 1):
            if self.stop_event.is_set():
                break
            self._status(f"Baseline conditioning scan {i}/{n_scans}…")
            meas = self._perform_illuminated_jv_scan(save_backup=False)
            if meas is None:
                break
            scans.append(meas)
            self._save_baseline_sheet(i, meas)
            if i < n_scans and not self.stop_event.is_set():
                self._oc_dark_rest(rest_s)

        used = scans[1:] if len(scans) > 1 else scans     # discard first
        pces = [m.get("PCE_rev", 0.0) for m in used]
        median = float(np.median(pces)) if pces else 0.0
        std    = float(np.std(pces)) if pces else 0.0
        return {
            "PCE_0_median": median,
            "PCE_0_std":    std,
            "all_scans":    scans,
            "method":       "median_of_N-1",
        }

    def apply_dark_equilibration(self, duration_h):
        """Hold the cell at OC in the dark for *duration_h* hours.

        Checks ``stop_event`` every 10 s and records a row to the
        ``Equilibration_log`` sheet on completion (or interruption).
        """
        self._status(f"Dark equilibration — {duration_h:.2f} h…")
        configure_oc_mode(self.inst, float(self.cfg.get("nplc", 1.0)))
        target_s = duration_h * 3600.0
        start_s  = self._elapsed_s()
        while not self.stop_event.is_set() and (self._elapsed_s() - start_s) < target_s - 1e-9:
            self._sleep_interruptible(10.0)
        actual_h = (self._elapsed_s() - start_s) / 3600.0
        if self._tracking_path:
            try:
                append_log_sheet_row(
                    self._tracking_path, "Equilibration_log",
                    ["Timestamp", "Planned_h", "Actual_h", "Voc_end (V)"],
                    [datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     round(duration_h, 4), round(actual_h, 4),
                     self._safe_read_voc()],
                )
            except Exception:
                pass

    def pre_scan_relaxation(self, relaxation_s=60.0):
        """Switch to OC mode and rest for *relaxation_s* before a J-V scan."""
        if relaxation_s <= 0:
            return
        self._status(f"Pre-scan relaxation — {relaxation_s:.0f} s at OC…")
        try:
            configure_oc_mode(self.inst, float(self.cfg.get("nplc", 1.0)))
        except Exception:
            pass
        self._sleep_interruptible(relaxation_s)

    def perform_spo_after_scan(self, vmpp, duration_s=20.0) -> dict:
        """Hold at *vmpp* for *duration_s*, sampling J at 5 Hz.

        Returns ``{PCE_SPO, PCE_SPO_std, time_s, pce_trace}`` where ``PCE_SPO`` is
        the mean of the final third of the trace. Persists the trace as an
        ``SPO_#NNN_tXXXXh`` sheet.
        """
        empty = {"PCE_SPO": None, "PCE_SPO_std": None,
                 "time_s": [], "pce_trace": []}
        if vmpp is None or duration_s <= 0 or self.stop_event.is_set():
            return empty

        area = float(self.cfg.get("cell_area", 0.07))
        pin  = float(self.cfg.get("pin", 0) or 0)
        try:
            self.inst.write(f":SOUR:VOLT {vmpp:.4f}")
        except Exception:
            return empty
        self._clock.sleep(0.1)   # settle

        t_trace, pce_trace = [], []
        start = self._clock.time()
        while not self.stop_event.is_set() and (self._clock.time() - start) < duration_s - 1e-9:
            try:
                raw = self.inst.query(":READ?").split(",")
                j   = -(float(raw[1]) * 1000.0) / area
            except Exception:
                break
            pce = (vmpp * j) / pin * 100.0 if pin > 0 else 0.0
            t_trace.append(round(self._clock.time() - start, 3))
            pce_trace.append(pce)
            self._sleep_interruptible(0.2)   # ~5 Hz

        if not pce_trace:
            return empty
        tail = pce_trace[max(1, len(pce_trace) * 2 // 3) - 1:] or pce_trace
        pce_spo     = float(np.mean(tail))
        pce_spo_std = float(np.std(tail))

        if self._tracking_path:
            try:
                elapsed_h = self._elapsed_h()
                sheet = f"SPO_#{self.scan_count + 1:03d}_t{int(elapsed_h):04d}h"
                append_curve_sheet(self._tracking_path, sheet, {
                    "Time (s)":  t_trace,
                    "PCE (%)":   pce_trace,
                })
            except Exception:
                pass

        return {
            "PCE_SPO":     pce_spo,
            "PCE_SPO_std": pce_spo_std,
            "time_s":      t_trace,
            "pce_trace":   pce_trace,
        }

    def next_scan_time(self, elapsed_h):
        """Return the next absolute scan time (h) from a tiered schedule or a
        constant interval."""
        sched = self.cfg.get("tiered_schedule")
        if sched:
            for tier in sched:
                start_h, end_h, interval_min = tier
                if start_h <= elapsed_h < end_h:
                    return elapsed_h + interval_min / 60.0
            # Past every tier — keep using the last tier's interval.
            return elapsed_h + sched[-1][2] / 60.0
        interval_min = float(self.cfg.get("scan_interval_min", 60.0))
        return elapsed_h + interval_min / 60.0

    def _check_failure(self, meas):
        """Return ``(failed, reason)`` if any configured threshold is crossed.

        Thresholds (all optional) live in ``config['failure_thresholds']``:
          * ``pce_norm``   — PCE_rev / PCE_0 below this ratio.
          * ``idark_ratio``— |I_dark| above ``baseline_idark * idark_ratio``.
          * ``rsh_dark``   — dark reverse shunt resistance below this value.
        """
        th = self.cfg.get("failure_thresholds") or {}
        reasons = []

        pce_th = th.get("pce_norm")
        if pce_th is not None and self.pce_0:
            norm = meas.get("PCE_rev", 0.0) / self.pce_0 if self.pce_0 else 1.0
            if norm < pce_th:
                reasons.append(f"PCE_norm {norm:.3f} < {pce_th}")

        idark_th = th.get("idark_ratio")
        idark = meas.get("i_dark")
        if idark_th is not None and self._baseline_idark and idark is not None:
            if abs(idark) > abs(self._baseline_idark) * idark_th:
                reasons.append(
                    f"I_dark {idark:.3e} > {idark_th}× baseline")

        rsh_th = th.get("rsh_dark")
        rsh = meas.get("r_sh_dark_rev")
        if rsh_th is not None and rsh is not None:
            if rsh < rsh_th:
                reasons.append(f"R_sh_dark {rsh:.1f} < {rsh_th}")

        failed = bool(reasons)
        reason = "; ".join(reasons) if reasons else None
        meas["failure_detected"] = failed
        meas["failure_reason"]   = reason
        return failed, reason

    # ── Stability metrics ────────────────────────────────────────────────────

    def _update_stability_metrics(self, meas, spo):
        """Append this scan's canonical PCE and recompute stability metrics.

        Canonical PCE prefers the stabilized power output (``PCE_SPO``) and falls
        back to the reverse-scan ``PCE_rev`` when SPO is unavailable. Returns the
        latest :class:`StabilityMetrics`, or ``None`` if no PCE is available.
        Runs entirely in the runner thread — never the GUI thread.
        """
        spo_pce = spo.get("PCE_SPO") if spo else None
        if spo_pce is not None:
            pce_val, col = float(spo_pce), "PCE_SPO"
        elif meas.get("PCE_rev") is not None:
            pce_val, col = float(meas["PCE_rev"]), "PCE_rev"
        else:
            return None

        self._metric_times.append(self._elapsed_h())
        self._metric_pce.append(pce_val)
        self._metric_pce_col = col
        return self._compute_metrics()

    def _compute_metrics(self):
        """Recompute StabilityMetrics from the cumulative series (thread-safe).

        Snapshots the parallel series to a common length so a concurrent append
        from the runner thread cannot mismatch them mid-call.
        """
        n = min(len(self._metric_times), len(self._metric_pce))
        if n == 0:
            return None
        times = list(self._metric_times[:n])
        pce   = list(self._metric_pce[:n])
        try:
            metrics = compute_stability_metrics(
                times, pce, self.pce_0,
                burn_in_mode=self.cfg.get("burn_in_mode", "auto"),
                manual_burn_in_h=self.cfg.get("manual_burn_in_h"),
                extrapolate_n1000=bool(self.cfg.get("extrapolate_n1000", True)),
                pce_column_used=self._metric_pce_col,
            )
        except Exception as exc:
            self._emit("on_log", f"Stability metrics failed: {exc}")
            return None
        self.last_metrics = metrics
        return metrics

    def recompute_metrics(self, burn_in_mode=None, manual_burn_in_h=None):
        """Recompute metrics after a live burn-in-mode change (GUI thread).

        Updates the runner config so subsequent scans use the new settings, then
        returns the freshly computed :class:`StabilityMetrics` (or ``None`` when
        no scans have been recorded yet).
        """
        if burn_in_mode is not None:
            self.cfg["burn_in_mode"] = burn_in_mode
        if manual_burn_in_h is not None:
            self.cfg["manual_burn_in_h"] = manual_burn_in_h
        return self._compute_metrics()

    # ── Stress phase ─────────────────────────────────────────────────────────

    def _apply_stress_until(self, target_h):
        """Apply the configured stress until elapsed time reaches *target_h*."""
        load       = self.cfg.get("load_mode", "OC")
        with_light = bool(self.cfg.get("stress_with_light", True))
        interval_s = max(0.0, float(self.cfg.get("stress_log_interval_s", 2.0)))
        target_s   = target_h * 3600.0
        try:
            if with_light and load == "MPP":
                self._stress_loop_mpp_po(target_s, interval_s)
            elif with_light:        # L-1 OC
                configure_oc_mode(self.inst, float(self.cfg.get("nplc", 1.0)))
                self._stress_loop_oc(target_s, interval_s)
            else:                   # V-1 dark hold (OC or MPP setpoint)
                self._stress_loop_dark_hold(target_s, interval_s, load)
        except Exception as exc:
            self._emit("on_log", f"Stress phase error: {exc}")

    def _stress_loop_oc(self, target_s, interval_s):
        while not self.stop_event.is_set() and self._elapsed_s() < target_s - 1e-9:
            v = read_voc_now(self.inst)
            self.last_voc = v
            self._record_stress(v, 0.0, 0.0, "stress_oc")
            self._sleep_interruptible(interval_s)

    def _stress_loop_mpp_po(self, target_s, interval_s):
        c    = self.cfg
        area = float(c.get("cell_area", 0.07))
        pin  = float(c.get("pin", 0) or 0)
        step_v = float(c.get("perturb_step_mv", 10.0)) / 1000.0
        v_min  = float(c.get("v_min", 0.0))
        v_max  = float(c.get("v_max", c.get("end_v", 1.2)))

        init_instrument(self.inst, float(c.get("nplc", 1.0)),
                        float(c.get("delay_ms", 20)) / 1000.0,
                        bool(c.get("four_wire", False)))
        v = self.last_vmpp if self.last_vmpp is not None else v_max * 0.8
        v = max(v_min, min(v_max, v))
        self.inst.write(f":SOUR:VOLT {v:.4f}")

        p_prev, direction = None, 1
        while not self.stop_event.is_set() and self._elapsed_s() < target_s - 1e-9:
            raw = self.inst.query(":READ?").split(",")
            j   = -(float(raw[1]) * 1000.0) / area
            p   = v * j
            pce = p / pin * 100.0 if pin > 0 else 0.0
            self._record_stress(v, j, pce, "stress_mpp")

            if p_prev is not None and p < p_prev:
                direction = -direction
            p_prev = p
            v = max(v_min, min(v_max, v + direction * step_v))
            self.inst.write(f":SOUR:VOLT {v:.4f}")
            self.last_vmpp = v
            self._sleep_interruptible(interval_s)

    def _stress_loop_dark_hold(self, target_s, interval_s, load):
        c    = self.cfg
        area = float(c.get("cell_area", 0.07))
        setpoint = self.last_voc if load == "OC" else self.last_vmpp
        if setpoint is None:
            setpoint = float(c.get("setpoint_v", 0.0) or 0.0)

        init_instrument(self.inst, float(c.get("nplc", 1.0)),
                        float(c.get("delay_ms", 20)) / 1000.0,
                        bool(c.get("four_wire", False)))
        self.inst.write(f":SOUR:VOLT {setpoint:.4f}")
        log_idark = bool(c.get("log_i_dark_during_stress", True))

        while not self.stop_event.is_set() and self._elapsed_s() < target_s - 1e-9:
            j = 0.0
            if log_idark:
                try:
                    raw = self.inst.query(":READ?").split(",")
                    j   = -(float(raw[1]) * 1000.0) / area
                except Exception:
                    j = 0.0
                self.i_dark_trace.append((self._elapsed_s(), j))
            self._record_stress(setpoint, j, 0.0, "stress_dark")
            self._sleep_interruptible(interval_s)

    def _record_stress(self, v, j, pce, phase):
        t_s = self._elapsed_s()
        if self._csv is not None:
            try:
                self._csv.append({
                    "elapsed_h": round(t_s / 3600.0, 6),
                    "t_s":       round(t_s, 3),
                    "v":         v, "j": j, "pce": pce, "phase": phase,
                })
            except Exception:
                pass
        self._emit("on_stress_update", t_s, v, j, pce)

    # ── Shutter prompts ──────────────────────────────────────────────────────

    def _prompt_open_shutter(self):
        self._status("Open the shutter for the illuminated scan…")
        self._emit("on_shutter", "open")

    def _prompt_close_shutter(self):
        self._status("Close the shutter — resuming dark stress…")
        self._emit("on_shutter", "close")

    # ── J-V scans ──────────────────────────────────────────────────────────

    def _perform_illuminated_jv_scan(self, save_backup=True):
        c = self.cfg
        area = float(c.get("cell_area", 0.07))
        pin  = float(c.get("pin", 0) or 0)
        init_instrument(self.inst, float(c.get("nplc", 1.0)),
                        float(c.get("delay_ms", 20)) / 1000.0,
                        bool(c.get("four_wire", False)))

        if c.get("light_soaking_enabled"):
            apply_light_soaking(self.inst,
                                float(c.get("light_soaking_duration", 0) or 0),
                                self.stop_event, self._status_cb)
            if self.stop_event.is_set():
                return None

        self._status("Illuminated forward scan…")
        v_f, j_f = measure_scan(self.inst, c["start_v"], c["end_v"], c["step_v"],
                                area, self.stop_event)
        if v_f is None or self.stop_event.is_set():
            return None

        if c.get("prebias_enabled"):
            apply_prebias(self.inst, float(c.get("prebias_v", 0)),
                          float(c.get("prebias_t", 0)),
                          self.stop_event, self._status_cb)
            if self.stop_event.is_set():
                return None

        self._status("Illuminated reverse scan…")
        v_r, j_r = measure_scan(self.inst, c["end_v"], c["start_v"], -c["step_v"],
                                area, self.stop_event)
        if v_r is None or self.stop_event.is_set():
            return None
        try:
            self.inst.write(":OUTP OFF")
        except Exception:
            pass

        Voc_f, Jsc_f, FF_f, PCE_f = calculate_jv_parameters(v_f, j_f, pin)
        Voc_r, Jsc_r, FF_r, PCE_r = calculate_jv_parameters(v_r, j_r, pin)
        Rs_f,  Rsh_f = calculate_resistances(v_f, j_f, Voc_f)
        Rs_r,  Rsh_r = calculate_resistances(v_r, j_r, Voc_r)
        hi   = calculate_hysteresis_index(PCE_f, PCE_r)
        Vmpp = self._vmpp(v_r, j_r)
        self.last_vmpp = Vmpp
        # Latest open-circuit voltage — the V-1 dark-hold setpoint when load=OC.
        if np.isfinite(Voc_r):
            self.last_voc = float(Voc_r)
        scan_rate = self._scan_rate()

        meas = {
            "v_fwd": v_f, "j_fwd": j_f, "v_rev": v_r, "j_rev": j_r,
            "Voc_fwd": Voc_f, "Jsc_fwd": Jsc_f, "FF_fwd": FF_f, "PCE_fwd": PCE_f,
            "Rs_fwd": Rs_f, "Rsh_fwd": Rsh_f,
            "Voc_rev": Voc_r, "Jsc_rev": Jsc_r, "FF_rev": FF_r, "PCE_rev": PCE_r,
            "Rs_rev": Rs_r, "Rsh_rev": Rsh_r,
            "HI": hi if np.isfinite(hi) else 0.0,
            "Vmpp": Vmpp,
            "illumination": pin, "scan_rate": scan_rate,
            "cell_area": area,
            "delay_ms": c.get("delay_ms"), "nplc": c.get("nplc"),
            "four_wire": c.get("four_wire"),
            "prebias_enabled": c.get("prebias_enabled"),
            "prebias_voltage": c.get("prebias_v"),
            "prebias_duration": c.get("prebias_t"),
            "instrument_idn": c.get("instrument_idn", ""),
        }

        if save_backup and save_data is not None:
            try:
                save_data(
                    c.get("folder", "."), c.get("sample_name", "Sample"),
                    v_f, j_f, v_r, j_r, pin,
                    Voc_f, Jsc_f, FF_f, PCE_f, Rs_f, Rsh_f,
                    Voc_r, Jsc_r, FF_r, PCE_r, Rs_r, Rsh_r, hi,
                    area, c.get("delay_ms", 20), c.get("nplc", 1.0),
                    c.get("four_wire", False),
                    c.get("prebias_enabled", False),
                    c.get("prebias_v", 0), c.get("prebias_t", 0),
                    c.get("instrument_idn", ""),
                    scan_rate=scan_rate,
                )
            except Exception as exc:
                self._emit("on_log", f"Backup save failed: {exc}")
        return meas

    def _perform_dark_jv_scan(self):
        c = self.cfg
        area = float(c.get("cell_area", 0.07))
        # Dark scans use their own (typically wider) voltage range so the
        # exponential diode region is well sampled. Falls back to the
        # illuminated range when the dark range is not configured.
        start = float(c.get("dark_start_v", c["start_v"]))
        end   = float(c.get("dark_end_v",   c["end_v"]))
        step  = abs(float(c.get("dark_step_v", c["step_v"])))
        init_instrument(self.inst, float(c.get("nplc", 1.0)),
                        float(c.get("delay_ms", 20)) / 1000.0,
                        bool(c.get("four_wire", False)))
        self._status("Dark forward scan…")
        v_f, j_f = measure_scan(self.inst, start, end, step, area, self.stop_event)
        if v_f is None or self.stop_event.is_set():
            return None
        self._status("Dark reverse scan…")
        v_r, j_r = measure_scan(self.inst, end, start, -step, area, self.stop_event)
        if v_r is None or self.stop_event.is_set():
            return None
        try:
            self.inst.write(":SOUR:VOLT 0")
            self.inst.write(":OUTP OFF")
        except Exception:
            pass

        # Dark Rsh near 0 V; Rs from the high-forward-bias slope (Voc=None).
        Rs_dark_f, Rsh_dark_f = calculate_resistances(v_f, j_f, None)
        Rs_dark_r, Rsh_dark_r = calculate_resistances(v_r, j_r, None)

        # Dark ideality factor + saturation current from the reverse scan.
        v_min = float(c.get("dark_fit_v_min", 0.5))
        v_max = float(c.get("dark_fit_v_max", 0.8))
        T_k   = float(c.get("temperature_k", 298.15))
        n_dark = j_s_dark = n_dark_r2 = None
        try:
            fit = fit_dark_ideality(v_r, j_r, v_min, v_max, T=T_k)
            n_dark, j_s_dark, n_dark_r2 = fit.n_idd, fit.J_s, fit.r_squared
        except Exception as exc:
            self._emit("on_log", f"Dark ideality fit failed: {exc}")

        # Dark "hysteresis" — fwd vs rev current at the highest forward bias.
        i_dark_f = float(j_f[int(np.argmax(v_f))]) if len(v_f) else None
        i_dark_r = float(j_r[int(np.argmax(v_r))]) if len(v_r) else None
        i_dark   = i_dark_r
        hi_dark  = None
        if i_dark_f is not None and i_dark_r:
            hd = calculate_hysteresis_index(i_dark_f, i_dark_r)
            hi_dark = float(hd) if np.isfinite(hd) else None

        meas_dark = {
            "v_fwd": v_f, "j_fwd": j_f, "v_rev": v_r, "j_rev": j_r,
            "r_s_dark_fwd": Rs_dark_f, "r_sh_dark_fwd": Rsh_dark_f,
            "r_s_dark_rev": Rs_dark_r, "r_sh_dark_rev": Rsh_dark_r,
            "n_dark_rev": n_dark, "j_s_dark_rev": j_s_dark,
            "n_dark_rev_r2": n_dark_r2,
            "hi_dark": hi_dark, "i_dark": i_dark,
        }

        # Per-cycle dark JV sheet (JV_Dark_#NNN_tXXXXh), MeasurementData-compatible.
        if self._tracking_path:
            try:
                elapsed_h = self._elapsed_h()
                m = self.scan_count + 1
                sheet = f"JV_Dark_#{m:03d}_t{int(elapsed_h):04d}h"
                append_dark_jv_sheet(self._tracking_path, sheet, meas_dark, {
                    "cell_area": area,
                    "delay_ms":  c.get("delay_ms"),
                    "nplc":      c.get("nplc"),
                    "four_wire": c.get("four_wire"),
                    "instrument_idn": c.get("instrument_idn", ""),
                    "elapsed_h": elapsed_h,
                    "measurement_n": m,
                })
            except Exception as exc:
                self._emit("on_log", f"Dark JV sheet write failed: {exc}")

        if save_dark_jv_data is not None:
            try:
                save_dark_jv_data(
                    c.get("folder", "."), c.get("sample_name", "Sample"),
                    v_f, j_f, v_r, j_r,
                    area, c.get("delay_ms", 20), c.get("nplc", 1.0),
                    c.get("four_wire", False), c.get("instrument_idn", ""),
                )
            except Exception as exc:
                self._emit("on_log", f"Dark backup save failed: {exc}")

        return meas_dark

    # ── Persistence helpers ──────────────────────────────────────────────────

    def _ensure_tracking_file(self):
        if self._tracking_path:
            return
        folder = self.cfg.get("folder", ".")
        self._tracking_path = create_isos_tracking_file(
            folder, self._build_file_config())

    def _build_file_config(self):
        c = self.cfg
        sched = c.get("tiered_schedule")
        return {
            "protocol_id":            c.get("protocol_id", "ISOS-L-1"),
            "sample_name":            c.get("sample_name", "Sample"),
            "cell_area":              c.get("cell_area"),
            "detector_power":         c.get("detector_power"),
            "detector_area":          c.get("detector_area"),
            "start_voltage":          c.get("start_v"),
            "end_voltage":            c.get("end_v"),
            "step_voltage":           c.get("step_v"),
            "delay_ms":               c.get("delay_ms"),
            "nplc":                   c.get("nplc"),
            "four_wire":              c.get("four_wire"),
            "prebias_enabled":        c.get("prebias_enabled"),
            "prebias_voltage":        c.get("prebias_v"),
            "prebias_duration":       c.get("prebias_t"),
            "light_soaking_enabled":  c.get("light_soaking_enabled"),
            "light_soaking_duration": c.get("light_soaking_duration"),
            "dark_jv":                c.get("requires_dark_jv"),
            "electrical_load":        c.get("load_mode"),
            "scan_rate_mode":         c.get("scan_rate_mode", "advanced"),
            "target_scan_rate":       c.get("target_scan_rate", 0),
            "equilibration_duration_h": c.get("equilibration_duration_h"),
            "baseline_n_scans":       c.get("baseline_n_scans"),
            "baseline_rest_s":        c.get("baseline_rest_s"),
            "tiered_schedule_json":   json.dumps(sched) if sched else "",
            "stress_with_light":      bool(c.get("stress_with_light", True)),
            "schedule_mode":          c.get("schedule_mode",
                                            "tiered" if sched else "fixed"),
            "log_i_dark_during_stress": c.get("log_i_dark_during_stress"),
            "dark_start_voltage":     c.get("dark_start_v"),
            "dark_end_voltage":       c.get("dark_end_v"),
            "dark_step_voltage":      c.get("dark_step_v"),
            "dark_fit_v_min":         c.get("dark_fit_v_min"),
            "dark_fit_v_max":         c.get("dark_fit_v_max"),
        }

    def _open_csv(self):
        folder = self.cfg.get("folder", ".")
        name   = self.cfg.get("sample_name", "Sample") or "Sample"
        ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._csv_path = os.path.join(
            folder, f"{name}_stress_inprogress_{ts}.csv")
        try:
            self._csv = CSVStream(self._csv_path, _STRESS_CSV_COLUMNS).open()
        except Exception as exc:
            self._csv = None
            self._emit("on_log", f"Could not open autosave CSV: {exc}")

    def _append_to_tracking_file(self, meas, meas_dark, spo):
        if not self._tracking_path:
            return
        merged = dict(meas)
        merged.update({
            "load":              self.cfg.get("load_mode"),
            "setpoint_v":        self.last_vmpp if self.cfg.get("load_mode") == "MPP"
                                 else self.last_voc,
            "mpp_engine":        self.cfg.get("mpp_engine"),
            "stress_with_light": self.cfg.get("stress_with_light"),
            "stress_elapsed_h":  round(self._elapsed_h(), 4),
            "pre_scan_relaxation_s": self.cfg.get("pre_scan_relaxation_s"),
            "spo_duration_s":    self.cfg.get("spo_duration_s"),
        })
        if self.baseline:
            merged["pce_0_baseline_median"] = self.baseline.get("PCE_0_median")
            merged["pce_0_baseline_std"]    = self.baseline.get("PCE_0_std")
            merged["baseline_method"]       = self.baseline.get("method")
        if spo:
            merged["pce_spo"]     = spo.get("PCE_SPO")
            merged["pce_spo_std"] = spo.get("PCE_SPO_std")
        # Stress setpoints (dark hold target for V-1; informational for L-1).
        merged["voc_setpoint"]  = self.last_voc
        merged["vmpp_setpoint"] = self.last_vmpp
        # Mean dark current sampled during the stress segment since the last scan.
        idark_mean = self._mean_idark_since_last_scan()
        if idark_mean is not None:
            merged["i_dark_during_stress_mean"] = idark_mean
        if meas_dark:
            for k in ("r_s_dark_fwd", "r_sh_dark_fwd",
                      "r_s_dark_rev", "r_sh_dark_rev",
                      "n_dark_rev", "j_s_dark_rev", "n_dark_rev_r2",
                      "hi_dark"):
                merged[k] = meas_dark.get(k)
        try:
            append_isos_measurement(self._tracking_path, merged)
        except Exception as exc:
            self._emit("on_log", f"Tracking append failed: {exc}")

    def _save_baseline_sheet(self, idx, meas):
        if not self._tracking_path:
            return
        try:
            append_curve_sheet(self._tracking_path, f"Baseline_#{idx:02d}", {
                "Forward Voltage (V)":  list(meas["v_fwd"]),
                "Forward J (mA/cm²)":   list(meas["j_fwd"]),
                "Reverse Voltage (V)":  list(meas["v_rev"]),
                "Reverse J (mA/cm²)":   list(meas["j_rev"]),
                "PCE_rev (%)":          [meas.get("PCE_rev")],
            })
        except Exception:
            pass

    # ── Misc helpers ──────────────────────────────────────────────────────────

    def _oc_dark_rest(self, rest_s):
        try:
            configure_oc_mode(self.inst, float(self.cfg.get("nplc", 1.0)))
        except Exception:
            pass
        self._sleep_interruptible(rest_s)

    def _merge_dark(self, meas, meas_dark):
        if meas_dark:
            for k in ("r_s_dark_fwd", "r_sh_dark_fwd",
                      "r_s_dark_rev", "r_sh_dark_rev",
                      "n_dark_rev", "j_s_dark_rev", "n_dark_rev_r2",
                      "hi_dark", "i_dark"):
                if k in meas_dark:
                    meas[k] = meas_dark[k]
            # Dark J-V arrays under suffixed keys so the V-1 panel can plot the
            # latest dark curve (ignored by the illuminated JV-sheet writer).
            for src, dst in (("v_fwd", "v_fwd_dark"), ("j_fwd", "j_fwd_dark"),
                             ("v_rev", "v_rev_dark"), ("j_rev", "j_rev_dark")):
                if src in meas_dark:
                    meas[dst] = meas_dark[src]

    def _mean_idark_since_last_scan(self):
        """Mean |J| of dark-stress samples collected since the previous scan.

        Returns None when no samples were logged (e.g. L-1, or I_dark logging
        disabled). Advances the trace marker so each segment is counted once.
        """
        trace = self.i_dark_trace
        seg = trace[self._idark_mark:]
        self._idark_mark = len(trace)
        if not seg:
            return None
        return float(np.mean([j for _, j in seg]))

    def _safe_read_voc(self):
        try:
            return round(read_voc_now(self.inst), 4)
        except Exception:
            return None

    def _vmpp(self, v, j):
        if v is None or len(v) == 0:
            return None
        power  = np.asarray(v) * np.asarray(j)
        return float(np.asarray(v)[int(np.argmax(power))])

    def _scan_rate(self):
        c = self.cfg
        nplc_ms     = float(c.get("nplc", 1.0)) * (1000.0 / 60.0)
        t_per_point = (float(c.get("delay_ms", 20)) + nplc_ms + 50.0) / 1000.0
        step_v      = abs(float(c.get("step_v", 0.025)))
        return (step_v * 1000.0) / t_per_point if t_per_point > 0 else 0.0

    def _cleanup(self):
        try:
            self.inst.write(":OUTP OFF")
        except Exception:
            pass
        if self._csv is not None:
            try:
                self._csv.close()
            except Exception:
                pass
        # Fold the autosave CSV into the tracking workbook.
        if self._csv_path and self._tracking_path and os.path.exists(self._csv_path):
            try:
                ts = datetime.now().strftime("%H%M%S")
                consolidate_csv_into_xlsx(
                    self._csv_path, self._tracking_path, f"Stress_{ts}")
            except Exception as exc:
                self._emit("on_log", f"CSV consolidation failed: {exc}")
        self._status("ISOS experiment complete.")
        self._emit("on_complete")
