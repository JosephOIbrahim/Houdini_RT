#!/usr/bin/env bash
# verify/s4_firstlight.sh - exam for s4-first-light.
# Written by Claude on 2026-10-06 at Joe's request. It replaces the local model's draft of the same
# day, which stopped after stage [4] and so had no render, no judge and no verdict.
# Every Houdini and Karma fact below was probed on this workstation against Houdini 22.0.429.
#
# Called by the orchestrator as: s4_firstlight.sh <SEED>   (fresh unsigned 64-bit nonce per run)
# Contract: specs/build-spec.md, section s4-first-light.
#
# ---------------------------------------------------------------------------------------------
# THE CONTRACT (everything the delegate must do to pass; nothing else is assumed)
#
# BUILD AND REGISTRATION: exactly as in step 3.
#   cmake -S src/hdRT -B build/s4/cmake -G "Visual Studio 17 2022" -A x64
#         -DCMAKE_PREFIX_PATH=<HFS>/toolkit/cmake -DHRT_REPO_ROOT=<repo> -DOPENPBR=<openpbr or "">
#   Release build, cmake --install into build/plugin_stage. One HdRendererPlugin with displayName
#   "HoudiniRT", discovered from build/plugin_stage only. At least one staged .spv uses ray queries.
#
# THE SCENE. The exam writes it from the nonce; the delegate renders what Hydra hands it.
#   headless:  husk -R <plugin id> -o build/s4/out.exr build/s4/stage.usda      (384x216, from the stage)
#   - Two meshes (Hydra rprim type "mesh"):
#       /World/teapot/Geometry  the usd-wg Utah teapot, 1,236 quads, referenced from
#                               third_party/usd_wg_teapot/UtahTeapot.usd, under a transform with a
#                               translation, two rotations and a NON-UNIFORM scale;
#       /World/table            one polygon with 9 to 14 sides, under a rotation.
#     Both carry subdivisionScheme = "none": render the polygons as authored, do not subdivide.
#     Triangulate with HdMeshUtil::ComputeTriangleIndices (a fan: n - 2 triangles per n-sided face).
#     Neither mesh has authored normals: compute smooth vertex normals with
#     Hd_SmoothNormals::ComputeSmoothNormals and interpolate them. Karma does the same (probed: a
#     polygon mesh without normals renders like the same mesh with smooth normals authored).
#     Apply each mesh's world transform. Normals transform by the inverse transpose.
#     The teapot is four open shells (96 boundary edges): faces are seen from both sides. A face
#     seen from behind shades as if its normal pointed at the viewer, and blocks light like any
#     other. Karma does exactly that whatever doubleSided says (probed: a quad seen from behind
#     reads the same as one seen from the front, and casts the same shadow).
#   - Surfaces are Lambertian. albedo = the mesh's constant primvars:displayColor (RGB).
#     The exam also binds a MaterialX OpenPBR surface with that base_color and specular_weight 0,
#     which Karma renders as exactly Lambertian (probed: a sphere in a uniform dome reads
#     albedo * radiance to four digits from its centre to its rim). The delegate needs no material
#     support in this step. Albedo and light colour are used as the numbers they are: Karma applies
#     no colour-space conversion to either (probed).
#   - One UsdLuxDomeLight with no texture: the same radiance from every direction,
#         radiance = inputs:intensity * 2^inputs:exposure * inputs:color.
#     Hydra hands it over as an Sprim of type domeLight (HdLightTokens: intensity, exposure, color).
#     The dome is NOT visible to the camera: a camera ray that hits nothing is black, alpha 0.
#   - Paths: the first hit plus `houdinirt:bounces` further diffuse bounces (the nonce draws it from
#     6, 7, 9, 10, 11, 12; keep the delegate's own default outside that set). A path that leaves the
#     scene collects (its throughput) * (dome radiance). A path still inside the scene after its
#     last allowed bounce contributes nothing. No clamping, no denoiser.
#     (Karma's karma:object:diffuselimit counts the same way: probed at limits 0, 1, 2 and 8.)
#   - Camera: the UsdGeom.Camera named by the RenderSettings prim; perspective, no depth of field.
#     Take view and projection from Hydra as step 3 does.
#   - Pixels: a box filter exactly one pixel wide (jitter every sample uniformly inside its own
#     pixel). RGB is premultiplied by coverage; alpha is coverage.
#   - Settings, authored on /Render/settings and delivered as render settings:
#         houdinirt:seed (int)   houdinirt:spp (int)   houdinirt:bounces (int)
#     The step 3 furnace settings are no longer authored; the built-in box may go.
#   - The delegate prints the step 3 lines (stdout or stderr), in this order:
#         HoudiniRT: validation layer active
#         HoudiniRT: seed=<seed> spp=<spp> device=<Vulkan deviceName>
#         HoudiniRT: render thread start
#         HoudiniRT: render thread stop
#   - GetRenderStats() returns, besides hrt_seed, the bounce budget it rendered with and the
#     translation census of what was synced:
#         hrt_bounces         (int)  the houdinirt:bounces value the render used
#         hrt_mesh_count      (int)  meshes synced
#         hrt_triangle_count  (int)  triangles after triangulation, all meshes
#         hrt_bbox_min_x  hrt_bbox_min_y  hrt_bbox_min_z      world-space bounding box of every
#         hrt_bbox_max_x  hrt_bbox_max_y  hrt_bbox_max_z      triangle vertex, all meshes
#     husk copies stats into the EXR header (packed in husk:render_stats for a delegate with a
#     UsdRenderers.json entry, top-level attributes beside __delegate for any other).
#
# WHAT THE EXAM CHECKS
#   [2] in C++ code (not comments): pxr/imaging/hd/meshUtil.h and smoothNormals.h are included,
#       HdMeshUtil::ComputeTriangleIndices and Hd_SmoothNormals::ComputeSmoothNormals are called.
#   [7] the delegate's own image: fresh, 384x216 RGBA, names this delegate; hrt_bounces equals the
#       drawn bounce count; the census in its
#       header equals a census taken here from the stage with pxr.Usd; the image is finite and
#       premultiplied; a second render with the dome twice as bright has twice the mean (0.5%).
#   [9] against Karma CPU rendering the same stage (path traced, no denoiser, one-pixel box):
#       mean of the fully covered pixels within 1.0% per channel, and FLIP, 99th percentile,
#       at most FLIP_MAX. Both images are composited over the dome's own radiance, then
#       log-encoded (12 stops) before FLIP; the percentile runs over the pixels either image covers.
#       Mean |alpha difference| over the frame at most DA_MAX (silhouettes and pixel filter).
#
# HONEST LIMITS OF THIS EXAM (each one measured; the table is in the notes)
#   - The picture cannot tell smooth normals from flat ones. Under a uniform dome a Lambertian
#     surface is as bright whichever way its shading normal leans, and a flat-shaded render scored
#     the same as a correct one. HdMeshUtil and Hd_SmoothNormals are therefore enforced by reading
#     the source, as the spec says, until a later step brings a light that has a direction.
#   - Two wrong renders are too close to right for the picture to catch every time: 2 bounces
#     instead of the drawn count (0.3 to 0.7% darker), and a teapot subdivided instead of rendered as its polygon cage
#     (the census catches that one: the triangle count changes). A pixel filter half a pixel wide
#     is caught by the alpha gate. These all fail: 1 bounce, direct light only, a camera half a
#     pixel off, a missing mesh, an ignored transform, a light or an albedo 2% off.
#   - hrt_bounces proves the setting was read, not that it was honoured, and a delegate that
#     reports a constant from the drawn set matches one nonce in six; the picture cannot separate
#     6 from 12 bounces. Bounce counting is proved outside the exam (Karma probe D limits).
#   - Stage [2] reads source: code inside a preprocessor branch other than `#if 0` still counts.
#   - A light or albedo error that is the same everywhere is caught by the level check, not by FLIP.
#   - Live Pause/Resume is still not exercised (same reason as step 3).
#   - RETINA T0/T1 (Synapse) is not used; the judge reads the header with OpenImageIO and computes
#     FLIP itself. That is a deviation from the spec line and is listed in the notes.
#   - The reference is one renderer, Karma CPU. Its agreement with Karma XPU and with a third,
#     independent path tracer was measured when this exam was written (see the notes).
#
# What a cheat would still have to do: build and install a real Hydra plugin, be discovered from
# the staging directory only, be driven by husk, sync two meshes it did not author through a
# transform it did not choose, report a census that matches an independent one, render linearly,
# and match Karma's picture of a scene whose pose, camera, albedos and light come from the nonce.
#
# Exit codes mirror orchestrator/verify.ts:
#   11 cmake configure  12 build/link  13 toolchain, asset or reference renderer missing
#   14 validation layer  15 runtime / registration / wrong device  16 mismatch  17 invariant
# ---------------------------------------------------------------------------------------------

