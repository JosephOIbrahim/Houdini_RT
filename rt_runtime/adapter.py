"""Pure-Python runtime adapter for the HoudiniRT toggle.

The adapter owns the process-local half of the runtime switch: it appends the
target's ``site_packages`` to ``sys.path``, applies the target's environment
variables, and records exactly what it did in a small JSON state file so a
later ``deactivate()`` can restore the prior state precisely.

Design constraints:

* Standard library only. ``hou`` is never imported, at module level or later.
* ``sys.modules`` is never mutated. Modules imported from the runtime stay
  loaded until the host restarts; ``deactivate()`` reports them instead of
  pretending they were unloaded.
* A path that was already on ``sys.path`` before activation is never recorded
  and therefore never removed.
* All mutations run under one process-wide re-entrant lock, because
  ``sys.path`` and ``os.environ`` are process-global.
* Compatibility is delegated to ``rt_runtime.compat``, imported lazily. If the
  module or its check is missing or raises, activation fails safe: refuse,
  change nothing.

State file shape (written atomically)::

    {
      "active": true,
      "target": "houdini-22",
      "added_paths": ["D:/.../site-packages"],
      "env_backup": {"PYTHONPATH": null},
      "updated_at": "2026-11-11T18:40:00Z"
    }

``env_backup`` maps each variable this adapter set to its prior value, with
``null`` meaning "was not set".
"""

from __future__ import annotations

import inspect
import json
import os
import sys
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

__all__ = [
    "ActivationResult",
    "RuntimeAdapter",
    "RuntimeState",
    "RuntimeTarget",
    "get_adapter",
    "load_targets",
]

_STATE_FILENAME = ".rt_toggle_state.json"

# sys.path and os.environ are process-global, so mutation is serialized across
# every adapter instance in this process.
_GLOBAL_LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _coerce_path(value: Any, base: Path | None = None) -> Path:
    """Return an absolute Path, expanding ``~`` and resolving against *base*."""
    if value is None:
        raise ValueError("path value is required")
    path = Path(str(value)).expanduser()
    if base is not None and not path.is_absolute():
        path = base / path
    return Path(os.path.abspath(os.fspath(path)))


def _norm(path: Any) -> str:
    """Normalize a sys.path entry for comparison (case-insensitive on Windows)."""
    try:
        text = os.fspath(path)
    except TypeError:
        text = str(path)
    return os.path.normcase(os.path.abspath(text))


def _path_index(entries: Any, path: Any) -> int:
    """Index of *path* in a sys.path-like sequence, or -1."""
    wanted = _norm(path)
    for index, entry in enumerate(list(entries)):
        try:
            if _norm(entry) == wanted:
                return index
        except (TypeError, ValueError):
            continue
    return -1


def _remove_path(path: Any) -> bool:
    """Remove the first occurrence of *path* from sys.path. True if removed."""
    index = _path_index(sys.path, path)
    if index < 0:
        return False
    del sys.path[index]
    return True


def _is_dir(path: Any) -> bool:
    try:
        return bool(Path(path).is_dir())
    except (OSError, ValueError):
        return False


def _loaded_modules_under(root: Any) -> list[str]:
    """Names of loaded modules whose file lives under *root* (non-mutating)."""
    prefix = _norm(root).rstrip(os.sep) + os.sep
    found: list[str] = []
    for name, module in list(sys.modules.items()):
        filename = getattr(module, "__file__", None)
        if not filename:
            continue
        try:
            normalized = _norm(filename)
        except (TypeError, ValueError):
            continue
        if normalized == _norm(root) or normalized.startswith(prefix):
            found.append(name)
    return sorted(found)


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


