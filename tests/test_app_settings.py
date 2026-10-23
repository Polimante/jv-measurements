"""Tests for ~/.jv-measurement/settings.json persistence.

The contract that matters: a missing, unreadable or corrupt settings file must
never stop the application from starting, and a partial write must never be
left behind.
"""

import json
import os

import pytest

from app.io import app_settings


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "settings_dir", lambda: str(tmp_path))
    monkeypatch.setattr(app_settings, "settings_path",
                        lambda: str(tmp_path / "settings.json"))
    return tmp_path


def test_missing_file_reads_as_empty():
    assert app_settings.load_settings() == {}
    assert app_settings.load_section("shutter") == {}


def test_missing_section_returns_the_supplied_default():
    assert app_settings.load_section("shutter", {"backend": "manual"}) == \
        {"backend": "manual"}


def test_round_trip():
    assert app_settings.save_settings({"shutter": {"backend": "manual"}}) is True
    assert app_settings.load_settings() == {"shutter": {"backend": "manual"}}
    assert app_settings.load_section("shutter") == {"backend": "manual"}


def test_save_section_preserves_other_sections():
    app_settings.save_settings({"other": {"keep": 1}})
    app_settings.save_section("shutter", {"backend": "keithley_digio"})
    data = app_settings.load_settings()
    assert data["other"] == {"keep": 1}
    assert data["shutter"] == {"backend": "keithley_digio"}


def test_corrupt_file_reads_as_empty_instead_of_raising(_isolated_settings):
    (_isolated_settings / "settings.json").write_text("{not json at all",
                                                      encoding="utf-8")
    assert app_settings.load_settings() == {}
    assert app_settings.load_section("shutter", {"backend": "manual"}) == \
        {"backend": "manual"}


def test_a_json_file_that_is_not_an_object_reads_as_empty(_isolated_settings):
    (_isolated_settings / "settings.json").write_text("[1, 2, 3]",
                                                      encoding="utf-8")
    assert app_settings.load_settings() == {}


def test_a_section_that_is_not_an_object_falls_back_to_the_default():
    app_settings.save_settings({"shutter": "not a dict"})
    assert app_settings.load_section("shutter", {"backend": "manual"}) == \
        {"backend": "manual"}


def test_saving_creates_the_directory(tmp_path, monkeypatch):
    nested = tmp_path / "does" / "not" / "exist"
    monkeypatch.setattr(app_settings, "settings_path",
                        lambda: str(nested / "settings.json"))
    assert app_settings.save_settings({"a": {"b": 1}}) is True
    assert (nested / "settings.json").exists()


def test_save_rejects_non_dict_input():
    assert app_settings.save_settings(["not", "a", "dict"]) is False
    assert app_settings.save_section("shutter", "not a dict") is False


def test_a_failed_save_leaves_no_temp_file_and_no_exception(monkeypatch,
                                                            _isolated_settings):
    def explode(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(json, "dump", explode)
    assert app_settings.save_settings({"a": {"b": 1}}) is False
    leftovers = [n for n in os.listdir(_isolated_settings) if n.endswith(".tmp")]
    assert leftovers == []


def test_an_existing_file_survives_a_failed_save(monkeypatch):
    app_settings.save_settings({"shutter": {"backend": "manual"}})

    def explode(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(json, "dump", explode)
    app_settings.save_settings({"shutter": {"backend": "keithley_digio"}})
    # The atomic write means the old, good file is still there.
    assert app_settings.load_section("shutter") == {"backend": "manual"}
