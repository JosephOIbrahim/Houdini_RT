# Native OpenPBR workflow evidence — 2026-11-17

Phase 4A retains the integrated HoudiniRT viewer controls and uses Houdini's native
material nodes. The following construction was cooked and exported headlessly in
Houdini 22.0.429; it is fixture/API evidence, not a claim of image fidelity.

1. Create a Solaris Material Library (`materiallibrary`).
2. Inside it create a USD MaterialX Builder and an OpenPBR Surface
   (`mtlxopen_pbr_surface`). Connect its `out` to the builder's surface output.
3. Author the material under the library's default `/materials/` prefix.
4. Connect an Assign Material LOP (`assignmaterial`), set Primitives
   (`primpattern1`) and Material Path (`matspecpath1`).
5. Edit the native shader parameters and use the existing HoudiniRT panel to render,
   pause/resume, inspect support and restore the previous renderer.

The native builder is a configured `subnet`, not a `usdmaterialbuilder` node type.
The fixture invokes installed `voptoolutils._setupMtlXBuilderSubnet`; this private
helper is used only for test construction. Artists use the native Tab-menu builder.
It replaces the default Standard Surface with OpenPBR and leaves the builder's
default zero-displacement shader connected. Native normals/tangents are not authored
or connected by default. The fixture disables `genpreviewshaders` to isolate the
MaterialX terminal; production prefers `mtlx` over the universal render context.

Actual exported surface: `ND_open_pbr_surface_surfaceshader`, port `out`.
Actual neutral displacement: `ND_displacement_float`, displacement 0, scale 1.
The installed nodedef is OpenPBR 1.1.1 in a MaterialX 1.39 library.
The fixture receipt is `build/m6/native-material-fixture/receipt.json`; its USD SHA256
is `3bdf54d721ad1790208b68ab12169b54626daa15a9b987168c316bc6b2fde94d`.

| Native shader parameter | Supported range |
| --- | --- |
| `base_color`, `specular_color` | Each untagged scene-linear RGB component 0–1 |
| `base_weight`, `base_metalness`, `specular_weight` | 0–1 |
| `specular_roughness` | 0–1; numerical minimum 0.001, no exact delta mirror |
| `specular_ior` | 1–3 |

Keep every other input at its installed nodedef default, including diffuse roughness
0 and inactive lobe children. The installed profile lists these defaults exactly.
Shader-output connections, explicit nonempty colorSpace tags, explicit shader normals/tangents,
other surface models, volume and active displacement trigger whole-mesh matte
displayColor fallback. Finite displacement scale is allowed only when displacement
is exactly zero. Full-purpose material bindings resolve with USD all-purpose fallback.
MaterialBind face subsets with a resolved material also trigger whole-mesh fallback;
unused material definitions are ignored. Keep light diffuse and specular factors at 1:
the current diffuse factor scales both OpenPBR lobes and specular is ignored.
The albedo AOV is base_color × base_weight, a diagnostic base color.
Authored interface-input connections are marked Not assessed, without a fallback
claim: the installed UsdImaging CPU probe flattened a material interface constant
into the surface parameter. See `build/m6/cpu-02/receipt.json` and `2.log`.

## Cross-reference ledger

Paths below were read without modifying the reference corpus. Hashes are SHA256.
The corpus snapshot predates installed Houdini; installed APIs/nodedefs determine
exact OpenPBR names/defaults, while the corpus establishes the native workflow.

| Source path | Relevant section | SHA256 |
| --- | --- | --- |
| `D:/Houdini_AI_Reference/upstream_sidefx/snapshot-2026-10-04/docs/nodes/lop/materiallibrary.md` | Overview, material builder subnets, material assignment, preview shader generation | `22c4d403a331c549ab299d786b2b2c2b5b15f700de379c50ac629f2069d7f38e` |
| `D:/Houdini_AI_Reference/upstream_sidefx/snapshot-2026-10-04/docs/nodes/lop/assignmaterial.md` | How to: primitive pattern, Explicit Path, Material Path | `daa692254e2efacaf4efb79e2192eca37ef14602acb28c5c75587e04104f6ce7` |
| `D:/Houdini_AI_Reference/upstream_sidefx/snapshot-2026-10-04/docs/solaris/materialx.md` | MaterialX shader networks and Solaris USD material construction | `9ed45a2999cd8aa1fe98f485f0ace6851a7be10222a6e3d664362e3ca70c6204` |
| `C:/Program Files/Side Effects Software/Houdini 22.0.429/houdini/python3.13libs/voptoolutils.py` | createMaskedMtlXSubnet / _setupMtlXBuilderSubnet, lines 2477–2605 | `a0c3ca91f5b9e81f873e1e4a05ca37c605ec7726f4b56ec3890caf3197dec11b` |
| `C:/Program Files/Side Effects Software/Houdini 22.0.429/houdini/materialx/libraries/bxdf/open_pbr_surface.mtlx` | ND_open_pbr_surface_surfaceshader inputs/defaults, lines 6–94 | `2a3139df7d75801bbbba184977ecdfeb8b8bd40683264cb9a81ab470ad90c7b8` |
| `C:/Program Files/Side Effects Software/Houdini 22.0.429/houdini/materialx/libraries/pbrlib/pbrlib_defs.mtlx` | ND_displacement_float, lines 276–284 | `cba74f62b3620f114b3a6e0316b7bd5708a37d447dd64ad3ae288d570a89ba8e` |

The support report is bounded main-thread USD inspection, separate from the native
translated scene and image validation. Actual USD tests cover assignments, terminal
ports, malformed graphs, metadata, default displacement and stale-report notices.
