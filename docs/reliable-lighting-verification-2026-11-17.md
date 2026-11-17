# Reliable lighting in the integrated HoudiniRT viewer

The milestone is installed and verified in the regular Houdini 22.0.429 setup.
Final package: `reliable-lighting-h22.0.429-c6c4a05a45ca`; all 104 files reverified.

Phase 3 of the approved review roadmap adds native lighting, authored shading
normals, real diagnostic outputs and an on-demand scene-support report. Native
Houdini Light and Light Mixer parameters remain the light-editing interface.
Enable, requested/actual render state, Pause/Resume and Off restoration remain
in the same HoudiniRT panel and viewer controller. The camera-edit repair is retained.

## Rendering contract

| Feature | Implemented behavior |
| --- | --- |
| Distant | Local -Z emission; angle is angular diameter; delta and finite cones |
| Rectangle | Transformed half-width/half-height basis; one-sided emission |
| Disk | Transformed ellipse basis; one-sided emission |
| Sphere | Uniform scale without shear; outward surface emission |
| Dome | Uniform untextured radiance; invisible to primary camera rays |
| Light controls | Intensity, exposure, color, temperature, diffuse, normalize, visibility and transform |
| Sampling | Power-weighted next-event estimation and diffuse continuation with power-heuristic MIS |
| Authored normals | Constant, uniform, vertex, varying, face-varying and indexed data |
| Shading fallback | Generated smooth normals with a path-specific warning for invalid authored data |

Area normalization divides by world surface area, including scale. Distant
normalization uses the projected angular size prescribed by UsdLux, including
the zero-angle case. Small finite cones retain a separately computed solid angle
so float rounding of cosine does not turn a small normalized sun into a delta.
Finite emitters are visible opaque surfaces. Setting intensity to zero keeps their
coverage and occlusion; setting diffuse to zero keeps their primary emission.
Karma's renderer-specific light-geometry visibility and shadow switches default
off in Houdini 22.0.429. HoudiniRT does not evaluate those switches; the support
report calls out its visible/opaque finite-emitter convention at each affected path.
Light textures, shaping, filters, linking, shadow overrides and point-mode sphere
lights remain outside this milestone. The scene-support report identifies these
limitations by path using the installed renderer capability profile.

The renderer remains matte constant displayColor. MaterialX/OpenPBR material
graphs, textures/HDRI, instancing, subdivision refinement and denoising are later
roadmap work. The report explicitly distinguishes stage inspection from a native
translation census, and marks USD analytic-primitive conversion as unverified.

## Output contract

Beauty uses progressive box-filtered samples. Other outputs use one deterministic
pixel-center primary hit, generated without consuming beauty randomness. All bound
outputs publish under the same request-generation check. IDs are written as integers.

| Raw output | Value | Default miss |
| --- | --- | --- |
| color | Averaged scene-linear premultiplied RGBA | (0,0,0,0) |
| normal | World-space shading normal | (-1,-1,-1) |
| Neye | Eye-space shading normal | (-1,-1,-1) |
| depth | Projected depth | 1 |
| cameraDepth | Ray distance from eye, or orthographic near plane, in scene units | 0 |
| primId | Hydra primitive ID | -1 |
| elementId | Authored polygon index, preserved through triangulation | -1 |
| instanceId | -1; instancing is unsupported | -1 |
| albedo | Matte constant displayColor | (0,0,0) |

Analytic emitters have coverage and depth/normal data but no Hydra mesh selection
ID or matte albedo. Explicit binding clear values override defaults. Unsupported
raw output names or incompatible formats produce a render error before publishing.
The installed `UsdRenderers.json` declares `depthstyle: opengl` so Houdini interprets
the renderer's 0–1 projected depth correctly. Actual bound formats are validated;
husk may coerce an authored RenderVar format to a compatible buffer before binding.
Normal-only edits upload shading data without geometry or acceleration-structure
work. Light-only changes update the light table. Guide readback occurs once per
generation when requested; actual allocations and transfer bytes are reported.

