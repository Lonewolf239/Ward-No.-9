import argparse
import ast
import collections
import hashlib
import io
import os
import sys
import tokenize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".venv", ".git", "__pycache__", "qa_out", "publish", "model_qa_out", "scratchpad"}


def normalise(path):
    with open(path, "rb") as f:
        src = f.read()
    try:
        toks = list(tokenize.tokenize(io.BytesIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []
    rows = collections.defaultdict(list)
    for tok in toks:
        if tok.type in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE,
                        tokenize.ENCODING, tokenize.ENDMARKER):
            continue
        text = tok.string
        if tok.type == tokenize.STRING:
            text = '""'
        if tok.type == tokenize.INDENT or tok.type == tokenize.DEDENT:
            continue
        rows[tok.start[0]].append(text)
    return [(n, " ".join(rows[n])) for n in sorted(rows) if rows[n]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=8)
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()

    files = []
    for base, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for n in names:
            if n.endswith(".py"):
                files.append(os.path.join(base, n))
    seen = collections.defaultdict(list)
    total_lines = 0
    for path in sorted(files):
        lines = normalise(path)
        total_lines += len(lines)
        for i in range(len(lines) - args.window + 1):
            chunk = lines[i:i + args.window]
            key = hashlib.blake2b("\n".join(t for _n, t in chunk).encode(),
                                  digest_size=12).hexdigest()
            seen[key].append((os.path.relpath(path, ROOT), chunk[0][0], chunk[-1][0]))

    groups = [v for v in seen.values() if len(v) > 1]
    groups.sort(key=lambda g: -len(g))
    shown, covered = [], set()
    for g in groups:
        span = {(p, n) for p, a, b in g for n in range(a, b + 1)}
        if span & covered:
            continue
        covered |= span
        shown.append(g)
    print("файлов: %d, значимых строк: %d, окно %d строк" % (len(files), total_lines, args.window))
    print("повторяющихся участков: %d" % len(shown))
    dup_lines = sum((g[0][2] - g[0][1] + 1) * (len(g) - 1) for g in shown)
    print("строк, написанных повторно: %d (%.2f%% кода)" % (dup_lines, 100.0 * dup_lines / max(1, total_lines)))
    for g in shown[:args.top]:
        n = g[0][2] - g[0][1] + 1
        print("  %2d строк x %d мест:" % (n, len(g)))
        for path, a, b in g[:6]:
            print("      %s:%d-%d" % (path, a, b))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
