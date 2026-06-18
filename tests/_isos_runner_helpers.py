"""
Shared fakes for the ContinuousISOSRunner test suite.

The runner drives the raw-SCPI instrument interface (``inst.write`` /
``inst.query``), exactly like the existing trackers, so these helpers provide a
SCPI-style fake backed by a :class:`DummyCell`, plus a deterministic virtual
clock so time-driven scheduling runs instantly.

This module is intentionally NOT named ``test_*`` so pytest does not collect it.
"""

from __future__ import annotations

from app.simulation.dummy_cell import DummyCell


class VirtualClock:
    """Deterministic clock: ``sleep`` advances virtual time; ``time`` reads it."""

    def __init__(self, start: float = 0.0):
        self.t = float(start)

    def time(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        if seconds and seconds > 0:
            self.t += seconds


class FakeKeithley:
    """SCPI-style fake backed by a DummyCell.

    ``:READ?`` returns ``Voc`` in field 0 when in current-source (OC) mode, and
    the cell current (A) in field 1 when in voltage-source mode. Field 1 is
    pre-negated and scaled so the keithley/runner sign convention recovers the
    DummyCell's positive photocurrent (mA/cm²).
    """

    def __init__(self, cell: DummyCell | None = None, area: float = 0.07,
                 voc: float = 1.0):
        self.cell = cell if cell is not None else DummyCell(mode="ideal_stable")
        self.area = area
        self.voc  = voc
        self.v    = 0.0
        self.func = "VOLT"
        self.writes: list[str] = []

    def write(self, cmd: str) -> None:
        self.writes.append(cmd)
        if cmd.startswith(":SOUR:FUNC"):
            self.func = "CURR" if "CURR" in cmd else "VOLT"
        elif cmd.startswith(":SOUR:VOLT"):
            try:
                self.v = float(cmd.split()[1])
            except (IndexError, ValueError):
                pass

    def query(self, cmd: str) -> str:
        if cmd == ":READ?":
            if self.func == "CURR":          # OC mode → Voc in field 0
                return f"{self.voc:.6f},0,0,0,0"
            j = self.cell.current_density(self.v, scan_direction="reverse")
            raw_a = -(j * self.area / 1000.0)  # invert runner's transform
            return f"{self.v:.6f},{raw_a:.9e},0,0,0"
        return "0,0,0,0,0"


def make_runner_config(tmp_path, **overrides) -> dict:
    """Base config for a fast, deterministic continuous run.

    end_v is above the DummyCell's ~1.18 V Voc so the J-V curve has a real
    zero-crossing (calculate_jv_parameters requires one).
    """
    cfg = {
        "protocol_id":            "ISOS-L-1",
        "sample_name":            "L1Test",
        "folder":                 str(tmp_path),
        "total_duration_h":       0.05,    # 180 s
        "scan_interval_min":      0.6,     # 36 s  → 5 scans
        "load_mode":              "OC",
        "stress_with_light":      True,
        "start_v":                -0.05,
        "end_v":                  1.30,
        "step_v":                 0.05,
        "cell_area":              0.07,
        "pin":                    100.0,
        "delay_ms":               20.0,
        "nplc":                   1.0,
        "four_wire":              False,
        "v_min":                  0.0,
        "v_max":                  1.30,
        "perturb_step_mv":        10.0,
        "pre_scan_relaxation_s":  0.0,
        "spo_duration_s":         0.0,
        "stress_log_interval_s":  6.0,
        "clock":                  VirtualClock(),
    }
    cfg.update(overrides)
    return cfg
