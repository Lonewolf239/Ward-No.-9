import math

from game import settings as S

HELD_ITEM_DEFS = {
    "flashlight": dict(
        mesh="flashlight_handheld",
        body_color=(0.58, 0.58, 0.62),
        hand_offset=(0.15, -0.13, 0.30),
        view_yaw=0.0,
        pull_seconds=0.32,
        stow_seconds=0.22,
    ),
    "lighter": dict(
        mesh="lighter_handheld",
        body_color=(1.0, 1.0, 1.0),
        light_radius=S.LIGHTER_LIGHT_RADIUS,
        light_color=S.LIGHTER_LIGHT_COLOR,
        hand_offset=(0.13, -0.12, 0.27),
        view_yaw=math.radians(-38),
        pull_seconds=0.30,
        stow_seconds=0.20,
    ),
    "map": dict(
        mesh="map_handheld",
        body_color=(1.0, 1.0, 1.0),
        hand_offset=(0.0, -0.19, 0.30),
        view_pitch=math.radians(45),
        raised_offset=(0.0, -0.05, 0.34),
        raised_pitch=math.radians(10),
        clearance=False,
        depth_squash=0.25,
        bounce_light=0.3,
        pull_seconds=0.35,
        stow_seconds=0.25,
    ),
}
