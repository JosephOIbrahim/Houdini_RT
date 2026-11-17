# UV, image texture, and HDRI acceptance design

This is supplementary verification of the authorized milestone, not a sealed
exam. Candidate outputs cannot define the oracle. Fixtures, independent reference
calculations, source/configuration hashes, and numeric bounds are frozen before
candidate images are judged. Existing protected exams remain unchanged and run in
copied runboxes. No GPL source is used.

## Separate the contracts

1. Geometry supplies a well-defined surface coordinate at a hit. Test this through
   deterministic pixel-center albedo guides, without Monte Carlo lighting noise.
2. A file decoder and color transform produce scene-linear texels. Compare those
   against authored integer values, independent transfer functions, and the pinned
   OCIO CPU processor. JPEG is compared with decoded file values, not its lossy
   source chart. HDR and EXR values above one must survive the lighting path.
3. Texture sampling maps a UV to a value. Independently compute texel-center
   nearest/bilinear sampling, orientation, out-of-range addressing, and seams.
4. An environment supplies directional radiance. Its sampling PDF must integrate
   to one, match its chosen distribution, and preserve energy under MIS. An
   importance sampler changes variance, not the expected result.
5. Edits produce a new current image without stale publication, unnecessary
   acceleration-structure work, or disturbing another viewer. Pause is a native
   acknowledged no-work interval, not merely a changed button label.

## UV and texture fixtures

- Asymmetric 4x4 and non-power-of-two 5x3 charts with distinct corner/channel
  values expose U/V flips, transpose, channel mistakes, and stride errors.
- Triangles and nonplanar/ngon triangulation cases cover constant, uniform,
  vertex, varying, and face-varying coordinates, including indexed primvars and
  a shared geometric edge with a deliberate UV seam.
- Missing coordinates, missing named primvars, malformed indices/counts, and
  nonfinite values must yield the declared path-specific unsupported/default
  behavior; none may silently retain an earlier valid texture.
- Address tests sample texel centers, interpolation midpoints, exact boundaries,
  and negative/multiple-period coordinates independently on both axes. The
  installed MaterialX image nodedef defaults to periodic addressing and linear
  filtering. Closest/linear and constant/clamp/periodic/mirror are distinct cases.
- Minification and grazing views expose aliasing. If mip/derivative filtering is
  outside the admitted implementation, its limitation must be explicit rather
  than claiming MaterialX cubic or production texture filtering.
- Color3 and scalar image outputs exercise each admitted OpenPBR destination.
  Albedo AOV verifies base color/weight delivery; actual beauty tests exercise
  roughness, metalness, and other admitted scalar destinations so merely adding
  a diagnostic texture does not pass.
- PNG8/PNG16, JPEG, FLOAT/HALF EXR, and Radiance HDR test actual USD asset paths,
  relative resolution, channel selection, orientation, and decode failures.
  High-dynamic-range lighting values are separate from bounded base reflectance.
- Raw, analytic sRGB, automatic file rules, and named OCIO spaces are separate
  contracts. Pin the effective configuration, destination role, processor cache
  identity, and file metadata; report invalid/unknown spaces truthfully.

## Environment fixtures and independent physics

- Constant white/color/black maps: Lambert output equals reflectance times dome
  radiance, regardless of map size, rotation, or proposal mixture.
- Cardinal color bands and smooth directional ramps: verify center, poles, seam,
  handedness, rotation, intensity, exposure, and old/new USD dome schemas.
- OpenEXR/old DomeLight convention is north +Y, map center +Z, positive longitude
  +X. DomeLight_1 defaults poleAxis to the stage up axis; for Z-up its north is +Z
  and map center is -Y. Explicit Y/Z pole axes and parent transforms are tested.
- Finite solid-angle cells use exact area dphi*(cos(theta0)-cos(theta1)), not
  sin(theta) at one point as an integration oracle. The sum must equal 4*pi.
- One bright cell and narrow bright patches test high dynamic range, pole rows,
  seam-adjacent emitters, importance selection, and undersampling. Independent
  solid-angle quadrature yields Lambert irradiance, including cosine and shadows.
- Multiple differently rotated maps plus uniform dome and finite emitters test
  radiance additivity, proposal selection probabilities, and light/BSDF MIS.
  Finite opaque emitter geometry replaces/occludes environment radiance.
- Black maps require a finite, valid sampling fallback and zero contribution;
  tiny nonzero total power must not create NaN, Inf, division by zero, or bias.
- A narrow environment behind a glossy lobe exercises BSDF and environment
  overlap. Shared Adobe CPU transport is identified as shared-model integration
  evidence; independent energy, Lambert, and PDF checks remain separate.
- Statistical rows use independent seeds, recorded standard errors, a fixed
  absolute/relative ceiling, and a prespecified sigma-plus-numerical-floor bound.
  Quadrature convergence is checked at two resolutions before accepting a target.

## Edits, costs, and failures

- UV-only edits: coordinate upload, no BLAS/TLAS or position/index upload.
- In-session image revisions, material image parameters, color-space changes,
  and HDRI rotation/exposure: no geometry/AS rebuilding. Measure actual
  texture/table/CDF uploads and cache hits/decodes using declared counters.
  Same-path external file changes require Off/On or a subsequent shading-scene
  edit; there is no file watcher or public resource-reload command. Off/On creates
  a new delegate and therefore rebuilds its acceleration structures. CPU tests
  prove file-version reload; GPU tests prove resource-update costs; the GUI tests
  prove immutable path changes and renderer ownership separately. No end-to-end
  same-path overwrite image test is claimed.
- Shared assets upload once; repeated unchanged snapshots perform no uploads.
  Remove/re-add and missing-file recovery must not reuse a stale asset version.
- Change assets while rendering and while paused. Hold after native pause ACK,
  resume the latest scene, and reject any stale-generation AOV publication.
- Cancellation and superseding edits must leave buffers/descriptors alive until
  submitted work completes. Test teardown and invalid asset recovery with Vulkan
  validation enabled; zero validation warnings/errors including destruction.
- Sync/Execute perform no Vulkan or acceleration-structure builds. Static review
  is supplemented by native edit counters and concurrent GUI responsiveness.

## Integration and retained baselines

Actual USD fixtures exercise the native Material Library/Image/OpenPBR graph and
Dome Light LOP output. Headless images use typed FLOAT render vars. Karma is an
independent comparison only where the material/lighting equations are shared;
the prior qualified OpenPBR appearance differences remain disclosed.

Run the unchanged s5/s4 copied exams and previous 109-case material GPU matrix
against the final frozen source/shaders. Verify exact final staged DLL in actual
USD tests, then install a versioned payload with rollback and registration hashes.
Regular-Houdini tests use native controls and the integrated panel. Matched fresh
GUI renders validate live-edit correspondence through the same host capture path;
the documented host-flipbook/raw-AOV coverage distinction is retained.

Filter footprint/mips, admitted image graph nodes, automatic color-space rules,
camera visibility, unsupported formats, and reload triggering must be finalized
with the implementation contract before the executable fixture freeze.
