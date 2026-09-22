import hashlib
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    if os.environ.get("PYTHONHASHSEED") != "0":
        env = dict(os.environ, PYTHONHASHSEED="0")
        os.execve(sys.executable, [sys.executable] + sys.argv, env)
    from game import settings as S
    from game.maze import Maze
    from game.props import populate_level

    per_theme = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    digest = hashlib.sha256()
    for spec in S.FLOOR_SPECS:
        if spec.get("layout", "corridor") == "yard":
            continue
        total = errors = floors_with = 0
        for seed in range(per_theme):
            try:
                maze = Maze(seed=seed, wall_bias=spec["wall_bias"], template_floor=spec.get("floor_theme"))
                populate_level(maze, spec, random.Random(seed ^ 0x5EED))
            except Exception as exc:
                errors += 1
                print(f"  {spec['key']} seed {seed}: {type(exc).__name__}: {exc}")
                continue
            cells = [(x, y) for y in range(maze.h) for x in range(maze.w) if maze.grid[y][x] == S.WALL_WINDOW]
            total += len(cells)
            floors_with += bool(cells)
            digest.update(f"{spec['key']}:{seed}:{sorted(cells)}".encode())
        print(f"{spec['key']}: floors={per_theme} errors={errors} window_cells={total} floors_with_windows={floors_with}")
    print("digest", digest.hexdigest()[:16])


if __name__ == "__main__":
    main()
