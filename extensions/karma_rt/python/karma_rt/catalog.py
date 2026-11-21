"""Houdini 22 Solaris/Karma USD attribute, delegate, and node-type catalog."""

from __future__ import annotations

from dataclasses import dataclass, field
import fnmatch
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable, Sequence

KARMA_XPU_ALIASES: tuple[str, ...] = (
    "Karma XPU",
    "BRAY_HdKarmaXPU",
    "karma_xpu",
    "xpu",
)

KARMA_CPU_ALIASES: tuple[str, ...] = (
    "Karma CPU",
    "Karma",
    "BRAY_HdKarma",
    "karma_cpu",
    "cpu",
)

VULKAN_ALIASES: tuple[str, ...] = (
    "Houdini VK",
    "Vulkan",
    "HD_HoudiniRendererPlugin",
    "houdini_vk",
    "vk",
)

KARMA_ALL_ALIASES: tuple[str, ...] = (
    "Karma XPU",
    "BRAY_HdKarmaXPU",
    "Karma CPU",
    "Karma",
    "BRAY_HdKarma",
    "karma_xpu",
    "karma_cpu",
    "xpu",
    "cpu",
)

STORM_ALIASES: tuple[str, ...] = (
    "Storm",
    "HdStormRendererPlugin",
    "storm",
)

_ALIAS_GROUPS: dict[str, tuple[str, ...]] = {
    "karma_xpu": KARMA_XPU_ALIASES,
    "xpu": KARMA_XPU_ALIASES,
    "karma xpu": KARMA_XPU_ALIASES,
    "bray_hdkarmaxpu": KARMA_XPU_ALIASES,
    "karma_cpu": KARMA_CPU_ALIASES,
    "cpu": KARMA_CPU_ALIASES,
    "karma cpu": KARMA_CPU_ALIASES,
    "bray_hdkarma": KARMA_CPU_ALIASES,
    "karma": KARMA_ALL_ALIASES,
    "karma_all": KARMA_ALL_ALIASES,
    "karma_any": KARMA_ALL_ALIASES,
    "vulkan": VULKAN_ALIASES,
    "vk": VULKAN_ALIASES,
    "houdini_vk": VULKAN_ALIASES,
    "houdini vk": VULKAN_ALIASES,
    "hd_houdinirendererplugin": VULKAN_ALIASES,
    "storm": STORM_ALIASES,
    "hdstormrendererplugin": STORM_ALIASES,
}

DEFAULT_RESTART_RENDER_SETTINGS: tuple[str, ...] = (
    "karma:global:engine",
    "karma:global:overrideobject",
    "karma:global:dicingqualityscale",
    "karma:object:*",
    "renderingColorSpace",
    "disableMotionBlur",
    "houdini:fps",
)

DEFAULT_RESTART_CAMERA_SETTINGS: tuple[str, ...] = (
    "shutter:close",
    "shutter:open",
)

DEFAULT_RENDERER_LABELS: dict[str, str] = {
    "HD_HoudiniRendererPlugin": "Houdini VK",
    "BRAY_HdKarma": "Karma CPU",
    "BRAY_HdKarmaXPU": "Karma XPU",
    "HdStormRendererPlugin": "Storm",
}

TARGET_RENDERER_ENTRIES: tuple[str, ...] = (
    "BRAY_HdKarma_base",
    "BRAY_HdKarma",
    "BRAY_HdKarmaXPU",
    "HD_HoudiniRendererPlugin",
)

TARGET_LOP_NODE_TYPES: tuple[str, ...] = (
    "karmarendersettings",
    "imagefilter",
    "renderpass",
    "light::2.0",
    "scatterinstances",
    "karmablockerlightfilter",
    "texturemateriallibrary",
    "rendergeometrysettings",
    "liverender",
)

TARGET_V0_PARMS: dict[str, str] = {
    "xn__inputskarmalightequiangular_1kbff": "light::2.0",
    "enablecameramask": "scatterinstances",
}


@dataclass(frozen=True)
class KarmaAttrSpec:
    """Metadata for a Karma / UsdRenderSettings USD attribute."""

    usd_attr: str
    sdf_type: str
    zero_restart: bool
    category: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "usd_attr": self.usd_attr,
            "sdf_type": self.sdf_type,
            "zero_restart": self.zero_restart,
            "category": self.category,
            "description": self.description,
        }


