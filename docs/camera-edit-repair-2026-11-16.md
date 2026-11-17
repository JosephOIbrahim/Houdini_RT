# Scripted camera-edit repair — Houdini 22.0.429

This repair keeps the existing HoudiniRT target viewer, Enable, Pause/Resume and
Off workflow. In the affected Houdini build, Enable temporarily hides that
viewer's reference floor. Off restores its original visibility. The session
controller also restores the floor after an external renderer switch, including
when the panel is closed. An artist who manually shows the floor keeps that
choice. No camera, render setting, grid spacing or other viewer is changed.

This is a scoped workaround for a native Houdini viewport problem, not a change
to HoudiniRT's GPU renderer. It applies only to build 22.0.429 and to renderer
sessions enabled by these controls. The panel discloses the temporary grid change.

## Diagnosis

The previous milestone measured approximately four seconds from a scripted bound
USD camera edit to observation of the current image. Native request-to-first-AOV
time was about 16 ms. The same delay occurred with the preceding renderer build.

Tests used the same orthographic camera fixture, 626 × 647 viewport and normal
preference copies. The fixture's USD clipping range is the default (1, 1,000,000).
These results characterize this reproduction, not every camera or scene.

- Removing the redraw fence, explicit draw, or both did not remove the delay:
  three measured edit observations were 3,867 / 3,685 / 3,792 ms.
- Houdini PerfMon located about 3.6 seconds in CPU `persp1/{Other}` drawing.
  USD updates, cooking, GPU drawing and GPU waits were only milliseconds.
- An independent native main-thread stack sampler found 115 of 133 samples in
  `dm_LabelGeometryVK::build`, called through `DM_InfiniteFloor::renderLabels`
  and `DM_FloorInterface::renderLabels`. Sampling instrumentation is diagnostic;
  its timings are excluded from the performance comparison.
- The stall also occurred with HoudiniRT paused and with Karma selected. It is
  therefore not explained by waiting for HoudiniRT's current GPU batch.
- Hiding reference-grid numbers, orthographic rulers, or both did not avoid the
  expensive build. Hiding the reference plane did: one controlled observation
  fell from 4,904 ms with both label modes hidden to 95 ms with the floor hidden.

An optional diagnostic of fixture clipping did not complete a valid comparison;
no conclusion is drawn from that phase. Camera clipping is not
part of the repair. Raw attempts are retained, including incomplete diagnostics.

Installed acceptance exposed an additional host side effect: setting reference
visibility broadcasts to the four split viewports' orthographic grid flags after
redraw. Immediate readback does not reveal it. Native renderer selection alone
does not change these flags. A mixed Quad layout loses its mixed visibility after
a plain reference-plane False/True roundtrip, so the first candidate was rejected
and the normal package pointer was temporarily restored to the previous version.

A separate native probe tested the necessary distinction: hiding just the current
viewport's orthographic grid still took 3,904 / 3,896 / 4,066 ms. Hiding the
reference floor, yielding, then restoring every original orthographic grid flag
took 57.891 / 56.071 / 55.880 ms. Restoring the reference floor brought back a
4,068 ms update. All seven observations required new current-generation AOVs and
camera-pose changes with unchanged geometry content. This is the basis for
preserving split-grid flags across the reference-plane redraw in the final fix.

The ordering probe verified `postRedrawFence()` followed by a one-shot
`postEventCallback()`: the callback saw the native broadcast and restored the
mixed vector in both directions. Three further camera edits took 59.187 /
64.146 / 59.870 ms with that vector intact. Restoring the floor took the original
host draw path again (about 3.6 seconds); Off therefore remains pending until
native redraw and split-setting readback finish. The fix does not make Houdini's
floor renderer faster when an artist elects to show it.

## References and evidence

