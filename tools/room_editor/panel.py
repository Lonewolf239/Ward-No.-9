import pygame

from game import settings as S
from game import i18n
from game.props import HAND_FURNITURE_BY_KIND, HAND_FURNITURE_KINDS, ZONE_HAND_FURNITURE_BY_KIND
from tools.room_editor.room_model import (
    DOOR_KINDS, KINDS_BY_FLOOR, WALL_MATERIALS, list_saved_ids, required_fixture_kind,
)
from tools.room_editor import upload as up
from tools.room_editor import zone_model as zm

BG = (18, 18, 22)
ROW_BG = (34, 34, 40)
ROW_BG_ACTIVE = (70, 110, 90)
ROW_BG_QUEST_ACTIVE = (150, 95, 40)
ROW_BORDER = (60, 60, 68)
TEXT = (220, 220, 220)
TEXT_DIM = (150, 150, 155)
TOOL_LABELS = ("furniture", "wall", "floor", "door")


def _tool_label(tool):
    return i18n.t(f"editor.tool.{tool}")


def _door_kind_label(kind):
    return i18n.t(f"editor.door_kind.{kind}")


def _door_scope_label(scope):
    return i18n.t(f"editor.door_scope.{scope}")


def _floor_label(floor):
    return i18n.t(f"editor.floor.{floor}")


def _kind_label(kind):
    return i18n.t(f"editor.kind.{kind}")


def _furniture_label(kind):
    return i18n.t(f"editor.furniture.{kind}")


def _material_label(mat):
    return i18n.t(f"editor.material.{mat}")


def _furniture_kinds_for(kind, floor):
    kinds = set(HAND_FURNITURE_BY_KIND.get(kind, HAND_FURNITURE_KINDS))
    required = required_fixture_kind(kind, floor)
    if required:
        kinds.add(required)
    return sorted(kinds)


