"""
Low-level Keithley 2400 helpers.

All functions receive the pyvisa resource object explicitly so they remain
independent of the GUI layer and can be tested or reused without a running
Tkinter window.
"""

import time
import numpy as np


def init_instrument(inst, nplc, delay_s, four_wire):
    """
    (Re-)configure an already-open pyvisa resource for a J-V sweep.
    Sends SCPI commands only — does NOT reopen the connection.
    """
    inst.write("*RST")
    inst.write(":SOUR:FUNC VOLT")
    inst.write(":SENS:FUNC 'CURR:DC'")
    inst.write(":SENS:CURR:PROT 0.1")
    inst.write(f":SENS:CURR:NPLC {nplc:.3f}")
    inst.write(f":SOUR:DEL {delay_s:.4f}")
    inst.write(":SYST:RSEN ON" if four_wire else ":SYST:RSEN OFF")
    inst.write(":OUTP ON")


def configure_oc_mode(inst, nplc: float = 1.0):
    """
    Configure the instrument to hold the cell at open circuit and read Voc.

    Source = CURR with setpoint 0 A, sense = VOLT, so the measured voltage is
    the cell's open-circuit voltage. No perturbation is applied. Used by the
    ISOS-L-1 / V-1 OC stress phases (Khenkin et al. Nature Energy 2020).

    Sequence:
        *RST, :SOUR:FUNC CURR, :SOUR:CURR 0, :SENS:FUNC 'VOLT',
        :SENS:VOLT:PROT 2, :SENS:VOLT:NPLC <nplc>, :SYST:RSEN OFF, :OUTP ON
    """
    inst.write("*RST")
    inst.write(":SOUR:FUNC CURR")
    inst.write(":SOUR:CURR 0")
    inst.write(":SENS:FUNC 'VOLT'")
    inst.write(":SENS:VOLT:PROT 2")
    inst.write(f":SENS:VOLT:NPLC {nplc:.3f}")
    inst.write(":SYST:RSEN OFF")
    inst.write(":OUTP ON")


def read_voc_now(inst) -> float:
    """
    Read the open-circuit voltage once and return it in volts.

    Assumes the instrument is already in OC mode (see ``configure_oc_mode``);
    the first field of the :READ? response is the measured voltage.
    """
    return float(inst.query(":READ?").split(",")[0])


def apply_prebias(inst, v_pb, t_pb, stop_event, status_callback):
    """
    Hold the output at *v_pb* volts for *t_pb* seconds before the sweep.

    *status_callback* is a callable(str) used to report progress to the GUI
    (typically ``self.status_var.set``).
    Respects *stop_event* so the user can abort during pre-bias.
    """
    status_callback(f"Pre-bias: {v_pb:.2f} V for {t_pb:.1f} s\u2026")
    inst.write(f":SOUR:VOLT {v_pb:.3f}")
    elapsed = 0.0
    while elapsed < t_pb:
        if stop_event.is_set():
            return
        time.sleep(0.1)
        elapsed += 0.1


def apply_light_soaking(inst, duration_s, stop_event, status_callback):
    """
    Hold the cell at open circuit under illumination for *duration_s* seconds.

    The Keithley output is turned OFF so the cell terminals are floating —
    this is true open-circuit conditions.  After the timer completes the
    output is restored to ON so the scan can proceed immediately.

    Physical basis: perovskite absorbers contain mobile ions that redistribute
    on a seconds-to-minutes timescale under illumination.  Light soaking drives
    the device to a stabilised ionic state before the J-V scan, making the
    measured parameters reproducible and representative of steady-state operation.

    Recommended durations (Christians et al. 2015; Khenkin et al. 2020):
      n-i-p architectures  ~60 s
      p-i-n architectures  120–300 s
    """
    try:
        inst.write(":OUTP OFF")   # open circuit — no forced voltage or current
    except Exception:
        pass

    for remaining in range(int(duration_s), 0, -1):
        if stop_event.is_set():
            return
        status_callback(f"Light soaking: {remaining} s remaining\u2026")
        time.sleep(1)

    if not stop_event.is_set():
        try:
            inst.write(":OUTP ON")
        except Exception:
            pass


def measure_scan(inst, start, end, step, cell_area, stop_event):
    """
    Step the source voltage from *start* to *end* (inclusive) by *step*
    and read the current at each point.

    Returns ``(voltages, current_densities)`` as NumPy arrays, or
    ``(None, None)`` if the stop event fires mid-scan.
    """
    safety = 0.02
    voltages, currents = [], []
    for v in np.arange(start, end + step, step):
        if stop_event.is_set():
            return None, None
        inst.write(f":SOUR:VOLT {v:.3f}")
        time.sleep(safety)
        current = -float(inst.query(":READ?").split(",")[1])
        currents.append((current * 1000) / cell_area)
        voltages.append(v)
    return np.array(voltages), np.array(currents)
