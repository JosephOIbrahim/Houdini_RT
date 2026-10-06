# Reference only: OpenUSD hdEmbree (Pixar, TOST 1.0 licence; see LICENSE.txt)

Source: OpenUSD 26.03, `pxr/imaging/plugin/hdEmbree`. This is Pixar's own example Hydra render delegate and the template for this project's delegate. It does NOT build here (it needs Embree and the OpenUSD build system). Do not include or link it. Adapt its structure into `src/hdRT/`.

What to take, in the order you will need it:
- `rendererPlugin.h/.cpp` + `plugInfo.json`: how a delegate registers (HdRendererPlugin, TF_REGISTRY_FUNCTION, the plugInfo fields Hydra discovers).
- `renderDelegate.h/.cpp`: supported rprim/sprim/bprim types, CreateRenderPass, CreateBprim (render buffers), render settings, and how HdRenderThread is owned and started/stopped.
- `renderParam.h`: AcquireSceneForEdit stops the render thread before an edit. Sync writes through this.
- `renderPass.cpp`: _Execute never blocks; it binds AOV render buffers, restarts the render thread on camera/size change, and reports IsConverged.
- `renderBuffer.h/.cpp`: an HdRenderBuffer with Allocate / Map / Unmap / Resolve and a CPU-side buffer. This project keeps Map() real (CPU readback), unlike FRAGMENT.
- `renderer.cpp`: the render callback run by HdRenderThread; look at how it checks for a stop request between work units and copies into the buffer under LockFramebuffer.
- `mesh.cpp`: HdMeshUtil triangulation and Hd_SmoothNormals (needed at s4, not s3).

Houdini specifics are NOT in this code: Houdini discovers the plugin through PXR_PLUGINPATH_NAME (or a packages json), lists it via UsdRenderers.json, and builds against $HFS/toolkit (CMake: find_package(Houdini) with CMAKE_PREFIX_PATH=$HFS/toolkit/cmake, link target Houdini). See DECISIONS.md for the pinned HFS.