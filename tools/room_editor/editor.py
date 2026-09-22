import io
import math
import os
import random
import time

import moderngl
import pygame

from game import settings as S
from game import ui
from game import gl_math as gm
from game import i18n
from game.maze import Maze
from game.entities import Player, Monster
from game.props import (
    Door, Prop, PROP_DEFS, populate_level, populate_yard, link_adjacent_pipes,
)
from tools.room_editor.furniture_model import entry as furn_entry
from game.renderer3d import Renderer3D, EYE_HEIGHT, FOV_DEGREES
from game.room_templates import door_facing as _border_door_facing

from tools.room_editor import room_model as rm
from tools.room_editor import upload as up
from tools.room_editor import zone_model as zm
from tools.room_editor.camera import FreeCamera
from tools.room_editor.grid_view import GridView
from tools.room_editor.panel import Panel
from tools.room_editor import panel as panel_mod
from tools.room_editor.raycast import (ray_floor_cell, ray_floor_point, screen_ray,
                                       world_to_screen, ray_vertical_plane_z)


def _apply_saved_language():
    import json
    from game.app import SETTINGS_PATH, _LEGACY_SETTINGS_PATH
    path = SETTINGS_PATH if SETTINGS_PATH.exists() else _LEGACY_SETTINGS_PATH
    try:
        with open(path, encoding="utf-8") as f:
            lang = json.load(f).get("language")
        if lang:
            i18n.set_language(lang)
    except (OSError, ValueError):
        pass


_apply_saved_language()

PANEL_W = 380
PAD = 3
MONSTER_PARK = (-2000.0, -2000.0)


def _seg_dist(px, py, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L <= 1e-9 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - (ax + dx * t), py - (ay + dy * t))


