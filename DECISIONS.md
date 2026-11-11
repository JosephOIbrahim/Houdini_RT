# DECISIONS — durable lessons, dated

## 2026-10-04 (seed from the cloud workspace runs)
- Texture-LUT mode for the OpenPBR BSDF is the production mode: 289 KB SPIR-V, ~2.4 s pipeline build on a software driver. Embedded-array mode produced 1.2 MB SPIR-V and ran the software driver's compiler out of memory (~6 GB). Treat array mode as a diagnostic only.
- The CPU reference table was produced with array LUTs and the GPU uses filtered textures. No offset was visible at 65k samples per row; if a row drifts near tolerance on real hardware, that filtering difference is the first suspect, not the sampler.
- The coat row at roughness 0.1, view cosine 0.1 sits at +2.6% on both CPU and GPU. It is the documented coat reciprocity-vs-energy trade in the BSDF, and the exam allows 3% there. Do not "fix" it.
- The host builds with no compiler extensions under g++ and clang++ pedantic C++17. MSVC specifics to expect: `/EHsc`, `/std:c++17`, possibly `/bigobj` for the LUT translation unit, and `strtoul` returning 32-bit `unsigned long` (the exam reduces the seed mod 2^32−5 for this reason).

## 2026-10-05 (human)
- Reference code for BLAS/TLAS construction and GLSL ray queries is in `third_party/sascha_willems_vulkan/` (MIT). Read its README first. Adapt, do not link: it depends on a framework this project does not use.
- In GLSL an acceleration structure is declared `layout(binding = N) uniform accelerationStructureEXT name;`. The `uniform` qualifier is required.

## 2026-10-05 (workstation facts, verified by listing the install)
- Houdini 22 for s3 and s4: `C:/Program Files/Side Effects Software/Houdini 22.0.429` (HFS). `bin/hython.exe` and `bin/husk.exe` exist. Also installed: 22.0.417, 22.0.400, 22.0.397, 22.0.368, 21.0.773. Pin 22.0.429 in exams.
- The HDK ships `toolkit/include/pxr/imaging/hd/renderThread.h`, `meshUtil.h`, `smoothNormals.h` and `renderDelegate.h` in 22.0.429. HdRenderThread, HdMeshUtil and Hd_SmoothNormals are available.
- Exam authors: run every analytic reference function on one hand-checked case before proposing the exam. The first s2 draft had a reference that returned 0.0 for every ray and a byte scan that rejected correct shaders.

## 2026-10-05 (references for s3)
- `third_party/openusd_hdEmbree/` is Pixar's example Hydra delegate (TOST licence): the template for `src/hdRT/`. `third_party/fragment_m3_check/` shows a working hermetic-hython + HDK exam. Read each README first. Adapt, do not link.

## 2026-11-11 (Joe's decisions for s4, recorded by Claude)
- s4 code is written by a Claude Code session (the s3 route) and landed with `_work/dev/land_step.mts`. Joe seals the s4 exam (separation of duties: Claude wrote the exam and writes the code).
- CLAUDE.md:30 amended: acceleration structures are built on the render thread at the head of Render() from the snapshot Execute hands over; never in Sync; Execute makes no Vulkan calls (hdEmbree's _PreRenderSetup pattern).
- The s3 furnace image is retired when s4 lands: `verify/s3_delegate.sh` is not expected to pass on integration afterwards, and no gate reruns it.
- The dome light is never visible to camera rays (camera misses are black, alpha 0), whatever domeLightCameraVisibility says.
- Approved for the s4 exam before sealing: measured timeouts, an alpha-coverage gate, code-only source greps with required includes, first-render thread-order check, safe evidence fallbacks, a wider unset list, and bounces drawn from the nonce and reported as `hrt_bounces`.
- The root folder is `D:\Houdini_RT` (was `D:\HoudiniRT`); product identifiers are unchanged.

## 2026-11-11 (X8, live pause in Solaris, checked by Joe)
- Live pause ok, menu yes, edit-while-paused safe, 22.0.429, 2026-11-11. On the step 3 build (`.claude/worktrees/s3-delegate-shell/build/plugin_stage`): HoudiniRT appears in the Solaris viewport renderer menu (no UsdRenderers.json entry needed); pause stops accumulation and resume continues; tumbling while paused is safe; switching the renderer away and back is clean. This closes the human check owed since s3 (build-spec.md line 43).

## 2026-11-11 (s4 exam sealed)
- The s4 exam was sealed at d4a9c9d ("seal exam for s4-first-light (reviewed by: Joe Ibrahim)"). Claude ran `seal_exam.ps1 -Milestone s4-first-light -By "Joe Ibrahim"` at Joe's explicit instruction in the session ("Run the seal for me"); the review and the decision to seal are Joe's. Output: merges into _integration and s4-first-light, "sealed s4-first-light at d4a9c9d", exit 0. Three earlier attempts from outside the session never reached the repo.
- Sealed exam sha256 5bd6027fc9843a71b1137aaeb0d9b9762278a987d405b3d01c563f46c8e0f3d4, identical in verify/s4_firstlight.sh (master, integration, feat/s4-first-light) and drafts/sealed/. state.json: s4-first-light pending, round 1. The night task stays Disabled while a Claude Code session writes the s4 code; do not press Start on the dashboard.
