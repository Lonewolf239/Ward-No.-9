from __future__ import annotations

import argparse
import ast
import io
import os
import re
import subprocess
import sys
import tokenize

DESCRIPTION = "Take every comment out of the project and prove nothing else changed."
ROOT = os.path.dirname(os.path.abspath(__file__))

PY_EXT = (".py", ".spec")
YAML_EXT = (".yml", ".yaml")
HASH_LINE_FILES = (".gitignore", ".gitattributes")

_COOKIE = re.compile(r"coding[:=][ \t]*[-\w.]+")
_GLSL = re.compile(r"#version\b|\b(uniform|varying|gl_Position|gl_FragCoord|gl_FrontFacing"
                   r"|sampler2D\w*|vec[234]|mat[234]|void\s+main)\b")
_YAML_BLOCK = re.compile(r"(^|:|\s-)\s*[|>][-+0-9]*\s*$")


def project_files(root):
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    names = {n for n in out.decode("utf-8").split("\0") if n}
    return sorted(n for n in names if os.path.isfile(os.path.join(root, n)))


def kind_of(path):
    name = os.path.basename(path)
    if name in HASH_LINE_FILES:
        return "hash"
    if name.endswith(PY_EXT):
        return "python"
    if name.endswith(YAML_EXT):
        return "yaml"
    return None


def is_glsl(text):
    return bool(_GLSL.search(text))


def strip_glsl(text):
    out = []
    had_comment = set()
    line = 0
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/" and not (i > 0 and text[i - 1] == ":"):
            j = text.find("\n", i)
            j = n if j < 0 else j
            had_comment.add(line)
            i = j
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            if j < 0:
                return text
            body = text[i:j + 2]
            had_comment.add(line)
            breaks = body.count("\n")
            out.append("\n" * breaks if breaks else " ")
            line += breaks
            for k in range(breaks):
                had_comment.add(line - k)
            i = j + 2
            continue
        if c == "\n":
            line += 1
        out.append(c)
        i += 1
    kept = []
    for row, piece in enumerate("".join(out).split("\n")):
        if row in had_comment:
            piece = piece.rstrip(" \t")
            if not piece.strip():
                continue
        kept.append(piece)
    return "\n".join(kept)


def _byte_to_char(line, byte_col):
    return len(line.encode("utf-8")[:byte_col].decode("utf-8")) if byte_col > 0 else 0


def _lines(text):
    return io.StringIO(text).readlines()


def _split_eol(line):
    for eol in ("\r\n", "\n", "\r"):
        if line.endswith(eol):
            return line[:-len(eol)], eol
    return line, ""


def _is_bare_string(stmt):
    return (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str))


def _statement_lists(tree):
    for node in ast.walk(tree):
        for _field, value in ast.iter_fields(node):
            if isinstance(value, list) and value and all(isinstance(v, ast.stmt) for v in value):
                yield value


def _deletion(lines, offsets, start_row, start_col, end_row, end_col, keep_as=None):
    start_line = lines[start_row - 1]
    end_content, _eol = _split_eol(lines[end_row - 1])
    before = start_line[:start_col]
    after = end_content[end_col:]
    if keep_as is not None:
        return offsets[start_row - 1] + start_col, offsets[end_row - 1] + end_col, keep_as
    if not before.strip(" \t") and not after.strip(" \t"):
        return offsets[start_row - 1], offsets[end_row - 1] + len(lines[end_row - 1]), ""
    start = offsets[start_row - 1] + start_col - (len(before) - len(before.rstrip(" \t")))
    if not after.strip(" \t"):
        return start, offsets[end_row - 1] + len(end_content), ""
    rest = after.lstrip(" \t")
    if rest.startswith(";"):
        rest_after = rest[1:]
        cut = len(after) - len(rest) + 1 + (len(rest_after) - len(rest_after.lstrip(" \t")))
        return offsets[start_row - 1] + start_col, offsets[end_row - 1] + end_col + cut, ""
    return start, offsets[end_row - 1] + end_col + (len(after) - len(rest)), ""


