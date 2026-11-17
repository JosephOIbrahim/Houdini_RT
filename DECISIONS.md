# DECISIONS — durable lessons, dated

## 2026-11-17 (reliable lighting remains one artist workflow)
- Edit lights through native Houdini Light/Light Mixer parameters. The integrated
  controller owns viewer targeting, enable, pause, actual state and restoration;
  it does not grow a parallel set of light parameters.
- Use world-space emitter area for UsdLux normalization. Keep a stable solid angle
  for small distant lights, and pair next-event sampling with BSDF-hit emission
  using the light selection probability in both MIS weights.
- Light geometry and diffuse energy have distinct meanings: a black area light
  remains opaque, and a zero diffuse multiplier does not remove camera emission.
  Independent mixed-light references must account for emitter occlusion and the
  part of the dome that finite emitters obscure.
- Authored corner normals are shading data, separate from position/topology
  revisions. Keep actual Hydra IDs and coarse polygon IDs, not transient GPU slots.
- Diagnostic AOVs use coherent pixel-center hits, separate from beauty randomness.
  Never average integer IDs or silently publish clear images for unsupported AOVs.
  Expose actual guide allocation and readback work in the same native statistics.
- Declare OpenGL 0–1 depth through Houdini's UsdRenderers.json. Report critical
  current-generation failures through `huskErrorStatus`: diagnostic logs and Tf
  errors alone do not stop husk from successfully writing a clear image.
- Validate new HOM calls against the actual installed class as well as mocks.
  Native GUI verification caught a mocked getter name that did not exist.
- Scene support is an explicit bounded inspection of the target viewer's stage,
  labelled partial/stale when necessary. Resolve capability metadata from the
  actual installed plugin; never substitute a newer source-tree capability list.
- Phase 3 acceptance is supplementary: the sealed repository exams stop at s5.
  Preserve those exams and run them in a copied runbox so generated evidence does
  not modify protected source files. Install the exact accepted staged artifact.

## 2026-11-16 (scripted camera edits and native reference-floor drawing)
- Measure from the artist's edit to a current image, not only from Hydra Execute.
  A short request-to-AOV time can conceal a host viewport stall before submission.
  First return to Qt is also insufficient: a later draw can still block the image.
- The reproduced four-second stall belongs to Houdini 22.0.429's native
  `dm_LabelGeometryVK::build` / infinite reference-floor drawing. Removing fences
  or hiding grid numbers did not fix it; disabling the reference floor did.
- Keep the workaround in the integrated controller, scoped to this build and
  its owned viewer. Temporarily hide only reference-plane visibility; restore it
  on Off or external renderer takeover, including with the panel closed. Respect
  an artist who manually shows it. Never change camera clipping, projection,
  render settings or grid spacing to make a timing test appear faster.
- Display cleanup has its own recovery record. A failed grid restore must remain
  retryable without inventing renderer ownership after a manual renderer switch.
  The temporary observer exists only while display ownership needs servicing.
- ReferencePlane visibility broadcasts to every split viewport's orthographic
  grid flag after redraw. Immediate readback misses this side effect; a simple
  False/True roundtrip loses mixed settings. Preserve each actual viewport's
  flags across the redraw, both when hiding and restoring the reference plane.
  Hiding orthographic grids alone did not remove the stall; restoring their
  flags while keeping the reference floor hidden retained the speedup.

## 2026-11-16 (integrated artist viewer controls)
- HoudiniRT is one artist workflow. The primary panel, shelf, radial and node
  actions select the Hydra renderer in an explicit Solaris viewer. The optional
  assistant remains a separate implementation concern inside the same product;
  its service health never determines renderer state.
- Renderer restoration belongs to a process-wide, per-pane controller. Native
  pane identity survives recreated HOM wrappers; names alone cannot establish
  ownership. An external renderer selection relinquishes that ownership.
- Houdini 22.0.429 queues `setRendererPaused()` changes. A native-only GUI probe
  observed the old flag immediately and the requested flag later for both
  HoudiniRT and Karma CPU. Track a pending request separately from actual state,
  yield to the UI, confirm by readback, and bound the wait. Never report a false
  success or treat an immediate old flag as a definitive failure.
- The shared controller has no Qt dependency and never waits on the render
  thread. The panel polls native state; shelf/node-only commands still dispatch
  normally, with later actions or Refresh confirming their completion. Restore
  records survive timeout or partial restoration so the artist can retry.
