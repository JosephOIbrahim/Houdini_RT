# Notes for the step 4 exam (`s4-first-light.check.sh`)

Written by Claude on 2026-10-06, at Joe's request, for whoever seals this exam. Updated 2026-11-11
after the X2 changes and the calibration on the real teapot (X4).

## Status: FROZEN 2026-11-11, not yet sealed

**Frozen exam:** `s4-first-light.check.sh`, 54,416 bytes,
sha256 `5bd6027fc9843a71b1137aaeb0d9b9762278a987d405b3d01c563f46c8e0f3d4`.
Every later copy (`drafts\`, `verify/s4_firstlight.sh` after the seal, the copy any dry run uses) must
carry this hash. If the exam has to change, it is unfrozen, re-calibrated and frozen again under a new hash.

| Owed before the seal | State |
|---|---|
| The teapot file in the repo | **Done.** Committed on master `3fb3ad2` (Joe's yes, 2026-11-11) |
| X2: the pre-seal changes Joe approved | **Done** (see "What changed on 2026-11-11" below) |
| Calibrate `FLIP_MAX` on the real teapot | **Done.** 0.045 stands (6 nonces, see "Calibration on the real teapot") |
| Dry run against today's merged code: must stop at stage [2], exit 17 | **Done** (X5): integration `d369872` + the real teapot, nonce 13835058055282163713, exit 17 at [2] ("does not triangulate through HdMeshUtil::ComputeTriangleIndices in code"), 55 s |
| Replace the broken local-model draft in `drafts\` with this exam and these notes | **Done** (X7, 2026-11-11), hash checked on the copy |
| Spec s4 wording (lines 47-55) and config.ts:76 amended to match this exam, as its own commit | **Approved** by Joe 2026-11-11 (RETINA as a non-gating witness). Text: `_work\s4exam\x6_spec_draft.md`. Committed just before the seal |
| Live pause checked by a human in the Solaris viewport (`specs/build-spec.md` line 43) | Owed (X8, Joe). The how-to is in `D:\Houdini_RT\PLAN-2026-11-11.md` |

**Where the exam is.** The frozen copy is in `D:\Houdini_RT\HoudiniRT\drafts\` (X7), where the dashboard
offers it for review and the Seal button. The working copy stays in `D:\Houdini_RT\_work\s4exam\`, read-only.

**Who seals: Joe.** Claude wrote this exam, and a Claude Code session writes the step 4 code (Joe's
decision of 2026-11-11, DECISIONS.md). Separation of duties rules Claude out as the sealer.

## What happened to the local model's draft

The night model wrote a draft on 2026-10-06 between 17:17 and 18:13. A copy is kept in
`D:\Houdini_RT\_incoming\s4-draft-localmodel-2026-10-06\`. It could not be finished by editing:

- It stops inside stage [4]: no render, no judge, no PASS line. As it stands it exits 13 at stage [4]
  on any code that gets that far (the three calls below raise). With those calls repaired it would fall
  off its end with status 0 and pass anything that registers. The dashboard refuses to seal it (no PASS
  line).
- It calls three functions that do not exist (`AddPositionOp`, `AddLookAtOp`, `AddDisplayColorAttr`).
- It counts 1,023 triangles for a mesh that has 1,536.
- It renders a procedural blob. The spec names the usd-wg Teapot.
- It authors a `houdini:background:color` that would break the exposure check.
- It repeats the step 3 seed flaw: the nonce is unsigned 64-bit, bash arithmetic is signed, and half of
  all nonces come out as seed 1.

## What the exam does, stage by stage

| Stage | What it checks | Exit on failure |
|---|---|---|
| 0 | Houdini 22.0.429, cmake, spirv-dis, timeout, sha256sum, perl, the teapot file and its sha256, a decimal nonce | 13 |
| 1 | Configure, build (at most 600 s), install the plugin into a fresh `build/plugin_stage`. Every `HOUDINIRT_*` variable from the caller's shell is cleared first; only `HOUDINIRT_VALIDATION=1` is set back | 11, 12, 13 |
| 2 | Source: HdRenderThread, Pause, Resume; in C++ code, with comments and `#if 0` blocks stripped: the `pxr/imaging/hd/meshUtil.h` and `smoothNormals.h` includes and the calls `HdMeshUtil::ComputeTriangleIndices` and `Hd_SmoothNormals::ComputeSmoothNormals`; no GPL reference; a staged shader uses ray queries | 17 |
| 3 | Exactly one renderer plugin named HoudiniRT, found in the staging folder only | 15 |
| 4 | The exam writes the scene from the nonce (in Python, not bash) and takes its own census of it | 13 |
| 5 | husk renders the scene, then the same scene with the dome twice as bright (at most 300 s each) | 14, 15 |
| 6 | Validation layer proven on and silent; seed and spp echoed; a real GPU; render thread start before stop, judged inside the first render's log only | 14, 15, 17 |
| 7 | The delegate's own image: fresh, 384x216 RGBA, names this delegate; `hrt_bounces` equals the drawn bounce count; its census (mesh count, triangle count, world bounding box) equals the exam's; finite, premultiplied, dome not visible; 2x dome gives 2x mean within 0.5% | 16 |
| 8 | Karma CPU renders the same stage (path traced, 2,048 samples, no denoiser, one-pixel box), at most 120 s | 13 |
| 9 | Against Karma: mean of the fully covered pixels within 1.0% per channel; FLIP 99th percentile at most `FLIP_MAX` (0.045); mean alpha difference over the frame at most `DA_MAX` (0.0004) | 16 |

The scene: the teapot under a transform with a translation, two rotations and a non-uniform scale; one
polygon with 9 to 14 sides as a table; one uniform dome. Pose, table, camera orbit, focal length,
framing, both albedos, the dome's intensity, exposure and tint, and the bounce count (6, 7, 9, 10, 11 or
12; drawn last, so every earlier draw is what it was before the count was drawn) all come from the nonce.

