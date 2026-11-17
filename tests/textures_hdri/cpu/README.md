# Native CPU ingress regressions

These tests do no GPU work. They compile against Houdini 22.0.429 and check
MaterialX image/UV graph translation, indexed UV domains, immutable revisions,
dome orientation/color metadata, and the existing material/normal regressions.
The five native Material Library exports must first exist in
`build/m7/native-texture-fixture`; their generator is preserved in
[`../native_fixture/`](../native_fixture/). The runner hashes those exports and
assets before and after.

From a Visual Studio 2022 developer prompt at the repository root:

```powershell
python tests/textures_hdri/cpu/run.py --out build/m7/cpu-receipt-new
```

The wrapper uses one MSBuild worker and compiles shared native sources once with
four compiler jobs. `receipt.json` records commands, logs, executable hashes and
the exact compiled source dependencies. The initial complete run passed all
three executables in `build/m7/cpu-receipt-01`.
