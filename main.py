from tkinter import Tk
from app.gui.main_window import JVMeasurementApp

if __name__ == "__main__":
    root = Tk()
    app  = JVMeasurementApp(root)
    root.protocol("WM_DELETE_WINDOW", app._on_exit)
    root.mainloop()
