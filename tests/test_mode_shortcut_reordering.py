"""Structural tests for Part A — mode shortcut reordering.

Verifies that main_window.py binds each mode to the correct Alt+F key and
that Alt+F4 (reserved by Windows for window-close) is never bound.

These tests parse the source file with regex rather than instantiating the
full GUI, avoiding the pyvisa / winsound dependency and keeping the suite
hardware-free.
"""

from __future__ import annotations

import re
from pathlib import Path

_SRC = Path(__file__).parent.parent / "app" / "gui" / "main_window.py"


def _bindings() -> dict[str, str]:
    """Return {shortcut: mode_key} by parsing bind() calls in main_window.py."""
    text = _SRC.read_text(encoding="utf-8")
    pattern = re.compile(
        r'self\.root\.bind\(\s*"<Alt-F(\d+)>"\s*,\s*lambda\s+e\s*:\s*self\.switch_mode\("(\w+)"\)'
    )
    return {f"Alt+F{m.group(1)}": m.group(2) for m in pattern.finditer(text)}


def _accelerators() -> dict[str, str]:
    """Return {mode_key: accelerator} by parsing add_command() calls."""
    text = _SRC.read_text(encoding="utf-8")
    pattern = re.compile(
        r'accelerator="(Alt\+F\d+)".*?self\.switch_mode\("(\w+)"\)',
        re.DOTALL,
    )
    return {m.group(2): m.group(1) for m in pattern.finditer(text)}


# ── Binding existence and correctness ────────────────────────────────────────

class TestBindings:
    def test_jv_bound_to_alt_f2(self):
        assert _bindings().get("Alt+F2") == "jv"

    def test_light_jv_bound_to_alt_f3(self):
        assert _bindings().get("Alt+F3") == "light_jv"

    def test_mpp_bound_to_alt_f5(self):
        assert _bindings().get("Alt+F5") == "mpp"

    def test_stab_bound_to_alt_f6(self):
        assert _bindings().get("Alt+F6") == "stab"

    def test_pure_mpp_bound_to_alt_f7(self):
        assert _bindings().get("Alt+F7") == "pure_mpp"

    def test_curation_bound_to_alt_f8(self):
        assert _bindings().get("Alt+F8") == "curation"

    def test_isos_bound_to_alt_f9(self):
        assert _bindings().get("Alt+F9") == "isos"

    def test_alt_f4_not_bound(self):
        """Alt+F4 must not appear in any bind() call (Windows close-window key)."""
        text = _SRC.read_text(encoding="utf-8")
        assert "<Alt-F4>" not in text, "Alt+F4 must never be bound"


# ── Old shortcuts must no longer appear as bindings ──────────────────────────

class TestOldShortcutsGone:
    def test_mpp_not_on_alt_f3(self):
        b = _bindings()
        assert b.get("Alt+F3") != "mpp", "Alt+F3 must not bind mpp (old shortcut)"

    def test_stab_not_on_alt_f5(self):
        b = _bindings()
        assert b.get("Alt+F5") != "stab", "Alt+F5 must not bind stab (old shortcut)"

    def test_pure_mpp_not_on_alt_f6(self):
        b = _bindings()
        assert b.get("Alt+F6") != "pure_mpp", "Alt+F6 must not bind pure_mpp (old shortcut)"

    def test_curation_not_on_alt_f7(self):
        b = _bindings()
        assert b.get("Alt+F7") != "curation", "Alt+F7 must not bind curation (old shortcut)"

    def test_isos_not_on_alt_f8(self):
        b = _bindings()
        assert b.get("Alt+F8") != "isos", "Alt+F8 must not bind isos (old shortcut)"


# ── Menu accelerators match bindings ─────────────────────────────────────────

class TestMenuAccelerators:
    def test_menu_jv_accelerator(self):
        assert _accelerators().get("jv") == "Alt+F2"

    def test_menu_light_jv_accelerator(self):
        assert _accelerators().get("light_jv") == "Alt+F3"

    def test_menu_mpp_accelerator(self):
        assert _accelerators().get("mpp") == "Alt+F5"

    def test_menu_stab_accelerator(self):
        assert _accelerators().get("stab") == "Alt+F6"

    def test_menu_pure_mpp_accelerator(self):
        assert _accelerators().get("pure_mpp") == "Alt+F7"

    def test_menu_curation_accelerator(self):
        assert _accelerators().get("curation") == "Alt+F8"

    def test_menu_isos_accelerator(self):
        assert _accelerators().get("isos") == "Alt+F9"
