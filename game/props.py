import math
import random

from game import settings as S
from game.lighting import LIGHT_EPOCH
from game.mesh_tops import MESH_TOP_FRAC

NOTE_POOL = {
    "floor0": [f"note.floor0.{i}" for i in range(16)],
    "floor1": [f"note.floor1.{i}" for i in range(15)],
    "floor2": [f"note.floor2.{i}" for i in range(10)],
}

NOTE_PLACES = {
    "note.floor0.0": ("office", "nurse", "corridor", "dayroom"),
    "note.floor0.1": ("nurse", "office", "archive"),
    "note.floor0.2": ("plain", "entrance", "exit", "bath"),
    "note.floor0.3": ("ward", "nurse", "treatment", "bath"),
    "note.floor0.4": ("corridor", "nurse", "office", "entrance"),
    "note.floor0.5": ("bath", "cafeteria", "dayroom", "corridor", "morgue"),
    "note.floor0.6": ("archive", "office"),
    "note.floor0.7": ("nurse", "cafeteria", "dayroom"),
    "note.floor0.8": ("ward", "nurse", "morgue"),
    "note.floor0.9": ("ward", "dayroom", "treatment", "exit"),
    "note.floor0.10": ("pharmacy", "treatment", "nurse"),
    "note.floor0.11": ("ward", "nurse", "archive", "corridor"),
    "note.floor0.12": ("cafeteria", "dayroom", "bath", "nurse", "morgue"),
    "note.floor0.13": ("office", "archive", "pharmacy", "treatment"),
    "note.floor0.14": ("morgue", "ward", "nurse", "plain"),
    "note.floor0.15": ("cafeteria", "dayroom", "plain", "storage"),
    "note.floor1.0": ("tech_corridor", "vent", "cell", "plain", "morgue"),
    "note.floor1.1": ("pump", "boiler", "cell"),
    "note.floor1.2": ("boiler", "workshop"),
    "note.floor1.3": ("boiler", "pump", "tech_corridor", "cell"),
    "note.floor1.4": ("tech_corridor", "vent", "stairwell"),
    "note.floor1.5": ("storage", "archive"),
    "note.floor1.6": ("storage", "workshop", "pump"),
    "note.floor1.7": ("cell", "workshop", "tech_corridor", "archive"),
    "note.floor1.8": ("vent", "tech_corridor"),
    "note.floor1.9": ("vent", "cell", "laundry", "morgue"),
    "note.floor1.10": ("morgue", "archive", "storage"),
    "note.floor1.11": ("laundry", "storage", "plain", "cell"),
    "note.floor1.12": ("morgue", "archive", "storage"),
    "note.floor1.13": ("morgue", "cell"),
    "note.floor1.14": ("cell", "plain", "archive"),
    "note.floor2.0": ("forest", "alley", "pen"),
    "note.floor2.1": ("shed", "tool_shed", "storage"),
    "note.floor2.2": ("forest", "alley"),
    "note.floor2.3": ("forest", "pen", "open"),
    "note.floor2.4": ("alley", "open", "ruin", "morgue_dock"),
    "note.floor2.5": ("forest", "pen", "dump"),
    "note.floor2.6": ("tool_shed", "shed", "storage"),
    "note.floor2.7": ("open", "alley", "ruin", "dump", "morgue_dock"),
    "note.floor2.8": ("chapel", "morgue_dock", "ruin"),
    "note.floor2.9": ("greenhouse", "plant", "dump"),
}


NOTE_AWARE = {
    "note.floor0.1", "note.floor0.2", "note.floor0.5", "note.floor0.7",
    "note.floor0.11", "note.floor0.12", "note.floor0.13", "note.floor0.14",
    "note.floor1.12", "note.floor1.13",
    "note.floor1.0", "note.floor1.1", "note.floor1.4", "note.floor1.6",
    "note.floor1.8", "note.floor1.9",
    "note.floor2.3", "note.floor2.5", "note.floor2.6", "note.floor2.8",
    "note.floor2.9",
}


def _notes_of_voice(pool, voice, want, rng):
    if voice == "mixed":
        return list(pool)
    aware = voice == "aware"
    first = [k for k in pool if (k in NOTE_AWARE) == aware]
    rest = [k for k in pool if (k in NOTE_AWARE) != aware]
    rng.shuffle(rest)
    return first + rest[:max(0, want - len(first))]


def _notes_in_place(pool, kinds, rng):
    pool = list(pool)
    rng.shuffle(pool)
    n = len(kinds)
    cand = [[j for j, key in enumerate(pool) if kinds[i] in NOTE_PLACES.get(key, ())]
            for i in range(n)]
    holder = [-1] * len(pool)

    def take(i, seen):
        for j in cand[i]:
            if j in seen:
                continue
            seen.add(j)
            if holder[j] < 0 or take(holder[j], seen):
                holder[j] = i
                return True
        return False

    for i in range(n):
        take(i, set())
    out = [None] * n
    for j, i in enumerate(holder):
        if i >= 0:
            out[i] = pool[j]
    spare = [key for j, key in enumerate(pool) if holder[j] < 0]
    for i in range(n):
        if out[i] is None:
            out[i] = spare.pop() if spare else pool[i % len(pool)]
    return out


PROP_DEFS = {
    "bed":         dict(hw=0.46, hd=0.24, height=0.38, color=(200, 196, 188), solid=True,  wall_mounted=False),
    "desk":        dict(hw=0.34, hd=0.20, height=0.36, color=(92, 70, 48),    solid=True,  wall_mounted=False, texture="wood"),
    "table":       dict(hw=0.42, hd=0.42, height=0.36, color=(100, 84, 60),   solid=True,  wall_mounted=False, texture="wood"),
    "shelf":       dict(hw=0.32, hd=0.16, height=0.88, color=(96, 72, 42),    solid=True,  wall_mounted=True,  texture="wood",
                         collide_hw=0.285, collide_hd=0.15),
    "gurney":      dict(hw=0.44, hd=0.20, height=0.42, color=(146, 148, 150), solid=True,  wall_mounted=False, texture="metal"),
    "locker":      dict(hw=0.26, hd=0.28, height=0.98, color=(48, 92, 108),   solid=True,  wall_mounted=True,  texture="metal",
                         interactable="locker"),
    "note_flat":   dict(hw=0.17, hd=0.13, height=0.016, color=(150, 142, 120), solid=False, wall_mounted=False,
                         texture="paper", interactable="note"),
    "battery":     dict(hw=0.09, hd=0.07, height=0.14, color=(110, 205, 135), solid=False, wall_mounted=False,
                         texture="metal", interactable="pickup"),
    "fuse":        dict(hw=0.10, hd=0.10, height=0.075, color=(255, 255, 255),  solid=False, wall_mounted=False,
                         texture="metal", interactable="pickup"),
    "valve_key":   dict(hw=0.09, hd=0.09, height=0.18, color=(90, 195, 195),  solid=False, wall_mounted=False,
                         texture="metal", interactable="pickup"),
    "sanity_pill": dict(hw=0.07, hd=0.07, height=0.10, color=(255, 255, 255), solid=False, wall_mounted=False,
                         texture="metal", interactable="pickup"),
    "lighter":     dict(hw=0.06, hd=0.03, height=0.09, color=(255, 255, 255), solid=False, wall_mounted=False,
                         texture="metal", interactable="pickup"),
    "paper_map":   dict(hw=0.11, hd=0.08, height=0.03, color=(210, 200, 170), solid=False, wall_mounted=False,
                         interactable="pickup"),
    "pencil":      dict(hw=0.10, hd=0.02, height=0.02, color=(220, 180, 60),  solid=False, wall_mounted=False,
                         interactable="pickup"),
    "map_sheet":   dict(hw=0.10, hd=0.075, height=0.006, color=(218, 209, 184), solid=False, wall_mounted=False,
                         interactable="pickup"),
    "fuse_box":    dict(hw=0.28, hd=0.16, height=0.58, color=(196, 164, 70),   solid=True,  wall_mounted=True,  texture="metal",
                         interactable="panel", collide_hd=0.124, collide_off=-0.036),
    "valve_panel": dict(hw=0.30, hd=0.18, height=0.62, color=(210, 205, 196),   solid=True,  wall_mounted=True,  texture="metal",
                         interactable="panel", collide_hd=0.129, collide_off=-0.051),
    "elevator":    dict(hw=0.49, hd=0.05, height=1.12, color=(46, 110, 88),   solid=True,  wall_mounted=True,  texture="metal",
                         interactable="exit", light_lobe=(1.0, 0.0, -0.2, -0.1, 0.5, 0.0)),
    "elevator_arrival": dict(hw=0.49, hd=0.05, height=1.12, color=(46, 110, 88), solid=True, wall_mounted=True, texture="metal",
                              light_lobe=(1.0, 0.0, -0.2, -0.1, 0.5, 0.0)),
    "hatch":       dict(hw=0.34, hd=0.34, height=0.18, color=(255, 255, 255),   solid=False, wall_mounted=False, texture="metal",
                         interactable="exit", z0=S.WALL_HEIGHT - 0.18),
    "hatch_arrival": dict(hw=0.34, hd=0.34, height=0.18, color=(255, 255, 255), solid=True, wall_mounted=False,
                          texture="metal", collide_hw=0.30, collide_hd=0.30),
    "asylum_echo": dict(hw=19.0, hd=3.5, height=11.0, color=(255, 255, 255), solid=False, wall_mounted=False),
    "crate":       dict(hw=0.26, hd=0.26, height=0.34, color=(200, 170, 130),   solid=True,  wall_mounted=False, texture="wood"),
    "barrel":      dict(hw=0.20, hd=0.20, height=0.44, color=(170, 170, 170), solid=True,  wall_mounted=False),
    "pipes":       dict(hw=0.40, hd=0.14, height=0.72, color=(150, 150, 150), solid=False, wall_mounted=True),
    "chair":       dict(hw=0.15, hd=0.15, height=0.44, color=(88, 66, 44),    solid=True,  wall_mounted=False, texture="wood"),
    "cabinet":     dict(hw=0.30, hd=0.18, height=0.64, color=(176, 178, 172),    solid=True,  wall_mounted=True,  texture="metal"),
    "sink":        dict(hw=0.24, hd=0.16, height=0.34, color=(152, 152, 148), solid=True,  wall_mounted=True),
    "trash_can":   dict(hw=0.14, hd=0.14, height=0.30, color=(230, 232, 230),    solid=True,  wall_mounted=False, texture="metal"),
    "vending":     dict(hw=0.26, hd=0.20, height=0.86, color=(58, 92, 100),   solid=True,  wall_mounted=True,  texture="metal"),
    "clutter_papers": dict(hw=0.09, hd=0.07, height=0.05, color=(140, 133, 116), solid=False, wall_mounted=False,
                            texture="paper"),
    "clutter_bottle":  dict(hw=0.045, hd=0.045, height=0.17, color=(230, 235, 230), solid=False, wall_mounted=False),
    "clutter_junk":    dict(hw=0.15, hd=0.15, height=0.10, color=(92, 86, 76),   solid=False, wall_mounted=False,
                             texture="metal"),
    "lamp_desk":   dict(hw=0.07, hd=0.07, height=0.20, color=(255, 214, 150), solid=False, wall_mounted=False,
                         emissive=True, light_radius=2.7, light_color=(1.0, 0.80, 0.48), flicker=True,
                         light_lobe=(0.0, 0.0, -1.0, -0.55, 0.05, 0.18)),
    "sign_exit":   dict(hw=0.15, hd=0.035, height=0.11, color=(60, 230, 100), solid=False, wall_mounted=True,
                         emissive=True, light_radius=2.0, light_color=(0.35, 1.0, 0.5), z0=0.85,
                         light_lobe=(1.0, 0.0, 0.0, -0.3, 0.35, 0.0)),
    "wall_sconce": dict(hw=0.09, hd=0.07, height=0.16, color=(255, 205, 135), solid=False, wall_mounted=True,
                         emissive=True, light_radius=3.0, light_color=(1.0, 0.76, 0.43), z0=0.55,
                         light_lobe=(1.0, 0.0, 0.0, -0.5, 0.1, 0.0)),
    "barricade":   dict(hw=0.44, hd=0.34, height=0.24, color=(126, 104, 74), solid=False,
                         wall_mounted=False, texture="wood", variants=3),
    "emergency_lamp": dict(hw=0.10, hd=0.07, height=0.17, color=(255, 74, 60), solid=False, wall_mounted=True,
                         emissive=True, light_radius=1.35,
                         light_color=(1.0 * S.EMERGENCY_STRENGTH, 0.20 * S.EMERGENCY_STRENGTH,
                                       0.14 * S.EMERGENCY_STRENGTH), z0=0.62,
                         light_lobe=(1.0, 0.0, 0.0, -0.35, 0.15, 0.0)),
    "monitor":     dict(hw=0.14, hd=0.10, height=0.16, color=(255, 255, 255), solid=False, wall_mounted=False,
                         emissive=True, light_radius=1.7, light_color=(0.55, 0.76, 1.0), flicker=True,
                         light_lobe=(1.0, 0.0, 0.0, 0.0, 1.0, 0.03)),
    "tree":        dict(hw=0.18, hd=0.18, height=2.2,  color=(255, 255, 255), solid=True,  wall_mounted=False,
                         collide_hw=0.075, collide_hd=0.075, variants=4),
    "bush":        dict(hw=0.34, hd=0.34, height=0.48, color=(64, 96, 50),    solid=False, wall_mounted=False,
                         variants=4, hides=True),
    "rock":        dict(hw=0.22, hd=0.20, height=0.26, color=(112, 108, 100), solid=False,  wall_mounted=False,
                         variants=4),
    "whetstone":   dict(hw=0.055, hd=0.022, height=0.024, color=(150, 146, 140), solid=False,
                         wall_mounted=False, texture="metal"),
    "padlock_dropped": dict(hw=0.055, hd=0.045, height=0.10, color=(150, 148, 150), solid=False,
                         wall_mounted=False, texture="metal"),
    "shed_lock":   dict(hw=0.48, hd=0.10, height=0.82, color=(90, 86, 80),    solid=True,  wall_mounted=False, texture="metal",
                         interactable="panel"),
    "fence_gap":   dict(hw=0.50, hd=0.05, height=0.85, color=(255, 255, 255), solid=True,  wall_mounted=True,
                         interactable="exit"),
    "cutters":     dict(hw=0.10, hd=0.05, height=0.14, color=(255, 255, 255),   solid=False, wall_mounted=False,
                         texture="metal", interactable="pickup"),
    "key":         dict(hw=0.06, hd=0.06, height=0.10, color=(212, 182, 60),  solid=False, wall_mounted=False,
                         texture="metal", interactable="pickup"),
    "portal":      dict(hw=0.32, hd=0.10, height=0.9,  color=(150, 90, 230),  solid=False, wall_mounted=False,
                         interactable="portal", emissive=True),
    "portal_live": dict(hw=0.32, hd=0.10, height=0.9,  color=(220, 60, 55),   solid=False, wall_mounted=False,
                         interactable="portal", emissive=True),
    "mortuary_wall": dict(hw=0.48, hd=0.14, height=0.90, color=(255, 255, 255), solid=False, wall_mounted=True,
                           texture="metal"),
    "autopsy_table": dict(hw=0.44, hd=0.20, height=0.38, color=(255, 255, 255), solid=True, wall_mounted=False,
                           texture="metal"),
    "instrument_tray": dict(hw=0.12, hd=0.08, height=0.05, color=(255, 255, 255), solid=False, wall_mounted=False,
                             texture="metal"),
    "floor_drain":   dict(hw=0.10, hd=0.10, height=0.02, color=(255, 255, 255), solid=False, wall_mounted=False,
                           texture="metal"),
    "boiler_tank":   dict(hw=0.42, hd=0.42, height=0.95, color=(255, 255, 255), solid=True, wall_mounted=False,
                           texture="metal", collide_hw=0.38, collide_hd=0.38, emissive=True, light_radius=1.2, light_color=(1.0, 0.55, 0.25),
                           flicker=True, light_at=(0.37, 0.0, 0.29), light_lobe=(1.0, 0.0, -0.35, -0.05, 0.6, 0.0)),
    "gauge_panel":   dict(hw=0.26, hd=0.14, height=0.42, color=(255, 255, 255), solid=False, wall_mounted=True,
                           texture="metal"),
    "kitchen_counter": dict(hw=0.42, hd=0.36, height=0.54, color=(255, 255, 255), solid=True, wall_mounted=False, texture="metal"),
    "fridge_freezer":  dict(hw=0.30, hd=0.24, height=0.86, color=(255, 255, 255), solid=True, wall_mounted=True,
                             texture="metal"),
    "tray_stack":      dict(hw=0.10, hd=0.10, height=0.10, color=(255, 255, 255), solid=False, wall_mounted=False,
                             texture="metal"),
    "workbench":     dict(hw=0.44, hd=0.26, height=0.42, color=(255, 255, 255), solid=True, wall_mounted=False,
                           texture="wood", interactable="workbench", collide_hd=0.285, collide_off=0.025),
    "tool_pegboard": dict(hw=0.42, hd=0.10, height=0.86, color=(255, 255, 255), solid=False, wall_mounted=True, texture="wood"),
    "reception_desk": dict(hw=0.50, hd=0.24, height=0.42, color=(255, 255, 255), solid=True, wall_mounted=False,
                            texture="wood"),
    "fire_extinguisher": dict(hw=0.06, hd=0.06, height=0.28, color=(255, 255, 255), solid=False, wall_mounted=True, z0=0.45),
    "iv_stand":  dict(hw=0.07, hd=0.07, height=0.85, color=(255, 255, 255), solid=True, wall_mounted=False,
                       texture="metal", collide_hw=0.115, collide_hd=0.115),
    "wheelchair":  dict(hw=0.16, hd=0.20, height=0.53, color=(255, 255, 255), solid=True, wall_mounted=False, texture="metal",
                         collide_hd=0.20, collide_off=-0.025),
    "fallen_log": dict(hw=0.50, hd=0.22, height=0.28, color=(255, 255, 255), solid=True, wall_mounted=False,
                       variants=3, collide_hw=0.50, collide_hd=0.15),
    "tree_stump":  dict(hw=0.20, hd=0.20, height=0.22, color=(255, 255, 255), solid=True, wall_mounted=False, texture="wood",
                        collide_hw=0.16, collide_hd=0.16, variants=3),
    "park_bench":  dict(hw=0.40, hd=0.16, height=0.80, color=(255, 255, 255), solid=True, wall_mounted=False, texture="wood",
                        collide_hd=0.154, collide_off=-0.024),
    "lamppost":   dict(hw=0.08, hd=0.08, height=1.6,  color=(255, 255, 255), solid=True, wall_mounted=False,
                        texture="metal", collide_hw=0.065, collide_hd=0.065,
                        emissive=True, light_radius=3.2, light_color=(1.0, 0.82, 0.55), flicker=True,
                        light_lobe=(0.0, 0.0, -1.0, -0.6, -0.1, 0.2)),
}

