"""
Pure MPP Tracker — Perturb-and-Observe algorithm for perovskite solar cells.

The output is turned ON once and stays ON for the entire experiment.
No J-V scans are performed between measurements. The voltage is continuously
adjusted by the P&O algorithm to track the maximum power point in real time.

This produces a single, continuous PCE(t) trace identical to what is shown
in Christians et al. (J. Phys. Chem. Lett. 2015) and Cimaroli et al.
(J. Mater. Chem. C 2017).

Algorithm (perturb-and-observe):
  1. Apply V, read J, compute P = V * J
  2. If P > P_prev  → continue in same direction
     If P < P_prev  → reverse direction
  3. V_new = V + direction * step_mV
  4. Clamp V_new to [v_min, v_max]
  5. Repeat every interval_ms milliseconds
"""

import time
import numpy as np

from app.instrument.keithley import configure_oc_mode, read_voc_now


class PureMPPTracker:
    def __init__(self, instrument, params, callbacks, stop_event,
                 load_mode="MPP"):
        """
        instrument : open pyvisa resource (Keithley 2400)
        params     : dict with all measurement parameters
        callbacks  : dict of callables for UI updates
        stop_event : threading.Event to abort measurement
        load_mode  : "MPP" (perturb-and-observe tracking, default) or
                     "OC" (hold at open circuit and record Voc). In OC mode
                     j and pce are reported as 0 on every tick.
        """
        self.inst       = instrument
        self.params     = params
        self.callbacks  = callbacks
        self.stop_event = stop_event
        self.load_mode  = load_mode

        self.time_s   = []   # elapsed seconds from t_start
        self.v_track  = []   # tracked Vmpp (V)
        self.j_track  = []   # current density at Vmpp (mA/cm²)
        self.pce_track = []  # PCE (%)
        self.t_start  = None

    def run(self):
        """Main tracking loop — runs in a worker thread."""
        if self.load_mode == "OC":
            return self._run_oc()

        self._configure_instrument()

        # ── Initial J-V scan to find Vmpp ─────────────────────────────────────
        self.callbacks["on_status"]("Initial J-V scan — finding Vmpp…")
        v_arr, j_arr = self._measure_scan(
            self.params["scan_end_v"],   # reverse scan: end → start
            self.params["scan_start_v"],
            -self.params["scan_step_v"],
        )
        v = self._find_vmpp(v_arr, j_arr)
        self.callbacks["on_status"](
            f"Vmpp found: {v:.3f} V — starting P&O tracking…")
        self.callbacks["on_jv_done"](v_arr, j_arr, v)

        # Apply Vmpp and let the cell settle before the first P&O reading
        self.inst.write(f":SOUR:VOLT {v:.4f}")
        time.sleep(0.2)

        # t=0 is defined after the initial J-V scan, when P&O begins
        self.t_start = time.time()

        total_s    = self.params["total_duration_min"] * 60.0
        step_v     = self.params["perturb_step_mv"] / 1000.0
        v_min      = self.params["v_min"]
        v_max      = self.params["v_max"]
        interval_s = self.params["interval_ms"] / 1000.0
        pin        = self.params["pin"]
        area       = self.params["cell_area"]

        p_prev    = None
        direction = 1   # start by perturbing upward

        try:
            while not self.stop_event.is_set():
                if time.time() - self.t_start >= total_s:
                    break

                t_loop = time.time()

                # ── Read current at V ──────────────────────────────────────
                raw = self.inst.query(":READ?").split(",")
                j   = -(float(raw[1]) * 1000.0) / area
                p   = v * j              # mW/cm²  (power density at this point)
                pce = p / pin * 100.0 if pin > 0 else 0.0

                t_abs = time.time() - self.t_start
                self.time_s.append(t_abs)
                self.v_track.append(v)
                self.j_track.append(j)
                self.pce_track.append(pce)

                self.callbacks["on_update"](t_abs, v, j, pce)

                # ── P&O decision ───────────────────────────────────────────
                if p_prev is not None:
                    if p < p_prev:
                        direction = -direction   # power dropped → reverse

                p_prev = p

                # ── Apply next voltage ─────────────────────────────────────
                v = max(v_min, min(v_max, v + direction * step_v))
                self.inst.write(f":SOUR:VOLT {v:.4f}")

                # ── Wait for remainder of interval ─────────────────────────
                elapsed = time.time() - t_loop
                remaining = interval_s - elapsed
                if remaining > 0:
                    # Break the sleep into short chunks so stop_event is
                    # checked frequently even with long intervals.
                    deadline = time.time() + remaining
                    while time.time() < deadline and not self.stop_event.is_set():
                        time.sleep(min(0.05, deadline - time.time()))

        finally:
            try:
                self.inst.write(":OUTP OFF")
            except Exception:
                pass
            self.callbacks["on_status"]("Pure MPP Tracking complete.")
            self.callbacks["on_complete"](
                self.time_s, self.v_track, self.j_track, self.pce_track)

    # ── OC (open-circuit) tracking ────────────────────────────────────────────

    def _run_oc(self):
        """Hold the cell at open circuit and record Voc(t).

        Mirrors :meth:`run` but with no perturbation: the instrument sources
        0 A, senses voltage, and ``read_voc_now`` returns Voc on each tick.
        ``j`` and ``pce`` are reported as 0.0 throughout. An optional baseline
        J-V scan runs first unless ``params['oc_skip_initial_scan']`` is set.
        """
        # ── Optional baseline J-V scan ────────────────────────────────────────
        if not self.params.get("oc_skip_initial_scan", False):
            self._configure_instrument()
            self.callbacks["on_status"]("Initial J-V scan — baseline…")
            v_arr, j_arr = self._measure_scan(
                self.params["scan_end_v"],
                self.params["scan_start_v"],
                -self.params["scan_step_v"],
            )
            vmpp = self._find_vmpp(v_arr, j_arr)
            self.callbacks["on_jv_done"](v_arr, j_arr, vmpp)

        # ── Switch to OC mode ─────────────────────────────────────────────────
        configure_oc_mode(self.inst, self.params.get("nplc", 1.0))
        self.callbacks["on_status"](
            "Holding at open circuit — recording Voc…")

        # t=0 begins when OC tracking starts (after any baseline scan)
        self.t_start = time.time()

        total_s    = self.params["total_duration_min"] * 60.0
        interval_s = self.params["interval_ms"] / 1000.0

        try:
            while not self.stop_event.is_set():
                if time.time() - self.t_start >= total_s:
                    break

                t_loop = time.time()

                v   = read_voc_now(self.inst)
                j   = 0.0
                pce = 0.0

                t_abs = time.time() - self.t_start
                self.time_s.append(t_abs)
                self.v_track.append(v)
                self.j_track.append(j)
                self.pce_track.append(pce)

                self.callbacks["on_update"](t_abs, v, j, pce)

                # ── Wait for remainder of interval ─────────────────────────
                elapsed = time.time() - t_loop
                remaining = interval_s - elapsed
                if remaining > 0:
                    deadline = time.time() + remaining
                    while time.time() < deadline and not self.stop_event.is_set():
                        time.sleep(min(0.05, deadline - time.time()))

        finally:
            try:
                self.inst.write(":OUTP OFF")
            except Exception:
                pass
            self.callbacks["on_status"]("Pure OC tracking complete.")
            self.callbacks["on_complete"](
                self.time_s, self.v_track, self.j_track, self.pce_track)

    # ── Instrument setup ──────────────────────────────────────────────────────

    def _configure_instrument(self):
        p = self.params
        self.inst.write("*RST")
        time.sleep(0.3)
        self.inst.write(":SOUR:FUNC VOLT")
        self.inst.write(":SENS:FUNC 'CURR:DC'")
        self.inst.write(":SENS:CURR:PROT 0.1")
        self.inst.write(f":SENS:CURR:NPLC {p['nplc']:.3f}")
        self.inst.write(f":SOUR:DEL {p['delay_ms'] / 1000:.4f}")
        self.inst.write(":SYST:RSEN OFF")
        self.inst.write(f":SOUR:VOLT {p['scan_start_v']:.4f}")
        self.inst.write(":OUTP ON")

    def _measure_scan(self, start, end, step):
        """Quick J-V sweep. Returns (voltages, currents) as numpy arrays."""
        area = self.params["cell_area"]
        voltages, currents = [], []
        v = start
        while (step > 0 and v <= end + 1e-9) or (step < 0 and v >= end - 1e-9):
            if self.stop_event.is_set():
                break
            self.inst.write(f":SOUR:VOLT {v:.4f}")
            time.sleep(0.02)
            raw     = self.inst.query(":READ?").split(",")
            current = -float(raw[1])
            j       = (current * 1000.0) / area
            voltages.append(v)
            currents.append(j)
            v = round(v + step, 6)
        return np.array(voltages), np.array(currents)

    def _find_vmpp(self, voltages, currents):
        """Return voltage at maximum power point from a J-V array."""
        if len(voltages) == 0:
            return self.params.get("v_max", 1.0) * 0.7
        power  = voltages * currents
        idx_mp = int(np.argmax(power))
        return float(voltages[idx_mp])
