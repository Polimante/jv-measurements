"""
Auto-update checker and installer for JV Measurements.

Fetches version.json from GitHub, compares with local version, and can
download + apply the update in-place by extracting the release zip over
the installation directory.

Usage:
    from app.utils.updater import check_for_updates, get_local_version
    check_for_updates(parent_window, silent=True)
"""

import json
import os
import shutil
import tempfile
import threading
import webbrowser
import zipfile
from pathlib import Path
from tkinter import messagebox, ttk
import tkinter as tk

VERSION_URL = (
    "https://raw.githubusercontent.com/Polimante/jv-measurements/main/version.json"
)

_INSTALL_DIR        = Path(__file__).parent.parent.parent.resolve()
_LOCAL_VERSION_FILE = _INSTALL_DIR / "version.json"

# Folders/files to never overwrite during an in-place update
_UPDATE_SKIP = {"__pycache__", ".git", "build_installer", "dist",
                ".gitignore", ".claude"}


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


# ── Progress dialog ───────────────────────────────────────────────────────────

class _ProgressDialog:
    def __init__(self, parent, title):
        self.top = tk.Toplevel(parent)
        self.top.title(title)
        self.top.resizable(False, False)
        self.top.grab_set()
        self.top.protocol("WM_DELETE_WINDOW", lambda: None)  # block close

        tk.Label(self.top, text=title,
                 font=("Arial", 10, "bold"), padx=24, pady=10).pack()

        self.status_var = tk.StringVar(value="Connecting…")
        tk.Label(self.top, textvariable=self.status_var,
                 font=("Arial", 9), fg="#555555", padx=24).pack()

        self.pb = ttk.Progressbar(self.top, mode="indeterminate", length=320)
        self.pb.pack(padx=24, pady=(6, 18))
        self.pb.start(10)

        # Centre over parent
        self.top.update_idletasks()
        px = parent.winfo_rootx() + (parent.winfo_width()  - self.top.winfo_width())  // 2
        py = parent.winfo_rooty() + (parent.winfo_height() - self.top.winfo_height()) // 2
        self.top.geometry(f"+{px}+{py}")

    def set_status(self, msg):
        self.status_var.set(msg)

    def close(self):
        self.pb.stop()
        self.top.grab_release()
        self.top.destroy()


# ── In-place update ───────────────────────────────────────────────────────────

def _apply_update(parent, remote_ver, zip_url):
    """Download the release zip and extract it over the installation directory."""
    dlg = _ProgressDialog(parent, f"Updating to v{remote_ver}…")

    def _worker():
        tmp_zip = None
        try:
            import requests

            # ── Download ──────────────────────────────────────────────────────
            parent.after(0, dlg.set_status, "Downloading update…")
            resp = requests.get(zip_url, timeout=120, stream=True)
            resp.raise_for_status()

            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
                tmp_zip = f.name
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)

            # ── Extract to temp dir ───────────────────────────────────────────
            parent.after(0, dlg.set_status, "Extracting files…")
            with tempfile.TemporaryDirectory() as extract_dir:
                with zipfile.ZipFile(tmp_zip, "r") as zf:
                    zf.extractall(extract_dir)

                # GitHub archive zips have one top-level folder:
                # jv-measurements-2.6.0/
                entries = os.listdir(extract_dir)
                src_root = os.path.join(extract_dir, entries[0]) \
                    if entries else extract_dir

                # ── Copy files over install dir ───────────────────────────────
                parent.after(0, dlg.set_status, "Applying update…")
                for item in os.listdir(src_root):
                    if item in _UPDATE_SKIP:
                        continue
                    src  = os.path.join(src_root, item)
                    dest = str(_INSTALL_DIR / item)
                    if os.path.isdir(src):
                        if os.path.exists(dest):
                            shutil.rmtree(dest)
                        shutil.copytree(
                            src, dest,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                    else:
                        shutil.copy2(src, dest)

        except Exception as exc:
            parent.after(0, dlg.close)
            parent.after(0, messagebox.showerror,
                "Update Failed",
                f"Could not apply the update:\n\n{exc}\n\n"
                "Please update manually by downloading the latest installer\n"
                "from the GitHub Releases page.")
            return
        finally:
            if tmp_zip and os.path.exists(tmp_zip):
                try:
                    os.unlink(tmp_zip)
                except Exception:
                    pass

        parent.after(0, dlg.close)
        parent.after(0, messagebox.showinfo,
            "Update Complete",
            f"JV Measurements has been updated to v{remote_ver}.\n\n"
            "Please close and reopen the application to apply the changes.")

    threading.Thread(target=_worker, daemon=True).start()


# ── Public API ────────────────────────────────────────────────────────────────

def check_for_updates(parent, silent=False):
    """
    Check GitHub for a newer version.

    Parameters
    ----------
    parent : tk.Tk or tk.Toplevel
        Parent window used to anchor dialogs.
    silent : bool
        True  → only show a dialog when an update IS available (startup check).
        False → always show result (user clicked Help → Check for Updates).
    """
    def _check():
        try:
            import requests
            response = requests.get(VERSION_URL, timeout=5)
            response.raise_for_status()
            remote     = response.json()
            remote_ver = remote.get("version", "0.0.0")
            notes      = remote.get("release_notes", "")
            zip_url    = remote.get("zip_url", "")
            rel_date   = remote.get("release_date", "")
            local_ver  = get_local_version()

            if _parse_version(remote_ver) > _parse_version(local_ver):
                msg = (
                    f"A new version of JV Measurements is available!\n\n"
                    f"  Installed : {local_ver}\n"
                    f"  Available : {remote_ver}  ({rel_date})\n\n"
                    f"What's new:\n{notes}\n\n"
                    f"Update now? The application will restart automatically."
                )
                if messagebox.askyesno("Update Available", msg, parent=parent):
                    if zip_url:
                        parent.after(0, _apply_update, parent, remote_ver, zip_url)
                    else:
                        webbrowser.open(
                            remote.get("download_url",
                                       "https://github.com/Polimante/jv-measurements/releases"))
            else:
                if not silent:
                    messagebox.showinfo(
                        "No Updates Available",
                        f"JV Measurements is up to date.\n\nInstalled version: {local_ver}",
                        parent=parent)

        except ImportError:
            if not silent:
                messagebox.showwarning(
                    "Update Check Failed",
                    "The 'requests' library is not installed.\n"
                    "Run install.bat to fix this.",
                    parent=parent)
        except Exception:
            if not silent:
                messagebox.showwarning(
                    "Update Check Failed",
                    "Could not reach GitHub to check for updates.\n"
                    "Please check your internet connection.",
                    parent=parent)

    threading.Thread(target=_check, daemon=True).start()