def _python_edits(text):
    tree = ast.parse(text)
    lines = _lines(text)
    offsets, pos = [], 0
    for line in lines:
        offsets.append(pos)
        pos += len(line)
    edits = []
    counts = {"comments": 0, "strings": 0, "glsl": 0}
    dropped_rows = set()

    for body in _statement_lists(tree):
        strs = [s for s in body if _is_bare_string(s)]
        if not strs:
            continue
        keep_one = len(strs) == len(body)
        for idx, stmt in enumerate(strs):
            v = stmt.value
            sc = _byte_to_char(lines[v.lineno - 1], v.col_offset)
            ec = _byte_to_char(lines[v.end_lineno - 1], v.end_col_offset)
            keep = "pass" if keep_one and idx == len(strs) - 1 else None
            edits.append(_deletion(lines, offsets, v.lineno, sc, v.end_lineno, ec, keep))
            dropped_rows.update(range(v.lineno, v.end_lineno + 1))
            counts["strings"] += 1

    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            row, col = tok.start
            if row == 1 and col == 0 and tok.string.startswith("#!"):
                continue
            if row in (1, 2) and _COOKIE.search(tok.string):
                continue
            edits.append(_deletion(lines, offsets, row, col, tok.end[0], tok.end[1]))
            counts["comments"] += 1
        elif tok.type == tokenize.STRING and tok.start[0] not in dropped_rows:
            s = tok.string
            q = 0
            while s[q] not in "'\"":
                q += 1
            prefix, quote = s[:q].lower(), s[q:q + 3]
            if quote not in ('"""', "'''") or "\\" in s or set(prefix) - {"u"}:
                continue
            body = s[q + 3:-3]
            if not is_glsl(body):
                continue
            new_body = strip_glsl(body)
            if new_body == body:
                continue
            start = offsets[tok.start[0] - 1] + tok.start[1] + q + 3
            edits.append((start, start + len(body), new_body))
            counts["glsl"] += 1
    return edits, counts


def _apply(text, edits):
    merged = []
    for start, end, repl in sorted(edits):
        if merged and start < merged[-1][1]:
            p_start, p_end, p_repl = merged[-1]
            if repl or p_repl:
                raise ValueError("overlapping cuts at offset %d" % start)
            merged[-1] = (p_start, max(p_end, end), "")
            continue
        merged.append((start, end, repl))
    for start, end, repl in reversed(merged):
        text = text[:start] + repl + text[end:]
    return text


def _tidy_blank_lines(text):
    lines = _lines(text)
    protected = set()
    openers = set()
    open_rows = []
    prev = None
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        name = tokenize.tok_name.get(tok.type, "")
        if tok.type == tokenize.STRING and tok.end[0] > tok.start[0]:
            protected.update(range(tok.start[0] + 1, tok.end[0] + 1))
        elif name.endswith("STRING_START"):
            open_rows.append(tok.start[0])
        elif name.endswith("STRING_END") and open_rows:
            protected.update(range(open_rows.pop() + 1, tok.end[0] + 1))
        if tok.type == tokenize.NEWLINE and prev is not None and prev.type == tokenize.OP and prev.string == ":":
            openers.add(tok.start[0])
        if tok.type not in (tokenize.NL, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT):
            prev = tok
    out = []
    seen_code = False
    last_code_row = None
    run = []
    for row, line in enumerate(lines, 1):
        blank = not line.strip() and row not in protected
        if blank:
            run.append(line)
            continue
        if run and seen_code and last_code_row not in openers:
            out.extend(run[:2])
        run = []
        out.append(line)
        seen_code = True
        last_code_row = row
    return "".join(out)


class _Normalized(ast.NodeTransformer):
    def generic_visit(self, node):
        super().generic_visit(node)
        for field, value in ast.iter_fields(node):
            if isinstance(value, list) and value and all(isinstance(v, ast.stmt) for v in value):
                kept = [s for s in value if not _is_bare_string(s)]
                setattr(node, field, kept or [ast.Pass()])
        return node

    def visit_Constant(self, node):
        if isinstance(node.value, str) and is_glsl(node.value):
            node.value = strip_glsl(node.value)
        return node


def _comments_left(text):
    left = 0
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type != tokenize.COMMENT:
            continue
        row, col = tok.start
        if row == 1 and col == 0 and tok.string.startswith("#!"):
            continue
        if row in (1, 2) and _COOKIE.search(tok.string):
            continue
        left += 1
    return left


def strip_python(text):
    edits, counts = _python_edits(text)
    return _tidy_blank_lines(_apply(text, edits)), counts


