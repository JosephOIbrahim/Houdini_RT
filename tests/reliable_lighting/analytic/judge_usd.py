"""H22 hython/OIIO judge for native USD and Karma images against frozen CPU pixels."""
import hashlib
import json
from pathlib import Path
import sys
import numpy as np
import OpenImageIO as oiio


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    source = oiio.ImageInput.open(str(path))
    if source is None: raise RuntimeError(oiio.geterror())
    spec = source.spec(); names = list(spec.channelnames)
    image = np.asarray(source.read_image("float"), dtype=np.float64).reshape(spec.height, spec.width, spec.nchannels)
    source.close()
    return image[..., [names.index(c) for c in ("R", "G", "B", "A")]], spec


def main():
    out = Path(sys.argv[1]).resolve(); manifest = json.loads((out / "fixtures/manifest.json").read_text())
    limits = manifest["tolerances"]; failures = []; evidence = []; hashes = {str(Path(__file__).resolve()): sha(Path(__file__))}
    def require(okay, detail):
        if not okay: failures.append(detail)
    groups = {}
    for case in manifest["cases"]: groups.setdefault(case["name"], []).append(case)
    convergence = {row["case"]: row for row in manifest["oracle_convergence"]}
    stats = manifest["statistics"]
    for name, seed_cases in groups.items():
        case = seed_cases[0]
        stage = Path(case["stage"]); oracle = Path(case["oracle"])
        require(sha(stage) == case["stage_sha256"], case["name"] + ": changed USD")
        require(sha(oracle) == case["oracle_sha256"], case["name"] + ": changed oracle")
        reference = np.flipud(np.load(oracle)); means = {}; standard_errors = {}; row = {"case": case["name"], "renderers": {}}
        for renderer in ("karma", "candidate"):
            images = []
            for seed_case in seed_cases:
                path = out / (case["name"] + "_" + str(seed_case["seed"]) + "." + renderer + ".exr")
                image, spec = read(path); hashes[str(path)] = sha(path)
                require(bool(np.isfinite(image).all()), str(path) + ": nonfinite")
                images.append(image)
                seed_stage = Path(seed_case["stage"])
                require(sha(seed_stage) == seed_case["stage_sha256"], str(seed_stage) + ": changed stage")
                hashes[str(seed_stage)] = sha(seed_stage)
            require(len(images) == stats["seed_count"], case["name"] + ": wrong seed count")
            image = np.mean(images, axis=0)
            require(image.shape == reference.shape, case["name"] + ": resolution mismatch")
            require(bool(np.isfinite(image).all()), case["name"] + ": nonfinite " + renderer)
            require(float(np.max(np.abs(image[..., 3] - 1))) <= limits["alpha_max_abs"], case["name"] + ": coverage " + renderer)
            expected_mean = reference[..., :3].mean(axis=(0, 1)); actual_mean = image[..., :3].mean(axis=(0, 1)); means[renderer] = actual_mean
            delta = np.abs(actual_mean / expected_mean - 1)
            nrmse = float(np.sqrt(np.mean((image[..., :3]-reference[..., :3])**2)) / np.sqrt(np.mean(reference[..., :3]**2)))
            pixels = image.shape[0] * image.shape[1]
            variance = np.var(np.stack(images)[..., :3], axis=0, ddof=1)
            se = np.sqrt(np.sum(variance, axis=(0,1)) / (pixels*pixels*len(images))); standard_errors[renderer] = se
            conv = convergence[name]
            if conv.get("relative_mean_delta") is not None: oracle_bound = conv["relative_mean_delta"] * abs(expected_mean)
            else: oracle_bound = np.full(3, np.sqrt(3) * conv["relative_rmse"] * np.sqrt(np.mean(reference[..., :3]**2)))
            floor = stats["base_relative_floor"] * abs(expected_mean) + stats["quadrature_safety_multiplier"] * oracle_bound
            statistical_bound = stats["SE_multiplier"] * se + floor
            require(bool(np.all(abs(actual_mean-expected_mean) <= statistical_bound)), case["name"] + ": analytic SE/numerical bound " + renderer)
            require(float(np.max(delta)) <= limits["each_renderer_analytic_channel_mean_relative"], case["name"] + ": analytic energy " + renderer)
            require(nrmse <= limits["each_renderer_analytic_rgb_relative_rmse"], case["name"] + ": analytic spatial image " + renderer)
            row["renderers"][renderer] = dict(mean=actual_mean.tolist(), standard_error=se.tolist(), statistical_abs_error_limit=statistical_bound.tolist(), numerical_oracle_floor=floor.tolist(), reference_mean=expected_mean.tolist(), relative_mean_error=delta.tolist(), relative_rmse=nrmse)
        ratio = np.abs(means["candidate"] / means["karma"] - 1)
        require(float(np.max(ratio)) <= limits["candidate_karma_channel_mean_relative"], case["name"] + ": candidate/Karma disagreement")
        difference_se = np.sqrt(standard_errors["candidate"]**2 + standard_errors["karma"]**2)
        difference_bound = stats["SE_multiplier"] * difference_se + stats["candidate_karma_relative_floor"] * abs(expected_mean)
        require(bool(np.all(abs(means["candidate"] - means["karma"]) <= difference_bound)), case["name"] + ": candidate/Karma statistical bound")
        row["candidate_karma_mean_difference_SE"] = difference_se.tolist(); row["candidate_karma_abs_error_limit"] = difference_bound.tolist()
        row["candidate_karma_relative_mean_error"] = ratio.tolist(); evidence.append(row)
        hashes.update({str(stage): sha(stage), str(oracle): sha(oracle)})
    receipt = dict(status="passed" if not failures else "failed", scope=manifest["scope"], failures=failures, cases=evidence, thresholds=limits, inputs=hashes, manifest_sha256=sha(out / "fixtures/manifest.json"))
    (out / "judge.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(status=receipt["status"], case_count=len(evidence), failures=failures), indent=2))
    return 0 if not failures else 16


if __name__ == "__main__": raise SystemExit(main())
