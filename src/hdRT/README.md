# src/hdRT — the HoudiniRT render delegate

A Hydra render delegate for Houdini 22. Houdini (the Solaris viewport, or `husk` on the command line)
loads it as a renderer called **HoudiniRT**.

**Step 4 state: first light.** The delegate syncs polygon meshes (points, topology, transform,
visibility, constant `displayColor`) and one untextured dome light from Hydra, flattens them to one
world-space mesh, and path-traces it on the GPU with Vulkan ray queries: Lambertian surfaces, the dome
lighting every path that leaves the scene, never visible to the camera. Not yet: materials, textures,
other lights, instancing, subdivision, AOVs other than colour.

## Build

The sealed exam (`verify/s4_firstlight.sh`) builds it like this (from the repo root, Git Bash,
`VULKAN_SDK` set):

```
cmake -S src/hdRT -B build/s4/cmake -G "Visual Studio 17 2022" -A x64 \
      -DCMAKE_PREFIX_PATH="C:/Program Files/Side Effects Software/Houdini 22.0.429/toolkit/cmake"
cmake --build build/s4/cmake --config Release
cmake --install build/s4/cmake --config Release --prefix build/plugin_stage
```

`build/plugin_stage` then holds `plugInfo.json`, `hdRT.dll` and `shaders/furnace.spv`. Nothing is
written anywhere else. The Vulkan SDK is needed only for `glslangValidator`; headers and the import
library for Vulkan come from the Houdini install.

## Run

```
set PXR_PLUGINPATH_NAME=<repo>\build\plugin_stage
husk -R HdRTRendererPlugin -r 256 256 -o out.exr stage.usda
```

In Houdini, start it with the same `PXR_PLUGINPATH_NAME` and pick **HoudiniRT** in the Solaris
viewport's renderer menu. (The viewport has not been exercised yet; `husk` has.)

What you will see: the meshes, Lambert-shaded by `displayColor` under the dome, on a transparent
background (the dome lights the scene but is not drawn). `verify/s4_firstlight.sh` checks the census,
linearity in the light, and the picture against Karma CPU.

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

## Files

| File | What it is |
|---|---|
| `CMakeLists.txt`, `plugInfo.json` | build, install, and the plugin registration Houdini discovers |
| `rendererPlugin.*` | the class Hydra loads; creates the delegate |
| `renderDelegate.*` | prim types, render pass, settings, stats, pause and resume, owns the render thread |
| `renderPass.*` | runs on Houdini's thread; restarts the background render when camera, size, buffers or settings change. Never renders, never waits |
| `renderBuffer.*` | the CPU image Houdini reads back |
| `renderParam.h` | lets a prim stop the render thread before it changes something; carries the scene |
| `mesh.*` | the mesh rprim: triangulates with `HdMeshUtil`, smooth normals with `Hd_SmoothNormals` |
| `light.*` | the dome light sprim: radiance = intensity * 2^exposure * color |
| `scene.h` | the CPU-side scene prims publish into during Sync |
| `world.*` | flattens the scene to one world-space mesh in Execute, and takes the census |
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
- Pause and resume are checked by the exam only by reading the source; Joe checked live pause in the
  Solaris viewport on 2026-11-11 (DECISIONS.md).
- AOVs other than colour (depth, ids, normals) are cleared and never written.
- The running average is copied to Houdini's buffer on the CPU after every pass. Fine at this size, to
  be revisited when the viewport runs at full resolution.
