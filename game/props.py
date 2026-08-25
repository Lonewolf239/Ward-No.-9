import math
import random

from game import settings as S

NOTE_POOL = {
    "floor0": [f"note.floor0.{i}" for i in range(10)],
    "floor1": [f"note.floor1.{i}" for i in range(10)],
    "floor2": [f"note.floor2.{i}" for i in range(8)],
}

PROP_DEFS = {
    "bed":         dict(hw=0.46, hd=0.30, height=0.25, color=(118, 108, 94),  solid=True,  wall_mounted=False, texture="wood"),
    "desk":        dict(hw=0.36, hd=0.22, height=0.36, color=(92, 70, 48),    solid=True,  wall_mounted=False, texture="wood"),
    "table":       dict(hw=0.42, hd=0.42, height=0.36, color=(100, 84, 60),   solid=True,  wall_mounted=False, texture="wood"),
    "shelf":       dict(hw=0.32, hd=0.16, height=0.88, color=(96, 72, 42),    solid=True,  wall_mounted=True,  texture="wood"),
    "gurney":      dict(hw=0.44, hd=0.20, height=0.42, color=(146, 148, 150), solid=True,  wall_mounted=False, texture="metal"),
    "locker":      dict(hw=0.26, hd=0.28, height=0.98, color=(48, 92, 108),   solid=True,  wall_mounted=True,  texture="metal",
                         interactable="locker"),
    "note_flat":   dict(hw=0.17, hd=0.13, height=0.016, color=(222, 212, 186), solid=False, wall_mounted=False,
                         texture="paper", interactable="note"),
    "battery":     dict(hw=0.09, hd=0.07, height=0.14, color=(110, 205, 135), solid=False, wall_mounted=False,
                         texture="metal", interactable="pickup"),
    "fuse":        dict(hw=0.07, hd=0.07, height=0.20, color=(230, 150, 60),  solid=False, wall_mounted=False,
                         texture="metal", interactable="pickup", emissive=True),
    "valve_key":   dict(hw=0.09, hd=0.09, height=0.18, color=(90, 195, 195),  solid=False, wall_mounted=False,
                         texture="metal", interactable="pickup", emissive=True),
    "sanity_pill": dict(hw=0.07, hd=0.07, height=0.10, color=(196, 176, 224), solid=False, wall_mounted=False,
                         texture="metal", interactable="pickup", emissive=True),
    "fuse_box":    dict(hw=0.28, hd=0.16, height=0.58, color=(120, 96, 30),   solid=True,  wall_mounted=True,  texture="metal",
                         interactable="panel"),
    "valve_panel": dict(hw=0.30, hd=0.18, height=0.62, color=(122, 66, 40),   solid=True,  wall_mounted=True,  texture="metal",
                         interactable="panel"),
    "elevator":    dict(hw=0.46, hd=0.20, height=1.0,  color=(46, 110, 88),   solid=True,  wall_mounted=True,  texture="metal",
                         interactable="exit"),
    "hatch":       dict(hw=0.34, hd=0.34, height=0.18, color=(118, 82, 36),   solid=False, wall_mounted=False, texture="metal",
                         interactable="exit", z0=S.WALL_HEIGHT - 0.18),
    "crate":       dict(hw=0.26, hd=0.26, height=0.34, color=(120, 95, 55),   solid=True,  wall_mounted=False, texture="wood"),
    "barrel":      dict(hw=0.20, hd=0.20, height=0.44, color=(92, 92, 98),    solid=True,  wall_mounted=False, texture="metal"),
    "pipes":       dict(hw=0.40, hd=0.09, height=0.72, color=(72, 78, 84),    solid=False, wall_mounted=True,  texture="metal"),
    "chair":       dict(hw=0.18, hd=0.18, height=0.42, color=(88, 66, 44),    solid=True,  wall_mounted=False, texture="wood"),
    "cabinet":     dict(hw=0.30, hd=0.18, height=0.64, color=(84, 80, 74),    solid=True,  wall_mounted=True,  texture="metal"),
    "sink":        dict(hw=0.24, hd=0.16, height=0.34, color=(152, 152, 148), solid=True,  wall_mounted=True,  texture="metal"),
    "trash_can":   dict(hw=0.14, hd=0.14, height=0.30, color=(66, 70, 66),    solid=True,  wall_mounted=False, texture="metal"),
    "vending":     dict(hw=0.26, hd=0.20, height=0.86, color=(58, 92, 100),   solid=True,  wall_mounted=True,  texture="metal"),
    "clutter_papers": dict(hw=0.09, hd=0.07, height=0.05, color=(206, 196, 172), solid=False, wall_mounted=False,
                            texture="paper"),
    "clutter_bottle":  dict(hw=0.045, hd=0.045, height=0.17, color=(90, 118, 82), solid=False, wall_mounted=False),
    "clutter_junk":    dict(hw=0.15, hd=0.15, height=0.10, color=(92, 86, 76),   solid=False, wall_mounted=False,
                             texture="metal"),
    "lamp_desk":   dict(hw=0.07, hd=0.07, height=0.20, color=(255, 214, 150), solid=False, wall_mounted=False,
                         emissive=True, light_radius=2.7, light_color=(1.0, 0.80, 0.48), flicker=True),
    "sign_exit":   dict(hw=0.15, hd=0.035, height=0.11, color=(60, 230, 100), solid=False, wall_mounted=True,
                         emissive=True, light_radius=2.0, light_color=(0.35, 1.0, 0.5), z0=0.85),
    "wall_sconce": dict(hw=0.09, hd=0.07, height=0.16, color=(255, 205, 135), solid=False, wall_mounted=True,
                         emissive=True, light_radius=3.0, light_color=(1.0, 0.76, 0.43), z0=0.55),
    "monitor":     dict(hw=0.14, hd=0.10, height=0.16, color=(150, 205, 255), solid=False, wall_mounted=False,
                         emissive=True, light_radius=1.7, light_color=(0.55, 0.76, 1.0), flicker=True),
    "tree":        dict(hw=0.18, hd=0.18, height=2.2,  color=(255, 255, 255), solid=True,  wall_mounted=False,
                         collide_hw=0.09, collide_hd=0.09),
    "bush":        dict(hw=0.24, hd=0.24, height=0.34, color=(64, 96, 50),    solid=False, wall_mounted=False),
    "rock":        dict(hw=0.22, hd=0.20, height=0.26, color=(112, 108, 100), solid=False,  wall_mounted=False),
    "shed_lock":   dict(hw=0.48, hd=0.10, height=0.82, color=(90, 86, 80),    solid=True,  wall_mounted=False, texture="metal",
                         interactable="panel"),
    "fence_gap":   dict(hw=0.30, hd=0.10, height=0.82, color=(150, 130, 90),  solid=True,  wall_mounted=True,  texture="wood",
                         interactable="exit"),
    "cutters":     dict(hw=0.10, hd=0.05, height=0.14, color=(205, 85, 40),   solid=False, wall_mounted=False,
                         texture="metal", interactable="pickup", emissive=True),
    "key":         dict(hw=0.06, hd=0.06, height=0.10, color=(212, 182, 60),  solid=False, wall_mounted=False,
                         texture="metal", interactable="pickup", emissive=True),
    "portal":      dict(hw=0.32, hd=0.10, height=0.9,  color=(150, 90, 230),  solid=False, wall_mounted=False,
                         interactable="portal", emissive=True),
}

