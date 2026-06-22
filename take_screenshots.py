"""
Launches the app, waits for it to load, then cycles through each mode
capturing a screenshot. Saves images to docs/screenshots/.
Run from the project root: python take_screenshots.py
"""

import subprocess
import sys
import time
from pathlib import Path

import pyautogui
from PIL import ImageGrab

OUT = Path(__file__).parent / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05


def screenshot(name: str):
    img = ImageGrab.grab()
    path = OUT / name
    img.save(path)
    print(f"  saved {path.name}  ({img.size[0]}×{img.size[1]})")


def focus_app():
    """Bring the JV Measurements window to foreground."""
    import ctypes
    import ctypes.wintypes

    EnumWindows = ctypes.windll.user32.EnumWindows
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    GetWindowText = ctypes.windll.user32.GetWindowTextW
    GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW
    SetForegroundWindow = ctypes.windll.user32.SetForegroundWindow
    IsWindowVisible = ctypes.windll.user32.IsWindowVisible

    found = []

    def callback(hwnd, _):
        if IsWindowVisible(hwnd):
            length = GetWindowTextLength(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                GetWindowText(hwnd, buf, length + 1)
                title = buf.value
                if "Solar Cell" in title or "JV Measurement" in title:
                    found.append(hwnd)
        return True

    EnumWindows(EnumWindowsProc(callback), 0)
    if found:
        SetForegroundWindow(found[0])
        return True
    return False


print("Launching app…")
proc = subprocess.Popen(
    [sys.executable, "main.py"],
    cwd=str(Path(__file__).parent),
)

print("Waiting for app to load…")
time.sleep(4)

if not focus_app():
    print("  Could not find app window — make sure it is open and visible.")
    proc.terminate()
    sys.exit(1)

time.sleep(0.5)

shots = [
    ("alt+F2", "01_jv_mode.png",       "J-V Measurement mode"),
    ("alt+F3", "02_mpp_mode.png",       "MPP Tracking (JV-SPO) mode"),
    ("alt+F5", "03_stab_mode.png",      "Stabilized Current mode"),
    ("alt+F6", "04_pure_mpp_mode.png",  "Pure MPP Tracking mode"),
    ("alt+F7", "05_curation_mode.png",  "Data Curation mode"),
]

for shortcut, filename, label in shots:
    print(f"Switching to {label}…")
    pyautogui.hotkey(*shortcut.split("+"))
    time.sleep(1.0)
    focus_app()
    time.sleep(0.3)
    screenshot(filename)

print("\nAll screenshots saved to docs/screenshots/")
print("Closing app…")
proc.terminate()
