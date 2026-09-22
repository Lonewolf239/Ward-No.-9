import math
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game.props import PICKUP_ROOM_AFFINITY

PICKUP_KINDS = {"battery", "note_flat", "sanity_pill", "lighter", "fuse", "valve_key", "key", "cutters", "paper_map", "pencil"}
CLUTTER_KINDS = {"clutter_papers", "clutter_bottle", "clutter_junk"}
CORRIDOR_KINDS = {"corridor", "tech_corridor", "vent", "link"}
MIN_ROOM_CELLS = 12


def nearest_share(points, limit):
    if len(points) < 2:
        return 0.0
    close = 0
    for i, (x, y) in enumerate(points):
        d = min(math.hypot(x - ox, y - oy) for j, (ox, oy) in enumerate(points) if j != i)
        close += d < limit
    return close / len(points)


def nearest_distances(points):
    return [min(math.hypot(x - ox, y - oy) for j, (ox, oy) in enumerate(points) if j != i)
            for i, (x, y) in enumerate(points)] if len(points) > 1 else []


def pct(values, q):
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))]


def yard_census(spec, floors, S, Maze, populate_yard):
    near15, near3, top_zone, empty_zones, totals = [], [], [], [], []
    for seed in range(floors):
        maze = Maze(w=S.YARD_W, h=S.YARD_H, seed=seed, layout="yard")
        props = populate_yard(maze, spec, random.Random(seed ^ 0x5EED))[0]
        pickups = [p for p in props if p.kind in PICKUP_KINDS]
        totals.append(len(pickups))
        pts = [(p.x, p.y) for p in pickups]
        near15.append(nearest_share(pts, 1.5))
        near3.append(nearest_share(pts, 3.0))
        per_zone = {}
        for p in pickups:
            zid = next((i for i, z in enumerate(maze.zones)
                        if z["rect"][0] <= p.x < z["rect"][2] and z["rect"][1] <= p.y < z["rect"][3]), None)
            per_zone[zid] = per_zone.get(zid, 0) + 1
        top_zone.append(max(per_zone.values()) / len(pickups) if pickups else 0.0)
        empty_zones.append(sum(1 for i in range(len(maze.zones)) if i not in per_zone) / max(1, len(maze.zones)))
    mean = statistics.mean
    print(f"{spec['key']} ({floors} floors)")
    print(f"  pickups per floor {mean(totals):.1f}; closer than 1.5 m {100 * mean(near15):.1f}%, than 3 m "
          f"{100 * mean(near3):.1f}%; fullest zone holds {100 * mean(top_zone):.1f}%; zones with none "
          f"{100 * mean(empty_zones):.1f}%")


