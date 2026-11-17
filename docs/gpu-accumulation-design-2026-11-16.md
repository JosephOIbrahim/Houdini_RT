# Phase 2B: GPU accumulation, batching and resolve

Implements the approved next milestone from the integrated live-edit baseline
`bdce073`. The panel, target viewer, native Pause/Resume and Off restoration
remain the artist workflow. This change adds no separate renderer toggle or
quality UI. Materials, denoising and additional AOVs remain later work.

## Design choices

The scratch accumulation spike (`40b8284`, explicitly never land) establishes
the measurement idea, not release code. The current per-mesh scene handling,
immutable requests and generation-safe publication remain the foundation.

Three approaches were considered: retaining one submission/readback per sample;
recording many single-sample dispatches with inter-dispatch barriers; or tracing
a bounded sequential sample loop per pixel in one dispatch. The last gives a
small implementation and reduces both submissions and repeated sum traffic.
It preserves absolute sample indices and sequential additions to the existing
sum, without a separately grouped batch subtotal.

## GPU ownership and synchronization

- Running RGBA sums and resolved RGBA output live in device-local buffers.
  BeginFrame clears sums with GPU work. Image size changes allocate the required
  buffers; ordinary frame resets reuse them.
- A tracing dispatch receives absolute first sample and sample count through
  16-byte push constants. A precise sequential accumulator prevents regrouping
  sample additions across batch boundaries.
- A dedicated resolve shader divides all four components by completed samples;
  alpha remains coverage and RGB remains scene-linear/premultiplied. Its separate
  descriptor layout has no TLAS dependency, so initially empty scenes are valid.
- Resolve copies only the finished image to host-visible readback storage.
  Production never reads the running sum or performs CPU normalization.
  Existing standalone RenderPass/Accumulation callers retain an explicit raw-sum
  compatibility path, counted separately and unused by the delegate.
- A reusable fence completes each render-thread submission. Required compute,
  transfer and host visibility barriers order clears, accumulation, resolve and
  readback. Execute never submits Vulkan work or waits for the render thread.

## Interactive scheduling

Each new request starts with one sample and a first-image publication. Subsequent
batches adapt from measured submit-to-fence wall time toward an 8 ms target,
bounded to 1–32 samples and the remaining sample budget. Growth is limited to
doubling; an expensive batch can shrink immediately. Eight milliseconds is an
estimate, not a hard GPU deadline: one sample and shared GPU scheduling can take
longer. Observed batch and pause costs must be reported honestly.

Resolve/readback occurs near the existing 30 Hz presentation cadence, at final
completion, or when pausing with unpublished samples. Cancellation, stop and
pause are checked at batch boundaries and before resolve/publication. A stopped
or superseded frame cannot publish as current. Resume without edits continues
the same sample sequence; resumed edits reset to the newest complete request.

The existing staged-buffer publication keeps mapped readers stable and swaps
storage only under the generation check. CPU code copies resolved pixels and
handles AOV-format conversion/cropping; it does not divide running sums.

## Observability

Report actual trace/resolve submissions and dispatches, samples, readback bytes,
clear operations and allocated image storage. Memory claims derive from selected
allocation flags. Host submit-to-fence duration is not labelled GPU execution
time. First current-image publication has a separate request-to-publish duration
from Execute-to-first-submit edit time.

Pause request and render-thread acknowledgement use an epoch and monotonic time.
The acknowledgement comes at a safe boundary after in-flight work completes.
Active, requested and presented generations, presented samples and current-image
status remain distinct. Debug records bind counters and publications to renderer
identity and generation for actual desktop tests.

## Acceptance and installation

No s6 exam or seal exists in the inspected repository history. Do not create a
claim of sealed s6 acceptance or modify protected exams/specs. Run unchanged
sealed s5 (including s4/Karma), then supplementary tests maintained separately:

- Fixed-seed/analytic equivalence for batch sizes 1/2/8/32, partial final batches,
  GPU resolve and premultiplied alpha, resets/resize, empty scenes and cancellation.
- Exact counters prove reduced submission/readback work; inspect actual memory
  placement and compare identical baseline/candidate scenes and sample counts.
- In regular Houdini, pause during incomplete rendering, confirm a safe-point
  acknowledgement and held work counters, then resume the same generation.
- Check latest-scene resume after paused edits, rapid edits without stale
  publication, viewer isolation, panel recreation and previous-renderer restore.
- Record p50/p95 edit-to-current-image observation latency and polling uncertainty,
  native publication timing, pause latency, memory and resolve cost. These are
  not physical compositor/panel display latency measurements.

Install only a passing, versioned payload using the single existing package
pointer. Keep the prior live-edit payload and exact package bytes for rollback;
preserve normal preferences and other packages. No user scene is replaced.

References: approved local roadmap in `E:/Myth_and_Logic/Software/HoudiniRT/reviews/`,
`D:/Houdini_RT/PLAN-realtime-2026-11-11.md`, installed Houdini 22.0.429 HDK, and
the Khronos [synchronization examples](https://docs.vulkan.org/guide/latest/synchronization_examples.html)
and [push-constant guide](https://docs.vulkan.org/guide/latest/push_constants.html).
