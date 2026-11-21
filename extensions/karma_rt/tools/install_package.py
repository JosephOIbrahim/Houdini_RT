"""CLI installer and legacy-package quarantine utility for KarmaRT (Houdini 22)."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Sequence

PACKAGE_FILENAME = "karma_rt.json"
DEFAULT_KARMA_RT_HOME = Path(__file__).resolve().parents[1]
ENABLE_EXPRESSION = (
    "houdini_os == 'windows' and houdini_version >= '22.0' and houdini_version < '23.0'"
)
LEGACY_PACKAGE_FILES: tuple[str, ...] = (
    "zz_houdinirt.json",
    "zzz_houdinirt_artist_ui.json",
)
QUARANTINE_SUFFIX = ".disabled-before-karmart"


def default_packages_dir() -> Path:
    """Resolve the default Windows Houdini 22.0 user packages directory."""
    user_pref = os.environ.get("HOUDINI_USER_PREF_DIR", "").strip()
    if user_pref:
        return Path(user_pref) / "packages"
    return Path.home() / "Documents" / "houdini22.0" / "packages"


def build_package_manifest(
    karma_rt_home: str | os.PathLike[str] = DEFAULT_KARMA_RT_HOME,
    *,
    xpu_turbo_env: bool = False,
    gpu_only: bool = False,
) -> dict[str, Any]:
    """Build the Houdini 22 ``karma_rt.json`` package manifest with forward slashes."""
    home_posix = Path(karma_rt_home).resolve().as_posix()
    env_entries: list[dict[str, Any]] = [
        {"KARMA_RT_HOME": home_posix},
        {
            "PYTHONPATH": {
                "value": "$KARMA_RT_HOME/python",
                "method": "append",
            }
        },
        {
            "HOUDINI_TOOLBAR_PATH": {
                "value": "$KARMA_RT_HOME/houdini/toolbar",
                "method": "prepend",
            }
        },
        {
            "HOUDINI_PANEL_PATH": {
                "value": "$KARMA_RT_HOME/houdini/python_panels",
                "method": "append",
            }
        },
    ]
    if xpu_turbo_env:
        env_entries.extend(
            [
                {"KARMA_XPU_NUM_PER_DEVICE_BLENDING_THREADS": "4"},
                {"KARMA_XPU_OPTIX_SPARSE_TEXTURES": "1"},
            ]
        )
    if gpu_only:
        env_entries.append({"KARMA_XPU_DEVICES": "optix"})

    return {
        "enable": ENABLE_EXPRESSION,
        "show": True,
        "load_package_once": True,
        "env": env_entries,
        "hpath": "$KARMA_RT_HOME/houdini",
    }


def quarantine_legacy_packages(
    packages_dir: Path,
    *,
    dry_run: bool = False,
) -> list[dict[str, str]]:
    """Reversibly rename legacy HoudiniRT JSON descriptors without deleting anything."""
    actions: list[dict[str, str]] = []
    for filename in LEGACY_PACKAGE_FILES:
        src = packages_dir / filename
        dst = packages_dir / f"{filename}{QUARANTINE_SUFFIX}"
        if src.exists() and not dst.exists():
            if not dry_run:
                src.rename(dst)
            actions.append(
                {
                    "file": filename,
                    "source": src.as_posix(),
                    "target": dst.as_posix(),
                    "status": "would_quarantine" if dry_run else "quarantined",
                }
            )
        elif src.exists() and dst.exists():
            actions.append(
                {
                    "file": filename,
                    "source": src.as_posix(),
                    "target": dst.as_posix(),
                    "status": "skipped_quarantine_target_exists",
                }
            )
        elif not src.exists() and dst.exists():
            actions.append(
                {
                    "file": filename,
                    "source": src.as_posix(),
                    "target": dst.as_posix(),
                    "status": "already_quarantined",
                }
            )
        else:
            actions.append(
                {
                    "file": filename,
                    "source": src.as_posix(),
                    "target": dst.as_posix(),
                    "status": "not_present",
                }
            )
    return actions


def install_package(
    packages_dir: Path,
    *,
    karma_rt_home: Path = DEFAULT_KARMA_RT_HOME,
    dry_run: bool = False,
    quarantine_legacy: bool = False,
    xpu_turbo_env: bool = False,
    gpu_only: bool = False,
) -> dict[str, Any]:
    """Install ``karma_rt.json`` into ``packages_dir`` and optionally quarantine legacy JSONs."""
    manifest = build_package_manifest(
        karma_rt_home=karma_rt_home,
        xpu_turbo_env=xpu_turbo_env,
        gpu_only=gpu_only,
    )
    target_path = packages_dir / PACKAGE_FILENAME
    rendered_json = json.dumps(manifest, indent=4) + "\n"

    quarantine_results: list[dict[str, str]] = []
    if quarantine_legacy:
        quarantine_results = quarantine_legacy_packages(packages_dir, dry_run=dry_run)

    existed = target_path.exists()
    if dry_run:
        install_status = "would_update" if existed else "would_install"
    else:
        packages_dir.mkdir(parents=True, exist_ok=True)
        target_path.write_text(rendered_json, encoding="utf-8")
        install_status = "updated" if existed else "installed"

    return {
        "action": "install",
        "dry_run": bool(dry_run),
        "packages_dir": packages_dir.as_posix(),
        "karma_rt_home": Path(karma_rt_home).resolve().as_posix(),
        "package_file": target_path.as_posix(),
        "status": install_status,
        "manifest": manifest,
        "quarantine_legacy": quarantine_results,
    }


def uninstall_package(
    packages_dir: Path,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Remove ``karma_rt.json`` from ``packages_dir`` without deleting quarantined legacy files."""
    target_path = packages_dir / PACKAGE_FILENAME
    if not target_path.exists():
        status = "not_installed"
    elif dry_run:
        status = "would_uninstall"
    else:
        target_path.unlink()
        status = "uninstalled"

    return {
        "action": "uninstall",
        "dry_run": bool(dry_run),
        "packages_dir": packages_dir.as_posix(),
        "package_file": target_path.as_posix(),
        "status": status,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="install_package.py",
        description="Install, quarantine legacy configs for, or uninstall KarmaRT for Houdini 22.",
    )
    parser.add_argument(
        "--packages-dir",
        type=Path,
        default=None,
        help="Target Houdini 22 packages directory (defaults to Documents/houdini22.0/packages).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report planned package and quarantine changes without modifying disk.",
    )
    parser.add_argument(
        "--quarantine-legacy",
        action="store_true",
        help=(
            "Reversibly rename legacy zz_houdinirt.json and zzz_houdinirt_artist_ui.json "
            "to *.disabled-before-karmart."
        ),
    )
    parser.add_argument(
        "--xpu-turbo-env",
        action="store_true",
        help=(
            "Write SideFX-recommended Karma XPU performance env vars "
            "(KARMA_XPU_NUM_PER_DEVICE_BLENDING_THREADS=4, KARMA_XPU_OPTIX_SPARSE_TEXTURES=1)."
        ),
    )
    parser.add_argument(
        "--gpu-only",
        action="store_true",
        help="Also set KARMA_XPU_DEVICES=optix to dedicate Karma XPU strictly to OptiX GPU(s).",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove karma_rt.json from the target packages directory.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    packages_dir = (
        args.packages_dir.expanduser().resolve()
        if args.packages_dir is not None
        else default_packages_dir().resolve()
    )

    if args.uninstall:
        summary = uninstall_package(packages_dir, dry_run=args.dry_run)
    else:
        summary = install_package(
            packages_dir,
            karma_rt_home=DEFAULT_KARMA_RT_HOME,
            dry_run=args.dry_run,
            quarantine_legacy=args.quarantine_legacy,
            xpu_turbo_env=args.xpu_turbo_env,
            gpu_only=args.gpu_only,
        )

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
