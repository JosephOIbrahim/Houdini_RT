This is a CPU-only model qualification witness, not a Karma parity test. It preserves the distinction found by the first strict USD comparison: the original failed run and its original bounds remain unchanged.

The official [OpenPBR 1.1.1 specification, commit f8d6d947](https://github.com/AcademySoftwareFoundation/OpenPBR/blob/f8d6d947dfae4c9b599965a86c22826ea7a8dbfb/index.html#L585-L602) describes both view-dependent albedo scaling and a reciprocal glossy-diffuse approximation. It leaves their selection to implementers. The same version discusses multiple microfacet scattering approaches at lines 369–377. Both the installed MaterialX nodedef and Adobe implementation declare OpenPBR 1.1.1; differing version numbers or default roughness values do not explain this result.

The archived specification is `build/m6/model-qualification/spec-v1.1.1.html`, SHA-256 `3bec2edd0abd1f6a626777783f945825d771caf680c2e1e19abe7c3329e1595a`. Its Apache-2.0 license is retained beside it. `provenance.json` records installed public MaterialX source hashes and function names; these sources and the numeric coefficient translation use Apache-2.0 (see `LICENSE`). No private Karma implementation is used.

For the fixture's normal view, the public MaterialX implementation gives:

```
alpha = specular_roughness^2
C = 1 + F(halfAngle) * (1 - E_white(view)) / E_white(view)
transmitted = 1 - E_dielectric(view) * C
f*cos = base_color * NdotL/pi * transmitted + D*F*G2*C/4
```

The `mx_ggx_dir_albedo_analytic`, `mx_ggx_energy_compensation`, `mx_dielectric_bsdf`, and `mx_layer_bsdf` functions supply these terms. Adobe instead applies reciprocal incident/view energy-complement factors to diffuse reflection and uses separate microfacet multiple-scattering lobes. Equal furnace energy does not imply identical angular reflectance.

The script reads the actual USD fixture values. For the rectangle it integrates the public generalized-Schlick model with 16×16 and 24×24 Gauss quadrature over the emitter and averages the same interior pixel centers as the comparison. Nothing is fitted to image values. The public GLSL is a predictive witness, not evidence that Karma internally executes that code.

Initial results against the preserved strict run:

| Witness | Public MaterialX prediction | Measured Karma | Interpretation |
| --- | --- | --- | --- |
| Default dielectric, distant light | 0.4051539311 | 0.4052734375 | Within 0.0295%; supports a layering-model distinction from Adobe/HoudiniRT's 0.4216308594 |
| Rough metal, rectangle | (0.22744234, 0.08176688, 0.01870070) | (0.23120426, 0.08670159, 0.02023034) | Differences remain 1.63%, 5.69%, 7.56%; the exact private Karma metal cause is unresolved |

The rectangle quadrature changed by less than 1e-16 between orders. This establishes numerical convergence of this public-model calculation, not agreement with Karma. The images in that first run used half-float beauty output; a separate format correction uses explicit float output for subsequent analytic acceptance.

Reproduce with Houdini 22.0.429's `hython`, using a new output directory:

```powershell
hython -B tests/reliable_materials/model_qualification/materialx_witness.py `
  build/m6/runs/20261117T143009Z-usd-116e2673 `
  --out build/m6/model-qualification/reproduced
```

`--spec` and `--license` can point to copies of the pinned official files. Their hashes and installed source hashes are checked. A `passed` qualification receipt means the pinned sources and calculations were reproduced; it explicitly sets `karma_parity=false` and `renderer_acceptance=false`.

Keep independent analytic limits, furnace/energy/reciprocity checks, and separately integrated CPU transport against the declared Adobe model as strict acceptance checks. The CPU and GPU share Adobe's BSDF model, so their agreement validates transport/integration rather than constituting an independent BSDF implementation. Keep non-Lambert Karma comparisons as measured interoperability diagnostics, retain the original failed comparison, and disclose the observed appearance differences. Do not tune the renderer to these images or loosen the original thresholds to label the failed comparison a pass.
