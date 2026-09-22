import math
import random

from game import settings as S
from game.held_items import HELD_ITEM_DEFS
from game.lighting import FLASH_RANGE
from game.props import line_blocked_by_cover, _circle_hits_prop, props_near_segment


def monster_lit_frac(light_level, light_norm=S.MONSTER_VISION_LIGHT_NORM):
    return min(1.0, max(0.0, light_level) / light_norm)


def monster_vision_base(light_level, light_norm=S.MONSTER_VISION_LIGHT_NORM):
    lit_frac = monster_lit_frac(light_level, light_norm)
    return S.MONSTER_VISION_RANGE + (S.MONSTER_VISION_RANGE_LIT - S.MONSTER_VISION_RANGE) * lit_frac


class Player:
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.angle = 0.0
        self.pitch = 0.0
        self.flashlight_on = False
        self.battery = 100.0
        self.has_lighter = False
        self.lighter_on = False
        self.active_held_item = None
        self.equip_t = 0.0
        self._pre_hide_light = None
        self.has_map = False
        self.map_open = False
        self.pencil_ink = 0.0
        self._pre_map_light = None
        self.map_drawing = False
        self.map_raise_t = 0.0
        self.stamina = S.STAMINA_MAX
        self.stamina_locked = False
        self.stamina_lock_t = 0.0
        self.sanity = S.SANITY_MAX
        self.carried = 0
        self.has_cutters = False
        self.cutters_broken = False
        self.is_hiding = False
        self.hidden_in = None
        self.flashlight_before_peek = False
        self.is_sprinting = False
        self.is_crouching = False
        self.crouch = 0.0
        self.alive = True
        self._step_accum = 0.0
        self.noise_radius = 0.0
        self.fly_z = 0.0
        self._noise_pulse = 0.0
        self._noise_pulse_t = 0.0
        self.moved_this_frame = False
        self.bumped_wall = False
        self.bob_phase = 0.0
        self.move_ease = 0.0
        self.noclip = False
        self.locker_use_count = 0
        self.last_move_dx = 0.0
        self.last_move_dy = 0.0
        self.light_level = 0.0
        self.room_light = 0.0
        self.is_lit = False
        self.lean_t = 0.0
        self.peek_x = x
        self.peek_y = y

    @property
    def cell(self):
        return int(self.x), int(self.y)

    def _collides(self, maze, props, x, y, r):
        if maze.circle_hits_wall(x, y, r):
            return True
        for p in props:
            px, py = p.collide_x, p.collide_y
            if (x - px) ** 2 + (y - py) ** 2 > (r + p.collide_hw + p.collide_hd) ** 2:
                continue
            if not p.solid:
                continue
            if getattr(p, "ignore_player", False):
                continue
            fx, fy = math.cos(p.collide_facing), math.sin(p.collide_facing)
            rx, ry = -fy, fx
            dx, dy = x - px, y - py
            local_f = dx * fx + dy * fy
            local_r = dx * rx + dy * ry
            closest_r = max(-p.collide_hw, min(p.collide_hw, local_r))
            closest_f = max(-p.collide_hd, min(p.collide_hd, local_f))
            dr, df = local_r - closest_r, local_f - closest_f
            if dr * dr + df * df < r * r:
                return True
        return False

    def try_move(self, maze, props, dx, dy):
        if self.noclip:
            self.x += dx
            self.y += dy
            self.bumped_wall = False
            return False
        r = S.PLAYER_RADIUS
        props = props_near_segment(props, self.x, self.y, self.x + dx, self.y + dy,
                                   r + abs(dx) + abs(dy))
        if dx and dy:
            nx, ny = self.x + dx, self.y + dy
            if not self._collides(maze, props, nx, ny, r):
                self.x, self.y = nx, ny
                self.bumped_wall = False
                return False
        bumped = False
        if dx:
            nx = self.x + dx
            if not self._collides(maze, props, nx, self.y, r):
                self.x = nx
            else:
                bumped = True
        if dy:
            ny = self.y + dy
            if not self._collides(maze, props, self.x, ny, r):
                self.y = ny
            else:
                bumped = True
        self.bumped_wall = bumped
        return bumped

    def update_movement(self, dt, keys_down, mouse_dx, mouse_dy, maze, props, turn_left=False, turn_right=False,
                         crouch_held=False, infinite_stamina=False, effective_facing=None,
                         comedown_intensity=0.0):
        self.angle += mouse_dx * S.MOUSE_SENSITIVITY
        if turn_left:
            self.angle -= S.ROT_SPEED * dt
        if turn_right:
            self.angle += S.ROT_SPEED * dt
        self.angle %= 2 * math.pi

        self.pitch -= mouse_dy * S.MOUSE_SENSITIVITY_Y
        self.pitch = max(-S.PITCH_LIMIT, min(S.PITCH_LIMIT, self.pitch))

        self.is_crouching = bool(crouch_held)
        target_crouch = 1.0 if self.is_crouching else 0.0
        self.crouch += (target_crouch - self.crouch) * min(1.0, dt * S.CROUCH_TRANSITION_RATE)

        if self.is_hiding:
            self.moved_this_frame = False
            self.noise_radius = 0.0
            if infinite_stamina:
                self.stamina = S.STAMINA_MAX
                self.stamina_locked = False
                self.stamina_lock_t = 0.0
            else:
                self.stamina = min(S.STAMINA_MAX, self.stamina + S.STAMINA_REGEN_STANDING * dt)
                self._tick_stamina_lock(dt)
            return

        facing = self.angle if effective_facing is None else effective_facing
        fwd_x, fwd_y = math.cos(facing), math.sin(facing)
        strafe_x, strafe_y = -fwd_y, fwd_x

        mv_f = (1 if keys_down.get("forward") else 0) - (1 if keys_down.get("back") else 0)
        mv_s = (1 if keys_down.get("right") else 0) - (1 if keys_down.get("left") else 0)
        moving = bool(mv_f or mv_s)

        if infinite_stamina:
            self.stamina = S.STAMINA_MAX
            self.stamina_locked = False
            self.stamina_lock_t = 0.0

        want_sprint = bool(keys_down.get("sprint")) and mv_f > 0 and moving and not self.is_crouching
        self.is_sprinting = want_sprint and self.stamina > 0 and not self.stamina_locked
        if self.is_crouching:
            speed = S.CROUCH_SPEED
        else:
            speed = S.SPRINT_SPEED if self.is_sprinting else S.WALK_SPEED
        speed *= 1.0 - (1.0 - S.LEAN_SPEED_MULT) * min(1.0, abs(self.lean_t))
        speed *= 1.0 - (1.0 - S.PILL_COMEDOWN_SPEED_MULT) * comedown_intensity

        ease_rate = S.MOVE_ACCEL_RATE if moving else S.MOVE_DECEL_RATE
        self.move_ease += ((1.0 if moving else 0.0) - self.move_ease) * min(1.0, dt * ease_rate)

        self.moved_this_frame = moving
        if moving:
            eff_speed = speed * self.move_ease
            length = math.hypot(mv_f, mv_s) or 1.0
            self.last_move_dx = (fwd_x * mv_f + strafe_x * mv_s) / length
            self.last_move_dy = (fwd_y * mv_f + strafe_y * mv_s) / length
            dx = self.last_move_dx * eff_speed * dt
            dy = self.last_move_dy * eff_speed * dt
            self.try_move(maze, props, dx, dy)
            self._step_accum += eff_speed * dt
            self.bob_phase += dt * self.move_ease * (8.0 if self.is_sprinting else (4.2 if self.is_crouching else 5.3))
            if self.is_crouching:
                self.noise_radius = S.NOISE_CROUCH
            elif self.is_sprinting:
                self.noise_radius = S.NOISE_SPRINT
            else:
                self.noise_radius = S.NOISE_WALK
        else:
            self.noise_radius = 0.0
            self.bumped_wall = False

        if self.bumped_wall:
            self.noise_radius = max(self.noise_radius, S.NOISE_BUMP)

        for p in props:
            if getattr(p, "ignore_player", False) and not p.blocks_point(self.x, self.y, S.PLAYER_RADIUS):
                p.ignore_player = False

        if infinite_stamina:
            pass
        elif self.is_sprinting:
            self.stamina -= S.STAMINA_DRAIN * dt
            if self.stamina <= 0:
                self.stamina = 0.0
                self.stamina_locked = True
                self.stamina_lock_t = S.STAMINA_EXHAUST_LOCKOUT
        else:
            regen = S.STAMINA_REGEN_STANDING if not moving else S.STAMINA_REGEN
            self.stamina = min(S.STAMINA_MAX, self.stamina + regen * dt)
            self._tick_stamina_lock(dt)

    def _tick_stamina_lock(self, dt):
        if not self.stamina_locked:
            return
        self.stamina_lock_t = max(0.0, self.stamina_lock_t - dt)
        if self.stamina_lock_t <= 0.0:
            self.stamina_locked = False

    def make_noise(self, radius, seconds=None):
        if radius > self._noise_pulse:
            self._noise_pulse = radius
        self._noise_pulse_t = max(self._noise_pulse_t,
                                  S.NOISE_PULSE_SECONDS if seconds is None else seconds)

    def tick_noise(self, dt):
        if self._noise_pulse_t > 0.0:
            self.noise_radius = max(self.noise_radius, self._noise_pulse)
            self._noise_pulse_t -= dt
            if self._noise_pulse_t <= 0.0:
                self._noise_pulse = 0.0

    def consume_step(self, step_len=0.55):
        if self._step_accum >= step_len:
            self._step_accum = 0.0
            return True
        return False

    def toggle_map(self):
        if not self.has_map or self.is_hiding:
            return False
        if self.map_open:
            self.map_open = False
            if self._pre_map_light == "flashlight" and self.battery > 0.5:
                self.flashlight_on = True
            elif self._pre_map_light == "lighter" and self.has_lighter:
                self.lighter_on = True
            self._pre_map_light = None
            return True
        self._pre_map_light = "flashlight" if self.flashlight_on else "lighter" if self.lighter_on else None
        self.flashlight_on = False
        self.lighter_on = False
        self.map_open = True
        return True

    def toggle_flashlight(self):
        if self.map_open:
            self.map_open = False
            self._pre_map_light = None
        if self.flashlight_on:
            self.flashlight_on = False
            return True
        if self.lighter_on:
            self.lighter_on = False
            return True
        if self.battery > 0.5:
            self.flashlight_on = True
            return True
        if self.has_lighter and not self.is_hiding:
            self.lighter_on = True
            return True
        return False

    def update_flashlight(self, dt):
        if self.flashlight_on:
            self.battery -= S.FLASHLIGHT_DRAIN * dt
            if self.battery <= 0:
                self.battery = 0
                self.flashlight_on = False
                if self.has_lighter:
                    self.lighter_on = True
                return True
        return False

    def update_held_item(self, dt, force_stow=False):
        desired = None
        if not self.is_hiding and not force_stow:
            desired = ("map" if self.map_open else "flashlight" if self.flashlight_on
                       else "lighter" if self.lighter_on else None)
        if self.active_held_item is None and desired is not None:
            self.active_held_item = desired
        switching = desired is not None and self.active_held_item is not None and desired != self.active_held_item
        target = 0.0 if (switching or desired is None) else 1.0
        item = self.active_held_item
        defs = HELD_ITEM_DEFS.get(item) if item else None
        duration = (defs["pull_seconds"] if target > self.equip_t else defs["stow_seconds"]) if defs else 0.2
        step = dt / max(0.001, duration)
        if target > self.equip_t:
            self.equip_t = min(target, self.equip_t + step)
        else:
            self.equip_t = max(target, self.equip_t - step)
        if self.equip_t <= 0.0:
            self.active_held_item = desired if switching else None
        raise_target = 1.0 if (self.map_drawing and self.active_held_item == "map") else 0.0
        self.map_raise_t += (raise_target - self.map_raise_t) * min(1.0, dt * S.MAP_RAISE_RATE)

    def update_lean(self, dt, lean_left_held, lean_right_held, maze, blockers):
        target = 0.0
        if lean_left_held:
            target -= 1.0
        if lean_right_held:
            target += 1.0
        self.lean_t += (target - self.lean_t) * min(1.0, dt * S.LEAN_TRANSITION_RATE)
        rx, ry = -math.sin(self.angle), math.cos(self.angle)
        if target != 0.0:
            n_steps = 12
            full_dx = rx * target * S.LEAN_MAX_OFFSET
            full_dy = ry * target * S.LEAN_MAX_OFFSET
            blockers = props_near_segment(blockers, self.x, self.y, self.x + full_dx,
                                          self.y + full_dy, S.LEAN_HITBOX_RADIUS)
            prev_frac = 0.0
            max_frac = 1.0
            for step in range(1, n_steps + 1):
                frac = step / n_steps
                mx, my = self.x + full_dx * frac, self.y + full_dy * frac
                if self._collides(maze, blockers, mx, my, S.LEAN_HITBOX_RADIUS):
                    lo, hi = prev_frac, frac
                    for _ in range(6):
                        mid = (lo + hi) * 0.5
                        bx, by = self.x + full_dx * mid, self.y + full_dy * mid
                        if self._collides(maze, blockers, bx, by, S.LEAN_HITBOX_RADIUS):
                            hi = mid
                        else:
                            lo = mid
                    max_frac = lo
                    break
                prev_frac = frac
            if target > 0:
                self.lean_t = min(self.lean_t, max_frac)
            else:
                self.lean_t = max(self.lean_t, -max_frac)
        offset = self.lean_t * S.LEAN_MAX_OFFSET
        self.peek_x, self.peek_y = self.x + rx * offset, self.y + ry * offset

    def add_battery(self, amount=S.BATTERY_PICKUP_AMOUNT):
        self.battery = min(100.0, self.battery + amount)
        if self.lighter_on:
            self.lighter_on = False
            self.flashlight_on = True

    def apply_sanity(self, delta):
        self.sanity = max(0.0, min(S.SANITY_MAX, self.sanity + delta))


