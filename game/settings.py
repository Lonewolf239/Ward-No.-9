import pygame

TITLE = "ПАЛАТА №9"
VERSION = "ALPHA_3"
AUTHOR = "Lonewolf239"

SCREEN_W, SCREEN_H = 1280, 720
FPS = 60


GFX_RENDER_SCALE_OPTIONS = ("240p", "320p", "400p", "480p", "640p")
GFX_RENDER_SCALE_VALUES = {
    "240p": ((240, 135), 135.0),
    "320p": ((320, 180), 180.0),
    "400p": ((400, 225), 225.0),
    "480p": ((480, 270), 270.0),
    "640p": ((640, 360), 360.0),
}

GFX_SHADOW_RES_OPTIONS = ("low", "medium", "high", "ultra")
GFX_SHADOW_RES_VALUES = {
    "low":    (64, 96, 192),
    "medium": (96, 128, 256),
    "high":   (128, 160, 320),
    "ultra":  (160, 192, 384),
}

GFX_SHADOW_PENUMBRA_OPTIONS = ("low", "medium", "high")


GFX_SETTING_OPTIONS = {
    "gfx_render_scale": GFX_RENDER_SCALE_OPTIONS,
    "gfx_shadow_res": GFX_SHADOW_RES_OPTIONS,
    "gfx_shadow_penumbra": GFX_SHADOW_PENUMBRA_OPTIONS,
}

QUALITY_PRESETS = {
    "low":    {"gfx_render_scale": "240p", "gfx_shadow_res": "low",
               "gfx_shadow_penumbra": "low"},
    "medium": {"gfx_render_scale": "320p", "gfx_shadow_res": "medium",
               "gfx_shadow_penumbra": "medium"},
    "high":   {"gfx_render_scale": "400p", "gfx_shadow_res": "high",
               "gfx_shadow_penumbra": "high"},
    "ultra":  {"gfx_render_scale": "480p", "gfx_shadow_res": "ultra",
               "gfx_shadow_penumbra": "high"},
}
QUALITY_PRESET_ORDER = ("low", "medium", "high", "ultra", "custom")


def match_quality_preset(values):
    for name, preset in QUALITY_PRESETS.items():
        if all(values.get(k) == v for k, v in preset.items()):
            return name
    return "custom"

MAZE_W, MAZE_H = 21, 21
WALL_HEIGHT = 1.15

FLOOR_CEILING_TEX_DENSITY = 2.5
FLOOR_TEX_PERIODS = 2
CEILING_TEX_PERIODS = 4
FLOOR_CELL_TONE_VAR = 0.05

FLOOR = 0
WALL_CONCRETE = 1
WALL_TILE = 2
WALL_METAL = 3
WALL_BLOOD = 4
WALL_FENCE = 5
WALL_FOREST = 6
WALL_SHED = 7
WALL_WINDOW = 8
WALL_BARS = 9
WALL_BRICK = 10
WALL_OUTDOOR = 11
SEE_THROUGH_WALLS = (WALL_WINDOW, WALL_BARS)
BUILDING_WALLS = (WALL_SHED, WALL_BRICK)

YARD_EAVE_DROP = 0.25 * 0.36
YARD_DOOR_CLEARANCE = 0.02

WALL_HEIGHTS = {
    WALL_FENCE: 0.85,
    WALL_FOREST: 2.6,
    WALL_SHED: 1.05,
    WALL_BRICK: 1.05,
}

PLAYER_RADIUS = 0.22
WALK_SPEED = 1.85
SPRINT_SPEED = 3.3
CROUCH_SPEED = 0.95
MOVE_ACCEL_RATE = 7.0
MOVE_DECEL_RATE = 10.0
CROUCH_EYE_DROP = 0.22
CROUCH_TRANSITION_RATE = 8.0
HIDE_VIGNETTE_FADE = 0.35
ROT_SPEED = 2.6
MOUSE_SENSITIVITY = 0.0022
MOUSE_SENSITIVITY_Y = 0.0016
PITCH_LIMIT = 1.3

FLASHLIGHT_DRAIN = 1.2
FLASHLIGHT_LOW = 20.0
BATTERY_PICKUP_AMOUNT = 60.0

LIGHTER_LIGHT_RADIUS = 1.5

MAP_TEXTURE_SIZE = 256
MAP_PAGE_TURN_SECONDS = 0.42
MAP_PENCIL_RAISE_RATE = 11.0

HUD_STYLES = ("clipboard", "chart", "minimal")
HUD_REFRESH_HZ = 60.0
DEBUG_HUD_REFRESH_HZ = 10.0
CONDITION_OK = 0.62
CONDITION_BAD = 0.28
CONDITION_CLEAN = 0.75
CONDITION_MARKS_MAX = 11
HUD_STAMINA_LINGER = 2.0
HUD_BATTERY_WARN = 0.34
HUD_CLIPBOARD_REST = 0.40
HUD_FORM_SECONDS = 5.0
HUD_FORM_MAX = 4
PENCIL_SHEETS = 7.5
PENCIL_MAX_CARRIED = 4
MAP_CURSOR_SPEED = 0.0014
MAP_LINE_WIDTH = 3
MAP_RAISE_RATE = 9.0
LIGHTER_LIGHT_COLOR = (0.80, 0.42, 0.15)
LIGHTER_LIGHT_LEVEL_BONUS = 0.26
LIGHTER_DARK_DRAIN_MULT = 0.60
HELD_ITEM_SWAY_AMOUNT = 0.012