set -uo pipefail
SEED="${1:-$RANDOM$RANDOM}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/build/s4"
CMK="$OUT/cmake"
STAGE_DIR="$ROOT/build/plugin_stage"
BUILD_LOG="$ROOT/verify/build.log"
RUN_LOG="$ROOT/verify/run.log"
KARMA_LOG="$ROOT/verify/karma.log"
JUDGE_LOG="$ROOT/verify/judge.log"
EVIDENCE="$ROOT/verify/evidence.json"
TRUTH="$OUT/truth.json"
USDA="$OUT/stage.usda"
USDA2X="$OUT/stage_2x.usda"
EXR="$OUT/out.exr"
EXR2X="$OUT/out_2x.exr"
KEXR="$OUT/karma.exr"
TEAPOT="$ROOT/third_party/usd_wg_teapot/UtahTeapot.usd"
TEAPOT_SHA="7f64b77cd959e8c2b6760bb63ef40a880b6d5daa0f501251294069839ec9f4d4"
PIN="22.0.429"
HFS_DIR="${HRT_HFS:-C:/Program Files/Side Effects Software/Houdini $PIN}"
HYTHON="$HFS_DIR/bin/hython.exe"
HUSK="$HFS_DIR/bin/husk.exe"

# The numbers this exam was calibrated with (see the notes). Change one and the rest need re-measuring.
WIDTH=384; HEIGHT=216
SPP=2048                # the delegate's samples per pixel
KARMA_SPP=2048          # Karma's path traced samples
FLIP_MAX="0.045"        # FLIP 99th percentile ceiling. Calibrated 2026-11-11 on the real teapot, 6 nonces: correct
                        # renders (Karma XPU, Karma CPU other seed) 0.0128-0.0241. Revisit at first light
# Time limits (X2a), from renders measured 2026-11-11: Karma CPU reference 4-11 s; the step 3 delegate
# rendered in 3 s. Build 600 s + 2 x 300 s delegate + 120 s Karma = 1,320 s, inside the 1,800 s milestone limit.
LEVEL_TOL="0.010"       # mean of fully covered pixels, per channel, relative
DA_MAX="0.0004"         # mean |alpha difference| against Karma over the frame. Correct renders 0.0000-0.0001;
                        # a pixel filter half a pixel wide 0.0008-0.0009 (FLIP alone lets that through).
                        # Calibrated 2026-11-11 on the real teapot, 6 nonces: correct 0.00003-0.00007, half-pixel
                        # filter 0.00074-0.00089. Karma vs Karma only (and a tracer on the stand-in): revisit at first light
EXPOSURE_TOL="0.005"    # 2x dome -> 2x mean
BBOX_TOL="1e-4"         # census bounding box, as a fraction of its diagonal

mkdir -p "$OUT"
: > "$BUILD_LOG"; : > "$RUN_LOG"; : > "$KARMA_LOG"; : > "$JUDGE_LOG"
rm -f "$EVIDENCE" "$TRUTH" "$USDA" "$USDA2X" "$EXR" "$EXR2X" "$KEXR" "$OUT/flip_error.exr"

emit() { printf '{"stage":"%s","seed":"%s"%s}\n' "$1" "$SEED" "${2:-}" > "$EVIDENCE"; }
mpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
die() { # die <exit> <stage> <extra-json> <message>
  emit "$2" "$3"; echo "FAIL: $4"; exit "$1"
}
case "$SEED" in ''|*[!0-9]*) die 13 "binary-missing" ',"which":"nonce"' "the nonce '$SEED' is not a decimal number" ;; esac

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
command -v sha256sum >/dev/null 2>&1 || die 13 "binary-missing" ',"which":"sha256sum"' "coreutils sha256sum not on PATH"
command -v perl >/dev/null 2>&1 || die 13 "binary-missing" ',"which":"perl"' "perl not on PATH (Git for Windows ships it; stage [2] strips C++ comments with it)"
[ -f "$ROOT/src/hdRT/CMakeLists.txt" ] || die 13 "binary-missing" ',"which":"src/hdRT/CMakeLists.txt"' "src/hdRT/CMakeLists.txt does not exist"
[ -f "$TEAPOT" ] || die 13 "binary-missing" ',"which":"teapot asset"' "third_party/usd_wg_teapot/UtahTeapot.usd is missing (it is part of the repo; see its README)"
GOT_SHA="$(sha256sum "$TEAPOT" | cut -d' ' -f1)"
[ "$GOT_SHA" = "$TEAPOT_SHA" ] || die 13 "binary-missing" ',"which":"teapot asset changed"' "third_party/usd_wg_teapot/UtahTeapot.usd is not the file this exam was written against (sha256 $GOT_SHA)"
echo "houdini $HVER | $("$CMAKE" --version | head -n1) | teapot asset ok" | tee -a "$BUILD_LOG"

# Hermetic Houdini environment for every hython and husk call below.
export HOUDINI_USER_PREF_DIR="$(mpath "$OUT")/prefs__HVER__"
export HOUDINI_PATH='&'
export HOUDINI_NO_ENV_FILE=1
unset PYTHONHOME PYTHONPATH HOUDINI_PACKAGE_DIR HOUDINI_DSO_PATH PXR_PLUGINPATH_NAME OCIO
# the delegate's own switches: nothing from the caller's shell may steer the run under test
# (HOUDINIRT_S3_CONFIG, _ALLOW_SOFTWARE_DEVICE, _DEBUG, _DEBUG_OPEN_FACE and any added later);
# the exam sets back only what it owns
unset HOUDINIRT_S3_CONFIG HOUDINIRT_ALLOW_SOFTWARE_DEVICE HOUDINIRT_DEBUG
while IFS= read -r v; do unset "$v"; done < <(compgen -e | grep '^HOUDINIRT_' || true)
export PXR_PLUGINPATH_NAME="$(mpath "$STAGE_DIR")"
export HOUDINIRT_VALIDATION=1

