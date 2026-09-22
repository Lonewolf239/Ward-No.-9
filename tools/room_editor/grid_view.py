import math

import pygame

from game import settings as S
from game.maze import bars_arms_on
from game.props import PROP_DEFS
from tools.room_editor.furniture_model import entry as furn_entry
from game import ui

BG = (14, 12, 12)
FLOOR_COLOR = (138, 130, 120)
WALL_COLOR = (46, 40, 38)
BORDER_COLOR = ui.ROW_BORDER
DOOR_COLORS = {
    "passage": (90, 200, 120), 
    "door": (230, 190, 60), 
    "broken": (205, 95, 60), 
    "random": (170, 80, 220)
}
WINDOW_COLOR = (100, 200, 240)
BARS_COLOR = (120, 124, 134)
BRICK_COLOR = (132, 74, 58)
ENCLOSED_HATCH = (92, 92, 100)
FURNITURE_COLOR = (110, 150, 230)
QUEST_COLOR = (235, 140, 40)
HOVER_COLOR = (255, 255, 255)
LABEL_COLOR = (15, 15, 18)


class GridView:
    def __init__(self, rect):
        self.rect = rect
        self.cell_px = 32
        self.origin = (rect.x, rect.y)
        pygame.font.init()
        self.font = ui.font(12)

    def _layout(self, model):
        margin = 16
        avail_w = max(1, self.rect.w - margin * 2)
        avail_h = max(1, self.rect.h - margin * 2)
        self.cell_px = max(10, min(56, avail_w // model.w, avail_h // model.h))
        gw, gh = model.w * self.cell_px, model.h * self.cell_px
        self.origin = (self.rect.x + (self.rect.w - gw) // 2, self.rect.y + (self.rect.h - gh) // 2)

    def cell_at(self, mx, my, model):
        if not self.rect.collidepoint(mx, my):
            return None
        self._layout(model)
        ox, oy = self.origin
        gw, gh = model.w * self.cell_px, model.h * self.cell_px
        if not (ox <= mx < ox + gw and oy <= my < oy + gh):
            return None
        return ((mx - ox) // self.cell_px, (my - oy) // self.cell_px)

    def point_at(self, mx, my, model):
        if not self.rect.collidepoint(mx, my):
            return None
        self._layout(model)
        ox, oy = self.origin
        return ((mx - ox) / self.cell_px, (my - oy) / self.cell_px)

    def screen_of(self, lx, ly, model):
        self._layout(model)
        ox, oy = self.origin
        return ox + lx * self.cell_px, oy + ly * self.cell_px

    _ROD_STEP = 5

    def _draw_bars(self, surf, model, x, y, ox, oy, cp):
        def is_bars(cx, cy):
            return (0 <= cx < model.w and 0 <= cy < model.h
                    and model.cells[cy][cx] == S.WALL_BARS)

        def is_open(cx, cy):
            return (0 <= cx < model.w and 0 <= cy < model.h
                    and model.cells[cy][cx] == S.FLOOR)

        arms = bars_arms_on(x, y, is_bars, is_open)
        cx = ox + x * cp + cp // 2
        cy = oy + y * cp + cp // 2
        half = (cp + 1) // 2
        thick = max(2, cp // 7)
        for dx, dy in arms:
            ex, ey = cx + dx * half, cy + dy * half
            pygame.draw.line(surf, WALL_COLOR, (cx, cy), (ex, ey), thick)
            n = max(1, half // self._ROD_STEP)
            for i in range(1, n + 1):
                t = i / float(n + 1)
                px, py = cx + dx * half * t, cy + dy * half * t
                tx, ty = -dy, dx
                pygame.draw.line(surf, BARS_COLOR,
                                 (px - tx * thick, py - ty * thick),
                                 (px + tx * thick, py + ty * thick), 1)
        if len(arms) > 2 or (len(arms) == 2 and arms[0][0] != -arms[1][0]):
            pygame.draw.circle(surf, WALL_COLOR, (cx, cy), max(2, thick))

    def draw(self, surf, model, hover_cell, quest_marker_kinds):
        self._layout(model)
        pygame.draw.rect(surf, BG, self.rect)
        ox, oy = self.origin
        cp = self.cell_px

        enclosed = model.bar_enclosed_cells() if hasattr(model, "bar_enclosed_cells") else set()
        for y in range(model.h):
            for x in range(model.w):
                r = pygame.Rect(ox + x * cp, oy + y * cp, cp - 1, cp - 1)
                tile = model.cells[y][x]
                colour = (FLOOR_COLOR if tile == S.FLOOR else BRICK_COLOR if tile == S.WALL_BRICK
                          else WINDOW_COLOR if tile == S.WALL_WINDOW
                          else BARS_COLOR if tile == S.WALL_BARS else WALL_COLOR)
                pygame.draw.rect(surf, colour, r)
                if tile == S.WALL_BARS:
                    self._draw_bars(surf, model, x, y, ox, oy, cp)
                elif (x, y) in enclosed:
                    for k in range(0, r.w + r.h, 6):
                        pygame.draw.line(surf, ENCLOSED_HATCH, (r.x + max(0, k - r.h), r.y + min(k, r.h) - 1),
                                         (r.x + min(k, r.w) - 1, r.y + max(0, k - r.w)), 1)

        for info in model.doors.values():
            dx, dy = info["cell"]
            r = pygame.Rect(ox + dx * cp, oy + dy * cp, cp - 1, cp - 1)
            pygame.draw.rect(surf, DOOR_COLORS[info["kind"]], r)

        for dx, dy, facing, kind in model.interior_doors:
            r = pygame.Rect(ox + dx * cp, oy + dy * cp, cp - 1, cp - 1)
            pygame.draw.rect(surf, DOOR_COLORS[kind], r)

        for item in sorted(model.furniture, key=lambda f: float(f[3])):
            kind, fx, fy, fz, facing = furn_entry(item)
            spec = PROP_DEFS[kind]
            c, sn = math.cos(facing), math.sin(facing)
            hw, hd = spec["hw"] * cp, spec["hd"] * cp
            cx, cy = ox + fx * cp, oy + fy * cp
            pts = [(cx - sn * hw * sw + c * hd * sd, cy + c * hw * sw + sn * hd * sd)
                   for sw, sd in ((1, 1), (1, -1), (-1, -1), (-1, 1))]
            is_quest = kind in quest_marker_kinds
            col = QUEST_COLOR if is_quest else FURNITURE_COLOR
            pygame.draw.polygon(surf, col, pts)
            if fz > 0.02:
                pygame.draw.circle(surf, LABEL_COLOR, (int(cx), int(cy)), max(2, cp // 9))
            pygame.draw.line(surf, LABEL_COLOR, (cx, cy),
                             (cx + c * hd * 1.15, cy + sn * hd * 1.15), 1)
            if cp >= 22:
                txt = self.font.render(kind[:3], True, LABEL_COLOR)
                surf.blit(txt, (int(cx) - txt.get_width() // 2, int(cy) - txt.get_height() // 2))

        if hover_cell is not None:
            hx, hy = hover_cell
            if 0 <= hx < model.w and 0 <= hy < model.h:
                r = pygame.Rect(ox + hx * cp, oy + hy * cp, cp - 1, cp - 1)
                pygame.draw.rect(surf, HOVER_COLOR, r, 2)

        gw, gh = model.w * cp, model.h * cp
        pygame.draw.rect(surf, BORDER_COLOR, pygame.Rect(ox - 1, oy - 1, gw + 2, gh + 2), 1)
