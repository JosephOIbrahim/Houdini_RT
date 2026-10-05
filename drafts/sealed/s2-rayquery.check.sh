#!/usr/bin/env bash
# drafts/s2-rayquery.check.sh — CANDIDATE exam for s2-rayquery (frontier mode).
# Not trusted until a human moves it to verify/s2_rayquery.sh, reviews it, and commits.
#
# Called by the orchestrator as: s2_rayquery.sh <SEED>   (fresh 64-bit nonce per run)
# Contract: specs/build-spec.md §s2-rayquery.
#
# What a cheating implementation would have to do to pass:
#   - compile a shader that actually contains ray-query instructions (the exam scans the
#     SPIR-V for the GL_EXT_ray_query opcodes), on a device it must report as rayQuery-capable;
#   - report first-hit distances for 24 seed-chosen directions from a seed-chosen interior
#     camera point that match an analytic ray–box solution (computed in this script's own
#     Python, not by the program) to 1e-4;
#   - classify shadow blocking for a 12x12 probe grid against a seed-shaped occluder
#     triangle EXACTLY as the analytic barycentric test says — a wrong triangle test, a
#     flipped winding, or an epsilon fudge flips at least one verdict;
#   - converge a closed Lambertian box of seed-chosen albedo and emission to L = E/(1-rho)
#     within 1% + 3 sigma, with the finite-bounce tail extrapolated consistently and the raw
#     value provably below the analytic limit (a formula-only fake would have to fabricate a
#     plausible standard error AND a consistent raw/extrapolated pair under a seed it did not choose);
#   - while echoing the injected seed, naming a discrete GPU, keeping the validation layer
#     silent, and writing fresh CSVs this run. Reproducing all of that is as hard as tracing
#     the rays for real.
#
# Exit codes mirror orchestrator/verify.ts:
#   11 shader compile  12 host build/link  13 toolchain or binary missing
#   14 validation layer  15 runtime / wrong device  16 mismatch or tolerance  17 invariant

set -uo pipefail
SEED="${1:-$RANDOM$RANDOM}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/build/s2"
BUILD_LOG="$ROOT/verify/build.log"
RUN_LOG="$ROOT/verify/run.log"
JUDGE_LOG="$ROOT/verify/judge.log"
EVIDENCE="$ROOT/verify/evidence.json"
CFG="$OUT/config.json"
HITS="$OUT/hits.csv"
OCC="$OUT/occlusion.csv"
FURN="$OUT/furnace.csv"
mkdir -p "$OUT"
: > "$BUILD_LOG"; : > "$RUN_LOG"; : > "$JUDGE_LOG"
rm -f "$EVIDENCE" "$CFG" "$HITS" "$OCC" "$FURN"

emit() { printf '{"stage":"%s","seed":"%s"%s}\n' "$1" "$SEED" "${2:-}" > "$EVIDENCE"; }
winpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }

# The host parses --seed with strtoul into uint32. Reduce the 64-bit nonce so it survives.
SEED32=$(( SEED % 4294967291 ))
[ "$SEED32" -gt 0 ] || SEED32=1

PY=python3; command -v python3 >/dev/null 2>&1 || PY=python

echo "== [0] prerequisites ==" | tee -a "$BUILD_LOG"
command -v "$PY" >/dev/null 2>&1 || { emit "binary-missing" ',"which":"python"'; echo "FAIL: python not found"; exit 13; }
[ -n "${VULKAN_SDK:-}" ] || { emit "binary-missing" ',"which":"VULKAN_SDK"'; echo "FAIL: VULKAN_SDK not set"; exit 13; }

echo "== [0b] derive the scene from the nonce (exam-side, independent of the program) ==" | tee -a "$BUILD_LOG"
# The exam owns the geometry: it is written to config.json and handed to the host. The host
# must use exactly this scene — a cached answer for a previous seed cannot match it.
"$PY" - "$SEED" > "$CFG" <<'PY' || { emit "binary-missing" ',"which":"config generator"'; echo "FAIL: config generation failed"; exit 13; }
import json, math, random, sys
seed = int(sys.argv[1])
rng = random.Random(seed)
# Camera strictly interior to the unit cube (margin 0.2 keeps analytic edge cases away).
cam = [round(rng.uniform(0.2, 0.8), 6) for _ in range(3)]
# 24 seed-chosen directions; reject near-axis-parallel ones so the analytic distance is
# numerically well-conditioned (|component| >= 0.15 after normalization).
dirs = []
while len(dirs) < 24:
    v = [rng.uniform(-1.0, 1.0) for _ in range(3)]
    n = (v[0]*v[0] + v[1]*v[1] + v[2]*v[2]) ** 0.5
    if n < 1e-6: continue
    v = [x / n for x in v]
    if min(abs(x) for x in v) < 0.15: continue
    dirs.append([round(x, 8) for x in v])
