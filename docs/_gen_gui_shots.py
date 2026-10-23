"""Capture REAL GUI screenshots (with plotted curves) by driving the live app
with a simulated instrument — no hardware required.

Builds the actual JVMeasurementApp, injects a SCPI-shaped fake Keithley backed by
a DummyCell, runs a real J-V measurement so the app's own canvas draws the curves,
then grabs the application window region. Falls back to saving the app's figure
directly if the window grab fails.

Run from the project root:  python docs/_gen_gui_shots.py
"""
from __future__ import annotations

import os
import sys
import time

# Per-monitor DPI awareness so winfo_* pixel coords match the screen grab.
try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import tkinter as tk  # noqa: E402
from PIL import ImageGrab  # noqa: E402

from app.gui.main_window import JVMeasurementApp  # noqa: E402
from app.simulation.dummy_cell import DummyCell  # noqa: E402
from _isos_runner_helpers import FakeKeithley  # noqa: E402

OUT = os.path.join(ROOT, "docs", "screenshots")
os.makedirs(OUT, exist_ok=True)
TMP = os.path.join(ROOT, "isos_d1_simulation", "_gui_shot_tmp")
os.makedirs(TMP, exist_ok=True)


class DirectionalFakeKeithley(FakeKeithley):
    """FakeKeithley that infers scan direction so forward/reverse show hysteresis."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._last_v = 0.0
        self._dir = "reverse"

    def write(self, cmd: str) -> None:
        if cmd.startswith(":SOUR:VOLT"):
            try:
                nv = float(cmd.split()[1])
                self._dir = "forward" if nv >= self._last_v else "reverse"
                self._last_v = nv
            except (IndexError, ValueError):
                pass
        super().write(cmd)

    def query(self, cmd: str) -> str:
        if cmd == ":READ?" and self.func == "VOLT":
            j = self.cell.current_density(self.v, scan_direction=self._dir)
            raw_a = -(j * self.area / 1000.0)
            return f"{self.v:.6f},{raw_a:.9e},0,0,0"
        return super().query(cmd)


def grab_window(root, path):
    """Grab the maximized app at native screen resolution; return True on success."""
    root.update_idletasks()
    root.lift()
    root.attributes("-topmost", True)
    root.update()
    time.sleep(0.5)
    # The app is maximized (state 'zoomed') so it fills the work area. Grab the
    # full screen at native resolution, then crop to the toplevel's own bounds so
    # no taskbar / desktop bleeds in. all_screens=False → primary monitor only.
    full = ImageGrab.grab()
    x = max(root.winfo_rootx(), 0)
    y = max(root.winfo_rooty(), 0)
    w = root.winfo_width()
    h = root.winfo_height()
    img = full.crop((x, y, min(x + w, full.width), min(y + h, full.height)))
    img.save(path)
    root.attributes("-topmost", False)
    print(f"  grabbed window {img.size} -> {os.path.relpath(path, ROOT)}")
    return img.size[0] > 600 and img.size[1] > 400


def main():
    root = tk.Tk()
    app = JVMeasurementApp(root)

    # Maximize so the capture is at full native screen resolution (matches how
    # the app is actually used and avoids the down-scaled / "zoomed" look).
    try:
        root.state("zoomed")
    except Exception:
        root.attributes("-fullscreen", True)
    root.update()
    time.sleep(0.4)

    # ── Inject the simulated instrument ───────────────────────────────────────
    cell = DummyCell(mode="with_hysteresis", j_ph=22.5, j0=2e-12, n=1.45,
                     rs=4.0, rsh=6000.0, hi_fraction=0.10)
    app.instrument = DirectionalFakeKeithley(cell=cell, area=0.07, voc=1.12)
    app.instrument_idn = "KEITHLEY INSTRUMENTS INC.,MODEL 2400 (SIMULATED)"
    app.instrument_label_var.set(
        "✔  Connected: KEITHLEY MODEL 2400 (SIMULATED)  [GPIB0::21::INSTR]")
    for btn in ("start_btn", "light_jv_start_btn", "mpp_start_btn",
                "stab_start_btn", "pmpp_start_btn", "isos_start_btn"):
        try:
            getattr(app, btn).config(state="normal")
        except Exception:
            pass

    # ── Fill in J-V parameters ────────────────────────────────────────────────
    app.switch_mode("jv")
    app.sample_name_var.set("DemoCell_01")
    app.export_folder_var.set(TMP)
    app.detector_power_var.set(113.0)
    app.detector_area_var.set(1.13)     # → 100 mW/cm²
    app.start_voltage_var.set(-0.1)
    app.end_voltage_var.set(1.15)
    app.step_voltage_var.set(0.02)
    app.cell_area_var.set(0.07)
    app.delay_ms_var.set(20.0)
    app.nplc_var.set(1.0)
    app.dark_jv_var.set(False)
    app.light_soaking_var.set(False)
    app.prebias_enabled_var.set(False)
    app.auto_single_diode_fit_var.set(True)
    root.update()

    # ── Run a real measurement (synchronously) so the canvas draws curves ─────
    print("Running simulated J-V measurement…")
    app._measurement_running = True
    try:
        app.run_measurement()
    except Exception as exc:  # pragma: no cover
        print("  measurement error:", exc)
    root.update()
    time.sleep(0.3)
    root.update()

    # Always save the app's real figure (guaranteed good).
    fig_path = os.path.join(ROOT, "docs", "figures", "gui_jv_plot.png")
    try:
        app.fig.savefig(fig_path, dpi=110, bbox_inches="tight", facecolor="white")
        print("  saved app figure ->", os.path.relpath(fig_path, ROOT))
    except Exception as exc:
        print("  figure save failed:", exc)

    # Try to grab the full window (panels + curves).
    ok = False
    try:
        ok = grab_window(root, os.path.join(OUT, "01_jv_mode.png"))
    except Exception as exc:
        print("  window grab failed:", exc)

    # Re-grab the other mode panels for freshness (no curves needed).
    for mode, fname in [("stab", "03_stab_mode.png"),
                        ("mpp", "02_mpp_mode.png"),
                        ("pure_mpp", "04_pure_mpp_mode.png"),
                        ("curation", "05_curation_mode.png")]:
        try:
            app.switch_mode(mode)
            root.update()
            time.sleep(0.2)
            grab_window(root, os.path.join(OUT, fname))
        except Exception as exc:
            print(f"  {mode} grab failed:", exc)

    print("window-grab success:", ok)
    root.destroy()


if __name__ == "__main__":
    main()