SURFACE_KINDS = {"desk", "table", "shelf", "gurney", "autopsy_table", "kitchen_counter", "workbench",
                  "reception_desk", "crate", "cabinet", "barrel",
                  "tree_stump", "park_bench"}

SHOWCASE_ITEMS = ("instrument_tray", "lamp_desk", "monitor", "tray_stack")

SURFACE_TOP_FRAC = {"desk": 1.0, "table": 1.0, "shelf": 0.765, "gurney": 1.0,
                     "autopsy_table": 0.964,
                     "kitchen_counter": 0.76, "workbench": 1.0, "reception_desk": 1.0,
                     "crate": 1.0,
                     "cabinet": 1.0,
                     "barrel": 1.0,
                     "tree_stump": (1.0, 0.795, 1.0),
                     "park_bench": 0.27}

SURFACE_SPOT_FRAC = {"barrel": 0.85,
                     "tree_stump": (0.45, 0.65),
                      "workbench": 0.20}

SHELF_LEVEL_FRACS = (0.085, 0.425, 0.765)


def surface_top_frac(kind, variant=0):
    frac = SURFACE_TOP_FRAC[kind]
    if isinstance(frac, tuple):
        return frac[variant % len(frac)]
    return frac


def _surface_top_z0(surf, rng=None):
    base = getattr(surf, "z0", 0.0)
    if surf.kind == "shelf" and rng is not None:
        return base + surf.height * rng.choice(SHELF_LEVEL_FRACS)
    return base + surf.height * surface_top_frac(surf.kind, getattr(surf, "variant", 0))

HAND_FURNITURE_KINDS = {
    "bed", "desk", "table", "shelf", "gurney", "crate", "barrel", "pipes",
    "chair", "cabinet", "sink", "trash_can", "vending", "locker",
    "lamp_desk", "wall_sconce", "sign_exit", "monitor", "tree", "bush", "rock",
    "mortuary_wall", "autopsy_table", "instrument_tray",
    "floor_drain", "boiler_tank", "gauge_panel",
    "kitchen_counter", "fridge_freezer", "tray_stack",
    "workbench", "tool_pegboard", "reception_desk", "fire_extinguisher",
    "iv_stand", "wheelchair", "fallen_log", "tree_stump", "park_bench", "lamppost",
}

HAND_FURNITURE_BY_KIND = {
    "ward": {"bed", "shelf", "chair", "cabinet", "locker", "lamp_desk", "wall_sconce",
             "iv_stand", "wheelchair"},
    "office": {"desk", "shelf", "chair", "cabinet", "locker", "table", "lamp_desk", "monitor", "wall_sconce"},
    "morgue": {"gurney", "shelf", "cabinet", "locker", "wall_sconce",
               "mortuary_wall", "autopsy_table", "instrument_tray", "sink"},
    "cafeteria": {"table", "chair", "trash_can", "vending", "wall_sconce", "locker",
                  "kitchen_counter", "fridge_freezer", "tray_stack"},
    "plain": {"shelf", "chair", "crate", "locker", "wall_sconce"},
    "stairwell": {"shelf", "cabinet", "locker", "wall_sconce", "fire_extinguisher"},
    "boiler": {"barrel", "crate", "pipes", "shelf", "wall_sconce", "locker",
               "boiler_tank", "gauge_panel"},
    "storage": {"crate", "shelf", "pipes", "cabinet", "barrel", "locker", "wall_sconce"},
    "cell": {"gurney", "shelf", "pipes", "trash_can", "sink", "locker", "wall_sconce",
             "floor_drain"},
    "exit": {"shelf", "cabinet", "wall_sconce", "sign_exit", "locker"},
    "unlocker": {"shelf", "cabinet", "pipes", "crate", "wall_sconce", "locker"},
    "corridor": {"wall_sconce", "locker", "fire_extinguisher"},
    "tech_corridor": {"pipes", "wall_sconce", "locker"},
    "vent": {"wall_sconce", "locker"},
    "entrance": {"shelf", "chair", "trash_can", "locker", "wall_sconce", "reception_desk", "monitor"},
    "bath": {"sink", "floor_drain", "trash_can", "locker", "wall_sconce", "shelf", "pipes"},
    "pharmacy": {"cabinet", "shelf", "desk", "chair", "monitor", "locker", "wall_sconce", "table"},
    "archive": {"shelf", "cabinet", "crate", "desk", "chair", "lamp_desk", "wall_sconce", "locker"},
    "dayroom": {"table", "chair", "vending", "monitor", "trash_can", "shelf", "wall_sconce", "locker", "bed"},
    "nurse": {"reception_desk", "desk", "chair", "monitor", "cabinet", "shelf", "locker", "wall_sconce"},
    "treatment": {"gurney", "iv_stand", "instrument_tray", "cabinet", "sink", "wheelchair",
                  "shelf", "locker", "wall_sconce", "autopsy_table"},
    "workshop": {"workbench", "tool_pegboard", "shelf", "crate", "barrel", "locker",
                 "wall_sconce", "pipes", "cabinet"},
    "pump": {"boiler_tank", "gauge_panel", "pipes", "barrel", "floor_drain", "crate",
             "wall_sconce", "shelf"},
    "laundry": {"sink", "crate", "barrel", "shelf", "pipes", "trash_can", "locker", "wall_sconce"},
    "link": {"pipes", "emergency_lamp", "locker", "crate"},
}

ZONE_HAND_FURNITURE_BY_KIND = {
    "open": {"tree", "bush", "rock", "crate", "barrel", "lamppost", "park_bench", "tree_stump", "fallen_log"},
    "shed": {"crate", "barrel", "wall_sconce", "workbench", "tool_pegboard"},
    "tool_shed": {"crate", "barrel", "shelf", "wall_sconce", "workbench", "tool_pegboard"},
    "storage": {"crate", "barrel", "shelf", "locker", "wall_sconce"},
    "forest": {"tree", "bush", "rock", "fallen_log", "tree_stump"},
    "alley": {"tree", "bush", "park_bench", "lamppost", "rock", "trash_can"},
    "greenhouse": {"shelf", "bush", "crate", "sink", "wall_sconce", "tree", "rock", "barrel", "table"},
    "chapel": {"park_bench", "table", "wall_sconce", "trash_can", "bush", "tree", "rock", "shelf"},
    "plant": {"boiler_tank", "gauge_panel", "pipes", "barrel", "crate", "wall_sconce", "bush", "rock", "shelf"},
    "morgue_dock": {"mortuary_wall", "gurney", "instrument_tray", "sink", "floor_drain",
                    "wall_sconce", "crate", "bush", "tree", "rock"},
    "pen": {"park_bench", "bush", "rock", "lamppost", "tree", "trash_can"},
    "ruin": {"rock", "crate", "barrel", "bush", "tree", "fallen_log", "tree_stump"},
    "dump": {"barrel", "crate", "trash_can", "rock", "bush", "tree"},
}


def variant_for_position(x, y, count):
    if count <= 1:
        return 0
    h = (int(round(x * 64.0)) * 0x9E3779B1) ^ (int(round(y * 64.0)) * 0x85EBCA77)
    h &= 0xFFFFFFFF
    h ^= h >> 15
    h = (h * 0x2C1B3C6D) & 0xFFFFFFFF
    h ^= h >> 12
    h = (h * 0x297A2D39) & 0xFFFFFFFF
    h ^= h >> 15
    return h % count