The local SideFX snapshot at
`D:/Houdini_AI_Reference/upstream_sidefx/snapshot-2026-10-04/docs/hom/hou/ReferencePlane.md`
documents a viewer-scoped infinite plane and its visibility API. `SceneViewer.md`
documents `referencePlane()`. `GeometryViewportSettings.md` and
`viewportGridRuler.md` distinguish orthographic rulers from the reference plane.
The corresponding official documentation is
[ReferencePlane](https://www.sidefx.com/docs/houdini/hom/hou/ReferencePlane.html)
and [viewrefplane](https://www.sidefx.com/docs/houdini/commands/viewrefplane.html).
Only HOM visibility is needed in the repair; it does not execute HScript.

Raw diagnostic evidence is under this worktree's ignored `build/m4/camera-profile/`:
`profile-01`, `profile-02`, `grid-01` and `floor-01`. Each run records its exact
script, loaded delegate, preferences and camera state. PerfMon profiles and native
stack logs localize the stall independently of the renderer's own timing.

## Release verification

Version 0.2.1 is installed through the existing normal
`houdini22.0/packages/zz_houdinirt.json` registration, pointing to
`C:/Users/User/AppData/Local/MythAndLogic/HoudiniRT/camera-edits-h22.0.429-1c4d6bfea15c`.
The previous GPU-accumulation payload and exact rollback package remain intact.
All 94 installed files and 15 surrounding registrations were rehashed after the
GUI acceptance run; the registrations are unchanged. There are no discovery or
source-path overrides in the test launch.

Full Python 3.14 runtime suite: **431 passed, 1 skipped**. Python 3.12 suite:
414 passed, 18 skipped and 18 subtests passed; 17 skips require the external
sidecar's Python 3.14 ABI and are covered by that runtime run. The remaining skip
concerns POSIX permission bits on Windows. The focused controller/surface suite
passed 140 cases. Release structure and embedded panel-script parity checks pass.

Final GUI run: `build/m4/installed/gui-normal-04/`, normal Houdini 22.0.429, owned
PID 26420. The application loaded the exact installed controls and renderer.
Native UI observation confirmed the intended `panetab1`, HoudiniRT, Paused,
Resume/Off controls and the temporary reference-grid explanation.

At 626 × 647, the same-session camera controls and repaired measurements were:

| Measurement | Edit to observed current AOV |
|---|---:|
| Reference floor shown, before repaired sequence | 4,652.44 ms |
| Reference floor hidden, 20 edits, p50 / p95 | 71.68 / 87.51 ms |
| Reference floor hidden, maximum | 89.18 ms |
| Reference floor shown again after image checks | 4,253.01 ms |

Quantiles use nearest rank. Native request-to-first-publication p50 / p95 was
13.02 / 21.63 ms, and maximum polling-gap p50 / p95 was 27.61 / 33.96 ms.
Observation latency includes log polling; it is not physical display latency or
a general benchmark across production scenes. The two slow controls reproduce
the same floor-dependent stall before and after the repaired sequence.

The independent judge reopened all three viewport EXRs. The same camera pose
produced **exactly equal RGBA arrays** with the reference floor hidden and shown.
Moving the camera by 0.2 units shifted both colored objects by approximately
20.51 pixels, against a 20.87-pixel projection expectation. Camera clipping,
projection, aperture, focal length and camera binding remained unchanged.

Pause acknowledged at 12,511 / 65,536 samples in 4.251 ms, held for 8.002 seconds
without batches or publications, then resumed the same generation to 12,543
samples. Mixed split-grid visibility survived activation, camera edits, pause and
Off. Manual global Show retained Houdini's native behavior. The second viewer
stayed untouched. Closing the panel and switching renderers externally restored
the floor and mixed flags without a controller-status call; reopening the panel
correctly reported Off with no ownership. Original paused Karma CPU and startup
grid flags were restored, and the owned process exited.

The independent audit reparsed 5,152 native events, checked sample continuity,
current-generation publication and pause holds, and found no Vulkan validation
errors. The native DLL and both SPIR-V files are byte-identical to the preceding
verified GPU build. All 30 native source files and eight sealed inputs also match
that build; its sealed gate is reused, not represented as a newly run native exam.

Receipts in the final run directory:
`result.json`, `independent_judge.json`, `installed_gui_receipt.json`,
`cua_receipt.json` and `final_receipt.json`. Executed harnesses are archived.
Earlier attempts are retained. In particular, `normal-03` passed initial pause
and mixed-grid restoration but rejected a later already-converged render; the
final harness requires unfinished samples only for the active-pause proof.
