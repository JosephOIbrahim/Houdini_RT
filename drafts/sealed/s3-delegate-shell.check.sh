#!/usr/bin/env bash
# verify/s3_delegate.sh - exam for s3-delegate-shell.
# Authored by Claude at Joe's request on 2026-10-06, replacing the truncated draft sealed on 10-05.
# Every Houdini fact below was probed on this workstation against Houdini 22.0.429.
#
# Called by the orchestrator as: s3_delegate.sh <SEED>   (fresh 64-bit nonce per run)
# Contract: specs/build-spec.md, section s3-delegate-shell.
#
# ---------------------------------------------------------------------------------------------
# THE CONTRACT (everything the delegate must do to pass; nothing else is assumed)
#
# BUILD
#   - src/hdRT/CMakeLists.txt exists. The exam configures it with
#       cmake -S src/hdRT -B build/s3/cmake -G "Visual Studio 17 2022" -A x64
#             -DCMAKE_PREFIX_PATH=<HFS>/toolkit/cmake -DHRT_REPO_ROOT=<repo> -DOPENPBR=<openpbr or "">
#     builds Release, then runs  cmake --install build/s3/cmake --config Release
#     --prefix build/plugin_stage.
#   - Known-good link line (probed): find_package(Houdini REQUIRED) then
#       target_link_libraries(<plugin> Houdini HoudiniThirdParty)
#     "Houdini" alone does not link libpxr_hd or python313 (LNK2019).
#   - After install, build/plugin_stage/plugInfo.json exists (it may Include subdirectories),
#     and at least one *.spv under build/plugin_stage contains ray-query instructions.
#
# REGISTRATION
#   - Exactly one HdRendererPlugin type with displayName "HoudiniRT" is discovered, and its
#     library lives under build/plugin_stage. The exam runs hython hermetically
#     (PXR_PLUGINPATH_NAME = build/plugin_stage, user prefs redirected, HOUDINI_PATH = &).
#
# RENDER (headless:  husk -R <plugin id> -r 256 256 -o build/s3/out.exr build/s3/stage.usda)
#   - The image is the closed-box furnace from step 2: a camera inside the unit cube
#     (camera and cube may be hardcoded), Lambertian walls of albedo rho that emit E (RGB).
#     Convention as in step 2: the first hit plus `bounces` further bounces, so every pixel
#     estimates   L_c = E_c * (1 - rho^(bounces+1)) / (1 - rho).
#   - Parameters come from the exam. They are authored on the RenderSettings prim
#     /Render/settings and are ALSO written to the JSON file named by the environment variable
#     HOUDINIRT_S3_CONFIG. Same values in both; read either.
#         houdinirt:seed (int)   houdinirt:spp (int)   houdinirt:furnace:rho (float)
#         houdinirt:furnace:emission (color3f)          houdinirt:furnace:bounces (int)
#     JSON keys: seed, spp, rho, emission [r,g,b], bounces.
#   - The delegate prints these lines (stdout or stderr), in this order:
#         HoudiniRT: validation layer active
#         HoudiniRT: seed=<seed> spp=<spp> device=<Vulkan deviceName>
#         HoudiniRT: render thread start
#         HoudiniRT: render thread stop
#     The validation layer must be enabled when HOUDINIRT_VALIDATION=1 (the exam sets it).
#   - GetRenderStats() should return hrt_seed (int). husk copies delegate stats into the EXR
#     attribute husk:render_stats (observed for Karma). If it is absent the exam warns and
#     relies on the log line; if it is present it must equal the seed.
#
# SOURCE INVARIANTS (static; see the honest limit below)
#   - src/hdRT uses HdRenderThread, and the delegate overrides IsPauseSupported, Pause, Resume.
#
# HONEST LIMITS OF THIS EXAM
#   - Live Pause/Resume is NOT exercised. The only scripted driver, UsdImagingGL.Engine under
#     hython, crashes without a GL context on this machine (probed). Pause is checked
#     statically here and must be checked by a human in the Solaris viewport before step 4.
#   - A constant image is a legitimate answer (cosine-weighted sampling gives zero variance in
#     a furnace), so pixel statistics cannot prove rays were traced. Step 2 proved the kernel;
#     this step proves the shell: build, discovery, settings in, pixels out, GPU, validation.
#
# What a cheat would still have to do: build and install a real Hydra plugin against this HDK,
# be discovered from the staging directory only, be driven by husk, read seed-derived
# parameters it did not choose, write a fresh 256x256 EXR whose three channels each match an
# analytic value computed here, name the discrete GPU, and keep the validation layer silent.
#
# Exit codes mirror orchestrator/verify.ts:
#   11 cmake configure  12 build/link  13 toolchain or product missing
#   14 validation layer  15 runtime / registration / wrong device  16 mismatch  17 invariant
# ---------------------------------------------------------------------------------------------

