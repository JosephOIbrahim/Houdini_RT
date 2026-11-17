"""Independent closed-form plane/sphere intersections; never imports renderer code."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import struct

WIDTH = HEIGHT = 32
GUIDE = struct.Struct("<8f4i4f")
CASES = ("hard_seam", "normal_update", "nonuniform", "reflected", "perspective",
         "rotated_perspective", "rotated_ortho", "recovered", "rect_only",
         "disk_only", "sphere_only", "zero_emitter", "diffuse_zero", "empty")
MISS = (-1., -1., -1., 1., -1., -1., -1., 0., -1, -1, -1, 0, 0., 0., 0., 0.)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def unit(vector):
    size = math.sqrt(sum(x*x for x in vector))
    return tuple(x/size for x in vector)


def expected_guide(case, x, y):
    """Physical camera at distance 3, near .1/far 10; row 0 is image bottom."""
    u, v = 2*(x+.5)/WIDTH-1, 2*(y+.5)/HEIGHT-1
    depth, distance = 2.9/9.9, 2.9
    if case in CASES[:8]:
        face, normal = 71, (.6, 0., .8)
        if case in ("hard_seam", "normal_update"):
            face = 101 if v < u else 303
            tilt = .6 if v < u else -.6
            normal = (tilt, 0., .8) if case == "hard_seam" else (0., tilt, .8)
        elif case in ("nonuniform", "reflected"):
            # Covectors transform by inverse transpose, never by the position matrix.
            normal = unit((.5 if case == "nonuniform" else -.5, 2., 1.))
        eye = normal
        if case in ("rotated_perspective", "rotated_ortho", "recovered"):
            normal = (.8, 0., -.6)
        if case in ("perspective", "rotated_perspective"):
            depth = 10/9.9 - 1/(9.9*3)
            distance = 3*math.sqrt(1+u*u+v*v)
        return (*normal, depth, *eye, distance, 20000001, face, -1, 1, .2, .4, .6, 1.)
    if case == "empty":
        return MISS
    px, py = 2*u, 2*v
    disk = case in ("disk_only", "sphere_only")
    covered = px*px+py*py < .75**2 if disk else abs(px)<.75 and abs(py)<.5
    if not covered:
        return MISS
    normal = (0., 0., 1.)
    if case == "sphere_only":
        z = math.sqrt(.75**2-px*px-py*py)
        normal = (px/.75, py/.75, z/.75)
        depth, distance = (2.9-z)/9.9, 2.9-z
    return (*normal, depth, *normal, distance, -1, -1, -1, 1, 0., 0., 0., 0.)


def check_beauty(case, x, y, rgba):
    if case in CASES[:8]:
        target = (0., 0., 0., 1.)  # no illumination, fully covered planes
    elif case == "empty":
        target = (0., 0., 0., 0.)
    else:
        energy = (0., 0., 0.) if case == "zero_emitter" else (.25, .5, .75)
        assert -.000001 <= rgba[3] <= 1.000001, (case, "invalid alpha", rgba)
        for actual, radiance in zip(rgba[:3], energy):
            assert abs(actual-radiance*rgba[3]) < 2e-6, (case, "emission", rgba)
        px, py = 4*(x+.5)/WIDTH-2, 4*(y+.5)/HEIGHT-2
        margin = .75-math.hypot(px, py) if case in ("disk_only", "sphere_only") else min(.75-abs(px), .5-abs(py))
        # Exclude only pixel footprints crossing a silhouette: no RNG model needed.
        if abs(margin) <= math.sqrt(2)*2/WIDTH:
            return
        alpha = 1. if margin > 0 else 0.
        target = (*(value*alpha for value in energy), alpha)
    assert max(abs(a-b) for a, b in zip(rgba, target)) < 2e-6, (case, x, y, rgba, target)


def judge(run):
    manifest = json.loads((run/"manifest.json").read_text())
    for name, expected in manifest["sha256"].items():
        assert digest(run/name) == expected, ("frozen input changed", name)
    raw = run/"raw"
    result = {"status": "passed", "cases": {}, "inputs": manifest["sha256"]}
    for case in CASES:
        packed = (raw/(case+".guides")).read_bytes()
        beauty = (raw/(case+".rgba32f")).read_bytes()
        assert len(packed) == WIDTH*HEIGHT*64 and len(beauty) == WIDTH*HEIGHT*16, case
        maximum, checked = 0., 0
        for y in range(HEIGHT):
            for x in range(WIDTH):
                index = y*WIDTH+x
                actual = GUIDE.unpack_from(packed, index*64)
                rgba = struct.unpack_from("<4f", beauty, index*16)
                assert all(math.isfinite(n) for n in (*actual, *rgba)), (case, "nonfinite")
                check_beauty(case, x, y, rgba)
                # Shared-edge primitive ownership is implementation-defined at exact ties.
                if x == y and case in ("hard_seam", "normal_update"):
                    continue
                expected = expected_guide(case, x, y)
                assert actual[8:12] == expected[8:12], (case, x, y, "IDs", actual[8:12], expected[8:12])
                error = max(abs(actual[i]-expected[i]) for i in (*range(8), *range(12,16)))
                maximum = max(maximum, error)
                assert error < 4e-5, (case, x, y, "guide mismatch", error, actual, expected)
                checked += 1
        result["cases"][case] = {"guide_pixels_checked": checked, "max_absolute_error": maximum,
                                  "guide_sha256": digest(raw/(case+".guides")),
                                  "beauty_sha256": digest(raw/(case+".rgba32f"))}
    with (raw/"stats.csv").open(newline="") as stream:
        stats = list(csv.DictReader(stream))
    assert [row["case"] for row in stats] == list(CASES), "missing/duplicate/reordered cases"
    for row in stats:
        assert row["errors"] == row["warnings"] == "0", row
        assert int(row["samples"]) == (0 if row["case"] == "empty" else 32), row
        assert int(row["trace_submits"]) == (0 if row["case"] == "empty" else 1), row
        expected_bytes = WIDTH*HEIGHT*(16 if row["case"] == "empty" else 80)
        assert int(row["readback_bytes"]) == expected_bytes, row
    normal = next(row for row in stats if row["case"] == "normal_update")
    assert all(int(normal[key]) == 0 for key in ("blas_builds", "blas_refits", "tlas_builds", "tlas_updates", "geometry_upload")), normal
    assert int(normal["table_upload"]) > 0, normal
    assert (raw/"rotated_ortho.guides").read_bytes() == (raw/"recovered.guides").read_bytes(), "recovery changed guides"
    assert (raw/"rotated_ortho.rgba32f").read_bytes() == (raw/"recovered.rgba32f").read_bytes(), "recovery changed beauty"
    bad = (raw/"malformed.txt").read_text()
    assert "accepted=0 has_scene=0" in bad and "error=" in bad and len(bad.split("error=", 1)[1].strip()) > 0, bad
    assert (raw/"teardown.txt").read_text().strip() == "errors=0 warnings=0"
    result["normal_update_cost"] = normal
    result["malformed_recovery"] = bad.strip()
    result["stats_sha256"] = digest(raw/"stats.csv")
    result["scope"] = "Supplementary standalone GPU checks, not Hydra translation or installed Houdini acceptance."
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    try:
        receipt = judge(args.run)
    except Exception as error:
        receipt = {"status": "failed", "error": str(error)}
    (args.run/"judge.json").write_text(json.dumps(receipt, indent=2)+"\n")
    print(json.dumps({"status": receipt["status"], "cases": len(receipt.get("cases", {})), "error": receipt.get("error")}))
    raise SystemExit(0 if receipt["status"] == "passed" else 1)
