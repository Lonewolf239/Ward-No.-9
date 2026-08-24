import json
import math
import os
import random
import sys
import webbrowser
from pathlib import Path

import moderngl
import numpy as np
import pygame

from game import settings as S
from game import fx
from game import i18n
from game.audio import SoundBank
from game.maze import Maze
from game.mic import MicListener
from game.entities import Player, Monster
from game.props import populate_level, populate_yard, populate_debug, line_blocked_by_cover, make_prop
from game.renderer3d import Renderer3D, FOV_DEGREES


def _default_settings_path():
    app_name = "ward9"
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA")
        base = Path(base) if base else Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else Path.home() / ".config"
    return base / app_name / "settings.json"


SETTINGS_PATH = _default_settings_path()
_LEGACY_SETTINGS_PATH = Path.home() / ".priyut9_settings.json"


PICKUP_LABEL_KEYS = {"battery": "pickup.battery", "fuse": "pickup.fuse", "valve_key": "pickup.valve_key",
                      "sanity_pill": "pickup.sanity_pill"}
PORTAL_LABEL_KEYS = {0: "portal.floor0", 1: "portal.floor1", 2: "portal.floor2", "hub": "portal.hub"}
DEBUG_HUD_OPTIONS = ("fps", "coords", "monster", "seed", "scares")
MENU_ONLY_DEBUG_HUD_OPTIONS = ("coords", "monster", "seed", "scares")
SETTINGS_TABS = ("graphics", "sound", "controls", "language", "debug")
FPS_OPTIONS = (30, 60, 75, 120, 144, 0)
ICON_LINKS = (
    ("web", "http://31.58.179.104"),
    ("tg", "https://t.me/an1onime"),
    ("gh", "https://github.com/Lonewolf239"),
)
FEEDBACK_URL = "https://t.me/ward9_feedback_bot"
CONTRIBUTORS = (
    ("music", "Darsin", "https://t.me/DARSINrock"),
)
FLOOR_THEME_SURFACE = {"upper": "tile", "basement": "stone", "yard": "grass"}
SLIDER_SPECS = {
    "master_volume": (0.0, 1.0),
    "sfx_volume": (0.0, 1.0),
    "music_volume": (0.0, 1.0),
    "mouse_sensitivity": (0.4, 2.2),
    "mouse_sensitivity_y": (0.4, 2.2),
    "view_distance": (0.6, 1.6),
    "mic_sensitivity": (1.0, 20.0),
}
STEPPED_SLIDERS = {
    "fps_limit": FPS_OPTIONS,
    "quality_preset": S.QUALITY_PRESET_ORDER,
}

_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
FONT_PATH = os.path.join(_ASSETS_DIR, "font.ttf")
NOTE_FONT_PATH = os.path.join(_ASSETS_DIR, "note_font.ttf")


class _DemoMonster:
    def __init__(self, start, target, has_locker, speed=1.1):
        self.start = start
        self.target = target
        self.has_locker = has_locker
        self.speed = speed
        self.x, self.y = start
        self.facing = math.atan2(target[1] - start[1], target[0] - start[0])
        self.walk_phase = 0.0
        self.walk_amp = 0.0
        self.alert_level = 0.0
        self.checking_timer = 0.0
        self.phase = "approach"
        self.phase_t = 0.0

    def _travel_time(self):
        dist = math.hypot(self.target[0] - self.start[0], self.target[1] - self.start[1])
        return max(0.6, dist / self.speed)

    def _lerp_pos(self, a, b, t):
        self.x = a[0] + (b[0] - a[0]) * t
        self.y = a[1] + (b[1] - a[1]) * t
        if t < 1.0:
            self.facing = math.atan2(b[1] - a[1], b[0] - a[0])

    def update(self, dt):
        if self.phase == "approach":
            self.phase_t += dt
            t = min(1.0, self.phase_t / self._travel_time())
            self._lerp_pos(self.start, self.target, t)
            self.walk_amp += (1.0 - self.walk_amp) * min(1.0, dt * 8.0)
            self.walk_phase += dt * self.speed * 3.4
            if t >= 1.0:
                if self.has_locker:
                    self.phase, self.phase_t, self.checking_timer = "check", 0.0, S.MONSTER_LOCKER_CHECK_SECONDS
                else:
                    self.phase, self.phase_t = "pause", 0.0
        elif self.phase == "check":
            self.walk_amp += (0.0 - self.walk_amp) * min(1.0, dt * 8.0)
            self.checking_timer = max(0.0, self.checking_timer - dt)
            if self.checking_timer <= 0.0:
                self.phase, self.phase_t = "return", 0.0
        elif self.phase == "pause":
            self.phase_t += dt
            self.walk_amp += (0.0 - self.walk_amp) * min(1.0, dt * 8.0)
            if self.phase_t >= S.MONSTER_LOCKER_CHECK_SECONDS:
                self.phase, self.phase_t = "return", 0.0
        elif self.phase == "return":
            self.phase_t += dt
            t = min(1.0, self.phase_t / self._travel_time())
            self._lerp_pos(self.target, self.start, t)
            self.walk_amp += (1.0 - self.walk_amp) * min(1.0, dt * 8.0)
            self.walk_phase += dt * self.speed * 3.4
            if t >= 1.0:
                self.phase, self.phase_t = "wait", 0.0
        elif self.phase == "wait":
            self.phase_t += dt
            self.walk_amp += (0.0 - self.walk_amp) * min(1.0, dt * 8.0)
            if self.phase_t >= 1.2:
                self.phase, self.phase_t = "approach", 0.0


