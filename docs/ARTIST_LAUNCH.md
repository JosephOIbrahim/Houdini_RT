# Artist launch — shelf, panel and viewport controls

How an artist finds and uses RT Toggle without reading this repository. Every
claim carries its source and its status; nothing here is a promise the build has
not made.

Status words used below: **documented** (stated in a cited page), **tested**
(run in Houdini 22.0.429 and recorded), **unverified** (not exercised in this
build).

## The five native places an artist looks

| Level | Surface | What it gives | Status |
|---|---|---|---|
| 1 | Shelf strip | one click, always visible, custom SVG icons | tested (registration, icons) |
| 2 | Viewer Tab menu | type `rt`, Enter; lists shelf tools | documented |
| 3 | HUD handles | controls floating over the viewport, bound to node parms | documented |
| 4 | Radial menu | direction instead of reading; 3D viewer and network editor only | documented |
| 5 | Python Panel | full state, blockers and turn result; a pane, never the viewport | tested |

## 1. Shelf

Three tools in the `RT` shelf set: **RT Toggle**, **RT Turn**, **RT Turn
Cancel**, each with its own SVG from `houdini/icons/`. The icons and the panel
icon resolve through `RT_TOGGLE_ROOT`; the package JSON sets it, and a copy
install must set it manually.

- Shelf tools can carry a file path, URL or `opdef:` icon — documented in
  `hou.Tool.setIcon`.
- A global hotkey is assigned with **Shift+Ctrl+Alt+click** on the tool —
  documented in `shelf/customize`.

## 2. Viewer Tab menu

The Tab menu over the 3D view lists **shelf tools**, not node types —
documented in `basics/tabmenu`. No new code is needed beyond the tools carrying
viewer context; an artist presses Tab, types `rt`, and gets the three tools.

## 3. HUD handles — the answer to "controls in the viewport"

HUD handles display in the view and control a parameter on a node. The
installed 22.0.429 help lists five handle types — **slider, checkbox,
dropdown, button, trackpad** — and says a handle is created by dragging a
parameter from the parameter editor into the view
(`help:22.0.429/basics.zip/hud_handles.txt`). Houdini's own node pages tell
artists to do exactly this (`nodes/sop/rbdpaint.md:49`).

### The recipe, for the RT_TOGGLE node

1. Build the node: `rt_node.build()` (or open a scene that already has it).
2. Select it so its parameters show.
3. Drag `enable` into the viewport — a **checkbox** HUD.
4. Drag `rt_run_turn` (Run Turn) into the viewport — a **button** HUD.
5. Drag `rt_cancel_turn` (Cancel Turn) into the viewport — a second button HUD.
6. Right-click each handle and turn on **Persistent** so it stays visible when
   another node is selected. Persistent handles are per node instance;
   non-persistent ones are per node type and hide when the node is deselected.
7. Position them, then turn on **Lock Placement** so a stray drag cannot move
   the control.
8. Use the Handles tool in the toolbox to hide or show the whole set.

This is an **artist action, not an install step**. No public HOM API exists to
create parameter HUD handles: a search of `Parm.md`, `Node.md` and `OpNode.md`
in the reference corpus returns no `hud` hits. The only scriptable HUD is a
viewer-state handle (`hudslider`, `hudintslider`) bound through
`ViewerStateTemplate.bindHandle`, and that is a slider for a custom viewer
state, not a button on a node.

### Hard limits, so nobody promises more

- **No global viewport right-click hook.** Right-click menus belong to viewer
  states and exist only while that state is active (`hom/state_menus.md:8`).
- **`hudInfo` is read-only.** It shows hints and values in an experimental
  panel; row types are plain, divider, bargraph, choicegraph and group — no
  buttons (`hom/hud_info.md:101-179`, `hom/hou/SceneViewer.md:505`).
- **Qt cannot be embedded.** `hou.qt.ViewerOverlay` is "read-only information"
  over the scene viewer; a Python Panel is a pane tab (`hom/hou/qt/
  ViewerOverlay.md:13`, `hom/hou/PythonPanel.md:5`).
- **Vulkan is undocumented here.** No corpus page links HUD handles, overlays
  or menus to Vulkan. The only documented Vulkan differences are materials and
  COP output. Whether the handles draw over the Vulkan viewport is
  **unverified** and belongs in the GUI pass.

## 4. Radial menu

Radial menus are supported in the **3D viewer and the network editor** and are
assigned in the Radial Menu Editor (`basics/radialmenus`). They are the fastest
path once learned, and the only native surface that can show a state tick next
to an action. Not implemented yet; it is the next surface after the HUD recipe
is proven.

## 5. Panel

The Python Panel remains the source of truth for state, blockers and turn
results. It is a pane tab, so it can sit beside the view but not over it.

## What SYNAPSE's architecture says about this layer

The SYNAPSE reference implementation (`%USERPROFILE%\SYNAPSE`) settles several
questions by rule rather than taste:

- **UI observes; the host applies** (`AGENTS.md:136`). Surfaces call the
  adapter; they never write runtime state themselves. Ours already does this.
- **One owner per state** (`AGENTS.md:132`). The adapter owns toggle state; the
  shelf, node and panel are thin readers. Do not let a HUD handle, a shelf tool
  and the panel grow three copies of the truth.
- **Honest seam** (`AGENTS.md:27`). An absent sidecar reports "not running",
  never a false ON. The ABI refusal is the same discipline.
- **Runtime is truth** (`AGENTS.md:41`). A documented API is not a working API.
  Probe in the build before shipping a claim; that is why the HUD and Vulkan
  questions are checks, not assertions.
- **A test that can fail; flip only on user click** (`AGENTS.md:159`). No
  auto-activation, and every check must have been watched going red.

## What this document does not claim

- That HUD handles render over the Vulkan viewport.
- That the shelf icons draw at the intended size.
- That any of the three turn controls has been clicked.

All three are in `verification/GUI_CHECKS.md`, under **NOT RUN** until a
person records a result.
