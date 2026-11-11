"""Houdini_RT logo builder. Run: python build_logo.py  (writes the SVGs next to this file)

Direction A (typographic), chosen by a design review on 2026-11-11 and refined:
vertical optical overshoot on the rounds, a wider R-T gap, one 48-unit ray everywhere, and a
self-grounded square mark (ink tile) that works on any background without media queries.
The spurs where a bowl meets its stem (u, n, d) are kept on purpose: the geometric-sans
convention that keeps u and n from reading as symmetric arches.

One glyph table drives every file, so light, dark and mark cannot drift apart.

Construction
  Screen coordinates (y down). Cap line y=0, x-height line y=16, baseline y=56.
  Grid unit 4. Stem 8 (two units). Every glyph is a filled outline built from
  rectangles, semicircular bands (pairs of quarter circles) and one diagonal
  (the R leg). All ink of a file lives in ONE <path> with nonzero winding:
  solids run clockwise, counters run counter-clockwise, so overlaps never seam.
  The underscore is the only accent element: the ray, 48 x 8 on the baseline.

Filled outlines (not strokes) on purpose: one ink path per file, no seams
between parts, and no dependence on how a renderer strokes arcs or caps.

Files
  logo-light.svg / logo-dark.svg  fixed colours, for a README <picture> pair
  mark.svg                        square ink tile, paper H, vermilion ray: one file for
                                  every background (avatar, favicon, social card)
"""
import math, os, sys

OUT = os.path.dirname(os.path.abspath(__file__))

INK_L, ACC_L = "#2E2B28", "#C8321A"   # on light
INK_D, ACC_D = "#DAD6CF", "#E25B3F"   # on dark

S = 8          # stem
BASE = 56      # baseline
XTOP = 16      # x-height line (x-height = 40)
CAP = 0        # cap line (cap height = 56)


OS = 0.75      # vertical optical overshoot of every round


def f(v):
    v = round(v, 2)
    return str(int(v)) if v == int(v) else ("%.2f" % v).rstrip("0").rstrip(".")


# ---- primitives: every call returns path data in absolute coords, offset by dx
def rect(x0, y0, x1, y1, dx=0):
    """Clockwise rectangle."""
    return f"M{f(x0+dx)} {f(y0)}H{f(x1+dx)}V{f(y1)}H{f(x0+dx)}Z"


def ring(cx, cy, R, r, dx=0):
    """Clockwise outer ellipse + counter-clockwise inner ellipse (vertical overshoot OS)."""
    cx += dx
    Ry, ry = R + OS, r + OS
    return (f"M{f(cx-R)} {f(cy)}A{f(R)} {f(Ry)} 0 0 1 {f(cx+R)} {f(cy)}A{f(R)} {f(Ry)} 0 0 1 {f(cx-R)} {f(cy)}Z"
            f"M{f(cx-r)} {f(cy)}A{f(r)} {f(ry)} 0 0 0 {f(cx+r)} {f(cy)}A{f(r)} {f(ry)} 0 0 0 {f(cx-r)} {f(cy)}Z")


def band_lower(cx, cy, R, r, dx=0):
    """Lower semicircular band, clockwise."""
    cx += dx
    return (f"M{f(cx+R)} {f(cy)}A{f(R)} {f(R+OS)} 0 0 1 {f(cx-R)} {f(cy)}"
            f"H{f(cx-r)}A{f(r)} {f(r+OS)} 0 0 0 {f(cx+r)} {f(cy)}Z")


def band_upper(cx, cy, R, r, dx=0):
    """Upper semicircular band, clockwise."""
    cx += dx
    return (f"M{f(cx-R)} {f(cy)}A{f(R)} {f(R+OS)} 0 0 1 {f(cx+R)} {f(cy)}"
            f"H{f(cx+r)}A{f(r)} {f(r+OS)} 0 0 0 {f(cx-r)} {f(cy)}Z")


# ---- glyph table: (advance width, builder(dx) -> path data)
R_O, R_I = 20, 12           # bowl radii: outer = half x-height, inner = outer - stem
# Optical overshoot is vertical only: the arcs are ellipses whose vertical radius is OS larger, so
# rounds pass the x-height and baseline by OS (and look as tall as the stems) while their sides stay
# exactly on the stems. Inner and outer grow alike, so the strokes at top and bottom stay 8 thick.
CY = BASE - 20              # bowl centre y


