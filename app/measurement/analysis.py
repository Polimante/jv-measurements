import numpy as np


def calculate_jv_parameters(voltages, currents, illumination):
    if illumination <= 0:
        raise ValueError(
            "Illumination is 0 mW/cm\u00b2 \u2014 PCE cannot be calculated.\n"
            "Enter the detector power in the Illumination Settings panel."
        )

    crossings = np.where(np.diff(np.sign(currents)))[0]
    if len(crossings) == 0:
        raise ValueError(
            "No current zero-crossing found in the J-V curve.\n"
            "Voc cannot be determined.\n\n"
            "Possible causes:\n"
            "  \u2022 Solar simulator shutter is closed (dark measurement)\n"
            "  \u2022 End Voltage is set below the cell\u2019s Voc\n"
            "  \u2022 Cell is not illuminated or not connected correctly"
        )

    power  = voltages * currents
    idx_mp = np.argmax(power)
    Vm, Jm = voltages[idx_mp], currents[idx_mp]
    Voc    = voltages[crossings[0]]
    Jsc    = currents[np.argmin(np.abs(voltages))]

    if Voc == 0 or Jsc == 0:
        raise ValueError(
            "Voc or Jsc is zero \u2014 FF and PCE cannot be calculated.\n"
            "Check cell connections and illumination."
        )

    FF  = (Vm * Jm) / (Voc * Jsc) * 100
    PCE = (Voc * Jsc * FF) / illumination
    return Voc, abs(Jsc), FF, PCE


def calculate_resistances(voltages, currents, Voc, n=5):
    try:
        zi       = np.argmin(np.abs(voltages))
        s0, e0   = max(0, zi - n//2), min(len(voltages), zi - n//2 + n)
        m_rsh, _ = np.polyfit(voltages[s0:e0], currents[s0:e0], 1)
        Rsh      = abs(1000 / m_rsh) if m_rsh else float("inf")
        vi       = np.argmin(np.abs(voltages - Voc))
        s1, e1   = max(0, vi - n), min(len(voltages), vi + 1)
        m_rs, _  = np.polyfit(voltages[s1:e1], currents[s1:e1], 1)
        Rs       = (1 / abs(m_rs)) * 1000 if m_rs else float("inf")
        return Rs, Rsh
    except Exception:
        return float("nan"), float("nan")


def calculate_hysteresis_index(PCE_f, PCE_r):
    """Returns HI in % following Kim & Park (2014): (PCE_RS - PCE_FS) / PCE_RS × 100."""
    return (PCE_r - PCE_f) / PCE_r * 100 if PCE_r else float("nan")
