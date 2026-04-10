import pyvisa
from tkinter import (
    ttk, Label, Frame, Button, StringVar,
    Toplevel, LEFT, RIGHT, BOTH, X, Y, W, END, DISABLED, NORMAL,
)


class InstrumentDialog(Toplevel):
    """
    Modal dialog that:
      1. Scans all VISA resources available on the system.
      2. Queries each one for its *IDN? response (identification string).
      3. Displays the results in a table (resource address | model string).
      4. Lets the user select one and click Connect.

    The dialog sets self.selected_resource and self.selected_idn before
    closing, which the caller reads.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Connect Instrument")
        self.geometry("620x380")
        self.resizable(False, False)
        self.grab_set()          # modal

        self.selected_resource = None
        self.selected_idn      = None

        self._build_ui()
        self._scan()             # auto-scan on open

    def _build_ui(self):
        Label(self, text="Available VISA instruments on this computer:",
              font=("Arial", 10, "bold")).pack(anchor=W, padx=10, pady=(10, 2))

        table_frame = Frame(self)
        table_frame.pack(fill=BOTH, expand=True, padx=10, pady=4)

        columns = ("resource", "idn")
        self.tree = ttk.Treeview(table_frame, columns=columns,
                                 show="headings", selectmode="browse")
        self.tree.heading("resource", text="VISA Resource Address")
        self.tree.heading("idn",      text="Instrument Identification (*IDN?)")
        self.tree.column("resource",  width=200, anchor=W)
        self.tree.column("idn",       width=370, anchor=W)

        vsb = ttk.Scrollbar(table_frame, orient="vertical",
                             command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        vsb.pack(side=RIGHT, fill=Y)

        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        self.scan_status_var = StringVar(value="")
        Label(self, textvariable=self.scan_status_var,
              fg="gray").pack(anchor=W, padx=10)

        btn_frame = Frame(self)
        btn_frame.pack(fill=X, padx=10, pady=8)

        self.rescan_btn = Button(btn_frame, text="\U0001f504  Rescan",
                                 command=self._scan, width=12)
        self.rescan_btn.pack(side=LEFT, padx=4)

        self.connect_btn = Button(btn_frame, text="\u2714  Connect",
                                  command=self._connect,
                                  bg="#4CAF50", fg="white",
                                  width=12, state=DISABLED)
        self.connect_btn.pack(side=LEFT, padx=4)

        Button(btn_frame, text="\u2716  Cancel",
               command=self.destroy,
               bg="#f44336", fg="white", width=12).pack(side=LEFT, padx=4)

    def _scan(self):
        """
        Enumerate VISA resources and query *IDN? for each.
        Runs synchronously (blocks the dialog briefly) because VISA
        timeouts are short (1 s) and user expects immediate feedback.
        """
        self.tree.delete(*self.tree.get_children())
        self.connect_btn.config(state=DISABLED)
        self.rescan_btn.config(state=DISABLED)
        self.scan_status_var.set("Scanning\u2026  please wait.")
        self.update()

        try:
            rm        = pyvisa.ResourceManager()
            resources = rm.list_resources()
        except Exception as exc:
            self.scan_status_var.set(f"VISA error: {exc}")
            self.rescan_btn.config(state=NORMAL)
            return

        found = 0
        for addr in resources:
            idn = self._query_idn(rm, addr)
            self.tree.insert("", END, values=(addr, idn))
            found += 1

        if found == 0:
            self.scan_status_var.set(
                "No instruments found. Check GPIB/USB connections and drivers.")
        else:
            self.scan_status_var.set(
                f"{found} instrument(s) found. Select one and click Connect.")

        self.rescan_btn.config(state=NORMAL)

    @staticmethod
    def _query_idn(rm, addr):
        """Try to open a resource and query *IDN?. Return the response or an error string."""
        try:
            res = rm.open_resource(addr)
            res.timeout = 1500
            idn = res.query("*IDN?").strip()
            res.close()
            return idn if idn else "(no response)"
        except Exception as exc:
            return f"(error: {exc})"

    def _on_select(self, event=None):
        if self.tree.selection():
            self.connect_btn.config(state=NORMAL)

    def _connect(self):
        sel = self.tree.selection()
        if not sel:
            return
        values = self.tree.item(sel[0], "values")
        self.selected_resource = values[0]
        self.selected_idn      = values[1]
        self.destroy()
