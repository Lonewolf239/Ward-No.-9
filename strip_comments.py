"""strip_comments.py - one-off manual cleanup tool for this repo.

NOT wired into the game. A solo developer runs this by hand, on demand, to
strip every `#` comment and every real docstring (the first bare
string-literal statement of a module/class/function body) out of the
project's .py files.

Design notes / decisions (read before running with --apply):

* Correctness strategy: `tokenize` finds COMMENT tokens (so we never touch a
  `#` that lives inside a string literal - tokenize already tells STRING and
  COMMENT tokens apart), and `ast` finds true docstring nodes by structural
  position (first statement of a Module/ClassDef/FunctionDef/AsyncFunctionDef
  body, and only when that statement is a bare string-literal Expr). Nothing
  here is a line-based regex.

* Byte vs. character columns: `ast` column offsets are UTF-8 BYTE offsets
  into the physical source line, while `tokenize` column offsets are
  CHARACTER offsets. This repo has Cyrillic text in places, and getting this
  wrong silently corrupts any line where non-ASCII text precedes the token
  being stripped. Every ast-derived column is converted byte->char (see
  `byte_col_to_char_col`) before it is used to slice a Python string.

* Empty-body guard: if a class/function's docstring is its ONLY statement,
  deleting it would leave a body with nothing in it (`SyntaxError: expected
  an indented block`). In that specific case the docstring is replaced with
  `pass` instead of being deleted outright. Module docstrings never need
  this (an empty module is valid Python).

* Shebang (`#!` on line 1) and a PEP 263 encoding cookie (`# -*- coding: ... -*-`
  on line 1 or 2) are left alone even though they are lexically comments.
  This project's files are plain UTF-8 with no encoding cookies, so that
  half of the rule is mostly moot in practice, but it's cheap correctness
  to keep and costs nothing.

* Self-exclusion: a default run SKIPS this file itself (matched by absolute
  path, not just filename) so that "clean the repo" never has the side
  effect of mangling the tool doing the cleaning. Pass --include-self to
  opt this file into the walk explicitly.

* Safety net: every stripped file is re-parsed with ast.parse() before
  anything is written. If the stripped output does not parse, that file is
  left untouched on disk and reported as an error - never written half-broken.

* Blank lines: a line that becomes comment/docstring-only is deleted
  entirely (not left blank). A line with real code plus a trailing comment
  keeps the code and loses only the comment and the whitespace it leaves
  behind. Pre-existing blank lines elsewhere are left as they were; no
  global "collapse blank lines" pass is run (that would risk touching
  whitespace inside untouched multi-line string literals elsewhere in the
  file).

CLI:
    python strip_comments.py                 # dry run: report only
    python strip_comments.py --apply          # actually rewrite files
    python strip_comments.py --write          # alias for --apply
    python strip_comments.py --include-self   # also let the walk see this file
    python strip_comments.py --quiet          # summary line only

Dry run is the default on purpose: this tool permanently deletes comments
and docstrings across an entire codebase, and that should never happen by
accident.
"""

from __future__ import annotations

import argparse
import ast
import io
import os
import re
import sys
import tokenize
from typing import List, NamedTuple, Optional, Sequence, Tuple

EXCLUDED_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
}

_SELF_ABSPATH = os.path.abspath(__file__)

_DOCSTRING_PARENT_TYPES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
_BLOCK_TYPES_NEEDING_NONEMPTY_BODY = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)

_CODING_COOKIE_RE = re.compile(r"coding[:=][ \t]*([-\w.]+)")


def iter_python_files(root: str, include_self: bool) -> List[str]:
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            full = os.path.join(dirpath, name)
            if not include_self and os.path.abspath(full) == _SELF_ABSPATH:
                continue
            found.append(full)
    found.sort()
    return found


def line_boundaries(text: str) -> Tuple[List[str], List[int]]:
    lines = text.splitlines(keepends=True)
    offsets = [0] * len(lines)
    pos = 0
    for i, line in enumerate(lines):
        offsets[i] = pos
        pos += len(line)
    return lines, offsets


