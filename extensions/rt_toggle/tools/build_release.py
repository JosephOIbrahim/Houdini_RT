#!/usr/bin/env python3
"""Build the houdini-rt-toggle release zip.

Usage (from anywhere):

    py -3.12 tools/build_release.py           # write dist/houdini-rt-toggle-<version>.zip
    py -3.12 tools/build_release.py --check   # validate the tree, write nothing

The script resolves the project root as its own parent directory, so it works
from any current working directory. Standard library only; no third-party
imports, no shell calls.

The archive is deterministic: members are sorted, every entry uses a fixed
timestamp (1980-01-01 00:00:00) and fixed permissions, and deflate level is
pinned. Rebuilding the same tree produces the same bytes and the same SHA-256.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "dist"

# Top-level files that always ship when present.
INCLUDE_FILES = (
    "README.md",
    "DESIGN.md",
    "INSTALL.md",
    "CHANGELOG.md",
    "VERSION",
    "rt_toggle.json",
    "pyproject.toml",
    "LICENSE",
)

# Additional top-level files matched by glob (release notes are optional).
INCLUDE_GLOBS = ("RELEASE_NOTES_*.md",)

# Directories copied recursively when present.
INCLUDE_DIRS = (
    "rt_runtime",
    "houdini",
    "tests",
    "verification",
    "docs",
    "tools",
)

# Never shipped, at any depth.
EXCLUDED_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".benchmarks",
    ".git",
    ".venv",
    "dist",
}
EXCLUDED_FILE_NAMES = {
    ".rt_toggle_state.json",
    ".DS_Store",
    "Thumbs.db",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}

# Hard requirements for --check. These are structural: without them the
# archive is not a usable release, even if it would still build.
REQUIRED_FILES = (
    "VERSION",
    "LICENSE",
    "rt_toggle.json",
    "rt_runtime/__init__.py",
    "rt_runtime/render_session.py",
    "houdini/rt_panel.pypanel",
    "houdini/rt_shelf.shelf",
    "houdini/rt_node.py",
    "tests/conftest.py",
)

# Documents expected in a release, but authored elsewhere and possibly not
# landed yet. --check warns about these instead of failing.
EXPECTED_DOCS = (
    "README.md",
    "DESIGN.md",
    "INSTALL.md",
    "CHANGELOG.md",
)

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+.][0-9A-Za-z.-]+)?$")

# (year, month, day, hour, minute, second); ZIP cannot store dates before 1980.
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)
FILE_MODE = 0o644


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def read_version() -> str:
    version_file = ROOT / "VERSION"
    if not version_file.is_file():
        fail(f"VERSION file not found at {version_file}")
    raw = version_file.read_text(encoding="utf-8")
    version = raw.strip()
    if not version:
        fail("VERSION file is empty")
    if raw.count("\n") > 1 or any(ch.isspace() for ch in version):
        fail(f"VERSION must contain a single version token, got {version!r}")
    if not VERSION_RE.match(version):
        fail(f"VERSION {version!r} is not a valid version (expected e.g. 0.1.0)")
    return version


def is_excluded(rel: PurePosixPath) -> bool:
    """True when a project-relative path must stay out of the archive."""
    for part in rel.parts:
        if part in EXCLUDED_DIR_NAMES:
            return True
    name = rel.parts[-1]
    if name in EXCLUDED_FILE_NAMES:
        return True
    if rel.suffix.lower() in EXCLUDED_SUFFIXES:
        return True
    return False


def collect_entries() -> list[tuple[PurePosixPath, Path]]:
    """Return (project-relative posix path, absolute source path), sorted."""
    candidates: set[Path] = set()

    for name in INCLUDE_FILES:
        path = ROOT / name
        if path.is_file():
            candidates.add(path)

    for pattern in INCLUDE_GLOBS:
        for path in ROOT.glob(pattern):
            if path.is_file():
                candidates.add(path)

    for dirname in INCLUDE_DIRS:
        base = ROOT / dirname
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file():
                candidates.add(path)

    entries: list[tuple[PurePosixPath, Path]] = []
    for path in candidates:
        rel = PurePosixPath(path.relative_to(ROOT).as_posix())
        if is_excluded(rel):
            continue
        entries.append((rel, path))

    entries.sort(key=lambda item: item[0].as_posix())
    return entries


def arcname(version: str, rel: PurePosixPath) -> str:
    return f"houdini-rt-toggle-{version}/{rel.as_posix()}"


def print_plan(version: str, entries: list[tuple[PurePosixPath, Path]]) -> None:
    print(f"Project root : {ROOT}")
    print(f"Version      : {version}")
    print(f"Archive      : dist/houdini-rt-toggle-{version}.zip")
    print(f"Members      : {len(entries)}")
    for rel, _src in entries:
        print(f"  {arcname(version, rel)}")


def check(version: str, entries: list[tuple[PurePosixPath, Path]]) -> None:
    problems: list[str] = []
    warnings: list[str] = []

    for required in REQUIRED_FILES:
        if not (ROOT / required).is_file():
            problems.append(f"required file missing: {required}")

    for doc in EXPECTED_DOCS:
        if not (ROOT / doc).is_file():
            warnings.append(f"expected document not present yet: {doc}")

    for dirname in ("rt_runtime", "houdini", "tests", "verification", "docs", "tools"):
        if not (ROOT / dirname).is_dir():
            problems.append(f"required directory missing: {dirname}/")

    seen: set[str] = set()
    for rel, src in entries:
        name = arcname(version, rel)
        if name in seen:
            problems.append(f"duplicate member: {name}")
        seen.add(name)

        if rel.is_absolute() or ".." in rel.parts:
            problems.append(f"unsafe member path: {name}")

        if any(part in EXCLUDED_DIR_NAMES for part in rel.parts):
            problems.append(f"cache/ignored directory in release set: {name}")
        if rel.suffix.lower() in EXCLUDED_SUFFIXES:
            problems.append(f"bytecode file in release set: {name}")
        if rel.parts[-1] in EXCLUDED_FILE_NAMES:
            problems.append(f"ignored file in release set: {name}")

        if not src.is_file():
            problems.append(f"source vanished before packaging: {rel.as_posix()}")

    # Surface caches that exist on disk. They are excluded from the archive by
    # construction, so this is informational, not a failure.
    strays: set[Path] = set()
    for pattern in ("**/__pycache__", "**/.pytest_cache", ".pytest_cache", ".benchmarks"):
        strays.update(path for path in ROOT.glob(pattern) if path.exists())
    for stray in sorted(strays):
        warnings.append(f"cache present on disk (excluded from archive): {stray.relative_to(ROOT).as_posix()}")

    for warning in warnings:
        print(f"WARN: {warning}")

    if problems:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        fail("release tree did not pass --check")

    print_plan(version, entries)
    print("CHECK OK: required files present; no caches, bytecode, or unsafe paths in the release set.")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(version: str, entries: list[tuple[PurePosixPath, Path]]) -> None:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = DIST_DIR / f"houdini-rt-toggle-{version}.zip"
    tmp_path = DIST_DIR / f".houdini-rt-toggle-{version}.zip.tmp"

    with zipfile.ZipFile(
        tmp_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for rel, src in entries:
            info = zipfile.ZipInfo(arcname(version, rel), date_time=FIXED_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3  # unix, so external_attr is honoured
            info.external_attr = FILE_MODE << 16
            archive.writestr(info, src.read_bytes())

    tmp_path.replace(zip_path)

    size = zip_path.stat().st_size
    digest = sha256_of(zip_path)

    print(f"Wrote        : {zip_path}")
    print(f"Members      : {len(entries)}")
    print(f"Size         : {size} bytes ({size / 1024:.1f} KiB)")
    print(f"SHA-256      : {digest}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the houdini-rt-toggle release zip.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the release tree without writing the archive",
    )
    args = parser.parse_args(argv)

    version = read_version()
    entries = collect_entries()

    if not entries:
        fail("no files collected for the release archive")

    if args.check:
        check(version, entries)
        return 0

    build(version, entries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
