# RT Toggle v0.1.5

This release makes the extension look and behave like a Houdini tool rather
than a script with buttons. The three shelf tools and the panel now share one
icon family, the shelf and node can submit turns as well as the panel, and
`docs/ARTIST_LAUNCH.md` records where an artist finds the controls — including
the viewport. Nothing about activation or the ABI guard changes.

## What is new

- **One icon family.** `houdini/icons/rt_toggle.svg`, `rt_turn.svg` and
  `rt_turn_cancel.svg` are drawn on a 32 px grid: one amber glyph each on a dark
  tile, geometry only, no text. They replace `BUTTONS_switch`, `BUTTONS_play`,
  `BUTTONS_stop` and the generic `MISC_python` panel glyph. The paths resolve
  through `RT_TOGGLE_ROOT`, which the package JSON now sets.
- **Turn control on all three surfaces.** The shelf gained **RT Turn** and
  **RT Turn Cancel** tools; the node gained `rt_prompt`, Run Turn, Cancel Turn
  and a read-only `rt_turn_status`. Every surface submits with `wait_ms=0`; the
  panel polls on a 250 ms Qt timer, while the shelf and node poll for a bounded
  window (`RT_TURN_POLL_S`, default 2 s) and then leave the turn reachable
  through the cancel control.
- **An artist-launch document.** `docs/ARTIST_LAUNCH.md` ranks the five native
  places an artist looks — shelf, viewer Tab menu, HUD handles, radial menu,
  panel — with a step-by-step recipe for putting `enable`, Run Turn and Cancel
  Turn over the viewport as HUD handles.
- **A tenth GUI check** covering the HUD handles, with an optional Vulkan
  sub-check.

## The limits, stated plainly

- **HUD handles are an artist action.** No public HOM API creates parameter HUD
  handles; the scriptable `hudslider` belongs to viewer states. The recipe is
  four drags, then **Persistent** and **Lock Placement**.
- **No Qt in the viewport.** `ViewerOverlay` is read-only information; a Python
  Panel is a pane tab.
- **No global viewport right-click hook.** Viewer-state menus exist only while
  that state is active.
- **Vulkan overlay behaviour is undocumented.** No reference-corpus page links
  HUD handles or menus to Vulkan; the documented Vulkan differences are
  materials and COP output.

## Proven

- Shelf: validates against 22.0.429's own `shelf.xsd` with zero errors; the
  three tools register with the intended labels and icons.
- Node: `rt_node.build()` produces `rt_prompt`, `rt_run_turn`, `rt_cancel_turn`
  and a locked `rt_turn_status`; both button callbacks read back as Python and
  name `turn_callback` and `cancel_turn_callback`.
- Icons: `hou.text.expandString` resolves `$RT_TOGGLE_ROOT/...` to an existing
  file for each icon.
- Suite: 223 passed / 18 skipped locally on CPython 3.12.10 and 240 / 1 on
  CPython 3.14.2. CI run `43286654296` at `00d237e`: 224 passed / 17 skipped on
  Ubuntu 3.12 and 3.13, 238 passed / 3 skipped on Windows 3.14, all four jobs
  green.

## Not proven

- **None of the controls has been clicked in Houdini.** The panel buttons, the
  two shelf tools, the three node parms and the HUD handles are statically
  tested or documented; the click and the visible result are the ten steps in
  `verification/GUI_CHECKS.md`, still unrecorded.
- **Icon rendering is unverified.** The strings expand and the files exist; how
  the glyphs draw at shelf size and in the pane tab is a GUI check.
- **HUD handles over Vulkan are unverified**, and the documentation does not
  address it either way.
- The 3.13 in-process sibling runtime is still planned, not installed.
- Licensing remains TBD.

## Upgrade

Replace the extension folder in place. `VERSION`, `rt_runtime.__version__` and
`pyproject.toml` all read `0.1.5`. Reinstall `houdini/rt_panel.pypanel` and the
`rt_shelf.shelf` + `rt_shelf.py` pair together — both changed. Rebuild any
existing RT_TOGGLE node with `rt_node.build()` to pick up the turn parms. If you
install by package JSON, `RT_TOGGLE_ROOT` is now set for you; a copy install
into the toolbar folder must set it, or the tools draw without icons.

## Install

See `INSTALL.md`, and `docs/ARTIST_LAUNCH.md` for the shelf, tab-menu and
viewport paths.
