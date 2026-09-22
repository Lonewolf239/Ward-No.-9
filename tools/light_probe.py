import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import moderngl
import numpy as np

from game import settings as S
from game.maze import Maze
from game.entities import Player
from game.renderer3d import Renderer3D

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "light_probe_out")


def blank_maze(w, h, fill=S.FLOOR):
    m = Maze.__new__(Maze)
    m.w, m.h = w, h
    m.grid = [[S.WALL_CONCRETE] * w for _ in range(h)]
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            m.grid[y][x] = fill
    m.start = (w * 0.5, h * 0.5)
    m.rooms, m.zones, m.template_doors = [], [], []
    m.showcase_rect = m.surface_showcase_rect = None
    m.reset_derived()
    return m


class Probe:
    def __init__(self, out_dir=OUT_DIR):
        self.ctx = moderngl.create_standalone_context()
        self.renderer = Renderer3D(self.ctx)
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.player = Player(1.5, 1.5)
        self.player.battery = 100.0
        self.player.flashlight_on = False

    def build(self, maze, theme="upper"):
        self.maze = maze
        self.renderer.build_level(maze, theme=theme)

    def _place(self, pos, angle, pitch):
        p = self.player
        p.x, p.y = pos
        p.peek_x, p.peek_y = pos
        p.angle, p.pitch = angle, pitch
        return p

    def shot(self, name, pos, angle, pitch=0.0, props=(), save=True, **light):
        p = self.player
        p.x, p.y = pos
        p.peek_x, p.peek_y = pos
        p.angle, p.pitch = angle, pitch
        self.renderer.render(
            self.maze, p, None, list(props), dread=0.0, t=0.0,
            hide_monster=True,
            **light,
        )
        data = self.renderer.color_tex.read()
        w, h = self.renderer.color_tex.size
        arr = np.frombuffer(data, dtype=np.uint8).reshape(h, w, 3).astype(np.float32)
        if save:
            self._save(name, arr)
        return arr

    def _save(self, name, arr):
        import pygame
        flipped = np.flipud(arr).astype(np.uint8)
        surf = pygame.image.frombuffer(flipped.tobytes(), (arr.shape[1], arr.shape[0]), "RGB")
        pygame.image.save(surf, os.path.join(self.out_dir, f"{name}.png"))


def band(arr, x0, x1, y0, y1):
    h, w = arr.shape[0], arr.shape[1]
    return arr[int(h * y0):int(h * y1), int(w * x0):int(w * x1), :]


def luma(a):
    return float(a.mean())


def levels(a):
    return int(np.unique(np.round(a.mean(axis=2))).size)


def report(scene, **kv):
    parts = " ".join(f"{k}={v}" for k, v in kv.items())
    print(f"{scene}: {parts}")


SCENES = {}


def scene(fn):
    SCENES[fn.__name__] = fn
    return fn


@scene
def moon(probe):
    from game.lighting import MOON_TOWARD as T
    probe.build(blank_maze(13, 13), theme="yard")
    probe.player.flashlight_on = False
    yard = dict(fog_color=(10, 14, 9), fog_dist=8.5, ambient=0.16)
    cases = (("east_wall", (9.5, 6.5), 0.0, (-1, 0, 0)), ("west_wall", (3.5, 6.5), math.pi, (1, 0, 0)),
             ("north_wall", (6.5, 9.5), math.pi / 2, (0, -1, 0)), ("south_wall", (6.5, 3.5), -math.pi / 2, (0, 1, 0)),
             ("floor", (6.5, 6.5), 0.0, (0, 0, 1)))
    all_ok = True
    for tag, pos, ang, n in cases:
        pitch = -1.35 if n[2] else 0.0
        vals = [luma(band(probe.shot(f"moon_{tag}_{st}", pos, ang, pitch, moon_strength=st, **yard),
                          0.40, 0.60, 0.44, 0.56)) for st in (0.0, 0.6)]
        facing = n[0] * T[0] + n[1] * T[1] + n[2] * T[2]
        ok = (facing > 0) == (vals[1] - vals[0] > 0.3)
        all_ok &= ok
        report(f"moon/{tag}", facing=round(facing, 2), delta=round(vals[1] - vals[0], 3), matches_direction=ok)
    report("moon", all_surfaces_match=all_ok)


@scene
def cache(probe):
    from game.props import Prop, Door
    probe.build(blank_maze(21, 11))
    probe.player.flashlight_on = False
    r = probe.renderer
    near_lamp = Prop("wall_sconce", 5.0, 5.0, facing=0.0)
    mid_lamp = Prop("wall_sconce", 9.0, 5.0, facing=0.0)
    far_lamp = Prop("wall_sconce", 16.0, 5.0, facing=0.0)
    door = Door(5.5, 7.5, 0.0)
    props = [near_lamp, mid_lamp, far_lamp, Prop("crate", 7.0, 4.0, facing=0.0), door]
    light = dict(fog_color=S.COL_FOG, fog_dist=12.5, ambient=0.055, moon_strength=0.0)

    def frame(pos, t):
        probe.renderer.render(probe.maze, probe._place(pos, 0.0, 0.0), None, props, dread=0.0, t=t,
                              hide_monster=True, **light)
        return list(r.last_shadow_rebuilds)

    frame((7.0, 5.5), 0.0)
    static = sum(len(frame((7.0, 5.5), i / 60.0)) for i in range(1, 301))
    walk = 0
    for i in range(120):
        x = 5.5 + 3.0 * (0.5 - 0.5 * math.cos(i / 120.0 * 2 * math.pi))
        walk += len(frame((x, 5.5), 5.0 + i / 60.0))
    swing_rebuilds = set()
    for i in range(30):
        door.swing = i / 29.0
        swing_rebuilds |= set(frame((7.0, 5.5), 7.0 + i / 60.0))
    names = {id(near_lamp): "near_lamp", id(mid_lamp): "mid_lamp", id(far_lamp): "far_lamp"}
    report("cache", static_300_frames_rebuilds=static, walk_order_swap_rebuilds=walk,
           door_swing_rebuilt=sorted(names.get(k, str(k)) for k in swing_rebuilds))


