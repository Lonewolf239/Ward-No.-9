import collections
import math
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from game import settings as S
from game import room_templates as rt
from game import zone_templates as zt

STRUCTURAL = {"upper": {"corridor", "link"}, "basement": {"tech_corridor", "vent", "link"}}


def build(floor_key, seed, target):
    rng = random.Random(seed)
    return rt.generate(rng, floor_key, target)


def graph_of(grid, rooms):
    owner = {}
    for idx, room in enumerate(rooms):
        cells = room.get("cells")
        if cells is None:
            x0, y0, x1, y1 = room["rect"]
            cells = [(x, y) for y in range(y0, y1) for x in range(x0, x1)]
        for (x, y) in cells:
            if 0 <= y < len(grid) and 0 <= x < len(grid[0]) and grid[y][x] == S.FLOOR:
                owner.setdefault((x, y), set()).add(idx)

    adj = collections.defaultdict(set)
    for (x, y), here in owner.items():
        for idx in here:
            adj[idx] |= here - {idx}
        for dx, dy in ((1, 0), (0, 1)):
            there = owner.get((x + dx, y + dy))
            if not there:
                continue
            for a in here:
                for b in there:
                    if a != b:
                        adj[a].add(b)
                        adj[b].add(a)
    return adj


def longest_sightline(grid, gw, gh, corridor_cells=None):
    def ok(x, y):
        if grid[y][x] != S.FLOOR:
            return False
        return corridor_cells is None or (x, y) in corridor_cells

    best = 0
    for y in range(gh):
        run = 0
        for x in range(gw):
            run = run + 1 if ok(x, y) else 0
            best = max(best, run)
    for x in range(gw):
        run = 0
        for y in range(gh):
            run = run + 1 if ok(x, y) else 0
            best = max(best, run)
    return best


def longest_chain(adj, nodes):
    best = 0
    for start in nodes:
        stack = [(start, frozenset((start,)))]
        budget = 4000
        while stack and budget > 0:
            budget -= 1
            node, path = stack.pop()
            best = max(best, len(path))
            for nxt in adj[node]:
                if nxt in nodes and nxt not in path:
                    stack.append((nxt, path | {nxt}))
    return best


