from pathlib import Path
from tkinter import Tk
from app.gui.main_window import JVMeasurementApp

_ICON = Path(__file__).parent / "assets" / "icon.ico"

if __name__ == "__main__":
    root = Tk()
    if _ICON.exists():
        root.iconbitmap(str(_ICON))
    app  = JVMeasurementApp(root)
    root.protocol("WM_DELETE_WINDOW", app._on_exit)
    root.mainloop()
