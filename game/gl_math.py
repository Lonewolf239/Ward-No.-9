import math

import numpy as np


def identity():
    return np.eye(4, dtype=np.float64)


def trs_z(x, y, z, theta, sx, sy, sz):
    c, s = math.cos(theta), math.sin(theta)
    return np.array((
        (c * sx, -s * sy, 0.0, x),
        (s * sx, c * sy, 0.0, y),
        (0.0, 0.0, sz, z),
        (0.0, 0.0, 0.0, 1.0),
    ))


def translate(x, y, z):
    m = np.eye(4)
    m[0, 3], m[1, 3], m[2, 3] = x, y, z
    return m


def scale(sx, sy, sz):
    m = np.eye(4)
    m[0, 0], m[1, 1], m[2, 2] = sx, sy, sz
    return m


def rotate_z(theta):
    c, s = np.cos(theta), np.sin(theta)
    m = np.eye(4)
    m[0, 0], m[0, 1] = c, -s
    m[1, 0], m[1, 1] = s, c
    return m


def rotate_y(theta):
    c, s = np.cos(theta), np.sin(theta)
    m = np.eye(4)
    m[0, 0], m[0, 2] = c, s
    m[2, 0], m[2, 2] = -s, c
    return m


def perspective(fovy, aspect, near, far):
    f = 1.0 / np.tan(fovy / 2.0)
    m = np.zeros((4, 4))
    m[0, 0] = f / aspect
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = (2 * far * near) / (near - far)
    m[3, 2] = -1.0
    return m


def view_matrix(eye, yaw, pitch):
    cy, sy = np.cos(yaw), np.sin(yaw)
    cp, sp = np.cos(pitch), np.sin(pitch)
    forward = np.array([cy * cp, sy * cp, sp])
    world_up = np.array([0.0, 0.0, 1.0])
    right = np.cross(forward, world_up)
    rn = np.linalg.norm(right)
    right = right / rn if rn > 1e-8 else np.array([1.0, 0.0, 0.0])
    up = np.cross(right, forward)

    screen_right = -right

    m = np.eye(4)
    m[0, 0:3] = screen_right
    m[1, 0:3] = up
    m[2, 0:3] = -forward
    eye = np.asarray(eye, dtype=np.float64)
    m[0, 3] = -np.dot(screen_right, eye)
    m[1, 3] = -np.dot(up, eye)
    m[2, 3] = np.dot(forward, eye)
    return m


def to_gl(m):
    return np.ascontiguousarray(m.T, dtype="f4")
