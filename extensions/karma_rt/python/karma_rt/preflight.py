"""Read-only Houdini 22 / Solaris / Karma XPU preflight diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import sys
from typing import Any

from .catalog import (
    DEFAULT_RENDERER_LABELS,
    H22Catalog,
    KARMA_CPU_ALIASES,
    KARMA_XPU_ALIASES,
    VULKAN_ALIASES,
    load_h22_catalog,
    resolve_delegate_label,
)
from .hardware import HardwareSnapshot, probe_hardware

DIAGNOSTIC_ENV_VARS: tuple[str, ...] = (
    "KARMA_XPU_DEVICES",
    "KARMA_XPU_DISABLE_OPTIX_DEVICE",
    "KARMA_XPU_DISABLE_EMBREE_DEVICE",
    "KARMA_XPU_OPTIX_SPARSE_TEXTURES",
    "KARMA_XPU_NUM_PER_DEVICE_BLENDING_THREADS",
    "OCIO",
)

REFERENCE_GRID_GUARD_VERSION: tuple[int, int, int] = (22, 0, 429)


@dataclass(frozen=True)
class PreflightCheck:
    """Single read-only preflight check result."""

    name: str
    status: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass
class PreflightReport:
    """Aggregated Houdini 22 / KarmaRT environment readiness report."""

    status: str
    houdini_version: str | None
    python_version: str
    usd_version: str | None
    checks: list[PreflightCheck] = field(default_factory=list)
    available_delegates: list[str] = field(default_factory=list)
    karma_xpu_available: bool = False
    karma_cpu_available: bool = False
    vulkan_available: bool = False
    reference_grid_guard_needed: bool = False
    recommended_denoiser: str = "optix"
    env_diagnostics: dict[str, str | None] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    hardware: HardwareSnapshot | None = None
    panes_follow_current_node: bool | None = None
    update_renderer_in_background: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "houdini_version": self.houdini_version,
            "python_version": self.python_version,
            "usd_version": self.usd_version,
            "checks": [check.to_dict() for check in self.checks],
            "available_delegates": list(self.available_delegates),
            "karma_xpu_available": self.karma_xpu_available,
            "karma_cpu_available": self.karma_cpu_available,
            "vulkan_available": self.vulkan_available,
            "reference_grid_guard_needed": self.reference_grid_guard_needed,
            "recommended_denoiser": self.recommended_denoiser,
            "env_diagnostics": dict(self.env_diagnostics),
            "warnings": list(self.warnings),
            "hardware": self.hardware.to_dict() if self.hardware is not None else None,
            "panes_follow_current_node": self.panes_follow_current_node,
            "update_renderer_in_background": self.update_renderer_in_background,
        }


def _resolve_hou_module(hou_module: Any) -> Any:
    if hou_module is not None:
        return hou_module
    try:
        import hou  # type: ignore[import-not-found]

        return hou
    except Exception:
        return None


def _read_env_var(name: str, hou_module: Any) -> str | None:
    val = os.environ.get(name)
    if val is not None and val.strip() != "":
        return val.strip()
    if hou_module is not None:
        try:
            hou_val = hou_module.getenv(name)
            if hou_val is not None and str(hou_val).strip() != "":
                return str(hou_val).strip()
        except Exception:
            pass
    return None


def _is_truthy_env(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().casefold() not in ("", "0", "false", "no", "off")


def _extract_version_tuple(hou_module: Any) -> tuple[int, ...] | None:
    if hou_module is None:
        return None
    try:
        raw = hou_module.applicationVersion()
        return tuple(int(part) for part in raw)
    except Exception:
        return None


def _collect_delegates(
    hou_module: Any,
    viewer: Any,
    catalog: H22Catalog,
) -> tuple[list[str], list[str]]:
    """Collect live Hydra delegate labels and raw names without mutating state."""
    labels: list[str] = []
    names: list[str] = []
    seen_labels: set[str] = set()
    seen_names: set[str] = set()

    def _add_label(lbl: str) -> None:
        cleaned = str(lbl).strip()
        if cleaned and cleaned not in seen_labels:
            seen_labels.add(cleaned)
            labels.append(cleaned)

    def _add_name(nm: str) -> None:
        cleaned = str(nm).strip()
        if cleaned and cleaned not in seen_names:
            seen_names.add(cleaned)
            names.append(cleaned)

    if hou_module is not None:
        try:
            for lbl in hou_module.lop.availableRendererLabels():
                _add_label(str(lbl))
        except Exception:
            pass
        had_live_labels = bool(labels)
        try:
            for nm in hou_module.lop.availableRendererNames():
                nm_str = str(nm)
                _add_name(nm_str)
                if not had_live_labels:
                    mapped = catalog.renderer_labels.get(nm_str) or DEFAULT_RENDERER_LABELS.get(nm_str)
                    if mapped:
                        _add_label(mapped)
        except Exception:
            pass

    if viewer is not None and hasattr(viewer, "hydraRenderers"):
        try:
            viewer_renderers = viewer.hydraRenderers()
            if viewer_renderers:
                for entry in viewer_renderers:
                    entry_str = str(entry).strip()
                    if not entry_str:
                        continue
                    mapped = catalog.renderer_labels.get(entry_str) or DEFAULT_RENDERER_LABELS.get(entry_str)
                    if mapped:
                        _add_name(entry_str)
                        _add_label(mapped)
                    else:
                        _add_label(entry_str)
        except Exception:
            pass

    return labels, names


def run_preflight(
    hou_module: Any = None,
    viewer: Any = None,
    catalog: H22Catalog | None = None,
) -> PreflightReport:
    """Run strictly read-only Houdini 22 / Solaris / Karma XPU preflight checks."""
    hou_mod = _resolve_hou_module(hou_module)
    cat = catalog if catalog is not None else load_h22_catalog(hou_module=hou_mod)

    checks: list[PreflightCheck] = []
    warnings: list[str] = []
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    # 1. Houdini 22.0.* version check & H22.0.429 reference grid guard check.
    version_tuple = _extract_version_tuple(hou_mod)
    houdini_version = cat.houdini_version
    if houdini_version is None and version_tuple is not None:
        houdini_version = ".".join(str(x) for x in version_tuple)

    reference_grid_guard_needed = (
        version_tuple is not None
        and len(version_tuple) >= 3
        and version_tuple[:3] == REFERENCE_GRID_GUARD_VERSION
    )

    if version_tuple is None:
        msg = "Houdini `hou` module is not available in this Python interpreter."
        checks.append(PreflightCheck("houdini_version", "FAIL", msg))
        warnings.append(msg)
    elif len(version_tuple) >= 2 and version_tuple[0] == 22 and version_tuple[1] == 0:
        checks.append(
            PreflightCheck(
                "houdini_version",
                "PASS",
                f"Houdini {houdini_version} is within supported 22.0.* range.",
            )
        )
    else:
        msg = f"Houdini {houdini_version} is outside the validated 22.0.* range."
        checks.append(PreflightCheck("houdini_version", "WARN", msg))
        warnings.append(msg)

    if reference_grid_guard_needed:
        msg = (
            "Houdini 22.0.429 detected: reference grid guard should be enabled "
            "when toggling viewport overlays."
        )
        checks.append(PreflightCheck("reference_grid_guard", "WARN", msg))
        warnings.append(msg)
    else:
        checks.append(
            PreflightCheck(
                "reference_grid_guard",
                "PASS",
                "Reference grid guard not required for this build.",
            )
        )

    # 2. Python version check.
    py_status = "PASS" if sys.version_info >= (3, 13) else "INFO"
    checks.append(
        PreflightCheck(
            "python_version",
            py_status,
            f"Python {python_version} active.",
        )
    )

    # 3. pxr.Usd check.
    usd_version = cat.usd_version
    if usd_version is None:
        try:
            from pxr import Usd  # type: ignore[import-not-found]

            ver = Usd.GetVersion()
            usd_version = ".".join(str(x) for x in ver) if isinstance(ver, (tuple, list)) else str(ver)
        except Exception:
            usd_version = None

    if usd_version is not None:
        checks.append(PreflightCheck("pxr_usd", "PASS", f"pxr.Usd {usd_version} available."))
    else:
        msg = "pxr.Usd is unavailable; USD viewport overrides cannot be authored."
        checks.append(PreflightCheck("pxr_usd", "FAIL", msg))
        warnings.append(msg)

    # 4. UsdRenderers.json catalog health check.
    if cat.source != "fallback":
        checks.append(
            PreflightCheck(
                "usd_renderers_catalog",
                "PASS",
                (
                    f"Loaded live UsdRenderers.json from {cat.hfs_path} "
                    f"({len(cat.restart_render_settings)} render / "
                    f"{len(cat.restart_camera_settings)} camera restart patterns)."
                ),
            )
        )
    else:
        msg = (
            "UsdRenderers.json could not be read from $HFS; using built-in "
            "Houdini 22.0.504 fallback catalog."
        )
        checks.append(PreflightCheck("usd_renderers_catalog", "WARN", msg))
        warnings.append(msg)

    # 5. Hydra delegates check.
    available_delegates, delegate_names = _collect_delegates(hou_mod, viewer, cat)
    probe_pool = [*available_delegates, *delegate_names]

    karma_xpu_available = resolve_delegate_label(probe_pool, KARMA_XPU_ALIASES) is not None
    karma_cpu_available = resolve_delegate_label(probe_pool, KARMA_CPU_ALIASES) is not None
    vulkan_available = resolve_delegate_label(probe_pool, VULKAN_ALIASES) is not None

    if available_delegates:
        checks.append(
            PreflightCheck(
                "hydra_delegates",
                "PASS" if karma_xpu_available else ("WARN" if karma_cpu_available else "FAIL"),
                f"Detected Hydra delegates: {', '.join(available_delegates)}.",
            )
        )
    else:
        msg = "No Hydra render delegates detected."
        checks.append(PreflightCheck("hydra_delegates", "FAIL", msg))
        warnings.append(msg)

    if not karma_xpu_available:
        msg = (
            "Karma XPU delegate is not available; falling back to Karma CPU."
            if karma_cpu_available
            else "Neither Karma XPU nor Karma CPU Hydra delegates are available."
        )
        warnings.append(msg)

    # 6. Karma XPU environment diagnostics & denoiser recommendation.
    env_diagnostics: dict[str, str | None] = {
        var: _read_env_var(var, hou_mod) for var in DIAGNOSTIC_ENV_VARS
    }

    optix_disabled = _is_truthy_env(env_diagnostics.get("KARMA_XPU_DISABLE_OPTIX_DEVICE"))
    devices_val = (env_diagnostics.get("KARMA_XPU_DEVICES") or "").strip().casefold()
    if devices_val in ("cpu", "embree"):
        optix_disabled = True

    recommended_denoiser = "oidn" if optix_disabled else "optix"

    if optix_disabled:
        msg = "OptiX device is disabled via environment; recommending 'oidn' denoiser."
        checks.append(PreflightCheck("denoiser", "WARN", msg))
        warnings.append(msg)
    else:
        checks.append(
            PreflightCheck(
                "denoiser",
                "PASS",
                "OptiX not disabled; recommending 'optix' denoiser.",
            )
        )

    if _is_truthy_env(env_diagnostics.get("KARMA_XPU_DISABLE_EMBREE_DEVICE")):
        checks.append(
            PreflightCheck(
                "xpu_embree",
                "INFO",
                "KARMA_XPU_DISABLE_EMBREE_DEVICE is enabled (GPU-only XPU mode).",
            )
        )

    # 6b. Live NVIDIA GPU, driver >= 575 (Windows), VRAM eviction risk, & OptiX kernel cache probe.
    major_minor = (
        f"{version_tuple[0]}.{version_tuple[1]}"
        if version_tuple is not None and len(version_tuple) >= 2
        else "22.0"
    )
    hw = probe_hardware(major_minor=major_minor)
    if hw.gpus:
        if hw.driver_ok_h22 is False:
            msg = (
                f"NVIDIA driver {hw.driver_version} is below SideFX Houdini 22 recommended "
                "minimum (575+ on Windows / 570+ on Linux); Karma XPU OptiX stability may degrade."
            )
            checks.append(PreflightCheck("nvidia_driver", "WARN", msg))
            warnings.append(msg)
        else:
            checks.append(
                PreflightCheck(
                    "nvidia_driver",
                    "PASS",
                    f"{hw.summary_badge}",
                )
            )
        if hw.vram_eviction_risk in ("WARN", "CRITICAL"):
            msg = (
                f"GPU VRAM pressure is {hw.vram_eviction_risk} ({hw.gpus[0].vram_used_pct:.1f}% used). "
                "Karma XPU may evict geometry/textures or fall back to CPU; use Res Scale 0.5x or Focus Crop."
            )
            checks.append(PreflightCheck("vram_pressure", "WARN", msg))
            warnings.append(msg)
        else:
            checks.append(
                PreflightCheck(
                    "vram_pressure",
                    "PASS",
                    f"VRAM headroom healthy ({hw.gpus[0].vram_used_pct:.1f}% used on {hw.gpus[0].short_name}).",
                )
            )
        if hw.optix_cache_ready:
            checks.append(
                PreflightCheck(
                    "optix_kernel_cache",
                    "PASS",
                    f"Houdini {major_minor} OptiX pre-compiled cache warm ({hw.optix_cache_files} file(s)).",
                )
            )
        else:
            msg = (
                f"Houdini {major_minor} OptiX pre-compiled kernel cache is cold/missing at "
                f"{hw.optix_cache_dir or 'OptixCache'}; run Karma -> Pre-compile Shaders or "
                "'husk --precompile-shaders' to eliminate first-frame stalls."
            )
            checks.append(PreflightCheck("optix_kernel_cache", "INFO", msg))
    else:
        checks.append(
            PreflightCheck(
                "nvidia_driver",
                "INFO",
                "No NVIDIA GPU reported by nvidia-smi; Karma CPU Path Traced mode ready.",
            )
        )

    # 6c. Solaris viewport workflow preferences (read-only).
    panes_follow: bool | None = None
    update_bg: bool | None = None
    if hou_mod is not None and hasattr(hou_mod, "lop"):
        try:
            fn = getattr(hou_mod.lop, "panesFollowCurrentNode", None)
            if callable(fn):
                panes_follow = bool(fn())
        except Exception:
            panes_follow = None
        try:
            fn = getattr(hou_mod.lop, "updateRendererInBackground", None)
            if callable(fn):
                update_bg = bool(fn())
        except Exception:
            update_bg = None

    if panes_follow is True:
        checks.append(
            PreflightCheck(
                "solaris_pin_stage",
                "INFO",
                "panesFollowCurrentNode=True: selecting upstream LOPs recomposes the stage. Use 'Pin Stage' while lookdeving.",
            )
        )

    # 7. Compute overall readiness status.
    if not karma_xpu_available and not karma_cpu_available:
        status = "UNAVAILABLE"
    elif not karma_xpu_available or cat.source == "fallback":
        status = "DEGRADED"
    else:
        status = "READY"

    return PreflightReport(
        status=status,
        houdini_version=houdini_version,
        python_version=python_version,
        usd_version=usd_version,
        checks=checks,
        available_delegates=available_delegates,
        karma_xpu_available=karma_xpu_available,
        karma_cpu_available=karma_cpu_available,
        vulkan_available=vulkan_available,
        reference_grid_guard_needed=reference_grid_guard_needed,
        recommended_denoiser=recommended_denoiser,
        env_diagnostics=env_diagnostics,
        warnings=warnings,
        hardware=hw,
        panes_follow_current_node=panes_follow,
        update_renderer_in_background=update_bg,
    )
