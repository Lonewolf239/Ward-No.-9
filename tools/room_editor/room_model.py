import glob
import json
import math
import os
import re

from game import settings as S
from game import i18n
from game.props import PROP_DEFS, SURFACE_KINDS

SURFACE_ITEM_KINDS = {"monitor", "lamp_desk"}

ROOM_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "game", "room_data")

KINDS_BY_FLOOR = {
    "upper": ["ward", "office", "morgue", "cafeteria", "plain",
              "entrance", "exit", "unlocker", "corridor"],
    "basement": ["boiler", "storage", "cell", "morgue", "plain",
                 "stairwell", "exit", "unlocker", "tech_corridor", "vent"],
}
STRUCTURAL_KINDS = {"corridor", "tech_corridor", "vent"}
REQUIRED_KINDS = {"entrance", "stairwell", "exit", "unlocker"}

_FIXTURE_SPEC_FIELD_BY_KIND = {"exit": "exit_prop", "unlocker": "panel"}


def required_fixture_kind(kind, floor):
    field = _FIXTURE_SPEC_FIELD_BY_KIND.get(kind)
    if field is None:
        return None
    spec = next((s for s in S.FLOOR_SPECS if s.get("floor_theme") == floor), None)
    return spec.get(field) if spec else None

WALL_MATERIALS = ["concrete", "tile", "metal", "blood"]
_WALL_MATERIAL_TO_TILE = {
    "concrete": S.WALL_CONCRETE, "tile": S.WALL_TILE,
    "metal": S.WALL_METAL, "blood": S.WALL_BLOOD,
}

DOOR_SIDES = ("N", "S", "E", "W")
DOOR_KINDS = ("passage", "door", "broken", "window", "random")
PI, HALF, TAU34 = math.pi, math.pi / 2, 3 * math.pi / 2


