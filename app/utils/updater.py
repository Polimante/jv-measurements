"""
Auto-update checker and installer for JV Measurements.

Fetches version.json from GitHub, compares with local version, and can
download + apply the update in-place by extracting the release zip over
the installation directory.

The update is applied in an order that never leaves a half-working
installation behind:

1. download and extract the release zip to a temporary folder;
2. compare the release's ``requirements.txt`` with the installed one. An
   in-place update only copies files, so a release that needs a different
   set of libraries (scipy was one) would copy code that cannot even be
   imported. Such a release is **not** applied in place: the operator is
   sent to the installer, whose ``install.bat`` installs the libraries;
3. check that the installation folder is writable (an "all users" install
   under Program Files is not, without administrator rights);
4. only then copy the files over the installation.

A failure at steps 1-3 leaves the installed version untouched.

Every dialog is shown on the Tk thread: the network and file work runs on
a daemon thread and posts back through ``parent.after``.

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
RELEASES_URL = "https://github.com/Polimante/jv-measurements/releases"

_INSTALL_DIR        = Path(__file__).parent.parent.parent.resolve()
_LOCAL_VERSION_FILE = _INSTALL_DIR / "version.json"

# Folders/files to never overwrite during an in-place update
_UPDATE_SKIP = {"__pycache__", ".git", "build_installer", "dist",
                ".gitignore", ".claude"}


class UpdateError(Exception):
    """An update step failed; the message is shown to the operator as-is."""


class NeedsInstaller(UpdateError):
    """The release cannot be applied in place; the installer is required."""


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


# ── Tk-free update steps ──────────────────────────────────────────────────────

def _release_root(extract_dir):
    """The folder holding the release files inside an extracted zip.

    GitHub archive zips have one top-level folder (``jv-measurements-2.6.0/``).
    """
    entries = os.listdir(extract_dir)
    if len(entries) == 1 and os.path.isdir(os.path.join(extract_dir, entries[0])):
        return os.path.join(extract_dir, entries[0])
    return extract_dir


def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _requirement_lines(text):
    if text is None:
        return None
    return {ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("#")}


def _new_requirements(new_req, installed_req):
    """Requirement lines the release has that the installation does not.

    Blank lines, comments and order are ignored. A release with no
    requirements file needs nothing new; an installation with none is
    treated as needing every line of the release's.
    """
    new = _requirement_lines(_read_text(new_req))
    if not new:
        return []
    have = _requirement_lines(_read_text(installed_req)) or set()
    return sorted(new - have)


def _check_writable(install_dir):
    """Raise UpdateError unless files can be created in *install_dir*."""
    try:
        fd, probe = tempfile.mkstemp(prefix=".update_probe_", dir=str(install_dir))
        os.close(fd)
        os.unlink(probe)
    except OSError as exc:
        raise UpdateError(
            f"The installation folder is not writable:\n{install_dir}\n\n"
            "This happens when JV Measurements was installed for all users "
            "(under Program Files). Run the application once as administrator "
            "to update, or reinstall it for the current user only.\n\n"
            f"({exc})") from exc


def _copy_release(src_root, install_dir):
    """Copy every release file over *install_dir*, except ``_UPDATE_SKIP``."""
    for item in os.listdir(src_root):
        if item in _UPDATE_SKIP:
            continue
        src  = os.path.join(src_root, item)
        dest = os.path.join(str(install_dir), item)
        if os.path.isdir(src):
            if os.path.exists(dest):
                shutil.rmtree(dest)
            shutil.copytree(
                src, dest,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, dest)


def apply_release(src_root, install_dir, status=lambda msg: None):
    """Steps 2-4 of the update, on an already-extracted release.

    Raises NeedsInstaller or UpdateError before touching *install_dir*.
    """
    status("Checking dependencies…")
    missing = _new_requirements(
        os.path.join(src_root, "requirements.txt"),
        os.path.join(str(install_dir), "requirements.txt"))
    if missing:
        raise NeedsInstaller(
            "This version needs Python libraries that are not installed yet:\n"
            + "".join(f"  • {m}\n" for m in missing)
            + "\nAn in-place update cannot install them, so it was not applied "
              "and your installed version is unchanged.\n\n"
              "Please download and run the latest installer, which installs "
              "the libraries for you.")
    status("Checking the installation folder…")
    _check_writable(install_dir)
    status("Applying update…")
    _copy_release(src_root, install_dir)


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

def _apply_update(parent, remote_ver, zip_url, download_url):
    """Download the release zip and apply it (see the module docstring)."""
    dlg = _ProgressDialog(parent, f"Updating to v{remote_ver}…")

    def status(msg):
        parent.after(0, dlg.set_status, msg)

    def fail(text):
        parent.after(0, dlg.close)
        parent.after(0, lambda: messagebox.showerror(
            "Update Failed", text, parent=parent))

    def needs_installer(text):
        def show():
            dlg.close()
            if messagebox.askyesno(
                    "Installer Required",
                    text + "\n\nOpen the download page now?", parent=parent):
                webbrowser.open(download_url)
        parent.after(0, show)

    def _worker():
        tmp_zip = None
        try:
            import requests

            status("Downloading update…")
            try:
                resp = requests.get(zip_url, timeout=120, stream=True)
                resp.raise_for_status()
            except Exception as exc:
                raise UpdateError(
                    f"Could not download v{remote_ver}:\n{exc}\n\n"
                    "The release may not be published yet. Try again later, "
                    f"or download the installer from\n{RELEASES_URL}") from exc

            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
                tmp_zip = f.name
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)

            status("Extracting files…")
            with tempfile.TemporaryDirectory() as extract_dir:
                with zipfile.ZipFile(tmp_zip, "r") as zf:
                    zf.extractall(extract_dir)
                apply_release(_release_root(extract_dir), _INSTALL_DIR, status)

        except NeedsInstaller as exc:
            needs_installer(str(exc))
            return
        except UpdateError as exc:
            fail(str(exc))
            return
        except Exception as exc:
            fail(f"Could not apply the update:\n\n{exc}\n\n"
                 "Please update manually by downloading the latest installer\n"
                 f"from {RELEASES_URL}")
            return
        finally:
            if tmp_zip and os.path.exists(tmp_zip):
                try:
                    os.unlink(tmp_zip)
                except Exception:
                    pass

        parent.after(0, dlg.close)
        parent.after(0, lambda: messagebox.showinfo(
            "Update Complete",
            f"JV Measurements has been updated to v{remote_ver}.\n\n"
            "Please close and reopen the application to start using it.",
            parent=parent))

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
    def on_tk(func):
        parent.after(0, func)

    def offer(remote, remote_ver, local_ver):
        notes        = remote.get("release_notes", "")
        zip_url      = remote.get("zip_url", "")
        rel_date     = remote.get("release_date", "")
        download_url = remote.get("download_url", RELEASES_URL)
        msg = (
            f"A new version of JV Measurements is available!\n\n"
            f"  Installed : {local_ver}\n"
            f"  Available : {remote_ver}  ({rel_date})\n\n"
            f"What's new:\n{notes}\n\n"
            f"Update now? You will be asked to reopen the application "
            f"when it is done."
        )
        if messagebox.askyesno("Update Available", msg, parent=parent):
            if zip_url:
                _apply_update(parent, remote_ver, zip_url, download_url)
            else:
                webbrowser.open(download_url)

    def _check():
        try:
            import requests
            response = requests.get(VERSION_URL, timeout=5)
            response.raise_for_status()
            remote     = response.json()
            remote_ver = remote.get("version", "0.0.0")
            local_ver  = get_local_version()

            if _parse_version(remote_ver) > _parse_version(local_ver):
                on_tk(lambda: offer(remote, remote_ver, local_ver))
            elif not silent:
                on_tk(lambda: messagebox.showinfo(
                    "No Updates Available",
                    f"JV Measurements is up to date.\n\nInstalled version: {local_ver}",
                    parent=parent))

        except ImportError:
            if not silent:
                on_tk(lambda: messagebox.showwarning(
                    "Update Check Failed",
                    "The 'requests' library is not installed.\n"
                    "Run install.bat to fix this.",
                    parent=parent))
        except Exception:
            if not silent:
                on_tk(lambda: messagebox.showwarning(
                    "Update Check Failed",
                    "Could not reach GitHub to check for updates.\n"
                    "Please check your internet connection.",
                    parent=parent))

    threading.Thread(target=_check, daemon=True).start()
