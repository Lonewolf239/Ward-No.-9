import glob
import json
import math
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_here))

from game import settings as S
from game.props import PROP_DEFS

MAX_PUSH = 0.30
MARGIN = 0.005
THIN = (S.WALL_BARS,)


def _solid(cells, w, h, x, y):
    if not (0 <= x < w and 0 <= y < h):
        return True
    v = cells[y][x]
    return v not in (S.FLOOR, S.WALL_OUTDOOR) and v not in THIN


def _corners(kind, x, y, facing):
    spec = PROP_DEFS[kind]
    hw, hd = spec["hw"], spec["hd"]
    c, s = math.cos(facing), math.sin(facing)
    return [(x + ox * c - oy * s, y + ox * s + oy * c)
            for ox, oy in ((hd, hw), (hd, -hw), (-hd, hw), (-hd, -hw))]


def push_out(cells, w, h, kind, x, y, facing):
    dx = dy = 0.0
    for _ in range(4):
        moved = False
        for cx, cy in _corners(kind, x + dx, y + dy, facing):
            ix, iy = int(math.floor(cx)), int(math.floor(cy))
            if not _solid(cells, w, h, ix, iy):
                continue
            px, py = x + dx, y + dy
            cand = []
            if not _solid(cells, w, h, ix - 1, iy) and px < ix + 0.5:
                cand.append((ix - cx - MARGIN, 0.0))
            if not _solid(cells, w, h, ix + 1, iy) and px > ix + 0.5:
                cand.append((ix + 1 - cx + MARGIN, 0.0))
            if not _solid(cells, w, h, ix, iy - 1) and py < iy + 0.5:
                cand.append((0.0, iy - cy - MARGIN))
            if not _solid(cells, w, h, ix, iy + 1) and py > iy + 0.5:
                cand.append((0.0, iy + 1 - cy + MARGIN))
            if not cand:
                continue
            ox, oy = min(cand, key=lambda v: abs(v[0]) + abs(v[1]))
            dx += ox
            dy += oy
            moved = True
        if not moved:
            break
    return dx, dy


def main():
    write = "--write" in sys.argv
    root = os.path.dirname(_here)
    files = sorted(glob.glob(os.path.join(root, "game", "room_data", "*.json")) +
                   glob.glob(os.path.join(root, "game", "zone_data", "*.json")))
    moved = too_far = 0
    for path in files:
        data = json.load(open(path, encoding="utf-8"))
        cells = data["cells"]
        h = len(cells)
        w = len(cells[0]) if h else 0
        changed = False
        for e in data.get("furniture", []):
            kind, x, y, z, facing = e[0], float(e[1]), float(e[2]), float(e[3]), float(e[4])
            spec = PROP_DEFS.get(kind, {})
            if spec.get("wall_mounted"):
                continue
            dx, dy = push_out(cells, w, h, kind, x, y, facing)
            d = math.hypot(dx, dy)
            if d < 1e-6:
                continue
            name = os.path.basename(path)
            if d > MAX_PUSH:
                too_far += 1
                print("  НЕ ТРОГАЮ  %-30s %-14s (%.2f, %.2f): нужно %.2f - это не касание"
                      % (name, kind, x, y, d))
                continue
            moved += 1
            print("  %-10s %-30s %-14s (%.2f, %.2f) -> (%.2f, %.2f), сдвиг %.3f"
                  % ("прижат" if write else "прижму", name, kind, x, y, x + dx, y + dy, d))
            e[1], e[2] = round(x + dx, 4), round(y + dy, 4)
            changed = True
        if write and changed:
            with open(path, encoding="utf-8") as f:
                newline = f.read().endswith("\n")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                if newline:
                    f.write("\n")
    print("предметов прижато: %d, оставлено (слишком далеко): %d%s"
          % (moved, too_far, "" if write else "  (ничего не записано, нужен --write)"))


if __name__ == "__main__":
    main()
