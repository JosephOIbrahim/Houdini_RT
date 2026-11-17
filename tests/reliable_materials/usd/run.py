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
    out = ROOT / "build/m6/runs" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-usd-" + secrets.token_hex(4)); out.mkdir(parents=True)
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("HOUDINIRT_") or key in ("PYTHONHOME", "PYTHONPATH", "HOUDINI_PACKAGE_DIR", "HOUDINI_DSO_PATH", "PXR_PLUGINPATH_NAME", "OCIO"):
            env.pop(key, None)
    env.update(HOUDINI_USER_PREF_DIR=str(out / "prefs__HVER__"), HOUDINI_PATH="&", HOUDINI_NO_ENV_FILE="1", PXR_PLUGINPATH_NAME=str(plugin), HOUDINIRT_VALIDATION="1", HOUDINIRT_DEBUG="1")
    payload = {str(p.relative_to(plugin)): sha(p) for p in plugin.rglob("*") if p.is_file()}
    script_paths = [HERE / name for name in ("make_fixture.py", "judge.py", "run.py", "native_openpbr.usda", "native_karma_vars.usda", "transport_judge.py", "prepare_transport.py", "cpu_transport.cpp")]
    scripts_before = {str(p): sha(p) for p in script_paths}
    scripts_archive = out / "executed_scripts"; scripts_archive.mkdir()
    for path in script_paths: shutil.copy2(path, scripts_archive / path.name)
    receipt = dict(status="running", output=str(out), payload=str(plugin), payload_hashes=payload, scripts_before=scripts_before,
                   houdini=str(HFS), husk_executable_sha256=sha(HFS / "bin/husk.exe"), started_utc=datetime.now(timezone.utc).isoformat(), renders=[])
    receipt['native_source_files'] = {p.relative_to(ROOT).as_posix():sha(p) for part in ('src/hdRT','third_party/openpbr-bsdf') for p in (ROOT/part).rglob('*') if p.is_file()}
    prior=ROOT/'build/m6/runs/20261117T143009Z-usd-116e2673'
    receipt['original_strict_comparison']=dict(status='failed',scope='Original identical-model Karma assumption failed; archived without reinterpretation',
                                              **{key:str(prior/(key+'.json')) for key in ('receipt','judge')},
                                              **{key+'_sha256':sha(prior/(key+'.json')) for key in ('receipt','judge')})
    qualification=ROOT/'build/m6/model-qualification'
    qualification_files=[p for p in qualification.iterdir() if p.is_file() and p.suffix in ('.py','.json','.md','.html') or p.name=='spec-LICENSE']
    assert (qualification/'MODEL_QUALIFICATION.md').is_file(),'Missing qualified model scope'
    receipt['model_qualification']=dict(scope='Pinned OpenPBR1.1.1 implementation choices; public MaterialX dielectric witness, not a proof of private Karma transport',
                                       files={str(p):sha(p) for p in qualification_files})
    qualification_archive=out/'model_qualification';qualification_archive.mkdir()
    for path in qualification_files:shutil.copy2(path,qualification_archive/path.name)
    receipt['model_qualification']['archive_files']={str(p):sha(p) for p in qualification_archive.iterdir() if p.is_file()}
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
        execute([str(HFS / "bin/hython.exe"), "-B", str(HERE / "make_fixture.py"), str(out / "fixtures")], out / "fixture.log")
        manifest_path = out / "fixtures/manifest.json"; manifest = json.loads(manifest_path.read_text()); receipt["fixture_manifest_before_render_sha256"] = sha(manifest_path); write()
        execute([str(HFS / 'bin/hython.exe'),'-B',str(HERE/'prepare_transport.py'),str(manifest_path),str(out/'transport')],out/'transport.log',timeout=300)
        receipt['transport_reference_before_render_sha256']=sha(out/'transport/reference_manifest.json');write()
        # Karma is rendered first for each immutable stage; it never sees candidate pixels.
        for case in manifest["cases"]:
            for label, renderer in (("karma", "BRAY_HdKarma"), ("candidate", "HdRTRendererPlugin")):
                if label=='karma' and (case['negative'] or case['aov']): continue
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
                    receipt["renders"].append(dict(case=case["name"], seed=case["seed"], renderer=renderer,render_settings=previous['render_settings'],command=previous['command'], image=str(image), image_sha256=sha(image), log=str(log), log_sha256=sha(log), reused_from=dict(image=previous["image"], log=previous["log"], stage=previous_stage["stage"])))
                    write(); print(stem + " " + label + " exact-hash reused", flush=True); continue
                settings='/Render/karma_settings' if label=='karma' else '/Render/settings'
                command=[str(HFS / "bin/husk.exe"), "-R", renderer, '--settings',settings,"-V2", "-o", str(image), case["stage"]]
                execute(command, log)
                raw = log.read_text(errors="replace")
                if any(s in raw.casefold() for s in ('vuid-','validation error','validation warning','vk_error')): raise RuntimeError("native validation message: " + str(log))
                if label == "candidate" and "HoudiniRT: validation layer active" not in raw: raise RuntimeError("validation not proven: " + str(log))
                receipt["renders"].append(dict(case=case["name"], seed=case["seed"], renderer=renderer,command=command,render_settings=settings, image=str(image), image_sha256=sha(image), log=str(log), log_sha256=sha(log)))
                write(); print(stem + " " + label, flush=True)
        execute([str(HFS / "bin/hython.exe"), "-B", str(HERE / "judge.py"), str(out)], out / "judge.log")
        receipt["status"] = "passed"
        receipt['karma_parity_status']='failed / diagnostic, not claimed matched'
        receipt['acceptance_scope']=manifest['scope']
        receipt['judge_sha256']=sha(out/'judge.json')
    except BaseException as exc:
        receipt.update(status="failed", error=repr(exc)); code = 16
    receipt["payload_hashes_after"] = {str(p.relative_to(plugin)): sha(p) for p in plugin.rglob("*") if p.is_file()}
    if receipt["payload_hashes_after"] != payload: receipt.update(status="failed", error="payload changed"); code = 97
    receipt["scripts_after"] = {str(p): sha(p) for p in script_paths}
    if receipt["scripts_after"] != scripts_before: receipt.update(status="failed", error="validation scripts changed"); code = 97
    receipt['native_source_files_after'] = {p.relative_to(ROOT).as_posix():sha(p) for part in ('src/hdRT','third_party/openpbr-bsdf') for p in (ROOT/part).rglob('*') if p.is_file()}
    if receipt['native_source_files_after'] != receipt['native_source_files']: receipt.update(status='failed',error='native source changed'); code=97
    if 'fixture_manifest_before_render_sha256' in receipt and sha(out/'fixtures/manifest.json') != receipt['fixture_manifest_before_render_sha256']:
        receipt.update(status='failed',error='fixture manifest changed'); code=97
    if 'transport_reference_before_render_sha256' in receipt and sha(out/'transport/reference_manifest.json') != receipt['transport_reference_before_render_sha256']:
        receipt.update(status='failed',error='CPU transport reference changed'); code=97
    for path,digest in receipt['model_qualification']['files'].items():
        if sha(Path(path))!=digest:receipt.update(status='failed',error='Model qualification changed');code=97
    receipt["finished_utc"] = datetime.now(timezone.utc).isoformat(); write()
    print(json.dumps({"status": receipt["status"], "output": str(out), "error": receipt.get("error")})); return code


if __name__ == "__main__": raise SystemExit(main())
