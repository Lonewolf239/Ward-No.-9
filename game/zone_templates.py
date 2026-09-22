import collections
import glob
import json
import math
import os

from game import props
from game import settings as S

ZONE_DATA_DIR = os.path.join(os.path.dirname(__file__), "zone_data")
ZONE_SIZE = S.YARD_ZONE_SIZE


class ZoneTemplate:
    def __init__(self, id, kind, required, weight, cells, interior_doors, furniture):
        self.id = id
        self.kind = kind
        self.required = required
        self.weight = weight
        self.cells = cells
        self.interior_doors = interior_doors
        self.furniture = furniture
        self.building = bool(enclosed_cells(cells, interior_doors))


def enclosed_cells(cells, interior_doors):
    n = ZONE_SIZE
    blocked = {(x, y) for y in range(n) for x in range(n) if cells[y][x] != S.FLOOR}
    blocked |= {(d[0], d[1]) for d in interior_doors}
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


def _load_zones():
    templates = []
    for path in sorted(glob.glob(os.path.join(ZONE_DATA_DIR, "*.json"))):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cells = data["cells"]
        border_blocked = any(
            cells[y][x] != S.FLOOR
            for y in range(ZONE_SIZE) for x in range(ZONE_SIZE)
            if x in (0, ZONE_SIZE - 1) or y in (0, ZONE_SIZE - 1)
        )
        if border_blocked:
            raise ValueError(
                f"zone template {data['id']!r} has a wall on its outer ring - "
                "a zone's border must always be open ground (see zone_model.py)"
            )
        templates.append(ZoneTemplate(
            id=data["id"], kind=data["kind"], required=data.get("required", False),
            weight=data.get("weight", 1.0), cells=cells,
            interior_doors=[tuple(d) for d in data.get("interior_doors", [])],
            furniture=[props.furniture_place(f) for f in data.get("furniture", [])],
        ))
    return templates


_TEMPLATES = None


def _templates():
    global _TEMPLATES
    if _TEMPLATES is None:
        _TEMPLATES = _load_zones()
    return _TEMPLATES


def _weighted_choice(rng, templates):
    total = sum(max(0.0001, t.weight) for t in templates)
    r = rng.uniform(0, total)
    upto = 0.0
    for t in templates:
        upto += max(0.0001, t.weight)
        if r <= upto:
            return t
    return templates[-1]


def _turn_zone_furniture(item, n):
    kind, x, y, z, facing = props.furniture_place(item)
    return (kind, n - y, x, z, (facing + math.pi / 2) % math.tau)


def _rotate_cw(template, k):
    cells = template.cells
    idoors = list(template.interior_doors)
    furn = list(template.furniture)
    n = ZONE_SIZE
    for _ in range(k % 4):
        cells = [list(row) for row in zip(*cells[::-1])]
        idoors = [(n - 1 - ly, lx, (facing + math.pi / 2) % math.tau, kind)
                  for (lx, ly, facing, kind) in idoors]
        furn = [_turn_zone_furniture(item, n) for item in furn]
    return cells, idoors, furn


BLEND_KINDS = {
    "forest": ("tree", "tree", "bush", "tree_stump", "fallen_log"),
    "alley": ("bush", "bush", "park_bench"),
    "open": ("bush", "rock", "tree"),
    "pen": ("bush", "rock"),
    "ruin": ("rock", "rock", "bush"),
    "dump": ("barrel", "crate", "trash_can"),
    "shed": ("crate", "barrel"),
    "tool_shed": ("crate", "barrel"),
    "storage": ("crate", "barrel"),
    "greenhouse": ("bush", "bush", "crate"),
    "chapel": ("bush", "rock"),
    "plant": ("barrel", "crate"),
    "morgue_dock": ("crate", "bush"),
}
BLEND_DEFAULT = ("bush", "rock")
BLEND_BAND = 2
BLEND_PER_EDGE = (1, 3)
BLEND_SPACING = 2
_BLEND_REACH = range(1 - BLEND_SPACING, BLEND_SPACING)


