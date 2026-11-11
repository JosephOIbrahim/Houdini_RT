# Changelog

All notable changes to the RT Toggle extension are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**Release claim boundary:** `0.1.0` is a scaffold release. It has been exercised
only by its own pytest suite on CPython 3.12.10. No Houdini session and no
hython session has been launched, so no Houdini-side behaviour in this release
is proven. The wording below distinguishes what the files contain from what has
actually run.

## [Unreleased]

## [0.1.0] - 2026-11-11

### Added

- **`rt_runtime` pure-Python package** (`rt_runtime/`) with the public surface
  `RuntimeTarget`, `RuntimeAdapter`, `RuntimeState`, `ActivationResult`,
  `load_targets`, and `get_adapter`. Standard library only; it never imports
  `hou`, at module level or later.
- **ABI compatibility guard** (`rt_runtime/compat.py`). It inspects extension
  module filenames only (PEP 3149 `cp313`/`cp314` tags and the PEP 384 `abi3`
  stable-ABI tag), never imports the modules it inspects, and returns a
  `CompatibilityReport` whose `safe` flag is the only activation decision.
- **Idempotent runtime adapter** (`rt_runtime/adapter.py`). It appends the
  target's `site_packages` to `sys.path`, applies the target's environment
  variables, and records exactly what it changed in an atomically written JSON
  state file (`.rt_toggle_state.json`). `status()` reports `OFF`, `ON`, or
  `ERROR`; `deactivate()` removes only paths the adapter itself added and
  restores prior environment values.
- **Shared access point** (`rt_runtime/session.py`). `get_adapter()` returns one
  cached adapter per (config, target) pair, so the panel, shelf tool, and node
  recipe share one lock, one `sys.path` bookkeeping record, and one state file.
- **Runtime target config** (`rt_toggle.json`) describing one target,
  `houdini_rt`: the HoudiniRT distribution root as `home`, declared Python
  `3.14`, that tree's `Lib/site-packages` as `site_packages`,
  `entry_point: null`, and `allow_pure_python_only: false`.
- **Python Panel surface** (`houdini/rt_panel.pypanel` plus readable copy
  `houdini/rt_panel.py`). Interface name `RT_Toggle`; the widget script is
  embedded in a CDATA block, so the `.pypanel` is self-contained.
- **Shelf surface** (`houdini/rt_shelf.shelf` plus `houdini/rt_shelf.py`).
  Shelf set `RT`, shelf tab `RT`, tool `rt_toggle`. The shelf XML is a bootstrap
  that locates and calls the sibling module.
- **Node recipe** (`houdini/rt_node.py` and `houdini/RT_TOGGLE_NODE.md`). A
  top-level `build()` entry point, an `enable` toggle, and a parm callback that
  writes the activation result into `rt_status`.
- **Pane tab type menu supplement** (`houdini/PaneTabTypeMenu.xml`) that inserts
  `pythonpanel::RT_Toggle` after `pythonpanel::log_viewer`.
- **Install package file** (`houdini/packages/rt_toggle.json`) that appends only
  the extension root to `PYTHONPATH`; it never adds the HoudiniRT
  `site-packages` tree.
- **Documentation**: `DESIGN.md` (surface analysis and design rationale),
  `INSTALL.md` (six-step install and uninstall), `docs/CLAIMS_VS_REFERENCE.md`
  (evidence review of the ABI boundary), and three verification documents
  (`verification/RUN.md`, `verification/MATRIX.md`,
  `verification/INSTALL_CHECKS.md`).
- **Test suite** (`tests/`). 74 pytest tests collected and passed locally on
  CPython 3.12.10 on 2026-11-11, using a pure-Python `fake_hou` stand-in. The
  suite covers adapter state handling, compatibility verdicts, the state-file
  schema, and the `.pypanel` / `.shelf` / node-recipe file contracts.
- **Release tooling** (`tools/build_release.py`, `tools/audit_release.py`). The
  builder writes a deterministic `dist/houdini-rt-toggle-<version>.zip` from a
  `VERSION` file, with sorted members, a fixed ZIP timestamp, and fixed
  permissions. It excludes `__pycache__`, `.pytest_cache`, `.benchmarks`,
  `.pyc`/`.pyo`, `.git`, `dist`, and the runtime state file. The audit checks
  required files, cache and state artefacts, secret patterns, machine-specific
  absolute paths, JSON/XML parseability, and, when a zip exists, its layout,
  integrity, required members, and text hygiene.

