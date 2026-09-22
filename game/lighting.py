import math


SHAPE_OMNI = 0
SHAPE_SPOT = 1
SHAPE_DIRECTIONAL = 2


FALLOFF_SHAPE = 0.6


FLASH_CONE_OUTER_COS = 0.6
FLASH_CONE_INNER_COS = 0.95
FLASH_HOT_OUTER_COS = 0.88
FLASH_HOT_INNER_COS = 0.99
FLASH_HOT_GAIN = 0.6
FLASH_OUTPUT_GAIN = 0.8
FLASH_TINT = (1.05, 0.93, 0.78)
FLASH_RANGE = 10.5
FLASH_FALLOFF_SHAPE = 1.0
FLASH_FILL_SHARE = 0.2
FLASH_FILL_CONE_OUTER_COS = 0.45
FLASH_FILL_CONE_INNER_COS = 0.85


_MOON_TOWARD_RAW = (0.4, 0.28, 0.62)
_ml = math.sqrt(sum(c * c for c in _MOON_TOWARD_RAW))
MOON_TOWARD = tuple(c / _ml for c in _MOON_TOWARD_RAW)
MOON_TINT = (0.55, 0.62, 0.78)


FLASH_SHADOW_NEAR = 0.02
FLASH_SHADOW_FAR = FLASH_RANGE
FLASH_SHADOW_FOV_DEGREES = 110.0
POINT_SHADOW_NEAR = 0.02
POINT_SHADOW_FAR = 4.5
POINT_SHADOW_FOV_DEGREES = 115.0
MOON_SHADOW_NEAR = 1.0
MOON_SHADOW_FAR = 60.0
MOON_SHADOW_ORIGIN_DIST = 30.0
MOON_SHADOW_HALF_EXTENT = 22.0
MOON_SHADOW_RECENTER_DIST = 9.0


FLASH_EMITTER_RADIUS = 0.02
LIGHTER_EMITTER_RADIUS = 0.012
PROP_EMITTER_RADIUS = {
    "lamp_desk": 0.05,
    "wall_sconce": 0.07,
    "lamppost": 0.10,
    "monitor": 0.10,
    "sign_exit": 0.08,
    "boiler_tank": 0.12,
}
PROP_EMITTER_RADIUS_DEFAULT = 0.06
MOON_ANGULAR_RADIUS = 0.0045

OMNI_FACE_DIRS = (
    (1.0, 0.0, 0.0), (-1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0), (0.0, -1.0, 0.0),
    (0.0, 0.0, 1.0), (0.0, 0.0, -1.0),
)


def shadow_bias_k_perspective(near, far):
    return far * near / (far - near)


def shadow_bias_k_ortho(near, far):
    return 1.0 / (far - near)


FLASH_SHADOW_BIAS_K = shadow_bias_k_perspective(FLASH_SHADOW_NEAR, FLASH_SHADOW_FAR)
POINT_SHADOW_BIAS_K = shadow_bias_k_perspective(POINT_SHADOW_NEAR, POINT_SHADOW_FAR)
MOON_SHADOW_BIAS_K = shadow_bias_k_ortho(MOON_SHADOW_NEAR, MOON_SHADOW_FAR)


LIGHT_SELECT_MARGIN = 2.0

MAX_OMNI_LIGHTS = 32
FIXED_LIGHTS = 3
MAX_LIGHTS = MAX_OMNI_LIGHTS + FIXED_LIGHTS
FIXED_TILES = 2
MAX_SHADOW_TILES = FIXED_TILES + 6 * MAX_OMNI_LIGHTS
TILE_MOON = 0
TILE_FLASH = 1


def omni_tile0(slot):
    return FIXED_TILES + slot * 6