## Acceptance record

The sealed repository exams stop at s5. This milestone therefore has supplementary
independent acceptance in addition to unchanged s5/s4 regression. No source files
under verify, specs, orchestrator or agents are modified.

The first supplementary run is retained at
`build/m5/runs/20261117T121449Z-analytic-dca454cf`: single-light controls passed,
but three mixed-light fixtures failed because the initial CPU reference treated
finite emitters as transparent and left the dome unoccluded. A separately frozen
v2 reference accounts for emitter visibility; acceptance tolerances are not relaxed.
The first harness also expected one transfer rather than the two actual beauty and
guide copies. Correcting that assertion does not change renderer counters.

Initial supplementary checks, before the final host/GUI integration fixes:

- HDK Release CPU tests: 2/2, including actual mesh Sync revisions, interpolation,
  indexed normals, holes/orientation, malformed fallback, coarse IDs, all guide
  fields, integer IDs above float's exact range, offset addressing and miss clears.
  Evidence: `build/m5/cpu`.
- Independent GPU lighting: 40 cases, three seeds each, plus five exact live/fresh
  comparisons. Worst channel mean error 0.1966%; worst pooled relative RMSE 2.1886%.
  Each stochastic mean passes 5 estimated standard errors plus the independently
  measured numerical/quadrature floor, as well as the unchanged fixed ceilings.
  All guide fields and actual transfer counters pass; validation errors/warnings
  are zero including teardown. Evidence:
  `build/m5/runs/20261117T122458Z-analytic-8a2d706c/{receipt,judge}.json`.
- Independent deterministic GPU edge checks: 14 cases covering corner seams,
  nonuniform/reflected transforms, camera coordinates, empty-scene finite emitters,
  zero-energy/diffuse behavior and malformed-input recovery. Normal-only changes
  submit zero BLAS/TLAS work and zero geometry-upload bytes. Validation remains
  zero including teardown. Evidence: `build/m5/edges/run-02/judge.json`.
- Extension regressions: Python 3.14 has 461 passed/1 skipped; Python 3.12 has
  444 passed/18 skipped; installed Houdini 22.0.429 hython USD checks have 4 passed.
  The 83 release-collected source files match before/after hashes. Evidence:
  `build/m5/python-tests/receipt.json`.

- Unchanged sealed s5/s4 passes with nonce `4277757661968090791`: both 11-frame
  edit sequences at 1M/4M ballast match 22 fresh renders. Edit costs, visible-scene
  census and device-local placement pass; Vulkan validation is silent. s4 Karma
  FLIP p99 is 0.0304 against 0.045. Native and all protected source hashes remain
  unchanged. Exact staged plugin and receipt:
  `build/m5/runs/20261117T122604Z-s5-4277757661968090791/`.

The first USD/Karma crosscheck is retained at
`build/m5/runs/20261117T122923Z-usd-723c3062`. All 15 HoudiniRT case groups matched
the independent reference; 14 matched Karma. The mixed reference alone needed
Karma's light-geometry visibility/shadow switches explicitly enabled to represent
the same scene. Unaffected reference images retain their exact hashes; the
corrected mixed comparison keeps the same acceptance tolerances.