_BASE_KNOWN_ATTRS: tuple[KarmaAttrSpec, ...] = (
    # Zero-restart safe IPR / progressive controls
    KarmaAttrSpec(
        usd_attr="karma:global:ipr_proxydepth",
        sdf_type="int",
        zero_restart=True,
        category="ipr",
        description="Interactive viewport proxy resolution downscale depth (2^n).",
    ),
    KarmaAttrSpec(
        usd_attr="karma:global:ipr_proxy_for_imagefilter",
        sdf_type="int",
        zero_restart=True,
        category="ipr",
        description="Minimum IPR proxy depth at which image filters/denoisers activate.",
    ),
    KarmaAttrSpec(
        usd_attr="karma:global:ipr_reserve_threads",
        sdf_type="int",
        zero_restart=True,
        category="ipr",
        description="CPU threads reserved for UI and scene interaction during IPR.",
    ),
    KarmaAttrSpec(
        usd_attr="karma:global:ipr_continuous_dicing",
        sdf_type="bool",
        zero_restart=True,
        category="ipr",
        description="Continuously re-dice displaced geometry during camera motion.",
    ),
    # Zero-restart safe sampling & convergence
    KarmaAttrSpec(
        usd_attr="karma:global:convergence_mode",
        sdf_type="token",
        zero_restart=True,
        category="sampling",
        description="Karma CPU convergence strategy ('Path Traced' avoids variance-mode overhead during IPR).",
    ),
    KarmaAttrSpec(
        usd_attr="karma:global:pathtracedsamples",
        sdf_type="int",
        zero_restart=True,
        category="sampling",
        description="Maximum path-traced samples per pixel.",
    ),
    KarmaAttrSpec(
        usd_attr="karma:global:samplesperpixel",
        sdf_type="int",
        zero_restart=True,
        category="sampling",
        description="Primary camera samples per pixel.",
    ),
    KarmaAttrSpec(
        usd_attr="karma:global:percentofsamples",
        sdf_type="float",
        zero_restart=True,
        category="sampling",
        description="Percentage multiplier applied to target sample counts (0-100).",
    ),
    KarmaAttrSpec(
        usd_attr="karma:global:pixeloracle",
        sdf_type="string",
        zero_restart=True,
        category="sampling",
        description="Adaptive sampling pixel oracle JSON specification.",
    ),
    # Zero-restart safe denoising / image filter
    KarmaAttrSpec(
        usd_attr="karma:global:imagefilter",
        sdf_type="string",
        zero_restart=True,
        category="denoising",
        description="Karma image filter pipeline JSON specification (OptiX / OIDN).",
    ),
    # Zero-restart safe shading & firefly controls
    KarmaAttrSpec(
        usd_attr="karma:global:russianroulette_cutoff",
        sdf_type="int",
        zero_restart=True,
        category="shading",
        description="Path depth at which Russian roulette termination begins.",
    ),
    KarmaAttrSpec(
        usd_attr="karma:global:constrainmaxrough",
        sdf_type="bool",
        zero_restart=True,
        category="shading",
        description="Constrain secondary bounce roughness to suppress specular fireflies.",
    ),
    KarmaAttrSpec(
        usd_attr="karma:global:colorlimit",
        sdf_type="float",
        zero_restart=True,
        category="shading",
        description="Maximum radiance clamp for direct and indirect samples.",
    ),
    KarmaAttrSpec(
        usd_attr="karma:global:colorlimitindlinked",
        sdf_type="bool",
        zero_restart=True,
        category="shading",
        description="When True, indirect bounces share colorlimit and colorlimitindirect is ignored.",
    ),
    KarmaAttrSpec(
        usd_attr="karma:global:colorlimitindirect",
        sdf_type="float",
        zero_restart=True,
        category="shading",
        description="Maximum radiance clamp for indirect bounce contributions (requires colorlimitindlinked=False).",
    ),
    KarmaAttrSpec(
        usd_attr="karma:global:mtlx_imagewidth",
        sdf_type="float",
        zero_restart=True,
        category="shading",
        description="Global MaterialX texture filter width multiplier (>1.0 uses smaller mip levels to save VRAM bandwidth).",
    ),
    KarmaAttrSpec(
        usd_attr="karma:global:offscreenquality",
        sdf_type="float",
        zero_restart=True,
        category="dicing",
        description="Shading/dicing quality multiplier for offscreen secondary-ray geometry (default 0.1; lower is faster).",
    ),
    # Zero-restart safe lighting controls
    KarmaAttrSpec(
        usd_attr="karma:global:light_sampling_mode",
        sdf_type="token",
        zero_restart=True,
        category="lighting",
        description="Light importance sampling strategy ('Light Tree' or 'Uniform').",
    ),
    KarmaAttrSpec(
        usd_attr="karma:global:light_sampling_quality",
        sdf_type="float",
        zero_restart=True,
        category="lighting",
        description="Light tree traversal quality threshold.",
    ),
    KarmaAttrSpec(
        usd_attr="karma:global:override_lighting",
        sdf_type="token",
        zero_restart=True,
        category="lighting",
        description="Viewport lighting override mode ('Off', 'Headlight', 'Dome Light', 'Emissive Objects').",
    ),
    KarmaAttrSpec(
        usd_attr="karma:global:head_ao_samples",
        sdf_type="int",
        zero_restart=True,
        category="lighting",
        description="Ambient occlusion samples per shade in Headlight lighting override mode.",
    ),
    KarmaAttrSpec(
        usd_attr="karma:global:head_ao_distance",
        sdf_type="float",
        zero_restart=True,
        category="lighting",
        description="Ambient occlusion ray distance in Headlight lighting override mode.",
    ),
    # Zero-restart safe camera / lens & render window settings
    KarmaAttrSpec(
        usd_attr="disableDepthOfField",
        sdf_type="bool",
        zero_restart=True,
        category="camera",
        description="Disable depth-of-field sampling without restarting Hydra.",
    ),
    KarmaAttrSpec(
        usd_attr="instantaneousShutter",
        sdf_type="bool",
        zero_restart=True,
        category="camera",
        description="Collapse motion blur shutter without triggering a Hydra restart (unlike disableMotionBlur).",
    ),
    KarmaAttrSpec(
        usd_attr="dataWindowNDC",
        sdf_type="float4",
        zero_restart=True,
        category="camera",
        description="Normalized crop window (minX, minY, maxX, maxY) for zero-restart Focus Crop rendering.",
    ),
    KarmaAttrSpec(
        usd_attr="resolution",
        sdf_type="int2",
        zero_restart=True,
        category="camera",
        description="Session viewport render resolution override (width, height) in LOP:Custom.",
    ),
    # Restart-triggering render & camera settings (from BRAY_HdKarma_base)
    KarmaAttrSpec(
        usd_attr="karma:global:engine",
        sdf_type="token",
        zero_restart=False,
        category="restart",
        description="Karma render engine selection (triggers delegate restart).",
    ),
    KarmaAttrSpec(
        usd_attr="karma:global:overrideobject",
        sdf_type="string",
        zero_restart=False,
        category="restart",
        description="Global object override pattern (triggers delegate restart).",
    ),
    KarmaAttrSpec(
        usd_attr="karma:global:dicingqualityscale",
        sdf_type="float",
        zero_restart=False,
        category="restart",
        description="Global geometry dicing quality scale (triggers delegate restart).",
    ),
    KarmaAttrSpec(
        usd_attr="karma:object:*",
        sdf_type="any",
        zero_restart=False,
        category="restart",
        description="Per-object Karma render settings wildcard (triggers delegate restart).",
    ),
    KarmaAttrSpec(
        usd_attr="renderingColorSpace",
        sdf_type="token",
        zero_restart=False,
        category="restart",
        description="Working rendering color space (triggers delegate restart).",
    ),
    KarmaAttrSpec(
        usd_attr="disableMotionBlur",
        sdf_type="bool",
        zero_restart=False,
        category="restart",
        description="Disable motion blur sampling (triggers delegate restart).",
    ),
    KarmaAttrSpec(
        usd_attr="houdini:fps",
        sdf_type="double",
        zero_restart=False,
        category="restart",
        description="Scene frames-per-second rate (triggers delegate restart).",
    ),
    KarmaAttrSpec(
        usd_attr="shutter:open",
        sdf_type="double",
        zero_restart=False,
        category="camera_restart",
        description="Camera shutter open time offset (triggers delegate restart).",
    ),
    KarmaAttrSpec(
        usd_attr="shutter:close",
        sdf_type="double",
        zero_restart=False,
        category="camera_restart",
        description="Camera shutter close time offset (triggers delegate restart).",
    ),
)