### Changed

- **Surface choice.** The original request suggested the viewport camera
  dropdown. Houdini 22.0.429 documents no extension point there, so the release
  ships a Python Panel, a shelf tool, and a node recipe instead. The evidence is
  in `DESIGN.md` section 2.1.
- **Runtime exposure.** The original activation idea was to add the whole
  HoudiniRT `site-packages` to `PYTHONPATH`. That crosses a CPython 3.13-to-3.14
  ABI boundary, so the release adds only the extension root to `PYTHONPATH` and
  routes all runtime access through the compatibility guard. The correction is
  documented in `docs/CLAIMS_VS_REFERENCE.md` section 4.
- **Pure-Python allowance.** `allow_pure_python_only` is read from each target's
  config entry, carried on `RuntimeTarget`, and forwarded to
  `check_compatibility`. It defaults to `false`, so a version mismatch is
  refused unless a target explicitly opts in.
- **`get_adapter` location.** The function lives in `rt_runtime/session.py` and
  is re-exported from the package root, alongside the adapter types.

### Security

- **Strict ABI default.** The guard refuses the default 3.14 target under
  Houdini 22.0.429's 3.13.10 interpreter, including a pure-Python-only tree,
  unless that target sets `allow_pure_python_only: true`. A compiled filename
  whose ABI tag conflicts with the host is refused even when the declared
  versions match.
- **Filename-only inspection.** The guard parses names only. It does not import,
  execute, load, or subprocess the modules it scans, and it performs no network
  access.
- **Fail-safe activation.** If the compatibility module is missing, exposes no
  check function, or raises, activation refuses and changes nothing. A crash in
  the guard cannot take down the host.
- **Atomic state and rollback.** State writes go to a temporary file, are
  fsynced where supported, and are installed with `os.replace`. If applying
  `sys.path` or environment changes fails, the adapter rolls back what it
  changed in that call.
- **Exact restoration.** `deactivate()` removes only paths recorded as added by
  this adapter. A path that was already on `sys.path` is never recorded and
  never removed. Environment variables that were previously unset are unset
  again.
- **No module unloading pretence.** `sys.modules` is never mutated. Modules
  already imported from the runtime remain loaded, and `deactivate()` reports
  them instead of claiming they were unloaded.
- **Install guardrail.** `INSTALL.md` explicitly prohibits adding
  `%HOUDINI_RT_ROOT%\Lib\site-packages` to `PYTHONPATH`,
  `houdini.env`, a package file, or a startup script.

### Known limitations

- **This is not a Houdini-tested release.** Nothing in this version has run
  inside Houdini 22.0.429 or hython. The panel, the shelf, the pane-menu
  supplement, the node callback, and the guard's exact refusal text are all
  unproven on the target build.
- **Test coverage is plain-CPython only.** The 74 tests ran on CPython 3.12.10,
  not on Houdini's 3.13.10. The compatibility tests simulate the host version by
  passing `houdini_python="3.13"`; that models the ABI decision but does not
  execute Houdini's interpreter.
- **No runtime entry point is selected.** `entry_point` is `null`. "ON" means
  the target's `site-packages` path is visible on `sys.path`; it does not mean a
  named runtime package was imported or verified.
- **The default target is always refused on 22.0.429.** That is intended
  behaviour, not a bug: the target is Python 3.14 and the host is 3.13.10.
- **GUI behaviour is unverified.** The panel's rendering under Houdini's PySide6,
  the shelf icon and label, and the menu supplement's merge position have not
  been observed.
- **Stable-ABI minimums are unknowable from filenames.** The guard cannot
  recover an `abi3` extension's minimum CPython version, so it does not trust
  stable-ABI binaries across minor versions.
- **Release verification is pending.** `VERSION`, `README.md`, and both release
  tools are present, but every item in `verification/RELEASE_CHECKS.md` is
  marked NOT RUN at the time of writing. A zip built during authoring is
  present, but it must be rebuilt after the final document edits; an earlier
  build does not contain later edits.
- **No license file is included.** Licensing has not been decided for this
  release, so no license text is added.
- **Two owner decisions remain open:** (1) a curated pure-Python allowlist
  versus a subprocess runtime for 3.14 work, and (2) the runtime package and
  entry point that would make "ON" mean more than path exposure. Repository
  visibility is not open; it has been decided as private.
