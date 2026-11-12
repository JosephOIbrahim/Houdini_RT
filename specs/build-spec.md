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
**Scripted acceptance** (`verify/s3_delegate.sh`, sealed 2026-10-06, stage 7 amended the same day; its header is the full contract):
- Plugin builds from `src/hdRT/CMakeLists.txt` against Houdini 22.0.429 (pinned in the exam), installs into `build/plugin_stage`, and registers in a **hermetic hython** as exactly one HdRendererPlugin with displayName `HoudiniRT`, discovered from `build/plugin_stage` only (11/12/13/15).
- `husk -R <plugin id>` on the exam's stage writes a 256x256 EXR; OIIO reads it; the EXR header names the delegate, and the seed is echoed in the delegate log (and in the header as `hrt_seed` when husk carries it) (15/16). Amended 2026-10-06: the first version demanded the attribute `husk:render_stats`, which husk writes only for a renderer with a `UsdRenderers.json` entry (Karma). For any other delegate husk writes `__delegate` and each render stat as top-level EXR attributes. The exam accepts either form; in both, the header must name this plugin as the delegate.
- Pixel check: the image is the step 2 closed-box furnace with seed-chosen albedo, RGB emission and bounce count handed in through the RenderSettings prim. Each channel mean matches the analytic value `E * (1 - rho^(bounces+1)) / (1 - rho)` within 0.5% (16). Amended 2026-10-06: the earlier wording compared against a standalone kernel image, but no program produces one, and an analytic value is the more independent reference.
- Pause/Resume: checked statically only (HdRenderThread in use; IsPauseSupported, Pause, Resume overridden; render thread start/stop markers in order) (17). Amended 2026-10-06: the only scripted driver, UsdImagingGL.Engine under hython, crashes without a GL context on the workstation. **Live pause must be checked by a human in the Solaris viewport before s4.**
- Runs on the discrete GPU; validation layer proven active and silent (14/15). A staged `.spv` contains ray-query instructions (17).
**Boundary:** Camera and mesh may be hardcoded test values; no real scene sync yet; viewport interactivity is not measured.

## s4-first-light
**Deliverable:** Mesh and camera sync from Hydra; the usd-wg Utah teapot (CC0, `third_party/usd_wg_teapot/`, sha256-pinned) and a polygon table under one uniform dome light render through the delegate and match Karma CPU.
**Scripted acceptance** (`verify/s4_firstlight.sh`, sealed 2026-11-11; its header is the full contract):
- Mesh sync goes through `HdMeshUtil::ComputeTriangleIndices` and `Hd_SmoothNormals::ComputeSmoothNormals`, checked in C++ code with comments and `#if 0` blocks removed, and the two HDK headers must be included (17). Face-varying primvars are not exercised in s4.
- The nonce draws the scene: teapot pose with a non-uniform scale, a 9- to 14-sided table, camera orbit and focal length, both albedos, the dome's intensity, exposure and tint, and the bounce count (6, 7, 9, 10, 11 or 12). Settings `houdinirt:seed`, `houdinirt:spp` and `houdinirt:bounces` arrive as render settings.
- Translation census: mesh count, triangle count and world bounding box, plus `hrt_bounces`, reported through GetRenderStats, match the stage read through `pxr.Usd` and the drawn bounce count (16).
- The delegate's image is finite and premultiplied, and the dome is never visible to the camera (a miss is black, alpha 0). Exposure invariance: doubling dome intensity doubles mean beauty within 0.5% (16).
- Beauty vs Karma CPU (husk, 2,048 samples, no denoiser, one-pixel box), same stage: mean of the fully covered pixels within 1.0% per channel; FLIP 99th percentile, after compositing over the dome and log-encoding 12 stops, at most 0.045; mean alpha difference over the frame at most 0.0004 (16).
- Validation layer proven active and silent (14); seed and spp echoed and a discrete GPU named (15); render-thread start before stop in the first render (17).
- RETINA T0/T1 are not part of the gate: the exam stays hermetic (repo + Houdini only). They may run beside it as a non-gating witness.
**Boundary:** No textures, no materials beyond Lambert albedo from `displayColor`, no instancing, no importance-sampled dome, no denoiser, no edits while rendering. Bounce counting beyond `hrt_bounces` and smooth vs flat normals are not visible in this picture; the first is proved against Karma before landing, the second waits for a directional light (s5).

