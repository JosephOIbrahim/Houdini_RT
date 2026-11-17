"""Independent double-precision diffuse irradiance, never imports renderer code.

References: installed UsdLux lightAPI.h (normalization); the rendering equation
and PBRT4 Light Sources/Area Lights. Gauss quadrature integrates physical area,
NOT the candidate's light sampler or PDF. Array rows run bottom to top.
"""
from __future__ import annotations
import math
import numpy as np


def unit(value):
    value = np.asarray(value, dtype=np.float64)
    return value / np.linalg.norm(value, axis=-1, keepdims=True)


def light_area(light):
    if light["type"] == "rect":
        return 4 * np.linalg.norm(np.cross(light["u"], light["v"]))
    if light["type"] == "disk":
        return math.pi * np.linalg.norm(np.cross(light["u"], light["v"]))
    if light["type"] == "sphere":
        return 4 * math.pi * light["radius"] ** 2
    return 0.0


def radiance(light):
    value = np.asarray(light["color"], dtype=np.float64) * light.get("intensity", 1) * 2 ** light.get("exposure", 0)
    if light.get("normalize"):
        if light["type"] == "distant":
            alpha = math.radians(light["angle"]) / 2
            factor = math.pi * math.sin(alpha) ** 2 if alpha else 1.0
        else:
            factor = light_area(light)
        value /= factor
    return value


def quadrature(light, order):
    """Return world positions, emission normals, and physical dA weights."""
    x, w = np.polynomial.legendre.leggauss(order)
    kind = light["type"]
    center = np.asarray(light["position"], dtype=np.float64)
    if kind == "rect":
        u, v = np.asarray(light["u"]), np.asarray(light["v"])
        p = center + x[:, None, None] * u + x[None, :, None] * v
        weights = np.outer(w, w) * np.linalg.norm(np.cross(u, v))
        normals = np.broadcast_to(unit(light["normal"]), p.shape)
    elif kind == "disk":
        # r=sqrt(t), dA=|u cross v|/2 dt dphi. Integrate t in [0,1].
        t = (x + 1) / 2
        phi = (np.arange(order * 4) + 0.5) * (2 * math.pi / (order * 4))
        u, v = np.asarray(light["u"]), np.asarray(light["v"])
        radial = np.cos(phi)[:, None] * u + np.sin(phi)[:, None] * v
        p = center + np.sqrt(t)[:, None, None] * radial[None, :, :]
        weights = np.broadcast_to((w / 2)[:, None], p.shape[:2]) * (math.pi / (order * 4)) * np.linalg.norm(np.cross(u, v))
        normals = np.broadcast_to(unit(light["normal"]), p.shape)
    elif kind == "sphere":
        phi = (np.arange(order * 4) + 0.5) * (2 * math.pi / (order * 4))
        zz = np.broadcast_to(x[:, None], (order, len(phi)))
        rr = np.sqrt(1 - zz * zz)
        normals = np.stack((rr * np.cos(phi), rr * np.sin(phi), zz), axis=-1)
        p = center + light["radius"] * normals
        weights = np.broadcast_to(w[:, None], zz.shape) * (2 * math.pi / len(phi)) * light["radius"] ** 2
    else:
        raise ValueError(kind)
    return p.reshape(-1, 3), normals.reshape(-1, 3), weights.reshape(-1)


def visible(points, direction, distance, blocker):
    if not blocker:
        return np.ones(direction.shape[:-1], dtype=bool)
    # Independent ray/plane intersection, finite rectangle in plane x=blocker.x.
    dx = direction[..., 0]
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (blocker["x"] - points[..., 0]) / dx
        yy = points[..., 1] + t * direction[..., 1]
        zz = points[..., 2] + t * direction[..., 2]
    hit = (t > 1e-7) & (t < distance - 1e-7) & (abs(yy) < blocker["half_y"]) & (zz > blocker["z0"]) & (zz < blocker["z1"])
    return ~hit


