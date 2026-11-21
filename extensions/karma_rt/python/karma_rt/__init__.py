"""KarmaRT: Zero-restart Solaris/Karma interactive lookdev toolkit for Houdini 22."""

from __future__ import annotations

import importlib
from typing import Any

__version__ = "2.0.0"

_LAZY_SUBMODULES: tuple[str, ...] = (
    "catalog",
    "hardware",
    "preflight",
    "presets",
)

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "H22Catalog": ("catalog", "H22Catalog"),
    "KARMA_ALL_ALIASES": ("catalog", "KARMA_ALL_ALIASES"),
    "KARMA_CPU_ALIASES": ("catalog", "KARMA_CPU_ALIASES"),
    "KARMA_XPU_ALIASES": ("catalog", "KARMA_XPU_ALIASES"),
    "KarmaAttrSpec": ("catalog", "KarmaAttrSpec"),
    "VULKAN_ALIASES": ("catalog", "VULKAN_ALIASES"),
    "load_h22_catalog": ("catalog", "load_h22_catalog"),
    "resolve_delegate_label": ("catalog", "resolve_delegate_label"),
    "GpuDeviceInfo": ("hardware", "GpuDeviceInfo"),
    "HardwareSnapshot": ("hardware", "HardwareSnapshot"),
    "probe_hardware": ("hardware", "probe_hardware"),
    "PreflightCheck": ("preflight", "PreflightCheck"),
    "PreflightReport": ("preflight", "PreflightReport"),
    "run_preflight": ("preflight", "run_preflight"),
    "AUTO_TURBO_OVERRIDES": ("presets", "AUTO_TURBO_OVERRIDES"),
    "FOCUS_CROP_REGIONS": ("presets", "FOCUS_CROP_REGIONS"),
    "SUPPORTED_RESOLUTION_SCALES": ("presets", "SUPPORTED_RESOLUTION_SCALES"),
    "PresetSpec": ("presets", "PresetSpec"),
    "build_denoise_imagefilter_json": ("presets", "build_denoise_imagefilter_json"),
    "build_focus_crop_override": ("presets", "build_focus_crop_override"),
    "build_pixel_oracle_json": ("presets", "build_pixel_oracle_json"),
    "build_resolution_scale_override": ("presets", "build_resolution_scale_override"),
    "get_presets": ("presets", "get_presets"),
    "strip_builtin_denoiser_if_usd_image_filters": ("presets", "strip_builtin_denoiser_if_usd_image_filters"),
    "validate_overrides_zero_restart": ("presets", "validate_overrides_zero_restart"),
    "validate_preset_zero_restart": ("presets", "validate_preset_zero_restart"),
}

__all__ = [
    "__version__",
    *_LAZY_SUBMODULES,
    *_LAZY_EXPORTS.keys(),
]


def __getattr__(name: str) -> Any:
    if name in _LAZY_SUBMODULES:
        return importlib.import_module(f"{__name__}.{name}")
    target = _LAZY_EXPORTS.get(name)
    if target is not None:
        submod_name, attr_name = target
        module = importlib.import_module(f"{__name__}.{submod_name}")
        return getattr(module, attr_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