Time limits: the build 600 s, each delegate render 300 s, the Karma reference 120 s. That is 1,320 s,
inside the milestone's 1,800 s. Karma CPU measured 4 to 11 s per reference on 2026-11-11 (with a second
calibration run competing for the machine); the step 3 delegate rendered in 3 s. Karma's limit is about
eleven times the slowest measurement rather than 1.5x: a 17 s limit would turn a busy machine into an
exit 13 "reference missing", which sends repair after the wrong cause.

For FLIP both images are composited over the dome's own radiance, then log-encoded over 12 stops. The
percentile runs over the pixels either image covers. Against a black background a silhouette pixel's
value is its coverage, and the log encoding turned that into the largest error in the picture.

## Facts probed on this workstation (Houdini 22.0.429, hermetic environment)

| Question | Answer | Probe |
|---|---|---|
| Which material is exactly Lambertian in Karma? | MaterialX OpenPBR with `specular_weight 0`: a sphere reads 0.5000 to 0.5002 from centre to rim on CPU, 0.4997 to 0.5003 on XPU. UsdPreviewSurface reads 0.3 to 0.4% low. A mesh with no material is not Lambertian at all (0.233 for an albedo of 0.5). | A, C |
| Is the dome visible to the camera? | No. A miss is black with alpha 0. | A |
| Is any colour conversion applied to light colour or albedo? | No. | A, rehearsal |
| Is the render linear in the light? | Yes. Twice the intensity gives exactly twice the image. | A |
| A polygon mesh with no authored normals: flat or smooth? | Smooth. The same picture as the mesh with smooth normals authored. `catmullClark` gets subdivided. | B |
| How does `karma:object:diffuselimit` count? | First hit plus D further diffuse bounces. Checked at 0, 1, 2 and 8. Limits 6 and 9 to 12 not probed directly; Karma XPU and CPU agree at each of them on the real teapot (X4). | D, X4 |
| What is `karma:global:pixelfiltersize`? | A box of size 2.0 is one pixel wide. Size 1.0 is half a pixel. The exam authors 2.0. | E |
| Does Karma shade a face seen from behind? Does such a face block light? | Yes to both, whatever `doubleSided` says. A quad seen from behind reads 0.4999 against 0.5000 from the front. A blocker facing down casts the same shadow as one facing up. | F |
| Is the dome's `exposure` honoured? | Yes. The independent tracer applies `2^exposure` and matches Karma's level to 0.1%. | rehearsal |
| Where does husk put the delegate's stats? | For our delegate: `__delegate` and one top-level EXR attribute per stat. For Karma: JSON in `husk:render_stats`. The judge reads both. | dry run |
| How long does the reference take? | Karma CPU, 384x216, 2,048 samples: 5 to 12 seconds (stand-in); 4 to 11 seconds on the real teapot. | rehearsal, X4 |

Houdini's Python has numpy and OpenImageIO but neither scipy nor the `flip-evaluator` package. The judge
therefore carries LDR-FLIP itself: a numpy port of NVIDIA's reference (github.com/NVlabs/flip, BSD
3-Clause). The port matches `flip-evaluator` to 5.1e-5 over 13 image pairs.

