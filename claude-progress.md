# Progress

## Done
- Step 1 code written: `src/furnace/furnace.comp`, `src/furnace/furnace_host.cpp`. Passes on Mesa llvmpipe (software Vulkan) with seeds 1, 7, 12345, validation clean, independent judge exit 0.
- **s1-furnace-4090 GREEN.** Builds under MSVC (`cl /std:c++17` via `vcvars64`, `glslangValidator -DFURNACE_TEX_LUTS`) and passes `verify/s1_furnace.sh` on the RTX 4090 (discrete GPU, type 2), validation layer silent (0 errors/warnings), independent judge exit 0. Verified locally across seeds 1, 23070, 999999: 72 rows, ≤8 byte-identical to the CPU reference (≤16 guard), largest |gpu−cpu| ≈ 0.006 within 3σ+0.001, MIS consistency < 1e-3.

- **s2-rayquery GREEN.** `src/furnace/rayq_host.cpp` builds two BLASes (unit cube, occluder triangle) and one TLAS with instance masks 0x1 / 0x2; `src/furnace/rayq.comp` does hit distance, shadow blocking and a closed-box furnace with inline ray queries. Passes the sealed `verify/s2_rayquery.sh` on the RTX 4090 (discrete GPU, type 2), validation silent, independent judge exit 0. Verified locally across seeds 12345, 7, 999999: 24 hit rows to 1e-4, occlusion exact vs analytic barycentric test, furnace within 1% + 3σ of E/(1−ρ). BLAS/TLAS sequence adapted from `third_party/sascha_willems_vulkan` (MIT).

- **s3-delegate-shell GREEN.** Written by Claude, by hand, at Joe's request; not by the night loop. `src/hdRT/` is a Hydra render delegate for Houdini 22.0.429: plugin + delegate + render pass + render buffer ported from OpenUSD hdEmbree (TOST), the step 2 Vulkan ray-query kernel refactored into `vkFurnace.*`, one compute shader `shaders/furnace.comp`. `husk -R HdRTRendererPlugin` renders the closed-box furnace on the RTX 4090 on an HdRenderThread, validation layer active with zero errors, parameters taken from the RenderSettings prim (5 of 5), image constant at the analytic value to half-float precision (worst 4.3e-4 relative), alpha 1. Open-wall controls through husk (`HOUDINIRT_DEBUG_OPEN_FACE`) put a black hole where the camera should see it. Passes the sealed `verify/s3_delegate.sh` on the RTX 4090. Stage 7 of that exam was amended and sealed again on 2026-10-06 on the owner's go: the first version demanded an EXR attribute, `husk:render_stats`, which husk writes only for delegates that have a `UsdRenderers.json` entry (Karma). How to build and run: `src/hdRT/README.md`.

## Current delta
- s3 is done. It was put on its branch by hand (Claude, on the owner's go, 2026-10-06) and merges only on the loop's trusted check. The Generator and the Scribe did not run for it; its lessons are in `DECISIONS.md` under 2026-10-06.
- Owed before s4 (human): press pause and resume in the Solaris viewport with HoudiniRT selected. The exam checks pause only by reading the source.
- Next milestone per build order: **s4-first-light** (mesh and camera sync, Teapot under a dome light vs Karma CPU). Its exam is not sealed yet. Start from `src/hdRT/`: the delegate declares no rprim types yet, and `vkFurnace.*` traces a hardcoded box.

## Parked ideas (one line each)
- s3: a headless pause test (C++ against the HDK: render index, the delegate, Pause, watch `numCompletedSamples` stop) would replace the human viewport check.
- s3: the running average is copied to the Hydra buffer on the CPU after every pass; move to a mapped device image when the viewport runs at full resolution.
- s3: depth, primId and normal AOVs are cleared, never written. They need real geometry (s4).