def byte_col_to_char_col(line: str, byte_col: int) -> int:
    if byte_col <= 0:
        return 0
    encoded = line.encode("utf-8")
    return len(encoded[:byte_col].decode("utf-8"))


def _split_line(line: str) -> Tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    if line.endswith("\r"):
        return line[:-1], "\r"
    return line, ""


class DocSpan(NamedTuple):
    start_row: int
    start_col: int
    end_row: int
    end_col: int
    needs_pass: bool


class CommentSpan(NamedTuple):
    start_row: int
    start_col: int
    end_row: int
    end_col: int


def find_docstring_spans(tree: ast.AST, lines: Sequence[str]) -> List[DocSpan]:
    spans: List[DocSpan] = []
    for node in ast.walk(tree):
        if not isinstance(node, _DOCSTRING_PARENT_TYPES):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if not isinstance(first, ast.Expr):
            continue
        value = first.value
        if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
            continue
        needs_pass = isinstance(node, _BLOCK_TYPES_NEEDING_NONEMPTY_BODY) and len(body) == 1
        start_line = lines[value.lineno - 1]
        end_line = lines[value.end_lineno - 1]
        start_col = byte_col_to_char_col(start_line, value.col_offset)
        end_col = byte_col_to_char_col(end_line, value.end_col_offset)
        spans.append(DocSpan(value.lineno, start_col, value.end_lineno, end_col, needs_pass))
    return spans


def _is_coding_cookie(comment_text: str) -> bool:
    return bool(_CODING_COOKIE_RE.search(comment_text))


def find_comment_spans(text: str) -> List[CommentSpan]:
    spans: List[CommentSpan] = []
    tokens = tokenize.generate_tokens(io.StringIO(text).readline)
    for tok in tokens:
        if tok.type != tokenize.COMMENT:
            continue
        row, col = tok.start
        if row == 1 and col == 0 and tok.string.startswith("#!"):
            continue
        if row in (1, 2) and _is_coding_cookie(tok.string):
            continue
        spans.append(CommentSpan(row, col, tok.end[0], tok.end[1]))
    return spans


def build_deletion(
    lines: Sequence[str],
    offsets: Sequence[int],
    start_row: int,
    start_col: int,
    end_row: int,
    end_col: int,
    keep_as: Optional[str],
) -> Tuple[int, int, str]:
    start_line = lines[start_row - 1]
    end_line = lines[end_row - 1]
    end_content, _end_term = _split_line(end_line)

    before = start_line[:start_col]
    after = end_content[end_col:]

    before_all_ws = before.strip(" \t") == ""
    after_all_ws = after.strip(" \t") == ""

    if keep_as is not None:
        new_start = offsets[start_row - 1] + start_col
        trim = (len(after) - len(after.lstrip(" \t"))) if after_all_ws else 0
        new_end = offsets[end_row - 1] + end_col + trim
        return new_start, new_end, keep_as

    if before_all_ws and after_all_ws:
        new_start = offsets[start_row - 1]
        new_end = offsets[end_row - 1] + len(end_line)
        return new_start, new_end, ""

    trailing_ws_before = len(before) - len(before.rstrip(" \t"))
    new_start = offsets[start_row - 1] + start_col - trailing_ws_before
    if after_all_ws:
        new_end = offsets[end_row - 1] + len(end_content)
    else:
        trim = len(after) - len(after.lstrip(" \t"))
        new_end = offsets[end_row - 1] + end_col + trim
    return new_start, new_end, ""


class FileResult(NamedTuple):
    path: str
    status: str
    new_text: Optional[str]
    n_comments: int
    n_docstrings: int
    message: str


