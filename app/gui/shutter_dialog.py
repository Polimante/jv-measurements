"""
The Shutter Control window (Instrument → Shutter Control…).

It lives on the menu bar rather than inside any one measurement panel because
the shutter is a property of the *rig*, not of a procedure: once configured it
serves J-V, Light Intensity, MPP, Stabilized Current, Pure MPP and every ISOS
protocol alike.

The settings form is generated from the selected backend's
``BackendSpec.fields`` tuple, so this file names no backend's options and
supporting new shutter hardware needs no GUI code — see app/shutter/__init__.py.
"""

from __future__ import annotations

from tkinter import (
    ttk, Button, Checkbutton, Entry, Frame, Label, LabelFrame, StringVar,
    BooleanVar, Toplevel, messagebox,
    LEFT, RIGHT, W, E, X, BOTH,
)

from app.gui.tooltip import ToolTip
from app.shutter import CLOSED, OPEN, ShutterError, available_backends


class ShutterDialog(Toplevel):
    """Configure, test and monitor the shutter.

    Args:
        parent: the Tk root.
        manager: the application's :class:`~app.shutter.manager.ShutterManager`.
        is_busy: ``() -> bool`` — True (having told the user why) when a
            measurement is using the instrument and the shutter must be left
            alone.
        state_var: the app's shutter-state StringVar, so the badge here follows
            state changes coming from anywhere, including measurement threads.
    """

    def __init__(self, parent, manager, is_busy, state_var) -> None:
        super().__init__(parent)
        self.title("Shutter Control")
        self.transient(parent)
        self.resizable(False, False)

        self._manager = manager
        self._is_busy = is_busy
        self._field_vars = {}
        self._specs = {s.label: s for s in available_backends()}

        self.result_var = StringVar(value="")
        self._build_ui(state_var)
        self._rebuild_fields()
        self.set_badge_colour(self._colour_for(manager))

    # ── Construction ─────────────────────────────────────────────────────

    def _build_ui(self, state_var) -> None:
        outer = Frame(self, padx=12, pady=10)
        outer.pack(fill=BOTH, expand=True)

        head = Frame(outer)
        head.pack(fill=X)
        Label(head, text="System:").pack(side=LEFT)

        current = self._manager.spec
        self.backend_var = StringVar(value=current.label)
        combo = ttk.Combobox(head, textvariable=self.backend_var,
                             state="readonly", width=32,
                             values=list(self._specs))
        combo.pack(side=LEFT, padx=(6, 12))
        combo.bind("<<ComboboxSelected>>", self._on_backend_selected)
        ToolTip(combo,
                "Which hardware moves the shutter.\n\n"
                "Manual: the application prompts you at each dark/illuminated\n"
                "boundary — the behaviour this software has always had.\n\n"
                "Other backends actuate it themselves, are closed automatically\n"
                "when you connect the instrument, and are closed again after\n"
                "every measurement.\n\n"
                "The setting applies to every measurement mode, not just J-V.")

        self.badge = Label(head, textvariable=state_var,
                           font=("Arial", 9, "bold"), fg="#757575")
        self.badge.pack(side=RIGHT)

        self.desc_var = StringVar(value=current.description)
        Label(outer, textvariable=self.desc_var, fg="#666666", justify=LEFT,
              wraplength=470).pack(fill=X, pady=(8, 4))

        self._fields_frame = LabelFrame(outer, text="Settings", padx=8, pady=6)
        self._fields_frame.pack(fill=X, pady=(2, 6))

        self._build_verification(outer)

        btns = Frame(outer)
        btns.pack(fill=X, pady=(2, 4))
        Button(btns, text="Apply & Save",
               command=lambda: self.apply()).pack(side=LEFT)
        Button(btns, text="Test: Open",
               command=lambda: self.test("open")).pack(side=LEFT, padx=(10, 0))
        Button(btns, text="Test: Close",
               command=lambda: self.test("close")).pack(side=LEFT, padx=(4, 0))
        Button(btns, text="Self-test",
               command=self.self_test).pack(side=LEFT, padx=(10, 0))
        Button(btns, text="Close", command=self.destroy).pack(side=RIGHT)

        Label(outer, textvariable=self.result_var, fg="#666666", justify=LEFT,
              wraplength=470).pack(fill=X, pady=(4, 0))

    def _build_verification(self, parent) -> None:
        """The backend-independent 'did the light actually change?' check."""
        frame = LabelFrame(parent, text="Verification", padx=8, pady=6)
        frame.pack(fill=X, pady=(0, 6))

        config = self._manager.config
        self.verify_var = BooleanVar(
            value=bool(config.get("verify_with_current", False)))
        check = Checkbutton(
            frame, text="Confirm the light state with an SMU reading",
            variable=self.verify_var)
        check.grid(row=0, column=0, columnspan=3, sticky=W)
        ToolTip(check,
                "After moving the shutter, read the cell current at 0 V and\n"
                "check it matches: bright when opened, dark when closed.\n\n"
                "This is the only closed-loop check available — the shutter can\n"
                "only report what it was told to do. It catches the dangerous\n"
                "case: a 'dark' scan that silently ran illuminated looks\n"
                "perfectly plausible in the data.\n\n"
                "Needs a cell connected and the lamp on, so it is off by\n"
                "default. A mismatch asks whether to continue or abort.")

        Label(frame, text="Illuminated above:").grid(row=1, column=0, sticky=W,
                                                     pady=(4, 0))
        self.threshold_var = StringVar(
            value=f"{float(config.get('verify_threshold_a', 1e-6)) * 1e3:g}")
        entry = Entry(frame, textvariable=self.threshold_var, width=12)
        entry.grid(row=1, column=1, sticky=W, padx=6, pady=(4, 0))
        Label(frame, text="mA", fg="#666666").grid(row=1, column=2, sticky=W,
                                                   pady=(4, 0))
        ToolTip(entry,
                "Current at 0 V above which the cell counts as illuminated.\n\n"
                "A dark cell reads nanoamps and a lit one milliamps, so\n"
                "anything in between works. The default of 0.001 mA (1 uA)\n"
                "sits three orders of magnitude from both.")

    def _collect_verification(self) -> dict:
        """Read the verification frame. Raises ValueError with a user message."""
        raw = self.threshold_var.get()
        try:
            threshold_ma = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"Illuminated above: expected a number, got {raw!r}")
        if threshold_ma <= 0:
            raise ValueError("Illuminated above: must be greater than zero")
        return {
            "verify_with_current": bool(self.verify_var.get()),
            "verify_threshold_a": threshold_ma / 1e3,
        }

    # ── Form ─────────────────────────────────────────────────────────────

    @property
    def selected_spec(self):
        return self._specs.get(self.backend_var.get(), self._manager.spec)

    @property
    def field_vars(self) -> dict:
        """``{name: (ConfigField, tk var)}`` for the visible form."""
        return self._field_vars

    def _on_backend_selected(self, _event=None) -> None:
        self.desc_var.set(self.selected_spec.description)
        self._rebuild_fields()
        self.result_var.set("")
        self.apply(announce=False)

    def _rebuild_fields(self) -> None:
        """Render the selected backend's settings form from its field specs."""
        for child in self._fields_frame.winfo_children():
            child.destroy()
        self._field_vars = {}

        spec = self.selected_spec
        if not spec.fields:
            Label(self._fields_frame, text="This backend has no settings.",
                  fg="#666666").grid(row=0, column=0, sticky=W)
            return

        stored = (self._manager.config.get("options") or {}).get(spec.id, {})
        for row, fld in enumerate(spec.fields):
            value = stored.get(fld.name, fld.default)
            if fld.kind == "bool":
                var = BooleanVar(value=bool(value))
                widget = Checkbutton(self._fields_frame, text=fld.label,
                                     variable=var)
                widget.grid(row=row, column=0, columnspan=3, sticky=W, pady=2)
            else:
                Label(self._fields_frame, text=f"{fld.label}:").grid(
                    row=row, column=0, sticky=W, pady=2)
                var = StringVar(value=str(value))
                if fld.kind == "choice":
                    widget = ttk.Combobox(self._fields_frame, textvariable=var,
                                          state="readonly",
                                          values=list(fld.choices), width=14)
                else:
                    widget = Entry(self._fields_frame, textvariable=var, width=12)
                widget.grid(row=row, column=1, sticky=W, padx=6, pady=2)
                if fld.unit:
                    Label(self._fields_frame, text=fld.unit, fg="#666666").grid(
                        row=row, column=2, sticky=W)
            if fld.tooltip:
                ToolTip(widget, fld.tooltip)
            self._field_vars[fld.name] = (fld, var)

    def _collect_options(self) -> dict:
        """Read and validate the form. Raises ValueError with a user message."""
        return {name: fld.coerce(var.get())
                for name, (fld, var) in self._field_vars.items()}

    # ── Actions ──────────────────────────────────────────────────────────

    def apply(self, announce: bool = True) -> bool:
        """Validate the form, activate the backend and persist it."""
        if self._is_busy():
            return False
        spec = self.selected_spec
        try:
            options = self._collect_options()
            verification = self._collect_verification()
        except ValueError as exc:
            messagebox.showerror("Shutter settings", str(exc), parent=self)
            return False

        config = {
            "backend": spec.id,
            "options": dict(self._manager.config.get("options") or {}),
        }
        config["options"][spec.id] = options
        config.update(verification)
        self._manager.configure(config)
        saved = self._manager.save_config()
        if announce:
            self.result_var.set(
                self._manager.describe() +
                ("" if saved else "  (could not be saved to settings.json)"))
        return True

    def test(self, action: str) -> None:
        """Apply what is on screen, then actually move the shutter."""
        if not self.apply(announce=False):
            return
        try:
            (self._manager.open if action == "open" else self._manager.close)()
        except ShutterError as exc:
            self.result_var.set(f"Test '{action}' failed.")
            messagebox.showerror("Shutter test failed", str(exc), parent=self)
            return
        self.result_var.set(
            f"Test '{action}' succeeded — {self._manager.describe()}")

    def self_test(self) -> None:
        """Close → open → close, timing each step."""
        if not self.apply(announce=False):
            return
        try:
            report = self._manager.self_test()
        except ShutterError as exc:
            self.result_var.set("Self-test failed.")
            messagebox.showerror("Shutter self-test failed", str(exc),
                                 parent=self)
            return
        self.result_var.set(f"Self-test: {report}")
        messagebox.showinfo(
            "Shutter self-test",
            f"{self._manager.describe()}\n\n{report}\n\n"
            "The shutter is now closed.", parent=self)

    # ── Badge ────────────────────────────────────────────────────────────

    @staticmethod
    def _colour_for(manager) -> str:
        if not manager.is_automatic or not manager.ready:
            return "#757575"
        return {OPEN: "#E65100", CLOSED: "#2E7D32"}.get(manager.state, "#757575")

    def set_badge_colour(self, colour: str) -> None:
        try:
            self.badge.config(fg=colour)
        except Exception:
            pass          # window already destroyed