@dataclass
class H22Catalog:
    """Verified Houdini 22 Solaris/Karma delegate, restart-rule, and node catalog."""

    source: str
    hfs_path: str | None
    houdini_version: str | None
    usd_version: str | None
    restart_render_settings: tuple[str, ...]
    restart_camera_settings: tuple[str, ...]
    renderer_labels: dict[str, str] = field(default_factory=dict)
    renderer_names: dict[str, str] = field(default_factory=dict)
    verified_node_types: dict[str, bool] = field(default_factory=dict)
    verified_v0_symbols: dict[str, bool] = field(default_factory=dict)
    known_attrs: dict[str, KarmaAttrSpec] = field(default_factory=dict)

    def is_restart_attribute(
        self,
        attr_name: str,
        *,
        include_camera: bool = True,
    ) -> bool:
        """Return True if modifying ``attr_name`` triggers a Karma Hydra restart."""
        name = attr_name.strip()
        if not name:
            return False
        patterns: Iterable[str] = (
            (*self.restart_render_settings, *self.restart_camera_settings)
            if include_camera
            else self.restart_render_settings
        )
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "hfs_path": self.hfs_path,
            "houdini_version": self.houdini_version,
            "usd_version": self.usd_version,
            "restart_render_settings": list(self.restart_render_settings),
            "restart_camera_settings": list(self.restart_camera_settings),
            "renderer_labels": dict(self.renderer_labels),
            "renderer_names": dict(self.renderer_names),
            "verified_node_types": dict(self.verified_node_types),
            "verified_v0_symbols": dict(self.verified_v0_symbols),
            "known_attrs": {k: v.to_dict() for k, v in self.known_attrs.items()},
        }


