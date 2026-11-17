# Reliable materials, phase 4A — verification

The next bounded increment of roadmap phase 4 integrates Adobe OpenPBR into the
same HoudiniRT artist workflow. UVs, texture decoding/color conversion and HDRI
importance sampling remain subsequent increments. The accepted starting commit
is `b3fa7b5`; protected specifications and sealed exams are unchanged.

## Implemented scope

- Native Solaris Material Library → MtlX OpenPBR Surface → Assign Material.
  Material render contexts prefer `mtlx`, followed by the universal context.
- Whole-mesh opaque isotropic materials: base color/weight/metalness and specular
  color/weight/roughness/IOR. Colors and weights are bounded to [0,1], IOR to [1,3].
  Other inputs retain their declared defaults; diffuse roughness stays zero.
- CPU material/mesh binding snapshots and GPU shading-row updates; material-only
  changes do not upload geometry or rebuild acceleration structures.
- Adobe's pinned Apache-2.0 BSDF, texture LUT mode, full BSDF light MIS, and base-color
  AOV (`base_color * base_weight`). Existing accumulation and publication fencing remain.
- Path-specific unsupported-material diagnostics and bounded scene-support inspection.
  Shader graphs, textures, tagged color spaces and unsupported lobes use a declared
  whole-material matte fallback. Interface-input graphs are marked Not assessed:
  the host can flatten constants before they reach the delegate.

Authored roughness zero uses Adobe's numerical minimum 0.001. Separate light-lobe
weights are not implemented: use light diffuse/specular multipliers of one for
material-accurate results. The report identifies nondefault multipliers. Additional
lobes, per-face material assignments, textures and HDRI remain outside this slice.

## Verification

| Check | Evidence | Result |
| --- | --- | --- |
| Native HDK translation and USD ingress | `build/m6/cpu-03/receipt.json` | PASS: defaults/ranges, material and binding revisions, subset fallback, actual USD type metadata, color-space rejection, neutral displacement, interface flattening |
| Unchanged sealed s5 + s4 | `build/m6/runs/20261117T150108Z-s5-1889256396951641727/receipt.json` | PASS, nonce 1889256396951641727; 22 live/fresh images equal; 1M/4M transform medians 0.9247/1.0286 ms; s4 Karma FLIP p99 0.0247 < 0.045 |
| Open-interval BSDF randoms | `build/m6/rng-endpoints-02/receipt.json` and `pixel-correlation.json` | PASS: all 8,388,608 midpoints strictly interior; eleven independently predicted zero-PDF paths exactly identify the archived defective pixels |
| Integrated GPU materials | `build/m6/runs/20261117T150627Z-gpu-98bf5765/receipt.json` | PASS: 109 cases × 3 seeds, seven fresh controls, energy/reciprocity, finite-light MIS including grazing glossy endpoints, guide values and zero geometry/AS work on material edits |
| Real USD material transport and outputs | `build/m6/runs/20261117T150825Z-usd-026890e3/receipt.json` and `judge.json` | PASS for the qualified scope: 78 renders, twelve strict exact-scene CPU transport cases, native binding, analytic Lambert/fallback and albedo; non-Lambert Karma parity remains failed/diagnostic |
| Extension release and real host APIs | `build/m6/python-tests-bounded/receipt.json` | PASS: Python 3.14 494 passed/2 skipped; Python 3.12 477 passed/19 skipped; actual H22 hython 21 passed; 89 release inputs unchanged |

The exact sealed DLL SHA256 is
`0456559f31f8c371e61b5dcbe755df0374752efe8dcd227abe36a69983cb3b36`.
Its native/vendor source inventory contains 96 files; GPU proofs match the sealed
shader hashes. Vulkan validation, including teardown, was clean in these runs.

## Karma comparison and reference qualification

The initial strict parity run is **FAILED and retained**, not relabeled as a pass:
`build/m6/runs/20261117T143009Z-usd-116e2673/receipt.json` and `judge.json`.
Default beauty products were half-float, causing exact Lambert/fallback checks to
fail on quantization; explicit float32 output fixes the measurement without changing
its limits. Separate material differences remain: roughly 4% in the distant-light
default dielectric and up to 7.6% in the rough-metal rectangle case.

The exact OpenPBR 1.1.1 specification permits both view-only and reciprocal
glossy-diffuse approximations. Houdini's public MaterialX layer equations predict
0.4051539311 for the diagnostic distant-light case; Karma's half-float value is
0.4052734375. Adobe's CPU implementation predicts 0.4216699600; HoudiniRT's
half-float value is 0.4216308594. This supports a material-model distinction, not a
claim about Karma's private implementation. HoudiniRT is not a pixel-matching Karma mode.
The public MaterialX metal calculation does not fully predict Karma's metal result;
the precise cause of that private implementation's residual remains unresolved.
The pinned specification, source hashes, equations and both predictive results are
recorded in `tests/reliable_materials/model_qualification/README.md`.

The replacement acceptance reference uses the declared Adobe BSDF with separate
CPU scene transport and fixed-pixel quadrature; its numerical bounds are not widened.
CPU and GPU share the Adobe BSDF model, so that agreement is identified as integration
evidence and supplemented by analytic energy tests, reciprocity and independent
public-MaterialX calculations. All non-Lambert Karma comparisons remain visible as
cross-renderer characterization, including cases which happened to meet the initial bounds.

