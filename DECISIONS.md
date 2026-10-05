# DECISIONS — durable lessons, dated

## 2026-10-04 (seed from the cloud workspace runs)
- Texture-LUT mode for the OpenPBR BSDF is the production mode: 289 KB SPIR-V, ~2.4 s pipeline build on a software driver. Embedded-array mode produced 1.2 MB SPIR-V and ran the software driver's compiler out of memory (~6 GB). Treat array mode as a diagnostic only.
- The CPU reference table was produced with array LUTs and the GPU uses filtered textures. No offset was visible at 65k samples per row; if a row drifts near tolerance on real hardware, that filtering difference is the first suspect, not the sampler.
- The coat row at roughness 0.1, view cosine 0.1 sits at +2.6% on both CPU and GPU. It is the documented coat reciprocity-vs-energy trade in the BSDF, and the exam allows 3% there. Do not "fix" it.
- The host builds with no compiler extensions under g++ and clang++ pedantic C++17. MSVC specifics to expect: `/EHsc`, `/std:c++17`, possibly `/bigobj` for the LUT translation unit, and `strtoul` returning 32-bit `unsigned long` (the exam reduces the seed mod 2^32−5 for this reason).
