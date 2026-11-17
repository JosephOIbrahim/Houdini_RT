# GPU accumulation milestone verification — 2026-11-16

## Scope and provenance

Phase 2B implements device-local accumulation, bounded adaptive sample batches,
GPU resolve and controlled presentation readback, based on live-edit commit
`bdce073`. Existing integrated panel/controller code is unchanged. There is no
sealed s6 exam; the results below distinguish sealed regression from supplementary
tests. No protected exam, specification or orchestrator file was modified.

Houdini/HDK 22.0.429, Windows, MSVC 19.44.35222, Vulkan SDK 1.4.341.1 and
NVIDIA GeForce RTX 4090. Source/exam hashes stayed unchanged through the passing
sealed run, and the installed DLL/shaders are bound to its archived artifacts.

## Sealed regression

`build/m3/runs/20261117T001644Z-s5-6981375414681118428/receipt.json`: PASS.

- Fresh s5 nonce `6981375414681118428`, independent s4 nonce `828888709215242714`.
- s4/Karma FLIP p99 0.0247 < 0.045, worst level error 0.00057; census and light
  linearity passed, with silent Vulkan validation.
- Both 11-frame live-edit sequences at 1M/4M ballast matched all 22 fresh renders
  exactly: worst level, FLIP and alpha differences zero. Minimum visible edit
  changed 877 pixels. Exact BLAS/TLAS/upload work and device-local placement passed.
- Median Execute-to-first-trace-submit edit time: 1.0471 ms / 0.8868 ms at 1M/4M.
  These are renderer setup measurements, not application or display latency.

## Supplementary GPU tests

`tests/gpu_accumulation/` preserves the probe and independent artifact judge.
All 26 states passed: batches 1/2/8/32, checkpoints 1/7/33/257, analytic HDR and
premultiplied alpha, exact operation/readback counters, rejected-call recovery,
discarded partial frames, resize/reset, color/dome/camera/seed changes and empty
scene recovery. Validation was active with zero errors/warnings through teardown.
Evidence: `build/m3/gpu-probe-20261117T001306Z/`.

The existing standalone RenderPass/Accumulation compatibility tests also passed
all 24 mesh-edit states, with exact live/fresh images and zero validation messages.
They exercise the explicit raw-sum compatibility path; production reports zero
compatibility readbacks. Evidence: `build/m3/legacy-edits/`.

## Controlled baseline comparison

The previous installed s5 build and exact new sealed-build payload each rendered
three repeats of the same saved scene at each size, with identical cameras,
seeds, sample budgets, bounces and fixture hashes. An independent OpenImageIO
judge reread all 12 EXRs: candidate and baseline RGBA pixels were exactly equal,
and repeats were exact. Known s4/Karma mean/silhouette checks also passed.

| Test | Previous renderer median | New renderer median | Ratio | Whole husk process medians |
|---|---:|---:|---:|---:|
| 384 × 216, 1024 samples | 0.392 s | 0.196 s | 2.00× | 1.815 → 1.620 s |
| 1920 × 1080, 256 samples | 1.215 s | 0.249 s | 4.88× | 2.796 → 1.769 s |

Renderer time includes its initialization and rendering; process time also
includes host startup. These three-repeat scene-specific observations are not
an FPS promise or a general performance guarantee.

The small case used 37 trace submissions and 2 readbacks for 1024 samples; HD used
13 trace submissions and 3 readbacks for 256 samples. Compatibility readbacks
were zero. Exact readback bytes matched presented image size times readback count.
Both accumulation and resolved output used actual device-local allocations.

At HD, each image buffer occupied 33,177,600 bytes: accumulation and resolve on
device, readback on host (99,532,800 bytes across these three buffers, excluding
scene geometry, CPU AOVs and other resources). One HD repeat's three resolve
submissions took 6.156 ms total host submit-to-fence time; this includes transfer
and waiting, not GPU-only resolve shader time.

Receipts and independent judge:
`build/m3/benchmark-baseline-01/receipt.json`,
`build/m3/benchmark-candidate-01/receipt.json`,
`build/m3/benchmark-candidate-01/independent-judge.json`.

## Installation

Payload: `C:/Users/User/AppData/Local/MythAndLogic/HoudiniRT/gpu-accum-h22.0.429-21fdae8ebf43`.
The existing `C:/Users/User/OneDrive/Documents/houdini22.0/packages/zz_houdinirt.json`
points to it. All 91 payload files were verified; 15 other package/preference
registrations were unchanged. Both tracing and resolve SPIR-V shaders ship with
the native plugin, existing extension and Houdini overlays.

