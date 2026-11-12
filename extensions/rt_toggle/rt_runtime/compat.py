"""ABI compatibility guard for activating the HoudiniRT runtime inside Houdini.

Why this exists
---------------
Houdini 22.0.429 ships CPython 3.13.10 (see the runtime smoke evidence, Python
reported as ``3.13.10 ... [MSC v.1942 64 bit (AMD64)]``), while HoudiniRT is
built for CPython 3.14 and ships compiled extensions tagged ``cp314``. Putting
the whole HoudiniRT ``site-packages`` on Houdini's 3.13 interpreter lets
pure-Python imports work but binary extension modules fail to load with the
wrong ABI. This module answers, from filenames only, whether activation is
safe.

Where the ABI-tag rule comes from
---------------------------------
Python extension module filenames encode the interpreter ABI. That convention
is standardised by PEP 3149, "ABI version tagged .so files"
(https://peps.python.org/pep-3149/), and the ``abi3`` tag denotes the stable
ABI from PEP 384 (https://peps.python.org/pep-0384/). Real names look like
``foo.cp314-win_amd64.pyd``, ``foo.cpython-313-x86_64-linux-gnu.so`` and
``foo.abi3.so``. This module parses those names only. It never imports,
executes or loads the modules it inspects.

This is a **local rule** for the rt_toggle runtime guard. It is not a Houdini
API and makes no HOM call. It imports no ``hou`` module, has no third-party
dependencies, and performs no network or subprocess work.

Known limitation
----------------
Filename inspection cannot recover an ``abi3`` extension's minimum CPython
version, so the guard does not silently trust stable-ABI binaries across
minor versions.

Verdict rules (first applicable wins)
-------------------------------------
1. Host interpreter version unknown -> unsafe. Activation is never called safe
   without a verified host version.
2. Target ``site-packages`` missing or not a directory -> unsafe.
3. Any compiled extension whose encoded ABI tag conflicts with the host ->
   unsafe, even when the target's declared version matches the host. The
   declared version is a claim; the filenames are evidence.
4. Host and target share major.minor and no compiled tag conflicts -> safe.
5. Different major.minor and any compiled extension exists -> unsafe, with
   blockers naming examples.
6. Different major.minor and only pure Python -> safe only when
   ``allow_pure_python_only`` is true, with a warning reason.

Call this from inside Houdini (where ``sys.version_info`` is Houdini's Python),
or pass ``houdini_python`` explicitly.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "CompatibilityReport",
    "ExtensionInfo",
    "RuntimeTarget",
    "check_compatibility",
    "interpreter_abi",
    "scan_extensions",
]

#: File suffixes treated as compiled Python extension modules.
_COMPILED_SUFFIXES = frozenset({".pyd", ".so", ".dll"})

#: File suffixes treated as pure-Python modules.
_PURE_SUFFIXES = frozenset({".py"})

#: How many example filenames to include in a blocker or reason.
_MAX_EXAMPLES = 5

# PEP 3149 / wheel-style CPython ABI tag at the start of a filename component,
# e.g. "cp314", "cp39", "cp313t", "cpython-313-x86_64-linux-gnu".
_CP_TAG_RE = re.compile(r"^(?:cpython-|cp)(\d+?)(\d+)([a-z]?)(?=-|$)", re.IGNORECASE)

# Stable-ABI tag, e.g. "abi3-win_amd64" or bare "abi3".
_STABLE_ABI_RE = re.compile(r"^abi3(?=-|$)", re.IGNORECASE)

# Dotted version anywhere in a string, e.g. "3.13.10" or the head of
# sys.version ("3.13.10 (main, ...)").
_DOTTED_VERSION_RE = re.compile(r"(?<![\d.])(\d+)\.(\d+)(?:\.(\d+))?")

# Only CPython 2.x and 3.x are recognised. Anything else (for example a
# Houdini build number such as "22.0.429") is treated as an unknown
# interpreter, never as a matching one.
_KNOWN_MAJORS = frozenset({2, 3})


@dataclass(frozen=True)
class ExtensionInfo:
    """One importable-looking file found while scanning site-packages."""

    path: Path
    filename: str
    abi_tag: str
    compiled: bool

    @property
    def is_stable_abi(self) -> bool:
        """True when the file carries the PEP 384 stable-ABI tag."""
        return self.abi_tag == "abi3"


@dataclass(frozen=True)
class RuntimeTarget:
    """Guard-side description of a switchable runtime.

    Mirrors the fields the adapter exposes: ``name``, ``home``,
    ``python_version``, ``site_packages``, ``entry_point`` and ``env``.
    ``site_packages`` may be one path or a sequence of paths; the guard scans
    every path it is given. Deliberately permissive: no coercion happens here,
    so a target built from JSON, from ``rt_runtime.adapter``, or directly in a
    test all work.
    """

    name: str
    home: Any
    python_version: str
    site_packages: Any
    entry_point: str | None = None
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class CompatibilityReport:
    """Verdict returned by :func:`check_compatibility`.

    ``safe`` is the only activation decision. ``reasons`` explains the verdict
    and may contain warnings even when ``safe`` is true. ``blockers`` is empty
    exactly when activation is allowed; every entry is a reason to refuse.
    ``interpreter_abi`` is the host interpreter's ABI tag (e.g. ``cp313``).
    """

    safe: bool
    reasons: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    interpreter_abi: str = "unknown"
    host_python: str | None = None
    target_python: str | None = None

    def summary(self) -> str:
        """One-line human summary, e.g. ``UNSAFE (host 3.13.10 / target 3.14.2)``."""
        verdict = "SAFE" if self.safe else "UNSAFE"
        host = self.host_python or "unknown"
        target = self.target_python or "unknown"
        return f"{verdict} (host {host} / target {target})"


@dataclass(frozen=True)
class _VersionInfo:
    """Internal, normalised interpreter version."""

    major: int
    minor: int | None = None
    micro: int | None = None

    @property
    def key(self) -> tuple[int, int] | None:
        """Comparable ``(major, minor)`` pair, or None when the minor is unknown."""
        if self.minor is None:
            return None
        return (self.major, self.minor)

    def display(self) -> str:
        if self.minor is None:
            return str(self.major)
        if self.micro is None:
            return f"{self.major}.{self.minor}"
        return f"{self.major}.{self.minor}.{self.micro}"


# ---------------------------------------------------------------------------
# ABI tag parsing
# ---------------------------------------------------------------------------


def _parse_abi_tag(filename: str) -> str:
    """Return the ABI tag encoded in an extension module filename.

    Returns ``cp313``/``cp314``-style tags, ``abi3`` for the stable ABI, ``py3``
    for pure-Python files, and ``none`` when a binary carries no encoded tag
    (for example ``foo.pyd``).
    """
    suffix = os.path.splitext(filename)[1].lower()
    if suffix == ".py":
        return "py3"

    stem = filename[: len(filename) - len(suffix)] if suffix else filename
    # The platform/ABI tag is the final dot-separated component of the stem.
    token = stem.rsplit(".", 1)[-1]

    match = _CP_TAG_RE.match(token)
    if match:
        major, minor, abi_suffix = match.groups()
        return f"cp{major}{minor}{abi_suffix.lower()}"

    if _STABLE_ABI_RE.match(token):
        return "abi3"

    return "none"


# ---------------------------------------------------------------------------
# Version parsing
# ---------------------------------------------------------------------------


def _valid(info: _VersionInfo | None) -> _VersionInfo | None:
    if info is None or info.major not in _KNOWN_MAJORS:
        return None
    return info


def _from_sequence(parts: Sequence[Any]) -> _VersionInfo | None:
    values = list(parts)
    if not values:
        return None
    try:
        major = int(str(values[0]))
        minor = int(str(values[1])) if len(values) > 1 and values[1] is not None else None
        micro = int(str(values[2])) if len(values) > 2 and values[2] is not None else None
    except (TypeError, ValueError):
        return None
    return _valid(_VersionInfo(major, minor, micro))


def _from_interpreter_filename(name: str) -> _VersionInfo | None:
    """Parse ``python3.13.exe``, ``python313.dll``; unversioned names give None."""
    match = re.search(r"python[-_]?(\d)\.(\d+)", name, re.IGNORECASE)
    if match:
        return _valid(_VersionInfo(int(match.group(1)), int(match.group(2))))

    match = re.search(r"python(\d)(\d+)", name, re.IGNORECASE)
    if match:
        return _valid(_VersionInfo(int(match.group(1)), int(match.group(2))))

    match = re.search(r"python(\d)(?:\.exe|\.dll)?$", name, re.IGNORECASE)
    if match:
        return _valid(_VersionInfo(int(match.group(1)), None))

    return None


def _parse_version(value: Any) -> _VersionInfo | None:
    """Normalise a version given as sys.version_info, tuple, string or path.

    Returns None when the value cannot be resolved to a CPython major.minor.
    A path to an executable is only parsed from its filename; an unversioned
    name such as ``python.exe`` is deliberately left unknown.
    """
    if value is None:
        return None
    if isinstance(value, _VersionInfo):
        return value

    major_attr = getattr(value, "major", None)
    if major_attr is not None:
        try:
            major = int(major_attr)
            minor_attr = getattr(value, "minor", None)
            minor = int(minor_attr) if minor_attr is not None else None
            micro_attr = getattr(value, "micro", None)
            micro = int(micro_attr) if micro_attr is not None else None
        except (TypeError, ValueError):
            return None
        return _valid(_VersionInfo(major, minor, micro))

    if isinstance(value, (tuple, list)):
        return _from_sequence(value)

    if isinstance(value, (str, os.PathLike)):
        text = os.fspath(value).strip()
        if not text:
            return None

        looks_like_path = any(sep in text for sep in ("\\", "/")) or text.lower().endswith(
            (".exe", ".dll", ".pyd", ".so")
        )
        if looks_like_path:
            return _from_interpreter_filename(re.split(r"[\\/]", text)[-1])

        if text.isdigit():
            return _valid(_VersionInfo(int(text), None))

        match = _CP_TAG_RE.match(text)
        if match:
            return _valid(_VersionInfo(int(match.group(1)), int(match.group(2))))

        match = _DOTTED_VERSION_RE.search(text)
        if match:
            micro = int(match.group(3)) if match.group(3) else None
            return _valid(_VersionInfo(int(match.group(1)), int(match.group(2)), micro))

    return None


def interpreter_abi(version: str | tuple | None = None) -> str:
    """Return a CPython ABI tag for an interpreter version.

    ``None`` reads :data:`sys.version_info` of the running interpreter. Known
    CPython versions become ``cp313``, ``cp314``, ``cp39`` and so on (minor
    versions below 10 are not zero-padded, matching wheel tags). A value that
    only identifies the major version returns ``py3``; anything unparseable or
    non-CPython returns ``unknown``.
    """
    info = _parse_version(sys.version_info if version is None else version)
    if info is None:
        return "unknown"
    if info.minor is None:
        return f"py{info.major}"
    return f"cp{info.major}{info.minor}"


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def scan_extensions(site_packages: Path) -> list[ExtensionInfo]:
    """Walk ``site_packages`` and classify Python module files.

    Compiled extension modules (``.pyd``, ``.so``, ``.dll``) are returned with
    ``compiled=True`` and their parsed ABI tag. Pure ``.py`` files are returned
    with ``compiled=False`` and the tag ``py3``. ``__pycache__`` directories,
    bytecode (``.pyc``/``.pyo``) and data files are ignored.

    A missing or non-directory path yields an empty list; whether that is safe
    is decided by :func:`check_compatibility`, not here.
    """
    root = Path(site_packages)
    found: list[ExtensionInfo] = []
    if not root.is_dir():
        return found

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=lambda _exc: None):
        # Never descend into bytecode caches.
        dirnames[:] = sorted(name for name in dirnames if name != "__pycache__")

        for filename in sorted(filenames):
            suffix = os.path.splitext(filename)[1].lower()
            if suffix in _COMPILED_SUFFIXES:
                compiled = True
            elif suffix in _PURE_SUFFIXES:
                compiled = False
            else:
                continue

            found.append(
                ExtensionInfo(
                    path=Path(dirpath) / filename,
                    filename=filename,
                    abi_tag=_parse_abi_tag(filename),
                    compiled=compiled,
                )
            )

    found.sort(key=lambda info: str(info.path).lower())
    return found


# ---------------------------------------------------------------------------
# Compatibility check
# ---------------------------------------------------------------------------


def _target_parts(target: Any) -> tuple[Any, Any]:
    """Extract ``(site_packages, python_version)`` from a RuntimeTarget-like value.

    Accepts any object with ``.site_packages`` and ``.python_version``, a
    mapping with those keys, or a bare path to site-packages (whose version is
    then unknown).
    """
    if isinstance(target, Mapping):
        return target.get("site_packages"), target.get("python_version")

    site_packages = getattr(target, "site_packages", None)
    python_version = getattr(target, "python_version", None)
    if site_packages is None and isinstance(target, (str, os.PathLike)):
        return target, python_version
    return site_packages, python_version


def _coerce_site_paths(value: Any) -> list[Path]:
    """Normalize a target's ``site_packages`` to a list of paths.

    The adapter's RuntimeTarget holds one path; the guard also accepts a
    sequence so a config can describe a split site-packages tree.
    """
    if isinstance(value, (str, os.PathLike)):
        return [Path(os.fspath(value))]

    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        paths: list[Path] = []
        for item in value:
            if item is None:
                continue
            try:
                paths.append(Path(os.fspath(item)))
            except TypeError:
                paths.append(Path(str(item)))
        return paths

    try:
        return [Path(os.fspath(value))]
    except TypeError:
        return [Path(str(value))]


def _examples(extensions: Sequence[ExtensionInfo], limit: int = _MAX_EXAMPLES) -> str:
    shown = [f"{info.filename} [{info.abi_tag}]" for info in extensions[:limit]]
    text = ", ".join(shown)
    if len(extensions) > limit:
        text += f", ... ({len(extensions)} total)"
    return text


def _tag_conflicts(info: ExtensionInfo, host_abi: str) -> bool:
    """True when a compiled file definitely cannot load in the host interpreter.

    ``abi3`` is the cross-version stable ABI and ``none`` carries no encoded
    tag, so neither is a definite conflict here; both are handled by the
    cross-version rules instead.
    """
    if not info.compiled:
        return False
    if info.abi_tag in ("abi3", "none"):
        return False
    return info.abi_tag != host_abi


def _check_against(
    site_paths: Sequence[Path],
    host: _VersionInfo,
    target: _VersionInfo,
    host_abi: str,
    target_abi: str,
    allow_pure_python_only: bool,
    report: CompatibilityReport,
) -> CompatibilityReport:
    extensions: list[ExtensionInfo] = []
    for path in site_paths:
        extensions.extend(scan_extensions(path))
    compiled = [info for info in extensions if info.compiled]
    pure = [info for info in extensions if not info.compiled]

    # The declared target version is a claim; compiled filenames are evidence.
    # Refuse a foreign ABI even when the declared versions match: that is the
    # cp314-wheel-on-cp313 case this guard exists to catch.
    incompatible = [info for info in compiled if _tag_conflicts(info, host_abi)]
    if incompatible:
        report.reasons.append(
            f"Host CPython {host.display()} (ABI {host_abi}); target declares "
            f"CPython {target.display()} (ABI {target_abi})."
        )
        report.blockers.append(
            f"{len(incompatible)} compiled extension module(s) cannot be imported by "
            f"CPython {host.display()} (ABI {host_abi}): {_examples(incompatible)}. "
            "The filenames disagree with the declared interpreter version; rebuild the "
            "runtime for the host interpreter."
        )
        return report

    if host.key == target.key:
        report.safe = True
        report.reasons.append(
            f"Host CPython {host.display()} matches the target runtime's declared "
            f"CPython {target.display()} (ABI {host_abi})."
        )
        if extensions:
            report.reasons.append(
                f"Scanned {len(extensions)} module file(s) across {len(site_paths)} "
                f"path(s): {len(compiled)} compiled, {len(pure)} pure-Python."
            )
        else:
            report.reasons.append(
                "Site-packages contains no Python modules; there is nothing to import."
            )
        return report

    mismatch = (
        f"Host CPython {host.display()} (ABI {host_abi}) differs from target "
        f"CPython {target.display()} (ABI {target_abi})."
    )
    report.reasons.append(mismatch)

    if compiled:
        stable_only = all(info.is_stable_abi for info in compiled)
        if stable_only:
            report.blockers.append(
                f"Target declares CPython {target.display()} and contains only stable-ABI "
                f"(abi3) compiled extension(s) ({_examples(compiled)}); their minimum CPython "
                "version is not encoded in the filename, so they are not trusted across "
                f"minor versions. Run on CPython {target.display()} or rebuild for "
                f"{host.display()}."
            )
        else:
            report.blockers.append(
                f"Target declares CPython {target.display()} but the host is CPython "
                f"{host.display()}, and the target contains {len(compiled)} compiled "
                f"extension module(s) ({_examples(compiled)}). Activation is refused until "
                "the runtime matches the host interpreter."
            )
        return report

    if allow_pure_python_only:
        report.safe = True
        report.reasons.append(
            f"Target site-packages contains only pure-Python modules ({len(pure)} file(s), "
            "0 compiled); allow_pure_python_only=True permits activation. Compiled extensions "
            "cannot be added to this tree later without rebuilding for the host interpreter."
        )
    else:
        report.blockers.append(
            "Target site-packages contains only pure-Python modules, but "
            "allow_pure_python_only=False; activation across interpreter versions is refused."
        )
    return report


def check_compatibility(
    target: Any,
    houdini_python: Any = None,
    allow_pure_python_only: bool = False,
) -> CompatibilityReport:
    """Decide whether the HoudiniRT runtime may be activated on the host.

    ``target`` is a RuntimeTarget-like value (``.site_packages`` and
    ``.python_version``), a mapping with those keys, or a bare site-packages
    path. ``site_packages`` may be one path or a sequence of paths.
    ``houdini_python`` is the host interpreter version (string, tuple or
    versioned executable filename); ``None`` uses :data:`sys.version_info`
    of the running interpreter. ``allow_pure_python_only`` controls whether a
    version mismatch is tolerated when nothing compiled would be imported; it
    defaults to False so a mismatch is refused unless the caller opts in.

    Never returns ``safe=True`` when the host or target interpreter version is
    unknown, or when the target path is missing.
    """
    site_packages_value, target_version_value = _target_parts(target)
    report = CompatibilityReport(safe=False)
    site_paths: list[Path] = []

    if site_packages_value is None:
        report.blockers.append(
            "Target has no site_packages path; nothing to inspect. Expected a RuntimeTarget "
            "with .site_packages and .python_version, or a filesystem path."
        )
    else:
        site_paths = _coerce_site_paths(site_packages_value)
        if not site_paths:
            report.blockers.append(
                "Target site_packages is empty; there is nothing to inspect."
            )
        for path in site_paths:
            if not path.is_dir():
                report.blockers.append(
                    f"Target site-packages path does not exist or is not a directory: {path}"
                )

    host = _parse_version(sys.version_info if houdini_python is None else houdini_python)
    if host is None:
        report.blockers.append(
            "Host interpreter version could not be determined from "
            f"{houdini_python!r}; activation safety cannot be verified. Pass an explicit "
            "version string such as '3.13.10', or run the check inside Houdini."
        )
    else:
        report.interpreter_abi = interpreter_abi(host)
        report.host_python = host.display()

    target_version = _parse_version(target_version_value)
    if target_version is None:
        report.blockers.append(
            "Target interpreter version is unknown (python_version missing or unparseable); "
            "ABIs cannot be compared."
        )
    else:
        report.target_python = target_version.display()

    if report.blockers:
        return report

    assert host is not None
    assert target_version is not None

    if host.minor is None or target_version.minor is None:
        report.blockers.append(
            "A major-only interpreter version (py3) is not specific enough to verify ABI "
            "compatibility; supply a full major.minor version."
        )
        return report

    return _check_against(
        site_paths=site_paths,
        host=host,
        target=target_version,
        host_abi=report.interpreter_abi,
        target_abi=interpreter_abi(target_version),
        allow_pure_python_only=allow_pure_python_only,
        report=report,
    )