class Prop:
    @property
    def light_radius(self):
        return self._light_radius

    @light_radius.setter
    def light_radius(self, value):
        self._light_radius = value
        LIGHT_EPOCH[0] += 1

    swing = 0.0
    break_askew = 0.0
    is_broken = False
    fall_t = 1.0
    strain = 0.0
    hatch_open_t = 0.0
    wheel_spin = 0.0
    ghost_alpha = 1.0
    pipe_open_neg = False
    pipe_open_pos = False
    pipe_end_neg = "bend"
    pipe_end_pos = "bend"
    latch_anim_t = 0.0
    hides = False
    authored = False
    locker_blocked = False

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
        self.collide_off = spec.get("collide_off", 0.0)
        self.height = spec["height"]
        self.base_color = spec["color"]
        self._solid = spec["solid"]
        self.wall_mounted = spec["wall_mounted"]
        self.interactable = spec.get("interactable")
        self.emissive = spec.get("emissive", False)
        self.texture = spec.get("texture")
        self.hides = spec.get("hides", False)
        self.light_radius = spec.get("light_radius")
        self.light_at = spec.get("light_at")
        self.light_lobe = spec.get("light_lobe")
        self.light_color = spec.get("light_color")
        self.flicker = spec.get("flicker", False)
        self.z0 = spec.get("z0", 0.0)
        self.cut_stage = 0
        self._occ_hash = None
        self.install_t = 0.0
        self.variant = variant_for_position(x, y, spec.get("variants", 1))

        self.picked = False
        self.outdoor = False
        self.installed = 0
        self.powered = False
        self.broken = False
        self.cut = False
        self.note_text = note_text
        self.bob_phase = random.uniform(0, math.tau)
        self.interact_cell = None

    @property
    def alive(self):
        return not self.picked

    @property
    def collide_x(self):
        return self.x + math.cos(self.facing) * self.collide_off if self.collide_off else self.x

    @property
    def collide_y(self):
        return self.y + math.sin(self.facing) * self.collide_off if self.collide_off else self.y

    @property
    def collide_facing(self):
        return self.facing

    @property
    def solid(self):
        if self.kind == "shed_lock" and self.powered:
            return False
        return self._solid


def make_prop(kind, cell, facing=0.0, note_text=None):
    return Prop(kind, cell[0] + 0.5, cell[1] + 0.5, facing=facing, note_text=note_text)


DOOR_LEAF = (0.485, 0.07, S.WALL_HEIGHT - 0.02)


class Door:
    fall_t = 1.0
    fall_dir = 1.0
    strain = 0.0
    strain_gain = 1.0
    hatch_open_t = 0.0
    ghost_alpha = 1.0
    just_landed = False
    broken = False
    cut = False
    hides = False
    pipe_open_neg = False
    pipe_open_pos = False
    pipe_end_neg = "bend"
    pipe_end_pos = "bend"
    installed = 0
    cut_stage = 0
    _occ_hash = None
    install_t = 0.0

    def __init__(self, x, y, facing, hw=DOOR_LEAF[0], hd=DOOR_LEAF[1], height=DOOR_LEAF[2]):
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
        self.is_latched = False
        self.latch_anim_t = 0.0
        self._pending_open = False
        self._pending_latch = False
        self.just_auto_opened = False
        self.just_auto_latched = False
        self.swing = 0.0
        self._swing_target = 0.0
        self.break_askew = 0.0
        self.fall_t = 1.0
        self.fall_dir = 1.0
        self.strain = 0.0
        self.strain_gain = 1.0
        self.just_landed = False
        self.ignore_player = False
        rx, ry = -math.sin(facing), math.cos(facing)
        self._hinge_x = x + rx * hw
        self._hinge_y = y + ry * hw
        self._vx0 = -rx * hw
        self._vy0 = -ry * hw
        self._centre_key = None
        self._centre_xy = (x, y)

    @property
    def facing(self):
        return self.base_facing + self.swing * (math.pi / 2) + self.break_askew

    def _centre(self):
        key = (self.swing, self.break_askew)
        if key != self._centre_key:
            angle = self.swing * (math.pi / 2) + self.break_askew
            ca, sa = math.cos(angle), math.sin(angle)
            self._centre_xy = (self._hinge_x + (self._vx0 * ca - self._vy0 * sa),
                               self._hinge_y + (self._vx0 * sa + self._vy0 * ca))
            self._centre_key = key
        return self._centre_xy

    @property
    def x(self):
        return self._centre()[0]

    @property
    def y(self):
        return self._centre()[1]

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
        if self.is_latched and not self.is_open:
            self.is_latched = False
            self._pending_open = True
            return
        self._pending_open = False
        self.is_open = not self.is_open
        self._swing_target = 1.0 if self.is_open else 0.0
        if not self.is_open:
            self.ignore_player = True

    def toggle_latch(self):
        if self.is_broken:
            return
        if self.is_open:
            self.toggle()
            self._pending_latch = True
        elif self.is_latched:
            self.is_latched = False
        else:
            self.is_latched = True

    def _aim_fall(self, from_xy):
        if from_xy is None:
            return
        fx, fy = math.cos(self.base_facing), math.sin(self.base_facing)
        side = (from_xy[0] - self._hinge_x) * fx + (from_xy[1] - self._hinge_y) * fy
        self.fall_dir = -1.0 if side > 0.0 else 1.0

    def take_hit(self, from_xy=None, progress=0.0):
        if self.is_broken:
            return
        self.strain = 1.0
        self.strain_gain = 0.35 + 0.65 * max(0.0, min(1.0, progress))
        self._aim_fall(from_xy)

    DOOR_COLOR_WHOLE = (96, 64, 40)
    DOOR_COLOR_BROKEN = (46, 32, 26)

    def break_open(self, from_xy=None, animate=True):
        self.is_open = True
        self.is_broken = True
        self.is_latched = False
        self.swing = 0.0
        self._swing_target = 0.0
        self.fall_t = 0.0 if animate else 1.0
        self.just_landed = False
        self.strain = 0.0
        self._aim_fall(from_xy)
        self.break_askew = ((self.cell[0] * 7 + self.cell[1] * 3) % 7 - 3) * 0.035
        self._fade_break_color()

    def _fade_break_color(self):
        f = min(1.0, self.fall_t * 1.4)
        self.base_color = tuple(int(a + (b - a) * f)
                                for a, b in zip(self.DOOR_COLOR_WHOLE, self.DOOR_COLOR_BROKEN))

    def update(self, dt):
        rate = 5.0
        self.just_landed = False
        if self.strain > 0.0:
            self.strain = max(0.0, self.strain - dt / S.DOOR_STRAIN_DECAY)
        if self.fall_t < 1.0:
            was = self.fall_t
            self.fall_t = min(1.0, self.fall_t + dt / S.DOOR_FALL_SECONDS)
            self.just_landed = was < S.DOOR_FALL_LAND <= self.fall_t
            self._fade_break_color()
        self.swing += (self._swing_target - self.swing) * min(1.0, dt * rate)
        self.just_auto_opened = False
        self.just_auto_latched = False
        if self._pending_latch and self.swing <= 0.02:
            self._pending_latch = False
            self.is_latched = True
            self.just_auto_latched = True
        latch_target = 1.0 if self.is_latched else 0.0
        self.latch_anim_t += (latch_target - self.latch_anim_t) * min(1.0, dt * S.DOOR_LATCH_ANIM_RATE)
        if self._pending_open and self.latch_anim_t <= 0.02:
            self._pending_open = False
            self.is_open = True
            self._swing_target = 1.0
            self.just_auto_opened = True

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
        if (p.solid or p.hides) and p.height >= min_height and not p.picked
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


CABIN_KINDS = {"elevator", "elevator_arrival"}


def _wall_cells_around(maze, floor_cell, opaque=False):
    fx, fy = floor_cell
    out = []
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        wx, wy = fx + dx, fy + dy
        if 0 <= wx < maze.w and 0 <= wy < maze.h and maze.grid[wy][wx] != S.FLOOR and \
                not (opaque and maze.is_see_through(wx, wy)):
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


def _wall_mount_position(boundary, facing, hd, offset=0.005):
    return (
        boundary[0] + math.cos(facing) * (hd + offset),
        boundary[1] + math.sin(facing) * (hd + offset)
    )


def furniture_place(item):
    return item[0], float(item[1]), float(item[2]), float(item[3]), float(item[4])


def support_top(kind, z0=0.0, variant=0):
    spec = PROP_DEFS[kind]
    frac = SURFACE_TOP_FRAC.get(kind, MESH_TOP_FRAC.get(kind, 1.0))
    if isinstance(frac, tuple):
        frac = frac[variant % len(frac)]
    return z0 + spec["height"] * frac


def prop_support_top(p):
    return support_top(p.kind, p.z0, getattr(p, "variant", 0))


def _place_fence_gaps(maze, count, yard_cells, dist, spawn, used, blocked_solid, props, rng):
    hd = PROP_DEFS["fence_gap"]["hd"]
    cx, cy = maze.w / 2.0, maze.h / 2.0
    candidates = [c for c in yard_cells if c not in used and dist.get(c, 0) >= 4 and _fence_wall_cells(maze, c)]
    if not candidates:
        candidates = [c for c in yard_cells if _fence_wall_cells(maze, c)]
    placed = []
    base = rng.uniform(0.0, math.tau)
    for i in range(max(1, count)):
        want = base + math.tau * i / max(1, count)
        ranked = sorted(candidates, key=lambda c: abs((math.atan2(c[1] + 0.5 - cy, c[0] + 0.5 - cx) - want
                                                       + math.pi) % math.tau - math.pi))
        for cell in ranked:
            if any(abs(cell[0] - q[0]) + abs(cell[1] - q[1]) < 8 for q in (p.interact_cell for p in placed)):
                continue
            for boundary, fac, fc in _fence_wall_cells(maze, cell):
                if not _is_safe_to_block(maze, spawn, dist, blocked_solid, fc):
                    continue
                x, y = _wall_mount_position(boundary, fac, hd)
                gap = Prop("fence_gap", x, y, facing=fac)
                if not _region_physically_clear(maze, props + [gap], fc):
                    continue
                gap.interact_cell = fc
                gap.powered = True
                placed.append(gap)
                props.append(gap)
                used.add(fc)
                blocked_solid |= cells_no_body_fits([gap], S.PLAYER_RADIUS)
                break
            if placed and placed[-1].interact_cell == cell:
                break
    _mark_fence_gap_edges(maze, placed)
    return placed


def _mark_fence_gap_edges(maze, gaps):
    edges = set()
    for gap in gaps:
        fx, fy = gap.interact_cell
        dx, dy = round(math.cos(gap.facing)), round(math.sin(gap.facing))
        edges.add(((fx - dx, fy - dy), (dx, dy)))
    maze.fence_gap_edges = edges


def _is_safe_to_block(maze, spawn, all_reachable, blocked_solid, cell):
    trial = blocked_solid | {cell}
    reach = maze.bfs_distances(spawn[0], spawn[1], blocked=trial)
    expected = len(all_reachable) - len(trial & all_reachable.keys())
    return len(reach) == expected


SEGMENT_MAX_REACH = 1.5
SEGMENT_BIG_KINDS = frozenset(
    k for k, v in PROP_DEFS.items()
    if v.get("collide_hw", v["hw"]) + v.get("collide_hd", v["hd"])
    + abs(v.get("collide_off", 0.0)) > SEGMENT_MAX_REACH)


def props_near_segment(props, ax, ay, bx, by, r):
    vx, vy = bx - ax, by - ay
    vv = vx * vx + vy * vy
    m = r + SEGMENT_MAX_REACH
    lo_x, hi_x = min(ax, bx) - m, max(ax, bx) + m
    lo_y, hi_y = min(ay, by) - m, max(ay, by) + m
    big = SEGMENT_BIG_KINDS
    out = []
    for p in props:
        if not (lo_x <= p.x <= hi_x and lo_y <= p.y <= hi_y) and p.kind not in big:
            continue
        if not p.solid:
            continue
        px, py = p.collide_x, p.collide_y
        t = 0.0 if vv <= 1e-12 else max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / vv))
        dx, dy = px - (ax + vx * t), py - (ay + vy * t)
        reach = r + p.collide_hw + p.collide_hd
        if dx * dx + dy * dy <= reach * reach:
            out.append(p)
    return out


def footprint_cells(p, margin=0.0):
    fx, fy = math.cos(p.collide_facing), math.sin(p.collide_facing)
    hw, hd = p.collide_hw + margin, p.collide_hd + margin
    xs, ys = [], []
    for sw, sd in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
        xs.append(p.collide_x - fy * hw * sw + fx * hd * sd)
        ys.append(p.collide_y + fx * hw * sw + fy * hd * sd)
    return {(x, y)
            for y in range(int(math.floor(min(ys))), int(math.floor(max(ys))) + 1)
            for x in range(int(math.floor(min(xs))), int(math.floor(max(xs))) + 1)}


def cells_no_body_fits(props_list, radius, cells=None):
    solids = [q for q in props_list if q.solid and not getattr(q, "picked", False)]
    near = {}
    for q in solids:
        for c in footprint_cells(q, radius):
            near.setdefault(c, []).append(q)
    out = set()
    for c, here in near.items():
        if cells is not None and c not in cells:
            continue
        cx, cy = c[0] + 0.5, c[1] + 0.5
        if any(_circle_hits_prop(cx, cy, radius, q) for q in here):
            out.add(c)
    return out


def _circle_hits_prop(x, y, r, p):
    dx, dy = x - p.collide_x, y - p.collide_y
    reach = p.collide_hw + p.collide_hd + r
    if dx * dx + dy * dy >= reach * reach:
        return False
    fx, fy = math.cos(p.collide_facing), math.sin(p.collide_facing)
    rx, ry = -fy, fx
    local_f = dx * fx + dy * fy
    local_r = dx * rx + dy * ry
    closest_r = max(-p.collide_hw, min(p.collide_hw, local_r))
    closest_f = max(-p.collide_hd, min(p.collide_hd, local_f))
    dr, df = local_r - closest_r, local_f - closest_f
    return dr * dr + df * df < r * r


def _prop_cells_index(props_list, pad):
    idx = {}
    for p in props_list:
        reach = p.collide_hw + p.collide_hd + pad
        for iy in range(int(math.floor(p.collide_y - reach)), int(math.floor(p.collide_y + reach)) + 1):
            for ix in range(int(math.floor(p.collide_x - reach)), int(math.floor(p.collide_x + reach)) + 1):
                idx.setdefault((ix, iy), []).append(p)
    return idx


