import math


class FreeCamera:
    def __init__(self, x=2.5, y=2.5, z=3.0, yaw=math.pi, pitch=-0.5):
        self.x, self.y, self.z = x, y, z
        self.yaw = yaw
        self.pitch = pitch
        self.move_speed = 4.0
        self.sprint_mult = 2.5

    @property
    def eye(self):
        return (self.x, self.y, self.z)

    def look(self, dx_pixels, dy_pixels, sensitivity=0.0035):
        self.yaw += dx_pixels * sensitivity
        self.pitch -= dy_pixels * sensitivity
        limit = math.pi / 2 - 0.02
        self.pitch = max(-limit, min(limit, self.pitch))

    def update(self, dt, keys, sprint=False):
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        forward = (cy, sy)
        screen_right = (-sy, cy)
        speed = self.move_speed * (self.sprint_mult if sprint else 1.0) * dt
        mx = my = mz = 0.0
        if keys.get("forward"):
            mx += forward[0]; my += forward[1]
        if keys.get("back"):
            mx -= forward[0]; my -= forward[1]
        if keys.get("right"):
            mx += screen_right[0]; my += screen_right[1]
        if keys.get("left"):
            mx -= screen_right[0]; my -= screen_right[1]
        if keys.get("up"):
            mz += 1.0
        if keys.get("down"):
            mz -= 1.0
        n = math.hypot(mx, my)
        if n > 1e-6:
            mx, my = mx / n, my / n
        self.x += mx * speed
        self.y += my * speed
        self.z += mz * speed * 0.6
        self.z = max(0.3, min(20.0, self.z))
