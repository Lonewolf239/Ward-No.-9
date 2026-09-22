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
from game.props import _edge_physically_clear, cells_no_body_fits
from game import settings as S
from game.props import PROP_DEFS

RUNS = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8

tmp = tempfile.mkdtemp()
if A.SETTINGS_PATH.exists():
    shutil.copy(A.SETTINGS_PATH, os.path.join(tmp, "settings.json"))
A.SETTINGS_PATH = A.Path(os.path.join(tmp, "settings.json"))
_sp = os.path.join(tmp, "settings.json")
_sd = json.load(io.open(_sp, encoding="utf-8")) if os.path.exists(_sp) else {}
_sd["fullscreen"] = False
io.open(_sp, "w", encoding="utf-8").write(json.dumps(_sd))
pygame.display.flip = lambda: None

random.seed(4)
app = A.App()
app._sync_window_size = lambda: None
if app.state == "splash":
    app._finish_splash()
app.new_game()
app._start_playing()

fails = 0
cut_off_total = 0
worst = (0.0, None)
for floor_idx, spec in enumerate(S.FLOOR_SPECS):
    lost = []
    for run in range(RUNS):
        random.seed(6000 + floor_idx * 100 + run)
        app._load_floor(floor_idx)
        app._begin_playing()
        maze = app.maze
        start = (int(maze.start[0]), int(maze.start[1]))
        solids = [p for p in app.props
                  if p.solid and (p.kind != "door" or p.is_open or p.is_broken)]
        free = {start: 0}
        stack = [start]
        while stack:
            c = stack.pop()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (c[0] + dx, c[1] + dy)
                if n in free or not maze.is_walkable_cell(*n):
                    continue
                if _edge_physically_clear(maze, solids, c[0], c[1], n[0], n[1]):
                    free[n] = free[c] + 1
                    stack.append(n)
        blocked = cells_no_body_fits(solids, S.PLAYER_RADIUS)
        blocked.discard(start)
        sealed = set()
        for zone in getattr(maze, "zones", ()):
            if zone["kind"] == "shed":
                x0, y0, x1, y1 = zone["rect"]
                sealed |= {(x, y) for y in range(y0, y1) for x in range(x0, x1)}
        open_grid = maze.bfs_distances(start[0], start[1])
        walkable = [c for c in maze.floor_cells()
                    if c not in blocked and c not in sealed and c in open_grid]
        cut = [c for c in walkable if c not in free]
        share = 100.0 * len(cut) / max(1, len(walkable))
        lost.append(share)
        if share > worst[0]:
            worst = (share, (spec["key"], run))
        if share > 20.0:
            between = collections.Counter()
            for cell in blocked:
                around = [(cell[0] + dx, cell[1] + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))]
                if any(c in free for c in around) and any(c in cut for c in around):
                    for p in app.props:
                        if (int(p.x), int(p.y)) == cell:
                            between[p.kind] += 1
            print("   %s run %d: %.0f%% of the floor is cut off; standing between the "
                  "two sides: %s" % (spec["key"], run, share,
                                     ", ".join("%s x%d" % kv for kv in between.most_common())
                                     or "nothing - the grid itself is split"))
            if os.environ.get("REACH_AUDIT_DUMP"):
                for cell in sorted(blocked):
                    around = [(cell[0] + dx, cell[1] + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))]
                    if not (any(c in free for c in around) and any(c in cut for c in around)):
                        continue
                    kinds = [p.kind for p in app.props if (int(p.x), int(p.y)) == cell]
                    print("      choke at %s: %s" % (cell, kinds))
                    for y in range(cell[1] - 3, cell[1] + 4):
                        row = ""
                        for x in range(cell[0] - 6, cell[0] + 7):
                            if (x, y) == cell:
                                row += "X"
                            elif not (0 <= x < maze.w and 0 <= y < maze.h):
                                row += " "
                            elif maze.grid[y][x] != S.FLOOR:
                                row += "#"
                            elif (x, y) in blocked:
                                row += "o"
                            elif (x, y) in free:
                                row += "."
                            else:
                                row += ","
                        print("         " + row)

        need = []
        if app.panel_prop is not None:
            need.append(("the objective", app.panel_prop))
        if app.exit_prop is not None:
            need.append(("the way out", app.exit_prop))
        for p in app.props:
            if p.kind == spec.get("collectible"):
                need.append(("a %s" % p.kind, p))
        for what, p in need:
            cell = getattr(p, "interact_cell", None) or (int(p.x), int(p.y))
            reach = [c for c in ([tuple(cell)] + [(cell[0] + dx, cell[1] + dy)
                                                  for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))])
                     if c in free]
            if not reach:
                fails += 1
                print("   FAIL %s on %s run %d: %s at %s cannot be reached"
                      % (what, spec["key"], run, p.kind, cell))
    print("%-7s %d floors: floor cut off by furniture, median %.2f%%, worst %.2f%%"
          % (spec["key"], RUNS, sorted(lost)[len(lost) // 2], max(lost)))

print("worst floor overall: %.2f%% cut off (%s)" % worst)
print("objectives out of reach: %d" % fails)
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if fails else 0)