def _pick_monster_spawn(maze, far, ranked, props, rng, doors=()):
    solid_candidates = [p for p in props if p.solid and (not p.wall_mounted or p.kind == "locker")]
    door_cells = {d.cell for d in doors}

    near = _prop_cells_index(solid_candidates, S.MONSTER_RADIUS)
    empty = []

    def clear(cell):
        cx, cy = cell[0] + 0.5, cell[1] + 0.5
        if cell in door_cells:
            return False
        if maze.circle_hits_wall(cx, cy, S.MONSTER_RADIUS):
            return False
        return not any(_circle_hits_prop(cx, cy, S.MONSTER_RADIUS, p)
                       for p in near.get(cell, empty))

    dark = maze.wing_darkness(S.MONSTER_DARK_SPAWN) if hasattr(maze, "wing_darkness") else {}

    def pick(cells):
        if not dark:
            return rng.choice(cells)
        return rng.choices(cells, weights=[dark.get(c, 1.0) for c in cells])[0]

    far_clear = [c for c in far if clear(c)]
    if far_clear:
        return pick(far_clear)
    any_clear = [c for c in ranked if clear(c)]
    if any_clear:
        return pick(any_clear)
    return pick(far)


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


def _needs_solid_wall(kind):
    return kind in CABIN_KINDS or bool(PROP_DEFS[kind].get("wall_mounted"))


def _wall_prop(maze, floor_cell, kind, rng, spawn=None, all_reachable=None, blocked_solid=None, note_text=None,
                props_list=None, used=None, spawn_guard=True):
    candidates = _wall_cells_around(maze, floor_cell, opaque=_needs_solid_wall(kind))
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
            blocked_solid |= cells_no_body_fits([prop], S.PLAYER_RADIUS)
        if used is not None:
            used.add(fc)
        return prop
    return None


def place_arrival_prop(maze, kind, rng, used=None):
    used = used if used is not None else set()
    sx, sy = int(maze.start[0]), int(maze.start[1])
    if maze.rooms:
        dist = maze.bfs_distances(sx, sy)
        cells = maze.room_cells(maze.rooms[0])
        prop = _place_wall_prop_in_cells(maze, cells, kind, dist, used, rng, (sx, sy), dist, set())
        if prop is not None:
            return prop
    return _wall_prop(maze, (sx, sy), kind, rng, used=used)


def _place_wall_prop_in_cells(maze, cells, kind, dist, used, rng, spawn, all_reachable, blocked_solid,
                               prefer_far=False, props_list=None):
    candidates = []
    for c in cells:
        if c in used:
            continue
        candidates.extend(_wall_cells_around(maze, c, opaque=_needs_solid_wall(kind)))
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
        x, y = _wall_mount_position(boundary, facing, PROP_DEFS[kind]["hd"])
        prop = Prop(kind, x, y, facing=facing)
        if solid:
            blocked_solid |= cells_no_body_fits([prop], S.PLAYER_RADIUS)
        prop.interact_cell = fc
        return prop
    return None


def _spread_pick(cells, n, used, rng, min_gap=3, cell_to_group=None, group_weight=None, group_caps=None,
                 taken=None, taken_gap=0, group_counts=None, cell_rank=None):
    taken = [] if taken is None else taken
    candidates = [c for c in cells if c not in used]

    def spaced(c, chosen):
        return (all(abs(c[0] - o[0]) + abs(c[1] - o[1]) >= min_gap for o in chosen)
                and all(abs(c[0] - o[0]) + abs(c[1] - o[1]) >= taken_gap for o in taken))
    if cell_to_group is None:
        rng.shuffle(candidates)
        chosen = []
        for c in candidates:
            if len(chosen) >= n:
                break
            if spaced(c, chosen):
                chosen.append(c)
        if len(chosen) < n:
            for c in candidates:
                if len(chosen) >= n:
                    break
                if c not in chosen:
                    chosen.append(c)
        taken.extend(chosen)
        return chosen
    by_group = {}
    for c in candidates:
        by_group.setdefault(cell_to_group(c), []).append(c)
    for pool in by_group.values():
        rng.shuffle(pool)
        if cell_rank is not None:
            pool.sort(key=cell_rank, reverse=True)
    counts = {} if group_counts is None else group_counts
    base_weights = {
        gid: max(0.0001, group_weight(gid, pool) if group_weight else float(len(pool)))
        for gid, pool in by_group.items()
    }
    weights = {gid: w / (1 + counts.get(gid, 0)) for gid, w in base_weights.items()}
    if group_caps is not None:
        for gid in list(by_group.keys()):
            if group_caps.get(gid, None) == 0:
                del by_group[gid]
                weights.pop(gid, None)
    chosen = []
    group_ids = list(by_group.keys())
    guard = 0
    while len(chosen) < n and group_ids and guard < n * 50:
        guard += 1
        gid = rng.choices(group_ids, weights=[weights[g] for g in group_ids], k=1)[0]
        pool = by_group[gid]
        picked_idx = next((i for i, c in enumerate(pool) if spaced(c, chosen)), None)
        if picked_idx is not None:
            chosen.append(pool.pop(picked_idx))
            counts[gid] = counts.get(gid, 0) + 1
            weights[gid] = base_weights[gid] / (1 + counts[gid])
            if group_caps is not None and gid in group_caps:
                group_caps[gid] -= 1
                if group_caps[gid] <= 0:
                    pool.clear()
        if picked_idx is None or not pool:
            group_ids.remove(gid)
            del weights[gid]
    if len(chosen) < n:
        capped_out = set() if group_caps is None else {g for g, rem in group_caps.items() if rem <= 0}
        for exclude_capped in (True, False):
            if len(chosen) >= n:
                break
            leftover = [c for gid, pool in by_group.items() for c in pool
                        if c not in chosen and not (exclude_capped and gid in capped_out)]
            rng.shuffle(leftover)
            leftover.sort(key=lambda c: not spaced(c, chosen))
            for c in leftover:
                if len(chosen) >= n:
                    break
                chosen.append(c)
    taken.extend(chosen)
    return chosen


PICKUP_SURFACE_MAX_Z0 = 0.02
SURFACE_EDGE_PULL = 0.45


def mark_surface_open_side(surf, dist):
    cell = getattr(surf, "interact_cell", None)
    if cell is None:
        return
    cx, cy = cell
    free = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
            if (dx or dy) and (cx + dx, cy + dy) in dist]
    if not free:
        return
    ax = sum(dx for dx, dy in free) / len(free)
    ay = sum(dy for dx, dy in free) / len(free)
    n = math.hypot(ax, ay)
    if n > 1e-6:
        surf.open_side = (ax / n, ay / n)


def _spot_openness(x, y, blockers, z, item_hw):
    if not blockers:
        return 1.0
    free = 0
    for k in range(8):
        a = k * math.pi / 4.0
        dx, dy = math.cos(a), math.sin(a)
        clear = True
        for step in (item_hw + 0.10, item_hw + 0.26):
            sx, sy = x + dx * step, y + dy * step
            for q in blockers:
                if q.height <= 0.1 or not (q.z0 <= z + 0.06 <= q.z0 + q.height):
                    continue
                fx, fy = math.cos(q.collide_facing), math.sin(q.collide_facing)
                rx, ry = -fy, fx
                lx, ly = sx - q.collide_x, sy - q.collide_y
                if abs(lx * fx + ly * fy) <= q.hd and abs(lx * rx + ly * ry) <= q.hw:
                    clear = False
                    break
            if not clear:
                break
        free += clear
    return free / 8.0


def _pick_surface_spot(surf, item_hw, rng, occupied, tries=16, blockers=None, item_z=None):
    keep = SURFACE_SPOT_FRAC.get(surf.kind, 1.0)
    keep_x, keep_y = keep if isinstance(keep, tuple) else (keep, keep)
    span_x = max(0.02, (surf.hd - item_hw) * keep_x)
    span_y = max(0.02, (surf.hw - item_hw) * keep_y)
    fx, fy = math.cos(surf.facing), math.sin(surf.facing)
    rx, ry = -fy, fx
    open_side = getattr(surf, "open_side", None)
    open_f = open_side[0] * fx + open_side[1] * fy if open_side else 0.0
    open_r = open_side[0] * rx + open_side[1] * ry if open_side else 0.0
    key = getattr(surf, "interact_cell", None)
    existing = occupied.get(key, ()) if key is not None else ()
    best_xy, best_score = None, float("-inf")
    for _ in range(tries):
        along_forward = rng.uniform(-span_x, span_x)
        along_right = rng.uniform(-span_y, span_y)
        if open_side is not None:
            if abs(open_f) > 0.3:
                along_forward = math.copysign(
                    span_x * (SURFACE_EDGE_PULL + (1.0 - SURFACE_EDGE_PULL) * rng.random()), open_f)
            if abs(open_r) > 0.3:
                along_right = math.copysign(
                    span_y * (SURFACE_EDGE_PULL + (1.0 - SURFACE_EDGE_PULL) * rng.random()), open_r)
        x = surf.x + fx * along_forward + rx * along_right
        y = surf.y + fy * along_forward + ry * along_right
        score = min((math.hypot(x - ox, y - oy) - r - item_hw for ox, oy, r in existing), default=999.0)
        if blockers is not None and item_z is not None:
            open_frac = _spot_openness(x, y, blockers, item_z, item_hw)
            score = min(score, 0.0) + open_frac
            if open_frac >= 0.75 and score >= 0.75:
                best_xy = (x, y)
                break
        elif score >= 0:
            best_xy = (x, y)
            break
        if score > best_score:
            best_score, best_xy = score, (x, y)
    x, y = best_xy
    if key is not None:
        occupied.setdefault(key, []).append((x, y, item_hw))
    return x, y


PICKUP_SURFACE_REACH = 2.3


def _place_pickup(kind, cell, surfaces_by_room, cell_to_room, rng, surface_occupied,
                  on_surface_chance=0.45, reach=None, prefer=(), allow=None, blockers=None):
    room = cell_to_room(cell) if cell_to_room else None
    room_surfaces = surfaces_by_room.get(room) if surfaces_by_room else None
    if room_surfaces and rng.random() < on_surface_chance:
        cx, cy = cell[0] + 0.5, cell[1] + 0.5
        limit = PICKUP_SURFACE_REACH if reach is None else reach
        near = [s for s in room_surfaces
                if math.hypot(s.x - cx, s.y - cy) <= limit
                and getattr(s, "z0", 0.0) <= PICKUP_SURFACE_MAX_Z0
                and (allow is None or allow(s))]
        if near:
            good = [s for s in near if s.kind in prefer]
            near = good or near
            surf = min(near, key=lambda s: math.hypot(s.x - cx, s.y - cy))
            room_surfaces.remove(surf)
            item_hw = PROP_DEFS[kind]["hw"]
            z = _surface_top_z0(surf, rng)
            near_props = None
            if blockers is not None:
                near_props = [q for q in blockers
                              if q is not surf and abs(q.x - surf.x) < 2.0 and abs(q.y - surf.y) < 2.0]
            x, y = _pick_surface_spot(surf, item_hw, rng, surface_occupied,
                                      blockers=near_props, item_z=z)
            prop = Prop(kind, x, y, facing=rng.uniform(0, math.tau))
            prop.z0 = z
            return prop
    return make_prop(kind, cell)


def _reachable_surface(surf, dist):
    cell = getattr(surf, "interact_cell", None)
    if cell is None:
        return True
    cx, cy = cell
    return any((cx + dx, cy + dy) in dist for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy)


PICKUP_ROOM_AFFINITY = {
    "battery": {"workshop": 3.0, "storage": 2.5, "pump": 2.0, "boiler": 2.0,
                "laundry": 1.6, "plain": 1.3, "entrance": 1.3},
    "fuse": {"workshop": 3.0, "storage": 2.5, "pump": 2.0, "boiler": 2.0, "plain": 1.3},
    "valve_key": {"workshop": 3.0, "pump": 2.5, "boiler": 2.5, "storage": 2.0},
    "sanity_pill": {"pharmacy": 4.0, "treatment": 3.0, "nurse": 2.5, "ward": 1.6, "bath": 1.3},
    "note_flat": {"office": 2.5, "archive": 2.5, "nurse": 2.0, "ward": 1.6,
                  "cell": 1.6, "dayroom": 1.5, "morgue": 1.4},
    "pencil": {"office": 3.0, "archive": 2.5, "nurse": 2.0, "pharmacy": 1.6, "dayroom": 1.5},
    "paper_map": {"office": 2.5, "archive": 2.5, "nurse": 2.0, "entrance": 2.0, "stairwell": 1.6},
    "map_sheet": {"office": 2.0, "archive": 2.0, "nurse": 1.6, "entrance": 1.6},
    "lighter": {"workshop": 2.5, "boiler": 2.5, "cafeteria": 2.0, "dayroom": 2.0,
                "laundry": 1.6, "cell": 1.4},
}

ZONE_PICKUP_AFFINITY = {
    "battery": {"shed": 3.0, "tool_shed": 3.0, "storage": 2.5, "plant": 2.0, "dump": 1.6},
    "key": {"shed": 2.0, "tool_shed": 2.0, "storage": 2.0, "plant": 1.6, "morgue_dock": 1.6},
    "note_flat": {"chapel": 2.5, "morgue_dock": 2.0, "greenhouse": 1.6, "alley": 1.4, "pen": 1.4},
    "pencil": {"chapel": 2.0, "morgue_dock": 2.0, "storage": 1.6},
    "map_sheet": {"shed": 2.0, "storage": 2.0, "chapel": 1.6},
}

