# Installed native texture and HDRI workflow

This is an opt-in test in fresh, owned regular Houdini 22.0.429 processes.
It loads normal preferences and packages and verifies the installed manifest,
controller, panel, capability profile and actual loaded renderer DLL. It never
opens a user HIP. Acquire the shared rendering slot before any GUI launch.

The artist fixture uses native Material Library / OpenPBR / Assign Material,
Image / Texcoord / Place2D, and Dome Light 3.0. The Python LOP creates only the
small receiver geometry, camera, render settings and a deliberately unsupported
light. Texture and lighting edits use the native node parameters.

Nine current-image captures cover baseline, UV placement, roughness map, image
file swap, HDRI rotation, exposure, HDRI file swap, a missing image and recovery.
Assets remain immutable so every original and reference stage reads identical
bytes. The missing path remains absent. Each capture records the exported stage,
asset hashes, effective sample settings, actual camera/viewport and current
native generation/scene identity.

The workflow also checks the integrated support action and its stale state,
eight seconds without GPU work after active pause acknowledgment, a native UV
edit while paused, latest-scene resume, panel recreation, a second viewer,
camera edits, and restoration of the previous paused Karma renderer and grids.
Missing-file availability is correctly **Not assessed** by the panel; native
warnings/logs and the independent image judge verify fallback and recovery.

Generate the immutable candidate input images once, with H22 hython:

```text
hython -B tests/textures_hdri/installed/prepare_assets.py build/m7/gui-assets
hython -B tests/textures_hdri/installed/probe_native_fixture.py
```

The CPU-only probe cooks and exports all nine actual native graph states without
opening a GUI or rendering. Its receipt is `build/m7/gui-fixture-cpu/receipt.json`.
These input images are not the independent analytic sampler/lighting oracle.

After the tested payload is installed and the GPU slot is explicitly granted:

```powershell
tests/textures_hdri/installed/launch.ps1 -Label accepted-01 -Manifest <installed-manifest>
```

The launcher archives and executes the exact helper copies. Observe the actual
native texture/HDRI controls and integrated panel with computer use; preserve its
separate observation receipt.

While paused, the harness opens a task-owned native Parameter pane for Image and
then Dome Light. Each phase waits up to 90 seconds for its actual CUA observation.
Only after viewing the controls, write `native_image_observed.json` or
`native_dome_observed.json` in the run directory, respectively, with this shape:

```json
{"observed": true, "phase": "image", "node": "/stage/hrt_native_materials/openpbr/base_image", "notes": "Observed native controls and their values"}
```

For the dome phase use `phase: "dome"` and `node: "/stage/hrt_native_hdri"`.
The harness records marker hashes and closes its Parameter pane before captures.
These markers document observations; they must not be generated speculatively.
When the owned child exits, run:

```powershell
tests/textures_hdri/installed/audit_processes.ps1 -Label accepted-01
```

Use an unused label after any failure. Preserve failed runs and their logs.
The launch and process audit identify owned processes by PID and creation time;
preexisting processes remain untouched.

## Fresh normal-Houdini image reference

The image comparison uses a fresh normal-Houdini process and delegate per exact
saved stage, through the same flipbook capture path. It tests live updates against
fresh evaluation, not an independent BSDF reference or raw-AOV parity. Separate
CPU/GPU analytic and native ingress tests provide the physical and resource-work
evidence. Houdini's previously measured fractional-coverage capture behavior is
preserved in the independently qualified M6 receipt; no pixels are adjusted.

Freeze the independent judge/criteria before candidate captures, then prepare:

```text
python -B tests/textures_hdri/installed/run_fresh_gui.py --prepare build/m7/installed/gui-accepted-01/result.json --label fresh-01 --qualification <preserved-M6-capture_reference_qualification.json>
```

Review `prepared.json`, `freeze.json`, exact asset/source hashes and archived
helpers. After the original owned session exits and the rendering slot is free:

```text
python -B tests/textures_hdri/installed/run_fresh_gui.py --run build/m7/installed/gui-accepted-01/fresh-01
```

The fresh child verifies the unchanged displayed composition, 626×647 viewport,
camera, 256 samples, seed 101, four bounces, native OCIO file hash/working role/cache
identity, original display context and all captured
display-decoration flags. It restores the prior renderer/pause/display state
before native quit. The wrapper cleans up only its exact owned process handle,
including on failures, and audits preservation before accepting a capture.

The independent judge retains whole-frame alpha 1e-6, RGB relative RMSE 1%, mean
0.5% plus 0.0005, and restored-image 5e-5 limits. No crop, mask, fit, alpha remap,
or exposure correction is permitted. Its separately frozen source owns the
final interpretation; a successful harness run alone is not image acceptance.

Same-path file reload is covered by independent loader tests. This GUI workflow
checks Off/On restoration but does not claim an end-to-end overwrite/reload image
test, because overwriting shared assets would invalidate earlier references.
