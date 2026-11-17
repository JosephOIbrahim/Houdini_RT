# HoudiniRT controls — v0.5.0

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

On Houdini 22.0.429, enabling HoudiniRT temporarily hides the chosen viewer's
reference grid to avoid a native viewport stall during camera edits. Off restores
its original visibility; a manual renderer switch also restores it, even with
the panel closed. Individual split-view grid visibility is preserved across
Houdini's deferred redraw. Camera settings, grid spacing and other viewers are unchanged.
You can turn the reference grid back on while rendering; that choice is respected,
though the host delay can return. This is a scoped workaround for Houdini's
reference-floor drawing, not a change to render quality or camera clipping.

## Check scene support

Choose the intended viewer, then click **Check scene support**. The panel compares
that viewer's displayed USD stage with the matching installed HoudiniRT capability
profile. Findings name exact primitive paths and explain ignored or unverified
features, including materials, texture graphs, light shaping/linking, instancing,
subdivision and authored RenderVar requests. Edit supported lights through
Houdini's **Light LOP** or **Light Mixer**; the panel does not create parallel controls.

Use a native **Material Library** with a **USD MaterialX Builder**, connect an
**OpenPBR Surface**, and bind it through **Assign Material**. This release supports
opaque isotropic constants and bounded image inputs: base color/weight/metalness
and specular color/weight/roughness/IOR. Colors and weights use 0–1; IOR uses 1–3.
Keep other inputs at their node-definition defaults, including diffuse roughness 0.
Constant colors must be untagged scene-linear RGB. Authored roughness 0 uses a numerical
minimum of 0.001; exact delta mirrors are not implemented.

The report resolves full-purpose material assignments, including inherited
bindings, and names the exact property that triggers whole-mesh matte displayColor
fallback. Arbitrary shader graphs, color-space tags on constants, material face subsets,
active displacement and other shader models are outside this release.
The native builder's unchanged zero-displacement output is accepted.
Unused material definitions do not produce fallback warnings. Material edits mark
the report outdated; click **Check scene support** again after editing.
OpenPBR requires each light's diffuse and specular controls at 1: the current diffuse
factor scales both lobes, and the specular factor is ignored. Other values receive
a path-specific limitation. Material and light editing stay in Houdini's native nodes.
Interface-input connections are marked **Not assessed**: Hydra can flatten them
into supported constants, so the report does not claim that they necessarily fall back.

Connect native **MtlX Image** nodes directly to the supported OpenPBR inputs. Use
Color for color inputs and Float for scalar inputs. UVs use `st`: **MtlX Texcoord**
index 0 or **MtlX Geompropvalue** named `st`, optionally through **MtlX Place2D**
or **MtlX USD Transform2D**. Constant 2D coordinates are also supported. Place2D and
USD Transform2D have different native transform semantics. Addressing supports
constant, clamp, periodic and mirror; filtering supports closest and linear.
UDIMs, sequences, normal maps, arbitrary graph nodes, mipmaps and cubic filtering
remain outside this release. Each image graph is limited to 16 nodes and depth 8,
including the image node. Constant-address borders must fit the target input range;
for IOR, explicitly set a border/default in 1–3 if using constant addressing.

Use **Dome Light** with a latitude-longitude HDRI and its native rotation/pole-axis
controls. Up to eight textured domes illuminate surfaces and reflections. Camera
background remains transparent. Automatic projection requires a 2:1 image.
Dome transforms require uniform scale without shear.
Image File Color Space is interpreted by the native loader using Houdini's OCIO
context; empty color metadata infers sRGB for color PNG/JPEG and linear for EXR/HDR.
Scalar/data maps use raw values.
Named image/HDRI spaces convert to the active native OCIO `scene_linear` role.
For bounded material maps, nonidentity transforms allow only 0.00003 of numerical
endpoint roundoff before boundary correction; raw and identity transforms retain
strict input bounds. HDRI radiance is not restricted to 0–1.

The support report does not open, resolve or decode asset files and does not claim
that an image exists or can be loaded. It reports these checks as **Not assessed**.
Actual asset, decoding and color-processing failures appear in native renderer
warnings/logs and the `hrt_texture_diagnostics` render statistic. Houdini 22's
Python viewer API does not expose that statistic to this panel.

Rectangle, disk and sphere lights are visible opaque emitters in HoudiniRT.
Karma's `renderlightgeo` and `lightgeocastsshadow` switches are not evaluated,
so light visibility and occlusion can differ from Karma. The report identifies
this limitation at each affected light path.

This is a stage inspection, not a count of geometry translated by Hydra or proof
that an output rendered correctly. It records the viewer, frame and stage-lock
state. Native renderer selection and pause state remain separate.

Scanning runs in short main-thread steps and can be cancelled. A large scan stops
after 20,000 prims and displays **Partial**; at most 200 findings are shown, with an
explicit omitted count. Stage, frame or viewer changes mark the result **Outdated**.
Click again to inspect the current scene. Closing/hiding the panel ends the scan.
No USD opinions, light parameters, renderer selection or saved preferences change.

A missing or incompatible installed profile is reported as unavailable. A registered
older renderer cannot borrow a newer installation's capability claims. Analytic USD
shapes are marked **Not assessed**, since imaging adapters may convert them to meshes.

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
