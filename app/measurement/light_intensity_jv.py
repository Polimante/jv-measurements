"""Light-Intensity Dependent J-V measurement orchestrator.

Runs one forward + reverse J-V scan at each user-defined illumination
intensity (with manual solar-simulator adjustment via a GUI prompt),
then fits the light ideality factor n_idL from the Voc vs ln(L) relation.

Reference:
    Caprioglio et al., Adv. Energy Mater. 2020, 10, 2000502.
    Fluxim DC measurement tutorial, equation 4.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from app.instrument.base import SourceMeter
from app.measurement.analysis import (
    LightIdealityFit,
    calculate_hysteresis_index,
    calculate_jv_parameters,
    calculate_resistances,
    fit_light_ideality,
    fit_single_diode,
)
from app.io.exporter import save_data


@dataclass
class IntensityResult:
    """Result for one intensity step."""

    index: int
    intensity: float
    status: str                         # "completed", "skipped", "failed"
    v_fwd: Optional[np.ndarray] = None
    j_fwd: Optional[np.ndarray] = None
    v_rev: Optional[np.ndarray] = None
    j_rev: Optional[np.ndarray] = None
    Voc_fwd: float = float("nan")
    Jsc_fwd: float = float("nan")
    FF_fwd:  float = float("nan")
    PCE_fwd: float = float("nan")
    Rs_fwd:  float = float("nan")
    Rsh_fwd: float = float("nan")
    Voc_rev: float = float("nan")
    Jsc_rev: float = float("nan")
    FF_rev:  float = float("nan")
    PCE_rev: float = float("nan")
    Rs_rev:  float = float("nan")
    Rsh_rev: float = float("nan")
    HI:      float = float("nan")
    single_diode_fit: object = None
    individual_backup_path: str = ""
    error_message: str = ""


class LightIntensityJVMeasurement:
    """Runs J-V sweeps at multiple illumination intensities.

    Each intensity step:
        1. Prompts the user to set the solar simulator via *on_prompt_user*.
        2. Waits *stabilization_time_s* seconds (with countdown).
        3. Optionally applies light soaking.
        4. Measures forward then reverse J-V scans.
        5. Optionally applies pre-bias between scans.
        6. Extracts PV parameters.
        7. Optionally fits the single-diode model.
        8. Saves an individual backup file.
        9. Fires *on_measurement_done*.

    After all steps, fires *on_complete* with the path to the consolidated
    workbook (written by the caller-supplied *save_consolidated* hook or by
    the light_intensity_exporter module).

    Args:
        sourcemeter: Connected SourceMeter (real Keithley adapter or DummySMU).
        intensities: Illumination intensity values in measurement order
            (arbitrary proportional units — suns, %, or mW/cm²).
        scan_params: Dict with keys: start_v, end_v, step_v, delay_ms, nplc,
            cell_area, four_wire, reference_illumination_mw_cm2.
        stabilization_time_s: Seconds to wait after the user confirms the
            simulator is set.
        pre_bias_settings: Dict with keys: enabled (bool), voltage (V),
            duration (s).  Pass None to disable.
        light_soaking_settings: Dict with keys: enabled (bool), duration (s).
            Pass None to disable.
        sample_name: Sample identifier string.
        output_folder: Path to the directory for individual backup files.
        auto_single_diode_fit: Run single-diode fit on the reverse scan.
        on_status: Called with a status string (thread-safe, any thread).
        on_prompt_user: Called on the measurement thread with (message, intensity).
            Must block until the user confirms or cancels.
            Returns True → proceed, False → abort this step.
        on_progress: Called with (current_index, total, status_string) after
            each completed/skipped step.
        on_measurement_done: Called with (index, IntensityResult) after each step.
        on_complete: Called with (consolidated_path) at the end.
        stop_event: Set by the GUI to abort mid-sequence.
    """

    def __init__(
        self,
        sourcemeter: SourceMeter,
        intensities: list[float],
        scan_params: dict,
        stabilization_time_s: float,
        pre_bias_settings: Optional[dict],
        light_soaking_settings: Optional[dict],
        sample_name: str,
        output_folder: Path,
        auto_single_diode_fit: bool,
        on_status: Callable[[str], None],
        on_prompt_user: Callable[[str, float], bool],
        on_progress: Callable[[int, int, str], None],
        on_measurement_done: Callable[[int, "IntensityResult"], None],
        on_complete: Callable[[Path], None],
        stop_event: threading.Event,
        instrument_idn: str = "",
    ) -> None:
        self._smu                   = sourcemeter
        self._intensities           = list(intensities)
        self._scan_params           = dict(scan_params)
        self._stab_time             = float(stabilization_time_s)
        self._prebias               = pre_bias_settings
        self._light_soaking         = light_soaking_settings
        self._sample_name           = sample_name
        self._output_folder         = Path(output_folder)
        self._auto_fit              = auto_single_diode_fit
        self._on_status             = on_status
        self._on_prompt_user        = on_prompt_user
        self._on_progress           = on_progress
        self._on_measurement_done   = on_measurement_done
        self._on_complete           = on_complete
        self._stop_event            = stop_event
        self._instrument_idn        = instrument_idn

        self.results: list[IntensityResult] = []
        self._aborted_at: Optional[int]     = None

    # ── Public entry point ────────────────────────────────────────────────────

    def run(self) -> None:
        """Execute the full sequence.  Designed to run in a daemon thread."""
        n = len(self._intensities)
        sp = self._scan_params

        self._smu.configure(
            nplc      = sp["nplc"],
            delay_s   = sp["delay_ms"] / 1000.0,
            four_wire = sp.get("four_wire", False),
        )

        try:
            for idx, intensity in enumerate(self._intensities):
                if self._stop_event.is_set():
                    self._aborted_at = idx
                    self._mark_remaining_skipped(idx)
                    break

                result = self._run_one_step(idx, intensity, n)
                self.results.append(result)
                self._on_measurement_done(idx, result)
                self._on_progress(idx + 1, n, result.status)
        finally:
            try:
                self._smu.output_off()
            except Exception:
                pass

        consolidated = self._save_consolidated()
        self._on_complete(consolidated)

    # ── Single-step execution ─────────────────────────────────────────────────

    def _run_one_step(self, idx: int, intensity: float, n_total: int) -> IntensityResult:
        result = IntensityResult(index=idx, intensity=intensity, status="skipped")

        # 1. Prompt user
        msg = (
            f"Set the solar simulator illumination to {intensity} sun.\n"
            f"Make sure the cell is properly aligned and the simulator is stable.\n\n"
            f"Measurement {idx + 1} of {n_total}"
        )
        ok = self._on_prompt_user(msg, intensity)
        if not ok or self._stop_event.is_set():
            result.status = "skipped"
            return result

        # 2. Stabilization countdown
        for remaining in range(int(self._stab_time), 0, -1):
            if self._stop_event.is_set():
                result.status = "skipped"
                return result
            self._on_status(
                f"Stabilizing at {intensity} sun: {remaining} s remaining…"
            )
            self._sleep_interruptible(1.0)

        if self._stop_event.is_set():
            result.status = "skipped"
            return result

        try:
            # 3. Light soaking (optional)
            if self._light_soaking and self._light_soaking.get("enabled"):
                self._smu.apply_light_soaking(
                    self._light_soaking["duration"],
                    self._stop_event,
                    self._on_status,
                )
                if self._stop_event.is_set():
                    result.status = "skipped"
                    return result

            # 4. Forward scan
            self._on_status(f"[{intensity} sun] Measuring forward scan…")
            sp = self._scan_params
            v_f, j_f = self._smu.measure_scan(
                sp["start_v"], sp["end_v"], sp["step_v"],
                sp["cell_area"], self._stop_event,
            )
            if v_f is None or self._stop_event.is_set():
                result.status = "skipped"
                return result

            # 5. Pre-bias (optional, between forward and reverse)
            if self._prebias and self._prebias.get("enabled"):
                self._smu.apply_prebias(
                    self._prebias["voltage"],
                    self._prebias["duration"],
                    self._stop_event,
                    self._on_status,
                )
                if self._stop_event.is_set():
                    result.status = "skipped"
                    return result

            # 6. Reverse scan
            self._on_status(f"[{intensity} sun] Measuring reverse scan…")
            v_r, j_r = self._smu.measure_scan(
                sp["end_v"], sp["start_v"], -sp["step_v"],
                sp["cell_area"], self._stop_event,
            )
            if v_r is None or self._stop_event.is_set():
                result.status = "skipped"
                return result

            # 7. PV parameter extraction
            ref_illum = sp.get("reference_illumination_mw_cm2", 100.0)
            illum_mw = intensity * ref_illum

            Voc_f, Jsc_f, FF_f, PCE_f = calculate_jv_parameters(v_f, j_f, illum_mw)
            Voc_r, Jsc_r, FF_r, PCE_r = calculate_jv_parameters(v_r, j_r, illum_mw)
            Rs_f, Rsh_f = calculate_resistances(v_f, j_f, Voc_f)
            Rs_r, Rsh_r = calculate_resistances(v_r, j_r, Voc_r)
            HI = calculate_hysteresis_index(PCE_f, PCE_r)

            result.v_fwd   = v_f;  result.j_fwd   = j_f
            result.v_rev   = v_r;  result.j_rev   = j_r
            result.Voc_fwd = Voc_f; result.Jsc_fwd = Jsc_f
            result.FF_fwd  = FF_f;  result.PCE_fwd = PCE_f
            result.Rs_fwd  = Rs_f;  result.Rsh_fwd = Rsh_f
            result.Voc_rev = Voc_r; result.Jsc_rev = Jsc_r
            result.FF_rev  = FF_r;  result.PCE_rev = PCE_r
            result.Rs_rev  = Rs_r;  result.Rsh_rev = Rsh_r
            result.HI      = HI

            # 8. Optional single-diode fit
            if self._auto_fit:
                self._on_status(f"[{intensity} sun] Fitting single-diode model…")
                try:
                    result.single_diode_fit = fit_single_diode(v_r, j_r)
                except Exception:
                    pass

            # 9. Save individual backup file
            intensity_str = f"{intensity:.4f}".rstrip("0").rstrip(".").replace(".", "_")
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = f"{self._sample_name}_LD_{intensity_str}sun_{ts}.xlsx"
            backup_path = str(self._output_folder / fname)

            save_data(
                str(self._output_folder),
                f"{self._sample_name}_LD_{intensity_str}sun",
                v_f, j_f, v_r, j_r, illum_mw,
                Voc_f, Jsc_f, FF_f, PCE_f, Rs_f, Rsh_f,
                Voc_r, Jsc_r, FF_r, PCE_r, Rs_r, Rsh_r, HI,
                sp["cell_area"],
                sp["delay_ms"],
                sp["nplc"],
                sp.get("four_wire", False),
                self._prebias.get("enabled", False) if self._prebias else False,
                self._prebias.get("voltage", 0.0)   if self._prebias else 0.0,
                self._prebias.get("duration", 0.0)  if self._prebias else 0.0,
                self._instrument_idn,
                fit_result=result.single_diode_fit,
            )
            # save_data builds its own filename; we track the expected name
            result.individual_backup_path = backup_path
            result.status = "completed"

        except Exception as exc:
            result.status = "failed"
            result.error_message = str(exc)

        return result

    # ── Post-sequence work ────────────────────────────────────────────────────

    def _save_consolidated(self) -> Path:
        """Write the consolidated multi-sheet workbook and return its path."""
        from app.io.light_intensity_exporter import save_light_intensity_jv_consolidated
        return save_light_intensity_jv_consolidated(
            output_folder    = self._output_folder,
            sample_name      = self._sample_name,
            intensities      = self._intensities,
            results          = self.results,
            scan_params      = self._scan_params,
            stabilization_s  = self._stab_time,
            auto_fit         = self._auto_fit,
            aborted_at       = self._aborted_at,
            instrument_idn   = self._instrument_idn,
        )

    def _mark_remaining_skipped(self, from_index: int) -> None:
        """Append skipped IntensityResult entries for steps not yet started."""
        for idx in range(from_index, len(self._intensities)):
            if idx >= len(self.results):
                self.results.append(IntensityResult(
                    index=idx,
                    intensity=self._intensities[idx],
                    status="skipped",
                ))

    def _sleep_interruptible(self, seconds: float) -> None:
        """Sleep in 100 ms chunks so stop_event is checked frequently."""
        elapsed = 0.0
        chunk   = 0.1
        while elapsed < seconds:
            if self._stop_event.is_set():
                return
            time.sleep(min(chunk, seconds - elapsed))
            elapsed += chunk