CLUTTER_KINDS = ("clutter_papers", "clutter_bottle", "clutter_junk")
CLUTTER_PER_CELL = (0.045, 0.012)
CLUTTER_MAX_PER_ROOM = 6
CLUTTER_MIN_ROOM_CELLS = 12
CLUTTER_ON_SURFACE_CHANCE = 0.55
CLUTTER_SPACING = 1.6


def _scatter_clutter(maze, rooms, props, rng, surface_occupied, used, dist, corridor_kinds,
                     density=1.0):
    surfaces_by_cell = {}
    for p in props:
        if p.kind in SURFACE_KINDS and getattr(p, "interact_cell", None) is not None:
            surfaces_by_cell.setdefault(p.interact_cell, []).append(p)
    placed = []
    added = []

    def clear_of_others(x, y):
        return all(math.hypot(x - ox, y - oy) >= CLUTTER_SPACING for ox, oy in placed)

    for room in rooms:
        cells = maze.room_cells(room)
        if not cells:
            continue
        rate = CLUTTER_PER_CELL[1] if room.get("kind") in corridor_kinds else CLUTTER_PER_CELL[0]
        rate *= density
        expected = len(cells) * rate
        n = int(expected) + (1 if rng.random() < expected - int(expected) else 0)
        if len(cells) >= CLUTTER_MIN_ROOM_CELLS and room.get("kind") not in corridor_kinds:
            n = max(n, 1)
        n = min(n, CLUTTER_MAX_PER_ROOM)
        surfaces = [s for c in cells for s in surfaces_by_cell.get(c, ()) if _reachable_surface(s, dist)]
        for s_ in surfaces:
            mark_surface_open_side(s_, dist)
        floor_spots = [c for c in cells
                       if c in dist and c not in used and _wall_cells_around(maze, c, opaque=True)]
        rng.shuffle(surfaces)
        rng.shuffle(floor_spots)
        for _ in range(n):
            kind = rng.choice(CLUTTER_KINDS)
            item = None
            order = ("surface", "floor") if rng.random() < CLUTTER_ON_SURFACE_CHANCE else ("floor", "surface")
            for where in order:
                if where == "surface":
                    surf = next((s for s in surfaces if clear_of_others(s.x, s.y)), None)
                    if surf is None:
                        continue
                    surfaces.remove(surf)
                    x, y = _pick_surface_spot(surf, PROP_DEFS[kind]["hw"], rng, surface_occupied)
                    item = Prop(kind, x, y, facing=rng.uniform(0, math.tau))
                    item.z0 = _surface_top_z0(surf, rng)
                else:
                    spot = next((c for c in floor_spots if clear_of_others(c[0] + 0.5, c[1] + 0.5)), None)
                    if spot is None:
                        continue
                    floor_spots.remove(spot)
                    (bx, by), facing, _cell = rng.choice(_wall_cells_around(maze, spot, opaque=True))
                    fx, fy = math.cos(facing), math.sin(facing)
                    along = rng.uniform(-0.3, 0.3)
                    x = bx + fx * 0.22 - fy * along
                    y = by + fy * 0.22 + fx * along
                    item = Prop(kind, x, y, facing=rng.uniform(0, math.tau))
                    used.add(spot)
                break
            if item is None:
                break
            placed.append((item.x, item.y))
            added.append(item)
    return added


def _walled_interior_cells(maze, rect, door_cell):
    rx0, ry0, rx1, ry1 = rect
    dcx, dcy = door_cell

    def flood(start):
        if start == door_cell:
            return None
        if not (rx0 <= start[0] < rx1 and ry0 <= start[1] < ry1):
            return None
        if maze.grid[start[1]][start[0]] != S.FLOOR:
            return None
        seen = {start}
        stack = [start]
        touches_border = False
        while stack:
            x, y = stack.pop()
            if x in (rx0, rx1 - 1) or y in (ry0, ry1 - 1):
                touches_border = True
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if (nx, ny) == door_cell:
                    continue
                if not (rx0 <= nx < rx1 and ry0 <= ny < ry1):
                    continue
                if maze.grid[ny][nx] != S.FLOOR or (nx, ny) in seen:
                    continue
                seen.add((nx, ny))
                stack.append((nx, ny))
        return seen, touches_border

    results = []
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        r = flood((dcx + dx, dcy + dy))
        if r is not None:
            results.append(r)
    enclosed = [cells for cells, touches_border in results if not touches_border]
    if enclosed:
        return min(enclosed, key=len) if len(enclosed) > 1 else enclosed[0]
    if results:
        return min((cells for cells, _ in results), key=len)
    return set()


def _blocks_the_monster(p):
    return p.solid and (not getattr(p, "wall_mounted", False) or p.kind == "locker")


def _unplug_for_the_monster(maze, props, start_cell, protected_kinds):
    removed = []
    for _ in range(12):
        movers = [p for p in props if _blocks_the_monster(p)]
        blocked = cells_no_body_fits(movers, S.MONSTER_RADIUS)
        blocked.discard(start_cell)
        free = maze.bfs_distances(start_cell[0], start_cell[1], blocked=blocked)
        cut = [c for c in maze.floor_cells() if c not in blocked and c not in free]
        if not cut:
            break
        cut_set = set(cut)
        by_cell = {}
        for p in movers:
            if p.kind in protected_kinds:
                continue
            for c in footprint_cells(p, S.MONSTER_RADIUS):
                by_cell.setdefault(c, []).append(p)
        best = None
        for cell in blocked:
            around = [(cell[0] + dx, cell[1] + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))]
            if not (any(c in free for c in around) and any(c in cut_set for c in around)):
                continue
            here = by_cell.get(cell, [])
            if not here:
                continue
            rank = (1 if any(p.kind == "locker" for p in here) else 0, len(here))
            if best is None or rank < best[0]:
                best = (rank, here)
        if best is None:
            break
        for p in best[1]:
            if p in props:
                props.remove(p)
                removed.append(p.kind)
                for rider in _resting_on(p, props):
                    props.remove(rider)
    return removed


def _blocker_lateral_span(blocker, lx, ly, rx, ry):
    fx, fy = math.cos(blocker.collide_facing), math.sin(blocker.collide_facing)
    px, py = -fy, fx
    lo = hi = None
    for sw, sd in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
        cx = blocker.collide_x + px * blocker.collide_hw * sw + fx * blocker.collide_hd * sd
        cy = blocker.collide_y + py * blocker.collide_hw * sw + fy * blocker.collide_hd * sd
        t = (cx - lx) * rx + (cy - ly) * ry
        lo = t if lo is None else min(lo, t)
        hi = t if hi is None else max(hi, t)
    return lo, hi


LOCKER_BLOCK_REACH = 1.6
LOCKER_BLOCK_GAP = 0.34
LOCKER_SQUEEZE = 0.36


def _block_shut_lockers(props):
    lockers = [p for p in props if p.kind == "locker"]
    if not lockers:
        return 0
    solids = [p for p in props if p.solid and not getattr(p, "picked", False)]
    shut = 0
    for lk in lockers:
        fx, fy = math.cos(lk.facing), math.sin(lk.facing)
        rx, ry = -fy, fx
        door = lk.collide_hd
        spans = []
        for b in solids:
            if b is lk:
                continue
            dx, dy = b.collide_x - lk.x, b.collide_y - lk.y
            if dx * dx + dy * dy > LOCKER_BLOCK_REACH ** 2:
                continue
            near, far = _blocker_forward_span(b, lk.x, lk.y, fx, fy)
            if far <= door or near - door > LOCKER_BLOCK_GAP:
                continue
            spans.append(_blocker_lateral_span(b, lk.x, lk.y, rx, ry))
        want = PROP_DEFS["locker"]["hw"]
        if _widest_gap(spans, -want, want) >= LOCKER_SQUEEZE:
            continue
        lk.locker_blocked = True
        lk.interactable = None
        shut += 1
    return shut


def _blocker_forward_span(blocker, lx, ly, fx, fy):
    bfx, bfy = math.cos(blocker.collide_facing), math.sin(blocker.collide_facing)
    px, py = -bfy, bfx
    lo = hi = None
    for sw, sd in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
        cx = blocker.collide_x + px * blocker.collide_hw * sw + bfx * blocker.collide_hd * sd
        cy = blocker.collide_y + py * blocker.collide_hw * sw + bfy * blocker.collide_hd * sd
        t = (cx - lx) * fx + (cy - ly) * fy
        lo = t if lo is None else min(lo, t)
        hi = t if hi is None else max(hi, t)
    return lo, hi


def _widest_gap(spans, lo, hi):
    reach = lo
    best = 0.0
    for a, b in sorted(spans):
        if a > reach:
            best = max(best, a - reach)
        reach = max(reach, b)
        if reach >= hi:
            return best
    return max(best, hi - reach)


def _resting_on(base, props, eps=0.03):
    out, frontier = [], [base]
    while frontier:
        b = frontier.pop()
        top = prop_support_top(b)
        c, s = math.cos(-b.facing), math.sin(-b.facing)
        for q in props:
            if q is base or any(q is o for o in out) or q.z0 <= 0.02:
                continue
            if abs(q.z0 - top) > eps:
                continue
            dx, dy = q.x - b.x, q.y - b.y
            if abs(dx * s + dy * c) <= b.hw and abs(dx * c - dy * s) <= b.hd:
                out.append(q)
                frontier.append(q)
    return out


def _finalize_physical_safety(maze, props, dist):
    protected_kinds = {"fuse_box", "valve_panel", "elevator", "hatch", "shed_lock", "fence_gap"}
    guard = 0
    while guard < 30:
        guard += 1
        solids = [
            p for p in props
            if p.solid and (p.kind != "door" or p.is_open or p.is_broken)
        ]
        removed_any = False
        near = _prop_cells_index(solids, S.PLAYER_RADIUS)
        empty = []
        for (cx, cy) in dist.keys():
            for dx, dy in ((1, 0), (0, 1)):
                nx, ny = cx + dx, cy + dy
                if (nx, ny) not in dist:
                    continue
                a = near.get((cx, cy), empty)
                b = near.get((nx, ny), empty)
                cands = a if not b else (b if not a else list(dict.fromkeys(a + b)))
                if not cands or _edge_physically_clear(maze, cands, cx, cy, nx, ny):
                    continue
                mx, my = cx + 0.5 + dx * 0.5, cy + 0.5 + dy * 0.5
                culprits = [
                    p for p in cands
                    if p.kind not in protected_kinds
                    and math.hypot(p.x - mx, p.y - my) < p.hw + p.hd + S.PLAYER_RADIUS + 0.5
                ]
                if not culprits:
                    continue
                for p in culprits:
                    if p in props:
                        props.remove(p)
                        for rider in _resting_on(p, props):
                            props.remove(rider)
                removed_any = True
        if not removed_any:
            break
    return props


_RANDOM_DOOR_KIND_CHOICES = ("door", "broken", "passage")


def _resolve_random_door_kind(kind, rng):
    return rng.choice(_RANDOM_DOOR_KIND_CHOICES) if kind == "random" else kind


_PIPE_NEIGHBOR_EPS = 0.05


def link_adjacent_pipes(props, maze=None):
    pipes = [p for p in props if p.kind == "pipes"]
    for p in pipes:
        p.pipe_end_neg = "bend"
        p.pipe_end_pos = "bend"
        p.pipe_open_neg = False
        p.pipe_open_pos = False
    if maze is not None:
        for p in pipes:
            tx, ty = -math.sin(p.facing), math.cos(p.facing)
            for attr, end, sgn in (("pipe_open_pos", "pipe_end_pos", 1),
                                   ("pipe_open_neg", "pipe_end_neg", -1)):
                cx, cy = int(p.x + tx * sgn), int(p.y + ty * sgn)
                if not maze.is_walkable_cell(cx, cy):
                    setattr(p, attr, True)
                    setattr(p, end, "wall")
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
                a.pipe_end_pos = "join"
            elif abs(along + 1.0) < _PIPE_NEIGHBOR_EPS * 4:
                a.pipe_open_neg = True
                a.pipe_end_neg = "join"


BREAKABLE_LIGHT_KINDS = {"wall_sconce", "lamp_desk", "monitor"}
WING_TINTED_KINDS = {"wall_sconce", "lamp_desk"}


def _barricades(maze, doors, dist, used, rng, count):
    if not count or not doors:
        return []
    order = [d for d in doors if dist.get(d.cell, -1) >= 3]
    rng.shuffle(order)
    out = []
    for door in order:
        if len(out) >= count:
            break
        dx, dy = round(math.cos(door.facing)), round(math.sin(door.facing))
        spots = []
        for sign in (1, -1):
            base = (door.cell[0] + dx * sign, door.cell[1] + dy * sign)
            for ax, ay in ((dy, dx), (-dy, -dx)):
                spots.append((base[0] + ax, base[1] + ay))
        rng.shuffle(spots)
        for cell in spots:
            if cell in used or cell not in dist or not maze.is_walkable_cell(*cell):
                continue
            used.add(cell)
            out.append(make_prop("barricade", cell, facing=rng.uniform(0, math.tau)))
            if not getattr(door, "broken", False):
                door.break_open(animate=False)
            break
    return out


def _break_some_lights(props, rng, chance=S.BROKEN_LIGHT_CHANCE, stage_mult=1.0):
    for p in props:
        p_chance = getattr(p, "break_chance", None)
        want = min(S.ANOMALY_BROKEN_MAX,
                   (chance if p_chance is None else p_chance) * stage_mult)
        if p.kind in BREAKABLE_LIGHT_KINDS and rng.random() < want:
            p.broken = True
    return props


