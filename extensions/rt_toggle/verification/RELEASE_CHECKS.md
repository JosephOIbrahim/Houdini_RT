# RT Toggle v0.1.0 — release checks

**Every item in this file is NOT RUN.** This is the lead's release gate, not a
record of a completed release. Do not treat any row below as evidence, and do
not promote a row to passed without pasting the actual command output next to
it.

None of these commands launches Houdini or hython. This checklist validates the
repository and the archive only. Houdini-side checks live in
`verification/INSTALL_CHECKS.md` and stay NOT RUN until a human runs them.

Run everything from the extension root:

```powershell
cd %HOUDINI_RT_ROOT%\extensions\rt_toggle
```

## Prerequisites — verify before starting

| # | Prerequisite | State when this file was written | Result |
|---|---|---|---|
| P1 | `VERSION` exists at the extension root and contains `0.1.0` | **Present** (checked 2026-11-11). A missing or malformed file makes `tools/build_release.py` exit 1. | NOT RUN |
| P2 | `tools/audit_release.py` exists | **Present** (checked 2026-11-11). If it is absent at release time, stop and report; do not substitute another command. | NOT RUN |
| P3 | `README.md` exists | **Present** (checked 2026-11-11). It is required by `audit_release.py` and is included in the archive. | NOT RUN |
| P4 | The extension is inside a git repository with a configured remote | The harness reports the extension root itself is **not** a git repository. Run the git rows from the repository root that contains the extension. | NOT RUN |
| P5 | `gh` is installed and authenticated for the private repository | Not verified here. | NOT RUN |
| P6 | Interpreter discipline | Use `py -3.12` for pytest (CPython 3.12.10, pytest 9.0.2). The bare `python` on this workstation is 3.14.2 with a broken pytest import path per `verification/RUN.md`. The release scripts are standard-library only. | NOT RUN |
| P7 | Working-tree caches are excluded from the release | `audit_release.py` reports `.pytest_cache/`, `.benchmarks/`, or `__pycache__/` in the working tree as a **warning**, because `.gitignore` excludes them and the zip builder skips them. The zip cache check is strict: `zip has no cache/compiled/state entries` must PASS. Run the cleanup command below when you want a warning-free gate. | NOT RUN |
| P8 | No machine-specific absolute paths in tracked text | Must be clean at release time. The audit rejects workstation drive paths and user-profile paths in tracked text. Docs may use `%HOUDINI_RT_ROOT%` and `%USERPROFILE%`, which the audit allows; code must derive the path rather than embed a drive letter. | NOT RUN |

P7 cleanup command, run from the extension root. These caches are regenerable;
do not delete any other directory.

```powershell
Get-ChildItem -Path . -Recurse -Force -Directory |
  Where-Object { $_.Name -in @('__pycache__', '.pytest_cache', '.benchmarks') } |
  Remove-Item -Recurse -Force
```

Pass criteria: the command completes without error, and the audit reports
`[PASS] no cache/VCS directories in tree`. If the caches cannot be removed on
this workstation, a `[WARN] working-tree caches (git- and zip-excluded)` line is
acceptable; the strict gate is `[PASS] zip has no cache/compiled/state entries`.

P8 note: do not satisfy the audit by replacing a working runtime fallback with
an unresolved placeholder. In code, derive the path from `__file__` or use an
environment variable that Houdini actually sets. In documentation,
`%HOUDINI_RT_ROOT%` is an accepted convention.

---

## Pre-commit

Run these before staging the release commit. Each is a separate gate; do not
skip a failure because a later command passes.

### 1 of 6 — Local test suite

```powershell
py -3.12 -m pytest tests -q
```

Pass criteria: the final line reads `74 passed` and the exit code is 0. Any
failure, error, or skip outside the suite's documented optional-file skips is a
stop.

Result: **NOT RUN**

### 2 of 6 — Build the release archive

```powershell
python tools/build_release.py
```

Pass criteria: exits 0, prints a line beginning `Wrote` naming
`dist\houdini-rt-toggle-0.1.0.zip`, and reports a SHA-256. This writes
`dist/houdini-rt-toggle-0.1.0.zip`. If `VERSION` or the version token is
invalid, the script exits 1 and writes nothing.

Optional dry run, writes nothing:

```powershell
python tools/build_release.py --check
```

Pass criteria for the dry run: final line
`CHECK OK: required files present; no caches, bytecode, or unsafe paths in the release set.`
`WARN:` lines about any missing expected document or caches on disk are
informational; `ERROR:` lines are stop conditions.

Result: **NOT RUN**

### 3 of 6 — Audit the release tree

```powershell
python tools/audit_release.py
```

