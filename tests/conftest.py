import os
import sys
import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "windows_only: skip on non-Windows platforms"
    )
    config.addinivalue_line(
        "markers", "gui: requires a live Tk display; skipped in headless environments and CI"
    )


def pytest_runtest_setup(item):
    for _ in item.iter_markers(name="windows_only"):
        if not sys.platform.startswith("win"):
            pytest.skip("windows_only: requires Windows")

    for _ in item.iter_markers(name="gui"):
        in_ci = os.environ.get("CI", "").lower() in ("true", "1", "yes")
        if sys.platform.startswith("win") and in_ci:
            pytest.skip("gui: skipped in CI on Windows (no display)")
        elif not sys.platform.startswith("win") and not os.environ.get("DISPLAY"):
            pytest.skip("gui: no DISPLAY variable set (headless Linux)")
