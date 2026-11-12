#!/usr/bin/env bash
# verify/s5_liveedits.sh - exam for s5-live-edits.
# Drafted by Claude (exam author, not a code author) on 2026-11-11 from specs/build-spec.md section
# s5-live-edits and the sealed s4 exam only. No s5 code or design note was read to write it.
#
# Called by the orchestrator as: s5_liveedits.sh <SEED>   (fresh unsigned 64-bit nonce per run)
# Contract: specs/build-spec.md, section s5-live-edits. This header restates it in full.
#
# ---------------------------------------------------------------------------------------------
# THE CONTRACT (everything the delegate must do to pass; nothing else is assumed)
#
# [A] NO REGRESSION. The sealed verify/s4_firstlight.sh runs first, unchanged, with its own nonce
#     derived from this one (decimal of the first 15 hex digits of sha256("s5-live-edits/s4:<SEED>")).
#     It builds and installs the plugin into build/plugin_stage; this exam renders with that build.
#     If it fails, its exit code is this exam's exit code and nothing else runs.
#
# [B] THE EDIT SEQUENCE. The exam writes two .usdc stages from the nonce, identical except for the
#     ballast. Five meshes (Hydra rprim type "mesh", subdivisionScheme "none", no authored normals),
#     each with a constant primvars:displayColor as its Lambert albedo, under one uniform dome:
#       /World/teapot/Geometry  the usd-wg teapot (the s4 asset, sha256-pinned), non-uniform scale
#       /World/table            one polygon with 9 to 14 sides
#       /World/grid             a wavy grid of quads (the deforming mesh)
#       /World/sphere           a polygon sphere (the hideable mesh)
#       /World/ballast          a static grid of quads, 50 units below the table and out of view:
#                               exactly 1,000,000 triangles in one stage, 4,000,000 in the other
#     Camera, dome (intensity, exposure, colour), albedos, poses and the bounce count come from the
#     nonce. Settings on /Render/settings, delivered as render settings as in s4:
#         houdinirt:seed (int)   houdinirt:spp (int)   houdinirt:bounces (int)
#     Frames 1..11. Frame 1 is the scene. Every frame from 2 on applies exactly one edit; the order
#     is drawn from the nonce (visibility off always comes before visibility on). The ten edits:
#         transform    x4  translate (and for the teapot and the grid, rotate) one visible mesh
#         visibility   x2  the sphere's visibility to invisible, later back to inherited
#         points       x2  new grid point positions, same topology
#         topology     x1  new grid resolution (new counts, indices and points)
#         displayColor x1  a new constant colour on the teapot, the table or the grid
#     ONE husk session renders the whole sequence and keeps ONE delegate alive across frames:
#         husk -R <plugin id> -f 1 -n 11 [--preframe-script build/s5/preframe_<1m|4m>.py]
#              -o build/s5/<1m|4m>/seq.$F4.exr <stage>
#     How the edits reach husk is EDIT_MODE below (fixed at the seal; notes, decision 1):
#       timesampled: an attribute that some edit changes is time-sampled on every frame (value held
#         between edits); every other attribute has no samples. Probed 2026-11-11: Hydra then
#         re-delivers EVERY time-sampled attribute as dirty on EVERY frame, changed or not (a
#         transform-only frame still brings DirtyPoints for the grid and DirtyVisibility for the
#         sphere). A delta is a value that differs from what the delegate already holds; the counters
#         count real changes, not dirty bits.
#       preframe: the stage has no time samples; before each frame the exam's own --preframe-script
#         sets exactly the attributes that frame's edit changes. Probed 2026-11-11: Hydra then dirties
#         only the edited prim, only on its frame, so dirty bits and real changes coincide.
#
# [C] CORRECT AFTER EVERY EDIT (16). Every frame k of each sequence is rendered again from scratch:
#         husk -R <plugin id> -f k -n 1 -o build/s5/<1m|4m>/one.$F4.exr <same stage>
#     a fresh husk and a fresh delegate (in preframe mode the script applies every edit up to k before
#     the first sync), same seed and spp. Same seed and spp must mean the same
#     samples: the delegate's random sequence for a pixel may depend on the seed, the pixel and the
#     sample index, not on the frame number, the scene version or the edit history.
#     For every frame of both sequences, sequence image against from-scratch image:
#       - per-channel mean of the pixels fully covered in both (alpha > 0.999) within LEVEL_TOL;
#       - LDR-FLIP 99th percentile at most FLIP_MAX, after compositing both over the dome's radiance
#         and log-encoding 12 stops, over the pixels either image covers (the s4 judge's pipeline);
#       - mean |alpha difference| over the frame at most DA_MAX.
#     And every edit must show: frame k of a sequence differs from frame k-1 by more than 2% in at
#     least CHANGE_MIN pixels (the exam draws every edit to be visible; an edit the delegate drops
#     in both runs would otherwise pass).
#
# [D] EDITS COST WHAT THEY CHANGE (16). Every frame's EXR header carries these integer counters
#     (GetRenderStats; husk writes each as a top-level attribute, as for hrt_seed in s4). Each one
#     counts the work done for THAT frame: since the previous frame's image, not since the start.
#         hrt_blas_builds  hrt_blas_refits  hrt_tlas_builds  hrt_tlas_updates
#         hrt_geometry_upload_bytes   bytes of vertex (position, normal) and index data uploaded
#         hrt_table_upload_bytes      bytes uploaded into the per-mesh table (colours and the like)
#     Rules, per frame from 2 on (frame 1 builds everything and is recorded, not judged):
#       transform or visibility frame: blas_builds = 0, blas_refits = 0, geometry_upload_bytes = 0,
#                                      tlas_builds + tlas_updates = 1
#       points frame:     blas_builds + blas_refits = 1, and
#                         geometry_upload_bytes <= 1.1 x VERTEX DATA of the grid
#       topology frame:   blas_builds = 1, blas_refits = 0, and
#                         geometry_upload_bytes <= VERTEX DATA + INDEX DATA of the new grid
#       displayColor frame: blas_builds = 0, blas_refits = 0, geometry_upload_bytes = 0,
#                         0 < table_upload_bytes <= 4096
#     VERTEX DATA of a mesh = its point count x 32 bytes (a float4 position and a float4 normal per
#     point). INDEX DATA = its triangle count x 12 bytes (three 32-bit indices). Both are taken from
#     the stage after the edit. (The spec does not fix these sizes; see the notes.)
#
# [E] COST DOES NOT FOLLOW SCENE SIZE (16). The same edit sequence runs with the 1M and the 4M
#     ballast. On frames 2..11 the six counters above must be identical between the two runs.
#     hrt_edit_ms (a number, milliseconds from Execute seeing the new scene version to the first
#     pass submitted) is required on frames 2..11; over the transform frames, the median of the 4M
#     run must be at most 1.5 x the median of the 1M run + 0.5 ms. No absolute time is judged.
#
# [F] DEVICE-LOCAL SCENE (17). Every sequence frame's header carries hrt_scene_memory, a string of
#     name=kind entries separated by ';' or ',', for example
#         geometry=device_local;index=device_local;normal=device_local;table=device_local;staging=host_visible
#     geometry, index, normal and table must be present and device_local. Any entry that is not
#     device_local must be named staging or readback (a prefix is enough: staging_ring is fine).
#
# [G] AS BEFORE. The delegate prints, as in s4, "HoudiniRT: validation layer active" (14) and
#     "HoudiniRT: seed=<seed> spp=<spp> device=<Vulkan deviceName>" in every husk run (15); the device
#     is not a software device and names the GPU nvidia-smi reports (15); no VUID / Validation Error /
#     VK_ERROR line in any log (14). Every frame's header names this delegate (__delegate) and
#     carries hrt_seed, hrt_bounces and the census of what it rendered at that frame (16):
#         hrt_mesh_count  hrt_triangle_count  hrt_bbox_min_x/y/z  hrt_bbox_max_x/y/z
#     taken over the VISIBLE meshes only: a hidden mesh leaves the census on the frames it is hidden.
#
# WHAT THE EXAM DOES NOT CHECK (boundary and honest limits; measured where noted, see the notes)
#   - No prim insertion or removal beyond visibility; no instancing; no materials beyond displayColor.
#   - No absolute speed. hrt_edit_ms is judged only as a 4M/1M ratio of medians.
#   - The from-scratch reference is rendered by the code under test. A delegate wrong in the same way
#     in both runs passes [C]; the s4 regression (Karma) and the change guard limit that.
#   - Counters are reported by the delegate. The exam cross-checks them against each other, against
#     the scale run and against the images, not against the GPU.
#   - The ballast is out of view, so the 4M/1M timing compares edits, not pictures.
#
# Exit codes mirror orchestrator/verify.ts:
#   11 cmake configure  12 build/link  13 toolchain, asset or exam-side failure
#   14 validation layer  15 runtime / registration / wrong device  16 mismatch  17 invariant
#   (any code from the s4 run is passed through unchanged)
# ---------------------------------------------------------------------------------------------

