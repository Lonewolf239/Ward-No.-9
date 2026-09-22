import glob
import json
import math
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_here))

from game import settings as S
from game.maze import Maze
from game.props import (PROP_DEFS, _authored_prop_position, base_kind_by_cell,
                        furniture_entry)

PAD = 1


def _maze_for(cells, w, h):
    gw, gh = w + PAD * 2, h + PAD * 2
    grid = [[S.WALL_CONCRETE for _ in range(gw)] for _ in range(gh)]
    for y in range(h):
        for x in range(w):
            grid[y + PAD][x + PAD] = cells[y][x]
    m = Maze(w=gw, h=gh, layout="blank")
    m.regrid(gw, gh, grid)
    return m


def bake(cells, w, h, furniture):
    maze = _maze_for(cells, w, h)
    entries = [furniture_entry(e) for e in furniture]
    base = base_kind_by_cell((e[0], (e[1] + PAD, e[2] + PAD), e[3]) for e in entries)
    out = []
    for kind, lx, ly, facing, ox, oy in entries:
        cell = (lx + PAD, ly + PAD)
        x, y, z0, forced = _authored_prop_position(
            cell, kind, facing, maze, base, rng=None,
            offset=None if ox is None else (ox, oy))
        out.append((kind, round(x - PAD, 4), round(y - PAD, 4),
                    round(z0, 4), round((facing if forced is None else forced) % math.tau, 4)))
    return out


def main():
    write = "--write" in sys.argv
    files = sorted(glob.glob(os.path.join(os.path.dirname(_here), "game", "room_data", "*.json")) +
                   glob.glob(os.path.join(os.path.dirname(_here), "game", "zone_data", "*.json")))
    total = moved = already = 0
    worst = (0.0, None)
    for path in files:
        data = json.load(open(path, encoding="utf-8"))
        furn = data.get("furniture", [])
        if not furn:
            continue
        if all(len(e) == 5 and isinstance(e[1], float) for e in furn):
            already += 1
            continue
        cells = data["cells"]
        h = len(cells)
        w = len(cells[0]) if h else 0
        baked = bake(cells, w, h, furn)
        for old, new in zip(furn, baked):
            total += 1
            k, lx, ly = old[0], old[1], old[2]
            d = math.hypot(new[1] - (lx + 0.5), new[2] - (ly + 0.5))
            if d > 1e-9 or new[3] > 0:
                moved += 1
            if d > worst[0]:
                worst = (d, "%s %s %s" % (os.path.basename(path), k, (lx, ly)))
        if write:
            data["furniture"] = [list(e) for e in baked]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
                f.write("\n")
    print("файлов: %d (уже в новом формате: %d)" % (len(files), already))
    print("записей мебели: %d, из них стоят не в центре клетки: %d" % (total, moved))
    print("самое большое смещение от центра клетки: %.3f (%s)" % worst)
    print("ЗАПИСАНО" if write else "ничего не записано (нужен --write)")


if __name__ == "__main__":
    main()