## What changed on 2026-11-11 (X2, approved by Joe)

| Item | Change | Why |
|---|---|---|
| (a) | Time limits: build 600 s, delegate renders 300 s each, Karma 120 s (was unbounded / 600 / 600 / 900) | The old sum exceeded the 1,800 s milestone limit; a Karma timeout now says so instead of blaming the install |
| (b) | Judge [9]: mean alpha difference against Karma at most `DA_MAX = 0.0004` | FLIP alone let a pixel filter half a pixel wide through |
| (c) | Stage [2]: the mesh and normal checks read C++ code only (whole files; `/* */`, `//` and `#if 0` removed) and require the two HDK includes | A comment naming the calls used to satisfy the check, and the picture cannot tell flat normals from smooth |
| (d) | Render-thread order judged inside the first render's log only (cut by byte length) | A stop from the second render could stand in for a first render that never stopped |
| (e) | `vuid_count` evidence: `|| true` instead of `|| echo 0` | The old form wrote `0\n0`, invalid JSON |
| (f) | Clear `OCIO` and every `HOUDINIRT_*` variable before the run; set back only `HOUDINIRT_VALIDATION=1` (the prefix sweep approved by Joe on 2026-11-11) | `HOUDINIRT_DEBUG_OPEN_FACE` removes a wall from the scene; a stray switch must not steer the run under test |
| (g) | Bounce count drawn from the nonce (6, 7, 9, 10, 11, 12; never 8, the old fixed value) and reported as `hrt_bounces` (int, compared exactly) | A hardcoded count of 8 used to match every run |

Two independent reviewers attacked the first version of these edits. Their findings (block-comment and
`#if 0` cheats, a valid `*out = ...` call line rejected, the log-slice edge case, the skip2 rehearsal no
longer skipping, the old bounce draw including 8) were fixed before calibration. The harness changed
with it: `cases.sh` (timed renders, `DA_MAX` to judge [9], two `hrt_bounces` header cases, the
half-pixel filter now expected to fail), `mkfake.py` (`hrt_bounces` in the fake header),
`extract_heredocs.py` (`DA_MAX`), `env.sh` (the same clearing as the exam), `dry.sh` (skip2 removes the
new stage [2] lines). Pre-change copies: `*.pre-x2-20261111`. Full diff: `probe\x2.diff`.

## Calibration on the real teapot (X4, 2026-11-11)

`cases.sh` on the committed usd-wg teapot, six nonces, with the exam's own judge code (byte-identical
to the frozen exam's). Karma XPU stands in for a correct delegate; Karma CPU with another seed is a
second correct image; each wrong row is Karma XPU rendering a deliberately wrong stage, or a correct
image with one header fault. **0 BAD rows on all six nonces.**

| Nonce | Bounces drawn | Correct, Karma XPU: FLIP p99 / level / alpha | Correct, Karma CPU other seed | Karma CPU reference time |
|---|---|---|---|---|
| 101 | 9 | 0.0195 / 0.016% / 0.00005 | 0.0179 / 0.004% / 0.00005 | 6 s |
| 918463680293402384 | 9 | 0.0241 / 0.039% / 0.00006 | 0.0234 / 0.011% / 0.00007 | 4 s |
| 16589207745918490877 | 10 | 0.0161 / 0.013% / 0.00005 | 0.0154 / 0.005% / 0.00005 | 7 s |
| 7 | 12 | 0.0206 / 0.057% / 0.00006 | 0.0205 / 0.005% / 0.00006 | 5 s |
| 12297829382473034410 | 11 | 0.0130 / 0.016% / 0.00003 | 0.0128 / 0.007% / 0.00003 | 11 s |
| 5 | 6 | 0.0182 / 0.035% / 0.00005 | 0.0182 / 0.014% / 0.00005 | 7 s |

**Rule applied (from the plan): keep `FLIP_MAX = 0.045` only if there is no BAD row, correct rows stay at or
under 0.030 FLIP, 0.2% level and 0.0002 alpha, and every row that must fail does.** Worst correct row:
FLIP 0.0241, level 0.057%, alpha 0.00007. Every must-fail row failed. **0.045 stands; `DA_MAX = 0.0004`
stands.** Both are Karma-against-Karma numbers (plus the tracer on the stand-in in October): revisit
both once a real step 4 delegate has rendered the scene.

