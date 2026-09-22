import ast
import json
import os
import re
import string
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SOURCE_DIRS = ("game", os.path.join("tools", "room_editor"))
LOCALE_DIR = os.path.join(ROOT, "game", "locale_data")
LANGUAGES = ("en", "ru", "es", "fr", "de")

UI_MODULES = {os.path.join("game", "app.py")}
ALLOWED_LITERALS = {
    "WARD No. 9",
    "by Lonewolf239",
    "Darsin",
    "FPS: ",
    "AppData", "Roaming", "Library", "Application Support",
}
NON_UI_CALLS = {"print", "t", "get", "setdefault", "pop", "open", "getattr", "hasattr", "setattr", "add_argument",
                "startswith", "endswith", "join", "split", "replace", "strftime", "encode", "decode", "SysFont",
                "Font", "tostring", "fromstring", "frombuffer", "compile", "match", "search", "sub", "fullmatch",
                "Path", "execve", "run", "Popen", "urlopen", "Request", "loads", "dumps", "warn", "info", "debug",
                "error", "exception", "askopenfilename", "open_new", "environ"}


def load_locales():
    return {code: json.load(open(os.path.join(LOCALE_DIR, f"{code}.json"), encoding="utf-8")) for code in LANGUAGES}


def placeholders(s):
    return sorted({name for _, name, _, _ in string.Formatter().parse(s) if name})


def is_i18n_call(node):
    f = node.func
    return ((isinstance(f, ast.Attribute) and f.attr in ("t", "Text") and isinstance(f.value, ast.Name)
             and f.value.id == "i18n")
            or (isinstance(f, ast.Name) and f.id in ("t", "Text")))


def fstring_regex(node):
    parts = []
    for v in node.values:
        if isinstance(v, ast.Constant):
            parts.append(re.escape(v.value))
        else:
            parts.append(r"[^.]+?")
    return "^" + "".join(parts) + "$"


def looks_like_words(s):
    return re.search(r"[^\W\d_]{3,}", s) is not None


def human_literals(tree, rel):
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    docstrings = set()
    for node in ast.walk(tree):
        if (isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body
                and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)):
            docstrings.add(node.body[0].value)
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)) or node in docstrings:
            continue
        s = node.value
        if not looks_like_words(s) or s in ALLOWED_LITERALS or s.startswith(("http://", "https://")):
            continue
        if re.fullmatch(r"[a-z0-9_.:/\\*%{}+\-]+", s) or re.fullmatch(r"[A-Z0-9_]+", s):
            continue
        p, skip = parents.get(node), False
        while p is not None and not isinstance(p, ast.Call):
            if isinstance(p, (ast.Compare, ast.Subscript, ast.Raise, ast.Dict)) or (
                    isinstance(p, ast.keyword) and p.arg in ("encoding", "mode")):
                skip = True
                break
            p = parents.get(p)
        if skip:
            continue
        if isinstance(p, ast.Call):
            f = p.func
            name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            if name in NON_UI_CALLS or name.endswith(("Error", "Exception", "Warning")):
                continue
        found.append((s, rel, node.lineno))
    return found


def scan_sources():
    literal_keys, patterns, dynamic_calls, hardcoded, all_strings = [], [], [], [], set()
    all_fstrings = []
    for d in SOURCE_DIRS:
        for dirpath, _dirs, files in os.walk(os.path.join(ROOT, d)):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(dirpath, fn)
                rel = os.path.relpath(path, ROOT)
                tree = ast.parse(open(path, encoding="utf-8").read(), filename=rel)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str):
                        all_strings.add(node.value)
                    if isinstance(node, ast.JoinedStr):
                        all_fstrings.append(fstring_regex(node))
                    if not isinstance(node, ast.Call):
                        continue
                    if is_i18n_call(node) and node.args:
                        a = node.args[0]
                        if isinstance(a, ast.Constant) and isinstance(a.value, str):
                            literal_keys.append((a.value, rel, node.lineno))
                        elif isinstance(a, ast.JoinedStr):
                            patterns.append((fstring_regex(a), ast.unparse(a), rel, node.lineno))
                        else:
                            dynamic_calls.append((ast.unparse(a), rel, node.lineno))
                        continue
                if rel in UI_MODULES or rel.startswith(os.path.join("tools", "room_editor")):
                    hardcoded += human_literals(tree, rel)
    return literal_keys, patterns, dynamic_calls, hardcoded, all_strings, all_fstrings


