"""Lazy exports for the KarmaRT Houdini 22 PySide6 UI and artist entrypoints."""

from __future__ import annotations

import importlib
from typing import Any

_LAZY_SUBMODULES: tuple[str, ...] = (
    "panel",
    "radial_menu",
    "shelf_actions",
)

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "KarmaRTPanelWidget": ("panel", "KarmaRTPanelWidget"),
    "create_panel": ("panel", "create_panel"),
}

__all__ = [
    "KarmaRTPanelWidget",
    "create_panel",
    *_LAZY_SUBMODULES,
]


def __getattr__(name: str) -> Any:
    if name in _LAZY_SUBMODULES:
        return importlib.import_module(f"{__name__}.{name}")
    target = _LAZY_EXPORTS.get(name)
    if target is not None:
        submod_name, attr_name = target
        module = importlib.import_module(f"{__name__}.{submod_name}")
        return getattr(module, attr_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
