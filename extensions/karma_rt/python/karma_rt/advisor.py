"""Read-only Solaris/USD stage bottleneck advisor and non-gating latency ledger.

Inspects an active Houdini 22 Solaris stage and LOP network without mutating
USD state, modifying parameters, changing display flags, or triggering cooks.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from pxr import Sdf, Usd, UsdGeom, UsdLux, UsdShade, UsdVol  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional outside Houdini Python
    Sdf = None
    Usd = None
    UsdGeom = None
    UsdLux = None
    UsdShade = None
    UsdVol = None

from .hardware import probe_hardware

UNKNOWN = "UNKNOWN"

FLAT_HIERARCHY_SIBLING_THRESHOLD = 500
MULTI_LIGHT_TREE_THRESHOLD = 8

_PRINCIPLED_SHADER_TOKENS = (
    "principledshader",
    "classicshader",
    "mantra",
    "vex",
)

_NON_LIGHT_TREE_TYPES = frozenset(
    {
        "DomeLight",
        "DistantLight",
        "PortalLight",
        "GeometryLight",
    }
)

_VOLUME_TYPE_NAMES = frozenset(
    {"Volume", "OpenVDBAsset", "Field3DAsset", "HoudiniFieldAsset"}
)

_BUMP_OVERRIDE_ATTRS = (
    "primvars:karma:object:bump_override",
    "karma:object:bump_override",
    "primvars:karma:object:displacement_as_bump",
    "karma:object:displacement_as_bump",
    "primvars:karma:object:use_bump_only",
    "karma:object:use_bump_only",
)

_DISPLACE_BOUND_ATTRS = (
    "primvars:karma:object:displacebound",
    "karma:object:displacebound",
)

_XPU_ENV_KEYS = (
    "KARMA_XPU_NUM_PER_DEVICE_BLENDING_THREADS",
    "KARMA_XPU_DISABLE_OPTIX_DEVICE",
    "KARMA_XPU_DISABLE_EMBREE_DEVICE",
    "KARMA_XPU_MAX_SHADER_COMPLEXITY",
    "HOUDINI_OCL_DEVICETYPE",
    "HOUDINI_OCL_DEVICENUMBER",
)

_SHADER_FILE_ATTRS = (
    "inputs:file",
    "inputs:filename",
    "inputs:texture",
    "inputs:map",
    "inputs:image",
)


@dataclass(slots=True)
class StageTelemetry:
    """Materialized, read-only telemetry snapshot for a Solaris LOP stage."""

    lopnet_path: str
    display_lop_path: str
    stage_serial: int | str
    scan_ms: float
    prim_count: int
    truncated: bool
    mesh_count: int
    subdiv_count: int
    volume_count: int
    light_count: int
    instancer_count: int
    scatter_lop_paths: list[str] = field(default_factory=list)
    fog_volume_paths: list[str] = field(default_factory=list)
    lights_missing_equiangular: list[str] = field(default_factory=list)
    displaced_subdiv_paths: list[str] = field(default_factory=list)
    missing_tangentu_paths: list[str] = field(default_factory=list)
    has_image_filter_list: bool = False
    has_texture_material_lib: bool = False
    xpu_env_summary: dict[str, str] = field(default_factory=dict)
    principled_shader_paths: list[str] = field(default_factory=list)
    flat_hierarchy_parents: list[str] = field(default_factory=list)
    gaussian_splat_paths: list[str] = field(default_factory=list)
    non_light_tree_light_paths: list[str] = field(default_factory=list)
    double_denoiser_paths: list[str] = field(default_factory=list)
    hardware_summary: str = UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "lopnet_path": self.lopnet_path,
            "display_lop_path": self.display_lop_path,
            "stage_serial": self.stage_serial,
            "scan_ms": self.scan_ms,
            "prim_count": self.prim_count,
            "truncated": self.truncated,
            "mesh_count": self.mesh_count,
            "subdiv_count": self.subdiv_count,
            "volume_count": self.volume_count,
            "light_count": self.light_count,
            "instancer_count": self.instancer_count,
            "scatter_lop_paths": list(self.scatter_lop_paths),
            "fog_volume_paths": list(self.fog_volume_paths),
            "lights_missing_equiangular": list(self.lights_missing_equiangular),
            "displaced_subdiv_paths": list(self.displaced_subdiv_paths),
            "missing_tangentu_paths": list(self.missing_tangentu_paths),
            "has_image_filter_list": self.has_image_filter_list,
            "has_texture_material_lib": self.has_texture_material_lib,
            "xpu_env_summary": dict(self.xpu_env_summary),
            "principled_shader_paths": list(self.principled_shader_paths),
            "flat_hierarchy_parents": list(self.flat_hierarchy_parents),
            "gaussian_splat_paths": list(self.gaussian_splat_paths),
            "non_light_tree_light_paths": list(self.non_light_tree_light_paths),
            "double_denoiser_paths": list(self.double_denoiser_paths),
            "hardware_summary": self.hardware_summary,
        }


@dataclass(slots=True)
class SceneAdvisory:
    """Actionable scene performance or parity finding from stage inspection."""

    id: str
    severity: str
    category: str
    title: str
    mechanism: str
    recommendation: str
    target_paths: list[str] = field(default_factory=list)
    triggers_restart: bool = False
    action_kind: str = "advisory_only"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity,
            "category": self.category,
            "title": self.title,
            "mechanism": self.mechanism,
            "recommendation": self.recommendation,
            "target_paths": list(self.target_paths),
            "triggers_restart": self.triggers_restart,
            "action_kind": self.action_kind,
        }


@dataclass(slots=True)
class LedgerEntry:
    """Single structured JSONL record for KarmaRT latency and decision logging."""

    timestamp_utc: str
    event: str
    decision: str
    via: str
    proof: str
    stage_serial: int | str
    apply_ms: float | str
    scan_ms: float | str
    preset_id: str
    observed_delegate: str
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp_utc": self.timestamp_utc,
            "event": self.event,
            "decision": self.decision,
            "via": self.via,
            "proof": self.proof,
            "stage_serial": self.stage_serial,
            "apply_ms": self.apply_ms,
            "scan_ms": self.scan_ms,
            "preset_id": self.preset_id,
            "observed_delegate": self.observed_delegate,
            "metrics": dict(self.metrics),
        }


def _is_real_usd_prim(prim: Any) -> bool:
    return Usd is not None and isinstance(prim, Usd.Prim)


def _safe_node_path(node: Any) -> str:
    if node is None:
        return ""
    path_attr = getattr(node, "path", None)
    if callable(path_attr):
        try:
            return str(path_attr())
        except Exception:
            return ""
    if isinstance(path_attr, str):
        return path_attr
    return ""


def _read_stage_readonly(display_lop: Any) -> Any:
    """Return the cached stage with viewport overrides preserved; never cooks."""
    if display_lop is None:
        return None
    stage_fn = getattr(display_lop, "stage", None)
    if not callable(stage_fn):
        return getattr(display_lop, "stage", None)
    try:
        return stage_fn(apply_viewport_overrides=True)
    except TypeError:
        try:
            return stage_fn()
        except Exception:
            return None
    except Exception:
        return None


def _extract_stage_serial(display_lop: Any, stage: Any) -> int | str:
    for attr_name in ("stageSerial", "stageGenId", "stage_serial"):
        candidate = getattr(display_lop, attr_name, None) if display_lop is not None else None
        if callable(candidate):
            try:
                val = candidate()
                if isinstance(val, (int, str)) and not isinstance(val, bool) and str(val) != "":
                    return val
            except Exception:
                pass
        elif isinstance(candidate, (int, str)) and not isinstance(candidate, bool) and str(candidate) != "":
            return candidate

    if stage is not None:
        for attr_name in ("stage_serial", "serial"):
            candidate = getattr(stage, attr_name, None)
            if callable(candidate):
                try:
                    val = candidate()
                    if isinstance(val, (int, str)) and not isinstance(val, bool) and str(val) != "":
                        return val
                except Exception:
                    pass
            elif isinstance(candidate, (int, str)) and not isinstance(candidate, bool) and str(candidate) != "":
                return candidate

    if display_lop is not None:
        cook_count_fn = getattr(display_lop, "cookCount", None)
        if callable(cook_count_fn):
            try:
                val = cook_count_fn()
                if isinstance(val, int) and not isinstance(val, bool):
                    return val
            except Exception:
                pass

    return UNKNOWN


def _prim_path(prim: Any) -> str:
    get_path = getattr(prim, "GetPath", None)
    if callable(get_path):
        try:
            return str(get_path())
        except Exception:
            pass
    return str(getattr(prim, "path", ""))


def _prim_name(prim: Any, path_str: str) -> str:
    get_name = getattr(prim, "GetName", None)
    if callable(get_name):
        try:
            name = str(get_name())
            if name:
                return name
        except Exception:
            pass
    return path_str.rsplit("/", 1)[-1] if path_str else ""


def _prim_type_name(prim: Any) -> str:
    get_type = getattr(prim, "GetTypeName", None)
    if callable(get_type):
        try:
            return str(get_type())
        except Exception:
            pass
    return str(getattr(prim, "type_name", ""))


def _prim_applied_schemas(prim: Any) -> set[str]:
    get_schemas = getattr(prim, "GetAppliedSchemas", None)
    if callable(get_schemas):
        try:
            return {str(s) for s in (get_schemas() or ())}
        except Exception:
            return set()
    raw = getattr(prim, "applied_schemas", None)
    if raw:
        return {str(s) for s in raw}
    return set()


def _get_valid_attr(prim: Any, attr_name: str) -> Any:
    get_attr = getattr(prim, "GetAttribute", None)
    if not callable(get_attr):
        return None
    try:
        attr = get_attr(attr_name)
    except Exception:
        return None
    if attr is None:
        return None
    is_valid = getattr(attr, "IsValid", None)
    if callable(is_valid):
        try:
            if not is_valid():
                return None
        except Exception:
            return None
    return attr


def _has_attr_value(prim: Any, attr_name: str) -> bool:
    """O(1) existence/opinion check avoiding vertex array payload reads."""
    attr = _get_valid_attr(prim, attr_name)
    if attr is None:
        return False

    has_opinion = getattr(attr, "HasAuthoredValueOpinion", None)
    has_value = getattr(attr, "HasValue", None)
    if callable(has_opinion) or callable(has_value):
        try:
            if callable(has_opinion) and has_opinion():
                return True
            if callable(has_value) and has_value():
                return True
            return False
        except Exception:
            pass

    get_fn = getattr(attr, "Get", None)
    if callable(get_fn):
        try:
            val = get_fn()
            return val is not None
        except Exception:
            return False
    return True


def _get_attr_value(prim: Any, attr_name: str, default: Any = None) -> Any:
    attr = _get_valid_attr(prim, attr_name)
    if attr is None:
        return default
    get_fn = getattr(attr, "Get", None)
    if not callable(get_fn):
        return default
    try:
        val = get_fn()
        return default if val is None else val
    except Exception:
        return default


def _rel_has_targets_or_opinion(rel: Any) -> bool:
    if rel is None:
        return False
    is_valid = getattr(rel, "IsValid", None)
    if callable(is_valid):
        try:
            if not is_valid():
                return False
        except Exception:
            return False
    get_targets = getattr(rel, "GetTargets", None)
    if callable(get_targets):
        try:
            targets = get_targets()
            if targets:
                return True
        except Exception:
            pass
    has_opinion = getattr(rel, "HasAuthoredTargets", None)
    if callable(has_opinion):
        try:
            if has_opinion():
                return True
        except Exception:
            pass
    return False


def _direct_material_binding_targets(prim: Any) -> list[str]:
    targets: list[str] = []
    get_rel = getattr(prim, "GetRelationship", None)
    if callable(get_rel):
        try:
            rel = get_rel("material:binding")
            if _rel_has_targets_or_opinion(rel):
                get_targets = getattr(rel, "GetTargets", None)
                if callable(get_targets):
                    targets.extend(str(t) for t in (get_targets() or ()))
                else:
                    targets.append("<bound>")
        except Exception:
            pass

    if not targets:
        get_rels = getattr(prim, "GetRelationships", None)
        if callable(get_rels):
            try:
                for rel in get_rels() or ():
                    get_name = getattr(rel, "GetName", None)
                    rname = str(get_name()) if callable(get_name) else ""
                    if rname == "material:binding" or rname.startswith("material:binding:"):
                        if _rel_has_targets_or_opinion(rel):
                            get_targets = getattr(rel, "GetTargets", None)
                            if callable(get_targets):
                                targets.extend(str(t) for t in (get_targets() or ()))
                            else:
                                targets.append("<bound>")
            except Exception:
                pass

    if not targets and getattr(prim, "has_material_binding", False):
        targets.append("<bound>")
    return targets


def _resolve_material_binding(
    prim: Any,
    stage: Any,
    bindings_cache: Any,
    collection_cache: Any,
    mock_ancestor_cache: dict[str, bool],
) -> tuple[bool, bool]:
    """Return (has_material_binding, has_displacement_signal)."""
    has_binding = False
    has_displacement = False

    if _is_real_usd_prim(prim) and UsdShade is not None:
        try:
            api = UsdShade.MaterialBindingAPI(prim)
            if bindings_cache is not None and collection_cache is not None:
                material, _ = api.ComputeBoundMaterial(bindings_cache, collection_cache)
            else:
                material, _ = api.ComputeBoundMaterial()
            if material and material.GetPrim().IsValid():
                has_binding = True
                for render_context in ("", "karma", "mtlx"):
                    try:
                        disp_out = (
                            material.GetDisplacementOutput(render_context)
                            if render_context
                            else material.GetDisplacementOutput()
                        )
                        if disp_out and disp_out.HasConnectedSource():
                            has_displacement = True
                            break
                    except Exception:
                        pass
        except Exception:
            pass

    if not has_binding:
        direct_targets = _direct_material_binding_targets(prim)
        if direct_targets:
            has_binding = True
            if stage is not None:
                get_prim_at = getattr(stage, "GetPrimAtPath", None)
                if callable(get_prim_at):
                    for target_path in direct_targets:
                        if not target_path.startswith("/"):
                            continue
                        try:
                            mat_prim = get_prim_at(target_path)
                        except Exception:
                            mat_prim = None
                        if mat_prim is not None:
                            for disp_attr in (
                                "outputs:displacement",
                                "outputs:karma:displacement",
                                "outputs:mtlx:displacement",
                            ):
                                if _has_attr_value(mat_prim, disp_attr):
                                    has_displacement = True
                                    break
                            if has_displacement:
                                break
        else:
            # Check inherited material binding on mock ancestor chains
            get_parent = getattr(prim, "GetParent", None)
            if callable(get_parent):
                visited: list[str] = []
                curr = None
                try:
                    curr = get_parent()
                except Exception:
                    curr = None
                while curr is not None:
                    cpath = _prim_path(curr)
                    if not cpath or cpath == "/":
                        break
                    if cpath in mock_ancestor_cache:
                        has_binding = mock_ancestor_cache[cpath]
                        break
                    visited.append(cpath)
                    if _direct_material_binding_targets(curr):
                        has_binding = True
                        break
                    next_parent = getattr(curr, "GetParent", None)
                    try:
                        curr = next_parent() if callable(next_parent) else None
                    except Exception:
                        curr = None
                for vpath in visited:
                    mock_ancestor_cache[vpath] = has_binding

    for bound_attr in _DISPLACE_BOUND_ATTRS:
        val = _get_attr_value(prim, bound_attr)
        if isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0:
            has_displacement = True
            break

    return has_binding, has_displacement


def _has_bump_override(prim: Any) -> bool:
    for attr_name in _BUMP_OVERRIDE_ATTRS:
        val = _get_attr_value(prim, attr_name)
        if val is True or (isinstance(val, (int, float)) and not isinstance(val, bool) and val != 0):
            return True
    return False


def _is_light_prim(prim: Any, type_name: str, applied_schemas: set[str]) -> bool:
    if _is_real_usd_prim(prim) and UsdLux is not None:
        try:
            if prim.HasAPI(UsdLux.LightAPI):
                return True
        except Exception:
            pass
    if any(schema == "LightAPI" or schema.endswith("LightAPI") for schema in applied_schemas):
        return True
    if type_name.endswith("Light"):
        return True
    return False


def _is_volume_prim(prim: Any, type_name: str, prim_name: str) -> bool:
    if _is_real_usd_prim(prim) and UsdVol is not None:
        try:
            if prim.IsA(UsdVol.Volume) or prim.IsA(UsdVol.OpenVDBAsset):
                return True
        except Exception:
            pass
    if type_name in _VOLUME_TYPE_NAMES:
        return True
    if "fog" in prim_name.lower() and "volume" in type_name.lower():
        return True
    return False


def _parent_is_volume(prim: Any) -> bool:
    get_parent = getattr(prim, "GetParent", None)
    if not callable(get_parent):
        return False
    try:
        parent = get_parent()
    except Exception:
        return False
    if parent is None:
        return False
    if _is_real_usd_prim(parent) and UsdVol is not None:
        try:
            if parent.IsA(UsdVol.Volume):
                return True
        except Exception:
            pass
    return _prim_type_name(parent) == "Volume"


def _is_point_instancer(prim: Any, type_name: str) -> bool:
    if _is_real_usd_prim(prim) and UsdGeom is not None:
        try:
            if prim.IsA(UsdGeom.PointInstancer):
                return True
        except Exception:
            pass
    return type_name == "PointInstancer"


def _is_mesh_prim(prim: Any, type_name: str) -> bool:
    if _is_real_usd_prim(prim) and UsdGeom is not None:
        try:
            if prim.IsA(UsdGeom.Mesh):
                return True
        except Exception:
            pass
    return type_name == "Mesh"


def _has_image_filter_list_on_prim(prim: Any, type_name: str, applied_schemas: set[str]) -> bool:
    if type_name == "HoudiniImageFilterList" or "HoudiniImageFilterList" in applied_schemas:
        return True
    get_rel = getattr(prim, "GetRelationship", None)
    if callable(get_rel):
        try:
            if _rel_has_targets_or_opinion(get_rel("husk:orderedImageFilters")):
                return True
        except Exception:
            pass
    if _has_attr_value(prim, "husk:orderedImageFilters"):
        val = _get_attr_value(prim, "husk:orderedImageFilters")
        if val is None or val:
            return True
    return False


def _is_op_cop_path(val: Any) -> bool:
    if val is None:
        return False
    raw = getattr(val, "path", None)
    path_text = str(raw if raw is not None else val).strip().lower()
    return path_text.startswith("op:") and (
        "cop" in path_text or "texturemateriallibrary" in path_text or path_text.startswith("op:/")
    )


def _shader_has_embedded_cop_op_path(prim: Any, type_name: str) -> bool:
    if type_name != "Shader":
        return False
    checked: set[str] = set()
    for attr_name in _SHADER_FILE_ATTRS:
        checked.add(attr_name)
        if _is_op_cop_path(_get_attr_value(prim, attr_name)):
            return True
    get_authored = getattr(prim, "GetAuthoredAttributes", None)
    if callable(get_authored):
        try:
            for attr in get_authored() or ():
                get_name = getattr(attr, "GetName", None)
                name = str(get_name()) if callable(get_name) else ""
                if not name or name in checked:
                    continue
                get_type = getattr(attr, "GetTypeName", None)
                type_str = str(get_type()).lower() if callable(get_type) else ""
                if "asset" in type_str or any(
                    tok in name.lower() for tok in ("file", "texture", "image", "map")
                ):
                    get_fn = getattr(attr, "Get", None)
                    if callable(get_fn) and _is_op_cop_path(get_fn()):
                        return True
        except Exception:
            pass
    return False


def _read_env_value(key: str, hou_module: Any | None = None) -> str | None:
    env_val = os.environ.get(key)
    if env_val is not None and env_val != "":
        return env_val
    if hou_module is not None:
        getenv_fn = getattr(hou_module, "getenv", None)
        if callable(getenv_fn):
            try:
                hval = getenv_fn(key)
                if hval is not None and str(hval) != "":
                    return str(hval)
            except Exception:
                pass
    return None


def _collect_xpu_env_summary(hou_module: Any | None = None) -> dict[str, str]:
    summary: dict[str, str] = {}
    for key in _XPU_ENV_KEYS:
        val = _read_env_value(key, hou_module=hou_module)
        summary[key] = val if val is not None else "UNSET"
    return summary


def _iter_stage_prims(stage: Any) -> Iterable[Any]:
    if stage is None:
        return ()
    traverse = getattr(stage, "Traverse", None)
    if callable(traverse):
        try:
            return traverse()
        except Exception:
            return ()
    prims = getattr(stage, "prims", None)
    if prims is not None:
        return prims
    return ()


def _inspect_lopnet_children(
    lopnet: Any,
) -> tuple[list[str], list[str], list[str]]:
    """Inspect LOP network children read-only for scatterinstances and COP texture libs."""
    scatter_lop_paths: list[str] = []
    scatter_missing_frustum: list[str] = []
    texture_mat_lib_paths: list[str] = []

    if lopnet is None:
        return scatter_lop_paths, scatter_missing_frustum, texture_mat_lib_paths

    children_fn = getattr(lopnet, "children", None)
    if not callable(children_fn):
        return scatter_lop_paths, scatter_missing_frustum, texture_mat_lib_paths

    try:
        children = children_fn() or ()
    except Exception:
        return scatter_lop_paths, scatter_missing_frustum, texture_mat_lib_paths

    for child in children:
        is_bypassed = getattr(child, "isBypassed", None)
        if callable(is_bypassed):
            try:
                if is_bypassed():
                    continue
            except Exception:
                pass

        node_type_name = ""
        type_fn = getattr(child, "type", None)
        if callable(type_fn):
            try:
                ntype = type_fn()
                name_fn = getattr(ntype, "name", None)
                node_type_name = str(name_fn()) if callable(name_fn) else str(ntype)
            except Exception:
                node_type_name = ""

        lowered_type = node_type_name.lower()
        base_type = lowered_type.split("::", 1)[0]
        child_path = _safe_node_path(child)

        if base_type == "scatterinstances" or "scatterinstances" in lowered_type:
            if child_path:
                scatter_lop_paths.append(child_path)
            parm_fn = getattr(child, "parm", None)
            if callable(parm_fn):
                try:
                    mask_parm = parm_fn("enablecameramask")
                    if mask_parm is not None:
                        eval_fn = getattr(mask_parm, "eval", None)
                        val = eval_fn() if callable(eval_fn) else mask_parm
                        if int(val) == 0:
                            scatter_missing_frustum.append(child_path or "<scatterinstances>")
                except Exception:
                    pass

        if base_type == "texturemateriallibrary" or "texturemateriallibrary" in lowered_type:
            texture_mat_lib_paths.append(child_path or "<texturemateriallibrary>")

    return scatter_lop_paths, scatter_missing_frustum, texture_mat_lib_paths


def scan_stage_bottlenecks(
    display_lop: Any,
    lopnet: Any | None = None,
    hou_module: Any | None = None,
    max_prims: int = 50000,
) -> tuple[StageTelemetry, list[SceneAdvisory]]:
    """Inspect a Solaris stage and LOP network strictly read-only for bottlenecks.

    Never calls ``setDisplayFlag``, never modifies USD or LOP parameters, and
    never forces additional cooks on ``display_lop``.
    """
    t0 = time.perf_counter()
    limit = max(0, int(max_prims))

    resolved_lopnet = lopnet
    if resolved_lopnet is None and display_lop is not None:
        parent_fn = getattr(display_lop, "parent", None)
        if callable(parent_fn):
            try:
                resolved_lopnet = parent_fn()
            except Exception:
                resolved_lopnet = None

    lopnet_path = _safe_node_path(resolved_lopnet)
    display_lop_path = _safe_node_path(display_lop)

    stage = _read_stage_readonly(display_lop)
    stage_serial = _extract_stage_serial(display_lop, stage)

    prim_count = 0
    truncated = False
    mesh_count = 0
    subdiv_count = 0
    volume_count = 0
    light_count = 0
    instancer_count = 0

    fog_volume_paths: list[str] = []
    lights_missing_equiangular: list[str] = []
    displaced_subdiv_paths: list[str] = []
    explicit_displaced_subdiv_count = 0
    missing_tangentu_paths: list[str] = []
    has_image_filter_list = False
    embedded_cop_shader_paths: list[str] = []
    principled_shader_paths: list[str] = []
    parent_child_counts: dict[str, int] = {}
    gaussian_splat_paths: list[str] = []
    non_light_tree_light_paths: list[str] = []
    uniform_light_sampling_settings: list[str] = []
    builtin_denoise_settings_paths: list[str] = []

    bindings_cache = None
    collection_cache = None
    if UsdShade is not None:
        try:
            bindings_cache = UsdShade.MaterialBindingAPI.BindingsCache()
            collection_cache = UsdShade.MaterialBindingAPI.CollectionQueryCache()
        except Exception:
            bindings_cache = None
            collection_cache = None
    mock_ancestor_cache: dict[str, bool] = {}

    for prim in _iter_stage_prims(stage):
        if prim_count >= limit:
            truncated = True
            break

        is_active_fn = getattr(prim, "IsActive", None)
        if callable(is_active_fn):
            try:
                if not is_active_fn():
                    continue
            except Exception:
                pass

        prim_count += 1
        prim_path = _prim_path(prim)
        prim_name = _prim_name(prim, prim_path)
        type_name = _prim_type_name(prim)
        applied_schemas = _prim_applied_schemas(prim)

        if prim_path and "/" in prim_path:
            parent_path = prim_path.rsplit("/", 1)[0] or "/"
            parent_child_counts[parent_path] = parent_child_counts.get(parent_path, 0) + 1

        if not has_image_filter_list and _has_image_filter_list_on_prim(
            prim, type_name, applied_schemas
        ):
            has_image_filter_list = True

        if type_name == "RenderSettings":
            img_flt = _get_attr_value(prim, "karma:global:imagefilter")
            if isinstance(img_flt, str) and "denoise" in img_flt.lower() and prim_path:
                builtin_denoise_settings_paths.append(prim_path)
            lsm = _get_attr_value(prim, "karma:global:light_sampling_mode")
            if isinstance(lsm, str) and lsm.strip().lower() == "uniform" and prim_path:
                uniform_light_sampling_settings.append(prim_path)

        if "gaussiansplat" in type_name.lower() or type_name == "ParticleField3DGaussianSplat":
            if prim_path:
                gaussian_splat_paths.append(prim_path)

        if _is_point_instancer(prim, type_name):
            instancer_count += 1

        if _is_volume_prim(prim, type_name, prim_name):
            if not (type_name == "OpenVDBAsset" and _parent_is_volume(prim)):
                volume_count += 1
                if prim_path:
                    fog_volume_paths.append(prim_path)

        if _is_light_prim(prim, type_name, applied_schemas):
            light_count += 1
            equi_val = _get_attr_value(prim, "inputs:karma:light:equiangular")
            is_equiangular_enabled = equi_val is True or (
                isinstance(equi_val, int) and not isinstance(equi_val, bool) and equi_val == 1
            )
            if not is_equiangular_enabled and prim_path:
                lights_missing_equiangular.append(prim_path)
            if type_name in _NON_LIGHT_TREE_TYPES and prim_path:
                non_light_tree_light_paths.append(prim_path)

        if _is_mesh_prim(prim, type_name):
            mesh_count += 1
            scheme = _get_attr_value(prim, "subdivisionScheme")
            is_subdiv = scheme is not None and str(scheme).strip() != "" and str(scheme).lower() != "none"
            if is_subdiv:
                subdiv_count += 1

            has_binding, has_displacement = _resolve_material_binding(
                prim,
                stage,
                bindings_cache,
                collection_cache,
                mock_ancestor_cache,
            )

            if is_subdiv and (has_binding or has_displacement) and not _has_bump_override(prim):
                if prim_path:
                    displaced_subdiv_paths.append(prim_path)
                if has_displacement:
                    explicit_displaced_subdiv_count += 1

            if has_binding and not _has_attr_value(prim, "primvars:tangentu"):
                if prim_path:
                    missing_tangentu_paths.append(prim_path)

        if type_name == "Shader":
            shader_id = str(_get_attr_value(prim, "info:id") or "").lower()
            source_type = str(_get_attr_value(prim, "info:implementationSource") or "").lower()
            if (
                any(tok in shader_id for tok in _PRINCIPLED_SHADER_TOKENS)
                or "vex" in source_type
            ) and prim_path:
                principled_shader_paths.append(prim_path)

        if _shader_has_embedded_cop_op_path(prim, type_name):
            if prim_path:
                embedded_cop_shader_paths.append(prim_path)

    flat_hierarchy_parents = [
        f"{p} ({count} children)"
        for p, count in sorted(parent_child_counts.items())
        if count > FLAT_HIERARCHY_SIBLING_THRESHOLD
    ]
    double_denoiser_paths = (
        list(builtin_denoise_settings_paths)
        if has_image_filter_list and builtin_denoise_settings_paths
        else []
    )
    hw_snap = probe_hardware()

    scatter_lop_paths, scatter_missing_frustum, texture_mat_lib_paths = _inspect_lopnet_children(
        resolved_lopnet
    )
    cop_target_paths = list(dict.fromkeys([*texture_mat_lib_paths, *embedded_cop_shader_paths]))
    has_texture_material_lib = bool(cop_target_paths)

    xpu_env_summary = _collect_xpu_env_summary(hou_module=hou_module)

    advisories: list[SceneAdvisory] = []

    # 1. Fog/Volume + Lights missing Equiangular MIS
    if fog_volume_paths and lights_missing_equiangular:
        advisories.append(
            SceneAdvisory(
                id="equiangular_mis",
                severity="warning",
                category="lighting",
                title="Enable Equiangular MIS for Volume/Fog Lighting",
                mechanism=(
                    f"Detected {len(fog_volume_paths)} volume prim(s) alongside "
                    f"{len(lights_missing_equiangular)} light(s) without "
                    "inputs:karma:light:equiangular=True. Distance-only sampling in "
                    "participating media produces high volumetric variance near lights."
                ),
                recommendation=(
                    "Enable Equiangular MIS on affected light::2.0 LOPs "
                    "(xn__inputskarmalightequiangular_control_2xbff / "
                    "xn__inputskarmalightequiangular_1kbff, USD inputs:karma:light:equiangular=True). "
                    "Zero-restart safe."
                ),
                target_paths=list(lights_missing_equiangular),
                triggers_restart=False,
                action_kind="usd_attribute_edit",
            )
        )

    # 2. scatterinstances LOPs without camera frustum mask
    if scatter_missing_frustum:
        advisories.append(
            SceneAdvisory(
                id="scatter_camera_frustum",
                severity="warning",
                category="instancing",
                title="Enable Camera Frustum Mask on Scatter Instances",
                mechanism=(
                    f"Found {len(scatter_missing_frustum)} scatterinstances LOP node(s) with "
                    "enablecameramask=0, generating off-camera PointInstancer transforms "
                    "that inflate USD scene-index sync and raytracing BVH memory."
                ),
                recommendation=(
                    "Set enablecameramask=1 (with enablecamera/camerapath and optional "
                    "cameramaskpadding) on the listed scatterinstances LOPs."
                ),
                target_paths=list(scatter_missing_frustum),
                triggers_restart=False,
                action_kind="lop_parm_edit",
            )
        )

    # 3. Subdiv meshes with material binding / displacement and no bump override (RESTART WARNING)
    if displaced_subdiv_paths:
        disp_detail = (
            f" ({explicit_displaced_subdiv_count} with explicit displacement output/bound)"
            if explicit_displaced_subdiv_count > 0
            else ""
        )
        advisories.append(
            SceneAdvisory(
                id="subdiv_displacement_dicing",
                severity="warning",
                category="geometry",
                title="Subdivision Displacement & Dicing Overhead (Hydra Restart Required)",
                mechanism=(
                    f"Found {len(displaced_subdiv_paths)} subdivision mesh(es) "
                    f"(subdivisionScheme != 'none') with material binding or displacement"
                    f"{disp_detail} and no bump-only override. WARNING: Modifying "
                    "karma:global:dicingqualityscale or karma:object:* attributes is listed "
                    "in BRAY_HdKarma_base.restartrendersettings and WILL TRIGGER A FULL "
                    "HYDRA RENDER RESTART."
                ),
                recommendation=(
                    "Use a bump-only lookdev shader variant or intentionally accept a Hydra "
                    "render restart before adjusting karma:global:dicingqualityscale or "
                    "karma:object:* dicing settings on the listed subdivision meshes."
                ),
                target_paths=list(displaced_subdiv_paths),
                triggers_restart=True,
                action_kind="restart_required_override",
            )
        )

    # 4. Material-bound meshes missing primvars:tangentu (Vulkan / Karma parity)
    if missing_tangentu_paths:
        advisories.append(
            SceneAdvisory(
                id="vulkan_tangentu_parity",
                severity="info",
                category="viewport_parity",
                title="Material-Bound Meshes Missing primvars:tangentu",
                mechanism=(
                    f"{len(missing_tangentu_paths)} material-bound UsdGeom.Mesh prim(s) lack "
                    "primvars:tangentu. Houdini VK (HD_HoudiniRendererPlugin) and Karma "
                    "(BRAY_HdKarma / BRAY_HdKarmaXPU) diverge when deriving fallback tangent "
                    "frames for normal maps and anisotropic shading."
                ),
                recommendation=(
                    "Author primvars:tangentu (for example with a PolyFrame SOP in MikkT/UV "
                    "tangent mode before SOP Import) on shaded meshes for viewport delegate parity."
                ),
                target_paths=list(missing_tangentu_paths),
                triggers_restart=False,
                action_kind="geometry_prep",
            )
        )

    # 5a. Karma XPU blending threads env check
    blending_raw = xpu_env_summary.get("KARMA_XPU_NUM_PER_DEVICE_BLENDING_THREADS", "UNSET")
    blending_low_or_unset = True
    if blending_raw != "UNSET":
        try:
            blending_low_or_unset = int(blending_raw) <= 1
        except ValueError:
            blending_low_or_unset = True
    if blending_low_or_unset:
        advisories.append(
            SceneAdvisory(
                id="xpu_blending_threads",
                severity="warning",
                category="xpu",
                title="Configure KARMA_XPU_NUM_PER_DEVICE_BLENDING_THREADS",
                mechanism=(
                    f"KARMA_XPU_NUM_PER_DEVICE_BLENDING_THREADS is currently {blending_raw!r} "
                    "(unset or <= 1), limiting multi-threaded per-device buffer blending "
                    "throughput in Karma XPU."
                ),
                recommendation=(
                    "Set KARMA_XPU_NUM_PER_DEVICE_BLENDING_THREADS=2 (or 4 on high-core-count "
                    "hosts) in houdini.env or your Houdini package JSON before launching Houdini."
                ),
                target_paths=[],
                triggers_restart=False,
                action_kind="env_config",
            )
        )

    # 5b. Embedded Copernicus texturemateriallibrary OpenCL VRAM hygiene
    if has_texture_material_lib:
        advisories.append(
            SceneAdvisory(
                id="opencl_vram_hygiene",
                severity="warning",
                category="memory",
                title="Embedded Copernicus Texture Material Library VRAM Contention",
                mechanism=(
                    f"Detected {len(cop_target_paths)} texturemateriallibrary / op: COP "
                    "texture source(s). Live Copernicus OpenCL buffers share GPU VRAM with "
                    "Karma XPU (OptiX/CUDA), risking VRAM pressure or CPU fallback during IPR."
                ),
                recommendation=(
                    "Bake Copernicus textures to tiled .rat/.exr assets on disk for lookdev, "
                    "or isolate OpenCL via HOUDINI_OCL_DEVICENUMBER / clear COP caches when "
                    "rendering on Karma XPU."
                ),
                target_paths=cop_target_paths,
                triggers_restart=False,
                action_kind="cop_hygiene",
            )
        )

    # 6. Principled / VEX legacy shader fallback on Karma XPU (solaris/karma_xpu.md)
    if principled_shader_paths:
        advisories.append(
            SceneAdvisory(
                id="xpu_principled_shader_fallback",
                severity="warning",
                category="shading",
                title="Legacy Principled/VEX Shaders Approximated on Karma XPU",
                mechanism=(
                    f"Detected {len(principled_shader_paths)} Principled/VEX shader prim(s). "
                    "Karma XPU does not natively execute VEX shaders and falls back to "
                    "UsdPreviewSurface approximations, causing look divergence and slower shading."
                ),
                recommendation=(
                    "Migrate materials to native MaterialX (mtlxstandard_surface / OpenPBR) "
                    "for full Karma XPU GPU compilation and CPU/XPU parity."
                ),
                target_paths=list(principled_shader_paths),
                triggers_restart=False,
                action_kind="shader_migration",
            )
        )

    # 7. Flat high-sibling prim hierarchies (>500 children under one parent) (solaris/performance.md)
    if flat_hierarchy_parents:
        advisories.append(
            SceneAdvisory(
                id="flat_hierarchy_siblings",
                severity="warning",
                category="usd_performance",
                title="Flat High-Sibling USD Hierarchy Slows Composition & Hydra Sync",
                mechanism=(
                    f"Found {len(flat_hierarchy_parents)} parent prim(s) exceeding "
                    f"{FLAT_HIERARCHY_SIBLING_THRESHOLD} direct children: "
                    f"{', '.join(flat_hierarchy_parents[:3])}. Flat sibling lists degrade "
                    "Solaris Scene Graph Tree responsiveness and Hydra scene-index updates."
                ),
                recommendation=(
                    "Group large sibling sets into nested Xform scopes or convert repeated "
                    "assets into UsdGeom.PointInstancer prototypes."
                ),
                target_paths=list(flat_hierarchy_parents),
                triggers_restart=False,
                action_kind="hierarchy_refactor",
            )
        )

    # 8. Gaussian Splat shadow & indirect ray overhead (solaris/karma_xpu.md:293)
    if gaussian_splat_paths:
        advisories.append(
            SceneAdvisory(
                id="gaussian_splat_shadows",
                severity="info",
                category="geometry",
                title="3D Gaussian Splat Prims Active in Karma Path Tracing",
                mechanism=(
                    f"Detected {len(gaussian_splat_paths)} ParticleField3DGaussianSplat prim(s). "
                    "Gaussian splats add dense semi-transparent ray traversal cost for shadow "
                    "and indirect bounce rays in Karma XPU."
                ),
                recommendation=(
                    "Use KarmaRT Clay/Focus Crop during layout or disable indirect/shadow "
                    "visibility on Gaussian Splat lookdev proxies when iterating on lighting."
                ),
                target_paths=list(gaussian_splat_paths),
                triggers_restart=False,
                action_kind="advisory_only",
            )
        )

    # 9. Multi-light Uniform / Non-Light-Tree sampling overhead (solaris/kug/sampling_xpu.md)
    if light_count >= MULTI_LIGHT_TREE_THRESHOLD and (
        uniform_light_sampling_settings or len(non_light_tree_light_paths) >= 4
    ):
        targets = uniform_light_sampling_settings or non_light_tree_light_paths
        advisories.append(
            SceneAdvisory(
                id="light_tree_non_supported_lights",
                severity="warning",
                category="lighting",
                title="Multi-Light Scene Bypassing Light Tree Acceleration",
                mechanism=(
                    f"Scene has {light_count} lights with "
                    + (
                        "karma:global:light_sampling_mode='Uniform'"
                        if uniform_light_sampling_settings
                        else f"{len(non_light_tree_light_paths)} Dome/Distant/Portal lights outside Light Tree"
                    )
                    + ", increasing per-sample shadow ray variance."
                ),
                recommendation=(
                    "Switch karma:global:light_sampling_mode to 'Light Tree' (Interactive Lookdev "
                    "enables this automatically) and consolidate redundant Dome/Distant lights."
                ),
                target_paths=list(targets),
                triggers_restart=False,
                action_kind="usd_attribute_edit",
            )
        )

    # 10. Double-denoiser conflict (H22 husk:orderedImageFilters + legacy karma:global:imagefilter)
    if double_denoiser_paths:
        advisories.append(
            SceneAdvisory(
                id="double_denoiser_conflict",
                severity="warning",
                category="denoising",
                title="Double-Denoiser Risk: USD Image Filters + Built-in Karma Denoiser",
                mechanism=(
                    "Stage contains Houdini 22 USD Image Filters (husk:orderedImageFilters / "
                    "HoudiniImageFilterList) while RenderSettings also sets karma:global:imagefilter "
                    "denoise. Running two denoisers wastes VRAM/ms and over-blurs detail."
                ),
                recommendation=(
                    "KarmaRT v2.0 automatically suppresses its built-in imagefilter override when "
                    "USD Image Filters are present; clean the duplicate production denoiser on "
                    "karmarendersettings."
                ),
                target_paths=list(double_denoiser_paths),
                triggers_restart=False,
                action_kind="advisory_only",
            )
        )

    # 11. Live Hardware: Cold Houdini 22 OptiX Kernel Cache & VRAM Eviction Risk
    if hw_snap.gpus:
        if not hw_snap.optix_cache_ready:
            advisories.append(
                SceneAdvisory(
                    id="optix_cache_cold",
                    severity="info",
                    category="xpu",
                    title="Houdini 22 OptiX Pre-Compiled Shader Cache Is Cold",
                    mechanism=(
                        f"No pre-compiled Houdini 22 OptiX kernels found at "
                        f"{hw_snap.optix_cache_dir or 'OptixCache/Houdini22.0'}. First-frame "
                        "Karma XPU compilation incurs multi-second cold-start stalls."
                    ),
                    recommendation=(
                        "Run SideFX 'Karma -> Pre-compile Shaders' or 'husk --precompile-shaders' "
                        "once per driver update to warm the Houdini 22 OptiX cache."
                    ),
                    target_paths=[hw_snap.optix_cache_dir] if hw_snap.optix_cache_dir else [],
                    triggers_restart=False,
                    action_kind="precompile_cache",
                )
            )
        if hw_snap.vram_eviction_risk in ("WARN", "CRITICAL"):
            primary = hw_snap.gpus[0]
            advisories.append(
                SceneAdvisory(
                    id="gpu_vram_eviction_risk",
                    severity="warning" if hw_snap.vram_eviction_risk == "WARN" else "error",
                    category="memory",
                    title=f"GPU VRAM Pressure {hw_snap.vram_eviction_risk} ({primary.vram_used_pct:.1f}% Used)",
                    mechanism=(
                        f"{primary.short_name} is using {primary.vram_used_mib}/{primary.vram_total_mib} MiB "
                        "VRAM. Exceeding VRAM triggers Karma XPU OptiX eviction or slow CPU fallback."
                    ),
                    recommendation=(
                        "Drop KarmaRT Res Scale to 0.5x, enable Focus Crop, set "
                        "KARMA_XPU_OPTIX_SPARSE_TEXTURES=1, or Yield interactive viewport before disk renders."
                    ),
                    target_paths=[],
                    triggers_restart=False,
                    action_kind="vram_hygiene",
                )
            )

    scan_ms = round((time.perf_counter() - t0) * 1000.0, 3)

    telemetry = StageTelemetry(
        lopnet_path=lopnet_path,
        display_lop_path=display_lop_path,
        stage_serial=stage_serial,
        scan_ms=scan_ms,
        prim_count=prim_count,
        truncated=truncated,
        mesh_count=mesh_count,
        subdiv_count=subdiv_count,
        volume_count=volume_count,
        light_count=light_count,
        instancer_count=instancer_count,
        scatter_lop_paths=scatter_lop_paths,
        fog_volume_paths=fog_volume_paths,
        lights_missing_equiangular=lights_missing_equiangular,
        displaced_subdiv_paths=displaced_subdiv_paths,
        missing_tangentu_paths=missing_tangentu_paths,
        has_image_filter_list=has_image_filter_list,
        has_texture_material_lib=has_texture_material_lib,
        xpu_env_summary=xpu_env_summary,
        principled_shader_paths=principled_shader_paths,
        flat_hierarchy_parents=flat_hierarchy_parents,
        gaussian_splat_paths=gaussian_splat_paths,
        non_light_tree_light_paths=non_light_tree_light_paths,
        double_denoiser_paths=double_denoiser_paths,
        hardware_summary=hw_snap.summary_badge,
    )
    return telemetry, advisories


def _default_ledger_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base_dir = Path(local_app_data) / "MythAndLogic" / "KarmaRT"
    else:
        base_dir = Path.home() / ".local" / "share" / "MythAndLogic" / "KarmaRT"
    return base_dir / "karma_rt_ledger.jsonl"


def _coerce_str_or_unknown(value: Any) -> str:
    if value is None:
        return UNKNOWN
    text = str(value).strip()
    return text if text else UNKNOWN


def _coerce_serial_or_unknown(value: Any) -> int | str:
    if value is None or isinstance(value, bool):
        return UNKNOWN
    if isinstance(value, int):
        return value
    text = str(value).strip()
    return text if text else UNKNOWN


def _coerce_number_or_unknown(value: Any) -> float | str:
    if value is None or isinstance(value, bool):
        return UNKNOWN
    if isinstance(value, (int, float)):
        num = float(value)
        if math.isnan(num) or math.isinf(num):
            return UNKNOWN
        return round(num, 4)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped.upper() == UNKNOWN:
            return UNKNOWN
        try:
            num = float(stripped)
            if math.isnan(num) or math.isinf(num):
                return UNKNOWN
            return round(num, 4)
        except ValueError:
            return UNKNOWN
    return UNKNOWN


def _sanitize_json_value(value: Any) -> Any:
    if value is None:
        return UNKNOWN
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return UNKNOWN if (math.isnan(value) or math.isinf(value)) else value
    if isinstance(value, str):
        return value if value.strip() else UNKNOWN
    if isinstance(value, dict):
        return {str(k): _sanitize_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_json_value(v) for v in value]
    return str(value)


def _extract_numeric_series(entries: list[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for entry in entries:
        raw = entry.get(key)
        if isinstance(raw, bool) or raw is None:
            continue
        if isinstance(raw, (int, float)):
            num = float(raw)
            if not (math.isnan(num) or math.isinf(num)):
                values.append(num)
    return values


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if len(sorted_vals) == 1:
        return round(float(sorted_vals[0]), 4)
    rank = (pct / 100.0) * (len(sorted_vals) - 1)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return round(float(sorted_vals[lo]), 4)
    frac = rank - lo
    return round(float(sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])), 4)


def _summarize_series(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "median": UNKNOWN,
            "p90": UNKNOWN,
            "max": UNKNOWN,
        }
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "median": round(float(statistics.median(ordered)), 4),
        "p90": _percentile(ordered, 90.0),
        "max": round(float(ordered[-1]), 4),
    }


class LatencyLedger:
    """Append-only, non-gating JSONL telemetry ledger for KarmaRT."""

    def __init__(self, ledger_path: str | os.PathLike[str] | None = None) -> None:
        self.ledger_path: Path = (
            Path(ledger_path) if ledger_path is not None else _default_ledger_path()
        )
        self.last_error: str | None = None

    def record(
        self,
        event: str | None = None,
        decision: str | None = None,
        via: str | None = None,
        proof: str | None = None,
        stage_serial: int | str | None = None,
        apply_ms: float | int | str | None = None,
        scan_ms: float | int | str | None = None,
        preset_id: str | None = None,
        observed_delegate: str | None = None,
        metrics: dict[str, Any] | None = None,
        timestamp_utc: str | None = None,
    ) -> LedgerEntry:
        """Append a structured ledger record without ever raising on I/O errors."""
        ts = (
            timestamp_utc.strip()
            if isinstance(timestamp_utc, str) and timestamp_utc.strip()
            else datetime.now(timezone.utc).isoformat()
        )
        clean_metrics = (
            {str(k): _sanitize_json_value(v) for k, v in metrics.items()}
            if isinstance(metrics, dict)
            else {}
        )
        entry = LedgerEntry(
            timestamp_utc=ts,
            event=_coerce_str_or_unknown(event),
            decision=_coerce_str_or_unknown(decision),
            via=_coerce_str_or_unknown(via),
            proof=_coerce_str_or_unknown(proof),
            stage_serial=_coerce_serial_or_unknown(stage_serial),
            apply_ms=_coerce_number_or_unknown(apply_ms),
            scan_ms=_coerce_number_or_unknown(scan_ms),
            preset_id=_coerce_str_or_unknown(preset_id),
            observed_delegate=_coerce_str_or_unknown(observed_delegate),
            metrics=clean_metrics,
        )

        try:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            serialized = json.dumps(entry.to_dict(), ensure_ascii=False, separators=(",", ":"))
            with self.ledger_path.open("a", encoding="utf-8") as handle:
                handle.write(serialized + "\n")
            self.last_error = None
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"

        return entry

    def _read_all_entries(self) -> list[dict[str, Any]]:
        if not self.ledger_path.exists():
            return []
        entries: list[dict[str, Any]] = []
        try:
            with self.ledger_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        parsed = json.loads(stripped)
                    except Exception:
                        continue
                    if isinstance(parsed, dict):
                        entries.append(parsed)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return []
        return entries

    def read_recent(self, limit: int = 25) -> list[dict[str, Any]]:
        """Return up to the most recent ``limit`` valid ledger entries."""
        count = int(limit)
        if count <= 0:
            return []
        entries = self._read_all_entries()
        return entries[-count:]

    def summary_stats(self) -> dict[str, Any]:
        """Compute count and median/p90/max statistics for numeric apply_ms & scan_ms."""
        entries = self._read_all_entries()
        apply_stats = _summarize_series(_extract_numeric_series(entries, "apply_ms"))
        scan_stats = _summarize_series(_extract_numeric_series(entries, "scan_ms"))
        return {
            "count": len(entries),
            "apply_ms": apply_stats,
            "scan_ms": scan_stats,
            "apply_ms_count": apply_stats["count"],
            "apply_ms_median": apply_stats["median"],
            "apply_ms_p90": apply_stats["p90"],
            "apply_ms_max": apply_stats["max"],
            "scan_ms_count": scan_stats["count"],
            "scan_ms_median": scan_stats["median"],
            "scan_ms_p90": scan_stats["p90"],
            "scan_ms_max": scan_stats["max"],
        }


__all__ = [
    "UNKNOWN",
    "StageTelemetry",
    "SceneAdvisory",
    "LedgerEntry",
    "scan_stage_bottlenecks",
    "LatencyLedger",
]
