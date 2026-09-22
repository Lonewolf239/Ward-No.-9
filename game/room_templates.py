import glob
import json
import math
import os
from collections import deque

from game import props
from game import settings as S

_OPPOSITE = {"N": "S", "S": "N", "E": "W", "W": "E"}
ROOM_DATA_DIR = os.path.join(os.path.dirname(__file__), "room_data")


class RoomTemplate:
    def __init__(self, id, kind, w, h, doors, weight=1.0, required=False,
                 cells=None, door_cells=None, furniture=None, door_kinds=None, interior_doors=None,
                 mirror=True):
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
        self.mirror = mirror
        self.rot = 0
        self.mirrored = False

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


DOOR_KINDS = ("door", "broken", "random", "passage")
_DOOR_KIND_PRIORITY = ("door", "broken", "random", "passage")


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
        kinds_used = list(door_kinds.values()) + [d[3] for d in data.get("interior_doors", [])]
        unknown = sorted({k for k in kinds_used if k not in DOOR_KINDS})
        if unknown:
            raise ValueError(f"{os.path.basename(path)}: unknown door kind(s) {unknown}; "
                             f"allowed {DOOR_KINDS} (windows are the wall material S.WALL_WINDOW)")
        rooms.append(RoomTemplate(
            data["id"], data["kind"], data["w"], data["h"], set(doors.keys()),
            weight=data.get("weight", 1.0), required=data.get("required", False),
            cells=data["cells"], door_cells=door_cells, door_kinds=door_kinds,
            furniture=[props.furniture_place(item) for item in data.get("furniture", [])],
            interior_doors=[tuple(d) for d in data.get("interior_doors", [])],
            mirror=data.get("mirror", True),
        ))
    return rooms


_ROT_SIDE = {"N": "E", "E": "S", "S": "W", "W": "N"}


def _rotate_cells_cw(cells, w, h):
    out = [[S.WALL_CONCRETE] * h for _ in range(w)]
    for ly in range(h):
        for lx in range(w):
            out[lx][h - 1 - ly] = cells[ly][lx]
    return out


def _turn_furniture(item, point, face):
    kind, x, y, z, facing = props.furniture_place(item)
    nx, ny = point(x, y)
    return (kind, nx, ny, z, face(facing) % math.tau)


def _rotated_cw(t):
    w, h = t.w, t.h

    def cell(lx, ly):
        return (h - 1 - ly, lx)

    door_cells, door_kinds = {}, {}
    for side in t.doors:
        lx, ly = t.door_local(side)
        door_cells[_ROT_SIDE[side]] = cell(lx, ly)
        door_kinds[_ROT_SIDE[side]] = t.door_kind(side)
    return RoomTemplate(
        t.id, t.kind, h, w, {_ROT_SIDE[s] for s in t.doors},
        weight=t.weight, required=t.required,
        cells=_rotate_cells_cw(t.cells, w, h),
        door_cells=door_cells, door_kinds=door_kinds,
        furniture=[_turn_furniture(item, lambda x, y: (h - y, x),
                                   lambda f: f + math.pi / 2)
                   for item in t.furniture],
        interior_doors=[cell(lx, ly) + ((facing + math.pi / 2) % math.tau, kind)
                        for (lx, ly, facing, kind) in t.interior_doors],
    )


_MIRROR_SIDE = {"E": "W", "W": "E", "N": "N", "S": "S"}


def _mirrored(t):
    w = t.w

    def cell(lx, ly):
        return (w - 1 - lx, ly)

    def face(f):
        return (math.pi - f) % math.tau

    door_cells, door_kinds = {}, {}
    for side in t.doors:
        lx, ly = t.door_local(side)
        door_cells[_MIRROR_SIDE[side]] = cell(lx, ly)
        door_kinds[_MIRROR_SIDE[side]] = t.door_kind(side)
    return RoomTemplate(
        t.id, t.kind, t.w, t.h, {_MIRROR_SIDE[s] for s in t.doors},
        weight=t.weight, required=t.required,
        cells=[list(reversed(row)) for row in t.cells],
        door_cells=door_cells, door_kinds=door_kinds,
        furniture=[_turn_furniture(item, lambda x, y: (t.w - x, y), face)
                   for item in t.furniture],
        interior_doors=[cell(lx, ly) + (face(f), kind) for (lx, ly, f, kind) in t.interior_doors],
        mirror=t.mirror,
    )


def _with_orientations(templates):
    out = []
    for t in templates:
        bases = [(t, False)] + ([(_mirrored(t), True)] if t.mirror else [])
        for base, flipped in bases:
            r = base
            for turn in range(4):
                r.rot = turn
                r.mirrored = flipped
                out.append(r)
                r = _rotated_cw(r)
    return out


_TEMPLATE_CACHE = {}


def template_set(floor_key):
    if floor_key not in _TEMPLATE_CACHE:
        _TEMPLATE_CACHE[floor_key] = _with_orientations(_load_hand_authored(floor_key))
    return _TEMPLATE_CACHE[floor_key]


TEMPLATE_SETS = {
    "upper": lambda: template_set("upper"),
    "basement": lambda: template_set("basement"),
}
STRUCTURAL_KINDS = {"upper": {"corridor"}, "basement": {"tech_corridor", "vent"}}


def _compatible(a_kind, b_kind, structural):
    return a_kind in structural or b_kind in structural


def _room_to_breathe(cand, origin, placed, docked_side, want=2):
    free = 1
    for side in sorted(cand.doors):
        if side == docked_side:
            continue
        lx, ly = cand.door_local(side)
        dx, dy = _SIDE_STEP[side]
        cell = (origin[0] + lx + dx, origin[1] + ly + dy)
        if any(r[0] <= cell[0] < r[2] and r[1] <= cell[1] < r[3] for r, _t, _o in placed):
            continue
        free += 1
        if free >= want:
            return True
    return free >= want


def _weighted_order(rng, cands, weight_of=None):
    weight_of = weight_of or (lambda c: c.weight)

    def key(c):
        u = max(rng.random(), 1e-9)
        return u ** (1.0 / max(weight_of(c), 1e-6))
    return sorted(cands, key=key, reverse=True)


def _landmark_factor(cand, placed_count, target_rooms):
    if cand.w * cand.h < S.LANDMARK_MIN_CELLS:
        return 1.0
    young = placed_count <= max(1, int(target_rooms * S.LANDMARK_EARLY_FRACTION))
    return S.LANDMARK_EARLY_BOOST if young else 1.0 / S.LANDMARK_EARLY_BOOST


def _kind_factor(kind, kind_counts, caps=None, bite=None):
    caps = caps or {}
    cap = caps.get(kind, S.ROOM_KIND_SOFT_CAP.get(kind, S.ROOM_KIND_CAP_DEFAULT))
    over = kind_counts.get(kind, 0) - cap + 1
    if over <= 0:
        return 1.0
    k = bite if (bite is not None and kind in caps) else S.ROOM_KIND_OVER_CAP
    return k ** over


class _GenFailed(Exception):
    pass


def _prefer_non_repeat(cands, parent_id):
    non_repeat = [c for c in cands if c.id != parent_id]
    return non_repeat if non_repeat else cands


