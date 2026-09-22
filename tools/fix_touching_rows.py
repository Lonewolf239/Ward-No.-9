import glob, json, math, os, sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_here))
from game.props import PROP_DEFS
from tools.room_editor.furniture_model import FurnitureGrid, MAGNET_GAP

TOUCH = 0.001
TURN = 0.01


def _rows(furniture):
    n = len(furniture)
    links = {"side": {i: set() for i in range(n)}, "depth": {i: set() for i in range(n)}}
    for i in range(n):
        a = furniture[i]
        if FurnitureGrid.docked(a[0]):
            continue
        fa = float(a[4])
        tx, ty = -math.sin(fa), math.cos(fa)
        nx, ny = math.cos(fa), math.sin(fa)
        for j in range(i + 1, n):
            b = furniture[j]
            if FurnitureGrid.docked(b[0]) or abs(float(a[3]) - float(b[3])) > TOUCH:
                continue
            turn = (float(b[4]) - fa) % math.pi
            if min(turn, math.pi - turn) > TURN:
                continue
            dx, dy = float(b[1]) - float(a[1]), float(b[2]) - float(a[2])
            along, across = abs(dx * tx + dy * ty), abs(dx * nx + dy * ny)
            da, db = PROP_DEFS[a[0]], PROP_DEFS[b[0]]
            if across <= TOUCH and abs(along - (da["hw"] + db["hw"])) <= TOUCH:
                links["side"][i].add(j)
                links["side"][j].add(i)
            elif along <= TOUCH and abs(across - (da["hd"] + db["hd"])) <= TOUCH:
                links["depth"][i].add(j)
                links["depth"][j].add(i)
    rows = []
    for axis, graph in links.items():
        seen = set()
        for i in range(n):
            if i in seen or not graph[i]:
                continue
            group, stack = [], [i]
            seen.add(i)
            while stack:
                k = stack.pop()
                group.append(k)
                for m in graph[k]:
                    if m not in seen:
                        seen.add(m)
                        stack.append(m)
            f = float(furniture[group[0]][4])
            ux, uy = (-math.sin(f), math.cos(f)) if axis == "side" else (math.cos(f), math.sin(f))
            group.sort(key=lambda k: float(furniture[k][1]) * ux + float(furniture[k][2]) * uy)
            rows.append((group, (ux, uy)))
    return rows


def open_rows(furniture):
    moved = 0
    for group, (ux, uy) in _rows(furniture):
        mid = (len(group) - 1) / 2.0
        for rank, k in enumerate(group):
            shift = (rank - mid) * MAGNET_GAP
            if not shift:
                continue
            e = furniture[k]
            e[1] = round(float(e[1]) + ux * shift, 4)
            e[2] = round(float(e[2]) + uy * shift, 4)
            moved += 1
    return moved


def main():
    write = "--write" in sys.argv
    root = os.path.dirname(_here)
    total = 0
    for path in sorted(glob.glob(os.path.join(root, "game", "room_data", "*.json"))
                       + glob.glob(os.path.join(root, "game", "zone_data", "*.json"))):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        data = json.loads(text)
        furniture = data.get("furniture", [])
        rows = _rows(furniture)
        if not rows:
            continue
        moved = 0
        for _ in range(8):
            step = open_rows(furniture)
            if not step:
                break
            moved += step
        total += moved
        sizes = ", ".join("%s x%d" % (furniture[g[0]][0], len(g)) for g, _ in rows)
        print("%-32s %d row(s): %s - %d moved" % (os.path.basename(path), len(rows), sizes, moved))
        if write:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                if text.endswith("\n"):
                    f.write("\n")
    print("%s: %d piece(s) %s" % ("written" if write else "dry run", total, "moved" if write else "would move"))


if __name__ == "__main__":
    main()
