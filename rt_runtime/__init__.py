"""HoudiniRT runtime toggle: a pure-Python on/off switch for the runtime.

Public surface::

    from rt_runtime import (
        RuntimeTarget, RuntimeAdapter, RuntimeState, ActivationResult,
        load_targets, get_adapter,
    )

Nothing in this package imports ``hou``, so it is safe to import from a plain
Python process, a test, or a Houdini Python Panel alike.
"""

from __future__ import annotations

from .adapter import (
    ActivationResult,
    RuntimeAdapter,
    RuntimeState,
    RuntimeTarget,
    load_targets,
)
from .session import get_adapter

__all__ = [
    "ActivationResult",
    "RuntimeAdapter",
    "RuntimeState",
    "RuntimeTarget",
    "get_adapter",
    "load_targets",
]

__version__ = "0.1.1"
