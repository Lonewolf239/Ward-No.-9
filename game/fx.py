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
