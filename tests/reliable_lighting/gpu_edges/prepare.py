"""Freeze inputs and compile only. Running the executable requires a separate command."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--shader-dir", type=Path)
    parser.add_argument("--vulkan-sdk", type=Path, default=Path(os.environ.get("VULKAN_SDK", "C:/VulkanSDK/1.4.341.1")))
    parser.add_argument("--vcvars", type=Path, default=Path("C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools/VC/Auxiliary/Build/vcvars64.bat"))
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    root = here.parents[2]
    out = args.output.resolve()
    shaders = args.shader_dir or root/"build/m5/cmake/shaders"
    out.mkdir(parents=True, exist_ok=False)
    mapping = {}
    for name in ("probe.cpp", "judge.py", "prepare.py"):
        mapping["inputs/probe/"+name] = here/name
    for name in ("vkFurnace.cpp", "vkFurnace.h", "log.h"):
        mapping["inputs/source/"+name] = root/"src/hdRT"/name
    for name in ("furnace.comp", "lighting.glsl", "guides.glsl", "resolve.comp"):
        mapping["inputs/source/shaders/"+name] = root/"src/hdRT/shaders"/name
    for name in ("furnace.spv", "resolve.spv"):
        mapping["inputs/shaders/"+name] = shaders/name
    for relative, source in mapping.items():
        destination = out/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    manifest = {"created_utc": datetime.now(timezone.utc).isoformat(),
                "scope": "Supplementary standalone deterministic GPU edge test; compile step does not run GPU.",
                "source_paths": {k: str(v.resolve()) for k, v in mapping.items()},
                "sha256": {k: digest(out/k) for k in mapping}}
    (out/"manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    script = ["@echo off", "setlocal", f'call "{args.vcvars}" > toolchain.log 2>&1',
              "if errorlevel 1 exit /b 13",
              f'cl /nologo /std:c++20 /O2 /EHsc /D_CRT_SECURE_NO_WARNINGS /I"{args.vulkan_sdk / "Include"}" '
              f'/I"{out / "inputs/source"}" "{out / "inputs/probe/probe.cpp"}" '
              f'"{out / "inputs/source/vkFurnace.cpp"}" /Fe:edge_probe.exe '
              f'/link /LIBPATH:"{args.vulkan_sdk / "Lib"}" vulkan-1.lib > compile.log 2>&1',
              "exit /b %ERRORLEVEL%"]
    (out/"compile.cmd").write_text("\n".join(script)+"\n")
    command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(out/"compile.cmd")]
    compiled = subprocess.run(command, cwd=out, check=False)
    manifest["compile_exit"] = compiled.returncode
    if compiled.returncode == 0:
        manifest["sha256"]["edge_probe.exe"] = digest(out/"edge_probe.exe")
    (out/"manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    run = ["@echo off", "setlocal", f'cd /d "{out}"',
           'edge_probe.exe "inputs/shaders/furnace.spv" "raw" > stdout.log 2> stderr.log',
           "set edge_result=%ERRORLEVEL%", "echo %edge_result% > run_exit.txt", "exit /b %edge_result%"]
    (out/"run.cmd").write_text("\n".join(run)+"\n")
    print(json.dumps({"output": str(out), "compile_exit": compiled.returncode, "gpu_launched": False}))
    return compiled.returncode


if __name__ == "__main__":
    raise SystemExit(main())