STAMINA_MAX = 100.0
STAMINA_DRAIN = 11.0
STAMINA_REGEN = 18.0
STAMINA_REGEN_STANDING = 26.0
STAMINA_EXHAUST_LOCKOUT = 8.0

SANITY_MAX = 100.0
SANITY_DARK_DRAIN = 0.55
SANITY_HIDE_DRAIN = 0.18
SANITY_MONSTER_DRAIN = 5.5
SANITY_REGEN = 1.1
SANITY_HIDE_CROUCH_MULT = 1.6
SANITY_HIDE_LIT_REGEN = 0.5
SANITY_DARK_DRAIN_MAX_MULT = 2.0
SANITY_DARK_DRAIN_RAMP_SECONDS = 45.0

TOTAL_BATTERIES = 6
TOTAL_NOTES = 5
DOOR_BREAK_SECONDS = 2.2
DOOR_FALL_SECONDS = 0.8
DOOR_FALL_LAND = 0.72
DOOR_FALL_REST = 1.0
DOOR_FALL_BOUNCE = 0.16
DOOR_FALL_SLIDE = 0.06
DOOR_STRAIN_TILT = 0.12
DOOR_STRAIN_DECAY = 0.45
DOOR_BREAK_TRIGGER_DIST = 0.6
DOOR_CLOSE_TRAIL_DIST = 1.3
DOOR_LATCH_ANIM_RATE = 6.0
INSTALL_HOLD_SECONDS = 1.1
NOTE_FADE_SECONDS = 0.26
NOTE_SETTLE_RATE = 9.0
NOTE_TURN_SPEED = 0.012
PEEK_HOLD_SECONDS = 0.35
PEEK_TRANSITION_SECONDS = 0.25
PEEK_FOV_DEGREES = 65.0
DOOR_KEYHOLE_LOCAL_Y = -0.4
DOOR_KEYHOLE_LOCAL_Z = 0.44

LEAN_MAX_OFFSET = 0.33
LEAN_TRANSITION_RATE = 6.0
LEAN_HITBOX_RADIUS = 0.14
MONSTER_LEAN_DETECT_MULT = 1.9
MONSTER_LEAN_RANGE_MULT = 0.85
LEAN_ROLL_DEGREES = 11.0
LEAN_INPUT_GRACE_SECONDS = 0.2
LEAN_SPEED_MULT = 0.5

MIC_LEVEL_SCALE = 7.0
MIC_NOISE_GATE = 0.05
MIC_VU_ATTACK_RATE = 26.0
MIC_VU_RELEASE_RATE = 9.0

MONSTER_BASE_SPEED = 1.55
MONSTER_HUNT_SPEED = 2.85
MONSTER_VISION_RANGE = 5.8
MONSTER_VISION_RANGE_LIT = 8.3
MONSTER_VISION_LIGHT_NORM = 0.5
MONSTER_FOV_ACUTE_RAD = 0.75
MONSTER_FOV_PERIPHERAL_RAD = 1.31
MONSTER_FOV_PERIPHERAL_RANGE_MULT = 0.55
MONSTER_FOV_PERIPHERAL_DETECT_SECONDS_LIT = 0.6
MONSTER_FOV_PERIPHERAL_DETECT_SECONDS_DARK = 1.4
MONSTER_FOV_ACUTE_DETECT_SECONDS_LIT = 0.3
MONSTER_FOV_ACUTE_DETECT_SECONDS_DARK = 0.5
SANITY_NEAR_RANGE = 6.5
SANITY_DARK_RANGE = 4.0
SANITY_SAFE_RANGE = 6.0
SANITY_TERROR_VISION_MULT = 1.45

NOISE_WALK = 4.4
NOISE_SPRINT = 11.0
NOISE_CROUCH = 1.6
NOISE_BUMP = 5.0
NOISE_DOOR = 5.5
NOISE_LATCH = 2.0
NOISE_LOCKER = 6.0
NOISE_CUTTERS = 9.0
NOISE_PULSE_SECONDS = 0.4

MONSTER_GROWL_FALLOFF = 12.0
MONSTER_TURN_RATE = 6.3
MONSTER_TURN_RATE_HUNT = 11.0
MONSTER_TURN_GAIN = 7.0
MONSTER_LOOK_AHEAD = 1.7
MONSTER_TURN_SLOW_MIN = 0.72
MONSTER_PIVOT_ANGLE = 1.05
MONSTER_ARRIVE_DIST = 0.30

DEBUG_SPECTATOR_AMBIENT = 0.80
DEBUG_SPECTATOR_FOG = 60.0
DEBUG_SPECTATOR_SPEED = 4.5
DEBUG_SPECTATOR_SPRINT = 11.0
DEBUG_SPECTATOR_RISE = 3.2
DEBUG_SPECTATOR_Z_MIN = -0.3
DEBUG_SPECTATOR_Z_MAX = 9.0
MONSTER_FOV_HUNT_RAD = 2.30
MONSTER_HUNT_TRACK_MULT = 2.4
MONSTER_LOCKER_NOTICE_RANGE = 3.0
CATCH_TURN_SECONDS = 0.34
CATCH_BACK_OFF = 0.85
CATCH_EYE_RISE = 0.10
SCREAMER_SECONDS = 0.85
SCREAMER_EVENTS = ((0.0, "stinger", 0.8), (0.34, "bang", 0.5))
MONSTER_CATCH_RADIUS = 0.62
MONSTER_RADIUS = 0.24
MONSTER_REPLAN_INTERVAL = 0.6
MONSTER_PATH_LOOKAHEAD = 10
MONSTER_PATH_LOOKAHEAD_DIST = 8.0
MONSTER_LOSE_INTEREST_TIME = 4.0
MONSTER_HUNT_ARRIVE_GRACE = 6.0
MONSTER_CHASE_MAX_SECONDS = 14.0
MONSTER_CHASE_BLIND_GIVE_UP = 3.5
MONSTER_CHASE_COOLDOWN = 7.0
MONSTER_CHASE_TIRE_START = 6.0
MONSTER_CHASE_TIRE_RATE = 0.030
MONSTER_CHASE_TIRE_FLOOR = 0.75
MONSTER_LOCKER_CHECK_SECONDS = 1.3
MONSTER_HEARING_WALL_MUFFLE = 0.55
MONSTER_WINDOW_SIGHT_MULT = 0.6
MONSTER_LIT_LOCKER_DETECT_RANGE = 4.5

