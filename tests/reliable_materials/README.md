# Bounded opaque OpenPBR validation

This supplementary acceptance is separate from the unchanged sealed s1 and s5/s4
regressions. It targets the actual delegate kernel and native material workflow.
No protected exam or vendor implementation is edited by the test author.

## Scope

The initial model accepts constant base color/weight/metalness and specular
color/weight/roughness/IOR. Diffuse roughness is zero. Geometry is opaque and the
BSDF isotropic; transmission, subsurface, coat, fuzz, emission, opacity, textures,
normal maps and arbitrary connected graphs are outside this slice. Unsupported
content must be identified with its USD path and explicit matte fallback.

## Independent evidence

1. Integrated white-furnace images: lossless white dielectric/metal, roughness
   0/0.1/0.5/1, and view cosine 1/0.7/0.3/0.1. Expected radiance is exactly one.
   Black, colored and weighted Lambert controls have analytic expectations.
2. Absorbing-material energy: no channel generates energy in the admitted
   parameter range; independent CPU sampling carries per-channel standard errors.
3. Directional illumination: actual Vulkan images under a delta distant light
   compare against independently compiled CPU BSDF evaluations. CPU uses array
   LUTs; production must use texture LUTs. This shares Adobe's material model and
   therefore establishes integration agreement, not independent physical truth.
4. Reciprocity: re-prepare with exchanged incident/outgoing directions and divide
   evaluation by its incident cosine before comparison. Only admitted uncoated
   opaque lobes are judged. Adobe documents nonreciprocal default coat/fuzz layers;
   they are excluded rather than assigned a misleading reciprocity requirement.
5. Sampling consistency: sample weight agrees with the full BSDF evaluation/PDF;
   rejected zero-PDF samples contribute zero to the estimator. Returned lobe type
   never substitutes for the full-mixture PDF in light MIS.
6. Independent renderer diagnostics: actual native MaterialX OpenPBR USD scenes
   compare against Karma CPU under a dome, distant and finite emitters. Low
   roughness, exact roughness zero, colored metal, dielectric IOR and mixed lights
   distinguish sampling errors from diffuse-only plausibility.
   Adobe's pinned implementation disables ideal delta lobes and clamps authored
   zero roughness to 0.001. Endpoint cases retain strict integrated image checks;
   the CPU reference records its sharp-lobe floating-point PDF discrepancies.
   The first strict glossy Karma parity run failed and remains archived. Pinned
   OpenPBR1.1.1 permits different glossy-diffuse and multiple-scattering models;
   all non-Lambert Karma rows are now appearance diagnostics, including rows
   which happened to meet the original parity limits. See `model_qualification`.
   Exact-USD candidate acceptance uses independent CPU transport around the
   specified Adobe BSDF model, with unchanged numeric bounds and recorded SE.
7. Live material changes: current-generation images match fresh renders, and
   constants/bindings require no BLAS/TLAS work or geometry upload. Shared material
   edits, rebind, removal, unbind and explicit fallback exercise ownership.
8. Installed artist workflow: native Material Library/OpenPBR/Assign Material
   nodes, actual panel buttons, supported edits while active and paused, current
   image evidence, support-report staleness and path-specific limitations, intended
   viewer isolation, previous renderer/pause/grid restoration, and clean exit.

## Reference discipline

`analytic` holds the independent fixture generator, CPU reference driver, Vulkan
probe and separate judge. Freeze fixtures, source hashes and tolerances before any
candidate GPU output. Archive failed runs unchanged. Numerical/model corrections
need explicit evidence and a new frozen contract; a failed candidate never selects
its own reference or tolerance. Record per-channel uncertainty for statistical rows.

The CPU reference depends on pinned Adobe OpenPBR 1.1.1 source (Apache-2.0) and GLM
1.0.1 commit `0af55ccecd98d4e5a8d1fad7de25ba429d60e863` (MIT option). GLM is a test
dependency under ignored `build/m6/deps`, not a new renderer runtime dependency.
Its source is the official repository archive and all extracted headers are hashed.

`usd/native_openpbr.usda` is the exact native Material Library/OpenPBR/Assign Material
fixture exported by Houdini 22.0.429 (SHA-256
`3bdf54d721ad1790208b68ab12169b54626daa15a9b987168c316bc6b2fde94d`). The generator copies
its native material subtree, including the neutral displacement terminal, instead
of substituting a hand-invented graph. It authors explicit constants into copies.

Do not confuse the original standalone s1 furnace with integrated rendering proof.
The existing s1 CPU/GPU implementation already shares Adobe's model. Analytic
limits, invariants and separately implemented Karma images provide complementary
checks. Run sealed exams only from isolated copied runboxes.

The `usd` pack explicitly requests float32 beauty. One immutable USD contains
equivalent scene/transport settings with renderer-specific output bindings:
Hydra raw `color`, and the native Karma beauty LPE `C.*[LO]`. `husk --settings`
selects the appropriate product. The canonical native Karma Render Var export is
retained as `usd/native_karma_vars.usda`. A raw Hydra color request is not a Karma
beauty request; the failed blank-output diagnostic is retained separately.

`installed` freezes the GUI acceptance contract before any GUI material images.
`run_replay.py <result.json>` produces raw husk references. Those initial full-frame
comparisons failed at fractional-alpha edges: native Karma reproduces the same
Houdini 22 flipbook/raw-output distinction. Retain these failures and the independently
bound control in `build/m6/installed/capture_reference_qualification.json`; do not
correct pixels, crop edges or loosen the original limits.

The matching-context workflow reference uses `installed/run_fresh_gui.py --prepare
<result.json> --label <new-label>`, followed by `--run <prepared-directory>` after
reviewing its frozen inputs. It launches one fresh normal-Houdini process/delegate
for each exact exported stage, using the installed payload and captured scene,
camera, settings and display path. The exclusive GPU slot is required. Run H22
`hython -B judge_fresh_gui_reference.py <fresh-reference/receipt.json>` to audit
freshness and provenance, then the unchanged `installed/judge_gui.py <result.json>
<fresh-reference/receipt.json>` for full-frame pixels and workflow events. Archive
the original failed husk judge/log before that judge writes its new output.
`FRESH_GUI.md` also documents the separate filesystem-identity qualification for
the frozen freshness audit's Windows slash-spelling comparison defect.

This establishes live-versus-fresh viewport/control delivery, not raw-AOV export
equivalence, an independent BSDF implementation, or Karma appearance parity.
Raw coverage and material transport are tested separately by the GPU/USD packs.
