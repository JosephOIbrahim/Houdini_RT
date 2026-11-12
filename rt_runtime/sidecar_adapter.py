"""Sidecar mode for the HoudiniRT runtime adapter.

This module is the merge-ready half of the adapter split. The existing
``rt_runtime.adapter`` keeps owning in-process activation (``sys.path`` append,
env, state file). This module owns the **sidecar** mode: it starts and stops an
S1 ``SidecarSupervisor``, verifies the S2 ``/health`` transport, and projects
both into the adapter's existing ``RuntimeState`` / ``ActivationResult`` shape.

Design constraints, identical to ``adapter.py``:

* Standard library only. ``hou`` is never imported, now or later.
* Nothing is imported from the work directory at module import time. The S1/S2
  modules (``lifecycle``, ``transport``) are loaded lazily inside ``activate`` /
  ``deactivate`` / ``status`` / ``probe``, and a test may inject a supervisor
  factory and a health checker so no process is ever spawned.
* ``status()`` is read-only and never performs network I/O. ``status_detail()``
  can, on request, add one bounded ``/health`` check.
* All mutations run under one process-wide re-entrant lock, because a toggle is
  a cross-file, cross-process operation (S1's ``sidecar.lock`` is the real
  inter-process guard; this lock serialises calls inside one host).

Merge into ``rt_runtime/adapter.py``
------------------------------------
Copy this file to ``rt_runtime/sidecar_adapter.py`` as a sibling module, then
make four small edits in ``adapter.py``:

1. ``RuntimeTarget`` gains ``mode: str = "in_process"`` and
   ``sidecar: SidecarSettings | None = None``; ``_TARGET_FIELDS`` gains both.
2. ``_target_from_mapping`` calls ``target_mode(raw)`` and, for sidecar mode,
   ``settings_for_target(raw, base)``. An invalid value raises ``ValueError``,
   matching the existing config validation style.
3. ``RuntimeAdapter.__init__`` builds ``self._sidecar = SidecarAdapter(...)``
   lazily when ``self.target.mode == "sidecar"`` (use a local import to avoid a
   module cycle).
4. ``activate`` / ``deactivate`` / ``status`` / ``probe`` / ``toggle`` begin
   with ``if self.target.mode == "sidecar": return self._sidecar.<same>()``.

Nothing else in ``adapter.py`` changes. In-process targets have no ``mode`` key
and therefore take exactly the code path they take today.

Standalone use (integration tests, before the merge)
----------------------------------------------------
Run with the work root on ``PYTHONPATH`` or let the adapter find it through
``RT_SIDECAR_WORK_DIR``. ``_load_sidecar_modules`` tries, in order:

1. ``rt_runtime.sidecar.lifecycle`` (post-merge package layout),
2. ``sidecar.lifecycle`` from ``RT_SIDECAR_WORK_DIR``,
3. ``sidecar.lifecycle`` from ``%TEMP%\\rt-sidecar\\work``.

It never copies S1/S2 code into the repo. See ``ADAPTER_SIDECAR.md``.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

__all__ = [
    "HealthResult",
    "IntegrityReport",
    "PackageInfo",
    "PrecedenceDecision",
    "SidecarAdapter",
    "SidecarConfigError",
    "SidecarSettings",
    "SidecarUnavailable",
    "decide_precedence",
    "default_integrity",
    "read_target_config",
    "settings_for_target",
    "sidecar_settings_from_config",
    "target_mode",
]

MODE_IN_PROCESS = "in_process"
MODE_SIDECAR = "sidecar"

PRECEDENCE_RUNTIME_FIRST = "runtime-first"
PRECEDENCE_BASE_FIRST = "base-first"
_PRECEDENCES = frozenset({PRECEDENCE_RUNTIME_FIRST, PRECEDENCE_BASE_FIRST})

DEFAULT_EXPECT_PYTHON = "3.14"

#: Packages whose compiled extension must be loadable before the runtime tree
#: may take precedence. ``pydantic_core`` is the known cp314 binary in the
#: 2026-11 audit; extend this list when a payload imports more binaries.
DEFAULT_CRITICAL_PACKAGES = ("pydantic_core",)

#: Interpreter-level files the repaired runtime tree must carry before its own
#: ``python.exe`` can be the launcher. These are reported, not used to gate
#: package precedence, because RT-first changes ``sys.path`` order only.
_CORE_DLLS = ("_socket.pyd", "_ctypes.pyd", "_ssl.pyd", "_hashlib.pyd")

_STATUS_ABSENT = "absent"
_STATUS_STARTING = "starting"
_STATUS_READY = "ready"
_STATUS_STOPPING = "stopping"
_STATUS_STOPPED = "stopped"
_STATUS_UNRESPONSIVE = "unresponsive"
_STATUS_STALE = "stale"
_STATUS_ERROR = "error"

#: sys.path and the supervisor files are process-global. When merged into
#: ``adapter.py`` this should alias that module's ``_GLOBAL_LOCK``.
_GLOBAL_LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SidecarConfigError(ValueError):
    """The sidecar section of ``rt_toggle.json`` is malformed."""


class SidecarUnavailable(RuntimeError):
    """The S1/S2 modules could not be imported and no fake was injected."""


# ---------------------------------------------------------------------------
# Adapter result types (real ones when merged, fallbacks standalone)
# ---------------------------------------------------------------------------

_TYPES: tuple[Any, Any] | None = None


class _FallbackRuntimeState(str, Enum):
    OFF = "off"
    ON = "on"
    ERROR = "error"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value

    @property
    def state(self) -> "_FallbackRuntimeState":
        return self

    @property
    def ok(self) -> bool:
        return self is not _FallbackRuntimeState.ERROR

    @property
    def changed(self) -> bool:
        return False

    @property
    def reasons(self) -> list[str]:
        return []

    @property
    def error(self) -> str | None:
        return None


@dataclass
class _FallbackActivationResult:
    ok: bool = False
    state: _FallbackRuntimeState = _FallbackRuntimeState.OFF
    changed: bool = False
    reasons: list[str] = field(default_factory=list)
    error: str | None = None


def _adapter_types() -> tuple[Any, Any]:
    """Return ``(ActivationResult, RuntimeState)``.

    Lazy on purpose. Standalone import must not require ``rt_runtime``; the
    merged sibling resolves to the package's own classes at call time, which is
    long after ``adapter.py`` has finished importing, so there is no cycle.
    """
    global _TYPES
    if _TYPES is None:
        try:
            from rt_runtime import adapter as _adapter  # type: ignore[import-not-found]

            _TYPES = (_adapter.ActivationResult, _adapter.RuntimeState)
        except Exception:
            _TYPES = (_FallbackActivationResult, _FallbackRuntimeState)
    return _TYPES


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _coerce_path(value: Any, base: Path | None = None) -> Path | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    path = Path(str(value)).expanduser()
    if base is not None and not path.is_absolute():
        path = base / path
    return Path(os.path.abspath(os.fspath(path)))


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _state_value(state: Any) -> str:
    """Normalise an S1 ``SidecarState`` enum, a string, or a fake to a string."""
    value = getattr(state, "value", state)
    return str(value)


def _major_minor(value: Any) -> str | None:
    match = re.match(r"\s*(\d+)\.(\d+)", str(value or ""))
    return f"{match.group(1)}.{match.group(2)}" if match else None


def _version_matches(value: Any, expected: Any) -> bool:
    left = _major_minor(value)
    right = _major_minor(expected)
    return bool(left and right and left == right)


def _python_from_path(path: Any) -> str | None:
    """Infer ``3.14`` from ``python314.exe`` / ``python3.14.exe``; else None."""
    if not path:
        return None
    name = Path(str(path)).name
    match = re.search(r"python[-_]?(\d)(?:[.](\d+)|(\d+))", name, re.IGNORECASE)
    if not match:
        return None
    major = match.group(1)
    minor = match.group(2) or match.group(3)
    return f"{major}.{minor}"


def _package_version(site_packages: Path | None, name: str) -> str | None:
    """Read a package version from its ``.dist-info`` folder, without importing."""
    if site_packages is None:
        return None
    patterns = (f"{name}-*.dist-info", f"{name.replace('-', '_')}-*.dist-info")
    try:
        for pattern in patterns:
            for candidate in sorted(site_packages.glob(pattern)):
                text = candidate.name[: -len(".dist-info")]
                version = text.split("-", 1)[1] if "-" in text else ""
                if version:
                    return version
    except OSError:
        return None
    return None


def _scan_package(site_packages: Path | None, name: str) -> "PackageInfo":
    present = False
    binaries: list[str] = []
    if site_packages is not None:
        package_dir = Path(site_packages) / name
        try:
            present = package_dir.is_dir()
            if present:
                for pattern in ("*.pyd", "*.so", "*.dll", "*/*.pyd", "*/*.so"):
                    binaries.extend(
                        sorted(item.name for item in package_dir.glob(pattern))
                    )
        except OSError:
            present = False
    return PackageInfo(
        name=name,
        present=present,
        binaries=tuple(sorted(set(binaries))),
        version=_package_version(site_packages, name),
    )


def _runtime_tree_report(home: Path | None) -> dict[str, Any]:
    """Facts about the runtime tree as a launcher. Reported, never blocking."""
    if home is None:
        return {
            "home": None,
            "python_exe": False,
            "pythonw_exe": False,
            "python314_dll": False,
            "missing_core_dlls": list(_CORE_DLLS),
        }
    home = Path(home)
    missing: list[str] = []
    for dll in _CORE_DLLS:
        try:
            if not (home / "DLLs" / dll).is_file():
                missing.append(dll)
        except OSError:
            missing.append(dll)
    return {
        "home": str(home),
        "python_exe": _safe_is_file(home / "python.exe"),
        "pythonw_exe": _safe_is_file(home / "pythonw.exe"),
        "python314_dll": _safe_is_file(home / "python314.dll"),
        "missing_core_dlls": missing,
    }


def _safe_is_file(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        return Path(path).is_file()
    except OSError:
        return False


def _safe_is_dir(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        return Path(path).is_dir()
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Config: mode + sidecar section
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SidecarSettings:
    """Validated ``sidecar`` section for one target.

    All paths are absolute after ``from_mapping``. ``runtime_home`` and
    ``site_packages`` may be inherited from the target's own fields when the
    section omits them (see :func:`settings_for_target`).
    """

    instance: str = "default"
    launcher: Path | None = None
    host_script: Path | None = None
    state_dir: Path | None = None
    runtime_home: Path | None = None
    site_packages: Path | None = None
    base_site_packages: Path | None = None
    entry_point: str | None = None
    port: int = 0
    package_precedence: str = PRECEDENCE_RUNTIME_FIRST
    allow_degraded_base_first: bool = False
    critical_packages: tuple[str, ...] = DEFAULT_CRITICAL_PACKAGES
    start_timeout: float = 10.0
    health_timeout: float = 3.0
    stop_grace: float = 10.0
    health_interval: float = 0.1
    require_health: bool = True
    parent_watchdog: bool = True
    expect_python: str = DEFAULT_EXPECT_PYTHON
    strict_sys_prefix: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "instance", str(self.instance or "default"))
        for name in (
            "launcher",
            "host_script",
            "state_dir",
            "runtime_home",
            "site_packages",
            "base_site_packages",
        ):
            object.__setattr__(self, name, _coerce_path(getattr(self, name)))

        object.__setattr__(
            self,
            "entry_point",
            None if self.entry_point is None else str(self.entry_point),
        )

        precedence = str(self.package_precedence or "").strip().lower()
        if precedence not in _PRECEDENCES:
            raise SidecarConfigError(
                f"sidecar.package_precedence must be one of "
                f"{sorted(_PRECEDENCES)}, got {self.package_precedence!r}"
            )
        object.__setattr__(self, "package_precedence", precedence)

        port = _as_int(self.port)
        if port is None or port < 0 or port > 65535:
            raise SidecarConfigError(f"sidecar.port out of range: {self.port!r}")
        object.__setattr__(self, "port", port)

        for name in (
            "allow_degraded_base_first",
            "require_health",
            "parent_watchdog",
            "strict_sys_prefix",
        ):
            if not isinstance(getattr(self, name), bool):
                raise SidecarConfigError(f"sidecar.{name} must be true or false")

        for name in ("start_timeout", "health_timeout", "stop_grace", "health_interval"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SidecarConfigError(f"sidecar.{name} must be a number")
            if float(value) <= 0:
                raise SidecarConfigError(f"sidecar.{name} must be greater than zero")
            object.__setattr__(self, name, float(value))

        packages = self.critical_packages
        if isinstance(packages, str):
            packages = (packages,)
        cleaned = tuple(str(item) for item in packages if str(item).strip())
        if not cleaned:
            raise SidecarConfigError("sidecar.critical_packages must name at least one package")
        object.__setattr__(self, "critical_packages", cleaned)

        expect = str(self.expect_python or "").strip()
        if not _major_minor(expect):
            raise SidecarConfigError(
                f"sidecar.expect_python must be a major.minor version, got {expect!r}"
            )
        object.__setattr__(self, "expect_python", expect)

    @classmethod
    def from_mapping(
        cls, raw: Mapping[str, Any] | None, base: Path | None = None
    ) -> "SidecarSettings":
        """Build settings from a JSON object. Unknown keys are ignored."""
        if raw is None:
            return cls()
        if not isinstance(raw, Mapping):
            raise SidecarConfigError("sidecar section must be a JSON object")

        kwargs: dict[str, Any] = {}
        passthrough = (
            "instance",
            "port",
            "package_precedence",
            "allow_degraded_base_first",
            "critical_packages",
            "start_timeout",
            "health_timeout",
            "stop_grace",
            "health_interval",
            "require_health",
            "parent_watchdog",
            "expect_python",
            "strict_sys_prefix",
            "entry_point",
        )
        for key in passthrough:
            if key in raw:
                kwargs[key] = raw[key]

        # Paths resolve against the config file's folder, like the target's own
        # home/site_packages do in adapter.load_targets.
        for key in (
            "launcher",
            "host_script",
            "state_dir",
            "runtime_home",
            "site_packages",
            "base_site_packages",
        ):
            if key in raw:
                kwargs[key] = _coerce_path(raw[key], base)
            else:
                kwargs[key] = None

        return cls(**kwargs)


def target_mode(raw: Mapping[str, Any]) -> str:
    """Return ``"in_process"`` (default) or ``"sidecar"`` for one target."""
    if not isinstance(raw, Mapping):
        raise SidecarConfigError("runtime target must be a JSON object")
    mode = str(raw.get("mode") or MODE_IN_PROCESS).strip().lower()
    if mode not in (MODE_IN_PROCESS, MODE_SIDECAR):
        raise SidecarConfigError(
            f"runtime target mode must be {MODE_IN_PROCESS!r} or "
            f"{MODE_SIDECAR!r}, got {raw.get('mode')!r}"
        )
    return mode


def settings_for_target(
    raw: Mapping[str, Any],
    base: Path | None = None,
    *,
    target_home: Any = None,
    target_site_packages: Any = None,
) -> SidecarSettings:
    """Build the resolved settings for one target.

    The ``sidecar`` block may omit ``runtime_home`` and ``site_packages``; they
    are inherited from the target's own fields so a config does not repeat
    itself. In-process targets may omit the block entirely.
    """
    section = raw.get("sidecar")
    settings = SidecarSettings.from_mapping(section, base)

    runtime_home = settings.runtime_home or _coerce_path(
        raw.get("home") if target_home is None else target_home, base
    )
    site_packages = settings.site_packages or _coerce_path(
        raw.get("site_packages") if target_site_packages is None else target_site_packages,
        base,
    )
    entry_point = settings.entry_point or raw.get("entry_point")
    return replace(
        settings,
        runtime_home=runtime_home,
        site_packages=site_packages,
        entry_point=None if entry_point is None else str(entry_point),
    )


def read_target_config(config_path: Any, target_name: str) -> tuple[dict[str, Any], Path]:
    """Return ``(raw target mapping, config folder)`` from a rt_toggle config.

    Accepts the two shapes the existing loader accepts: ``{"targets": {...}}``
    and a top-level ``{name: target}`` mapping. Raises ``LookupError`` when the
    target is absent and ``SidecarConfigError`` when the file is not usable.
    """
    path = Path(config_path).expanduser()
    base = Path(os.path.abspath(os.fspath(path))).parent
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SidecarConfigError(f"runtime config not found: {path}") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise SidecarConfigError(f"invalid JSON in runtime config {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise SidecarConfigError("rt_toggle config must be a JSON object")

    targets = data.get("targets")
    if isinstance(targets, dict):
        raw = targets.get(target_name)
        if isinstance(raw, dict):
            entry = dict(raw)
            entry.setdefault("name", target_name)
            return entry, base
        raise LookupError(f"runtime target {target_name!r} is not defined in {path}")

    if isinstance(targets, list):
        for item in targets:
            if isinstance(item, dict) and item.get("name") == target_name:
                return dict(item), base
        raise LookupError(f"runtime target {target_name!r} is not defined in {path}")

    raw = data.get(target_name)
    if isinstance(raw, dict):
        return dict(raw), base
    raise LookupError(f"runtime target {target_name!r} is not defined in {path}")


def sidecar_settings_from_config(
    config_path: Any, target_name: str
) -> tuple[str, SidecarSettings | None]:
    """Return ``(mode, settings)``. ``settings`` is None for in-process targets."""
    raw, base = read_target_config(config_path, target_name)
    mode = target_mode(raw)
    if mode != MODE_SIDECAR:
        return mode, None
    return mode, settings_for_target(raw, base)


# ---------------------------------------------------------------------------
# Integrity and package precedence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PackageInfo:
    """One critical package as found in a site-packages tree."""

    name: str
    present: bool
    binaries: tuple[str, ...]
    version: str | None


@dataclass(frozen=True)
class IntegrityReport:
    """Whether the runtime tree may take precedence over the base interpreter."""

    ok: bool
    blockers: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    runtime: dict[str, PackageInfo] = field(default_factory=dict)
    base: dict[str, PackageInfo] = field(default_factory=dict)
    runtime_tree: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PrecedenceDecision:
    """The effective package order, after applying the fallback policy."""

    requested: str
    effective: str
    degraded: bool
    ok: bool
    reasons: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()


def default_integrity(settings: SidecarSettings) -> IntegrityReport:
    """Filesystem-only integrity check for the runtime tree.

    RT-first means ``sys.path.insert(0, runtime_site_packages)``: every import
    would resolve from the runtime tree first. That is only safe when each
    critical package in the runtime tree carries a loadable compiled extension.
    Base-first appends the tree instead, so the base interpreter's working
    copies win. No module is imported here.
    """
    blockers: list[str] = []
    notes: list[str] = []
    runtime: dict[str, PackageInfo] = {}
    base: dict[str, PackageInfo] = {}

    runtime_site = settings.site_packages
    if not _safe_is_dir(runtime_site):
        blockers.append(f"runtime site-packages does not exist: {runtime_site}")
    else:
        for name in settings.critical_packages:
            info = _scan_package(runtime_site, name)
            runtime[name] = info
            if not info.present:
                notes.append(f"runtime {name}: not installed in the runtime tree")
            elif not info.binaries:
                version = f" {info.version}" if info.version else ""
                blockers.append(
                    f"runtime {name}{version} has no compiled extension "
                    "(cp314 binary missing); runtime-first would shadow a "
                    "working base copy"
                )

    base_site = settings.base_site_packages
    if not _safe_is_dir(base_site):
        notes.append(
            f"base site-packages not found: {base_site}; base-first fallback "
            "is unavailable"
        )
    else:
        for name in settings.critical_packages:
            info = _scan_package(base_site, name)
            base[name] = info
            if info.present and info.binaries:
                version = f" {info.version}" if info.version else ""
                notes.append(
                    f"base {name}{version} has a compiled extension; "
                    "base-first fallback is viable"
                )
            elif info.present:
                notes.append(
                    f"base {name}: present without a compiled extension; "
                    "base-first will not repair the runtime tree"
                )

    return IntegrityReport(
        ok=not blockers,
        blockers=tuple(blockers),
        notes=tuple(notes),
        runtime=runtime,
        base=base,
        runtime_tree=_runtime_tree_report(settings.runtime_home),
    )


def decide_precedence(
    settings: SidecarSettings, integrity: IntegrityReport | None = None
) -> PrecedenceDecision:
    """Apply the RT-first default and the explicit degraded fallback rule.

    * ``runtime-first`` with a healthy tree -> RT-first.
    * ``runtime-first`` with blockers, ``allow_degraded_base_first=true`` ->
      base-first, flagged degraded, with every blocker visible.
    * ``runtime-first`` with blockers, flag absent -> refused.
    * ``base-first`` requested -> allowed only with the explicit flag; it is
      always flagged degraded, because base copies win by definition.
    """
    report = integrity if integrity is not None else default_integrity(settings)
    requested = settings.package_precedence

    if requested == PRECEDENCE_RUNTIME_FIRST:
        if report.ok:
            return PrecedenceDecision(
                requested=requested,
                effective=PRECEDENCE_RUNTIME_FIRST,
                degraded=False,
                ok=True,
                reasons=(
                    "runtime-first: runtime site-packages take precedence over "
                    "the base interpreter",
                ),
            )
        if settings.allow_degraded_base_first:
            return PrecedenceDecision(
                requested=requested,
                effective=PRECEDENCE_BASE_FIRST,
                degraded=True,
                ok=True,
                reasons=(
                    "DEGRADED base-first fallback: allow_degraded_base_first=true "
                    "and the runtime tree cannot safely take precedence",
                    *report.blockers,
                ),
            )
        return PrecedenceDecision(
            requested=requested,
            effective=PRECEDENCE_RUNTIME_FIRST,
            degraded=False,
            ok=False,
            blockers=(
                *report.blockers,
                "set sidecar.allow_degraded_base_first=true to run degraded "
                "(base packages win), or repair the runtime tree",
            ),
        )

    # base-first was asked for explicitly.
    if not settings.allow_degraded_base_first:
        return PrecedenceDecision(
            requested=requested,
            effective=PRECEDENCE_BASE_FIRST,
            degraded=True,
            ok=False,
            blockers=(
                "base-first requires sidecar.allow_degraded_base_first=true; "
                "base-first means base interpreter packages win over the runtime "
                "tree",
            ),
        )
    return PrecedenceDecision(
        requested=requested,
        effective=PRECEDENCE_BASE_FIRST,
        degraded=True,
        ok=True,
        reasons=(
            "DEGRADED base-first requested explicitly: base interpreter packages "
            "take precedence over the runtime tree",
            *report.blockers,
        ),
    )


# ---------------------------------------------------------------------------
# Health result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HealthResult:
    ok: bool
    detail: str
    code: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Lazy imports of the S1/S2 modules
# ---------------------------------------------------------------------------


def _work_dir_candidates() -> list[Path]:
    candidates: list[Path] = []
    explicit = os.environ.get("RT_SIDECAR_WORK_DIR")
    if explicit:
        candidates.append(Path(explicit))
    temp = os.environ.get("TEMP") or os.environ.get("TMP")
    if temp:
        candidates.append(Path(temp) / "rt-sidecar" / "work")
    return candidates


def _load_sidecar_modules() -> tuple[Any, Any]:
    """Import ``(lifecycle, transport)`` without copying them into the repo."""
    errors: list[str] = []

    try:  # post-merge package layout
        from .sidecar import lifecycle as _lifecycle  # type: ignore[import-not-found]
        from .sidecar import transport as _transport  # type: ignore[import-not-found]

        return _lifecycle, _transport
    except Exception as exc:  # noqa: BLE001 - fall through to the work dir
        errors.append(f"rt_runtime.sidecar: {exc!r}")

    import importlib

    for base in _work_dir_candidates():
        if not (base / "sidecar" / "lifecycle.py").is_file():
            continue
        if str(base) not in sys.path:
            sys.path.insert(0, str(base))
        try:
            lifecycle = importlib.import_module("sidecar.lifecycle")
            transport = importlib.import_module("sidecar.transport")
            return lifecycle, transport
        except Exception as exc:  # noqa: BLE001 - report every attempt
            errors.append(f"{base}: {exc!r}")

    raise SidecarUnavailable(
        "cannot import the sidecar modules (lifecycle, transport); tried "
        + "; ".join(errors or ["no work dir candidate found"])
    )


def _default_health_checker(base_url: str, timeout: float) -> tuple[int, dict[str, Any]]:
    _, transport = _load_sidecar_modules()
    return transport.request(
        base_url, "/health", method="GET", timeout=timeout
    )


# ---------------------------------------------------------------------------
# Internal preflight
# ---------------------------------------------------------------------------


@dataclass
class _Preflight:
    ok: bool
    refused: bool
    available: bool
    reasons: list[str]
    blockers: list[str]
    policy: PrecedenceDecision
    integrity: IntegrityReport


@dataclass
class _StartOutcome:
    ok: bool
    state: str
    pid: int | None
    changed: bool
    reasons: list[str]
    error: str | None


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------


class SidecarAdapter:
    """Sidecar-mode implementation of the RuntimeAdapter public surface.

    Injected seams (all optional, used by tests so nothing is spawned):

    * ``supervisor_factory(**kwargs)`` -> an S1 ``SidecarSupervisor``-shaped
      object with ``probe()``, ``start(force=False)`` and ``stop(force=False)``.
      The kwargs mirror ``SidecarSupervisor.__init__``: ``launcher`` (a list,
      already carrying ``-I -X utf8 -u``), ``host_script``, ``state_dir``,
      ``instance``, ``runtime_home``, ``site_packages``, ``env``, ``parent_pid``,
      ``start_timeout``, ``stop_grace`` and ``expect_python``.
    * ``health_checker(base_url, timeout)`` -> ``(status_code, body_dict)``.
      The default is ``transport.request(base_url, "/health", ...)``.
    * ``integrity_checker(settings)`` -> :class:`IntegrityReport`.
    * ``clock`` / ``sleep`` for deterministic timeout tests.
    """

    def __init__(
        self,
        target_name: str,
        settings: SidecarSettings,
        *,
        target_home: Any = None,
        target_site_packages: Any = None,
        supervisor_factory: Callable[..., Any] | None = None,
        health_checker: Callable[[str, float], tuple[int, dict[str, Any]]] | None = None,
        integrity_checker: Callable[[SidecarSettings], IntegrityReport] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.target_name = str(target_name)
        self._supervisor_factory = supervisor_factory
        self._health_checker = health_checker or _default_health_checker
        self._integrity_checker = integrity_checker or default_integrity
        self._clock = clock
        self._sleep = sleep

        runtime_home = settings.runtime_home or _coerce_path(target_home)
        site_packages = settings.site_packages or _coerce_path(target_site_packages)
        launcher = _resolve_launcher(settings, runtime_home)
        host_script = _resolve_host_script(settings)
        state_dir = _resolve_state_dir(settings)
        base_site_packages = _resolve_base_site_packages(settings, launcher)

        # One resolved copy: every method below sees absolute, inherited paths.
        self.settings = replace(
            settings,
            runtime_home=runtime_home,
            site_packages=site_packages,
            launcher=launcher,
            host_script=host_script,
            state_dir=state_dir,
            base_site_packages=base_site_packages,
        )

    # -- resolved paths -----------------------------------------------------

    @property
    def launcher(self) -> Path | None:
        return self.settings.launcher

    @property
    def host_script(self) -> Path | None:
        return self.settings.host_script

    @property
    def state_dir(self) -> Path | None:
        return self.settings.state_dir

    @property
    def runtime_home(self) -> Path | None:
        return self.settings.runtime_home

    @property
    def site_packages(self) -> Path | None:
        return self.settings.site_packages

    @property
    def base_site_packages(self) -> Path | None:
        return self.settings.base_site_packages

    # -- integrity / policy -------------------------------------------------

    def integrity(self) -> IntegrityReport:
        try:
            report = self._integrity_checker(self.settings)
        except Exception as exc:  # a broken check means "not safe"
            return IntegrityReport(
                ok=False,
                blockers=(f"integrity check failed: {exc!r}",),
                runtime_tree=_runtime_tree_report(self.runtime_home),
            )
        if not isinstance(report, IntegrityReport):
            raise SidecarConfigError("integrity_checker must return IntegrityReport")
        return report

    def policy(self, integrity: IntegrityReport | None = None) -> PrecedenceDecision:
        return decide_precedence(
            self.settings, integrity if integrity is not None else self.integrity()
        )

    # -- supervisor ---------------------------------------------------------

    def _launcher_argv(self) -> list[str]:
        if self.launcher is None:
            raise SidecarConfigError(
                "no sidecar launcher: set `launcher` in rt_toggle.json or "
                "RT_SIDECAR_PYTHON to the runtime tree's python.exe"
            )
        return [str(self.launcher), "-I", "-X", "utf8", "-u"]

    def _child_env(self, policy: PrecedenceDecision) -> dict[str, str]:
        env = {"RT_SIDECAR_PACKAGE_PRECEDENCE": policy.effective}
        if policy.effective == PRECEDENCE_RUNTIME_FIRST:
            # Read by the Tier 1+ host: insert the runtime tree at the front of
            # sys.path instead of appending it. Env, not argv, so today's Tier 0
            # host stub (which uses argparse) ignores it safely.
            env["RT_SIDECAR_PREFER_RUNTIME"] = "1"
        return env

    def _supervisor(self, policy: PrecedenceDecision) -> Any:
        kwargs: dict[str, Any] = {
            "launcher": self._launcher_argv(),
            "host_script": self.host_script,
            "state_dir": self.state_dir,
            "instance": self.settings.instance,
            "runtime_home": self.runtime_home,
            "site_packages": [str(self.site_packages)] if self.site_packages else [],
            "env": self._child_env(policy),
            "parent_pid": os.getpid() if self.settings.parent_watchdog else None,
            "start_timeout": self.settings.start_timeout,
            "stop_grace": self.settings.stop_grace,
            "expect_python": self.settings.expect_python,
            # Health-required mode needs the control surface; the host stub
            # serves nothing without --serve.
            "serve": bool(self.settings.require_health),
            "entry_point": self.settings.entry_point,
        }
        if self._supervisor_factory is not None:
            return self._supervisor_factory(**kwargs)
        lifecycle, _ = _load_sidecar_modules()
        return lifecycle.SidecarSupervisor(**kwargs)

    # -- preflight ----------------------------------------------------------

    def _preflight(self) -> _Preflight:
        reasons: list[str] = []
        blockers: list[str] = []
        integrity = self.integrity()
        policy = self.policy(integrity)

        launcher_ok = _safe_is_file(self.launcher)
        if not launcher_ok:
            blockers.append(
                f"launcher not found: {self.launcher}. Set `launcher` in "
                "rt_toggle.json or RT_SIDECAR_PYTHON to the runtime tree's "
                "python.exe."
            )
        launcher_python = _python_from_path(self.launcher)
        expect_python = self.settings.expect_python
        if _major_minor(expect_python) != _major_minor(DEFAULT_EXPECT_PYTHON):
            blockers.append(
                f"sidecar requires CPython {DEFAULT_EXPECT_PYTHON}; "
                f"sidecar.expect_python is {expect_python!r}"
            )
        elif launcher_python and not _version_matches(launcher_python, expect_python):
            blockers.append(
                f"launcher is Python {launcher_python}; the sidecar requires "
                f"Python {expect_python}"
            )
        elif launcher_python is None and launcher_ok:
            reasons.append(
                "launcher filename does not encode a Python version; the host "
                "asserts it at launch with --expect-python"
            )

        host_ok = _safe_is_file(self.host_script)
        if not host_ok:
            blockers.append(f"host script not found: {self.host_script}")

        runtime_home_ok = _safe_is_dir(self.runtime_home)
        if not runtime_home_ok:
            blockers.append(f"runtime home does not exist: {self.runtime_home}")

        site_packages_ok = _safe_is_dir(self.site_packages)
        if not site_packages_ok:
            blockers.append(f"runtime site-packages does not exist: {self.site_packages}")

        if policy.effective == PRECEDENCE_BASE_FIRST and not _safe_is_dir(
            self.base_site_packages
        ):
            blockers.append(
                f"base-first was selected but base site-packages does not exist: "
                f"{self.base_site_packages}"
            )

        if policy.ok:
            reasons.extend(policy.reasons)
        else:
            blockers.extend(policy.blockers)

        return _Preflight(
            ok=not blockers,
            refused=bool(blockers),
            available=launcher_ok and host_ok and site_packages_ok,
            reasons=reasons,
            blockers=blockers,
            policy=policy,
            integrity=integrity,
        )

    # -- health -------------------------------------------------------------

    @staticmethod
    def base_url_for(report: Any) -> str | None:
        document = getattr(report, "status", None) or {}
        port = _as_int(document.get("port"))
        if port is None or port <= 0:
            return None
        host = str(document.get("host") or "127.0.0.1")
        if host not in ("127.0.0.1", "::1", "localhost"):
            host = "127.0.0.1"
        if ":" in host:
            host = f"[{host}]"
        return f"http://{host}:{port}"

    def _health_verdict(
        self, code: int | None, body: Mapping[str, Any], expected_pid: int | None
    ) -> tuple[bool, str]:
        if code != 200:
            return False, f"/health returned HTTP {code!r}"
        if not body.get("ok"):
            return False, "/health body is not ok"
        version = str(body.get("python_version") or "")
        if version and not _version_matches(version, self.settings.expect_python):
            return False, (
                f"/health reports Python {version}; expected "
                f"{self.settings.expect_python}"
            )
        pid = _as_int(body.get("pid"))
        if expected_pid is not None and pid is not None and pid != expected_pid:
            return False, (
                f"/health pid {pid} does not match the status pid {expected_pid}"
            )
        state = body.get("status")
        if state is not None and str(state) != _STATUS_READY:
            return False, f"/health reports state {state!r}"
        return True, f"/health 200 (pid {pid}, python {version or 'unknown'})"

    def _wait_health(
        self, report: Any, timeout: float | None = None
    ) -> HealthResult:
        if not self.settings.require_health:
            return HealthResult(
                ok=True, detail="health check disabled by sidecar.require_health=false"
            )
        base_url = self.base_url_for(report)
        if base_url is None:
            return HealthResult(
                ok=False,
                detail=(
                    "status published no port; GET /health cannot be verified "
                    "(is the S2 transport installed?)"
                ),
            )

        document = getattr(report, "status", None) or {}
        expected_pid = _as_int(document.get("pid")) or _as_int(
            getattr(report, "pid", None)
        )
        limit = self.settings.health_timeout if timeout is None else float(timeout)
        deadline = self._clock() + max(0.0, limit)
        interval = max(0.01, self.settings.health_interval)
        last_detail = "no response"
        last_code: int | None = None
        last_body: dict[str, Any] = {}

        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            try:
                code, body = self._health_checker(base_url, min(1.0, remaining))
            except Exception as exc:  # noqa: BLE001 - a refused socket is a retry
                last_detail = f"/health request failed: {exc!r}"
                last_code, last_body = None, {}
            else:
                ok, detail = self._health_verdict(code, body, expected_pid)
                last_code, last_body = code, dict(body)
                if ok:
                    return HealthResult(True, detail, code, dict(body))
                last_detail = detail
                if code in (401, 403, 404, 405):
                    # Definitive misconfiguration; retrying cannot help.
                    return HealthResult(False, detail, code, dict(body))
            self._sleep(interval)

        return HealthResult(
            False,
            f"/health did not become healthy within {limit:.1f}s: {last_detail}",
            last_code,
            last_body,
        )

    # -- status -------------------------------------------------------------

    def _snapshot(self) -> dict[str, Any]:
        try:
            supervisor = self._supervisor(self.policy())
            report = supervisor.probe()
        except Exception as exc:  # noqa: BLE001 - status must never raise
            return {
                "runtime_state": "error",
                "sidecar_state": "error",
                "pid": None,
                "heartbeat_age": None,
                "detail": f"sidecar probe failed: {exc!r}",
                "python_version": None,
                "sys_prefix": None,
                "port": None,
                "status": {},
                "lock": None,
                "log_tail": [],
                "reasons": [f"sidecar probe failed: {exc!r}"],
                "last_error": str(exc),
            }

        state = _state_value(getattr(report, "state", _STATUS_ERROR))
        document = dict(getattr(report, "status", None) or {})
        reasons: list[str] = []
        detail = str(getattr(report, "detail", "") or "")

        runtime_state = {
            _STATUS_READY: "on",
            _STATUS_STARTING: "on",
            _STATUS_STOPPING: "on",
            _STATUS_ABSENT: "off",
            _STATUS_STOPPED: "off",
            _STATUS_STALE: "error",
            _STATUS_UNRESPONSIVE: "error",
            _STATUS_ERROR: "error",
        }.get(state, "error")

        if state == _STATUS_READY:
            version = document.get("python_version")
            if not version:
                runtime_state = "error"
                reasons.append(
                    "status is ready but does not report python_version; "
                    "readiness cannot be verified"
                )
            elif not _version_matches(version, self.settings.expect_python):
                runtime_state = "error"
                reasons.append(
                    f"sidecar is running Python {version}; expected "
                    f"{self.settings.expect_python}"
                )
            prefix = document.get("sys_prefix")
            if (
                self.settings.strict_sys_prefix
                and runtime_state == "on"
                and self.runtime_home is not None
                and prefix
            ):
                try:
                    same = Path(str(prefix)).resolve() == Path(
                        self.runtime_home
                    ).resolve()
                except OSError:
                    same = False
                if not same:
                    runtime_state = "error"
                    reasons.append(
                        f"sys_prefix {prefix!r} does not resolve to the runtime "
                        f"home {self.runtime_home}"
                    )

        if detail and runtime_state == "error":
            reasons.append(detail)
        last_error = document.get("last_error")
        if last_error:
            reasons.append(str(last_error))

        return {
            "runtime_state": runtime_state,
            "sidecar_state": state,
            "pid": getattr(report, "pid", None),
            "heartbeat_age": getattr(report, "heartbeat_age", None),
            "detail": detail,
            "python_version": document.get("python_version"),
            "sys_prefix": document.get("sys_prefix"),
            "port": document.get("port"),
            "status": document,
            "lock": getattr(report, "lock", None),
            "log_tail": list(getattr(report, "log_tail", []) or []),
            "reasons": reasons,
            "last_error": last_error,
        }

    def status(self) -> Any:
        """Tri-state, file-based, read-only. Matches the in-process contract."""
        _, RuntimeState = _adapter_types()
        snap = self._snapshot()
        if snap["runtime_state"] == "on":
            return RuntimeState.ON
        if snap["runtime_state"] == "off":
            return RuntimeState.OFF
        return RuntimeState.ERROR

    def status_detail(self, *, check_health: bool = False) -> dict[str, Any]:
        """Merged status: supervisor document + adapter state machine."""
        snap = self._snapshot()
        policy = self.policy()
        snap.update(
            {
                "mode": MODE_SIDECAR,
                "target": self.target_name,
                "package_precedence": policy.effective,
                "package_precedence_requested": policy.requested,
                "precedence_degraded": policy.degraded,
            }
        )
        if check_health:
            try:
                supervisor = self._supervisor(policy)
                report = supervisor.probe()
                if _state_value(getattr(report, "state", "")) == _STATUS_READY:
                    health = self._wait_health(report, timeout=1.0)
                    snap["health"] = {
                        "ok": health.ok,
                        "detail": health.detail,
                        "code": health.code,
                    }
                    if not health.ok and snap["runtime_state"] == "on":
                        snap["runtime_state"] = "error"
                        snap["reasons"].append(health.detail)
            except Exception as exc:  # noqa: BLE001
                snap["health"] = {"ok": False, "detail": repr(exc), "code": None}
        return snap

    # -- turns --------------------------------------------------------------

    def turn_control(self) -> Any:
        """Return a bound turn client, or ``None`` when no sidecar is ready.

        Read-only: one supervisor probe, no spawn, no write. The token is read
        per call later, because a restart rotates it.
        """
        try:
            supervisor = self._supervisor(self.policy())
            report = supervisor.probe()
        except Exception:  # noqa: BLE001 - "not running" is a normal answer
            return None
        if _state_value(getattr(report, "state", "")) != _STATUS_READY:
            return None
        base_url = self.base_url_for(report)
        state_dir = self.state_dir
        if base_url is None or state_dir is None:
            return None

        from .turn_control import TurnControl

        control = TurnControl(base_url=base_url, state_dir=Path(state_dir))
        try:
            control.token()
        except Exception:  # noqa: BLE001 - no readable token, no usable surface
            return None
        return control

    # -- probe --------------------------------------------------------------

    def probe(self) -> dict[str, Any]:
        """Read-only diagnostics. Never mutates, never raises, never spawns."""
        try:
            pre = self._preflight()
            snap = self._snapshot()
            policy = pre.policy
            integrity = pre.integrity
            site_packages_exists = _safe_is_dir(self.site_packages)

            def _packages(mapping: Mapping[str, PackageInfo]) -> dict[str, Any]:
                return {
                    name: {
                        "present": info.present,
                        "version": info.version,
                        "binaries": list(info.binaries),
                    }
                    for name, info in mapping.items()
                }

            return {
                # Same top-level keys as the in-process probe, so a panel can
                # render either mode without branching on shape.
                "mode": MODE_SIDECAR,
                "target": self.target_name,
                "available": pre.available,
                "python_version": self.settings.expect_python,
                "site_packages_exists": site_packages_exists,
                "compatibility": {
                    "safe": pre.ok,
                    "reasons": list(pre.reasons),
                    "blockers": list(pre.blockers),
                },
                "active": snap["runtime_state"] == "on",
                # Sidecar-specific.
                "launcher": str(self.launcher) if self.launcher else None,
                "launcher_exists": _safe_is_file(self.launcher),
                "launcher_python": _python_from_path(self.launcher),
                "expect_python": self.settings.expect_python,
                "python_314": _version_matches(
                    self.settings.expect_python, DEFAULT_EXPECT_PYTHON
                ),
                "host_script": str(self.host_script) if self.host_script else None,
                "host_script_exists": _safe_is_file(self.host_script),
                "runtime_home": str(self.runtime_home) if self.runtime_home else None,
                "site_packages": (
                    str(self.site_packages) if self.site_packages else None
                ),
                "base_site_packages": (
                    str(self.base_site_packages) if self.base_site_packages else None
                ),
                "state_dir": str(self.state_dir) if self.state_dir else None,
                "package_precedence": policy.effective,
                "package_precedence_requested": policy.requested,
                "precedence_ok": policy.ok,
                "precedence_degraded": policy.degraded,
                "precedence_reasons": list(policy.reasons),
                "integrity": {
                    "ok": integrity.ok,
                    "blockers": list(integrity.blockers),
                    "notes": list(integrity.notes),
                    "runtime_packages": _packages(integrity.runtime),
                    "base_packages": _packages(integrity.base),
                    "runtime_tree": dict(integrity.runtime_tree),
                },
                "sidecar_state": snap["sidecar_state"],
                "runtime_state": snap["runtime_state"],
                "pid": snap["pid"],
                "port": snap["port"],
                "running_python_version": snap["python_version"],
                "sys_prefix": snap["sys_prefix"],
                "heartbeat_age": snap["heartbeat_age"],
                "reasons": list(pre.reasons) + list(snap["reasons"]),
                "error": snap["last_error"],
            }
        except Exception as exc:  # noqa: BLE001 - probe is a diagnostic surface
            return {
                "mode": MODE_SIDECAR,
                "target": self.target_name,
                "available": False,
                "python_version": self.settings.expect_python,
                "site_packages_exists": False,
                "compatibility": {
                    "safe": False,
                    "reasons": [f"probe failed: {exc!r}"],
                    "blockers": [],
                },
                "active": False,
                "runtime_state": "error",
                "reasons": [f"probe failed: {exc!r}"],
                "error": repr(exc),
            }

    # -- activate -----------------------------------------------------------

    def _success_reasons(
        self,
        report: Any,
        pre: _Preflight,
        health: HealthResult,
        *,
        started: bool,
    ) -> list[str]:
        document = getattr(report, "status", None) or {}
        pid = document.get("pid") or getattr(report, "pid", None)
        port = document.get("port")
        reasons: list[str] = []
        if not started:
            reasons.append("already running")
        reasons.append(f"sidecar ready (pid {pid}, port {port})")
        if document.get("python_version"):
            reasons.append(f"python {document.get('python_version')}")
        reasons.append(health.detail)
        reasons.append(f"package precedence: {pre.policy.effective}")
        if pre.policy.degraded:
            detail = "; ".join(pre.policy.reasons) or "runtime tree not precedence-ready"
            reasons.append(f"DEGRADED: {detail}")
        return reasons

    def _start_error(
        self, message: str, *, changed: bool, reasons: list[str] | None = None
    ) -> Any:
        ActivationResult, RuntimeState = _adapter_types()
        return ActivationResult(
            ok=False,
            state=RuntimeState.ERROR,
            changed=changed,
            reasons=list(reasons or []) + [message],
            error=message,
        )

    def _await_ready(self, supervisor: Any, result: Any) -> _StartOutcome:
        state = _state_value(getattr(result, "state", _STATUS_ERROR))
        ok = bool(getattr(result, "ok", False))
        pid = _as_int(getattr(result, "pid", None))
        changed = bool(getattr(result, "changed", False))
        reasons = [str(item) for item in (getattr(result, "reasons", None) or [])]
        error = getattr(result, "error", None)

        if ok and state == _STATUS_READY:
            return _StartOutcome(True, state, pid, changed, reasons, None)
        if not ok:
            return _StartOutcome(
                False,
                state,
                pid,
                changed,
                reasons,
                str(error or f"supervisor start failed in state {state!r}"),
            )

        # ``ok`` but not yet ready: the supervisor may return before the host
        # publishes ready. Poll the file, bounded by start_timeout.
        deadline = self._clock() + self.settings.start_timeout
        while self._clock() < deadline:
            try:
                report = supervisor.probe()
            except Exception as exc:  # noqa: BLE001
                return _StartOutcome(
                    False, _STATUS_ERROR, pid, changed, reasons, repr(exc)
                )
            current = _state_value(getattr(report, "state", _STATUS_ERROR))
            if current == _STATUS_READY:
                return _StartOutcome(
                    True,
                    current,
                    _as_int(getattr(report, "pid", None)) or pid,
                    changed,
                    reasons,
                    None,
                )
            if current in (_STATUS_ERROR, _STATUS_STALE, _STATUS_UNRESPONSIVE):
                return _StartOutcome(
                    False,
                    current,
                    _as_int(getattr(report, "pid", None)) or pid,
                    changed,
                    reasons,
                    str(getattr(report, "detail", "") or f"supervisor state {current}"),
                )
            self._sleep(0.05)

        return _StartOutcome(
            False,
            _STATUS_STARTING,
            pid,
            changed,
            reasons,
            f"sidecar did not become ready within {self.settings.start_timeout:.1f}s",
        )

    def _safe_stop(self, supervisor: Any) -> bool:
        try:
            result = supervisor.stop()
        except Exception:  # noqa: BLE001 - rollback must not mask the first error
            return False
        return bool(getattr(result, "ok", False))

    def activate(self) -> Any:
        """Start the sidecar and verify it. Idempotent and fail-safe."""
        ActivationResult, RuntimeState = _adapter_types()
        with _GLOBAL_LOCK:
            pre = self._preflight()
            if pre.refused:
                # REFUSED: we declined before spawning. Nothing changed, so the
                # state is OFF (matching the in-process compat refusal) and the
                # reasons name every blocker.
                return ActivationResult(
                    ok=False,
                    state=RuntimeState.OFF,
                    changed=False,
                    reasons=list(pre.blockers) + list(pre.reasons),
                    error=None,
                )

            try:
                supervisor = self._supervisor(pre.policy)
            except Exception as exc:  # noqa: BLE001
                return self._start_error(
                    f"cannot create the sidecar supervisor: {exc!r}", changed=False
                )

            try:
                report = supervisor.probe()
            except Exception as exc:  # noqa: BLE001
                return self._start_error(
                    f"cannot probe the sidecar: {exc!r}", changed=False
                )

            if _state_value(getattr(report, "state", "")) == _STATUS_READY:
                health = self._wait_health(report)
                if health.ok:
                    return ActivationResult(
                        ok=True,
                        state=RuntimeState.ON,
                        changed=False,
                        reasons=self._success_reasons(
                            report, pre, health, started=False
                        ),
                    )
                # An already-running sidecar we did not start is reported, not
                # killed. The operator chooses the restart.
                return ActivationResult(
                    ok=False,
                    state=RuntimeState.ERROR,
                    changed=False,
                    reasons=[
                        f"sidecar is ready but unhealthy: {health.detail}",
                        "run deactivate() then activate() to restart it",
                    ],
                    error=health.detail,
                )

            try:
                result = supervisor.start(force=False)
            except Exception as exc:  # noqa: BLE001
                return self._start_error(
                    f"supervisor.start() raised: {exc!r}", changed=False
                )

            outcome = self._await_ready(supervisor, result)
            if not outcome.ok:
                return ActivationResult(
                    ok=False,
                    state=RuntimeState.ERROR,
                    changed=outcome.changed,
                    reasons=outcome.reasons
                    + [f"supervisor state: {outcome.state}"],
                    error=outcome.error or "sidecar failed to start",
                )

            try:
                report = supervisor.probe()
            except Exception as exc:  # noqa: BLE001
                return self._start_error(
                    f"sidecar started but cannot be probed: {exc!r}",
                    changed=outcome.changed,
                )
            if _state_value(getattr(report, "state", "")) != _STATUS_READY:
                return self._start_error(
                    f"sidecar is {_state_value(getattr(report, 'state', 'unknown'))!r} "
                    "immediately after a ready result",
                    changed=outcome.changed,
                )

            health = self._wait_health(report)
            if health.ok:
                return ActivationResult(
                    ok=True,
                    state=RuntimeState.ON,
                    changed=outcome.changed,
                    reasons=self._success_reasons(
                        report, pre, health, started=outcome.changed
                    ),
                )

            rolled_back = self._safe_stop(supervisor)
            reasons = [f"started but /health failed: {health.detail}"]
            reasons.append(
                "rolled back: the sidecar was stopped"
                if rolled_back
                else "rollback failed: stop the sidecar manually"
            )
            return ActivationResult(
                ok=False,
                state=RuntimeState.ERROR,
                changed=outcome.changed,
                reasons=reasons,
                error=health.detail,
            )

    # -- deactivate ---------------------------------------------------------

    def _cleanup_off_files(self) -> list[str]:
        """Remove token/lock/flag only when the probe already says "off".

        S1 owns these files and its ``stop()`` removes them. This is the
        adapter's verification step for a record that is off but left debris:
        the probe established that no recorded pid is alive, so removal is the
        documented stale cleanup.
        """
        removed: list[str] = []
        if self.state_dir is None:
            return removed
        for name in ("token", "stop.flag", "sidecar.lock"):
            path = Path(self.state_dir) / name
            try:
                if path.exists():
                    path.unlink()
                    removed.append(f"removed leftover {name}")
            except OSError as exc:
                removed.append(f"could not remove {name}: {exc!r}")
        return removed

    def deactivate(self) -> Any:
        """Stop the sidecar gracefully, verify exit, remove token/lock."""
        ActivationResult, RuntimeState = _adapter_types()
        with _GLOBAL_LOCK:
            try:
                supervisor = self._supervisor(self.policy())
            except Exception as exc:  # noqa: BLE001
                return ActivationResult(
                    ok=False,
                    state=RuntimeState.ERROR,
                    changed=False,
                    reasons=[f"cannot create the sidecar supervisor: {exc!r}"],
                    error=repr(exc),
                )

            try:
                report = supervisor.probe()
            except Exception as exc:  # noqa: BLE001
                return ActivationResult(
                    ok=False,
                    state=RuntimeState.ERROR,
                    changed=False,
                    reasons=[f"cannot probe the sidecar: {exc!r}"],
                    error=repr(exc),
                )

            state = _state_value(getattr(report, "state", _STATUS_ERROR))
            if state in (_STATUS_ABSENT, _STATUS_STOPPED):
                cleaned = self._cleanup_off_files()
                return ActivationResult(
                    ok=True,
                    state=RuntimeState.OFF,
                    changed=False,
                    reasons=["already inactive", *cleaned],
                )

            try:
                result = supervisor.stop(force=False)
            except Exception as exc:  # noqa: BLE001
                return ActivationResult(
                    ok=False,
                    state=RuntimeState.ERROR,
                    changed=False,
                    reasons=[f"supervisor.stop() raised: {exc!r}"],
                    error=repr(exc),
                )

            reasons = [str(item) for item in (getattr(result, "reasons", None) or [])]
            if not bool(getattr(result, "ok", False)):
                return ActivationResult(
                    ok=False,
                    state=RuntimeState.ERROR,
                    changed=bool(getattr(result, "changed", False)),
                    reasons=reasons + ["stop failed"],
                    error=str(
                        getattr(result, "error", None) or "could not stop the sidecar"
                    ),
                )

            try:
                after = supervisor.probe()
            except Exception as exc:  # noqa: BLE001
                return ActivationResult(
                    ok=False,
                    state=RuntimeState.ERROR,
                    changed=True,
                    reasons=reasons + [f"stop reported success but the probe failed: {exc!r}"],
                    error=repr(exc),
                )

            after_state = _state_value(getattr(after, "state", _STATUS_ERROR))
            if after_state in (_STATUS_ABSENT, _STATUS_STOPPED, _STATUS_STALE):
                cleaned = self._cleanup_off_files()
                reasons.append(f"sidecar {after_state}")
                reasons.extend(cleaned)
                return ActivationResult(
                    ok=True,
                    state=RuntimeState.OFF,
                    changed=True,
                    reasons=reasons,
                )

            return ActivationResult(
                ok=False,
                state=RuntimeState.ERROR,
                changed=True,
                reasons=reasons
                + [f"sidecar is still {after_state!r} after stop"],
                error=f"could not stop pid {getattr(after, 'pid', None)}",
            )

    def toggle(self) -> Any:
        """Switch the sidecar: on -> off, off / error -> on."""
        with _GLOBAL_LOCK:
            if _state_value(self.status()) == "on":
                return self.deactivate()
            return self.activate()

    def health(self, timeout: float | None = None) -> HealthResult:
        """One bounded ``/health`` check against the running sidecar."""
        try:
            supervisor = self._supervisor(self.policy())
            report = supervisor.probe()
        except Exception as exc:  # noqa: BLE001
            return HealthResult(False, f"cannot probe the sidecar: {exc!r}")
        return self._wait_health(report, timeout)

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"SidecarAdapter(target={self.target_name!r}, "
            f"state_dir={str(self.state_dir)!r})"
        )


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _resolve_launcher(
    settings: SidecarSettings, runtime_home: Path | None
) -> Path | None:
    """Pick the launcher: config, then env, then the runtime tree's interpreter.

    Only the runtime tree is trusted. A launcher from another CPython starts
    without this runtime's ``site-packages``, which silently breaks the
    runtime-first guarantee, so there is no cross-install fallback: if the tree
    has no ``python.exe``, set ``launcher`` in the target or
    ``RT_SIDECAR_PYTHON``. The tree has carried its own interpreter since the
    2026-11-11 repair.
    """
    candidates: list[Path] = []
    if settings.launcher is not None:
        candidates.append(Path(settings.launcher))
    env_launcher = os.environ.get("RT_SIDECAR_PYTHON")
    if env_launcher:
        candidates.append(Path(env_launcher))
    if runtime_home is not None:
        candidates.append(Path(runtime_home) / "python.exe")
        candidates.append(Path(runtime_home) / "pythonw.exe")
    for candidate in candidates:
        if _safe_is_file(candidate):
            return Path(os.path.abspath(os.fspath(candidate)))
    return None


def _resolve_host_script(settings: SidecarSettings) -> Path | None:
    candidates: list[Path] = []
    if settings.host_script is not None:
        candidates.append(Path(settings.host_script))

    # Post-merge package layout: rt_runtime/sidecar_adapter.py -> sidecar/.
    here = Path(__file__).resolve().parent
    candidates.append(here / "sidecar" / "host_stub.py")
    candidates.append(here.parent / "sidecar" / "host_stub.py")

    for work in _work_dir_candidates():
        candidates.append(work / "sidecar" / "host_stub.py")

    for candidate in candidates:
        if _safe_is_file(candidate):
            return Path(os.path.abspath(os.fspath(candidate)))
    return candidates[0] if candidates else None


def _resolve_state_dir(settings: SidecarSettings) -> Path | None:
    """Match ``lifecycle.default_state_dir`` exactly.

    Note: SIDECAR.md §3 spells this ``%LOCALAPPDATA%\\HoudiniRT\\sidecar``; the
    S1 code and IPC.md §9 use ``%LOCALAPPDATA%\\rt-sidecar``. The code wins.
    """
    if settings.state_dir is not None:
        return Path(settings.state_dir)
    explicit = os.environ.get("RT_SIDECAR_STATE_DIR")
    if explicit:
        return Path(os.path.abspath(os.fspath(Path(explicit).expanduser())))
    root = os.environ.get("RT_SIDECAR_DIR")
    if root:
        base = Path(root)
    else:
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) / "rt-sidecar" if local else Path.home() / ".rt-sidecar"
    base = Path(os.path.abspath(os.fspath(base)))
    return base if settings.instance == "default" else base / settings.instance


def _resolve_base_site_packages(
    settings: SidecarSettings, launcher: Path | None
) -> Path | None:
    if settings.base_site_packages is not None:
        return Path(settings.base_site_packages)
    if launcher is None:
        return None
    # Windows layout C:\Python314\Lib\site-packages. The POSIX layout is
    # lib/pythonX.Y/site-packages; both are tried, Windows first.
    windows = Path(launcher).parent / "Lib" / "site-packages"
    if _safe_is_dir(windows):
        return windows
    posix = Path(launcher).parent / "lib" / f"python{settings.expect_python}" / "site-packages"
    if _safe_is_dir(posix):
        return posix
    return windows