MONSTER_GLOW_HUNT_RANGE = 7.0
MONSTER_GLOW_HUNT_SECONDS = 0.35

MONSTER_GLOW_RANGE = 22.0
MONSTER_GLOW_SECONDS = 1.5
MONSTER_LIGHTER_GLOW_MULT = 0.45

MONSTER_BEAM_SPOT_RANGE = 9.0
MONSTER_BEAM_BACKTRACK = (0.55, 1.0)
MONSTER_SIGHT_MEMORY_SECONDS = 2.0
MONSTER_SEARCH_SPREAD = 1.3
MONSTER_SEARCH_MAX_RADIUS = 15.0
MONSTER_SEARCH_SECONDS = 20.0
MONSTER_SEARCH_HOPS = 24
MONSTER_SEARCH_MARK_RADIUS = 3
MONSTER_SEARCH_MIN_LEG = 4
MONSTER_SEARCH_ROOM_BONUS = 3.0
MONSTER_SEARCH_SEEN_PENALTY = 7.0
MONSTER_SEARCH_LEG_WEIGHT = 0.6
MONSTER_SEARCH_AHEAD_BONUS = 6.0
MONSTER_SEARCH_HERE_CHANCE = 0.45
MONSTER_SEARCH_TURN_BACK_CHANCE = 0.5
MONSTER_SEARCH_MODE_WEIGHT = {"here": 0.6, "ahead": 1.5, "back": -0.8}
MONSTER_DOOR_MEMORY_RANGE = 9.0
MONSTER_PATROL_OPENNESS = (1.0, 1.0, 1.0, 0.45, 0.30, 0.10, 0.05, 0.02, 0.0)
MONSTER_PATROL_SAMPLES = 48
MONSTER_PATROL_FAILS_BEFORE_LOCAL = 3
MONSTER_PATROL_PREFERRED_DIST = 14.0
MONSTER_PATROL_ROOM_BONUS = 1.35
MONSTER_PATROL_HAUNT_BONUS = 2.2
MONSTER_PATROL_HAUNT_RADIUS = 12.0
MONSTER_PATROL_HAUNT_FADE = 90.0
MONSTER_PATROL_EXIT_BONUS = 1.5
MONSTER_PATROL_EXIT_RADIUS = 7.0
MONSTER_DARK_PATROL = 1.8
MONSTER_DARK_SPAWN = 3.0
MONSTER_PATROL_RECENT = 6
MONSTER_PATROL_RECENT_PENALTY = 0.25
MONSTER_PATROL_BACKTRACK_PENALTY = 0.30
MONSTER_COVER_FULL_HEIGHT = 0.44
MONSTER_PARTIAL_COVER_MULT = 0.45
MONSTER_HUNT_SPEED_CAP_RATIO = 1.05
MONSTER_STALK_WAIT_MIN = 5.0
MONSTER_STALK_WAIT_MAX = 7.0
MONSTER_STALK_OPEN_SECONDS = 1.8
MONSTER_STALK_APPROACH_SPEED = 0.22
MONSTER_LOCKER_CLOSE_SECONDS = 0.9
MONSTER_LOCKER_RECHECK_COOLDOWN = 10.0
MONSTER_LOCKER_TARGET_TIMEOUT = 20.0

STALK_RELEASE_BASE = 0.35
STALK_RELEASE_SANITY_WEIGHT = 0.40
STALK_RELEASE_OVERUSE_WEIGHT = 0.30
STALK_RELEASE_OVERUSE_SATURATION = 6.0
STALK_RELEASE_MIN = 0.12
STALK_RELEASE_MAX = 0.72
STALK_RELEASE_SANITY_HIT = 40.0
STALK_RELEASE_SANITY_FLOOR = 3.0

MONSTER_PATROL_LOCKER_NOTICE_RANGE = 1.6
MONSTER_PATROL_LOCKER_CHECK_BASE = 0.015
MONSTER_PATROL_LOCKER_CHECK_OVERUSE_BONUS = 0.06

MONSTER_GUARD_RADIUS = 6.0
MONSTER_TELEPORT_DIST = 15.0
MONSTER_GUARD_LOCKER_CHECK_BASE = 0.06
MONSTER_GUARD_LOCKER_CHECK_OVERUSE_BONUS = 0.10

INVESTIGATE_LOCKER_EVENT_BASE_PROB = 0.10
INVESTIGATE_LOCKER_EVENT_OVERUSE_BONUS = 0.25
INVESTIGATE_LOCKER_EVENT_DECOY_RADIUS = 5.0
MONSTER_LOCKER_NOTICE_RANGE_STAND = 4.0
MONSTER_LOCKER_NOTICE_RANGE_CROUCH = 2.2