set -uo pipefail
SEED="${1:-$RANDOM$RANDOM}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/build/s3"
CMK="$OUT/cmake"
STAGE_DIR="$ROOT/build/plugin_stage"
BUILD_LOG="$ROOT/verify/build.log"
RUN_LOG="$ROOT/verify/run.log"
JUDGE_LOG="$ROOT/verify/judge.log"
EVIDENCE="$ROOT/verify/evidence.json"
CFG="$OUT/config.json"
USDA="$OUT/stage.usda"
EXR="$OUT/out.exr"
PIN="22.0.429"
HFS_DIR="${HRT_HFS:-C:/Program Files/Side Effects Software/Houdini $PIN}"
HYTHON="$HFS_DIR/bin/hython.exe"
HUSK="$HFS_DIR/bin/husk.exe"

mkdir -p "$OUT"
: > "$BUILD_LOG"; : > "$RUN_LOG"; : > "$JUDGE_LOG"
rm -f "$EVIDENCE" "$CFG" "$USDA" "$EXR"

emit() { printf '{"stage":"%s","seed":"%s"%s}\n' "$1" "$SEED" "${2:-}" > "$EVIDENCE"; }
mpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
die() { # die <exit> <stage> <extra-json> <message>
  emit "$2" "$3"; echo "FAIL: $4"; exit "$1"
}

# The delegate reads the seed as a signed 32-bit int. Reduce the 64-bit nonce so it survives.
SEED31=$(( SEED % 2147483647 ))
[ "$SEED31" -gt 0 ] || SEED31=1
SPP=64

echo "== [0] prerequisites ==" | tee -a "$BUILD_LOG"
[ -x "$HYTHON" ] || die 13 "binary-missing" ',"which":"hython"' "hython not found at $HYTHON (set HRT_HFS to a Houdini $PIN install)"
[ -x "$HUSK" ]   || die 13 "binary-missing" ',"which":"husk"' "husk not found at $HUSK"
HVER="$("$HUSK" --version 2>&1 | tr -d '\r' | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1)"
[ "$HVER" = "$PIN" ] || die 13 "binary-missing" ",\"which\":\"houdini version\",\"found\":\"$HVER\"" "this exam is pinned to Houdini $PIN, found '$HVER'"
CMAKE="cmake"
command -v cmake >/dev/null 2>&1 || CMAKE="/c/Program Files/CMake/bin/cmake.exe"
"$CMAKE" --version >/dev/null 2>&1 || die 13 "binary-missing" ',"which":"cmake"' "cmake not found"
SPVDIS="spirv-dis"
command -v spirv-dis >/dev/null 2>&1 || SPVDIS="${VULKAN_SDK:-}/Bin/spirv-dis.exe"
"$SPVDIS" --version >/dev/null 2>&1 || die 13 "binary-missing" ',"which":"spirv-dis"' "spirv-dis not found (VULKAN_SDK=${VULKAN_SDK:-unset})"
command -v timeout >/dev/null 2>&1 || die 13 "binary-missing" ',"which":"timeout"' "coreutils timeout not on PATH"
[ -f "$ROOT/src/hdRT/CMakeLists.txt" ] || die 13 "binary-missing" ',"which":"src/hdRT/CMakeLists.txt"' "src/hdRT/CMakeLists.txt does not exist (the delegate has not been written)"
echo "houdini $HVER | $("$CMAKE" --version | head -n1) | seed31 $SEED31" | tee -a "$BUILD_LOG"

