import math
import random

import numpy as np
import pygame

from game import settings as S

EYE_RED = (255, 34, 24)


def _smooth(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def draw_eye_pair(surf, cx, cy, size, intensity, t):
    if intensity <= 0.0 or size < 1.0:
        return
    spread = size * 1.55
    glow = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
    for sgn, lift, k in ((-1.0, 0.0, 1.00), (1.0, -0.42, 0.76)):
        flick = 0.84 + 0.16 * math.sin(t * 70.0 + sgn * 1.7)
        ex, ey = cx + sgn * spread * 0.5, cy + lift * size * 0.6
        ew, eh = size * k, size * k * 0.58
        for grow, a in ((3.4, 14), (2.8, 22), (2.2, 34), (1.7, 52), (1.3, 78)):
            r = pygame.Rect(0, 0, max(2, int(ew * grow)), max(2, int(eh * grow + size * 0.12 * grow)))
            r.center = (int(ex), int(ey))
            pygame.draw.ellipse(glow, (*EYE_RED, int(a * intensity * flick)), r)
        body = pygame.Rect(0, 0, max(2, int(ew)), max(1, int(eh)))
        body.center = (int(ex), int(ey))
        pygame.draw.rect(glow, (int(235 * flick), 24, 18, int(255 * min(1.0, intensity * 1.2))), body)
        core = body.inflate(-ew * 0.40, -eh * 0.40)
        if core.width > 1 and core.height > 1:
            pygame.draw.rect(glow, (255, int(110 * flick), 80, int(255 * min(1.0, intensity * 1.2))), core)
        slit = body.inflate(-ew * 0.78, -eh * 0.25)
        if slit.width > 0 and slit.height > 0:
            pygame.draw.rect(glow, (255, int(200 * flick), 170, int(255 * min(1.0, intensity * 1.2))), slit)
    surf.blit(glow, (0, 0))


class Screamer:
    def __init__(self):
        self.t = 0.0
        self.snapshot = None
        self._fired = set()

    @property
    def duration(self):
        return S.SCREAMER_SECONDS

    def wants_snapshot(self):
        return self.snapshot is None

    def update(self, dt, sounds, shake):
        prev, self.t = self.t, self.t + dt
        for when, what, jolt in S.SCREAMER_EVENTS:
            key = (when, what)
            if key in self._fired or not prev <= when < self.t:
                continue
            self._fired.add(key)
            if what == "stinger":
                sounds.play_stinger()
            elif what == "bang":
                sounds.play_bang()
            shake.add(jolt)

    def _eye_point(self, surf, head_at):
        w, h = surf.get_size()
        if head_at is None or not (-w * 0.2 < head_at[0] < w * 1.2 and -h * 0.2 < head_at[1] < h * 1.2):
            return w * 0.5, h * 0.42
        return head_at

    def _snapshot_surface(self, img, size):
        h, w = img.shape[:2]
        s = pygame.image.frombuffer(np.ascontiguousarray(img).tobytes(), (w, h), "RGB")
        return pygame.transform.scale(s, size)

    def draw(self, surf, head_at):
        surf.fill((0, 0, 0, 0))
        if self.snapshot is None:
            return
        W, H = surf.get_size()
        img = self.snapshot.astype(np.int16)
        h, w = img.shape[:2]
        k = self.t / self.duration
        crt = 0.86
        if k >= crt:
            q = (k - crt) / (1.0 - crt)
            surf.fill((0, 0, 0, 255))
            if q < 0.55:
                lh = max(2, int(H * 0.5 * (1.0 - q / 0.55) ** 3))
                pygame.draw.rect(surf, (255, 235, 225), (0, H // 2 - lh // 2, W, lh))
            elif q < 0.9:
                lw = max(2, int(W * (1.0 - (q - 0.55) / 0.35) ** 2))
                pygame.draw.rect(surf, (255, 240, 235), (W // 2 - lw // 2, H // 2 - 1, lw, 3))
            return
        rng = random.Random(int(self.t * 60))
        amp = 4 + 70 * _smooth(k * 1.2)
        out = img.copy()
        y = 0
        while y < h:
            band = rng.randint(2, 18)
            if rng.random() < 0.25 + 0.6 * k:
                out[y:y + band] = np.roll(out[y:y + band], int(rng.uniform(-amp, amp)), axis=1)
            y += band
        split = int(2 + 14 * k)
        red = np.roll(out[..., 0], split, axis=1)
        blue = np.roll(out[..., 2], -split, axis=1)
        out = np.stack([red, out[..., 1], blue], axis=2)
        mono = out.mean(axis=2, keepdims=True)
        tint = np.concatenate([mono * 1.5, mono * 0.25, mono * 0.2], axis=2)
        out = out * (1.0 - _smooth(k * 1.3)) + tint * _smooth(k * 1.3)
        out = np.roll(out, int(h * 0.35 * _smooth((k - 0.4) / 0.4)), axis=0)
        if 0.38 < k < 0.46 or 0.62 < k < 0.66:
            out[..., 0] = 255 - out[..., 0]
        noise = np.array([rng.random() < 0.04 + 0.12 * k for _ in range(h)])
        out[noise] = out[noise] * 0.35 + rng.randint(30, 140)
        out = np.clip(out, 0, 255).astype(np.uint8)
        surf.blit(self._snapshot_surface(out, (W, H)), (0, 0))
        ex, ey = self._eye_point(surf, head_at)
        ex += rng.uniform(-1.0, 1.0) * amp * W / w * 0.6
        draw_eye_pair(surf, ex, ey, W * (0.028 + 0.05 * _smooth(k * 1.2)), 1.0, self.t)