- The main panel reports selected renderer and native pause state. These HOM
  APIs do not establish pixel correctness, convergence, or GPU health. The
  unchanged renderer's sealed rendering exams retain their separate scope.
- GUI smoke shutdown uses a posted native `quit` command. Raising `SystemExit`
  through a PySide timer callback caused Houdini teardown crashes in the test
  harness; native quit exited cleanly after renderer teardown.

## 2026-11-12 (rt_toggle consolidation)
- The Python extension enters as `extensions/rt_toggle/` by git subtree, preserving its history from source commit d1cac89 (the Apache-2.0 licensing commit after v0.1.7 at 8e30cce). Source repository: JosephOIbrahim/houdini-rt-toggle.
- Licensing is Apache-2.0 by Joe's explicit owner decision. The extension carries its own LICENSE and its release builder/audit require it.
- The CPython runtime remains external. Runtime paths use HOUDINI_RT_ROOT; the extension path is independently RT_TOGGLE_ROOT. The monorepo is source/build storage, not a Python distribution.
- Renderer build, sealed exams, orchestrator and TypeScript harness are unchanged. The extension keeps its own interpreter matrix in a root-level rt-toggle-tests workflow.

## 2026-10-04 (seed from the cloud workspace runs)
- Texture-LUT mode for the OpenPBR BSDF is the production mode: 289 KB SPIR-V, ~2.4 s pipeline build on a software driver. Embedded-array mode produced 1.2 MB SPIR-V and ran the software driver's compiler out of memory (~6 GB). Treat array mode as a diagnostic only.
- The CPU reference table was produced with array LUTs and the GPU uses filtered textures. No offset was visible at 65k samples per row; if a row drifts near tolerance on real hardware, that filtering difference is the first suspect, not the sampler.
- The coat row at roughness 0.1, view cosine 0.1 sits at +2.6% on both CPU and GPU. It is the documented coat reciprocity-vs-energy trade in the BSDF, and the exam allows 3% there. Do not "fix" it.
- The host builds with no compiler extensions under g++ and clang++ pedantic C++17. MSVC specifics to expect: `/EHsc`, `/std:c++17`, possibly `/bigobj` for the LUT translation unit, and `strtoul` returning 32-bit `unsigned long` (the exam reduces the seed mod 2^32−5 for this reason).

## 2026-10-05 (s1-furnace-4090 GREEN on the RTX 4090)
- Calling a `.bat` from Git Bash: use `MSYS_NO_PATHCONV=1 cmd.exe /c "..."`. Plain `cmd.exe //c` lets MSYS rewrite the Windows paths and mangles the arguments. Every Windows exam that shells out to a batch file hits this.
- Line endings are load-bearing: `.gitattributes` pins `*.sh` to LF and `*.bat` to CRLF. A batch file with LF (or a shell script with CRLF) breaks under its own interpreter before any real error surfaces.
- MSVC host build, confirmed on the 4090: `/bigobj` is **required** for the LUT translation unit (upgrades the 2026-10-04 "possibly"), and `/wd4305 /wd4996` must be silenced for a clean build (`/std:c++17 /O2 /EHsc /W3`).
- Fidelity exams carry a **copy guard**: rows byte-identical to the CPU reference are capped (≤16; s1 landed at 8) so a GPU that merely echoes the reference fails. The kernel must genuinely compute — never reuse or copy reference rows.
## 2026-10-05 (human)
- Reference code for BLAS/TLAS construction and GLSL ray queries is in `third_party/sascha_willems_vulkan/` (MIT). Read its README first. Adapt, do not link: it depends on a framework this project does not use.
- In GLSL an acceleration structure is declared `layout(binding = N) uniform accelerationStructureEXT name;`. The `uniform` qualifier is required.

