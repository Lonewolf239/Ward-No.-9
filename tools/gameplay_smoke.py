import math
import os
import random
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SDL_VIDEODRIVER", "x11")

import pygame

from game import settings as S
from game.app import App
from tools.sandbox_settings import redirect as _sandbox_settings
_sandbox_settings()


def main():
    frames_per_floor = int(sys.argv[1]) if len(sys.argv) > 1 else 80
    app = App()
    pygame.display.flip = lambda: None
    app._sync_window_size = lambda: None
    app.window_size = (S.SCREEN_W, S.SCREEN_H)
    app.new_game()
    app._start_playing()
    rng = random.Random(7)
    errors, frames = [], 0
    for floor in range(len(S.FLOOR_SPECS)):
        if floor:
            app._load_floor(floor)
        cells = [(x + 0.5, y + 0.5) for y in range(app.maze.h) for x in range(app.maze.w)
                 if app.maze.grid[y][x] == S.FLOOR]
        for i in range(frames_per_floor):
            p = app.player
            p.x, p.y = rng.choice(cells)
            p.peek_x, p.peek_y = p.x, p.y
            p.angle = rng.uniform(-math.pi, math.pi)
            p.pitch = rng.uniform(-0.5, 0.5)
            app._trip_cam_yaw = p.angle
            app._trip_cam_pitch = p.pitch
            app._trip_prev_player_angle = p.angle
            app._trip_target_unwrapped = p.angle
            app._trip_follower_unwrapped = p.angle
            p.flashlight_on = (i % 3 != 0)
            p.battery = rng.uniform(5.0, 100.0)
            if app.doors and i % 5 == 0:
                d = rng.choice(app.doors)
                d.is_open = not getattr(d, "is_open", False)
            if i % 7 == 0:
                for key, opts in S.GFX_SETTING_OPTIONS.items():
                    app.settings[key] = rng.choice(opts)
                app._apply_graphics_settings()
            if i % 11 == 0:
                app._apply_quality_preset(rng.choice(list(S.QUALITY_PRESETS)))
            try:
                app.player.update_held_item(1 / 60)
                app.draw()
                frames += 1
            except Exception:
                errors.append(traceback.format_exc())
    print(f"ran {frames} frames across {len(S.FLOOR_SPECS)} floors, errors: {len(errors)}")
    for e in errors[:3]:
        print(e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
