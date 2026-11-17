# Supplementary UV, texture and HDRI acceptance

These tests are additional acceptance, not a sealed s6 exam. Protected s5/s4
tests remain unchanged and execute in copied runboxes. Expected images, physical
equations, numeric limits and sampling seeds are frozen before candidate output.
The renderer writes images/counters; separate Python judges decide acceptance.

## Independent layers

- `oracle.py` implements texel-center filtering, all four address modes, latlong
  directions, exact cell solid angles, and horizon-split Lambert integration.
  It imports no production code. `test_oracle.py` checks its identities.
- `make_assets.py` creates independently specified asymmetric PNG/JPEG/EXR/HDR
  inputs, scalar maps and high-dynamic-range/pole/seam environments. Lossy codec
  decoded values and exact encoded hashes are recorded before testing.
- `asset_cpu/` tests actual OIIO/OCIO decoding, destination domains, named color
  transforms, file replacement, cancellation, cache separation and recovery.
- `make_reference.py`, `gpu_probe.cpp`, `run_gpu.py`, `judge_gpu.py` exercise
  actual production Vulkan sampling. The accepted matrix has 24 exact guide
  cases and 14 statistical environment cases. A diagnostic shader calls the
  unchanged production environment functions to expose 131,072 sample directions
  and PDF values per distribution; an independent oracle checks them.
- `environment_furnace/` adds twelve actual white-HDRI OpenPBR furnace cases,
  dielectric/metal, roughness 0/.3/1 and normal/grazing views, with three seeds.
  Unit energy is the independent physical oracle, including the new environment
  NEE and BSDF escape MIS paths. Material-only edits must not rebuild geometry.
- `usd_texture/` tests actual H22 USD-to-Hydra image/UV ingress: 42 FLOAT renders,
  all interpolation domains, native graph forms, codecs/color spaces, diagnosed
  fallbacks and seven texture inputs paired with constant-material beauty.
- `usd_hdri/` freezes native DomeLight/DomeLight_1 USD scenes and independent
  Lambert targets. Twenty-six scenarios produce 62 HoudiniRT plus 15 Karma
  renders. The shadow target integrates receiver and blocker area independently.
  Karma comparisons are restricted to shared constant-map Lambert domains;
  prior OpenPBR glossy-model nonparity remains disclosed.
- `combine_usd.py` verifies source, exact sealed DLL/SPIR-V, stages, encoded
  assets, images, logs and child judges before issuing one combined receipt.
- `installed/` contains the native artist workflow, fresh normal-Houdini
  reference processes, independent raw-log/freshness audits and whole-frame
  pixel checks. Host flipbook images are compared through the same host capture
  path; they are not represented as raw husk AOVs or independent BSDF physics.

## Reproduction

Run Python-only equations with `python -B -m unittest discover -s
tests/textures_hdri -p test_oracle.py`. Use installed H22.0.429 `hython -B` for
asset/fixture generation and OIIO/OCIO/USD judges. Native GPU runners require
MSVC2022, Vulkan SDK1.4.341.1 and an explicitly coordinated exclusive GPU slot.

1. Generate assets with `make_assets.py OUTPUT`, then freeze the matrix with
   `make_reference.py --assets OUTPUT/manifest.json --output REFERENCE`.
2. `run_gpu.py --reference REFERENCE --shaders PASSED_PLUGIN/shaders --run`
   snapshots native/vendor source and inputs before compile/render/judge.
3. `environment_furnace/run.py --sealed SEALED_RECEIPT` tests those exact shaders.
4. `usd_texture/run.py --sealed SEALED_RECEIPT --output RUN --render` generates,
   freezes, renders and judges the texture pack.
5. `hython usd_hdri/make_fixture.py --assets ASSET_MANIFEST --native
   NATIVE_IMAGE_LOP_EXPORT --output FIXTURES` freezes dome targets. Then run
   `usd_hdri/run.py --sealed SEALED_RECEIPT --manifest FIXTURES/manifest.json`.
6. `combine_usd.py --textures TEXTURE_RECEIPT --hdri HDRI_RECEIPT --output OUT`
   binds both independent subproofs. See `installed/README.md` for GUI execution.

The original failed non-power-of-two negative-wrap diagnostic is retained in
`build/m7/runs/20261117T164544Z-gpu-19111977`. GLSL signed remainder with a negative
operand was undefined; production now uses unsigned magnitude modulo. Eight
NPOT regression cases were frozen before observing the repaired output. The
judge's earlier NumPy integer-inference exception is separately retained; only
its temporary accumulator dtype was corrected, without changing any bound.

External same-path file changes require Off/On or a shading-scene edit; there is
no file watcher. Loader tests prove file-version reload, GPU tests prove
in-session resource costs, and GUI tests prove path changes/ownership. Off/On
creates a fresh delegate and its acceleration structures. No end-to-end external
overwrite image test, mip/footprint filtering, arbitrary graph or UDIM claim is
made by these tests.