# Hermetic Houdini environment for every hython and husk call below.
export HOUDINI_USER_PREF_DIR="$(mpath "$OUT")/prefs__HVER__"
export HOUDINI_PATH='&'
export HOUDINI_NO_ENV_FILE=1
unset PYTHONHOME PYTHONPATH HOUDINI_PACKAGE_DIR HOUDINI_DSO_PATH PXR_PLUGINPATH_NAME
export PXR_PLUGINPATH_NAME="$(mpath "$STAGE_DIR")"
export HOUDINIRT_S3_CONFIG="$(mpath "$CFG")"
export HOUDINIRT_VALIDATION=1

echo "== [1] build and stage the plugin (fresh staging directory) ==" | tee -a "$BUILD_LOG"
rm -rf "$STAGE_DIR" "$CMK"
mkdir -p "$STAGE_DIR"
"$CMAKE" -S "$(mpath "$ROOT/src/hdRT")" -B "$(mpath "$CMK")" -G "Visual Studio 17 2022" -A x64 \
  "-DCMAKE_PREFIX_PATH=$(mpath "$HFS_DIR")/toolkit/cmake" \
  "-DHRT_REPO_ROOT=$(mpath "$ROOT")" "-DOPENPBR=${OPENPBR:-}" >>"$BUILD_LOG" 2>&1 \
  || die 11 "cmake-configure" '' "cmake configure failed (see verify/build.log)"
"$CMAKE" --build "$(mpath "$CMK")" --config Release >>"$BUILD_LOG" 2>&1 \
  || die 12 "build-link" '' "build or link failed (see verify/build.log)"
"$CMAKE" --install "$(mpath "$CMK")" --config Release --prefix "$(mpath "$STAGE_DIR")" >>"$BUILD_LOG" 2>&1 \
  || die 13 "binary-missing" ',"which":"cmake --install"' "cmake --install failed (the project needs install rules; see verify/build.log)"
[ -f "$STAGE_DIR/plugInfo.json" ] || die 13 "binary-missing" ',"which":"plugInfo.json"' "build/plugin_stage/plugInfo.json was not installed"
DLLS=$(find "$STAGE_DIR" -type f -iname '*.dll' | wc -l)
[ "$DLLS" -ge 1 ] || die 13 "binary-missing" ',"which":"plugin dll"' "no .dll was installed under build/plugin_stage"

echo "== [2] source and kernel invariants ==" | tee -a "$BUILD_LOG"
SRC="$ROOT/src/hdRT"
grep -rqs "HdRenderThread" "$SRC" || die 17 "invariant-violation" ',"which":"HdRenderThread"' "src/hdRT never uses HdRenderThread"
grep -rqsE "IsPauseSupported[[:space:]]*\(" "$SRC" || die 17 "invariant-violation" ',"which":"IsPauseSupported"' "the delegate does not override IsPauseSupported"
grep -rqsE "(^|[^A-Za-z_])Pause[[:space:]]*\(" "$SRC" || die 17 "invariant-violation" ',"which":"Pause"' "the delegate does not override Pause"
grep -rqsE "(^|[^A-Za-z_])Resume[[:space:]]*\(" "$SRC" || die 17 "invariant-violation" ',"which":"Resume"' "the delegate does not override Resume"
if grep -rqsi "gatling" "$SRC"; then die 17 "invariant-violation" ',"which":"gpl reference"' "src/hdRT mentions gatling (GPL code is off limits)"; fi
RQ=0
while IFS= read -r spv; do
  if "$SPVDIS" "$(mpath "$spv")" 2>/dev/null | grep -q "OpRayQueryInitializeKHR"; then RQ=$((RQ + 1)); fi
done < <(find "$STAGE_DIR" -type f -iname '*.spv')
[ "$RQ" -ge 1 ] || die 17 "invariant-violation" ',"which":"ray-query SPIR-V"' "no .spv under build/plugin_stage contains ray-query instructions"
echo "invariants ok ($RQ ray-query shader(s) staged)" | tee -a "$BUILD_LOG"

