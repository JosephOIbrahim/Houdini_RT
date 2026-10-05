# DECISIONS — durable lessons, dated

## 2026-10-04 (seed from the cloud workspace runs)
- Texture-LUT mode for the OpenPBR BSDF is the production mode: 289 KB SPIR-V, ~2.4 s pipeline build on a software driver. Embedded-array mode produced 1.2 MB SPIR-V and ran the software driver's compiler out of memory (~6 GB). Treat array mode as a diagnostic only.
- The CPU reference table was produced with array LUTs and the GPU uses filtered textures. No offset was visible at 65k samples per row; if a row drifts near tolerance on real hardware, that filtering difference is the first suspect, not the sampler.
- The coat row at roughness 0.1, view cosine 0.1 sits at +2.6% on both CPU and GPU. It is the documented coat reciprocity-vs-energy trade in the BSDF, and the exam allows 3% there. Do not "fix" it.
- The host builds with no compiler extensions under g++ and clang++ pedantic C++17. MSVC specifics to expect: `/EHsc`, `/std:c++17`, possibly `/bigobj` for the LUT translation unit, and `strtoul` returning 32-bit `unsigned long` (the exam reduces the seed mod 2^32−5 for this reason).

## 2026-10-05 (s1-furnace-4090 GREEN on the RTX 4090)
- Calling a `.bat` from Git Bash: use `MSYS_NO_PATHCONV=1 cmd.exe /c "..."`. Plain `cmd.exe //c` lets MSYS rewrite the Windows paths and mangles the arguments. Every Windows exam that shells out to a batch file hits this.
- Line endings are load-bearing: `.gitattributes` pins `*.sh` to LF and `*.bat` to CRLF. A batch file with LF (or a shell script with CRLF) breaks under its own interpreter before any real error surfaces.
- MSVC host build, confirmed on the 4090: `/bigobj` is **required** for the LUT translation unit (upgrades the 2026-10-04 "possibly"), and `/wd4305 /wd4996` must be silenced for a clean build (`/std:c++17 /O2 /EHsc /W3`).
- Fidelity exams carry a **copy guard**: rows byte-identical to the CPU reference are capped (≤16; s1 landed at 8) so a GPU that merely echoes the reference fails. The kernel must genuinely compute — never reuse or copy reference rows.
## 2026-10-05 (human)
- Reference code for BLAS/TLAS construction and GLSL ray queries is in `third_party/sascha_willems_vulkan/` (MIT). Read its README first. Adapt, do not link: it depends on a framework this project does not use.
- In GLSL an acceleration structure is declared `layout(binding = N) uniform accelerationStructureEXT name;`. The `uniform` qualifier is required.

## 2026-10-05 (s2-rayquery, human + cloud assistant)
- KHR acceleration-structure functions are not exported by vulkan-1.lib; fetch them with vkGetDeviceProcAddr (see `AsApi` in rayq_host.cpp).
- Scratch buffers for AS builds are over-allocated and the device address rounded up to `minAccelerationStructureScratchOffsetAlignment`.
- One TLAS, two instances, visibility masks: cube 0x1, occluder 0x2. Shadow rays use mask 0x2 so they do not see the cube; the light is outside the cube.
- A ray from inside a closed box reports the EXIT distance. The first draft of the s2 exam had the reference returning 0.0; it was corrected by the owner's decision.
- Per-path RNG must be seeded through a full-avalanche hash of (seed, path index). XOR-ing a multiple of the index into the seed gave a reproducible 3.7 sigma furnace outlier for one seed.
- The furnace uses uniform-hemisphere bounces (weight rho * 2 cos), an unbiased estimator with real variance. At rho = 0.8 expect a statistical exam failure roughly once per hundred runs; that is the 3 sigma test, not a bug.
- The s2 exam disassembles `rayq.spv` with `spirv-dis` and fails (exit 11) if no ray-query instruction is present: the kernel must genuinely use inline ray queries — a compute-only fallback or a hand-rolled intersection pass does not count.
- Host protocol the exam greps for (keep it in s3/s4 hosts too): a device line naming the GPU and its type, a `rayQuery: supported` line, and an echo of the injected seed (`seed N:`). Missing any of them is exit 15; a software device is rejected outright.
