import sys, os, traceback, collections
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import moderngl
import pygame

records = []
_orig_va = moderngl.Context.vertex_array


def _spy(self, program, content=None, *args, **kwargs):
    vao = _orig_va(self, program, content, *args, **kwargs)
    try:
        for entry in content or []:
            if len(entry) >= 2 and entry[1] == "3f 3f 2f 3f":
                site = next((f for f in reversed(traceback.extract_stack()[:-1])
                             if f.filename.endswith("renderer3d.py")), None)
                label = f"renderer3d.py:{site.lineno} {site.line.strip()[:48]}" if site else "?"
                records.append((label, entry[0]))
    except Exception:
        pass
    return vao


moderngl.Context.vertex_array = _spy

from game.app import App
from tools.sandbox_settings import redirect as _sandbox_settings
_sandbox_settings()


def audit(buf):
    raw = np.frombuffer(buf.read(), dtype=np.float32).astype(np.float64)
    a = raw[: (len(raw) // 11) * 11].reshape(-1, 11)
    n_tri = len(a) // 3
    t = a[: n_tri * 3].reshape(n_tri, 3, 11)
    p0, p1, p2 = t[:, 0, :3], t[:, 1, :3], t[:, 2, :3]
    c = np.cross(p1 - p0, p2 - p0)
    area = np.linalg.norm(c, axis=1)
    n = t[:, :, 3:6].mean(axis=1)
    live = area > 1e-9
    bad = live & (np.einsum('ij,ij->i', c, n) < 0)
    normals = collections.Counter(tuple(np.round(v / max(1e-9, np.linalg.norm(v)), 2)) for v in n[bad])
    return int(live.sum()), int(bad.sum()), normals


app = App()
pygame.display.flip = lambda: None
seen = {}


def sweep(tag):
    for label, buf in records:
        key = id(buf)
        if key in seen:
            continue
        try:
            live, bad, normals = audit(buf)
        except Exception:
            continue
        seen[key] = (tag, label, live, bad, normals)


sweep("init")
app.new_game(); app._start_playing()
for f in (0, 1, 2):
    app._load_floor(f)
    sweep(f"floor{f}")
app._start_debug_level()
sweep("debug")

total = 0
by_label = collections.defaultdict(lambda: [0, 0, collections.Counter(), set()])
for tag, label, live, bad, normals in seen.values():
    e = by_label[label]
    e[0] += live; e[1] += bad; e[2].update(normals); e[3].add(tag)
for label, (live, bad, normals, tags) in sorted(by_label.items()):
    if bad:
        total += bad
        print(f"INVERTED {bad:6d}/{live:7d}  {label}  [{','.join(sorted(tags))}]  normals {dict(normals.most_common(6))}")
print("buffers audited:", len(seen), "sites:", len(by_label), "clean sites:",
      sum(1 for v in by_label.values() if not v[1]), "TOTAL inverted:", total)
sys.exit(1 if total else 0)
