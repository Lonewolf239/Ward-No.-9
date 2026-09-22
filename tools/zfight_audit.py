import collections
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SDL_VIDEODRIVER", "x11")
import numpy as np

SEPARATION_MM = 1.0
MIN_OVERLAP_MM2 = 4.0
RAY_REACH = 2.5
COLOR_EPS = 0.02
VERBOSE = "-v" in sys.argv
EYE_BAND = (0.35, 0.70)
EYE_MIN_DIST = 0.8
SIDE_OFFSET = 1e-4

SURFACE_TOP = 0.38

FIB_DIRS = None


def _hemisphere_dirs(n=48):
    global FIB_DIRS
    if FIB_DIRS is None:
        i = np.arange(n) + 0.5
        phi = np.arccos(1.0 - i / n)
        theta = math.pi * (1.0 + 5 ** 0.5) * i
        FIB_DIRS = np.stack([np.cos(theta) * np.sin(phi), np.sin(theta) * np.sin(phi), np.cos(phi)], axis=1)
    return FIB_DIRS


class Soup:
    def __init__(self):
        self.tris, self.cols, self.uvs, self.obj, self.textured, self.labels = [], [], [], [], [], []

    def add(self, data, label, textured, matrix=None, scale=None):
        a = np.asarray(data, dtype=np.float64).reshape(-1, 11)
        n = (len(a) // 3) * 3
        a = a[:n]
        pos = a[:, 0:3].copy()
        if scale is not None:
            pos *= np.asarray(scale, dtype=np.float64)
        if matrix is not None:
            pos = pos @ matrix[:3, :3].T + matrix[:3, 3]
        obj = len(self.labels)
        self.labels.append(label)
        self.tris.append(pos.reshape(-1, 3, 3))
        self.cols.append(a[:, 8:11].reshape(-1, 3, 3).mean(axis=1))
        self.uvs.append(a[:, 6:8].reshape(-1, 3, 2))
        self.obj.append(np.full(n // 3, obj))
        self.textured.append(np.full(n // 3, bool(textured)))

    def arrays(self):
        return (np.concatenate(self.tris), np.concatenate(self.cols), np.concatenate(self.uvs),
                np.concatenate(self.obj), np.concatenate(self.textured))


def _clip_area(a2, b2):
    def ccw(t):
        return t if (t[1][0] - t[0][0]) * (t[2][1] - t[0][1]) - (t[1][1] - t[0][1]) * (t[2][0] - t[0][0]) >= 0 else t[::-1]
    poly = [tuple(p) for p in ccw(a2)]
    clip = [tuple(p) for p in ccw(b2)]
    for i in range(3):
        cx0, cy0 = clip[i]
        cx1, cy1 = clip[(i + 1) % 3]
        ex, ey = cx1 - cx0, cy1 - cy0
        inside = lambda p: ex * (p[1] - cy0) - ey * (p[0] - cx0) >= -1e-12
        out = []
        for j in range(len(poly)):
            p, q = poly[j], poly[(j + 1) % len(poly)]
            pin, qin = inside(p), inside(q)
            if pin:
                out.append(p)
            if pin != qin:
                dx, dy = q[0] - p[0], q[1] - p[1]
                den = ex * dy - ey * dx
                if abs(den) > 1e-18:
                    t = (ey * (p[0] - cx0) - ex * (p[1] - cy0)) / den
                    out.append((p[0] + dx * t, p[1] + dy * t))
        poly = out
        if len(poly) < 3:
            return 0.0, None
    area = 0.0
    cx = cy = 0.0
    for j in range(len(poly)):
        x0, y0 = poly[j]
        x1, y1 = poly[(j + 1) % len(poly)]
        cr = x0 * y1 - x1 * y0
        area += cr
        cx += (x0 + x1) * cr
        cy += (y0 + y1) * cr
    if abs(area) < 1e-14:
        return 0.0, None
    return abs(area) / 2.0, (cx / (3.0 * area), cy / (3.0 * area))


def _rays_blocked(origin, dirs, tris, reach):
    reach = np.broadcast_to(np.asarray(reach, dtype=np.float64), (len(dirs),))
    v0, v1, v2 = tris[:, 0], tris[:, 1], tris[:, 2]
    e1, e2 = v1 - v0, v2 - v0
    blocked = np.zeros(len(dirs), dtype=bool)
    s = origin - v0
    for k, d in enumerate(dirs):
        p = np.cross(d, e2)
        det = np.einsum("ij,ij->i", e1, p)
        ok = np.abs(det) > 1e-14
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        u = np.einsum("ij,ij->i", s, p) * inv
        q = np.cross(s, e1)
        v = (q @ d) * inv
        t = np.einsum("ij,ij->i", e2, q) * inv
        hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-6) & (t < reach[k])
        blocked[k] = hit.any()
    return blocked


def _eye_distances(origin_z, dirs, reach, band, min_clear=EYE_MIN_DIST):
    lo_z, hi_z = band
    dz = dirs[:, 2]
    t = np.full(len(dirs), np.inf)
    inside = lo_z <= origin_z <= hi_z
    if inside:
        t[:] = 0.0
    elif origin_z > hi_z:
        down = dz < -1e-9
        t[down] = (hi_z - origin_z) / dz[down]
    else:
        up = dz > 1e-9
        t[up] = (lo_z - origin_z) / dz[up]
    t[t > reach] = np.inf
    t = np.where(np.isfinite(t), np.minimum(np.maximum(t, min_clear), reach), t)
    return t


def find_fights(soup, reach=RAY_REACH, local_radius=None, occluders=None, eye_band=EYE_BAND, z_offset=0.0,
                min_clear=EYE_MIN_DIST):
    tris, cols, uvs, obj, textured = soup.arrays()
    kinds = [label.split("@")[0] for label in soup.labels]
    static_obj = np.array([("@" not in label) for label in soup.labels]) if local_radius is not None \
        else np.zeros(len(soup.labels), dtype=bool)
    e1 = tris[:, 1] - tris[:, 0]
    e2 = tris[:, 2] - tris[:, 0]
    cr = np.cross(e1, e2)
    area2 = np.linalg.norm(cr, axis=1)
    live = area2 > 2e-8
    n = np.zeros_like(cr)
    n[live] = cr[live] / area2[live, None]
    big = np.argmax(np.abs(n), axis=1)
    sign = np.sign(n[np.arange(len(n)), big])
    sign[sign == 0] = 1.0
    cn = n * sign[:, None]
    d = np.einsum("ij,ij->i", cn, tris[:, 0])
    buckets = collections.defaultdict(list)
    for offset in (0.0, 0.5):
        nk = np.floor(cn * 300.0 + offset).astype(np.int64)
        dk = np.floor(d / (2.0 * SEPARATION_MM / 1000.0) + offset).astype(np.int64)
        for i in np.nonzero(live)[0]:
            key = (offset, nk[i, 0], nk[i, 1], nk[i, 2], dk[i])
            buckets[key].append(i)
    lo = tris.min(axis=1)
    hi = tris.max(axis=1)
    seen = set()
    fights = []
    for key, idx in buckets.items():
        if len(idx) < 2:
            continue
        idx = np.array(idx)
        cell = 0.5
        grid = collections.defaultdict(list)
        for i in idx:
            for gx in range(int(math.floor(lo[i, 0] / cell)), int(math.floor(hi[i, 0] / cell)) + 1):
                for gy in range(int(math.floor(lo[i, 1] / cell)), int(math.floor(hi[i, 1] / cell)) + 1):
                    for gz in range(int(math.floor(lo[i, 2] / cell)), int(math.floor(hi[i, 2] / cell)) + 1):
                        grid[(gx, gy, gz)].append(i)
        for members in grid.values():
            for a_i in range(len(members)):
                i = members[a_i]
                for j in members[a_i + 1:]:
                    pair = (i, j) if i < j else (j, i)
                    if pair in seen:
                        continue
                    seen.add(pair)
                    if abs(np.dot(cn[i], cn[j])) < 0.99995:
                        continue
                    sep = abs(np.dot(cn[i], tris[j, 0]) - d[i])
                    if sep * 1000.0 > SEPARATION_MM:
                        continue
                    same_look = (kinds[obj[i]] == kinds[obj[j]] and not textured[i] and not textured[j]
                                 and np.abs(cols[i] - cols[j]).max() < COLOR_EPS)
                    if same_look:
                        continue
                    tol = SEPARATION_MM / 1000.0 + 1e-6
                    if np.any(hi[i] < lo[j] - tol) or np.any(hi[j] < lo[i] - tol):
                        continue
                    u_axis = np.cross(cn[i], [0.0, 0.0, 1.0] if abs(cn[i][2]) < 0.9 else [1.0, 0.0, 0.0])
                    u_axis /= np.linalg.norm(u_axis)
                    v_axis = np.cross(cn[i], u_axis)
                    a2 = [(float(np.dot(p, u_axis)), float(np.dot(p, v_axis))) for p in tris[i]]
                    b2 = [(float(np.dot(p, u_axis)), float(np.dot(p, v_axis))) for p in tris[j]]
                    area, c2 = _clip_area(a2, b2)
                    if area * 1e6 < MIN_OVERLAP_MM2:
                        continue
                    if obj[i] == obj[j] and textured[i] and np.abs(cols[i] - cols[j]).max() < COLOR_EPS:
                        if np.allclose(np.sort(uvs[i].ravel()), np.sort(uvs[j].ravel()), atol=1e-4):
                            continue
                    centre = cn[i] * d[i] + u_axis * c2[0] + v_axis * c2[1]
                    dj = float(np.dot(cn[i], tris[j, 0]) - d[i])
                    fights.append(dict(i=int(i), j=int(j), centre=centre, normal=cn[i].copy(), area_mm2=area * 1e6,
                                       sep_mm=sep * 1000.0, facing="same" if np.dot(n[i], n[j]) > 0 else "back-to-back",
                                       span=(min(0.0, dj), max(0.0, dj))))
    out = []
    dirs_up = _hemisphere_dirs()
    for f in fights:
        nrm = f["normal"]
        z = np.array([0.0, 0.0, 1.0])
        axis = np.cross(z, nrm)
        s_ = np.linalg.norm(axis)
        c_ = float(np.dot(z, nrm))
        if s_ < 1e-9:
            rot = np.eye(3) if c_ > 0 else np.diag([1.0, -1.0, -1.0])
        else:
            k = axis / s_
            K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
            rot = np.eye(3) + K * s_ + K @ K * (1 - c_)
        if local_radius is not None:
            near = np.all((hi > f["centre"] - local_radius) & (lo < f["centre"] + local_radius), axis=1)
            local = tris[near]
        else:
            near = np.ones(len(tris), dtype=bool)
            local = tris
        if occluders is not None:
            local = np.concatenate([local, occluders])
        open_sides = 0
        f["open"] = []
        for side in (1.0, -1.0):
            edge = f["span"][1] if side > 0 else f["span"][0]
            origin = f["centre"] + nrm * (edge + side * SIDE_OFFSET)
            dirs = (dirs_up @ rot.T) * side
            own_mask = (obj == obj[f["i"]]) | (obj == obj[f["j"]]) | static_obj[obj]
            if local_radius is not None:
                own_mask &= near
            own = tris[own_mask]
            if occluders is not None:
                own = np.concatenate([own, occluders])
            if _rays_blocked(origin, dirs, own, 50.0).all():
                continue
            if eye_band is None:
                t_eye = np.full(len(dirs), reach)
            else:
                t_eye = _eye_distances(origin[2] + z_offset, dirs, reach, eye_band, min_clear)
            live_dirs = np.isfinite(t_eye)
            if not live_dirs.any():
                continue
            if not _rays_blocked(origin, dirs[live_dirs], local, t_eye[live_dirs]).all():
                open_sides += 1
                f["open"].append("+" if side > 0 else "-")
        if open_sides:
            f["open_sides"] = open_sides
            out.append(f)
    return out, (tris, cols, obj)


def _report(title, soup, fights, arrays, limit=12):
    tris, cols, obj = arrays
    by_label = collections.defaultdict(list)
    for f in fights:
        la, lb = soup.labels[obj[f["i"]]], soup.labels[obj[f["j"]]]
        key = la if la == lb else " / ".join(sorted((la.split("@")[0], lb.split("@")[0])))
        by_label[key.split("@")[0]].append((f, la, lb))
    print(f"== {title}: {len(fights)} visible fighting pairs in {len(by_label)} places")
    for key, items in sorted(by_label.items(), key=lambda kv: -len(kv[1])):
        f, la, lb = max(items, key=lambda it: it[0]["area_mm2"])
        c = f["centre"]
        print(f"  {key:44s} pairs {len(items):4d}  biggest {f['area_mm2']:8.0f} mm2 sep {f['sep_mm']:.2f} mm  "
              f"at ({c[0]:.3f}, {c[1]:.3f}, {c[2]:.3f}) n ({f['normal'][0]:.2f}, {f['normal'][1]:.2f}, {f['normal'][2]:.2f})"
              f" {f['facing']} open {''.join(f['open'])}"
              f"  colours {tuple(float(c) for c in np.round(cols[f['i']], 2))} vs {tuple(float(c) for c in np.round(cols[f['j']], 2))}  [{la} | {lb}]")
        if VERBOSE:
            shown = set()
            for f2, _, _ in sorted(items, key=lambda it: -it[0]["area_mm2"]):
                key2 = (tuple(np.round(cols[f2["i"]], 2)), tuple(np.round(cols[f2["j"]], 2)), f2["facing"])
                if key2 in shown:
                    continue
                shown.add(key2)
                c2 = f2["centre"]
                print(f"      {f2['area_mm2']:8.0f} mm2 at ({c2[0]:.3f}, {c2[1]:.3f}, {c2[2]:.3f}) "
                      f"n ({f2['normal'][0]:.2f}, {f2['normal'][1]:.2f}, {f2['normal'][2]:.2f}) {f2['facing']} open {''.join(f2['open'])} "
                      f"{tuple(float(c) for c in key2[0])} vs {tuple(float(c) for c in key2[1])}")
    return len(fights)


def mesh_pass():
    from game import renderer3d as R
    from game import settings as S
    from game.props import PROP_DEFS, Door
    SURFACE_ITEM_KINDS = {"monitor", "lamp_desk", "instrument_tray", "tray_stack"}
    import re
    import tempfile
    import light_probe
    r = light_probe.Probe(out_dir=tempfile.mkdtemp()).renderer
    total = 0
    items = dict(r._mesh_builders)
    for kind, builders in R.VARIANT_MESH_BUILDERS.items():
        for vi, b in enumerate(builders):
            items[f"{kind}_v{vi}"] = b
    items.update(locker=R.build_locker_mesh, locker_door=R.build_locker_door_mesh, door=R.build_door_mesh,
                 door_broken=R.build_broken_door_mesh)
    held = {"flashlight_handheld", "lighter_handheld", "lighter_flame", "map_handheld"}
    for name, builder in sorted(items.items()):
        base = "locker" if name == "locker_door" else name
        for suffix in ("_broken", "_open_neg", "_open_pos", "_open_both", "_open"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        mv = re.match(r"(.+)_v\d+$", base)
        if mv and mv.group(1) in PROP_DEFS:
            base = mv.group(1)
        if name in held:
            scale, textured = (1.0, 1.0, 1.0), False
        elif base == "door":
            dd = Door(0.5, 0.5, 0.0)
            scale, textured = (dd.hd * 2, dd.hw * 2, dd.height), True
        elif base in PROP_DEFS:
            spec = PROP_DEFS[base]
            scale, textured = (spec["hd"] * 2, spec["hw"] * 2, spec["height"]), bool(spec.get("texture"))
        else:
            print("  (skipped, no size)", name)
            continue
        soup = Soup()
        soup.add(builder(), name, textured, scale=scale)
        occ, band, z0 = [], EYE_BAND, 0.0
        big = 3.0
        if name in held:
            band = None
        elif base == "hatch":
            z0 = S.WALL_HEIGHT - scale[2]
            h = scale[2]
            occ.append([[-big, -big, h], [big, -big, h], [big, big, h]])
            occ.append([[-big, -big, h], [big, big, h], [-big, big, h]])
        else:
            spec = PROP_DEFS.get(base, {})
            z0 = spec.get("z0", 0.0) or (SURFACE_TOP if base in SURFACE_ITEM_KINDS else 0.0)
            from game.renderer3d import DECAL_KINDS, DECAL_LIFT
            if base in DECAL_KINDS:
                z0 += DECAL_LIFT
            if z0 == 0.0 or base in SURFACE_ITEM_KINDS:
                occ.append([[-big, -big, 0.0], [big, -big, 0.0], [big, big, 0.0]])
                occ.append([[-big, -big, 0.0], [big, big, 0.0], [-big, big, 0.0]])
            if spec.get("wall_mounted"):
                wx = -scale[0] / 2
                occ.append([[wx, -big, -big], [wx, big, -big], [wx, big, big]])
                occ.append([[wx, -big, -big], [wx, big, big], [wx, -big, big]])
        fights, arrays = find_fights(soup, reach=20.0, min_clear=20.0, occluders=np.array(occ) if occ else None,
                                     eye_band=band, z_offset=z0)
        if fights:
            total += _report(f"mesh {name}", soup, fights, arrays, limit=6)
    print(f"MESHES: {total} visible fighting pairs")
    return total


def _floor_soup(app, R):
    maze = app.maze
    soup = Soup()
    for tile_type, data in R.build_maze_walls_by_type(maze).items():
        soup.add(data, f"walls:{tile_type}", True)
    soup.add(R.build_floor_mesh(maze), "floor", True)
    if app.spec.get("floor_theme") == "yard":
        roof_metal, roof_wood = R.build_yard_roofs_mesh(maze)
        soup.add(roof_metal, "yard_roofs", True)
        soup.add(roof_wood, "yard_roof_timber", True)
    else:
        soup.add(R.build_ceiling_mesh(maze), "ceiling", True)
    builders = app.renderer._mesh_builders
    for p in app.props + app.doors:
        if p.picked:
            continue
        m = np.frombuffer(R.prop_model_bytes(p), dtype=np.float32).reshape(4, 4).T.astype(np.float64)
        if p.kind == "locker":
            datas = [(R.build_locker_mesh(), "locker"), (R.build_locker_door_mesh(), "locker_door")]
        elif p.kind in R.VARIANT_MESH_BUILDERS:
            datas = [(R.VARIANT_MESH_BUILDERS[p.kind][p.variant](), f"{p.kind}_v{p.variant}")]
        elif p.kind == "door":
            if getattr(p, "is_open", False):
                continue
            datas = [(R.build_broken_door_mesh() if getattr(p, "is_broken", False) else R.build_door_mesh(), "door")]
        elif p.kind == "pipes":
            neg = getattr(p, "pipe_end_neg", "bend")
            pos = getattr(p, "pipe_end_pos", "bend")
            datas = [(R.build_pipes_mesh(neg=neg, pos=pos), "pipes_%s_%s" % (neg, pos))]
        elif p.kind in R.BREAKABLE_LIGHT_KINDS and getattr(p, "broken", False):
            datas = [(builders[p.kind + "_broken"](), p.kind + "_broken")]
        elif p.kind == "fence_gap" and getattr(p, "cut", False):
            datas = [(R.build_fence_gap_open_mesh(), "fence_gap_open")]
        elif p.kind in builders:
            datas = [(builders[p.kind](), p.kind)]
        else:
            datas = [(R.build_box_mesh(), p.kind + "(box)")]
        for data, label in datas:
            soup.add(data, f"{label}@{p.x:.2f},{p.y:.2f}", bool(p.texture), matrix=m)
    return soup


def floor_pass():
    import random
    import shutil
    import tempfile
    import pygame
    import game.app as A
    from game import settings as S
    from game import renderer3d as R
    tmp = tempfile.mkdtemp()
    if A.SETTINGS_PATH.exists():
        shutil.copy(A.SETTINGS_PATH, os.path.join(tmp, "settings.json"))
    A.SETTINGS_PATH = A.Path(os.path.join(tmp, "settings.json"))
    runs = int(sys.argv[sys.argv.index("--runs") + 1]) if "--runs" in sys.argv else 2
    app = A.App()
    pygame.display.flip = lambda: None
    total = 0
    try:
        for run in range(runs):
            app.new_game()
            app._start_playing()
            for fi in range(len(S.FLOOR_SPECS)):
                random.seed(9001 + run * 16 + fi)
                app._load_floor(fi)
                soup = _floor_soup(app, R)
                fights, arrays = find_fights(soup, reach=RAY_REACH, local_radius=RAY_REACH)
                total += _report(f"run {run} floor {fi} ({app.maze.w}x{app.maze.h}, seed {app.floor_seed})",
                                 soup, fights, arrays)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"FLOORS: {total} visible fighting pairs")
    return total


if __name__ == "__main__":
    which = [a for a in sys.argv[1:] if not a.startswith("-")] or ["meshes", "floors"]
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    if len(which) > 1:
        import subprocess
        bad = 0
        for name in which:
            r = subprocess.run([sys.executable, os.path.abspath(__file__), name])
            bad += 1 if r.returncode else 0
        sys.exit(1 if bad else 0)
    bad = 0
    if "meshes" in which:
        bad += mesh_pass()
    if "floors" in which:
        bad += floor_pass()
    sys.exit(1 if bad else 0)
