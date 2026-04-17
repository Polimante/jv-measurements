"""
Auto-update checker for JV Measurements software.

Fetches version.json from GitHub and compares with the local version.
Runs in a background thread so it never blocks the UI.

Usage:
    from app.utils.updater import check_for_updates, get_local_version
    check_for_updates(parent_window, silent=True)
"""

import json
import threading
import webbrowser
from tkinter import messagebox
from pathlib import Path

VERSION_URL = (
    "https://raw.githubusercontent.com/Polimante/jv-measurements/main/version.json"
)

_LOCAL_VERSION_FILE = Path(__file__).parent.parent.parent / "version.json"


def get_local_version():
    """Return the version string from the local version.json, or '0.0.0'."""
    try:
        with open(_LOCAL_VERSION_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("version", "0.0.0")
    except Exception:
        return "0.0.0"


def _parse_version(version_str):
    """Convert '2.5.1' → (2, 5, 1) for numeric comparison."""
    try:
        return tuple(int(x) for x in str(version_str).strip().split("."))
    except Exception:
        return (0, 0, 0)


def check_for_updates(parent, silent=False):
    """
    Check GitHub for a newer version.

    Parameters
    ----------
    parent : tk.Tk or tk.Toplevel
        Parent window used to anchor dialogs.
    silent : bool
        True  → only show a dialog when an update IS available (startup check).
        False → always show result (user clicked the menu item).
    """
    def _check():
        try:
            import requests
            response = requests.get(VERSION_URL, timeout=5)
            response.raise_for_status()
            remote       = response.json()
            remote_ver   = remote.get("version", "0.0.0")
            notes        = remote.get("release_notes", "")
            download_url = remote.get("download_url", "")
            rel_date     = remote.get("release_date", "")
            local_ver    = get_local_version()

            if _parse_version(remote_ver) > _parse_version(local_ver):
                msg = (
                    f"A new version of JV Measurements is available!\n\n"
                    f"  Installed:  {local_ver}\n"
                    f"  Available:  {remote_ver}  ({rel_date})\n\n"
                    f"What's new:\n{notes}\n\n"
                    f"Click OK to open the download page."
                )
                if messagebox.askokcancel("Update Available", msg, parent=parent):
                    if download_url:
                        webbrowser.open(download_url)
            else:
                if not silent:
                    messagebox.showinfo(
                        "No Updates Available",
                        f"JV Measurements is up to date.\n\nInstalled version: {local_ver}",
                        parent=parent,
                    )

        except ImportError:
            if not silent:
                messagebox.showwarning(
                    "Update Check Failed",
                    "The 'requests' library is not installed.\n"
                    "Run install.bat to fix this.",
                    parent=parent,
                )
        except Exception:
            if not silent:
                messagebox.showwarning(
                    "Update Check Failed",
                    "Could not reach GitHub to check for updates.\n"
                    "Please check your internet connection.",
                    parent=parent,
                )

    threading.Thread(target=_check, daemon=True).start()
