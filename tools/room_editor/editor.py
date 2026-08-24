import io
import math
import os
import random
import time

import moderngl
import pygame

from game import settings as S
from game import gl_math as gm
from game import i18n
from game.maze import Maze
from game.entities import Player, Monster
from game.props import (
    Door, Prop, _authored_prop_position, PROP_DEFS, SURFACE_ITEM_KINDS, populate_level, populate_yard,
    link_adjacent_pipes,
)
from game.renderer3d import Renderer3D, EYE_HEIGHT, FOV_DEGREES
from game.room_templates import door_facing as _border_door_facing

from tools.room_editor import room_model as rm
from tools.room_editor import upload as up
from tools.room_editor import zone_model as zm
from tools.room_editor.camera import FreeCamera
from tools.room_editor.grid_view import GridView
from tools.room_editor.panel import Panel
from tools.room_editor.raycast import ray_floor_cell


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

        self._fake_maze = Maze(w=16, h=16, layout="debug")
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
        self._msg_font = pygame.font.SysFont("consolas,monospace", 22)
        self._help_title_font = pygame.font.SysFont("consolas,monospace", 28, bold=True)
        self._help_font = pygame.font.SysFont("consolas,monospace", 19)
        self.show_help = False

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
        self._fake_maze.w, self._fake_maze.h = gw, gh
        self._fake_maze.grid = grid
        theme = self.model.floor if self.editor_mode == "room" else "yard"
        self.renderer.build_level(self._fake_maze, theme=theme)
        self.dirty = False

    def _mark_dirty(self):
        self.dirty = True

    def _current_props(self):
        out = []
        furn_kind_by_cell = {
            (lx + PAD, ly + PAD): (kind, facing) for kind, lx, ly, facing in self.model.furniture
            if kind not in SURFACE_ITEM_KINDS
        }
        for kind, lx, ly, facing in self.model.furniture:
            cell = (lx + PAD, ly + PAD)
            x, y, z0, forced_facing = _authored_prop_position(cell, kind, facing, self._fake_maze, furn_kind_by_cell)
            if forced_facing is not None:
                facing = forced_facing
            p = Prop(kind, x, y, facing=facing)
            p.z0 = z0
            out.append(p)
        for info in self.model.doors.values():
            if info["kind"] in ("passage", "window"):
                continue
            lx, ly = info["cell"]
            side = self.model.side_of_border_cell(lx, ly)
            facing = _border_door_facing(side) if side else 0.0
            d = Door(lx + PAD + 0.5, ly + PAD + 0.5, facing)
            if info["kind"] == "broken":
                d.break_open()
            out.append(d)
        for lx, ly, facing, kind in self.model.interior_doors:
            if kind == "window":
                continue
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
            self._update_camera(dt)
            if self.testing_floor is not None:
                self.hover_cell = None
            elif not self.looking:
                self.hover_cell = self._resolve_cell(*pygame.mouse.get_pos())
            if self.dirty and self.testing_floor is None:
                self._rebuild_level()
            self._draw()
        pygame.quit()

    def _toggle_view(self):
        self.view_mode = "camera" if self.view_mode == "grid" else "grid"
        self.painting = False

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
                self._handle_keydown(event)
            elif event.type == pygame.MOUSEBUTTONDOWN:
                self._handle_mousedown(event)
            elif event.type == pygame.MOUSEBUTTONUP:
                if event.button == 3:
                    self.looking = False
                    pygame.mouse.set_visible(True)
                    pygame.event.set_grab(False)
                elif event.button == 1:
                    self.painting = False
            elif event.type == pygame.MOUSEWHEEL:
                mx, my = pygame.mouse.get_pos()
                if mx >= self.primary_w:
                    self.panel.scroll(event.y, self.window_h)
            elif event.type == pygame.MOUSEMOTION:
                if self.painting and self.panel.current_tool in ("wall", "floor"):
                    self._paint_at(*event.pos)

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
            else:
                self.running = False
            return
        if self.hover_cell is not None:
            if event.key == pygame.K_r:
                self.model.rotate_furniture(*self.hover_cell)
                self._mark_dirty()
            elif event.key in (pygame.K_DELETE, pygame.K_BACKSPACE):
                self.model.remove_furniture(*self.hover_cell)
                self._mark_dirty()

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
            action = self.panel.handle_click(mx - self.primary_w, my, self.model, self.view_mode,
                                              self.natural_color, self.testing_floor, self.editor_mode,
                                              self.mode == "dev")
            self._handle_panel_action(action)
            return
        if self.testing_floor is not None:
            return
        cell = self._resolve_cell(mx, my)
        if cell is None:
            return
        x, y = cell
        tool = self.panel.current_tool
        if tool == "wall":
            if self.editor_mode == "zone" and self.model.on_border(x, y):
                self._set_message(i18n.t("editor.msg.zone_border_fixed"), True)
                return
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
            scope = self.panel.current_door_scope
            if self.model.click_door(x, y, kind=self.panel.current_door_kind, scope=scope):
                self._mark_dirty()
            else:
                side = self.model.side_of_border_cell(x, y)
                if side is not None and scope == "link" and side in self.model.doors:
                    self._set_message(i18n.t("editor.msg.door_side_taken"), True)
                else:
                    self._set_message(i18n.t("editor.msg.no_door_here"), True)
        elif tool == "furniture":
            ok = self.model.place_furniture(self.panel.current_furniture_kind, x, y)
            if ok:
                self._mark_dirty()
            elif PROP_DEFS[self.panel.current_furniture_kind]["wall_mounted"]:
                self._set_message(i18n.t("editor.msg.wall_only_furniture"), True)
            else:
                self._set_message(i18n.t("editor.msg.cant_place_furniture"), True)

    def _handle_panel_action(self, action):
        if action is None:
            self._mark_dirty()
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
                filetypes=[("Zip files", "*.zip")],
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
        already_testing = self.testing_floor is not None
        spec = S.FLOOR_SPECS[index]
        seed = random.randrange(1 << 30)
        layout = spec.get("layout", "corridor")
        if layout == "yard":
            maze = Maze(w=S.YARD_W, h=S.YARD_H, seed=seed, layout="yard")
            props, panel_prop, exit_prop, monster_cell, doors = populate_yard(maze, spec, random.Random(seed ^ 0x5EED))
        else:
            maze = Maze(seed=seed, wall_bias=spec["wall_bias"], template_floor=spec.get("floor_theme"))
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
        if not already_testing:
            self.natural_color = True
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
        keys = pygame.key.get_pressed()
        move = {
            "forward": keys[pygame.K_w], "back": keys[pygame.K_s],
            "left": keys[pygame.K_a], "right": keys[pygame.K_d],
            "up": keys[pygame.K_SPACE], "down": keys[pygame.K_LCTRL],
        }
        self.camera.update(dt, move, sprint=keys[pygame.K_LSHIFT])

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
        elif self.hover_cell is not None:
            hx, hy = self.hover_cell[0] + PAD, self.hover_cell[1] + PAD
            model = gm.translate(hx + 0.5, hy + 0.5, 0.02) @ gm.scale(0.92, 0.92, 0.03)
            color = (1.0, 0.85, 0.2) if self.panel.current_tool == "furniture" else \
                    (0.3, 0.8, 1.0) if self.panel.current_tool == "door" else (1.0, 0.3, 0.3)
            self.renderer._draw_box(model, color, emissive=1.0, vao=self.renderer.box_vao)

        panel_surf = pygame.Surface((self.panel.width, self.window_h), pygame.SRCALPHA)
        self.panel.draw(panel_surf, self.model, self.view_mode, self.natural_color, self.testing_floor,
                         self.editor_mode, self.mode == "dev")

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

        col_w = min(560, self.window_w // 2 - 60)
        col_x = (cx - col_w - 30, cx + 30)
        col_y = [104, 104]
        col = 0
        for header, lines in self._help_sections():
            needed = 30 + len(lines) * 22 + 14
            if col_y[col] + needed > self.window_h - 20 and col == 0:
                col = 1
            x, y = col_x[col], col_y[col]
            head_surf = self._msg_font.render(header, True, (205, 120, 100))
            hud_surf.blit(head_surf, (x, y))
            y += 28
            for line in lines:
                line_surf = self._help_font.render(line, True, (205, 198, 190))
                hud_surf.blit(line_surf, (x, y))
                y += 22
            col_y[col] = y + 16

    def _draw_message(self, hud_surf):
        if not self.message:
            return
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