set -uo pipefail
SEED="${1:-$RANDOM$RANDOM}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/build/s5"
STAGE_DIR="$ROOT/build/plugin_stage"
S4_LOG="$ROOT/verify/s5_s4.log"
RUN_LOG="$ROOT/verify/s5_run.log"
JUDGE_LOG="$ROOT/verify/s5_judge.log"
EVIDENCE="$ROOT/verify/evidence.json"
TRUTH="$OUT/truth.json"
TEAPOT="$ROOT/third_party/usd_wg_teapot/UtahTeapot.usd"
TEAPOT_SHA="7f64b77cd959e8c2b6760bb63ef40a880b6d5daa0f501251294069839ec9f4d4"
PIN="22.0.429"
HFS_DIR="${HRT_HFS:-C:/Program Files/Side Effects Software/Houdini $PIN}"
HYTHON="$HFS_DIR/bin/hython.exe"
HUSK="$HFS_DIR/bin/husk.exe"

# The numbers this exam was drafted with (see the notes). Change one and the rest need re-measuring.
WIDTH=384; HEIGHT=216   # the s4 frame
SPP=1024                # the delegate's samples per pixel, every render
FRAMES=11               # frame 1 plus ten edits
# HOW EDITS REACH HUSK: Joe's decision at the seal (notes, decision 1). Both are implemented and dry-run.
#   timesampled  the edits are USD time samples on the stage; husk advances time
#   preframe     the stage has no time samples; husk --preframe-script sets each frame's edit
EDIT_MODE="timesampled"
BALLASTS="1000000,4000000"
LEVEL_TOL="0.002"       # [C] per-channel mean of fully covered pixels, relative (spec: 0.2%)
FLIP_MAX="0.01"         # [C] FLIP 99th percentile (spec). Dry run on s4 code: 0 (bit-identical); a 1e-6
                        # scale of the whole world, camera included: <= 0.0006; another seed: 0.015-0.019
DA_MAX="0.0004"         # [C] mean |alpha difference| over the frame (spec)
CHANGE_MIN=200          # [C] pixels that must change by more than 2% when an edit is applied
UPLOAD_SLACK="1.1"      # [D] points frame: upload <= 1.1 x vertex data (spec)
TABLE_MAX=4096          # [D] displayColor frame: table upload at most 4 KB (spec)
EDIT_RATIO="1.5"; EDIT_SLACK_MS="0.5"   # [E] 4M median <= 1.5 x 1M median + 0.5 ms (spec)
# Time limits. The s4 run keeps its own (at most 1,320 s). Here: the stage writer 300 s, each sequence
# 300 s, each from-scratch frame 90 s, and every render must start before RENDER_BUDGET seconds have
# passed since the exam began, so the whole exam stays inside the milestone's 2,400 s.
GEN_LIMIT=300; SEQ_LIMIT=300; ONE_LIMIT=90; RENDER_BUDGET=2100
EXAM_T0=$(date +%s)

mkdir -p "$OUT" "$ROOT/verify"
: > "$S4_LOG"; : > "$RUN_LOG"; : > "$JUDGE_LOG"
rm -f "$EVIDENCE" "$TRUTH"
rm -rf "$OUT/1m" "$OUT/4m" "$OUT/logs"

emit() { printf '{"stage":"%s","seed":"%s"%s}\n' "$1" "$SEED" "${2:-}" > "$EVIDENCE"; }
mpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
die() { # die <exit> <stage> <extra-json> <message>
  emit "$2" "$3"; echo "FAIL: $4"; exit "$1"
}
case "$SEED" in ''|*[!0-9]*) die 13 "binary-missing" ',"which":"nonce"' "the nonce '$SEED' is not a decimal number" ;; esac

echo "== [0] prerequisites ==" | tee -a "$RUN_LOG"
[ -f "$ROOT/verify/s4_firstlight.sh" ] || die 13 "binary-missing" ',"which":"verify/s4_firstlight.sh"' "the sealed s4 exam is missing; s5 cannot prove there is no regression"
[ -x "$HYTHON" ] || die 13 "binary-missing" ',"which":"hython"' "hython not found at $HYTHON (set HRT_HFS to a Houdini $PIN install)"
[ -x "$HUSK" ]   || die 13 "binary-missing" ',"which":"husk"' "husk not found at $HUSK"
HVER="$("$HUSK" --version 2>&1 | tr -d '\r' | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1)"
[ "$HVER" = "$PIN" ] || die 13 "binary-missing" ",\"which\":\"houdini version\",\"found\":\"$HVER\"" "this exam is pinned to Houdini $PIN, found '$HVER'"
for tool in timeout sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || die 13 "binary-missing" ",\"which\":\"$tool\"" "coreutils $tool not on PATH"
done
[ -f "$TEAPOT" ] || die 13 "binary-missing" ',"which":"teapot asset"' "third_party/usd_wg_teapot/UtahTeapot.usd is missing (it is part of the repo; see its README)"
GOT_SHA="$(sha256sum "$TEAPOT" | cut -d' ' -f1)"
[ "$GOT_SHA" = "$TEAPOT_SHA" ] || die 13 "binary-missing" ',"which":"teapot asset changed"' "third_party/usd_wg_teapot/UtahTeapot.usd is not the file this exam was written against (sha256 $GOT_SHA)"
echo "houdini $HVER | teapot asset ok" | tee -a "$RUN_LOG"

echo "== [1] no regression: the sealed s4 exam, with its own nonce ==" | tee -a "$RUN_LOG"
# 15 hex digits are 60 bits: a positive number in bash's signed 64-bit arithmetic (the s3 seed flaw
# came from reducing an unsigned 64-bit nonce in bash).
S4_HEX="$(printf 's5-live-edits/s4:%s' "$SEED" | sha256sum | cut -c1-15)"
S4_NONCE="$((16#$S4_HEX))"
echo "s4 nonce $S4_NONCE" | tee -a "$RUN_LOG"
S4_T0=$(date +%s)
bash "$ROOT/verify/s4_firstlight.sh" "$S4_NONCE" > "$S4_LOG" 2>&1
S4_RC=$?
S4_T1=$(date +%s)
S4_EV="$(tr -d '\r\n' < "$EVIDENCE" 2>/dev/null)"
[ -n "$S4_EV" ] || S4_EV='{}'
tail -n 3 "$S4_LOG" | tr -d '\r' | tee -a "$RUN_LOG"
if [ "$S4_RC" -ne 0 ]; then
  emit "s4-regression" ",\"s4_nonce\":\"$S4_NONCE\",\"s4_exit\":$S4_RC,\"s4\":$S4_EV"
  echo "FAIL: the s4 exam failed on the s5 build (exit $S4_RC, passed through; see verify/s5_s4.log and the s4 logs in verify/)"
  exit "$S4_RC"
fi
echo "s4 passed in $((S4_T1 - S4_T0)) s" | tee -a "$RUN_LOG"

# Hermetic Houdini environment for every hython and husk call below (the s4 exam's, verbatim).
export HOUDINI_USER_PREF_DIR="$(mpath "$OUT")/prefs__HVER__"
export HOUDINI_PATH='&'
export HOUDINI_NO_ENV_FILE=1
unset PYTHONHOME PYTHONPATH HOUDINI_PACKAGE_DIR HOUDINI_DSO_PATH PXR_PLUGINPATH_NAME OCIO
# the delegate's own switches: nothing from the caller's shell may steer the run under test;
# the exam sets back only what it owns
while IFS= read -r v; do unset "$v"; done < <(compgen -e | grep '^HOUDINIRT_' || true)
export PXR_PLUGINPATH_NAME="$(mpath "$STAGE_DIR")"
export HOUDINIRT_VALIDATION=1