MONSTER_PROP_STUCK_TRIGGER = 0.25
MONSTER_PROP_IGNORE_DURATION = 1.2
MONSTER_PROP_DETOUR_SECONDS = 1.6
MONSTER_STUCK_NEAR_PLAYER_DIST = 3.0
MONSTER_TURN_PROBE_DURATION = 0.8
MONSTER_TURN_PROBE_RATE = 2.2
MONSTER_TEMP_BLOCK_DURATION = 4.0
MONSTER_NAV_STUCK_TIMEOUT = 4.0
MONSTER_PROGRESS_DIST = 1.0
MONSTER_PROGRESS_TIMEOUT = 5.0
MONSTER_UNWEDGE_SEARCH_CELLS = 40
MONSTER_WEDGED_TIMEOUT = 1.2
MONSTER_WALL_UNSTICK_PULL = 0.15

MONSTER_REACTION_DELAY_CHANCE = 0.12
MONSTER_REACTION_DELAY_MIN = 0.6
MONSTER_REACTION_DELAY_MAX = 1.6
MONSTER_INTERCEPT_DISTANCE = 2.5
MONSTER_INTERCEPT_CHANCE = 0.20

MONSTER_STUCK_CATCH_TIME = 0.5
MONSTER_STUCK_CATCH_RADIUS = 1.0

HALLUCINATION_SANITY_THRESHOLD = 0.40
HALLUCINATION_SECONDS_MIN = 1.5
HALLUCINATION_SECONDS_MAX = 45.0
HALLUCINATION_MIN_GAP = 1.5
HALLUCINATION_PULSE_MIN_LEN = 2.5
HALLUCINATION_PULSE_MAX_LEN = 4.0
HALLUCINATION_PULSE_FAR_DIST = 10.0
HALLUCINATION_PULSE_NEAR_DIST = 1.5
HALLUCINATION_DOOR_MIN_LEN = 1.5
HALLUCINATION_DOOR_MAX_LEN = 2.2
HALLUCINATION_DOOR_HIT_INTERVAL = 0.4
HALLUCINATION_EYES_MAX_DIST = 9.0
HALLUCINATION_EYES_LIGHT_THRESHOLD = 0.10
HALLUCINATION_EYES_HEIGHT = 0.62
HALLUCINATION_EYES_GAZE_SECONDS = 3.5
HALLUCINATION_EYES_GAZE_FOV = 0.22
HALLUCINATION_EYES_FADE_SECONDS = 0.5
HALLUCINATION_EYES_SCALE = 2.20
HALLUCINATION_EYES_BLINK_EVERY = 2.1
HALLUCINATION_EYES_BLINK_LEN = 0.13
HALLUCINATION_EYES_SWAY = 0.035

SANITY_PILL_DURATION = 45.0
SANITY_PILL_REGEN_MULT = 2.5
PILL_TRIP_FADE_IN_SECONDS = 2.5
PILL_TRIP_FADE_OUT_SECONDS = 14.0
PILL_TRIP_ROLL_DEGREES = 6.0
PILL_TRIP_FOV_DEGREES = 9.0
PILL_TRIP_CAM_LAG_SECONDS = 1.1

PILL_COMEDOWN_SECONDS = 22.0
PILL_COMEDOWN_SPEED_MULT = 0.88
PILL_COMEDOWN_CAM_LAG_FRAC = 0.3
PILL_COMEDOWN_SFX_MULT = 0.85

DREAD_RAMP_SECONDS = 420.0
SCARE_SECONDS_DARK = 26.0
SCARE_SECONDS_DARK_DREAD = 10.0
SCARE_SECONDS_LIT = 50.0
SCARE_SECONDS_LIT_DREAD = 14.0
SCARE_MIN_GAP = 12.0

COL_CEIL_TOP = (28, 25, 32)
COL_CEIL_BOT = (14, 13, 18)
COL_FLOOR_TOP = (48, 42, 36)
COL_FLOOR_BOT = (21, 18, 16)
COL_FOG = (4, 3, 5)
COL_UI_DIM = (120, 110, 110)
COL_TEXT = (200, 190, 180)

WALL_CELL_TONE_VAR = 0.07

WALL_BASE_COLORS = {
    WALL_CONCRETE: (92, 88, 80),
    WALL_TILE: (72, 96, 82),
    WALL_METAL: (68, 78, 94),
    WALL_BLOOD: (92, 52, 44),
    WALL_FENCE: (120, 122, 118),
    WALL_FOREST: (18, 24, 16),
    WALL_SHED: (92, 68, 44),
    WALL_BRICK: (104, 62, 48),
    WALL_WINDOW: (98, 90, 76),
    WALL_BARS: (64, 66, 70),
}

import os as _os
_ASSETS_DIR = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "assets")
FONT_PATH = _os.path.join(_ASSETS_DIR, "font.ttf")
NOTE_FONT_PATH = _os.path.join(_ASSETS_DIR, "note_font.ttf")

WALL_TONE_BASE = (84, 84, 80)
WALL_TONE_MIX = 0.35

GRIME_AMOUNT = 0.60
GRIME_SCALE = 1.60
GRIME_DEPTH = 0.32

DADO_AMOUNT = 0.0
DADO_SKIRT_TOP = 0.13
DADO_RAIL_Z = 0.95
DADO_RAIL_HALF = 0.035
DADO_COLOR = (58, 60, 57)
DADO_PANEL = 0.86

