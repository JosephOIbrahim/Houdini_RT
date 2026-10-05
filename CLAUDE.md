# HoudiniRT — Operating Contract (v1)

Read this first, every run. Source of truth. Kept under ~2500 tokens.

## What we are building
A **path-tracing Hydra render delegate for Houdini 22 (Solaris)**: one GLSL compute
megakernel using **Vulkan inline ray queries** (`GL_EXT_ray_query`), shaded by
**Adobe's OpenPBR BSDF** (`openpbr-bsdf`, Apache 2.0, included from `$OPENPBR`),
rendering progressively on an **HdRenderThread** into an HdRenderBuffer.
First light = a static USD scene under one dome light converging in the viewport.

## Build order (WIP = 1, in this order)
s1 furnace on the 4090 → s2 ray queries, standalone → s3 delegate shell in Houdini 22 →
s4 mesh + camera sync (first light). `specs/build-spec.md` holds the acceptance criteria.

## Verification contract — how you are judged
- **A deterministic script judges you, not a model.** The orchestrator restores `verify/` from a
  trusted snapshot before every check; editing it is wasted effort.
- **Nonce-keyed:** the check passes a fresh seed; the program under test must use it and the
  exam cross-checks against an **independent reference** (CPU table, analytic value, Karma EXR).
- **The program never grades itself.** It writes a CSV or image; a separate judge decides.
- Exit codes localize the stage: 11 configure, 12 build/link, 13 binary missing,
  14 validation layer, 15 runtime, 16 mismatch, 17 invariant, 90 exam not authored.
- Iterate locally with `bash <check> $RANDOM`; commit only green. Evidence lands in `verify/evidence.json`.

## Hard rules
- **Vulkan validation layer silent.** Zero VUID lines in any log.
- **No hardcoded device or queue indices.** Query, prefer a discrete GPU, never a software device.
- **RAII or explicit teardown on every Vulkan object.** No leaks at exit.
- **Acceleration structures are built in Execute, never in Sync.** Sync marks dirty.
- **Execute never blocks on the render thread.** Pause must really pause (Houdini pauses on edits).
- **Mesh sync uses HdMeshUtil and Hd_SmoothNormals.** No hand-rolled triangulation.
- **Texture-LUT mode for the BSDF** (`-DFURNACE_TEX_LUTS` / `OPENPBR_USE_TEXTURE_LUTS`). Embedded-array mode only as a diagnostic.
- **No GPL code**, in the repo or in your context. gatling is off limits. MIT/Apache/BSD/TOST only.
- **Integer or deterministic references where fidelity is asserted.** Statistical rows carry a standard error and are judged with it.

## Toolchain (Windows 11)
- MSVC (`cl`) via `vcvars64.bat`, Vulkan SDK (`%VULKAN_SDK%`), `glslangValidator`, Python 3, Git Bash.
- Houdini 22 at `%HFS%` for s3+ (hython, husk). The exam pins the version it probed.
- RTX 4090, 24 GB. The language model is unloaded before each exam; during your own local runs it may hold ~20 GB.

## Layout
- `src/furnace/` — step 1 kernel and host (`furnace.comp`, `furnace_host.cpp`). Step 2 grows here.
- `src/hdRT/` — the delegate (s3+). `verify/` — the exams (read-only to you). `drafts/` — candidate exams awaiting human review.
- `DECISIONS.md` — durable lessons, dated. `claude-progress.md` — what is done and the current delta.

## Agent invariants
- **WIP = 1.** One milestone per Generator run. Adjacent ideas → one line in `claude-progress.md`.
- One **atomic commit** per sprint; update `claude-progress.md` with the delta.
- Repair mode fixes **only** the ticket. No unrelated files.
- Never edit `verify/`, `orchestrator/`, `agents/` or `specs/`. Those are the human's.

## Autonomy boundary
The loop owns code for milestones whose exam is sealed. The human authors and seals every exam
(frontier mode drafts them into `drafts/` for review). The human owns the plan.
