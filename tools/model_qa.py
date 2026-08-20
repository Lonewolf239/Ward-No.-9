import os, sys, math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pygame
from game import settings as S
from game import gl_math as gm
from game.app import App
from game.props import PROP_DEFS, Prop, make_prop, _wall_cells_around, _wall_mount_position, Door
from game.renderer3d import EYE_HEIGHT

app = App()
app._start_debug_level()
maze = app.maze
app.player.flashlight_on = True
app.player.battery = 100.0

outdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_qa_out")
os.makedirs(outdir, exist_ok=True)

WALL_CELL = (maze.w // 2, 1)
FLOOR_CELL = (maze.w // 2, 4)

RULER_POLE_COLOR = (0.5, 0.5, 0.55)
RULER_EYE_COLOR = (1.0, 0.85, 0.15)
RULER_CEIL_COLOR = (0.25, 0.85, 1.0)


def build_scene(kind):
    spec = PROP_DEFS[kind]
    if spec["wall_mounted"]:
        candidates = _wall_cells_around(maze, WALL_CELL)
        boundary, facing, fc = candidates[0]
        x, y = _wall_mount_position(boundary, facing, spec["hd"])
        prop = Prop(kind, x, y, facing=facing)
    else:
        prop = make_prop(kind, FLOOR_CELL, facing=0.6)
    return prop


def camera_for(prop, wall_mounted):
    span = max(prop.hw, prop.hd, prop.height) if hasattr(prop, "height") else 1.0
    dist = 0.95 + span * 0.95
    if wall_mounted:
        ang = prop.facing
    else:
        ang = prop.facing + math.radians(200)
    cx = prop.x + math.cos(ang) * dist
    cy = prop.y + math.sin(ang) * dist
    look = math.atan2(prop.y - cy, prop.x - cx)
    return cx, cy, look


def ruler_position(prop, cx, cy):
    to_prop = math.atan2(prop.y - cy, prop.x - cx)
    perp = to_prop + math.pi / 2
    span = max(getattr(prop, "hw", 0.3), getattr(prop, "hd", 0.3))
    offset = span + 0.45
    return prop.x + math.cos(perp) * offset, prop.y + math.sin(perp) * offset


def draw_scale_ruler(renderer, x, y):
    pole_h = S.WALL_HEIGHT
    renderer._draw_box(
        gm.translate(x, y, 0.0) @ gm.scale(0.025, 0.025, pole_h),
        RULER_POLE_COLOR, emissive=0.5, vao=renderer.box_vao,
    )
    renderer._draw_box(
        gm.translate(x, y, EYE_HEIGHT - 0.015) @ gm.scale(0.11, 0.11, 0.03),
        RULER_EYE_COLOR, emissive=1.0, vao=renderer.box_vao,
    )
    renderer._draw_box(
        gm.translate(x, y, pole_h - 0.03) @ gm.scale(0.11, 0.11, 0.03),
        RULER_CEIL_COLOR, emissive=1.0, vao=renderer.box_vao,
    )


def main():
    kinds = sorted(PROP_DEFS.keys()) + ["locker", "door", "door_broken", "tree"]
    for kind in kinds:
        if kind == "door_broken":
            prop = Door(FLOOR_CELL[0] + 0.5, FLOOR_CELL[1] + 0.5, 0.0)
            prop.break_open()
            wall_mounted = False
        elif kind == "door":
            candidates = _wall_cells_around(maze, WALL_CELL)
            boundary, facing, fc = candidates[0]
            prop = Door(WALL_CELL[0] + 0.5, WALL_CELL[1] + 1.5, facing)
            wall_mounted = False
        elif kind == "locker":
            candidates = _wall_cells_around(maze, WALL_CELL)
            boundary, facing, fc = candidates[0]
            x, y = _wall_mount_position(boundary, facing, PROP_DEFS["locker"]["hd"])
            prop = Prop("locker", x, y, facing=facing)
            wall_mounted = True
        elif kind == "tree":
            prop = make_prop("tree", FLOOR_CELL, facing=0.0)
            wall_mounted = False
        else:
            prop = build_scene(kind)
            wall_mounted = PROP_DEFS[kind]["wall_mounted"]

        cx, cy, look = camera_for(prop, wall_mounted)
        app.player.x, app.player.y = cx, cy
        app.player.angle = look
        app.player.pitch = -0.10 if wall_mounted else -0.24

        app.renderer.render(
            maze, app.player, app.monster, [prop], dread=0.0, t=0.0,
            fog_color=(40, 40, 40), fog_dist=60.0, ambient=0.75, moon_strength=0.0,
            qa_mode=True,
        )
        rx, ry = ruler_position(prop, cx, cy)
        draw_scale_ruler(app.renderer, rx, ry)

        data = app.renderer.color_tex.read()
        img = pygame.image.frombuffer(data, app.renderer.color_tex.size, "RGB")
        img = pygame.transform.flip(img, False, True)
        img = pygame.transform.scale(img, (img.get_width() * 6, img.get_height() * 6))
        pygame.image.save(img, os.path.join(outdir, f"{kind}.png"))
        print("saved", kind)

    print(f"DONE: {len(kinds)} kinds -> {outdir}")
    print("Ruler: yellow tick = player eye height, cyan tick = ceiling (S.WALL_HEIGHT)")


if __name__ == "__main__":
    main()