# ---------------------------------------------------------------------------
# Target description + config loading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuntimeTarget:
    """One runtime that can be switched on: an interpreter, its packages, env."""

    name: str
    home: Path
    python_version: str
    site_packages: Path
    entry_point: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    # Opt-in only: a mismatched interpreter is refused unless this is true.
    allow_pure_python_only: bool = False
    # "in_process" (default) keeps today's path; "sidecar" drives S1/S2.
    mode: str = "in_process"
    sidecar: Any = None

    def __post_init__(self) -> None:
        # Coerce plain strings so RuntimeTarget works from JSON and from code.
        object.__setattr__(self, "name", str(self.name))
        object.__setattr__(self, "home", _coerce_path(self.home))
        object.__setattr__(self, "site_packages", _coerce_path(self.site_packages))
        object.__setattr__(self, "python_version", str(self.python_version))
        if self.entry_point is not None:
            object.__setattr__(self, "entry_point", str(self.entry_point))
        object.__setattr__(self, "env", {str(k): str(v) for k, v in dict(self.env or {}).items()})
        object.__setattr__(self, "allow_pure_python_only", bool(self.allow_pure_python_only))
        mode = str(self.mode or "in_process").strip().lower()
        if mode not in ("in_process", "sidecar"):
            raise ValueError(
                f"runtime target {self.name!r}: mode must be 'in_process' or "
                f"'sidecar', got {self.mode!r}"
            )
        object.__setattr__(self, "mode", mode)


_TARGET_FIELDS = {
    "name",
    "home",
    "python_version",
    "site_packages",
    "entry_point",
    "env",
    "allow_pure_python_only",
    "mode",
    "sidecar",
}
_REQUIRED_FIELDS = ("name", "home", "python_version", "site_packages")


def _looks_like_target(mapping: Mapping[str, Any]) -> bool:
    return any(key in mapping for key in ("site_packages", "home", "python_version"))


def _target_entries(data: Any) -> list[Any]:
    """Extract raw target mappings from the tolerated config shapes."""
    if isinstance(data, list):
        return list(data)

    if not isinstance(data, dict):
        raise ValueError("rt_toggle config must be a JSON object or array")

    if "targets" in data:
        inner = data["targets"]
        if isinstance(inner, list):
            return list(inner)
        if isinstance(inner, dict):
            entries = []
            for key, value in inner.items():
                if not isinstance(value, dict):
                    raise ValueError(f"target {key!r} must be a JSON object")
                entry = dict(value)
                entry.setdefault("name", key)
                entries.append(entry)
            return entries
        raise ValueError("'targets' must be a JSON object or array")

    if _looks_like_target(data):
        return [dict(data)]

    # Top-level {name: target} mapping, with optional metadata keys.
    entries = []
    for key, value in data.items():
        if key in ("default", "default_target", "version", "description"):
            continue
        if isinstance(value, dict) and _looks_like_target(value):
            entry = dict(value)
            entry.setdefault("name", key)
            entries.append(entry)
    if entries:
        return entries

    raise ValueError("no runtime targets found in config")


def _target_from_mapping(raw: Any, base: Path) -> RuntimeTarget:
    if not isinstance(raw, dict):
        raise ValueError("each runtime target must be a JSON object")

    # Tolerate {"houdini-22": { ...fields... }} entries inside a list.
    if len(raw) == 1:
        (only_key, only_value), = raw.items()
        if only_key not in _TARGET_FIELDS and isinstance(only_value, dict):
            raw = dict(only_value)
            raw.setdefault("name", only_key)

    missing = [key for key in _REQUIRED_FIELDS if not raw.get(key)]
    if missing:
        label = raw.get("name") or "<unnamed>"
        raise ValueError(
            f"runtime target {label!r} is missing required field(s): "
            + ", ".join(missing)
        )

    env_raw = raw.get("env") or {}
    if not isinstance(env_raw, dict):
        raise ValueError(f"runtime target {raw['name']!r}: 'env' must be a JSON object")

    allow_pure = raw.get("allow_pure_python_only", False)
    if not isinstance(allow_pure, bool):
        raise ValueError(
            f"runtime target {raw['name']!r}: 'allow_pure_python_only' must be true or false"
        )

    from .sidecar_adapter import settings_for_target, target_mode

    mode = target_mode(raw)
    home = _coerce_path(raw["home"], base)
    site_packages = _coerce_path(raw["site_packages"], base)
    sidecar = None
    if mode == "sidecar":
        sidecar = settings_for_target(
            raw,
            base,
            target_home=home,
            target_site_packages=site_packages,
        )

    return RuntimeTarget(
        name=raw["name"],
        home=home,
        python_version=raw["python_version"],
        site_packages=site_packages,
        entry_point=raw.get("entry_point"),
        env=env_raw,
        allow_pure_python_only=allow_pure,
        mode=mode,
        sidecar=sidecar,
    )


