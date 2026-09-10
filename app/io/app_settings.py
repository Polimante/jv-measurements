"""
User preferences that outlive a session — ``~/.jv-measurement/settings.json``.

Deliberately tiny and Tk-free so it can be unit-tested and imported from
anywhere. Sits alongside ``isos_recovery.recent_folders_path()`` in the same
``~/.jv-measurement`` directory.

The file is a flat dict of named sections, e.g.::

    {"shutter": {"backend": "keithley_digio", "options": {...}}}

Every read tolerates a missing, unreadable or corrupt file by returning the
caller's default: a bad settings file must never stop the application from
starting. Writes are atomic (temp file + ``os.replace``) so an interrupted save
cannot leave a half-written file behind.
"""

from __future__ import annotations

import json
import os

_SETTINGS_FILE = "settings.json"


def settings_dir() -> str:
    """Return the directory holding the settings file (may not exist yet)."""
    return os.path.join(os.path.expanduser("~"), ".jv-measurement")


def settings_path() -> str:
    """Return the full path to ``~/.jv-measurement/settings.json``."""
    return os.path.join(settings_dir(), _SETTINGS_FILE)


def load_settings() -> dict:
    """Return the whole settings dict, or ``{}`` if unreadable or corrupt."""
    try:
        with open(settings_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_settings(data: dict) -> bool:
    """Write the whole settings dict atomically. Returns True on success.

    Never raises: persistence of a preference is not worth crashing over.
    """
    if not isinstance(data, dict):
        return False
    path = settings_path()
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)          # atomic on Windows and POSIX
        return True
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def load_section(name: str, default: dict | None = None) -> dict:
    """Return one named section, or *default* (or ``{}``) when absent."""
    section = load_settings().get(name)
    if isinstance(section, dict):
        return section
    return dict(default) if default else {}


def save_section(name: str, value: dict) -> bool:
    """Merge one named section into the settings file. Returns True on success.

    Reads the current file first so a section written by another part of the
    application (or a newer version) is preserved.
    """
    if not isinstance(value, dict):
        return False
    data = load_settings()
    data[name] = value
    return save_settings(data)