class Editor:
    def __init__(self, mode="dev"):
        self.mode = mode
        pygame.init()
        pygame.display.set_caption(f"{S.TITLE} - {i18n.t('editor.ui.window_title_suffix')}")
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 3)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_CORE)
        pygame.display.gl_set_attribute(pygame.GL_DEPTH_SIZE, 24)
        pygame.display.gl_set_attribute(pygame.GL_DOUBLEBUFFER, 1)
        info = pygame.display.Info()
        self.window_w, self.window_h = info.current_w, info.current_h
        self.screen = pygame.display.set_mode(
            (self.window_w, self.window_h), pygame.OPENGL | pygame.DOUBLEBUF | pygame.FULLSCREEN
        )
        self.primary_w = self.window_w - PANEL_W
        self.ctx = moderngl.create_context()
        self.renderer = Renderer3D(self.ctx)

        self.panel = Panel(width=PANEL_W)
        self.grid = GridView(pygame.Rect(0, 0, self.primary_w, self.window_h))
        self.view_mode = "camera"

        self.editor_mode = "room"
        self.model = rm.RoomModel(id=rm.fresh_id("ward", "upper"), kind="ward", floor="upper")
        self.camera = FreeCamera()
        self._center_camera_on_room()

        self._fake_maze = Maze(w=S.DEBUG_W, h=S.DEBUG_H, layout="debug")
        self._fake_player = Player(0.0, 0.0)
        self._fake_player.flashlight_on = False
        self._fake_player.battery = 100.0
        self._fake_player.sanity = S.SANITY_MAX
        self._fake_monster = Monster(
            MONSTER_PARK[0], MONSTER_PARK[1], self._fake_maze, rng=random.Random(0),
            speed_mult=0.0, vision_mult=0.0, blocked_cells=set(), lockers=[], doors=[],
        )
        self._rebuild_level()

        self.looking = False
        self.painting = False
        self.selected_furn = None
        self.gizmo_handle = None
        self.gizmo_grab = (0.0, 0.0)
        self.gizmo_turn_from = (0.0, 0.0)
        self.selected_entry = None
        self.selection = []
        self._hover_furn_index = None
        self.gizmo_hover = None
        self._drag_riders = None
        self.repeat_action = None
        self.repeat_at = 0.0
        self.paint_value = S.WALL_CONCRETE
        self.natural_color = False
        self.testing_floor = None
        self._test_maze = None
        self._test_props = None
        self._test_monster = None
        self._test_spawn = (0.0, 0.0)
        self.dirty = False
        self.hover_cell = None
        self.prompting_nickname = False
        self._nickname_buffer = ""
        self.uploading = False
        self.message = ""
        self.message_is_error = False
        self.message_time = 0.0
        self.clock = pygame.time.Clock()
        self.running = True
        self._t0 = time.time()
        pygame.font.init()
        self._msg_font = ui.font(20)
        self._help_title_font = ui.font(26)
        self._help_font = ui.font(17)
        self.show_help = False
        self._scan_down = set()

    def _center_camera_on_room(self):
        self.camera.x = PAD + 1.5
        self.camera.y = PAD + 1.5
        self.camera.z = 0.9
        self.camera.yaw = math.pi / 4
        self.camera.pitch = -0.12

    def _set_message(self, text, is_error=False):
        self.message = text
        self.message_is_error = is_error
        self.message_time = time.time()

    def _rebuild_level(self):
        gw, gh, grid, pad = self.model.to_maze_grid(pad=PAD)
        self._fake_maze.regrid(gw, gh, grid)
        theme = self.model.floor if self.editor_mode == "room" else "yard"
        self._fake_maze.layout = "yard" if self.editor_mode == "zone" else "debug"
        self._fake_maze.zones = ([{"rect": (pad, pad, pad + self.model.w, pad + self.model.h),
                                   "kind": self.model.kind}]
                                 if self.editor_mode == "zone" else [])
        self.renderer.build_level(self._fake_maze, theme=theme)
        self.dirty = False

    def _mark_dirty(self):
        self.dirty = True

    def _current_props(self):
        out = []
        for _i, kind, x, y, z0, facing in self._furniture_props():
            p = Prop(kind, x, y, facing=facing)
            p.z0 = z0
            out.append(p)
        for info in self.model.doors.values():
            if info["kind"] == "passage":
                continue
            lx, ly = info["cell"]
            side = self.model.side_of_border_cell(lx, ly)
            facing = _border_door_facing(side) if side else 0.0
            d = Door(lx + PAD + 0.5, ly + PAD + 0.5, facing)
            if info["kind"] == "broken":
                d.break_open()
            out.append(d)
        for lx, ly, facing, kind in self.model.interior_doors:
            d = Door(lx + PAD + 0.5, ly + PAD + 0.5, facing)
            if kind == "broken":
                d.break_open()
            out.append(d)
        link_adjacent_pipes(out)
        return out

    def run(self):
        while self.running:
            dt = self.clock.tick(60) / 1000.0
            self._handle_events(dt)
            self._tick_repeat()
            self._update_camera(dt)
            if self.testing_floor is not None:
                self.hover_cell = None
            elif not self.looking:
                self.hover_cell = self._resolve_cell(*pygame.mouse.get_pos())
                mx, my = pygame.mouse.get_pos()
                if self.gizmo_handle is None:
                    self.gizmo_hover = self._handle_under_cursor(mx, my)
                    self._hover_furn_index = (
                        None if self.panel.gizmo_mode == "cursor" or self.gizmo_hover
                        else self._pick_furniture(mx, my))
            if self.dirty and self.testing_floor is None:
                self._rebuild_level()
            self._draw()
        pygame.quit()

    def _toggle_view(self):
        self.view_mode = "camera" if self.view_mode == "grid" else "grid"
        self.painting = False

    def _resolve_point(self, mx, my):
        if mx >= self.primary_w:
            return None
        if self.view_mode == "grid":
            pt = self.grid.point_at(mx, my, self.model)
            return None if pt is None else (pt[0] + PAD, pt[1] + PAD)
        aspect = self.renderer.low_w / self.renderer.low_h
        pt = ray_floor_point(
            mx, my, self.primary_w, self.window_h, self.camera.eye, self.camera.yaw,
            self.camera.pitch, math.radians(FOV_DEGREES), aspect,
        )
        return pt

    def _resolve_cell(self, mx, my):
        if mx >= self.primary_w:
            return None
        if self.view_mode == "grid":
            return self.grid.cell_at(mx, my, self.model)
        aspect = self.renderer.low_w / self.renderer.low_h
        cell = ray_floor_cell(
            mx, my, self.primary_w, self.window_h, self.camera.eye, self.camera.yaw, self.camera.pitch,
            math.radians(FOV_DEGREES), aspect,
        )
        if cell is None:
            return None
        lx, ly = cell[0] - PAD, cell[1] - PAD
        return (lx, ly) if self.model.in_bounds(lx, ly) else None

    def _handle_events(self, dt):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.TEXTINPUT and self.prompting_nickname:
                ch = event.text
                if ch.isprintable():
                    self._nickname_buffer = (self._nickname_buffer + ch)[:32]
            elif event.type == pygame.KEYDOWN:
                self._scan_down.add(event.scancode)
                self._handle_keydown(event)
            elif event.type == pygame.KEYUP:
                self._scan_down.discard(event.scancode)
            elif event.type == pygame.MOUSEBUTTONDOWN:
                self._handle_mousedown(event)
            elif event.type == pygame.MOUSEBUTTONUP:
                if event.button == 3:
                    self.looking = False
                    pygame.mouse.set_visible(True)
                    pygame.event.set_grab(False)
                elif event.button == 1:
                    self.repeat_action = None
                    self._end_drag()
                    self.painting = False
            elif event.type == pygame.MOUSEWHEEL:
                mx, my = pygame.mouse.get_pos()
                if self.show_help:
                    self.help_scroll = max(0, getattr(self, "help_scroll", 0) - event.y * 44)
                elif mx >= self.primary_w:
                    self.panel.scroll(event.y, self.window_h)
                elif self.hover_cell is not None:
                    if pygame.key.get_mods() & pygame.KMOD_CTRL:
                        self._raise_hovered(event.y)
                    else:
                        step = self._rotate_step()
                        self._rotate_hovered(step if event.y > 0 else -step)
            elif event.type == pygame.MOUSEMOTION:
                if self.gizmo_handle is not None:
                    self._gizmo_drag(*event.pos)
                elif self.painting and self.panel.current_tool in ("wall", "window", "bars", "floor"):
                    self._paint_at(*event.pos)

    def _hovered_item(self):
        idx = self._hover_furn_index
        if idx is not None and idx < len(self.model.furniture):
            return self.model.furniture[idx]
        item = self._selected_item()
        if item is not None:
            return item
        if self.hover_cell is not None:
            return self.model.furniture_at(*self.hover_cell)
        return None

    HEIGHT_STEP = 0.05

    def _raise_hovered(self, dir_):
        item = self._hovered_item()
        if item is None:
            return
        if not self.model.hangs(item[0]):
            self._set_message(i18n.t("editor.msg.height_fixed"), True)
            return
        self.model.set_hanging_height(
            item, float(item[3]) + self.HEIGHT_STEP * (1 if dir_ > 0 else -1))
        self._set_message(i18n.t("editor.msg.height", z=("%.2f" % float(item[3]))))
        self._mark_dirty()

    def _rotate_hovered(self, step):
        item = self._hovered_item()
        if item is None:
            return
        if not self.model.turns(item[0]):
            self._set_message(i18n.t("editor.msg.no_turn_wall"), True)
            return
        if self._alt_held():
            want = round((float(item[4]) + step) / (math.pi / 2)) * (math.pi / 2)
            step = want - float(item[4])
        self.model.rotate_entry(item, step)
        self._mark_dirty()

    ROTATE_COARSE = math.pi / 2
    ROTATE_FINE = math.radians(15.0)

    def _rotate_step(self):
        mods = pygame.key.get_mods()
        step = self.ROTATE_FINE if mods & pygame.KMOD_SHIFT else self.ROTATE_COARSE
        return -step if mods & pygame.KMOD_CTRL else step

    def _handle_keydown(self, event):
        if self.prompting_nickname:
            if event.key == pygame.K_RETURN:
                nickname = self._nickname_buffer.strip()
                if nickname:
                    self.prompting_nickname = False
                    pygame.key.stop_text_input()
                    up.save_nickname(nickname)
                    self._do_upload_save(nickname)
                else:
                    self._set_message(i18n.t("editor.msg.nickname_empty"), True)
            elif event.key == pygame.K_ESCAPE:
                self.prompting_nickname = False
                pygame.key.stop_text_input()
            elif event.key == pygame.K_BACKSPACE:
                self._nickname_buffer = self._nickname_buffer[:-1]
            return
        if event.key == pygame.K_F1:
            self.show_help = not self.show_help
            return
        if self.show_help:
            if event.key == pygame.K_ESCAPE:
                self.show_help = False
            return
        if event.key == pygame.K_TAB:
            if self.testing_floor is None:
                self._toggle_view()
            return
        if event.key == pygame.K_ESCAPE:
            if self.testing_floor is not None:
                self._exit_test_floor()
            elif self.panel.gizmo_mode == "magnet" and self.selection:
                self.selection = []
                self._set_message(i18n.t("editor.msg.magnet_cleared"))
            elif self.selected_entry is not None and self.panel.gizmo_mode != "cursor":
                self.selected_entry = None
                self.selection = []
            else:
                self.running = False
            return
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and self.panel.gizmo_mode == "magnet":
            self._handle_panel_action("magnet")
            return
        modes = {pygame.KSCAN_Q: "cursor", pygame.KSCAN_W: "move",
                 pygame.KSCAN_E: "turn", pygame.KSCAN_M: "magnet"}
        if event.scancode in modes and not self.looking:
            self._set_gizmo_mode(modes[event.scancode])
            return
        if pygame.K_1 <= event.key <= pygame.K_9:
            tools = panel_mod._tools_for(self.editor_mode == "zone",
                                          getattr(self.model, "kind", None))
            idx = event.key - pygame.K_1
            if idx < len(tools):
                self.panel.current_tool = tools[idx]
                self._set_message(i18n.t("editor.msg.tool_picked",
                                          tool=i18n.t("editor.tool.%s" % tools[idx])))
            return
        if self.hover_cell is not None:
            if event.scancode == pygame.KSCAN_R:
                self._rotate_hovered(self._rotate_step())
            elif event.key in (pygame.K_DELETE, pygame.K_BACKSPACE):
                item = self._hovered_item()
                if item is not None:
                    self.model.remove_entry(item)
                else:
                    self.model.remove_furniture(*self.hover_cell)
                self._mark_dirty()

    DRAG_SLOP = 0.12

    REPEAT_DELAY = 0.38
    REPEAT_EVERY = 0.07

    def _tick_repeat(self):
        if self.repeat_action is None:
            return
        now = time.time()
        if now < self.repeat_at:
            return
        self.repeat_at = now + self.REPEAT_EVERY
        self._handle_panel_action(self.panel.apply_action(self.repeat_action, self.model))

    GIZMO_ARM = 0.62
    GIZMO_RING = 0.70
    GIZMO_ZARM = 0.50
    GIZMO_PICK_PX = 18
    GIZMO_RING_PX = 12

    def _furniture_props(self):
        out = []
        for i, f in enumerate(self.model.furniture):
            kind, x, y, z, facing = furn_entry(f)
            out.append((i, kind, x + PAD, y + PAD, z, facing))
        return out

    @staticmethod
    def _ray_box(origin, direction, cx, cy, z0, z1, hd, hw, facing):
        c, sn = math.cos(-facing), math.sin(-facing)
        ox, oy = origin[0] - cx, origin[1] - cy
        lx = ox * c - oy * sn
        ly = ox * sn + oy * c
        dx = direction[0] * c - direction[1] * sn
        dy = direction[0] * sn + direction[1] * c
        lo, hi = 0.0, 1e9
        for p, d, half_lo, half_hi in ((lx, dx, -hd, hd), (ly, dy, -hw, hw),
                                        (origin[2], direction[2], z0, z1)):
            if abs(d) < 1e-9:
                if p < half_lo or p > half_hi:
                    return None
                continue
            t0, t1 = (half_lo - p) / d, (half_hi - p) / d
            if t0 > t1:
                t0, t1 = t1, t0
            lo, hi = max(lo, t0), min(hi, t1)
            if lo > hi:
                return None
        return lo if lo > 0.0 else None

    def _cam(self):
        aspect = self.renderer.low_w / self.renderer.low_h
        return (self.primary_w, self.window_h, self.camera.eye, self.camera.yaw,
                self.camera.pitch, math.radians(FOV_DEGREES), aspect)

    def _to_screen(self, p):
        if self.view_mode == "grid":
            return self.grid.screen_of(p[0] - PAD, p[1] - PAD, self.model)
        return world_to_screen(p, *self._cam())

    def _plane_point(self, mx, my, z):
        if self.view_mode == "grid":
            pt = self.grid.point_at(mx, my, self.model)
            return None if pt is None else (pt[0] + PAD, pt[1] + PAD)
        if mx >= self.primary_w:
            return None
        return ray_floor_point(mx, my, *self._cam(), plane_z=z)

    def _pick_furniture(self, mx, my):
        if mx >= self.primary_w:
            return None
        if self.view_mode == "grid":
            pt = self.grid.point_at(mx, my, self.model)
            if pt is None:
                return None
            item = self.model.piece_at_point(pt[0], pt[1])
            if item is None:
                return None
            return next(i for i, f in enumerate(self.model.furniture) if f is item)
        origin, direction = screen_ray(mx, my, *self._cam())
        best = None
        for i, kind, x, y, z0, facing in self._furniture_props():
            spec = PROP_DEFS[kind]
            grab = 0.04
            t = self._ray_box(origin, direction, x, y, z0 - grab,
                              z0 + spec["height"] + grab,
                              spec["hd"] + grab, spec["hw"] + grab, facing)
            if t is None:
                continue
            key = (-z0, t)
            if best is None or key < best[0]:
                best = (key, i)
        return None if best is None else best[1]

    def _selected_item(self):
        entry = self.selected_entry
        if entry is None:
            return None
        for e in self.model.furniture:
            if e is entry:
                return entry
        self.selected_entry = None
        return None

    def _furn_world_pos(self, item):
        return float(item[1]) + PAD, float(item[2]) + PAD

    @staticmethod
    def _alt_held():
        return bool(pygame.key.get_mods() & pygame.KMOD_ALT)

    @staticmethod
    def _shift_held():
        return bool(pygame.key.get_mods() & pygame.KMOD_SHIFT)

    def _set_gizmo_mode(self, mode):
        self.panel.gizmo_mode = mode
        if mode != "cursor":
            self.panel.current_tool = "furniture"
        self.gizmo_handle = None
        self._set_message(i18n.t("editor.gizmo.%s" % mode))

    GIZMO_REF_DIST = 3.2

    def _gizmo_scale(self, item):
        if self.view_mode == "grid":
            return 1.0
        px, py = self._furn_world_pos(item)
        ex, ey, ez = self.camera.eye
        d = math.sqrt((px - ex) ** 2 + (py - ey) ** 2 + (float(item[3]) - ez) ** 2)
        return max(0.6, min(3.0, d / self.GIZMO_REF_DIST))


    def _gizmo_handles(self, item):
        kind = item[0]
        if self.model.docked(kind):
            return {}
        px, py = self._furn_world_pos(item)
        z = float(item[3]) + 0.03
        k = self._gizmo_scale(item)
        arm = self.GIZMO_ARM * k
        if PROP_DEFS[kind]["wall_mounted"]:
            ax, ay = self.model.wall_axis(item)
            out = {"along": (px + ax * arm, py + ay * arm, z),
                   "along-": (px - ax * arm, py - ay * arm, z)}
            if self.model.hangs(kind):
                out["z"] = (px, py, z + PROP_DEFS[kind]["height"] + self.GIZMO_ZARM * k)
            return out
        d = arm * 0.62
        return {"x": (px + arm, py, z), "y": (px, py + arm, z),
                "xy": (px + d, py + d, z), "free": (px, py, z)}

    def _handle_under_cursor(self, mx, my):
        item = self._selected_item()
        mode = self.panel.gizmo_mode
        if item is None or mode not in ("move", "turn") or mx >= self.primary_w:
            return None
        if mode == "turn":
            if not self.model.turns(item[0]):
                return None
            px, py = self._furn_world_pos(item)
            z = float(item[3]) + 0.03
            ring = self.GIZMO_RING * self._gizmo_scale(item)
            best = None
            n = 32
            pts = [self._to_screen((px + math.cos(math.tau * i / n) * ring,
                                    py + math.sin(math.tau * i / n) * ring, z))
                   for i in range(n + 1)]
            for a, b in zip(pts, pts[1:]):
                if a is None or b is None:
                    continue
                d = _seg_dist(mx, my, a, b)
                best = d if best is None else min(best, d)
            return "turn" if best is not None and best <= self.GIZMO_RING_PX else None
        best, best_d = None, self.GIZMO_PICK_PX
        for name, p in self._gizmo_handles(item).items():
            s = self._to_screen(p)
            if s is None:
                continue
            d = math.hypot(s[0] - mx, s[1] - my)
            if d <= best_d:
                best, best_d = name, d
        return best


    def _gizmo_press(self, mx, my, cell):
        mode = self.panel.gizmo_mode
        item = self._selected_item()
        if mode == "magnet":
            idx = self._pick_furniture(mx, my)
            if idx is None:
                return
            entry = self.model.furniture[idx]
            if any(e is entry for e in self.selection):
                self.selection = [e for e in self.selection if e is not entry]
            else:
                self.selection.append(entry)
            self.selected_entry = entry
            self._set_message(i18n.t("editor.msg.magnet_count", n=len(self.selection)))
            return
        handle = self._handle_under_cursor(mx, my)
        if item is not None and handle is not None:
            self._begin_drag(item, handle, mx, my)
            return
        idx = self._pick_furniture(mx, my)
        if idx is None:
            self.selected_furn = None
            self._set_message(i18n.t("editor.msg.gizmo_pick"))
            return
        entry = self.model.furniture[idx]
        self.selected_furn = (int(entry[1]), int(entry[2]))
        if self._shift_held():
            if any(e is entry for e in self.selection):
                self.selection = [e for e in self.selection if e is not entry]
            else:
                self.selection.append(entry)
        else:
            self.selection = [entry]
        self.selected_entry = entry
        if mode == "move":
            if self.model.docked(entry[0]):
                self._set_message(i18n.t("editor.msg.docked_fixed"), True)
                return
            wall = PROP_DEFS[entry[0]]["wall_mounted"]
            self._begin_drag(entry, "along" if wall else "free", mx, my)
        elif mode == "turn":
            if not self.model.turns(entry[0]):
                self._set_message(i18n.t("editor.msg.no_turn_wall"), True)
                return
            self._begin_drag(entry, "turn", mx, my)

    def _begin_drag(self, item, handle, mx, my):
        self.gizmo_handle = handle
        self._drag_riders = self.model.resting_on(item)
        self._drag_z = float(item[3]) + 0.03
        self._drag_moved = False
        px, py = self._furn_world_pos(item)
        pt = self._plane_point(mx, my, self._drag_z)
        self.gizmo_grab = (0.0, 0.0) if pt is None else (px - pt[0], py - pt[1])
        if handle == "turn":
            a0 = 0.0 if pt is None else math.atan2(pt[1] - py, pt[0] - px)
            self.gizmo_turn_from = (a0, float(item[4]))
        elif handle == "z":
            zc = ray_vertical_plane_z(mx, my, *self._cam(), px, py)
            self._z_grab = 0.0 if zc is None else float(item[3]) - zc

    def _end_drag(self):
        item = self._selected_item()
        if item is not None and self.gizmo_handle is not None and getattr(self, "_drag_moved", False):
            self.model.settle(item, riders=getattr(self, "_drag_riders", None))
            self._mark_dirty()
        self.gizmo_handle = None
        self._drag_riders = None

    def _gizmo_drag(self, mx, my):
        item = self._selected_item()
        if item is None or self.gizmo_handle is None:
            return
        handle = self.gizmo_handle
        riders = getattr(self, "_drag_riders", None)
        px, py = self._furn_world_pos(item)
        if handle == "z":
            zc = ray_vertical_plane_z(mx, my, *self._cam(), px, py)
            if zc is None:
                return
            want = zc + self._z_grab
            if self._alt_held():
                want = round(want / 0.05) * 0.05
            if self.model.set_hanging_height(item, want):
                self._drag_moved = True
                self._mark_dirty()
            return
        pt = self._plane_point(mx, my, self._drag_z)
        if pt is None:
            return
        gx, gy = self.gizmo_grab
        if handle == "turn":
            if math.hypot(pt[0] - px, pt[1] - py) < 0.08 * self._gizmo_scale(item):
                return
            a0, f0 = self.gizmo_turn_from
            want = f0 + math.atan2(pt[1] - py, pt[0] - px) - a0
            if self._alt_held():
                want = round(want / (math.pi / 2)) * (math.pi / 2)
            step = (want - float(item[4])) % math.tau
            self.model.rotate_entry(item, step, riders=riders)
            self._drag_moved = True
            self._mark_dirty()
            return
        if handle in ("along", "along-"):
            ax, ay = self.model.wall_axis(item)
            t = (pt[0] + gx - px) * ax + (pt[1] + gy - py) * ay
            want_x, want_y = px + ax * t, py + ay * t
        elif handle == "xy":
            step = ((pt[0] + gx - px) + (pt[1] + gy - py)) * 0.5
            want_x, want_y = px + step, py + step
        else:
            want_x = pt[0] + gx if handle in ("x", "free") else px
            want_y = pt[1] + gy if handle in ("y", "free") else py
        if self._alt_held():
            want_x = math.floor(want_x) + 0.5
            want_y = math.floor(want_y) + 0.5
        if self.model.move_entry(item, want_x - PAD, want_y - PAD, carry=True,
                                 riders=riders, settle=False):
            self.selected_furn = (int(item[1]), int(item[2]))
            self._drag_moved = True
            self._mark_dirty()


    _GIZMO_X = (1.0, 0.25, 0.22)
    _GIZMO_Y = (0.35, 0.95, 0.40)
    _GIZMO_FREE = (1.0, 0.85, 0.25)
    _GIZMO_XY = (1.0, 0.62, 0.20)
    _GIZMO_TURN = (0.35, 0.72, 1.0)
    _GIZMO_Z = (0.40, 0.60, 1.0)
    _GIZMO_WALL = (1.0, 0.55, 0.85)
    _GIZMO_PICKED = (0.35, 1.0, 0.85)

    def _draw_furniture_marks(self):
        hover = self._hover_furn_index
        chosen = self._selected_item()
        if hover is None and chosen is None and not self.selection:
            return
        ctx = self.renderer.ctx
        ctx.disable(moderngl.DEPTH_TEST)
        try:
            self._draw_marks_inner(hover, chosen)
        finally:
            ctx.enable(moderngl.DEPTH_TEST)

    def _draw_marks_inner(self, hover, chosen):
        magnet = self.panel.gizmo_mode == "magnet"
        for i, kind, x, y, z0, facing in self._furniture_props():
            f = self.model.furniture[i]
            is_chosen = chosen is not None and f is chosen
            in_sel = any(f is e for e in self.selection)
            if i != hover and not is_chosen and not (in_sel and (magnet or len(self.selection) > 1)):
                continue
            spec = PROP_DEFS[kind]
            if in_sel and (magnet or len(self.selection) > 1):
                color = self._GIZMO_PICKED
            elif is_chosen:
                color = self._GIZMO_FREE
            else:
                color = (0.55, 0.75, 0.95)
            grow = 2.35 if i == hover else 2.2
            self.renderer._draw_box(
                gm.translate(x, y, max(0.015, z0 - 0.01)) @ gm.rotate_z(facing)
                @ gm.scale(spec["hd"] * grow, spec["hw"] * grow, 0.03),
                color, emissive=1.0, vao=self.renderer.box_vao)

    def _selected_draw_info(self):
        item = self._selected_item()
        if item is None:
            return None
        px, py = self._furn_world_pos(item)
        return px, py, float(item[3])

    GHOST_COLOR = (1.0, 0.85, 0.25)

    def _draw_place_ghost(self):
        mx, my = pygame.mouse.get_pos()
        pt = self._resolve_point(mx, my)
        if pt is None:
            return False
        kind = self.panel.current_furniture_kind
        px, py = pt[0] - PAD, pt[1] - PAD
        if self._alt_held():
            px, py = math.floor(px) + 0.5, math.floor(py) + 0.5
        want = self.model.preview_place(kind, px, py)
        if want is None:
            return False
        spec = PROP_DEFS[kind]
        _k, gx, gy, gz, gf = furn_entry(want)
        model = (gm.translate(gx + PAD, gy + PAD, gz)
                 @ gm.rotate_z(gf)
                 @ gm.scale(spec["hd"] * 2, spec["hw"] * 2, spec["height"]))
        vao = self.renderer.prop_vaos.get(kind, self.renderer.box_vao)
        self.renderer._draw_box(model, self.GHOST_COLOR, emissive=1.0, vao=vao)
        return True

    def _draw_gizmo(self):
        item = self._selected_item()
        if (item is None or self.panel.gizmo_mode not in ("move", "turn")
                or self.testing_floor is not None):
            return
        ctx = self.renderer.ctx
        ctx.disable(moderngl.DEPTH_TEST)
        try:
            self._draw_gizmo_shapes(item)
        finally:
            ctx.enable(moderngl.DEPTH_TEST)

    def _draw_gizmo_shapes(self, item):
        box = self.renderer.box_vao
        live = self.gizmo_handle or self.gizmo_hover

        def lit(color, on):
            return tuple(min(1.0, c * 0.55 + 0.55) for c in color) if on else color

        def cube(x, y, z, s, color, on=False):
            k = 1.45 if on else 1.0
            self.renderer._draw_box(gm.translate(x, y, z) @ gm.scale(s * k, s * k, s * 0.7 * k),
                                    lit(color, on), emissive=1.0, vao=box)

        def rod(a, b, color, on, n=7, s=0.045):
            for i in range(n):
                t = (i + 1) / (n + 1.0)
                cube(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t,
                     a[2] + (b[2] - a[2]) * t, s, color, on)

        px, py = self._furn_world_pos(item)
        z = float(item[3]) + 0.03
        k = self._gizmo_scale(item)
        if self.panel.gizmo_mode == "turn":
            if not self.model.turns(item[0]):
                return
            on = live == "turn"
            n = 24
            ring = self.GIZMO_RING * k
            for i in range(n):
                a = math.tau * i / n
                cube(px + math.cos(a) * ring, py + math.sin(a) * ring, z, 0.075 * k,
                     self._GIZMO_TURN, on)
            cube(px, py, z, 0.09 * k, self._GIZMO_FREE)
            return
        centre = (px, py, z)
        colours = {"x": self._GIZMO_X, "y": self._GIZMO_Y, "xy": self._GIZMO_XY,
                   "free": self._GIZMO_FREE, "along": self._GIZMO_WALL,
                   "along-": self._GIZMO_WALL, "z": self._GIZMO_Z}
        for name, p in self._gizmo_handles(item).items():
            on = live == name or (live in ("along", "along-") and name in ("along", "along-"))
            col = colours[name]
            if name != "free":
                rod(centre, p, col, on, s=0.045 * k)
            cube(p[0], p[1], p[2], (0.11 if name != "free" else 0.10) * k, col, on)

    def _paint_at(self, mx, my):
        cell = self._resolve_cell(mx, my)
        if cell is None:
            return
        self.hover_cell = cell
        if self.panel.current_tool == "floor":
            self._erase_cell(*cell)
        else:
            self.model.set_cell(cell[0], cell[1], self.paint_value)
        self._mark_dirty()

    def _erase_cell(self, x, y):
        if self.editor_mode == "zone":
            if self.model.furniture_at(x, y) is not None:
                self.model.remove_furniture(x, y)
                return True
            if self.model.interior_door_at(x, y) is not None:
                self.model.click_interior_door(x, y)
                return True
            if self.model.cells[y][x] != S.FLOOR:
                self.model.set_cell(x, y, S.FLOOR)
                return True
            return False
        if self.model.furniture_at(x, y) is not None:
            self.model.remove_furniture(x, y)
            return True
        if self.model.door_at(x, y) is not None or self.model.interior_door_at(x, y) is not None:
            self.model.click_door(x, y, kind="passage")
            return True
        if self.model.cells[y][x] != S.FLOOR:
            self.model.set_cell(x, y, S.FLOOR)
            return True
        return False

    def _handle_mousedown(self, event):
        mx, my = event.pos
        if self.show_help:
            self.show_help = False
            return
        if event.button == 3:
            self.looking = True
            pygame.mouse.set_visible(False)
            pygame.event.set_grab(True)
            pygame.mouse.set_pos(self._look_center())
            return
        if event.button != 1:
            return
        if mx >= self.primary_w:
            hit = self.panel.action_at(mx - self.primary_w, my)
            if hit in panel_mod.REPEATABLE_ACTIONS:
                self.repeat_action = hit
                self.repeat_at = time.time() + self.REPEAT_DELAY
            action = self.panel.handle_click(mx - self.primary_w, my, self.model, self.view_mode,
                                              self.natural_color, self.testing_floor, self.editor_mode,
                                              self.mode == "dev")
            self._handle_panel_action(action)
            return
        if self.testing_floor is not None:
            return
        if self.panel.gizmo_mode != "cursor" and self.panel.current_tool == "furniture":
            self._gizmo_press(mx, my, None)
            return
        cell = self._resolve_cell(mx, my)
        if cell is None:
            return
        x, y = cell
        tool = self.panel.current_tool
        if tool in ("wall", "window", "bars", "brick"):
            if self.editor_mode == "zone" and self.model.on_border(x, y):
                self._set_message(i18n.t("editor.msg.zone_border_fixed"), True)
                return
            if tool == "window":
                self.paint_value = S.WALL_WINDOW
            elif tool == "bars":
                self.paint_value = S.WALL_BARS
            elif tool == "brick":
                self.paint_value = S.WALL_BRICK
            else:
                self.paint_value = S.WALL_SHED if self.editor_mode == "zone" else S.WALL_CONCRETE
            self.painting = True
            self.model.set_cell(x, y, self.paint_value)
            self._mark_dirty()
        elif tool == "floor":
            self.painting = True
            if self._erase_cell(x, y):
                self._mark_dirty()
            else:
                self._set_message(i18n.t("editor.msg.nothing_to_erase"), True)
        elif tool == "door" and self.editor_mode == "zone":
            if self.model.click_interior_door(x, y, kind=self.panel.current_door_kind):
                self._mark_dirty()
            else:
                self._set_message(i18n.t("editor.msg.no_interior_door"), True)
        elif tool == "door":
            if self.model.click_door(x, y, kind=self.panel.current_door_kind):
                self._mark_dirty()
            else:
                side = self.model.side_of_border_cell(x, y)
                if side is not None and side in self.model.doors:
                    self._set_message(i18n.t("editor.msg.door_side_taken"), True)
                else:
                    self._set_message(i18n.t("editor.msg.no_door_here"), True)
        elif tool == "furniture":
            if self.panel.gizmo_mode != "cursor":
                self._gizmo_press(mx, my, (x, y))
                return
            pt = self._resolve_point(mx, my)
            px = (pt[0] - PAD) if pt else (x + 0.5)
            py = (pt[1] - PAD) if pt else (y + 0.5)
            if self._alt_held():
                px, py = math.floor(px) + 0.5, math.floor(py) + 0.5
            ok = self.model.place_furniture(self.panel.current_furniture_kind, px, py)
            if ok is not None:
                self.selected_entry = ok
                self.selection = [ok]
                self._mark_dirty()
            elif PROP_DEFS[self.panel.current_furniture_kind]["wall_mounted"]:
                self._set_message(i18n.t("editor.msg.wall_only_furniture"), True)
            else:
                self._set_message(i18n.t("editor.msg.cant_place_furniture"), True)

    def _handle_panel_action(self, action):
        if action is None:
            self._mark_dirty()
            return
        if action == "magnet":
            picked = [e for e in self.selection if any(e is f for f in self.model.furniture)]
            if len(picked) < 2:
                self._set_message(i18n.t("editor.msg.magnet_none"), True)
                return
            n = self.model.magnet(picked)
            self._set_message(i18n.t("editor.msg.magnet_done", n=n))
            self._mark_dirty()
            return
        if action == "magnet_clear":
            self.selection = []
            self._set_message(i18n.t("editor.msg.magnet_cleared"))
            return
        if action == "toggle_view":
            self._toggle_view()
            return
        if action == "toggle_natural_color":
            self.natural_color = not self.natural_color
            return
        if action.startswith("test_floor:"):
            self._enter_test_floor(int(action.split(":", 1)[1]))
            return
        if action == "test_reroll":
            if self.testing_floor is not None:
                self._enter_test_floor(self.testing_floor)
            return
        if action == "test_exit":
            self._exit_test_floor()
            return
        if action == "toggle_editor_mode":
            self.editor_mode = "zone" if self.editor_mode == "room" else "room"
            if self.editor_mode == "zone":
                self.model = zm.ZoneModel(id=zm.fresh_id("open"), kind="open")
            else:
                self.model = rm.RoomModel(id=rm.fresh_id("ward", "upper"), kind="ward", floor="upper")
            self.panel.current_tool = "wall" if self.editor_mode == "zone" else "furniture"
            self._center_camera_on_room()
            self._mark_dirty()
            self._set_message(i18n.t("editor.msg.mode_zone" if self.editor_mode == "zone" else "editor.msg.mode_room"))
            return
        if action == "new":
            if self.editor_mode == "zone":
                kind = self.model.kind
                self.model = zm.ZoneModel(id=zm.fresh_id(kind), kind=kind)
            else:
                kind, floor = self.model.kind, self.model.floor
                self.model = rm.RoomModel(id=rm.fresh_id(kind, floor), kind=kind, floor=floor)
            self._center_camera_on_room()
            self._mark_dirty()
            msg_key = "editor.msg.new_zone" if self.editor_mode == "zone" else "editor.msg.new_room"
            self._set_message(i18n.t(msg_key, id=self.model.id))
        elif action == "save" and self.mode == "user":
            errors = self.model.all_errors()
            if errors:
                self._set_message(i18n.t("editor.msg.cant_save", errors="; ".join(errors)), True)
                return
            nickname = up.get_saved_nickname()
            if not nickname:
                self.prompting_nickname = True
                self._nickname_buffer = ""
                pygame.key.start_text_input()
                return
            self._do_upload_save(nickname)
        elif action == "save":
            errors = self.model.all_errors()
            if errors:
                self._set_message(i18n.t("editor.msg.cant_save", errors="; ".join(errors)), True)
                return
            if not self.model.id or not self.model.id.replace("_", "").replace("-", "").isalnum():
                self._set_message(i18n.t("editor.msg.bad_id"), True)
                return
            path = self.model.save()
            self._set_message(i18n.t("editor.msg.saved", filename=os.path.basename(path)))
        elif action == "validate":
            errors = self.model.all_errors()
            ok_key = "editor.msg.zone_ok" if self.editor_mode == "zone" else "editor.msg.room_ok"
            self._set_message(i18n.t(ok_key) if not errors else "; ".join(errors), bool(errors))
        elif action.startswith("load:"):
            self._load(action.split(":", 1)[1])
        elif action == "import_pick":
            self._do_import_via_dialog()

    def _do_import_via_dialog(self):
        try:
            import tkinter as tk
            from tkinter import filedialog
        except Exception as e:
            self._set_message(i18n.t("editor.msg.import_dialog_unavailable", error=str(e)), True)
            return
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
            path = filedialog.askopenfilename(
                title=i18n.t("editor.ui.import_button"),
                filetypes=[(i18n.t("editor.ui.zip_files"), "*.zip")],
            )
        finally:
            root.destroy()
        if not path:
            return
        try:
            with open(path, "rb") as f:
                blob = f.read()
        except OSError as e:
            self._set_message(i18n.t("editor.msg.import_failed", error=str(e)), True)
            return
        self._do_import(blob)

    def _do_import(self, blob):
        try:
            parsed = up.unpack_upload_zip(blob)
            data = parsed["model_json"]
            if not isinstance(data, dict):
                raise ValueError("no room/zone json in the archive")

            is_zone = "floor" not in data
            if is_zone:
                kind = data.get("kind")
                if kind not in zm.ZONE_KINDS:
                    kind = "open"
                data["kind"] = kind
                existing = {k: v for k, v in data.items() if k != "id"}
                for eid in zm.list_saved_ids():
                    other = zm.load(eid)
                    if {k: v for k, v in other.to_json_dict().items() if k != "id"} == existing:
                        self._set_message(i18n.t("editor.msg.import_duplicate", id=eid))
                        return
                data["id"] = zm.fresh_id(kind)
                model = zm.ZoneModel.from_json_dict(data)
            else:
                floor = data.get("floor")
                if floor not in rm.KINDS_BY_FLOOR:
                    floor = "upper"
                kind = data.get("kind")
                if kind not in rm.KINDS_BY_FLOOR[floor]:
                    kind = rm.KINDS_BY_FLOOR[floor][0]
                data["floor"], data["kind"] = floor, kind
                existing = {k: v for k, v in data.items() if k != "id"}
                for eid in rm.list_saved_ids():
                    other = rm.load(eid)
                    if {k: v for k, v in other.to_json_dict().items() if k != "id"} == existing:
                        self._set_message(i18n.t("editor.msg.import_duplicate", id=eid))
                        return
                data["id"] = rm.fresh_id(kind, floor)
                model = rm.RoomModel.from_json_dict(data)
            path = model.save()
        except Exception as e:
            self._set_message(i18n.t("editor.msg.import_failed", error=str(e)), True)
            return
        nickname = parsed.get("nickname") or "?"
        self._set_message(i18n.t("editor.msg.imported", filename=os.path.basename(path), nickname=nickname))

    def _load(self, item_id):
        self.model = zm.load(item_id) if self.editor_mode == "zone" else rm.load(item_id)
        self._center_camera_on_room()
        self._mark_dirty()
        self._set_message(i18n.t("editor.msg.loaded", id=item_id))

    def _do_upload_save(self, nickname):
        self.uploading = True
        self._set_message(i18n.t("editor.msg.uploading"))
        self._draw()
        kind = "zone" if self.editor_mode == "zone" else "room"
        jpegs = self._capture_prerenders(3)
        model_json = self.model.to_json_dict()
        filename = f"{self.model.id}.json"
        blob = up.build_upload_zip(model_json, filename, nickname, jpegs)
        ok, err = up.upload(blob, kind, self.model.id)
        self.uploading = False
        if ok:
            self._set_message(i18n.t("editor.msg.upload_ok"))
        else:
            self._set_message(i18n.t("editor.msg.upload_failed", error=err or "?"), True)

    def _enter_test_floor(self, index):
        spec = S.FLOOR_SPECS[index]
        seed = random.randrange(1 << 30)
        self.test_seed = seed
        layout = spec.get("layout", "corridor")
        if layout == "yard":
            maze = Maze(w=S.YARD_W, h=S.YARD_H, seed=seed, layout="yard")
            props, panel_prop, exit_prop, monster_cell, doors = populate_yard(maze, spec, random.Random(seed ^ 0x5EED))
        else:
            maze = Maze(seed=seed, wall_bias=spec["wall_bias"], template_floor=spec.get("floor_theme"),
                        room_count_range=spec.get("room_count"))
            props, panel_prop, exit_prop, monster_cell, doors = populate_level(maze, spec, random.Random(seed ^ 0x5EED))

        self.testing_floor = index
        self._test_maze = maze
        self._test_props = props + doors
        self._test_spawn = maze.start
        self._test_monster = Monster(
            monster_cell[0] + 0.5, monster_cell[1] + 0.5, maze, rng=random.Random(0),
            speed_mult=0.0, vision_mult=0.0, blocked_cells=set(), lockers=[], doors=doors,
        )
        self.renderer.build_level(maze, theme=spec.get("floor_theme", "upper"))
        self.view_mode = "camera"
        sx, sy = maze.start
        self.camera.x, self.camera.y, self.camera.z = sx, sy, 2.4
        self.camera.yaw = math.pi / 4
        self.camera.pitch = -0.4
        self._set_message(i18n.t("editor.msg.test_floor", title=i18n.t(spec["title"])))

    def _exit_test_floor(self):
        self.testing_floor = None
        self._test_maze = None
        self._test_props = None
        self._test_monster = None
        self._mark_dirty()
        self._center_camera_on_room()
        self._set_message(i18n.t("editor.msg.back_to_editing"))

    def _look_center(self):
        return (self.primary_w // 2, self.window_h // 2)

    def _update_camera(self, dt):
        sd = self._scan_down
        if not self.looking:
            self.camera.update(dt, {}, sprint=False)
            return
        move = {
            "forward": pygame.KSCAN_W in sd, "back": pygame.KSCAN_S in sd,
            "left": pygame.KSCAN_A in sd, "right": pygame.KSCAN_D in sd,
            "up": pygame.KSCAN_SPACE in sd, "down": pygame.KSCAN_LCTRL in sd,
        }
        self.camera.update(dt, move, sprint=pygame.KSCAN_LSHIFT in sd)

        if self.looking:
            cx, cy = self._look_center()
            mx, my = pygame.mouse.get_pos()
            dx, dy = mx - cx, my - cy
            if dx or dy:
                self.camera.look(dx, dy)
            pygame.mouse.set_pos((cx, cy))

    _FLAT_PREVIEW = dict(fog_color=(30, 30, 34), fog_dist=80.0, ambient=0.85,
                          moon_strength=0.0, qa_mode=True)

    def _render_light_params(self):
        if not self.natural_color:
            return self._FLAT_PREVIEW
        if self.testing_floor is not None:
            spec = S.FLOOR_SPECS[self.testing_floor]
        elif self.editor_mode == "zone":
            spec = next((s for s in S.FLOOR_SPECS if s.get("layout") == "yard"), None)
        else:
            spec = next((s for s in S.FLOOR_SPECS if s.get("floor_theme") == self.model.floor), None)
        if spec is None:
            return self._FLAT_PREVIEW
        return dict(
            fog_color=spec["fog_color"], fog_dist=spec["fog_dist"], ambient=spec["ambient_level"],
            moon_strength=spec.get("moon_strength", 0.0), qa_mode=False,
        )

    def _capture_prerenders(self, count=3):
        saved = (self.camera.x, self.camera.y, self.camera.z, self.camera.yaw, self.camera.pitch)
        cx, cy = self.model.w / 2 + PAD, self.model.h / 2 + PAD
        margin = min(1.8, max(0.8, min(self.model.w, self.model.h) / 3))
        corners = [
            (PAD + margin, PAD + margin),
            (PAD + self.model.w - margin, PAD + margin),
            (PAD + margin, PAD + self.model.h - margin),
            (PAD + self.model.w - margin, PAD + self.model.h - margin),
        ]
        jpegs = []
        for i in range(count):
            self.camera.x, self.camera.y = corners[i % len(corners)]
            self.camera.z = 0.9
            self.camera.yaw = math.atan2(cy - self.camera.y, cx - self.camera.x)
            self.camera.pitch = -0.05
            self._fake_player.x, self._fake_player.y = self.camera.x, self.camera.y
            self._fake_player.peek_x, self._fake_player.peek_y = self._fake_player.x, self._fake_player.y
            self._fake_player.angle = self.camera.yaw
            self._fake_player.pitch = self.camera.pitch
            self._fake_player.bob_phase = 0.0
            self._fake_player.is_sprinting = False
            self._fake_player.is_hiding = False
            self._fake_player.crouch = (EYE_HEIGHT - self.camera.z) / S.CROUCH_EYE_DROP
            self.renderer.render(
                self._fake_maze, self._fake_player, self._fake_monster, self._current_props(),
                dread=0.0, t=0.0, fog_color=(30, 30, 34), fog_dist=80.0, ambient=0.85,
                moon_strength=0.0, qa_mode=True,
            )
            data = self.renderer.color_tex.read()
            img = pygame.image.frombuffer(data, self.renderer.color_tex.size, "RGB")
            img = pygame.transform.flip(img, False, True)
            buf = io.BytesIO()
            pygame.image.save(img, buf, "shot.jpg")
            jpegs.append(buf.getvalue())
        self.camera.x, self.camera.y, self.camera.z, self.camera.yaw, self.camera.pitch = saved
        return jpegs

    def _draw(self):
        self._fake_player.x, self._fake_player.y = self.camera.x, self.camera.y
        self._fake_player.peek_x, self._fake_player.peek_y = self._fake_player.x, self._fake_player.y
        self._fake_player.angle = self.camera.yaw
        self._fake_player.pitch = self.camera.pitch
        self._fake_player.bob_phase = 0.0
        self._fake_player.is_sprinting = False
        self._fake_player.is_hiding = False
        self._fake_player.crouch = (EYE_HEIGHT - self.camera.z) / S.CROUCH_EYE_DROP

        saved_ceil = self.renderer.ceil_vao
        if self.camera.z >= S.WALL_HEIGHT:
            self.renderer.ceil_vao = None

        testing = self.testing_floor is not None
        if testing:
            maze, monster, props = self._test_maze, self._test_monster, self._test_props
        else:
            maze, monster, props = self._fake_maze, self._fake_monster, self._current_props()
        t = time.time() - self._t0
        self.renderer.render(
            maze, self._fake_player, monster, props, dread=0.0, t=t,
            **self._render_light_params(),
        )
        self.renderer.ceil_vao = saved_ceil

        if testing:
            sx, sy = self._test_spawn
            model = gm.translate(sx, sy, 0.0) @ gm.scale(0.14, 0.14, 1.7)
            self.renderer._draw_box(model, (0.35, 1.0, 0.55), emissive=1.0, vao=self.renderer.box_vao)
        elif self.panel.gizmo_mode != "cursor":
            self._draw_furniture_marks()
        elif self.panel.current_tool == "furniture" and self._draw_place_ghost():
            pass
        elif self.hover_cell is not None:
            hx, hy = self.hover_cell[0] + PAD, self.hover_cell[1] + PAD
            model = gm.translate(hx + 0.5, hy + 0.5, 0.02) @ gm.scale(0.92, 0.92, 0.03)
            color = (1.0, 0.85, 0.2) if self.panel.current_tool == "furniture" else \
                    (0.3, 0.8, 1.0) if self.panel.current_tool == "door" else (1.0, 0.3, 0.3)
            self.renderer._draw_box(model, color, emissive=1.0, vao=self.renderer.box_vao)
        self._draw_gizmo()

        self.panel.magnet_count = sum(
            1 for e in self.selection if any(e is f for f in self.model.furniture))
        panel_surf = pygame.Surface((self.panel.width, self.window_h), pygame.SRCALPHA)
        self.panel.draw(panel_surf, self.model, self.view_mode, self.natural_color, self.testing_floor,
                         self.editor_mode, self.mode == "dev", getattr(self, "test_seed", None))

        hud_surf = pygame.Surface((self.window_w, self.window_h), pygame.SRCALPHA)
        if not testing and self.view_mode == "grid":
            grid_surf = pygame.Surface((self.primary_w, self.window_h), pygame.SRCALPHA)
            required = None if self.editor_mode == "zone" else rm.required_fixture_kind(
                self.model.kind, self.model.floor)
            self.grid.draw(grid_surf, self.model, self.hover_cell, {required} if required else set())
            hud_surf.blit(grid_surf, (0, 0))
        hud_surf.blit(panel_surf, (self.primary_w, 0))
        self._draw_message(hud_surf)
        if not testing:
            hint = i18n.t("editor.ui.f1_hint")
            hint_surf = self._msg_font.render(hint, True, (150, 145, 140))
            hud_surf.blit(hint_surf, (12, self.window_h - hint_surf.get_height() - 10))
        if self.show_help:
            self._draw_help(hud_surf)
        if self.prompting_nickname:
            self._draw_nickname_prompt(hud_surf)

        hud_bytes = pygame.image.tostring(hud_surf, "RGBA", True)
        self._composite(hud_bytes)
        pygame.display.flip()

    @staticmethod
    def _help_sections():
        return [
            (i18n.t(f"editor.help.{n}.title"), i18n.t(f"editor.help.{n}.body").split("\n"))
            for n in range(1, 9)
        ]

    def _draw_nickname_prompt(self, hud_surf):
        overlay = pygame.Surface((self.window_w, self.window_h), pygame.SRCALPHA)
        overlay.fill((4, 3, 4, 220))
        hud_surf.blit(overlay, (0, 0))

        cx, cy = self.window_w // 2, self.window_h // 2
        box_w, box_h = 560, 160
        box = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
        box.fill((22, 20, 24, 245))
        pygame.draw.rect(box, (90, 85, 80), box.get_rect(), 2)
        title = self._msg_font.render(i18n.t("editor.ui.nickname_prompt_title"), True, (225, 210, 200))
        box.blit(title, title.get_rect(center=(box_w // 2, 34)))
        field = pygame.Rect(30, 66, box_w - 60, 40)
        pygame.draw.rect(box, (40, 38, 42), field)
        pygame.draw.rect(box, (140, 130, 120), field, 1)
        text = self._nickname_buffer + ("_" if int(time.time() * 2) % 2 == 0 else "")
        text_surf = self._msg_font.render(text, True, (230, 225, 220))
        box.blit(text_surf, (field.x + 10, field.y + (field.h - text_surf.get_height()) // 2))
        hint = self._help_font.render(i18n.t("editor.ui.nickname_prompt_hint"), True, (150, 145, 140))
        box.blit(hint, hint.get_rect(center=(box_w // 2, box_h - 26)))
        hud_surf.blit(box, (cx - box_w // 2, cy - box_h // 2))

    def _draw_help(self, hud_surf):
        overlay = pygame.Surface((self.window_w, self.window_h), pygame.SRCALPHA)
        overlay.fill((4, 3, 4, 235))
        hud_surf.blit(overlay, (0, 0))

        cx = self.window_w // 2
        title = self._help_title_font.render(i18n.t("editor.ui.help_title"), True, (225, 210, 200))
        hud_surf.blit(title, title.get_rect(center=(cx, 46)))
        sub = self._msg_font.render(i18n.t("editor.ui.help_close_hint"), True, (140, 135, 130))
        hud_surf.blit(sub, sub.get_rect(center=(cx, 78)))

        col_w = min(620, self.window_w // 2 - 60)
        col_x = (cx - col_w - 30, cx + 30)
        top, line_h, head_h, gap = 104, 22, 28, 16
        blocks = []
        for header, lines in self._help_sections():
            wrapped = [w for para in self._reflow_help(lines)
                       for w in self._wrap_help(para, col_w)]
            blocks.append((header, wrapped, head_h + len(wrapped) * line_h + gap))
        total = sum(b[2] for b in blocks)
        split, run = len(blocks), 0
        for i, b in enumerate(blocks):
            if run + b[2] / 2.0 > total / 2.0:
                split = max(1, i)
                break
            run += b[2]
        cols = (blocks[:split], blocks[split:])
        tallest = max(sum(b[2] for b in c) for c in cols)
        room = self.window_h - top - 20
        self.help_scroll = max(0, min(max(0, tallest - room), getattr(self, "help_scroll", 0)))
        clip = hud_surf.get_clip()
        hud_surf.set_clip(pygame.Rect(0, top - 4, self.window_w, room + 8))
        for ci, column in enumerate(cols):
            x, y = col_x[ci], top - self.help_scroll
            for header, wrapped, _h in column:
                hud_surf.blit(self._msg_font.render(header, True, (205, 120, 100)), (x, y))
                y += head_h
                for line in wrapped:
                    if line:
                        hud_surf.blit(self._help_font.render(line, True, (205, 198, 190)), (x, y))
                    y += line_h
                y += gap
        hud_surf.set_clip(clip)
        if tallest > room:
            more = self._msg_font.render(i18n.t("editor.ui.help_scroll"), True, (140, 135, 130))
            hud_surf.blit(more, more.get_rect(center=(cx, self.window_h - 12)))

    @staticmethod
    def _reflow_help(lines):
        out, cur = [], ""
        for line in lines:
            line = line.strip()
            if not line:
                if cur:
                    out.append(cur)
                    cur = ""
                out.append("")
                continue
            cur = line if not cur else cur + " " + line
            if cur[-1] in ".:!?)":
                out.append(cur)
                cur = ""
        if cur:
            out.append(cur)
        return out

    def _wrap_help(self, text, width):
        if not text.strip():
            return [""]
        words, out, cur = text.split(" "), [], ""
        for word in words:
            trial = word if not cur else cur + " " + word
            if self._help_font.size(trial)[0] <= width:
                cur = trial
            else:
                if cur:
                    out.append(cur)
                cur = word
        if cur:
            out.append(cur)
        return out

    MESSAGE_SECONDS = 3.5
    MESSAGE_SECONDS_ERROR = 8.0
    MESSAGE_FADE = 0.7

    def _draw_message(self, hud_surf):
        if not self.message:
            return
        total = self.MESSAGE_SECONDS_ERROR if self.message_is_error else self.MESSAGE_SECONDS
        age = time.time() - self.message_time
        if age >= total:
            self.message = ""
            return
        fade = min(1.0, max(0.0, (total - age) / self.MESSAGE_FADE))
        color = (255, 130, 110) if self.message_is_error else (225, 225, 230)
        max_w = self.window_w - 80
        words = self.message.split(" ")
        lines, cur = [], ""
        for word in words:
            trial = (cur + " " + word).strip()
            if cur and self._msg_font.size(trial)[0] > max_w:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        if cur:
            lines.append(cur)
        line_h = self._msg_font.get_height() + 6
        box_h = line_h * len(lines) + 24
        box = pygame.Surface((self.window_w, box_h), pygame.SRCALPHA)
        box.fill((12, 12, 15, 235))
        border = (200, 70, 60) if self.message_is_error else (80, 80, 92)
        pygame.draw.rect(box, border, box.get_rect(), 2)
        for i, line in enumerate(lines):
            txt = self._msg_font.render(line, True, color)
            box.blit(txt, ((self.window_w - txt.get_width()) // 2, 12 + i * line_h))
        if fade < 1.0:
            box.set_alpha(int(255 * fade))
        hud_surf.blit(box, (0, self.window_h - box_h - 16))

    def _composite(self, hud_rgba_bytes):
        ctx = self.ctx
        ctx.screen.use()
        ctx.viewport = (0, 0, self.window_w, self.window_h)
        ctx.disable(moderngl.DEPTH_TEST)
        ctx.disable(moderngl.BLEND)
        ctx.clear(0.0, 0.0, 0.0)

        if self.view_mode == "camera":
            ctx.viewport = (0, 0, self.primary_w, self.window_h)
            self.renderer.color_tex.use(location=0)
            self.renderer.quad_prog["tex0"].value = 0
            self.renderer.quad_vao.render(moderngl.TRIANGLES)

        ctx.viewport = (0, 0, self.window_w, self.window_h)
        self.renderer.ensure_hud_texture(self.window_w, self.window_h)
        self.renderer.hud_tex.write(hud_rgba_bytes)
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
        self.renderer.hud_tex.use(location=0)
        self.renderer.quad_prog["tex0"].value = 0
        self.renderer.quad_vao.render(moderngl.TRIANGLES)
        ctx.disable(moderngl.BLEND)


def main():
    Editor(mode="dev").run()
