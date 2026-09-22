import math

from game import settings as S

MAX_LINES = 300
PROMPT = "> "

GIVE_KINDS = ("battery", "fuse", "valve_key", "key", "cutters", "lighter",
              "paper_map", "pencil", "map_sheet", "sanity_pill", "all")


def _num(text, what="value"):
    try:
        return float(text)
    except (TypeError, ValueError):
        raise ValueError("%s: expected a number, got %r" % (what, text))


def _parse_value(text):
    low = text.lower()
    if low in ("true", "on", "yes"):
        return True
    if low in ("false", "off", "no"):
        return False
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


class DebugConsole:
    def __init__(self):
        self.open = False
        self.line = ""
        self.lines = []
        self.history = []
        self.history_pos = None
        self.scroll = 0
        self.caret_t = 0.0
        self.god = False
        self.noclip = False
        self.time_scale = 1.0

    def echo(self, text, kind="out"):
        for part in str(text).split("\n"):
            self.lines.append((part, kind))
        del self.lines[:-MAX_LINES]
        self.scroll = 0

    def fail(self, text):
        self.echo(text, "err")

    def toggle(self, app):
        self.open = not self.open
        if self.open:
            self.line = ""
            self.history_pos = None
            if not self.lines:
                self.echo("ward9 console. `help` lists commands, `help <cmd>` explains one.")
                self.echo("The world is stopped while this is open.")
        if self.open:
            app._release_mouse()
        else:
            app._grab_mouse_for_play()

    def tick(self, dt):
        self.caret_t += dt

    def handle_key(self, event, app):
        import pygame
        if not self.open:
            return False
        if event.key in (pygame.K_ESCAPE, pygame.K_BACKQUOTE):
            self.toggle(app)
            return True
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            text = self.line.strip()
            self.line = ""
            self.history_pos = None
            if text:
                self.history.append(text)
                self.echo(PROMPT + text, "in")
                self.run(app, text)
            return True
        if event.key == pygame.K_BACKSPACE:
            self.line = self.line[:-1]
            return True
        if event.key == pygame.K_UP:
            self._recall(-1)
            return True
        if event.key == pygame.K_DOWN:
            self._recall(1)
            return True
        if event.key == pygame.K_PAGEUP:
            self.scroll = min(len(self.lines), self.scroll + 5)
            return True
        if event.key == pygame.K_PAGEDOWN:
            self.scroll = max(0, self.scroll - 5)
            return True
        if event.key == pygame.K_TAB:
            self._complete()
            return True
        ch = getattr(event, "unicode", "")
        if ch and ch.isprintable() and ch != "`" and len(self.line) < 120:
            self.line += ch
        return True

    def _recall(self, step):
        if not self.history:
            return
        if self.history_pos is None:
            self.history_pos = len(self.history)
        self.history_pos = max(0, min(len(self.history), self.history_pos + step))
        self.line = "" if self.history_pos >= len(self.history) else self.history[self.history_pos]

    def _complete(self):
        head = self.line.split(" ")[0]
        if " " in self.line or not head:
            return
        hits = sorted(n for n in COMMANDS if n.startswith(head))
        if len(hits) == 1:
            self.line = hits[0] + " "
        elif hits:
            self.echo("  ".join(hits))

    def run(self, app, text):
        name, _, rest = text.partition(" ")
        cmd = COMMANDS.get(name.lower())
        if cmd is None:
            near = sorted(n for n in COMMANDS if n.startswith(name.lower()[:2]))
            self.fail("no command %r%s" % (name, (". did you mean: " + ", ".join(near)) if near else ""))
            return
        args = rest.split()
        try:
            cmd[0](self, app, args)
        except ValueError as e:
            self.fail(str(e))
        except Exception as e:
            self.fail("%s: %s" % (type(e).__name__, e))

    def cmd_help(self, app, args):
        if args:
            name = args[0].lower()
            cmd = COMMANDS.get(name)
            if cmd is None:
                self.fail("no command %r" % name)
                return
            self.echo("%s - %s" % (cmd[1], cmd[2]))
            return
        self.echo("commands (help <cmd> for detail, tab completes):")
        for name in sorted(COMMANDS):
            self.echo("  %-9s %s" % (name, COMMANDS[name][2]))

    def cmd_clear(self, app, args):
        self.lines = []

    def cmd_cfg(self, app, args):
        if not args:
            self.fail("cfg <NAME> [value] - names are the CAPITALS in settings.py")
            return
        name = args[0].upper()
        if not hasattr(S, name):
            hits = sorted(n for n in dir(S) if n.isupper() and args[0].upper() in n)[:12]
            self.fail("no setting %s%s" % (name, (". close: " + ", ".join(hits)) if hits else ""))
            return
        old = getattr(S, name)
        if len(args) == 1:
            self.echo("%s = %r" % (name, old))
            return
        if not isinstance(old, (int, float, bool)):
            self.fail("%s is %s - only numbers and flags can be set from here"
                      % (name, type(old).__name__))
            return
        new = _parse_value(args[1])
        if isinstance(old, bool):
            new = bool(new)
        elif isinstance(new, bool):
            self.fail("%s is a number" % name)
            return
        elif isinstance(old, int) and not isinstance(old, bool):
            new = int(new)
        else:
            new = float(new)
        setattr(S, name, new)
        self.echo("%s: %r -> %r" % (name, old, new))

    def cmd_player(self, app, args):
        p = app.player
        fields = {
            "sanity": ("sanity", 0.0, S.SANITY_MAX),
            "battery": ("battery", 0.0, 100.0),
            "stamina": ("stamina", 0.0, S.STAMINA_MAX),
            "carried": ("carried", 0, 99),
        }
        if not args:
            self.echo("pos %.2f, %.2f  angle %.0f  sanity %.0f  battery %.0f  stamina %.0f  carried %d"
                      % (p.x, p.y, math.degrees(p.angle) % 360, p.sanity, p.battery,
                         p.stamina, p.carried))
            self.echo("lighter %s  map %s  cutters %s  pencil %.1f  hiding %s"
                      % (p.has_lighter, p.has_map, p.has_cutters, p.pencil_ink, p.is_hiding))
            self.echo("settable: " + ", ".join(sorted(fields)))
            return
        field = args[0].lower()
        if field not in fields:
            self.fail("player <%s> [value]" % "|".join(sorted(fields)))
            return
        attr, lo, hi = fields[field]
        if len(args) == 1:
            self.echo("%s = %s" % (field, getattr(p, attr)))
            return
        v = max(lo, min(hi, _num(args[1], field)))
        setattr(p, attr, type(lo)(v))
        if field == "stamina":
            p.stamina_locked = False
        self.echo("%s = %s" % (field, getattr(p, attr)))

    def cmd_give(self, app, args):
        if not args:
            self.echo("give <%s>" % "|".join(GIVE_KINDS))
            return
        want = args[0].lower()
        if want not in GIVE_KINDS:
            self.fail("give: no such thing %r. try: %s" % (want, ", ".join(GIVE_KINDS)))
            return
        kinds = [k for k in GIVE_KINDS if k != "all"] if want == "all" else [want]
        p = app.player
        for kind in kinds:
            if kind == "battery":
                p.add_battery()
            elif kind == "cutters":
                p.has_cutters, p.cutters_broken = True, False
            elif kind == "lighter":
                p.has_lighter = True
            elif kind == "paper_map":
                p.has_map = True
                p.pencil_ink = min(S.PENCIL_MAX_CARRIED * S.PENCIL_SHEETS,
                                   p.pencil_ink + S.PENCIL_SHEETS)
                app._add_map_sheet()
            elif kind == "pencil":
                p.pencil_ink = min(S.PENCIL_MAX_CARRIED * S.PENCIL_SHEETS,
                                   p.pencil_ink + S.PENCIL_SHEETS)
            elif kind == "map_sheet":
                app._add_map_sheet()
            elif kind == "sanity_pill":
                app.sanity_boost_timer = S.SANITY_PILL_DURATION
            else:
                p.carried += 1
        self.echo("gave " + ", ".join(kinds))

    def cmd_tp(self, app, args):
        p = app.player
        if not args:
            self.fail("tp <x> <y> | tp monster | tp exit | tp start")
            return
        where = args[0].lower()
        if where == "monster":
            m = app.monster
            tx, ty = m.x, m.y
        elif where == "exit":
            ex = app.exit_prop
            if ex is None:
                self.fail("this floor has no exit prop")
                return
            tx, ty = ex.x, ex.y
        elif where == "start":
            tx, ty = app.maze.start
        else:
            if len(args) < 2:
                self.fail("tp <x> <y>")
                return
            tx, ty = _num(args[0], "x"), _num(args[1], "y")
        p.x, p.y = tx, ty
        p.peek_x, p.peek_y = tx, ty
        self.echo("player -> %.2f, %.2f" % (tx, ty))

    def cmd_monster(self, app, args):
        from game.entities import Monster
        m = app.monster
        states = (Monster.PATROL, Monster.INVESTIGATE, Monster.HUNT, Monster.STALK, Monster.GUARD)
        if not args:
            self.echo("state %s  at %.2f, %.2f  dist %.1f  alert %.2f  speed x%.2f  vision x%.2f"
                      % (m.state, m.x, m.y,
                         math.hypot(m.x - app.player.x, m.y - app.player.y),
                         m.alert_level, m.speed_mult, m.vision_mult))
            self.echo("monster <%s> | here | speed <x> | vision <x> | alert <0-1> | freeze | thaw"
                      % "|".join(states))
            return
        what = args[0].lower()
        if what in states:
            m.state = what
            m.target_cell = None
            m.path = []
            self.echo("monster state = %s" % what)
        elif what == "here":
            p = app.player
            m.x, m.y = p.x, p.y
            m.path = []
            self.echo("monster -> %.2f, %.2f" % (m.x, m.y))
        elif what == "freeze":
            m.speed_mult = 0.0
            self.echo("monster frozen (speed x0)")
        elif what == "thaw":
            m.speed_mult = app.spec.get("speed_mult", 1.0)
            self.echo("monster speed x%.2f" % m.speed_mult)
        elif what in ("speed", "vision", "alert"):
            if len(args) < 2:
                self.fail("monster %s <value>" % what)
                return
            v = _num(args[1], what)
            if what == "speed":
                m.speed_mult = v
            elif what == "vision":
                m.vision_mult = v
            else:
                m.alert_level = max(0.0, min(1.0, v))
            self.echo("monster %s = %.2f" % (what, v))
        else:
            self.fail("monster: no such thing %r" % what)

    def cmd_anomaly(self, app, args):
        if not args:
            self.echo("anomaly = %s (stage of this floor)" % app.anomaly_stage)
            return
        stage = int(_num(args[0], "stage"))
        if not 0 <= stage <= 3:
            self.fail("anomaly 0..3")
            return
        app.anomaly_stage = stage
        self.echo("anomaly = %d (this floor only - reload the floor to rebuild with it)" % stage)

    def cmd_floor(self, app, args):
        if not args:
            self.echo("floor = %s, seed %s" % ((app.spec or {}).get("key"), app.floor_seed))
            return
        which = args[0].lower()
        if which == "debug":
            app._start_debug_level()
        else:
            app._load_floor(int(_num(args[0], "floor")))
        app._begin_playing()
        self.echo("loaded %s, seed %s" % ((app.spec or {}).get("key"), app.floor_seed))

    def cmd_god(self, app, args):
        self.god = not self.god if not args else bool(_parse_value(args[0]))
        self.echo("god = %s (the catch cannot start)" % self.god)

    def cmd_noclip(self, app, args):
        self.noclip = not self.noclip if not args else bool(_parse_value(args[0]))
        app.player.noclip = self.noclip
        self.echo("noclip = %s" % self.noclip)

    def cmd_time(self, app, args):
        if not args:
            self.echo("time = x%.2f" % self.time_scale)
            return
        self.time_scale = max(0.0, min(8.0, _num(args[0], "scale")))
        self.echo("time = x%.2f" % self.time_scale)

    def cmd_scare(self, app, args):
        app._start_catch_sequence()
        self.open = False
        self.echo("catch sequence started")

    def cmd_hallu(self, app, args):
        kinds = {"eyes": "_start_hallu_eyes", "pulse": "_start_hallu_pulse",
                 "door": "_start_hallu_door_break"}
        if not args or args[0].lower() not in kinds:
            self.fail("hallu <%s>" % "|".join(sorted(kinds)))
            return
        getattr(app, kinds[args[0].lower()])()
        self.echo("spawned %s" % args[0].lower())

    def cmd_glance(self, app, args):
        p, m = app.player, app.monster
        if not p.is_hiding or p.hidden_in is None:
            self.fail("glance: get in a locker first - the event is about being looked at")
            return
        m._glance_cooldown = 0.0
        m._glance_armed = None
        m.glance_locker = p.hidden_in
        m.glance_t = 0.0
        m.glance_phase = "stop" if m._seen_from_locker(p.hidden_in) else "approach"
        self.open = False
        self.echo("glance: %s" % m.glance_phase)