GLANCE_CHANCE = 0.40
GLANCE_RANGE = 2.8
GLANCE_COOLDOWN = 20.0
GLANCE_MIN_OFF_AXIS = 0.55
GLANCE_VISIBLE_ARC = 0.70
GLANCE_VISIBLE_RANGE = 3.2
GLANCE_APPROACH_MAX = 3.5
GLANCE_ARRIVE_DIST = 0.45
GLANCE_STOP = 0.40
GLANCE_TURN = 1.20
GLANCE_HOLD = 1.40
GLANCE_BACK = 0.90
GLANCE_HEAD_YAW = 1.70

YARD_FENCE_GAPS = 4

YARD_ZONE_SIZE = 15
YARD_ZONE_KIND_WEIGHT = {"open": 2.2, "forest": 1.8, "morgue_dock": 1.5, "alley": 1.4}
YARD_ZONE_KIND_REPEAT = 0.3
YARD_MAX_BUILDINGS = 4
YARD_ZONE_GRID = 4
YARD_W = YARD_H = 4 + YARD_ZONE_GRID * YARD_ZONE_SIZE
ZONE_NEIGHBOURS = {
    "plant": ("alley", "dump", "open"),
    "shed": ("alley", "open", "dump"),
    "tool_shed": ("alley", "open"),
    "storage": ("alley", "dump", "open"),
    "morgue_dock": ("alley", "open"),
    "chapel": ("open", "forest", "ruin"),
    "greenhouse": ("open", "pen"),
    "alley": ("dump", "ruin", "open"),
    "dump": ("alley", "ruin"),
    "forest": ("forest", "pen", "ruin", "open"),
    "pen": ("open", "forest"),
    "ruin": ("dump", "forest", "open", "alley"),
    "open": ("open", "pen", "forest", "ruin", "alley"),
}
ZONE_NEIGHBOUR_BONUS = 2.2
ZONE_AT_HATCH = {"morgue_dock": 9.0, "alley": 1.5}
TOTAL_KEYS = 2

DEBUG_W, DEBUG_H = 78, 47

DEFAULT_BINDINGS = {
    "forward": ("key", pygame.K_w),
    "back": ("key", pygame.K_s),
    "left": ("key", pygame.K_a),
    "right": ("key", pygame.K_d),
    "sprint": ("key", pygame.K_LSHIFT),
    "crouch": ("key", pygame.K_LCTRL),
    "flashlight": ("key", pygame.K_f),
    "interact": ("key", pygame.K_e),
    "alt_interact": ("key", pygame.K_r),
    "lean_left": ("mouse", pygame.BUTTON_LEFT),
    "lean_right": ("mouse", pygame.BUTTON_RIGHT),
    "map": ("key", pygame.K_TAB),
    "map_mode": ("mouse", pygame.BUTTON_RIGHT),
    "map_pencil": ("mouse", pygame.BUTTON_LEFT),
    "status": ("key", pygame.K_q),
}

BINDING_ORDER = ("forward", "back", "left", "right", "sprint", "crouch", "flashlight", "interact",
                  "alt_interact", "status", "lean_left", "lean_right", "map", "map_mode", "map_pencil")
SHARED_BINDINGS = tuple(frozenset((m, lean)) for m in ("map_mode", "map_pencil") for lean in ("lean_left", "lean_right"))

ROOM_WALL_TINT = {
    "ward":       (1.06, 1.04, 0.92),
    "bath":       (0.90, 1.02, 1.06),
    "morgue":     (0.84, 0.96, 1.02),
    "treatment":  (0.96, 1.02, 1.00),
    "nurse":      (1.02, 1.02, 0.96),
    "office":     (1.06, 1.00, 0.88),
    "archive":    (1.04, 0.96, 0.84),
    "pharmacy":   (0.94, 1.00, 1.04),
    "dayroom":    (1.08, 1.00, 0.86),
    "cafeteria":  (1.08, 1.02, 0.88),
    "cell":       (0.94, 0.86, 0.80),
    "storage":    (0.96, 0.92, 0.86),
    "laundry":    (0.92, 0.98, 1.00),
    "boiler":     (0.92, 0.82, 0.72),
    "pump":       (0.94, 0.90, 0.84),
    "workshop":   (0.98, 0.94, 0.86),
    "vent":       (0.88, 0.88, 0.86),
    "tech_corridor": (0.94, 0.94, 0.92),
    "corridor":   (1.0, 1.0, 1.0),
    "entrance":   (1.04, 1.02, 0.96),
    "exit":       (1.0, 1.0, 1.0),
}

ROOM_WALL_BIAS = {
    "ward": WALL_TILE,
    "office": WALL_CONCRETE,
    "morgue": WALL_BLOOD,
    "cafeteria": WALL_TILE,
    "boiler": WALL_METAL,
    "storage": WALL_CONCRETE,
    "cell": WALL_BLOOD,
    "plain": None,
    "entrance": WALL_CONCRETE,
    "corridor": None,
    "stairwell": WALL_METAL,
    "tech_corridor": WALL_METAL,
    "vent": WALL_METAL,
    "exit": WALL_METAL,
    "unlocker": WALL_METAL,
    "bath": WALL_TILE,
    "pharmacy": WALL_TILE,
    "archive": WALL_CONCRETE,
    "dayroom": WALL_TILE,
    "nurse": WALL_CONCRETE,
    "treatment": WALL_TILE,
    "workshop": WALL_METAL,
    "pump": WALL_METAL,
    "laundry": WALL_TILE,
    "link": None,
}


