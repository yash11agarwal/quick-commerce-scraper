"""Enforces the import rules in docs/ARCHITECTURE.md section 3."""

from __future__ import annotations

import ast
from pathlib import Path

import qcom

ROOT = Path(qcom.__file__).parent


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return {n for n in names if n.startswith("qcom")}


def _modules(sub: str) -> list[Path]:
    return sorted((ROOT / sub).rglob("*.py"))


def test_platforms_never_import_io_or_cli():
    for path in _modules("platforms"):
        bad = {m for m in _imports(path) if m.startswith(("qcom.io", "qcom.cli"))}
        assert not bad, f"{path.relative_to(ROOT)} imports {bad}"


def test_platforms_import_only_allowed_core_modules():
    allowed = {"qcom.core.models", "qcom.core.errors", "qcom.core.normalise", "qcom.core.location", "qcom.core.clock"}
    for path in _modules("platforms"):
        core = {m for m in _imports(path) if m.startswith("qcom.core")}
        assert core <= allowed, f"{path.relative_to(ROOT)} imports {core - allowed}"


def test_io_never_imports_platforms():
    for path in _modules("io"):
        bad = {m for m in _imports(path) if m.startswith("qcom.platforms")}
        assert not bad, f"{path.relative_to(ROOT)} imports {bad}"


def test_core_never_imports_io_or_cli():
    for path in _modules("core"):
        bad = {m for m in _imports(path) if m.startswith(("qcom.io", "qcom.cli"))}
        assert not bad, f"{path.relative_to(ROOT)} imports {bad}"


def test_no_platform_branching_outside_platforms():
    """No `if platform == "blinkit"` anywhere downstream. The canonical name list in models.py is the one exception."""
    names = ("blinkit", "swiggy_instamart", "zepto", "bigbasket")
    for sub in ("core", "io", "cli"):
        for path in _modules(sub):
            if path.name == "models.py":
                continue
            text = path.read_text(encoding="utf-8")
            for n in names:
                assert f'"{n}"' not in text and f"'{n}'" not in text, f"{path.relative_to(ROOT)} mentions {n!r}"
