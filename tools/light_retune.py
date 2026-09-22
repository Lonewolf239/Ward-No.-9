import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game import settings as S
from game.entities import monster_lit_frac, monster_vision_base
from game.lighting import (FLASHLIGHT_BEARER_REVEAL, GAMEPLAY_RECEIVER_HEIGHT, LightSource, SHAPE_OMNI,
                           light_emit_pos, light_reaching, luminance, moon_light)
from game.props import PROP_DEFS


class _Lamp:
    def __init__(self, kind, z0):
        spec = PROP_DEFS[kind]
        self.kind = kind
        self.x = self.y = 0.0
        self.z0 = z0
        self.height = spec["height"]
        self.light_radius = spec["light_radius"]
        self.light_color = spec["light_color"]


def old_atten(dist, radius):
    lt = max(0.0, min(1.0, dist / max(radius, 0.001)))
    tail = 1.0
    if lt > 0.85:
        tt = (lt - 0.85) / 0.15
        tail = 1.0 - tt * tt * (3.0 - 2.0 * tt)
    return (1.0 - lt) * tail


def new_lamp(kind, z0, dist):
    lamp = _Lamp(kind, z0)
    pos = light_emit_pos(lamp)
    light = LightSource(key=kind, shape=SHAPE_OMNI, pos=pos, radius=lamp.light_radius, color=lamp.light_color)
    return light_reaching(light, (dist, 0.0, GAMEPLAY_RECEIVER_HEIGHT))


LAMPS = {
    "lamp_desk": 0.36,
    "wall_sconce": PROP_DEFS["wall_sconce"].get("z0", 0.55),
    "sign_exit": PROP_DEFS["sign_exit"].get("z0", 0.85),
    "lamppost": 0.0,
}

SITUATIONS = [
    ("unlit", None, 0.0, False, False),
    ("lamp_desk 1m", "lamp_desk", 1.0, False, False),
    ("lamp_desk 2m", "lamp_desk", 2.0, False, False),
    ("sconce 1.5m", "wall_sconce", 1.5, False, False),
    ("exit sign 1m", "sign_exit", 1.0, False, False),
    ("lamppost 2m", "lamppost", 2.0, False, False),
    ("flashlight", None, 0.0, True, False),
    ("lighter", None, 0.0, False, True),
]


def vision_and_spot(level, vision_mult, norm=S.MONSTER_VISION_LIGHT_NORM):
    lit = monster_lit_frac(level, norm)
    vision = monster_vision_base(level, norm) * vision_mult
    spot = S.MONSTER_FOV_ACUTE_DETECT_SECONDS_DARK + (
        S.MONSTER_FOV_ACUTE_DETECT_SECONDS_LIT - S.MONSTER_FOV_ACUTE_DETECT_SECONDS_DARK) * lit
    return vision, spot


def main():
    for spec in S.FLOOR_SPECS:
        ambient = spec.get("ambient_level", 0.0)
        moon = spec.get("moon_strength", 0.0)
        vm = spec.get("vision_mult", 1.0)
        norm = spec.get("vision_light_norm", S.MONSTER_VISION_LIGHT_NORM)
        moon_new = light_reaching(moon_light(moon), (0.0, 0.0, GAMEPLAY_RECEIVER_HEIGHT)) if moon else 0.0
        print(f"\n{spec['key']}: ambient {ambient}, moon {moon}, vision_mult {vm}")
        print(f"  {'situation':14s} {'old lvl':>7s} {'new lvl':>7s} | {'old sight':>9s} {'new sight':>9s} {'d%':>5s} "
              f"| {'old spot s':>10s} {'new spot s':>10s}")
        for name, kind, dist, flash, lighter in SITUATIONS:
            old = ambient
            new = ambient + moon_new
            if kind:
                lamp = _Lamp(kind, LAMPS[kind])
                old += old_atten(dist, lamp.light_radius)
                new += new_lamp(kind, LAMPS[kind], dist)
            if flash:
                old += 0.5
                new += FLASHLIGHT_BEARER_REVEAL
            if lighter:
                old += S.LIGHTER_LIGHT_LEVEL_BONUS
                new += S.LIGHTER_LIGHT_LEVEL_BONUS
            ov, os_ = vision_and_spot(old, vm)
            nv, ns = vision_and_spot(new, vm, norm)
            print(f"  {name:14s} {old:7.3f} {new:7.3f} | {ov:9.2f} {nv:9.2f} {100 * (nv - ov) / ov:+5.1f} "
                  f"| {os_:10.2f} {ns:10.2f}")
    print("\nlight colour as brightness:", {k: round(luminance(PROP_DEFS[k]["light_color"]), 2) for k in LAMPS})


if __name__ == "__main__":
    main()
