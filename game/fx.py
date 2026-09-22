import math
import random

import numpy as np
import pygame


class ScreenShake:
    def __init__(self):
        self.trauma = 0.0

    def add(self, amount):
        self.trauma = min(1.0, self.trauma + amount)

    def update(self, dt):
        self.trauma = max(0.0, self.trauma - dt * 1.4)

    def offset(self, max_px=14):
        if self.trauma <= 0:
            return 0, 0
        power = self.trauma ** 2
        return (
            random.uniform(-1, 1) * max_px * power,
            random.uniform(-1, 1) * max_px * power,
        )


def draw_flash(surf, color, alpha):
    if alpha <= 0:
        return
    overlay = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
    overlay.fill((*color, max(0, min(255, int(alpha)))))
    surf.blit(overlay, (0, 0))


_FACE_RNG_SEED = 90210


def _glow(surf, rect, color, alpha, steps=3):
    layer = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
    for i in range(steps, 0, -1):
        grow = i * max(2, int(rect.height * 0.22))
        a = int(alpha / (i + 1))
        pygame.draw.rect(layer, (*color, a), rect.inflate(grow * 2, grow * 2))
    surf.blit(layer, (0, 0))


def draw_jumpscare_face(surf, progress):
    w, h = surf.get_size()
    rng = random.Random(_FACE_RNG_SEED)
    surf.fill((0, 0, 0, 0))

    shut = progress ** 0.75
    jx = math.sin(progress * 120.0) * (2 + shut * 7)
    jy = math.cos(progress * 97.0) * (2 + shut * 5)
    cx, cy = w / 2 + jx, h / 2 + jy

    dark = pygame.Surface((w, h), pygame.SRCALPHA)
    dark.fill((0, 0, 0, 255))
    hole = max(w, h) * 1.15 * (1.0 - shut)
    for k, a in ((1.55, 205), (1.34, 150), (1.16, 85), (1.00, 0)):
        r = pygame.Rect(0, 0, hole * k * 1.30, hole * k)
        r.center = (cx, cy)
        pygame.draw.ellipse(dark, (0, 0, 0, a), r)
    surf.blit(dark, (0, 0))

    if 0.04 < shut < 0.97 and hole > 4:
        ring = pygame.Surface((w, h), pygame.SRCALPHA)
        outer = pygame.Rect(0, 0, hole * 1.20 * 1.30, hole * 1.20)
        outer.center = (cx, cy)
        pygame.draw.ellipse(ring, (110, 12, 10, int(150 * shut)), outer)
        inner = pygame.Rect(0, 0, hole * 1.02 * 1.30, hole * 1.02)
        inner.center = (cx, cy)
        pygame.draw.ellipse(ring, (0, 0, 0, 0), inner)
        surf.blit(ring, (0, 0))

    show = max(0.0, (shut - 0.28) / 0.72)
    if show > 0.0:
        spread = (0.19 - 0.08 * show) * w
        size = (0.030 + 0.105 * show ** 1.25) * w
        for sgn, eyf, k in ((-1.0, -0.03, 1.00), (1.0, -0.075, 0.76)):
            flick = 0.82 + 0.18 * math.sin(progress * 70.0 + sgn)
            ex, ey = cx + sgn * spread, cy + eyf * h
            ew, eh = size * k, size * k * 0.60
            r = pygame.Rect(0, 0, max(2, int(ew)), max(2, int(eh)))
            r.center = (ex, ey)
            _glow(surf, r, (255, 34, 24), int(215 * flick * show))
            pygame.draw.rect(surf, (int(240 * flick), 26, 20), r)
            core = r.inflate(-ew * 0.44, -eh * 0.44)
            if core.width > 1 and core.height > 1:
                pygame.draw.rect(surf, (255, int(96 * flick), 72), core)
            slit = r.inflate(-ew * 0.80, -eh * 0.30)
            if slit.width > 0 and slit.height > 1:
                pygame.draw.rect(surf, (255, int(190 * flick), 160), slit)

    draw_flash(surf, (255, 255, 255), max(0, 200 - progress * 3000) if progress < 0.07 else 0)
    draw_flash(surf, (0, 0, 0), 255 * max(0.0, (progress - 0.94) / 0.06))
