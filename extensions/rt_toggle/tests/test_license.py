"""Apache-2.0 licensing and release-archive coverage."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_tool(name):
    path = ROOT / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"release_tool_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_license_is_apache_and_names_the_owner():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")

    assert "Apache License" in text
    assert "Version 2.0, January 2004" in text
    assert "Copyright 2026 Myth and Logic" in text


def test_package_metadata_declares_apache():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert 'license = { text = "Apache-2.0" }' in pyproject
    assert "[LICENSE](LICENSE)" in readme
    assert "License: TBD" not in readme


def test_release_builder_includes_the_license():
    builder = _load_tool("build_release")
    entries = {rel.as_posix() for rel, _src in builder.collect_entries()}

    assert "LICENSE" in builder.INCLUDE_FILES
    assert "LICENSE" in builder.REQUIRED_FILES
    assert "LICENSE" in entries


def test_release_audit_requires_the_license():
    audit = _load_tool("audit_release")

    assert "LICENSE" in audit.REQUIRED_FILES
