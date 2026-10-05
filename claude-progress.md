# Progress

## Done
- Step 1 code written: `src/furnace/furnace.comp`, `src/furnace/furnace_host.cpp`. Passes on Mesa llvmpipe (software Vulkan) with seeds 1, 7, 12345, validation clean, independent judge exit 0.
- **s1-furnace-4090 GREEN.** Builds under MSVC (`cl /std:c++17` via `vcvars64`, `glslangValidator -DFURNACE_TEX_LUTS`) and passes `verify/s1_furnace.sh` on the RTX 4090 (discrete GPU, type 2), validation layer silent (0 errors/warnings), independent judge exit 0. Verified locally across seeds 1, 23070, 999999: 72 rows, ≤8 byte-identical to the CPU reference (≤16 guard), largest |gpu−cpu| ≈ 0.006 within 3σ+0.001, MIS consistency < 1e-3.

## Current delta
- s1 complete. Next milestone per build order: **s2-rayquery** (inline ray queries against a static triangle scene, host-built BLAS/TLAS, radiometry vs analytic references).

## Parked ideas (one line each)
