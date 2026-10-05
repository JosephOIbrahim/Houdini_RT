# Progress

## Done
- Step 1 code written: `src/furnace/furnace.comp`, `src/furnace/furnace_host.cpp`. Passes on Mesa llvmpipe (software Vulkan) with seeds 1, 7, 12345, validation clean, independent judge exit 0.
- **s1-furnace-4090 GREEN.** Builds under MSVC (`cl /std:c++17` via `vcvars64`, `glslangValidator -DFURNACE_TEX_LUTS`) and passes `verify/s1_furnace.sh` on the RTX 4090 (discrete GPU, type 2), validation layer silent (0 errors/warnings), independent judge exit 0. Verified locally across seeds 1, 23070, 999999: 72 rows, ≤8 byte-identical to the CPU reference (≤16 guard), largest |gpu−cpu| ≈ 0.006 within 3σ+0.001, MIS consistency < 1e-3.

- **s2-rayquery GREEN.** `src/furnace/rayq_host.cpp` builds two BLASes (unit cube, occluder triangle) and one TLAS with instance masks 0x1 / 0x2; `src/furnace/rayq.comp` does hit distance, shadow blocking and a closed-box furnace with inline ray queries. Passes the sealed `verify/s2_rayquery.sh` on the RTX 4090 (discrete GPU, type 2), validation silent, independent judge exit 0. Verified locally across seeds 12345, 7, 999999: 24 hit rows to 1e-4, occlusion exact vs analytic barycentric test, furnace within 1% + 3σ of E/(1−ρ). BLAS/TLAS sequence adapted from `third_party/sascha_willems_vulkan` (MIT).

## Current delta
- s2-rayquery complete and committed. Next milestone per build order: **s3-delegate-shell** (HdRenderDelegate plugin for Houdini 22 renders the kernel's image on an HdRenderThread; husk writes EXR; pixel-equivalent to the standalone kernel).

## Parked ideas (one line each)
