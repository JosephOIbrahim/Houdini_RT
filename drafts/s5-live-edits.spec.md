# DRAFT: s5-live-edits (for Joe's review, not part of specs/ yet)

Drafted 2026-11-11 by Claude (the code author for s5). Once you approve it, the text under "Spec text" goes into `specs/build-spec.md`, and the `desc` line goes into `orchestrator/config.ts`. Under the separation-of-duties rule, a separate session drafts the exam (`drafts/s5-live-edits.check.sh`) from this text, and you seal it.

How to review (about 10 minutes): read the Deliverable and the seven acceptance bullets first. The design notes explain why each check is shaped the way it is. The open questions at the end are the decisions only you can make.

Evidence behind this draft: `D:\Houdini_RT\PLAN-realtime-2026-11-11.md`, sections 1, 2 and 8 (bench: every edit re-uploads the whole scene, 20 to 23 ms at 980k triangles).

---

## Spec text

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

**Boundary:** No prim insertion or removal mid-session beyond visibility (USD cannot time-sample a prim's existence; see question 3). No native or point instancing (the TLAS makes it cheap later; not in s5). Accumulation stays where s4 put it; device-local accumulation and batched submits are s6. No materials beyond per-mesh `displayColor` albedo. No absolute speed gate: timings are reported and compared as a ratio, never against a fixed number of milliseconds (the GPU is shared).

`orchestrator/config.ts` line:

```ts
    { id: "s5-live-edits",     gate: false, check: "verify/s5_liveedits.sh",  timeoutMs: 40 * 60_000,
      desc: "Edits cost what they change: one BLAS per mesh in object space, one TLAS instance per mesh, deltas from Hydra dirty bits (transform/visibility -> TLAS only; points -> one refit; topology -> one rebuild; displayColor -> one table row), device-local scene memory, per-frame cost counters in the EXR header. A multi-frame husk edit sequence matches from-scratch renders frame by frame, and the counters are identical at 1M and 4M ballast triangles. s4 still passes. The header of verify/s5_liveedits.sh is the full contract." },
```

---

## Design notes (why each check is shaped this way)

01. **husk frames are the edit harness.** The bench showed husk keeps one delegate across frames: meshes sync on frame 1 only, then the scene version moves each frame. So a time-sampled stage is a scripted sequence of live edits that runs headless and hermetic. That gives s5 its interactivity test without a GL context, which is what blocked s3's live pause check.

02. **Counters, not milliseconds, are the gate.** Other sessions share the GPU, and timings on a shared machine are noisy. Counts of BLAS builds, refits and uploaded bytes are deterministic and say exactly whether work follows the edit. The one timing check is a ratio between two scene sizes in the same exam run, with slack.

03. **From-scratch equality, not Karma, judges each edit.** An incremental scene must render the same picture as the same scene built cold. Karma is already the judge of the picture in s4, which still runs. Bounds are near-exact rather than bitwise, because a refit tree and a rebuilt tree can break ties between equally close hits differently.

04. **Points moved: refit or rebuild is the code's choice.** The exam allows either, as long as exactly one structure is touched. A refit is faster, but its quality drops after large deformations; the code can rebuild after N refits.

05. **The delegate must publish these stats** through `GetRenderStats`. s3's amendment records that husk writes them as top-level EXR attributes for a non-Karma delegate, and the s4 exam already reads them that way.

06. **Code shape this spec implies:**
    - an `HdRTScene` that keeps per-mesh records (object-space positions, normals, indices, transform, visibility, albedo, version) instead of a flattened world;
    - a render-thread "apply deltas" step;
    - a persistent device-local allocator with a staging ring;
    - per-mesh BLAS with `ALLOW_UPDATE`;
    - a TLAS rebuilt (or updated) from an instance array whose `instanceCustomIndex` points at a mesh table;
    - in the kernel, hit data from the mesh table (object-space normals transformed by the instance's matrix).

    `world.cpp`'s flatten goes away.

---

## Open questions (yours)

01. **Ballast sizes 1M and 4M triangles.** Recommended. Larger makes the ratio check sharper, but the exam then takes longer to generate and run (4M triangles in `.usdc` is about 100 MB on disk). It turns on how long you are willing to let the exam run: about 40 minutes is the proposed timeout.

02. **Is the edit-time ratio gating, or a witness only?** Recommended: gating with the stated slack (1.5 times plus 0.5 ms). It turns on whether a noisy shared GPU could fail a correct build. The counters remain the main gate either way.

03. **Prim insertion and removal.** Recommended: out of s5, and covered by your viewport check together with live pause (add and delete a sphere while rendering). It turns on whether you want a scripted check, which would need a second harness (a Python Hydra driver), and that is what failed for s3.

04. **Who drafts the exam.** Recommended: a fresh Claude session given only this spec and the s4 exam as a template, never the s5 code. It turns on the separation-of-duties rule (exam authors are not code authors).