## 2026-10-05 (s2-rayquery, human + cloud assistant)
- KHR acceleration-structure functions are not exported by vulkan-1.lib; fetch them with vkGetDeviceProcAddr (see `AsApi` in rayq_host.cpp).
- Scratch buffers for AS builds are over-allocated and the device address rounded up to `minAccelerationStructureScratchOffsetAlignment`.
- One TLAS, two instances, visibility masks: cube 0x1, occluder 0x2. Shadow rays use mask 0x2 so they do not see the cube; the light is outside the cube.
- A ray from inside a closed box reports the EXIT distance. The first draft of the s2 exam had the reference returning 0.0; it was corrected by the owner's decision.
- Per-path RNG must be seeded through a full-avalanche hash of (seed, path index). XOR-ing a multiple of the index into the seed gave a reproducible 3.7 sigma furnace outlier for one seed.
- The furnace uses uniform-hemisphere bounces (weight rho * 2 cos), an unbiased estimator with real variance. At rho = 0.8 expect a statistical exam failure roughly once per hundred runs; that is the 3 sigma test, not a bug.
- The s2 exam disassembles `rayq.spv` with `spirv-dis` and fails (exit 11) if no ray-query instruction is present: the kernel must genuinely use inline ray queries — a compute-only fallback or a hand-rolled intersection pass does not count.
- Host protocol the exam greps for (keep it in s3/s4 hosts too): a device line naming the GPU and its type, a `rayQuery: supported` line, and an echo of the injected seed (`seed N:`). Missing any of them is exit 15; a software device is rejected outright.
- In GLSL an acceleration structure is declared `layout(binding = N) uniform accelerationStructureEXT name;`. The `uniform` qualifier is required.

## 2026-10-05 (workstation facts, verified by listing the install)
- Houdini 22 for s3 and s4: `C:/Program Files/Side Effects Software/Houdini 22.0.429` (HFS). `bin/hython.exe` and `bin/husk.exe` exist. Also installed: 22.0.417, 22.0.400, 22.0.397, 22.0.368, 21.0.773. Pin 22.0.429 in exams.
- The HDK ships `toolkit/include/pxr/imaging/hd/renderThread.h`, `meshUtil.h`, `smoothNormals.h` and `renderDelegate.h` in 22.0.429. HdRenderThread, HdMeshUtil and Hd_SmoothNormals are available.
- Exam authors: run every analytic reference function on one hand-checked case before proposing the exam. The first s2 draft had a reference that returned 0.0 for every ray and a byte scan that rejected correct shaders.

## 2026-10-05 (references for s3)
- `third_party/openusd_hdEmbree/` is Pixar's example Hydra delegate (TOST licence): the template for `src/hdRT/`. `third_party/fragment_m3_check/` shows a working hermetic-hython + HDK exam. Read each README first. Adapt, do not link.

