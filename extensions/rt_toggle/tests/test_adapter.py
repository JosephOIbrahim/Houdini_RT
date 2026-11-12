"""Tests for the pure-Python HoudiniRT runtime adapter.

These tests never touch Houdini. They use a temporary fake site-packages
containing a pure ``.py`` module and a ``cp314`` extension module, and they
stub the compatibility check so refusal behavior is deterministic regardless of
the host interpreter running pytest.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

SCAFFOLD_ROOT = Path(__file__).resolve().parents[1]
if str(SCAFFOLD_ROOT) not in sys.path:
    sys.path.insert(0, str(SCAFFOLD_ROOT))

from rt_runtime import adapter as adapter_mod
from rt_runtime import session as session_mod
from rt_runtime.adapter import (
    ActivationResult,
    RuntimeAdapter,
    RuntimeState,
    RuntimeTarget,
    load_targets,
)

ENV_KEY = "RT_TOGGLE_TEST_KEY"


class FakeCompat:
    """Stands in for rt_runtime.compat during tests."""

    def __init__(self, safe=True, reasons=None, blockers=None, abi="cp314"):
        self.safe = safe
        self.reasons = list(reasons or [])
        self.blockers = list(blockers or [])
        self.abi = abi
        self.calls = []

    def check_compatibility(self, target):
        self.calls.append(target.name)
        return SimpleNamespace(
            safe=self.safe,
            reasons=list(self.reasons),
            blockers=list(self.blockers),
            interpreter_abi=self.abi,
        )


@pytest.fixture
def compat_ok(monkeypatch):
    fake = FakeCompat()
    monkeypatch.setattr(adapter_mod, "_load_compat", lambda: fake)
    return fake


@pytest.fixture
def process_snapshot():
    """Restore sys.path and os.environ after each test, and clean adapters first."""
    before_path = list(sys.path)
    before_env = dict(os.environ)
    try:
        yield before_path
    finally:
        sys.path[:] = before_path
        os.environ.clear()
        os.environ.update(before_env)


@pytest.fixture
def target(tmp_path):
    home = tmp_path / "houdini_home"
    home.mkdir()
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    (site_packages / "foo.py").write_text("VALUE = 1\n", encoding="utf-8")
    (site_packages / "foo.cp314-win_amd64.pyd").write_bytes(b"\x00fake-pyd")
    return RuntimeTarget(
        name="test-target",
        home=home,
        python_version="3.14",
        site_packages=site_packages,
        entry_point="foo",
        env={},
    )


@pytest.fixture
def make_adapter(tmp_path, process_snapshot):
    created = []

    def _make(target_obj, state_path=None):
        path = tmp_path / "state.json" if state_path is None else Path(state_path)
        adapter = RuntimeAdapter(target_obj, state_path=path)
        created.append(adapter)
        return adapter

    yield _make

    for adapter in reversed(created):
        try:
            adapter.deactivate()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# activate / deactivate
# ---------------------------------------------------------------------------


def test_activate_is_idempotent(make_adapter, target, compat_ok):
    os.environ.pop(ENV_KEY, None)
    target = replace(target, env={ENV_KEY: "1"})
    adapter = make_adapter(target)
    site_packages = os.fspath(target.site_packages)

    first = adapter.activate()
    assert first.ok is True
    assert first.state is RuntimeState.ON
    assert first.changed is True
    assert sys.path.count(site_packages) == 1
    assert os.environ[ENV_KEY] == "1"

    second = adapter.activate()
    assert second.ok is True
    assert second.state is RuntimeState.ON
    assert second.changed is False
    assert sys.path.count(site_packages) == 1
    assert compat_ok.calls  # compatibility was consulted


def test_deactivate_restores_sys_path_and_env_exactly(
    make_adapter, target, compat_ok, process_snapshot
):
    os.environ.pop(ENV_KEY, None)
    target = replace(target, env={ENV_KEY: "runtime"})
    adapter = make_adapter(target)
    before = list(sys.path)

    assert adapter.activate().changed is True
    assert sys.path != before
    assert os.environ[ENV_KEY] == "runtime"

    result = adapter.deactivate()
    assert result.ok is True
    assert result.state is RuntimeState.OFF
    assert result.changed is True
    assert sys.path == before
    assert ENV_KEY not in os.environ

    again = adapter.deactivate()
    assert again.ok is True
    assert again.state is RuntimeState.OFF
    assert again.changed is False
    assert sys.path == before


def test_deactivate_restores_overwritten_env_value(make_adapter, target, compat_ok):
    os.environ[ENV_KEY] = "original"
    target = replace(target, env={ENV_KEY: "replacement"})
    adapter = make_adapter(target)

    adapter.activate()
    assert os.environ[ENV_KEY] == "replacement"

    adapter.deactivate()
    assert os.environ[ENV_KEY] == "original"


def test_preexisting_sys_path_entry_is_never_removed(
    make_adapter, target, compat_ok
):
    site_packages = os.fspath(target.site_packages)
    sys.path.append(site_packages)
    before = list(sys.path)
    adapter = make_adapter(target)

    activated = adapter.activate()
    assert activated.ok is True
    assert any("already on sys.path" in reason for reason in activated.reasons)

    adapter.deactivate()
    assert sys.path == before
    assert site_packages in sys.path


def test_toggle_switches_on_and_off(make_adapter, target, compat_ok, process_snapshot):
    adapter = make_adapter(target)
    before = list(sys.path)

    on = adapter.toggle()
    assert on.ok is True
    assert on.state is RuntimeState.ON
    assert on.changed is True

    off = adapter.toggle()
    assert off.ok is True
    assert off.state is RuntimeState.OFF
    assert off.changed is True
    assert sys.path == before

    on_again = adapter.toggle()
    assert on_again.state is RuntimeState.ON


# ---------------------------------------------------------------------------
# Refusal rules
# ---------------------------------------------------------------------------


def test_unsafe_compatibility_refuses_without_change(
    make_adapter, target, monkeypatch
):
    unsafe = FakeCompat(safe=False, blockers=["interpreter ABI mismatch (cp312 vs cp314)"])
    monkeypatch.setattr(adapter_mod, "_load_compat", lambda: unsafe)
    adapter = make_adapter(target)
    state_path = Path(adapter.state_path)
    before = list(sys.path)

    result = adapter.activate()
    assert result.ok is False
    assert result.state is RuntimeState.OFF
    assert result.changed is False
    assert any("ABI" in reason for reason in result.reasons)
    assert sys.path == before
    assert not state_path.exists()
    assert adapter.status() is RuntimeState.OFF


def test_missing_compat_module_fails_safe(make_adapter, target, monkeypatch):
    monkeypatch.setattr(adapter_mod, "_load_compat", lambda: None)
    adapter = make_adapter(target)
    before = list(sys.path)

    result = adapter.activate()
    assert result.ok is False
    assert result.state is RuntimeState.OFF
    assert result.changed is False
    assert any("compat" in reason for reason in result.reasons)
    assert sys.path == before

    probe = adapter.probe()
    assert probe["compatibility"]["safe"] is False


def test_missing_site_packages_reports_error_and_refuses(
    make_adapter, target, compat_ok, tmp_path
):
    broken = replace(target, site_packages=tmp_path / "does-not-exist")
    adapter = make_adapter(broken)

    probe = adapter.probe()
    assert probe["site_packages_exists"] is False
    assert probe["available"] is False
    assert probe["active"] is False

    result = adapter.activate()
    assert result.ok is False
    assert result.state is RuntimeState.ERROR
    assert result.changed is False
    assert result.error is not None


def test_corrupt_state_file_reads_as_off(make_adapter, target, compat_ok):
    adapter = make_adapter(target)
    Path(adapter.state_path).write_text("{not valid json", encoding="utf-8")

    assert adapter.status() is RuntimeState.OFF
    assert adapter.activate().ok is True


def test_status_is_error_when_recorded_path_vanishes(
    make_adapter, target, compat_ok
):
    adapter = make_adapter(target)
    site_packages = os.fspath(target.site_packages)

    adapter.activate()
    assert adapter.status() is RuntimeState.ON

    sys.path.remove(site_packages)
    assert adapter.status() is RuntimeState.ERROR

    healed = adapter.activate()
    assert healed.ok is True
    assert healed.state is RuntimeState.ON
    assert healed.changed is True
    assert sys.path.count(site_packages) == 1


# ---------------------------------------------------------------------------
# State file round-trip
# ---------------------------------------------------------------------------


def test_state_file_round_trip(make_adapter, target, compat_ok):
    os.environ.pop(ENV_KEY, None)
    target = replace(target, env={ENV_KEY: "1"})
    adapter = make_adapter(target)

    adapter.activate()
    data = json.loads(Path(adapter.state_path).read_text(encoding="utf-8"))

    assert data["active"] is True
    assert data["target"] == "test-target"
    assert os.fspath(target.site_packages) in data["added_paths"]
    assert data["env_backup"] == {ENV_KEY: None}
    assert data["updated_at"].endswith("Z")

    second = RuntimeAdapter(target, state_path=adapter.state_path)
    assert second.status() is RuntimeState.ON
    assert second.activate().changed is False

    second.deactivate()
    data = json.loads(Path(adapter.state_path).read_text(encoding="utf-8"))
    assert data["active"] is False
    assert data["added_paths"] == []
    assert data["env_backup"] == {}
    assert adapter.status() is RuntimeState.OFF


def test_activate_refuses_when_another_target_is_active(
    make_adapter, target, compat_ok
):
    adapter = make_adapter(target)
    Path(adapter.state_path).write_text(
        json.dumps(
            {
                "active": True,
                "target": "other-target",
                "added_paths": [],
                "env_backup": {},
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    result = adapter.activate()
    assert result.ok is False
    assert result.state is RuntimeState.OFF
    assert any("other-target" in reason for reason in result.reasons)


def test_activate_replaces_stale_other_target_state(
    make_adapter, target, compat_ok
):
    adapter = make_adapter(target)
    Path(adapter.state_path).write_text(
        json.dumps(
            {
                "active": True,
                "target": "other-target",
                "added_paths": [str(Path(adapter.state_path).parent / "gone")],
                "env_backup": {},
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    result = adapter.activate()
    assert result.ok is True
    assert result.state is RuntimeState.ON
    assert any("stale" in reason for reason in result.reasons)

    data = json.loads(Path(adapter.state_path).read_text(encoding="utf-8"))
    assert data["target"] == "test-target"


def test_state_path_none_keeps_state_in_memory(target, compat_ok, process_snapshot):
    adapter = RuntimeAdapter(target)

    assert adapter.activate().ok is True
    assert adapter.status() is RuntimeState.ON
    assert adapter.activate().changed is False

    assert adapter.deactivate().ok is True
    assert adapter.status() is RuntimeState.OFF


def test_concurrent_activate_adds_path_exactly_once(make_adapter, target, compat_ok):
    adapter = make_adapter(target)
    site_packages = os.fspath(target.site_packages)
    results = []

    def worker():
        results.append(adapter.activate())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 8
    assert all(result.ok for result in results)
    assert sys.path.count(site_packages) == 1
    assert sum(1 for result in results if result.changed) == 1


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------


def test_probe_shape(make_adapter, target, compat_ok):
    adapter = make_adapter(target)
    probe = adapter.probe()

    assert set(probe) == {
        "available",
        "python_version",
        "site_packages_exists",
        "compatibility",
        "active",
    }
    assert set(probe["compatibility"]) == {"safe", "reasons", "blockers"}
    assert probe["available"] is True
    assert probe["site_packages_exists"] is True
    assert probe["python_version"] == "3.14"
    assert probe["compatibility"]["safe"] is True
    assert probe["active"] is False


# ---------------------------------------------------------------------------
# Config loading + session defaults
# ---------------------------------------------------------------------------


def test_load_targets_missing_file_returns_empty(tmp_path):
    assert load_targets(tmp_path / "missing.json") == {}


def test_load_targets_resolves_relative_paths(tmp_path):
    config = tmp_path / "rt_toggle.json"
    config.write_text(
        json.dumps(
            {
                "targets": [
                    {
                        "name": "houdini-22",
                        "home": "runtime/22",
                        "python_version": "3.13",
                        "site_packages": "runtime/22/site-packages",
                        "entry_point": "houdini_rt",
                        "env": {"HOUDINI_RT": "1"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    targets = load_targets(config)

    assert set(targets) == {"houdini-22"}
    houdini = targets["houdini-22"]
    assert houdini.home == (tmp_path / "runtime" / "22")
    assert houdini.site_packages == (tmp_path / "runtime" / "22" / "site-packages")
    assert houdini.env == {"HOUDINI_RT": "1"}


def test_load_targets_accepts_mapping_and_bare_list(tmp_path):
    mapping_config = tmp_path / "mapping.json"
    mapping_config.write_text(
        json.dumps(
            {
                "targets": {
                    "a": {
                        "home": str(tmp_path / "a"),
                        "python_version": "3.13",
                        "site_packages": str(tmp_path / "a" / "sp"),
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    assert set(load_targets(mapping_config)) == {"a"}

    list_config = tmp_path / "list.json"
    list_config.write_text(
        json.dumps(
            [
                {
                    "name": "b",
                    "home": str(tmp_path / "b"),
                    "python_version": "3.14",
                    "site_packages": str(tmp_path / "b" / "sp"),
                }
            ]
        ),
        encoding="utf-8",
    )
    assert set(load_targets(list_config)) == {"b"}


def test_load_targets_raises_on_malformed_entry(tmp_path):
    config = tmp_path / "bad.json"
    config.write_text(
        json.dumps({"targets": [{"name": "broken", "home": "x"}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_targets(config)


def test_get_adapter_is_singleton_and_uses_config_adjacent_state(tmp_path):
    config = tmp_path / "rt_toggle.json"
    config.write_text(
        json.dumps(
            {
                "targets": [
                    {
                        "name": "only",
                        "home": str(tmp_path / "home"),
                        "python_version": "3.14",
                        "site_packages": str(tmp_path / "sp"),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    session_mod.reset_cache()
    try:
        first = session_mod.get_adapter(config)
        second = session_mod.get_adapter(config)

        assert first is second
        assert first.target.name == "only"
        assert Path(first.state_path) == tmp_path / ".rt_toggle_state.json"
    finally:
        session_mod.reset_cache()


def test_get_adapter_prefers_active_target_from_state(tmp_path):
    config = tmp_path / "rt_toggle.json"
    targets = [
        {
            "name": name,
            "home": str(tmp_path / name),
            "python_version": "3.14",
            "site_packages": str(tmp_path / name / "sp"),
        }
        for name in ("alpha", "beta")
    ]
    config.write_text(json.dumps({"targets": targets}), encoding="utf-8")
    (tmp_path / ".rt_toggle_state.json").write_text(
        json.dumps({"active": True, "target": "beta"}),
        encoding="utf-8",
    )

    session_mod.reset_cache()
    try:
        adapter = session_mod.get_adapter(config)
        assert adapter.target.name == "beta"

        explicit = session_mod.get_adapter(config, target_name="alpha")
        assert explicit.target.name == "alpha"
    finally:
        session_mod.reset_cache()


def test_get_adapter_raises_for_empty_config(tmp_path):
    config = tmp_path / "empty.json"
    config.write_text(json.dumps({"targets": []}), encoding="utf-8")

    session_mod.reset_cache()
    try:
        with pytest.raises(LookupError):
            session_mod.get_adapter(config)
    finally:
        session_mod.reset_cache()


# ---------------------------------------------------------------------------
# allow_pure_python_only: config -> RuntimeTarget -> check_compatibility
# ---------------------------------------------------------------------------


def _pure_site(tmp_path: Path) -> Path:
    site = tmp_path / "pure-site-packages"
    site.mkdir(exist_ok=True)
    (site / "demo_mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    return site


def _mismatched_version() -> str:
    """A declared version that always differs from the running interpreter."""
    major, minor = sys.version_info.major, sys.version_info.minor
    return f"{major}.{minor + 1}"


def _write_pure_config(tmp_path: Path, allow: bool) -> Path:
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "targets": [
                    {
                        "name": "pure-demo",
                        "home": str(tmp_path),
                        "python_version": _mismatched_version(),
                        "site_packages": str(_pure_site(tmp_path)),
                        "allow_pure_python_only": allow,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return config


def test_config_reads_the_opt_in_flag(tmp_path):
    allowed = load_targets(_write_pure_config(tmp_path, True))["pure-demo"]
    assert allowed.allow_pure_python_only is True

    refused = load_targets(_write_pure_config(tmp_path, False))["pure-demo"]
    assert refused.allow_pure_python_only is False


def test_config_rejects_a_non_boolean_opt_in(tmp_path):
    config = tmp_path / "bad.json"
    config.write_text(
        json.dumps(
            {
                "targets": [
                    {
                        "name": "bad",
                        "home": str(tmp_path),
                        "python_version": "3.14",
                        "site_packages": str(tmp_path),
                        "allow_pure_python_only": "yes",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="allow_pure_python_only"):
        load_targets(config)


def test_opt_in_allows_a_pure_python_mismatch(tmp_path):
    target = load_targets(_write_pure_config(tmp_path, True))["pure-demo"]
    adapter = RuntimeAdapter(target, state_path=tmp_path / "state.json")
    try:
        probe = adapter.probe()
        assert probe["compatibility"]["safe"] is True, probe
        result = adapter.activate()
        assert result.ok is True, result
        assert result.state == RuntimeState.ON
        assert result.changed is True
    finally:
        adapter.deactivate()


def test_default_still_refuses_the_same_pure_python_mismatch(tmp_path):
    target = load_targets(_write_pure_config(tmp_path, False))["pure-demo"]
    adapter = RuntimeAdapter(target, state_path=tmp_path / "state.json")
    try:
        probe = adapter.probe()
        assert probe["compatibility"]["safe"] is False, probe
        result = adapter.activate()
        assert result.ok is False
        assert result.state == RuntimeState.OFF
        assert result.changed is False
    finally:
        adapter.deactivate()


def test_call_compat_forwards_the_opt_in(target):
    seen: dict[str, bool] = {}

    def spy(target, houdini_python=None, allow_pure_python_only=False):
        seen["flag"] = allow_pure_python_only
        return SimpleNamespace(safe=True, reasons=[], blockers=[], interpreter_abi="cp312")

    adapter_mod._call_compat(spy, replace(target, allow_pure_python_only=True))
    assert seen["flag"] is True

    adapter_mod._call_compat(spy, replace(target, allow_pure_python_only=False))
    assert seen["flag"] is False
