# HoudiniRT controls — v0.2.0

One artist workflow for the HoudiniRT Hydra renderer in Houdini 22 / Solaris.
The main toggle enables the renderer in the **chosen Scene Viewer**. It does
not start a Python sidecar or require a model connection.

## Artist workflow

1. Open the **HoudiniRT** Python Panel and choose a Solaris viewer.
2. Click **Render with HoudiniRT**. The panel reads back the actual renderer.
3. Use **Pause / Resume** to control that viewer's rendering.
4. Click **Turn off — restore previous renderer** to return to the renderer
   and pause state recorded when this controller enabled HoudiniRT.

State is read from `currentHydraRenderer()` and `isRendererPaused()` every
500 ms while the panel is visible. **Active (unpaused)** means exactly that;
it is not a claim about first pixels, GPU success, sample count or convergence.
Houdini's viewport remains the place to inspect image progress/errors.
Houdini applies pause changes asynchronously. During a request the panel
keeps showing the observed state, displays Pausing / Resuming / Restoring,
and prevents duplicate commands until readback confirms the change. A
10-second timeout exposes an error and keeps restoration available to retry.

The same controller serves the shelf, radial menu and optional node. State
survives panel recreation for the current Houdini process. Closing a panel
does not switch renderers or stop another viewer's work.

## Targeting and ownership

- The panel uses its chosen viewer; it never retargets a running session when
  the mouse moves. With several viewers, choose explicitly.
- Shelf/radial actions use their invoking viewer. Without a supplied viewer,
  the resolver uses the Solaris viewer under the cursor or the sole eligible
  viewer. Ambiguity is reported rather than selecting the first pane.
- The optional node has a target viewer name, Pause / Resume, Restore Previous
  Renderer, and Refresh.
  Its Enable control is directional; loading/cooking a node does not activate
  rendering. Refresh reads current state into its controls, including after
  a pending native request completes. Restore can retry a partial Off.
- A manual renderer switch relinquishes restoration ownership. Off does not
  overwrite that user choice.
- If HoudiniRT was already enabled elsewhere, Pause / Resume remains usable;
  no previous renderer is invented. Use the viewport renderer menu to switch.
- A failed restoration keeps its recovery record so Off can be retried.

## Install

See [INSTALL.md](INSTALL.md). Install the host controls and stage the matching
HoudiniRT renderer plugin. The existing Python Panel interface ID `RT_Toggle`
is retained for desktop compatibility; its displayed label is **HoudiniRT**.

Render controls need only the pure-Python `rt_runtime` extension and Houdini's
native HOM APIs. Never expose a foreign Python `site-packages` tree to Houdini.

## Assistant diagnostics

The existing assistant service controls and turn probe remain available under
**Assistant diagnostics…**, constructed only when opened. They are optional,
and their availability does not determine renderer state. Their existing
in-process ABI refusal and sidecar configuration are unchanged. The legacy
assistant transport still uses synchronous I/O and is not part of this
milestone's responsive renderer-control path.

## Implementation

`rt_runtime/render_session.py` owns per-pane restoration records and observes
HOM state. Every control calls it on Houdini's main application thread. Native
pane identity is used for ownership, not a persisted checkbox or a pane name.
Renderer ownership stays in the session. The optional node mirrors observed
state into its controls on actions/Refresh; no render control saves a HIP or
runtime state file.

The renderer and external Python runtime retain their implementation
boundaries within one product. Render selection does not unload a plugin DLL,
and Pause does not promise to release GPU memory.

## Verification

Run the extension suite with the runtime interpreter:

```powershell
& "$env:HOUDINI_RT_ROOT/python.exe" -B -m pytest tests -q -p no:cacheprovider
```

`tests/test_render_session.py` covers native readback, multi-viewer ownership,
external switches, partial failures, restoration and main-thread access.
`test_render_surfaces.py` and `test_render_panel.py` exercise targeting and UI
state. `verification/gui_viewer_smoke.py` is an opt-in isolated Houdini GUI
test that clicks the actual panel and records native readback. See
[the milestone verification guide](verification/VIEWER_MILESTONE.md) for scope
and the exact evidence requirements.

The earlier runtime verification documents remain historical records; they
do not establish the renderer-control behavior added in v0.2.0.

Apache-2.0. See [LICENSE](LICENSE).