## 2026-10-06 (s3-delegate-shell, written by Claude by hand; facts measured on the workstation)
- **HDK build.** `find_package(Houdini REQUIRED)` then `target_link_libraries(x PRIVATE Houdini HoudiniThirdParty)`. `HoudiniThirdParty` carries every `libpxr_*` import library and `vulkan-1.lib`; the HDK also ships Vulkan headers (`toolkit/include/vulkan`, 1.3.236). The Vulkan SDK is needed only for `glslangValidator` and `spirv-dis`. Flags come with the target: `/MD /std:c++20 /permissive-`, `NOMINMAX`. Add `/MP` (build drops from ~110 s to ~40 s) and `_SILENCE_CXX20_OLD_SHARED_PTR_ATOMIC_SUPPORT_DEPRECATION_WARNING` (USD's own headers trip it).
- **Never call `houdini_configure_target()`**: its default output directory is the user's `houdini22.0/dso`, which plants an ambient plugin the exam then rejects. Set `PREFIX ""` by hand and install with `install(FILES $<TARGET_FILE:...>)` (a plugin DLL exports nothing, so there is no import library to install).
- **Plugin layout that registers:** a flat directory with `plugInfo.json` (`"Root": "."`, `"LibraryPath": "hdRT.dll"`, `"ResourcePath": "."`), the DLL, and resources beside them. Define `MFB_PACKAGE_NAME`, `MFB_ALT_PACKAGE_NAME` (= the plugInfo `Name`) and `MFB_PACKAGE_MODULE`; `PLUG_THIS_PLUGIN` + `PlugFindPluginResource` then find `shaders/furnace.spv`.
- **How husk 22.0.429 drives a delegate** (traced with `HOUDINIRT_DEBUG=1`): every non-schema attribute on the RenderSettings prim arrives in the settings map passed to the delegate's constructor, typed as authored (`houdinirt:seed` int, `houdinirt:furnace:emission` GfVec3f), along with husk's own (`resolution`, `renderCameraPath`, `batchCommandLine`, `houdini:frame`...). husk uses the legacy viewport, not camera framing (`GetFraming().IsValid()` is false): take the data window from `GetViewport()`. With no RenderProduct authored it binds one AOV, `color`, in the format `GetDefaultAovDescriptor` returns (Float32Vec4), allocated multisampled, and writes it as a half-float RGBA EXR. The camera sprim gives correct view and projection matrices. The delegate IS destroyed before exit (default `--fast-exit 2`), so teardown runs under the validation layer.
- **husk and render stats.** For a delegate with no `UsdRenderers.json` entry husk writes each key of `GetRenderStats()` as its own top-level EXR attribute, next to `__delegate` (the plugin id). The packed `husk:render_stats` JSON exists only for delegates whose `UsdRenderers.json` entry defines `husk.metadata` (Karma). Under a hermetic `HOUDINI_PATH` a new delegate cannot have such an entry.
- **GfMatrix4d to GLSL.** Copy the 16 doubles to floats in memory order and use `M * v` in the shader. Gf is row-major with row vectors; GLSL reads the same bytes as the column-major matrix of the equivalent column-vector transform. No transpose. Hydra buffers are bottom row first; husk flips for the EXR. Verified with open-wall renders: the hole appears where the camera should see it.
- **Half-float quantisation.** husk's default EXR is half. A constant 4.3966 comes back as 4.3984 (+4.2e-4). The worst case is 2^-11 = 4.9e-4 relative, so never set a husk-EXR tolerance tighter than about 1e-3.
- **Furnace estimator for s3 is cosine-weighted** (throughput times rho per bounce): zero variance in a closed box, so the image is exactly constant. Step 2's uniform-hemisphere estimator has per-path relative deviation ~0.69 at rho 0.7, which at 64 spp sits too close to the s3 judge's "99% of pixels within 25%" rule.
- **Wall normals come from the primitive index** (`rayQueryGetIntersectionPrimitiveIndexEXT / 2`), never from the hit position, and each bounce starts 1e-4 inside every wall. Deriving the normal from the nearest face let about 3 paths in 10,000 escape through edges on a software driver. With the change the image is bit-constant on both the software driver and the 4090.
- **One validation warning is expected under Houdini**: `WARNING-vkGetDeviceProcAddr-device ... vkGetPhysicalDeviceVideoEncodeQualityLevelPropertiesKHR`, once per device creation. Houdini's bundled Vulkan loader (1.3.236) asks the device for an instance-level function and the SDK's newer validation layer remarks on it. The delegate never requests that function. It has no VUID and is not an error.
- **The kernel can be tested without Houdini and without a GPU**: `src/hdRT/test/furnace_standalone.cpp` + `vkFurnace.cpp` build with any C++20 compiler and run on Mesa's software Vulkan (25.2 supports ray queries) with `--software`. The delegate refuses software devices unless `HOUDINIRT_ALLOW_SOFTWARE_DEVICE=1`.
- **Exam authors (lesson from the s3 exam's stage 7):** an assumption observed on Karma is not a fact about husk. Probe it with a stock delegate that is NOT Karma (`HUSD_HDEBUG_DELEGATE=1 husk -R HdHDebug`) before sealing.

## 2026-10-06 (s3-delegate-shell: exam stage 7 amended, step landed by hand)
- **Who did what, stated plainly.** For this step one party (Claude) wrote the exam, the stage 7 amendment and the code under test. The harness rule is the opposite (nobody edits an exam they are tested by), so the amendment went to the owner with its evidence and was sealed only on his go ("Use recommendations", 2026-10-06). What backs it without trusting the author: it relaxes one demand (an EXR attribute husk writes only for Karma) and keeps the demand behind it (the header must name this plugin); the judge alone was run on nine synthetic images and still fails every wrong one; the version it replaces is kept as `drafts/sealed/s3-delegate-shell.check.v1-2026-10-06.sh`.
- **How it was merged.** The code was written on `claude/s3-delegate` and fast-forwarded onto `feat/s3-delegate-shell`. The Generator and the Scribe did not run. The merge into `integration` is made by a script that calls the loop's own `runChecks` (trusted snapshot of `verify/`, fresh random nonce) and merges only on PASS; the outcome is recorded in `.claude/state.json` like any other step.
- **Known weakness in the s3 exam, not fixed (it is sealed).** `SEED31=$(( SEED % 2147483647 ))` runs in bash, whose arithmetic is signed 64-bit, while the loop's nonce is unsigned 64-bit. A nonce at or above 2^63 wraps negative and the next line clamps the seed to 1, which is also the delegate's default seed. On those runs, half of them, the seed-echo check cannot tell "read the setting" from "used the default". The furnace values are not affected: Python draws them from the full nonce. Because of this the merge script asks for one more thing than the loop does: at least one passing run whose nonce is below 2^63.
- **Exam authors:** do not reduce the nonce with bash arithmetic. Do it in Python, or write `(( (SEED % M + M) % M ))`. And check that no value the exam injects can equal a default of the program under test.
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

## 2026-11-11 (s4 C6: what a cosine sample below the face should do)
- Measured on nonce 101 with the sealed exam's own FLIP code (Karma CPU reference; harness `_work/s4exam/c6_ab.sh`, results `probe/c6_ab_iid.txt`). Noise floors: the delegate against itself, two seeds, FLIP 0.0159 and per-pixel relative RMS 0.0118; Karma CPU against itself 0.0179 and 0.0187. The delegate is less noisy than Karma, so a thin FLIP margin against Karma means bias, not noise.
- Mirroring a cosine sample about the shading normal back above the geometric face (the C5 kernel) scored FLIP 0.0444/0.0452, level +0.12%, RMS 0.0251: about a 2% per-pixel residual once noise is removed. Ending the path scored 0.0285/0.0294, level -0.05%, RMS 0.0188; sampling about the face normal 0.0289/0.0288, -0.05%, 0.0182. Both leave about 1% residual, which is not zero and not explained yet.
- Chosen: end the path (the Lambert BRDF with a shading normal is zero below the geometric face), which keeps smooth normals meaningful for directional lights in s5. Frozen exam after the change: 8 of 8 nonces pass, FLIP 0.019-0.031. No stratified sampling was needed.

## 2026-11-16 (s5 live edits, one HoudiniRT workflow)

- The approved roadmap's next renderer milestone is Phase 2A / sealed s5-live-edits.
  The existing integrated panel/toggle remains the artist front door. Accumulation
  batching and GPU resolve belong to Phase 2B / s6; no s6 spike is imported.
- Dirty bits signal a possible change, not proof of a changed value. Sync compares
  actual values and publishes immutable object-space mesh records with globally
  unique geometry/topology versions, including deletion and recreation.
- Execute hands over a complete latest-frame request without waiting for rendering
  or calling Vulkan. The render callback stays available between converged frames
  to avoid losing a request when HdRenderThread::StartRender is already active.
- Image conversion uses detached CPU storage. A generation check and constant-time
  storage swaps publish it; mapped readers retain old pixels until Unmap. Neither
  pixel conversion nor retired image destruction holds the request lock.
- Persistent device-local per-mesh buffers and BLASes separate geometry edits from
  transform/visibility changes in TLAS instances and color changes in 48-byte rows.
  Counters record executed operations and carry unpresented work across cancellation.
- Installation uses a new immutable payload and replaces only the existing
  zz_houdinirt.json pointer after validation. The prior payload and package bytes
  remain available for rollback; other regular Houdini packages remain enabled.

## 2026-11-16 (Phase 2B, GPU accumulation and presentation)

- The user requested implementation, installation and regular Houdini verification
  of the approved accumulation/batching/resolve milestone. No sealed s6 exam exists
  in the inspected history. Acceptance combines unchanged sealed s5/s4 with clearly
  labelled supplementary checks; no protected spec or exam is authored or modified.
- Keep the integrated panel/controller and per-viewer ownership. This is a native
  renderer change; new materials, denoising, guide AOVs and quality UI remain later.
- Use device-local sums and resolved output, a separate resolve pipeline valid even
  without a TLAS, and host readback only for presentation. The legacy standalone
  raw-sum API is explicit, separately counted, and unused by the delegate.
- A batch uses one dispatch with consecutive absolute sample indices and sequential
  precise additions to the existing sum. Do not accumulate a separate batch subtotal:
  that would make floating-point grouping depend on interactive scheduling.
- Keep one in-flight submission on the render thread, completed by a reusable fence.
  Start each request at one sample; adapt toward 8 ms with 1–32 samples and bounded
  growth. This is a measured target, not a hard GPU deadline or an FPS promise.
- Native pause flags describe the request. A separate epoch acknowledgement records
  reaching a completed-work boundary. Active/requested/frame/presented generations
  and sample counts remain distinct; only a generation-checked swap publishes pixels.
- Report actual work, allocation flags/bytes, host submit-to-fence timings and
  first-current-publication latency. Desktop observation includes polling delay;
  it does not measure the physical display compositor.
- A newer request generation alone does not prove a queued geometry edit arrived:
  render settings can advance first. Desktop capture readiness must also require
  the expected scene-content advancement and independently check the pixels.
- Keep high sample budgets for active-pause evidence and a finite image-test
  budget for flipbooks. Houdini's capture time limit did not stop the high-budget
  bound-camera render in this harness. Preserve failed harness attempts and state
  exactly which completed components contribute to acceptance.
