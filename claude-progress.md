# Progress

## 2026-11-16 — scripted camera-edit delay repair

- Diagnosed the reproduced four-second delay in Houdini 22.0.429's native
  infinite reference-floor label builder, independently of renderer GPU work.
- Integrated controls v0.2.1 temporarily hide the owned viewer's reference floor.
  Preserve individual split-grid flags across Houdini's deferred broadcast on
  hide and restore; keep camera, render settings and other viewers untouched.
  Off and panel-closed external takeover restore the floor; manual Show remains
  the artist's choice. Partial cleanup stays retryable without blocking renderer Off.
- Installed `camera-edits-h22.0.429-1c4d6bfea15c` in the normal preferences:
  94 payload files verified, 15 surrounding registrations unchanged, rollback kept.
  431 runtime tests pass (one Windows-inapplicable skip); focused 140 pass.
- Regular GUI and independent pixel/log audit PASS: 20 camera edits at
  71.68 / 87.51 ms p50 / p95 versus 4,652 / 4,253 ms floor-shown controls.
  Matched-pose RGBA exact; expected camera pixel shift; active pause ACK 4.251 ms,
  eight-second no-work hold and same-generation Resume. Mixed grid flags,
  second-viewer isolation, manual override and original paused Karma restoration pass.
- Native renderer/shaders unchanged; 30 source files and eight sealed inputs
  rehashed against the prior passing gate. Scope, host restoration cost and
  evidence: `docs/camera-edit-repair-2026-11-16.md`.

## 2026-11-16 — GPU accumulation, batching and resolve (Phase 2B)

- The existing target viewer, Enable, Pause/Resume and Off restoration remain
  the integrated artist workflow. No Python controller changes or new artist
  controls. Running sums and resolved RGBA live in device-local buffers; a
  dedicated GPU resolve averages scene-linear RGB and coverage alpha.
- Batches preserve absolute sample indices and sequential accumulation. Each
  request starts with one sample, then adapts toward 8 ms within 1–32 samples.
  Readback occurs for first/final images, approximately 30 Hz presentation and
  pending samples at Pause. Generation checks reject superseded publications.
- Render-thread safe-point pause acknowledgement and separate requested,
  active, GPU-frame and presented identities expose actual progress. Counters
  report trace/resolve submissions, readbacks/bytes and actual memory placement;
  timings are host submit-to-fence wall time, not GPU timestamps.
- Unchanged sealed s5/s4 PASS: nonce `6981375414681118428`, s4 nonce
  `828888709215242714`; source/exam hashes unchanged. All 22 live/fresh images
  exact; s4/Karma FLIP p99 0.0247 < 0.045. Vulkan validation silent.
  No sealed s6 exam exists; Phase 2B acceptance uses supplementary checks.
- New 26-state GPU probe and independent judge PASS for batching, resolve,
  analytic HDR/alpha, counters, reset/resize, invalid-call recovery and empty
  scenes. Existing 24-state GPU edit compatibility probe also PASS.
- Controlled three-repeat comparison against the previous installed renderer:
  all six candidate RGBA images exactly equal baseline. Native renderer median
  0.392 → 0.196 s at 384×216/1024 spp and 1.215 → 0.249 s at 1920×1080/256 spp.
  HD uses 13 trace submissions and 3 readbacks; these are scene-specific results.
- Tested payload installed as `gpu-accum-h22.0.429-21fdae8ebf43`; 91 payload
  files verified and 15 surrounding registrations unchanged. Both SPIR-V
  shaders ship together; prior payload and exact package bytes remain available.
- Combined regular GUI acceptance PASS: unfinished render paused at 12,575 of
  65,536 samples, acknowledged in 10.478 ms, held without GPU work for 30.008 s,
  then resumed the same generation. Nine independent viewport EXRs prove live
  and paused edits; panel recreation, viewer isolation and original paused
  Karma restoration passed. Actual mouse navigation also changed the image.
- All 60 edit timings are retained, including a roughly 3.7 s host event-loop
  gap for scripted bound-camera edits despite short native publication times.
  A same-fixture three-edit comparison reproduced the delay before this change
  (median observation 4.104 s baseline / 3.934 s candidate). Its host cause remains
  unresolved; no claim of general realtime viewport latency. Detailed component scopes,
  harness corrections and evidence: `docs/gpu-accumulation-verification-2026-11-16.md`.

## 2026-11-16 — live scene edits (Phase 2A / s5)

- One integrated HoudiniRT artist workflow remains: existing target viewer,
  Enable, Pause/Resume and previous-renderer restoration. No Python controller
  changes in this milestone; its focused 86-test regression passed.
- Immutable value-compared object-space mesh snapshots replace full-scene
  flattening. Device-local per-mesh buffers/BLASes persist; transforms and
  visibility change TLAS instances, color changes a 48-byte table row, and
  geometry changes touch only the changed mesh. Exact world census is cached.