COMMANDS = {
    "help": (DebugConsole.cmd_help, "help [cmd]", "list commands, or explain one"),
    "clear": (DebugConsole.cmd_clear, "clear", "empty the scrollback"),
    "cfg": (DebugConsole.cmd_cfg, "cfg <NAME> [value]", "read or set a settings.py constant, live"),
    "player": (DebugConsole.cmd_player, "player [field] [value]", "show the player, or set sanity/battery/stamina/carried"),
    "give": (DebugConsole.cmd_give, "give <thing|all>", "hand over a pickup"),
    "tp": (DebugConsole.cmd_tp, "tp <x> <y>|monster|exit|start", "move the player"),
    "monster": (DebugConsole.cmd_monster, "monster [what] [value]", "show it, or set state/speed/vision/alert, or move it"),
    "anomaly": (DebugConsole.cmd_anomaly, "anomaly [0-3]", "show or set this floor's anomaly stage"),
    "floor": (DebugConsole.cmd_floor, "floor [0|1|2|debug]", "show the floor, or load one"),
    "god": (DebugConsole.cmd_god, "god [on|off]", "the catch cannot start"),
    "noclip": (DebugConsole.cmd_noclip, "noclip [on|off]", "walk through walls and furniture"),
    "time": (DebugConsole.cmd_time, "time [scale]", "speed the world up or slow it down"),
    "scare": (DebugConsole.cmd_scare, "scare", "play the catch right now"),
    "hallu": (DebugConsole.cmd_hallu, "hallu <eyes|pulse|door>", "spawn a hallucination"),
    "glance": (DebugConsole.cmd_glance, "glance", "force the locker look (be in a locker)"),
}
