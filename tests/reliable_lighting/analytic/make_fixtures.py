"""Freeze supplementary fixtures, independent oracles and tolerances BEFORE GPU output."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import numpy as np
from oracle import expected, light_area, radiance, self_check

HERE = Path(__file__).resolve().parent
SEEDS = [479123, 1299721, 753649]
CASES = []


def emitter(kind, **values):
    light = dict(type=kind, position=[0, 0, 3], normal=[0, 0, -1], color=[.9, .6, .3], intensity=1, exposure=0, normalize=False)
    if kind in ("rect", "disk"):
        light.update(u=[.7, 0, 0], v=[0, .45 if kind == "rect" else .7, 0])
    if kind == "sphere": light["radius"] = .55
    if kind == "distant": light["angle"] = 0
    light.update(values)
    return light


def add(name, lights, **values):
    case = dict(name=name, width=24, height=24, extent=1.2, albedo=[.45, .3, .18], normal=[0, 0, 1], dome=[0, 0, 0], lights=deepcopy(lights), spp=2048, seeds=SEEDS, bounces=0, blocker=None, tolerance="stochastic")
    case.update(values)
    CASES.append(case)


add("dark", [], tolerance="exact")
add("dome_control", [], dome=[.7, 1.1, .4], tolerance="exact")
add("distant_delta", [emitter("distant")], tolerance="exact")
add("distant_oblique", [emitter("distant", normal=[-.6, 0, -.8])], tolerance="exact")
add("distant_translation", [emitter("distant", position=[12, -53, 72])], tolerance="exact")
add("distant_below_receiver", [emitter("distant", normal=[0, 0, 1])], tolerance="exact")
add("distant_double", [emitter("distant", intensity=2)], tolerance="exact")
add("distant_exposure", [emitter("distant", exposure=1)], tolerance="exact")
add("distant_tilted_normal", [emitter("distant")], normal=[.6, 0, .8], tolerance="exact")
for angle in (12, 60):
    add("distant_cone_" + str(angle), [emitter("distant", angle=angle)])
    add("distant_normalized_" + str(angle), [emitter("distant", angle=angle, normalize=True)])
for kind in ("rect", "disk", "sphere"):
    add(kind, [emitter(kind)])
    add(kind + "_normalized", [emitter(kind, normalize=True)])
    add(kind + "_moved", [emitter(kind, position=[.6, -.35, 2.2])])
    if kind != "sphere":
        add(kind + "_backface", [emitter(kind, normal=[0, 0, 1])], tolerance="exact")
        add(kind + "_scaled_normalized", [emitter(kind, u=[1.05, 0, 0], v=[0, .3, 0], normalize=True)])
    else:
        add(kind + "_scaled_normalized", [emitter(kind, radius=.8, normalize=True)])
add("rect_tilted", [emitter("rect", u=[.56, 0, -.42], normal=[-.6, 0, -.8])])
add("disk_tilted", [emitter("disk", u=[.56, 0, -.42], normal=[-.6, 0, -.8])])
add("rect_small", [emitter("rect", u=[.025, 0, 0], v=[0, .025, 0], intensity=100)])
add("rect_double", [emitter("rect", intensity=2)])
add("rect_exposure", [emitter("rect", exposure=1)])
mix = [emitter("rect", position=[-.7, 0, 3], color=[1, .1, .05], intensity=3), emitter("disk", position=[.8, .2, 2.7], color=[.1, 1, .2], intensity=.7), emitter("sphere", position=[0, -.4, 2.4], color=[.1, .2, 1], intensity=1.2), emitter("distant", color=[.2, .3, .1], intensity=.2)]
add("mixed", mix)
add("mixed_reordered", list(reversed(mix)))
add("mixed_dome", mix, dome=[.17, .11, .23])
add("light_removed", mix[:1])
blocker = dict(x=0, half_y=10, z0=.3, z1=1.5)
add("distant_shadow", [emitter("distant", normal=[.6, 0, -.8])], blocker=blocker, tolerance="shadow")
for kind in ("rect", "disk"):
    add(kind + "_shadow", [emitter(kind, position=[-1.0, 0, 3])], blocker=blocker, tolerance="shadow")
add("blocker_behind_light", [emitter("rect", position=[-1, 0, 3])], blocker=dict(x=-2, half_y=10, z0=4, z1=10), tolerance="stochastic")


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def cpp_float(value):
    value = format(float(value), ".9g")
    if "." not in value and "e" not in value: value += ".0"
    return value + "f"


def arr(values): return "{" + ",".join(cpp_float(v) for v in values) + "}"


def generate_header():
    lines = ["// Generated only from frozen semantic fixtures; does not contain oracle output.", "std::vector<Case> cases() {", "std::vector<Case> out;"]
    for case in CASES:
        lines += ["{ Case c;", 'c.name="' + case["name"] + '";', f'c.width={case["width"]}; c.height={case["height"]}; c.spp={case["spp"]};', "c.extent=" + cpp_float(case["extent"]) + ";", "c.albedo=" + arr(case["albedo"]) + ";", "c.normal=" + arr(case["normal"]) + ";", "c.dome=" + arr(case["dome"]) + ";", "c.seeds={" + ",".join(map(str, case["seeds"])) + "};"]
        if case["blocker"]:
            b = case["blocker"]
            lines += ["c.blocker=true; c.block=" + arr([b["x"], b["half_y"], b["z0"], b["z1"]]) + ";"]
        for light in case["lights"]:
            kind = {"rect": 0, "disk": 1, "sphere": 2, "distant": 3}[light["type"]]
            u = light.get("u", [0, 0, 0]); v = light.get("v", [0, 0, 0])
            fields = [light["position"] + [kind], u + [light_area(light)], v + [math.cos(math.radians(light.get("angle", 0))/2)], light["normal"] + [light.get("radius", 0)], radiance(light).tolist() + [0], [0, 0, 0, 0]]
            lines += ["c.lights.push_back(HdRTVkLight{" + ",".join(arr(f) for f in fields) + "});"]
        lines += ["out.push_back(c); }"]
    return "\n".join(lines + ["return out;", "}", ""])


def main():
    frozen = HERE / "frozen"
    frozen.mkdir(exist_ok=False)
    contract = {
        "schema": 2, "created_utc": datetime.now(timezone.utc).isoformat(),
        "oracle_revision": "v2: finite emitters occlude one another and replace dome over their projected solid angles; v1 incorrectly assumed transparent emitter geometry in mixed fixtures",
        "previous_freeze_sha256": sha(HERE.parent / "analytic/frozen/freeze.json"),
        "previous_candidate_diagnostic": "../runs/20261117T121449Z-analytic-dca454cf; v1 failure preserved without alteration",
        "scope": "Supplementary standalone GPU transport; NOT a sealed exam or proof of Hydra/UsdLux translation",
        "samples_per_case": 2048 * len(SEEDS),
        "camera": {"orthographic": True, "eye": [0, 0, 1], "near": .1, "far": 10, "ray_origin_z": .9},
        "bounces": "0 permits primary-surface direct light and one escape; no indirect surface contribution",
        "tolerances": {
            "exact": {"rgb_max_abs": 3e-5},
            "stochastic": {"pooled_channel_mean_relative": .01, "pooled_rgb_rmse_over_reference_rms": .04},
            "shadow": {"pooled_channel_mean_relative": .025, "pooled_rgb_rmse_over_reference_rms": .06},
            "alpha_max_abs": 1e-6, "guides_normal_max_abs": 2e-5, "guides_albedo_max_abs": 2e-6,
            "guides_depth_distance_max_abs": 2e-5,
            "oracle_coarse_fine_rmse_over_reference_rms": {"stochastic": .0001, "shadow": .02},
            "oracle_multi_emitter_visibility_relative_rmse": .01,
            "oracle_multi_emitter_visibility_relative_mean": .002,
            "live_fresh_max_abs": 0,
        },
        "caveats": ["GPU inputs are already normalized radiance: normalization/temperature/interpolation translation require independent USD/husk tests", "Camera is below emitters, so primary analytic light visibility is not exercised", "No universal variance superiority claim", "Sphere receiver directions stay wholly above horizon", "Ray-origin epsilon may introduce sub-tolerance energy difference"],
        "self_check": self_check(), "cases": CASES,
    }
    (frozen / "contract.json").write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
    (frozen / "fixtures.hpp").write_text(generate_header(), encoding="utf-8")
    comparisons = []
    for case in CASES:
        print(case["name"], flush=True)
        mixed = sum(light["type"] != "distant" for light in case["lights"]) > 1
        coarse = expected(case, order=48 if mixed else 24)
        fine = expected(case, order=96 if mixed else 64)
        denom = max(float(np.sqrt(np.mean(fine[..., :3] ** 2))), 1e-12)
        delta = float(np.sqrt(np.mean((coarse[..., :3]-fine[..., :3]) ** 2))) / denom
        limit = contract["tolerances"]["oracle_coarse_fine_rmse_over_reference_rms"].get(case["tolerance"], .0001)
        if mixed:
            limit = contract["tolerances"]["oracle_multi_emitter_visibility_relative_rmse"]
            mean_delta = float(np.max(np.abs(coarse[..., :3].mean(axis=(0,1)) / fine[..., :3].mean(axis=(0,1)) - 1)))
            if mean_delta > contract["tolerances"]["oracle_multi_emitter_visibility_relative_mean"]:
                raise AssertionError((case["name"], "oracle visibility mean did not converge", mean_delta))
        if delta > limit:
            raise AssertionError((case["name"], "oracle did not converge", delta, limit))
        np.save(frozen / (case["name"] + ".npy"), fine)
        comparisons.append(dict(case=case["name"], relative_rmse=delta, limit=limit, relative_mean_delta=mean_delta if mixed else None))
    (frozen / "oracle_convergence.json").write_text(json.dumps(comparisons, indent=2) + "\n", encoding="utf-8")
    files = sorted(p for p in frozen.iterdir() if p.is_file())
    seal = dict(created_utc=datetime.now(timezone.utc).isoformat(), candidate_rendered=False,
                inputs={str(p.relative_to(HERE)): sha(p) for p in files + [HERE / "oracle.py", HERE / "make_fixtures.py"]})
    (frozen / "freeze.json").write_text(json.dumps(seal, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(status="frozen", case_count=len(CASES), frozen=str(frozen), self_check=contract["self_check"])), flush=True)


if __name__ == "__main__": main()
