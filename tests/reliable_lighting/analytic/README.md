This is supplementary Phase 3 acceptance, not a sealed lighting exam.

`frozen/freeze.json` records the oracle source, semantic fixture definitions,
tolerances and 40 independent expected images before any candidate GPU run.
`oracle.py` integrates area in double precision with Gaussian quadrature; it
does not import production code or reproduce its sampling/PDF implementation.
Disk/rectangle center closed forms and a separately integrated sphere verify
the oracle. Frozen per-image coarse/fine convergence is also required.

This is oracle revision 2. The preserved first attempt incorrectly treated
finite emitters as transparent in mixed scenes. The correction integrates
nearest-emitter visibility and subtracts their visible projected solid angle
from the dome. It changed the physical reference, not the fixed image-error
ceilings. The prior freeze hash and diagnostic run are recorded in contract.json.
The independent statistical contract was frozen before the second candidate
run: each mean must satisfy both its original fixed ceiling and 5 estimated
standard errors plus an explicit quadrature/numerical floor. All per-channel
standard errors and actual numerical floors appear in judge.json.

The native probe consumes only `fixtures.hpp`; it cannot see oracle images.
Three seeds, 2048 spp each, exercise direct distant/rect/disk/sphere lighting,
normalization as already-translated radiance, transformed emitter bases,
intensity/exposure, mixed unequal light powers, permutation, removal, dome MIS,
one-sided emission, finite shadow segments and soft shadows. Camera z1 is below
all light shapes. Bounces0 allows the primary diffuse hit and one following ray,
so no secondary surface scattering enters this direct-light reference.

Each image also checks real deterministic center guides: world/eye normal,
projected depth, ray distance from the near-plane origin, albedo, host primitive
ID and original polygon ID (both triangles share face41), instance=-1 and
coverage. Five scenes are independently re-created for exact live/fresh checks.
Vulkan validation must be active and silent, including destruction.
Actual guide and beauty copies count as two readbacks and width*height*80 bytes.

This does not prove UsdLux attribute translation, authored normal interpolation,
camera-visible emitters, all AOV miss/perspective cases, or universal MIS variance
improvement. Those require supplementary headless USD/Karma and host tests.

`run_probe.py --shaders PATH` archives inputs and compiles only. Adding `--run`
is authorized only after root grants the GPU slot. The archived probe is judged
by a separate process; receipts bind source, SPIR-V, fixture and result hashes.

`../run_gate.py s5` executes unchanged sealed scripts in an ignored copied runbox.
It never directs exam output at the worktree's protected `verify/` directory.
The runbox staged plugin is the exact gate artifact for downstream verification.

`run_usd.py --plugin PATH` verifies 15 semantic USD cases with three explicit
seeds each through both Karma CPU and the candidate, using the exact staged
plugin supplied. It authors actual transform scale, raw normalize/intensity/
exposure/angle, and a diffuse-only MaterialX reference. The independent oracle
and explicit statistical/fixed limits are recorded before any render. Its
fixtures use the installed Karma `karma:global:randomseed` property. The run
uses isolated workspace preferences and never changes a regular installation.

When this accepted pack is stored at tests/reliable_lighting/analytic, the
native runner is invoked from the repository root as:

```
python -B tests/reliable_lighting/analytic/run_probe.py --shaders build/m5/cmake/shaders --run
python -B tests/reliable_lighting/analytic/run_usd.py --plugin PATH_TO_EXACT_GATE_PLUGIN
```

GPU runs require the team's exclusive GPU slot. Omit `--run` for compile only.
Expected images and frozen contracts are checked inputs, not generated from a
candidate. `make_fixtures.py` is the historical CPU generator; normal validation
consumes its frozen outputs without rewriting them. New oracle revisions need
their own directory and a new pre-candidate freeze, preserving the old contract.