SURFACE_KINDS = {"desk", "table", "shelf", "gurney"}

SURFACE_ITEM_KINDS = {"monitor", "lamp_desk"}

SURFACE_TOP_FRAC = {"desk": 1.0, "table": 1.0, "shelf": 0.765, "gurney": 1.0}

SHELF_LEVEL_FRACS = (0.085, 0.425, 0.765)


def _surface_top_z0(surf, rng=None):
    if surf.kind == "shelf" and rng is not None:
        return surf.height * rng.choice(SHELF_LEVEL_FRACS)
    return surf.height * SURFACE_TOP_FRAC[surf.kind]

HAND_FURNITURE_KINDS = {
    "bed", "desk", "table", "shelf", "gurney", "crate", "barrel", "pipes",
    "chair", "cabinet", "sink", "trash_can", "vending", "locker",
    "lamp_desk", "wall_sconce", "sign_exit", "monitor", "tree", "bush", "rock",
}

HAND_FURNITURE_BY_KIND = {
    "ward": {"bed", "shelf", "chair", "cabinet", "locker", "lamp_desk", "wall_sconce"},
    "office": {"desk", "shelf", "chair", "cabinet", "locker", "table", "lamp_desk", "monitor", "wall_sconce"},
    "morgue": {"gurney", "shelf", "cabinet", "locker", "wall_sconce"},
    "cafeteria": {"table", "chair", "trash_can", "vending", "wall_sconce", "locker"},
    "plain": {"shelf", "chair", "crate", "locker", "wall_sconce"},
    "stairwell": {"shelf", "cabinet", "locker", "wall_sconce"},
    "boiler": {"barrel", "crate", "pipes", "shelf", "wall_sconce", "locker"},
    "storage": {"crate", "shelf", "pipes", "cabinet", "barrel", "locker", "wall_sconce"},
    "cell": {"gurney", "shelf", "pipes", "trash_can", "sink", "locker", "wall_sconce"},
    "exit": {"shelf", "cabinet", "wall_sconce", "sign_exit", "locker"},
    "unlocker": {"shelf", "cabinet", "pipes", "crate", "wall_sconce", "locker"},
    "corridor": {"wall_sconce", "locker"},
    "tech_corridor": {"pipes", "wall_sconce", "locker"},
    "vent": {"wall_sconce", "locker"},
    "entrance": {"shelf", "chair", "trash_can", "locker", "wall_sconce"},
}

ZONE_HAND_FURNITURE_BY_KIND = {
    "open": {"tree", "bush", "rock", "crate", "barrel"},
    "shed": {"crate", "barrel", "wall_sconce"},
    "tool_shed": {"crate", "barrel", "shelf", "wall_sconce"},
    "storage": {"crate", "barrel", "shelf", "locker", "wall_sconce"},
    "forest": {"tree", "bush", "rock"},
    "alley": {"tree", "bush"},
}


class Prop:
    def __init__(self, kind, x, y, facing=0.0, note_text=None):
        spec = PROP_DEFS[kind]
        self.kind = kind
        self.x = x
        self.y = y
        self.facing = facing
        self.hw = spec["hw"]
        self.hd = spec["hd"]
        self.collide_hw = spec.get("collide_hw", self.hw)
        self.collide_hd = spec.get("collide_hd", self.hd)
        self.height = spec["height"]
        self.base_color = spec["color"]
        self._solid = spec["solid"]
        self.wall_mounted = spec["wall_mounted"]
        self.interactable = spec.get("interactable")
        self.emissive = spec.get("emissive", False)
        self.texture = spec.get("texture")
        self.light_radius = spec.get("light_radius")
        self.light_color = spec.get("light_color")
        self.flicker = spec.get("flicker", False)
        self.z0 = spec.get("z0", 0.0)

        self.picked = False
        self.installed = 0
        self.powered = False
        self.broken = False
        self.note_text = note_text
        self.bob_phase = random.uniform(0, math.tau)
        self.interact_cell = None

    @property
    def alive(self):
        return not self.picked

    @property
    def collide_x(self):
        return self.x

    @property
    def collide_y(self):
        return self.y

    @property
    def collide_facing(self):
        return self.facing

    @property
    def solid(self):
        if self.kind == "shed_lock" and self.powered:
            return False
        return self._solid

    def corners(self):
        fx, fy = math.cos(self.facing), math.sin(self.facing)
        rx, ry = -fy, fx
        cx, cy = self.x, self.y
        return [
            (cx - rx * self.hw - fx * self.hd, cy - ry * self.hw - fy * self.hd),
            (cx + rx * self.hw - fx * self.hd, cy + ry * self.hw - fy * self.hd),
            (cx + rx * self.hw + fx * self.hd, cy + ry * self.hw + fy * self.hd),
            (cx - rx * self.hw + fx * self.hd, cy - ry * self.hw + fy * self.hd),
        ]


def make_prop(kind, cell, facing=0.0, note_text=None):
    return Prop(kind, cell[0] + 0.5, cell[1] + 0.5, facing=facing, note_text=note_text)


