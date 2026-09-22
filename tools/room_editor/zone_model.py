import glob
import json
import math
import os
import re

from game import settings as S
from game import i18n
from game.props import PROP_DEFS
from tools.room_editor.furniture_model import FurnitureGrid
from tools.room_editor.room_model import bar_enclosed, flood_cells

ZONE_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "game", "zone_data")

ZONE_SIZE = S.YARD_ZONE_SIZE
ZONE_KINDS = ["open", "shed", "tool_shed", "storage", "forest", "alley",
              "greenhouse", "chapel", "plant", "morgue_dock", "pen", "ruin", "dump"]
REQUIRED_ZONE_KINDS = {"shed"}

_SIDES = ("N", "S", "E", "W")
PI, HALF, TAU34 = math.pi, math.pi / 2, 3 * math.pi / 2


class ZoneModel(FurnitureGrid):
    GAP_SKIPS_BORDER = True

    def __init__(self, id="new_zone", kind="open"):
        self.id = id
        self.kind = kind
        self.weight = 1.0
        self.required = kind in REQUIRED_ZONE_KINDS
        self.w = self.h = ZONE_SIZE
        self.cells = _blank(ZONE_SIZE, ZONE_SIZE, kind)
        self.doors = {}
        self.interior_doors = []
        self.furniture = []

    def set_kind(self, kind):
        if kind != self.kind and _is_auto_id(self.id, self.kind):
            self.id = fresh_id(kind)
        if kind != self.kind and self._is_untouched():
            self.cells = _blank(self.w, self.h, kind)
        self.kind = kind
        self.required = kind in REQUIRED_ZONE_KINDS

    def _is_untouched(self):
        return (not self.furniture and not self.doors and not self.interior_doors
                and self.cells == _blank(self.w, self.h, self.kind))

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
            self.furniture = [f for f in self.furniture
                              if (int(f[1]), int(f[2])) != (x, y)]
            self.interior_doors = [d for d in self.interior_doors if (d[0], d[1]) != (x, y)]
            self._drop_orphaned_interior_doors()

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

    def bar_enclosed_cells(self):
        border_floor = [c for c in self._border_cells() if self.cells[c[1]][c[0]] == S.FLOOR]
        if not border_floor:
            return set()
        return bar_enclosed(self.cells, self.w, self.h, self.furniture, border_floor[0])

    def connectivity_errors(self):
        errors = []
        border_blocked = [c for c in self._border_cells() if self.cells[c[1]][c[0]] != S.FLOOR]
        if border_blocked:
            errors.append(i18n.t("editor.err.zone_border_blocked", n=len(border_blocked)))
        floor_cells = {(x, y) for y in range(self.h) for x in range(self.w) if self.cells[y][x] == S.FLOOR}
        floor_cells |= {(int(f[1]), int(f[2])) for f in self.furniture}
        if not floor_cells:
            return errors
        border_floor = [c for c in self._border_cells() if c in floor_cells]
        start = border_floor[0] if border_floor else next(iter(floor_cells))
        bars = {(x, y) for y in range(self.h) for x in range(self.w) if self.cells[y][x] == S.WALL_BARS}
        seen = flood_cells(floor_cells | bars, start)
        unreachable = floor_cells - seen
        if unreachable:
            errors.append(i18n.t("editor.err.zone_unreachable", n=len(unreachable)))
        return errors


    def all_errors(self):
        return self.connectivity_errors() + self.window_errors()

    def to_maze_grid(self, pad=3):
        gw, gh = self.w + pad * 2, self.h + pad * 2
        grid = [[S.WALL_CONCRETE for _ in range(gw)] for _ in range(gh)]
        for y in range(self.h):
            for x in range(self.w):
                grid[y + pad][x + pad] = self.cells[y][x]
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


ZONE_SHELL_MATERIAL = {
    "shed": S.WALL_SHED, "tool_shed": S.WALL_SHED, "storage": S.WALL_SHED,
    "greenhouse": S.WALL_SHED, "plant": S.WALL_SHED,
    "chapel": S.WALL_BRICK, "morgue_dock": S.WALL_BRICK,
    "ruin": S.WALL_CONCRETE,
}


def _blank(w, h, kind="open"):
    material = ZONE_SHELL_MATERIAL.get(kind)

    def cell(x, y):
        if material is None:
            return S.FLOOR
        if x in (0, w - 1) or y in (0, h - 1):
            return S.FLOOR
        if x in (1, w - 2) or y in (1, h - 2):
            return material
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