echo "== [1] build and stage the plugin (fresh staging directory) ==" | tee -a "$BUILD_LOG"
rm -rf "$STAGE_DIR" "$CMK"
mkdir -p "$STAGE_DIR"
"$CMAKE" -S "$(mpath "$ROOT/src/hdRT")" -B "$(mpath "$CMK")" -G "Visual Studio 17 2022" -A x64 \
  "-DCMAKE_PREFIX_PATH=$(mpath "$HFS_DIR")/toolkit/cmake" \
  "-DHRT_REPO_ROOT=$(mpath "$ROOT")" "-DOPENPBR=${OPENPBR:-}" >>"$BUILD_LOG" 2>&1 \
  || die 11 "cmake-configure" '' "cmake configure failed (see verify/build.log)"
timeout -k 10 600 "$CMAKE" --build "$(mpath "$CMK")" --config Release >>"$BUILD_LOG" 2>&1
BUILD_RC=$?
[ "$BUILD_RC" -ne 124 ] || die 12 "build-link" ',"which":"build timeout"' "the build did not finish within 600 s (see verify/build.log)"
[ "$BUILD_RC" -eq 0 ] || die 12 "build-link" '' "build or link failed (see verify/build.log)"
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
# Mesh sync is enforced on code, not prose: C++ sources only, comment lines and trailing // comments
# removed, and the HDK headers must be included. (The picture cannot tell smooth normals from flat
# ones under a uniform dome, so this read of the source is the only smooth-normal check in step 4.)
codegrep() { # codegrep <ERE>: true if the pattern appears in C++ code under src/hdRT, outside comments
  # Each source file is read whole; /* */ blocks, // comments and #if 0 ... #endif blocks are removed
  # before matching. (No grep -q at the end of the pipe: under pipefail an early exit reads as a miss.)
  find "$SRC" -type f \( -iname '*.cpp' -o -iname '*.cc' -o -iname '*.cxx' -o -iname '*.h' -o -iname '*.hh' \
       -o -iname '*.hpp' -o -iname '*.hxx' -o -iname '*.inl' -o -iname '*.ipp' \) -print0 \
    | xargs -0 -r perl -0777 -ne 's{/\*.*?\*/}{ }gs; s{//[^\n]*}{}g; s{^[ \t]*#[ \t]*if[ \t]+0\b.*?^[ \t]*#[ \t]*endif\b}{}gms; print "$_\n"' \
    | tr -d '\r' | grep -E -- "$1" >/dev/null
}
codegrep '^[[:space:]]*#[[:space:]]*include[[:space:]]*[<"]pxr/imaging/hd/meshUtil\.h[>"]' \
  && codegrep "HdMeshUtil" && codegrep "ComputeTriangleIndices[[:space:]]*\(" \
  || die 17 "invariant-violation" ',"which":"HdMeshUtil"' "src/hdRT does not triangulate through HdMeshUtil::ComputeTriangleIndices in code (it must include pxr/imaging/hd/meshUtil.h and call it; comments do not count)"
codegrep '^[[:space:]]*#[[:space:]]*include[[:space:]]*[<"]pxr/imaging/hd/smoothNormals\.h[>"]' \
  && codegrep "Hd_SmoothNormals" && codegrep "ComputeSmoothNormals[[:space:]]*\(" \
  || die 17 "invariant-violation" ',"which":"Hd_SmoothNormals"' "src/hdRT does not compute normals through Hd_SmoothNormals::ComputeSmoothNormals in code (it must include pxr/imaging/hd/smoothNormals.h and call it; comments do not count)"
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

echo "== [4] derive the scene from the nonce and write the stages (exam-side) ==" | tee -a "$RUN_LOG"
# The nonce goes to Python as text. It is an unsigned 64-bit number and bash arithmetic is signed:
# the step 3 exam reduced it in bash and turned half of all nonces into seed 1.
"$HYTHON" - "$SEED" "$(mpath "$TEAPOT")" "$(mpath "$USDA")" "$(mpath "$USDA2X")" "$(mpath "$TRUTH")" "$WIDTH" "$HEIGHT" "$SPP" "$KARMA_SPP" >>"$RUN_LOG" 2>&1 <<'PY' || die 13 "binary-missing" ',"which":"stage generator"' "the exam could not write its own stage (exam bug; see verify/run.log)"
import json, math, random, sys
from pxr import Usd, UsdGeom, UsdLux, UsdRender, UsdShade, Sdf, Gf

nonce = int(sys.argv[1]); teapot_asset = sys.argv[2]
stage_path, stage2x_path, truth_path = sys.argv[3], sys.argv[4], sys.argv[5]
W, H, SPP, KARMA_SPP = int(sys.argv[6]), int(sys.argv[7]), int(sys.argv[8]), int(sys.argv[9])

seed31 = nonce % 2147483647
if seed31 < 2:
    seed31 = 2                       # 1 is the delegate's default seed: never inject a default
rng = random.Random(nonce)

# --- what the nonce decides ------------------------------------------------------------------
yaw = rng.uniform(0.0, 360.0)                                  # teapot pose
tilt = rng.uniform(-12.0, 12.0)
scale = (rng.uniform(0.85, 1.20), rng.uniform(0.85, 1.20), rng.uniform(0.85, 1.20))   # not uniform, on purpose
lift = rng.uniform(0.05, 0.10)                                 # the teapot floats this far above the table
teapot_albedo = [round(rng.uniform(0.30, 0.65), 4) for _ in range(3)]
table_albedo = [round(rng.uniform(0.25, 0.55), 4) for _ in range(3)]
table_radius = rng.uniform(0.38, 0.55)
table_sides = rng.choice([9, 11, 12, 14])                      # one polygon: a fan gives sides - 2 triangles
table_spin = rng.uniform(0.0, 360.0)
azim = math.radians(rng.uniform(0.0, 360.0))                   # camera on an orbit above the table
elev = math.radians(rng.uniform(12.0, 50.0))
focal = rng.uniform(38.0, 60.0)
fit = rng.uniform(1.00, 1.30)                                  # how loosely the teapot sits in the frame
aim = (rng.uniform(-0.03, 0.03), rng.uniform(0.07, 0.15), rng.uniform(-0.03, 0.03))
intensity = round(rng.uniform(0.8, 1.6), 4)                    # dome radiance = intensity * 2^exposure * color
exposure = round(rng.uniform(-0.75, 0.75), 4)
tint = [round(rng.uniform(0.55, 1.45), 4) for _ in range(3)]
# Drawn last, so every draw above is unchanged: pose, camera, albedos and light of a nonce are what they
# were before this line existed. The bounce count, and so the images, now vary with the nonce.
# 8 is left out: it was this exam's fixed value before, so it is the likeliest hardcoded count.
BOUNCES = rng.choice([6, 7, 9, 10, 11, 12])

HAP = 36.0
half_v = math.atan(0.5 * HAP * H / W / focal)
dist = fit * 0.40 / math.sin(half_v)                           # 0.40 bounds the scaled teapot's half diagonal
eye = (aim[0] + dist * math.cos(elev) * math.cos(azim), aim[1] + dist * math.sin(elev), aim[2] + dist * math.cos(elev) * math.sin(azim))