Wrong renders on the real teapot, six nonces each:

| Wrong render | Result | What caught it |
|---|---|---|
| Albedo 5% low | FAIL 6/6 | level 5.2 to 5.5% low |
| Albedo 3% high | FAIL 6/6 | level 3.1 to 3.3% high |
| Light 3% low | FAIL 6/6 | level 3.0% low |
| Light 2% high | FAIL 6/6 | level 2.0% high |
| Camera one pixel off | FAIL 6/6 | FLIP 0.157 to 0.221 |
| Camera half a pixel off | FAIL 6/6 | FLIP 0.098 to 0.136 |
| 1 bounce instead of the drawn count | FAIL 6/6 | level 1.3 to 2.4% low (FLIP 0.090 on one) |
| Teapot's scale ignored | FAIL 6/6 | level 1.6 to 2.8% or FLIP 0.17 to 0.37 |
| Table missing | FAIL 6/6 | level 3.9 to 16% high |
| **Pixel filter half a pixel wide** | **FAIL 6/6 (new)** | alpha 0.00074 to 0.00089 against the 0.0004 ceiling (on two nonces FLIP 0.045 and 0.050 got there first; the alpha gate alone catches those too) |
| Subdivided instead of the polygon cage | FAIL 6/6 | FLIP 0.067 to 0.088; the census also catches it |
| **2 bounces instead of the drawn count** | **not caught** | FLIP 0.025 to 0.040; level 0.10 to 0.36% low |

Fourteen header and census cases, each a correct image with one thing wrong, fail at stage [7] on all six
nonces: the ten listed below, plus `hrt_bounces` one off the drawn count and `hrt_bounces` missing.

