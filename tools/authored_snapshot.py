import json
import math
import os
import random
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_here))
os.environ.setdefault("SDL_VIDEODRIVER", "offscreen")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
from tools.sandbox_settings import redirect
redirect()
import game.app as A
from game import settings as S

pygame.display.flip = lambda: None
SEEDS = [11, 202, 3003, 40004, 555, 6666]


def collect():
    random.seed(5)
    app = A.App()
    app._sync_window_size = lambda: None
    app.window_size = (S.SCREEN_W, S.SCREEN_H)
    if app.state == "splash":
        app._finish_splash()
    app.new_game()
    app._start_playing()
    rows = []
    for seed in SEEDS:
        for floor in (0, 1, 2):
            random.seed(seed * 7 + floor)
            app._load_floor(floor)
            app._begin_playing()
            for p in app.props:
                if not getattr(p, "authored", False):
                    continue
                rows.append([seed, floor, p.kind,
                             round(p.x, 4), round(p.y, 4), round(p.z0, 4),
                             round(p.facing % math.tau, 4)])
    return rows


def main():
    mode, path = sys.argv[1], sys.argv[2]
    rows = collect()
    if mode == "write":
        json.dump(rows, open(path, "w"))
        print("снято %d авторских пропов на %d сидах x 3 этажа" % (len(rows), len(SEEDS)))
        return
    old = json.load(open(path))
    print("было %d пропов, стало %d" % (len(old), len(rows)))
    okey = {}
    for r in old:
        okey.setdefault((r[0], r[1], r[2]), []).append(r[3:])
    nkey = {}
    for r in rows:
        nkey.setdefault((r[0], r[1], r[2]), []).append(r[3:])
    moved = same = gone = 0
    worst = (0.0, None)
    for k in sorted(set(okey) | set(nkey)):
        a, b = sorted(okey.get(k, [])), sorted(nkey.get(k, []))
        if len(a) != len(b):
            gone += abs(len(a) - len(b))
        for ra, rb in zip(a, b):
            d = math.dist(ra[:3], rb[:3])
            df = abs((ra[3] - rb[3] + math.pi) % math.tau - math.pi)
            if d < 1e-4 and df < 1e-4:
                same += 1
            else:
                moved += 1
                if d > worst[0]:
                    worst = (d, "%s %s" % (k[2], (round(ra[0], 2), round(ra[1], 2))))
    print("на месте: %d | сдвинулось: %d | появилось/исчезло: %d" % (same, moved, gone))
    print("самый большой сдвиг: %.4f (%s)" % worst)


if __name__ == "__main__":
    main()