echo "== [2] the plugin the s4 run installed: hermetic registration ==" | tee -a "$RUN_LOG"
[ -f "$STAGE_DIR/plugInfo.json" ] || die 13 "binary-missing" ',"which":"plugInfo.json"' "build/plugin_stage/plugInfo.json is missing after the s4 run"
PLUGIN_ID="$("$HYTHON" - "$(mpath "$STAGE_DIR")" 2>>"$RUN_LOG" <<'PY' | tr -d '\r' | tail -n1
import os, sys
from pxr import Plug
stage = os.path.normcase(os.path.realpath(sys.argv[1]))
inside, outside = [], []
for pl in Plug.Registry().GetAllPlugins():
    for name, d in pl.metadata.get("Types", {}).items():
        if "HdRendererPlugin" not in d.get("bases", []) or d.get("displayName") != "HoudiniRT":
            continue
        p = os.path.normcase(os.path.realpath(pl.path))
        (inside if p.startswith(stage + os.sep) else outside).append(name)
if outside:
    print("ERR:ambient")
elif len(inside) != 1:
    print("ERR:count=%d" % len(inside))
else:
    print("OK:" + inside[0])
PY
)"
case "$PLUGIN_ID" in
  OK:*) PLUGIN_ID="${PLUGIN_ID#OK:}" ;;
  *) die 15 "runtime-nonzero" ",\"which\":\"registration\",\"probe\":\"$PLUGIN_ID\"" "the plugin did not register as exactly one HdRendererPlugin named HoudiniRT from build/plugin_stage (probe: ${PLUGIN_ID:-no output})" ;;
esac
echo "registered: $PLUGIN_ID" | tee -a "$RUN_LOG"

echo "== [3] derive the scene and the edit sequence from the nonce; write both stages (exam-side) ==" | tee -a "$RUN_LOG"
mkdir -p "$OUT/1m" "$OUT/4m" "$OUT/logs"
timeout -k 10 "$GEN_LIMIT" "$HYTHON" - "$SEED" "$(mpath "$TEAPOT")" "$(mpath "$OUT")" "$WIDTH" "$HEIGHT" "$SPP" "$FRAMES" "$BALLASTS" "$EDIT_MODE" >>"$RUN_LOG" 2>&1 <<'PY' || die 13 "binary-missing" ',"which":"stage generator"' "the exam could not write its own stages (exam bug; see verify/s5_run.log)"
import json, math, random, sys
import numpy as np
from pxr import Usd, UsdGeom, UsdLux, UsdRender, Sdf, Gf, Vt

# argv: nonce teapot out_dir W H SPP FRAMES BALLAST_LIST(comma)
nonce = int(sys.argv[1]); teapot_asset = sys.argv[2]; out_dir = sys.argv[3]
W, H, SPP, NF = int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6]), int(sys.argv[7])
BALLASTS = [int(v) for v in sys.argv[8].split(",")]
MODE = sys.argv[9]                   # "timesampled": edits are time samples; "preframe": the stage has no
assert MODE in ("timesampled", "preframe")   # samples and husk --preframe-script applies each frame's edit

seed31 = nonce % 2147483647
if seed31 < 2:
    seed31 = 2                       # 1 is the delegate's default seed: never inject a default
rng = random.Random("s5-live-edits:%d" % nonce)

# --- what the nonce decides: the static scene ----------------------------------------------------
yaw0 = rng.uniform(0.0, 360.0); tilt = rng.uniform(-8.0, 8.0)
scale = (rng.uniform(0.50, 0.70), rng.uniform(0.50, 0.70), rng.uniform(0.50, 0.70))
lift = rng.uniform(0.03, 0.06)
table_radius = rng.uniform(0.66, 0.78); table_sides = rng.choice([9, 11, 12, 14]); table_spin = rng.uniform(0.0, 360.0)
ang_grid = rng.uniform(0.0, 2.0 * math.pi); ang_sphere = ang_grid + rng.uniform(0.6, 1.4) * math.pi
grid_size = rng.uniform(0.26, 0.32); grid_res0 = rng.choice([10, 11, 12, 13, 14])
grid_amp0 = rng.uniform(0.025, 0.045); grid_phase0 = rng.uniform(0.0, 2.0 * math.pi); grid_k = rng.uniform(14.0, 22.0)
sph_r = rng.uniform(0.085, 0.11); sph_lon = rng.choice([16, 18, 20, 24]); sph_lat = rng.choice([10, 12, 14])
albedo = {n: [round(rng.uniform(0.30, 0.65), 4) for _ in range(3)] for n in ("teapot", "table", "grid", "sphere")}
azim = math.radians(rng.uniform(0.0, 360.0)); elev = math.radians(rng.uniform(20.0, 45.0))
focal = rng.uniform(30.0, 42.0); fit = rng.uniform(1.00, 1.12)
aim = (rng.uniform(-0.03, 0.03), rng.uniform(0.04, 0.10), rng.uniform(-0.03, 0.03))
intensity = round(rng.uniform(0.8, 1.6), 4); exposure = round(rng.uniform(-0.75, 0.75), 4)
tint = [round(rng.uniform(0.55, 1.45), 4) for _ in range(3)]
BOUNCES = rng.choice([6, 7, 9, 10, 11, 12])

# --- the edit sequence: frame 1 is the scene, every later frame applies exactly one edit ----------
kinds = ["transform"] * 4 + ["visibility_off", "visibility_on", "points", "points", "topology", "displaycolor"]
assert len(kinds) == NF - 1, "the edit list is written for %d frames" % (len(kinds) + 1)
rng.shuffle(kinds)
if kinds.index("visibility_off") > kinds.index("visibility_on"):
    i, j = kinds.index("visibility_off"), kinds.index("visibility_on")
    kinds[i], kinds[j] = kinds[j], kinds[i]

state = {"teapot_xf": (0.0, lift, 0.0, yaw0), "grid_xf": (0.42 * math.cos(ang_grid), 0.07, -0.42 * math.sin(ang_grid), rng.uniform(0.0, 90.0)),
         "sphere_xf": (0.45 * math.cos(ang_sphere), sph_r + 0.005, -0.45 * math.sin(ang_sphere), 0.0),
         "sphere_vis": True, "grid_geo": (grid_res0, grid_amp0, grid_phase0), "color": {k: list(v) for k, v in albedo.items()}}
frames = [json.loads(json.dumps(state))]
plan = [{"frame": 1, "kind": "initial", "target": None}]
for f, kind in enumerate(kinds, start=2):
    s = json.loads(json.dumps(frames[-1])); e = {"frame": f, "kind": kind}
    if kind == "transform":
        pool = ["teapot", "grid"] + (["sphere"] if s["sphere_vis"] else [])
        tgt = rng.choice(pool); x, y, z, r = s[tgt + "_xf"]
        dx, dz = rng.choice([-1, 1]) * rng.uniform(0.03, 0.06), rng.choice([-1, 1]) * rng.uniform(0.03, 0.06)
        s[tgt + "_xf"] = [x + dx, y, z + dz, r + (rng.choice([-1, 1]) * rng.uniform(20.0, 50.0) if tgt != "sphere" else 0.0)]
        e["target"] = tgt
    elif kind == "visibility_off":
        s["sphere_vis"] = False; e["target"] = "sphere"
    elif kind == "visibility_on":
        s["sphere_vis"] = True; e["target"] = "sphere"
    elif kind == "points":
        res, amp, ph = s["grid_geo"]
        s["grid_geo"] = [res, rng.uniform(0.025, 0.045), ph + rng.uniform(1.5, 4.5)]; e["target"] = "grid"
    elif kind == "topology":
        res, amp, ph = s["grid_geo"]
        s["grid_geo"] = [rng.choice([r for r in (6, 7, 8, 16, 17, 18) if r != res]), rng.uniform(0.025, 0.045), ph + rng.uniform(1.5, 4.5)]
        e["target"] = "grid"                                   # new topology and a new shape, so the picture changes
    elif kind == "displaycolor":
        tgt = rng.choice(["teapot", "table", "grid"])
        s["color"][tgt] = [round(min(0.85, max(0.10, c + rng.choice([-1, 1]) * rng.uniform(0.15, 0.25))), 4) for c in s["color"][tgt]]
        e["target"] = tgt
    frames.append(s); plan.append(e)


# --- geometry -------------------------------------------------------------------------------------
def grid_mesh(res, amp, ph):
    n = res + 1
    u = np.linspace(-0.5 * grid_size, 0.5 * grid_size, n)
    X, Z = np.meshgrid(u, u, indexing="xy")
    Y = amp * np.sin(grid_k * X + ph) * np.cos(grid_k * 0.7 * Z)
    pts = np.stack([X.ravel(), Y.ravel(), Z.ravel()], axis=1).astype(np.float32)
    i = np.arange(res); I, J = np.meshgrid(i, i, indexing="xy")
    v0 = (J * n + I).ravel()
    idx = np.stack([v0, v0 + n, v0 + n + 1, v0 + 1], axis=1).astype(np.int32).ravel()   # quads
    return pts, np.full(res * res, 4, np.int32), idx