def _zone_furniture_kinds_for(kind):
    return sorted(ZONE_HAND_FURNITURE_BY_KIND.get(kind, ()))


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

    def layout(self, model, view_mode, natural_color=False, testing_floor=None, editor_mode="room", dev_mode=True):
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
            header(i18n.t("editor.ui.test_header"))
            label(i18n.t(spec["title"]))
            label(i18n.t("editor.ui.test_hint"))
            next_row(h=0, gap=8)
            row(i18n.t("editor.ui.reroll"), "test_reroll", h=40)
            next_row(h=40)
            color_label = i18n.t("editor.ui.color_natural_short") if natural_color \
                else i18n.t("editor.ui.color_bright_short")
            row(color_label, "toggle_natural_color", active=natural_color, h=36)
            next_row(h=36, gap=16)
            row(i18n.t("editor.ui.test_back"), "test_exit", h=42)
            next_row(h=42)
            self.content_h = y + 14
            return

        if self.mode == "browse":
            header(i18n.t("editor.ui.browse_zones" if editor_mode == "zone" else "editor.ui.browse_rooms"))
            label(i18n.t("editor.ui.browse_hint"))
            row(i18n.t("editor.ui.back"), "browse_back")
            next_row()
            ids = zm.list_saved_ids() if editor_mode == "zone" else list_saved_ids()
            for rid in ids:
                row(rid, f"load:{rid}", h=32)
                next_row(h=32, gap=4)
            self.content_h = y + 14
            return

        if self.mode == "import_browse":
            header(i18n.t("editor.ui.import_header"))
            label(i18n.t("editor.ui.import_hint", dir=up.INCOMING_DIR))
            row(i18n.t("editor.ui.back"), "browse_back")
            next_row()
            names = up.list_incoming()
            if not names:
                label(i18n.t("editor.ui.import_empty"))
            for name in names:
                row(name, f"import:{name}", h=32)
                next_row(h=32, gap=4)
            self.content_h = y + 14
            return

        mode_label = i18n.t("editor.ui.mode_zone" if editor_mode == "zone" else "editor.ui.mode_room")
        row(mode_label, "toggle_editor_mode", active=True, h=40)
        next_row(h=40, gap=4)

        view_label = i18n.t("editor.ui.view_camera" if view_mode == "camera" else "editor.ui.view_grid")
        row(view_label, "toggle_view", active=True, h=42)
        next_row(h=42, gap=4)

        color_label = i18n.t("editor.ui.color_natural" if natural_color else "editor.ui.color_bright")
        row(color_label, "toggle_natural_color", active=natural_color, h=36)
        next_row(h=36, gap=12)

        is_zone = editor_mode == "zone"
        id_suffix = "" if is_zone else f"  ({_floor_label(model.floor)})"
        label(f"{model.id}{id_suffix}")
        arrow = "▲" if self.settings_open else "▼"
        settings_label = i18n.t("editor.ui.settings_zone" if is_zone else "editor.ui.settings_room")
        row(f"{settings_label} {arrow}", "toggle_settings")
        next_row()

        if self.settings_open:
            arrow2 = "▲" if self.kind_open else "▼"
            row(i18n.t("editor.ui.type_label", label=_kind_label(model.kind)) + f"  {arrow2}", "toggle_kind_open")
            next_row()
            if self.kind_open:
                kind_list = zm.ZONE_KINDS if is_zone else KINDS_BY_FLOOR[model.floor]
                for k in kind_list:
                    row(_kind_label(k), f"kind:{k}", active=(k == model.kind), h=30)
                    next_row(h=30, gap=2)

            if not is_zone:
                floor_label = i18n.t("editor.ui.floor_upper_full" if model.floor == "upper"
                                      else "editor.ui.floor_basement_full")
                row(floor_label, "toggle_floor")
                next_row()

                label(i18n.t("editor.ui.size_label", w=model.w, h=model.h))
                row(i18n.t("editor.ui.width_minus"), "w-", rw=w // 2 - 3)
                row(i18n.t("editor.ui.width_plus"), "w+", rx=x + w // 2 + 3, rw=w // 2 - 3)
                next_row()
                row(i18n.t("editor.ui.height_minus"), "h-", rw=w // 2 - 3)
                row(i18n.t("editor.ui.height_plus"), "h+", rx=x + w // 2 + 3, rw=w // 2 - 3)
                next_row()
            else:
                label(i18n.t("editor.ui.size_fixed", w=model.w, h=model.h))

            label(i18n.t("editor.ui.weight_label", weight=f"{model.weight:.2f}"))
            row("-", "weight-", rw=w // 2 - 3)
            row("+", "weight+", rx=x + w // 2 + 3, rw=w // 2 - 3)
            next_row()

            if not is_zone:
                label(i18n.t("editor.ui.material_preview"))
                mw = w // len(WALL_MATERIALS)
                for i, mat in enumerate(WALL_MATERIALS):
                    row(_material_label(mat), f"material:{mat}", active=(mat == model.wall_material_preview),
                        h=32, rx=x + i * mw, rw=mw - 4)
                next_row(h=32, gap=14)

        tool_list = TOOL_LABELS
        header(i18n.t("editor.ui.tool_header"))
        tw = w // 2
        for i, tool in enumerate(tool_list):
            col, r = i % 2, i // 2
            row(_tool_label(tool), f"tool:{tool}", active=(self.current_tool == tool),
                rx=x + col * tw, rw=tw - 4, h=40)
            if col == 1:
                next_row(h=40)
        if len(tool_list) % 2:
            next_row(h=40)

        if self.current_tool == "door" and is_zone:
            header(i18n.t("editor.ui.interior_door_header"))
            label(i18n.t("editor.ui.zone_edge_hint1"))
            label(i18n.t("editor.ui.zone_edge_hint2"))
            next_row(h=0, gap=8)
            for kind in DOOR_KINDS:
                if kind == "random":
                    continue
                row(_door_kind_label(kind), f"door_kind:{kind}", active=(kind == self.current_door_kind), h=36)
                next_row(h=36)
            label(i18n.t("editor.ui.door_remove_hint"))
            next_row(h=0, gap=6)
        elif self.current_tool == "door":
            header(i18n.t("editor.ui.border_door_header"))
            for scope in ("link", "local"):
                row(_door_scope_label(scope), f"door_scope:{scope}", active=(scope == self.current_door_scope), h=32)
                next_row(h=32, gap=2)
            label(i18n.t("editor.ui.local_door_hint1"))
            label(i18n.t("editor.ui.local_door_hint2"))
            next_row(h=0, gap=8)

            header(i18n.t("editor.ui.door_kind_header"))
            for kind in DOOR_KINDS:
                row(_door_kind_label(kind), f"door_kind:{kind}", active=(kind == self.current_door_kind), h=36)
                next_row(h=36)
            label(i18n.t("editor.ui.door_remove_hint"))
            next_row(h=0, gap=6)

        if self.current_tool == "furniture":
            required_kind = None if is_zone else required_fixture_kind(model.kind, model.floor)
            if required_kind:
                header(i18n.t("editor.ui.required_furniture_header", label=_furniture_label(required_kind)))
                row(_furniture_label(required_kind), f"furniture_kind:{required_kind}",
                    active=(required_kind == self.current_furniture_kind), h=36, quest=True)
                next_row(h=36, gap=12)

            kinds_here = _zone_furniture_kinds_for(model.kind) if is_zone else _furniture_kinds_for(model.kind, model.floor)
            header(i18n.t("editor.ui.furniture_header"))
            col_w = w // 2
            for i, kind in enumerate(kinds_here):
                col, r = i % 2, i // 2
                row(_furniture_label(kind), f"furniture_kind:{kind}", active=(kind == self.current_furniture_kind),
                    h=30, rx=x + col * col_w, rw=col_w - 4)
                if col == 1:
                    next_row(h=30, gap=4)
            if len(kinds_here) % 2:
                next_row(h=30, gap=4)

        header(i18n.t("editor.ui.file_header"))
        row(i18n.t("editor.ui.new_zone" if is_zone else "editor.ui.new_room"), "new", h=36)
        next_row(h=36)
        row(i18n.t("editor.ui.open_list"), "list", h=36)
        next_row(h=36)
        if dev_mode:
            row(i18n.t("editor.ui.import_button"), "import_list", h=36)
            next_row(h=36)
        row(i18n.t("editor.ui.save" if dev_mode else "editor.ui.save_upload"), "save", h=42)
        next_row(h=42)
        row(i18n.t("editor.ui.validate"), "validate", h=36)
        next_row(h=36, gap=14)

        header(i18n.t("editor.ui.test_floor_header"))
        label(i18n.t("editor.ui.test_floor_hint"))
        for i, spec in enumerate(S.FLOOR_SPECS):
            row(i18n.t(spec["title"]).split(" - ")[-1], f"test_floor:{i}", h=34)
            next_row(h=34, gap=4)

        self.content_h = y + 14

    def draw(self, surf, model, view_mode, natural_color=False, testing_floor=None, editor_mode="room", dev_mode=True):
        self.layout(model, view_mode, natural_color, testing_floor, editor_mode, dev_mode)
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

    def handle_click(self, mx, my, model, view_mode, natural_color=False, testing_floor=None, editor_mode="room",
                      dev_mode=True):
        self.layout(model, view_mode, natural_color, testing_floor, editor_mode, dev_mode)
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
        if action == "import_list":
            self.mode = "import_browse"; return None
        if action.startswith("import:"):
            self.mode = "normal"
            return action
        return action
