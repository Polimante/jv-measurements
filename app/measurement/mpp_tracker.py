"""
MPP Tracking module — JV-SPO cycling protocol for perovskite solar cells.

Protocol (per cycle):
  1. [Optional] Forward J-V scan
  2. Pre-bias conditioning (if enabled)
  3. Reverse J-V scan → extract Vmpp, Voc, Jsc, FF, PCE
  4. SPO phase: hold at Vmpp, read J every interval_ms
     → calculate PCE(t) = Vmpp × J / Pin × 100
     → check stabilization criterion
  5. Repeat from step 1 until total_duration_min elapsed

The instrument output is turned ON once at the start and stays ON for the
entire experiment. It is turned OFF only in the finally block at the end.
This preserves the cell's ionic distribution between phases and produces a
continuous PCE(t) trace without disconnected segments.

References:
  [1] Christians et al., J. Phys. Chem. Lett. 2015, 6, 3808.
  [2] Zimmermann et al., APL Materials 2016, 4, 091901.
  [3] Khenkin et al., Nature Energy 2020, 5, 35 (ISOS consensus).
  [4] NREL stabilization criterion: ΔJ/Δt < 0.1%/min over 60 s window.
"""

import numpy as np
import time
import threading


class MPPTracker:
    def __init__(self, instrument, params, callbacks, stop_event):
        """
        instrument : open pyvisa resource (Keithley 2400)
        params     : dict with all measurement parameters
        callbacks  : dict of callables for UI updates
        stop_event : threading.Event to abort measurement
        """
        self.inst       = instrument
        self.params     = params
        self.callbacks  = callbacks
        self.stop_event = stop_event

        self.cycles           = []
        self.spo_time         = []   # continuous absolute elapsed seconds
        self.spo_j            = []   # continuous J (mA/cm²)
        self.spo_pce          = []   # continuous PCE (%)
        self.spo_cycle_idx    = []
        self.jv_scan_intervals = []  # list of (t_start_s, t_end_s) per cycle
        self.t_start          = None

    def run(self):
        """Main tracking loop — runs in a worker thread."""
        total_s  = self.params["total_duration_min"] * 60.0
        cycle_n  = 0

        self._configure_instrument()
        # t=0 is defined AFTER the output turns ON so the first SPO reading
        # appears at approximately t=0 on the plot.
        self.t_start = time.time()

        try:
            while not self.stop_event.is_set():
                elapsed = time.time() - self.t_start
                if elapsed >= total_s:
                    break

                cycle_n += 1
                self.callbacks["on_status"](f"Cycle {cycle_n} — J-V scan…")
                self.callbacks["on_log"](f"\n── Cycle {cycle_n} ──────────────────")

                # ── Record J-V scan interval start ────────────────────────
                t_jv_start = time.time() - self.t_start

                # ── Optional forward scan ──────────────────────────────────
                v_fwd, j_fwd = None, None
                if self.params["include_forward"]:
                    self.callbacks["on_status"](f"Cycle {cycle_n} — Forward scan…")
                    v_fwd, j_fwd = self._measure_scan(
                        self.params["start_v"], self.params["end_v"],
                        self.params["step_v"])
                    if self.stop_event.is_set():
                        break

                # ── Pre-bias (output stays ON throughout) ──────────────────
                if self.params["prebias_enabled"]:
                    self.callbacks["on_status"](
                        f"Cycle {cycle_n} — Pre-bias "
                        f"{self.params['prebias_v']:.2f} V "
                        f"for {self.params['prebias_t']:.1f} s…")
                    self._apply_prebias()
                    if self.stop_event.is_set():
                        break

                # ── Reverse scan ───────────────────────────────────────────
                self.callbacks["on_status"](f"Cycle {cycle_n} — Reverse scan…")
                v_rev, j_rev = self._measure_scan(
                    self.params["end_v"], self.params["start_v"],
                    -self.params["step_v"])
                if self.stop_event.is_set():
                    break

                # ── Record J-V scan interval end ───────────────────────────
                t_jv_end = time.time() - self.t_start
                self.jv_scan_intervals.append((t_jv_start, t_jv_end))

                # ── Extract parameters ─────────────────────────────────────
                Voc, Jsc, FF, PCE = self._calc_jv_params(v_rev, j_rev)
                Vmpp, Jmpp        = self._find_mpp(v_rev, j_rev)

                HI = None
                if v_fwd is not None:
                    _, _, _, PCE_fwd = self._calc_jv_params(v_fwd, j_fwd)
                    HI = (PCE - PCE_fwd) / PCE * 100.0 if PCE else None

                # Time when this cycle's Vmpp is about to be applied — used
                # by the GUI to draw a thin dashed vertical line on PCE(t).
                t_vmpp = time.time() - self.t_start

                cycle_data = {
                    "cycle":       cycle_n,
                    "time_s":      t_vmpp,
                    "Vmpp":        Vmpp,  "Jmpp": Jmpp,
                    "Voc":         Voc,   "Jsc":  Jsc,
                    "FF":          FF,    "PCE":  PCE,
                    "HI":          HI,
                    "v_rev":       v_rev, "j_rev": j_rev,
                    "v_fwd":       v_fwd, "j_fwd": j_fwd,
                    "jv_interval": (t_jv_start, t_jv_end),
                }
                self.cycles.append(cycle_data)

                log_msg = (
                    f"  Vmpp = {Vmpp:.3f} V | Jmpp = {Jmpp:.3f} mA/cm²\n"
                    f"  Voc = {Voc:.3f} V  | Jsc = {Jsc:.3f} mA/cm²\n"
                    f"  FF = {FF:.1f}%     | PCE = {PCE:.2f}%"
                )
                if HI is not None:
                    log_msg += f" | HI = {HI:+.1f}%"
                self.callbacks["on_log"](log_msg)
                self.callbacks["on_jv_update"](cycle_data)

                # ── SPO phase — output stays ON, voltage transition inside _run_spo
                self.callbacks["on_status"](
                    f"Cycle {cycle_n} — SPO at {Vmpp:.3f} V "
                    f"for {self.params['spo_duration_s']:.0f} s…")
                self._run_spo(cycle_n, Vmpp)

                if self.stop_event.is_set():
                    break

        finally:
            # Output turns OFF exactly once, here, regardless of how the loop
            # ended (normal completion, stop button, or exception).
            try:
                self.inst.write(":OUTP OFF")
            except Exception:
                pass
            self.callbacks["on_status"]("MPP Tracking complete.")
            self.callbacks["on_complete"](
                self.cycles, self.spo_time, self.spo_j, self.spo_pce)

    # ── Instrument helpers ────────────────────────────────────────────────────

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
        self.inst.write(":OUTP ON")

    def _measure_scan(self, start, end, step):
        voltages, currents = [], []
        v = start
        while (step > 0 and v <= end + 1e-9) or (step < 0 and v >= end - 1e-9):
            if self.stop_event.is_set():
                break
            self.inst.write(f":SOUR:VOLT {v:.4f}")
            time.sleep(0.02)
            raw     = self.inst.query(":READ?").split(",")
            current = -float(raw[1])
            j       = (current * 1000.0) / self.params["cell_area"]
            voltages.append(v)
            currents.append(j)
            v = round(v + step, 6)
        return np.array(voltages), np.array(currents)

    def _apply_prebias(self):
        self.inst.write(f":SOUR:VOLT {self.params['prebias_v']:.3f}")
        elapsed, step = 0.0, 0.1
        while elapsed < self.params["prebias_t"]:
            if self.stop_event.is_set():
                return
            time.sleep(step)
            elapsed += step

    # ── Analysis helpers ──────────────────────────────────────────────────────

    def _calc_jv_params(self, voltages, currents):
        try:
            power      = voltages * currents
            idx_mp     = np.argmax(power)
            Vm, Jm     = voltages[idx_mp], currents[idx_mp]
            crossings  = np.where(np.diff(np.sign(currents)))[0]
            if len(crossings) == 0:
                return 0.0, 0.0, 0.0, 0.0
            Voc    = voltages[crossings[0]]
            Jsc    = abs(currents[np.argmin(np.abs(voltages))])
            FF     = (Vm * Jm) / (Voc * Jsc) * 100.0
            PCE    = (Voc * Jsc * FF) / self.params["pin"]
            return float(Voc), float(Jsc), float(FF), float(PCE)
        except Exception:
            return 0.0, 0.0, 0.0, 0.0

    def _find_mpp(self, voltages, currents):
        power  = voltages * currents
        idx_mp = int(np.argmax(power))
        return float(voltages[idx_mp]), float(currents[idx_mp])

    # ── SPO phase ─────────────────────────────────────────────────────────────

    def _run_spo(self, cycle_n, Vmpp):
        """Hold at Vmpp and record J(t) and PCE(t). Check stabilization.

        Sets the voltage to Vmpp here (not in the caller) so the settling
        sleep is always applied before the first reading.
        """
        # Apply Vmpp and let the cell settle — prevents the first reading
        # from catching the instrument mid-transition from the scan voltage.
        self.inst.write(f":SOUR:VOLT {Vmpp:.4f}")
        time.sleep(0.1)

        pin         = self.params["pin"]
        interval_s  = self.params["spo_interval_ms"] / 1000.0
        spo_dur_s   = self.params["spo_duration_s"]
        threshold   = self.params["stab_threshold"]
        t_spo_start = time.time()
        j_window    = []
        t_window    = []

        while not self.stop_event.is_set():
            elapsed_spo = time.time() - t_spo_start
            if elapsed_spo >= spo_dur_s:
                break

            raw = self.inst.query(":READ?").split(",")
            j   = -(float(raw[1]) * 1000.0) / self.params["cell_area"]
            pce = (Vmpp * j) / pin * 100.0 if pin > 0 else 0.0

            t_abs = time.time() - self.t_start
            self.spo_time.append(t_abs)
            self.spo_j.append(j)
            self.spo_pce.append(pce)
            self.spo_cycle_idx.append(cycle_n)

            j_window.append(j)
            t_window.append(t_abs)

            # Keep only last 60 s in window
            cutoff = t_abs - 60.0
            while t_window and t_window[0] < cutoff:
                t_window.pop(0)
                j_window.pop(0)

            # Stabilization check — only after 60 s of data
            stabilized = False
            if len(j_window) >= 2 and (t_window[-1] - t_window[0]) >= 55.0:
                j_mean = np.mean(j_window) or 1e-9
                rate   = abs(
                    (j_window[-1] - j_window[0]) / j_mean * 100.0
                    / ((t_window[-1] - t_window[0]) / 60.0)
                )
                stabilized = rate < threshold

            self.callbacks["on_spo_update"](t_abs, j, pce, cycle_n, stabilized)
            time.sleep(interval_s)