## s5-live-edits
**Deliverable:** Scene edits cost what they change. Each mesh has its own bottom-level acceleration structure in object space, the top-level structure holds one instance per mesh, edits are applied to the GPU as deltas classified by Hydra's dirty bits, the scene lives in device-local memory, and the delegate reports what each edit cost.

**Scripted acceptance** (`verify/s5_liveedits.sh`, to be sealed; its header will be the full contract):
- **No regression.** The s4 contract still holds on the s5 build: the exam runs the sealed `verify/s4_firstlight.sh` with its own nonce, and that run must pass (its exit code is passed through).
- **Edit sequence through husk.** The nonce draws a stage with the s4 teapot and table, a deforming grid, a hideable sphere, and a static "ballast" mesh. One husk session renders 8 or more frames (`-f 1 -n N`), and every frame from frame 2 on applies exactly one edit, in a nonce-drawn order. The edit kinds are: transform only, visibility off, visibility on, points moved with the same topology, topology changed, and `displayColor` changed. The delegate persists across husk frames, so each frame is an edit to a live scene.
- **Correct after every edit (16).** Each frame of the sequence matches a from-scratch render of that same frame (a separate husk run of that one frame, same seed and spp): the per-channel mean of covered pixels agrees within 0.2%, the FLIP 99th percentile is at most 0.01, and alpha coverage agrees to 0.0004. (The exam calibrates these bounds on unchanged code across 6 nonces before sealing, as was done for s4.)
- **Edits cost what they change (16)**, judged from per-frame render stats in the EXR header, as integer counters, not timings:
  - transform-only or visibility frame: `hrt_blas_builds` = 0, `hrt_blas_refits` = 0, `hrt_geometry_upload_bytes` = 0, `hrt_tlas_builds` + `hrt_tlas_updates` = 1;
  - points-moved frame: exactly one BLAS refit or build, and `hrt_geometry_upload_bytes` at most 1.1 times that mesh's vertex data;
  - topology frame: exactly one BLAS build, and the upload bounded by that mesh's vertex and index data;
  - `displayColor` frame: no BLAS work, and `hrt_geometry_upload_bytes` = 0 (the colour travels in a per-mesh table, counted in `hrt_table_upload_bytes`, at most 4 KB).
- **Cost does not follow scene size (16).** The same sequence runs twice: with a 1M-triangle ballast and with a 4M-triangle ballast. The counters above must be identical between the two runs. The delegate's `hrt_edit_ms` (from Execute seeing the new scene version to the first pass submitted) for transform-only frames is compared as the median over those frames: the 4M median at most 1.5 times the 1M median plus 0.5 ms.
- **Device-local scene (17).** `hrt_scene_memory` reports `device_local` for geometry, index, normal and table buffers. Host-visible memory is allowed only for staging and readback.
- **As before:** validation layer proven active and silent (14); seed, spp and a discrete GPU echoed (15); census stats (`hrt_mesh_count`, `hrt_triangle_count`, `hrt_bbox_*`) correct on every frame, including hidden meshes leaving the census (16).

**Boundary:** No prim insertion or removal mid-session beyond visibility (USD cannot time-sample a prim's existence; adding and deleting a prim while rendering is checked by a human in the Solaris viewport, together with live pause; decided 2026-11-11). No native or point instancing (the TLAS makes it cheap later; not in s5). Accumulation stays where s4 put it; device-local accumulation and batched submits are s6. No materials beyond per-mesh `displayColor` albedo. No absolute speed gate: timings are reported and compared as a ratio, never against a fixed number of milliseconds (the GPU is shared).
