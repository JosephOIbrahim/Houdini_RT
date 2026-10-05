#!/usr/bin/env bash
# verify/s1_furnace.sh — s1 trusted check: the OpenPBR white furnace on the RTX 4090 under MSVC.
#
# Called by the orchestrator as: s1_furnace.sh <SEED>   (fresh nonce per run)
# Lives in the trusted snapshot: the orchestrator overwrites the worktree's copy before every
# run. Editing it in a worktree does nothing.
#
# What a cheating implementation would have to do to pass:
#   - produce 72 rows whose albedo lands within 3 sigma + 0.001 of a CPU reference it may read,
#     with a positive standard error, under a seed it did not choose, while NOT copying more than
#     16 rows byte-for-byte (12 Lambert rows are legitimately identical), AND report a discrete
#     GPU, AND keep the validation layer silent, AND echo the seed. Faking the statistics
#     convincingly is harder than running the Monte Carlo estimator that produces them.
#
# Exit codes mirror orchestrator/verify.ts:
#   11 shader compile  12 host build/link  13 toolchain or binary missing
#   14 validation layer  15 runtime / wrong device  16 mismatch or tolerance  17 invariant

set -uo pipefail
SEED="${1:-$RANDOM$RANDOM}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/build/s1"
BUILD_LOG="$ROOT/verify/build.log"
RUN_LOG="$ROOT/verify/run.log"
JUDGE_LOG="$ROOT/verify/judge.log"
EVIDENCE="$ROOT/verify/evidence.json"
REF="$ROOT/verify/furnace_cpu_reference.csv"
CSV="$OUT/gpu_rows.csv"
mkdir -p "$OUT"
: > "$BUILD_LOG"; : > "$RUN_LOG"; : > "$JUDGE_LOG"; rm -f "$EVIDENCE" "$CSV"

emit() { printf '{"stage":"%s","seed":"%s"%s}\n' "$1" "$SEED" "${2:-}" > "$EVIDENCE"; }
winpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }

# The host parses --seed with strtoul into uint32. Reduce the 64-bit nonce so it survives.
SEED32=$(( SEED % 4294967291 ))
[ "$SEED32" -gt 0 ] || SEED32=1

echo "== [0] prerequisites ==" | tee -a "$BUILD_LOG"
[ -f "$REF" ] || { emit "binary-missing" ',"which":"furnace_cpu_reference.csv"'; echo "FAIL: CPU reference table missing from verify/"; exit 13; }
[ -n "${OPENPBR:-}" ] && [ -f "$OPENPBR/openpbr.h" ] || { emit "binary-missing" ',"which":"OPENPBR"'; echo "FAIL: OPENPBR must point at a checkout of adobe/openpbr-bsdf"; exit 13; }
[ -n "${VULKAN_SDK:-}" ] || { emit "binary-missing" ',"which":"VULKAN_SDK"'; echo "FAIL: VULKAN_SDK not set"; exit 13; }
PY=python3; command -v python3 >/dev/null 2>&1 || PY=python
command -v "$PY" >/dev/null 2>&1 || { emit "binary-missing" ',"which":"python"'; echo "FAIL: python not found"; exit 13; }

echo "== [1] build (MSVC via verify/s1_build.bat) ==" | tee -a "$BUILD_LOG"
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    cmd.exe //c "\"$(winpath "$ROOT/verify/s1_build.bat")\" \"$(winpath "$ROOT")\" \"$(winpath "$OPENPBR")\"" >>"$BUILD_LOG" 2>&1
    RC=$? ;;
  *)
    # Non-Windows fallback for the cloud test bench (correctness only, never the real exam target).
    glslangValidator -V --target-env vulkan1.2 -S comp -DFURNACE_TEX_LUTS -I"$OPENPBR" "$ROOT/src/furnace/furnace.comp" -o "$OUT/furnace_tex.spv" >>"$BUILD_LOG" 2>&1 || { RC=11; }
    [ "${RC:-0}" -eq 0 ] && { g++ -std=c++17 -O2 -I"$OPENPBR" "$ROOT/src/furnace/furnace_host.cpp" -o "$OUT/furnace_host" -lvulkan >>"$BUILD_LOG" 2>&1 || RC=12; }
    RC=${RC:-0} ;;
esac
case "$RC" in
  0) ;;
  11) emit "cmake-configure" ',"which":"glslangValidator"'; echo "FAIL: shader compile (see verify/build.log)"; exit 11 ;;
  12) emit "build-link"; echo "FAIL: host compile/link (see verify/build.log)"; exit 12 ;;
  *)  emit "binary-missing" ",\"which\":\"s1_build.bat rc $RC\""; echo "FAIL: build helper exited $RC (toolchain?)"; exit 13 ;;
esac

