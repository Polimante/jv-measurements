"""
Shared MPP / P&O parameter-grid helper.

Both the MPP Tracking panel and the Pure MPP Tracking panel build the same
label / entry / unit grid of numeric parameters.  This module factors that
loop into one reusable function so the two panels stay in sync.

Each row is a Tk ``DoubleVar`` bound onto the owning window as
``owner.<prefix>_<key>_var`` — preserving the historical attribute names
(e.g. ``self.pmpp_step_mv_var`` for prefix ``"pmpp"`` and key ``"step_mv"``)
so the rest of ``main_window.py`` keeps working unchanged.
"""

from tkinter import LabelFrame, Label, Entry, DoubleVar, W

from app.gui.tooltip import ToolTip


def build_param_grid(parent, prefix, rows, owner):
    """Populate *parent* with a label / entry / unit grid.

    Args:
        parent: Tk container the widgets are gridded into.
        prefix: attribute-name prefix, e.g. ``"pmpp"`` or ``"mpp"``.
        rows: iterable of ``(label, key, default, unit)`` or
            ``(label, key, default, unit, tooltip)`` tuples.
        owner: object the created vars are bound onto as
            ``owner.<prefix>_<key>_var``.

    Returns:
        dict mapping each *key* to its ``DoubleVar``.
    """
    vars_ = {}
    for row, spec in enumerate(rows):
        label, key, default, unit = spec[0], spec[1], spec[2], spec[3]
        tip = spec[4] if len(spec) > 4 else None

        Label(parent, text=label).grid(row=row, column=0, sticky=W, padx=4, pady=3)
        var = DoubleVar(value=default)
        setattr(owner, f"{prefix}_{key}_var", var)
        entry = Entry(parent, textvariable=var, width=10)
        entry.grid(row=row, column=1, sticky=W, padx=4, pady=3)
        Label(parent, text=unit, fg="#666666").grid(row=row, column=2, sticky=W)
        if tip:
            ToolTip(entry, tip)
        vars_[key] = var
    return vars_


def build_mpp_settings_frame(parent, prefix, defaults, owner,
                             title="Tracking Parameters"):
    """Create a titled ``LabelFrame`` filled with a parameter grid.

    Args:
        parent: Tk container the frame is created in (caller packs/grids it).
        prefix: attribute-name prefix (see :func:`build_param_grid`).
        defaults: ordered list of row specs (see :func:`build_param_grid`).
        owner: object the vars are bound onto.
        title: ``LabelFrame`` heading.

    Returns:
        ``(frame, vars_dict)`` — the unpacked frame and the key→var mapping.
    """
    frame = LabelFrame(parent, text=title, padx=6, pady=4)
    vars_ = build_param_grid(frame, prefix, defaults, owner)
    return frame, vars_
