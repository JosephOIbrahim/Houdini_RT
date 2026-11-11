"""Contract tests for the RT runtime state machine and its state file.

These tests pin the public contract the adapter implementation must satisfy::

    RuntimeState: OFF, ON, ERROR
    ActivationResult(ok, state, changed, reasons, error)
    RuntimeAdapter(target, state_path=None)
        .probe()      -> dict diagnostics (compatibility + availability)
        .activate()   -> ActivationResult
        .deactivate() -> ActivationResult
        .toggle()     -> ActivationResult
        .status()     -> RuntimeState
    get_adapter(config_path=None)  (exported by the rt_runtime package)

    State JSON keys: active, target, added_paths, env_backup, updated_at

Pinned semantics
----------------
* ``state["target"]`` is the ``RuntimeTarget.name`` string.
* ``updated_at`` is an ISO-8601 string parsable by ``datetime.fromisoformat``.
* Extra keys in the state file are permitted; the five above are required.
* Activation is idempotent: a second call reports ``changed`` false and leaves
  ``sys.path`` untouched.
* Deactivation restores ``sys.path`` to the exact prior list, including order
  and pre-existing duplicates, and never removes a path that was already on
  ``sys.path`` before activation.
* Activation applies ``target.env`` and deactivation restores the prior
  environment values.

Every test skips with a clear reason while ``rt_runtime.adapter`` does not
exist. Nothing here imports or starts Houdini.
"""

from __future__ import annotations

import contextlib
import importlib
import inspect
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import pytest

EXT_ROOT = Path(__file__).resolve().parents[1]
ENV_KEY = "RT_HOME"
_MISSING = object()


# ---------------------------------------------------------------------------
# import helpers
# ---------------------------------------------------------------------------
def _import(module_name: str, hint: str):
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing == module_name or module_name.startswith(missing + "."):
            pytest.skip(f"{module_name} is not implemented yet ({hint})")
        raise


@pytest.fixture(scope="module")
def compat_mod():
    return _import(
        "rt_runtime.compat",
        "expects RuntimeTarget, CompatibilityReport, check_compatibility",
    )


@pytest.fixture(scope="module")
def adapter_mod():
    return _import(
        "rt_runtime.adapter",
        "expects RuntimeState, ActivationResult, RuntimeAdapter, get_adapter",
    )


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------
def make_target(
    compat_mod,
    tmp_path: Path,
    *,
    python_version: str = f"{sys.version_info.major}.{sys.version_info.minor}",
    name: str = "fake-runtime",
):
    """Build a RuntimeTarget pointing at a throwaway pure-Python site-packages."""
    home = tmp_path / "runtime"
    site = home / "site-packages"
    package = site / "sample_pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 42\n", encoding="utf-8")
    from rt_runtime import RuntimeTarget  # the public surface; compat only consumes it

    return RuntimeTarget(
        name=name,
        home=str(home),
        python_version=python_version,
        site_packages=site,
        entry_point="sample_pkg",
        env={ENV_KEY: str(home)},
    )


@contextlib.contextmanager
def preserved_sys_path_and_env() -> Iterator[list[str]]:
    """Snapshot sys.path and RT_HOME, restore both no matter what the test does."""
    prior_path = list(sys.path)
    prior_env = os.environ.get(ENV_KEY, _MISSING)
    try:
        yield prior_path
    finally:
        sys.path[:] = prior_path
        if prior_env is _MISSING:
            os.environ.pop(ENV_KEY, None)
        else:
            os.environ[ENV_KEY] = str(prior_env)


def read_state(state_path: Path) -> dict[str, Any]:
    return json.loads(state_path.read_text(encoding="utf-8"))


def without_timestamp(state: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in state.items() if key != "updated_at"}


