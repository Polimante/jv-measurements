from tkinter import Toplevel, Label, LEFT


class ToolTip:
    def __init__(self, widget, text):
        self.widget  = widget
        self.text    = text
        self.tooltip = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, event=None):
        x, y, _, _ = self.widget.bbox("insert")
        x += self.widget.winfo_rootx() + 25
        y += self.widget.winfo_rooty() + 25
        self.tooltip = Toplevel(self.widget)
        self.tooltip.wm_overrideredirect(True)
        self.tooltip.wm_geometry(f"+{x}+{y}")
        Label(self.tooltip, text=self.text, bg="lightyellow",
              relief="solid", borderwidth=1, justify=LEFT).pack()

    def hide(self, event=None):
        if self.tooltip:
            self.tooltip.destroy()
            self.tooltip = None