def emitters_visible(points, direction, distance, emitters, excluded=None):
    """Visibility against opaque finite emitters, independent analytic intersections.

    The target endpoint is excluded, but every other emitter (including black
    ones and the back of a planar light) can block transport. This computes only
    physical visibility; it has no knowledge of sampling probabilities or MIS.
    """
    result = np.ones(direction.shape[:-1], dtype=bool)
    for item in emitters:
        if item is excluded or item["type"] == "distant":
            continue
        delta = points - np.asarray(item["position"], dtype=np.float64)
        if item["type"] == "sphere":
            b = np.sum(delta * direction, axis=-1)
            c = np.sum(delta * delta, axis=-1) - item["radius"]**2
            disc = b*b-c
            root = np.sqrt(np.maximum(0, disc))
            front, back = -b-root, -b+root
            t = np.where(front > 1e-7, front, back)
            hits = disc >= 0
        else:
            normal_light = unit(item["normal"])
            denominator = direction @ normal_light
            with np.errstate(divide="ignore", invalid="ignore"):
                t = -(delta @ normal_light) / denominator
                intersection = delta + t[..., None] * direction
            # The independent geometry is a linear basis, not candidate GPU UVs.
            basis = np.stack((item["u"], item["v"]), axis=1)
            inverse_basis = np.linalg.pinv(basis)
            ab = intersection @ inverse_basis.T
            if item["type"] == "rect": hits = np.max(np.abs(ab), axis=-1) <= 1
            else: hits = np.sum(ab*ab, axis=-1) <= 1
            hits &= abs(denominator) > 1e-12
        result &= ~(hits & (t > 1e-7) & (t < distance - 1e-7))
    return result


def irradiance(points, normal, light, order=64, blocker=None, emitters=(), projection_only=False):
    points = np.asarray(points, dtype=np.float64)
    normal = unit(normal)
    le = np.ones(3) if projection_only else radiance(light)
    if not np.any(le):
        return np.zeros((len(points), 3))
    if light["type"] == "distant":
        wi = -unit(light["normal"])
        alpha = math.radians(light["angle"]) / 2
        if alpha and blocker:
            raise ValueError("Finite cone + blocker requires independent cone quadrature; fixture not supported")
        cosine = float(normal @ wi)
        if alpha and cosine < math.sin(alpha):
            raise ValueError("Fixture cone crosses receiver horizon; closed form not applicable")
        factor = math.pi * math.sin(alpha) ** 2 if alpha else 1.0
        vis = visible(points, np.broadcast_to(wi, points.shape), np.full(len(points), np.inf), blocker)
        vis &= emitters_visible(points, np.broadcast_to(wi, points.shape), np.full(len(points), np.inf), emitters)
        return vis[:, None] * max(0, cosine) * factor * le
    other_finite = any(item is not light and item["type"] != "distant" for item in emitters)
    if light["type"] == "sphere" and blocker is None and not other_finite:
        delta = np.asarray(light["position"]) - points
        d = np.linalg.norm(delta, axis=1)
        cosine = unit(delta) @ normal
        if np.any(cosine < light["radius"] / d):
            raise ValueError("Fixture sphere crosses receiver horizon; closed form not applicable")
        return (math.pi * light["radius"] ** 2 * cosine / d ** 2)[:, None] * le
    q, nl, weights = quadrature(light, order)
    result = np.zeros((len(points), 3))
    for start in range(0, len(points), 48):
        p = points[start:start + 48, None, :]
        delta = q[None, :, :] - p
        dist2 = np.sum(delta * delta, axis=-1)
        dist = np.sqrt(dist2)
        direction = delta / dist[..., None]
        receive = np.maximum(0, direction @ normal)
        emit = -np.sum(direction * nl, axis=-1)
        emit = np.abs(emit) if projection_only and light["type"] != "sphere" else np.maximum(0, emit)
        vis = visible(p, direction, dist, blocker)
        vis &= emitters_visible(p, direction, dist, emitters, excluded=light)
        integral = np.sum(receive * emit / dist2 * weights * vis, axis=1)
        result[start:start + len(p)] = integral[:, None] * le
    return result


