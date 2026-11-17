# OpenPBR sampler endpoint regression

The legacy generator maps 24 integer bits to `[0,1)`. Zero is possible.
Adobe's cosine sampler uses `z = sqrt(u.y)`, and its diffuse lobe rejects
`z == 0` with zero PDF. Under a constant dome that drops a complete sample.
The OpenPBR-only fix maps 23 bits to bin midpoints:

```text
u = (float(word >> 9) + 0.5) * 2^-23
```

Every result is exactly representable as a float, from `2^-24` through
`1-2^-24`. The midpoint addition is exact even in the highest bin. Using 24
bits with `+0.5` instead would round the top value to one. Legacy Lambert,
camera jitter and direct-light random streams retain their original mapping.

Run from the repository root in a Windows environment with VS2022 Build Tools
and the existing pinned GLM dependency under `build/m6/deps`:

```powershell
python -B tests/reliable_materials/run_rng_endpoints.py --out build/m6/rng-endpoints-new
```

The C++ observation executable enumerates all 8,388,608 midpoint bins and the
128-by-96, 4,096-sample PCG streams for seeds 101, 107 and 113. The Python
judge checks exact interior endpoints, unique increasing midpoints, exact sum,
and the old/new Adobe sample rejection behavior. It also checks sample versus
evaluation consistency. It does not assert ideal Lambert weight at the extreme
grazing angle, where the upstream LUT approximation differs slightly.

To correlate the predicted coordinates with the preserved pre-fix FLOAT EXRs,
run the following with Houdini 22.0.429's `hython` (CPU image reads only):

```text
hython -B tests/reliable_materials/judge_rng_pixels.py
  build/m6/rng-endpoints-02/observations.json
  build/m6/runs/20261117T145335Z-usd-cbe7bd99
  --out build/m6/rng-endpoints-02/pixel-correlation.json
```

The actual executed receipts are `build/m6/rng-endpoints-02/receipt.json` and
`pixel-correlation.json`, both passed. All 11 predicted rejected samples match
the old image's affected pixel set exactly, with no missing or extra pixels.
The initial `rng-endpoints-01` failure is preserved: its additional ideal-weight
assertion was inappropriate for this endpoint test and was replaced with the
sample/evaluation consistency check before the successful run.

These tests establish float-domain behavior and the historical defect. They do
not execute the production shader or replace corrected GPU and sealed rendering
regressions. The observation executable uses the repository's Apache-2.0 Adobe
OpenPBR headers; their source hashes and the recorded shader hash are in the
receipt. No production or protected verification files are modified by these
tests.
