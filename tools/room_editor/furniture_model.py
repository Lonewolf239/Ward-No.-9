import math

from game import settings as S
from game import i18n
from game.props import PROP_DEFS, support_top

PI, HALF, TAU34 = math.pi, math.pi / 2, 3 * math.pi / 2

SUPPORT_EPS = 0.02
WALL_SNAP = 0.75
MAGNET_REACH = 0.45
MAGNET_GAP = 0.004


def _is_one_of(item, group):
    return any(item is g for g in group)


def entry(item):
    return item[0], float(item[1]), float(item[2]), float(item[3]), float(item[4])


def box_corners(kind, x, y, facing, margin=0.0):
    spec = PROP_DEFS[kind]
    fx, fy = math.cos(facing), math.sin(facing)
    hw, hd = spec["hw"] + margin, spec["hd"] + margin
    return [(x - fy * hw * sw + fx * hd * sd, y + fx * hw * sw + fy * hd * sd)
            for sw, sd in ((1, 1), (1, -1), (-1, 1), (-1, -1))]


def plan_overlap(a, b):
    ka, xa, ya, _za, fa = entry(a)
    kb, xb, yb, _zb, fb = entry(b)
    sa, sb = PROP_DEFS[ka], PROP_DEFS[kb]
    if math.hypot(xa - xb, ya - yb) > (sa["hw"] + sa["hd"] + sb["hw"] + sb["hd"]):
        return 0.0

    def inside(px, py, x, y, f, spec):
        c, s = math.cos(-f), math.sin(-f)
        dx, dy = px - x, py - y
        return (abs(dx * s + dy * c) <= spec["hw"] + 1e-9
                and abs(dx * c - dy * s) <= spec["hd"] + 1e-9)

    n = 7
    small, big = (a, b) if sa["hw"] * sa["hd"] <= sb["hw"] * sb["hd"] else (b, a)
    ks, xs, ys, _zs, fs = entry(small)
    kg, xg, yg, _zg, fg = entry(big)
    ss = PROP_DEFS[ks]
    hit = 0
    cs, sn = math.cos(fs), math.sin(fs)
    for i in range(n):
        for j in range(n):
            u = (i + 0.5) / n * 2.0 - 1.0
            v = (j + 0.5) / n * 2.0 - 1.0
            px = xs - sn * ss["hw"] * u + cs * ss["hd"] * v
            py = ys + cs * ss["hw"] * u + sn * ss["hd"] * v
            if inside(px, py, xg, yg, fg, PROP_DEFS[kg]):
                hit += 1
    return hit / float(n * n)