def _tint_for_wing(prop, wing, spec):
    if wing is None or not prop.emissive or prop.light_color is None:
        return
    tint = wing.get("light")
    if tint is not None and prop.kind in WING_TINTED_KINDS:
        k = S.WING_LIGHT_BLEND
        was = prop.light_color
        prop.light_color = tuple(c * (1.0 - k) + t * k for c, t in zip(was, tint))
        prop.base_color = tuple(
            max(0, min(255, int(round(b * (new / old if old > 1e-4 else 1.0)))))
            for b, new, old in zip(prop.base_color, prop.light_color, was))
    prop.break_chance = wing.get("broken", spec.get("broken_light_chance",
                                                    S.BROKEN_LIGHT_CHANCE))


def _emergency_lights(maze, props, doors, dist, used, rng, wing_specs, stage_mult=1.0):
    wing_of = {}
    for room in maze.rooms:
        spec = wing_specs.get(room.get("wing"))
        if spec and spec.get("emergency"):
            for c in maze.room_cells(room):
                wing_of.setdefault(c, spec)
    if not wing_of:
        return []

    spots = []
    for door in doors:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            cell = (door.cell[0] + dx, door.cell[1] + dy)
            if cell in wing_of and dist.get(cell, -1) >= 2:
                spots.append((cell, 2.0))
    for cell, spec in wing_of.items():
        if dist.get(cell, -1) < 2:
            continue
        open_dirs = sum(1 for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                        if maze.is_walkable_cell(cell[0] + dx, cell[1] + dy))
        if open_dirs >= 3:
            spots.append((cell, 1.0))
    lit = [(p.x, p.y) for p in props
           if getattr(p, "emissive", False) and getattr(p, "light_radius", None)
           and not getattr(p, "broken", False) and not getattr(p, "picked", False)]
    dark2 = S.EMERGENCY_DARK_RADIUS ** 2
    spots = [(c, w) for (c, w) in spots
             if not any((c[0] + 0.5 - lx) ** 2 + (c[1] + 0.5 - ly) ** 2 < dark2
                        for lx, ly in lit)]
    if not spots:
        return []
    rng.shuffle(spots)
    spots.sort(key=lambda s: -s[1])

    want = max(1, int(round(len(wing_of) * S.EMERGENCY_PER_CELL * stage_mult)))
    out = []
    placed_cells = set()
    for cell, _weight in spots:
        if len(out) >= want:
            break
        if cell in placed_cells or cell in used:
            continue
        if any(abs(cell[0] - c[0]) + abs(cell[1] - c[1]) < S.EMERGENCY_MIN_GAP
               for c in placed_cells):
            continue
        lamp = _wall_prop(maze, cell, "emergency_lamp", rng, used=None)
        if lamp is None:
            continue
        colour = wing_of[cell].get("emergency_light")
        lamp.light_color = tuple(c * S.EMERGENCY_STRENGTH for c in colour)
        lamp.base_color = tuple(max(0, min(255, int(round(c * 255)))) for c in colour)
        placed_cells.add(cell)
        out.append(lamp)
    return out


