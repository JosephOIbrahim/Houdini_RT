# usd-wg Utah teapot (test asset for the s4 exam)

`UtahTeapot.usd` is the scene the step 4 exam (`verify/s4_firstlight.sh`) renders through the
delegate and through Karma CPU. The exam pins this exact file by sha256; do not edit or replace it.

| | |
|---|---|
| Source | https://github.com/usd-wg/assets, `full_assets/Teapot/geo/UtahTeapot.usd` |
| Commit | `3b75c2dad6a494897557dcca0098257bcf42a8c6` (2026-08-24) |
| Size | 259,621 bytes |
| sha256 | `7f64b77cd959e8c2b6760bb63ef40a880b6d5daa0f501251294069839ec9f4d4` |
| Licence | Public Domain, CC0 (declared in the asset's README, `full_assets/Teapot/README.md`, section "License") |
| Added | 2026-11-11, by Claude at Joe's yes |

Fetched with:

```
curl -fsSL -o UtahTeapot.usd https://raw.githubusercontent.com/usd-wg/assets/3b75c2d/full_assets/Teapot/geo/UtahTeapot.usd
sha256sum UtahTeapot.usd
```

The exam renders it as the polygon cage it is authored as (`subdivisionScheme = none` is set by the
exam, not here): 1,236 quads in four open shells.
