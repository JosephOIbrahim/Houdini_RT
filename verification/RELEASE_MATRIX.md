# RT Toggle — release claim matrix (v0.1.0)

Generated 2026-11-11 by the release-audit agent. This file covers the
**release loop only**: what the automated audit proves locally, and what the
release owner must still run. It does not replace `verification/MATRIX.md`,
which tracks Houdini-behaviour claims.

The tree changed while this audit ran. The results below are from the last
audit run of this task: **17 passed, 0 failed, 1 warning, exit 0 — PASS**.
The one warning is the tree-cache policy noted below. Re-run the audit after
any further edit, because the text-file count and the zip hash move with the
tree.

## Status legend

| Status | Meaning |
|---|---|
| `locally_tested` | A local command exists, was run on this workstation, and its result is recorded below. |
| `not_run` | No local execution was performed. Git, GitHub, and live-Houdini steps belong to the release owner. |

`locally_tested` says the check ran, not that it passed. The result column
carries the outcome.

## Commands used

All commands run from the extension root. No command writes bytecode or a
pytest cache into the tree.

```powershell
cd <extension root>
$env:PYTHONDONTWRITEBYTECODE = "1"
py -3.12 -m pytest tests -q -p no:cacheprovider
py -3.12 tools/audit_release.py
```

The audit is stdlib-only and exits 0 only when every non-skipped check passes.
A missing release zip is reported as `zip not built` and is not a failure.

## Matrix

| # | Claim | Verification | Status | Result | Evidence |
|---|---|---|---|---|---|
| 1 | The full test suite passes on CPython 3.12. | `py -3.12 -m pytest tests -q -p no:cacheprovider` | `locally_tested` | PASS | 74 passed in 1.37s (CPython 3.12.10, pytest 9.0.2). |
| 2 | `rt_toggle.json` is valid JSON. | audit check | `locally_tested` | PASS | `json.loads` succeeds. |
| 3 | `houdini/rt_panel.pypanel` is well-formed XML. | audit check | `locally_tested` | PASS | `ElementTree.fromstring` succeeds. |
| 4 | `houdini/rt_shelf.shelf` is well-formed XML. | audit check | `locally_tested` | PASS | `ElementTree.fromstring` succeeds. |
| 5 | `VERSION` is semver-shaped and matches the zip name. | audit check | `locally_tested` | PASS | `0.1.0`. |
| 6 | All required release files and the `tests/` directory exist in the tree. | audit check | `locally_tested` | PASS | 11 required files plus `tests/` present. |
| 7 | No likely secret patterns in tracked text files. | audit check | `locally_tested` | PASS | 35 text files scanned, 0 hits. The audit excludes its own script and the CI scanner definition; token patterns are key-shaped. |
| 8 | No likely secret patterns inside the release zip's text members. | audit check | `locally_tested` | PASS | Zip text members scanned; 0 hits. |
| 9 | No cache or VCS directories in the tree. | audit check | `locally_tested` | WARN | `.benchmarks/`, `.pytest_cache/`, `houdini/__pycache__/`, `rt_runtime/__pycache__/`, `tests/__pycache__/` are present but git-ignored and excluded from the zip. See the policy note below. |
| 10 | No compiled or state files in the tree. | audit check | `locally_tested` | PASS | 0 `.pyc`/`.pyo` outside the pruned cache directories; no runtime state file. |
| 11 | No machine-specific absolute paths in tracked text. | audit check | `locally_tested` | PASS | 35 text files scanned, 0 hits. Hits in `houdini/rt_shelf.py`, `houdini/packages/rt_toggle.json`, and `verification/RELEASE_CHECKS.md` were fixed during this task; a transient regression in the doc was also fixed before the final run. |
| 12 | No machine-specific absolute paths inside the release zip. | audit check | `locally_tested` | PASS | The rebuilt zip carries 0 path hits. The pre-rebuild zip failed this check. |
| 13 | Release zip exists at `dist/houdini-rt-toggle-0.1.0.zip`. | audit check | `locally_tested` | PASS | 35 members; the builder prints the current size. |
| 14 | Zip has one top-level folder named `houdini-rt-toggle-0.1.0/`. | audit check | `locally_tested` | PASS | Every member carries that prefix. |
| 15 | Zip integrity and member-name safety. | audit check | `locally_tested` | PASS | `testzip` clean; no absolute or parent-escaping member names. |
| 16 | Zip contains no cache, compiled, or state entries. | audit check | `locally_tested` | PASS | 0 forbidden entries. |
| 17 | Zip contains every required release file. | audit check | `locally_tested` | PASS | All 11 required files present, including `README.md`. |
| 18 | Zip SHA-256 is recorded by the builder. | `tools/build_release.py` output | `locally_tested` | PASS | The value changes whenever any shipped file changes; use the hash printed by the final build, not a hash copied into this file. |
| 19 | Git commit created. | `git add` / `git commit` | `not_run` | not run | Release owner. |
| 20 | Push to the private `houdini-rt-toggle` repository. | `git push` | `not_run` | not run | Release owner. |
| 21 | Annotated tag `v0.1.0` created. | `git tag -a v0.1.0` | `not_run` | not run | Release owner. |
| 22 | GitHub release published for `v0.1.0`. | `gh release create` | `not_run` | not run | Release owner. |
| 23 | Extension installs and runs inside Houdini 22.0.429. | manual | `not_run` | not run | Tracked in `verification/MATRIX.md` and `verification/INSTALL_CHECKS.md`. |

## Audit policy note (read before signing off)

The audit's tree-cache check was changed during this task from FAIL to WARN by
another contributor. The rationale recorded in the script is that these
directories are git-ignored and excluded from the release zip, so they cannot
reach the published artifact.

That rationale holds for the artifact, but the release brief this audit was
built from lists working-tree caches as a **failure**. The current behaviour
is therefore a policy divergence, not a bug:

- Tree caches: `WARN` (current), brief required `FAIL`.
- Zip cache entries: `FAIL` (unchanged, still strict).
- `.github/workflows/tests.yml` is excluded from the text scan because it is
  itself a scanner definition. A real secret or path placed in that file
  would not be caught.

The release owner should decide which behaviour is wanted. Restoring FAIL is
a one-line change in `check_forbidden_tree`.

`verification/RELEASE_CHECKS.md` (row P7) still states that the audit fails
while cache directories exist. That is now inaccurate and should be aligned
with whichever policy is chosen.

## Resolved during this task

These absolute-path hits were present when the audit was first written and are
gone from the working tree now:

| File | Fix |
|---|---|
| `houdini/rt_shelf.py` | `REPO_ROOT` is now empty; the root comes from `RT_TOGGLE_ROOT` or the file's own location. |
| `houdini/packages/rt_toggle.json` | `PYTHONPATH` now uses `$HOUDINI_RT_ROOT/extensions/rt_toggle`. |
| `verification/RELEASE_CHECKS.md` | Path example replaced. |

## Blocking list before commit

The automated gate is green at the last run. Two items remain:

1. Decide the tree-cache policy above. If the brief's `FAIL` behaviour is
   required, restore it in `check_forbidden_tree` (or remove the ignored
   cache directories), and align `verification/RELEASE_CHECKS.md` P7.
2. Perform the git/push/tag/release steps. Nothing in this file has tested
   them.
