import math


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(v):
    n = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    if n < 1e-8:
        return (1.0, 0.0, 0.0)
    return (v[0] / n, v[1] / n, v[2] / n)


def _add(*vs):
    return tuple(sum(c) for c in zip(*vs))


def _scale(v, s):
    return (v[0] * s, v[1] * s, v[2] * s)


def camera_basis(yaw, pitch):
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    forward = (cy * cp, sy * cp, sp)
    right = _norm(_cross(forward, (0.0, 0.0, 1.0)))
    up = _cross(right, forward)
    screen_right = _scale(right, -1.0)
    return forward, screen_right, up


def screen_ray(mx, my, viewport_w, viewport_h, eye, yaw, pitch, fovy, aspect):
    forward, screen_right, up = camera_basis(yaw, pitch)
    nx = (2.0 * mx / viewport_w) - 1.0
    ny = 1.0 - (2.0 * my / viewport_h)
    tan_half = math.tan(fovy / 2.0)
    vx = nx * tan_half * aspect
    vy = ny * tan_half
    direction = _norm(_add(_scale(screen_right, vx), _scale(up, vy), forward))
    return eye, direction


def ray_floor_cell(mx, my, viewport_w, viewport_h, eye, yaw, pitch, fovy, aspect, plane_z=0.0):
    origin, direction = screen_ray(mx, my, viewport_w, viewport_h, eye, yaw, pitch, fovy, aspect)
    dz = direction[2]
    if abs(dz) < 1e-6:
        return None
    t = (plane_z - origin[2]) / dz
    if t <= 0:
        return None
    x = origin[0] + direction[0] * t
    y = origin[1] + direction[1] * t
    return math.floor(x), math.floor(y)