@scene
def ambient(probe):
    probe.build(blank_maze(13, 13))
    for floor_spec in S.FLOOR_SPECS[:2]:
        amb = floor_spec["ambient_level"]
        arr = probe.shot(f"ambient_{floor_spec['key']}", (6.5, 9.0), -math.pi / 2, 0.0,
                         fog_color=floor_spec["fog_color"], fog_dist=floor_spec["fog_dist"],
                         ambient=amb, moon_strength=0.0)
        wall = band(arr, 0.35, 0.65, 0.35, 0.60)
        report(f"ambient/{floor_spec['key']}", ambient=amb, luma=round(luma(wall), 3),
               levels=levels(wall), black_pct=round(float((wall.mean(axis=2) < 0.5).mean()) * 100, 1))


@scene
def census(probe):
    import random as _random
    from game import props as P
    reach, visible = [], []
    for seed in range(20):
        spec = S.FLOOR_SPECS[0]
        _random.seed(seed)
        maze = Maze(seed=seed, wall_bias=spec.get("wall_bias"),
                    template_floor=spec.get("floor_theme"))
        rng = _random.Random(seed ^ 0xB0B0)
        try:
            objs, _panel, _exit, _mcell, doors = P.populate_level(maze, spec, rng)
        except Exception as exc:
            report("census", error=type(exc).__name__, msg=str(exc)[:70])
            return
        lights = [o for o in list(objs) + list(doors) if getattr(o, "light_radius", None)]
        floors = [(x + 0.5, y + 0.5) for y in range(maze.h) for x in range(maze.w)
                  if maze.grid[y][x] == S.FLOOR]
        if not floors:
            continue
        view = spec.get("fog_dist", 12.5)
        for (px, py) in _random.Random(seed).sample(floors, min(40, len(floors))):
            d2 = [((L.x - px) ** 2 + (L.y - py) ** 2, L.light_radius) for L in lights]
            reach.append(sum(1 for dd, r in d2 if dd < r * r))
            visible.append(sum(1 for dd, r in d2 if dd < (view + r) ** 2))

    def pct(a):
        a = np.array(a)
        return dict(mean=round(float(a.mean()), 2), p50=int(np.percentile(a, 50)),
                    p90=int(np.percentile(a, 90)), p99=int(np.percentile(a, 99)), max=int(a.max()))
    report("census/reaching_point", samples=len(reach), **pct(reach))
    report("census/within_view", **pct(visible))


@scene
def corridor(probe):
    probe.build(blank_maze(25, 5))
    probe.player.flashlight_on = True
    for dist in (2, 4, 6, 8, 10):
        arr = probe.shot(f"corridor_d{dist}", (23.5 - dist, 2.5), 0.0, 0.0,
                         fog_color=S.COL_FOG, fog_dist=12.5, ambient=0.055, moon_strength=0.0)
        patch = band(arr, 0.45, 0.55, 0.44, 0.56)
        report(f"corridor/d{dist}", wall_luma=round(luma(patch), 2))
    probe.player.flashlight_on = False


@scene
def lamps(probe):
    from game.props import Prop
    probe.build(blank_maze(13, 13))
    lamp = Prop("wall_sconce", 4.6, 6.5, facing=0.0)
    crate = Prop("crate", 6.0, 6.5, facing=0.0)
    locker = Prop("locker", 8.2, 4.4, facing=math.pi / 2)
    props = [lamp, crate, locker]
    light = dict(fog_color=S.COL_FOG, fog_dist=12.5, ambient=0.055, moon_strength=0.0)
    views = (((8.6, 8.4), math.atan2(6.5 - 8.4, 6.6 - 8.6), -0.30),
             ((6.5, 10.5), -math.pi / 2, -0.10),
             ((3.0, 3.0), math.radians(40), -0.05))
    for flash in (False, True):
        probe.player.flashlight_on = flash
        for i, (pos, ang, pitch) in enumerate(views):
            arr = probe.shot(f"lamps_f{int(flash)}_v{i}", pos, ang, pitch, props=props, **light)
            report(f"lamps/flash{int(flash)}/v{i}", luma=round(luma(arr), 3))
    crate.x += 0.6
    probe.player.flashlight_on = True
    arr = probe.shot("lamps_moved", views[0][0], views[0][1], views[0][2], props=props, **light)
    report("lamps/after_move", luma=round(luma(arr), 3))
    probe.player.flashlight_on = False


@scene
def farfog(probe):
    probe.build(blank_maze(27, 5))
    probe.player.flashlight_on = False
    for fd in (12.5, 60.0):
        arr = probe.shot(f"farfog_{int(fd)}", (2.5, 2.5), 0.0, 0.0,
                         fog_color=(40, 40, 40), fog_dist=fd, ambient=0.75, moon_strength=0.0)
        report(f"farfog/fog{int(fd)}", far_wall=round(luma(band(arr, 0.46, 0.54, 0.45, 0.55)), 3))