class LightSource:
    __slots__ = ("key", "shape", "pos", "dir", "radius", "falloff", "color", "cone", "emitter",
                 "casts_shadow", "dynamic", "skip_bearer", "owner", "tile0", "bias_k",
                 "bearer_reveal", "counts_for_sanity", "at_viewpoint")

    def __init__(self, key, shape, pos, color, radius=0.0, direction=(0.0, 0.0, -1.0),
                 cone=(0.0, 0.0, 0.0, 0.0), emitter=PROP_EMITTER_RADIUS_DEFAULT, casts_shadow=True,
                 dynamic=False, skip_bearer=False, owner=None, bearer_reveal=0.0, counts_for_sanity=True,
                 at_viewpoint=False, falloff=FALLOFF_SHAPE):
        self.key = key
        self.shape = shape
        self.pos = pos
        self.dir = direction
        self.radius = radius
        self.falloff = falloff
        self.color = color
        self.cone = cone
        self.emitter = emitter
        self.casts_shadow = casts_shadow
        self.dynamic = dynamic
        self.skip_bearer = skip_bearer
        self.owner = owner
        self.tile0 = -1
        self.bias_k = 0.0
        self.bearer_reveal = bearer_reveal
        self.counts_for_sanity = counts_for_sanity
        self.at_viewpoint = at_viewpoint


def light_emit_pos(prop):
    at = getattr(prop, "light_at", None)
    if at is None:
        return (prop.x, prop.y, prop.z0 + prop.height * 0.6)
    c, s = math.cos(prop.facing), math.sin(prop.facing)
    return (prop.x + c * at[0] - s * at[1], prop.y + s * at[0] + c * at[1], prop.z0 + at[2])


def smoothstep(e0, e1, x):
    t = min(1.0, max(0.0, (x - e0) / (e1 - e0)))
    return t * t * (3.0 - 2.0 * t)


def light_lobe_factor(light, to_point):
    cone = light.cone
    if light.shape != SHAPE_OMNI or cone[3] < 0.5:
        return 1.0
    dx, dy, dz = light.dir
    c = to_point[0] * dx + to_point[1] * dy + to_point[2] * dz
    return cone[2] + (1.0 - cone[2]) * smoothstep(cone[0], cone[1], c)


def prop_light_lobe(prop):
    lobe = getattr(prop, "light_lobe", None)
    if lobe is None:
        return (0.0, 0.0, -1.0), (0.0, 0.0, 0.0, 0.0)
    lx, ly, lz = lobe[0], lobe[1], lobe[2]
    n = math.sqrt(lx * lx + ly * ly + lz * lz)
    c, s = math.cos(prop.facing), math.sin(prop.facing)
    direction = ((c * lx - s * ly) / n, (s * lx + c * ly) / n, lz / n)
    return direction, (lobe[3], lobe[4], lobe[5], 1.0)


def prop_flicker(prop, t):
    if getattr(prop, "flicker", False):
        return 0.8 + 0.2 * math.sin(t * 10.0 + prop.bob_phase * 5.0)
    return 1.0


EMISSIVE_GLOW = 0.85


def prop_emission(prop, t):
    if not getattr(prop, "emissive", False) or getattr(prop, "broken", False):
        return 0.0
    if getattr(prop, "light_radius", None):
        return prop_flicker(prop, t)
    return 1.0


LIGHT_EPOCH = [0]
_EMITTER_CACHE = {}


def _emitters(props):
    sig = (len(props), sum(map(id, props)), LIGHT_EPOCH[0])
    found = _EMITTER_CACHE.get(sig)
    if found is None:
        if len(_EMITTER_CACHE) >= 4:
            _EMITTER_CACHE.clear()
        found = _EMITTER_CACHE[sig] = [p for p in props if getattr(p, "light_radius", None)]
    return found


def select_prop_lights(props, eye, view_dist, t, limit=MAX_OMNI_LIGHTS):
    ex, ey, ez = eye
    picked = []
    for p in _emitters(props):
        radius = getattr(p, "light_radius", None)
        if not radius or p.picked or getattr(p, "broken", False):
            continue
        pos = light_emit_pos(p)
        d2 = (pos[0] - ex) ** 2 + (pos[1] - ey) ** 2 + (pos[2] - ez) ** 2
        reach = view_dist + radius + LIGHT_SELECT_MARGIN
        if d2 > reach * reach:
            continue
        picked.append((d2, p, pos))
    picked.sort(key=lambda item: item[0])
    lights = []
    for _d2, p, pos in picked[:limit]:
        k = prop_flicker(p, t)
        direction, cone = prop_light_lobe(p)
        lights.append(LightSource(
            key=id(p), shape=SHAPE_OMNI, pos=pos, radius=p.light_radius,
            color=tuple(c * k for c in p.light_color), direction=direction, cone=cone,
            emitter=PROP_EMITTER_RADIUS.get(p.kind, PROP_EMITTER_RADIUS_DEFAULT), owner=p))
    return lights