# Occluder triangle between the light (0.5, 0.5, 2.0) and the probe plane z = 0:
# center + circumradius so its shadow reliably covers some but not all probes.
def dot(a, b): return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]
def cross(a, b): return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]
LIGHT = [0.5, 0.5, 2.0]
def shadow_count(tri):
    """Analytic blocked count on the probe grid (same test the judge uses)."""
    A, B, C = tri
    e2 = [B[i]-A[i] for i in range(3)]
    e3 = [C[i]-A[i] for i in range(3)]
    n = 0
    for p in PROBES:
        e1 = [p[i]-LIGHT[i] for i in range(3)]
        a = dot(e2, cross(e1, e3))
        if abs(a) < 1e-12: continue
        f = 1.0/a
        s = [p[i]-A[i] for i in range(3)]
        u = f*dot(s, cross(e1, e3))
        v = f*dot(e1, cross(s, e2))
        m = min(u, v, 1.0-u-v)
        if abs(m) >= 1e-3 and m > 0.0: n += 1
    return n
grid = [round(0.05 + 0.9 * i / 11.0, 6) for i in range(12)]
PROBES = [[x, y, 0.0] for x in grid for y in grid]
occl = None
for _ in range(100):   # deterministic per seed: same rng stream, same retry sequence
    cx, cy, cz = rng.uniform(0.35, 0.65), rng.uniform(0.35, 0.65), rng.uniform(0.35, 0.65)
    r, th = rng.uniform(0.18, 0.30), rng.uniform(0.0, 2.0*math.pi)
    tri = [[round(cx + r*math.cos(th + k*2.0*math.pi/3.0), 6),
            round(cy + r*math.sin(th + k*2.0*math.pi/3.0), 6), cz] for k in range(3)]
    n = shadow_count(tri)
    if 1 <= n <= len(PROBES) - 1:
        occl = tri
        break
assert occl is not None, "no non-degenerate occluder found (exam bug)"
probes = PROBES
rho = round(rng.uniform(0.3, 0.8), 6)          # seed-chosen albedo in [0.3, 0.8]
E   = round(rng.uniform(0.5, 1.5), 6)          # seed-chosen emission (not a round number)
budget = rng.choice([8, 16, 32])               # seed-chosen bounce budget
json.dump({"seed": seed, "camera": cam, "directions": dirs, "occluder": occl,
           "light": [0.5, 0.5, 2.0], "probes": probes, "rho": rho, "E": E,
           "budget": budget, "samples": 4096}, sys.stdout)
PY

echo "== [1] build (MSVC via a generated .bat helper) ==" | tee -a "$BUILD_LOG"
# The exam generates its own build helper under build/ so the exact compile line is part of
# the trusted script, not a file the worktree could pre-place.
cat > "$OUT/build_s2.bat" <<'BAT'
@echo off
REM generated by s2_rayquery.sh - MSVC build helper for the s2 exam.
REM Exit 11: shader compile failed. Exit 12: host compile/link failed. Exit 13: toolchain missing.
REM Arguments: %1 = repo root (Windows path), %2 = output dir (Windows path)
setlocal
set "ROOT=%~1"
set "OUT=%~2"
if "%ROOT%"=="" exit /b 13
if "%OUT%"=="" exit /b 13
if "%VULKAN_SDK%"=="" (
  echo s2_build: VULKAN_SDK is not set
  exit /b 13
)
if not exist "%OUT%" mkdir "%OUT%"

set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" (
  echo s2_build: vswhere.exe not found; is Visual Studio or Build Tools installed?
  exit /b 13
)
for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSROOT=%%i"
if "%VSROOT%"=="" (
  echo s2_build: no Visual Studio with the C++ x64 toolset found
  exit /b 13
)
call "%VSROOT%\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 (
  echo s2_build: vcvars64.bat failed
  exit /b 13
)

