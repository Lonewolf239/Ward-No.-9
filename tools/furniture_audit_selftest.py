import io, math, os, sys
sys.path.insert(0, '/mnt/Coding/code.py/ward9')
os.environ["SDL_VIDEODRIVER"] = "offscreen"
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

src = io.open("/mnt/Coding/code.py/ward9/tools/furniture_audit.py", encoding="utf-8").read()
src = src.split('print("lights and furniture in every hand-authored room")')[0]
ns = {"__name__": "fa", "__file__": "/mnt/Coding/code.py/ward9/tools/furniture_audit.py"}
sys.argv = ["furniture_audit", "--quiet"]
exec(compile(src, "furniture_audit.py", "exec"), ns)
check, problems, S = ns["check"], ns["problems"], ns["S"]
from game.props import PROP_DEFS, support_top

W = H = 9


def room(extra_walls=()):
    cells = [[S.WALL_CONCRETE] * W for _ in range(H)]
    for y in range(1, H - 1):
        for x in range(1, W - 1):
            cells[y][x] = S.FLOOR
    for (x, y) in extra_walls:
        cells[y][x] = S.WALL_CONCRETE
    return cells


def run(tag, cells, furn, expect):
    problems.clear()
    check(tag, cells, W, H, furn, per_light=None)
    got = bool(problems)
    ok = got == expect
    print("  %-46s %s" % (tag, "ок" if ok else "ПРОВАЛ"))
    if not ok:
        for p in problems:
            print("        " + p)
    return ok


fails = 0
crate_top = support_top("crate")
cab_hw = PROP_DEFS["cabinet"]["hw"]
print("  (верх ящика %.3f, полуширина шкафа %.3f)" % (crate_top, cab_hw))

fails += not run("проп в стене", room(), [["crate", 0.5, 4.5, 0.0, 0.0]], True)
fails += not run("проп вплотную к стене", room(), [["crate", 1.28, 4.5, 0.0, 0.0]], False)

fails += not run("бра посреди комнаты", room(), [["wall_sconce", 4.5, 4.5, 1.6, 0.0]], True)
fails += not run("бра на стене", room(), [["wall_sconce", 1.07, 4.5, 1.6, 0.0]], False)

fails += not run("ящик в ящике", room(),
                 [["crate", 4.5, 4.5, 0.0, 0.0], ["crate", 4.55, 4.5, 0.0, 0.0]], True)
fails += not run("ящик НА ящике", room(),
                 [["crate", 4.5, 4.5, 0.0, 0.0], ["crate", 4.5, 4.5, crate_top, 0.0]], False)
fails += not run("стул придвинут к столу", room(),
                 [["desk", 4.5, 4.5, 0.0, 0.0], ["chair", 4.5, 3.85, 0.0, 1.5708]], False)

fails += not run("ящик висит в воздухе", room(), [["crate", 4.5, 4.5, 0.9, 0.0]], True)

step = 2 * PROP_DEFS["crate"]["hw"] + 0.04
corner = ([["crate", 3.0, 1.3 + i * step, 0.0, 0.0] for i in range(5)]
          + [["crate", 1.3 + i * step, 3.0, 0.0, 0.0] for i in range(4)])
fails += not run("ящиками отгорожен угол", room(), corner, True)
fails += not run("те же ящики вдоль стены", room(),
                 [["crate", 1.28, 1.5 + i * step, 0.0, 0.0] for i in range(5)], False)

bars_row = [(4, y) for y in range(1, H - 1)]
jail = room()
for (x, y) in bars_row:
    jail[y][x] = S.WALL_BARS
fails += not run("каталка в камере за решёткой", jail,
                 [["gurney", 2.5, 3.5, 0.0, 1.5708]], False)
fails += not run("скамейка вплотную к решётке", jail,
                 [["park_bench", 3.62, 3.5, 0.0, 1.5708]], False)
fails += not run("скамейка поперёк решётки", jail,
                 [["park_bench", 4.5, 3.5, 0.0, 0.0]], True)

print("ПРОВАЛОВ: %d" % fails)
sys.exit(1 if fails else 0)