Pass criteria: exit 0, final summary `RESULT: PASS`, and `failed : 0`. A
`[SKIP] release zip - zip not built` line is acceptable only for a dry run
before step 2; for the release gate the zip must exist and pass every archive
check. The audit fails on machine-specific absolute paths in tracked text (P8)
and on cache entries inside the zip; working-tree caches are a warning (P7).

`audit_release.py` recommends disabling bytecode so the audit does not leave a
`__pycache__` behind. The exact gate command is the single line above. If you
want that protection, set the variable first:

```powershell
$env:PYTHONDONTWRITEBYTECODE=1
python tools/audit_release.py
```

Result: **NOT RUN**

### 4 of 6 — Archive sanity: no caches, no absolute member paths

```powershell
$zip = "dist/houdini-rt-toggle-0.1.0.zip"
py -3.12 -c 'import sys, zipfile; names = zipfile.ZipFile(sys.argv[1]).namelist(); prefix = "houdini-rt-toggle-0.1.0/"; bad = [n for n in names if not n.startswith(prefix) or "__pycache__" in n or n.endswith((".pyc", ".pyo"))]; print("\n".join(names)); print("BAD:", bad); sys.exit(1 if bad else 0)' $zip
```

Pass criteria:

- Exit code 0 and `BAD: []`.
- Every member name starts with `houdini-rt-toggle-0.1.0/`.
- No member contains `__pycache__`, `.pytest_cache`, `.benchmarks`.
- No member ends in `.pyc` or `.pyo`.
- No member is an absolute path or contains `..`.

Scope note: this check is about **archive member names** only. An absolute path
remaining inside file content is a separate audit failure (P8), not a
member-name failure. Do not conflate the two.

Result: **NOT RUN**

### 5 of 6 — Version alignment

```powershell
Get-Content VERSION
py -3.12 -c "import rt_runtime; print(rt_runtime.__version__)"
```

Pass criteria: both print `0.1.0`, and the planned git tag is `v0.1.0`. A
mismatch between `VERSION`, `rt_runtime.__version__`, and the release tag is a
stop.

Result: **NOT RUN**

### 6 of 6 — Working tree review before staging

```powershell
git status --short
```

Run from the repository root, not the extension root if they differ. Pass
criteria: only intended release files are modified or untracked, and no
`__pycache__`, `.pytest_cache`, `.benchmarks`, `.rt_toggle_state.json`, or
`dist/` output is staged. `dist/` and caches should be ignored, not committed.

Result: **NOT RUN**

---

## Pre-push

Run after the release commit exists locally and before pushing or tagging.

### 1 of 2 — Confirm the commit contents

```powershell
git status --short
git log -1 --stat
```

Pass criteria: the working tree is clean, and the release commit contains
`CHANGELOG.md`, `RELEASE_NOTES_v0.1.0.md`, `verification/RELEASE_CHECKS.md`,
`VERSION`, and any packaging tooling intended to ship. No cache or state files.

Result: **NOT RUN**

### 2 of 2 — Confirm the tag target

```powershell
git tag --list v0.1.0
git rev-parse HEAD
```

Pass criteria: `v0.1.0` does not already exist, or exists at the intended commit.
Record the full commit hash next to this row before tagging.

Result: **NOT RUN**

---

## Post-release

### 1 of 2 — Confirm the published release

```powershell
gh release view v0.1.0
```

Pass criteria: the release exists, the tag is `v0.1.0`, it is not a draft, and
the asset list includes `houdini-rt-toggle-0.1.0.zip`. The repository is private,
so the release should not be publicly visible.

Optional structured check:

```powershell
gh release view v0.1.0 --json tagName,isDraft,isPrerelease,assets
```

Pass criteria: `"tagName":"v0.1.0"`, `"isDraft":false`, and one asset whose name
ends in `houdini-rt-toggle-0.1.0.zip`.

Result: **NOT RUN**

### 2 of 2 — Re-check the published asset

Download the asset from the release and repeat check 4 of 6 against the
downloaded file. Do not assume the uploaded asset matches the locally built zip;
compare the SHA-256 printed by `tools/build_release.py` with the downloaded
file's hash.

```powershell
Get-FileHash -Algorithm SHA256 .\houdini-rt-toggle-0.1.0.zip
```

Pass criteria: the hash matches the build output, and the member-name check
still passes.

Result: **NOT RUN**

---

## Release claim boundary

If every row above passes, the release claim is still:

- the repository is internally consistent on CPython 3.12.10;
- the archive contains the intended files and no caches or unsafe member paths;
- the published tag and asset match the built archive.

The claim does **not** include any Houdini 22.0.429 behaviour. No panel render,
shelf load, menu merge, node callback, activation, refusal, or toggle has been
executed in Houdini or hython at any point in this release.
