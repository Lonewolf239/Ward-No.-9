import pygame

from game import settings as S
from game import i18n
from game.props import HAND_FURNITURE_BY_KIND, HAND_FURNITURE_KINDS
from tools.room_editor.room_model import (
    DOOR_KINDS, KINDS_BY_FLOOR, WALL_MATERIALS, list_saved_ids, required_fixture_kind,
)

BG = (18, 18, 22)
ROW_BG = (34, 34, 40)
ROW_BG_ACTIVE = (70, 110, 90)
ROW_BG_QUEST_ACTIVE = (150, 95, 40)
ROW_BORDER = (60, 60, 68)
TEXT = (220, 220, 220)
TEXT_DIM = (150, 150, 155)
DOOR_KIND_LABELS = {"passage": "Проход", "door": "Дверь", "broken": "Сломана", "window": "Окно",
                     "random": "Случайная"}
DOOR_SCOPE_LABELS = {"link": "Между комнатами", "local": "Локальная"}
TOOL_LABELS = (("furniture", "Мебель"), ("wall", "Стена"), ("floor", "Ластик"), ("door", "Двери"))

FLOOR_LABELS_RU = {"upper": "жилой", "basement": "подвал"}

KIND_LABELS_RU = {
    "ward": "Палата", "office": "Кабинет", "morgue": "Морг", "cafeteria": "Столовая",
    "utility": "Техпомещение", "plain": "Комната", "entrance": "Вход", "stairwell": "Лестница",
    "exit": "Выход", "unlocker": "Разблокировка", "corridor": "Коридор",
    "boiler": "Котельная", "storage": "Склад", "cell": "Камера",
    "tech_corridor": "Техкоридор", "vent": "Вентиляция",
}

FURNITURE_LABELS_RU = {
    "bed": "Кровать", "desk": "Письменный стол", "table": "Стол", "shelf": "Полка",
    "gurney": "Каталка", "crate": "Ящик", "barrel": "Бочка", "pipes": "Трубы",
    "chair": "Стул", "cabinet": "Шкаф", "sink": "Раковина", "trash_can": "Урна",
    "vending": "Автомат", "lamp_desk": "Настольная лампа", "wall_sconce": "Настенный светильник",
    "sign_exit": "Табличка «Выход»", "monitor": "Монитор", "locker": "Шкафчик",
    "fuse_box": "Электрощит", "valve_panel": "Паровой щит", "elevator": "Лифт", "hatch": "Люк",
}

MATERIAL_LABELS_RU = {"concrete": "Бетон", "tile": "Плитка", "metal": "Металл", "blood": "Кровь"}


def _kind_label(kind):
    return KIND_LABELS_RU.get(kind, kind)


def _furniture_label(kind):
    return FURNITURE_LABELS_RU.get(kind, kind)


def _material_label(mat):
    return MATERIAL_LABELS_RU.get(mat, mat)


def _furniture_kinds_for(kind, floor):
    kinds = set(HAND_FURNITURE_BY_KIND.get(kind, HAND_FURNITURE_KINDS))
    required = required_fixture_kind(kind, floor)
    if required:
        kinds.add(required)
    return sorted(kinds)