where glslangValidator >nul 2>&1
if errorlevel 1 set "PATH=%VULKAN_SDK%\Bin;%PATH%"
echo s2_build: glslangValidator rayq.comp
glslangValidator -V --target-env vulkan1.2 -S comp "%ROOT%\src\furnace\rayq.comp" -o "%OUT%\rayq.spv"
if errorlevel 1 (
  echo s2_build: shader compile failed
  exit /b 11
)

echo s2_build: cl rayq_host.cpp
pushd "%OUT%"
cl /nologo /std:c++17 /O2 /EHsc /bigobj /W3 /wd4305 /wd4996 /I"%VULKAN_SDK%\Include" "%ROOT%\src\furnace\rayq_host.cpp" /Fe:"%OUT%\rayq_host.exe" /Fo:"%OUT%\\" /link /LIBPATH:"%VULKAN_SDK%\Lib" vulkan-1.lib
set "RC=%ERRORLEVEL%"
popd
if not "%RC%"=="0" (
  echo s2_build: host compile/link failed ^(%RC%^)
  exit /b 12
)
if not exist "%OUT%\rayq_host.exe" exit /b 12
echo s2_build: ok
exit /b 0
BAT

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    MSYS_NO_PATHCONV=1 cmd.exe /c "$(winpath "$OUT/build_s2.bat")" "$(winpath "$ROOT")" "$(winpath "$OUT")" >>"$BUILD_LOG" 2>&1
    RC=$? ;;
  *)
    # Non-Windows fallback for the cloud test bench (correctness only, never the real exam target).
    glslangValidator -V --target-env vulkan1.2 -S comp "$ROOT/src/furnace/rayq.comp" -o "$OUT/rayq.spv" >>"$BUILD_LOG" 2>&1 || { RC=11; }
    [ "${RC:-0}" -eq 0 ] && { g++ -std=c++17 -O2 "$ROOT/src/furnace/rayq_host.cpp" -o "$OUT/rayq_host" -lvulkan >>"$BUILD_LOG" 2>&1 || RC=12; }
    RC=${RC:-0} ;;
esac
case "$RC" in
  0) ;;
  11) emit "cmake-configure" ',"which":"glslangValidator"'; echo "FAIL: shader compile (see verify/build.log)"; exit 11 ;;
  12) emit "build-link"; echo "FAIL: host compile/link (see verify/build.log)"; exit 12 ;;
  *)  emit "binary-missing" ",\"which\":\"s2_build rc $RC\""; echo "FAIL: build helper exited $RC (toolchain?)"; exit 13 ;;
esac

echo "== [2] locate binary and prove the shader uses ray queries ==" | tee -a "$RUN_LOG"
BIN="$OUT/rayq_host.exe"; [ -x "$BIN" ] || BIN="$OUT/rayq_host"
[ -x "$BIN" ] || { emit "binary-missing" ',"which":"rayq_host"'; echo "FAIL: rayq_host not built"; exit 13; }
[ -f "$OUT/rayq.spv" ] || { emit "binary-missing" ',"which":"rayq.spv"'; echo "FAIL: SPIR-V missing"; exit 13; }
# GL_EXT_ray_query instructions: OpRayQueryInitializeKHR = 4642 (0x1222),
# OpRayQueryGetIntersectionKHR = 4645 (0x1225). A shader that never issues a ray query
# cannot contain these opcodes. (Heuristic: a 2-byte pattern could in principle appear in
# literals; the radiometry below is the real proof.)
"$PY" -c 'import sys; b=open(sys.argv[1],"rb").read(); sys.exit(0 if (b.find(b"\x22\x12")>=0 or b.find(b"\x25\x12")>=0) else 1)' "$OUT/rayq.spv" \
  || { emit "cmake-configure" ',"which":"ray-query opcodes in SPIR-V"'; echo "FAIL: compiled shader contains no ray-query instructions"; exit 11; }

echo "== [3] run ray queries on the GPU (seed=$SEED32, validation on) ==" | tee -a "$RUN_LOG"
T0=$(date +%s)
"$BIN" --spv "$(winpath "$OUT/rayq.spv")" --config "$(winpath "$CFG")" \
       --seed "$SEED32" --out-dir "$(winpath "$OUT")" >>"$RUN_LOG" 2>&1
HOST_RC=$?
T1=$(date +%s)

