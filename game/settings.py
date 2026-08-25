import pygame

TITLE = "ПАЛАТА №9"
VERSION = "ALPHA_2"
AUTHOR = "Lonewolf239"

SCREEN_W, SCREEN_H = 1280, 720
FPS = 60

QUALITY_PRESETS = {
    "low":    {"low_res": (240, 135), "snap_res": 135.0, "shadow_lights": 1},
    "medium": {"low_res": (320, 180), "snap_res": 180.0, "shadow_lights": 2},
    "high":   {"low_res": (400, 225), "snap_res": 225.0, "shadow_lights": 3},
    "ultra":  {"low_res": (480, 270), "snap_res": 270.0, "shadow_lights": 3},
}
QUALITY_PRESET_ORDER = ("low", "medium", "high", "ultra")

MAZE_W, MAZE_H = 21, 21
WALL_HEIGHT = 1.15

FLOOR_CEILING_TEX_DENSITY = 2.5

FLOOR = 0
WALL_CONCRETE = 1
WALL_TILE = 2
WALL_METAL = 3
WALL_BLOOD = 4
WALL_FENCE = 5
WALL_FOREST = 6
WALL_SHED = 7
WALL_WINDOW = 8

WALL_HEIGHTS = {
    WALL_FENCE: 0.85,
    WALL_FOREST: 2.6,
    WALL_SHED: 1.05,
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

FLASHLIGHT_DRAIN = 2.0
FLASHLIGHT_LOW = 20.0
BATTERY_PICKUP_AMOUNT = 45.0

STAMINA_MAX = 100.0
STAMINA_DRAIN = 16.0
STAMINA_REGEN = 18.0
STAMINA_REGEN_STANDING = 26.0
STAMINA_EXHAUST_LOCKOUT = 18.0

SANITY_MAX = 100.0
SANITY_DARK_DRAIN = 0.55
SANITY_HIDE_DRAIN = 0.18
SANITY_MONSTER_DRAIN = 5.5
SANITY_REGEN = 1.1
SANITY_HIDE_CROUCH_MULT = 1.6
SANITY_HIDE_LIT_REGEN = 0.5

TOTAL_BATTERIES = 6
TOTAL_NOTES = 5
DOOR_BREAK_SECONDS = 2.2
DOOR_BREAK_TRIGGER_DIST = 0.6
INSTALL_HOLD_SECONDS = 1.1
NOTE_DISPLAY_SECONDS = 7.5
PEEK_HOLD_SECONDS = 0.35
PEEK_TRANSITION_SECONDS = 0.25
PEEK_FOV_DEGREES = 65.0
DOOR_KEYHOLE_LOCAL_Y = -0.4
DOOR_KEYHOLE_LOCAL_Z = 0.44

MIC_LEVEL_SCALE = 7.0
MIC_NOISE_GATE = 0.05
MIC_VU_ATTACK_RATE = 26.0
MIC_VU_RELEASE_RATE = 9.0

MONSTER_BASE_SPEED = 1.55
MONSTER_HUNT_SPEED = 2.85
MONSTER_VISION_RANGE = 5.8
MONSTER_VISION_RANGE_LIT = 8.3
MONSTER_HEARING_RANGE = 4.0
MONSTER_HEARING_RANGE_SPRINT = 7.0
MONSTER_GROWL_FALLOFF = 12.0
MONSTER_CATCH_RADIUS = 0.62
MONSTER_RADIUS = 0.24
MONSTER_REPLAN_INTERVAL = 0.6
MONSTER_LOSE_INTEREST_TIME = 4.0
MONSTER_LOCKER_CHECK_SECONDS = 1.3
MONSTER_HEARING_WALL_MUFFLE = 0.5
MONSTER_PROXIMITY_RANGE = 1.2
MONSTER_LIT_LOCKER_DETECT_RANGE = 4.5
MONSTER_SIGHT_MEMORY_SECONDS = 2.0
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

MONSTER_PATROL_LOCKER_NOTICE_RANGE = 1.6
MONSTER_PATROL_LOCKER_CHECK_BASE = 0.015
MONSTER_PATROL_LOCKER_CHECK_OVERUSE_BONUS = 0.06

INVESTIGATE_LOCKER_EVENT_BASE_PROB = 0.10
INVESTIGATE_LOCKER_EVENT_OVERUSE_BONUS = 0.25
INVESTIGATE_LOCKER_EVENT_DECOY_RADIUS = 5.0
MONSTER_LOCKER_NOTICE_RANGE_STAND = 4.0
MONSTER_LOCKER_NOTICE_RANGE_CROUCH = 2.2

MONSTER_PROP_STUCK_TRIGGER = 0.25
MONSTER_PROP_IGNORE_DURATION = 1.2
MONSTER_STUCK_NEAR_PLAYER_DIST = 3.0
MONSTER_TURN_PROBE_DURATION = 0.8
MONSTER_TURN_PROBE_RATE = 2.2
MONSTER_TEMP_BLOCK_DURATION = 4.0
MONSTER_NAV_STUCK_TIMEOUT = 4.0

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

SANITY_PILL_DURATION = 30.0
SANITY_PILL_REGEN_MULT = 2.5

DREAD_RAMP_SECONDS = 420.0

COL_CEIL_TOP = (28, 25, 32)
COL_CEIL_BOT = (14, 13, 18)
COL_FLOOR_TOP = (48, 42, 36)
COL_FLOOR_BOT = (21, 18, 16)
COL_FOG = (4, 3, 5)
COL_UI_DIM = (120, 110, 110)
COL_TEXT = (200, 190, 180)

WALL_BASE_COLORS = {
    WALL_CONCRETE: (86, 82, 78),
    WALL_TILE: (63, 78, 68),
    WALL_METAL: (70, 76, 86),
    WALL_BLOOD: (74, 46, 42),
    WALL_FENCE: (120, 122, 118),
    WALL_FOREST: (18, 24, 16),
    WALL_SHED: (92, 68, 44),
    WALL_WINDOW: (60, 55, 46),
}

YARD_ZONE_SIZE = 15
YARD_ZONE_GRID = 3
YARD_W = YARD_H = 4 + YARD_ZONE_GRID * YARD_ZONE_SIZE
TOTAL_KEYS = 2

DEBUG_W, DEBUG_H = 39, 21

DEFAULT_BINDINGS = {
    "forward": pygame.K_w,
    "back": pygame.K_s,
    "left": pygame.K_a,
    "right": pygame.K_d,
    "sprint": pygame.K_LSHIFT,
    "crouch": pygame.K_LCTRL,
    "flashlight": pygame.K_f,
    "interact": pygame.K_e,
}

BINDING_ORDER = ("forward", "back", "left", "right", "sprint", "crouch", "flashlight", "interact")

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
}


TEMPLATE_ROOM_COUNT = (18, 26)
MENU_ROOM_COUNT = (2, 3)
ROOM_TOUCH_CONNECT_CHANCE = 0.35
BROKEN_LIGHT_CHANCE = 0.4

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
        n_batteries=10,
        n_notes=7,
        n_sanity_pills=1,
        wall_bias=None,
        floor_theme="upper",
        speed_mult=1.0,
        vision_mult=1.0,
        grace=0.0,
        fog_color=COL_FOG,
        fog_dist=12.5,
        ambient_level=0.045,
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
        n_batteries=8,
        n_notes=6,
        n_sanity_pills=1,
        wall_bias=WALL_BLOOD,
        floor_theme="basement",
        speed_mult=1.18,
        vision_mult=1.15,
        grace=0.0,
        fog_color=(9, 4, 4),
        fog_dist=9.5,
        ambient_level=0.03,
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
        wall_bias=None,
        floor_theme="yard",
        speed_mult=1.1,
        vision_mult=1.3,
        grace=0.0,
        fog_color=(10, 14, 9),
        fog_dist=8.5,
        ambient_level=0.16,
        moon_strength=0.15,
        intro="floor2.intro",
        descend_text=None,
    ),
]
