# HoudiniRT live-edit verification — 2026-11-16

## Tested build and scope

Phase 2A / s5-live-edits, based on integrated viewer controls commit `4252793`.
The artist uses the existing HoudiniRT panel, target viewer, Enable, Pause/Resume
and Off controls. The Python extension remains version 0.2.0; this milestone
changes the native renderer and scene handoff.

Houdini/HDK 22.0.429, Windows, MSVC 19.44.35222, Vulkan SDK 1.4.341.1,
NVIDIA GeForce RTX 4090. Source and protected-exam hashes stayed unchanged
through the successful sealed run. No protected acceptance file was edited.

## Sealed acceptance

- Command: `python -B build/m2/run_gate.py s5`.
- Nonce: `12094041924750763570`; independent s4 nonce `317460601411944871`.
- Result: exit 0, about 147 seconds including s4 and artifact archiving.
- s4/Karma comparison: FLIP p99 0.0249 (limit 0.045), worst level error 0.00056;
  exact scene census and light linearity passed.
- s5: both 11-frame sequences passed, at approximately 1M and 4M triangles.
  Every frame matched its fresh same-frame/seed/spp render: worst FLIP, level
  and alpha differences all zero. The smallest visible edit changed 363 pixels.
- Exact BLAS/TLAS/upload counters, equal work at both ballast sizes, and
  device-local geometry/index/normal/table placement passed.
- Median Execute-to-first-submit edit latency: 0.8874 ms at 1M, 0.9171 ms at 4M.
  This measures renderer edit setup, not full application interaction latency.
- Vulkan validation was silent in the sealed render logs.

Local evidence archive:
`build/m2/runs/20261116T230033Z-s5-12094041924750763570/`.
Its receipt binds source, exams, input stages, images, logs and installed DLL/SPIR-V
by SHA256. The full receipt also ships inside the installed payload.

## Focused regressions

- GPU: 24 states, including transforms, visibility, color, deformation, topology,
  insertion/deletion, path reuse, empty scenes, failed update recovery and invalid
  transforms. Live images matched fresh scenes exactly. Mirrored nonuniform
  transforms matched independently baked world geometry exactly. Analytic
  albedo-times-dome error stayed below 2.63e-7. No validation errors or warnings.
- Zero scale/NaN/infinite transforms never enter a Vulkan instance. Returning to
  a valid transform rebuilt one TLAS, uploaded no geometry and restored the exact
  original image; the existing mesh/BLAS remained cached.
- CPU: immutable snapshots, shared geometry, revision identity, exact used-point
  bounds and hidden census passed. Buffer tests passed stable mapped-reader
  lifetimes, nested mappings, pointer swaps and concurrent publication.
- The existing integrated controller's focused suite passed 86 tests before
  native implementation. Python controller code is unchanged by this milestone.

Reproducible focused probes are preserved under `tests/live_edits/`.
Local probe artifacts are under `build/m2/gpu-edits/` and `build/m2/`.
The preserved CPU CMake targets were subsequently built against the same HDK:
CTest passed both tests in 0.64 seconds (`build/live-edits-tests/ctest.log`).

## Regular installation

Installed payload:
`C:/Users/User/AppData/Local/MythAndLogic/HoudiniRT/s5-h22.0.429-48af10c1bed3`.

Active package:
`C:/Users/User/OneDrive/Documents/houdini22.0/packages/zz_houdinirt.json`.

All 87 payload files were hash-verified. Fourteen other package/preference
registrations were unchanged. SYNAPSE, Redshift and the remaining normal packages
remain enabled. No custom preferences or discovery-path overrides are used.

The previous payload `0.2.0-h22.0.429-4252793` is retained. Its exact package bytes
are retained beside the active package as
`zz_houdinirt.json.pre-s5-s5-h22.0.429-48af10c1bed3.disabled`.
Rollback consists of closing Houdini and restoring those bytes to the active
package path; the previous payload requires no rebuild.

## Regular Houdini GUI acceptance

`build/m2/gui-normal-01/result.json`: PASS in a fresh regular Houdini 22.0.429
process, using the installed panel/controller and normal package discovery.
The actually loaded native module matched SHA256
`48af10c1bed3e3891a1dac9d02d1a11ad519033ba01ca17a0499e9f7f8a0608a`.

- Eight viewport EXRs independently checked spatial/color changes: baseline,
  transform, hidden, shown, color, inserted, deleted and resumed combined edits.
- One renderer instance survived all edits, generations 1 through 8. No stale
  generation publication appeared. Each capture retained the same request
  generation and native user-pause state before/after export.
- Houdini's flipbook internally called Pause/Resume during capture; it did not
  recreate the delegate or create a replacement scene generation. Captures were
  taken only while unpaused. The pause-hold check did not invoke flipbook.
- The real panel paused the viewer. Multiple ordinary node-parameter edits were
  applied; presentation stayed at generation 7 for 2.25 seconds. Resume presented
  generation 8 / scene revision 12 with all final edits visible in the EXR.
  The hold began after convergence; this verifies edits waiting for Resume,
  rather than measuring pause latency during an active GPU sample.
- Panel recreation retained ownership of the intended viewer. After a second
  Karma viewer was created/focused, Off restored the first viewer's original
  Karma CPU renderer and paused state; the second viewer stayed unpaused on Karma.
- Qt labels, controller state and native renderer/pause readback agreed.

Final independent receipt: `build/m2/gui-normal-01/installed_gui_receipt.json`.
It rechecked all 87 installed file hashes and 14 preserved registrations after
the session. No VUID/validation errors appeared. The owned test process exited.
The 626 x 647 captures measured a 73-pixel move against 73.03 expected; hiding
removed all red coverage, recoloring changed 13,225 green pixels to blue, and
insertion added 3,982 magenta pixels that deletion removed. Resumed edits moved
red by -47 pixels and showed the final green and magenta objects together.

Computer-use observation selected the exact test window. A helper error limited
the visual UI inspection; no new complete-panel screenshot is claimed. Acceptance
uses the actual Qt/native state records and independently read viewport EXRs.
The pre-existing separate Houdini process was left running.
Already-open Houdini windows need a restart to load the new native DLL.

Limitations remain the documented renderer scope: constant displayColor,
untextured dome lighting, polygon meshes and color output. Native instancing,
materials/textures, other lights/AOVs and GPU accumulation/resolve are later work.