def assert_iso_timestamp(value: Any) -> None:
    assert isinstance(value, str) and value, "updated_at must be a non-empty ISO-8601 string"
    # Accept both "+00:00" and "Z" suffixes.
    datetime.fromisoformat(value.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# type surfaces
# ---------------------------------------------------------------------------
def test_runtime_state_members(adapter_mod):
    state = adapter_mod.RuntimeState
    for member in ("OFF", "ON", "ERROR"):
        assert hasattr(state, member), f"RuntimeState is missing {member}"
    assert len({state.OFF, state.ON, state.ERROR}) == 3


def test_activation_result_contract(adapter_mod):
    result = adapter_mod.ActivationResult(
        ok=True,
        state=adapter_mod.RuntimeState.ON,
        changed=True,
        reasons=["activated"],
        error=None,
    )
    assert result.ok is True
    assert result.state == adapter_mod.RuntimeState.ON
    assert result.changed is True
    assert result.reasons == ["activated"]
    assert result.error is None


def test_adapter_public_surface(adapter_mod):
    for method in ("probe", "activate", "deactivate", "toggle", "status"):
        assert hasattr(adapter_mod.RuntimeAdapter, method), f"RuntimeAdapter missing {method}()"
        assert callable(getattr(adapter_mod.RuntimeAdapter, method))


def test_adapter_constructor_signature(adapter_mod):
    signature = inspect.signature(adapter_mod.RuntimeAdapter)
    assert "target" in signature.parameters
    assert "state_path" in signature.parameters
    assert signature.parameters["state_path"].default is None


def test_get_adapter_signature():
    from rt_runtime.session import get_adapter

    assert callable(get_adapter)
    signature = inspect.signature(get_adapter)
    assert "config_path" in signature.parameters
    assert signature.parameters["config_path"].default is None


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------
def test_probe_returns_diagnostics(adapter_mod, compat_mod, tmp_path):
    target = make_target(compat_mod, tmp_path)
    adapter = adapter_mod.RuntimeAdapter(target, state_path=str(tmp_path / "state.json"))
    report = adapter.probe()
    assert isinstance(report, dict), "probe() returns the documented diagnostics dict"
    for key in ("available", "python_version", "site_packages_exists", "compatibility", "active"):
        assert key in report, f"probe() is missing {key!r}"
    assert {"safe", "reasons", "blockers"} <= set(report["compatibility"])


# ---------------------------------------------------------------------------
# state file schema
# ---------------------------------------------------------------------------
def test_state_json_schema(adapter_mod, compat_mod, tmp_path):
    with preserved_sys_path_and_env():
        target = make_target(compat_mod, tmp_path)
        state_path = tmp_path / "state.json"
        adapter = adapter_mod.RuntimeAdapter(target, state_path=str(state_path))

        result = adapter.activate()
        assert result.ok is True, f"activation failed: {result.error!r} {result.reasons!r}"
        assert result.state == adapter_mod.RuntimeState.ON
        assert result.changed, "first activation must report changed"

        assert state_path.exists(), "activate() must write the state file"
        state = read_state(state_path)

        for key in ("active", "target", "added_paths", "env_backup", "updated_at"):
            assert key in state, f"state file is missing required key {key!r}"

        assert state["active"] is True
        assert state["target"] == target.name, "state['target'] must be the target name string"
        assert isinstance(state["added_paths"], list)
        assert all(isinstance(path, str) for path in state["added_paths"])
        assert state["added_paths"], "first activation must record at least one added path"
        for path in state["added_paths"]:
            assert path in sys.path, f"recorded added path is not on sys.path: {path!r}"
        assert isinstance(state["env_backup"], dict)
        assert_iso_timestamp(state["updated_at"])

        adapter.deactivate()
        state = read_state(state_path)
        assert state["active"] is False
        assert state["added_paths"] == []


# ---------------------------------------------------------------------------
# idempotency
# ---------------------------------------------------------------------------
def test_activation_is_idempotent(adapter_mod, compat_mod, tmp_path):
    with preserved_sys_path_and_env():
        target = make_target(compat_mod, tmp_path)
        state_path = tmp_path / "state.json"
        adapter = adapter_mod.RuntimeAdapter(target, state_path=str(state_path))

        first = adapter.activate()
        assert first.ok is True and first.changed
        path_after_first = list(sys.path)
        state_after_first = without_timestamp(read_state(state_path))

        second = adapter.activate()
        assert second.ok is True
        assert not second.changed, "second activation must report changed=false"
        assert list(sys.path) == path_after_first, "idempotent activation must not touch sys.path"
        assert without_timestamp(read_state(state_path)) == state_after_first


def test_deactivate_when_off_is_noop(adapter_mod, compat_mod, tmp_path):
    with preserved_sys_path_and_env():
        target = make_target(compat_mod, tmp_path)
        adapter = adapter_mod.RuntimeAdapter(target, state_path=str(tmp_path / "state.json"))
        prior = list(sys.path)

        result = adapter.deactivate()
        assert result.ok is True
        assert not result.changed
        assert result.error is None
        assert list(sys.path) == prior


# ---------------------------------------------------------------------------
# sys.path restoration
# ---------------------------------------------------------------------------
def test_deactivate_restores_sys_path_exactly(adapter_mod, compat_mod, tmp_path):
    with preserved_sys_path_and_env():
        sentinel = str(tmp_path / "sentinel-on-path")
        sys.path.insert(0, sentinel)
        prior = list(sys.path)

        target = make_target(compat_mod, tmp_path)
        adapter = adapter_mod.RuntimeAdapter(target, state_path=str(tmp_path / "state.json"))

        adapter.activate()
        assert list(sys.path) != prior, "activation must add at least one sys.path entry"

        result = adapter.deactivate()
        assert result.ok is True
        assert result.state == adapter_mod.RuntimeState.OFF
        assert list(sys.path) == prior, "deactivation must restore sys.path exactly"


def test_deactivate_preserves_preexisting_path(adapter_mod, compat_mod, tmp_path):
    with preserved_sys_path_and_env():
        target = make_target(compat_mod, tmp_path)
        preexisting = str(target.site_packages)
        sys.path.insert(0, preexisting)
        prior = list(sys.path)

        adapter = adapter_mod.RuntimeAdapter(target, state_path=str(tmp_path / "state.json"))
        adapter.activate()
        adapter.deactivate()

        assert list(sys.path) == prior, (
            "deactivation removed a path that was already on sys.path before activation"
        )


# ---------------------------------------------------------------------------
# toggle / status / persistence
# ---------------------------------------------------------------------------
def test_toggle_flips_state(adapter_mod, compat_mod, tmp_path):
    with preserved_sys_path_and_env():
        target = make_target(compat_mod, tmp_path)
        adapter = adapter_mod.RuntimeAdapter(target, state_path=str(tmp_path / "state.json"))
        prior = list(sys.path)

        on = adapter.toggle()
        assert on.ok is True
        assert on.state == adapter_mod.RuntimeState.ON
        assert on.changed is True

        off = adapter.toggle()
        assert off.ok is True
        assert off.state == adapter_mod.RuntimeState.OFF
        assert off.changed is True
        assert list(sys.path) == prior


def test_status_tracks_state(adapter_mod, compat_mod, tmp_path):
    with preserved_sys_path_and_env():
        target = make_target(compat_mod, tmp_path)
        adapter = adapter_mod.RuntimeAdapter(target, state_path=str(tmp_path / "state.json"))

        assert adapter.status() == adapter_mod.RuntimeState.OFF
        adapter.activate()
        assert adapter.status() == adapter_mod.RuntimeState.ON
        adapter.deactivate()
        assert adapter.status() == adapter_mod.RuntimeState.OFF


def test_state_persists_across_adapter_instances(adapter_mod, compat_mod, tmp_path):
    with preserved_sys_path_and_env():
        prior = list(sys.path)
        target = make_target(compat_mod, tmp_path)
        state_path = tmp_path / "state.json"

        first = adapter_mod.RuntimeAdapter(target, state_path=str(state_path))
        assert first.activate().ok is True

        # A fresh adapter (stand-in for a new Houdini session) must recover ON
        # from the state file and be able to turn the runtime back off.
        second = adapter_mod.RuntimeAdapter(target, state_path=str(state_path))
        status = second.status()
        assert status == adapter_mod.RuntimeState.ON

        result = second.deactivate()
        assert result.ok is True
        assert result.state == adapter_mod.RuntimeState.OFF
        assert list(sys.path) == prior, "a restarted adapter must restore sys.path exactly"


def test_activation_applies_env_and_restores(adapter_mod, compat_mod, tmp_path):
    with preserved_sys_path_and_env():
        os.environ.pop(ENV_KEY, None)
        target = make_target(compat_mod, tmp_path)
        adapter = adapter_mod.RuntimeAdapter(target, state_path=str(tmp_path / "state.json"))

        adapter.activate()
        assert os.environ.get(ENV_KEY) == target.env[ENV_KEY], (
            "activation must apply target.env"
        )

        adapter.deactivate()
        assert ENV_KEY not in os.environ, (
            "deactivation must remove env vars that were absent before activation"
        )