MAX_CORRIDOR_RUN = 3
SPINE_CORRIDORS = (6, 9)
LANDMARK_MIN_CELLS = 60
LANDMARK_EARLY_BOOST = 4.0
LANDMARK_EARLY_FRACTION = 0.4
ROOM_KIND_SOFT_CAP = {
    "cafeteria": 1, "morgue": 1, "boiler": 1, "entrance": 1, "stairwell": 2,
    "office": 3, "ward": 4, "storage": 2, "cell": 4, "plain": 1, "vent": 4,
    "bath": 2, "pharmacy": 1, "archive": 2, "dayroom": 1, "nurse": 2,
    "treatment": 2, "workshop": 2, "pump": 1, "laundry": 1,
}
ROOM_KIND_CAP_DEFAULT = 3
ROOM_KIND_OVER_CAP = 0.22

ANOMALY_ROLLS = (1, 2, 3)
ANOMALY_ROLL_WEIGHTS = (1, 2, 1)
ANOMALY_STAGE_BY_FLOOR = {
    "floor0": (0, 1, 4),
    "floor1": (1, 1, 4),
    "floor2": (0, 1, 2),
}
ANOMALY_DEFAULT_STAGE = 2
ANOMALY = {
    1: dict(key="routine",    lights=0.85, clutter=0.70, emergency=0.6,
            voice="unaware", caps={"morgue": 1}, bite=0.04, wing_kinds={},
            wing_mix=0.10),
    2: dict(key="conversion", lights=1.00, clutter=1.00, emergency=1.0,
            voice="mixed",   caps={"morgue": 3}, bite=0.22, wing_kinds={},
            wing_mix=0.45),
    3: dict(key="lockdown",   lights=1.15, clutter=1.40, emergency=1.6,
            voice="aware",   caps={"morgue": 4}, bite=0.40,
            wing_kinds={"stores": ("morgue",)}, blind_doors=1, barricades=3,
            wing_mix=1.00),
    4: dict(key="anomaly",    lights=1.25, clutter=1.60, emergency=2.0,
            voice="aware",   caps={"morgue": 6, "storage": 1}, bite=0.55,
            wing_kinds={"stores": ("morgue",), "plant": ("morgue",)},
            echo_room=True, blind_doors=3, barricades=2, wing_mix=1.45),
}
ANOMALY_ECHO_MIN_CELLS = 42
ANOMALY_BROKEN_MAX = 0.92
ANOMALY_EMERGENCY_MAX = 0.75


def anomaly_stage(roll, floor_key):
    add, lo, hi = ANOMALY_STAGE_BY_FLOOR.get(floor_key, (0, 1, 4))
    return max(lo, min(hi, roll + add))


def anomaly_of(stage):
    return ANOMALY.get(stage, ANOMALY[ANOMALY_DEFAULT_STAGE])

LINK_MAX_CELLS = 18
LINK_TURN_COST = 2.5
LINK_MAX_PER_FLOOR = 8
LINK_LAMP_EVERY = 5
LINK_DRAIN_EVERY = 9
LINK_CLUTTER_EVERY = 4
LINK_PIPE_RUN = (3, 6)
LINK_MAX_HUG = 5
LINK_CUTS_PER_ROOM = 3
LINK_MIN_SHORTCUT = 4
LINK_SHORTCUT_WEIGHT = 2.0

FLOOR_WINGS = {
    "upper": (
        dict(key="wards", material=WALL_TILE, light=(1.00, 0.88, 0.70), broken=0.45,
             kinds=("ward", "bath", "dayroom", "treatment", "nurse", "morgue")),
        dict(key="admin", material=WALL_CONCRETE, light=(0.86, 0.93, 1.00), broken=0.55,
             kinds=("office", "archive", "pharmacy", "nurse", "plain", "morgue")),
        dict(key="back", material=WALL_METAL, light=(0.78, 0.92, 0.80), broken=0.75,
             emergency=0.30, emergency_light=(0.26, 1.00, 0.44),
             kinds=("cafeteria", "storage", "plain", "morgue")),
    ),
    "basement": (
        dict(key="plant", material=WALL_METAL, light=(1.00, 0.72, 0.42), broken=0.50,
             kinds=("boiler", "pump", "workshop", "laundry")),
        dict(key="stores", material=WALL_CONCRETE, light=(0.95, 0.93, 0.88), broken=0.65,
             kinds=("storage", "archive", "plain")),
        dict(key="cells", material=WALL_BLOOD, light=(0.72, 0.82, 1.00), broken=0.80,
             emergency=0.35, emergency_light=(1.00, 0.19, 0.13),
             kinds=("cell", "morgue")),
    ),
}
EMERGENCY_STRENGTH = 0.26
EMERGENCY_PER_CELL = 0.022
EMERGENCY_MIN_GAP = 5
EMERGENCY_DARK_RADIUS = 5.5
WING_ANY_KINDS = ()
ROOM_NEIGHBOURS = {
    "ward": ("ward", "bath", "nurse", "treatment", "dayroom"),
    "bath": ("ward", "dayroom", "nurse"),
    "nurse": ("ward", "treatment", "pharmacy", "office", "bath"),
    "treatment": ("nurse", "ward", "pharmacy"),
    "dayroom": ("ward", "bath", "cafeteria"),
    "office": ("office", "archive", "nurse", "pharmacy", "entrance"),
    "archive": ("office", "archive", "storage"),
    "pharmacy": ("nurse", "office", "treatment"),
    "cafeteria": ("cafeteria", "dayroom", "storage", "entrance", "plain"),
    "morgue": ("morgue", "storage", "cell", "plain", "cafeteria", "ward",
               "office", "treatment", "bath", "archive"),
    "cell": ("cell", "morgue", "plain"),
    "boiler": ("pump", "workshop", "plain"),
    "pump": ("boiler", "workshop"),
    "workshop": ("pump", "boiler", "storage"),
    "laundry": ("storage", "bath", "plain"),
    "storage": ("storage", "archive", "workshop", "laundry"),
}
ROOM_NEIGHBOURS_ANY = ("plain",)
ROOM_NEIGHBOUR_BONUS = 2.5
ROOM_STRANGER_PENALTY = 0.4
WING_MIN_ROOMS = 5
WING_ALL_BY_FRACTION = 0.5
WING_LIGHT_BLEND = 0.85
WING_SWITCH_CHANCE = 0.45
WING_OVERSIZE_PUSH = 0.35
WING_KEEPS_OWN_SKIN = ("morgue", "cell", "boiler", "exit", "unlocker",
                       "stairwell", "entrance")

