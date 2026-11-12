"""Minimal, pure-Python stand-in for Houdini's ``hou`` module.

Purpose
-------
Let the RT toggle UI modules (Python panel, shelf tool, node recipe) import and
run under plain CPython in pytest, with no Houdini installation and no Qt.

This is a test double, not a Houdini emulator. It models only the small surface
the UI tests need:

* ``hou.ToggleParmTemplate`` (plus a few sibling parm template classes)
* ``hou.ParmTemplateGroup``
* ``hou.Parm`` / ``hou.Node`` including a fireable parm callback
* ``hou.ui.displayMessage`` -- records the message, returns 0
* ``hou.pypanel.installFile`` -- records the path, returns True

Nothing in production code imports this module. Install it with::

    import fake_hou
    fake_hou.install()      # sys.modules["hou"] = fake_hou
    ...
    fake_hou.uninstall()

Parm callbacks
--------------
Real Houdini registers a callback script on a parameter. The fake stores a
Python callable via ``Parm.setCallback(fn)`` and fires it through
``Parm.runCallback(**kwargs)`` or ``Node.fireParmCallback(name, **kwargs)``.
Those callback helpers describe the contract the UI tests check; do not copy
their names into production code as if they were real HOM API. Production code
must use whatever callback mechanism the installed Houdini version documents.

The fake accepts extra keyword arguments on templates and UI calls so tests do
not break when an implementation passes legitimate Houdini options the fake
does not care about.
"""

from __future__ import annotations

import sys
from typing import Any, Callable

__all__ = [
    "ParmTemplate",
    "ToggleParmTemplate",
    "IntParmTemplate",
    "FloatParmTemplate",
    "StringParmTemplate",
    "ButtonParmTemplate",
    "ParmTemplateGroup",
    "Parm",
    "Node",
    "ui",
    "pypanel",
    "scriptLanguage",
    "severityType",
    "install",
    "uninstall",
    "is_installed",
]


class ParmTemplate:
    """Base parm template with the accessors UI code normally uses."""

    def __init__(
        self,
        name: str,
        label: str | None = None,
        default_value: Any = None,
        help: str = "",
        **kwargs: Any,
    ) -> None:
        self._name = str(name)
        self._label = str(label if label is not None else name)
        self._default = default_value
        self._help = str(help)
        self._callback: Callable[..., Any] | None = None
        self._extra = dict(kwargs)

    # -- identity ---------------------------------------------------------
    def name(self) -> str:
        return self._name

    def label(self) -> str:
        return self._label

    def setLabel(self, label: str) -> None:
        self._label = str(label)

    def help(self) -> str:
        return self._help

    def setHelp(self, text: str) -> None:
        self._help = str(text)

    # -- value ------------------------------------------------------------
    def defaultValue(self) -> Any:
        return self._default

    def setDefaultValue(self, value: Any) -> None:
        self._default = value

    # -- harness-only callback plumbing ----------------------------------
    def setCallback(self, callback: Callable[..., Any] | None) -> None:
        self._callback = callback

    def callback(self) -> Callable[..., Any] | None:
        return self._callback

    def type(self) -> str:
        return self.__class__.__name__

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.__class__.__name__} {self._name!r} label={self._label!r}>"


