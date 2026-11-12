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
| 19 | Git commit created. | `git add` / `git commit` | `locally_tested` | PASS | `2a42e6c` release commit; `80b554d` results commit. |
| 20 | Push to the private `houdini-rt-toggle` repository. | `git push` | `locally_tested` | PASS | `main` at `80b554d`; `main...origin/main`. |
| 21 | Annotated tag `v0.1.0` created. | `git tag -a v0.1.0` | `locally_tested` | PASS | Tag pushed to origin. |
| 22 | GitHub release published for `v0.1.0`. | `gh release create` | `locally_tested` | PASS | https://github.com/JosephOIbrahim/houdini-rt-toggle/releases/tag/v0.1.0 ; asset digest `sha256:e650e05c…7202a8` matched the local zip. |
| 23 | Extension installs and runs inside Houdini 22.0.429. | hython 22.0.429, 2026-11-11; GUI steps marked | `locally_tested` | PARTIAL | Checks A, B, C and E pass in hython; D's build and callback function pass. The pane render, shelf click and parm event dispatch are GUI-ONLY. Details in `verification/RUN.md`. |
| 24 | Check A: `.pypanel` installs and `interfaceByName("RT_Toggle")` resolves. | hython 22.0.429 | `locally_tested` | PASS | `installFile` ok; interface returned. Render GUI-ONLY. |
| 25 | Check B: the default cp314 target is refused; no cp314 module imported. | hython 22.0.429 | `locally_tested` | PASS | `safe=False`; 104 blockers; `activate()` `False/off/False`; `cp314_modules: []`. Button press GUI-ONLY. |
| 26 | Check C: shelf XSD valid; shelf and tool register; tool script reports refusal. | hython 22.0.429 | `locally_tested` | PASS | XSD `True`, 0 errors; `RT` + `rt_toggle`; one `HoudiniRT: OFF - refused: ...` line. GUI click GUI-ONLY. |
| 27 | Check D: node builds; callback function writes REFUSED and reverts `enable`. | hython 22.0.429 | `locally_tested` | PASS | `/obj/RT_TOGGLE`; direct call wrote `REFUSED`, `enable` 1→0. Event dispatch GUI-ONLY: hython does not dispatch parm callbacks. |
| 28 | Check E: state keys, restart ERROR, toggle ON, deactivate OFF. | hython 22.0.429 | `locally_tested` | PASS | Five keys, ISO timestamp; fresh process `error`; toggle `on`; deactivate `off`, `added_paths: []`. |
| 29 | Check F: one PaneTabTypeMenu entry after `log_viewer`; interface installed. | hython 22.0.429 | `locally_tested` | PASS | One `pythonpanel::RT_Toggle`, anchor `pythonpanel::log_viewer`; menu render and gear menu GUI-ONLY; uninstall NOT RUN. |

## Audit policy note (read before signing off)

The tree-cache policy was settled on **2026-11-11**: working-tree caches are a
`WARN` because they are git-ignored and excluded from the release zip, so they
cannot reach the published artifact. Cache entries inside the zip remain a hard
`FAIL`. `verification/RELEASE_CHECKS.md` P7 was aligned with this policy.

- Tree caches: `WARN` (decided).
- Zip cache entries: `FAIL` (strict).
- `.github/workflows/tests.yml` is excluded from the text scan because it is
  itself a scanner definition. A real secret or path placed in that file
  would not be caught.

## Resolved during this task

These absolute-path hits were present when the audit was first written and are
gone from the working tree now:

| File | Fix |
|---|---|
| `houdini/rt_shelf.py` | `REPO_ROOT` is now empty; the root comes from `RT_TOGGLE_ROOT` or the file's own location. |
| `houdini/packages/rt_toggle.json` | `PYTHONPATH` now uses `$HOUDINI_RT_ROOT/extensions/rt_toggle`. |
| `verification/RELEASE_CHECKS.md` | Path example replaced. |

## Release state

The release is published and this documentation update is on `main`. The
automated gate is green; the git/push/tag/release rows above are complete.

Remaining manual items:

1. GUI checks: pane render, shelf click, parm event dispatch, and the
   gear-menu duplicate question.
2. Uninstall (`verification/INSTALL_CHECKS.md` Check 6), which is still
   `NOT RUN`.