def lambert(stage, path, albedo):
    """MaterialX OpenPBR with every lobe but the diffuse base switched off: exactly Lambertian in Karma (probed)."""
    mat = UsdShade.Material.Define(stage, path)
    sh = UsdShade.Shader.Define(stage, path + "/surface")
    sh.CreateIdAttr("ND_open_pbr_surface_surfaceshader")
    sh.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*albedo))
    for name, value in (("base_weight", 1.0), ("base_diffuse_roughness", 0.0), ("specular_weight", 0.0), ("coat_weight", 0.0), ("fuzz_weight", 0.0)):
        sh.CreateInput(name, Sdf.ValueTypeNames.Float).Set(value)
    mat.CreateSurfaceOutput("mtlx").ConnectToSource(sh.CreateOutput("out", Sdf.ValueTypeNames.Token))
    return mat


def build(path, dome_intensity):
    st = Usd.Stage.CreateNew(path)
    UsdGeom.SetStageUpAxis(st, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(st, 1.0)
    UsdGeom.Xform.Define(st, "/World")
    # the usd-wg Utah teapot, by reference; posed by the nonce; rendered as the polygon cage it is authored as
    tp = UsdGeom.Xform.Define(st, "/World/teapot")
    tp.GetPrim().GetReferences().AddReference(teapot_asset, "/UtahTeapot")
    tp.AddTranslateOp().Set(Gf.Vec3d(0.0, lift, 0.0))
    tp.AddRotateYOp().Set(yaw)
    tp.AddRotateXOp().Set(tilt)
    tp.AddScaleOp().Set(Gf.Vec3f(*scale))
    geo = UsdGeom.Mesh(st.OverridePrim("/World/teapot/Geometry"))
    geo.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)       # the asset says catmullClark; Karma would subdivide it
    geo.CreateDisplayColorPrimvar(UsdGeom.Tokens.constant).Set([Gf.Vec3f(*teapot_albedo)])
    UsdShade.MaterialBindingAPI.Apply(geo.GetPrim()).Bind(lambert(st, "/World/mtl/teapot", teapot_albedo))
    # the table: a single polygon with many sides, facing up
    table = UsdGeom.Mesh.Define(st, "/World/table")
    ring = [Gf.Vec3f(table_radius * math.cos(2.0 * math.pi * k / table_sides), 0.0, -table_radius * math.sin(2.0 * math.pi * k / table_sides)) for k in range(table_sides)]
    table.CreatePointsAttr(ring)
    table.CreateFaceVertexCountsAttr([table_sides])
    table.CreateFaceVertexIndicesAttr(list(range(table_sides)))
    table.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    table.CreateExtentAttr([Gf.Vec3f(-table_radius, 0.0, -table_radius), Gf.Vec3f(table_radius, 0.0, table_radius)])
    table.CreateDisplayColorPrimvar(UsdGeom.Tokens.constant).Set([Gf.Vec3f(*table_albedo)])
    table.AddRotateYOp().Set(table_spin)
    UsdShade.MaterialBindingAPI.Apply(table.GetPrim()).Bind(lambert(st, "/World/mtl/table", table_albedo))
    # camera
    cam = UsdGeom.Camera.Define(st, "/World/cam")
    cam.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(Gf.Vec3d(*eye), Gf.Vec3d(*aim), Gf.Vec3d(0.0, 1.0, 0.0)).GetInverse())
    cam.CreateFocalLengthAttr(focal)
    cam.CreateHorizontalApertureAttr(HAP)
    cam.CreateVerticalApertureAttr(HAP * H / W)
    cam.CreateClippingRangeAttr(Gf.Vec2f(0.01, 100.0))
    # one uniform dome
    dome = UsdLux.DomeLight.Define(st, "/World/dome")
    dome.CreateIntensityAttr(dome_intensity)
    dome.CreateExposureAttr(exposure)
    dome.CreateColorAttr(Gf.Vec3f(*tint))
    # render settings: the delegate's own, and Karma's for the reference render of the same stage
    rs = UsdRender.Settings.Define(st, "/Render/settings")
    rs.CreateResolutionAttr(Gf.Vec2i(W, H))
    rs.CreateCameraRel().SetTargets(["/World/cam"])
    p = rs.GetPrim()
    for name, tn, val in (
        ("houdinirt:seed", Sdf.ValueTypeNames.Int, seed31),
        ("houdinirt:spp", Sdf.ValueTypeNames.Int, SPP),
        ("houdinirt:bounces", Sdf.ValueTypeNames.Int, BOUNCES),
        ("karma:global:convergence_mode", Sdf.ValueTypeNames.Token, "Path Traced"),
        ("karma:global:pathtracedsamples", Sdf.ValueTypeNames.Int, KARMA_SPP),
        ("karma:global:pixelfilter", Sdf.ValueTypeNames.String, "box"),
        ("karma:global:pixelfiltersize", Sdf.ValueTypeNames.Float, 2.0),      # Karma's unit: 2.0 is a box one pixel wide (probed)
        ("karma:global:imagefilter", Sdf.ValueTypeNames.String, "[]"),        # no denoiser
        ("karma:object:diffuselimit", Sdf.ValueTypeNames.Float, float(BOUNCES)),
        ("karma:global:colorlimit", Sdf.ValueTypeNames.Float, 1.0e6),         # no clamping
        ("karma:global:colorlimitindirect", Sdf.ValueTypeNames.Float, 1.0e6),
    ):
        p.CreateAttribute(name, tn).Set(val)
    st.SetMetadata("renderSettingsPrimPath", "/Render/settings")
    st.GetRootLayer().Save()
    return st


st = build(stage_path, intensity)
build(stage2x_path, 2.0 * intensity)

# --- the census, from the composed stage and nothing else -----------------------------------
xc = UsdGeom.XformCache(Usd.TimeCode.Default())
meshes = tris = 0
lo = [math.inf] * 3; hi = [-math.inf] * 3
for prim in st.Traverse():
    if not prim.IsA(UsdGeom.Mesh):
        continue
    m = UsdGeom.Mesh(prim)
    meshes += 1
    counts = m.GetFaceVertexCountsAttr().Get(); indices = m.GetFaceVertexIndicesAttr().Get(); points = m.GetPointsAttr().Get()
    tris += sum(c - 2 for c in counts if c >= 3)               # a fan: n - 2 triangles per n-sided face
    used = set(); at = 0
    for c in counts:
        if c >= 3:
            used.update(indices[at:at + c])
        at += c
    M = xc.GetLocalToWorldTransform(prim)
    for i in sorted(used):                                     # every point a triangle uses, and no other
        q = points[i]
        w = M.Transform(Gf.Vec3d(q[0], q[1], q[2]))            # its exact world position
        for k in range(3):
            lo[k] = min(lo[k], w[k]); hi[k] = max(hi[k], w[k])
radiance = [intensity * (2.0 ** exposure) * c for c in tint]
truth = {"nonce": str(nonce), "seed": seed31, "spp": SPP, "bounces": BOUNCES, "width": W, "height": H,
         "meshes": meshes, "triangles": tris, "bbox_min": lo, "bbox_max": hi, "dome_radiance": radiance,
         "teapot_albedo": teapot_albedo, "table_albedo": table_albedo}
with open(truth_path, "w") as f:
    json.dump(truth, f)
