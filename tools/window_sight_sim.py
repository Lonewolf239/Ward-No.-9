import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game import settings as S
from game.entities import Monster, Player
from game.maze import Maze

DT = 1.0 / 30.0
PLAYER = (20.5, 4.5)
LIGHT_LEVEL = 0.3
VISION_MULT = 2.0
TIMING_DIST = 4.5


def build(gap):
    m = Maze(w=24, h=9, seed=1, layout="blank")
    for y in range(1, m.h - 1):
        for x in range(1, m.w - 1):
            m.grid[y][x] = S.FLOOR
    columns = (18,) if gap != "two panes" else (17, 18)
    for cx in columns:
        for y in range(1, m.h - 1):
            m.grid[y][cx] = S.WALL_CONCRETE
        m.grid[4][cx] = S.FLOOR if gap == "open" else S.WALL_WINDOW
    m.start = PLAYER
    return m


def spot_time(maze, dist, max_seconds=4.0):
    px, py = PLAYER
    mx, my = px - dist, py
    monster = Monster(mx, my, maze, rng=random.Random(1), speed_mult=0.0, vision_mult=VISION_MULT,
                      blocked_cells=set(), lockers=[], doors=[])
    player = Player(px, py)
    player.flashlight_on = False
    t = 0.0
    while t < max_seconds:
        player.light_level = LIGHT_LEVEL
        player.x, player.y = px, py
        player.peek_x, player.peek_y = px, py
        monster.x, monster.y = mx, my
        monster.facing = 0.0
        monster.update(DT, maze, player, 0.0, [])
        t += DT
        if monster._had_visual_last_frame:
            return t
    return None


def farthest_spot(maze):
    best = 0.0
    d = 3.75
    while d <= 17.5:
        if spot_time(maze, d) is not None:
            best = d
        d += 0.25
    return best


def walk_past(maze, dist=5.0, span=6.0, seconds=14.0):
    px, py = PLAYER
    player = Player(px, py)
    player.flashlight_on = False
    player.light_level = LIGHT_LEVEL
    player.peek_x, player.peek_y = px, py
    monster = Monster(px - dist, py - span / 2, maze, rng=random.Random(3),
                      speed_mult=0.0, vision_mult=VISION_MULT,
                      blocked_cells=set(), lockers=[], doors=[])
    t = 0.0
    seen_at = None
    caught = False
    while t < seconds:
        frac = (t / seconds) * 2.0
        frac = frac if frac <= 1.0 else 2.0 - frac
        monster.x = px - dist
        monster.y = py - span / 2 + span * frac
        monster.facing = 0.0
        player.x, player.y = px, py
        player.peek_x, player.peek_y = px, py
        monster.update(DT, maze, player, 0.0, [])
        if monster._had_visual_last_frame and seen_at is None:
            seen_at = (t, abs(monster.y - py))
        if monster.caught_player:
            caught = True
            break
        t += DT
    return seen_at, caught


def reach_through(maze):
    px, py = PLAYER
    player = Player(px, py)
    player.light_level = LIGHT_LEVEL
    monster = Monster(px, py, maze, rng=random.Random(5), speed_mult=0.0,
                      vision_mult=VISION_MULT, blocked_cells=set(), lockers=[], doors=[])
    row = maze.grid[int(py)]
    hi = max((x for x in range(1, int(px)) if row[x] != S.FLOOR), default=None)
    if hi is None:
        wall_x = px - 2.0
    else:
        lo = hi
        while lo - 1 >= 1 and row[lo - 1] != S.FLOOR:
            lo -= 1
        wall_x = (lo + hi + 1) / 2.0
    step = 0.005
    mx = wall_x - 2.5
    while mx < wall_x and not maze.circle_hits_wall(mx + step, py, S.MONSTER_RADIUS):
        mx += step
    ppx = wall_x + 2.5
    while ppx > wall_x and not maze.circle_hits_wall(ppx - step, py, S.PLAYER_RADIUS):
        ppx -= step
    player.x, player.peek_x = ppx, ppx
    monster.x, monster.y = mx, py
    monster.state = Monster.HUNT
    monster.facing = 0.0
    for _ in range(90):
        monster.x, monster.y = mx, py
        monster.state = Monster.HUNT
        player.x, player.peek_x = ppx, ppx
        monster.update(DT, maze, player, 0.0, [])
        if monster.caught_player:
            return ppx - mx, True
    return ppx - mx, False