def load_targets(config_path: Any) -> dict[str, RuntimeTarget]:
    """Read a rt_toggle JSON config into ``{name: RuntimeTarget}``.

    A missing config file yields an empty mapping; malformed JSON or malformed
    target entries raise ``ValueError``. Relative ``home`` / ``site_packages``
    values resolve against the config file's folder.
    """
    path = Path(config_path).expanduser()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}

    base = Path(os.path.abspath(os.fspath(path))).parent
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in runtime config {path}: {exc}") from exc

    targets: dict[str, RuntimeTarget] = {}
    for raw in _target_entries(data):
        target = _target_from_mapping(raw, base)
        if target.name in targets:
            raise ValueError(f"duplicate runtime target name: {target.name!r}")
        targets[target.name] = target
    return targets


# ---------------------------------------------------------------------------
# Public result types
# ---------------------------------------------------------------------------


class RuntimeState(str, Enum):
    OFF = "off"
    ON = "on"
    ERROR = "error"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value

    # `status()` returns a RuntimeState, but some consumers were written against
    # an ActivationResult-shaped value (`status().state`, `status().ok`). These
    # read-only properties let one enum serve both without a second method.
    @property
    def state(self) -> "RuntimeState":
        return self

    @property
    def ok(self) -> bool:
        return self is not RuntimeState.ERROR

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
class ActivationResult:
    """Outcome of activate / deactivate / toggle."""

    ok: bool = False
    state: RuntimeState = RuntimeState.OFF
    changed: bool = False
    reasons: list[str] = field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# Compatibility bridge (lazy, fail-safe)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CompatView:
    available: bool
    safe: bool
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]
    interpreter_abi: str | None


_COMPAT_IMPORT_ERROR: str | None = None


def _load_compat() -> Any:
    """Import rt_runtime.compat lazily, or return None if unavailable.

    Any import failure (missing file, missing dependency, broken module body)
    degrades to ``None`` so activation refuses instead of taking down the host.
    The failure text is kept for the refusal reason.
    """
    global _COMPAT_IMPORT_ERROR
    try:
        from . import compat  # noqa: WPS433 - deliberately lazy
    except ImportError as exc:
        _COMPAT_IMPORT_ERROR = str(exc) or type(exc).__name__
        return None
    except Exception as exc:  # a broken compat module must not crash the host
        _COMPAT_IMPORT_ERROR = repr(exc)
        return None
    _COMPAT_IMPORT_ERROR = None
    return compat


_COMPAT_FUNCTION_NAMES = ("check_compatibility", "check_target", "check")
_COMPAT_FACTORY_NAMES = ("for_target", "check", "evaluate")
_PRIMITIVE_PARAMS = {"site_packages", "python_version", "entry_point", "env", "home"}