def key_families():
    import game.app as app
    from game import settings as S
    from game.entities import Monster
    from game.props import (HAND_FURNITURE_BY_KIND, HAND_FURNITURE_KINDS, NOTE_POOL,
                            ZONE_HAND_FURNITURE_BY_KIND)
    from tools.room_editor import panel, room_model, zone_model
    furniture = set(HAND_FURNITURE_KINDS)
    for kinds in list(HAND_FURNITURE_BY_KIND.values()) + list(ZONE_HAND_FURNITURE_BY_KIND.values()):
        furniture.update(kinds)
    for floor, kinds in room_model.KINDS_BY_FLOOR.items():
        for kind in kinds:
            required = room_model.required_fixture_kind(kind, floor)
            if required:
                furniture.add(required)
    gfx = [f"{key}_{opt}" for key, opts in S.GFX_SETTING_OPTIONS.items() for opt in opts]
    return [
        ("{}", list(app.PICKUP_LABEL_KEYS.values()) + list(app.PORTAL_LABEL_KEYS.values())
         + [k for pool in NOTE_POOL.values() for k in pool]),
        ("debug_hud.{}", app.DEBUG_HUD_OPTIONS),
        ("debug_hud.state_{}", (Monster.PATROL, Monster.INVESTIGATE, Monster.HUNT, Monster.STALK, Monster.GUARD)),
        ("settings.tab_{}", app.SETTINGS_TABS),
        ("settings.quality_{}", S.QUALITY_PRESET_ORDER),
        ("settings.{}", gfx),
        ("settings.{}_label", list(S.GFX_SETTING_OPTIONS)),
        ("binding.{}", list(S.DEFAULT_BINDINGS)),
        ("about.link_{}", [key for key, _url in app.ICON_LINKS]),
        ("credit.{}", [role for role, _name, _url in app.CONTRIBUTORS]),
        ("editor.tool.{}", panel.TOOL_LABELS),
        ("editor.gizmo.{}", panel.GIZMO_MODES),
        ("editor.door_kind.{}", room_model.DOOR_KINDS),
        ("editor.floor.{}", list(room_model.KINDS_BY_FLOOR)),
        ("editor.kind.{}", sorted({k for ks in room_model.KINDS_BY_FLOOR.values() for k in ks}
                                  | set(zone_model.ZONE_KINDS))),
        ("editor.furniture.{}", sorted(furniture)),
        ("editor.help.{}.title", range(1, 9)),
        ("editor.help.{}.body", range(1, 9)),
    ]


def main():
    verbose = "-v" in sys.argv
    locales = load_locales()
    en = locales["en"]
    problems = 0

    print("1. key parity")
    for code in LANGUAGES[1:]:
        loc = locales[code]
        missing = sorted(set(en) - set(loc))
        extra = sorted(set(loc) - set(en))
        bad_fmt = sorted(k for k in set(en) & set(loc) if placeholders(en[k]) != placeholders(loc[k]))
        same = sorted(k for k in set(en) & set(loc) if loc[k] == en[k] and looks_like_words(en[k]))
        print(f"   {code}: missing {len(missing)}, extra {len(extra)}, placeholder mismatch {len(bad_fmt)}, "
              f"identical to English {len(same)}")
        for k in missing:
            print(f"      missing: {k}")
        for k in extra:
            print(f"      extra: {k}")
        for k in bad_fmt:
            print(f"      placeholders differ: {k}: en {placeholders(en[k])} vs {placeholders(loc[k])}")
        if verbose:
            for k in same:
                print(f"      same as English: {k} = {en[k]!r}")
        problems += len(missing) + len(extra) + len(bad_fmt)

    literal_keys, patterns, dynamic_calls, hardcoded, all_strings, all_fstrings = scan_sources()

    print("2. literal keys")
    bad = [(k, f, ln) for k, f, ln in literal_keys if k not in en]
    print(f"   {len(literal_keys)} calls, {len(bad)} unknown keys")
    for k, f, ln in bad:
        print(f"      {f}:{ln}: {k}")
    problems += len(bad)

    print("3. key patterns")
    reached = set()
    for rx, src, f, ln in patterns:
        hits = sorted(k for k in en if re.match(rx, k))
        reached.update(hits)
        flag = "" if hits else "   <-- matches no key"
        print(f"   {f}:{ln}: {src} -> {len(hits)} keys{flag}")
        if verbose:
            print("      " + ", ".join(hits))
        problems += 0 if hits else 1
    if dynamic_calls:
        print("   calls with a computed key (checked by hand):")
        for src, f, ln in dynamic_calls:
            print(f"      {f}:{ln}: i18n.t({src})")

    print("4. key families")
    for template, members in key_families():
        need = [template.format(m) for m in members]
        absent = [k for k in need if k not in en]
        print(f"   {template}: {len(need)} members, {len(absent)} without a key")
        for k in absent:
            print(f"      missing: {k}")
        problems += len(absent)

    print("5. unused keys")
    for rx in all_fstrings:
        reached.update(k for k in en if re.match(rx, k))
    used = {k for k, _, _ in literal_keys} | reached | (set(en) & all_strings)
    unused = sorted(set(en) - used)
    print(f"   {len(unused)} of {len(en)} keys not referenced by a literal, pattern or data string")
    for k in unused:
        print(f"      {k}")

    print("6. hardcoded text")
    print(f"   {len(hardcoded)} human-readable literals outside i18n")
    for text, f, ln in hardcoded:
        print(f"      {f}:{ln}: {text!r}")
    problems += len(hardcoded)

    print("PASS" if problems == 0 else f"FAIL: {problems} problems")
    sys.exit(0 if problems == 0 else 1)


if __name__ == "__main__":
    main()
