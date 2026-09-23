import json
import math
import os
import random
import sys
import time
import webbrowser
from pathlib import Path

import moderngl
import numpy as np
import pygame

from game import settings as S
from game import debug_console
from game import ui
from game import fx
from game import screamer as screamer_mod
from game import i18n
from game.audio import SoundBank
from game.maze import Maze
from game.mic import MicListener
from game.entities import Player, Monster, monster_vision_base, monster_lit_frac
from game.props import (populate_level, populate_yard, populate_debug, populate_micro_yard, populate_micro_room,
                         populate_forest_run, line_blocked_by_cover, make_prop, place_arrival_prop, Prop,
                         cells_no_body_fits)
from game.renderer3d import (Renderer3D, FOV_DEGREES, EYE_HEIGHT, MAP_CURSOR_V_MIN,
                             ELEVATOR_OPENING_HALF as R3D_ELEVATOR_OPENING_HALF,
                             HATCH_HALF as R3D_HATCH_HALF, HATCH_SHAFT_RISE as R3D_HATCH_SHAFT_RISE)
from game.lighting import (SHAPE_DIRECTIONAL, GAMEPLAY_RECEIVER_HEIGHT, gameplay_lights, light_level_at,
                           player_light_levels, flashlight_light, lighter_light)


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
                      "sanity_pill": "pickup.sanity_pill", "lighter": "pickup.lighter",
                      "cutters": "pickup.cutters", "key": "pickup.key",
                      "paper_map": "pickup.paper_map", "pencil": "pickup.pencil",
                      "map_sheet": "pickup.map_sheet"}
PORTAL_LABEL_KEYS = {0: "portal.floor0", 1: "portal.floor1", 2: "portal.floor2", "hub": "portal.hub"}
DEBUG_HUD_OPTIONS = ("fps", "coords", "monster", "seed", "scares", "console")
MENU_ONLY_DEBUG_HUD_OPTIONS = ("coords", "monster", "seed", "scares", "console")
MENU_CAM_LAMP_KINDS = ("wall_sconce", "lamp_desk")
MENU_CAM_LAMP_KINDS_FALLBACK = ("wall_sconce", "lamp_desk", "sign_exit", "monitor")


def _wrap_angle(a):
    return (a + math.pi) % math.tau - math.pi
SETTINGS_TABS = ("graphics", "sound", "controls", "interface", "debug")
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

SPLASH_TITLE_FADE = (0.0, 0.6)
SPLASH_SUBTITLE_FADE = (0.25, 0.6)
SPLASH_HEADPHONES_FADE = (1.3, 0.6)
SPLASH_SKIP_HINT_FADE = (0.3, 0.5)
SPLASH_HOLD_END = 3.0
SPLASH_FADE_OUT_DUR = 0.6
SPLASH_TOTAL_DURATION = SPLASH_HOLD_END + SPLASH_FADE_OUT_DUR
_splash_shown = False

_COMMUNITY_CREATORS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "community_creators.json")


def _load_community_creators():
    try:
        with open(_COMMUNITY_CREATORS_PATH, "r", encoding="utf-8") as f:
            names = json.load(f)
        return [str(n).strip() for n in names if str(n).strip()]
    except Exception:
        return []


COMMUNITY_CREATORS = _load_community_creators()
FLOOR_THEME_SURFACE = {"upper": "tile", "basement": "stone", "yard": "grass"}
SLIDER_SPECS = {
    "master_volume": (0.0, 1.0),
    "sfx_volume": (0.0, 1.0),
    "music_volume": (0.0, 1.0),
    "mouse_sensitivity": (0.4, 2.2),
    "view_distance": (0.6, 1.6),
    "mic_sensitivity": (1.0, 20.0),
    "gamma": (0.75, 1.8),
}

GAMMA_SYMBOL_STEPS = (0.5, 1.0, 2.5)
STEPPED_SLIDERS = {
    "fps_limit": FPS_OPTIONS,
    "quality_preset": S.QUALITY_PRESET_ORDER,
    **S.GFX_SETTING_OPTIONS,
}
GFX_SLIDER_KEYS = tuple(S.GFX_SETTING_OPTIONS)

FONT_PATH = S.FONT_PATH
NOTE_FONT_PATH = S.NOTE_FONT_PATH