def _call_compat(func: Any, target: RuntimeTarget) -> Any:
    """Invoke a compatibility function with either a target or primitives."""
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return func(target)

    parameters = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    ]
    names = {parameter.name for parameter in parameters}

    first = parameters[0].name if parameters else ""
    positional = first == "target" or (first and first not in _PRIMITIVE_PARAMS)
    if positional:
        if "allow_pure_python_only" in names:
            return func(target, allow_pure_python_only=target.allow_pure_python_only)
        return func(target)

    kwargs: dict[str, Any] = {}
    if "site_packages" in names:
        kwargs["site_packages"] = os.fspath(target.site_packages)
    if "python_version" in names:
        kwargs["python_version"] = target.python_version
    if "entry_point" in names:
        kwargs["entry_point"] = target.entry_point
    if "home" in names:
        kwargs["home"] = os.fspath(target.home)
    if "env" in names:
        kwargs["env"] = dict(target.env)
    if "target" in names:
        kwargs["target"] = target
    if "allow_pure_python_only" in names:
        kwargs["allow_pure_python_only"] = target.allow_pure_python_only

    if not kwargs:
        return func(target)
    return func(**kwargs)


def _field(report: Any, name: str, default: Any) -> Any:
    if isinstance(report, Mapping):
        return report.get(name, default)
    return getattr(report, name, default)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    try:
        return [str(item) for item in value]
    except TypeError:
        return [str(value)]


def _compat_view(module: Any, target: RuntimeTarget) -> _CompatView:
    """Normalize compat's report into a view; never raise."""
    if module is None:
        detail = (
            f" (import failed: {_COMPAT_IMPORT_ERROR})"
            if _COMPAT_IMPORT_ERROR
            else ""
        )
        return _CompatView(
            available=False,
            safe=False,
            reasons=(
                "compatibility module rt_runtime.compat is unavailable" + detail,
            ),
            blockers=(),
            interpreter_abi=None,
        )

    func = None
    for name in _COMPAT_FUNCTION_NAMES:
        candidate = getattr(module, name, None)
        if callable(candidate):
            func = candidate
            break

    if func is None:
        report_class = getattr(module, "CompatibilityReport", None)
        for name in _COMPAT_FACTORY_NAMES:
            candidate = getattr(report_class, name, None)
            if callable(candidate):
                func = candidate
                break

    if func is None:
        return _CompatView(
            available=False,
            safe=False,
            reasons=(
                "compatibility module rt_runtime.compat exposes no "
                "check_compatibility() function",
            ),
            blockers=(),
            interpreter_abi=None,
        )

    try:
        report = _call_compat(func, target)
    except Exception as exc:  # fail safe: a broken check means "not safe"
        return _CompatView(
            available=True,
            safe=False,
            reasons=(f"compatibility check failed: {exc!r}",),
            blockers=(),
            interpreter_abi=None,
        )

    safe = bool(_field(report, "safe", False))
    reasons = _string_list(_field(report, "reasons", []))
    blockers = _string_list(_field(report, "blockers", []))
    abi = _field(report, "interpreter_abi", None)

    if not safe and not reasons and not blockers:
        reasons = ["compatibility check reported unsafe without a reason"]

    return _CompatView(
        available=True,
        safe=safe,
        reasons=tuple(reasons),
        blockers=tuple(blockers),
        interpreter_abi=None if abi is None else str(abi),
    )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


def _empty_state(name: str = "") -> dict[str, Any]:
    return {
        "active": False,
        "target": name,
        "added_paths": [],
        "env_backup": {},
        "updated_at": None,
    }