echo "== [4] device, rayQuery support, seed and validation evidence ==" | tee -a "$RUN_LOG"
DEVICE_LINE="$(grep -m1 '^device:' "$RUN_LOG" || true)"
[ -n "$DEVICE_LINE" ] || { emit "runtime-nonzero" ',"which":"no device line"'; echo "FAIL: host printed no device line (exit $HOST_RC)"; exit 15; }
if echo "$DEVICE_LINE" | grep -qiE 'llvmpipe|lavapipe|swiftshader|software|type 4\)'; then
  emit "runtime-nonzero" ',"which":"software device"'; echo "FAIL: ran on a software device, not the GPU: $DEVICE_LINE"; exit 15
fi
grep -q '^rayQuery: supported' "$RUN_LOG" || { emit "runtime-nonzero" ',"which":"rayQuery unsupported"'; echo "FAIL: device does not report rayQuery support"; exit 15; }
grep -q "^seed $SEED32:" "$RUN_LOG" || { emit "runtime-nonzero" ',"which":"seed not echoed"'; echo "FAIL: host did not echo the injected seed"; exit 15; }
if grep -Eq "VUID|Validation Error|VK_ERROR" "$RUN_LOG"; then
  emit "validation-layer" ",\"vuid_count\":$(grep -cE 'VUID' "$RUN_LOG" || echo 0)"; echo "FAIL: validation layer spoke (see verify/run.log)"; exit 14
fi
case "$HOST_RC" in
  0|16) ;;   # 16 = the host's own convenience verdict; the judge below decides
  14) emit "validation-layer"; echo "FAIL: host reports validation errors"; exit 14 ;;
  *)  emit "runtime-nonzero" ",\"exit\":$HOST_RC"; echo "FAIL: host exited $HOST_RC"; exit 15 ;;
esac
for f in "$HITS" "$OCC" "$FURN"; do
  [ -s "$f" ] || { emit "hash-mismatch" ",\"which\":\"missing $(basename "$f")\""; echo "FAIL: $(basename "$f") not written by this run"; exit 16; }
done

echo "== [5] independent judge (analytic references computed here, in Python) ==" | tee -a "$JUDGE_LOG"
"$PY" - "$CFG" "$HITS" "$OCC" "$FURN" <<'PY'
import csv, json, math, sys

cfg = json.load(open(sys.argv[1]))
hits = list(csv.DictReader(open(sys.argv[2], newline="")))
occ  = list(csv.DictReader(open(sys.argv[3], newline="")))
furn = list(csv.DictReader(open(sys.argv[4], newline="")))
fails = []

def dot(a, b): return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]
def cross(a, b): return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]

# --- hit-distance: analytic ray-box (unit cube), slab method -------------------
def ray_box(o, d):
    tmin, tmax = 0.0, math.inf
    for i in range(3):
        if abs(d[i]) < 1e-12:
            if o[i] < 0.0 or o[i] > 1.0: return None
        else:
            t1, t2 = (-o[i]/d[i], (1.0-o[i])/d[i])
            if t1 > t2: t1, t2 = t2, t1
            tmin = max(tmin, t1); tmax = min(tmax, t2)
            if tmin > tmax: return None
    return tmin

for r in hits:
    d = [float(r["dx"]), float(r["dy"]), float(r["dz"])]
    exp = ray_box(cfg["camera"], d)
    got = float(r["distance"])
    if exp is None:
        if got >= 0.0: fails.append(f"hit row {d}: expected miss, got t={got}")
    elif got < 0.0 or abs(got - exp) > 1e-4:
        fails.append(f"hit row {d}: measured {got} vs analytic {exp:.6f}")
if len(hits) != len(cfg["directions"]):
    fails.append(f"hit rows {len(hits)} != directions {len(cfg['directions'])}")

# --- occlusion: analytic ray-triangle, barycentric test ------------------------
def bary(o, p):
    """Barycentric (u, v) of the plane intersection, or None if off-plane/behind."""
    e1 = [p[i]-o[i] for i in range(3)]
    A, B, C = cfg["occluder"]
    e2 = [B[i]-A[i] for i in range(3)]
    e3 = [C[i]-A[i] for i in range(3)]
    h = cross(e1, e3)
    a = dot(e2, h)
    if abs(a) < 1e-12: return None
    f = 1.0/a
    s = [p[i]-A[i] for i in range(3)]
    u = f*dot(s, h)
    q = cross(s, e2)
    v = f*dot(e1, q)
    return (u, v)

