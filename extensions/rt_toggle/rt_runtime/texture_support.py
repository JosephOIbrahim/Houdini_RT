"""Bounded graph inspection only: never resolve, stat, open or decode image files."""
import re
from .material_support import MaterialIssue, numeric, scalar


IMAGE_IDS = {"float": "ND_image_float", "color3": "ND_image_color3"}
UV_IDS = {"ND_texcoord_vector2", "ND_geompropvalue_vector2", "ND_UsdTransform2d", "ND_place2d_vector2"}


class UnassessedGraph(ValueError):
    pass


def authored_asset(attr, time_code):
    """Read raw Sdf defaults, avoiding UsdAttribute.Get's implicit asset resolve."""
    if not attr:
        return None, ""
    if attr.GetNumTimeSamples():
        return None, "Time-sampled asset filenames are not resolved by stage inspection."
    stack = attr.GetPropertyStack(time_code)
    if len(stack) > 16:
        return None, "Asset opinion stack exceeds the inspection limit."
    for spec in stack:
        if spec.HasDefaultValue():
            return spec.default, ""
    return None, ""


def validate_texture_policy(policy):
    if not isinstance(policy, dict):
        raise ValueError("Missing texture graph policy.")
    if policy.get("image_shaders") != IMAGE_IDS or set(policy.get("uv_shaders", ())) != UV_IDS:
        raise ValueError("Unknown texture or UV shader policy.")
    if policy.get("primvar") != "st" or policy.get("texcoord_index") != 0:
        raise ValueError("Unsupported texture coordinate policy.")
    if set(policy.get("address_modes", ())) != {"constant", "clamp", "periodic", "mirror"}:
        raise ValueError("Invalid image addressing policy.")
    if set(policy.get("filters", ())) != {"closest", "linear"}:
        raise ValueError("Invalid image filtering policy.")
    if policy.get("max_graph_nodes") != 16 or policy.get("max_graph_depth") != 8:
        raise ValueError("Unsupported graph inspection limits.")
    return policy


