import pygame

from game import settings as S

BG = (10, 10, 12)
FLOOR_COLOR = (150, 150, 158)
WALL_COLOR = (58, 52, 46)
BORDER_COLOR = (100, 100, 110)
DOOR_COLORS = {
    "passage": (90, 200, 120), 
    "door": (230, 190, 60), 
    "broken": (205, 95, 60), 
    "window": (100, 200, 240), 
    "random": (170, 80, 220)
}
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
        self.font = pygame.font.SysFont("consolas,monospace", 12)

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

    def draw(self, surf, model, hover_cell, quest_marker_kinds):
        self._layout(model)
        pygame.draw.rect(surf, BG, self.rect)
        ox, oy = self.origin
        cp = self.cell_px

        for y in range(model.h):
            for x in range(model.w):
                r = pygame.Rect(ox + x * cp, oy + y * cp, cp - 1, cp - 1)
                is_wall = model.cells[y][x] != S.FLOOR
                pygame.draw.rect(surf, WALL_COLOR if is_wall else FLOOR_COLOR, r)

        for info in model.doors.values():
            dx, dy = info["cell"]
            r = pygame.Rect(ox + dx * cp, oy + dy * cp, cp - 1, cp - 1)
            pygame.draw.rect(surf, DOOR_COLORS[info["kind"]], r)

        for dx, dy, facing, kind in model.interior_doors:
            r = pygame.Rect(ox + dx * cp, oy + dy * cp, cp - 1, cp - 1)
            pygame.draw.rect(surf, DOOR_COLORS[kind], r)

        for kind, fx, fy, facing in model.furniture:
            r = pygame.Rect(ox + fx * cp, oy + fy * cp, cp - 1, cp - 1)
            is_quest = kind in quest_marker_kinds
            pygame.draw.rect(surf, QUEST_COLOR if is_quest else FURNITURE_COLOR, r)
            if cp >= 22:
                txt = self.font.render(kind[:3], True, LABEL_COLOR)
                surf.blit(txt, (r.x + 2, r.y + 2))

        if hover_cell is not None:
            hx, hy = hover_cell
            if 0 <= hx < model.w and 0 <= hy < model.h:
                r = pygame.Rect(ox + hx * cp, oy + hy * cp, cp - 1, cp - 1)
                pygame.draw.rect(surf, HOVER_COLOR, r, 2)

        gw, gh = model.w * cp, model.h * cp
        pygame.draw.rect(surf, BORDER_COLOR, pygame.Rect(ox - 1, oy - 1, gw + 2, gh + 2), 1)
