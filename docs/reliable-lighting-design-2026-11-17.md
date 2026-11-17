# Reliable lighting — Phase 3

The approved 2026-11-16 roadmap advances the integrated Solaris viewer to reliable
lighting. Native Houdini Light and Light Mixer controls remain the editing surface;
HoudiniRT's existing viewer controller owns enable, pause, status and restoration.

## Scope and contracts

- Untextured dome, distant, rectangle, disk and uniformly scaled sphere lights.
  Intensity, exposure, color, color temperature, visibility, transform and normalize
  use UsdLux conventions. Area normalization uses world surface area; distant angle
  is the angular diameter. Unsupported features are reported by scene path.
- Power-weighted light selection, next-event estimation and power-heuristic MIS
  with diffuse continuation. Delta distant lights have no BSDF competing density.
  Existing dome-only random streams and camera-invisible dome behavior are preserved.
- Authored normals support constant, uniform, vertex, varying and face-varying
  interpolation, including indexed data. Invalid normals fall back visibly to
  generated smooth normals. Shading-only edits do not rebuild acceleration structures.
- Diagnostic outputs use deterministic pixel-center primary hits: world normal,
  eye normal, projected depth [0,1], ray distance (cameraDepth), native primitive ID,
  authored polygon ID, instance ID (-1 for uninstanced meshes), and matte albedo.
  Beauty retains progressive box-filtered sampling. Miss values are documented and
  checked; all outputs publish under the same generation fence.
- An on-demand, bounded scene-support scan inspects the intended viewer's stage.
  It states its provenance, incomplete/stale status and exact unsupported paths.
  Material graphs, textures/HDRI, subdivision and instancing remain later work.

## Implementation boundaries

Sync publishes CPU data only. The render thread owns GPU light, shading and guide
buffers and their teardown. Light changes upload a light table; normal-only edits
upload shading data. Neither requires geometry uploads or BLAS construction.
Primary guides are generated independently of beauty randomness and read back only
when requested. The installed capability profile ships alongside the native plugin.

## Verification and delivery

Supplementary Phase 3 acceptance uses independently frozen analytic/quadrature
lighting references, normal/ID fixtures, AOV semantics, and native Karma comparisons.
The existing sealed s5/s4 exams remain unchanged and must pass with a fresh nonce.
Vulkan validation must remain silent. Python controller/report regressions must pass.

A versioned package is installed into the regular Houdini 22.0.429 preference setup,
preserving other package registrations and the previous package for rollback. A fresh
normal-preference GUI verifies native light edits, pause and edits while paused,
resume, intended-viewer targeting, support reporting and previous-renderer restoration.
The camera-edit repair and mixed-view grid restoration remain regression requirements.

Primary conventions: installed Houdini 22.0.429 USD headers, local
D:/Houdini_AI_Reference, OpenUSD UsdLux LightAPI, and PBRT 4e light sampling/MIS.
Phase 3 has no sealed exam in this repository; supplementary results will be labelled
as such, separately from the unchanged sealed regression.