def _dedupe_str(items: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if not isinstance(item, str):
            continue
        cleaned = item.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            ordered.append(cleaned)
    return tuple(ordered)


def _strip_json_comments(raw: str) -> str:
    """Strip C/C++ comments outside quoted strings for relaxed Houdini JSON files."""
    token_re = re.compile(
        r'"(?:\\.|[^"\\])*"|//[^\r\n]*|/\*[\s\S]*?\*/',
        re.MULTILINE,
    )

    def _replace(match: re.Match[str]) -> str:
        text = match.group(0)
        return text if text.startswith('"') else ""

    cleaned = token_re.sub(_replace, raw)
    # Remove trailing commas before closing brackets/braces if present.
    return re.sub(r",(\s*[}\]])", r"\1", cleaned)


def _load_json_relaxed(path: Path) -> Any:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            data = json.loads(_strip_json_comments(raw))
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, (dict, list)) else None


def _resolve_hou_module(hou_module: Any) -> Any:
    if hou_module is not None:
        return hou_module
    try:
        import hou  # type: ignore[import-not-found]

        return hou
    except Exception:
        return None


def _resolve_hfs_path(hou_module: Any, hfs_override: str | os.PathLike[str] | None) -> str | None:
    if hfs_override is not None:
        text = os.fspath(hfs_override).strip()
        return text or None
    env_hfs = os.environ.get("HFS", "").strip()
    if env_hfs:
        return env_hfs
    if hou_module is not None:
        try:
            hou_hfs = str(hou_module.getenv("HFS") or "").strip()
            if hou_hfs:
                return hou_hfs
        except Exception:
            pass
        try:
            expanded = str(hou_module.expandString("$HFS") or "").strip()
            if expanded and expanded != "$HFS":
                return expanded
        except Exception:
            pass
    return None


def _detect_houdini_version(hou_module: Any) -> str | None:
    if hou_module is None:
        return None
    try:
        ver_str = hou_module.applicationVersionString()
        if ver_str:
            return str(ver_str)
    except Exception:
        pass
    try:
        ver_tuple = hou_module.applicationVersion()
        if ver_tuple:
            return ".".join(str(part) for part in ver_tuple)
    except Exception:
        pass
    return None


def _detect_usd_version() -> str | None:
    try:
        from pxr import Usd  # type: ignore[import-not-found]

        ver = Usd.GetVersion()
        if isinstance(ver, (tuple, list)):
            return ".".join(str(part) for part in ver)
        return str(ver)
    except Exception:
        return None


def _extract_renderer_entries(raw_json: Any) -> dict[str, dict[str, Any]]:
    """Normalize top-level dict, nested ``renderers``, or list entries from UsdRenderers.json."""
    entries: dict[str, dict[str, Any]] = {}
    candidate: Any = raw_json
    if isinstance(raw_json, dict) and isinstance(raw_json.get("renderers"), (dict, list)):
        candidate = raw_json["renderers"]

    if isinstance(candidate, dict):
        for key, value in candidate.items():
            if isinstance(key, str) and isinstance(value, dict):
                entries[key] = value
    elif isinstance(candidate, list):
        for item in candidate:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("id")
            if isinstance(name, str) and name.strip():
                entries[name.strip()] = item
    return entries


