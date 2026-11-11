# src/hdRT — the HoudiniRT render delegate

A Hydra render delegate for Houdini 22. Houdini (the Solaris viewport, or `husk` on the command line)
loads it as a renderer called **HoudiniRT**.

**Step 3 state: the delegate shell.** The delegate builds, registers, takes settings from the
RenderSettings prim, renders on a background thread on the GPU, and hands pixels back to Houdini.
What it renders is a built-in test scene, the closed-box furnace from step 2, seen through the scene's
camera. It does not read scene geometry yet. That is step 4.

## Build

The sealed exam builds it like this (from the repo root, Git Bash, `VULKAN_SDK` set):

```
cmake -S src/hdRT -B build/s3/cmake -G "Visual Studio 17 2022" -A x64 \
      -DCMAKE_PREFIX_PATH="C:/Program Files/Side Effects Software/Houdini 22.0.429/toolkit/cmake"
cmake --build build/s3/cmake --config Release
cmake --install build/s3/cmake --config Release --prefix build/plugin_stage
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

What you will see: a flat image. Inside a closed box whose walls all glow and reflect equally, every
direction looks the same, so the right answer is one colour: `E * (1 - rho^(bounces+1)) / (1 - rho)`
per channel. The exam checks that number.

## Settings

Author these on the RenderSettings prim. Houdini passes them to the delegate as render settings.

| Attribute | Type | Default | Meaning |
|---|---|---|---|
| `houdinirt:spp` | int | 64 | samples per pixel; the render is finished after this many passes |
| `houdinirt:seed` | int | 1 | seed for the per-pixel random streams |
| `houdinirt:furnace:rho` | float | 0.5 | wall albedo |
| `houdinirt:furnace:emission` | color3f | (1, 1, 1) | wall emission |
| `houdinirt:bounces` | int | 16 | diffuse bounces after the first hit (renamed from `houdinirt:furnace:bounces` in s4; the default stays outside the s4 exam's drawn set 6, 7, 9..12) |

For each value the delegate uses, in this order: the render setting, else the JSON file named by the
environment variable `HOUDINIRT_S3_CONFIG` (keys `seed`, `spp`, `rho`, `emission`, `bounces`), else the
default. The log says which source was used.

## Environment variables

| Variable | Effect |
|---|---|
| `HOUDINIRT_VALIDATION=1` | load the Vulkan validation layer and print everything it reports |
| `HOUDINIRT_DEBUG=1` | print what Houdini hands the delegate: settings, camera, AOV buffers |
| `HOUDINIRT_S3_CONFIG=<file>` | fallback source for the five furnace values (see above) |
| `HOUDINIRT_DEBUG_OPEN_FACE=0..5` | leave one wall out of the box: 0 z=0, 1 z=1, 2 y=0, 3 y=1, 4 x=0, 5 x=1 |
| `HOUDINIRT_ALLOW_SOFTWARE_DEVICE=1` | accept a CPU Vulkan driver. For tests on machines without a GPU only |

`HOUDINIRT_DEBUG_OPEN_FACE` is the check that rays are really traced. With a wall missing the image
stops being flat: the hole is black, in the place the camera should see it, and the rest gets darker.

## What it prints

One line each, on stderr, prefixed `HoudiniRT: `. The exam reads the first four.

```
HoudiniRT: validation layer active                       only if the layer really loaded
HoudiniRT: seed=<seed> spp=<spp> device=<GPU name>
HoudiniRT: render thread start
HoudiniRT: render thread stop
HoudiniRT: furnace rho=... emission=(...) bounces=..., WxH, values from <source>
HoudiniRT: Validation Error: [ id ] ...                   anything the validation layer objects to
HoudiniRT: ERROR: ...                                     a failure; the render ends, Houdini does not crash
```

Under Houdini 22.0.429 one validation *warning* appears each time the GPU device is created:
`WARNING-vkGetDeviceProcAddr-device ... vkGetPhysicalDeviceVideoEncodeQualityLevelPropertiesKHR`. It
names a function this delegate never asks for. It comes from the Vulkan loader Houdini ships meeting a
newer validation layer. It is not an error and the exam does not treat it as one.

`GetRenderStats()` returns `hrt_seed`, `hrt_spp`, `hrt_device`, `hrt_settings_source`,
`hrt_validation_active`, `hrt_validation_errors`, `numCompletedSamples`, `percentDone`,
`totalClockTime`. `husk` writes each of them into the EXR header.

## Files

| File | What it is |
|---|---|
| `CMakeLists.txt`, `plugInfo.json` | build, install, and the plugin registration Houdini discovers |
| `rendererPlugin.*` | the class Hydra loads; creates the delegate |
| `renderDelegate.*` | prim types, render pass, settings, stats, pause and resume, owns the render thread |
| `renderPass.*` | runs on Houdini's thread; restarts the background render when camera, size, buffers or settings change. Never renders, never waits |
| `renderBuffer.*` | the CPU image Houdini reads back |
| `renderParam.h` | lets a prim stop the render thread before it changes something |
| `renderer.*` | what the render thread runs: one sample per pixel per pass, pause point, stop point, write the average |
| `settings.*` | the five furnace values and where they come from |
| `vkFurnace.*` | all the Vulkan: instance, device, acceleration structure, pipeline, dispatch, teardown. No USD in it |
| `shaders/furnace.comp` | the compute kernel (GLSL, inline ray queries) |
| `log.h` | the one function everything prints through |
| `test/furnace_standalone.cpp` | the kernel without Houdini (below) |
| `NOTICE.md` | what was adapted from where, and under which licence |

## The kernel on its own

`test/furnace_standalone.cpp` links `vkFurnace.cpp` and Vulkan and nothing else. It renders the furnace
with a camera at the centre of the box and prints each channel's mean next to the analytic value. It is
not built with the plugin. From a Visual Studio x64 prompt in `src/hdRT/test`:

```
glslangValidator -V --target-env vulkan1.2 -S comp ..\shaders\furnace.comp -o furnace.spv
cl /std:c++20 /O2 /EHsc /D_CRT_SECURE_NO_WARNINGS /I"%HFS%\toolkit\include" /I.. furnace_standalone.cpp ..\vkFurnace.cpp ^
   /link /LIBPATH:"%HFS%\custom\houdini\dsolib" vulkan-1.lib
furnace_standalone furnace.spv --rho 0.7 --bounces 32
furnace_standalone furnace.spv --open-face 3 --fov 100
```

On the RTX 4090 it reports about 0.2 ms per pass at 256x256 and 2 ms at 1920x1080, a constant image
equal to the analytic value to seven digits, and no validation messages at all.

## Not done yet

- Scene geometry, materials and lights (step 4). The delegate declares no rprim types.
- Pause and resume are implemented through `HdRenderThread` and checked by the exam only by reading the
  source. Nobody has pressed pause in the Solaris viewport yet. That check is owed before step 4.
- AOVs other than colour (depth, ids, normals) are cleared and never written.
- The running average is copied to Houdini's buffer on the CPU after every pass. Fine at this size, to
  be revisited when the viewport runs at full resolution.