def _attempt(rng, floor_key, target_rooms, anomaly=None):
    templates = TEMPLATE_SETS[floor_key]()
    structural = STRUCTURAL_KINDS[floor_key]
    _anom = S.anomaly_of(anomaly or S.ANOMALY_DEFAULT_STAGE)
    kind_caps = _anom.get("caps", {})
    kind_bite = _anom.get("bite")
    extra_wing_kinds = _anom.get("wing_kinds", {})
    wing_mix = _anom.get("wing_mix", 1.0)
    echo_left = 1 if _anom.get("echo_room") else 0
    echo_seed = {}
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
    kind_counts = {start_t.kind: 1}
    runs = {0: 1 if start_t.kind in structural else 0}

    wings = S.FLOOR_WINGS.get(floor_key, ())
    wing_of = {0: 0}
    wing_left = set()
    wing_size = {0: 1}

    content_of = {0: 0}

    hub_kinds = {}

    def _fits(a, b):
        if a in S.ROOM_NEIGHBOURS_ANY or b in S.ROOM_NEIGHBOURS_ANY:
            return True
        return b in S.ROOM_NEIGHBOURS.get(a, ()) or a in S.ROOM_NEIGHBOURS.get(b, ())

    def neighbour_factor(cand, parent_idx):
        if cand.kind in structural:
            return 1.0
        w = 1.0
        for kind in hub_kinds.get(parent_idx, ()):
            w *= S.ROOM_NEIGHBOUR_BONUS if _fits(cand.kind, kind) else S.ROOM_STRANGER_PENALTY
        anc = content_of.get(parent_idx)
        if anc is not None and _fits(cand.kind, rooms[anc]["template"].kind):
            w *= S.ROOM_NEIGHBOUR_BONUS
        return max(0.05, min(20.0, w))

    pending_wing = {}

    def wing_for_side(parent_idx, side):
        key = (parent_idx, side)
        if key not in pending_wing:
            pending_wing[key] = wing_for(parent_idx, None)
        return pending_wing[key]

    def wing_for(parent_idx, cand):
        w = wing_of.get(parent_idx, 0)
        if len(wings) < 2:
            return w
        parent_kind = rooms[parent_idx]["template"].kind
        if parent_kind not in structural or wing_size.get(w, 0) < S.WING_MIN_ROOMS:
            return w
        total = max(1, sum(wing_size.values()))
        share = wing_size.get(w, 0) / float(total)
        fair = 1.0 / len(wings)
        over = max(0.0, share - fair)
        chance = min(0.95, S.WING_SWITCH_CHANCE
                     * (wing_mix * (0.5 + share * len(wings))
                        + over * len(wings) * S.WING_OVERSIZE_PUSH))
        unused_now = [i for i in range(len(wings)) if i not in wing_size]
        if unused_now and len(rooms) > target_rooms * S.WING_ALL_BY_FRACTION:
            chance = 0.95
        if rng.random() >= chance:
            return w
        unused = [i for i in range(len(wings)) if i not in wing_size]
        if unused:
            wing_left.add(w)
            return rng.choice(unused)
        others = [i for i in range(len(wings)) if i != w]
        return min(others, key=lambda i: wing_size.get(i, 0))

    def wing_allows(cand, wing_idx):
        if not wings:
            return True
        if cand.kind in structural or cand.required or cand.kind in S.WING_ANY_KINDS:
            return True
        w = wings[wing_idx]
        return (cand.kind in w["kinds"]
                or cand.kind in extra_wing_kinds.get(w["key"], ()))

    def wing_filter(cands, wing_idx, parent_idx, side):
        kept = [c for c in cands if wing_allows(c, wing_idx)]
        if kept:
            return kept, wing_idx
        home = wing_of.get(parent_idx, 0)
        if home != wing_idx:
            kept = [c for c in cands if wing_allows(c, home)]
            if kept:
                pending_wing[(parent_idx, side)] = home
                return kept, home
        return cands, wing_idx

    spine_sides = list(sorted(start_t.doors)) or ["E"]
    rng.shuffle(spine_sides)
    spine_dir = spine_sides[0]
    spine_left = rng.randint(*S.SPINE_CORRIDORS)
    spine_rooms = {0}

    def straight_through(cand, into_side):
        opp = _OPPOSITE[into_side]
        if into_side not in cand.doors or opp not in cand.doors:
            return False
        a, b = cand.door_local(opp), cand.door_local(into_side)
        return (a[1] == b[1]) if into_side in ("E", "W") else (a[0] == b[0])

    def on_spine(parent_idx, side):
        return (parent_idx in spine_rooms and spine_left > 0
                and side in (spine_dir, _OPPOSITE[spine_dir]))

    def may_follow(parent_idx, cand, side=None):
        if side is not None and on_spine(parent_idx, side) and cand.kind in structural:
            return straight_through(cand, side)
        return cand.kind not in structural or runs.get(parent_idx, 0) < S.MAX_CORRIDOR_RUN

    def note_placed(parent_idx, cand, side=None):
        nonlocal spine_left
        idx = len(rooms) - 1
        if side is not None and on_spine(parent_idx, side) and cand.kind in structural:
            spine_rooms.add(idx)
            spine_left -= 1
            runs[idx] = 0
            kind_counts[cand.kind] = kind_counts.get(cand.kind, 0) + 1
            content_of[idx] = content_of.get(parent_idx)
            w = wing_for_side(parent_idx, side) if side is not None else wing_of.get(parent_idx, 0)
            wing_of[idx] = w
            wing_size[w] = wing_size.get(w, 0) + 1
            return
        runs[idx] = runs.get(parent_idx, 0) + 1 if cand.kind in structural else 0
        kind_counts[cand.kind] = kind_counts.get(cand.kind, 0) + 1
        content_of[idx] = idx if cand.kind not in structural else content_of.get(parent_idx)
        if cand.kind not in structural:
            hub_kinds.setdefault(parent_idx, []).append(cand.kind)
        w = wing_for_side(parent_idx, side) if side is not None else wing_of.get(parent_idx, 0)
        wing_of[idx] = w
        wing_size[w] = wing_size.get(w, 0) + 1

    exit_room_idx = [None]

    queue = deque()
    queue.append((0, start_t, origin))
    kinds_spawned = {start_t.kind}

    while queue and len(rooms) < target_rooms:
        ridx, t, o = queue.popleft()
        sides = sorted(t.doors)
        rng.shuffle(sides)
        if ridx in spine_rooms:
            for want in (spine_dir, _OPPOSITE[spine_dir]):
                if want in sides:
                    sides.remove(want)
                    sides.insert(0, want)
        for side in sides:
            if len(rooms) >= target_rooms:
                break
            lx, ly = t.door_local(side)
            world_cell = (o[0] + lx, o[1] + ly)
            continue_chance = 0.96 - 0.25 * (len(rooms) / target_rooms)
            if (rng.random() > continue_chance and not (t.required and len(rooms) < 2)
                    and not on_spine(ridx, side)):
                dead_ends.append((ridx, side, world_cell))
                continue
            opp = _OPPOSITE[side]
            cands = [c for c in templates if opp in c.doors and _compatible(t.kind, c.kind, structural)
                     and not c.required and may_follow(ridx, c, side)]
            if on_spine(ridx, side):
                straight = [c for c in cands if c.kind in structural and straight_through(c, side)]
                if straight:
                    span = (lambda c: c.w) if side in ("E", "W") else (lambda c: c.h)
                    cands = sorted(straight, key=span, reverse=True)
            here = wing_for_side(ridx, side)
            cands, here = wing_filter(cands, here, ridx, side)
            twin = None
            if echo_left and len(rooms) > 3:
                seeded = echo_seed.get(ridx)
                if seeded is None and (t.kind not in structural
                                       and t.w * t.h >= S.ANOMALY_ECHO_MIN_CELLS):
                    seeded = (ridx, t)
                seed_t = seeded[1] if seeded else None
                if seed_t is not None:
                    mirrors = [c for c in cands if c.id == seed_t.id]
                    mirrors.sort(key=lambda c: (c.mirrored == seed_t.mirrored,
                                                c.rot != seed_t.rot))
                    twin = mirrors[0] if mirrors else None
            cands = _weighted_order(rng, _prefer_non_repeat(cands, t.id),
                                    lambda c: (c.weight * _kind_factor(c.kind, kind_counts, kind_caps, kind_bite)
                                               * _landmark_factor(c, len(rooms), target_rooms)
                                               * neighbour_factor(c, ridx)))
            if twin is not None:
                cands = [twin] + [c for c in cands if c is not twin]
            placed_ok = False
            t_along = ly if side in ("E", "W") else lx
            for cand in cands:
                clx, cly = cand.door_local(opp)
                new_o = (world_cell[0] - clx, world_cell[1] - cly)
                rect = _rect_of(cand, *new_o)
                if rect[0] < 2 or rect[1] < 2 or rect[2] > 195 or rect[3] > 195:
                    continue
                if any(_rects_touch_or_overlap(rect, r) for r, _, _ in placed):
                    continue
                cand_along = cly if opp in ("E", "W") else clx
                if not _furniture_free(t, _clearance_cells(t, side, t_along)):
                    continue
                if not _furniture_free(cand, _clearance_cells(cand, opp, cand_along)):
                    continue
                if (cand.kind in S.SECOND_WAY_OUT_KINDS
                        and target_rooms >= S.SECOND_WAY_OUT_MIN_ROOMS
                        and not _room_to_breathe(cand, new_o, placed, opp)):
                    continue
                placed.append((rect, cand, new_o))
                rooms.append({"template": cand, "origin": new_o})
                note_placed(ridx, cand, side)
                doors_made.append((world_cell, door_facing(side), _merge_door_kind(t.door_kind(side), cand.door_kind(opp))))
                resolved_doors.add((ridx, side))
                resolved_doors.add((len(rooms) - 1, opp))
                kinds_spawned.add(cand.kind)
                queue.append((len(rooms) - 1, cand, new_o))
                if cand is twin:
                    echo_left -= 1
                    rooms[-1]["echo"] = True
                    rooms[seeded[0]]["echo"] = True
                elif (cand.kind not in structural
                      and cand.w * cand.h >= S.ANOMALY_ECHO_MIN_CELLS):
                    echo_seed.setdefault(ridx, (len(rooms) - 1, cand))
                placed_ok = True
                break
            if not placed_ok:
                dead_ends.append((ridx, side, world_cell))

    while queue:
        ridx, t, o = queue.popleft()
        for side in sorted(t.doors):
            lx, ly = t.door_local(side)
            dead_ends.append((ridx, side, (o[0] + lx, o[1] + ly)))

    def try_attach(dead_end, cand_pool):
        ridx, side, world_cell = dead_end
        opp = _OPPOSITE[side]
        parent_t = rooms[ridx]["template"]
        plx, ply = parent_t.door_local(side)
        parent_along = ply if side in ("E", "W") else plx
        if not _furniture_free(parent_t, _clearance_cells(parent_t, side, parent_along)):
            return False
        pool = [c for c in cand_pool if may_follow(ridx, c)]
        here = wing_for_side(ridx, side)
        pool, here = wing_filter(pool, here, ridx, side)
        pool = _weighted_order(rng, _prefer_non_repeat(pool, parent_t.id),
                               lambda c: (c.weight * _kind_factor(c.kind, kind_counts, kind_caps, kind_bite)
                                          * neighbour_factor(c, ridx)))
        for cand in pool:
            if opp not in cand.doors or not _compatible(parent_t.kind, cand.kind, structural):
                continue
            clx, cly = cand.door_local(opp)
            new_o = (world_cell[0] - clx, world_cell[1] - cly)
            rect = _rect_of(cand, *new_o)
            if rect[0] < 2 or rect[1] < 2 or rect[2] > 195 or rect[3] > 195:
                continue
            if any(_rects_touch_or_overlap(rect, r) for r, _, _ in placed):
                continue
            cand_along = cly if opp in ("E", "W") else clx
            if not _furniture_free(cand, _clearance_cells(cand, opp, cand_along)):
                continue
            if (cand.kind in S.SECOND_WAY_OUT_KINDS
                    and target_rooms >= S.SECOND_WAY_OUT_MIN_ROOMS
                    and not _room_to_breathe(cand, new_o, placed, opp)):
                continue
            placed.append((rect, cand, new_o))
            rooms.append({"template": cand, "origin": new_o})
            note_placed(ridx, cand, side)
            parent_kind = rooms[ridx]["template"].door_kind(side)
            doors_made.append((world_cell, door_facing(side), _merge_door_kind(parent_kind, cand.door_kind(opp))))
            resolved_doors.add((ridx, side))
            new_idx = len(rooms) - 1
            resolved_doors.add((new_idx, opp))
            kinds_spawned.add(cand.kind)
            for other in sorted(cand.doors):
                if other == opp:
                    continue
                olx, oly = cand.door_local(other)
                dead_ends.append((new_idx, other, (new_o[0] + olx, new_o[1] + oly)))
            return True
        return False

    force_required = [k for k in ("exit", "unlocker") if k not in kinds_spawned]
    if floor_key == "basement" and "stairwell" not in kinds_spawned:
        force_required.insert(0, "stairwell")

    def far_first(from_idx, away_from_wing=None):
        _adj, dist = _room_distances(rooms, placed, doors_made)
        reach = dist.get(from_idx, {})

        def key(de):
            other_wing = (away_from_wing is not None
                          and wing_of.get(de[0], 0) != away_from_wing)
            return (0 if other_wing else 1, -reach.get(de[0], 0))
        return sorted(dead_ends, key=key)

    for req_kind in force_required:
        attached = False
        if req_kind == "exit":
            order = far_first(0)
        elif req_kind == "unlocker" and exit_room_idx[0] is not None:
            order = far_first(exit_room_idx[0],
                              away_from_wing=wing_of.get(exit_room_idx[0], 0))
        else:
            order = list(dead_ends)
            rng.shuffle(order)
        for dead_end in order:
            if req_kind == "unlocker" and exit_room_idx[0] is not None and len(wings) > 1:
                exit_wing = wing_of.get(exit_room_idx[0], 0)
                others = [i for i in range(len(wings)) if i != exit_wing]
                if others:
                    pending_wing[(dead_end[0], dead_end[1])] = rng.choice(others)
            if dead_end in dead_ends and try_attach(dead_end, by_kind.get(req_kind, [])):
                dead_ends.remove(dead_end)
                attached = True
                if req_kind == "exit":
                    exit_room_idx[0] = len(rooms) - 1
                break
        if not attached:
            raise _GenFailed(f"no {req_kind} could be attached")

    content_kinds = set(by_kind) - structural - {"entrance", "stairwell", "exit", "unlocker"}
    content_pool = [t for k in sorted(content_kinds) for t in by_kind[k]]
    have_content = len(kinds_spawned & content_kinds)
    rng.shuffle(dead_ends)
    for dead_end in list(dead_ends):
        if have_content >= 2:
            break
        if try_attach(dead_end, content_pool):
            dead_ends.remove(dead_end)
            have_content = len(kinds_spawned & content_kinds)

    fill_pool = content_pool + [t for k in sorted(structural) for t in by_kind.get(k, [])]
    for dead_end in [d for d in dead_ends
                     if rooms[d[0]]["template"].kind in S.SECOND_WAY_OUT_KINDS]:
        if dead_end in dead_ends and try_attach(dead_end, fill_pool):
            dead_ends.remove(dead_end)
    rng.shuffle(dead_ends)
    for dead_end in list(dead_ends):
        if try_attach(dead_end, fill_pool):
            dead_ends.remove(dead_end)

    closure_pool = sorted(fill_pool, key=lambda t: t.w * t.h)
    for dead_end in list(dead_ends):
        if try_attach(dead_end, closure_pool):
            dead_ends.remove(dead_end)

    extra_open = []
    links = _draw_link_corridors(rng, rooms, placed, dead_ends, resolved_doors, doors_made,
                                 extra_open, structural)
    _connect_adjacent_rooms(rooms, resolved_doors, doors_made)
    _connect_touching_rooms(rooms, resolved_doors, doors_made, extra_open, structural, rng)

    adj_now, _dist_now = _room_distances(rooms, placed, doors_made)
    need_way_out = {i for i, r in enumerate(rooms)
                    if r["template"].kind in S.SECOND_WAY_OUT_KINDS and len(adj_now[i]) < 2}
    if need_way_out:
        _connect_touching_rooms(rooms, resolved_doors, doors_made, extra_open, structural, rng,
                                force_rooms=need_way_out)
        _force_second_way_out(rng, rooms, placed, resolved_doors, doors_made, extra_open, links,
                              structural)

    if target_rooms >= S.SECOND_WAY_OUT_MIN_ROOMS:
        adj_end, dist_end = _room_distances(rooms, placed, doors_made)
        for idx, r in enumerate(rooms):
            if r["template"].kind in S.SECOND_WAY_OUT_KINDS and len(adj_end[idx]) < 2:
                raise _GenFailed("%s has only one way out" % r["template"].kind)
        if exit_room_idx[0] is not None:
            hops = dist_end.get(0, {}).get(exit_room_idx[0])
            if hops is not None and hops < S.EXIT_MIN_HOPS:
                raise _GenFailed("the way out is %d rooms from the way in" % hops)

    if len(rooms) < min(10, target_rooms - 4):
        raise _GenFailed("too few rooms")
    if not (kinds_spawned & content_kinds):
        raise _GenFailed("no furnished landmark room")

    for idx, r in enumerate(rooms):
        r["wing"] = wing_of.get(idx, 0)
        r["spine"] = idx in spine_rooms
    return rooms, doors_made, resolved_doors, extra_open, links


