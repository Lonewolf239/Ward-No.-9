import collections
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from game import settings as S
from game import zone_templates as zt
from game.props import PROP_DEFS, furniture_place
from game.maze import Maze

N = S.YARD_ZONE_SIZE
problems = []


def solid(cells, x, y):
    if not (0 <= x < N and 0 <= y < N):
        return False
    return cells[y][x] != S.FLOOR


def report(tid, msg):
    problems.append("%-24s %s" % (tid, msg))


def enclosed_cells(t):
    blocked = {(x, y) for y in range(N) for x in range(N) if solid(t.cells, x, y)}
    blocked |= {(dx, dy) for (dx, dy, _f, _k) in t.interior_doors}
    seen = set()
    stack = [c for c in ([(x, y) for x in range(N) for y in (0, N - 1)]
                         + [(x, y) for y in range(N) for x in (0, N - 1)]) if c not in blocked]
    seen.update(stack)
    while stack:
        x, y = stack.pop()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            c = (x + dx, y + dy)
            if 0 <= c[0] < N and 0 <= c[1] < N and c not in blocked and c not in seen:
                seen.add(c)
                stack.append(c)
    return [(x, y) for y in range(N) for x in range(N)
            if (x, y) not in blocked and (x, y) not in seen]


templates = zt._templates()
print("1. doorways clear of furniture (%d templates)" % len(templates))
for t in templates:
    seen = {}
    for (kind, fx, fy, _z, _f) in map(furniture_place, t.furniture):
        lx, ly = int(fx), int(fy)
        if not (0 <= lx < N and 0 <= ly < N):
            report(t.id, "furniture %s outside the patch at %s" % (kind, (lx, ly)))
            continue
        seen.setdefault((lx, ly), kind)
    for (dx, dy, _f, _k) in t.interior_doors:
        if solid(t.cells, dx, dy):
            report(t.id, "door at %s is walled up" % ((dx, dy),))
        if (dx, dy) in seen:
            report(t.id, "door at %s has %s standing in it" % ((dx, dy), seen[(dx, dy)]))

print("2. buildings: a way in, and a roof")
for t in templates:
    shed_cells = [(x, y) for y in range(N) for x in range(N)
                  if t.cells[y][x] in S.BUILDING_WALLS]
    if not shed_cells:
        continue
    inside = set(enclosed_cells(t))
    if inside and not t.interior_doors:
        report(t.id, "%d cells shut inside with no door" % len(inside))
    if inside and t.interior_doors and not any(
            (dx + ox, dy + oy) in inside
            for (dx, dy, _f, _k) in t.interior_doors
            for ox, oy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
        report(t.id, "its door does not open into the inside")

print("3. roofs")
from game.maze import shed_roof_areas_for
for t in templates:
    shed_cells = [(x, y) for y in range(N) for x in range(N)
                  if t.cells[y][x] in S.BUILDING_WALLS]
    if not shed_cells:
        continue
    grid = [[t.cells[y][x] for x in range(N)] for y in range(N)]
    areas = shed_roof_areas_for(grid, N, N, Maze.SHED_MAX_WALL_GAP)
    covered = set()
    for area in areas:
        covered |= set(area)
    inner = enclosed_cells(t)
    bare = [c for c in inner if c not in covered]
    print("   %-24s roofs %d, enclosed cells %d, of them unroofed %d"
          % (t.id, len(areas), len(inner), len(bare)))
    if bare and len(bare) > len(inner) * 0.2:
        report(t.id, "%d of %d enclosed cells have no roof over them" % (len(bare), len(inner)))

print("4. a laid-out yard stays walkable (40 seeds)")
stranded = 0
for seed in range(40):
    m = Maze(seed=seed, w=S.YARD_W, h=S.YARD_H, layout="yard")
    dist = m.bfs_distances(int(m.start[0]), int(m.start[1]))
    stranded += len(set(m.floor_cells()) - set(dist))
print("   floor cells cut off from the spawn: %d" % stranded)
if stranded:
    report("yard", "%d floor cells cut off from the spawn" % stranded)

print()
if problems:
    print("FAIL")
    for p in problems:
        print("   " + p)
    sys.exit(1)
print("PASS")
