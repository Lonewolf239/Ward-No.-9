import collections
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from game import settings as S
from game import room_templates as rt
from game import zone_templates as zt
from game.maze import Maze
from game.props import (PROP_DEFS, SURFACE_KINDS, Prop, furniture_place,
                        support_top, footprint_cells, _edge_physically_clear)
from tools.room_editor.furniture_model import plan_overlap

LIGHT_KINDS = {k for k, v in PROP_DEFS.items() if v.get("emissive") and v.get("light_radius")}
CELLS_PER_LIGHT = 16
CUT_OFF_CELLS = 3
INSIDE_FRAC = 0.55
IN_WALL_FRAC = 0.30
HEIGHT_SHARE = 0.12
ZONE_CELLS_PER_LIGHT = 20

ON_PURPOSE_ON_THE_FLOOR = {"ward_upper_02"}

problems = []


def enclosed_cells(t):
    n = zt.ZONE_SIZE
    blocked = {(x, y) for y in range(n) for x in range(n) if t.cells[y][x] != S.FLOOR}
    blocked |= {(dx, dy) for (dx, dy, _f, _k) in t.interior_doors}
    seen, stack = set(), []
    for c in ([(x, y) for x in range(n) for y in (0, n - 1)]
              + [(x, y) for y in range(n) for x in (0, n - 1)]):
        if c not in blocked and c not in seen:
            seen.add(c)
            stack.append(c)
    while stack:
        x, y = stack.pop()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            c = (x + dx, y + dy)
            if 0 <= c[0] < n and 0 <= c[1] < n and c not in blocked and c not in seen:
                seen.add(c)
                stack.append(c)
    return [(x, y) for y in range(n) for x in range(n)
            if (x, y) not in blocked and (x, y) not in seen]


def report(tid, msg):
    problems.append("%-26s %s" % (tid, msg))


def _blocks_the_way(kind):
    spec = PROP_DEFS.get(kind, {})
    return bool(spec.get("solid")) and (not spec.get("wall_mounted") or kind == "locker")


def _scene(cells, w, h, furniture):
    pad = 1
    gw, gh = w + pad * 2, h + pad * 2
    grid = [[S.WALL_CONCRETE for _ in range(gw)] for _ in range(gh)]
    for y in range(h):
        for x in range(w):
            grid[y + pad][x + pad] = cells[y][x]
    maze = Maze(w=gw, h=gh, layout="blank")
    maze.regrid(gw, gh, grid)
    props = []
    for kind, x, y, z, facing in map(furniture_place, furniture):
        prop = Prop(kind, x + pad, y + pad, facing=facing)
        prop.z0 = z
        props.append(prop)
    return maze, props, pad


def _report_cut_off_floor(tid, cells, w, h, furniture, doors):
    maze, props, pad = _scene(cells, w, h, furniture)
    floor = {(x + pad, y + pad) for y in range(h) for x in range(w)
             if cells[y][x] == S.FLOOR}
    if not floor:
        return

    def components(prop_list):
        left, out = set(floor), []
        while left:
            start = min(left)
            seen, stack = {start}, [start]
            while stack:
                c = stack.pop()
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    n = (c[0] + dx, c[1] + dy)
                    if n in floor and n not in seen and _edge_physically_clear(
                            maze, prop_list, c[0], c[1], n[0], n[1]):
                        seen.add(n)
                        stack.append(n)
            out.append(seen)
            left -= seen
        return sorted(out, key=len, reverse=True)

    parts = components(props)
    if len(parts) < 2:
        return
    bare = components([])
    home = {c: i for i, part in enumerate(bare) for c in part}
    main_by_bare = {}
    for part in parts:
        for c in part:
            main_by_bare.setdefault(home[c], set()).update(part)
    parts = [part for part in parts
             if not any(len(part) == len(bare[home[c]]) and part == bare[home[c]]
                        for c in list(part)[:1])]
    by_home = {}
    for part in parts:
        by_home.setdefault(home[next(iter(part))], []).append(part)
    parts = [None]
    for frags in by_home.values():
        frags.sort(key=len, reverse=True)
        parts.extend(frags[1:])
    if len(parts) < 2:
        return
    real = [part for part in parts[1:]
            if part is not None and any(all((c[0] + dx, c[1] + dy) in part
                       for dx in (0, 1) for dy in (0, 1))
                   for c in part)]
    if not real:
        return
    lost = set().union(*real)
    if len(lost) < CUT_OFF_CELLS:
        return
    near = sorted({(l[0] + dx, l[1] + dy) for l in lost
                   for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))} & (floor - lost))
    near += sorted(c for c in lost
                   if any(p.solid and (int(p.x), int(p.y)) == c for p in props))
    guilty = []
    for c in sorted(set(near)):
        keep = [p for p in props if (int(p.x), int(p.y)) != c or not p.solid]
        if len(keep) == len(props):
            continue
        smaller = components(keep)
        if len(smaller) < 2 or len(set().union(*smaller[1:])) < len(lost):
            guilty.append(c)
    names = []
    for c in (guilty or sorted(set(near))):
        here = sorted({p.kind for p in props if (int(p.x), int(p.y)) == c and p.solid})
        names.append("%s at %s" % ("/".join(here) or "?", (c[0] - pad, c[1] - pad)))
    if not names:
        return
    report(tid, "%s cuts %d cells off from the rest of the room (e.g. %s)"
           % (", ".join(names), len(lost),
              (sorted(lost)[0][0] - pad, sorted(lost)[0][1] - pad)))