class App:
    def __init__(self):
        pygame.mixer.pre_init(44100, -16, 2, 512)
        pygame.init()
        pygame.display.set_caption(S.TITLE)

        self.settings = {
            "fullscreen": False,
            "fps_limit": S.FPS,
            "mouse_sensitivity": 1.0,
            "mouse_sensitivity_y": 1.0,
            "master_volume": 0.9,
            "sfx_volume": 1.0,
            "music_volume": 0.6,
            "mic_enabled": False,
            "mic_device": None,
            "mic_sensitivity": S.MIC_LEVEL_SCALE,
            "view_distance": 1.0,
            "quality_preset": "medium",
            "upscale_smoothing": False,
            "vsync": True,
            "language": "en",
            "debug_hud_fps": False,
            "debug_hud_coords": False,
            "debug_hud_monster": False,
            "debug_hud_seed": False,
            "debug_hud_scares": False,
            "warning_seen": False,
            "bindings": dict(S.DEFAULT_BINDINGS),
            "last_seen_version": S.VERSION,
            "pending_changelog": "",
        }
        self._load_settings()
        i18n.set_language(self.settings["language"])

        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 3)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_CORE)
        pygame.display.gl_set_attribute(pygame.GL_DEPTH_SIZE, 24)
        pygame.display.gl_set_attribute(pygame.GL_DOUBLEBUFFER, 1)
        self.screen = pygame.display.set_mode(
            (S.SCREEN_W, S.SCREEN_H), pygame.OPENGL | pygame.DOUBLEBUF,
            vsync=1 if self.settings["vsync"] else 0,
        )
        self.clock = pygame.time.Clock()
        self.window_size = (S.SCREEN_W, S.SCREEN_H)
        self.awaiting_bind = None
        self.controls_msg = None
        self.controls_msg_timer = 0.0
        self.dragging_slider = None
        self.open_combo = None
        if self.settings["fullscreen"]:
            pygame.display.toggle_fullscreen()
        self.settings_return = "menu"
        self.settings_page = SETTINGS_TABS[0]

        self.ctx = moderngl.create_context()
        self.renderer = Renderer3D(self.ctx)
        self._apply_quality_preset(self.settings["quality_preset"])
        self.renderer.set_upscale_smoothing(self.settings["upscale_smoothing"])
        self.hud_surf = pygame.Surface((S.SCREEN_W, S.SCREEN_H), pygame.SRCALPHA)

        self.sounds = SoundBank()
        self.sounds.set_master_volume(self.settings["master_volume"])
        self.sounds.set_sfx_volume(self.settings["sfx_volume"])
        self.sounds.set_music_volume(self.settings["music_volume"])

        self.mic = MicListener()
        self.mic_vu_level = 0.0
        self._apply_mic_setting()

        self.font_title = pygame.font.Font(FONT_PATH, 72)
        self.font_lg = pygame.font.Font(FONT_PATH, 42)
        self.font_md = pygame.font.Font(FONT_PATH, 24)
        self.font_sm = pygame.font.Font(FONT_PATH, 18)
        self.font_note = pygame.font.Font(NOTE_FONT_PATH, 30)

        self.static_overlay = self._make_static_overlay()
        self.hide_vignette = self._make_hide_vignette()
        self.sanity_vignette = self._make_sanity_vignette()
        self.menu_gradient = self._make_menu_gradient()
        self.pause_gradient = self._make_menu_gradient(max_alpha=125)

        self.running = True
        self._next_mode = None
        from game.updater import UpdateChecker
        self._updater = UpdateChecker()
        self._updater.start_check()
        self._update_prompt_seen = False
        self._update_apply_started = False
        self._pending_changelog = self.settings.get("pending_changelog", "")
        self._changelog_scroll_px = 0
        self._changelog_max_scroll = 0
        self._changelog_close_rect = pygame.Rect(0, 0, 0, 0)
        if self.settings.get("last_seen_version") != S.VERSION:
            self._show_changelog = True
            self._changelog_scroll_px = 0
            self.settings["last_seen_version"] = S.VERSION
            self.settings["pending_changelog"] = ""
            self._save_settings()
            from game.updater import cleanup_backup
            cleanup_backup()
        else:
            self._show_changelog = False
        self.new_game()
        if not self.settings.get("warning_seen"):
            self.state = "warning"
        if not self.sounds.ch_ambient.get_busy():
            self.sounds.start_ambient()

    def _load_settings(self):
        path = SETTINGS_PATH if SETTINGS_PATH.exists() else _LEGACY_SETTINGS_PATH
        try:
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            for k in self.settings:
                if k not in saved:
                    continue
                if k == "bindings":
                    self.settings["bindings"].update(saved["bindings"])
                else:
                    self.settings[k] = saved[k]
        except Exception:
            pass

    def _save_settings(self):
        try:
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(self.settings, f)
        except Exception:
            pass

    def _apply_mic_setting(self):
        self._mic_device_missing = False
        if self.settings.get("mic_enabled") and self.mic.available:
            preferred = self.settings.get("mic_device")
            if preferred is not None and not isinstance(preferred, str):
                preferred = None
                self.settings["mic_device"] = None
            self.mic.refresh_devices()
            self.mic.start(preferred)
            if not self.mic.active and preferred is not None:
                self._mic_device_missing = True
                self.mic.start(None)
        else:
            self.mic.stop()

    def _update_mic_level(self, dt):
        if self.settings.get("mic_enabled") and self.mic.available and self.mic.active:
            raw = min(1.0, self.mic.get_level() * self.settings.get("mic_sensitivity", S.MIC_LEVEL_SCALE))
            gated = raw if raw > S.MIC_NOISE_GATE else 0.0
            rate = S.MIC_VU_ATTACK_RATE if gated > self.mic_vu_level else S.MIC_VU_RELEASE_RATE
            self.mic_vu_level += (gated - self.mic_vu_level) * min(1.0, dt * rate)
            return gated
        self.mic_vu_level = 0.0
        return 0.0

    def _make_static_overlay(self):
        surf = pygame.Surface((256, 256), pygame.SRCALPHA)
        for y in range(0, 256, 2):
            a = random.randint(3, 13)
            pygame.draw.line(surf, (255, 255, 255, a), (0, y), (256, y))
        return surf

    def _make_hide_vignette(self):
        w, h = S.SCREEN_W, S.SCREEN_H
        yy, xx = np.mgrid[0:h, 0:w]
        cx, cy = w / 2.0, h / 2.0
        nx = (xx - cx) / cx
        ny = (yy - cy) / cy
        dist = np.sqrt(nx * nx + ny * ny)
        t = np.clip((dist - 0.45) / (1.3 - 0.45), 0.0, 1.0)
        alpha = (t * t * (3.0 - 2.0 * t) * 190.0).astype(np.uint8)
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        alpha_view = pygame.surfarray.pixels_alpha(surf)
        alpha_view[:, :] = alpha.T
        del alpha_view
        return surf

    def _make_sanity_vignette(self):
        w, h = S.SCREEN_W, S.SCREEN_H
        yy, xx = np.mgrid[0:h, 0:w]
        cx, cy = w / 2.0, h / 2.0
        nx = (xx - cx) / cx
        ny = (yy - cy) / cy
        dist = np.sqrt(nx * nx + ny * ny)
        t = np.clip((dist - 0.45) / (1.3 - 0.45), 0.0, 1.0)
        alpha = (t * t * (3.0 - 2.0 * t) * 150.0).astype(np.uint8)
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        surf.fill((120, 0, 0, 255))
        alpha_view = pygame.surfarray.pixels_alpha(surf)
        alpha_view[:, :] = alpha.T
        del alpha_view
        return surf

    def _make_menu_gradient(self, max_alpha=232):
        w, h = S.SCREEN_W, S.SCREEN_H
        xx = np.arange(w, dtype=np.float32)
        t = np.clip((xx - 820.0) / max(1.0, w - 1 - 820.0), 0.0, 1.0)
        col = (1.0 - t)
        alpha_col = (col * col * (3.0 - 2.0 * col) * max_alpha).astype(np.uint8)
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        alpha_view = pygame.surfarray.pixels_alpha(surf)
        alpha_view[:, :] = alpha_col[:, None]
        del alpha_view
        pixel_view = pygame.surfarray.pixels3d(surf)
        pixel_view[:, :, 0] = 3
        pixel_view[:, :, 1] = 2
        pixel_view[:, :, 2] = 4
        del pixel_view
        return surf

    def new_game(self):
        self.sounds.stop_all_threat_audio()
        self.player = Player(0.0, 0.0)
        self.debug_demo_monsters = []
        self._load_floor(0)

        self.elapsed = 0.0
        self.anim_t = 0.0
        self.dread = 0.0
        self._dread_time_frac = 0.0
        self._dread_progress_frac = 0.0
        self._scare_seconds_to_fill = 14.0
        self._scare_lit = False
        self.note_text = None
        self.note_timer = 0.0
        self.hint_text = None
        self.hint_timer = 0.0
        self.floor_banner = self._spec_t("title")
        self.floor_banner_timer = 4.0
        self.scare_progress = 0.0
        self.scare_target = random.uniform(0.85, 1.15)
        self.scare_source = None
        self.hallu_progress = 0.0
        self.hallu_target = random.uniform(0.85, 1.15)
        self.hallu_cooldown = 0.0
        self.hallu_active = []
        self.sanity_boost_timer = 0.0
        self.fx_shake = fx.ScreenShake()
        self.catch_timer = 0.0
        self.transition_timer = 0.0
        self.hide_transition = None
        self.hide_vignette_t = 0.0
        self._battery_warned = False
        self.stats = {"notes": 0, "batteries": 0, "scares": 0}
        self.scare_flash_timer = 0.0
        self.interact_feedback_timer = 0.0
        self.install_hold_target = None
        self.install_hold_t = 0.0
        self.peek_hold_target = None
        self.peek_hold_t = 0.0
        self.is_peeking = False
        self.peek_t = 0.0
        self.peek_door = None
        self.confirm_return = "menu"
        self.state = "menu"

    def _load_floor(self, index):
        spec = S.FLOOR_SPECS[index]
        self.floor_i = index
        self.spec = spec
        self._floor_music_pending = True
        seed = random.randrange(1 << 30)
        self.floor_seed = seed
        layout = spec.get("layout", "corridor")
        if layout == "yard":
            self.maze = Maze(w=S.YARD_W, h=S.YARD_H, seed=seed, layout="yard")
        else:
            self.maze = Maze(seed=seed, wall_bias=spec["wall_bias"], template_floor=spec.get("floor_theme"))
        rng = random.Random(seed ^ 0x5EED)
        if layout == "yard":
            self.props, self.panel_prop, self.exit_prop, monster_cell, self.doors = populate_yard(self.maze, spec, rng)
        else:
            self.props, self.panel_prop, self.exit_prop, monster_cell, self.doors = populate_level(self.maze, spec, rng)
        self.props.sort(key=lambda p: (p.kind, p.texture or ""))
        self.renderer.build_level(self.maze, theme=spec.get("floor_theme", "upper"))

        sx, sy = self.maze.start
        self.player.x, self.player.y = sx, sy
        self.player.angle = random.uniform(0, math.tau)
        self.player.pitch = 0.0
        self.player.carried = 0
        self.player.is_hiding = False
        self.player.hidden_in = None

        monster_blocked = {
            (int(p.x), int(p.y)) for p in self.props
            if p.solid and (not p.wall_mounted or p.kind == "locker")
        }
        lockers = [p for p in self.props if p.kind == "locker"]
        self.monster = Monster(
            monster_cell[0] + 0.5, monster_cell[1] + 0.5, self.maze,
            rng=random.Random(seed ^ 0xB0B0),
            speed_mult=spec["speed_mult"], vision_mult=spec["vision_mult"],
            blocked_cells=monster_blocked,
            lockers=lockers,
            doors=self.doors,
            blocked_prop_candidates=[p for p in self.props if not p.wall_mounted or p.kind == "locker"],
            dead_end_lockers=self.maze.dead_end_lockers(lockers),
        )
        self._door_break_sfx_timer = 0.0
        self.floor_elapsed = 0.0
        self.floor_banner = self._spec_t("title")
        self.floor_banner_timer = 4.0
        self.hint_text = self._spec_t("intro")
        self.hint_timer = 6.0

    def _start_debug_level(self):
        self.player = Player(0.0, 0.0)
        seed = 424242
        self.floor_seed = seed
        self.floor_i = 0
        self.spec = dict(
            key="debug", title="debug.title", collectible="fuse",
            collectible_label="debug.collectible_label", panel_label="debug.panel_label", panel_room=None,
            exit_label="debug.exit_label", exit_room=None, n_collectible=9999,
            wall_bias=None, room_kinds=None, floor_theme="yard",
            speed_mult=1.0, vision_mult=1.0, grace=999999.0,
            fog_color=(10, 14, 9), fog_dist=14.0, ambient_level=0.24,
            moon_strength=0.25, intro=None, descend_text=None,
            no_threat=True,
        )
        self.maze = Maze(w=S.DEBUG_W, h=S.DEBUG_H, seed=seed, layout="debug")
        rng = random.Random(seed)
        self.props, self.panel_prop, self.exit_prop, monster_cell, self.doors = populate_debug(self.maze, rng)
        self.props.sort(key=lambda p: (p.kind, p.texture or ""))
        self.renderer.build_level(self.maze, theme=self.spec["floor_theme"])

        sx, sy = self.maze.start
        self.player.x, self.player.y = sx, sy
        self.player.angle = 0.0
        self.player.pitch = 0.0
        self.player.flashlight_on = True
        self.player.battery = 100.0
        self.player.sanity = S.SANITY_MAX
        self.player.carried = 0
        self.player.is_hiding = False
        self.player.hidden_in = None

        debug_lockers = [p for p in self.props if p.kind == "locker"]
        self.monster = Monster(
            monster_cell[0] + 0.5, monster_cell[1] + 0.5, self.maze,
            rng=random.Random(1), speed_mult=0.0, vision_mult=0.0,
            blocked_cells=set(), lockers=debug_lockers, doors=self.doors,
            blocked_prop_candidates=[p for p in self.props if not p.wall_mounted or p.kind == "locker"],
            dead_end_lockers=self.maze.dead_end_lockers(debug_lockers),
        )
        self.debug_demo_monsters = [
            _DemoMonster(start, target, has_locker)
            for start, target, has_locker in getattr(self.maze, "demo_monster_spots", [])
        ]
        self._door_break_sfx_timer = 0.0
        self.floor_elapsed = 0.0
        self.elapsed = 0.0
        self.dread = 0.0
        self.stats = {"notes": 0, "batteries": 0, "scares": 0}
        self.install_hold_target = None
        self.install_hold_t = 0.0
        self.peek_hold_target = None
        self.peek_hold_t = 0.0
        self.is_peeking = False
        self.peek_t = 0.0
        self.peek_door = None
        self.floor_banner = self._spec_t("title")
        self.floor_banner_timer = 4.0
        self.hint_text = i18n.t("debug.hint")
        self.hint_timer = 6.0
        self._begin_playing()

    def _begin_playing(self):
        self.state = "playing"
        pygame.mouse.set_visible(False)
        pygame.event.set_grab(True)
        pygame.mouse.get_rel()
        if not self.sounds.ch_ambient.get_busy():
            self.sounds.start_ambient()

    def _release_mouse(self):
        pygame.mouse.set_visible(True)
        pygame.event.set_grab(False)

    def _quit(self):
        self.running = False

    def _open_room_editor(self):
        self._next_mode = "editor"
        self.running = False

    def _ask_quit(self, return_state):
        self.confirm_return = return_state
        self.state = "confirm_quit"

    def _cancel_quit(self):
        self.state = self.confirm_return

    def _draw_confirm_quit(self):
        overlay = pygame.Surface((S.SCREEN_W, S.SCREEN_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 240))
        self.hud_surf.blit(overlay, (0, 0))
        if self.confirm_return != "menu":
            self._blit_static()
        cx, cy = S.SCREEN_W // 2, S.SCREEN_H // 2
        title = i18n.t("ui.confirm_quit_title")
        panel_w = max(520, self.font_lg.size(title)[0] + 80)
        self._draw_panel(pygame.Rect(cx - panel_w // 2, cy - 110, panel_w, 220))
        self._text(self.font_lg, title, S.COL_TEXT, center=(cx, cy - 40))
        for btn in self._confirm_quit_buttons():
            self._draw_button(btn)

    def _confirm_quit_buttons(self):
        cx, cy = S.SCREEN_W // 2, S.SCREEN_H // 2
        w, h, gap = 210, 54, 20
        y0 = cy + 10
        return [
            self._button((cx - w - gap // 2, y0, w, h), i18n.t("ui.yes_quit"), self._quit),
            self._button((cx + gap // 2, y0, w, h), i18n.t("ui.no"), self._cancel_quit),
        ]

    def _draw_changelog(self):
        overlay = pygame.Surface((S.SCREEN_W, S.SCREEN_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 255))
        self.hud_surf.blit(overlay, (0, 0))
        cx, cy = S.SCREEN_W // 2, S.SCREEN_H // 2
        title = i18n.t("update.changelog_title", version=S.VERSION)
        body = self._pending_changelog.strip() or i18n.t("update.changelog_empty")
        wrap_w = min(760, S.SCREEN_W - 160)
        line_h = 24
        lines = []
        for raw_line in body.split("\n"):
            wrapped = self._wrap_text(raw_line, self.font_sm, wrap_w) if raw_line.strip() else [""]
            lines.extend(wrapped)

        header_h, footer_h = 78, 42
        max_panel_h = min(560, S.SCREEN_H - 100)
        content_h = len(lines) * line_h
        panel_h = min(max_panel_h, header_h + content_h + footer_h)
        panel_w = wrap_w + 80
        rect = pygame.Rect(cx - panel_w // 2, cy - panel_h // 2, panel_w, panel_h)
        self._draw_panel(rect)
        self._text(self.font_lg, title, S.COL_TEXT, center=(cx, rect.top + 40))

        viewport = pygame.Rect(rect.left + 40, rect.top + header_h,
                                panel_w - 80, panel_h - header_h - footer_h)
        self._changelog_max_scroll = max(0, content_h - viewport.h)
        self._changelog_scroll_px = max(0, min(self._changelog_max_scroll, self._changelog_scroll_px))

        prev_clip = self.hud_surf.get_clip()
        self.hud_surf.set_clip(viewport)
        y = viewport.top - self._changelog_scroll_px
        for line in lines:
            if y + line_h >= viewport.top and y <= viewport.bottom:
                self._text(self.font_sm, line, S.COL_UI_DIM, topleft=(viewport.left, y), shadow=True)
            y += line_h
        self.hud_surf.set_clip(prev_clip)

        if self._changelog_max_scroll > 0:
            track = pygame.Rect(viewport.right + 14, viewport.top, 6, viewport.h)
            pygame.draw.rect(self.hud_surf, (28, 26, 24), track, border_radius=3)
            pygame.draw.rect(self.hud_surf, (70, 64, 58), track, width=1, border_radius=3)
            thumb_h = max(28, int(viewport.h * viewport.h / content_h))
            thumb_y = viewport.top + int(
                (viewport.h - thumb_h) * (self._changelog_scroll_px / self._changelog_max_scroll))
            thumb = pygame.Rect(track.x, thumb_y, track.w, thumb_h)
            pygame.draw.rect(self.hud_surf, (110, 96, 62), thumb, border_radius=3)
            pygame.draw.rect(self.hud_surf, (150, 130, 90), thumb, width=1, border_radius=3)

        btn_w, btn_h = 160, 32
        self._changelog_close_rect = pygame.Rect(cx - btn_w // 2, rect.bottom - btn_h - 12, btn_w, btn_h)
        self._draw_button(self._button(self._changelog_close_rect, i18n.t("update.changelog_close_btn"),
                                        self._close_changelog))

    def _close_changelog(self):
        self._show_changelog = False
        self.sounds.play_ui()

    def _in_debug_preview(self):
        return bool(self.spec.get("no_threat")) and self.spec.get("key") != "debug"

    def _to_menu(self):
        if self._in_debug_preview():
            self._start_debug_level()
        else:
            self.new_game()

    def _open_settings(self, return_state):
        self.settings_return = return_state
        self.state = "settings"
        self.settings_page = SETTINGS_TABS[0]
        self.open_combo = None
        self._release_mouse()

    def _close_settings(self):
        self.state = self.settings_return
        self.dragging_slider = None
        self.awaiting_bind = None
        self.open_combo = None

    def _toggle_fullscreen(self):
        self.settings["fullscreen"] = not self.settings["fullscreen"]
        pygame.display.toggle_fullscreen()
        self._save_settings()

    def _toggle_mic(self):
        if not self.mic.available:
            self.sounds.play_denied()
            return
        self.settings["mic_enabled"] = not self.settings["mic_enabled"]
        self._apply_mic_setting()
        self._save_settings()

    def _mic_device_options(self):
        devices = self.mic.list_devices()
        labels = [i18n.t("settings.mic_device_default")] + [label for _, label in devices]
        device_ids = [None] + [name for name, _ in devices]
        current = self.settings.get("mic_device")
        selected = device_ids.index(current) if current in device_ids else 0
        return labels, selected, device_ids

    def _select_mic_device(self, device_id):
        self.settings["mic_device"] = device_id
        self._save_settings()
        self.sounds.play_ui()
        if self.mic.active:
            self.mic.stop()
            self._apply_mic_setting()

    def _select_language(self, code):
        i18n.set_language(code)
        self.settings["language"] = code
        self._save_settings()
        self.sounds.play_ui()

    def _sync_window_size(self):
        try:
            size = pygame.display.get_window_size()
        except AttributeError:
            surf = pygame.display.get_surface()
            size = surf.get_size() if surf else self.window_size
        if size[0] > 0 and size[1] > 0:
            self.window_size = size

    def _logical_mouse_pos(self, real_pos=None):
        rx, ry = real_pos if real_pos is not None else pygame.mouse.get_pos()
        ww, wh = self.window_size
        if ww <= 0 or wh <= 0:
            return rx, ry
        return rx * S.SCREEN_W / ww, ry * S.SCREEN_H / wh

    def _hide_exit_target(self, locker):
        p = self.player
        for radius in (0.85, 0.7, 0.55, 0.42, 1.05, 1.3, 1.6):
            for offset in (0.0, 0.35, -0.35, 0.7, -0.7, 1.05, -1.05, 1.4, -1.4, math.pi):
                ang = locker.facing + offset
                x = locker.x + math.cos(ang) * radius
                y = locker.y + math.sin(ang) * radius
                if self.maze.is_wall(x, y):
                    continue
                if not p._collides(self.maze, self.props + self.doors, x, y, S.PLAYER_RADIUS):
                    return x, y, ang
        return (locker.x + math.cos(locker.facing) * 0.85, locker.y + math.sin(locker.facing) * 0.85,
                locker.facing)

    def _start_hide_transition(self, locker, entering):
        p = self.player
        if entering:
            p.locker_use_count += 1
            end_x = locker.x - math.cos(locker.facing) * locker.hd * 0.35
            end_y = locker.y - math.sin(locker.facing) * locker.hd * 0.35
            end_angle = locker.facing
            end_pitch = -0.05
        else:
            end_x, end_y, end_angle = self._hide_exit_target(locker)
            end_pitch = 0.0
        self.hide_transition = {
            "t": 0.0, "duration": 0.5, "entering": entering, "locker": locker,
            "sx": p.x, "sy": p.y, "sa": p.angle, "sp": p.pitch,
            "ex": end_x, "ey": end_y, "ea": end_angle, "ep": end_pitch,
        }
        p.is_hiding = False
        p.moved_this_frame = False
        self.fx_shake.add(0.18)
        self.sounds.play_locker()

    def _update_hide_transition(self, dt):
        ht = self.hide_transition
        ht["t"] += dt
        frac = min(1.0, ht["t"] / ht["duration"])
        if ht["entering"]:
            ease = 1.0 - (1.0 - frac) ** 3
            dip = math.sin(frac * math.pi) * 0.08
        else:
            ease = frac * frac * (3 - 2 * frac)
            dip = 0.0
        p = self.player
        p.x = ht["sx"] + (ht["ex"] - ht["sx"]) * ease
        p.y = ht["sy"] + (ht["ey"] - ht["sy"]) * ease
        da = (ht["ea"] - ht["sa"] + math.pi) % (2 * math.pi) - math.pi
        p.angle = (ht["sa"] + da * ease) % math.tau
        p.pitch = ht["sp"] + (ht["ep"] - ht["sp"]) * ease + dip
        p.moved_this_frame = False
        p.noise_radius = 0.0
        if frac >= 1.0:
            if ht["entering"]:
                p.is_hiding = True
                p.hidden_in = ht["locker"]
            else:
                p.is_hiding = False
                p.hidden_in = None
            self.hide_transition = None

    _BUTTON_THEMES = {
        "red": {
            "bg_hover": (66, 22, 22, 225), "bg_idle": (15, 13, 13, 195),
            "border_hover": (215, 80, 80), "border_idle": (90, 84, 79),
            "accent_hover": (225, 70, 65), "accent_idle": (110, 46, 44),
        },
        "gold": {
            "bg_hover": (58, 42, 14, 225), "bg_idle": (15, 13, 13, 195),
            "border_hover": (222, 178, 92), "border_idle": (110, 96, 62),
            "accent_hover": (230, 180, 80), "accent_idle": (140, 108, 55),
        },
    }

    def _button(self, rect, label, action, enabled=True, theme="red"):
        return {"rect": pygame.Rect(rect), "label": label, "action": action, "enabled": enabled, "theme": theme}

    def _button_shape(self, rect, cut=14):
        x, y, w, h = rect
        return [(x, y), (x + w - cut, y), (x + w, y + cut), (x + w, y + h), (x, y + h)]

    def _draw_button_frame(self, btn, active=False):
        rect = btn["rect"]
        hovered = active or (btn["enabled"] and rect.collidepoint(self._logical_mouse_pos()))
        t = self._BUTTON_THEMES[btn.get("theme", "red")]
        bg = t["bg_hover"] if hovered else t["bg_idle"]
        shape = self._button_shape(rect)
        surf = pygame.Surface(rect.size, pygame.SRCALPHA)
        local_shape = [(px - rect.x, py - rect.y) for px, py in shape]
        pygame.draw.polygon(surf, bg, local_shape)
        self.hud_surf.blit(surf, rect.topleft)
        border = t["border_hover"] if hovered else t["border_idle"]
        pygame.draw.polygon(self.hud_surf, border, shape, width=2)
        accent = t["accent_hover"] if hovered else t["accent_idle"]
        pygame.draw.rect(self.hud_surf, accent, (rect.x, rect.y, 4, rect.h))
        col = (235, 225, 215) if btn["enabled"] else (110, 100, 95)
        text_x = rect.x + 18
        return rect, hovered, border, col, text_x

    def _ellipsize(self, text, font, max_w):
        if max_w <= 0 or font.size(text)[0] <= max_w:
            return text
        ell = "…"
        if font.size(ell)[0] > max_w:
            return ell
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if font.size(text[:mid] + ell)[0] <= max_w:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo] + ell

    def _draw_button(self, btn, active=False):
        rect, hovered, border, col, text_x = self._draw_button_frame(btn, active=active)
        label = self._ellipsize(btn["label"], self.font_md, rect.right - text_x - 14)
        self._text(self.font_md, label, col,
                   topleft=(text_x, rect.centery - self.font_md.get_height() // 2), shadow=False)

    def _draw_text_link(self, btn):
        rect = btn["rect"]
        hovered = rect.collidepoint(self._logical_mouse_pos())
        col = (215, 90, 85) if hovered else (200, 190, 180)
        r = self._text(self.font_sm, btn["label"], col, topleft=rect.topleft, shadow=False)
        if hovered:
            y = r.bottom - 1
            pygame.draw.line(self.hud_surf, col, (r.left, y), (r.right, y), 1)

    def _draw_toggle(self, btn, is_on):
        rect, hovered, border, col, text_x = self._draw_button_frame(btn)
        sw_w, sw_h = 54, 26
        track = pygame.Rect(rect.right - sw_w - 16, rect.centery - sw_h // 2, sw_w, sw_h)
        label_max_w = track.x - 12 - text_x
        font = self.font_md
        if font.size(btn["label"])[0] > label_max_w:
            font = self.font_sm
        label = self._ellipsize(btn["label"], font, label_max_w)
        self._text(font, label, col, topleft=(text_x, rect.centery - font.get_height() // 2), shadow=False)
        on_color = (185, 70, 65) if btn["enabled"] else (95, 58, 55)
        off_color = (46, 42, 40)
        pygame.draw.rect(self.hud_surf, on_color if is_on else off_color, track, border_radius=sw_h // 2)
        pygame.draw.rect(self.hud_surf, border, track, width=1, border_radius=sw_h // 2)
        knob_r = sw_h // 2 - 3
        knob_x = track.right - knob_r - 3 if is_on else track.x + knob_r + 3
        pygame.draw.circle(self.hud_surf, (235, 228, 220), (knob_x, track.centery), knob_r)

    def _draw_panel(self, rect, fill=(10, 9, 8, 165), border=(95, 88, 82), accent=(150, 55, 50),
                     corner=20, target=None):
        target = target if target is not None else self.hud_surf
        surf = pygame.Surface(rect.size, pygame.SRCALPHA)
        surf.fill(fill)
        target.blit(surf, rect.topleft)
        pygame.draw.rect(target, border, rect, width=1)
        c = max(0, min(corner, rect.w // 2, rect.h // 2))
        t = 2
        x0, y0, x1, y1 = rect.left, rect.top, rect.right - 1, rect.bottom - 1
        for hx, hy, vx, vy in (
            (x0, y0, x0, y0),
            (x1 - c + 1, y0, x1 - t + 1, y0),
            (x0, y1 - t + 1, x0, y1 - c + 1),
            (x1 - c + 1, y1 - t + 1, x1 - t + 1, y1 - c + 1),
        ):
            pygame.draw.rect(target, accent, (hx, hy, c, t))
            pygame.draw.rect(target, accent, (vx, vy, t, c))

    def _wrap_text(self, text, font, max_width):
        words = text.replace("\n", " ").split()
        lines = []
        cur = ""
        for word in words:
            trial = f"{cur} {word}".strip()
            if not cur or font.size(trial)[0] <= max_width:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        return lines

    def _controls_hint_lines(self):
        b = self.settings["bindings"]

        def k(action):
            return pygame.key.name(b[action]).upper()

        move_keys = "/".join(k(a) for a in ("forward", "left", "back", "right"))
        return [
            i18n.t("controls.legend_line1", move=move_keys, sprint=k("sprint"), crouch=k("crouch")),
            i18n.t("controls.legend_line2", flashlight=k("flashlight"), interact=k("interact")),
        ]

    def _blit_static(self):
        surf = self.static_overlay
        w, h = surf.get_size()
        off = int(self.anim_t * 18) % h
        y = -off
        while y < S.SCREEN_H:
            x = 0
            while x < S.SCREEN_W:
                self.hud_surf.blit(surf, (x, y))
                x += w
            y += h

    def _handle_button_click(self, buttons, pos):
        for btn in buttons:
            if btn["enabled"] and btn["rect"].collidepoint(pos):
                btn["action"]()
                return True
        return False

    def _draw_slider(self, rect, frac, label, value_text, dragging=False):
        self._text(self.font_sm, label, S.COL_UI_DIM, topleft=(rect.x, rect.y - 22), shadow=False)
        hovered = dragging or rect.collidepoint(self._logical_mouse_pos())
        cut = min(8, rect.h)
        shape = self._button_shape(rect, cut=cut)
        local_shape = [(px - rect.x, py - rect.y) for px, py in shape]

        track = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.polygon(track, (18, 16, 15, 225), local_shape)
        self.hud_surf.blit(track, rect.topleft)

        frac = max(0.0, min(1.0, frac))
        fill_w = int(rect.w * frac)
        if fill_w > 0:
            fill_col = (200, 80, 75) if hovered else (170, 65, 65)
            fill = pygame.Surface(rect.size, pygame.SRCALPHA)
            pygame.draw.polygon(fill, fill_col, local_shape)
            if fill_w < rect.w:
                pygame.draw.rect(fill, (0, 0, 0, 0), (fill_w, 0, rect.w - fill_w, rect.h))
            self.hud_surf.blit(fill, rect.topleft)

        border = (215, 80, 80) if hovered else (95, 88, 82)
        pygame.draw.polygon(self.hud_surf, border, shape, width=2)
        accent = (225, 70, 65) if hovered else (110, 46, 44)
        pygame.draw.rect(self.hud_surf, accent, (rect.x, rect.y, 4, rect.h))

        value_text = self._ellipsize(value_text, self.font_sm, 150)
        self._text(self.font_sm, value_text, S.COL_TEXT, topleft=(rect.right + 16, rect.y - 2), shadow=False)

    def _draw_stepped_slider(self, rect, index, count, label, value_text, dragging=False):
        frac = index / (count - 1) if count > 1 else 0.0
        self._draw_slider(rect, frac, label, value_text, dragging=dragging)
        for i in range(1, count - 1):
            tx = rect.x + rect.w * (i / (count - 1))
            pygame.draw.line(self.hud_surf, (60, 55, 52), (tx, rect.y + 3), (tx, rect.bottom - 3), 1)

    def _combo_option_rects(self, header_rect, n):
        row_h = 38
        return [pygame.Rect(header_rect.x, header_rect.bottom + i * row_h, header_rect.w, row_h)
                for i in range(n)]

    def _draw_combo(self, key, combo):
        rect = combo["rect"]
        enabled = combo.get("enabled", True)
        is_open = enabled and self.open_combo == key
        btn = {"rect": rect, "label": combo["options"][combo["selected_index"]],
               "action": None, "enabled": enabled, "icon": None}
        r, hovered, border, col, text_x = self._draw_button_frame(btn, active=is_open)
        label = self._ellipsize(btn["label"], self.font_md, r.right - text_x - 30)
        self._text(self.font_md, label, col,
                   topleft=(text_x, r.centery - self.font_md.get_height() // 2), shadow=False)
        ax, ay = r.right - 24, r.centery
        tri = [(ax - 6, ay + 3), (ax + 6, ay + 3), (ax, ay - 4)] if is_open else \
              [(ax - 6, ay - 3), (ax + 6, ay - 3), (ax, ay + 4)]
        pygame.draw.polygon(self.hud_surf, col, tri)

        if not is_open:
            return
        options = combo["options"]
        option_rects = self._combo_option_rects(rect, len(options))
        list_rect = pygame.Rect(rect.x, rect.bottom, rect.w, option_rects[-1].bottom - rect.bottom)
        surf = pygame.Surface(list_rect.size, pygame.SRCALPHA)
        surf.fill((12, 11, 10, 250))
        self.hud_surf.blit(surf, list_rect.topleft)
        pygame.draw.rect(self.hud_surf, (150, 60, 55), list_rect, width=2)
        mouse = self._logical_mouse_pos()
        for i, (opt_label, opt_rect) in enumerate(zip(options, option_rects)):
            row_hovered = opt_rect.collidepoint(mouse)
            selected = i == combo["selected_index"]
            if selected or row_hovered:
                hi = pygame.Surface(opt_rect.size, pygame.SRCALPHA)
                hi.fill((70, 24, 22, 235) if selected else (40, 34, 32, 210))
                self.hud_surf.blit(hi, opt_rect.topleft)
            opt_col = (240, 230, 220) if (selected or row_hovered) else (185, 175, 168)
            disp = self._ellipsize(opt_label, self.font_sm, opt_rect.w - 28)
            self._text(self.font_sm, disp, opt_col,
                      topleft=(opt_rect.x + 14, opt_rect.centery - self.font_sm.get_height() // 2), shadow=False)
            if i < len(options) - 1:
                pygame.draw.line(self.hud_surf, (45, 41, 38), (opt_rect.x + 8, opt_rect.bottom),
                                 (opt_rect.right - 8, opt_rect.bottom), 1)

    def _menu_buttons(self, layout=None):
        left, w, _story_lines, _legend_lines, button_y0 = layout or self._menu_layout()
        h, gap = 44, 7
        buttons = [
            self._button((left, button_y0, w, h), i18n.t("menu.play"), self._begin_playing),
            self._button((left, button_y0 + (h + gap), w, h), i18n.t("menu.settings"), lambda: self._open_settings("menu")),
            self._button((left, button_y0 + 2 * (h + gap), w, h), i18n.t("menu.about"), self._open_credits),
            self._button((left, button_y0 + 3 * (h + gap), w, h), i18n.t("menu.room_editor"), self._open_room_editor),
            self._button((left, button_y0 + 4 * (h + gap), w, h), i18n.t("menu.quit"), lambda: self._ask_quit("menu")),
            self._feedback_button(),
        ]
        update_btn = self._update_button()
        if update_btn is not None:
            buttons.append(update_btn)
        return buttons

    def _update_button(self):
        snap = self._updater.snapshot()
        state = snap["state"]
        if state == "available":
            label = i18n.t("update.available_short", version=snap["latest_tag"])
            action = self._accept_update
        else:
            return None
        w, h = 340, 44
        rect = (S.SCREEN_W - w - 20, S.SCREEN_H - h - 92, w, h)
        return self._button(rect, label, action, theme="gold")

    def _update_prompt_buttons(self):
        cx, cy = S.SCREEN_W // 2, S.SCREEN_H // 2
        w, h, gap = 210, 54, 20
        y0 = cy + 10
        return [
            self._button((cx - w - gap // 2, y0, w, h), i18n.t("update.yes_download"), self._accept_update),
            self._button((cx + gap // 2, y0, w, h), i18n.t("ui.no"), self._decline_update),
        ]

    def _accept_update(self):
        self._updater.start_download()
        self.state = "update_downloading"
        self._update_apply_started = False
        self.sounds.play_ui()

    def _decline_update(self):
        self.state = "menu"
        self.sounds.play_ui()

    def _draw_update_prompt(self):
        overlay = pygame.Surface((S.SCREEN_W, S.SCREEN_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 255))
        self.hud_surf.blit(overlay, (0, 0))
        cx, cy = S.SCREEN_W // 2, S.SCREEN_H // 2
        line1 = i18n.t("update.prompt_available", version=self._updater.snapshot()["latest_tag"] or "")
        line2 = i18n.t("update.prompt_question")
        panel_w = max(520, self.font_lg.size(line1)[0] + 80, self.font_lg.size(line2)[0] + 80)
        panel_h = 240
        self._draw_panel(pygame.Rect(cx - panel_w // 2, cy - panel_h // 2, panel_w, panel_h))
        self._text(self.font_lg, line1, S.COL_TEXT, center=(cx, cy - 75))
        self._text(self.font_lg, line2, S.COL_TEXT, center=(cx, cy - 38))
        for btn in self._update_prompt_buttons():
            self._draw_button(btn)

    def _format_size_mb(self, n_bytes):
        return f"{n_bytes / (1024 * 1024):.1f}"

    def _format_eta(self, seconds):
        seconds = max(0, int(seconds))
        m, s = divmod(seconds, 60)
        return f"{m:02d}:{s:02d}"

    def _draw_update_downloading(self):
        overlay = pygame.Surface((S.SCREEN_W, S.SCREEN_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 255))
        self.hud_surf.blit(overlay, (0, 0))
        snap = self._updater.snapshot()
        cx, cy = S.SCREEN_W // 2, S.SCREEN_H // 2
        panel_w, panel_h = 620, 220
        rect = pygame.Rect(cx - panel_w // 2, cy - panel_h // 2, panel_w, panel_h)
        self._draw_panel(rect)
        title = i18n.t("update.downloading_title", version=self._updater.snapshot()["latest_tag"] or "")
        self._text(self.font_lg, title, S.COL_TEXT, center=(cx, rect.top + 42))

        bar_x, bar_y = rect.left + 40, rect.top + 90
        bar_w, bar_h = panel_w - 80, 22
        frac = max(0.0, min(1.0, snap["progress"]))
        pygame.draw.rect(self.hud_surf, (32, 29, 27), (bar_x, bar_y, bar_w, bar_h), border_radius=4)
        pygame.draw.rect(self.hud_surf, (222, 178, 92), (bar_x, bar_y, int(bar_w * frac), bar_h), border_radius=4)
        pygame.draw.rect(self.hud_surf, (110, 96, 62), (bar_x, bar_y, bar_w, bar_h), width=1, border_radius=4)
        pct_text = f"{int(frac * 100)}%"
        self._text(self.font_sm, pct_text, S.COL_TEXT, center=(cx, bar_y + bar_h // 2), shadow=True)

        total = snap["total_bytes"]
        done = snap["downloaded_bytes"]
        if total:
            size_text = i18n.t("update.downloading_size",
                                done=self._format_size_mb(done), total=self._format_size_mb(total))
        else:
            size_text = i18n.t("update.downloading_size_unknown", done=self._format_size_mb(done))
        speed = snap["speed_bps"]
        speed_text = i18n.t("update.downloading_speed", speed=f"{speed / (1024 * 1024):.2f}")
        remaining = (total - done) if total else None
        if remaining is not None and speed > 1024:
            eta_text = i18n.t("update.downloading_eta", eta=self._format_eta(remaining / speed))
        else:
            eta_text = i18n.t("update.downloading_eta_unknown")

        info_y = bar_y + bar_h + 20
        self._text(self.font_sm, size_text, S.COL_UI_DIM, topleft=(bar_x, info_y), shadow=False)
        self._text(self.font_sm, speed_text, S.COL_UI_DIM, center=(cx, info_y + 10), shadow=False)
        eta_w = self.font_sm.size(eta_text)[0]
        self._text(self.font_sm, eta_text, S.COL_UI_DIM, topleft=(bar_x + bar_w - eta_w, info_y), shadow=False)

    def _apply_update_now(self):
        snap = self._updater.snapshot()
        path = snap["downloaded_path"]
        if not path:
            return
        self.settings["pending_changelog"] = snap["release_notes"] or ""
        self._save_settings()
        from game.updater import apply_update_and_restart
        try:
            apply_update_and_restart(path)
        except Exception as e:
            print(f"ward9: failed to apply update from {path}: {e}")

    def _feedback_button(self):
        w, h = 340, 44
        rect = (S.SCREEN_W - w - 20, S.SCREEN_H - h - 40, w, h)
        return self._button(rect, i18n.t("menu.feedback"), lambda: webbrowser.open(FEEDBACK_URL), theme="gold")

    def _open_credits(self):
        self.state = "credits"

    def _credits_content(self):
        left = 90
        content_w = 700
        body_lines = self._wrap_text(i18n.t("about.body"), self.font_sm, content_w)

        rows = []
        y = 70 + self.font_lg.get_height() + 30

        def text_row(text, font, col, step):
            nonlocal y
            rows.append((y, "text", (text, font, col)))
            y += step

        def divider_row():
            nonlocal y
            rows.append((y + 10, "divider", None))
            y += 30

        text_row(i18n.t("about.version", v=S.VERSION), self.font_sm, S.COL_UI_DIM, 30)
        text_row(i18n.t("about.author", a=S.AUTHOR), self.font_md, (215, 200, 190), 34)

        contributor_links = []
        for role_key, name, url in CONTRIBUTORS:
            role_label = i18n.t(f"credit.{role_key}") + ": "
            rows.append((y, "text", (role_label, self.font_sm, S.COL_UI_DIM)))
            name_x = left + self.font_sm.size(role_label)[0]
            name_rect = pygame.Rect(name_x, y, self.font_sm.size(name)[0], self.font_sm.get_height())
            contributor_links.append(self._button(name_rect, name, lambda u=url: webbrowser.open(u)))
            y += 24

        for line in body_lines:
            text_row(line, self.font_sm, S.COL_UI_DIM, 24)
        divider_row()
        text_row(i18n.t("about.links_label"), self.font_md, (215, 200, 190), 30)
        link_row_y = y
        y += 54

        link_w, link_gap = 220, 20
        link_buttons = [
            self._button((left + i * (link_w + link_gap), link_row_y, link_w, 44),
                         i18n.t(f"about.link_{key}"), lambda u=url: webbrowser.open(u))
            for i, (key, url) in enumerate(ICON_LINKS)
        ]
        back_btn = self._button((left, y + 30, 300, 44), i18n.t("ui.back"),
                                lambda: setattr(self, "state", "menu"))
        return rows, left, content_w, link_buttons, contributor_links, back_btn

    def _credits_buttons(self):
        _, _, _, link_buttons, contributor_links, back_btn = self._credits_content()
        return link_buttons + contributor_links + [back_btn]

    def _warning_buttons(self):
        cx = S.SCREEN_W // 2
        w, h = 260, 54
        return [self._button((cx - w // 2, S.SCREEN_H - 130, w, h), i18n.t("ui.understood"), self._dismiss_warning)]

    def _dismiss_warning(self):
        self.state = "menu"
        self.settings["warning_seen"] = True
        self._save_settings()

    def _pause_layout(self):
        left = 90
        button_y0 = 90 + self.font_lg.get_height() + 40
        return left, button_y0

    def _pause_buttons(self):
        left, y0 = self._pause_layout()
        w, h, gap = 360, 54, 12
        return [
            self._button((left, y0, w, h), i18n.t("pause.resume"), self._begin_playing),
            self._button((left, y0 + (h + gap), w, h), i18n.t("menu.settings"), lambda: self._open_settings("paused")),
            self._button((left, y0 + 2 * (h + gap), w, h), i18n.t("pause.to_menu"), self._to_menu),
            self._button((left, y0 + 3 * (h + gap), w, h), i18n.t("pause.quit_game"), lambda: self._ask_quit("paused")),
            self._feedback_button(),
        ]

    def _settings_geom(self):
        left = 90
        top = 70
        header_divider_y = top + self.font_lg.get_height() + 20
        column_top = header_divider_y + 24
        bottom_margin = 50
        sidebar_w = 210
        back_h = 48
        back_rect = pygame.Rect(left, S.SCREEN_H - bottom_margin - back_h, sidebar_w, back_h)
        tabs_area = pygame.Rect(left, column_top, sidebar_w, back_rect.top - 18 - column_top)
        content_x = left + sidebar_w + 50
        content = pygame.Rect(content_x, column_top, S.SCREEN_W - 90 - content_x,
                              (S.SCREEN_H - bottom_margin) - column_top)
        return left, header_divider_y, tabs_area, back_rect, content

    def _settings_tabs_layout(self):
        _, _, tabs_area, _, _ = self._settings_geom()
        h, gap = 48, 12
        tabs = []
        for i, key in enumerate(SETTINGS_TABS):
            rect = pygame.Rect(tabs_area.x, tabs_area.y + i * (h + gap), tabs_area.w, h)
            tabs.append((key, rect))
        return tabs

    def _settings_page_layout(self, page):
        _, _, _, _, content = self._settings_geom()
        w, h, gap, pad = content.w, 44, 10, 10

        if page == "graphics":
            y = content.y + pad
            fs_rect = pygame.Rect(content.x, y, w, h)
            y += h + 12
            aa_rect = pygame.Rect(content.x, y, w, h)
            y += h + 12
            vs_rect = pygame.Rect(content.x, y, w, h)
            y += h + 34
            fps_rect = pygame.Rect(content.x, y, w - 170, 16)
            y += 16 + 34
            dist_rect = pygame.Rect(content.x, y, w - 170, 16)
            y += 16 + 34
            quality_rect = pygame.Rect(content.x, y, w - 170, 16)
            fs_btn = self._button(fs_rect, i18n.t("settings.fullscreen"), self._toggle_fullscreen)
            aa_btn = self._button(aa_rect, i18n.t("settings.upscale_smoothing"), self._toggle_upscale_smoothing)
            vs_btn = self._button(vs_rect, i18n.t("settings.vsync"), self._toggle_vsync)
            return {"sliders": {"fps_limit": fps_rect, "view_distance": dist_rect, "quality_preset": quality_rect},
                    "buttons": [fs_btn, aa_btn, vs_btn]}

        if page == "sound":
            y = content.y + pad
            vol_rect = pygame.Rect(content.x, y, w - 170, 16)
            y += 44
            sfx_rect = pygame.Rect(content.x, y, w - 170, 16)
            y += 44
            music_rect = pygame.Rect(content.x, y, w - 170, 16)
            y += 44
            mic_rect = pygame.Rect(content.x, y, w, h)
            y += h + 34
            device_rect = pygame.Rect(content.x, y, w, h)
            missing_notice_y = y + h + 8
            y += h + 76
            sens_rect = pygame.Rect(content.x, y, w - 170, 16)
            y += 16 + 34
            vu_rect = pygame.Rect(content.x, y, w, 16)
            mic_btn = self._button(mic_rect, i18n.t("settings.mic"), self._toggle_mic,
                                    enabled=self.mic.available)
            labels, selected_idx, device_ids = self._mic_device_options()
            device_combo = {
                "rect": device_rect, "options": labels, "selected_index": selected_idx,
                "on_select": lambda i, ids=device_ids: self._select_mic_device(ids[i]),
                "enabled": self.mic.available,
            }
            return {"sliders": {"master_volume": vol_rect, "sfx_volume": sfx_rect, "music_volume": music_rect,
                                 "mic_sensitivity": sens_rect},
                    "buttons": [mic_btn], "combos": {"mic_device": device_combo}, "vu_rect": vu_rect,
                    "missing_notice_y": missing_notice_y}

        if page == "controls":
            row_h, row_gap = 44, 10
            col_w = (content.w - 20) // 2
            rows_h = 4 * (row_h + row_gap) - row_gap
            y = content.y + pad
            sens_x_rect = pygame.Rect(content.x, y, w - 170, 16)
            y += 44
            sens_y_rect = pygame.Rect(content.x, y, w - 170, 16)
            y += 44
            rows = []
            for i, action in enumerate(S.BINDING_ORDER):
                row_i, col_i = divmod(i, 2)
                rx = content.x + col_i * (col_w + 20)
                ry = y + row_i * (row_h + row_gap)
                rows.append((action, pygame.Rect(rx, ry, col_w, row_h)))
            y += rows_h + 20
            reset_rect = pygame.Rect(content.centerx - 140, y, 280, 44)
            reset_btn = self._button(reset_rect, i18n.t("controls.reset"), self._reset_bindings)
            return {"sliders": {"mouse_sensitivity": sens_x_rect, "mouse_sensitivity_y": sens_y_rect},
                    "buttons": [reset_btn], "rows": rows, "msg_y": reset_rect.bottom + 24}

        if page == "language":
            y = content.y + pad + 26
            lang_rect = pygame.Rect(content.x, y, w, h)
            labels = [i18n.LANGUAGE_NAMES[code] for code in i18n.LANGUAGES]
            current = i18n.get_language()
            selected = i18n.LANGUAGES.index(current) if current in i18n.LANGUAGES else 0
            lang_combo = {
                "rect": lang_rect, "options": labels, "selected_index": selected,
                "on_select": lambda i: self._select_language(i18n.LANGUAGES[i]),
                "enabled": True,
            }
            return {"sliders": {}, "buttons": [], "combos": {"language": lang_combo}}

        if page == "debug":
            y = content.y + pad
            rows = []
            menu_only_ok = self.settings_return == "menu"
            for opt in DEBUG_HUD_OPTIONS:
                enabled = menu_only_ok or opt not in MENU_ONLY_DEBUG_HUD_OPTIONS
                rows.append(self._button((content.x, y, w, h), i18n.t(f"debug_hud.{opt}"),
                                         lambda o=opt: self._toggle_debug_hud(o), enabled=enabled))
                y += h + gap
            return {"sliders": {}, "buttons": rows}

        return {"sliders": {}, "buttons": []}

    def _fps_value_text(self):
        v = self.settings["fps_limit"]
        return i18n.t("settings.fps_unlimited") if v == 0 else str(v)

    def _apply_quality_preset(self, name):
        preset = S.QUALITY_PRESETS.get(name, S.QUALITY_PRESETS["medium"])
        low_w, low_h = preset["low_res"]
        self.renderer.set_resolution(low_w, low_h, preset["snap_res"])
        self.renderer.set_max_shadow_lights(preset["shadow_lights"])

    def _toggle_upscale_smoothing(self):
        self.settings["upscale_smoothing"] = not self.settings["upscale_smoothing"]
        self.renderer.set_upscale_smoothing(self.settings["upscale_smoothing"])
        self._save_settings()
        self.sounds.play_ui()

    def _toggle_vsync(self):
        self.settings["vsync"] = not self.settings["vsync"]
        pygame.display.set_mode(
            (S.SCREEN_W, S.SCREEN_H), pygame.OPENGL | pygame.DOUBLEBUF,
            vsync=1 if self.settings["vsync"] else 0,
        )
        if self.settings["fullscreen"]:
            pygame.display.toggle_fullscreen()
        self._save_settings()
        self.sounds.play_ui()

    def _apply_slider(self, key, rect, mouse_x):
        frac = max(0.0, min(1.0, (mouse_x - rect.x) / rect.w))
        if key in STEPPED_SLIDERS:
            options = STEPPED_SLIDERS[key]
            idx = round(frac * (len(options) - 1)) if len(options) > 1 else 0
            self.settings[key] = options[max(0, min(len(options) - 1, idx))]
            if key == "quality_preset":
                self._apply_quality_preset(self.settings[key])
        else:
            lo, hi = SLIDER_SPECS[key]
            self.settings[key] = lo + frac * (hi - lo)
            if key == "master_volume":
                self.sounds.set_master_volume(self.settings[key])
            elif key == "sfx_volume":
                self.sounds.set_sfx_volume(self.settings[key])
            elif key == "music_volume":
                self.sounds.set_music_volume(self.settings[key])
        self._save_settings()

    def _select_settings_tab(self, key):
        self.settings_page = key
        self.dragging_slider = None
        self.awaiting_bind = None
        self.open_combo = None
        if key == "sound":
            if self.settings.get("mic_enabled") and self._mic_device_missing:
                self.mic.stop()
                self._apply_mic_setting()
            else:
                self.mic.refresh_devices()
        self.sounds.play_ui()

    def _handle_settings_click(self, pos):
        for key, rect in self._settings_tabs_layout():
            if rect.collidepoint(pos):
                self._select_settings_tab(key)
                return
        _, _, _, back_rect, _ = self._settings_geom()
        if back_rect.collidepoint(pos):
            self._close_settings()
            return

        layout = self._settings_page_layout(self.settings_page)
        combos = layout.get("combos", {})

        if self.open_combo is not None:
            combo = combos.get(self.open_combo)
            if combo is not None:
                for i, r in enumerate(self._combo_option_rects(combo["rect"], len(combo["options"]))):
                    if r.collidepoint(pos):
                        combo["on_select"](i)
                        self.open_combo = None
                        return
            self.open_combo = None
            return

        for key, combo in combos.items():
            if combo.get("enabled", True) and combo["rect"].collidepoint(pos):
                if key == "mic_device":
                    self.mic.refresh_devices()
                self.open_combo = key
                self.sounds.play_ui()
                return

        for k, rect in layout["sliders"].items():
            if rect.collidepoint(pos):
                self._apply_slider(k, rect, pos[0])
                self.dragging_slider = k
                return
        if self.settings_page == "controls":
            for action, rect in layout.get("rows", ()):
                if rect.collidepoint(pos):
                    self.awaiting_bind = action
                    self.controls_msg = None
                    return
        self._handle_button_click(layout["buttons"], pos)

    def _resolve_bind(self, key):
        action = self.awaiting_bind
        self.awaiting_bind = None
        if key == pygame.K_ESCAPE:
            return
        conflict = next((a for a, k in self.settings["bindings"].items() if k == key and a != action), None)
        if conflict is not None:
            self.controls_msg = i18n.t("controls.key_taken", label=i18n.t(f"binding.{conflict}"))
            self.controls_msg_timer = 3.0
            self.sounds.play_denied()
            return
        self.settings["bindings"][action] = key
        self._save_settings()
        self.controls_msg = None
        self.sounds.play_ui()

    def _reset_bindings(self):
        self.settings["bindings"] = dict(S.DEFAULT_BINDINGS)
        self._save_settings()
        self.controls_msg = i18n.t("controls.reset_done")
        self.controls_msg_timer = 2.5
        self.sounds.play_ui()

    def _toggle_debug_hud(self, option):
        key = f"debug_hud_{option}"
        self.settings[key] = not self.settings[key]
        self._save_settings()
        self.sounds.play_ui()

    def find_interactable(self):
        p = self.player
        if p.is_hiding:
            return "exit_hide", p.hidden_in
        best, best_d = None, 999.0
        aim_half_angle = 0.5
        for prop in self.props:
            if prop.picked or prop.interactable is None:
                continue
            radius = 1.3 if prop.wall_mounted else 1.05
            dx, dy = prop.x - p.x, prop.y - p.y
            d = math.hypot(dx, dy)
            if d >= radius or d >= best_d:
                continue
            if d >= 0.45:
                rel = (math.atan2(dy, dx) - p.angle + math.pi) % (2 * math.pi) - math.pi
                if abs(rel) > aim_half_angle:
                    continue
            best, best_d = (prop.interactable, prop), d
        for door in self.doors:
            if door.is_broken:
                continue
            d = math.hypot(door.x - p.x, door.y - p.y)
            if d < 1.1 and d < best_d:
                best, best_d = ("door", door), d
        return best

    def _spec_t(self, field, default_key=None):
        key = self.spec.get(field, default_key)
        return i18n.t(key) if key else None

    def prompt_text(self, res):
        if res is None:
            return None
        kind, obj = res
        if kind == "exit_hide":
            return i18n.t("prompt.exit_hide")
        if kind == "locker":
            return i18n.t("prompt.hide")
        if kind == "note":
            return i18n.t("prompt.read_note")
        if kind == "pickup":
            item = i18n.t(PICKUP_LABEL_KEYS[obj.kind]) if obj.kind in PICKUP_LABEL_KEYS else obj.kind
            return i18n.t("prompt.pickup", item=item)
        if kind == "panel":
            panel_label = self._spec_t("panel_label")
            if obj.powered:
                return i18n.t("hud.panel_status", label=panel_label, status=self._spec_t("panel_powered_text", "hud.powered"))
            if self.player.carried > 0:
                return i18n.t("prompt.install_hold", label=self._spec_t("collectible_label"),
                              have=obj.installed, need=self.spec["n_collectible"])
            return i18n.t("prompt.panel_need", panel=panel_label, have=obj.installed,
                          need=self.spec["n_collectible"], label=self._spec_t("collectible_label"))
        if kind == "exit":
            req = self.spec.get("exit_requires_item")
            if req and not getattr(self.player, f"has_{req}", False):
                return self._spec_t("exit_requires_label", "hint.something_missing")
            if obj.powered:
                return i18n.t("prompt.exit_ready", label=self._spec_t("exit_label").capitalize())
            return i18n.t("prompt.exit_no_power", label=self._spec_t("exit_label").capitalize())
        if kind == "door":
            return i18n.t("prompt.door_close") if obj.is_open else i18n.t("prompt.door_open")
        if kind == "portal":
            target = i18n.t(PORTAL_LABEL_KEYS[obj.target_floor]) if obj.target_floor in PORTAL_LABEL_KEYS else "?"
            return i18n.t("prompt.portal", target=target)
        return None

    def interact(self):
        if self.hide_transition is not None:
            return
        res = self.find_interactable()
        if res is None:
            return
        kind, obj = res
        if kind == "exit_hide":
            self._start_hide_transition(self.player.hidden_in, entering=False)
        elif kind == "locker":
            self._start_hide_transition(obj, entering=True)
        elif kind == "note":
            self.note_text = i18n.t(obj.note_text)
            self.note_timer = S.NOTE_DISPLAY_SECONDS
            self.stats["notes"] += 1
            self.sounds.play_note_pickup()
        elif kind == "door":
            if obj.is_open:
                obj.toggle()
                pan, vol = self._pan_vol_for(obj.x, obj.y)
                self.sounds.play_door(pan, vol)
            else:
                self.peek_hold_target = obj
                self.peek_hold_t = 0.0
        elif kind == "pickup":
            obj.picked = True
            if obj.kind == "battery":
                self.player.add_battery()
                self.stats["batteries"] += 1
            elif obj.kind == "cutters":
                self.player.has_cutters = True
                self.hint_text = i18n.t("hint.cutters_picked")
                self.hint_timer = 3.0
            elif obj.kind == "sanity_pill":
                self.sanity_boost_timer = S.SANITY_PILL_DURATION
                self.hint_text = i18n.t("hint.sanity_pill_used")
                self.hint_timer = 3.0
            else:
                self.player.carried += 1
            self.sounds.play_pickup()
        elif kind == "panel":
            if obj.powered:
                self.sounds.play_ui()
            elif self.player.carried <= 0:
                self.sounds.play_denied()
                self.hint_text = i18n.t("hint.need_collectible", label=self._spec_t("collectible_label"),
                                         have=obj.installed, need=self.spec["n_collectible"])
                self.hint_timer = 2.5
        elif kind == "exit":
            req = self.spec.get("exit_requires_item")
            if req and not getattr(self.player, f"has_{req}", False):
                self.sounds.play_denied()
                self.hint_text = self._spec_t("exit_requires_label", "hint.something_missing")
                self.hint_timer = 2.5
            elif obj.powered:
                self._trigger_exit()
            else:
                self.sounds.play_denied()
        elif kind == "portal":
            if obj.target_floor == "hub":
                self._start_debug_level()
            else:
                self._enter_debug_peek(obj.target_floor)

    def _complete_install(self, obj):
        self.player.carried -= 1
        obj.installed += 1
        self.sounds.play_unlock()
        self._start_interact_feedback()
        if obj.installed >= self.spec["n_collectible"]:
            obj.powered = True
            if obj.kind == "shed_lock":
                obj.swing_target = 1.0
            if self.exit_prop is not None:
                self.exit_prop.powered = True
            self.hint_text = i18n.t("hint.exit_powered", label=self._spec_t("exit_label").capitalize())
            self.hint_timer = 3.5

    def _update_install_hold(self, dt, interact_held):
        target = None
        if interact_held:
            res = self.find_interactable()
            if res is not None:
                kind, obj = res
                if kind == "panel" and not obj.powered and self.player.carried > 0:
                    target = obj
        if target is None:
            self.install_hold_target = None
            self.install_hold_t = 0.0
            return
        if target is not self.install_hold_target:
            self.install_hold_target = target
            self.install_hold_t = 0.0
        self.install_hold_t += dt
        if self.install_hold_t >= S.INSTALL_HOLD_SECONDS:
            self._complete_install(target)
            self.install_hold_target = None
            self.install_hold_t = 0.0

    def _update_peek_hold(self, dt, interact_held):
        if interact_held and self.peek_hold_target is not None:
            res = self.find_interactable()
            target = self.peek_hold_target
            still_valid = res is not None and res[0] == "door" and res[1] is target and not target.is_open
            if still_valid:
                if not self.is_peeking:
                    self.peek_hold_t += dt
                    if self.peek_hold_t >= S.PEEK_HOLD_SECONDS:
                        self.is_peeking = True
                        self.peek_door = target
                        self.player.flashlight_before_peek = self.player.flashlight_on
                        self.player.flashlight_on = False
            else:
                self.peek_hold_target = None
                self.peek_hold_t = 0.0
                self.is_peeking = False
        elif not interact_held:
            if self.peek_hold_target is not None and not self.is_peeking:
                target = self.peek_hold_target
                target.toggle()
                pan, vol = self._pan_vol_for(target.x, target.y)
                self.sounds.play_door(pan, vol)
            self.peek_hold_target = None
            self.peek_hold_t = 0.0
            self.is_peeking = False

        target_t = 1.0 if self.is_peeking else 0.0
        step = dt / max(0.001, S.PEEK_TRANSITION_SECONDS)
        if abs(target_t - self.peek_t) <= step:
            self.peek_t = target_t
        else:
            self.peek_t += step if target_t > self.peek_t else -step
        if self.peek_t <= 0.0 and self.peek_door is not None:
            if getattr(self.player, "flashlight_before_peek", False) and self.player.battery > 0.5:
                self.player.flashlight_on = True
            self.peek_door = None

    def _peek_camera_override(self):
        if self.peek_door is None:
            return None
        door = self.peek_door
        kx, ky, kz = door.keyhole_world_pos()
        ax, ay = math.cos(door.facing), math.sin(door.facing)
        dx, dy = door.x - self.player.x, door.y - self.player.y
        if ax * dx + ay * dy < 0.0:
            ax, ay = -ax, -ay
        ldx, ldy = ax, ay
        nudge = door.hd * 2.0 + 0.05
        eye = (kx + ldx * nudge, ky + ldy * nudge, kz)
        yaw = math.atan2(ldy, ldx)
        ease = self.peek_t * self.peek_t * (3.0 - 2.0 * self.peek_t)
        fov = FOV_DEGREES + (S.PEEK_FOV_DEGREES - FOV_DEGREES) * ease
        return eye, yaw, 0.0, fov

    def _trigger_exit(self):
        if self._in_debug_preview():
            self._start_transition()
        elif self.floor_i >= len(S.FLOOR_SPECS) - 1:
            self._start_win_sequence()
        else:
            self._start_transition()

    def _enter_debug_peek(self, target_floor):
        self._load_floor(target_floor)
        self.spec = dict(self.spec)
        self.spec["no_threat"] = True
        self.monster.speed_mult = 0.0
        self.monster.vision_mult = 0.0
        self.player.sanity = S.SANITY_MAX
        self.debug_demo_monsters = []
        sx, sy = self.maze.start
        ret = make_prop("portal", (int(sx), int(sy)), facing=0.0)
        ret.target_floor = "hub"
        self.props.append(ret)
        self.floor_banner = i18n.t("debug.peek_banner", title=self._spec_t("title"))
        self.floor_banner_timer = 4.0
        self.hint_text = i18n.t("debug.peek_hint")
        self.hint_timer = 6.0
        self.sounds.play_ui()

    def update(self, dt):
        dt = min(dt, 0.05)
        self.anim_t += dt
        if self.state in ("menu", "credits", "warning"):
            self.player.angle = (self.player.angle + dt * 0.06) % math.tau
            self.sounds.set_ambient_volume(0.13)
        elif self.state == "playing":
            self._update_playing(dt)
        elif self.state == "catch":
            self._update_catch(dt)
        elif self.state == "catch_sanity":
            self._update_sanity_death(dt)
        elif self.state == "win_seq":
            self._update_win_seq(dt)
        elif self.state == "transition":
            self._update_transition(dt)
        elif self.state == "settings":
            if self.settings_return == "menu":
                self.player.angle = (self.player.angle + dt * 0.06) % math.tau
                self.sounds.set_ambient_volume(0.13)
            if self.controls_msg_timer > 0:
                self.controls_msg_timer -= dt
            self._update_mic_level(dt)
        elif self.state == "update_downloading":
            snap = self._updater.snapshot()
            if snap["state"] == "downloaded" and not self._update_apply_started:
                self._update_apply_started = True
                self._apply_update_now()
            elif snap["state"] == "error":
                self.state = "menu"
        self.fx_shake.update(dt)

        if self.state == "menu" and not self._update_prompt_seen:
            if self._updater.snapshot()["state"] == "available":
                self._update_prompt_seen = True
                self.state = "update_prompt"

        want_menu_music = self.state in ("menu", "credits", "warning", "update_prompt", "update_downloading") or (
            self.state == "settings" and self.settings_return == "menu") or (
            self.state == "confirm_quit" and self.confirm_return == "menu")
        if want_menu_music:
            self.sounds.play_menu_music()
        elif self._floor_music_pending:
            self.sounds.play_floor_music()
            self._floor_music_pending = False

    def _update_playing(self, dt):
        for dm in self.debug_demo_monsters:
            dm.update(dt)
        mouse_dx, mouse_dy = pygame.mouse.get_rel()
        if self.hide_transition is not None:
            self._update_hide_transition(dt)
            self._update_install_hold(dt, False)
            self._update_peek_hold(dt, False)
        else:
            keys = pygame.key.get_pressed()
            b = self.settings["bindings"]
            if self.is_peeking:
                self.player.moved_this_frame = False
                self.player.noise_radius = 0.0
            else:
                keys_down = {
                    "forward": keys[b["forward"]] or keys[pygame.K_UP],
                    "back": keys[b["back"]] or keys[pygame.K_DOWN],
                    "left": keys[b["left"]],
                    "right": keys[b["right"]],
                    "sprint": keys[b["sprint"]],
                }
                turn_left = keys[pygame.K_LEFT]
                turn_right = keys[pygame.K_RIGHT]
                crouch_held = keys[b["crouch"]]
                sens = self.settings["mouse_sensitivity"]
                sens_y = self.settings["mouse_sensitivity_y"]
                self.player.update_movement(
                    dt, keys_down, mouse_dx * sens, mouse_dy * sens_y, self.maze, self.props + self.doors,
                    turn_left, turn_right, crouch_held=crouch_held,
                    infinite_stamina=self.spec.get("no_threat", False),
                )
            self._update_install_hold(dt, keys[b["interact"]])
            self._update_peek_hold(dt, keys[b["interact"]])
        if not self.spec.get("no_threat"):
            self.player.update_flashlight(dt)
        self._update_player_lit()

        fade_dir = 1.0 if self.player.is_hiding else -1.0
        self.hide_vignette_t = max(0.0, min(S.HIDE_VIGNETTE_FADE,
                                             self.hide_vignette_t + fade_dir * dt))

        gated = self._update_mic_level(dt)
        if gated > 0.0:
            self.player.noise_radius = max(self.player.noise_radius, gated * S.MONSTER_HEARING_RANGE_SPRINT)

        if not self.player.is_hiding and self.player.moved_this_frame and self.player.consume_step():
            surface = FLOOR_THEME_SURFACE.get(self.spec.get("floor_theme"), "tile")
            self.sounds.play_footstep(self.player.is_sprinting, surface)

        if self.player.flashlight_on and self.player.battery < S.FLASHLIGHT_LOW and not self._battery_warned:
            self.sounds.play_battery_low()
            self._battery_warned = True
        if self.player.battery >= S.FLASHLIGHT_LOW:
            self._battery_warned = False

        for door in self.doors:
            door.update(dt)
        shed_lock_barrier = (self.panel_prop,) if (
            self.panel_prop is not None and self.panel_prop.kind == "shed_lock") else ()
        self.renderer.sync_door_mask(self.doors, extra_barriers=shed_lock_barrier)

        if self.panel_prop is not None and hasattr(self.panel_prop, "swing_target"):
            sw = getattr(self.panel_prop, "swing", 0.0)
            self.panel_prop.swing = sw + (self.panel_prop.swing_target - sw) * min(1.0, dt * 3.0)

        self.elapsed += dt
        self.floor_elapsed += dt
        time_frac = min(1.0, self.elapsed / S.DREAD_RAMP_SECONDS)
        progress_frac = 0.0
        if self.panel_prop is not None and self.spec["n_collectible"] > 0:
            progress_frac = min(1.0, self.panel_prop.installed / self.spec["n_collectible"])
        if self.panel_prop is not None and self.panel_prop.powered:
            progress_frac = 1.0
        self.dread = min(1.0, 0.6 * time_frac + 0.4 * progress_frac)
        self._dread_time_frac = time_frac
        self._dread_progress_frac = progress_frac

        if not self.spec.get("no_threat"):
            grace = self.floor_elapsed < self.spec["grace"]
            self.monster.update(dt, self.maze, self.player, self.dread, self.props, grace=grace)
            if self.monster.just_noticed:
                self.sounds.play_alert()
                self.fx_shake.add(0.25)
        self._update_door_break_feedback(dt)

        self._update_sanity(dt)
        self._update_audio_3d(dt)
        self._update_scares(dt)
        self._update_hallucinations(dt)

        if self.note_timer > 0:
            self.note_timer -= dt
        if self.hint_timer > 0:
            self.hint_timer -= dt
        if self.floor_banner_timer > 0:
            self.floor_banner_timer -= dt
        if self.scare_flash_timer > 0:
            self.scare_flash_timer -= dt
        if self.interact_feedback_timer > 0:
            self.interact_feedback_timer -= dt
        if self.sanity_boost_timer > 0:
            self.sanity_boost_timer -= dt

        if self.monster.caught_player:
            self._start_catch_sequence()
        elif self.player.sanity <= 0:
            self._start_sanity_death()

    def _start_interact_feedback(self):
        self.interact_feedback_timer = 0.3
        self.fx_shake.add(0.12)

    def _update_door_break_feedback(self, dt):
        if self.monster.breaking_door is None:
            self._door_break_sfx_timer = 0.0
            return
        self._door_break_sfx_timer -= dt
        if self._door_break_sfx_timer <= 0:
            d = self.monster.breaking_door
            pan, vol = self._pan_vol_for(d.x, d.y)
            self.sounds.play_bang(pan, vol)
            self.fx_shake.add(0.4)
            self._door_break_sfx_timer = 0.4

    def _update_sanity(self, dt):
        if self.spec.get("no_threat"):
            return
        p, m = self.player, self.monster
        dist = math.hypot(m.x - p.x, m.y - p.y)
        vision = (S.MONSTER_VISION_RANGE_LIT if p.flashlight_on else S.MONSTER_VISION_RANGE) * m.vision_mult
        covered = dist < vision and p.is_crouching and line_blocked_by_cover(self.props, m.x, m.y, p.x, p.y)
        visible = (not p.is_hiding) and dist < vision and not covered and self.maze.has_line_of_sight(p.x, p.y, m.x, m.y)
        near_range = S.MONSTER_HEARING_RANGE * 1.4
        proximity_dread = 0.0 if p.is_hiding else max(0.0, 1.0 - dist / near_range)
        if visible:
            proximity = max(0.0, 1.0 - dist / vision)
            p.apply_sanity(-S.SANITY_MONSTER_DRAIN * proximity * dt)
        elif proximity_dread > 0:
            p.apply_sanity(-S.SANITY_MONSTER_DRAIN * 0.5 * proximity_dread * dt)
        elif p.is_hiding:
            if p.flashlight_on:
                p.apply_sanity(S.SANITY_HIDE_LIT_REGEN * dt)
            else:
                crouch_mult = S.SANITY_HIDE_CROUCH_MULT if p.is_crouching else 1.0
                p.apply_sanity(-S.SANITY_HIDE_DRAIN * crouch_mult * dt)
        else:
            boosted = self.sanity_boost_timer > 0.0
            if boosted or p.is_lit:
                if dist > S.MONSTER_HEARING_RANGE * 1.5:
                    regen_rate = S.SANITY_REGEN * (S.SANITY_PILL_REGEN_MULT if boosted else 1.0)
                    p.apply_sanity(regen_rate * dt)
            elif dist > S.MONSTER_HEARING_RANGE:
                p.apply_sanity(-S.SANITY_DARK_DRAIN * dt)

    def _pan_vol_for(self, wx, wy, falloff=8.0):
        p = self.player
        dist = math.hypot(wx - p.x, wy - p.y)
        ang_to = math.atan2(wy - p.y, wx - p.x)
        rel = (ang_to - p.angle + math.pi) % (2 * math.pi) - math.pi
        pan = max(-1.0, min(1.0, math.sin(rel)))
        vol = max(0.0, 1.0 - dist / falloff)
        return pan, vol

    @staticmethod
    def _growl_pan_vol(dist, rel_angle, alert_level):
        pan = max(-1.0, min(1.0, math.sin(rel_angle)))
        vol = max(0.0, 1.0 - dist / S.MONSTER_GROWL_FALLOFF) * (0.25 + 0.75 * alert_level) * 0.7
        return pan, vol

    def _update_audio_3d(self, dt):
        p, m = self.player, self.monster
        pulse = next((h for h in self.hallu_active if h["kind"] == "pulse"), None)
        if pulse is not None:
            progress = max(0.0, min(1.0, 1.0 - pulse["remaining"] / pulse["total"]))
            dist = S.HALLUCINATION_PULSE_FAR_DIST + (
                S.HALLUCINATION_PULSE_NEAR_DIST - S.HALLUCINATION_PULSE_FAR_DIST) * progress
            rel = (pulse["angle"] - p.angle + math.pi) % (2 * math.pi) - math.pi
            pan, vol = self._growl_pan_vol(dist, rel, 1.0)
        else:
            dist = math.hypot(m.x - p.x, m.y - p.y)
            ang_to = math.atan2(m.y - p.y, m.x - p.x)
            rel = (ang_to - p.angle + math.pi) % (2 * math.pi) - math.pi
            pan, vol = self._growl_pan_vol(dist, rel, m.alert_level)
        self.sounds.set_growl(vol > 0.02, vol, pan)
        self.sounds.update_heartbeat(dt, p.sanity / S.SANITY_MAX)
        ambient_vol = 0.17 + 0.14 * (1 - p.sanity / S.SANITY_MAX) + 0.08 * self.dread
        self.sounds.set_ambient_volume(min(1.0, ambient_vol))
        self.sounds.set_hunt(m.state == Monster.HUNT)
        self._update_scare_source()

    def _update_scare_source(self):
        if self.scare_source is None:
            return
        if not self.sounds.ch_voice.get_busy():
            self.scare_source = None
            return
        pan, vol, _dist = self._scare_pan_vol()
        self.sounds.set_scare_pan(pan, vol)

    @staticmethod
    def _point_light_atten(dist, radius):
        lt = max(0.0, min(1.0, dist / max(radius, 0.001)))
        tail = 1.0
        if lt > 0.85:
            tt = (lt - 0.85) / 0.15
            tail = 1.0 - tt * tt * (3.0 - 2.0 * tt)
        return (1.0 - lt) * tail

    _FLASHLIGHT_USEFUL_RANGE = 8.0

    def _flashlight_lights_something(self):
        p = self.player
        if not p.flashlight_on:
            return False
        fx, fy = math.cos(p.angle), math.sin(p.angle)
        step = 0.25
        for i in range(1, int(self._FLASHLIGHT_USEFUL_RANGE / step) + 1):
            if self.maze.is_wall(p.x + fx * step * i, p.y + fy * step * i):
                return True
        return False

    _IS_LIT_THRESHOLD = 0.12

    def _update_player_lit(self):
        p = self.player
        level = self.spec.get("ambient_level", 0.0)
        for obj in self.props:
            radius = getattr(obj, "light_radius", None)
            if not radius or obj.picked:
                continue
            if not self.maze.has_line_of_sight(p.x, p.y, obj.x, obj.y):
                continue
            if line_blocked_by_cover(self.doors, p.x, p.y, obj.x, obj.y, min_height=0.1):
                continue
            level += self._point_light_atten(math.hypot(p.x - obj.x, p.y - obj.y), radius)
        if self._flashlight_lights_something():
            level += 0.5
        p.light_level = level
        p.is_lit = level > self._IS_LIT_THRESHOLD

    def _update_scares(self, dt):
        if self.spec.get("no_threat"):
            return
        seconds_dark = 14.0 - 8.0 * self.dread
        seconds_lit = 34.0 - 10.0 * self.dread
        self._scare_lit = self.player.is_lit
        seconds_to_fill = seconds_lit if self._scare_lit else seconds_dark
        self._scare_seconds_to_fill = seconds_to_fill
        self.scare_progress += dt / max(1.0, seconds_to_fill)
        if self.scare_progress >= self.scare_target:
            self.scare_progress = 0.0
            self.scare_target = random.uniform(0.85, 1.15)
            self._trigger_random_scare()

    def _update_hallucinations(self, dt):
        if self.spec.get("no_threat"):
            return
        self._tick_active_hallucinations(dt)
        if self.hallu_cooldown > 0:
            self.hallu_cooldown -= dt
        p = self.player
        sanity_frac = p.sanity / S.SANITY_MAX
        if sanity_frac >= S.HALLUCINATION_SANITY_THRESHOLD or p.is_hiding or self.monster.state == Monster.HUNT:
            self.hallu_progress = 0.0
            return
        deficit = (S.HALLUCINATION_SANITY_THRESHOLD - sanity_frac) / S.HALLUCINATION_SANITY_THRESHOLD
        seconds_to_fill = (S.HALLUCINATION_SECONDS_MAX
                            - (S.HALLUCINATION_SECONDS_MAX - S.HALLUCINATION_SECONDS_MIN) * deficit)
        self.hallu_progress += dt / max(0.1, seconds_to_fill)
        if self.hallu_progress >= self.hallu_target and self.hallu_cooldown <= 0:
            self.hallu_progress = 0.0
            self.hallu_target = random.uniform(0.85, 1.15)
            self.hallu_cooldown = S.HALLUCINATION_MIN_GAP
            self._trigger_random_hallucination()

    def _tick_active_hallucinations(self, dt):
        still = []
        for h in self.hallu_active:
            h["remaining"] -= dt
            if h["remaining"] <= 0:
                continue
            if h["kind"] == "door":
                h["next_hit"] -= dt
                if h["next_hit"] <= 0:
                    h["next_hit"] = S.HALLUCINATION_DOOR_HIT_INTERVAL
                    pan, vol = self._pan_vol_for(*h["pos"])
                    self.sounds.play_hallu_bang(pan, vol)
                    self.fx_shake.add(0.15)
            still.append(h)
        self.hallu_active = still

    def _trigger_random_hallucination(self):
        roll = random.random()
        if roll < 0.40:
            self._start_hallu_pulse()
        elif roll < 0.70:
            self.sounds.play_hallu_alert()
            self.fx_shake.add(0.10)
        else:
            self._start_hallu_door_break()

    def _start_hallu_pulse(self):
        duration = random.uniform(S.HALLUCINATION_PULSE_MIN_LEN, S.HALLUCINATION_PULSE_MAX_LEN)
        angle = random.uniform(0.0, math.tau)
        self.hallu_active.append({
            "kind": "pulse", "remaining": duration, "total": duration, "angle": angle,
        })
        self.fx_shake.add(0.06)

    def _start_hallu_door_break(self):
        if not self.doors:
            return
        p = self.player
        nearest = min(self.doors, key=lambda d: math.hypot(d.x - p.x, d.y - p.y))
        duration = random.uniform(S.HALLUCINATION_DOOR_MIN_LEN, S.HALLUCINATION_DOOR_MAX_LEN)
        self.hallu_active.append({
            "kind": "door", "pos": (nearest.x, nearest.y), "next_hit": 0.0, "remaining": duration,
        })

    def _play_spatial_scare(self):
        p = self.player
        ang = random.uniform(0, math.tau)
        dist = random.uniform(6.0, 14.0)
        self.scare_source = (p.x + math.cos(ang) * dist, p.y + math.sin(ang) * dist)
        pan, vol, dist = self._scare_pan_vol()
        self.sounds.play_scare(pan, vol, dist=dist)

    def _scare_pan_vol(self):
        p = self.player
        sx, sy = self.scare_source
        dist = math.hypot(sx - p.x, sy - p.y)
        ang_to = math.atan2(sy - p.y, sx - p.x)
        rel = (ang_to - p.angle + math.pi) % (2 * math.pi) - math.pi
        pan = max(-1.0, min(1.0, math.sin(rel)))
        vol = max(0.0, 1.0 - dist / 16.0)
        return pan, vol, dist

    def _trigger_random_scare(self):
        if self.player.is_hiding:
            return
        self.stats["scares"] += 1
        roll = random.random()
        if roll < 0.3:
            self._play_spatial_scare()
            self.player.apply_sanity(-2.0)
        elif roll < 0.55:
            self.sounds.play_bang()
            self.fx_shake.add(0.3)
            self.player.apply_sanity(-1.5)
        elif roll < 0.8:
            self._play_spatial_scare()
            self.sounds.play_bang()
            self.fx_shake.add(0.45)
            self.player.apply_sanity(-3.5)
        else:
            self._play_spatial_scare()
            self.scare_flash_timer = 0.14
            self.fx_shake.add(0.2)
            self.player.apply_sanity(-2.5)

    def _start_catch_sequence(self):
        self.state = "catch"
        self.catch_timer = 0.0
        self.sounds.set_growl(False)
        self.sounds.stop_hunt(fade_ms=2000)
        self.sounds.play_stinger()
        self.fx_shake.add(1.0)
        self._release_mouse()

    def _update_catch(self, dt):
        self.catch_timer += dt
        self.fx_shake.add(dt * 2.6)
        if self.catch_timer > 0.7:
            self.state = "dead_caught"

    def _start_sanity_death(self):
        self.state = "catch_sanity"
        self.catch_timer = 0.0
        self.sounds.set_growl(False)
        self.sounds.stop_hunt(fade_ms=2000)
        self.sounds.play_scare(0.0, 1.0)
        self._release_mouse()

    def _update_sanity_death(self, dt):
        self.catch_timer += dt
        self.fx_shake.add(dt * 0.3)
        if self.catch_timer > 2.6:
            self.state = "dead_sanity"

    def _start_transition(self):
        self.state = "transition"
        self.transition_timer = 0.0
        self.sounds.play_unlock()
        self.sounds.set_growl(False)
        self._release_mouse()

    def _update_transition(self, dt):
        self.transition_timer += dt
        if self.transition_timer > 2.8:
            if self._in_debug_preview():
                self._start_debug_level()
            else:
                self._load_floor(self.floor_i + 1)
                self._begin_playing()

    def _start_win_sequence(self):
        self.state = "win_seq"
        self.catch_timer = 0.0
        self.sounds.set_growl(False)
        self.sounds.play_win()
        self._release_mouse()

    def _update_win_seq(self, dt):
        self.catch_timer += dt
        if self.catch_timer > 3.0:
            self.state = "win"

    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                self._handle_keydown(event.key)
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                self._handle_click(self._logical_mouse_pos(event.pos))
            elif event.type == pygame.MOUSEMOTION:
                if self.dragging_slider is not None and self.state == "settings":
                    layout = self._settings_page_layout(self.settings_page)
                    rect = layout["sliders"].get(self.dragging_slider)
                    if rect is not None:
                        self._apply_slider(self.dragging_slider, rect, self._logical_mouse_pos(event.pos)[0])
            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                self.dragging_slider = None
            elif event.type == pygame.MOUSEWHEEL and self.state == "menu" and self._show_changelog:
                self._changelog_scroll_px = max(
                    0, min(self._changelog_max_scroll, self._changelog_scroll_px - event.y * 40))
            elif event.type == pygame.WINDOWFOCUSLOST and self.state == "playing":
                self.state = "paused"
                self._release_mouse()

    def _handle_click(self, pos):
        if self.state == "menu" and self._show_changelog:
            if self._changelog_close_rect.collidepoint(pos):
                self._close_changelog()
        elif self.state == "menu":
            self._handle_button_click(self._menu_buttons(), pos)
        elif self.state == "paused":
            self._handle_button_click(self._pause_buttons(), pos)
        elif self.state == "settings":
            self._handle_settings_click(pos)
        elif self.state == "credits":
            self._handle_button_click(self._credits_buttons(), pos)
        elif self.state == "warning":
            self._handle_button_click(self._warning_buttons(), pos)
        elif self.state == "confirm_quit":
            self._handle_button_click(self._confirm_quit_buttons(), pos)
        elif self.state == "update_prompt":
            self._handle_button_click(self._update_prompt_buttons(), pos)
        elif self.state in ("dead_caught", "dead_sanity", "win"):
            self._handle_button_click(self._end_buttons(
                S.SCREEN_H // 2 + (85 if self.state == "win" else 105)), pos)

    def _handle_keydown(self, key):
        if self.awaiting_bind is not None:
            self._resolve_bind(key)
            return
        if self.state == "menu" and self._show_changelog:
            if key == pygame.K_ESCAPE:
                self._close_changelog()
        elif self.state == "menu":
            if key == pygame.K_F9:
                self._start_debug_level()
        elif self.state == "credits":
            if key == pygame.K_ESCAPE:
                self.state = "menu"
        elif self.state == "confirm_quit":
            if key == pygame.K_ESCAPE:
                self._cancel_quit()
        elif self.state == "update_prompt":
            if key == pygame.K_ESCAPE:
                self._decline_update()
        elif self.state == "playing":
            if key == pygame.K_ESCAPE:
                self.state = "paused"
                self._release_mouse()
            elif key == self.settings["bindings"]["flashlight"]:
                if self.is_peeking:
                    self.sounds.play_denied()
                elif not self.player.toggle_flashlight():
                    self.sounds.play_denied()
                else:
                    self.sounds.play_ui()
            elif key == self.settings["bindings"]["interact"]:
                self.interact()
        elif self.state == "paused":
            if key == pygame.K_ESCAPE:
                self._begin_playing()
        elif self.state == "settings":
            if key == pygame.K_ESCAPE:
                self._close_settings()

    def draw(self):
        self.hud_surf.fill((0, 0, 0, 0))

        if self.state in ("menu", "playing", "paused", "settings",
                           "catch_sanity", "win_seq", "credits", "warning", "confirm_quit",
                           "update_prompt", "update_downloading"):
            shake_yaw, shake_pitch = self.fx_shake.offset(max_px=0.045)
            hide_locker, hide_swing = None, 0.0
            if self.hide_transition is not None:
                hide_locker = self.hide_transition["locker"]
                hide_frac = min(1.0, self.hide_transition["t"] / self.hide_transition["duration"])
                hide_swing = math.sin(hide_frac * math.pi)
            self.renderer.render(
                self.maze, self.player, self.monster, self.props + self.doors, self.dread, self.anim_t,
                shake_yaw, shake_pitch,
                fog_color=self.spec.get("fog_color", S.COL_FOG), fog_dist=self.spec.get("fog_dist", 12.5),
                ambient=self.spec.get("ambient_level", 0.06),
                moon_strength=self.spec.get("moon_strength", 0.0),
                view_distance_mult=self.settings.get("view_distance", 1.0),
                hide_locker=hide_locker, hide_swing=hide_swing,
                camera_override=self._peek_camera_override(),
            )
            for dm in self.debug_demo_monsters:
                self.renderer._draw_monster(dm, 0.0, check_frac=self.renderer.compute_check_frac(dm))
            if self.player.is_hiding or self.hide_vignette_t > 0.0:
                self._draw_hide_frame()

        if self.state == "menu":
            self._draw_menu()
            if self._show_changelog:
                self._draw_changelog()
        elif self.state == "update_prompt":
            self._draw_menu()
            self._draw_update_prompt()
        elif self.state == "update_downloading":
            self._draw_menu()
            self._draw_update_downloading()
        elif self.state == "warning":
            self._draw_warning()
        elif self.state == "credits":
            self._draw_credits()
        elif self.state == "playing":
            self._draw_hud()
            self._draw_peek_mask()
        elif self.state == "paused":
            self._draw_hud()
            self._draw_peek_mask()
            self._draw_pause()
        elif self.state == "catch":
            progress = min(1.0, self.catch_timer / 0.7)
            fx.draw_jumpscare_face(self.hud_surf, progress)
        elif self.state == "catch_sanity":
            t = self.catch_timer / 2.6
            white = max(0, 255 * (1 - abs(t - 0.3) / 0.3)) if t < 0.6 else 0
            fx.draw_flash(self.hud_surf, (255, 255, 255), white)
            black = 255 * max(0.0, (t - 0.55) / 0.45)
            fx.draw_flash(self.hud_surf, (0, 0, 0), black)
        elif self.state == "win_seq":
            t = self.catch_timer / 3.0
            fx.draw_flash(self.hud_surf, (255, 255, 245), 255 * min(1.0, t * 1.2))
        elif self.state == "transition":
            self._draw_transition()
        elif self.state == "dead_caught":
            self._draw_gameover(i18n.t("gameover.caught_title"), (150, 20, 20), i18n.t("gameover.caught_subtitle"))
        elif self.state == "dead_sanity":
            self._draw_gameover(i18n.t("gameover.sanity_title"), (200, 200, 210), i18n.t("gameover.sanity_subtitle"))
        elif self.state == "win":
            self._draw_win()
        elif self.state == "settings":
            self._draw_settings()
        elif self.state == "confirm_quit":
            if self.confirm_return == "menu":
                self._draw_menu()
            else:
                self._draw_hud()
            self._draw_confirm_quit()

        if self.state not in ("catch", "catch_sanity", "win_seq", "transition"):
            self._draw_wip_label()

        self._draw_debug_overlay()

        self._sync_window_size()
        hud_bytes = pygame.image.tostring(self.hud_surf, "RGBA", True)
        self.renderer.composite(hud_bytes, (S.SCREEN_W, S.SCREEN_H), self.window_size)
        pygame.display.flip()

    def _draw_wip_label(self):
        text = "Work in progress..."
        w = self.font_sm.size(text)[0]
        self._text(self.font_sm, text, S.COL_UI_DIM,
                   topleft=(S.SCREEN_W - w - 14, S.SCREEN_H - 26), shadow=False)

    def _draw_debug_overlay(self):
        lines = []
        if self.settings.get("debug_hud_fps"):
            lines.append(f"FPS: {self.clock.get_fps():.0f}")
            perf = getattr(self.renderer, "last_perf", None)
            if perf:
                lines.append("  ".join(f"{k}:{v:.1f}ms" for k, v in perf.items()))
        gameplay = self.state in ("playing", "paused")
        if gameplay and self.settings.get("debug_hud_coords"):
            p = self.player
            lines.append(f"XY: {p.x:.2f}, {p.y:.2f}  cell: {p.cell}  ang: {math.degrees(p.angle) % 360:.0f} deg")
        if gameplay and self.settings.get("debug_hud_monster"):
            m, p = self.monster, self.player
            dist = math.hypot(m.x - p.x, m.y - p.y)
            lines.append(f"Monster: {i18n.t(f'debug_hud.state_{m.state}')}  dist: {dist:.1f}  alert: {m.alert_level:.2f}")
        if gameplay and self.settings.get("debug_hud_seed"):
            lines.append(f"Seed: {self.floor_seed}")
        if gameplay and self.settings.get("debug_hud_scares"):
            lines.append(f"Dread: {self.dread*100:.0f}%  (time {self._dread_time_frac*100:.0f}% "
                          f"+ progress {self._dread_progress_frac*100:.0f}%)")
            fill_pct = self.scare_progress / self.scare_target * 100.0 if self.scare_target else 0.0
            rate = 100.0 / max(1.0, self._scare_seconds_to_fill)
            lit_tag = "lit" if self._scare_lit else "dark"
            lines.append(f"Scare fill: {fill_pct:.0f}%  rate: {rate:.1f}%/s  ({lit_tag})")
        if not lines:
            return
        font = self.font_sm
        pad = 6
        w = max(font.size(line)[0] for line in lines) + pad * 2
        h = len(lines) * 18 + pad * 2
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        surf.fill((0, 0, 0, 150))
        self.hud_surf.blit(surf, (8, 8))
        for i, line in enumerate(lines):
            self._text(font, line, (120, 230, 120), topleft=(8 + pad, 8 + pad + i * 18), shadow=False)

    def _draw_hide_frame(self):
        fade = min(1.0, self.hide_vignette_t / S.HIDE_VIGNETTE_FADE)
        if fade <= 0.0:
            return
        self.hide_vignette.set_alpha(int(255 * fade))
        self.hud_surf.blit(self.hide_vignette, (0, 0))

    def _draw_peek_mask(self):
        if self.peek_t <= 0.0:
            return
        w, h = S.SCREEN_W, S.SCREEN_H
        cx, cy = w // 2, h // 2
        ease = self.peek_t * self.peek_t * (3.0 - 2.0 * self.peek_t)
        max_r = min(w, h) * 0.46
        r = max(3, int(max_r * (0.05 + 0.95 * ease)))
        overlay = pygame.Surface((w, h), pygame.SRCALPHA)
        overlay.fill((5, 4, 6, 255))
        top_cy = cy - int(r * 0.15)
        pygame.draw.circle(overlay, (0, 0, 0, 0), (cx, top_cy), int(r * 0.62))
        slit_w = max(1, int(r * 0.34))
        slit_top = cy - int(r * 0.05)
        slit_h = int(r * 0.95)
        pygame.draw.polygon(overlay, (0, 0, 0, 0), [
            (cx - slit_w, slit_top),
            (cx + slit_w, slit_top),
            (cx + int(slit_w * 0.55), slit_top + slit_h),
            (cx - int(slit_w * 0.55), slit_top + slit_h),
        ])
        self.hud_surf.blit(overlay, (0, 0))

    def _text(self, font, text, color, center=None, topleft=None, shadow=True):
        surf = font.render(text, True, color)
        if shadow:
            sh = font.render(text, True, (0, 0, 0))
            r = surf.get_rect()
            if center:
                r.center = (center[0] + 2, center[1] + 2)
            else:
                r.topleft = (topleft[0] + 2, topleft[1] + 2)
            self.hud_surf.blit(sh, r)
        r = surf.get_rect()
        if center:
            r.center = center
        else:
            r.topleft = topleft
        self.hud_surf.blit(surf, r)
        return r

    def _draw_menu(self):
        left = 90
        self.hud_surf.blit(self.menu_gradient, (0, 0))
        self._blit_static()

        flicker = 0.88 + 0.12 * math.sin(self.anim_t * 1.3)
        if random.random() < 0.0015:
            flicker *= 0.55
        title_y = 70
        for spread, a in ((4, 26), (2, 55)):
            glow = self.font_title.render(S.TITLE, True, (200, 30, 30))
            glow.set_alpha(int(a * flicker))
            for ox, oy in ((-spread, 0), (spread, 0), (0, -spread), (0, spread)):
                self.hud_surf.blit(glow, (left + ox, title_y + oy))
        title_col = (int(215 * flicker), int(40 * flicker), int(38 * flicker))
        title_surf = self.font_title.render(S.TITLE, True, title_col)
        self.hud_surf.blit(title_surf, (left, title_y))
        tagline_y = title_y + title_surf.get_height() + 6
        self._text(self.font_sm, i18n.t("menu.tagline"), S.COL_UI_DIM,
                   topleft=(left + 2, tagline_y), shadow=False)

        layout = self._menu_layout()
        left, content_w, story_lines, legend_lines, button_y0 = layout
        y = tagline_y + 44
        for line in story_lines:
            self._text(self.font_md, line, S.COL_TEXT, topleft=(left, y), shadow=True)
            y += 28

        y += 10
        pygame.draw.line(self.hud_surf, (110, 46, 44), (left, y), (left + content_w, y), 1)
        y += 20
        for line in legend_lines:
            self._text(self.font_sm, line, S.COL_UI_DIM, topleft=(left, y), shadow=False)
            y += 22

        for btn in self._menu_buttons(layout):
            self._draw_button(btn)

        self._text(self.font_sm, f"{S.VERSION}", S.COL_UI_DIM, topleft=(14, S.SCREEN_H - 26), shadow=False)

    def _menu_layout(self):
        left = 90
        content_w = 680
        lang = i18n.get_language()
        cache = getattr(self, "_menu_story_cache", None)
        if cache is not None and cache[0] == lang:
            story_lines = cache[1]
        else:
            story_lines = self._wrap_text(i18n.t("menu.story"), self.font_md, content_w)
            self._menu_story_cache = (lang, story_lines)
        legend_lines = self._controls_hint_lines()

        title_h = self.font_title.get_height()
        y = 70 + title_h + 6
        y += 44
        y += len(story_lines) * 28
        y += 10 + 1 + 20
        y += len(legend_lines) * 22
        y += 30
        return left, content_w, story_lines, legend_lines, y

    def _draw_warning(self):
        overlay = pygame.Surface((S.SCREEN_W, S.SCREEN_H), pygame.SRCALPHA)
        overlay.fill((2, 2, 3, 235))
        self.hud_surf.blit(overlay, (0, 0))
        self._blit_static()
        cx = S.SCREEN_W // 2
        self._text(self.font_lg, i18n.t("warning.title"), (200, 60, 60), center=(cx, 220))
        wrap_w = S.SCREEN_W - 380
        y = 300
        for line in self._wrap_text(i18n.t("warning.body"), self.font_md, wrap_w):
            self._text(self.font_md, line, S.COL_TEXT, center=(cx, y))
            y += 30
        y += 20
        for line in self._wrap_text(i18n.t("warning.extra"), self.font_md, wrap_w):
            self._text(self.font_md, line, S.COL_TEXT, center=(cx, y))
            y += 30
        for btn in self._warning_buttons():
            self._draw_button(btn)

    def _draw_credits(self):
        self.hud_surf.blit(self.menu_gradient, (0, 0))
        self._blit_static()
        rows, left, content_w, link_buttons, contributor_links, back_btn = self._credits_content()
        self._text(self.font_lg, i18n.t("about.title"), S.COL_TEXT, topleft=(left, 70))
        for y, kind, payload in rows:
            if kind == "text":
                text, font, col = payload
                self._text(font, text, col, topleft=(left, y))
            elif kind == "divider":
                pygame.draw.line(self.hud_surf, (70, 62, 58), (left, y), (left + content_w, y), 1)
        for btn in contributor_links:
            self._draw_text_link(btn)
        for btn in link_buttons:
            self._draw_button(btn)
        self._draw_button(back_btn)

    def _draw_pause(self):
        self.hud_surf.blit(self.pause_gradient, (0, 0))
        self._blit_static()
        left, _y0 = self._pause_layout()
        self._text(self.font_lg, i18n.t("pause.title"), S.COL_TEXT, topleft=(left, 90))
        for btn in self._pause_buttons():
            self._draw_button(btn)

    def _draw_mic_test_meter(self, rect):
        self._text(self.font_sm, i18n.t("settings.mic_test"), S.COL_UI_DIM,
                   topleft=(rect.x, rect.y - 22), shadow=False)
        cut = min(8, rect.h)
        shape = self._button_shape(rect, cut=cut)
        local_shape = [(px - rect.x, py - rect.y) for px, py in shape]

        track = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.polygon(track, (18, 16, 15, 225), local_shape)
        self.hud_surf.blit(track, rect.topleft)

        level = self.mic_vu_level
        fill_w = int(rect.w * max(0.0, min(1.0, level)))
        if fill_w > 0:
            fill = pygame.Surface(rect.size, pygame.SRCALPHA)
            pygame.draw.polygon(fill, self._vu_color(level), local_shape)
            if fill_w < rect.w:
                pygame.draw.rect(fill, (0, 0, 0, 0), (fill_w, 0, rect.w - fill_w, rect.h))
            self.hud_surf.blit(fill, rect.topleft)

        pygame.draw.polygon(self.hud_surf, (95, 88, 82), shape, width=2)
        if not (self.settings.get("mic_enabled") and self.mic.available):
            hint = i18n.t("settings.mic_test_disabled")
        elif not self.mic.active:
            hint = i18n.t("settings.mic_test_error")
        else:
            hint = None
        if hint:
            self._text(self.font_sm, hint, (200, 110, 100), topleft=(rect.x, rect.bottom + 8), shadow=False)

    def _draw_settings(self):
        self.hud_surf.blit(self.menu_gradient, (0, 0))
        self._blit_static()

        left, header_divider_y, tabs_area, back_rect, content = self._settings_geom()
        self._text(self.font_lg, i18n.t("settings.title"), S.COL_TEXT, topleft=(left, 70))
        pygame.draw.line(self.hud_surf, (110, 46, 44), (left, header_divider_y),
                         (content.right, header_divider_y), 1)

        for key, rect in self._settings_tabs_layout():
            tab_btn = self._button(rect, i18n.t(f"settings.tab_{key}"), lambda k=key: self._select_settings_tab(k))
            self._draw_button(tab_btn, active=(key == self.settings_page))
        back_btn = self._button(back_rect, i18n.t("ui.back"), self._close_settings)
        self._draw_button(back_btn)
        divider_x = (tabs_area.right + content.x) // 2
        pygame.draw.line(self.hud_surf, (60, 54, 50), (divider_x, tabs_area.y), (divider_x, back_rect.bottom), 1)

        page = self.settings_page
        cx = content.centerx
        layout = self._settings_page_layout(page)

        def slider_frac(key):
            lo, hi = SLIDER_SPECS[key]
            return (self.settings[key] - lo) / (hi - lo)

        slider_specs = {
            "master_volume": (i18n.t("settings.volume"), f"{int(self.settings['master_volume'] * 100)}%"),
            "sfx_volume": (i18n.t("settings.sfx_volume"), f"{int(self.settings['sfx_volume'] * 100)}%"),
            "music_volume": (i18n.t("settings.music_volume"), f"{int(self.settings['music_volume'] * 100)}%"),
            "mouse_sensitivity": (i18n.t("settings.mouse_sens_x"), f"{self.settings['mouse_sensitivity']:.1f}x"),
            "mouse_sensitivity_y": (i18n.t("settings.mouse_sens_y"), f"{self.settings['mouse_sensitivity_y']:.1f}x"),
            "view_distance": (i18n.t("settings.view_distance"), f"{int(self.settings['view_distance'] * 100)}%"),
            "mic_sensitivity": (i18n.t("settings.mic_sensitivity"), f"{self.settings['mic_sensitivity']:.1f}"),
        }
        for key, rect in layout["sliders"].items():
            if key in STEPPED_SLIDERS:
                continue
            label, value_text = slider_specs[key]
            self._draw_slider(rect, slider_frac(key), label, value_text, dragging=(self.dragging_slider == key))

        if page == "graphics":
            fs_btn, aa_btn, vs_btn = layout["buttons"]
            self._draw_toggle(fs_btn, self.settings["fullscreen"])
            self._draw_toggle(aa_btn, self.settings["upscale_smoothing"])
            self._draw_toggle(vs_btn, self.settings["vsync"])
            fps_options = STEPPED_SLIDERS["fps_limit"]
            fps_idx = fps_options.index(self.settings["fps_limit"]) if self.settings["fps_limit"] in fps_options else 0
            self._draw_stepped_slider(layout["sliders"]["fps_limit"], fps_idx, len(fps_options),
                                      i18n.t("settings.fps_limit_label"), self._fps_value_text(),
                                      dragging=(self.dragging_slider == "fps_limit"))
            quality_options = STEPPED_SLIDERS["quality_preset"]
            quality_idx = (quality_options.index(self.settings["quality_preset"])
                           if self.settings["quality_preset"] in quality_options else 1)
            self._draw_stepped_slider(layout["sliders"]["quality_preset"], quality_idx, len(quality_options),
                                      i18n.t("settings.quality_preset_label"),
                                      i18n.t(f"settings.quality_{self.settings['quality_preset']}"),
                                      dragging=(self.dragging_slider == "quality_preset"))
        elif page == "sound":
            mic_btn, = layout["buttons"]
            self._draw_toggle(mic_btn, self.settings["mic_enabled"])
            self._draw_mic_test_meter(layout["vu_rect"])
            device_rect = layout["combos"]["mic_device"]["rect"]
            self._text(self.font_sm, i18n.t("settings.mic_device_label"), S.COL_UI_DIM,
                      topleft=(device_rect.x, device_rect.y - 22), shadow=False)
            if self._mic_device_missing:
                ny = layout["missing_notice_y"]
                for line in self._wrap_text(i18n.t("settings.mic_device_missing"), self.font_sm, device_rect.w):
                    self._text(self.font_sm, line, (200, 110, 100), topleft=(device_rect.x, ny), shadow=False)
                    ny += 18
            self._draw_combo("mic_device", layout["combos"]["mic_device"])
        elif page == "controls":
            mouse = self._logical_mouse_pos()
            for action, rect in layout["rows"]:
                waiting = self.awaiting_bind == action
                hovered = waiting or rect.collidepoint(mouse)
                bg = (70, 20, 20, 220) if waiting else ((66, 22, 22, 225) if hovered else (16, 15, 15, 190))
                surf = pygame.Surface(rect.size, pygame.SRCALPHA)
                surf.fill(bg)
                self.hud_surf.blit(surf, rect.topleft)
                pygame.draw.rect(self.hud_surf, (215, 80, 80) if hovered else (95, 88, 82), rect, width=2)
                self._text(self.font_sm, i18n.t(f"binding.{action}"), S.COL_TEXT,
                          topleft=(rect.x + 14, rect.y + rect.h // 2 - self.font_sm.get_height() // 2), shadow=False)
                key_label = "..." if waiting else pygame.key.name(self.settings["bindings"][action]).upper()
                key_w = self.font_sm.size(key_label)[0]
                self._text(self.font_sm, key_label, (225, 200, 120) if waiting else (200, 190, 180),
                          topleft=(rect.right - 12 - key_w, rect.y + rect.h // 2 - self.font_sm.get_height() // 2),
                          shadow=False)
            for btn in layout["buttons"]:
                self._draw_button(btn)
            if self.controls_msg_timer > 0 and self.controls_msg:
                self._text(self.font_sm, self.controls_msg, (225, 150, 90), center=(cx, layout["msg_y"]))
            elif self.awaiting_bind:
                self._text(self.font_sm, i18n.t("controls.press_key"), S.COL_UI_DIM, center=(cx, layout["msg_y"]))
        elif page == "language":
            lang_rect = layout["combos"]["language"]["rect"]
            self._text(self.font_sm, i18n.t("settings.language_label"), S.COL_UI_DIM,
                      topleft=(lang_rect.x, lang_rect.y - 22), shadow=False)
            self._draw_combo("language", layout["combos"]["language"])
        elif page == "debug":
            for opt, btn in zip(DEBUG_HUD_OPTIONS, layout["buttons"]):
                self._draw_toggle(btn, self.settings[f"debug_hud_{opt}"])
            if self.settings_return != "menu":
                hint_y = layout["buttons"][-1]["rect"].bottom + 16
                for line in self._wrap_text(i18n.t("settings.debug_menu_only"), self.font_sm, content.w):
                    self._text(self.font_sm, line, S.COL_UI_DIM, topleft=(content.x, hint_y), shadow=False)
                    hint_y += 20

    def _draw_transition(self):
        dur = 2.8
        t = self.transition_timer
        frac = max(0.0, min(1.0, t / dur))
        cx, cy = S.SCREEN_W // 2, S.SCREEN_H // 2

        flicker = random.random() < 0.05
        base = (10, 3, 3) if flicker else (2, 2, 3)
        self.hud_surf.fill(base)

        jx = math.sin(t * 37.0) * 2.0
        jy = math.sin(t * 23.0 + 1.7) * 1.4

        alpha_in = min(1.0, t / 0.5)
        alpha_out = min(1.0, max(0.0, (dur - t) / 0.5))
        alpha = min(alpha_in, alpha_out)

        txt = self._spec_t("descend_text") or "..."
        surf = self.font_lg.render(txt, True, S.COL_TEXT)
        surf.set_alpha(int(255 * alpha))
        self.hud_surf.blit(surf, surf.get_rect(center=(cx + jx, cy - 70 + jy)))

        next_spec = S.FLOOR_SPECS[min(self.floor_i + 1, len(S.FLOOR_SPECS) - 1)]
        sub = self.font_sm.render(i18n.t(next_spec["title"]), True, S.COL_UI_DIM)
        sub.set_alpha(int(255 * alpha))
        self.hud_surf.blit(sub, sub.get_rect(center=(cx + jx * 0.5, cy - 20 + jy * 0.5)))

        shaft_top, shaft_bot = cy + 40, cy + 190
        pygame.draw.line(self.hud_surf, (70, 62, 58), (cx, shaft_top), (cx, shaft_bot), 3)
        marker_y = shaft_top + (shaft_bot - shaft_top) * frac
        glow = pygame.Surface((26, 26), pygame.SRCALPHA)
        pygame.draw.circle(glow, (200, 60, 50, 90), (13, 13), 13)
        self.hud_surf.blit(glow, (cx - 13, int(marker_y) - 13))
        pygame.draw.circle(self.hud_surf, (215, 90, 70), (cx, int(marker_y)), 6)

    def _draw_compass(self):
        cx = S.SCREEN_W // 2
        top, w, h = 14, 280, 24
        rect = pygame.Rect(cx - w // 2, top, w, h)
        self._draw_panel(rect, fill=(10, 9, 8, 130), corner=8)

        deg_per_px = 140.0 / w
        ang_deg = math.degrees(self.player.angle)
        labels = {0: i18n.t("compass.n"), 90: i18n.t("compass.e"), 180: i18n.t("compass.s"), 270: i18n.t("compass.w")}
        for mark_deg in range(0, 360, 15):
            rel = ((mark_deg - ang_deg + 180) % 360) - 180
            if abs(rel) > 70:
                continue
            x = cx + rel / deg_per_px
            major = mark_deg % 90 == 0
            tick_h = 10 if major else 5
            col = (200, 190, 180) if major else (110, 105, 100)
            pygame.draw.line(self.hud_surf, col, (x, top + h - tick_h), (x, top + h), 2 if major else 1)
            if major:
                self._text(self.font_sm, labels[mark_deg], (215, 205, 190), center=(x, top + 8), shadow=False)
        pygame.draw.polygon(self.hud_surf, (210, 70, 70), [(cx - 5, top - 2), (cx + 5, top - 2), (cx, top + 6)])

    @staticmethod
    def _vu_color(level):
        stops = ((0.0, (60, 200, 90)), (0.45, (205, 195, 60)), (0.72, (215, 140, 45)), (1.0, (205, 55, 45)))
        level = max(0.0, min(1.0, level))
        for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
            if level <= t1 or t1 == stops[-1][0]:
                t = 0.0 if t1 == t0 else (level - t0) / (t1 - t0)
                t = max(0.0, min(1.0, t))
                return tuple(int(c0[i] + (c1[i] - c0[i]) * t) for i in range(3))
        return stops[-1][1]

    def _draw_mic_vu(self, x, y, h):
        w = 16
        level = self.mic_vu_level
        pygame.draw.rect(self.hud_surf, (32, 29, 27), (x, y, w, h), border_radius=3)
        fill_h = int(h * max(0.0, min(1.0, level)))
        if fill_h > 0:
            col = self._vu_color(level)
            fill_rect = pygame.Rect(x, y + h - fill_h, w, fill_h)
            pygame.draw.rect(self.hud_surf, col, fill_rect, border_radius=3)
        pygame.draw.rect(self.hud_surf, (95, 88, 82), (x, y, w, h), width=1, border_radius=3)
        for frac in (0.45, 0.72):
            ty = y + h - int(h * frac)
            pygame.draw.line(self.hud_surf, (60, 55, 52), (x, ty), (x + w, ty), 1)

    def _draw_hud(self):
        p = self.player
        pad = 22
        row_h = 36
        bar_w, bar_h = 196, 14

        fl_label = i18n.t("hud.flashlight")
        st_label = i18n.t("hud.stamina")
        sanity_label = i18n.t("hud.sanity")
        label_x_offset = pad + 16 - (pad - 12)
        max_label_w = max(self.font_sm.size(t)[0] for t in (fl_label, st_label, sanity_label))
        panel_w = max(bar_w + 44, max_label_w + label_x_offset + 10)
        panel_h = row_h * 3 + 18
        panel_x, panel_y = pad - 12, S.SCREEN_H - pad - panel_h
        self._draw_panel(pygame.Rect(panel_x, panel_y, panel_w, panel_h),
                          fill=(10, 9, 8, 150), corner=12)

        def bar(row, frac, color, icon_color, label):
            bx = pad
            top = panel_y + 12 + row * row_h
            icon_r = pygame.Rect(bx, top + 1, 10, 10)
            pygame.draw.rect(self.hud_surf, icon_color, icon_r, border_radius=2)
            self._text(self.font_sm, label, S.COL_UI_DIM, topleft=(bx + 16, top - 4), shadow=False)
            by = top + 17
            pygame.draw.rect(self.hud_surf, (32, 29, 27), (bx, by, bar_w, bar_h), border_radius=3)
            pygame.draw.rect(self.hud_surf, color, (bx, by, int(bar_w * max(0.0, min(1.0, frac))), bar_h), border_radius=3)
            pygame.draw.rect(self.hud_surf, (95, 88, 82), (bx, by, bar_w, bar_h), width=1, border_radius=3)

        fl_col = (60, 200, 90) if p.battery > S.FLASHLIGHT_LOW else (210, 70, 40)
        bar(0, p.battery / 100.0, fl_col, fl_col, fl_label)
        st_col = (150, 60, 60) if p.stamina_locked else ((110, 170, 205) if p.is_crouching else (205, 175, 60))
        bar(1, p.stamina / 100.0, st_col, st_col, st_label)
        bar(2, p.sanity / 100.0, (150, 130, 205), (150, 130, 205), sanity_label)
        self._draw_compass()

        if self.settings.get("mic_enabled") and self.mic.available:
            self._draw_mic_vu(panel_x + panel_w + 16, panel_y, panel_h)

        san_frac = p.sanity / S.SANITY_MAX
        if san_frac < 0.35:
            pulse = 0.5 + 0.5 * math.sin(pygame.time.get_ticks() * 0.006)
            intensity = min(1.0, (1 - san_frac) * pulse)
            self.sanity_vignette.set_alpha(int(255 * intensity))
            self.hud_surf.blit(self.sanity_vignette, (0, 0))

        exit_req = self.spec.get("exit_requires_item")
        info_w = 350
        info_h = 58 + (22 if exit_req else 0)
        info_x, info_y = S.SCREEN_W - pad - info_w, pad - 10
        self._draw_panel(pygame.Rect(info_x, info_y, info_w, info_h),
                          fill=(10, 9, 8, 150), corner=12)
        text_max_w = info_w - 28
        carried_txt = i18n.t("hud.carried", label=self._spec_t("collectible_label").capitalize(), n=p.carried)
        carried_txt = self._ellipsize(carried_txt, self.font_sm, text_max_w)
        self._text(self.font_sm, carried_txt, S.COL_TEXT, topleft=(info_x + 14, info_y + 10), shadow=False)
        if self.panel_prop is not None:
            powered_word = self._spec_t("panel_powered_text", "hud.powered")
            status = powered_word if self.panel_prop.powered else i18n.t(
                "hud.installed_count", n=self.panel_prop.installed, total=self.spec["n_collectible"])
            panel_txt = i18n.t("hud.panel_status", label=self._spec_t("panel_label").capitalize(), status=status)
            panel_txt = self._ellipsize(panel_txt, self.font_sm, text_max_w)
            self._text(self.font_sm, panel_txt,
                       S.COL_UI_DIM, topleft=(info_x + 14, info_y + 32), shadow=False)
        if exit_req:
            have = getattr(p, f"has_{exit_req}", False)
            label = self._spec_t("exit_requires_item_label") or exit_req.capitalize()
            col = (110, 200, 130) if have else S.COL_UI_DIM
            status = i18n.t("hud.have") if have else i18n.t("hud.dont_have")
            exit_txt = self._ellipsize(i18n.t("hud.exit_req", label=label, status=status), self.font_sm, text_max_w)
            self._text(self.font_sm, exit_txt, col,
                       topleft=(info_x + 14, info_y + 54), shadow=False)

        if self.scare_flash_timer > 0:
            fx.draw_flash(self.hud_surf, (5, 0, 0), 235 * min(1.0, self.scare_flash_timer / 0.14))

        cx, cy = S.SCREEN_W // 2, S.SCREEN_H // 2
        if not p.is_hiding:
            pygame.draw.circle(self.hud_surf, (210, 205, 195), (cx, cy), 2)

        if self.interact_feedback_timer > 0:
            frac = max(0.0, min(1.0, self.interact_feedback_timer / 0.3))
            for ang_deg in (20, 100, 160, 230, 300, 340):
                ang = math.radians(ang_deg)
                r0, r1 = 6 + (1 - frac) * 14, 6 + (1 - frac) * 14 + 10 * frac
                x0, y0 = cx + math.cos(ang) * r0, cy + math.sin(ang) * r0
                x1, y1 = cx + math.cos(ang) * r1, cy + math.sin(ang) * r1
                pygame.draw.line(self.hud_surf, (230, 200, 120), (x0, y0), (x1, y1), 2)

        if self.install_hold_target is not None and self.install_hold_t > 0:
            frac = min(1.0, self.install_hold_t / S.INSTALL_HOLD_SECONDS)
            self._draw_action_bar(cx, cy + 34, frac, i18n.t("hud.installing"), (205, 175, 60))

        prompt = None if self.hide_transition is not None or self.peek_t > 0.0 else self.prompt_text(self.find_interactable())
        if prompt:
            self._text(self.font_md, prompt, (225, 220, 205), center=(cx, S.SCREEN_H - 150))

        if self.note_timer > 0 and self.note_text and self.peek_t <= 0.0:
            self._draw_note_box(cx)

        if self.hint_timer > 0 and self.hint_text and self.peek_t <= 0.0:
            lines = self.hint_text.split("\n")
            yy = S.SCREEN_H - 190 - (len(lines) - 1) * 13
            for line in lines:
                self._text(self.font_md, line, (210, 170, 90), center=(cx, yy))
                yy += 26

        if self.floor_banner_timer > 0:
            alpha = 255 if self.floor_banner_timer > 1.0 else int(255 * self.floor_banner_timer)
            surf = self.font_lg.render(self.floor_banner, True, S.COL_TEXT)
            surf.set_alpha(alpha)
            r = surf.get_rect(center=(cx, 70))
            self.hud_surf.blit(surf, r)

    def _draw_note_box(self, cx):
        title = i18n.t("note.title")
        pad_x, pad_top, pad_bottom = 20, 34, 16
        line_h = self.font_note.get_linesize()
        max_box_w = min(820, S.SCREEN_W - 220)
        lines = self._wrap_text(self.note_text, self.font_note, max_box_w - pad_x * 2)

        content_w = max([self.font_note.size(line)[0] for line in lines] +
                         [self.font_md.size(title)[0]])
        box_w = min(max_box_w, max(380, content_w + pad_x * 2))
        box_h = pad_top + line_h * len(lines) + pad_bottom
        box_x = cx - box_w // 2
        box_bottom = S.SCREEN_H - 175
        box_y = box_bottom - box_h

        t_elapsed = S.NOTE_DISPLAY_SECONDS - self.note_timer
        alpha = max(0.0, min(1.0, t_elapsed / 0.3, self.note_timer / 0.6))
        if alpha <= 0.0:
            return

        note_surf = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
        self._draw_panel(pygame.Rect(0, 0, box_w, box_h), fill=(10, 9, 8, 215), corner=14, target=note_surf)
        pygame.draw.line(note_surf, (150, 55, 50), (pad_x, 30), (box_w - pad_x, 30), 1)
        note_surf.blit(self.font_md.render(title, True, (225, 205, 150)), (pad_x, 9))
        yy = pad_top
        for line in lines:
            note_surf.blit(self.font_note.render(line, True, (215, 205, 185)), (pad_x, yy))
            yy += line_h
        note_surf.set_alpha(int(255 * alpha))
        self.hud_surf.blit(note_surf, (box_x, box_y))

    def _draw_action_bar(self, cx, y, frac, label, color):
        w, h = 220, 12
        self._text(self.font_sm, label, S.COL_TEXT, center=(cx, y - 12), shadow=True)
        rect = pygame.Rect(cx - w // 2, y, w, h)
        pygame.draw.rect(self.hud_surf, (28, 25, 24), rect, border_radius=4)
        fill = pygame.Rect(rect.x, rect.y, int(rect.w * max(0.0, min(1.0, frac))), rect.h)
        pygame.draw.rect(self.hud_surf, color, fill, border_radius=4)
        pygame.draw.rect(self.hud_surf, (95, 88, 82), rect, width=1, border_radius=4)

    def _restart_from_end(self):
        if self._in_debug_preview():
            self._start_debug_level()
        else:
            self.new_game()
            self._begin_playing()

    def _end_buttons(self, y0):
        cx = S.SCREEN_W // 2
        w, h, gap = 230, 52, 18
        return [
            self._button((cx - w - gap // 2, y0, w, h), i18n.t("end.restart"), self._restart_from_end),
            self._button((cx + gap // 2, y0, w, h), i18n.t("end.to_menu"), self._to_menu),
        ]

    def _stats_extra_text(self):
        return i18n.t("end.stats_extra", notes=self.stats["notes"], batteries=self.stats["batteries"],
                      scares=self.stats["scares"])

    def _draw_gameover(self, title, color, subtitle):
        overlay = pygame.Surface((S.SCREEN_W, S.SCREEN_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 235))
        self.hud_surf.blit(overlay, (0, 0))
        cx = S.SCREEN_W // 2
        self._text(self.font_title, title, color, center=(cx, S.SCREEN_H // 2 - 70))
        self._text(self.font_md, subtitle, S.COL_UI_DIM, center=(cx, S.SCREEN_H // 2 - 10))
        mins, secs = divmod(int(self.elapsed), 60)
        stats = i18n.t("end.survived", time=f"{mins:02d}:{secs:02d}", floor=self.floor_i + 1, total=len(S.FLOOR_SPECS))
        self._text(self.font_sm, stats, S.COL_UI_DIM, center=(cx, S.SCREEN_H // 2 + 40))
        self._text(self.font_sm, self._stats_extra_text(), S.COL_UI_DIM, center=(cx, S.SCREEN_H // 2 + 65))
        for btn in self._end_buttons(S.SCREEN_H // 2 + 105):
            self._draw_button(btn)

    def _draw_win(self):
        overlay = pygame.Surface((S.SCREEN_W, S.SCREEN_H), pygame.SRCALPHA)
        overlay.fill((6, 8, 6, 225))
        self.hud_surf.blit(overlay, (0, 0))
        cx = S.SCREEN_W // 2
        self._text(self.font_title, i18n.t("win.title"), (90, 200, 140), center=(cx, S.SCREEN_H // 2 - 90))
        self._text(self.font_md, i18n.t("win.subtitle"), S.COL_UI_DIM,
                   center=(cx, S.SCREEN_H // 2 - 30))
        mins, secs = divmod(int(self.elapsed), 60)
        self._text(self.font_sm, i18n.t("win.time", time=f"{mins:02d}:{secs:02d}"), S.COL_UI_DIM, center=(cx, S.SCREEN_H // 2 + 20))
        self._text(self.font_sm, self._stats_extra_text(), S.COL_UI_DIM, center=(cx, S.SCREEN_H // 2 + 50))
        for btn in self._end_buttons(S.SCREEN_H // 2 + 85):
            self._draw_button(btn)

    def run(self):
        while self.running:
            dt = self.clock.tick(self.settings["fps_limit"]) / 1000.0
            self.handle_events()
            self.update(dt)
            self.draw()
        self.mic.stop()
        pygame.quit()
        if self._next_mode == "editor":
            return "editor"
        sys.exit(0)
