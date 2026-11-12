# Notes for the step 5 exam (`s5-live-edits.check.sh`)

Drafted by Claude (exam author) on 2026-11-11 for Joe, who reviews and seals it. Written from the
`## s5-live-edits` section of `specs/build-spec.md` (approved by Joe 2026-11-11, uncommitted), the
milestone line in `orchestrator/config.ts`, the sealed s4 exam and its notes. No s5 code, no s5 design
note and no plan file was read: `src/`, `_work\spike-accum\`, `_work\s4-claude\`,
`PLAN-realtime-2026-11-11.md` and `drafts\s5-live-edits.spec.md` stayed closed.

## Status: DRAFT, not frozen, not sealed

| Item | State |
|---|---|
| Exam drafted, header restates every acceptance bullet | Done (`drafts\s5-live-edits.check.sh`) |
| Dry run on the s4 code (integration `846a1c6`), 6 nonces | Done, see "Dry run" below |
| Judge self-test: a correct fake and 15 planted faults | Done, 16 of 16 behave |
| Joe's answers to the decisions below | **Owed** |
| Freeze (sha256), then seal from the dashboard | **Owed** (Joe) |

## Decisions for Joe (ambiguities in the spec, and how the draft resolves each)

Each one is flagged, not silently invented. The draft implements the recommended answer, so
"all recommended" needs no edit.

**1. Time samples re-dirty every animated attribute on every frame (the big one).**
Probed with Karma and `TF_DEBUG=HD_*` (`_work\s5exam\probe\dirty\`): a mesh whose points are sampled on
frames 1 to 4 but change only at frame 3, and a mesh whose translate changes only at frame 4, are
both re-synced ("Rprim Cleaned") on frames 2, 3 and 4. The same two edits made by a
`husk --preframe-script` on a stage with no time samples re-sync only the edited mesh, only on its frame.
Consequence: on a transform-only frame of a time-sampled sequence, the grid still arrives with
DirtyPoints and DirtyTopology, the sphere with DirtyVisibility, and so on. A delegate that turns dirty
bits straight into GPU work will refit the grid on every frame and fail the counter rules.
- **Recommended (implemented):** keep the time-sampled `.usdc` (it is what the brief asked for, and
  what a scrubbed timeline in Solaris does). The header says it in so many words: a delta is a value
  that differs from what the delegate already holds; counters count real changes, not dirty bits.
  Only attributes that some edit changes carry time samples, so the delegate compares small things
  (one grid, one sphere, three transforms, three colours); the 4M ballast is never re-delivered.
- Alternative: edits through `--preframe-script` on an unsampled stage. Exact dirty bits, closer to a
  user editing in the viewport, but it leans on husk's script hook and is not what the spec text
  ("one husk session renders 8 or more frames") pictures. Team lead was told on 2026-11-11.

**2. "That mesh's vertex data" and "index data" have no byte size in the spec.**
Implemented: vertex data = point count x 32 bytes (a float4 position and a float4 normal per point;
float3 + float3 = 24 B fits too). Index data = triangle count x 12 bytes. Both taken from the stage after
the edit. Points frame: upload <= 1.1 x vertex data. Topology frame: upload <= vertex data + index data,
no 1.1 factor (the spec gives the factor for points only). A delegate that de-indexes (three vertices
per triangle) fails these bounds; say so now if that layout should be allowed.

**3. `hrt_scene_memory` has no format in the spec.** Implemented: one string stat of `name=kind` entries
separated by `;` or `,`. `geometry`, `index`, `normal`, `table` must be `device_local`; any entry that is
not device_local must be named `staging*` or `readback*`. Probed: husk does carry a string stat into the
EXR header (s4's `hrt_device` arrives as a string). Checked on every sequence frame.

**4. What a counter counts.** Implemented: the work done for that frame, since the previous frame's
image (not a running total). A running total fails (self-test case "cumulative").

**5. Frame 1 and the scale comparison.** The spec gives no rule for frame 1 (it builds everything, and
its upload is four times larger with the 4M ballast). Implemented: frame 1 counters are recorded, not
judged; the 1M/4M "identical counters" rule runs on frames 2 to 11.

**6. `hrt_edit_ms`.** Implemented: a non-negative number (float allowed), required on frames 2 to 11 of
both sequences. "Transform-only frames" are the four transform frames; the two visibility frames do
not enter the median.

**7. displayColor frame.** The spec says the colour "travels in a per-mesh table, counted in
`hrt_table_upload_bytes`, at most 4 KB". Implemented as 0 < table_upload_bytes <= 4096 (zero would mean
the colour never travelled).

**8. TLAS work on points, topology and colour frames.** The spec names TLAS counts only for transform
and visibility frames (builds + updates = 1). Implemented: not judged elsewhere; recorded in the judge log.

**9. Same seed and spp must mean the same samples.** The FLIP 0.01 bound only holds if the delegate's
random sequence per pixel depends on seed, pixel and sample index, not on the frame number, the scene
version or the edit history: two honest renders with different seeds score 0.015 to 0.019 at 1,024 spp
(table below). The header states this as part of the contract. A design that reseeds per scene version
cannot pass any bound this tight.

**10. A change guard was added (not in the spec).** Each sequence frame must differ from the previous
one by more than 2% in at least 200 pixels. Reason: the from-scratch reference comes from the code under
test, so an edit the delegate drops in both runs (for example visibility ignored entirely) passes the
equality check. The exam draws every edit to be visible; the smallest change seen in the dry runs is
listed below. Drop it if Joe prefers the spec's letter.

**11. From-scratch renders at both ballast sizes.** The spec says "each frame of the sequence"; both
sequences are covered (22 extra husk runs, about 60 s).

**12. Census.** `hrt_seed` and `hrt_bounces` are now required in every frame's header (s4 tolerated a
missing `hrt_seed`). The bounding box tolerance is s4's (1e-4 of the diagonal); the ballast 50 units
below stretches the diagonal to about 50, so the tolerance is about 5 mm. Hiding the sphere moves the box
by 25 mm or more, so the hidden-mesh rule is still seen by the box, and always by the counts.

**13. The ballast is out of view.** A 2 x 1 unit sheet of quads 50 units below the table: in every
census, in no picture (it subtends about a thousandth of a steradian of the dome). Keeping it out of the
frame stops a million static triangles from diluting the per-channel level check and from adding
sub-pixel aliasing. Consequence: the 4M/1M timing compares edit cost, not render speed.

## What the exam does, stage by stage

| Stage | What it checks | Exit on failure |
|---|---|---|
| 0 | Houdini 22.0.429, timeout, sha256sum, the teapot file and its sha256, a decimal nonce, the sealed s4 exam present | 13 |
| 1 | **No regression.** Runs `verify/s4_firstlight.sh` unchanged with its own nonce: the decimal of the first 15 hex digits of sha256(`s5-live-edits/s4:<nonce>`) (60 bits, so bash's signed arithmetic is safe). That run builds and installs the plugin. Its log goes to `verify/s5_s4.log`; its own logs stay where it writes them | the s4 exit code, passed through; evidence carries the s4 evidence nested |
| 2 | Hermetic environment (the s4 exam's, verbatim: `HOUDINIRT_*` cleared, only `HOUDINIRT_VALIDATION=1` set back); exactly one HoudiniRT renderer plugin, from `build/plugin_stage` only | 13, 15 |
| 3 | Writes both stages and `build/s5/truth.json` from the nonce in hython: the scene, the edit order, the census of every frame of both stages, and the byte bounds of every points and topology edit | 13 |
| 4 | The two edit sequences: `husk -f 1 -n 11` once per ballast (300 s each) | 14, 15 |
| 5 | Every frame again from scratch: `husk -f k -n 1`, 22 runs (90 s each) | 14, 15 |
| 4-5 | In every husk log: no VUID / Validation Error / VK_ERROR; "validation layer active"; "seed=<seed> spp=1024 device=..."; not a software device; the GPU nvidia-smi names | 14, 15 |
| 6 | The judge (all findings printed, then the first failing section in this order): header (delegate named, seed, bounces, size, finite) 16; census on every frame of all 44 images 16; equality sequence vs from-scratch 16; change guard 16; counters 16; scale (1M vs 4M counters, edit-time medians) 16; scene memory 17 | 16, 17 |

## The scene and the sequence

Five meshes under one uniform dome, all `subdivisionScheme = none`, no authored normals, constant
`displayColor`, no materials (no Karma reference in s5, so none is needed):

| Mesh | What the nonce draws | Triangles |
|---|---|---|
| `/World/teapot/Geometry` | the s4 asset by reference; yaw, tilt, non-uniform scale 0.5 to 0.7 | 2,472 |
| `/World/table` | 9, 11, 12 or 14 sides, radius 0.66 to 0.78, spin | 7 to 12 |
| `/World/grid` | 10 to 14 quads a side, wave amplitude and phase, place on the table | 200 to 392, then 72 to 648 after the topology edit |
| `/World/sphere` | 16 to 24 segments, 10 to 14 rings, radius 0.085 to 0.11 | 288 to 624 |
| `/World/ballast` | nothing: 1000 x 500 or 2000 x 1000 quads, 50 units below, out of view | 1,000,000 or 4,000,000 |

Camera on an orbit (elevation 20 to 45 degrees, focal 30 to 42 mm) framing the whole table; dome
intensity, exposure and tint and the bounce count (6, 7, 9, 10, 11, 12) as in s4. The RNG is
`random.Random("s5-live-edits:<nonce>")`, so s5 scenes never repeat s4 scenes.

Edits, ten of them on frames 2 to 11, shuffled by the nonce with visibility-off before visibility-on:
transform x4 (teapot, grid, or the sphere while it is visible; 3 to 6 cm and, for teapot and grid, a
20 to 50 degree turn), visibility off and on (sphere), points x2 (grid: new amplitude and phase), topology
x1 (grid: new resolution and shape), displayColor x1 (teapot, table or grid; every channel moves 0.15 to
0.25). Four transform frames give the edit-time median something to stand on.

## The numbers chosen

| Number | Value | Why |
|---|---|---|
| Resolution | 384 x 216 | the s4 frame; the s4 delegate renders it at 1,024 spp in well under a second |
| spp | 1,024 | more samples narrow how far a single diverging path can move a pixel. Same-seed renders on s4 code are bit-identical, so the bound does not depend on spp there |
| Frames | 11 (`-f 1 -n 11`) | frame 1 plus ten edits; the spec asks for 8 or more |
| Ballast | 1,000,000 and 4,000,000 triangles exactly | the spec |
| `LEVEL_TOL` / `FLIP_MAX` / `DA_MAX` | 0.2% / 0.01 / 0.0004 | the spec's numbers, kept; see calibration |
| `CHANGE_MIN` | 200 pixels changing by more than 2% | about a quarter of the smallest change seen on a correct edit (table below) |
| Time limits | s4 its own (1,320 s worst); stage writer 300 s; each sequence 300 s; each from-scratch frame 90 s; no render may start after 2,100 s | worst case stays inside the milestone's 2,400 s |

## Judge self-test (fake headers on real dry-run images)

`_work\s5exam\tools\fake.py` copies a dry run's 44 images and writes the headers a correct s5 delegate
would write (census from the stage; counters by the rules: one TLAS update per transform, one refit and
24 B per point for a points edit, one build for topology, 16 table bytes for a colour; edit time 0.8 ms
at 1M and 0.9 ms at 4M; all four buffers device-local). Then it plants one fault per case and runs the
exam's own judge block (`tools\judge_block.py`, extracted byte for byte from the draft).

| Case | Planted fault | Result |
|---|---|---|
| correct | none | **PASS** (exit 0) |
| stale_seq | a transform never reaches the live scene (sequence frame = previous frame) | 16, equality (FLIP 0.18) |
| dropped_both | the colour edit ignored in both runs | 16, change guard (0 pixels changed) |
| census_hidden | the hidden sphere still counted | 16, census |
| cumulative | counters as running totals | 16, counters |
| refit_on_transform | a BLAS refit on a transform frame | 16, counters |
| two_tlas | TLAS build and update on one transform frame | 16, counters |
| points_whole_scene | a points edit uploads the ballast | 16, counters |
| topology_refit | a topology edit refits instead of building | 16, counters |
| color_geometry | a colour edit uploads geometry | 16, counters |
| color_big_table | a colour edit uploads 8 KB of table | 16, counters |
| scale_counters | 4 more upload bytes at 4M than at 1M on a points frame | 16, scale |
| slow_4m | transform edits 3.0 ms at 4M against 0.8 ms at 1M | 16, scale |
| host_geometry | geometry buffer host-visible | 17, memory |
| host_extra | an extra `scratch=host_visible` buffer | 17, memory |
| no_memory | `hrt_scene_memory` missing | 17, memory |

## Honest limits

- **The reference is the code under test.** Equality proves the live scene equals the scene a fresh
  delegate builds, not that either is right. Rightness of a fresh build is what s4 proves against Karma
  (it runs first), plus the census and the change guard. A wrong-in-both delegate on a feature s4 does
  not cover (visibility, a moved grid) is caught only if it changes the census or freezes the picture.
- **Counters are self-reported.** The exam cannot see the GPU. It cross-checks counters against the
  edit plan, against each other at two scene sizes, and indirectly against the images: a delegate that
  reports zero work but skips the work shows a stale frame. A delegate that does extra work and
  under-reports it is not caught.
- **The calibration is on code that rebuilds everything.** The s4 delegate gives bit-identical frames, so
  the dry run cannot show what an object-space BLAS and refits do to the bounds. The 1e-6 world-scale
  measurement stands in for that (floating-point differences, same samples). Re-measure on the first
  real s5 build before sealing if Joe wants numbers from the code itself.
- **Time-sampled edits arrive with every animated attribute dirty** (decision 1). An implementation tuned
  only to this exam could compare values for everything and still be slow in the viewport, where dirty
  bits are exact. The ratio rule still bounds that cost by scene size.
- **`hrt_edit_ms` is self-timed** and judged only as a ratio of medians over four frames, with 0.5 ms of
  slack. On a shared GPU this is the noisiest number in the exam; it is the one most likely to flake.
- **Visibility and transform frames share one TLAS rule** (builds + updates = 1). Whether hiding is done
  by an instance mask or by dropping the instance is not visible to the exam.
- **No prim insertion or removal, no instancing, no pause** (the spec's boundary). Live pause and
  add/delete while rendering stay with Joe in the Solaris viewport.