def _merge_wing_islands(rooms):
    owner = {}
    for i, r in enumerate(rooms):
        cells = r.get("cells")
        if cells is None:
            x0, y0, x1, y1 = r["rect"]
            cells = [(x, y) for y in range(y0, y1) for x in range(x0, x1)]
        for c in cells:
            owner[tuple(c)] = i
    neigh = {i: set() for i in range(len(rooms))}
    for (cx, cy), i in owner.items():
        for dx, dy in ((1, 0), (0, 1)):
            j = owner.get((cx + dx, cy + dy))
            if j is not None and j != i:
                neigh[i].add(j)
                neigh[j].add(i)
    for _ in range(4):
        moved = 0
        for i, r in enumerate(rooms):
            mine = r.get("wing")
            if mine is None or not neigh[i]:
                continue
            others = [rooms[j].get("wing") for j in neigh[i]]
            others = [o for o in others if o is not None]
            if not others or mine in others:
                continue
            best = max(set(others), key=others.count)
            r["wing"] = best
            moved += 1
        if not moved:
            break


_SIDE_STEP = {"N": (0, -1), "S": (0, 1), "E": (1, 0), "W": (-1, 0)}


def _route_link(start, goal, blocked, taken, budget):
    import heapq
    lo_x, hi_x = min(start[0], goal[0]) - 6, max(start[0], goal[0]) + 6
    lo_y, hi_y = min(start[1], goal[1]) - 6, max(start[1], goal[1]) + 6
    best = {}
    heap = [(0.0, 0, start, None)]
    came = {}
    while heap:
        cost, steps, cell, indir = heapq.heappop(heap)
        if (cell, indir) in best and best[(cell, indir)] <= cost:
            continue
        best[(cell, indir)] = cost
        if cell == goal:
            path = [cell]
            key = (cell, indir)
            while key in came:
                key = came[key]
                path.append(key[0])
            path.reverse()
            return path
        if steps >= budget:
            continue
        for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nxt = (cell[0] + d[0], cell[1] + d[1])
            if not (lo_x <= nxt[0] <= hi_x and lo_y <= nxt[1] <= hi_y):
                continue
            if not (2 <= nxt[0] < 195 and 2 <= nxt[1] < 195):
                continue
            if nxt in blocked or (nxt != goal and nxt in taken):
                continue
            step_cost = 1.0 + (S.LINK_TURN_COST if indir is not None and d != indir else 0.0)
            ncost = cost + step_cost
            if best.get((nxt, d), 1e18) <= ncost:
                continue
            came[(nxt, d)] = (cell, indir)
            heapq.heappush(heap, (ncost, steps + 1, nxt, d))
    return None


