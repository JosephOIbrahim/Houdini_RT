"""Sequential isolated husk/Karma crosscheck. Run only with parent's GPU-slot authorization."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent; ROOT = HERE.parents[2]
HFS = Path("C:/Program Files/Side Effects Software/Houdini 22.0.429")


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--plugin", required=True, type=Path)
    parser.add_argument("--reuse", type=Path, help="Reuse exact-stage Karma images; candidate reuse additionally requires exact payload hashes")
    args = parser.parse_args()
    plugin = args.plugin.resolve()
    if not (plugin / "hdRT.dll").is_file(): parser.error("missing candidate plugin")
    out = ROOT / "build/m5/runs" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-usd-" + secrets.token_hex(4)); out.mkdir(parents=True)
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("HOUDINIRT_") or key in ("PYTHONHOME", "PYTHONPATH", "HOUDINI_PACKAGE_DIR", "HOUDINI_DSO_PATH", "PXR_PLUGINPATH_NAME", "OCIO"):
            env.pop(key, None)
    env.update(HOUDINI_USER_PREF_DIR=str(out / "prefs__HVER__"), HOUDINI_PATH="&", HOUDINI_NO_ENV_FILE="1", PXR_PLUGINPATH_NAME=str(plugin), HOUDINIRT_VALIDATION="1", HOUDINIRT_DEBUG="1")
    payload = {str(p.relative_to(plugin)): sha(p) for p in plugin.rglob("*") if p.is_file()}
    script_paths = [HERE / name for name in ("make_usd.py", "judge_usd.py", "run_usd.py")]
    scripts_before = {str(p): sha(p) for p in script_paths}
    scripts_archive = out / "executed_scripts"; scripts_archive.mkdir()
    for path in script_paths: shutil.copy2(path, scripts_archive / path.name)
    receipt = dict(status="running", output=str(out), payload=str(plugin), payload_hashes=payload, scripts_before=scripts_before,
                   houdini=str(HFS), husk_executable_sha256=sha(HFS / "bin/husk.exe"), started_utc=datetime.now(timezone.utc).isoformat(), renders=[])
    def write(): (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    def execute(command, log_path, timeout=180):
        with log_path.open("wb") as log:
            process = subprocess.Popen(command, env=env, cwd=out, stdout=log, stderr=subprocess.STDOUT)
            receipt["owned_child_pid"] = process.pid; write()
            try: code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(); raise
        if code: raise RuntimeError(f"command exited {code}; {log_path}")
    write(); print(out, flush=True)
    reuse_receipt = reuse_manifest = None
    if args.reuse:
        reuse_receipt = json.loads((args.reuse / "receipt.json").read_text())
        reuse_manifest = json.loads((args.reuse / "fixtures/manifest.json").read_text())
        if reuse_receipt["payload_hashes_after"] != reuse_receipt["payload_hashes"]:
            raise RuntimeError("prior run payload changed during its own execution")
        if reuse_receipt.get("houdini", str(HFS)) != str(HFS):
            raise RuntimeError("Karma reference used a different Houdini build")
        if reuse_receipt.get("husk_executable_sha256", receipt["husk_executable_sha256"]) != receipt["husk_executable_sha256"]:
            raise RuntimeError("Husk executable differs from reference")
        receipt["reuse_candidate_payload_matches"] = reuse_receipt["payload_hashes"] == payload
        receipt["reuse_karma_basis"] = "Exact USD stage, image/log hashes and same installed Houdini22.0.429; native candidate payload may change and does not produce Karma reference pixels"
        receipt["reused_receipt"] = str(args.reuse / "receipt.json")
        receipt["reused_receipt_sha256"] = sha(args.reuse / "receipt.json")
    code = 0
    try:
        execute([str(HFS / "bin/hython.exe"), "-B", str(HERE / "make_usd.py"), str(out / "fixtures")], out / "fixture.log")
        manifest_path = out / "fixtures/manifest.json"; manifest = json.loads(manifest_path.read_text()); receipt["fixture_manifest_before_render_sha256"] = sha(manifest_path); write()
        # Karma is rendered first for each immutable stage; it never sees candidate pixels.
        for case in manifest["cases"]:
            for label, renderer in (("karma", "BRAY_HdKarma"), ("candidate", "HdRTRendererPlugin")):
                stem = case["name"] + "_" + str(case["seed"])
                image = out / (stem + "." + label + ".exr"); log = out / (stem + "." + label + ".log")
                previous_stage = next((c for c in reuse_manifest["cases"] if c["name"] == case["name"] and c["seed"] == case["seed"]), None) if reuse_manifest else None
                previous = next((r for r in reuse_receipt["renders"] if r["case"] == case["name"] and r["seed"] == case["seed"] and r["renderer"] == renderer), None) if reuse_receipt else None
                payload_eligible = label == "karma" or (reuse_receipt and reuse_receipt["payload_hashes"] == payload)
                if payload_eligible and previous_stage and previous and previous_stage["stage_sha256"] == case["stage_sha256"]:
                    if sha(Path(previous_stage["stage"])) != case["stage_sha256"]: raise RuntimeError("reuse stage changed")
                    for key, target in (("image", image), ("log", log)):
                        source = Path(previous[key])
                        if sha(source) != previous[key + "_sha256"]: raise RuntimeError("reuse " + key + " changed")
                        shutil.copy2(source, target)
                    receipt["renders"].append(dict(case=case["name"], seed=case["seed"], renderer=renderer, image=str(image), image_sha256=sha(image), log=str(log), log_sha256=sha(log), reused_from=dict(image=previous["image"], log=previous["log"], stage=previous_stage["stage"])))
                    write(); print(stem + " " + label + " exact-hash reused", flush=True); continue
                execute([str(HFS / "bin/husk.exe"), "-R", renderer, "-V2", "-o", str(image), case["stage"]], log)
                raw = log.read_text(errors="replace")
                if "VUID-" in raw or "Validation Error" in raw or "VK_ERROR" in raw: raise RuntimeError("native validation error: " + str(log))
                if label == "candidate" and "HoudiniRT: validation layer active" not in raw: raise RuntimeError("validation not proven: " + str(log))
                receipt["renders"].append(dict(case=case["name"], seed=case["seed"], renderer=renderer, image=str(image), image_sha256=sha(image), log=str(log), log_sha256=sha(log)))
                write(); print(stem + " " + label, flush=True)
        execute([str(HFS / "bin/hython.exe"), "-B", str(HERE / "judge_usd.py"), str(out)], out / "judge.log")
        receipt["status"] = "passed"
    except BaseException as exc:
        receipt.update(status="failed", error=repr(exc)); code = 16
    receipt["payload_hashes_after"] = {str(p.relative_to(plugin)): sha(p) for p in plugin.rglob("*") if p.is_file()}
    if receipt["payload_hashes_after"] != payload: receipt.update(status="failed", error="payload changed"); code = 97
    receipt["scripts_after"] = {str(p): sha(p) for p in script_paths}
    if receipt["scripts_after"] != scripts_before: receipt.update(status="failed", error="validation scripts changed"); code = 97
    receipt["finished_utc"] = datetime.now(timezone.utc).isoformat(); write()
    print(json.dumps({"status": receipt["status"], "output": str(out), "error": receipt.get("error")})); return code


if __name__ == "__main__": raise SystemExit(main())