The first qualified float run, `build/m6/runs/20261117T145335Z-usd-cbe7bd99`,
is also retained as failed. It revealed both a wrong Karma output binding and a
real rare zero-PDF defect in the renderer. The exact-seed CPU reproduction matched
all eleven missing-sample pixels. OpenPBR randoms now use exactly representable
23-bit midpoints in (0,1); the legacy matte stream is unchanged. No acceptance
tolerance was widened. Karma's native float beauty uses LPE `C.*[LO]`, color4f,
and husk AOV name `C`; the final fixture carries separate canonical output settings
for the two delegates within one immutable scene.

The final candidate uses 4096 samples with completion checked in native publications.
Karma has an authored 4096 budget but can adapt its actual pixel sampling; its native
ray statistics remain in the logs. The corrected candidate's worst Lambert absolute
error is 0.000030768, below the unchanged 0.00005 bound, with no missing-sample pixels.
Across the twelve CPU-transport cases, the worst channel-mean relative error is
0.0657% and worst spatial normalized RMSE is 0.7548%. The final measured Karma
channel difference reaches 7.611% for rough metal and remains diagnostic.

## Regular installation

Installed at
`C:/Users/User/AppData/Local/MythAndLogic/HoudiniRT/reliable-materials-h22.0.429-744c84b9820d`.
The manifest binds 112 payload files to 185 native/vendor/extension source files
and seven verification receipts. All 15 surrounding registrations remain unchanged.
The existing regular `zz_houdinirt.json` pointer was replaced atomically; the prior
lighting payload and exact package bytes remain available. Installation receipt:
`build/m6/install-744c84b9820d/install_receipt.json`.
An independent read-only inventory audit also passes:
`build/m6/installed/independent_install_audit.json`.

Regular-GUI workflow and matching-host image acceptance pass, with the capture
qualification below. The final independent installation audit is
`build/m6/installed/independent_install_final_audit.json`.

The first regular-GUI attempt, `build/m6/installed/gui-accepted-01`, passed the
native control/event checks but failed its independent full-frame replay judge.
Its flipbooks included Solaris light wire guides and origin ticks even with
`beautyPassOnly(True)`. Those decorations are visible in the retained EXR/PNG
diagnostics and absent from fresh-render beauty. The failed images and judge remain
unchanged. Capture-only native light-guide visibility was saved, disabled and restored.
No image mask, pixel fitting, renderer adjustment or tolerance change was used.

The second run, `gui-accepted-02`, also passed all native workflow checks. Its
light-guide-free flipbooks still differed from raw husk images at fractional-coverage
edges (maximum alpha difference 0.250488). Disabling flipbook antialiasing, selection/
origin decorations and OCIO separately did not eliminate that difference. The failed
raw-output comparison is retained as `independent_husk_judge.json`, with its original
log and a hash-bound archive receipt. These diagnostic attempts remain separate from
acceptance; passing setup/restoration is not a passing image comparison.

A native **Karma CPU** control reproduces the same capture behavior: maximum alpha
difference 0.251465 against its own exact-stage headless output, while opaque interior
RGB agrees. The diagnostic alpha-over relation reduces its edge RMSE from 0.176256
to 0.002281; this is evidence of the capture transfer, not a correction applied to
any accepted image. Evidence is
`gui-karma-control-01/karma-reference-01/comparison.json`. That control's later,
unrelated inherited grid check failed; its already-completed Karma capture, exact USD,
settings and hashes are independently checked. The overall failed run is preserved.

Therefore a Houdini 22 beauty flipbook is not treated as a raw delegate AOV export.
The live-edit reference uses new normal-Houdini viewer/delegate sessions rendering
the exact exported stages through the same host capture path, with the original
full-frame numeric limits and judge unchanged. This establishes live-versus-fresh
artist workflow equivalence. Raw transport, physical/material limits and coverage
are established by the separate native GPU and headless USD tests above. The stock
Karma control qualifies the host distinction; it does not prove the private host
compositor's exact implementation. Raw/flipbook edge-alpha equivalence is not claimed.
The diagnostic qualification and twenty evidence hashes are retained in
`build/m6/installed/capture_reference_qualification.json`.

## Accepted regular-Houdini workflow

`build/m6/installed/gui-accepted-02/result.json` and the unchanged
`independent_judge.json` pass. All nine live captures match the fresh regular-Houdini
references **exactly in every decoded RGBA pixel**: baseline, base color, metalness,
roughness, IOR, specular weight, reassignment, unsupported connection and repair.
The fresh reference receipt is `fresh-gui-03/receipt.json` beneath that run folder.
Each stage used a new process and delegate, the exact installed DLL, identical
USD values/camera/settings and the same host capture path. No crop or pixel fitting.

The active render paused at 2,975 of 65,536 samples; native acknowledgement was
17.544 ms. It held for 8.00036 seconds without GPU work, accepted a material edit
while paused and resumed the latest state. Eleven material publications, the
second-viewer boundary, prior paused Karma CPU and mixed-grid restoration pass.
Four scripted camera edits measured 110.784/120.829 ms p50/p95 by the independent
judge. These fixture timings include publication observation/polling, not physical
screen latency. GPU/CPU tests establish the no-AS-work invariant; HOM does not
expose those counters to this GUI harness.

The frozen freshness audit's only failure was literal Windows slash spelling in
the preferences path. `independent_freshness_path_qualified.json` preserves that
failed audit, rehashes its inputs and verifies all nine pairs identify the same
existing directory using both `resolve()` and `samefile()`. No frozen judge or
numerical criterion changed. Reproduction is documented in
`tests/reliable_materials/FRESH_GUI.md`.

CUA observations cover the native OpenPBR Base/Specular controls beside the
integrated Paused/Resume panel, and the final Off/restoration transition. See
`gui-visual-01/cua_observation.json` and `gui-accepted-02/cua_observation.json`.
All owned GUI/headless processes exited; preexisting processes were preserved.