def _room_distances(rooms, placed, doors_made):
    adj = {i: set() for i in range(len(rooms))}
    for cell, _facing, _kind in doors_made:
        here = [i for i, (rect, _t, _o) in enumerate(placed)
                if rect[0] <= cell[0] < rect[2] and rect[1] <= cell[1] < rect[3]]
        for a in here:
            for b in here:
                if a != b:
                    adj[a].add(b)
    dist = {}
    for src in adj:
        seen = {src: 0}
        frontier = deque([src])
        while frontier:
            node = frontier.popleft()
            for nxt in adj[node]:
                if nxt not in seen:
                    seen[nxt] = seen[node] + 1
                    frontier.append(nxt)
        dist[src] = seen
    return adj, dist


_REACHABLE_CACHE = {}


def _reachable_from_doors(t):
    key = (t.id, t.w, t.h, id(t.cells))
    got = _REACHABLE_CACHE.get(key)
    if got is not None:
        return got
    seen = set()
    stack = []
    for side in sorted(t.doors):
        lx, ly = t.door_local(side)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = lx + dx, ly + dy
            if 0 <= nx < t.w and 0 <= ny < t.h and t.cells[ny][nx] == S.FLOOR \
                    and (nx, ny) not in seen:
                seen.add((nx, ny))
                stack.append((nx, ny))
    while stack:
        x, y = stack.pop()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if (0 <= nx < t.w and 0 <= ny < t.h and t.cells[ny][nx] == S.FLOOR
                    and (nx, ny) not in seen):
                seen.add((nx, ny))
                stack.append((nx, ny))
    _REACHABLE_CACHE[key] = seen
    return seen