def sphere_mesh():
    pts = [(0.0, sph_r, 0.0)]
    for a in range(1, sph_lat):
        th = math.pi * a / sph_lat
        for b in range(sph_lon):
            ph = 2.0 * math.pi * b / sph_lon
            pts.append((sph_r * math.sin(th) * math.cos(ph), sph_r * math.cos(th), -sph_r * math.sin(th) * math.sin(ph)))
    pts.append((0.0, -sph_r, 0.0)); last = len(pts) - 1
    counts, idx = [], []
    ring = lambda a, b: 1 + (a - 1) * sph_lon + (b % sph_lon)
    for b in range(sph_lon):
        counts.append(3); idx += [0, ring(1, b), ring(1, b + 1)]
    for a in range(1, sph_lat - 1):
        for b in range(sph_lon):
            counts.append(4); idx += [ring(a, b), ring(a + 1, b), ring(a + 1, b + 1), ring(a, b + 1)]
    for b in range(sph_lon):
        counts.append(3); idx += [last, ring(sph_lat - 1, b + 1), ring(sph_lat - 1, b)]
    return np.array(pts, np.float32), np.array(counts, np.int32), np.array(idx, np.int32)


def ballast_mesh(tris):
    # a dense, gently curved sheet 50 units below the table: out of every camera's view, in every census
    nx = 1000 if tris <= 1000000 else 2000
    nz = tris // (2 * nx)
    assert 2 * nx * nz == tris
    x = np.linspace(-1.0, 1.0, nx + 1, dtype=np.float32); z = np.linspace(-0.5, 0.5, nz + 1, dtype=np.float32)
    X, Z = np.meshgrid(x, z, indexing="xy")
    Y = -50.0 + 0.1 * np.cos(2.0 * X) * np.cos(3.0 * Z)
    pts = np.stack([X.ravel(), Y.ravel().astype(np.float32), Z.ravel()], axis=1).astype(np.float32)
    I, J = np.meshgrid(np.arange(nx, dtype=np.int32), np.arange(nz, dtype=np.int32), indexing="xy")
    v0 = (J * (nx + 1) + I).ravel()
    idx = np.stack([v0, v0 + nx + 1, v0 + nx + 2, v0 + 1], axis=1).astype(np.int32).ravel()
    return pts, np.full(nx * nz, 4, np.int32), idx


def set_mesh(mesh, pts, counts, idx, t=Usd.TimeCode.Default()):
    mesh.GetPointsAttr().Set(Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(pts)), t)
    mesh.GetFaceVertexCountsAttr().Set(Vt.IntArray.FromNumpy(np.ascontiguousarray(counts)), t)
    mesh.GetFaceVertexIndicesAttr().Set(Vt.IntArray.FromNumpy(np.ascontiguousarray(idx)), t)
    lo, hi = pts.min(0), pts.max(0)
    mesh.GetExtentAttr().Set(Vt.Vec3fArray([Gf.Vec3f(*map(float, lo)), Gf.Vec3f(*map(float, hi))]), t)


def varies(key):
    return any(json.dumps(fr[key]) != json.dumps(frames[0][key]) for fr in frames)


def color_varies(name):
    return any(fr["color"][name] != frames[0]["color"][name] for fr in frames)


HAP = 36.0
half_v = math.atan(0.5 * HAP * H / W / focal)
dist = fit * 0.80 / math.sin(half_v)                           # 0.80 bounds the table, the grid and the sphere
eye = (aim[0] + dist * math.cos(elev) * math.cos(azim), aim[1] + dist * math.sin(elev), aim[2] + dist * math.cos(elev) * math.sin(azim))
FR = list(range(1, NF + 1))


REC = {"sampled": True, "edits": None}   # how build() writes an attribute that some edit changes


def norm(v):
    """Gf / Vt / Python values -> plain JSON values (exact: float32 and float64 both survive a double)."""
    if isinstance(v, str):
        return v
    if hasattr(v, "__len__"):
        return [norm(x) for x in v]
    return float(v) if not isinstance(v, (bool, int)) else v


def timed(attr_or_op, values, vary):
    attr = attr_or_op.GetAttr() if hasattr(attr_or_op, "GetAttr") else attr_or_op
    if vary and REC["sampled"]:
        for f, v in zip(FR, values):
            attr.Set(v, Usd.TimeCode(f))
    else:
        attr.Set(values[0])
        if vary and REC["edits"] is not None:          # preframe mode: the script sets frame f's value
            REC["edits"].append({"path": str(attr.GetPath()), "type": str(attr.GetTypeName()), "values": [norm(v) for v in values]})


def build(path, ballast_tris):
    st = Usd.Stage.CreateNew(path) if path else Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(st, UsdGeom.Tokens.y); UsdGeom.SetStageMetersPerUnit(st, 1.0)
    st.SetStartTimeCode(1); st.SetEndTimeCode(NF); st.SetTimeCodesPerSecond(24); st.SetFramesPerSecond(24)
    UsdGeom.Xform.Define(st, "/World")
    tp = UsdGeom.Xform.Define(st, "/World/teapot")
    tp.GetPrim().GetReferences().AddReference(teapot_asset, "/UtahTeapot")
    v = varies("teapot_xf")
    timed(tp.AddTranslateOp(), [Gf.Vec3d(*fr["teapot_xf"][:3]) for fr in frames], v)
    timed(tp.AddRotateYOp(), [fr["teapot_xf"][3] for fr in frames], v)
    tp.AddRotateXOp().Set(tilt); tp.AddScaleOp().Set(Gf.Vec3f(*scale))
    geo = UsdGeom.Mesh(st.OverridePrim("/World/teapot/Geometry"))
    geo.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    timed(geo.CreateDisplayColorPrimvar(UsdGeom.Tokens.constant), [Vt.Vec3fArray([Gf.Vec3f(*fr["color"]["teapot"])]) for fr in frames], color_varies("teapot"))
    table = UsdGeom.Mesh.Define(st, "/World/table")
    ring = np.array([(table_radius * math.cos(2.0 * math.pi * k / table_sides), 0.0, -table_radius * math.sin(2.0 * math.pi * k / table_sides)) for k in range(table_sides)], np.float32)
    set_mesh(table, ring, np.array([table_sides], np.int32), np.arange(table_sides, dtype=np.int32))
    table.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none); table.AddRotateYOp().Set(table_spin)
    timed(table.CreateDisplayColorPrimvar(UsdGeom.Tokens.constant), [Vt.Vec3fArray([Gf.Vec3f(*fr["color"]["table"])]) for fr in frames], color_varies("table"))
    grid = UsdGeom.Mesh.Define(st, "/World/grid")
    grid.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    v = varies("grid_xf")
    timed(grid.AddTranslateOp(), [Gf.Vec3d(*fr["grid_xf"][:3]) for fr in frames], v)
    timed(grid.AddRotateYOp(), [fr["grid_xf"][3] for fr in frames], v)
    geos = [grid_mesh(*fr["grid_geo"]) for fr in frames]; v = varies("grid_geo")
    timed(grid.GetPointsAttr(), [Vt.Vec3fArray.FromNumpy(g[0]) for g in geos], v)
    timed(grid.GetFaceVertexCountsAttr(), [Vt.IntArray.FromNumpy(g[1]) for g in geos], v)
    timed(grid.GetFaceVertexIndicesAttr(), [Vt.IntArray.FromNumpy(g[2]) for g in geos], v)
    timed(grid.GetExtentAttr(), [Vt.Vec3fArray([Gf.Vec3f(*map(float, g[0].min(0))), Gf.Vec3f(*map(float, g[0].max(0)))]) for g in geos], v)
    timed(grid.CreateDisplayColorPrimvar(UsdGeom.Tokens.constant), [Vt.Vec3fArray([Gf.Vec3f(*fr["color"]["grid"])]) for fr in frames], color_varies("grid"))
    sph = UsdGeom.Mesh.Define(st, "/World/sphere")
    sph.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none); set_mesh(sph, *sphere_mesh())
    timed(sph.AddTranslateOp(), [Gf.Vec3d(*fr["sphere_xf"][:3]) for fr in frames], varies("sphere_xf"))
    timed(sph.CreateVisibilityAttr(), [UsdGeom.Tokens.inherited if fr["sphere_vis"] else UsdGeom.Tokens.invisible for fr in frames], varies("sphere_vis"))
    sph.CreateDisplayColorPrimvar(UsdGeom.Tokens.constant).Set(Vt.Vec3fArray([Gf.Vec3f(*albedo["sphere"])]))
    bal = UsdGeom.Mesh.Define(st, "/World/ballast")
    bal.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none); set_mesh(bal, *ballast_mesh(ballast_tris))
    bal.CreateDisplayColorPrimvar(UsdGeom.Tokens.constant).Set(Vt.Vec3fArray([Gf.Vec3f(0.5, 0.5, 0.5)]))
    cam = UsdGeom.Camera.Define(st, "/World/cam")
    cam.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(Gf.Vec3d(*eye), Gf.Vec3d(*aim), Gf.Vec3d(0.0, 1.0, 0.0)).GetInverse())
    cam.CreateFocalLengthAttr(focal); cam.CreateHorizontalApertureAttr(HAP); cam.CreateVerticalApertureAttr(HAP * H / W)
    cam.CreateClippingRangeAttr(Gf.Vec2f(0.01, 1000.0))
    dome = UsdLux.DomeLight.Define(st, "/World/dome")
    dome.CreateIntensityAttr(intensity); dome.CreateExposureAttr(exposure); dome.CreateColorAttr(Gf.Vec3f(*tint))
    rs = UsdRender.Settings.Define(st, "/Render/settings")
    rs.CreateResolutionAttr(Gf.Vec2i(W, H)); rs.CreateCameraRel().SetTargets(["/World/cam"])
    p = rs.GetPrim()
    for name, val in (("houdinirt:seed", seed31), ("houdinirt:spp", SPP), ("houdinirt:bounces", BOUNCES)):
        p.CreateAttribute(name, Sdf.ValueTypeNames.Int).Set(val)
    st.SetMetadata("renderSettingsPrimPath", "/Render/settings")
    if path:
        st.GetRootLayer().Save()
    return st


