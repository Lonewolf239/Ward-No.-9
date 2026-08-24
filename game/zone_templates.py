import glob
import json
import math
import os

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
            furniture=[tuple(f) for f in data.get("furniture", [])],
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


def _rotate_cw(template, k):
    cells = template.cells
    idoors = list(template.interior_doors)
    furn = list(template.furniture)
    n = ZONE_SIZE
    for _ in range(k % 4):
        cells = [list(row) for row in zip(*cells[::-1])]
        idoors = [(n - 1 - ly, lx, (facing + math.pi / 2) % math.tau, kind)
                  for (lx, ly, facing, kind) in idoors]
        furn = [(kind, n - 1 - ly, lx, (facing + math.pi / 2) % math.tau)
                for (kind, lx, ly, facing) in furn]
    return cells, idoors, furn


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

    plan = {}
    content_cells = set()
    center = (n_grid // 2, n_grid // 2)
    plan[center] = "open"

    def place_content(kind):
        for c in all_cells:
            if c in plan:
                continue
            if any(n in content_cells for n in _neighbors(c, n_grid)):
                continue
            plan[c] = kind
            content_cells.add(c)
            return True
        return False

    required_kinds = sorted({t.kind for t in templates if t.required})
    for kind in required_kinds:
        place_content(kind)

    for kind in ("forest", "alley"):
        if kind in by_kind:
            place_content(kind)

    extra_pool = [k for k in by_kind if k not in required_kinds and k not in ("open", "forest", "alley")]
    for _ in range(rng.randint(2, 3)):
        if not extra_pool:
            break
        place_content(rng.choice(extra_pool))

    for c in all_cells:
        plan.setdefault(c, "open")

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
                "furniture": [(fkind, zx0 + lx, zy0 + ly, facing) for (fkind, lx, ly, facing) in furn],
            })
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