def check_python(original, new):
    try:
        new_tree = ast.parse(new)
    except SyntaxError as exc:
        return "does not parse: %s" % exc
    want = ast.dump(_Normalized().visit(ast.parse(original)))
    got = ast.dump(_Normalized().visit(new_tree))
    if want != got:
        return "the code itself would change"
    left = _comments_left(new)
    if left:
        return "%d comment(s) would be left" % left
    again, _ = strip_python(new)
    if again != new:
        return "a second run would change it again"
    return None


def _yaml_code(line):
    quote = None
    i = 0
    while i < len(line):
        c = line[i]
        if quote == "'":
            if c == "'":
                if i + 1 < len(line) and line[i + 1] == "'":
                    i += 1
                else:
                    quote = None
        elif quote == '"':
            if c == "\\":
                i += 1
            elif c == '"':
                quote = None
        elif c in "'\"" and (i == 0 or line[i - 1] in " \t[{,:-"):
            quote = c
        elif c == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip(" \t")
        i += 1
    return line


def strip_yaml(text):
    out = []
    removed = 0
    block_indent = None
    for line in _lines(text):
        content, eol = _split_eol(line)
        indent = len(content) - len(content.lstrip(" "))
        if block_indent is not None:
            if not content.strip() or indent > block_indent:
                out.append(line)
                continue
            block_indent = None
        if content.lstrip().startswith("#"):
            removed += 1
            continue
        code = _yaml_code(content)
        if code != content:
            removed += 1
            line = code + eol
        if _YAML_BLOCK.search(code):
            block_indent = indent
        out.append(line)
    return "".join(out), {"comments": removed}


def check_yaml(original, new):
    try:
        import yaml
    except ImportError:
        return "PyYAML is not installed: cannot prove the data is unchanged"
    try:
        if yaml.safe_load(original) != yaml.safe_load(new):
            return "the data itself would change"
    except yaml.YAMLError as exc:
        return "does not load: %s" % exc
    if strip_yaml(new)[0] != new:
        return "a second run would change it again"
    return None


def strip_hash_lines(text):
    lines = _lines(text)
    kept = [l for l in lines if not l.lstrip().startswith("#")]
    return "".join(kept), {"comments": len(lines) - len(kept)}


def check_hash_lines(_original, new):
    return None if strip_hash_lines(new)[0] == new else "a second run would change it again"


HANDLERS = {"python": (strip_python, check_python),
            "yaml": (strip_yaml, check_yaml),
            "hash": (strip_hash_lines, check_hash_lines)}


def process(path, kind):
    with open(path, "r", encoding="utf-8", newline="") as f:
        original = f.read()
    strip, check = HANDLERS[kind]
    try:
        new, counts = strip(original)
    except (SyntaxError, ValueError, tokenize.TokenError) as exc:
        return None, {}, "could not be read as %s: %s" % (kind, exc)
    if new == original:
        return None, counts, None
    problem = check(original, new)
    if problem:
        return None, counts, problem
    return new, counts, None


def main(argv=None):
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--apply", "--write", dest="apply", action="store_true",
                        help="rewrite the files (without it: a dry run)")
    parser.add_argument("--quiet", action="store_true", help="print only the summary")
    args = parser.parse_args(argv)

    names = project_files(ROOT)
    if names is None:
        print("git is not available here: it is what says which files are the project's")
        return 2
    changed = errors = scanned = 0
    totals = {}
    for rel in names:
        kind = kind_of(rel)
        if kind is None:
            continue
        scanned += 1
        path = os.path.join(ROOT, rel)
        new, counts, error = process(path, kind)
        if error:
            errors += 1
            print("ERROR       %s: %s - left as it is" % (rel, error))
            continue
        if new is None:
            continue
        changed += 1
        for k, v in counts.items():
            totals[k] = totals.get(k, 0) + v
        if not args.quiet:
            what = ", ".join("%s %d" % (k, v) for k, v in counts.items() if v)
            print("%-11s %s: %s" % ("rewritten" if args.apply else "would strip", rel, what))
        if args.apply:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(new)
    what = ", ".join("%s %d" % (k, v) for k, v in sorted(totals.items())) or "nothing"
    print("\n%s: %d file(s) scanned, %d %s (%s), %d refused"
          % ("APPLIED" if args.apply else "DRY RUN", scanned, changed,
             "rewritten" if args.apply else "would change", what, errors))
    if not args.apply and changed:
        print("run again with --apply to write them")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
