# Live-edit regression probes

These CPU probes preserve the test sources already compiled and run against Houdini
22.0.429 during implementation. They exercise the production source without starting
Houdini, creating a Vulkan device, or changing a renderer installation.

- `scene_snapshots.cpp`: exact rotated bounds, exclusion of unused points and hidden
  meshes, immutable snapshots, shared geometry, no-op revisions, and fresh versions.
- `buffer_publication.cpp`: pointer-preserving publication, mapped reader lifetime,
  nested readers, consumed staging storage, and concurrent mapping/publication.

From the repository root in an **x64 Visual Studio 2022 Developer Command Prompt**,
set `HFS` to the Houdini installation whose HDK is being used:

```bat
set "HFS=C:\Program Files\Side Effects Software\Houdini 22.0.429"
set "PATH=%HFS%\bin;%PATH%"
cmake -S tests/live_edits -B build/live-edits-tests -G "Visual Studio 17 2022" -A x64 -DCMAKE_PREFIX_PATH="%HFS%/toolkit/cmake"
cmake --build build/live-edits-tests --config Release --parallel 1 -- /nodeReuse:false
ctest --test-dir build/live-edits-tests -C Release --output-on-failure
```

The HDK's `Houdini` and `HoudiniThirdParty` targets supply the matching USD/Hydra,
Python, and platform libraries. Use Release; the checks remain active with `NDEBUG`.
The binaries and build files stay under `build/live-edits-tests`.

The sources preserve the successful native probes, with LF line endings. This
CMake wrapper passed configure, Release build, and both CTest cases on 2026-11-16
with Houdini 22.0.429, MSVC 19.44, and CMake 4.2.1 (2/2 passed in 0.64 seconds).
The single-worker build avoids MSBuild worker connection failures in restricted
environments. Logs and `receipt.json` are under `build/live-edits-tests`.
These focused checks supplement the sealed exams under `verify/` and the live
Solaris workflow check.

See [GPU.md](GPU.md) for the separate Vulkan edit-cost and image-equivalence probe.
