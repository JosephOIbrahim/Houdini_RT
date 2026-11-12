#!/usr/bin/env python3
"""Release audit for houdini-rt-toggle.

Stdlib only. Exit code 0 = PASS, 1 = FAIL.

Checks:
  1. Required release files and directories exist.
  2. No cache/VCS artefacts (.pytest_cache, __pycache__, *.pyc,
     .rt_toggle_state.json, .venv, .git, ...) in the tree or in the zip.
  3. No likely secret patterns in tracked text files.
  4. No machine-specific absolute paths in tracked text files.
  5. rt_toggle.json is valid JSON, the panel/shelf are valid XML, VERSION is
     semver-shaped.
  6. If dist/houdini-rt-toggle-<version>.zip exists: list members, verify the
     single top-level folder is houdini-rt-toggle-<version>/, verify no cache
     entries and no required-file gaps inside it, and print its SHA-256.
     A missing zip is reported as "zip not built" and is NOT a failure.

Usage:
    py -3.12 tools/audit_release.py

Set PYTHONDONTWRITEBYTECODE=1 when running so the audit never leaves a
__pycache__ behind in the tree it is auditing.

This file is excluded from its own secret/path scan: it necessarily contains
the patterns it searches for.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "dist"
VERSION_FALLBACK = "0.1.1"
SELF_REL = "tools/audit_release.py"
# Scanner definitions necessarily contain the patterns they look for.
SCAN_EXCLUDED_RELS = {SELF_REL, ".github/workflows/tests.yml"}

REQUIRED_FILES = [
    "README.md",
    "DESIGN.md",
    "INSTALL.md",
    "VERSION",
    "rt_toggle.json",
    "rt_runtime/__init__.py",
    "rt_runtime/adapter.py",
    "rt_runtime/compat.py",
    "rt_runtime/session.py",
    "houdini/rt_panel.pypanel",
    "houdini/rt_shelf.shelf",
]
REQUIRED_DIRS = ["tests"]

# Directory names that must not ship. Pruned from the text scan (they are
# reported once each by the forbidden-entry check instead of per file).
FORBIDDEN_DIR_NAMES = {
    ".pytest_cache",
    "__pycache__",
    ".venv",
    "venv",
    ".git",
    ".hg",
    ".svn",
    ".benchmarks",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "node_modules",
}
FORBIDDEN_FILE_NAMES = {".rt_toggle_state.json"}
FORBIDDEN_FILE_SUFFIXES = {".pyc", ".pyo"}

# Pruned from the text walk (the zip is a binary archive, scanned separately).
TEXT_WALK_PRUNED = FORBIDDEN_DIR_NAMES | {"dist"}

TEXT_SUFFIXES = {
    ".md",
    ".txt",
    ".py",
    ".pyi",
    ".json",
    ".xml",
    ".shelf",
    ".pypanel",
    ".toml",
    ".cfg",
    ".ini",
    ".yml",
    ".yaml",
    ".ps1",
    ".bat",
    ".cmd",
    ".sh",
    ".css",
    ".js",
    ".html",
    ".gitignore",
    ".gitattributes",
}
TEXT_NAMES = {"VERSION", "LICENSE", "LICENCE", "NOTICE", "Makefile", ".gitignore", ".gitattributes", ".editorconfig"}

MAX_TEXT_BYTES = 4 * 1024 * 1024

# (label, compiled regex). The brief's required patterns come first.
SECRET_PATTERNS = [
    # Key-shaped, not the bare prefix: a scanner definition that spells the
    # prefix followed by a character class must not match itself.
    ("apikey_", re.compile(r"apikey_[A-Za-z0-9_-]{8,}")),
    ("gho_", re.compile(r"\bgho_[A-Za-z0-9]+")),
    ("sk-", re.compile(r"\bsk-[A-Za-z0-9_-]{8,}")),
    ("ANTHROPIC_API_KEY=", re.compile(r"ANTHROPIC_API_KEY\s*=")),
    ("TYPESAFE_API_KEY=", re.compile(r"TYPESAFE_API_KEY\s*=")),
    # Extra high-signal token shapes; harmless if absent.
    ("ghp_", re.compile(r"\bghp_[A-Za-z0-9]{20,}")),
    ("github_pat_", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
]

# The two machine-specific path roots that must never be committed. Both
# slash directions are matched, case-insensitively. %HOUDINI_RT_ROOT% and
# %USERPROFILE% are allowed by construction (they are not matched).
ABS_PATH_PATTERNS = [
    ("E:\\Myth_and_Logic", re.compile(r"(?i)\bE:[\\/]Myth_and_Logic")),
    ("C:\\Users\\User", re.compile(r"(?i)\bC:[\\/]Users[\\/]User")),
]

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
ZIP_ABS_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|/)")


class Audit:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.failures: list[tuple[str, list[str]]] = []

    def ok(self, name: str, detail: str = "") -> None:
        self.passed += 1
        print(f"[PASS] {name}" + (f" - {detail}" if detail else ""))

    def fail(self, name: str, details: list[str]) -> None:
        self.failed += 1
        self.failures.append((name, details))
        print(f"[FAIL] {name}")
        for line in details:
            print(f"       {line}")

    def warn(self, name: str, details: list[str]) -> None:
        self.skipped += 1
        print(f"[WARN] {name}")
        for line in details:
            print(f"       {line}")

    def skip(self, name: str, detail: str = "") -> None:
        self.skipped += 1
        print(f"[SKIP] {name}" + (f" - {detail}" if detail else ""))


def is_text_name(name: str) -> bool:
    return Path(name).suffix.lower() in TEXT_SUFFIXES or name in TEXT_NAMES


def decode_text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="replace")


def scan_text(text: str):
    """Return (secret_hits, abs_path_hits) as lists of (line, label, snippet)."""
    secrets = []
    paths = []
    for label, regex in SECRET_PATTERNS:
        for match in regex.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            secrets.append((line, label, "<redacted>"))
    for label, regex in ABS_PATH_PATTERNS:
        for match in regex.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            paths.append((line, label, match.group(0)))
    return secrets, paths


def iter_text_files():
    """Yield (relative_posix_path, absolute_path) for text files in the tree."""
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in TEXT_WALK_PRUNED)
        for filename in sorted(filenames):
            absolute = Path(dirpath) / filename
            relative = absolute.relative_to(ROOT).as_posix()
            if relative in SCAN_EXCLUDED_RELS:
                continue
            if not is_text_name(filename):
                continue
            try:
                if absolute.stat().st_size > MAX_TEXT_BYTES:
                    continue
            except OSError:
                continue
            yield relative, absolute


def check_required(audit: Audit) -> None:
    missing = [rel for rel in REQUIRED_FILES if not (ROOT / rel).is_file()]
    if missing:
        audit.fail("required files present", [f"missing: {rel}" for rel in missing])
    else:
        audit.ok("required files present", f"{len(REQUIRED_FILES)} files")

    missing_dirs = [rel for rel in REQUIRED_DIRS if not (ROOT / rel).is_dir()]
    if missing_dirs:
        audit.fail("required directories present", [f"missing: {rel}" for rel in missing_dirs])
    else:
        audit.ok("required directories present", ", ".join(REQUIRED_DIRS))


def check_forbidden_tree(audit: Audit) -> None:
    dir_hits = []
    file_hits = []
    pyc_count = 0
    pyc_dirs = set()
    for dirpath, dirnames, filenames in os.walk(ROOT):
        # Record and prune forbidden directories.
        kept = []
        for d in sorted(dirnames):
            if d in FORBIDDEN_DIR_NAMES:
                dir_hits.append((Path(dirpath) / d).relative_to(ROOT).as_posix() + "/")
            else:
                kept.append(d)
        dirnames[:] = kept
        for filename in filenames:
            absolute = Path(dirpath) / filename
            relative = absolute.relative_to(ROOT).as_posix()
            suffix = Path(filename).suffix.lower()
            if suffix in FORBIDDEN_FILE_SUFFIXES:
                pyc_count += 1
                pyc_dirs.add(Path(dirpath).relative_to(ROOT).as_posix())
            elif filename in FORBIDDEN_FILE_NAMES:
                file_hits.append(relative)

    if dir_hits:
        # Caches in the working tree are not published: .gitignore excludes
        # them and the release zip skips them. The zip check below is strict.
        audit.warn("working-tree caches (git- and zip-excluded)",
                   [f"found: {p}" for p in sorted(dir_hits)])
    else:
        audit.ok("no cache/VCS directories in tree")

    details = [f"found: {p}" for p in sorted(file_hits)]
    if pyc_count:
        details.append(f"found: {pyc_count} compiled .pyc/.pyo file(s) under: " + ", ".join(sorted(pyc_dirs)[:8]))
    if details:
        audit.fail("no compiled/state files in tree", details)
    else:
        audit.ok("no compiled/state files in tree")


def check_text_hygiene(audit: Audit) -> None:
    secret_hits = []
    path_hits = []
    scanned = 0
    for relative, absolute in iter_text_files():
        try:
            data = absolute.read_bytes()
        except OSError as exc:
            secret_hits.append(f"{relative}: unreadable ({exc})")
            continue
        scanned += 1
        text = decode_text(data)
        secrets, paths = scan_text(text)
        for line, label, snippet in secrets:
            secret_hits.append(f"{relative}:{line}: secret pattern {label!r} {snippet}")
        for line, label, snippet in paths:
            path_hits.append(f"{relative}:{line}: absolute path {label!r} ({snippet})")

    if secret_hits:
        audit.fail("no secret patterns in tracked text", secret_hits)
    else:
        audit.ok("no secret patterns in tracked text", f"{scanned} text files scanned")

    if path_hits:
        audit.fail("no machine-specific absolute paths in tracked text", path_hits)
    else:
        audit.ok("no machine-specific absolute paths in tracked text", f"{scanned} text files scanned")


def read_version(audit: Audit) -> str:
    version_file = ROOT / "VERSION"
    if not version_file.is_file():
        # Missing VERSION is already reported by check_required; use fallback
        # so the zip name can still be resolved.
        return VERSION_FALLBACK
    version = decode_text(version_file.read_bytes()).strip()
    if not SEMVER_RE.fullmatch(version):
        audit.fail("VERSION is semver", [f"VERSION contains {version!r}"])
        return version or VERSION_FALLBACK
    audit.ok("VERSION is semver", version)
    return version


def check_parse(audit: Audit) -> None:
    manifest = ROOT / "rt_toggle.json"
    if manifest.is_file():
        try:
            json.loads(decode_text(manifest.read_bytes()))
            audit.ok("rt_toggle.json parses as JSON")
        except json.JSONDecodeError as exc:
            audit.fail("rt_toggle.json parses as JSON", [str(exc)])

    for rel in ("houdini/rt_panel.pypanel", "houdini/rt_shelf.shelf"):
        target = ROOT / rel
        if not target.is_file():
            continue  # already reported as missing
        try:
            ET.fromstring(target.read_bytes())
            audit.ok(f"{rel} parses as XML")
        except ET.ParseError as exc:
            audit.fail(f"{rel} parses as XML", [str(exc)])

    tests_dir = ROOT / "tests"
    if tests_dir.is_dir():
        test_files = sorted(p.name for p in tests_dir.glob("test_*.py"))
        if test_files:
            audit.ok("test modules present", f"{len(test_files)} test modules")
        else:
            audit.fail("test modules present", ["no tests/test_*.py found"])


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def zip_relative_text(name: str) -> bool:
    return is_text_name(Path(name).name)


def check_zip(audit: Audit, version: str) -> None:
    zip_path = DIST_DIR / f"houdini-rt-toggle-{version}.zip"
    if not zip_path.is_file():
        audit.skip("release zip", f"zip not built ({zip_path.relative_to(ROOT).as_posix()})")
        return

    expected_prefix = f"houdini-rt-toggle-{version}/"
    digest = sha256_file(zip_path)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            bad_member = archive.testzip()
            members = archive.namelist()
            member_set = set(members)

            print(f"       zip: {zip_path.relative_to(ROOT).as_posix()}")
            print(f"       sha256: {digest}")
            print(f"       members: {len(members)}")
            for member in sorted(members):
                print(f"         {member}")

            # Layout: one top-level folder named houdini-rt-toggle-<version>/.
            wrong_prefix = [m for m in members if not m.startswith(expected_prefix)]
            if wrong_prefix:
                audit.fail(
                    "zip top-level folder is houdini-rt-toggle-<version>/",
                    [f"expected prefix {expected_prefix!r}; off-prefix member: {m}" for m in wrong_prefix[:20]]
                    + ([f"... {len(wrong_prefix) - 20} more"] if len(wrong_prefix) > 20 else []),
                )
            else:
                audit.ok("zip top-level folder is houdini-rt-toggle-<version>/", expected_prefix)

            if bad_member:
                audit.fail("zip integrity", [f"corrupt member: {bad_member}"])
            else:
                audit.ok("zip integrity", "testzip clean")

            # No absolute paths or forbidden entries inside the archive.
            absolute_members = [m for m in members if ZIP_ABS_RE.match(m) or ".." in Path(m).parts]
            if absolute_members:
                audit.fail("zip has no absolute/escaping member names", [f"found: {m}" for m in absolute_members[:20]])
            else:
                audit.ok("zip has no absolute/escaping member names")

            bad_parts = []
            for member in members:
                parts = Path(member).parts
                for part in parts:
                    if part in FORBIDDEN_DIR_NAMES:
                        bad_parts.append(f"forbidden directory entry: {member}")
                        break
                else:
                    name = parts[-1] if parts else ""
                    if name in FORBIDDEN_FILE_NAMES or Path(name).suffix.lower() in FORBIDDEN_FILE_SUFFIXES:
                        bad_parts.append(f"forbidden file entry: {member}")
            if bad_parts:
                audit.fail("zip has no cache/compiled/state entries", bad_parts[:20]
                           + ([f"... {len(bad_parts) - 20} more"] if len(bad_parts) > 20 else []))
            else:
                audit.ok("zip has no cache/compiled/state entries")

            # Required release files must be inside the zip at the expected prefix.
            missing_members = [
                f"missing from zip: {expected_prefix}{rel}"
                for rel in REQUIRED_FILES
                if f"{expected_prefix}{rel}" not in member_set
            ]
            if missing_members:
                audit.fail("zip contains required release files", missing_members)
            else:
                audit.ok("zip contains required release files", f"{len(REQUIRED_FILES)} files")

            # Scan text members with the same secret/path rules.
            zip_secrets = []
            zip_paths = []
            for member in members:
                if member.endswith("/") or not zip_relative_text(member):
                    continue
                # Same self-exclusion as the tree scan: this script contains
                # the very patterns it looks for.
                member_rel = member.replace("\\", "/")
                if any(member_rel.endswith(rel) for rel in SCAN_EXCLUDED_RELS):
                    continue
                try:
                    info = archive.getinfo(member)
                except KeyError:
                    continue
                if info.file_size > MAX_TEXT_BYTES:
                    continue
                text = decode_text(archive.read(member))
                secrets, paths = scan_text(text)
                for line, label, snippet in secrets:
                    zip_secrets.append(f"{member}:{line}: secret pattern {label!r} {snippet}")
                for line, label, snippet in paths:
                    zip_paths.append(f"{member}:{line}: absolute path {label!r} ({snippet})")
            if zip_secrets:
                audit.fail("zip text has no secret patterns", zip_secrets[:20])
            else:
                audit.ok("zip text has no secret patterns")
            if zip_paths:
                audit.fail("zip text has no machine-specific absolute paths", zip_paths[:20])
            else:
                audit.ok("zip text has no machine-specific absolute paths")
    except (zipfile.BadZipFile, OSError) as exc:
        audit.fail("release zip readable", [f"{zip_path.name}: {exc}"])


def main() -> int:
    print("=" * 62)
    print("houdini-rt-toggle release audit")
    print(f"root: {ROOT}")
    print("=" * 62)

    audit = Audit()
    check_required(audit)
    check_forbidden_tree(audit)
    check_text_hygiene(audit)
    version = read_version(audit)
    check_parse(audit)
    check_zip(audit, version)

    print()
    print("=" * 62)
    print("RELEASE AUDIT SUMMARY")
    print(f"  passed : {audit.passed}")
    print(f"  failed : {audit.failed}")
    print(f"  skipped: {audit.skipped}")
    if audit.failures:
        print("  failing checks:")
        for name, _details in audit.failures:
            print(f"    - {name}")
    result = "PASS" if audit.failed == 0 else "FAIL"
    print(f"RESULT: {result}")
    print("=" * 62)
    return 0 if audit.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
