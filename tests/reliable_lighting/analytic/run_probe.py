"""Archive, compile and optionally run the supplemental probe. GPU requires --run."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
VS = Path("C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools/VC/Auxiliary/Build/vcvars64.bat")
SDK = Path("C:/VulkanSDK/1.4.341.1")


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shaders", type=Path, required=True)
    parser.add_argument("--run", action="store_true", help="Requires parent authorization and exclusive GPU slot")
    args = parser.parse_args()
    for path in (VS, SDK / "Include/vulkan/vulkan.h", args.shaders / "furnace.spv", args.shaders / "resolve.spv"):
        if not path.is_file(): parser.error("missing prerequisite: " + str(path))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "build/m5/runs" / (stamp + "-analytic-" + secrets.token_hex(4))
    out.mkdir(parents=True, exist_ok=False)
    original = []
    for path in (ROOT / "src/hdRT").rglob("*"):
        if path.is_file(): original.append((path, Path("source") / path.relative_to(ROOT / "src/hdRT")))
    for name in ("oracle.py", "make_fixtures.py", "judge.py", "lighting_probe.cpp", "run_probe.py"):
        original.append((HERE / name, Path("probe") / name))
    for path in (HERE / "frozen").iterdir():
        if path.is_file(): original.append((path, Path("probe/frozen") / path.name))
    for name in ("furnace.spv", "resolve.spv"):
        original.append((args.shaders / name, Path("shaders") / name))
    before = {str(path): sha(path) for path, _ in original}
    for path, rel in original:
        dest = out / "inputs" / rel; dest.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(path, dest)
        if sha(dest) != before[str(path)]: raise RuntimeError("source changed during snapshot: " + str(path))
    source = out / "inputs/source"; probe = out / "inputs/probe"
    batch = out / "compile.cmd"
    batch.write_bytes(("\r\n".join([
        "@echo off", "setlocal", f'call "{VS}" > toolchain.log 2>&1', "if errorlevel 1 exit /b 13",
        f'cl /nologo /std:c++20 /O2 /EHsc /D_CRT_SECURE_NO_WARNINGS /I"{SDK / "Include"}" /I"{source}" "{probe / "lighting_probe.cpp"}" "{source / "vkFurnace.cpp"}" /Fe:lighting_probe.exe /link /LIBPATH:"{SDK / "Lib"}" vulkan-1.lib > compile.log 2>&1',
        "exit /b %ERRORLEVEL%", ""]) ).encode("utf-8"))
    receipt = dict(status="running", scope="supplementary analytical lighting", output=str(out), gpu_requested=args.run, started_utc=datetime.now(timezone.utc).isoformat(), original_hashes=before)
    def write(): (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    write(); print(out, flush=True)
    code = subprocess.call(["cmd.exe", "/d", "/c", str(batch)], cwd=out)
    receipt["compile_exit_code"] = code
    if not code and args.run:
        with (out / "run.log").open("wb") as log:
            process = subprocess.Popen([str(out / "lighting_probe.exe"), str(out / "inputs/shaders/furnace.spv"), str(out)], cwd=out, stdout=log, stderr=subprocess.STDOUT)
            receipt["owned_gpu_pid"] = process.pid; write(); code = process.wait()
        receipt["gpu_exit_code"] = code
        if not code:
            with (out / "judge.log").open("wb") as log:
                code = subprocess.call([sys.executable, "-B", str(probe / "judge.py"), str(out)], cwd=out, stdout=log, stderr=subprocess.STDOUT)
            receipt["judge_exit_code"] = code
    after = {str(path): sha(path) for path, _ in original}
    receipt["original_hashes_after"] = after; receipt["source_changed_during_run"] = before != after
    if not code and before != after: code = 97
    receipt["status"] = "passed" if not code and args.run else "compiled_only" if not code else "failed"
    receipt["exit_code"] = code; receipt["finished_utc"] = datetime.now(timezone.utc).isoformat()
    receipt["snapshot_hashes"] = {str(p.relative_to(out)): sha(p) for p in sorted((out / "inputs").rglob("*")) if p.is_file()}
    write(); print(json.dumps({"status": receipt["status"], "out": str(out), "code": code}), flush=True)
    return code


if __name__ == "__main__": raise SystemExit(main())
