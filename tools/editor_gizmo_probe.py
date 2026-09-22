import math, os, random, sys, time
_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_here))
sys.path.insert(0, _here)
os.environ.setdefault("SDL_VIDEODRIVER", "offscreen")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
import pygame
pygame.display.flip = lambda: None
from room_editor.editor import Editor, PAD
from game import settings as S
from game.props import PROP_DEFS

fails = []


def check(tag, ok, extra=""):
    print("  %-52s %s %s" % (tag, "ок" if ok else "ПРОВАЛ", extra))
    if not ok:
        fails.append(tag)


class Ev:
    def __init__(self, pos, button=1):
        self.pos, self.button = pos, button


random.seed(7)
ed = Editor(mode="dev")
ed.model.resize(12, 12)
for y in range(1, 11):
    for x in range(1, 11):
        ed.model.cells[y][x] = S.FLOOR
ed.model.furniture = []
ed._rebuild_level()
ed.camera.x, ed.camera.y, ed.camera.z = PAD + 6.0, PAD + 12.5, 5.0
ed.camera.yaw, ed.camera.pitch = math.radians(-90), -0.75
ed.panel.current_tool = "furniture"
W, H = ed.primary_w, ed.window_h


def screen(p):
    return tuple(int(round(v)) for v in ed._to_screen(p))


def body_px(item):
    return screen((float(item[1]) + PAD, float(item[2]) + PAD,
                   float(item[3]) + PROP_DEFS[item[0]]["height"] * 0.5))


def drag(start, end, steps=12):
    ed._handle_mousedown(Ev(start))
    for k in range(1, steps + 1):
        t = k / float(steps)
        ed._gizmo_drag(int(start[0] + (end[0] - start[0]) * t),
                       int(start[1] + (end[1] - start[1]) * t))
    ed._end_drag()


desk = ed.model.place_furniture("desk", 5.5, 5.5, facing=0.0, snap=False)
lamp = ed.model.place_furniture("lamp_desk", 5.5, 5.5, snap=False)
ed._set_gizmo_mode("move")
ed._handle_mousedown(Ev(body_px(lamp)))
ed._end_drag()
check("клик по лампе на столе выбирает лампу", ed._selected_item() is lamp)
hx = screen(ed._gizmo_handles(lamp)["x"])
check("наведение на стрелку X видит стрелку X", ed._handle_under_cursor(*hx) == "x")
check("в 40 px от ручек наведения нет",
      ed._handle_under_cursor(hx[0] + 40, hx[1] + 40) is None)
y0, z0 = float(lamp[2]), float(lamp[3])
x0 = float(lamp[1])
far = screen((float(lamp[1]) + PAD + 1.5, float(lamp[2]) + PAD, z0 + 0.03))
drag(hx, (far[0] + (hx[0] - screen((x0 + PAD, y0 + PAD, z0 + 0.03))[0]), far[1]))
check("стрелка X двигает только по X",
      abs(float(lamp[2]) - y0) < 0.02 and float(lamp[1]) > x0 + 0.5,
      "(x %.2f -> %.2f, y %.2f -> %.2f)" % (x0, float(lamp[1]), y0, float(lamp[2])))
check("лампа, снятая со стола, опустилась на пол", float(lamp[3]) < 0.02,
      "(z %.3f)" % float(lamp[3]))

ed.model.furniture = []
sc = ed.model.place_furniture("wall_sconce", 1.3, 5.5)
ed.selected_entry = sc
handles = set(ed._gizmo_handles(sc))
check("у бра нет ручек 'от стены'", handles == {"along", "along-", "z"}, str(sorted(handles)))
x_wall = float(sc[1])
a = screen(ed._gizmo_handles(sc)["along"])
into_room = screen((PAD + 5.0, float(sc[2]) + PAD + 2.0, float(sc[3])))
drag(a, into_room)
check("бра, потянутое в комнату, осталось на стене",
      abs(float(sc[1]) - x_wall) < 1e-3, "(x %.3f, стена %.3f)" % (float(sc[1]), x_wall))
zh = screen(ed._gizmo_handles(sc)["z"])
za = float(sc[3])
drag(zh, (zh[0], zh[1] - 90))
check("ручка высоты поднимает бра", float(sc[3]) > za + 0.1,
      "(z %.2f -> %.2f)" % (za, float(sc[3])))
lk = ed.model.place_furniture("locker", 1.3, 8.0)
ed.selected_entry = lk
check("у шкафчика (стоит у стены) нет ручки высоты",
      set(ed._gizmo_handles(lk)) == {"along", "along-"})

ed.model.furniture = []
box = ed.model.place_furniture("crate", 5.5, 5.5, facing=0.0, snap=False)
ed.selected_entry = box
ed._set_gizmo_mode("turn")
ed.selected_entry = box
px, py = PAD + 5.5, PAD + 5.5
R = ed.GIZMO_RING * ed._gizmo_scale(box)
ring = screen((px + R, py, 0.03))
check("наведение на кольцо видит кольцо", ed._handle_under_cursor(*ring) == "turn")
ring2 = screen((px, py + R, 0.03))
f0 = float(box[4])
drag(ring, ring2)
turned = (float(box[4]) - f0) % math.tau
check("протяжка по кольцу на четверть поворачивает на четверть",
      abs(turned - math.pi / 2) < 0.12 or abs(turned - 3 * math.pi / 2) < 0.12,
      "(%.0f°)" % math.degrees(turned))