def survey(runs, want_maps, out_dir):
    for floor_idx, spec in enumerate(S.FLOOR_SPECS):
        theme = spec.get("floor_theme")
        if theme not in rt.TEMPLATE_SETS:
            continue
        structural = STRUCTURAL[theme]
        chains, shares, room_counts, cells_counts, sightlines = [], [], [], [], []
        spines = []
        landmark = []
        dead_ends, loops, kinds = [], [], collections.Counter()
        fails = 0
        for seed in range(runs):
            rng = random.Random(seed)
            target = rng.randint(*(spec.get("room_count") or S.TEMPLATE_ROOM_COUNT))
            result = build(theme, seed, target)
            if result is None:
                fails += 1
                continue
            gw, gh, grid, rooms, doors, start = result
            adj = graph_of(grid, rooms)
            corridor_nodes = {i for i, r in enumerate(rooms) if r["kind"] in structural}
            off_spine = {i for i in corridor_nodes if not rooms[i].get("spine")}
            chains.append(longest_chain(adj, off_spine))
            spine_rooms = [r for r in rooms if r.get("spine")]
            spine_cells = set()
            for r in spine_rooms:
                x0, y0, x1, y1 = r["rect"]
                spine_cells.update((x, y) for y in range(y0, y1) for x in range(x0, x1))
            spine_cells.update(tuple(c) for c, _f, _k in doors)
            spines.append((len(spine_rooms), longest_sightline(grid, gw, gh, spine_cells)))
            shares.append(100.0 * len(corridor_nodes) / max(1, len(rooms)))
            room_counts.append(len(rooms))
            cells_counts.append(sum(row.count(S.FLOOR) for row in grid))
            dead_ends.append(sum(1 for i in range(len(rooms)) if len(adj[i]) <= 1))
            edges = sum(len(v) for v in adj.values()) // 2
            loops.append(edges - len(rooms) + 1)
            kinds.update(r["kind"] for r in rooms)
            corridor_cells = set()
            for room in rooms:
                if room["kind"] not in structural:
                    continue
                own = room.get("cells")
                if own is None:
                    x0, y0, x1, y1 = room["rect"]
                    own = [(x, y) for y in range(y0, y1) for x in range(x0, x1)]
                corridor_cells.update(own)
            corridor_cells.update(tuple(c) for c, _f, _k in doors)
            sightlines.append(longest_sightline(grid, gw, gh, corridor_cells))
            reach = math.hypot(gw, gh) / 2.0
            for r in rooms:
                if r["kind"] in structural:
                    continue
                area = (r["rect"][2] - r["rect"][0]) * (r["rect"][3] - r["rect"][1])
                if area < S.LANDMARK_MIN_CELLS:
                    continue
                bx = (r["rect"][0] + r["rect"][2]) / 2.0
                by = (r["rect"][1] + r["rect"][3]) / 2.0
                landmark.append(min(1.0, math.hypot(bx - gw / 2.0, by - gh / 2.0) / max(1.0, reach)))

        key = spec["key"]
        print("%s (%s), %d seeds%s" % (key, theme, runs, ", %d failed" % fails if fails else ""))
        print("   corridors in a row : median %d, worst %d  (not counting the spine)"
              % (statistics.median(chains), max(chains)))
        print("   the spine          : %d corridors, %d m straight (median)"
              % (statistics.median([n for n, _m in spines]),
                 statistics.median([m for _n, m in spines])))
        print("   corridor share     : %.0f%% of rooms" % statistics.median(shares))
        floor_min = spec.get("min_cells") or 0
        short = 100.0 * sum(1 for c in cells_counts if c < floor_min) / max(1, len(cells_counts))
        print("   rooms / floor cells: %d rooms, %d cells (median), smallest %d, %.0f%% under min_cells=%d"
              % (statistics.median(room_counts), statistics.median(cells_counts),
                 min(cells_counts), short, floor_min))
        print("   dead-end rooms     : median %d of %d"
              % (statistics.median(dead_ends), statistics.median(room_counts)))
        print("   ways round (loops) : median %d" % statistics.median(loops))
        total = sum(kinds.values())
        print("   longest corridor you can be seen along: %d m (median), %d m (best floor)"
              % (statistics.median(sightlines), max(sightlines)))
        if landmark:
            corner = sum(1 for v in landmark if v > 0.7)
            print("   big rooms sit      : %.0f%% of the way out (median of %d); "
                  "%.0f%% of them out past 70%% - in a corner"
                  % (100.0 * statistics.median(landmark), len(landmark),
                     100.0 * corner / len(landmark)))
        print("   made of            : %s"
              % ", ".join("%s %.0f%%" % (k, 100.0 * v / total) for k, v in kinds.most_common()))
        print()

    wing_survey(runs)

    templates = zt._templates()
    by_kind = collections.Counter(t.kind for t in templates)
    per_yard = (S.YARD_ZONE_GRID) ** 2
    print("yard: %d zone templates in %d kinds (%s); a yard shows %d zones"
          % (len(templates), len(by_kind),
             ", ".join("%s x%d" % kv for kv in by_kind.most_common()), per_yard))

    if want_maps:
        draw_maps(out_dir)