class RoomModel:
    def __init__(self, id="new_room", kind="ward", floor="upper", w=7, h=6):
        self.id = id
        self.kind = kind
        self.floor = floor
        self.w = w
        self.h = h
        self.weight = 1.0
        self.required = kind in REQUIRED_KINDS
        self.wall_material_preview = "concrete"
        self.cells = _blank(w, h)
        self.doors = {}
        self.interior_doors = []
        self.furniture = []

    def set_kind(self, kind):
        if kind != self.kind and _is_auto_id(self.id, self.kind, self.floor):
            self.id = fresh_id(kind, self.floor)
        self.kind = kind
        self.required = kind in REQUIRED_KINDS

    def set_floor(self, floor):
        if floor == self.floor:
            return
        was_auto = _is_auto_id(self.id, self.kind, self.floor)
        self.floor = floor
        if self.kind not in KINDS_BY_FLOOR[floor]:
            self.kind = KINDS_BY_FLOOR[floor][0]
            self.required = self.kind in REQUIRED_KINDS
        if was_auto:
            self.id = fresh_id(self.kind, floor)

    def in_bounds(self, x, y):
        return 0 <= x < self.w and 0 <= y < self.h

    def on_border(self, x, y):
        return self.in_bounds(x, y) and (x in (0, self.w - 1) or y in (0, self.h - 1))

    def door_cell_set(self):
        return {info["cell"] for info in self.doors.values()}

    def resize(self, w, h):
        w, h = max(3, w), max(3, h)
        new_cells = _blank(w, h)
        for y in range(min(h, self.h)):
            for x in range(min(w, self.w)):
                new_cells[y][x] = self.cells[y][x]
        self.w, self.h = w, h
        self.cells = new_cells
        self.doors = {s: info for s, info in self.doors.items() if self.on_border(*info["cell"])}
        self.interior_doors = [d for d in self.interior_doors if self.in_bounds(d[0], d[1])]
        self.furniture = [f for f in self.furniture if self.in_bounds(f[1], f[2])]
        self._drop_orphaned_wall_furniture()
        self._drop_orphaned_interior_doors()

    def set_cell(self, x, y, value):
        if not self.in_bounds(x, y) or (x, y) in self.door_cell_set():
            return
        was_wall = self.cells[y][x] != S.FLOOR
        self.cells[y][x] = value
        if was_wall and value == S.FLOOR:
            self._drop_orphaned_wall_furniture()
        if value != S.FLOOR:
            self.furniture = [f for f in self.furniture if (f[1], f[2]) != (x, y)]
            self.interior_doors = [d for d in self.interior_doors if (d[0], d[1]) != (x, y)]
            self._drop_orphaned_interior_doors()

    def _drop_orphaned_wall_furniture(self):
        self.furniture = [
            f for f in self.furniture
            if not PROP_DEFS[f[0]]["wall_mounted"] or self.wall_facing_at(f[1], f[2]) is not None
        ]
        self._drop_orphaned_surface_items()

    def _drop_orphaned_surface_items(self):
        base_cells = {(f[1], f[2]) for f in self.furniture if f[0] in SURFACE_KINDS}
        self.furniture = [
            f for f in self.furniture
            if f[0] not in SURFACE_ITEM_KINDS or (f[1], f[2]) in base_cells
        ]

    def _drop_orphaned_interior_doors(self):
        self.interior_doors = [d for d in self.interior_doors if self.gap_facing(d[0], d[1]) is not None]

    def set_door(self, side, x, y, kind="passage"):
        if not self.on_border(x, y):
            return
        self.set_cell(x, y, S.FLOOR)
        self.doors[side] = {"cell": (x, y), "kind": kind}

    def clear_door(self, side):
        self.doors.pop(side, None)

    def door_at(self, x, y):
        for side, info in self.doors.items():
            if info["cell"] == (x, y):
                return side
        return None

    def side_of_border_cell(self, x, y):
        if not self.on_border(x, y):
            return None
        if x == 0:
            return "W"
        if x == self.w - 1:
            return "E"
        if y == 0:
            return "N"
        return "S"

    def _is_wall_or_edge(self, x, y):
        if not self.in_bounds(x, y):
            return True
        return self.cells[y][x] != S.FLOOR

    def gap_facing(self, x, y):
        if not self.in_bounds(x, y):
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

    def click_door(self, x, y, kind, scope="link"):
        if not self.in_bounds(x, y) or self.furniture_at(x, y) is not None:
            return False
        side = self.side_of_border_cell(x, y)
        if side is not None and side in self.doors and self.doors[side]["cell"] == (x, y):
            self.clear_door(side)
            return True
        existing = self.interior_door_at(x, y)
        if existing is not None:
            self.interior_doors.remove(existing)
            return True
        if side is not None and scope == "link":
            if side in self.doors:
                return False
            self.set_door(side, x, y, kind=kind)
            return True
        facing = self.gap_facing(x, y)
        if facing is None:
            return False
        self.set_cell(x, y, S.FLOOR)
        if kind != "passage":
            self.interior_doors.append([x, y, facing, kind])
        return True

    def furniture_at(self, x, y):
        found = None
        for item in self.furniture:
            if (item[1], item[2]) != (x, y):
                continue
            if item[0] not in SURFACE_ITEM_KINDS:
                return item
            found = item
        return found

    def wall_facings_at(self, x, y):
        checks = ((-1, 0, 0.0), (1, 0, PI), (0, -1, HALF), (0, 1, TAU34))
        out = []
        for dx, dy, facing in checks:
            wx, wy = x + dx, y + dy
            is_wall = not (0 <= wx < self.w and 0 <= wy < self.h) or self.cells[wy][wx] != S.FLOOR
            if is_wall:
                out.append(facing)
        return out

    def wall_facing_at(self, x, y):
        facings = self.wall_facings_at(x, y)
        return facings[0] if facings else None

    def place_furniture(self, kind, x, y):
        if (not self.in_bounds(x, y) or self.cells[y][x] != S.FLOOR
                or (x, y) in self.door_cell_set() or self.interior_door_at(x, y) is not None):
            return False
        if PROP_DEFS[kind]["wall_mounted"]:
            facing = self.wall_facing_at(x, y)
            if facing is None:
                return False
        else:
            facing = 0.0
        existing = self.furniture_at(x, y)
        stacking = kind in SURFACE_ITEM_KINDS and existing is not None and existing[0] in SURFACE_KINDS
        if stacking:
            self.furniture = [
                f for f in self.furniture
                if (f[1], f[2]) != (x, y) or f[0] not in SURFACE_ITEM_KINDS
            ]
        else:
            self.furniture = [f for f in self.furniture if (f[1], f[2]) != (x, y)]
        self.furniture.append([kind, x, y, facing])
        return True

    def remove_furniture(self, x, y):
        self.furniture = [f for f in self.furniture if (f[1], f[2]) != (x, y)]

    def rotate_furniture(self, x, y):
        item = self.furniture_at(x, y)
        if item is None:
            return
        if PROP_DEFS[item[0]]["wall_mounted"]:
            facings = self.wall_facings_at(x, y)
            if len(facings) <= 1:
                return
            idx = facings.index(item[3]) if item[3] in facings else -1
            item[3] = facings[(idx + 1) % len(facings)]
        else:
            item[3] = (item[3] + math.pi / 2) % math.tau

    def connectivity_errors(self):
        if not self.doors:
            return [i18n.t("editor.err.no_doors")]
        floor_cells = {(x, y) for y in range(self.h) for x in range(self.w)
                       if self.cells[y][x] == S.FLOOR}
        floor_cells |= {(f[1], f[2]) for f in self.furniture}
        start = next(iter(self.doors.values()))["cell"]
        seen = {start}
        stack = [start]
        while stack:
            cx, cy = stack.pop()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nc = (cx + dx, cy + dy)
                if nc in floor_cells and nc not in seen:
                    seen.add(nc)
                    stack.append(nc)
        errors = []
        unreachable = floor_cells - seen
        if unreachable:
            errors.append(i18n.t("editor.err.unreachable_floor", n=len(unreachable)))
        for side, info in self.doors.items():
            if info["cell"] not in seen:
                errors.append(i18n.t("editor.err.door_disconnected", side=side))
        return errors

    def content_errors(self):
        errors = []
        required = required_fixture_kind(self.kind, self.floor)
        if required:
            count = sum(1 for f in self.furniture if f[0] == required)
            if count == 0:
                errors.append(i18n.t("editor.err.missing_required", kind=self.kind, req=required))
            elif count > 1:
                errors.append(i18n.t("editor.err.too_many_required", kind=self.kind, req=required, count=count))
        return errors

    def all_errors(self):
        return self.connectivity_errors() + self.content_errors()

    def to_maze_grid(self, pad=3):
        gw, gh = self.w + pad * 2, self.h + pad * 2
        material = _WALL_MATERIAL_TO_TILE[self.wall_material_preview]
        grid = [[material for _ in range(gw)] for _ in range(gh)]
        for y in range(self.h):
            for x in range(self.w):
                grid[y + pad][x + pad] = self.cells[y][x]
        for info in self.doors.values():
            dx, dy = info["cell"]
            grid[dy + pad][dx + pad] = S.WALL_WINDOW if info["kind"] == "window" else S.FLOOR
        for x, y, facing, kind in self.interior_doors:
            if kind == "window":
                grid[y + pad][x + pad] = S.WALL_WINDOW
        return gw, gh, grid, pad

    def to_json_dict(self):
        return {
            "schema_version": 3, "id": self.id, "kind": self.kind, "floor": self.floor,
            "w": self.w, "h": self.h, "weight": self.weight, "required": self.required,
            "cells": self.cells,
            "doors": {s: {"cell": list(info["cell"]), "kind": info["kind"]} for s, info in self.doors.items()},
            "interior_doors": [list(d) for d in self.interior_doors],
            "furniture": [list(f) for f in self.furniture],
        }

    @classmethod
    def from_json_dict(cls, data):
        m = cls(id=data["id"], kind=data["kind"], floor=data["floor"], w=data["w"], h=data["h"])
        m.weight = data.get("weight", 1.0)
        m.required = data.get("required", False)
        m.cells = [list(row) for row in data["cells"]]
        m.doors = {}
        for side, info in data["doors"].items():
            if isinstance(info, dict):
                m.doors[side] = {"cell": tuple(info["cell"]), "kind": info.get("kind", "passage")}
            else:
                m.doors[side] = {"cell": tuple(info), "kind": "passage"}
        m.interior_doors = [list(d) for d in data.get("interior_doors", [])]
        m.furniture = [list(item) for item in data.get("furniture", [])]
        return m

    def save(self):
        os.makedirs(ROOM_DATA_DIR, exist_ok=True)
        path = os.path.join(ROOM_DATA_DIR, f"{self.id}.json")
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.to_json_dict(), f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
        return path


def _blank(w, h):
    return [[S.FLOOR if (0 < x < w - 1 and 0 < y < h - 1) else S.WALL_CONCRETE
             for x in range(w)] for y in range(h)]


def list_saved_ids():
    return sorted(
        os.path.splitext(os.path.basename(p))[0]
        for p in glob.glob(os.path.join(ROOM_DATA_DIR, "*.json"))
    )


def fresh_id(kind, floor):
    existing = set(list_saved_ids())
    i = 1
    while True:
        candidate = f"{kind}_{floor}_{i:02d}"
        if candidate not in existing:
            return candidate
        i += 1


def _is_auto_id(id_, kind, floor):
    return bool(re.fullmatch(rf"{re.escape(kind)}_{re.escape(floor)}_\d+", id_))


def load(room_id):
    path = os.path.join(ROOM_DATA_DIR, f"{room_id}.json")
    with open(path, encoding="utf-8") as f:
        return RoomModel.from_json_dict(json.load(f))
