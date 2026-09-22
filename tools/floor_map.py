import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SDL_VIDEODRIVER", "offscreen")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from game import settings as S
from game.maze import Maze
from game.props import populate_level, populate_yard, NOTE_AWARE

SCALE = 7
PAD = 8
HEADER = 46
LEGEND_W = 232

WALL_PLAIN = (25, 25, 29)
WALL_COL = {
    S.WALL_WINDOW: (72, 132, 158),
    S.WALL_BARS: (128, 128, 140),
    S.WALL_FENCE: (52, 52, 60),
    S.WALL_FOREST: (20, 30, 20),
    S.WALL_SHED: (96, 76, 52),
    S.WALL_BRICK: (104, 62, 50),
}
WING_COL = {
    "wards": (124, 108, 84), "admin": (84, 100, 126), "back": (80, 116, 92),
    "plant": (132, 100, 66), "stores": (108, 108, 108), "cells": (132, 84, 84),
    None: (86, 86, 90),
}
ZONE_COL = {
    "open": (78, 94, 62), "forest": (44, 62, 44), "pen": (86, 96, 66),
    "alley": (92, 90, 80), "dump": (92, 80, 64), "ruin": (86, 82, 78),
    "plant": (128, 96, 64), "shed": (118, 92, 60), "tool_shed": (112, 88, 58),
    "storage": (104, 100, 92), "greenhouse": (72, 110, 88),
    "chapel": (108, 96, 116), "morgue_dock": (120, 84, 84),
}
CORRIDOR_KINDS = {"corridor", "tech_corridor", "vent", "link"}
MARKS = [
    ("exit",        (255, 210, 60),  "way out"),
    ("unlocker",    (120, 200, 255), "objective"),
    ("start",       (255, 255, 255), "way in"),
    ("echo",        (255, 90, 200),  "the repeated room (a pair)"),
    ("blind",       (255, 70, 60),   "door onto a wall"),
    ("barricade",   (200, 140, 70),  "barricade"),
    ("emergency",   (120, 255, 150), "emergency lamp"),
    ("note_aware",  (255, 255, 255), "note: they knew"),
    ("note_plain",  (150, 150, 150), "note: they did not"),
]


