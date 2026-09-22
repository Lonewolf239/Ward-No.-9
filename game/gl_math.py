import math

import numpy as np


_IDENTITY = np.eye(4, dtype=np.float64)


def identity():
    return _IDENTITY.copy()


def trs_z(x, y, z, theta, sx, sy, sz):
    c, s = math.cos(theta), math.sin(theta)
    return np.array((
        (c * sx, -s * sy, 0.0, x),
        (s * sx, c * sy, 0.0, y),
        (0.0, 0.0, sz, z),
        (0.0, 0.0, 0.0, 1.0),
    ))


def translate(x, y, z):
    m = _IDENTITY.copy()
    m[0, 3] = x
    m[1, 3] = y
    m[2, 3] = z
    return m


def scale(sx, sy, sz):
    m = _IDENTITY.copy()
    m[0, 0] = sx
    m[1, 1] = sy
    m[2, 2] = sz
    return m


def rotate_z(theta):
    c, s = math.cos(theta), math.sin(theta)
    m = _IDENTITY.copy()
    m[0, 0] = c
    m[0, 1] = -s
    m[1, 0] = s
    m[1, 1] = c
    return m


def rotate_x(theta):
    c, s = math.cos(theta), math.sin(theta)
    return np.array((
        (1.0, 0.0, 0.0, 0.0),
        (0.0, c, -s, 0.0),
        (0.0, s, c, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    ), dtype="f4")


def rotate_y(theta):
    c, s = math.cos(theta), math.sin(theta)
    return np.array((
        (c, 0.0, s, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (-s, 0.0, c, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    ))


def perspective(fovy, aspect, near, far):
    f = 1.0 / np.tan(fovy / 2.0)
    m = np.zeros((4, 4))
    m[0, 0] = f / aspect
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = (2 * far * near) / (near - far)
    m[3, 2] = -1.0
    return m


def orthographic(left, right, bottom, top, near, far):
    m = np.eye(4)
    m[0, 0] = 2.0 / (right - left)
    m[1, 1] = 2.0 / (top - bottom)
    m[2, 2] = -2.0 / (far - near)
    m[0, 3] = -(right + left) / (right - left)
    m[1, 3] = -(top + bottom) / (top - bottom)
    m[2, 3] = -(far + near) / (far - near)
    return m


def view_matrix(eye, yaw, pitch, roll=0.0):
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    fx, fy, fz = cy * cp, sy * cp, sp
    rx, ry, rz = fy * 1.0 - fz * 0.0, fz * 0.0 - fx * 1.0, fx * 0.0 - fy * 0.0
    rn = math.sqrt(rx * rx + ry * ry + rz * rz)
    if rn > 1e-8:
        rx, ry, rz = rx / rn, ry / rn, rz / rn
    else:
        rx, ry, rz = 1.0, 0.0, 0.0
    ux, uy, uz = ry * fz - rz * fy, rz * fx - rx * fz, rx * fy - ry * fx

    if roll:
        cr, sr = math.cos(roll), math.sin(roll)
        rx, ry, rz, ux, uy, uz = (rx * cr + ux * sr, ry * cr + uy * sr, rz * cr + uz * sr,
                                  -rx * sr + ux * cr, -ry * sr + uy * cr, -rz * sr + uz * cr)

    sx_, sy_, sz_ = -rx, -ry, -rz
    ex, ey, ez = float(eye[0]), float(eye[1]), float(eye[2])

    m = np.eye(4)
    m[0, 0:3] = (sx_, sy_, sz_)
    m[1, 0:3] = (ux, uy, uz)
    m[2, 0:3] = (-fx, -fy, -fz)
    m[0, 3] = -(sx_ * ex + sy_ * ey + sz_ * ez)
    m[1, 3] = -(ux * ex + uy * ey + uz * ez)
    m[2, 3] = fx * ex + fy * ey + fz * ez
    return m


def to_gl(m):
    return np.ascontiguousarray(m.T, dtype="f4")