def wall_bite(maze, prop, n=21):
    spec = PROP_DEFS[prop.kind]
    c, sn = math.cos(prop.facing), math.sin(prop.facing)
    thin = (S.WALL_BARS, S.WALL_WINDOW)
    solid = crosses = 0
    for i in range(n):
        for j in range(n):
            u = (i + 0.5) / n * 2.0 - 1.0
            v = (j + 0.5) / n * 2.0 - 1.0
            px = prop.x - sn * spec["hw"] * u + c * spec["hd"] * v
            py = prop.y + c * spec["hw"] * u + sn * spec["hd"] * v
            if not maze.is_wall(px, py):
                continue
            ix, iy = int(math.floor(px)), int(math.floor(py))
            tile = maze.grid[iy][ix] if 0 <= ix < maze.w and 0 <= iy < maze.h else None
            if tile in thin:
                crosses += 1
            else:
                solid += 1
    return solid / float(n * n), crosses > 0


def check(tid, cells, w, h, furniture, doors=(), per_light=CELLS_PER_LIGHT):
    maze, props, pad = _scene(cells, w, h, furniture)

    for prop in props:
        kind = prop.kind
        spec = PROP_DEFS[kind]
        local = (round(prop.x - pad, 2), round(prop.y - pad, 2))
        bite, through = wall_bite(maze, prop)
        if not spec["wall_mounted"]:
            if bite > IN_WALL_FRAC:
                report(tid, "%s at %s is %d%% inside a wall" % (kind, local, round(100 * bite)))
            elif through:
                report(tid, "%s at %s stands through a grille" % (kind, local))
        if spec["wall_mounted"]:
            back_x = prop.x - math.cos(prop.facing) * (spec["hd"] + 0.06)
            back_y = prop.y - math.sin(prop.facing) * (spec["hd"] + 0.06)
            bx, by = int(back_x), int(back_y)
            if 0 <= bx < w + 2 * pad and 0 <= by < h + 2 * pad:
                solid_behind = not (pad <= bx < w + pad and pad <= by < h + pad
                                    and cells[by - pad][bx - pad] == S.FLOOR)
            else:
                solid_behind = True
            if not solid_behind:
                report(tid, "%s at %s hangs on a wall that is not there" % (kind, local))

    for i, a in enumerate(props):
        for b in props[i + 1:]:
            za0, za1 = a.z0, a.z0 + a.height
            zb0, zb1 = b.z0, b.z0 + b.height
            share = min(za1, zb1) - max(za0, zb0)
            if share <= HEIGHT_SHARE:
                continue
            ea = (a.kind, a.x, a.y, a.z0, a.facing)
            eb = (b.kind, b.x, b.y, b.z0, b.facing)
            if plan_overlap(ea, eb) < INSIDE_FRAC:
                continue
            report(tid, "%s at %s and %s at %s stand inside each other"
                   % (a.kind, (round(a.x - pad, 2), round(a.y - pad, 2)),
                      b.kind, (round(b.x - pad, 2), round(b.y - pad, 2))))

    for prop in props:
        if prop.z0 <= 0.02 or PROP_DEFS[prop.kind]["wall_mounted"]:
            continue
        if PROP_DEFS[prop.kind].get("z0"):
            continue
        ea = (prop.kind, prop.x, prop.y, prop.z0, prop.facing)
        under = [q for q in props if q is not prop
                 and abs(support_top(q.kind, q.z0) - prop.z0) <= 0.02
                 and plan_overlap(ea, (q.kind, q.x, q.y, q.z0, q.facing)) > 0.05]
        if not under and tid not in ON_PURPOSE_ON_THE_FLOOR:
            report(tid, "%s at %s stands at %.2f with nothing under it"
                   % (prop.kind, (round(prop.x - pad, 2), round(prop.y - pad, 2)), prop.z0))

    _report_cut_off_floor(tid, cells, w, h, furniture, doors)

    floor_cells = sum(1 for y in range(h) for x in range(w) if cells[y][x] == S.FLOOR)
    lights = sum(1 for e in furniture if furniture_place(e)[0] in LIGHT_KINDS)
    if per_light is None:
        return floor_cells, lights, 0
    want = max(1, int(round(floor_cells / float(per_light)))) if floor_cells else 0
    return floor_cells, lights, want