def main():
    mult = S.MONSTER_WINDOW_SIGHT_MULT
    gaps = ("open", "one pane", "two panes")
    mazes = {g: build(g) for g in gaps}
    px, py = PLAYER
    for g in gaps:
        print(f"{g:10s} transmission from 6 units: {mazes[g].sight_transmission(px - 6, py, px, py):.3f}")
    far = {g: farthest_spot(mazes[g]) for g in gaps}
    close = {g: spot_time(mazes[g], TIMING_DIST) for g in gaps}
    for g in gaps:
        print(f"{g:10s} farthest spotting distance {far[g]:5.2f}  time to spot at {TIMING_DIST} units "
              f"{close[g] if close[g] is None else round(close[g], 3)}")
    r1 = far["one pane"] / far["open"]
    r2 = far["two panes"] / far["open"]
    tr = close["one pane"] / close["open"]
    tr2 = close["two panes"] / close["open"]
    tol = 0.25 / far["open"] * 2
    print(f"range ratio one pane {r1:.3f} (expect {mult:.3f}), two panes {r2:.3f} (expect {mult * mult:.3f}); "
          f"time ratio one pane {tr:.3f} (expect {1 / mult:.3f}), two panes {tr2:.3f} "
          f"(expect {1 / mult / mult:.3f}); +-1 frame")
    print()
    print("монстр ходит вдоль стены, игрок стоит за проёмом:")
    for g in gaps:
        seen, caught = walk_past(mazes[g])
        if seen is None:
            print(f"   {g:10s} не заметил за весь проход")
        else:
            print(f"   {g:10s} заметил через {seen[0]:.2f} с, когда был в {seen[1]:.2f} "
                  f"от линии взгляда; поймал: {'да' if caught else 'нет'}")
    print()
    print("нос к носу через проём (может ли достать):")
    grabbed = []
    for g in gaps:
        gap_w, caught = reach_through(mazes[g])
        print(f"   {g:10s} между телами {gap_w:.2f} клетки, хват {S.MONSTER_CATCH_RADIUS:.2f}: "
              f"{'ПОЙМАЛ' if caught else 'не достал'}")
        if caught and g != "open":
            grabbed.append(g)
    blank = Maze(w=24, h=9, seed=1, layout="blank")
    for yy in range(1, 8):
        for xx in range(1, 23):
            blank.grid[yy][xx] = S.FLOOR
    cp = Player(12.0, 4.5)
    cp.light_level = LIGHT_LEVEL
    cm = Monster(11.5, 4.5, blank, rng=random.Random(1), speed_mult=0.0,
                 vision_mult=VISION_MULT, blocked_cells=set(), lockers=[], doors=[])
    cm.state = Monster.HUNT
    cm.update(DT, blank, cp, 0.0, [])
    print(f"   контроль: чистый пол, между телами 0.50 — "
          f"{'ПОЙМАЛ' if cm.caught_player else 'НЕ ПОЙМАЛ (проверка ничего не доказывает)'}")
    if not cm.caught_player:
        grabbed.append("контроль")

    ok = (not grabbed
          and abs(r1 - mult) <= tol and abs(r2 - mult * mult) <= tol
          and abs(close["one pane"] - close["open"] / mult) <= 2 * DT
          and abs(close["two panes"] - close["open"] / mult / mult) <= 2 * DT)
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