class TextureInspector:
    def __init__(self, shade, time_code, policy):
        self.shade, self.time, self.policy = shade, time_code, policy
        self.visited = set()
        self.uses_st = False

    @staticmethod
    def issue(attr, reason, kind="Unsupported"):
        return MaterialIssue(str(attr.GetPath()), reason, kind)

    def source(self, item):
        connections = item.GetAttr().GetConnections()
        source = item.GetConnectedSource()
        if len(connections) != 1 or not source:
            raise ValueError("Expected one resolvable shader connection.")
        node, port, kind = source
        if kind == self.shade.AttributeType.Input:
            raise UnassessedGraph("Interface connections may be flattened by Hydra; effective texture controls were not assessed.")
        if port != "out" or kind != self.shade.AttributeType.Output:
            raise ValueError("Texture graph connections must use the shader's out output.")
        shader = self.shade.Shader(node.GetPrim())
        if not shader or not shader.GetPrim().IsActive() or not shader.GetPrim().IsLoaded():
            raise ValueError("Connected shader is invalid, inactive or unloaded.")
        path = str(shader.GetPath())
        self.visited.add(path)
        if len(self.visited) > self.policy["max_graph_nodes"]:
            raise ValueError("Texture graph inspection limit exceeded; remaining graph is unassessed.")
        return shader

    def inputs(self, shader, names):
        inputs = shader.GetInputs()
        if len(inputs) > 16:
            raise ValueError("Texture node input limit exceeded; this node is unassessed.")
        result = {item.GetBaseName(): item for item in inputs}
        unknown = set(result) - set(names)
        if unknown:
            raise ValueError("Unsupported texture node inputs: " + ", ".join(sorted(unknown)))
        return result

    def value(self, items, name, default):
        item = items.get(name)
        if item is None:
            return default
        if item.GetAttr().GetConnections():
            self.connected_control(item, name)
        value = item.Get(self.time)
        return default if value is None else value

    def connected_control(self, item, name):
        source = item.GetConnectedSource()
        if len(item.GetAttr().GetConnections()) == 1 and source and source[2] == self.shade.AttributeType.Input:
            raise UnassessedGraph("Interface input %s may be flattened by Hydra; its effective value was not assessed." % name)
        raise ValueError("Connected texture control is unsupported: " + name)

    def inspect(self, item, parameter):
        self.visited.clear()  # Native bounds apply independently to each image chain.
        expected = parameter["type"]
        try:
            shader = self.source(item)
            identifier = shader.GetIdAttr().Get()
            if identifier != IMAGE_IDS[expected]:
                raise ValueError("Expected %s; connected shader is %s." % (IMAGE_IDS[expected], identifier))
            yield from self.image(shader, parameter)
        except UnassessedGraph as error:
            yield self.issue(item.GetAttr(), str(error), "Not assessed")
        except (ValueError, TypeError, OverflowError) as error:
            yield self.issue(item.GetAttr(), str(error))

    def image(self, shader, parameter):
        expected = parameter["type"]
        items = self.inputs(shader, ("file", "layer", "default", "texcoord", "uaddressmode", "vaddressmode",
                                      "filtertype", "framerange", "frameoffset", "frameendaction"))
        for name, item in items.items():
            if name != "file" and item.GetAttr().GetColorSpace():
                raise ValueError("Only the image filename may carry colorSpace metadata.")
        for name, default in (("layer", ""), ("framerange", ""), ("frameoffset", 0), ("frameendaction", "constant")):
            if self.value(items, name, default) != default:
                raise ValueError("Image %s must remain %s; layers and image sequences are unsupported." % (name, repr(default)))
        if type(self.value(items, "frameoffset", 0)) is not int:
            raise ValueError("Image frameoffset must be integer zero.")
        for name in ("uaddressmode", "vaddressmode"):
            if self.value(items, name, "periodic") not in self.policy["address_modes"]:
                raise ValueError("Unsupported image address mode: " + name)
        if self.value(items, "filtertype", "linear") not in self.policy["filters"]:
            raise ValueError("Only closest and linear image filtering are supported; cubic is unsupported.")
        fallback = (0.0,) * (3 if expected == "color3" else 1)
        if "default" in items:
            value = self.value(items, "default", None)
            if value is not None:
                fallback = numeric(value, expected == "color3")
        constant_border = any(self.value(items, name, "periodic") == "constant"
                              for name in ("uaddressmode", "vaddressmode"))
        if constant_border and any(v < parameter["min"] or v > parameter["max"] for v in fallback):
            raise ValueError("Constant image borders must fit the target input range %g to %g."
                             % (parameter["min"], parameter["max"]))
        file = items.get("file")
        if file and file.GetAttr().GetConnections():
            self.connected_control(file, "file")
        value, deferred = authored_asset(file.GetAttr() if file else None, self.time)
        path = getattr(value, "path", None)
        if not deferred and (not isinstance(path, str) or not path):
            raise ValueError("Image filename must be a nonempty USD asset path.")
        if path and re.search(r"<(?:UDIM|UVTILE)>|\$F\d*|%0?\d*d", path, re.IGNORECASE):
            raise ValueError("UDIMs and image-sequence filename patterns are unsupported.")
        coordinate = items.get("texcoord")
        if coordinate and coordinate.GetAttr().GetConnections():
            self.uv(coordinate, set(), 2)  # The image itself is native graph depth 1.
        elif coordinate and coordinate.Get(self.time) is not None:
            self.vector2(coordinate.Get(self.time))
        else:
            self.uses_st = True
        yield self.issue(file.GetAttr(), (deferred + " " if deferred else "") +
                         "Image asset %s; file availability, decoding, color processing and texel ranges "
                         "are not checked by stage inspection. Consult native renderer diagnostics." % path, "Not assessed")

    @staticmethod
    def vector2(value):
        if type(value).__name__ not in {"Vec2f", "Vec2d"}:
            raise ValueError("Texture coordinates and affine controls must be finite float2 values.")
        return tuple(scalar(component) for component in value)

    def uv(self, item, ancestors, depth):
        if depth > self.policy["max_graph_depth"]:
            raise ValueError("UV graph depth limit exceeded.")
        shader = self.source(item)
        path = str(shader.GetPath())
        if path in ancestors:
            raise ValueError("Texture coordinate graph contains a cycle.")
        ancestors = ancestors | {path}
        identifier = shader.GetIdAttr().Get()
        if identifier == "ND_texcoord_vector2":
            items = self.inputs(shader, ("index",))
            index = self.value(items, "index", 0)
            if type(index) is not int or index != 0:
                raise ValueError("Only texture coordinate index 0 (st) is supported.")
            self.uses_st = True
        elif identifier == "ND_geompropvalue_vector2":
            items = self.inputs(shader, ("geomprop", "default"))
            if self.value(items, "geomprop", "") != "st":
                raise ValueError("Only the st geometry property is supported for texture coordinates.")
            fallback = self.value(items, "default", None)
            if fallback is not None and self.vector2(fallback) != (0.0, 0.0):
                raise ValueError("The st geometry property's default must remain (0, 0).")
            self.uses_st = True
        elif identifier in {"ND_UsdTransform2d", "ND_place2d_vector2"}:
            usd_transform = identifier == "ND_UsdTransform2d"
            names = (("in", "rotation", "scale", "translation") if usd_transform else
                     ("texcoord", "pivot", "scale", "rotate", "offset", "operationorder"))
            items = self.inputs(shader, names)
            input_name = "in" if usd_transform else "texcoord"
            coordinate = items.get(input_name)
            if coordinate and coordinate.GetAttr().GetConnections():
                self.uv(coordinate, ancestors, depth + 1)
            elif coordinate and coordinate.Get(self.time) is not None:
                self.vector2(coordinate.Get(self.time))
            # An absent affine input is the nodedef constant (0,0), not UV0.
            for name in names:
                if name == input_name or name not in items:
                    continue
                value = self.value(items, name, None)
                if value is None:
                    continue  # No effective authored value: native nodedef default.
                if name in {"rotation", "rotate"}:
                    scalar(value)
                elif name == "operationorder":
                    if type(value) is not int or value not in (0, 1):
                        raise ValueError("Place2D operation order must be SRT (0) or TRS (1).")
                else:
                    vector = self.vector2(value)
                    if name == "scale" and not usd_transform and 0 in vector:
                        raise ValueError("Place2D scale cannot contain zero.")
        else:
            raise ValueError("Unsupported texture coordinate shader: " + str(identifier))
        for control in items.values():
            if control.GetAttr().GetColorSpace():
                raise ValueError("Color-space metadata on UV controls is unsupported.")