NEAR = 1e-3   # probes this close to the triangle's edge lines are epsilon-convention
              # dependent and are excluded from the exact-match requirement
compared = blocked = unblocked = near = 0
for r in occ:
    p = [float(r["px"]), float(r["py"]), float(r["pz"])]
    uv = bary(cfg["light"], p)
    if uv is None:
        fails.append(f"occlusion probe {p}: ray parallel to occluder plane (exam bug)")
        continue
    u, v = uv
    margin = min(u, v, 1.0 - u - v)
    got = str(r["blocked"]).strip().lower() in ("1", "true", "yes")
    if abs(margin) < NEAR:
        near += 1
        continue
    compared += 1
    want = margin > 0.0
    if want: blocked += 1
    else: unblocked += 1
    if got != want:
        fails.append(f"occlusion probe {p}: measured {got}, analytic {want}")
if len(occ) != len(cfg["probes"]):
    fails.append(f"occlusion rows {len(occ)} != probes {len(cfg['probes'])}")
if compared < 24:
    fails.append(f"invariant: only {compared} probes far enough from the occluder edges to judge")
if blocked == 0 or unblocked == 0:
    fails.append(f"invariant: analytic blocked set degenerate ({blocked} blocked / {unblocked} lit of {compared})")

# --- closed-box furnace: L = E / (1 - rho), finite-bounce tail extrapolated -----
rho, E, B = cfg["rho"], cfg["E"], cfg["budget"]
L_analytic = E / (1.0 - rho)
tail = 1.0 - rho ** (B + 1)
worst = 0.0
for r in furn:
    raw, se, ext = float(r["radiance"]), float(r["std_error"]), float(r["radiance_infinite"])
    if se <= 0.0:
        fails.append(f"furnace {r['probe']}: non-positive standard error")
        continue
    if raw >= L_analytic:
        fails.append(f"furnace {r['probe']}: raw {raw} >= analytic limit {L_analytic:.6f} (finite bounces cannot exceed it)")
    if abs(ext - raw / tail) > 0.005 * max(raw, 1e-9) + 4.0 * se:
        fails.append(f"furnace {r['probe']}: extrapolation {ext} inconsistent with raw/tail {raw/tail:.6f}")
    dev = abs(ext - L_analytic) / L_analytic
    worst = max(worst, dev)
    if dev > 0.01 + 3.0 * se / L_analytic:
        fails.append(f"furnace {r['probe']}: L={ext:.6f} vs E/(1-rho)={L_analytic:.6f} (rel {dev:.4%} > 1% + 3 sigma)")
if len(furn) < 2:
    fails.append("invariant: expected at least two furnace probe points")

print(f"rho={rho} E={E} budget={B} | hit rows {len(hits)} | "
      f"occlusion judged {compared} (blocked {blocked}, lit {unblocked}, near-edge skipped {near}) | "
      f"worst furnace rel dev {worst:.4%}")
if fails:
    for m in fails[:20]: print("FAIL", m)
    sys.exit(16)
print("judge: all analytic checks passed")
sys.exit(0)
PY
JRC=$?
cat "$JUDGE_LOG"
case "$JRC" in
  0) ;;
  16) emit "hash-mismatch" ',"which":"judge"'; echo "FAIL: judge found measurements out of tolerance or inconsistent"; exit 16 ;;
  *)  emit "runtime-nonzero" ",\"which\":\"judge exit $JRC\""; echo "FAIL: judge could not run"; exit 15 ;;
esac

BLOCKED=$("$PY" -c 'import csv,sys; print(sum(1 for r in csv.DictReader(open(sys.argv[1],newline="")) if str(r["blocked"]).strip().lower() in ("1","true","yes")))' "$OCC")
HITROWS=$("$PY" -c 'import csv,sys; print(len(list(csv.DictReader(open(sys.argv[1],newline="")))))' "$HITS")
emit "all" ",\"hit_rows\":$HITROWS,\"blocked\":$BLOCKED,\"run_seconds\":$((T1 - T0)),\"device\":\"$(echo "$DEVICE_LINE" | sed 's/"/\\"/g' | cut -c1-160)\""
echo "PASS: s2 ray queries on $(echo "$DEVICE_LINE" | cut -d'|' -f1) — hits, occlusion and furnace all match the analytic references; validation silent; seed $SEED32"
exit 0
