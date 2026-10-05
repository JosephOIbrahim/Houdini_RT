#!/usr/bin/env python3
"""Independent judge for the GPU furnace. The program under test never grades itself.

Reads the per-row CSV written by `furnace_host --csv` and the CPU reference table written by
`white_furnace`, and applies the tolerances. Put this file (and the CPU reference) under the
harness's trusted verify/ directory so the code under test cannot edit its own exam.

    python judge_furnace.py gpu_rows.csv furnace_cpu_reference.csv

Exit codes follow the FRAGMENT stage map: 0 pass, 16 a row is out of tolerance, 15 bad input.

Tolerances, per row:
  energy      |gpu - expected| <= model_tolerance + 3 * gpu_std_error
                white rows 0.5%; coat rows with cosV < 0.2 allowed 3% (documented coat trade);
                lambert rows 0.1%; control rows are not judged on energy
  agreement   |gpu - cpu| <= 3 * sqrt(gpu_se^2 + cpu_se^2) + 0.001
  MIS safety  mean relative |weight - eval/pdf| < 1e-3 and mean relative pdf mismatch < 1e-3
"""
import csv
import math
import sys


def key(row):
    return (row["config"], f'{float(row["roughness"]):.2f}', f'{float(row["cos_view"]):.2f}')


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 15
    try:
        with open(sys.argv[1], newline="") as f:
            gpu_rows = list(csv.DictReader(f))
        with open(sys.argv[2], newline="") as f:
            cpu = {key(r): r for r in csv.DictReader(f)}
    except OSError as e:
        print(f"cannot read input: {e}")
        return 15
    if not gpu_rows:
        print("no GPU rows")
        return 15

    failures = 0
    seen = set()
    worst = 0.0
    for r in gpu_rows:
        k = key(r)
        seen.add(k)
        albedo, se = float(r["albedo"]), float(r["std_error"])
        reasons = []

        kind = r["kind"]
        if kind == "white":
            coat_grazing = float(r["coat"]) > 0.0 and float(r["cos_view"]) < 0.2
            expected, model_tol = 1.0, (0.03 if coat_grazing else 0.005)
        elif kind == "lambert":
            expected, model_tol = float(r["base_gray"]), 0.001
        else:
            expected, model_tol = None, 0.0
        if expected is not None and abs(albedo - expected) > model_tol + 3.0 * se:
            reasons.append(f"energy {albedo:.4f} vs {expected:.4f}")

        if float(r["weight_vs_eval"]) >= 1e-3 or float(r["pdf_mismatch"]) >= 1e-3:
            reasons.append("sample/eval/pdf disagree")

        ref = cpu.get(k)
        if ref is None:
            reasons.append("row missing from the CPU reference")
        else:
            diff = abs(albedo - float(ref["albedo"]))
            worst = max(worst, diff)
            if diff > 3.0 * math.sqrt(se * se + float(ref["std_error"]) ** 2) + 0.001:
                reasons.append(f"gpu {albedo:.4f} vs cpu {float(ref['albedo']):.4f}")

        if reasons:
            failures += 1
            print(f"FAIL {k[0]:<26} rough {k[1]} cosV {k[2]}: " + "; ".join(reasons))

    missing = set(cpu) - seen
    if missing:
        failures += len(missing)
        print(f"FAIL {len(missing)} reference rows were not produced by the GPU run")

    print(f"{len(gpu_rows)} GPU rows judged, {failures} failed. Largest |gpu - cpu| = {worst:.4f}")
    return 0 if failures == 0 else 16


if __name__ == "__main__":
    sys.exit(main())
