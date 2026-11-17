# Supplementary GPU edge regressions

The native probe uses the production Vulkan backend with validation enabled. The
separate Python judge calculates plane and sphere intersections, inverse-transpose
normals, projected depth and camera-ray distances analytically. It checks each
guide pixel, integer IDs, actual beauty coverage/emission, normal-only upload
counters and invalid-input recovery. Shared triangle edges and beauty pixels that
cross emitter silhouettes have explicitly bounded exclusions; guide silhouette
coverage is still checked everywhere.

Cases include hard authored corner-normal seams and coarse polygon IDs; a normal
edit with no geometry upload, BLAS or TLAS work; nonuniform and reflected object
transforms; perspective and rotated cameras; finite lights with no mesh; black
zero-intensity emitters; diffuse-zero camera emission; malformed normal counts;
recovery and an empty scene. These checks complement CPU Hydra-translation tests
and installed Houdini acceptance; they do not replace either.

After building the renderer shaders, prepare in a fresh output directory from the
repository root. This copies and hashes the backend, shaders, probe and independent
judge **before** output exists, and compiles without creating a GPU device:

```powershell
python tests/reliable_lighting/gpu_edges/prepare.py build/m5/edges/run-01
```

Windows defaults are VS2022 Build Tools and Vulkan SDK 1.4.341.1. Override
`--vcvars`, `--vulkan-sdk` and `--shader-dir` as needed. Once the GPU is available:

```powershell
cmd /d /c build\m5\edges\run-01\run.cmd
python build/m5/edges/run-01/inputs/probe/judge.py build/m5/edges/run-01
```

The output preserves frozen inputs, compiler/validation logs, raw guides/beauty,
cost counters, malformed-input evidence, teardown validation and `judge.json`.
Use a new directory for every attempt; preserve failed runs.

Verified on 2026-11-17 with MSVC 19.44, Vulkan SDK 1.4.341.1 and an RTX 4090:
`build/m5/edges/run-02/judge.json` passed all 14 cases with no Vulkan validation
errors or warnings, including teardown. Run 01 was a compile-only wrapper failure
(missing copied `log.h`); it never created a GPU device. Run 02's native process
returned 0 and its independent judge passed. A subsequent wrapper-only correction
adds whitespace before CMD output redirection so `run_exit.txt` records zero;
the frozen run 02 inputs and outputs remain unchanged.