def process_file(path: str) -> FileResult:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            original = f.read()
    except (OSError, UnicodeDecodeError) as exc:
        return FileResult(path, "error", None, 0, 0, f"could not read file: {exc}")

    try:
        tree = ast.parse(original, filename=path)
    except SyntaxError as exc:
        return FileResult(path, "error", None, 0, 0, f"pre-existing syntax error, skipped: {exc}")

    lines, offsets = line_boundaries(original)
    doc_spans = find_docstring_spans(tree, lines)

    try:
        comment_spans = find_comment_spans(original)
    except Exception as exc:
        return FileResult(path, "error", None, 0, 0, f"tokenize failed, skipped: {exc}")

    deletions: List[Tuple[int, int, str, str]] = []
    for ds in doc_spans:
        keep_as = "pass" if ds.needs_pass else None
        start, end, repl = build_deletion(
            lines, offsets, ds.start_row, ds.start_col, ds.end_row, ds.end_col, keep_as
        )
        deletions.append((start, end, repl, "docstring"))

    for cs in comment_spans:
        start, end, repl = build_deletion(
            lines, offsets, cs.start_row, cs.start_col, cs.end_row, cs.end_col, None
        )
        deletions.append((start, end, repl, "comment"))

    if not deletions:
        return FileResult(path, "unchanged", original, 0, 0, "")

    deletions.sort(key=lambda d: d[0])
    for i in range(1, len(deletions)):
        if deletions[i][0] < deletions[i - 1][1]:
            return FileResult(
                path, "error", None, 0, 0,
                "internal error: overlapping deletions detected, skipped for safety",
            )

    new_text = original
    for start, end, replacement, _kind in sorted(deletions, key=lambda d: d[0], reverse=True):
        new_text = new_text[:start] + replacement + new_text[end:]

    try:
        ast.parse(new_text, filename=path)
    except SyntaxError as exc:
        return FileResult(
            path, "error", None, 0, 0,
            f"stripped output failed to parse (stripper bug, NOT written): {exc}",
        )

    n_comments = sum(1 for d in deletions if d[3] == "comment")
    n_docstrings = sum(1 for d in deletions if d[3] == "docstring")

    if new_text == original:
        return FileResult(path, "unchanged", original, 0, 0, "")

    return FileResult(path, "changed", new_text, n_comments, n_docstrings, "")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply", "--write", dest="apply", action="store_true",
        help="Actually rewrite files in place. Without this flag: dry run only.",
    )
    parser.add_argument(
        "--include-self", action="store_true",
        help="Also let strip_comments.py process itself (excluded by default).",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Print only the final summary line, not one line per file.",
    )
    args = parser.parse_args(argv)

    root = os.path.dirname(os.path.abspath(__file__))
    files = iter_python_files(root, args.include_self)

    changed_files = 0
    error_files = 0
    total_comments = 0
    total_docstrings = 0

    for path in files:
        rel = os.path.relpath(path, root)
        result = process_file(path)

        if result.status == "error":
            error_files += 1
            print(f"ERROR   {rel}: {result.message}")
            continue

        if result.status == "unchanged":
            continue

        changed_files += 1
        total_comments += result.n_comments
        total_docstrings += result.n_docstrings

        if not args.quiet:
            verb = "MODIFIED" if args.apply else "would modify"
            print(f"{verb:10} {rel}: {result.n_comments} comment(s), {result.n_docstrings} docstring(s)")

        if args.apply:
            with open(path, "w", encoding="utf-8") as f:
                f.write(result.new_text)

    mode = "APPLY (files rewritten)" if args.apply else "DRY RUN (nothing written)"
    print(
        f"\n{mode}: scanned {len(files)} .py file(s) under {root}\n"
        f"  {changed_files} file(s) {'modified' if args.apply else 'would be modified'} "
        f"- {total_comments} comment(s), {total_docstrings} docstring(s) removed\n"
        f"  {error_files} file(s) skipped due to errors"
    )
    if not args.apply and changed_files:
        print("\nRe-run with --apply (or --write) to actually rewrite these files.")

    return 1 if error_files else 0


if __name__ == "__main__":
    sys.exit(main())
