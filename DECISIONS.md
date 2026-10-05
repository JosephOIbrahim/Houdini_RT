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
