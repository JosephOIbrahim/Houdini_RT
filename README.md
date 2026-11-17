<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/logo/logo-dark.svg">
  <img src="assets/logo/logo-light.svg" alt="HoudiniRT" width="360">
</picture>

# HoudiniRT for Houdini 22

HoudiniRT is a progressive GPU path tracer for Solaris, implemented as a Hydra
render delegate with Vulkan inline ray queries and Adobe OpenPBR. Native Houdini
nodes author the scene; the integrated HoudiniRT panel controls the chosen viewer.

## Current delivery: UVs, textures and HDRI lighting

The Phase 4B delivery adds native MaterialX Image and UV inputs, PNG/JPEG/EXR/HDR
loading, current-config OCIO conversion, and importance-sampled latlong Dome Lights.
It builds on live scene edits, GPU accumulation/resolve, finite lights, authored
normals, diagnostic outputs and bounded opaque OpenPBR materials.

- [Artist controls and supported inputs](extensions/rt_toggle/README.md)
- [Current texture/HDRI contract](docs/textures-hdri-design-2026-11-17.md)
- [Native Houdini node and reference evidence](docs/textures-hdri-native-workflow-2026-11-17.md)
- [Verification, installation and known limits](docs/textures-hdri-verification-2026-11-17.md)
- [Milestone history](claude-progress.md)

The dated contract and capability profile describe the current release. Earlier
Phase 4A prose in the native developer README describes the previous milestone;
its later native-workflow section and the links above cover this delivery.

## Artist workflow

1. Author the scene in Solaris. Use Material Library, a USD MaterialX Builder,
   OpenPBR Surface and Assign Material for supported opaque materials.
2. Connect native MtlX Image nodes to supported color or scalar inputs. Use `st`
   coordinates through Texcoord/Geompropvalue and optional Place2D/USD Transform2D.
3. Add a Dome Light with a latitude-longitude HDRI; use its native rotation,
   pole-axis, intensity, exposure and color controls.
4. Open the HoudiniRT Python Panel, choose the intended Scene Viewer and enable
   rendering. Pause/Resume and Off operate on that viewer. Off restores its prior
   renderer, pause state and reference-grid visibility.
5. Use Check scene support for bounded stage inspection. Asset availability and
   decoding remain unassessed there; native renderer diagnostics report failures.

The panel, shelf, radial menu and optional node share one session controller.
Material and light controls remain native Houdini controls. No model connection
or separate Python sidecar is required to render.

## Architecture

Hydra Sync extracts CPU scene data. Execute publishes immutable requests without
Vulkan calls or waiting for the render thread. Object-space mesh resources and
BLASes persist; transforms/visibility update instances, and shading changes update
independent material, UV and image resources. CPU image decoding runs asynchronously.

The render thread owns GPU uploads, acceleration structures and tracing. Short
batches accumulate samples in device-local memory; a separate resolve kernel
produces scene-linear images. Generation checks prevent stale publication.
Pause is acknowledged at a completed batch boundary; paused edits resume with the
latest scene. Actual counters record submitted work and resource updates.

## Build and validation

The tested toolchain is Windows 11, Houdini 22.0.429/HDK, Visual Studio 2022 Build
Tools, CMake, Vulkan SDK 1.4 and an RTX 4090. Use the matching Houdini SDK and
its OpenUSD, OpenImageIO and OpenColorIO libraries. See
[the native build instructions](src/hdRT/README.md#build).

Sealed exams in `verify/` remain unmodified regression gates. Additional feature
probes and separate judges in `tests/textures_hdri/` cover native translation,
image decoding/color handling, sampling, lighting transport and installed GUI
behavior. Receipts bind sources, executable inputs, exact assets and outputs.
Vulkan validation must remain silent through teardown. The verification report
separates physical references from live-vs-fresh scene update comparisons.

## Bounded feature scope

Supported images feed seven opaque isotropic OpenPBR inputs: base color, base
weight, metalness, specular color, specular weight, roughness and IOR. Closest and
bilinear filters operate at level zero. Uniform domes, up to eight textured domes,
distant lights and rectangle/disk/uniformly-scaled sphere lights are supported.
Beauty and geometric diagnostic outputs share a publication generation.

UDIMs, sequences, mipmapping, normal maps, arbitrary shader graphs, additional
OpenPBR lobes, material subsets, instancing and subdivision remain unsupported.
HDRIs illuminate and reflect; camera misses remain transparent. External changes
to the same image file require Off/On for explicit reload. Limits and fallback
rules are specified in the current contract and `src/hdRT/capabilities.json`.

## Repository

| Path | Purpose |
| --- | --- |
| `src/hdRT/` | Native delegate, GPU kernels and capability profile |
| `extensions/rt_toggle/` | Integrated HoudiniRT artist controls and stage inspection |
| `tests/` | Reproducible feature probes and independent judges |
| `verify/`, `specs/` | Human-owned sealed exams and original specifications |
| `src/furnace/` | Standalone foundational GPU experiments |
| `docs/` | Dated designs, reference evidence and verification reports |
| `DECISIONS.md`, `claude-progress.md` | Lessons and implementation history |
| `third_party/` | Licensed reference implementations and test assets |

## License and credits

HoudiniRT and its Python extension use Apache 2.0. Pixar-derived files retain
Tomorrow Open Source Technology License headers; see `src/hdRT/NOTICE.md`.
Vendored Adobe OpenPBR uses Apache 2.0, Sascha Willems Vulkan samples use MIT,
and the usd-wg teapot asset is CC0. Third-party directories retain their licenses.
The external CPython runtime is not part of this repository.

Houdini, Solaris, Karma and the HDK are SideFX products. This project is independent
and is not affiliated with or endorsed by SideFX. Karma is a reference renderer
for the explicitly shared subset of the verification scenes.