def wing_survey(runs):
    from game.maze import Maze
    print("wings: telling one part of the floor from another")
    for spec in S.FLOOR_SPECS:
        theme = spec.get("floor_theme")
        if theme not in rt.TEMPLATE_SETS:
            continue
        far_pairs = differ = 0
        corridor_named = corridor_total = 0
        for seed in range(runs):
            m = Maze(seed=seed, wall_bias=spec.get("wall_bias"), template_floor=theme,
                     room_count_range=spec.get("room_count"))
            tone = {}
            for idx, room in enumerate(m.rooms):
                x0, y0, x1, y1 = room["rect"]
                mats = collections.Counter()
                for y in range(y0 - 1, y1 + 1):
                    for x in range(x0 - 1, x1 + 1):
                        if 0 <= x < m.w and 0 <= y < m.h and m.grid[y][x] != S.FLOOR:
                            mats[m.grid[y][x]] += 1
                if mats:
                    tone[idx] = (mats.most_common(1)[0][0],
                                 ((x0 + x1) / 2.0, (y0 + y1) / 2.0), room)
            for a in tone:
                for b in tone:
                    if a >= b:
                        continue
                    (ma, ca, _ra), (mb, cb, _rb) = tone[a], tone[b]
                    if math.hypot(ca[0] - cb[0], ca[1] - cb[1]) < 15.0:
                        continue
                    far_pairs += 1
                    differ += ma != mb
            for idx, (mat, _c, room) in tone.items():
                if room["kind"] not in STRUCTURAL[theme] or not room.get("wing"):
                    continue
                corridor_total += 1
                want = next(w["material"] for w in S.FLOOR_WINGS[theme]
                            if w["key"] == room["wing"])
                corridor_named += mat == want
        print("   %-7s far-apart room pairs whose walls differ: %.0f%% of %d; "
              "corridors whose walls name their wing: %.0f%%"
              % (spec["key"], 100.0 * differ / max(1, far_pairs), far_pairs,
                 100.0 * corridor_named / max(1, corridor_total)))
    print()


KIND_COLOR = {
    "corridor": (70, 70, 78), "tech_corridor": (64, 64, 74), "vent": (52, 58, 64),
    "link": (108, 84, 52),
    "entrance": (120, 110, 80), "stairwell": (120, 110, 80),
    "exit": (60, 120, 70), "unlocker": (120, 70, 60),
    "ward": (90, 100, 120), "office": (100, 90, 110), "cafeteria": (110, 100, 70),
    "morgue": (100, 60, 70), "cell": (80, 60, 60), "plain": (80, 80, 80),
    "boiler": (110, 80, 50), "storage": (80, 95, 80),
}


def draw_maps(out_dir):
    os.environ.setdefault("SDL_VIDEODRIVER", "offscreen")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    import pygame
    pygame.init()
    os.makedirs(out_dir, exist_ok=True)
    scale = 6
    for spec in S.FLOOR_SPECS:
        theme = spec.get("floor_theme")
        if theme not in rt.TEMPLATE_SETS:
            continue
        for seed in (0, 1, 2):
            rng = random.Random(seed)
            target = rng.randint(*(spec.get("room_count") or S.TEMPLATE_ROOM_COUNT))
            result = build(theme, seed, target)
            if result is None:
                continue
            gw, gh, grid, rooms, doors, start = result
            surf = pygame.Surface((gw * scale, gh * scale))
            surf.fill((18, 18, 20))
            owner = {}
            for room in rooms:
                cells = room.get("cells")
                if cells is None:
                    x0, y0, x1, y1 = room["rect"]
                    cells = [(x, y) for y in range(y0, y1) for x in range(x0, x1)]
                for c in cells:
                    owner[c] = room["kind"]
            for y in range(gh):
                for x in range(gw):
                    if grid[y][x] != S.FLOOR:
                        continue
                    col = KIND_COLOR.get(owner.get((x, y)), (40, 40, 44))
                    surf.fill(col, (x * scale, y * scale, scale, scale))
            for (cx, cy), _facing, kind in doors:
                col = (220, 190, 90) if kind != "passage" else (150, 150, 160)
                surf.fill(col, (cx * scale + 1, cy * scale + 1, scale - 2, scale - 2))
            path = os.path.join(out_dir, "map_%s_%d.png" % (spec["key"], seed))
            pygame.image.save(surf, path)
            print("   wrote %s (%dx%d cells)" % (path, gw, gh))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    runs = 20
    if args and args[0].isdigit():
        runs = int(args.pop(0))
    want_maps = "--map" in args
    if want_maps:
        args.remove("--map")
    out = args[0] if args else "qa_out"
    survey(runs, want_maps, out)
