from pathlib import Path
from tkinter import Tk
from app.gui.main_window import JVMeasurementApp

_ICON = Path(__file__).parent / "assets" / "icon.ico"

# Tell Windows to use our icon in the taskbar instead of the generic Python one.
# Must be called before the Tk window is created.
try:
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
        "UFABC.JVMeasurements")
except Exception:
    pass

if __name__ == "__main__":
    root = Tk()
    app  = JVMeasurementApp(root)          # calls setup_gui → state("zoomed")
    if _ICON.exists():
        root.iconbitmap(str(_ICON))        # set after zoomed so state is preserved
    root.protocol("WM_DELETE_WINDOW", app._on_exit)
    root.mainloop()