echo "== [3] hermetic registration and provenance ==" | tee -a "$RUN_LOG"
PLUGIN_ID="$("$HYTHON" - "$(mpath "$STAGE_DIR")" 2>>"$RUN_LOG" <<'PY' | tr -d '\r' | tail -n1
import os, sys
from pxr import Plug
stage = os.path.normcase(os.path.realpath(sys.argv[1]))
inside, outside = [], []
for pl in Plug.Registry().GetAllPlugins():
    for name, d in pl.metadata.get("Types", {}).items():
        if "HdRendererPlugin" not in d.get("bases", []):
            continue
        if d.get("displayName") != "HoudiniRT":
            continue
        p = os.path.normcase(os.path.realpath(pl.path))
        (inside if p.startswith(stage + os.sep) else outside).append((name, p))
sys.stderr.write("probe: inside=%r outside=%r\n" % (inside, outside))
if outside:
    print("ERR:ambient"); sys.exit(0)
if len(inside) != 1:
    print("ERR:count=%d" % len(inside)); sys.exit(0)
name, path = inside[0]
if not os.path.isfile(path):
    print("ERR:library-missing"); sys.exit(0)
from pxr import UsdImagingGL
if name not in [str(x) for x in UsdImagingGL.Engine.GetRendererPlugins()]:
    print("ERR:not-in-renderer-list"); sys.exit(0)
print("OK:" + name)
PY
)"
case "$PLUGIN_ID" in
  OK:*) PLUGIN_ID="${PLUGIN_ID#OK:}" ;;
  ERR:ambient) die 15 "runtime-nonzero" ',"which":"ambient HoudiniRT plugin"' "a HoudiniRT plugin was discovered OUTSIDE build/plugin_stage; provenance cannot be asserted" ;;
  *) die 15 "runtime-nonzero" ",\"which\":\"registration\",\"probe\":\"$PLUGIN_ID\"" "the plugin did not register as exactly one HdRendererPlugin named HoudiniRT from build/plugin_stage (probe: ${PLUGIN_ID:-no output}; see verify/run.log)" ;;
esac
echo "registered: $PLUGIN_ID" | tee -a "$RUN_LOG"

echo "== [4] derive the test from the nonce and write the stage (exam-side) ==" | tee -a "$RUN_LOG"
"$HYTHON" - "$SEED" "$SEED31" "$SPP" "$(mpath "$CFG")" "$(mpath "$USDA")" >>"$RUN_LOG" 2>&1 <<'PY' || die 13 "binary-missing" ',"which":"stage generator"' "the exam could not write its own stage (exam bug; see verify/run.log)"
import json, random, sys
from pxr import Usd, UsdGeom, UsdRender, Sdf, Gf
nonce, seed31, spp, cfg_path, usda_path = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5]
rng = random.Random(nonce)
rho = round(rng.uniform(0.30, 0.70), 6)
emission = [round(rng.uniform(0.40, 1.60), 6) for _ in range(3)]
bounces = rng.choice([8, 16, 32])
with open(cfg_path, "w") as f:
    json.dump({"seed": seed31, "spp": spp, "rho": rho, "emission": emission, "bounces": bounces}, f)
st = Usd.Stage.CreateNew(usda_path)
UsdGeom.SetStageUpAxis(st, UsdGeom.Tokens.y)
cam = UsdGeom.Camera.Define(st, "/cam")
cam.AddTranslateOp().Set(Gf.Vec3d(0.5, 0.5, 0.5))
rs = UsdRender.Settings.Define(st, "/Render/settings")
rs.CreateResolutionAttr(Gf.Vec2i(256, 256))
rs.CreateCameraRel().SetTargets(["/cam"])
p = rs.GetPrim()
p.CreateAttribute("houdinirt:seed", Sdf.ValueTypeNames.Int).Set(seed31)
p.CreateAttribute("houdinirt:spp", Sdf.ValueTypeNames.Int).Set(spp)
p.CreateAttribute("houdinirt:furnace:rho", Sdf.ValueTypeNames.Float).Set(rho)
p.CreateAttribute("houdinirt:furnace:emission", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*emission))
p.CreateAttribute("houdinirt:furnace:bounces", Sdf.ValueTypeNames.Int).Set(bounces)
st.SetMetadata("renderSettingsPrimPath", "/Render/settings")
st.GetRootLayer().Save()
print("exam test: rho=%s emission=%s bounces=%s spp=%s seed=%s" % (rho, emission, bounces, spp, seed31))
PY
[ -s "$CFG" ] && [ -s "$USDA" ] || die 13 "binary-missing" ',"which":"stage generator output"' "the exam's stage or config was not written (exam bug)"

