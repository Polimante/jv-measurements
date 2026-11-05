"""The installer must ship what the application opens at run time.

``docs/help.html`` lives outside ``app/``, so a release can pass every other test
and still install a Help menu that opens nothing. These tests read the [Files]
section of ``setup.iss`` instead of compiling it, so they run on any platform.
"""

from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path

import pytest

from app.utils.constants import APP_VERSION

ROOT = Path(__file__).resolve().parent.parent
ISS = ROOT / "build_installer" / "setup.iss"
HELP = ROOT / "docs" / "help.html"


def _iss_text() -> str:
    return ISS.read_text(encoding="utf-8-sig")


def _sources() -> list[tuple[str, bool]]:
    """(source pattern relative to the repo root, recursive?) of each [Files] line."""
    section = re.search(r"^\[Files\]$(.*?)^\[", _iss_text(), re.M | re.S).group(1)
    sources = []
    for line in section.splitlines():
        match = re.match(r'Source:\s*"\.\.\\([^"]+)"', line)
        if match:
            sources.append((match.group(1).replace("\\", "/"), "recursesubdirs" in line))
    return sources


def _packaged(rel_path: str) -> bool:
    for pattern, recursive in _sources():
        folder, _, mask = pattern.rpartition("/")
        path_folder, _, name = rel_path.rpartition("/")
        if not fnmatch.fnmatch(name, mask):
            continue
        if path_folder == folder or (recursive and path_folder.startswith(f"{folder}/")):
            return True
    return False


def _help_assets() -> list[str]:
    refs = re.findall(r'(?:src|href)="([^"#]+)"', HELP.read_text(encoding="utf-8"))
    return sorted({r for r in refs if "://" not in r and not r.startswith("mailto:")})


def test_help_page_is_packaged():
    assert _packaged("docs/help.html")


def test_help_page_references_some_local_assets():
    assert _help_assets()  # otherwise the next test would pass by checking nothing


def test_every_asset_of_the_help_page_exists_and_is_packaged():
    for asset in _help_assets():
        assert (HELP.parent / asset).is_file(), f"{asset} is missing from docs/"
        assert _packaged(f"docs/{asset}"), f"{asset} is not in the installer"


def test_arduino_sketch_named_by_the_shutter_settings_is_packaged():
    assert _packaged("docs/arduino_shutter.ino")


@pytest.mark.parametrize("developer_only", [
    "docs/_gen_samples.py",
    "docs/git-workflow.md",
    "docs/sessions/README.md",
    "docs/notebooks/dummy_cell_demo.ipynb",
])
def test_developer_documents_are_not_packaged(developer_only):
    assert not _packaged(developer_only)


def test_every_version_field_of_a_release_agrees():
    text = _iss_text()
    version = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))["version"]
    assert version == APP_VERSION
    assert re.search(r'^#define AppVersion "(.+)"$', text, re.M).group(1) == version
    assert re.search(r"^AppVersion=(.+)$", text, re.M).group(1) == version
    assert re.search(r"^OutputBaseFilename=JV-Measurements-Setup-v(.+)$", text, re.M).group(1) == version