ed.model.furniture = []
row = [ed.model.place_furniture("locker", 1.6, 3.0 + i * 0.9, facing=0.0, snap=False)
       for i in range(3)]
ed.camera.x, ed.camera.y, ed.camera.z = PAD + 6.0, PAD + 3.9, 2.4
ed.camera.yaw, ed.camera.pitch = math.radians(180), -0.30
ed._set_gizmo_mode("magnet")
ed.selection = []
for lkr in row:
    ed._handle_mousedown(Ev(body_px(lkr)))
check("в режиме магнита клики набирают выделение", len(ed.selection) == 3,
      "(%d)" % len(ed.selection))
gaps0 = [round(float(b[2]) - float(a[2]), 3) for a, b in zip(row, row[1:])]
ed._handle_keydown(type("K", (), {"key": pygame.K_RETURN, "scancode": 0, "unicode": ""})())
gaps = [round(abs(float(b[2]) - float(a[2])), 3) for a, b in zip(row, row[1:])]
from room_editor.furniture_model import MAGNET_GAP
want = round(2 * PROP_DEFS["locker"]["hw"] + MAGNET_GAP, 3)
check("Enter смыкает ряд вплотную", all(abs(g - want) < 2e-3 for g in gaps),
      "(зазоры %s -> %s, нужно %s)" % (gaps0, gaps, want))
ed._handle_keydown(type("K", (), {"key": pygame.K_ESCAPE, "scancode": 0, "unicode": ""})())
check("Esc в режиме магнита снимает выделение и не закрывает редактор",
      ed.selection == [] and ed.running)

ed.model.furniture = []
pile = [ed.model.place_furniture("bed", 5.5, 5.5, snap=False) for _ in range(30)]
ed._set_gizmo_mode("move")
ed.selected_entry = pile[0]
ed.selection = [pile[0]]
fx = screen(ed._gizmo_handles(pile[0])["free"])
ed._handle_mousedown(Ev(fx))
t0 = time.perf_counter()
for k in range(20):
    ed._gizmo_drag(fx[0] + k * 3, fx[1])
dt = (time.perf_counter() - t0) / 20.0
ed._end_drag()
check("шаг протяжки горы из 30 кроватей < 16 мс", dt < 1.0 / 60.0, "(%.2f мс)" % (dt * 1000))
check("вся гора поехала", all(abs(float(b[1]) - float(pile[0][1])) < 1e-3 for b in pile))

from game import i18n
ed.model.furniture = []
sc = ed.model.place_furniture("wall_sconce", 1.3, 5.5)
ed.selection = [sc]
ed.selected_entry = sc
ed._set_gizmo_mode("turn")
ed.selected_entry = sc
spx, spy = ed._furn_world_pos(sc)
R = ed.GIZMO_RING * ed._gizmo_scale(sc)
ring = screen((spx + R, spy, float(sc[3]) + 0.03))
check("у бра нет кольца поворота", ed._handle_under_cursor(*ring) is None)
f0 = float(sc[4])
drag(body_px(sc), ring)
check("бра не поворачивается протяжкой", float(sc[4]) == f0, "(%.3f -> %.3f)" % (f0, float(sc[4])))
check("...и редактор говорит почему", ed.message == i18n.t("editor.msg.no_turn_wall"))
check("бра не поворачивается и через модель", not ed.model.rotate_entry(sc, math.pi / 2) and float(sc[4]) == f0)
pipe = ed.model.place_furniture("pipes", 1.3, 5.2)
pipe2 = ed.model.place_furniture("pipes", 1.3, 6.9)
check("труба встаёт в центр клетки вдоль стены",
      abs(float(pipe[2]) - 5.5) < 1e-6 and abs(float(pipe2[2]) - 6.5) < 1e-6,
      "(y %.3f, %.3f)" % (float(pipe[2]), float(pipe2[2])))
ed._set_gizmo_mode("move")
ed.selection = [pipe]
ed.selected_entry = pipe
check("у трубы нет ручек", ed._gizmo_handles(pipe) == {})
before = list(pipe)
drag(body_px(pipe), screen((PAD + 1.3, PAD + 8.5, 0.4)))
check("трубу нельзя утащить", list(pipe) == before, str(pipe))
check("...и редактор говорит почему", ed.message == i18n.t("editor.msg.docked_fixed"))
check("магнит трубу не двигает", ed.model.magnet([pipe, pipe2]) == 0 and list(pipe) == before)

for v in ("camera", "grid"):
    ed.view_mode = v
    ed._draw()
print("  оба вида нарисованы с гизмо")
print("ПРОВАЛОВ: %d" % len(fails))
sys.exit(1 if fails else 0)