def _segments_cross(p0, p1, p2, p3):
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    d1, d2 = cross(p2, p3, p0), cross(p2, p3, p1)
    d3, d4 = cross(p0, p1, p2), cross(p0, p1, p3)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def _closed_door_between(doors, x0, y0, x1, y1):
    for d in doors:
        if d.is_open or d.is_broken:
            continue
        a, b = d.closed_line()
        if _segments_cross((x0, y0), (x1, y1), a, b):
            return True
    return False


def _push_out_of_props(x, y, blocked_props, radius=0.16):
    for p in blocked_props:
        dx, dy = x - p.collide_x, y - p.collide_y
        reach = p.collide_hw + p.collide_hd + radius
        if dx * dx + dy * dy >= reach * reach:
            continue
        fx, fy = math.cos(p.collide_facing), math.sin(p.collide_facing)
        rx, ry = -fy, fx
        local_f = dx * fx + dy * fy
        local_r = dx * rx + dy * ry
        closest_r = max(-p.collide_hw, min(p.collide_hw, local_r))
        closest_f = max(-p.collide_hd, min(p.collide_hd, local_f))
        dr, df = local_r - closest_r, local_f - closest_f
        dist = math.hypot(dr, df)
        if dist >= radius:
            continue
        if dist > 1e-6:
            ndr, ndf = dr / dist, df / dist
        else:
            over_r, over_f = p.collide_hw - abs(local_r), p.collide_hd - abs(local_f)
            if over_r < over_f:
                ndr, ndf = (1.0 if local_r >= 0 else -1.0), 0.0
            else:
                ndr, ndf = 0.0, (1.0 if local_f >= 0 else -1.0)
        push = radius - dist
        x += (ndr * rx + ndf * fx) * push
        y += (ndr * ry + ndf * fy) * push
    return x, y