@scene
def churn(probe):
    import random as _random
    from game import props as P
    from game.lighting import light_emit_pos
    spec = S.FLOOR_SPECS[0]
    _random.seed(3)
    maze = Maze(seed=3, wall_bias=spec.get("wall_bias"), template_floor=spec.get("floor_theme"))
    objs, _p, _e, _m, doors = P.populate_level(maze, spec, _random.Random(3 ^ 0xB0B0))
    probe.build(maze)
    props = list(objs) + list(doors)
    by_key = {id(o): o for o in objs}
    r = probe.renderer
    view = spec["fog_dist"]
    best = (0, 0, 0)
    for y in range(maze.h):
        run = 0
        for x in range(maze.w):
            run = run + 1 if maze.grid[y][x] == S.FLOOR else 0
            if run > best[0]:
                best = (run, x - run + 1, y)
    length, x0, yy = best
    path = [x0 + 0.5 + i * 0.25 for i in range(int((length - 1) / 0.25))]
    path = path + path[::-1]
    captured = {}
    real_upload = r._upload_lights

    def spy(lights):
        captured["lights"] = [(l.key, 0 if l.at_viewpoint else l.tile0) for l in lights]
        return real_upload(lights)
    r._upload_lights = spy
    light = dict(fog_color=spec["fog_color"], fog_dist=view, ambient=spec["ambient_level"], moon_strength=0.0)
    prev, unshadowed, switches, visible_switches, max_up = None, 0, 0, 0, 0
    for ex in path:
        probe.shot("churn", (ex, yy + 0.5), 0.0, 0.0, props=props, save=False, **light)
        now = dict(captured["lights"])
        max_up = max(max_up, len(now))
        unshadowed += sum(1 for k, t0 in now.items() if t0 < 0)
        if prev is not None:
            for k in set(now) ^ set(prev):
                switches += 1
                o = by_key.get(k)
                if o is not None:
                    lx, ly, lz = light_emit_pos(o)
                    d = math.sqrt((lx - ex) ** 2 + (ly - yy - 0.5) ** 2 + (lz - 0.62) ** 2)
                    if d <= view + o.light_radius:
                        visible_switches += 1
        prev = now
    r._upload_lights = real_upload
    report("churn", corridor_len=length, steps=len(path), max_lights_uploaded=max_up,
           uploaded_without_shadow=unshadowed, on_off_switches=switches,
           switches_within_view_reach=visible_switches)


@scene
def perf(probe):
    import random as _random
    import time as _time
    from game import props as P
    spec = S.FLOOR_SPECS[0]
    _random.seed(5)
    maze = Maze(seed=5, wall_bias=spec.get("wall_bias"), template_floor=spec.get("floor_theme"))
    objs, _p, _e, _m, doors = P.populate_level(maze, spec, _random.Random(5 ^ 0xB0B0))
    probe.build(maze)
    props = list(objs) + list(doors)
    r = probe.renderer
    probe.player.flashlight_on = True
    lights = [o for o in objs if getattr(o, "light_radius", None) and not getattr(o, "broken", False)]
    anchor_light = max(lights, key=lambda L: sum(1 for M in lights if (M.x - L.x) ** 2 + (M.y - L.y) ** 2 < 36))
    cells = [(x + 0.5, y + 0.5) for y in range(maze.h) for x in range(maze.w) if maze.grid[y][x] == S.FLOOR]
    pos = min(cells, key=lambda c: (c[0] - anchor_light.x) ** 2 + (c[1] - anchor_light.y) ** 2)
    light = dict(fog_color=spec["fog_color"], fog_dist=spec["fog_dist"], ambient=spec["ambient_level"], moon_strength=0.0)
    full, warm, total, counts = [], [], [], []
    for i in range(40):
        r._invalidate_shadow_cache()
        t0 = _time.perf_counter()
        probe.shot(f"perf_{i}", pos, i * 0.15, 0.0, props=props, save=(i in (0, 13, 26)), **light)
        probe.ctx.finish()
        total.append((_time.perf_counter() - t0) * 1000.0)
        full.append(r.last_perf.get("shadows", 0.0))
        counts.append(r.prog["light_count"].value)
    for i in range(20):
        probe.shot("perf_steady", pos, i * 0.15, 0.0, props=props, save=False, **light)
        warm.append(r.last_perf.get("shadows", 0.0))
    med = lambda a, k=5: round(float(np.median(a[k:])), 3)
    report("perf", level=f"{maze.w}x{maze.h}", props=len(props), lights_uploaded=int(np.median(counts)),
           full_rebuild_shadow_ms=med(full), steady_shadow_ms=med(warm, 2), frame_ms_full_rebuild=med(total))
    probe.player.flashlight_on = False


@scene
def pcf(probe):
    from game.props import Prop
    from game.renderer3d import SHADOW_QUALITY_TAPS
    probe.build(blank_maze(13, 13))
    props = [Prop("wall_sconce", 4.6, 6.5, facing=0.0), Prop("crate", 6.0, 6.5, facing=0.0),
             Prop("locker", 8.2, 4.4, facing=math.pi / 2)]
    light = dict(fog_color=S.COL_FOG, fog_dist=12.5, ambient=0.055, moon_strength=0.0)
    pos, ang = (8.6, 8.4), math.atan2(6.5 - 8.4, 6.6 - 8.6)
    r = probe.renderer
    keep = (r.shadow_blocker_taps, r.shadow_filter_taps)
    for level in SHADOW_QUALITY_TAPS:
        r.set_shadow_quality(level)
        for flash in (False, True):
            probe.player.flashlight_on = flash
            arr = probe.shot(f"pcf_{level}_f{int(flash)}", pos, ang, -0.30, props=props, **light)
            report(f"pcf/{level}/flash{int(flash)}", luma=round(luma(arr), 3))
    r.shadow_blocker_taps, r.shadow_filter_taps = keep
    probe.player.flashlight_on = False


def _shadow_fraction(probe, name, pos, yaw, pitch, props, light, cam=None, emitter=None):
    r = probe.renderer
    orig = r._upload_lights
    cam = cam or {}
    def with_emitter(lights):
        for l in lights:
            if emitter is not None:
                l.emitter = emitter
        return orig(lights)
    def unshadowed(lights):
        for l in lights:
            l.tile0 = -1
        return orig(lights)
    try:
        r._upload_lights = with_emitter
        shaded = probe.shot(name, pos, yaw, pitch, props=props, **cam, **light).mean(axis=2)
        r._upload_lights = unshadowed
        free = probe.shot(name + "_unshadowed", pos, yaw, pitch, props=props, save=False, **cam, **light).mean(axis=2)
    finally:
        r._upload_lights = orig
    return np.where(free > 3.0, np.clip(1.0 - shaded / np.maximum(free, 1.0), 0.0, 1.0), 0.0), free