echo "== [5] headless render through husk (validation on) ==" | tee -a "$RUN_LOG"
T0=$(date +%s)
timeout -k 10 600 "$HUSK" -R "$PLUGIN_ID" -r 256 256 -V2 -o "$(mpath "$EXR")" "$(mpath "$USDA")" >>"$RUN_LOG" 2>&1
HUSK_RC=$?
T1=$(date +%s)
if grep -Eq "VUID|Validation Error|VK_ERROR" "$RUN_LOG"; then
  die 14 "validation-layer" ",\"vuid_count\":$(grep -cE 'VUID' "$RUN_LOG" || echo 0)" "the validation layer spoke (see verify/run.log)"
fi
[ "$HUSK_RC" -ne 124 ] || die 15 "runtime-nonzero" ',"which":"husk timeout"' "husk did not finish within 600 s (a render that never converges or a blocked Execute)"
[ "$HUSK_RC" -eq 0 ] || die 15 "runtime-nonzero" ",\"exit\":$HUSK_RC" "husk exited $HUSK_RC (see verify/run.log)"
[ -s "$EXR" ] || die 15 "runtime-nonzero" ',"which":"no EXR"' "husk exited 0 but wrote no image"

echo "== [6] delegate log: validation, seed, device, render thread ==" | tee -a "$RUN_LOG"
grep -q "HoudiniRT: validation layer active" "$RUN_LOG" \
  || die 14 "validation-layer" ',"which":"layer not proven active"' "the delegate never printed 'HoudiniRT: validation layer active'; a silent log proves nothing"
INFO_LINE="$(grep -m1 "HoudiniRT: seed=" "$RUN_LOG" | tr -d '\r' || true)"
[ -n "$INFO_LINE" ] || die 15 "runtime-nonzero" ',"which":"no seed line"' "the delegate printed no 'HoudiniRT: seed=... spp=... device=...' line"
echo "$INFO_LINE" | grep -q "seed=$SEED31 spp=$SPP device=" \
  || die 15 "runtime-nonzero" ',"which":"seed not echoed"' "the delegate did not echo the injected seed and spp: $INFO_LINE"