# --- the census of every frame, from the composed stage and nothing else ------------------------
def census(st):
    cache, out = {}, []
    for f in FR:
        t = Usd.TimeCode(f); xc = UsdGeom.XformCache(t)
        meshes = tris = 0; lo = np.full(3, np.inf); hi = np.full(3, -np.inf); sizes = {}
        for prim in st.Traverse():
            if not prim.IsA(UsdGeom.Mesh):
                continue
            if UsdGeom.Imageable(prim).ComputeVisibility(t) == UsdGeom.Tokens.invisible:
                continue                                       # a hidden mesh leaves the census
            m = UsdGeom.Mesh(prim); key = str(prim.GetPath())
            attrs = (m.GetPointsAttr(), m.GetFaceVertexCountsAttr(), m.GetFaceVertexIndicesAttr())
            static = not any(a.ValueMightBeTimeVarying() for a in attrs)
            if static and key in cache:
                used, ntri, npts = cache[key]
            else:
                pts = np.asarray(attrs[0].Get(t), np.float64); counts = np.asarray(attrs[1].Get(t), np.int64)
                idx = np.asarray(attrs[2].Get(t), np.int64)
                ok = np.repeat(counts >= 3, counts)
                used = pts[np.unique(idx[ok])]                     # every point a triangle uses, and no other
                ntri = int((counts[counts >= 3] - 2).sum()); npts = int(len(pts))   # a fan: n - 2 per n-gon
                if static:
                    cache[key] = (used, ntri, npts)
            M = np.array(xc.GetLocalToWorldTransform(prim), np.float64)
            w = used @ M[:3, :3] + M[3, :3]
            lo = np.minimum(lo, w.min(0)); hi = np.maximum(hi, w.max(0))
            meshes += 1; tris += ntri; sizes[key] = {"points": npts, "triangles": ntri}
        out.append({"frame": f, "meshes": meshes, "triangles": tris, "bbox_min": lo.tolist(), "bbox_max": hi.tolist(), "sizes": sizes})
    return out


# husk runs this before every frame in preframe mode (stage and hou are provided by husk). It sets every
# attribute whose current value differs from its value at this frame: in a sequence that is exactly this
# frame's edit; in a from-scratch render of frame k it is every edit up to k, before the first sync.
PREFRAME = r'''import json, hou
from pxr import Gf, Vt
EDITS = json.load(open(r"@EDITS@"))
def norm(v):
    if isinstance(v, str):
        return v
    if hasattr(v, "__len__"):
        return [norm(x) for x in v]
    return float(v) if not isinstance(v, (bool, int)) else v
def typed(t, w):
    if t.endswith("[]"):
        return Vt.IntArray(w) if t == "int[]" else Vt.Vec3fArray([Gf.Vec3f(*p) for p in w])
    return Gf.Vec3d(*w) if t == "double3" else w
f = int(round(hou.frame())); n = 0
for e in EDITS:
    attr = stage.GetAttributeAtPath(e["path"]); want = e["values"][f - 1]
    if norm(attr.Get()) != want:
        attr.Set(typed(e["type"], want)); n += 1
print("HRT-EXAM preframe frame=%d set=%d" % (f, n), flush=True)
'''

radiance = [intensity * (2.0 ** exposure) * c for c in tint]
truth = {"nonce": str(nonce), "mode": MODE, "seed": seed31, "spp": SPP, "bounces": BOUNCES, "width": W, "height": H, "frames": NF,
         "dome_radiance": radiance, "plan": plan, "runs": {}}
for tris in BALLASTS:
    tag = "%dm" % (tris // 1000000)
    path = "%s/stage_%s.usdc" % (out_dir, tag)
    if MODE == "timesampled":
        st = build(path, tris)                          # rendered, and read for the census
    else:
        REC["sampled"], REC["edits"] = False, []
        build(path, tris)                               # rendered: frame 1's values, no time samples
        edits_path = "%s/edits_%s.json" % (out_dir, tag)
        with open(edits_path, "w") as fh:
            json.dump(REC["edits"], fh)
        with open("%s/preframe_%s.py" % (out_dir, tag), "w") as fh:
            fh.write(PREFRAME.replace("@EDITS@", edits_path))
        REC["sampled"], REC["edits"] = True, None
        st = build(None, tris)                          # the same scene time-sampled in memory, for the census
    truth["runs"][tag] = {"stage": path, "ballast_triangles": tris, "census": census(st)}
    print("exam scene %s: %s" % (tag, " | ".join("f%d meshes=%d tris=%d" % (c["frame"], c["meshes"], c["triangles"]) for c in truth["runs"][tag]["census"])))
# the bounds the counter rules use, from the stage: the edited mesh as it is after the edit
first = truth["runs"]["%dm" % (BALLASTS[0] // 1000000)]["census"]
for e in plan:
    if e["kind"] in ("points", "topology"):
        sz = first[e["frame"] - 1]["sizes"]["/World/grid"]
        e["points"] = sz["points"]; e["triangles"] = sz["triangles"]
        e["vertex_bytes"] = 32 * sz["points"]                 # a float4 position and a float4 normal per point
        e["index_bytes"] = 12 * sz["triangles"]               # three 32-bit indices per triangle
with open("%s/truth.json" % out_dir, "w") as fh:
    json.dump(truth, fh)
print("exam plan: " + " ".join("f%d=%s%s" % (e["frame"], e["kind"], ":" + e["target"] if e.get("target") else "") for e in plan))
print("exam scene: seed=%d spp=%d bounces=%d %dx%d frames=%d dome radiance=%s" % (seed31, SPP, BOUNCES, W, H, NF, ["%.4f" % v for v in radiance]))
PY
[ -s "$TRUTH" ] && [ -s "$OUT/stage_1m.usdc" ] && [ -s "$OUT/stage_4m.usdc" ] || die 13 "binary-missing" ',"which":"stage generator output"' "the exam's stages or census were not written (exam bug)"
SEED31="$(tr -d '\r' < "$TRUTH" | sed -n 's/.*"seed": \([0-9][0-9]*\).*/\1/p')"
[ -n "$SEED31" ] || die 13 "binary-missing" ',"which":"seed"' "the exam could not read the seed back from its own census (exam bug)"
grep "^exam " "$RUN_LOG" | tr -d '\r'
case "$EDIT_MODE" in
  timesampled) : ;;
  preframe) [ -s "$OUT/preframe_1m.py" ] && [ -s "$OUT/preframe_4m.py" ] || die 13 "binary-missing" ',"which":"preframe scripts"' "the exam's preframe scripts were not written (exam bug)" ;;
  *) die 13 "binary-missing" ',"which":"edit mode"' "EDIT_MODE '$EDIT_MODE' is neither timesampled nor preframe (exam bug)" ;;