- Execute publishes the latest complete request without Vulkan or waiting for
  rendering. Detached image storage and generation-checked swaps prevent stale
  publication while mapped readers retain stable pixels. Unpresented edit work
  survives cancellation in the actual operation counters.
- Sealed s5 GREEN: nonce `12094041924750763570`, s4 nonce
  `317460601411944871`, unchanged source/exam hashes. All 22 live/fresh image
  comparisons were exact. Median edit latency 0.8874/0.9171 ms at 1M/4M;
  exact operation/upload counters and device-local placement passed. s4/Karma
  FLIP p99 0.0249 < 0.045, level error 0.00056; Vulkan validation silent.
- Additional GPU probe: 24 states passed, including insertion/deletion, path
  reuse, empty/failure recovery, mirrored transforms and singular-transform
  recovery. CPU snapshot and concurrent mapped-buffer publication probes passed.
  Reproducible probes are under `tests/live_edits/`.
- Exact tested payload installed as `s5-h22.0.429-48af10c1bed3`; 87 files
  verified, 14 other registrations unchanged, old version/package retained.
  Regular GUI acceptance PASS: eight viewport EXRs checked edit visibility,
  insertion/deletion and combined paused edits after Resume. One delegate,
  generations 1–8; pause held generation 7 for 2.25 seconds. Panel recreation,
  target isolation and restoration to originally paused Karma CPU passed.
- Evidence and scope: `docs/live-edits-verification-2026-11-16.md`.
  Phase 2B/s6 GPU accumulation, batching and resolve remain the next milestone.

## 2026-11-16 — integrated viewer controls

- Combined the renderer integration baseline with the consolidated Python
  extension in one working branch. The primary HoudiniRT action now controls
  the selected Solaris viewer; assistant diagnostics remain optional.
- Added one session controller shared by panel, shelf, radial and node:
  native state readback, nonblocking pause/resume confirmation, per-viewer
  ownership, previous renderer/pause restoration, external takeover handling,
  bounded errors and retry after partial restoration. No load/cook activation.
- Actual Houdini 22.0.429 desktop smoke PASS: enable, pause, panel recreation,
  resume, two-viewer isolation, then restoration to originally paused Karma
  CPU. Fresh unchanged renderer built with the matching HDK; source/artifact
  hashes and GUI receipts are in `build/m1-viewer/` (local, ignored).
- Extension suite: Python 3.14 **377 passed, 1 skipped**; Python 3.12
  **360 passed, 18 skipped**. Updated artist documentation and v0.2.0 release
  metadata. Native renderer state is verified; pixel fidelity, convergence,
  installed-user-preference discovery and radial/HUD GUI interaction retain
  their separate verification scope. No sealed exam or renderer source edited.

The earlier renderer milestone notes below are retained as history.

## Done
- Step 1 code written: `src/furnace/furnace.comp`, `src/furnace/furnace_host.cpp`. Passes on Mesa llvmpipe (software Vulkan) with seeds 1, 7, 12345, validation clean, independent judge exit 0.
- **s1-furnace-4090 GREEN.** Builds under MSVC (`cl /std:c++17` via `vcvars64`, `glslangValidator -DFURNACE_TEX_LUTS`) and passes `verify/s1_furnace.sh` on the RTX 4090 (discrete GPU, type 2), validation layer silent (0 errors/warnings), independent judge exit 0. Verified locally across seeds 1, 23070, 999999: 72 rows, ≤8 byte-identical to the CPU reference (≤16 guard), largest |gpu−cpu| ≈ 0.006 within 3σ+0.001, MIS consistency < 1e-3.

- **s2-rayquery GREEN.** `src/furnace/rayq_host.cpp` builds two BLASes (unit cube, occluder triangle) and one TLAS with instance masks 0x1 / 0x2; `src/furnace/rayq.comp` does hit distance, shadow blocking and a closed-box furnace with inline ray queries. Passes the sealed `verify/s2_rayquery.sh` on the RTX 4090 (discrete GPU, type 2), validation silent, independent judge exit 0. Verified locally across seeds 12345, 7, 999999: 24 hit rows to 1e-4, occlusion exact vs analytic barycentric test, furnace within 1% + 3σ of E/(1−ρ). BLAS/TLAS sequence adapted from `third_party/sascha_willems_vulkan` (MIT).