Actual husk AOV testing found that a diagnostic log alone did not fail a batch
job: unsupported output requests initially exited successfully and saved a clear
EXR. The fix publishes generation-guarded `huskErrorStatus` through render stats,
as specified by [SideFX's Hydra integration contract](https://www.sidefx.com/docs/hdk/_h_d_k__u_s_d_hydra.html).
The focused corrected run (`build/m5/aov/run-04`) passes all five cases: unknown
outputs exit 1 with no image or publication; valid identity/rotated/indexed-normal
fixtures deliver all 18 typed channels with maximum error 3.8147e-7. OpenEXR stores
integer channels as UINT; the independent reader preserves those bits, including
the signed -1 sentinel represented as UINT_MAX. The first three diagnostic runs
are retained. The final exact-payload run is separate from these diagnostics.

## Final delivery and regular Houdini verification

Installed **reliable-lighting-h22.0.429-c6c4a05a45ca**, integrated controls v0.3.0,
through the existing normal-preference `zz_houdinirt.json`. All 104 installed
files were rehashed after the GUI run; 15 surrounding registrations are unchanged.
Native DLL SHA-256 starts `9792fc6c2218`. The package SHA-256 is
`03c9ac8e8274bd35f79aeb38b0c739ca683dcdec8b2d15436fd7ed68e33ce965`.
Previous payloads and exact package bytes remain available. The pre-milestone
camera-edit setup is recoverable from the package backup ending
`before-reliable-lighting-h22.0.429-3d0a1ee28eba.disabled`.

Final accepted proof paths, relative to this worktree:

| Check | Receipt |
| --- | --- |
| Sealed s5/s4 | build/m5/runs/20261117T124654Z-s5-4598160757163180536/receipt.json |
| Analytic GPU | build/m5/runs/20261117T125057Z-analytic-7a29b4dc/receipt.json |
| USD/Karma | build/m5/runs/20261117T125638Z-usd-f91914c8/receipt.json |
| GPU edges | build/m5/edges/final-01/execution_receipt.json |
| Actual husk AOVs | build/m5/aov/final-01/receipt.json |
| Extension and real HOM API | build/m5/python-tests-installed-fix/receipt.json |
| Installation | build/m5/install-c6c4a05a45ca/install_receipt.json |
| Regular GUI workflow | build/m5/installed/gui-delivery-03/result.json |
| Independent GUI images/events | build/m5/installed/gui-delivery-03/independent_judge.json |
| Live CUA observations | build/m5/installed/gui-delivery-03/cua_observation.json |
| Process preservation | build/m5/installed/gui-delivery-03/process_audit.json |

The final sealed gate repeats all checks after the husk error-status fix: s4
FLIP p99 0.0194, level error within 0.00030; all 22 live/fresh images exact.
Final extension counts are 464 passed/2 skipped on Python 3.14, 447 passed/19
skipped on Python 3.12, and five actual 22.0.429 hython checks. All 85 release
inputs match their proof hashes. Skip differences reflect unavailable host/runtime
interfaces; the actual HOM method contract runs in hython.

The regular GUI used native Light LOPs for all four new light types. The intended
viewer, installed DLL, profile, configuration discovery and effective OpenGL depth
settings were verified. Scene support inspected 16 prims, reported the deliberate
unsupported cylinder and three finite-emitter limitations, and became stale after
a native light edit. Pause acknowledged at 4,959/65,536 samples in 18.865 ms, held
8.003 seconds without work, and resumed the latest lighting edited while paused.
The other viewer stayed on Karma. Off restored previous paused Karma and the
original mixed grid settings. The owned process exited; preexisting apps were untouched.

Six scene-linear viewport EXRs pass the independently frozen physical-light
reference. Worst receiver channel mean error is 0.399%; worst per-pixel RGB NRMSE
is 10.463% at 256 spp. The exposure pair is exactly 2x. The fixture's two coplanar
receivers cannot illuminate each other, which justifies its direct-light oracle
despite the configured bounce budget. This is not a general scene benchmark.
Independent native-event timing over six camera edits gives p50 100.3 ms and
p95 104.4 ms, retaining the camera-delay repair. CUA visually observed the actual
paused controls, target viewer, support report and lit geometry; restoration is
proved by native readback and the independently audited event record.

Two earlier GUI attempts remain archived: delivery-01 caught an invented HOM
getter (`isSceneGraphStageLocked` instead of `sceneGraphStageLocked`). The narrow
fix now has an actual-HOM API contract test. Delivery-02 caught a harness race
between a sample-budget generation and the subsequent light-edit generation;
the final harness waits for both the current generation and changed scene before
capturing. Neither failure was accepted as successful verification.

Reproducible CPU, GPU, actual AOV and frozen independent judge sources are under
`tests/reliable_lighting/`. No protected exam or corpus files were edited.
