"""Zero-restart Solaris LOP:Custom session override engine and .hip lifecycle guard.

Targets strictly ``hou.lopViewportOverridesLayer.Custom`` (``LOP:Custom``) without
creating LOP nodes, touching ``hou.LopPostLayer``, or mutating production
``karmarendersettings`` parameters.
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

PROVENANCE_KEY = "karma_rt_session_owner"
EPHEMERAL_SETTINGS_PATH = "/__KarmaRT_ViewportSettings__"

_TOOL_NAME = "karma_rt"

# Live-verified Houdini 22.0.504 BRAY_HdKarma_base restart triggers.
_DEFAULT_RESTART_ATTRS: frozenset[str] = frozenset(
    {
        "karma:global:engine",
        "karma:global:overrideobject",
        "karma:global:dicingqualityscale",
        "renderingColorSpace",
        "disableMotionBlur",
        "houdini:fps",
        "shutter:open",
        "shutter:close",
    }
)
_DEFAULT_RESTART_PREFIXES: tuple[str, ...] = ("karma:object:",)

# Live-verified Houdini 22.0.504 KarmaRenderSettingsAPI & UsdRender.Settings types.
_KNOWN_SDF_TYPE_NAMES: dict[str, str] = {
    "karma:global:ipr_proxydepth": "int",
    "karma:global:ipr_proxy_for_imagefilter": "int",
    "karma:global:ipr_reserve_threads": "int",
    "karma:global:ipr_continuous_dicing": "bool",
    "karma:global:convergence_mode": "token",
    "karma:global:pathtracedsamples": "int",
    "karma:global:samplesperpixel": "int",
    "karma:global:percentofsamples": "float",
    "karma:global:pixeloracle": "string",
    "karma:global:imagefilter": "string",
    "karma:global:russianroulette_cutoff": "int",
    "karma:global:constrainmaxrough": "bool",
    "karma:global:colorlimit": "float",
    "karma:global:colorlimitindlinked": "bool",
    "karma:global:colorlimitindirect": "float",
    "karma:global:mtlx_imagewidth": "float",
    "karma:global:offscreenquality": "float",
    "karma:global:light_sampling_mode": "token",
    "karma:global:light_sampling_quality": "float",
    "karma:global:override_lighting": "token",
    "karma:global:head_ao_samples": "int",
    "karma:global:head_ao_distance": "float",
    "disableDepthOfField": "bool",
    # Common restart-guarded or supplementary RenderSettings attributes.
    "karma:global:engine": "token",
    "karma:global:overrideobject": "token",
    "karma:global:dicingqualityscale": "float",
    "karma:global:diffuselimit": "int",
    "karma:global:reflectlimit": "int",
    "karma:global:refractlimit": "int",
    "karma:global:volumelimit": "int",
    "karma:global:sss_limit": "int",
    "karma:global:maxrayrecursion": "int",
    "karma:global:diffusequality": "float",
    "karma:global:reflectquality": "float",
    "karma:global:refractquality": "float",
    "karma:global:volumequality": "float",
    "karma:global:sssquality": "float",
    "karma:global:convergencemode": "token",
    "karma:global:progressivepasses": "int",
    "karma:global:bucketorder": "token",
    "karma:global:bucketsize": "int",
    "karma:global:causticsenable": "bool",
    "karma:global:causticsroughnessclamp": "float",
    "renderingColorSpace": "token",
    "disableMotionBlur": "bool",
    "houdini:fps": "float",
    "resolution": "int2",
    "pixelAspectRatio": "float",
    "aspectRatioConformPolicy": "token",
    "dataWindowNDC": "float4",
    "instantaneousShutter": "bool",
}

_SDF_TYPE_ATTR_MAP: dict[str, str] = {
    "bool": "Bool",
    "int": "Int",
    "int2": "Int2",
    "int3": "Int3",
    "int4": "Int4",
    "float": "Float",
    "float2": "Float2",
    "float3": "Float3",
    "float4": "Float4",
    "double": "Double",
    "double2": "Double2",
    "double3": "Double3",
    "double4": "Double4",
    "string": "String",
    "token": "Token",
    "asset": "Asset",
    "bool[]": "BoolArray",
    "int[]": "IntArray",
    "float[]": "FloatArray",
    "double[]": "DoubleArray",
    "string[]": "StringArray",
    "token[]": "TokenArray",
}


class SessionOverrideError(RuntimeError):
    """Raised when a Solaris LOP:Custom override operation cannot be completed."""


@dataclass(slots=True)
class ResolvedSettingsBinding:
    """Resolved target RenderSettings prim for a LOP network display node."""

    lopnet_path: str
    display_lop_path: str
    settings_prim_path: str
    source: str
    ephemeral_created: bool = False
    stage_serial: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AttributeDiff:
    """Single attribute comparison between production, current session, and proposed values."""

    usd_attr: str
    sdf_type: str
    production_value: Any
    current_session_value: Any
    proposed_value: Any
    triggers_restart: bool
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class OverridePreview:
    """Read-only dry-run preview of proposed LOP:Custom session overrides."""

    binding: ResolvedSettingsBinding
    preset_id: str
    diffs: list[AttributeDiff] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    valid: bool = True
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding": self.binding.to_dict(),
            "preset_id": self.preset_id,
            "diffs": [diff.to_dict() for diff in self.diffs],
            "warnings": list(self.warnings),
            "valid": self.valid,
            "reason": self.reason,
        }


@dataclass(slots=True)
class OverrideReceipt:
    """Execution and readback verification receipt for an applied override batch."""

    applied: bool
    binding: ResolvedSettingsBinding | None
    preset_id: str
    written_attrs: list[str] = field(default_factory=list)
    verified_values: dict[str, Any] = field(default_factory=dict)
    mismatches: dict[str, dict[str, Any]] = field(default_factory=dict)
    cook_count_before: int = 0
    cook_count_after: int = 0
    apply_ms: float = 0.0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "binding": self.binding.to_dict() if self.binding is not None else None,
            "preset_id": self.preset_id,
            "written_attrs": list(self.written_attrs),
            "verified_values": dict(self.verified_values),
            "mismatches": dict(self.mismatches),
            "cook_count_before": self.cook_count_before,
            "cook_count_after": self.cook_count_after,
            "apply_ms": self.apply_ms,
            "error": self.error,
        }


def _import_hou(hou_module: Any = None) -> Any:
    if hou_module is not None:
        return hou_module
    try:
        import hou  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SessionOverrideError("Houdini 'hou' module is not available.") from exc
    return hou


def _import_sdf() -> Any:
    try:
        from pxr import Sdf  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SessionOverrideError("OpenUSD 'pxr.Sdf' module is not available.") from exc
    return Sdf


def _import_usd_render() -> Any:
    try:
        from pxr import UsdRender  # type: ignore[import-not-found]
    except ImportError:
        return None
    return UsdRender


def _import_gf() -> Any:
    try:
        from pxr import Gf  # type: ignore[import-not-found]
    except ImportError:
        return None
    return Gf


def _unpack_typed_override(raw_value: Any) -> tuple[str | None, Any]:
    """Accept either plain values or ``(sdf_type_key, value)`` preset tuples."""
    if (
        isinstance(raw_value, tuple)
        and len(raw_value) == 2
        and isinstance(raw_value[0], str)
        and raw_value[0].strip().lower() in _SDF_TYPE_ATTR_MAP
    ):
        return raw_value[0].strip().lower(), raw_value[1]
    return None, raw_value


def _materialize_usd_value(value: Any) -> Any:
    """Immediately materialize a USD value into plain Python primitives/tuples."""
    if value is None:
        return None
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    # Sdf.AssetPath exposes .path and .resolvedPath
    if hasattr(value, "path") and hasattr(value, "resolvedPath"):
        return str(value.path)
    # Sdf.Path exposes .pathString
    if hasattr(value, "pathString") and hasattr(value, "IsEmpty"):
        return "" if value.IsEmpty else str(value)
    if isinstance(value, Mapping):
        return tuple((str(k), _materialize_usd_value(v)) for k, v in value.items())
    if isinstance(value, Iterable) or (
        hasattr(value, "__len__") and hasattr(value, "__getitem__")
    ):
        try:
            return tuple(_materialize_usd_value(value[i]) for i in range(len(value)))
        except Exception:
            return tuple(_materialize_usd_value(item) for item in value)
    return str(value)


def _values_match(left: Any, right: Any) -> bool:
    """Compare materialized Python values with float32-tolerant equality."""
    if left is None or right is None:
        return left is right
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if isinstance(left, int) and isinstance(right, int):
            return left == right
        return math.isclose(float(left), float(right), rel_tol=1e-5, abs_tol=1e-6)
    if isinstance(left, tuple) and isinstance(right, tuple):
        return len(left) == len(right) and all(_values_match(a, b) for a, b in zip(left, right))
    return left == right


def _normalize_prim_path(candidate: Any) -> str:
    if candidate is None:
        return ""
    if isinstance(candidate, str):
        return candidate.strip()
    if hasattr(candidate, "IsValid") and hasattr(candidate, "GetPath"):
        return str(candidate.GetPath()) if candidate.IsValid() else ""
    if hasattr(candidate, "IsEmpty"):
        return "" if candidate.IsEmpty else str(candidate)
    return str(candidate).strip()


def _safe_cook_count(node: Any) -> int:
    if node is None:
        return 0
    try:
        return int(node.cookCount())
    except Exception:
        return 0


def _safe_stage_serial(display_lop: Any) -> int:
    if display_lop is None:
        return 0
    for method_name in ("stageGenId", "cookCount"):
        method = getattr(display_lop, method_name, None)
        if callable(method):
            try:
                return int(method())
            except Exception:
                continue
    return 0


def _get_provenance(spec: Any) -> dict[str, Any] | None:
    if spec is None:
        return None
    try:
        custom_data = spec.customData
        if not custom_data or PROVENANCE_KEY not in custom_data:
            return None
        raw = custom_data[PROVENANCE_KEY]
        if isinstance(raw, Mapping):
            return dict(raw)
        return {"raw": raw}
    except Exception:
        return None


def _is_owned_spec(spec: Any, session_id: str | None = None) -> bool:
    meta = _get_provenance(spec)
    if meta is None:
        return False
    tool = meta.get("tool")
    if tool is not None and tool != _TOOL_NAME:
        return False
    if session_id is not None and meta.get("session_id") != str(session_id):
        return False
    return True


def _has_non_tool_metadata(prim_spec: Any, *, allow_ephemeral_def: bool = False) -> bool:
    """Return True if prim_spec carries any metadata unrelated to KarmaRT."""
    allowed = {"specifier", "properties", "primChildren"}
    if allow_ephemeral_def:
        allowed.add("typeName")
    try:
        info_keys = list(prim_spec.ListInfoKeys())
    except Exception:
        info_keys = []
    for key in info_keys:
        if key in allowed:
            continue
        if key == "customData":
            try:
                unrelated = [k for k in prim_spec.customData.keys() if k != PROVENANCE_KEY]
            except Exception:
                return True
            if unrelated:
                return True
            continue
        return True
    try:
        if prim_spec.customData:
            unrelated = [k for k in prim_spec.customData.keys() if k != PROVENANCE_KEY]
            if unrelated:
                return True
    except Exception:
        pass
    return False


def _can_prune_over_prim(prim_spec: Any, sdf_mod: Any) -> bool:
    if prim_spec is None or getattr(prim_spec, "nameParent", None) is None:
        return False
    if prim_spec.specifier != sdf_mod.SpecifierOver:
        return False
    if len(prim_spec.properties) > 0 or len(prim_spec.nameChildren) > 0:
        return False
    return not _has_non_tool_metadata(prim_spec, allow_ephemeral_def=False)


def _infer_sdf_type_name_from_value(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        if len(value) == 2 and all(isinstance(v, int) and not isinstance(v, bool) for v in value):
            return "int2"
        if len(value) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
            return "float2"
        if len(value) == 3 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
            return "float3"
        if len(value) == 4 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
            return "float4"
        if all(isinstance(v, str) for v in value):
            return "string[]"
        if all(isinstance(v, int) and not isinstance(v, bool) for v in value):
            return "int[]"
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
            return "float[]"
    return "string"


def _resolve_sdf_value_type(sdf_mod: Any, type_name: str) -> Any:
    normalized = str(type_name).strip()
    value_types = sdf_mod.ValueTypeNames
    find_fn = getattr(value_types, "Find", None)
    if callable(find_fn):
        found = find_fn(normalized)
        if found:
            return found
    attr_name = _SDF_TYPE_ATTR_MAP.get(normalized.lower())
    if attr_name is not None and hasattr(value_types, attr_name):
        return getattr(value_types, attr_name)
    if hasattr(value_types, normalized):
        return getattr(value_types, normalized)
    raise SessionOverrideError(f"Unsupported USD Sdf.ValueTypeNames type: {type_name!r}")


def _coerce_value_for_sdf(type_name: str, value: Any) -> Any:
    t = type_name.strip().lower()
    if t == "bool":
        return bool(value)
    if t == "int":
        return int(value)
    if t in ("float", "double"):
        return float(value)
    if t in ("string", "token"):
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return str(value)
    gf_mod = _import_gf()
    if t == "int2":
        vals = (int(value[0]), int(value[1]))
        return gf_mod.Vec2i(*vals) if gf_mod is not None else vals
    if t == "int3":
        vals = (int(value[0]), int(value[1]), int(value[2]))
        return gf_mod.Vec3i(*vals) if gf_mod is not None else vals
    if t == "int4":
        vals = (int(value[0]), int(value[1]), int(value[2]), int(value[3]))
        return gf_mod.Vec4i(*vals) if gf_mod is not None else vals
    if t == "float2":
        vals = (float(value[0]), float(value[1]))
        return gf_mod.Vec2f(*vals) if gf_mod is not None else vals
    if t == "float3":
        vals = (float(value[0]), float(value[1]), float(value[2]))
        return gf_mod.Vec3f(*vals) if gf_mod is not None else vals
    if t == "float4":
        vals = (float(value[0]), float(value[1]), float(value[2]), float(value[3]))
        return gf_mod.Vec4f(*vals) if gf_mod is not None else vals
    if t == "double2":
        vals = (float(value[0]), float(value[1]))
        return gf_mod.Vec2d(*vals) if gf_mod is not None else vals
    if t == "double3":
        vals = (float(value[0]), float(value[1]), float(value[2]))
        return gf_mod.Vec3d(*vals) if gf_mod is not None else vals
    if t == "double4":
        vals = (float(value[0]), float(value[1]), float(value[2]), float(value[3]))
        return gf_mod.Vec4d(*vals) if gf_mod is not None else vals
    if t in ("string[]", "token[]"):
        return [str(v) for v in value]
    if t == "int[]":
        return [int(v) for v in value]
    if t in ("float[]", "double[]"):
        return [float(v) for v in value]
    if t == "bool[]":
        return [bool(v) for v in value]
    return value


def _catalog_sdf_type(catalog: Any, attr_name: str) -> str | None:
    if catalog is None:
        return None
    for method_name in ("get_sdf_type", "sdf_type_for", "resolve_sdf_type"):
        fn = getattr(catalog, method_name, None)
        if callable(fn):
            try:
                res = fn(attr_name)
                if res:
                    return str(res)
            except Exception:
                pass
    for getter_name in ("get_attribute", "get", "lookup"):
        fn = getattr(catalog, getter_name, None)
        if callable(fn):
            try:
                spec = fn(attr_name)
            except Exception:
                spec = None
            if spec is not None:
                for field_name in ("sdf_type", "type_name", "value_type"):
                    val = getattr(spec, field_name, None)
                    if val:
                        return str(val)
                if isinstance(spec, Mapping):
                    for field_name in ("sdf_type", "type_name", "value_type"):
                        if spec.get(field_name):
                            return str(spec[field_name])
    return None


class SessionOverrideEngine:
    """Manages non-destructive Karma RT overrides in ``LOP:Custom``."""

    def __init__(self, hou_module: Any = None) -> None:
        self._hou_module = hou_module
        self._solo_cache: dict[str, dict[str, list[str]]] = {}

    def _hou(self) -> Any:
        return _import_hou(self._hou_module)

    def _custom_layer_enum(self) -> Any:
        hou = self._hou()
        return hou.lopViewportOverridesLayer.Custom

    def _resolve_nodes(self, lopnet: Any, display_lop: Any = None) -> tuple[Any, Any]:
        hou = self._hou()
        lopnet_node = hou.node(lopnet) if isinstance(lopnet, str) else lopnet
        if lopnet_node is None:
            raise SessionOverrideError(f"Invalid or missing LOP network: {lopnet!r}")

        display_node = hou.node(display_lop) if isinstance(display_lop, str) else display_lop

        if not hasattr(lopnet_node, "viewportOverrides"):
            parent = lopnet_node.parent() if hasattr(lopnet_node, "parent") else None
            if parent is not None and hasattr(parent, "viewportOverrides"):
                if display_node is None:
                    display_node = lopnet_node
                lopnet_node = parent
            else:
                raise SessionOverrideError(
                    f"Node {lopnet_node.path()!r} does not support viewportOverrides()."
                )

        if display_node is None:
            if hasattr(lopnet_node, "displayNode"):
                display_node = lopnet_node.displayNode()
            if display_node is None and hasattr(lopnet_node, "renderNode"):
                display_node = lopnet_node.renderNode()
        if display_node is None:
            raise SessionOverrideError(
                f"LOP network {lopnet_node.path()!r} has no display LOP node."
            )

        return lopnet_node, display_node

    def _is_restart_attribute(self, attr_name: str, catalog: Any = None) -> bool:
        if catalog is not None and hasattr(catalog, "is_restart_attribute"):
            return bool(catalog.is_restart_attribute(attr_name))
        if attr_name in _DEFAULT_RESTART_ATTRS:
            return True
        return any(attr_name.startswith(prefix) for prefix in _DEFAULT_RESTART_PREFIXES)

    def _read_stage_attr_types(
        self,
        display_lop: Any,
        prim_path: str,
        attr_names: Iterable[str],
    ) -> dict[str, str]:
        names = list(attr_names)
        found: dict[str, str] = {}
        if not names or not prim_path:
            return found
        try:
            stage = display_lop.stage(apply_viewport_overrides=False)
            if stage is not None:
                prim = stage.GetPrimAtPath(str(prim_path))
                if prim and prim.IsValid():
                    for name in names:
                        attr = prim.GetAttribute(name)
                        if attr and attr.IsValid():
                            tname = attr.GetTypeName()
                            if tname:
                                found[name] = str(tname)
        finally:
            try:
                display_lop.stage(apply_viewport_overrides=True)
            except Exception:
                pass
        return found

    def _determine_sdf_type_name(
        self,
        attr_name: str,
        value: Any,
        *,
        catalog: Any = None,
        stage_types: Mapping[str, str] | None = None,
    ) -> str:
        from_catalog = _catalog_sdf_type(catalog, attr_name)
        if from_catalog:
            return from_catalog
        if stage_types and stage_types.get(attr_name):
            return stage_types[attr_name]
        if attr_name in _KNOWN_SDF_TYPE_NAMES:
            return _KNOWN_SDF_TYPE_NAMES[attr_name]
        return _infer_sdf_type_name_from_value(value)

    def resolve_binding(
        self,
        lopnet: Any,
        display_lop: Any = None,
        renderpass_path: str = "",
    ) -> ResolvedSettingsBinding:
        """Resolve the target UsdRender.Settings prim without mutating the stage."""
        lopnet_node, display_node = self._resolve_nodes(lopnet, display_lop)
        lopnet_path = str(lopnet_node.path())
        display_path = str(display_node.path())
        stage_serial = _safe_stage_serial(display_node)

        # 1. Native Houdini 22 bestRenderSettings resolution.
        best_fn = getattr(display_node, "bestRenderSettings", None)
        if callable(best_fn):
            try:
                try:
                    best_raw = best_fn(
                        renderpass_path=renderpass_path,
                        pick_first_of_many=True,
                    )
                except TypeError:
                    best_raw = best_fn()
                best_path = _normalize_prim_path(best_raw)
                if best_path and best_path != EPHEMERAL_SETTINGS_PATH:
                    return ResolvedSettingsBinding(
                        lopnet_path=lopnet_path,
                        display_lop_path=display_path,
                        settings_prim_path=best_path,
                        source="best_render_settings",
                        ephemeral_created=False,
                        stage_serial=stage_serial,
                    )
            except Exception as exc:
                logger.debug("bestRenderSettings lookup failed on %s: %s", display_path, exc)

        # 2 & 3. Inspect production stage (apply_viewport_overrides=False) and restore True.
        metadata_path = ""
        scanned_path = ""
        usd_render = _import_usd_render()
        try:
            stage = display_node.stage(apply_viewport_overrides=False)
            if stage is not None:
                raw_meta = stage.GetMetadata("renderSettingsPrimPath")
                meta_candidate = _normalize_prim_path(raw_meta)
                if meta_candidate and meta_candidate != EPHEMERAL_SETTINGS_PATH:
                    prim = stage.GetPrimAtPath(meta_candidate)
                    if prim and prim.IsValid():
                        metadata_path = meta_candidate

                if not metadata_path:
                    for prim in stage.Traverse():
                        if not prim or not prim.IsValid():
                            continue
                        prim_path = str(prim.GetPath())
                        if prim_path == EPHEMERAL_SETTINGS_PATH:
                            continue
                        is_settings = False
                        if usd_render is not None and hasattr(usd_render, "Settings"):
                            try:
                                is_settings = bool(prim.IsA(usd_render.Settings))
                            except Exception:
                                is_settings = False
                        if not is_settings:
                            is_settings = prim.GetTypeName() == "RenderSettings"
                        if is_settings:
                            scanned_path = prim_path
                            break
        finally:
            try:
                display_node.stage(apply_viewport_overrides=True)
            except Exception:
                pass

        if metadata_path:
            return ResolvedSettingsBinding(
                lopnet_path=lopnet_path,
                display_lop_path=display_path,
                settings_prim_path=metadata_path,
                source="stage_metadata",
                ephemeral_created=False,
                stage_serial=stage_serial,
            )

        if scanned_path:
            return ResolvedSettingsBinding(
                lopnet_path=lopnet_path,
                display_lop_path=display_path,
                settings_prim_path=scanned_path,
                source="stage_scan",
                ephemeral_created=False,
                stage_serial=stage_serial,
            )

        # 4. Fallback to KarmaRT ephemeral session RenderSettings prim.
        return ResolvedSettingsBinding(
            lopnet_path=lopnet_path,
            display_lop_path=display_path,
            settings_prim_path=EPHEMERAL_SETTINGS_PATH,
            source="ephemeral_session",
            ephemeral_created=False,
            stage_serial=stage_serial,
        )

    def read_stage_values(
        self,
        display_lop: Any,
        prim_path: str,
        attr_names: Iterable[str],
        apply_viewport_overrides: bool,
    ) -> dict[str, Any]:
        """Read and immediately materialize plain Python values from a display LOP stage.

        Always restores ``display_lop.stage(apply_viewport_overrides=True)`` in
        ``finally:`` because Houdini 22 mutates and reuses the same cached
        ``Usd.Stage`` instance across override toggle calls.
        """
        names = list(attr_names)
        values: dict[str, Any] = {name: None for name in names}
        if not names or not prim_path:
            return values

        display_node = (
            self._hou().node(display_lop) if isinstance(display_lop, str) else display_lop
        )
        if display_node is None:
            raise SessionOverrideError(f"Invalid display LOP node: {display_lop!r}")

        try:
            stage = display_node.stage(apply_viewport_overrides=bool(apply_viewport_overrides))
            if stage is not None:
                prim = stage.GetPrimAtPath(str(prim_path))
                if prim and prim.IsValid():
                    for name in names:
                        attr = prim.GetAttribute(name)
                        if attr and attr.IsValid():
                            values[name] = _materialize_usd_value(attr.Get())
        finally:
            try:
                display_node.stage(apply_viewport_overrides=True)
            except Exception:
                pass

        return values

    def _read_owned_attr_names_on_prim(
        self,
        lopnet_node: Any,
        display_node: Any,
        prim_path: str,
    ) -> list[str]:
        owned: list[str] = []
        with lopnet_node.viewportOverrides(
            display_node,
            output_index=0,
            active_layer=self._custom_layer_enum(),
        ) as ov:
            layer = ov.layer()
            if layer is None:
                return owned
            prim_spec = layer.GetPrimAtPath(str(prim_path))
            if prim_spec is None:
                return owned
            for prop_name, prop_spec in list(prim_spec.properties.items()):
                if _is_owned_spec(prop_spec):
                    owned.append(str(prop_name))
        return owned

    def preview_overrides(
        self,
        lopnet: Any,
        overrides: Mapping[str, Any],
        preset_id: str,
        catalog: Any = None,
        display_lop: Any = None,
        renderpass_path: str = "",
    ) -> OverridePreview:
        """Pure read-only dry-run returning exact attribute diffs and restart warnings."""
        lopnet_node, display_node = self._resolve_nodes(lopnet, display_lop)
        binding = self.resolve_binding(
            lopnet_node,
            display_lop=display_node,
            renderpass_path=renderpass_path,
        )

        target_path = binding.settings_prim_path
        existing_owned = self._read_owned_attr_names_on_prim(
            lopnet_node, display_node, target_path
        )

        requested_attrs = list(overrides.keys())
        stale_attrs = [name for name in existing_owned if name not in overrides]
        all_attrs = list(dict.fromkeys([*requested_attrs, *stale_attrs]))

        stage_types = self._read_stage_attr_types(display_node, target_path, all_attrs)
        production_values = self.read_stage_values(
            display_node, target_path, all_attrs, apply_viewport_overrides=False
        )
        current_values = self.read_stage_values(
            display_node, target_path, all_attrs, apply_viewport_overrides=True
        )

        diffs: list[AttributeDiff] = []
        warnings: list[str] = []
        blocked_attrs: list[str] = []

        if binding.source == "ephemeral_session":
            warnings.append(
                f"No production RenderSettings prim found; will author ephemeral "
                f"session prim {EPHEMERAL_SETTINGS_PATH!r} in LOP:Custom."
            )

        for attr_name, raw_entry in overrides.items():
            triggers_restart = self._is_restart_attribute(attr_name, catalog=catalog)
            prod_val = production_values.get(attr_name)
            curr_val = current_values.get(attr_name)
            explicit_type, raw_value = _unpack_typed_override(raw_entry)

            if raw_value is None:
                sdf_type = explicit_type or self._determine_sdf_type_name(
                    attr_name, prod_val, catalog=catalog, stage_types=stage_types
                )
                status = "remove" if attr_name in existing_owned else "unchanged"
                diffs.append(
                    AttributeDiff(
                        usd_attr=attr_name,
                        sdf_type=sdf_type,
                        production_value=prod_val,
                        current_session_value=curr_val,
                        proposed_value=prod_val,
                        triggers_restart=triggers_restart,
                        status=status,
                    )
                )
                continue

            sdf_type = explicit_type or self._determine_sdf_type_name(
                attr_name, raw_value, catalog=catalog, stage_types=stage_types
            )
            try:
                coerced = _coerce_value_for_sdf(sdf_type, raw_value)
                proposed_val = _materialize_usd_value(coerced)
            except Exception as exc:
                warnings.append(
                    f"Attribute {attr_name!r} value {raw_value!r} cannot be coerced to "
                    f"{sdf_type!r}: {exc}"
                )
                proposed_val = _materialize_usd_value(raw_value)

            if triggers_restart:
                blocked_attrs.append(attr_name)
                warnings.append(
                    f"Attribute {attr_name!r} triggers a Hydra delegate restart in Karma."
                )
                status = "blocked_restart"
            elif _values_match(curr_val, proposed_val):
                status = "unchanged"
            elif curr_val is None:
                status = "add"
            else:
                status = "modify"

            diffs.append(
                AttributeDiff(
                    usd_attr=attr_name,
                    sdf_type=sdf_type,
                    production_value=prod_val,
                    current_session_value=curr_val,
                    proposed_value=proposed_val,
                    triggers_restart=triggers_restart,
                    status=status,
                )
            )

        for stale_attr in stale_attrs:
            prod_val = production_values.get(stale_attr)
            curr_val = current_values.get(stale_attr)
            sdf_type = self._determine_sdf_type_name(
                stale_attr, curr_val, catalog=catalog, stage_types=stage_types
            )
            diffs.append(
                AttributeDiff(
                    usd_attr=stale_attr,
                    sdf_type=sdf_type,
                    production_value=prod_val,
                    current_session_value=curr_val,
                    proposed_value=prod_val,
                    triggers_restart=False,
                    status="remove",
                )
            )

        valid = len(blocked_attrs) == 0
        reason = (
            f"Blocked restart-triggering attribute(s): {', '.join(blocked_attrs)}"
            if blocked_attrs
            else ""
        )

        return OverridePreview(
            binding=binding,
            preset_id=preset_id,
            diffs=diffs,
            warnings=warnings,
            valid=valid,
            reason=reason,
        )

    def apply_overrides(
        self,
        lopnet: Any,
        overrides: Mapping[str, Any],
        preset_id: str,
        session_id: str,
        catalog: Any = None,
        display_lop: Any = None,
        renderpass_path: str = "",
        allow_restart: bool = False,
    ) -> OverrideReceipt:
        """Apply session overrides atomically to ``LOP:Custom`` and verify readback."""
        t0 = time.perf_counter()
        lopnet_node, display_node = self._resolve_nodes(lopnet, display_lop)
        binding = self.resolve_binding(
            lopnet_node,
            display_lop=display_node,
            renderpass_path=renderpass_path,
        )
        cook_before = _safe_cook_count(display_node)

        effective_overrides = {k: v for k, v in overrides.items() if v is not None}

        if not allow_restart:
            blocked = [
                attr_name
                for attr_name in effective_overrides
                if self._is_restart_attribute(attr_name, catalog=catalog)
            ]
            if blocked:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                return OverrideReceipt(
                    applied=False,
                    binding=binding,
                    preset_id=preset_id,
                    written_attrs=[],
                    verified_values={},
                    mismatches={},
                    cook_count_before=cook_before,
                    cook_count_after=_safe_cook_count(display_node),
                    apply_ms=elapsed_ms,
                    error=(
                        f"Rejected restart-triggering attribute(s) without allow_restart=True: "
                        f"{', '.join(blocked)}"
                    ),
                )

        sdf_mod = _import_sdf()
        target_path = binding.settings_prim_path
        is_ephemeral = (
            binding.source == "ephemeral_session" or target_path == EPHEMERAL_SETTINGS_PATH
        )

        stage_types = self._read_stage_attr_types(
            display_node, target_path, effective_overrides.keys()
        )

        prepared: list[tuple[str, Any, Any, Any]] = []
        for attr_name, raw_entry in effective_overrides.items():
            explicit_type, raw_value = _unpack_typed_override(raw_entry)
            if raw_value is None:
                continue
            sdf_type_name = explicit_type or self._determine_sdf_type_name(
                attr_name, raw_value, catalog=catalog, stage_types=stage_types
            )
            sdf_value_type = _resolve_sdf_value_type(sdf_mod, sdf_type_name)
            coerced_value = _coerce_value_for_sdf(sdf_type_name, raw_value)
            expected_plain = _materialize_usd_value(coerced_value)
            prepared.append((attr_name, sdf_value_type, coerced_value, expected_plain))

        provenance = {
            "tool": _TOOL_NAME,
            "session_id": str(session_id),
            "preset": str(preset_id),
        }

        written_attrs: list[str] = []
        expected_values: dict[str, Any] = {}
        ephemeral_active = False

        with lopnet_node.viewportOverrides(
            display_node,
            output_index=0,
            active_layer=self._custom_layer_enum(),
        ) as ov:
            layer = ov.layer()
            if layer is None:
                raise SessionOverrideError(
                    f"Failed to acquire LOP:Custom Sdf.Layer for {binding.lopnet_path!r}."
                )

            with sdf_mod.ChangeBlock():
                # If a real production RenderSettings exists now, clean up any stale ephemeral prim.
                if not is_ephemeral:
                    stale_ephemeral = layer.GetPrimAtPath(EPHEMERAL_SETTINGS_PATH)
                    if stale_ephemeral is not None and _is_owned_spec(stale_ephemeral):
                        for prop_spec in list(stale_ephemeral.properties.values()):
                            if _is_owned_spec(prop_spec):
                                stale_ephemeral.RemoveProperty(prop_spec)
                        if (
                            len(stale_ephemeral.properties) == 0
                            and len(stale_ephemeral.nameChildren) == 0
                            and not _has_non_tool_metadata(
                                stale_ephemeral, allow_ephemeral_def=True
                            )
                        ):
                            parent = stale_ephemeral.nameParent
                            if parent is not None:
                                del parent.nameChildren[stale_ephemeral.name]
                    self._clear_ephemeral_root_metadata(layer)

                prim_spec = layer.GetPrimAtPath(target_path)
                if prim_spec is not None:
                    for prop_name, prop_spec in list(prim_spec.properties.items()):
                        if prop_name not in effective_overrides and _is_owned_spec(prop_spec):
                            prim_spec.RemoveProperty(prop_spec)

                if not prepared:
                    if prim_spec is not None:
                        self._prune_if_empty_owned_branch(layer, prim_spec, sdf_mod)
                    self._clear_ephemeral_root_metadata(layer)
                else:
                    created_prim = prim_spec is None
                    if prim_spec is None:
                        prim_spec = sdf_mod.CreatePrimInLayer(layer, sdf_mod.Path(target_path))

                    if is_ephemeral:
                        prim_spec.specifier = sdf_mod.SpecifierDef
                        prim_spec.typeName = "RenderSettings"
                        prim_spec.customData[PROVENANCE_KEY] = dict(provenance)
                        layer.pseudoRoot.SetInfo("renderSettingsPrimPath", EPHEMERAL_SETTINGS_PATH)
                        ephemeral_active = True
                    elif created_prim:
                        prim_spec.specifier = sdf_mod.SpecifierOver

                    for attr_name, sdf_value_type, coerced_value, expected_plain in prepared:
                        existing = (
                            prim_spec.attributes[attr_name]
                            if attr_name in prim_spec.attributes
                            else None
                        )
                        if existing is not None:
                            if existing.typeName != sdf_value_type:
                                preserved_cd = {
                                    k: existing.customData[k]
                                    for k in existing.customData.keys()
                                    if k != PROVENANCE_KEY
                                }
                                prim_spec.RemoveProperty(existing)
                                attr_spec = sdf_mod.AttributeSpec(
                                    prim_spec, attr_name, sdf_value_type
                                )
                                for k, v in preserved_cd.items():
                                    attr_spec.customData[k] = v
                            else:
                                attr_spec = existing
                        else:
                            attr_spec = sdf_mod.AttributeSpec(
                                prim_spec, attr_name, sdf_value_type
                            )

                        attr_spec.default = coerced_value
                        attr_spec.customData[PROVENANCE_KEY] = dict(provenance)
                        written_attrs.append(attr_name)
                        expected_values[attr_name] = expected_plain

        verified_values = self.read_stage_values(
            display_node,
            target_path,
            written_attrs,
            apply_viewport_overrides=True,
        )

        mismatches: dict[str, dict[str, Any]] = {}
        for attr_name in written_attrs:
            expected_val = expected_values[attr_name]
            actual_val = verified_values.get(attr_name)
            if not _values_match(expected_val, actual_val):
                mismatches[attr_name] = {"expected": expected_val, "actual": actual_val}

        final_binding = ResolvedSettingsBinding(
            lopnet_path=binding.lopnet_path,
            display_lop_path=binding.display_lop_path,
            settings_prim_path=binding.settings_prim_path,
            source=binding.source,
            ephemeral_created=ephemeral_active,
            stage_serial=_safe_stage_serial(display_node),
        )
        cook_after = _safe_cook_count(display_node)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        return OverrideReceipt(
            applied=True,
            binding=final_binding,
            preset_id=preset_id,
            written_attrs=written_attrs,
            verified_values=verified_values,
            mismatches=mismatches,
            cook_count_before=cook_before,
            cook_count_after=cook_after,
            apply_ms=elapsed_ms,
            error="",
        )

    def _clear_ephemeral_root_metadata(self, layer: Any) -> bool:
        root_spec = getattr(layer, "pseudoRoot", None)
        if root_spec is None:
            return False
        if layer.GetPrimAtPath(EPHEMERAL_SETTINGS_PATH) is not None:
            return False
        if root_spec.HasInfo("renderSettingsPrimPath"):
            current = str(root_spec.GetInfo("renderSettingsPrimPath"))
            if current == EPHEMERAL_SETTINGS_PATH:
                root_spec.ClearInfo("renderSettingsPrimPath")
                return True
        return False

    def _prune_if_empty_owned_branch(self, layer: Any, prim_spec: Any, sdf_mod: Any) -> list[str]:
        pruned: list[str] = []
        pseudo_root = getattr(layer, "pseudoRoot", None)
        current = prim_spec
        while current is not None and current != pseudo_root and str(getattr(current, "path", "/")) != "/":
            parent = getattr(current, "realNameParent", None) or getattr(current, "nameParent", None) or pseudo_root
            if parent is None:
                break
            is_owned_prim = _is_owned_spec(current)
            if is_owned_prim:
                if (
                    len(current.properties) == 0
                    and len(current.nameChildren) == 0
                    and not _has_non_tool_metadata(current, allow_ephemeral_def=True)
                ):
                    pruned.append(str(current.path))
                    del parent.nameChildren[current.name]
                    current = parent
                    continue
                if PROVENANCE_KEY in current.customData:
                    del current.customData[PROVENANCE_KEY]
                break
            if _can_prune_over_prim(current, sdf_mod):
                pruned.append(str(current.path))
                del parent.nameChildren[current.name]
                current = parent
                continue
            break
        return pruned

    def clear_owned_overrides(
        self,
        lopnet: Any,
        display_lop: Any = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Surgically delete ONLY ``PROVENANCE_KEY``-owned specs from ``LOP:Custom``."""
        lopnet_node, display_node = self._resolve_nodes(lopnet, display_lop)
        sdf_mod = _import_sdf()

        removed_specs: list[str] = []
        pruned_prims: list[str] = []
        cleared_root_metadata = False

        def _walk_and_clean(prim_spec: Any, pseudo_root: Any) -> bool:
            touched = False
            for child in list(prim_spec.nameChildren.values()):
                if _walk_and_clean(child, pseudo_root):
                    touched = True

            for prop_spec in list(prim_spec.properties.values()):
                if _is_owned_spec(prop_spec, session_id=session_id):
                    removed_specs.append(str(prop_spec.path))
                    prim_spec.RemoveProperty(prop_spec)
                    touched = True

            if prim_spec == pseudo_root or str(getattr(prim_spec, "path", "/")) == "/":
                return touched

            parent = getattr(prim_spec, "realNameParent", None) or getattr(prim_spec, "nameParent", None) or pseudo_root
            if parent is None:
                return touched

            if _is_owned_spec(prim_spec, session_id=session_id):
                touched = True
                if (
                    len(prim_spec.properties) == 0
                    and len(prim_spec.nameChildren) == 0
                    and not _has_non_tool_metadata(prim_spec, allow_ephemeral_def=True)
                ):
                    pruned_prims.append(str(prim_spec.path))
                    del parent.nameChildren[prim_spec.name]
                else:
                    if PROVENANCE_KEY in prim_spec.customData:
                        del prim_spec.customData[PROVENANCE_KEY]
                return True

            if touched and _can_prune_over_prim(prim_spec, sdf_mod):
                pruned_prims.append(str(prim_spec.path))
                del parent.nameChildren[prim_spec.name]

            return touched

        with lopnet_node.viewportOverrides(
            display_node,
            output_index=0,
            active_layer=self._custom_layer_enum(),
        ) as ov:
            layer = ov.layer()
            if layer is not None and getattr(layer, "pseudoRoot", None) is not None:
                with sdf_mod.ChangeBlock():
                    _walk_and_clean(layer.pseudoRoot, layer.pseudoRoot)
                    cleared_root_metadata = self._clear_ephemeral_root_metadata(layer)

        return {
            "cleared": True,
            "lopnet_path": str(lopnet_node.path()),
            "display_lop_path": str(display_node.path()),
            "removed_specs": removed_specs,
            "pruned_prims": pruned_prims,
            "cleared_root_metadata": cleared_root_metadata,
            "removed_count": len(removed_specs) + len(pruned_prims),
        }

    def list_owned_specs(self, lopnet: Any, display_lop: Any = None) -> list[str]:
        """Read-only inspection returning all ``PROVENANCE_KEY``-owned spec paths in ``LOP:Custom``."""
        lopnet_node, display_node = self._resolve_nodes(lopnet, display_lop)
        found: list[str] = []

        def _collect(prim_spec: Any, pseudo_root: Any) -> None:
            is_root = prim_spec == pseudo_root or str(getattr(prim_spec, "path", "/")) == "/"
            if not is_root and _is_owned_spec(prim_spec):
                found.append(str(prim_spec.path))
            for prop_spec in list(prim_spec.properties.values()):
                if _is_owned_spec(prop_spec):
                    found.append(str(prop_spec.path))
            for child in list(prim_spec.nameChildren.values()):
                _collect(child, pseudo_root)

        with lopnet_node.viewportOverrides(
            display_node,
            output_index=0,
            active_layer=self._custom_layer_enum(),
        ) as ov:
            layer = ov.layer()
            if layer is not None and getattr(layer, "pseudoRoot", None) is not None:
                _collect(layer.pseudoRoot, layer.pseudoRoot)

        found.sort()
        return found

    def has_usd_image_filters(
        self,
        display_lop: Any,
        settings_prim_path: str = "",
    ) -> bool:
        """Read-only check for H22 ``husk:orderedImageFilters`` / ``HoudiniImageFilterList``."""
        display_node = (
            self._hou().node(display_lop) if isinstance(display_lop, str) else display_lop
        )
        if display_node is None:
            return False
        found = False
        try:
            stage = display_node.stage(apply_viewport_overrides=False)
            if stage is not None:
                if settings_prim_path:
                    prim = stage.GetPrimAtPath(str(settings_prim_path))
                    if prim and prim.IsValid():
                        rel = prim.GetRelationship("husk:orderedImageFilters")
                        if rel and rel.IsValid() and rel.GetTargets():
                            found = True
                        if not found:
                            attr = prim.GetAttribute("husk:orderedImageFilters")
                            if attr and attr.IsValid() and attr.Get():
                                found = True
                if not found:
                    for prim in stage.Traverse():
                        if not prim or not prim.IsValid() or not prim.IsActive():
                            continue
                        if prim.GetTypeName() == "HoudiniImageFilterList":
                            found = True
                            break
                        if "HoudiniImageFilterList" in (prim.GetAppliedSchemas() or ()):
                            found = True
                            break
                        rel = prim.GetRelationship("husk:orderedImageFilters")
                        if rel and rel.IsValid() and rel.GetTargets():
                            found = True
                            break
        except Exception:
            found = False
        finally:
            try:
                display_node.stage(apply_viewport_overrides=True)
            except Exception:
                pass
        return found

    def read_production_resolution(
        self,
        display_lop: Any,
        settings_prim_path: str = "",
    ) -> tuple[int, int] | None:
        """Read production ``resolution`` (width, height) from ``apply_viewport_overrides=False``."""
        display_node = (
            self._hou().node(display_lop) if isinstance(display_lop, str) else display_lop
        )
        if display_node is None or not settings_prim_path:
            return None
        res_tuple: tuple[int, int] | None = None
        try:
            stage = display_node.stage(apply_viewport_overrides=False)
            if stage is not None:
                prim = stage.GetPrimAtPath(str(settings_prim_path))
                if prim and prim.IsValid():
                    attr = prim.GetAttribute("resolution")
                    if attr and attr.IsValid():
                        val = _materialize_usd_value(attr.Get())
                        if isinstance(val, tuple) and len(val) >= 2:
                            w, h = int(val[0]), int(val[1])
                            if w > 0 and h > 0:
                                res_tuple = (w, h)
        except Exception:
            res_tuple = None
        finally:
            try:
                display_node.stage(apply_viewport_overrides=True)
            except Exception:
                pass
        return res_tuple

    def _build_selection_rule(self, prim_paths: Iterable[str] | None) -> Any | None:
        if prim_paths is None:
            return None
        cleaned = [str(p).strip() for p in prim_paths if str(p).strip()]
        if not cleaned:
            return None
        hou = self._hou()
        rule_cls = getattr(hou, "LopSelectionRule", None)
        if rule_cls is None:
            raise SessionOverrideError("hou.LopSelectionRule is not available in this Houdini build.")
        rule = rule_cls()
        rule.setPathPattern(" ".join(cleaned))
        return rule

    def set_solo_geometry(
        self,
        lopnet: Any,
        prim_paths: Iterable[str] | None,
        display_lop: Any = None,
    ) -> list[str]:
        """Non-destructively solo geometry prims via ``LopViewportOverrides.setSoloGeometry``."""
        lopnet_node, display_node = self._resolve_nodes(lopnet, display_lop)
        rule = self._build_selection_rule(prim_paths)
        with lopnet_node.viewportOverrides(display_node) as ov:
            ov.setSoloGeometry(rule)
            geo = [str(p) for p in (ov.soloGeometry() or ())]
            lights = [str(p) for p in (ov.soloLights() or ())]
        self._solo_cache[str(lopnet_node.path())] = {"geometry": geo, "lights": lights}
        return geo

    def set_solo_lights(
        self,
        lopnet: Any,
        prim_paths: Iterable[str] | None,
        display_lop: Any = None,
    ) -> list[str]:
        """Non-destructively solo light prims via ``LopViewportOverrides.setSoloLights``."""
        lopnet_node, display_node = self._resolve_nodes(lopnet, display_lop)
        rule = self._build_selection_rule(prim_paths)
        with lopnet_node.viewportOverrides(display_node) as ov:
            ov.setSoloLights(rule)
            geo = [str(p) for p in (ov.soloGeometry() or ())]
            lights = [str(p) for p in (ov.soloLights() or ())]
        self._solo_cache[str(lopnet_node.path())] = {"geometry": geo, "lights": lights}
        return lights

    def get_solo_state(
        self,
        lopnet: Any,
        display_lop: Any = None,
        *,
        live: bool = False,
    ) -> dict[str, list[str]]:
        """Return active viewport solo geometry and solo lights without triggering HUSD copy crash.

        Note: ``hou.LopNetwork.copyViewportOverrides()`` segfaults inside ``HUSD_Overrides::copy``
        in Houdini 22.0.429 / 22.0.504 on uninitialized override containers, so we intentionally
        avoid it and read from the session solo cache (or ``viewportOverrides`` when ``live=True``).
        """
        lopnet_node, display_node = self._resolve_nodes(lopnet, display_lop)
        key = str(lopnet_node.path())
        if not live:
            cached = self._solo_cache.get(key)
            if cached is not None:
                return {"geometry": list(cached["geometry"]), "lights": list(cached["lights"])}
            return {"geometry": [], "lights": []}
        with lopnet_node.viewportOverrides(display_node) as ov:
            geo = [str(p) for p in (ov.soloGeometry() or ())]
            lights = [str(p) for p in (ov.soloLights() or ())]
        self._solo_cache[key] = {"geometry": geo, "lights": lights}
        return {"geometry": list(geo), "lights": list(lights)}

    def clear_solo_overrides(
        self,
        lopnet: Any,
        display_lop: Any = None,
    ) -> dict[str, list[str]]:
        """Clear both solo geometry and solo light overrides cleanly."""
        lopnet_node, display_node = self._resolve_nodes(lopnet, display_lop)
        with lopnet_node.viewportOverrides(display_node) as ov:
            ov.setSoloGeometry(None)
            ov.setSoloLights(None)
            geo = [str(p) for p in (ov.soloGeometry() or ())]
            lights = [str(p) for p in (ov.soloLights() or ())]
        self._solo_cache[str(lopnet_node.path())] = {"geometry": geo, "lights": lights}
        return {"geometry": geo, "lights": lights}