- **s3-delegate-shell GREEN.** Written by Claude, by hand, at Joe's request; not by the night loop. `src/hdRT/` is a Hydra render delegate for Houdini 22.0.429: plugin + delegate + render pass + render buffer ported from OpenUSD hdEmbree (TOST), the step 2 Vulkan ray-query kernel refactored into `vkFurnace.*`, one compute shader `shaders/furnace.comp`. `husk -R HdRTRendererPlugin` renders the closed-box furnace on the RTX 4090 on an HdRenderThread, validation layer active with zero errors, parameters taken from the RenderSettings prim (5 of 5), image constant at the analytic value to half-float precision (worst 4.3e-4 relative), alpha 1. Open-wall controls through husk (`HOUDINIRT_DEBUG_OPEN_FACE`) put a black hole where the camera should see it. Passes the sealed `verify/s3_delegate.sh` on the RTX 4090. Stage 7 of that exam was amended and sealed again on 2026-10-06 on the owner's go: the first version demanded an EXR attribute, `husk:render_stats`, which husk writes only for delegates that have a `UsdRenderers.json` entry (Karma). How to build and run: `src/hdRT/README.md`.

## Current delta
- s3 is done. It was put on its branch by hand (Claude, on the owner's go, 2026-10-06) and merges only on the loop's trusted check. The Generator and the Scribe did not run for it; its lessons are in `DECISIONS.md` under 2026-10-06.
- The Solaris live-pause check owed since s3 was done by Joe on 2026-11-11 (DECISIONS.md).
- **s4-first-light in progress** (Claude Code session, branch `claude/s4-first-light`, worktree `_work/s4-claude`; Joe seals the exam, frozen sha256 5bd6027f…f3d4). Done: **C2** `HdRTMesh` (`mesh.*`: points, topology, transform, visibility, constant displayColor; `HdMeshUtil::ComputeTriangleIndices`; `Hd_SmoothNormals` always; points/topology mismatch published empty with a warning; instanced meshes warn) and **C3** `HdRTLight` dome (`light.*`: radiance = intensity * 2^exposure * color), both publishing into the CPU-side `HdRTScene` (`scene.h`) carried by the render param. Against the frozen exam (real teapot): stages [0]-[6] pass; [7] stops on `hrt_bounces`. A debug husk run logs 2,482 triangles, the exact albedos and the dome radiance equal to the exam's truth to 6 digits.
- Done: **C1** `houdinirt:bounces` (default 16, outside the drawn 6, 7, 9..12; the old `houdinirt:furnace:bounces` is gone) reported as `hrt_bounces`; **C4** `world.*` flattens the `HdRTScene` snapshot in Execute (positions by the transform in double, normals by its inverse transpose, mirrored transforms re-wound, per-triangle albedo, summed dome radiance) and the census goes out through GetRenderStats (`hrt_mesh_count`, `hrt_triangle_count`, `hrt_bbox_*`, the box only when there is one). Re-flattens only when a mesh or light changes (content version), not on a viewport resize. Against the frozen exam: [7] passes hrt_bounces and the census exactly (2 meshes, 2,482 triangles, box error 0) and stops at the 2x-dome linearity check: the GPU still draws the step 3 furnace.
- Done: **C5** (`9e5c87e`) scene upload on the render thread (one BLAS + identity TLAS from the flattened mesh, 7 descriptor bindings, no dispatch without a scene) and the Lambertian path kernel under the uniform dome (`shaders/furnace.comp`); the step 3 furnace and its rho/emission/open-face settings are retired. **The frozen s4 exam passes on 8 of 8 nonces** (exit 0, validation silent): FLIP p99 0.0249-0.0444 against the 0.045 ceiling, level within 0.14%, alpha difference 0.00005-0.00007. Karma against itself measured 0.013-0.024, so the margin is thin: the worst 1% sits inside the surfaces near the image centre on every nonce.
- Done: **C6** (`2667291`, `41f60b1`). Noise floor and A/B on nonce 101 with the exam's own FLIP: the delegate's noise is below Karma's (two seeds: FLIP 0.0159, rel RMS 0.0118 vs Karma's 0.0179 / 0.0187), so the thin margin was a bias, not noise; mirroring below-the-face samples back up added +0.1% and about 2% per-pixel residual. The kernel now ends such paths (about 1% residual left, level within 0.05%; DECISIONS.md). No stratification needed. Frozen exam on 8 nonces: all PASS, FLIP p99 0.019-0.031 (was 0.025-0.044), level <= 0.11%. Reviewer minors fixed (pause before upload, early stop, device limits, memory with fallback, harness ported to an analytic convex-cube check that passes, README).
- Next: C7 provenance, then C8 landing with land_step.mts (the exam is sealed, d4a9c9d).

## Parked ideas (one line each)
- s4: the flatten ignores render tags (proxy/guide); a viewport purpose toggle does not re-flatten. Record GetRenderTag() per mesh and filter in Execute (viewport work).
- s3: a headless pause test (C++ against the HDK: render index, the delegate, Pause, watch `numCompletedSamples` stop) would replace the human viewport check.
- s3: the running average is copied to the Hydra buffer on the CPU after every pass; move to a mapped device image when the viewport runs at full resolution.
- s3: depth, primId and normal AOVs are cleared, never written. They need real geometry (s4).