@scene
def penumbra(probe):
    from game.props import Prop
    r = probe.renderer
    keep_res = (r.low_w, r.low_h, r.snap_res)
    r.set_resolution(1280, 720, keep_res[2])
    try:
        m = blank_maze(18, 16)
        for x in range(8, 13):
            m.grid[8][x] = S.WALL_CONCRETE
        probe.build(m)
        lamp = Prop("lamppost", 8.0, 7.2, facing=0.0)
        lamp.z0 = 0.1
        lamp.light_radius = 4.4
        light = dict(fog_color=(0, 0, 0), fog_dist=500.0, ambient=0.0, moon_strength=0.0)
        probe.player.flashlight_on = False
        cam = dict(camera_override=((8.0, 10.2, 1.12), math.pi / 2, -math.pi / 2 + 0.001, 100.0))
        for label, emitter in (("point", 0.0), ("real", None)):
            shadow, free = _shadow_fraction(probe, f"penumbra_{label}", (8.0, 10.2), 0.0, 0.0, [lamp], light, cam, emitter)
            h, w = shadow.shape
            widths = []
            for frac in (0.15, 0.25, 0.35, 0.45):
                y = int(h * frac)
                row = shadow[y - 3:y + 4].mean(axis=0)
                crossing = int(np.argmin(np.abs(row - 0.5)))
                win = row[max(0, crossing - 120):crossing + 120]
                widths.append(int(((win > 0.1) & (win < 0.9)).sum()))
            report(f"penumbra/{label}", edge_px_near_to_far=widths)

        m = blank_maze(20, 13)
        for x in range(1, 9):
            m.grid[8][x] = S.WALL_CONCRETE
        for (bx, by) in ((12, 10), (13, 10)):
            m.grid[by][bx] = S.WALL_CONCRETE
        probe.build(m)
        pl = probe.player
        pl.flashlight_on = True
        pl.active_held_item = "flashlight"
        pl.equip_t = 1.0
        light = dict(fog_color=S.COL_FOG, fog_dist=12.5, ambient=0.055, moon_strength=0.0)
        for label, emitter in (("point", 0.0), ("real", None)):
            shadow, free = _shadow_fraction(probe, f"torch_edge_{label}", (8.45, 7.78), 0.55, 0.12, [], light, None, emitter)
            visible = free > 12.0
            report(f"torch_edge/{label}", shadowed_px=int((shadow[visible] > 0.1).sum()),
                   fully_dark_px=int((shadow[visible] > 0.9).sum()),
                   mean_shadow_where_shadowed=round(float(shadow[visible & (shadow > 0.1)].mean()), 3)
                   if (visible & (shadow > 0.1)).any() else 0.0)
        pl.active_held_item = None
        pl.equip_t = 0.0
        pl.flashlight_on = False

        probe.build(blank_maze(15, 15))
        lamp = Prop("lamp_desk", 7.5, 7.5, facing=0.0)
        lamp.z0 = 0.95
        light = dict(fog_color=(0, 0, 0), fog_dist=500.0, ambient=0.0, moon_strength=0.0)
        cam = dict(camera_override=((7.5, 8.6, 1.12), math.pi / 2, -math.pi / 2 + 0.001, 100.0))
        shadow, free = _shadow_fraction(probe, "penumbra_empty", (7.5, 8.6), 0.0, 0.0, [lamp], light, cam)
        lit = free > 3.0
        report("penumbra/empty_floor_self_shadow", max_shadow=round(float(shadow[lit].max()), 3),
               px_over_2pct=int((shadow[lit] > 0.02).sum()), lit_px=int(lit.sum()))
    finally:
        r.set_resolution(*keep_res)


@scene
def jamb(probe):
    m = blank_maze(26, 11, fill=S.WALL_CONCRETE)
    for x in range(1, 25):
        m.grid[5][x] = S.FLOOR
    for (ox, oy) in ((9, 4), (9, 3), (9, 6), (9, 7)):
        m.grid[oy][ox] = S.FLOOR
    probe.build(m)
    pl = probe.player
    light = dict(fog_color=S.COL_FOG, fog_dist=12.5, ambient=0.055, moon_strength=0.0)
    for held in (True, False):
        pl.flashlight_on = True
        pl.active_held_item = "flashlight" if held else None
        pl.equip_t = 1.0 if held else 0.0
        tag = "hand" if held else "eye"
        arr = probe.shot(f"jamb_{tag}", (3.5, 5.5), 0.0, 0.0, **light).mean(axis=2)
        h, w = arr.shape
        left = arr[int(h * 0.30):int(h * 0.62), int(w * 0.25):int(w * 0.48)]
        right = arr[int(h * 0.30):int(h * 0.62), int(w * 0.52):int(w * 0.75)][:, ::-1]
        report(f"jamb/{tag}", left_half_mean=round(float(left.mean()), 2), right_half_mean=round(float(right.mean()), 2),
               asymmetry_pct=round(float(abs(left.mean() - right.mean()) / max(1e-6, (left.mean() + right.mean()) / 2)) * 100, 1))
    pl.active_held_item = None
    pl.equip_t = 0.0
    pl.flashlight_on = False


@scene
def emission(probe):
    from game.props import Prop
    probe.build(blank_maze(11, 11))
    r = probe.renderer
    lamp = Prop("lamp_desk", 5.5, 5.5, facing=0.0)
    lamp.bob_phase = 0.3
    light = dict(fog_color=(0, 0, 0), fog_dist=500.0, ambient=0.03, moon_strength=0.0)
    pl = probe.player
    pl.flashlight_on = False
    pos, yaw, pitch = (5.5, 4.4), math.pi / 2, -0.35

    def frame(props, t):
        pl.x, pl.y = pos
        pl.peek_x, pl.peek_y = pos
        pl.angle, pl.pitch = yaw, pitch
        r.render(probe.maze, pl, None, list(props), dread=0.0, t=t, hide_monster=True, **light)
        w, h = r.color_tex.size
        return np.frombuffer(r.color_tex.read(), dtype=np.uint8).reshape(h, w, 3).mean(axis=2).astype(np.float32)

    empty = frame([], 0.0)
    with_lamp = frame([lamp], 0.0)
    lamp_mask = np.abs(with_lamp - empty) > 0
    shade = lamp_mask & (with_lamp > np.percentile(with_lamp[lamp_mask], 90))
    floor = lamp_mask & ~shade & (with_lamp > 2)
    shade_series, floor_series = [], []
    for i in range(60):
        img = frame([lamp], i / 30.0)
        shade_series.append(float(img[shade].mean()))
        floor_series.append(float(img[floor].mean()))
    shade_series, floor_series = np.array(shade_series), np.array(floor_series)
    corr = float(np.corrcoef(shade_series, floor_series)[0, 1]) if shade_series.std() > 0 and floor_series.std() > 0 else 0.0
    report("emission/flicker", shade_std=round(float(shade_series.std()), 3), floor_std=round(float(floor_series.std()), 3),
           shade_floor_correlation=round(corr, 3), shade_px=int(shade.sum()))
    broken = Prop("lamp_desk", 5.5, 5.5, facing=0.0)
    broken.broken = True
    dark = Prop("lamp_desk", 5.5, 5.5, facing=0.0)
    dark.broken = True
    dark.emissive = False
    a, b = frame([broken], 0.0), frame([dark], 0.0)
    report("emission/broken_vs_non_emissive", max_px_diff=float(np.abs(a - b).max()))


