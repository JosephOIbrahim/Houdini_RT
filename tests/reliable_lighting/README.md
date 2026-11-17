# Reliable-lighting CPU regressions

These tests execute production CPU translation and output code without starting
Houdini or creating a Vulkan device. `mesh_normals` covers interpolation, indexed
data, holes/orientation, malformed inputs and actual mesh Sync revision behavior.
`aov_outputs` covers supported names/formats, typed fields, integer ID precision,
misses and offset pixel addressing. Checks remain active in Release builds.

From an x64 Visual Studio 2022 Developer Command Prompt at the repository root:

```bat
set "HFS=C:\Program Files\Side Effects Software\Houdini 22.0.429"
set "PATH=%HFS%\bin;%PATH%"
cmake -S tests/reliable_lighting -B build/m5/cpu/cmake -G "Visual Studio 17 2022" -A x64 -DCMAKE_PREFIX_PATH="%HFS%/toolkit/cmake"
cmake --build build/m5/cpu/cmake --config Release --parallel 1 -- /nodeReuse:false
ctest --test-dir build/m5/cpu/cmake -C Release --output-on-failure
```

Verified against Houdini 22.0.429, MSVC 19.44 and CMake 4.2.1 on 2026-11-17:
2/2 passed. Configure/build/test logs are in `build/m5/cpu`. GPU transport and
installed Houdini workflow evidence are separate; these tests do not replace them.
