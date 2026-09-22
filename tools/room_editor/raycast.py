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


def ray_floor_point(mx, my, viewport_w, viewport_h, eye, yaw, pitch, fovy, aspect, plane_z=0.0):
    origin, direction = screen_ray(mx, my, viewport_w, viewport_h, eye, yaw, pitch, fovy, aspect)
    dz = direction[2]
    if abs(dz) < 1e-6:
        return None
    t = (plane_z - origin[2]) / dz
    if t <= 0:
        return None
    return origin[0] + direction[0] * t, origin[1] + direction[1] * t


def ray_floor_cell(mx, my, viewport_w, viewport_h, eye, yaw, pitch, fovy, aspect, plane_z=0.0):
    point = ray_floor_point(mx, my, viewport_w, viewport_h, eye, yaw, pitch, fovy, aspect, plane_z)
    if point is None:
        return None
    return math.floor(point[0]), math.floor(point[1])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def world_to_screen(p, viewport_w, viewport_h, eye, yaw, pitch, fovy, aspect):
    forward, screen_right, up = camera_basis(yaw, pitch)
    d = (p[0] - eye[0], p[1] - eye[1], p[2] - eye[2])
    z = _dot(d, forward)
    if z <= 1e-4:
        return None
    tan_half = math.tan(fovy / 2.0)
    nx = _dot(d, screen_right) / z / (tan_half * aspect)
    ny = _dot(d, up) / z / tan_half
    return (nx + 1.0) * 0.5 * viewport_w, (1.0 - ny) * 0.5 * viewport_h


def ray_vertical_plane_z(mx, my, viewport_w, viewport_h, eye, yaw, pitch, fovy, aspect,
                         px, py):
    origin, direction = screen_ray(mx, my, viewport_w, viewport_h, eye, yaw, pitch, fovy, aspect)
    nx, ny = math.cos(yaw), math.sin(yaw)
    denom = direction[0] * nx + direction[1] * ny
    if abs(denom) < 1e-6:
        return None
    t = ((px - origin[0]) * nx + (py - origin[1]) * ny) / denom
    if t <= 0:
        return None
    return origin[2] + direction[2] * t