class HipLifecycleGuard:
    """Strips KarmaRT ``LOP:Custom`` specs before save/teardown and sweeps orphans on load."""

    def __init__(
        self,
        engine: SessionOverrideEngine,
        hou_module: Any = None,
        teardown_hook: Callable[[Any], None] | None = None,
    ) -> None:
        self._engine = engine
        self._hou_module = hou_module
        self._teardown_hook = teardown_hook
        self._active: dict[str, dict[str, Any]] = {}
        self._suspended_before_save: dict[str, dict[str, Any]] = {}
        self._installed = False
        self._callback = self.handle_event

    def _hou(self) -> Any:
        return _import_hou(self._hou_module)

    @property
    def installed(self) -> bool:
        return self._installed

    def set_teardown_hook(self, hook: Callable[[Any], None] | None) -> None:
        self._teardown_hook = hook

    def register_active(self, lopnet_path: str, state_payload: dict[str, Any]) -> None:
        """Track an active KarmaRT session override payload for a LOP network path."""
        self._active[str(lopnet_path)] = dict(state_payload)

    def unregister_active(self, lopnet_path: str) -> dict[str, Any] | None:
        """Stop tracking a LOP network path and return its previous payload if any."""
        key = str(lopnet_path)
        self._suspended_before_save.pop(key, None)
        return self._active.pop(key, None)

    def active_entries(self) -> dict[str, dict[str, Any]]:
        return {k: dict(v) for k, v in self._active.items()}

    def install(self) -> bool:
        """Register the ``hou.hipFile`` lifecycle callback (idempotent)."""
        if self._installed:
            return True
        hou = self._hou()
        hou.hipFile.addEventCallback(self._callback)
        self._installed = True
        return True

    def uninstall(self) -> bool:
        """Unregister the ``hou.hipFile`` lifecycle callback safely."""
        if not self._installed:
            return False
        hou = self._hou()
        try:
            hou.hipFile.removeEventCallback(self._callback)
        except Exception as exc:
            logger.debug("removeEventCallback ignored error: %s", exc)
        self._installed = False
        return True

    def handle_event(self, event_type: Any) -> None:
        """Dispatch a ``hou.hipFileEventType`` notification."""
        hou = self._hou()
        ev = hou.hipFileEventType

        if event_type == ev.BeforeSave:
            self._handle_before_save()
        elif event_type == ev.AfterSave:
            self._handle_after_save()
        elif event_type in (ev.BeforeClear, ev.BeforeLoad, ev.BeforeQuit):
            self._handle_teardown(event_type)
        elif event_type == ev.AfterLoad:
            self.sweep_orphaned_overrides()

    def _resolve_display_for_payload(self, lopnet_node: Any, payload: Mapping[str, Any]) -> Any:
        display_path = payload.get("display_lop_path")
        if display_path:
            node = self._hou().node(str(display_path))
            if node is not None:
                return node
        return None

    def _handle_before_save(self) -> None:
        hou = self._hou()
        snapshot = {k: dict(v) for k, v in self._active.items()}
        self._suspended_before_save = snapshot

        for lopnet_path, payload in snapshot.items():
            try:
                lopnet_node = hou.node(lopnet_path)
                if lopnet_node is None:
                    continue
                display_node = self._resolve_display_for_payload(lopnet_node, payload)
                self._engine.clear_owned_overrides(
                    lopnet_node,
                    display_lop=display_node,
                    session_id=payload.get("session_id"),
                )
            except Exception as exc:
                logger.warning(
                    "KarmaRT BeforeSave strip failed on %s: %s", lopnet_path, exc
                )

    def _handle_after_save(self) -> None:
        hou = self._hou()
        snapshot = self._suspended_before_save
        self._suspended_before_save = {}

        for lopnet_path, payload in snapshot.items():
            if lopnet_path not in self._active:
                continue
            try:
                reapply_fn = payload.get("reapply")
                if callable(reapply_fn):
                    reapply_fn()
                    continue

                overrides = payload.get("overrides")
                if not isinstance(overrides, Mapping) or not overrides:
                    continue
                lopnet_node = hou.node(lopnet_path)
                if lopnet_node is None:
                    continue
                display_node = self._resolve_display_for_payload(lopnet_node, payload)
                self._engine.apply_overrides(
                    lopnet_node,
                    overrides=overrides,
                    preset_id=str(payload.get("preset_id") or ""),
                    session_id=str(payload.get("session_id") or ""),
                    catalog=payload.get("catalog"),
                    display_lop=display_node,
                    renderpass_path=str(payload.get("renderpass_path") or ""),
                    allow_restart=bool(payload.get("allow_restart", False)),
                )
            except Exception as exc:
                logger.warning(
                    "KarmaRT AfterSave re-apply failed on %s: %s", lopnet_path, exc
                )

    def _invoke_teardown_hook(self, hook: Callable[..., Any] | None, event_type: Any) -> None:
        if hook is None:
            return
        try:
            hook(event_type)
        except TypeError:
            try:
                hook()
            except Exception as exc:
                logger.warning("KarmaRT teardown hook failed: %s", exc)
        except Exception as exc:
            logger.warning("KarmaRT teardown hook failed: %s", exc)

    def _handle_teardown(self, event_type: Any) -> None:
        hou = self._hou()
        snapshot = list(self._active.items())
        self._active.clear()
        self._suspended_before_save.clear()

        for lopnet_path, payload in snapshot:
            try:
                lopnet_node = hou.node(lopnet_path)
                if lopnet_node is not None:
                    display_node = self._resolve_display_for_payload(lopnet_node, payload)
                    self._engine.clear_owned_overrides(
                        lopnet_node,
                        display_lop=display_node,
                        session_id=payload.get("session_id"),
                    )
            except Exception as exc:
                logger.debug(
                    "KarmaRT teardown strip skipped on %s: %s", lopnet_path, exc
                )
            per_entry_hook = payload.get("teardown_hook")
            if callable(per_entry_hook):
                self._invoke_teardown_hook(per_entry_hook, event_type)

        self._invoke_teardown_hook(self._teardown_hook, event_type)

    def sweep_orphaned_overrides(self) -> list[dict[str, Any]]:
        """Sweep any orphaned ``PROVENANCE_KEY`` specs across all LOP networks under ``/``."""
        hou = self._hou()
        root = hou.node("/")
        if root is None:
            return []

        try:
            candidates = root.allSubChildren(top_down=True, recurse_in_locked_nodes=False)
        except TypeError:
            candidates = root.allSubChildren()

        lopnet_cls = getattr(hou, "LopNetwork", None)
        reports: list[dict[str, Any]] = []

        for node in candidates:
            try:
                is_lopnet = False
                if lopnet_cls is not None and isinstance(node, lopnet_cls):
                    is_lopnet = True
                elif hasattr(node, "viewportOverrides"):
                    cat = node.childTypeCategory()
                    if cat is not None and cat.name() == "Lop":
                        is_lopnet = True
                if not is_lopnet:
                    continue
                if hasattr(node, "displayNode") and node.displayNode() is None:
                    continue
                owned = self._engine.list_owned_specs(node)
                if not owned:
                    continue
                reports.append(self._engine.clear_owned_overrides(node))
            except Exception as exc:
                logger.debug("KarmaRT orphan sweep skipped node: %s", exc)

        return reports


__all__ = [
    "AttributeDiff",
    "EPHEMERAL_SETTINGS_PATH",
    "HipLifecycleGuard",
    "OverridePreview",
    "OverrideReceipt",
    "PROVENANCE_KEY",
    "ResolvedSettingsBinding",
    "SessionOverrideEngine",
    "SessionOverrideError",
]
