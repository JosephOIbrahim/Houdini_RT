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

### Added

- **Radial menu builder** (`houdini/rt_radial.py`): creates the `RT` menu with
  **RT Toggle** (checked when the sidecar is ON), **RT Turn** and **RT Turn
  Cancel**, each with its icon, using `hou.ui.createRadialMenu` and
  `createScriptItem`. Optional `save_path` writes a `.radialmenu` file; the key
  binding stays a Radial Menu Editor action.
- 6 static tests covering the item spec, icon files, the check expression, the
  generated action scripts and the documented API markers.

## [0.1.5] - 2026-11-12

### Added

- **Three SVG icons** — `houdini/icons/rt_toggle.svg`, `rt_turn.svg` and
  `rt_turn_cancel.svg` — drawn as one family on a 32 px grid. The shelf tools
  and the panel tab now point at them instead of built-in glyphs. The paths go
  through `$RT_TOGGLE_ROOT`, so a copy install must set that variable; hython
  22.0.429 expands the string to a real file.
- A UI contract test that each icon exists, parses as XML, and is referenced by
  the shelf and the panel.
- **`docs/ARTIST_LAUNCH.md`**: the five native places an artist looks — shelf,
  viewer tab menu, HUD handles, radial menu, panel — with a step-by-step HUD
  recipe and the documented hard limits (no API for parameter HUD handles, no
  global viewport right-click hook, `hudInfo` is read-only, Qt cannot be
  embedded in the viewport, Vulkan overlay behaviour undocumented).
- A tenth GUI check for HUD handles, with an optional Vulkan sub-check whose
  honest verdict is NOT RUN when Vulkan is not in use.

### Changed

- The package JSON now sets `RT_TOGGLE_ROOT` alongside `PYTHONPATH`, so a
  package install resolves the icons without a manual environment variable.

## [0.1.4] - 2026-11-12

### Added

- **Turn control on the shelf and the node**, matching the panel. `rt_shelf.py`
  gains `turn_from_shelf()` and `cancel_turn_from_shelf()`; `rt_shelf.shelf`
  gains the **RT Turn** and **RT Turn Cancel** tools. `rt_node.py` gains
  `rt_prompt`, Run Turn, Cancel Turn and a read-only `rt_turn_status` parm.
  All three surfaces submit with `wait_ms=0`; the panel polls on a Qt timer,
  while the shelf and node poll for a bounded window (`RT_TURN_POLL_S`,
  default 2 s) and leave the turn running after that.
- 2 UI contract tests: the shelf exposes both turn tools, and the node recipe
  carries the turn parms and callback.

## [0.1.3] - 2026-11-12

### Added

- **Turn client** (`rt_runtime/turn_control.py`): `TurnControl` with
  `submit`/`status`/`cancel`, returning the raw `(http_status, body)` pair so a
  202 is not read as failure. The token is read from the sidecar state dir on
  every call, because a relaunch rotates it; nothing is cached.
- **Turn poller**: `TurnPoller.refresh()` follows one admitted turn with no Qt,
  no threads and no Houdini, and turns 404/401/transport faults into a terminal
  error state instead of raising.
- **Adapter accessors**: `SidecarAdapter.turn_control()` returns a bound client
  only when a probe says `ready` and a token reads; `RuntimeAdapter.turn_control()`
  forwards it and returns `None` for in-process targets.
- **Panel turn control**: a prompt field, **Run turn** and **Cancel turn**, and
  a status label. Submit uses `wait_ms=0` (202) and a 250 ms Qt timer polls
  `/turn/<id>`, so Houdini never blocks on a turn. Offline by default;
  `RT_SIDECAR_LIVE_MODEL=1` labels and runs a live turn.

### Changed

- `rt_panel.pypanel` is regenerated from `houdini/rt_panel.py`, and a test now
  fails if the two copies drift.

### Tests

- 11 new tests in `tests/test_turn_control.py` (token read, rotation, 202,
  terminal poll, `wait_ms`, 404, cooperative cancel, unknown-key rejection,
  both adapter paths) and 2 new UI contract tests.
- Local Windows: 220 passed / 18 skipped on CPython 3.12.10, 237 passed /
  1 skipped on CPython 3.14.2.

## [0.1.2] - 2026-11-12

### Added

- **Turn loop** in `rt_runtime.sidecar.turn_loop`: a single-flight service over
  the existing loopback control surface. `POST /turn` admits a turn (202) or
  returns the terminal result inline (200 with `wait_ms`); `GET /turn/<id>`
  polls it; `POST /cancel` requests cooperative cancellation through a
  `threading.Event`. One turn runs at a time, a second is rejected `429` with
  `Retry-After: 1`, results are bounded and retained, a 120 s soft deadline
  applies, and the worker is a daemon so shutdown never waits on a hung
  endpoint.
- **Transport routes** in `rt_runtime.sidecar.transport`: `/turn`,
  `/turn/<id>` and `/cancel` with a 128 KiB body cap, `TurnError` → HTTP status
  mapping, and turn state merged into `/status` last.
- **Host wiring** in `rt_runtime.sidecar.host_stub`: the guarded `run_turn`
  entry is built in `--serve`, and the loop is shut down before the server
  stops.

### Changed

- `README.md` and `verification/RUN.md` carry the current suite counts and the
  turn routes.

### Tests

- 32 new turn-loop tests and 16 new transport route tests.
- 207 passed / 18 skipped on CPython 3.12.10; 224 passed / 1 skipped on
  CPython 3.14.2; release audit PASS.

## [0.1.1] - 2026-11-12

### Added

- **Sidecar mode** in `rt_runtime`: a per-target `mode: "sidecar"` and
  `sidecar` block, a `SidecarAdapter`, and `rt_runtime.sidecar` with the
  lifecycle supervisor, loopback transport and host stub. The in-process
  adapter path is unchanged.
- **Supervised 3.14 sidecar**: start/stop/health, lock and stale-lock
  handling, host crash recovery, parent-exit watchdog, a loopback HTTP
  control surface with token auth, and a `status.json` contract.
- **Package precedence**: runtime-first by default; base-first only with an
  explicit degraded opt-in and a visible `DEGRADED` reason.
- **Payload entry point**: `sidecar.entry_point` reaches the host as
  `--entry-point`; the host invokes a no-arg target, a one-context target, or
  the `run_turn` shape with a guarded client and dispatcher.
- **Guarded live turn**: the `run_turn` probe uses a sidecar-scoped
  `model_access.json`, a no-network guarded client by default, and
  `RT_SIDECAR_LIVE_MODEL=1` for a real endpoint turn.

### Changed

- The runtime tree was repaired: missing core DLLs and cp314 binaries were
  restored, and the six affected distributions pass a 798-hash RECORD audit.
- CI now runs 3.12/3.13 on Ubuntu and 3.14 on Windows; actions bumped to v7.
- Sidecar lifecycle and transport suites moved into `tests/`.

### Proven

- RT-first live activation from hython 22.0.429 reaches ON with `/health 200`.
- A live `run_turn` against `https://api.anthropic.com` returns
  `AgentTurnResult: complete`.
- 176 tests pass on Python 3.14; 159 pass with skips on 3.12.

### Known limitations

- GUI render, shelf click, parm event dispatch, gear-menu duplicate and
  uninstall are still manual checks.
- `run_turn` is invoked once at startup; a request/turn service loop is not
  implemented.

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