WINDOW_RUNS_PER_FLOOR = (3, 6)
OUTER_WINDOW_RUNS_PER_FLOOR = (3, 6)
OUTER_WINDOW_YARD_DEPTH = 7
OUTER_WINDOW_YARD_MARGIN = 2
OUTER_WINDOW_YARD_MIN_DEPTH = 3
OUTER_WINDOW_YARD_MIN_MARGIN = 1
OUTER_WINDOW_PANE = (2, 3)
OUTER_WINDOW_GAP = (1, 2)
OUTER_WINDOW_CORNER = 1
OUTER_WINDOW_CORNER_MIN_WALL = 9
OUTER_WINDOW_WIDE_WALL = 10
WINDOW_SILL_FRAC = 0.39
FOG_EXPONENT = 1.8
NO_GENERATED_WINDOWS = ("ward",)
OUTER_WINDOW_MIN_WALL = 6
OUTER_YARD_FENCE_INSET = 1
OUTER_YARD_PROP_CHANCE = 0.18
OUTER_YARD_MIN_PROPS = 2
OUTER_YARD_TREE_EVERY = 4
OUTER_YARD_TREE_GAP = 2.5
OUTER_YARD_AMBIENT = 0.24
OUTER_YARD_FOG_COLOR = (34, 37, 47)
OUTER_YARD_FOG_MULT = 2.5
OUTER_YARD_FOG_DIST = 14.0 / (OUTER_YARD_FOG_MULT ** (1.0 / FOG_EXPONENT))
OUTER_WINDOW_MOON = 0.22
WINDOW_RUN_CELLS = (1, 3)

PICKUP_DEAD_END_BONUS = 2.5
PICKUP_AFFINITY_STRENGTH = 1.0

TEMPLATE_ROOM_COUNT = (18, 26)
MENU_ROOM_COUNT = (2, 3)
ROOM_TOUCH_CONNECT_CHANCE = 0.35
SECOND_WAY_OUT_KINDS = ("unlocker",)
SECOND_WAY_OUT_MIN_ROOMS = 10
EXIT_MIN_HOPS = 5
BROKEN_LIGHT_CHANCE = 0.6
MENU_BROKEN_LIGHT_CHANCE = 0.1
MENU_CAM_NDC_X = 0.52
MENU_CAM_DIST = (2.4, 5.0)
MENU_CAM_SWAY = 0.20
MENU_CAM_DOLLY = 0.30
MENU_CAM_RATE = 0.13
PROP_FADE_BAND = 1.6
PROP_FADE_FRACTION = 0.30

ELEVATOR_FLAT_SHADE_DIST = 11.0
ELEVATOR_CABIN_STAND = 0.55
ELEVATOR_DOORWAY_STEP = 0.35
ELEVATOR_EXIT_STEP_DIST = 0.7
ELEVATOR_ARRIVE_SECONDS = 26.0
ELEVATOR_ARRIVE_LIGHT_COUNT = 3
ELEVATOR_GUARD_TIMEOUT_SECONDS = 30.0
ELEVATOR_RIDE_ENTER_SECONDS = 1.6
ELEVATOR_DOOR_SWING_SECONDS = 0.7
ELEVATOR_DESCEND_HOLD_SECONDS = 2.6
ELEVATOR_SWAP_FRAC = 0.45
ELEVATOR_SHAKE = 0.006
ELEVATOR_RIDE_EXIT_SECONDS = 1.0
ELEVATOR_CABIN_LIGHT_RADIUS = 1.8
ELEVATOR_CABIN_LIGHT_COLOR = (0.95, 0.92, 0.80)

HATCH_WALK_SPEED = 1.5
HATCH_OPEN_SECONDS = 1.4
HATCH_CLIMB_SECONDS = 2.2
HATCH_STEP_SECONDS = 0.45
HATCH_SWAP_FRAC = 0.58
HATCH_LOOK_PITCH = 1.15
HATCH_CLIMB_MOON = 0.45
HATCH_SKY_LIGHT_RADIUS = 2.1
HATCH_SKY_LIGHT_COLOR = (0.58, 0.70, 0.95)

HATCH_WHEEL_TURNS = 3.5
HATCH_WHEEL_EASE = 2.4
HATCH_CLIMB_RUNGS = 4.0
HATCH_CLIMB_RUNG_RISE = 0.035
HATCH_CLIMB_RUNG_SWAY = 0.022

HATCH_TURN_SECONDS = 16.0
HATCH_CREAK_CHANCE_PER_SEC = 0.10