print("exam scene: meshes=%d triangles=%d bbox=[%s .. %s]" % (meshes, tris, ["%.5f" % v for v in lo], ["%.5f" % v for v in hi]))
print("exam scene: seed=%d spp=%d bounces=%d %dx%d dome radiance=%s teapot yaw=%.1f tilt=%.1f scale=%s" % (seed31, SPP, BOUNCES, W, H, ["%.4f" % v for v in radiance], yaw, tilt, ["%.3f" % v for v in scale]))
PY
[ -s "$TRUTH" ] && [ -s "$USDA" ] && [ -s "$USDA2X" ] || die 13 "binary-missing" ',"which":"stage generator output"' "the exam's stages or census were not written (exam bug)"
SEED31="$(tr -d '\r' < "$TRUTH" | sed -n 's/.*"seed": \([0-9][0-9]*\).*/\1/p')"
[ -n "$SEED31" ] || die 13 "binary-missing" ',"which":"seed"' "the exam could not read the seed back from its own census (exam bug)"
grep "^exam scene:" "$RUN_LOG" | tr -d '\r'

echo "== [5] headless renders through husk: the scene, then the scene with the dome twice as bright ==" | tee -a "$RUN_LOG"
T0=$(date +%s)
timeout -k 10 300 "$HUSK" -R "$PLUGIN_ID" -V2 -o "$(mpath "$EXR")" "$(mpath "$USDA")" >>"$RUN_LOG" 2>&1
HUSK_RC=$?
T1=$(date +%s)
FIRST_BYTES=$(wc -c < "$RUN_LOG")      # the first render's log ends here; stage [6] judges thread order inside it
if grep -Eq "VUID|Validation Error|VK_ERROR" "$RUN_LOG"; then
  die 14 "validation-layer" ",\"vuid_count\":$(grep -cE 'VUID' "$RUN_LOG" || true)" "the validation layer spoke (see verify/run.log)"
fi
[ "$HUSK_RC" -ne 124 ] || die 15 "runtime-nonzero" ',"which":"husk timeout"' "husk did not finish within 300 s (a render that never converges or a blocked Execute)"
[ "$HUSK_RC" -eq 0 ] || die 15 "runtime-nonzero" ",\"exit\":$HUSK_RC" "husk exited $HUSK_RC (see verify/run.log)"
[ -s "$EXR" ] || die 15 "runtime-nonzero" ',"which":"no EXR"' "husk exited 0 but wrote no image"
echo "-- second render: dome twice as bright --" >>"$RUN_LOG"
timeout -k 10 300 "$HUSK" -R "$PLUGIN_ID" -V2 -o "$(mpath "$EXR2X")" "$(mpath "$USDA2X")" >>"$RUN_LOG" 2>&1
HUSK2_RC=$?
[ "$HUSK2_RC" -ne 124 ] || die 15 "runtime-nonzero" ',"which":"husk timeout, second render"' "the second husk render did not finish within 300 s"
if grep -Eq "VUID|Validation Error|VK_ERROR" "$RUN_LOG"; then
  die 14 "validation-layer" ",\"vuid_count\":$(grep -cE 'VUID' "$RUN_LOG" || true)" "the validation layer spoke during the second render (see verify/run.log)"
fi
[ "$HUSK2_RC" -eq 0 ] && [ -s "$EXR2X" ] || die 15 "runtime-nonzero" ",\"which\":\"second render\",\"exit\":$HUSK2_RC" "the second render (dome twice as bright) failed or wrote no image (husk exit $HUSK2_RC)"

echo "== [6] delegate log: validation, seed, device, render thread ==" | tee -a "$RUN_LOG"
grep -q "HoudiniRT: validation layer active" "$RUN_LOG" \
  || die 14 "validation-layer" ',"which":"layer not proven active"' "the delegate never printed 'HoudiniRT: validation layer active'; a silent log proves nothing"
INFO_LINE="$(grep -m1 "HoudiniRT: seed=" "$RUN_LOG" | tr -d '\r' || true)"
[ -n "$INFO_LINE" ] || die 15 "runtime-nonzero" ',"which":"no seed line"' "the delegate printed no 'HoudiniRT: seed=... spp=... device=...' line"
echo "$INFO_LINE" | grep -q "seed=$SEED31 spp=$SPP device=" \
  || die 15 "runtime-nonzero" ',"which":"seed not echoed"' "the delegate did not echo the injected seed $SEED31 and spp $SPP: $INFO_LINE"
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
# The order is judged inside the first render only: a stop from the second render must not stand
# in for a first render that never stopped its thread.
FIRST_RUN="$(head -c "$FIRST_BYTES" "$RUN_LOG" | tr -d '\r')"
L_START="$(printf '%s\n' "$FIRST_RUN" | grep -n -m1 "HoudiniRT: render thread start" | cut -d: -f1)"
L_STOP="$(printf '%s\n' "$FIRST_RUN" | grep -n "HoudiniRT: render thread stop" | tail -n1 | cut -d: -f1)"
[ -n "$L_START" ] && [ -n "$L_STOP" ] && [ "$L_START" -lt "$L_STOP" ] \
  || die 17 "invariant-violation" ',"which":"render thread markers"' "render thread start/stop markers are missing or out of order in the first render"

echo "== [7] the delegate's own image: header, census, linearity ==" | tee -a "$JUDGE_LOG"
"$HYTHON" - "$(mpath "$EXR")" "$(mpath "$EXR2X")" "$(mpath "$TRUTH")" "$PLUGIN_ID" "$T0" "$EXPOSURE_TOL" "$BBOX_TOL" >>"$JUDGE_LOG" 2>&1 <<'PY'
import json, math, os, sys
import numpy as np
import OpenImageIO as oiio
exr, exr2x, truth_path, plugin_id, t0 = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
exposure_tol, bbox_tol = float(sys.argv[6]), float(sys.argv[7])
truth = json.load(open(truth_path))


def fail(msg):
    print("judge FAIL: " + msg); sys.exit(16)


def load(path):
    if os.path.getmtime(path) < t0 - 2:
        fail("%s is older than this run" % os.path.basename(path))
    inp = oiio.ImageInput.open(path)
    if inp is None:
        fail("OpenImageIO cannot open %s: %s" % (os.path.basename(path), oiio.geterror()))
    spec = inp.spec()
    names = list(spec.channelnames)
    if (spec.width, spec.height) != (truth["width"], truth["height"]):
        fail("%s is %dx%d, expected %dx%d (the resolution is on the RenderSettings prim)" % (os.path.basename(path), spec.width, spec.height, truth["width"], truth["height"]))
    if names[:4] != ["R", "G", "B", "A"]:
        fail("%s has channels %s, expected R G B A" % (os.path.basename(path), names))
    px = np.asarray(inp.read_image("float"), dtype=np.float64).reshape(spec.height, spec.width, spec.nchannels)[:, :, :4]
    inp.close()
    return spec, px


spec, px = load(exr)
print("judge: %dx%d channels=%s" % (spec.width, spec.height, list(spec.channelnames)))

# husk records the delegate and its render stats in one of two places: packed as JSON in
# "husk:render_stats" (delegates with a UsdRenderers.json entry), or as top-level attributes
# "__delegate" plus one attribute per stat (every other delegate).
KEYS = ["__delegate", "hrt_seed", "hrt_bounces", "hrt_mesh_count", "hrt_triangle_count"] +["hrt_bbox_%s_%s" % (a, b) for a in ("min", "max") for b in "xyz"]
raw = spec.getattribute("husk:render_stats")
if raw:
    stats = json.loads(raw); where = "husk:render_stats"
