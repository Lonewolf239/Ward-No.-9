import os, sys, random

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_here))
sys.path.insert(0, _here)
os.environ["SDL_VIDEODRIVER"] = "offscreen"
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
pygame.display.flip = lambda: None

from room_editor.editor import Editor
from game import settings as S
from game.props import PROP_DEFS

random.seed(7)
ed = Editor(mode="dev")
print("editor built, mode", ed.editor_mode, "view", ed.view_mode)
import math
from room_editor.zone_model import ZoneModel
from room_editor.furniture_model import MAGNET_GAP

ed.model.resize(12, 12)
for _name, _model in (("room", ed.model), ("zone", ZoneModel(kind="open"))):
    for _y in range(1, _model.h - 1):
        for _x in range(1, _model.w - 1):
            _model.cells[_y][_x] = S.FLOOR
    _model.furniture = []
    _desk = _model.place_furniture("desk", 3.2, 3.4, facing=0.0, snap=False)
    assert _desk is not None, "%s: не поставилась мебель" % _name
    assert abs(float(_desk[1]) - 3.2) < 1e-6, "%s: точка размещения не сохранилась" % _name

    _top = _model.top_of(_desk)
    _lamp = _model.place_furniture("lamp_desk", 3.25, 3.45, snap=False)
    assert abs(float(_lamp[3]) - _top) < 1e-3, \
        "%s: лампа не встала на стол (%.3f, ждали %.3f)" % (_name, float(_lamp[3]), _top)
    assert _model.support_under(_lamp) is _desk, "%s: опора лампы определилась неверно" % _name

    assert _model.move_entry(_desk, 5.0, 5.0), "%s: стол не поехал" % _name
    assert abs(float(_lamp[1]) - 5.05) < 1e-3 and abs(float(_lamp[2]) - 5.05) < 1e-3, \
        "%s: лампа не поехала за столом" % _name
    _model.rotate_entry(_desk, math.pi / 2)
    assert abs(float(_lamp[1]) - 4.95) < 1e-3, "%s: лампа не повернулась вокруг стола" % _name

    _pile = [_model.place_furniture("crate", 8.0, 8.0, snap=False) for _ in range(3)]
    _zs = [round(float(c[3]), 3) for c in _pile]
    assert _zs[0] < _zs[1] < _zs[2], "%s: ящики не встали друг на друга: %s" % (_name, _zs)
    assert _model.support_under(_pile[2]) is _pile[1], "%s: верхний ящик не на среднем" % _name
    assert _model.move_entry(_pile[0], 9.5, 8.0), "%s: гора не поехала" % _name
    assert abs(float(_pile[2][1]) - 9.5) < 1e-3, "%s: верхний ящик остался на месте" % _name
    assert _model.remove_entry(_pile[0]) and not _model.furniture_on(9, 8), \
        "%s: снос нижнего ящика не убрал гору" % _name

    for _y in range(1, 6):
        _model.cells[_y][1] = S.WALL_CONCRETE
    _row = [_model.place_furniture("locker", 2.6, 2.0 + i * 0.9, facing=0.0, snap=False)
            for i in range(3)]
    assert all(l is not None for l in _row), "%s: шкафы не встали у стены" % _name
    _model.magnet(_row)
    _xs = {round(float(l[1]), 3) for l in _row}
    assert len(_xs) == 1, "%s: магнит не выровнял ряд по стене: %s" % (_name, _xs)
    _gaps = sorted(round(abs(float(a[2]) - float(b[2])), 3)
                   for a, b in zip(_row, _row[1:]))
    assert all(g <= 2 * PROP_DEFS["locker"]["hw"] + MAGNET_GAP + 1e-3 for g in _gaps), \
        "%s: магнит не сдвинул шкафы вплотную: %s" % (_name, _gaps)
    print("   %s: точка, опора, гора, перенос с грузом, поворот, магнит" % _name)

ed._rebuild_level()
for _view in ("camera", "grid"):
    ed.view_mode = _view
    ed._draw()
print("   нарисованы оба вида со стопкой и горой")

for idx, spec in enumerate(S.FLOOR_SPECS):
    ed._enter_test_floor(idx)
    print("   test floor %s: seed %s, %d props, maze %dx%d"
          % (spec["key"], ed.test_seed, len(ed._test_props), ed._test_maze.w, ed._test_maze.h))
    ed._draw()
ed._handle_panel_action("test_exit")
ed._draw()
print("drew every test floor and came back, no errors")
