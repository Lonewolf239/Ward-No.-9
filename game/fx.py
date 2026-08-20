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


def draw_jumpscare_face(surf, progress):
    w, h = surf.get_size()
    rng = random.Random(_FACE_RNG_SEED)
    surf.fill((3, 1, 2))

    eased = progress ** 0.22
    violence = min(1.0, eased * 1.15)
    scale = 0.42 + eased * 1.05
    jx = math.sin(progress * 90.0) * (10 + violence * 20) + math.sin(progress * 210.0) * violence * 9
    jy = math.cos(progress * 77.0) * (7 + violence * 13)
    cx, cy = w / 2 + jx, h / 2 + jy
    face_w, face_h = w * scale, h * scale * 1.08
    rx, ry = face_w / 2, face_h / 2

    skin_dark = (24, 13, 13)
    skin_mid = (54, 30, 28)
    skin_hi = (82, 46, 38)
    pygame.draw.ellipse(surf, skin_dark, (cx - rx, cy - ry, face_w, face_h))

    for _ in range(90):
        while True:
            ox, oy = rng.uniform(-1, 1), rng.uniform(-1, 1)
            if ox * ox + oy * oy <= 1.0:
                break
        x, y = cx + ox * rx, cy + oy * ry
        r = rng.uniform(5, 34)
        col = rng.choice((skin_mid, skin_mid, skin_hi, skin_dark))
        a = rng.randint(35, 110)
        patch = pygame.Surface((int(r * 2) + 1, int(r * 2) + 1), pygame.SRCALPHA)
        pygame.draw.ellipse(patch, (*col, a), patch.get_rect())
        surf.blit(patch, (x - r, y - r))

    for i, (exf, eyf) in enumerate(((-0.24, -0.10), (0.22, -0.13))):
        wobble = math.sin(progress * 40 + i * 5) * 0.01
        ex, ey = cx + (exf + wobble) * face_w, cy + eyf * face_h
        ew = face_w * 0.17
        eh = face_h * (0.085 + 0.02 * math.sin(progress * 9 + i))
        sclera_rect = pygame.Rect(0, 0, ew, eh)
        sclera_rect.center = (ex, ey)
        pygame.draw.ellipse(surf, (215, 200, 185), sclera_rect)
        for _ in range(4):
            ang = rng.uniform(0, math.tau)
            pygame.draw.line(surf, (150, 20, 20), (ex, ey),
                              (ex + math.cos(ang) * ew * 0.5, ey + math.sin(ang) * eh * 0.5), 1)
        pupil_off = 0.18 * (1 if i == 0 else -1)
        pygame.draw.circle(surf, (10, 3, 3), (ex + pupil_off * ew, ey), max(2, eh * 0.42))

    mouth_w = face_w * 0.46
    mouth_h = face_h * (0.09 + 0.26 * progress)
    my = cy + face_h * 0.30
    n_pts = 12
    pts = []
    for k in range(n_pts):
        ang = math.pi * k / (n_pts - 1)
        rxx = mouth_w / 2 * (1 + rng.uniform(-0.05, 0.05))
        ryy = mouth_h / 2 * (1 + rng.uniform(-0.08, 0.08))
        pts.append((cx - rxx * math.cos(ang), my + ryy * math.sin(ang)))
    pts += [(cx + mouth_w / 2, my), (cx - mouth_w / 2, my)]
    pygame.draw.polygon(surf, (10, 2, 3), pts)

    teeth_n = 8
    for i in range(teeth_n):
        tx = cx - mouth_w / 2 + mouth_w * (i + 0.5) / teeth_n + rng.uniform(-4, 4)
        tw = rng.uniform(6, 11)
        th = mouth_h * rng.uniform(0.35, 0.75)
        top_y = my - mouth_h * 0.42
        pygame.draw.polygon(surf, (210, 198, 175),
                             [(tx - tw / 2, top_y), (tx + tw / 2, top_y), (tx + rng.uniform(-3, 3), top_y + th)])
    for i in range(teeth_n - 1):
        bx = cx - mouth_w / 2 + mouth_w * (i + 1) / teeth_n + rng.uniform(-4, 4)
        bw = rng.uniform(6, 10)
        bh = mouth_h * rng.uniform(0.3, 0.6)
        bot_y = my + mouth_h * 0.42
        pygame.draw.polygon(surf, (200, 188, 165),
                             [(bx - bw / 2, bot_y), (bx + bw / 2, bot_y), (bx + rng.uniform(-3, 3), bot_y - bh)])

    for _ in range(7):
        sx, sy = cx + rng.uniform(-rx, rx), cy + rng.uniform(-ry, ry)
        length, ang = rng.uniform(15, 55), rng.uniform(0, math.tau)
        pygame.draw.line(surf, (100, 10, 12), (sx, sy),
                          (sx + math.cos(ang) * length, sy + math.sin(ang) * length), rng.randint(1, 3))

    arr = pygame.surfarray.array3d(surf).astype(np.int16)
    shift = int(2 + violence * 7)
    if shift > 0:
        arr[:, :, 0] = np.roll(arr[:, :, 0], shift, axis=0)
        arr[:, :, 2] = np.roll(arr[:, :, 2], -shift, axis=0)
    grain = (np.random.random(arr.shape[:2]) * 34 - 17).astype(np.int16)
    arr += grain[:, :, None]
    pygame.surfarray.blit_array(surf, np.clip(arr, 0, 255).astype(np.uint8))

    flash_alpha = max(0, 235 - progress * 2350) if progress < 0.1 else 0
    draw_flash(surf, (255, 255, 255), flash_alpha)
    draw_flash(surf, (120, 0, 0), 60 * violence)
