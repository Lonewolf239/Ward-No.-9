import argparse
import math
import os
import random
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_SPOTS = ["38.15,36.27,148", "35.76,36.68,347"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=453179563)
    ap.add_argument("--floor", type=int, default=0)
    ap.add_argument("--frames", type=int, default=90)
    ap.add_argument("--spot", action="append")
    ap.add_argument("--walk", action="store_true", help="run the real game loop along a walk between the spots")
    ap.add_argument("--settings", default=None,
                    help="a settings.json to measure with (default: a copy of the player's own). "
                         "Pass a fixed one to compare two runs: the live file changes when anyone plays")
    ap.add_argument("--anomaly", type=int, default=None,
                    help="the run's anomaly roll (default: settings.ANOMALY_DEFAULT_STAGE)")
    args = ap.parse_args()
    spots = [tuple(float(v) for v in s.split(",")) for s in (args.spot or DEFAULT_SPOTS)]

    import pygame
    import numpy as np
    import game.app as A
    from game import settings as S

    tmp = tempfile.mkdtemp()
    src = args.settings or (A.SETTINGS_PATH if A.SETTINGS_PATH.exists() else None)
    if src:
        shutil.copy(src, os.path.join(tmp, "settings.json"))
    A.SETTINGS_PATH = A.Path(os.path.join(tmp, "settings.json"))
    app = A.App()
    pygame.display.flip = lambda: None
    app._sync_window_size = lambda: None
    app.window_size = (S.SCREEN_W, S.SCREEN_H)
    r = app.renderer
    print(f"settings: render {app.settings.get('gfx_render_scale')} shadows {app.settings.get('gfx_shadow_res')} "
          f"penumbra {app.settings.get('gfx_shadow_penumbra')} internal {r.low_w}x{r.low_h}")

    random.seed(args.seed)
    roll = getattr(S, "ANOMALY_DEFAULT_STAGE", 2) if args.anomaly is None else args.anomaly
    real_randrange = random.randrange
    random.randrange = lambda *a, **k: args.seed
    try:
        app.new_game()
        app.anomaly_roll = roll
        app._start_playing()
        app._load_floor(args.floor)
    finally:
        random.randrange = real_randrange
    print(f"floor {args.floor} seed {args.seed} anomaly roll {roll} -> stage {getattr(app, 'anomaly_stage', None)}, "
          f"{len(app.props)} props")

    timers = {}

    def timed(name, fn):
        def wrapper(*a, **k):
            t0 = time.perf_counter()
            try:
                return fn(*a, **k)
            finally:
                timers[name] = timers.get(name, 0.0) + (time.perf_counter() - t0) * 1000.0
        return wrapper

    def gpu_synced(name, fn):
        def wrapper(*a, **k):
            app.ctx.finish()
            t0 = time.perf_counter()
            try:
                return fn(*a, **k)
            finally:
                app.ctx.finish()
                timers[name] = timers.get(name, 0.0) + (time.perf_counter() - t0) * 1000.0
        return wrapper

    r.render = gpu_synced("render_gpu", r.render)
    r.composite = gpu_synced("composite_gpu", r.composite)
    app._draw_hud = timed("hud_draw", app._draw_hud)
    app._draw_debug_overlay = timed("debug_overlay", app._draw_debug_overlay)
    _tostring = pygame.image.tostring
    pygame.image.tostring = timed("hud_tostring", _tostring)
    r._render_flash_face = timed("flash_faces", r._render_flash_face)
    r._render_omni_faces = timed("omni_faces", r._render_omni_faces)
    r._occluder_buckets = timed("occluder_hash", r._occluder_buckets)
    r._render_moon_face = timed("moon_face", r._render_moon_face)

    p = app.player
    p.flashlight_on = True
    p.active_held_item = "flashlight"
    p.equip_t = 1.0
    app.spec = dict(app.spec)
    app.spec["no_threat"] = True
    if args.walk:
        app.spec["no_threat"] = False
        route = spots + spots[::-1]
        waypoints = []
        for (x0, y0, _), (x1, y1, _) in zip(route, route[1:]):
            n = max(1, int(math.hypot(x1 - x0, y1 - y0) / 0.05))
            waypoints += [(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n, math.atan2(y1 - y0, x1 - x0)) for i in range(n)]
        waypoints = (waypoints * (1 + args.frames * 3 // max(1, len(waypoints))))[:args.frames * 3]
        rows = []
        warmup = 10
        for x, y, ang in waypoints[-warmup:] + waypoints:
            timers.clear()
            p.x, p.y, p.peek_x, p.peek_y, p.angle = x, y, x, y, ang
            for a in ("_trip_cam_yaw", "_trip_prev_player_angle", "_trip_target_unwrapped", "_trip_follower_unwrapped"):
                setattr(app, a, ang)
            t0 = time.perf_counter()
            app.update(1 / 60)
            p.x, p.y, p.peek_x, p.peek_y = x, y, x, y
            t1 = time.perf_counter()
            app.draw()
            app.ctx.finish()
            t2 = time.perf_counter()
            rows.append(dict(update=(t1 - t0) * 1000, draw=(t2 - t1) * 1000, rebuilt=len(r.last_shadow_rebuilds),
                             **r.last_perf, **timers))
        rows = rows[warmup:]
        for k in ("update", "draw", "render_gpu", "composite_gpu", "hud_draw", "debug_overlay", "hud_tostring",
                  "shadows", "props", "monster", "flash_faces", "omni_faces", "occluder_hash", "moon_face", "rebuilt"):
            vals = np.array([row.get(k, 0.0) for row in rows])
            print(f"   walk {k:13s} median {np.median(vals):6.2f}  p90 {np.percentile(vals, 90):6.2f}  max {vals.max():6.2f}")
        total = np.array([row["update"] + row["draw"] for row in rows])
        print(f"   walk frame (update+draw) median {np.median(total):.2f} ms ({1000 / np.median(total):.0f} fps), "
              f"mean {total.mean():.2f} ms ({1000 / total.mean():.0f} fps)")
        shutil.rmtree(tmp, ignore_errors=True)
        return
    for x, y, deg in spots:
        ang = math.radians(deg)
        app.hint_text = ""
        p.x, p.y = x, y
        p.peek_x, p.peek_y = x, y
        p.angle, p.pitch = ang, 0.0
        for a in ("_trip_cam_yaw", "_trip_prev_player_angle", "_trip_target_unwrapped", "_trip_follower_unwrapped"):
            setattr(app, a, ang)
        app._trip_cam_pitch = 0.0
        for _ in range(15):
            p.update_held_item(1 / 60)
            app.draw()
        app.ctx.finish()
        frames, parts, shadow_parts, counts = [], [], [], []
        for _ in range(args.frames):
            timers.clear()
            t0 = time.perf_counter()
            app.draw()
            app.ctx.finish()
            frames.append((time.perf_counter() - t0) * 1000.0)
            parts.append(dict(r.last_perf))
            counts.append((r.last_drawn_prop_count, getattr(r, "last_light_count", 0),
                           len(app.props)))
            shadow_parts.append(dict(timers))
        med = lambda key, rows: float(np.median([row.get(key, 0.0) for row in rows]))
        print(f"spot ({x:.2f}, {y:.2f}, {deg:.0f} deg): frame {np.median(frames):.2f} ms "
              f"({1000.0 / np.median(frames):.0f} fps)")
        print("   cpu: " + "  ".join(f"{k} {med(k, parts):.2f}" for k in parts[0]))
        print(f"   counts: props drawn {int(np.median([c[0] for c in counts]))} "
              f"of {counts[0][2]} on the floor; lights uploaded "
              f"{int(np.median([c[1] for c in counts]))}")
        print("   shadow phase: " + "  ".join(f"{k} {med(k, shadow_parts):.2f}"
                                             for k in ("flash_faces", "omni_faces", "occluder_hash", "moon_face")))
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