def draw_floor(maze, props, spec, stage, seed, font, small):
    w, h = maze.w, maze.h
    surf = pygame.Surface((w * SCALE + PAD * 2 + LEGEND_W, h * SCALE + PAD * 2 + HEADER))
    surf.fill((16, 16, 18))
    ox, oy = PAD, PAD + HEADER

    owner = {}
    for room in maze.rooms:
        for c in maze.room_cells(room):
            owner.setdefault(c, room)
    zone_of = {}
    for zone in getattr(maze, "zones", ()):
        zx0, zy0, zx1, zy1 = zone["rect"]
        for zx in range(zx0, zx1):
            for zy in range(zy0, zy1):
                zone_of[(zx, zy)] = zone["kind"]

    for y in range(h):
        for x in range(w):
            tile = maze.grid[y][x]
            if tile == S.FLOOR and zone_of:
                col = ZONE_COL.get(zone_of.get((x, y)), (70, 78, 62))
            elif tile == S.FLOOR:
                room = owner.get((x, y))
                col = WING_COL.get(room.get("wing") if room else None, WING_COL[None])
                if room is not None and room["kind"] in CORRIDOR_KINDS:
                    col = tuple(int(c * 0.78) for c in col)
                elif room is not None and room.get("spine"):
                    col = tuple(min(255, int(c * 1.3)) for c in col)
            else:
                col = WALL_COL.get(tile, WALL_PLAIN)
            surf.fill(col, (ox + x * SCALE, oy + y * SCALE, SCALE, SCALE))

    def dot(cell, col, r=3):
        pygame.draw.circle(surf, col, (ox + int(cell[0] * SCALE + SCALE / 2),
                                       oy + int(cell[1] * SCALE + SCALE / 2)), r)

    def ring(cell, col, r=5):
        pygame.draw.circle(surf, col, (ox + int(cell[0] * SCALE + SCALE / 2),
                                       oy + int(cell[1] * SCALE + SCALE / 2)), r, 2)

    for room in maze.rooms:
        if room["kind"] in CORRIDOR_KINDS:
            continue
        rx0, ry0, rx1, ry1 = room["rect"]
        rect = (ox + rx0 * SCALE, oy + ry0 * SCALE,
                (rx1 - rx0) * SCALE, (ry1 - ry0) * SCALE)
        pygame.draw.rect(surf, (26, 26, 30), rect, 1)
        if room.get("echo"):
            pygame.draw.rect(surf, (255, 90, 200), rect, 2)
        lab = small.render(room["kind"], True, (198, 198, 198))
        if lab.get_width() < rect[2] - 2:
            surf.blit(lab, (rect[0] + 3, rect[1] + 2))
        if room["kind"] == "exit":
            ring(((rx0 + rx1) / 2.0 - 0.5, (ry0 + ry1) / 2.0 - 0.5), (255, 210, 60), 8)
        if room["kind"] == "unlocker":
            ring(((rx0 + rx1) / 2.0 - 0.5, (ry0 + ry1) / 2.0 - 0.5), (120, 200, 255), 8)

    for cell, _facing, _kind in getattr(maze, "template_doors", []):
        ways = sum(1 for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                   if maze.is_walkable_cell(cell[0] + dx, cell[1] + dy))
        if ways <= 1:
            ring(cell, (255, 70, 60), 6)
        else:
            dot(cell, (150, 130, 70), 1)

    for p in props:
        cell = (int(p.x), int(p.y))
        if p.kind == "barricade":
            dot(cell, (200, 140, 70), 3)
        elif p.kind == "emergency_lamp":
            dot(cell, tuple(min(255, int(c * 255)) for c in p.light_color), 2)
        elif p.kind == "note_flat" and p.note_text:
            dot(cell, (255, 255, 255) if p.note_text in NOTE_AWARE else (150, 150, 150), 2)
    dot((int(maze.start[0]), int(maze.start[1])), (255, 255, 255), 4)

    for zone in getattr(maze, "zones", ()):
        zx0, zy0, zx1, zy1 = zone["rect"]
        rect = (ox + zx0 * SCALE, oy + zy0 * SCALE,
                (zx1 - zx0) * SCALE, (zy1 - zy0) * SCALE)
        pygame.draw.rect(surf, (28, 30, 26), rect, 1)
        surf.blit(small.render(zone["kind"], True, (228, 224, 200)), (rect[0] + 4, rect[1] + 3))

    anom = S.anomaly_of(stage)
    parts = len(maze.zones) if getattr(maze, "zones", None) else len(maze.rooms)
    title = "%s   seed %d   stage %d (%s)   %d %s, %d cells" % (
        spec["key"], seed, stage, anom["key"], parts,
        "patches" if getattr(maze, "zones", None) else "rooms", len(maze.floor_cells()))
    surf.blit(font.render(title, True, (235, 230, 210)), (PAD, 8))
    surf.blit(small.render(
        "a plan of one generated floor - tools/floor_map.py", True, (128, 128, 132)),
        (PAD, 28))

    lx = ox + w * SCALE + 12
    ly = oy + 4
    for key, col, label in MARKS:
        pygame.draw.circle(surf, col, (lx + 7, ly + 6), 5, 0 if key not in ("exit", "unlocker", "blind") else 2)
        surf.blit(small.render(label, True, (205, 205, 205)), (lx + 20, ly))
        ly += 18
    ly += 8
    if getattr(maze, "zones", None):
        seen_kinds = []
        for zone in maze.zones:
            if zone["kind"] not in seen_kinds:
                seen_kinds.append(zone["kind"])
        for kind in sorted(seen_kinds):
            surf.fill(ZONE_COL.get(kind, (70, 78, 62)), (lx, ly + 2, 14, 10))
            surf.blit(small.render(kind, True, (205, 205, 205)), (lx + 20, ly))
            ly += 17
    wings = S.FLOOR_WINGS.get(spec.get("floor_theme"), ())
    for wing in wings:
        surf.fill(WING_COL.get(wing["key"], WING_COL[None]), (lx, ly + 2, 14, 10))
        surf.blit(small.render("wing %s" % wing["key"], True, (205, 205, 205)), (lx + 20, ly))
        ly += 18
    ly += 8
    for note in ("brighter fill = the spine",
                 "darker fill = corridors",
                 "pink outline = the same room twice"):
        surf.blit(small.render(note, True, (150, 150, 150)), (lx, ly))
        ly += 16
    return surf


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "qa_out", "maps")
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    os.makedirs(out_dir, exist_ok=True)
    pygame.init()
    font = pygame.font.SysFont("dejavusans", 16)
    small = pygame.font.SysFont("dejavusans", 12)
    for spec in S.FLOOR_SPECS:
        yard = spec.get("layout", "corridor") == "yard"
        for stage in ((1, 2) if yard else (1, 4)):
            rng = random.Random(seed ^ 0x5EED)
            if yard:
                maze = Maze(w=S.YARD_W, h=S.YARD_H, seed=seed, layout="yard", anomaly=stage)
                props = populate_yard(maze, spec, rng, stage=stage)[0]
            else:
                maze = Maze(seed=seed, wall_bias=spec["wall_bias"],
                            template_floor=spec.get("floor_theme"),
                            room_count_range=spec.get("room_count"), anomaly=stage)
                props = populate_level(maze, spec, rng, stage=stage)[0]
            surf = draw_floor(maze, props, spec, stage, seed, font, small)
            path = os.path.join(out_dir, "%s_stage%d.png" % (spec["key"], stage))
            pygame.image.save(surf, path)
            print("wrote %s  (%dx%d)" % (path, *surf.get_size()))


if __name__ == "__main__":
    main()
