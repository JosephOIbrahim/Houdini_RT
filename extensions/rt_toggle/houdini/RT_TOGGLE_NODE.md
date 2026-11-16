# RT_TOGGLE — HoudiniRT node controls, v0.2

`rt_node.py` builds a null named `RT_TOGGLE` with spare parameters for the
shared HoudiniRT viewer controller. The node provides explicit artist actions;
its saved values do not own a renderer session or activate one on load/cook.
The same session owner serves the panel, shelf and radial menu.

## Build

After installing the extension, run in Houdini's Python Shell:

```python
import os, sys
root = os.environ["RT_TOGGLE_ROOT"]
sys.path.insert(0, root)
sys.path.insert(0, os.path.join(root, "houdini"))
import rt_node
rt_node.build()
```

This creates `/obj/RT_TOGGLE`. Passing a geometry network as `parent` creates
a SOP null instead. Building again reuses the node and replaces its parameter
templates without changing the viewer renderer. Callback scripts contain the
extension paths from build time; rebuild after moving the extension.

## Renderer parameters

| Internal name | Artist label | Behavior |
|---|---|---|
| `rt_viewer` | Solaris Viewer | Target Scene Viewer pane name. |
| `enable` | Render with HoudiniRT | Checked requests Enable; unchecked requests Off. |
| `rt_pause` | Pause / Resume | Request the opposite native pause state. |
| `rt_restore` | Restore Previous Renderer | Request Off, including retry after partial restoration. |
| `rt_refresh` | Refresh Render State | Read the native renderer and pause state. |
| `rt_status` | RT Status | Locked text from the last action or Refresh. |

1. Open a Solaris Scene Viewer and display the intended scene.
2. Enter its pane name in **Solaris Viewer**, as listed in the HoudiniRT panel.
   An empty field uses the invoking viewer or the sole eligible viewer, then
   saves the resolved name. Multiple eligible viewers require a clear target.
3. Click **Refresh Render State** before relying on saved values.
4. Check **Render with HoudiniRT** to enable the renderer in that viewer.
5. Use **Pause / Resume** as needed.
6. Uncheck Enable, or press **Restore Previous Renderer**, to restore the
   renderer and pause state recorded when this session enabled HoudiniRT.

The checkbox follows observed selection after each action: it remains checked
when HoudiniRT is paused. It requests a direction rather than blindly inverting
the renderer. An unreadable native state reports an error and preserves the
previous checkbox; a failed Enable/Off request with no observation is reverted.

## Pending state and recovery

Native changes can finish asynchronously. **RT Status** reports a pending
operation alongside the observed state. The checkbox never represents an
unconfirmed desired state. The node has no polling timer: press **Refresh
Render State** after the request completes, or use the live HoudiniRT panel.
Repeated actions while a request is pending do not issue duplicate mutations.
A request that remains unconfirmed for 10 seconds reports an error on readback.

An Off may restore the renderer but fail to restore its pause state. In that
case the checkbox correctly shows Off while the session retains its recovery
record. Press **Restore Previous Renderer** to retry; this button works even
when the checkbox is already unchecked.

Manual renderer changes relinquish the controller's restoration ownership.
If HoudiniRT was selected outside these controls, Pause / Resume is available,
but there is no recorded previous renderer: switch through the viewport menu.
A closed, missing or ambiguous named viewer produces an error and does not
silently redirect the action to another pane.

## Saved scenes and optional assistant controls

The `.hip` saves parameter values and last status text. It does not save the
session's renderer ownership, replay a request, or turn rendering on when
opened. Refresh after loading and whenever another surface changes the viewer.
Two nodes targeting the same viewer share the same session owner.

The optional `rt_prompt`, `rt_run_turn`, `rt_cancel_turn` and `rt_turn_status`
parameters remain available as **RT Prompt**, **Run Turn**, **Cancel Turn** and
**RT Turn Status**. They control assistant turns independently of rendering.

Native parameter HUD handles are an optional, untested recipe; see
[ARTIST_LAUNCH.md](../docs/ARTIST_LAUNCH.md). HDA conversion, locked-asset writes,
HUD rendering and saved-scene round trips require separate live validation.
Current renderer-control evidence and checks are in
[VIEWER_MILESTONE.md](../verification/VIEWER_MILESTONE.md).
