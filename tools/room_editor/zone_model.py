import glob
import json
import math
import os
import re

from game import settings as S
from game import i18n
from game.props import PROP_DEFS, SURFACE_KINDS

ZONE_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "game", "zone_data")

ZONE_SIZE = S.YARD_ZONE_SIZE
ZONE_KINDS = ["open", "shed", "tool_shed", "storage", "forest", "alley"]
REQUIRED_ZONE_KINDS = {"shed"}
SURFACE_ITEM_KINDS = {"monitor", "lamp_desk"}

_SIDES = ("N", "S", "E", "W")
PI, HALF, TAU34 = math.pi, math.pi / 2, 3 * math.pi / 2


class ZoneModel:
    def __init__(self, id="new_zone", kind="open"):
        self.id = id
        self.kind = kind
        self.weight = 1.0
        self.required = kind in REQUIRED_ZONE_KINDS
        self.w = self.h = ZONE_SIZE
        self.cells = _blank(ZONE_SIZE, ZONE_SIZE)
        self.doors = {}
        self.interior_doors = []
        self.furniture = []

    def set_kind(self, kind):
        if kind != self.kind and _is_auto_id(self.id, self.kind):
            self.id = fresh_id(kind)
        self.kind = kind
        self.required = kind in REQUIRED_ZONE_KINDS

    def in_bounds(self, x, y):
        return 0 <= x < self.w and 0 <= y < self.h

    def on_border(self, x, y):
        return self.in_bounds(x, y) and (x in (0, self.w - 1) or y in (0, self.h - 1))

    def _border_cells(self):
        return (
            [(x, 0) for x in range(self.w)] + [(x, self.h - 1) for x in range(self.w)]
            + [(0, y) for y in range(self.h)] + [(self.w - 1, y) for y in range(self.h)]
        )

    def set_cell(self, x, y, value):
        if not self.in_bounds(x, y):
            return
        if self.on_border(x, y) and value != S.FLOOR:
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

    def _is_wall_or_edge(self, x, y):
        if not self.in_bounds(x, y):
            return True
        return self.cells[y][x] != S.FLOOR

    def gap_facing(self, x, y):
        if not self.in_bounds(x, y) or self.on_border(x, y):
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

    def click_interior_door(self, x, y, kind="door"):
        existing = self.interior_door_at(x, y)
        if existing is not None:
            self.interior_doors.remove(existing)
            return True
        if self.furniture_at(x, y) is not None:
            return False
        facing = self.gap_facing(x, y)
        if facing is None:
            return False
        self.set_cell(x, y, S.FLOOR)
        self.interior_doors = [d for d in self.interior_doors if (d[0], d[1]) != (x, y)]
        self.interior_doors.append([x, y, facing, kind])
        return True

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

    def furniture_at(self, x, y):
        found = None
        for item in self.furniture:
            if (item[1], item[2]) != (x, y):
                continue
            if item[0] not in SURFACE_ITEM_KINDS:
                return item
            found = item
        return found

    def place_furniture(self, kind, x, y):
        if (not self.in_bounds(x, y) or self.cells[y][x] != S.FLOOR
                or self.interior_door_at(x, y) is not None):
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
        errors = []
        border_blocked = [c for c in self._border_cells() if self.cells[c[1]][c[0]] != S.FLOOR]
        if border_blocked:
            errors.append(i18n.t("editor.err.zone_border_blocked", n=len(border_blocked)))
        floor_cells = {(x, y) for y in range(self.h) for x in range(self.w) if self.cells[y][x] == S.FLOOR}
        floor_cells |= {(f[1], f[2]) for f in self.furniture}
        if not floor_cells:
            return errors
        border_floor = [c for c in self._border_cells() if c in floor_cells]
        start = border_floor[0] if border_floor else next(iter(floor_cells))
        seen = {start}
        stack = [start]
        while stack:
            cx, cy = stack.pop()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nc = (cx + dx, cy + dy)
                if nc in floor_cells and nc not in seen:
                    seen.add(nc)
                    stack.append(nc)
        unreachable = floor_cells - seen
        if unreachable:
            errors.append(i18n.t("editor.err.zone_unreachable", n=len(unreachable)))
        return errors

    def all_errors(self):
        return self.connectivity_errors()

    def to_maze_grid(self, pad=3):
        gw, gh = self.w + pad * 2, self.h + pad * 2
        grid = [[S.WALL_CONCRETE for _ in range(gw)] for _ in range(gh)]
        for y in range(self.h):
            for x in range(self.w):
                grid[y + pad][x + pad] = self.cells[y][x]
        for x, y, facing, kind in self.interior_doors:
            if kind == "window":
                grid[y + pad][x + pad] = S.WALL_WINDOW
        return gw, gh, grid, pad

    def to_json_dict(self):
        return {
            "schema_version": 2, "id": self.id, "kind": self.kind,
            "required": self.required, "weight": self.weight,
            "cells": self.cells,
            "interior_doors": [list(d) for d in self.interior_doors],
            "furniture": [list(f) for f in self.furniture],
        }

    @classmethod
    def from_json_dict(cls, data):
        m = cls(id=data["id"], kind=data["kind"])
        m.weight = data.get("weight", 1.0)
        m.required = data.get("required", False)
        m.cells = [list(row) for row in data["cells"]]
        m.interior_doors = [list(d) for d in data.get("interior_doors", [])]
        m.furniture = [list(f) for f in data.get("furniture", [])]
        return m

    def save(self):
        os.makedirs(ZONE_DATA_DIR, exist_ok=True)
        path = os.path.join(ZONE_DATA_DIR, f"{self.id}.json")
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.to_json_dict(), f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
        return path


def _blank(w, h):
    def cell(x, y):
        if x in (0, w - 1) or y in (0, h - 1):
            return S.FLOOR
        if x in (1, w - 2) or y in (1, h - 2):
            return S.WALL_SHED
        return S.FLOOR
    return [[cell(x, y) for x in range(w)] for y in range(h)]


def list_saved_ids():
    return sorted(
        os.path.splitext(os.path.basename(p))[0]
        for p in glob.glob(os.path.join(ZONE_DATA_DIR, "*.json"))
    )


def fresh_id(kind):
    existing = set(list_saved_ids())
    i = 1
    while True:
        candidate = f"{kind}_yard_{i:02d}"
        if candidate not in existing:
            return candidate
        i += 1


def _is_auto_id(id_, kind):
    return bool(re.fullmatch(rf"{re.escape(kind)}_yard_\d+", id_))


def load(zone_id):
    path = os.path.join(ZONE_DATA_DIR, f"{zone_id}.json")
    with open(path, encoding="utf-8") as f:
        return ZoneModel.from_json_dict(json.load(f))