class RuntimeAdapter:
    """Switch one RuntimeTarget on or off inside the current process."""

    def __init__(self, target: RuntimeTarget, state_path: Any | None = None) -> None:
        self.target = target
        self.state_path = (
            None if state_path is None else Path(state_path).expanduser().resolve()
        )
        self._memory_state: dict[str, Any] = _empty_state(target.name)
        self._sidecar = None
        if target.mode == "sidecar":
            if target.sidecar is None:
                raise ValueError(
                    f"runtime target {target.name!r}: mode 'sidecar' needs a sidecar section"
                )
            from .sidecar_adapter import SidecarAdapter

            self._sidecar = SidecarAdapter(
                target.name,
                target.sidecar,
                target_home=target.home,
                target_site_packages=target.site_packages,
            )

    # -- turns --------------------------------------------------------------

    def turn_control(self) -> Any | None:
        """Return the sidecar turn client, or ``None`` for in-process targets.

        Surfaces call this after activation; ``None`` means there is nothing to
        submit to yet, not that anything failed.
        """
        if self._sidecar is None:
            return None
        return self._sidecar.turn_control()

    def status_detail(self, *, check_health: bool = False) -> dict[str, Any]:
        """Return operator-facing detail, including optional sidecar health.

        Sidecar targets get the merged supervisor/status document. In-process
        targets get the same outer state vocabulary without pretending to have
        a sidecar health endpoint.
        """
        if self._sidecar is not None:
            return self._sidecar.status_detail(check_health=check_health)
        state = self.status()
        return {
            "target": self.target.name,
            "runtime_state": getattr(state, "value", str(state)),
            "probe": self.probe(),
        }

    # -- state file ---------------------------------------------------------

    def _read_state(self) -> dict[str, Any]:
        if self.state_path is None:
            return _copy_state(self._memory_state)

        try:
            text = self.state_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return _empty_state(self.target.name)
        except OSError:
            return _empty_state(self.target.name)

        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return _empty_state(self.target.name)
        if not isinstance(data, dict):
            return _empty_state(self.target.name)

        env_backup_raw = data.get("env_backup")
        env_backup: dict[str, str | None] = {}
        if isinstance(env_backup_raw, dict):
            for key, value in env_backup_raw.items():
                env_backup[str(key)] = None if value is None else str(value)

        added_raw = data.get("added_paths")
        added_paths = []
        if isinstance(added_raw, list):
            added_paths = [str(item) for item in added_raw]

        return {
            "active": bool(data.get("active", False)),
            "target": str(data.get("target") or ""),
            "added_paths": added_paths,
            "env_backup": env_backup,
            "updated_at": data.get("updated_at"),
        }

    def _write_state(self, state: Mapping[str, Any]) -> dict[str, Any]:
        record = {
            "active": bool(state.get("active", False)),
            "target": str(state.get("target") or self.target.name),
            "added_paths": [str(item) for item in state.get("added_paths") or []],
            "env_backup": {
                str(key): (None if value is None else str(value))
                for key, value in dict(state.get("env_backup") or {}).items()
            },
            "updated_at": _now_iso(),
        }

        if self.state_path is None:
            self._memory_state = record
            return _copy_state(record)

        parent = self.state_path.parent
        parent.mkdir(parents=True, exist_ok=True)
        temp_path = parent / (
            f".{self.state_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        payload = json.dumps(record, indent=2, sort_keys=True) + "\n"
        try:
            with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
            os.replace(temp_path, self.state_path)
        except BaseException:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass
            raise
        return _copy_state(record)

    # -- verification -------------------------------------------------------

    def _runtime_paths_present(self, state: Mapping[str, Any]) -> bool:
        """True when every path this activation needs is on sys.path."""
        if _path_index(sys.path, self.target.site_packages) < 0:
            return False
        for path in state.get("added_paths") or []:
            if _path_index(sys.path, path) < 0:
                return False
        return True

    # -- public API ---------------------------------------------------------

    def status(self) -> RuntimeState:
        """Current state: OFF, ON, or ERROR (state file says on but sys.path disagrees)."""
        if self.target.mode == "sidecar":
            return self._sidecar.status()
        state = self._read_state()
        if not state.get("active"):
            return RuntimeState.OFF

        recorded_target = str(state.get("target") or "")
        if recorded_target and recorded_target != self.target.name:
            # Another target is the active one in this process.
            return RuntimeState.OFF

        return (
            RuntimeState.ON
            if self._runtime_paths_present(state)
            else RuntimeState.ERROR
        )

    def check_compatibility(self) -> dict[str, Any]:
        """Compatibility report as a plain dict; never raises."""
        view = _compat_view(_load_compat(), self.target)
        return {
            "safe": view.safe,
            "reasons": list(view.reasons),
            "blockers": list(view.blockers),
            "interpreter_abi": view.interpreter_abi,
        }

    def probe(self) -> dict[str, Any]:
        """Read-only diagnostics. Never mutates sys.path, env, or the state file."""
        if self.target.mode == "sidecar":
            return self._sidecar.probe()
        try:
            compatibility = self.check_compatibility()
            home_exists = _is_dir(self.target.home)
            site_packages_exists = _is_dir(self.target.site_packages)
            active = self.status() == RuntimeState.ON
        except Exception as exc:  # probe should never take the host down
            return {
                "available": False,
                "python_version": self.target.python_version,
                "site_packages_exists": False,
                "compatibility": {
                    "safe": False,
                    "reasons": [f"probe failed: {exc!r}"],
                    "blockers": [],
                },
                "active": False,
            }

        return {
            "available": bool(home_exists and site_packages_exists),
            "python_version": self.target.python_version,
            "site_packages_exists": site_packages_exists,
            "compatibility": {
                "safe": compatibility["safe"],
                "reasons": compatibility["reasons"],
                "blockers": compatibility["blockers"],
            },
            "active": active,
        }

    def activate(self) -> ActivationResult:
        """Turn the runtime on. Idempotent; refuses when unsafe or unavailable."""
        if self.target.mode == "sidecar":
            return self._sidecar.activate()
        with _GLOBAL_LOCK:
            state = self._read_state()
            recorded_target = str(state.get("target") or "")
            stale_reason: str | None = None

            if state.get("active") and recorded_target and recorded_target != self.target.name:
                recorded_paths = list(state.get("added_paths") or [])
                # A different target is recorded as active. It is only a live
                # conflict if the paths it added are still on this process's
                # sys.path; a state file left over from a previous host session
                # is stale, because sys.path did not survive the restart.
                still_live = not recorded_paths or all(
                    _path_index(sys.path, path) >= 0 for path in recorded_paths
                )
                if still_live:
                    return ActivationResult(
                        ok=False,
                        state=RuntimeState.OFF,
                        changed=False,
                        reasons=[
                            f"another runtime target {recorded_target!r} is active; "
                            "deactivate it first"
                        ],
                    )
                stale_reason = (
                    f"replacing stale state from target {recorded_target!r} "
                    "(its recorded paths are not on sys.path)"
                )

            if state.get("active") and self._runtime_paths_present(state):
                return ActivationResult(
                    ok=True,
                    state=RuntimeState.ON,
                    changed=False,
                    reasons=["already active"],
                )

            site_packages = os.fspath(self.target.site_packages)
            if not _is_dir(self.target.site_packages):
                message = f"site-packages path does not exist: {site_packages}"
                return ActivationResult(
                    ok=False,
                    state=RuntimeState.ERROR,
                    changed=False,
                    reasons=[message],
                    error=message,
                )

            view = _compat_view(_load_compat(), self.target)
            if not view.safe:
                reasons = list(view.reasons) + list(view.blockers)
                if not reasons:
                    reasons = ["compatibility check refused activation"]
                return ActivationResult(
                    ok=False,
                    state=RuntimeState.OFF,
                    changed=False,
                    reasons=reasons,
                )

            already_present = _path_index(sys.path, self.target.site_packages) >= 0
            added_paths: list[str] = []
            env_backup: dict[str, str | None] = {}
            reasons: list[str] = []

            try:
                if not already_present:
                    sys.path.append(site_packages)
                    added_paths.append(site_packages)

                for key, value in dict(self.target.env).items():
                    key = str(key)
                    value = str(value)
                    env_backup[key] = os.environ.get(key)
                    os.environ[key] = value

                self._write_state(
                    {
                        "active": True,
                        "target": self.target.name,
                        "added_paths": added_paths,
                        "env_backup": env_backup,
                    }
                )
            except Exception as exc:
                # Roll the process back to what it looked like before this call.
                for path in added_paths:
                    _remove_path(path)
                for key, previous in env_backup.items():
                    if previous is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = previous
                return ActivationResult(
                    ok=False,
                    state=RuntimeState.ERROR,
                    changed=False,
                    reasons=["failed to apply runtime state"],
                    error=repr(exc),
                )

            if stale_reason:
                reasons.append(stale_reason)
            if added_paths:
                reasons.append(f"added to sys.path: {site_packages}")
            else:
                reasons.append(
                    f"site-packages was already on sys.path: {site_packages}"
                )
            if env_backup:
                reasons.append(
                    "applied environment variables: "
                    + ", ".join(sorted(env_backup))
                )
            if state.get("active"):
                reasons.append("repaired state (recorded paths were missing)")

            return ActivationResult(
                ok=True,
                state=RuntimeState.ON,
                changed=True,
                reasons=reasons,
            )

    def deactivate(self) -> ActivationResult:
        """Turn the runtime off. Idempotent; removes only paths this adapter added."""
        if self.target.mode == "sidecar":
            return self._sidecar.deactivate()
        with _GLOBAL_LOCK:
            state = self._read_state()
            was_active = bool(state.get("active"))
            added_paths = list(state.get("added_paths") or [])
            env_backup = dict(state.get("env_backup") or {})

            if not was_active and not added_paths and not env_backup:
                return ActivationResult(
                    ok=True,
                    state=RuntimeState.OFF,
                    changed=False,
                    reasons=["already inactive"],
                )

            reasons: list[str] = []
            recorded_target = str(state.get("target") or "")
            if recorded_target and recorded_target != self.target.name:
                reasons.append(
                    f"state file belonged to target {recorded_target!r}; "
                    "cleaning up the recorded activation"
                )

            removed: list[str] = []
            for path in added_paths:
                if _remove_path(path):
                    removed.append(path)

            restored: list[str] = []
            for key, previous in env_backup.items():
                if previous is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = previous
                restored.append(key)

            residual = _loaded_modules_under(self.target.site_packages)

            try:
                self._write_state(
                    {
                        "active": False,
                        "target": self.target.name,
                        "added_paths": [],
                        "env_backup": {},
                    }
                )
            except Exception as exc:
                return ActivationResult(
                    ok=False,
                    state=RuntimeState.ERROR,
                    changed=True,
                    reasons=["runtime paths were removed but state could not be written"],
                    error=repr(exc),
                )

            if removed:
                reasons.append(
                    "removed from sys.path: " + ", ".join(removed)
                )
            if restored:
                reasons.append(
                    "restored environment variables: " + ", ".join(sorted(restored))
                )
            if residual:
                reasons.append(
                    f"{len(residual)} module(s) from the runtime remain loaded "
                    "(sys.modules is never mutated); restart the host to unload them"
                )
            reasons.append("inactive")

            return ActivationResult(
                ok=True,
                state=RuntimeState.OFF,
                changed=True,
                reasons=reasons,
            )

    def toggle(self) -> ActivationResult:
        """Switch the runtime: on -> off, off / error -> on."""
        if self.target.mode == "sidecar":
            return self._sidecar.toggle()
        with _GLOBAL_LOCK:
            if self.status() == RuntimeState.ON:
                return self.deactivate()
            return self.activate()


def _copy_state(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "active": bool(state.get("active", False)),
        "target": str(state.get("target") or ""),
        "added_paths": [str(item) for item in state.get("added_paths") or []],
        "env_backup": {
            str(key): (None if value is None else str(value))
            for key, value in dict(state.get("env_backup") or {}).items()
        },
        "updated_at": state.get("updated_at"),
    }


def get_adapter(config_path: Any | None = None) -> RuntimeAdapter:
    """Convenience re-export of :func:`rt_runtime.session.get_adapter`.

    Imported lazily because ``session`` imports this module at load time.
    """
    from .session import get_adapter as _get_adapter

    return _get_adapter(config_path)
