The CPU test uses the installed Houdini 22.0.429 USD/Hydra libraries. It covers native defaults, numeric boundaries, unsupported inputs and graphs, neutral displacement, legacy network conversion, material binding changes, deletion/recreation, immutable scene snapshots, and per-face fallback without geometry revision changes.

It also builds real USD-to-Hydra networks through `UsdImagingBuildHdMaterialNetworkFromTerminal`. This catches host-added `typeName:` metadata, proves explicit shader-input `colorSpace:` rejection, and verifies that a promoted material input holding a constant is resolved to a constant parameter before the renderer sees it. Arbitrary connected shader nodes remain outside the profile.

From a Windows developer environment with CMake and Python available:

```powershell
python -B tests/reliable_materials/run_cpu.py --out build/m6/cpu-new
```

Use a new output directory per run. `--hfs` selects the installed Houdini build; `--build` selects the CMake build directory. The receipt binds the executable, test sources, and compiled native dependencies by SHA-256 and records full native/vendor source inventories before and after. Its scope is CPU translation and publication; it makes no GPU, husk, or installed GUI claim.
