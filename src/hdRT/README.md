# src/hdRT — the HoudiniRT render delegate

A Hydra render delegate for Houdini 22. Houdini (the Solaris viewport, or `husk` on the command line)
loads it as a renderer called **HoudiniRT**.

**Phase 4A: bounded MaterialX/OpenPBR materials.** The delegate renders polygon meshes with
authored normals, uniform domes, distant lights and rectangle/disk/sphere lights. Whole-mesh
MaterialX OpenPBR bindings support opaque isotropic base and specular constants through Adobe's
BSDF in texture LUT mode. Unbound or unsupported materials use the diagnosed displayColor
Lambert fallback. The integrated **Check scene support** report identifies limitations by USD path.
Textures, HDRI maps, additional lobes, instancing and subdivision remain unsupported.
Beauty, normal, eye normal, depth, camera depth, primitive/element/instance IDs and base-color
diagnostics share the same publication generation; exact semantics are in `capabilities.json`.

Running sums stay in device-local GPU memory. Short batches trace consecutive samples; a separate
compute pass resolves scene-linear RGBA for presentation. Only resolved images are read back to Houdini.
Each new request starts with one sample, then adapts toward an estimated 8 ms batch duration, capped
at 32 samples. The target is an estimate: a single sample or shared GPU scheduling can take longer.
Pause takes effect at a completed batch boundary; Resume continues the same sequence unless the scene
changed. Publication remains generation-checked, and the previous image remains available between updates.

## Build

The sealed exam (`verify/s5_liveedits.sh`, including the s4 regression) builds it like this (from the repo root, Git Bash,
`VULKAN_SDK` set):

```
cmake -S src/hdRT -B build/s4/cmake -G "Visual Studio 17 2022" -A x64 \
      -DCMAKE_PREFIX_PATH="C:/Program Files/Side Effects Software/Houdini 22.0.429/toolkit/cmake"
cmake --build build/s4/cmake --config Release --parallel 1
cmake --install build/s4/cmake --config Release --prefix build/plugin_stage
```

`build/plugin_stage` then holds `plugInfo.json`, `capabilities.json`, `hdRT.dll`, both
`shaders/furnace.spv` and `shaders/resolve.spv`, and the OpenPBR license. Both shaders are required. Nothing is
written anywhere else. The Vulkan SDK is needed only for `glslangValidator`; headers and the import
library for Vulkan come from the Houdini install.

## Run

```
set PXR_PLUGINPATH_NAME=<repo>\build\plugin_stage
husk -R HdRTRendererPlugin -r 256 256 -o out.exr stage.usda
```

For an artist installation, use the unified HoudiniRT package and **HoudiniRT** panel. Enable selects
HoudiniRT in the chosen Solaris viewer; Pause/Resume uses that viewer's native render state; Off restores
its previous renderer and pause state. The native renderer menu remains available for direct selection.

Edit ordinary scene parameters while rendering. Transforms and visibility update the instance structure;
points and topology update only the changed mesh; displayColor and material constants update shading tables. Edits
made while paused remain pending until Resume. The last image stays visible until a current replacement
is ready. Insertion and deletion use the same scene handoff, without a separate toggle or restart command.

Use a Material Library with an MtlX OpenPBR Surface and an Assign Material LOP for material editing.
Supported controls and native defaults are listed in `capabilities.json`; unsupported connected
inputs or lobes fall back as a whole material. The dome lights the scene but is not drawn.
`verify/s4_firstlight.sh` retains the independent matte regression; `tests/reliable_materials`
adds material-specific physical, reference-image, live-edit and installed-workflow checks.

## Settings

Author these on the RenderSettings prim. Houdini passes them to the delegate as render settings.