def _blend_zone_edges(rng, grid, zones, n_grid):
    by_cell = {}
    for idx, zone in enumerate(zones):
        gx = idx % n_grid
        gy = idx // n_grid
        by_cell[(gx, gy)] = zone

    taken = set()
    for zone in zones:
        taken |= {(int(e[1]), int(e[2]))
                  for e in (props.furniture_place(f) for f in zone["furniture"])}
        taken |= {(x, y) for (x, y, _f, _k) in zone["interior_doors"]}
        for (dx, dy, _f, _k) in zone["interior_doors"]:
            taken |= {(dx + ox, dy + oy) for ox, oy in
                      ((1, 0), (-1, 0), (0, 1), (0, -1), (0, 0))}

    def band(zone, side):
        x0, y0, x1, y1 = zone["rect"]
        if side == "W":
            return [(x, y) for y in range(y0, y1) for x in range(x0, x0 + BLEND_BAND)]
        if side == "E":
            return [(x, y) for y in range(y0, y1) for x in range(x1 - BLEND_BAND, x1)]
        if side == "N":
            return [(x, y) for x in range(x0, x1) for y in range(y0, y0 + BLEND_BAND)]
        return [(x, y) for x in range(x0, x1) for y in range(y1 - BLEND_BAND, y1)]

    def spill(source, target, side):
        pool = BLEND_KINDS.get(source["kind"], BLEND_DEFAULT)
        spots = [c for c in band(target, side)
                 if grid[c[1]][c[0]] == S.FLOOR and c not in taken]
        rng.shuffle(spots)
        want = rng.randint(*BLEND_PER_EDGE)
        placed = 0
        for c in spots:
            if placed >= want:
                break
            if any((c[0] + dx, c[1] + dy) in taken for dx in _BLEND_REACH for dy in _BLEND_REACH):
                continue
            kind = rng.choice(pool)
            target["furniture"].append(
                (kind, c[0] + 0.5, c[1] + 0.5, 0.0, round(rng.uniform(0, math.tau), 3)))
            taken.add(c)
            placed += 1

    for (gx, gy), zone in sorted(by_cell.items()):
        for dx, dy, mine, theirs in ((1, 0, "E", "W"), (0, 1, "S", "N")):
            other = by_cell.get((gx + dx, gy + dy))
            if other is None:
                continue
            spill(zone, other, theirs)
            spill(other, zone, mine)


