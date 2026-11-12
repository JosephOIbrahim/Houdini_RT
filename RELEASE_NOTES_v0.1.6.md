# RT Toggle v0.1.6

This release adds the radial menu: the runtime controls at the mouse, with a
state tick on the toggle. It is a small release on purpose — one module, one
checklist step, and no change to the runtime, the sidecar or any existing
surface.

## What is new

- **`houdini/rt_radial.py`** builds the `RT` menu with `hou.ui.createRadialMenu`
  and `createScriptItem`:

  | Location | Item | Icon |
  |---|---|---|
  | Top | **RT Toggle**, checked while the sidecar is ON | `rt_toggle.svg` |
  | Right | **RT Turn** | `rt_turn.svg` |
  | Left | **RT Turn Cancel** | `rt_turn_cancel.svg` |

  An existing menu of the same name is destroyed first, so a rebuild does not
  stack duplicate actions. `build(save_path)` also writes a `.radialmenu` file;
  assigning the key stays a Radial Menu Editor action, because that is the
  artist's choice, not the extension's.
- **One implementation, four surfaces.** The radial actions call the same
  `rt_shelf` helpers as the shelf tools — `toggle_from_shelf`,
  `turn_from_shelf`, `cancel_turn_from_shelf` — rather than growing a fourth
  copy of the logic.
- **Checklist step 11** for the radial menu. It records three verdicts
  separately — builds, ticks, actions run — because a menu that builds but never
  ticks is a check-expression failure, and the actions can still work.

## Proven

- Six static tests: the three items and their distinct locations, the shipped
  icon files, the check expression compiling in eval mode and naming `ON`, the
  generated action scripts compiling and calling the right helpers, the
  documented API markers, and the absence of a top-level `hou` import.
- Suite: 229 passed / 18 skipped locally on CPython 3.12.10 and 246 / 1 on
  CPython 3.14.2. CI run `43301551119` at `cfc81da`: 230 passed / 17 skipped on
  Ubuntu 3.12 and 3.13, 244 passed / 3 skipped on Windows 3.14, all four jobs
  green — six higher per leg than at `a20147a`, none of them skipped away.

## Not proven

- **The menu has never been built in Houdini.** hython 22.0.429 has no `hou.ui`
  attribute at all — confirmed in this build, not assumed — so the radial API
  cannot be exercised headlessly. Step 11 is GUI-only by necessity.
- **The tick has never been seen.** The check expression imports `rt_runtime`
  and reads the adapter status; without the package on `sys.path` it cannot
  evaluate.
- The key binding has not been assigned, and the menu has not been opened with
  a key.
- Steps 1–10 of the GUI checklist remain unrecorded, as does icon rendering and
  HUD behaviour under Vulkan.
- The 3.13 in-process sibling runtime is still planned, not installed.
- Licensing remains TBD.

## Upgrade

Replace the extension folder in place. `VERSION`, `rt_runtime.__version__` and
`pyproject.toml` all read `0.1.6`. No existing surface changed, so no reinstall
of the panel, shelf or node is needed for this release — the radial module is
new and additive. Build it once from the Python Shell, then bind a key in
**Edit > Radial Menus**.

## Install

See `INSTALL.md`, `docs/ARTIST_LAUNCH.md` for the shelf, tab-menu, HUD and radial
paths, and `verification/GUI_CHECKS.md` for the eleven checks.
