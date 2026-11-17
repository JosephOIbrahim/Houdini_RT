"""Bounded USD material inspection for the installed opaque OpenPBR profile."""
from dataclasses import dataclass
import math
import struct


MODEL = "openpbr-opaque-constants-v1"


def validate_policy(policy):
    if not policy:
        return {}
    if not isinstance(policy, dict) or policy.get("model") != MODEL:
        raise ValueError("Unsupported installed material capability policy.")
    if policy.get("surface_shader") != "ND_open_pbr_surface_surfaceshader":
        raise ValueError("Invalid material surface shader policy.")
    if policy.get("render_contexts") != ["mtlx", ""]:
        raise ValueError("Invalid material render-context preference.")
    parameters = policy.get("parameters", {})
    expected = {"base_color", "base_weight", "base_metalness", "specular_color",
                "specular_weight", "specular_roughness", "specular_ior"}
    if not isinstance(parameters, dict) or set(parameters) != expected:
        raise ValueError("Incomplete opaque OpenPBR parameter policy.")
    for name, spec in parameters.items():
        if not isinstance(spec, dict) or spec.get("type") not in {"float", "color3"}:
            raise ValueError("Invalid material parameter policy: " + name)
        if not all(isinstance(spec.get(k), (int, float)) and math.isfinite(spec[k]) for k in ("min", "max")):
            raise ValueError("Invalid material parameter range: " + name)
        if spec["min"] > spec["max"] or "default" not in spec:
            raise ValueError("Invalid material parameter default/range: " + name)
    if not isinstance(policy.get("required_defaults"), dict):
        raise ValueError("Missing unsupported material defaults.")
    if not isinstance(policy.get("unsupported_explicit_inputs"), list):
        raise ValueError("Missing explicit geometry-input restrictions.")
    if policy.get("neutral_displacement") != {"shader": "ND_displacement_float", "displacement": 0, "scale_default": 1}:
        raise ValueError("Invalid neutral displacement policy.")
    if policy.get("connections") != "unsupported" or policy.get("material_subsets") != "unsupported":
        raise ValueError("Unsupported material graph or subset policy.")
    return policy


