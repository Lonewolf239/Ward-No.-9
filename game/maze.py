import math
import random
from collections import deque

from game import settings as S
from game import room_templates


def _rects_overlap(a, b, pad=0):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return not (ax1 + pad <= bx0 or bx1 + pad <= ax0 or ay1 + pad <= by0 or by1 + pad <= ay0)


class Maze:
    def __init__(self, w=S.MAZE_W, h=S.MAZE_H, seed=None, wall_bias=None, layout="corridor",
                 template_floor=None):
        self.w = w if w % 2 == 1 else w + 1
        self.h = h if h % 2 == 1 else h + 1
        self.rng = random.Random(seed)
        self.wall_bias = wall_bias
        self.layout = layout
        self.grid = [[S.WALL_CONCRETE for _ in range(self.w)] for _ in range(self.h)]
        self.rooms = []
        self.template_doors = []
        self.start = (1.5, 1.5)
        self.shed_rect = None
        self.shed_door_cell = None
        self.showcase_rect = None
        self.yard_buildings = []
        self.yard_forest_rect = None
        self.yard_alley_line = None
        if layout == "yard":
            self._carve_yard()
        elif layout == "debug":
            self._carve_debug()
        else:
            self._carve_template_rooms(template_floor)

    def _carve_template_rooms(self, floor_key):
        target = self.rng.randint(*S.TEMPLATE_ROOM_COUNT)
        result = room_templates.generate(self.rng, floor_key, target)
        if result is None:
            raise RuntimeError(
                f"не удалось собрать этаж '{floor_key}' из комнат редактора - "
                "проверьте комплект комнат в game/room_data (обязательные типы, двери)"
            )
        gw, gh, grid, room_dicts, template_doors, start = result
        self.w, self.h = gw, gh
        self.grid = grid
        self.rooms = room_dicts
        self.template_doors = template_doors
        self.start = start
        self._skin_walls()

    def _carve_yard(self):
        w, h = self.w, self.h
        rng = self.rng
        for y in range(h):
            for x in range(w):
                self.grid[y][x] = S.WALL_FOREST
        for y in range(1, h - 1):
            for x in range(1, w - 1):
                self.grid[y][x] = S.WALL_FENCE
        for y in range(2, h - 2):
            for x in range(2, w - 2):
                self.grid[y][x] = S.FLOOR

        sw, sh = 3, 3
        margin = 3
        sx = rng.randrange(margin, w - margin - sw)
        sy = rng.randrange(margin, h - margin - sh)
        if abs(sx + sw / 2 - w / 2) < 3 and abs(sy + sh / 2 - h / 2) < 3:
            sx = margin if sx < w / 2 else w - margin - sw
        shed_rect, shed_door, shed_door_facing, _shed_interior_door, _shed_rooms = self._stamp_building(
            sw, sh, sx=sx, sy=sy)
        self.shed_rect = shed_rect
        self.shed_door_cell = shed_door
        self.shed_door_facing = shed_door_facing

        self.start = (w / 2 + 0.5, h / 2 + 0.5)
        if self.grid[int(self.start[1])][int(self.start[0])] != S.FLOOR:
            self.grid[int(self.start[1])][int(self.start[0])] = S.FLOOR

        avoid = [shed_rect, (int(self.start[0]) - 2, int(self.start[1]) - 2,
                              int(self.start[0]) + 2, int(self.start[1]) + 2)]

        self.yard_forest_rect = self._reserve_zone(rng, 6, 8, avoid)
        if self.yard_forest_rect is not None:
            avoid.append(self.yard_forest_rect)

        self.yard_alley_line = self._reserve_alley_line(rng, avoid)
        if self.yard_alley_line is not None:
            (ax0, ay0), (ax1, ay1) = self.yard_alley_line
            pad = 2
            avoid.append((min(ax0, ax1) - pad, min(ay0, ay1) - pad,
                           max(ax0, ax1) + pad, max(ay0, ay1) + pad))

        self.yard_buildings = []
        layouts = [
            dict(size=(7, 5), two_room=False, kind="tool_shed"),
            dict(size=(5, 7), two_room=False, kind="tool_shed"),
            dict(size=(11, 6), two_room=True, kind="storage"),
            dict(size=(6, 11), two_room=True, kind="storage"),
        ]
        chosen = rng.sample(layouts, 3)
        chosen.sort(key=lambda l: -(l["size"][0] * l["size"][1]))
        for layout in chosen:
            bw, bh = layout["size"]
            rect, door, door_facing, interior_door, rooms = self._stamp_building(
                bw, bh, avoid_rects=avoid, margin=3, two_room=layout["two_room"])
            if rect is None:
                continue
            avoid.append(rect)
            self.yard_buildings.append({
                "rect": rect, "door_cell": door, "door_facing": door_facing,
                "interior_door": interior_door, "interior_rects": rooms, "kind": layout["kind"],
            })

    def _reserve_zone(self, rng, min_size, max_size, avoid_rects):
        w, h = self.w, self.h
        margin = 3
        sizes = [(s, s - 1) for s in range(max_size, min_size - 1, -1)] + \
                [(s - 1, s) for s in range(max_size, min_size - 1, -1)]
        for pw, ph in sizes:
            corners = [(margin, margin), (w - margin - pw, margin),
                       (margin, h - margin - ph), (w - margin - pw, h - margin - ph)]
            rng.shuffle(corners)
            for px, py in corners:
                cand = (px, py, px + pw, py + ph)
                if avoid_rects and any(_rects_overlap(cand, r) for r in avoid_rects):
                    continue
                return cand
        return None

    def _reserve_alley_line(self, rng, avoid_rects):
        w, h = self.w, self.h
        margin = 4
        length = rng.randint(7, 10)
        for _ in range(20):
            vertical = rng.random() < 0.5
            if vertical:
                x = rng.choice((margin, w - margin))
                y0 = rng.randrange(margin, max(margin + 1, h - margin - length))
                p0, p1 = (x, y0), (x, y0 + length)
            else:
                y = rng.choice((margin, h - margin))
                x0 = rng.randrange(margin, max(margin + 1, w - margin - length))
                p0, p1 = (x0, y), (x0 + length, y)
            span = (min(p0[0], p1[0]) - 1, min(p0[1], p1[1]) - 1,
                    max(p0[0], p1[0]) + 1, max(p0[1], p1[1]) + 1)
            if avoid_rects and any(_rects_overlap(span, r) for r in avoid_rects):
                continue
            return (p0, p1)
        return None

    def _stamp_building(self, sw, sh, sx=None, sy=None, avoid_rects=None, margin=3, two_room=False):
        w, h = self.w, self.h
        rng = self.rng
        if sx is None or sy is None:
            found = False
            for pad in (4, 3, 2):
                for _ in range(150):
                    tx = rng.randrange(margin, w - margin - sw)
                    ty = rng.randrange(margin, h - margin - sh)
                    rect = (tx, ty, tx + sw, ty + sh)
                    if avoid_rects and any(_rects_overlap(rect, r, pad=pad) for r in avoid_rects):
                        continue
                    sx, sy = tx, ty
                    found = True
                    break
                if found:
                    break
            if not found:
                return None, None, None, None, None
        for yy in range(sy, sy + sh):
            for xx in range(sx, sx + sw):
                self.grid[yy][xx] = S.WALL_SHED
        for yy in range(sy + 1, sy + sh - 1):
            for xx in range(sx + 1, sx + sw - 1):
                self.grid[yy][xx] = S.FLOOR

        interior_rects = [(sx + 1, sy + 1, sx + sw - 1, sy + sh - 1)]
        door_sides = ("n", "s", "e", "w")
        interior_door = None
        if two_room and max(sw, sh) >= 7:
            if sw >= sh:
                split_x = sx + sw // 2
                for yy in range(sy + 1, sy + sh - 1):
                    self.grid[yy][split_x] = S.WALL_SHED
                gap_y = sy + sh // 2
                self.grid[gap_y][split_x] = S.FLOOR
                interior_door = ((split_x, gap_y), 0.0)
                interior_rects = [(sx + 1, sy + 1, split_x, sy + sh - 1),
                                   (split_x + 1, sy + 1, sx + sw - 1, sy + sh - 1)]
                door_sides = ("e", "w")
            else:
                split_y = sy + sh // 2
                for xx in range(sx + 1, sx + sw - 1):
                    self.grid[split_y][xx] = S.WALL_SHED
                gap_x = sx + sw // 2
                self.grid[split_y][gap_x] = S.FLOOR
                interior_door = ((gap_x, split_y), math.pi / 2)
                interior_rects = [(sx + 1, sy + 1, sx + sw - 1, split_y),
                                   (sx + 1, split_y + 1, sx + sw - 1, sy + sh - 1)]
                door_sides = ("n", "s")

        mid_x, mid_y = sx + sw // 2, sy + sh // 2
        side = rng.choice(door_sides)
        if side == "n":
            door_cell = (mid_x, sy)
        elif side == "s":
            door_cell = (mid_x, sy + sh - 1)
        elif side == "w":
            door_cell = (sx, mid_y)
        else:
            door_cell = (sx + sw - 1, mid_y)
        self.grid[door_cell[1]][door_cell[0]] = S.FLOOR
        door_facing = 0.0 if side in ("e", "w") else math.pi / 2
        return (sx, sy, sx + sw, sy + sh), door_cell, door_facing, interior_door, interior_rects

    def _carve_debug(self):
        w, h = self.w, self.h
        for y in range(h):
            for x in range(w):
                self.grid[y][x] = S.WALL_FOREST
        for y in range(1, h - 1):
            for x in range(1, w - 1):
                self.grid[y][x] = S.FLOOR
        mats = [S.WALL_CONCRETE, S.WALL_TILE, S.WALL_METAL, S.WALL_BLOOD, S.WALL_FENCE, S.WALL_SHED]
        band = max(1, (w - 2) // len(mats))
        for x in range(1, w - 1):
            self.grid[1][x] = mats[min((x - 1) // band, len(mats) - 1)]
        self.start = (1.5, h - 2.5)

        bx0, by0, bw, bh = w - 20, 1, 18, h - 2
        for yy in range(by0, by0 + bh):
            for xx in range(bx0, bx0 + bw):
                self.grid[yy][xx] = S.WALL_CONCRETE
        for yy in range(by0 + 1, by0 + bh - 1):
            for xx in range(bx0 + 1, bx0 + bw - 1):
                self.grid[yy][xx] = S.FLOOR
        door_y = by0 + bh // 2
        self.grid[door_y][bx0] = S.FLOOR
        self.showcase_rect = (bx0 + 1, by0 + 1, bx0 + bw - 1, by0 + bh - 1)

    def _skin_walls(self):
        weights = [S.WALL_CONCRETE] * 5 + [S.WALL_TILE] * 3 + [S.WALL_METAL] * 3 + [S.WALL_BLOOD]
        if self.wall_bias is not None:
            weights += [self.wall_bias] * 7
        for y in range(self.h):
            for x in range(self.w):
                if self.grid[y][x] not in (S.FLOOR, S.WALL_WINDOW):
                    self.grid[y][x] = self.rng.choice(weights)
        for room in self.rooms:
            bias = S.ROOM_WALL_BIAS.get(room["kind"])
            if bias is None:
                continue
            rx0, ry0, rx1, ry1 = room["rect"]
            for yy in range(ry0 - 1, ry1 + 1):
                for xx in range(rx0 - 1, rx1 + 1):
                    if not (0 <= xx < self.w and 0 <= yy < self.h):
                        continue
                    if self.grid[yy][xx] in (S.FLOOR, S.WALL_WINDOW):
                        continue
                    if self.rng.random() < 0.75:
                        self.grid[yy][xx] = bias

    def floor_cells(self):
        return [(x, y) for y in range(self.h) for x in range(self.w) if self.grid[y][x] == S.FLOOR]

    def room_cells(self, room):
        rx, ry, rx2, ry2 = room["rect"]
        return [(x, y) for y in range(ry, ry2) for x in range(rx, rx2) if self.grid[y][x] == S.FLOOR]

    def room_exit_cells(self, room):
        cells = set(self.room_cells(room))
        exits = set()
        for (cx, cy) in cells:
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = cx + dx, cy + dy
                if (nx, ny) in cells or not (0 <= nx < self.w and 0 <= ny < self.h):
                    continue
                if self.grid[ny][nx] == S.FLOOR:
                    exits.add((nx, ny))
        return exits

    def room_door_count(self, room):
        return len(self.room_exit_cells(room)) + len(room.get("interior_doors", []))

    def dead_end_lockers(self, lockers):
        result = set()
        for room in self.rooms:
            cells = set(self.room_cells(room))
            if not cells or self.room_door_count(room) != 1:
                continue
            for lk in lockers:
                cell = (int(lk.x), int(lk.y))
                if cell in cells:
                    result.add(lk)
        return result

    def is_wall(self, x, y):
        ix, iy = int(x), int(y)
        if ix < 0 or iy < 0 or ix >= self.w or iy >= self.h:
            return True
        return self.grid[iy][ix] != S.FLOOR

    def circle_hits_wall(self, x, y, r):
        rd = r * 0.7071067811865476
        for ox, oy in ((r, 0), (-r, 0), (0, r), (0, -r), (0, 0),
                       (rd, rd), (rd, -rd), (-rd, rd), (-rd, -rd)):
            if self.is_wall(x + ox, y + oy):
                return True
        return False

    def blocks_sight(self, x, y):
        ix, iy = int(x), int(y)
        if ix < 0 or iy < 0 or ix >= self.w or iy >= self.h:
            return True
        tile = self.grid[iy][ix]
        return tile != S.FLOOR and tile != S.WALL_WINDOW

    def tile_at(self, x, y):
        ix, iy = int(x), int(y)
        if ix < 0 or iy < 0 or ix >= self.w or iy >= self.h:
            return S.WALL_CONCRETE
        return self.grid[iy][ix]

    def is_walkable_cell(self, x, y):
        if x < 0 or y < 0 or x >= self.w or y >= self.h:
            return False
        return self.grid[y][x] == S.FLOOR

    def is_see_through(self, x, y):
        if x < 0 or y < 0 or x >= self.w or y >= self.h:
            return False
        return self.grid[y][x] in (S.FLOOR, S.WALL_FENCE, S.WALL_WINDOW)

    def wall_adjacent_floor(self, wx, wy):
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = wx + dx, wy + dy
            if self.is_walkable_cell(nx, ny):
                return (nx, ny), (-dx, -dy)
        return None

    def bfs_distances(self, sx, sy, blocked=None):
        dist = {(sx, sy): 0}
        q = deque([(sx, sy)])
        while q:
            cx, cy = q.popleft()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = cx + dx, cy + dy
                if (nx, ny) in dist:
                    continue
                if blocked and (nx, ny) in blocked:
                    continue
                if self.is_walkable_cell(nx, ny):
                    dist[(nx, ny)] = dist[(cx, cy)] + 1
                    q.append((nx, ny))
        return dist

    def bfs_path(self, sx, sy, tx, ty, blocked=None):
        if (sx, sy) == (tx, ty):
            return [(sx, sy)]
        prev = {(sx, sy): None}
        q = deque([(sx, sy)])
        found = False
        while q:
            cx, cy = q.popleft()
            if (cx, cy) == (tx, ty):
                found = True
                break
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = cx + dx, cy + dy
                if (nx, ny) in prev:
                    continue
                if blocked and (nx, ny) in blocked and (nx, ny) != (tx, ty):
                    continue
                if self.is_walkable_cell(nx, ny):
                    prev[(nx, ny)] = (cx, cy)
                    q.append((nx, ny))
        if not found:
            return []
        path = [(tx, ty)]
        while prev[path[-1]] is not None:
            path.append(prev[path[-1]])
        path.reverse()
        return path

    def has_line_of_sight(self, x0, y0, x1, y1, step=0.1):
        dx, dy = x1 - x0, y1 - y0
        dist = (dx * dx + dy * dy) ** 0.5
        if dist < 1e-6:
            return True
        steps = max(1, int(dist / step))
        for i in range(1, steps):
            t = i / steps
            if self.blocks_sight(x0 + dx * t, y0 + dy * t):
                return False
        return True
