"""Independent artifact judge; no rendering, production imports, or candidate reference images."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
GUIDE = np.dtype([("normal_depth", "<f4", (4,)), ("eye_distance", "<f4", (4,)), ("ids", "<i4", (4,)), ("albedo", "<f4", (4,))])


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("run", type=Path); args = parser.parse_args()
    out = args.run.resolve(); frozen = HERE / "frozen"
    contract = json.loads((frozen / "contract.json").read_text()); seal = json.loads((frozen / "freeze.json").read_text())
    statistical = json.loads((frozen / "statistical_contract.json").read_text())
    convergence = {row["case"]: row for row in json.loads((frozen / "oracle_convergence.json").read_text())}
    failures = []; evidence = []; paths = {frozen / "freeze.json", Path(__file__).resolve()}
    def require(okay, message):
        if not okay: failures.append(message)
    for relative, expected_hash in seal["inputs"].items():
        path = HERE / relative; paths.add(path)
        require(path.exists() and sha(path) == expected_hash, "frozen input changed: " + relative)
    if failures: raise RuntimeError(failures)
    with (out / "stats.csv").open(newline="") as source: stats = list(csv.DictReader(source))
    require(len(stats) == sum(len(c["seeds"]) for c in contract["cases"]), "unexpected rendered case count")
    indexed = {(r["case"], int(r["seed"])): r for r in stats}
    require(len(indexed) == len(stats), "duplicate case/seed statistics")
    paths.add(out / "stats.csv"); paths.add(out / "teardown.txt"); paths.add(out / "run.log")
    require((out / "teardown.txt").read_text().strip() == "errors=0 warnings=0", "validation messages during teardown")
    log = (out / "run.log").read_text(errors="replace")
    require("validation=active" in log and "device=" in log, "missing real validation/device evidence")
    require("VUID-" not in log and "Validation Error" not in log, "validation reported in raw log")
    tol = contract["tolerances"]
    fresh_count = 0
    for case in contract["cases"]:
        name = case["name"]; w, h = case["width"], case["height"]
        oracle_path = frozen / (name + ".npy"); paths.add(oracle_path); target = np.load(oracle_path)
        images = []; max_guide_error = 0.0
        for seed in case["seeds"]:
            stem = name + "_" + str(seed); image_path = out / (stem + ".rgba32f"); guide_path = out / (stem + ".guides")
            paths.update((image_path, guide_path))
            image = np.fromfile(image_path, dtype="<f4").reshape(h, w, 4).astype(np.float64)
            require(bool(np.isfinite(image).all()), stem + ": nonfinite RGBA")
            require(float(np.max(np.abs(image[..., 3] - 1))) <= tol["alpha_max_abs"], stem + ": coverage mismatch")
            images.append(image)
            row = indexed.get((name, seed), {})
            for key, value in (("spp", case["spp"]), ("width", w), ("height", h), ("traced_samples", case["spp"]), ("trace_submits", math.ceil(case["spp"]/32)), ("resolve_submits", 1), ("readback_count", 2), ("errors", 0), ("warnings", 0)):
                require(int(row.get(key, -999)) == value, stem + ": unexpected " + key)
            require(int(row.get("readback_bytes", 0)) == w*h*(16+64), stem + ": beauty/guide transfer accounting mismatch")
            guides = np.fromfile(guide_path, dtype=GUIDE).reshape(h, w)
            for field in ("normal_depth", "eye_distance", "albedo"):
                require(bool(np.isfinite(guides[field]).all()), stem + ": nonfinite " + field)
            n = np.asarray(case["normal"], dtype=np.float64); n /= np.linalg.norm(n)
            for field in ("normal_depth", "eye_distance"):
                error = float(np.max(np.abs(guides[field][..., :3] - n))); max_guide_error = max(max_guide_error, error)
                require(error <= tol["guides_normal_max_abs"], stem + ": incorrect " + field)
            near = contract["camera"]["near"]; far = contract["camera"]["far"]; eye = contract["camera"]["eye"][2]
            require(float(np.max(np.abs(guides["normal_depth"][..., 3] - (eye-near)/(far-near)))) <= tol["guides_depth_distance_max_abs"], stem + ": projected depth mismatch")
            require(float(np.max(np.abs(guides["eye_distance"][..., 3] - contract["camera"]["ray_origin_z"]))) <= tol["guides_depth_distance_max_abs"], stem + ": ray distance mismatch")
            require(np.array_equal(guides["ids"], np.broadcast_to([17, 41, -1, 1], (h, w, 4))), stem + ": primitive/authored-face/instance/coverage IDs mismatch")
            require(float(np.max(np.abs(guides["albedo"][..., :3] - case["albedo"]))) <= tol["guides_albedo_max_abs"], stem + ": albedo mismatch")
            fresh = out / (stem + ".fresh.rgba32f")
            if fresh.exists():
                paths.add(fresh); fresh_count += 1
                alternate = np.fromfile(fresh, dtype="<f4").reshape(h, w, 4)
                require(float(np.max(np.abs(image-alternate))) <= tol["live_fresh_max_abs"], stem + ": live/fresh differs")
                fresh_guide = out / (stem + ".fresh.guides"); paths.add(fresh_guide)
                require(guide_path.read_bytes() == fresh_guide.read_bytes(), stem + ": live/fresh guide differs")
        pooled = np.mean(images, axis=0)
        reference = target[..., :3]; actual = pooled[..., :3]
        mean_reference = reference.mean(axis=(0, 1)); mean_actual = actual.mean(axis=(0, 1))
        relative_mean = np.abs(mean_actual - mean_reference) / np.maximum(abs(mean_reference), 1e-9)
        rmse = float(np.sqrt(np.mean((actual-reference)**2)))
        nrmse = rmse / max(float(np.sqrt(np.mean(reference**2))), 1e-12)
        maximum = float(np.max(np.abs(actual-reference)))
        stack = np.stack([im[..., :3] for im in images])
        # Different seed streams are independent. Sum per-pixel variance for the
        # image mean; divide by pixel-count squared and number of seed images.
        pixel_variance = np.var(stack, axis=0, ddof=1)
        mean_se = np.sqrt(np.sum(pixel_variance, axis=(0,1)) / ((w*h)**2 * len(images)))
        coarse = convergence[name]
        if coarse.get("relative_mean_delta") is not None:
            oracle_mean_bound = coarse["relative_mean_delta"] * np.abs(mean_reference)
        else:
            # Cauchy-Schwarz bound: absolute channel mean error <= channel RMSE
            # <= sqrt(3)*RGB RMSE. No unmeasured confidence claim for quadrature.
            oracle_mean_bound = np.full(3, math.sqrt(3) * coarse["relative_rmse"] * float(np.sqrt(np.mean(reference**2))))
        numerical_floor = statistical["base_relative_numerical_floor"] * np.abs(mean_reference) + statistical["oracle_error_safety_multiplier"] * oracle_mean_bound
        statistical_bound = statistical["standard_error_multiplier"] * mean_se + numerical_floor
        limits = tol[case["tolerance"]]
        if case["tolerance"] == "exact":
            # Check each independent sample set, not only errors cancelled by pooling.
            require(all(float(np.max(np.abs(im[..., :3]-reference))) <= limits["rgb_max_abs"] for im in images), name + ": deterministic/zero energy mismatch")
        else:
            require(float(np.max(relative_mean)) <= limits["pooled_channel_mean_relative"], name + ": biased mean energy")
            require(nrmse <= limits["pooled_rgb_rmse_over_reference_rms"], name + ": spatial irradiance mismatch")
            require(bool(np.all(np.abs(mean_actual-mean_reference) <= statistical_bound)), name + ": mean exceeds SE plus oracle/numerical bound")
        evidence.append(dict(case=name, channel_mean_reference=mean_reference.tolist(), channel_mean_actual=mean_actual.tolist(), channel_mean_relative_error=relative_mean.tolist(), channel_mean_standard_error=mean_se.tolist(), statistical_abs_mean_error_limit=statistical_bound.tolist(), numerical_oracle_floor=numerical_floor.tolist(), estimated_95_percent_mean_interval=[[float(m-1.96*s),float(m+1.96*s)] for m,s in zip(mean_actual,mean_se)], pooled_relative_rmse=nrmse, pooled_max_abs=maximum, max_guide_normal_error=max_guide_error, thresholds=limits))
    require(fresh_count == 5, "missing live/fresh control render")
    receipt = dict(schema=2, status="passed" if not failures else "failed", scope=contract["scope"], statistical_contract=statistical, failures=failures, case_count=len(evidence), render_count=len(stats), fresh_count=fresh_count, cases=evidence,
                   inputs={str(p): sha(p) for p in sorted(paths)}, contract_sha256=sha(frozen / "contract.json"))
    (out / "judge.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: receipt[k] for k in ("status", "case_count", "render_count", "fresh_count", "failures")}, indent=2))
    raise SystemExit(0 if not failures else 16)


if __name__ == "__main__": main()