def main():
    if os.environ.get("PYTHONHASHSEED") != "0":
        env = dict(os.environ, PYTHONHASHSEED="0")
        os.execve(sys.executable, [sys.executable] + sys.argv, env)
    from game import settings as S
    from game.maze import Maze
    from game.props import populate_level

    per_theme = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    from game.props import populate_yard
    for spec in S.FLOOR_SPECS:
        if spec.get("layout", "corridor") == "yard":
            yard_census(spec, per_theme, S, Maze, populate_yard)
            continue
        near15, near3, nn_med, top_room, corridor_share, cv_rooms, empty_rooms = [], [], [], [], [], [], []
        dead_end_paid, in_fitting_room = [], []
        clutter_density, clutter_empty, clutter_near1, clutter_window, clutter_total, pickups_total = [], [], [], [], [], []
        for seed in range(per_theme):
            maze = Maze(seed=seed, wall_bias=spec["wall_bias"], template_floor=spec.get("floor_theme"))
            props = populate_level(maze, spec, random.Random(seed ^ 0x5EED))[0]
            cell_room = {}
            for room in maze.rooms:
                for c in maze.room_cells(room):
                    cell_room.setdefault(c, id(room))
            room_kind = {id(r): r.get("kind") for r in maze.rooms}
            room_cells = {}
            for c, rid in cell_room.items():
                if maze.grid[c[1]][c[0]] == S.FLOOR:
                    room_cells[rid] = room_cells.get(rid, 0) + 1

            pickups = [p for p in props if p.kind in PICKUP_KINDS]
            clutter = [p for p in props if p.kind in CLUTTER_KINDS]
            pickups_total.append(len(pickups))
            clutter_total.append(len(clutter))
            pts = [(p.x, p.y) for p in pickups]
            near15.append(nearest_share(pts, 1.5))
            near3.append(nearest_share(pts, 3.0))
            nn_med.append(statistics.median(nearest_distances(pts)) if len(pts) > 1 else 0.0)

            per_room = {}
            for p in pickups:
                rid = cell_room.get((int(p.x), int(p.y)))
                per_room[rid] = per_room.get(rid, 0) + 1
            top_room.append(max(per_room.values()) / len(pickups) if pickups else 0.0)
            corridor_share.append(sum(n for rid, n in per_room.items()
                                      if rid is None or room_kind.get(rid) in CORRIDOR_KINDS) / max(1, len(pickups)))
            big = [rid for rid, n in room_cells.items()
                   if n >= MIN_ROOM_CELLS and room_kind.get(rid) not in CORRIDOR_KINDS]
            dens = [per_room.get(rid, 0) / room_cells[rid] for rid in big]
            if len(dens) > 1 and statistics.mean(dens) > 0:
                cv_rooms.append(statistics.pstdev(dens) / statistics.mean(dens))
            empty_rooms.append(sum(1 for d in dens if d == 0) / max(1, len(dens)))

            ends = [id(r) for r in maze.rooms
                    if maze.room_door_count(r) == 1
                    and r.get("kind") not in CORRIDOR_KINDS and maze.room_cells(r)]
            if ends:
                dead_end_paid.append(sum(1 for rid in ends if per_room.get(rid)) / len(ends))
            fitting = 0
            for p in pickups:
                rid = cell_room.get((int(p.x), int(p.y)))
                want = PICKUP_ROOM_AFFINITY.get(p.kind, {})
                if rid is not None and want.get(room_kind.get(rid), 1.0) > 1.0:
                    fitting += 1
            in_fitting_room.append(fitting / max(1, len(pickups)))

            c_room = {}
            for p in clutter:
                rid = cell_room.get((int(p.x), int(p.y)))
                c_room[rid] = c_room.get(rid, 0) + 1
            cd = [10.0 * c_room.get(rid, 0) / room_cells[rid] for rid in big]
            clutter_density.extend(cd)
            clutter_empty.append(sum(1 for d in cd if d == 0) / max(1, len(cd)))
            clutter_near1.append(nearest_share([(p.x, p.y) for p in clutter], 1.0))
            grid = {}
            for p in clutter:
                grid[(int(p.x), int(p.y))] = grid.get((int(p.x), int(p.y)), 0) + 1
            clutter_window.append(max((sum(grid.get((x + dx, y + dy), 0) for dx in (-1, 0, 1) for dy in (-1, 0, 1))
                                       for (x, y) in grid), default=0))

        mean = statistics.mean
        print(f"{spec['key']} ({per_theme} floors)")
        print(f"  pickups per floor {mean(pickups_total):.1f}; nearest other pickup median {mean(nn_med):.2f} m; "
              f"closer than 1.5 m {100 * mean(near15):.1f}%, than 3 m {100 * mean(near3):.1f}%")
        print(f"  fullest room holds {100 * mean(top_room):.1f}% of pickups; corridors {100 * mean(corridor_share):.1f}%; "
              f"per-cell spread over rooms CV {mean(cv_rooms):.2f}; rooms (12+ cells) with none {100 * mean(empty_rooms):.1f}%")
        if dead_end_paid:
            print(f"  dead-end rooms holding something {100 * mean(dead_end_paid):.1f}%; "
                  f"pickups in a room whose purpose suits them {100 * mean(in_fitting_room):.1f}%")
        print(f"  clutter per floor {mean(clutter_total):.1f}; per 10 cells: median {pct(clutter_density, 0.5):.2f} "
              f"p90 {pct(clutter_density, 0.9):.2f} max {max(clutter_density):.2f}; rooms with none "
              f"{100 * mean(clutter_empty):.1f}%; within 1 m of another {100 * mean(clutter_near1):.1f}%; "
              f"most in a 3x3 window {max(clutter_window)} (mean {mean(clutter_window):.1f})")


if __name__ == "__main__":
    main()