class Monster:
    PATROL, INVESTIGATE, HUNT, STALK, GUARD = "patrol", "investigate", "hunt", "stalk", "guard"

    def __init__(self, x, y, maze, rng=None, speed_mult=1.0, vision_mult=1.0, blocked_cells=None, lockers=None,
                 doors=None, blocked_prop_candidates=None, dead_end_lockers=None, exit_cell=None,
                 vision_light_norm=S.MONSTER_VISION_LIGHT_NORM):
        self.x = x
        self.y = y
        self.state = Monster.PATROL
        self.rng = rng or random.Random()
        self.speed_mult = speed_mult
        self.vision_mult = vision_mult
        self.vision_light_norm = vision_light_norm
        self.path = []
        self.target_cell = None
        self.replan_timer = 0.0
        self.lose_interest_timer = 0.0
        self.alert_level = 0.0
        self._patrol_wait = 0.0
        self.locker_target = None
        self.locker_target_certain = False
        self.head_yaw = 0.0
        self.glance_phase = None
        self.glance_t = 0.0
        self.glance_locker = None
        self._glance_cooldown = 0.0
        self._glance_armed = None
        self.caught_player = False
        self.walk_phase = 0.0
        self.walk_amp = 0.0
        self.facing = 0.0
        self.blocked_cells = blocked_cells or set()
        self.lockers = lockers or []
        self.doors = doors or []
        self.exit_cell = exit_cell
        self.guard_mode = False
        self.blocked_prop_candidates = blocked_prop_candidates or []
        self.breaking_door = None
        self.break_timer = 0.0
        self.just_opened_door = None
        self.trailing_door = None
        self.just_closed_door = None
        self._search_hops_left = 0
        self.last_known = None
        self.last_known_age = 0.0
        self._searched = set()
        self._search_t = 0.0
        self._door_memory = {}
        self.haunt = None
        self.haunt_age = 0.0
        self._patrol_recent = []
        self._patrol_from = None
        self._room_cells_cache = None
        self._openness_cache = {}
        self._dark_cells = None
        self.just_noticed = False
        self._prev_hiding = False
        self._had_visual_last_frame = False
        self._stuck_time = 0.0
        self._catch_stuck_time = 0.0
        self._nav_stuck_time = 0.0
        self.checking_timer = 0.0
        self.checking_timer_total = S.MONSTER_LOCKER_CHECK_SECONDS
        self._sight_memory_t = 0.0
        self._peripheral_sight_t = 0.0
        self._acute_sight_t = 0.0
        self._glow_sight_t = 0.0
        self._last_track_reach = 0.0
        self._pivoting = False
        self._door_side = {}
        self.last_vision_debug = None
        self.dead_end_lockers = dead_end_lockers or set()
        self.stalk_timer = 0.0
        self.stalk_origin = False
        self.stalk_phase = "wait"
        self.closing_locker = None
        self.closing_timer = 0.0
        self.recent_miss_locker = None
        self.recent_miss_cooldown = 0.0
        self._locker_target_watch = None
        self._locker_target_elapsed = 0.0
        self._ignored_props = {}
        self._temp_blocked_cells = {}
        self._prop_stuck_time = 0.0
        self._turn_probe_timer = 0.0
        self._turn_probe_dir = 1.0
        self.pending_reaction = None
        self.peek_sight_timer = 0.0

    @property
    def cell(self):
        return int(self.x), int(self.y)

    def _room_cell_set(self, maze):
        if self._room_cells_cache is None:
            cells = set()
            for room in getattr(maze, "rooms", ()):
                cells.update(maze.room_cells(room))
            self._room_cells_cache = cells
        return self._room_cells_cache

    def _dark_lean(self, maze, cell):
        if self._dark_cells is None:
            self._dark_cells = (maze.wing_darkness(S.MONSTER_DARK_PATROL)
                                if hasattr(maze, "wing_darkness") else {})
        return self._dark_cells.get(cell, 1.0)

    def _solid_around(self, maze, cell):
        got = self._openness_cache.get(cell)
        if got is None:
            got = sum(1 for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                      if (dx or dy) and not maze.is_walkable_cell(cell[0] + dx, cell[1] + dy))
            self._openness_cache[cell] = got
        return got

    def _patrol_weight(self, maze, cell):
        mx, my = self.cell
        d = math.hypot(cell[0] - mx, cell[1] - my)
        if d < 3.0:
            return 0.0
        pref = S.MONSTER_PATROL_PREFERRED_DIST
        w = 1.0 / (1.0 + abs(d - pref) / pref)
        if cell in self._room_cell_set(maze):
            w *= S.MONSTER_PATROL_ROOM_BONUS
        w *= S.MONSTER_PATROL_OPENNESS[self._solid_around(maze, cell)]
        w *= self._dark_lean(maze, cell)
        if self.haunt is not None and self.haunt_age < S.MONSTER_PATROL_HAUNT_FADE:
            hd = math.hypot(cell[0] - self.haunt[0], cell[1] - self.haunt[1])
            if hd < S.MONSTER_PATROL_HAUNT_RADIUS:
                fade = 1.0 - self.haunt_age / S.MONSTER_PATROL_HAUNT_FADE
                near = 1.0 - hd / S.MONSTER_PATROL_HAUNT_RADIUS
                w *= 1.0 + (S.MONSTER_PATROL_HAUNT_BONUS - 1.0) * fade * near
        if self.exit_cell is not None:
            ed = math.hypot(cell[0] - self.exit_cell[0], cell[1] - self.exit_cell[1])
            if ed < S.MONSTER_PATROL_EXIT_RADIUS:
                w *= S.MONSTER_PATROL_EXIT_BONUS
        if cell in self._patrol_recent:
            w *= S.MONSTER_PATROL_RECENT_PENALTY
        if self._patrol_from is not None:
            hx, hy = mx - self._patrol_from[0], my - self._patrol_from[1]
            hl = math.hypot(hx, hy)
            if hl > 0.5 and d > 0.0:
                behind = -((cell[0] - mx) * hx + (cell[1] - my) * hy) / (hl * d)
                if behind > 0.0:
                    w *= 1.0 - (1.0 - S.MONSTER_PATROL_BACKTRACK_PENALTY) * behind
        return w

    def _pick_patrol_target(self, maze):
        floors = [c for c in maze.floor_cells() if c not in self.blocked_cells]
        if not floors:
            return self.rng.choice(maze.floor_cells())
        best, best_w = None, -1.0
        for _ in range(min(S.MONSTER_PATROL_SAMPLES, len(floors))):
            c = self.rng.choice(floors)
            w = self._patrol_weight(maze, c) * self.rng.uniform(0.75, 1.0)
            if w > best_w:
                best, best_w = c, w
        if best is None:
            best = self.rng.choice(floors)
        self._patrol_recent.append(best)
        if len(self._patrol_recent) > S.MONSTER_PATROL_RECENT:
            self._patrol_recent.pop(0)
        self._patrol_from = self.cell
        return best

    def _pick_guard_target(self, maze):
        if self.exit_cell is None:
            return self._pick_patrol_target(maze)
        ex, ey = self.exit_cell
        radius2 = S.MONSTER_GUARD_RADIUS * S.MONSTER_GUARD_RADIUS
        near = [c for c in maze.floor_cells()
                if c not in self.blocked_cells and (c[0] - ex) ** 2 + (c[1] - ey) ** 2 <= radius2]
        return self.rng.choice(near) if near else self._pick_patrol_target(maze)

    def _locker_stand_point(self, lk):
        standoff = getattr(lk, "hd", 0.28) + 0.34
        fx, fy = math.cos(lk.facing), math.sin(lk.facing)
        return lk.x + fx * standoff, lk.y + fy * standoff

    def _locker_cell(self, lk):
        sx, sy = self._locker_stand_point(lk)
        return int(sx), int(sy)

    def _locker_stalk_stand_point(self, maze, lk):
        fx, fy = math.cos(lk.facing), math.sin(lk.facing)
        base = getattr(lk, "hd", 0.28) + 0.34
        for extra in (1.4, 0.9, 0.5, 0.0):
            sx, sy = lk.x + fx * (base + extra), lk.y + fy * (base + extra)
            if not maze.is_wall(sx, sy):
                return sx, sy
        return self._locker_stand_point(lk)

    def _locker_stalk_cell(self, maze, lk):
        sx, sy = self._locker_stalk_stand_point(maze, lk)
        return int(sx), int(sy)

    def _enter_patrol(self):
        self.state = Monster.GUARD if self.guard_mode else Monster.PATROL
        self.target_cell = None
        self.path = []
        self._patrol_wait = self.rng.uniform(0.5, 1.5)
        self.last_known = None
        self.last_known_age = 0.0
        self._searched.clear()
        self._search_t = 0.0

    def _note_contact(self, cell):
        if cell != self.last_known:
            self._searched.clear()
        self.last_known = cell
        self.last_known_age = 0.0
        self._search_t = 0.0
        self.haunt = cell
        self.haunt_age = 0.0

    def _pick_search_cell(self, maze):
        if self.last_known is None:
            return None
        reach = min(S.MONSTER_SEARCH_MAX_RADIUS,
                    max(3.0, S.MONSTER_SEARCH_SPREAD * self.last_known_age))
        dists = maze.bfs_distances(self.last_known[0], self.last_known[1],
                                   blocked=self.blocked_cells)
        here = maze.bfs_distances(self.cell[0], self.cell[1], blocked=self.blocked_cells)
        rooms = self._room_cell_set(maze)
        inner = reach * 0.4
        best, best_score = None, None
        for c, d in dists.items():
            if c in self._searched or not (inner <= d <= reach):
                continue
            leg = here.get(c)
            if leg is None or leg < S.MONSTER_SEARCH_MIN_LEG:
                continue
            score = leg + self.rng.uniform(0.0, 1.5)
            if c in rooms:
                score -= S.MONSTER_SEARCH_ROOM_BONUS
            if maze.has_line_of_sight(self.x, self.y, c[0] + 0.5, c[1] + 0.5):
                score += S.MONSTER_SEARCH_SEEN_PENALTY
            if best_score is None or score < best_score:
                best, best_score = c, score
        if best is None:
            rest = [c for c, d in dists.items() if d <= reach and c not in self._searched]
            if not rest:
                return None
            best = min(rest, key=lambda c: here.get(c, 10 ** 6))
        self._mark_searched(maze, best)
        return best

    def _mark_searched(self, maze, cell):
        r = S.MONSTER_SEARCH_MARK_RADIUS
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if abs(dx) + abs(dy) <= r:
                    self._searched.add((cell[0] + dx, cell[1] + dy))

    def _check_door_memory(self, maze):
        lead = None
        for d in self.doors:
            if getattr(d, "is_broken", False):
                continue
            dx, dy = d.x - self.x, d.y - self.y
            if dx * dx + dy * dy > S.MONSTER_DOOR_MEMORY_RANGE ** 2:
                continue
            if not maze.has_line_of_sight(self.x, self.y, d.x, d.y):
                continue
            key = id(d)
            was = self._door_memory.get(key)
            now = bool(d.is_open)
            self._door_memory[key] = now
            if (was is False and now and d is not self.just_opened_door
                    and d is not self.trailing_door and lead is None):
                lead = (int(d.x), int(d.y))
        return lead

    def notify_global_noise(self, maze, target_cell):
        if self.state in (Monster.HUNT, Monster.STALK):
            return
        self.state = Monster.INVESTIGATE
        self.target_cell = target_cell
        self.pending_reaction = None
        self._search_hops_left = 3
        self._replan(maze, target_cell)

    def _abandon_target_and_patrol(self):
        if self.locker_target is not None:
            stuck_locker = self.locker_target
            self.closing_locker = stuck_locker
            self.closing_timer = S.MONSTER_LOCKER_CLOSE_SECONDS
            self.recent_miss_locker = stuck_locker
            self.recent_miss_cooldown = S.MONSTER_LOCKER_RECHECK_COOLDOWN
            self.locker_target = None
            self.locker_target_certain = False
            self.stalk_origin = False
            self.checking_timer = 0.0
            self.stalk_timer = 0.0
        self._turn_probe_timer = 0.0
        self._enter_patrol()

    def _locker_in_notice_range(self, lk, reach=None):
        reach = S.MONSTER_LOCKER_NOTICE_RANGE if reach is None else reach
        return math.hypot(lk.x - self.x, lk.y - self.y) < reach

    def _roll_investigate_locker_event(self, maze, player):
        roll = self.rng.random()
        lk = None
        if roll < 0.25:
            notice_range = (S.MONSTER_LOCKER_NOTICE_RANGE_CROUCH if player.is_crouching
                             else S.MONSTER_LOCKER_NOTICE_RANGE_STAND)
            if (player.is_hiding and player.hidden_in is not None
                    and math.hypot(player.hidden_in.x - self.x, player.hidden_in.y - self.y) < notice_range):
                lk = player.hidden_in
                self.locker_target_certain = True
        elif roll < 0.75:
            nearby = [o for o in self.lockers if o is not self.recent_miss_locker
                      and math.hypot(o.x - self.x, o.y - self.y) < S.INVESTIGATE_LOCKER_EVENT_DECOY_RADIUS]
            if nearby:
                lk = self.rng.choice(nearby)
                self.locker_target_certain = False
        if lk is None:
            return
        self.locker_target = lk
        self.target_cell = self._locker_cell(lk)
        self._replan(maze, self.target_cell)

    def _predict_target_cell(self, maze, player, origin_cell):
        ox, oy = origin_cell[0] + 0.5, origin_cell[1] + 0.5
        dx, dy = player.last_move_dx, player.last_move_dy
        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            return origin_cell
        for frac in (1.0, 0.7, 0.5, 0.3):
            tx = ox + dx * S.MONSTER_INTERCEPT_DISTANCE * frac
            ty = oy + dy * S.MONSTER_INTERCEPT_DISTANCE * frac
            if not maze.is_wall(tx, ty):
                return (int(tx), int(ty))
        return origin_cell

    def _nearby_open_cell(self, maze, cx, cy, max_manhattan=4):
        cands = [c for c in maze.floor_cells()
                 if c not in self.blocked_cells and 1 <= abs(c[0] - cx) + abs(c[1] - cy) <= max_manhattan]
        cands.sort(key=lambda c: abs(c[0] - cx) + abs(c[1] - cy))
        return cands

    def _replan(self, maze, target_cell, extra_blocked=None):
        sx, sy = self.cell
        tx, ty = target_cell
        blocked = self.blocked_cells | extra_blocked if extra_blocked else self.blocked_cells
        path = maze.bfs_path(sx, sy, tx, ty, blocked=blocked)
        if not path:
            for cx, cy in self._nearby_open_cell(maze, tx, ty)[:6]:
                path = maze.bfs_path(sx, sy, cx, cy, blocked=blocked)
                if path:
                    break
        if path and path[0] == (sx, sy):
            path.pop(0)
        self.path = path

    def _tick_temp_state(self, dt):
        if self.last_known is not None:
            self.last_known_age += dt
        if self.haunt is not None:
            self.haunt_age += dt
        if self.state == Monster.INVESTIGATE:
            self._search_t += dt
        for pid in list(self._ignored_props):
            self._ignored_props[pid] -= dt
            if self._ignored_props[pid] <= 0:
                del self._ignored_props[pid]
        for cell in list(self._temp_blocked_cells):
            self._temp_blocked_cells[cell] -= dt
            if self._temp_blocked_cells[cell] <= 0:
                del self._temp_blocked_cells[cell]

    def _door_sides(self, doors):
        for d in doors:
            if d.kind != "door" or d.is_broken:
                continue
            dx, dy = self.x - d.collide_x, self.y - d.collide_y
            if dx * dx + dy * dy > 4.0:
                self._door_side.pop(id(d), None)
                continue
            fx, fy = math.cos(d.collide_facing), math.sin(d.collide_facing)
            local_f = dx * fx + dy * fy
            if abs(local_f) > d.collide_hd + S.MONSTER_RADIUS:
                self._door_side[id(d)] = 1.0 if local_f >= 0.0 else -1.0

    def _hold_door_side(self, doors):
        for d in doors:
            if d.kind != "door" or d.is_open or d.is_broken:
                continue
            side = self._door_side.get(id(d))
            if side is None:
                continue
            fx, fy = math.cos(d.collide_facing), math.sin(d.collide_facing)
            dx, dy = self.x - d.collide_x, self.y - d.collide_y
            if abs(dx * -fy + dy * fx) > d.collide_hw + S.MONSTER_RADIUS:
                continue
            need = d.collide_hd + S.MONSTER_RADIUS * 0.8
            along = (dx * fx + dy * fy) * side
            if along < need:
                shift = need - along
                self.x += fx * side * shift
                self.y += fy * side * shift

    def _active_blocked_props(self):
        return [p for p in self.blocked_prop_candidates
                if p.solid and id(p) not in self._ignored_props] + [
            d for d in self.doors if d.solid]

    def _props_within(self, props, dist):
        mx, my = self.x, self.y
        radius = S.MONSTER_RADIUS
        kept = []
        for p in props:
            reach = dist + max(p.hw + p.hd + 0.55, p.collide_hw + p.collide_hd + radius) + p.hw
            dx, dy = p.x - mx, p.y - my
            if dx * dx + dy * dy < reach * reach:
                kept.append(p)
        return kept

    def _move_toward(self, maze, nx, ny, blocked_props=()):
        r = S.MONSTER_RADIUS

        def blocked(x, y):
            if maze.circle_hits_wall(x, y, r):
                return True
            for p in blocked_props:
                if p.kind != "door" and id(p) in self._ignored_props:
                    continue
                if _circle_hits_prop(x, y, r, p):
                    return True
            return False

        if not blocked(nx, ny):
            self.x, self.y = nx, ny
        elif not blocked(nx, self.y):
            self.x = nx
        elif not blocked(self.x, ny):
            self.y = ny

    def _turn_rate(self):
        return (S.MONSTER_TURN_RATE_HUNT if self.state in (Monster.HUNT, Monster.STALK)
                else S.MONSTER_TURN_RATE)

    _GLANCE_PHASES = ("approach", "stop", "turn", "hold", "back")

    def _glance_phase_length(self, phase):
        return {"approach": S.GLANCE_APPROACH_MAX, "stop": S.GLANCE_STOP,
                "turn": S.GLANCE_TURN, "hold": S.GLANCE_HOLD,
                "back": S.GLANCE_BACK}[phase]

    def _update_glance(self, dt, maze, player, grace, blind):
        self._glance_cooldown = max(0.0, self._glance_cooldown - dt)
        calm = (self.state in (Monster.PATROL, Monster.GUARD) and self.locker_target is None
                and self.alert_level < 0.2 and not grace and not blind)
        hiding = player.is_hiding and player.hidden_in is not None

        if self.glance_phase is not None:
            if not calm or not hiding or self.glance_locker is not player.hidden_in:
                self.glance_phase = None
                self.glance_locker = None
                self.head_yaw = 0.0
                return False
            self.glance_t += dt
            lk = self.glance_locker
            if self.glance_phase == "approach" and self._seen_from_locker(lk):
                self.glance_phase = "stop"
                self.glance_t = 0.0
                self.path = []
                self.target_cell = None
            if self.glance_phase == "approach":
                sx, sy = self._locker_stand_point(lk)
                cell = (int(sx), int(sy))
                if self.target_cell != cell:
                    self.target_cell = cell
                    self._replan(maze, cell)
                    self._patrol_wait = 0.0
                if math.hypot(sx - self.x, sy - self.y) <= S.GLANCE_ARRIVE_DIST:
                    self.glance_phase = "stop"
                    self.glance_t = 0.0
                    self.path = []
                    self.target_cell = None
                elif self.glance_t < S.GLANCE_APPROACH_MAX:
                    self.head_yaw = 0.0
                    return False
                else:
                    self.glance_phase = None
                    self.glance_locker = None
                    self.head_yaw = 0.0
                    self.path = []
                    self.target_cell = None
                    return False
            length = self._glance_phase_length(self.glance_phase)
            while self.glance_t >= length:
                self.glance_t -= length
                nxt = self._GLANCE_PHASES.index(self.glance_phase) + 1
                if nxt >= len(self._GLANCE_PHASES):
                    self.glance_phase = None
                    self.glance_locker = None
                    self.head_yaw = 0.0
                    self.path = []
                    self.target_cell = None
                    return False
                self.glance_phase = self._GLANCE_PHASES[nxt]
                length = self._glance_phase_length(self.glance_phase)
            want = math.atan2(lk.y - self.y, lk.x - self.x)
            off = (want - self.facing + math.pi) % (2 * math.pi) - math.pi
            off = max(-S.GLANCE_HEAD_YAW, min(S.GLANCE_HEAD_YAW, off))
            f = self.glance_t / max(1e-6, self._glance_phase_length(self.glance_phase))
            ease = f * f * (3.0 - 2.0 * f)
            if self.glance_phase in ("approach", "stop"):
                self.head_yaw = 0.0
            elif self.glance_phase == "turn":
                self.head_yaw = off * ease
            elif self.glance_phase == "hold":
                self.head_yaw = off
            else:
                self.head_yaw = off * (1.0 - ease)
            return True

        self.head_yaw = 0.0
        if not calm or not hiding:
            self._glance_armed = None
            return False
        lk = player.hidden_in
        near = math.hypot(lk.x - self.x, lk.y - self.y) <= S.GLANCE_RANGE
        if not near:
            if self._glance_armed is lk:
                self._glance_armed = None
            return False
        if self._glance_armed is lk:
            return False
        self._glance_armed = lk
        if self._glance_cooldown > 0.0:
            return False
        want = math.atan2(lk.y - self.y, lk.x - self.x)
        off = abs((want - self.facing + math.pi) % (2 * math.pi) - math.pi)
        if off < S.GLANCE_MIN_OFF_AXIS:
            return False
        if self.rng.random() >= S.GLANCE_CHANCE:
            return False
        self.glance_phase = "stop" if self._seen_from_locker(lk) else "approach"
        self.glance_t = 0.0
        self.glance_locker = lk
        self._glance_cooldown = S.GLANCE_COOLDOWN
        return self.glance_phase == "stop"

    def _seen_from_locker(self, lk):
        dx, dy = self.x - lk.x, self.y - lk.y
        if math.hypot(dx, dy) > S.GLANCE_VISIBLE_RANGE:
            return False
        off = (math.atan2(dy, dx) - lk.facing + math.pi) % (2 * math.pi) - math.pi
        return abs(off) <= S.GLANCE_VISIBLE_ARC

    def _turn_toward(self, target_facing, dt, rate=None):
        rate = self._turn_rate() if rate is None else rate
        diff = (target_facing - self.facing + math.pi) % (2 * math.pi) - math.pi
        max_turn = min(rate, S.MONSTER_TURN_GAIN * abs(diff)) * dt
        self.facing = (self.facing + max(-max_turn, min(max_turn, diff))) % (2 * math.pi)

    def _walk_toward(self, maze, dt, speed, tx, ty, blocked_props=()):
        dx, dy = tx - self.x, ty - self.y
        dist = math.hypot(dx, dy)
        if dist < 1e-6:
            return True
        want = math.atan2(dy, dx)
        self._turn_toward(want, dt)
        err = abs((want - self.facing + math.pi) % (2 * math.pi) - math.pi)
        gait = max(S.MONSTER_TURN_SLOW_MIN, math.cos(err))
        self._pivoting = err > S.MONSTER_PIVOT_ANGLE
        step = min(speed * gait * dt, dist)
        self._move_toward(maze, self.x + math.cos(self.facing) * step,
                          self.y + math.sin(self.facing) * step, blocked_props)
        return math.hypot(tx - self.x, ty - self.y) < S.MONSTER_ARRIVE_DIST

    def _step_toward(self, maze, dt, speed, tx, ty, face=None, blocked_props=(),
                     face_limit=None):
        dx, dy = tx - self.x, ty - self.y
        dist = math.hypot(dx, dy)
        if dist > 1e-6:
            move_ang = math.atan2(dy, dx)
            if face is None:
                want = move_ang
            else:
                want = math.atan2(face[1] - self.y, face[0] - self.x)
                if face_limit is not None:
                    d = (want - move_ang + math.pi) % (2 * math.pi) - math.pi
                    want = move_ang + max(-face_limit, min(face_limit, d))
            self._turn_toward(want, dt)
            if face_limit is not None:
                err = abs((move_ang - self.facing + math.pi) % (2 * math.pi) - math.pi)
                speed *= max(S.MONSTER_TURN_SLOW_MIN, math.cos(err))
        step = min(speed * dt, dist)
        if dist <= step or dist < 1e-6:
            self._move_toward(maze, tx, ty, blocked_props)
            return True
        self._move_toward(maze, self.x + dx / dist * step, self.y + dy / dist * step, blocked_props)
        return False

    def _has_clear_path(self, maze, x0, y0, x1, y1, blocked_props=(), step=0.1, wall_radius=0.0):
        dx, dy = x1 - x0, y1 - y0
        dist = math.hypot(dx, dy)
        if dist < 1e-6:
            return True
        if blocked_props:
            pad = 0.55
            near = []
            inv_len2 = 1.0 / (dist * dist)
            for p in blocked_props:
                px, py = p.x, p.y
                t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) * inv_len2))
                cx, cy = x0 + dx * t, y0 + dy * t
                if (px - cx) ** 2 + (py - cy) ** 2 < (p.hw + p.hd + pad) ** 2:
                    near.append(p)
            blocked_props = near
        steps = max(1, int(dist / step))
        hits_wall = maze.circle_hits_wall if wall_radius else None
        blocked_cells = self.blocked_cells
        radius = S.MONSTER_RADIUS
        for i in range(1, steps + 1):
            t = i / steps
            sx, sy = x0 + dx * t, y0 + dy * t
            if hits_wall(sx, sy, wall_radius) if wall_radius else maze.is_wall(sx, sy):
                return False
            if blocked_cells and (int(sx), int(sy)) in blocked_cells:
                return False
            for p in blocked_props:
                if _circle_hits_prop(sx, sy, radius, p):
                    return False
        return True

    def update(self, dt, maze, player, dread, props, grace=False, blind=False):
        self._tick_temp_state(dt)
        glance_holding = self._update_glance(dt, maze, player, grace, blind)
        if self.pending_reaction is not None:
            self.pending_reaction["timer"] -= dt
            if self.pending_reaction["timer"] <= 0.0:
                pr = self.pending_reaction
                self.pending_reaction = None
                if self.state not in (Monster.HUNT, Monster.STALK):
                    self.state = Monster.INVESTIGATE
                    self.target_cell = pr["target_cell"]
                    self._search_hops_left = pr["hops"]

        if self.closing_timer > 0.0:
            self.closing_timer = max(0.0, self.closing_timer - dt)
            if self.closing_timer <= 0.0:
                self.closing_locker = None
        if self.recent_miss_cooldown > 0.0:
            self.recent_miss_cooldown = max(0.0, self.recent_miss_cooldown - dt)
            if self.recent_miss_cooldown <= 0.0:
                self.recent_miss_locker = None

        if self.locker_target is not None:
            if self.locker_target is not self._locker_target_watch:
                self._locker_target_watch = self.locker_target
                self._locker_target_elapsed = 0.0
            self._locker_target_elapsed += dt
            if self._locker_target_elapsed > S.MONSTER_LOCKER_TARGET_TIMEOUT:
                self._abandon_target_and_patrol()
        else:
            self._locker_target_watch = None
            self._locker_target_elapsed = 0.0

        dist = math.hypot(player.x - self.x, player.y - self.y)

        if grace or blind:
            if grace and self.state != Monster.PATROL:
                self.state = Monster.PATROL
                self.target_cell = None
                self.path = []
            can_see = False
            hearing_hit = False
            spotted_by_beam = False
            glow_spotted = False
            glow_hunt = False
            tracking = False
            track_range = 0.0
            vision = 0.0
            beam_spot = None
            loud_and_close = False
            peek_spotted = False
        else:
            lit_frac = monster_lit_frac(player.light_level, self.vision_light_norm)
            vision = (monster_vision_base(player.light_level, self.vision_light_norm)
                      * self.vision_mult * (1.0 + dread * 0.35))
            hearing = player.noise_radius * (1.0 + dread * 0.2)
            near_enough = dist < vision * 1.6
            transmission = maze.sight_transmission(self.x, self.y, player.x, player.y) if near_enough else 0.0
            has_los = near_enough and transmission > 0.0 and not line_blocked_by_cover(
                self.doors, self.x, self.y, player.x, player.y, min_height=0.1)
            covered = False
            if near_enough and player.is_crouching:
                if line_blocked_by_cover(props, self.x, self.y, player.x, player.y,
                                         min_height=S.MONSTER_COVER_FULL_HEIGHT):
                    covered = True
                elif line_blocked_by_cover(props, self.x, self.y, player.x, player.y):
                    transmission *= S.MONSTER_PARTIAL_COVER_MULT
            ang_to_player = math.atan2(player.y - self.y, player.x - self.x)
            facing_rel = (ang_to_player - self.facing + math.pi) % (2 * math.pi) - math.pi
            abs_rel = abs(facing_rel)
            fov_edge = (S.MONSTER_FOV_HUNT_RAD if self.state == Monster.HUNT
                        else S.MONSTER_FOV_PERIPHERAL_RAD)
            in_fov = abs_rel < fov_edge
            in_acute_fov = abs_rel < S.MONSTER_FOV_ACUTE_RAD
            effective_vision = (vision if in_acute_fov else vision * S.MONSTER_FOV_PERIPHERAL_RANGE_MULT) * transmission
            raw_visible = (not player.is_hiding and dist < effective_vision and has_los
                           and not covered and in_fov)
            patrol_idle = self.state in (Monster.PATROL, Monster.GUARD) and not self.path
            if raw_visible and not in_acute_fov and not patrol_idle:
                self._peripheral_sight_t += dt * transmission
            else:
                self._peripheral_sight_t = 0.0
            if raw_visible and in_acute_fov:
                self._acute_sight_t += dt * transmission
            else:
                self._acute_sight_t = 0.0
            acute_detect_seconds = S.MONSTER_FOV_ACUTE_DETECT_SECONDS_DARK + (
                S.MONSTER_FOV_ACUTE_DETECT_SECONDS_LIT - S.MONSTER_FOV_ACUTE_DETECT_SECONDS_DARK) * lit_frac
            peripheral_detect_seconds = S.MONSTER_FOV_PERIPHERAL_DETECT_SECONDS_DARK + (
                S.MONSTER_FOV_PERIPHERAL_DETECT_SECONDS_LIT - S.MONSTER_FOV_PERIPHERAL_DETECT_SECONDS_DARK) * lit_frac
            can_see = raw_visible and (
                (in_acute_fov and self._acute_sight_t >= acute_detect_seconds)
                or (not in_acute_fov and self._peripheral_sight_t >= peripheral_detect_seconds))
            self.last_vision_debug = (self.x, self.y, self.facing, vision,
                                       vision * S.MONSTER_FOV_PERIPHERAL_RANGE_MULT,
                                       S.MONSTER_FOV_ACUTE_RAD, S.MONSTER_FOV_PERIPHERAL_RAD, patrol_idle)

            peek_visible = False
            transmission_p = 0.0
            dist_p = dist
            if not can_see and not player.is_hiding and abs(getattr(player, "lean_t", 0.0)) > 0.05:
                lx, ly = player.peek_x, player.peek_y
                dist_p = math.hypot(lx - self.x, ly - self.y)
                transmission_p = maze.sight_transmission(self.x, self.y, lx, ly)
                near_peek = dist_p < vision * S.MONSTER_LEAN_RANGE_MULT * transmission_p
                has_los_p = near_peek and transmission_p > 0.0 and not line_blocked_by_cover(
                    self.doors, self.x, self.y, lx, ly, min_height=0.1)
                covered_p = near_peek and line_blocked_by_cover(
                    props, self.x, self.y, lx, ly, min_height=S.MONSTER_COVER_FULL_HEIGHT)
                ang_p = math.atan2(ly - self.y, lx - self.x)
                in_fov_p = abs((ang_p - self.facing + math.pi) % (2 * math.pi) - math.pi) < fov_edge
                peek_visible = near_peek and has_los_p and not covered_p and in_fov_p
            self.peek_sight_timer = self.peek_sight_timer + dt * transmission_p if peek_visible else 0.0
            lean_seconds = acute_detect_seconds * S.MONSTER_LEAN_DETECT_MULT
            peek_spotted = self.peek_sight_timer >= lean_seconds

            spotted_by_beam = False
            if not can_see and not player.is_hiding and player.flashlight_on and has_los and not covered and dist < vision * 1.6:
                ang_to_monster = math.atan2(self.y - player.y, self.x - player.x)
                rel = (ang_to_monster - player.angle + math.pi) % (2 * math.pi) - math.pi
                spotted_by_beam = abs(rel) < 0.5

            glow_range = 0.0
            if not player.is_hiding:
                if player.flashlight_on:
                    glow_range = S.MONSTER_GLOW_RANGE
                elif player.lighter_on:
                    glow_range = S.MONSTER_GLOW_RANGE * S.MONSTER_LIGHTER_GLOW_MULT
            glow_range *= self.vision_mult
            track_range = 0.0
            if self.state == Monster.HUNT and not player.is_hiding and not covered:
                track_range = max(vision * S.MONSTER_HUNT_TRACK_MULT, glow_range)
            far_reach = max(glow_range, track_range)
            clear_line = False
            if far_reach > 0.0 and dist < far_reach and in_fov:
                clear_line = (maze.sight_transmission(self.x, self.y, player.x, player.y) > 0.0
                              and not line_blocked_by_cover(self.doors, self.x, self.y,
                                                            player.x, player.y, min_height=0.1))
            glow_visible = clear_line and glow_range > 0.0 and dist < glow_range
            tracking = clear_line and track_range > 0.0 and dist < track_range
            self._glow_sight_t = self._glow_sight_t + dt if glow_visible else 0.0
            glow_spotted = self._glow_sight_t >= S.MONSTER_GLOW_SECONDS
            glow_hunt = (glow_visible and dist < S.MONSTER_GLOW_HUNT_RANGE * self.vision_mult
                         and self._glow_sight_t >= S.MONSTER_GLOW_HUNT_SECONDS)

            beam_spot = None
            if (not can_see and not spotted_by_beam and not glow_spotted and not player.is_hiding
                    and player.flashlight_on and self.state != Monster.HUNT):
                bx, by, step = player.x, player.y, 0.25
                fx, fy = math.cos(player.angle), math.sin(player.angle)
                hx, hy = bx, by
                for i in range(1, int(FLASH_RANGE / step) + 1):
                    tx, ty = bx + fx * step * i, by + fy * step * i
                    if maze.is_wall(tx, ty):
                        break
                    hx, hy = tx, ty
                if (math.hypot(hx - self.x, hy - self.y) < S.MONSTER_BEAM_SPOT_RANGE
                        and maze.has_line_of_sight(self.x, self.y, hx, hy)):
                    beam_spot = (hx, hy)

            if hearing > 0.0 and not player.is_hiding:
                hearing *= maze.sound_transmission(self.x, self.y, player.x, player.y)
                hearing_hit = dist < hearing
            else:
                hearing_hit = False
            loud_and_close = hearing_hit and dist < hearing * 0.4

        just_hid = player.is_hiding and not self._prev_hiding
        witnessed_hide = just_hid and self._had_visual_last_frame
        witness_reach = self._last_track_reach
        self._prev_hiding = player.is_hiding
        self._had_visual_last_frame = (can_see or spotted_by_beam or peek_spotted
                                       or glow_hunt or tracking)
        self._last_track_reach = max(track_range, vision)
        if (not grace) and witnessed_hide and player.hidden_in is not None and self.state in (Monster.HUNT, Monster.INVESTIGATE):
            lk = player.hidden_in
            if self._locker_in_notice_range(lk, reach=max(S.MONSTER_LOCKER_NOTICE_RANGE,
                                                          witness_reach + 1.0)):
                self.locker_target = lk
                self.locker_target_certain = True
                self.target_cell = self._locker_cell(lk)
                self.state = Monster.INVESTIGATE
                self._replan(maze, self.target_cell)
        elif (not grace) and just_hid and not witnessed_hide and player.hidden_in is not None and self.state == Monster.HUNT:
            lk = player.hidden_in
            if lk in self.dead_end_lockers:
                self.locker_target = lk
                self.locker_target_certain = False
                self.stalk_origin = True
                self.target_cell = self._locker_stalk_cell(maze, lk)
                self._replan(maze, self.target_cell)
            else:
                self.state = Monster.INVESTIGATE
                self.pending_reaction = None
                self._search_hops_left = 3
                self.target_cell = maze.room_center_near((int(lk.x), int(lk.y)))
                self._replan(maze, self.target_cell)

        if (not grace and not blind and player.is_hiding and player.flashlight_on and player.hidden_in is not None
                and self.state != Monster.STALK
                and not (self.locker_target is player.hidden_in and self.locker_target_certain)):
            lk = player.hidden_in
            if (math.hypot(lk.x - self.x, lk.y - self.y) < S.MONSTER_LIT_LOCKER_DETECT_RANGE
                    and maze.has_line_of_sight(self.x, self.y, lk.x, lk.y)):
                self.locker_target = lk
                self.locker_target_certain = True
                self.target_cell = self._locker_cell(lk)
                self.state = Monster.INVESTIGATE
                self.pending_reaction = None
                self._replan(maze, self.target_cell)

        self.just_noticed = False
        if (can_see or spotted_by_beam or peek_spotted or glow_hunt
                or loud_and_close) and self.state != Monster.HUNT:
            self.just_noticed = True

        if self.state != Monster.STALK or can_see or spotted_by_beam or peek_spotted or glow_hunt:
            if can_see or spotted_by_beam or peek_spotted or glow_hunt:
                self.state = Monster.HUNT
                self.lose_interest_timer = S.MONSTER_LOSE_INTEREST_TIME
                self._sight_memory_t = S.MONSTER_SIGHT_MEMORY_SECONDS
                self.target_cell = player.cell
                self.pending_reaction = None
                if self.checking_timer > 0.0 and self.locker_target is not None:
                    self.closing_locker = self.locker_target
                    self.closing_timer = S.MONSTER_LOCKER_CLOSE_SECONDS
                self.locker_target = None
                self.stalk_origin = False
                self._glow_sight_t = 0.0
                self._note_contact(player.cell)
            elif loud_and_close:
                self.state = Monster.HUNT
                self.lose_interest_timer = S.MONSTER_LOSE_INTEREST_TIME * 0.6
                self._sight_memory_t = S.MONSTER_SIGHT_MEMORY_SECONDS
                self.target_cell = player.cell
                self.pending_reaction = None
                if self.checking_timer > 0.0 and self.locker_target is not None:
                    self.closing_locker = self.locker_target
                    self.closing_timer = S.MONSTER_LOCKER_CLOSE_SECONDS
                self.locker_target = None
                self.stalk_origin = False
                self._note_contact(player.cell)
            elif tracking:
                self.target_cell = player.cell
                self.lose_interest_timer = S.MONSTER_LOSE_INTEREST_TIME
                self._sight_memory_t = S.MONSTER_SIGHT_MEMORY_SECONDS
                self._note_contact(player.cell)
            elif self.state == Monster.HUNT and self._sight_memory_t > 0:
                self._sight_memory_t -= dt
                if not self.stalk_origin:
                    self.target_cell = player.cell
                    self._note_contact(player.cell)
            elif self.state == Monster.HUNT:
                if hearing_hit and not self.stalk_origin:
                    self.target_cell = player.cell
                    self._note_contact(player.cell)
                self.lose_interest_timer -= dt
                if self.lose_interest_timer <= 0:
                    self.state = Monster.INVESTIGATE
                    self._search_hops_left = S.MONSTER_SEARCH_HOPS
            elif hearing_hit:
                target = player.cell
                if self.rng.random() < S.MONSTER_INTERCEPT_CHANCE:
                    target = self._predict_target_cell(maze, player, player.cell)
                if self.pending_reaction is None and self.rng.random() < S.MONSTER_REACTION_DELAY_CHANCE:
                    self.pending_reaction = {
                        "target_cell": target, "hops": 3,
                        "timer": self.rng.uniform(S.MONSTER_REACTION_DELAY_MIN, S.MONSTER_REACTION_DELAY_MAX),
                    }
                else:
                    self.state = Monster.INVESTIGATE
                    self.target_cell = target
                    self._search_hops_left = S.MONSTER_SEARCH_HOPS
                self._note_contact(player.cell)
            elif glow_spotted:
                self.state = Monster.INVESTIGATE
                self.target_cell = player.cell
                self.pending_reaction = None
                self._search_hops_left = S.MONSTER_SEARCH_HOPS
                self._glow_sight_t = 0.0
                self._note_contact(player.cell)
            elif beam_spot is not None:
                hx, hy = beam_spot
                f = self.rng.uniform(*S.MONSTER_BEAM_BACKTRACK)
                gx, gy = int(hx + (player.x - hx) * f), int(hy + (player.y - hy) * f)
                if maze.is_walkable_cell(gx, gy) and (gx, gy) not in self.blocked_cells:
                    target = (gx, gy)
                else:
                    near = self._nearby_open_cell(maze, gx, gy)
                    target = near[0] if near else player.cell
                if self.pending_reaction is None and self.rng.random() < S.MONSTER_REACTION_DELAY_CHANCE:
                    self.pending_reaction = {
                        "target_cell": target, "hops": 2,
                        "timer": self.rng.uniform(S.MONSTER_REACTION_DELAY_MIN, S.MONSTER_REACTION_DELAY_MAX),
                    }
                else:
                    self.state = Monster.INVESTIGATE
                    self.target_cell = target
                    self._search_hops_left = S.MONSTER_SEARCH_HOPS
                self._note_contact(target)
            elif self.state in (Monster.PATROL, Monster.GUARD):
                door_lead = self._check_door_memory(maze)
                if door_lead is not None:
                    self.state = Monster.INVESTIGATE
                    self.target_cell = door_lead
                    self._search_hops_left = max(2, S.MONSTER_SEARCH_HOPS // 2)
                    self._note_contact(door_lead)
                    self._replan(maze, door_lead)

        if (not grace and not blind and self.state == Monster.INVESTIGATE and self.checking_timer <= 0.0
                and self.locker_target is None and self._search_hops_left <= 1):
            overuse_frac = min(1.0, player.locker_use_count / S.STALK_RELEASE_OVERUSE_SATURATION)
            chance_per_sec = (S.INVESTIGATE_LOCKER_EVENT_BASE_PROB
                               + S.INVESTIGATE_LOCKER_EVENT_OVERUSE_BONUS * overuse_frac)
            if self.rng.random() < chance_per_sec * dt:
                self._roll_investigate_locker_event(maze, player)

        if (not grace and self.state in (Monster.PATROL, Monster.GUARD)
                and self.checking_timer <= 0.0 and self.locker_target is None):
            overuse_frac = min(1.0, player.locker_use_count / S.STALK_RELEASE_OVERUSE_SATURATION)
            if self.state == Monster.GUARD:
                chance_per_sec = (S.MONSTER_GUARD_LOCKER_CHECK_BASE
                                   + S.MONSTER_GUARD_LOCKER_CHECK_OVERUSE_BONUS * overuse_frac)
            else:
                chance_per_sec = (S.MONSTER_PATROL_LOCKER_CHECK_BASE
                                   + S.MONSTER_PATROL_LOCKER_CHECK_OVERUSE_BONUS * overuse_frac)
            near = [lk for lk in self.lockers if lk is not self.recent_miss_locker
                    and math.hypot(lk.x - self.x, lk.y - self.y) < S.MONSTER_PATROL_LOCKER_NOTICE_RANGE]
            if near and self.rng.random() < chance_per_sec * dt:
                lk = min(near, key=lambda o: math.hypot(o.x - self.x, o.y - self.y))
                self.state = Monster.INVESTIGATE
                self.target_cell = self._locker_cell(lk)
                self.locker_target = lk
                self.locker_target_certain = False
                self._replan(maze, self.target_cell)

        alert_target = 1.0 if self.state in (Monster.HUNT, Monster.STALK) else (0.4 if self.state == Monster.INVESTIGATE else 0.0)
        self.alert_level += (alert_target - self.alert_level) * min(1.0, dt * 2)
        self.alert_level = max(0.0, min(1.0, self.alert_level))

        if self.state == Monster.STALK:
            self.stalk_timer -= dt
            if self.locker_target is not None:
                self.facing = math.atan2(self.locker_target.y - self.y, self.locker_target.x - self.x)
            if self.stalk_timer > 0.0:
                self.walk_amp += (0.0 - self.walk_amp) * min(1.0, dt * 8.0)
                return
            if self.stalk_phase == "wait":
                self.stalk_phase = "approach"
            if self.stalk_phase == "approach" and self.locker_target is not None:
                close_stand = self._locker_stand_point(self.locker_target)
                d = math.hypot(close_stand[0] - self.x, close_stand[1] - self.y)
                if d > 0.06:
                    blocked_props = self._active_blocked_props()
                    self._step_toward(maze, dt, S.MONSTER_STALK_APPROACH_SPEED, close_stand[0], close_stand[1],
                                       face=(self.locker_target.x, self.locker_target.y), blocked_props=blocked_props)
                    self.x, self.y = _push_out_of_props(self.x, self.y, blocked_props)
                    self.walk_phase += dt * S.MONSTER_STALK_APPROACH_SPEED * 5.5
                    self.walk_amp += (1.0 - self.walk_amp) * min(1.0, dt * 8.0)
                    return
                self.stalk_phase = "open"
            if self.checking_timer <= 0.0:
                self.checking_timer = S.MONSTER_STALK_OPEN_SECONDS
                self.checking_timer_total = S.MONSTER_STALK_OPEN_SECONDS

        if self.checking_timer > 0.0:
            if self.state == Monster.HUNT:
                self.checking_timer = 0.0
            else:
                self.checking_timer = max(0.0, self.checking_timer - dt)
                if self.locker_target is not None:
                    self.facing = math.atan2(self.locker_target.y - self.y, self.locker_target.x - self.x)
                if self.checking_timer > 0.0:
                    return
                if (self.locker_target is not None and self.locker_target_certain
                        and player.is_hiding and player.hidden_in is self.locker_target):
                    self.caught_player = True
                elif (self.locker_target is not None and self.stalk_origin
                        and player.is_hiding and player.hidden_in is self.locker_target):
                    sanity_frac = max(0.0, min(1.0, player.sanity / S.SANITY_MAX))
                    overuse_frac = min(1.0, player.locker_use_count / S.STALK_RELEASE_OVERUSE_SATURATION)
                    release_chance = (S.STALK_RELEASE_BASE
                                       + S.STALK_RELEASE_SANITY_WEIGHT * (1.0 - sanity_frac)
                                       - S.STALK_RELEASE_OVERUSE_WEIGHT * overuse_frac)
                    release_chance = max(S.STALK_RELEASE_MIN, min(S.STALK_RELEASE_MAX, release_chance))
                    if self.rng.random() >= release_chance:
                        self.caught_player = True
                    else:
                        player.sanity = max(S.STALK_RELEASE_SANITY_FLOOR,
                                             player.sanity - S.STALK_RELEASE_SANITY_HIT)
                if not self.caught_player:
                    self.closing_locker = self.locker_target
                    self.closing_timer = S.MONSTER_LOCKER_CLOSE_SECONDS
                    self.recent_miss_locker = self.locker_target
                    self.recent_miss_cooldown = S.MONSTER_LOCKER_RECHECK_COOLDOWN
                self.locker_target = None
                self.locker_target_certain = False
                self.stalk_origin = False
                self._enter_patrol()
                return

        if self.state in (Monster.PATROL, Monster.GUARD):
            self._patrol_wait -= dt
            if not self.path and self._patrol_wait <= 0:
                self.target_cell = (self._pick_guard_target(maze) if self.state == Monster.GUARD
                                     else self._pick_patrol_target(maze))
                self._replan(maze, self.target_cell)
                self._patrol_wait = self.rng.uniform(1.0, 3.0) if self.path else 0.15

        self.replan_timer -= dt
        if self.target_cell and self.replan_timer <= 0:
            self.replan_timer = S.MONSTER_REPLAN_INTERVAL
            self._replan(maze, self.target_cell)

        if self.path and self.lockers:
            fcx, fcy = self.path[-1][0] + 0.5, self.path[-1][1] + 0.5
            for lk in self.lockers:
                if math.hypot(fcx - lk.x, fcy - lk.y) > 1.3:
                    continue
                if math.hypot(lk.x - self.x, lk.y - self.y) < 0.9:
                    self.path = []
                    break

        near_locker_target = None
        locker_stand = None
        if self.locker_target is not None:
            if self.stalk_origin and self.state != Monster.STALK:
                locker_stand = self._locker_stalk_stand_point(maze, self.locker_target)
            else:
                locker_stand = self._locker_stand_point(self.locker_target)
            near_locker_target = math.hypot(locker_stand[0] - self.x, locker_stand[1] - self.y)

        if self.breaking_door is not None and (self.breaking_door.is_open or grace):
            self.breaking_door = None
            self.break_timer = 0.0
        self.just_opened_door = None
        self.just_closed_door = None
        if self.breaking_door is None:
            for d in self.doors:
                dcx, dcy = d.cell[0] + 0.5, d.cell[1] + 0.5
                if math.hypot(dcx - self.x, dcy - self.y) >= S.DOOR_BREAK_TRIGGER_DIST:
                    continue
                if d.is_open:
                    if not d.is_broken and (d.cell == self.cell or d.cell in self.path[:2]):
                        self.trailing_door = d
                    continue
                if d.cell != self.cell and d.cell not in self.path[:2]:
                    continue
                if d.is_latched or d._pending_latch:
                    self.breaking_door = d
                    self.break_timer = 0.0
                else:
                    d.toggle()
                    self.just_opened_door = d
                    self.trailing_door = d
                break

        if self.trailing_door is not None:
            td = self.trailing_door
            if td.is_broken or not td.is_open:
                self.trailing_door = None
            else:
                still_needed = not self.path or td.cell in self.path[:2]
                clear = math.hypot(td.x - self.x, td.y - self.y) >= S.DOOR_CLOSE_TRAIL_DIST
                if clear and not still_needed:
                    td.toggle()
                    self.just_closed_door = td
                    self.trailing_door = None

        base = S.MONSTER_HUNT_SPEED if self.state == Monster.HUNT else S.MONSTER_BASE_SPEED
        speed = base * self.speed_mult * (1.0 + dread * 0.25)
        speed = min(speed, S.SPRINT_SPEED * S.MONSTER_HUNT_SPEED_CAP_RATIO)
        if glance_holding:
            speed = 0.0
        blocked_props = self._active_blocked_props()
        local_props = self._props_within(blocked_props, S.MONSTER_PATH_LOOKAHEAD_DIST + 1.0)
        close_direct_chase = (self.state == Monster.HUNT and not grace and dist < 1.3
                               and self._has_clear_path(maze, self.x, self.y, player.x, player.y, local_props))
        no_path_fallback = (
            not close_direct_chase and not self.path and self.target_cell is not None and not grace
            and self._has_clear_path(maze, self.x, self.y,
                                      self.target_cell[0] + 0.5, self.target_cell[1] + 0.5, blocked_props,
                                      wall_radius=S.MONSTER_RADIUS)
        )
        locker_fine_approach = (
            self.locker_target is not None and self.checking_timer <= 0.0
            and not self.path and near_locker_target is not None and near_locker_target > 0.06
        )
        prev_x, prev_y = self.x, self.y
        if self.breaking_door is not None:
            self.break_timer += dt
            if self.break_timer >= S.DOOR_BREAK_SECONDS:
                self.breaking_door.break_open(from_xy=(self.x, self.y))
                self.breaking_door = None
                self.break_timer = 0.0
                if self.target_cell:
                    self._replan(maze, self.target_cell)
            moved = False
        elif self._turn_probe_timer > 0.0:
            self._turn_probe_timer -= dt
            probe_facing = (self.facing + self._turn_probe_dir * S.MONSTER_TURN_PROBE_RATE * dt) % (2 * math.pi)
            px = self.x + math.cos(probe_facing) * 0.8
            py = self.y + math.sin(probe_facing) * 0.8
            self.facing = probe_facing
            if self._has_clear_path(maze, self.x, self.y, px, py, local_props, wall_radius=S.MONSTER_RADIUS):
                self._turn_probe_timer = 0.0
                if self.target_cell:
                    self._replan(maze, self.target_cell)
            moved = False
        elif close_direct_chase:
            self._walk_toward(maze, dt, speed, player.x, player.y, blocked_props=local_props)
            self.path = []
            moved = True
        elif locker_fine_approach:
            self._step_toward(maze, dt, speed, locker_stand[0], locker_stand[1],
                               face=(self.locker_target.x, self.locker_target.y), blocked_props=local_props)
            moved = True
        elif no_path_fallback:
            self._walk_toward(maze, dt, speed, self.target_cell[0] + 0.5, self.target_cell[1] + 0.5,
                               blocked_props=local_props)
            moved = True
        else:
            moved = self._advance(dt, speed, maze, local_props)
        self.x, self.y = _push_out_of_props(self.x, self.y, local_props)
        if self.breaking_door is not None:
            self.x, self.y = _push_out_of_props(self.x, self.y, [self.breaking_door])
        self._hold_door_side(self.doors)
        self._door_sides(self.doors)
        if moved:
            self.walk_phase += dt * speed * 5.5
        target_amp = 1.0 if moved else 0.0
        self.walk_amp += (target_amp - self.walk_amp) * min(1.0, dt * 8.0)

        touching_prop = None
        if moved and self.breaking_door is None and not self._pivoting:
            actually_moved = math.hypot(self.x - prev_x, self.y - prev_y)
            if actually_moved < speed * dt * 0.2:
                self._stuck_time += dt
                touching_prop = next((p for p in blocked_props
                                       if _circle_hits_prop(self.x, self.y, S.MONSTER_RADIUS + 0.15, p)), None)
                self._prop_stuck_time = self._prop_stuck_time + dt if touching_prop else 0.0
                if self._stuck_time > 0.35:
                    self._stuck_time = 0.0
                    if self.target_cell:
                        self._temp_blocked_cells[self.cell] = S.MONSTER_TEMP_BLOCK_DURATION
                        self._replan(maze, self.target_cell, extra_blocked=set(self._temp_blocked_cells))
            else:
                self._stuck_time = 0.0
                self._prop_stuck_time = 0.0
        else:
            self._stuck_time = 0.0
            self._prop_stuck_time = 0.0

        if self.breaking_door is None and math.hypot(self.x - prev_x, self.y - prev_y) < speed * dt * 0.2:
            self._catch_stuck_time += dt
        else:
            self._catch_stuck_time = 0.0

        if self.breaking_door is None and math.hypot(self.x - prev_x, self.y - prev_y) < speed * dt * 0.2:
            self._nav_stuck_time += dt
        else:
            self._nav_stuck_time = 0.0
        if self._nav_stuck_time > S.MONSTER_NAV_STUCK_TIMEOUT:
            self._nav_stuck_time = 0.0
            self._abandon_target_and_patrol()

        if touching_prop is not None and self._prop_stuck_time > S.MONSTER_PROP_STUCK_TRIGGER:
            self._prop_stuck_time = 0.0
            if touching_prop.kind == "door" and not touching_prop.is_open:
                if not touching_prop.is_broken and self.breaking_door is None:
                    if touching_prop.is_latched or touching_prop._pending_latch:
                        self.breaking_door = touching_prop
                        self.break_timer = 0.0
                    else:
                        touching_prop.toggle()
                        self.just_opened_door = touching_prop
                        self.trailing_door = touching_prop
            elif dist >= S.MONSTER_STUCK_NEAR_PLAYER_DIST:
                self._ignored_props[id(touching_prop)] = S.MONSTER_PROP_IGNORE_DURATION
            else:
                self._turn_probe_timer = S.MONSTER_TURN_PROBE_DURATION
                self._turn_probe_dir = self.rng.choice((-1.0, 1.0))

        if (self.locker_target is not None and self.checking_timer <= 0.0 and self.state != Monster.STALK
                and not self.path and near_locker_target is not None and near_locker_target <= 0.06):
            self.facing = math.atan2(self.locker_target.y - self.y, self.locker_target.x - self.x)
            if self.stalk_origin:
                self.state = Monster.STALK
                self.stalk_phase = "wait"
                self.stalk_timer = self.rng.uniform(S.MONSTER_STALK_WAIT_MIN, S.MONSTER_STALK_WAIT_MAX)
            else:
                self.checking_timer = S.MONSTER_LOCKER_CHECK_SECONDS
                self.checking_timer_total = S.MONSTER_LOCKER_CHECK_SECONDS

        catch_reach = (dist < S.MONSTER_CATCH_RADIUS
                        and not _closed_door_between(self.doors, self.x, self.y, player.x, player.y)
                        and not maze.blocks_reach(self.x, self.y, player.x, player.y))
        stuck_catch = self._catch_stuck_time > S.MONSTER_STUCK_CATCH_TIME and dist < S.MONSTER_STUCK_CATCH_RADIUS
        if (not grace and not blind and not player.is_hiding
                and self.state == Monster.HUNT and (catch_reach or stuck_catch)):
            self.caught_player = True

        arrived = (self.state in (Monster.HUNT, Monster.INVESTIGATE) and not self.path
                   and self.locker_target is None and self.cell == self.target_cell
                   and not can_see and not spotted_by_beam)
        if arrived:
            self._mark_searched(maze, self.cell)
            searching = (self._search_hops_left > 0 and self.target_cell is not None
                         and self._search_t < S.MONSTER_SEARCH_SECONDS)
            if searching:
                self._search_hops_left -= 1
                nxt = self._pick_search_cell(maze)
                if nxt is None:
                    tcx, tcy = self.target_cell
                    nearby = [c for c in self._nearby_open_cell(maze, tcx, tcy)
                              if c not in self._searched]
                    nxt = nearby[0] if nearby else None
                if nxt is not None:
                    self.target_cell = nxt
                    self._replan(maze, self.target_cell)
                elif self.state == Monster.HUNT:
                    self.state = Monster.INVESTIGATE
                    self._search_hops_left = S.MONSTER_SEARCH_HOPS
                else:
                    self._enter_patrol()
            elif self.state == Monster.HUNT:
                self.state = Monster.INVESTIGATE
                self._search_hops_left = S.MONSTER_SEARCH_HOPS
            else:
                self._enter_patrol()

    def _advance(self, dt, speed, maze, blocked_props=()):
        if not self.path:
            return False
        idx = 0
        for i, (cx, cy) in enumerate(self.path[:S.MONSTER_PATH_LOOKAHEAD]):
            tx, ty = cx + 0.5, cy + 0.5
            if math.hypot(tx - self.x, ty - self.y) > S.MONSTER_PATH_LOOKAHEAD_DIST:
                break
            if self._has_clear_path(maze, self.x, self.y, tx, ty, blocked_props, wall_radius=S.MONSTER_RADIUS):
                idx = i
            else:
                break
        tx, ty = self.path[idx][0] + 0.5, self.path[idx][1] + 0.5
        if self._walk_toward(maze, dt, speed, tx, ty, blocked_props=blocked_props):
            del self.path[: idx + 1]
        return True
