# Progress

## Done
- Step 1 code written: `src/furnace/furnace.comp`, `src/furnace/furnace_host.cpp`. Passes on Mesa llvmpipe (software Vulkan) with seeds 1, 7, 12345, validation clean, independent judge exit 0.
- **s1-furnace-4090 GREEN.** Builds under MSVC (`cl /std:c++17` via `vcvars64`, `glslangValidator -DFURNACE_TEX_LUTS`) and passes `verify/s1_furnace.sh` on the RTX 4090 (discrete GPU, type 2), validation layer silent (0 errors/warnings), independent judge exit 0. Verified locally across seeds 1, 23070, 999999: 72 rows, ≤8 byte-identical to the CPU reference (≤16 guard), largest |gpu−cpu| ≈ 0.006 within 3σ+0.001, MIS consistency < 1e-3.

- **s2-rayquery GREEN.** `src/furnace/rayq_host.cpp` builds two BLASes (unit cube, occluder triangle) and one TLAS with instance masks 0x1 / 0x2; `src/furnace/rayq.comp` does hit distance, shadow blocking and a closed-box furnace with inline ray queries. Passes the sealed `verify/s2_rayquery.sh` on the RTX 4090 (discrete GPU, type 2), validation silent, independent judge exit 0. Verified locally across seeds 12345, 7, 999999: 24 hit rows to 1e-4, occlusion exact vs analytic barycentric test, furnace within 1% + 3σ of E/(1−ρ). BLAS/TLAS sequence adapted from `third_party/sascha_willems_vulkan` (MIT).

- **s3-delegate-shell: delegate written (by Claude, by hand, at Joe's request; not by the night loop).** `src/hdRT/` is a Hydra render delegate for Houdini 22.0.429: plugin + delegate + render pass + render buffer ported from OpenUSD hdEmbree (TOST), the step 2 Vulkan ray-query kernel refactored into `vkFurnace.*`, one compute shader `shaders/furnace.comp`. `husk -R HdRTRendererPlugin` renders the closed-box furnace on the RTX 4090 on an HdRenderThread, validation layer active with zero errors, parameters taken from the RenderSettings prim (5 of 5), image constant at the analytic value to half-float precision (worst 4.3e-4 relative), alpha 1. Open-wall controls through husk (`HOUDINIRT_DEBUG_OPEN_FACE`) put a black hole where the camera should see it. Against the exam as sealed on 2026-10-06 it passes stages 0 to 6 and fails stage 7: that stage demands an EXR attribute, `husk:render_stats`, which husk writes only for delegates that have a `UsdRenderers.json` entry (Karma). A stage 7 amendment is waiting for the owner's seal; with it the exam passes on every seed tried. How to build and run: `src/hdRT/README.md`.

## Current delta
- s3 code is complete. **Generator: run `bash verify/s3_delegate.sh $RANDOM` first. If it passes, change nothing in `src/hdRT/` and stop. If it fails only at stage [7] with "no husk:render_stats attribute", that is the exam defect described above, not a code fault: change nothing and stop.** The code was verified by hand on several seeds.
- Owed before s4 (human): press pause and resume in the Solaris viewport with HoudiniRT selected. The exam checks pause only by reading the source.
- Next milestone per build order: **s4-first-light** (mesh and camera sync, Teapot under a dome light vs Karma CPU). Its exam is not sealed yet.

## Parked ideas (one line each)
- s3: a headless pause test (C++ against the HDK: render index, the delegate, Pause, watch `numCompletedSamples` stop) would replace the human viewport check.
- s3: the running average is copied to the Hydra buffer on the CPU after every pass; move to a mapped device image when the viewport runs at full resolution.
- s3: depth, primId and normal AOVs are cleared, never written. They need real geometry (s4).