def _collect_inherited_list(
    entries: dict[str, dict[str, Any]],
    entry_names: Sequence[str],
    field_name: str,
) -> tuple[str, ...]:
    collected: list[str] = []
    visited: set[str] = set()

    def _visit(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        spec = entries.get(name)
        if not isinstance(spec, dict):
            return
        for inherit_key in ("base", "inherit", "inherits", "extends"):
            base = spec.get(inherit_key)
            if isinstance(base, str):
                _visit(base)
            elif isinstance(base, list):
                for base_name in base:
                    if isinstance(base_name, str):
                        _visit(base_name)
        values = spec.get(field_name)
        if isinstance(values, list):
            for item in values:
                if isinstance(item, str):
                    collected.append(item)

    for entry_name in entry_names:
        _visit(entry_name)
    return _dedupe_str(collected)


def _probe_lop_node_types_and_v0(
    hou_module: Any,
) -> tuple[dict[str, bool], dict[str, bool]]:
    """Read-only inspection of LOP NodeTypes and V0 parm templates (zero node creation)."""
    verified_nodes: dict[str, bool] = {name: False for name in TARGET_LOP_NODE_TYPES}
    verified_v0: dict[str, bool] = {parm: False for parm in TARGET_V0_PARMS}

    if hou_module is None:
        return verified_nodes, verified_v0

    try:
        lop_cat = hou_module.lopNodeTypeCategory()
        all_types = lop_cat.nodeTypes() or {}
    except Exception:
        return verified_nodes, verified_v0

    resolved_types: dict[str, Any] = {}

    for target_name in TARGET_LOP_NODE_TYPES:
        node_type = all_types.get(target_name)
        if node_type is None:
            try:
                node_type = lop_cat.nodeType(target_name)
            except Exception:
                node_type = None
        if node_type is None and "::" not in target_name:
            prefix = f"{target_name}::"
            for registered_name, candidate_type in all_types.items():
                if registered_name == target_name or registered_name.startswith(prefix):
                    node_type = candidate_type
                    break
        if node_type is not None:
            verified_nodes[target_name] = True
            resolved_types[target_name] = node_type

    ptg_cache: dict[str, Any] = {}
    for parm_name, owner_node in TARGET_V0_PARMS.items():
        node_type = resolved_types.get(owner_node)
        if node_type is None:
            continue
        if owner_node not in ptg_cache:
            try:
                ptg_cache[owner_node] = node_type.parmTemplateGroup()
            except Exception:
                ptg_cache[owner_node] = None
        ptg = ptg_cache[owner_node]
        if ptg is None:
            continue
        try:
            verified_v0[parm_name] = ptg.find(parm_name) is not None
        except Exception:
            verified_v0[parm_name] = False

    return verified_nodes, verified_v0


def _build_known_attrs(
    restart_render: tuple[str, ...],
    restart_camera: tuple[str, ...],
) -> dict[str, KarmaAttrSpec]:
    all_patterns = (*restart_render, *restart_camera)
    known: dict[str, KarmaAttrSpec] = {}
    for spec in _BASE_KNOWN_ATTRS:
        is_restart = any(fnmatch.fnmatchcase(spec.usd_attr, pat) for pat in all_patterns)
        known[spec.usd_attr] = KarmaAttrSpec(
            usd_attr=spec.usd_attr,
            sdf_type=spec.sdf_type,
            zero_restart=not is_restart,
            category=spec.category,
            description=spec.description,
        )
    # Include any newly discovered restart attributes from live UsdRenderers.json.
    for pat in restart_render:
        if pat not in known:
            known[pat] = KarmaAttrSpec(
                usd_attr=pat,
                sdf_type="any",
                zero_restart=False,
                category="restart",
                description="Restart-triggering render setting from live UsdRenderers.json.",
            )
    for pat in restart_camera:
        if pat not in known:
            known[pat] = KarmaAttrSpec(
                usd_attr=pat,
                sdf_type="any",
                zero_restart=False,
                category="camera_restart",
                description="Restart-triggering camera setting from live UsdRenderers.json.",
            )
    return known


def load_h22_catalog(
    hou_module: Any = None,
    hfs_override: str | os.PathLike[str] | None = None,
) -> H22Catalog:
    """Load Houdini 22 Solaris/Karma restart rules, delegates, and node metadata."""
    hou_mod = _resolve_hou_module(hou_module)
    hfs_path = _resolve_hfs_path(hou_mod, hfs_override)
    houdini_version = _detect_houdini_version(hou_mod)
    usd_version = _detect_usd_version()

    source = "fallback"
    restart_render = DEFAULT_RESTART_RENDER_SETTINGS
    restart_camera = DEFAULT_RESTART_CAMERA_SETTINGS
    renderer_labels: dict[str, str] = dict(DEFAULT_RENDERER_LABELS)

    if hfs_path is not None:
        renderers_json_path = Path(hfs_path) / "houdini" / "UsdRenderers.json"
        parsed = _load_json_relaxed(renderers_json_path) if renderers_json_path.is_file() else None
        if parsed is not None:
            entries = _extract_renderer_entries(parsed)
            karma_entries = ("BRAY_HdKarma_base", "BRAY_HdKarma", "BRAY_HdKarmaXPU")
            found_render = _collect_inherited_list(entries, karma_entries, "restartrendersettings")
            found_camera = _collect_inherited_list(entries, karma_entries, "restartcamerasettings")

            if any(name in entries for name in TARGET_RENDERER_ENTRIES):
                source = "live"
                if found_render:
                    restart_render = found_render
                if found_camera:
                    restart_camera = found_camera

                for delegate_name, spec in entries.items():
                    if delegate_name.endswith("_base"):
                        continue
                    label = spec.get("displayname") or spec.get("label")
                    if isinstance(label, str) and label.strip():
                        renderer_labels[delegate_name] = label.strip()

    # Overlay live Hydra delegates reported by hou.lop when running inside Houdini.
    if hou_mod is not None:
        try:
            live_names = tuple(hou_mod.lop.availableRendererNames())
            live_labels = tuple(hou_mod.lop.availableRendererLabels())
            if live_names and len(live_names) == len(live_labels):
                renderer_labels = {
                    str(name): str(label)
                    for name, label in zip(live_names, live_labels)
                }
        except Exception:
            pass

    renderer_names = {label: name for name, label in renderer_labels.items()}
    verified_node_types, verified_v0_symbols = _probe_lop_node_types_and_v0(hou_mod)
    known_attrs = _build_known_attrs(restart_render, restart_camera)

    return H22Catalog(
        source=source,
        hfs_path=hfs_path,
        houdini_version=houdini_version,
        usd_version=usd_version,
        restart_render_settings=restart_render,
        restart_camera_settings=restart_camera,
        renderer_labels=renderer_labels,
        renderer_names=renderer_names,
        verified_node_types=verified_node_types,
        verified_v0_symbols=verified_v0_symbols,
        known_attrs=known_attrs,
    )


def _expand_preferred_candidates(preferred: str | Iterable[str]) -> list[str]:
    raw_items: list[str]
    if isinstance(preferred, str):
        raw_items = [preferred]
    else:
        raw_items = [str(item) for item in preferred]

    expanded: list[str] = []
    seen: set[str] = set()

    def _add(val: str) -> None:
        key = val.casefold()
        if val and key not in seen:
            seen.add(key)
            expanded.append(val)

    for item in raw_items:
        stripped = item.strip()
        if not stripped:
            continue
        _add(stripped)
        group = _ALIAS_GROUPS.get(stripped.casefold())
        if group:
            for alias in group:
                _add(alias)
        mapped_label = DEFAULT_RENDERER_LABELS.get(stripped)
        if mapped_label:
            _add(mapped_label)

    return expanded


def resolve_delegate_label(
    available_labels: Iterable[str],
    preferred: str | Iterable[str],
) -> str | None:
    """Resolve a preferred delegate kind, name, or alias to a live Hydra label."""
    labels = [str(lbl) for lbl in available_labels if str(lbl).strip()]
    if not labels:
        return None

    candidates = _expand_preferred_candidates(preferred)
    if not candidates:
        return None

    # Pass 1: exact case-sensitive match.
    label_set = set(labels)
    for candidate in candidates:
        if candidate in label_set:
            return candidate

    # Pass 2: case-insensitive exact match.
    lower_map = {lbl.casefold(): lbl for lbl in labels}
    for candidate in candidates:
        matched = lower_map.get(candidate.casefold())
        if matched is not None:
            return matched

    # Pass 3: unambiguous case-insensitive substring match for descriptive labels.
    for candidate in candidates:
        cand_lower = candidate.casefold()
        if len(cand_lower) < 4:
            continue
        for lbl in labels:
            if cand_lower in lbl.casefold():
                return lbl

    return None