def pixel_positions(case, pixel_order=3):
    w, h = case["width"], case["height"]
    extent = case["extent"]
    q, weights = np.polynomial.legendre.leggauss(pixel_order)
    sub = np.stack(np.meshgrid(q, q, indexing="xy"), axis=-1).reshape(-1, 2) / 2
    subw = np.outer(weights, weights).reshape(-1) / 4
    xy = np.stack(np.meshgrid(np.arange(w) + .5, np.arange(h) + .5, indexing="xy"), axis=-1).reshape(-1, 1, 2)
    xy = (xy + sub[None, :, :]) / [w, h] * (2 * extent) - extent
    p = np.concatenate((xy, np.zeros(xy.shape[:-1] + (1,))), axis=-1)
    return p.reshape(-1, 3), subw


def expected(case, order=64, pixel_order=3):
    p, subw = pixel_positions(case, pixel_order)
    result = np.zeros((len(p), 3))
    for light in case["lights"]:
        result += irradiance(p, case["normal"], light, order, case.get("blocker"), case["lights"]) / math.pi
    if np.any(case["dome"]):
        if case.get("blocker") or case["normal"] != [0, 0, 1]:
            raise ValueError("Dome control requires unobstructed flat receiver")
        # The dome is hidden behind the UNION of opaque emitter silhouettes.
        # Visible emitting-surface area subtends cos(theta)/r² dA; nearest-shape
        # visibility makes those projected integrals disjoint, even if lights
        # overlap. Subtract them from the full hemisphere integral pi.
        hidden = np.zeros(len(p))
        for light in case["lights"]:
            if light["type"] != "distant":
                hidden += irradiance(p, case["normal"], light, order, None, case["lights"], projection_only=True)[:, 0]
        if np.min(hidden) < -1e-8 or np.max(hidden) > math.pi + 1e-6:
            raise ValueError("Invalid emitter-union projected solid angle")
        result += (1 - hidden[:, None] / math.pi) * np.asarray(case["dome"])
    result *= case["albedo"]
    result = result.reshape(case["height"], case["width"], len(subw), 3)
    rgb = np.sum(result * subw[None, None, :, None], axis=2)
    return np.concatenate((rgb, np.ones(rgb.shape[:2] + (1,))), axis=-1)


def self_check():
    center = np.zeros((1, 3))
    common = {"position": [0, 0, 3], "normal": [0, 0, -1], "color": [1, 1, 1]}
    disk = dict(common, type="disk", u=[.7, 0, 0], v=[0, .7, 0])
    expected_disk = math.pi * .7 ** 2 / (3 ** 2 + .7 ** 2)
    actual_disk = irradiance(center, [0, 0, 1], disk, 64)[0, 0]
    rect = dict(common, type="rect", u=[.7, 0, 0], v=[0, .4, 0])
    a, b, h = .7, .4, 3
    expected_rect = 2 * (a / math.sqrt(a*a+h*h) * math.atan(b / math.sqrt(a*a+h*h)) + b / math.sqrt(b*b+h*h) * math.atan(a / math.sqrt(b*b+h*h)))
    actual_rect = irradiance(center, [0, 0, 1], rect, 64)[0, 0]
    sphere = dict(common, type="sphere", radius=.7)
    q, n, weights = quadrature(sphere, 256)
    direction = unit(q)
    sphere_q = np.sum(np.maximum(0, direction[:, 2]) * np.maximum(0, -np.sum(direction*n, axis=1)) / np.sum(q*q, axis=1) * weights)
    sphere_exact = math.pi * .7 ** 2 / 9
    errors = {"disk_relative": abs(actual_disk / expected_disk - 1), "rect_relative": abs(actual_rect / expected_rect - 1), "sphere_quadrature_relative": abs(sphere_q / sphere_exact - 1)}
    assert errors["disk_relative"] < 1e-12 and errors["rect_relative"] < 1e-12
    assert errors["sphere_quadrature_relative"] < 1e-4
    return errors