DEVICE="$(printf "%s" "${INFO_LINE#*device=}" | sed "s/[[:space:]]*$//")"
[ -n "$DEVICE" ] || die 15 "runtime-nonzero" ',"which":"empty device"' "the delegate named no Vulkan device"
if echo "$DEVICE" | grep -qiE 'llvmpipe|lavapipe|swiftshader|software|basic render'; then
  die 15 "runtime-nonzero" ',"which":"software device"' "rendered on a software device, not the GPU: $DEVICE"
fi
if command -v nvidia-smi >/dev/null 2>&1; then
  GPU_NAME="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -n1 | tr -d '\r')"
  if [ -n "$GPU_NAME" ] && ! echo "$DEVICE" | grep -qiF "$GPU_NAME"; then
    die 15 "runtime-nonzero" ',"which":"wrong device"' "the delegate ran on '$DEVICE', expected the discrete GPU '$GPU_NAME'"
  fi
fi
L_START="$(grep -n -m1 "HoudiniRT: render thread start" "$RUN_LOG" | cut -d: -f1)"
L_STOP="$(grep -n "HoudiniRT: render thread stop" "$RUN_LOG" | tail -n1 | cut -d: -f1)"
[ -n "$L_START" ] && [ -n "$L_STOP" ] && [ "$L_START" -lt "$L_STOP" ] \
  || die 17 "invariant-violation" ',"which":"render thread markers"' "render thread start/stop markers are missing or out of order"

echo "== [7] independent judge (analytic reference computed here, under hython) ==" | tee -a "$JUDGE_LOG"
"$HYTHON" - "$(mpath "$EXR")" "$(mpath "$CFG")" "$PLUGIN_ID" "$T0" >>"$JUDGE_LOG" 2>&1 <<'PY'
import json, math, os, sys
import numpy as np
import OpenImageIO as oiio
exr, cfg_path, plugin_id, t0 = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
cfg = json.load(open(cfg_path))
def fail(msg):
    print("judge FAIL: " + msg); sys.exit(16)
if os.path.getmtime(exr) < t0 - 2:
    fail("the EXR is older than this run")
inp = oiio.ImageInput.open(exr)
if inp is None:
    fail("OpenImageIO cannot open the EXR: %s" % oiio.geterror())
spec = inp.spec()
print("judge: %dx%d channels=%s" % (spec.width, spec.height, list(spec.channelnames)))
if (spec.width, spec.height) != (256, 256):
    fail("resolution is %dx%d, expected 256x256" % (spec.width, spec.height))
if spec.nchannels < 3:
    fail("fewer than three channels")
stats_raw = spec.getattribute("husk:render_stats")
header_seed = "absent"
if stats_raw:
    stats = json.loads(stats_raw)
    if stats.get("__delegate") != plugin_id:
        fail("husk:render_stats names delegate %r, expected %r" % (stats.get("__delegate"), plugin_id))
    if "hrt_seed" in stats:
        if int(stats["hrt_seed"]) != int(cfg["seed"]):
            fail("header hrt_seed %r does not equal the seed %r" % (stats["hrt_seed"], cfg["seed"]))
        header_seed = "match"
else:
    fail("the EXR has no husk:render_stats attribute (was it written by husk?)")
if header_seed == "absent":
    print("judge WARN: hrt_seed is not in husk:render_stats; relying on the delegate log line")
px = np.asarray(inp.read_image("float"), dtype=np.float64).reshape(spec.height, spec.width, spec.nchannels)
inp.close()
rgb = px[:, :, :3]
if not np.isfinite(rgb).all():
    fail("the image contains NaN or Inf")
if (rgb < 0.0).any():
    fail("the image contains negative radiance")
if spec.nchannels >= 4 and "A" in spec.channelnames:
    a = px[:, :, list(spec.channelnames).index("A")]
    if abs(float(a.mean()) - 1.0) > 1e-3:
        fail("alpha mean is %.5f, expected 1 (a closed box covers every pixel)" % float(a.mean()))
rho, b = float(cfg["rho"]), int(cfg["bounces"])
k = (1.0 - rho ** (b + 1)) / (1.0 - rho)
worst = 0.0
for c in range(3):
    want = float(cfg["emission"][c]) * k
    got = float(rgb[:, :, c].mean())
    rel = abs(got - want) / want
    worst = max(worst, rel)
    print("judge: channel %d mean %.6f analytic %.6f rel err %.5f" % (c, got, want, rel))
    if rel > 5e-3:
        fail("channel %d mean is off by %.3f%% (tolerance 0.5%%)" % (c, 100.0 * rel))
    dev = np.abs(rgb[:, :, c] - want) / want
    p99 = float(np.percentile(dev, 99.0))
    if p99 > 0.25:
        fail("channel %d: 1%% of pixels deviate by more than 25%% (p99 %.3f)" % (c, p99))
print("judge: all analytic checks passed | worst_rel=%.6f header_seed=%s" % (worst, header_seed))
PY
JRC=$?
case "$JRC" in
  0) : ;;
  16) die 16 "hash-mismatch" ',"which":"judge"' "$(grep -m1 'judge FAIL' "$JUDGE_LOG" || echo 'judge found the image out of tolerance') (see verify/judge.log)" ;;
  *)  die 15 "runtime-nonzero" ",\"which\":\"judge exit $JRC\"" "the judge could not run (see verify/judge.log)" ;;
esac
grep -q "^judge: all analytic checks passed" "$JUDGE_LOG" || die 15 "runtime-nonzero" ',"which":"judge verdict missing"' "the judge exited 0 without a verdict"
HEADER_SEED="$(grep -oE 'header_seed=[a-z]+' "$JUDGE_LOG" | tail -n1 | cut -d= -f2)"

emit "all" ",\"plugin\":\"$PLUGIN_ID\",\"houdini\":\"$HVER\",\"render_seconds\":$((T1 - T0)),\"header_seed\":\"$HEADER_SEED\",\"pause_resume\":\"static-only\",\"device\":\"$(echo "$DEVICE" | sed 's/"/\\"/g' | cut -c1-120)\""
echo "PASS: s3 delegate $PLUGIN_ID on $DEVICE - built, staged, registered hermetically, rendered through husk, image matches the analytic furnace; validation silent; seed $SEED31; pause/resume checked statically only"
exit 0