else:
    stats = {}
    for key in KEYS:
        value = spec.getattribute(key)
        if value is not None:
            stats[key] = value
    where = "the EXR header"
print("judge: delegate and stats read from %s" % where)
if "__delegate" not in stats:
    fail("the EXR names no delegate: neither husk:render_stats nor __delegate is in its header (was it written by husk?)")
if stats.get("__delegate") != plugin_id:
    fail("%s names delegate %r, expected %r" % (where, stats.get("__delegate"), plugin_id))
header_seed = "absent"
if "hrt_seed" in stats:
    if int(float(str(stats["hrt_seed"]))) != int(truth["seed"]):
        fail("header hrt_seed %r does not equal the seed %r" % (stats["hrt_seed"], truth["seed"]))
    header_seed = "match"
else:
    print("judge WARN: hrt_seed is not in the EXR header; relying on the delegate log line")


def number(key):
    if key not in stats:
        fail("the census is incomplete: %s is not in the EXR header (GetRenderStats must return it)" % key)
    try:
        return float(str(stats[key]))
    except ValueError:
        fail("%s in the EXR header is %r, not a number" % (key, stats[key]))


# --- the bounce budget the delegate says it used: the nonce draws it, so a hardcoded count matches
# one run in six at best (see HONEST LIMITS); the setting has to be read to pass reliably ---------
got_bounces = number("hrt_bounces")
if got_bounces != round(got_bounces) or int(got_bounces) != int(truth["bounces"]):
    fail("the delegate reports hrt_bounces %d, the render settings say houdinirt:bounces = %d" % (got_bounces, truth["bounces"]))

# --- translation census: what the delegate says it synced, against the stage read here --------
got_meshes = number("hrt_mesh_count"); got_tris = number("hrt_triangle_count")
print("judge: census meshes %d (stage %d), triangles %d (stage %d)" % (got_meshes, truth["meshes"], got_tris, truth["triangles"]))
if int(got_meshes) != truth["meshes"]:
    fail("the delegate synced %d meshes, the stage has %d" % (got_meshes, truth["meshes"]))
if int(got_tris) != truth["triangles"]:
    fail("the delegate reports %d triangles, the stage has %d after a fan triangulation (n - 2 per n-sided face)" % (got_tris, truth["triangles"]))
diag = math.sqrt(sum((truth["bbox_max"][k] - truth["bbox_min"][k]) ** 2 for k in range(3)))
worst_bbox = 0.0
for which in ("min", "max"):
    for k, axis in enumerate("xyz"):
        got = number("hrt_bbox_%s_%s" % (which, axis)); want = truth["bbox_%s" % which][k]
        worst_bbox = max(worst_bbox, abs(got - want) / diag)
        if abs(got - want) > bbox_tol * diag + 1e-6:
            fail("world bounding box %s.%s is %.6f, the stage gives %.6f (diagonal %.4f): the mesh transform is not applied the way the stage applies it" % (which, axis, got, want, diag))
print("judge: census bounding box agrees (worst %.2e of the diagonal)" % worst_bbox)

# --- the image itself -------------------------------------------------------------------------
rgb, a = px[:, :, :3], px[:, :, 3]
if not np.isfinite(px).all():
    fail("the image contains NaN or Inf")
if (rgb < 0.0).any():
    fail("the image contains negative radiance")
if a.min() < -1e-4 or a.max() > 1.0 + 1e-3:
    fail("alpha leaves [0, 1] (min %.4f, max %.4f)" % (a.min(), a.max()))
if not (a > 0.999).any():
    fail("no pixel is fully covered: the camera does not see the scene")
if not (a < 0.001).any():
    fail("every pixel is covered: a camera ray that hits nothing must have alpha 0")
empty = a < 1e-6
if empty.any() and float(rgb[empty].max()) > 1e-4:
    fail("pixels with alpha 0 are not black (max %.5f): the dome must not be visible to the camera and RGB must be premultiplied" % float(rgb[empty].max()))

# --- exposure invariance: twice the dome, twice the picture --------------------------------
spec2, px2 = load(exr2x)
m1 = rgb.reshape(-1, 3).mean(0); m2 = px2[:, :, :3].reshape(-1, 3).mean(0)
ratio = m2 / np.maximum(m1, 1e-30)
print("judge: mean beauty %s, with the dome doubled %s, ratio %s" % (np.round(m1, 6).tolist(), np.round(m2, 6).tolist(), np.round(ratio, 5).tolist()))
if (m1 <= 0.0).any():
    fail("a channel of the image is black on average")
if np.abs(ratio - 2.0).max() > 2.0 * exposure_tol:
    fail("doubling the dome changed the mean by %s, expected 2.0 within %.1f%%: the render is not linear in the light (tone mapping, clamping, or the intensity is not applied)" % (np.round(ratio, 4).tolist(), 100.0 * exposure_tol))
if abs(float(px2[:, :, 3].mean()) - float(a.mean())) > 2e-3:
    fail("coverage changed when only the dome changed (alpha mean %.4f vs %.4f)" % (float(a.mean()), float(px2[:, :, 3].mean())))
print("judge: delegate image checks passed | header_seed=%s exposure_ratio_worst=%.5f coverage=%.4f" % (header_seed, float(np.abs(ratio - 2.0).max()), float(a.mean())))
PY
JRC=$?
case "$JRC" in
  0) : ;;
  16) die 16 "hash-mismatch" ',"which":"delegate image"' "$(grep -m1 'judge FAIL' "$JUDGE_LOG" || echo 'the delegate image failed its checks') (see verify/judge.log)" ;;
  *)  die 15 "runtime-nonzero" ",\"which\":\"judge exit $JRC\"" "the judge could not run (see verify/judge.log)" ;;
esac
grep -q "^judge: delegate image checks passed" "$JUDGE_LOG" || die 15 "runtime-nonzero" ',"which":"judge verdict missing"' "the judge exited 0 without a verdict"
HEADER_SEED="$(grep -oE 'header_seed=[a-z]+' "$JUDGE_LOG" | tail -n1 | cut -d= -f2)"

echo "== [8] reference render: Karma CPU on the same stage ==" | tee -a "$KARMA_LOG"
K0=$(date +%s)
timeout -k 10 120 "$HUSK" -R BRAY_HdKarma -V2 -o "$(mpath "$KEXR")" "$(mpath "$USDA")" >>"$KARMA_LOG" 2>&1
KARMA_RC=$?
K1=$(date +%s)
[ "$KARMA_RC" -ne 124 ] || die 13 "binary-missing" ',"which":"karma reference timeout"' "Karma CPU did not finish the reference within 120 s (measured 4 to 11 s on this workstation; is the machine busy?). This is the reference renderer, not the delegate"
[ "$KARMA_RC" -eq 0 ] && [ -s "$KEXR" ] \
  || die 13 "binary-missing" ",\"which\":\"karma reference\",\"exit\":$KARMA_RC" "Karma CPU could not render the reference (husk exit $KARMA_RC; a licence or install problem, not the delegate's; see verify/karma.log)"
echo "karma reference rendered in $((K1 - K0)) s" | tee -a "$KARMA_LOG"