def _neighbors(c, n_grid):
    gx, gy = c
    return [(gx + dx, gy + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
            if 0 <= gx + dx < n_grid and 0 <= gy + dy < n_grid]


def _flood_connected(grid, origin_x, origin_y, n_grid):
    span = n_grid * ZONE_SIZE
    floor_cells = {(x, y) for y in range(origin_y, origin_y + span)
                   for x in range(origin_x, origin_x + span) if grid[y][x] == S.FLOOR}
    if not floor_cells:
        return True
    start = next(iter(floor_cells))
    seen = {start}
    stack = [start]
    while stack:
        x, y = stack.pop()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            c = (x + dx, y + dy)
            if c in floor_cells and c not in seen:
                seen.add(c)
                stack.append(c)
    return len(seen) == len(floor_cells)


def _attempt_place_zones(rng, grid, origin_x, origin_y, n_grid):
    templates = _templates()
    by_kind = {}
    for t in templates:
        by_kind.setdefault(t.kind, []).append(t)

    all_cells = [(gx, gy) for gy in range(n_grid) for gx in range(n_grid)]
    rng.shuffle(all_cells)

    buildings = {kind for kind, ts in by_kind.items() if any(t.building for t in ts)}

    plan = {}
    center = (n_grid // 2, n_grid // 2)
    plan[center] = "open"
    required_kinds = sorted({t.kind for t in templates if t.required})

    def fits(c, kind):
        around = [plan.get(n) for n in _neighbors(c, n_grid)]
        if kind in around:
            return False
        if kind in buildings:
            if any(k in buildings for k in around):
                return False
            if sum(1 for k in plan.values() if k in buildings) >= S.YARD_MAX_BUILDINGS:
                return False
        return True

    hatch_ring = set(_neighbors(center, n_grid))

    def belongs(a, b):
        return (b in S.ZONE_NEIGHBOURS.get(a, ())
                or a in S.ZONE_NEIGHBOURS.get(b, ()))

    def standing(c, kind):
        s = 1.0
        if c in hatch_ring:
            s *= S.ZONE_AT_HATCH.get(kind, 1.0)
        for n in _neighbors(c, n_grid):
            here = plan.get(n)
            if here is not None and belongs(kind, here):
                s *= S.ZONE_NEIGHBOUR_BONUS
        return s

    def place_content(kind):
        best = None
        for c in all_cells:
            if c in plan or not fits(c, kind):
                continue
            s = standing(c, kind)
            if best is None or s > best[0]:
                best = (s, c)
        if best is None:
            return False
        plan[best[1]] = kind
        return True

    for kind in required_kinds:
        place_content(kind)

    kinds = sorted(by_kind)
    coverage = [k for k in kinds if k not in required_kinds]
    rng.shuffle(coverage)
    coverage.sort(key=lambda k: -S.YARD_ZONE_KIND_WEIGHT.get(k, 1.0))
    for kind in coverage:
        if len(plan) >= n_grid * n_grid:
            break
        place_content(kind)

    for c in all_cells:
        if c in plan:
            continue
        pool = [k for k in kinds if fits(c, k)]
        if not pool:
            plan[c] = "open"
            continue
        used = collections.Counter(plan.values())
        weights = [S.YARD_ZONE_KIND_WEIGHT.get(k, 1.0)
                   * (0.0 if k in required_kinds and used[k] else
                      S.YARD_ZONE_KIND_REPEAT ** used[k])
                   * standing(c, k)
                   for k in pool]
        if sum(weights) <= 0.0:
            weights = [1.0] * len(pool)
        r = rng.uniform(0, sum(weights))
        upto = 0.0
        for k, wgt in zip(pool, weights):
            upto += wgt
            if r <= upto:
                plan[c] = k
                break
        else:
            plan[c] = pool[-1]

    zones = []
    for gy in range(n_grid):
        for gx in range(n_grid):
            kind = plan[(gx, gy)]
            candidates = by_kind.get(kind) or by_kind["open"]
            template = _weighted_choice(rng, candidates)
            cells, idoors, furn = _rotate_cw(template, rng.randrange(4))

            zx0, zy0 = origin_x + gx * ZONE_SIZE, origin_y + gy * ZONE_SIZE
            for ly in range(ZONE_SIZE):
                for lx in range(ZONE_SIZE):
                    grid[zy0 + ly][zx0 + lx] = cells[ly][lx]

            zones.append({
                "rect": (zx0, zy0, zx0 + ZONE_SIZE, zy0 + ZONE_SIZE),
                "kind": kind,
                "interior_doors": [(zx0 + lx, zy0 + ly, facing, dkind) for (lx, ly, facing, dkind) in idoors],
                "furniture": [
                    (e[0], zx0 + e[1], zy0 + e[2], e[3], e[4])
                    for e in map(props.furniture_place, furn)
                ],
            })
    _blend_zone_edges(rng, grid, zones, n_grid)
    return zones


def generate_yard_zones(rng, grid, origin_x, origin_y):
    n_grid = S.YARD_ZONE_GRID
    zones = None
    for _ in range(30):
        scratch = [row[:] for row in grid]
        zones = _attempt_place_zones(rng, scratch, origin_x, origin_y, n_grid)
        if _flood_connected(scratch, origin_x, origin_y, n_grid):
            for y in range(len(grid)):
                grid[y][:] = scratch[y]
            return zones
    for y in range(len(grid)):
        grid[y][:] = scratch[y]
    return zones