A first X4 run was stopped when the bounce draw changed; its outputs are in `probe\superseded_x4_v1\`.
Stopping it left its inner loop running, which collided with the restarted run on nonces 7 and
12297829382473034410; those two tables are in `probe\superseded_x4_collided\` and both nonces were run
again alone. Logs: `probe\x4_run.part1.log`, `probe\x4_run.part2.log`.

## Evidence before X2 (2026-10-06): the exam runs, as far as it could then

Kept as the record. These runs used the pre-X2 exam (bounces fixed at 8, no alpha gate, plain greps).

Two end-to-end runs in a throwaway export of the step 4 branch (`dry.sh`), with the stand-in mesh where
the teapot file will be. Nothing in the repo was written.

| Run | Result |
|---|---|
| The exam as written, against today's code | Stops at stage [2], exit 17: "src/hdRT does not triangulate through HdMeshUtil::ComputeTriangleIndices (the delegate does not sync meshes yet)". 44 seconds. This is the first ticket the night model will get. |
| The same with the two new source checks switched off, so today's step 3 delegate carries on | Stages [3] to [6] pass through real husk: registered, rendered twice, validation active, seed echoed (nonce 13835058055282163713, above 2^63, became seed 4, not 1), RTX 4090. Stage [7] reads the real header and stops, exit 16: "the census is incomplete: hrt_mesh_count is not in the EXR header". 57 seconds. |

Stages [8] and [9] cannot be reached without a step 4 delegate. They were run through `cases.sh`, which
feeds the exam's own judge code real Karma renders. Those tables follow.

## Evidence before X2: correct renders must pass

Ceiling 0.045. Level tolerance 1.0%. Stand-in mesh, Karma CPU as the reference.

| Scene (nonce) | Compared with Karma CPU | FLIP p99 | Level, worst channel |
|---|---|---|---|
| 101 | Karma CPU, another seed | 0.0188 | 0.02% |
| | Karma XPU | 0.0188 | 0.03% |
| | Independent path tracer | 0.0176 | 0.08% |
| 918463680293402384 | Karma CPU, another seed | 0.0262 | 0.01% |
| | Karma XPU | 0.0255 | 0.05% |
| | Independent path tracer | 0.0219 | 0.09% |
| 16589207745918490877 | Karma CPU, another seed | 0.0162 | 0.00% |
| | Karma XPU | 0.0161 | 0.03% |
| | Independent path tracer | 0.0159 | 0.08% |
| 7301994586217305233 | Karma CPU, another seed | 0.0166 | 0.01% |
| | Karma XPU | 0.0169 | 0.04% |

The independent path tracer is about 200 lines of Python written from the exam's contract alone (Embree
for the rays; it lives in Claude's workspace, not in the repo). It stands in for an honest step 4
delegate. It scores the same against Karma as Karma scores against itself.

On the **real teapot** only the tracer has run, against itself with another seed. That is the noise
floor of the judge on the real geometry. It is not agreement with Karma.

| Real teapot scene (nonce) | Tracer, seed A against seed B: FLIP p99 | Frame covered |
|---|---|---|
| 101 | 0.0160 | 16% |
| 918463680293402384 | 0.0155 | 12% |
| 16589207745918490877 | 0.0148 | 31% |
| 7 | 0.0172 | 21% |
| 12297829382473034410 | 0.0160 | 15% |

At most 0.04% of the covered pixels are darker than a twentieth of the dome, so the log encoding has no
deep shadows to magnify.

## Evidence before X2: wrong renders must fail

Against Karma CPU on the stand-in scenes, with the pre-X2 judges (no alpha gate). The numbers come from two sources: the path tracer made wrong
on purpose, and Karma XPU rendering a stage made wrong on purpose and sent through the exam's own judges.

| Wrong render | Result | What caught it |
|---|---|---|
| Direct light only | FAIL | FLIP 0.19 to 0.33; level 5 to 9% low |
| 1 bounce instead of 8 | FAIL | FLIP 0.08 to 0.11; level 1 to 3% low |
| Albedo 5% low | FAIL | level 5.2 to 5.7% low |
| Albedo 3% high | FAIL | level 3.1 to 3.3% high. FLIP alone is 0.042 to 0.048: not enough. |
| Light 3% low | FAIL | level 3.0 to 3.1% low. FLIP alone is 0.043. |
| Light 2% high | FAIL | level 1.9 to 2.0% high. FLIP alone is 0.035. |
| Camera one pixel off | FAIL | FLIP 0.14 to 0.18 |
| Camera half a pixel off | FAIL | FLIP 0.09 to 0.11 |
| No antialiasing | FAIL | FLIP 0.061 |
| Teapot's scale ignored | FAIL | FLIP 0.34 on one scene; level 3% off on the other |
| All mesh transforms ignored | FAIL | FLIP 0.40 |
| Table missing | FAIL | level 2 to 7% high; FLIP 0.44 |
| **2 bounces instead of 8** | **not caught** | FLIP 0.027 to 0.046; level 0.3 to 0.7% low |
| **Pixel filter half a pixel wide** | **not caught** | FLIP 0.031 to 0.041 |
| **Subdivided instead of the polygon cage** | **caught on one scene of two** | FLIP 0.037 and 0.049. The census catches it every time: the triangle count changes. |
| **Flat normals instead of smooth** | **not caught** | FLIP 0.018 to 0.022, the same as a correct render |

Ten header and census cases, each a correct image with one thing wrong, all fail at stage [7] with a
message that names the fault: wrong triangle count; one mesh missing; bounding box 2% off; a census key
missing; the header names another delegate; the header names no delegate; the header seed is the default
1; a 2x dome that gives 1.94x; the 2x render being the 1x render again; a dome visible to the camera.

The same wrong renders on the **real teapot** (nonce 918463680293402384), tracer against tracer, no
Karma. The noise floor on this scene is 0.0155.

| Wrong render | FLIP p99 | Level | Would the two gates fail it? |
|---|---|---|---|
| Direct light only | 0.275 | 6 to 7% low | yes |
| 1 bounce instead of 8 | 0.152 | 2% low | yes |
| Albedo 5% low | 0.065 | 5.4% low | yes |
| Albedo 3% high | 0.048 | 3.3% high | yes |
| Light 3% low | 0.038 | 3.0% low | yes, by level |
| Light 2% high | 0.031 | 2.0% high | yes, by level |
| Camera one pixel off | 0.157 | same | yes |
| Camera half a pixel off | 0.096 | same | yes |
| No antialiasing | 0.065 | same | yes |
| All mesh transforms ignored | 0.39 | 2 to 3% high | yes |
| Table missing | 0.48 | 8% high | yes |
| 2 bounces instead of 8 | 0.039 | 0.2 to 0.3% low | no |
| Pixel filter half a pixel wide | 0.041 | same | no |
| Flat normals instead of smooth | 0.026 | 0.1% high | no |

The real teapot behaves like the stand-in: the same renders fail, the same three slip through.

## Honest limits

- **Normals.** Under a uniform dome a Lambertian surface is as bright whichever way its shading normal
  leans. The picture cannot tell smooth from flat. The source check (code only, includes required)
  stands in until a later step brings a light that has a direction (the recommended step 5).
- **Source reading.** Stage [2] strips comments and `#if 0` blocks, but code inside any other
  preprocessor branch that is never compiled still counts.
