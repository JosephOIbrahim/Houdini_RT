"""Process-wide access point for the HoudiniRT runtime adapter.

``get_adapter()`` returns one cached ``RuntimeAdapter`` per (config, target)
pair, so a Python Panel, a shelf tool, and a node toggle can all share the same
object and therefore the same lock, sys.path bookkeeping, and state file.

Defaults, as required by the scaffold contract:

* config: ``<scaffold root>/rt_toggle.json``
* state:  ``<scaffold root>/.rt_toggle_state.json``

``<scaffold root>`` is the ``rt_toggle`` extension folder that contains the
``rt_runtime`` package. When an explicit ``config_path`` is supplied, the state
file lives beside that config instead, because a config shipped elsewhere owns
its own runtime state.

Target selection when ``target_name`` is omitted:

1. the target recorded as active in the state file (so a toggle can turn off
   what a previous session left on),
2. a ``"default"`` / ``"default_target"`` key in the config,
3. the only configured target,
4. the first target in sorted order.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from .adapter import RuntimeAdapter, RuntimeTarget, load_targets

__all__ = [
    "default_config_path",
    "default_state_path",
    "get_adapter",
    "reset_cache",
]

_SCAFFOLD_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_PATH = _SCAFFOLD_ROOT / "rt_toggle.json"
_STATE_FILENAME = ".rt_toggle_state.json"

_CACHE: dict[tuple[str, str | None, str], RuntimeAdapter] = {}
_CACHE_LOCK = threading.RLock()


def default_config_path() -> Path:
    """The config used when ``get_adapter()`` is called with no arguments."""
    return _DEFAULT_CONFIG_PATH


def default_state_path() -> Path:
    """The state file used with the default config."""
    return _DEFAULT_CONFIG_PATH.parent / _STATE_FILENAME


def _absolute(value: Any) -> Path:
    return Path(os.path.abspath(os.fspath(Path(str(value)).expanduser())))


def _state_target(state_path: Path) -> str | None:
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("active"):
        return None
    target = data.get("target")
    return str(target) if target else None


def _config_default(config_path: Path) -> str | None:
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("default", "default_target"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _select_target(
    targets: dict[str, RuntimeTarget],
    config_path: Path,
    state_path: Path,
    target_name: str | None,
) -> RuntimeTarget:
    if target_name is not None:
        if target_name not in targets:
            raise LookupError(
                f"runtime target {target_name!r} is not defined in {config_path}"
            )
        return targets[target_name]

    active = _state_target(state_path)
    if active and active in targets:
        return targets[active]

    configured_default = _config_default(config_path)
    if configured_default and configured_default in targets:
        return targets[configured_default]

    if len(targets) == 1:
        return next(iter(targets.values()))

    return targets[sorted(targets)[0]]


def get_adapter(
    config_path: Any | None = None,
    target_name: str | None = None,
    state_path: Any | None = None,
) -> RuntimeAdapter:
    """Return the shared RuntimeAdapter for a config and target.

    ``target_name`` and ``state_path`` are optional extensions to the fixed
    contract; both default to automatic selection as described in the module
    docstring. Raises ``LookupError`` when the config has no usable target.
    """
    config = (
        _DEFAULT_CONFIG_PATH if config_path is None else _absolute(config_path)
    )
    state = (
        _absolute(state_path)
        if state_path is not None
        else config.parent / _STATE_FILENAME
    )

    key = (os.fspath(config), target_name, os.fspath(state))
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            return cached

        targets = load_targets(config)
        if not targets:
            raise LookupError(f"no runtime targets configured in {config}")

        target = _select_target(targets, config, state, target_name)
        adapter = RuntimeAdapter(target, state_path=state)
        _CACHE[key] = adapter
        return adapter


def reset_cache() -> None:
    """Drop the singleton cache. Intended for tests and config reloads."""
    with _CACHE_LOCK:
        _CACHE.clear()
