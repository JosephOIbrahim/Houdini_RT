#!/usr/bin/env bash
# drafts/s3-delegate-shell.check.sh — CANDIDATE exam for s3-delegate-shell (frontier mode).
# Not trusted until a human moves it to verify/s3_delegate.sh, reviews it, and commits.
#
# Called by the orchestrator as: s3_delegate.sh <SEED>   (fresh 64-bit nonce per run)
# Contract: specs/build-spec.md §s3-delegate-shell.
#
# What a cheating implementation would have to do to pass:
#   - build a real Hydra plugin DLL that Houdini 22's own hython/husk can load from the
#     exam-controlled staging dir (build/plugin_stage) with ambient plugin paths masked —
#     a fake "HoudiniRT" renderer shipped elsewhere in Houdini's tree is invisible to the
#     hermetic env and fails the provenance assertion on pxr.Hd.getLoadedPlugins();
#   - render, through husk, a 256x256 image whose pixels equal a seed-derived constant C
#     (computed by THIS script from the nonce, never handed to the delegate — it must come
#     out of the USD stage via Hydra), within 1e-3 of BOTH the standalone kernel's image
#     for the same seed AND the analytic constant itself;
#   - stamp the renderer name and the exact full nonce into the EXR header, read back with
#     OIIO (an independent reader);
#   - name a discrete GPU, keep the Vulkan validation layer silent in every log;
#   - make delegate.Pause() demonstrably block until an in-flight frame drains: ordered,
#     timestamped HRT_* markers plus a driver-side thread join that fails if Pause returns
#     early. The standalone kernel it is compared against was itself proven by the sealed
#     s1/s2 exams, so the reference chain does not trust the delegate at all. Reproducing
#     all of that under a nonce it did not choose is as hard as writing the delegate.
#
# Exit codes mirror orchestrator/verify.ts:
#   11 shader compile  12 plugin build/link  13 toolchain or binary missing
#   14 validation layer  15 runtime / registration / header  16 pixel mismatch  17 invariant

set -uo pipefail
SEED="${1:-$RANDOM$RANDOM}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/build/s3"
STAGE_DIR="$ROOT/build/plugin_stage"
BUILD_LOG="$ROOT/verify/build.log"
RUN_LOG="$ROOT/verify/run.log"
JUDGE_LOG="$ROOT/verify/judge.log"
PROBE_LOG="$ROOT/verify/probe.log"
EVIDENCE="$ROOT/verify/evidence.json"
CFG="$OUT/config.json"
USD="$OUT/stage.usd"
DELEGATE_EXR="$OUT/delegate.exr"
STANDALONE_F32="$OUT/standalone.f32"
mkdir -p "$OUT" "$STAGE_DIR"
: > "$BUILD_LOG"; : > "$RUN_LOG"; : > "$JUDGE_LOG"; : > "$PROBE_LOG"
rm -f "$EVIDENCE" "$CFG" "$USD" "$DELEGATE_EXR" "$STANDALONE_F32"

emit() { printf '{"stage":"%s","seed":"%s"%s}\n' "$1" "$SEED" "${2:-}" > "$EVIDENCE"; }
winpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }

# The standalone host parses --seed with strtoul into uint32 (s1 contract). Reduce the nonce.
SEED32=$(( SEED % 4294967291 ))
[ "$SEED32" -gt 0 ] || SEED32=1

PY=python3; command -v python3 >/dev/null 2>&1 || PY=python

# Hermetic Houdini environment: ambient plugin paths masked, only the exam's staging dir
# visible. Every hython/husk invocation runs inside this.
hermetic() {
  export HOUDINI_PATH="$(winpath "$STAGE_DIR")"
  export PXR_PLUGIN_LOAD_LIST="$(winpath "$STAGE_DIR/HoudiniRT.dll")"
  export HOUDINI_HDAA_LIBRARY_PATH=""
  unset HOUDINI_SESSION_VARS 2>/dev/null || true
}

