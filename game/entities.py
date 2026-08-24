import math
import random

from game import settings as S
from game.props import line_blocked_by_cover, _circle_hits_prop


class Player:
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.angle = 0.0
        self.pitch = 0.0
        self.flashlight_on = False
        self.battery = 100.0
        self.stamina = S.STAMINA_MAX
        self.stamina_locked = False
        self.sanity = S.SANITY_MAX
        self.carried = 0
        self.has_cutters = False
        self.is_hiding = False
        self.hidden_in = None
        self.flashlight_before_peek = False
        self.is_sprinting = False
        self.is_crouching = False
        self.crouch = 0.0
        self.alive = True
        self._step_accum = 0.0
        self.noise_radius = 0.0
        self.moved_this_frame = False
        self.bumped_wall = False
        self.bob_phase = 0.0
        self.move_ease = 0.0
        self.locker_use_count = 0
        self.last_move_dx = 0.0
        self.last_move_dy = 0.0
        self.light_level = 0.0
        self.is_lit = False

    @property
    def cell(self):
        return int(self.x), int(self.y)

    def _collides(self, maze, props, x, y, r):
        if maze.circle_hits_wall(x, y, r):
            return True
        for p in props:
            if not p.solid:
                continue
            if getattr(p, "ignore_player", False):
                continue
            px, py = p.collide_x, p.collide_y
            if (x - px) ** 2 + (y - py) ** 2 > (r + p.collide_hw + p.collide_hd) ** 2:
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
        r = S.PLAYER_RADIUS
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
                         crouch_held=False, infinite_stamina=False):
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
            else:
                self.stamina = min(S.STAMINA_MAX, self.stamina + S.STAMINA_REGEN_STANDING * dt)
                if self.stamina_locked and self.stamina >= S.STAMINA_EXHAUST_LOCKOUT:
                    self.stamina_locked = False
            return

        fwd_x, fwd_y = math.cos(self.angle), math.sin(self.angle)
        strafe_x, strafe_y = -fwd_y, fwd_x

        mv_f = (1 if keys_down.get("forward") else 0) - (1 if keys_down.get("back") else 0)
        mv_s = (1 if keys_down.get("right") else 0) - (1 if keys_down.get("left") else 0)
        moving = bool(mv_f or mv_s)

        if infinite_stamina:
            self.stamina = S.STAMINA_MAX
            self.stamina_locked = False

        want_sprint = bool(keys_down.get("sprint")) and mv_f > 0 and moving and not self.is_crouching
        self.is_sprinting = want_sprint and self.stamina > 0 and not self.stamina_locked
        if self.is_crouching:
            speed = S.CROUCH_SPEED
        else:
            speed = S.SPRINT_SPEED if self.is_sprinting else S.WALK_SPEED

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
                self.noise_radius = 0.0
            else:
                self.noise_radius = S.MONSTER_HEARING_RANGE_SPRINT if self.is_sprinting else S.MONSTER_HEARING_RANGE * 0.55
        else:
            self.noise_radius = 0.0

        if self.bumped_wall:
            self.noise_radius = max(self.noise_radius, S.MONSTER_HEARING_RANGE * 0.6)

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
        else:
            regen = S.STAMINA_REGEN_STANDING if not moving else S.STAMINA_REGEN
            self.stamina = min(S.STAMINA_MAX, self.stamina + regen * dt)
            if self.stamina_locked and self.stamina >= S.STAMINA_EXHAUST_LOCKOUT:
                self.stamina_locked = False

    def consume_step(self, step_len=0.55):
        if self._step_accum >= step_len:
            self._step_accum = 0.0
            return True
        return False

    def toggle_flashlight(self):
        if not self.flashlight_on and self.battery <= 0.5:
            return False
        self.flashlight_on = not self.flashlight_on
        return True

    def update_flashlight(self, dt):
        if self.flashlight_on:
            self.battery -= S.FLASHLIGHT_DRAIN * dt
            if self.battery <= 0:
                self.battery = 0
                self.flashlight_on = False

    def add_battery(self, amount=S.BATTERY_PICKUP_AMOUNT):
        self.battery = min(100.0, self.battery + amount)

    def apply_sanity(self, delta):
        self.sanity = max(0.0, min(S.SANITY_MAX, self.sanity + delta))


