import os, sys, time
_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_here))
sys.path.insert(0, _here)
os.environ.setdefault("SDL_VIDEODRIVER", "offscreen")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from room_editor.room_model import RoomModel
from game import settings as S

N = next((int(a) for a in sys.argv[1:] if a.isdigit()), 30)
m = RoomModel(w=14, h=14)
for y in range(1, 13):
    for x in range(1, 13):
        m.cells[y][x] = S.FLOOR
for i in range(20):
    m.place_furniture("chair", 2.5 + (i % 5) * 1.9, 9.5 + (i // 5) * 0.8, snap=False)
pile = [m.place_furniture("bed", 5.5, 5.5, snap=False) for _ in range(N)]
assert all(b is not None for b in pile)
print("в комнате %d предметов, из них гора из %d кроватей высотой %.2f"
      % (len(m.furniture), N, float(pile[-1][3])))

steps = 30
t0 = time.perf_counter()
for k in range(steps):
    m.move_entry(pile[0], 5.5 + 0.02 * k, 5.5, carry=True)
dt = (time.perf_counter() - t0) / steps
riders = len(m.resting_on(pile[0]))
print("один шаг перетаскивания: %.1f мс (едет %d предметов сверху)" % (dt * 1000.0, riders))
ok = dt < 1.0 / 60.0 and riders == N - 1
print("ОК" if ok else "ПРОВАЛ (нужно < 16.7 мс и чтобы ехала вся гора)")
sys.exit(0 if ok else 1)
