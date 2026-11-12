"""Launcher policy tests: no cross-install interpreter, and windowless spawns.

These cover the 2026-11-12 changes:

* ``_resolve_launcher`` trusts only the configured launcher, ``RT_SIDECAR_PYTHON``
  or the runtime tree, and returns ``None`` rather than borrowing another
  CPython. A borrowed interpreter starts without the runtime's site-packages.
* ``icacls`` is started with ``CREATE_NO_WINDOW`` so a GUI host does not flash a
  console window.

No sidecar process, no Houdini, no ``hou``.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from rt_runtime import sidecar_adapter as sa
from rt_runtime.sidecar import transport


def test_resolver_returns_none_when_no_interpreter_is_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("RT_SIDECAR_PYTHON", raising=False)
    settings = SimpleNamespace(launcher=None)
    assert sa._resolve_launcher(settings, tmp_path) is None


def test_resolver_uses_the_runtime_tree_interpreter(tmp_path, monkeypatch):
    monkeypatch.delenv("RT_SIDECAR_PYTHON", raising=False)
    exe = tmp_path / "python.exe"
    exe.write_bytes(b"")
    settings = SimpleNamespace(launcher=None)
    assert sa._resolve_launcher(settings, tmp_path) == Path(exe).resolve()


def test_resolver_prefers_the_configured_launcher(tmp_path, monkeypatch):
    monkeypatch.delenv("RT_SIDECAR_PYTHON", raising=False)
    configured = tmp_path / "other" / "python.exe"
    configured.parent.mkdir()
    configured.write_bytes(b"")
    settings = SimpleNamespace(launcher=configured)
    assert sa._resolve_launcher(settings, tmp_path) == Path(configured).resolve()


def test_launcher_argv_refuses_without_a_launcher():
    adapter = object.__new__(sa.SidecarAdapter)
    adapter.settings = SimpleNamespace(launcher=None)
    with pytest.raises(sa.SidecarConfigError):
        adapter._launcher_argv()


@pytest.mark.skipif(os.name != "nt", reason="icacls is Windows-only")
def test_restrict_file_acl_is_windowless(tmp_path, monkeypatch):
    seen: dict[str, object] = {}

    class Completed:
        returncode = 0

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen.update(kwargs)
        return Completed()

    monkeypatch.setattr(transport.subprocess, "run", fake_run)
    target = tmp_path / "state.json"
    target.write_text("{}", encoding="utf-8")

    assert transport.restrict_file_acl(str(target)) is True
    assert seen.get("creationflags") == 0x08000000  # CREATE_NO_WINDOW
