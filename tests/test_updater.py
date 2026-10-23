"""In-place update: the steps that decide whether it is safe to apply.

The updater only copies files. A release that needs a library the
installation lacks (scipy was the first) would copy code that cannot be
imported, leaving an application that does not start — so such a release
must be refused before anything is copied, and so must a read-only
installation folder.
"""

from __future__ import annotations

import os

import pytest

from app.utils import updater
from app.utils.updater import (
    NeedsInstaller,
    UpdateError,
    _new_requirements,
    _parse_version,
    _release_root,
    apply_release,
)

OLD_REQ = "numpy>=1.21.0\nmatplotlib>=3.5.0\nrequests>=2.28.0\n"


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def install(tmp_path):
    d = tmp_path / "install"
    _write(d / "requirements.txt", OLD_REQ)
    _write(d / "main.py", "old")
    _write(d / "app" / "old_module.py", "old")
    return d


def _release(tmp_path, req=OLD_REQ):
    r = tmp_path / "extract" / "jv-measurements-2.7.0"
    _write(r / "requirements.txt", req)
    _write(r / "main.py", "new")
    _write(r / "app" / "new_module.py", "new")
    _write(r / ".git" / "HEAD", "must not be copied")
    return r


class TestNewRequirements:
    def test_same_set_in_another_order_needs_nothing(self, tmp_path):
        _write(tmp_path / "a.txt", OLD_REQ)
        _write(tmp_path / "b.txt", "# deps\n\nrequests>=2.28.0\n"
                                   "numpy>=1.21.0\nmatplotlib>=3.5.0\n")
        assert _new_requirements(tmp_path / "b.txt", tmp_path / "a.txt") == []

    def test_added_library_is_reported(self, tmp_path):
        _write(tmp_path / "old.txt", OLD_REQ)
        _write(tmp_path / "new.txt", OLD_REQ + "scipy>=1.9.0\n")
        assert _new_requirements(tmp_path / "new.txt",
                                 tmp_path / "old.txt") == ["scipy>=1.9.0"]

    def test_removed_library_needs_nothing(self, tmp_path):
        _write(tmp_path / "old.txt", OLD_REQ)
        _write(tmp_path / "new.txt", "numpy>=1.21.0\n")
        assert _new_requirements(tmp_path / "new.txt", tmp_path / "old.txt") == []

    def test_release_without_requirements_needs_nothing(self, tmp_path):
        _write(tmp_path / "old.txt", OLD_REQ)
        assert _new_requirements(tmp_path / "missing.txt", tmp_path / "old.txt") == []


class TestApplyRelease:
    def test_compatible_release_is_copied(self, tmp_path, install):
        apply_release(str(_release(tmp_path)), install)
        assert (install / "main.py").read_text() == "new"
        assert (install / "app" / "new_module.py").exists()
        assert not (install / ".git").exists()       # _UPDATE_SKIP honoured

    def test_release_needing_a_new_library_is_refused_untouched(self, tmp_path, install):
        rel = _release(tmp_path, OLD_REQ + "scipy>=1.9.0\n")
        with pytest.raises(NeedsInstaller, match="scipy"):
            apply_release(str(rel), install)
        assert (install / "main.py").read_text() == "old"
        assert (install / "app" / "old_module.py").exists()

    def test_read_only_install_is_refused_untouched(self, tmp_path, install, monkeypatch):
        def deny(*_a, **_k):
            raise PermissionError("Access is denied")
        monkeypatch.setattr(updater.tempfile, "mkstemp", deny)
        with pytest.raises(UpdateError, match="not writable"):
            apply_release(str(_release(tmp_path)), install)
        assert (install / "main.py").read_text() == "old"

    def test_writable_check_leaves_no_probe_file(self, tmp_path, install):
        apply_release(str(_release(tmp_path)), install)
        assert not [f for f in os.listdir(install) if f.startswith(".update_probe_")]


def test_release_root_unwraps_the_github_folder(tmp_path):
    rel = _release(tmp_path)
    assert _release_root(str(tmp_path / "extract")) == str(rel)


@pytest.mark.parametrize("a, b", [("2.10.0", "2.9.9"), ("2.6.1", "2.6.0"), ("3.0", "2.99.99")])
def test_version_comparison_is_numeric(a, b):
    assert _parse_version(a) > _parse_version(b)