class FurnitureGrid:
    GAP_SKIPS_BORDER = False


    def in_bounds(self, x, y):
        return 0 <= x < self.w and 0 <= y < self.h

    def on_border(self, x, y):
        return self.in_bounds(x, y) and (x in (0, self.w - 1) or y in (0, self.h - 1))

    def _is_wall_or_edge(self, x, y):
        if not self.in_bounds(x, y):
            return True
        return self.cells[y][x] != S.FLOOR

    def is_open_at(self, x, y):
        return not self._is_wall_or_edge(int(math.floor(x)), int(math.floor(y)))

    def gap_facing(self, x, y):
        if not self.in_bounds(x, y):
            return None
        if self.GAP_SKIPS_BORDER and self.on_border(x, y):
            return None
        n, s = self._is_wall_or_edge(x, y - 1), self._is_wall_or_edge(x, y + 1)
        w, e = self._is_wall_or_edge(x - 1, y), self._is_wall_or_edge(x + 1, y)
        if n or s:
            return 0.0
        if w or e:
            return HALF
        return None

    def interior_door_at(self, x, y):
        for d in self.interior_doors:
            if d[0] == x and d[1] == y:
                return d
        return None

    def wall_facings_at(self, x, y):
        checks = ((-1, 0, 0.0), (1, 0, PI), (0, -1, HALF), (0, 1, TAU34))
        return [facing for dx, dy, facing in checks
                if self._is_wall_or_edge(x + dx, y + dy)]

    def wall_facing_at(self, x, y):
        facings = self.wall_facings_at(x, y)
        return facings[0] if facings else None


    def entries(self):
        return [entry(f) for f in self.furniture]

    def furniture_on(self, x, y):
        return [f for f in self.furniture if (int(f[1]), int(f[2])) == (x, y)]

    def furniture_at(self, x, y):
        here = self.furniture_on(x, y)
        return max(here, key=lambda f: f[3]) if here else None

    def piece_at_point(self, px, py, z=None):
        best = None
        for f in self.furniture:
            kind, x, y, fz, facing = entry(f)
            spec = PROP_DEFS[kind]
            c, s = math.cos(-facing), math.sin(-facing)
            dx, dy = px - x, py - y
            if abs(dx * s + dy * c) > spec["hw"] or abs(dx * c - dy * s) > spec["hd"]:
                continue
            if z is not None and fz > z + 1e-6:
                continue
            if best is None or fz > best[3]:
                best = f
        return best


    def top_of(self, item):
        kind, _x, _y, z, _f = entry(item)
        return support_top(kind, z)

    def support_under(self, item, ignore=()):
        kind, _x, _y, z, _f = entry(item)
        if z <= SUPPORT_EPS or self.fixed_height(kind) is not None:
            return None
        best, best_ov = None, 0.0
        for other in self.furniture:
            if other is item or _is_one_of(other, ignore):
                continue
            if abs(self.top_of(other) - z) > SUPPORT_EPS:
                continue
            ov = plan_overlap(item, other)
            if ov > best_ov:
                best, best_ov = other, ov
        return best if best_ov > 0.05 else None

    def support_map(self):
        return {id(f): self.support_under(f) for f in self.furniture}

    def resting_on(self, item, supports=None):
        if supports is None:
            supports = self.support_map()
        children = {}
        for f in self.furniture:
            base = supports.get(id(f))
            if base is not None:
                children.setdefault(id(base), []).append(f)
        out, frontier, seen = [], [item], {id(item)}
        while frontier:
            b = frontier.pop()
            for c in children.get(id(b), ()):
                if id(c) in seen:
                    continue
                seen.add(id(c))
                out.append(c)
                frontier.append(c)
        return out

    def drop_height(self, kind, x, y, facing, ignore=()):
        probe = [kind, x, y, 0.0, facing]
        best = 0.0
        for other in self.furniture:
            if _is_one_of(other, ignore):
                continue
            if plan_overlap(probe, other) < 0.35:
                continue
            best = max(best, self.top_of(other))
        return best


    def _cell_takes_furniture(self, x, y):
        return True

    def preview_place(self, kind, x, y, facing=None, snap=True):
        cx, cy = int(math.floor(x)), int(math.floor(y))
        if (not self.in_bounds(cx, cy) or self.cells[cy][cx] != S.FLOOR
                or not self._cell_takes_furniture(cx, cy)
                or self.interior_door_at(cx, cy) is not None):
            return None
        spec = PROP_DEFS[kind]
        near = self.nearest_wall(x, y)
        if spec["wall_mounted"] and near is None:
            return None
        if facing is None:
            facing = near[1] if near is not None else 0.0
        item = [kind, round(x, 4), round(y, 4), 0.0, round(facing % math.tau, 4)]
        if snap or spec["wall_mounted"]:
            self.snap_entry(item)
        fixed = self.fixed_height(kind)
        item[3] = round(fixed if fixed is not None
                        else self.drop_height(kind, item[1], item[2], item[4]), 4)
        return item

    def set_hanging_height(self, item, z):
        if item is None or not self.hangs(item[0]):
            return False
        lo, hi = self.height_limits(item[0])
        item[3] = round(max(lo, min(hi, z)), 4)
        return True

    def place_furniture(self, kind, x, y, facing=None, snap=True):
        item = self.preview_place(kind, x, y, facing=facing, snap=snap)
        if item is None:
            return None
        self.furniture.append(item)
        return item

    @staticmethod
    def fixed_height(kind):
        return PROP_DEFS[kind].get("z0")

    @classmethod
    def hangs(cls, kind):
        return bool(PROP_DEFS[kind]["wall_mounted"]) and cls.fixed_height(kind) is not None

    CELL_DOCKED = frozenset(("pipes",))

    @classmethod
    def docked(cls, kind):
        return kind in cls.CELL_DOCKED

    @staticmethod
    def turns(kind):
        return not PROP_DEFS[kind]["wall_mounted"]

    @staticmethod
    def height_limits(kind):
        top = S.WALL_HEIGHT - PROP_DEFS[kind]["height"] - 0.02
        return 0.05, max(0.05, top)

    def remove_entry(self, item):
        if not _is_one_of(item, self.furniture):
            return False
        doomed = [item] + self.resting_on(item)
        self.furniture = [f for f in self.furniture if not _is_one_of(f, doomed)]
        return True

    def remove_furniture(self, x, y):
        item = self.furniture_at(x, y)
        return self.remove_entry(item) if item is not None else False

    def move_entry(self, item, x, y, carry=True, snap=False, riders=None, settle=True):
        if item is None or self.docked(item[0]):
            return False
        cx, cy = int(math.floor(x)), int(math.floor(y))
        if not self.in_bounds(cx, cy) or self.cells[cy][cx] != S.FLOOR:
            return False
        if riders is None:
            riders = self.resting_on(item) if carry else []
        if PROP_DEFS[item[0]]["wall_mounted"]:
            trial = [item[0], round(x, 4), round(y, 4), float(item[3]), float(item[4])]
            if self.nearest_wall(x, y) is None or not self.snap_entry(trial):
                return False
            x, y = float(trial[1]), float(trial[2])
            item[4] = trial[4]
        dx, dy = x - float(item[1]), y - float(item[2])
        item[1], item[2] = round(x, 4), round(y, 4)
        for r in riders:
            r[1] = round(float(r[1]) + dx, 4)
            r[2] = round(float(r[2]) + dy, 4)
        if snap:
            self.snap_entry(item)
        if settle:
            self.settle(item, riders=riders)
        return True

    def wall_axis(self, item):
        f = float(item[4])
        return -math.sin(f), math.cos(f)

    def rotate_entry(self, item, step=HALF, carry=True, riders=None):
        if item is None or not self.turns(item[0]):
            return False
        if riders is None:
            riders = self.resting_on(item) if carry else []
        ox, oy = float(item[1]), float(item[2])
        c, s = math.cos(step), math.sin(step)
        for r in riders:
            rx, ry = float(r[1]) - ox, float(r[2]) - oy
            r[1] = round(ox + rx * c - ry * s, 4)
            r[2] = round(oy + rx * s + ry * c, 4)
            r[4] = round((float(r[4]) + step) % math.tau, 4)
        item[4] = round((float(item[4]) + step) % math.tau, 4)
        return True

    def set_z(self, item, z, riders=None):
        if item is None:
            return False
        if riders is None:
            riders = self.resting_on(item)
        dz = max(0.0, z) - float(item[3])
        item[3] = round(max(0.0, z), 4)
        for r in riders:
            r[3] = round(max(0.0, float(r[3]) + dz), 4)
        return True

    def settle(self, item, riders=None):
        if item is None:
            return False
        kind, x, y, _z, facing = entry(item)
        if riders is None:
            riders = self.resting_on(item)
        if self.hangs(kind):
            lo, hi = self.height_limits(kind)
            return self.set_z(item, max(lo, min(hi, float(item[3]))), riders=riders)
        fixed = self.fixed_height(kind)
        if fixed is not None:
            return self.set_z(item, fixed, riders=riders)
        z = self.drop_height(kind, x, y, facing, ignore=tuple(riders) + (item,))
        return self.set_z(item, z, riders=riders)


    def nearest_wall(self, x, y, reach=WALL_SNAP):
        best = None
        cx, cy = int(math.floor(x)), int(math.floor(y))
        for gy in range(cy - 1, cy + 2):
            for gx in range(cx - 1, cx + 2):
                if not self._is_wall_or_edge(gx, gy):
                    continue
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    if self._is_wall_or_edge(gx + dx, gy + dy):
                        continue
                    fx = gx + 0.5 + dx * 0.5
                    fy = gy + 0.5 + dy * 0.5
                    d = abs((x - fx) * dx + (y - fy) * dy)
                    lat = abs((x - fx) * dy - (y - fy) * dx)
                    if lat > 0.5 or d > reach:
                        continue
                    if best is None or d < best[0]:
                        best = (d, math.atan2(dy, dx), (fx, fy), (dx, dy))
        return best

    def snap_entry(self, item):
        if item is None:
            return False
        kind, x, y, _z, _f = entry(item)
        spec = PROP_DEFS[kind]
        near = self.nearest_wall(x, y)
        if near is None:
            return False
        _d, facing, (fx, fy), (dx, dy) = near
        if not spec["wall_mounted"] and _d > spec["hd"] + 0.12:
            return False
        if self.hangs(kind) and float(item[3]) <= 0.0:
            item[3] = round(self.fixed_height(kind), 4)
        item[4] = round(facing % math.tau, 4)
        item[1] = round(fx + math.cos(facing) * (spec["hd"] + 0.005), 4)
        item[2] = round(fy + math.sin(facing) * (spec["hd"] + 0.005), 4)
        if not (dx and dy):
            if dx:
                item[2] = round(math.floor(y) + 0.5 if self.docked(kind) else y, 4)
            else:
                item[1] = round(math.floor(x) + 0.5 if self.docked(kind) else x, 4)
        return True

    def tuck_entry(self, item, target):
        if item is None or target is None or item is target:
            return False
        kind, x, y, _z, _f = entry(item)
        tk, tx, ty, _tz, tf = entry(target)
        facing = math.atan2(ty - y, tx - x)
        rel = facing - tf
        spec, tspec = PROP_DEFS[kind], PROP_DEFS[tk]
        reach = abs(math.cos(rel)) * tspec["hd"] + abs(math.sin(rel)) * tspec["hw"]
        back = reach + spec["hd"] - 0.03
        item[1] = round(tx - math.cos(facing) * back, 4)
        item[2] = round(ty - math.sin(facing) * back, 4)
        item[4] = round(facing % math.tau, 4)
        self.settle(item)
        return True

    def magnet(self, items):
        items = [i for i in items if i is not None and not self.docked(i[0])]
        if not items:
            return 0
        before = [(i[1], i[2], i[4]) for i in items]
        for item in items:
            self.snap_entry(item)
        facing = float(items[0][4])
        sx, sy = -math.sin(facing), math.cos(facing)
        order = sorted(items, key=lambda i: float(i[1]) * sx + float(i[2]) * sy)
        was = [float(i[1]) * sx + float(i[2]) * sy for i in order]
        for n in range(1, len(order)):
            prev, item = order[n - 1], order[n]
            want = PROP_DEFS[prev[0]]["hw"] + PROP_DEFS[item[0]]["hw"] + MAGNET_GAP
            if not (-MAGNET_GAP - 0.001 <= was[n] - was[n - 1] - want <= MAGNET_REACH):
                continue
            now = float(prev[1]) * sx + float(prev[2]) * sy + want
            shift = now - (float(item[1]) * sx + float(item[2]) * sy)
            item[1] = round(float(item[1]) + sx * shift, 4)
            item[2] = round(float(item[2]) + sy * shift, 4)
        for item in items:
            self.settle(item)
        return sum(1 for i, b in zip(items, before) if (i[1], i[2], i[4]) != b)


    def _drop_orphaned_wall_furniture(self):
        keep = []
        for f in self.furniture:
            kind, x, y, _z, _fa = entry(f)
            if not PROP_DEFS[kind]["wall_mounted"]:
                keep.append(f)
                continue
            if self.nearest_wall(x, y, reach=PROP_DEFS[kind]["hd"] + 0.15) is not None:
                keep.append(f)
        self.furniture = keep
        self._drop_furniture_in_walls()

    def _drop_furniture_in_walls(self):
        self.furniture = [
            f for f in self.furniture
            if self.is_open_at(float(f[1]), float(f[2]))
        ]

    def _drop_orphaned_surface_items(self):
        for f in list(self.furniture):
            if self.fixed_height(f[0]) is not None:
                continue
            if float(f[3]) > SUPPORT_EPS and self.support_under(f) is None:
                self.settle(f)

    def _drop_orphaned_interior_doors(self):
        self.interior_doors = [d for d in self.interior_doors
                               if self.gap_facing(d[0], d[1]) is not None]


    def move_furniture(self, from_cell, to_cell):
        item = self.furniture_at(*from_cell)
        return self.move_entry(item, to_cell[0] + 0.5, to_cell[1] + 0.5)

    def rotate_furniture(self, x, y, step=HALF):
        return self.rotate_entry(self.furniture_at(x, y), step)

    def window_errors(self):
        bad = []
        for y in range(self.h):
            for x in range(self.w):
                if self.cells[y][x] != S.WALL_WINDOW:
                    continue
                if not any(self.in_bounds(x + dx, y + dy) and self.cells[y + dy][x + dx] == S.FLOOR
                           for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                    bad.append((x, y))
        return [i18n.t("editor.err.window_no_floor", x=x, y=y) for x, y in bad]