| Attribute | Type | Default | Meaning |
|---|---|---|---|
| `houdinirt:spp` | int | 64 | samples per pixel; the render is finished after this many passes |
| `houdinirt:seed` | int | 1 | seed for the per-pixel random streams |
| `houdinirt:bounces` | int | 16 | diffuse bounces after the first hit (renamed from `houdinirt:furnace:bounces` in s4; the default stays outside the s4 exam's drawn set 6, 7, 9..12) |

For each value the delegate uses, in this order: the render setting, else the JSON file named by the
environment variable `HOUDINIRT_S3_CONFIG` (keys `seed`, `spp`, `bounces`), else the default. The log
says which source was used. The step 3 furnace settings (`houdinirt:furnace:*`) are gone.

## Environment variables

| Variable | Effect |
|---|---|
| `HOUDINIRT_VALIDATION=1` | load the Vulkan validation layer and print everything it reports |
| `HOUDINIRT_DEBUG=1` | print what Houdini hands the delegate: settings, camera, AOV buffers |
| `HOUDINIRT_S3_CONFIG=<file>` | fallback source for seed, spp and bounces (see above) |
| `HOUDINIRT_ALLOW_SOFTWARE_DEVICE=1` | accept a CPU Vulkan driver. For tests on machines without a GPU only |
| `HOUDINIRT_BATCH_LIMIT=1..32` | diagnostic batch cap for controlled comparisons; default 32. This is not an artist quality setting |

## What it prints

One line each, on stderr, prefixed `HoudiniRT: `, in this order. The exam reads the validation line, the
seed line, and the render thread start and stop lines.

```
HoudiniRT: validation layer active                       only if the layer really loaded
HoudiniRT: seed=<seed> spp=<spp> device=<GPU name>
HoudiniRT: bounces=..., WxH, values from <source>; scene: N triangles, dome (r, g, b)
HoudiniRT: render thread start
HoudiniRT: render thread stop
HoudiniRT: Validation Error: [ id ] ...                   anything the validation layer objects to
HoudiniRT: ERROR: ...                                     a failure; the render ends, Houdini does not crash
```

Under Houdini 22.0.429 one validation *warning* appears each time the GPU device is created:
`WARNING-vkGetDeviceProcAddr-device ... vkGetPhysicalDeviceVideoEncodeQualityLevelPropertiesKHR`. It
names a function this delegate never asks for. It comes from the Vulkan loader Houdini ships meeting a
newer validation layer. It is not an error and the exam does not treat it as one.

`GetRenderStats()` returns `hrt_seed`, `hrt_spp`, `hrt_bounces`, the census of what was synced
(`hrt_mesh_count`, `hrt_triangle_count`, and `hrt_bbox_{min,max}_{x,y,z}` in world space when there is
geometry), `hrt_device`, `hrt_settings_source`, `hrt_validation_active`, `hrt_validation_errors`,
`numCompletedSamples`, `percentDone`, `totalClockTime`. `husk` writes each of them into the EXR header.

Live-edit diagnostics add `hrt_blas_builds`, `hrt_blas_refits`, `hrt_tlas_builds`, `hrt_tlas_updates`,
`hrt_geometry_upload_bytes`, `hrt_table_upload_bytes`, `hrt_edit_ms` and `hrt_scene_memory`.
Counters measure executed work carried through cancelled requests until an image is published.
`hrt_scene_generation` and `hrt_presented_generation` distinguish the requested frame from the shown
frame. Debug logs include renderer identity and generation on each request and first/final publication.

Accumulation diagnostics add trace/resolve submissions and dispatches, traced samples, clear submissions,
readback count/bytes and allocated accumulation/resolve/readback storage. The `hrt_accumulation_memory`
and `hrt_resolve_memory` values reflect actual selected allocation flags. `hrt_last_batch_ms`,
`hrt_max_batch_ms` and resolve timings measure host submit-to-fence wall time, not GPU timestamps.
`hrt_first_image_ms` measures request-to-current-publication, distinct from `hrt_edit_ms` (first trace submit).
Active, GPU-frame and presented generations/samples identify which work each statistic describes.
Pause request/acknowledgement epochs and `hrt_pause_latency_ms` record reaching a completed-work boundary.

## Files

| File | What it is |
|---|---|
| `CMakeLists.txt`, `plugInfo.json` | build, install, and the plugin registration Houdini discovers |
| `rendererPlugin.*` | the class Hydra loads; creates the delegate |
| `renderDelegate.*` | prim types, render pass, settings, stats, pause and resume, owns the render thread |
| `renderPass.*` | runs on Houdini's thread; restarts the background render when camera, size, buffers or settings change. Never renders, never waits |
| `renderBuffer.*` | the CPU image Houdini reads back |
| `renderParam.h` | synchronizes render-buffer lifetime outside Execute; carries the immutable scene |
| `mesh.*` | the mesh rprim: triangulates with `HdMeshUtil`, smooth normals with `Hd_SmoothNormals` |
| `light.*` | the dome light sprim: radiance = intensity * 2^exposure * color |
| `scene.h` | the CPU-side scene prims publish into during Sync |
| `world.*` | combines immutable mesh records and cached exact world bounds; never flattens geometry |
| `renderer.*` | what the render thread runs: one sample per pixel per pass, pause point, stop point, write the average |
| `settings.*` | seed, spp and bounces, and where they come from |
| `vkFurnace.*` | all the Vulkan: instance, device, scene upload and acceleration structures, pipeline, dispatch, teardown. No USD in it |
| `shaders/furnace.comp` | the path-tracing kernel (GLSL, inline ray queries); the file keeps its step 3 name |
| `log.h` | the one function everything prints through |
| `test/furnace_standalone.cpp` | the kernel without Houdini (below) |
| `NOTICE.md` | what was adapted from where, and under which licence |

## The kernel on its own

`test/furnace_standalone.cpp` links `vkFurnace.cpp` and Vulkan and nothing else. It renders a flat-shaded
unit cube under the dome from outside. The cube is convex, so every fully covered pixel must read exactly
albedo * dome and every empty pixel black with alpha 0; it prints both and exits 0 only if they hold.
It is not built with the plugin. From a Visual Studio x64 prompt in `src/hdRT/test`:

```
glslangValidator -V --target-env vulkan1.2 -S comp ..\shaders\furnace.comp -o furnace.spv
cl /std:c++20 /O2 /EHsc /D_CRT_SECURE_NO_WARNINGS /I"%HFS%\toolkit\include" /I.. furnace_standalone.cpp ..\vkFurnace.cpp ^
   /link /LIBPATH:"%HFS%\custom\houdini\dsolib" vulkan-1.lib
furnace_standalone furnace.spv --albedo 0.3 0.5 0.7 --dome 1.2 0.9 0.6 --bounces 8
furnace_standalone furnace.spv --empty
```

## Not done yet

- Materials beyond a constant `displayColor`, textures, lights other than an untextured dome, instancing,
  subdivision, render tags (proxy/guide toggles in the viewport).
- AOVs other than colour (depth, ids, normals) are cleared and never written.
- The viewport still consumes a CPU render buffer, so each presented image crosses GPU-to-host memory.
  GPU texture interop, denoising and extra guide outputs are later work.

The protected sealed s5 exam retains the s4/Karma regression. There is no sealed s6 exam;
supplementary accumulation and desktop interaction checks are documented under `docs/` and `tests/`.