def g_H(dx):  return rect(0, 0, 8, 56, dx) + rect(32, 0, 40, 56, dx) + rect(8, 24, 32, 32, dx)
def g_o(dx):  return ring(20, CY, R_O, R_I, dx)
def g_d(dx):  return ring(20, CY, R_O, R_I, dx) + rect(32, 0, 40, 56, dx)
def g_u(dx):  return rect(0, XTOP, 8, CY, dx) + band_lower(20, CY, R_O, R_I, dx) + rect(32, XTOP, 40, 56, dx)
def g_n(dx):  return rect(0, XTOP, 8, 56, dx) + band_upper(20, CY, R_O, R_I, dx) + rect(32, CY, 40, 56, dx)
def g_i(dx):  return rect(0, XTOP, 8, 56, dx) + rect(0, 0, 8, 8, dx)
def g_T(dx):  return rect(0, 0, 40, 8, dx) + rect(16, 0, 24, 56, dx)


def g_R(dx):
    # Bowl: a "D" on the cap line whose lower edge is the H crossbar (y 24..32).
    # Outer radius 16, counter radius 8, so the bowl's right edge lands on x = 40.
    bowl = (f"M{f(dx)} 0H{f(24+dx)}A16 16 0 0 1 {f(24+dx)} 32H{f(dx)}Z"
            f"M{f(8+dx)} 8V24H{f(24+dx)}A8 8 0 0 0 {f(24+dx)} 8Z")
    # Leg: parallelogram, horizontal cuts at y=28 (inside the bar) and the baseline,
    # perpendicular thickness = stem. Right foot lands on x = 40.
    t, w = 14, S
    for _ in range(40):
        w = S * math.hypot((40 - w) - t, BASE - 28) / (BASE - 28)
    leg = f"M{f(t+dx)} 28H{f(t+w+dx)}L{f(40+dx)} 56H{f(40-w+dx)}Z"
    return rect(0, 0, 8, 56, dx) + bowl + leg


GLYPHS = {"H": (40, g_H), "o": (40, g_o), "u": (40, g_u), "d": (40, g_d),
          "i": (8, g_i), "n": (40, g_n), "R": (40, g_R), "T": (40, g_T)}

# Gaps between glyph boxes. Straight-to-straight 11, round-to-straight 8.
KERN = {"Ho": 8, "ou": 8, "ud": 8, "di": 11, "in": 11, "ni": 11, "RT": 5}   # RT opened: the bowl and crossbar merged at 24 px
RAY_LEN, RAY_GAP = 48, 10


def wordmark():
    ink, x, prev = [], 0, None
    for ch in "Houdini":
        if prev:
            x += KERN[prev + ch]
        w, fn = GLYPHS[ch]
        ink.append(fn(x)); x += w; prev = ch
    x += RAY_GAP
    ray = rect(x, BASE - S, x + RAY_LEN, BASE)
    x += RAY_LEN + RAY_GAP
    prev = None
    for ch in "RT":
        if prev:
            x += KERN[prev + ch]
        w, fn = GLYPHS[ch]
        ink.append(fn(x)); x += w; prev = ch
    return x, "".join(ink), ray


def lockup_svg(ink_c, acc_c):
    W, ink, ray = wordmark()
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{f(W)}" height="58" viewBox="0 -1 {f(W)} 58" '
            f'role="img" aria-label="Houdini_RT"><title>Houdini_RT</title>'
            f'<path fill="{ink_c}" d="{ink}"/><path fill="{acc_c}" d="{ray}"/></svg>')


MARK_RAY = RAY_LEN   # one ray length everywhere
MARK_GAP = 8
TILE_INK, TILE_PAPER, TILE_ACC = "#2E2B28", "#FAF8F4", "#E25B3F"


def mark_svg(side=120):
    """'H_' on its own ground: a square ink tile, the H in paper, the ray in vermilion.
    Content 96 x 56, centred, so it reads at 32 px and survives a circular avatar crop."""
    cw = 40 + MARK_GAP + MARK_RAY
    x0, y0 = (side - cw) / 2, (side - 56) / 2
    ray = rect(40 + MARK_GAP, BASE - S, 40 + MARK_GAP + MARK_RAY, BASE)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{side}" height="{side}" viewBox="0 0 {side} {side}" '
            f'role="img" aria-label="Houdini_RT"><title>Houdini_RT</title>'
            f'<rect width="{side}" height="{side}" fill="{TILE_INK}"/>'
            f'<g transform="translate({f(x0)} {f(y0)})"><path fill="{TILE_PAPER}" d="{g_H(0)}"/><path fill="{TILE_ACC}" d="{ray}"/></g></svg>')


def write(name, text):
    with open(os.path.join(OUT, name), "w", newline="\n") as fh:
        fh.write(text)
    print(f"{name:18s} {len(text.encode()):5d} bytes")


if __name__ == "__main__":
    write("logo-light.svg", lockup_svg(INK_L, ACC_L))
    write("logo-dark.svg", lockup_svg(INK_D, ACC_D))
    write("mark.svg", mark_svg())
    cw = 40 + MARK_GAP + MARK_RAY
    print("mark content corner radius", round(math.hypot(cw / 2, 28), 1), "of", 60, "(inside a circular crop)")
