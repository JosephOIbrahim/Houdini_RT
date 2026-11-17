# GPU accumulation and resolve probes

These supplementary probes exercise the public GPU backend directly and judge
saved pixels and actual work counters independently. They do not replace or
modify the sealed s4/s5 exams, and are not a sealed s6 exam.

`gpu_probe.cpp` writes RGBA float32 images and CSV counters for batch sizes 1, 2,
8 and 32 at 1, 7, 33 and 257 accumulated samples. Further cases cover rejected
inputs, discarded partial frames, resize and resize-back, color, dome, camera and
seed changes, empty scenes and recovery. `judge_gpu.py` uses only Python's standard
library: it compares batch results, checks flat Lambert surfaces against analytic
albedo × dome × coverage, and verifies exact operation/readback counts.

Build the candidate renderer and both shaders first. From an x64 Visual Studio
2022 Developer Command Prompt, with `VULKAN_SDK` set, use a fresh output directory:

```bat
set "HRT_TEST_ROOT=%CD%"
set "HRT_TEST_OUT=%HRT_TEST_ROOT%\build\tests\gpu-accum-run1"
mkdir "%HRT_TEST_OUT%"
pushd "%HRT_TEST_OUT%"
cl /nologo /std:c++20 /O2 /EHsc /D_CRT_SECURE_NO_WARNINGS ^
 /I"%VULKAN_SDK%\Include" /I"%HRT_TEST_ROOT%\src\hdRT" ^
 "%HRT_TEST_ROOT%\tests\gpu_accumulation\gpu_probe.cpp" ^
 "%HRT_TEST_ROOT%\src\hdRT\vkFurnace.cpp" /Fe:gpu_probe.exe ^
 /link /LIBPATH:"%VULKAN_SDK%\Lib" vulkan-1.lib
```

Stop if compilation fails. Then run and judge:

```bat
gpu_probe.exe "%HRT_TEST_ROOT%\build\plugin_stage\shaders\furnace.spv" "%HRT_TEST_OUT%" > run.log 2>&1
python -B "%HRT_TEST_ROOT%\tests\gpu_accumulation\judge_gpu.py" "%HRT_TEST_OUT%"
popd
```

Require both exit codes to be zero and `judge.json` to contain `passed: true`.
Validation must be active and silent, including destruction. Timing fields are
host submit-to-fence wall times, not GPU timestamps or an FPS guarantee. Do not run
this probe concurrently with a GPU benchmark or sealed gate.

The standalone reset cases do not prove asynchronous generation cancellation or
Houdini pause behavior. Those require the actual delegate and regular GUI workflow
test, including incomplete sample counts at pause, a native pause acknowledgement,
and stable work counters during the hold.
