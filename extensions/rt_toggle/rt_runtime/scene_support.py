"""Bounded, read-only inspection of the selected viewer's displayed USD stage.

This is a capability-profile comparison, not a census of translated Hydra prims
or a claim about the rendered image. No renderer, node or USD opinion is changed.
"""
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import threading
import time
from .material_support import MaterialInspector, validate_policy
from .texture_support import authored_asset


@dataclass(frozen=True)
class CapabilityProfile:
    path: str
    version: str
    lights: frozenset
    aovs: frozenset
    build_id: str = ""
    limitations: tuple = ()
    materials: dict = field(default_factory=dict)
    environments: dict = field(default_factory=dict)


def load_profile(environ=None, registry=None):
    """Read installed resources only; never substitute a source-tree profile."""
    environ = os.environ if environ is None else environ
    roots = []
    explicit = environ.get("HRT_PLUGIN_ROOT")
    if explicit:
        roots.append(Path(explicit))
    else:
        if registry is None:
            try:
                from pxr import Plug
                registry = Plug.Registry()
            except ImportError:
                pass
        registered = False
        if registry is not None:
            plugin = registry.GetPluginWithName("hdRT")
            if plugin:
                roots.append(Path(plugin.resourcePath))
                registered = True
        # A registered older delegate must not borrow a newer installation's claims.
        if not registered and environ.get("HOUDINIRT_HOME"):
            roots.append(Path(environ["HOUDINIRT_HOME"]) / "renderer")
    for root in roots:
        for path in (root / "capabilities.json", root / "resources" / "capabilities.json"):
            if not path.is_file():
                continue
            if path.stat().st_size > 65536:
                raise ValueError("Installed capability profile exceeds 64 KiB.")
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict) or data.get("schema_version") != 1:
                raise ValueError("Unsupported installed capability profile schema.")
            if data.get("renderer", "HoudiniRT") != "HoudiniRT":
                raise ValueError("Capability profile belongs to another renderer.")
            for key in ("supported_lights", "supported_aovs"):
                values = data.get(key)
                if not isinstance(values, list) or not all(isinstance(v, str) and v for v in values):
                    raise ValueError("Invalid capability profile field: " + key)
            version = str(data.get("profile_version") or data.get("build_id") or "schema 1")
            limitations = data.get("limitations", [])
            if not isinstance(limitations, list) or not all(isinstance(v, str) for v in limitations):
                raise ValueError("Invalid capability profile limitations.")
            policy = data.get("material_support", {})
            if not isinstance(policy, dict):
                raise ValueError("Invalid installed material capability policy.")
            validate_policy(policy)
            environments = data.get("environment_support", {})
            if environments and environments != {"schemas": ["DomeLight", "DomeLight_1"],
                    "formats": ["latlong", "automatic"], "automatic_requires_2_to_1": True,
                    "max_textured_domes": 8, "camera_background": "transparent"}:
                raise ValueError("Unsupported environment capability policy.")
            return CapabilityProfile(str(path.resolve()), version,
                                     frozenset(data["supported_lights"]),
                                     frozenset(data["supported_aovs"]), str(data.get("build_id", "")),
                                     tuple(limitations), policy, environments)
    raise ValueError("Installed HoudiniRT capability profile was not found. Check the integrated installation.")


@dataclass(frozen=True)
class Finding:
    path: str
    feature: str
    reason: str
    kind: str = "Unsupported"