echo "== [2] locate binary ==" | tee -a "$RUN_LOG"
BIN="$OUT/furnace_host.exe"; [ -x "$BIN" ] || BIN="$OUT/furnace_host"
[ -x "$BIN" ] || { emit "binary-missing" ',"which":"furnace_host"'; echo "FAIL: furnace_host not built"; exit 13; }
[ -f "$OUT/furnace_tex.spv" ] || { emit "binary-missing" ',"which":"furnace_tex.spv"'; echo "FAIL: SPIR-V missing"; exit 13; }

echo "== [3] run furnace on the GPU (seed=$SEED32, validation on) ==" | tee -a "$RUN_LOG"
T0=$(date +%s)
"$BIN" --spv "$(winpath "$OUT/furnace_tex.spv")" --tex-luts --seed "$SEED32" \
       --csv "$(winpath "$CSV")" --ref "$(winpath "$REF")" >>"$RUN_LOG" 2>&1
HOST_RC=$?
T1=$(date +%s)

echo "== [4] device, seed and validation evidence ==" | tee -a "$RUN_LOG"
DEVICE_LINE="$(grep -m1 '^device:' "$RUN_LOG" || true)"
[ -n "$DEVICE_LINE" ] || { emit "runtime-nonzero" ',"which":"no device line"'; echo "FAIL: host printed no device line (exit $HOST_RC)"; exit 15; }
if echo "$DEVICE_LINE" | grep -qiE 'llvmpipe|lavapipe|swiftshader|software|type 4\)'; then
  emit "runtime-nonzero" ',"which":"software device"'; echo "FAIL: ran on a software device, not the GPU: $DEVICE_LINE"; exit 15
fi
grep -q "^seed $SEED32:" "$RUN_LOG" || { emit "runtime-nonzero" ',"which":"seed not echoed"'; echo "FAIL: host did not echo the injected seed"; exit 15; }
if grep -Eq "VUID|Validation Error|VK_ERROR|leak" "$RUN_LOG"; then
  emit "validation-layer" ",\"vuid_count\":$(grep -cE 'VUID' "$RUN_LOG" || echo 0)"; echo "FAIL: validation layer spoke (see verify/run.log)"; exit 14
fi
case "$HOST_RC" in
  0|16) ;;   # 16 = the host's own convenience verdict; the judge below decides
  14) emit "validation-layer"; echo "FAIL: host reports validation errors"; exit 14 ;;
  *)  emit "runtime-nonzero" ",\"exit\":$HOST_RC"; echo "FAIL: host exited $HOST_RC"; exit 15 ;;
esac
[ -s "$CSV" ] || { emit "hash-mismatch" ',"which":"no CSV produced"'; echo "FAIL: no per-row CSV written by this run"; exit 16; }

echo "== [5] copy guard: rows byte-identical to the reference ==" | tee -a "$JUDGE_LOG"
IDENTICAL="$("$PY" - "$CSV" "$REF" <<'PY'
import csv, sys
k = lambda r: (r["config"], f'{float(r["roughness"]):.2f}', f'{float(r["cos_view"]):.2f}')
gpu = {k(r): r for r in csv.DictReader(open(sys.argv[1], newline=""))}
ref = {k(r): r for r in csv.DictReader(open(sys.argv[2], newline=""))}
same = sum(1 for key, r in gpu.items() if key in ref and r["albedo"] == ref[key]["albedo"] and r["std_error"] == ref[key]["std_error"])
print(same)
PY
)" || IDENTICAL=99
echo "identical rows: $IDENTICAL" | tee -a "$JUDGE_LOG"
[ "$IDENTICAL" -le 16 ] || { emit "hash-mismatch" ",\"identical_rows\":$IDENTICAL"; echo "FAIL: $IDENTICAL rows byte-identical to the reference (copied, not computed)"; exit 16; }

echo "== [6] independent judge ==" | tee -a "$JUDGE_LOG"
"$PY" "$ROOT/verify/judge_furnace.py" "$CSV" "$REF" >>"$JUDGE_LOG" 2>&1
JRC=$?
cat "$JUDGE_LOG"
case "$JRC" in
  0) ;;
  16) emit "hash-mismatch" ',"which":"judge"'; echo "FAIL: judge found rows out of tolerance"; exit 16 ;;
  *)  emit "runtime-nonzero" ",\"which\":\"judge exit $JRC\""; echo "FAIL: judge could not run"; exit 15 ;;
esac

ROWS="$(($(wc -l < "$CSV") - 1))"
emit "all" ",\"rows\":$ROWS,\"identical_rows\":$IDENTICAL,\"run_seconds\":$((T1 - T0)),\"device\":\"$(echo "$DEVICE_LINE" | sed 's/"/\\"/g' | cut -c1-160)\""
echo "PASS: s1 furnace on $(echo "$DEVICE_LINE" | cut -d'|' -f1) — $ROWS rows, judge exit 0, validation silent, seed $SEED32"
exit 0
