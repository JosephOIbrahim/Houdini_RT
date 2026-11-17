# UVs, textures and HDRI lighting — verification, 2026-11-17

This delivery adds native Solaris image and environment authoring to the same
HoudiniRT viewer workflow. Material Library, OpenPBR, Image, UV and Assign Material
nodes author materials; Dome Light authors HDRI illumination. The existing panel
selects the viewer, enables HoudiniRT, pauses/resumes and restores the prior renderer.
The tested host is regular Houdini Indie 22.0.429, Python 3.13, on the RTX 4090.

## What is implemented

- Indexed `st` UVs across USD interpolation domains, including face-varying seams;
  independent corner uploads preserve geometry and acceleration structures.
- Direct native Image inputs for the seven supported OpenPBR parameters, native
  Texcoord/Geompropvalue, Place2D and USD Transform2D semantics, four address modes,
  closest/bilinear sampling, and PNG/JPEG/EXR/HDR decoding.
- Scalar data remains raw. Color processing uses the active Houdini OCIO config
  and `scene_linear` role; no hardcoded ACES working space.
- Up to eight latlong HDRIs, native pole-axis/rotation/exposure/tint, importance
  sampling with a uniform support component and consistent mixture PDFs/MIS.
- Asynchronous CPU decoding and immutable cached assets; render-thread GPU uploads,
  cancellation/pause checks, generation-safe publication and actual resource counters.
- A bounded support report names unsupported graphs and unassessed assets without
  opening files on the UI thread. Invalid decoded materials fall back with native
  diagnostics; invalid domes are excluded with diagnostics.

The exact supported subset and native reference ledger are in
[the design](textures-hdri-design-2026-11-17.md) and
[native workflow evidence](textures-hdri-native-workflow-2026-11-17.md).
Artist instructions are in [the extension README](../extensions/rt_toggle/README.md).

## Independent rendering and regression evidence

Paths below are relative to this worktree. Run outputs are retained locally under
ignored `build/`; reproducible probes and judges are under `tests/textures_hdri/`.
Candidate output is judged separately from the renderer. Numerical bounds were
fixed before accepted runs; no image fitting, crops or alpha remapping were used.

| Gate | Result and receipt |
| --- | --- |
| Unchanged sealed s5/s4 | PASS, 22 live/fresh images exact; Karma FLIP p99 0.0229 < 0.045. `build/m6/runs/20261117T165346Z-s5-18411768476708441281/receipt.json` |
| GPU sampling and environment PDFs | PASS, 38 cases and 14 distributions; maximum guide error 5.96e-8. `build/m7/runs/20261117T165045Z-gpu-8c76f7cc/receipt.json` |
| Existing material regression | PASS, 109 cases across three seeds plus fresh controls. `build/m6/runs/20261117T165802Z-gpu-c48e0b10/receipt.json` |
| Native CPU ingress | PASS, UV domains/seams, five native image graph forms, material and dome translation. `build/m7/cpu-receipt-01/receipt.json` |
| CPU image loader/cache | PASS, 40 cases plus cancellation/revision/configuration checks. `build/m7/asset-cpu-03/receipt.json` |
| Actual texture/UV USD | PASS, 42 FLOAT32 outputs; seven image-vs-constant pairs exact in full RGBA; independent albedo maximum error 1.33899e-5 < 4e-5. `build/m7/usd-texture/run-01/receipt.json` |
| Actual HDRI USD/Karma | PASS, 77 FLOAT outputs, 26 scenarios and five shared Lambert Karma comparisons. `build/m7/runs/20261117T170503Z-usd-hdri-faabad63/receipt.json` |
| Actual textured HDRI OpenPBR furnace | PASS, 12 cases across three seeds; maximum unit-energy error 0.00077331. `build/m7/runs/20261117T170821Z-environment-furnace-9255a65f/receipt.json` |
| Python extension | PASS, Python 3.12: 498 passed/19 skipped; Python 3.14: 515 passed/2 skipped; H22 native USD/HOM: 34 passed. `build/m7/python-tests-delivery/receipt.json` |

The USD combined receipt binds 119 outputs at `build/m7/usd-combined-01/receipt.json`.
Vulkan validation remained silent through teardown in the accepted GPU runs.
The native source, protected exam and exact tested DLL hashes are retained in the
receipts. Shared Lambert comparisons do not claim arbitrary OpenPBR/Karma parity.

## Installation

Version 0.5.0 / profile `textures-hdri-v1` is installed at:

`C:/Users/User/AppData/Local/MythAndLogic/HoudiniRT/textures-hdri-h22.0.429-d23eb03a049f`