class Panel:
    def __init__(self, width=380):
        self.width = width
        self.current_tool = "furniture"
        self.current_furniture_kind = "shelf"
        self.current_door_kind = "door"
        self.current_door_scope = "link"
        self.mode = "normal"
        self.settings_open = False
        self.kind_open = False
        self.scroll_y = 0
        self.content_h = 0
        self._rows = []
        self._labels = []
        pygame.font.init()
        self.font = pygame.font.SysFont("consolas,monospace", 18)
        self.font_small = pygame.font.SysFont("consolas,monospace", 15)

    def scroll(self, dy_rows, viewport_h):
        max_scroll = max(0, self.content_h - viewport_h)
        self.scroll_y = max(0, min(max_scroll, self.scroll_y - dy_rows * 30))

    def layout(self, model, view_mode, natural_color=False, testing_floor=None):
        self._rows = []
        self._labels = []
        y = 14
        x = 14
        w = self.width - 28

        def row(label, action, active=False, h=38, rx=None, rw=None, quest=False):
            r = pygame.Rect(rx if rx is not None else x, y, rw if rw is not None else w, h)
            self._rows.append((r, action, label, active, quest))
            return r

        def next_row(h=38, gap=8):
            nonlocal y
            y += h + gap

        def label(text):
            nonlocal y
            self._labels.append((text, x, y))
            y += 20

        def header(text):
            nonlocal y
            y += 8
            self._labels.append((text, x, y))
            y += 22

        if testing_floor is not None:
            spec = S.FLOOR_SPECS[testing_floor]
            header("ТЕСТОВАЯ ГЕНЕРАЦИЯ")
            label(i18n.t(spec["title"]))
            label("Свободная камера, редактирование недоступно")
            next_row(h=0, gap=8)
            row("Перегенерировать (новый сид)", "test_reroll", h=40)
            next_row(h=40)
            color_label = "Цвет: ЕСТЕСТВЕННЫЙ" if natural_color else "Цвет: ЯРКИЙ"
            row(color_label, "toggle_natural_color", active=natural_color, h=36)
            next_row(h=36, gap=16)
            row("<- Назад к редактированию (Esc)", "test_exit", h=42)
            next_row(h=42)
            self.content_h = y + 14
            return

        if self.mode == "browse":
            header("СОХРАНЁННЫЕ КОМНАТЫ")
            label("клик = загрузить")
            row("< Назад", "browse_back")
            next_row()
            for rid in list_saved_ids():
                row(rid, f"load:{rid}", h=32)
                next_row(h=32, gap=4)
            self.content_h = y + 14
            return

        view_label = "Вид: КАМЕРА  (Tab -> сетка)" if view_mode == "camera" else "Вид: СЕТКА  (Tab -> камера)"
        row(view_label, "toggle_view", active=True, h=42)
        next_row(h=42, gap=4)

        color_label = "Цвет: ЕСТЕСТВЕННЫЙ (как в игре)" if natural_color else "Цвет: ЯРКИЙ (для точной расстановки)"
        row(color_label, "toggle_natural_color", active=natural_color, h=36)
        next_row(h=36, gap=12)

        label(f"{model.id}  ({FLOOR_LABELS_RU.get(model.floor, model.floor)})")
        arrow = "▲" if self.settings_open else "▼"
        row(f"Настройки комнаты {arrow}", "toggle_settings")
        next_row()

        if self.settings_open:
            row(f"Этаж: {'ЖИЛОЙ' if model.floor == 'upper' else 'ПОДВАЛ'}", "toggle_floor")
            next_row()
            label("F2 = переименовать комнату")

            arrow2 = "▲" if self.kind_open else "▼"
            row(f"Тип: {_kind_label(model.kind)}  {arrow2}", "toggle_kind_open")
            next_row()
            if self.kind_open:
                for k in KINDS_BY_FLOOR[model.floor]:
                    row(_kind_label(k), f"kind:{k}", active=(k == model.kind), h=30)
                    next_row(h=30, gap=2)

            label(f"Размер: {model.w} x {model.h}")
            row("- ширина", "w-", rw=w // 2 - 3)
            row("+ ширина", "w+", rx=x + w // 2 + 3, rw=w // 2 - 3)
            next_row()
            row("- высота", "h-", rw=w // 2 - 3)
            row("+ высота", "h+", rx=x + w // 2 + 3, rw=w // 2 - 3)
            next_row()

            label(f"Вес в генераторе: {model.weight:.2f}")
            row("-", "weight-", rw=w // 2 - 3)
            row("+", "weight+", rx=x + w // 2 + 3, rw=w // 2 - 3)
            next_row()

            label("Материал стен (предпросмотр)")
            mw = w // len(WALL_MATERIALS)
            for i, mat in enumerate(WALL_MATERIALS):
                row(_material_label(mat), f"material:{mat}", active=(mat == model.wall_material_preview),
                    h=32, rx=x + i * mw, rw=mw - 4)
            next_row(h=32, gap=14)

        header("ИНСТРУМЕНТ")
        tw = w // 2
        for i, (tool, text) in enumerate(TOOL_LABELS):
            col, r = i % 2, i // 2
            row(text, f"tool:{tool}", active=(self.current_tool == tool),
                rx=x + col * tw, rw=tw - 4, h=40)
            if col == 1:
                next_row(h=40)
        if len(TOOL_LABELS) % 2:
            next_row(h=40)

        if self.current_tool == "door":
            header("ГРАНИЦА КОМНАТЫ - ЧТО ОЗНАЧАЕТ НОВЫЙ ПРОЁМ")
            for scope in ("link", "local"):
                row(DOOR_SCOPE_LABELS[scope], f"door_scope:{scope}", active=(scope == self.current_door_scope), h=32)
                next_row(h=32, gap=2)
            label("«Локальная» не участвует в стыковке комнат -")
            label("годится для мини-комнаты вплотную к границе")
            next_row(h=0, gap=8)

            header("ЧТО СТАВИТЬ (клик по границе или щели в стене)")
            for kind in DOOR_KINDS:
                row(DOOR_KIND_LABELS[kind], f"door_kind:{kind}", active=(kind == self.current_door_kind), h=36)
                next_row(h=36)
            label("Клик по своей же двери - убрать")
            next_row(h=0, gap=6)

        if self.current_tool == "furniture":
            required_kind = required_fixture_kind(model.kind, model.floor)
            if required_kind:
                header(f"ОБЯЗАТЕЛЬНО: 1x {_furniture_label(required_kind)} в этой комнате")
                row(_furniture_label(required_kind), f"furniture_kind:{required_kind}",
                    active=(required_kind == self.current_furniture_kind), h=36, quest=True)
                next_row(h=36, gap=12)

            kinds_here = _furniture_kinds_for(model.kind, model.floor)
            header("МЕБЕЛЬ  (R=поворот, Del=удалить)")
            col_w = w // 2
            for i, kind in enumerate(kinds_here):
                col, r = i % 2, i // 2
                row(_furniture_label(kind), f"furniture_kind:{kind}", active=(kind == self.current_furniture_kind),
                    h=30, rx=x + col * col_w, rw=col_w - 4)
                if col == 1:
                    next_row(h=30, gap=4)
            if len(kinds_here) % 2:
                next_row(h=30, gap=4)

        header("ФАЙЛ")
        row("Новая комната", "new", h=36)
        next_row(h=36)
        row("Открыть список...", "list", h=36)
        next_row(h=36)
        row("Сохранить  (= добавить в игру)", "save", h=42)
        next_row(h=42)
        row("Проверить", "validate", h=36)
        next_row(h=36, gap=14)

        header("ТЕСТОВАЯ ГЕНЕРАЦИЯ ЭТАЖА")
        label("пролёт по реально сгенерированному этажу")
        for i, spec in enumerate(S.FLOOR_SPECS):
            row(i18n.t(spec["title"]).split(" - ")[-1], f"test_floor:{i}", h=34)
            next_row(h=34, gap=4)

        self.content_h = y + 14

    def draw(self, surf, model, view_mode, natural_color=False, testing_floor=None):
        self.layout(model, view_mode, natural_color, testing_floor)
        viewport_h = surf.get_height()
        max_scroll = max(0, self.content_h - viewport_h)
        self.scroll_y = max(0, min(max_scroll, self.scroll_y))
        surf.fill(BG)
        for text, lx, ly in self._labels:
            sy = ly - self.scroll_y
            if -20 <= sy <= viewport_h:
                txt = self.font_small.render(text, True, TEXT_DIM)
                surf.blit(txt, (lx, sy))
        for rect, action, text, active, quest in self._rows:
            sy = rect.y - self.scroll_y
            if sy < -rect.h or sy > viewport_h:
                continue
            screen_rect = pygame.Rect(rect.x, sy, rect.w, rect.h)
            color = (ROW_BG_QUEST_ACTIVE if quest else ROW_BG_ACTIVE) if active else ROW_BG
            pygame.draw.rect(surf, color, screen_rect)
            pygame.draw.rect(surf, ROW_BORDER, screen_rect, 1)
            txt = self.font_small.render(text, True, TEXT)
            surf.blit(txt, (screen_rect.x + 8, screen_rect.y + (screen_rect.h - txt.get_height()) // 2))

    def handle_click(self, mx, my, model, view_mode, natural_color=False, testing_floor=None):
        self.layout(model, view_mode, natural_color, testing_floor)
        cy = my + self.scroll_y
        for rect, action, text, active, quest in self._rows:
            if action is not None and rect.collidepoint(mx, cy):
                return self._apply(action, model)
        return None

    def _apply(self, action, model):
        if action == "browse_back":
            self.mode = "normal"
            return None
        if action.startswith("load:"):
            self.mode = "normal"
            return action
        if action == "toggle_view":
            return action
        if action == "toggle_natural_color":
            return action
        if action.startswith("test_floor:") or action in ("test_reroll", "test_exit"):
            return action
        if action == "toggle_settings":
            self.settings_open = not self.settings_open
            return None
        if action == "toggle_floor":
            model.set_floor("basement" if model.floor == "upper" else "upper")
            return None
        if action == "toggle_kind_open":
            self.kind_open = not self.kind_open
            return None
        if action.startswith("kind:"):
            model.set_kind(action.split(":", 1)[1])
            self.kind_open = False
            return None
        if action == "w-":
            model.resize(model.w - 1, model.h); return None
        if action == "w+":
            model.resize(model.w + 1, model.h); return None
        if action == "h-":
            model.resize(model.w, model.h - 1); return None
        if action == "h+":
            model.resize(model.w, model.h + 1); return None
        if action == "weight-":
            step = 0.01 if model.weight <= 0.2 else 0.1
            model.weight = max(0.01, round(model.weight - step, 2)); return None
        if action == "weight+":
            step = 0.01 if model.weight < 0.2 else 0.1
            model.weight = round(model.weight + step, 2); return None
        if action.startswith("material:"):
            model.wall_material_preview = action.split(":", 1)[1]; return None
        if action.startswith("tool:"):
            self.current_tool = action.split(":", 1)[1]; return None
        if action.startswith("door_kind:"):
            self.current_door_kind = action.split(":", 1)[1]; return None
        if action.startswith("door_scope:"):
            self.current_door_scope = action.split(":", 1)[1]; return None
        if action.startswith("furniture_kind:"):
            self.current_furniture_kind = action.split(":", 1)[1]; return None
        if action == "list":
            self.mode = "browse"; return None
        return action
