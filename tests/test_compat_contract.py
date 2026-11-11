"""Contract tests for ``rt_runtime.compat``.

Pinned surface::

    RuntimeTarget(name, home, python_version, site_packages, entry_point, env)
    CompatibilityReport(safe, reasons, blockers, interpreter_abi)
    check_compatibility(target, houdini_python=None)

Pinned semantics
----------------
* A pure-Python target whose interpreter ABI matches the Houdini interpreter
  is safe: ``safe is True`` and ``blockers == []``.
* A pure-Python target whose interpreter version matches the host is safe:
  ``safe is True`` and ``blockers == []``.
* A target whose interpreter version does not match (for example cp314 against
  Houdini's cp313) is refused, even when every module is pure Python. The
  current compat default is ``allow_pure_python_only=False``.
* A compiled artifact (``.pyd`` / ``.so``) whose ABI tag does not match the
  host is refused even when the declared versions match. This is the
  cp314-wheel case the report exists to catch.

So "compat refuses cp314" means both a cp314 interpreter declaration and a
cp314 compiled payload are refused, while "pure-Python allowed" means a
pure-Python target on the matching interpreter is allowed. If the design
changes to tolerate pure-Python version mismatches, ``test_cp314_interpreter_refused``
and the module docstring are the pair to change together.

Every test skips while ``rt_runtime.compat`` does not exist. No Houdini import.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

EXT_ROOT = Path(__file__).resolve().parents[1]


def _import_compat():
    module_name = "rt_runtime.compat"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing == module_name or module_name.startswith(missing + "."):
            pytest.skip(
                f"{module_name} is not implemented yet "
                "(expects RuntimeTarget, CompatibilityReport, check_compatibility)"
            )
        raise


@pytest.fixture(scope="module")
def compat():
    return _import_compat()


def make_target(
    compat,
    tmp_path: Path,
    *,
    python_version: str = "3.13",
    compiled_files: tuple[str, ...] = (),
):
    home = tmp_path / "runtime"
    site = home / "site-packages"
    package = site / "sample_pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 42\n", encoding="utf-8")
    for filename in compiled_files:
        (package / filename).write_bytes(b"not a real extension")
    from rt_runtime import RuntimeTarget  # the public surface; compat only consumes it

    return RuntimeTarget(
        name="fake-runtime",
        home=str(home),
        python_version=python_version,
        site_packages=site,
        entry_point="sample_pkg",
        env={"RT_HOME": str(home)},
    )


def test_runtime_target_fields(compat, tmp_path):
    target = make_target(compat, tmp_path)
    assert target.name == "fake-runtime"
    assert target.python_version == "3.13"
    assert target.site_packages == tmp_path / "runtime" / "site-packages"
    assert target.entry_point == "sample_pkg"
    assert isinstance(target.env, dict)
    assert target.env["RT_HOME"] == str(tmp_path / "runtime")


def test_compatibility_report_shape(compat, tmp_path):
    target = make_target(compat, tmp_path)
    report = compat.check_compatibility(target, houdini_python="3.13")
    assert isinstance(report, compat.CompatibilityReport)
    assert isinstance(report.safe, bool)
    assert isinstance(report.reasons, list)
    assert isinstance(report.blockers, list)
    assert isinstance(report.interpreter_abi, str)


def test_pure_python_target_allowed(compat, tmp_path):
    target = make_target(compat, tmp_path, python_version="3.13")
    report = compat.check_compatibility(target, houdini_python="3.13")
    assert report.safe is True, f"pure-Python target refused: {report.blockers!r}"
    assert report.blockers == []


def test_cp314_interpreter_refused(compat, tmp_path):
    target = make_target(compat, tmp_path, python_version="3.14")
    report = compat.check_compatibility(target, houdini_python="3.13")
    assert report.safe is False, (
        "a cp314 target must be refused against Houdini cp313, "
        "even when it is pure Python"
    )
    assert report.blockers, "refusal must include at least one blocker reason"


def test_cp314_target_with_compiled_extension_refused(compat, tmp_path):
    target = make_target(
        compat,
        tmp_path,
        python_version="3.14",
        compiled_files=("demo.cp314-win_amd64.pyd",),
    )
    report = compat.check_compatibility(target, houdini_python="3.13")
    assert report.safe is False, "a cp314 target with compiled payload must be refused"
    assert report.blockers, "refusal must include at least one blocker reason"


def test_cp314_compiled_artifact_refused(compat, tmp_path):
    target = make_target(
        compat,
        tmp_path,
        python_version="3.13",
        compiled_files=("demo.cp314-win_amd64.pyd",),
    )
    report = compat.check_compatibility(target, houdini_python="3.13")
    assert report.safe is False, (
        "a cp314-tagged compiled extension must be refused even when the target "
        "declares python_version 3.13"
    )
    joined = " ".join(report.blockers).lower()
    assert "314" in joined or "abi" in joined, (
        f"blocker text should identify the ABI problem, got: {report.blockers!r}"
    )


def test_default_houdini_python_argument(compat, tmp_path):
    target = make_target(compat, tmp_path, python_version="3.13")
    report = compat.check_compatibility(target)
    assert isinstance(report, compat.CompatibilityReport)
