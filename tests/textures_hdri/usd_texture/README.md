# Actual USD texture and UV delivery

This supplementary pack uses Houdini 22.0.429's USD adapter and `husk` to test
texture delivery through the real native material network. It does not claim
Karma appearance parity. The independent oracle reads the frozen encoded asset
values and computes geometry, UV interpolation, addressing and sampling without
calling renderer implementation helpers.

The 42 renders cover UV domains and indexing, float2/float3 seams, the five native
MaterialX graph forms, PNG/JPEG/EXR/HDR and color-space handling, three addressing
modes, and diagnosed asset/UV/graph failures. Each of the seven texture-driven
OpenPBR inputs has an equivalent constant-material render. Every constant control
must visibly differ from the baseline, preventing an ignored texture from passing.

Prepare the independent assets and native fixture exports first (see
[`../native_fixture/`](../native_fixture/)). From the repository root, use a new
output directory and an already passed sealed `s5` receipt:

```powershell
python tests/textures_hdri/usd_texture/run.py --output build/m7/usd-texture/new-run --sealed PATH/receipt.json --render
```

Omit `--render` for CPU-only fixture preparation. `--assets` and `--native` can
override the default asset manifest and native export directory. Rendering must
have exclusive access to the validation GPU. The wrapper creates isolated child
preferences, retains every render/log, verifies the sealed payload and source
hashes, and freezes scripts, expectations and inputs before creating images.

The frozen contract requires finite FLOAT32 beauty/albedo channels at 48×48,
completed 128-sample frames, clean Vulkan validation, full-frame albedo error
≤4e-5, and paired full-RGBA error ≤1e-5. There is no image fitting or masking.

The first final run passed all 42 renders in `build/m7/usd-texture/run-01`.
Maximum albedo error was 1.338986e-5; all seven texture/control pairs were exactly
equal in every RGBA pixel. `receipt.json` binds source, shader, payload and script
hashes; `judge.json` retains individual channel headers, comparisons and limits.
