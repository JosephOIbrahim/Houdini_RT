# Actual Houdini AOV delivery

This supplementary test runs Houdini 22.0.429's `husk` against an exact plugin
payload, using fresh isolated preferences. The USD fixtures contain explicit raw
`UsdRender.Var` bindings. A separate OpenImageIO judge reads all stored EXR channel
types and pixels and compares them with analytic orthographic planes and misses.

The three positive scenes cover ordinary uniform normals, a rigidly rotated
camera/object, and indexed face-varying `primvars:normals` that override conflicting
built-in normals. Each checks color coverage, world normal, eye normal, projected
depth, camera-ray distance, primitive/coarse-face/instance IDs and albedo. The
single Hydra primitive must have one nonnegative ID throughout its two polygons;
its numeric assignment is left to Hydra. Coarse polygon IDs are exactly 0 and 1,
and misses retain each authored clear value.

Houdini stores integer channels as OpenEXR UINT. The judge reads native integers
before interpreting `0xffffffff` as the signed miss sentinel. Reading those
channels as floating point through OIIO would normalize them to `[0,1]`. The judge
also records Houdini's native channel names, including unprefixed RGBA,
`Pz.cameraDepth`, and repeated scalar layer/component names.

An unknown raw output must produce a diagnostic, nonzero husk exit and no native
published frame. A separate incompatible normal/int request records **host output
conversion**: husk uses the normal descriptor internally, then converts the saved
channel to UINT. It does not test the delegate receiving an incompatible buffer;
the CPU AOV contract test covers that validation directly.

From the repository root, after a sealed s5 gate has passed:

```powershell
python tests/reliable_lighting/aov_host/run.py --sealed build/m5/runs/RECEIPT_DIRECTORY/receipt.json --output build/m5/aov/new-run
```

The default only freezes scripts/payload hashes and generates USD fixtures with
Houdini's Python interpreter. Use a **different new output directory** and add
`--render` when the GPU is available. The runner executes owned children
sequentially with bounded timeouts and performs its independent judge afterwards.
`--candidate PATH` is available for diagnostics and explicitly marks the receipt
`diagnostic_only`; it does not claim sealed acceptance. All attempts are retained.

The executed scripts preserved here match `build/m5/aov/run-04/inputs` exactly.
That diagnostic passed all five cases on 2026-11-17; maximum positive AOV error was
`3.8147e-7`. The unknown output exited 1 and created no EXR. Final acceptance must
use the final sealed payload, recorded separately by its receipt and hashes.

The error fix follows SideFX's documented
[critical delegate error protocol](https://www.sidefx.com/docs/hdk/_h_d_k__u_s_d_hydra.html):
`GetRenderStats()` supplies a nonzero integer `huskErrorStatus`. USD diagnostics
alone do not make husk fail. Earlier diagnostic attempts and their failures remain
under `build/m5/aov`; the fixture's channel-name assumptions and OIIO integer reads
were corrected before the passing run, without changing analytic geometry values.