echo "== [9] independent judge: beauty against Karma CPU ==" | tee -a "$JUDGE_LOG"
"$HYTHON" - "$(mpath "$EXR")" "$(mpath "$KEXR")" "$(mpath "$TRUTH")" "$K0" "$FLIP_MAX" "$LEVEL_TOL" "$(mpath "$OUT/flip_error.exr")" "$DA_MAX" >>"$JUDGE_LOG" 2>&1 <<'PY'
import json, os, sys
import numpy as np
import OpenImageIO as oiio
exr, kexr, truth_path, k0 = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
flip_max, level_tol, err_path, da_max = float(sys.argv[5]), float(sys.argv[6]), sys.argv[7], float(sys.argv[8])
truth = json.load(open(truth_path))
dome = np.asarray(truth["dome_radiance"], dtype=np.float64)


def fail(msg):
    print("judge FAIL: " + msg); sys.exit(16)


def broken(msg):
    print("judge BROKEN REFERENCE: " + msg); sys.exit(13)


def load(path):
    inp = oiio.ImageInput.open(path)
    if inp is None:
        return None, None
    spec = inp.spec()
    px = np.asarray(inp.read_image("float"), dtype=np.float64).reshape(spec.height, spec.width, spec.nchannels)
    inp.close()
    return spec, px


# --- LDR-FLIP in numpy -------------------------------------------------------------------------
# A port of NVIDIA's reference implementation (github.com/NVlabs/flip, src/pytorch/flip_loss.py and
# src/cpp/FLIP.h; BSD 3-Clause, Copyright (c) 2020-2025 NVIDIA CORPORATION & AFFILIATES). Constants and
# order of operations are theirs. Checked against the flip-evaluator package to 5e-5 (see the notes).
PPD = (0.7 * 3840 / 0.7) * np.pi / 180.0
QC, QF, PC, PT = 0.7, 0.5, 0.4, 0.95
RGB2XYZ = np.array([[10135552 / 24577794, 8788810 / 24577794, 4435075 / 24577794],
                    [2613072 / 12288897, 8788810 / 12288897, 887015 / 12288897],
                    [1425312 / 73733382, 8788810 / 73733382, 70074185 / 73733382]])
XYZ2RGB = np.array([[3.241003275, -1.537398934, -0.498615861],
                    [-0.969224334, 1.875930071, 0.041554224],
                    [0.055639423, -0.204011202, 1.057148933]])
ILL = np.array([0.950428545, 1.000000000, 1.088900371])
INV_ILL = np.array([1.052156925, 1.000000000, 0.918357670])


def srgb2lin(c):
    return np.where(c > 0.04045, ((np.maximum(c, 0.04045) + 0.055) / 1.055) ** 2.4, c / 12.92)


def lin2ycxcz(c):
    xyz = (c @ RGB2XYZ.T) * INV_ILL
    return np.stack([116.0 * xyz[..., 1] - 16.0, 500.0 * (xyz[..., 0] - xyz[..., 1]), 200.0 * (xyz[..., 1] - xyz[..., 2])], axis=-1)


def ycxcz2lin(c):
    y = (c[..., 0] + 16.0) / 116.0
    xyz = np.stack([y + c[..., 1] / 500.0, y, y - c[..., 2] / 200.0], axis=-1) * ILL
    return xyz @ XYZ2RGB.T


def lin2lab(c):
    xyz = (c @ RGB2XYZ.T) * INV_ILL
    d = 6.0 / 29.0
    f = np.where(xyz > d ** 3, np.cbrt(np.maximum(xyz, d ** 3)), xyz / (3.0 * d * d) + 4.0 / 29.0)
    return np.stack([116.0 * f[..., 1] - 16.0, 500.0 * (f[..., 0] - f[..., 1]), 200.0 * (f[..., 1] - f[..., 2])], axis=-1)


def hunt(lab):
    out = lab.copy()
    out[..., 1] *= 0.01 * lab[..., 0]
    out[..., 2] *= 0.01 * lab[..., 0]
    return out


def hyab(p, q):
    d = p - q
    return np.abs(d[..., 0]) + np.sqrt(d[..., 1] ** 2 + d[..., 2] ** 2)


def correlate(img, k):
    r = k.shape[0] // 2
    h, w = img.shape
    p = np.pad(img, r, mode="edge")
    out = np.zeros((h, w), dtype=np.float64)
    for dy in range(2 * r + 1):
        for dx in range(2 * r + 1):
            if k[dy, dx] != 0.0:
                out += k[dy, dx] * p[dy:dy + h, dx:dx + w]
    return out


def csf(a1, b1, a2, b2):
    r = int(np.ceil(3.0 * np.sqrt(0.04 / (2.0 * np.pi ** 2)) * PPD))
    x, y = np.meshgrid(np.arange(-r, r + 1), np.arange(-r, r + 1))
    z = (x / PPD) ** 2 + (y / PPD) ** 2
    g = a1 * np.sqrt(np.pi / b1) * np.exp(-np.pi ** 2 * z / b1) + a2 * np.sqrt(np.pi / b2) * np.exp(-np.pi ** 2 * z / b2)
    return g / g.sum()


def spatial(ycxcz):
    f = np.stack([correlate(ycxcz[..., 0], csf(1.0, 0.0047, 0.0, 1e-5)),
                  correlate(ycxcz[..., 1], csf(1.0, 0.0053, 0.0, 1e-5)),
                  correlate(ycxcz[..., 2], csf(34.1, 0.04, 13.5, 0.025))], axis=-1)
    return np.clip(ycxcz2lin(f), 0.0, 1.0)


def features(y, kind):
    sd = 0.5 * 0.082 * PPD
    r = int(np.ceil(3.0 * sd))
    x, yy = np.meshgrid(np.arange(-r, r + 1), np.arange(-r, r + 1))
    g = np.exp(-(x ** 2 + yy ** 2) / (2.0 * sd * sd))
    gx = -x * g if kind == "edge" else (x ** 2 / (sd * sd) - 1.0) * g
    neg = -gx[gx < 0].sum(); pos = gx[gx > 0].sum()
    gx = np.where(gx < 0, gx / neg, gx / pos)
    fx = correlate(y, gx); fy = correlate(y, gx.T)
    return np.sqrt(fx * fx + fy * fy)


def flip_ldr(reference, test):
    ref = lin2ycxcz(srgb2lin(np.clip(reference, 0.0, 1.0)))
    tst = lin2ycxcz(srgb2lin(np.clip(test, 0.0, 1.0)))
    power = hyab(hunt(lin2lab(spatial(ref))), hunt(lin2lab(spatial(tst)))) ** QC
    cmax = float(hyab(hunt(lin2lab(np.array([[0.0, 1.0, 0.0]]))), hunt(lin2lab(np.array([[0.0, 0.0, 1.0]]))))[0]) ** QC
    pccmax = PC * cmax
    de_c = np.where(power < pccmax, (PT / pccmax) * power, PT + ((power - pccmax) / (cmax - pccmax)) * (1.0 - PT))
    ry = (ref[..., 0] + 16.0) / 116.0
    ty = (tst[..., 0] + 16.0) / 116.0
    de_f = np.maximum(np.abs(features(ry, "edge") - features(ty, "edge")), np.abs(features(ty, "point") - features(ry, "point")))
    de_f = ((1.0 / np.sqrt(2.0)) * de_f) ** QF
    return de_c ** (1.0 - de_f)


def log_encode(linear_rgb):
    """Scene-linear radiance -> [0, 1] over 12 stops (2^-8 .. 2^4): an equal ratio is an equal step at every level."""
    x = np.clip(linear_rgb, 2.0 ** -8, 2.0 ** 4)
    return (np.log2(x) + 8.0) / 12.0