class ToggleParmTemplate(ParmTemplate):
    def __init__(
        self,
        name: str,
        label: str | None = None,
        default_value: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(name, label, bool(default_value), **kwargs)


class IntParmTemplate(ParmTemplate):
    def __init__(
        self,
        name: str,
        label: str | None = None,
        default_value: int = 0,
        **kwargs: Any,
    ) -> None:
        super().__init__(name, label, int(default_value), **kwargs)


class FloatParmTemplate(ParmTemplate):
    def __init__(
        self,
        name: str,
        label: str | None = None,
        default_value: float = 0.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(name, label, float(default_value), **kwargs)


class StringParmTemplate(ParmTemplate):
    def __init__(
        self,
        name: str,
        label: str | None = None,
        default_value: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(name, label, str(default_value), **kwargs)


class ButtonParmTemplate(ParmTemplate):
    def __init__(
        self,
        name: str,
        label: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(name, label, None, **kwargs)


class ParmTemplateGroup:
    """Ordered collection of parm templates."""

    def __init__(self, templates: list[ParmTemplate] | None = None) -> None:
        self._templates: list[ParmTemplate] = list(templates or [])

    def addParmTemplate(self, template: ParmTemplate) -> None:
        self._templates.append(template)

    def append(self, template: ParmTemplate) -> None:
        """Alias Houdini exposes alongside addParmTemplate."""
        self._templates.append(template)

    def replace(self, name: str, template: ParmTemplate) -> bool:
        for index, existing in enumerate(self._templates):
            if existing.name() == name:
                self._templates[index] = template
                return True
        return False

    def remove(self, name: str) -> bool:
        for index, existing in enumerate(self._templates):
            if existing.name() == name:
                del self._templates[index]
                return True
        return False

    def templates(self) -> list[ParmTemplate]:
        return list(self._templates)

    def find(self, name: str) -> ParmTemplate | None:
        for template in self._templates:
            if template.name() == name:
                return template
        return None

    def __len__(self) -> int:
        return len(self._templates)

    def __iter__(self):
        return iter(self._templates)


class Parm:
    """A parameter instance attached to a fake :class:`Node`."""

    def __init__(self, node: "Node", template: ParmTemplate, value: Any = None) -> None:
        self._node = node
        self._template = template
        self._value = template.defaultValue() if value is None else value
        self._callback: Callable[..., Any] | None = template.callback()
        self._locked = False

    def name(self) -> str:
        return self._template.name()

    def node(self) -> "Node":
        return self._node

    def parmTemplate(self) -> ParmTemplate:
        return self._template

    def eval(self) -> Any:
        return self._value

    def evalAsBool(self) -> bool:
        return bool(self._value)

    def set(self, value: Any) -> None:
        self._value = value

    def lock(self, locked: bool) -> None:
        self._locked = bool(locked)

    def isLocked(self) -> bool:
        return self._locked

    # -- harness-only callback plumbing ----------------------------------
    def setCallback(self, callback: Callable[..., Any] | None) -> "Parm":
        self._callback = callback
        return self

    def callback(self) -> Callable[..., Any] | None:
        return self._callback

    def runCallback(self, **kwargs: Any) -> Any:
        if self._callback is None:
            return None
        kwargs.setdefault("parm", self)
        kwargs.setdefault("node", self._node)
        return self._callback(**kwargs)


class Node:
    """A minimal node that owns parms and a parm template group."""

    def __init__(self, name: str = "fake_node") -> None:
        self._name = str(name)
        self._group = ParmTemplateGroup()
        self._parms: dict[str, Parm] = {}

    def name(self) -> str:
        return self._name

    def path(self) -> str:
        return f"/obj/{self._name}"

    def parmTemplateGroup(self) -> ParmTemplateGroup:
        return self._group

    def setParmTemplateGroup(self, group: ParmTemplateGroup) -> None:
        self._group = group

    def parm(self, name: str) -> Parm | None:
        return self._parms.get(name)

    def parms(self) -> tuple[Parm, ...]:
        return tuple(self._parms.values())

    def addParm(self, template: ParmTemplate, value: Any = None) -> Parm:
        """Harness helper: create a Parm from a template in one call."""
        parm = Parm(self, template, value)
        self._parms[template.name()] = parm
        self._group.addParmTemplate(template)
        return parm

    def fireParmCallback(self, name: str, **kwargs: Any) -> Any:
        """Harness helper: fire the callback registered on a named parm."""
        parm = self._parms.get(name)
        if parm is None:
            raise KeyError(f"no parm named {name!r} on {self._name!r}")
        return parm.runCallback(**kwargs)


class _UI:
    """Stands in for ``hou.ui``; records messages instead of showing them."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.calls: list[dict[str, Any]] = []

    def displayMessage(self, text: str = "", *args: Any, **kwargs: Any) -> int:
        self.messages.append(str(text))
        self.calls.append({"text": str(text), "args": args, "kwargs": dict(kwargs)})
        return 0

    def setStatusMessage(self, text: str = "", *args: Any, **kwargs: Any) -> None:
        self.messages.append(str(text))
        self.calls.append({"text": str(text), "args": args, "kwargs": dict(kwargs)})

    def clear(self) -> None:
        self.messages.clear()
        self.calls.clear()


class _Pypanel:
    """Stands in for ``hou.pypanel``; records installed panel files."""

    def __init__(self) -> None:
        self.installed: list[str] = []

    def installFile(self, file_path: str, *args: Any, **kwargs: Any) -> bool:
        self.installed.append(str(file_path))
        return True

    def installedFiles(self) -> list[str]:
        return list(self.installed)

    def clear(self) -> None:
        self.installed.clear()


ui = _UI()
pypanel = _Pypanel()


class _ScriptLanguage:
    """Stands in for ``hou.scriptLanguage`` enum values."""

    Python = "python"
    Hscript = "hscript"


class _SeverityType:
    """Stands in for ``hou.severityType`` enum values."""

    Message = "message"
    Warning = "warning"
    Error = "error"
    Fatal = "fatal"


scriptLanguage = _ScriptLanguage()
severityType = _SeverityType()

_SAVED: dict[str, Any] = {}


def install() -> Any:
    """Register this module as ``hou`` in ``sys.modules`` and return it."""
    module = sys.modules[__name__]
    existing = sys.modules.get("hou")
    if existing is not None and existing is not module:
        _SAVED["hou"] = existing
    sys.modules["hou"] = module
    return module


def uninstall() -> None:
    """Remove the fake from ``sys.modules`` and restore any previous ``hou``."""
    module = sys.modules[__name__]
    if sys.modules.get("hou") is module:
        sys.modules.pop("hou", None)
    if "hou" in _SAVED:
        sys.modules["hou"] = _SAVED.pop("hou")


def is_installed() -> bool:
    return sys.modules.get("hou") is sys.modules.get(__name__)
