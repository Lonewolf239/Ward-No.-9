import math
import random
from collections import deque

from game import settings as S
from game import room_templates
from game import zone_templates


def window_axis_on(x, y, is_open):
    open_x = is_open(x - 1, y) or is_open(x + 1, y)
    open_y = is_open(x, y - 1) or is_open(x, y + 1)
    if open_x != open_y:
        return "x" if open_x else "y"
    if not open_x:
        return None
    both_x = is_open(x - 1, y) and is_open(x + 1, y)
    both_y = is_open(x, y - 1) and is_open(x, y + 1)
    return "y" if both_y and not both_x else "x"


def bars_arms_on(x, y, is_bars, is_open):
    linked = [(dx, dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)) if is_bars(x + dx, y + dy)]
    along_x = any(dx for dx, dy in linked)
    along_y = any(dy for dx, dy in linked)
    if along_x and along_y:
        return linked
    if along_x:
        return [(1, 0), (-1, 0)]
    if along_y:
        return [(0, 1), (0, -1)]
    return [(0, 1), (0, -1)] if window_axis_on(x, y, is_open) == "x" else [(1, 0), (-1, 0)]


_KEEP_WALLS = (S.WALL_FOREST, S.WALL_OUTDOOR, S.WALL_FENCE)


class Maze:
    def __init__(self, w=S.MAZE_W, h=S.MAZE_H, seed=None, wall_bias=None, layout="corridor",
                 template_floor=None, room_count_range=None, anomaly=None):
        self.w = w if w % 2 == 1 else w + 1
        self.h = h if h % 2 == 1 else h + 1
        self.rng = random.Random(seed)
        self.wall_bias = wall_bias
        self.layout = layout
        self.template_floor = template_floor
        self.anomaly = S.ANOMALY_DEFAULT_STAGE if anomaly is None else anomaly
        self.room_count_range = room_count_range or S.TEMPLATE_ROOM_COUNT
        self.grid = [[S.WALL_CONCRETE for _ in range(self.w)] for _ in range(self.h)]
        self.rooms = []
        self.template_doors = []
        self.start = (1.5, 1.5)
        self.showcase_rect = None
        self.surface_showcase_rect = None
        self.debug_origin = (0.0, 0.0)
        self.zones = []
        self.reset_derived()
        if layout == "yard":
            self._carve_yard()
        elif layout == "micro_yard":
            self._carve_micro_yard()
        elif layout == "micro_room":
            self._carve_micro_room()
        elif layout == "forest_run":
            self._carve_forest_run()
        elif layout == "debug":
            self._carve_debug()
        elif layout == "blank":
            pass
        else:
            self._carve_template_rooms(template_floor)

    def _carve_template_rooms(self, floor_key):
        target = self.rng.randint(*self.room_count_range)
        result = room_templates.generate(self.rng, floor_key, target, anomaly=self.anomaly)
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
        for y in range(h):
            for x in range(w):
                self.grid[y][x] = S.WALL_FOREST
        for y in range(1, h - 1):
            for x in range(1, w - 1):
                self.grid[y][x] = S.WALL_FENCE
        for y in range(2, h - 2):
            for x in range(2, w - 2):
                self.grid[y][x] = S.FLOOR

        self.zones = zone_templates.generate_yard_zones(self.rng, self.grid, 2, 2)

        self.start = (w / 2 + 0.5, h / 2 + 0.5)
        if self.grid[int(self.start[1])][int(self.start[0])] != S.FLOOR:
            self.grid[int(self.start[1])][int(self.start[0])] = S.FLOOR

    def _carve_micro_yard(self):
        w, h = self.w, self.h
        for y in range(h):
            for x in range(w):
                self.grid[y][x] = S.WALL_FOREST
        for y in range(1, h - 1):
            for x in range(1, w - 1):
                self.grid[y][x] = S.WALL_FENCE
        for y in range(2, h - 2):
            for x in range(2, w - 2):
                self.grid[y][x] = S.FLOOR
        self.start = (w / 2 + 0.5, h / 2 + 0.5)

    def _carve_micro_room(self):
        w, h = self.w, self.h
        for y in range(h):
            for x in range(w):
                self.grid[y][x] = S.WALL_CONCRETE
        for y in range(1, h - 1):
            for x in range(1, w - 1):
                self.grid[y][x] = S.FLOOR
        self.start = (w / 2 + 0.5, h / 2 + 0.5)
        self.rooms = [{"rect": (0, 0, w, h), "kind": "entrance", "furniture": [], "interior_doors": []}]

    def _carve_forest_run(self):
        w, h = self.w, self.h
        for y in range(h):
            for x in range(w):
                self.grid[y][x] = S.WALL_FOREST
        for y in range(1, h - 1):
            for x in range(1, w - 1):
                self.grid[y][x] = S.FLOOR
        self.start = (w / 2 + 0.5, h / 2 + 0.5)
        self.fence_row_y = int(S.FOREST_RUN_PERIOD * 1.5) - 3
        for x in range(1, w - 1):
            self.grid[self.fence_row_y][x] = S.WALL_FENCE

    def _carve_debug(self):
        w, h = self.w, self.h
        for y in range(h):
            for x in range(w):
                self.grid[y][x] = S.WALL_FOREST

        def carve(x0, y0, x1, y1):
            for yy in range(y0, y1 + 1):
                for xx in range(x0, x1 + 1):
                    self.grid[yy][xx] = S.WALL_CONCRETE
            for yy in range(y0 + 1, y1):
                for xx in range(x0 + 1, x1):
                    self.grid[yy][xx] = S.FLOOR
            return (x0 + 1, y0 + 1, x1, y1)

        hall = carve(1, 1, 35, h - 2)
        home = carve(35, 10, 58, 36)
        pen = carve(58, 10, 76, 36)
        door_y = 23
        self.grid[door_y][35] = S.FLOOR
        self.grid[door_y][58] = S.FLOOR

        self.surface_showcase_rect = hall
        self.showcase_rect = pen
        self.debug_origin = (home[0], home[1])
        self.start = (home[0] + 1.5, home[3] - 1.5)

        mats = [S.WALL_CONCRETE, S.WALL_TILE, S.WALL_METAL, S.WALL_BLOOD, S.WALL_FENCE, S.WALL_SHED]
        span = home[2] - home[0]
        band = max(1, span // len(mats))
        for i in range(span):
            self.grid[home[1] - 1][home[0] + i] = mats[min(i // band, len(mats) - 1)]

    def _skin_walls(self):
        weights = [S.WALL_CONCRETE] * 5 + [S.WALL_TILE] * 3 + [S.WALL_METAL] * 3 + [S.WALL_BLOOD]
        if self.wall_bias is not None:
            weights += [self.wall_bias] * 7
        base = self.wall_bias if self.wall_bias is not None else S.WALL_CONCRETE
        self.wall_face = {}
        self.wall_face_tint = {}
        for y in range(self.h):
            for x in range(self.w):
                if (self.grid[y][x] != S.FLOOR and self.grid[y][x] not in S.SEE_THROUGH_WALLS
                        and self.grid[y][x] not in _KEEP_WALLS):
                    self.grid[y][x] = base
        corridor_kinds = ("corridor", "tech_corridor", "vent", "link")
        ordered = ([r for r in self.rooms if r["kind"] not in corridor_kinds]
                   + [r for r in self.rooms if r["kind"] in corridor_kinds])
        not_link = set()
        for room in self.rooms:
            if room["kind"] != "link":
                not_link.update(self.room_cells(room))
        for room in ordered:
            bias = room.get("wall_bias") or S.ROOM_WALL_BIAS.get(room["kind"])
            if bias is None:
                bias = self.rng.choice(weights)
            tint = S.ROOM_WALL_TINT.get(room["kind"])
            rx0, ry0, rx1, ry1 = room["rect"]
            mine = set(self.room_cells(room))
            if room["kind"] == "link":
                mine -= not_link
            for yy in range(ry0 - 1, ry1 + 1):
                for xx in range(rx0 - 1, rx1 + 1):
                    if not (0 <= xx < self.w and 0 <= yy < self.h):
                        continue
                    cell = self.grid[yy][xx]
                    if cell != S.WALL_WINDOW:
                        if cell == S.FLOOR or cell in S.SEE_THROUGH_WALLS or cell in _KEEP_WALLS:
                            continue
                        self.grid[yy][xx] = bias
                        if tint is not None:
                            self.wall_tint[(xx, yy)] = tint
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = xx + dx, yy + dy
                        if (nx, ny) not in mine:
                            continue
                        if self.grid[ny][nx] != S.FLOOR:
                            continue
                        self.wall_face[(xx, yy, dx, dy)] = bias
                        if tint is not None:
                            self.wall_face_tint[(xx, yy, dx, dy)] = tint
                        else:
                            self.wall_face_tint.pop((xx, yy, dx, dy), None)

    def floor_cells(self):
        return [(x, y) for y in range(self.h) for x in range(self.w) if self.grid[y][x] == S.FLOOR]

    def room_cells(self, room):
        own = room.get("cells")
        if own is not None:
            return [(x, y) for (x, y) in own
                    if 0 <= y < self.h and 0 <= x < self.w and self.grid[y][x] == S.FLOOR]
        rx, ry, rx2, ry2 = room["rect"]
        return [(x, y) for y in range(ry, ry2) for x in range(rx, rx2) if self.grid[y][x] == S.FLOOR]

    def wing_darkness(self, at_worst):
        cache = getattr(self, "_wing_dark_cache", None)
        if cache is None:
            cache = self._wing_dark_cache = {}
        if at_worst in cache:
            return cache[at_worst]
        wings = S.FLOOR_WINGS.get(self.template_floor, ())
        out = {}
        if len(wings) >= 2:
            broken = [w.get("broken", 0.0) for w in wings]
            lo, hi = min(broken), max(broken)
            if hi - lo > 1e-6:
                lean = {w["key"]: 1.0 + (at_worst - 1.0) * (w.get("broken", 0.0) - lo) / (hi - lo)
                        for w in wings}
                for room in self.rooms:
                    k = lean.get(room.get("wing"))
                    if k is None or k <= 1.0:
                        continue
                    for c in self.room_cells(room):
                        out[c] = max(out.get(c, 1.0), k)
        cache[at_worst] = out
        return out

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

    def room_center_near(self, cell):
        cx, cy = cell
        for room in self.rooms:
            cells = self.room_cells(room)
            if (cx, cy) in cells and cells:
                avg_x = sum(c[0] for c in cells) / len(cells)
                avg_y = sum(c[1] for c in cells) / len(cells)
                return min(cells, key=lambda c: (c[0] - avg_x) ** 2 + (c[1] - avg_y) ** 2)
        return cell

    def dead_end_lockers(self, lockers):
        result = set()
        for room in self.rooms:
            cells = set(self.room_cells(room))
            if not cells or self.room_door_count(room) != 1:
                continue
            room_lockers = [lk for lk in lockers if (int(lk.x), int(lk.y)) in cells]
            if len(room_lockers) == 1:
                result.add(room_lockers[0])
        return result

    SHED_ROOF_OVERHANG = 0.25
    SHED_ROOF_RAKE = 0.12
    SHED_MAX_WALL_GAP = 2

    def shed_roof_areas(self):
        if getattr(self, "_shed_roof_grid", None) is self.grid:
            return self._shed_roof_areas
        self._shed_roof_areas = shed_roof_areas_for(self.grid, self.w, self.h, self.SHED_MAX_WALL_GAP)
        self._shed_roof_grid = self.grid
        return self._shed_roof_areas

    def under_roof(self, x, y):
        o = self.SHED_ROOF_OVERHANG
        ix, iy = int(x), int(y)
        for area in self.shed_roof_areas():
            if (ix, iy) in area:
                return True
            for cx, cy in ((ix - 1, iy), (ix + 1, iy), (ix, iy - 1), (ix, iy + 1),
                           (ix - 1, iy - 1), (ix + 1, iy - 1), (ix - 1, iy + 1), (ix + 1, iy + 1)):
                if (cx, cy) in area:
                    dx = max(cx - x, x - (cx + 1), 0.0)
                    dy = max(cy - y, y - (cy + 1), 0.0)
                    if dx * dx + dy * dy <= o * o:
                        return True
        return False

    def is_wall(self, x, y):
        ix, iy = int(x), int(y)
        if ix < 0 or iy < 0 or ix >= self.w or iy >= self.h:
            return True
        tile = self.grid[iy][ix]
        if tile == S.FLOOR:
            return False
        if tile == S.WALL_BARS:
            return self._bars_blocks(ix, iy, x, y, 0.0)
        if tile == S.WALL_WINDOW:
            return self._window_blocks(ix, iy, x, y, 0.0)
        return True

    def circle_hits_wall(self, x, y, r):
        x0, x1 = int(math.floor(x - r)), int(math.floor(x + r))
        y0, y1 = int(math.floor(y - r)), int(math.floor(y + r))
        grid, w, h, floor = self.grid, self.w, self.h, S.FLOOR
        r2 = r * r
        bars = S.WALL_BARS
        window = S.WALL_WINDOW
        for iy in range(y0, y1 + 1):
            row = grid[iy] if 0 <= iy < h else None
            for ix in range(x0, x1 + 1):
                inside = row is not None and 0 <= ix < w
                if inside and row[ix] == floor:
                    continue
                if inside and row[ix] == bars:
                    if self._bars_blocks(ix, iy, x, y, r):
                        return True
                    continue
                if inside and row[ix] == window:
                    if self._window_blocks(ix, iy, x, y, r):
                        return True
                    continue
                cx = max(ix, min(x, ix + 1))
                cy = max(iy, min(y, iy + 1))
                dx, dy = x - cx, y - cy
                if dx * dx + dy * dy < r2:
                    return True
        return False

    def blocks_sight(self, x, y):
        ix, iy = int(x), int(y)
        if ix < 0 or iy < 0 or ix >= self.w or iy >= self.h:
            return True
        tile = self.grid[iy][ix]
        return tile != S.FLOOR and tile not in S.SEE_THROUGH_WALLS

    def is_wall_cell(self, x, y):
        if x < 0 or y < 0 or x >= self.w or y >= self.h:
            return True
        return self.grid[y][x] != S.FLOOR

    def is_walkable_cell(self, x, y):
        if x < 0 or y < 0 or x >= self.w or y >= self.h:
            return False
        return self.grid[y][x] == S.FLOOR

    BARS_COLLIDE_HALF = 0.055

    WINDOW_COLLIDE_HALF = 0.075

    def window_axis(self, x, y):
        cached = self._window_axes.get((x, y))
        if cached is None:
            cached = (window_axis_on(x, y, self.is_walkable_cell),)
            self._window_axes[(x, y)] = cached
        return cached[0]

    def _window_blocks(self, ix, iy, x, y, r):
        t = self.WINDOW_COLLIDE_HALF
        if self.window_axis(ix, iy) == "x":
            return abs(x - (ix + 0.5)) < t + r and iy <= y + r and y - r <= iy + 1
        return abs(y - (iy + 0.5)) < t + r and ix <= x + r and x - r <= ix + 1

    def segment_blocked(self, x0, y0, x1, y1, step=0.04):
        dx, dy = x1 - x0, y1 - y0
        steps = max(1, int(math.hypot(dx, dy) / step) + 1)
        for i in range(steps + 1):
            t = i / float(steps)
            if self.is_wall(x0 + dx * t, y0 + dy * t):
                return True
        return False

    def standing_cell(self, x, y):
        ix, iy = int(x), int(y)
        if self.is_walkable_cell(ix, iy):
            return ix, iy
        best = None
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = ix + dx, iy + dy
            if not self.is_walkable_cell(nx, ny):
                continue
            cx, cy = nx + 0.5, ny + 0.5
            if self.segment_blocked(x, y, cx, cy):
                continue
            d = (cx - x) ** 2 + (cy - y) ** 2
            if best is None or d < best[0]:
                best = (d, (nx, ny))
        return best[1] if best is not None else (ix, iy)

    def reset_derived(self):
        self.wall_tint = {}
        self.wall_face = {}
        self.wall_face_tint = {}
        self._bars_arms = {}
        self._window_axes = {}
        self._wing_dark_cache = {}
        self.__dict__.pop("_has_outdoors", None)

    def regrid(self, w, h, grid):
        self.w, self.h, self.grid = w, h, grid
        self.reset_derived()

    def bars_arms(self, x, y):
        cached = self._bars_arms.get((x, y))
        if cached is None:
            cached = bars_arms_on(x, y, self._is_bars, self.is_walkable_cell)
            self._bars_arms[(x, y)] = cached
        return cached

    def _is_bars(self, x, y):
        return 0 <= x < self.w and 0 <= y < self.h and self.grid[y][x] == S.WALL_BARS

    def _bars_blocks(self, ix, iy, x, y, r):
        t = self.BARS_COLLIDE_HALF
        cx, cy = ix + 0.5, iy + 0.5
        for dx, dy in self.bars_arms(ix, iy):
            if dx:
                a, b = (cx, ix + 1.0) if dx > 0 else (float(ix), cx)
                if abs(y - cy) < t + r and x + r > a and x - r < b:
                    return True
            else:
                a, b = (cy, iy + 1.0) if dy > 0 else (float(iy), cy)
                if abs(x - cx) < t + r and y + r > a and y - r < b:
                    return True
        return False

    def moon_reaches(self, x, y):
        if self.has_outdoors():
            ix, iy = int(x), int(y)
            return 0 <= ix < self.w and 0 <= iy < self.h and self.grid[iy][ix] == S.WALL_OUTDOOR
        return not self.under_roof(x, y)

    def has_outdoors(self):
        cached = self.__dict__.get("_has_outdoors")
        if cached is None:
            cached = any(t == S.WALL_OUTDOOR for row in self.grid for t in row)
            self._has_outdoors = cached
        return cached

    def is_see_through(self, x, y):
        if x < 0 or y < 0 or x >= self.w or y >= self.h:
            return False
        return self.grid[y][x] in (S.FLOOR, S.WALL_FENCE, S.WALL_WINDOW,
                                  S.WALL_BARS, S.WALL_OUTDOOR)

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

    def has_line_of_sight(self, x0, y0, x1, y1):
        return self.sight_transmission(x0, y0, x1, y1) > 0.0

    def sight_transmission(self, x0, y0, x1, y1):
        dx, dy = x1 - x0, y1 - y0
        if dx * dx + dy * dy < 1e-12:
            return 1.0
        panes = 0
        ix, iy = int(x0), int(y0)
        end_x, end_y = int(x1), int(y1)
        step_x = 1 if dx > 0 else (-1 if dx < 0 else 0)
        step_y = 1 if dy > 0 else (-1 if dy < 0 else 0)
        t_max_x = ((ix + (dx > 0)) - x0) / dx if dx != 0 else float("inf")
        t_max_y = ((iy + (dy > 0)) - y0) / dy if dy != 0 else float("inf")
        t_delta_x = abs(1.0 / dx) if dx != 0 else float("inf")
        t_delta_y = abs(1.0 / dy) if dy != 0 else float("inf")
        while (ix, iy) != (end_x, end_y):
            if t_max_x < t_max_y:
                ix += step_x
                t_max_x += t_delta_x
            else:
                iy += step_y
                t_max_y += t_delta_y
            if (ix, iy) != (end_x, end_y):
                if self.blocks_sight(ix, iy):
                    return 0.0
                if self.grid[iy][ix] == S.WALL_WINDOW:
                    panes += 1
        return S.MONSTER_WINDOW_SIGHT_MULT ** panes if panes else 1.0

    def sound_transmission(self, x0, y0, x1, y1):
        dx, dy = x1 - x0, y1 - y0
        if dx * dx + dy * dy < 1e-12:
            return 1.0
        walls = 0
        ix, iy = int(x0), int(y0)
        end_x, end_y = int(x1), int(y1)
        step_x = 1 if dx > 0 else (-1 if dx < 0 else 0)
        step_y = 1 if dy > 0 else (-1 if dy < 0 else 0)
        t_max_x = ((ix + (dx > 0)) - x0) / dx if dx != 0 else float("inf")
        t_max_y = ((iy + (dy > 0)) - y0) / dy if dy != 0 else float("inf")
        t_delta_x = abs(1.0 / dx) if dx != 0 else float("inf")
        t_delta_y = abs(1.0 / dy) if dy != 0 else float("inf")
        while (ix, iy) != (end_x, end_y):
            if t_max_x < t_max_y:
                ix += step_x
                t_max_x += t_delta_x
            else:
                iy += step_y
                t_max_y += t_delta_y
            if (ix, iy) != (end_x, end_y) and not self.is_walkable_cell(ix, iy):
                walls += 1
                if walls >= 4:
                    return 0.0
        return S.MONSTER_HEARING_WALL_MUFFLE ** walls if walls else 1.0


def shed_roof_areas_for(grid, w, h, max_gap):
    building = set(S.BUILDING_WALLS)
    wall = [[grid[y][x] in building for x in range(w)] for y in range(h)]
    closed = [row[:] for row in wall]

    def walled_row(y, x0, x1):
        return 0 <= y < h and x0 >= 1 and x1 + 1 < w and wall[y][x0 - 1] and wall[y][x1 + 1]

    def walled_col(x, y0, y1):
        return 0 <= x < w and y0 >= 1 and y1 + 1 < h and wall[y0 - 1][x] and wall[y1 + 1][x]

    def row_line(y, x0, x1):
        return (x0 >= 2 and wall[y][x0 - 2]) or (x1 + 2 < w and wall[y][x1 + 2])

    def col_line(x, y0, y1):
        return (y0 >= 2 and wall[y0 - 2][x]) or (y1 + 2 < h and wall[y1 + 2][x])

    for y in range(h):
        x = 0
        while x < w:
            if wall[y][x]:
                x += 1
                continue
            x0 = x
            while x < w and not wall[y][x]:
                x += 1
            if x - x0 <= max_gap and walled_row(y, x0, x - 1) and row_line(y, x0, x - 1) \
                    and not walled_row(y - 1, x0, x - 1) and not walled_row(y + 1, x0, x - 1):
                for cx in range(x0, x):
                    closed[y][cx] = True
    for x in range(w):
        y = 0
        while y < h:
            if wall[y][x]:
                y += 1
                continue
            y0 = y
            while y < h and not wall[y][x]:
                y += 1
            if y - y0 <= max_gap and walled_col(x, y0, y - 1) and col_line(x, y0, y - 1) \
                    and not walled_col(x - 1, y0, y - 1) and not walled_col(x + 1, y0, y - 1):
                for cy in range(y0, y):
                    closed[cy][x] = True

    outside = [[False] * w for _ in range(h)]
    q = deque()
    for y in range(h):
        for x in range(w):
            if (x in (0, w - 1) or y in (0, h - 1)) and not closed[y][x]:
                outside[y][x] = True
                q.append((x, y))
    while q:
        x, y = q.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and not outside[ny][nx] and not closed[ny][nx]:
                outside[ny][nx] = True
                q.append((nx, ny))

    seen = set()
    areas = []
    for y in range(h):
        for x in range(w):
            if closed[y][x] or outside[y][x] or (x, y) in seen:
                continue
            comp = []
            q = deque([(x, y)])
            seen.add((x, y))
            while q:
                cx, cy = q.popleft()
                comp.append((cx, cy))
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in seen and not closed[ny][nx] \
                            and not outside[ny][nx]:
                        seen.add((nx, ny))
                        q.append((nx, ny))
            if len(comp) < 2:
                continue
            foot = set(comp)
            for cx, cy in comp:
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < w and 0 <= ny < h and closed[ny][nx]:
                            foot.add((nx, ny))
            areas.append(foot)
    merged = []
    for foot in areas:
        joined = [foot]
        for other in merged:
            if other & foot:
                joined.append(other)
        merged = [a for a in merged if a not in joined] if len(joined) > 1 else merged
        if len(joined) > 1:
            merged = [a for a in merged if not (a & foot)]
            union = set()
            for a in joined:
                union |= a
            merged.append(union)
        else:
            merged.append(foot)
    return merged
