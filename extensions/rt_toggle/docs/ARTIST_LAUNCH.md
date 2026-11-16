# Artist launch — HoudiniRT v0.2

HoudiniRT is one renderer workflow in Houdini 22 / Solaris. The panel, shelf,
radial menu and optional node share a session controller. The main toggle
selects HoudiniRT in a target Scene Viewer; assistant connectivity does not
control renderer state.

Install the controls and matching renderer using [INSTALL.md](../INSTALL.md).
The package sets `RT_TOGGLE_ROOT` to this extension. Start with a Solaris Scene
Viewer displaying the scene you want to render.

## Panel: choose, render, pause, restore

1. Open the **HoudiniRT** Python Panel.
2. Choose **Target viewer**. With several Solaris viewers, choose explicitly.
3. Click **Render with HoudiniRT**.
4. Use **Pause** or **Resume** for that viewer.
5. Click **Turn off — restore previous renderer** to restore its recorded
   renderer and pause state.

The panel reads native state every 500 ms while visible. **Active (unpaused)**
means HoudiniRT is selected and the native pause flag is false. **Paused**
means that flag is true. Inspect image progress and renderer errors in the
viewport; these labels do not measure samples, first pixels or convergence.

Houdini can apply requests asynchronously. The panel shows **Pausing…**,
**Resuming…** or **Restoring previous renderer…** alongside the observed
state. Mutation buttons are unavailable while confirmation is pending. A
10-second timeout reports an error; an incomplete Off can be retried.

Ownership lasts for the current Houdini process and survives panel recreation.
Closing the panel does not turn rendering off. A manual renderer switch
relinquishes ownership, so a later Off does not overwrite that choice. If
HoudiniRT was already selected outside these controls, no previous renderer is
invented: use the viewport renderer menu to switch away.

## Shelf and viewer menu

The **RT** shelf contains four tools:

| Tool | Action |
|---|---|
| HoudiniRT | Enable in the target viewer, or restore its previous renderer. |
| Pause / Resume RT | Change that viewer's pause state. |
| RT Turn | Submit an optional assistant turn. |
| RT Turn Cancel | Cancel an optional assistant turn. |

Render tools carry Solaris/LOP viewer context. Their status appears in the
status bar and Python output; shelf icons are static. The invoking viewer is
preferred. Without one, controls use the Solaris viewer under the cursor or
the only eligible viewer. An ambiguous target produces a message.

## Radial menu

Build the **RT** menu from Houdini's Python Shell:

```python
import os, sys
sys.path.insert(0, os.path.join(os.environ["RT_TOGGLE_ROOT"], "houdini"))
import rt_radial
rt_radial.build()
```

Assign a key in Houdini's Radial Menu Editor. The top item is **HoudiniRT**,
right is **Pause / Resume RT**, bottom is **RT Turn**, and left is **RT Turn
Cancel**. Checks reflect native renderer selection and pause state. Repeated
commands during a pending request report the current request without issuing
it again. Radial GUI interaction remains subject to the verification guide.

## Node and optional viewport HUD

The [RT_TOGGLE node guide](../houdini/RT_TOGGLE_NODE.md) covers **Solaris Viewer**,
**Render with HoudiniRT**, **Pause / Resume**, **Restore Previous Renderer**,
and **Refresh Render State**. The node refreshes only on explicit actions;
a saved checkbox or status text is not a live renderer indicator.

**HUD recipe — untested in this milestone:** select the built node, drag its
Enable, Pause, Restore or Refresh parameter into the viewport, then configure
Persistent and Lock Placement in Houdini's handle menu. These are native
parameter handles. Their behavior over the Vulkan viewport still needs a
live check; no custom viewer overlay or new HUD API is provided here.

Current evidence and remaining manual checks are in
[VIEWER_MILESTONE.md](../verification/VIEWER_MILESTONE.md). Older turn-service
checklists describe the optional assistant, not renderer-control acceptance.
