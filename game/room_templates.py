import glob
import json
import math
import os
from collections import deque

from game import settings as S

_OPPOSITE = {"N": "S", "S": "N", "E": "W", "W": "E"}
ROOM_DATA_DIR = os.path.join(os.path.dirname(__file__), "room_data")


class RoomTemplate:
    def __init__(self, id, kind, w, h, doors, weight=1.0, required=False,
                 cells=None, door_cells=None, furniture=None, door_kinds=None, interior_doors=None):
        self.id = id
        self.kind = kind
        self.w = w
        self.h = h
        self.doors = frozenset(doors)
        self.weight = weight
        self.required = required
        self.cells = cells if cells is not None else _hollow_cells(w, h)
        self.door_cells = dict(door_cells) if door_cells else {}
        self.furniture = list(furniture) if furniture else []
        self.door_kinds = dict(door_kinds) if door_kinds else {}
        self.interior_doors = list(interior_doors) if interior_doors else []

    def door_local(self, side):
        if side in self.door_cells:
            return self.door_cells[side]
        return _formula_door_local(self, side)

    def door_kind(self, side):
        return self.door_kinds.get(side, "passage")


def _formula_door_local(t, side):
    if side == "N":
        return (t.w // 2, 0)
    if side == "S":
        return (t.w // 2, t.h - 1)
    if side == "W":
        return (0, t.h // 2)
    return (t.w - 1, t.h // 2)


def _hollow_cells(w, h):
    return [[S.FLOOR if (0 < lx < w - 1 and 0 < ly < h - 1) else S.WALL_CONCRETE
             for lx in range(w)] for ly in range(h)]


def door_facing(side):
    return 0.0 if side in ("E", "W") else math.pi / 2


_DOOR_KIND_PRIORITY = ("door", "window", "broken", "random", "passage")


def _merge_door_kind(a, b):
    for kind in _DOOR_KIND_PRIORITY:
        if kind in (a, b):
            return kind
    return "passage"


def _rects_touch_or_overlap(a, b):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = ix1 - ix0, iy1 - iy0
    if iw <= 0 or ih <= 0:
        return False
    return iw > 1 and ih > 1


def _rect_of(t, ox, oy):
    return (ox, oy, ox + t.w, oy + t.h)


def _load_hand_authored(floor_key):
    rooms = []
    for path in sorted(glob.glob(os.path.join(ROOM_DATA_DIR, "*.json"))):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data["floor"] != floor_key:
            continue
        doors = data["doors"]
        door_cells, door_kinds = {}, {}
        for side, info in doors.items():
            if isinstance(info, dict):
                door_cells[side] = tuple(info["cell"])
                door_kinds[side] = info.get("kind", "passage")
            else:
                door_cells[side] = tuple(info)
                door_kinds[side] = "passage"
        rooms.append(RoomTemplate(
            data["id"], data["kind"], data["w"], data["h"], set(doors.keys()),
            weight=data.get("weight", 1.0), required=data.get("required", False),
            cells=data["cells"], door_cells=door_cells, door_kinds=door_kinds,
            furniture=[tuple(item) for item in data.get("furniture", [])],
            interior_doors=[tuple(d) for d in data.get("interior_doors", [])],
        ))
    return rooms


TEMPLATE_SETS = {
    "upper": lambda: _load_hand_authored("upper"),
    "basement": lambda: _load_hand_authored("basement"),
}
STRUCTURAL_KINDS = {"upper": {"corridor"}, "basement": {"tech_corridor", "vent"}}


def _compatible(a_kind, b_kind, structural):
    return a_kind in structural or b_kind in structural


def _weighted_order(rng, cands):
    def key(c):
        u = max(rng.random(), 1e-9)
        return u ** (1.0 / max(c.weight, 1e-6))
    return sorted(cands, key=key, reverse=True)


class _GenFailed(Exception):
    pass


def _prefer_non_repeat(cands, parent_id):
    non_repeat = [c for c in cands if c.id != parent_id]
    return non_repeat if non_repeat else cands


def _attempt(rng, floor_key, target_rooms):
    templates = TEMPLATE_SETS[floor_key]()
    structural = STRUCTURAL_KINDS[floor_key]
    by_kind = {}
    for t in templates:
        by_kind.setdefault(t.kind, []).append(t)

    def weighted_choice(cands):
        total = sum(t.weight for t in cands)
        r = rng.uniform(0, total)
        acc = 0.0
        for t in cands:
            acc += t.weight
            if r <= acc:
                return t
        return cands[-1]

    start_kind = "stairwell" if floor_key == "basement" else "entrance"
    if start_kind not in by_kind:
        raise _GenFailed(f"no {start_kind} template available")
    start_t = weighted_choice(by_kind[start_kind])
    origin = (100, 100)
    placed = [(_rect_of(start_t, *origin), start_t, origin)]
    rooms = [{"template": start_t, "origin": origin}]
    doors_made = []
    dead_ends = []
    resolved_doors = set()

    queue = deque()
    queue.append((0, start_t, origin))
    kinds_spawned = {start_t.kind}

    while queue and len(rooms) < target_rooms:
        ridx, t, o = queue.popleft()
        sides = list(t.doors)
        rng.shuffle(sides)
        for side in sides:
            if len(rooms) >= target_rooms:
                break
            lx, ly = t.door_local(side)
            world_cell = (o[0] + lx, o[1] + ly)
            continue_chance = 0.96 - 0.25 * (len(rooms) / target_rooms)
            if rng.random() > continue_chance and not (t.required and len(rooms) < 2):
                dead_ends.append((ridx, side, world_cell))
                continue
            opp = _OPPOSITE[side]
            cands = [c for c in templates if opp in c.doors and _compatible(t.kind, c.kind, structural)
                     and not (c.required and c.kind in kinds_spawned)]
            cands = _weighted_order(rng, _prefer_non_repeat(cands, t.id))
            placed_ok = False
            for cand in cands:
                clx, cly = cand.door_local(opp)
                new_o = (world_cell[0] - clx, world_cell[1] - cly)
                rect = _rect_of(cand, *new_o)
                if rect[0] < 2 or rect[1] < 2 or rect[2] > 195 or rect[3] > 195:
                    continue
                if any(_rects_touch_or_overlap(rect, r) for r, _, _ in placed):
                    continue
                placed.append((rect, cand, new_o))
                rooms.append({"template": cand, "origin": new_o})
                doors_made.append((world_cell, door_facing(side), _merge_door_kind(t.door_kind(side), cand.door_kind(opp))))
                resolved_doors.add((ridx, side))
                resolved_doors.add((len(rooms) - 1, opp))
                kinds_spawned.add(cand.kind)
                queue.append((len(rooms) - 1, cand, new_o))
                placed_ok = True
                break
            if not placed_ok:
                dead_ends.append((ridx, side, world_cell))

    while queue:
        ridx, t, o = queue.popleft()
        for side in t.doors:
            lx, ly = t.door_local(side)
            dead_ends.append((ridx, side, (o[0] + lx, o[1] + ly)))

    def try_attach(dead_end, cand_pool):
        ridx, side, world_cell = dead_end
        opp = _OPPOSITE[side]
        parent_t = rooms[ridx]["template"]
        for cand in _prefer_non_repeat(cand_pool, parent_t.id):
            if opp not in cand.doors or not _compatible(parent_t.kind, cand.kind, structural):
                continue
            clx, cly = cand.door_local(opp)
            new_o = (world_cell[0] - clx, world_cell[1] - cly)
            rect = _rect_of(cand, *new_o)
            if rect[0] < 2 or rect[1] < 2 or rect[2] > 195 or rect[3] > 195:
                continue
            if any(_rects_touch_or_overlap(rect, r) for r, _, _ in placed):
                continue
            placed.append((rect, cand, new_o))
            rooms.append({"template": cand, "origin": new_o})
            parent_kind = rooms[ridx]["template"].door_kind(side)
            doors_made.append((world_cell, door_facing(side), _merge_door_kind(parent_kind, cand.door_kind(opp))))
            resolved_doors.add((ridx, side))
            resolved_doors.add((len(rooms) - 1, opp))
            kinds_spawned.add(cand.kind)
            return True
        return False

    force_required = [k for k in ("exit", "unlocker") if k not in kinds_spawned]
    if floor_key == "basement" and "stairwell" not in kinds_spawned:
        force_required.insert(0, "stairwell")
    for req_kind in force_required:
        attached = False
        rng.shuffle(dead_ends)
        for dead_end in list(dead_ends):
            if try_attach(dead_end, by_kind.get(req_kind, [])):
                dead_ends.remove(dead_end)
                attached = True
                break
        if not attached:
            raise _GenFailed(f"no {req_kind} could be attached")

    content_kinds = set(by_kind) - structural - {"entrance", "stairwell", "exit", "unlocker"}
    content_pool = [t for k in content_kinds for t in by_kind[k]]
    have_content = len(kinds_spawned & content_kinds)
    rng.shuffle(dead_ends)
    for dead_end in list(dead_ends):
        if have_content >= 2:
            break
        if try_attach(dead_end, content_pool):
            dead_ends.remove(dead_end)
            have_content = len(kinds_spawned & content_kinds)

    fill_pool = content_pool + [t for k in structural for t in by_kind.get(k, [])]
    rng.shuffle(dead_ends)
    for dead_end in list(dead_ends):
        if try_attach(dead_end, fill_pool):
            dead_ends.remove(dead_end)

    closure_pool = sorted(fill_pool, key=lambda t: t.w * t.h)
    for dead_end in list(dead_ends):
        if try_attach(dead_end, closure_pool):
            dead_ends.remove(dead_end)

    extra_open = []
    _connect_adjacent_rooms(rooms, resolved_doors, doors_made)
    _connect_touching_rooms(rooms, resolved_doors, doors_made, extra_open, structural, rng)

    if len(rooms) < min(10, target_rooms - 4):
        raise _GenFailed("too few rooms")
    if not (kinds_spawned & content_kinds):
        raise _GenFailed("no furnished landmark room")

    return rooms, doors_made, resolved_doors, extra_open


def _connect_adjacent_rooms(rooms, resolved_doors, doors_made):
    open_slots = {}
    for ridx, r in enumerate(rooms):
        t = r["template"]
        ox, oy = r["origin"]
        for side in t.doors:
            if (ridx, side) in resolved_doors:
                continue
            lx, ly = t.door_local(side)
            open_slots.setdefault((ox + lx, oy + ly), []).append((ridx, side))

    for world_cell, slots in open_slots.items():
        for i in range(len(slots)):
            ridx_a, side_a = slots[i]
            if (ridx_a, side_a) in resolved_doors:
                continue
            for j in range(i + 1, len(slots)):
                ridx_b, side_b = slots[j]
                if (ridx_b, side_b) in resolved_doors or side_b != _OPPOSITE[side_a]:
                    continue
                kind_a = rooms[ridx_a]["template"].door_kind(side_a)
                kind_b = rooms[ridx_b]["template"].door_kind(side_b)
                doors_made.append((world_cell, door_facing(side_a), _merge_door_kind(kind_a, kind_b)))
                resolved_doors.add((ridx_a, side_a))
                resolved_doors.add((ridx_b, side_b))
                break


def _edge_touch(rect_a, rect_b):
    ax0, ay0, ax1, ay1 = rect_a
    bx0, by0, bx1, by1 = rect_b
    if ax1 == bx0:
        lo, hi = max(ay0, by0), min(ay1, by1)
        if hi > lo:
            return "E", lo, hi
    if bx1 == ax0:
        lo, hi = max(ay0, by0), min(ay1, by1)
        if hi > lo:
            return "W", lo, hi
    if ay1 == by0:
        lo, hi = max(ax0, bx0), min(ax1, bx1)
        if hi > lo:
            return "S", lo, hi
    if by1 == ay0:
        lo, hi = max(ax0, bx0), min(ax1, bx1)
        if hi > lo:
            return "N", lo, hi
    return None


def _side_local_cell(t, side, along):
    if side in ("E", "W"):
        return (t.w - 1 if side == "E" else 0, along)
    return (along, t.h - 1 if side == "S" else 0)


def _side_interior_cell(t, side, along):
    if side in ("E", "W"):
        return (t.w - 2 if side == "E" else 1, along)
    return (along, t.h - 2 if side == "S" else 1)


def _world_along(side, origin, local_along):
    return origin[1] + local_along if side in ("E", "W") else origin[0] + local_along


def _local_along_from_world(side, origin, world_along):
    return world_along - origin[1] if side in ("E", "W") else world_along - origin[0]


def _furniture_free(t, cells):
    occupied = {(lx, ly) for (_kind, lx, ly, _facing) in t.furniture}
    return not (occupied & set(cells))


def _connect_touching_rooms(rooms, resolved_doors, doors_made, extra_open, structural, rng):
    n = len(rooms)
    for a_idx in range(n):
        t_a, o_a = rooms[a_idx]["template"], rooms[a_idx]["origin"]
        rect_a = _rect_of(t_a, *o_a)
        for b_idx in range(a_idx + 1, n):
            t_b, o_b = rooms[b_idx]["template"], rooms[b_idx]["origin"]
            rect_b = _rect_of(t_b, *o_b)
            touch = _edge_touch(rect_a, rect_b)
            if touch is None:
                continue
            side_a, lo, hi = touch
            side_b = _OPPOSITE[side_a]
            if not _compatible(t_a.kind, t_b.kind, structural):
                continue

            def candidate_from(owner_idx, owner_t, owner_o, owner_side, other_idx, other_t, other_o, other_side):
                if owner_side not in owner_t.doors or (owner_idx, owner_side) in resolved_doors:
                    return None
                lx, ly = owner_t.door_local(owner_side)
                along = ly if owner_side in ("E", "W") else lx
                world_along = _world_along(owner_side, owner_o, along)
                if not (lo <= world_along < hi):
                    return None
                other_along = _local_along_from_world(other_side, other_o, world_along)
                other_span = other_t.h if other_side in ("E", "W") else other_t.w
                if not (0 <= other_along < other_span):
                    return None
                owner_border = _side_local_cell(owner_t, owner_side, along)
                owner_inner = _side_interior_cell(owner_t, owner_side, along)
                other_border = _side_local_cell(other_t, other_side, other_along)
                other_inner = _side_interior_cell(other_t, other_side, other_along)
                if owner_t.cells[owner_inner[1]][owner_inner[0]] != S.FLOOR:
                    return None
                if other_t.cells[other_inner[1]][other_inner[0]] != S.FLOOR:
                    return None
                if not _furniture_free(owner_t, (owner_border, owner_inner)):
                    return None
                if not _furniture_free(other_t, (other_border, other_inner)):
                    return None
                world_cell = (owner_o[0] + owner_border[0], owner_o[1] + owner_border[1])
                return dict(world_along=world_along, owner_idx=owner_idx, owner_side=owner_side,
                            other_idx=other_idx, owner_border=owner_border, other_border=other_border,
                            world_cell=world_cell, kind=owner_t.door_kind(owner_side))

            cand_a = candidate_from(a_idx, t_a, o_a, side_a, b_idx, t_b, o_b, side_b)
            cand_b = candidate_from(b_idx, t_b, o_b, side_b, a_idx, t_a, o_a, side_a)
            if (cand_a is not None or cand_b is not None) and rng.random() >= S.ROOM_TOUCH_CONNECT_CHANCE:
                continue
            if cand_a is not None and cand_b is not None and cand_a["world_along"] == cand_b["world_along"]:
                cand_a["kind"] = _merge_door_kind(cand_a["kind"], cand_b["kind"])
                cand_b = None
            chosen = [c for c in (cand_a, cand_b) if c is not None]
            if len(chosen) == 2 and rng.random() >= 0.35:
                chosen = [rng.choice(chosen)]
            for c in chosen:
                extra_open.append((c["owner_idx"], c["owner_border"], c["kind"]))
                extra_open.append((c["other_idx"], c["other_border"], "passage"))
                resolved_doors.add((c["owner_idx"], c["owner_side"]))
                doors_made.append((c["world_cell"], door_facing(c["owner_side"]), c["kind"]))


def generate(rng, floor_key, target_rooms):
    for _ in range(40):
        try:
            rooms, doors_made, resolved_doors, extra_open = _attempt(rng, floor_key, target_rooms)
            break
        except _GenFailed:
            continue
    else:
        return None

    min_x = min(o[0] for r in rooms for o in (r["origin"],))
    min_y = min(o[1] for r in rooms for o in (r["origin"],))
    max_x = max(o[0] + r["template"].w for r in rooms for o in (r["origin"],))
    max_y = max(o[1] + r["template"].h for r in rooms for o in (r["origin"],))
    pad = 2
    gw = (max_x - min_x) + pad * 2
    gh = (max_y - min_y) + pad * 2
    if gw % 2 == 0:
        gw += 1
    if gh % 2 == 0:
        gh += 1
    shift_x, shift_y = pad - min_x, pad - min_y

    window_cells = {(c[0] + shift_x, c[1] + shift_y) for c, facing, kind in doors_made if kind == "window"}

    extra_by_room = {}
    for ridx, local_cell, kind in extra_open:
        extra_by_room.setdefault(ridx, {})[local_cell] = kind

    grid = [[S.WALL_CONCRETE for _ in range(gw)] for _ in range(gh)]
    room_dicts = []
    for r_idx, r in enumerate(rooms):
        t = r["template"]
        ox, oy = r["origin"][0] + shift_x, r["origin"][1] + shift_y
        door_local_cells = {t.door_local(side) for side in t.doors}
        resolved_local_cells = {t.door_local(side) for side in t.doors if (r_idx, side) in resolved_doors}
        unresolved_local_cells = door_local_cells - resolved_local_cells
        interior_window_cells = {(lx, ly) for lx, ly, facing, kind in t.interior_doors if kind == "window"}
        extra_here = extra_by_room.get(r_idx, {})
        for ly in range(t.h):
            for lx in range(t.w):
                wx, wy = ox + lx, oy + ly
                if grid[wy][wx] == S.FLOOR:
                    continue
                if (lx, ly) in extra_here:
                    grid[wy][wx] = S.WALL_WINDOW if extra_here[(lx, ly)] == "window" else S.FLOOR
                elif (lx, ly) in unresolved_local_cells:
                    grid[wy][wx] = S.WALL_CONCRETE
                elif (lx, ly) in interior_window_cells:
                    grid[wy][wx] = S.WALL_WINDOW
                elif (lx, ly) in resolved_local_cells:
                    grid[wy][wx] = S.WALL_WINDOW if (wx, wy) in window_cells else S.FLOOR
                else:
                    grid[wy][wx] = t.cells[ly][lx]
        room_dicts.append({
            "rect": (ox, oy, ox + t.w, oy + t.h),
            "kind": t.kind,
            "furniture": [
                {"kind": kind, "cell": (ox + lx, oy + ly), "facing": facing}
                for (kind, lx, ly, facing) in t.furniture
                if (lx, ly) not in door_local_cells
            ],
            "interior_doors": [
                {"cell": (ox + lx, oy + ly), "facing": facing, "kind": kind}
                for (lx, ly, facing, kind) in t.interior_doors
                if (lx, ly) not in door_local_cells
            ],
        })

    template_doors = [((c[0] + shift_x, c[1] + shift_y), facing, kind) for c, facing, kind in doors_made]

    start_room = room_dicts[0]
    srx0, sry0, srx1, sry1 = start_room["rect"]
    start = ((srx0 + srx1) / 2.0, (sry0 + sry1) / 2.0)

    return gw, gh, grid, room_dicts, template_doors, start