@dataclass
class SupportReport:
    viewer: str = ""
    frame: float = 0
    locked: bool = False
    state: str = "running"
    detail: str = ""
    inspected: int = 0
    skipped: int = 0
    findings: list = field(default_factory=list)
    omitted_findings: int = 0
    profile: object = None
    material_bindings: int = 0
    supported_material_bindings: int = 0

    def text(self):
        profile = self.profile
        lines = ["Stage inspection; not a native translated-scene census.",
                 "Viewer: %s | Frame: %g | Stage locked: %s" %
                 (self.viewer, self.frame, "yes" if self.locked else "no"),
                 "Inspected: %d prims; skipped: %d inactive/unloaded prims." % (self.inspected, self.skipped)]
        if profile:
            lines += ["Capability profile: " + profile.version, profile.path,
                      "Profile build: " + (profile.build_id or "not specified"),
                      "Outputs supported by profile: " + ", ".join(sorted(profile.aovs)),
                      ("Shading: opaque isotropic OpenPBR within the installed graph profile; unsupported materials use matte displayColor Lambert."
                       if profile.materials else "Shading: matte displayColor; material graphs are not evaluated.")]
            if profile.materials:
                lines.append("Inspected material assignments: %d; within the profile: %d."
                             % (self.material_bindings, self.supported_material_bindings))
                lines.append("Edit materials through Material Library / OpenPBR Surface and Assign Material.")
                if profile.materials.get("textures"):
                    lines.append("Edit textures with native MaterialX Image and UV nodes; edit HDRI through Dome Light.")
                    lines.append("Asset files are not opened by this report; image availability and decoding remain unassessed.")
            lines.extend("Profile limitation: " + item for item in profile.limitations)
        if self.detail:
            lines.append(self.detail)
        for item in self.findings:
            lines.append("[%s] %s — %s: %s" % (item.kind, item.path, item.feature, item.reason))
        if self.omitted_findings:
            lines.append("%d additional findings omitted; narrow the scene and check again." % self.omitted_findings)
        if self.state == "complete" and not self.findings:
            lines.append("No listed limitations detected in inspected prims; this is not a fidelity guarantee.")
        return "\n".join(lines)