FENCE_CUT_SECONDS = 5.0
CUTTERS_BREAK_CHANCE_BASE = 0.18
CUTTERS_BREAK_CHANCE_PROGRESS_SCALE = 0.20
CUTTERS_BREAK_MAX_PER_CUT = 2
CUTTERS_REPAIR_SECONDS = 3.0
CUTTERS_REPAIR_GRACE_SECONDS = 2.0
FENCE_ESCAPE_SECONDS = 2.4
FENCE_ESCAPE_DUCK = 0.42
FOREST_RUN_PERIOD = 18.0
FOREST_RUN_SPEED = 3.6
HAZE_COLOR = (0.30, 0.34, 0.38)
YARD_HAZE = 0.55
YARD_HAZE_HEIGHT = 0.55
FOREST_HAZE = 0.72
FOREST_HAZE_HEIGHT = 0.70
FOREST_RUN_CRAWL_SPEED = 1.15
FOREST_RUN_PICKUP = 1.6
FOREST_RUN_START = FOREST_RUN_PERIOD * 1.5
FOREST_RUN_LOOP = FOREST_RUN_PERIOD * 14.0
FOREST_ECHO_VANISH = 30.0
FOREST_ECHO_FADE = 10.0
FOREST_ECHO_ALPHA = 0.9
FOREST_RUN_AMBIENT = 0.13
FOREST_RUN_MOON = 0.55
FOREST_RUN_FOG_DIST = 120.0
FOREST_RUN_FOG_COLOR = (9, 12, 14)
WIN_FADE_SECONDS = 2.0

ANGEL_EVENT_CHANCE = 0.1
ANGEL_TRIGGER_PROGRESS = 0.55
ANGEL_HEIGHT = 24.0
ANGEL_NOTICE_SECONDS = 1.1
ANGEL_TURN_SECONDS = 1.4
ANGEL_LOOK_SECONDS = 0.7
ANGEL_BURN_SECONDS = 2.8
ANGEL_BURN_REACH = 2.2
ANGEL_BURN_WIDTH = 2.4
ANGEL_RAY_STRENGTH = 0.85
ANGEL_WHITEOUT_SECONDS = 0.6
ANGEL_END_FADE_SECONDS = 0.6
ANGEL_FLOOD_AMBIENT = 0.55
ANGEL_FLOOD_MOON = 1.0
ANGEL_FLOOD_FOG_COLOR = (255, 246, 214)
ANGEL_FLOOD_FOG_DIST = 90.0
ANGEL_EYE_COUNT = 60
ANGEL_WING_COUNT = 8
ANGEL_CANVAS_SIZE = 1024
ANGEL_WORLD_SIZE = 70.0

FLOOR_SPECS = [
    dict(
        key="floor0",
        title="floor0.title",
        collectible="fuse",
        collectible_label="floor0.collectible_label",
        panel="fuse_box",
        panel_label="floor0.panel_label",
        panel_room="unlocker",
        exit_prop="elevator",
        exit_label="floor0.exit_label",
        exit_room="exit",
        n_collectible=3,
        n_batteries=5,
        n_notes=7,
        room_count=(20, 26),
        min_cells=500,
        n_sanity_pills=1,
        n_lighters=1,
        n_maps=1,
        n_pencils=3,
        n_sheets=1,
        wall_bias=None,
        floor_theme="upper",
        speed_mult=1.0,
        vision_mult=1.0,
        grace=0.0,
        fog_color=COL_FOG,
        fog_dist=12.5,
        ambient_level=0.055,
        intro="floor0.intro",
        descend_text="floor0.descend_text",
    ),
    dict(
        key="floor1",
        title="floor1.title",
        collectible="valve_key",
        collectible_label="floor1.collectible_label",
        panel="valve_panel",
        panel_label="floor1.panel_label",
        panel_room="unlocker",
        exit_prop="hatch",
        exit_label="floor1.exit_label",
        exit_room="exit",
        n_collectible=3,
        n_batteries=4,
        n_notes=6,
        room_count=(28, 38),
        min_cells=500,
        n_maps=1,
        n_pencils=3,
        n_sheets=1,
        n_sanity_pills=1,
        wall_bias=WALL_BLOOD,
        floor_theme="basement",
        speed_mult=1.18,
        vision_mult=1.15,
        grace=0.0,
        fog_color=(9, 4, 4),
        fog_dist=9.5,
        ambient_level=0.045,
        intro="floor1.intro",
        descend_text="floor1.descend_text",
    ),
    dict(
        key="floor2",
        title="floor2.title",
        layout="yard",
        collectible="key",
        collectible_label="floor2.collectible_label",
        panel="shed_lock",
        panel_label="floor2.panel_label",
        panel_powered_text="floor2.panel_powered_text",
        exit_prop="fence_gap",
        exit_label="floor2.exit_label",
        exit_requires_item="cutters",
        exit_requires_label="floor2.exit_requires_label",
        exit_requires_item_label="floor2.exit_requires_item_label",
        n_collectible=TOTAL_KEYS,
        n_pencils=2,
        n_sheets=1,
        wall_bias=None,
        floor_theme="yard",
        speed_mult=1.1,
        vision_mult=1.3,
        hearing_mult=1.3,
        grace=0.0,
        fog_color=(10, 14, 9),
        fog_dist=14.0,
        ambient_level=0.24,
        moon_strength=0.25,
        ground_haze=YARD_HAZE,
        haze_height=YARD_HAZE_HEIGHT,
        vision_light_norm=0.7,
        intro="floor2.intro",
        descend_text=None,
    ),
]
