"""Tests for the rt_toggle ABI compatibility guard.

Pure pytest with temp directories only: no Houdini, no hython, no network, no
subprocess, no real binary extensions.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

EXTENSION_ROOT = Path(__file__).resolve().parents[1]
if str(EXTENSION_ROOT) not in sys.path:
    sys.path.insert(0, str(EXTENSION_ROOT))

from rt_runtime.compat import (  # noqa: E402
    CompatibilityReport,
    ExtensionInfo,
    RuntimeTarget,
    check_compatibility,
    interpreter_abi,
    scan_extensions,
)

FIXTURE_FILES = (
    "pure.py",
    "pkg/nested.py",
    "foo.cp314-win_amd64.pyd",
    "foo.cp313-win_amd64.pyd",
    "foo.abi3.so",
    "bare.pyd",
    "__pycache__/pure.cpython-314.pyc",
    "__pycache__/ghost.cp314-win_amd64.pyd",
    "data.json",
)


def make_site_packages(tmp_path: Path, names=FIXTURE_FILES, folder: str = "site-packages") -> Path:
    """Create a fake site-packages tree containing the requested filenames."""
    site_packages = tmp_path / folder
    site_packages.mkdir()
    for name in names:
        path = site_packages / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith((".pyd", ".so", ".dll")):
            path.write_bytes(b"fake extension module")
        else:
            path.write_text("x = 1\n", encoding="utf-8")
    return site_packages


def make_target(site_packages: Path, python_version: str = "3.14.2") -> SimpleNamespace:
    """RuntimeTarget-shaped double: name, home, python_version, site_packages, entry_point, env."""
    return SimpleNamespace(
        name="houdini-rt",
        home=site_packages.parent,
        python_version=python_version,
        site_packages=site_packages,
        entry_point="rt_runtime/activate.py",
        env={},
    )


# ---------------------------------------------------------------------------
# interpreter_abi
# ---------------------------------------------------------------------------


def test_interpreter_abi_tags():
    assert interpreter_abi("3.13.10") == "cp313"
    assert interpreter_abi("3.14") == "cp314"
    assert interpreter_abi((3, 14, 2)) == "cp314"
    assert interpreter_abi("3.9.18") == "cp39"
    assert interpreter_abi("cp313-win_amd64") == "cp313"
    assert interpreter_abi("3") == "py3"
    assert interpreter_abi("python3.exe") == "py3"
    assert interpreter_abi("not-a-version") == "unknown"
    assert interpreter_abi() == f"cp{sys.version_info.major}{sys.version_info.minor}"


def test_interpreter_abi_from_sys_version_info():
    assert interpreter_abi(sys.version_info) == f"cp{sys.version_info.major}{sys.version_info.minor}"


# ---------------------------------------------------------------------------
# scan_extensions
# ---------------------------------------------------------------------------


def test_scan_extensions_classifies_and_ignores_noise(tmp_path):
    site_packages = make_site_packages(tmp_path)
    found = {info.filename: info for info in scan_extensions(site_packages)}

    assert set(found) == {
        "pure.py",
        "nested.py",
        "foo.cp314-win_amd64.pyd",
        "foo.cp313-win_amd64.pyd",
        "foo.abi3.so",
        "bare.pyd",
    }
    assert all(isinstance(info, ExtensionInfo) for info in found.values())
    assert all(info.path.is_file() for info in found.values())

    assert found["pure.py"].abi_tag == "py3"
    assert found["pure.py"].compiled is False
    assert found["nested.py"].path == site_packages / "pkg" / "nested.py"

    assert found["foo.cp314-win_amd64.pyd"].abi_tag == "cp314"
    assert found["foo.cp314-win_amd64.pyd"].compiled is True
    assert found["foo.cp313-win_amd64.pyd"].abi_tag == "cp313"
    assert found["foo.abi3.so"].abi_tag == "abi3"
    assert found["foo.abi3.so"].is_stable_abi is True
    assert found["bare.pyd"].abi_tag == "none"
    assert found["bare.pyd"].compiled is True

    # __pycache__, bytecode and data files are ignored.
    assert "pure.cpython-314.pyc" not in found
    assert "ghost.cp314-win_amd64.pyd" not in found
    assert "data.json" not in found


def test_scan_extensions_missing_path_is_empty(tmp_path):
    assert scan_extensions(tmp_path / "nope") == []


# ---------------------------------------------------------------------------
# check_compatibility verdicts
# ---------------------------------------------------------------------------


def test_313_host_rejects_314_target(tmp_path):
    site_packages = make_site_packages(tmp_path)
    report = check_compatibility(make_target(site_packages, "3.14.2"), houdini_python="3.13.10")

    assert report.safe is False
    assert report.interpreter_abi == "cp313"
    assert report.host_python == "3.13.10"
    assert report.target_python == "3.14.2"
    assert report.blockers

    joined = "\n".join(report.blockers + report.reasons)
    assert "foo.cp314-win_amd64.pyd" in joined
    assert "3.13.10" in joined
    assert "3.14.2" in joined


def test_matching_versions_are_safe(tmp_path):
    site_packages = make_site_packages(
        tmp_path, names=("pure.py", "foo.cp314-win_amd64.pyd", "foo.abi3.so")
    )
    report = check_compatibility(make_target(site_packages, "3.14.2"), houdini_python="3.14.2")

    assert report.safe is True
    assert report.blockers == []
    assert report.interpreter_abi == "cp314"
    assert report.host_python == "3.14.2"
    assert report.target_python == "3.14.2"


def test_declared_match_but_foreign_compiled_tag_is_refused(tmp_path):
    # The declared version matches the host, but the artifact is cp313.
    site_packages = make_site_packages(
        tmp_path, names=("pure.py", "foo.cp313-win_amd64.pyd")
    )
    report = check_compatibility(make_target(site_packages, "3.14.2"), houdini_python="3.14.2")

    assert report.safe is False
    joined = "\n".join(report.blockers)
    assert "foo.cp313-win_amd64.pyd" in joined
    assert "cp313" in joined


def test_missing_site_packages_path_is_unsafe(tmp_path):
    missing = tmp_path / "does-not-exist"
    report = check_compatibility(make_target(missing, "3.14.2"), houdini_python="3.14.2")

    assert report.safe is False
    assert any("does not exist" in blocker for blocker in report.blockers)


@pytest.mark.parametrize(
    "houdini_python",
    [
        "python.exe",
        "C:/Program Files/Side Effects Software/Houdini 22.0.429/bin/python.exe",
        "houdini-build-22.0.429",
    ],
)
def test_unknown_host_interpreter_is_never_safe(tmp_path, houdini_python):
    site_packages = make_site_packages(tmp_path, names=("pure.py",))
    # The target is pure-Python only, so a known host would be allowed.
    report = check_compatibility(make_target(site_packages, "3.13.10"), houdini_python=houdini_python)

    assert report.safe is False
    assert report.interpreter_abi == "unknown"
    assert any(
        "Host interpreter version could not be determined" in blocker
        for blocker in report.blockers
    )


def test_pure_python_only_allowlist(tmp_path):
    site_packages = make_site_packages(tmp_path, names=("pure.py", "pkg/nested.py"))
    target = make_target(site_packages, "3.14.2")

    allowed = check_compatibility(target, houdini_python="3.13.10", allow_pure_python_only=True)
    assert allowed.safe is True
    assert allowed.blockers == []
    assert any("pure-Python" in reason for reason in allowed.reasons)

    refused = check_compatibility(target, houdini_python="3.13.10", allow_pure_python_only=False)
    assert refused.safe is False
    assert any("allow_pure_python_only=False" in blocker for blocker in refused.blockers)


def test_bare_path_target_without_version_is_unsafe(tmp_path):
    site_packages = make_site_packages(tmp_path, names=("pure.py",))
    report = check_compatibility(site_packages, houdini_python="3.13.10")

    assert report.safe is False
    assert any("Target interpreter version is unknown" in blocker for blocker in report.blockers)


def test_versioned_interpreter_filename_resolves_host(tmp_path):
    site_packages = make_site_packages(tmp_path)
    host = tmp_path / "bin" / "python313.dll"
    report = check_compatibility(make_target(site_packages, "3.14.2"), houdini_python=str(host))

    assert report.interpreter_abi == "cp313"
    assert report.host_python == "3.13"
    assert report.safe is False


def test_abi3_only_target_is_refused_across_minor_versions(tmp_path):
    site_packages = make_site_packages(tmp_path, names=("foo.abi3.so",))
    report = check_compatibility(make_target(site_packages, "3.14.2"), houdini_python="3.13.10")

    assert report.safe is False
    assert any("stable-ABI" in blocker for blocker in report.blockers)


def test_runtime_target_accepts_list_site_packages(tmp_path):
    site_packages = make_site_packages(tmp_path, names=("pure.py",))
    target = RuntimeTarget(
        name="fake-runtime",
        home=str(tmp_path),
        python_version="3.14.2",
        site_packages=[str(site_packages)],
        entry_point="pure",
        env={"RT_HOME": str(tmp_path)},
    )

    report = check_compatibility(target, houdini_python="3.13.10", allow_pure_python_only=True)
    assert report.safe is True
    assert report.target_python == "3.14.2"
    assert any("pure-Python" in reason for reason in report.reasons)


def test_report_contract_fields_default():
    report = CompatibilityReport(safe=True, reasons=["ok"], blockers=[], interpreter_abi="cp314")
    assert report.host_python is None
    assert report.target_python is None
    assert report.summary() == "SAFE (host unknown / target unknown)"
