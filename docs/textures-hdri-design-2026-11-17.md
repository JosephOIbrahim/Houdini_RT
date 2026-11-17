# Phase 4B: native UVs, image textures and HDRI lighting

The artist edits the existing Solaris scene. Material Library, MaterialX image
and UV nodes, Assign Material and Dome Light remain the authoring interface.
The existing HoudiniRT panel selects the intended viewer, enables the delegate,
pauses/resumes it and restores the previous renderer. There is no second scene
database or separate toggle product.

## Supported contract

- `st` float2/float3 UV primvars, including indexed face-varying seams, become
  independent triangle-corner shading data. USD triangulation is authoritative.
- Direct, type-matching MaterialX image nodes feed the seven existing opaque
  OpenPBR inputs: base color/weight, metalness, specular color/weight, roughness
  and IOR. Constants keep their previous behavior.
- Texcoord index zero and geompropvalue `st` are supported. The native
  `ND_place2d_vector2` and `ND_UsdTransform2d` retain their distinct formulas.
- Address modes: constant, clamp, periodic and mirror. Filters: closest and
  bilinear, at level zero. Mip footprints, cubic filtering, UDIMs, sequences,
  normal maps and arbitrary MaterialX graphs are outside this bounded release.
- OpenImageIO decodes flat, full-window PNG, JPEG, EXR and HDR images. Opaque
  image RGB is unassociated; alpha does not implement material opacity.
- Scalar images are data. Named color spaces use Houdini's current OCIO config
  and `scene_linear` role. Empty color space means sRGB for PNG/JPEG, scene
  linear for EXR/HDR. The regular 22.0.429 config uses Linear Rec.709 (sRGB).
- Sampled OpenPBR values must fit the existing constant domains: [0,1], except
  IOR [1,3]. Invalid assets produce a diagnosed whole-material Lambert fallback.
  A 3e-5 endpoint allowance applies only after a nonidentity OCIO color transform,
  then snaps to the domain boundary: the installed optimized sRGB processor maps
  exact white to 1.00002408. Raw/data inputs retain strict bounds.
- Up to eight textured Dome Lights use latlong mapping. Automatic format
  requires a 2:1 image. Pole-axis offset and rigid rotation/uniform scale are
  honored. Invalid domes are excluded with a diagnostic. Negative/nonfinite
  radiance is unsupported. Camera misses remain transparent; HDRIs illuminate
  surfaces and appear in reflections.
- Limits: 16M texels per image, 32M decoded scene texels, 128 images and 65536
  texture bindings. Assets are never silently resized to fit.

## Ownership and resources

Hydra Sync extracts CPU metadata and UV arrays. Execute publishes immutable
requests and never waits for image decoding or Vulkan. A bounded per-renderer
CPU decoder runs separately; its future survives canceled requests. The render
thread can acknowledge GPU quiescence during decoding, and rechecks pause and
generation before uploading assets. Asset data is immutable after publication.

GPU image storage is device-local float4 data, with explicit sampling semantics.
Mesh tables hold independent UV addresses and texture-binding indices. UV,
texture, material and dome edits do not invalidate acceleration structures.
Actual upload/cache counters are exposed in native render statistics.

Asset identity includes path, file revision, color configuration and value
domain. Changing an authored path or scene input reloads revised assets. An
external file change alone is not a scene edit; switching HoudiniRT off/on
explicitly reloads it. No UI-thread file watcher is introduced.

Each environment has a luminance-times-solid-angle distribution, using exact
latlong cell solid angles. A 5% uniform component guarantees support around
filtered bright texels. Multiple environments and uniform dome illumination
form one mixture; evaluation sums all environments and uses the same mixture
PDF. Environment NEE and BSDF escape paths use power-heuristic MIS. Existing
finite-light proposals remain separate. Scenes without textured domes retain
their previous random streams.

## Verification

Independent CPU image/UV/color references precede GPU observation. Tests cover
UV seams and domains, native graph exports, all address/filter modes, scalar and
OCIO conversions, native dome pole axes, HDRI orientation and rotation, PDF
normalization, energy, occlusion, multi-seed uncertainty and edit costs. Existing
sealed geometry/material checks remain regression gates. Vulkan validation
must remain silent. Installation uses a new immutable payload and preserves
other package registrations. Final acceptance includes normal Houdini 22 GUI
enable, native edits, pause/resume, target isolation and renderer restoration.

The panel's bounded static support report does not claim to decode files.
Native asset diagnostics are available in renderer statistics and log; HOM
22.0.429 has no public per-viewer render-stat dictionary getter.
