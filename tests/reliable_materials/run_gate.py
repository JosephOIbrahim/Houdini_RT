"""Run unchanged sealed s4/s5 ONLY in a copied ignored runbox, never source verify/.

The exact runbox plugin is the accepted artifact. Source/protected file hashes are
checked before/after, and the original paths are never passed to exam commands.
GPU use requires the parent's exclusive-slot authorization before invocation.
"""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
VS = Path("C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools/VC/Auxiliary/Build/vcvars64.bat")
CMAKE = Path("C:/Program Files/CMake/bin/cmake.exe")
BASH = Path("C:/Program Files/Git/bin/bash.exe")
SDK = Path("C:/VulkanSDK/1.4.341.1")
HFS = Path("C:/Program Files/Side Effects Software/Houdini 22.0.429")


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes():
    return {p.relative_to(ROOT).as_posix(): sha(p)
            for base in (ROOT / "src/hdRT", ROOT / "third_party/openpbr-bsdf")
            for p in sorted(base.rglob("*")) if p.is_file()}


def protected_hashes():
    raw = subprocess.check_output(["git", "ls-files", "-z", "verify", "specs", "drafts", "orchestrator", "agents"], cwd=ROOT)
    return {name: sha(ROOT / name) for name in raw.decode().split("\0") if name and (ROOT / name).is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("mode", choices=("s4", "s5")); args = parser.parse_args()
    for path in (VS, CMAKE, BASH, SDK / "Bin/glslangValidator.exe", HFS / "bin/husk.exe"):
        if not path.is_file(): parser.error("Missing prerequisite: " + str(path))
    nonce = secrets.randbits(64); stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run = ROOT / "build/m6/runs" / f"{stamp}-{args.mode}-{nonce}"; run.mkdir(parents=True, exist_ok=False)
    box = run / "runbox"; box.mkdir()
    source = source_hashes(); protected = protected_hashes()
    copies = dict(source)
    copies.update({k: v for k, v in protected.items() if k.startswith(("verify/", "specs/"))})
    for path in (ROOT / "third_party/usd_wg_teapot").rglob("*"):
        if path.is_file(): copies[path.relative_to(ROOT).as_posix()] = sha(path)
    for rel, expected in copies.items():
        target = box / rel; target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(ROOT / rel, target)
        if sha(target) != expected: raise RuntimeError("copy mismatch: " + rel)
    # Every source verify file is a read-only input. The copied scripts may write
    # generated sibling evidence/logs only in this run's own ignored runbox.
    before_verify = {p.relative_to(ROOT).as_posix(): sha(p) for p in (ROOT / "verify").rglob("*") if p.is_file()}
    command = run / "run.cmd"
    lines = ["@echo off", "setlocal", f'call "{VS}" > "{run / "toolchain.log"}" 2>&1', "if errorlevel 1 exit /b 13",
             f'set "VULKAN_SDK={SDK}"', f'set "HRT_HFS={HFS.as_posix()}"',
             f'set "PATH=%VCToolsInstallDir%bin\\HostX64\\x64;{CMAKE.parent};{SDK / "Bin"};{BASH.parent.parent / "usr/bin"};{BASH.parent};%PATH%"',
             f'cd /d "{box}"', f'"{BASH}" "verify/{"s5_liveedits.sh" if args.mode == "s5" else "s4_firstlight.sh"}" "{nonce}"', "exit /b %ERRORLEVEL%"]
    command.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))
    receipt = dict(status="running", mode=args.mode, nonce=str(nonce), workspace=str(ROOT), runbox=str(box), started_utc=datetime.now(timezone.utc).isoformat(),
                   head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(), source_before=source, protected_before=protected,
                   source_verify_before=before_verify, copied_inputs=copies, command_sha256=sha(command), installation_changed=False)
    def write(): (run / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    write(); print("Run archive: " + str(run), flush=True); started = time.monotonic()
    with (run / "driver.log").open("wb") as log:
        process = subprocess.Popen(["cmd.exe", "/d", "/c", str(command)], cwd=box, stdout=log, stderr=subprocess.STDOUT)
        receipt["runner_pid"] = process.pid; write(); code = process.wait()
    receipt["exam_exit_code"] = code; receipt["duration_seconds"] = time.monotonic() - started
    receipt["source_after"] = source_hashes(); receipt["protected_after"] = protected_hashes()
    receipt["source_verify_after"] = {p.relative_to(ROOT).as_posix(): sha(p) for p in (ROOT / "verify").rglob("*") if p.is_file()}
    receipt["runbox_input_hashes_after"] = {rel: sha(box / rel) for rel in copies}
    changed = receipt["source_after"] != source or receipt["protected_after"] != protected or receipt["source_verify_after"] != before_verify or receipt["runbox_input_hashes_after"] != copies
    receipt["source_or_protected_changed"] = changed
    if changed and not code: code = 97
    receipt["status"] = "passed" if not code else "failed"; receipt["exit_code"] = code
    receipt["finished_utc"] = datetime.now(timezone.utc).isoformat()
    evidence = box / "verify/evidence.json"
    if evidence.is_file(): receipt["exam_evidence"] = json.loads(evidence.read_text(encoding="utf-8-sig"))
    artifacts = {}
    for base in (box / "verify", box / "build/plugin_stage", box / "build/s4", box / "build/s5"):
        if base.exists():
            for path in base.rglob("*"):
                rel = path.relative_to(base)
                if not path.is_file() or "cmake" in rel.parts or any(p.startswith("prefs") for p in rel.parts): continue
                if path.suffix.lower() not in (".log", ".json", ".exr", ".usd", ".usda", ".usdc", ".py", ".dll", ".spv", ".txt"): continue
                artifacts[path.relative_to(run).as_posix()] = dict(sha256=sha(path), bytes=path.stat().st_size)
    receipt["artifacts"] = artifacts; write()
    print(json.dumps({"status": receipt["status"], "exit_code": code, "receipt": str(run / "receipt.json")}), flush=True)
    return code


if __name__ == "__main__": raise SystemExit(main())