def composite(px):
    """Premultiplied RGBA over the dome's own radiance: the picture a camera-visible dome would have given.
    Against black, a silhouette pixel's value is its coverage and the log encoding magnifies that."""
    return px[:, :, :3] + (1.0 - np.clip(px[:, :, 3:4], 0.0, 1.0)) * dome[None, None, :]


# --- the two images ----------------------------------------------------------------------------
spec, d = load(exr)
kspec, k = load(kexr)
if kspec is None:
    broken("OpenImageIO cannot open Karma's image")
if os.path.getmtime(kexr) < k0 - 2:
    broken("Karma's image is older than this run")
kstats = kspec.getattribute("husk:render_stats")
if not kstats or json.loads(kstats).get("__delegate") != "BRAY_HdKarma":
    broken("the reference image was not written by Karma CPU (husk:render_stats names %r)" % (json.loads(kstats).get("__delegate") if kstats else None))
if (kspec.width, kspec.height) != (truth["width"], truth["height"]) or list(kspec.channelnames)[:4] != ["R", "G", "B", "A"]:
    broken("Karma's image is %dx%d %s" % (kspec.width, kspec.height, list(kspec.channelnames)))
d = d[:, :, :4]; k = k[:, :, :4]
if not np.isfinite(k).all():
    broken("Karma's image contains NaN or Inf")

# --- coverage, level, then FLIP ------------------------------------------------------------------
da = float(np.abs(d[:, :, 3] - k[:, :, 3]).mean())
print("judge: coverage delegate %.4f karma %.4f, mean |difference| %.5f" % (float(d[:, :, 3].mean()), float(k[:, :, 3].mean()), da))
full = (d[:, :, 3] > 0.999) & (k[:, :, 3] > 0.999)
if int(full.sum()) < 500:
    fail("only %d pixels are fully covered in both images (coverage delegate %.4f, Karma %.4f): the delegate is not looking at the same scene as Karma. Check the camera and the mesh transforms" % (int(full.sum()), float(d[:, :, 3].mean()), float(k[:, :, 3].mean())))
level = d[:, :, :3][full].mean(0) / k[:, :, :3][full].mean(0)
print("judge: level, delegate over karma, on %d fully covered pixels: %s" % (int(full.sum()), np.round(level, 5).tolist()))

err = flip_ldr(log_encode(composite(k)), log_encode(composite(d)))
mask = np.maximum(d[:, :, 3], k[:, :, 3]) > 0.0
vals = err[mask]
p99 = float(np.percentile(vals, 99.0))
edge = mask & ~full
worst = err >= p99
on_edge = int((worst & edge).sum()); n_worst = int((worst & mask).sum())
ys, xs = np.where(worst & mask)
print("judge: FLIP over %d covered pixels: 99th percentile %.4f (ceiling %.4f), mean %.4f, median %.4f, max %.4f" % (int(mask.sum()), p99, flip_max, float(vals.mean()), float(np.percentile(vals, 50.0)), float(vals.max())))
print("judge: the worst 1%% is %d pixels, %d of them on a silhouette, inside x %d..%d, y %d..%d of %dx%d" % (n_worst, on_edge, int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max()), truth["width"], truth["height"]))
try:
    out = oiio.ImageOutput.create(err_path)
    es = oiio.ImageSpec(err.shape[1], err.shape[0], 1, "float"); es.channelnames = ("flip",)
    out.open(err_path, es); out.write_image(err.astype(np.float32)); out.close()
    print("judge: error map written to build/s4/flip_error.exr")
except Exception as e:
    print("judge: (could not write the error map: %s)" % e)

# When the two images do not even cover the same pixels, say that first: it explains every other number.
shape = ""
if da > 0.002:
    shape = " FIRST look at the shape: the delegate and Karma do not cover the same pixels (alpha differs by %.4f on average over the frame; two correct renders differ by 0.0001). A mesh is missing or misplaced, or the camera is off" % da
if np.abs(level - 1.0).max() > level_tol:
    hint = "brighter" if float(level.mean()) > 1.0 else "darker"
    cause = shape or " A level error the same in every channel points at the light (intensity * 2^exposure * color), the albedo, or the bounce count; one that differs by channel points at a colour that is not applied per channel"
    fail("the fully covered pixels are %s than Karma's by %s per channel (tolerance %.1f%%).%s" % (hint, ["%+.2f%%" % (100.0 * (v - 1.0)) for v in level], 100.0 * level_tol, cause))
if p99 > flip_max:
    where = "mostly on silhouettes: camera, mesh transform, triangulation or the pixel filter" if on_edge * 2 > n_worst else "mostly inside the surfaces: normals, bounces, or something missing from the scene"
    fail("FLIP 99th percentile is %.4f, the ceiling is %.4f. The worst pixels are %s.%s" % (p99, flip_max, where, shape))
if da > da_max:
    fail("the silhouettes do not match Karma's: alpha differs by %.5f on average over the frame (ceiling %.4f; two correct renders differ by 0.0001). Check the pixel filter first (one pixel wide, every sample jittered inside its own pixel), then a sub-pixel camera offset" % (da, da_max))
print("judge: beauty matches Karma CPU | flip_p99=%.4f level_worst=%.5f coverage_delta=%.5f" % (p99, float(np.abs(level - 1.0).max()), da))
PY
JRC=$?
case "$JRC" in
  0) : ;;
  16) die 16 "hash-mismatch" ',"which":"beauty vs karma"' "$(grep 'judge FAIL' "$JUDGE_LOG" | tail -n1 || echo 'the beauty does not match Karma') (see verify/judge.log)" ;;
  13) die 13 "binary-missing" ',"which":"karma reference image"' "$(grep 'judge BROKEN REFERENCE' "$JUDGE_LOG" | tail -n1 || echo 'the Karma reference image is unusable') (see verify/judge.log)" ;;
  *)  die 15 "runtime-nonzero" ",\"which\":\"judge exit $JRC\"" "the judge could not run (see verify/judge.log)" ;;
esac
grep -q "^judge: beauty matches Karma CPU" "$JUDGE_LOG" || die 15 "runtime-nonzero" ',"which":"judge verdict missing"' "the judge exited 0 without a verdict"
FLIP_P99="$(grep -oE 'flip_p99=[0-9.]+' "$JUDGE_LOG" | tail -n1 | cut -d= -f2)"
LEVEL_WORST="$(grep -oE 'level_worst=[0-9.]+' "$JUDGE_LOG" | tail -n1 | cut -d= -f2)"

emit "all" ",\"plugin\":\"$PLUGIN_ID\",\"houdini\":\"$HVER\",\"render_seconds\":$((T1 - T0)),\"karma_seconds\":$((K1 - K0)),\"flip_p99\":$FLIP_P99,\"flip_max\":$FLIP_MAX,\"level_worst\":$LEVEL_WORST,\"header_seed\":\"$HEADER_SEED\",\"pause_resume\":\"static-only\",\"device\":\"$(echo "$DEVICE" | sed 's/"/\\"/g' | cut -c1-120)\""
echo "PASS: s4 first light - $PLUGIN_ID on $DEVICE rendered the usd-wg teapot under one dome through husk; census matches the stage; the image is linear in the light; beauty matches Karma CPU (FLIP p99 $FLIP_P99, ceiling $FLIP_MAX; level within $LEVEL_WORST); validation silent; seed $SEED31"
exit 0