print("lights and furniture in every hand-authored room")
rows = []
for floor_key in ("upper", "basement"):
    for t in rt._load_hand_authored(floor_key):
        cells, lights, want = check(t.id, t.cells, t.w, t.h, t.furniture, t.doors)
        rows.append((t.id, floor_key, cells, lights, want))
for t in zt._templates():
    inside = enclosed_cells(t)
    _c, lights, _w = check(t.id, t.cells, zt.ZONE_SIZE, zt.ZONE_SIZE, t.furniture, per_light=None)
    want = 0 if not inside else max(1, int(round(len(inside) / float(ZONE_CELLS_PER_LIGHT))))
    inside_lights = sum(1 for (kind, x, y, _z, _f) in map(furniture_place, t.furniture)
                        if kind in LIGHT_KINDS and (int(x), int(y)) in set(inside))
    rows.append((t.id, "yard", len(inside), inside_lights, want))

dark = [r for r in rows if r[3] < r[4]]
if "--quiet" not in sys.argv:
    for (tid, floor_key, cells, lights, want) in rows:
        flag = "  <- wants %d" % want if lights < want else ""
        print("   %-26s %-9s %3d cells, %d light(s)%s" % (tid, floor_key, cells, lights, flag))
print("   %d of %d templates have less light than %d cells per lamp asks for"
      % (len(dark), len(rows), CELLS_PER_LIGHT))

print()
print("items on a surface land on it")
try:
    import os as _os
    _os.environ.setdefault("SDL_VIDEODRIVER", "offscreen")
    _os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    from tools.sandbox_settings import redirect as _redirect
    _redirect()
    from game import renderer3d as _R
    from game.props import PROP_DEFS as _PD, SURFACE_KINDS as _SK, surface_top_frac as _stf
except Exception as exc:
    print("   (skipped: %s)" % exc)
else:
    floaters = 0
    for kind in sorted(_SK):
        builders = _R.VARIANT_MESH_BUILDERS.get(kind)
        if builders is None:
            one = getattr(_R, "build_%s_mesh" % kind, None)
            builders = (one,) if one else ()
        height = _PD[kind]["height"]
        for i, b in enumerate(builders):
            used = height * _stf(kind, i)
            top = float(b().reshape(-1, 11)[:, 2].max()) * height
            if used - top > 0.012:
                floaters += 1
                problems.append("%s%s: items sit at %.3f but its mesh ends at %.3f"
                                % (kind, "" if len(builders) == 1 else " v%d" % i, used, top))
    print("   %d surface meshes end below where items are placed on them" % floaters)

print()
if problems:
    print("FAIL: %d problems" % len(problems))
    for p in problems:
        print("   " + p)
    sys.exit(1)
print("PASS")