class SceneSupportScan:
    """One main-thread snapshot, advanced in small chunks by the panel's timer."""
    def __init__(self, viewer, profile=None, *, hou_module=None, usd_modules=None,
                 clock=time.monotonic, max_prims=20000, max_findings=200):
        self._main_thread()
        if hou_module is None:
            import hou as hou_module
        if usd_modules is None:
            from pxr import Usd, UsdGeom, Tf
            usd_modules = Usd, UsdGeom, Tf
        self._usd, self._geom, self._tf = usd_modules
        self._hou, self._viewer, self._clock = hou_module, viewer, clock
        self._stage = self._iterator = self._notice = None
        self._materials = None
        self._supported_meshes, self._subset_meshes = set(), set()
        self._textured_domes = 0
        self._changed = False
        self._seen = set()
        self._max_prims, self._max_findings = max_prims, max_findings
        self.report = SupportReport()
        try:
            if not viewer.isViewingSceneGraph():
                raise ValueError("Choose a Solaris Scene Viewer.")
            self.report.viewer = viewer.name()
            self.report.frame = float(hou_module.frame())
            self.report.locked = bool(viewer.sceneGraphStageLocked())
            self.report.profile = profile or load_profile()
            self._stage = viewer.stage()
            if self._stage is None:
                raise ValueError("The selected viewer has no displayed USD stage.")
            self._time = self._usd.TimeCode(self.report.frame)
            if self.report.profile.materials:
                from pxr import UsdShade
                self._materials = MaterialInspector(UsdShade, self.report.profile.materials, self._time)
            self._notice = self._tf.Notice.Register(self._usd.Notice.ObjectsChanged,
                                                    self._on_stage_changed, self._stage)
            self._iterator = iter(self._stage.TraverseAll())
        except Exception as exc:
            self._finish("unavailable", str(exc))

    @staticmethod
    def _main_thread():
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("Scene inspection must run on Houdini's main thread.")

    def _on_stage_changed(self, notice, sender):
        self._changed = True  # No HOM calls inside a USD notice.

    def close(self):
        if self._notice is not None:
            self._notice.Revoke()
            self._notice = None
        self._iterator = self._stage = None

    def _finish(self, state, detail):
        self.report.state, self.report.detail = state, detail
        if state != "complete":
            self.close()
        else:
            self._iterator = None  # Keep the notice so edits invalidate the report.

    def cancel(self, reason="Check cancelled; these are partial findings."):
        self._main_thread()
        if self.report.state == "running":
            self._finish("partial", reason)
        elif self._stage is not None:
            self._finish("stale", reason)

    def check_current(self, viewer=None):
        self._main_thread()
        if self._stage is None:
            return
        try:
            same = viewer is None or viewer == self._viewer
            same = same and not self._changed and float(self._hou.frame()) == self.report.frame
            same = same and self._viewer.isViewingSceneGraph() and self._viewer.stage() == self._stage
            same = same and bool(self._viewer.sceneGraphStageLocked()) == self.report.locked
            if not same:
                self._finish("stale", "Viewer, frame or stage changed. Check again for current findings.")
        except Exception as exc:
            self._finish("stale", "Displayed stage is no longer available: " + str(exc))

    def step(self, *, budget_seconds=0.008, max_items=64):
        self._main_thread()
        self.check_current()
        if self.report.state != "running":
            return self.report
        deadline = self._clock() + max(0.001, budget_seconds)
        try:
            for _ in range(max_items):
                if self.report.inspected >= self._max_prims:
                    self._finish("partial", "Inspection limit reached; remaining prims were not assessed.")
                    break
                prim = next(self._iterator)
                self.report.inspected += 1
                self._inspect(prim)
                if self._changed:
                    self.check_current()
                    break
                if self._clock() >= deadline:
                    break
        except StopIteration:
            self._finish("complete", "Snapshot check complete. Edit lights through Light LOP or Light Mixer.")
        except Exception as exc:
            self._finish("partial", "Inspection stopped; remaining content is unassessed: " + str(exc))
        return self.report

    def _add(self, prim, feature, reason, kind="Unsupported"):
        path = str(prim.GetPath())
        key = path, feature, reason
        if key in self._seen:
            return
        self._seen.add(key)
        if len(self.report.findings) < self._max_findings:
            self.report.findings.append(Finding(path, feature, reason, kind))
        else:
            self.report.omitted_findings += 1

    def _value(self, prim, name, fallback=None):
        attr = prim.GetAttribute(name)
        value = attr.Get(self._time) if attr else None
        return fallback if value is None else value

    def _inspect(self, prim):
        if not prim.IsActive() or not prim.IsLoaded():
            self.report.skipped += 1
            if not prim.IsLoaded():
                self._add(prim, "Unloaded content", "Payload contents were not inspected or loaded.", "Not assessed")
            return
        typename = prim.GetTypeName()
        if prim.IsInstance() or typename == "PointInstancer":
            self._add(prim, "Instancing", "Instances are not expanded; prototype placement is not a reliable preview.")
        for rel in prim.GetRelationships():
            name = rel.GetName()
            if not self._materials and name.startswith("material:binding") and rel.GetTargets():
                self._add(prim, "Material binding", "Material graphs are ignored; affected meshes use matte displayColor.")
        if typename == "Mesh":
            self._mesh(prim)
            if self._materials:
                self._mesh_material(prim)
        elif typename == "GeomSubset" and self._materials:
            self._subset_material(prim)
        elif typename in {"BasisCurves", "NurbsCurves", "Points", "Volume", "TetMesh"}:
            self._add(prim, typename, "This geometry type is not supported by the mesh renderer.")
        elif typename.split("_")[0] in {"Cube", "Sphere", "Cone", "Cylinder", "Capsule", "Plane"}:
            self._add(prim, typename, "USD imaging may convert this primitive to a mesh; this conversion has not been verified.", "Not assessed")
        if typename.split("_")[0].endswith("Light") or any("LightAPI" in str(s) for s in prim.GetAppliedSchemas()):
            self._light(prim, typename)
        if typename == "RenderVar":
            source = str(self._value(prim, "sourceName", ""))
            source_type = str(self._value(prim, "sourceType", "raw"))
            if source_type != "raw" or source not in self.report.profile.aovs:
                self._add(prim, "RenderVar " + (source or "(empty source)"),
                          "Authored %s output is outside the capability profile; actual viewer bindings were not queried." % source_type)

    def _mesh_material(self, prim):
        binding_api = self._materials.shade.MaterialBindingAPI
        material, binding = binding_api(prim).ComputeBoundMaterial(materialPurpose="full")
        if not material:
            if binding and binding.GetTargets():
                self._add(prim, "Material fallback", "Binding %s has no valid material; using matte displayColor Lambert."
                          % binding.GetPath())
            return
        self.report.material_bindings += 1
        issues = self._materials.inspect(material)
        for issue in issues:
            if issue.kind == "Not assessed":
                self._add(prim, "Material assessment", "%s — %s Bound material: %s."
                          % (issue.path, issue.reason, material.GetPath()), issue.kind)
            else:
                self._add(prim, "Material fallback", "%s — %s. Bound material %s uses whole-mesh matte displayColor Lambert."
                          % (issue.path, issue.reason, material.GetPath()))
        path = str(prim.GetPath())
        if self._materials.texture_coordinates.get(str(material.GetPath())):
            self._texture_coordinates(prim)
        if not issues and path not in self._subset_meshes:
            self._supported_meshes.add(path)
        self.report.supported_material_bindings = len(self._supported_meshes)

    def _texture_coordinates(self, prim):
        st = self._geom.PrimvarsAPI(prim).FindPrimvarWithInheritance("st")
        if not st or not st.GetAttr().HasAuthoredValueOpinion():
            self._add(prim, "Texture coordinates", "The bound image graph requires primvars:st, but no authored st was found.")
            return
        if st.GetInterpolation() not in {"constant", "uniform", "vertex", "varying", "faceVarying"}:
            self._add(prim, "Texture coordinates", "Unsupported st interpolation: " + str(st.GetInterpolation()))
        self._add(prim, "Texture coordinate data", "st element counts, indices and finite values are checked by the native translator, "
                  "not by this bounded stage inspection.", "Not assessed")

    def _subset_material(self, prim):
        # Each subset consumes its own outer traversal item/time budget.
        if self._value(prim, "familyName", "") != "materialBind" or self._value(prim, "elementType", "face") != "face":
            return
        parent = prim.GetParent()
        if parent.GetTypeName() != "Mesh":
            return
        material, binding = self._materials.shade.MaterialBindingAPI(prim).ComputeBoundMaterial(materialPurpose="full")
        if not material or not binding:
            return
        path = str(parent.GetPath())
        self._subset_meshes.add(path)
        self._supported_meshes.discard(path)
        self.report.supported_material_bindings = len(self._supported_meshes)
        self._add(parent, "Material subsets", "Assigned face subset %s is unsupported; the whole mesh uses matte displayColor Lambert."
                  % prim.GetPath())

    def _mesh(self, prim):
        scheme = str(self._value(prim, "subdivisionScheme", "catmullClark"))
        if scheme != "none":
            self._add(prim, "Subdivision " + scheme, "Renders the polygon cage without subdivision refinement.")
        color = prim.GetAttribute("primvars:displayColor")
        if color and color.HasAuthoredValueOpinion():
            interpolation = str(color.GetMetadata("interpolation") or "constant")
            if interpolation != "constant":
                self._add(prim, "displayColor " + interpolation, "Uses only the first color as a constant matte albedo.")
            indices = prim.GetAttribute("primvars:displayColor:indices")
            if indices and indices.HasAuthoredValueOpinion():
                self._add(prim, "Indexed displayColor", "Indexed color variation is not evaluated.")
        # USD's authored normals primvar takes precedence over intrinsic normals.
        for name in ("primvars:normals", "normals"):
            normals = prim.GetAttribute(name)
            if normals and normals.HasAuthoredValueOpinion():
                interpolation = str(normals.GetMetadata("interpolation") or
                                    ("vertex" if name == "normals" else "constant"))
                if interpolation not in {"constant", "uniform", "vertex", "varying", "faceVarying"}:
                    self._add(prim, name + " interpolation", "Unsupported interpolation: " + interpolation)
                break
        opacity = prim.GetAttribute("primvars:displayOpacity")
        if opacity and opacity.HasAuthoredValueOpinion():
            self._add(prim, "displayOpacity", "Surface opacity is not evaluated; geometry is opaque.", "Limitation")

    @staticmethod
    def _asset_nonempty(value):
        return bool(getattr(value, "path", value))

    def _light(self, prim, typename):
        normalized = "DomeLight" if typename == "DomeLight_1" else typename
        token = normalized[:1].lower() + normalized[1:]
        if token not in self.report.profile.lights:
            self._add(prim, typename or "Light API", "This light type is not in the renderer capability profile.")
        elif typename in {"RectLight", "DiskLight", "SphereLight"}:
            self._add(prim, "Finite light geometry",
                      "HoudiniRT renders this light as a visible opaque emitter. Karma's renderlightgeo "
                      "and lightgeocastsshadow switches are not evaluated.", "Limitation")
        if self.report.profile.materials:
            for name in ("inputs:diffuse", "inputs:specular"):
                if self._value(prim, name, 1.0) != 1.0:
                    self._add(prim, name,
                              "OpenPBR requires light diffuse and specular controls at 1. "
                              "The diffuse factor scales both lobes; the specular factor is ignored.", "Limitation")
        for name in ("inputs:texture:file", "inputs:shaping:ies:file"):
            value, deferred = authored_asset(prim.GetAttribute(name), self._time)
            if deferred:
                self._add(prim, name, deferred, "Not assessed")
            if self._asset_nonempty(value):
                if name == "inputs:texture:file" and normalized == "DomeLight" and self.report.profile.environments:
                    self._environment(prim, value)
                else:
                    self._add(prim, name, "Finite-light textures and IES profiles are not sampled.")
        defaults = {"inputs:shaping:focus": 0.0, "inputs:shaping:cone:angle": 90.0,
                    "inputs:shaping:cone:softness": 0.0,
                    "inputs:shaping:focusTint": (0.0, 0.0, 0.0)}
        for name, default in defaults.items():
            value = self._value(prim, name, default)
            if isinstance(default, tuple):
                value = tuple(value)
            if value != default:
                self._add(prim, name, "Light shaping is not evaluated.")
        shadows = {"inputs:shadow:enable": True, "inputs:shadow:color": (0.0, 0.0, 0.0),
                   "inputs:shadow:distance": -1.0, "inputs:shadow:falloff": -1.0,
                   "inputs:shadow:falloffGamma": 1.0}
        for name, default in shadows.items():
            value = self._value(prim, name, default)
            if isinstance(default, tuple):
                value = tuple(value)
            if value != default:
                self._add(prim, name, "Only ordinary opaque shadows are supported; this shadow override is ignored.")
        for rel in prim.GetRelationships():
            name = rel.GetName()
            if rel.GetTargets() and ("filters" in name.lower() or
                                     name.startswith(("collection:lightLink:", "collection:shadowLink:"))):
                self._add(prim, name, "Light filters and light/shadow linking are not evaluated.")
        for link in ("lightLink", "shadowLink"):
            if not self._value(prim, "collection:%s:includeRoot" % link, True):
                self._add(prim, link, "Explicit light/shadow linking is not evaluated.")
        for attr in prim.GetAttributes():
            if attr.GetName().startswith("inputs:") and attr.GetConnections():
                self._add(prim, attr.GetName(), "Connected light shader inputs are not evaluated.")
        if typename == "SphereLight":
            if self._value(prim, "treatAsPoint", False):
                self._add(prim, "treatAsPoint", "Point-mode sphere lights are not supported.")
            matrix = self._geom.Xformable(prim).ComputeLocalToWorldTransform(self._time)
            axes = [[float(matrix[row][col]) for col in range(3)] for row in range(3)]
            lengths = [sum(x * x for x in axis) for axis in axes]
            largest = max(lengths)
            shear = any(abs(sum(axes[i][k] * axes[j][k] for k in range(3))) > largest * 1e-6
                        for i, j in ((0, 1), (0, 2), (1, 2)))
            if not all(math.isfinite(x) and x > 0 for x in lengths) or shear or max(lengths) - min(lengths) > largest * 1e-6:
                self._add(prim, "Sphere transform", "Nonuniform, sheared or singular sphere-light scaling is unsupported.")

    def _environment(self, prim, value):
        policy = self.report.profile.environments
        matrix = self._geom.Xformable(prim).ComputeLocalToWorldTransform(self._time)
        axes = [[float(matrix[row][col]) for col in range(3)] for row in range(3)]
        lengths = [math.sqrt(sum(v * v for v in axis)) for axis in axes]
        scale = lengths[0]
        valid = all(math.isfinite(float(matrix[r][c])) for r in range(4) for c in range(4))
        valid = valid and all(matrix[r][3] == 0 for r in range(3)) and matrix[3][3] == 1
        valid = valid and math.isfinite(scale) and scale > 1e-15
        valid = valid and all(math.isfinite(v) and abs(v - scale) <= 1e-6 * scale for v in lengths)
        if valid:
            unit = [[v / scale for v in axis] for axis in axes]
            valid = all(abs(sum(unit[i][k] * unit[j][k] for k in range(3))) <= 1e-6
                        for i, j in ((0, 1), (0, 2), (1, 2)))
        if not valid:
            self._add(prim, "HDRI transform", "Dome transforms require finite uniform scale without shear or singular axes.")
        self._textured_domes += 1
        if self._textured_domes > policy["max_textured_domes"]:
            self._add(prim, "HDRI count", "The scene exceeds the profile limit of eight textured domes.")
        format = str(self._value(prim, "inputs:texture:format", "automatic"))
        if format not in policy["formats"]:
            self._add(prim, "HDRI projection", "Only latitude-longitude environment maps are supported; got " + format)
        pole = str(self._value(prim, "poleAxis", "scene"))
        if pole not in {"scene", "Y", "Z"}:
            self._add(prim, "HDRI pole axis", "Unknown pole-axis value: " + pole)
        self._add(prim, "HDRI asset", "%s; availability, decoding, color processing and image dimensions are not checked here. "
                  "Automatic projection requires a 2:1 image. Consult native renderer diagnostics."
                  % getattr(value, "path", value), "Not assessed")