esac
edit_args() { # edit_args <tag>: the husk arguments that deliver the edits in preframe mode
  [ "$EDIT_MODE" = "preframe" ] && printf '%s\n' --preframe-script "$(mpath "$OUT/preframe_$1.py")"
  return 0
}
edits_applied() { # edits_applied <log> <first> <last>: in preframe mode, the exam's own script set the planned edit
  [ "$EDIT_MODE" = "preframe" ] || return 0
  local f n
  for f in $(seq "$2" "$3"); do
    n="$(tr -d '\r' < "$1" | sed -n "s/^HRT-EXAM preframe frame=$f set=\([0-9]*\)$/\1/p" | head -n1)"
    if [ -z "$n" ] || { [ "$f" -eq 1 ] && [ "$n" -ne 0 ]; } || { [ "$f" -gt 1 ] && [ "$n" -lt 1 ]; }; then
      die 13 "binary-missing" ",\"which\":\"preframe edit\",\"frame\":$f" "the exam's preframe script did not apply frame $f's edit as planned (set=${n:-none}; an exam or husk problem, not the delegate's)"
    fi
  done
}

GPU_NAME=""
if command -v nvidia-smi >/dev/null 2>&1; then
  GPU_NAME="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -n1 | tr -d '\r')"
fi
render() { # render <name> <limit> <husk args...>: one husk run under the exam's budget, judged on its log
  local name="$1" limit="$2"; shift 2
  local left=$(( RENDER_BUDGET - ($(date +%s) - EXAM_T0) ))
  [ "$left" -gt 15 ] || die 15 "runtime-nonzero" ",\"which\":\"time budget\",\"at\":\"$name\"" "the exam's render budget ($RENDER_BUDGET s since it began) ran out before $name: the renders are too slow"
  [ "$limit" -le "$left" ] || limit="$left"
  local log="$OUT/logs/$name.log"
  timeout -k 10 "$limit" "$HUSK" -R "$PLUGIN_ID" -V2 "$@" > "$log" 2>&1
  local rc=$?
  { echo "-- $name: husk exit $rc --"; tr -d '\r' < "$log"; } >> "$RUN_LOG"
  if grep -Eq "VUID|Validation Error|VK_ERROR" "$log"; then
    die 14 "validation-layer" ",\"at\":\"$name\",\"vuid_count\":$(grep -cE 'VUID' "$log" || true)" "the validation layer spoke during $name (see build/s5/logs/$name.log)"
  fi
  [ "$rc" -ne 124 ] || die 15 "runtime-nonzero" ",\"which\":\"husk timeout\",\"at\":\"$name\"" "husk did not finish $name within $limit s (a render that never converges, a blocked Execute, or an edit that never lands)"
  [ "$rc" -eq 0 ] || die 15 "runtime-nonzero" ",\"at\":\"$name\",\"exit\":$rc" "husk exited $rc during $name (see build/s5/logs/$name.log)"
  grep -q "HoudiniRT: validation layer active" "$log" \
    || die 14 "validation-layer" ",\"which\":\"layer not proven active\",\"at\":\"$name\"" "the delegate never printed 'HoudiniRT: validation layer active' in $name; a silent log proves nothing"
  local info; info="$(grep -m1 "HoudiniRT: seed=" "$log" | tr -d '\r' || true)"
  [ -n "$info" ] || die 15 "runtime-nonzero" ",\"which\":\"no seed line\",\"at\":\"$name\"" "the delegate printed no 'HoudiniRT: seed=... spp=... device=...' line in $name"
  echo "$info" | grep -q "seed=$SEED31 spp=$SPP device=" \
    || die 15 "runtime-nonzero" ",\"which\":\"seed not echoed\",\"at\":\"$name\"" "the delegate did not echo the injected seed $SEED31 and spp $SPP in $name: $info"
  DEVICE="$(printf "%s" "${info#*device=}" | sed "s/[[:space:]]*$//")"
  [ -n "$DEVICE" ] || die 15 "runtime-nonzero" ',"which":"empty device"' "the delegate named no Vulkan device in $name"
  if echo "$DEVICE" | grep -qiE 'llvmpipe|lavapipe|swiftshader|software|basic render'; then
    die 15 "runtime-nonzero" ',"which":"software device"' "rendered on a software device, not the GPU: $DEVICE"
  fi
  if [ -n "$GPU_NAME" ] && ! echo "$DEVICE" | grep -qiF "$GPU_NAME"; then
    die 15 "runtime-nonzero" ',"which":"wrong device"' "the delegate ran on '$DEVICE', expected the discrete GPU '$GPU_NAME'"
  fi
}
frames_written() { # frames_written <dir> <prefix>: every frame of the sequence exists and is fresh
  local f p
  for f in $(seq 1 "$FRAMES"); do
    p="$1/$2.$(printf '%04d' "$f").exr"
    [ -s "$p" ] || die 15 "runtime-nonzero" ",\"which\":\"missing frame\",\"frame\":$f" "husk exited 0 but wrote no image for frame $f ($p)"
  done
}

echo "== [4] the edit sequences: one husk session, one delegate, $FRAMES frames, per ballast ==" | tee -a "$RUN_LOG"
declare -A SEQ_SECONDS
for tag in 1m 4m; do
  T0=$(date +%s)
  mapfile -t EARGS < <(edit_args "$tag")
  render "seq_$tag" "$SEQ_LIMIT" "${EARGS[@]}" -f 1 -n "$FRAMES" -o "$(mpath "$OUT/$tag")/seq.\$F4.exr" "$(mpath "$OUT/stage_$tag.usdc")"
  SEQ_SECONDS[$tag]=$(( $(date +%s) - T0 ))
  frames_written "$OUT/$tag" seq
  edits_applied "$OUT/logs/seq_$tag.log" 1 "$FRAMES"
  echo "sequence $tag: $FRAMES frames in ${SEQ_SECONDS[$tag]} s" | tee -a "$RUN_LOG"
done

echo "== [5] every frame again from scratch: a fresh husk and a fresh delegate per frame ==" | tee -a "$RUN_LOG"
T0=$(date +%s)
for tag in 1m 4m; do
  mapfile -t EARGS < <(edit_args "$tag")
  for f in $(seq 1 "$FRAMES"); do
    render "one_${tag}_$f" "$ONE_LIMIT" "${EARGS[@]}" -f "$f" -n 1 -o "$(mpath "$OUT/$tag")/one.\$F4.exr" "$(mpath "$OUT/stage_$tag.usdc")"
    edits_applied "$OUT/logs/one_${tag}_$f.log" "$f" "$f"
  done
  frames_written "$OUT/$tag" one
done
ONE_SECONDS=$(( $(date +%s) - T0 ))
echo "from-scratch renders: $((2 * FRAMES)) husk runs in $ONE_SECONDS s" | tee -a "$RUN_LOG"

echo "== [6] the judge: headers and census, frame equality, counters, scale, memory ==" | tee -a "$JUDGE_LOG"
# The judge evaluates everything and prints every finding, then exits with the code of the first
# failing section in contract order: header 16, census 16, equality 16, change 16, counters 16,
# scale 16, memory 17.
"$HYTHON" - "$(mpath "$OUT")" "$PLUGIN_ID" "$EXAM_T0" "$LEVEL_TOL" "$FLIP_MAX" "$DA_MAX" "$CHANGE_MIN" "$UPLOAD_SLACK" "$TABLE_MAX" "$EDIT_RATIO" "$EDIT_SLACK_MS" >>"$JUDGE_LOG" 2>&1 <<'PY'
import json, math, os, sys
import numpy as np
import OpenImageIO as oiio
out, plugin_id, t0 = sys.argv[1], sys.argv[2], int(sys.argv[3])
level_tol, flip_max, da_max, change_min = float(sys.argv[4]), float(sys.argv[5]), float(sys.argv[6]), int(sys.argv[7])
upload_slack, table_max, edit_ratio, edit_slack = float(sys.argv[8]), int(sys.argv[9]), float(sys.argv[10]), float(sys.argv[11])
truth = json.load(open(out + "/truth.json"))
NF, plan, TAGS = truth["frames"], truth["plan"], ("1m", "4m")
dome = np.asarray(truth["dome_radiance"], dtype=np.float64)
SECTIONS = [("header", 16), ("census", 16), ("equality", 16), ("change", 16), ("counters", 16), ("scale", 16), ("memory", 17)]
failures = {name: [] for name, _ in SECTIONS}
summary = {}


def fail(section, msg):
    failures[section].append(msg)
    print("judge FAIL [%s]: %s" % (section, msg))


def broken(msg):
    print("judge EXAM ERROR: " + msg); sys.exit(13)


CENSUS = ["hrt_mesh_count", "hrt_triangle_count"] + ["hrt_bbox_%s_%s" % (a, b) for a in ("min", "max") for b in "xyz"]
COUNTERS = ["hrt_blas_builds", "hrt_blas_refits", "hrt_tlas_builds", "hrt_tlas_updates", "hrt_geometry_upload_bytes", "hrt_table_upload_bytes"]
KEYS = ["__delegate", "hrt_seed", "hrt_bounces", "hrt_edit_ms", "hrt_scene_memory"] + CENSUS + COUNTERS


def load(path):
    """-> (stats, pixels) or (None, None) after recording why. Stats come from husk:render_stats when
    husk packs them there (delegates with a UsdRenderers.json entry), else from top-level attributes."""
    name = os.path.relpath(path, out).replace("\\", "/")
    if os.path.getmtime(path) < t0 - 2:
        fail("header", "%s is older than this run" % name); return None, None
    inp = oiio.ImageInput.open(path)
    if inp is None:
        fail("header", "OpenImageIO cannot open %s: %s" % (name, oiio.geterror())); return None, None
    spec = inp.spec()
    if (spec.width, spec.height) != (truth["width"], truth["height"]) or list(spec.channelnames)[:4] != ["R", "G", "B", "A"]:
        inp.close(); fail("header", "%s is %dx%d %s, expected %dx%d R G B A" % (name, spec.width, spec.height, list(spec.channelnames), truth["width"], truth["height"])); return None, None
    px = np.asarray(inp.read_image("float"), dtype=np.float64).reshape(spec.height, spec.width, spec.nchannels)[:, :, :4]
    inp.close()
    raw = spec.getattribute("husk:render_stats")
    if raw:
        stats = json.loads(raw)
    else:
        stats = {k: spec.getattribute(k) for k in KEYS if spec.getattribute(k) is not None}
    if stats.get("__delegate") != plugin_id:
        fail("header", "%s names delegate %r, expected %r" % (name, stats.get("__delegate"), plugin_id))
    if not np.isfinite(px).all() or (px[:, :, :3] < 0.0).any():
        fail("header", "%s contains NaN, Inf or negative radiance" % name)
    for key, want in (("hrt_seed", truth["seed"]), ("hrt_bounces", truth["bounces"])):
        got = stats.get(key)
        if got is None or float(str(got)) != want:
            fail("header", "%s: %s is %r, the render settings say %d" % (name, key, got, want))
    return stats, px


def num(stats, key):
    try:
        return float(str(stats[key]))
    except (KeyError, ValueError, TypeError):
        return None


# --- every image of both runs, and the census of every frame ---------------------------------------
imgs, stats = {}, {}
for tag in TAGS:
    census = truth["runs"][tag]["census"]
    for kind in ("seq", "one"):
        for f in range(1, NF + 1):
            s, px = load("%s/%s/%s.%04d.exr" % (out, tag, kind, f))
            imgs[(tag, kind, f)], stats[(tag, kind, f)] = px, s
            if s is None:
                continue
            c = census[f - 1]
            diag = math.sqrt(sum((c["bbox_max"][k] - c["bbox_min"][k]) ** 2 for k in range(3)))
            bad = []
            for key in CENSUS:
                got = num(s, key)
                if key in ("hrt_mesh_count", "hrt_triangle_count"):
                    want = c["meshes"] if key == "hrt_mesh_count" else c["triangles"]
                    ok = got is not None and got == want
                else:
                    want = c["bbox_" + key[9:12]]["xyz".index(key[-1])]
                    ok = got is not None and abs(got - want) <= 1e-4 * diag + 1e-6
                if not ok:
                    bad.append("%s %s (stage %s)" % (key, "missing" if got is None else "%.10g" % got, "%.10g" % want))
            if bad:
                hidden = " The sphere is hidden on this frame: a hidden mesh leaves the census." if "/World/sphere" not in c["sizes"] else ""
                fail("census", "%s/%s frame %d (%s): %s.%s" % (tag, kind, f, plan[f - 1]["kind"], "; ".join(bad), hidden))
print("judge: read %d images; census checked on every frame of both runs" % sum(1 for v in imgs.values() if v is not None))


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
    """Premultiplied RGBA over the dome's own radiance (the s4 judge's composite)."""
    return px[:, :, :3] + (1.0 - np.clip(px[:, :, 3:4], 0.0, 1.0)) * dome[None, None, :]


# --- [C] every sequence frame against the same frame from scratch; every edit must show ----------
worst = {"level": 0.0, "flip": 0.0, "da": 0.0, "change_min": None}
for tag in TAGS:
    for f in range(1, NF + 1):
        a, b = imgs[(tag, "seq", f)], imgs[(tag, "one", f)]
        if a is None or b is None:
            continue
        what = "%s frame %d (%s%s)" % (tag, f, plan[f - 1]["kind"], ": " + plan[f - 1]["target"] if plan[f - 1].get("target") else "")
        da = float(np.abs(a[:, :, 3] - b[:, :, 3]).mean())
        full = (a[:, :, 3] > 0.999) & (b[:, :, 3] > 0.999)
        if int(full.sum()) < 500:
            fail("equality", "%s: only %d pixels are fully covered in both the sequence image and the from-scratch image" % (what, int(full.sum()))); continue
        level = a[:, :, :3][full].mean(0) / np.maximum(b[:, :, :3][full].mean(0), 1e-30)
        err = flip_ldr(log_encode(composite(b)), log_encode(composite(a)))
        mask = np.maximum(a[:, :, 3], b[:, :, 3]) > 0.0
        p99 = float(np.percentile(err[mask], 99.0))
        lv = float(np.abs(level - 1.0).max())
        worst["level"] = max(worst["level"], lv); worst["flip"] = max(worst["flip"], p99); worst["da"] = max(worst["da"], da)
        line = "%s: level %s flip_p99 %.5f alpha %.6f" % (what, ["%+.4f%%" % (100.0 * (v - 1.0)) for v in level], p99, da)
        changed = None
        if f > 1 and imgs[(tag, "seq", f - 1)] is not None:
            prev = composite(imgs[(tag, "seq", f - 1)]); cur = composite(a)
            changed = int(((np.abs(cur - prev) / np.maximum(prev, 1e-4)).max(axis=2) > 0.02).sum())
            worst["change_min"] = changed if worst["change_min"] is None else min(worst["change_min"], changed)
            line += " | changed from frame %d: %d px" % (f - 1, changed)
        print("judge: " + line)
        if lv > level_tol:
            fail("equality", "%s: the sequence image differs from the from-scratch image by %s per channel on the fully covered pixels (tolerance %.1f%%). The live scene is not the scene a fresh delegate builds: a stale or half-applied edit, or accumulation that did not restart" % (what, ["%+.3f%%" % (100.0 * (v - 1.0)) for v in level], 100.0 * level_tol))
        if p99 > flip_max:
            fail("equality", "%s: FLIP 99th percentile against the from-scratch image is %.4f, the ceiling is %.4f. Same seed and spp must give the same samples; look for a stale BLAS, a stale instance transform, or a random sequence that depends on the frame or the edit history" % (what, p99, flip_max))
        if da > da_max:
            fail("equality", "%s: coverage differs from the from-scratch image by %.5f on average (ceiling %.4f): a mesh is in the wrong place or (in)visible when it should not be" % (what, da, da_max))
        if changed is not None and changed < change_min:
            fail("change", "%s: only %d pixels changed by more than 2%% from frame %d (at least %d expected). The exam drew this edit to be visible; the delegate did not apply it, in the sequence or in the from-scratch render" % (what, changed, f - 1, change_min))
summary.update({"level_worst": round(worst["level"], 6), "flip_worst": round(worst["flip"], 5), "alpha_worst": round(worst["da"], 6), "change_px_min": worst["change_min"]})


# --- [D] counters, per frame, per run -----------------------------------------------------------
def counters(tag, f):
    s = stats[(tag, "seq", f)]
    if s is None:
        return None
    vals, missing = {}, []
    for key in COUNTERS:
        v = num(s, key)
        if v is None:
            missing.append(key)
        elif v != round(v) or v < 0:
            fail("counters", "%s frame %d: %s is %r, not a non-negative integer" % (tag, f, key, s[key]))
        else:
            vals[key] = int(v)
    if missing:
        fail("counters", "%s frame %d: %s not in the EXR header (GetRenderStats must report every counter on every frame)" % (tag, f, ", ".join(missing)))
        return None
    return vals


C = {(tag, f): counters(tag, f) for tag in TAGS for f in range(1, NF + 1)}
for tag in TAGS:
    for f in range(2, NF + 1):
        c, e = C[(tag, f)], plan[f - 1]
        if c is None:
            continue
        kind = e["kind"]; what = "%s frame %d (%s: %s)" % (tag, f, kind, e["target"])
        print("judge: %s counters %s" % (what, " ".join("%s=%d" % (k[4:], v) for k, v in c.items())))
        nb, nr, geo, tab = c["hrt_blas_builds"], c["hrt_blas_refits"], c["hrt_geometry_upload_bytes"], c["hrt_table_upload_bytes"]
        tl = c["hrt_tlas_builds"] + c["hrt_tlas_updates"]
        if kind in ("transform", "visibility_off", "visibility_on"):
            if nb or nr or geo or tl != 1:
                fail("counters", "%s: a transform or visibility edit must cost one TLAS build or update and nothing else; got blas_builds=%d blas_refits=%d geometry_upload_bytes=%d tlas_builds+tlas_updates=%d" % (what, nb, nr, geo, tl))
        elif kind == "points":
            bound = upload_slack * e["vertex_bytes"]
            if nb + nr != 1 or geo > bound:
                fail("counters", "%s: moved points must cost exactly one BLAS refit or build and at most %.0f upload bytes (1.1 x %d points x 32 B); got blas_builds=%d blas_refits=%d geometry_upload_bytes=%d" % (what, bound, e["points"], nb, nr, geo))
        elif kind == "topology":
            bound = e["vertex_bytes"] + e["index_bytes"]
            if nb != 1 or nr != 0 or geo > bound:
                fail("counters", "%s: a new topology must cost exactly one BLAS build (no refit) and at most %d upload bytes (%d points x 32 B + %d triangles x 12 B); got blas_builds=%d blas_refits=%d geometry_upload_bytes=%d" % (what, bound, e["points"], e["triangles"], nb, nr, geo))
        elif kind == "displaycolor":
            if nb or nr or geo or not (0 < tab <= table_max):
                fail("counters", "%s: a new displayColor must travel in the per-mesh table only (0 < table_upload_bytes <= %d) with no BLAS work and no geometry upload; got blas_builds=%d blas_refits=%d geometry_upload_bytes=%d table_upload_bytes=%d" % (what, table_max, nb, nr, geo, tab))

# --- [E] the same counters at 1M and at 4M; edit time as a ratio of medians ----------------------
for f in range(2, NF + 1):
    a, b = C[("1m", f)], C[("4m", f)]
    if a is not None and b is not None and a != b:
        diff = ["%s %d vs %d" % (k, a[k], b[k]) for k in COUNTERS if a[k] != b[k]]
        fail("scale", "frame %d (%s): the counters differ between the 1M and the 4M ballast: %s. An edit must not cost more because an untouched mesh is bigger" % (f, plan[f - 1]["kind"], "; ".join(diff)))
med = {}
for tag in TAGS:
    ms = []
    for f in range(2, NF + 1):
        s = stats[(tag, "seq", f)]
        v = num(s, "hrt_edit_ms") if s is not None else None
        if s is not None and (v is None or not math.isfinite(v) or v < 0.0):
            fail("scale", "%s frame %d: hrt_edit_ms is %r, expected a non-negative number of milliseconds" % (tag, f, None if s is None else s.get("hrt_edit_ms")))
        elif v is not None and plan[f - 1]["kind"] == "transform":
            ms.append(v)
    if ms:
        med[tag] = float(np.median(ms))
if len(med) == 2:
    limit = edit_ratio * med["1m"] + edit_slack
    print("judge: hrt_edit_ms median over transform frames: 1M %.3f ms, 4M %.3f ms (limit %.3f ms)" % (med["1m"], med["4m"], limit))
    summary.update({"edit_ms_median_1m": round(med["1m"], 4), "edit_ms_median_4m": round(med["4m"], 4)})
    if med["4m"] > limit:
        fail("scale", "a transform edit takes %.3f ms (median) with the 4M ballast and %.3f ms with the 1M ballast; the limit is %.1f x + %.1f ms = %.3f ms. Edit cost follows scene size" % (med["4m"], med["1m"], edit_ratio, edit_slack, limit))

# --- [F] device-local scene memory --------------------------------------------------------------
for tag in TAGS:
    for f in range(1, NF + 1):
        s = stats[(tag, "seq", f)]
        if s is None:
            continue
        raw = s.get("hrt_scene_memory")
        if not isinstance(raw, str) or not raw.strip():
            fail("memory", "%s frame %d: hrt_scene_memory is %r; expected a string like 'geometry=device_local;index=device_local;normal=device_local;table=device_local'" % (tag, f, raw)); continue
        entries = {}
        for part in raw.replace(",", ";").split(";"):
            if part.strip():
                name, _, kind = part.partition("=")
                entries[name.strip().lower()] = kind.strip().lower()
        for need in ("geometry", "index", "normal", "table"):
            if entries.get(need) != "device_local":
                fail("memory", "%s frame %d: the %s buffer is %r, it must be device_local (hrt_scene_memory = %r)" % (tag, f, need, entries.get(need, "not reported"), raw))
        for name, kind in entries.items():
            if kind != "device_local" and not (name.startswith("staging") or name.startswith("readback")):
                fail("memory", "%s frame %d: %s is %s; only staging and readback may live outside device-local memory (hrt_scene_memory = %r)" % (tag, f, name, kind, raw))

# --- the verdict: every finding was printed; exit with the first failing section, in contract order
summary["failures"] = {name: len(v) for name, v in failures.items() if v}
with open(out + "/judge.json", "w") as fh:
    json.dump(summary, fh)
for name, code in SECTIONS:
    if failures[name]:
        print("judge VERDICT: %s failed (%d finding(s)); first: %s" % (name, len(failures[name]), failures[name][0]))
        sys.exit(code)
print("judge: s5 checks passed | flip_worst=%.5f level_worst=%.6f alpha_worst=%.6f change_px_min=%s" % (worst["flip"], worst["level"], worst["da"], worst["change_min"]))
PY
JRC=$?
JUDGE_JSON="$(tr -d '\r\n' < "$OUT/judge.json" 2>/dev/null)"
[ -n "$JUDGE_JSON" ] || JUDGE_JSON='{}'
VERDICT="$(grep -m1 '^judge VERDICT' "$JUDGE_LOG" | tr -d '\r' || true)"
case "$JRC" in
  0) : ;;
  16) die 16 "hash-mismatch" ",\"which\":\"$(echo "$VERDICT" | sed -n 's/^judge VERDICT: \([a-z]*\) failed.*/\1/p')\",\"judge\":$JUDGE_JSON" "${VERDICT#judge VERDICT: } (every finding is in verify/s5_judge.log)" ;;
  17) die 17 "invariant-violation" ",\"which\":\"memory\",\"judge\":$JUDGE_JSON" "${VERDICT#judge VERDICT: } (every finding is in verify/s5_judge.log)" ;;
  13) die 13 "binary-missing" ',"which":"judge"' "$(grep -m1 'judge EXAM ERROR' "$JUDGE_LOG" | tr -d '\r' || echo 'the judge could not read the exam truth') (exam bug)" ;;
  *)  die 15 "runtime-nonzero" ",\"which\":\"judge exit $JRC\"" "the judge could not run (see verify/s5_judge.log)" ;;
esac
grep -q "^judge: s5 checks passed" "$JUDGE_LOG" || die 15 "runtime-nonzero" ',"which":"judge verdict missing"' "the judge exited 0 without a verdict"

emit "all" ",\"plugin\":\"$PLUGIN_ID\",\"houdini\":\"$HVER\",\"s4_nonce\":\"$S4_NONCE\",\"s4\":$S4_EV,\"seq_seconds_1m\":${SEQ_SECONDS[1m]},\"seq_seconds_4m\":${SEQ_SECONDS[4m]},\"one_seconds\":$ONE_SECONDS,\"exam_seconds\":$(( $(date +%s) - EXAM_T0 )),\"judge\":$JUDGE_JSON,\"device\":\"$(echo "$DEVICE" | sed 's/"/\\"/g' | cut -c1-120)\""
echo "PASS: s5 live edits - $PLUGIN_ID on $DEVICE: s4 still passes; $FRAMES-frame edit sequences at 1M and 4M ballast match from-scratch renders frame by frame; every edit cost what it changed, the same at both scene sizes; scene memory device-local; validation silent; seed $SEED31"
exit 0