- **Bounce count.** 2 bounces instead of the drawn count is 0.1 to 0.4% darker on the real teapot.
  Neither gate sees that. `hrt_bounces` proves the setting was read, not honoured, and a delegate that
  reports a constant from the drawn set matches one nonce in six. Bounce counting is proved outside
  the exam, against Karma probe D's limits, before landing.
- **Pixel filter width.** Caught since X2 by the alpha gate (0.00074 to 0.00089 against 0.0004).
- **One reference.** Karma CPU is the only judge. Its agreement with Karma XPU is measured above on the
  real teapot, and with the independent tracer on the stand-in.
- **Thresholds from Karma only.** `FLIP_MAX` and `DA_MAX` were calibrated with Karma standing in for the
  delegate. A real delegate with independent jitter carries more noise in alpha at silhouettes than
  Karma's sampler; the headroom is about 6x on alpha and 1.9x on FLIP.
- **Pause and resume** are still checked by reading the source only.
- **The framing is loose.** Teapot and table cover 12 to 33% of the frame, so 10,000 to 27,000 pixels
  are judged. Loose framing guarantees empty pixels and a whole table in view.
- **No step 4 delegate has run it.** Stages [8] and [9] have only seen Karma and the tracer.

## Where this differs from the spec text

For Joe to accept or change when the exam is sealed.

1. **RETINA T0 and T1 are not used** (spec line 54). The exam runs in a hermetic Houdini environment
   with nothing but the repo and Houdini itself. Synapse is not in the repo and changes on its own
   schedule. The judge reads the header with OpenImageIO and computes FLIP itself. If Joe wants RETINA
   in the loop, the clean way is a second, non-gating witness that prints its numbers beside the
   judge's. Spec line 54 should be amended in the seal commit either way.
2. **A level check was added**: mean of the fully covered pixels within 1.0% per channel. FLIP alone
   lets a 2 to 3% error in the light or the albedo through.
3. **"Prim count" is the mesh count** (2). The triangle count and the bounding box carry the weight.
4. **The teapot is rendered as its polygon cage** (`subdivisionScheme = none`), 1,236 quads, with a
   table under it. The asset says `catmullClark`. The exam overrides it so that Karma and the delegate
   are given the same polygons.
5. **Face-varying primvars are not exercised.** Nothing in this scene needs one. The spec line names
   them as part of what HdMeshUtil is for.
6. **New things the delegate must provide:** the setting `houdinirt:bounces` (drawn 6, 7, 9 to 12; keep
   the delegate's own default outside that set), and nine stats (`hrt_bounces`, `hrt_mesh_count`,
   `hrt_triangle_count`, `hrt_bbox_min_x/y/z`, `hrt_bbox_max_x/y/z`) beside `hrt_seed`. The step 3
   furnace settings are no longer authored, and the step 3 furnace image is retired (Joe, 2026-11-11).
7. **An alpha-coverage gate was added** (judge [9], `DA_MAX`), and the source check reads code only.

## Before sealing: the list

1. ~~Joe's yes for the teapot file; commit it with a README.~~ Done: master `3fb3ad2`.
2. ~~Calibrate on it.~~ Done: six nonces, 0.045 stands (see above).
3. ~~X5: dry run against integration `d369872`; must stop at [2], exit 17.~~ Done 2026-11-11.
4. X6: spec s4 wording approved by Joe 2026-11-11 (RETINA as a non-gating witness). It is committed on
   its own, immediately before the seal, from `_work\s4exam\x6_spec_draft.md`.
5. ~~X7: copy this exam and these notes into `drafts\`; check the hash on the copy.~~ Done 2026-11-11.
6. X8: Joe checks live pause in the Solaris viewport.
7. X9: Joe seals from the dashboard. Before pressing Seal: `feat/s4-first-light` committed and clean,
   and the sha256 of `drafts\s4-first-light.check.sh` equals the frozen hash. After the seal, do not
   press Start on the dashboard while Claude writes the step 4 code.

Working files: `D:\Houdini_RT\_work\s4exam\` holds the probes (A to F), the harnesses (`cases.sh`,
`dry.sh`, `variants.py`, `mkfake.py`, `x4_calibrate.sh`) and their output tables
(`probe\*.table.txt`; the real-teapot calibration is `probe\cases_real_<nonce>.table.txt`).
Freeze record: `probe\x4_frozen_sha256.txt`.