def _push_out_of_props(x, y, blocked_props, radius=0.16):
    for p in blocked_props:
        fx, fy = math.cos(p.collide_facing), math.sin(p.collide_facing)
        rx, ry = -fy, fx
        dx, dy = x - p.collide_x, y - p.collide_y
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
    PATROL, INVESTIGATE, HUNT, STALK = "patrol", "investigate", "hunt", "stalk"

    def __init__(self, x, y, maze, rng=None, speed_mult=1.0, vision_mult=1.0, blocked_cells=None, lockers=None,
                 doors=None, blocked_prop_candidates=None, dead_end_lockers=None):
        self.x = x
        self.y = y
        self.state = Monster.PATROL
        self.rng = rng or random.Random()
        self.speed_mult = speed_mult
        self.vision_mult = vision_mult
        self.path = []
        self.target_cell = None
        self.replan_timer = 0.0
        self.lose_interest_timer = 0.0
        self.alert_level = 0.0
        self._patrol_wait = 0.0
        self.locker_target = None
        self.locker_target_certain = False
        self.caught_player = False
        self.walk_phase = 0.0
        self.walk_amp = 0.0
        self.facing = 0.0
        self.blocked_cells = blocked_cells or set()
        self.lockers = lockers or []
        self.doors = doors or []
        self.blocked_prop_candidates = blocked_prop_candidates or []
        self.breaking_door = None
        self.break_timer = 0.0
        self._search_hops_left = 0
        self.just_noticed = False
        self._prev_hiding = False
        self._had_visual_last_frame = False
        self._stuck_time = 0.0
        self._catch_stuck_time = 0.0
        self._nav_stuck_time = 0.0
        self.checking_timer = 0.0
        self.checking_timer_total = S.MONSTER_LOCKER_CHECK_SECONDS
        self._sight_memory_t = 0.0
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

    @property
    def cell(self):
        return int(self.x), int(self.y)

    def _pick_patrol_target(self, maze):
        floors = [c for c in maze.floor_cells() if c not in self.blocked_cells]
        return self.rng.choice(floors) if floors else self.rng.choice(maze.floor_cells())

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
        self.state = Monster.PATROL
        self.target_cell = None
        self.path = []
        self._patrol_wait = self.rng.uniform(0.5, 1.5)

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

    def _locker_in_notice_range(self, lk):
        return math.hypot(lk.x - self.x, lk.y - self.y) < 3.0

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
        for pid in list(self._ignored_props):
            self._ignored_props[pid] -= dt
            if self._ignored_props[pid] <= 0:
                del self._ignored_props[pid]
        for cell in list(self._temp_blocked_cells):
            self._temp_blocked_cells[cell] -= dt
            if self._temp_blocked_cells[cell] <= 0:
                del self._temp_blocked_cells[cell]

    def _active_blocked_props(self):
        return [p for p in self.blocked_prop_candidates
                if p.solid and id(p) not in self._ignored_props]

    def _move_toward(self, maze, nx, ny, blocked_props=()):
        r = S.MONSTER_RADIUS

        def blocked(x, y):
            if maze.circle_hits_wall(x, y, r):
                return True
            for p in blocked_props:
                if id(p) in self._ignored_props:
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

    def _turn_toward(self, target_facing, dt, rate=9.0):
        diff = (target_facing - self.facing + math.pi) % (2 * math.pi) - math.pi
        max_turn = rate * dt
        self.facing = (self.facing + max(-max_turn, min(max_turn, diff))) % (2 * math.pi)

    def _step_toward(self, maze, dt, speed, tx, ty, face=None, blocked_props=()):
        dx, dy = tx - self.x, ty - self.y
        dist = math.hypot(dx, dy)
        if dist > 1e-6:
            fx, fy = (face[0] - self.x, face[1] - self.y) if face is not None else (dx, dy)
            self._turn_toward(math.atan2(fy, fx), dt)
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
                t = max(0.0, min(1.0, ((p.x - x0) * dx + (p.y - y0) * dy) * inv_len2))
                cx, cy = x0 + dx * t, y0 + dy * t
                if (p.x - cx) ** 2 + (p.y - cy) ** 2 < (p.hw + p.hd + pad) ** 2:
                    near.append(p)
            blocked_props = near
        steps = max(1, int(dist / step))
        for i in range(1, steps + 1):
            t = i / steps
            sx, sy = x0 + dx * t, y0 + dy * t
            if maze.circle_hits_wall(sx, sy, wall_radius) if wall_radius else maze.is_wall(sx, sy):
                return False
            if (int(sx), int(sy)) in self.blocked_cells:
                return False
            for p in blocked_props:
                if _circle_hits_prop(sx, sy, S.MONSTER_RADIUS, p):
                    return False
        return True

    def update(self, dt, maze, player, dread, props, grace=False):
        self._tick_temp_state(dt)
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

        if grace:
            if self.state != Monster.PATROL:
                self.state = Monster.PATROL
                self.target_cell = None
                self.path = []
            can_see = False
            hearing_hit = False
            spotted_by_beam = False
            beam_glow_cell = None
            loud_and_close = False
            proximity_alert = False
        else:
            vision = (S.MONSTER_VISION_RANGE_LIT if player.is_lit else S.MONSTER_VISION_RANGE)
            vision *= self.vision_mult * (1.0 + dread * 0.35)
            hearing = player.noise_radius * (1.0 + dread * 0.2)
            near_enough = dist < vision * 1.6
            has_los = near_enough and (
                maze.has_line_of_sight(self.x, self.y, player.x, player.y)
                and not line_blocked_by_cover(self.doors, self.x, self.y, player.x, player.y, min_height=0.1))
            covered = (near_enough and player.is_crouching
                       and line_blocked_by_cover(props, self.x, self.y, player.x, player.y))
            ang_to_player = math.atan2(player.y - self.y, player.x - self.x)
            facing_rel = (ang_to_player - self.facing + math.pi) % (2 * math.pi) - math.pi
            in_fov = abs(facing_rel) < 1.31
            can_see = not player.is_hiding and dist < vision and has_los and not covered and in_fov

            proximity_alert = (not player.is_hiding and dist < S.MONSTER_PROXIMITY_RANGE and has_los)

            spotted_by_beam = False
            if not can_see and not player.is_hiding and player.flashlight_on and has_los and not covered and dist < vision * 1.6:
                ang_to_monster = math.atan2(self.y - player.y, self.x - player.x)
                rel = (ang_to_monster - player.angle + math.pi) % (2 * math.pi) - math.pi
                spotted_by_beam = abs(rel) < 0.5

            beam_glow_cell = None
            if (not can_see and not spotted_by_beam and not player.is_hiding
                    and player.flashlight_on and self.state != Monster.HUNT):
                bx, by, step, max_range = player.x, player.y, 0.25, 6.0
                fx, fy = math.cos(player.angle), math.sin(player.angle)
                hx, hy = bx, by
                for i in range(1, int(max_range / step) + 1):
                    tx, ty = bx + fx * step * i, by + fy * step * i
                    if maze.is_wall(tx, ty):
                        break
                    hx, hy = tx, ty
                if math.hypot(hx - self.x, hy - self.y) < 2.6 and maze.has_line_of_sight(self.x, self.y, hx, hy):
                    beam_glow_cell = (int(hx), int(hy))

            if dist < hearing and not player.is_hiding:
                hearing_has_los = maze.has_line_of_sight(self.x, self.y, player.x, player.y)
                effective_hearing = hearing if hearing_has_los else hearing * S.MONSTER_HEARING_WALL_MUFFLE
                hearing_hit = dist < effective_hearing and self.state in (Monster.PATROL, Monster.INVESTIGATE)
            else:
                hearing_hit = False
            loud_and_close = hearing_hit and dist < hearing * 0.4

        just_hid = player.is_hiding and not self._prev_hiding
        witnessed_hide = just_hid and self._had_visual_last_frame
        self._prev_hiding = player.is_hiding
        self._had_visual_last_frame = can_see or spotted_by_beam
        if (not grace) and witnessed_hide and player.hidden_in is not None and self.state in (Monster.HUNT, Monster.INVESTIGATE):
            lk = player.hidden_in
            if self._locker_in_notice_range(lk):
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

        if (not grace and player.is_hiding and player.flashlight_on and player.hidden_in is not None
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
        if (can_see or spotted_by_beam or loud_and_close or proximity_alert) and self.state != Monster.HUNT:
            self.just_noticed = True

        if self.state != Monster.STALK or can_see or spotted_by_beam:
            if can_see or spotted_by_beam:
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
            elif loud_and_close or proximity_alert:
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
            elif self.state == Monster.HUNT and self._sight_memory_t > 0:
                self._sight_memory_t -= dt
                self.target_cell = player.cell
            elif self.state == Monster.HUNT:
                self.lose_interest_timer -= dt
                if self.lose_interest_timer <= 0:
                    self.state = Monster.INVESTIGATE
                    self._search_hops_left = 3
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
                    self._search_hops_left = 3
            elif beam_glow_cell is not None:
                target = beam_glow_cell
                if self.rng.random() < S.MONSTER_INTERCEPT_CHANCE:
                    target = self._predict_target_cell(maze, player, beam_glow_cell)
                if self.pending_reaction is None and self.rng.random() < S.MONSTER_REACTION_DELAY_CHANCE:
                    self.pending_reaction = {
                        "target_cell": target, "hops": 2,
                        "timer": self.rng.uniform(S.MONSTER_REACTION_DELAY_MIN, S.MONSTER_REACTION_DELAY_MAX),
                    }
                else:
                    self.state = Monster.INVESTIGATE
                    self.target_cell = target
                    self._search_hops_left = 2

        if (not grace and self.state == Monster.INVESTIGATE and self.checking_timer <= 0.0
                and self.locker_target is None and self._search_hops_left <= 1):
            overuse_frac = min(1.0, player.locker_use_count / S.STALK_RELEASE_OVERUSE_SATURATION)
            chance_per_sec = (S.INVESTIGATE_LOCKER_EVENT_BASE_PROB
                               + S.INVESTIGATE_LOCKER_EVENT_OVERUSE_BONUS * overuse_frac)
            if self.rng.random() < chance_per_sec * dt:
                self._roll_investigate_locker_event(maze, player)

        if not grace and self.state == Monster.PATROL and self.checking_timer <= 0.0 and self.locker_target is None:
            overuse_frac = min(1.0, player.locker_use_count / S.STALK_RELEASE_OVERUSE_SATURATION)
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

        if self.state == Monster.PATROL:
            self._patrol_wait -= dt
            if not self.path and self._patrol_wait <= 0:
                self.target_cell = self._pick_patrol_target(maze)
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
        if self.breaking_door is None and self.state in (Monster.HUNT, Monster.INVESTIGATE):
            for d in self.doors:
                if d.is_open or math.hypot(d.x - self.x, d.y - self.y) >= S.DOOR_BREAK_TRIGGER_DIST:
                    continue
                dcell = (int(d.x), int(d.y))
                blocking = self.state == Monster.HUNT or dcell in self.path[:2]
                if blocking:
                    self.breaking_door = d
                    self.break_timer = 0.0
                    break

        base = S.MONSTER_HUNT_SPEED if self.state == Monster.HUNT else S.MONSTER_BASE_SPEED
        speed = base * self.speed_mult * (1.0 + dread * 0.25)
        speed = min(speed, S.SPRINT_SPEED * S.MONSTER_HUNT_SPEED_CAP_RATIO)
        blocked_props = self._active_blocked_props()
        close_direct_chase = (self.state == Monster.HUNT and not grace and dist < 1.3
                               and self._has_clear_path(maze, self.x, self.y, player.x, player.y, blocked_props))
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
                self.breaking_door.break_open()
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
            if self._has_clear_path(maze, self.x, self.y, px, py, blocked_props, wall_radius=S.MONSTER_RADIUS):
                self._turn_probe_timer = 0.0
                if self.target_cell:
                    self._replan(maze, self.target_cell)
            moved = False
        elif close_direct_chase:
            self._step_toward(maze, dt, speed, player.x, player.y, blocked_props=blocked_props)
            self.path = []
            moved = True
        elif locker_fine_approach:
            self._step_toward(maze, dt, speed, locker_stand[0], locker_stand[1],
                               face=(self.locker_target.x, self.locker_target.y), blocked_props=blocked_props)
            moved = True
        elif no_path_fallback:
            self._step_toward(maze, dt, speed, self.target_cell[0] + 0.5, self.target_cell[1] + 0.5,
                               blocked_props=blocked_props)
            moved = True
        else:
            moved = self._advance(dt, speed, maze, blocked_props)
        self.x, self.y = _push_out_of_props(self.x, self.y, blocked_props)
        if self.breaking_door is not None:
            self.x, self.y = _push_out_of_props(self.x, self.y, [self.breaking_door])
        if moved:
            self.walk_phase += dt * speed * 5.5
        target_amp = 1.0 if moved else 0.0
        self.walk_amp += (target_amp - self.walk_amp) * min(1.0, dt * 8.0)

        touching_prop = None
        if moved and self.breaking_door is None:
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
            if dist >= S.MONSTER_STUCK_NEAR_PLAYER_DIST:
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

        if not grace and not player.is_hiding and self.state == Monster.HUNT and (
                dist < S.MONSTER_CATCH_RADIUS
                or (self._catch_stuck_time > S.MONSTER_STUCK_CATCH_TIME and dist < S.MONSTER_STUCK_CATCH_RADIUS)):
            self.caught_player = True

        arrived = (self.state in (Monster.HUNT, Monster.INVESTIGATE) and not self.path
                   and self.locker_target is None and self.cell == self.target_cell
                   and not can_see and not spotted_by_beam)
        if arrived:
            searching = self._search_hops_left > 0 and self.target_cell is not None
            if searching:
                self._search_hops_left -= 1
                tcx, tcy = self.target_cell
                nearby = self._nearby_open_cell(maze, tcx, tcy)
                if nearby:
                    self.target_cell = self.rng.choice(nearby)
                    self._replan(maze, self.target_cell)
                elif self.state == Monster.HUNT:
                    self.state = Monster.INVESTIGATE
                    self._search_hops_left = 3
                else:
                    self._enter_patrol()
            elif self.state == Monster.HUNT:
                self.state = Monster.INVESTIGATE
                self._search_hops_left = 3
            else:
                self._enter_patrol()

    def _advance(self, dt, speed, maze, blocked_props=()):
        if not self.path:
            return False
        idx = 0
        for i, (cx, cy) in enumerate(self.path):
            tx, ty = cx + 0.5, cy + 0.5
            if self._has_clear_path(maze, self.x, self.y, tx, ty, blocked_props, wall_radius=S.MONSTER_RADIUS):
                idx = i
            else:
                break
        tx, ty = self.path[idx][0] + 0.5, self.path[idx][1] + 0.5
        if self._step_toward(maze, dt, speed, tx, ty, blocked_props=blocked_props):
            del self.path[: idx + 1]
        return True
