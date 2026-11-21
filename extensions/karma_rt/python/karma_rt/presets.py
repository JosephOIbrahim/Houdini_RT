"""Zero-restart KarmaRT v2.0 physics-verified lookdev presets, focus crops, and validators.

Every USD attribute and default in this module is grounded in Houdini 22 official
SideFX documentation and empirical RTX 4090 Karma XPU/CPU benchmarks:
- ``colorlimitindlinked = False`` paired with ``colorlimitindirect`` so indirect
  firefly clamping actually takes effect (nodes/lop/rendersettings.md:432-436).
- ``convergence_mode = 'Path Traced'`` for ~20x faster interactive Karma CPU
  preview (solaris/kug/sampling_cpu.md:80-93).
- Square-aligned primary samples (4, 9, 36) + perceptual OCIO variance oracle
  (``ociomode = 1``) (solaris/kug/noise_xpu.md:33-34; solaris/support/filters.md:137).
- Zero-restart ``mtlx_imagewidth``, ``offscreenquality``, ``instantaneousShutter``,
  ``override_lighting = 'Headlight'`` Clay AO, ``dataWindowNDC`` Focus Crop, and
  scaled ``resolution``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Mapping, Sequence

from .catalog import H22Catalog

SUPPORTED_DENOISER_ENGINES: tuple[str, ...] = ("optix", "oidn")

# Strictly zero-restart transient boost applied during camera tumble / live edit bursts.
AUTO_TURBO_OVERRIDES: dict[str, tuple[str, Any]] = {
    "karma:global:ipr_proxydepth": ("int", 4),
    "karma:global:ipr_proxy_for_imagefilter": ("int", 4),
    "karma:global:ipr_reserve_threads": ("int", 4),
    "karma:global:percentofsamples": ("float", 25.0),
    "karma:global:mtlx_imagewidth": ("float", 3.0),
    "karma:global:offscreenquality": ("float", 0.02),
    "instantaneousShutter": ("bool", True),
}

# Zero-restart normalized dataWindowNDC lookdev focus crops (minX, minY, maxX, maxY).
FOCUS_CROP_REGIONS: dict[str, tuple[str, tuple[float, float, float, float]]] = {
    "full": ("Full Frame (100%)", (0.0, 0.0, 1.0, 1.0)),
    "center_50": ("Center 50% (4x Faster)", (0.25, 0.25, 0.75, 0.75)),
    "center_33": ("Center 33% (9x Faster)", (0.3333, 0.3333, 0.6667, 0.6667)),
}

SUPPORTED_RESOLUTION_SCALES: tuple[float, ...] = (1.0, 0.75, 0.5, 0.25)


def build_pixel_oracle_json(
    mode: str = "variance",
    minrays: int = 4,
    variance: float = 0.02,
    plane: str = "C",
    ociomode: int = 1,
    ociodisplay: str = "sRGB - Display",
    ocioview: str = "",
    ociocolorspace: str = "default",
) -> str:
    """Build a Houdini 22 Karma ``karma:global:pixeloracle`` JSON string.

    Defaults to ``ociomode=1`` so Karma evaluates variance in perceptual OCIO
    Display/View space rather than linear gamma 2.0 (solaris/support/filters.md:137).
    """
    if int(minrays) < 1:
        raise ValueError(f"minrays must be >= 1, got {minrays!r}")
    if float(variance) < 0.0:
        raise ValueError(f"variance must be >= 0.0, got {variance!r}")

    payload = [
        str(mode),
        {
            "minrays": int(minrays),
            "plane": str(plane),
            "variance": float(variance),
            "ociomode": int(ociomode),
            "ociodisplay": str(ociodisplay),
            "ocioview": str(ocioview),
            "ociocolorspace": str(ociocolorspace),
        },
    ]
    return json.dumps(payload)


def build_denoise_imagefilter_json(
    engine: str = "optix",
    use_albedo: bool = True,
    use_n: bool = True,
    forcecpu: bool = False,
    aovs: Sequence[str] = ("C",),
) -> str:
    """Build a Houdini 22 Karma ``karma:global:imagefilter`` denoise JSON string."""
    engine_norm = str(engine).strip().lower()
    if engine_norm not in SUPPORTED_DENOISER_ENGINES:
        raise ValueError(
            f"Unsupported denoiser engine {engine!r}; expected one of {SUPPORTED_DENOISER_ENGINES}."
        )
    aov_list = [str(aov) for aov in aovs if str(aov).strip()] or ["C"]

    payload = [
        [
            "denoise",
            {
                "engine": engine_norm,
                "use_albedo_input": bool(use_albedo),
                "use_n_input": bool(use_n),
                "forcecpu": bool(forcecpu),
                "aovs": aov_list,
            },
        ]
    ]
    return json.dumps(payload)


def build_focus_crop_override(region_id: str) -> dict[str, tuple[str, Any]]:
    """Build a zero-restart ``dataWindowNDC`` crop override (empty for ``full``)."""
    key = str(region_id or "full").strip().lower()
    if key not in FOCUS_CROP_REGIONS:
        raise ValueError(
            f"Unsupported focus crop region {region_id!r}; expected one of {tuple(FOCUS_CROP_REGIONS)}."
        )
    if key == "full":
        return {}
    _label, ndc = FOCUS_CROP_REGIONS[key]
    return {"dataWindowNDC": ("float4", tuple(float(c) for c in ndc))}


def build_resolution_scale_override(
    base_resolution: Sequence[int] | None,
    scale: float,
) -> dict[str, tuple[str, Any]]:
    """Build a zero-restart scaled ``resolution`` ``int2`` override from production resolution."""
    factor = float(scale)
    if factor <= 0.0 or factor > 2.0:
        raise ValueError(f"Resolution scale must be in (0.0, 2.0], got {scale!r}")
    if abs(factor - 1.0) < 1e-4 or not base_resolution or len(base_resolution) < 2:
        return {}
    base_w, base_h = int(base_resolution[0]), int(base_resolution[1])
    if base_w <= 0 or base_h <= 0:
        return {}
    scaled_w = max(160, (int(round(base_w * factor)) // 2) * 2)
    scaled_h = max(90, (int(round(base_h * factor)) // 2) * 2)
    return {"resolution": ("int2", (scaled_w, scaled_h))}


def strip_builtin_denoiser_if_usd_image_filters(
    overrides: Mapping[str, tuple[str, Any]],
    *,
    has_usd_image_filters: bool,
) -> dict[str, tuple[str, Any]]:
    """Avoid double-denoising when H22 USD ``husk:orderedImageFilters`` is already wired."""
    cleaned = dict(overrides)
    if has_usd_image_filters and "karma:global:imagefilter" in cleaned:
        del cleaned["karma:global:imagefilter"]
    return cleaned


@dataclass(frozen=True)
class PresetSpec:
    """Declarative viewport & USD render-settings override preset."""

    id: str
    label: str
    description: str
    target_delegate_kind: str
    usd_overrides: dict[str, tuple[str, Any]] = field(default_factory=dict)
    viewport_options: dict[str, Any] = field(default_factory=dict)
    allow_restart_attrs: bool = False
    clears_overrides: bool = False


def get_presets(denoiser_engine: str = "optix") -> dict[str, PresetSpec]:
    """Return the built-in KarmaRT v2.0 presets parameterized by denoiser engine."""
    denoise_json = build_denoise_imagefilter_json(engine=denoiser_engine)

    return {
        "live_draft": PresetSpec(
            id="live_draft",
            label="Live Draft",
            description=(
                "Max FPS interactive Karma XPU/CPU lookdev: 9 square-aligned samples, "
                "Path Traced CPU parity, perceptual OCIO variance oracle, active indirect "
                "color clamp, mip-shrunk textures, zero-restart motion suppression, and live AI denoise."
            ),
            target_delegate_kind="karma_xpu",
            usd_overrides={
                "karma:global:ipr_proxydepth": ("int", 3),
                "karma:global:ipr_proxy_for_imagefilter": ("int", 2),
                "karma:global:ipr_reserve_threads": ("int", 3),
                "karma:global:ipr_continuous_dicing": ("bool", False),
                "karma:global:convergence_mode": ("token", "Path Traced"),
                "karma:global:pathtracedsamples": ("int", 9),
                "karma:global:samplesperpixel": ("int", 9),
                "karma:global:pixeloracle": (
                    "string",
                    build_pixel_oracle_json(mode="variance", minrays=4, variance=0.05, ociomode=1),
                ),
                "karma:global:russianroulette_cutoff": ("int", 2),
                "karma:global:constrainmaxrough": ("bool", True),
                "karma:global:colorlimitindlinked": ("bool", False),
                "karma:global:colorlimitindirect": ("float", 4.0),
                "karma:global:mtlx_imagewidth": ("float", 2.0),
                "karma:global:offscreenquality": ("float", 0.02),
                "disableDepthOfField": ("bool", True),
                "instantaneousShutter": ("bool", True),
                "karma:global:imagefilter": ("string", denoise_json),
            },
            viewport_options={
                "clean_overlays": True,
                "show_hydra_procedurals": None,
            },
            allow_restart_attrs=False,
            clears_overrides=False,
        ),
        "interactive_lookdev": PresetSpec(
            id="interactive_lookdev",
            label="Interactive Lookdev",
            description=(
                "Balanced shader & lighting lookdev: 36 square-aligned samples, Light Tree, "
                "Path Traced CPU parity, perceptual OCIO variance oracle, shot DoF honored, "
                "and live AI denoising (zero-restart)."
            ),
            target_delegate_kind="karma_xpu",
            usd_overrides={
                "karma:global:ipr_proxydepth": ("int", 2),
                "karma:global:ipr_proxy_for_imagefilter": ("int", 1),
                "karma:global:ipr_reserve_threads": ("int", 2),
                "karma:global:ipr_continuous_dicing": ("bool", False),
                "karma:global:convergence_mode": ("token", "Path Traced"),
                "karma:global:pathtracedsamples": ("int", 36),
                "karma:global:samplesperpixel": ("int", 36),
                "karma:global:pixeloracle": (
                    "string",
                    build_pixel_oracle_json(mode="variance", minrays=8, variance=0.02, ociomode=1),
                ),
                "karma:global:russianroulette_cutoff": ("int", 4),
                "karma:global:constrainmaxrough": ("bool", True),
                "karma:global:colorlimitindlinked": ("bool", False),
                "karma:global:colorlimitindirect": ("float", 10.0),
                "karma:global:mtlx_imagewidth": ("float", 1.0),
                "karma:global:offscreenquality": ("float", 0.05),
                "karma:global:light_sampling_mode": ("token", "Light Tree"),
                "disableDepthOfField": ("bool", False),
                "karma:global:imagefilter": ("string", denoise_json),
            },
            viewport_options={
                "clean_overlays": True,
                "show_hydra_procedurals": True,
            },
            allow_restart_attrs=False,
            clears_overrides=False,
        ),
        "clay_headlight": PresetSpec(
            id="clay_headlight",
            label="Clay / Headlight AO",
            description=(
                "Ultra-fast Karma silhouette & layout preview using zero-restart Headlight AO "
                "(override_lighting='Headlight') without touching scene lights in /shot/LGT."
            ),
            target_delegate_kind="karma_xpu",
            usd_overrides={
                "karma:global:ipr_proxydepth": ("int", 3),
                "karma:global:ipr_proxy_for_imagefilter": ("int", 2),
                "karma:global:ipr_reserve_threads": ("int", 3),
                "karma:global:ipr_continuous_dicing": ("bool", False),
                "karma:global:convergence_mode": ("token", "Path Traced"),
                "karma:global:pathtracedsamples": ("int", 9),
                "karma:global:samplesperpixel": ("int", 9),
                "karma:global:override_lighting": ("token", "Headlight"),
                "karma:global:head_ao_samples": ("int", 4),
                "karma:global:head_ao_distance": ("float", 5.0),
                "karma:global:offscreenquality": ("float", 0.02),
                "disableDepthOfField": ("bool", True),
                "instantaneousShutter": ("bool", True),
                "karma:global:imagefilter": ("string", denoise_json),
            },
            viewport_options={
                "clean_overlays": True,
                "show_hydra_procedurals": None,
            },
            allow_restart_attrs=False,
            clears_overrides=False,
        ),
        "final_parity": PresetSpec(
            id="final_parity",
            label="Final Parity",
            description=(
                "Full Karma parity: clears all viewport overrides so the Solaris viewer matches "
                "production USD Render ROP / husk disk output 1:1."
            ),
            target_delegate_kind="karma_xpu",
            usd_overrides={},
            viewport_options={
                "show_hydra_procedurals": True,
                "show_render_purpose": True,
                "clean_overlays": False,
            },
            allow_restart_attrs=False,
            clears_overrides=True,
        ),
        "vulkan_layout": PresetSpec(
            id="vulkan_layout",
            label="Vulkan Layout",
            description=(
                "Houdini VK 60+ fps layout viewport with Vulkan hardware raytracing, denoising, "
                "and fast interactive sampling."
            ),
            target_delegate_kind="vulkan",
            usd_overrides={},
            viewport_options={
                "vk_raytracing": True,
                "vk_denoising": True,
                "vk_fast_interactive": True,
                "show_hydra_procedurals": False,
            },
            allow_restart_attrs=False,
            clears_overrides=True,
        ),
    }


def validate_overrides_zero_restart(
    overrides: Mapping[str, Any],
    catalog: H22Catalog,
    allow_restart: bool = False,
) -> list[str]:
    """Validate that USD override attributes do not trigger a Karma Hydra restart."""
    violations = [
        attr_name
        for attr_name in overrides
        if catalog.is_restart_attribute(attr_name)
    ]
    if violations and not allow_restart:
        joined = ", ".join(violations)
        raise ValueError(
            f"Zero-restart override violation; attribute(s) trigger Karma Hydra restart: {joined}"
        )
    return violations


def validate_preset_zero_restart(
    preset: PresetSpec,
    catalog: H22Catalog,
) -> list[str]:
    """Validate a ``PresetSpec`` against the active Houdini 22 restart catalog."""
    return validate_overrides_zero_restart(
        preset.usd_overrides,
        catalog,
        allow_restart=preset.allow_restart_attrs,
    )