def populate_level(maze, spec, rng, stage=None):
    anom = S.anomaly_of(stage or S.ANOMALY_DEFAULT_STAGE)
    sx, sy = int(maze.start[0]), int(maze.start[1])
    wing_specs = {w["key"]: w for w in S.FLOOR_WINGS.get(spec.get("floor_theme"), ())}
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
    surfaces_by_room = {}
    cell_to_room = {}
    room_id_to_kind = {}
    surface_occupied = {}
    panel_prop = None
    exit_prop = None
    interior_door_specs = []

    pre_used = set(used)

    for room in reachable_rooms:
        room_id = id(room)
        room_id_to_kind[room_id] = room.get("kind")
        wing_of_room = wing_specs.get(room.get("wing"))
        for c in maze.room_cells(room):
            cell_to_room.setdefault(c, room_id)
        for item in room.get("furniture", []):
            kind, facing = item["kind"], item["facing"]
            x, y, z0 = item["pos"]
            cell = (int(x), int(y))
            if cell in pre_used and z0 <= 1e-6:
                continue
            prop = Prop(kind, x, y, facing=facing)
            prop.z0 = z0
            prop.interact_cell = cell
            prop.authored = True
            _tint_for_wing(prop, wing_of_room, spec)
            props.append(prop)
            used.add(cell)
            if PROP_DEFS[kind]["solid"]:
                blocked_solid |= cells_no_body_fits([prop], S.PLAYER_RADIUS)
            if kind in SURFACE_KINDS:
                surfaces_by_room.setdefault(room_id, []).append(prop)
            if z0 > 1e-6:
                item_r = max(PROP_DEFS[kind]["hw"], PROP_DEFS[kind]["hd"])
                surface_occupied.setdefault(cell, []).append((x, y, item_r))
            if kind == spec["panel"]:
                panel_prop = prop
            elif kind == spec["exit_prop"]:
                exit_prop = prop
        interior_door_specs.extend(room.get("interior_doors", []))

    dist = maze.bfs_distances(sx, sy, blocked=blocked_solid)
    for rid in surfaces_by_room:
        surfaces_by_room[rid] = [s for s in surfaces_by_room[rid] if _reachable_surface(s, dist)]
        for s_ in surfaces_by_room[rid]:
            mark_surface_open_side(s_, dist)

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

    start_room_id = cell_to_room.get((sx, sy))

    CORRIDOR_ROOM_KINDS = {"corridor", "tech_corridor", "vent", "link"}

    def _pickup_group(cell):
        rid = cell_to_room.get(cell)
        if rid is None or room_id_to_kind.get(rid) in CORRIDOR_ROOM_KINDS:
            return None
        return rid

    def _pickup_group_weight(gid, pool):
        size = math.sqrt(float(len(pool)))
        if gid is None:
            return size * 0.2
        if gid == start_room_id:
            return size * 0.4
        return size

    dead_end_rooms = {id(r) for r in reachable_rooms if maze.room_door_count(r) == 1}

    def weight_for(pickup_kind):
        affinity = PICKUP_ROOM_AFFINITY.get(pickup_kind, {})

        def weight(gid, pool):
            base = _pickup_group_weight(gid, pool)
            if gid is None:
                return base
            kind = room_id_to_kind.get(gid)
            base *= affinity.get(kind, 1.0) ** S.PICKUP_AFFINITY_STRENGTH
            if gid in dead_end_rooms and not pickup_room_counts.get(gid):
                base *= S.PICKUP_DEAD_END_BONUS
            return base
        return weight

    _total_planned_pickups = (
        spec["n_collectible"] + spec.get("n_batteries", S.TOTAL_BATTERIES)
        + spec.get("n_lighters", 0) + spec.get("n_sanity_pills", 0)
        + spec.get("n_notes", S.TOTAL_NOTES)
    )
    _corridor_fraction = sum(1 for c in open_cells if _pickup_group(c) is None) / max(1, len(open_cells))
    pickup_group_caps = {None: max(1, round(_total_planned_pickups * max(0.05, 0.5 * _corridor_fraction)))}
    pickup_cells_taken = []
    pickup_room_counts = {}
    PICKUP_CROSS_GAP = 3

    collectible_cells = _spread_pick(open_cells, spec["n_collectible"], used, rng, min_gap=5,
                                      cell_to_group=_pickup_group, group_weight=weight_for(spec["collectible"]),
                                      group_caps=pickup_group_caps, taken=pickup_cells_taken,
                                      taken_gap=PICKUP_CROSS_GAP, group_counts=pickup_room_counts)
    for cell in collectible_cells:
        used.add(cell)
        props.append(_place_pickup(spec["collectible"], cell, surfaces_by_room, cell_to_room.get, rng,
                                    surface_occupied, blockers=props, on_surface_chance=0.82))

    battery_cells = _spread_pick(open_cells, spec.get("n_batteries", S.TOTAL_BATTERIES), used, rng, min_gap=3,
                                  cell_to_group=_pickup_group, group_weight=weight_for("battery"),
                                  group_caps=pickup_group_caps, taken=pickup_cells_taken,
                                  taken_gap=PICKUP_CROSS_GAP, group_counts=pickup_room_counts)
    for cell in battery_cells:
        used.add(cell)
        props.append(_place_pickup("battery", cell, surfaces_by_room, cell_to_room.get, rng,
                                    surface_occupied, blockers=props, on_surface_chance=0.82))

    lighter_cells = _spread_pick(open_cells, spec.get("n_lighters", 0), used, rng, min_gap=6,
                                  cell_to_group=_pickup_group, group_weight=weight_for("lighter"),
                                  group_caps=pickup_group_caps, taken=pickup_cells_taken,
                                  taken_gap=PICKUP_CROSS_GAP, group_counts=pickup_room_counts)
    for cell in lighter_cells:
        used.add(cell)
        props.append(_place_pickup("lighter", cell, surfaces_by_room, cell_to_room.get, rng,
                                    surface_occupied, blockers=props, on_surface_chance=0.7))

    for kind, count, gap in (("paper_map", spec.get("n_maps", 0), 6), ("pencil", spec.get("n_pencils", 0), 4),
                             ("map_sheet", spec.get("n_sheets", 0), 7)):
        cells = _spread_pick(open_cells, count, used, rng, min_gap=gap,
                             cell_to_group=_pickup_group, group_weight=weight_for(kind),
                             group_caps=pickup_group_caps, taken=pickup_cells_taken,
                             taken_gap=PICKUP_CROSS_GAP, group_counts=pickup_room_counts)
        for cell in cells:
            used.add(cell)
            props.append(_place_pickup(kind, cell, surfaces_by_room, cell_to_room.get, rng,
                                        surface_occupied, blockers=props, on_surface_chance=0.75))

    pill_cells = _spread_pick(open_cells, spec.get("n_sanity_pills", 0), used, rng, min_gap=6,
                               cell_to_group=_pickup_group, group_weight=weight_for("sanity_pill"),
                               group_caps=pickup_group_caps, taken=pickup_cells_taken,
                               taken_gap=PICKUP_CROSS_GAP, group_counts=pickup_room_counts)
    for cell in pill_cells:
        used.add(cell)
        props.append(_place_pickup("sanity_pill", cell, surfaces_by_room, cell_to_room.get, rng,
                                    surface_occupied, blockers=props, on_surface_chance=0.6))

    note_cells = _spread_pick(open_cells, spec.get("n_notes", S.TOTAL_NOTES), used, rng, min_gap=3,
                               cell_to_group=_pickup_group, group_weight=weight_for("note_flat"),
                               group_caps=pickup_group_caps, taken=pickup_cells_taken,
                               taken_gap=PICKUP_CROSS_GAP, group_counts=pickup_room_counts)
    note_texts = _notes_in_place(
        _notes_of_voice(NOTE_POOL[spec["key"]], anom["voice"], len(note_cells), rng),
        [room_id_to_kind.get(cell_to_room.get(c)) for c in note_cells], rng)
    for i, cell in enumerate(note_cells):
        used.add(cell)
        flat = _place_pickup("note_flat", cell, surfaces_by_room, cell_to_room.get, rng,
                              surface_occupied, blockers=props, on_surface_chance=0.82)
        flat.note_text = note_texts[i]
        props.append(flat)

    props.extend(_scatter_clutter(maze, reachable_rooms, props, rng, surface_occupied, used, dist,
                                  CORRIDOR_ROOM_KINDS, density=anom["clutter"]))

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
        broken.break_open(animate=False)
        doors.append(broken)

    for item in interior_door_specs:
        cell = tuple(item["cell"])
        if cell in chosen_door_cells:
            continue
        used.add(cell)
        chosen_door_cells.append(cell)
        kind = _resolve_random_door_kind(item["kind"], rng)
        if kind == "passage":
            continue
        d = Door(cell[0] + 0.5, cell[1] + 0.5, item["facing"])
        if kind == "broken":
            d.break_open(animate=False)
        doors.append(d)

    props.extend(_barricades(maze, doors, dist, used, rng, anom.get("barricades", 0)))

    ranked = sorted(dist.keys(), key=lambda c: dist[c])
    far = ranked[-max(1, len(ranked) // 4):]
    monster_cell = _pick_monster_spawn(maze, far, ranked, props, rng, doors)

    props = _finalize_physical_safety(maze, props, dist)
    _unplug_for_the_monster(maze, props, (int(maze.start[0]), int(maze.start[1])),
                            {"fuse_box", "valve_panel", "elevator", "hatch", "shed_lock", "fence_gap"})
    link_adjacent_pipes(props, maze)
    _block_shut_lockers(props)
    _break_some_lights(props, rng, chance=spec.get("broken_light_chance", S.BROKEN_LIGHT_CHANCE),
                       stage_mult=anom["lights"])
    props.extend(_emergency_lights(maze, props, doors, dist, used, rng, wing_specs,
                                   anom["emergency"]))
    props.extend(_dress_outer_yards(maze, rng))

    return props, panel_prop, exit_prop, monster_cell, doors


OUTER_YARD_KINDS = ("bush", "rock", "tree_stump", "fallen_log")
OUTER_YARD_WEIGHTS = (3.0, 2.2, 1.4, 0.8)
OUTER_YARD_REACH = {"bush": 0.67, "rock": 0.33, "tree_stump": 0.41, "fallen_log": 0.63}
OUTER_YARD_TREE_REACH = (0.255, 0.448, 0.530, 0.411)


def _dress_outer_yards(maze, rng):
    cells = [(x, y) for y in range(maze.h) for x in range(maze.w)
             if maze.grid[y][x] == S.WALL_OUTDOOR]
    if not cells:
        return []
    cell_set = set(cells)
    grounds = (S.WALL_OUTDOOR, S.WALL_FENCE, S.WALL_FOREST)

    def tile(x, y):
        return maze.grid[y][x] if 0 <= x < maze.w and 0 <= y < maze.h else S.WALL_FOREST

    four = ((1, 0), (-1, 0), (0, 1), (0, -1))
    inside = {c for c in cells
              if all((c[0] + dx, c[1] + dy) in cell_set
                     for dx in (-1, 0, 1) for dy in (-1, 0, 1))}
    glass = {c for c in cells if any(tile(c[0] + dx, c[1] + dy) == S.WALL_WINDOW for dx, dy in four)}
    facade = {c for c in cells if any(tile(c[0] + dx, c[1] + dy) not in grounds for dx, dy in four)}
    out = []

    def place(kind, c, reach, variant=None):
        keep = max(0.0, 0.5 - reach - 0.02)
        p = Prop(kind, c[0] + 0.5 + rng.uniform(-keep, keep),
                 c[1] + 0.5 + rng.uniform(-keep, keep),
                 facing=rng.uniform(0.0, math.tau))
        p.outdoor = True
        if variant is not None:
            p.variant = variant
        else:
            variants = PROP_DEFS[kind].get("variants", 1)
            if variants > 1:
                p.variant = rng.randrange(variants)
        out.append(p)
        return p

    seen = set()
    for first in cells:
        if first in seen:
            continue
        pocket, stack = [], [first]
        seen.add(first)
        while stack:
            c = stack.pop()
            pocket.append(c)
            for dx, dy in four:
                n = (c[0] + dx, c[1] + dy)
                if n in cell_set and n not in seen:
                    seen.add(n)
                    stack.append(n)
        pocket.sort()
        depth = {c: 0 for c in pocket if c in facade}
        faces_glass = {c: c in glass for c in depth}
        frontier = sorted(depth)
        while frontier:
            nxt = []
            for c in frontier:
                for dx, dy in four:
                    n = (c[0] + dx, c[1] + dy)
                    if n in cell_set and n not in depth:
                        depth[n] = depth[c] + 1
                        faces_glass[n] = faces_glass[c]
                        nxt.append(n)
            frontier = nxt
        length = max(1, len([c for c in pocket if c in facade]))
        want = max(1, int(round(length / float(S.OUTER_YARD_TREE_EVERY))))
        spots = [c for c in pocket if c not in glass] or list(pocket)
        rng.shuffle(spots)
        spots.sort(key=lambda c: (faces_glass.get(c, False), -depth.get(c, 0)))
        trees = []
        for c in spots:
            if len(trees) >= want:
                break
            if any((c[0] - t[0]) ** 2 + (c[1] - t[1]) ** 2 < S.OUTER_YARD_TREE_GAP ** 2 for t in trees):
                continue
            fits = [v for v, r in enumerate(OUTER_YARD_TREE_REACH) if c in inside or r <= 0.48]
            v = rng.choice(fits)
            place("tree", c, OUTER_YARD_TREE_REACH[v], variant=v)
            trees.append(c)
        free = [c for c in pocket if c not in glass and c not in trees]
        rng.shuffle(free)
        placed = 0
        for i, c in enumerate(free):
            if rng.random() >= S.OUTER_YARD_PROP_CHANCE and len(free) - i > S.OUTER_YARD_MIN_PROPS - placed:
                continue
            kinds = [k for k in OUTER_YARD_KINDS if c in inside or OUTER_YARD_REACH[k] <= 0.48]
            weights = [OUTER_YARD_WEIGHTS[OUTER_YARD_KINDS.index(k)] for k in kinds]
            kind = rng.choices(kinds, weights=weights)[0]
            place(kind, c, OUTER_YARD_REACH[kind])
            placed += 1
    return out


YARD_ON_SURFACE = 0.7
YARD_KEY_SURFACES = ("workbench", "table", "desk", "crate", "park_bench",
                     "reception_desk", "cabinet", "shelf", "kitchen_counter", "barrel")
YARD_KEY_SURFACE_REACH = 12.0
YARD_INDOOR_BIAS = {"key": 1.0, "battery": 0.75, "pencil": 0.75, "note_flat": 0.5}
YARD_INDOOR_LEAN = {"key": 6.0, "battery": 1.6, "pencil": 1.6, "note_flat": 1.3}


def indoor_cells(maze):
    door_cells = {(wx, wy) for zone in maze.zones
                  for wx, wy, _facing, _kind in zone["interior_doors"]}
    start = (int(maze.start[0]), int(maze.start[1]))
    seen = {start}
    stack = [start]
    while stack:
        cx, cy = stack.pop()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (cx + dx, cy + dy)
            if n in seen or n in door_cells:
                continue
            if not (0 <= n[0] < maze.w and 0 <= n[1] < maze.h):
                continue
            if maze.grid[n[1]][n[0]] != S.FLOOR:
                continue
            seen.add(n)
            stack.append(n)
    return {c for c in maze.floor_cells() if c not in seen and c not in door_cells}


def populate_yard(maze, spec, rng, stage=None):
    anom = S.anomaly_of(stage or S.ANOMALY_DEFAULT_STAGE)
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

    cell_to_zone = {}
    for zone in maze.zones:
        zx0, zy0, zx1, zy1 = zone["rect"]
        for zx in range(zx0, zx1):
            for zy in range(zy0, zy1):
                cell_to_zone[(zx, zy)] = id(zone)

    props = []
    used = {spawn}
    blocked_solid = set()

    dcx, dcy = door_cell
    facing = shed_zone["interior_doors"][0][2]
    lock = Prop("shed_lock", dcx + 0.5, dcy + 0.5, facing=facing)
    lock.interact_cell = door_cell
    props.append(lock)
    panel_prop = lock
    used.add(door_cell)
    blocked_solid.add(door_cell)
    dist = maze.bfs_distances(sx, sy, blocked=blocked_solid)

    yard_doors = []
    for zone in maze.zones:
        if zone["kind"] == "shed":
            continue
        for wx, wy, dfacing, dkind in zone["interior_doors"]:
            cell = (wx, wy)
            d = Door(wx + 0.5, wy + 0.5, dfacing,
                     height=(S.WALL_HEIGHTS.get(S.WALL_SHED, S.WALL_HEIGHT)
                             - S.YARD_EAVE_DROP - S.YARD_DOOR_CLEARANCE))
            if dkind == "broken":
                d.break_open(animate=False)
            yard_doors.append(d)
            used.add(cell)
    dist = maze.bfs_distances(sx, sy, blocked=blocked_solid)

    surfaces_by_zone = {}
    surface_occupied = {}
    pre_used = set(used)
    for zone in maze.zones:
        zone_id = id(zone)
        for entry in zone["furniture"]:
            kind, x, y, z0, ffacing = furniture_place(entry)
            cell = (int(x), int(y))
            if cell in pre_used and z0 <= 1e-6:
                continue
            prop = Prop(kind, x, y, facing=ffacing)
            prop.z0 = z0
            prop.interact_cell = cell
            prop.authored = True
            props.append(prop)
            used.add(cell)
            if PROP_DEFS[kind]["solid"]:
                blocked_solid |= cells_no_body_fits([prop], S.PLAYER_RADIUS)
            if kind in SURFACE_KINDS:
                surfaces_by_zone.setdefault(zone_id, []).append(prop)
            if z0 > 1e-6:
                item_r = max(PROP_DEFS[kind]["hw"], PROP_DEFS[kind]["hd"])
                surface_occupied.setdefault(cell, []).append((x, y, item_r))
    dist = maze.bfs_distances(sx, sy, blocked=blocked_solid)
    for zid in surfaces_by_zone:
        surfaces_by_zone[zid] = [s for s in surfaces_by_zone[zid] if _reachable_surface(s, dist)]
        for s_ in surfaces_by_zone[zid]:
            mark_surface_open_side(s_, dist)

    shed_interior = _walled_interior_cells(maze, shed_zone["rect"], door_cell)
    workbench = next((p for p in props if p.kind == "workbench" and p.interact_cell in shed_interior), None)
    if workbench is None:
        shed_interior_cells = [c for c in shed_interior if c not in used]
        if shed_interior_cells:
            wcell = shed_interior_cells[0]
            workbench = Prop("workbench", wcell[0] + 0.5, wcell[1] + 0.5, facing=0.0)
            workbench.interact_cell = wcell
            props.append(workbench)
            used.add(wcell)
            blocked_solid |= cells_no_body_fits([workbench], S.PLAYER_RADIUS)
            dist = maze.bfs_distances(sx, sy, blocked=blocked_solid)
    if workbench is not None:
        cutters_hw = PROP_DEFS["cutters"]["hw"]
        cx_spot, cy_spot = _pick_surface_spot(workbench, cutters_hw, rng, {})
        cutters = Prop("cutters", cx_spot, cy_spot, facing=rng.uniform(0, math.tau))
        cutters.z0 = _surface_top_z0(workbench, rng)
        props.append(cutters)
    else:
        shed_interior_cells = [c for c in shed_interior if c not in used]
        if shed_interior_cells:
            ccell = shed_interior_cells[0]
            props.append(make_prop("cutters", ccell, facing=rng.uniform(0, math.tau)))
            used.add(ccell)

    def in_a_zone(c):
        return cell_to_zone.get(c) is not None

    yard_surfaces = [s for group in surfaces_by_zone.values() for s in group]

    near_surface_cells = set()
    for s_ in yard_surfaces:
        reach = int(math.ceil(PICKUP_SURFACE_REACH)) + 1
        for iy in range(int(s_.y) - reach, int(s_.y) + reach + 1):
            for ix in range(int(s_.x) - reach, int(s_.x) + reach + 1):
                if math.hypot(s_.x - (ix + 0.5), s_.y - (iy + 0.5)) <= PICKUP_SURFACE_REACH:
                    near_surface_cells.add((ix, iy))

    def near_a_surface(c):
        return c in near_surface_cells

    zone_kind_by_id = {id(z): z["kind"] for z in maze.zones}
    indoor = indoor_cells(maze)

    def zone_weight_for(pickup_kind):
        affinity = ZONE_PICKUP_AFFINITY.get(pickup_kind, {})
        indoor_lean = YARD_INDOOR_LEAN.get(pickup_kind, 1.0)

        def weight(gid, pool):
            base = math.sqrt(float(len(pool)))
            lean = affinity.get(zone_kind_by_id.get(gid), 1.0)
            if indoor_lean != 1.0 and any(c in indoor for c in pool):
                lean *= indoor_lean
            return base * lean ** S.PICKUP_AFFINITY_STRENGTH
        return weight

    def indoor_rank(pickup_kind):
        bias = YARD_INDOOR_BIAS.get(pickup_kind, 0.0)

        def rank(c):
            return 1.0 if (c in indoor and rng.random() < bias) else 0.0
        return rank

    def yard_spots(extra=None):
        pool = [c for c in yard_cells
                if c not in used and in_a_zone(c) and (extra is None or extra(c))]
        near = [c for c in pool if near_a_surface(c)]
        return near if len(near) >= 12 else (pool or
                                             [c for c in yard_cells if c not in used])

    open_yard = yard_spots(lambda c: dist.get(c, 0) >= 2)
    pickup_cells_taken = []
    pickup_zone_counts = {}
    house_cells = [c for c in yard_cells
                   if c in indoor and c not in used and dist.get(c, 0) >= 2 and in_a_zone(c)]
    house_zones = {cell_to_zone.get(c) for c in house_cells}
    key_pool = house_cells if len(house_cells) >= spec["n_collectible"] * 3 else open_yard
    key_caps = ({gid: 1 for gid in house_zones}
                if key_pool is house_cells and len(house_zones) >= spec["n_collectible"] else None)
    key_cells = _spread_pick(key_pool, spec["n_collectible"], used, rng, min_gap=5, cell_to_group=cell_to_zone.get,
                                 group_weight=zone_weight_for('key'), cell_rank=indoor_rank('key'),
                             group_caps=key_caps,
                             taken=pickup_cells_taken, taken_gap=4, group_counts=pickup_zone_counts)
    for cell in key_cells:
        used.add(cell)
        props.append(_place_pickup(spec["collectible"], cell, surfaces_by_zone, cell_to_zone.get, rng,
                                    surface_occupied, blockers=props, on_surface_chance=1.0,
                                    reach=YARD_KEY_SURFACE_REACH, prefer=YARD_KEY_SURFACES,
                                    allow=(lambda s: (int(s.x), int(s.y)) in indoor)
                                    if key_pool is house_cells else None))

    exits = _place_fence_gaps(maze, spec.get("n_fence_gaps", S.YARD_FENCE_GAPS), yard_cells, dist, spawn,
                               used, blocked_solid, props, rng)
    exit_prop = exits[0] if exits else None

    battery_cells = _spread_pick(yard_spots(), S.TOTAL_BATTERIES, used, rng, min_gap=3,
                                 group_weight=zone_weight_for('battery'), cell_rank=indoor_rank('battery'),
                                  cell_to_group=cell_to_zone.get, taken=pickup_cells_taken, taken_gap=4,
                                  group_counts=pickup_zone_counts)
    for cell in battery_cells:
        used.add(cell)
        props.append(_place_pickup("battery", cell, surfaces_by_zone, cell_to_zone.get, rng,
                                    surface_occupied, blockers=props, on_surface_chance=YARD_ON_SURFACE))

    pencil_cells = _spread_pick(yard_spots(), spec.get("n_pencils", 0), used, rng,
                                 group_weight=zone_weight_for('pencil'), cell_rank=indoor_rank('pencil'),
                                 min_gap=4, cell_to_group=cell_to_zone.get, taken=pickup_cells_taken, taken_gap=4,
                                 group_counts=pickup_zone_counts)
    for cell in pencil_cells:
        used.add(cell)
        props.append(_place_pickup("pencil", cell, surfaces_by_zone, cell_to_zone.get, rng,
                                    surface_occupied, blockers=props, on_surface_chance=YARD_ON_SURFACE))

    note_cells = _spread_pick(yard_spots(), S.TOTAL_NOTES, used, rng, min_gap=3,
                                 group_weight=zone_weight_for('note_flat'), cell_rank=indoor_rank('note_flat'),
                               cell_to_group=cell_to_zone.get, taken=pickup_cells_taken, taken_gap=4,
                               group_counts=pickup_zone_counts)
    note_texts = _notes_in_place(
        _notes_of_voice(NOTE_POOL[spec["key"]], anom["voice"], len(note_cells), rng),
        [zone_kind_by_id.get(cell_to_zone.get(c)) for c in note_cells], rng)
    for i, cell in enumerate(note_cells):
        used.add(cell)
        flat = _place_pickup("note_flat", cell, surfaces_by_zone, cell_to_zone.get, rng,
                              surface_occupied, blockers=props, on_surface_chance=YARD_ON_SURFACE)
        flat.note_text = note_texts[i]
        props.append(flat)

    ranked = sorted((c for c in dist.keys() if not in_shed(c) and c not in indoor),
                    key=lambda c: dist[c])
    far = ranked[-max(1, len(ranked) // 4):]
    monster_cell = _pick_monster_spawn(maze, far, ranked, props, rng, yard_doors)

    props = _finalize_physical_safety(maze, props, dist)
    _unplug_for_the_monster(maze, props, (int(maze.start[0]), int(maze.start[1])),
                            {"fuse_box", "valve_panel", "elevator", "hatch", "shed_lock", "fence_gap"})
    link_adjacent_pipes(props, maze)
    _block_shut_lockers(props)
    _break_some_lights(props, rng, chance=spec.get("broken_light_chance", S.BROKEN_LIGHT_CHANCE),
                       stage_mult=anom["lights"])

    return props, panel_prop, exit_prop, monster_cell, yard_doors


def _far_corner_cell(maze):
    best, best_d = (1, 1), -1.0
    cx, cy = maze.w / 2.0, maze.h / 2.0
    for y in range(maze.h):
        for x in range(maze.w):
            if not maze.is_walkable_cell(x, y):
                continue
            d = math.hypot(x + 0.5 - cx, y + 0.5 - cy)
            if d > best_d:
                best, best_d = (x, y), d
    return best


def populate_micro_yard(maze, rng):
    sx, sy = int(maze.start[0]), int(maze.start[1])
    exit_floor_cell = (sx, 2)
    candidates = _fence_wall_cells(maze, exit_floor_cell)
    boundary, facing, fc = candidates[0]
    hd = PROP_DEFS["fence_gap"]["hd"]
    x, y = _wall_mount_position(boundary, facing, hd)
    exit_prop = Prop("fence_gap", x, y, facing=facing)
    exit_prop.interact_cell = fc
    exit_prop.powered = True
    props = [exit_prop]
    _mark_fence_gap_edges(maze, [exit_prop])
    return props, None, exit_prop, _far_corner_cell(maze), []


def populate_micro_room(maze, exit_kind, rng):
    sx, sy = int(maze.start[0]), int(maze.start[1])
    if PROP_DEFS[exit_kind]["wall_mounted"]:
        wall_cell = (sx, 1)
        exit_prop = _wall_prop(maze, wall_cell, exit_kind, rng)
        if exit_prop is None:
            raise RuntimeError(f"couldn't place wall-mounted '{exit_kind}' in the micro room")
    else:
        exit_prop = make_prop(exit_kind, (sx, sy), facing=0.0)
        exit_prop.interact_cell = (sx, sy)
    exit_prop.powered = True
    return [exit_prop], None, exit_prop, _far_corner_cell(maze), []


def populate_forest_run(maze, rng):
    period = S.FOREST_RUN_PERIOD
    cx = maze.start[0]
    n_periods = max(1, int(maze.h / period) + 1)
    pattern_rng = random.Random(0xF03E57)
    half_w = maze.w / 2 - 0.6
    pattern = []

    def add(n, dx_lo, dx_hi, kinds):
        for _ in range(n):
            side = pattern_rng.choice((-1.0, 1.0))
            dx = side * pattern_rng.uniform(dx_lo, dx_hi)
            dy = pattern_rng.uniform(0.0, period)
            kind = pattern_rng.choice(kinds)
            facing = pattern_rng.uniform(0.0, math.tau)
            scale = pattern_rng.uniform(0.75, 1.4)
            pattern.append((dx, dy, kind, facing, scale))

    add(10, 1.6, 2.6, ("bush", "bush", "rock"))
    add(26, 2.8, 4.6, ("tree", "tree", "tree", "bush"))
    add(18, 4.4, half_w, ("tree", "tree", "bush", "rock"))

    props = []
    gap = None
    row_y = getattr(maze, "fence_row_y", None)
    if row_y is not None:
        hd = PROP_DEFS["fence_gap"]["hd"]
        gx = int(cx)
        facing = -math.pi / 2
        x, y = _wall_mount_position((gx + 0.5, float(row_y)), facing, hd)
        gap = Prop("fence_gap", x, y, facing=facing)
        gap.cut = True
        gap.interact_cell = (gx, row_y - 1)
        props.append(gap)
        _mark_fence_gap_edges(maze, [gap])
        maze.fence_blank_edges = {((x, row_y), (0, 1)) for x in range(1, maze.w - 1)}
    echo = Prop("asylum_echo", cx, S.FOREST_RUN_START + S.FOREST_RUN_LOOP + S.FOREST_ECHO_VANISH,
                facing=-math.pi / 2)
    echo.ghost_alpha = S.FOREST_ECHO_ALPHA
    props.append(echo)
    for k in range(n_periods):
        base_y = k * period
        for dx, dy, kind, facing, scale in pattern:
            x, y = cx + dx, base_y + dy
            if not (0.6 <= x <= maze.w - 0.6 and 0.6 <= y <= maze.h - 0.6):
                continue
            p = Prop(kind, x, y, facing=facing)
            p.hw *= scale
            p.hd *= scale
            p.height *= scale
            p.collide_hw *= scale
            p.collide_hd *= scale
            props.append(p)
    monster_cell = (int(cx), int(maze.start[1]))
    return props, None, None, monster_cell, []


def populate_debug(maze, rng):
    props = []
    wall_kinds = sorted(k for k, v in PROP_DEFS.items() if v["wall_mounted"])
    skip = {"portal", "portal_live", "asylum_echo"}
    floor_kinds = sorted(k for k, v in PROP_DEFS.items() if not v["wall_mounted"] and k not in skip)

    gx, gy = getattr(maze, "debug_origin", (0.0, 0.0))
    wall_y = float(gy)
    wx = gx + 0.3
    for kind in wall_kinds:
        spec = PROP_DEFS[kind]
        for variant in range(spec.get("variants", 1)):
            wx += spec["hw"]
            x, y = _wall_mount_position((wx, wall_y), math.pi / 2, spec["hd"])
            p = Prop(kind, x, y, facing=math.pi / 2)
            p.variant = variant
            props.append(p)
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
    x0, y0 = gx + 0.6, gy + 1.6
    floor_items = [(k, v) for k in floor_kinds for v in range(PROP_DEFS[k].get("variants", 1))]
    for i, (kind, variant) in enumerate(floor_items):
        row, col = divmod(i, cols)
        cell = (int(x0 + col * spacing), int(y0 + row * spacing))
        note_text = NOTE_POOL["floor0"][0] if kind == "note_flat" else None
        p = make_prop(kind, cell, facing=0.0, note_text=note_text)
        p.variant = variant
        props.append(p)

    broken_floor_kinds = sorted(k for k in BREAKABLE_LIGHT_KINDS if not PROP_DEFS[k]["wall_mounted"])
    for j, kind in enumerate(broken_floor_kinds):
        row, col = divmod(len(floor_items) + j, cols)
        cell = (int(x0 + col * spacing), int(y0 + row * spacing))
        p = make_prop(kind, cell, facing=0.0)
        p.broken = True
        props.append(p)

    total_floor_items = len(floor_items) + len(broken_floor_kinds)
    rows = -(-total_floor_items // cols)
    door_row_y = int(y0 + (rows + 1) * spacing)
    d_closed = Door(x0 + 0.5, door_row_y + 0.5, 0.0)
    d_open = Door(x0 + 2.3, door_row_y + 0.5, 0.0)
    d_open.is_open = True
    d_open.swing = 1.0
    d_open._swing_target = 1.0
    d_broken = Door(x0 + 4.1, door_row_y + 0.5, 0.0)
    d_broken.break_open(animate=False)
    doors = [d_closed, d_open, d_broken]

    portal_row_y = door_row_y + 2 * spacing
    for i in range(3):
        p = make_prop("portal", (int(x0 + i * 2 * spacing), int(portal_row_y)), facing=-math.pi / 2)
        p.target_floor = i
        p.portal_live = False
        props.append(p)
    for i in range(3):
        p = make_prop("portal_live", (int(x0 + i * 2 * spacing), int(portal_row_y + spacing)),
                       facing=-math.pi / 2)
        p.target_floor = i
        p.portal_live = True
        props.append(p)

    demo_spots = []
    if maze.showcase_rect is not None:
        sx0, sy0, sx1, sy1 = maze.showcase_rect
        wall_y_n, wall_y_s = sy0, sy1

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
            standoff = lk.hd + 0.34 + extra
            return lk.x + math.cos(lk.facing) * standoff, lk.y + math.sin(lk.facing) * standoff

        usable_top = sy0 + 4
        usable_bot = sy1 - 2
        span = max(1, usable_bot - usable_top)
        patrol_row, investigate_row, hunt_row, stalk_row = (
            int(usable_top + span * frac) for frac in (0.15, 0.40, 0.65, 0.90)
        )

        investigate_locker = _mount_demo_locker(investigate_row)
        stalk_locker = _mount_demo_locker(stalk_row)

        demo_spots.append(dict(
            start=(sx0 + 4.0, patrol_row + 0.5), target=(sx0 + 1.5, patrol_row + 0.5),
            has_locker=False, speed=S.MONSTER_BASE_SPEED, alert_target=0.0,
        ))
        if investigate_locker is not None:
            demo_spots.append(dict(
                start=_locker_stand_point(investigate_locker, extra=3.0),
                target=_locker_stand_point(investigate_locker),
                has_locker=True, speed=S.MONSTER_BASE_SPEED, alert_target=0.4,
                check_seconds=S.MONSTER_LOCKER_CHECK_SECONDS, locker_target=investigate_locker,
            ))
        demo_spots.append(dict(
            start=(sx0 + 4.0, hunt_row + 0.5), target=(sx0 + 1.5, hunt_row + 0.5),
            has_locker=False, speed=S.MONSTER_HUNT_SPEED, alert_target=1.0,
        ))
        if stalk_locker is not None:
            demo_spots.append(dict(
                start=_locker_stand_point(stalk_locker, extra=3.0),
                target=_locker_stand_point(stalk_locker),
                has_locker=True, speed=S.MONSTER_STALK_APPROACH_SPEED, alert_target=1.0,
                check_seconds=S.MONSTER_STALK_OPEN_SECONDS, locker_target=stalk_locker,
            ))
    maze.demo_monster_spots = demo_spots

    if maze.showcase_rect is not None:
        corridor_row = sy0 + 3
        corridor_x0 = sx0 + 4
        corridor_cells = [(corridor_x0 + i, corridor_row) for i in range(10)]
        door_cell = corridor_cells[5]
        doors.append(Door(door_cell[0] + 0.5, door_cell[1] + 0.5, 0.0))
        maze.door_demo_corridor = corridor_cells

    if maze.surface_showcase_rect is not None:
        fx0, fy0, fx1, fy1 = maze.surface_showcase_rect
        showcase_items = (SHOWCASE_ITEMS + CLUTTER_KINDS
                          + ("battery", "note_flat", "pencil", "map_sheet", "sanity_pill",
                             "key", "fuse", "valve_key", "lighter", "cutters"))
        rows = []
        for kind in sorted(SURFACE_KINDS):
            for var in range(max(1, PROP_DEFS[kind].get("variants", 1))):
                rows.append((kind, var))
        spacing_x, spacing_y = 2.2, 2.6
        ox, oy = fx0 + 1.4, fy0 + 1.6
        demo_surface_occupied = {}

        def put_surface(kind, var, cx, cy, carry):
            surf = Prop(kind, cx, cy, facing=0.0)
            surf.variant = var
            surf.interact_cell = ("surface_showcase", kind, var, cx)
            props.append(surf)
            for item_kind in carry:
                item_hw = PROP_DEFS[item_kind]["hw"]
                ix, iy = _pick_surface_spot(surf, item_hw, rng, demo_surface_occupied)
                item = Prop(item_kind, ix, iy, facing=rng.uniform(0, math.tau),
                            note_text=NOTE_POOL["floor0"][0] if item_kind == "note_flat" else None)
                item.z0 = _surface_top_z0(surf, rng)
                props.append(item)

        for r, (kind, var) in enumerate(rows):
            cy = oy + r * spacing_y
            if cy > fy1 - 1.0:
                break
            for c, item_kind in enumerate(showcase_items):
                cx = ox + c * spacing_x
                if cx > fx1 - 1.0:
                    break
                put_surface(kind, var, cx, cy, (item_kind,))
            else:
                mix = rng.sample(list(showcase_items), rng.randint(2, 3))
                put_surface(kind, var, ox + len(showcase_items) * spacing_x, cy, mix)

    monster_cell = (maze.w - 3, maze.h - 3)
    return props, None, None, monster_cell, doors
