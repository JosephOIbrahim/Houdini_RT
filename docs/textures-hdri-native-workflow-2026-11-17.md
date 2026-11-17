# Native texture and HDRI workflow evidence — 2026-11-17

This records CPU-only observations in installed Houdini 22.0.429. It is a node
translation contract, not a claim that the renderer supports every listed node.
The implementation capability profile and independent render receipts determine
the final supported subset. No GUI, GPU renderer, or user scene was opened for
these probes.

The artist workflow remains Material Library → native MaterialX builder → OpenPBR
Surface → Assign Material, with a native Dome Light LOP for environment lighting.
The integrated HoudiniRT panel remains responsible for renderer controls and an
on-demand scene support report; it does not duplicate material or light controls.

## Actual native nodes and USD translation

`build/m7/native-texture-fixture/probe_nodes.py` inventories actual node types,
parameter names, menus, and defaults in `node_inventory.json`.
`create_fixture.py` creates five USD exports and deterministic image data; its
`receipt.json` records the script, asset, and exported-stage hashes and shader
inputs. The fixtures use native Material Library and Assign Material nodes.

| Native node | Exported shader ID | Relevant native controls |
| --- | --- | --- |
| `mtlximage`, Color signature | `ND_image_color3` | `file`, `filecolorspace`, `uaddressmode`, `vaddressmode`, `filtertype` |
| `mtlximage`, Float signature | `ND_image_float` | Same controls; `signature=default` selects float |
| `mtlxtexcoord` | `ND_texcoord_vector2` | `signature=vector2`, `index=0` |
| `mtlxgeompropvalue` | `ND_geompropvalue_vector2` | `signature=vector2`, `geomprop=st` |
| `mtlxUsdTransform2d` | `ND_UsdTransform2d` | `in`, `rotation`, `scale`, `translation` |
| `mtlxplace2d` | `ND_place2d_vector2` | `texcoord`, `pivot`, `scale`, `rotate`, `offset`, `operationorder` |

There is no native node named `mtlxtransform2d`. Image nodes default to Color,
periodic U/V addressing and linear filtering. Houdini omits default input values
from USD; an implicit-coordinate image exports no `texcoord` input opinion.
The installed MaterialX definition supplies `defaultgeomprop=UV0`. The corpus
documents Texcoord as reading `primvars:st` or `primvars:uv`; a deliberately narrow
renderer implementation must report any unsupported alternative explicitly.

Setting Image **File Color Space** to `srgb_texture` or `Raw` authors USD
`colorSpace` metadata on the image shader's `inputs:file` asset attribute. It does
not put that tag on the OpenPBR input or image output. The default menu value is
empty. The exact case of `Raw` matters when parsing native-authored metadata.

The two transform nodes are not interchangeable. The installed nodegraphs define
Place2D SRT as `rotate2d((uv-pivot)/scale, rotate)-offset+pivot`, and TRS as
`rotate2d(uv-pivot-offset, rotate)/scale+pivot`. UsdTransform2d instead computes
`rotate2d(uv*scale, -rotation)+translation`. Angles are degrees; these expressions
use MaterialX's own `rotate2d` convention. A generic shared SRT interpretation
would incorrectly translate one of these native controls.

## Native Dome Light

Creating `domelight` resolves to `domelight::3.0`, which emits `DomeLight_1`.
`domelight::2.0` remains available and emits the older schema. The native texture
parameter is `xn__inputstexturefile_r3ah`; format is
`xn__inputstextureformat_06ah` (default `automatic`, fixture `latlong`).
The newer node has `poleAxis` choices `scene` (default), `Y`, and `Z`.
The fixtures explicitly choose `Y`, and use the normal rotation parameters.
No file-color-space menu was present on this Dome Light node in the inventory.

The corpus specifies a 2:1 latitude-longitude map: north/south at ±Y, longitude
zero toward +Z, and positive longitude toward +X. Native adapter handling of the
new pole-axis schema must be verified separately from this authored-USD evidence.

The tiny assets contain explicit numbers: a 2×2 RGB PNG, 2×2 scalar FLOAT EXR,
and 8×4 FLOAT HDR environment. They are implementation fixtures, not the
independent judge's reference images or its separately generated numeric charts.

## Native color context and bounded asset inspection

`probe_ocio.py` and `ocio_context.json` record fresh normal-preference hython.
Houdini's `hou.Color.ocio_configPath()`, `OCIO` environment value, and OCIO
Current/FromEnv/FromFile configurations agree. The installed package
`$HFS/packages/ocio.json` supplies
`$HFS/packages/ocio/houdini-config-v3.0.0_aces-v2.0_ocio-v2.5.ocio` only when the
user has not set `OCIO`. Its `scene_linear` role resolves to **Linear Rec.709
(sRGB)**, not ACEScg. The configuration SHA-256 is
`1bee9e5dbedd73bde04e647961e397994c6824a90f2e73b999b49a2f77eeed41`.
Named transforms must use the current native configuration and its scene-linear
role; the renderer must not replace a user OCIO context with this observed default.

`probe_asset_metadata.py` demonstrates that both USD attribute `Get()` and
`GetMetadata("default")` resolve asset paths. Reading an authored Sdf property
specification's `default` instead retains the unresolved path. The support scanner
uses that raw value, caps opinion stacks at 16, and marks time-sampled assets
unassessed. Its real-USD tests reject asset-valued `Get()` calls and filesystem
stat/open calls during inspection. Actual file and decode errors remain the
native loader's responsibility, exposed through warnings/logs and render stats.

## Primary reference ledger

Corpus prefix: `D:/Houdini_AI_Reference/upstream_sidefx/snapshot-2026-10-04/docs/`.
All files below were read only; the external corpus was not modified.

| Relative corpus path | Relevant section | SHA-256 |
| --- | --- | --- |
| `solaris/materialx.md` | Textures and patterns; Primvars and geometry attributes; native image wiring | `9ed45a2999cd8aa1fe98f485f0ace6851a7be10222a6e3d664362e3ca70c6204` |
| `nodes/lop/domelight.md` | New schema; Environment maps; Texture, Format, Pole Axis | `030e5b9025d523e8ed9dfa7e9e88283fab53c6d9f1e7f0bdf51ade66d59d0469` |
| `nodes/lop/materiallibrary.md` | MaterialX builders and material translation | `22c4d403a331c549ab299d786b2b2c2b5b15f700de379c50ac629f2069d7f38e` |
| `nodes/lop/assignmaterial.md` | Native material binding workflow | `daa692254e2efacaf4efb79e2192eca37ef14602acb28c5c75587e04104f6ce7` |

Installed definition prefix:
`C:/Program Files/Side Effects Software/Houdini 22.0.429/houdini/materialx/libraries/`.

| Relative installed path | Relevant definitions | SHA-256 |
| --- | --- | --- |
| `stdlib/stdlib_defs.mtlx` | Image, Texcoord, Geompropvalue and Place2D defaults | `47c6c0e7f4a3c140925f29d4bc5fc977950e1be95e1a2c3f62b40f412b234678` |
| `stdlib/stdlib_ng.mtlx` | Place2D implementation, line 3076 onward | `ac426ee8ef0e2044866eff8fbd726e85cc59b89c5e3696c198fae3195b007738` |
| `bxdf/usd_preview_surface.mtlx` | UsdTransform2d definition and implementation | `66f6c5034beb9ea73637d8dee6d75a4cb9ab95508b71ab5afe181551a7ee28f3` |
