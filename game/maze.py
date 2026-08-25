import random
from collections import deque

from game import settings as S
from game import room_templates
from game import zone_templates


class Maze:
    def __init__(self, w=S.MAZE_W, h=S.MAZE_H, seed=None, wall_bias=None, layout="corridor",
                 template_floor=None, room_count_range=None):
        self.w = w if w % 2 == 1 else w + 1
        self.h = h if h % 2 == 1 else h + 1
        self.rng = random.Random(seed)
        self.wall_bias = wall_bias
        self.layout = layout
        self.room_count_range = room_count_range or S.TEMPLATE_ROOM_COUNT
        self.grid = [[S.WALL_CONCRETE for _ in range(self.w)] for _ in range(self.h)]
        self.rooms = []
        self.template_doors = []
        self.start = (1.5, 1.5)
        self.showcase_rect = None
        self.zones = []
        if layout == "yard":
            self._carve_yard()
        elif layout == "debug":
            self._carve_debug()
        else:
            self._carve_template_rooms(template_floor)

    def _carve_template_rooms(self, floor_key):
        target = self.rng.randint(*self.room_count_range)
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
