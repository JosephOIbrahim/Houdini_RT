# DECISIONS — durable lessons, dated

## 2026-10-04 (seed from the cloud workspace runs)
- Texture-LUT mode for the OpenPBR BSDF is the production mode: 289 KB SPIR-V, ~2.4 s pipeline build on a software driver. Embedded-array mode produced 1.2 MB SPIR-V and ran the software driver's compiler out of memory (~6 GB). Treat array mode as a diagnostic only.
- The CPU reference table was produced with array LUTs and the GPU uses filtered textures. No offset was visible at 65k samples per row; if a row drifts near tolerance on real hardware, that filtering difference is the first suspect, not the sampler.
- The coat row at roughness 0.1, view cosine 0.1 sits at +2.6% on both CPU and GPU. It is the documented coat reciprocity-vs-energy trade in the BSDF, and the exam allows 3% there. Do not "fix" it.
- The host builds with no compiler extensions under g++ and clang++ pedantic C++17. MSVC specifics to expect: `/EHsc`, `/std:c++17`, possibly `/bigobj` for the LUT translation unit, and `strtoul` returning 32-bit `unsigned long` (the exam reduces the seed mod 2^32−5 for this reason).

## 2026-10-05 (human)
- Reference code for BLAS/TLAS construction and GLSL ray queries is in `third_party/sascha_willems_vulkan/` (MIT). Read its README first. Adapt, do not link: it depends on a framework this project does not use.
- In GLSL an acceleration structure is declared `layout(binding = N) uniform accelerationStructureEXT name;`. The `uniform` qualifier is required.

## 2026-10-05 (workstation facts, verified by listing the install)
- Houdini 22 for s3 and s4: `C:/Program Files/Side Effects Software/Houdini 22.0.429` (HFS). `bin/hython.exe` and `bin/husk.exe` exist. Also installed: 22.0.417, 22.0.400, 22.0.397, 22.0.368, 21.0.773. Pin 22.0.429 in exams.
- The HDK ships `toolkit/include/pxr/imaging/hd/renderThread.h`, `meshUtil.h`, `smoothNormals.h` and `renderDelegate.h` in 22.0.429. HdRenderThread, HdMeshUtil and Hd_SmoothNormals are available.
- Exam authors: run every analytic reference function on one hand-checked case before proposing the exam. The first s2 draft had a reference that returned 0.0 for every ray and a byte scan that rejected correct shaders.

## 2026-10-05 (references for s3)
- `third_party/openusd_hdEmbree/` is Pixar's example Hydra delegate (TOST licence): the template for `src/hdRT/`. `third_party/fragment_m3_check/` shows a working hermetic-hython + HDK exam. Read each README first. Adapt, do not link.
