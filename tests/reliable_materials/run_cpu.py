"""Build/run the CPU material regressions and bind their receipt to dependencies."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory():
    return {p.relative_to(ROOT).as_posix(): sha(p)
            for directory in (ROOT / "src/hdRT", ROOT / "third_party/openpbr-bsdf")
            for p in sorted(directory.rglob("*")) if p.is_file()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hfs", default=os.environ.get("HFS", "C:/Program Files/Side Effects Software/Houdini 22.0.429"))
    parser.add_argument("--build", type=Path, default=ROOT / "build/m6/cpu-materials")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    before = inventory()
    dependencies = ("material.cpp material.h mesh.cpp mesh.h meshNormals.cpp meshNormals.h "
                    "world.cpp world.h scene.h renderParam.h vkFurnace.h log.h").split()
    native = {"src/hdRT/" + name: before["src/hdRT/" + name] for name in dependencies}
    tests = {p.relative_to(ROOT).as_posix(): sha(p) for p in
             (Path(__file__), Path(__file__).with_name("material_translation.cpp"),
              Path(__file__).with_name("CMakeLists.txt"))}
    commands = [
        ["cmake", "-S", str(ROOT / "tests/reliable_materials"), "-B", str(args.build),
         "-G", "Visual Studio 17 2022", "-A", "x64", "-DCMAKE_PREFIX_PATH=" + args.hfs + "/toolkit/cmake"],
        ["cmake", "--build", str(args.build), "--config", "Release", "--parallel", "4"],
        ["ctest", "--test-dir", str(args.build), "-C", "Release", "--output-on-failure", "-V"],
    ]
    env = os.environ.copy()
    env["PATH"] = args.hfs + "/bin;C:/Program Files/CMake/bin;" + env.get("PATH", "")
    results = []
    for index, command in enumerate(commands):
        started = time.perf_counter()
        run = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
        logfile = args.out / (str(index) + ".log")
        logfile.write_text(run.stdout + run.stderr, encoding="utf-8")
        results.append({"command": command, "exit_code": run.returncode,
                        "seconds": time.perf_counter()-started, "log": str(logfile),
                        "log_sha256": sha(logfile)})
        if run.returncode:
            break
    after = inventory()
    changed = sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))
    dependency_changed = [p for p in native if before.get(p) != after.get(p)]
    exe = args.build / "Release/material_translation.exe"
    passed = len(results) == len(commands) and all(r["exit_code"] == 0 for r in results) and not dependency_changed
    receipt = {
        "status": "passed" if passed else "failed", "scope": "CPU material translation, native USD ingress and immutable scene binding; no GPU work",
        "hfs": args.hfs, "native_source_files": native, "test_source_files": tests,
        "native_inventory_before": before, "native_inventory_after": after,
        "native_files_changed_during_run": changed, "compiled_dependencies_changed": dependency_changed,
        "executable": str(exe), "executable_sha256": sha(exe) if exe.is_file() else None,
        "commands": results,
    }
    (args.out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": receipt["status"], "receipt": str(args.out / "receipt.json")}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
