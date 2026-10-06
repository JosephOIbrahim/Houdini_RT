# NOTICE — where the code in src/hdRT comes from

Most of this directory is new. Two parts are adapted from other people's open-source code. Their
copyright notices are kept in the files and the licence texts are in `third_party/`.

## OpenUSD hdEmbree — Pixar, Tomorrow Open Source Technology License 1.0

Source: OpenUSD 26.03, `pxr/imaging/plugin/hdEmbree`. A copy of the files used as reference, and the
licence text, are in `third_party/openusd_hdEmbree/` (`LICENSE.txt`).

| File here | Adapted from | What changed |
|---|---|---|
| `rendererPlugin.h`, `rendererPlugin.cpp` | `rendererPlugin.h/.cpp` | class renamed; creates `HdRTRenderDelegate` |
| `renderDelegate.h`, `renderDelegate.cpp` | `renderDelegate.h/.cpp` | Embree, meshes, lights and instancers removed; settings, statistics and shader lookup are new |
| `renderPass.h`, `renderPass.cpp` | `renderPass.h/.cpp` | settings resolved through the delegate; AOV buffers given by path are looked up; debug trace added |
| `renderBuffer.h`, `renderBuffer.cpp` | `renderBuffer.h/.cpp` | per-pixel sample accumulation removed; 8-bit writes clamped |
| `renderParam.h` | `renderParam.h` | Embree handles removed |
| `renderer.h`, `renderer.cpp` | `renderer.h/.cpp` | only the structure is kept (setters, AOV validation, Clear, the pass loop with its pause and stop points); the tracing is new |

Each of these files carries Pixar's original copyright header followed by a "Modified for HoudiniRT"
line.

## Vulkan examples — Sascha Willems, MIT License

Source: `examples/rayquery` and `base/VulkanRaytracingSample`, Copyright (c) 2016 Sascha Willems. The
reference files and the licence text are in `third_party/sascha_willems_vulkan/` (`LICENSE.md`).

| File here | What is adapted |
|---|---|
| `vkFurnace.cpp` | the call sequence that builds the bottom-level and top-level acceleration structures, by way of this project's step 2 host `src/furnace/rayq_host.cpp` |

## New in this project

`vkFurnace.h`, the rest of `vkFurnace.cpp`, `shaders/furnace.comp`, `settings.h`, `settings.cpp`, `log.h`,
`test/furnace_standalone.cpp`, `CMakeLists.txt`, `plugInfo.json`, `README.md`.

No code under a copyleft licence is used, linked or was consulted.
