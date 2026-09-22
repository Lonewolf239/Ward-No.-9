import glob
import json
import os
import random
import sys
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game import settings as S
from game.maze import Maze

ROOM_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "game", "room_data")
WALKABLE = {S.FLOOR}
SEEN_THROUGH = set(S.SEE_THROUGH_WALLS)
SEALED_CELL_MAX = 24


def pockets(cells, w, h):
    seen = set()
    out = []
    for y in range(h):
        for x in range(w):
            if cells[y][x] not in WALKABLE or (x, y) in seen:
                continue
            group = set()
            q = deque([(x, y)])
            seen.add((x, y))
            while q:
                cx, cy = q.popleft()
                group.add((cx, cy))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cx + dx, cy + dy
                    if (0 <= nx < w and 0 <= ny < h and (nx, ny) not in seen
                            and cells[ny][nx] in WALKABLE):
                        seen.add((nx, ny))
                        q.append((nx, ny))
            out.append(group)
    return out


def audit_doors():
    problems = []
    checked = 0
    for path in sorted(glob.glob(os.path.join(ROOM_DIR, "*.json"))):
        data = json.load(open(path, encoding="utf-8"))
        doors = data.get("doors") or {}
        if not doors:
            continue
        checked += 1
        cells, w, h = data["cells"], data["w"], data["h"]
        groups = pockets(cells, w, h)
        touched = {}
        for side, info in doors.items():
            x, y = info["cell"]
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if not (0 <= nx < w and 0 <= ny < h):
                    continue
                for i, group in enumerate(groups):
                    if (nx, ny) in group and side not in touched:
                        touched[side] = i
            touched.setdefault(side, None)
        if len(set(touched.values())) > 1:
            problems.append((os.path.basename(path), touched, [len(g) for g in groups]))
    return checked, problems


def stranded_pockets(maze):
    walkable = set(maze.floor_cells())
    sx, sy = int(maze.start[0]), int(maze.start[1])
    stranded = walkable - set(maze.bfs_distances(sx, sy))
    seen = set()
    out = []
    for start in stranded:
        if start in seen:
            continue
        q = deque([start])
        seen.add(start)
        group = set()
        barred = False
        while q:
            cx, cy = q.popleft()
            group.add((cx, cy))
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = cx + dx, cy + dy
                if not (0 <= nx < maze.w and 0 <= ny < maze.h):
                    continue
                tile = maze.grid[ny][nx]
                if tile in SEEN_THROUGH:
                    barred = True
                elif (nx, ny) in stranded and (nx, ny) not in seen:
                    seen.add((nx, ny))
                    q.append((nx, ny))
        out.append((group, barred))
    return len(walkable), out


def audit_floors(runs):
    problems = []
    report = []
    for spec in S.FLOOR_SPECS:
        theme = spec.get("floor_theme")
        if not theme or spec.get("layout") == "yard":
            continue
        sealed = accidental = 0
        worst = None
        for seed in range(runs):
            stage = 1 + seed % 4
            maze = Maze(seed=seed, wall_bias=spec.get("wall_bias"), template_floor=theme,
                        room_count_range=spec.get("room_count"), anomaly=stage)
            total, groups = stranded_pockets(maze)
            cell_room_cells = set()
            for room in maze.rooms:
                if room.get("kind") == "cell":
                    cell_room_cells.update(maze.room_cells(room))
            for group, barred in groups:
                authored = barred and group <= cell_room_cells
                if barred and (authored or len(group) <= SEALED_CELL_MAX):
                    sealed += len(group)
                else:
                    accidental += len(group)
                    if worst is None or len(group) > worst[0]:
                        worst = (len(group), seed, total)
        report.append((spec["key"], sealed, accidental, worst))
        if accidental:
            problems.append((spec["key"], worst))
    return report, problems


def audit_generated(runs):
    from game.props import PROP_DEFS
    mounted = {k for k, v in PROP_DEFS.items() if v.get("wall_mounted")}
    out = []
    for spec in S.FLOOR_SPECS:
        theme = spec.get("floor_theme")
        if not theme or spec.get("layout") == "yard":
            continue
        free_doors = total_doors = orphans = 0
        for seed in range(runs):
            stage = 1 + seed % 4
            maze = Maze(seed=seed, wall_bias=spec.get("wall_bias"), template_floor=theme,
                        room_count_range=spec.get("room_count"), anomaly=stage)

            def solid(x, y):
                if not (0 <= x < maze.w and 0 <= y < maze.h):
                    return True
                return maze.grid[y][x] != S.FLOOR

            for cell, _facing, _kind in getattr(maze, "template_doors", []):
                total_doors += 1
                x, y = int(cell[0]), int(cell[1])
                if not ((solid(x - 1, y) and solid(x + 1, y))
                        or (solid(x, y - 1) and solid(x, y + 1))):
                    free_doors += 1
            for room in maze.rooms:
                for f in room.get("furniture", []):
                    if f["kind"] not in mounted:
                        continue
                    fx, fy = int(f["pos"][0]), int(f["pos"][1])
                    if not any(solid(fx + dx, fy + dy)
                               for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                        orphans += 1
        out.append((spec["key"], free_doors, total_doors, orphans))
    return out


def main():
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    failed = False

    checked, problems = audit_doors()
    print("1. door pockets")
    print("   %d room templates with doors" % checked)
    if problems:
        failed = True
        for name, touched, sizes in problems:
            print("   BROKEN %s: doors reach different pockets (%s), pocket sizes %s"
                  % (name.replace(".json", ""),
                     ", ".join("%s->%s" % kv for kv in sorted(touched.items())), sizes))
    else:
        print("   every door opens into its room's own walkable space")

    print("2. reachable floor (%d generated floors each)" % runs)
    report, problems = audit_floors(runs)
    for key, sealed, accidental, worst in report:
        print("   %-8s %d cells shut in behind bars on purpose, %d walled off by accident"
              % (key, sealed, accidental))
    if problems:
        failed = True
        for key, worst in problems:
            size, seed, total = worst
            print("   BROKEN %s: a pocket of %d cells (of %d) nobody can reach, "
                  "shut in by solid wall - seed %d" % (key, size, total, seed))

    print("3. doors in walls, 4. furniture on walls that still exist")
    for key, free_doors, total_doors, orphans in audit_generated(runs):
        print("   %-8s %d of %d doors stand in the open, %d pieces of wall furniture "
              "left with no wall" % (key, free_doors, total_doors, orphans))
        if free_doors or orphans:
            failed = True

    print("FAIL" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
