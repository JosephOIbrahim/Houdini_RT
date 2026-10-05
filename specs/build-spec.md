# HoudiniRT — Build Specification (v1)

Hand-written, checked-in, stable. Each milestone: **Deliverable** (one sentence),
**Scripted acceptance** (what the trusted check asserts — this IS the exam's contract),
**Boundary** (what the milestone explicitly does not do). `◆GATE◆` milestones stop the
whole run on failure, by design: an unresolved unknown must not cascade.

Shared verification contract: nonce-keyed (argument 1 of every check is a fresh seed),
an independent reference the code under test cannot produce, evidence to
`verify/evidence.json`, distinct exit codes per stage (11 configure, 12 build/link,
13 binary missing, 14 validation layer, 15 runtime, 16 mismatch, 17 invariant, 90 unauthored).

---

## s1-furnace-4090 ◆GATE◆
**Deliverable:** The Vulkan compute white furnace for the OpenPBR BSDF passes on the RTX 4090 under MSVC.
**Scripted acceptance** (`verify/s1_furnace.sh` — sealed, runnable now):
- `verify/s1_build.bat` compiles `src/furnace/furnace.comp` with `glslangValidator -DFURNACE_TEX_LUTS` (≠ 11) and `src/furnace/furnace_host.cpp` with `cl /std:c++17` inside `vcvars64` (≠ 12). Output under `build/s1/`.
- `furnace_host --spv ... --tex-luts --seed <nonce mod 2^32-5> --csv build/s1/gpu_rows.csv --ref verify/furnace_cpu_reference.csv` runs with the validation layer on; its exit code passes through (14 validation, 15 Vulkan failure, 16 tolerance).
- The device line names a discrete GPU. A software device (llvmpipe, lavapipe, SwiftShader) fails (15).
- The run echoes the injected seed (`seed <N>:`), and the CSV is created fresh by this run (16 if absent).
- `verify/judge_furnace.py gpu_rows.csv furnace_cpu_reference.csv` exits 0: energy within tolerance, GPU agrees with CPU within 3σ + 0.001, MIS consistency < 1e-3 (else 16).
- Copy guard: more than 16 of 72 rows byte-identical to the reference albedo fails (16). Twelve Lambert rows are legitimately identical.
- Zero `VUID` / `Validation Error` / `leak` lines in the run log (14).
**Boundary:** No ray queries, no scene, no Hydra, no performance budget. If the pipeline compile time on the 4090 is long it lands in evidence for later reference, not in the verdict.

## s2-rayquery ◆GATE◆
**Deliverable:** The same standalone program traces inline ray queries against a static triangle scene with a host-built BLAS/TLAS, and its radiometry matches analytic references.
**Scripted acceptance** (`verify/s2_rayquery.sh` — to be sealed; frontier mode drafts it):
- Builds with `GL_EXT_ray_query` and the acceleration-structure extensions (≠ 11/12); device reports `rayQuery` support (15 otherwise).
- Hit-distance test: a seed-placed camera inside a unit cube reports first-hit distances matching the analytic ray–box solution to 1e-4 over N seed-chosen directions (16).
- Occlusion test: a seed-placed occluder blocks exactly the analytic set of shadow rays (16).
- Closed-box furnace: a Lambertian box of albedo ρ (seed-chosen in [0.3, 0.8]) with emission E on every face converges to L = E / (1 − ρ) within 1% + 3σ at the seed-chosen bounce budget (16).
- Validation layer silent (14). Independent judge script computes the analytic values; the program prints measurements only.
**Boundary:** No OpenPBR shading in this test (Lambert only), no Hydra, no textures, no importance-sampled dome.

## s3-delegate-shell ◆GATE◆
**Deliverable:** An HdRenderDelegate plugin for Houdini 22 renders the kernel's image on an HdRenderThread and husk writes it to an EXR.
**Scripted acceptance** (`verify/s3_delegate.sh` — to be sealed):
- Plugin builds against `%HFS%` (pinned version in the exam) and registers in a **hermetic hython** (ambient plugin paths masked; provenance asserted from `build/plugin_stage`) (11/12/15).
- `husk --renderer HoudiniRT` on a minimal USD stage writes an EXR; OIIO reads it; the header carries the renderer name and the seed (15).
- Pixel equivalence: the delegate's image for seed N matches the standalone kernel's image for seed N (mean abs diff < 1e-3 on a 256×256 constant-shading test) (16).
- Pause/Resume: a scripted edit while rendering returns from Pause only after the render thread is idle (log markers in order) (17).
- Validation silent (14).
**Boundary:** Camera and mesh may be hardcoded test values; no real scene sync yet; viewport interactivity is not measured.

## s4-first-light
**Deliverable:** Mesh and camera sync from Hydra; the usd-wg Teapot under one dome light renders through the delegate and matches Karma CPU within tolerance.
**Scripted acceptance** (`verify/s4_firstlight.sh` — to be sealed):
- Mesh sync goes through `HdMeshUtil` (triangulation, face-varying primvars) and `Hd_SmoothNormals` (grep-enforced, 17).
- Translation census: prim count, triangle count and bounding box reported by the delegate match the stage read through `pxr.Usd` (16).
- Exposure invariance: doubling dome intensity doubles mean beauty within 0.5% (16).
- Beauty vs Karma CPU (husk, high spp, no denoiser), both EXRs log-encoded, FLIP 99th percentile under the sealed threshold; camera, light scale and tint perturbed by the seed (16).
- Header conventions via RETINA T0; metrics via RETINA T1 (Synapse, Joe's own code).
**Boundary:** No textures, no instancing, no importance-sampled dome, no denoiser, no edits while rendering.