class Door:

    def __init__(self, x, y, facing, hw=0.485, hd=0.07, height=S.WALL_HEIGHT - 0.02):
        self.kind = "door"
        self.base_facing = facing
        self.cell = (int(x), int(y))
        self.hw = hw
        self.hd = hd
        self.collide_hw = hw
        self.collide_hd = hd
        self.height = height
        self.z0 = 0.0
        self.base_color = (96, 64, 40)
        self.texture = "wood"
        self.emissive = False
        self.picked = False
        self.interactable = "door"
        self.is_open = False
        self.is_broken = False
        self.swing = 0.0
        self._swing_target = 0.0
        self.break_askew = 0.0
        self.ignore_player = False
        rx, ry = -math.sin(facing), math.cos(facing)
        self._hinge_x = x + rx * hw
        self._hinge_y = y + ry * hw
        self._vx0 = -rx * hw
        self._vy0 = -ry * hw

    @property
    def facing(self):
        return self.base_facing + self.swing * (math.pi / 2) + self.break_askew

    @property
    def x(self):
        angle = self.swing * (math.pi / 2) + self.break_askew
        ca, sa = math.cos(angle), math.sin(angle)
        return self._hinge_x + (self._vx0 * ca - self._vy0 * sa)

    @property
    def y(self):
        angle = self.swing * (math.pi / 2) + self.break_askew
        ca, sa = math.cos(angle), math.sin(angle)
        return self._hinge_y + (self._vx0 * sa + self._vy0 * ca)

    @property
    def solid(self):
        return not self.is_open

    def closed_line(self):
        return (self._hinge_x, self._hinge_y), (self._hinge_x + 2 * self._vx0, self._hinge_y + 2 * self._vy0)

    def keyhole_world_pos(self):
        rx, ry = -math.sin(self.facing), math.cos(self.facing)
        width_offset = S.DOOR_KEYHOLE_LOCAL_Y * self.hw * 2
        return (self.x + rx * width_offset, self.y + ry * width_offset,
                self.z0 + S.DOOR_KEYHOLE_LOCAL_Z * self.height)

    @property
    def collide_x(self):
        return self._hinge_x + self._vx0

    @property
    def collide_y(self):
        return self._hinge_y + self._vy0

    @property
    def collide_facing(self):
        return self.base_facing

    def toggle(self):
        if self.is_broken:
            return
        self.is_open = not self.is_open
        self._swing_target = 1.0 if self.is_open else 0.0
        if not self.is_open:
            self.ignore_player = True

    def break_open(self):
        self.is_open = True
        self.is_broken = True
        self.swing = 1.0
        self._swing_target = 1.0
        self.break_askew = 0.0
        self.base_color = (46, 32, 26)

    def update(self, dt):
        rate = 5.0
        self.swing += (self._swing_target - self.swing) * min(1.0, dt * rate)

    def blocks_point(self, x, y, r):
        return _circle_hits_prop(x, y, r, self)


def line_blocked_by_cover(props, x0, y0, x1, y1, min_height=0.3, step=0.15):
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy)
    if dist < 1e-6:
        return False
    margin = 1.2
    min_x, max_x = min(x0, x1) - margin, max(x0, x1) + margin
    min_y, max_y = min(y0, y1) - margin, max(y0, y1) + margin
    candidates = [
        p for p in props
        if p.solid and p.height >= min_height and not p.picked
        and min_x <= p.x <= max_x and min_y <= p.y <= max_y
    ]
    if not candidates:
        return False
    steps = max(1, int(dist / step))
    for i in range(1, steps):
        t = i / steps
        sx, sy = x0 + dx * t, y0 + dy * t
        for p in candidates:
            fx, fy = math.cos(p.collide_facing), math.sin(p.collide_facing)
            rx, ry = -fy, fx
            lx, ly = sx - p.collide_x, sy - p.collide_y
            local_f = lx * fx + ly * fy
            local_r = lx * rx + ly * ry
            if abs(local_f) <= p.hd + 0.06 and abs(local_r) <= p.hw + 0.06:
                return True
    return False


def _wall_cells_around(maze, floor_cell):
    fx, fy = floor_cell
    out = []
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        wx, wy = fx + dx, fy + dy
        if 0 <= wx < maze.w and 0 <= wy < maze.h and maze.grid[wy][wx] != S.FLOOR:
            facing = math.atan2(-dy, -dx)
            boundary = (wx + 0.5 - dx * 0.5, wy + 0.5 - dy * 0.5)
            out.append((boundary, facing, floor_cell))
    return out


def _fence_wall_cells(maze, floor_cell):
    fx, fy = floor_cell
    out = []
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        wx, wy = fx + dx, fy + dy
        if 0 <= wx < maze.w and 0 <= wy < maze.h and maze.grid[wy][wx] == S.WALL_FENCE:
            facing = math.atan2(-dy, -dx)
            boundary = (wx + 0.5 - dx * 0.5, wy + 0.5 - dy * 0.5)
            out.append((boundary, facing, floor_cell))
    return out


def _wall_mount_position(boundary, facing, hd):
    return (boundary[0] + math.cos(facing) * hd, boundary[1] + math.sin(facing) * hd)


def _authored_prop_position(cell, kind, facing, maze=None, furn_kind_by_cell=None, rng=None):
    cx, cy = cell[0] + 0.5, cell[1] + 0.5
    z0 = PROP_DEFS[kind].get("z0", 0.0)
    if kind in SURFACE_ITEM_KINDS and furn_kind_by_cell:
        base = furn_kind_by_cell.get(cell)
        if base is not None and base[0] in SURFACE_KINDS:
            base_kind, base_facing = base
            z0 = PROP_DEFS[base_kind]["height"] * SURFACE_TOP_FRAC[base_kind]
            if base_kind != "table":
                back_x, back_y = math.cos(base_facing), math.sin(base_facing)
                side_x, side_y = -back_y, back_x
                back_off = max(0.0, PROP_DEFS[base_kind]["hd"] - PROP_DEFS[kind]["hd"] - 0.02)
                side_off = 0.0 if kind == "monitor" else -PROP_DEFS[base_kind]["hw"] * 0.45
                cx += back_x * back_off + side_x * side_off
                cy += back_y * back_off + side_y * side_off
            if kind == "monitor":
                jitter = rng.uniform(-0.12, 0.12) if rng is not None else 0.0
                return cx, cy, z0, base_facing + math.pi + jitter
            return cx, cy, z0, None
    if kind == "chair" and (maze is not None or furn_kind_by_cell is not None):
        hd = PROP_DEFS[kind]["hd"]
        edge_nudge = 0.5 - hd - 0.05
        dx, dy = round(math.cos(facing)), round(math.sin(facing))
        if furn_kind_by_cell:
            front_cell = (cell[0] + dx, cell[1] + dy)
            front = furn_kind_by_cell.get(front_cell)
            if front is not None and front[0] in ("table", "desk"):
                return cx + math.cos(facing) * edge_nudge, cy + math.sin(facing) * edge_nudge, z0, None
        back_cell = (cell[0] - dx, cell[1] - dy)
        if maze is not None and not maze.is_walkable_cell(*back_cell):
            return cx - math.cos(facing) * edge_nudge, cy - math.sin(facing) * edge_nudge, z0, None
        return cx, cy, z0, None
    if not PROP_DEFS[kind]["wall_mounted"]:
        return cx, cy, z0, None
    dx, dy = round(math.cos(facing)), round(math.sin(facing))
    wx, wy = cell[0] - dx, cell[1] - dy
    boundary = (wx + 0.5 + dx * 0.5, wy + 0.5 + dy * 0.5)
    wx_pos, wy_pos = _wall_mount_position(boundary, facing, PROP_DEFS[kind]["hd"])
    return wx_pos, wy_pos, z0, None


def _is_safe_to_block(maze, spawn, all_reachable, blocked_solid, cell):
    trial = blocked_solid | {cell}
    reach = maze.bfs_distances(spawn[0], spawn[1], blocked=trial)
    expected = len(all_reachable) - len(trial & all_reachable.keys())
    return len(reach) == expected