The previous s5 payload remains intact. Exact rollback package bytes are retained
as `zz_houdinirt.json.before-gpu-accum-h22.0.429-21fdae8ebf43.disabled` beside the
active package. Close Houdini and restore those bytes to the active package path
to roll back. Already-open Houdini windows require restart to load the new DLL.

## Regular Houdini acceptance

Combined acceptance PASS against the exact installed payload, using normal
Houdini preferences/packages without discovery-path overrides. The native DLL
was read back from the running process and hash-checked. Two component runs
are intentionally distinguished; the timing run is not labelled a full pass:

- `build/m3/gui-normal-04/`: completed active pause/resume and all 60 timing
  observations, then failed its later capture guard when a queued geometry
  reset arrived after a settings-only generation. That capture is not used.
- `build/m3/gui-spatial-01/`: passed all nine viewport EXRs, rapid edits,
  paused edits and latest-scene Resume, panel recreation, viewer isolation and
  restoration to the original paused Karma CPU. Geometry readiness requires
  scene-version advancement; each capture kept its generation/scene unchanged.
- `build/m3/gui-spatial-01/independent_judge.json`: reread all nine EXRs and
  both raw native logs, checked sample continuity/batch bounds, pause holds,
  stale-publication rejection, camera poses and recomputed timing summaries.

Pause stopped unfinished generation 1 at 12,575 / 65,536 samples; acknowledgement
took 10.478 ms. No batches or publications followed during the 30.008-second hold.
Resume advanced that same generation to 12,691 samples. Computer-use observation
also confirmed the installed panel showed HoudiniRT, Paused, Resume and previous
renderer Karma CPU. A later two-second paused-edit hold had no GPU work before
Resume; its final image contained the latest combined edits.

The nine 256-sample images independently prove baseline objects, final rapid-burst
placement, transform, hide/show, color, insertion/deletion and combined paused
edits. The other viewer remained unpaused Karma CPU; Off restored paused Karma CPU
in the intended viewer and released controller ownership. Vulkan validation was
active and silent through both renderer teardowns.

At a 626 × 647 raster, 20 observations per edit type gave these milliseconds:

| Edit | Driver edit → observed current AOV p50 / p95 | Native request → first AOV p50 / p95 | Maximum polling gap p50 / p95 |
|---|---:|---:|---:|
| Transform | 56.58 / 82.62 | 16.01 / 22.10 | 25.17 / 31.53 |
| Display color | 55.46 / 65.86 | 14.28 / 19.71 | 25.11 / 26.59 |
| Bound USD camera | 3872.49 / 3942.49 | 15.70 / 21.77 | 3666.92 / 3722.40 |

The camera row must not be presented as fast interactive end-to-end behavior.
Instrumentation locates the long gap after the Python action returned and before
the next Qt poll; parameter setting, cooking and USD inspection were short.
Its cause is not established by these measurements. Native publication timing
starts when Execute receives a request, so it excludes this host-side delay.
Observation timing includes polling and does not measure physical display latency.

A focused follow-up reproduced this delay with the previous installed s5 build.
Both builds used the same script, fixture, settings and isolated copies of the
normal preferences/registrations, with only their package payload pointer changed.
Three bound-camera edits each gave median observed latency 4103.69 ms before and
3934.33 ms after this milestone; candidate native publication was 12–16 ms.
The multi-second delay therefore predates GPU batching. Its underlying host cause
remains unresolved; this small diagnostic is not a general latency benchmark.
Both actual DLL hashes were checked, the normal package was unchanged and both
owned comparison processes exited. The independent raw-log/payload audit passed:
`build/m3/camera-compare/independent_comparison_judge.json`.

Actual artist mouse-wheel navigation was separately observed in
`build/m3/gui-navigation-probe-02/`: quads visibly enlarged, camera pose changed,
and generation advanced to 3 with scene version unchanged at 3. Direct HOM
free-camera setters in the diagnostic harness did not notify Hydra until a scene
update; the timing run therefore uses explicitly bound USD camera xform edits.

Earlier harness attempts remain preserved: asynchronous button enablement,
free-camera update assumptions, and a flipbook that waited for the full 65,536
sample budget despite its time limit. None required a production source change.
High sample counts test active Pause; the separate image phase uses 256 samples.

The final regular-install receipt is
`build/m3/gui-spatial-01/installed_gui_receipt.json`. It rehashes all 91 payload
files, the active package and 15 surrounding registrations; all acceptance and
diagnostic children had exited, while the preexisting user process retained its
original PID and start time. Executed harness copies and hashes are archived.
All 30 renderer source files and eight sealed inputs were rehashed against the
passing gate again after GUI acceptance, with no differences.