def scalar(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError("expected a numeric scalar")
    try:
        value = struct.unpack("f", struct.pack("f", value))[0]
    except (OverflowError, struct.error):
        raise ValueError("value exceeds the native float range") from None
    if not math.isfinite(value):
        raise ValueError("value is not finite")
    return value


def numeric(value, vector=False):
    if not vector:
        return (scalar(value),)
    # UsdShade color values must match native GfVec3f/GfVec3d, not arbitrary arrays.
    if type(value).__name__ not in {"Vec3f", "Vec3d"}:
        raise ValueError("expected a three-component color")
    return tuple(scalar(component) for component in value)


@dataclass(frozen=True)
class MaterialIssue:
    path: str
    reason: str
    kind: str = "Unsupported"


class MaterialInspector:
    def __init__(self, shade, policy, time_code):
        self.shade, self.policy, self.time = shade, policy, time_code
        self.cache = {}

    def _issue(self, attr, reason, kind="Unsupported"):
        return MaterialIssue(str(attr.GetPath()), reason, kind)

    def _connection(self, item, displacement=False):
        attr = item.GetAttr()
        source = item.GetConnectedSource()
        if len(attr.GetConnections()) == 1 and source and source[2] == self.shade.AttributeType.Input:
            return self._issue(attr, "Connected interface inputs may be flattened to constants by Hydra; "
                               "the effective value and fallback were not assessed.", "Not assessed")
        label = "displacement" if displacement else "shader inputs, including textures"
        return self._issue(attr, "Connected " + label + " are unsupported.")

    def _source(self, material, terminal):
        function = getattr(material, "Compute" + terminal + "Source")
        # Houdini 22's Python binding exposes the single-context overload only.
        for context in self.policy["render_contexts"]:
            source, port, source_type = function(context)
            if source:
                return source, port, source_type
        return None, "", None

    def inspect(self, material):
        path = str(material.GetPath())
        if path not in self.cache:
            self.cache[path] = tuple(self._inspect(material))
        return self.cache[path]

    def _output(self, material, terminal):
        for context in self.policy["render_contexts"]:
            output = getattr(material, "Get" + terminal + "Output")(context)
            if output and output.GetAttr().GetConnections():
                return output.GetAttr()
        return None

    def _inspect(self, material):
        for terminal in ("Surface", "Displacement", "Volume"):
            output = self._output(material, terminal)
            if output and len(output.GetConnections()) != 1:
                yield self._issue(output, "Multiple terminal connections are unsupported.")
            if terminal == "Volume" and output:
                yield self._issue(output, "Volume shading is unsupported.")
        surface, port, source_type = self._source(material, "Surface")
        if not surface:
            yield self._issue(material.GetPrim(), "No resolvable MaterialX/OpenPBR surface terminal.")
            return
        if port != "out" or source_type != self.shade.AttributeType.Output:
            yield self._issue(material.GetPrim(), "Surface terminal must connect to the shader's out output.")
        if not surface.GetPrim().IsActive() or not surface.GetPrim().IsLoaded():
            yield self._issue(surface.GetPrim(), "Surface shader is inactive or unloaded.")
            return
        identifier = surface.GetIdAttr().Get()
        if identifier != self.policy["surface_shader"]:
            yield self._issue(surface.GetPrim(), "Unsupported surface shader: " + str(identifier))
            return
        inputs = surface.GetInputs()
        if len(inputs) > 128:
            yield self._issue(surface.GetPrim(), "Material input limit exceeded; this graph was not fully assessed.")
            return
        for item in inputs:
            attr, name = item.GetAttr(), item.GetBaseName()
            if attr.GetColorSpace():
                yield self._issue(attr, "Explicit colorSpace tags are unsupported; use untagged scene-linear constants.")
            if attr.GetConnections():
                yield self._connection(item)
                continue
            value = attr.Get(self.time)
            if value is None:
                continue  # The native node definition supplies this input's default.
            if name in self.policy["unsupported_explicit_inputs"]:
                yield self._issue(attr, "Explicit shader normals and tangents are unsupported.")
                continue
            spec = self.policy["parameters"].get(name)
            try:
                if spec:
                    values = numeric(value, spec["type"] == "color3")
                    if any(component < spec["min"] or component > spec["max"] for component in values):
                        raise ValueError("supported range is %g to %g" % (spec["min"], spec["max"]))
                elif name in self.policy["required_defaults"]:
                    default = self.policy["required_defaults"][name]
                    if isinstance(default, bool):
                        equal = type(value) is bool and value == default
                    else:
                        vector = isinstance(default, list)
                        expected = tuple(scalar(v) for v in default) if vector else (scalar(default),)
                        equal = numeric(value, vector) == expected
                    if not equal:
                        raise ValueError("this unsupported input must remain at its node-definition default %s" % default)
                else:
                    raise ValueError("unknown OpenPBR input")
            except ValueError as exc:
                yield self._issue(attr, str(exc))
        displacement_output = self._output(material, "Displacement")
        if displacement_output:
            displacement, port, source_type = self._source(material, "Displacement")
            if not displacement or displacement.GetIdAttr().Get() != "ND_displacement_float":
                yield self._issue(displacement_output, "Only a known neutral scalar-displacement terminal is allowed.")
            else:
                if port != "out" or source_type != self.shade.AttributeType.Output:
                    yield self._issue(displacement_output, "Displacement terminal must connect to the shader's out output.")
                if not displacement.GetPrim().IsActive() or not displacement.GetPrim().IsLoaded():
                    yield self._issue(displacement.GetPrim(), "Displacement shader is inactive or unloaded.")
                    return
                inputs = displacement.GetInputs()
                if len(inputs) > 128:
                    yield self._issue(displacement.GetPrim(), "Displacement input limit exceeded; this graph was not fully assessed.")
                    return
                for item in inputs:
                    attr, name = item.GetAttr(), item.GetBaseName()
                    if attr.GetColorSpace():
                        yield self._issue(attr, "Explicit colorSpace tags are unsupported.")
                    if attr.GetConnections():
                        yield self._connection(item, displacement=True)
                        continue
                    value = attr.Get(self.time)
                    if value is None:
                        continue
                    try:
                        number = scalar(value)
                        if name not in {"displacement", "scale"}:
                            raise ValueError("unknown displacement input")
                        if name == "displacement" and number != 0:
                            raise ValueError("nonzero displacement is unsupported")
                    except ValueError as exc:
                        yield self._issue(attr, str(exc))
