# Reliable materials: phase 4A

This implements the next independently tested increment of the approved November 16
roadmap: a bounded MaterialX/OpenPBR path. UV interpolation, texture decoding/color
management and HDRI importance sampling follow this foundation. The accepted base
is `b3fa7b5`, including lighting, GPU accumulation and integrated viewer ownership.

## Artist contract

Author and assign a native MaterialX OpenPBR Surface in a Solaris Material Library.
Enable HoudiniRT in the existing panel/shelf. Edits to supported material constants
update the current viewer; pause holds the image, resume uses the latest material,
and Off restores the previous renderer and grids. No second material interface or
assistant connection is required. Headless and viewport rendering resolve the same
USD material and use the same BSDF.

The initial supported model is opaque and isotropic: base color/weight, metalness,
specular color/weight, roughness and IOR. Unsupported connected graphs and lobes
must produce path-specific diagnostics and an explicitly identified matte fallback.
The effective support matrix, including accepted parameter ranges and defaults,
will be published with the capability profile and exercised by tests.

The admitted inputs are untagged scene-linear constants: colors/weights, metalness
and roughness in [0,1], IOR in [1,3]. Diffuse roughness stays zero. Explicit color
space tags require a later conversion path and currently cause diagnosed fallback.
The upstream BSDF uses a 0.001 numerical roughness minimum. Material-accurate light
evaluation in this slice requires light diffuse/specular multipliers of one;
nondefault lobe controls are reported because the existing light transport applies
the diffuse multiplier to the combined response and ignores the specular multiplier.

## Implementation choice

Integrate Adobe's Apache-2.0 OpenPBR implementation into the existing Vulkan kernel
using its required texture LUT mode. A small native Hydra material adapter resolves
the bounded graph into immutable CPU values. Mesh bindings select those values in
the scene snapshot. GPU rows hold material constants alongside existing geometry
addresses; changing a material updates shading data without rebuilding geometry.
The legacy unbound displayColor path preserves its existing estimator.

A general MaterialX compiler would add graph execution, resources and caching before
the first BSDF is verified. A custom approximation would weaken correspondence to
the artist's OpenPBR controls. The bounded adapter plus the upstream BSDF provides a
testable path toward textures and more lobes without either dependency.

Keep CPU Sync and Execute free of Vulkan work. Integrate BSDF eval/sample/pdf with
the existing emitter MIS. Preserve generation fencing, device-local accumulation,
bounded batches, pause acknowledgement and all currently advertised diagnostic AOVs.
BSDF tables are initialized on the render thread and explicitly destroyed.

## Verification

- Freeze supported graph/lobe/range contracts and independent references before
  candidate image judgments. Test defaults, constants, bindings, removal, invalid
  values and unsupported graph/lobe diagnostics.
- Exercise the actual delegate kernel in white furnaces and directional/area-light
  cases; check energy, sample/eval consistency and reciprocity where applicable.
  CPU/GPU agreement with shared Adobe source is supporting evidence only.
- Compare independent material reference scenes and inspect every advertised output.
- Cross-renderer equality is qualified against the actual material models. The
  first strict Karma parity comparison is retained as failed evidence: the public
  MaterialX layer approximation differs from Adobe's reciprocal implementation.
  [OpenPBR's glossy-diffuse specification](https://academysoftwarefoundation.github.io/OpenPBR/#model/basesubstrate/glossy-diffuse)
  permits these alternatives. An independent public-MaterialX calculation and
  exact-USD CPU transport references qualify the distinction; numeric limits are
  not widened. Shared Adobe BSDF code is explicitly identified in CPU/GPU
  integration comparisons and complemented by analytic energy/reciprocity checks.
- Retain unchanged sealed s5/s4 regression exams, run in isolated copies, and require
  clean Vulkan validation including teardown.
- Install a versioned payload with source/build/proof hashes; preserve other regular
  Houdini packages. Test the installed native material-node workflow in Houdini
  22.0.429, including paused edits, two viewers, restoration and current images.

## References

- Approved roadmap: `reviews/HoudiniRT_Houdini22_Review_Roadmap_2026-11-16.md` in the
  external runtime workspace, phase 4.
- Local SideFX corpus: `D:/Houdini_AI_Reference/upstream_sidefx/snapshot-2026-10-04/docs`.
- [MtlX OpenPBR Surface](https://www.sidefx.com/docs/houdini/nodes/vop/mtlxopen_pbr_surface.html).
- [Adobe OpenPBR BSDF](https://github.com/adobe/openpbr-bsdf), Apache-2.0 snapshot
  copied byte-for-byte from the existing local dependency; provenance hashes accompany
  the vendored headers and LUT data.
