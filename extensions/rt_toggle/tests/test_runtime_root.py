"""Relocation tests for the external HoudiniRT runtime root."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rt_runtime.adapter import load_targets
from rt_runtime import sidecar_adapter as sa

ROOT = Path(__file__).resolve().parent.parent


def _write_config(tmp_path, home, site_packages, sidecar=None):
    target = {
        "home": home,
        "python_version": "3.14",
        "site_packages": site_packages,
    }
    if sidecar is not None:
        target["mode"] = "sidecar"
        target["sidecar"] = sidecar
    path = tmp_path / "config" / "rt_toggle.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "targets": {"demo": target}}), encoding="utf-8"
    )
    return path


@pytest.mark.parametrize("token", ["$HOUDINI_RT_ROOT", "${HOUDINI_RT_ROOT}"])
def test_target_paths_expand_the_external_root(tmp_path, monkeypatch, token):
    external = tmp_path / "external"
    monkeypatch.setenv("HOUDINI_RT_ROOT", str(external))
    config = _write_config(tmp_path, token, token + "/Lib/site-packages")

    target = load_targets(config)["demo"]

    assert target.home == external
    assert target.site_packages == external / "Lib" / "site-packages"


@pytest.mark.parametrize("token", ["$HOUDINI_RT_ROOT", "${HOUDINI_RT_ROOT}"])
@pytest.mark.parametrize("empty", [False, True])
def test_missing_root_fails_clearly(tmp_path, monkeypatch, token, empty):
    monkeypatch.setenv("HOUDINI_RT_ROOT", "" if empty else str(tmp_path))
    if not empty:
        monkeypatch.delenv("HOUDINI_RT_ROOT")
    config = _write_config(tmp_path, token, "packages")

    with pytest.raises(ValueError, match="HOUDINI_RT_ROOT"):
        load_targets(config)


def test_relative_paths_remain_config_relative(tmp_path, monkeypatch):
    monkeypatch.setenv("HOUDINI_RT_ROOT", str(tmp_path / "external"))
    config = _write_config(tmp_path, "$HOUDINI_RT_ROOT", "packages")

    target = load_targets(config)["demo"]

    assert target.site_packages == config.parent / "packages"


def test_sidecar_paths_expand_and_inherit(tmp_path, monkeypatch):
    external = tmp_path / "external"
    monkeypatch.setenv("HOUDINI_RT_ROOT", str(external))
    sidecar = {
        "launcher": "${HOUDINI_RT_ROOT}/python.exe",
        "host_script": "$HOUDINI_RT_ROOT/host.py",
        "state_dir": "$HOUDINI_RT_ROOT/state",
        "base_site_packages": "${HOUDINI_RT_ROOT}/base-packages",
    }
    config = _write_config(
        tmp_path, "$HOUDINI_RT_ROOT", "${HOUDINI_RT_ROOT}/Lib/site-packages", sidecar
    )

    target = load_targets(config)["demo"]
    assert target.sidecar.launcher == external / "python.exe"
    assert target.sidecar.host_script == external / "host.py"
    assert target.sidecar.state_dir == external / "state"
    assert target.sidecar.base_site_packages == external / "base-packages"
    assert target.sidecar.runtime_home == external
    assert target.sidecar.site_packages == external / "Lib" / "site-packages"

    _mode, settings = sa.sidecar_settings_from_config(config, "demo")
    assert settings.launcher == external / "python.exe"
    assert settings.runtime_home == external


def test_shipped_configs_point_at_the_external_root(tmp_path, monkeypatch):
    external = tmp_path / "external"
    monkeypatch.setenv("HOUDINI_RT_ROOT", str(external))

    for relative in ("rt_toggle.json", "verification/rt_toggle_sidecar.json"):
        targets = load_targets(ROOT / relative)
        for target in targets.values():
            assert target.home == external
            assert target.site_packages == external / "Lib" / "site-packages"
            if target.mode == "sidecar":
                assert target.sidecar.launcher == external / "python.exe"


def test_package_uses_an_independent_extension_root():
    package = json.loads(
        (ROOT / "houdini" / "packages" / "rt_toggle.json").read_text(encoding="utf-8")
    )
    env = package["env"]

    assert [entry["var"] for entry in env] == ["PYTHONPATH"]
    assert env[0]["value"] == "$RT_TOGGLE_ROOT"
    assert "HOUDINI_RT_ROOT/extensions/rt_toggle" not in json.dumps(package)