echo "== [0] prerequisites (HFS pinned to Houdini 22) ==" | tee -a "$BUILD_LOG"
command -v "$PY" >/dev/null 2>&1 || { emit "binary-missing" ',"which":"python"'; echo "FAIL: python not found"; exit 13; }
[ -n "${HFS:-}" ] || { emit "binary-missing" ',"which":"HFS"'; echo "FAIL: HFS not set (Houdini install root)"; exit 13; }
HYTHON="$HFS/bin/hython.exe"; HUSK="$HFS/bin/husk.exe"
[ -x "$HYTHON" ] && [ -x "$HUSK" ] || { emit "binary-missing" ',"which":"hython/husk"'; echo "FAIL: hython or husk missing under $HFS/bin"; exit 13; }
HVER="$(head -n1 "$HFS/version.txt" 2>/dev/null | tr -d '\r' || true)"
HMAJOR="${HVER%%.*}"
[ "$HMAJOR" = "22" ] || { emit "binary-missing" ",\"which\":\"Houdini version ${HVER:-unknown}\""; echo "FAIL: this exam pins Houdini 22, found '${HVER:-none}'"; exit 13; }
[ -n "${OPENPBR:-}" ] && [ -f "$OPENPBR/openpbr.h" ] || { emit "binary-missing" ',"which":"OPENPBR"'; echo "FAIL: OPENPBR must point at a checkout of adobe/openpbr-bsdf"; exit 13; }
[ -n "${VULKAN_SDK:-}" ] || { emit "binary-missing" ',"which":"VULKAN_SDK"'; echo "FAIL: VULKAN_SDK not set"; exit 13; }
echo "HFS=$HFS version=$HVER" | tee -a "$BUILD_LOG"

echo "== [0b] derive the scene from the nonce (exam-side, independent of the program) ==" | tee -a "$BUILD_LOG"
# The exam owns the scene: a full-screen emissive quad of color C. C is derived here from
# the nonce and written into the USD stage; the delegate must recover it through Hydra.
# The standalone kernel (the reference side) may read config.json directly.
"$PY" - "$SEED" > "$CFG" <<'PY' || { emit "binary-missing" ',"which":"config generator"'; echo "FAIL: config generation failed"; exit 13; }
import json, random, sys
seed = int(sys.argv[1])
rng = random.Random(seed)
color = [round(rng.uniform(0.05, 0.95), 6) for _ in range(3)]
json.dump({"seed": seed, "size": 256, "color": color,
           "spp_husk": 256, "spp_probe": 262144}, sys.stdout)
PY

# Write the USD stage with Houdini's own Usd (hermetic: no plugin paths visible).
( hermetic
  timeout 120 "$HYTHON" - "$(winpath "$CFG")" "$(winpath "$USD")" <<'PY' >>"$BUILD_LOG" 2>&1
import json, sys
from pxr import Usd, UsdGeom, Sdr, Gf
cfg = json.load(open(sys.argv[1]))
stage = Usd.Stage.CreateNew(sys.argv[2])
mtl = stage.DefinePrim("/Materials/flat", "UsdPreviewSurface")
mtl.CreateAttribute("inputs:emissiveColor", Sdr.ValueType.Vector3f).Set(tuple(cfg["color"]))
mtl.CreateAttribute("inputs:emissiveIntensity", Sdr.ValueType.Float).Set(1.0)
mtl.CreateAttribute("inputs:diffuseColor", Sdr.ValueType.Color3f).Set((0.0, 0.0, 0.0))
quad = UsdGeom.Quad.Define(stage, "/Quad")
quad.CreatePointsAttr([Gf.Vec3f(-1,-1,0), Gf.Vec3f(1,-1,0), Gf.Vec3f(1,1,0), Gf.Vec3f(-1,1,0)])
quad.CreateFaceVerticesAttr([(0, 1, 2, 3)])
quad.AddTranslateOp().Set(Gf.Vec3f(0.0, 0.0, -1.0))
rel = quad.CreateRelationship("material:binding")
rel.CreateTarget(mtl.GetPrimPath())
cam = UsdGeom.Camera.Define(stage, "/Camera")
cam.CreateFocalLengthAttr(12.0)   # 24 mm sensor -> 90 deg vfov -> the 2x2 quad fills the frame
cam.CreateSensorSizeAttr(Gf.Vec2f(36.0, 24.0))
stage.Save()
print("stage written:", sys.argv[2])
PY
)
RC=$?
[ "$RC" -eq 0 ] && [ -s "$USD" ] || { emit "binary-missing" ",\"which\":\"stage.usd (hython rc $RC)\""; echo "FAIL: could not write the USD stage (see verify/build.log)"; exit 13; }

echo "== [1] build (MSVC via a generated .bat helper) ==" | tee -a "$BUILD_LOG"
# The exam generates its own build helper under build/ so the exact compile line is part of
# the trusted script, not a file the worktree could pre-place. s3 is Windows/Houdini only:
# there is no cloud fallback (the exam needs hython/husk).
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) ;;
  *) emit "binary-missing" ',"which":"Windows + HFS"'; echo "FAIL: s3 requires Windows with Houdini at %HFS%"; exit 13 ;;
esac