def _circle_hits_prop(x, y, r, p):
    fx, fy = math.cos(p.collide_facing), math.sin(p.collide_facing)
    rx, ry = -fy, fx
    dx, dy = x - p.collide_x, y - p.collide_y
    local_f = dx * fx + dy * fy
    local_r = dx * rx + dy * ry
    closest_r = max(-p.collide_hw, min(p.collide_hw, local_r))
    closest_f = max(-p.collide_hd, min(p.collide_hd, local_f))
    dr, df = local_r - closest_r, local_f - closest_f
    return dr * dr + df * df < r * r


def _pick_monster_spawn(maze, far, ranked, props, rng):
    solid_candidates = [p for p in props if p.solid and (not p.wall_mounted or p.kind == "locker")]

    def clear(cell):
        cx, cy = cell[0] + 0.5, cell[1] + 0.5
        if maze.circle_hits_wall(cx, cy, S.MONSTER_RADIUS):
            return False
        return not any(_circle_hits_prop(cx, cy, S.MONSTER_RADIUS, p) for p in solid_candidates)

    far_clear = [c for c in far if clear(c)]
    if far_clear:
        return rng.choice(far_clear)
    any_clear = [c for c in ranked if clear(c)]
    if any_clear:
        return rng.choice(any_clear)
    return rng.choice(far)


def _edge_physically_clear(maze, props_list, ax, ay, bx, by, radius=None):
    r = radius if radius is not None else S.PLAYER_RADIUS
    dx, dy = bx - ax, by - ay
    mx, my = ax + 0.5 + dx * 0.5, ay + 0.5 + dy * 0.5
    lat_x, lat_y = -dy, dx
    for frac in (0.0, -0.15, 0.15, -0.3, 0.3, -0.42, 0.42):
        x, y = mx + lat_x * frac, my + lat_y * frac
        if maze.is_wall(x, y):
            continue
        if all(not (p.solid and _circle_hits_prop(x, y, r, p)) for p in props_list):
            return True
    return False


def _region_physically_clear(maze, props_list, cell):
    cx, cy = cell
    ring = [cell]
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nx, ny = cx + dx, cy + dy
        if maze.is_walkable_cell(nx, ny):
            ring.append((nx, ny))
    seen_edges = set()
    for ccx, ccy in ring:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = ccx + dx, ccy + dy
            if not maze.is_walkable_cell(nx, ny):
                continue
            edge = tuple(sorted(((ccx, ccy), (nx, ny))))
            if edge in seen_edges:
                continue
            seen_edges.add(edge)
            if not _edge_physically_clear(maze, props_list, ccx, ccy, nx, ny):
                return False
    return True


def _wall_prop(maze, floor_cell, kind, rng, spawn=None, all_reachable=None, blocked_solid=None, note_text=None,
                props_list=None, used=None, spawn_guard=True):
    candidates = _wall_cells_around(maze, floor_cell)
    rng.shuffle(candidates)
    solid = PROP_DEFS[kind]["solid"]
    for boundary, facing, fc in candidates:
        if used is not None and fc in used:
            continue
        x, y = _wall_mount_position(boundary, facing, PROP_DEFS[kind]["hd"])
        if spawn_guard and solid and spawn is not None and _circle_hits_prop(
            spawn[0] + 0.5, spawn[1] + 0.5, S.PLAYER_RADIUS, Prop(kind, x, y, facing=facing)
        ):
            continue
        if solid and blocked_solid is not None:
            if not _is_safe_to_block(maze, spawn, all_reachable, blocked_solid, fc):
                continue
        if solid and props_list is not None:
            trial = Prop(kind, x, y, facing=facing, note_text=note_text)
            if not _region_physically_clear(maze, props_list + [trial], fc):
                continue
        prop = Prop(kind, x, y, facing=facing, note_text=note_text)
        prop.interact_cell = fc
        if solid and blocked_solid is not None:
            blocked_solid.add(fc)
        if used is not None:
            used.add(fc)
        return prop
    return None