The regular preference package is
`C:/Users/User/OneDrive/Documents/houdini22.0/packages/zz_houdinirt.json`.
Its SHA-256 is `65cf53a286e2a9d3f919c0386bdd7e1df3dc0a9ebabdaaeff1827de38c37c754`.
The package points to the immutable tested payload; 112 payload files, 199 source
inputs, eight proof receipts and 15 preserved registrations were independently
checked. Only the package's HoudiniRT installation pointer changed. The prior
`reliable-materials-h22.0.429-744c84b9820d` payload remains available, with exact
prior package bytes in the adjacent `.before-textures-hdri-h22.0.429-d23eb03a049f.disabled`
backup. Receipts: `build/m7/install-d23eb03a049f/install_receipt.json`,
`build/m7/installed-audit-01.json`, `build/m7/final-freeze-independent-01.json`.

## Scope and preserved unsuccessful runs

Filtering is level zero; mipmapping, UDIMs, image sequences, normal maps, arbitrary
shader graphs, material subsets, instancing and subdivision remain unsupported.
HDRIs illuminate surfaces/reflections; camera background stays transparent.
External same-path file edits require Off/On for explicit reload. Loader tests
verify file revision handling; the GUI fixtures remain immutable and do not claim
an end-to-end same-path overwrite test. Limits are 16M texels/image, 32M scene
texels, 128 images, 65536 bindings and eight textured domes.

Earlier GPU attempts are retained: one source-freeze mismatch during parallel CPU
development and one actual negative-coordinate NPOT wrap defect. The final shader
uses defined unsigned modulo and passes the original bounds plus NPOT cases.

The first installed GUI run, `gui-accepted-01`, exceeded the first-image deadline.
Backend initialization took approximately 31.8 seconds; the exact cause was not
isolated. An unchanged warm repeat produced its first image in 135.610 ms, but
`gui-accepted-02` then timed out waiting for the separate visual-observation marker.
Both failed runs and process audits remain preserved. They are not accepted GUI
evidence, and the warm timing is not a universal startup guarantee.

## Regular Houdini desktop acceptance

`build/m7/installed/gui-accepted-03` passed the complete installed workflow using
the normal preference directory/package and the exact installed DLL. A native
Material Library/OpenPBR/Assign Material graph, Image/Texcoord/Place2D nodes and
Dome Light 3.0 supplied the scene. The native Image and Dome parameter panes were
observed with computer use alongside the integrated paused renderer controls;
the observation markers explicitly record partial desktop occlusion. Actual
parameter values, native publications and image changes were checked separately.

The independent judge reparsed 1,168 ordered native events and verified active
pause acknowledgment at 1,343 of 65,536 samples in 19.281 ms, followed by an
8.00543-second no-work hold. The paused UV/pole-axis edit took effect on Resume.
Panel recreation preserved ownership, a second viewer remained unaffected, and
Off restored the previous paused Karma CPU renderer and original mixed grids.
Four scripted camera edits measured 114.893/123.272 ms p50/p95 from edit to
observed current-image publication; these include observer polling and are
fixture-specific timings, not an interactive performance guarantee.

All nine live images matched fresh normal-Houdini process/delegate captures
**exactly in full RGBA**: baseline, UV placement, roughness map, image-file swap,
HDRI rotation, exposure, HDRI-file swap, missing-image fallback and recovery.
Every intended edit changed pixels, and restored controls reproduced the original
baseline exactly. Missing-file fallback was also verified in native diagnostics.
The fresh-process audit binds immutable stages/assets, native OCIO context,
626x647 viewport, camera, 256 samples, seed 101, four bounces and capture settings.

These are matching-host live/fresh comparisons. They do not claim equivalence
between viewport flipbook coverage and raw husk AOVs, or arbitrary Karma/OpenPBR
appearance parity. Separate analytic/CPU/GPU/USD tests establish physical scope.
No image fitting, crops, masks, alpha corrections or relaxed bounds were used.

Acceptance receipts:

- `build/m7/installed/gui-accepted-03/result.json`
- `build/m7/installed/gui-accepted-03/independent_judge.json`
- `build/m7/installed/gui-accepted-03/fresh-01/independent_freshness.json`
- `build/m7/installed/gui-accepted-03/fresh-01/receipt.json`
- `build/m7/installed-audit-02.json`

All owned original/fresh Houdini processes exited, without affecting preexisting
applications. The final installed audit reconfirmed payload/source/proof hashes,
preserved registrations and rollback bytes. `build/m7/delivery.json` binds the
accepted receipts and exact committed source blobs after the atomic commit.
