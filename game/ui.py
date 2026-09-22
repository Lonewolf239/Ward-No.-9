import pygame

from game import settings as S

PANEL_FILL = (10, 9, 8, 165)
PANEL_FILL_SOLID = (16, 14, 14, 248)
PANEL_BORDER = (95, 88, 82)
PANEL_ACCENT = (150, 55, 50)
ROW_FILL = (26, 23, 22, 235)
ROW_FILL_ACTIVE = (86, 34, 32, 245)
ROW_FILL_SPECIAL = (92, 62, 26, 245)
ROW_BORDER = (72, 66, 62)
ROW_BORDER_ACTIVE = (176, 78, 68)
TEXT = S.COL_TEXT
TEXT_DIM = S.COL_UI_DIM
TEXT_ON = (238, 226, 210)


def draw_panel(target, rect, fill=PANEL_FILL, border=PANEL_BORDER, accent=PANEL_ACCENT,
               corner=20):
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


def draw_row(target, rect, active=False, special=False, enabled=True, corner=9):
    fill = ROW_FILL_SPECIAL if special else (ROW_FILL_ACTIVE if active else ROW_FILL)
    if not enabled:
        fill = (fill[0] // 2, fill[1] // 2, fill[2] // 2, 200)
    surf = pygame.Surface(rect.size, pygame.SRCALPHA)
    surf.fill(fill)
    target.blit(surf, rect.topleft)
    if active:
        draw_panel(target, rect, fill=(0, 0, 0, 0), border=ROW_BORDER_ACTIVE,
                   accent=ROW_BORDER_ACTIVE, corner=corner)
    else:
        pygame.draw.rect(target, ROW_BORDER, rect, width=1)


def row_text_color(active=False, enabled=True):
    if not enabled:
        return (86, 80, 76)
    return TEXT_ON if active else TEXT


def font(size, path=None):
    return pygame.font.Font(path or S.FONT_PATH, size)
