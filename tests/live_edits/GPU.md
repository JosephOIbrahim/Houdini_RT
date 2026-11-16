# Live mesh GPU probe

`gpu_edits.cpp` exercises `HdRTVkFurnace::SetMeshes` directly, without Houdini or
Hydra. It writes 128 x 96 RGBA float32 images and actual GPU-work counters for
24 states. `judge_gpu_edits.py` independently checks those artifacts using only
the Python standard library.

Coverage includes transform, color, visibility, points, topology, insertion,
removal, reinsertion, empty scenes, rejected-update recovery, zero scale, and
nonfinite transforms. It compares live edits with fresh renders, checks flat
surfaces against albedo x dome x coverage, and compares a mirrored nonuniform
instance with independently baked geometry. Recovery must reuse cached geometry.
Validation must be active and silent, including teardown.

## Build and run on Windows

Use an x64 MSVC Developer Command Prompt, with `VULKAN_SDK` set to the installed
Vulkan SDK. Build/stage the current renderer first so its matching shader is at
`build/plugin_stage/shaders/furnace.spv`. Run these commands from the repository
root, choosing a fresh output directory for each run:

```bat
set "HRT_TEST_ROOT=%CD%"
set "HRT_TEST_OUT=%HRT_TEST_ROOT%\build\tests\gpu-edits-run1"
mkdir "%HRT_TEST_OUT%"
pushd "%HRT_TEST_OUT%"
cl /nologo /std:c++20 /O2 /EHsc /D_CRT_SECURE_NO_WARNINGS ^
  /I"%VULKAN_SDK%\Include" /I"%HRT_TEST_ROOT%\src\hdRT" ^
  "%HRT_TEST_ROOT%\tests\live_edits\gpu_edits.cpp" ^
  "%HRT_TEST_ROOT%\src\hdRT\vkFurnace.cpp" /Fe:gpu_edits.exe ^
  /link /LIBPATH:"%VULKAN_SDK%\Lib" vulkan-1.lib
```

Stop if compilation fails. If using Houdini's Vulkan headers/libraries instead,
set `HFS` to the installed Houdini directory and replace the SDK include and
library paths with `%HFS%\toolkit\include` and `%HFS%\custom\houdini\dsolib`.
The Vulkan loader and Khronos validation layer must still be available.

```bat
gpu_edits.exe "%HRT_TEST_ROOT%\build\plugin_stage\shaders\furnace.spv" "%HRT_TEST_OUT%" > run.log 2>&1
```

Require exit 0 before judging; exit 14 means validation errors, and exit 15
means initialization, usage, or runtime failure. Then run the separate judge:

```bat
python -B "%HRT_TEST_ROOT%\tests\live_edits\judge_gpu_edits.py" "%HRT_TEST_OUT%"
popd
```

Require judge exit 0 and `passed: true` in the output directory's `judge.json`.
All generated files remain under the chosen output directory. For existing
artifacts whose log is elsewhere, pass `--log path\to\run.log`.

This is a focused GPU regression probe. It does not replace the sealed s4/s5
Hydra/Houdini gates, image-reference checks, or large-scene scaling checks.
Do not run it concurrently with a sealed GPU gate.