def _wall_anchors(rng, rooms, placed, blocked, opened, degree, per_room):
    out = []
    for ridx, r in enumerate(rooms):
        if degree.get(ridx, 0) > 2:
            continue
        t, o = r["template"], r["origin"]
        inside = _reachable_from_doors(t)
        occupied = {(int(e[1]), int(e[2])) for e in map(props.furniture_place, t.furniture)}
        occupied |= {(lx, ly) for (lx, ly, _f, _k) in t.interior_doors}
        spots = []
        for side, cells in (
                ("N", [(lx, 0) for lx in range(1, t.w - 1)]),
                ("S", [(lx, t.h - 1) for lx in range(1, t.w - 1)]),
                ("W", [(0, ly) for ly in range(1, t.h - 1)]),
                ("E", [(t.w - 1, ly) for ly in range(1, t.h - 1)])):
            d = _SIDE_STEP[side]
            for (lx, ly) in cells:
                inner = (lx - d[0], ly - d[1])
                if t.cells[ly][lx] != S.WALL_CONCRETE:
                    continue
                if t.cells[inner[1]][inner[0]] != S.FLOOR:
                    continue
                if inner not in inside:
                    continue
                if (lx, ly) in occupied or inner in occupied:
                    continue
                world = (o[0] + lx, o[1] + ly)
                first = (world[0] + d[0], world[1] + d[1])
                if first in blocked:
                    continue
                if world in opened or any((world[0] + ax, world[1] + ay) in opened
                                          for ax, ay in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                    continue
                spots.append({"ridx": ridx, "side": side, "cell": world, "first": first,
                              "local": (lx, ly), "kind": "passage", "cut": True})
        rng.shuffle(spots)
        out.extend(spots[:per_room])
    return out


def _hugs_a_room(route, placed):
    worst = 0
    runs = {}
    for cell in route:
        touching = set()
        for idx, (rect, _t, _o) in enumerate(placed):
            x0, y0, x1, y1 = rect
            if x0 - 1 <= cell[0] <= x1 and y0 - 1 <= cell[1] <= y1:
                touching.add(idx)
        for idx in list(runs):
            if idx not in touching:
                runs.pop(idx)
        for idx in touching:
            runs[idx] = runs.get(idx, 0) + 1
            worst = max(worst, runs[idx])
    return worst


def _carve_link(end, path_taken, resolved_doors, doors_made, extra_open, opened):
    opened.add(end["cell"])
    if end["cut"]:
        extra_open.append((end["ridx"], end["local"], "passage"))
    else:
        resolved_doors.add((end["ridx"], end["side"]))
    doors_made.append((end["cell"], door_facing(end["side"]), end["kind"]))


def _force_second_way_out(rng, rooms, placed, resolved_doors, doors_made, extra_open, links,
                          structural):
    adj, _dist = _room_distances(rooms, placed, doors_made)
    targets = [i for i, r in enumerate(rooms)
               if r["template"].kind in S.SECOND_WAY_OUT_KINDS and len(adj[i]) < 2]
    if not targets:
        return
    blocked = set()
    for rect, _t, _o in placed:
        x0, y0, x1, y1 = rect
        for y in range(y0, y1):
            for x in range(x0, x1):
                blocked.add((x, y))
    opened = set()
    for ridx, r in enumerate(rooms):
        t, o = r["template"], r["origin"]
        for side in sorted(t.doors):
            lx, ly = t.door_local(side)
            opened.add((o[0] + lx, o[1] + ly))
    for cell, _f, _k in doors_made:
        opened.add(tuple(cell))
    taken = set()
    for path in links:
        for c in path:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    taken.add((c[0] + dx, c[1] + dy))

    degree = {i: len(v) for i, v in adj.items()}
    anchors = _wall_anchors(rng, rooms, placed, blocked, opened, degree, S.LINK_CUTS_PER_ROOM)
    for ridx in targets:
        mine = [a for a in anchors if a["ridx"] == ridx]
        theirs = [a for a in anchors if a["ridx"] != ridx and a["ridx"] not in adj[ridx]]
        pairs = sorted(((abs(a["first"][0] - b["first"][0]) + abs(a["first"][1] - b["first"][1]), i, j)
                        for i, a in enumerate(mine) for j, b in enumerate(theirs)),
                       key=lambda pr: pr[0])
        for span, i, j in pairs:
            if span > S.LINK_MAX_CELLS:
                break
            a, b = mine[i], theirs[j]
            if a["first"] in taken or b["first"] in taken:
                continue
            route = _route_link(a["first"], b["first"], blocked, taken, S.LINK_MAX_CELLS)
            if route is None or _hugs_a_room(route, placed) > S.LINK_MAX_HUG:
                continue
            links.append([a["cell"]] + route + [b["cell"]])
            for c in route:
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        taken.add((c[0] + dx, c[1] + dy))
            for end in (a, b):
                _carve_link(end, taken, resolved_doors, doors_made, extra_open, opened)
            break


def _draw_link_corridors(rng, rooms, placed, dead_ends, resolved_doors, doors_made, extra_open,
                         structural):
    blocked = set()
    for rect, _t, _o in placed:
        x0, y0, x1, y1 = rect
        for y in range(y0, y1):
            for x in range(x0, x1):
                blocked.add((x, y))

    opened = set()
    for ridx, r in enumerate(rooms):
        t, o = r["template"], r["origin"]
        for side in sorted(t.doors):
            lx, ly = t.door_local(side)
            opened.add((o[0] + lx, o[1] + ly))
    for cell, _f, _k in doors_made:
        opened.add(tuple(cell))

    adj, dist = _room_distances(rooms, placed, doors_made)
    degree = {i: len(v) for i, v in adj.items()}

    def walk_apart(a_idx, b_idx):
        return dist.get(a_idx, {}).get(b_idx)

    def joined(a_idx, b_idx):
        adj[a_idx].add(b_idx)
        adj[b_idx].add(a_idx)
        for src in adj:
            seen = {src: 0}
            frontier = deque([src])
            while frontier:
                node = frontier.popleft()
                for nxt in adj[node]:
                    if nxt not in seen:
                        seen[nxt] = seen[node] + 1
                        frontier.append(nxt)
            dist[src] = seen

    ends = []
    for ridx, side, world_cell in dead_ends:
        if (ridx, side) in resolved_doors:
            continue
        d = _SIDE_STEP[side]
        first = (world_cell[0] + d[0], world_cell[1] + d[1])
        if first in blocked:
            continue
        ends.append({"ridx": ridx, "side": side, "cell": tuple(world_cell), "first": first,
                     "local": rooms[ridx]["template"].door_local(side),
                     "kind": rooms[ridx]["template"].door_kind(side), "cut": False})
    ends.extend(_wall_anchors(rng, rooms, placed, blocked, opened, degree, S.LINK_CUTS_PER_ROOM))

    pairs = []
    for i in range(len(ends)):
        for j in range(i + 1, len(ends)):
            a, b = ends[i], ends[j]
            if a["ridx"] == b["ridx"]:
                continue
            if (rooms[a["ridx"]]["template"].kind in structural
                    and rooms[b["ridx"]]["template"].kind in structural):
                continue
            span = abs(a["first"][0] - b["first"][0]) + abs(a["first"][1] - b["first"][1])
            if span > S.LINK_MAX_CELLS:
                continue
            apart = walk_apart(a["ridx"], b["ridx"])
            if apart is not None and apart < S.LINK_MIN_SHORTCUT:
                continue
            saved = S.LINK_MAX_CELLS if apart is None else apart
            pairs.append((span - S.LINK_SHORTCUT_WEIGHT * min(saved, 14), span, i, j))
    pairs.sort(key=lambda pr: (pr[0], pr[2], pr[3]))

    taken = set()
    used_ends = set()
    used_rooms = {}
    links = []
    for _score, _span, i, j in pairs:
        if len(links) >= S.LINK_MAX_PER_FLOOR:
            break
        if i in used_ends or j in used_ends:
            continue
        a, b = ends[i], ends[j]
        if used_rooms.get(a["ridx"], 0) >= 2 or used_rooms.get(b["ridx"], 0) >= 2:
            continue
        apart_now = walk_apart(a["ridx"], b["ridx"])
        if apart_now is not None and apart_now < S.LINK_MIN_SHORTCUT:
            continue
        if a["first"] in taken or b["first"] in taken:
            continue
        route = _route_link(a["first"], b["first"], blocked, taken, S.LINK_MAX_CELLS)
        if route is None or _hugs_a_room(route, placed) > S.LINK_MAX_HUG:
            continue
        links.append([a["cell"]] + route + [b["cell"]])
        joined(a["ridx"], b["ridx"])
        used_ends.add(i)
        used_ends.add(j)
        for c in route:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    taken.add((c[0] + dx, c[1] + dy))
        for end in (a, b):
            used_rooms[end["ridx"]] = used_rooms.get(end["ridx"], 0) + 1
            opened.add(end["cell"])
            if end["cut"]:
                extra_open.append((end["ridx"], end["local"], "passage"))
            else:
                resolved_doors.add((end["ridx"], end["side"]))
            doors_made.append((end["cell"], door_facing(end["side"]), end["kind"]))
    return links


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
    occupied = {(int(e[1]), int(e[2])) for e in map(props.furniture_place, t.furniture)}
    return not (occupied & set(cells))


def _clearance_cells(t, side, along):
    return [_side_local_cell(t, side, along), _side_interior_cell(t, side, along)]


def _opened_world_cells(rooms, resolved_doors, doors_made, extra_open):
    cells = set()
    for ridx, r in enumerate(rooms):
        t, o = r["template"], r["origin"]
        for side in t.doors:
            if (ridx, side) in resolved_doors:
                lx, ly = t.door_local(side)
                cells.add((o[0] + lx, o[1] + ly))
    for world_cell, _facing, _kind in doors_made:
        cells.add(tuple(world_cell))
    for ridx, local_cell, _kind in extra_open:
        o = rooms[ridx]["origin"]
        cells.add((o[0] + local_cell[0], o[1] + local_cell[1]))
    return cells


def _connect_touching_rooms(rooms, resolved_doors, doors_made, extra_open, structural, rng,
                            force_rooms=()):
    n = len(rooms)
    opened = _opened_world_cells(rooms, resolved_doors, doors_made, extra_open)
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
                if not _furniture_free(owner_t, _clearance_cells(owner_t, owner_side, along)):
                    return None
                if not _furniture_free(other_t, _clearance_cells(other_t, other_side, other_along)):
                    return None
                world_cell = (owner_o[0] + owner_border[0], owner_o[1] + owner_border[1])
                return dict(world_along=world_along, owner_idx=owner_idx, owner_side=owner_side,
                            other_idx=other_idx, owner_border=owner_border, other_border=other_border,
                            world_cell=world_cell, kind=owner_t.door_kind(owner_side))

            cand_a = candidate_from(a_idx, t_a, o_a, side_a, b_idx, t_b, o_b, side_b)
            cand_b = candidate_from(b_idx, t_b, o_b, side_b, a_idx, t_a, o_a, side_a)
            forced = a_idx in force_rooms or b_idx in force_rooms
            if (not forced and (cand_a is not None or cand_b is not None)
                    and rng.random() >= S.ROOM_TOUCH_CONNECT_CHANCE):
                continue
            if cand_a is not None and cand_b is not None and cand_a["world_along"] == cand_b["world_along"]:
                cand_a["kind"] = _merge_door_kind(cand_a["kind"], cand_b["kind"])
                cand_b = None
            chosen = [c for c in (cand_a, cand_b) if c is not None]
            if len(chosen) == 2 and rng.random() >= 0.35:
                chosen = [rng.choice(chosen)]
            for c in chosen:
                wx, wy = c["world_cell"]
                if any((wx + dx, wy + dy) in opened
                       for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                    continue
                extra_open.append((c["owner_idx"], c["owner_border"], c["kind"]))
                extra_open.append((c["other_idx"], c["other_border"], "passage"))
                resolved_doors.add((c["owner_idx"], c["owner_side"]))
                doors_made.append((c["world_cell"], door_facing(c["owner_side"]), c["kind"]))
                opened.add((wx, wy))
                o_other = rooms[c["other_idx"]]["origin"]
                opened.add((o_other[0] + c["other_border"][0],
                            o_other[1] + c["other_border"][1]))


WALL_DEBRIS_MAX = 12


def _stands_inside_one_room(clump, grid, gw, gh, owners):
    common = None
    for (x, y) in clump:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if not (0 <= nx < gw and 0 <= ny < gh) or grid[ny][nx] != S.FLOOR:
                continue
            here = owners.get((nx, ny))
            if not here:
                return False
            common = here if common is None else (common & here)
            if not common:
                return False
    return common is not None


def _clear_wall_debris(grid, gw, gh, rooms, shift_x, shift_y, room_dicts, door_cells):
    from game.props import PROP_DEFS

    interior = set()
    for r in rooms:
        t, o = r["template"], r["origin"]
        ox, oy = o[0] + shift_x, o[1] + shift_y
        for ly in range(1, t.h - 1):
            for lx in range(1, t.w - 1):
                if t.cells[ly][lx] != S.FLOOR:
                    interior.add((ox + lx, oy + ly))

    owners = {}
    for idx, room in enumerate(room_dicts):
        x0, y0, x1, y1 = room["rect"]
        for cy in range(y0, y1):
            for cx in range(x0, x1):
                if 0 <= cx < gw and 0 <= cy < gh and grid[cy][cx] == S.FLOOR:
                    owners.setdefault((cx, cy), set()).add(idx)

    clumps = []
    seen = set()
    for y in range(1, gh - 1):
        for x in range(1, gw - 1):
            if grid[y][x] == S.FLOOR or (x, y) in seen:
                continue
            stack, clump, touches_edge = [(x, y)], set(), False
            seen.add((x, y))
            while stack:
                cx, cy = stack.pop()
                clump.add((cx, cy))
                if cx in (0, gw - 1) or cy in (0, gh - 1):
                    touches_edge = True
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cx + dx, cy + dy
                    if (0 <= nx < gw and 0 <= ny < gh and grid[ny][nx] != S.FLOOR
                            and (nx, ny) not in seen):
                        seen.add((nx, ny))
                        stack.append((nx, ny))
            if touches_edge or len(clump) > WALL_DEBRIS_MAX or (clump & interior):
                continue
            if not _stands_inside_one_room(clump, grid, gw, gh, owners):
                continue
            clumps.append(clump)
    if not clumps:
        return

    was = {c: grid[c[1]][c[0]] for clump in clumps for c in clump}
    for c in was:
        grid[c[1]][c[0]] = S.FLOOR

    def solid(x, y):
        if not (0 <= x < gw and 0 <= y < gh):
            return True
        return grid[y][x] != S.FLOOR

    taken = {(int(f["pos"][0]), int(f["pos"][1]))
             for room in room_dicts for f in room["furniture"]}
    taken |= {tuple(d["cell"]) for room in room_dicts for d in room["interior_doors"]}

    restore = set()
    for room in room_dicts:
        x0, y0, x1, y1 = room["rect"]
        kept = []
        for f in room["furniture"]:
            defs = PROP_DEFS.get(f["kind"], {})
            fx, fy, fz = f["pos"]
            cell = (int(fx), int(fy))
            if not defs.get("wall_mounted") or any(
                    solid(cell[0] + dx, cell[1] + dy)
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                kept.append(f)
                continue
            seat = None
            best = None
            for cy in range(y0, y1):
                for cx in range(x0, x1):
                    if (cx, cy) in taken or (cx, cy) in door_cells or solid(cx, cy):
                        continue
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        if not solid(cx + dx, cy + dy):
                            continue
                        if (cx + dx, cy + dy) in door_cells:
                            continue
                        d2 = (cx - cell[0]) ** 2 + (cy - cell[1]) ** 2
                        if best is None or d2 < best:
                            best, seat = d2, ((cx, cy), (dx, dy))
                        break
            if seat is not None:
                (cx, cy), (dx, dy) = seat
                taken.discard(cell)
                taken.add((cx, cy))
                facing = math.atan2(-dy, -dx)
                hd = defs.get("hd", 0.2)
                bx, by = cx + 0.5 + dx * 0.5, cy + 0.5 + dy * 0.5
                f = dict(f, pos=(bx - dx * (hd + 0.005), by - dy * (hd + 0.005), fz),
                         facing=facing)
                kept.append(f)
            elif defs.get("interactable"):
                kept.append(f)
                for clump in clumps:
                    if any((cell[0] + dx, cell[1] + dy) in clump
                           for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                        restore |= clump
        room["furniture"] = kept

    for c in restore:
        grid[c[1]][c[0]] = was[c]

    for room in room_dicts:
        room["interior_doors"] = [
            d for d in room["interior_doors"]
            if (solid(d["cell"][0] - 1, d["cell"][1]) and solid(d["cell"][0] + 1, d["cell"][1]))
            or (solid(d["cell"][0], d["cell"][1] - 1) and solid(d["cell"][0], d["cell"][1] + 1))
        ]


def _touches_door(cell, door_cells):
    x, y = cell
    return any((x + dx, y + dy) in door_cells
               for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))


def _glaze_walls(rng, grid, gw, gh, room_dicts, door_cells, structural, outdoors=True):
    owner = {}
    for idx, room in enumerate(room_dicts):
        cells = room.get("cells")
        if cells is None:
            x0, y0, x1, y1 = room["rect"]
            cells = [(x, y) for y in range(y0, y1) for x in range(x0, x1)]
        for (x, y) in cells:
            if 0 <= x < gw and 0 <= y < gh and grid[y][x] == S.FLOOR:
                owner.setdefault((x, y), set()).add(idx)
    kind_of = [r["kind"] for r in room_dicts]
    from game.props import PROP_DEFS
    hung = set()
    hung_block = set()
    for room in room_dicts:
        for f in room["furniture"]:
            cell = (int(f["pos"][0]), int(f["pos"][1]))
            hung.add(cell)
            defs = PROP_DEFS.get(f["kind"], {})
            if defs.get("wall_mounted"):
                hung_block.add(cell)

    no_glass = {i for i, k in enumerate(kind_of) if k in S.NO_GENERATED_WINDOWS}

    def glazable(cell):
        rooms = owner.get(cell)
        return not rooms or not (rooms & no_glass)

    def between(cell, step):
        if _touches_door(cell, door_cells):
            return None
        a = owner.get((cell[0] - step[0], cell[1] - step[1]))
        b = owner.get((cell[0] + step[0], cell[1] + step[1]))
        if not a or not b or (a & b):
            return None
        if (a | b) & no_glass:
            return None
        if not any(kind_of[i] in structural for i in a | b):
            return None
        along = (step[1], step[0])
        for d in (along, (-along[0], -along[1])):
            nx, ny = cell[0] + d[0], cell[1] + d[1]
            if not (0 <= nx < gw and 0 <= ny < gh) or grid[ny][nx] == S.FLOOR:
                return None
        return True

    runs = []
    for step, along in (((1, 0), (0, 1)), ((0, 1), (1, 0))):
        seen = set()
        for y in range(1, gh - 1):
            for x in range(1, gw - 1):
                cell = (x, y)
                if cell in seen or grid[y][x] != S.WALL_CONCRETE:
                    continue
                if cell in door_cells or between(cell, step) is None:
                    continue
                run = []
                c = cell
                while (c not in seen and 0 <= c[0] < gw and 0 <= c[1] < gh
                       and grid[c[1]][c[0]] == S.WALL_CONCRETE
                       and c not in door_cells and between(c, step) is not None
                       and not any((c[0] + o[0], c[1] + o[1]) in hung
                                   for o in (step, (-step[0], -step[1])))):
                    seen.add(c)
                    run.append(c)
                    c = (c[0] + along[0], c[1] + along[1])
                if run:
                    runs.append(run)
    rng.shuffle(runs)
    want = rng.randint(*S.WINDOW_RUNS_PER_FLOOR)
    glazed = 0
    for run in runs:
        if glazed >= want:
            break
        n = min(len(run), rng.randint(*S.WINDOW_RUN_CELLS))
        start = rng.randrange(len(run) - n + 1)
        for (x, y) in run[start:start + n]:
            grid[y][x] = S.WALL_WINDOW
        glazed += 1
    if outdoors:
        glazed += _glaze_outer_walls(rng, grid, gw, gh, owner, hung_block, door_cells, glazable)
    return glazed


def _glaze_outer_walls(rng, grid, gw, gh, owner, hung_block, door_cells, glazable):
    runs = []
    for step, along in (((1, 0), (0, 1)), ((0, 1), (1, 0))):
        seen = set()
        for y in range(1, gh - 1):
            for x in range(1, gw - 1):
                cell = (x, y)
                if cell in seen or grid[y][x] != S.WALL_CONCRETE or cell in door_cells:
                    continue

                def outward(c):
                    if _touches_door(c, door_cells):
                        return None
                    for sgn in (1, -1):
                        ins = (c[0] - step[0] * sgn, c[1] - step[1] * sgn)
                        out = (c[0] + step[0] * sgn, c[1] + step[1] * sgn)
                        if ins not in owner:
                            continue
                        if not (0 <= out[0] < gw and 0 <= out[1] < gh):
                            continue
                        if out in owner or grid[out[1]][out[0]] != S.WALL_CONCRETE:
                            continue
                        if not glazable(ins):
                            continue
                        far = (out[0] + step[0] * sgn, out[1] + step[1] * sgn)
                        if far in owner:
                            continue
                        return ins, out
                    return None

                if outward(cell) is None:
                    continue
                run = []
                c = cell
                while (c not in seen and 0 <= c[0] < gw and 0 <= c[1] < gh
                       and grid[c[1]][c[0]] == S.WALL_CONCRETE
                       and c not in door_cells and outward(c) is not None):
                    seen.add(c)
                    run.append(c)
                    c = (c[0] + along[0], c[1] + along[1])
                if len(run) >= 2:
                    runs.append((run, step))
    rng.shuffle(runs)
    runs.sort(key=lambda r: -(len(r[0]) + rng.random() * 3.0))
    want = rng.randint(*S.OUTER_WINDOW_RUNS_PER_FLOOR)
    made = 0
    along_of = {(1, 0): (0, 1), (0, 1): (1, 0)}
    for run, step in runs:
        if made >= want:
            break
        if len(run) < S.OUTER_WINDOW_MIN_WALL:
            continue
        sgn = None
        for trial in (1, -1):
            c = run[0]
            ins = (c[0] - step[0] * trial, c[1] - step[1] * trial)
            out = (c[0] + step[0] * trial, c[1] + step[1] * trial)
            if ins in owner and out not in owner:
                sgn = trial
                break
        if sgn is None:
            continue
        wall, yard = _fit_pocket(grid, gw, gh, owner, run, step, sgn, along_of[step])
        if yard is None:
            continue
        panes = _facade_panes(wall, hung_block, rng, step, sgn, along_of[step])
        if not panes:
            continue
        for c in panes:
            grid[c[1]][c[0]] = S.WALL_WINDOW
        for c in yard["open"]:
            grid[c[1]][c[0]] = S.WALL_OUTDOOR
        for c in yard["fence"]:
            grid[c[1]][c[0]] = S.WALL_FENCE
        for c in yard["trees"]:
            grid[c[1]][c[0]] = S.WALL_FOREST
        made += 1
    return made


def _fit_pocket(grid, gw, gh, owner, run, step, sgn, along):
    for span in range(len(run), S.OUTER_WINDOW_MIN_WALL - 1, -1):
        for start in range(0, len(run) - span + 1):
            sub = run[start:start + span]
            got = _outdoor_pocket(grid, gw, gh, owner, sub, step, sgn, along)
            if got is not None:
                return sub, got
    return run, None


def _facade_panes(run, hung_block, rng, step, sgn, along):
    corner = (S.OUTER_WINDOW_CORNER
              if len(run) >= S.OUTER_WINDOW_CORNER_MIN_WALL else 0)
    inner = run[corner:len(run) - corner] if corner else list(run)
    if not inner:
        return []
    lo, hi = S.OUTER_WINDOW_PANE
    pane = min(lo if len(inner) < S.OUTER_WINDOW_WIDE_WALL else rng.randint(lo, hi),
               len(inner))
    gap = rng.randint(*S.OUTER_WINDOW_GAP)
    n = max(1, (len(inner) + gap) // (pane + gap))
    span = n * pane + (n - 1) * gap
    off = (len(inner) - span) // 2
    panes = []
    for i in range(n):
        j = off + i * (pane + gap)
        block = inner[j:j + pane]
        if not any(cell in hung_block for cell in _pane_guard(block, step, sgn, along)):
            panes.extend(block)
    return panes


def _pane_guard(block, step, sgn, along):
    return [(c[0] - step[0] * sgn, c[1] - step[1] * sgn) for c in block]


def _outdoor_pocket(grid, gw, gh, owner, wall, step, sgn, along):
    for depth in range(S.OUTER_WINDOW_YARD_DEPTH, S.OUTER_WINDOW_YARD_MIN_DEPTH - 1, -1):
        for margin in range(S.OUTER_WINDOW_YARD_MARGIN, S.OUTER_WINDOW_YARD_MIN_MARGIN - 1, -1):
            got = _pocket_at(grid, gw, gh, owner, wall, step, sgn, along, depth, margin)
            if got is not None:
                return got
    return None


def _pocket_at(grid, gw, gh, owner, wall, step, sgn, along, depth, margin):
    wall_set = set(wall)
    coord = lambda c: c[0] * along[0] + c[1] * along[1]
    a0 = min(coord(wall[0]), coord(wall[-1])) - margin
    a1 = max(coord(wall[0]), coord(wall[-1])) + margin
    base = wall[0]
    fence_d = depth - S.OUTER_YARD_FENCE_INSET
    open_cells, fence_cells, tree_cells = [], [], []
    claimed = set()
    for d in range(1, depth + 2):
        for a in range(a0, a1 + 1):
            c = (base[0] * (1 - along[0]) + a * along[0] + step[0] * sgn * d,
                 base[1] * (1 - along[1]) + a * along[1] + step[1] * sgn * d)
            if not (0 <= c[0] < gw and 0 <= c[1] < gh):
                return None
            if c in owner or grid[c[1]][c[0]] != S.WALL_CONCRETE:
                return None
            claimed.add(c)
    for c in claimed:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                n = (c[0] + dx, c[1] + dy)
                if n in claimed or n in wall_set:
                    continue
                if n in owner:
                    return None
    for d in range(1, depth + 2):
        for a in range(a0, a1 + 1):
            c = (base[0] * (1 - along[0]) + a * along[0] + step[0] * sgn * d,
                 base[1] * (1 - along[1]) + a * along[1] + step[1] * sgn * d)
            flank = a < a0 + margin or a > a1 - margin
            if d > depth or flank:
                tree_cells.append(c)
            elif d == fence_d:
                fence_cells.append(c)
            else:
                open_cells.append(c)
    return {"open": open_cells, "fence": fence_cells, "trees": tree_cells}


def _dress_link(rng, cells, grid, gw, gh):
    def solid(x, y):
        if not (0 <= x < gw and 0 <= y < gh):
            return True
        return grid[y][x] != S.FLOOR

    def wall_side(cell):
        sides = [(dx, dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                 if solid(cell[0] + dx, cell[1] + dy)]
        return rng.choice(sides) if sides else None

    out = []
    taken = set()
    ends = set(cells[:2] + cells[-2:])

    def put(kind, cell, facing):
        x, y = cell[0] + 0.5, cell[1] + 0.5
        defs = props.PROP_DEFS.get(kind, {})
        if defs.get("wall_mounted"):
            dx, dy = round(math.cos(facing)), round(math.sin(facing))
            hd = defs.get("hd", 0.2) + 0.005
            x = cell[0] - dx * 0.5 + 0.5 + math.cos(facing) * hd
            y = cell[1] - dy * 0.5 + 0.5 + math.sin(facing) * hd
        out.append({"kind": kind, "pos": (x, y, defs.get("z0", 0.0)), "facing": facing})
        taken.add(cell)

    pipe_at = rng.randrange(max(1, len(cells) - 4)) if len(cells) > 6 else None
    pipe_len = rng.randint(*S.LINK_PIPE_RUN)
    pipe_side = None
    for i, cell in enumerate(cells):
        if cell in ends or cell in taken:
            continue
        if pipe_at is not None and pipe_at <= i < pipe_at + pipe_len:
            if pipe_side is None:
                pipe_side = wall_side(cell)
            if pipe_side is not None and solid(cell[0] + pipe_side[0], cell[1] + pipe_side[1]):
                put("pipes", cell, math.atan2(-pipe_side[1], -pipe_side[0]) % math.tau)
                continue
        if i % S.LINK_LAMP_EVERY == 2:
            side = wall_side(cell)
            if side is not None:
                put("emergency_lamp", cell, math.atan2(-side[1], -side[0]) % math.tau)
                continue
        if i % S.LINK_DRAIN_EVERY == 5:
            put("floor_drain", cell, 0.0)
            continue
        if i % S.LINK_CLUTTER_EVERY == 3:
            put(rng.choice(("clutter_papers", "clutter_junk", "clutter_bottle")),
                cell, round(rng.uniform(0, math.tau), 3))
    return out


def _blind_doors(rng, grid, gw, gh, room_dicts, door_cells, count):
    if not count:
        return []
    def solid(x, y):
        if not (0 <= x < gw and 0 <= y < gh):
            return True
        return grid[y][x] != S.FLOOR

    inside = []
    for room in room_dicts:
        if room["kind"] == "link":
            continue
        rx0, ry0, rx1, ry1 = room["rect"]
        for y in range(ry0, ry1):
            for x in range(rx0, rx1):
                if grid[y][x] == S.FLOOR:
                    inside.append((x, y))
    rng.shuffle(inside)
    taken = set(door_cells)
    for room in room_dicts:
        taken.update(tuple(d["cell"]) for d in room["interior_doors"])
    spoken_for = set()
    for cell in taken:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                spoken_for.add((cell[0] + dx, cell[1] + dy))
    for room in room_dicts:
        for f in room["furniture"]:
            fx, fy = int(f["pos"][0]), int(f["pos"][1])
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (0, 0)):
                spoken_for.add((fx + dx, fy + dy))
    out = []
    for (x, y) in inside:
        if len(out) >= count:
            break
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            hole = (x + dx, y + dy)
            if hole in spoken_for or not solid(*hole):
                continue
            if grid[hole[1]][hole[0]] in S.SEE_THROUGH_WALLS:
                continue
            if not solid(x + dx * 2, y + dy * 2):
                continue
            ax, ay = dy, dx
            if not (solid(hole[0] + ax, hole[1] + ay) and solid(hole[0] - ax, hole[1] - ay)):
                continue
            if not (solid(hole[0] + ax + dx, hole[1] + ay + dy)
                    and solid(hole[0] - ax + dx, hole[1] - ay + dy)):
                continue
            grid[hole[1]][hole[0]] = S.FLOOR
            taken.add(hole)
            out.append((hole, door_facing("E" if dx else "N"), "door"))
            break
    return out


def generate(rng, floor_key, target_rooms, anomaly=None):
    _anomaly = S.anomaly_of(anomaly or S.ANOMALY_DEFAULT_STAGE)
    for _ in range(40):
        try:
            rooms, doors_made, resolved_doors, extra_open, links = _attempt(
                rng, floor_key, target_rooms, anomaly)
            break
        except _GenFailed:
            continue
    else:
        return None

    link_cells = [c for path in links for c in path]
    min_x = min([r["origin"][0] for r in rooms] + [c[0] for c in link_cells])
    min_y = min([r["origin"][1] for r in rooms] + [c[1] for c in link_cells])
    max_x = max([r["origin"][0] + r["template"].w for r in rooms] + [c[0] + 1 for c in link_cells])
    max_y = max([r["origin"][1] + r["template"].h for r in rooms] + [c[1] + 1 for c in link_cells])
    pad = 2
    gw = (max_x - min_x) + pad * 2
    gh = (max_y - min_y) + pad * 2
    if gw % 2 == 0:
        gw += 1
    if gh % 2 == 0:
        gh += 1
    shift_x, shift_y = pad - min_x, pad - min_y

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
        extra_here = extra_by_room.get(r_idx, {})
        for ly in range(t.h):
            for lx in range(t.w):
                wx, wy = ox + lx, oy + ly
                if grid[wy][wx] == S.FLOOR:
                    continue
                if (lx, ly) in extra_here:
                    grid[wy][wx] = S.FLOOR
                elif (lx, ly) in unresolved_local_cells:
                    grid[wy][wx] = S.WALL_CONCRETE
                elif (lx, ly) in resolved_local_cells:
                    grid[wy][wx] = S.FLOOR
                else:
                    grid[wy][wx] = t.cells[ly][lx]
        wings = S.FLOOR_WINGS.get(floor_key, ())
        wing = wings[r.get("wing", 0) % len(wings)] if wings else None
        room_dicts.append({
            "rect": (ox, oy, ox + t.w, oy + t.h),
            "kind": t.kind,
            "wing": wing["key"] if wing else None,
            "spine": bool(r.get("spine")),
            "echo": bool(r.get("echo")),
            "template_id": t.id,
            "mirrored": bool(t.mirrored),
            "wall_bias": (None if t.kind in S.WING_KEEPS_OWN_SKIN or wing is None
                          else wing["material"]),
            "furniture": [
                {"kind": e[0], "pos": (ox + e[1], oy + e[2], e[3]), "facing": e[4]}
                for e in (props.furniture_place(f) for f in t.furniture)
                if (int(e[1]), int(e[2])) not in door_local_cells
            ],
            "interior_doors": [
                {"cell": (ox + lx, oy + ly), "facing": facing, "kind": kind}
                for (lx, ly, facing, kind) in t.interior_doors
                if (lx, ly) not in door_local_cells
            ],
        })

    for path in links:
        cells = [(c[0] + shift_x, c[1] + shift_y) for c in path]
        for (x, y) in cells:
            grid[y][x] = S.FLOOR
        xs = [c[0] for c in cells]
        ys = [c[1] for c in cells]
        room_dicts.append({
            "rect": (min(xs), min(ys), max(xs) + 1, max(ys) + 1),
            "kind": "link",
            "cells": cells,
            "furniture": _dress_link(rng, cells, grid, gw, gh),
            "interior_doors": [],
        })

    _merge_wing_islands(room_dicts)
    _clear_wall_debris(grid, gw, gh, rooms, shift_x, shift_y, room_dicts,
                       {(c[0] + shift_x, c[1] + shift_y) for c, _f, _k in doors_made})

    template_doors = [((c[0] + shift_x, c[1] + shift_y), facing, kind) for c, facing, kind in doors_made]
    def _has_jambs(cell):
        x, y = cell
        def solid(cx, cy):
            if not (0 <= cx < gw and 0 <= cy < gh):
                return True
            return grid[cy][cx] != S.FLOOR
        return (solid(x - 1, y) and solid(x + 1, y)) or (solid(x, y - 1) and solid(x, y + 1))

    template_doors = [d for d in template_doors if _has_jambs(d[0])]

    template_doors += _blind_doors(rng, grid, gw, gh, room_dicts,
                                   {d[0] for d in template_doors},
                                   _anomaly.get("blind_doors", 0))

    _glaze_walls(rng, grid, gw, gh, room_dicts,
                 {d[0] for d in template_doors} | {tuple(d["cell"]) for r in room_dicts
                                                   for d in r["interior_doors"]},
                 STRUCTURAL_KINDS[floor_key] | {"link"},
                 outdoors=floor_key == "upper")

    start_room = room_dicts[0]
    srx0, sry0, srx1, sry1 = start_room["rect"]
    start = ((srx0 + srx1) / 2.0, (sry0 + sry1) / 2.0)

    return gw, gh, grid, room_dicts, template_doors, start
