"""
Crash recovery for interrupted ISOS stress runs.

Continuous protocols (L-1 / V-1) stream every stress sample to a durable
``*_stress_inprogress_*.csv`` file (see ``app.io.isos_autosave.CSVStream``). On a
clean finish the runner consolidates that CSV into the tracking workbook and
renames it ``.consumed``. If the application is killed mid-run the CSV is left
behind — an *orphan*. On the next launch we scan the user's recent ISOS folders,
pair each orphan with its tracking workbook, and offer to consolidate it.

This module is intentionally Tk-free so the detection / consolidation logic is
unit-testable headlessly. The GUI recovery dialog in ``main_window.py`` is a thin
wrapper over these functions.
"""

from __future__ import annotations

import glob
import json
import os
from datetime import datetime

from app.io.isos_autosave import consolidate_csv_into_xlsx

# Marker substring in an in-progress autosave CSV file name.
_INPROGRESS_MARK = "_stress_inprogress_"


# ─────────────────────────────────────────────────────────────────────────────
# Recent-folder persistence
# ─────────────────────────────────────────────────────────────────────────────

def recent_folders_path() -> str:
    """Return the path to the recent-folders JSON file (~/.jv-measurement/)."""
    return os.path.join(os.path.expanduser("~"), ".jv-measurement",
                        "recent_folders.json")


def load_recent_folders() -> list:
    """Return the saved list of recent ISOS folders (most-recent-first).

    Tolerates a missing or corrupt file by returning an empty list.
    """
    path = recent_folders_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return [str(p) for p in data if p]
    except Exception:
        pass
    return []


def add_recent_folder(folder: str, limit: int = 10) -> list:
    """Record *folder* as the most-recently-used ISOS folder.

    Deduplicates (case-insensitively on Windows via ``os.path.normcase``), keeps
    the most-recent entry first, caps the list at *limit*, and creates the
    ``~/.jv-measurement`` directory if needed. Returns the updated list. Any I/O
    failure is swallowed (recovery is best-effort, never fatal to startup).
    """
    if not folder:
        return load_recent_folders()
    folder = os.path.abspath(folder)
    existing = load_recent_folders()
    key = os.path.normcase(folder)
    deduped = [p for p in existing if os.path.normcase(p) != key]
    updated = [folder] + deduped
    updated = updated[:max(1, int(limit))]
    try:
        path = recent_folders_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(updated, fh, indent=2)
    except Exception:
        pass
    return updated


# ─────────────────────────────────────────────────────────────────────────────
# Orphan detection
# ─────────────────────────────────────────────────────────────────────────────

def _sample_prefix(csv_name: str) -> str:
    """Extract the sample-name prefix that precedes ``_stress_inprogress_``."""
    idx = csv_name.find(_INPROGRESS_MARK)
    return csv_name[:idx] if idx > 0 else ""


def _find_tracking_xlsx(folder: str, sample_prefix: str) -> str | None:
    """Return the tracking workbook paired with *sample_prefix* in *folder*.

    A tracking file is ``{sample}_{label}_{ts}.xlsx`` (see
    ``isos_tracking.create_isos_tracking_file``), so we match ``.xlsx`` files
    whose basename starts with the sample prefix. When several match (re-runs of
    the same sample) the most recently modified wins.
    """
    if not sample_prefix:
        return None
    candidates = []
    for xlsx in glob.glob(os.path.join(folder, "*.xlsx")):
        base = os.path.basename(xlsx)
        if base.startswith(sample_prefix + "_"):
            candidates.append(xlsx)
    if not candidates:
        return None
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[0]


def find_orphan_stress_csvs(folders, include_unpaired: bool = False) -> list:
    """Find orphaned in-progress stress CSVs across *folders*.

    Returns a list of ``(csv_path, xlsx_path)`` tuples. ``xlsx_path`` is the
    paired tracking workbook, or ``None`` when none was found. By default only
    paired orphans are returned (per the recovery spec); pass
    ``include_unpaired=True`` to also surface CSVs with no matching workbook so a
    caller can still offer to discard them.

    A file qualifies as an orphan when its name contains
    ``_stress_inprogress_`` and ends in ``.csv`` (i.e. not already
    ``.consumed`` / ``.discarded``).
    """
    seen = set()
    out = []
    for folder in folders or []:
        if not folder or not os.path.isdir(folder):
            continue
        try:
            csvs = glob.glob(os.path.join(folder, f"*{_INPROGRESS_MARK}*.csv"))
        except Exception:
            continue
        for csv_path in csvs:
            if not csv_path.lower().endswith(".csv"):
                continue
            real = os.path.normcase(os.path.abspath(csv_path))
            if real in seen:
                continue
            seen.add(real)
            prefix = _sample_prefix(os.path.basename(csv_path))
            xlsx = _find_tracking_xlsx(folder, prefix)
            if xlsx is None and not include_unpaired:
                continue
            out.append((csv_path, xlsx))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Recovery actions
# ─────────────────────────────────────────────────────────────────────────────

def consolidate_orphan(csv_path: str, xlsx_path: str,
                       sheet_name: str | None = None) -> str:
    """Fold an orphan CSV into its tracking workbook and mark it consumed.

    Delegates to :func:`app.io.isos_autosave.consolidate_csv_into_xlsx`, which
    appends a new sheet and renames the CSV ``.consumed``. Returns the sheet name
    used. Raises if the workbook cannot be written (caller summarizes errors).
    """
    if sheet_name is None:
        sheet_name = f"Stress_recovered_{datetime.now().strftime('%H%M%S')}"
    consolidate_csv_into_xlsx(csv_path, xlsx_path, sheet_name)
    return sheet_name


def discard_orphan(csv_path: str) -> str:
    """Rename an orphan CSV to ``<csv_path>.discarded`` (preserve, don't delete).

    Returns the new path. A pre-existing ``.discarded`` file is replaced.
    """
    discarded = csv_path + ".discarded"
    if os.path.exists(discarded):
        os.remove(discarded)
    os.replace(csv_path, discarded)
    return discarded
