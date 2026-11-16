# HoudiniRT Phase 2A: live scene edits

This implements the approved roadmap's next milestone, s5-live-edits, from
the integrated viewer-control baseline 4252793. The panel, native viewer
targeting, deferred pause/resume and restoration remain the artist front door.
The installed v0.2.0 payload stays available until the new build passes.

## Data and ownership

- Hydra Sync compares values before publishing immutable mesh snapshots.
  Topology and geometry revisions are globally unique, including removal and
  recreation at the same USD path. Unchanged time samples do no new work.
- Triangulation and smooth normals retain the existing HDK implementation.
  Exact used-point world bounds are cached per mesh and recomputed only when
  that mesh changes. Scene census combines visible meshes and cached bounds.
- Execute publishes one complete immutable frame request: scene, camera,
  settings, output bindings, generation and observation timestamp. It makes
  no Vulkan calls and does not wait for GPU rendering to stop.
- The render thread consumes the latest request between passes. Superseded
  generations cannot publish pixels or claim current convergence. Image
  conversion uses detached storage; a generation-checked constant-time swap
  publishes it. Existing Map readers retain their old storage until Unmap.
  Render-buffer
  destruction/reallocation still synchronizes lifetime outside Execute;
  fallback buffers are retained by the immutable request that uses them.
- Full versioned snapshots are compared with the GPU's last applied versions.
  Cancellation therefore cannot consume and lose a scene delta.

## GPU changes

Each mesh retains device-local indexed positions, normals and indices, with
one object-space BLAS. TLAS instances carry transforms and visibility masks.
Shader buffer addresses and constant albedo live in a 48-byte per-mesh table.
Ray-query instance matrices supply object/world transforms and normal
transforms without world-space geometry uploads.

Transforms and visibility rebuild/update only the TLAS. A deformation uploads
that mesh's positions/normals and rebuilds its BLAS; unchanged indices remain
resident. Topology rebuilds only that mesh. Color edits upload its table row.
Allocation/upload/build counters report work actually executed, not requested
dirty flags. Indexed geometry is retained to meet the sealed upload bounds.
Work from cancelled requests remains in the counters until the next image is
published, so cancellation does not hide uploads or acceleration builds.

The existing accumulation and per-pass submit behavior remain in this phase;
the separate s6 experiment is not imported. Materials, light breadth, native
instancing, denoising and new artist quality sliders are later milestones.

## Verification

The unmodified sealed s5 exam includes the s4/Karma regression, 11-frame
sequences and fresh-frame comparisons at 1M/4M ballast sizes, edit visibility,
census, memory placement, exact counters, scaling and Vulkan validation.
Exam SHA256: a27756cd6c0b53e85508340fb7bd3a9bdd3c02906f13d13bf38d07b69de345be.
No protected acceptance file will be edited.

Additional regular Houdini 22.0.429 checks cover native parameter edits,
insertion/deletion, edits while paused, resume to the latest scene, the chosen
viewer and Off restoration. Image captures and generation diagnostics must
show changes rather than inferring image freshness from renderer selection.
Installation uses a new versioned payload and the existing single package
registration; the known-good payload remains available for rollback.
