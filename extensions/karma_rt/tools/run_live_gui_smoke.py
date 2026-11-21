"""One-command launcher for automated live Houdini 22 GUI smoke verification & demo boot."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOUDINI_BIN = Path(r"C:\Program Files\Side Effects Software\Houdini 22.0.504\bin\houdini.exe")
DEFAULT_HYTHON_BIN = Path(r"C:\Program Files\Side Effects Software\Houdini 22.0.504\bin\hython.exe")
DEFAULT_DEMO_SCENE = ROOT / "demo" / "karmart_solaris_smoke.hiplc"
DEFAULT_EVIDENCE_DIR = ROOT / "demo" / "smoke_evidence"


def ensure_demo_scene(hython_bin: Path, demo_scene: Path) -> None:
    if demo_scene.exists():
        return
    builder = ROOT / "demo" / "build_smoke_scene.py"
    subprocess.run([str(hython_bin), str(builder)], check=True)


def verify_saved_hip_purity(hython_bin: Path, saved_hip: Path) -> dict[str, object]:
    """Independently load the smoke-saved .hiplc in fresh hython and verify 0 KarmaRT specs."""
    if not saved_hip.exists():
        return {"checked": False, "pure": False, "detail": f"Missing {saved_hip}"}
    code = (
        "import json, hou; "
        "from karma_rt.session_overrides import SessionOverrideEngine; "
        f"hou.hipFile.load({saved_hip.as_posix()!r}, suppress_save_prompt=True); "
        "s = hou.node('/stage'); "
        "eng = SessionOverrideEngine(hou); "
        "owned = eng.list_owned_specs(s, display_lop=s.displayNode()) if s and s.displayNode() else []; "
        "krs = s.node('production_karma_settings') if s else None; "
        "spp = int(krs.parm('pathtracedsamples').eval()) if krs else -1; "
        "print(json.dumps({'owned': owned, 'spp': spp}))"
    )
    proc = subprocess.run([str(hython_bin), "-c", code], capture_output=True, text=True, check=False)
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            data = json.loads(line)
            pure = (data.get("owned") == []) and (data.get("spp") == 128)
            return {"checked": True, "pure": pure, **data}
    return {"checked": True, "pure": False, "stdout": proc.stdout, "stderr": proc.stderr}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Automate live Houdini 22 Solaris/KarmaRT GUI verification.")
    parser.add_argument("--houdini-bin", type=Path, default=DEFAULT_HOUDINI_BIN)
    parser.add_argument("--hython-bin", type=Path, default=DEFAULT_HYTHON_BIN)
    parser.add_argument("--demo-scene", type=Path, default=DEFAULT_DEMO_SCENE)
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE_DIR)
    parser.add_argument("--mode", choices=("gui_smoke", "open_demo"), default="gui_smoke")
    parser.add_argument("--keep-open", action="store_true", help="Leave Houdini 22 open in Interactive Lookdev after checks pass.")
    parser.add_argument("--timeout", type=float, default=150.0, help="Seconds to wait for the GUI smoke report.")
    args = parser.parse_args(argv)

    ensure_demo_scene(args.hython_bin, args.demo_scene)
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.evidence_dir / "gui_smoke_report.json"
    if report_path.exists():
        report_path.unlink()

    env = os.environ.copy()
    env["KARMA_RT_AUTORUN_MODE"] = args.mode
    env["KARMA_RT_EVIDENCE_DIR"] = str(args.evidence_dir)
    env["KARMA_RT_REPORT_PATH"] = str(report_path)
    env["KARMA_RT_DEMO_SCENE"] = str(args.demo_scene)
    env["KARMA_RT_EXIT_ON_DONE"] = "0" if args.keep_open else "1"
    env["KARMA_RT_FINAL_PRESET"] = "interactive_lookdev"

    cmd = [str(args.houdini_bin), str(args.demo_scene)]
    print(f"Launching Houdini 22 GUI: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, env=env)

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if report_path.exists():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
                break
            except Exception:
                pass
        if proc.poll() is not None and not report_path.exists():
            print(f"ERROR: Houdini exited early with code {proc.returncode} before writing report.")
            return 2
        time.sleep(0.5)
    else:
        print(f"ERROR: Timed out after {args.timeout:.0f}s waiting for {report_path}")
        if not args.keep_open:
            proc.terminate()
        return 3

    if not args.keep_open:
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.terminate()

    # Independently verify saved .hiplc purity in fresh hython
    saved_hip = args.evidence_dir / "smoke_saved_clean.hiplc"
    hip_check = verify_saved_hip_purity(args.hython_bin, saved_hip)
    report["independent_hip_purity_check"] = hip_check
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\n=== KarmaRT Live Houdini 22 GUI Smoke Report ===")
    for chk in report.get("checks", []):
        tag = "PASS" if chk["passed"] else "FAIL"
        print(f"  [{tag}] {chk['name']}: {chk['detail']}")
    if hip_check.get("checked"):
        tag = "PASS" if hip_check.get("pure") else "FAIL"
        print(f"  [{tag}] independent_hip_purity: owned={hip_check.get('owned')} spp={hip_check.get('spp')}")
    print(f"Screenshots: {report.get('screenshots')}")
    print(f"Report: {report_path}")

    overall_ok = bool(report.get("ok")) and (not hip_check.get("checked") or bool(hip_check.get("pure")))
    print(f"OVERALL: {'PASS' if overall_ok else 'FAIL'} ({report.get('passed')}/{report.get('total')} GUI checks)")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