class _DemoMonster:
    def __init__(self, start, target, has_locker, speed=1.1, alert_target=0.0, check_seconds=None,
                 locker_target=None):
        self.start = start
        self.target = target
        self.has_locker = has_locker
        self.speed = speed
        self.alert_target = alert_target
        self.check_seconds = check_seconds if check_seconds is not None else S.MONSTER_LOCKER_CHECK_SECONDS
        self.locker_target = locker_target
        self.x, self.y = start
        self.facing = math.atan2(target[1] - start[1], target[0] - start[0])
        self.walk_phase = 0.0
        self.walk_amp = 0.0
        self.alert_level = 0.0
        self.checking_timer = 0.0
        self.checking_timer_total = self.check_seconds
        self.closing_locker = None
        self.closing_timer = 0.0
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
        self.alert_level += (self.alert_target - self.alert_level) * min(1.0, dt * 2.0)
        if self.closing_timer > 0.0:
            self.closing_timer = max(0.0, self.closing_timer - dt)
            if self.closing_timer <= 0.0:
                self.closing_locker = None
        if self.phase == "approach":
            self.phase_t += dt
            t = min(1.0, self.phase_t / self._travel_time())
            self._lerp_pos(self.start, self.target, t)
            self.walk_amp += (1.0 - self.walk_amp) * min(1.0, dt * 8.0)
            self.walk_phase += dt * self.speed * 3.4
            if t >= 1.0:
                if self.has_locker:
                    self.phase, self.phase_t, self.checking_timer = "check", 0.0, self.check_seconds
                else:
                    self.phase, self.phase_t = "pause", 0.0
        elif self.phase == "check":
            self.walk_amp += (0.0 - self.walk_amp) * min(1.0, dt * 8.0)
            self.checking_timer = max(0.0, self.checking_timer - dt)
            if self.locker_target is not None:
                self.facing = math.atan2(self.locker_target.y - self.y, self.locker_target.x - self.x)
            if self.checking_timer <= 0.0:
                self.closing_locker = self.locker_target
                self.closing_timer = S.MONSTER_LOCKER_CLOSE_SECONDS
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
            "master_volume": 0.9,
            "sfx_volume": 1.0,
            "music_volume": 0.6,
            "mic_enabled": False,
            "mic_device": None,
            "mic_sensitivity": S.MIC_LEVEL_SCALE,
            "view_distance": 1.0,
            "gamma": 1.0,
            "quality_preset": "medium",
            **S.QUALITY_PRESETS["medium"],
            "upscale_smoothing": False,
            "vsync": True,
            "language": "en",
            "debug_hud_fps": False,
            "debug_hud_coords": False,
            "debug_hud_monster": False,
            "debug_hud_seed": False,
            "debug_hud_scares": False,
            "debug_hud_console": False,
            "warning_seen": False,
            "hud_style": S.HUD_STYLES[0],
            "compass": True,
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
        self.screen = self._open_display()
        self.clock = pygame.time.Clock()
        self.window_size = (S.SCREEN_W, S.SCREEN_H)
        self.awaiting_bind = None
        self.controls_msg = None
        self.controls_msg_timer = 0.0
        self._reset_map()
        self.dragging_slider = None
        self._settings_save_pending = False
        self.open_combo = None
        if self.settings["fullscreen"]:
            self._try_toggle_fullscreen()
        self._scene_props_cache = None
        self._hud_drawn_at = 0.0
        self._debug_lines = None
        self._debug_lines_at = 0.0
        self._debug_back = None
        self._clip_pending = None
        self._hud_live = True
        self.settings_return = "menu"
        self.settings_page = SETTINGS_TABS[0]
        self.console = debug_console.DebugConsole()

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
        self._mic_device_missing = False
        self._apply_mic_setting()

        self._text_cache = {}
        self.font_title = pygame.font.Font(FONT_PATH, 72)
        self.font_lg = pygame.font.Font(FONT_PATH, 42)
        self.font_md = pygame.font.Font(FONT_PATH, 24)
        self.font_sm = pygame.font.Font(FONT_PATH, 18)
        self.font_note = pygame.font.Font(NOTE_FONT_PATH, 30)
        self.font_note_big = pygame.font.Font(NOTE_FONT_PATH, 62)
        self.font_form = pygame.font.Font(FONT_PATH, 44)
        self.font_form_s = pygame.font.Font(FONT_PATH, 30)
        self.font_type = pygame.font.Font(FONT_PATH, 16)
        self.font_type_s = pygame.font.Font(FONT_PATH, 13)

        self.static_overlay = self._make_static_overlay()
        self.hide_vignette = self._make_hide_vignette()
        self.sanity_vignette = self._make_sanity_vignette()
        self.menu_gradient = self._make_menu_gradient()
        self.pause_gradient = self._make_menu_gradient(max_alpha=125)
        self.angel_glow_surf = self._make_angel_glow()
        self._angel_eye_specs = self._make_angel_eye_specs()
        self._angel_wing_specs = self._make_angel_wing_specs()
        self._angel_vein_specs = self._make_angel_vein_specs()
        self._angel_center_eye_specs = self._make_angel_center_eye_specs()
        self.angel_canvas = pygame.Surface((S.ANGEL_CANVAS_SIZE, S.ANGEL_CANVAS_SIZE), pygame.SRCALPHA)

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
        self._community_scroll_px = 0
        self._community_max_scroll = 0
        self._controls_scroll_px = 0
        self._controls_max_scroll = 0
        self._scroll_surf = None
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
        global _splash_shown
        self._splash_t = 0.0
        self._post_splash_state = self.state
        if not _splash_shown:
            _splash_shown = True
            self.state = "splash"
        if not self.sounds.ch_ambient.get_busy():
            self.sounds.start_ambient()

    @staticmethod
    def _normalize_binding(value):
        if isinstance(value, int):
            return ("key", value)
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return (value[0], value[1])
        return None

    def _load_settings(self):
        path = SETTINGS_PATH if SETTINGS_PATH.exists() else _LEGACY_SETTINGS_PATH
        try:
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            for k in self.settings:
                if k not in saved:
                    continue
                if k == "bindings":
                    if not isinstance(saved["bindings"], dict):
                        continue
                    for action, value in saved["bindings"].items():
                        nb = self._normalize_binding(value)
                        if nb is not None and action in S.DEFAULT_BINDINGS:
                            self.settings["bindings"][action] = nb
                elif self._saved_setting_ok(k, self.settings[k], saved[k]):
                    value = saved[k]
                    if k in SLIDER_SPECS:
                        lo, hi = SLIDER_SPECS[k]
                        value = max(lo, min(hi, value))
                    self.settings[k] = value
            if self.settings.get("hud_style") not in S.HUD_STYLES:
                self.settings["hud_style"] = S.HUD_STYLES[0]
        except Exception:
            pass

    @staticmethod
    def _saved_setting_ok(key, default, value):
        if key in STEPPED_SLIDERS:
            return not isinstance(value, bool) and value in STEPPED_SLIDERS[key]
        if key == "language":
            return value in i18n.LOCALES
        if isinstance(default, bool):
            return isinstance(value, bool)
        if isinstance(default, (int, float)):
            return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        if default is None:
            return value is None or isinstance(value, str)
        return isinstance(value, type(default))

    def _save_settings(self):
        try:
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = SETTINGS_PATH.with_suffix(SETTINGS_PATH.suffix + ".new")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.settings, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, SETTINGS_PATH)
        except Exception:
            pass

    def _scene_props(self):
        props, doors = self.props, self.doors
        cache = self._scene_props_cache
        if (cache is not None and cache[0] is props and cache[1] == len(props)
                and cache[2] is doors and cache[3] == len(doors)):
            return cache[4]
        merged = props + doors
        self._scene_props_cache = (props, len(props), doors, len(doors), merged)
        return merged

    def _moon_strength(self):
        m = self.spec.get("moon_strength", 0.0) if self.spec else 0.0
        if not m and self.maze is not None and self.maze.has_outdoors():
            return S.OUTER_WINDOW_MOON
        return m

    def _apply_mic_setting(self):
        if self.settings.get("mic_enabled") and self.mic.available:
            preferred = self.settings.get("mic_device")
            if preferred is not None and not isinstance(preferred, str):
                preferred = None
                self.settings["mic_device"] = None
            self.mic.refresh_devices()
            self.mic.start(preferred)
        else:
            self.mic.stop()

    def _update_mic_level(self, dt):
        self._mic_device_missing = self.mic.device_missing
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

    def _make_angel_glow(self):
        size = 640
        yy, xx = np.mgrid[0:size, 0:size]
        cx = cy = size / 2.0
        dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / (size / 2.0)
        t = np.clip(1.0 - dist, 0.0, 1.0)
        alpha = (t ** 1.6 * 255.0).astype(np.uint8)
        surf = pygame.Surface((size, size), pygame.SRCALPHA)
        surf.fill((255, 248, 222, 255))
        alpha_view = pygame.surfarray.pixels_alpha(surf)
        alpha_view[:, :] = alpha.T
        del alpha_view
        return surf

    def _make_angel_eye_specs(self):
        rng = random.Random(0xA4732)
        specs = []
        for _ in range(S.ANGEL_EYE_COUNT):
            ang = rng.uniform(0, math.tau)
            rad = rng.uniform(0.2, 1.4)
            phase = rng.uniform(0, math.tau)
            rate = rng.uniform(1.2, 3.4)
            size = rng.uniform(0.55, 1.15)
            jitter_phase = rng.uniform(0, math.tau)
            specs.append((ang, rad, phase, rate, size, jitter_phase))
        return specs

    def _make_angel_wing_specs(self):
        rng = random.Random(0x71E63)
        specs = []
        for i in range(S.ANGEL_WING_COUNT):
            base_ang = math.tau * i / S.ANGEL_WING_COUNT + rng.uniform(-0.08, 0.08)
            length = rng.uniform(0.85, 1.15)
            flap_phase = rng.uniform(0, math.tau)
            flap_rate = rng.uniform(1.5, 2.3)
            n_feathers = rng.randint(6, 8)
            layer_offset = rng.uniform(-0.09, 0.09) * rng.choice((-1.0, 1.0))
            specs.append((base_ang, length, flap_phase, flap_rate, n_feathers, layer_offset))
        return specs

    def _make_angel_vein_specs(self):
        rng = random.Random(0x9CE41)
        return [(rng.uniform(0, math.tau), rng.uniform(0.55, 1.0), rng.uniform(0, math.tau))
                for _ in range(12)]

    def _make_angel_center_eye_specs(self):
        rng = random.Random(0x0C1E5)
        specs = []
        for i in range(18):
            ang = math.tau * i / 18 + rng.uniform(-0.22, 0.22)
            rad = rng.uniform(0.0, 1.0) ** 0.6
            size = rng.uniform(0.55, 1.05)
            phase = rng.uniform(0, math.tau)
            rate = rng.uniform(1.1, 2.6)
            specs.append((ang, rad, size, phase, rate))
        return specs

    def new_game(self):
        self.sounds.stop_all_threat_audio()
        self.sounds.stop_angel_choir(fade_ms=200)
        self.player = Player(0.0, 0.0)
        self.debug_demo_monsters = []
        self.debug_door_monster = None
        self.debug_door_monster_door = None
        self._debug_door_goal = None
        self._debug_door_ends = None
        self._debug_door_corridor_set = None
        self.anim_t = 0.0
        self.frame_dt = 1.0 / 60.0
        self._load_menu_scene()

        self.anomaly_roll = random.choices(S.ANOMALY_ROLLS,
                                           weights=S.ANOMALY_ROLL_WEIGHTS)[0]
        self.elapsed = 0.0
        self.dread = 0.0
        self._dread_time_frac = 0.0
        self._dread_progress_frac = 0.0
        self._scare_seconds_to_fill = 14.0
        self._scare_lit = False
        self.note_text = None
        self.note_back_text = None
        self.note_back_kind = None
        self.note_return_state = "playing"
        self._note_fade = 0.0
        self._note_closing = False
        self._note_settling = False
        self.note_spin = [0.0, 0.0]
        self._note_dirty = False
        self.hint_text = None
        self.hint_timer = 0.0
        self.floor_banner = self._spec_text("title")
        self.floor_banner_timer = 4.0
        self.scare_progress = 0.0
        self.scare_target = random.uniform(0.85, 1.15)
        self.scare_cooldown = 0.0
        self.scare_source = None
        self.hallu_progress = 0.0
        self.hallu_target = random.uniform(0.85, 1.15)
        self.hallu_cooldown = 0.0
        self.hallu_active = []
        self.hallu_intensity = 0.0
        self.sanity_boost_timer = 0.0
        self.trip_intensity = 0.0
        self.comedown_intensity = 0.0
        self._comedown_remaining = 0.0
        self._trip_cam_yaw = self.player.angle
        self._trip_cam_pitch = self.player.pitch
        self._trip_prev_player_angle = self.player.angle
        self._trip_target_unwrapped = self.player.angle
        self._trip_follower_unwrapped = self.player.angle
        self._dark_time_t = 0.0
        self.fx_shake = fx.ScreenShake()
        self.catch_timer = 0.0
        self.hide_transition = None
        self.hide_vignette_t = 0.0
        self.elevator_ride = None
        self.hatch_climb = None
        self._debug_transition_test = False
        self._debug_spectator = False
        self.catch_cam = None
        self.elevator_called = False
        self.elevator_call_t = 0.0
        self.elevator_arrived = False
        self.elevator_guard_idle_t = 0.0
        self._elevator_lit_index = 0
        self.hatch_turn_progress = 0.0
        self._hatch_wheel_coast = 0.0
        self.hatch_turning = False
        self._hatch_creak_timer = 0.0
        self.fence_cut_progress = 0.0
        self._cutters_break_count = 0
        self.fence_cutting = False
        self.fence_escape = None
        self.angel_seq = None
        self._angel_roll_done = False
        self._battery_warned = False
        self.stats = {"notes": 0, "batteries": 0, "scares": 0}
        self._reset_map()
        self.scare_flash_timer = 0.0
        self.interact_feedback_timer = 0.0
        self._reset_interaction_state()
        self._lean_input_grace = 0.0
        self.confirm_return = "menu"
        self.state = "menu"

    def _reset_interaction_state(self):
        self.install_hold_target = None
        self.install_hold_t = 0.0
        self.cutters_repair_target = None
        self.cutters_repair_t = 0.0
        self._cutters_break_timer = 0.0
        self._cutters_grace_remaining = 0.0
        self.peek_hold_target = None
        self.peek_hold_t = 0.0
        self.is_peeking = False
        self.peek_t = 0.0
        self.peek_door = None
        self._lean_prev_left = False
        self._lean_prev_right = False
        self._lean_active_dir = 0

    def _install_level(self, spec, default_theme="upper"):
        self.props.sort(key=lambda p: (p.kind, p.texture or ""))
        self.renderer.build_level(self.maze, theme=spec.get("floor_theme", default_theme),
                                  **self._hatch_holes())
        sx, sy = self.maze.start
        self.player.x, self.player.y = sx, sy
        self.player.angle = random.uniform(0, math.tau)
        self.player.pitch = 0.0
        self.player.carried = 0
        self.player.is_hiding = False
        self.player.hidden_in = None

    def _inert_monster(self, cell, seed):
        return Monster(
            cell[0] + 0.5, cell[1] + 0.5, self.maze,
            rng=random.Random(seed ^ 0xB0B0),
            speed_mult=0.0, vision_mult=0.0,
            blocked_cells=set(), lockers=[], doors=self.doors,
            blocked_prop_candidates=[], dead_end_lockers=[],
        )

    _FLOOR_TRIES = 6

    def _build_floor_scene(self, spec, room_count_range=None):
        layout = spec.get("layout", "corridor")
        stage = S.anomaly_stage(getattr(self, "anomaly_roll", S.ANOMALY_DEFAULT_STAGE),
                                spec["key"])
        self.anomaly_stage = stage

        def build(seed):
            if layout == "yard":
                return Maze(w=S.YARD_W, h=S.YARD_H, seed=seed, layout="yard",
                            anomaly=stage)
            return Maze(seed=seed, wall_bias=spec["wall_bias"], template_floor=spec.get("floor_theme"),
                         room_count_range=room_count_range or spec.get("room_count"),
                         anomaly=stage)

        min_cells = 0 if room_count_range else spec.get("min_cells", 0)
        best = None
        for _ in range(self._FLOOR_TRIES if min_cells else 1):
            seed = random.randrange(1 << 30)
            maze = build(seed)
            size = len(maze.floor_cells())
            if best is None or size > best[0]:
                best = (size, seed, maze)
            if size >= min_cells:
                break
        _size, seed, maze = best
        self.floor_seed = seed
        self.maze = maze
        rng = random.Random(seed ^ 0x5EED)
        if getattr(self.player, "has_map", False) and spec.get("n_maps"):
            spec = dict(spec, n_maps=0)
        if layout == "yard":
            self.props, self.panel_prop, self.exit_prop, monster_cell, self.doors = populate_yard(
                self.maze, spec, rng, stage=stage)
        else:
            self.props, self.panel_prop, self.exit_prop, monster_cell, self.doors = populate_level(
                self.maze, spec, rng, stage=stage)
        self._install_level(spec)

        monster_blocked = cells_no_body_fits(
            [p for p in self.props if not p.wall_mounted or p.kind == "locker"],
            S.MONSTER_RADIUS)
        lockers = [p for p in self.props if p.kind == "locker" and not p.locker_blocked]
        self.monster = Monster(
            monster_cell[0] + 0.5, monster_cell[1] + 0.5, self.maze,
            rng=random.Random(seed ^ 0xB0B0),
            speed_mult=spec["speed_mult"], vision_mult=spec["vision_mult"],
            hearing_mult=spec.get("hearing_mult", 1.0),
            vision_light_norm=spec.get("vision_light_norm", S.MONSTER_VISION_LIGHT_NORM),
            blocked_cells=monster_blocked,
            lockers=lockers,
            doors=self.doors,
            blocked_prop_candidates=[p for p in self.props if not p.wall_mounted or p.kind == "locker"],
            dead_end_lockers=self.maze.dead_end_lockers(lockers),
            exit_cell=self.exit_prop.interact_cell if self.exit_prop is not None else None,
        )

    def _load_menu_scene(self):
        self.floor_i = 0
        self.spec = dict(S.FLOOR_SPECS[0])
        self.spec["broken_light_chance"] = S.MENU_BROKEN_LIGHT_CHANCE
        self._floor_music_pending = False
        self._build_floor_scene(self.spec, room_count_range=S.MENU_ROOM_COUNT)
        self._menu_cam = self._pick_menu_camera()
        self.player.x, self.player.y = self._menu_cam[0], self._menu_cam[1]
        self.player.peek_x, self.player.peek_y = self.player.x, self.player.y
        self.player.angle = self._menu_cam[2]
        self.player.pitch = 0.0

    def _pick_menu_camera(self):
        maze, props = self.maze, self.props
        lamps = [p for p in props if p.light_radius and not p.broken
                 and p.kind in MENU_CAM_LAMP_KINDS]
        if not lamps:
            lamps = [p for p in props if p.light_radius and not p.broken
                     and p.kind in MENU_CAM_LAMP_KINDS_FALLBACK]
        if not lamps:
            for p in props:
                if p.light_radius and p.kind in MENU_CAM_LAMP_KINDS_FALLBACK:
                    p.broken = False
                    lamps = [p]
                    break
        cx, cy = maze.room_center_near((int(self.player.x), int(self.player.y)))
        fallback = (cx + 0.5, cy + 0.5, self.player.angle)
        if not lamps:
            return fallback

        tan_h = math.tan(math.radians(FOV_DEGREES) / 2.0) * (self.renderer.low_w / self.renderer.low_h)
        off = math.atan(S.MENU_CAM_NDC_X * tan_h)
        near, far = S.MENU_CAM_DIST
        best, best_score = fallback, -1.0
        for lamp in lamps:
            lx, ly = lamp.x, lamp.y
            for iy in range(int(ly - far), int(ly + far) + 1):
                for ix in range(int(lx - far), int(lx + far) + 1):
                    if not maze.is_walkable_cell(ix, iy):
                        continue
                    x, y = ix + 0.5, iy + 0.5
                    dist = math.hypot(lx - x, ly - y)
                    if not near <= dist <= far:
                        continue
                    if not maze.has_line_of_sight(x, y, lx, ly):
                        continue
                    yaw = math.atan2(ly - y, lx - x) - off
                    if not self._menu_cam_fits(x, y, yaw):
                        continue
                    score = self._menu_shot_score(x, y, yaw)
                    if score > best_score:
                        best, best_score = (x, y, yaw), score
        return best

    def _menu_cam_fits(self, x, y, yaw):
        r = S.PLAYER_RADIUS + 0.12
        fx, fy = math.cos(yaw), math.sin(yaw)
        d = S.MENU_CAM_DOLLY
        for ox, oy in ((0.0, 0.0), (-fy * d, fx * d), (fy * d, -fx * d),
                       (fx * d, fy * d), (-fx * d, -fy * d)):
            if self.player._collides(self.maze, self.props, x + ox, y + oy, r):
                return False
        return True

    def _menu_shot_score(self, x, y, yaw):
        maze = self.maze
        tan_h = math.tan(math.radians(FOV_DEGREES) / 2.0) * (self.renderer.low_w / self.renderer.low_h)
        score, centre_reach = 0.0, 0.0
        for ndc, weight in ((-0.2, 0.3), (0.25, 0.8), (0.6, 1.0), (0.9, 0.7)):
            ang = yaw + math.atan(ndc * tan_h)
            dx, dy = math.cos(ang), math.sin(ang)
            reach = 0.0
            for d in (0.8, 1.6, 2.4, 3.4, 4.6, 6.0, 7.5, 9.0):
                px, py = x + dx * d, y + dy * d
                if maze.blocks_sight(px, py):
                    break
                if line_blocked_by_cover(self.props, x, y, px, py, min_height=0.5):
                    break
                reach = d
                if d <= 6.0:
                    score += weight * self._light_level_at(px, py)
            score += weight * reach * 0.05
            if ndc == 0.25:
                centre_reach = reach
        if centre_reach < 2.2:
            score -= 2.0
        return score

    def _sync_menu_player(self):
        override = self._menu_camera_override()
        if override is None:
            return
        (ex, ey, _ez), yaw, _pitch, _fov = override
        p = self.player
        p.x, p.y = ex, ey
        p.peek_x, p.peek_y = ex, ey
        p.angle = yaw

    def _load_floor(self, index):
        t_load = time.perf_counter()
        spec = S.FLOOR_SPECS[index]
        self.floor_i = index
        self.spec = spec
        self._floor_music_pending = True
        if self._debug_transition_test:
            if spec.get("layout") == "yard":
                self._build_micro_yard_scene(spec)
            else:
                self._build_micro_room_scene(spec)
        else:
            self._build_floor_scene(spec)
        self._door_break_sfx_timer = 0.0
        self.floor_elapsed = 0.0
        self.dread = 0.0
        self._dread_time_frac = 0.0
        self._dread_progress_frac = 0.0
        self.scare_progress = 0.0
        self.scare_cooldown = S.SCARE_MIN_GAP
        self.hallu_progress = 0.0
        self.floor_banner = self._spec_text("title")
        self.floor_banner_timer = 4.0
        self.hint_text = self._spec_text("intro")
        self.hint_timer = 6.0
        if self._debug_transition_test:
            self._apply_debug_transition_test_state()
        self.floor_load_ms = (time.perf_counter() - t_load) * 1000.0

    def _hatch_holes(self):
        hatch = next((q for q in self.props if q.kind == "hatch"), None)
        if hatch is not None:
            return {"ceiling_hole": (hatch.x, hatch.y, R3D_HATCH_HALF), "sky": True}
        arrival = next((q for q in self.props if q.kind == "hatch_arrival"), None)
        if arrival is not None:
            return {"floor_hole": (arrival.x, arrival.y, R3D_HATCH_HALF)}
        return {}

    def _build_micro_yard_scene(self, spec):
        seed = random.randrange(1 << 30)
        self.floor_seed = seed
        self.maze = Maze(w=19, h=19, seed=seed, layout="micro_yard")
        rng = random.Random(seed ^ 0x5EED)
        self.props, self.panel_prop, self.exit_prop, monster_cell, self.doors = populate_micro_yard(self.maze, rng)
        self._install_level(spec, "yard")

        self.monster = self._inert_monster(monster_cell, seed)

    def _build_micro_room_scene(self, spec):
        seed = random.randrange(1 << 30)
        self.floor_seed = seed
        self.maze = Maze(w=11, h=11, seed=seed, layout="micro_room")
        rng = random.Random(seed ^ 0x5EED)
        self.props, self.panel_prop, self.exit_prop, monster_cell, self.doors = populate_micro_room(
            self.maze, spec["exit_prop"], rng)
        self._install_level(spec)

        self.monster = self._inert_monster(monster_cell, seed)

    def _apply_debug_transition_test_state(self):
        if self.panel_prop is not None:
            self.panel_prop.installed = self.spec.get("n_collectible", 0)
            self.panel_prop.powered = True
        if self.exit_prop is not None:
            self.exit_prop.powered = True
        p = self.player
        p.battery = 100.0
        if self.elevator_ride is None and self.hatch_climb is None:
            p.flashlight_on = True
        p.has_lighter = True
        p.has_cutters = True
        self.spec = dict(self.spec)
        self.spec["no_threat"] = True

    def _fly_move(self, dt, mouse_dx, mouse_dy):
        p = self.player
        p.angle = (p.angle + mouse_dx * S.MOUSE_SENSITIVITY) % (2 * math.pi)
        p.pitch = max(-S.PITCH_LIMIT, min(S.PITCH_LIMIT, p.pitch - mouse_dy * S.MOUSE_SENSITIVITY_Y))
        fast = self._binding_down("sprint")
        speed = S.DEBUG_SPECTATOR_SPRINT if fast else S.DEBUG_SPECTATOR_SPEED
        fwd = (1.0 if self._binding_down("forward") else 0.0) - (1.0 if self._binding_down("back") else 0.0)
        side = (1.0 if self._binding_down("right") else 0.0) - (1.0 if self._binding_down("left") else 0.0)
        if fwd or side:
            n = math.hypot(fwd, side) or 1.0
            fx, fy = math.cos(p.angle), math.sin(p.angle)
            p.x += (fx * fwd - fy * side) / n * speed * dt
            p.y += (fy * fwd + fx * side) / n * speed * dt
        keys = pygame.key.get_pressed()
        rise = ((1.0 if keys[pygame.K_SPACE] else 0.0)
                - (1.0 if self._binding_down("crouch") else 0.0))
        if rise:
            p.fly_z = max(S.DEBUG_SPECTATOR_Z_MIN,
                          min(S.DEBUG_SPECTATOR_Z_MAX, p.fly_z + rise * S.DEBUG_SPECTATOR_RISE * dt))
        p.peek_x, p.peek_y = p.x, p.y
        p.moved_this_frame = False
        p.noise_radius = 0.0
        p.lean_t = 0.0
        p.is_crouching = False
        p.is_sprinting = False
        p.stamina = S.STAMINA_MAX
        self._trip_cam_yaw, self._trip_cam_pitch = p.angle, p.pitch

    def _start_spectator_level(self):
        self._debug_spectator = True
        self._load_floor(0)
        self._begin_playing()
        self.spec = dict(self.spec, ambient_level=S.DEBUG_SPECTATOR_AMBIENT,
                         fog_dist=S.DEBUG_SPECTATOR_FOG, grace=0.0)
        p = self.player
        p.fly_z = 0.0
        p.flashlight_on = False
        p.lighter_on = False
        p.map_open = False
        p.active_held_item = None
        p.equip_t = 0.0
        p.sanity = S.SANITY_MAX
        self.floor_banner = i18n.Text("debug.spectator_banner")
        self.floor_banner_timer = 4.0
        self.hint_text = i18n.Text("debug.spectator_hint")
        self.hint_timer = 8.0

    def _start_transition_test_level(self):
        self._debug_transition_test = True
        self._load_floor(0)
        self._begin_playing()
        self.floor_banner = i18n.Text("debug.transition_test_banner")
        self.floor_banner_timer = 4.0
        self.hint_text = i18n.Text("debug.transition_test_hint")
        self.hint_timer = 6.0

    def _start_debug_level(self):
        self._build_debug_scene()
        self._begin_playing()

    def _build_debug_scene(self):
        self.player = Player(0.0, 0.0)
        self._reset_map()
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

        debug_lockers = [p for p in self.props if p.kind == "locker" and not p.locker_blocked]
        self.monster = Monster(
            monster_cell[0] + 0.5, monster_cell[1] + 0.5, self.maze,
            rng=random.Random(1), speed_mult=0.0, vision_mult=0.0,
            blocked_cells=set(), lockers=debug_lockers, doors=self.doors,
            blocked_prop_candidates=[p for p in self.props if not p.wall_mounted or p.kind == "locker"],
            dead_end_lockers=self.maze.dead_end_lockers(debug_lockers),
        )
        self.debug_demo_monsters = [
            _DemoMonster(**spec) for spec in getattr(self.maze, "demo_monster_spots", [])
        ]
        self.debug_door_monster = None
        self.debug_door_monster_door = None
        self._debug_door_goal = None
        self._debug_door_ends = None
        self._debug_door_corridor_set = None
        corridor = getattr(self.maze, "door_demo_corridor", None)
        if corridor:
            all_floor = set(self.maze.floor_cells())
            corridor_set = set(corridor)
            end_a, end_b = corridor[0], corridor[-1]
            door_cell = corridor[len(corridor) // 2]
            self.debug_door_monster_door = next(
                (d for d in self.doors if d.cell == door_cell), None)
            self._debug_door_ends = (end_a, end_b)
            self._debug_door_corridor_set = corridor_set
            self.debug_door_monster = Monster(
                end_a[0] + 0.5, end_a[1] + 0.5, self.maze,
                rng=random.Random(11), speed_mult=1.0, vision_mult=0.0,
                blocked_cells=all_floor - corridor_set, lockers=[], doors=self.doors,
                blocked_prop_candidates=[],
            )
        self._door_break_sfx_timer = 0.0
        self.floor_elapsed = 0.0
        self.elapsed = 0.0
        self.dread = 0.0
        self.stats = {"notes": 0, "batteries": 0, "scares": 0}
        self._reset_interaction_state()
        self.floor_banner = self._spec_text("title")
        self.floor_banner_timer = 4.0
        self.hint_text = i18n.Text("debug.hint")
        self.hint_timer = 6.0

    def _begin_playing(self):
        self.state = "playing"
        pygame.mouse.set_visible(False)
        pygame.event.set_grab(True)
        pygame.mouse.get_rel()
        self._lean_input_grace = S.LEAN_INPUT_GRACE_SECONDS
        if not self.sounds.ch_ambient.get_busy():
            self.sounds.start_ambient()

    def _start_playing(self):
        self._load_floor(0)
        self._begin_playing()

    def _release_mouse(self):
        pygame.mouse.set_visible(True)
        pygame.event.set_grab(False)

    def _grab_mouse_for_play(self):
        if self.state == "playing":
            pygame.mouse.set_visible(False)
            pygame.event.set_grab(True)
            pygame.mouse.get_rel()

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

    def _in_debug_level(self):
        return bool(self._in_debug_preview() or getattr(self, "_debug_spectator", False)
                    or getattr(self, "_debug_transition_test", False)
                    or (self.spec or {}).get("key") == "debug")

    def _in_debug_preview(self):
        return (bool(self.spec.get("no_threat")) and self.spec.get("key") != "debug"
                and not self._debug_transition_test)

    def _in_debug_showcase(self):
        return self.spec.get("key") == "debug"

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

    def _open_gamma_calibration(self):
        self.state = "gamma_calibration"
        self.dragging_slider = None
        self.sounds.play_ui()

    def _close_gamma_calibration(self):
        self.dragging_slider = None
        self.state = "settings"
        self._flush_pending_settings_save()
        self.sounds.play_ui()

    def _reset_gamma(self):
        self.settings["gamma"] = 1.0
        self.renderer.set_gamma(1.0)
        self._settings_save_pending = True
        self.sounds.play_ui()

    _BAYER = (0.0, 8.0, 2.0, 10.0, 12.0, 4.0, 14.0, 6.0,
              3.0, 11.0, 1.0, 9.0, 15.0, 7.0, 13.0, 5.0)

    @staticmethod
    def gamma_encode(level, gamma):
        return int(round(255.0 * max(0.0, min(1.0, level)) ** (1.0 / max(gamma, 0.01))))

    @classmethod
    def gamma_symbol_pixel(cls, level, gamma, x, y):
        from game.renderer3d import COLOR_QUANT_LEVELS
        c = max(0.0, min(1.0, level)) ** (1.0 / max(gamma, 0.01))
        c += (cls._BAYER[(y % 4) * 4 + (x % 4)] / 16.0 - 0.5) / 34.0
        c = math.floor(c * COLOR_QUANT_LEVELS + 0.5) / COLOR_QUANT_LEVELS
        return int(round(255.0 * max(0.0, min(1.0, c))))

    def _gamma_layout(self):
        cx = S.SCREEN_W // 2
        slider = pygame.Rect(cx - 280, 500, 560, 16)
        reset = self._button(pygame.Rect(cx - 250, 580, 230, 48), i18n.t("gamma.reset"), self._reset_gamma)
        done = self._button(pygame.Rect(cx + 20, 580, 230, 48), i18n.t("gamma.done"), self._close_gamma_calibration)
        return {"slider": slider, "buttons": [reset, done]}

    def _draw_value_row(self, btn, value_text):
        rect, hovered, border, col, text_x = self._draw_button_frame(btn)
        vw = self.font_md.size(value_text)[0]
        label = self._ellipsize(btn["label"], self.font_md, rect.right - 28 - vw - text_x)
        self._text(self.font_md, label, col, topleft=(text_x, rect.centery - self.font_md.get_height() // 2),
                   shadow=False)
        self._text(self.font_md, value_text, col,
                   topleft=(rect.right - 16 - vw, rect.centery - self.font_md.get_height() // 2), shadow=False)

    def _draw_gamma_calibration(self):
        from game.renderer3d import COLOR_QUANT_LEVELS
        self.hud_surf.fill((0, 0, 0, 255))
        gamma = self.settings["gamma"]
        cx = S.SCREEN_W // 2
        self._text(self.font_lg, i18n.t("gamma.title"), S.COL_TEXT, topleft=(90, 70))
        words, lines, line = i18n.t("gamma.hint").split(), [], ""
        for word in words:
            trial = f"{line} {word}".strip()
            if self.font_md.size(trial)[0] > 900 and line:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
        for i, text in enumerate(lines):
            self._text(self.font_md, text, S.COL_UI_DIM, center=(cx, 175 + i * 32), shadow=False)
        captions = ("gamma.invisible", "gamma.barely", "gamma.visible")
        for i, (steps, caption) in enumerate(zip(GAMMA_SYMBOL_STEPS, captions)):
            level = steps / COLOR_QUANT_LEVELS
            sx = cx + (i - 1) * 270
            eye = pygame.Rect(0, 0, 170, 86)
            eye.center = (sx, 335)
            mark = pygame.Surface(eye.size)
            for yy in range(eye.h):
                for xx in range(eye.w):
                    v = self.gamma_symbol_pixel(level, gamma, eye.x + xx, eye.y + yy)
                    mark.set_at((xx, yy), (v, v, v))
            shape = pygame.Surface(eye.size, pygame.SRCALPHA)
            pygame.draw.ellipse(shape, (255, 255, 255, 255), shape.get_rect())
            pygame.draw.circle(shape, (0, 0, 0, 0), (eye.w // 2, eye.h // 2), 30)
            pygame.draw.circle(shape, (255, 255, 255, 255), (eye.w // 2, eye.h // 2), 11)
            mark.blit(shape, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            self.hud_surf.blit(mark, eye.topleft)
            self._text(self.font_sm, i18n.t(caption), (120, 112, 106), center=(sx, 410), shadow=False)
        layout = self._gamma_layout()
        lo, hi = SLIDER_SPECS["gamma"]
        self._draw_slider(layout["slider"], (gamma - lo) / (hi - lo), i18n.t("gamma.label"), f"{gamma:.2f}",
                          dragging=(self.dragging_slider == "gamma"))
        for btn in layout["buttons"]:
            self._draw_button(btn)

    def _close_settings(self):
        self.state = self.settings_return
        self._flush_pending_settings_save()
        self.dragging_slider = None
        self.awaiting_bind = None
        self.open_combo = None

    def _open_display(self):
        flags = pygame.OPENGL | pygame.DOUBLEBUF
        if self.settings["vsync"]:
            try:
                return pygame.display.set_mode((S.SCREEN_W, S.SCREEN_H), flags, vsync=1)
            except pygame.error:
                self.settings["vsync"] = False
        return pygame.display.set_mode((S.SCREEN_W, S.SCREEN_H), flags, vsync=0)

    def _try_toggle_fullscreen(self):
        try:
            pygame.display.toggle_fullscreen()
        except pygame.error:
            return False
        return True

    def _toggle_fullscreen(self):
        if not self._try_toggle_fullscreen():
            self.sounds.play_denied()
            return
        self.settings["fullscreen"] = not self.settings["fullscreen"]
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
        if self.settings.get("mic_enabled"):
            self._apply_mic_setting()

    def _toggle_compass(self):
        self.settings["compass"] = not self.settings.get("compass", True)
        self._save_settings()
        self.sounds.play_ui()

    def _select_hud_style(self, style):
        self.settings["hud_style"] = style
        self._save_settings()
        self.sounds.play_ui()

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
                if not p._collides(self.maze, self._scene_props(), x, y, S.PLAYER_RADIUS):
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
            p.equip_t = 0.0
            p.active_held_item = None
            p.map_open = False
            p._pre_map_light = None
            p._pre_hide_light = tuple(
                name for name, on in (("lighter", p.lighter_on),
                                      ("flashlight", p.flashlight_on)) if on)
            p.lighter_on = False
            p.flashlight_on = False
        else:
            end_x, end_y, end_angle = self._hide_exit_target(locker)
            end_pitch = 0.0
            was = p._pre_hide_light or ()
            if isinstance(was, str):
                was = (was,)
            if "lighter" in was and p.has_lighter:
                p.lighter_on = True
            if "flashlight" in was and p.battery > 0.5:
                p.flashlight_on = True
            p._pre_hide_light = None
        self.hide_transition = {
            "t": 0.0, "duration": 0.5, "entering": entering, "locker": locker,
            "sx": p.x, "sy": p.y, "sa": p.angle, "sp": p.pitch,
            "ex": end_x, "ey": end_y, "ea": end_angle, "ep": end_pitch,
        }
        p.is_hiding = False
        p.moved_this_frame = False
        self.fx_shake.add(0.18)
        self.sounds.play_locker()
        self.player.make_noise(S.NOISE_LOCKER)

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

    def _draw_panel(self, rect, fill=ui.PANEL_FILL, border=ui.PANEL_BORDER,
                     accent=ui.PANEL_ACCENT, corner=20, target=None):
        ui.draw_panel(target if target is not None else self.hud_surf, rect,
                      fill=fill, border=border, accent=accent, corner=corner)

    _WRAP_CACHE_MAX = 512

    def _wrap_text(self, text, font, max_width):
        cache = self.__dict__.setdefault("_wrap_cache", {})
        key = (text, font, max_width)
        hit = cache.get(key)
        if hit is not None:
            return list(hit)
        lines = self._wrap_text_uncached(text, font, max_width)
        if len(cache) >= self._WRAP_CACHE_MAX:
            cache.clear()
        cache[key] = tuple(lines)
        return lines

    def _wrap_text_uncached(self, text, font, max_width):
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
        def k(action):
            return self._binding_label(action)

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
            self._button((left, button_y0, w, h), i18n.t("menu.play"), self._start_playing),
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
            self.state = "menu"

    def _feedback_button(self):
        w, h = 340, 44
        rect = (S.SCREEN_W - w - 20, S.SCREEN_H - h - 40, w, h)
        return self._button(rect, i18n.t("menu.feedback"), lambda: webbrowser.open(FEEDBACK_URL), theme="gold")

    def _update_splash(self, dt):
        self._splash_t += dt
        if self._splash_t >= SPLASH_TOTAL_DURATION:
            self._finish_splash()

    def _finish_splash(self):
        if self.state != "splash":
            return
        self.state = self._post_splash_state

    @staticmethod
    def _smooth01(x):
        x = max(0.0, min(1.0, x))
        return x * x * (3.0 - 2.0 * x)

    def _splash_group_alpha(self, fade_start, fade_dur):
        t = self._splash_t
        fade_in = self._smooth01((t - fade_start) / fade_dur) if fade_dur > 0 else (1.0 if t >= fade_start else 0.0)
        fade_out = 1.0 - self._smooth01((t - SPLASH_HOLD_END) / SPLASH_FADE_OUT_DUR)
        return fade_in * fade_out

    def _open_credits(self):
        self.state = "credits"
        self._community_scroll_px = 0

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

        community_viewport = None
        if COMMUNITY_CREATORS:
            divider_row()
            text_row(i18n.t("about.community_title"), self.font_md, (215, 200, 190), 26)
            text_row(i18n.t("about.community_hint", n=len(COMMUNITY_CREATORS)), self.font_sm, S.COL_UI_DIM, 28)
            names_lines = self._wrap_text(", ".join(COMMUNITY_CREATORS), self.font_sm, content_w)
            line_h = 22
            viewport_h = min(len(names_lines), 5) * line_h
            community_viewport = dict(
                rect=pygame.Rect(left, y, content_w, viewport_h),
                lines=names_lines, line_h=line_h,
            )
            y += viewport_h + 20

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
        return rows, left, content_w, link_buttons, contributor_links, back_btn, community_viewport

    def _credits_buttons(self):
        _, _, _, link_buttons, contributor_links, back_btn, _ = self._credits_content()
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

    def _scroll_layer(self, viewport):
        layer = self._scroll_surf
        if layer is None or layer.get_size() != self.hud_surf.get_size():
            layer = self._scroll_surf = pygame.Surface(self.hud_surf.get_size(), pygame.SRCALPHA)
        layer.fill((0, 0, 0, 0), viewport)
        layer.set_clip(viewport)
        return layer

    def _blit_scroll_layer(self, layer, viewport, scroll_px, max_scroll, band=30):
        layer.set_clip(None)
        band = max(0, min(band, viewport.h // 3))
        if band:
            alpha = pygame.surfarray.pixels_alpha(layer)
            ramp = np.linspace(0.0, 1.0, band, dtype=np.float32)
            x0, x1 = viewport.left, viewport.right
            if scroll_px > 0:
                top = viewport.top
                strip = alpha[x0:x1, top:top + band].astype(np.float32) * ramp[None, :]
                alpha[x0:x1, top:top + band] = strip.astype(np.uint8)
            if scroll_px < max_scroll:
                bot = viewport.bottom - band
                strip = alpha[x0:x1, bot:bot + band].astype(np.float32) * ramp[::-1][None, :]
                alpha[x0:x1, bot:bot + band] = strip.astype(np.uint8)
            del alpha
        self.hud_surf.blit(layer, viewport.topleft, viewport)

    def _draw_scrollbar(self, viewport, scroll_px, max_scroll):
        if max_scroll <= 0:
            return
        track = pygame.Rect(viewport.right + 14, viewport.top, 6, viewport.h)
        pygame.draw.rect(self.hud_surf, (28, 26, 24), track, border_radius=3)
        pygame.draw.rect(self.hud_surf, (70, 64, 58), track, width=1, border_radius=3)
        content_h = viewport.h + max_scroll
        thumb_h = max(28, int(viewport.h * viewport.h / content_h))
        thumb_y = viewport.top + int((viewport.h - thumb_h) * (scroll_px / max_scroll))
        thumb = pygame.Rect(track.x, thumb_y, track.w, thumb_h)
        pygame.draw.rect(self.hud_surf, (110, 96, 62), thumb, border_radius=3)
        pygame.draw.rect(self.hud_surf, (150, 130, 90), thumb, width=1, border_radius=3)

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
            toggle_gap, slider_gap = 6, 28
            y = content.y + pad
            fs_rect = pygame.Rect(content.x, y, w, h)
            y += h + toggle_gap
            aa_rect = pygame.Rect(content.x, y, w, h)
            y += h + toggle_gap
            vs_rect = pygame.Rect(content.x, y, w, h)
            y += h + toggle_gap
            gamma_rect = pygame.Rect(content.x, y, w, h)
            y += h + slider_gap
            slider_keys = ("fps_limit", "view_distance", "quality_preset") + GFX_SLIDER_KEYS
            sliders = {}
            for key in slider_keys:
                sliders[key] = pygame.Rect(content.x, y, w - 170, 16)
                y += 16 + slider_gap
            fs_btn = self._button(fs_rect, i18n.t("settings.fullscreen"), self._toggle_fullscreen)
            aa_btn = self._button(aa_rect, i18n.t("settings.upscale_smoothing"), self._toggle_upscale_smoothing)
            vs_btn = self._button(vs_rect, i18n.t("settings.vsync"), self._toggle_vsync)
            gamma_btn = self._button(gamma_rect, i18n.t("settings.gamma_row"), self._open_gamma_calibration)
            return {"sliders": sliders, "buttons": [fs_btn, aa_btn, vs_btn, gamma_btn]}

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
            n_rows = math.ceil(len(S.BINDING_ORDER) / 2)
            rows_h = n_rows * (row_h + row_gap) - row_gap
            y = content.y + pad + 26
            sens_rect = pygame.Rect(content.x, y, w - 170, 16)
            y += 44
            rows = []
            for i, action in enumerate(S.BINDING_ORDER):
                row_i, col_i = divmod(i, 2)
                rx = content.x + col_i * (col_w + 20)
                ry = y + row_i * (row_h + row_gap)
                rows.append((action, pygame.Rect(rx, ry, col_w, row_h)))
            y += rows_h + 20
            reset_rect = pygame.Rect(content.centerx - 140, y, 280, 44)
            total_h = (reset_rect.bottom + 34) - content.y
            self._controls_max_scroll = max(0, total_h - content.h)
            self._controls_scroll_px = max(0, min(self._controls_max_scroll,
                                                   self._controls_scroll_px))
            off = self._controls_scroll_px
            if off:
                sens_rect = sens_rect.move(0, -off)
                rows = [(a, r.move(0, -off)) for a, r in rows]
                reset_rect = reset_rect.move(0, -off)
            reset_btn = self._button(reset_rect, i18n.t("controls.reset"), self._reset_bindings)
            return {"sliders": {"mouse_sensitivity": sens_rect},
                    "buttons": [reset_btn], "rows": rows, "msg_y": reset_rect.bottom + 24,
                    "viewport": content}

        if page == "interface":
            y = content.y + pad + 26
            hud_rect = pygame.Rect(content.x, y, w, h)
            styles = list(S.HUD_STYLES)
            cur = self.settings.get("hud_style", styles[0])
            hud_combo = {
                "rect": hud_rect,
                "options": [i18n.t("hud.style_" + k) for k in styles],
                "selected_index": styles.index(cur) if cur in styles else 0,
                "on_select": lambda i, ks=styles: self._select_hud_style(ks[i]),
                "enabled": True,
            }
            comp_rect = pygame.Rect(content.x, hud_rect.bottom + 74, w, h)
            comp_btn = self._button(comp_rect, i18n.t("settings.compass"), self._toggle_compass)
            lang_rect = pygame.Rect(content.x, comp_rect.bottom + 48, w, h)
            labels = [i18n.LANGUAGE_NAMES[code] for code in i18n.LANGUAGES]
            current = i18n.get_language()
            selected = i18n.LANGUAGES.index(current) if current in i18n.LANGUAGES else 0
            lang_combo = {
                "rect": lang_rect, "options": labels, "selected_index": selected,
                "on_select": lambda i: self._select_language(i18n.LANGUAGES[i]),
                "enabled": True,
            }
            return {"sliders": {}, "buttons": [comp_btn],
                    "combos": {"hud_style": hud_combo, "language": lang_combo},
                    "blurb_y": hud_rect.bottom + 26}

        if page == "debug":
            y = content.y + pad
            rows = []
            menu_only_ok = self.settings_return == "menu" or self._in_debug_level()
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
        preset = S.QUALITY_PRESETS.get(name)
        if preset is not None:
            self.settings.update(preset)
        self._apply_graphics_settings()

    def _apply_graphics_settings(self):
        get = self.settings.get
        self.renderer.set_gamma(get("gamma", 1.0))
        scale = get("gfx_render_scale", "320p")
        (low_w, low_h), snap = S.GFX_RENDER_SCALE_VALUES.get(
            scale, S.GFX_RENDER_SCALE_VALUES["320p"])
        self.renderer.set_resolution(low_w, low_h, snap)

        shadow_res = get("gfx_shadow_res", "medium")
        point_res, flash_res, moon_res = S.GFX_SHADOW_RES_VALUES.get(
            shadow_res, S.GFX_SHADOW_RES_VALUES["medium"])
        self.renderer.set_shadow_resolution(point_res, flash_res, moon_res)

        self.renderer.set_shadow_quality(get("gfx_shadow_penumbra", "medium"))
        self._sync_quality_preset_name()

    def _sync_quality_preset_name(self):
        self.settings["quality_preset"] = S.match_quality_preset(self.settings)

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
        if self.settings["fullscreen"] and not self._try_toggle_fullscreen():
            self.settings["fullscreen"] = False
        self._save_settings()
        self.sounds.play_ui()

    def _apply_slider(self, key, rect, mouse_x):
        frac = max(0.0, min(1.0, (mouse_x - rect.x) / rect.w))
        if key in STEPPED_SLIDERS:
            options = STEPPED_SLIDERS[key]
            if key == "quality_preset":
                options = tuple(o for o in options if o in S.QUALITY_PRESETS)
            idx = round(frac * (len(options) - 1)) if len(options) > 1 else 0
            value = options[max(0, min(len(options) - 1, idx))]
            if value == self.settings.get(key):
                return
            self.settings[key] = value
            if key == "quality_preset":
                self._apply_quality_preset(value)
            elif key in S.GFX_SETTING_OPTIONS:
                self._apply_graphics_settings()
        else:
            lo, hi = SLIDER_SPECS[key]
            self.settings[key] = lo + frac * (hi - lo)
            if key == "master_volume":
                self.sounds.set_master_volume(self.settings[key])
            elif key == "sfx_volume":
                self.sounds.set_sfx_volume(self.settings[key])
            elif key == "music_volume":
                self.sounds.set_music_volume(self.settings[key])
            elif key == "gamma":
                self.renderer.set_gamma(self.settings[key])
        self._settings_save_pending = True

    def _flush_pending_settings_save(self):
        if getattr(self, "_settings_save_pending", False):
            self._settings_save_pending = False
            self._save_settings()

    def _select_settings_tab(self, key):
        self.settings_page = key
        self._flush_pending_settings_save()
        self.dragging_slider = None
        self.awaiting_bind = None
        self.open_combo = None
        if key == "sound":
            self.mic.refresh_devices()
            if self.settings.get("mic_enabled") and self._mic_device_missing:
                self._apply_mic_setting()
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

    def _binding_down(self, action):
        kind, code = self.settings["bindings"][action]
        if kind == "key":
            return pygame.key.get_pressed()[code]
        return pygame.mouse.get_pressed(num_buttons=5)[code - 1]

    def _binding_matches_keydown(self, action, key):
        kind, code = self.settings["bindings"][action]
        return kind == "key" and code == key

    def _binding_label(self, action):
        kind, code = self.settings["bindings"][action]
        if kind == "key":
            return pygame.key.name(code).upper()
        if code in (1, 2, 3):
            return i18n.t(f"binding.mouse_{code}")
        return i18n.t("binding.mouse_n", n=code)

    def _prompt(self, key, **kwargs):
        return i18n.t(key, interact=self._binding_label("interact"),
                      alt_interact=self._binding_label("alt_interact"), **kwargs)


    def _reset_map(self):
        self.map_sheets = []
        self.map_page = 0
        self.map_cursor = [0.5, 0.5]
        self._map_mode = False
        self._map_drawing = False
        self._map_dirty = True
        self._map_ink_warned = False
        self._map_turn = None
        self._map_pencil_t = 0.0
        self._map_help_shown = False
        self._stamina_seen_t = 0.0
        self._sheet_seed = random.randrange(1 << 30)
        self._sheet_mark_list = None
        self._clip_key = None
        self._clip_surf = None
        self._clip_pending = None
        self._clip_raise = 0.0
        self._clip_shade = None
        self._clip_lit = -1.0
        self._form_seen = None
        self._form_lines = []
        self._note_key = None
        self._note_surf = None
        self.note_back_text = None
        self._form_key = None
        self._form_surf = None
        self._form_shade = None
        self._form_lit = -1.0

    def _add_map_sheet(self):
        self.map_sheets.append(self._new_map_sheet(max(self.maze.w, self.maze.h) if self.maze else 40))
        self._map_dirty = True
        return len(self.map_sheets)

    def _lean_taken_by_map(self, action):
        if not self.player.map_open:
            return False
        b = self.settings["bindings"]
        return b[action] == b["map_mode"] or (self._map_mode and b[action] == b["map_pencil"])

    def _toggle_map_mode(self):
        p = self.player
        if p.map_open and p.active_held_item == "map" and not self.is_peeking and self.hide_transition is None:
            self._map_mode = not self._map_mode
            self._map_dirty = True

    def _map_sync_floor(self):
        if not self.map_sheets:
            self._add_map_sheet()
        self.map_page = max(0, min(len(self.map_sheets) - 1, self.map_page))

    @staticmethod
    def _new_map_sheet(meters):
        size = S.MAP_TEXTURE_SIZE
        surf = pygame.Surface((size, size))
        surf.fill((214, 204, 176))
        grid = (196, 188, 162)
        for i in range(0, size, 16):
            pygame.draw.line(surf, grid, (i, 0), (i, size - 1))
            pygame.draw.line(surf, grid, (0, i), (size - 1, i))
        return {"surf": surf, "meters": float(meters)}

    def _flip_map_page(self, step):
        self._map_sync_floor()
        if len(self.map_sheets) < 2 or self._map_turn is not None:
            return
        d = 1 if step > 0 else -1
        nxt = self.map_page + d
        if not (0 <= nxt < len(self.map_sheets)):
            return
        if d > 0:
            flat, moving = nxt, self.map_page
        else:
            flat, moving = self.map_page, nxt
        self.map_page = nxt
        self._map_turn = {"t": 0.0, "dir": d, "flat": flat, "moving": moving}
        self._map_dirty = True
        surf = self.map_sheets[moving]["surf"]
        rgba = pygame.image.tostring(
            surf.convert_alpha() if surf.get_bitsize() != 32 else surf, "RGBA", True)
        self.renderer.set_map_turn_sheet(rgba, S.MAP_TEXTURE_SIZE)
        self.sounds.play_page_turn()

    def _update_map_turn(self, dt):
        turn = self._map_turn
        if turn is None:
            self.renderer.map_turn = 0.0
            return
        turn["t"] += dt / S.MAP_PAGE_TURN_SECONDS
        if turn["t"] >= 1.0:
            self._map_turn = None
            self.renderer.map_turn = 0.0
            self._map_dirty = True
            return
        f = turn["t"]
        f = f * f * (3.0 - 2.0 * f)
        self.renderer.map_turn = f if turn["dir"] > 0 else 1.0 - f

    def _update_map_drawing(self, mouse_dx, mouse_dy):
        p = self.player
        if not p.map_open or p.active_held_item != "map":
            self._map_mode = False
        drawing = self._map_mode and p.equip_t > 0.95 and self._map_turn is None
        if drawing != self._map_drawing:
            self._map_drawing = drawing
            self._map_dirty = True
        p.map_drawing = drawing
        if not drawing:
            return False
        self._map_sync_floor()
        sheet = self.map_sheets[self.map_page]
        x0, y0 = self.map_cursor
        x1 = min(0.98, max(0.02, x0 + mouse_dx * S.MAP_CURSOR_SPEED))
        y1 = min(0.98, max(MAP_CURSOR_V_MIN, y0 + mouse_dy * S.MAP_CURSOR_SPEED))
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg > 0.0 and self._binding_down("map_pencil"):
            cost = seg
            if p.pencil_ink <= 0.0:
                if not self._map_ink_warned:
                    self.hint_text = i18n.Text("hint.map_no_ink")
                    self.hint_timer = 2.5
                    self._map_ink_warned = True
            else:
                frac = min(1.0, p.pencil_ink / cost)
                ex, ey = x0 + (x1 - x0) * frac, y0 + (y1 - y0) * frac
                size = S.MAP_TEXTURE_SIZE
                pygame.draw.line(sheet["surf"], (58, 56, 60), (x0 * size, y0 * size), (ex * size, ey * size),
                                 S.MAP_LINE_WIDTH)
                p.pencil_ink = max(0.0, p.pencil_ink - cost * frac)
                if p.pencil_ink > 0.0:
                    self._map_ink_warned = False
        if seg > 0.0:
            self.map_cursor = [x1, y1]
            self._map_dirty = True
        return True

    def _sync_map_pencil(self, dt):
        p = self.player
        target = 1.0 if (self._map_mode and p.active_held_item == "map") else 0.0
        self._map_pencil_t += (target - self._map_pencil_t) * min(1.0, dt * S.MAP_PENCIL_RAISE_RATE)
        pencils = max(0.0, p.pencil_ink) / S.PENCIL_SHEETS
        whole = int(math.ceil(pencils - 1e-6))
        spares = max(0, whole - 1)
        in_use = pencils - spares if pencils > 0.0 else 0.0
        self.renderer.map_pencil = (in_use, spares, self._map_pencil_t,
                                     self.map_cursor[0], self.map_cursor[1])

    def _upload_map_sheet(self):
        if self.player.active_held_item != "map":
            return
        behind = self.map_page
        if self._map_turn is not None and self._map_turn["dir"] > 0:
            behind -= 1
        self.renderer.map_roll_pages = max(0, behind)
        if not self._map_dirty:
            return
        self._map_sync_floor()
        page = self._map_turn["flat"] if self._map_turn is not None else self.map_page
        surf = self.map_sheets[page]["surf"]
        if self._map_drawing:
            surf = surf.copy()
            size = S.MAP_TEXTURE_SIZE
            cx, cy = int(self.map_cursor[0] * size), int(self.map_cursor[1] * size)
            pygame.draw.circle(surf, (150, 30, 30), (cx, cy), 3, 1)
        rgba = pygame.image.tostring(surf.convert_alpha() if surf.get_bitsize() != 32 else surf, "RGBA", True)
        self.renderer.set_map_sheet(rgba, S.MAP_TEXTURE_SIZE)
        self._map_dirty = False

    def _resolve_lean_input(self, dt, suppress):
        self._lean_input_grace = max(0.0, self._lean_input_grace - dt)
        gated = suppress or self._lean_input_grace > 0.0
        left_held = self._binding_down("lean_left") and not gated and not self._lean_taken_by_map("lean_left")
        right_held = self._binding_down("lean_right") and not gated and not self._lean_taken_by_map("lean_right")

        if left_held and not self._lean_prev_left:
            self._lean_active_dir = -1
        if right_held and not self._lean_prev_right:
            self._lean_active_dir = 1
        if self._lean_active_dir == -1 and not left_held:
            self._lean_active_dir = 1 if right_held else 0
        elif self._lean_active_dir == 1 and not right_held:
            self._lean_active_dir = -1 if left_held else 0
        elif not left_held and not right_held:
            self._lean_active_dir = 0

        self._lean_prev_left, self._lean_prev_right = left_held, right_held
        return self._lean_active_dir == -1, self._lean_active_dir == 1

    def _resolve_bind(self, binding):
        action = self.awaiting_bind
        self.awaiting_bind = None
        if binding == ("key", pygame.K_ESCAPE):
            return
        conflict = next((a for a, b in self.settings["bindings"].items()
                         if b == binding and a != action and frozenset((a, action)) not in S.SHARED_BINDINGS), None)
        if conflict is not None:
            self.controls_msg = i18n.Text("controls.key_taken", label=i18n.Text(f"binding.{conflict}"))
            self.controls_msg_timer = 3.0
            self.sounds.play_denied()
            return
        self.settings["bindings"][action] = binding
        self._save_settings()
        self.controls_msg = None
        self.sounds.play_ui()

    def _reset_bindings(self):
        self.settings["bindings"] = dict(S.DEFAULT_BINDINGS)
        self._save_settings()
        self.controls_msg = i18n.Text("controls.reset_done")
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
        on_top = {}
        aim_half_angle = 0.5
        for prop in self.props:
            if prop.picked or prop.interactable is None:
                continue
            radius = 1.3 if prop.wall_mounted else 1.05
            dx, dy = prop.x - p.x, prop.y - p.y
            d = math.hypot(dx, dy)
            if d >= radius:
                continue
            if d >= 0.45:
                rel = (math.atan2(dy, dx) - self._trip_cam_yaw + math.pi) % (2 * math.pi) - math.pi
                if abs(rel) > aim_half_angle:
                    continue
            if prop.interactable == "pickup":
                on_top.setdefault((int(prop.x), int(prop.y)), []).append(prop)
            if d >= best_d:
                continue
            best, best_d = (prop.interactable, prop), d
        if best is not None and best[0] != "pickup":
            over = on_top.get((int(best[1].x), int(best[1].y)))
            if over:
                item = min(over, key=lambda q: math.hypot(q.x - p.x, q.y - p.y))
                best, best_d = ("pickup", item), math.hypot(item.x - p.x, item.y - p.y)
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

    def _spec_text(self, field, default_key=None, capitalize=False):
        key = self.spec.get(field, default_key)
        return i18n.Text(key, capitalize=capitalize) if key else None

    def prompt_text(self, res):
        if res is None:
            return None
        kind, obj = res
        if kind == "exit_hide":
            return self._prompt("prompt.exit_hide")
        if kind == "locker":
            return self._prompt("prompt.hide")
        if kind == "note":
            return self._prompt("prompt.read_note")
        if kind == "workbench":
            if self.player.cutters_broken:
                return self._prompt("prompt.repair_cutters")
            return None
        if kind == "pickup":
            item = i18n.t(PICKUP_LABEL_KEYS[obj.kind]) if obj.kind in PICKUP_LABEL_KEYS else obj.kind
            return self._prompt("prompt.pickup", item=item)
        if kind == "panel":
            panel_label = self._spec_t("panel_label")
            if obj.powered:
                return i18n.t("hud.panel_status", label=panel_label, status=self._spec_t("panel_powered_text", "hud.powered"))
            if self.player.carried > 0:
                return self._prompt("prompt.install_hold", label=self._spec_t("collectible_label"),
                              have=obj.installed, need=self.spec["n_collectible"])
            return self._prompt("prompt.panel_need", panel=panel_label, have=obj.installed,
                          need=self.spec["n_collectible"], label=self._spec_t("collectible_label"))
        if kind == "exit":
            req = self.spec.get("exit_requires_item")
            if req and not self._player_has_exit_item(req):
                return self._spec_t("exit_requires_label", "hint.something_missing")
            if obj.powered:
                if obj.kind == "elevator" and not self._in_debug_showcase():
                    if not self.elevator_called:
                        return self._prompt("prompt.elevator_call")
                    if not self.elevator_arrived:
                        return self._prompt("prompt.elevator_arriving")
                    return self._prompt("prompt.elevator_enter")
                if obj.kind == "hatch" and not self._in_debug_showcase():
                    return self._prompt("prompt.hatch_turn")
                if obj.kind == "fence_gap" and not self._in_debug_showcase():
                    return self._prompt("prompt.fence_cut")
                return self._prompt("prompt.exit_ready", label=self._spec_t("exit_label").capitalize())
            return self._prompt("prompt.exit_no_power", label=self._spec_t("exit_label").capitalize())
        if kind == "door":
            return self._prompt("prompt.door_close") if obj.is_open else self._prompt("prompt.door_open")
        if kind == "portal":
            target = i18n.t(PORTAL_LABEL_KEYS[obj.target_floor]) if obj.target_floor in PORTAL_LABEL_KEYS else "?"
            return self._prompt("prompt.portal", target=target)
        return None

    def alt_prompt_text(self, res):
        if res is None:
            return None
        kind, obj = res
        if kind != "door" or obj.is_broken:
            return None
        if obj.is_open:
            return self._prompt("prompt.door_close_and_lock")
        if obj.is_latched:
            return self._prompt("prompt.door_unlock")
        return self._prompt("prompt.door_lock")

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
            self._open_note(i18n.Text(obj.note_text), getattr(obj, "note_back", None),
                            getattr(obj, "note_back_kind", None))
            self.stats["notes"] += 1
            self.sounds.play_note_pickup()
        elif kind == "door":
            if obj.is_open:
                obj.toggle()
                pan, vol = self._pan_vol_for(obj.x, obj.y)
                self.sounds.play_door(pan, vol)
                self.player.make_noise(S.NOISE_DOOR)
            else:
                self.peek_hold_target = obj
                self.peek_hold_t = 0.0
        elif kind == "pickup":
            if obj.kind == "pencil" and self.player.pencil_ink >= S.PENCIL_MAX_CARRIED * S.PENCIL_SHEETS:
                self.hint_text = i18n.Text("hint.pencils_full", n=S.PENCIL_MAX_CARRIED)
                self.hint_timer = 2.5
                self.sounds.play_denied()
                return
            obj.picked = True
            if obj.kind == "battery":
                self.player.add_battery()
                self.stats["batteries"] += 1
            elif obj.kind == "cutters":
                self.player.has_cutters = True
                self.hint_text = i18n.Text("hint.cutters_picked")
                self.hint_timer = 3.0
            elif obj.kind == "lighter":
                self.player.has_lighter = True
                self.hint_text = i18n.Text("hint.lighter_picked")
                self.hint_timer = 3.0
            elif obj.kind == "paper_map":
                self.player.has_map = True
                self.player.pencil_ink = min(S.PENCIL_MAX_CARRIED * S.PENCIL_SHEETS,
                                             self.player.pencil_ink + S.PENCIL_SHEETS)
                self._add_map_sheet()
                self.hint_text = i18n.Text("hint.map_picked", map=self._binding_label("map"),
                                           mode=self._binding_label("map_mode"),
                                           pencil=self._binding_label("map_pencil"))
                self.hint_timer = 5.0
            elif obj.kind == "pencil":
                self.player.pencil_ink = min(S.PENCIL_MAX_CARRIED * S.PENCIL_SHEETS,
                                             self.player.pencil_ink + S.PENCIL_SHEETS)
                self.hint_text = i18n.Text("hint.pencil_picked")
                self.hint_timer = 2.5
            elif obj.kind == "map_sheet":
                n = self._add_map_sheet()
                self.hint_text = i18n.Text("hint.sheet_picked", pages=n)
                self.hint_timer = 3.0
            elif obj.kind == "sanity_pill":
                self.sanity_boost_timer = S.SANITY_PILL_DURATION
                self.hint_text = i18n.Text("hint.sanity_pill_used")
                self.hint_timer = 3.0
            else:
                self.player.carried += 1
            self.sounds.play_pickup()
        elif kind == "panel":
            if obj.powered:
                self.sounds.play_ui()
            elif self.player.carried <= 0:
                self.sounds.play_denied()
                self.hint_text = i18n.Text("hint.need_collectible", label=self._spec_text("collectible_label"),
                                         have=obj.installed, need=self.spec["n_collectible"])
                self.hint_timer = 2.5
        elif kind == "exit":
            if self._in_debug_showcase():
                return
            req = self.spec.get("exit_requires_item")
            if req and not self._player_has_exit_item(req):
                self.sounds.play_denied()
                self.hint_text = self._spec_text("exit_requires_label", "hint.something_missing")
                self.hint_timer = 2.5
            elif obj.powered:
                if obj.kind == "elevator":
                    if not self.elevator_called:
                        self._call_elevator()
                    elif self.elevator_arrived:
                        self._start_elevator_ride()
                    else:
                        self.sounds.play_denied()
                elif obj.kind in ("hatch", "fence_gap"):
                    pass
            else:
                self.sounds.play_denied()
        elif kind == "portal":
            if obj.target_floor == "hub":
                self._start_debug_level()
            else:
                self._enter_debug_peek(obj.target_floor,
                                        live=bool(getattr(obj, "portal_live", False)))

    def interact_alt(self):
        if self.hide_transition is not None or self.is_peeking:
            return
        res = self.find_interactable()
        if res is None:
            return
        kind, obj = res
        if kind != "door":
            return
        if obj.is_broken:
            self.sounds.play_denied()
            return
        was_open = obj.is_open
        obj.toggle_latch()
        pan, vol = self._pan_vol_for(obj.x, obj.y)
        if was_open:
            self.sounds.play_door(pan, vol)
            self.player.make_noise(S.NOISE_DOOR)
        else:
            self.sounds.play_latch(pan, vol)
            self.player.make_noise(S.NOISE_LATCH)

    def _complete_install(self, obj):
        self.player.carried -= 1
        obj.installed += 1
        if obj.kind == "shed_lock":
            self._drop_padlock(obj, obj.installed - 1)
        self.sounds.play_unlock()
        self._start_interact_feedback()
        if obj.installed >= self.spec["n_collectible"]:
            obj.powered = True
            if obj.kind == "shed_lock":
                obj.swing_target = 1.0
            if self.exit_prop is not None:
                self.exit_prop.powered = True
            self.hint_text = i18n.Text("hint.exit_powered", label=self._spec_text("exit_label", capitalize=True))
            self.hint_timer = 3.5

    def _drop_padlock(self, door, index):
        fx, fy = math.cos(door.facing), math.sin(door.facing)
        side = -0.40 + 0.26 * index
        x = door.x + fx * 0.34 - fy * side
        y = door.y + fy * 0.34 + fx * side
        if self.maze.circle_hits_wall(x, y, 0.08):
            x, y = door.x + fx * 0.22 - fy * side, door.y + fy * 0.22 + fx * side
        lock = Prop("padlock_dropped", x, y, facing=random.uniform(0.0, math.tau))
        self.props.append(lock)
        self.props.sort(key=lambda q: (q.kind, q.texture or ""))

    def _update_install_hold(self, dt, interact_held):
        target = None
        if interact_held:
            res = self.find_interactable()
            if res is not None:
                kind, obj = res
                if kind == "panel" and not obj.powered and self.player.carried > 0:
                    target = obj
        if target is None:
            if self.install_hold_target is not None:
                self.install_hold_target.install_t = 0.0
            self.install_hold_target = None
            self.install_hold_t = 0.0
            return
        if target is not self.install_hold_target:
            if self.install_hold_target is not None:
                self.install_hold_target.install_t = 0.0
            self.install_hold_target = target
            self.install_hold_t = 0.0
        self.install_hold_t += dt
        target.install_t = min(1.0, self.install_hold_t / S.INSTALL_HOLD_SECONDS)
        if self.install_hold_t >= S.INSTALL_HOLD_SECONDS:
            self._complete_install(target)
            target.install_t = 0.0
            self.install_hold_target = None
            self.install_hold_t = 0.0

    def _update_cutters_repair(self, dt, interact_held):
        target = None
        if interact_held and self.player.cutters_broken:
            res = self.find_interactable()
            if res is not None:
                kind, obj = res
                if kind == "workbench":
                    target = obj
        was_repairing = self.cutters_repair_target is not None
        if target is None:
            if was_repairing:
                self.sounds.stop_action_loop()
                self.cutters_repair_target.install_t = 0.0
            self.cutters_repair_target = None
            self.cutters_repair_t = 0.0
            return
        if target is not self.cutters_repair_target:
            if self.cutters_repair_target is not None:
                self.cutters_repair_target.install_t = 0.0
            self.cutters_repair_target = target
            self.cutters_repair_t = 0.0
        if not was_repairing:
            self.sounds.start_cutters_repair_loop()
        self.cutters_repair_t += dt
        target.install_t = min(1.0, self.cutters_repair_t / S.CUTTERS_REPAIR_SECONDS)
        if self.cutters_repair_t >= S.CUTTERS_REPAIR_SECONDS:
            self.player.cutters_broken = False
            target.install_t = 0.0
            self.cutters_repair_target = None
            self.cutters_repair_t = 0.0
            self._cutters_grace_remaining = S.CUTTERS_REPAIR_GRACE_SECONDS
            self.sounds.stop_action_loop()
            self.sounds.play_unlock()
            self.hint_text = i18n.Text("hint.cutters_repaired")
            self.hint_timer = 2.5

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
                was_latched = target.is_latched
                target.toggle()
                pan, vol = self._pan_vol_for(target.x, target.y)
                if was_latched:
                    self.sounds.play_latch(pan, vol)
                else:
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

    def _update_fence_cut(self, dt, interact_held):
        target = None
        if interact_held and self.fence_escape is None and self.angel_seq is None:
            res = self.find_interactable()
            if res is not None:
                kind, obj = res
                if kind == "exit" and obj.kind == "fence_gap" and obj.powered:
                    req = self.spec.get("exit_requires_item")
                    if not req or self._player_has_exit_item(req):
                        target = obj
        was_cutting = self.fence_cutting
        self.fence_cutting = target is not None
        if self.fence_cutting and not was_cutting:
            self.sounds.start_fence_cut_loop()
        elif was_cutting and not self.fence_cutting:
            self.sounds.stop_action_loop()
        if target is None:
            return
        self.player.make_noise(S.NOISE_CUTTERS)
        self.fence_cut_progress += dt / S.FENCE_CUT_SECONDS
        target.cut_stage = min(2, int(self.fence_cut_progress * 3.0))
        if self._cutters_grace_remaining > 0.0:
            self._cutters_grace_remaining -= dt
            self._cutters_break_timer = 0.0
        elif self._cutters_break_count >= S.CUTTERS_BREAK_MAX_PER_CUT:
            pass
        elif not any(q.kind == "workbench" for q in self.props):
            pass
        else:
            self._cutters_break_timer += dt
            if self._cutters_break_timer >= 1.0:
                self._cutters_break_timer -= 1.0
                chance = S.CUTTERS_BREAK_CHANCE_BASE + S.CUTTERS_BREAK_CHANCE_PROGRESS_SCALE * self.fence_cut_progress
                if random.random() < chance:
                    self._cutters_break_count += 1
                    self.player.cutters_broken = True
                    self.fence_cutting = False
                    self.sounds.stop_action_loop(fade_ms=0)
                    self.sounds.play_cutters_snap()
                    self.hint_text = i18n.Text("hint.cutters_broke")
                    self.hint_timer = 3.0
                    return
        if not self._angel_roll_done and self.fence_cut_progress >= S.ANGEL_TRIGGER_PROGRESS:
            self._angel_roll_done = True
            if random.random() < S.ANGEL_EVENT_CHANCE:
                self.sounds.stop_action_loop()
                self._start_angel_sequence()
                return
        if self.fence_cut_progress >= 1.0:
            self.fence_cutting = False
            self.fence_cut_progress = 0.0
            self._cutters_break_count = 0
            self.sounds.stop_action_loop()
            target.cut = True
            self.fx_shake.add(0.3)
            self.sounds.play_bang()
            self._start_fence_escape(cut_gap=target)

    def _build_forest_run_scene(self):
        seed = random.randrange(1 << 30)
        self.floor_seed = seed
        period = S.FOREST_RUN_PERIOD
        self.maze = Maze(w=17, h=int(period * 25), seed=seed, layout="forest_run")
        rng = random.Random(seed ^ 0x5EED)
        self.props, self.panel_prop, self.exit_prop, monster_cell, self.doors = populate_forest_run(self.maze, rng)
        self.props.sort(key=lambda p: (p.kind, p.texture or ""))
        self.renderer.build_level(self.maze, theme="yard")
        self.spec = dict(self.spec, ambient_level=S.FOREST_RUN_AMBIENT,
                         moon_strength=S.FOREST_RUN_MOON, fog_dist=S.FOREST_RUN_FOG_DIST,
                         fog_color=S.FOREST_RUN_FOG_COLOR,
                         ground_haze=S.FOREST_HAZE, haze_height=S.FOREST_HAZE_HEIGHT)
        sx, _ = self.maze.start
        self.player.x, self.player.y = sx, S.FOREST_RUN_START
        self.player.angle = math.pi / 2
        self.player.pitch = 0.0
        self._forest_run_cx = sx

    def _ensure_forest_run_scene(self):
        if self.maze is not None and self.maze.layout == "forest_run":
            return
        self._build_forest_run_scene()
        p = self.player
        p.moved_this_frame = False
        p.noise_radius = 0.0
        p.lean_t = 0.0
        p.peek_x, p.peek_y = p.x, p.y
        p.flashlight_on = False
        p.lighter_on = False
        p.active_held_item = None
        p.equip_t = 0.0
        p.is_sprinting = True
        self.monster = Monster(
            self.player.x, self.player.y - 3.0, self.maze,
            rng=random.Random(self.floor_seed ^ 0xB0B0),
            speed_mult=0.0, vision_mult=0.0,
            blocked_cells=set(), lockers=[], doors=[],
            blocked_prop_candidates=[], dead_end_lockers=[],
        )

    def _start_fence_escape(self, cut_gap=None):
        p = self.player
        rel_f, rel_r, look_err, rel_pitch = 0.9, 0.0, 0.0, p.pitch
        if cut_gap is not None:
            gfx, gfy = math.cos(cut_gap.facing), math.sin(cut_gap.facing)
            dx, dy = p.x - cut_gap.x, p.y - cut_gap.y
            rel_f = dx * gfx + dy * gfy
            rel_r = -dx * gfy + dy * gfx
            look_err = _wrap_angle(p.angle - math.atan2(cut_gap.y - p.y, cut_gap.x - p.x))
        light0 = (self.spec.get("ambient_level", 0.06), self._moon_strength(),
                  self.spec.get("fog_dist", 12.5), tuple(self.spec.get("fog_color", S.COL_FOG)))
        self._ensure_forest_run_scene()
        gap = next((q for q in self.props if q.kind == "fence_gap"), None)
        gx, gy = (gap.x, gap.y) if gap is not None else (p.x, p.y - 1.0)
        sx, sy = gx + rel_r, gy - max(0.35, rel_f)
        start_yaw = math.atan2(gy - sy, gx - sx) + look_err
        self._run_speed_t = 0.0
        self.fence_escape = {"t": 0.0, "gx": gx, "gy": gy,
                             "sx": sx, "sy": sy, "sa": start_yaw,
                             "sp": rel_pitch, "light0": light0}
        p.x, p.y = sx, sy
        p.peek_x, p.peek_y = p.x, p.y
        p.angle, p.pitch = start_yaw, rel_pitch
        p.is_sprinting = True
        self.sounds.set_growl(False)
        self._release_mouse()
        self.state = "fence_escape"

    def _fence_escape_camera_override(self):
        fe = self.fence_escape
        if fe is None:
            return None
        u = min(1.0, fe["t"] / S.FENCE_ESCAPE_SECONDS)
        ease = u * u * (3.0 - 2.0 * u)
        duck = math.sin(min(1.0, max(0.0, (u - 0.18) / 0.58)) * math.pi)
        blend = min(1.0, u / 0.28)
        blend = blend * blend * (3.0 - 2.0 * blend)
        px = fe["gx"] + (fe["sx"] - fe["gx"]) * (1.0 - blend)
        py_path = fe["sy"] + (fe["gy"] + 1.8 - fe["sy"]) * ease
        py = py_path
        z = EYE_HEIGHT - S.FENCE_ESCAPE_DUCK * duck
        yaw_path = math.pi / 2
        yaw = yaw_path + _wrap_angle(fe["sa"] - yaw_path) * (1.0 - blend)
        pitch_path = -0.20 * duck + 0.08 * ease
        pitch = pitch_path + (fe["sp"] - pitch_path) * (1.0 - blend)
        life_yaw, life_pitch = self._cam_life(self.anim_t, 0.7 + duck)
        bob = math.sin(u * 26.0) * 0.010 * (0.3 + ease)
        return ((px, py, z + bob), yaw + life_yaw, pitch + life_pitch, FOV_DEGREES)

    def _cam_life(self, t, amount=1.0):
        yaw = (math.sin(t * 1.7) * 0.011 + math.sin(t * 0.63 + 1.1) * 0.017) * amount
        pitch = (math.sin(t * 1.29 + 0.4) * 0.009 + math.sin(t * 2.87) * 0.005) * amount
        return yaw, pitch

    def _update_forest_run_motion(self, dt):
        p = self.player
        p.moved_this_frame = False
        p.noise_radius = 0.0
        self._update_forest_echoes()
        self._run_speed_t = min(1.0, getattr(self, "_run_speed_t", 0.0) + dt / S.FOREST_RUN_PICKUP)
        ramp = self._run_speed_t * self._run_speed_t * (3.0 - 2.0 * self._run_speed_t)
        speed = S.FOREST_RUN_CRAWL_SPEED + (S.FOREST_RUN_SPEED - S.FOREST_RUN_CRAWL_SPEED) * ramp
        p.y += speed * dt
        if p.y >= S.FOREST_RUN_START + S.FOREST_RUN_LOOP:
            p.y -= S.FOREST_RUN_LOOP
        p.bob_phase += dt * speed * 6.0
        p.x = self._forest_run_cx + math.sin(self.anim_t * 1.7) * 0.16
        p.peek_x, p.peek_y = p.x, p.y

    def _update_forest_echoes(self):
        p = self.player
        for q in self.props:
            if q.kind != "asylum_echo":
                continue
            ahead = q.y - p.y
            thin = (ahead - S.FOREST_ECHO_VANISH) / max(0.01, S.FOREST_ECHO_FADE)
            q.ghost_alpha = S.FOREST_ECHO_ALPHA * max(0.0, min(1.0, thin))

    def _update_fence_escape(self, dt):
        fe = self.fence_escape
        fe["t"] += dt
        p = self.player
        p.moved_this_frame = False
        p.noise_radius = 0.0
        cam = self._fence_escape_camera_override()
        if cam is not None:
            p.x, p.y = cam[0][0], cam[0][1]
            p.peek_x, p.peek_y = p.x, p.y
            p.bob_phase += dt * 6.0
        self._update_forest_echoes()
        if fe["t"] >= S.FENCE_ESCAPE_SECONDS:
            self.fence_escape = None
            self.player.pitch = 0.0
            self.player.angle = math.pi / 2
            self._trip_cam_yaw, self._trip_cam_pitch = self.player.angle, 0.0
            self._trip_target_unwrapped = self._trip_follower_unwrapped = self.player.angle
            self._trip_prev_player_angle = self.player.angle
            if self._in_debug_preview():
                self._start_debug_level()
            else:
                self._start_win_sequence()

    def _update_win(self, dt):
        self._update_forest_run_motion(dt)

    def _start_angel_sequence(self):
        p = self.player
        ax, ay = self.maze.w / 2.0, self.maze.h / 2.0
        az = S.ANGEL_HEIGHT
        dx, dy, dz = ax - p.x, ay - p.y, az - EYE_HEIGHT
        target_yaw = math.atan2(dy, dx)
        target_pitch = max(-1.45, min(1.45, math.atan2(dz, math.hypot(dx, dy))))
        self.angel_seq = {
            "phase": "notice", "t": 0.0,
            "start_yaw": p.angle, "start_pitch": p.pitch,
            "yaw": p.angle, "pitch": p.pitch,
            "target_yaw": target_yaw, "target_pitch": target_pitch,
            "flood_t": 0.0, "pos": (ax, ay, az),
            "burn": 0.0, "ray": 0.0,
            "reach": math.hypot(math.hypot(ax, ay), az) * S.ANGEL_BURN_REACH,
        }
        p.moved_this_frame = False
        p.noise_radius = 0.0
        p.lean_t = 0.0
        p.peek_x, p.peek_y = p.x, p.y
        self.sounds.set_growl(False)
        self.sounds.play_angel_stinger()
        self.sounds.start_angel_choir()
        self._release_mouse()
        self.state = "angel_seq"

    def _update_angel_sequence(self, dt):
        a = self.angel_seq
        a["t"] += dt
        p = self.player
        ax, ay, az = a["pos"]
        ang_to = math.atan2(ay - p.y, ax - p.x)
        rel = (ang_to - a["yaw"] + math.pi) % (2 * math.pi) - math.pi
        self.sounds.update_angel_voice(max(-1.0, min(1.0, math.sin(rel))), a["flood_t"])
        p.moved_this_frame = False
        p.noise_radius = 0.0
        if a["phase"] == "notice":
            frac = min(1.0, a["t"] / S.ANGEL_NOTICE_SECONDS)
            a["flood_t"] = 0.16 * frac
            if frac >= 1.0:
                a["phase"], a["t"] = "turn", 0.0
        elif a["phase"] == "turn":
            frac = min(1.0, a["t"] / S.ANGEL_TURN_SECONDS)
            ease = frac * frac * (3.0 - 2.0 * frac)
            da = (a["target_yaw"] - a["start_yaw"] + math.pi) % (2 * math.pi) - math.pi
            a["yaw"] = (a["start_yaw"] + da * ease) % math.tau
            a["pitch"] = a["start_pitch"] + (a["target_pitch"] - a["start_pitch"]) * ease
            a["flood_t"] = 0.16 + 0.64 * ease
            if frac >= 1.0:
                a["yaw"], a["pitch"], a["flood_t"] = a["target_yaw"], a["target_pitch"], 0.8
                a["phase"], a["t"] = "reveal", 0.0
        elif a["phase"] == "reveal":
            a["flood_t"] = min(0.92, 0.8 + 0.12 * (a["t"] / 0.6))
            a["ray"] = S.ANGEL_RAY_STRENGTH * min(1.0, a["t"] / max(0.01, S.ANGEL_LOOK_SECONDS))
            if a["t"] >= S.ANGEL_LOOK_SECONDS:
                a["phase"], a["t"] = "burn", 0.0
        elif a["phase"] == "burn":
            frac = min(1.0, a["t"] / S.ANGEL_BURN_SECONDS)
            a["burn"] = a["reach"] * (frac ** 2.0)
            a["ray"] = S.ANGEL_RAY_STRENGTH * (1.0 + frac * 0.6)
            a["flood_t"] = 0.92 + 0.08 * frac
            if frac >= 1.0:
                a["phase"], a["t"] = "whiteout", 0.0
        elif a["phase"] == "whiteout":
            a["flood_t"] = 1.0
            a["burn"] = a["reach"]
            if a["t"] >= S.ANGEL_WHITEOUT_SECONDS:
                self._start_angel_end()
        p.peek_x, p.peek_y = p.x, p.y

    def _angel_camera_override(self):
        if self.angel_seq is None:
            return None
        p = self.player
        eye = (p.x, p.y, EYE_HEIGHT)
        return eye, self.angel_seq["yaw"], self.angel_seq["pitch"], FOV_DEGREES

    def _start_angel_end(self):
        self.angel_seq = None
        self.state = "angel_end"
        self.catch_timer = 0.0
        self.sounds.set_growl(False)
        self.sounds.stop_angel_choir()
        self._release_mouse()

    def _update_angel_end(self, dt):
        self.catch_timer += dt

    def _call_elevator(self):
        self.elevator_called = True
        self.elevator_call_t = 0.0
        self.elevator_arrived = False
        self.elevator_guard_idle_t = 0.0
        self._elevator_lit_index = 0
        self.monster.guard_mode = True
        if self.monster.state not in (Monster.HUNT, Monster.STALK):
            self._teleport_monster_if_far(self.exit_prop.x, self.exit_prop.y, S.MONSTER_GUARD_RADIUS)
        if self.monster.state == Monster.PATROL:
            self.monster._enter_patrol()
        self.hint_text = i18n.Text("hint.elevator_called")
        self.hint_timer = 3.0
        self.sounds.play_ui()

    def _update_elevator_call(self, dt):
        if not self.elevator_called:
            return
        if not self.elevator_arrived:
            self.elevator_call_t += dt
            idx = self._elevator_active_light_index()
            if idx != self._elevator_lit_index:
                self._elevator_lit_index = idx
                self.sounds.play_elevator_floor_ding()
            if self.elevator_call_t >= S.ELEVATOR_ARRIVE_SECONDS:
                self.elevator_arrived = True
                self.elevator_guard_idle_t = 0.0
                self.hint_text = i18n.Text("hint.elevator_arrived")
                self.hint_timer = 3.0
                self.sounds.play_unlock()
        else:
            self.elevator_guard_idle_t += dt
            if self.elevator_guard_idle_t >= S.ELEVATOR_GUARD_TIMEOUT_SECONDS:
                self.elevator_called = False
                self.elevator_arrived = False
                self.elevator_call_t = 0.0
                self.elevator_guard_idle_t = 0.0
                self._elevator_lit_index = 0
                self.monster.guard_mode = False
                pan, vol = self._pan_vol_for(self.exit_prop.x, self.exit_prop.y)
                self.sounds.play_door(pan, vol)


    CABIN_KINDS = ("elevator", "elevator_arrival")

    @staticmethod
    def _cabin_point(prop, forward, right=0.0):
        fx, fy = math.cos(prop.facing), math.sin(prop.facing)
        wall = prop.hd + 0.005
        return (prop.x + fx * (forward - wall) - fy * right, prop.y + fy * (forward - wall) + fx * right)

    def _sync_wall_holes(self):
        key = (id(self.props), len(self.props))
        if getattr(self, "_wall_holes_key", None) == key:
            return
        self._wall_holes_key = key
        holes = []
        for p in self.props:
            if p.kind not in self.CABIN_KINDS:
                continue
            fx, fy = math.cos(p.facing), math.sin(p.facing)
            wx, wy = self._cabin_point(p, 0.0)
            if abs(fx) > abs(fy):
                holes.append((wx, p.y, R3D_ELEVATOR_OPENING_HALF, 0.0))
            else:
                holes.append((wy, p.x, R3D_ELEVATOR_OPENING_HALF, 1.0))
        self.renderer.set_wall_holes(holes)

    def _set_cabin_door(self, prop, door_t):
        prop.door_t = door_t
        open_frac = 1.0 - door_t
        if open_frac > 0.02:
            prop.light_radius = S.ELEVATOR_CABIN_LIGHT_RADIUS
            prop.light_color = tuple(c * open_frac for c in S.ELEVATOR_CABIN_LIGHT_COLOR)
        else:
            prop.light_radius = None

    def _update_elevator_doors(self, dt):
        if self.elevator_ride is not None:
            return
        step = dt / S.ELEVATOR_DOOR_SWING_SECONDS
        for p in self.props:
            if p.kind not in self.CABIN_KINDS:
                continue
            target = 0.0 if (p is self.exit_prop and self.elevator_arrived) else 1.0
            cur = getattr(p, "door_t", 1.0)
            if cur != target:
                cur = min(target, cur + step) if target > cur else max(target, cur - step)
                self._set_cabin_door(p, cur)

    def _start_elevator_ride(self):
        p = self.player
        panel = self.exit_prop
        self.elevator_called = False
        self.elevator_arrived = False
        self.monster.guard_mode = False
        self.elevator_ride = {
            "phase": "enter", "t": 0.0, "cabin": panel,
            "sx": p.x, "sy": p.y, "sa": p.angle, "sp": p.pitch,
            "flashlight_was_on": p.flashlight_on, "lighter_was_on": p.lighter_on,
            "swapped": False,
        }
        p.moved_this_frame = False
        p.noise_radius = 0.0
        p.lean_t = 0.0
        p.peek_x, p.peek_y = p.x, p.y
        p.map_open = False
        self.sounds.set_growl(False)
        self._battery_warned = False
        self.state = "elevator_ride"

    def _load_floor_for_elevator_arrival(self):
        r = self.elevator_ride
        if self._in_debug_preview():
            self._build_debug_scene()
            arrival = None
        else:
            self._load_floor(self.floor_i + 1)
            rng = random.Random(self.floor_seed ^ 0xE1EA)
            used_cells = {p.interact_cell for p in self.props if getattr(p, "interact_cell", None) is not None}
            arrival = place_arrival_prop(self.maze, "elevator_arrival", rng, used=used_cells)
        p = self.player
        if arrival is not None:
            self.props.append(arrival)
            self._set_cabin_door(arrival, 1.0)
            p.x, p.y = self._cabin_point(arrival, -S.ELEVATOR_CABIN_STAND)
            p.angle = arrival.facing % math.tau
        else:
            p.x, p.y = self.maze.start
        p.pitch = 0.0
        r["cabin"] = arrival

    def _update_elevator_ride(self, dt):
        r = self.elevator_ride
        r["t"] += dt
        p = self.player
        phase = r["phase"]
        cabin = r["cabin"]
        p.moved_this_frame = False
        p.noise_radius = 0.0
        p.update_held_item(dt, force_stow=True)

        def ease(f):
            f = max(0.0, min(1.0, f))
            return f * f * (3.0 - 2.0 * f)

        def turn_to(a0, a1, f):
            da = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
            return (a0 + da * f) % math.tau

        if phase == "enter":
            frac = min(1.0, r["t"] / S.ELEVATOR_RIDE_ENTER_SECONDS)
            w1 = self._cabin_point(cabin, S.ELEVATOR_DOORWAY_STEP)
            w2 = self._cabin_point(cabin, -S.ELEVATOR_CABIN_STAND)
            inward = (cabin.facing + math.pi) % math.tau
            if frac < 0.45:
                f = ease(frac / 0.45)
                p.x, p.y = r["sx"] + (w1[0] - r["sx"]) * f, r["sy"] + (w1[1] - r["sy"]) * f
                p.angle = turn_to(r["sa"], inward, f)
                p.pitch = r["sp"] * (1.0 - f)
            elif frac < 0.75:
                f = ease((frac - 0.45) / 0.30)
                p.x, p.y = w1[0] + (w2[0] - w1[0]) * f, w1[1] + (w2[1] - w1[1]) * f
                p.angle, p.pitch = inward, 0.0
            else:
                p.x, p.y = w2
                p.angle = turn_to(inward, cabin.facing, ease((frac - 0.75) / 0.25))
            if frac >= 1.0:
                p.angle = cabin.facing % math.tau
                r["phase"], r["t"] = "closing", 0.0
                self.sounds.play_elevator_door()
        elif phase == "closing":
            frac = min(1.0, r["t"] / S.ELEVATOR_DOOR_SWING_SECONDS)
            self._set_cabin_door(cabin, ease(frac))
            if frac >= 1.0:
                r["phase"], r["t"] = "ride", 0.0
                self.sounds.start_elevator_hum()
        elif phase == "ride":
            if not r["swapped"] and r["t"] >= S.ELEVATOR_DESCEND_HOLD_SECONDS * S.ELEVATOR_SWAP_FRAC:
                r["swapped"] = True
                self._load_floor_for_elevator_arrival()
                cabin = r["cabin"]
            if r["t"] >= S.ELEVATOR_DESCEND_HOLD_SECONDS:
                self.sounds.stop_elevator_hum()
                if cabin is None:
                    self.elevator_ride = None
                    p.flashlight_on = r["flashlight_was_on"]
                    p.lighter_on = r["lighter_was_on"]
                    self._begin_playing()
                    return
                r["phase"], r["t"] = "opening", 0.0
                self.sounds.play_elevator_door()
        elif phase == "opening":
            frac = min(1.0, r["t"] / S.ELEVATOR_DOOR_SWING_SECONDS)
            self._set_cabin_door(cabin, 1.0 - ease(frac))
            if frac >= 1.0:
                r["phase"], r["t"] = "exit", 0.0
        elif phase == "exit":
            frac = min(1.0, r["t"] / S.ELEVATOR_RIDE_EXIT_SECONDS)
            w0 = self._cabin_point(cabin, -S.ELEVATOR_CABIN_STAND)
            w1 = self._cabin_point(cabin, S.ELEVATOR_EXIT_STEP_DIST)
            f = ease(frac)
            p.x, p.y = w0[0] + (w1[0] - w0[0]) * f, w0[1] + (w1[1] - w0[1]) * f
            if frac >= 1.0:
                p.flashlight_on = r["flashlight_was_on"]
                p.lighter_on = r["lighter_was_on"]
                self.elevator_ride = None
                self._begin_playing()
        p.peek_x, p.peek_y = p.x, p.y

    def _elevator_camera_override(self):
        r = self.elevator_ride
        if r is None:
            return None
        p = self.player
        life_yaw, life_pitch = self._cam_life(self.anim_t, 0.8)
        if r["phase"] != "ride":
            return ((p.x, p.y, EYE_HEIGHT), p.angle + life_yaw, p.pitch + life_pitch, FOV_DEGREES)
        t = self.anim_t
        k = S.ELEVATOR_SHAKE * max(0.0, min(1.0, r["t"] / 0.3, (S.ELEVATOR_DESCEND_HOLD_SECONDS - r["t"]) / 0.3))
        eye = (p.x + math.sin(t * 37.0) * k, p.y + math.sin(t * 29.0 + 1.3) * k,
               EYE_HEIGHT + math.sin(t * 43.0 + 0.7) * k)
        return (eye, p.angle + life_yaw, p.pitch + life_pitch + math.sin(t * 23.0) * k * 0.4,
                FOV_DEGREES)

    def _update_hatch_turn(self, dt, interact_held):
        target = None
        if interact_held and self.hatch_climb is None and self.elevator_ride is None:
            res = self.find_interactable()
            if res is not None:
                kind, obj = res
                if kind == "exit" and obj.kind == "hatch" and obj.powered and not self._in_debug_showcase():
                    target = obj
        was_turning = self.hatch_turning
        self.hatch_turning = target is not None
        if self.hatch_turning and not was_turning:
            self.sounds.start_hatch_turn_loop()
        elif was_turning and not self.hatch_turning:
            self.sounds.stop_action_loop()
        hatch = target
        if hatch is None:
            hatch = next((q for q in self.props if q.kind == "hatch"), None)
        if hatch is not None:
            rate = S.HATCH_WHEEL_TURNS * math.tau / S.HATCH_TURN_SECONDS
            spin = getattr(hatch, "wheel_spin", 0.0)
            if target is None:
                hatch.wheel_spin = spin + self._hatch_wheel_coast * dt
                self._hatch_wheel_coast *= max(0.0, 1.0 - dt * S.HATCH_WHEEL_EASE)
            else:
                self._hatch_wheel_coast = rate
                hatch.wheel_spin = spin + rate * dt
        if target is None:
            return
        self.hatch_turn_progress += dt / S.HATCH_TURN_SECONDS
        self._hatch_creak_timer += dt
        if self._hatch_creak_timer >= 1.0:
            self._hatch_creak_timer -= 1.0
            if random.random() < S.HATCH_CREAK_CHANCE_PER_SEC:
                self._play_hatch_creak(target)
        if self.hatch_turn_progress >= 1.0:
            self.hatch_turn_progress = 1.0
            self.hatch_turning = False
            self.sounds.stop_action_loop()
            self._start_hatch_climb()

    def _play_hatch_creak(self, hatch):
        pan, vol = self._pan_vol_for(hatch.x, hatch.y)
        self.sounds.play_door(pan, vol)
        self.hint_text = i18n.Text("hint.hatch_creaking")
        self.hint_timer = 2.5
        if self.monster.state not in (Monster.HUNT, Monster.STALK):
            self._teleport_monster_if_far(hatch.x, hatch.y, S.MONSTER_GUARD_RADIUS)
            self.monster.notify_global_noise(self.maze, hatch.interact_cell)

    def _start_hatch_climb(self):
        p = self.player
        hatch = self.exit_prop
        walk = math.hypot(hatch.x - p.x, hatch.y - p.y)
        self.hatch_climb = {
            "phase": "approach" if walk > 0.2 else "open", "t": 0.0,
            "walk_t": walk / S.HATCH_WALK_SPEED,
            "sx": p.x, "sy": p.y, "sa": p.angle, "sp": p.pitch,
            "hx": hatch.x, "hy": hatch.y, "facing": hatch.facing,
            "ax": hatch.x, "ay": hatch.y,
            "step_x": None, "step_y": None,
            "yaw": p.angle, "pitch": p.pitch, "z": EYE_HEIGHT, "moon": 0.0,
            "swapped": False,
            "flashlight_was_on": p.flashlight_on, "lighter_was_on": p.lighter_on,
        }
        hatch.hatch_open_t = 0.0
        hatch.light_color = S.HATCH_SKY_LIGHT_COLOR
        hatch.light_at = (0.0, 0.0, R3D_HATCH_SHAFT_RISE * 0.55)
        hatch.light_lobe = (0.0, 0.0, -1.0, -0.15, 0.75, 0.0)
        hatch.light_radius = 0.0
        p.moved_this_frame = False
        p.noise_radius = 0.0
        p.lean_t = 0.0
        p.peek_x, p.peek_y = p.x, p.y
        self.sounds.set_growl(False)
        self.sounds.play_elevator_door()
        self._battery_warned = False
        self.state = "hatch_climb"

    def _load_floor_for_hatch_arrival(self, facing=0.0):
        if self._in_debug_preview():
            self._build_debug_scene()
        else:
            self._load_floor(self.floor_i + 1)
        sx, sy = self.maze.start
        arrival = Prop("hatch_arrival", sx, sy, facing=facing)
        arrival.hatch_open_t = 1.0
        self.props = [q for q in self.props
                      if q.wall_mounted or math.hypot(q.x - sx, q.y - sy) > 0.75]
        self.props.append(arrival)
        self.renderer.rebuild_floor(self.maze, (arrival.x, arrival.y, R3D_HATCH_HALF))
        return arrival

    def _hatch_step_off(self, arrival):
        cx, cy = int(arrival.x), int(arrival.y)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, 1), (1, -1), (-1, -1)):
            nx, ny = cx + dx, cy + dy
            if self.maze.is_walkable_cell(nx, ny) and not self.maze.circle_hits_wall(
                    nx + 0.5, ny + 0.5, S.PLAYER_RADIUS):
                return nx + 0.5, ny + 0.5
        return arrival.x, arrival.y

    HATCH_YARD_Z = S.WALL_HEIGHT + R3D_HATCH_SHAFT_RISE

    def _update_hatch_climb(self, dt):
        hc = self.hatch_climb
        hc["t"] += dt
        p = self.player
        phase = hc["phase"]
        p.moved_this_frame = False
        p.noise_radius = 0.0
        p.update_held_item(dt)
        hatch = self.exit_prop
        if phase == "approach":
            frac = min(1.0, hc["t"] / max(0.2, hc["walk_t"]))
            ease = frac * frac * (3.0 - 2.0 * frac)
            look = math.atan2(hc["hy"] - hc["sy"], hc["hx"] - hc["sx"])
            hc["yaw"] = hc["sa"] + _wrap_angle(look - hc["sa"]) * min(1.0, frac * 2.0)
            hc["pitch"] = hc["sp"] * (1.0 - ease)
            hc["walk"] = ease
            if frac >= 1.0:
                hc["sx"], hc["sy"] = hc["hx"], hc["hy"]
                hc["sa"], hc["sp"] = hc["yaw"], hc["pitch"]
                hc["phase"], hc["t"] = "open", 0.0
        elif phase == "open":
            frac = min(1.0, hc["t"] / S.HATCH_OPEN_SECONDS)
            ease = frac * frac * (3.0 - 2.0 * frac)
            lid = max(0.0, (frac - 0.38) / 0.62)
            lid = lid * lid * (3.0 - 2.0 * lid)
            if hatch is not None and hatch.kind == "hatch":
                hatch.hatch_open_t = lid
                hatch.light_radius = S.HATCH_SKY_LIGHT_RADIUS * lid
            look = math.atan2(hc["hy"] - hc["sy"], hc["hx"] - hc["sx"])
            hc["yaw"] = hc["sa"] + _wrap_angle(look - hc["sa"]) * ease
            hc["pitch"] = hc["sp"] + (S.HATCH_LOOK_PITCH - hc["sp"]) * ease
            hc["moon"] = S.HATCH_CLIMB_MOON * lid
            if frac >= 1.0:
                hc["phase"], hc["t"] = "climb", 0.0
        elif phase == "climb":
            frac = min(1.0, hc["t"] / S.HATCH_CLIMB_SECONDS)
            ease = frac * frac * (3.0 - 2.0 * frac)
            rungs = S.HATCH_CLIMB_RUNGS
            step = math.sin(ease * rungs * math.tau) * S.HATCH_CLIMB_RUNG_RISE
            sway = math.sin(ease * rungs * math.pi + 0.7) * S.HATCH_CLIMB_RUNG_SWAY
            hc["z"] = EYE_HEIGHT + self.HATCH_YARD_Z * ease + step
            hc["sway"] = sway
            hc["pitch"] = S.HATCH_LOOK_PITCH * (1.0 - min(1.0, max(0.0, (frac - 0.55) / 0.45)) ** 2)
            hc["moon"] = S.HATCH_CLIMB_MOON
            hc["out"] = max(0.0, (frac - 0.72) / 0.28) ** 2
            if not hc["swapped"] and frac >= S.HATCH_SWAP_FRAC:
                arrival = self._load_floor_for_hatch_arrival(hc["facing"])
                hc["ax"], hc["ay"] = arrival.x, arrival.y
                hc["step_x"], hc["step_y"] = self._hatch_step_off(arrival)
                hc["swapped"] = True
            if frac >= 1.0:
                hc["phase"], hc["t"] = "step", 0.0
                if hc["step_x"] is None:
                    hc["step_x"], hc["step_y"] = p.x, p.y
        elif phase == "step":
            frac = min(1.0, hc["t"] / S.HATCH_STEP_SECONDS)
            ease = frac * frac * (3.0 - 2.0 * frac)
            hc["pitch"] = hc["pitch"] * (1.0 - ease)
            hc["moon"] = S.HATCH_CLIMB_MOON * (1.0 - ease)
            if frac >= 1.0:
                p.x, p.y = hc["step_x"], hc["step_y"]
                p.pitch = 0.0
                p.angle = hc["yaw"]
                self._trip_cam_yaw, self._trip_cam_pitch = p.angle, 0.0
                self._trip_target_unwrapped = self._trip_follower_unwrapped = p.angle
                self._trip_prev_player_angle = p.angle
                p.flashlight_on = hc["flashlight_was_on"]
                p.lighter_on = hc["lighter_was_on"]
                self.hatch_climb = None
                self._begin_playing()
                return
        p.peek_x, p.peek_y = p.x, p.y

    def _hatch_camera_override(self):
        if self.hatch_climb is None:
            return None
        hc = self.hatch_climb
        z = hc["z"]
        if hc["swapped"]:
            z -= self.HATCH_YARD_Z
            bx, by = hc["ax"], hc["ay"]
        else:
            bx, by = hc["hx"], hc["hy"]
        if hc["phase"] == "approach":
            w = hc.get("walk", 0.0)
            x = hc["sx"] + (hc["hx"] - hc["sx"]) * w
            y = hc["sy"] + (hc["hy"] - hc["sy"]) * w
            z += math.sin(hc["t"] * 9.0) * 0.012
        elif hc["phase"] == "open":
            x, y = hc["sx"], hc["sy"]
        elif hc["phase"] == "climb":
            out = hc.get("out", 0.0)
            if out > 0.0 and hc["step_x"] is not None:
                x = bx + (hc["step_x"] - bx) * out
                y = by + (hc["step_y"] - by) * out
            else:
                x, y = bx, by
            sway = hc.get("sway", 0.0)
            if sway:
                sx_, sy_ = -math.sin(hc["yaw"]), math.cos(hc["yaw"])
                x += sx_ * sway
                y += sy_ * sway
        else:
            x, y = hc["step_x"], hc["step_y"]
        amount = 1.0 if hc["phase"] in ("approach", "climb") else 0.6
        life_yaw, life_pitch = self._cam_life(self.anim_t, amount)
        return (x, y, z), hc["yaw"] + life_yaw, hc["pitch"] + life_pitch, FOV_DEGREES

    def _menu_scene_active(self):
        if self.state in ("menu", "credits", "warning", "update_prompt", "update_downloading"):
            return True
        if self.state in ("settings", "gamma_calibration") and self.settings_return == "menu":
            return True
        return self.state == "confirm_quit" and self.confirm_return == "menu"

    def _menu_camera_override(self):
        cam = getattr(self, "_menu_cam", None)
        if cam is None:
            return None
        x, y, yaw0 = cam
        ph = self.anim_t * S.MENU_CAM_RATE
        yaw = yaw0 + math.sin(ph) * S.MENU_CAM_SWAY
        fx, fy = math.cos(yaw0), math.sin(yaw0)
        side = math.sin(ph * 0.73 + 1.2) * S.MENU_CAM_DOLLY
        fwd = math.sin(ph * 0.41) * S.MENU_CAM_DOLLY * 0.6
        eye = (x - fy * side + fx * fwd, y + fx * side + fy * fwd,
               EYE_HEIGHT + math.sin(ph * 0.6 + 0.4) * 0.035)
        pitch = -0.02 + math.sin(ph * 0.53) * 0.02
        return (eye, yaw, pitch, FOV_DEGREES)

    def _active_camera_override(self):
        if self._menu_scene_active():
            return self._menu_camera_override()
        if self.fence_escape is not None:
            return self._fence_escape_camera_override()
        if self.hatch_climb is not None:
            return self._hatch_camera_override()
        if self.elevator_ride is not None:
            override = self._elevator_camera_override()
            if override is not None:
                return override
        if self.angel_seq is not None:
            return self._angel_camera_override()
        if self.state in ("catch", "dead_caught"):
            override = self._catch_camera_override()
            if override is not None:
                return override
        return self._peek_camera_override()

    def _enter_debug_peek(self, target_floor, live=False):
        self._load_floor(target_floor)
        self.spec = dict(self.spec)
        self.spec["no_threat"] = not live
        if not live:
            self.monster.speed_mult = 0.0
            self.monster.vision_mult = 0.0
            self.player.sanity = S.SANITY_MAX
        self.debug_demo_monsters = []
        self.debug_door_monster = None
        self.debug_door_monster_door = None
        self._debug_door_goal = None
        self._debug_door_ends = None
        self._debug_door_corridor_set = None
        sx, sy = self.maze.start
        ret = make_prop("portal", (int(sx), int(sy)), facing=0.0)
        ret.target_floor = "hub"
        ret.portal_live = False
        self.props.append(ret)
        self.floor_banner = i18n.Text("debug.peek_banner_live" if live else "debug.peek_banner",
                                       title=self._spec_text("title"))
        self.floor_banner_timer = 4.0
        self.hint_text = i18n.Text("debug.peek_hint")
        self.hint_timer = 6.0
        self.sounds.play_ui()

    def update(self, dt):
        dt = min(dt, 0.05)
        self.anim_t += dt
        self.frame_dt = dt
        self._stamina_seen_t = max(0.0, getattr(self, "_stamina_seen_t", 0.0) - dt)
        target = 1.0 if self._status_held() else 0.0
        self._clip_raise += (target - self._clip_raise) * min(1.0, dt * 11.0)
        self._update_map_turn(dt)
        self._sync_map_pencil(dt)
        if self.console.open:
            self.console.tick(dt)
            return
        dt *= self.console.time_scale
        if self.state == "note":
            step = dt / max(0.01, S.NOTE_FADE_SECONDS)
            if self._note_closing:
                self._note_fade = max(0.0, self._note_fade - step)
                if self._note_fade <= 0.0:
                    self._finish_close_note()
                return
            self._note_fade = min(1.0, self._note_fade + step)
            mouse_dx, mouse_dy = pygame.mouse.get_rel()
            if self._binding_down("map_pencil"):
                self._note_settling = False
                self._turn_note(mouse_dx, mouse_dy)
            elif self._note_settling:
                rate = min(1.0, dt * S.NOTE_SETTLE_RATE)
                done = True
                for i in (0, 1):
                    a = (self.note_spin[i] + math.pi) % math.tau - math.pi
                    a -= a * rate
                    self.note_spin[i] = 0.0 if abs(a) < 0.004 else a
                    done = done and self.note_spin[i] == 0.0
                self.renderer.note_spin = (self.note_spin[0], self.note_spin[1])
                self._note_settling = not done
            return
        if self.state == "splash":
            self._update_splash(dt)
        elif self.state in ("menu", "credits", "warning"):
            self._sync_menu_player()
            self.sounds.set_ambient_volume(0.13)
        elif self.state == "playing":
            self._update_playing(dt)
        elif self.state == "elevator_ride":
            self._update_elevator_ride(dt)
        elif self.state == "hatch_climb":
            self._update_hatch_climb(dt)
        elif self.state == "fence_escape":
            self._update_fence_escape(dt)
        elif self.state == "angel_seq":
            self._update_angel_sequence(dt)
        elif self.state == "catch":
            self._update_catch(dt)
        elif self.state == "catch_sanity":
            self._update_sanity_death(dt)
        elif self.state == "win_seq":
            self._update_win_seq(dt)
        elif self.state == "win":
            self._update_win(dt)
        elif self.state == "angel_end":
            self._update_angel_end(dt)
        elif self.state == "settings":
            if self.settings_return == "menu":
                self._sync_menu_player()
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

        if self._menu_scene_active():
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
            self._update_hatch_turn(dt, False)
            self._update_fence_cut(dt, False)
            self._update_cutters_repair(dt, False)
            self.player.update_lean(dt, False, False, self.maze, self._scene_props())
            self._resolve_lean_input(dt, suppress=True)
        else:
            keys = pygame.key.get_pressed()
            blockers = self._scene_props()
            if self.is_peeking:
                self.player.moved_this_frame = False
                self.player.noise_radius = 0.0
                self._resolve_lean_input(dt, suppress=True)
                self.player.update_lean(dt, False, False, self.maze, blockers)
            else:
                keys_down = {
                    "forward": self._binding_down("forward") or keys[pygame.K_UP],
                    "back": self._binding_down("back") or keys[pygame.K_DOWN],
                    "left": self._binding_down("left"),
                    "right": self._binding_down("right"),
                    "sprint": self._binding_down("sprint"),
                }
                turn_left = keys[pygame.K_LEFT]
                turn_right = keys[pygame.K_RIGHT]
                crouch_held = self._binding_down("crouch")
                if self._update_map_drawing(mouse_dx, mouse_dy):
                    mouse_dx = mouse_dy = 0
                sens = sens_y = self.settings["mouse_sensitivity"]
                if self._debug_spectator:
                    self._fly_move(dt, mouse_dx * sens, mouse_dy * sens_y)
                else:
                    self.player.update_movement(
                        dt, keys_down, mouse_dx * sens, mouse_dy * sens_y, self.maze, blockers,
                        turn_left, turn_right, crouch_held=crouch_held,
                        infinite_stamina=self.spec.get("no_threat", False),
                        effective_facing=self._trip_cam_yaw,
                        comedown_intensity=self.comedown_intensity,
                    )
                    lean_left, lean_right = self._resolve_lean_input(dt, suppress=self.player.is_hiding)
                    self.player.update_lean(dt, lean_left, lean_right, self.maze, blockers)
            held = self._binding_down("interact") and not self._debug_spectator
            self._update_install_hold(dt, held)
            self._update_peek_hold(dt, held)
            self._update_hatch_turn(dt, held)
            self._update_fence_cut(dt, held)
            self._update_cutters_repair(dt, held)
        self._update_elevator_call(dt)
        self._update_elevator_doors(dt)
        if not self.spec.get("no_threat"):
            if self.player.update_flashlight(dt) and self.player.has_lighter:
                self.hint_text = i18n.Text("hint.lighter_no_battery")
                self.hint_timer = 3.0
        self.player.update_held_item(dt, force_stow=self.hide_transition is not None)
        self._update_player_lit()

        fade_dir = 1.0 if self.player.is_hiding else -1.0
        self.hide_vignette_t = max(0.0, min(S.HIDE_VIGNETTE_FADE,
                                             self.hide_vignette_t + fade_dir * dt))

        self.player.tick_noise(dt)
        gated = self._update_mic_level(dt)
        if gated > 0.0:
            self.player.noise_radius = max(self.player.noise_radius, gated * S.NOISE_SPRINT)

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
            if door.just_landed:
                pan, vol = self._pan_vol_for(door.x, door.y)
                self.sounds.play_bang(pan, vol)
                self.fx_shake.add(0.55)
            if door.just_auto_opened:
                pan, vol = self._pan_vol_for(door.x, door.y)
                self.sounds.play_door(pan, vol)
            if door.just_auto_latched:
                pan, vol = self._pan_vol_for(door.x, door.y)
                self.sounds.play_latch(pan, vol)

        if self.panel_prop is not None and hasattr(self.panel_prop, "swing_target"):
            sw = getattr(self.panel_prop, "swing", 0.0)
            self.panel_prop.swing = sw + (self.panel_prop.swing_target - sw) * min(1.0, dt * 3.0)

        self.elapsed += dt
        self.floor_elapsed += dt
        time_frac = min(1.0, self.floor_elapsed / S.DREAD_RAMP_SECONDS)
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
            self.monster.update(dt, self.maze, self.player, self.dread, self.props, grace=grace,
                                blind=self._debug_spectator)
            if self.monster.just_noticed:
                self.sounds.play_alert()
                self.fx_shake.add(0.25)
            if self.monster.just_opened_door is not None:
                d = self.monster.just_opened_door
                pan, vol = self._pan_vol_for(d.x, d.y)
                self.sounds.play_door(pan, vol)
            if self.monster.just_closed_door is not None:
                d = self.monster.just_closed_door
                pan, vol = self._pan_vol_for(d.x, d.y)
                self.sounds.play_door(pan, vol)
        if self.debug_door_monster is not None:
            dm = self.debug_door_monster
            dm.update(dt, self.maze, self.player, 0.0, [], grace=False)
            if dm.just_opened_door is not None:
                d = dm.just_opened_door
                pan, vol = self._pan_vol_for(d.x, d.y)
                self.sounds.play_door(pan, vol)
            if dm.just_closed_door is not None:
                d = dm.just_closed_door
                pan, vol = self._pan_vol_for(d.x, d.y)
                self.sounds.play_door(pan, vol)
            end_a, end_b = self._debug_door_ends
            if dm.cell not in self._debug_door_corridor_set:
                nearest = min((end_a, end_b), key=lambda c: (c[0] - dm.x) ** 2 + (c[1] - dm.y) ** 2)
                dm.x, dm.y = nearest[0] + 0.5, nearest[1] + 0.5
                dm.path = []
                self._debug_door_goal = None
            if self._debug_door_goal is None:
                self._debug_door_goal = end_b
            if not dm.path:
                if dm.cell == self._debug_door_goal:
                    self._debug_door_goal = end_a if self._debug_door_goal == end_b else end_b
                dm.target_cell = self._debug_door_goal
                dm._replan(self.maze, self._debug_door_goal)
            door = self.debug_door_monster_door
            if door is not None and door.is_broken:
                door.is_broken = False
                door.is_open = False
                door.is_latched = False
                door._pending_latch = False
                door.swing = 0.0
                door._swing_target = 0.0
                door.break_askew = 0.0
                door.base_color = (96, 64, 40)
        self._update_door_break_feedback(dt)

        self._update_sanity(dt)
        self._update_audio_3d(dt)
        self._update_scares(dt)
        self._update_hallucinations(dt)

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
        self._update_pill_trip(dt)

        if self.monster.caught_player and not self.console.god:
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
            d.take_hit(from_xy=(self.monster.x, self.monster.y),
                       progress=self.monster.break_timer / S.DOOR_BREAK_SECONDS)
            self._door_break_sfx_timer = 0.4

    def _update_sanity(self, dt):
        if self.spec.get("no_threat"):
            return
        if self._debug_spectator:
            self.player.sanity = S.SANITY_MAX
            return
        p, m = self.player, self.monster
        dist = math.hypot(m.x - p.x, m.y - p.y)
        vision = monster_vision_base(p.light_level, m.vision_light_norm) * m.vision_mult
        vision *= self.maze.sight_transmission(m.x, m.y, p.x, p.y)
        covered = dist < vision and p.is_crouching and line_blocked_by_cover(self.props, m.x, m.y, p.x, p.y)
        visible = (not p.is_hiding) and dist < vision and not covered
        near_range = max(S.SANITY_NEAR_RANGE, vision * S.SANITY_TERROR_VISION_MULT)
        proximity_dread = 0.0 if p.is_hiding else max(0.0, 1.0 - dist / near_range)
        in_dark_drain = False
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
                drain_mult = S.LIGHTER_DARK_DRAIN_MULT if p.lighter_on else 1.0
                p.apply_sanity(-S.SANITY_HIDE_DRAIN * crouch_mult * drain_mult * dt)
        else:
            boosted = self.sanity_boost_timer > 0.0
            if boosted or p.is_lit:
                if dist > max(S.SANITY_SAFE_RANGE, near_range):
                    regen_rate = S.SANITY_REGEN * (S.SANITY_PILL_REGEN_MULT if boosted else 1.0)
                    p.apply_sanity(regen_rate * dt)
            elif dist > S.SANITY_DARK_RANGE:
                in_dark_drain = True
                drain_mult = S.LIGHTER_DARK_DRAIN_MULT if p.lighter_on else 1.0
                ramp = self._dark_time_t / S.SANITY_DARK_DRAIN_RAMP_SECONDS
                drain_mult *= min(S.SANITY_DARK_DRAIN_MAX_MULT, 1.0 + ramp * (S.SANITY_DARK_DRAIN_MAX_MULT - 1.0))
                p.apply_sanity(-S.SANITY_DARK_DRAIN * drain_mult * dt)
        self._dark_time_t = self._dark_time_t + dt if in_dark_drain else 0.0

    @staticmethod
    def _smoothstep(t):
        t = max(0.0, min(1.0, t))
        return t * t * (3.0 - 2.0 * t)

    def _update_pill_trip(self, dt):
        if self.sanity_boost_timer > 0.0:
            elapsed = S.SANITY_PILL_DURATION - self.sanity_boost_timer
            fade_in = self._smoothstep(elapsed / S.PILL_TRIP_FADE_IN_SECONDS)
            fade_out = self._smoothstep(self.sanity_boost_timer / S.PILL_TRIP_FADE_OUT_SECONDS)
            self.trip_intensity = max(0.0, min(fade_in, fade_out))
            self._comedown_remaining = S.PILL_COMEDOWN_SECONDS * fade_in * (1.0 - fade_out)
            self.comedown_intensity = fade_in * (1.0 - fade_out)
            self.sounds.update_trip(self.trip_intensity)
            self.sounds.update_comedown(self.comedown_intensity)
        else:
            self.trip_intensity = 0.0
            if self._comedown_remaining > 0.0:
                self._comedown_remaining = max(0.0, self._comedown_remaining - dt)
            self.comedown_intensity = self._smoothstep(
                self._comedown_remaining / S.PILL_COMEDOWN_SECONDS)
            self.sounds.update_trip(0.0)
            self.sounds.update_comedown(self.comedown_intensity)
        self._update_trip_cam_lag(dt)

    def _update_trip_cam_lag(self, dt):
        p = self.player
        raw_delta = (p.angle - self._trip_prev_player_angle + math.pi) % math.tau - math.pi
        self._trip_prev_player_angle = p.angle
        self._trip_target_unwrapped += raw_delta

        drag = self.trip_intensity + self.comedown_intensity * S.PILL_COMEDOWN_CAM_LAG_FRAC
        tau = drag * S.PILL_TRIP_CAM_LAG_SECONDS
        if tau <= 0.0:
            self._trip_follower_unwrapped = self._trip_target_unwrapped
            self._trip_cam_pitch = p.pitch
        else:
            alpha = 1.0 - math.exp(-dt / tau)
            gap = self._trip_target_unwrapped - self._trip_follower_unwrapped
            self._trip_follower_unwrapped += gap * alpha
            self._trip_cam_pitch += (p.pitch - self._trip_cam_pitch) * alpha
        self._trip_cam_yaw = self._trip_follower_unwrapped % math.tau

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

    def _player_has_exit_item(self, req):
        if not getattr(self.player, f"has_{req}", False):
            return False
        if req == "cutters" and self.player.cutters_broken:
            return False
        return True

    def _player_can_see_point(self, x, y):
        p = self.player
        dist = math.hypot(x - p.x, y - p.y)
        if dist > self.spec.get("fog_dist", 12.5):
            return False
        ang_to_point = math.atan2(y - p.y, x - p.x)
        rel = (ang_to_point - p.angle + math.pi) % (2 * math.pi) - math.pi
        if abs(rel) > math.radians(FOV_DEGREES) / 2:
            return False
        return self.maze.has_line_of_sight(p.x, p.y, x, y)

    def _teleport_monster_if_far(self, ax, ay, guard_radius):
        m = self.monster
        if math.hypot(m.x - ax, m.y - ay) <= guard_radius:
            return
        p = self.player
        candidates = [c for c in self.maze.floor_cells()
                      if not self._player_can_see_point(c[0] + 0.5, c[1] + 0.5)]
        if not candidates:
            return
        candidates.sort(key=lambda c: abs(math.hypot(c[0] + 0.5 - p.x, c[1] + 0.5 - p.y)
                                           - S.MONSTER_TELEPORT_DIST))
        tx, ty = candidates[0]
        m.x, m.y = tx + 0.5, ty + 0.5
        m.path = []
        m.target_cell = None

    def _flashlight_lights_point(self, x, y):
        p = self.player
        if not p.flashlight_on:
            return False
        dist = math.hypot(x - p.x, y - p.y)
        if dist < 1e-4 or dist > self._FLASHLIGHT_USEFUL_RANGE:
            return False
        ang_to_point = math.atan2(y - p.y, x - p.x)
        rel = (ang_to_point - p.angle + math.pi) % (2 * math.pi) - math.pi
        if abs(rel) > 0.55:
            return False
        return self.maze.has_line_of_sight(p.x, p.y, x, y)

    def _light_occluded(self, light, point):
        x, y = point[0], point[1]
        if light.shape == SHAPE_DIRECTIONAL:
            return not self.maze.moon_reaches(x, y)
        lx, ly = light.pos[0], light.pos[1]
        if not self.maze.has_line_of_sight(x, y, lx, ly):
            return True
        return line_blocked_by_cover(self.doors, x, y, lx, ly, min_height=0.1)

    def _light_level_at(self, x, y):
        point = (x, y, GAMEPLAY_RECEIVER_HEIGHT)
        lights = gameplay_lights(self.props, point, self.anim_t, self._moon_strength())
        return light_level_at(point, lights, self.spec.get("ambient_level", 0.0), self._light_occluded)

    _IS_LIT_THRESHOLD = 0.12

    def _update_player_lit(self):
        p = self.player
        point = (p.x, p.y, GAMEPLAY_RECEIVER_HEIGHT)
        carried = []
        if self._flashlight_lights_something():
            carried.append(flashlight_light(point, (math.cos(p.angle), math.sin(p.angle), 0.0), 1.0, self.anim_t))
        if p.lighter_on:
            carried.append(lighter_light(point, S.LIGHTER_LIGHT_RADIUS, S.LIGHTER_LIGHT_COLOR,
                                         bearer_reveal=S.LIGHTER_LIGHT_LEVEL_BONUS))
        lights = gameplay_lights(self.props, point, self.anim_t, self._moon_strength())
        level, lit_for_sanity, room = player_light_levels(
            point, lights, self.spec.get("ambient_level", 0.0), self._light_occluded, carried)
        p.is_lit = lit_for_sanity > self._IS_LIT_THRESHOLD
        p.light_level = level
        p.room_light = room

    def _update_scares(self, dt):
        if self.spec.get("no_threat") or self._debug_spectator:
            return
        seconds_dark = S.SCARE_SECONDS_DARK - S.SCARE_SECONDS_DARK_DREAD * self.dread
        seconds_lit = S.SCARE_SECONDS_LIT - S.SCARE_SECONDS_LIT_DREAD * self.dread
        self._scare_lit = self.player.is_lit
        seconds_to_fill = seconds_lit if self._scare_lit else seconds_dark
        self._scare_seconds_to_fill = seconds_to_fill
        if self.scare_cooldown > 0.0:
            self.scare_cooldown -= dt
            return
        self.scare_progress += dt / max(1.0, seconds_to_fill)
        if self.scare_progress >= self.scare_target:
            self.scare_progress = 0.0
            self.scare_target = random.uniform(0.85, 1.15)
            self.scare_cooldown = S.SCARE_MIN_GAP
            self._trigger_random_scare()

    def _update_hallucinations(self, dt):
        if self.spec.get("no_threat") or self._debug_spectator:
            self.hallu_intensity = 0.0
            return
        self._tick_active_hallucinations(dt)
        if self.hallu_cooldown > 0:
            self.hallu_cooldown -= dt
        p = self.player
        sanity_frac = p.sanity / S.SANITY_MAX
        self.hallu_intensity = max(0.0, (S.HALLUCINATION_SANITY_THRESHOLD - sanity_frac) / S.HALLUCINATION_SANITY_THRESHOLD)
        if sanity_frac >= S.HALLUCINATION_SANITY_THRESHOLD or p.is_hiding or self.monster.state == Monster.HUNT:
            self.hallu_progress = 0.0
            return
        deficit = self.hallu_intensity
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
            if h["kind"] == "eyes":
                if self._tick_hallu_eyes(h, dt):
                    still.append(h)
                continue
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

    def _tick_hallu_eyes(self, h, dt):
        h["spawn_t"] = h.get("spawn_t", 0.0) + dt
        ex, ey, _ = h["pos"]
        if not h.get("despawning"):
            lit = (self._light_level_at(ex, ey) >= S.HALLUCINATION_EYES_LIGHT_THRESHOLD
                   or self._flashlight_lights_point(ex, ey))
            p = self.player
            ang = math.atan2(ey - p.y, ex - p.x)
            rel = (ang - p.angle + math.pi) % (2 * math.pi) - math.pi
            gazing = abs(rel) < S.HALLUCINATION_EYES_GAZE_FOV and self.maze.has_line_of_sight(p.x, p.y, ex, ey)
            h["gaze_t"] = h.get("gaze_t", 0.0) + dt if gazing else 0.0
            if lit or h["gaze_t"] >= S.HALLUCINATION_EYES_GAZE_SECONDS:
                h["despawning"] = True
        if h.get("despawning"):
            h["fade_out_t"] = h.get("fade_out_t", 0.0) + dt / S.HALLUCINATION_EYES_FADE_SECONDS
            if h["fade_out_t"] >= 1.0:
                return False
        return True

    def _trigger_random_hallucination(self):
        roll = random.random()
        if roll < 0.30:
            self._start_hallu_pulse()
        elif roll < 0.55:
            self.sounds.play_hallu_alert()
            self.fx_shake.add(0.10)
        elif roll < 0.75:
            self._start_hallu_door_break()
        else:
            self._start_hallu_eyes()

    def _start_hallu_eyes(self):
        p = self.player
        for _ in range(12):
            dist = random.uniform(3.0, S.HALLUCINATION_EYES_MAX_DIST)
            ang = p.angle + random.uniform(-0.9, 0.9)
            ex, ey = p.x + math.cos(ang) * dist, p.y + math.sin(ang) * dist
            if self.maze.is_wall(ex, ey):
                continue
            if not self.maze.has_line_of_sight(p.x, p.y, ex, ey):
                continue
            if self._light_level_at(ex, ey) >= S.HALLUCINATION_EYES_LIGHT_THRESHOLD:
                continue
            if self._flashlight_lights_point(ex, ey):
                continue
            self.hallu_active.append({
                "kind": "eyes", "pos": (ex, ey, S.HALLUCINATION_EYES_HEIGHT),
                "spawn_t": 0.0, "gaze_t": 0.0, "fade_out_t": 0.0, "despawning": False,
            })
            return

    def _draw_angel_overlay(self, t, reveal):
        surf = self.angel_canvas
        surf.fill((0, 0, 0, 0))
        if reveal <= 0.0:
            return
        cx = cy = S.ANGEL_CANVAS_SIZE / 2.0
        base_r = 140.0 * reveal

        ray_n = 26
        for i in range(ray_n):
            ang = math.tau * i / ray_n + t * 0.10
            flick = 0.55 + 0.45 * math.sin(t * 3.1 + i * 1.7)
            length = base_r * (2.8 + 0.7 * flick)
            x2 = cx + math.cos(ang) * length
            y2 = cy + math.sin(ang) * length
            a = int(90 * reveal * flick)
            if a > 0:
                pygame.draw.line(surf, (255, 240, 205, a), (cx, cy), (x2, y2), 3 if i % 3 == 0 else 1)

        glow = self.angel_glow_surf
        gscale = (0.75 + 0.12 * math.sin(t * 1.3)) * reveal
        gw, gh = max(2, int(glow.get_width() * gscale)), max(2, int(glow.get_height() * gscale))
        scaled = pygame.transform.smoothscale(glow, (gw, gh))
        scaled.set_alpha(int(245 * reveal))
        surf.blit(scaled, (cx - gw // 2, cy - gh // 2))

        def feather_poly(ang, length, width):
            dx, dy = math.cos(ang), math.sin(ang)
            px, py = -dy, dx
            tip = (cx + dx * length, cy + dy * length)
            mid = (cx + dx * length * 0.58, cy + dy * length * 0.58)
            left = (mid[0] + px * width * 0.5, mid[1] + py * width * 0.5)
            right = (mid[0] - px * width * 0.5, mid[1] - py * width * 0.5)
            base_l = (cx + px * width * 0.14, cy + py * width * 0.14)
            base_r2 = (cx - px * width * 0.14, cy - py * width * 0.14)
            return [base_l, left, tip, right, base_r2]

        wing_alpha = int(220 * reveal)
        for base_ang, length, flap_phase, flap_rate, n_feathers, layer_offset in self._angel_wing_specs:
            flap = 0.5 + 0.5 * math.sin(t * flap_rate + flap_phase)
            wing_len = base_r * 1.9 * length
            spread = 0.62 * (0.55 + 0.45 * flap)
            for layer, layer_ang in ((0, 0.0), (1, layer_offset)):
                n = n_feathers if layer == 0 else max(3, n_feathers - 3)
                for k in range(n):
                    f = k / max(1, n - 1)
                    fang = base_ang + layer_ang + (f - 0.5) * spread
                    flen = wing_len * (0.42 + 0.58 * math.sin(f * math.pi) ** 0.7) * (0.9 + 0.1 * layer)
                    fwidth = wing_len * (0.16 - 0.05 * abs(f - 0.5))
                    pts = feather_poly(fang, flen, fwidth)
                    shade = 235 - int(70 * f) - layer * 25
                    pygame.draw.polygon(surf, (shade, shade - 8, shade - 18, wing_alpha), pts)
                    pygame.draw.lines(surf, (60, 48, 46, int(wing_alpha * 0.7)), False, pts[1:4], 1)

        for ang, rad, phase, rate, size, jitter_phase in self._angel_eye_specs:
            blink = 0.5 + 0.5 * math.sin(t * rate + phase)
            if blink < 0.08:
                continue
            jitter = 1.0 + 0.05 * math.sin(t * 1.6 + jitter_phase)
            r = base_r * rad * jitter
            ex = cx + math.cos(ang) * r
            ey = cy + math.sin(ang) * r
            ew, eh = 8 * size, 5 * size
            a = int(235 * reveal * blink)
            pygame.draw.ellipse(surf, (225, 195, 190, a), (ex - ew / 2, ey - eh / 2, ew, eh))
            pygame.draw.ellipse(surf, (150, 20, 20, int(a * 0.55)), (ex - ew / 2, ey - eh / 2, ew, eh), 1)
            pygame.draw.ellipse(surf, (35, 8, 8, a), (ex - ew * 0.22, ey - eh * 0.35, ew * 0.44, eh * 0.7))

        mass_r = base_r * 0.62
        a_full = int(255 * reveal)
        for ang, len_frac, jitter_phase in self._angel_vein_specs:
            wob = 0.85 + 0.15 * math.sin(t * 2.2 + jitter_phase)
            vx = cx + math.cos(ang) * mass_r * 1.15 * len_frac * wob
            vy = cy + math.sin(ang) * mass_r * 1.15 * len_frac * wob
            pygame.draw.line(surf, (165, 15, 15, int(a_full * 0.5)), (cx, cy), (vx, vy), 1)
        for ang, rad, size, phase, rate in self._angel_center_eye_specs:
            ex = cx + math.cos(ang) * rad * mass_r
            ey = cy + math.sin(ang) * rad * mass_r
            blink = 0.5 + 0.5 * math.sin(t * rate + phase)
            eye_r = mass_r * (0.30 + 0.10 * size) * (0.35 + 0.65 * max(0.15, blink))
            pygame.draw.circle(surf, (232, 208, 202, a_full), (int(ex), int(ey)), int(eye_r))
            iris_r = eye_r * 0.46
            pygame.draw.circle(surf, (92, 24, 20, a_full), (int(ex), int(ey)), int(iris_r))
            pupil_r = iris_r * 0.42
            pygame.draw.circle(surf, (8, 4, 4, a_full), (int(ex), int(ey)), int(pupil_r))
            hl_r = max(1, int(pupil_r * 0.32))
            pygame.draw.circle(surf, (255, 250, 240, int(200 * reveal)),
                                (int(ex - pupil_r * 0.3), int(ey - pupil_r * 0.3)), hl_r)

    def _sync_hallu_eyes(self):
        out = []
        for h in self.hallu_active:
            if h["kind"] != "eyes":
                continue
            fade = (min(1.0, h.get("spawn_t", 1.0) / 0.3)
                    * (1.0 - min(1.0, h.get("fade_out_t", 0.0))))
            if fade <= 0.0:
                continue
            out.append({"pos": h["pos"], "fade": fade, "age": h.get("spawn_t", 0.0)})
        self.renderer.hallu_eyes = out

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
        self.screamer = screamer_mod.Screamer()
        self.sounds.set_growl(False)
        self.sounds.stop_hunt(fade_ms=2000)
        self._release_mouse()
        p, m = self.player, self.monster
        self.catch_cam = {
            "sx": p.x, "sy": p.y, "sa": p.angle, "sp": p.pitch,
            "mx": m.x, "my": m.y,
        }
        p.map_open = False

    def _catch_camera_override(self):
        cc = getattr(self, "catch_cam", None)
        if cc is None:
            return None
        sc = getattr(self, "screamer", None) if self.state == "catch" else None
        f = 1.0 if sc is not None else min(1.0, self.catch_timer / S.CATCH_TURN_SECONDS)
        f = f * f * (3.0 - 2.0 * f)
        to_m = math.atan2(cc["my"] - cc["sy"], cc["mx"] - cc["sx"])
        yaw = cc["sa"] + _wrap_angle(to_m - cc["sa"]) * f
        bx, by = -math.cos(to_m), -math.sin(to_m)
        head = getattr(self.renderer, "last_monster_head", None)
        back = S.CATCH_BACK_OFF * f
        tx, ty = (head[0], head[1]) if head is not None else (cc["mx"], cc["my"])

        def blocked(b):
            ex, ey = cc["sx"] + bx * b, cc["sy"] + by * b
            return (self.maze.circle_hits_wall(ex, ey, 0.18)
                    or line_blocked_by_cover(self.doors, ex, ey, tx, ty, min_height=0.3))
        while back > 0.05 and blocked(back):
            back -= 0.1
        back = max(0.0, back)
        eye = (cc["sx"] + bx * back, cc["sy"] + by * back, EYE_HEIGHT)
        rise = S.CATCH_EYE_RISE
        if sc is not None and head is not None:
            rise = math.atan2(head[2] - EYE_HEIGHT, max(0.25, math.hypot(head[0] - eye[0], head[1] - eye[1])))
        pitch = cc["sp"] + (rise - cc["sp"]) * f
        return (eye, yaw, pitch, FOV_DEGREES)

    def _update_catch(self, dt):
        self.catch_timer += dt
        self.screamer.update(dt, self.sounds, self.fx_shake)
        self.fx_shake.add(dt * 2.6)
        if self.catch_timer > self.screamer.duration:
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

    def _start_win_sequence(self):
        self._ensure_forest_run_scene()
        self.state = "win_seq"
        self.catch_timer = 0.0
        self.sounds.set_growl(False)
        self.sounds.play_escape_end()
        self._release_mouse()

    def _update_win_seq(self, dt):
        self.catch_timer += dt
        self._update_forest_run_motion(dt)
        if self.catch_timer > S.WIN_FADE_SECONDS:
            self.state = "win"

    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                if self.console.handle_key(event, self):
                    continue
                if (event.key == pygame.K_BACKQUOTE and self.state == "playing"
                        and self.settings.get("debug_hud_console")):
                    self.console.toggle(self)
                    continue
                self._handle_keydown(event.key)
            elif event.type == pygame.MOUSEBUTTONDOWN and self.awaiting_bind is not None:
                self._resolve_bind(("mouse", event.button))
            elif (event.type == pygame.MOUSEBUTTONDOWN and self.state == "playing"
                  and self.settings["bindings"]["map_mode"] == ("mouse", event.button)):
                self._toggle_map_mode()
            elif (event.type == pygame.MOUSEBUTTONDOWN and self.state == "note"
                  and event.button == 3):
                self._note_settling = True
                self.sounds.play_ui()
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                self._handle_click(self._logical_mouse_pos(event.pos))
            elif event.type == pygame.MOUSEMOTION:
                if self.dragging_slider is not None and self.state == "settings":
                    layout = self._settings_page_layout(self.settings_page)
                    rect = layout["sliders"].get(self.dragging_slider)
                    if rect is not None:
                        self._apply_slider(self.dragging_slider, rect, self._logical_mouse_pos(event.pos)[0])
                elif self.dragging_slider == "gamma" and self.state == "gamma_calibration":
                    self._apply_slider("gamma", self._gamma_layout()["slider"],
                                       self._logical_mouse_pos(event.pos)[0])
            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                self.dragging_slider = None
                self._flush_pending_settings_save()
            elif event.type == pygame.MOUSEWHEEL and self.state == "menu" and self._show_changelog:
                self._changelog_scroll_px = max(
                    0, min(self._changelog_max_scroll, self._changelog_scroll_px - event.y * 40))
            elif event.type == pygame.MOUSEWHEEL and self.state == "playing" and self.player.map_open:
                self._flip_map_page(-event.y)
            elif (event.type == pygame.MOUSEWHEEL and self.state == "settings"
                    and self.settings_page == "controls"):
                self._controls_scroll_px = max(
                    0, min(self._controls_max_scroll, self._controls_scroll_px - event.y * 46))
            elif event.type == pygame.MOUSEWHEEL and self.state == "credits":
                self._community_scroll_px = max(
                    0, min(self._community_max_scroll, self._community_scroll_px - event.y * 40))
            elif event.type == pygame.WINDOWFOCUSLOST and self.state == "playing":
                self.state = "paused"
                self._release_mouse()

    def _handle_click(self, pos):
        if self.state == "splash":
            self._finish_splash()
        elif self.state == "menu" and self._show_changelog:
            if self._changelog_close_rect.collidepoint(pos):
                self._close_changelog()
        elif self.state == "menu":
            self._handle_button_click(self._menu_buttons(), pos)
        elif self.state == "paused":
            self._handle_button_click(self._pause_buttons(), pos)
        elif self.state == "settings":
            self._handle_settings_click(pos)
        elif self.state == "gamma_calibration":
            layout = self._gamma_layout()
            if layout["slider"].inflate(0, 24).collidepoint(pos):
                self._apply_slider("gamma", layout["slider"], pos[0])
                self.dragging_slider = "gamma"
            else:
                self._handle_button_click(layout["buttons"], pos)
        elif self.state == "credits":
            self._handle_button_click(self._credits_buttons(), pos)
        elif self.state == "warning":
            self._handle_button_click(self._warning_buttons(), pos)
        elif self.state == "confirm_quit":
            self._handle_button_click(self._confirm_quit_buttons(), pos)
        elif self.state == "update_prompt":
            self._handle_button_click(self._update_prompt_buttons(), pos)
        elif self.state in ("dead_caught", "dead_sanity", "win", "angel_end"):
            self._handle_button_click(self._end_buttons(self.END_BUTTON_Y), pos)

    def _handle_keydown(self, key):
        if self.awaiting_bind is not None:
            self._resolve_bind(("key", key))
            return
        if self.state == "splash":
            self._finish_splash()
        elif self.state == "menu" and self._show_changelog:
            if key == pygame.K_ESCAPE:
                self._close_changelog()
        elif self.state == "menu":
            if key == pygame.K_F9:
                self._start_debug_level()
            elif key == pygame.K_F10:
                self._start_transition_test_level()
            elif key == pygame.K_F11:
                self._start_spectator_level()
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
            elif self._binding_matches_keydown("flashlight", key):
                if self.is_peeking or self.hide_transition is not None:
                    self.sounds.play_denied()
                elif not self.player.toggle_flashlight():
                    self.sounds.play_denied()
                else:
                    self.sounds.play_ui()
            elif self._binding_matches_keydown("interact", key):
                self.interact()
            elif self._binding_matches_keydown("alt_interact", key):
                self.interact_alt()
            elif self._binding_matches_keydown("map", key):
                if self.is_peeking or self.hide_transition is not None or not self.player.toggle_map():
                    self.sounds.play_denied()
                else:
                    self.sounds.play_ui()
                    if self.player.map_open and not self._map_help_shown:
                        self._map_help_shown = True
                        self.hint_text = i18n.Text(
                            "hint.map_keys", mode=self._binding_label("map_mode"),
                            pencil=self._binding_label("map_pencil"))
                        self.hint_timer = 6.0
            elif self._binding_matches_keydown("map_mode", key):
                self._toggle_map_mode()
        elif self.state == "note":
            if key == pygame.K_ESCAPE or self._binding_matches_keydown("interact", key):
                self._close_note()
                self.sounds.play_ui()
        elif self.state == "paused":
            if key == pygame.K_ESCAPE:
                self._begin_playing()
        elif self.state == "settings":
            if key == pygame.K_ESCAPE:
                self._close_settings()
        elif self.state == "gamma_calibration":
            if key == pygame.K_ESCAPE:
                self._close_gamma_calibration()

    def _hud_live_now(self):
        if self.state != "playing" or S.HUD_REFRESH_HZ <= 0.0:
            return True
        now = time.monotonic()
        if now - self._hud_drawn_at < 1.0 / S.HUD_REFRESH_HZ:
            return False
        self._hud_drawn_at = now
        return True

    def draw(self):
        self._hud_live = self._hud_live_now()
        if self._hud_live:
            self.hud_surf.fill((0, 0, 0, 0))

        if self.state == "note":
            self._draw_note_scene()
            return

        if self.state in ("menu", "playing", "paused", "settings", "elevator_ride", "hatch_climb",
                           "fence_escape", "angel_seq", "catch", "catch_sanity", "win_seq", "win",
                           "dead_caught", "dead_sanity", "credits", "warning", "confirm_quit",
                           "update_prompt", "update_downloading"):
            if self.state in ("fence_escape", "win_seq", "win"):
                shake_yaw = math.sin(self.anim_t * 11.3) * 0.018 + math.sin(self.anim_t * 23.7) * 0.008
                shake_pitch = math.cos(self.anim_t * 13.1) * 0.014 + math.sin(self.anim_t * 7.4) * 0.006
            else:
                shake_yaw, shake_pitch = self.fx_shake.offset(max_px=0.045)
            hide_locker, hide_swing = None, 0.0
            if self.hide_transition is not None:
                hide_locker = self.hide_transition["locker"]
                hide_frac = min(1.0, self.hide_transition["t"] / self.hide_transition["duration"])
                hide_swing = math.sin(hide_frac * math.pi)
            demo_door_swings = {}
            for dm in self.debug_demo_monsters:
                if dm.locker_target is not None and dm.checking_timer > 0.0:
                    demo_door_swings[id(dm.locker_target)] = self.renderer.compute_check_frac(dm)
                if dm.closing_locker is not None and dm.closing_timer > 0.0:
                    close_frac = dm.closing_timer / S.MONSTER_LOCKER_CLOSE_SECONDS
                    key = id(dm.closing_locker)
                    demo_door_swings[key] = max(demo_door_swings.get(key, 0.0), close_frac)
            self._sync_wall_holes()
            ambient_level = self.spec.get("ambient_level", 0.06)
            moon_strength = self._moon_strength()
            fe_light = self.fence_escape
            if fe_light is not None:
                b = min(1.0, fe_light["t"] / (S.FENCE_ESCAPE_SECONDS * 0.8))
                b = b * b * (3.0 - 2.0 * b)
                a0, m0, f0, c0 = fe_light["light0"]
                ambient_level = a0 + (ambient_level - a0) * b
                moon_strength = m0 + (moon_strength - m0) * b
            if self.hatch_climb is not None:
                moon_strength = max(moon_strength, self.hatch_climb["moon"])
            fog_color = self.spec.get("fog_color", S.COL_FOG)
            fog_dist = self.spec.get("fog_dist", 12.5)
            if fe_light is not None:
                fog_dist = f0 + (fog_dist - f0) * b
                fog_color = tuple(c0[i] + (fog_color[i] - c0[i]) * b for i in range(3))
            world_flood = 0.0
            angel_billboard = None
            burn = None
            self._god_ray = None
            if self.angel_seq is not None:
                flood_t = self.angel_seq["flood_t"]
                ambient_level += (S.ANGEL_FLOOD_AMBIENT - ambient_level) * flood_t
                moon_strength += (S.ANGEL_FLOOD_MOON - moon_strength) * flood_t
                fog_color = tuple(
                    fog_color[i] + (S.ANGEL_FLOOD_FOG_COLOR[i] - fog_color[i]) * flood_t for i in range(3))
                fog_dist += (S.ANGEL_FLOOD_FOG_DIST - fog_dist) * flood_t
                angel_reveal = flood_t
                world_flood = flood_t * 0.22
                if self.angel_seq["burn"] > 0.0:
                    burn = (self.angel_seq["pos"], self.angel_seq["burn"], S.ANGEL_BURN_WIDTH)
                if self.angel_seq["phase"] == "whiteout":
                    wo_frac = min(1.0, self.angel_seq["t"] / S.ANGEL_WHITEOUT_SECONDS)
                    world_flood = 0.22 + 0.78 * wo_frac
                    angel_reveal = 1.0
                self._draw_angel_overlay(self.anim_t, angel_reveal)
                angel_billboard = (
                    pygame.image.tostring(self.angel_canvas, "RGBA"), S.ANGEL_CANVAS_SIZE,
                    self.angel_seq["pos"], S.ANGEL_WORLD_SIZE,
                )
            self._upload_map_sheet()
            self._upload_note_sheet()
            self._sync_hallu_eyes()
            self.renderer.render(
                self.maze, self.player, self.monster, self._scene_props(), self.dread, self.anim_t,
                shake_yaw, shake_pitch,
                fog_color=fog_color, fog_dist=fog_dist,
                ambient=ambient_level,
                moon_strength=moon_strength,
                world_flood=world_flood, burn=burn,
                haze=(self.spec.get("ground_haze", 0.0), self.spec.get("haze_height", 1.0),
                      self.spec.get("haze_color", S.HAZE_COLOR)),
                view_distance_mult=self.settings.get("view_distance", 1.0),
                hide_locker=hide_locker, hide_swing=hide_swing,
                camera_override=self._active_camera_override(),
                extra_door_swings=demo_door_swings,
                hallu_intensity=self.hallu_intensity,
                hide_monster=self._debug_transition_test or self.state in ("fence_escape", "win_seq", "win"),
                angel_billboard=angel_billboard,
                trip_intensity=self.trip_intensity,
                debug_vision_cone=(self.state in ("playing", "paused")
                                    and (self.settings.get("debug_hud_monster")
                                          or self._debug_spectator)),
                look_yaw=self._trip_cam_yaw, look_pitch=self._trip_cam_pitch,
            )
            if self.angel_seq is not None and self.angel_seq["ray"] > 0.0:
                cam = self.renderer.last_camera
                at = self.renderer.project_to_screen(self.angel_seq["pos"], cam[0], cam[1], cam[2],
                                                     cam[3], cam[4])
                if at is not None:
                    self._god_ray = (at[0] / S.SCREEN_W, 1.0 - at[1] / S.SCREEN_H,
                                     self.angel_seq["ray"])
            if self.exit_prop is not None and self.exit_prop.kind == "elevator":
                self.renderer._draw_elevator_call_lights(
                    (self.exit_prop.x, self.exit_prop.y), self.exit_prop.facing,
                    self._elevator_active_light_index(), S.ELEVATOR_ARRIVE_LIGHT_COUNT, self.anim_t)
            for arrived in self.props:
                if arrived.kind == "elevator_arrival":
                    eye = self.renderer.last_camera[0] if self.renderer.last_camera else None
                    self.renderer._draw_elevator_call_lights(
                        (arrived.x, arrived.y), arrived.facing, S.ELEVATOR_ARRIVE_LIGHT_COUNT - 1,
                        S.ELEVATOR_ARRIVE_LIGHT_COUNT, self.anim_t)
                    if self.renderer.eye_inside_cabin(arrived, eye):
                        self.renderer._draw_elevator_call_lights(
                            (arrived.x, arrived.y), arrived.facing, S.ELEVATOR_ARRIVE_LIGHT_COUNT - 1,
                            S.ELEVATOR_ARRIVE_LIGHT_COUNT, self.anim_t, inside=True)
            ride = self.elevator_ride
            if ride is not None and ride.get("cabin") is not None and ride["phase"] in ("closing", "ride", "opening"):
                frac = min(1.0, ride["t"] / S.ELEVATOR_DESCEND_HOLD_SECONDS) if ride["phase"] == "ride" else (
                    0.0 if ride["phase"] == "closing" else 1.0)
                n = S.ELEVATOR_ARRIVE_LIGHT_COUNT
                self.renderer._draw_elevator_call_lights(
                    (ride["cabin"].x, ride["cabin"].y), ride["cabin"].facing,
                    min(n - 1, int(frac * n)), n, self.anim_t, inside=True)
            for dm in self.debug_demo_monsters:
                self.renderer._draw_monster(dm, 0.0, check_frac=self.renderer.compute_check_frac(dm))
            if self.debug_door_monster is not None:
                self.renderer._draw_monster(self.debug_door_monster, 0.0)
            if self._hud_live and (self.player.is_hiding or self.hide_vignette_t > 0.0):
                self._draw_hide_frame()

        if self.state == "splash":
            self._draw_splash()
        elif self.state == "menu":
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
            if self._hud_live:
                self._draw_hud()
                self._draw_peek_mask()
        elif self.state == "paused":
            self._draw_hud()
            self._draw_peek_mask()
            self._draw_pause()
        elif self.state == "elevator_ride":
            if self._status_held():
                self._draw_hud()
        elif self.state == "hatch_climb":
            if self._status_held():
                self._draw_hud()
        elif self.state == "fence_escape":
            fe = self.fence_escape
            pass
        elif self.state == "angel_seq":
            a = self.angel_seq
            if a["phase"] == "whiteout":
                frac = min(1.0, a["t"] / S.ANGEL_WHITEOUT_SECONDS)
                fx.draw_flash(self.hud_surf, (255, 252, 240), 255 * frac)
        elif self.state == "angel_end":
            frac = min(1.0, self.catch_timer / S.ANGEL_END_FADE_SECONDS)
            self._draw_angel_end(alpha_mult=frac)
        elif self.state == "catch":
            sc = self.screamer
            if sc.wants_snapshot():
                w, h = self.renderer.fbo.size
                frame = np.frombuffer(self.renderer.fbo.read(components=3), dtype=np.uint8)
                sc.snapshot = frame.reshape(h, w, 3)[::-1].copy()
            head = getattr(self.renderer, "last_monster_head", None)
            cam = self.renderer.last_camera
            head_at = (self.renderer.project_to_screen(head, cam[0], cam[1], cam[2], cam[3], cam[4])
                       if head is not None and cam is not None else None)
            sc.draw(self.hud_surf, head_at)
        elif self.state == "catch_sanity":
            t = self.catch_timer / 2.6
            white = max(0, 255 * (1 - abs(t - 0.3) / 0.3)) if t < 0.6 else 0
            fx.draw_flash(self.hud_surf, (255, 255, 255), white)
            fx.draw_flash(self.hud_surf, (0, 0, 0), 190 * max(0.0, (t - 0.55) / 0.45))
        elif self.state == "win_seq":
            frac = min(1.0, self.catch_timer / S.WIN_FADE_SECONDS)
            self._draw_win(alpha_mult=frac)
        elif self.state == "dead_caught":
            self._draw_gameover(i18n.t("gameover.caught_title"), (150, 20, 20), i18n.t("gameover.caught_subtitle"))
        elif self.state == "dead_sanity":
            self._draw_gameover(i18n.t("gameover.sanity_title"), (200, 200, 210), i18n.t("gameover.sanity_subtitle"))
        elif self.state == "win":
            self._draw_win()
        elif self.state == "settings":
            self._draw_settings()
        elif self.state == "gamma_calibration":
            self._draw_gamma_calibration()
        elif self.state == "confirm_quit":
            if self.confirm_return == "menu":
                self._draw_menu()
            else:
                self._draw_hud()
            self._draw_confirm_quit()

        if self._hud_live:
            if self.state not in ("catch", "catch_sanity", "win_seq", "elevator_ride", "hatch_climb",
                                   "fence_escape", "angel_seq"):
                self._draw_wip_label()
            self._draw_debug_overlay()
            self._draw_console()

        self._sync_window_size()
        self.renderer.composite(None, (S.SCREEN_W, S.SCREEN_H), self.window_size,
                                 trip_intensity=self.trip_intensity, t=self.anim_t,
                                 comedown_intensity=self.comedown_intensity,
                                 hud_surface=self.hud_surf if self._hud_live else None,
                                 hud_reuse=not self._hud_live,
                                 god_ray=getattr(self, "_god_ray", None))
        pygame.display.flip()

    def _draw_wip_label(self):
        text = i18n.t("hud.wip")
        w = self.font_sm.size(text)[0]
        self._text(self.font_sm, text, S.COL_UI_DIM,
                   topleft=(S.SCREEN_W - w - 14, S.SCREEN_H - 26), shadow=False)

    def _draw_console(self):
        con = self.console
        if not con.open:
            return
        pad = 12
        rect = pygame.Rect(pad, pad, S.SCREEN_W - pad * 2, int(S.SCREEN_H * 0.56))
        self._draw_panel(rect, fill=(6, 6, 8, 232), border=(120, 108, 96),
                         accent=(150, 55, 50), corner=18)
        font = self.font_sm
        lh = font.get_height() + 2
        input_y = rect.bottom - pad - lh
        pygame.draw.line(self.hud_surf, (70, 64, 58),
                         (rect.x + pad, input_y - 6), (rect.right - pad, input_y - 6), 1)
        room = max(1, (input_y - 10 - (rect.y + pad)) // lh)
        end = len(con.lines) - con.scroll
        shown = con.lines[max(0, end - room):max(0, end)]
        colours = {"in": (205, 196, 178), "out": (150, 146, 136), "err": (208, 96, 84)}
        y = rect.y + pad
        for text, kind in shown:
            self._text(font, text, colours.get(kind, colours["out"]),
                       topleft=(rect.x + pad, y), shadow=False)
            y += lh
        caret = "_" if (con.caret_t % 1.0) < 0.55 else " "
        self._text(font, debug_console.PROMPT + con.line + caret, (225, 216, 198),
                   topleft=(rect.x + pad, input_y), shadow=False)
        if con.scroll:
            tag = "^ %d" % con.scroll
            self._text(font, tag, (150, 146, 136),
                       topleft=(rect.right - pad - font.size(tag)[0], rect.y + pad), shadow=False)

    def _draw_debug_overlay(self):
        now = time.monotonic()
        if self._debug_lines is not None and now - self._debug_lines_at < 1.0 / S.DEBUG_HUD_REFRESH_HZ:
            self._blit_debug_lines(self._debug_lines)
            return
        lines = []
        if self.settings.get("debug_hud_fps"):
            lines.append(f"FPS: {self.clock.get_fps():.0f}")
            perf = getattr(self.renderer, "last_perf", None)
            if perf:
                lines.append("  ".join(f"{k}:{v:.1f}ms" for k, v in perf.items()))
        gameplay = self.state in ("playing", "paused")
        if gameplay and self.settings.get("debug_hud_coords"):
            p = self.player
            lines.append(i18n.t("debug_hud.line_coords", x=f"{p.x:.2f}", y=f"{p.y:.2f}", cell=p.cell,
                                ang=f"{math.degrees(p.angle) % 360:.0f}"))
        if gameplay and (self.settings.get("debug_hud_monster") or self._debug_spectator):
            m, p = self.monster, self.player
            dist = math.hypot(m.x - p.x, m.y - p.y)
            lines.append(i18n.t("debug_hud.line_monster", state=i18n.t(f"debug_hud.state_{m.state}"),
                                dist=f"{dist:.1f}", alert=f"{m.alert_level:.2f}"))
            sight = monster_vision_base(p.light_level, m.vision_light_norm) * m.vision_mult
            lines.append(i18n.t("debug_hud.line_light", light=f"{p.light_level:.2f}",
                                lit=f"{monster_lit_frac(p.light_level, m.vision_light_norm):.2f}",
                                sight=f"{sight:.1f}", sanity=i18n.t("debug_hud.yes" if p.is_lit else "debug_hud.no")))
        if gameplay and self.settings.get("debug_hud_seed"):
            stage = getattr(self, "anomaly_stage", S.ANOMALY_DEFAULT_STAGE)
            lines.append(i18n.t("debug_hud.line_seed", seed=self.floor_seed,
                                roll=getattr(self, "anomaly_roll", "-"), stage=stage,
                                name=S.anomaly_of(stage)["key"],
                                load="%.0f" % getattr(self, "floor_load_ms", 0.0)))
        if gameplay and self.settings.get("debug_hud_scares"):
            lines.append(i18n.t("debug_hud.line_dread", dread=f"{self.dread * 100:.0f}",
                                time=f"{self._dread_time_frac * 100:.0f}",
                                progress=f"{self._dread_progress_frac * 100:.0f}"))
            fill_pct = self.scare_progress / self.scare_target * 100.0 if self.scare_target else 0.0
            rate = 100.0 / max(1.0, self._scare_seconds_to_fill)
            lines.append(i18n.t("debug_hud.line_scare", fill=f"{fill_pct:.0f}", rate=f"{rate:.1f}",
                                tag=i18n.t("debug_hud.tag_lit" if self._scare_lit else "debug_hud.tag_dark")))
        self._debug_lines = lines
        self._debug_lines_at = now
        self._blit_debug_lines(lines)

    def _blit_debug_lines(self, lines):
        if not lines:
            return
        font = self.font_sm
        pad = 6
        w = max(font.size(line)[0] for line in lines) + pad * 2
        h = len(lines) * 18 + pad * 2
        back = self._debug_back
        if back is None or back.get_size() != (w, h):
            back = self._debug_back = pygame.Surface((w, h), pygame.SRCALPHA)
            back.fill((0, 0, 0, 150))
        self.hud_surf.blit(back, (8, 8))
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

    _TEXT_CACHE_LIMIT = 1024

    def _rendered_text(self, font, text, color):
        key = (font, text, tuple(color))
        cache = self._text_cache
        surf = cache.get(key)
        if surf is None:
            if len(cache) >= self._TEXT_CACHE_LIMIT:
                cache.clear()
            surf = cache[key] = font.render(text, True, color)
        return surf

    def _text(self, font, text, color, center=None, topleft=None, shadow=True):
        surf = self._rendered_text(font, text, color)
        if shadow:
            sh = self._rendered_text(font, text, (0, 0, 0))
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

    def _draw_splash(self):
        self.hud_surf.fill((0, 0, 0, 255))
        cx, cy = S.SCREEN_W // 2, S.SCREEN_H // 2
        page_a = 1.0 - self._smooth01((self._splash_t - SPLASH_HOLD_END) / SPLASH_FADE_OUT_DUR)
        if page_a <= 0.003:
            return
        self._blit_static()

        flicker = 0.88 + 0.12 * math.sin(self.anim_t * 1.3)
        if random.random() < 0.0015:
            flicker *= 0.55

        title_a = self._splash_group_alpha(*SPLASH_TITLE_FADE)
        if title_a > 0.003:
            drift = (1.0 - self._smooth01((self._splash_t - SPLASH_TITLE_FADE[0]) / SPLASH_TITLE_FADE[1])) * 14
            frame = pygame.Rect(0, 0, 620, 252)
            frame.center = (cx, cy - 52 + int(drift * 0.5))
            card = pygame.Surface(frame.size, pygame.SRCALPHA)
            self._draw_panel(pygame.Rect(0, 0, frame.w, frame.h), fill=(8, 6, 6, 120),
                             corner=26, target=card)
            card.set_alpha(int(210 * title_a))
            self.hud_surf.blit(card, frame.topleft)

            ty = frame.top + 74 + drift
            for spread, a in ((4, 26), (2, 55)):
                glow = self.font_title.render(S.TITLE, True, (200, 30, 30))
                glow.set_alpha(int(a * flicker * title_a))
                r = glow.get_rect(center=(cx, ty))
                for ox, oy in ((-spread, 0), (spread, 0), (0, -spread), (0, spread)):
                    self.hud_surf.blit(glow, (r.x + ox, r.y + oy))
            surf = self.font_title.render(
                S.TITLE, True, (int(215 * flicker), int(40 * flicker), int(38 * flicker)))
            surf.set_alpha(int(255 * title_a))
            self.hud_surf.blit(surf, surf.get_rect(center=(cx, ty)))

            ry = frame.top + 132 + drift
            rule = pygame.Surface((300, 1), pygame.SRCALPHA)
            rule.fill((70, 62, 58, 255))
            rule.set_alpha(int(255 * title_a))
            self.hud_surf.blit(rule, rule.get_rect(center=(cx, ry)))

            surf = self.font_sm.render("WARD No. 9", True, S.COL_UI_DIM)
            surf.set_alpha(int(200 * title_a))
            self.hud_surf.blit(surf, surf.get_rect(center=(cx, ry + 24)))

        sub_a = self._splash_group_alpha(*SPLASH_SUBTITLE_FADE)
        if sub_a > 0.003:
            drift = (1.0 - self._smooth01((self._splash_t - SPLASH_SUBTITLE_FADE[0]) / SPLASH_SUBTITLE_FADE[1])) * 10
            surf = self.font_md.render("by Lonewolf239", True, S.COL_TEXT)
            surf.set_alpha(int(190 * sub_a))
            self.hud_surf.blit(surf, surf.get_rect(center=(cx, cy + 22 + drift)))

        hp_a = self._splash_group_alpha(*SPLASH_HEADPHONES_FADE)
        if hp_a > 0.003:
            self._draw_headphones_hint(cx, cy + 150, hp_a)

        skip_a = self._splash_group_alpha(*SPLASH_SKIP_HINT_FADE)
        if skip_a > 0.003:
            surf = self.font_sm.render(i18n.t("splash.skip_hint"), True, S.COL_UI_DIM)
            surf.set_alpha(int(130 * skip_a))
            self.hud_surf.blit(surf, surf.get_rect(center=(cx, S.SCREEN_H - 50)))

    def _draw_headphones_hint(self, cx, y, alpha):
        gold = (215, 180, 120)
        text_surf = self.font_sm.render(i18n.t("splash.headphones_hint"), True, gold)
        icon_size = 28
        gap = 10
        group_w = icon_size + gap + text_surf.get_width()
        group_h = max(icon_size, text_surf.get_height())
        group = pygame.Surface((group_w, group_h), pygame.SRCALPHA)

        icon_cx, icon_cy = icon_size // 2, group_h // 2
        r = icon_size // 2 - 2
        arc_rect = pygame.Rect(icon_cx - r, icon_cy - r - 2, r * 2, r * 2)
        pygame.draw.arc(group, gold, arc_rect, 0.0, math.pi, width=3)
        cup_w, cup_h = 7, 13
        pygame.draw.rect(group, gold, (icon_cx - r - cup_w // 2, icon_cy - 2, cup_w, cup_h), border_radius=3)
        pygame.draw.rect(group, gold, (icon_cx + r - cup_w // 2, icon_cy - 2, cup_w, cup_h), border_radius=3)

        group.blit(text_surf, (icon_size + gap, (group_h - text_surf.get_height()) // 2))
        group.set_alpha(int(255 * alpha))
        self.hud_surf.blit(group, group.get_rect(center=(cx, y)))

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
        rows, left, content_w, link_buttons, contributor_links, back_btn, community_viewport = self._credits_content()
        self._text(self.font_lg, i18n.t("about.title"), S.COL_TEXT, topleft=(left, 70))
        for y, kind, payload in rows:
            if kind == "text":
                text, font, col = payload
                self._text(font, text, col, topleft=(left, y))
            elif kind == "divider":
                pygame.draw.line(self.hud_surf, (70, 62, 58), (left, y), (left + content_w, y), 1)
        if community_viewport is not None:
            self._draw_community_creators(community_viewport)
        for btn in contributor_links:
            self._draw_text_link(btn)
        for btn in link_buttons:
            self._draw_button(btn)
        self._draw_button(back_btn)

    def _draw_community_creators(self, viewport_spec):
        rect = viewport_spec["rect"]
        lines = viewport_spec["lines"]
        line_h = viewport_spec["line_h"]
        content_h = len(lines) * line_h
        self._community_max_scroll = max(0, content_h - rect.h)
        self._community_scroll_px = max(0, min(self._community_max_scroll, self._community_scroll_px))

        prev_clip = self.hud_surf.get_clip()
        self.hud_surf.set_clip(rect)
        y = rect.top - self._community_scroll_px
        for line in lines:
            if y + line_h >= rect.top and y <= rect.bottom:
                self._text(self.font_sm, line, S.COL_UI_DIM, topleft=(rect.left, y), shadow=True)
            y += line_h
        self.hud_surf.set_clip(prev_clip)

        if self._community_max_scroll > 0:
            track = pygame.Rect(rect.right + 14, rect.top, 6, rect.h)
            pygame.draw.rect(self.hud_surf, (28, 26, 24), track, border_radius=3)
            pygame.draw.rect(self.hud_surf, (70, 64, 58), track, width=1, border_radius=3)
            thumb_h = max(20, int(rect.h * rect.h / content_h))
            thumb_y = rect.top + int(
                (rect.h - thumb_h) * (self._community_scroll_px / self._community_max_scroll))
            thumb = pygame.Rect(track.x, thumb_y, track.w, thumb_h)
            pygame.draw.rect(self.hud_surf, (110, 96, 62), thumb, border_radius=3)
            pygame.draw.rect(self.hud_surf, (150, 130, 90), thumb, width=1, border_radius=3)

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
        elif self.mic.busy:
            hint = None
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
            "mouse_sensitivity": (i18n.t("settings.mouse_sens"), f"{self.settings['mouse_sensitivity']:.1f}x"),
            "view_distance": (i18n.t("settings.view_distance"), f"{int(self.settings['view_distance'] * 100)}%"),
            "mic_sensitivity": (i18n.t("settings.mic_sensitivity"), f"{self.settings['mic_sensitivity']:.1f}"),
        }
        def draw_plain_sliders():
            for key, rect in layout["sliders"].items():
                if key in STEPPED_SLIDERS:
                    continue
                label, value_text = slider_specs[key]
                self._draw_slider(rect, slider_frac(key), label, value_text,
                                  dragging=(self.dragging_slider == key))

        if page != "controls":
            draw_plain_sliders()

        if page == "graphics":
            fs_btn, aa_btn, vs_btn, gamma_btn = layout["buttons"]
            self._draw_toggle(fs_btn, self.settings["fullscreen"])
            self._draw_toggle(aa_btn, self.settings["upscale_smoothing"])
            self._draw_toggle(vs_btn, self.settings["vsync"])
            self._draw_value_row(gamma_btn, f"{self.settings['gamma']:.2f}  >")
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
            for key in GFX_SLIDER_KEYS:
                options = S.GFX_SETTING_OPTIONS[key]
                current = self.settings.get(key, options[0])
                idx = options.index(current) if current in options else 0
                value_text = i18n.t(f"settings.{key}_{current}")
                self._draw_stepped_slider(layout["sliders"][key], idx, len(options),
                                          i18n.t(f"settings.{key}_label"), value_text,
                                          dragging=(self.dragging_slider == key))
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
            viewport = layout["viewport"]
            hud = self.hud_surf
            self.hud_surf = self._scroll_layer(viewport)
            draw_plain_sliders()
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
                key_label = "..." if waiting else self._binding_label(action)
                key_w = self.font_sm.size(key_label)[0]
                self._text(self.font_sm, key_label, (225, 200, 120) if waiting else (200, 190, 180),
                          topleft=(rect.right - 12 - key_w, rect.y + rect.h // 2 - self.font_sm.get_height() // 2),
                          shadow=False)
            for btn in layout["buttons"]:
                self._draw_button(btn)
            if self.controls_msg_timer > 0 and self.controls_msg:
                self._text(self.font_sm, str(self.controls_msg), (225, 150, 90), center=(cx, layout["msg_y"]))
            elif self.awaiting_bind:
                self._text(self.font_sm, i18n.t("controls.press_key"), S.COL_UI_DIM, center=(cx, layout["msg_y"]))
            layer, self.hud_surf = self.hud_surf, hud
            self._blit_scroll_layer(layer, viewport, self._controls_scroll_px,
                                    self._controls_max_scroll)
            self._draw_scrollbar(viewport, self._controls_scroll_px,
                                 self._controls_max_scroll)
        elif page == "interface":
            hud_rect = layout["combos"]["hud_style"]["rect"]
            self._text(self.font_sm, i18n.t("hud.style"), S.COL_UI_DIM,
                      topleft=(hud_rect.x, hud_rect.y - 22), shadow=False)
            style = self.settings.get("hud_style", S.HUD_STYLES[0])
            self._text(self.font_sm,
                      i18n.t("hud.style_about_" + style, bind=self._binding_label("status")),
                      S.COL_UI_DIM, topleft=(hud_rect.x, layout["blurb_y"]), shadow=False)
            self._draw_toggle(layout["buttons"][0], self.settings.get("compass", True))
            lang_rect = layout["combos"]["language"]["rect"]
            self._text(self.font_sm, i18n.t("settings.language_label"), S.COL_UI_DIM,
                      topleft=(lang_rect.x, lang_rect.y - 22), shadow=False)
            self._draw_combo("hud_style", layout["combos"]["hud_style"])
            self._draw_combo("language", layout["combos"]["language"])
        elif page == "debug":
            for opt, btn in zip(DEBUG_HUD_OPTIONS, layout["buttons"]):
                self._draw_toggle(btn, self.settings[f"debug_hud_{opt}"])
            if self.settings_return != "menu":
                hint_y = layout["buttons"][-1]["rect"].bottom + 16
                for line in self._wrap_text(i18n.t("settings.debug_menu_only"), self.font_sm, content.w):
                    self._text(self.font_sm, line, S.COL_UI_DIM, topleft=(content.x, hint_y), shadow=False)
                    hint_y += 20

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

    def _condition_state(self):
        frac = self.player.sanity / S.SANITY_MAX
        if frac >= S.CONDITION_OK:
            return "ok", i18n.t("hud.cond_ok"), (150, 190, 150)
        if frac >= S.CONDITION_BAD:
            return "bad", i18n.t("hud.cond_bad"), (210, 185, 110)
        return "worst", i18n.t("hud.cond_worst"), (205, 90, 80)

    def _sheet_damage(self):
        frac = self.player.sanity / S.SANITY_MAX
        d = max(0.0, min(1.0, (S.CONDITION_CLEAN - frac) / max(1e-6, S.CONDITION_CLEAN)))
        return d, int(d * 24 + 0.5)

    def _sheet_marks(self, n):
        marks = self.__dict__.get("_sheet_mark_list")
        if marks is None:
            rng = random.Random(getattr(self, "_sheet_seed", 0) ^ 0x5A17)
            marks = [(rng.uniform(0.08, 0.92), rng.uniform(0.17, 0.94),
                      rng.uniform(12.0, 34.0), rng.randrange(1 << 30))
                     for _ in range(S.CONDITION_MARKS_MAX)]
            self._sheet_mark_list = marks
        return marks[:max(0, n)]

    def _pocket_lines(self):
        p = self.player
        out = []
        label = self._spec_t("collectible_label").capitalize()
        out.append(i18n.t("hud.carried", label=label, n=p.carried))
        out.append(i18n.t("item.battery", n=int(p.battery)))
        if p.has_lighter:
            out.append(i18n.t("item.lighter"))
        if p.has_cutters:
            out.append(i18n.t("item.cutters_broken" if p.cutters_broken else "item.cutters"))
        if p.has_map:
            out.append(i18n.t("item.map"))
            out.append(i18n.t("item.pencils", n=f"{p.pencil_ink / S.PENCIL_SHEETS:.1f}"))
            out.append(i18n.t("item.sheets", n=len(self.map_sheets)))
        return out

    def _task_lines(self):
        p = self.player
        out = []
        if self.panel_prop is not None:
            powered_word = self._spec_t("panel_powered_text", "hud.powered")
            status = powered_word if self.panel_prop.powered else i18n.t(
                "hud.installed_count", n=self.panel_prop.installed, total=self.spec["n_collectible"])
            out.append(i18n.t("hud.panel_status",
                              label=self._spec_t("panel_label").capitalize(), status=status))
        exit_req = self.spec.get("exit_requires_item")
        if exit_req:
            have = self._player_has_exit_item(exit_req)
            label = self._spec_t("exit_requires_item_label") or exit_req.capitalize()
            out.append(i18n.t("hud.exit_req", label=label,
                              status=i18n.t("hud.have" if have else "hud.dont_have")))
        return out

    def _sheet_caption(self, surf, text, y, max_w, col, max_lines=2):
        for line in self._wrap_text(text, self.font_note, max_w)[:max_lines]:
            self._text_on(surf, self.font_note, line, col, (18, y))
            y += self.font_note.get_height() - 6
        return y

    def _stamina_showing(self):
        p = self.player
        if p.stamina_locked or p.is_sprinting or p.stamina < S.STAMINA_MAX - 0.5:
            self._stamina_seen_t = S.HUD_STAMINA_LINGER
        return self._stamina_seen_t > 0.0

    def _draw_hud(self):
        style = self.settings.get("hud_style", S.HUD_STYLES[0])
        if style == "clipboard":
            self._draw_hud_clipboard()
        elif style == "chart":
            self._draw_hud_chart()
        else:
            self._draw_hud_minimal()
        self._draw_hud_common()

    @staticmethod
    def _wobble(rng, amount):
        return rng.uniform(-amount, amount), rng.uniform(-amount, amount)

    def _sketch_line(self, surf, p0, p1, col, rng, jitter, width=2, passes=2):
        for _ in range(passes):
            ax, ay = p0[0] + rng.uniform(-jitter, jitter), p0[1] + rng.uniform(-jitter, jitter)
            bx, by = p1[0] + rng.uniform(-jitter, jitter), p1[1] + rng.uniform(-jitter, jitter)
            mx, my = (ax + bx) / 2 + rng.uniform(-jitter, jitter), (ay + by) / 2 + rng.uniform(-jitter, jitter)
            pygame.draw.lines(surf, col, False, [(ax, ay), (mx, my), (bx, by)], width)

    def _sketch_rect(self, surf, rect, col, rng, jitter, width=2):
        x, y, w, h = rect
        pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        for i in range(4):
            self._sketch_line(surf, pts[i], pts[(i + 1) % 4], col, rng, jitter, width, passes=1)

    def _sketch_fill(self, surf, rect, col, rng, jitter, frac):
        x, y, w, h = rect
        n = max(1, int(w / 5))
        for i in range(int(n * max(0.0, min(1.0, frac)))):
            px = x + 3 + i * 5
            self._sketch_line(surf, (px, y + 3), (px - h * 0.25, y + h - 3), col, rng,
                              jitter, 1, passes=1)

    def _sketch_circle_dot(self, surf, cx, cy, col, rng, jitter):
        pygame.draw.circle(surf, col, (int(cx + rng.uniform(-jitter, jitter)),
                                       int(cy + rng.uniform(-jitter, jitter))), 3, 2)

    def _sketch_scribble(self, surf, cx, cy, r, col, rng, jitter):
        pts = []
        for _ in range(rng.randint(6, 11)):
            a = rng.uniform(0, math.tau)
            d = rng.uniform(r * 0.2, r)
            pts.append((cx + math.cos(a) * d, cy + math.sin(a) * d))
        pygame.draw.lines(surf, col, False, pts, 2)

    def _clipboard_sheet(self):
        p = self.player
        damage, damage_step = self._sheet_damage()
        key = (int(p.battery / 4), p.carried, p.has_lighter, p.has_cutters, p.cutters_broken,
               p.has_map, int(p.pencil_ink / (S.PENCIL_SHEETS * 0.5)), len(self.map_sheets),
               self.panel_prop.installed if self.panel_prop is not None else -1,
               bool(self.panel_prop.powered) if self.panel_prop is not None else False,
               damage_step, self.floor_i, i18n.get_language())
        if getattr(self, "_clip_key", None) == key and self._clip_surf is not None:
            return self._clip_surf
        pending = self._clip_pending
        if pending is not None:
            if pending[1] != key:
                pending = self._clip_pending = None
            else:
                if self._wear_step(pending):
                    self._clip_key = key
                    self._clip_surf = pending[0]
                    self._clip_shade = None
                    self._clip_pending = None
                return self._clip_surf
        jitter = 0.9 + damage * 3.1
        marks = self._sheet_marks(int(damage * S.CONDITION_MARKS_MAX + 0.001))
        rng = random.Random(hash(key) & 0xFFFF)
        w, h = 300, 400
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        paper = (214, 205, 180)
        surf.fill(paper)
        ink = (58, 54, 50)
        faint = (150, 142, 122)
        for i in range(1, 12):
            gy = i * 32
            pygame.draw.line(surf, (198, 189, 165), (12, gy), (w - 12, gy))
        pygame.draw.rect(surf, (150, 142, 120), (0, 0, w, h), width=2)

        y = 8
        title = self._spec_t("title")
        for dash in ("\u2014", "\u2013", " - "):
            if dash in title:
                title = title.split(dash)[-1]
                break
        self._text_on(surf, self.font_note,
                      self._ellipsize(title.strip(), self.font_note, w - 32), ink, (16, y))
        y += 34
        self._sketch_line(surf, (14, y), (w - 14, y), faint, rng, jitter, 1, passes=1)
        y += 6

        cell = pygame.Rect(18, y, 134, 36)
        self._sketch_rect(surf, cell, ink, rng, jitter)
        self._sketch_rect(surf, (cell.right, y + 11, 9, 15), ink, rng, jitter, 1)
        self._sketch_fill(surf, cell, ink, rng, jitter, p.battery / 100.0)
        y += 38

        if self.panel_prop is not None:
            y = self._sheet_caption(surf, self._spec_t("panel_label").capitalize() + ":",
                                    y, w - 36, ink) + 4
            total = max(1, int(self.spec.get("n_collectible", 1)))
            done = int(self.panel_prop.installed)
            for i in range(min(8, total)):
                bx = 18 + i * 34
                self._sketch_rect(surf, (bx, y, 26, 26), ink, rng, jitter, 2)
                if i < done:
                    self._sketch_line(surf, (bx + 4, y + 13), (bx + 11, y + 22), ink, rng, jitter, 2, 1)
                    self._sketch_line(surf, (bx + 11, y + 22), (bx + 23, y + 3), ink, rng, jitter, 2, 1)
            y += 32
            for i in range(min(10, p.carried)):
                tx = 20 + i * 11
                self._sketch_line(surf, (tx, y), (tx + 4, y + 22), ink, rng, jitter, 2, 1)
            y += 38

        exit_req = self.spec.get("exit_requires_item")
        if exit_req:
            label = self._spec_t("exit_requires_item_label") or exit_req.capitalize()
            y = self._sheet_caption(surf, label.capitalize() + ":", y, w - 36, ink) + 6
            dr = pygame.Rect(18, y, 34, 52)
            self._sketch_rect(surf, dr, ink, rng, jitter, 2)
            self._sketch_circle_dot(surf, dr.right - 8, dr.centery, ink, rng, jitter)
            have = self._player_has_exit_item(exit_req)
            ax = dr.right + 20
            if have:
                self._sketch_line(surf, (ax, y + 28), (ax + 10, y + 40), ink, rng, jitter, 3, 1)
                self._sketch_line(surf, (ax + 10, y + 40), (ax + 30, y + 10), ink, rng, jitter, 3, 1)
            else:
                self._sketch_line(surf, (ax, y + 12), (ax + 28, y + 40), ink, rng, jitter, 3, 1)
                self._sketch_line(surf, (ax + 28, y + 12), (ax, y + 40), ink, rng, jitter, 3, 1)
            y += 66
        y = max(y, 288)
        self._sketch_line(surf, (14, y - 12), (w - 14, y - 12), faint, rng, jitter, 1, passes=1)

        gx, gy = 22, y
        def slot():
            nonlocal gx, gy
            r = (gx, gy)
            gx += 74
            if gx > w - 56:
                gx, gy = 22, gy + 58
            return r
        if p.has_lighter:
            ox, oy = slot()
            self._sketch_rect(surf, (ox, oy + 14, 22, 30), ink, rng, jitter, 2)
            self._sketch_line(surf, (ox + 11, oy + 14), (ox + 16, oy), ink, rng, jitter, 2, 1)
            self._sketch_line(surf, (ox + 16, oy), (ox + 6, oy + 5), ink, rng, jitter, 2, 1)
        if p.has_cutters:
            ox, oy = slot()
            col = (150, 60, 55) if p.cutters_broken else ink
            self._sketch_line(surf, (ox, oy), (ox + 36, oy + 40), col, rng, jitter, 3, 1)
            self._sketch_line(surf, (ox + 36, oy), (ox, oy + 40), col, rng, jitter, 3, 1)
            if p.cutters_broken:
                self._sketch_line(surf, (ox + 2, oy + 20), (ox + 34, oy + 20), col, rng, jitter + 2, 2, 1)
        if p.has_map:
            ox, oy = slot()
            self._sketch_rect(surf, (ox, oy, 30, 40), ink, rng, jitter, 2)
            self._sketch_line(surf, (ox + 6, oy + 26), (ox + 24, oy + 12), ink, rng, jitter, 1, 1)
            n = int(p.pencil_ink / S.PENCIL_SHEETS + 0.5)
            for i in range(min(5, n)):
                px = ox + 40 + i * 8
                self._sketch_line(surf, (px, oy + 4), (px, oy + 34), ink, rng, jitter, 2, 1)

        for mx, my, mr, mseed in marks:
            self._sketch_scribble(surf, 24 + mx * (w - 48), 70 + my * (h - 94),
                                  mr, ink, random.Random(mseed), jitter)

        if self._clip_surf is None:
            self._paper_wear(surf, hash(key) & 0xFFFF)
            self._clip_key = key
            self._clip_surf = surf
            self._clip_shade = None
            return surf
        self._clip_pending = self._wear_start(surf, key, hash(key) & 0xFFFF)
        return self._clip_surf

    def _paper_dirt(self, w, h, floor, power):
        key = (w, h, floor, power)
        cache = self.__dict__.setdefault("_paper_dirt_cache", {})
        base = cache.get(key)
        if base is None:
            edge_x = np.minimum(np.arange(w), w - 1 - np.arange(w))[:, None] / (w * 0.5)
            edge_y = np.minimum(np.arange(h), h - 1 - np.arange(h))[None, :] / (h * 0.5)
            dirt = np.clip(np.minimum(edge_x, edge_y), 0.0, 1.0) ** power
            base = cache[key] = floor + (1.0 - floor) * dirt
        return base

    _WEAR_SLICES = 6

    def _wear_start(self, surf, key, seed, floor=0.55, grain=7.0, power=0.55):
        w, h = surf.get_size()
        noise = np.random.default_rng(seed & 0xFFFFFFFF).normal(0.0, grain, (w, h))
        shade = self._paper_dirt(w, h, floor, power) + noise / 255.0
        return [surf, key, shade, 0]

    def _wear_step(self, pending):
        surf, _key, shade, done = pending
        w, h = surf.get_size()
        band = -(-h // self._WEAR_SLICES)
        y0 = done * band
        y1 = min(h, y0 + band)
        px = pygame.surfarray.pixels3d(surf)
        flat = np.array(px[:, y0:y1], dtype=np.float64)
        flat *= shade[:, y0:y1, None]
        np.clip(flat, 0, 255, out=flat)
        px[:, y0:y1] = flat.astype(np.uint8)
        del px
        pending[3] = done + 1
        return y1 >= h

    def _paper_wear(self, surf, seed, floor=0.55, grain=7.0, power=0.55):
        px = pygame.surfarray.pixels3d(surf)
        w, h = surf.get_size()
        noise = np.random.default_rng(seed & 0xFFFFFFFF).normal(0.0, grain, (w, h))
        shade = self._paper_dirt(w, h, floor, power) + noise / 255.0
        flat = np.array(px, dtype=np.float64)
        flat *= shade[:, :, None]
        np.clip(flat, 0, 255, out=flat)
        px[:] = flat.astype(np.uint8)
        del px

    def _text_on(self, surf, font, text, col, topleft):
        surf.blit(font.render(str(text), True, col), topleft)

    _STATUS_STATES = ("playing", "elevator_ride", "hatch_climb")

    def _status_held(self):
        return (self._binding_down("status") and self.state in self._STATUS_STATES
                and not self.player.map_open)

    def _draw_mic_corner(self):
        if not (self.settings.get("mic_enabled") and self.mic.available):
            return
        w, h = 140, 5
        x = S.SCREEN_W - w - 14
        y = S.SCREEN_H - 52
        level = max(0.0, min(1.0, self.mic_vu_level))
        loud = level > S.MIC_NOISE_GATE
        self._text(self.font_sm, i18n.t("hud.mic"), S.COL_UI_DIM,
                   topleft=(x, y - 22), shadow=False)
        pygame.draw.rect(self.hud_surf, (26, 24, 22), (x, y, w, h))
        if level > 0.0:
            col = (190, 70, 60) if loud else (120, 112, 100)
            pygame.draw.rect(self.hud_surf, col, (x, y, max(1, int(w * level)), h))
        gate_x = x + int(w * S.MIC_NOISE_GATE)
        pygame.draw.line(self.hud_surf, (150, 55, 50), (gate_x, y - 3), (gate_x, y + h + 2), 1)
        if not self.mic.active and not self.mic.busy:
            self._text(self.font_sm, i18n.t("settings.mic_test_error"), (170, 95, 88),
                       topleft=(x, y + h + 6), shadow=False)

    def _draw_stamina_strip(self, y=None):
        if not self._stamina_showing():
            return
        p = self.player
        frac = max(0.0, min(1.0, p.stamina / 100.0))
        col = (150, 60, 60) if p.stamina_locked else (198, 172, 92)
        w, h = 120, 4
        x = (S.SCREEN_W - w) // 2
        y = S.SCREEN_H - 44 if y is None else y
        pygame.draw.rect(self.hud_surf, (26, 24, 22), (x, y, w, h))
        pygame.draw.rect(self.hud_surf, col, (x, y, int(w * frac), h))

    def _draw_compass_strip(self, alpha=150):
        if not self.settings.get("compass", True):
            return
        cx = S.SCREEN_W // 2
        top, w = 18, 240
        deg_per_px = 150.0 / w
        ang_deg = math.degrees(self.player.angle)
        labels = {0: i18n.t("compass.n"), 90: i18n.t("compass.e"),
                  180: i18n.t("compass.s"), 270: i18n.t("compass.w")}
        for mark_deg in range(0, 360, 15):
            rel = ((mark_deg - ang_deg + 180) % 360) - 180
            if abs(rel) > 72:
                continue
            x = cx + rel / deg_per_px
            fade = 1.0 - min(1.0, abs(rel) / 72.0)
            major = mark_deg % 90 == 0
            v = int((210 if major else 130) * fade)
            col = (max(0, v), max(0, v - 10), max(0, v - 18))
            if major:
                self._text(self.font_sm, labels[mark_deg], col, center=(x, top), shadow=False)
            else:
                pygame.draw.line(self.hud_surf, col, (x, top + 6), (x, top + 11), 1)

    def _draw_hud_minimal(self):
        p = self.player
        pad = 26
        y = S.SCREEN_H - pad - 18
        if p.battery / 100.0 < S.HUD_BATTERY_WARN:
            frac = max(0.0, min(1.0, p.battery / 100.0))
            col = (210, 70, 40) if frac < 0.15 else (205, 175, 60)
            self._text(self.font_sm, i18n.t("item.battery", n=int(p.battery)), col,
                       topleft=(pad, y), shadow=False)
            y -= 24
        state, word, col = self._condition_state()
        if state != "ok":
            self._text(self.font_sm, word, col, topleft=(pad, y), shadow=False)
        self._draw_stamina_strip()
        self._draw_compass_strip()
        self._draw_mic_corner()
        if self._status_held():
            ly = S.SCREEN_H - 120
            for line in self._task_lines():
                self._text(self.font_sm, line, S.COL_TEXT, center=(S.SCREEN_W // 2, ly), shadow=True)
                ly += 24
            for line in self._pocket_lines()[:3]:
                self._text(self.font_sm, line, S.COL_UI_DIM, center=(S.SCREEN_W // 2, ly), shadow=True)
                ly += 22

    def _paper_shaded(self, sheet, color):
        tints = self.__dict__.setdefault("_paper_tints", {})
        size = sheet.get_size()
        tint = tints.get(size)
        if tint is None:
            tint = tints[size] = pygame.Surface(size, pygame.SRCALPHA)
        tint.fill(color)
        shaded = sheet.copy()
        shaded.blit(tint, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        return shaded

    def _draw_clipboard(self, rest_frac):
        t = self._clip_raise
        ease = t * t * (3.0 - 2.0 * t)
        shown = rest_frac + (1.0 - rest_frac) * ease
        if shown <= 0.01:
            return False
        sheet = self._clipboard_sheet()
        lit = 0.30 + 0.70 * max(0.0, min(1.0, self.player.room_light * 1.6))
        if abs(lit - getattr(self, "_clip_lit", -1.0)) > 0.04 or self._clip_shade is None:
            self._clip_lit = lit
            self._clip_shade = self._paper_shaded(
                sheet, (int(255 * lit), int(252 * lit), int(246 * lit), 255))
        sheet = self._clip_shade
        tilt = -6.0 + 4.0 * ease
        if getattr(self, "_clip_turned_src", None) is not sheet or self._clip_turned_tilt != tilt:
            self._clip_turned_src = sheet
            self._clip_turned_tilt = tilt
            self._clip_turned = pygame.transform.rotate(sheet, tilt)
        turned = self._clip_turned
        y = int(S.SCREEN_H - 18 - turned.get_height() * shown)
        self.hud_surf.blit(turned, (26, y))
        return True

    def _draw_hud_clipboard(self):
        self._draw_stamina_strip()
        self._draw_compass_strip()
        self._draw_mic_corner()
        self._draw_clipboard(S.HUD_CLIPBOARD_REST)

    def _form_watch(self):
        p = self.player
        state, _, _ = self._condition_state()
        return {
            "carried": p.carried,
            "installed": self.panel_prop.installed if self.panel_prop is not None else 0,
            "powered": bool(self.panel_prop.powered) if self.panel_prop is not None else False,
            "lighter": p.has_lighter, "cutters": p.has_cutters, "broken": p.cutters_broken,
            "map": p.has_map, "sheets": len(self.map_sheets),
            "low": p.battery / 100.0 < S.HUD_BATTERY_WARN,
            "cond": state,
        }

    def _form_note(self, snap):
        was = self._form_seen
        self._form_seen = snap
        if was is None:
            return None
        if snap["installed"] != was["installed"] or snap["powered"] != was["powered"]:
            return self._task_lines()[0] if self._task_lines() else None
        if snap["cond"] != was["cond"]:
            return i18n.t("hud.condition") + ": " + self._condition_state()[1]
        if snap["low"] and not was["low"]:
            return i18n.t("item.battery", n=int(self.player.battery))
        if snap["broken"] and not was["broken"]:
            return i18n.t("item.cutters_broken")
        for k, key in (("carried", None), ("lighter", "item.lighter"), ("cutters", "item.cutters"),
                       ("map", "item.map")):
            if snap[k] != was[k] and key:
                return i18n.t(key)
        if snap["carried"] != was["carried"]:
            return i18n.t("hud.carried", label=self._spec_t("collectible_label").capitalize(),
                          n=snap["carried"])
        if snap["sheets"] != was["sheets"]:
            return i18n.t("item.sheets", n=snap["sheets"])
        return None

    _FORM_PAPER = (184, 182, 186)
    _FORM_INK = (78, 70, 122)
    _FORM_DIM = (124, 118, 156)
    _FORM_RULE = (164, 160, 184)
    _FORM_W = 400
    _FORM_FOLD = 176
    _FORM_CONTENT = 336
    _FORM_H = 400
    _FORM_LABEL_X = 26
    _FORM_VALUE_X = 172
    _FORM_TEAR_SEED = 0x9E17

    def _typed(self, surf, text, pos, col, font, smear=1, keep=None):
        if smear:
            ghost = font.render(text, True, col)
            ghost.set_alpha(74)
            surf.blit(ghost, (pos[0] + smear, pos[1] + smear))
        surf.blit(font.render(text, True, col), pos)
        if keep is not None and text:
            keep.append((text, font, pos))

    def _form_field(self, surf, y, label, value, smear, val_col=None, keep=None):
        x0, x1 = self._FORM_LABEL_X, self._FORM_VALUE_X
        self._typed(surf, label, (x0, y + 4), self._FORM_DIM, self.font_type_s, smear, keep)
        value = self._ellipsize(value, self.font_type, self._FORM_W - x1 - 30)
        self._typed(surf, value, (x1, y), val_col or self._FORM_INK, self.font_type, smear, keep)
        pygame.draw.line(surf, self._FORM_RULE, (x0, y + 24), (self._FORM_W - 30, y + 24))

    def _form_row(self, surf, y, label, value, smear, keep=None, charge=None):
        x0, x1 = self._FORM_LABEL_X + 8, self._FORM_VALUE_X
        label = self._ellipsize(label, self.font_type_s, x1 - x0 - 22)
        self._typed(surf, label, (x0, y), self._FORM_INK, self.font_type_s, smear, keep)
        lw = self.font_type_s.size(label)[0]
        for dx in range(x0 + lw + 9, x1 - 9, 7):
            pygame.draw.line(surf, self._FORM_DIM, (dx, y + 11), (dx + 1, y + 11))
        if charge is not None:
            self._form_battery(surf, x1, y - 1, charge)
        else:
            value = self._ellipsize(value, self.font_type_s, self._FORM_W - x1 - 30)
            self._typed(surf, value, (x1, y), self._FORM_INK, self.font_type_s, smear, keep)

    def _form_battery(self, surf, x, y, frac, w=92, h=20):
        col = self._FORM_INK
        pygame.draw.rect(surf, col, (x, y, w, h), width=2)
        pygame.draw.rect(surf, col, (x + w, y + 6, 5, h - 12), width=2)
        for i in range(int((w - 8) / 5 * max(0.0, min(1.0, frac)))):
            gx = x + 4 + i * 5
            pygame.draw.line(surf, col, (gx, y + 4), (gx - 3, y + h - 4))

    def _form_sheet(self):
        p = self.player
        state, cond_word, _ = self._condition_state()
        damage, damage_step = self._sheet_damage()
        total = int(self.spec.get("n_collectible", 0))
        installed = self.panel_prop.installed if self.panel_prop is not None else -1
        exit_req = self.spec.get("exit_requires_item")
        key = (int(p.battery / 4), p.carried, installed,
               bool(self.panel_prop.powered) if self.panel_prop is not None else False,
               p.has_lighter, p.has_cutters, p.cutters_broken, p.has_map,
               len(self.map_sheets), int(p.pencil_ink / (S.PENCIL_SHEETS * 0.5)),
               bool(exit_req) and self._player_has_exit_item(exit_req),
               state, damage_step, self.floor_i, i18n.get_language())
        if self._form_key == key and self._form_surf is not None:
            return self._form_surf
        smear = 1 + int(damage * 2.99)
        rng = random.Random(hash(key) & 0xFFFF)
        struck = []
        w, h = self._FORM_W, self._FORM_H
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        surf.fill(self._FORM_PAPER)
        tear = random.Random(self._FORM_TEAR_SEED)
        ragged = [(w, 0)] + [(w - tear.randint(0, 9), y) for y in range(0, h + 10, 10)] + [(w, h)]
        pygame.draw.polygon(surf, (0, 0, 0, 0), ragged)

        self._typed(surf, i18n.t("form.title"), (26, 12), self._FORM_INK, self.font_type, smear, struck)
        pygame.draw.line(surf, (150, 146, 172), (26, 38), (w - 30, 38))
        copy_txt = i18n.t("form.copy")
        self._typed(surf, copy_txt, (w - 30 - self.font_type_s.size(copy_txt)[0], 44),
                    self._FORM_DIM, self.font_type_s, smear, struck)

        title = self._spec_t("title")
        for dash in ("\u2014", "\u2013", " - "):
            if dash in title:
                title = title.split(dash)[-1]
                break
        y = 72
        self._form_field(surf, y, i18n.t("form.ward"), title.strip().upper(), smear, keep=struck)
        y += 34
        self._form_field(surf, y, i18n.t("form.state"), cond_word.upper(), smear,
                         val_col=(122, 62, 112) if state == "worst" else None, keep=struck)
        y += 34
        if self.panel_prop is not None:
            label = self._spec_t("panel_label").upper()
            done = i18n.t("form.done") if self.panel_prop.powered else "%d / %d" % (installed, total)
            self._form_field(surf, y, i18n.t("form.assigned"),
                             "%s  %s" % (self._ellipsize(label, self.font_type, 128), done),
                             smear, keep=struck)

        fold = self._FORM_FOLD
        pygame.draw.line(surf, (222, 220, 222), (0, fold - 1), (w, fold - 1))
        pygame.draw.line(surf, (156, 152, 156), (0, fold), (w, fold))

        y = fold + 14
        self._typed(surf, i18n.t("form.issued"), (self._FORM_LABEL_X, y),
                    self._FORM_DIM, self.font_type_s, smear, struck)
        y += 26
        rows = [(i18n.t("hud.flashlight"), None, p.battery / 100.0)]
        if p.has_lighter:
            rows.append((i18n.t("item.lighter"), i18n.t("hud.have"), None))
        if p.has_cutters:
            rows.append((i18n.t("item.cutters"),
                         i18n.t("form.broken") if p.cutters_broken else i18n.t("hud.have"), None))
        if p.has_map:
            rows.append((i18n.t("item.map"), "%s %d   %s %d" % (
                i18n.t("form.sheets"), len(self.map_sheets),
                i18n.t("form.pencils"), int(p.pencil_ink / S.PENCIL_SHEETS)), None))
        if exit_req:
            req = (self._spec_t("exit_requires_item_label") or exit_req)
            if req.upper() not in [r[0].upper() for r in rows]:
                rows.append((req, i18n.t("hud.have" if self._player_has_exit_item(exit_req)
                                          else "hud.dont_have"), None))
        for label, value, charge in rows:
            self._form_row(surf, y, label.upper(), (value or "").upper(), smear,
                           keep=struck, charge=charge)
            y += 23

        for _ in range({"ok": 0, "bad": 1, "worst": 4}[state]):
            if not struck:
                break
            text, font, (ox, oy) = struck[rng.randrange(len(struck))]
            ghost = font.render(text, True, (104, 94, 146))
            at = (max(14, min(w - ghost.get_width() - 14, ox + rng.randint(-30, 90))),
                  max(12, min(h - ghost.get_height() - 16, oy + rng.randint(-46, 46))))
            ghost.set_alpha(150)
            surf.blit(ghost, at)
            surf.blit(ghost, (at[0] + smear + 1, at[1] + smear + 1))

        self._paper_wear(surf, hash(key) & 0xFFFF, floor=0.80, grain=6.0, power=0.5)

        self._form_key = key
        self._form_surf = surf
        self._form_shade = None
        return surf

    def _draw_hud_chart(self):
        self._draw_stamina_strip()
        self._draw_compass_strip()
        self._draw_mic_corner()
        sheet = self._form_sheet()
        lit = 0.34 + 0.54 * max(0.0, min(1.0, self.player.room_light * 1.6))
        if abs(lit - self._form_lit) > 0.04 or self._form_shade is None:
            self._form_lit = lit
            self._form_shade = self._paper_shaded(
                sheet, (int(255 * lit), int(252 * lit), int(255 * lit), 255))
        sheet = self._form_shade
        t = self._clip_raise
        ease = t * t * (3.0 - 2.0 * t)
        show = self._FORM_FOLD + (self._FORM_CONTENT - self._FORM_FOLD) * ease
        if getattr(self, "_form_turned_src", None) is not sheet:
            self._form_turned_src = sheet
            self._form_turned = pygame.transform.rotate(sheet, 1.2)
        self.hud_surf.blit(self._form_turned, (22, int(S.SCREEN_H - show)))

    def _draw_hud_common(self):
        p = self.player
        san_frac = p.sanity / S.SANITY_MAX
        if san_frac < 0.35:
            pulse = 0.5 + 0.5 * math.sin(pygame.time.get_ticks() * 0.006)
            intensity = min(1.0, (1 - san_frac) * pulse)
            self.sanity_vignette.set_alpha(int(255 * intensity))
            self.hud_surf.blit(self.sanity_vignette, (0, 0))
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

        if self.hatch_turning:
            frac = min(1.0, self.hatch_turn_progress)
            self._draw_action_bar(cx, cy + 34, frac, i18n.t("hud.hatch_turning"), (150, 190, 200))

        if self.fence_cutting:
            frac = min(1.0, self.fence_cut_progress)
            self._draw_action_bar(cx, cy + 34, frac, i18n.t("hud.fence_cutting"), (205, 130, 70))

        if self.cutters_repair_target is not None and self.cutters_repair_t > 0:
            frac = min(1.0, self.cutters_repair_t / S.CUTTERS_REPAIR_SECONDS)
            self._draw_action_bar(cx, cy + 34, frac, i18n.t("hud.repairing_cutters"), (205, 175, 60))

        if self.hide_transition is not None or self.peek_t > 0.0:
            prompt, alt_prompt = None, None
        else:
            res = self.find_interactable()
            prompt, alt_prompt = self.prompt_text(res), self.alt_prompt_text(res)
        if prompt:
            self._text(self.font_md, prompt, (225, 220, 205), center=(cx, S.SCREEN_H - 150))
        if alt_prompt:
            self._text(self.font_md, alt_prompt, (190, 185, 170), center=(cx, S.SCREEN_H - 120))

        if self.hint_timer > 0 and self.hint_text and self.peek_t <= 0.0:
            lines = []
            for para in str(self.hint_text).split("\n"):
                lines.extend(self._wrap_text(para, self.font_md, S.SCREEN_W - 260) or [""])
            yy = S.SCREEN_H - 190 - (len(lines) - 1) * 13
            for line in lines:
                self._text(self.font_md, line, (210, 170, 90), center=(cx, yy))
                yy += 26

        if self.floor_banner_timer > 0:
            alpha = 255 if self.floor_banner_timer > 1.0 else int(255 * self.floor_banner_timer)
            surf = self.font_lg.render(str(self.floor_banner), True, S.COL_TEXT)
            surf.set_alpha(alpha)
            r = surf.get_rect(center=(cx, 70))
            self.hud_surf.blit(surf, r)

    NOTE_TEX = (768, 992)

    def _open_note(self, text, back_text=None, back_kind=None):
        self.note_text = text
        self.note_back_text = back_text
        self.note_back_kind = back_kind
        self.note_spin = [0.0, 0.0]
        self.renderer.note_spin = (0.0, 0.0)
        self._note_settling = False
        self._note_dirty = True
        self.note_return_state = self.state
        self._note_fade = 0.0
        self._note_closing = False
        self.state = "note"

    def _close_note(self):
        if self.state != "note" or self._note_closing:
            return False
        self._note_closing = True
        return True

    def _finish_close_note(self):
        self.state = self.note_return_state or "playing"
        self._lean_prev_left = self._binding_down("lean_left")
        self._lean_prev_right = self._binding_down("lean_right")
        self._lean_active_dir = 0
        self.note_text = None
        self.note_back_text = None
        self.note_back_kind = None
        self._note_closing = False

    NOTE_BACK_KINDS = ("blank", "form", "scrap")

    @staticmethod
    def _note_written_text(text):
        out = str(text).strip()
        if len(out) > 1 and out[0] == "\u00ab" and out[-1] == "\u00bb":
            out = out[1:-1].strip()
        return out

    def _note_write_hand(self, surf, written, rng, pad_x, pad_top, ink=(54, 48, 44)):
        if not written:
            return
        w = surf.get_width()
        font = self.font_note_big
        line_h = font.get_linesize()
        lines = self._wrap_text(self._note_written_text(written), font, w - pad_x * 2 - 30)
        drift = rng.uniform(-0.016, 0.016)
        y = pad_top + rng.uniform(-10, 10)
        for i, line in enumerate(lines):
            shade = rng.randint(-14, 10)
            glyphs = font.render(line, True, (ink[0] + shade, ink[1] + shade, ink[2] + shade))
            glyphs = pygame.transform.rotate(glyphs, rng.uniform(-1.1, 1.1))
            x = pad_x + rng.uniform(-6, 14) + i * drift * line_h
            surf.blit(glyphs, (int(x), int(y + rng.uniform(-5, 5))))
            y += line_h * rng.uniform(0.94, 1.07)

    def _note_back_face(self, kind, written, seed):
        w, h = self.NOTE_TEX
        rng = random.Random(seed)
        surf = pygame.Surface((w, h), pygame.SRCALPHA)

        if kind == "form":
            surf.fill((214, 208, 190))
            ink = (96, 92, 108)
            title = self.font_form.render(i18n.t("note.back.form_title"), True, ink)
            surf.blit(title, (84, 92))
            pygame.draw.line(surf, ink, (84, 150), (w - 84, 150), 3)
            y = 210
            for field in ("dept", "patient", "date", "sign"):
                label = self.font_form_s.render(i18n.t("note.back.form_" + field), True, ink)
                surf.blit(label, (84, y))
                lx = 84 + label.get_width() + 24
                pygame.draw.line(surf, (168, 162, 180), (lx, y + label.get_height() - 4),
                                 (w - 84, y + label.get_height() - 4), 2)
                y += 108
            stamp = self.font_form_s.render(i18n.t("note.back.form_foot"), True, (150, 146, 162))
            surf.blit(stamp, (w - 84 - stamp.get_width(), h - 150))
            self._paper_wear(surf, seed, floor=0.72, grain=5.0)
        elif kind == "scrap":
            surf.fill((188, 176, 148))
            for _ in range(rng.randint(4, 7)):
                r = rng.randint(50, 190)
                blot = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
                pygame.draw.circle(blot, (120, 104, 74, rng.randint(26, 52)), (r, r), r)
                surf.blit(blot, (rng.randint(-r, w - r), rng.randint(-r, h - r)))
            for _ in range(rng.randint(2, 4)):
                x0, y0 = rng.randint(60, w - 60), rng.randint(80, h - 80)
                pygame.draw.lines(surf, (128, 116, 92), False,
                                  [(x0 + rng.randint(-90, 90), y0 + rng.randint(-40, 40))
                                   for _ in range(4)], rng.randint(2, 4))
            self._paper_wear(surf, seed, floor=0.50, grain=11.0, power=0.38)
        else:
            surf.fill((216, 207, 182))
            self._paper_wear(surf, seed, floor=0.66, grain=6.0)

        if written:
            top = 640 if kind == "form" else 104
            self._note_write_hand(surf, written, random.Random(seed ^ 0x77), 78, top)
        return surf

    def _note_faces(self, text, back_text=None, back_kind=None):
        key = (str(text), str(back_text or ""), back_kind, i18n.get_language())
        if self._note_key == key and self._note_surf is not None:
            return self._note_surf
        w, h = self.NOTE_TEX
        seed = hash(key) & 0xFFFF
        if back_kind not in self.NOTE_BACK_KINDS:
            back_kind = self.NOTE_BACK_KINDS[seed % len(self.NOTE_BACK_KINDS)]

        front = pygame.Surface((w, h), pygame.SRCALPHA)
        front.fill((216, 207, 182))
        self._note_write_hand(front, text, random.Random(seed), 78, 104)
        self._paper_wear(front, seed, floor=0.66, grain=6.0)

        self._note_key = key
        self._note_surf = (front, self._note_back_face(back_kind, back_text, seed ^ 0x51))
        return self._note_surf

    def _upload_note_sheet(self):
        if not self._note_dirty or self.note_text is None:
            return
        front, back = self._note_faces(self.note_text, self.note_back_text, self.note_back_kind)
        self.renderer.set_note_sheet(
            pygame.image.tostring(front, "RGBA", True),
            pygame.image.tostring(pygame.transform.flip(back, True, False), "RGBA", True),
            self.NOTE_TEX)
        self._note_dirty = False

    def _draw_note_scene(self):
        self._upload_note_sheet()
        self._sync_window_size()
        fade = self._note_fade
        eased = fade * fade * (3.0 - 2.0 * fade)
        self.renderer.draw_note_examine(tuple(self.note_spin), self.window_size, fade=eased)
        lines = [i18n.t("hint.note_turn", turn=self._binding_label("map_pencil")),
                 i18n.t("hint.note_reset"),
                 i18n.t("hint.note_close", close=self._binding_label("interact"))]
        col = tuple(int(c * eased) for c in (186, 178, 162))
        y = S.SCREEN_H - 34 - (len(lines) - 1) * 26
        for line in lines:
            self._text(self.font_sm, line, col, topleft=(34, y), shadow=False)
            y += 26
        self.renderer.composite(None, (S.SCREEN_W, S.SCREEN_H), self.window_size,
                                 hud_surface=self.hud_surf, keep_color=True)
        pygame.display.flip()

    def _turn_note(self, mouse_dx, mouse_dy):
        self.note_spin[0] += mouse_dx * S.NOTE_TURN_SPEED
        self.note_spin[1] += mouse_dy * S.NOTE_TURN_SPEED
        self.renderer.note_spin = (self.note_spin[0], self.note_spin[1])

    def _draw_action_bar(self, cx, y, frac, label, color):
        w, h = 220, 12
        self._text(self.font_sm, label, S.COL_TEXT, center=(cx, y - 12), shadow=True)
        rect = pygame.Rect(cx - w // 2, y, w, h)
        pygame.draw.rect(self.hud_surf, (28, 25, 24), rect, border_radius=4)
        fill = pygame.Rect(rect.x, rect.y, int(rect.w * max(0.0, min(1.0, frac))), rect.h)
        pygame.draw.rect(self.hud_surf, color, fill, border_radius=4)
        pygame.draw.rect(self.hud_surf, (95, 88, 82), rect, width=1, border_radius=4)

    def _elevator_active_light_index(self):
        if self.exit_prop is None or not self.exit_prop.powered:
            return -1
        n = S.ELEVATOR_ARRIVE_LIGHT_COUNT
        frac = min(1.0, self.elevator_call_t / S.ELEVATOR_ARRIVE_SECONDS)
        return max(0, min(n - 1, int(frac * n)))

    def _restart_from_end(self):
        if self._in_debug_preview():
            self._start_debug_level()
        else:
            self.new_game()
            self._start_playing()

    END_BUTTON_Y = S.SCREEN_H // 2 + 182

    def _end_buttons(self, y0=None):
        cx = S.SCREEN_W // 2
        y0 = self.END_BUTTON_Y if y0 is None else y0
        w, h, gap = 230, 52, 18
        return [
            self._button((cx - w - gap // 2, y0, w, h), i18n.t("end.restart"), self._restart_from_end),
            self._button((cx + gap // 2, y0, w, h), i18n.t("end.to_menu"), self._to_menu),
        ]

    def _stats_extra_text(self):
        return i18n.t("end.stats_extra", notes=self.stats["notes"], batteries=self.stats["batteries"],
                      scares=self.stats["scares"])

    def _end_veil(self, dim):
        cache = getattr(self, "_end_veil_cache", None)
        if cache is None:
            cache = self._end_veil_cache = {}
        surf = cache.get(dim)
        if surf is not None:
            return surf
        h = S.SCREEN_H
        yy = np.arange(h, dtype=np.float32) / max(1, h - 1)
        band = np.clip((yy - 0.34) / 0.22, 0.0, 1.0) * np.clip((1.02 - yy) / 0.18, 0.0, 1.0)
        alpha = (band * band * (3.0 - 2.0 * band) * dim).astype(np.uint8)
        surf = pygame.Surface((S.SCREEN_W, h), pygame.SRCALPHA)
        view = pygame.surfarray.pixels_alpha(surf)
        view[:, :] = alpha[None, :]
        del view
        px = pygame.surfarray.pixels3d(surf)
        px[:, :, 0] = 4
        px[:, :, 1] = 6
        px[:, :, 2] = 6
        del px
        cache[dim] = surf
        return surf

    def _draw_end_screen(self, title, title_color, subtitle, lines, alpha_mult=1.0,
                         dim=150, text_color=None, dim_color=None):
        real_surf = self.hud_surf
        fading = alpha_mult < 1.0
        if fading:
            self.hud_surf = pygame.Surface((S.SCREEN_W, S.SCREEN_H), pygame.SRCALPHA)
        cx, cy = S.SCREEN_W // 2, S.SCREEN_H // 2
        self.hud_surf.blit(self._end_veil(dim), (0, 0))
        self._text(self.font_title, title, title_color, center=(cx, cy - 8), shadow=True)
        self._text(self.font_md, subtitle, text_color or S.COL_TEXT, center=(cx, cy + 48), shadow=True)
        y = cy + 92
        for line in lines:
            self._text(self.font_sm, line, dim_color or S.COL_UI_DIM, center=(cx, y), shadow=True)
            y += 26
        for btn in self._end_buttons():
            self._draw_button(btn)
        if fading:
            temp = self.hud_surf
            self.hud_surf = real_surf
            temp.set_alpha(int(255 * max(0.0, min(1.0, alpha_mult))))
            self.hud_surf.blit(temp, (0, 0))

    def _end_stat_lines(self, with_floor=True):
        mins, secs = divmod(int(self.elapsed), 60)
        if with_floor:
            first = i18n.t("end.survived", time=f"{mins:02d}:{secs:02d}",
                           floor=self.floor_i + 1, total=len(S.FLOOR_SPECS))
        else:
            first = i18n.t("win.time", time=f"{mins:02d}:{secs:02d}")
        return [first, self._stats_extra_text(),
                i18n.t("end.seed", seed=self.floor_seed)]

    def _draw_gameover(self, title, color, subtitle):
        self._draw_end_screen(title, color, subtitle, self._end_stat_lines())

    def _draw_win(self, alpha_mult=1.0):
        self._draw_end_screen(i18n.t("win.title"), (90, 200, 140), i18n.t("win.subtitle"),
                              self._end_stat_lines(with_floor=False), alpha_mult=alpha_mult)

    def _draw_angel_end(self, alpha_mult=1.0):
        overlay = pygame.Surface((S.SCREEN_W, S.SCREEN_H), pygame.SRCALPHA)
        overlay.fill((255, 252, 240, 255))
        self.hud_surf.blit(overlay, (0, 0))
        self._draw_end_screen(i18n.t("angel.title"), (40, 34, 28), i18n.t("angel.subtitle"),
                              self._end_stat_lines(with_floor=False), alpha_mult=alpha_mult,
                              dim=0, text_color=(70, 62, 54), dim_color=(96, 86, 76))

    def run(self):
        while self.running:
            dt = self.clock.tick(self.settings["fps_limit"]) / 1000.0
            self.handle_events()
            self.update(dt)
            self.draw()
        self.mic.shutdown()
        self.sounds.shutdown()
        pygame.quit()
        if self._next_mode == "editor":
            return "editor"
        sys.exit(0)