def _flash_gain(intensity, t):
    living = 0.97 + 0.03 * math.sin(t * 2.3)
    return intensity * living * FLASH_OUTPUT_GAIN


def flashlight_light(pos, direction, intensity, t, share=1.0):
    gain = _flash_gain(intensity, t) * share
    return LightSource(
        key="flashlight", shape=SHAPE_SPOT, pos=pos, direction=direction,
        radius=FLASH_RANGE, falloff=FLASH_FALLOFF_SHAPE, color=tuple(c * gain for c in FLASH_TINT),
        cone=(FLASH_CONE_OUTER_COS, FLASH_CONE_INNER_COS, FLASH_HOT_OUTER_COS, FLASH_HOT_INNER_COS),
        emitter=FLASH_EMITTER_RADIUS, dynamic=True, skip_bearer=True,
        bearer_reveal=FLASHLIGHT_BEARER_REVEAL)


def flashlight_fill_light(eye, direction, intensity, t):
    gain = _flash_gain(intensity, t) * FLASH_FILL_SHARE
    return LightSource(
        key="flashlight_fill", shape=SHAPE_SPOT, pos=eye, direction=direction,
        radius=FLASH_RANGE, falloff=FLASH_FALLOFF_SHAPE, color=tuple(c * gain for c in FLASH_TINT),
        cone=(FLASH_FILL_CONE_OUTER_COS, FLASH_FILL_CONE_INNER_COS, 1.5, 2.0),
        emitter=FLASH_EMITTER_RADIUS, dynamic=True, skip_bearer=True, at_viewpoint=True)


def lighter_light(pos, radius, color, bearer_reveal=0.0):
    return LightSource(key="lighter", shape=SHAPE_OMNI, pos=pos, radius=radius, color=color,
                       emitter=LIGHTER_EMITTER_RADIUS, dynamic=True,
                       bearer_reveal=bearer_reveal, counts_for_sanity=False)


def moon_light(strength):
    return LightSource(
        key="moon", shape=SHAPE_DIRECTIONAL, pos=(0.0, 0.0, 0.0),
        direction=tuple(-c for c in MOON_TOWARD), color=tuple(c * strength for c in MOON_TINT),
        emitter=MOON_ANGULAR_RADIUS)


FLASHLIGHT_BEARER_REVEAL = 0.5

GAMEPLAY_RECEIVER_HEIGHT = 0.5


def light_falloff(dist, radius, shape=FALLOFF_SHAPE):
    lt = min(1.0, max(0.0, dist / max(radius, 0.001)))
    ramp = (1.0 - lt) ** shape
    if lt > 0.85:
        s = (lt - 0.85) / 0.15
        ramp *= 1.0 - s * s * (3.0 - 2.0 * s)
    return ramp


def luminance(rgb):
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def light_reaching(light, point):
    if light.shape == SHAPE_DIRECTIONAL:
        tx, ty = -light.dir[0], -light.dir[1]
        return luminance(light.color) * math.hypot(tx, ty)
    dx = light.pos[0] - point[0]
    dy = light.pos[1] - point[1]
    dz = light.pos[2] - point[2]
    d = math.sqrt(dx * dx + dy * dy + dz * dz)
    atten = light_falloff(d, light.radius, light.falloff)
    if atten <= 0.0:
        return 0.0
    facing = (math.hypot(dx, dy) / d) ** 0.45 if d > 1e-4 else 1.0
    lobe = light_lobe_factor(light, (-dx / d, -dy / d, -dz / d)) if d > 1e-4 else 1.0
    return luminance(light.color) * atten * facing * lobe


def gameplay_lights(props, point, t, moon_strength):
    lights = select_prop_lights(props, point, 0.0, t)
    if moon_strength > 0.0:
        lights.append(moon_light(moon_strength))
    return lights


def light_level_at(point, lights, ambient, is_occluded):
    level = ambient
    for light in lights:
        if light.bearer_reveal:
            continue
        amount = light_reaching(light, point)
        if amount > 0.0 and not is_occluded(light, point):
            level += amount
    return level


def player_light_levels(point, lights, ambient, is_occluded, carried):
    room = light_level_at(point, lights, ambient, is_occluded)
    level = sanity = room
    for light in carried:
        level += light.bearer_reveal
        if light.counts_for_sanity:
            sanity += light.bearer_reveal
    return level, sanity, room