cat > "$OUT/build_s3.bat" <<'BAT'
@echo off
REM generated by s3_delegate.sh - MSVC build helper for the s3 exam.
REM Exit 11: shader compile failed. Exit 12: host or plugin compile/link failed. Exit 13: toolchain missing.
REM Arguments: %1 = repo root, %2 = output dir (build/s3), %3 = plugin staging dir (build/plugin_stage)
setlocal enabledelayedexpansion
set "ROOT=%~1"
set "OUT=%~2"
set "STAGE=%~3"
if "%ROOT%"=="" exit /b 13
if "%HFS%"=="" (echo s3_build: HFS is not set & exit /b 13)
if not exist "%HFS%\bin\husk.exe" (echo s3_build: no husk under %%HFS%%\bin & exit /b 13)
if "%OPENPBR%"=="" (echo s3_build: OPENPBR is not set & exit /b 13)
if "%VULKAN_SDK%"=="" (echo s3_build: VULKAN_SDK is not set & exit /b 13)
if not exist "%STAGE%" mkdir "%STAGE%"
if not exist "%OUT%" mkdir "%OUT%"

set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" (echo s3_build: vswhere.exe not found & exit /b 13)
for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSROOT=%%i"
if "%VSROOT%"=="" (echo s3_build: no Visual Studio with the C++ x64 toolset found & exit /b 13)
call "%VSROOT%\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 (echo s3_build: vcvars64.bat failed & exit /b 13)

where glslangValidator >nul 2>&1
if errorlevel 1 set "PATH=%VULKAN_SDK%\Bin;%PATH%"

echo s3_build: glslangValidator furnace.comp
glslangValidator -V --target-env vulkan1.2 -S comp -DFURNACE_TEX_LUTS -I"%OPENPBR%" "%ROOT%\src\furnace\furnace.comp" -o "%STAGE%\furnace_tex.spv"
if errorlevel 1 (echo s3_build: shader compile failed & exit /b 11)

echo s3_build: cl furnace_host.cpp (standalone kernel, image mode)
pushd "%OUT%"
cl /nologo /std:c++17 /O2 /EHsc /bigobj /W3 /wd4305 /wd4996 /I"%OPENPBR%" /I"%VULKAN_SDK%\Include" "%ROOT%\src\furnace\furnace_host.cpp" /Fe:"%OUT%\furnace_host.exe" /Fo:"%OUT%\\" /link /LIBPATH:"%VULKAN_SDK%\Lib" vulkan-1.lib
set "RC=%ERRORLEVEL%"
popd
if not "%RC%"=="0" (echo s3_build: standalone host compile/link failed ^(%RC%^) & exit /b 12)

echo s3_build: cl src\hdRT\*.cpp -^> HoudiniRT.dll
set "SRCS="
for /r "%ROOT%\src\hdRT" %%f in (*.cpp) do set "SRCS=!SRCS! "%%~ff""
if not defined SRCS (echo s3_build: no sources under src\hdRT & exit /b 12)
pushd "%STAGE%"
cl /nologo /std:c++17 /O2 /EHsc /bigobj /W3 /wd4305 /wd4996 /I"%HFS%\include" /I"%OPENPBR%" %SRCS% /Fe:"%STAGE%\HoudiniRT.dll" /Fo:"%STAGE%\\" /link /LIBPATH:"%HFS%\lib" hydra.lib usd.lib tf.lib arch.lib tbb.lib
set "RC=%ERRORLEVEL%"
popd
if not "%RC%"=="0" (echo s3_build: plugin compile/link failed ^(%RC%^) & exit /b 12)
if not exist "%STAGE%\HoudiniRT.dll" exit /b 12
echo s3_build: ok
exit /b 0
BAT

MSYS_NO_PATHCONV=1 cmd.exe /c "$(winpath "$OUT/build_s3.bat")" "$(winpath "$ROOT")" "$(winpath "$OUT")" "$(winpath "$STAGE_DIR")" >>"$BUILD_LOG" 2>&1
RC=$?
case "$RC" in
  0) ;;
  11) emit "cmake-configure" ',"which":"glslangValidator"'; echo "FAIL: shader compile (see verify/build.log)"; exit 11 ;;
  12) emit "build-link"; echo "FAIL: host or plugin compile/link (see verify/build.log)"; exit 12 ;;
  *)  emit "binary-missing" ",\"which\":\"s3_build rc $RC\""; echo "FAIL: build helper exited $RC (toolchain?)"; exit 13 ;;
esac
[ -f "$STAGE_DIR/HoudiniRT.dll" ] || { emit "binary-missing" ',"which":"HoudiniRT.dll"'; echo "FAIL: plugin DLL missing from staging dir"; exit 13; }
[ -f "$STAGE_DIR/furnace_tex.spv" ] || { emit "binary-missing" ',"which":"furnace_tex.spv"'; echo "FAIL: SPIR-V missing"; exit 13; }
