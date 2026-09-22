import math, os, random, sys
_root = "/mnt/Coding/code.py/ward9"
sys.path.insert(0, _root); sys.path.insert(0, os.path.join(_root, "tools"))
os.environ["SDL_VIDEODRIVER"] = "offscreen"; os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
import numpy as np, pygame
pygame.display.flip = lambda: None
from room_editor.editor import Editor, PAD
from game import settings as S

random.seed(7)
ed = Editor(mode="dev")
ed.model.resize(11, 11)
for y in range(1, 10):
    for x in range(1, 10):
        ed.model.cells[y][x] = S.FLOOR
ed.model.furniture = []
ed._rebuild_level()
ed.camera.x, ed.camera.y, ed.camera.z = PAD + 5.5, PAD + 5.5, 9.0
ed.camera.yaw, ed.camera.pitch = math.radians(90), -1.45
ed.panel.current_tool = "furniture"
ed.panel.current_furniture_kind = "crate"
ed.panel.gizmo_mode = "cursor"

fails = []
W, H = ed.primary_w, ed.window_h
for tag, (mx, my) in (("центр", (W // 2, H // 2)),
                      ("левее-выше", (int(W * 0.42), int(H * 0.40))),
                      ("правее-ниже", (int(W * 0.58), int(H * 0.60)))):
    pt = ed._resolve_point(mx, my)
    if pt is None:
        print("  %-14s луч мимо пола" % tag)
        fails.append(tag)
        continue
    px, py = pt[0] - PAD, pt[1] - PAD
    item = ed.model.place_furniture("crate", px, py)
    if item is None:
        print("  %-14s точка %s - поставить не удалось" % (tag, (round(px, 2), round(py, 2))))
        fails.append(tag)
        continue
    d = math.hypot(float(item[1]) - px, float(item[2]) - py)
    ok = d < 0.01
    print("  %-14s курсор -> %s, встало на %s, промах %.3f  %s"
          % (tag, (round(px, 2), round(py, 2)),
             (round(float(item[1]), 2), round(float(item[2]), 2)), d,
             "ок" if ok else "ПРОВАЛ"))
    if not ok:
        fails.append(tag)

ed.model.furniture = []
ed._rebuild_level()
pygame.mouse.get_pos = lambda: (W // 2, H // 2)
ed.panel.current_furniture_kind = "crate"
ed._draw()
fb = ed.renderer.ctx.screen
w, h = fb.size
arr = np.flipud(np.frombuffer(fb.read(components=3), dtype=np.uint8).reshape(h, w, 3)).copy()
font = pygame.font.SysFont("dejavusans", 16)
view = arr[:, :int(w * 0.82)]
g = view.astype(int)
gold = ((g[..., 0] - g[..., 2] > 45) & (g[..., 0] > 90)).sum()
print("  пикселей призрака в виде: %d" % gold)
if gold < 200:
    fails.append("призрак не нарисован")
img = pygame.image.frombuffer(np.ascontiguousarray(arr).tobytes(), (w, h), "RGB")
img = pygame.transform.scale(img, (960, 540))
img.blit(font.render("призрак мебели под курсором", True, (255, 230, 120)), (8, 6))
_out = os.path.join(_root, "qa_out")
os.makedirs(_out, exist_ok=True)
pygame.image.save(img, os.path.join(_out, "editor_ghost.png"))
print("  снимок: qa_out/editor_ghost.png")
print("ПРОВАЛОВ: %d" % len(fails))
sys.exit(1 if fails else 0)
