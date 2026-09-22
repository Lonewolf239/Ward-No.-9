import collections
import io
import json
import math
import os
import random
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SDL_VIDEODRIVER", "offscreen")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import game.app as A
from game import settings as S
from game.props import PROP_DEFS

def _looks_outside(maze, wx, wy):
    return any(0 <= wx + dx < maze.w and 0 <= wy + dy < maze.h
               and maze.grid[wy + dy][wx + dx] in (S.WALL_OUTDOOR, S.WALL_FOREST)
               for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))


RUNS = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8
INTO_WALL = 0.06

tmp = tempfile.mkdtemp()
if A.SETTINGS_PATH.exists():
    shutil.copy(A.SETTINGS_PATH, os.path.join(tmp, "settings.json"))
A.SETTINGS_PATH = A.Path(os.path.join(tmp, "settings.json"))
_sp = os.path.join(tmp, "settings.json")
_sd = json.load(io.open(_sp, encoding="utf-8")) if os.path.exists(_sp) else {}
_sd["fullscreen"] = False
io.open(_sp, "w", encoding="utf-8").write(json.dumps(_sd))
pygame.display.flip = lambda: None

random.seed(3)
app = A.App()
app._sync_window_size = lambda: None
if app.state == "splash":
    app._finish_splash()
app.new_game()
app._start_playing()

problems = collections.Counter()
examples = collections.defaultdict(list)


def solid(maze, x, y):
    if not (0 <= x < maze.w and 0 <= y < maze.h):
        return True
    return maze.grid[y][x] not in (S.FLOOR, S.WALL_OUTDOOR)


for floor_idx in (0, 1, 2):
    for run in range(RUNS):
        random.seed(5000 + floor_idx * 100 + run)
        app._load_floor(floor_idx)
        app._begin_playing()
        maze = app.maze
        doors = {(int(d.x), int(d.y)) for d in app.doors}
        windows = [(x, y) for y in range(maze.h) for x in range(maze.w)
                   if maze.grid[y][x] == S.WALL_WINDOW]
        see_through = [(x, y) for y in range(maze.h) for x in range(maze.w)
                       if maze.grid[y][x] in S.SEE_THROUGH_WALLS]
        occupied = collections.defaultdict(list)
        for p in app.props:
            occupied[(int(p.x), int(p.y))].append(p)

        for p in app.props:
            defs = PROP_DEFS.get(p.kind, {})
            if not defs.get("solid") and not defs.get("wall_mounted"):
                continue
            hw, hd = defs.get("hw", 0.0), defs.get("hd", 0.0)
            c, s = math.cos(p.facing), math.sin(p.facing)
            deep = 0.0
            for ox, oy in ((hd, hw), (hd, -hw), (-hd, hw), (-hd, -hw)):
                wx = p.x + ox * c - oy * s
                wy = p.y + ox * s + oy * c
                if solid(maze, int(wx), int(wy)):
                    if (0 <= int(wx) < maze.w and 0 <= int(wy) < maze.h
                            and maze.grid[int(wy)][int(wx)] == S.WALL_BARS
                            and not maze.is_wall(wx, wy)):
                        continue
                    cx, cy = int(wx) + 0.5, int(wy) + 0.5
                    deep = max(deep, 0.5 - max(abs(wx - cx), abs(wy - cy)))
            if deep > INTO_WALL:
                problems["prop sunk into a wall"] += 1
                if len(examples["prop sunk into a wall"]) < 6:
                    examples["prop sunk into a wall"].append(
                        "%s at (%.1f, %.1f) reaches %.2f into a wall, floor%d run %d"
                        % (p.kind, p.x, p.y, deep, floor_idx, run))
            if (int(p.x), int(p.y)) in doors and p.kind not in ("note_flat",):
                problems["prop standing in a doorway"] += 1
                if len(examples["prop standing in a doorway"]) < 6:
                    examples["prop standing in a doorway"].append(
                        "%s in the doorway at (%d, %d), floor%d run %d"
                        % (p.kind, int(p.x), int(p.y), floor_idx, run))

        if floor_idx == 2:
            continue
        for (wx, wy) in windows:
            sides = [(1, 0), (-1, 0), (0, 1), (0, -1)]
            open_sides = [d for d in sides if not solid(maze, wx + d[0], wy + d[1])]
            looks_out = any(0 <= wx + d[0] < maze.w and 0 <= wy + d[1] < maze.h
                            and maze.grid[wy + d[1]][wx + d[0]] in (S.WALL_FOREST, S.WALL_OUTDOOR)
                            for d in sides)
            if len(open_sides) < 2 and not (open_sides and looks_out):
                problems["window with nothing to see through it"] += 1
                if len(examples["window with nothing to see through it"]) < 6:
                    examples["window with nothing to see through it"].append(
                        "window at (%d, %d) has %d open sides, floor%d run %d"
                        % (wx, wy, len(open_sides), floor_idx, run))
            del open_sides

        for (wx, wy) in see_through:
            sides = [(1, 0), (-1, 0), (0, 1), (0, -1)]
            for d in [q for q in sides if not solid(maze, wx + q[0], wy + q[1])]:
                for p in occupied.get((wx + d[0], wy + d[1]), ()):
                    defs = PROP_DEFS.get(p.kind, {})
                    cell = (int(p.x), int(p.y))
                    if defs.get("wall_mounted"):
                        mx = cell[0] - round(math.cos(p.facing))
                        my = cell[1] - round(math.sin(p.facing))
                        if (mx, my) != (wx, wy):
                            continue
                        problems["hung on the glass"] += 1
                        if len(examples["hung on the glass"]) < 6:
                            what = "window" if maze.grid[wy][wx] == S.WALL_WINDOW else "bars"
                            examples["hung on the glass"].append(
                                "%s bolted to the %s at (%d, %d), floor%d run %d"
                                % (p.kind, what, wx, wy, floor_idx, run))
                    elif (defs.get("solid") and maze.grid[wy][wx] == S.WALL_WINDOW
                          and not _looks_outside(maze, wx, wy)):
                        problems["standing in front of a window"] += 1
                        if len(examples["standing in front of a window"]) < 6:
                            examples["standing in front of a window"].append(
                                "%s in front of the window at (%d, %d), floor%d run %d"
                                % (p.kind, wx, wy, floor_idx, run))

total_runs = RUNS * 3
print("%d generated floors (%d per theme)" % (total_runs, RUNS))
for name in ("prop sunk into a wall", "prop standing in a doorway",
             "hung on the glass", "standing in front of a window",
             "window with nothing to see through it"):
    print("   %-38s %4d  (%.2f per floor)"
          % (name, problems[name], problems[name] / float(total_runs)))
    for line in examples[name][:4]:
        print("        " + line)
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if problems else 0)