@scene
def gameplay_light(probe):
    from game.props import Prop
    from game.lighting import (GAMEPLAY_RECEIVER_HEIGHT, gameplay_lights, light_level_at, light_emit_pos,
                               SHAPE_DIRECTIONAL, MOON_TOWARD)
    r = probe.renderer
    pl = probe.player
    pl.flashlight_on = False
    results = []

    def measure(maze, theme, props, point_xy, toward_xy, ambient, moon, label, also_toward=None, cpu_props=None):
        if also_toward is not None:
            before = len(results)
            lamp_a = [p for p in props if (p.x, p.y) == toward_xy]
            lamp_b = [p for p in props if (p.x, p.y) == also_toward]
            measure(maze, theme, props, point_xy, toward_xy, ambient, moon, "_a", cpu_props=lamp_a)
            measure(maze, theme, props, point_xy, also_toward, ambient, moon, "_b", cpu_props=lamp_b)
            (_, cpu_a, gpu_a, _), (_, cpu_b, gpu_b, _) = results[before:]
            del results[before:]
            cpu = cpu_a + cpu_b - ambient
            gpu = gpu_a + gpu_b - ambient
            results.append((label, round(cpu, 3), round(gpu, 3), round(cpu - gpu, 3)))
            return
        box = Prop("crate", point_xy[0], point_xy[1], facing=0.0)
        box.kind = "gameplay_probe"
        box.texture = None
        box.base_color = (255, 255, 255)
        box.hw = box.hd = 0.08
        box.height = 0.24
        box.z0 = GAMEPLAY_RECEIVER_HEIGHT - box.height / 2
        facing = math.atan2(toward_xy[1] - point_xy[1], toward_xy[0] - point_xy[0])
        box.facing = facing
        cam = (point_xy[0] + math.cos(facing) * 0.45, point_xy[1] + math.sin(facing) * 0.45)
        light = dict(fog_color=(0, 0, 0), fog_dist=500.0, ambient=ambient, moon_strength=moon)
        pl.x, pl.y = cam
        pl.peek_x, pl.peek_y = cam
        pl.angle = facing + math.pi
        pl.pitch = -math.atan2(0.62 - GAMEPLAY_RECEIVER_HEIGHT, 0.45)
        r.render(maze, pl, None, list(props) + [box], dread=0.0, t=0.0, hide_monster=True, qa_mode=True, **light)
        w, h = r.color_tex.size
        img = np.frombuffer(r.color_tex.read(), dtype=np.uint8).reshape(h, w, 3).astype(np.float32)
        patch = img[h // 2 - 4:h // 2 + 4, w // 2 - 4:w // 2 + 4]
        gpu = float((0.299 * patch[..., 0] + 0.587 * patch[..., 1] + 0.114 * patch[..., 2]).mean() / 255.0)
        point = (point_xy[0] + math.cos(facing) * box.hw, point_xy[1] + math.sin(facing) * box.hw,
                 GAMEPLAY_RECEIVER_HEIGHT)

        def occluded(l, pt):
            if l.shape == SHAPE_DIRECTIONAL:
                return not maze.moon_reaches(pt[0], pt[1])
            return not maze.has_line_of_sight(pt[0], pt[1], l.pos[0], l.pos[1])
        cpu = light_level_at(point, gameplay_lights(props if cpu_props is None else cpu_props, point, 0.0, moon),
                             ambient, occluded)
        results.append((label, round(cpu, 3), round(gpu, 3), round(cpu - gpu, 3)))

    m = blank_maze(20, 14)
    for y in range(1, 8):
        m.grid[y][10] = S.WALL_CONCRETE
    probe.build(m)
    lamp = Prop("lamp_desk", 5.5, 4.5, facing=0.0)
    lamp.z0 = 0.45
    lamp2 = Prop("lamp_desk", 5.5, 9.5, facing=0.0)
    lamp2.z0 = 0.45
    sign = Prop("sign_exit", 15.5, 11.2, facing=0.0)
    mon = Prop("monitor", 15.5, 4.5, facing=0.0)
    mon.z0 = 0.4
    for prop in (lamp, lamp2, sign, mon):
        prop.bob_phase = 0.0
    props = [lamp, lamp2, sign, mon]
    ambient = 0.055
    lp = light_emit_pos(lamp)
    for d, label in ((0.6, "lamp 0.6m"), (1.2, "lamp 1.2m"), (1.8, "lamp 1.8m"), (2.3, "lamp 2.3m")):
        measure(m, "upper", props, (lp[0] - d, lp[1]), (lp[0], lp[1]), ambient, 0.0, label)
    measure(m, "upper", props, (11.5, 4.5), (lp[0], lp[1]), ambient, 0.0, "behind wall from lamp")
    measure(m, "upper", props, (2.0, 12.5), (1.0, 12.5), ambient, 0.0, "dark corner")
    se = light_emit_pos(sign)
    measure(m, "upper", props, (se[0] + 1.0, se[1] - 0.2), (se[0], se[1]), ambient, 0.0, "exit sign 1m")
    me = light_emit_pos(mon)
    measure(m, "upper", props, (me[0] + 1.0, me[1]), (me[0], me[1]), ambient, 0.0, "monitor 1m")
    measure(m, "upper", props, (me[0] - 0.8, me[1]), (me[0], me[1]), ambient, 0.0, "behind monitor 0.8m")
    measure(m, "upper", props, (5.5, 7.0), (5.5, 4.5), ambient, 0.0, "between two lamps", also_toward=(5.5, 9.5))
    measure(m, "upper", props, (8.0, 4.5), (lp[0], lp[1]), ambient, 0.0, "lamp 2.5m, wall 2m behind")

    ym = blank_maze(20, 14)
    ym.zones = [{"rect": (11, 3, 18, 10)}]
    for x in range(11, 18):
        ym.grid[3][x] = S.WALL_SHED
        ym.grid[9][x] = S.WALL_SHED
    for y in range(3, 10):
        ym.grid[y][11] = S.WALL_SHED
        ym.grid[y][17] = S.WALL_SHED
    ym.grid[6][11] = S.FLOOR
    probe.build(ym, theme="yard")
    toward = (MOON_TOWARD[0], MOON_TOWARD[1])
    for pt, label in (((5.5, 6.5), "moon, open yard"), ((14.5, 6.5), "moon, inside shed")):
        measure(ym, "yard", [], pt, (pt[0] + toward[0], pt[1] + toward[1]), 0.24, 0.25, label)
    worst = max(abs(x[3]) for x in results)
    for label, cpu, gpu, diff in results:
        report(f"gameplay_light/{label}", cpu=cpu, gpu=gpu, diff=diff)
    report("gameplay_light", points=len(results), max_abs_diff=round(worst, 3), within_0_02=worst <= 0.02)


@scene
def windows(probe):
    from game.props import Prop
    from game import renderer3d as R
    r = probe.renderer
    keep_res = (r.low_w, r.low_h, r.snap_res)
    r.set_resolution(640, 360, keep_res[2])
    glass = R.GLASS_MATERIAL
    keep_glass = (glass.alpha, glass.specular)
    try:
        m = blank_maze(16, 12)
        for x in range(1, 15):
            m.grid[5][x] = S.WALL_CONCRETE
        for x in (4, 5, 6):
            m.grid[5][x] = S.WALL_WINDOW
        m.grid[5][11] = S.FLOOR
        probe.build(m)
        lamp_a = Prop("lamp_desk", 4.2, 9.8, facing=0.0)
        lamp_a.z0 = 0.5
        lamp_b = Prop("lamp_desk", 10.2, 9.8, facing=0.0)
        lamp_b.z0 = 0.5
        lamp_a.bob_phase = lamp_b.bob_phase = 0.0
        props = [lamp_a, lamp_b]
        light = dict(fog_color=(0, 0, 0), fog_dist=500.0, ambient=0.055, moon_strength=0.0)
        pl = probe.player
        pl.flashlight_on = False

        def luma2(a):
            return a.mean(axis=2)

        arr = probe.shot("windows_frame", (5.5, 4.2), math.pi / 2, -0.35, props=props, **light)
        h, w = arr.shape[:2]
        band = luma2(arr)[int(h * 0.55):int(h * 0.8), int(w * 0.3):int(w * 0.7)]
        report("windows/frame", material_texture=R.wall_material(S.WALL_WINDOW).texture,
               frame_std=round(float(band.std()), 2), frame_mean=round(float(band.mean()), 2))

        look = dict(props=props, **light)
        through_glass = probe.shot("windows_through_glass", (5.5, 3.2), math.pi / 2, 0.04, **look)
        through_door = probe.shot("windows_through_door", (11.5, 3.2), math.pi / 2, 0.04, **look)
        centre = (slice(int(h * 0.47), int(h * 0.53)), slice(int(w * 0.47), int(w * 0.53)))
        g = float(luma2(through_glass)[centre].mean())
        d = float(luma2(through_door)[centre].mean())
        report("windows/far_wall", through_glass=round(g, 2), through_doorway=round(d, 2), ratio=round(g / max(d, 1e-6), 3))

        pl.flashlight_on = True
        pl.active_held_item = "flashlight"
        pl.equip_t = 1.0
        arr = probe.shot("windows_torch_blank", (5.5, 5.0 - S.PLAYER_RADIUS), math.pi / 2, 0.0, **light)
        pane = luma2(arr)[int(h * 0.3):int(h * 0.6), int(w * 0.35):int(w * 0.65)]
        report("windows/torch_point_blank", saturated_pct=round(float((pane >= 250).mean() * 100), 2),
               pane_mean=round(float(pane.mean()), 2))
        pl.flashlight_on = False
        pl.active_held_item = None
        pl.equip_t = 0.0

        glass.specular = 0.0
        view = ((5.5, 3.4), math.pi / 2, 0.04)
        glass.alpha = 1.0
        opaque = luma2(probe.shot("windows_pane_opaque", *view, save=False, **look))
        glass.alpha = 0.0
        behind = luma2(probe.shot("windows_pane_none", *view, save=False, **look))
        glass.alpha = keep_glass[0]
        actual = luma2(probe.shot("windows_pane_actual", *view, save=False, **look))
        expect = glass.alpha * opaque + (1.0 - glass.alpha) * behind
        err = np.abs(actual - expect)[centre]
        report("windows/single_layer", alpha=glass.alpha, mean_abs_err=round(float(err.mean()), 2),
               max_abs_err=round(float(err.max()), 2))
        pane_px = np.abs(opaque - behind) > 2.0
        err = np.abs(actual - expect)[pane_px]
        report("windows/behind_pane", pane_px=int(pane_px.sum()), mean_abs_err=round(float(err.mean()), 2),
               px_err_over_4=int((err > 4.0).sum()))
        glass.specular = keep_glass[1]

        pl.flashlight_on = True
        pl.active_held_item = "flashlight"
        pl.equip_t = 1.0
        for label, pos, yaw, pitch in (("head_on", (5.5, 3.6), math.pi / 2, 0.0),
                                       ("grazing_up", (5.5, 4.55), math.pi / 2, 0.55)):
            with_pane = luma2(probe.shot(f"windows_wash_{label}", pos, yaw, pitch, **look))
            glass.alpha = 0.0
            glass.specular = 0.0
            bare = luma2(probe.shot(f"windows_wash_{label}_bare", pos, yaw, pitch, save=False, **look))
            glass.alpha, glass.specular = keep_glass
            changed = np.abs(with_pane - bare) > 1.0
            report(f"windows/wash_{label}", pane_px=int(changed.sum()),
                   mean_added=round(float((with_pane - bare)[changed].mean()), 2) if changed.any() else 0.0,
                   bare_mean_there=round(float(bare[changed].mean()), 2) if changed.any() else 0.0)
        pl.flashlight_on = False
        pl.active_held_item = None
        pl.equip_t = 0.0
    finally:
        glass.alpha, glass.specular = keep_glass
        r.set_resolution(*keep_res)


def _umbra_mask(probe, pts, light_pos, emitter, texel_angle=0.0):
    maze = probe.maze
    grid = np.array([[maze.grid[y][x] != S.FLOOR and maze.grid[y][x] not in S.SEE_THROUGH_WALLS for x in range(maze.w)]
                     for y in range(maze.h)], dtype=bool)
    light = np.array(light_pos, dtype=float)
    dist = np.linalg.norm(pts - light, axis=1)
    radius = emitter + 2.0 * texel_angle * dist
    dirs = [(0, 0, 0)]
    for v in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
        dirs += [v, tuple(-c for c in v)]
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz in (-1, 1):
                dirs.append((sx / 3 ** 0.5, sy / 3 ** 0.5, sz / 3 ** 0.5))
    umbra = np.ones(len(pts), dtype=bool)
    for d in dirs:
        src = light[None, :] + np.array(d)[None, :] * radius[:, None]
        seg = pts - src
        length = np.linalg.norm(seg, axis=1)
        steps = int(np.ceil(length.max() / 0.01))
        hit = np.zeros(len(pts), dtype=bool)
        for i in range(1, steps):
            t = np.minimum(i * 0.01 / np.maximum(length, 1e-6), 1.0)
            q = src + seg * t[:, None]
            inside = (t < 1.0) & (q[:, 2] > 0.0) & (q[:, 2] < S.WALL_HEIGHT)
            cx = np.clip(np.floor(q[:, 0]).astype(int), 0, maze.w - 1)
            cy = np.clip(np.floor(q[:, 1]).astype(int), 0, maze.h - 1)
            hit |= inside & grid[cy, cx]
        umbra &= hit
        if not umbra.any():
            break
    return umbra


def _view_leak(probe, lamp, eye, yaw, pitch, light, save_name=None):
    from game import gl_math as gm
    from game.lighting import light_emit_pos, PROP_EMITTER_RADIUS, POINT_SHADOW_FOV_DEGREES
    r = probe.renderer
    w, h = r.low_w, r.low_h
    cam = dict(camera_override=(eye, yaw, pitch, 70.0))
    lit = probe.shot(save_name or "x", eye[:2], 0.0, 0.0, props=[lamp], save=save_name is not None, **cam, **light)
    depth = np.frombuffer(r.fbo.read(components=1, attachment=-1, dtype="f4"), dtype="f4").reshape(h, w)
    orig = r._upload_lights
    try:
        r._upload_lights = lambda lights: orig([])
        dark = probe.shot("x", eye[:2], 0.0, 0.0, props=[lamp], save=False, **cam, **light)
    finally:
        r._upload_lights = orig
    added = np.clip(lit - dark, 0.0, None).mean(axis=2)
    ys, xs = np.nonzero((added > 2.0) & (depth < 1.0))
    if not len(xs):
        return 0, 0.0, 0
    view = gm.view_matrix(eye, yaw, pitch, 0.0)
    proj = gm.perspective(math.radians(70.0), w / h, 0.03, 130.0)
    inv = np.linalg.inv(proj @ view)
    ndc = np.stack([(xs + 0.5) / w * 2 - 1, (ys + 0.5) / h * 2 - 1, depth[ys, xs] * 2 - 1, np.ones(len(xs))], axis=1)
    world = ndc @ inv.T
    world = world[:, :3] / world[:, 3:4]
    toward = np.array(eye) - world
    world += toward / np.linalg.norm(toward, axis=1)[:, None] * 0.02
    texel_angle = 2.0 * math.tan(math.radians(POINT_SHADOW_FOV_DEGREES) / 2.0) / r.POINT_SHADOW_RES
    umbra = _umbra_mask(probe, world, light_emit_pos(lamp), PROP_EMITTER_RADIUS.get(lamp.kind, 0.06), texel_angle)
    return int(umbra.sum()), float(added[ys, xs][umbra].sum()), int(len(xs))


@scene
def seam_leak(probe):
    from game.props import Prop
    from game.lighting import light_emit_pos
    r = probe.renderer
    keep_res = (r.low_w, r.low_h, r.snap_res)
    r.set_resolution(320, 180, 100000.0)
    try:
        m = blank_maze(18, 12)
        for x in range(3, 9):
            m.grid[6][x] = S.WALL_CONCRETE
        probe.build(m)
        probe.player.flashlight_on = False
        light = dict(fog_color=(0, 0, 0), fog_dist=500.0, ambient=0.0, moon_strength=0.0)
        sign = Prop("sign_exit", 7.5, 7.05, facing=math.pi / 2)
        sconce = Prop("wall_sconce", 5.5, 7.08, facing=math.pi / 2)
        desk = Prop("lamp_desk", 5.5, 7.5, facing=0.0)
        desk.z0 = 0.75
        total_px = 0
        down, up = -math.pi / 2 + 0.001, math.pi / 2 - 0.001
        for name, lamp in (("sign", sign), ("sconce", sconce), ("desk_lamp", desk)):
            lamp.bob_phase = 0.0
            emit = light_emit_pos(lamp)
            views = (("behind_floor", (lamp.x, 5.3, 1.12), math.pi / 2, down),
                     ("behind_ceiling", (lamp.x, 5.3, 0.25), math.pi / 2, up),
                     ("end_floor", (8.6, 5.3, 1.12), math.pi / 2, down),
                     ("end_ceiling", (8.6, 5.3, 0.25), math.pi / 2, up),
                     ("corner", (10.6, 4.4, 0.62), math.atan2(6.0 - 4.4, 9.0 - 10.6), 0.0),
                     ("along_wall", (10.2, 5.4, 0.62), math.pi + 0.12, 0.0))
            for view_name, eye, yaw, pitch in views:
                leak_px, leak_sum, lit_px = _view_leak(probe, lamp, eye, yaw, pitch, light,
                                                       f"seam_leak_{name}_{view_name}")
                total_px += leak_px
                report(f"seam_leak/{name}_{view_name}", lit_px=lit_px, leak_px=leak_px, leak_sum=round(leak_sum, 1))
        report("seam_leak", total_leak_px=total_px)
    finally:
        r.set_resolution(*keep_res)


@scene
def seam_census(probe):
    import random as _random
    from game.maze import Maze
    from game.props import populate_level
    r = probe.renderer
    keep_res = (r.low_w, r.low_h, r.snap_res)
    r.set_resolution(320, 180, 100000.0)
    light = dict(fog_color=(0, 0, 0), fog_dist=500.0, ambient=0.0, moon_strength=0.0)
    probe.player.flashlight_on = False
    total_px, total_sum, views_with_leak, views = 0, 0.0, 0, 0
    worst = (0, None)
    try:
        for spec in S.FLOOR_SPECS[:2]:
            for seed in (11, 23):
                maze = Maze(seed=seed, wall_bias=spec["wall_bias"], template_floor=spec.get("floor_theme"))
                props = populate_level(maze, spec, _random.Random(seed ^ 0x5EED))
                props = props[0] if isinstance(props, tuple) else props
                lamps = [p for p in props if getattr(p, "light_radius", None) and not getattr(p, "broken", False)]
                probe.build(maze)
                rng = _random.Random(seed)
                for lamp in lamps[:10]:
                    lamp.bob_phase = 0.0
                    cells = [(x + 0.5, y + 0.5) for y in range(maze.h) for x in range(maze.w)
                             if maze.grid[y][x] == S.FLOOR
                             and 0.8 < math.hypot(x + 0.5 - lamp.x, y + 0.5 - lamp.y) < lamp.light_radius + 0.8
                             and not maze.has_line_of_sight(lamp.x, lamp.y, x + 0.5, y + 0.5)]
                    rng.shuffle(cells)
                    for cx, cy in cells[:3]:
                        yaw = math.atan2(lamp.y - cy, lamp.x - cx)
                        for pitch in (0.0, -0.45, 0.45):
                            px, s_ = _view_leak(probe, lamp, (cx, cy, 0.62), yaw, pitch, light)[:2]
                            views += 1
                            total_px += px
                            total_sum += s_
                            views_with_leak += px > 0
                            if px > worst[0]:
                                worst = (px, (spec["key"], seed, lamp.kind, round(lamp.x, 2), round(lamp.y, 2),
                                              round(cx, 2), round(cy, 2), round(pitch, 2)))
        report("seam_census", views=views, views_with_leak=views_with_leak, leak_px=total_px,
               leak_sum=round(total_sum, 1), worst=worst)
    finally:
        r.set_resolution(*keep_res)


@scene
def torch_reach(probe):
    from game import lighting as L
    r = probe.renderer
    keep_res = (r.low_w, r.low_h, r.snap_res)
    r.set_resolution(320, 180, keep_res[2])
    try:
        m = blank_maze(28, 5)
        probe.build(m)
        pl = probe.player
        pl.flashlight_on = True
        pl.active_held_item = None
        pl.equip_t = 0.0
        far_x = 27.0
        dists = (1.5, 3.0, 5.0, 7.0, 8.0, 9.0, 10.0, 11.0)
        for label, light in (("no_fog", dict(fog_color=(0, 0, 0), fog_dist=500.0, ambient=0.0, moon_strength=0.0)),
                             ("floor0_fog", dict(fog_color=S.COL_FOG, fog_dist=S.FLOOR_SPECS[0]["fog_dist"],
                                                 ambient=0.0, moon_strength=0.0))):
            vals = []
            for d in dists:
                arr = probe.shot(f"torch_reach_{label}_{d}", (far_x - d, 2.5), 0.0, 0.0, save=False, **light)
                h, w = arr.shape[:2]
                vals.append(float(arr[int(h * 0.45):int(h * 0.55), int(w * 0.45):int(w * 0.55)].mean()))
            report(f"torch_reach/{label}", **{f"d{d}": round(v, 1) for d, v in zip(dists, vals)})
            if label == "no_fog":
                beam = L.flashlight_light((0, 0, 0), (1, 0, 0), 1.0, 0.0)
                ref = L.light_falloff(dists[0], beam.radius, beam.falloff)
                report("torch_reach/curve", **{f"d{d}": f"{v / vals[0]:.2f}~{L.light_falloff(d, beam.radius, beam.falloff) / ref:.2f}"
                                               for d, v in zip(dists, vals)})
        pl.flashlight_on = False
    finally:
        r.set_resolution(*keep_res)


def main():
    if os.environ.get("PYTHONHASHSEED") != "0":
        env = dict(os.environ, PYTHONHASHSEED="0")
        os.execve(sys.executable, [sys.executable] + sys.argv, env)
    ap = argparse.ArgumentParser()
    ap.add_argument("scenes", nargs="*", help="scene names (default: all)")
    ap.add_argument("--out", default=OUT_DIR)
    args = ap.parse_args()
    probe = Probe(args.out)
    print(f"GPU: {probe.ctx.info['GL_RENDERER']}")
    print(f"internal render size: {probe.renderer.color_tex.size}")
    for name in (args.scenes or SCENES):
        if name not in SCENES:
            print(f"  (no scene named {name!r}; have: {', '.join(SCENES)})")
            continue
        SCENES[name](probe)
    print(f"PNGs -> {args.out}")


if __name__ == "__main__":
    main()