def _place_wall_prop_in_cells(maze, cells, kind, dist, used, rng, spawn, all_reachable, blocked_solid,
                               prefer_far=False, props_list=None):
    candidates = []
    for c in cells:
        if c in used:
            continue
        candidates.extend(_wall_cells_around(maze, c))
    if not candidates:
        return None
    if prefer_far:
        candidates.sort(key=lambda t: -dist.get(t[2], 0))
        top = candidates[: max(1, len(candidates) // 6)]
        rng.shuffle(top)
        ordered = top + candidates
    else:
        ordered = list(candidates)
        rng.shuffle(ordered)

    solid = PROP_DEFS[kind]["solid"]
    for boundary, facing, fc in ordered:
        if fc in used:
            continue
        if solid and not _is_safe_to_block(maze, spawn, all_reachable, blocked_solid, fc):
            continue
        if solid and props_list is not None:
            x0, y0 = _wall_mount_position(boundary, facing, PROP_DEFS[kind]["hd"])
            trial = Prop(kind, x0, y0, facing=facing)
            if not _region_physically_clear(maze, props_list + [trial], fc):
                continue
        used.add(fc)
        if solid:
            blocked_solid.add(fc)
        x, y = _wall_mount_position(boundary, facing, PROP_DEFS[kind]["hd"])
        prop = Prop(kind, x, y, facing=facing)
        prop.interact_cell = fc
        return prop
    return None


def _spread_pick(cells, n, used, rng, min_gap=3):
    candidates = [c for c in cells if c not in used]
    rng.shuffle(candidates)
    chosen = []
    for c in candidates:
        if len(chosen) >= n:
            break
        if all(abs(c[0] - o[0]) + abs(c[1] - o[1]) >= min_gap for o in chosen):
            chosen.append(c)
    if len(chosen) < n:
        for c in candidates:
            if len(chosen) >= n:
                break
            if c not in chosen:
                chosen.append(c)
    return chosen


def _pick_surface_spot(surf, item_hw, rng, occupied, tries=16):
    span_x = max(0.02, surf.hd - item_hw)
    span_y = max(0.02, surf.hw - item_hw)
    fx, fy = math.cos(surf.facing), math.sin(surf.facing)
    rx, ry = -fy, fx
    key = getattr(surf, "interact_cell", None)
    existing = occupied.get(key, ()) if key is not None else ()
    best_xy, best_score = None, float("-inf")
    for _ in range(tries):
        along_forward = rng.uniform(-span_x, span_x)
        along_right = rng.uniform(-span_y, span_y)
        x = surf.x + fx * along_forward + rx * along_right
        y = surf.y + fy * along_forward + ry * along_right
        score = min((math.hypot(x - ox, y - oy) - r - item_hw for ox, oy, r in existing), default=999.0)
        if score >= 0:
            best_xy = (x, y)
            break
        if score > best_score:
            best_score, best_xy = score, (x, y)
    x, y = best_xy
    if key is not None:
        occupied.setdefault(key, []).append((x, y, item_hw))
    return x, y


def _place_pickup(kind, cell, surfaces, rng, surface_occupied, on_surface_chance=0.45):
    if surfaces and rng.random() < on_surface_chance:
        surf = surfaces.pop(rng.randrange(len(surfaces)))
        item_hw = PROP_DEFS[kind]["hw"]
        x, y = _pick_surface_spot(surf, item_hw, rng, surface_occupied)
        prop = Prop(kind, x, y, facing=rng.uniform(0, math.tau))
        prop.z0 = _surface_top_z0(surf, rng)
        return prop
    return make_prop(kind, cell)


CLUTTER_KINDS = ("clutter_papers", "clutter_bottle", "clutter_junk")


def _scatter_surface_clutter(props, rng, surface_occupied, chance=0.5):
    added = []
    for surf in props:
        if surf.kind not in SURFACE_KINDS or rng.random() > chance:
            continue
        kind = rng.choice(CLUTTER_KINDS)
        item_hw = PROP_DEFS[kind]["hw"]
        x, y = _pick_surface_spot(surf, item_hw, rng, surface_occupied)
        item = Prop(kind, x, y, facing=rng.uniform(0, math.tau))
        item.z0 = _surface_top_z0(surf, rng)
        added.append(item)
    return added


def _cell_wall_facings(maze, cell):
    cx, cy = cell
    out = []
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        wx, wy = cx + dx, cy + dy
        if 0 <= wx < maze.w and 0 <= wy < maze.h and maze.grid[wy][wx] != S.FLOOR:
            out.append(math.atan2(-dy, -dx))
    return out


def _finalize_physical_safety(maze, props, dist):
    protected_kinds = {"fuse_box", "valve_panel", "elevator", "hatch", "shed_lock", "fence_gap"}
    guard = 0
    while guard < 30:
        guard += 1
        solids = [p for p in props if p.solid and p.kind != "door"]
        removed_any = False
        for (cx, cy) in dist.keys():
            for dx, dy in ((1, 0), (0, 1)):
                nx, ny = cx + dx, cy + dy
                if (nx, ny) not in dist:
                    continue
                if _edge_physically_clear(maze, solids, cx, cy, nx, ny):
                    continue
                mx, my = cx + 0.5 + dx * 0.5, cy + 0.5 + dy * 0.5
                culprits = [
                    p for p in solids
                    if p.kind not in protected_kinds
                    and math.hypot(p.x - mx, p.y - my) < p.hw + p.hd + S.PLAYER_RADIUS + 0.5
                ]
                if not culprits:
                    continue
                for p in culprits:
                    if p in props:
                        props.remove(p)
                removed_any = True
        if not removed_any:
            break
    return props


_RANDOM_DOOR_KIND_CHOICES = ("door", "broken", "passage")


def _resolve_random_door_kind(kind, rng):
    return rng.choice(_RANDOM_DOOR_KIND_CHOICES) if kind == "random" else kind


_PIPE_NEIGHBOR_EPS = 0.05


def link_adjacent_pipes(props):
    pipes = [p for p in props if p.kind == "pipes"]
    for p in pipes:
        p.pipe_open_neg = False
        p.pipe_open_pos = False
    for a in pipes:
        nx, ny = math.cos(a.facing), math.sin(a.facing)
        tx, ty = -math.sin(a.facing), math.cos(a.facing)
        for b in pipes:
            if b is a:
                continue
            if abs((a.facing - b.facing + math.pi) % math.tau - math.pi) > _PIPE_NEIGHBOR_EPS:
                continue
            dx, dy = b.x - a.x, b.y - a.y
            if abs(dx * nx + dy * ny) > _PIPE_NEIGHBOR_EPS:
                continue
            along = dx * tx + dy * ty
            if abs(along - 1.0) < _PIPE_NEIGHBOR_EPS * 4:
                a.pipe_open_pos = True
            elif abs(along + 1.0) < _PIPE_NEIGHBOR_EPS * 4:
                a.pipe_open_neg = True


BREAKABLE_LIGHT_KINDS = {"wall_sconce", "lamp_desk", "monitor"}


def _break_some_lights(props, rng):
    for p in props:
        if p.kind in BREAKABLE_LIGHT_KINDS and rng.random() < S.BROKEN_LIGHT_CHANCE:
            p.broken = True
    return props


def populate_level(maze, spec, rng):
    sx, sy = int(maze.start[0]), int(maze.start[1])
    spawn = (sx, sy)
    dist = maze.bfs_distances(sx, sy)
    reachable_rooms = [r for r in maze.rooms if any(c in dist for c in maze.room_cells(r))]
    room_cells_all = {c for r in reachable_rooms for c in maze.room_cells(r)}
    resolved_template_doors = [
        (c, f, _resolve_random_door_kind(k, rng)) for c, f, k in maze.template_doors
    ]

    props = []
    used = {(sx, sy)}
    blocked_solid = set()
    surfaces = []
    surface_occupied = {}
    panel_prop = None
    exit_prop = None
    interior_door_specs = []

    for room in reachable_rooms:
        furn_kind_by_cell = {
            tuple(f["cell"]): (f["kind"], f["facing"]) for f in room.get("furniture", [])
            if f["kind"] not in SURFACE_ITEM_KINDS
        }
        for item in room.get("furniture", []):
            cell = tuple(item["cell"])
            kind, facing = item["kind"], item["facing"]
            if cell in used and kind not in SURFACE_ITEM_KINDS:
                continue
            x, y, z0, forced_facing = _authored_prop_position(cell, kind, facing, maze, furn_kind_by_cell, rng)
            if forced_facing is not None:
                facing = forced_facing
            prop = Prop(kind, x, y, facing=facing)
            prop.z0 = z0
            prop.interact_cell = cell
            props.append(prop)
            used.add(cell)
            if PROP_DEFS[kind]["solid"]:
                blocked_solid.add(cell)
            if kind in SURFACE_KINDS:
                surfaces.append(prop)
            elif kind in SURFACE_ITEM_KINDS:
                item_r = max(PROP_DEFS[kind]["hw"], PROP_DEFS[kind]["hd"])
                surface_occupied.setdefault(cell, []).append((x, y, item_r))
            if kind == spec["panel"]:
                panel_prop = prop
            elif kind == spec["exit_prop"]:
                exit_prop = prop
        interior_door_specs.extend(room.get("interior_doors", []))

    dist = maze.bfs_distances(sx, sy, blocked=blocked_solid)

    reachable_room_cells = [c for c in room_cells_all if c in dist]

    def _first_cell_with_wall(cells):
        for c in cells:
            if c not in used and _wall_cells_around(maze, c):
                return c
        return None

    _fallback_cell = (_first_cell_with_wall(reachable_room_cells)
                      or _first_cell_with_wall(dist.keys())
                      or (sx, sy))

    if panel_prop is None:
        panel_room_kind = spec.get("panel_room") or "utility"
        panel_rooms = [r for r in reachable_rooms if r["kind"] == panel_room_kind]
        if panel_rooms:
            panel_cells = maze.room_cells(panel_rooms[0])
            panel_prop = _place_wall_prop_in_cells(maze, panel_cells, spec["panel"], dist, used, rng, spawn, dist, blocked_solid, props_list=props)
        else:
            panel_prop = _place_wall_prop_in_cells(maze, reachable_room_cells, spec["panel"], dist, used, rng, spawn, dist, blocked_solid, props_list=props)
    if panel_prop is None:
        panel_prop = _wall_prop(maze, _fallback_cell, spec["panel"], rng, spawn, dist, blocked_solid, props_list=props, used=used)
    if panel_prop is None:
        panel_prop = _wall_prop(maze, _fallback_cell, spec["panel"], rng, spawn, dist, blocked_solid, used=used)
    if panel_prop is None:
        panel_prop = _wall_prop(maze, (sx, sy), spec["panel"], rng, spawn=(sx, sy))
    if panel_prop is None:
        panel_prop = _wall_prop(maze, (sx, sy), spec["panel"], rng, spawn_guard=False)
    if panel_prop is not None and panel_prop not in props:
        props.append(panel_prop)

    if exit_prop is None:
        exit_room_kind = spec.get("exit_room")
        exit_rooms = [r for r in reachable_rooms if r["kind"] == exit_room_kind] if exit_room_kind else []
        exit_cells = maze.room_cells(exit_rooms[0]) if exit_rooms else reachable_room_cells
        exit_prop = _place_wall_prop_in_cells(
            maze, exit_cells, spec["exit_prop"], dist, used, rng, spawn, dist, blocked_solid, prefer_far=True,
            props_list=props,
        )
    if exit_prop is None:
        exit_prop = _wall_prop(maze, _fallback_cell, spec["exit_prop"], rng, spawn, dist, blocked_solid, props_list=props, used=used)
    if exit_prop is None:
        exit_prop = _wall_prop(maze, _fallback_cell, spec["exit_prop"], rng, spawn, dist, blocked_solid, used=used)
    if exit_prop is None:
        exit_prop = _wall_prop(maze, (sx, sy), spec["exit_prop"], rng, spawn=(sx, sy))
    if exit_prop is None:
        exit_prop = _wall_prop(maze, (sx, sy), spec["exit_prop"], rng, spawn_guard=False)
    if exit_prop is not None and exit_prop not in props:
        props.append(exit_prop)

    used |= {c for c, facing, kind in resolved_template_doors}
    used |= {tuple(spec["cell"]) for spec in interior_door_specs}

    open_cells = [c for c in maze.floor_cells() if c not in used and dist.get(c, 0) >= 3]

    collectible_cells = _spread_pick(open_cells, spec["n_collectible"], used, rng, min_gap=5)
    for cell in collectible_cells:
        used.add(cell)
        props.append(_place_pickup(spec["collectible"], cell, surfaces, rng, surface_occupied, on_surface_chance=0.82))

    battery_cells = _spread_pick(open_cells, spec.get("n_batteries", S.TOTAL_BATTERIES), used, rng, min_gap=3)
    for cell in battery_cells:
        used.add(cell)
        props.append(_place_pickup("battery", cell, surfaces, rng, surface_occupied, on_surface_chance=0.82))

    pill_cells = _spread_pick(open_cells, spec.get("n_sanity_pills", 0), used, rng, min_gap=6)
    for cell in pill_cells:
        used.add(cell)
        props.append(_place_pickup("sanity_pill", cell, surfaces, rng, surface_occupied, on_surface_chance=0.6))

    note_cells = _spread_pick(open_cells, spec.get("n_notes", S.TOTAL_NOTES), used, rng, min_gap=3)
    note_pool = NOTE_POOL[spec["key"]]
    note_texts = rng.sample(note_pool, k=min(len(note_cells), len(note_pool)))
    for i, cell in enumerate(note_cells):
        used.add(cell)
        flat = _place_pickup("note_flat", cell, surfaces, rng, surface_occupied, on_surface_chance=0.82)
        flat.note_text = note_texts[i % len(note_texts)]
        props.append(flat)

    props.extend(_scatter_surface_clutter(props, rng, surface_occupied))

    forced_doors = [(c, f) for c, f, k in resolved_template_doors if k == "door" and dist.get(c, -1) >= 2]
    forced_broken = [(c, f) for c, f, k in resolved_template_doors if k == "broken" and dist.get(c, -1) >= 2]
    doors = []
    chosen_door_cells = []

    for cell, facing in forced_doors:
        if cell in chosen_door_cells:
            continue
        used.add(cell)
        chosen_door_cells.append(cell)
        doors.append(Door(cell[0] + 0.5, cell[1] + 0.5, facing))
    for cell, facing in forced_broken:
        if cell in chosen_door_cells:
            continue
        used.add(cell)
        chosen_door_cells.append(cell)
        broken = Door(cell[0] + 0.5, cell[1] + 0.5, facing)
        broken.break_open()
        doors.append(broken)

    for item in interior_door_specs:
        cell = tuple(item["cell"])
        if cell in chosen_door_cells:
            continue
        used.add(cell)
        chosen_door_cells.append(cell)
        kind = _resolve_random_door_kind(item["kind"], rng)
        if kind in ("window", "passage"):
            continue
        d = Door(cell[0] + 0.5, cell[1] + 0.5, item["facing"])
        if kind == "broken":
            d.break_open()
        doors.append(d)

    ranked = sorted(dist.keys(), key=lambda c: dist[c])
    far = ranked[-max(1, len(ranked) // 4):]
    monster_cell = _pick_monster_spawn(maze, far, ranked, props, rng)

    props = _finalize_physical_safety(maze, props, dist)
    link_adjacent_pipes(props)
    _break_some_lights(props, rng)

    return props, panel_prop, exit_prop, monster_cell, doors


def populate_yard(maze, spec, rng):
    sx, sy = int(maze.start[0]), int(maze.start[1])
    spawn = (sx, sy)
    dist = maze.bfs_distances(sx, sy)
    shed_zone = next(z for z in maze.zones if z["kind"] == "shed")
    sx0, sy0, sx1, sy1 = shed_zone["rect"]

    def in_shed(c):
        return sx0 <= c[0] < sx1 and sy0 <= c[1] < sy1

    all_floor = maze.floor_cells()
    door_cell = shed_zone["interior_doors"][0][:2]
    yard_cells = [c for c in all_floor if not in_shed(c)]

    props = []
    used = {spawn}
    blocked_solid = set()

    dcx, dcy = door_cell
    facing = 0.0
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nx, ny = dcx + dx, dcy + dy
        if 0 <= nx < maze.w and 0 <= ny < maze.h and maze.grid[ny][nx] == S.FLOOR and not in_shed((nx, ny)):
            facing = math.atan2(dy, dx)
            break
    lock = Prop("shed_lock", dcx + 0.5, dcy + 0.5, facing=facing)
    lock.interact_cell = door_cell
    props.append(lock)
    panel_prop = lock
    used.add(door_cell)
    blocked_solid.add(door_cell)
    dist = maze.bfs_distances(sx, sy, blocked=blocked_solid)

    shed_interior_cells = [c for c in all_floor if in_shed(c) and c not in used]
    if shed_interior_cells:
        ccell = shed_interior_cells[0]
        props.append(make_prop("cutters", ccell, facing=rng.uniform(0, math.tau)))
        used.add(ccell)

    yard_doors = []
    for zone in maze.zones:
        if zone["kind"] == "shed":
            continue
        for wx, wy, dfacing, dkind in zone["interior_doors"]:
            cell = (wx, wy)
            d = Door(wx + 0.5, wy + 0.5, dfacing)
            if dkind == "broken":
                d.break_open()
            yard_doors.append(d)
            used.add(cell)
            blocked_solid.add(cell)
    dist = maze.bfs_distances(sx, sy, blocked=blocked_solid)

    for zone in maze.zones:
        for kind, wx, wy, ffacing in zone["furniture"]:
            cell = (wx, wy)
            if cell in used:
                continue
            x, y, z0, forced_facing = _authored_prop_position(cell, kind, ffacing, maze)
            if forced_facing is not None:
                ffacing = forced_facing
            prop = Prop(kind, x, y, facing=ffacing)
            prop.z0 = z0
            prop.interact_cell = cell
            props.append(prop)
            used.add(cell)
            if PROP_DEFS[kind]["solid"]:
                blocked_solid.add(cell)
    dist = maze.bfs_distances(sx, sy, blocked=blocked_solid)

    open_yard = [c for c in yard_cells if c not in used and dist.get(c, 0) >= 2]
    key_cells = _spread_pick(open_yard, spec["n_collectible"], used, rng, min_gap=5)
    for cell in key_cells:
        used.add(cell)
        props.append(make_prop(spec["collectible"], cell, facing=rng.uniform(0, math.tau)))

    fence_candidates = [c for c in yard_cells if c not in used and dist.get(c, 0) >= 4 and _fence_wall_cells(maze, c)]
    fence_candidates.sort(key=lambda c: -dist.get(c, 0))
    exit_prop = None
    hd = PROP_DEFS["fence_gap"]["hd"]
    for c in fence_candidates:
        for boundary, fac, fc in _fence_wall_cells(maze, c):
            if not _is_safe_to_block(maze, spawn, dist, blocked_solid, fc):
                continue
            x, y = _wall_mount_position(boundary, fac, hd)
            trial = Prop("fence_gap", x, y, facing=fac)
            if not _region_physically_clear(maze, props + [trial], fc):
                continue
            trial.interact_cell = fc
            trial.powered = True
            exit_prop = trial
            used.add(fc)
            blocked_solid.add(fc)
            break
        if exit_prop is not None:
            break
    if exit_prop is None:
        for c in yard_cells:
            wc = _fence_wall_cells(maze, c)
            if not wc:
                continue
            boundary, fac, fc = wc[0]
            x, y = _wall_mount_position(boundary, fac, hd)
            exit_prop = Prop("fence_gap", x, y, facing=fac)
            exit_prop.interact_cell = fc
            exit_prop.powered = True
            used.add(fc)
            blocked_solid.add(fc)
            break
    if exit_prop is not None:
        props.append(exit_prop)
        exit_cell = exit_prop.interact_cell
        sign_candidates = [exit_cell] + [
            (exit_cell[0] + dx, exit_cell[1] + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
        ]
        for cell in sign_candidates:
            if cell != exit_cell and (cell in used or not maze.is_walkable_cell(*cell)):
                continue
            sign = _wall_prop(maze, cell, "sign_exit", rng, spawn=spawn, all_reachable=dist,
                               blocked_solid=blocked_solid, props_list=props,
                               used=used if cell != exit_cell else None)
            if sign is not None:
                props.append(sign)
                break

    battery_cells = _spread_pick([c for c in yard_cells if c not in used], S.TOTAL_BATTERIES, used, rng, min_gap=3)
    for cell in battery_cells:
        used.add(cell)
        props.append(make_prop("battery", cell, facing=rng.uniform(0, math.tau)))

    note_cells = _spread_pick([c for c in yard_cells if c not in used], S.TOTAL_NOTES, used, rng, min_gap=3)
    note_pool = NOTE_POOL[spec["key"]]
    note_texts = rng.sample(note_pool, k=min(len(note_cells), len(note_pool)))
    for i, cell in enumerate(note_cells):
        used.add(cell)
        flat = make_prop("note_flat", cell, facing=rng.uniform(0, math.tau))
        flat.note_text = note_texts[i % len(note_texts)]
        props.append(flat)

    ranked = sorted((c for c in dist.keys() if not in_shed(c)), key=lambda c: dist[c])
    far = ranked[-max(1, len(ranked) // 4):]
    monster_cell = _pick_monster_spawn(maze, far, ranked, props, rng)

    props = _finalize_physical_safety(maze, props, dist)
    link_adjacent_pipes(props)
    _break_some_lights(props, rng)

    return props, panel_prop, exit_prop, monster_cell, yard_doors


def populate_debug(maze, rng):
    props = []
    wall_kinds = sorted(k for k, v in PROP_DEFS.items() if v["wall_mounted"])
    floor_kinds = sorted(k for k, v in PROP_DEFS.items() if not v["wall_mounted"] and k != "portal")

    wall_y = 2.0
    wx = 1.3
    for kind in wall_kinds:
        spec = PROP_DEFS[kind]
        wx += spec["hw"]
        x, y = _wall_mount_position((wx, wall_y), math.pi / 2, spec["hd"])
        props.append(Prop(kind, x, y, facing=math.pi / 2))
        wx += spec["hw"] + 0.35

    broken_wall_kinds = sorted(k for k in BREAKABLE_LIGHT_KINDS if PROP_DEFS[k]["wall_mounted"])
    for kind in broken_wall_kinds:
        spec = PROP_DEFS[kind]
        wx += spec["hw"]
        x, y = _wall_mount_position((wx, wall_y), math.pi / 2, spec["hd"])
        broken_prop = Prop(kind, x, y, facing=math.pi / 2)
        broken_prop.broken = True
        props.append(broken_prop)
        wx += spec["hw"] + 0.35

    cols = 8
    spacing = 1.5
    x0, y0 = 1.6, 3.6
    for i, kind in enumerate(floor_kinds):
        row, col = divmod(i, cols)
        cell = (int(x0 + col * spacing), int(y0 + row * spacing))
        note_text = NOTE_POOL["floor0"][0] if kind == "note_flat" else None
        p = make_prop(kind, cell, facing=0.0, note_text=note_text)
        props.append(p)

    broken_floor_kinds = sorted(k for k in BREAKABLE_LIGHT_KINDS if not PROP_DEFS[k]["wall_mounted"])
    for j, kind in enumerate(broken_floor_kinds):
        row, col = divmod(len(floor_kinds) + j, cols)
        cell = (int(x0 + col * spacing), int(y0 + row * spacing))
        p = make_prop(kind, cell, facing=0.0)
        p.broken = True
        props.append(p)

    total_floor_items = len(floor_kinds) + len(broken_floor_kinds)
    rows = -(-total_floor_items // cols)
    door_row_y = int(y0 + (rows + 1) * spacing)
    d_closed = Door(x0 + 0.5, door_row_y + 0.5, 0.0)
    d_open = Door(x0 + 2.3, door_row_y + 0.5, 0.0)
    d_open.is_open = True
    d_open.swing = 1.0
    d_open._swing_target = 1.0
    d_broken = Door(x0 + 4.1, door_row_y + 0.5, 0.0)
    d_broken.break_open()
    doors = [d_closed, d_open, d_broken]

    portal_row_y = door_row_y + 2 * spacing
    portal_labels = ["floor0", "floor1", "floor2"]
    for i, target in enumerate(portal_labels):
        p = make_prop("portal", (int(x0 + i * 2 * spacing), int(portal_row_y)), facing=-math.pi / 2)
        p.target_floor = i
        props.append(p)

    demo_spots = []
    if maze.showcase_rect is not None:
        sx0, sy0, sx1, sy1 = maze.showcase_rect
        wall_y_n, wall_y_s = sy0 - 1, sy1

        def _mount_row(wall_y, facing, wx0, kinds_with_states):
            wx = wx0
            for kind, states in kinds_with_states:
                for state in states:
                    spec = PROP_DEFS[kind]
                    wx += spec["hw"]
                    x, y = _wall_mount_position((wx, wall_y), facing, spec["hd"])
                    p = Prop(kind, x, y, facing=facing)
                    if state == "installed":
                        p.installed = 1
                    elif state == "powered":
                        p.powered = True
                    props.append(p)
                    wx += spec["hw"] + 0.35
                wx += 0.4

        _mount_row(wall_y_n, math.pi / 2, sx0 + 0.5, [
            ("fuse_box", ("base", "installed", "powered")),
            ("valve_panel", ("base", "installed", "powered")),
        ])
        _mount_row(wall_y_s, -math.pi / 2, sx0 + 0.5, [
            ("shed_lock", ("base", "powered")),
            ("elevator", ("base", "powered")),
            ("fence_gap", ("base", "powered")),
        ])
        hatch_base = make_prop("hatch", (sx0 + 2, sy0 + 1), facing=0.0)
        props.append(hatch_base)
        hatch_powered = make_prop("hatch", (sx0 + 2, sy0 + 2), facing=0.0)
        hatch_powered.powered = True
        props.append(hatch_powered)

        def _mount_demo_locker(row):
            # The showcase's own entrance doorway interrupts its west wall at one
            # specific row (maze._carve_debug's door_y) - if a demo row lands on
            # exactly that row there is no wall to mount on, so nudge outward
            # until a real wall segment is found.
            for candidate_row in (row, row + 1, row - 1, row + 2, row - 2):
                wall_candidates = _wall_cells_around(maze, (sx0, candidate_row))
                if wall_candidates:
                    boundary, facing, fc = wall_candidates[0]
                    lx, ly = _wall_mount_position(boundary, facing, PROP_DEFS["locker"]["hd"])
                    locker = Prop("locker", lx, ly, facing=facing)
                    props.append(locker)
                    return locker
            return None

        def _locker_stand_point(lk, extra=0.0):
            # Mirrors Monster._locker_stand_point (game/entities.py) exactly - the
            # spot directly in front of the locker's door, along its facing.
            standoff = lk.hd + 0.34 + extra
            return lk.x + math.cos(lk.facing) * standoff, lk.y + math.sin(lk.facing) * standoff

        # Spread the 4 demo rows evenly across the usable showcase height, clear of the
        # hatch pair near the top - offset-from-center math here previously let the
        # farthest row (stalk) land outside the floor area on short showcases.
        usable_top = sy0 + 4
        usable_bot = sy1 - 2
        span = max(1, usable_bot - usable_top)
        patrol_row, investigate_row, hunt_row, stalk_row = (
            int(usable_top + span * frac) for frac in (0.15, 0.40, 0.65, 0.90)
        )

        investigate_locker = _mount_demo_locker(investigate_row)
        stalk_locker = _mount_demo_locker(stalk_row)

        # Patrol: calm, normal speed, no locker involved.
        demo_spots.append(dict(
            start=(sx0 + 4.0, patrol_row + 0.5), target=(sx0 + 1.5, patrol_row + 0.5),
            has_locker=False, speed=S.MONSTER_BASE_SPEED, alert_target=0.0,
        ))
        # Investigate: normal speed, briefly checks a locker (moderate alert).
        if investigate_locker is not None:
            demo_spots.append(dict(
                start=_locker_stand_point(investigate_locker, extra=3.0),
                target=_locker_stand_point(investigate_locker),
                has_locker=True, speed=S.MONSTER_BASE_SPEED, alert_target=0.4,
                check_seconds=S.MONSTER_LOCKER_CHECK_SECONDS, locker_target=investigate_locker,
            ))
        # Hunt: fast aggressive charge, no locker, max alert.
        demo_spots.append(dict(
            start=(sx0 + 4.0, hunt_row + 0.5), target=(sx0 + 1.5, hunt_row + 0.5),
            has_locker=False, speed=S.MONSTER_HUNT_SPEED, alert_target=1.0,
        ))
        # Stalk: slow creeping approach, then a long hold checking its own locker (max alert).
        if stalk_locker is not None:
            demo_spots.append(dict(
                start=_locker_stand_point(stalk_locker, extra=3.0),
                target=_locker_stand_point(stalk_locker),
                has_locker=True, speed=S.MONSTER_STALK_APPROACH_SPEED, alert_target=1.0,
                check_seconds=S.MONSTER_STALK_OPEN_SECONDS, locker_target=stalk_locker,
            ))
    maze.demo_monster_spots = demo_spots

    monster_cell = (maze.w - 3, maze.h - 3)
    return props, None, None, monster_cell, doors
