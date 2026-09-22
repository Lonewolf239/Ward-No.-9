import math
import operator
import random
import struct
import time
from collections import deque

import moderngl
import numpy as np

from game import settings as S
from game import gl_math as gm
from game.held_items import HELD_ITEM_DEFS
from game.props import BREAKABLE_LIGHT_KINDS, DOOR_LEAF, PROP_DEFS, SURFACE_TOP_FRAC, props_near_segment

from game.lighting import (
    FLASH_HOT_GAIN, SHAPE_OMNI, SHAPE_SPOT, SHAPE_DIRECTIONAL,
    FLASH_SHADOW_NEAR, FLASH_SHADOW_FAR, FLASH_SHADOW_FOV_DEGREES, FLASH_SHADOW_BIAS_K,
    POINT_SHADOW_NEAR, POINT_SHADOW_FAR, POINT_SHADOW_FOV_DEGREES, POINT_SHADOW_BIAS_K,
    MOON_SHADOW_NEAR, MOON_SHADOW_FAR, MOON_SHADOW_ORIGIN_DIST, MOON_SHADOW_HALF_EXTENT,
    SHADOW_NORMAL_OFFSET,
    MOON_SHADOW_RECENTER_DIST, MOON_SHADOW_BIAS_K, MOON_TOWARD,
    MAX_OMNI_LIGHTS, OMNI_FACE_DIRS, FIXED_LIGHTS, FIXED_TILES, TILE_MOON, TILE_FLASH,
    GAMEPLAY_RECEIVER_HEIGHT,
    omni_tile0, select_prop_lights, prop_emission, EMISSIVE_GLOW, flashlight_light, flashlight_fill_light, lighter_light, moon_light,
    FLASH_FILL_SHARE,
)

EYE_HEIGHT = 0.62
FOV_DEGREES = 88.0

FOG_EXPONENT = S.FOG_EXPONENT
COLOR_QUANT_LEVELS = 44.0

SHADOW_POLYGON_OFFSET = (0.0, 2.0)

SHADOW_BLOCKER_MIN_DIST = 0.1
MOON_BLOCKER_SEARCH_DEPTH = 20.0
SHADOW_MAX_SEARCH_TEXELS = 16.0
SHADOW_MAX_FILTER_TEXELS = 16.0
SHADOW_QUALITY_TAPS = {
    "low": (8, 12),
    "medium": (12, 20),
    "high": (16, 32),
}

SHADOW_REBUILD_FACE_BUDGET = 24

DOOR_SWING_MAX_ANGLE = math.radians(120)


def _configure_shadow_texture(tex):
    tex.compare_func = "<="
    tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
    tex.repeat_x = False
    tex.repeat_y = False


SHADOW_ATLAS_GUTTER = 2


def shadow_atlas_layout(point_res, flash_res, moon_res, omni_lights):
    g = SHADOW_ATLAS_GUTTER
    faces = 6 * omni_lights
    per_row = 6 * max(1, math.ceil(math.sqrt(faces) / 6.0))
    rects = [None] * (FIXED_TILES + faces)
    rects[TILE_MOON] = (g, g, moon_res)
    rects[TILE_FLASH] = (g + moon_res + g, g, flash_res)
    grid_y = g + max(moon_res, flash_res) + g
    for i in range(faces):
        row, col = divmod(i, per_row)
        rects[FIXED_TILES + i] = (g + col * (point_res + g), grid_y + row * (point_res + g), point_res)
    rows = max(1, math.ceil(faces / per_row))
    width = max(rects[TILE_FLASH][0] + flash_res + g, g + per_row * (point_res + g))
    height = grid_y + rows * (point_res + g)
    return width, height, rects


_PACK_MAT4 = struct.Struct("16f").pack


_INSTALL_PARTS = {
    "fuse_box":    dict(part="fuse", mode="fit", at=(-0.090, 0.1344, 0.2545),
                        step=(0.0, -0.11, 0.0), scale=0.761, yaw=0.0,
                        roll=math.pi * 0.5),
    "valve_panel": dict(part="valve_key", mode="fit", at=(0.092, -0.195, 0.511),
                        step=(0.0, 0.0, -0.13), scale=0.55, yaw=math.pi),
    "shed_lock":   dict(part="key", mode="fit", at=(0.175, -0.400, 0.508),
                        step=(0.0, 0.0, -0.229), scale=1.0, yaw=math.pi,
                        turn=math.tau, fit_frac=0.25, keep=False),
    "workbench":   dict(part="cutters", mode="vice", at=(0.30, 0.32, 0.375),
                        step=(0.0, 0.0, 0.0), scale=1.0, yaw=0.0,
                        tool="whetstone", tool_at=(0.335, 0.32, 0.455), tool_travel=0.045),
}


def part_model(p, lx, ly, lz, kind, yaw=0.0, roll=0.0, tilt=0.0, scale=1.0):
    c, sn = math.cos(p.facing), math.sin(p.facing)
    d = PROP_DEFS[kind]
    m = gm.translate(p.x + c * lx - sn * ly, p.y + sn * lx + c * ly, p.z0 + lz)
    m = m @ gm.rotate_z(p.facing + yaw)
    if tilt:
        m = m @ gm.rotate_y(tilt)
    if roll:
        m = m @ gm.rotate_x(roll)
    return m @ gm.scale(d["hd"] * 2 * scale, d["hw"] * 2 * scale, d["height"] * scale)


def prop_model_bytes(p):
    key = (p.x, p.y, p.z0, p.facing, p.hd, p.hw, p.height)
    cached = p.__dict__.get("_model_bytes")
    if cached is not None and cached[0] == key:
        return cached[1]
    x, y, z, theta, hd, hw, sz = key
    if p.kind in DECAL_KINDS:
        z += DECAL_LIFT
    c, s = math.cos(theta), math.sin(theta)
    sx, sy = hd * 2, hw * 2
    data = _PACK_MAT4(c * sx, s * sx, 0.0, 0.0,
                      -s * sy, c * sy, 0.0, 0.0,
                      0.0, 0.0, sz, 0.0,
                      x, y, z, 1.0)
    p._model_bytes = (key, data)
    return data


def door_break_model(p):
    t = p.fall_t
    rest = S.DOOR_FALL_REST * (math.pi / 2.0)
    land = S.DOOR_FALL_LAND
    if not p.is_broken:
        angle = S.DOOR_STRAIN_TILT * p.strain * p.strain_gain
    elif t >= 1.0:
        angle = rest
    elif t < land:
        angle = rest * (t / land) ** 2
    else:
        f = (t - land) / (1.0 - land)
        angle = rest - S.DOOR_FALL_BOUNCE * math.sin(f * math.pi) * (1.0 - f)
    theta = p.base_facing + p.break_askew
    key = (p.x, p.y, p.z0, theta, p.hd, p.hw, p.height, angle, p.fall_dir)
    cached = p.__dict__.get("_fall_model_bytes")
    if cached is not None and cached[0] == key:
        return cached[1]
    a = angle * p.fall_dir
    sx, sy, sz = p.hd * 2.0, p.hw * 2.0, p.height
    ca, sa = math.cos(a), math.sin(a)
    ct, st = math.cos(theta), math.sin(theta)
    px = p.fall_dir * p.hd
    slide = p.fall_dir * S.DOOR_FALL_SLIDE * (min(1.0, t / land) if p.is_broken else 0.0)
    dx = px + slide - px * ca
    dz = px * sa
    data = _PACK_MAT4(ct * sx * ca, st * sx * ca, -sx * sa, 0.0,
                      -st * sy, ct * sy, 0.0, 0.0,
                      ct * sz * sa, st * sz * sa, sz * ca, 0.0,
                      p.x + ct * dx, p.y + st * dx, p.z0 + dz, 1.0)
    p._fall_model_bytes = (key, data)
    return data


def _rect_uniform(rect):
    x, y, size = rect
    return (float(x), float(y), float(size), 0.0)


VERTEX_SHADER = """
#version 330
uniform mat4 model;
uniform mat4 view;
uniform mat4 proj;
uniform float snap_res;
uniform float snap_object;
uniform vec4 snap_anchor;
uniform float depth_squash;

in vec3 in_pos;
in vec3 in_normal;
in vec2 in_uv;
in vec3 in_color;

out vec3 v_world_pos;
out vec3 v_normal;
out vec2 v_uv;
out vec3 v_color;

void main() {
    vec4 world = model * vec4(in_pos, 1.0);
    vec4 viewpos = view * world;
    vec4 clip = proj * viewpos;

    if (clip.w > 0.0001) {
        if (snap_object > 0.5) {
            vec4 origin_clip = snap_anchor.w > 0.5 ? proj * view * vec4(snap_anchor.xyz, 1.0)
                                                   : proj * view * model * vec4(0.0, 0.0, 0.0, 1.0);
            if (origin_clip.w > 0.0001) {
                vec2 o_ndc = origin_clip.xy / origin_clip.w;
                clip.xy += (floor(o_ndc * snap_res + 0.5) / snap_res - o_ndc) * clip.w;
            }
        } else {
            vec2 ndc = clip.xy / clip.w;
            vec2 snapped = floor(ndc * snap_res + 0.5) / snap_res;
            clip.xy = snapped * clip.w;
        }
    }
    if (depth_squash > 0.0) {
        clip.z = mix(-clip.w, clip.z, depth_squash);
    }

    gl_Position = clip;
    v_world_pos = world.xyz;
    v_normal = mat3(model) * in_normal;
    v_uv = in_uv;
    v_color = in_color;
}
"""

SPECULAR_SHININESS = 1200.0

FRAGMENT_SHADER = """
#version 330
uniform vec3 base_color;
uniform float emissive;
uniform vec3 cam_pos;
uniform float is_held_item;
uniform float held_bounce;
uniform vec3 held_fill;
uniform vec3 held_shadow_at;
uniform sampler2DShadow shadow_atlas;
uniform sampler2D wall_grid;
uniform ivec2 wall_grid_size;
uniform sampler2D shadow_atlas_raw;
uniform vec2 shadow_atlas_size;
uniform int shadow_blocker_taps;
uniform int shadow_filter_taps;
uniform vec3 fog_color;
uniform float fog_dist;
uniform vec2 tex_scale;
uniform sampler2D tex0;
uniform float use_tex;
uniform float u_time;
uniform float sanity_dark;
uniform float hallu_intensity;
uniform vec2 u_resolution;
uniform float ambient_level;
uniform vec3 moon_dir;
uniform float moon_strength;
uniform float world_flood;
uniform vec4 burn_c;
uniform float burn_width;
uniform float ground_haze;
uniform float haze_height;
uniform vec3 haze_color;
uniform float no_fog;
uniform float outdoor;
uniform float moon_outdoor_only;
uniform float outdoor_ambient;
uniform vec3 outdoor_fog_color;
uniform float outdoor_fog_dist;
uniform vec3 grime;
uniform vec4 dado;
uniform vec4 dado_color;
uniform float flat_shade;
uniform float qa_mode;
uniform vec4 wall_holes[4];
uniform int wall_hole_count;
uniform float wall_hole_top;
uniform float apply_wall_holes;
uniform float gamma;
uniform float u_alpha;
uniform float material_diffuse;
uniform float material_specular;
uniform float alpha_cutout;

#define MAX_LIGHTS __MAX_LIGHTS__
#define MAX_SHADOW_TILES __MAX_SHADOW_TILES__
#define SHAPE_OMNI """ + str(SHAPE_OMNI) + """
#define SHAPE_SPOT """ + str(SHAPE_SPOT) + """
#define SHAPE_DIRECTIONAL """ + str(SHAPE_DIRECTIONAL) + """
uniform int light_count;
uniform vec3 light_pos[MAX_LIGHTS];
uniform vec3 light_dir[MAX_LIGHTS];
uniform vec3 light_color[MAX_LIGHTS];
uniform float light_radius[MAX_LIGHTS];
uniform float light_falloff[MAX_LIGHTS];
uniform int light_shape[MAX_LIGHTS];
uniform vec4 light_cone[MAX_LIGHTS];
uniform int light_tile0[MAX_LIGHTS];
uniform float light_bias_k[MAX_LIGHTS];
uniform float light_skip_bearer[MAX_LIGHTS];
uniform float light_emitter[MAX_LIGHTS];
uniform mat4 tile_mvp[MAX_SHADOW_TILES];
uniform vec4 tile_rect[MAX_SHADOW_TILES];

in vec3 v_world_pos;
in vec3 v_normal;
in vec2 v_uv;
in vec3 v_color;
out vec4 frag_color;

const float FLASH_NEAR = """ + repr(FLASH_SHADOW_NEAR) + """;
const float FLASH_FAR = """ + repr(FLASH_SHADOW_FAR) + """;
const float FLASH_TAN_HALF = """ + repr(math.tan(math.radians(FLASH_SHADOW_FOV_DEGREES) / 2.0)) + """;
const float POINT_NEAR = """ + repr(POINT_SHADOW_NEAR) + """;
const float POINT_FAR = """ + repr(POINT_SHADOW_FAR) + """;
const float POINT_TAN_HALF = """ + repr(math.tan(math.radians(POINT_SHADOW_FOV_DEGREES) / 2.0)) + """;
const float MOON_NEAR = """ + repr(MOON_SHADOW_NEAR) + """;
const float MOON_FAR = """ + repr(MOON_SHADOW_FAR) + """;
const float MOON_HALF_EXTENT = """ + repr(MOON_SHADOW_HALF_EXTENT) + """;
const float WALL_TOP = """ + repr(S.WALL_HEIGHT) + """;
const float SHADOW_BLOCKER_MIN_DIST = """ + repr(SHADOW_BLOCKER_MIN_DIST) + """;
const float MOON_BLOCKER_SEARCH_DEPTH = """ + repr(MOON_BLOCKER_SEARCH_DEPTH) + """;
const float SHADOW_MAX_SEARCH_TEXELS = """ + repr(SHADOW_MAX_SEARCH_TEXELS) + """;
const float SHADOW_MAX_FILTER_TEXELS = """ + repr(SHADOW_MAX_FILTER_TEXELS) + """;
const float SHADOW_NORMAL_OFFSET = """ + repr(SHADOW_NORMAL_OFFSET) + """;
const float GOLDEN_ANGLE = 2.39996323;

float shadow_axis_depth(float d, int shape) {
    if (shape == SHAPE_DIRECTIONAL) {
        return MOON_NEAR + d * (MOON_FAR - MOON_NEAR);
    }
    float n = shape == SHAPE_SPOT ? FLASH_NEAR : POINT_NEAR;
    float f = shape == SHAPE_SPOT ? FLASH_FAR : POINT_FAR;
    return 2.0 * n * f / (f + n - (d * 2.0 - 1.0) * (f - n));
}

float shadow_uv_per_world(float z, int shape) {
    if (shape == SHAPE_DIRECTIONAL) {
        return 0.5 / MOON_HALF_EXTENT;
    }
    return 0.5 / (max(z, 0.0001) * (shape == SHAPE_SPOT ? FLASH_TAN_HALF : POINT_TAN_HALF));
}

vec2 vogel_disk(int i, int n, float phi) {
    float r = sqrt((float(i) + 0.5) / float(n));
    float a = float(i) * GOLDEN_ANGLE + phi;
    return r * vec2(cos(a), sin(a));
}

float sample_shadow_tex_core(vec4 shadowRect, mat4 mvp, vec3 worldPos, vec3 N, float ndotl,
                              float distToLight, float depthBiasK, int shape, float emitter) {
    float tan_half = shape == SHAPE_SPOT ? FLASH_TAN_HALF : POINT_TAN_HALF;
    float texel_world = shape == SHAPE_DIRECTIONAL ? 2.0 * MOON_HALF_EXTENT / shadowRect.z
                                                   : 2.0 * distToLight * tan_half / shadowRect.z;
    worldPos += N * (texel_world * SHADOW_NORMAL_OFFSET);
    vec4 clip = mvp * vec4(worldPos, 1.0);
    if (clip.w <= 0.0001) return 0.0;
    vec3 proj = clip.xyz / clip.w;
    vec2 uv = proj.xy * 0.5 + 0.5;
    float fragDepth = proj.z * 0.5 + 0.5;
    if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0 || fragDepth > 1.0) {
        return 0.0;
    }
    float slope = sqrt(max(0.0, 1.0 - ndotl * ndotl)) / max(ndotl, 0.06);
    float worldEps = clamp(0.008 + slope * 0.006, 0.008, 0.05);
    float bias = clamp(worldEps * depthBiasK / max(distToLight * distToLight, 0.01), 0.00004, 0.02);
    float zr = shadow_axis_depth(fragDepth, shape);

    vec3 t1 = normalize(cross(N, abs(N.z) < 0.9 ? vec3(0.0, 0.0, 1.0) : vec3(1.0, 0.0, 0.0)));
    vec3 t2 = cross(N, t1);
    vec2 dz_duv = vec2(0.0);
    vec4 c1 = mvp * vec4(worldPos + t1 * 0.02, 1.0);
    vec4 c2 = mvp * vec4(worldPos + t2 * 0.02, 1.0);
    if (c1.w > 0.0001 && c2.w > 0.0001) {
        vec2 du1 = (c1.xy / c1.w) * 0.5 + 0.5 - uv;
        vec2 du2 = (c2.xy / c2.w) * 0.5 + 0.5 - uv;
        float dd1 = (c1.z / c1.w) * 0.5 + 0.5 - fragDepth;
        float dd2 = (c2.z / c2.w) * 0.5 + 0.5 - fragDepth;
        float det = du1.x * du2.y - du1.y * du2.x;
        if (abs(det) > 1e-12) {
            dz_duv = vec2(dd1 * du2.y - dd2 * du1.y, du1.x * dd2 - du2.x * dd1) / det;
        }
    }

    vec2 atlasTexel = 1.0 / shadow_atlas_size;
    vec2 lo = (shadowRect.xy + 0.5) * atlasTexel;
    vec2 hi = (shadowRect.xy + vec2(shadowRect.z) - 0.5) * atlasTexel;
    vec2 base = (shadowRect.xy + uv * shadowRect.z) * atlasTexel;
    float faceUvPerTexel = 1.0 / shadowRect.z;
    float phi = 6.2831853 * fract(52.9829189 * fract(dot(gl_FragCoord.xy, vec2(0.06711056, 0.00583715))));

    float searchWorld;
    if (shape == SHAPE_DIRECTIONAL) {
        searchWorld = emitter * MOON_BLOCKER_SEARCH_DEPTH;
    } else {
        float zMin = max(shape == SHAPE_SPOT ? FLASH_NEAR : POINT_NEAR, SHADOW_BLOCKER_MIN_DIST);
        searchWorld = emitter * max(zr - zMin, 0.0) / zMin;
    }
    float searchTexels = clamp(searchWorld * shadow_uv_per_world(zr, shape) * shadowRect.z,
                               1.0, SHADOW_MAX_SEARCH_TEXELS);
    float blockerSum = 0.0;
    int blockers = 0;
    for (int i = 0; i < shadow_blocker_taps; i++) {
        vec2 o = vogel_disk(i, shadow_blocker_taps, phi) * searchTexels;
        float stored = texture(shadow_atlas_raw, clamp(base + o * atlasTexel, lo, hi)).r;
        float receiver = fragDepth + clamp(dot(dz_duv, o * faceUvPerTexel), -0.05, 0.05);
        if (stored < receiver - bias) {
            blockerSum += shadow_axis_depth(stored, shape);
            blockers++;
        }
    }
    if (blockers == 0) {
        return 0.0;
    }
    float zb = blockerSum / float(blockers);

    float gap = max(zr - zb, 0.0);
    float penumbraWorld = shape == SHAPE_DIRECTIONAL ? emitter * gap : emitter * gap / max(zb, 0.001);
    float filterTexels = clamp(penumbraWorld * shadow_uv_per_world(zr, shape) * shadowRect.z,
                               1.0, SHADOW_MAX_FILTER_TEXELS);

    float lit = 0.0;
    for (int i = 0; i < shadow_filter_taps; i++) {
        vec2 o = vogel_disk(i, shadow_filter_taps, phi) * filterTexels;
        float ref = fragDepth + clamp(dot(dz_duv, o * faceUvPerTexel), -0.05, 0.05) - bias;
        lit += texture(shadow_atlas, vec3(clamp(base + o * atlasTexel, lo, hi), ref));
    }
    return 1.0 - lit / float(max(shadow_filter_taps, 1));
}

float sample_shadow_tex(vec4 shadowRect, mat4 mvp, vec3 worldPos, vec3 N, vec3 lightPos, float depthBiasK,
                        int shape, float emitter) {
    vec3 toLight = lightPos - worldPos;
    float distToLight = length(toLight);
    float ndotl = distToLight > 0.0001 ? clamp(dot(N, toLight / distToLight), 0.0, 1.0) : 1.0;
    return sample_shadow_tex_core(shadowRect, mvp, worldPos, N, ndotl, distToLight, depthBiasK, shape, emitter);
}

float sample_shadow_tex_directional(vec4 shadowRect, mat4 mvp, vec3 worldPos, vec3 N, vec3 lightDir,
                                     float depthBiasK, float emitter) {
    float ndotl = clamp(dot(N, lightDir), 0.0, 1.0);
    return sample_shadow_tex_core(shadowRect, mvp, worldPos, N, ndotl, 1.0, depthBiasK, SHAPE_DIRECTIONAL, emitter);
}

float light_atten(vec3 lightPos, vec3 worldPos, float radius, float shape) {
    float d = length(lightPos - worldPos);
    float lt = clamp(d / max(radius, 0.001), 0.0, 1.0);
    float ramp = pow(1.0 - lt, shape);
    return ramp * (1.0 - smoothstep(0.85, 1.0, lt));
}

float grime_hash(vec3 p) {
    return fract(sin(dot(p, vec3(127.1, 311.7, 74.7))) * 43758.5453123);
}

float grime_value(vec3 p) {
    vec3 i = floor(p);
    vec3 f = p - i;
    f = f * f * (3.0 - 2.0 * f);
    float n00 = mix(grime_hash(i + vec3(0.0, 0.0, 0.0)), grime_hash(i + vec3(1.0, 0.0, 0.0)), f.x);
    float n10 = mix(grime_hash(i + vec3(0.0, 1.0, 0.0)), grime_hash(i + vec3(1.0, 1.0, 0.0)), f.x);
    float n01 = mix(grime_hash(i + vec3(0.0, 0.0, 1.0)), grime_hash(i + vec3(1.0, 0.0, 1.0)), f.x);
    float n11 = mix(grime_hash(i + vec3(0.0, 1.0, 1.0)), grime_hash(i + vec3(1.0, 1.0, 1.0)), f.x);
    return mix(mix(n00, n10, f.y), mix(n01, n11, f.y), f.z);
}

float grime_mask(vec3 world) {
    vec3 p = world * vec3(grime.y, grime.y, grime.y * 0.22);
    float g = grime_value(p) * 0.65 + grime_value(p * 2.6 + 11.3) * 0.35;
    return 1.0 - grime.z * smoothstep(0.30, 0.85, g);
}

float light_facing(vec3 lightPos, vec3 worldPos, vec3 N) {
    vec3 toLight = lightPos - worldPos;
    float len = length(toLight);
    if (len < 0.0001) return 1.0;
    return pow(max(0.0, dot(N, toLight / len)), 0.45);
}

bool wall_between(vec2 a, vec2 b) {
    ivec2 cell = ivec2(floor(a));
    if (cell.x < 0 || cell.y < 0 || cell.x >= wall_grid_size.x || cell.y >= wall_grid_size.y
            || texelFetch(wall_grid, cell, 0).r > 0.5) {
        return true;
    }
    vec2 d = b - a;
    ivec2 stepDir = ivec2(sign(d));
    vec2 invAbs = 1.0 / max(abs(d), vec2(1e-6));
    vec2 frac = a - floor(a);
    vec2 tMax = vec2(d.x > 0.0 ? (1.0 - frac.x) * invAbs.x : (d.x < 0.0 ? frac.x * invAbs.x : 2.0),
                     d.y > 0.0 ? (1.0 - frac.y) * invAbs.y : (d.y < 0.0 ? frac.y * invAbs.y : 2.0));
    for (int i = 0; i < 64; i++) {
        float tEnter;
        if (tMax.x < tMax.y) {
            tEnter = tMax.x;
            tMax.x += invAbs.x;
            cell.x += stepDir.x;
        } else {
            tEnter = tMax.y;
            tMax.y += invAbs.y;
            cell.y += stepDir.y;
        }
        if (tEnter >= 1.0) {
            return false;
        }
        if (cell.x < 0 || cell.y < 0 || cell.x >= wall_grid_size.x || cell.y >= wall_grid_size.y) {
            return true;
        }
        if (texelFetch(wall_grid, cell, 0).r > 0.5) {
            return true;
        }
    }
    return false;
}

bool wall_umbra(vec3 lightPos, vec3 worldPos, vec3 N, float emitter) {
    if (worldPos.z > WALL_TOP || lightPos.z > WALL_TOP) {
        return false;
    }
    vec2 p = worldPos.xy + N.xy * 0.02;
    vec2 d = p - lightPos.xy;
    float len = length(d);
    if (len < 0.001) {
        return false;
    }
    vec2 side = vec2(-d.y, d.x) * (emitter / len);
    return wall_between(lightPos.xy + side, p) && wall_between(lightPos.xy - side, p);
}

float compute_fog_t(float dist, float fogDist) {
    return pow(clamp(dist / fogDist, 0.0, 1.0), """ + str(FOG_EXPONENT) + """);
}

void main() {
    if (apply_wall_holes > 0.5) {
        for (int i = 0; i < wall_hole_count; i++) {
            vec4 hole = wall_holes[i];
            float across = hole.w < 0.5 ? v_world_pos.x : v_world_pos.y;
            float along = hole.w < 0.5 ? v_world_pos.y : v_world_pos.x;
            if (abs(across - hole.x) < 0.03 && abs(along - hole.y) < hole.z && v_world_pos.z < wall_hole_top) {
                discard;
            }
        }
    }
    vec3 N = normalize(v_normal);
    if (gl_FrontFacing) {
        N = -N;
    }
    vec3 to_cam = cam_pos - v_world_pos;
    float dist = length(to_cam);

    float ambient = mix(ambient_level, outdoor_ambient, outdoor);
    vec3 air_color = mix(fog_color, outdoor_fog_color, outdoor);
    float air_dist = mix(fog_dist, outdoor_fog_dist, outdoor);
    vec3 color;
    if (no_fog > 0.5) {
        vec3 dir = normalize(v_world_pos - cam_pos);
        float horizon = 1.0 - clamp(dir.z, -0.2, 1.0);
        vec3 sky_base = mix(vec3(0.020, 0.024, 0.036), vec3(0.10, 0.11, 0.16), horizon * horizon);
        vec3 hp = floor(dir * 46.0);
        float sh = fract(sin(dot(hp, vec3(12.9898, 78.233, 37.719))) * 43758.5453);
        float star = step(0.9978, sh) * (0.45 + 0.55 * fract(sh * 91.7));
        vec3 sky_moon_dir = normalize(moon_dir);
        float moon_dot = max(0.0, dot(dir, sky_moon_dir));
        float moon_glow = pow(moon_dot, 220.0) * 1.5 + pow(moon_dot, 14.0) * 0.4;
        float moon_amt = clamp(moon_strength * 3.0, 0.3, 1.3);
        color = sky_base + vec3(star * 0.8) + moon_glow * vec3(0.75, 0.82, 0.95) * moon_amt;
    } else if (flat_shade > 0.5) {
        color = base_color * v_color;
        float fog_t = compute_fog_t(dist, air_dist);
        color = mix(color, air_color, fog_t);
    } else {
        vec3 lighting = vec3(ambient);
        if (is_held_item > 0.5) {
            lighting += held_fill;
        }
        vec3 highlight = vec3(0.0);
        vec3 view_dir = to_cam / max(dist, 0.0001);
        bool held = is_held_item > 0.5;
        vec3 occ_pos = held ? held_shadow_at : v_world_pos;
        for (int i = 0; i < light_count; i++) {
            if (is_held_item > 0.5 && light_skip_bearer[i] > 0.5) {
                continue;
            }
            int shape = light_shape[i];
            int tile0 = light_tile0[i];
            if (shape == SHAPE_DIRECTIONAL) {
                if (moon_outdoor_only > 0.5 && outdoor < 0.5) {
                    continue;
                }
                vec3 L = -light_dir[i];
                float k = max(held_bounce, dot(N, L));
                if (k <= 0.0) {
                    continue;
                }
                if (tile0 >= 0) {
                    k *= 1.0 - sample_shadow_tex_directional(tile_rect[tile0], tile_mvp[tile0],
                                                             occ_pos, held ? L : N, L, light_bias_k[i],
                                                             light_emitter[i]);
                }
                lighting += light_color[i] * k;
                if (material_specular > 0.0) {
                    highlight += light_color[i] * k * pow(max(dot(N, normalize(L + view_dir)), 0.0), """ + repr(SPECULAR_SHININESS) + """);
                }
                continue;
            }
            vec3 lp = light_pos[i];
            float k = light_atten(lp, v_world_pos, light_radius[i], light_falloff[i]);
            if (k <= 0.0) {
                continue;
            }
            k *= max(held_bounce, light_facing(lp, v_world_pos, N));
            int tile = tile0;
            if (shape == SHAPE_SPOT) {
                float c = dot(normalize(v_world_pos - lp), normalize(light_dir[i]));
                vec4 cone = light_cone[i];
                k *= smoothstep(cone.x, cone.y, c) + smoothstep(cone.z, cone.w, c) * """ + str(FLASH_HOT_GAIN) + """;
            } else {
                vec4 lobe = light_cone[i];
                if (lobe.w > 0.5) {
                    float c = dot(normalize(v_world_pos - lp), light_dir[i]);
                    k *= mix(lobe.z, 1.0, smoothstep(lobe.x, lobe.y, c));
                }
            }
            if (shape != SHAPE_SPOT && tile0 >= 0) {
                vec3 d = occ_pos - lp;
                vec3 ad = abs(d);
                if (ad.x >= ad.y && ad.x >= ad.z) {
                    tile = tile0 + (d.x >= 0.0 ? 0 : 1);
                } else if (ad.y >= ad.z) {
                    tile = tile0 + (d.y >= 0.0 ? 2 : 3);
                } else {
                    tile = tile0 + (d.z >= 0.0 ? 4 : 5);
                }
            }
            if (k <= 0.0) {
                continue;
            }
            if (tile >= 0) {
                vec3 occ_n = held ? normalize(lp - occ_pos) : N;
                if (wall_umbra(lp, occ_pos, occ_n, light_emitter[i])) {
                    continue;
                }
                k *= 1.0 - sample_shadow_tex(tile_rect[tile], tile_mvp[tile], occ_pos, occ_n, lp,
                                             light_bias_k[i], shape, light_emitter[i]);
            }
            lighting += light_color[i] * k;
            if (material_specular > 0.0) {
                vec3 L = normalize(lp - v_world_pos);
                highlight += light_color[i] * k * pow(max(dot(N, normalize(L + view_dir)), 0.0), """ + repr(SPECULAR_SHININESS) + """);
            }
        }
        lighting = clamp(lighting, 0.0, 1.45);
        vec3 tex_col = vec3(1.0);
        if (use_tex > 0.5) {
            vec4 texel = texture(tex0, v_uv * tex_scale);
            if (alpha_cutout > 0.5 && texel.a < 0.5) {
                discard;
            }
            tex_col = texel.rgb;
        }

        float glow_part = smoothstep(0.8, 0.95, max(v_color.r, max(v_color.g, v_color.b)));
        color = base_color * v_color * tex_col * (lighting * material_diffuse + emissive * glow_part * """ + repr(EMISSIVE_GLOW) + """);
        if (grime.x > 0.0) {
            color *= mix(1.0, grime_mask(v_world_pos), grime.x);
        }
        if (dado.x > 0.0 && abs(N.z) < 0.5) {
            float z = v_world_pos.z;
            vec3 band = dado_color.rgb * (lighting * material_diffuse);
            float skirt = 1.0 - smoothstep(dado.y, dado.y + 0.025, z);
            float rail = 1.0 - smoothstep(dado.w, dado.w + 0.012, abs(z - dado.z));
            float below = 1.0 - smoothstep(dado.z, dado.z + 0.02, z);
            color = mix(color, color * dado_color.a, dado.x * below);
            color = mix(color, band, dado.x * max(skirt, rail));
        }
        vec3 reflection = vec3(0.0);
        if (material_specular > 0.0) {
            float fresnel = pow(clamp(1.0 - dot(N, view_dir), 0.0, 1.0), 4.0);
            vec3 surroundings = vec3(ambient) + 0.1 * clamp(lighting, 0.0, 1.0);
            float glint = 0.04 + 0.96 * fresnel;
            reflection = (highlight * glint + fresnel * surroundings) * material_specular;
        }
        float fog_t = compute_fog_t(dist, air_dist);
        color = mix(color, air_color, fog_t);
        if (material_specular > 0.0) {
            color = color * u_alpha + reflection * (1.0 - fog_t);
        }
    }

    if (ground_haze > 0.0) {
        float lie = exp(-max(0.0, v_world_pos.z) / max(0.05, haze_height));
        float reach = 1.0 - exp(-dist * 0.05);
        color = mix(color, haze_color, clamp(ground_haze * lie * reach, 0.0, 0.92));
    }

    color = mix(color, vec3(1.05, 1.0, 0.9), world_flood);

    if (burn_c.w > 0.0) {
        vec3 cell = floor(v_world_pos * 5.0);
        float n = fract(sin(dot(cell, vec3(12.9898, 78.233, 37.719))) * 43758.5453);
        float d = length(v_world_pos - burn_c.xyz) + (n - 0.5) * burn_width;
        float inside = burn_c.w - d;
        if (inside > 0.0) {
            color = vec3(2.2, 2.05, 1.7);
        } else {
            color = mix(color, vec3(2.0, 1.75, 1.2), smoothstep(burn_width, 0.0, -inside));
        }
    }

    if (qa_mode < 0.5) {
        if (gamma != 1.0) {
            color = pow(max(color, vec3(0.0)), vec3(1.0 / gamma));
        }
        vec2 vp = gl_FragCoord.xy / u_resolution;
        float d = distance(vp, vec2(0.5, 0.5));
        float vig = smoothstep(0.32, 0.92, d);
        color = mix(color, vec3(0.0), vig * 0.55);
        color = mix(color, vec3(0.10, 0.0, 0.0), sanity_dark * 0.30);

        float luma = dot(color, vec3(0.333));
        float grain = fract(sin(dot(gl_FragCoord.xy + u_time * 130.0, vec2(12.9898, 78.233))) * 43758.5453);
        color += (grain - 0.5) * 0.010 * clamp(luma * 3.0, 0.35, 1.0) * (1.0 + hallu_intensity * 3.0);

        if (hallu_intensity > 0.0) {
            vec2 block = floor(gl_FragCoord.xy / 3.0);
            float coarse = fract(sin(dot(block + u_time * 19.0, vec2(41.3, 289.1))) * 24634.6345);
            color += (coarse - 0.5) * 0.16 * hallu_intensity;
            float swap_t = hallu_intensity * 0.38 * (0.5 + 0.5 * sin(u_time * 0.7));
            color = mix(color, color.gbr, swap_t);
        }

        const float bayer[16] = float[16](
            0.0, 8.0, 2.0, 10.0, 12.0, 4.0, 14.0, 6.0,
            3.0, 11.0, 1.0, 9.0, 15.0, 7.0, 13.0, 5.0
        );
        int xi = int(mod(gl_FragCoord.x, 4.0));
        int yi = int(mod(gl_FragCoord.y, 4.0));
        float threshold = (bayer[yi * 4 + xi] / 16.0 - 0.5) * (1.0 / 34.0);
        color += threshold;

        float levels = """ + str(COLOR_QUANT_LEVELS) + """;
        color = floor(color * levels + 0.5) / levels;
    }

    frag_color = vec4(clamp(color, 0.0, 1.0), u_alpha);
}
"""

QUAD_VERTEX_SHADER = """
#version 330
in vec2 in_pos;
in vec2 in_uv;
out vec2 v_uv;
void main() {
    v_uv = in_uv;
    gl_Position = vec4(in_pos, 0.0, 1.0);
}
"""

QUAD_FRAGMENT_SHADER = """
#version 330
uniform sampler2D tex0;
uniform float trip_intensity;
uniform float comedown_intensity;
uniform float u_time;
uniform float apply_trip;
uniform float raw_surface;
uniform vec3 god_ray;
in vec2 v_uv;
out vec4 frag_color;
void main() {
    vec2 uv = v_uv;
    float trip = trip_intensity * apply_trip;
    float comedown = comedown_intensity * apply_trip;
    if (trip > 0.0) {
        float wob = sin(uv.y * 9.0 + u_time * 1.3) * 0.026
                  + sin(uv.x * 7.0 - u_time * 0.9) * 0.026
                  + sin(uv.x * 15.0 + uv.y * 11.0 + u_time * 2.1) * 0.014;
        uv += wob * trip;
    }
    vec2 cuv = clamp(uv, 0.0, 1.0);
    if (raw_surface > 0.5) {
        cuv.y = 1.0 - cuv.y;
    }
    vec3 color;
    float srcA;
    if (trip > 0.0) {
        vec2 dir = normalize(cuv - vec2(0.5)) * trip;
        float d = length(cuv - vec2(0.5));
        float r = texture(tex0, clamp(cuv + dir * (0.011 + d * 0.017), 0.0, 1.0)).r;
        float g = texture(tex0, cuv).g;
        float b = texture(tex0, clamp(cuv - dir * (0.011 + d * 0.017), 0.0, 1.0)).b;
        srcA = texture(tex0, cuv).a;
        color = vec3(r, g, b);
    } else {
        vec4 src = texture(tex0, cuv);
        if (raw_surface > 0.5) {
            src = src.bgra;
        }
        color = src.rgb;
        srcA = src.a;
    }

    if (god_ray.z > 0.0 && apply_trip > 0.5) {
        vec2 step_uv = (cuv - god_ray.xy) / 28.0;
        vec2 samp = cuv;
        float illum = 1.0;
        vec3 shafts = vec3(0.0);
        for (int i = 0; i < 28; i++) {
            samp -= step_uv;
            vec3 s = texture(tex0, clamp(samp, 0.0, 1.0)).rgb;
            float bright = max(0.0, (s.r + s.g + s.b) * 0.3333 - 0.62);
            shafts += s * bright * illum;
            illum *= 0.955;
        }
        color += shafts * (god_ray.z * 0.16);
    }

    if (trip > 0.0) {
        float hue_t = 0.5 + 0.5 * sin(u_time * 0.55);
        vec3 rotated = mix(color.gbr, color.brg, hue_t);
        color = mix(color, rotated, trip * 0.92);

        vec3 glow = vec3(0.0);
        const int TAPS = 8;
        for (int i = 0; i < TAPS; i++) {
            float a = 6.2831853 * float(i) / float(TAPS);
            vec2 off = vec2(cos(a), sin(a)) * (0.016 + 0.010 * sin(u_time * 0.7 + a));
            vec3 s = texture(tex0, clamp(cuv + off, 0.0, 1.0)).rgb;
            float lum = dot(s, vec3(0.299, 0.587, 0.114));
            glow += s * smoothstep(0.22, 0.7, lum);
        }
        glow /= float(TAPS);
        color += glow * trip * 1.1;

        vec2 ghost_off = vec2(cos(u_time * 0.17), sin(u_time * 0.13)) * 0.020 * trip;
        vec3 ghost = texture(tex0, clamp(cuv + ghost_off, 0.0, 1.0)).rgb;
        color = mix(color, ghost, 0.16 * trip);
    }

    if (comedown > 0.0) {
        float lum = dot(color, vec3(0.299, 0.587, 0.114));
        color = mix(color, vec3(lum), comedown * 0.4);
        color *= 1.0 - comedown * 0.16;
    }

    frag_color = vec4(color, srcA);
}
"""

BILLBOARD_VERTEX_SHADER = """
#version 330
in vec2 in_local;
in vec2 in_uv;
uniform mat4 view;
uniform mat4 proj;
uniform vec3 world_pos;
uniform vec3 right;
uniform vec3 up;
uniform vec2 size;
out vec2 v_uv;
void main() {
    vec3 wp = world_pos + right * (in_local.x * size.x) + up * (in_local.y * size.y);
    gl_Position = proj * view * vec4(wp, 1.0);
    v_uv = in_uv;
}
"""

BILLBOARD_FRAGMENT_SHADER = """
#version 330
uniform sampler2D tex0;
in vec2 v_uv;
out vec4 frag_color;
void main() {
    frag_color = texture(tex0, v_uv);
}
"""


def _organic_flicker(t, phase=0.0):
    x = t + phase
    n = (math.sin(x * 9.1) + math.sin(x * 13.7 + 1.3) * 0.55
         + math.sin(x * 21.3 + 3.1) * 0.30 + math.sin(x * 34.9 + 0.4) * 0.18)
    return n / 2.03


def _quad(verts, p0, p1, p2, p3, normal, color, uv_scale=(1.0, 1.0), uv_offset=(0.0, 0.0)):
    n = normal
    c = color
    ou, ov = uv_offset
    uvs = [(ou, ov), (ou + uv_scale[0], ov), (ou + uv_scale[0], ov + uv_scale[1]), (ou, ov + uv_scale[1])]
    pts = [p0, p1, p2, p3]
    nx = ny = nz = 0.0
    for k in range(4):
        a, b = pts[k], pts[(k + 1) % 4]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    order = (0, 1, 2, 0, 2, 3) if nx * n[0] + ny * n[1] + nz * n[2] >= 0.0 else (0, 2, 1, 0, 3, 2)
    for i in order:
        p, uv = pts[i], uvs[i]
        verts.extend([p[0], p[1], p[2], n[0], n[1], n[2], uv[0], uv[1], c[0], c[1], c[2]])


def build_box_mesh(include_bottom=False):
    v = []
    x0, x1, y0, y1, z0, z1 = -0.5, 0.5, -0.5, 0.5, 0.0, 1.0
    w = (1, 1, 1)
    _quad(v, (x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1), (1, 0, 0), w)
    _quad(v, (x0, y1, z0), (x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (-1, 0, 0), w)
    _quad(v, (x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (0, 1, 0), w)
    _quad(v, (x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1), (0, -1, 0), w)
    _quad(v, (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (0, 0, 1), w)
    if include_bottom:
        _quad(v, (x1, y0, z0), (x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (0, 0, -1), w)
    return np.array(v, dtype="f4")


def build_locker_mesh():
    v = []
    x0, x1, y0, y1, z0, z1 = -0.5, 0.5, -0.5, 0.5, 0.0, 1.0
    w = (1, 1, 1)
    _quad(v, (x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (0, 1, 0), w)
    _quad(v, (x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1), (0, -1, 0), w)
    _quad(v, (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (0, 0, 1), w)

    interior = (0.55, 0.52, 0.48)
    fz = z0 + 0.03
    _quad(v, (x0, y0, fz), (x1, y0, fz), (x1, y1, fz), (x0, y1, fz), (0, 0, 1), interior)
    bx = x0 + 0.04
    _quad(v, (bx, y0, z0), (bx, y1, z0), (bx, y1, z1), (bx, y0, z1), (1, 0, 0), interior)
    return np.array(v, dtype="f4")


def build_locker_door_mesh(n_bars=4, gap_frac=0.42):
    v = []
    x1, y0, y1, z0, z1 = 0.5, -0.5, 0.5, 0.0, 1.0
    w = (1, 1, 1)
    wy0, wy1 = -0.34, 0.34
    wz0, wz1 = 0.58, 0.86

    _quad(v, (x1, y0, z0), (x1, y1, z0), (x1, y1, wz0), (x1, y0, wz0), (1, 0, 0), w)
    _quad(v, (x1, y0, wz1), (x1, y1, wz1), (x1, y1, z1), (x1, y0, z1), (1, 0, 0), w)
    _quad(v, (x1, y0, wz0), (x1, wy0, wz0), (x1, wy0, wz1), (x1, y0, wz1), (1, 0, 0), w)
    _quad(v, (x1, wy1, wz0), (x1, y1, wz0), (x1, y1, wz1), (x1, wy1, wz1), (1, 0, 0), w)

    seg = (wz1 - wz0) / n_bars
    bar_h = seg * (1.0 - gap_frac)
    for i in range(n_bars):
        cz = wz0 + seg * (i + 0.5)
        bz0, bz1 = cz - bar_h / 2, cz + bar_h / 2
        _quad(v, (x1, wy0, bz0), (x1, wy1, bz0), (x1, wy1, bz1), (x1, wy0, bz1), (1, 0, 0), w)
    plate = (0.86, 0.85, 0.80)
    dark = (0.14, 0.15, 0.16)
    chrome = (0.80, 0.80, 0.80)
    _mini_box(v, x1 + 0.006, 0.0, 0.925, 0.006, 0.10, 0.022, plate, skip_back=True)
    _mini_box(v, x1 + 0.013, -0.37, 0.50, 0.013, 0.03, 0.07, chrome, skip_back=True)
    _mini_box(v, x1 + 0.024, -0.37, 0.47, 0.012, 0.018, 0.018, dark, skip_back=True)
    for i in range(4):
        _mini_box(v, x1 + 0.006, 0.0, 0.10 + i * 0.045, 0.006, 0.26, 0.009, dark, skip_back=True)
    return np.array(v, dtype="f4")


def _mini_box(v, cx, cy, cz, sx, sy, sz, color, skip_bottom=False, skip_back=False, skip=()):
    x0, x1, y0, y1, z0, z1 = cx - sx, cx + sx, cy - sy, cy + sy, cz - sz, cz + sz
    if "+x" not in skip:
        _quad(v, (x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1), (1, 0, 0), color)
    if not skip_back and "-x" not in skip:
        _quad(v, (x0, y1, z0), (x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (-1, 0, 0), color)
    if "+y" not in skip:
        _quad(v, (x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (0, 1, 0), color)
    if "-y" not in skip:
        _quad(v, (x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1), (0, -1, 0), color)
    if "+z" not in skip:
        _quad(v, (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (0, 0, 1), color)
    if not skip_bottom and "-z" not in skip:
        _quad(v, (x0, y1, z0), (x1, y1, z0), (x1, y0, z0), (x0, y0, z0), (0, 0, -1), color)


class _MetricMesh:
    _AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}

    def __init__(self, kind=None, dims=None, v=None, scale=1.0):
        if dims is None:
            d = PROP_DEFS[kind]
            dims = (d["hd"] * 2.0, d["hw"] * 2.0, d["height"])
        self.k = (scale / dims[0], scale / dims[1], scale / dims[2])
        self.v = [] if v is None else v

    def array(self):
        return np.array(self.v, dtype="f4")

    def _p(self, pt):
        k = self.k
        return (pt[0] * k[0], pt[1] * k[1], pt[2] * k[2])

    def quad(self, p0, p1, p2, p3, normal, color, uv_scale=(1.0, 1.0), uv_offset=(0.0, 0.0)):
        k = self.k
        _quad(self.v, self._p(p0), self._p(p1), self._p(p2), self._p(p3),
              (normal[0] * k[0], normal[1] * k[1], normal[2] * k[2]), color, uv_scale=uv_scale, uv_offset=uv_offset)

    def face(self, pts, color, inside):
        nx = ny = nz = 0.0
        for i in range(4):
            a, b = pts[i], pts[(i + 1) % 4]
            nx += (a[1] - b[1]) * (a[2] + b[2])
            ny += (a[2] - b[2]) * (a[0] + b[0])
            nz += (a[0] - b[0]) * (a[1] + b[1])
        cx = sum(q[0] for q in pts) / 4 - inside[0]
        cy = sum(q[1] for q in pts) / 4 - inside[1]
        cz = sum(q[2] for q in pts) / 4 - inside[2]
        if nx * cx + ny * cy + nz * cz < 0.0:
            nx, ny, nz = -nx, -ny, -nz
        self.quad(pts[0], pts[1], pts[2], pts[3], (nx, ny, nz), color)

    def box(self, cx, cy, cz, hx, hy, hz, color, skip_bottom=False, skip_back=False):
        k = self.k
        _mini_box(self.v, cx * k[0], cy * k[1], cz * k[2], hx * k[0], hy * k[1], hz * k[2], color,
                  skip_bottom=skip_bottom, skip_back=skip_back)

    @staticmethod
    def _ring(axis, c, t, r, ang):
        ca, sa = math.cos(ang) * r, math.sin(ang) * r
        if axis == "z":
            return (c[0] + ca, c[1] + sa, t)
        if axis == "y":
            return (c[0] + ca, t, c[1] + sa)
        return (t, c[0] + ca, c[1] + sa)

    def tube(self, axis, c, t0, t1, r0, r1, color, n=8, cap0=False, cap1=False, phase=0.0):
        ax = self._AXES[axis]
        slope = (r1 - r0) / (t1 - t0) if t1 != t0 else 0.0
        for i in range(n):
            a0 = phase + math.tau * i / n
            a1 = phase + math.tau * (i + 1) / n
            radial = self._ring(axis, (0.0, 0.0), 0.0, 1.0, (a0 + a1) / 2)
            normal = tuple(radial[j] - ax[j] * slope for j in range(3))
            self.quad(self._ring(axis, c, t0, r0, a0), self._ring(axis, c, t0, r0, a1),
                      self._ring(axis, c, t1, r1, a1), self._ring(axis, c, t1, r1, a0), normal, color)
        for cap, t, r, sign in ((cap0, t0, r0, -1.0), (cap1, t1, r1, 1.0)):
            if not cap or r <= 0.0:
                continue
            centre = self._ring(axis, c, t, 0.0, 0.0)
            normal = tuple(sign * a for a in ax)
            for i in range(n):
                a0 = phase + math.tau * i / n
                a1 = phase + math.tau * (i + 1) / n
                self.quad(self._ring(axis, c, t, r, a0), self._ring(axis, c, t, r, a1), centre, centre, normal, color)

    def annulus(self, axis, c, t, r_in, r_out, color, n=8, positive=True):
        ax = self._AXES[axis]
        normal = tuple((1.0 if positive else -1.0) * a for a in ax)
        for i in range(n):
            a0, a1 = math.tau * i / n, math.tau * (i + 1) / n
            self.quad(self._ring(axis, c, t, r_in, a0), self._ring(axis, c, t, r_out, a0),
                      self._ring(axis, c, t, r_out, a1), self._ring(axis, c, t, r_in, a1), normal, color)

    def annulus_z(self, c, z, r_in, r_out, color, n=8, up=True):
        self.annulus("z", c, z, r_in, r_out, color, n=n, positive=up)

    def ring(self, axis, c, t0, t1, r_in, r_out, color, n=12, phase=0.0):
        ax = self._AXES[axis]
        for i in range(n):
            a0 = phase + math.tau * i / n
            a1 = phase + math.tau * (i + 1) / n
            radial = self._ring(axis, (0.0, 0.0), 0.0, 1.0, (a0 + a1) / 2)
            out_n = radial
            in_n = tuple(-r for r in radial)
            self.quad(self._ring(axis, c, t0, r_out, a0), self._ring(axis, c, t0, r_out, a1),
                      self._ring(axis, c, t1, r_out, a1), self._ring(axis, c, t1, r_out, a0), out_n, color)
            self.quad(self._ring(axis, c, t0, r_in, a1), self._ring(axis, c, t0, r_in, a0),
                      self._ring(axis, c, t1, r_in, a0), self._ring(axis, c, t1, r_in, a1), in_n, color)
            for t, sign in ((t0, -1.0), (t1, 1.0)):
                self.quad(self._ring(axis, c, t, r_in, a0), self._ring(axis, c, t, r_out, a0),
                          self._ring(axis, c, t, r_out, a1), self._ring(axis, c, t, r_in, a1),
                          tuple(sign * a for a in ax), color)

    def spoke(self, axis, c, t, r0, r1, ang, half_w, color):
        dx, dy = math.cos(ang), math.sin(ang)
        px, py = -dy * half_w, dx * half_w
        pts2 = ((dx * r0 - px, dy * r0 - py), (dx * r1 - px, dy * r1 - py),
                (dx * r1 + px, dy * r1 + py), (dx * r0 + px, dy * r0 + py))
        pts = []
        for u, w in pts2:
            if axis == "z":
                pts.append((c[0] + u, c[1] + w, t))
            elif axis == "y":
                pts.append((c[0] + u, t, c[1] + w))
            else:
                pts.append((t, c[0] + u, c[1] + w))
        self.quad(*pts, self._AXES[axis], color)

    def hull(self, bottom, top, color, cap_bottom=False):
        inside = tuple(sum(q[j] for q in bottom + top) / 8.0 for j in range(3))
        for i in range(4):
            j = (i + 1) % 4
            self.face((bottom[i], bottom[j], top[j], top[i]), color, inside)
        self.face(tuple(top), color, inside)
        if cap_bottom:
            self.face(tuple(bottom), color, inside)

    def taper_x(self, x0, x1, sec0, sec1, color, cap0=False, cap1=False):
        (w0, b0, h0), (w1, b1, h1) = sec0, sec1
        c0 = [(x0, -w0, b0), (x0, w0, b0), (x0, w0, h0), (x0, -w0, h0)]
        c1 = [(x1, -w1, b1), (x1, w1, b1), (x1, w1, h1), (x1, -w1, h1)]
        inside = ((x0 + x1) / 2, 0.0, (b0 + h0 + b1 + h1) / 4)
        for i in range(4):
            j = (i + 1) % 4
            self.face((c0[i], c0[j], c1[j], c1[i]), color, inside)
        if cap0:
            self.face(c0, color, inside)
        if cap1:
            self.face(c1, color, inside)


def build_door_mesh():
    v = []
    x0, x1, y0, y1, z0, z1 = -0.5, 0.5, -0.5, 0.5, 0.0, 1.0
    w = (1, 1, 1)
    dark = (0.5, 0.44, 0.35)
    _quad(v, (x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (0, 1, 0), w)
    _quad(v, (x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1), (0, -1, 0), w)
    _quad(v, (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (0, 0, 1), w)

    def panel_face(fx, order):
        def q(ya, yb, za, zb, col):
            uv_sc = (yb - ya, zb - za)
            if order > 0:
                uv_off = (ya - y0, za - z0)
                _quad(v, (fx, ya, za), (fx, yb, za), (fx, yb, zb), (fx, ya, zb), (order, 0, 0), col,
                      uv_scale=uv_sc, uv_offset=uv_off)
            else:
                uv_off = (y1 - yb, za - z0)
                _quad(v, (fx, yb, za), (fx, ya, za), (fx, ya, zb), (fx, yb, zb), (order, 0, 0), col,
                      uv_scale=uv_sc, uv_offset=uv_off)
        q(y0, y1, z0, 0.07, w)
        q(y0, y1, 0.93, z1, w)
        q(y0, y0 + 0.06, 0.07, 0.93, w)
        q(y1 - 0.06, y1, 0.07, 0.93, w)
        q(y0 + 0.06, y1 - 0.06, 0.47, 0.53, w)
        q(y0 + 0.06, y1 - 0.06, 0.07, 0.47, dark)
        q(y0 + 0.06, y1 - 0.06, 0.53, 0.93, dark)

    panel_face(x1, 1)
    panel_face(x0, -1)

    trim = (0.62, 0.54, 0.40)
    steel = (0.60, 0.61, 0.63)
    for fx, sgn in ((x1, 1.0), (x0, -1.0)):
        for pz0, pz1 in ((0.09, 0.45), (0.55, 0.91)):
            for yc in (y0 + 0.14, y1 - 0.14):
                _mini_box(v, fx + sgn * 0.015, yc, (pz0 + pz1) / 2, 0.015, 0.01, (pz1 - pz0) / 2, trim, skip_back=sgn > 0)
            for zc in (pz0 + 0.009, pz1 - 0.009):
                _mini_box(v, fx + sgn * 0.015, 0.0, zc, 0.015, (y1 - y0) / 2 - 0.15, 0.009, trim, skip_back=sgn > 0)
        _mini_box(v, fx + sgn * 0.012, 0.0, 0.034, 0.012, (y1 - y0) / 2 - 0.07, 0.028, steel, skip_back=sgn > 0)
    brass = (0.8, 0.68, 0.32)
    dims = (0.14, 0.97, S.WALL_HEIGHT - 0.02)
    m = _MetricMesh(dims=dims, v=v)
    ky, kz = (y0 + 0.1) * dims[1], 0.55 * dims[2]
    for sgn in (1.0, -1.0):
        face = sgn * dims[0] / 2
        pts = sorted((face, face + sgn * 0.009))
        m.tube("x", (ky, kz), pts[0], pts[1], 0.028, 0.028, brass, n=10, cap0=sgn < 0, cap1=sgn > 0)
        pts = sorted((face + sgn * 0.009, face + sgn * 0.022))
        m.tube("x", (ky, kz), pts[0], pts[1], 0.009, 0.009, brass, n=8)
        pts = sorted((face + sgn * 0.022, face + sgn * 0.044))
        m.tube("x", (ky, kz), pts[0], pts[1], 0.02, 0.02, brass, n=10, cap0=True, cap1=True)

    keyhole = (0.02, 0.02, 0.02)
    khy = S.DOOR_KEYHOLE_LOCAL_Y
    khz = S.DOOR_KEYHOLE_LOCAL_Z
    _mini_box(v, x1 + 0.012, khy, khz + 0.02, 0.012, 0.018, 0.018, keyhole)
    _mini_box(v, x1 + 0.012, khy, khz - 0.025, 0.012, 0.009, 0.014, keyhole)
    _mini_box(v, x0 - 0.012, khy, khz + 0.02, 0.012, 0.018, 0.018, keyhole)
    _mini_box(v, x0 - 0.012, khy, khz - 0.025, 0.012, 0.009, 0.014, keyhole)
    return np.array(v, dtype="f4")


_DOOR_BREAK_WARP = 0.05


def build_broken_door_mesh():
    hw, hd, h = DOOR_LEAF
    m = _MetricMesh(dims=(hd * 2.0, hw * 2.0, h))
    w = (1, 1, 1)
    dark = (0.5, 0.44, 0.35)
    raw = (1.18, 1.05, 0.88)
    steel = (0.55, 0.56, 0.58)
    stile = 0.10
    panel_d = hd - 0.022
    rail_b, rail_m0, rail_m1, rail_t = 0.10, 0.50, 0.60, 1.02
    inner = hw - stile
    hole_gap = (-0.05, 0.12)

    m.box(0.0, hw - stile / 2, h / 2, hd, stile / 2, h / 2, w)
    m.box(0.0, -(hw - stile / 2), h / 2, hd, stile / 2, h / 2, w)
    m.box(0.0, 0.0, rail_b / 2, hd, inner, rail_b / 2, w)
    m.box(0.0, 0.0, (rail_t + h) / 2, hd, inner, (h - rail_t) / 2, w)
    for y0, y1 in ((-inner, hole_gap[0]), (hole_gap[1], inner)):
        m.box(0.0, (y0 + y1) / 2, (rail_m0 + rail_m1) / 2, hd, (y1 - y0) / 2,
              (rail_m1 - rail_m0) / 2, w)
    m.box(0.0, 0.0, (rail_b + rail_m0) / 2, panel_d, inner, (rail_m0 - rail_b) / 2, dark)

    def splinter_z(y, half, base_z, tip_z, tip_dy, tip_dx=0.0):
        b = [(-hd, y - half, base_z), (hd, y - half, base_z),
             (hd, y + half, base_z), (-hd, y + half, base_z)]
        t = [(-hd * 0.35 + tip_dx, y + tip_dy - 0.005, tip_z), (hd * 0.35 + tip_dx, y + tip_dy - 0.005, tip_z),
             (hd * 0.35 + tip_dx, y + tip_dy + 0.005, tip_z), (-hd * 0.35 + tip_dx, y + tip_dy + 0.005, tip_z)]
        m.hull(b, t, raw)

    def splinter_y(z, half, base_y, tip_y, tip_dz, tip_dx=0.0):
        b = [(-hd, base_y, z - half), (hd, base_y, z - half),
             (hd, base_y, z + half), (-hd, base_y, z + half)]
        t = [(-hd * 0.35 + tip_dx, tip_y, z + tip_dz - 0.005), (hd * 0.35 + tip_dx, tip_y, z + tip_dz - 0.005),
             (hd * 0.35 + tip_dx, tip_y, z + tip_dz + 0.005), (-hd * 0.35 + tip_dx, tip_y, z + tip_dz + 0.005)]
        m.hull(b, t, raw)

    for y, half, up, dy, dx in ((-0.30, 0.055, 0.115, 0.02, 0.012), (-0.13, 0.04, 0.055, -0.015, -0.01),
                                (0.06, 0.05, 0.155, 0.03, 0.02), (0.235, 0.045, 0.075, -0.02, -0.014)):
        splinter_z(y, half, rail_m1, rail_m1 + up, dy, dx)
    for y, half, down, dy, dx in ((-0.24, 0.05, 0.10, -0.025, -0.016), (0.0, 0.042, 0.055, 0.02, 0.01),
                                  (0.21, 0.055, 0.135, 0.015, -0.018)):
        splinter_z(y, half, rail_t, rail_t - down, dy, dx)
    for z, half, reach, dz, dx in ((0.72, 0.06, 0.075, 0.03, 0.018), (0.90, 0.045, 0.045, -0.02, -0.012)):
        splinter_y(z, half, -inner, -inner + reach, dz, dx)
    for z, half, reach, dz, dx in ((0.78, 0.055, 0.06, -0.025, -0.02),):
        splinter_y(z, half, inner, inner - reach, dz, dx)
    splinter_z(hole_gap[0] - 0.015, 0.03, rail_m1, rail_m0 + 0.02, 0.05, 0.02)
    splinter_z(hole_gap[1] + 0.015, 0.03, rail_m1, rail_m0 + 0.03, -0.05, -0.016)
    m.hull([(-0.012, 0.055, rail_m1), (0.02, 0.055, rail_m1), (0.02, 0.21, rail_m1), (-0.012, 0.21, rail_m1)],
           [(-0.05, 0.10, rail_m1 + 0.20), (-0.018, 0.10, rail_m1 + 0.20),
            (-0.018, 0.235, rail_m1 + 0.185), (-0.05, 0.235, rail_m1 + 0.185)], raw)

    for z in (0.94, 0.18):
        torn = z < 0.5
        m.box(0.0, hw - 0.010, z, hd * 0.55, 0.014, 0.045, steel)
        if torn:
            m.hull([(hd * 0.4, hw - 0.004, z - 0.04), (hd * 0.62, hw - 0.004, z - 0.03),
                    (hd * 0.62, hw + 0.03, z - 0.045), (hd * 0.4, hw + 0.03, z - 0.05)],
                   [(hd * 0.4, hw - 0.004, z + 0.04), (hd * 0.62, hw - 0.004, z + 0.05),
                    (hd * 0.62, hw + 0.03, z + 0.035), (hd * 0.4, hw + 0.03, z + 0.03)], steel)
        else:
            m.tube("z", (hd * 0.5, hw + 0.006), z - 0.05, z + 0.05, 0.011, 0.011, steel, n=6)
    m.box(0.0, -(hw - 0.006), 0.56, hd * 0.6, 0.010, 0.06, steel)
    m.hull([(-0.02, -(hw + 0.002), 0.545), (0.02, -(hw + 0.002), 0.545),
            (0.02, -(hw + 0.03), 0.55), (-0.02, -(hw + 0.03), 0.55)],
           [(-0.02, -(hw + 0.002), 0.575), (0.02, -(hw + 0.002), 0.575),
            (0.02, -(hw + 0.03), 0.585), (-0.02, -(hw + 0.03), 0.585)], steel)

    v = m.v
    for i in range(0, len(v), _WALL_VERTEX_FLOATS):
        v[i] += _DOOR_BREAK_WARP * (v[i + 1] + 0.5) * (0.35 + v[i + 2])
    return m.array()


def build_tree_mesh():
    v = []
    trunk_col = (0.40, 0.27, 0.16)
    canopy_col = (0.15, 0.33, 0.13)
    tw = 0.16
    tx0, tx1, ty0, ty1, tz0, tz1 = -tw, tw, -tw, tw, 0.0, 0.5
    _quad(v, (tx1, ty0, tz0), (tx1, ty1, tz0), (tx1, ty1, tz1), (tx1, ty0, tz1), (1, 0, 0), trunk_col)
    _quad(v, (tx0, ty1, tz0), (tx0, ty0, tz0), (tx0, ty0, tz1), (tx0, ty1, tz1), (-1, 0, 0), trunk_col)
    _quad(v, (tx1, ty1, tz0), (tx0, ty1, tz0), (tx0, ty1, tz1), (tx1, ty1, tz1), (0, 1, 0), trunk_col)
    _quad(v, (tx0, ty0, tz0), (tx1, ty0, tz0), (tx1, ty0, tz1), (tx0, ty0, tz1), (0, -1, 0), trunk_col)

    cx0, cx1, cy0, cy1, cz0, cz1 = -0.5, 0.5, -0.5, 0.5, 0.4, 1.0
    _quad(v, (cx1, cy0, cz0), (cx1, cy1, cz0), (cx1, cy1, cz1), (cx1, cy0, cz1), (1, 0, 0), canopy_col)
    _quad(v, (cx0, cy1, cz0), (cx0, cy0, cz0), (cx0, cy0, cz1), (cx0, cy1, cz1), (-1, 0, 0), canopy_col)
    _quad(v, (cx1, cy1, cz0), (cx0, cy1, cz0), (cx0, cy1, cz1), (cx1, cy1, cz1), (0, 1, 0), canopy_col)
    _quad(v, (cx0, cy0, cz0), (cx1, cy0, cz0), (cx1, cy0, cz1), (cx0, cy0, cz1), (0, -1, 0), canopy_col)
    _quad(v, (cx0, cy0, cz1), (cx1, cy0, cz1), (cx1, cy1, cz1), (cx0, cy1, cz1), (0, 0, 1), canopy_col)
    sz = 0.44
    _quad(v, (cx0, cy0, sz), (cx1, cy0, sz), (cx1, cy1, sz), (cx0, cy1, sz), (0, 0, -1), canopy_col)
    return np.array(v, dtype="f4")


def build_tree_spruce_mesh():
    v = []
    trunk = (0.34, 0.23, 0.14)
    _mini_box(v, 0.0, 0.0, 0.15, 0.14, 0.14, 0.15, trunk, skip_bottom=True)
    tiers = (
        (0.36, 0.15, 0.88, (0.10, 0.24, 0.12)),
        (0.60, 0.12, 0.64, (0.12, 0.27, 0.13)),
        (0.80, 0.10, 0.42, (0.11, 0.25, 0.12)),
        (0.94, 0.06, 0.18, (0.13, 0.29, 0.14)),
    )
    for cz, half_h, half_w, col in tiers:
        _mini_box(v, 0.0, 0.0, cz, half_w, half_w, half_h, col)
    return np.array(v, dtype="f4")


def build_tree_broadleaf_mesh():
    v = []
    trunk = (0.38, 0.26, 0.15)
    _mini_box(v, 0.0, 0.0, 0.29, 0.18, 0.18, 0.29, trunk, skip_bottom=True)
    lumps = (
        (0.00, 0.00, 0.70, 0.86, 0.86, 0.16, (0.17, 0.33, 0.12)),
        (0.84, 0.14, 0.66, 0.46, 0.50, 0.11, (0.15, 0.30, 0.11)),
        (-0.80, -0.24, 0.72, 0.46, 0.52, 0.10, (0.19, 0.36, 0.13)),
        (0.12, 0.84, 0.645, 0.50, 0.42, 0.10, (0.16, 0.31, 0.12)),
        (-0.16, -0.80, 0.625, 0.44, 0.40, 0.09, (0.15, 0.29, 0.11)),
        (-0.06, 0.08, 0.90, 0.56, 0.52, 0.06, (0.20, 0.37, 0.14)),
    )
    for cx, cy, cz, sx, sy, sz, col in lumps:
        _mini_box(v, cx, cy, cz, sx, sy, sz, col)
    return np.array(v, dtype="f4")


def build_tree_dead_mesh():
    v = []
    bark = (0.30, 0.25, 0.20)
    bark_dark = (0.23, 0.19, 0.15)
    _mini_box(v, 0.0, 0.0, 0.225, 0.20, 0.20, 0.225, bark, skip_bottom=True)
    _mini_box(v, 0.0, 0.0, 0.65, 0.14, 0.14, 0.20, bark)
    _mini_box(v, 0.0, 0.0, 0.925, 0.09, 0.09, 0.075, bark_dark)
    branches = (
        (True, 1.0, 0.46, 1.05, 0.17),
        (False, -1.0, 0.58, 0.95, 0.18),
        (True, -1.0, 0.70, 0.85, 0.16),
        (False, 1.0, 0.52, 0.72, 0.12),
        (True, 1.0, 0.82, 0.56, 0.12),
        (False, -1.0, 0.86, 0.42, 0.10),
    )
    th, steps = 0.055, 7
    for along_x, sign, z, reach, rise in branches:
        def box(d, half_d, cz, half_z):
            if along_x:
                _mini_box(v, sign * d, 0.0, cz, half_d, th, half_z, bark_dark)
            else:
                _mini_box(v, 0.0, sign * d, cz, th, half_d, half_z, bark_dark)
        run, lift = reach / steps, rise / steps
        for i in range(steps):
            box(0.08 + (i + 0.5) * run, run / 2 + 0.01, z + (i + 0.5) * lift, lift / 2 + 0.007)
        box(0.08 + reach - run / 2, th * 0.8, z + rise + 0.035, 0.035)
    return np.array(v, dtype="f4")


TREE_MESH_BUILDERS = (build_tree_mesh, build_tree_spruce_mesh, build_tree_broadleaf_mesh, build_tree_dead_mesh)


def _leg_set(v, hx, hy, leg_h, top_h, leg_th, leg_color, top_color):
    top_z0, top_z1 = leg_h, leg_h + top_h
    _quad(v, (hx, -hy, top_z0), (hx, hy, top_z0), (hx, hy, top_z1), (hx, -hy, top_z1), (1, 0, 0), top_color)
    _quad(v, (-hx, hy, top_z0), (-hx, -hy, top_z0), (-hx, -hy, top_z1), (-hx, hy, top_z1), (-1, 0, 0), top_color)
    _quad(v, (hx, hy, top_z0), (-hx, hy, top_z0), (-hx, hy, top_z1), (hx, hy, top_z1), (0, 1, 0), top_color)
    _quad(v, (-hx, -hy, top_z0), (hx, -hy, top_z0), (hx, -hy, top_z1), (-hx, -hy, top_z1), (0, -1, 0), top_color)
    _quad(v, (-hx, -hy, top_z1), (hx, -hy, top_z1), (hx, hy, top_z1), (-hx, hy, top_z1), (0, 0, 1), top_color)
    _quad(v, (-hx, hy, top_z0), (hx, hy, top_z0), (hx, -hy, top_z0), (-hx, -hy, top_z0), (0, 0, -1), top_color)
    lx, ly = hx - leg_th, hy - leg_th
    for sx in (-1, 1):
        for sy in (-1, 1):
            _mini_box(v, sx * lx, sy * ly, leg_h / 2, leg_th, leg_th, leg_h / 2, leg_color, skip_bottom=True)
    return top_z1


def _ring_of_boxes(v, cx, cy, cz, radius, n, box_size, color):
    for i in range(n):
        ang = math.tau * i / n
        _mini_box(v, cx + radius * math.cos(ang), cy + radius * math.sin(ang), cz, box_size, box_size, box_size, color)


def build_bed_mesh():
    m = _MetricMesh("bed")
    steel = (0.56, 0.57, 0.60)
    dark = (0.16, 0.16, 0.17)
    mattress = (0.80, 0.80, 0.77)
    pillow = (0.93, 0.92, 0.89)
    blanket = (0.46, 0.52, 0.60)
    d = PROP_DEFS["bed"]
    hx, hy, h = d["hd"], d["hw"], d["height"]
    lx, ly = hx - 0.035, hy - 0.022
    deck = 0.15
    for sx in (-1, 1):
        for sy in (-1, 1):
            m.box(sx * lx, sy * ly, 0.013, 0.008, 0.016, 0.013, dark)
            m.tube("z", (sx * lx, sy * ly), 0.024, deck + 0.004, 0.009, 0.009, steel, n=6)
        m.box(sx * (hx - 0.022), 0.0, deck - 0.004, 0.010, hy - 0.006, 0.012, steel)
    m.box(0.0, 0.0, deck, hx - 0.032, ly, 0.006, steel)
    m.box(0.0, 0.0, deck + 0.04, hx - 0.03, hy - 0.035, 0.035, mattress)
    top = deck + 0.075
    m.box(0.0, -hy + 0.115, top + 0.012, hx - 0.075, 0.055, 0.014, pillow)
    m.box(0.0, 0.10, top + 0.003, hx - 0.025, 0.25, 0.005, blanket)
    m.box(0.0, hy - 0.045, top + 0.007, hx - 0.025, 0.018, 0.012, blanket)
    for sy in (-1, 1):
        for sx in (-1, 1):
            m.tube("z", (sx * lx, sy * ly), 0.024, (h if sy < 0 else 0.30) - 0.01, 0.011, 0.011, steel, n=6)
    m.box(0.0, -hy + 0.008, (h + deck) / 2, hx - 0.005, 0.008, (h - deck) / 2, steel, skip_bottom=True)
    foot_h = 0.30
    m.box(0.0, hy - 0.008, (foot_h + deck) / 2, hx - 0.005, 0.008, (foot_h - deck) / 2, steel, skip_bottom=True)
    for x in (-0.10, 0.0, 0.10):
        m.box(x, hy + 0.0015, foot_h - 0.06, 0.012, 0.0025, 0.035, dark)
    return m.array()


def build_desk_mesh():
    m = _MetricMesh("desk")
    wood = (0.55, 0.40, 0.24)
    dark = (0.40, 0.29, 0.17)
    handle = (0.72, 0.68, 0.58)
    d = PROP_DEFS["desk"]
    h, hx, hy = d["height"], d["hd"], d["hw"]
    t = 0.02
    under = h - t + 0.002
    m.box(0.0, 0.0, h - t / 2, hx, hy, t / 2, wood)
    px, py = hx - 0.006, 0.095
    pcy = -hy + 0.006 + py
    m.box(0.0, pcy, under / 2, px, py, under / 2, dark, skip_bottom=True)
    dh = (h - t - 0.03) / 3
    for i in range(3):
        z = 0.02 + dh * (i + 0.5)
        m.box(-px - 0.002, pcy, z, 0.004, py - 0.012, dh / 2 - 0.006, wood)
        m.box(-px - 0.007, pcy, z + dh * 0.2, 0.003, 0.028, 0.004, handle)
    for sx in (-1, 1):
        m.box(sx * (hx - 0.03), hy - 0.03, under / 2, 0.012, 0.012, under / 2, dark, skip_bottom=True)
    y0, y1 = pcy + py - 0.003, hy - 0.03 + 0.003
    m.box(hx - 0.03, (y0 + y1) / 2, h - t - 0.07, 0.006, (y1 - y0) / 2, 0.07 + 0.002, dark)
    return m.array()


def build_table_mesh():
    m = _MetricMesh("table")
    top_c = (0.66, 0.50, 0.32)
    wood = (0.56, 0.42, 0.26)
    dark = (0.46, 0.34, 0.21)
    h = PROP_DEFS["table"]["height"]
    hx = PROP_DEFS["table"]["hd"]
    t = 0.022
    m.box(0.0, 0.0, h - t / 2, hx, hx, t / 2, top_c)
    leg_in, leg = hx - 0.05, 0.020
    apron_h = 0.045
    for sx in (-1, 1):
        m.box(sx * (leg_in - 0.004), 0.0, h - t - apron_h / 2, 0.0075, leg_in - leg, apron_h / 2, dark)
        m.box(0.0, sx * (leg_in - 0.004), h - t - apron_h / 2, leg_in - leg, 0.0075, apron_h / 2, dark)
    for sx in (-1, 1):
        for sy in (-1, 1):
            cx, cy = sx * leg_in, sy * leg_in
            m.hull(((cx - 0.013, cy - 0.013, 0.0), (cx + 0.013, cy - 0.013, 0.0), (cx + 0.013, cy + 0.013, 0.0),
                    (cx - 0.013, cy + 0.013, 0.0)),
                   ((cx - leg, cy - leg, h - t), (cx + leg, cy - leg, h - t), (cx + leg, cy + leg, h - t),
                    (cx - leg, cy + leg, h - t)), wood)
    for sx in (-1, 1):
        m.box(sx * leg_in, 0.0, 0.05, 0.008, leg_in - 0.008, 0.008, dark)
    return m.array()


def build_chair_mesh():
    m = _MetricMesh("chair")
    wood = (0.50, 0.36, 0.22)
    back = (0.42, 0.30, 0.18)
    d = PROP_DEFS["chair"]
    h = d["height"]
    seat_z, t = 0.22, 0.016
    a = d["hw"] - 0.028
    leg = 0.011
    m.box(0.005, 0.0, seat_z + t / 2, d["hd"] - 0.015, d["hw"] - 0.015, t / 2, wood)
    m.box(a + 0.001, 0.0, seat_z - 0.014, 0.006, a - leg + 0.003, 0.014, back)
    lean = 0.022
    for sy in (-1, 1):
        m.box(a, sy * a, seat_z / 2, leg, leg, seat_z / 2, wood, skip_bottom=True)
        m.hull(((-a - leg, sy * a - leg, 0.0), (-a + leg, sy * a - leg, 0.0),
                (-a + leg, sy * a + leg, 0.0), (-a - leg, sy * a + leg, 0.0)),
               ((-a - lean - leg, sy * a - leg, h), (-a - lean + leg, sy * a - leg, h),
                (-a - lean + leg, sy * a + leg, h), (-a - lean - leg, sy * a + leg, h)), back)
        m.box(0.0, sy * a, 0.04, a - leg + 0.003, 0.005, 0.005, back)
    for z, hz in ((0.30, 0.016), (0.355, 0.016), (h - 0.018, 0.018)):
        m.box(-a - lean * z / h, 0.0, z, 0.005, a - leg + 0.003, hz, back)
    return m.array()


def build_gurney_mesh():
    m = _MetricMesh("gurney")
    chrome = (0.72, 0.73, 0.76)
    pad = (0.26, 0.40, 0.46)
    dark = (0.16, 0.16, 0.17)
    tyre = (0.08, 0.08, 0.09)
    h = PROP_DEFS["gurney"]["height"]
    m.box(0.0, 0.0, h - 0.035, 0.175, 0.405, 0.035, pad)
    fz = h - 0.08
    for sx in (-1, 1):
        m.box(sx * 0.185, 0.0, fz, 0.01, 0.42, 0.01, chrome)
        m.tube("y", (sx * 0.20, h - 0.045), -0.36, 0.36, 0.007, 0.007, chrome, n=6, cap0=True, cap1=True)
        for sy in (-0.3, 0.0, 0.3):
            m.box(sx * 0.192, sy, (fz + h - 0.045) / 2 + 0.005, 0.006, 0.006, (h - 0.045 - fz) / 2 - 0.005, chrome)
    for sy in (-1, 1):
        m.box(0.0, sy * 0.41, fz, 0.175, 0.01, 0.01, chrome)
    for sy in (-0.22, 0.22):
        m.box(0.0, sy, fz, 0.175, 0.012, 0.01, chrome)
        m.tube("z", (0.0, sy), 0.11, fz - 0.01, 0.022, 0.022, chrome, n=8)
    bz = 0.10
    for sx in (-1, 1):
        m.box(sx * 0.12, 0.0, bz, 0.012, 0.36, 0.012, chrome)
    for sy in (-1, 1):
        m.box(0.0, sy * 0.36, bz, 0.132, 0.012, 0.012, chrome)
    m.box(0.0, 0.0, bz + 0.016, 0.108, 0.30, 0.004, dark)
    for sx in (-1, 1):
        for sy in (-1, 1):
            cx, cy = sx * 0.12, sy * 0.36
            m.box(cx, cy, 0.068, 0.006, 0.02, 0.02, chrome)
            m.ring("x", (cy, 0.035), cx - 0.012, cx + 0.012, 0.0, 0.035, tyre, n=10)
    for sx in (-1, 1):
        m.tube("z", (sx * 0.15, 0.43), fz - 0.01, h + 0.09, 0.009, 0.009, chrome, n=6)
    m.tube("x", (0.43, h + 0.09), -0.159, 0.159, 0.009, 0.009, chrome, n=6, cap0=True, cap1=True)
    return m.array()


def build_shelf_mesh():
    v = []
    frame = (0.42, 0.32, 0.20)
    board = (0.55, 0.42, 0.26)
    back = (0.30, 0.22, 0.14)
    for sy in (-1, 1):
        _mini_box(v, 0.0, sy * 0.47, 0.5, 0.46, 0.03, 0.5, frame, skip_bottom=True)
    _mini_box(v, -0.46, 0.0, 0.5, 0.02, 0.44, 0.5, back)
    for top in (0.085, 0.425, 0.765):
        _mini_box(v, 0.0, 0.0, top - 0.025, 0.46, 0.44, 0.025, board)
        _mini_box(v, 0.45, 0.0, top - 0.062, 0.012, 0.44, 0.012, frame)
    return np.array(v, dtype="f4")


def build_cabinet_mesh():
    m = _MetricMesh("cabinet")
    body = (0.64, 0.67, 0.64)
    door = (0.70, 0.73, 0.70)
    dark = (0.16, 0.16, 0.17)
    handle = (0.80, 0.80, 0.78)
    h = PROP_DEFS["cabinet"]["height"]
    wall, front, hy = -0.18, 0.16, 0.30
    m.box((wall + front) / 2, 0.0, (0.035 + h - 0.02) / 2, (front - wall) / 2, hy - 0.004,
          (h - 0.02 - 0.035) / 2, body, skip_back=True)
    m.box((wall + front - 0.03) / 2, 0.0, 0.0175, (front - 0.03 - wall) / 2, hy - 0.02, 0.0175, dark, skip_back=True)
    m.box((wall + front + 0.022) / 2, 0.0, h - 0.01, (front + 0.022 - wall) / 2, hy, 0.01, body, skip_back=True)
    dz0, dz1 = 0.045, h - 0.035
    for sy in (-1, 1):
        y0, y1 = sy * 0.004, sy * (hy - 0.012)
        cy, ay = (y0 + y1) / 2, abs(y1 - y0) / 2
        m.box(front + 0.009, cy, (dz0 + dz1) / 2, 0.009, ay, (dz1 - dz0) / 2, door)
        m.box(front + 0.0205, cy, 0.25, 0.0025, ay - 0.035, 0.17, body)
        for k in range(4):
            m.box(front + 0.0205, cy, 0.48 + k * 0.022, 0.0025, ay - 0.05, 0.005, dark)
        hyh = sy * 0.035
        m.box(front + 0.03, hyh, 0.30, 0.004, 0.006, 0.06, handle)
        for z in (0.25, 0.35):
            m.box(front + 0.022, hyh, z, 0.004, 0.004, 0.004, handle)
    return m.array()


def build_mortuary_wall_mesh():
    m = _MetricMesh("mortuary_wall")
    steel = (0.70, 0.71, 0.73)
    panel = (0.78, 0.79, 0.81)
    dark = (0.16, 0.16, 0.17)
    chrome = (0.84, 0.84, 0.86)
    card = (0.90, 0.88, 0.80)
    h = PROP_DEFS["mortuary_wall"]["height"]
    wall, frame_x, hy = -0.14, -0.10, 0.48
    m.box((wall + frame_x) / 2, 0.0, h / 2, (frame_x - wall) / 2, hy, h / 2, steel, skip_back=True)
    m.box(frame_x + 0.003, 0.0, 0.03, 0.003, hy - 0.01, 0.03, dark)
    for k in range(6):
        m.box(frame_x + 0.003, -0.25 + k * 0.1, h - 0.045, 0.003, 0.04, 0.006, dark)
    dx0 = frame_x
    for cy in (-0.235, 0.235):
        for cz in (0.26, 0.60):
            dw, dh = 0.21, 0.155
            m.box(dx0 + 0.012, cy, cz, 0.012, dw, dh, steel)
            m.box(dx0 + 0.027, cy, cz, 0.003, dw - 0.03, dh - 0.03, panel)
            side = 1.0 if cy > 0 else -1.0
            for zz in (cz - dh + 0.03, cz + dh - 0.03):
                m.tube("z", (dx0 + 0.026, cy + side * (dw + 0.004)), zz - 0.018, zz + 0.018, 0.008, 0.008, chrome, n=6,
                       cap0=True, cap1=True)
            ly = cy - side * (dw - 0.045)
            m.box(dx0 + 0.036, ly, cz, 0.012, 0.012, 0.012, dark)
            m.box(dx0 + 0.052, ly, cz - 0.04, 0.006, 0.01, 0.055, chrome)
            m.box(dx0 + 0.0315, cy, cz + dh - 0.028, 0.0015, 0.05, 0.016, card)
    return m.array()


def build_autopsy_table_mesh():
    m = _MetricMesh("autopsy_table")
    steel = (0.70, 0.71, 0.73)
    shade = (0.56, 0.57, 0.60)
    hole = (0.08, 0.08, 0.09)
    h = PROP_DEFS["autopsy_table"]["height"]
    hx, hy = PROP_DEFS["autopsy_table"]["hd"], PROP_DEFS["autopsy_table"]["hw"]
    tray_z, rim = h - 0.018, 0.012
    m.box(0.0, 0.0, tray_z - 0.012, hx, hy, 0.012, steel)
    for sx in (-1, 1):
        m.box(sx * (hx - rim / 2), 0.0, tray_z + 0.009, rim / 2, hy, 0.009, steel)
        m.box(0.0, sx * (hy - rim / 2), tray_z + 0.009, hx - rim, rim / 2, 0.009, steel)
    for k in range(5):
        m.box(0.0, hy - 0.08 - k * 0.035, tray_z + 0.002, 0.008, 0.008, 0.002, hole)
    m.box(0.0, 0.0, tray_z - 0.024 - 0.012, hx - 0.05, hy - 0.08, 0.012, shade)
    ped_top = tray_z - 0.048
    m.box(0.0, 0.0, (ped_top + 0.02) / 2, 0.06, 0.08, (ped_top - 0.02) / 2, shade, skip_bottom=True)
    m.box(0.0, 0.0, 0.01, hx - 0.06, 0.20, 0.01, shade, skip_bottom=True)
    m.tube("z", (0.0, hy - 0.10), tray_z - 0.13, tray_z - 0.024, 0.012, 0.04, steel, n=10)
    m.tube("z", (0.0, hy - 0.10), 0.02, tray_z - 0.13, 0.008, 0.008, shade, n=6)
    return m.array()


def build_instrument_tray_mesh():
    m = _MetricMesh("instrument_tray")
    steel = (0.66, 0.67, 0.70)
    rim = (0.52, 0.53, 0.56)
    tool = (0.84, 0.84, 0.86)
    handle = (0.30, 0.32, 0.34)
    hx, hy, base = 0.075, 0.115, 0.004
    m.box(0.0, 0.0, base / 2, hx, hy, base / 2, steel, skip_bottom=True)
    for sx in (-1, 1):
        m.box(sx * (hx - 0.003), 0.0, base + 0.006, 0.003, hy, 0.006, rim)
        m.box(0.0, sx * (hy - 0.003), base + 0.006, hx - 0.006, 0.003, 0.006, rim)
    z = base
    m.box(-0.04, -0.01, z + 0.0015, 0.004, 0.05, 0.0015, handle)
    m.hull(((-0.043, 0.04, z), (-0.037, 0.04, z), (-0.038, 0.07, z), (-0.041, 0.072, z)),
           ((-0.043, 0.04, z + 0.002), (-0.037, 0.04, z + 0.002), (-0.038, 0.07, z + 0.002), (-0.041, 0.072, z + 0.002)), tool)
    for sx in (-1, 1):
        m.ring("z", (0.005 + sx * 0.012, -0.06), z, z + 0.003, 0.005, 0.0085, tool, n=8)
        m.hull(((0.005 + sx * 0.012 - 0.002, -0.052, z), (0.005 + sx * 0.012 + 0.002, -0.052, z),
                (0.005 - sx * 0.006 + 0.0015, 0.03, z), (0.005 - sx * 0.006 - 0.0015, 0.03, z)),
               ((0.005 + sx * 0.012 - 0.002, -0.052, z + 0.002 + 0.002 * (sx > 0)), (0.005 + sx * 0.012 + 0.002, -0.052, z + 0.002 + 0.002 * (sx > 0)),
                (0.005 - sx * 0.006 + 0.0015, 0.03, z + 0.002 + 0.002 * (sx > 0)), (0.005 - sx * 0.006 - 0.0015, 0.03, z + 0.002 + 0.002 * (sx > 0))), tool)
    for sx in (-1, 1):
        m.hull(((0.045 + sx * 0.008, -0.07, z), (0.045 + sx * 0.011, -0.07, z), (0.045 + sx * 0.0015, 0.06, z), (0.045, 0.06, z)),
               ((0.045 + sx * 0.008, -0.07, z + 0.002), (0.045 + sx * 0.011, -0.07, z + 0.002), (0.045 + sx * 0.0015, 0.06, z + 0.002),
                (0.045, 0.06, z + 0.002)), tool)
    return m.array()


def build_floor_drain_mesh():
    m = _MetricMesh("floor_drain")
    frame = (0.40, 0.40, 0.42)
    well = (0.05, 0.05, 0.06)
    bar = (0.30, 0.30, 0.32)
    a, inner = 0.10, 0.075
    top = 0.012
    for sx in (-1, 1):
        m.box(sx * (a + inner) / 2, 0.0, top / 2, (a - inner) / 2, a, top / 2, frame, skip_bottom=True)
        m.box(0.0, sx * (a + inner) / 2, top / 2, inner, (a - inner) / 2, top / 2, frame, skip_bottom=True)
    m.quad((-inner, -inner, 0.004), (inner, -inner, 0.004), (inner, inner, 0.004), (-inner, inner, 0.004), (0, 0, 1), well)
    m.ring("z", (0.0, 0.0), 0.004, top - 0.002, 0.058, 0.07, bar, n=16)
    for k in range(5):
        m.box(0.0, -0.048 + k * 0.024, 0.0075, 0.06, 0.004, 0.0025, bar)
    m.box(0.0, 0.0, 0.0075, 0.004, 0.06, 0.0025, bar)
    return m.array()


def build_boiler_tank_mesh():
    m = _MetricMesh("boiler_tank")
    body = (0.36, 0.35, 0.33)
    band = (0.24, 0.23, 0.22)
    plinth = (0.18, 0.18, 0.19)
    door = (0.20, 0.19, 0.18)
    glow = (1.0, 0.58, 0.24)
    pipe = (0.40, 0.38, 0.34)
    face = (0.90, 0.88, 0.80)
    needle = (0.70, 0.08, 0.06)
    R = 0.34
    m.box(0.0, 0.0, 0.04, 0.38, 0.38, 0.04, plinth, skip_bottom=True)
    m.tube("z", (0.0, 0.0), 0.08, 0.80, R, R, body, n=16)
    for bz in (0.16, 0.48, 0.76):
        m.ring("z", (0.0, 0.0), bz - 0.014, bz + 0.014, R, R + 0.008, band, n=16)
    m.tube("z", (0.0, 0.0), 0.80, 0.90, R, 0.18, body, n=16)
    m.tube("z", (0.0, 0.0), 0.90, 0.92, 0.18, 0.18, band, n=16, cap1=True)
    m.tube("z", (-0.05, 0.0), 0.92, 1.21, 0.07, 0.07, pipe, n=10)
    m.box(R - 0.02, 0.0, 0.29, 0.035, 0.13, 0.09, band)
    m.box(R + 0.02, 0.0, 0.29, 0.006, 0.11, 0.075, door)
    for k in range(3):
        m.box(R + 0.0275, 0.0, 0.25 + k * 0.04, 0.0015, 0.08, 0.008, glow)
    m.box(R + 0.03, 0.09, 0.29, 0.004, 0.012, 0.03, band)
    m.tube("x", (-0.12, 0.62), R - 0.03, R + 0.03, 0.05, 0.05, band, n=12)
    m.tube("x", (-0.12, 0.62), R + 0.03, R + 0.031, 0.042, 0.042, face, n=12, cap1=True)
    m.spoke("x", (-0.12, 0.62), R + 0.034, -0.004, 0.034, 2.3, 0.0025, needle)
    m.tube("x", (0.20, 0.66), 0.20, R + 0.08, 0.025, 0.025, pipe, n=8)
    m.tube("z", (R + 0.08, 0.20), 0.0, 0.685, 0.025, 0.025, pipe, n=8, cap1=True)
    return m.array()


def build_gauge_panel_mesh():
    m = _MetricMesh("gauge_panel")
    steel = (0.52, 0.54, 0.55)
    bezel = (0.20, 0.20, 0.21)
    face = (0.92, 0.90, 0.84)
    needle = (0.70, 0.08, 0.06)
    pipe = (0.46, 0.44, 0.40)
    valve = (0.72, 0.16, 0.12)
    wall = -0.14
    m.box(wall + 0.006, 0.0, 0.27, 0.006, 0.25, 0.14, steel, skip_back=True)
    for sy in (-0.21, 0.21):
        m.tube("z", (wall + 0.03, sy), 0.0, 0.14, 0.012, 0.012, pipe, n=8)
        m.box(wall + 0.018, sy, 0.135, 0.006, 0.02, 0.012, pipe)
    dials = ((-0.14, 0.31, 0.052, 0.6), (0.0, 0.33, 0.062, -0.4), (0.14, 0.31, 0.052, 1.9))
    for dy, dz, r, ang in dials:
        x0 = wall + 0.012
        m.tube("x", (dy, dz), x0, x0 + 0.026, r, r, bezel, n=12, cap1=False, phase=math.pi / 12)
        m.tube("x", (dy, dz), x0 + 0.022, x0 + 0.023, r - 0.006, r - 0.006, face, n=12, cap1=True, phase=math.pi / 12)
        m.spoke("x", (dy, dz), x0 + 0.026, -0.006, r - 0.014, ang, 0.0025, needle)
        m.tube("x", (dy, dz), x0 + 0.023, x0 + 0.028, 0.0, 0.006, bezel, n=6, cap1=True)
        m.tube("z", (x0 + 0.016, dy), 0.10, dz - r, 0.007, 0.007, pipe, n=6)
    for sy in (-0.07, 0.07):
        m.tube("x", (sy, 0.17), wall + 0.012, wall + 0.05, 0.008, 0.008, pipe, n=6)
        m.box(wall + 0.054, sy, 0.17, 0.004, 0.022, 0.005, valve)
    return m.array()


def build_kitchen_counter_mesh():
    m = _MetricMesh("kitchen_counter")
    d = PROP_DEFS["kitchen_counter"]
    hw, hd, h = d["hw"], d["hd"], d["height"]
    top = h * SURFACE_TOP_FRAC["kitchen_counter"]
    steel = (0.66, 0.68, 0.70)
    steel_dark = (0.40, 0.42, 0.44)
    door_c = (0.74, 0.76, 0.78)
    bright = (0.86, 0.88, 0.90)
    black = (0.15, 0.15, 0.17)
    slab = 0.022
    kick = 0.055
    body_top = top - slab
    front = hd - 0.03

    m.box(-0.02, 0.0, kick / 2, hd - 0.05, hw - 0.03, kick / 2, black, skip_bottom=True)
    m.box(-0.015, 0.0, (kick + body_top) / 2, hd - 0.015, hw - 0.008,
          (body_top - kick) / 2, steel_dark, skip_bottom=True)

    door_h = (body_top - kick) / 2 - 0.012
    for sy in (-1, 1):
        cy = sy * (hw / 2 + 0.004)
        m.box(front + 0.009, cy, (kick + body_top) / 2, 0.009, hw / 2 - 0.016, door_h, door_c)
        m.box(front + 0.020, cy, (kick + body_top) / 2, 0.003, hw / 2 - 0.040, door_h - 0.022, steel)
        hy = cy - sy * (hw / 2 - 0.05)
        for foot in (-1, 1):
            m.box(front + 0.026, hy, (kick + body_top) / 2 + foot * 0.055, 0.008, 0.008, 0.008, bright)
        m.box(front + 0.034, hy, (kick + body_top) / 2, 0.006, 0.010, 0.063, bright)
        for hz in (kick + 0.05, body_top - 0.05):
            m.box(front + 0.012, sy * (hw - 0.022), hz, 0.010, 0.012, 0.018, steel)

    m.box(0.0, 0.0, top - slab / 2, hd, hw, slab / 2, steel)
    m.box(hd - 0.006, 0.0, top - slab - 0.010, 0.010, hw, 0.010, steel)
    m.box(-hd + 0.014, 0.0, top + 0.036, 0.014, hw, 0.036, steel)
    m.box(-hd + 0.030, 0.0, top + 0.070, 0.004, hw - 0.02, 0.003, bright)

    m.box(-0.015, 0.0, (kick + body_top) / 2, hd - 0.05, hw - 0.05, 0.006, steel_dark)
    return m.array()


def build_fridge_freezer_mesh():
    m = _MetricMesh("fridge_freezer")
    enamel = (0.86, 0.87, 0.84)
    door_c = (0.90, 0.91, 0.88)
    dark = (0.14, 0.14, 0.15)
    chrome = (0.70, 0.71, 0.73)
    h = PROP_DEFS["fridge_freezer"]["height"]
    wall, face, hy = -0.24, 0.20, 0.30
    m.box((wall + face) / 2, 0.0, (0.035 + h) / 2, (face - wall) / 2, hy, (h - 0.035) / 2, enamel, skip_back=True)
    m.box((wall + face - 0.02) / 2, 0.0, 0.0175, (face - 0.02 - wall) / 2, hy - 0.01, 0.0175, dark, skip_back=True)
    for k in range(5):
        m.box(face - 0.018, -0.2 + k * 0.1, 0.018, 0.003, 0.035, 0.004, (0.3, 0.3, 0.31))
    door_t = 0.035
    split = 0.585
    for z0, z1, handle_z0, handle_z1 in ((0.04, split - 0.004, split - 0.2, split - 0.02), (split + 0.004, h - 0.006, split + 0.02, split + 0.14)):
        m.box(face + door_t / 2, 0.0, (z0 + z1) / 2, door_t / 2, hy - 0.004, (z1 - z0) / 2, door_c)
        hx = face + door_t
        m.box(hx + 0.022, -hy + 0.045, (handle_z0 + handle_z1) / 2, 0.006, 0.008, (handle_z1 - handle_z0) / 2, chrome)
        for zz in (handle_z0 + 0.01, handle_z1 - 0.01):
            m.box(hx + 0.008, -hy + 0.045, zz, 0.008, 0.005, 0.005, chrome)
        for zz in (z0 + 0.02, z1 - 0.02):
            m.box(hx - 0.012, hy + 0.004, zz, 0.012, 0.004, 0.012, chrome)
    m.box(face + door_t + 0.002, 0.15, h - 0.05, 0.002, 0.03, 0.008, chrome)
    return m.array()


def build_tray_stack_mesh():
    m = _MetricMesh("tray_stack")
    colors = ((0.62, 0.48, 0.32), (0.58, 0.45, 0.30))
    hx, hy = 0.088, 0.084
    step = 0.017
    for i in range(5):
        z = i * step
        ox = 0.004 if i % 2 else -0.004
        oy = -0.003 if i % 2 else 0.003
        col = colors[i % 2]
        m.box(ox, oy, z + 0.0015, hx, hy, 0.0015, col, skip_bottom=i > 0)
        for sx in (-1, 1):
            m.box(ox + sx * (hx - 0.002), oy, z + 0.003 + 0.004, 0.002, hy, 0.004, col)
            m.box(ox, oy + sx * (hy - 0.002), z + 0.003 + 0.004, hx - 0.004, 0.002, 0.004, col)
    return m.array()


def build_workbench_mesh():
    m = _MetricMesh("workbench")
    planks = ((0.50, 0.38, 0.23), (0.46, 0.35, 0.21), (0.52, 0.40, 0.24), (0.47, 0.36, 0.22), (0.49, 0.37, 0.22))
    leg_c = (0.38, 0.29, 0.18)
    dark = (0.30, 0.23, 0.14)
    iron = (0.30, 0.31, 0.33)
    crate = (0.58, 0.45, 0.28)
    h = PROP_DEFS["workbench"]["height"]
    hx, hy, t = 0.26, 0.44, 0.04
    n = len(planks)
    w = 2 * hx / n
    for i, col in enumerate(planks):
        m.box(-hx + w * (i + 0.5), 0.0, h - t / 2, w / 2 - 0.0015, hy, t / 2, col)
    leg = 0.03
    for sx in (-1, 1):
        for sy in (-1, 1):
            m.box(sx * (hx - 0.05), sy * (hy - 0.06), (h - t) / 2, leg, leg, (h - t) / 2, leg_c, skip_bottom=True)
        m.box(sx * (hx - 0.05), 0.0, h - t - 0.035, 0.012, hy - 0.09, 0.035, dark)
    m.box(0.0, 0.0, 0.09, hx - 0.05 - leg, hy - 0.06 - leg, 0.012, dark)
    m.box(0.02, 0.18, 0.102 + 0.06, 0.07, 0.09, 0.06, crate)
    m.box(-hx + 0.012, 0.0, h - t - 0.02, 0.012, hy, 0.02, dark)
    vy = hy - 0.12
    m.box(hx - 0.05, vy, h - t - 0.012, 0.06, 0.06, 0.012, iron)
    m.box(hx + 0.016, vy, h - t - 0.03, 0.006, 0.055, 0.03, iron)
    m.tube("x", (vy, h - t - 0.035), hx + 0.022, hx + 0.042, 0.006, 0.006, iron, n=6, cap1=True)
    m.box(hx + 0.042, vy, h - t - 0.035, 0.005, 0.04, 0.005, iron)
    return m.array()


def build_tool_pegboard_mesh():
    d = PROP_DEFS["tool_pegboard"]
    hw, hd, h = d["hw"], d["hd"], d["height"]
    m = _MetricMesh("tool_pegboard")
    wall = -hd
    board = (0.52, 0.40, 0.26)
    board_b = (0.47, 0.36, 0.23)
    backing = (0.11, 0.10, 0.10)
    frame_c = (0.29, 0.24, 0.19)
    steel = (0.68, 0.70, 0.72)
    steel_d = (0.36, 0.38, 0.40)
    paint = (0.14, 0.13, 0.12)
    red = (0.62, 0.17, 0.14)
    wood = (0.55, 0.38, 0.21)
    black = (0.13, 0.13, 0.14)
    z0, z1 = 0.13, 0.80
    face = wall + 0.040

    m.box(wall + 0.012, 0.0, (z0 + z1) / 2, 0.007, hw - 0.02, (z1 - z0) / 2, backing, skip_back=True)
    slat_h, gap = 0.052, 0.013
    zz = z0
    i = 0
    while zz + slat_h <= z1:
        m.box(wall + 0.030, 0.0, zz + slat_h / 2, 0.010, hw - 0.025, slat_h / 2,
              board if i % 2 == 0 else board_b)
        zz += slat_h + gap
        i += 1
    for sy in (-1, 1):
        m.box(wall + 0.030, sy * (hw - 0.014), (z0 + z1) / 2, 0.012, 0.014, (z1 - z0) / 2 + 0.014, frame_c)
    for zz in (z0 - 0.014, z1 + 0.014):
        m.box(wall + 0.030, 0.0, zz, 0.012, hw - 0.014, 0.014, frame_c)

    def hook(y, z, reach=0.030):
        m.tube("x", (y, z), face, face + reach, 0.005, 0.005, steel_d, n=6)
        m.tube("z", (face + reach, y), z, z + 0.022, 0.005, 0.005, steel_d, n=6)

    hook(-0.30, 0.66)
    m.box(face + 0.022, -0.30, 0.545, 0.011, 0.011, 0.075, wood)
    m.box(face + 0.022, -0.30, 0.640, 0.017, 0.050, 0.018, steel)
    m.hull([(face + 0.008, -0.348, 0.628), (face + 0.036, -0.348, 0.628),
            (face + 0.036, -0.348, 0.652), (face + 0.008, -0.348, 0.652)],
           [(face + 0.014, -0.368, 0.634), (face + 0.030, -0.368, 0.634),
            (face + 0.030, -0.368, 0.650), (face + 0.014, -0.368, 0.650)], steel)

    hook(-0.16, 0.62)
    m.box(face + 0.020, -0.16, 0.545, 0.009, 0.014, 0.060, steel_d)
    m.box(face + 0.020, -0.16, 0.615, 0.011, 0.026, 0.016, steel)
    m.box(face + 0.020, -0.172, 0.636, 0.013, 0.014, 0.010, steel)

    hook(-0.03, 0.60)
    for sy in (-1, 1):
        m.box(face + 0.018, -0.03 + sy * 0.008, 0.560, 0.008, 0.008, 0.045, red)
        m.box(face + 0.018, -0.03 + sy * 0.004, 0.612, 0.010, 0.007, 0.022, steel)
    m.box(face + 0.018, -0.03, 0.636, 0.012, 0.016, 0.006, steel)

    for y, grip, length in ((0.09, red, 0.055), (0.145, black, 0.042)):
        hook(y, 0.58)
        m.box(face + 0.016, y, 0.543, 0.008, 0.010, 0.026, grip)
        m.tube("z", (face + 0.016, y), 0.560, 0.569 + length, 0.004, 0.003, steel, n=6, cap1=True)

    hook(0.31, 0.72, reach=0.034)
    m.box(face + 0.024, 0.325, 0.690, 0.010, 0.030, 0.034, wood)
    m.face([(face + 0.023, 0.310, 0.700), (face + 0.023, 0.310, 0.658),
            (face + 0.023, 0.196, 0.452), (face + 0.023, 0.196, 0.505)],
           steel, (wall, 0.25, 0.59))
    m.face([(face + 0.028, 0.307, 0.658), (face + 0.028, 0.196, 0.452),
            (face + 0.028, 0.192, 0.459), (face + 0.028, 0.305, 0.665)],
           steel_d, (wall, 0.25, 0.59))

    hook(-0.24, 0.30)
    hook(-0.03, 0.30)
    m.box(face + 0.020, -0.135, 0.322, 0.010, 0.115, 0.016, (0.78, 0.62, 0.12))
    m.box(face + 0.031, -0.135, 0.322, 0.002, 0.020, 0.009, black)
    m.box(face + 0.031, -0.135, 0.322, 0.003, 0.016, 0.005, (0.55, 0.78, 0.45))
    hook(0.24, 0.34)
    for r_in, r_out, off in ((0.042, 0.052, 0.018), (0.034, 0.044, 0.028), (0.046, 0.055, 0.030)):
        m.ring("x", (0.24, 0.300 - off * 0.2), face + off, face + off + 0.009, r_in, r_out, black, n=12)

    for y, hy, hz, zc in ((0.375, 0.024, 0.060, 0.47), (-0.30, 0.020, 0.055, 0.36)):
        for oy, oz, sy_, sz_ in ((0.0, hz, hy, 0.004), (0.0, -hz, hy, 0.004),
                                 (hy, 0.0, 0.004, hz), (-hy, 0.0, 0.004, hz)):
            m.box(face + 0.004, y + oy, zc + oz, 0.004, sy_, sz_, paint)

    m.box(wall + 0.055, 0.0, z0 - 0.040, 0.037, hw - 0.03, 0.008, frame_c)
    m.box(wall + 0.090, 0.0, z0 - 0.026, 0.004, hw - 0.03, 0.014, frame_c)
    for y, r, tall, col in ((-0.20, 0.026, 0.038, steel_d), (-0.12, 0.020, 0.030, red),
                            (0.14, 0.024, 0.026, steel)):
        m.tube("z", (wall + 0.055, y), z0 - 0.032, z0 - 0.032 + tall, r, r, col, n=8, cap1=True)
    return m.array()


def build_reception_desk_mesh():
    m = _MetricMesh("reception_desk")
    top_c = (0.54, 0.41, 0.26)
    panel = (0.44, 0.33, 0.20)
    batten = (0.50, 0.38, 0.24)
    dark = (0.22, 0.18, 0.13)
    handle = (0.72, 0.68, 0.58)
    h = PROP_DEFS["reception_desk"]["height"]
    hx, hy, t = PROP_DEFS["reception_desk"]["hd"], PROP_DEFS["reception_desk"]["hw"], 0.024
    m.box(0.0, 0.0, h - t / 2, hx, hy, t / 2, top_c)
    under = h - t
    front = hx - 0.02
    m.box(front - 0.01, 0.0, under / 2, 0.01, hy - 0.03, under / 2, panel, skip_bottom=True)
    for y in (-hy + 0.12, 0.0, hy - 0.12):
        m.box(front + 0.004, y, 0.03 + (under - 0.03) / 2, 0.004, 0.025, (under - 0.03) / 2, batten)
    m.box(front + 0.004, 0.0, under - 0.02, 0.004, hy - 0.03, 0.02, batten)
    m.box(front + 0.003, 0.0, 0.015, 0.003, hy - 0.03, 0.015, dark)
    for sy in (-1, 1):
        m.box(0.0, sy * (hy - 0.015), under / 2, hx - 0.02, 0.015, under / 2, panel, skip_bottom=True)
    ph = hx - 0.06
    pw = 0.12
    pcy = -(hy - 0.03) + pw
    m.box(-0.01, pcy, under / 2, ph, pw, under / 2, panel, skip_bottom=True)
    dz = (under - 0.02) / 3
    for i in range(3):
        z = 0.015 + dz * (i + 0.5)
        m.box(-ph - 0.014, pcy, z, 0.004, pw - 0.01, dz / 2 - 0.008, batten)
        m.box(-ph - 0.022, pcy, z + dz * 0.2, 0.004, 0.04, 0.006, handle)
    return m.array()


def build_fire_extinguisher_mesh():
    m = _MetricMesh("fire_extinguisher")
    red = (0.78, 0.10, 0.08)
    dark = (0.16, 0.16, 0.17)
    brass = (0.62, 0.52, 0.28)
    label = (0.88, 0.86, 0.78)
    wall = -0.06
    r = 0.045
    cx = wall + 0.012 + r
    m.box(wall + 0.004, 0.0, 0.14, 0.004, 0.03, 0.12, dark, skip_back=True)
    m.tube("z", (cx, 0.0), 0.02, 0.10, r, r, red, n=12, cap0=True)
    m.tube("z", (cx, 0.0), 0.10, 0.15, r, r, label, n=12)
    m.tube("z", (cx, 0.0), 0.15, 0.215, r, r, red, n=12)
    m.tube("z", (cx, 0.0), 0.215, 0.24, r, 0.02, red, n=12)
    m.ring("z", (cx, 0.0), 0.07, 0.085, r - 0.003, r + 0.004, dark, n=12)
    m.tube("z", (cx, 0.0), 0.24, 0.255, 0.013, 0.013, brass, n=8)
    m.box(cx, 0.0, 0.265, 0.018, 0.016, 0.012, dark)
    m.tube("x", (0.0, 0.265), cx + 0.018, cx + 0.05, 0.006, 0.006, dark, n=6, cap1=True)
    m.box(cx - 0.005, 0.0, 0.279, 0.03, 0.005, 0.004, red)
    return m.array()


def build_iv_stand_mesh():
    m = _MetricMesh("iv_stand")
    chrome = (0.70, 0.71, 0.74)
    dark = (0.10, 0.10, 0.11)
    bag = (0.82, 0.88, 0.86)
    fluid = (0.92, 0.84, 0.56)
    line = (0.80, 0.84, 0.84)
    m.tube("z", (0.0, 0.0), 0.02, 0.06, 0.022, 0.018, chrome, n=8, cap1=True)
    for k in range(5):
        a = math.tau * k / 5
        dx, dy = math.cos(a), math.sin(a)
        px, py = -dy * 0.008, dx * 0.008
        foot = [(dx * 0.015 - px, dy * 0.015 - py), (dx * 0.12 - px, dy * 0.12 - py),
                (dx * 0.12 + px, dy * 0.12 + py), (dx * 0.015 + px, dy * 0.015 + py)]
        m.hull([(x, y, 0.036) for x, y in foot], [(x, y, 0.046) for x, y in foot], chrome, cap_bottom=True)
        cx, cy = dx * 0.12, dy * 0.12
        m.ring("y", (cx, 0.018), cy - 0.006, cy + 0.006, 0.0, 0.018, dark, n=8)
    m.tube("z", (0.0, 0.0), 0.06, 0.80, 0.008, 0.008, chrome, n=6, cap1=True)
    m.box(0.0, 0.0, 0.785, 0.006, 0.07, 0.005, chrome)
    for sy in (-1, 1):
        m.box(0.0, sy * 0.068, 0.765, 0.005, 0.005, 0.018, chrome)
    m.box(0.0, 0.068, 0.70, 0.012, 0.035, 0.05, bag)
    m.box(0.0, 0.068, 0.675, 0.0135, 0.028, 0.02, fluid)
    m.tube("z", (0.0, 0.068), 0.60, 0.65, 0.007, 0.007, bag, n=6)
    m.box(0.0, 0.068, 0.45, 0.005, 0.005, 0.15, line)
    m.box(0.0, 0.041, 0.30, 0.005, 0.032, 0.005, line)
    return m.array()


def build_wheelchair_mesh():
    m = _MetricMesh("wheelchair", scale=0.62)
    chrome = (0.64, 0.65, 0.68)
    vinyl = (0.13, 0.14, 0.17)
    tyre = (0.07, 0.07, 0.08)
    grip = (0.10, 0.10, 0.10)
    seat_z = 0.38
    for sy in (-1, 1):
        y = sy * 0.19
        m.box(-0.02, y, seat_z - 0.015, 0.18, 0.011, 0.011, chrome)
        m.tube("z", (-0.19, y), 0.12, 0.84, 0.011, 0.011, chrome, n=6)
        m.box(-0.225, y, 0.835, 0.035, 0.012, 0.012, chrome)
        m.box(-0.268, y, 0.835, 0.018, 0.016, 0.016, grip)
        m.tube("z", (0.14, y), 0.12, seat_z - 0.026, 0.011, 0.011, chrome, n=6)
        m.box(-0.04, y, 0.58, 0.14, 0.022, 0.012, vinyl)
        m.tube("z", (0.07, y), seat_z - 0.004, 0.568, 0.008, 0.008, chrome, n=6)
        m.tube("z", (-0.15, y), seat_z - 0.004, 0.568, 0.008, 0.008, chrome, n=6)
        m.box(0.165, sy * 0.165, 0.335, 0.032, 0.040, 0.013, chrome)
        m.box(0.190, sy * 0.145, 0.205, 0.009, 0.009, 0.135, chrome)
        m.box(0.230, sy * 0.115, 0.072, 0.046, 0.055, 0.006, chrome)
        wy0, wy1 = sy * 0.205, sy * 0.225
        lo, hi = min(wy0, wy1), max(wy0, wy1)
        m.ring("y", (-0.10, 0.26), lo, hi, 0.225, 0.255, tyre, n=16)
        m.ring("y", (-0.10, 0.26), lo + 0.004, hi - 0.004, 0.205, 0.225, chrome, n=16)
        hr = sy * 0.238
        m.ring("y", (-0.10, 0.26), min(hr, hr + sy * 0.006), max(hr, hr + sy * 0.006), 0.205, 0.214, chrome, n=16)
        for k in range(6):
            m.spoke("y", (-0.10, 0.26), sy * 0.215, 0.02, 0.205, math.tau * k / 6 + 0.2, 0.003, chrome)
        m.tube("y", (-0.10, 0.26), min(sy * 0.19, sy * 0.232), max(sy * 0.19, sy * 0.232), 0.02, 0.02, chrome, n=8,
               cap0=True, cap1=True)
        m.box(0.16, sy * 0.17, 0.10, 0.008, 0.02, 0.03, chrome)
        m.ring("y", (0.16, 0.055), sy * 0.17 - 0.012, sy * 0.17 + 0.012, 0.0, 0.055, tyre, n=10)
    m.box(-0.02, 0.0, seat_z, 0.16, 0.178, 0.012, vinyl)
    m.box(-0.19, 0.0, 0.62, 0.012, 0.178, 0.17, vinyl)
    return m.array()


def _log_body(m, segs, bark, bark_b, n=9):
    for i, (y0, y1, cx, cz, r0, r1) in enumerate(segs):
        m.tube("y", (cx, cz), y0, y1, r0, r1, bark if i % 2 == 0 else bark_b, n=n)


def _log_break(m, y, direction, cx, cz, r, cut, bark):
    m.tube("y", (cx, cz), y - direction * 0.012, y, r * 0.92, r * 0.86, cut, n=9,
           cap1=True)
    for k in range(6):
        a = math.tau * k / 6 + 0.4
        ca, sa = math.cos(a), math.sin(a)
        reach = 0.05 + 0.055 * ((k * 5) % 7) / 7.0
        base = [(cx + ca * r * 0.7 - sa * 0.022, y, cz + sa * r * 0.7 + ca * 0.022),
                (cx + ca * r - sa * 0.03, y, cz + sa * r + ca * 0.03),
                (cx + ca * r + sa * 0.03, y, cz + sa * r - ca * 0.03),
                (cx + ca * r * 0.7 + sa * 0.022, y, cz + sa * r * 0.7 - ca * 0.022)]
        tip_x, tip_z = cx + ca * r * 0.8, cz + sa * r * 0.8
        top = [(tip_x - sa * 0.006, y + direction * reach, tip_z + ca * 0.006),
               (tip_x - sa * 0.006, y + direction * reach, tip_z + ca * 0.006),
               (tip_x + sa * 0.006, y + direction * reach, tip_z - ca * 0.006),
               (tip_x + sa * 0.006, y + direction * reach, tip_z - ca * 0.006)]
        m.hull(base, top, cut if k % 2 == 0 else bark)


def _log_extras(m, moss, bark, stubs, moss_bands):
    for (y, ang, cx, cz, r, length) in stubs:
        ca, sa = math.cos(ang), math.sin(ang)
        m.hull([(cx + ca * r * 0.5 - sa * 0.03, y - 0.03, cz + sa * r * 0.5 + ca * 0.03),
                (cx + ca * r * 0.5 - sa * 0.03, y + 0.03, cz + sa * r * 0.5 + ca * 0.03),
                (cx + ca * r * 0.5 + sa * 0.03, y + 0.03, cz + sa * r * 0.5 - ca * 0.03),
                (cx + ca * r * 0.5 + sa * 0.03, y - 0.03, cz + sa * r * 0.5 - ca * 0.03)],
               [(cx + ca * (r + length) - sa * 0.018, y - 0.018, cz + sa * (r + length) + ca * 0.018),
                (cx + ca * (r + length) - sa * 0.018, y + 0.018, cz + sa * (r + length) + ca * 0.018),
                (cx + ca * (r + length) + sa * 0.018, y + 0.018, cz + sa * (r + length) - ca * 0.018),
                (cx + ca * (r + length) + sa * 0.018, y - 0.018, cz + sa * (r + length) - ca * 0.018)],
               bark)
    for (y0, y1, cx, cz, r) in moss_bands:
        for a0, a1 in ((0.95, 1.55), (1.55, 2.15)):
            p = []
            for (yy, aa) in ((y0, a0), (y1, a0), (y1, a1), (y0, a1)):
                p.append((cx + math.cos(aa) * r, yy, cz + math.sin(aa) * r))
            m.face(p, moss, (cx, (y0 + y1) / 2, cz))


def build_fallen_log_mesh():
    m = _MetricMesh("fallen_log")
    bark = (0.36, 0.26, 0.16)
    bark_b = (0.30, 0.21, 0.13)
    cut = (0.62, 0.48, 0.30)
    moss = (0.21, 0.29, 0.14)
    segs = [(-0.50, -0.16, 0.0, 0.140, 0.140, 0.134),
            (-0.16, 0.16, 0.018, 0.135, 0.134, 0.126),
            (0.16, 0.50, 0.046, 0.126, 0.126, 0.104)]
    _log_body(m, segs, bark, bark_b)
    m.tube("y", (0.0, 0.140), -0.502, -0.50, 0.140, 0.140, cut, n=9, cap0=True)
    _log_break(m, 0.50, 1.0, 0.046, 0.126, 0.104, cut, bark)
    _log_extras(m, moss, bark,
                stubs=[(-0.28, 1.15, 0.0, 0.140, 0.140, 0.075), (0.22, 0.35, 0.046, 0.126, 0.118, 0.055)],
                moss_bands=[(-0.44, -0.24, 0.0, 0.140, 0.142), (-0.02, 0.14, 0.018, 0.135, 0.131),
                            (0.26, 0.40, 0.046, 0.126, 0.116)])
    return m.array()


def build_fallen_log_peeled_mesh():
    m = _MetricMesh("fallen_log")
    bark = (0.33, 0.24, 0.15)
    bark_b = (0.28, 0.20, 0.12)
    cut = (0.66, 0.53, 0.35)
    moss = (0.19, 0.27, 0.13)
    segs = [(-0.50, -0.05, 0.0, 0.132, 0.132, 0.130),
            (-0.05, 0.50, 0.012, 0.130, 0.130, 0.122)]
    _log_body(m, segs, bark, bark_b)
    m.tube("y", (0.0, 0.132), -0.502, -0.50, 0.132, 0.132, cut, n=9, cap0=True)
    _log_break(m, 0.50, 1.0, 0.012, 0.122, 0.122, cut, bark)
    for y0, y1, r in ((-0.34, 0.06, 0.128), (0.06, 0.34, 0.124)):
        for a0, a1 in ((0.55, 1.15), (1.15, 1.75)):
            p = []
            for (yy, aa) in ((y0, a0), (y1, a0), (y1, a1), (y0, a1)):
                p.append((0.006 + math.cos(aa) * r * 0.99, yy, 0.130 + math.sin(aa) * r * 0.99))
            m.face(p, cut, (0.006, (y0 + y1) / 2, 0.130))
    _log_extras(m, moss, bark,
                stubs=[(0.30, -0.9, 0.012, 0.126, 0.120, 0.06)],
                moss_bands=[(-0.48, -0.30, 0.0, 0.132, 0.134), (0.14, 0.28, 0.012, 0.130, 0.126)])
    return m.array()


def build_fallen_log_hollow_mesh():
    m = _MetricMesh("fallen_log")
    bark = (0.31, 0.23, 0.15)
    bark_b = (0.26, 0.19, 0.12)
    cut = (0.58, 0.45, 0.28)
    inner = (0.10, 0.08, 0.06)
    moss = (0.22, 0.30, 0.15)
    segs = [(-0.42, 0.02, 0.0, 0.145, 0.150, 0.145),
            (0.02, 0.44, 0.02, 0.140, 0.145, 0.138)]
    _log_body(m, segs, bark, bark_b)
    for y, r_out, r_in, cx, cz in ((-0.42, 0.150, 0.098, 0.0, 0.145), (0.44, 0.138, 0.086, 0.02, 0.140)):
        m.ring("y", (cx, cz), y - 0.004, y + 0.004, r_in, r_out, cut, n=10)
    m.tube("y", (0.0, 0.143), -0.418, 0.436, 0.096, 0.086, inner, n=10)
    _log_extras(m, moss, bark,
                stubs=[(-0.18, 1.5, 0.0, 0.145, 0.148, 0.05)],
                moss_bands=[(-0.34, -0.12, 0.0, 0.145, 0.148), (0.10, 0.34, 0.02, 0.140, 0.140)])
    return m.array()


def _stump_roots(m, n, reach, root, seed_phase=0.3):
    for k in range(n):
        a = math.tau * k / n + seed_phase
        dx, dy = math.cos(a), math.sin(a)
        px, py = -dy, dx
        w0, w1 = 0.05, 0.02
        base = [(dx * 0.12 - px * w0, dy * 0.12 - py * w0, 0.0), (dx * reach - px * w1, dy * reach - py * w1, 0.0),
                (dx * reach + px * w1, dy * reach + py * w1, 0.0), (dx * 0.12 + px * w0, dy * 0.12 + py * w0, 0.0)]
        top = [(dx * 0.12 - px * w0 * 0.7, dy * 0.12 - py * w0 * 0.7, 0.09),
               (dx * reach - px * w1, dy * reach - py * w1, 0.012),
               (dx * reach + px * w1, dy * reach + py * w1, 0.012),
               (dx * 0.12 + px * w0 * 0.7, dy * 0.12 + py * w0 * 0.7, 0.09)]
        m.hull(base, top, root)


def build_tree_stump_mesh():
    m = _MetricMesh("tree_stump")
    bark = (0.42, 0.30, 0.18)
    cut = (0.66, 0.52, 0.34)
    root = (0.36, 0.26, 0.16)
    m.tube("z", (0.0, 0.0), 0.0, 0.08, 0.17, 0.155, bark, n=10)
    m.tube("z", (0.0, 0.0), 0.08, 0.215, 0.155, 0.16, bark, n=10)
    m.tube("z", (0.0, 0.0), 0.215, 0.22, 0.16, 0.16, cut, n=10, cap1=True)
    _stump_roots(m, 5, 0.25, root)
    m.hull(((0.10, -0.06, 0.22), (0.155, -0.03, 0.22), (0.15, 0.03, 0.22), (0.105, 0.0, 0.22)),
           ((0.135, -0.03, 0.29), (0.148, -0.02, 0.28), (0.144, -0.005, 0.28), (0.13, -0.015, 0.29)), bark)
    return m.array()


def build_tree_stump_split_mesh():
    m = _MetricMesh("tree_stump")
    bark = (0.38, 0.27, 0.16)
    cut = (0.62, 0.48, 0.31)
    root = (0.33, 0.24, 0.15)
    m.tube("z", (0.0, 0.0), 0.0, 0.07, 0.175, 0.16, bark, n=10)
    m.tube("z", (0.0, 0.0), 0.07, 0.17, 0.16, 0.15, bark, n=10)
    m.tube("z", (0.0, 0.0), 0.17, 0.175, 0.15, 0.15, cut, n=10, cap1=True)
    _stump_roots(m, 6, 0.27, root, seed_phase=0.9)
    for sx, tilt, tall in ((1.0, 0.075, 0.30), (-1.0, -0.06, 0.215)):
        m.hull(((sx * 0.118, -0.065, 0.17), (sx * 0.162, -0.05, 0.17),
                (sx * 0.162, 0.045, 0.17), (sx * 0.118, 0.06, 0.17)),
               ((sx * 0.118 + tilt, -0.020, tall), (sx * 0.152 + tilt, -0.014, tall),
                (sx * 0.152 + tilt, 0.014, tall), (sx * 0.118 + tilt, 0.020, tall)), cut)
    return m.array()


def build_tree_stump_mossy_mesh():
    m = _MetricMesh("tree_stump")
    bark = (0.34, 0.28, 0.19)
    cut = (0.45, 0.40, 0.26)
    root = (0.30, 0.25, 0.17)
    moss = (0.22, 0.31, 0.15)
    shelf = (0.55, 0.47, 0.33)
    m.tube("z", (0.0, 0.0), 0.0, 0.09, 0.19, 0.175, bark, n=12)
    m.tube("z", (0.0, 0.0), 0.09, 0.205, 0.175, 0.175, bark, n=12)
    m.tube("z", (0.0, 0.0), 0.205, 0.212, 0.175, 0.175, cut, n=12, cap1=True)
    m.tube("z", (0.0, 0.0), 0.212, 0.220, 0.152, 0.140, moss, n=12, cap1=True)
    _stump_roots(m, 7, 0.29, root, seed_phase=0.15)
    for k, (r, z) in enumerate(((0.175, 0.105), (0.17, 0.150))):
        a = 2.0 + k * 0.5
        ca, sa = math.cos(a), math.sin(a)
        m.hull(((ca * r * 0.9, sa * r * 0.9, z), (ca * r * 0.9 - sa * 0.05, sa * r * 0.9 + ca * 0.05, z),
                (ca * (r + 0.07) - sa * 0.04, sa * (r + 0.07) + ca * 0.04, z),
                (ca * (r + 0.07), sa * (r + 0.07), z)),
               ((ca * r * 0.9, sa * r * 0.9, z + 0.016), (ca * r * 0.9 - sa * 0.05, sa * r * 0.9 + ca * 0.05, z + 0.016),
                (ca * (r + 0.06) - sa * 0.035, sa * (r + 0.06) + ca * 0.035, z + 0.020),
                (ca * (r + 0.06), sa * (r + 0.06), z + 0.020)), shelf)
    return m.array()


def build_park_bench_mesh():
    m = _MetricMesh("park_bench")
    iron = (0.18, 0.19, 0.20)
    slat = ((0.46, 0.33, 0.20), (0.42, 0.30, 0.18), (0.48, 0.35, 0.21), (0.44, 0.32, 0.19))
    seat_z = 0.207
    back = -0.025
    for sy in (-1, 1):
        y = sy * 0.36
        m.box(0.11, y, seat_z / 2, 0.012, 0.012, seat_z / 2, iron, skip_bottom=True)
        m.hull(((-0.10, y - 0.012, 0.0), (-0.076, y - 0.012, 0.0), (-0.076, y + 0.012, 0.0), (-0.10, y + 0.012, 0.0)),
               ((-0.152 + back, y - 0.012, 0.47), (-0.128 + back, y - 0.012, 0.47), (-0.128 + back, y + 0.012, 0.47),
                (-0.152 + back, y + 0.012, 0.47)), iron)
        m.box(0.0, y, seat_z - 0.016, 0.115, 0.012, 0.01, iron)
        m.box(0.0, y, 0.30, 0.13, 0.016, 0.008, iron)
        m.box(0.11, y, (seat_z + 0.30) / 2, 0.008, 0.008, (0.30 - seat_z) / 2, iron)
    for i, x in enumerate((-0.09, -0.03, 0.03, 0.09)):
        m.box(x, 0.0, seat_z, 0.026, 0.40, 0.009, slat[i])
    for i, z in enumerate((0.29, 0.355, 0.42)):
        lean = -0.088 - (0.052 - back) * z / 0.47
        m.box(lean, 0.0, z, 0.008, 0.40, 0.024, slat[(i + 1) % 4])
    return m.array()


def build_lamppost_mesh():
    m = _MetricMesh("lamppost")
    iron = (0.16, 0.17, 0.18)
    glow = (1.0, 0.88, 0.58)
    m.tube("z", (0.0, 0.0), 0.0, 0.05, 0.075, 0.07, iron, n=8, cap1=True, phase=math.pi / 8)
    m.tube("z", (0.0, 0.0), 0.05, 0.16, 0.055, 0.03, iron, n=8, phase=math.pi / 8)
    m.tube("z", (0.0, 0.0), 0.16, 1.40, 0.022, 0.02, iron, n=8, phase=math.pi / 8)
    for cz in (0.55, 1.05):
        m.ring("z", (0.0, 0.0), cz - 0.012, cz + 0.012, 0.02, 0.03, iron, n=8, phase=math.pi / 8)
    m.tube("z", (0.0, 0.0), 1.40, 1.44, 0.02, 0.06, iron, n=8, cap1=True, phase=math.pi / 8)
    a0, a1 = 0.045, 0.062
    m.hull(((-a0, -a0, 1.44), (a0, -a0, 1.44), (a0, a0, 1.44), (-a0, a0, 1.44)),
           ((-a1, -a1, 1.56), (a1, -a1, 1.56), (a1, a1, 1.56), (-a1, a1, 1.56)), glow)
    for sx in (-1, 1):
        for sy in (-1, 1):
            m.hull(((sx * a0 - 0.006, sy * a0 - 0.006, 1.44), (sx * a0 + 0.006, sy * a0 - 0.006, 1.44),
                    (sx * a0 + 0.006, sy * a0 + 0.006, 1.44), (sx * a0 - 0.006, sy * a0 + 0.006, 1.44)),
                   ((sx * a1 - 0.006, sy * a1 - 0.006, 1.56), (sx * a1 + 0.006, sy * a1 - 0.006, 1.56),
                    (sx * a1 + 0.006, sy * a1 + 0.006, 1.56), (sx * a1 - 0.006, sy * a1 + 0.006, 1.56)), iron)
    c = 0.078
    m.hull(((-c, -c, 1.56), (c, -c, 1.56), (c, c, 1.56), (-c, c, 1.56)),
           ((-0.015, -0.015, 1.63), (0.015, -0.015, 1.63), (0.015, 0.015, 1.63), (-0.015, 0.015, 1.63)), iron,
           cap_bottom=True)
    m.tube("z", (0.0, 0.0), 1.63, 1.67, 0.008, 0.0, iron, n=6)
    return m.array()


def build_vending_mesh():
    v = []
    x0, x1, y0, y1, z0, z1 = -0.5, 0.5, -0.5, 0.5, 0.0, 1.0
    body = (0.30, 0.34, 0.38)
    header = (0.75, 0.18, 0.16)
    header_glow = (1.3, 1.0, 0.35)
    trim = (0.55, 0.56, 0.60)
    button = (0.85, 0.75, 0.30)
    slot = (0.12, 0.12, 0.13)
    products = [(0.75, 0.20, 0.15), (0.20, 0.55, 0.75), (0.85, 0.75, 0.20), (0.30, 0.65, 0.30)]

    _quad(v, (x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (0, 1, 0), body)
    _quad(v, (x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1), (0, -1, 0), body)
    _quad(v, (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (0, 0, 1), body)
    _quad(v, (x0, y1, z0), (x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (-1, 0, 0), body)
    zb = 0.02
    _quad(v, (x0, y1, zb), (x1, y1, zb), (x1, y0, zb), (x0, y0, zb), (0, 0, -1), body)

    wz0, wz1, wy0, wy1 = 0.20, 0.86, y0 + 0.07, y1 - 0.07
    _quad(v, (x1, y0, wz1), (x1, y1, wz1), (x1, y1, z1), (x1, y0, z1), (1, 0, 0), header)
    _mini_box(v, x1 + 0.02, 0.0, (wz1 + z1) / 2, 0.02, 0.35, 0.03, header_glow)
    flap_y0, flap_y1 = y0 + 0.09, y0 + 0.40
    flap_z0, flap_z1 = zb, zb + 0.13
    flap_back = 0.40
    _quad(v, (x1, y0, z0), (x1, y1, z0), (x1, y1, flap_z0), (x1, y0, flap_z0), (1, 0, 0), body)
    _quad(v, (x1, y0, flap_z1), (x1, y1, flap_z1), (x1, y1, wz0), (x1, y0, wz0), (1, 0, 0), body)
    _quad(v, (x1, y0, flap_z0), (x1, flap_y0, flap_z0), (x1, flap_y0, flap_z1), (x1, y0, flap_z1), (1, 0, 0), body)
    _quad(v, (x1, flap_y1, flap_z0), (x1, y1, flap_z0), (x1, y1, flap_z1), (x1, flap_y1, flap_z1), (1, 0, 0), body)
    _quad(v, (x1, flap_y0, flap_z1), (x1, flap_y1, flap_z1), (flap_back, flap_y1, flap_z1), (flap_back, flap_y0, flap_z1), (0, 0, -1), body)
    _quad(v, (x1, flap_y1, flap_z0), (x1, flap_y0, flap_z0), (flap_back, flap_y0, flap_z0), (flap_back, flap_y1, flap_z0), (0, 0, 1), body)
    _quad(v, (x1, flap_y0, flap_z1), (x1, flap_y0, flap_z0), (flap_back, flap_y0, flap_z0), (flap_back, flap_y0, flap_z1), (0, 1, 0), body)
    _quad(v, (x1, flap_y1, flap_z0), (x1, flap_y1, flap_z1), (flap_back, flap_y1, flap_z1), (flap_back, flap_y1, flap_z0), (0, -1, 0), body)
    _quad(v, (flap_back, flap_y0, flap_z0), (flap_back, flap_y1, flap_z0), (flap_back, flap_y1, flap_z1), (flap_back, flap_y0, flap_z1), (1, 0, 0), slot)
    _quad(v, (x1, y0, wz0), (x1, wy0, wz0), (x1, wy0, wz1), (x1, y0, wz1), (1, 0, 0), trim)
    _quad(v, (x1, wy1, wz0), (x1, y1, wz0), (x1, y1, wz1), (x1, wy1, wz1), (1, 0, 0), trim)
    glass_x = 0.47
    _quad(v, (x1, wy0, wz1), (x1, wy1, wz1), (glass_x, wy1, wz1), (glass_x, wy0, wz1), (0, 0, -1), trim)
    _quad(v, (x1, wy1, wz0), (x1, wy0, wz0), (glass_x, wy0, wz0), (glass_x, wy1, wz0), (0, 0, 1), trim)
    _quad(v, (0.30, y0, z0), (0.30, y1, z0), (0.30, y1, z1), (0.30, y0, z1), (1, 0, 0), body)
    rows, cols = 3, 4
    row_h, col_w = (wz1 - wz0) / rows, (wy1 - wy0) / cols
    shelf = (0.20, 0.21, 0.23)
    tag = (0.86, 0.84, 0.76)
    for r in range(rows):
        base_z = wz0 + row_h * r + 0.012
        _mini_box(v, 0.37, 0.0, base_z - 0.006, 0.085, (wy1 - wy0) / 2, 0.006, shelf)
        for c in range(cols):
            cy = wy0 + col_w * (c + 0.5)
            _mini_box(v, 0.4575, cy, base_z - 0.006, 0.0025, col_w * 0.18, 0.005, tag)
            col = products[(r * 2 + c) % len(products)]
            kind = (r + c) % 3
            for k, dy in enumerate((-0.28, 0.28)):
                iy = cy + dy * col_w
                if kind == 0:
                    _mini_box(v, 0.40, iy, base_z + row_h * 0.28, 0.035, col_w * 0.16, row_h * 0.28, col)
                    _mini_box(v, 0.40, iy, base_z + row_h * 0.565, 0.03, col_w * 0.12, row_h * 0.012, (0.75, 0.76, 0.78))
                elif kind == 1:
                    _mini_box(v, 0.40 - 0.01 * k, iy, base_z + row_h * 0.36, 0.022, col_w * 0.2, row_h * 0.36, col)
                else:
                    _mini_box(v, 0.41, iy, base_z + row_h * 0.22, 0.015, col_w * 0.18, row_h * 0.22, col)
    _mini_box(v, x1 + 0.02, y1 - 0.10, 0.13, 0.015, 0.015, 0.045, slot)
    for i in range(3):
        _mini_box(v, x1 + 0.02, y1 - 0.22 - i * 0.07, 0.13, 0.015, 0.02, 0.02, button)
    return np.array(v, dtype="f4")


def build_vending_glass_mesh():
    v = []
    glass = (0.55, 0.75, 0.85)
    y0, y1 = -0.5, 0.5
    wz0, wz1, wy0, wy1 = 0.20, 0.86, y0 + 0.07, y1 - 0.07
    x1 = 0.47
    _quad(v, (x1, wy0, wz0), (x1, wy1, wz0), (x1, wy1, wz1), (x1, wy0, wz1), (1, 0, 0), glass)
    return np.array(v, dtype="f4")


def build_sink_mesh():
    m = _MetricMesh("sink")
    shell = (0.96, 0.96, 0.93)
    inner = (0.56, 0.58, 0.58)
    grout = (0.46, 0.48, 0.46)
    tile = (0.92, 0.95, 0.90)
    chrome = (0.78, 0.80, 0.84)
    dark = (0.10, 0.10, 0.11)
    rust = (0.46, 0.28, 0.14)
    hot, cold = (0.80, 0.22, 0.18), (0.22, 0.36, 0.80)
    wall, front, hw = -0.16, 0.16, 0.24
    top, t = 0.30, 0.035
    m.box(front - t / 2, 0.0, top / 2, t / 2, hw, top / 2, shell, skip_bottom=True)
    m.box(wall + t / 2, 0.0, top / 2, t / 2, hw - t, top / 2, shell, skip_bottom=True)
    for sy in (-1, 1):
        m.box(0.0, sy * (hw - t / 2), top / 2, (front - wall) / 2, t / 2, top / 2, shell, skip_bottom=True)
    lip = 0.006
    m.box(front - t / 2 + lip / 2, 0.0, top + 0.012, t / 2 + lip / 2, hw + lip, 0.012, shell)
    m.box(wall + t / 2, 0.0, top + 0.012, t / 2, hw + lip, 0.012, shell)
    for sy in (-1, 1):
        m.box(0.0, sy * (hw - t / 2 + lip / 2), top + 0.012, (front - wall) / 2 - t, t / 2 + lip / 2, 0.012, shell)
    fz = 0.15
    ix0, ix1, iy = wall + t, front - t, hw - t
    e = 0.003
    lining = (0.76, 0.78, 0.77)
    m.quad((ix0, -iy, fz), (ix1, -iy, fz), (ix1, iy, fz), (ix0, iy, fz), (0, 0, 1), inner)
    m.quad((ix1 - e, -iy, fz), (ix1 - e, iy, fz), (ix1 - e, iy, top), (ix1 - e, -iy, top), (-1, 0, 0), lining)
    m.quad((ix0 + e, iy, fz), (ix0 + e, -iy, fz), (ix0 + e, -iy, top), (ix0 + e, iy, top), (1, 0, 0), lining)
    m.quad((ix0, iy - e, fz), (ix1, iy - e, fz), (ix1, iy - e, top), (ix0, iy - e, top), (0, -1, 0), lining)
    m.quad((ix1, -iy + e, fz), (ix0, -iy + e, fz), (ix0, -iy + e, top), (ix1, -iy + e, top), (0, 1, 0), lining)
    m.box(0.01, 0.0, fz + 0.002, 0.022, 0.022, 0.002, dark)
    m.quad((0.03, -0.06, fz + 0.0015), (0.09, -0.03, fz + 0.0015), (0.08, 0.05, fz + 0.0015), (0.04, 0.02, fz + 0.0015),
           (0, 0, 1), rust)
    sz0, sz1 = top + 0.03, 0.57
    m.box(wall + 0.004, 0.0, (sz0 + sz1) / 2, 0.004, hw, (sz1 - sz0) / 2, grout, skip_back=True)
    cols, rows = 6, 3
    tw, th = 2 * hw / cols, (sz1 - sz0) / rows
    for r in range(rows):
        for c in range(cols):
            m.box(wall + 0.009, -hw + tw * (c + 0.5), sz0 + th * (r + 0.5), 0.002, tw / 2 - 0.004, th / 2 - 0.004, tile,
                  skip_back=True)
    tz = 0.46
    m.box(wall + 0.014, 0.0, tz, 0.003, 0.09, 0.03, chrome, skip_back=True)
    m.tube("x", (0.0, tz - 0.004), wall + 0.017, wall + 0.15, 0.012, 0.012, chrome, n=6)
    m.tube("z", (wall + 0.15, 0.0), tz - 0.075, tz + 0.008, 0.01, 0.01, chrome, n=6, cap0=True, cap1=True)
    for sy, col in ((-0.064, hot), (0.064, cold)):
        m.tube("x", (sy, tz), wall + 0.017, wall + 0.05, 0.008, 0.008, chrome, n=6)
        m.box(wall + 0.052, sy, tz, 0.004, 0.026, 0.005, chrome)
        m.box(wall + 0.057, sy, tz, 0.002, 0.008, 0.008, col)
    return m.array()


def build_trash_can_mesh():
    m = _MetricMesh("trash_can")
    body = (0.34, 0.38, 0.34)
    rim = (0.50, 0.52, 0.50)
    bag = (0.08, 0.08, 0.09)
    litter = ((0.80, 0.78, 0.70), (0.55, 0.30, 0.22))
    m.ring("z", (0.0, 0.0), 0.0, 0.014, 0.10, 0.118, rim, n=12)
    m.tube("z", (0.0, 0.0), 0.014, 0.262, 0.112, 0.126, body, n=12)
    m.tube("z", (0.0, 0.0), 0.02, 0.021, 0.0, 0.112, bag, n=12, cap1=True)
    m.ring("z", (0.0, 0.0), 0.25, 0.264, 0.116, 0.134, bag, n=12)
    m.ring("z", (0.0, 0.0), 0.264, 0.28, 0.118, 0.136, rim, n=12)
    m.tube("z", (0.0, 0.0), 0.20, 0.24, 0.10, 0.10, bag, n=12, cap1=True)
    m.hull(((-0.05, -0.04, 0.24), (0.02, -0.05, 0.24), (0.03, 0.02, 0.24), (-0.04, 0.03, 0.24)),
           ((-0.03, -0.02, 0.30), (0.01, -0.03, 0.31), (0.02, 0.01, 0.30), (-0.02, 0.015, 0.29)), litter[0])
    m.hull(((0.03, 0.02, 0.24), (0.08, 0.03, 0.24), (0.07, 0.07, 0.24), (0.03, 0.06, 0.24)),
           ((0.045, 0.035, 0.28), (0.07, 0.04, 0.275), (0.065, 0.06, 0.28), (0.045, 0.055, 0.285)), litter[1])
    return m.array()


def build_crate_mesh():
    m = _MetricMesh("crate")
    post = (0.46, 0.34, 0.20)
    planks = ((0.62, 0.47, 0.28), (0.56, 0.42, 0.25), (0.66, 0.50, 0.30))
    inside = (0.12, 0.09, 0.06)
    h = PROP_DEFS["crate"]["height"]
    a = 0.26
    lid = 0.022
    m.box(0.0, 0.0, (h - lid) / 2, a - 0.02, a - 0.02, (h - lid) / 2, inside, skip_bottom=True)
    for sx in (-1, 1):
        for sy in (-1, 1):
            m.box(sx * (a - 0.018), sy * (a - 0.018), (h - lid) / 2, 0.018, 0.018, (h - lid) / 2, post, skip_bottom=True)
    rows = 3
    gap = 0.012
    ph = (h - lid - gap * (rows + 1)) / rows
    for r in range(rows):
        z = gap + ph / 2 + r * (ph + gap)
        col = planks[r % 3]
        for sx in (-1, 1):
            m.box(sx * (a - 0.012), 0.0, z, 0.008, a - 0.036, ph / 2, col)
            m.box(0.0, sx * (a - 0.012), z, a - 0.036, 0.008, ph / 2, planks[(r + 1) % 3])
    n = 4
    w = 2 * a / n
    for i in range(n):
        m.box(-a + w * (i + 0.5), 0.0, h - lid / 2, w / 2 - 0.004, a, lid / 2, planks[i % 3])
    return m.array()


def build_barrel_mesh():
    m = _MetricMesh("barrel")
    paint = (0.44, 0.52, 0.64)
    steel = (0.60, 0.61, 0.63)
    rust = (0.46, 0.29, 0.16)
    band = (0.86, 0.70, 0.26)
    lid = (0.40, 0.47, 0.58)
    n = 14
    R = 0.18
    m.tube("z", (0, 0), 0.0, 0.016, R + 0.007, R + 0.007, steel, n)
    for z0, z1, col in ((0.016, 0.075, rust), (0.075, 0.139, paint), (0.157, 0.2, paint), (0.2, 0.236, band),
                        (0.236, 0.283, paint), (0.301, 0.421, paint)):
        m.tube("z", (0, 0), z0, z1, R, R, col, n)
    for hz in (0.148, 0.292):
        m.tube("z", (0, 0), hz - 0.009, hz - 0.004, R, R + 0.008, steel, n)
        m.tube("z", (0, 0), hz - 0.004, hz + 0.004, R + 0.008, R + 0.008, steel, n)
        m.tube("z", (0, 0), hz + 0.004, hz + 0.009, R + 0.008, R, steel, n)
    m.tube("z", (0, 0), 0.421, 0.44, R + 0.007, R + 0.007, steel, n)
    m.annulus_z((0, 0), 0.44, R - 0.006, R + 0.007, steel, n)
    m.tube("z", (0, 0), 0.434, 0.44, R - 0.006, R - 0.006, steel, n)
    m.tube("z", (0, 0), 0.433, 0.434, R - 0.006, R - 0.006, lid, n, cap1=True)
    m.tube("z", (0.075, 0.07), 0.434, 0.4365, 0.024, 0.024, steel, 8, cap1=True)
    m.tube("z", (0.075, 0.07), 0.4365, 0.438, 0.011, 0.011, rust, 6, cap1=True)
    m.tube("z", (-0.09, -0.05), 0.434, 0.437, 0.014, 0.014, steel, 8, cap1=True)
    return m.array()


_ELBOW_PLANES = {"xy": (0, 1, 2), "xz": (0, 2, 1)}


def _elbow(m, plane, cu, cv, fixed, r_bend, r_pipe, a0, a1, color, n_seg=6, n_side=8):
    iu, iv, iw = _ELBOW_PLANES[plane]

    def ring(a):
        pu, pv = cu + math.cos(a) * r_bend, cv + math.sin(a) * r_bend
        ru, rv = math.cos(a), math.sin(a)
        pts = []
        for i in range(n_side):
            t = math.tau * i / n_side + math.pi / n_side
            q = [0.0, 0.0, 0.0]
            q[iu] = pu + ru * math.cos(t) * r_pipe
            q[iv] = pv + rv * math.cos(t) * r_pipe
            q[iw] = fixed + math.sin(t) * r_pipe
            pts.append(tuple(q))
        axis = [0.0, 0.0, 0.0]
        axis[iu], axis[iv], axis[iw] = pu, pv, fixed
        return pts, tuple(axis)

    prev, prev_axis = ring(a0)
    for step in range(1, n_seg + 1):
        a = a0 + (a1 - a0) * step / n_seg
        cur, cur_axis = ring(a)
        mid = tuple((prev_axis[k] + cur_axis[k]) * 0.5 for k in range(3))
        for i in range(n_side):
            j = (i + 1) % n_side
            m.face((prev[i], prev[j], cur[j], cur[i]), color, mid)
        prev, prev_axis = cur, cur_axis


def _elbow_xy(m, cx, cy, z, r_bend, r_pipe, a0, a1, color, n_seg=6, n_side=8):
    _elbow(m, "xy", cx, cy, z, r_bend, r_pipe, a0, a1, color, n_seg, n_side)


PIPE_ENDS = ("bend", "join", "wall")


def build_pipes_mesh(neg="bend", pos="bend"):
    m = _MetricMesh("pipes")
    wall = -0.14
    clamp = (0.24, 0.24, 0.26)
    wheel = (0.78, 0.20, 0.16)
    runs = ((0.56, 0.030, (0.72, 0.40, 0.32)), (0.35, 0.044, (0.62, 0.64, 0.66)), (0.13, 0.027, (0.40, 0.52, 0.70)))
    bend = 0.055
    y_neg = -0.5 if neg != "bend" else -0.30
    y_pos = 0.5 if pos != "bend" else 0.30
    for z, r, col in runs:
        x = wall + 0.058 + r
        m.tube("y", (x, z), y_neg, y_pos, r, r, col, n=8, phase=math.pi / 8)
        for y_end, mode, sgn in ((y_neg, neg, -1.0), (y_pos, pos, 1.0)):
            if mode == "join":
                continue
            if mode == "wall":
                m.tube("y", (x, z), y_end - sgn * 0.030, y_end - sgn * 0.010,
                       r + 0.016, r + 0.016, clamp, n=10,
                       cap0=sgn < 0, cap1=sgn > 0, phase=math.pi / 10)
                continue
            _elbow_xy(m, x - bend, y_end, z, bend, r, 0.0, sgn * math.pi / 2, col)
            wall_y = y_end + sgn * bend
            m.tube("x", (wall_y, z), wall + 0.004, x - bend, r, r, col, n=8, phase=math.pi / 8)
            m.tube("x", (wall_y, z), wall, wall + 0.016, r + 0.016, r + 0.016, clamp, n=10,
                   cap1=True, phase=math.pi / 10)
        if neg == "join" and pos == "join":
            m.tube("y", (x, z), -0.018, 0.018, r + 0.008, r + 0.008, col, n=8, phase=math.pi / 8)
    for cy in (-0.16, 0.16):
        m.box(wall + 0.007, cy, 0.345, 0.007, 0.016, 0.25, clamp, skip_back=True)
        for z, r, _ in runs:
            x = wall + 0.058 + r
            stay0 = wall + 0.014
            m.box((stay0 + x) * 0.5, cy, z, (x - stay0) * 0.5, 0.010, 0.008, clamp)
            m.box(x, cy, z, r + 0.008, 0.013, r + 0.008, clamp)
    if neg == "bend" and pos == "bend":
        z, r, _ = runs[1]
        x = wall + 0.058 + r
        wx = x + r + 0.028
        m.tube("x", (0.0, z), x + r - 0.004, wx, 0.006, 0.006, clamp, n=6)
        spokes = 10
        for i in range(spokes):
            a = math.tau * i / spokes
            m.box(wx, 0.034 * math.cos(a), z + 0.034 * math.sin(a), 0.004, 0.009, 0.009, wheel)
        m.box(wx, 0.0, z, 0.003, 0.032, 0.004, wheel)
        m.box(wx, 0.0, z, 0.003, 0.004, 0.032, wheel)
    return m.array()


def build_fuse_box_mesh():
    m = _MetricMesh("fuse_box")
    body = (1.0, 1.0, 1.0)
    inside = (0.20, 0.19, 0.17)
    ceramic = (0.88, 0.86, 0.80)
    brass = (0.70, 0.58, 0.30)
    yellow = (1.0, 0.86, 0.20)
    black = (0.06, 0.06, 0.06)
    lever = (0.72, 0.14, 0.10)
    wall, front, hy = -0.16, 0.08, 0.24
    z0, z1, top = 0.06, 0.52, 0.44
    t = 0.02
    m.box(wall + t / 2, 0.0, (z0 + z1) / 2, t / 2, hy, (z1 - z0) / 2, inside, skip_back=True)
    for sy in (-1, 1):
        m.box((wall + t + front) / 2, sy * (hy - t / 2), (z0 + z1) / 2, (front - wall - t) / 2, t / 2, (z1 - z0) / 2, body)
    m.box((wall + t + front) / 2, 0.0, z0 + t / 2, (front - wall - t) / 2, hy - t, t / 2, body)
    m.box((wall + t + front) / 2, 0.0, z1 - t / 2, (front - wall - t) / 2, hy - t, t / 2, body)
    m.box(front - t / 2, 0.0, (top + z1 - t) / 2, t / 2, hy - t, (z1 - t - top) / 2, body)
    holder = (0.46, 0.44, 0.40)
    for sy in (-0.11, 0.0, 0.11):
        m.box(wall + t + 0.014, sy, 0.25, 0.014, 0.034, 0.09, holder)
        for zc in (0.183, 0.317):
            m.box(wall + t + 0.030, sy, zc, 0.006, 0.016, 0.010, brass)
    m.box(front + 0.002, 0.0, 0.48, 0.002, 0.05, 0.03, yellow)
    for dy, dz, ay, az in ((0.01, 0.494, 0.006, 0.01), (0.0, 0.48, 0.012, 0.004), (-0.01, 0.466, 0.006, 0.01)):
        m.box(front + 0.0055, dy, dz, 0.0015, ay, az, black)
    m.box(0.0, hy + 0.02, 0.33, 0.04, 0.02, 0.05, body)
    m.box(0.02, hy + 0.045, 0.36, 0.008, 0.005, 0.035, lever)
    for sy in (-0.12, 0.12):
        m.tube("z", (wall + 0.06, sy), z1, S.WALL_HEIGHT, 0.014, 0.014, body, n=8)
    return m.array()


def build_valve_panel_mesh():
    m = _MetricMesh("valve_panel")
    steel = (0.60, 0.60, 0.62)
    frame = (0.30, 0.30, 0.32)
    pipe = (0.50, 0.48, 0.44)
    wheel = (0.78, 0.14, 0.10)
    warn = (0.90, 0.74, 0.18)
    black = (0.08, 0.08, 0.08)
    face = (0.92, 0.90, 0.84)
    wall = -0.18
    front = 0.02
    m.box((wall + front) / 2, 0.0, 0.33, (front - wall) / 2, 0.26, 0.25, steel, skip_back=True)
    m.box(front + 0.004, 0.0, 0.33, 0.004, 0.27, 0.26, frame, skip_back=True)
    m.box(front + 0.010, 0.0, 0.33, 0.003, 0.235, 0.225, steel, skip_back=True)
    px, bend, pr = -0.08, 0.055, 0.04
    m.tube("y", (px, 0.20), -0.30, -0.26, pr, pr, pipe, n=10)
    _elbow(m, "xy", px - bend, -0.30, 0.20, bend, pr, 0.0, -math.pi / 2, pipe)
    m.tube("x", (-0.30 - bend, 0.20), wall + 0.004, px - bend, pr, pr, pipe, n=10)
    m.tube("x", (-0.30 - bend, 0.20), wall, wall + 0.016, pr + 0.016, pr + 0.016,
           frame, n=10, cap1=True, phase=math.pi / 10)
    m.tube("z", (px, 0.12), 0.58, 0.62, pr, pr, pipe, n=10)
    m.tube("z", (px, 0.12), 0.58, 0.595, 0.052, 0.052, frame, n=10, cap1=True)
    _elbow(m, "xz", px - bend, 0.62, 0.12, bend, pr, 0.0, math.pi / 2, pipe)
    m.tube("x", (0.12, 0.62 + bend), wall + 0.004, px - bend, pr, pr, pipe, n=10)
    m.tube("x", (0.12, 0.62 + bend), wall, wall + 0.016, pr + 0.016, pr + 0.016,
           frame, n=10, cap1=True, phase=math.pi / 10)
    hx = front + 0.045
    hub = (0.0, 0.33)
    m.tube("x", hub, front + 0.013, hx, 0.018, 0.018, frame, n=8)
    m.ring("x", hub, hx - 0.008, hx + 0.008, 0.10, 0.118, wheel, n=16)
    for k in range(4):
        m.spoke("x", hub, hx, 0.02, 0.10, math.tau * k / 4 + 0.4, 0.008, wheel)
    m.tube("x", hub, hx - 0.01, hx + 0.012, 0.026, 0.026, wheel, n=8, cap0=True, cap1=True)
    m.box(front + 0.015, 0.0, 0.10, 0.002, 0.14, 0.03, warn)
    for k in range(5):
        m.box(front + 0.018, -0.11 + k * 0.055, 0.10, 0.0015, 0.012, 0.028, black)
    m.tube("x", (0.17, 0.52), front + 0.013, front + 0.035, 0.04, 0.04, frame, n=10)
    m.tube("x", (0.17, 0.52), front + 0.035, front + 0.036, 0.032, 0.032, face, n=10, cap1=True)
    m.spoke("x", (0.17, 0.52), front + 0.039, -0.004, 0.026, 1.0, 0.002, wheel)
    for i in range(3):
        sy, cz = -0.195, 0.52 - i * 0.13
        m.box(front + 0.020, sy, cz, 0.008, 0.024, 0.024, frame)
        m.box(front + 0.038, sy, cz, 0.010, 0.014, 0.014, frame)
        m.box(front + 0.049, sy, cz, 0.002, 0.009, 0.009, black)
    return m.array()


def _padlock(v, k, cx, cy, cz, body, shackle, dark, open_shackle=False):
    def box(x, y, z, hx, hy, hz, col):
        _mini_box(v, cx + x / k[0], cy + y / k[1], cz + z / k[2],
                  hx / k[0], hy / k[1], hz / k[2], col)
    box(0.0, 0.0, 0.0, 0.014, 0.030, 0.038, body)
    box(0.014, 0.0, 0.0, 0.003, 0.025, 0.033, tuple(min(1.0, c * 1.25) for c in body))
    box(0.017, 0.0, -0.006, 0.003, 0.009, 0.012, dark)
    arm = 0.020 if not open_shackle else 0.038
    lean = 0.022 if open_shackle else 0.0
    box(lean, -0.021, 0.038 + arm / 2, 0.007, 0.007, arm / 2, shackle)
    box(0.0, 0.021, 0.038 + 0.010, 0.007, 0.007, 0.010, shackle)
    box(lean / 2, 0.0, 0.038 + arm, 0.007, 0.028, 0.007, shackle)


def build_shed_lock_mesh(locks=2):
    v = []
    x0, x1, y0, y1, z0, z1 = -0.5, 0.5, -0.5, 0.5, 0.0, 1.0
    k = (2 * PROP_DEFS["shed_lock"]["hd"], 2 * PROP_DEFS["shed_lock"]["hw"],
         PROP_DEFS["shed_lock"]["height"])
    frame = (0.5, 0.46, 0.40)
    plank_a = (0.40, 0.31, 0.21)
    plank_b = (0.34, 0.26, 0.17)
    brace = (0.46, 0.36, 0.24)
    lock_body = (0.26, 0.25, 0.27)
    lock_body2 = (0.34, 0.28, 0.20)
    shackle = (0.60, 0.60, 0.63)
    dark = (0.05, 0.05, 0.05)
    hasp = (0.30, 0.30, 0.32)
    _quad(v, (x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (0, 1, 0), frame)
    _quad(v, (x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1), (0, -1, 0), frame)
    _quad(v, (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (0, 0, 1), frame)
    planks = 6
    for i in range(planks):
        ya = y0 + i / planks
        yb = y0 + (i + 1) / planks
        col = plank_a if i % 2 == 0 else plank_b
        for fx, order in ((x1, 1), (x0, -1)):
            if order > 0:
                _quad(v, (fx, ya, z0), (fx, yb, z0), (fx, yb, z1), (fx, ya, z1), (1, 0, 0), col)
            else:
                _quad(v, (fx, yb, z0), (fx, ya, z0), (fx, ya, z1), (fx, yb, z1), (-1, 0, 0), col)
    for fx in (x1 + 0.03, x0 - 0.03):
        _mini_box(v, fx, 0.0, 0.18, 0.03, 0.46, 0.05, brace)
        _mini_box(v, fx, 0.0, 0.82, 0.03, 0.46, 0.05, brace)
        steps = 7
        for kk in range(steps):
            t = (kk + 0.5) / steps
            _mini_box(v, fx, -0.40 + 0.80 * t, 0.24 + 0.52 * t, 0.028, 0.07, 0.045, brace)
    for z_h in (0.62, 0.34):
        _mini_box(v, x1 + 0.020, -0.27, z_h, 0.013 / k[0], 0.15, 0.026 / k[2], hasp)
        for sy in (-0.14, -0.39):
            _mini_box(v, x1 + 0.030, sy, z_h, 0.006 / k[0], 0.012, 0.010 / k[2], shackle)
        _mini_box(v, x1 + 0.016, -0.448, z_h, 0.016 / k[0], 0.038, 0.036 / k[2], hasp)
        _mini_box(v, x1 + 0.034, -0.420, z_h, 0.009 / k[0], 0.016, 0.019 / k[2], hasp)
    for i, (z_h, col) in enumerate(((0.62, lock_body), (0.34, lock_body2))):
        if i >= 2 - locks:
            _padlock(v, k, x1 + 0.048 / k[0], -0.420, z_h, col, shackle, dark)
    return np.array(v, dtype="f4")


def build_shed_lock_one_mesh():
    return build_shed_lock_mesh(1)


def build_shed_lock_open_mesh():
    return build_shed_lock_mesh(0)


def build_whetstone_mesh():
    m = _MetricMesh("whetstone")
    stone = (0.52, 0.50, 0.47)
    worn = (0.36, 0.35, 0.33)
    wood = (0.40, 0.30, 0.19)
    m.box(-0.008, 0.0, 0.012, 0.018, 0.046, 0.011, stone)
    m.box(-0.008, 0.0, 0.0225, 0.014, 0.040, 0.002, worn)
    m.box(0.016, 0.0, 0.012, 0.007, 0.042, 0.012, wood)
    return m.array()


def build_padlock_dropped_mesh():
    v = []
    k = (2 * PROP_DEFS["padlock_dropped"]["hd"], 2 * PROP_DEFS["padlock_dropped"]["hw"],
         PROP_DEFS["padlock_dropped"]["height"])
    body = (0.28, 0.27, 0.29)
    shackle = (0.62, 0.62, 0.65)
    dark = (0.05, 0.05, 0.05)

    def box(x, y, z, hx, hy, hz, col):
        _mini_box(v, x / k[0], y / k[1], z / k[2], hx / k[0], hy / k[1], hz / k[2], col)

    box(0.0, 0.0, 0.014, 0.038, 0.030, 0.014, body)
    box(0.0, 0.0, 0.029, 0.033, 0.025, 0.003, tuple(min(1.0, c * 1.25) for c in body))
    box(-0.006, 0.0, 0.031, 0.012, 0.009, 0.002, dark)
    box(0.048, -0.021, 0.008, 0.010, 0.007, 0.008, shackle)
    box(0.038, 0.021, 0.008, 0.008, 0.007, 0.008, shackle)
    box(0.058, -0.004, 0.008, 0.008, 0.026, 0.008, shackle)
    return np.array(v, dtype="f4")


ELEVATOR_OPENING_HALF = 0.32
ELEVATOR_OPENING_H = 0.95
ELEVATOR_CABIN_BACK = 0.98
ELEVATOR_CABIN_HALF = 0.47
ELEVATOR_CABIN_CEIL = 1.10
ELEVATOR_DOOR_T = 0.006
ELEVATOR_DOOR_DEPTHS = (-0.024, -0.010)
ELEVATOR_JAMB = 0.17
ELEVATOR_FRONT_WALL = 0.04


def build_elevator_mesh():
    m = _MetricMesh("elevator")
    hd = PROP_DEFS["elevator"]["hd"]
    wall = -hd
    frame = (0.62, 0.64, 0.66)
    plate = (0.10, 0.10, 0.11)
    button = (0.78, 0.62, 0.22)
    steel = (0.52, 0.53, 0.56)
    oh, h = ELEVATOR_OPENING_HALF, ELEVATOR_OPENING_H
    top = S.WALL_HEIGHT - 0.03
    d = 0.02
    back = wall - 0.005
    jamb = ELEVATOR_JAMB
    for sy in (-1, 1):
        m.box((back + wall + 2 * d) / 2, sy * (oh + jamb / 2), h / 2, (wall + 2 * d - back) / 2, jamb / 2, h / 2, frame,
              skip_back=True)
    m.box((back + wall + 2 * d) / 2, 0.0, (h + top) / 2, (wall + 2 * d - back) / 2, oh + jamb, (top - h) / 2, frame,
          skip_back=True)
    m.box(wall + 2 * d + 0.0015, 0.0, 1.04, 0.0015, 0.12, 0.035, plate)
    m.box(wall + 2 * d + 0.004, -(oh + jamb / 2), 0.46, 0.004, 0.03, 0.05, steel, skip_back=True)
    m.tube("x", (-(oh + jamb / 2), 0.47), wall + 2 * d + 0.008, wall + 2 * d + 0.014, 0.012, 0.012, button, n=8, cap1=True)
    m.box(wall + 0.025, 0.0, 0.004, 0.025, oh, 0.004, steel, skip_bottom=True, skip_back=True)
    return m.array()


def build_elevator_cabin_mesh():
    m = _MetricMesh("elevator")
    hd = PROP_DEFS["elevator"]["hd"]
    wall = -hd
    front = wall - ELEVATOR_FRONT_WALL
    back = wall - ELEVATOR_CABIN_BACK
    hy, ceil = ELEVATOR_CABIN_HALF, ELEVATOR_CABIN_CEIL
    oh, oz = ELEVATOR_OPENING_HALF, ELEVATOR_OPENING_H
    floor_c = (0.30, 0.30, 0.32)
    wall_hi = (0.86, 0.86, 0.84)
    wall_lo = (0.62, 0.62, 0.62)
    seam = (0.40, 0.40, 0.42)
    ceil_c = (0.55, 0.55, 0.54)
    light = (0.88, 0.86, 0.79)
    rail = (0.82, 0.82, 0.83)
    panel_c = (0.46, 0.46, 0.48)
    lit_btn = (1.0, 0.86, 0.45)
    fz = 0.012
    mid = 0.55
    m.quad((back, -hy, fz), (front, -hy, fz), (front, hy, fz), (back, hy, fz), (0, 0, 1), floor_c)
    m.quad((back, -hy, ceil), (front, -hy, ceil), (front, hy, ceil), (back, hy, ceil), (0, 0, -1), ceil_c)
    m.quad((back + 0.25, -0.2, ceil - 0.004), (front - 0.25, -0.2, ceil - 0.004), (front - 0.25, 0.2, ceil - 0.004),
           (back + 0.25, 0.2, ceil - 0.004), (0, 0, -1), light)
    for sy in (-1, 1):
        y = sy * hy
        m.quad((back, y, fz), (front, y, fz), (front, y, mid), (back, y, mid), (0, -sy, 0), wall_lo)
        m.quad((back, y, mid), (front, y, mid), (front, y, ceil), (back, y, ceil), (0, -sy, 0), wall_hi)
        for k in (1, 2):
            x = back + (front - back) * k / 3
            m.box(x, y - sy * 0.0035, (fz + ceil) / 2, 0.004, 0.0035, (ceil - fz) / 2, seam)
    m.quad((back, -hy, fz), (back, hy, fz), (back, hy, mid), (back, -hy, mid), (1, 0, 0), wall_lo)
    m.quad((back, -hy, mid), (back, hy, mid), (back, hy, ceil), (back, -hy, ceil), (1, 0, 0), wall_hi)
    m.box(back + 0.025, 0.0, 0.42, 0.012, hy - 0.06, 0.012, rail)
    for sy in (-1, 1):
        m.box(back + 0.012, sy * (hy - 0.06), 0.42, 0.012, 0.01, 0.01, rail)
    for sy in (-1, 1):
        m.quad((front, sy * oh, fz), (front, sy * hy, fz), (front, sy * hy, ceil), (front, sy * oh, ceil), (-1, 0, 0), wall_hi)
        m.quad((front, sy * oh, fz), (wall, sy * oh, fz), (wall, sy * oh, oz), (front, sy * oh, oz), (0, -sy, 0), wall_lo)
    m.quad((front, -oh, oz), (front, oh, oz), (front, oh, ceil), (front, -oh, ceil), (-1, 0, 0), wall_hi)
    m.quad((front, -oh, oz), (wall, -oh, oz), (wall, oh, oz), (front, oh, oz), (0, 0, -1), wall_lo)
    px = front - 0.12
    m.box(px, -hy + 0.008, 0.52, 0.06, 0.008, 0.13, panel_c)
    for k in range(4):
        m.box(px, -hy + 0.018, 0.44 + k * 0.05, 0.012, 0.002, 0.012, lit_btn if k == 1 else rail)
    m.box(front - 0.004, 0.0, 1.035, 0.004, 0.10, 0.03, (0.06, 0.06, 0.07))
    return m.array()


HATCH_HALF = 0.34
HATCH_SHAFT_RISE = 0.62
HATCH_LID_ANGLE = 1.45
HATCH_RIM_H = 0.065
HATCH_LID_T = 0.028


def _hatch_shaft(m, z0, z1):
    conc = (0.26, 0.25, 0.24)
    conc_side = (0.21, 0.20, 0.19)
    rung = (0.44, 0.36, 0.27)
    rail = (0.34, 0.30, 0.26)
    t = 0.05
    half, mid, span = HATCH_HALF, (z0 + z1) / 2.0, (z1 - z0) / 2.0
    for sy in (-1, 1):
        m.box(0.0, sy * (half + t / 2), mid, half + t, t / 2, span, conc, skip_bottom=True)
    for sx in (-1, 1):
        m.box(sx * (half + t / 2), 0.0, mid, t / 2, half, span, conc_side, skip_bottom=True)
    for sy in (-1, 1):
        m.box(half - 0.022, sy * 0.10, mid, 0.012, 0.014, span, rail)
    zz = z0 + 0.09
    while zz < z1 - 0.04:
        m.box(half - 0.030, 0.0, zz, 0.014, 0.10, 0.011, rung)
        zz += 0.145


def _hatch_collar(m, face_z, up):
    frame = (0.30, 0.28, 0.25)
    h = PROP_DEFS["hatch"]["height"]
    half = HATCH_HALF
    for sx, sy, hx, hy in ((0.0, half - 0.03, half, 0.03), (0.0, -half + 0.03, half, 0.03),
                           (half - 0.03, 0.0, 0.03, half - 0.06), (-half + 0.03, 0.0, 0.03, half - 0.06)):
        m.box(sx, sy, face_z + up * h / 2, hx, hy, h / 2, frame)


def _hatch_rim(m, ground_z):
    kerb = (0.28, 0.27, 0.25)
    half, t, hgt = HATCH_HALF, 0.055, HATCH_RIM_H
    for sy in (-1, 1):
        m.box(0.0, sy * (half + t / 2), ground_z + hgt / 2, half + t, t / 2, hgt / 2, kerb,
              skip_bottom=True)
    for sx in (-1, 1):
        m.box(sx * (half + t / 2), 0.0, ground_z + hgt / 2, t / 2, half, hgt / 2, kerb,
              skip_bottom=True)


def _hatch_column(m, ground_z):
    h = PROP_DEFS["hatch"]["height"]
    ceil_face = ground_z - HATCH_SHAFT_RISE - h
    _hatch_collar(m, ceil_face, 1.0)
    _hatch_shaft(m, ceil_face + h, ground_z)
    _hatch_rim(m, ground_z)
    return ceil_face


def hatch_lid_face_z(kind):
    h = PROP_DEFS["hatch"]["height"]
    if kind == "hatch":
        return 0.0
    return -(HATCH_SHAFT_RISE + h)


def build_hatch_mesh():
    m = _MetricMesh("hatch")
    _hatch_column(m, PROP_DEFS["hatch"]["height"] + HATCH_SHAFT_RISE)
    return m.array()


def build_hatch_arrival_mesh():
    m = _MetricMesh("hatch")
    _hatch_column(m, 0.0)
    return m.array()


def build_asylum_echo_mesh():
    d = PROP_DEFS["asylum_echo"]
    hw, hd, h = d["hw"], d["hd"], d["height"]
    m = _MetricMesh("asylum_echo")
    wall = (0.13, 0.13, 0.15)
    wall_hi = (0.17, 0.17, 0.19)
    window = (0.80, 0.76, 0.56)
    window_dark = (0.16, 0.17, 0.20)
    body = h - 0.9
    m.box(0.0, 0.0, 0.45 + body / 2, hd * 0.6, hw - 1.0, body / 2, wall)
    m.box(0.0, 0.0, 0.45 + body + 0.18, hd * 0.75, hw - 0.8, 0.18, wall_hi)
    for sy in (-1, 1):
        m.box(0.0, sy * (hw - 2.2), 0.45 + body * 0.62, hd * 0.68, 1.6, body * 0.62, wall)
    rng = random.Random(0x9A5D)
    rows = max(3, int(body / 1.1))
    for r in range(rows):
        row_z = 1.1 + r * (body - 0.9) / max(1, rows - 1)
        yy = -hw + 1.4
        while yy <= hw - 1.4:
            lit = rng.random() < 0.30
            m.box(hd * 0.62, yy, row_z, 0.05, 0.28, 0.34, window if lit else window_dark)
            yy += 1.05
    return m.array()


def build_hatch_lid_mesh():
    m = _MetricMesh(dims=(1.0, 1.0, 1.0))
    plate = (0.52, 0.42, 0.30)
    rib = (0.40, 0.33, 0.24)
    wheel = (0.70, 0.16, 0.10)
    hinge = (0.22, 0.21, 0.20)
    half = HATCH_HALF
    cx0 = half

    def box(x, y, d0, d1, hx, hy, col):
        m.box(cx0 + x, y, (d0 + d1) / 2.0, hx, hy, abs(d1 - d0) / 2.0, col)

    box(0.0, 0.0, 0.012, 0.03, half - 0.062, half - 0.062, plate)
    box(0.0, 0.0, 0.0, 0.012, 0.018, half - 0.09, rib)
    box(0.0, 0.0, 0.0, 0.012, half - 0.09, 0.018, rib)
    for sy in (-0.16, 0.16):
        box(-half + 0.075, sy, -0.008, 0.012, 0.03, 0.05, hinge)
    m.tube("z", (cx0, 0.0), -0.05, 0.0, 0.014, 0.014, rib, n=8)
    return m.array()


def build_hatch_wheel_mesh():
    m = _MetricMesh(dims=(1.0, 1.0, 1.0))
    wheel = (0.70, 0.16, 0.10)
    m.ring("z", (0.0, 0.0), -0.058, -0.042, 0.10, 0.12, wheel, n=16)
    for k in range(3):
        m.spoke("z", (0.0, 0.0), -0.05, 0.012, 0.10, math.tau * k / 3, 0.008, wheel)
    return m.array()


def hatch_wheel_model(p, angle, face_z, spin):
    theta = p.facing
    ct, st = math.cos(theta), math.sin(theta)
    ca, sa = math.cos(angle), math.sin(angle)
    cs, ss = math.cos(spin), math.sin(spin)
    half = HATCH_HALF
    ex = (ct * ca, st * ca, sa)
    ey = (-st, ct, 0.0)
    ez = (-ct * sa, -st * sa, ca)
    ox = p.x - ct * half + ex[0] * half
    oy = p.y - st * half + ex[1] * half
    oz = p.z0 + face_z + ex[2] * half
    return _PACK_MAT4(ex[0] * cs + ey[0] * ss, ex[1] * cs + ey[1] * ss, ex[2] * cs + ey[2] * ss, 0.0,
                      -ex[0] * ss + ey[0] * cs, -ex[1] * ss + ey[1] * cs, -ex[2] * ss + ey[2] * cs, 0.0,
                      ez[0], ez[1], ez[2], 0.0,
                      ox, oy, oz, 1.0)


def hatch_lid_model(p, angle, face_z):
    theta = p.facing
    ct, st = math.cos(theta), math.sin(theta)
    ca, sa = math.cos(angle), math.sin(angle)
    half = HATCH_HALF
    return _PACK_MAT4(ct * ca, st * ca, sa, 0.0,
                      -st, ct, 0.0, 0.0,
                      -ct * sa, -st * sa, ca, 0.0,
                      p.x - ct * half, p.y - st * half, p.z0 + face_z, 1.0)


FENCE_GAP_HOLE_HW = 0.26
FENCE_GAP_HOLE_TOP = 0.42


def _fence_gap_panels(cut, stage=0):
    d = PROP_DEFS["fence_gap"]
    hd, hw, h = d["hd"], d["hw"], d["height"]
    px = -hd
    mesh = (0.92, 0.94, 0.95)
    patch = (0.62, 0.45, 0.32)
    top = h - 0.03
    hy, hz = FENCE_GAP_HOLE_HW, FENCE_GAP_HOLE_TOP
    v = []

    def panel(y0, y1, z0, z1, x, color, lean=0.0):
        _quad(v, (x, y0, z0), (x + lean, y1, z0), (x + lean, y1, z1), (x, y0, z1), (-1.0, 0.0, 0.0), color,
              uv_scale=(y1 - y0, z1 - z0), uv_offset=(y0 + 0.5, z0))

    panel(-hw, hw, hz, top, px, mesh)
    panel(-hw, -hy, 0.02, hz, px, mesh)
    panel(hy, hw, 0.02, hz, px, mesh)
    if cut:
        for sy in (-1, 1):
            _quad(v, (px, sy * hy, 0.03), (px - 0.22, sy * (hy - 0.06), 0.05),
                  (px - 0.22, sy * (hy - 0.06), hz - 0.05), (px, sy * hy, hz), (-1.0, 0.0, 0.0), mesh,
                  uv_scale=(0.24, hz), uv_offset=(sy * hy + 0.5, 0.03))
    elif stage < 2:
        lean = 0.0 if stage == 0 else 0.13
        _quad(v, (px + 0.018 + lean, -hy - 0.03, 0.0), (px + 0.018 + lean, hy + 0.03, 0.0),
              (px + 0.018, hy + 0.03, hz + 0.04), (px + 0.018, -hy - 0.03, hz + 0.04),
              (-1.0, 0.0, 0.0), patch,
              uv_scale=(2 * hy + 0.06, hz + 0.04), uv_offset=(-hy + 0.47, 0.0))
    else:
        panel(-hy, hy, 0.02, hz, px, mesh)
        _quad(v, (px + 0.30, -hy - 0.03, 0.0), (px + 0.30, hy + 0.03, 0.0),
              (px + 0.04, hy + 0.03, 0.055), (px + 0.04, -hy - 0.03, 0.055),
              (0.0, 0.0, 1.0), patch,
              uv_scale=(2 * hy + 0.06, hz + 0.04), uv_offset=(-hy + 0.47, 0.0))
    return np.array(v, dtype="f4")


def build_fence_gap_panel_mesh():
    return _fence_gap_panels(False)


def build_fence_gap_cut1_panel_mesh():
    return _fence_gap_panels(False, stage=1)


def build_fence_gap_cut2_panel_mesh():
    return _fence_gap_panels(False, stage=2)


def build_fence_gap_open_panel_mesh():
    return _fence_gap_panels(True)


def _fence_gap_details(cut, stage=0):
    d = PROP_DEFS["fence_gap"]
    hd, h = d["hd"], d["height"]
    px = -hd
    wire = (0.30, 0.29, 0.27)
    rust = (0.42, 0.26, 0.15)
    rag = (0.66, 0.60, 0.46)
    board = (0.46, 0.35, 0.22)
    earth = (0.24, 0.19, 0.14)
    hy, hz = FENCE_GAP_HOLE_HW, FENCE_GAP_HOLE_TOP
    m = _MetricMesh("fence_gap")
    for i, (cy, r) in enumerate(((-0.12, 0.10), (0.08, 0.12), (0.22, 0.08))):
        _mound(m.v, (px + 0.10) / (2 * hd), cy / (2 * d["hw"]), 0.0, r / (2 * hd), r / (2 * d["hw"]),
               0.035 / h, earth, taper=0.55, jitter=0.3, seed=11 + i, closed=False)
    if cut:
        for cy, cz, ln in ((-hy + 0.01, hz - 0.05, 0.05), (hy - 0.02, hz - 0.12, 0.06),
                           (-hy + 0.04, 0.10, 0.05), (hy - 0.05, 0.16, 0.045), (0.0, hz - 0.01, 0.04)):
            m.box(px + 0.004, cy, cz - ln / 2, 0.004, 0.005, ln / 2, wire)
        m.hull(((px + 0.05, -hy + 0.02, 0.012), (px + 0.20, -hy + 0.10, 0.012),
                (px + 0.23, hy - 0.06, 0.012), (px + 0.08, hy - 0.14, 0.012)),
               ((px + 0.05, -hy + 0.02, 0.030), (px + 0.20, -hy + 0.10, 0.030),
                (px + 0.23, hy - 0.06, 0.030), (px + 0.08, hy - 0.14, 0.030)), board, cap_bottom=True)
    else:
        ties = [(-hy - 0.02, 0.04), (hy + 0.02, 0.04), (-hy - 0.02, hz + 0.02), (hy + 0.02, hz + 0.02),
                (0.0, hz + 0.03), (0.0, 0.03)]
        for i, (cy, cz) in enumerate(ties):
            if stage >= 2 and i >= 4:
                continue
            m.box(px + 0.012, cy, cz, 0.012, 0.012, 0.008, wire)
            m.box(px + 0.006, cy, cz - 0.05, 0.002, 0.02, 0.05, rust)
        if stage < 2:
            sag = 0.0 if stage == 0 else -0.09
            m.hull(((px + 0.026, -hy - 0.04, 0.10 + sag), (px + 0.026, hy + 0.04, 0.16),
                    (px + 0.026, hy + 0.04, 0.23), (px + 0.026, -hy - 0.04, 0.17 + sag)),
                   ((px + 0.046, -hy - 0.04, 0.10 + sag), (px + 0.046, hy + 0.04, 0.16),
                    (px + 0.046, hy + 0.04, 0.23), (px + 0.046, -hy - 0.04, 0.17 + sag)),
                   board, cap_bottom=True)
            for sy in (-1, 1):
                if stage >= 1 and sy < 0:
                    continue
                m.box(px + 0.036, sy * (hy + 0.02), 0.135 + sy * 0.03, 0.014, 0.008, 0.05, wire)
        else:
            m.hull(((px + 0.07, -hy + 0.04, 0.010), (px + 0.21, -hy + 0.11, 0.010),
                    (px + 0.24, hy - 0.02, 0.010), (px + 0.10, hy - 0.09, 0.010)),
                   ((px + 0.07, -hy + 0.04, 0.028), (px + 0.21, -hy + 0.11, 0.028),
                    (px + 0.24, hy - 0.02, 0.028), (px + 0.10, hy - 0.09, 0.028)),
                   board, cap_bottom=True)
            for cy, cz, ln in ((-hy + 0.02, hz - 0.06, 0.04), (0.0, hz - 0.02, 0.035),
                               (hy - 0.03, hz - 0.10, 0.045)):
                m.box(px + 0.004, cy, cz - ln / 2, 0.004, 0.005, ln / 2, wire)
    m.box(px + 0.010, hy + 0.06, hz + 0.12, 0.007, 0.030, 0.022, rag)
    m.hull(((px + 0.012, hy + 0.03, hz + 0.10), (px + 0.012, hy + 0.10, hz + 0.10),
            (px + 0.012, hy + 0.10, hz + 0.02), (px + 0.012, hy + 0.05, hz - 0.02)),
           ((px + 0.020, hy + 0.03, hz + 0.10), (px + 0.020, hy + 0.11, hz + 0.10),
            (px + 0.020, hy + 0.12, hz - 0.01), (px + 0.020, hy + 0.06, hz - 0.05)), rag, cap_bottom=True)
    return m.array()


def build_fence_gap_mesh():
    return _fence_gap_details(False)


def build_fence_gap_cut1_mesh():
    return _fence_gap_details(False, stage=1)


def build_fence_gap_cut2_mesh():
    return _fence_gap_details(False, stage=2)


def build_fence_gap_open_mesh():
    return _fence_gap_details(True)


def build_battery_mesh():
    v = []
    body = (0.35, 0.75, 0.45)
    band = (0.18, 0.40, 0.24)
    cap = (0.72, 0.72, 0.68)
    for sx, sy in ((0.38, 0.28), (0.28, 0.38)):
        _mini_box(v, 0.0, 0.0, 0.45, sx, sy, 0.37, body, skip_bottom=True)
        _mini_box(v, 0.0, 0.0, 0.46, sx + 0.01, sy + 0.01, 0.12, band)
        _mini_box(v, 0.0, 0.0, 0.04, sx, sy, 0.04, cap, skip_bottom=True)
        _mini_box(v, 0.0, 0.0, 0.855, sx - 0.02, sy - 0.02, 0.035, cap)
    _mini_box(v, 0.0, 0.0, 0.94, 0.10, 0.10, 0.05, cap)
    return np.array(v, dtype="f4")


def build_fuse_mesh():
    m = _MetricMesh("fuse")
    ceramic = (0.86, 0.84, 0.78)
    band = (0.92, 0.46, 0.12)
    brass = (0.70, 0.58, 0.30)
    r = 0.028
    rim = r + 0.004
    cz = rim
    c = (0.0, cz)
    m.box(0.0, -0.094, cz, 0.004, 0.012, 0.016, brass)
    m.tube("y", c, -0.082, -0.056, rim, rim, brass, n=10, cap0=True)
    m.annulus("y", c, -0.056, r, rim, brass, n=10, positive=True)
    m.tube("y", c, -0.056, -0.021, r, r, ceramic, n=10)
    m.tube("y", c, -0.021, 0.009, r, r, band, n=10)
    m.tube("y", c, 0.009, 0.044, r, r, ceramic, n=10)
    m.tube("y", c, 0.044, 0.070, rim, rim, brass, n=10, cap1=True)
    m.annulus("y", c, 0.044, r, rim, brass, n=10, positive=False)
    m.box(0.0, 0.082, cz, 0.004, 0.012, 0.016, brass)
    return m.array()


NOTE_EXAMINE_VERTEX = """#version 330
uniform mat4 mvp;
uniform mat4 model;
in vec3 in_pos;
in vec3 in_normal;
in vec2 in_uv;
out vec2 v_uv;
out vec3 v_normal;
void main() {
    v_uv = in_uv;
    v_normal = mat3(model) * in_normal;
    gl_Position = mvp * vec4(in_pos, 1.0);
}
"""

NOTE_EXAMINE_FRAGMENT = """#version 330
uniform sampler2D tex0;
uniform vec3 light_dir;
uniform float ambient;
uniform float fade;
in vec2 v_uv;
in vec3 v_normal;
out vec4 frag_color;
void main() {
    vec3 n = normalize(v_normal);
    if (!gl_FrontFacing) {
        n = -n;
    }
    float lam = max(0.0, dot(n, light_dir));
    vec3 c = texture(tex0, v_uv).rgb * (ambient + (1.0 - ambient) * lam) * fade;
    frag_color = vec4(clamp(c, 0.0, 1.0), 1.0);
}
"""

NOTE_EXAMINE_SCALE = (1.0, 1.0, 1.0)


def build_note_examine_mesh(hx=0.168, hy=0.217, ear=0.052, nx=8, ny=14):
    m = []
    paper = (1.0, 1.0, 1.0)
    cut = hx + hy - ear

    def height(x, y):
        crease = -0.0075 * max(0.0, 1.0 - abs(y) / (hy * 0.62))
        curl = 0.0065 * (y / hy) ** 2 + 0.004 * (x / hx) ** 2
        return crease + curl

    def x_max(y):
        return min(hx, cut - y)

    def at(x, y):
        return (x, y, height(x, y))

    def uv(x, y):
        return ((x + hx) / (2 * hx), (y + hy) / (2 * hy))

    def tri(p0, p1, p2):
        ax, ay, az = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
        bx, by, bz = (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])
        n = (ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx)
        ln = math.sqrt(sum(c * c for c in n)) or 1.0
        n = tuple(c / ln for c in n)
        if n[2] < 0.0:
            n = tuple(-c for c in n)
            p1, p2 = p2, p1
        for p in (p0, p1, p2):
            u, v = uv(p[0], p[1])
            m.extend([p[0], p[1], p[2], n[0], n[1], n[2], u, v, paper[0], paper[1], paper[2]])

    for j in range(ny):
        y0 = -hy + 2 * hy * j / ny
        y1 = -hy + 2 * hy * (j + 1) / ny
        xa, xb = x_max(y0), x_max(y1)
        for i in range(nx):
            f0, f1 = i / nx, (i + 1) / nx
            p00 = at(-hx + (xa + hx) * f0, y0)
            p10 = at(-hx + (xa + hx) * f1, y0)
            p11 = at(-hx + (xb + hx) * f1, y1)
            p01 = at(-hx + (xb + hx) * f0, y1)
            tri(p00, p10, p11)
            tri(p00, p11, p01)

    a = (hx - ear, hy)
    b = (hx, hy - ear)
    corner = (hx, hy)
    folded = (cut - corner[1], cut - corner[0])
    lift = 0.0012
    p_a = at(a[0], a[1])
    p_b = at(b[0], b[1])
    p_c = (folded[0], folded[1], height(folded[0], folded[1]) + lift)
    ax, ay, az = (p_b[0] - p_a[0], p_b[1] - p_a[1], p_b[2] - p_a[2])
    bx, by, bz = (p_c[0] - p_a[0], p_c[1] - p_a[1], p_c[2] - p_a[2])
    n = (ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx)
    ln = math.sqrt(sum(c * c for c in n)) or 1.0
    n = tuple(c / ln for c in n)
    if n[2] < 0.0:
        n = tuple(-c for c in n)
        p_a, p_b = p_b, p_a
    for p, src in ((p_a, a), (p_b, b), (p_c, corner)):
        u, v = uv(src[0], src[1])
        m.extend([p[0], p[1], p[2], n[0], n[1], n[2], u, v, paper[0], paper[1], paper[2]])
    return np.array(m, dtype="f4")


def build_note_flat_mesh():
    m = _MetricMesh("note_flat")
    paper = (1.0, 1.0, 1.0)
    back = (0.84, 0.82, 0.78)
    hx, hy, ear = 0.12, 0.155, 0.035
    z0, lift = 0.002, 0.018
    zy = lambda y: z0 + lift * max(0.0, y) / hy
    up = (0.0, -lift / hy, 1.0)
    v_of = lambda y: (y + hy) / (2 * hy)
    m.quad((-hx, -hy, z0), (hx, -hy, z0), (hx, 0.0, z0), (-hx, 0.0, z0), (0, 0, 1), paper, uv_scale=(1.0, 0.5))
    y1 = hy - ear
    m.quad((-hx, 0.0, zy(0.0)), (hx, 0.0, zy(0.0)), (hx, y1, zy(y1)), (-hx, y1, zy(y1)), up, paper,
           uv_scale=(1.0, v_of(y1) - 0.5), uv_offset=(0.0, 0.5))
    m.quad((-hx, y1, zy(y1)), (hx, y1, zy(y1)), (hx - ear, hy, zy(hy)), (-hx, hy, zy(hy)), up, paper,
           uv_scale=(1.0, 1.0 - v_of(y1)), uv_offset=(0.0, v_of(y1)))
    fz = 0.003
    a, b, c = (hx, y1), (hx - ear, hy), (hx - ear, y1)
    m.quad((a[0], a[1], zy(a[1]) + fz), (b[0], b[1], zy(b[1]) + fz), (c[0], c[1], zy(c[1]) + fz),
           (c[0], c[1], zy(c[1]) + fz), up, back)
    return m.array()


def build_sanity_pill_mesh():
    m = _MetricMesh("sanity_pill")
    bottle = (0.86, 0.84, 0.80)
    cap = (0.40, 0.26, 0.64)
    label = (0.62, 0.50, 0.86)
    pill = (0.84, 0.76, 0.96)
    bx, by, r = -0.015, -0.01, 0.028
    m.tube("z", (bx, by), 0.0, 0.018, r, r, bottle, n=10)
    m.tube("z", (bx, by), 0.018, 0.048, r, r, label, n=10)
    m.tube("z", (bx, by), 0.048, 0.062, r, r, bottle, n=10)
    m.tube("z", (bx, by), 0.062, 0.07, r, 0.022, bottle, n=10)
    m.tube("z", (bx, by), 0.07, 0.092, 0.03, 0.03, cap, n=12, cap1=True)
    for px, py, ang in ((0.035, 0.03, 0.4), (0.042, -0.035, 1.6)):
        m.tube("z", (px, py), 0.0, 0.007, 0.011, 0.011, pill, n=8, cap1=True, phase=ang)
    return m.array()


def build_valve_key_mesh():
    m = _MetricMesh("valve_key")
    steel = (0.60, 0.66, 0.68)
    worn = (0.76, 0.79, 0.79)
    dark = (0.10, 0.10, 0.11)
    z = 0.017
    m.tube("x", (0.0, z), -0.062, 0.052, 0.010, 0.010, steel, n=8)
    m.tube("y", (-0.062, z), -0.050, 0.050, 0.0095, 0.0095, worn, n=8, cap0=True, cap1=True)
    for sy in (-0.050, 0.050):
        m.tube("y", (-0.062, z), sy - 0.007, sy + 0.007, 0.013, 0.013, steel, n=8,
               cap0=True, cap1=True)
    m.box(0.062, 0.0, z, 0.016, 0.021, 0.021, steel)
    m.box(0.079, 0.0, z, 0.002, 0.010, 0.010, dark)
    return m.array()


def build_key_mesh():
    m = _MetricMesh("key")
    brass = (0.90, 0.76, 0.34)
    t = 0.006
    m.ring("z", (-0.035, 0.0), 0.0, t, 0.012, 0.022, brass, n=10)
    m.ring("z", (-0.008, 0.0), 0.0, t * 1.2, 0.0, 0.007, brass, n=8)
    m.box(0.02, 0.0, t / 2, 0.028, 0.0035, t / 2, brass)
    m.box(0.042, -0.009, t / 2, 0.006, 0.009, t / 2, brass)
    m.box(0.03, -0.007, t / 2, 0.004, 0.007, t / 2, brass)
    return m.array()


def build_cutters_mesh():
    m = _MetricMesh("cutters")
    steel = (0.56, 0.57, 0.60)
    dark = (0.22, 0.22, 0.24)
    grip = (0.72, 0.12, 0.08)
    t = 0.006
    for sx in (-1, 1):
        m.hull(((sx * 0.002, 0.055, 0.0), (sx * 0.012, 0.055, 0.0), (sx * 0.006, 0.098, 0.0), (sx * 0.001, 0.095, 0.0)),
               ((sx * 0.002, 0.055, t * 2), (sx * 0.012, 0.055, t * 2), (sx * 0.006, 0.098, t * 2), (sx * 0.001, 0.095, t * 2)),
               dark)
        m.hull(((sx * 0.004, 0.05, 0.0), (sx * 0.012, 0.05, 0.0), (sx * 0.034, -0.04, 0.0), (sx * 0.026, -0.04, 0.0)),
               ((sx * 0.004, 0.05, t), (sx * 0.012, 0.05, t), (sx * 0.034, -0.04, t), (sx * 0.026, -0.04, t)),
               steel)
        m.hull(((sx * 0.023, -0.04, 0.0), (sx * 0.037, -0.04, 0.0), (sx * 0.046, -0.098, 0.0), (sx * 0.032, -0.098, 0.0)),
               ((sx * 0.023, -0.04, t * 1.6), (sx * 0.037, -0.04, t * 1.6), (sx * 0.046, -0.098, t * 1.6),
                (sx * 0.032, -0.098, t * 1.6)), grip)
    m.box(0.0, 0.052, t * 1.2, 0.016, 0.012, t * 1.2, steel)
    for sx in (-1, 1):
        m.tube("z", (sx * 0.008, 0.052), t * 2.4, t * 2.4 + 0.003, 0.004, 0.004, dark, n=6, cap1=True)
    return m.array()


def build_lighter_mesh():
    m = _MetricMesh("lighter")
    _plastic_lighter(m, lambda w, h, t: (t, w, h + 0.032), "y", lambda h, t: (t, h + 0.032))
    return m.array()


def build_flashlight_handheld_mesh():
    v = []
    body = (0.55, 0.55, 0.60)
    head = (0.32, 0.31, 0.28)
    lens = (0.95, 0.90, 0.55)
    tail = (0.28, 0.27, 0.25)
    switch = (0.70, 0.15, 0.12)
    n = 8
    r_body, r_head = 0.019, 0.027
    z0, z1, z2 = -0.115, 0.070, 0.125
    pts_b = [(r_body * math.cos(math.tau * i / n), r_body * math.sin(math.tau * i / n)) for i in range(n)]
    pts_h = [(r_head * math.cos(math.tau * i / n), r_head * math.sin(math.tau * i / n)) for i in range(n)]

    def ring(pts, za, zb, color):
        for i in range(n):
            xa, ya = pts[i]
            xb, yb = pts[(i + 1) % n]
            _quad(v, (xb, yb, za), (xa, ya, za), (xa, ya, zb), (xb, yb, zb), (xa + xb, ya + yb, 0), color)

    ring(pts_b, z0, z1, body)
    ring(pts_h, z1, z2, head)
    for i in range(n):
        xa, ya = pts_h[i]
        xb, yb = pts_h[(i + 1) % n]
        _quad(v, (xa, ya, z2), (xb, yb, z2), (0.0, 0.0, z2), (0.0, 0.0, z2), (0, 0, 1), lens)
        xa, ya = pts_b[i]
        xb, yb = pts_b[(i + 1) % n]
        _quad(v, (xb, yb, z0), (xa, ya, z0), (0.0, 0.0, z0), (0.0, 0.0, z0), (0, 0, -1), tail)
    _mini_box(v, 0.0, r_body - 0.004, -0.01, 0.008, 0.011, 0.014, switch)
    return np.array(v, dtype="f4")


LIGHTER_FLAME_OFFSET = (0.0, 0.046, -0.002)
HELD_LIGHTER_FILL = 0.9
HELD_FLASHLIGHT_FILL = (0.26, 0.25, 0.23)


def _plastic_lighter(m, P, wheel_axis, wheel_centre):
    body = (0.72, 0.14, 0.10)
    body_dark = (0.46, 0.08, 0.06)
    fuel = (0.86, 0.60, 0.20)
    steel = (0.80, 0.81, 0.83)
    dark = (0.10, 0.10, 0.11)
    lever = (0.82, 0.16, 0.12)
    W, T = 0.012, 0.007
    b = 0.002

    def block(w0, w1, h0, h1, t0, t1, col, bevel=0.0):
        bottom = [P(w0 + bevel, h0, t0 + bevel), P(w1 - bevel, h0, t0 + bevel), P(w1 - bevel, h0, t1 - bevel),
                  P(w0 + bevel, h0, t1 - bevel)]
        top = [P(w0, h0 + bevel, t0), P(w1, h0 + bevel, t0), P(w1, h0 + bevel, t1), P(w0, h0 + bevel, t1)]
        if bevel:
            m.hull(bottom, top, col, cap_bottom=True)
        m.hull([P(w0, h0 + bevel, t0), P(w1, h0 + bevel, t0), P(w1, h0 + bevel, t1), P(w0, h0 + bevel, t1)],
               [P(w0, h1, t0), P(w1, h1, t0), P(w1, h1, t1), P(w0, h1, t1)], col)

    block(-W, W, -0.032, 0.026, -T, T, body, bevel=b)
    block(-W - 0.0015, W + 0.0015, -0.026, 0.004, -T + 0.0015, T - 0.0015, fuel)
    block(-W - 0.002, W + 0.002, 0.018, 0.026, -T - 0.002, T + 0.002, body_dark)
    block(-W + 0.0005, W - 0.0005, 0.026, 0.040, -T + 0.0005, T - 0.0005, steel)
    for k in range(3):
        wk = -0.006 + k * 0.006
        block(wk - 0.0015, wk + 0.0015, 0.029, 0.037, -T - 0.0005 - 0.0015, -T + 0.0015, dark)
    block(-0.0025, 0.0025, 0.040, 0.043, -0.004, 0.0, steel)
    m.tube(wheel_axis, wheel_centre(0.043, 0.0035), -0.005, 0.005, 0.0045, 0.0045, dark, n=8, cap0=True, cap1=True)
    block(-0.004, 0.004, 0.036, 0.041, 0.0035, 0.0085, lever)


def build_lighter_handheld_mesh():
    m = _MetricMesh(dims=(1.0, 1.0, 1.0))
    _plastic_lighter(m, lambda w, h, t: (w, h, t), "x", lambda h, t: (h, t))
    return m.array()


def build_paper_map_mesh():
    v = []
    paper = (0.86, 0.82, 0.70)
    shade = (0.70, 0.66, 0.54)
    ink = (0.35, 0.30, 0.26)
    _mini_box(v, 0.0, 0.0, 0.25, 0.48, 0.48, 0.25, paper, skip_bottom=True)
    _mini_box(v, 0.02, 0.0, 0.62, 0.44, 0.46, 0.12, shade, skip_bottom=True)
    for x0 in (-0.20, 0.12):
        _mini_box(v, x0, -0.10, 0.76, 0.12, 0.012, 0.02, ink)
    _mini_box(v, -0.05, 0.18, 0.76, 0.012, 0.16, 0.02, ink)
    return np.array(v, dtype="f4")


def build_pencil_mesh():
    v = []
    yellow = (0.95, 0.76, 0.18)
    wood = (0.86, 0.70, 0.46)
    lead = (0.15, 0.15, 0.16)
    cut = (0.62, 0.48, 0.16)
    _mini_box(v, 0.0, -0.087, 0.5, 0.42, 0.397, 0.45, yellow,
              skip_bottom=True, skip=("-y",))
    _mini_box(v, 0.0, -0.492, 0.5, 0.42, 0.008, 0.45, cut,
              skip_bottom=True, skip=("+y",))
    _mini_box(v, 0.0, 0.34, 0.5, 0.28, 0.06, 0.30, wood, skip_bottom=True)
    _mini_box(v, 0.0, 0.43, 0.5, 0.12, 0.05, 0.12, lead, skip_bottom=True)
    return np.array(v, dtype="f4")


MAP_PAPER_HALF = (0.135, 0.155)
MAP_PAPER_BACK = (0.84, 0.80, 0.69)
MAP_ROLL_BOTTOM = 0.1450
MAP_ROLL_BASE = 0.007
MAP_ROLL_PER_SHEET = 0.009
MAP_ROLL_MAX = 0.055
MAP_PENCIL_LEN = 0.132
MAP_PENCIL_THICK = 0.0085
MAP_PENCIL_MIN = 0.22
MAP_PENCIL_CLIP = (0.0, -0.1605, -0.010)
MAP_PENCIL_SPARE_DY = 0.0086
MAP_PENCIL_HOLD = (-0.52, -0.70, 0.49)
MAP_PENCIL_ZONE_Y = 0.1445
MAP_CURSOR_V_MIN = 0.5 - MAP_PENCIL_ZONE_Y / (2.0 * MAP_PAPER_HALF[1])
MAP_SHEET_LIFT = 0.002


def map_pencil_grip(d, tip_y, length):
    dl = math.sqrt(sum(c * c for c in d)) or 1.0
    d = tuple(c / dl for c in d)
    head = MAP_PENCIL_ZONE_Y - tip_y
    if -d[1] * length > head:
        dy = -max(0.0, head) / length
        xz = math.hypot(d[0], d[2]) or 1.0
        rest = math.sqrt(max(1e-6, 1.0 - dy * dy))
        d = (d[0] / xz * rest, dy, d[2] / xz * rest)
    return d
MAP_TUCK_START = 0.74
MAP_TUCK_LEFT = 0.07


def build_map_sheet_mesh():
    v = []
    paper = (0.86, 0.83, 0.72)
    under = (0.78, 0.75, 0.65)
    for i, (dx, dy, rot, col) in enumerate(((0.0, 0.0, 0.0, under),
                                            (0.05, -0.06, 0.06, under),
                                            (-0.04, 0.05, -0.05, paper))):
        z = 0.14 + i * 0.28
        ca, sa = math.cos(rot), math.sin(rot)
        hx, hy = 0.46, 0.44
        pts = [(-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy)]
        top = [(dx + x * ca - y * sa, dy + x * sa + y * ca) for x, y in pts]
        zt = z + 0.24
        _quad(v, (top[0][0], top[0][1], zt), (top[1][0], top[1][1], zt),
              (top[2][0], top[2][1], zt), (top[3][0], top[3][1], zt), (0, 0, 1), col)
        for k in range(4):
            a, b = top[k], top[(k + 1) % 4]
            n = (b[1] - a[1], -(b[0] - a[0]), 0.0)
            _quad(v, (a[0], a[1], z), (b[0], b[1], z), (b[0], b[1], zt), (a[0], a[1], zt), n, under)
    return np.array(v, dtype="f4")


def build_map_handheld_mesh():
    v = []
    board = (0.36, 0.26, 0.16)
    clip = (0.62, 0.62, 0.64)
    _mini_box(v, 0.0, -0.01, 0.006, 0.150, 0.180, 0.005, board)
    _mini_box(v, 0.0, 0.160, -0.004, 0.045, 0.014, 0.007, clip)
    _mini_box(v, 0.0, 0.176, -0.002, 0.018, 0.006, 0.004, clip)
    return np.array(v, dtype="f4")


def build_map_roll_mesh():
    m = _MetricMesh(dims=(1.0, 1.0, 1.0))
    paper = (0.80, 0.76, 0.65)
    m.tube("x", (0.0, 0.0), -1.0, 1.0, 1.0, 1.0, paper, n=14, cap0=True, cap1=True)
    return m.array()


def build_map_paper_quad():
    v = []
    hx, hy = MAP_PAPER_HALF
    z = -0.0012
    _quad(v, (-hx, -hy, z), (hx, -hy, z), (hx, hy, z), (-hx, hy, z), (0, 0, -1), (1.0, 1.0, 1.0))
    return np.array(v, dtype="f4")


def build_lighter_flame_mesh():
    v = []
    base = (1.0, 0.45, 0.08)
    tip = (1.0, 0.85, 0.40)
    n = 5
    r = 0.007
    y0, ym, y1 = 0.0, 0.013, 0.030
    pts = [(r * math.cos(math.tau * i / n), r * math.sin(math.tau * i / n)) for i in range(n)]
    for i in range(n):
        xa, za = pts[i]
        xb, zb = pts[(i + 1) % n]
        _quad(v, (xb, y0, zb), (xa, y0, za), (xa * 0.35, ym, za * 0.35), (xb * 0.35, ym, zb * 0.35),
              (xa + xb, 0.4, za + zb), base)
        _quad(v, (xb * 0.35, ym, zb * 0.35), (xa * 0.35, ym, za * 0.35), (0.0, y1, 0.0), (0.0, y1, 0.0),
              (xa + xb, 0.4, za + zb), tip)
    return np.array(v, dtype="f4")


def build_clutter_papers_mesh():
    m = _MetricMesh("clutter_papers")
    paper = (0.92, 0.91, 0.86)
    old = (0.80, 0.78, 0.70)

    def sheet(cx, cy, z, ang, hx, hy, col):
        ca, sa = math.cos(ang), math.sin(ang)
        pts = [(cx + ux * hx * ca - uy * hy * sa, cy + ux * hx * sa + uy * hy * ca, z)
               for ux, uy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        m.quad(*pts, (0, 0, 1), col)

    sheet(-0.01, 0.0, 0.002, 0.2, 0.05, 0.065, old)
    sheet(0.015, -0.01, 0.006, -0.35, 0.05, 0.065, paper)
    sheet(-0.02, 0.02, 0.010, 0.9, 0.045, 0.06, paper)
    m.hull(((0.04, 0.04, 0.0), (0.07, 0.035, 0.0), (0.075, 0.07, 0.0), (0.045, 0.075, 0.0)),
           ((0.05, 0.048, 0.03), (0.066, 0.045, 0.028), (0.068, 0.063, 0.032), (0.05, 0.066, 0.029)), paper)
    return m.array()


def build_clutter_bottle_mesh():
    m = _MetricMesh("clutter_bottle")
    glass = (0.30, 0.46, 0.32)
    label = (0.72, 0.68, 0.56)
    r = 0.027
    m.tube("z", (0.0, 0.0), 0.0, 0.035, r, r, glass, n=10)
    m.tube("z", (0.0, 0.0), 0.035, 0.08, r, r, label, n=10)
    m.tube("z", (0.0, 0.0), 0.08, 0.10, r, r, glass, n=10)
    m.tube("z", (0.0, 0.0), 0.10, 0.128, r, 0.011, glass, n=10)
    m.tube("z", (0.0, 0.0), 0.128, 0.158, 0.011, 0.011, glass, n=8)
    m.ring("z", (0.0, 0.0), 0.158, 0.166, 0.008, 0.013, glass, n=8)
    return m.array()


def build_clutter_junk_mesh():
    v = []
    carton = (0.56, 0.46, 0.32)
    can = (0.60, 0.60, 0.62)
    rag = (0.30, 0.27, 0.24)
    pipe = (0.36, 0.34, 0.33)
    _mini_box(v, -0.10, -0.06, 0.20, 0.26, 0.20, 0.20, carton, skip_bottom=True)
    _mini_box(v, -0.08, -0.06, 0.42, 0.22, 0.18, 0.02, (0.48, 0.39, 0.27))
    _mini_box(v, 0.22, 0.20, 0.12, 0.10, 0.16, 0.12, can, skip_bottom=True)
    _mini_box(v, 0.10, -0.30, 0.03, 0.30, 0.16, 0.03, rag, skip_bottom=True)
    _mini_box(v, -0.20, 0.30, 0.06, 0.26, 0.05, 0.06, pipe, skip_bottom=True)
    return np.array(v, dtype="f4")


def _barricade_mesh(which):
    m = _MetricMesh("barricade")
    wood = (0.42, 0.31, 0.19)
    pale = (0.52, 0.40, 0.26)
    dark = (0.28, 0.20, 0.13)
    metal = (0.36, 0.35, 0.34)
    cloth = (0.30, 0.30, 0.32)
    if which == 0:
        m.hull(((-0.30, -0.40, 0.004), (0.24, -0.40, 0.004), (0.24, 0.24, 0.004), (-0.30, 0.24, 0.004)),
               ((-0.26, -0.38, 0.115), (0.28, -0.38, 0.035), (0.28, 0.22, 0.035), (-0.26, 0.22, 0.115)),
               wood)
        m.box(0.06, 0.32, 0.045, 0.17, 0.10, 0.045, pale)
        m.box(0.17, 0.26, 0.125, 0.02, 0.02, 0.08, pale)
        m.box(0.17, 0.38, 0.125, 0.02, 0.02, 0.08, pale)
        m.box(-0.22, -0.16, 0.128, 0.14, 0.05, 0.014, pale)
        m.box(-0.19, -0.02, 0.126, 0.16, 0.04, 0.013, dark)
        m.box(-0.10, 0.12, 0.030, 0.19, 0.05, 0.014, pale)
        m.box(0.25, -0.10, 0.020, 0.06, 0.06, 0.016, dark)
    elif which == 1:
        m.box(-0.04, -0.10, 0.055, 0.24, 0.26, 0.055, metal)
        m.box(-0.04, -0.10, 0.116, 0.22, 0.24, 0.008, (0.30, 0.30, 0.30))
        m.box(0.10, 0.26, 0.012, 0.22, 0.10, 0.012, metal)
        m.box(0.10, 0.26, 0.026, 0.03, 0.02, 0.004, (0.55, 0.54, 0.52))
        m.box(-0.06, -0.08, 0.148, 0.13, 0.12, 0.024, pale)
        m.box(-0.16, -0.08, 0.166, 0.02, 0.11, 0.042, pale)
        m.box(-0.20, 0.30, 0.018, 0.10, 0.04, 0.018, dark)
        m.box(0.26, -0.24, 0.014, 0.06, 0.14, 0.014, wood)
    else:
        m.box(-0.02, -0.06, 0.036, 0.28, 0.30, 0.036, cloth)
        m.box(-0.02, -0.06, 0.074, 0.26, 0.28, 0.004, (0.38, 0.38, 0.40))
        m.box(0.14, 0.28, 0.022, 0.20, 0.10, 0.022, metal)
        for dy in (0.20, 0.36):
            m.box(0.14, dy, 0.056, 0.02, 0.012, 0.034, metal)
        m.box(-0.24, 0.22, 0.016, 0.08, 0.14, 0.016, wood)
        m.box(0.02, -0.06, 0.092, 0.10, 0.09, 0.018, pale)
    return m.array()


def build_barricade_mesh():
    return _barricade_mesh(0)


def build_barricade_locker_mesh():
    return _barricade_mesh(1)


def build_barricade_bed_mesh():
    return _barricade_mesh(2)


def _desk_lamp_mesh(broken):
    m = _MetricMesh("lamp_desk")
    base = (0.13, 0.12, 0.11)
    brass = (0.52, 0.44, 0.26)
    shade_out = (0.44, 0.41, 0.33) if broken else (0.90, 0.84, 0.66)
    shade_in = (0.36, 0.34, 0.28) if broken else (0.98, 0.93, 0.78)
    bulb = (0.08, 0.08, 0.08) if broken else (1.0, 0.97, 0.86)
    m.tube("z", (0.0, 0.0), 0.0, 0.014, 0.072, 0.064, base, n=12, cap1=True)
    sx = -0.042
    m.tube("z", (sx, 0.0), 0.014, 0.024, 0.016, 0.010, brass, n=8, cap1=True)
    m.box(0.03, 0.028, 0.0145, 0.007, 0.006, 0.0035, brass)
    if broken:
        cx, cy, zb, zt = 0.040, 0.012, 0.072, 0.135
        arm_z = 0.155
    else:
        cx, cy, zb, zt = 0.026, 0.0, 0.10, 0.165
        arm_z = 0.19
    m.tube("z", (sx, 0.0), 0.024, arm_z + 0.005, 0.005, 0.005, brass, n=6, cap1=True)
    m.hull(((sx, -0.005, arm_z - 0.005), (cx, cy - 0.005, arm_z - 0.005), (cx, cy + 0.005, arm_z - 0.005),
            (sx, 0.005, arm_z - 0.005)),
           ((sx, -0.005, arm_z + 0.005), (cx, cy - 0.005, arm_z + 0.005), (cx, cy + 0.005, arm_z + 0.005),
            (sx, 0.005, arm_z + 0.005)), brass, cap_bottom=True)
    m.tube("z", (cx, cy), zt - 0.002, arm_z - 0.005, 0.005, 0.005, brass, n=6)
    m.tube("z", (cx, cy), zb, zt, 0.052, 0.02, shade_out, n=12, cap1=True)
    m.tube("z", (cx, cy), zb + 0.0015, zt - 0.003, 0.0485, 0.017, shade_in, n=12)
    m.tube("z", (cx, cy), zb + 0.018, zt - 0.025, 0.012, 0.008, bulb, n=6, cap0=True)
    return m.array()


def build_lamp_desk_mesh():
    return _desk_lamp_mesh(broken=False)


def build_lamp_desk_broken_mesh():
    return _desk_lamp_mesh(broken=True)


def build_sign_exit_mesh():
    v = []
    frame = (0.14, 0.13, 0.12)
    glow = (0.28, 1.0, 0.46)
    ink = (0.03, 0.10, 0.05)
    _mini_box(v, 0, 0, 0.5, 0.5, 0.46, 0.46, frame, skip_back=True)
    _mini_box(v, 0.15, 0, 0.5, 0.40, 0.38, 0.38, glow)
    fx = 0.56
    _mini_box(v, fx, 0.20, 0.50, 0.01, 0.02, 0.26, ink)
    _mini_box(v, fx, 0.29, 0.50, 0.01, 0.02, 0.26, ink)
    _mini_box(v, fx, 0.245, 0.76, 0.01, 0.065, 0.02, ink)
    _mini_box(v, fx, -0.02, 0.72, 0.01, 0.05, 0.05, ink)
    _mini_box(v, fx, -0.05, 0.53, 0.01, 0.04, 0.12, ink)
    _mini_box(v, fx, -0.15, 0.58, 0.01, 0.08, 0.025, ink)
    _mini_box(v, fx, 0.05, 0.56, 0.01, 0.08, 0.025, ink)
    _mini_box(v, fx, -0.14, 0.35, 0.01, 0.08, 0.03, ink)
    _mini_box(v, fx, 0.02, 0.30, 0.01, 0.03, 0.09, ink)
    return np.array(v, dtype="f4")


def _wall_sconce_mesh(broken):
    m = _MetricMesh("wall_sconce")
    plate = (0.26, 0.24, 0.20)
    brass = (0.56, 0.46, 0.26)
    shade = (0.40, 0.38, 0.32) if broken else (0.95, 0.89, 0.72)
    bulb = (0.10, 0.10, 0.10) if broken else (1.0, 0.97, 0.86)
    wall = -0.07
    m.tube("x", (0.0, 0.08), wall, wall + 0.008, 0.045, 0.045, plate, n=12, cap1=True)
    m.box((wall + 0.008 + 0.0) / 2, 0.0, 0.055, (0.0 - wall - 0.008) / 2, 0.006, 0.006, brass)
    m.tube("z", (0.005, 0.0), 0.045, 0.065, 0.02, 0.022, brass, n=10, cap0=True)
    m.tube("z", (0.005, 0.0), 0.065, 0.10, 0.012, 0.012, bulb, n=6, cap1=True)
    if broken:
        for k in range(5):
            a = math.tau * k / 5
            hi = 0.085 + 0.02 * ((k * 7) % 3) / 2
            m.face(((0.005 + math.cos(a) * 0.022, math.sin(a) * 0.022, 0.065),
                    (0.005 + math.cos(a + 1.26) * 0.022, math.sin(a + 1.26) * 0.022, 0.065),
                    (0.005 + math.cos(a + 1.26) * 0.03, math.sin(a + 1.26) * 0.03, 0.08),
                    (0.005 + math.cos(a + 0.6) * 0.033, math.sin(a + 0.6) * 0.033, hi)), shade, (0.005, 0.0, 0.07))
    else:
        m.tube("z", (0.005, 0.0), 0.065, 0.10, 0.022, 0.04, shade, n=12)
        m.tube("z", (0.005, 0.0), 0.10, 0.14, 0.04, 0.055, shade, n=12)
    return m.array()


def build_emergency_lamp_mesh():
    m = _MetricMesh("emergency_lamp")
    body = (0.24, 0.23, 0.21)
    bezel = (0.34, 0.33, 0.30)
    guard = (0.11, 0.10, 0.09)
    lens = (0.96, 0.93, 0.88)
    wall = -0.07
    m.box(wall + 0.012, 0.0, 0.085, 0.012, 0.074, 0.070, body)
    m.box(wall + 0.030, 0.0, 0.085, 0.006, 0.062, 0.056, body)
    m.tube("x", (0.0, 0.085), wall + 0.036, wall + 0.050, 0.042, 0.036, lens, n=16)
    m.tube("x", (0.0, 0.085), wall + 0.050, wall + 0.058, 0.036, 0.022, lens, n=16)
    m.tube("x", (0.0, 0.085), wall + 0.058, wall + 0.062, 0.022, 0.008, lens, n=16, cap1=True)
    m.ring("x", (0.0, 0.085), wall + 0.034, wall + 0.042, 0.042, 0.056, bezel, n=16)
    m.box(wall + 0.066, 0.0, 0.085, 0.004, 0.005, 0.056, guard)
    m.box(wall + 0.066, 0.0, 0.085, 0.004, 0.056, 0.005, guard)
    for dy, dz in ((0.0, 0.056), (0.0, -0.056), (0.056, 0.0), (-0.056, 0.0)):
        m.box(wall + 0.053, dy, 0.085 + dz, 0.017, 0.005, 0.005, guard)
    return m.array()


def build_wall_sconce_mesh():
    return _wall_sconce_mesh(broken=False)


def build_wall_sconce_broken_mesh():
    return _wall_sconce_mesh(broken=True)


def _crt_monitor_mesh(broken):
    m = _MetricMesh("monitor")
    case = (0.78, 0.75, 0.66)
    bezel = (0.72, 0.69, 0.61)
    dark = (0.09, 0.09, 0.10)
    glass = (0.05, 0.05, 0.06) if broken else (0.30, 0.56, 0.96)
    led = (0.20, 0.22, 0.20) if broken else (0.30, 1.0, 0.42)
    crack = (0.55, 0.58, 0.62)
    m.tube("z", (-0.01, 0.0), 0.0, 0.01, 0.07, 0.062, case, n=10, cap1=True)
    m.box(-0.01, 0.0, 0.0165, 0.032, 0.045, 0.0065, bezel)
    fx = 0.10
    m.box(fx - 0.021, 0.0, 0.09, 0.021, 0.136, 0.066, bezel)
    m.taper_x(fx - 0.042, -0.10, (0.126, 0.03, 0.148), (0.07, 0.05, 0.122), case, cap1=True)
    for i in range(4):
        m.box(-0.1015, 0.0, 0.066 + i * 0.013, 0.0015, 0.045, 0.003, dark)
    m.quad((fx + 0.003, -0.114, 0.038), (fx + 0.003, 0.114, 0.038), (fx + 0.003, 0.114, 0.146),
           (fx + 0.003, -0.114, 0.146), (1, 0, 0), dark)
    m.box(fx + 0.0055, 0.0, 0.092, 0.0015, 0.102, 0.046, glass, skip_back=True)
    m.box(fx + 0.0045, 0.1, 0.03, 0.0015, 0.006, 0.0035, led, skip_back=True)
    m.box(fx + 0.004, 0.08, 0.03, 0.002, 0.007, 0.004, dark, skip_back=True)
    if broken:
        m.box(fx + 0.0115, -0.03, 0.11, 0.0015, 0.06, 0.0015, crack)
        m.box(fx + 0.0115, 0.03, 0.075, 0.0015, 0.05, 0.0015, crack)
        m.box(fx + 0.0115, 0.0, 0.094, 0.0015, 0.0015, 0.03, crack)
    return m.array()


def build_monitor_mesh():
    return _crt_monitor_mesh(broken=False)


def build_monitor_broken_mesh():
    return _crt_monitor_mesh(broken=True)


def build_bush_mesh():
    m = _MetricMesh("bush")
    low = (0.14, 0.27, 0.11)
    mid = (0.18, 0.33, 0.14)
    high = (0.22, 0.39, 0.17)
    clumps = (
        (0.0, 0.0, 0.0, 0.26, 0.24, 0.26, low),
        (0.16, -0.12, 0.04, 0.16, 0.17, 0.30, mid),
        (-0.17, 0.10, 0.02, 0.15, 0.18, 0.27, mid),
        (-0.08, -0.16, 0.06, 0.14, 0.12, 0.24, low),
        (0.12, 0.15, 0.05, 0.15, 0.13, 0.25, mid),
        (0.03, -0.02, 0.22, 0.17, 0.16, 0.44, high),
        (-0.13, -0.03, 0.18, 0.10, 0.11, 0.37, high),
        (0.19, 0.03, 0.16, 0.09, 0.10, 0.34, high),
    )
    for cx, cy, z0, sx, sy, z1, color in clumps:
        bottom = ((cx - sx, cy - sy, z0), (cx + sx, cy - sy, z0), (cx + sx, cy + sy, z0), (cx - sx, cy + sy, z0))
        k = 0.72
        top = ((cx - sx * k, cy - sy * k, z1), (cx + sx * k, cy - sy * k, z1), (cx + sx * k, cy + sy * k, z1),
               (cx - sx * k, cy + sy * k, z1))
        m.hull(bottom, top, color)
    return m.array()


def build_rock_mesh():
    m = _MetricMesh("rock")
    c1 = (0.50, 0.48, 0.44)
    c2 = (0.40, 0.38, 0.35)
    c3 = (0.45, 0.43, 0.39)
    m.hull(((-0.20, -0.17, 0.0), (0.18, -0.19, 0.0), (0.21, 0.15, 0.0), (-0.17, 0.18, 0.0)),
           ((-0.15, -0.12, 0.17), (0.13, -0.14, 0.19), (0.15, 0.10, 0.22), (-0.12, 0.13, 0.15)), c1)
    m.hull(((-0.15, -0.12, 0.17), (0.13, -0.14, 0.19), (0.15, 0.10, 0.22), (-0.12, 0.13, 0.15)),
           ((-0.08, -0.06, 0.235), (0.07, -0.08, 0.245), (0.09, 0.04, 0.26), (-0.06, 0.07, 0.225)), c3)
    m.hull(((0.14, 0.02, 0.0), (0.26, 0.00, 0.0), (0.27, 0.16, 0.0), (0.15, 0.17, 0.0)),
           ((0.17, 0.05, 0.09), (0.24, 0.04, 0.08), (0.25, 0.13, 0.07), (0.18, 0.14, 0.10)), c2)
    return m.array()


def _stick(v, a, b, r0, r1, color):
    dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    length = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
    ux, uy, uz = dx / length, dy / length, dz / length
    px, py, pz = (-uy, ux, 0.0) if abs(uz) < 0.95 else (1.0, 0.0, 0.0)
    pl = math.sqrt(px * px + py * py + pz * pz)
    px, py, pz = px / pl, py / pl, pz / pl
    qx, qy, qz = uy * pz - uz * py, uz * px - ux * pz, ux * py - uy * px
    corners = ((-1, -1), (1, -1), (1, 1), (-1, 1))
    ring_a = [(a[0] + (px * i + qx * j) * r0, a[1] + (py * i + qy * j) * r0, a[2] + (pz * i + qz * j) * r0) for i, j in corners]
    ring_b = [(b[0] + (px * i + qx * j) * r1, b[1] + (py * i + qy * j) * r1, b[2] + (pz * i + qz * j) * r1) for i, j in corners]
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2)
    for k in range(4):
        pts = (ring_a[k], ring_a[(k + 1) % 4], ring_b[(k + 1) % 4], ring_b[k])
        nx = ny = nz = 0.0
        for i in range(4):
            p0, p1 = pts[i], pts[(i + 1) % 4]
            nx += (p0[1] - p1[1]) * (p0[2] + p1[2])
            ny += (p0[2] - p1[2]) * (p0[0] + p1[0])
            nz += (p0[0] - p1[0]) * (p0[1] + p1[1])
        cxp = sum(q[0] for q in pts) / 4 - mid[0]
        cyp = sum(q[1] for q in pts) / 4 - mid[1]
        czp = sum(q[2] for q in pts) / 4 - mid[2]
        if nx * cxp + ny * cyp + nz * czp < 0:
            nx, ny, nz = -nx, -ny, -nz
        _quad(v, pts[0], pts[1], pts[2], pts[3], (nx, ny, nz), color)
    _quad(v, ring_b[0], ring_b[1], ring_b[2], ring_b[3], (ux, uy, uz), color)


def _mound(v, cx, cy, cz, sx, sy, sz, color, taper=0.72, jitter=0.0, seed=0, lean=(0.0, 0.0), closed=False):
    rng = random.Random(seed)
    j = lambda: (rng.random() - 0.5) * 2.0 * jitter
    z0, z1 = cz - sz, cz + sz
    bottom = [(cx + ux * sx * (1.0 + j()), cy + uy * sy * (1.0 + j()), z0)
              for ux, uy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    tx, ty = cx + lean[0], cy + lean[1]
    top = [(tx + ux * sx * taper * (1.0 + j()), ty + uy * sy * taper * (1.0 + j()), z1 + sz * j() * 0.5)
           for ux, uy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    faces = [(bottom[i], bottom[(i + 1) % 4], top[(i + 1) % 4], top[i]) for i in range(4)] + [tuple(top)]
    if closed:
        faces.append(tuple(bottom))
    for pts in faces:
        nx = ny = nz = 0.0
        for i in range(4):
            a, b = pts[i], pts[(i + 1) % 4]
            nx += (a[1] - b[1]) * (a[2] + b[2])
            ny += (a[2] - b[2]) * (a[0] + b[0])
            nz += (a[0] - b[0]) * (a[1] + b[1])
        mx = sum(q[0] for q in pts) / 4 - cx
        my = sum(q[1] for q in pts) / 4 - cy
        mz = sum(q[2] for q in pts) / 4 - cz
        if nx * mx + ny * my + nz * mz < 0.0:
            nx, ny, nz = -nx, -ny, -nz
        _quad(v, pts[0], pts[1], pts[2], pts[3], (nx, ny, nz), color)


def build_bush_wide_mesh():
    v = []
    low = (0.14, 0.27, 0.11)
    mid = (0.17, 0.32, 0.13)
    clumps = (
        (0.0, 0.0, 0.22, 0.52, 0.46, 0.22, low),
        (0.46, 0.20, 0.18, 0.26, 0.24, 0.18, mid),
        (-0.44, -0.18, 0.20, 0.28, 0.26, 0.20, low),
        (0.10, -0.44, 0.16, 0.24, 0.20, 0.16, mid),
        (-0.12, 0.40, 0.17, 0.22, 0.22, 0.17, mid),
        (0.06, 0.04, 0.52, 0.30, 0.28, 0.12, (0.19, 0.35, 0.15)),
    )
    for i, (cx, cy, cz, sx, sy, sz, color) in enumerate(clumps):
        _mound(v, cx, cy, cz, sx, sy, sz, color, taper=0.7, jitter=0.12, seed=i + 1)
    return np.array(v, dtype="f4")


def build_bush_tall_mesh():
    v = []
    base = (0.13, 0.26, 0.11)
    body = (0.16, 0.31, 0.13)
    top = (0.21, 0.37, 0.16)
    clumps = (
        (0.0, 0.0, 0.30, 0.28, 0.26, 0.30, base),
        (0.26, 0.10, 0.46, 0.16, 0.18, 0.18, body),
        (-0.24, -0.14, 0.58, 0.16, 0.14, 0.16, body),
        (0.04, -0.02, 0.84, 0.22, 0.22, 0.20, body),
        (-0.14, 0.20, 0.92, 0.12, 0.12, 0.14, top),
        (0.20, -0.16, 1.02, 0.11, 0.11, 0.12, top),
        (-0.04, 0.02, 1.20, 0.12, 0.12, 0.14, top),
    )
    for i, (cx, cy, cz, sx, sy, sz, color) in enumerate(clumps):
        _mound(v, cx, cy, cz, sx, sy, sz, color, taper=0.7, jitter=0.12, seed=i + 1)
    return np.array(v, dtype="f4")


def build_bush_dry_mesh():
    v = []
    rng = random.Random(77)
    bark = (0.44, 0.25, 0.30)
    bark_old = (0.36, 0.21, 0.26)
    leaves = (0.52, 0.34, 0.24)
    leaves_dark = (0.42, 0.30, 0.22)
    _mound(v, 0.0, 0.0, 0.0, 0.10, 0.09, 0.08, bark_old, taper=0.55, jitter=0.1, seed=3)
    tips = []
    stems = 9
    for i in range(stems):
        a = math.tau * (i + rng.uniform(-0.25, 0.25)) / stems
        base = (math.cos(a) * 0.03, math.sin(a) * 0.03, 0.04)
        reach = rng.uniform(0.30, 0.44)
        top = rng.uniform(0.70, 0.98)
        kink = (math.cos(a) * reach * 0.55, math.sin(a) * reach * 0.55, top * 0.45)
        a2 = a + rng.uniform(-0.3, 0.3)
        end = (math.cos(a2) * reach, math.sin(a2) * reach, top)
        col = bark if i % 3 else bark_old
        _stick(v, base, kink, 0.026, 0.018, col)
        _stick(v, kink, end, 0.018, 0.008, col)
        tips.append((kink, end))
        for f, side in ((0.35, 1), (0.7, -1)):
            p0 = tuple(kink[k] + (end[k] - kink[k]) * f for k in range(3))
            b = a2 + side * rng.uniform(0.5, 0.9)
            ln = rng.uniform(0.12, 0.20)
            p1 = (p0[0] + math.cos(b) * ln, p0[1] + math.sin(b) * ln, p0[2] + ln * rng.uniform(0.5, 1.0))
            _stick(v, p0, p1, 0.011, 0.006, col)
            tips.append((p0, p1))
            for fork in (-0.6, 0.6):
                c = b + fork
                fl = ln * 0.45
                _stick(v, p1, (p1[0] + math.cos(c) * fl, p1[1] + math.sin(c) * fl, p1[2] + fl * 0.8),
                       0.006, 0.004, col)
    for i in range(5):
        a = math.tau * (i + 0.3) / 5
        _mound(v, math.cos(a) * 0.16, math.sin(a) * 0.16, 0.0, 0.13, 0.12, 0.16, leaves_dark,
               taper=0.45, jitter=0.35, seed=20 + i)
    for k, (p0, p1) in enumerate(tips[1::5]):
        for j in range(3):
            f = 0.45 + 0.2 * j
            tx, ty, tz = (p0[q] + (p1[q] - p0[q]) * f for q in range(3))
            ox, oy = rng.uniform(-0.02, 0.02), rng.uniform(-0.02, 0.02)
            r = rng.uniform(0.03, 0.05)
            _mound(v, tx + ox, ty + oy, tz, r, r * rng.uniform(0.7, 1.3), r * 0.55,
                   leaves if (k + j) % 2 else leaves_dark, taper=0.8, jitter=0.4, seed=40 + k * 3 + j, closed=True)
    return np.array(v, dtype="f4")


def build_rock_flat_mesh():
    v = []
    c1 = (0.46, 0.45, 0.42)
    c2 = (0.37, 0.36, 0.33)
    _mound(v, 0.0, 0.0, 0.10, 0.44, 0.36, 0.10, c1, taper=0.82, jitter=0.10, seed=3)
    _mound(v, -0.06, 0.04, 0.26, 0.32, 0.26, 0.07, c2, taper=0.62, jitter=0.18, seed=4)
    _mound(v, 0.36, -0.30, 0.12, 0.14, 0.12, 0.12, c2, taper=0.55, jitter=0.2, seed=5, lean=(0.03, -0.02))
    return np.array(v, dtype="f4")


def build_rock_tall_mesh():
    v = []
    c1 = (0.44, 0.43, 0.40)
    c2 = (0.35, 0.34, 0.31)
    moss = (0.26, 0.33, 0.19)
    _mound(v, 0.0, 0.0, 0.21, 0.27, 0.25, 0.21, moss, taper=0.80, jitter=0.08, seed=7)
    _mound(v, 0.02, 0.01, 0.78, 0.216, 0.20, 0.36, c1, taper=0.55, jitter=0.12, seed=8, lean=(0.07, 0.03))
    _mound(v, -0.26, 0.14, 0.07, 0.10, 0.09, 0.07, c2, taper=0.5, jitter=0.2, seed=9)
    return np.array(v, dtype="f4")


def build_rock_cluster_mesh():
    v = []
    c1 = (0.48, 0.46, 0.43)
    c2 = (0.38, 0.37, 0.34)
    chunks = (
        (-0.20, -0.12, 0.14, 0.18, 0.16, 0.14, c1),
        (0.22, 0.14, 0.10, 0.14, 0.13, 0.10, c2),
        (0.14, -0.28, 0.07, 0.10, 0.09, 0.07, c1),
        (-0.30, 0.28, 0.06, 0.09, 0.08, 0.06, c2),
        (0.38, -0.06, 0.05, 0.07, 0.07, 0.05, c1),
        (-0.02, 0.30, 0.05, 0.08, 0.06, 0.05, c1),
    )
    for i, (cx, cy, cz, sx, sy, sz, color) in enumerate(chunks):
        _mound(v, cx, cy, cz, sx, sy, sz, color, taper=0.55, jitter=0.22, seed=20 + i)
    return np.array(v, dtype="f4")


VARIANT_MESH_BUILDERS = {
    "tree": TREE_MESH_BUILDERS,
    "fallen_log": (build_fallen_log_mesh, build_fallen_log_peeled_mesh, build_fallen_log_hollow_mesh),
    "tree_stump": (build_tree_stump_mesh, build_tree_stump_split_mesh, build_tree_stump_mossy_mesh),
    "bush": (build_bush_mesh, build_bush_wide_mesh, build_bush_tall_mesh, build_bush_dry_mesh),
    "rock": (build_rock_mesh, build_rock_flat_mesh, build_rock_tall_mesh, build_rock_cluster_mesh),
    "barricade": (build_barricade_mesh, build_barricade_locker_mesh, build_barricade_bed_mesh),
}


def build_portal_mesh(segments=10):
    v = []
    ring_col = (0.55, 0.30, 0.85)
    glow = (0.95, 0.75, 1.15)
    cy, cz, ry, rz = 0.0, 0.5, 0.34, 0.42
    for i in range(segments):
        ang = math.tau * i / segments
        y = cy + ry * math.cos(ang)
        z = cz + rz * math.sin(ang)
        _mini_box(v, 0.0, y, z, 0.04, 0.055, 0.055, ring_col)
    _quad(v, (0.03, cy - ry * 0.75, cz - rz * 0.75), (0.03, cy + ry * 0.75, cz - rz * 0.75),
          (0.03, cy + ry * 0.75, cz + rz * 0.75), (0.03, cy - ry * 0.75, cz + rz * 0.75), (1, 0, 0), glow)
    _quad(v, (-0.03, cy + ry * 0.75, cz - rz * 0.75), (-0.03, cy - ry * 0.75, cz - rz * 0.75),
          (-0.03, cy - ry * 0.75, cz + rz * 0.75), (-0.03, cy + ry * 0.75, cz + rz * 0.75), (-1, 0, 0), glow)
    return np.array(v, dtype="f4")


def build_sky_dome_mesh(w, h, radius=90.0, rings=10, segments=28, horizon_drop=0.18):
    cx, cy = w / 2.0, h / 2.0
    max_phi = math.pi / 2.0 + horizon_drop
    verts = []

    def pos(ring, seg):
        phi = max_phi * (ring / rings)
        theta = 2 * math.pi * (seg / segments)
        r = radius * math.sin(phi)
        x = cx + r * math.cos(theta)
        y = cy + r * math.sin(theta)
        z = radius * math.cos(phi)
        return x, y, z

    def uv(ring, seg):
        return seg / segments, ring / rings

    def emit_tri(pa, pb, pc, uva, uvb, uvc):
        for p, uvp in ((pa, uva), (pb, uvb), (pc, uvc)):
            dx, dy, dz = p[0] - cx, p[1] - cy, p[2]
            l = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
            verts.extend([p[0], p[1], p[2], -dx / l, -dy / l, -dz / l, uvp[0], uvp[1], 1.0, 1.0, 1.0])

    for ring in range(rings):
        for seg in range(segments):
            p0, p1 = pos(ring, seg), pos(ring, seg + 1)
            p2, p3 = pos(ring + 1, seg + 1), pos(ring + 1, seg)
            uv0, uv1, uv2, uv3 = uv(ring, seg), uv(ring, seg + 1), uv(ring + 1, seg + 1), uv(ring + 1, seg)
            emit_tri(p0, p1, p2, uv0, uv1, uv2)
            emit_tri(p0, p2, p3, uv0, uv2, uv3)
    return np.array(verts, dtype="f4")


WINDOW_GLASS_KEY = "window_glass"


DECAL_KINDS = frozenset(("clutter_papers", "floor_drain", "note_flat",
                         "paper_map", "pencil", "map_sheet"))
DECAL_LIFT = 0.002


class WallMaterial:
    __slots__ = ("texture", "alpha", "diffuse", "specular", "casts_shadow", "cutout")

    def __init__(self, texture=None, alpha=1.0, diffuse=1.0, specular=0.0, casts_shadow=True, cutout=False):
        self.texture = texture
        self.alpha = alpha
        self.diffuse = diffuse
        self.specular = specular
        self.casts_shadow = casts_shadow
        self.cutout = cutout


GLASS_MATERIAL = WallMaterial(alpha=0.22, diffuse=0.08, specular=0.35, casts_shadow=False)

FENCE_FRAME_KEY = "fence_frame"
CHAINLINK_MATERIAL = WallMaterial("tex_chainlink", cutout=True, casts_shadow=False)

WALL_MATERIALS = {
    S.WALL_CONCRETE: WallMaterial("tex_wall_concrete"),
    S.WALL_TILE: WallMaterial("tex_wall_tile"),
    S.WALL_METAL: WallMaterial("tex_metal"),
    S.WALL_BLOOD: WallMaterial("tex_wall_blood"),
    S.WALL_FENCE: CHAINLINK_MATERIAL,
    FENCE_FRAME_KEY: WallMaterial("tex_sheet_metal"),
    S.WALL_FOREST: WallMaterial("tex_wall_forest", cutout=True),
    S.WALL_SHED: WallMaterial("tex_wall_shed"),
    S.WALL_BRICK: WallMaterial("tex_wall_brick"),
    S.WALL_WINDOW: WallMaterial("tex_window_frame"),
    WINDOW_GLASS_KEY: GLASS_MATERIAL,
    S.WALL_BARS: WallMaterial("tex_metal"),
}


def wall_material(key):
    return WALL_MATERIALS.get(key, WALL_MATERIALS[S.WALL_CONCRETE])


def wall_face_color(tile, tone=1.0, tint=(1.0, 1.0, 1.0)):
    base = S.WALL_BASE_COLORS.get(tile, S.WALL_BASE_COLORS[S.WALL_CONCRETE])
    k = S.WALL_TONE_MIX
    return tuple((base[i] * (1.0 - k) + S.WALL_TONE_BASE[i] * k) / 255.0 * tone * tint[i]
                 for i in range(3))


WINDOW_SILL_FRAC = S.WINDOW_SILL_FRAC
WINDOW_LINTEL_FRAC = 0.87
WINDOW_JAMB = 0.07
WINDOW_MULLION = 0.015
WINDOW_FRAME_HALF_DEPTH = 0.07
WINDOW_GLASS_COLOR = (0.46, 0.60, 0.66)


def window_axis(maze, x, y):
    return maze.window_axis(x, y)


def _build_window_cell(v_frame, v_glass, x, y, h, maze, apron=None):
    axis = window_axis(maze, x, y) or "y"
    cx, cy = x + 0.5, y + 0.5

    def joins(cell):
        nx_, ny_ = cell
        return (0 <= nx_ < maze.w and 0 <= ny_ < maze.h and maze.grid[ny_][nx_] == S.WALL_WINDOW
                and (window_axis(maze, nx_, ny_) or "y") == axis)

    if axis == "y":
        def P(u, n, z):
            return (x + u, cy + n, z)

        def Nrm(nu, nn, nz):
            return (nu, nn, nz)
        before, after = (x - 1, y), (x + 1, y)
    else:
        def P(u, n, z):
            return (cx + n, y + u, z)

        def Nrm(nu, nn, nz):
            return (nn, nu, nz)
        before, after = (x, y - 1), (x, y + 1)
    frame = wall_face_color(S.WALL_WINDOW)
    sill, lintel = h * WINDOW_SILL_FRAC, h * WINDOW_LINTEL_FRAC
    hd = WINDOW_FRAME_HALF_DEPTH
    m0 = WINDOW_MULLION if joins(before) else WINDOW_JAMB
    m1 = WINDOW_MULLION if joins(after) else WINDOW_JAMB

    def face(u0, u1, z0, z1, n):
        uv = dict(uv_scale=(u1 - u0, (z1 - z0) / h), uv_offset=(u0, z0 / h))
        _quad(v_frame, P(u0, n, z0), P(u1, n, z0), P(u1, n, z1), P(u0, n, z1), Nrm(0, 1 if n > 0 else -1, 0), frame, **uv)

    def wall_face_panel(u0, u1, z0, z1, n):
        if apron is None:
            face(u0, u1, z0, z1, n)
            return
        d = Nrm(0, 1 if n > 0 else -1, 0)
        vlist, color = apron(int(round(d[0])), int(round(d[1])))
        if vlist is None:
            face(u0, u1, z0, z1, n)
            return
        uv = dict(uv_scale=(u1 - u0, (z1 - z0) / h), uv_offset=(u0, z0 / h))
        _quad(vlist, P(u0, n, z0), P(u1, n, z0), P(u1, n, z1), P(u0, n, z1), d, color, **uv)

    for n in (-hd, hd):
        wall_face_panel(0.0, 1.0, 0.0, sill, n)
        wall_face_panel(0.0, 1.0, lintel, h, n)
        face(0.0, m0, sill, lintel, n)
        face(1.0 - m1, 1.0, sill, lintel, n)
    depth_uv = dict(uv_scale=(1.0 - m0 - m1, 2 * hd), uv_offset=(m0, 0.0))
    _quad(v_frame, P(m0, -hd, lintel), P(1 - m1, -hd, lintel), P(1 - m1, hd, lintel), P(m0, hd, lintel),
          Nrm(0, 0, -1), frame, **depth_uv)
    _quad(v_frame, P(m0, -hd, sill), P(1 - m1, -hd, sill), P(1 - m1, hd, sill), P(m0, hd, sill),
          Nrm(0, 0, 1), frame, **depth_uv)
    jamb_uv = dict(uv_scale=(2 * hd, (lintel - sill) / h), uv_offset=(0.0, sill / h))
    _quad(v_frame, P(m0, -hd, sill), P(m0, hd, sill), P(m0, hd, lintel), P(m0, -hd, lintel), Nrm(1, 0, 0), frame, **jamb_uv)
    _quad(v_frame, P(1 - m1, -hd, sill), P(1 - m1, hd, sill), P(1 - m1, hd, lintel), P(1 - m1, -hd, lintel),
          Nrm(-1, 0, 0), frame, **jamb_uv)
    cap_uv = dict(uv_scale=(2 * hd, 1.0), uv_offset=(0.0, 0.0))
    if maze.is_walkable_cell(*before):
        _quad(v_frame, P(0, -hd, 0), P(0, hd, 0), P(0, hd, h), P(0, -hd, h), Nrm(-1, 0, 0), frame, **cap_uv)
    if maze.is_walkable_cell(*after):
        _quad(v_frame, P(1, -hd, 0), P(1, hd, 0), P(1, hd, h), P(1, -hd, h), Nrm(1, 0, 0), frame, **cap_uv)
    _quad(v_glass, P(m0, 0, sill), P(1 - m1, 0, sill), P(1 - m1, 0, lintel), P(m0, 0, lintel),
          Nrm(0, 1, 0), WINDOW_GLASS_COLOR)


BARS_ROD_SPACING = 0.1
BARS_ROD_HALF = 0.013
BARS_POST_HALF = 0.024
BARS_RAIL_HALF = (0.017, 0.02)


def bars_arms(maze, x, y):
    return maze.bars_arms(x, y)


def _build_bars_cell(v, x, y, h, maze):
    color = tuple(c / 255.0 for c in S.WALL_BASE_COLORS[S.WALL_BARS])
    arms = bars_arms(maze, x, y)
    cx, cy = x + 0.5, y + 0.5
    for dx, dy in arms:
        d = BARS_ROD_SPACING / 2
        while d < 0.5:
            _mini_box(v, cx + dx * d, cy + dy * d, h / 2, BARS_ROD_HALF, BARS_ROD_HALF, h / 2, color, skip_bottom=True)
            d += BARS_ROD_SPACING
        hd, hh = BARS_RAIL_HALF
        rail = 0.2485
        for z in (0.06, h * 0.5, h - 0.06):
            if dx:
                _mini_box(v, cx + dx * rail, cy, z, rail, hd, hh, color)
            else:
                _mini_box(v, cx, cy + dy * rail, z, hd, rail, hh, color)
        if maze.is_walkable_cell(x + dx, y + dy):
            _mini_box(v, cx + dx * 0.5, cy + dy * 0.5, h / 2, BARS_POST_HALF, BARS_POST_HALF, h / 2, color,
                      skip_bottom=True)
    if len(arms) > 2 or (len(arms) == 2 and arms[0][0] != -arms[1][0]):
        _mini_box(v, cx, cy, h / 2, BARS_POST_HALF, BARS_POST_HALF, h / 2, color, skip_bottom=True)


DEPTH_CHUNK_CELLS = 8
_WALL_VERTEX_FLOATS = 11


def build_depth_wall_chunks(maze, chunk_cells=DEPTH_CHUNK_CELLS, walls=None):
    grouped = {}
    for tile_type, data in (walls if walls is not None else build_maze_walls_by_type(maze)).items():
        if not wall_material(tile_type).casts_shadow:
            continue
        tris = data.reshape(-1, 3, _WALL_VERTEX_FLOATS)
        centroid_xy = tris[:, :, 0:2].mean(axis=1)
        keys = np.floor(centroid_xy / chunk_cells).astype(np.int64)
        for key in {tuple(k) for k in keys.tolist()}:
            mask = (keys[:, 0] == key[0]) & (keys[:, 1] == key[1])
            grouped.setdefault(key, []).append(tris[mask])
    chunks = []
    for key in sorted(grouped):
        tris = np.concatenate(grouped[key], axis=0)
        xy = tris[:, :, 0:2].reshape(-1, 2)
        bounds = (float(xy[:, 0].min()), float(xy[:, 1].min()),
                  float(xy[:, 0].max()), float(xy[:, 1].max()))
        chunks.append((np.ascontiguousarray(tris.reshape(-1, _WALL_VERTEX_FLOATS), dtype="f4"), bounds))
    return chunks


TREELINE_CELLS_PER_REPEAT = 4


_OUTDOOR_TILES = (S.WALL_FOREST, S.WALL_FENCE)
_OUTDOOR_AIR_TILES = (S.WALL_OUTDOOR,) + _OUTDOOR_TILES
MOON_OUTDOOR_SHOW_PAD = 0.5


def _build_treeline_cell(v, x, y, h, maze):
    white = (1.0, 1.0, 1.0)
    k = 1.0 / TREELINE_CELLS_PER_REPEAT
    if maze.is_see_through(x + 1, y):
        _quad(v, (x + 1, y, 0), (x + 1, y + 1, 0), (x + 1, y + 1, h), (x + 1, y, h), (1, 0, 0), white,
              uv_scale=(k, 1.0), uv_offset=(y * k, 0.0))
    if maze.is_see_through(x - 1, y):
        _quad(v, (x, y + 1, 0), (x, y, 0), (x, y, h), (x, y + 1, h), (-1, 0, 0), white,
              uv_scale=(k, 1.0), uv_offset=(-(y + 1) * k, 0.0))
    if maze.is_see_through(x, y + 1):
        _quad(v, (x + 1, y + 1, 0), (x, y + 1, 0), (x, y + 1, h), (x + 1, y + 1, h), (0, 1, 0), white,
              uv_scale=(k, 1.0), uv_offset=(-(x + 1) * k, 0.0))
    if maze.is_see_through(x, y - 1):
        _quad(v, (x, y, 0), (x + 1, y, 0), (x + 1, y, h), (x, y, h), (0, -1, 0), white,
              uv_scale=(k, 1.0), uv_offset=(x * k, 0.0))


FENCE_POST_R = 0.028
FENCE_RAIL_R = 0.015
FENCE_MESH_GAP = 0.02
FENCE_BARB_ARM = 0.10
FENCE_BARB_R = 0.006


def _world_rod(v, a, b, r, color, sides=6, cap=False):
    ax, ay, az = a
    dx, dy, dz = b[0] - ax, b[1] - ay, b[2] - az
    length = math.hypot(math.hypot(dx, dy), dz) or 1.0
    ux, uy, uz = dx / length, dy / length, dz / length
    px, py, pz = ((-uy, ux, 0.0) if abs(uz) < 0.95 else (1.0, 0.0, 0.0))
    pl = math.hypot(math.hypot(px, py), pz)
    px, py, pz = px / pl, py / pl, pz / pl
    qx, qy, qz = uy * pz - uz * py, uz * px - ux * pz, ux * py - uy * px
    ring = []
    for i in range(sides):
        ang = math.tau * (i + 0.5) / sides
        ca, sa = math.cos(ang) * r, math.sin(ang) * r
        ring.append((px * ca + qx * sa, py * ca + qy * sa, pz * ca + qz * sa))
    for i in range(sides):
        o0, o1 = ring[i], ring[(i + 1) % sides]
        n = ((o0[0] + o1[0]) / 2, (o0[1] + o1[1]) / 2, (o0[2] + o1[2]) / 2)
        _quad(v, (ax + o0[0], ay + o0[1], az + o0[2]), (ax + o1[0], ay + o1[1], az + o1[2]),
              (b[0] + o1[0], b[1] + o1[1], b[2] + o1[2]), (b[0] + o0[0], b[1] + o0[1], b[2] + o0[2]), n, color)
    if cap:
        _quad(v, (b[0] + ring[0][0], b[1] + ring[0][1], b[2] + ring[0][2]),
              (b[0] + ring[1][0], b[1] + ring[1][1], b[2] + ring[1][2]),
              (b[0] + ring[3][0], b[1] + ring[3][1], b[2] + ring[3][2]),
              (b[0] + ring[4][0], b[1] + ring[4][1], b[2] + ring[4][2]), (ux, uy, uz), color)


def fence_panel_sides(maze, x, y):
    return [(dx, dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
            if maze.is_walkable_cell(x + dx, y + dy)]


def _build_fence_cell(mesh_v, frame_v, x, y, h, maze, gaps, blanks=()):
    steel = (0.60, 0.62, 0.63)
    dark = (0.42, 0.44, 0.45)
    white = (1.0, 1.0, 1.0)
    posts = set()
    for dx, dy in fence_panel_sides(maze, x, y):
        if ((x, y), (dx, dy)) in blanks:
            continue
        if dx:
            bx = x + (1 if dx > 0 else 0)
            a, b = (bx, y), (bx, y + 1)
        else:
            by = y + (1 if dy > 0 else 0)
            a, b = (x, by), (x + 1, by)
        posts.add((a, (dx, dy)))
        posts.add((b, (dx, dy)))
        top = h - 0.03
        if ((x, y), (dx, dy)) not in gaps:
            u0 = a[1] if dx else a[0]
            _quad(mesh_v, (a[0], a[1], FENCE_MESH_GAP), (b[0], b[1], FENCE_MESH_GAP), (b[0], b[1], top),
                  (a[0], a[1], top), (float(dx), float(dy), 0.0), white,
                  uv_scale=(1.0, top - FENCE_MESH_GAP), uv_offset=(u0, FENCE_MESH_GAP))
        _world_rod(frame_v, (a[0], a[1], top), (b[0], b[1], top), FENCE_RAIL_R, steel)
        _world_rod(frame_v, (a[0], a[1], 0.05), (b[0], b[1], 0.05), 0.006, dark)
        ox, oy = -dx * FENCE_BARB_ARM, -dy * FENCE_BARB_ARM
        for k, hz in ((0.45, 0.055), (1.0, 0.11)):
            _world_rod(frame_v, (a[0] + ox * k, a[1] + oy * k, h + hz), (b[0] + ox * k, b[1] + oy * k, h + hz),
                       FENCE_BARB_R, dark)
    for (px, py), (dx, dy) in posts:
        _world_rod(frame_v, (px, py, 0.0), (px, py, h + 0.02), FENCE_POST_R, steel, sides=8, cap=True)
        _world_rod(frame_v, (px, py, h - 0.03), (px - dx * FENCE_BARB_ARM, py - dy * FENCE_BARB_ARM, h + 0.12),
                   0.012, steel, sides=6, cap=True)


WALL_SEAM_OVERLAP = 0.035


def build_maze_walls_by_type(maze):
    open_sky = "yard" in getattr(maze, "layout", "")
    roofed = set()
    if open_sky:
        for area in maze.shed_roof_areas():
            roofed |= set(area)
    buckets = {}
    wall_tint = getattr(maze, "wall_tint", {})
    wall_face = getattr(maze, "wall_face", {})
    wall_face_tint = getattr(maze, "wall_face_tint", {})
    for y in range(maze.h):
        for x in range(maze.w):
            tile = maze.grid[y][x]
            if tile == S.FLOOR or tile == S.WALL_OUTDOOR:
                continue
            if tile == S.WALL_WINDOW:
                frame_v = buckets.setdefault(tile, [])
                glass_v = buckets.setdefault(WINDOW_GLASS_KEY, [])

                def apron(dx, dy, _x=x, _y=y):
                    hsh2 = (_x * 73856093) ^ (_y * 19349663)
                    tone2 = 1.0 + ((hsh2 >> 5) % 1000 / 999.0 - 0.5) * 2.0 * S.WALL_CELL_TONE_VAR
                    own = wall_face.get((_x, _y, dx, dy))
                    if own is not None:
                        ti = wall_face_tint.get((_x, _y, dx, dy), (1.0, 1.0, 1.0))
                        return buckets.setdefault(own, []), wall_face_color(own, tone2, ti)
                    step = (dy, dx)
                    for d in (1, -1):
                        for k in range(1, 6):
                            nx, ny = _x + step[0] * d * k, _y + step[1] * d * k
                            if not (0 <= nx < maze.w and 0 <= ny < maze.h):
                                break
                            t = maze.grid[ny][nx]
                            if t == S.WALL_WINDOW:
                                continue
                            if t not in WALL_MATERIALS or t in (S.WALL_FOREST, S.WALL_OUTDOOR):
                                break
                            t = wall_face.get((nx, ny, dx, dy), t)
                            hsh2 = (_x * 73856093) ^ (_y * 19349663)
                            tone2 = 1.0 + ((hsh2 >> 5) % 1000 / 999.0 - 0.5) * 2.0 * S.WALL_CELL_TONE_VAR
                            ti = wall_face_tint.get((nx, ny, dx, dy),
                                                    wall_tint.get((nx, ny), (1.0, 1.0, 1.0)))
                            return buckets.setdefault(t, []), wall_face_color(t, tone2, ti)
                    return None, None

                _build_window_cell(frame_v, glass_v, x, y, S.WALL_HEIGHT, maze, apron)
                continue
            if tile == S.WALL_BARS:
                _build_bars_cell(buckets.setdefault(tile, []), x, y, S.WALL_HEIGHT, maze)
                continue
            if tile == S.WALL_FENCE:
                _build_fence_cell(buckets.setdefault(tile, []), buckets.setdefault(FENCE_FRAME_KEY, []),
                                  x, y, S.WALL_HEIGHTS[S.WALL_FENCE], maze,
                                  getattr(maze, "fence_gap_edges", ()),
                                  getattr(maze, "fence_blank_edges", ()))
                continue
            h = S.WALL_HEIGHTS.get(tile, S.WALL_HEIGHT)
            v = buckets.setdefault(tile, [])
            if tile == S.WALL_FOREST:
                _build_treeline_cell(v, x, y, h, maze)
                continue
            hsh = (x * 73856093) ^ (y * 19349663)
            tone = 1.0 + ((hsh >> 5) % 1000 / 999.0 - 0.5) * 2.0 * S.WALL_CELL_TONE_VAR
            uoff = ((hsh >> 17) % 4) * 0.25
            tint = wall_tint.get((x, y), (1.0, 1.0, 1.0))
            color = wall_face_color(tile, tone, tint)

            def face(dx, dy):
                t = wall_face.get((x, y, dx, dy))
                if t is None or t == tile:
                    return v, color
                ti = wall_face_tint.get((x, y, dx, dy), tint)
                return buckets.setdefault(t, []), wall_face_color(t, tone, ti)

            z0 = -WALL_SEAM_OVERLAP
            z1 = h + (WALL_SEAM_OVERLAP if not open_sky else 0.0)
            def reveal_halves(dx, dy, a, b):
                nx, ny = x + dx, y + dy
                if not (0 <= nx < maze.w and 0 <= ny < maze.h) or maze.grid[ny][nx] != S.WALL_WINDOW:
                    return None
                if window_axis(maze, nx, ny) != ("y" if dx else "x"):
                    return None
                ux, uy = b[0] - a[0], b[1] - a[1]
                out = []
                for sx, sy in ((-ux, -uy), (ux, uy)):
                    key = (nx, ny, sx, sy)
                    t = wall_face.get(key)
                    if t is not None:
                        ti = wall_face_tint.get(key, (1.0, 1.0, 1.0))
                    else:
                        key = (x, y, sx, sy)
                        t = wall_face.get(key, tile)
                        ti = wall_face_tint.get(key, tint)
                    out.append((t, ti))
                whole = wall_face.get((x, y, dx, dy))
                if whole is None or whole == tile:
                    whole, whole_ti = tile, tint
                else:
                    whole_ti = wall_face_tint.get((x, y, dx, dy), tint)
                if out[0] == out[1] == (whole, whole_ti):
                    return None
                return out

            def side(dx, dy, a, b):
                halves = reveal_halves(dx, dy, a, b)
                if halves is None:
                    fv, fc = face(dx, dy)
                    _quad(fv, (a[0], a[1], z0), (b[0], b[1], z0), (b[0], b[1], z1), (a[0], a[1], z1),
                          (dx, dy, 0), fc, uv_offset=(uoff, 0.0))
                    return
                mid = ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)
                for (t, ti), (p, q), u in zip(halves, ((a, mid), (mid, b)), (0.0, 0.5)):
                    _quad(buckets.setdefault(t, []), (p[0], p[1], z0), (q[0], q[1], z0), (q[0], q[1], z1),
                          (p[0], p[1], z1), (dx, dy, 0), wall_face_color(t, tone, ti),
                          uv_scale=(0.5, 1.0), uv_offset=(uoff + u, 0.0))

            if maze.is_see_through(x + 1, y):
                side(1, 0, (x + 1, y), (x + 1, y + 1))
            if maze.is_see_through(x - 1, y):
                side(-1, 0, (x, y + 1), (x, y))
            if maze.is_see_through(x, y + 1):
                side(0, 1, (x + 1, y + 1), (x, y + 1))
            if maze.is_see_through(x, y - 1):
                side(0, -1, (x, y), (x + 1, y))
            if open_sky or h < S.WALL_HEIGHT - 1e-4:
                cap = h - 0.006 if (x, y) in roofed else h
                _quad(v, (x, y, cap), (x + 1, y, cap), (x + 1, y + 1, cap), (x, y + 1, cap), (0, 0, 1), color)
    return {t: np.array(v, dtype="f4") for t, v in buckets.items() if v}


def _plane_with_hole(v, x0, y0, x1, y1, z, normal, hole):
    up = normal[2] > 0.0
    w = max(1e-6, x1 - x0)
    h = max(1e-6, y1 - y0)

    def piece(ax, ay, bx, by):
        if bx - ax <= 1e-6 or by - ay <= 1e-6:
            return
        su, sv = (bx - ax) / w, (by - ay) / h
        if up:
            _quad(v, (ax, ay, z), (bx, ay, z), (bx, by, z), (ax, by, z), normal, (1, 1, 1),
                  uv_scale=(su, sv), uv_offset=((ax - x0) / w, (ay - y0) / h))
        else:
            _quad(v, (ax, by, z), (bx, by, z), (bx, ay, z), (ax, ay, z), normal, (1, 1, 1),
                  uv_scale=(su, sv), uv_offset=((ax - x0) / w, 1.0 - (by - y0) / h))

    if hole is None:
        piece(x0, y0, x1, y1)
        return
    hx, hy, half = hole
    a, b = max(x0, hx - half), min(x1, hx + half)
    c, d = max(y0, hy - half), min(y1, hy + half)
    piece(x0, y0, x1, c)
    piece(x0, d, x1, y1)
    piece(x0, c, a, d)
    piece(b, c, x1, d)


def _plane_per_cell(v, maze, z, normal, density, periods, tone_var):
    w, h = maze.w, maze.h
    up = normal[2] > 0.0
    step_u = 1.0 / (w * density * periods)
    step_v = 1.0 / (h * density * periods)
    grid = maze.grid
    wanted = set()
    for y in range(h):
        row = grid[y]
        for x in range(w):
            if row[x] != S.FLOOR:
                continue
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h:
                        wanted.add((nx, ny))
    wanted -= {(x, y) for y in range(h) for x in range(w)
               if grid[y][x] == S.WALL_OUTDOOR}
    for y in range(h):
        for x in range(w):
            if (x, y) not in wanted:
                continue
            hsh = (x * 83492791) ^ (y * 29986577)
            tone = 1.0 + ((hsh >> 7) % 1000 / 999.0 - 0.5) * 2.0 * tone_var
            ou = (hsh % periods) * step_u
            ov = ((hsh >> 11) % periods) * step_v
            col = (tone, tone, tone)
            u0, v0 = x / float(w) + ou, y / float(h) + ov
            su, sv = 1.0 / w, 1.0 / h
            if up:
                _quad(v, (x, y, z), (x + 1, y, z), (x + 1, y + 1, z), (x, y + 1, z),
                      normal, col, uv_scale=(su, sv), uv_offset=(u0, v0))
            else:
                _quad(v, (x, y + 1, z), (x + 1, y + 1, z), (x + 1, y, z), (x, y, z),
                      normal, col, uv_scale=(su, sv), uv_offset=(u0, v0))


def build_outdoor_ground_mesh(maze, density):
    open_cells = {(x, y) for y in range(maze.h) for x in range(maze.w)
                  if maze.grid[y][x] == S.WALL_OUTDOOR}
    cells = set(open_cells)
    for (x, y) in open_cells:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                nx, ny = x + dx, y + dy
                if not (0 <= nx < maze.w and 0 <= ny < maze.h):
                    continue
                if maze.grid[ny][nx] in (S.WALL_FENCE, S.WALL_FOREST):
                    cells.add((nx, ny))
    cells = sorted(cells)
    v = []
    for x, y in cells:
        hsh = (x * 83492791) ^ (y * 29986577)
        tone = 1.0 + ((hsh >> 7) % 1000 / 999.0 - 0.5) * 2.0 * S.FLOOR_CELL_TONE_VAR
        col = (tone, tone, tone)
        u0, v0 = x * density, y * density
        _quad(v, (x, y, 0.0), (x + 1, y, 0.0), (x + 1, y + 1, 0.0), (x, y + 1, 0.0),
              (0, 0, 1), col, uv_scale=(density, density), uv_offset=(u0, v0))
    return np.array(v, dtype="f4")


SHED_FLOOR_LIFT = 0.004
SHED_FLOOR_DIM = 0.62


EARTH_FLOOR_ZONES = {"greenhouse", "pen"}


def build_shed_floor_mesh(maze, density):
    earth = [z["rect"] for z in getattr(maze, "zones", [])
             if z.get("kind") in EARTH_FLOOR_ZONES]

    def on_earth(x, y):
        return any(x0 <= x < x1 and y0 <= y < y1 for (x0, y0, x1, y1) in earth)

    four = ((1, 0), (-1, 0), (0, 1), (0, -1))
    v = []
    for area in maze.shed_roof_areas():
        for (x, y) in sorted(area):
            tile = maze.grid[y][x]
            if tile not in (S.FLOOR, S.WALL_WINDOW) or on_earth(x, y):
                continue
            if tile == S.FLOOR:
                out = [(dx, dy) for dx, dy in four if (x + dx, y + dy) not in area]
                if len(out) > 1:
                    continue
                halves = [(-out[0][0], -out[0][1])] if out else [None]
            else:
                axis = maze.window_axis(x, y)
                sides = ((1, 0), (-1, 0)) if axis == "x" else ((0, 1), (0, -1))
                halves = [(dx, dy) for dx, dy in sides
                          if (x + dx, y + dy) in area and maze.grid[y + dy][x + dx] == S.FLOOR]
            hsh = (x * 83492791) ^ (y * 29986577)
            tone = 1.0 + ((hsh >> 7) % 1000 / 999.0 - 0.5) * 2.0 * S.FLOOR_CELL_TONE_VAR
            tone *= SHED_FLOOR_DIM
            col = (tone, tone, tone)
            z = SHED_FLOOR_LIFT
            for half in halves:
                x0, y0, x1, y1 = x, y, x + 1, y + 1
                if half is not None:
                    hx, hy = half
                    if hx:
                        x0, x1 = (x + 0.5, x + 1) if hx > 0 else (x, x + 0.5)
                    else:
                        y0, y1 = (y + 0.5, y + 1) if hy > 0 else (y, y + 0.5)
                _quad(v, (x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z),
                      (0, 0, 1), col, uv_scale=(density * (x1 - x0), density * (y1 - y0)),
                      uv_offset=(x0 * density, y0 * density))
    return np.array(v, dtype="f4")


def build_floor_mesh(maze, hole=None, density=None, per_cell=False):
    v = []
    if per_cell and hole is None:
        _plane_per_cell(v, maze, 0.0, (0, 0, 1), density, S.FLOOR_TEX_PERIODS,
                        S.FLOOR_CELL_TONE_VAR)
    else:
        _plane_with_hole(v, 0, 0, maze.w, maze.h, 0.0, (0, 0, 1), hole)
    return np.array(v, dtype="f4")


def build_ceiling_mesh(maze, hole=None, density=None, per_cell=False):
    v = []
    if per_cell and hole is None:
        _plane_per_cell(v, maze, S.WALL_HEIGHT, (0, 0, -1), density,
                        S.CEILING_TEX_PERIODS, S.FLOOR_CELL_TONE_VAR)
    else:
        _plane_with_hole(v, 0, 0, maze.w, maze.h, S.WALL_HEIGHT, (0, 0, -1), hole)
    return np.array(v, dtype="f4")


def _gray_to_rgb_bytes(arr):
    u8 = np.clip(arr * 255.0, 0, 255).astype(np.uint8)
    rgb = np.repeat(u8[:, :, None], 3, axis=2)
    return np.ascontiguousarray(rgb)


def _value_noise(size, cells, rng):
    lat = rng.random((cells, cells))
    lat = np.vstack([lat, lat[:1]])
    lat = np.hstack([lat, lat[:, :1]])
    t = np.linspace(0.0, cells, size, endpoint=False)
    i0 = t.astype(int)
    f = t - i0
    f = f * f * (3.0 - 2.0 * f)
    fy, fx = f[:, None], f[None, :]
    a = lat[i0][:, i0]
    b = lat[i0][:, i0 + 1]
    c = lat[i0 + 1][:, i0]
    d = lat[i0 + 1][:, i0 + 1]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def _fractal_noise(size, rng, octaves=(4, 8, 16, 32), gain=0.5):
    out = np.zeros((size, size))
    amp, total = 1.0, 0.0
    for cells in octaves:
        out += _value_noise(size, cells, rng) * amp
        total += amp
        amp *= gain
    return out / total


def _wall_profile(size, skirt=0.075, rail=0.40, upper=1.04):
    rows = np.arange(size)[:, None] / float(size)
    out = np.ones((size, size))
    out = np.where(rows < skirt, 0.80, out)
    out = np.where(rows < skirt * 0.35, 0.66, out)
    out += 0.16 * np.exp(-((rows - skirt) / 0.012) ** 2)
    out -= 0.10 * np.exp(-((rows - skirt * 1.5) / 0.016) ** 2)
    out += 0.14 * np.exp(-((rows - rail) / 0.010) ** 2)
    out -= 0.13 * np.exp(-((rows - (rail - 0.022)) / 0.014) ** 2)
    out = np.where(rows > rail, out * upper, out)
    return out


def _streaks(size, rng, count, width, reach, strength):
    out = np.zeros((size, size))
    rows = np.arange(size)[:, None] / float(size)
    for _ in range(count):
        cx = float(rng.integers(0, size))
        top = rng.uniform(0.55, 1.0)
        wob = (_value_noise(size, 8, rng)[:, 0:1] - 0.5) * width * 1.6
        dx = np.abs(((np.arange(size)[None, :] - cx + size / 2) % size) - size / 2)
        prof = np.exp(-((dx - wob) / width) ** 2)
        fall = np.clip((top - rows) / reach, 0.0, 1.0)
        out -= prof * fall * (1.0 - rows * 0.35) * rng.uniform(0.6, 1.0) * strength
    return out


def _make_ceiling_texture(size=128, seed=23):
    rng = np.random.default_rng(seed)
    per = 4
    panel = size // per
    rows = np.arange(size)[:, None]
    cols = np.arange(size)[None, :]
    py, px = rows // panel, cols // panel
    age = 0.74 + rng.random((per, per)) * 0.26
    base = age[py % per, px % per]
    base *= 0.93 + 0.14 * _fractal_noise(size, rng, (16, 32, 64))
    ey = np.minimum(rows % panel, panel - 1 - (rows % panel))
    ex = np.minimum(cols % panel, panel - 1 - (cols % panel))
    e = np.minimum(ey, ex)
    base = np.where(e < 1, base * 0.42, base)
    base = np.where((e >= 1) & (e < 3), base * 1.06, base)
    yy, xx = np.mgrid[:size, :size]
    for _ in range(2):
        cy, cx = rng.integers(0, size, 2)
        r2 = ((xx - cx + size / 2) % size - size / 2) ** 2 + ((yy - cy + size / 2) % size - size / 2) ** 2
        base -= np.exp(-r2 / (2 * (size * 0.14) ** 2)) * rng.uniform(0.18, 0.34)
    gone_y, gone_x = int(rng.integers(0, per)), int(rng.integers(0, per))
    hole = (py % per == gone_y) & (px % per == gone_x) & (e >= 1)
    base = np.where(hole, 0.13 + 0.05 * _value_noise(size, 32, rng), base)
    return np.clip(base, 0.10, 1.12)


def _make_basement_floor_texture(size=128, seed=71):
    rng = np.random.default_rng(seed)
    rows = np.arange(size)[:, None]
    cols = np.arange(size)[None, :]
    bay = size // 2
    base = (0.74 + 0.10 * _fractal_noise(size, rng, (4, 8, 16, 32))
            + 0.05 * _value_noise(size, 48, rng))
    shade = 0.95 + rng.random((2, 2)) * 0.10
    base *= shade[(rows // bay) % 2, (cols // bay) % 2]
    jy = np.minimum(rows % bay, bay - 1 - (rows % bay))
    jx = np.minimum(cols % bay, bay - 1 - (cols % bay))
    base -= 0.16 * np.exp(-np.minimum(jy, jx) / 1.4)
    for _ in range(5):
        px, py = float(rng.integers(0, size)), float(rng.integers(0, size))
        ang = rng.uniform(0, math.tau)
        for _ in range(int(rng.integers(size // 3, size))):
            ang += rng.uniform(-0.22, 0.22)
            px += math.cos(ang)
            py += math.sin(ang)
            base[int(py) % size, int(px) % size] *= 0.62
    yy, xx = np.mgrid[:size, :size]
    for _ in range(3):
        cy, cx = rng.integers(0, size, 2)
        r2 = ((xx - cx + size / 2) % size - size / 2) ** 2 + ((yy - cy + size / 2) % size - size / 2) ** 2
        base -= np.exp(-r2 / (2 * (size * 0.13) ** 2)) * rng.uniform(0.06, 0.20)
    return np.clip(base, 0.08, 1.10)


def _make_pipes_ceiling_texture(size=128, seed=73):
    rng = np.random.default_rng(seed)
    rows = np.arange(size)[:, None]
    cols = np.arange(size)[None, :]
    base = 0.34 + 0.16 * _fractal_noise(size, rng, (5, 10, 20, 40))
    rib = (rows % (size // 4)) / float(size // 4)
    base += 0.10 * np.exp(-((rib - 0.5) / 0.18) ** 2)
    base -= 0.09 * np.exp(-(rib / 0.05) ** 2)
    for centre, width, tone, lag in ((0.22, 0.045, 0.66, True), (0.68, 0.030, 0.52, False)):
        d = np.abs(((rows / float(size)) - centre + 0.5) % 1.0 - 0.5)
        body = np.exp(-(d / width) ** 2)
        shade = tone * (1.0 - 0.35 * (d / width) ** 2)
        if lag:
            shade = shade * (0.90 + 0.14 * ((cols // 5) % 2))
        base = base * (1.0 - body) + shade * body
        for cx in range(3, size, size // 6):
            r2 = (cols - cx) ** 2 + ((rows / float(size) - centre) * size / 1.4) ** 2
            base -= 0.16 * np.exp(-r2 / 6.0)
    yy, xx = np.mgrid[:size, :size]
    for _ in range(4):
        cy, cx = rng.integers(0, size, 2)
        r2 = ((xx - cx + size / 2) % size - size / 2) ** 2 + ((yy - cy + size / 2) % size - size / 2) ** 2
        base -= np.exp(-r2 / (2 * (size * 0.11) ** 2)) * rng.uniform(0.05, 0.14)
    return np.clip(base, 0.06, 1.05)


def _make_wall_detail_texture(size=128, seed=7):
    rng = np.random.default_rng(seed)
    rows = np.arange(size)[:, None] / float(size)
    cols = np.arange(size)[None, :] / float(size)
    base = 0.84 + (_fractal_noise(size, rng, (5, 10, 20, 40)) - 0.5) * 0.30
    boards = 4
    course = np.clip((rows[:, 0] * boards).astype(int), 0, boards - 1)
    base = base * (0.88 + 0.22 * rng.random(boards)[course][:, None])
    edge = (rows * boards) % 1.0
    base -= 0.22 * np.exp(-(edge / 0.020) ** 2)
    base -= 0.11 * np.exp(-((1.0 - edge) / 0.030) ** 2)
    base += 0.07 * np.exp(-((edge - 0.06) / 0.030) ** 2)
    joint = np.abs(((cols * 2.0) % 1.0) - 0.5)
    base -= 0.09 * np.exp(-(joint / 0.014) ** 2)
    yy, xx = np.mgrid[:size, :size]
    for ty in range(boards):
        for tx in range(2):
            cy = (ty + 0.5) / boards * size
            cx = (tx + 0.28) / 2.0 * size
            r2 = (((xx - cx + size / 2) % size) - size / 2) ** 2 + \
                 (((yy - cy + size / 2) % size) - size / 2) ** 2
            base -= 0.40 * np.exp(-r2 / 7.0)
            base -= 0.09 * np.exp(-r2 / 40.0)
    base += _streaks(size, rng, 5, 2.4, 0.55, 0.30)
    base -= np.exp(-(rows / 0.07) ** 2) * 0.22
    base *= _wall_profile(size)
    return np.clip(base, 0.16, 1.10)


def _make_window_frame_texture(size=64, seed=113):
    rng = np.random.default_rng(seed)
    x = np.arange(size)[None, :]
    grain = 0.84 + 0.08 * np.sin(x * (2 * np.pi / 7.0) + rng.random() * 6.0) \
        + 0.05 * np.sin(x * (2 * np.pi / 2.7))
    base = np.repeat(grain, size, axis=0) + (rng.random((size, size)) - 0.5) * 0.06
    streak = np.repeat(rng.random((1, size)) > 0.86, size, axis=0)
    base = np.where(streak, base * 0.78, base)
    worn = np.zeros((size, size))
    for _ in range(7):
        cx, cy = rng.integers(0, size, 2)
        yy, xx = np.ogrid[:size, :size]
        r2 = ((xx - cx) * 1.6) ** 2 + ((yy - cy) * 0.6) ** 2
        worn += np.exp(-r2 / (2 * (size * 0.07) ** 2)) * rng.uniform(-0.22, -0.08)
    return np.clip(base + worn, 0.35, 1.05)


def _make_floor_texture(size=128, seed=11):
    rng = np.random.default_rng(seed)
    rows = np.arange(size)[:, None]
    cols = np.arange(size)[None, :]
    sq = size // 4
    ky, kx = rows // sq, cols // sq
    checker = ((ky + kx) % 2)
    wear = 0.94 + rng.random((4, 4)) * 0.10
    base = np.where(checker == 0, 0.90, 0.70) * wear[ky % 4, kx % 4]
    seam = np.abs(((rows / float(size)) % 0.5) - 0.0)
    base -= 0.07 * np.exp(-(seam / 0.012) ** 2)
    base *= 0.96 + 0.08 * _fractal_noise(size, rng, (6, 12, 24, 48))
    for _ in range(26):
        y0 = int(rng.integers(0, size))
        x0 = int(rng.integers(0, size))
        ln = int(rng.integers(4, 18))
        dy = rng.uniform(-0.35, 0.35)
        for k in range(ln):
            base[(y0 + int(k * dy)) % size, (x0 + k) % size] *= rng.uniform(0.86, 0.96)
    return np.clip(base, 0.14, 1.18)


def _make_wood_texture(size=64, seed=31):
    rng = np.random.default_rng(seed)
    y = np.arange(size)[:, None].astype(np.float64)
    grain = 0.78 + 0.16 * np.sin(y * 0.85) + 0.07 * np.sin(y * 3.4 + 1.1) + 0.05 * np.sin(y * 9.0 + 2.0)
    grain = np.repeat(grain, size, axis=1)
    knots = np.zeros((size, size))
    for _ in range(3):
        cx, cy = rng.integers(0, size, 2)
        yy, xx = np.ogrid[:size, :size]
        r2 = (xx - cx) ** 2 + ((yy - cy) * 0.4) ** 2
        knots += np.exp(-r2 / (2 * (size * 0.05) ** 2)) * -0.35
    noise = rng.random((size, size)) * 0.06
    return np.clip(grain + knots + noise - 0.03, 0.35, 1.2)


def _make_metal_texture(size=128, seed=41):
    rng = np.random.default_rng(seed)
    rows = np.arange(size)[:, None]
    cols = np.arange(size)[None, :]
    per = 2
    panel = size // per
    py, px = rows // panel, cols // panel
    base = (0.80 + rng.random((per, per)) * 0.16)[py % per, px % per]
    base = base * (0.94 + 0.10 * _value_noise(size, 40, rng))
    base *= 0.97 + 0.06 * _fractal_noise(size, rng, (6, 12, 24))
    ey = np.minimum(rows % panel, panel - 1 - (rows % panel))
    ex = np.minimum(cols % panel, panel - 1 - (cols % panel))
    e = np.minimum(ey, ex)
    base = np.where(e < 2, base * 0.52, base)
    base = np.where((e >= 2) & (e < 4), base * 1.05, base)
    yy, xx = np.mgrid[:size, :size]
    step = panel // 4
    for oy in range(step // 2, panel, step):
        for ox in range(step // 2, panel, step):
            if not (oy < 5 or oy > panel - 6 or ox < 5 or ox > panel - 6):
                continue
            for gy in range(per):
                for gx in range(per):
                    cy, cx = gy * panel + oy, gx * panel + ox
                    r2 = (xx - cx) ** 2 + (yy - cy) ** 2
                    base -= 0.30 * np.exp(-r2 / 2.2)
                    base += 0.10 * np.exp(-((xx - cx) ** 2 + (yy - cy + 1.2) ** 2) / 1.6)
    base -= np.clip((_fractal_noise(size, rng, (4, 8, 16)) - 0.62) / 0.16, 0.0, 1.0) * 0.22
    base *= _wall_profile(size, skirt=0.06, rail=0.40, upper=1.02)
    return np.clip(base, 0.16, 1.16)


def _make_sheet_metal_texture(size=64, seed=43):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[:size, :size]
    brushed = 0.9 + (rng.random((size, 1)) - 0.5) * 0.10
    base = np.broadcast_to(brushed, (size, size)).copy()
    for _ in range(6):
        cx, cy = rng.random() * size, rng.random() * size
        rad = size * (0.12 + rng.random() * 0.18)
        dx = np.minimum(np.abs(xx - cx), size - np.abs(xx - cx))
        dy = np.minimum(np.abs(yy - cy), size - np.abs(yy - cy))
        base -= 0.07 * np.exp(-(dx * dx + dy * dy) / (rad * rad))
    for _ in range(9):
        x0, y0 = rng.random() * size, rng.random() * size
        ang = rng.random() * math.pi
        length = 4 + rng.random() * 12
        for t in np.linspace(0.0, length, int(length * 2)):
            px, py = int(x0 + math.cos(ang) * t) % size, int(y0 + math.sin(ang) * t) % size
            base[py, px] += 0.06
    base += (rng.random((size, size)) - 0.5) * 0.05
    return np.clip(base - 0.08, 0.3, 1.2)


def _make_tile_texture(size=128, seed=17):
    rng = np.random.default_rng(seed)
    per = 8
    tile = size // per
    rows = np.arange(size)[:, None]
    cols = np.arange(size)[None, :]
    ty, tx = rows // tile, cols // tile
    shade = 0.90 + rng.random((per, per)) * 0.13
    base = shade[ty % per, tx % per]
    gy = np.minimum(rows % tile, tile - 1 - (rows % tile)) / float(tile)
    gx = np.minimum(cols % tile, tile - 1 - (cols % tile)) / float(tile)
    g = np.minimum(gy, gx)
    grout = np.clip((0.085 - g) / 0.085, 0.0, 1.0)
    base = base * (1.0 - grout) + (0.40 + 0.10 * _value_noise(size, 24, rng)) * grout
    base *= 0.97 + 0.06 * _fractal_noise(size, rng, (8, 16, 32))
    gone = rng.random((per, per)) < 0.045
    base = np.where(gone[ty % per, tx % per], 0.46 + 0.10 * _value_noise(size, 32, rng), base)
    chip = rng.random((per, per)) < 0.10
    corner = ((rows % tile) < tile * 0.3) & ((cols % tile) < tile * 0.3)
    base = np.where(chip[ty % per, tx % per] & corner, base * 0.66, base)
    base += _streaks(size, rng, 3, 2.2, 0.5, 0.18)
    base *= _wall_profile(size)
    return np.clip(base, 0.18, 1.14)


def _make_blood_texture(size=128, seed=51):
    rng = np.random.default_rng(seed)
    rows = np.arange(size)[:, None] / float(size)
    base = 0.86 + (_fractal_noise(size, rng, (4, 8, 16, 32)) - 0.5) * 0.22
    bare = _fractal_noise(size, rng, (3, 6, 12))
    mask = np.clip((bare - 0.52) / 0.10, 0.0, 1.0)
    under = 0.54 + 0.18 * _fractal_noise(size, rng, (12, 24, 48))
    base = base * (1.0 - mask) + under * mask
    base += _streaks(size, rng, 7, 1.9, 0.75, 0.34)
    reach = np.exp(-((rows - 0.38) / 0.16) ** 2)
    for _ in range(22):
        y0 = int(np.clip(rng.normal(size * 0.38, size * 0.14), 2, size - 3))
        x0 = int(rng.integers(0, size))
        ln = int(rng.integers(3, 14))
        dy = rng.uniform(-0.5, 0.5)
        for k in range(ln):
            yy = (y0 + int(k * dy)) % size
            base[yy, (x0 + k) % size] *= 0.78 + 0.18 * (1.0 - float(reach[yy, 0]))
    base -= np.exp(-(rows / 0.07) ** 2) * 0.18
    base *= _wall_profile(size)
    return np.clip(base, 0.12, 1.10)


CHAINLINK_WIRE_HALF = 1.5
CHAINLINK_WIRE_TONE = 0.72


def _make_chainlink_texture(size=128, period=16, seed=91):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[:size, :size].astype(float)
    d1 = np.abs(((xx + yy) % period) - period / 2.0) / math.sqrt(2.0)
    d2 = np.abs(((xx - yy) % period) - period / 2.0) / math.sqrt(2.0)
    wire = np.minimum(d1, d2)
    half = CHAINLINK_WIRE_HALF
    alpha = np.clip(half + 0.5 - wire, 0.0, 1.0)
    over = d1 < d2
    tone = np.where(over, 0.70, 0.56) * CHAINLINK_WIRE_TONE
    tone = tone * (1.0 - 0.28 * np.clip(wire / half, 0.0, 1.0))
    tone = tone * (0.93 + 0.14 * rng.random((size, size)))
    rgb = np.stack([tone * 1.0, tone * 1.02, tone * 1.04], axis=-1)
    for _ in range(7):
        cx, cy = rng.integers(0, size, 2)
        r2 = (xx - cx) ** 2 + (yy - cy) ** 2
        blob = np.exp(-r2 / (2 * (size * rng.uniform(0.04, 0.10)) ** 2))[..., None]
        rgb = rgb * (1 - blob * 0.7) + blob * 0.7 * np.array([0.34, 0.20, 0.11])
    rgb *= (0.72 + 0.28 * np.clip(yy / (size * 0.35), 0.0, 1.0))[..., None]
    return np.clip(np.concatenate([rgb, alpha[..., None]], axis=-1), 0.0, 1.0)


def _make_fence_texture(size=64, seed=81):
    rng = np.random.default_rng(seed)
    base = np.full((size, size), 0.30)
    x = np.arange(size)[:, None].astype(np.float64)
    y = np.arange(size)[None, :].astype(np.float64)
    cell = 8.0
    d1 = np.abs(((x + y) % (cell * 2)) - cell)
    d2 = np.abs(((x - y) % (cell * 2)) - cell)
    wire = np.minimum(d1, d2)
    lattice = np.clip(1.0 - wire / 1.6, 0.0, 1.0)
    base = base + lattice * 0.62
    rust = np.zeros((size, size))
    for _ in range(4):
        cx, cy = rng.integers(0, size, 2)
        yy, xx = np.ogrid[:size, :size]
        r2 = (xx - cx) ** 2 + (yy - cy) ** 2
        rust += np.exp(-r2 / (2 * (size * 0.1) ** 2)) * rng.uniform(-0.14, -0.03)
    noise = rng.random((size, size)) * 0.05
    return np.clip(base + rust + noise, 0.06, 1.1)


def _make_roof_texture(size=64, seed=97):
    rng = np.random.default_rng(seed)
    y = np.arange(size)[:, None]
    ridge = 0.72 + 0.22 * np.sin(y * (2 * np.pi / 8.0))
    base = np.repeat(ridge, size, axis=1)
    base[:, ::16] *= 0.55
    weather = np.zeros((size, size))
    for _ in range(4):
        cx, cy = rng.integers(0, size, 2)
        yy, xx = np.ogrid[:size, :size]
        r2 = (xx - cx) ** 2 + (yy - cy) ** 2
        weather += np.exp(-r2 / (2 * (size * 0.10) ** 2)) * rng.uniform(-0.22, -0.05)
    noise = rng.random((size, size)) * 0.05
    return np.clip(base + weather + noise - 0.05, 0.08, 1.05)


ROOF_SHEET_T = 0.025
ROOF_UNITS_PER_REPEAT = 1.6
ROOF_STEP = 0.5
ROOF_SLOPE = 0.36
ROOF_RISE_MAX = 1.0
ROOF_RAFTER_STEP = 0.9
ROOF_TIE_STEP = 2.4


def _tri(v, p0, p1, p2, normal, color, uvs):
    for p, uv in zip((p0, p1, p2), uvs):
        v.extend([p[0], p[1], p[2], normal[0], normal[1], normal[2], uv[0], uv[1], color[0], color[1], color[2]])


def tri_rev(tri):
    return [tri[0], tri[2], tri[1]]


def _face_up(p0, p1, p2):
    ux, uy, uz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
    vx, vy, vz = p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]
    n = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
    if n[2] < 0.0:
        return p0, p2, p1, (-n[0], -n[1], -n[2])
    return p0, p1, p2, n


def _point_segment_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = 0.0 if (dx == 0.0 and dy == 0.0) else ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + dx * t), py - (ay + dy * t))


def _roof_field(cells, base_z):
    edges = []
    for cx, cy in cells:
        if (cx + 1, cy) not in cells:
            edges.append((cx + 1.0, cy + 0.0, cx + 1.0, cy + 1.0, 1.0, 0.0))
        if (cx - 1, cy) not in cells:
            edges.append((cx + 0.0, cy + 0.0, cx + 0.0, cy + 1.0, -1.0, 0.0))
        if (cx, cy + 1) not in cells:
            edges.append((cx + 0.0, cy + 1.0, cx + 1.0, cy + 1.0, 0.0, 1.0))
        if (cx, cy - 1) not in cells:
            edges.append((cx + 0.0, cy + 0.0, cx + 1.0, cy + 0.0, 0.0, -1.0))
    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    steps_x = int(round((max(xs) + 1 - min(xs)) / ROOF_STEP))
    steps_y = int(round((max(ys) + 1 - min(ys)) / ROOF_STEP))
    x0, y0 = min(xs), min(ys)
    field = {}
    far = 0.0
    for j in range(steps_y + 1):
        for i in range(steps_x + 1):
            px, py = x0 + i * ROOF_STEP, y0 + j * ROOF_STEP
            ix, iy = math.floor(px), math.floor(py)
            touch = [(ix, iy)]
            if px == ix:
                touch.append((ix - 1, iy))
            if py == iy:
                touch.append((ix, iy - 1))
            if px == ix and py == iy:
                touch.append((ix - 1, iy - 1))
            if not all(c in cells for c in touch):
                field[(px, py)] = 0.0
                continue
            d = min(_point_segment_dist(px, py, e[0], e[1], e[2], e[3]) for e in edges)
            field[(px, py)] = d
            far = max(far, d)
    slope = min(ROOF_SLOPE, ROOF_RISE_MAX / far) if far > 0.0 else ROOF_SLOPE
    return {p: base_z + d * slope for p, d in field.items()}, slope, edges


def _contiguous_runs(values):
    runs = []
    for v in values:
        if runs and v == runs[-1][-1] + 1:
            runs[-1].append(v)
        else:
            runs.append([v])
    return runs


def _add_shed_roof(metal, wood, cells, base_z, overhang, wall_tops):
    sheet = (0.62, 0.50, 0.42)
    sheet_dark = (0.34, 0.28, 0.24)
    timber = (0.42, 0.33, 0.22)
    timber_dark = (0.32, 0.25, 0.17)
    board = (0.48, 0.38, 0.26)
    z, slope, edges = _roof_field(cells, base_z)
    t = ROOF_SHEET_T
    k = 1.0 / ROOF_UNITS_PER_REPEAT
    step = ROOF_STEP

    def zat(x, y):
        gx, gy = math.floor(x / step) * step, math.floor(y / step) * step
        fx, fy = (x - gx) / step, (y - gy) / step
        z00 = z.get((gx, gy), base_z)
        z10 = z.get((gx + step, gy), base_z)
        z01 = z.get((gx, gy + step), base_z)
        z11 = z.get((gx + step, gy + step), base_z)
        if z00 + z11 >= z10 + z01:
            return (z00 + (z10 - z00) * fx + (z11 - z10) * fy) if fx >= fy else \
                   (z00 + (z11 - z01) * fx + (z01 - z00) * fy)
        return (z10 + (z10 - z00) * (fx - 1.0) + (z11 - z10) * fy) if fx + fy >= 1.0 else \
               (z00 + (z10 - z00) * fx + (z01 - z00) * fy)

    def facet(a, b, c, into_metal=True):
        a, b, c, n = _face_up(a, b, c)
        gx, gy = n[0], n[1]
        gl = math.hypot(gx, gy)
        if gl < 1e-6:
            gx, gy, gl = 1.0, 0.0, 1.0
        gx, gy = gx / gl, gy / gl
        uvs = [((q[0] * gx + q[1] * gy) * k, (-q[0] * gy + q[1] * gx) * k) for q in (a, b, c)]
        _tri(metal, (a[0], a[1], a[2] + t), (b[0], b[1], b[2] + t), (c[0], c[1], c[2] + t), n, sheet, uvs)
        _tri(metal, a, c, b, (-n[0], -n[1], -n[2]), sheet_dark, [uvs[0], uvs[2], uvs[1]])

    for cx, cy in sorted(cells):
        for j in range(int(1 / step)):
            for i in range(int(1 / step)):
                ax, ay = cx + i * step, cy + j * step
                p = [(ax, ay, z[(ax, ay)]), (ax + step, ay, z[(ax + step, ay)]),
                     (ax + step, ay + step, z[(ax + step, ay + step)]), (ax, ay + step, z[(ax, ay + step)])]
                if p[0][2] + p[2][2] >= p[1][2] + p[3][2]:
                    facet(p[0], p[1], p[2])
                    facet(p[0], p[2], p[3])
                else:
                    facet(p[0], p[1], p[3])
                    facet(p[1], p[2], p[3])

    for ex0, ey0, ex1, ey1, ox, oy in edges:
        n_sub = int(round(math.hypot(ex1 - ex0, ey1 - ey0) / step))
        for i in range(n_sub):
            f0, f1 = i / n_sub, (i + 1) / n_sub
            ax, ay = ex0 + (ex1 - ex0) * f0, ey0 + (ey1 - ey0) * f0
            bx, by = ex0 + (ex1 - ex0) * f1, ey0 + (ey1 - ey0) * f1
            ai = (ax, ay, z.get((ax, ay), base_z))
            bi = (bx, by, z.get((bx, by), base_z))
            ao = (ax + ox * overhang, ay + oy * overhang, ai[2] - overhang * slope)
            bo = (bx + ox * overhang, by + oy * overhang, bi[2] - overhang * slope)
            first, second, third, nrm = _face_up(ai, bi, bo)
            rev = second is not bi
            uvs = [((q[0] * ox + q[1] * oy) * k, (q[0] * oy - q[1] * ox) * k) for q in (ai, bi, bo, ao)]
            top = [(ai, 0), (bo, 2), (bi, 1)] if rev else [(ai, 0), (bi, 1), (bo, 2)]
            top2 = [(ai, 0), (ao, 3), (bo, 2)] if rev else [(ai, 0), (bo, 2), (ao, 3)]
            dn = (-nrm[0], -nrm[1], -nrm[2])
            for tri, normal, col, lift in ((top, nrm, sheet, t), (top2, nrm, sheet, t),
                                           (tri_rev(top), dn, sheet_dark, 0.0),
                                           (tri_rev(top2), dn, sheet_dark, 0.0)):
                pts = [(q[0], q[1], q[2] + lift) for q, _i in tri]
                _tri(metal, pts[0], pts[1], pts[2], normal, col, [uvs[i] for _q, i in tri])
            _quad(metal, ao, bo, (bo[0], bo[1], bo[2] + t), (ao[0], ao[1], ao[2] + t), (ox, oy, 0.0),
                  sheet_dark, uv_scale=(step * k, t * k))
            fa = (ao[0] + ox * 0.012, ao[1] + oy * 0.012, ao[2])
            fb = (bo[0] + ox * 0.012, bo[1] + oy * 0.012, bo[2])
            _quad(wood, (fa[0], fa[1], fa[2] - 0.07), (fb[0], fb[1], fb[2] - 0.07), fb, fa, (ox, oy, 0.0),
                  board, uv_scale=(step, 0.07))

    corner_cells = {}
    for cx, cy in cells:
        for c in ((cx, cy), (cx + 1, cy), (cx, cy + 1), (cx + 1, cy + 1)):
            corner_cells[c] = corner_cells.get(c, 0) + 1
    for (px, py), n_here in corner_cells.items():
        if n_here != 1:
            continue
        sx = 1.0 if (px, py) in cells or (px, py - 1) in cells else -1.0
        sy = 1.0 if (px, py) in cells or (px - 1, py) in cells else -1.0
        zc = z.get((float(px), float(py)), base_z)
        o = overhang
        a = (px, py, zc)
        b = (px + sx * o, py, zc - o * slope)
        c = (px + sx * o, py + sy * o, zc - 1.41 * o * slope)
        d = (px, py + sy * o, zc - o * slope)
        if sx * sy < 0.0:
            b, d = d, b
        uvs = [(q[0] * k, q[1] * k) for q in (a, b, c, d)]
        up = (0.0, 0.0, 1.0)
        _tri(metal, (a[0], a[1], a[2] + t), (b[0], b[1], b[2] + t), (c[0], c[1], c[2] + t), up, sheet, uvs[:3])
        _tri(metal, (a[0], a[1], a[2] + t), (c[0], c[1], c[2] + t), (d[0], d[1], d[2] + t), up, sheet,
             [uvs[0], uvs[2], uvs[3]])
        _tri(metal, a, c, b, (0.0, 0.0, -1.0), sheet_dark, [uvs[0], uvs[2], uvs[1]])
        _tri(metal, a, d, c, (0.0, 0.0, -1.0), sheet_dark, [uvs[0], uvs[3], uvs[2]])

    def beam(ax, ay, bx, by, drop, thick, col):
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length < 1e-6:
            return
        ux, uy = dx / length, dy / length
        px, py = -uy * thick, ux * thick
        n_seg = max(1, int(round(length / step)))
        for i in range(n_seg):
            t0, t1 = i / n_seg, (i + 1) / n_seg
            sx, sy = ax + dx * t0, ay + dy * t0
            ex, ey = ax + dx * t1, ay + dy * t1
            sz = zat(sx + ux * 1e-4, sy + uy * 1e-4) - 0.006
            ez = zat(ex - ux * 1e-4, ey - uy * 1e-4) - 0.006
            for side in (-1, 1):
                _quad(wood, (sx + px * side, sy + py * side, sz - drop), (ex + px * side, ey + py * side, ez - drop),
                      (ex + px * side, ey + py * side, ez), (sx + px * side, sy + py * side, sz),
                      (px * side, py * side, 0.0), col, uv_scale=(length / n_seg, drop))
            _quad(wood, (sx - px, sy - py, sz - drop), (sx + px, sy + py, sz - drop),
                  (ex + px, ey + py, ez - drop), (ex - px, ey - py, ez - drop), (0.0, 0.0, -1.0), timber_dark,
                  uv_scale=(2 * thick, length / n_seg))

    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    x0, x1, y0, y1 = min(xs), max(xs) + 1, min(ys), max(ys) + 1
    along_x = (x1 - x0) >= (y1 - y0)
    pos = (x0 if along_x else y0) + 0.5
    end = x1 if along_x else y1
    while pos < end:
        line = sorted((c[1] if along_x else c[0]) for c in cells
                      if (c[0] if along_x else c[1]) == int(pos))
        for run in _contiguous_runs(line):
            lo, hi = run[0], run[-1] + 1
            if along_x:
                beam(pos, lo, pos, hi, 0.06, 0.035, timber)
            else:
                beam(lo, pos, hi, pos, 0.06, 0.035, timber)
        pos += ROOF_RAFTER_STEP

    for (cx, cy), wall_top in wall_tops.items():
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (cx + dx, cy + dy)
            if n in wall_tops or n not in cells:
                continue
            inset = 0.015
            if dx:
                x = cx + (1 if dx > 0 else 0) + dx * inset
                a, b = (x, cy), (x, cy + 1)
                za, zb = zat(x, cy + 0.02), zat(x, cy + 0.98)
            else:
                y = cy + (1 if dy > 0 else 0) + dy * inset
                a, b = (cx, y), (cx + 1, y)
                za, zb = zat(cx + 0.02, y), zat(cx + 0.98, y)
            foot = wall_top - 0.02
            _quad(wood, (a[0], a[1], foot), (b[0], b[1], foot), (b[0], b[1], zb),
                  (a[0], a[1], za), (float(dx), float(dy), 0.0), timber_dark, uv_scale=(1.0, 0.2))


def build_yard_roofs_mesh(maze):
    metal, wood = [], []
    for cells in maze.shed_roof_areas():
        wall_tops = {c: S.WALL_HEIGHTS.get(maze.grid[c[1]][c[0]], S.WALL_HEIGHT)
                     for c in cells if maze.is_wall_cell(c[0], c[1])}
        base_z = max(wall_tops.values(), default=S.WALL_HEIGHTS.get(S.WALL_SHED, S.WALL_HEIGHT))
        _add_shed_roof(metal, wood, cells, base_z, maze.SHED_ROOF_OVERHANG, wall_tops)
    empty = np.zeros((0, 11), dtype="f4")
    return (np.array(metal, dtype="f4") if metal else empty,
            np.array(wood, dtype="f4") if wood else empty)


def _make_shed_texture(size=64, seed=87):
    rng = np.random.default_rng(seed)
    x = np.arange(size)[None, :]
    ridge = 0.75 + 0.20 * np.sin(x * (2 * np.pi / 6.0))
    base = np.repeat(ridge, size, axis=0)
    base[::15, :] *= 0.5
    rust = np.zeros((size, size))
    for _ in range(5):
        cx, cy = rng.integers(0, size, 2)
        yy, xx = np.ogrid[:size, :size]
        r2 = (xx - cx) ** 2 + (yy - cy) ** 2
        rust += np.exp(-r2 / (2 * (size * 0.08) ** 2)) * rng.uniform(-0.18, -0.04)
    noise = rng.random((size, size)) * 0.06
    return np.clip(base + rust + noise - 0.02, 0.1, 1.1)


def _make_brick_texture(size=64, seed=61):
    rng = np.random.default_rng(seed)
    course = max(4, size // 8)
    brick = max(8, size // 4)
    joint = max(1, size // 64)
    out = np.full((size, size), 0.82)
    for y in range(size):
        row = y // course
        if y % course < joint:
            out[y, :] = 0.55
            continue
        shift = (row % 2) * (brick // 2)
        xs = (np.arange(size) + shift) % brick
        out[y, xs < joint] = 0.55
    out += rng.normal(0.0, 0.035, out.shape)
    for _ in range(size // 3):
        by, bx = rng.integers(0, size // course) * course, rng.integers(0, size // brick) * brick
        out[by:by + course, bx:bx + brick] *= rng.uniform(0.82, 1.06)
    return np.clip(out, 0.1, 1.1)


def _box_wrap(a, radius):
    k = 2 * radius + 1
    out = a.copy()
    for axis in (0, 1):
        acc = np.zeros_like(out)
        for s in range(-radius, radius + 1):
            acc += np.roll(out, s, axis=axis)
        out = acc / k
    return out


def _blur_wrap(a, radius):
    if radius <= 0:
        return a
    r = max(1, radius // 2)
    return _box_wrap(_box_wrap(_box_wrap(a, r), r), r)


def _tiling_noise(rng, size, octaves):
    total = np.zeros((size, size))
    weight = 0.0
    for radius, amp in octaves:
        layer = _blur_wrap(rng.random((size, size)), radius)
        layer = (layer - layer.min()) / max(1e-9, layer.max() - layer.min())
        total += layer * amp
        weight += amp
    return total / weight


def _make_yard_ground_texture(size=64, seed=89):
    rng = np.random.default_rng(seed)
    dirt = np.array([0.42, 0.34, 0.24])
    grass = np.array([0.30, 0.40, 0.20])
    mix = rng.random((size, size))
    yy, xx = np.ogrid[:size, :size]
    for _ in range(14):
        cx, cy = rng.integers(0, size, 2)
        r2 = (xx - cx) ** 2 + (yy - cy) ** 2
        mix += np.exp(-r2 / (2 * (size * rng.uniform(0.05, 0.14)) ** 2)) * rng.uniform(-0.4, 0.4)
    mix = np.clip(mix, 0.0, 1.0)[:, :, None]
    rgb = dirt * (1 - mix) + grass * mix
    noise = (rng.random((size, size, 1)) - 0.5) * 0.08
    return np.clip((rgb + noise) * 0.62, 0.0, 1.0)


CUTOUT_PASS = 128.0 / 255.0


def _coverage_mip_levels(rgba):
    levels = [rgba]
    target = float((rgba[..., 3] >= CUTOUT_PASS).mean())
    cur = rgba
    while cur.shape[0] > 1 or cur.shape[1] > 1:
        h, w = cur.shape[0], cur.shape[1]
        if h > 1:
            cur = 0.5 * (cur[0:h - h % 2:2] + cur[1:h - h % 2:2])
        if w > 1:
            cur = 0.5 * (cur[:, 0:w - w % 2:2] + cur[:, 1:w - w % 2:2])
        a = cur[..., 3]
        lo, hi = 0.25, 16.0
        for _ in range(24):
            mid = (lo * hi) ** 0.5
            if (np.clip(a * mid, 0.0, 1.0) >= CUTOUT_PASS).mean() < target:
                lo = mid
            else:
                hi = mid
        level = cur.copy()
        level[..., 3] = np.clip(a * hi, 0.0, 1.0)
        levels.append(level)
    return levels


def _cutout_bytes(level):
    return np.ascontiguousarray(np.clip(np.rint(level * 255.0), 0, 255).astype(np.uint8))


def _make_treeline_texture(width=256, height=128, seed=83):
    rng = np.random.default_rng(seed)
    yy = np.arange(height)[:, None].astype(float)
    xx = np.arange(width)[None, :].astype(float)
    alpha = np.zeros((height, width))
    tone = np.zeros((height, width))

    def wrap_dx(cx):
        d = np.abs(xx - cx)
        return np.minimum(d, width - d)

    rag = _tiling_noise(rng, max(width, height), ((6, 1.0), (2, 0.5)))[0, :width]
    under_top = height * (0.22 + 0.14 * rag)
    under = yy <= under_top[None, :]
    alpha[under] = 1.0
    tone[under] = 0.55

    back = _tiling_noise(rng, max(width, height), ((10, 1.0), (3, 0.6)))[1, :width]
    back_top = height * (0.50 + 0.20 * back)
    back_mask = (yy <= back_top[None, :]) & ~under
    alpha[back_mask] = 1.0
    tone[back_mask] = 0.62

    trees = []
    n = 17
    base_x = np.linspace(0, width, n, endpoint=False) + rng.uniform(-6, 6, n)
    for cx in base_x:
        kind = "conifer" if rng.random() < 0.7 else "broad"
        top = height * rng.uniform(0.70, 0.99)
        trees.append((kind, cx % width, top, rng.uniform(0.72, 1.0)))
    for kind, cx, top, shade in sorted(trees, key=lambda t: t[2]):
        dx = wrap_dx(cx)
        sdx = ((xx - cx + width / 2.0) % width) - width / 2.0
        trunk_w = rng.uniform(1.0, 2.2)
        if kind == "conifer":
            crown_base = top * rng.uniform(0.22, 0.34)
            half_w = rng.uniform(11, 17)
            frac = np.clip((yy - crown_base) / max(1.0, top - crown_base), 0.0, 1.0)
            tiers = 1.0 - 0.2 * ((frac * 5.0) % 1.0)
            crown = (yy >= crown_base) & (yy <= top) & (dx <= half_w * (1.0 - frac) * tiers)
            trunk = (dx <= trunk_w) & (yy <= crown_base + 4)
        else:
            rx, ry = rng.uniform(17, 24), rng.uniform(16, 22)
            cy = top - ry
            lumps = 1.0 + 0.18 * np.sin(sdx * 0.45 + rng.uniform(0, 6)) * np.sin(yy * 0.37)
            crown = ((dx / rx) ** 2 + ((yy - cy) / ry) ** 2) <= lumps
            trunk = (dx <= trunk_w) & (yy <= cy)
        alpha[trunk] = 1.0
        tone[trunk] = 0.42 * shade
        alpha[crown] = 1.0
        tone[crown] = shade

    speckle = rng.random((height, width))
    edge = _blur_wrap(alpha, 2)
    alpha[(edge < 0.8) & (edge > 0.2) & (speckle > 0.82) & (yy > back_top[None, :])] = 0.0
    alpha[under] = 1.0

    crown_col = np.array([0.20, 0.27, 0.17])
    trunk_col = np.array([0.20, 0.16, 0.12])
    var = _tiling_noise(rng, max(width, height), ((3, 1.0), (1, 0.4)))[:height, :width]
    rgb = crown_col[None, None, :] * (0.55 + 0.45 * var[:, :, None]) * (0.6 + 0.4 * tone[:, :, None])
    trunkish = (tone < 0.45) & (tone > 0.0)
    rgb[trunkish] = trunk_col * (0.7 + 0.3 * var[trunkish][:, None])
    rgb *= (0.82 + 0.18 * (yy / height))[:, :, None]
    return np.dstack([np.clip(rgb, 0, 1), alpha])


def _make_paper_texture(size=64, seed=61):
    rng = np.random.default_rng(seed)
    row = np.arange(size)[:, None]
    line_mask = ((row % 7) == 0) & (row > 8)
    lines = 1.0 - line_mask.astype(np.float64) * 0.12
    base = np.full((size, size), 1.05) * lines
    cx, cy = rng.integers(0, size, 2)
    yy, xx = np.ogrid[:size, :size]
    r2 = (xx - cx) ** 2 + (yy - cy) ** 2
    stain = np.exp(-r2 / (2 * (size * 0.22) ** 2)) * -0.12
    noise = rng.random((size, size)) * 0.05
    return np.clip(base + stain + noise - 0.025, 0.5, 1.15)


_STATEFUL_VAO_KINDS = frozenset(
    {"locker", "door", "shed_lock", "fence_gap", "pipes"} | set(BREAKABLE_LIGHT_KINDS))


class Renderer3D:
    def __init__(self, ctx, low_res=(320, 180), snap_res=180.0):
        self.ctx = ctx
        self.low_w, self.low_h = low_res
        self.snap_res = snap_res
        self._upscale_linear = False
        self.last_camera = None
        self.last_perf = {}

        self.prog = self._build_main_program(ctx)
        self.wall_holes = []
        self.snap_props_whole = True
        self._uniform_cache = {}
        self._uniform_members = {}
        self.quad_prog = ctx.program(vertex_shader=QUAD_VERTEX_SHADER, fragment_shader=QUAD_FRAGMENT_SHADER)

        box_data = build_box_mesh()
        self.box_vbo = ctx.buffer(box_data.tobytes())
        self.box_vao = ctx.vertex_array(
            self.prog, [(self.box_vbo, "3f 3f 2f 3f", "in_pos", "in_normal", "in_uv", "in_color")]
        )

        latch_data = build_box_mesh(include_bottom=True)
        self.latch_vbo = ctx.buffer(latch_data.tobytes())
        self.latch_vao = ctx.vertex_array(
            self.prog, [(self.latch_vbo, "3f 3f 2f 3f", "in_pos", "in_normal", "in_uv", "in_color")]
        )

        locker_data = build_locker_mesh()
        self.locker_vbo = ctx.buffer(locker_data.tobytes())
        self.locker_vao = ctx.vertex_array(
            self.prog, [(self.locker_vbo, "3f 3f 2f 3f", "in_pos", "in_normal", "in_uv", "in_color")]
        )

        locker_door_data = build_locker_door_mesh()
        self.locker_door_vbo = ctx.buffer(locker_door_data.tobytes())
        self.locker_door_vao = ctx.vertex_array(
            self.prog, [(self.locker_door_vbo, "3f 3f 2f 3f", "in_pos", "in_normal", "in_uv", "in_color")]
        )

        door_data = build_door_mesh()
        self.door_vbo = ctx.buffer(door_data.tobytes())
        self.door_vao = ctx.vertex_array(
            self.prog, [(self.door_vbo, "3f 3f 2f 3f", "in_pos", "in_normal", "in_uv", "in_color")]
        )

        broken_door_data = build_broken_door_mesh()
        self.broken_door_vbo = ctx.buffer(broken_door_data.tobytes())
        self.broken_door_vao = ctx.vertex_array(
            self.prog, [(self.broken_door_vbo, "3f 3f 2f 3f", "in_pos", "in_normal", "in_uv", "in_color")]
        )

        self._prop_xy_cache = None
        self._prop_subsets = []
        self.variant_vbos, self.variant_vaos = {}, {}
        for kind, builders in VARIANT_MESH_BUILDERS.items():
            if len(builders) != PROP_DEFS[kind].get("variants", 1):
                raise RuntimeError(f"VARIANT_MESH_BUILDERS[{kind!r}] and PROP_DEFS[{kind!r}]['variants'] disagree")
            self.variant_vbos[kind] = [ctx.buffer(build().tobytes()) for build in builders]
            self.variant_vaos[kind] = [
                ctx.vertex_array(self.prog, [(vbo, "3f 3f 2f 3f", "in_pos", "in_normal", "in_uv", "in_color")])
                for vbo in self.variant_vbos[kind]
            ]

        self._mesh_builders = {
            "bed": build_bed_mesh, "desk": build_desk_mesh, "table": build_table_mesh,
            "chair": build_chair_mesh, "gurney": build_gurney_mesh, "shelf": build_shelf_mesh,
            "cabinet": build_cabinet_mesh, "vending": build_vending_mesh, "sink": build_sink_mesh,
            "mortuary_wall": build_mortuary_wall_mesh, "autopsy_table": build_autopsy_table_mesh,
            "instrument_tray": build_instrument_tray_mesh,
            "floor_drain": build_floor_drain_mesh,
            "boiler_tank": build_boiler_tank_mesh, "gauge_panel": build_gauge_panel_mesh,
            "kitchen_counter": build_kitchen_counter_mesh, "fridge_freezer": build_fridge_freezer_mesh,
            "tray_stack": build_tray_stack_mesh,
            "workbench": build_workbench_mesh, "tool_pegboard": build_tool_pegboard_mesh,
            "reception_desk": build_reception_desk_mesh, "fire_extinguisher": build_fire_extinguisher_mesh,
            "iv_stand": build_iv_stand_mesh, "wheelchair": build_wheelchair_mesh,
            "fallen_log": build_fallen_log_mesh, "tree_stump": build_tree_stump_mesh,
            "park_bench": build_park_bench_mesh, "lamppost": build_lamppost_mesh,
            "trash_can": build_trash_can_mesh, "crate": build_crate_mesh, "barrel": build_barrel_mesh,
            "pipes": build_pipes_mesh, "fuse_box": build_fuse_box_mesh, "valve_panel": build_valve_panel_mesh,
            **{"pipes_%s_%s" % (a_, b_): (lambda a_=a_, b_=b_: build_pipes_mesh(a_, b_))
               for a_ in PIPE_ENDS for b_ in PIPE_ENDS},
            "shed_lock": build_shed_lock_mesh,
            "shed_lock_1": build_shed_lock_one_mesh, "shed_lock_0": build_shed_lock_open_mesh,
            "padlock_dropped": build_padlock_dropped_mesh,
            "whetstone": build_whetstone_mesh,
            "elevator": build_elevator_mesh,
            "elevator_cabin": build_elevator_cabin_mesh,
            "elevator_arrival": build_elevator_mesh, "hatch": build_hatch_mesh,
            "hatch_arrival": build_hatch_arrival_mesh, "hatch_lid": build_hatch_lid_mesh,
            "hatch_wheel": build_hatch_wheel_mesh,
            "asylum_echo": build_asylum_echo_mesh,
            "fence_gap": build_fence_gap_mesh, "fence_gap_open": build_fence_gap_open_mesh,
            "fence_gap_cut1": build_fence_gap_cut1_mesh,
            "fence_gap_cut2": build_fence_gap_cut2_mesh,
            "battery": build_battery_mesh, "fuse": build_fuse_mesh,
            "valve_key": build_valve_key_mesh, "key": build_key_mesh, "cutters": build_cutters_mesh,
            "sanity_pill": build_sanity_pill_mesh, "lighter": build_lighter_mesh, "note_flat": build_note_flat_mesh,
            "flashlight_handheld": build_flashlight_handheld_mesh,
            "lighter_handheld": build_lighter_handheld_mesh, "lighter_flame": build_lighter_flame_mesh,
            "paper_map": build_paper_map_mesh, "pencil": build_pencil_mesh,
            "map_sheet": build_map_sheet_mesh,
            "map_handheld": build_map_handheld_mesh,
            "map_roll": build_map_roll_mesh,
            "bush": build_bush_mesh, "rock": build_rock_mesh, "portal": build_portal_mesh, "portal_live": build_portal_mesh,
            "clutter_papers": build_clutter_papers_mesh, "clutter_bottle": build_clutter_bottle_mesh,
            "clutter_junk": build_clutter_junk_mesh, "barricade": build_barricade_mesh,
            "lamp_desk": build_lamp_desk_mesh, "sign_exit": build_sign_exit_mesh,
            "wall_sconce": build_wall_sconce_mesh, "monitor": build_monitor_mesh,
            "emergency_lamp": build_emergency_lamp_mesh,
            "lamp_desk_broken": build_lamp_desk_broken_mesh,
            "wall_sconce_broken": build_wall_sconce_broken_mesh,
            "monitor_broken": build_monitor_broken_mesh,
        }
        self.prop_vaos = {}
        for kind, builder in self._mesh_builders.items():
            data = builder()
            vbo = ctx.buffer(data.tobytes())
            vao = ctx.vertex_array(self.prog, [(vbo, "3f 3f 2f 3f", "in_pos", "in_normal", "in_uv", "in_color")])
            self.prop_vaos[kind] = vao

        self._cutout_builders = {
            "fence_gap": build_fence_gap_panel_mesh,
            "fence_gap_cut1": build_fence_gap_cut1_panel_mesh,
            "fence_gap_cut2": build_fence_gap_cut2_panel_mesh,
            "fence_gap_open": build_fence_gap_open_panel_mesh,
        }
        self.cutout_vaos = {}
        for kind, builder in self._cutout_builders.items():
            data = builder()
            vbo = ctx.buffer(data.tobytes())
            self.cutout_vaos[kind] = ctx.vertex_array(
                self.prog, [(vbo, "3f 3f 2f 3f", "in_pos", "in_normal", "in_uv", "in_color")])

        self._glass_builders = {
            "vending": build_vending_glass_mesh,
        }
        self.glass_vaos = {}
        for kind, builder in self._glass_builders.items():
            data = builder()
            vbo = ctx.buffer(data.tobytes())
            vao = ctx.vertex_array(self.prog, [(vbo, "3f 3f 2f 3f", "in_pos", "in_normal", "in_uv", "in_color")])
            self.glass_vaos[kind] = vao

        self.DEBUG_CONE_SEGMENTS = 24
        cone_floats = self.DEBUG_CONE_SEGMENTS * 2 * 3 * 11
        self.debug_cone_vbo = ctx.buffer(reserve=cone_floats * 4)
        self.debug_cone_vao = ctx.vertex_array(
            self.prog, [(self.debug_cone_vbo, "3f 3f 2f 3f", "in_pos", "in_normal", "in_uv", "in_color")]
        )

        self.tex_floor_upper = self._upload_gray(_make_floor_texture(128), linear=True)
        self.tex_ceiling_upper = self._upload_gray(_make_ceiling_texture(128), linear=True)
        self.tex_floor_basement = self._upload_gray(_make_basement_floor_texture(128), linear=True)
        self.tex_ceiling_basement = self._upload_gray(_make_pipes_ceiling_texture(128), linear=True)
        self.tex_floor = self.tex_floor_upper
        self.tex_ceiling = self.tex_ceiling_upper
        self.tex_wood = self._upload_gray(_make_wood_texture(64), linear=True)
        self.tex_metal = self._upload_gray(_make_metal_texture(128), linear=True)
        self.tex_sheet_metal = self._upload_gray(_make_sheet_metal_texture(64), linear=True)
        self.tex_wall_concrete = self._upload_gray(_make_wall_detail_texture(128, 7), linear=True)
        self.tex_wall_tile = self._upload_gray(_make_tile_texture(128), linear=True)
        self.tex_wall_blood = self._upload_gray(_make_blood_texture(128), linear=True)
        self.tex_wall_fence = self._upload_gray(_make_fence_texture(64), linear=True)
        self.tex_chainlink = self._upload_cutout(_make_chainlink_texture())
        self.tex_wall_forest = self._upload_cutout(_make_treeline_texture(), clamp_v=True)
        self.tex_wall_shed = self._upload_gray(_make_shed_texture(64), linear=True)
        self.tex_wall_brick = self._upload_gray(_make_brick_texture(64), linear=True)
        self.tex_roof = self._upload_gray(_make_roof_texture(64), linear=True)
        self.tex_floor_yard = self._upload_color(_make_yard_ground_texture(), linear=True)
        self.tex_window_frame = self._upload_gray(_make_window_frame_texture(64), linear=True)
        self.wall_textures = {
            S.WALL_CONCRETE: self.tex_wall_concrete,
            S.WALL_TILE: self.tex_wall_tile,
            S.WALL_METAL: self.tex_metal,
            S.WALL_BLOOD: self.tex_wall_blood,
            S.WALL_FENCE: self.tex_chainlink,
            S.WALL_FOREST: self.tex_wall_forest,
            S.WALL_SHED: self.tex_wall_shed,
            S.WALL_BRICK: self.tex_wall_brick,
        }
        self.tex_paper = self._upload_gray(_make_paper_texture(64))
        self.prop_textures = {"wood": self.tex_wood, "metal": self.tex_sheet_metal, "paper": self.tex_paper}

        self.color_tex = None
        self.depth_rb = None
        self.fbo = None
        self._build_framebuffer()

        self.gamma = 1.0
        self.shadow_blocker_taps, self.shadow_filter_taps = SHADOW_QUALITY_TAPS["medium"]

        self.flash_shadow_res = 128
        self.POINT_SHADOW_RES = 128
        self.MOON_SHADOW_RES = 256
        self._slot_key = [None] * self.max_omni_lights
        self._slot_token = [None] * self.max_omni_lights
        self._slot_frame = [-1] * self.max_omni_lights
        self._slot_of_key = {}
        self._moon_center = None
        self._moon_token = None
        self._frame_index = 0
        self._frame_door_swings = {}
        self.last_shadow_rebuilds = []
        self.shadow_atlas_tex = None
        self.shadow_atlas_fbo = None
        self.shadow_atlas_raw_sampler = None
        self._build_shadow_atlas()

        quad_verts = np.array([
            -1, -1, 0, 0,
            1, -1, 1, 0,
            1, 1, 1, 1,
            -1, -1, 0, 0,
            1, 1, 1, 1,
            -1, 1, 0, 1,
        ], dtype="f4")
        self.quad_vbo = ctx.buffer(quad_verts.tobytes())
        self.quad_vao = ctx.vertex_array(self.quad_prog, [(self.quad_vbo, "2f 2f", "in_pos", "in_uv")])

        self.billboard_prog = ctx.program(
            vertex_shader=BILLBOARD_VERTEX_SHADER, fragment_shader=BILLBOARD_FRAGMENT_SHADER)
        billboard_verts = np.array([
            -0.5, -0.5, 0, 1,
             0.5, -0.5, 1, 1,
             0.5,  0.5, 1, 0,
            -0.5, -0.5, 0, 1,
             0.5,  0.5, 1, 0,
            -0.5,  0.5, 0, 0,
        ], dtype="f4")
        self.billboard_vbo = ctx.buffer(billboard_verts.tobytes())
        self.billboard_vao = ctx.vertex_array(
            self.billboard_prog, [(self.billboard_vbo, "2f 2f", "in_local", "in_uv")])
        self.angel_tex = None
        self.map_tex = None
        self.map_turn_tex = None
        self.map_turn = 0.0
        self.map_turn_dir = 1
        self.map_roll_pages = 0
        self.map_pencil = (1.0, 0, 0.0, 0.5, 0.5)
        self.map_paper_vbo = ctx.buffer(build_map_paper_quad().tobytes())
        self.map_paper_vao = ctx.vertex_array(
            self.prog, [(self.map_paper_vbo, "3f 3f 2f 3f", "in_pos", "in_normal", "in_uv", "in_color")])

        self.note_tex = None
        self.note_back_tex = None
        self.note_prog = ctx.program(vertex_shader=NOTE_EXAMINE_VERTEX,
                                     fragment_shader=NOTE_EXAMINE_FRAGMENT)
        self.note_examine_vbo = ctx.buffer(build_note_examine_mesh().tobytes())
        self.note_examine_vao = ctx.vertex_array(
            self.note_prog, [(self.note_examine_vbo, "3f 3f 2f 3x4",
                              "in_pos", "in_normal", "in_uv")])

        self.hud_tex = None
        self._hud_tex_raw = False
        self.wall_parts = []
        self.depth_wall_chunks = []
        self.floor_tex_density = S.FLOOR_CEILING_TEX_DENSITY
        self._depth_only = False
        self.floor_vao = None
        self.ceil_vao = None
        self.sky_dome_vao = None
        self.hallu_eyes = []
        self.outdoor_ground_vao = None
        self._outdoor_cells = None
        self.shed_floor_vao = None
        self.yard_roof_vao = None
        self.yard_roof_wood_vao = None

    def _upload_color(self, arr, linear=False):
        u8 = np.ascontiguousarray(np.clip(arr * 255.0, 0, 255).astype(np.uint8))
        tex = self.ctx.texture((u8.shape[1], u8.shape[0]), u8.shape[2], u8.tobytes())
        tex.repeat_x = True
        tex.repeat_y = True
        if linear:
            tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
            tex.build_mipmaps()
        else:
            tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        return tex

    def _upload_cutout(self, arr, clamp_v=False):
        levels = _coverage_mip_levels(arr)
        u8 = _cutout_bytes(levels[0])
        tex = self.ctx.texture((u8.shape[1], u8.shape[0]), 4, u8.tobytes())
        tex.repeat_x = True
        tex.repeat_y = not clamp_v
        tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
        tex.build_mipmaps(0, len(levels) - 1)
        for i, level in enumerate(levels[1:], start=1):
            tex.write(_cutout_bytes(level).tobytes(), level=i)
        return tex

    def _upload_gray(self, arr, linear=False):
        data = _gray_to_rgb_bytes(arr)
        tex = self.ctx.texture((data.shape[1], data.shape[0]), 3, data.tobytes())
        if linear:
            tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
            tex.repeat_x = True
            tex.repeat_y = True
            tex.build_mipmaps()
        else:
            tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
            tex.repeat_x = True
            tex.repeat_y = True
        return tex

    def _build_framebuffer(self):
        ctx = self.ctx
        if self.fbo is not None:
            self.fbo.release()
        if self.depth_rb is not None:
            self.depth_rb.release()
        if self.color_tex is not None:
            self.color_tex.release()
        low_res = (self.low_w, self.low_h)
        self.color_tex = ctx.texture(low_res, 3)
        self.color_tex.filter = (
            (moderngl.LINEAR, moderngl.LINEAR) if self._upscale_linear else (moderngl.NEAREST, moderngl.NEAREST)
        )
        self.color_tex.repeat_x = False
        self.color_tex.repeat_y = False
        self.depth_rb = ctx.depth_renderbuffer(low_res)
        self.fbo = ctx.framebuffer(color_attachments=[self.color_tex], depth_attachment=self.depth_rb)

    def set_resolution(self, low_w, low_h, snap_res):
        if (low_w, low_h) == (self.low_w, self.low_h):
            self.snap_res = snap_res
            return
        self.low_w, self.low_h = low_w, low_h
        self.snap_res = snap_res
        self._build_framebuffer()

    def set_upscale_smoothing(self, enabled):
        if enabled == self._upscale_linear:
            return
        self._upscale_linear = enabled
        if self.color_tex is not None:
            self.color_tex.filter = (
                (moderngl.LINEAR, moderngl.LINEAR) if enabled else (moderngl.NEAREST, moderngl.NEAREST)
            )

    def set_gamma(self, gamma):
        self.gamma = max(0.1, float(gamma))

    def set_shadow_quality(self, level):
        self.shadow_blocker_taps, self.shadow_filter_taps = SHADOW_QUALITY_TAPS.get(
            level, SHADOW_QUALITY_TAPS["medium"])

    def set_shadow_resolution(self, point_res, flash_res, moon_res):
        new = (int(point_res), int(flash_res), int(moon_res))
        if new == (self.POINT_SHADOW_RES, self.flash_shadow_res, self.MOON_SHADOW_RES):
            return
        self.POINT_SHADOW_RES, self.flash_shadow_res, self.MOON_SHADOW_RES = new
        self._build_shadow_atlas()

    def _build_main_program(self, ctx):
        last_error = None
        for omni in (MAX_OMNI_LIGHTS, 24, 16, 8):
            source = (FRAGMENT_SHADER.replace("__MAX_LIGHTS__", str(omni + FIXED_LIGHTS))
                      .replace("__MAX_SHADOW_TILES__", str(FIXED_TILES + 6 * omni)))
            try:
                prog = ctx.program(vertex_shader=VERTEX_SHADER, fragment_shader=source)
            except moderngl.Error as exc:
                last_error = exc
                continue
            if omni != MAX_OMNI_LIGHTS:
                print(f"[renderer] light arrays reduced to {omni} omni lights "
                      f"(the {MAX_OMNI_LIGHTS}-light program did not link on this GPU)")
            self.max_omni_lights = omni
            self.max_lights = omni + FIXED_LIGHTS
            self.max_shadow_tiles = FIXED_TILES + 6 * omni
            return prog
        raise last_error

    def _build_shadow_atlas(self):
        ctx = self.ctx
        if self.shadow_atlas_fbo is not None:
            self.shadow_atlas_fbo.release()
        if self.shadow_atlas_tex is not None:
            self.shadow_atlas_tex.release()
        width, height, rects = shadow_atlas_layout(
            self.POINT_SHADOW_RES, self.flash_shadow_res, self.MOON_SHADOW_RES, self.max_omni_lights)
        self.shadow_atlas_size = (width, height)
        self.shadow_atlas_tex = ctx.depth_texture((width, height))
        _configure_shadow_texture(self.shadow_atlas_tex)
        self.shadow_atlas_fbo = ctx.framebuffer(depth_attachment=self.shadow_atlas_tex)
        if self.shadow_atlas_raw_sampler is not None:
            self.shadow_atlas_raw_sampler.release()
        self.shadow_atlas_raw_sampler = ctx.sampler(
            texture=self.shadow_atlas_tex, filter=(moderngl.NEAREST, moderngl.NEAREST),
            compare_func="", repeat_x=False, repeat_y=False)
        self._tile_rects = rects
        self._tile_mvp = [gm.to_gl(gm.identity())] * len(rects)
        self.prog["tile_rect"].write(np.array([_rect_uniform(r) for r in rects], dtype="f4").tobytes())
        self._invalidate_shadow_cache()

    def _invalidate_shadow_cache(self):
        self._slot_key = [None] * self.max_omni_lights
        self._slot_token = [None] * self.max_omni_lights
        self._slot_frame = [-1] * self.max_omni_lights
        self._slot_of_key = {}
        self._moon_center = None
        self._moon_token = None
        self._static_occ_sig = None

    @staticmethod
    def _direction_to_view(origin, direction):
        dx, dy, dz = direction
        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        if length > 1e-9:
            dx, dy, dz = dx / length, dy / length, dz / length
        yaw = math.atan2(dy, dx)
        pitch = math.asin(max(-1.0, min(1.0, dz)))
        return gm.view_matrix(origin, yaw, pitch)

    def _render_shadow_depth_pass(self, rect, view, proj, near_props, monster=None, reach=None):
        ctx = self.ctx
        prog = self.prog

        x, y, size = rect
        atlas = self.shadow_atlas_fbo
        atlas.use()
        atlas.clear(depth=1.0, viewport=(x, y, size, size))
        ctx.viewport = (x, y, size, size)
        ctx.enable(moderngl.DEPTH_TEST)
        ctx.disable(moderngl.CULL_FACE)
        ctx.polygon_offset = SHADOW_POLYGON_OFFSET
        ctx.disable(moderngl.BLEND)

        prog["view"].write(gm.to_gl(view))
        prog["proj"].write(gm.to_gl(proj))
        prog["snap_res"].value = 100000.0
        self._set_uniform("use_tex", 0.0)
        prog["flat_shade"].value = 1.0
        self._set_uniform("emissive", 0.0)
        self._set_uniform("base_color", (1.0, 1.0, 1.0))
        prog["model"].write(gm.to_gl(gm.identity()))

        for vao, _vbo, (bx0, by0, bx1, by1) in self.depth_wall_chunks:
            if reach is not None and (bx1 < reach[0] or bx0 > reach[2]
                                      or by1 < reach[1] or by0 > reach[3]):
                continue
            vao.render(moderngl.TRIANGLES)
        for vao in (self.yard_roof_vao, self.yard_roof_wood_vao):
            if vao is not None:
                vao.render(moderngl.TRIANGLES)
        if self.ceil_vao is not None:
            self.ceil_vao.render(moderngl.TRIANGLES)
        self._depth_only = True
        try:
            self._draw_props(near_props, 0.0, door_swings=self._frame_door_swings)
            if monster is not None:
                self._draw_monster(monster, 0.0)
        finally:
            self._depth_only = False

        ctx.polygon_offset = (0.0, 0.0)
        return gm.to_gl(proj @ view)

    def _restore_main_pass_state(self, view, proj, qa_mode):
        ctx = self.ctx
        prog = self.prog
        self.fbo.use()
        ctx.viewport = (0, 0, self.low_w, self.low_h)
        prog["view"].write(gm.to_gl(view))
        prog["proj"].write(gm.to_gl(proj))
        prog["snap_res"].value = 100000.0 if qa_mode else self.snap_res
        prog["flat_shade"].value = 0.0
        self.shadow_atlas_tex.use(location=2)
        prog["shadow_atlas"].value = 2
        self.shadow_atlas_raw_sampler.use(location=3)
        prog["shadow_atlas_raw"].value = 3
        if getattr(self, "wall_grid_tex", None) is not None and "wall_grid" in prog:
            self.wall_grid_tex.use(location=4)
            prog["wall_grid"].value = 4
        prog["shadow_atlas_size"].value = (float(self.shadow_atlas_size[0]), float(self.shadow_atlas_size[1]))

    _OCCLUDER_FIELDS = ("x", "y", "z0", "facing", "hw", "hd", "height", "picked", "swing",
                        "break_askew", "is_broken", "broken", "cut", "pipe_open_neg",
                        "pipe_open_pos", "pipe_end_neg", "pipe_end_pos",
                        "latch_anim_t", "fall_t", "strain", "ghost_alpha",
                        "installed", "cut_stage", "install_t")
    _occluder_state = staticmethod(operator.attrgetter("kind", *_OCCLUDER_FIELDS))
    _MUTABLE_OCCLUDERS = frozenset((
        "door", "locker", "asylum_echo", "hatch", "hatch_lid", "hatch_wheel",
        "elevator", "elevator_cabin", "elevator_arrival", "shed_lock",
        "fence_gap", "fuse_box", "valve_panel",
    ))
    _static_occ_sig = None
    _static_occ_buckets = None
    _static_occ_ids = None
    _dynamic_occ = ()
    _static_region_sums = None

    def _occluder_buckets(self, props, centre=None, reach=None):
        mask = (1 << 64) - 1
        state = self._occluder_state
        swings = self._frame_door_swings
        sig = (len(props), sum(map(id, props)))
        if self._static_occ_sig != sig:
            static, dynamic, sb = [], [], {}
            for p in props:
                if p.interactable is None and p.kind not in self._MUTABLE_OCCLUDERS:
                    h = p._occ_hash
                    if h is None:
                        h = p._occ_hash = hash(state(p))
                    key = (int(p.x // 1.0), int(p.y // 1.0))
                    sb[key] = (sb.get(key, 0) + h) & mask
                    static.append(p)
                else:
                    dynamic.append(p)
            self._static_occ_sig = sig
            self._static_occ_buckets = sb
            self._static_region_sums = {}
            self._static_occ_ids = {id(p): p for p in static}
            self._dynamic_occ = dynamic
        buckets = {}
        if swings:
            for pid, swing in swings.items():
                p = self._static_occ_ids.get(pid)
                if p is None or not swing:
                    continue
                key = (int(p.x // 1.0), int(p.y // 1.0))
                h = p._occ_hash
                buckets[key] = (buckets.get(key, 0) - h + hash((h, swing))) & mask
        if centre is not None:
            cx, cy = centre
            reach2 = reach * reach
        for p in self._dynamic_occ:
            px, py = p.x, p.y
            if centre is not None and (px - cx) * (px - cx) + (py - cy) * (py - cy) > reach2:
                continue
            h = hash(state(p))
            if swings:
                swing = swings.get(id(p))
                if swing:
                    h = hash((h, swing))
            key = (int(px // 1.0), int(py // 1.0))
            buckets[key] = (buckets.get(key, 0) + h) & mask
        return self._static_occ_buckets, buckets

    @staticmethod
    def _occluder_reach(lights, eye):
        ex, ey = eye[0], eye[1]
        reach = 0.0
        for light in lights:
            if light.shape == SHAPE_OMNI and not light.dynamic:
                d = math.hypot(light.pos[0] - ex, light.pos[1] - ey) + (POINT_SHADOW_FAR + 1.0) * math.sqrt(2.0)
            elif light.shape == SHAPE_DIRECTIONAL:
                d = MOON_SHADOW_RECENTER_DIST + (MOON_SHADOW_HALF_EXTENT + 6.0 + 1.0) * math.sqrt(2.0)
            else:
                continue
            reach = max(reach, d)
        return reach + 0.01

    def _occluder_hash(self, buckets, x, y, radius):
        static, moving = buckets
        x0, x1 = int(math.floor(x - radius)), int(math.floor(x + radius))
        y0, y1 = int(math.floor(y - radius)), int(math.floor(y + radius))
        mask = (1 << 64) - 1
        cache = self._static_region_sums
        key = (x0, x1, y0, y1)
        total = cache.get(key)
        if total is None:
            total = 0
            if (x1 - x0 + 1) * (y1 - y0 + 1) <= len(static):
                for cx in range(x0, x1 + 1):
                    for cy in range(y0, y1 + 1):
                        total += static.get((cx, cy), 0)
            else:
                for (cx, cy), h in static.items():
                    if x0 <= cx <= x1 and y0 <= cy <= y1:
                        total += h
            total &= mask
            cache[key] = total
        for (cx, cy), h in moving.items():
            if x0 <= cx <= x1 and y0 <= cy <= y1:
                total += h
        return total & mask

    def _claim_slot(self, key, frame):
        slot = self._slot_of_key.get(key)
        if slot is not None and self._slot_key[slot] == key:
            self._slot_frame[slot] = frame
            return slot
        free = [i for i, k in enumerate(self._slot_key) if k is None]
        if free:
            slot = free[0]
        else:
            idle = [i for i in range(len(self._slot_key)) if self._slot_frame[i] < frame]
            if not idle:
                return None
            slot = min(idle, key=lambda i: self._slot_frame[i])
            self._slot_of_key.pop(self._slot_key[slot], None)
        self._slot_key[slot] = key
        self._slot_token[slot] = None
        self._slot_frame[slot] = frame
        self._slot_of_key[key] = slot
        return slot

    def _render_omni_faces(self, light, slot, props, monster):
        lx, ly, lz = light.pos
        near = [p for p in props
                if p is not light.owner and (p.x - lx) ** 2 + (p.y - ly) ** 2 < POINT_SHADOW_FAR ** 2]
        near_monster = None
        if light.dynamic and monster is not None and (monster.x - lx) ** 2 + (monster.y - ly) ** 2 < POINT_SHADOW_FAR ** 2:
            near_monster = monster
        proj = gm.perspective(math.radians(POINT_SHADOW_FOV_DEGREES), 1.0, POINT_SHADOW_NEAR, POINT_SHADOW_FAR)
        reach = (lx - POINT_SHADOW_FAR, ly - POINT_SHADOW_FAR, lx + POINT_SHADOW_FAR, ly + POINT_SHADOW_FAR)
        tile0 = omni_tile0(slot)
        for face, direction in enumerate(OMNI_FACE_DIRS):
            view = self._direction_to_view(light.pos, direction)
            face_props = self._props_in_cone(near, light.pos, direction, self._OMNI_CULL_HALF_ANGLE, POINT_SHADOW_FAR)
            self._tile_mvp[tile0 + face] = self._render_shadow_depth_pass(
                self._tile_rects[tile0 + face], view, proj, face_props, monster=near_monster, reach=reach)

    _FLASH_CULL_HALF_ANGLE = math.atan(math.sqrt(2.0) * math.tan(math.radians(FLASH_SHADOW_FOV_DEGREES) / 2.0))
    _OMNI_CULL_HALF_ANGLE = math.atan(math.sqrt(2.0) * math.tan(math.radians(POINT_SHADOW_FOV_DEGREES) / 2.0))
    _CULL_OVERHANG = 0.5
    main_pass_cone_cull = True
    last_drawn_prop_count = 0
    last_light_count = 0

    def _prop_xy(self, props):
        cache = self._prop_xy_cache
        if cache is not None and cache[0] is props and cache[1] == len(props):
            return cache[2], cache[3]
        for kept, xs_sub, ys_sub in self._prop_subsets:
            if kept is props:
                return xs_sub, ys_sub
        xs = np.array([p.x for p in props], dtype=np.float64)
        ys = np.array([p.y for p in props], dtype=np.float64)
        self._prop_xy_cache = (props, len(props), xs, ys)
        return xs, ys

    def _within_radius(self, props, x, y, radius, inclusive=False):
        if len(props) < self._NUMPY_CULL_MIN:
            r2 = radius * radius
            if inclusive:
                return [p for p in props if (p.x - x) ** 2 + (p.y - y) ** 2 <= r2]
            return [p for p in props if (p.x - x) ** 2 + (p.y - y) ** 2 < r2]
        xs, ys = self._prop_xy(props)
        dx = xs - x
        dy = ys - y
        d2 = dx * dx + dy * dy
        r2 = radius * radius
        hits = d2 <= r2 if inclusive else d2 < r2
        idx = np.flatnonzero(hits)
        kept = [props[i] for i in idx]
        subsets = self._prop_subsets
        subsets.append((kept, xs[idx], ys[idx]))
        if len(subsets) > 3:
            del subsets[0]
        return kept

    def _props_in_cone(self, props, origin, direction, half_angle, far):
        fx, fy, fz = origin
        dx, dy, dz = direction
        overhang = self._CULL_OVERHANG
        kept = []
        for p in self._within_radius(props, fx, fy, far):
            ox, oy = p.x - fx, p.y - fy
            oz = p.z0 + p.height * 0.5 - fz
            radius = getattr(p, "_cull_radius", None)
            if radius is None:
                radius = p._cull_radius = math.sqrt(
                    p.hw * p.hw + p.hd * p.hd + 0.25 * p.height * p.height) + overhang
            dist = math.sqrt(ox * ox + oy * oy + oz * oz)
            if dist > radius:
                along = (ox * dx + oy * dy + oz * dz) / dist
                if along < math.cos(min(math.pi, half_angle + math.asin(min(1.0, radius / dist)))):
                    continue
            kept.append(p)
        return kept

    def _render_flash_face(self, light, visible_props, monster, tile):
        fx, fy, fz = light.pos
        near = self._props_in_cone(visible_props, light.pos, light.dir, self._FLASH_CULL_HALF_ANGLE, FLASH_SHADOW_FAR)
        near_monster = None
        if monster is not None and (monster.x - fx) ** 2 + (monster.y - fy) ** 2 < FLASH_SHADOW_FAR ** 2:
            near_monster = monster
        view = self._direction_to_view(light.pos, light.dir)
        proj = gm.perspective(math.radians(FLASH_SHADOW_FOV_DEGREES), 1.0, FLASH_SHADOW_NEAR, FLASH_SHADOW_FAR)
        reach = (fx - FLASH_SHADOW_FAR, fy - FLASH_SHADOW_FAR, fx + FLASH_SHADOW_FAR, fy + FLASH_SHADOW_FAR)
        self._tile_mvp[tile] = self._render_shadow_depth_pass(
            self._tile_rects[tile], view, proj, near, monster=near_monster, reach=reach)

    def _render_moon_face(self, props, center):
        tx, ty, tz = MOON_TOWARD
        cx, cy = center
        d = MOON_SHADOW_ORIGIN_DIST
        view = self._direction_to_view((cx + tx * d, cy + ty * d, tz * d), (-tx, -ty, -tz))
        h = MOON_SHADOW_HALF_EXTENT
        proj = gm.orthographic(-h, h, -h, h, MOON_SHADOW_NEAR, MOON_SHADOW_FAR)
        margin = h + 6.0
        near = [p for p in props if (p.x - cx) ** 2 + (p.y - cy) ** 2 < margin ** 2]
        self._tile_mvp[TILE_MOON] = self._render_shadow_depth_pass(self._tile_rects[TILE_MOON], view, proj, near)

    def _update_shadows(self, lights, props, visible_props, monster, eye):
        self._frame_index += 1
        frame = self._frame_index
        rebuilt = []
        ready = []
        buckets = None
        budget = SHADOW_REBUILD_FACE_BUDGET
        for light in lights:
            if light.at_viewpoint:
                light.tile0, light.bias_k = -1, 0.0
                ready.append(light)
            elif light.shape == SHAPE_SPOT:
                self._render_flash_face(light, visible_props, monster, TILE_FLASH)
                light.tile0, light.bias_k = TILE_FLASH, FLASH_SHADOW_BIAS_K
                rebuilt.append(light.key)
                ready.append(light)
            elif light.shape == SHAPE_DIRECTIONAL:
                if buckets is None:
                    buckets = self._occluder_buckets(props, eye[:2], self._occluder_reach(lights, eye))
                ex, ey = eye[0], eye[1]
                if (self._moon_center is None or (ex - self._moon_center[0]) ** 2 + (ey - self._moon_center[1]) ** 2
                        > MOON_SHADOW_RECENTER_DIST ** 2):
                    self._moon_center = (ex, ey)
                token = (self._moon_center, self._occluder_hash(
                    buckets, self._moon_center[0], self._moon_center[1], MOON_SHADOW_HALF_EXTENT + 6.0))
                if token != self._moon_token:
                    self._render_moon_face(props, self._moon_center)
                    self._moon_token = token
                    rebuilt.append(light.key)
                light.tile0, light.bias_k = TILE_MOON, MOON_SHADOW_BIAS_K
                ready.append(light)
        for light in lights:
            if light.shape != SHAPE_OMNI:
                continue
            slot = self._claim_slot(light.key, frame)
            if slot is None:
                continue
            if light.dynamic:
                token = "dynamic"
                stale = True
            else:
                if buckets is None:
                    buckets = self._occluder_buckets(props, eye[:2], self._occluder_reach(lights, eye))
                token = (light.pos, self._occluder_hash(buckets, light.pos[0], light.pos[1], POINT_SHADOW_FAR))
                stale = self._slot_token[slot] != token
            if stale:
                never_built = self._slot_token[slot] is None
                if light.dynamic or never_built or budget >= 6:
                    self._render_omni_faces(light, slot, props, monster)
                    self._slot_token[slot] = token
                    rebuilt.append(light.key)
                    if not (light.dynamic or never_built):
                        budget -= 6
            light.tile0, light.bias_k = omni_tile0(slot), POINT_SHADOW_BIAS_K
            ready.append(light)
        self.last_shadow_rebuilds = rebuilt
        return ready

    def _upload_lights(self, lights):
        n = min(len(lights), self.max_lights)
        self.last_light_count = n
        pos = np.zeros((self.max_lights, 3), dtype="f4")
        direction = np.zeros((self.max_lights, 3), dtype="f4")
        color = np.zeros((self.max_lights, 3), dtype="f4")
        radius = np.zeros(self.max_lights, dtype="f4")
        falloff = np.ones(self.max_lights, dtype="f4")
        shape = np.zeros(self.max_lights, dtype="i4")
        cone = np.zeros((self.max_lights, 4), dtype="f4")
        tile0 = np.full(self.max_lights, -1, dtype="i4")
        bias_k = np.zeros(self.max_lights, dtype="f4")
        skip_bearer = np.zeros(self.max_lights, dtype="f4")
        emitter = np.zeros(self.max_lights, dtype="f4")
        for i, light in enumerate(lights[:n]):
            pos[i] = light.pos
            direction[i] = light.dir
            color[i] = light.color
            radius[i] = light.radius
            falloff[i] = light.falloff
            shape[i] = light.shape
            cone[i] = light.cone
            tile0[i] = light.tile0
            bias_k[i] = light.bias_k
            skip_bearer[i] = 1.0 if light.skip_bearer else 0.0
            emitter[i] = light.emitter
        prog = self.prog
        prog["light_count"].value = n
        prog["light_pos"].write(pos.tobytes())
        prog["light_dir"].write(direction.tobytes())
        prog["light_color"].write(color.tobytes())
        prog["light_radius"].write(radius.tobytes())
        prog["light_falloff"].write(falloff.tobytes())
        prog["light_shape"].write(shape.tobytes())
        prog["light_cone"].write(cone.tobytes())
        prog["light_tile0"].write(tile0.tobytes())
        prog["light_bias_k"].write(bias_k.tobytes())
        prog["light_skip_bearer"].write(skip_bearer.tobytes())
        prog["light_emitter"].write(emitter.tobytes())
        prog["tile_mvp"].write(b"".join(self._tile_mvp))

    def ensure_hud_texture(self, w, h):
        if self.hud_tex is None or self.hud_tex.size != (w, h):
            if self.hud_tex is not None:
                self.hud_tex.release()
            self.hud_tex = self.ctx.texture((w, h), 4)
            self.hud_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self.hud_tex.repeat_x = False
            self.hud_tex.repeat_y = False

    YARD_GROUND_UNITS_PER_REPEAT = 1.0

    def rebuild_floor(self, maze, hole=None):
        if self.floor_vao is not None:
            self.floor_vao.release()
            self.floor_vbo.release()
        data = build_floor_mesh(maze, hole)
        self.floor_vbo = self.ctx.buffer(data.tobytes())
        self.floor_vao = self.ctx.vertex_array(
            self.prog, [(self.floor_vbo, "3f 3f 2f 3f", "in_pos", "in_normal", "in_uv", "in_color")]
        )

    def build_level(self, maze, theme="upper", ceiling_hole=None, floor_hole=None, sky=False):
        self.floor_tex_density = (1.0 / self.YARD_GROUND_UNITS_PER_REPEAT if theme == "yard"
                                  else S.FLOOR_CEILING_TEX_DENSITY)
        self._invalidate_shadow_cache()
        if theme == "basement":
            self.tex_floor, self.tex_ceiling = self.tex_floor_basement, self.tex_ceiling_basement
        elif theme == "yard":
            self.tex_floor, self.tex_ceiling = self.tex_floor_yard, None
        else:
            self.tex_floor, self.tex_ceiling = self.tex_floor_upper, self.tex_ceiling_upper
        for vao, vbo, _tile_type in getattr(self, "wall_parts", []):
            vao.release()
            vbo.release()
        for vao, vbo, _bounds in getattr(self, "depth_wall_chunks", []):
            vao.release()
            vbo.release()
        if self.floor_vao is not None:
            self.floor_vao.release()
            self.floor_vbo.release()
        if self.ceil_vao is not None:
            self.ceil_vao.release()
            self.ceil_vbo.release()
            self.ceil_vao = None

        self._build_wall_grid(maze)
        walls = build_maze_walls_by_type(maze)
        self.depth_wall_chunks = []
        for data, bounds in build_depth_wall_chunks(maze, walls=walls):
            vbo = self.ctx.buffer(data.tobytes())
            vao = self.ctx.vertex_array(
                self.prog, [(vbo, "3f 3f 2f 3f", "in_pos", "in_normal", "in_uv", "in_color")]
            )
            self.depth_wall_chunks.append((vao, vbo, bounds))

        self.wall_parts = []
        for tile_type, data in walls.items():
            vbo = self.ctx.buffer(data.tobytes())
            vao = self.ctx.vertex_array(
                self.prog, [(vbo, "3f 3f 2f 3f", "in_pos", "in_normal", "in_uv", "in_color")]
            )
            self.wall_parts.append((vao, vbo, tile_type))

        per_cell = theme != "yard"
        floor_data = build_floor_mesh(maze, floor_hole,
                                      density=self.floor_tex_density, per_cell=per_cell)
        self.floor_vbo = self.ctx.buffer(floor_data.tobytes())
        self.floor_vao = self.ctx.vertex_array(
            self.prog, [(self.floor_vbo, "3f 3f 2f 3f", "in_pos", "in_normal", "in_uv", "in_color")]
        )

        if self.sky_dome_vao is not None:
            self.sky_dome_vao.release()
            self.sky_dome_vbo.release()
            self.sky_dome_vao = None
        for name in ("yard_roof_vao", "yard_roof_wood_vao"):
            vao = getattr(self, name, None)
            if vao is not None:
                vao.release()
                getattr(self, name.replace("_vao", "_vbo")).release()
                setattr(self, name, None)

        vao = getattr(self, "outdoor_ground_vao", None)
        if vao is not None:
            vao.release()
            self.outdoor_ground_vbo.release()
            self.outdoor_ground_vao = None
        vao = getattr(self, "shed_floor_vao", None)
        if vao is not None:
            vao.release()
            self.shed_floor_vbo.release()
            self.shed_floor_vao = None
        if theme == "yard":
            shed_data = build_shed_floor_mesh(maze, S.FLOOR_CEILING_TEX_DENSITY)
            if len(shed_data):
                self.shed_floor_vbo = self.ctx.buffer(shed_data.tobytes())
                self.shed_floor_vao = self.ctx.vertex_array(
                    self.prog, [(self.shed_floor_vbo, "3f 3f 2f 3f",
                                 "in_pos", "in_normal", "in_uv", "in_color")])
        ground_data = build_outdoor_ground_mesh(
            maze, 1.0 / self.YARD_GROUND_UNITS_PER_REPEAT)
        has_outdoors = bool(len(ground_data))
        self._outdoor_cells = None
        if has_outdoors:
            self._outdoor_cells = np.array(
                [(x, y) for y in range(maze.h) for x in range(maze.w)
                 if maze.grid[y][x] in _OUTDOOR_AIR_TILES], dtype=np.float64)
            self.outdoor_ground_vbo = self.ctx.buffer(ground_data.tobytes())
            self.outdoor_ground_vao = self.ctx.vertex_array(
                self.prog, [(self.outdoor_ground_vbo, "3f 3f 2f 3f",
                             "in_pos", "in_normal", "in_uv", "in_color")])

        if theme == "yard" or sky or has_outdoors:
            dome_data = build_sky_dome_mesh(0.0, 0.0)
            self.sky_dome_vbo = self.ctx.buffer(dome_data.tobytes())
            self.sky_dome_vao = self.ctx.vertex_array(
                self.prog, [(self.sky_dome_vbo, "3f 3f 2f 3f", "in_pos", "in_normal", "in_uv", "in_color")]
            )
        if theme == "yard":
            roof_metal, roof_wood = build_yard_roofs_mesh(maze)
            for data, name in ((roof_metal, "yard_roof"), (roof_wood, "yard_roof_wood")):
                if not len(data):
                    continue
                vbo = self.ctx.buffer(data.tobytes())
                setattr(self, name + "_vbo", vbo)
                setattr(self, name + "_vao", self.ctx.vertex_array(
                    self.prog, [(vbo, "3f 3f 2f 3f", "in_pos", "in_normal", "in_uv", "in_color")]))
        else:
            ceil_data = build_ceiling_mesh(maze, ceiling_hole,
                                           density=self.floor_tex_density, per_cell=True)
            self.ceil_vbo = self.ctx.buffer(ceil_data.tobytes())
            self.ceil_vao = self.ctx.vertex_array(
                self.prog, [(self.ceil_vbo, "3f 3f 2f 3f", "in_pos", "in_normal", "in_uv", "in_color")]
            )

    def _build_wall_grid(self, maze):
        cells = np.zeros((maze.h, maze.w), dtype="u1")
        for y, row in enumerate(maze.grid):
            for x, tile in enumerate(row):
                if tile == S.WALL_OUTDOOR:
                    continue
                if (tile != S.FLOOR and tile not in S.SEE_THROUGH_WALLS
                        and S.WALL_HEIGHTS.get(tile, S.WALL_HEIGHT) >= S.WALL_HEIGHT):
                    cells[y, x] = 255
        if getattr(self, "wall_grid_tex", None) is not None:
            self.wall_grid_tex.release()
        self.wall_grid_tex = self.ctx.texture((maze.w, maze.h), 1, cells.tobytes())
        self.wall_grid_tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self.wall_grid_tex.repeat_x = False
        self.wall_grid_tex.repeat_y = False
        if "wall_grid_size" in self.prog:
            self.prog["wall_grid_size"].value = (maze.w, maze.h)

    def _prop_color(self, prop, t):
        base = prop.base_color
        cached = prop.__dict__.get("_color_cache")
        if cached is not None and cached[0] is base:
            return cached[1]
        col = tuple(c / 255.0 for c in base)
        prop._color_cache = (base, col)
        return col

    def _moon_can_show(self, eye):
        cells = getattr(self, "_outdoor_cells", None)
        if cells is None or not len(cells):
            return True
        ex, ey = eye[0], eye[1]
        dx = np.maximum(np.maximum(cells[:, 0] - ex, ex - cells[:, 0] - 1.0), 0.0)
        dy = np.maximum(np.maximum(cells[:, 1] - ey, ey - cells[:, 1] - 1.0), 0.0)
        reach = S.OUTER_YARD_FOG_DIST + MOON_OUTDOOR_SHOW_PAD
        return float((dx * dx + dy * dy).min()) <= reach * reach

    def _set_outdoor(self, on):
        self._set_uniform("outdoor", 1.0 if on else 0.0)

    def _set_dado(self, on):
        self._set_uniform("dado", (S.DADO_AMOUNT, S.DADO_SKIRT_TOP, S.DADO_RAIL_Z,
                                   S.DADO_RAIL_HALF) if on else (0.0, 0.0, 0.0, 0.0))
        if on:
            self._set_uniform("dado_color", tuple(c / 255.0 for c in S.DADO_COLOR)
                              + (S.DADO_PANEL,))

    def _set_grime(self, on):
        self._set_uniform("grime", (S.GRIME_AMOUNT, S.GRIME_SCALE, S.GRIME_DEPTH)
                          if on else (0.0, 0.0, 0.0))

    def _set_uniform(self, name, value):
        if self._uniform_cache.get(name) != value:
            member = self._uniform_members.get(name)
            if member is None:
                member = self._uniform_members[name] = self.prog[name]
            member.value = value
            self._uniform_cache[name] = value

    def _apply_material(self, material, alpha=None):
        prog = self.prog
        tex = getattr(self, material.texture) if material.texture else None
        if tex is not None:
            self._set_uniform("use_tex", 1.0)
            tex.use(location=0)
        else:
            self._set_uniform("use_tex", 0.0)
        a = material.alpha if alpha is None else alpha
        self._set_uniform("u_alpha", a)
        prog["material_diffuse"].value = material.diffuse
        prog["material_specular"].value = material.specular
        prog["alpha_cutout"].value = 1.0 if material.cutout else 0.0
        if a < 1.0:
            self.ctx.enable(moderngl.BLEND)
            if material.specular > 0.0:
                self.ctx.blend_func = moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA
            else:
                self.ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
            self.fbo.depth_mask = False
        else:
            self.ctx.disable(moderngl.BLEND)
            self.fbo.depth_mask = True

    def _draw_translucent_walls(self):
        parts = [(vao, wall_material(tile_type)) for vao, _vbo, tile_type in self.wall_parts
                 if wall_material(tile_type).alpha < 1.0]
        if not parts:
            return
        prog = self.prog
        prog["model"].write(gm.to_gl(gm.identity()))
        self._set_uniform("base_color", (1.0, 1.0, 1.0))
        self._set_uniform("emissive", 0.0)
        self._set_uniform("tex_scale", (1.0, 1.0))
        self._set_uniform("tex0", 0)
        if "snap_object" in prog:
            prog["snap_object"].value = 0.0
        for vao, material in sorted(parts, key=lambda part: -part[1].alpha):
            self._apply_material(material)
            vao.render(moderngl.TRIANGLES)
        self._end_material()
        if "snap_object" in prog:
            prog["snap_object"].value = 1.0 if self.snap_props_whole else 0.0
        self._set_uniform("use_tex", 1.0)

    def _end_material(self):
        prog = self.prog
        self.ctx.disable(moderngl.BLEND)
        self.fbo.depth_mask = True
        self._set_uniform("u_alpha", 1.0)
        prog["material_diffuse"].value = 1.0
        prog["material_specular"].value = 0.0
        prog["alpha_cutout"].value = 0.0

    def _draw_box(self, model, color, emissive=0.0, texture=None, tex_scale=(1.0, 1.0), vao=None, alpha=1.0):
        prog = self.prog
        prog["model"].write(model if isinstance(model, bytes) else gm.to_gl(model))
        if self._depth_only:
            (vao or self.box_vao).render(moderngl.TRIANGLES)
            return
        self._set_uniform("base_color", color)
        self._set_uniform("emissive", emissive)
        self._set_uniform("u_alpha", alpha)
        if texture is not None:
            self._set_uniform("use_tex", 1.0)
            self._set_uniform("tex_scale", tex_scale)
            texture.use(location=0)
            self._set_uniform("tex0", 0)
        else:
            self._set_uniform("use_tex", 0.0)
        if alpha < 1.0:
            self.ctx.enable(moderngl.BLEND)
            self.ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
            self.fbo.depth_mask = False
        (vao or self.box_vao).render(moderngl.TRIANGLES)
        if alpha < 1.0:
            self.fbo.depth_mask = True
            self.ctx.disable(moderngl.BLEND)

    def _cull_by_distance(self, props, eye, cull_dist, fade_band=0.0):
        return self._within_radius(props, eye[0], eye[1], cull_dist, inclusive=True)

    def _hinge_swing_model(self, p, swing, hinge_depth=0.0):
        fx, fy = math.cos(p.facing), math.sin(p.facing)
        rx, ry = -math.sin(p.facing), math.cos(p.facing)
        hinge_x = p.x + fx * hinge_depth + rx * p.hw
        hinge_y = p.y + fy * hinge_depth + ry * p.hw
        vx0, vy0 = p.x - hinge_x, p.y - hinge_y
        angle = swing * DOOR_SWING_MAX_ANGLE
        ca, sa = math.cos(angle), math.sin(angle)
        sx = hinge_x + (vx0 * ca - vy0 * sa)
        sy = hinge_y + (vx0 * sa + vy0 * ca)
        return gm.trs_z(sx, sy, p.z0, p.facing + angle, p.hd * 2, p.hw * 2, p.height)

    _NUMPY_CULL_MIN = 48

    _PLAIN_SPECIAL_KINDS = frozenset({"hatch", "hatch_arrival", "locker", "elevator", "elevator_arrival"})

    def _plain_draw_plan(self, p):
        kind = p.kind
        plan = False
        special = (kind in self.variant_vaos or kind in _STATEFUL_VAO_KINDS or kind in _INSTALL_PARTS
                   or kind in self._PLAIN_SPECIAL_KINDS or kind in self.glass_vaos
                   or any(k == kind or k.startswith(kind + "_") for k in self.cutout_vaos)
                   or getattr(p, "cut", False) or getattr(p, "cut_stage", 0))
        if not special:
            tex = self.prop_textures.get(p.texture) if p.texture else None
            if tex is not None:
                tex_scale = p.__dict__.get("_tex_scale")
                if tex_scale is None:
                    n = max(1, int(max(p.hw, p.hd, p.height) * 2.5))
                    tex_scale = p._tex_scale = (n, n)
            else:
                tex_scale = (1.0, 1.0)
            plan = (self.prop_vaos.get(kind, self.box_vao), tex, tex_scale,
                    bool(getattr(p, "outdoor", False)), bool(getattr(p, "emissive", False)))
        p._plain_plan = plan
        return plan

    def _draw_props(self, props, t, door_swings=None, eye=None, cull_dist=None, fade_band=0.0):
        door_swings = door_swings or {}
        for p in props:
            if p.picked:
                continue
            alpha = p.ghost_alpha
            if alpha <= 0.0:
                continue
            if eye is not None and fade_band > 0.0 and cull_dist is not None:
                d = math.hypot(p.x - eye[0], p.y - eye[1])
                start = cull_dist - fade_band
                if d > start:
                    alpha *= max(0.0, min(1.0, (cull_dist - d) / fade_band))
                    if alpha <= 0.0:
                        continue
            plan = p.__dict__.get("_plain_plan")
            if plan is None:
                plan = self._plain_draw_plan(p)
            if plan:
                vao, tex, tex_scale, outdoor, emits = plan
                self._set_outdoor(outdoor)
                self._draw_box(prop_model_bytes(p), self._prop_color(p, t),
                               prop_emission(p, t) if emits else 0.0,
                               texture=tex, tex_scale=tex_scale, vao=vao, alpha=alpha)
                continue
            self._set_outdoor(getattr(p, "outdoor", False))
            swing = p.swing
            if p.kind == "shed_lock" and swing > 0.0:
                model = self._hinge_swing_model(p, swing, hinge_depth=p.hd)
            elif p.kind == "door" and (p.is_broken or p.strain > 0.002):
                model = door_break_model(p)
            else:
                model = prop_model_bytes(p)
            tex = self.prop_textures.get(p.texture) if p.texture else None
            if tex is not None:
                tex_scale = getattr(p, "_tex_scale", None)
                if tex_scale is None:
                    n = max(1, int(max(p.hw, p.hd, p.height) * 2.5))
                    tex_scale = p._tex_scale = (n, n)
            else:
                tex_scale = (1.0, 1.0)
            if p.kind in self.variant_vaos:
                vao = self.variant_vaos[p.kind][p.variant]
            elif p.kind not in _STATEFUL_VAO_KINDS:
                vao = self.prop_vaos.get(p.kind, self.box_vao)
            elif p.kind == "locker":
                vao = self.locker_vao
            elif p.kind == "door":
                vao = (self.broken_door_vao if p.is_broken and p.fall_t >= S.DOOR_FALL_LAND
                       else self.door_vao)
            elif p.kind == "shed_lock" and getattr(p, "installed", 0) > 0:
                vao = self.prop_vaos.get("shed_lock_%d" % max(0, 2 - p.installed), self.box_vao)
            elif p.kind == "fence_gap" and not p.cut and getattr(p, "cut_stage", 0) > 0:
                vao = self.prop_vaos.get("fence_gap_cut%d" % min(2, p.cut_stage), self.box_vao)
            elif p.kind == "pipes":
                vao = self.prop_vaos.get("pipes_%s_%s" % (p.pipe_end_neg, p.pipe_end_pos),
                                         self.box_vao)
            elif p.kind in BREAKABLE_LIGHT_KINDS and getattr(p, "broken", False):
                vao = self.prop_vaos.get(p.kind + "_broken", self.box_vao)
            elif p.kind == "fence_gap" and getattr(p, "cut", False):
                vao = self.prop_vaos.get("fence_gap_open", self.box_vao)
            else:
                vao = self.prop_vaos.get(p.kind, self.box_vao)
            self._draw_box(model, self._prop_color(p, t), prop_emission(p, t),
                            texture=tex, tex_scale=tex_scale, vao=vao, alpha=alpha)
            if p.kind in ("hatch", "hatch_arrival"):
                open_t = p.hatch_open_t if p.kind == "hatch" else 1.0
                angle = HATCH_LID_ANGLE * max(0.0, min(1.0, open_t))
                if "snap_anchor" in self.prog:
                    self.prog["snap_anchor"].value = (p.x, p.y, 0.0, 1.0)
                face_z = hatch_lid_face_z(p.kind)
                self._draw_box(hatch_lid_model(p, angle, face_z),
                               self._prop_color(p, t), 0.0, texture=tex, tex_scale=tex_scale,
                               vao=self.prop_vaos["hatch_lid"], alpha=alpha)
                spin = getattr(p, "wheel_spin", 0.0)
                self._draw_box(hatch_wheel_model(p, angle, face_z, spin),
                               self._prop_color(p, t), 0.0, texture=tex, tex_scale=tex_scale,
                               vao=self.prop_vaos["hatch_wheel"], alpha=alpha)
                if "snap_anchor" in self.prog:
                    self.prog["snap_anchor"].value = (0.0, 0.0, 0.0, 0.0)
            cfg = _INSTALL_PARTS.get(p.kind)
            if cfg is not None and not self._depth_only:
                work = getattr(p, "install_t", 0.0)
                part = cfg["part"]
                lx, ly0, lz0 = cfg["at"]
                dx, dy, dz = cfg["step"]
                part_tex = self.prop_textures.get(PROP_DEFS[part].get("texture"))
                part_vao = self.prop_vaos.get(part, self.box_vao)

                def seat_model(i, out=0.0, turn=None):
                    return part_model(p, lx + dx * i + out, ly0 + dy * i, lz0 + dz * i,
                                      part, yaw=cfg["yaw"],
                                      roll=cfg.get("roll", 0.0)
                                      + (cfg.get("turn", 0.0) if turn is None else turn),
                                      scale=cfg["scale"])

                if cfg["mode"] == "fit" and cfg.get("keep", True):
                    for i in range(min(3, getattr(p, "installed", 0))):
                        self._draw_box(seat_model(i), (1.0, 1.0, 1.0), 0.0,
                                       texture=part_tex, vao=part_vao, alpha=alpha)
                if work > 0.0:
                    if cfg["mode"] == "fit":
                        fit = cfg.get("fit_frac", 0.7)
                        ease = min(1.0, work / fit)
                        ease = ease * ease * (3.0 - 2.0 * ease)
                        turn = cfg.get("turn", 0.0) * max(0.0, (work - fit) / (1.0 - fit))
                        seat = min(3, getattr(p, "installed", 0))
                        part_m = seat_model(seat, out=(1.0 - ease) * 0.14, turn=turn)
                    else:
                        part_m = part_model(p, lx, ly0, lz0, part,
                                            yaw=cfg["yaw"], roll=math.pi * 0.5,
                                            scale=cfg["scale"])
                        tx, ty, tz = cfg["tool_at"]
                        stroke = math.sin(t * 5.2)
                        lift = max(0.0, math.cos(t * 5.2)) * 0.008
                        self._draw_box(
                            part_model(p, tx + lift, ty, tz + stroke * cfg["tool_travel"],
                                       cfg["tool"], yaw=0.0, roll=stroke * 0.12),
                            (1.0, 1.0, 1.0), 0.0,
                            texture=self.prop_textures.get(PROP_DEFS[cfg["tool"]].get("texture")),
                            vao=self.prop_vaos.get(cfg["tool"], self.box_vao), alpha=alpha)
                    self._draw_box(part_m, (1.0, 1.0, 1.0), 0.0,
                                   texture=part_tex, vao=part_vao, alpha=alpha)
            if getattr(p, "cut", False):
                suffix = "_open"
            elif getattr(p, "cut_stage", 0) > 0:
                suffix = "_cut%d" % min(2, p.cut_stage)
            else:
                suffix = ""
            cutout_vao = self.cutout_vaos.get(p.kind + suffix)
            if cutout_vao is not None and not self._depth_only:
                prog = self.prog
                prog["model"].write(model if isinstance(model, bytes) else gm.to_gl(model))
                self._set_uniform("base_color", (1.0, 1.0, 1.0))
                self._set_uniform("emissive", 0.0)
                self._apply_material(CHAINLINK_MATERIAL, alpha=alpha)
                self._set_uniform("tex_scale", (1.0, 1.0))
                cutout_vao.render(moderngl.TRIANGLES)
                self._end_material()
            glass_vao = self.glass_vaos.get(p.kind)
            if glass_vao is not None and not self._depth_only:
                prog = self.prog
                prog["model"].write(model if isinstance(model, bytes) else gm.to_gl(model))
                self._set_uniform("base_color", (1.0, 1.0, 1.0))
                self._set_uniform("emissive", 0.0)
                self._apply_material(GLASS_MATERIAL, alpha=GLASS_MATERIAL.alpha * alpha)
                glass_vao.render(moderngl.TRIANGLES)
                self._end_material()
            if p.kind == "locker":
                leaf_swing = door_swings.get(id(p), 0.0)
                leaf_model = self._hinge_swing_model(p, leaf_swing, hinge_depth=p.hd) if leaf_swing > 0.0 else model
                self._draw_box(leaf_model, self._prop_color(p, t), 0.0, texture=tex, tex_scale=tex_scale,
                                vao=self.locker_door_vao, alpha=alpha)
            if p.kind in ("elevator", "elevator_arrival") and not self._depth_only:
                self._draw_elevator_extras(p, eye)
            if p.kind == "door" and not getattr(p, "is_broken", False):
                self._draw_box(self._door_latch_model(p, eye), (0.16, 0.16, 0.18), 0.0,
                                vao=self.latch_vao, alpha=alpha)
        self._set_outdoor(False)

    def _door_latch_model(self, door, eye):
        fx, fy = math.cos(door.facing), math.sin(door.facing)
        rx, ry = -fy, fx
        bolt_hw, bolt_hh, bolt_hd = 0.05, 0.035, 0.03
        near_face = door.hd + 0.005
        depth_off = near_face + bolt_hd
        rest = door.hw - bolt_hw * 2.0
        latched = door.hw + bolt_hw / 3.0
        travel = latched - rest
        right_off = -(rest + travel * door.latch_anim_t)
        cz = door.z0 + door.height * 0.72
        side = 1.0
        if eye is not None:
            side = 1.0 if (fx * (eye[0] - door.x) + fy * (eye[1] - door.y)) >= 0.0 else -1.0
        cx = door.x + fx * depth_off * side + rx * right_off
        cy = door.y + fy * depth_off * side + ry * right_off
        return gm.trs_z(cx, cy, cz - bolt_hh, door.facing, bolt_hd * 2, bolt_hw * 2, bolt_hh * 2)

    def _draw_vision_cone(self, monster):
        info = getattr(monster, "last_vision_debug", None)
        if info is None:
            return
        mx, my, facing, acute_range, peripheral_range, acute_rad, peripheral_rad, patrol_idle = info
        segs = self.DEBUG_CONE_SEGMENTS
        z = 0.03
        verts = []

        def add_wedge(half_angle, radius, color):
            start = facing - half_angle
            step = (2 * half_angle) / segs
            for i in range(segs):
                a0 = start + step * i
                a1 = start + step * (i + 1)
                p0 = (mx, my, z)
                p1 = (mx + math.cos(a0) * radius, my + math.sin(a0) * radius, z)
                p2 = (mx + math.cos(a1) * radius, my + math.sin(a1) * radius, z)
                for p in (p0, p1, p2):
                    verts.extend([p[0], p[1], p[2], 0, 0, 1, 0, 0, color[0], color[1], color[2]])

        peripheral_color = (0.55, 0.52, 0.4) if patrol_idle else (0.9, 0.75, 0.15)
        add_wedge(peripheral_rad, peripheral_range, peripheral_color)
        add_wedge(acute_rad, acute_range, (0.95, 0.2, 0.15))

        data = np.array(verts, dtype="f4")
        self.debug_cone_vbo.write(data.tobytes())

        prog = self.prog
        prog["model"].write(gm.to_gl(np.eye(4)))
        prog["flat_shade"].value = 1.0
        self._set_uniform("use_tex", 0.0)
        self._set_uniform("emissive", 0.0)
        self._set_uniform("base_color", (1.0, 1.0, 1.0))
        self._set_uniform("u_alpha", 0.55)
        prog["snap_res"].value = 100000.0
        self.ctx.enable(moderngl.BLEND)
        self.ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
        self.fbo.depth_mask = False
        vert_count = len(verts) // 11
        self.debug_cone_vao.render(moderngl.TRIANGLES, vertices=vert_count)
        self.fbo.depth_mask = True
        self.ctx.disable(moderngl.BLEND)
        self._set_uniform("u_alpha", 1.0)
        prog["flat_shade"].value = 0.0
        prog["snap_res"].value = self.snap_res

    def project_to_screen(self, world_xyz, eye, yaw, pitch, roll=0.0, fov_degrees=FOV_DEGREES):
        view = gm.view_matrix(eye, yaw, pitch, roll)
        proj = gm.perspective(math.radians(fov_degrees), self.low_w / self.low_h, 0.03, 130.0)
        wx, wy, wz = world_xyz
        vx, vy, vz, vw = view @ np.array([wx, wy, wz, 1.0])
        cx, cy, cz, cw = proj @ np.array([vx, vy, vz, vw])
        if cw <= 1e-4:
            return None
        ndc_x, ndc_y = cx / cw, cy / cw
        sx = (ndc_x * 0.5 + 0.5) * S.SCREEN_W
        sy = (1.0 - (ndc_y * 0.5 + 0.5)) * S.SCREEN_H
        return sx, sy

    @staticmethod
    def compute_check_frac(monster):
        checking_timer = getattr(monster, "checking_timer", 0.0)
        if checking_timer <= 0.0:
            return 0.0
        total = getattr(monster, "checking_timer_total", S.MONSTER_LOCKER_CHECK_SECONDS)
        elapsed = total - checking_timer
        return min(1.0, elapsed / max(0.35, total * 0.3))

    MONSTER_MODEL_REACH = 1.2

    def _monster_fully_fogged(self, monster, eye, fog_dist):
        if monster is None or self.sky_dome_vao is not None:
            return False
        dx, dy = monster.x - eye[0], monster.y - eye[1]
        return math.sqrt(dx * dx + dy * dy) - self.MONSTER_MODEL_REACH > fog_dist

    def _draw_monster(self, monster, dread, check_frac=0.0):
        alert = monster.alert_level
        amp = getattr(monster, "walk_amp", 1.0)
        phase = monster.walk_phase
        facing = monster.facing
        limb_amp = amp * (1.0 - check_frac)

        seed = (id(monster) % 997) * 0.0131
        twitch = math.sin(phase * 5.7 + seed) * (0.05 + alert * 0.16) * (0.35 + 0.65 * amp)
        idle_sway = math.sin(phase * 1.3 + seed) * 0.06 * (1.0 - amp)

        body = (0.045 + alert * 0.02, 0.035 + alert * 0.01, 0.045)
        dark = (0.016, 0.012, 0.016)

        base = gm.translate(monster.x, monster.y, 0) @ gm.rotate_z(facing)

        def part(frame, sx, sy, sz, color=body):
            self._draw_box(frame @ gm.scale(sx, sy, sz), color)

        hip_z, hip_y = 0.49, 0.16
        thigh_len, shin_len = 0.29, 0.25
        stance_depth = hip_z - 0.045
        lift_h = 0.03 + 0.03 * alert
        stride_reach = math.pi / (2.0 * 5.5)

        def solve_leg_ik(target_x, target_z):
            d = math.hypot(target_x, target_z)
            d = min(d, thigh_len + shin_len - 1e-4)
            d = max(d, abs(thigh_len - shin_len) + 1e-4)
            base_angle = math.atan2(-target_x, -target_z)
            cos_knee = (thigh_len ** 2 + shin_len ** 2 - d * d) / (2 * thigh_len * shin_len)
            knee_a = math.pi - math.acos(max(-1.0, min(1.0, cos_knee)))
            cos_hip = (thigh_len ** 2 + d * d - shin_len ** 2) / (2 * thigh_len * d)
            hip_offset = math.acos(max(-1.0, min(1.0, cos_hip)))
            return base_angle - hip_offset, knee_a

        part(base @ gm.translate(0.0, 0.0, hip_z), 0.22, 0.34, 0.10)

        def leg(side, ph_off, asym):
            hip_frame = base @ gm.translate(0.0, side * hip_y, hip_z)
            cyc = (phase + ph_off) / math.tau
            s = cyc - math.floor(cyc)
            reach = stride_reach * asym * limb_amp
            if s < 0.5:
                u = s / 0.5
                rel_x = reach * (1.0 - 2.0 * u)
                lift = 0.0
            else:
                u = (s - 0.5) / 0.5
                ease = u * u * (3.0 - 2.0 * u)
                rel_x = reach * (2.0 * ease - 1.0)
                lift = math.sin(math.pi * u) * lift_h * limb_amp
            thigh_a, knee_a = solve_leg_ik(rel_x, lift - stance_depth)
            thigh_frame = hip_frame @ gm.rotate_y(math.pi + thigh_a)
            part(thigh_frame, 0.085, 0.085, thigh_len)
            knee_frame = thigh_frame @ gm.translate(0.0, 0.0, thigh_len)
            shin_frame = knee_frame @ gm.rotate_y(knee_a)
            part(shin_frame, 0.075, 0.075, shin_len)
            foot_frame = shin_frame @ gm.translate(0.0, 0.0, shin_len)
            part(foot_frame @ gm.rotate_y(2.3), 0.07, 0.09, 0.075, color=dark)

        leg(1, 0.0, 1.0)
        leg(-1, math.pi + 0.15, 1.10)

        base_lean = 0.22 + 0.18 * alert + idle_sway
        lean = base_lean + (0.22 - base_lean) * check_frac
        lean_in = 0.04 * check_frac
        spine_root = base @ gm.translate(lean_in, 0.0, hip_z + 0.04) @ gm.rotate_y(lean)

        lower_len = 0.20
        part(spine_root, 0.20, 0.30, lower_len)
        base_upper_bend = -0.10 + twitch * 0.25
        upper_bend = base_upper_bend * (1.0 - check_frac)
        upper_root = spine_root @ gm.translate(0.0, 0.0, lower_len) @ gm.rotate_y(upper_bend)
        upper_len = 0.26
        part(upper_root, 0.24, 0.36, upper_len)

        for i, spike in enumerate((0.055, 0.05, 0.04)):
            ridge_z = lower_len * (0.35 + i * 0.32)
            part(spine_root @ gm.translate(-0.15, 0.0, ridge_z) @ gm.rotate_y(-0.75),
                 0.028, 0.028, spike, color=dark)

        neck_root = upper_root @ gm.translate(0.0, 0.0, upper_len)
        head_tilt = 0.20 + twitch + getattr(monster, "head_yaw", 0.0)
        base_head_pitch = 0.20
        head_pitch = base_head_pitch + (0.50 - base_head_pitch) * check_frac
        head_frame = neck_root @ gm.rotate_z(head_tilt) @ gm.rotate_y(head_pitch)
        head_sz = 0.15
        part(head_frame, 0.16, 0.16, head_sz)
        self.last_monster_head = tuple(float(c) for c in (head_frame @ np.array([0.0, 0.0, head_sz * 0.5, 1.0]))[:3])

        shoulder_z = upper_len * 0.86
        shoulder_y = 0.24
        arm_swing = (0.22 + 0.14 * alert) * 0.65 * limb_amp
        elbow_swing = (0.22 + 0.10 * alert) * 0.65 * limb_amp
        arm_bend = 0.14
        check_upper_a, check_splay, check_elbow_a = -1.10, 0.42, 0.0

        def arm(side, ph_off, length_mult, splay):
            shoulder_frame = upper_root @ gm.translate(0.02, side * shoulder_y, shoulder_z)
            swing = math.sin(phase + ph_off)
            base_upper_a = arm_bend + arm_swing * swing
            upper_a = base_upper_a + (check_upper_a - base_upper_a) * check_frac
            base_splay = side * splay
            total_splay = base_splay + (side * check_splay - base_splay) * check_frac
            upper_frame = shoulder_frame @ gm.rotate_z(total_splay) @ gm.rotate_y(math.pi + upper_a)
            ulen = 0.30 * length_mult
            part(upper_frame, 0.09, 0.09, ulen)
            elbow_frame = upper_frame @ gm.translate(0.0, 0.0, ulen)
            base_elbow_a = 0.14 + elbow_swing * (0.5 + 0.5 * swing)
            elbow_a = base_elbow_a + (check_elbow_a - base_elbow_a) * check_frac
            fore_frame = elbow_frame @ gm.rotate_y(elbow_a)
            flen = 0.32 * length_mult
            part(fore_frame, 0.075, 0.075, flen)
            hand_frame = fore_frame @ gm.translate(0.0, 0.0, flen)
            min_hand_z = 0.20
            if hand_frame[2, 3] < min_hand_z:
                hand_frame = gm.translate(0.0, 0.0, min_hand_z - hand_frame[2, 3]) @ hand_frame
            part(hand_frame, 0.085, 0.10, 0.07, color=dark)
            for cang in (-0.30, 0.0, 0.30):
                part(hand_frame @ gm.translate(0.0, 0.0, 0.04) @ gm.rotate_z(cang) @ gm.rotate_y(0.4),
                     0.016, 0.016, 0.12 * length_mult, color=dark)

        arm(1, math.pi, 1.0, 0.06)
        arm(-1, 0.10, 1.28, -0.16)

        pulse = (0.6 + 0.4 * math.sin(phase * 4.0)) if alert > 0.5 else 0.85
        eye_color = (1.0 * pulse, 0.05, 0.05)
        for side, drop, sz in ((1, 0.0, 0.05), (-1, 0.02, 0.038)):
            model = head_frame @ gm.translate(0.11, side * 0.065, 0.06 - drop) @ gm.scale(sz, sz, sz)
            self._draw_box(model, eye_color, emissive=1.0)

    def _draw_hallu_eyes(self, eye, t):
        for h in self.hallu_eyes:
            fade = h.get("fade", 1.0)
            if fade <= 0.0:
                continue
            ex, ey, ez = h["pos"]
            age = h.get("age", 0.0)
            if age % S.HALLUCINATION_EYES_BLINK_EVERY < S.HALLUCINATION_EYES_BLINK_LEN:
                continue
            sway = S.HALLUCINATION_EYES_SWAY
            ex += math.sin(age * 0.9) * sway
            ey += math.cos(age * 0.7) * sway
            ez += math.sin(age * 1.3) * sway * 0.5
            facing = math.atan2(eye[1] - ey, eye[0] - ex)
            base = gm.translate(ex, ey, ez) @ gm.rotate_z(facing)
            pulse = 0.72 + 0.28 * math.sin(t * 3.1 + ex)
            col = (1.0 * pulse, 0.05, 0.05)
            k = S.HALLUCINATION_EYES_SCALE
            for side, drop, sz in ((1, 0.0, 0.05), (-1, 0.02, 0.038)):
                seat = base @ gm.translate(0.0, side * 0.065 * k, -drop * k)
                self._draw_box(seat @ gm.scale(sz * k * 2.4, sz * k * 2.4, sz * k * 2.4),
                                (col[0] * 0.30, 0.02, 0.02), emissive=1.0, alpha=fade * 0.30)
                self._draw_box(seat @ gm.scale(sz * k, sz * k, sz * k), col,
                                emissive=1.0, alpha=fade)
        self._set_uniform("u_alpha", 1.0)

    def _basis_model(self, pos, right, up, fwd, sx, sy, sz):
        return np.array((
            (right[0] * sx, up[0] * sy, fwd[0] * sz, pos[0]),
            (right[1] * sx, up[1] * sy, fwd[1] * sz, pos[1]),
            (right[2] * sx, up[2] * sy, fwd[2] * sz, pos[2]),
            (0.0, 0.0, 0.0, 1.0),
        ))

    HELD_ITEM_CLEARANCE = 0.075

    def _held_blocked(self, maze, props, x, y, z, r):
        if maze.circle_hits_wall(x, y, r):
            return True
        for p in props:
            px, py = p.collide_x, p.collide_y
            if (x - px) ** 2 + (y - py) ** 2 > (r + p.collide_hw + p.collide_hd) ** 2:
                continue
            if not p.solid or getattr(p, "ignore_player", False):
                continue
            z0 = getattr(p, "z0", 0.0)
            if not (z0 - r <= z <= z0 + getattr(p, "height", 0.0) + r):
                continue
            fx, fy = math.cos(p.collide_facing), math.sin(p.collide_facing)
            rx, ry = -fy, fx
            dx, dy = x - px, y - py
            local_f = dx * fx + dy * fy
            local_r = dx * rx + dy * ry
            closest_r = max(-p.collide_hw, min(p.collide_hw, local_r))
            closest_f = max(-p.collide_hd, min(p.collide_hd, local_f))
            dr, df = local_r - closest_r, local_f - closest_f
            if dr * dr + df * df < r * r:
                return True
        return False

    @staticmethod
    def _held_candidates(props, eye, target, r):
        return props_near_segment(props, eye[0], eye[1], target[0], target[1], r)

    def _clear_held_item_pos(self, maze, props, player, eye, target):
        r = self.HELD_ITEM_CLEARANCE
        props = self._held_candidates(props, eye, target, r)
        if not self._held_blocked(maze, props, target[0], target[1], target[2], r):
            return target
        lo, hi = 0.0, 1.0
        for _ in range(6):
            mid = (lo + hi) * 0.5
            mx = eye[0] + (target[0] - eye[0]) * mid
            my = eye[1] + (target[1] - eye[1]) * mid
            mz = eye[2] + (target[2] - eye[2]) * mid
            if self._held_blocked(maze, props, mx, my, mz, r):
                hi = mid
            else:
                lo = mid
        return (
            eye[0] + (target[0] - eye[0]) * lo,
            eye[1] + (target[1] - eye[1]) * lo,
            eye[2] + (target[2] - eye[2]) * lo,
        )

    def _held_item_transform(self, maze, props, player, eye, yaw, pitch, roll=0.0, clear=True):
        item = getattr(player, "active_held_item", None)
        ease = getattr(player, "equip_t", 0.0)
        if not item or ease <= 0.0:
            return None
        defs = HELD_ITEM_DEFS.get(item)
        if defs is None:
            return None
        cy, sy = math.cos(yaw), math.sin(yaw)
        cp, sp = math.cos(pitch), math.sin(pitch)
        fwd = (cy * cp, sy * cp, sp)
        right = (-sy, cy, 0.0)
        up = (-sp * cy, -sp * sy, cp)
        if roll:
            cr, sr = math.cos(roll), math.sin(roll)
            right, up = (
                (right[0] * cr + up[0] * sr, right[1] * cr + up[1] * sr, right[2] * cr + up[2] * sr),
                (-right[0] * sr + up[0] * cr, -right[1] * sr + up[1] * cr, -right[2] * sr + up[2] * cr),
            )
        eased = ease * ease * (3.0 - 2.0 * ease)
        bx, by, bz = defs["hand_offset"]
        raise_t = getattr(player, "map_raise_t", 0.0) if "raised_offset" in defs else 0.0
        if raise_t > 0.0:
            k = raise_t * raise_t * (3.0 - 2.0 * raise_t)
            rx, ry, rz = defs["raised_offset"]
            bx, by, bz = bx + (rx - bx) * k, by + (ry - by) * k, bz + (rz - bz) * k
            defs = dict(defs, view_pitch=defs.get("view_pitch", 0.0)
                        + (defs["raised_pitch"] - defs.get("view_pitch", 0.0)) * k)
        stow_x, stow_y, stow_z = bx, by - 0.34, bz - 0.20
        ox = stow_x + (bx - stow_x) * eased
        oy = stow_y + (by - stow_y) * eased
        oz = stow_z + (bz - stow_z) * eased
        bob = getattr(player, "bob_phase", 0.0)
        ox += S.HELD_ITEM_SWAY_AMOUNT * math.sin(bob)
        oy += S.HELD_ITEM_SWAY_AMOUNT * 0.6 * math.sin(bob * 2.0)
        ex, ey, ez = eye
        pos = (
            ex + right[0] * ox + up[0] * oy + fwd[0] * oz,
            ey + right[1] * ox + up[1] * oy + fwd[1] * oz,
            ez + right[2] * ox + up[2] * oy + fwd[2] * oz,
        )
        if clear and defs.get("clearance", True):
            pos = self._clear_held_item_pos(maze, props, player, eye, pos)
        return item, defs, pos, right, up, fwd, eased, eye

    def _draw_map_pencil(self, pos, right, up, fwd, fade):
        vao = self.prop_vaos.get("pencil")
        if vao is None:
            return
        left, spares, hold, cu, cv = self.map_pencil
        hold = max(0.0, min(1.0, hold))
        ease = hold * hold * (3.0 - 2.0 * hold)

        def place(tip_local, dir_local, length):
            d = [dir_local[0], dir_local[1], dir_local[2]]
            dl = math.sqrt(sum(c * c for c in d)) or 1.0
            d = [c / dl for c in d]
            world_d = tuple(right[i] * d[0] + up[i] * d[1] + fwd[i] * d[2] for i in range(3))
            ref = fwd if abs(d[2]) < 0.9 else right
            ax = (world_d[1] * ref[2] - world_d[2] * ref[1],
                  world_d[2] * ref[0] - world_d[0] * ref[2],
                  world_d[0] * ref[1] - world_d[1] * ref[0])
            al = math.sqrt(sum(c * c for c in ax)) or 1.0
            ax = tuple(c / al for c in ax)
            az = (ax[1] * world_d[2] - ax[2] * world_d[1],
                  ax[2] * world_d[0] - ax[0] * world_d[2],
                  ax[0] * world_d[1] - ax[1] * world_d[0])
            tip = tuple(pos[i] + right[i] * tip_local[0] + up[i] * tip_local[1]
                        + fwd[i] * tip_local[2] for i in range(3))
            centre = tuple(tip[i] - world_d[i] * length * 0.5 for i in range(3))
            return self._basis_model(centre, ax, world_d, az,
                                     MAP_PENCIL_THICK, length, MAP_PENCIL_THICK)

        length = MAP_PENCIL_LEN * (MAP_PENCIL_MIN + (1.0 - MAP_PENCIL_MIN) * max(0.0, min(1.0, left)))
        rest_tip = (MAP_PENCIL_CLIP[0] - length * 0.5, MAP_PENCIL_CLIP[1], MAP_PENCIL_CLIP[2])
        rest_dir = (-1.0, 0.0, 0.0)
        hx, hy = MAP_PAPER_HALF
        draw_tip = ((cu - 0.5) * 2.0 * hx, (0.5 - cv) * 2.0 * hy, -0.004)
        tip = tuple(rest_tip[i] + (draw_tip[i] - rest_tip[i]) * ease for i in range(3))
        d = tuple(rest_dir[i] + (MAP_PENCIL_HOLD[i] - rest_dir[i]) * ease for i in range(3))
        d = map_pencil_grip(d, tip[1], length)
        if left > 0.0:
            self._draw_box(place(tip, d, length), (1.0, 1.0, 1.0), 0.0, vao=vao, alpha=fade)

        for i in range(min(3, int(spares))):
            dy = MAP_PENCIL_SPARE_DY * (i + 1)
            spare_tip = (MAP_PENCIL_CLIP[0] - MAP_PENCIL_LEN * 0.5,
                         MAP_PENCIL_CLIP[1] - dy, MAP_PENCIL_CLIP[2])
            self._draw_box(place(spare_tip, rest_dir, MAP_PENCIL_LEN),
                           (1.0, 1.0, 1.0), 0.0, vao=vao, alpha=fade)

    def _draw_held_item(self, player, held, t):
        if held is None:
            return
        item, defs, pos, right, up, fwd, eased, eye = held
        if eased <= 0.001:
            return
        fade = min(1.0, eased * 4.0)
        view_yaw = defs.get("view_yaw", 0.0)
        if view_yaw:
            ca, sa = math.cos(view_yaw), math.sin(view_yaw)
            right0, fwd0 = right, fwd
            right = tuple(right0[i] * ca + fwd0[i] * sa for i in range(3))
            fwd = tuple(-right0[i] * sa + fwd0[i] * ca for i in range(3))
        view_pitch = defs.get("view_pitch", 0.0)
        if view_pitch:
            ca, sa = math.cos(view_pitch), math.sin(view_pitch)
            up0, fwd0 = up, fwd
            up = tuple(up0[i] * ca + fwd0[i] * sa for i in range(3))
            fwd = tuple(fwd0[i] * ca - up0[i] * sa for i in range(3))
        model = self._basis_model(pos, right, up, fwd, 1.0, 1.0, 1.0)
        vao = self.prop_vaos.get(defs["mesh"])
        squash = defs.get("depth_squash", 0.0)
        fill = getattr(self, "_held_fill", (0.0, 0.0, 0.0)) if item in ("flashlight", "lighter") else (0.0, 0.0, 0.0)
        if "held_fill" in self.prog:
            self.prog["held_fill"].value = fill
        if squash and "depth_squash" in self.prog:
            self.prog["depth_squash"].value = squash
        bounce = defs.get("bounce_light", 0.0)
        if bounce and "held_bounce" in self.prog:
            self.prog["held_bounce"].value = bounce
        if defs.get("mesh") is not None:
            self._draw_box(model, defs.get("body_color", (0.6, 0.6, 0.6)), 0.0, vao=vao, alpha=fade)
        if item == "map" and self.map_tex is not None:
            self._draw_box(model, (1.0, 1.0, 1.0), 0.0, texture=self.map_tex, vao=self.map_paper_vao, alpha=fade)
            self._draw_map_pencil(pos, right, up, fwd, fade)
            turn = max(0.0, min(1.0, getattr(self, "map_turn", 0.0)))
            tuck = max(0.0, min(1.0, (turn - MAP_TUCK_START) / (1.0 - MAP_TUCK_START)))
            tuck = tuck * tuck * (3.0 - 2.0 * tuck)
            rolled = getattr(self, "map_roll_pages", 0) + tuck
            if rolled > 0.001:
                r = min(MAP_ROLL_MAX, MAP_ROLL_BASE + MAP_ROLL_PER_SHEET * rolled)
                cpos = tuple(pos[i] + up[i] * (MAP_ROLL_BOTTOM + r) for i in range(3))
                roll_model = self._basis_model(cpos, right, up, fwd,
                                               MAP_PAPER_HALF[0], r, r)
                self._draw_box(roll_model, MAP_PAPER_BACK, 0.0,
                               vao=self.prop_vaos.get("map_roll"), alpha=fade)
            if turn > 0.0 and self.map_turn_tex is not None:
                h = MAP_PAPER_HALF[1]
                th = math.pi * turn
                ct, st = math.cos(th), math.sin(th)
                up2 = tuple(up[i] * ct + fwd[i] * st for i in range(3))
                fwd2 = tuple(-up[i] * st + fwd[i] * ct for i in range(3))
                sy = 1.0 - (1.0 - MAP_TUCK_LEFT) * tuck
                hinge = tuple(pos[i] + h * up[i] for i in range(3))
                pos2 = tuple(hinge[i] - up2[i] * h * sy - fwd2[i] * MAP_SHEET_LIFT
                             for i in range(3))
                turn_model = self._basis_model(pos2, right, up2, fwd2, 1.0, sy, 1.0)
                to_eye = (eye[0] - pos2[0], eye[1] - pos2[1], eye[2] - pos2[2])
                front = -(fwd2[0] * to_eye[0] + fwd2[1] * to_eye[1] + fwd2[2] * to_eye[2])
                if front > 0.0:
                    self._draw_box(turn_model, (1.0, 1.0, 1.0), 0.0, texture=self.map_turn_tex,
                                   vao=self.map_paper_vao, alpha=fade)
                else:
                    self._draw_box(turn_model, MAP_PAPER_BACK, 0.0,
                                   vao=self.map_paper_vao, alpha=fade)
        if squash and "depth_squash" in self.prog:
            self.prog["depth_squash"].value = 0.0
        if bounce and "held_bounce" in self.prog:
            self.prog["held_bounce"].value = 0.0
        if "held_fill" in self.prog:
            self.prog["held_fill"].value = (0.0, 0.0, 0.0)
        if item == "lighter":
            flick = _organic_flicker(t)
            gust = _organic_flicker(t * 1.7, phase=3.1)
            dip = max(0.0, -gust) ** 3
            jitter = max(0.25, 1.0 + 0.16 * flick - 0.55 * dip)
            wobble = max(0.25, 1.0 + 0.07 * flick - 0.35 * dip)
            flame_alpha = fade * (1.0 - 0.7 * dip)
            flame_off = LIGHTER_FLAME_OFFSET
            fpos = (
                pos[0] + right[0] * flame_off[0] + up[0] * flame_off[1] + fwd[0] * flame_off[2],
                pos[1] + right[1] * flame_off[0] + up[1] * flame_off[1] + fwd[1] * flame_off[2],
                pos[2] + right[2] * flame_off[0] + up[2] * flame_off[1] + fwd[2] * flame_off[2],
            )
            fmodel = self._basis_model(fpos, right, up, fwd, wobble, jitter, wobble)
            self._draw_box(fmodel, (1.0, 0.75, 0.35), 1.0, vao=self.prop_vaos.get("lighter_flame"), alpha=flame_alpha)

    def set_map_turn_sheet(self, rgba_bytes, size):
        if self.map_turn_tex is None or self.map_turn_tex.size != (size, size):
            if self.map_turn_tex is not None:
                self.map_turn_tex.release()
            self.map_turn_tex = self.ctx.texture((size, size), 4)
            self.map_turn_tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
            self.map_turn_tex.repeat_x = False
            self.map_turn_tex.repeat_y = False
        self.map_turn_tex.write(rgba_bytes)
        self.map_turn_tex.build_mipmaps()

    NOTE_EXAMINE_DIST = 0.62
    NOTE_EXAMINE_FOV = 42.0

    def draw_note_examine(self, spin, screen_size, fade=1.0, ground=(0.035, 0.032, 0.030)):
        if self.note_tex is None or self.note_examine_vao is None:
            return
        ctx = self.ctx
        ctx.screen.use()
        ctx.viewport = (0, 0, screen_size[0], screen_size[1])
        ctx.enable(moderngl.DEPTH_TEST)
        ctx.disable(moderngl.BLEND)
        ctx.clear(ground[0] * fade, ground[1] * fade, ground[2] * fade, 1.0)

        sy, sx = spin
        cy, syy = math.cos(sy), math.sin(sy)
        cx, sxx = math.cos(sx), math.sin(sx)
        rot_y = np.array(((cy, 0.0, syy, 0.0), (0.0, 1.0, 0.0, 0.0),
                          (-syy, 0.0, cy, 0.0), (0.0, 0.0, 0.0, 1.0)))
        rot_x = np.array(((1.0, 0.0, 0.0, 0.0), (0.0, cx, -sxx, 0.0),
                          (0.0, sxx, cx, 0.0), (0.0, 0.0, 0.0, 1.0)))
        msx, msy, msz = NOTE_EXAMINE_SCALE
        flip = np.array(((msx, 0.0, 0.0, 0.0), (0.0, msy, 0.0, 0.0),
                         (0.0, 0.0, msz, 0.0), (0.0, 0.0, 0.0, 1.0)))
        model = rot_y @ rot_x @ flip
        view = gm.translate(0.0, 0.0, -self.NOTE_EXAMINE_DIST)
        proj = gm.perspective(math.radians(self.NOTE_EXAMINE_FOV),
                              screen_size[0] / max(1, screen_size[1]), 0.02, 8.0)
        prog = self.note_prog
        prog["mvp"].write(gm.to_gl(proj @ view @ model))
        prog["model"].write(gm.to_gl(model))
        prog["tex0"].value = 0
        prog["light_dir"].value = (-0.42, 0.40, 0.81)
        prog["ambient"].value = 0.46
        prog["fade"].value = max(0.0, min(1.0, fade))

        ctx.enable(moderngl.CULL_FACE)
        ctx.cull_face = "back"
        self.note_tex.use(location=0)
        self.note_examine_vao.render(moderngl.TRIANGLES)
        ctx.cull_face = "front"
        self.note_back_tex.use(location=0)
        self.note_examine_vao.render(moderngl.TRIANGLES)
        ctx.disable(moderngl.CULL_FACE)
        ctx.cull_face = "back"
        ctx.disable(moderngl.DEPTH_TEST)

    def set_note_sheet(self, front_bytes, back_bytes, size):
        for name in ("note_tex", "note_back_tex"):
            tex = getattr(self, name)
            if tex is None or tex.size != size:
                if tex is not None:
                    tex.release()
                tex = self.ctx.texture(size, 4)
                tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
                tex.repeat_x = False
                tex.repeat_y = False
                setattr(self, name, tex)
        self.note_tex.write(front_bytes)
        self.note_tex.build_mipmaps()
        self.note_back_tex.write(back_bytes)
        self.note_back_tex.build_mipmaps()

    def set_map_sheet(self, rgba_bytes, size):
        if self.map_tex is None or self.map_tex.size != (size, size):
            if self.map_tex is not None:
                self.map_tex.release()
            self.map_tex = self.ctx.texture((size, size), 4)
            self.map_tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
            self.map_tex.repeat_x = False
            self.map_tex.repeat_y = False
        self.map_tex.write(rgba_bytes)
        self.map_tex.build_mipmaps()

    def _draw_angel_billboard(self, rgba_bytes, tex_size, world_pos, world_size, view, proj, yaw, pitch):
        if self.angel_tex is None or self.angel_tex.size != (tex_size, tex_size):
            if self.angel_tex is not None:
                self.angel_tex.release()
            self.angel_tex = self.ctx.texture((tex_size, tex_size), 4)
            self.angel_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self.angel_tex.repeat_x = False
            self.angel_tex.repeat_y = False
        self.angel_tex.write(rgba_bytes)

        cy, sy = math.cos(yaw), math.sin(yaw)
        cp, sp = math.cos(pitch), math.sin(pitch)
        right = (-sy, cy, 0.0)
        up = (-sp * cy, -sp * sy, cp)

        prog = self.billboard_prog
        prog["view"].write(gm.to_gl(view))
        prog["proj"].write(gm.to_gl(proj))
        prog["world_pos"].value = world_pos
        prog["right"].value = right
        prog["up"].value = up
        prog["size"].value = (world_size, world_size)
        self._set_uniform("tex0", 0)
        self.angel_tex.use(location=0)

        self.ctx.enable(moderngl.BLEND)
        self.ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
        self.fbo.depth_mask = False
        self.billboard_vao.render(moderngl.TRIANGLES)
        self.fbo.depth_mask = True
        self.ctx.disable(moderngl.BLEND)

    def set_wall_holes(self, holes):
        self.wall_holes = list(holes)[:4]
        prog = self.prog
        if "wall_holes" not in prog:
            return
        data = [tuple(float(v) for v in h) for h in self.wall_holes] + [(0.0, 0.0, 0.0, 0.0)] * (4 - len(self.wall_holes))
        prog["wall_holes"].write(np.array(data, dtype="f4").tobytes())
        prog["wall_hole_count"].value = len(self.wall_holes)
        prog["wall_hole_top"].value = ELEVATOR_OPENING_H

    @staticmethod
    def eye_inside_cabin(p, eye):
        if eye is None:
            return False
        fx, fy = math.cos(p.facing), math.sin(p.facing)
        rx, ry = -fy, fx
        wall_x, wall_y = p.x - fx * p.hd, p.y - fy * p.hd
        rel_f = (eye[0] - wall_x) * fx + (eye[1] - wall_y) * fy
        rel_r = (eye[0] - wall_x) * rx + (eye[1] - wall_y) * ry
        return (-(ELEVATOR_CABIN_BACK + 0.15) < rel_f < 0.0
                and abs(rel_r) < ELEVATOR_CABIN_HALF + 0.15)

    def _draw_elevator_extras(self, p, eye):
        fx, fy = math.cos(p.facing), math.sin(p.facing)
        rx, ry = -fy, fx
        prog = self.prog
        if "snap_anchor" in prog:
            prog["snap_anchor"].value = (p.x, p.y, 0.0, 1.0)
        far = (eye is not None
               and math.hypot(p.x - eye[0], p.y - eye[1]) > S.ELEVATOR_FLAT_SHADE_DIST)
        prog["flat_shade"].value = 0.0 if far else 1.0
        self._draw_box(prop_model_bytes(p), (0.58, 0.58, 0.57), 0.0, vao=self.prop_vaos["elevator_cabin"])
        wall_x, wall_y = p.x - fx * p.hd, p.y - fy * p.hd
        inside = (not far) and self.eye_inside_cabin(p, eye)
        if not inside:
            prog["flat_shade"].value = 0.0
        closed = max(0.0, min(1.0, getattr(p, "door_t", 1.0)))
        oh, h = ELEVATOR_OPENING_HALF, ELEVATOR_OPENING_H
        w = oh / 2
        door_color = (0.56, 0.58, 0.60) if not inside else (0.50, 0.51, 0.52)
        for sy in (-1.0, 1.0):
            for depth, closed_c, open_c in ((ELEVATOR_DOOR_DEPTHS[0], w / 2, oh + w / 2),
                                            (ELEVATOR_DOOR_DEPTHS[1], w * 1.5, oh + w / 2)):
                along = sy * (open_c + (closed_c - open_c) * closed)
                back = p.hd + depth
                cx = p.x - fx * back + rx * along
                cy = p.y - fy * back + ry * along
                model = gm.trs_z(cx, cy, 0.0, p.facing, ELEVATOR_DOOR_T * 2, w, h + 0.01)
                self._draw_box(model, door_color, 0.0, texture=self.prop_textures["metal"], vao=self.box_vao)
        prog["flat_shade"].value = 0.0
        if "snap_anchor" in prog:
            prog["snap_anchor"].value = (0.0, 0.0, 0.0, 0.0)

    def _draw_elevator_call_lights(self, center, facing, lit_index, total_count, t, hd=None, inside=False):
        if total_count <= 0:
            return
        cx, cy = center
        fx, fy = math.cos(facing), math.sin(facing)
        rx, ry = -fy, fx
        prop_hd = PROP_DEFS["elevator"]["hd"] if hd is None else hd
        light_w, light_h, light_d = 0.05, 0.05, 0.012
        clearance = 0.002
        if inside:
            display_face = -prop_hd - ELEVATOR_FRONT_WALL - 0.008
            nudge = display_face - clearance - light_d / 2.0
            facing = facing + math.pi
            rx, ry = -rx, -ry
        else:
            plate_front = -prop_hd + 0.043
            nudge = plate_front + clearance + light_d / 2.0
        nx, ny = cx + fx * nudge, cy + fy * nudge
        spacing = 0.08
        start = -spacing * (total_count - 1) / 2.0
        z_base = 1.04 - light_h / 2.0
        lit_col = (0.35, 1.0, 0.55)
        unlit_col = (0.10, 0.11, 0.10)
        for i in range(total_count):
            off = start + i * spacing
            lx, ly = nx + rx * off, ny + ry * off
            model = gm.trs_z(lx, ly, z_base, facing, light_d, light_w, light_h)
            if i == lit_index:
                glow = 0.82 + 0.18 * _organic_flicker(t, 2.3 + i * 0.6)
                color = tuple(c * glow for c in lit_col)
                self._draw_box(model, color, 1.0, vao=self.box_vao)
            else:
                self._draw_box(model, unlit_col, 0.0, vao=self.box_vao)

    def render(self, maze, player, monster, props, dread, t, shake_yaw=0.0, shake_pitch=0.0,
               fog_color=None, fog_dist=12.5, ambient=0.06, moon_strength=0.0, world_flood=0.0,
               burn=None, haze=None,
               qa_mode=False, view_distance_mult=1.0, hide_locker=None, hide_swing=0.0,
               camera_override=None, extra_door_swings=None, hallu_intensity=0.0, hide_monster=False,
               angel_billboard=None, trip_intensity=0.0, debug_vision_cone=False,
               look_yaw=None, look_pitch=None):
        ctx = self.ctx
        self.fbo.use()
        ctx.viewport = (0, 0, self.low_w, self.low_h)
        ctx.clear(0.015, 0.013, 0.018, depth=1.0)
        ctx.enable(moderngl.DEPTH_TEST)
        ctx.disable(moderngl.CULL_FACE)
        ctx.disable(moderngl.BLEND)

        roll = 0.0
        if camera_override is not None:
            eye, yaw, pitch, fov_degrees = camera_override
        else:
            bob = math.sin(player.bob_phase) * (0.032 if player.is_sprinting else 0.020)
            bob *= 0.0 if player.is_hiding else 1.0
            crouch_drop = getattr(player, "crouch", 0.0) * S.CROUCH_EYE_DROP
            lean_x = getattr(player, "peek_x", player.x) - player.x
            lean_y = getattr(player, "peek_y", player.y) - player.y
            eye = (player.x + lean_x, player.y + lean_y,
                   EYE_HEIGHT + bob - crouch_drop + getattr(player, "fly_z", 0.0))
            yaw = (player.angle if look_yaw is None else look_yaw) + shake_yaw
            pitch = max(-1.45, min(1.45, (player.pitch if look_pitch is None else look_pitch) + shake_pitch))
            fov_degrees = FOV_DEGREES
            roll = getattr(player, "lean_t", 0.0) * math.radians(S.LEAN_ROLL_DEGREES)
            if trip_intensity > 0.0:
                roll += trip_intensity * math.sin(t * 0.31) * math.radians(S.PILL_TRIP_ROLL_DEGREES)
                fov_degrees += trip_intensity * math.sin(t * 0.23 + 1.7) * S.PILL_TRIP_FOV_DEGREES
        held_item = self._held_item_transform(maze, props, player, eye, yaw, pitch, roll,
                                              clear=camera_override is None)
        view = gm.view_matrix(eye, yaw, pitch, roll)
        proj = gm.perspective(math.radians(fov_degrees), self.low_w / self.low_h, 0.03, 130.0)
        self.last_camera = (eye, yaw, pitch, roll, fov_degrees)

        prog = self.prog
        prog["view"].write(gm.to_gl(view))
        prog["proj"].write(gm.to_gl(proj))
        prog["cam_pos"].value = eye
        fx, fy = math.cos(yaw) * math.cos(pitch), math.sin(yaw) * math.cos(pitch)
        fz = math.sin(pitch)
        prog["snap_res"].value = 100000.0 if qa_mode else self.snap_res
        prog["qa_mode"].value = 1.0 if qa_mode else 0.0
        prog["gamma"].value = self.gamma
        prog["fog_color"].value = tuple(c / 255.0 for c in (fog_color or S.COL_FOG))
        eff_fog_dist = max(0.1, fog_dist * view_distance_mult)
        prog["fog_dist"].value = eff_fog_dist
        prog["u_time"].value = t
        prog["u_resolution"].value = (float(self.low_w), float(self.low_h))
        prog["sanity_dark"].value = 1.0 - player.sanity / S.SANITY_MAX
        prog["hallu_intensity"].value = max(0.0, min(1.0, hallu_intensity))
        prog["ambient_level"].value = ambient
        prog["shadow_blocker_taps"].value = self.shadow_blocker_taps
        prog["shadow_filter_taps"].value = self.shadow_filter_taps
        prog["moon_dir"].value = MOON_TOWARD
        prog["moon_strength"].value = moon_strength
        prog["world_flood"].value = max(0.0, min(1.0, world_flood))
        if "ground_haze" in prog:
            strength, height, colour = haze if haze else (0.0, 1.0, (0.0, 0.0, 0.0))
            prog["ground_haze"].value = max(0.0, strength)
            prog["haze_height"].value = max(0.05, height)
            prog["haze_color"].value = tuple(float(c) for c in colour)
        if "burn_c" in prog:
            if burn is None:
                prog["burn_c"].value = (0.0, 0.0, 0.0, 0.0)
                prog["burn_width"].value = 1.0
            else:
                (bx, by, bz), radius, width = burn
                prog["burn_c"].value = (bx, by, bz, max(0.0, radius))
                prog["burn_width"].value = max(0.01, width)
        prog["no_fog"].value = 0.0
        prog["flat_shade"].value = 0.0
        self._set_uniform("u_alpha", 1.0)
        prog["material_diffuse"].value = 1.0
        prog["material_specular"].value = 0.0
        prog["alpha_cutout"].value = 0.0
        _t_lights0 = time.perf_counter()
        lights = select_prop_lights(props, eye, eff_fog_dist, t)
        self._held_fill = (0.0, 0.0, 0.0)
        if held_item is not None and held_item[0] == "lighter" and held_item[6] > 0.001:
            defs = held_item[1]
            fade = min(1.0, held_item[6] * 4.0)
            flick = 0.78 + 0.22 * _organic_flicker(t)
            self._held_fill = tuple(c * HELD_LIGHTER_FILL * flick * fade for c in defs["light_color"])
            lights.insert(0, lighter_light(held_item[2], defs["light_radius"],
                                           tuple(c * flick * fade for c in defs["light_color"])))
        prog["is_held_item"].value = 0.0
        if player.flashlight_on:
            flicker = 1.0
            if player.battery < S.FLASHLIGHT_LOW:
                flicker = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(t * 40 + random.random() * 3))
            if random.random() < 0.002:
                flicker *= 0.2
            intensity = flicker * (player.battery / 100.0)
            if held_item is not None and held_item[0] == "flashlight" and held_item[6] > 0.001:
                self._held_fill = tuple(c * intensity for c in HELD_FLASHLIGHT_FILL)
                lights.insert(0, flashlight_fill_light(eye, (fx, fy, fz), intensity, t))
                lights.insert(0, flashlight_light(held_item[2], held_item[5], intensity, t,
                                                  share=1.0 - FLASH_FILL_SHARE))
            else:
                lights.insert(0, flashlight_light(eye, (fx, fy, fz), intensity, t))
        if moon_strength > 0.0 and self._moon_can_show(eye):
            lights.insert(0, moon_light(moon_strength))
        _t_lights1 = time.perf_counter()

        prop_cull_dist = eff_fog_dist + 2.0
        prop_fade_band = max(S.PROP_FADE_BAND, prop_cull_dist * S.PROP_FADE_FRACTION)
        visible_props = self._cull_by_distance(props, eye, prop_cull_dist)

        check_frac = self.compute_check_frac(monster)
        checking_timer = getattr(monster, "checking_timer", 0.0)
        door_swings = {}
        if checking_timer > 0.0 and getattr(monster, "locker_target", None) is not None:
            door_swings[id(monster.locker_target)] = check_frac
        closing_locker = getattr(monster, "closing_locker", None)
        closing_timer = getattr(monster, "closing_timer", 0.0)
        if closing_locker is not None and closing_timer > 0.0:
            close_frac = closing_timer / S.MONSTER_LOCKER_CLOSE_SECONDS
            door_swings[id(closing_locker)] = max(door_swings.get(id(closing_locker), 0.0), close_frac)
        if hide_locker is not None and hide_swing > 0.0:
            door_swings[id(hide_locker)] = max(door_swings.get(id(hide_locker), 0.0), hide_swing)
        if extra_door_swings:
            for key, frac in extra_door_swings.items():
                door_swings[key] = max(door_swings.get(key, 0.0), frac)
        self._frame_door_swings = door_swings

        _t_shadowmap0 = time.perf_counter()
        lights = self._update_shadows(lights, props, visible_props, monster, eye)
        _t_shadowmap1 = time.perf_counter()
        self._restore_main_pass_state(view, proj, qa_mode)
        self._upload_lights(lights)

        prog["model"].write(gm.to_gl(gm.identity()))
        self._set_uniform("base_color", (1.0, 1.0, 1.0))
        self._set_uniform("emissive", 0.0)
        outdoor_air = getattr(self, "outdoor_ground_vao", None) is not None
        self._set_uniform("moon_outdoor_only", 1.0 if outdoor_air else 0.0)
        self._set_outdoor(False)
        self._set_uniform("outdoor_ambient", S.OUTER_YARD_AMBIENT)
        self._set_uniform("outdoor_fog_color",
                          tuple(c / 255.0 for c in S.OUTER_YARD_FOG_COLOR))
        self._set_uniform("outdoor_fog_dist", S.OUTER_YARD_FOG_DIST)
        if "snap_object" in prog:
            prog["snap_object"].value = 0.0

        if self.sky_dome_vao is not None:
            prog["no_fog"].value = 1.0
            prog["model"].write(gm.to_gl(gm.translate(eye[0], eye[1], 0.0)))
            self.sky_dome_vao.render(moderngl.TRIANGLES)
            prog["model"].write(gm.to_gl(gm.identity()))
            prog["no_fog"].value = 0.0
            self._set_uniform("emissive", 0.0)

        _t_walls0 = time.perf_counter()
        self._set_uniform("use_tex", 1.0)
        self._set_uniform("tex_scale", (1.0, 1.0))
        self._set_uniform("tex0", 0)
        if "apply_wall_holes" in prog:
            prog["apply_wall_holes"].value = 1.0 if self.wall_holes else 0.0
        self._set_grime(True)
        self._set_dado(True)
        for vao, _vbo, tile_type in self.wall_parts:
            material = wall_material(tile_type)
            if material.alpha < 1.0:
                continue
            self._apply_material(material)
            self._set_outdoor(outdoor_air and tile_type in _OUTDOOR_TILES)
            vao.render(moderngl.TRIANGLES)
        self._set_outdoor(False)
        self._set_dado(False)
        self._end_material()
        if "apply_wall_holes" in prog:
            prog["apply_wall_holes"].value = 0.0
        self._set_uniform("use_tex", 1.0)

        _t_walls1 = time.perf_counter()
        prog["snap_res"].value = 100000.0
        density = self.floor_tex_density
        self._set_uniform("tex_scale", (maze.w * density, maze.h * density))
        self.tex_floor.use(location=0)
        if self.floor_vao is not None:
            self.floor_vao.render(moderngl.TRIANGLES)
        ground_vao = getattr(self, "outdoor_ground_vao", None)
        if ground_vao is not None:
            self._set_uniform("tex_scale", (1.0, 1.0))
            self.tex_floor_yard.use(location=0)
            self._set_outdoor(outdoor_air)
            ground_vao.render(moderngl.TRIANGLES)
            self._set_outdoor(False)
            self._set_uniform("tex_scale", (maze.w * density, maze.h * density))
            self.tex_floor.use(location=0)
        shed_vao = getattr(self, "shed_floor_vao", None)
        if shed_vao is not None:
            self._set_uniform("tex_scale", (1.0, 1.0))
            self.tex_floor_basement.use(location=0)
            shed_vao.render(moderngl.TRIANGLES)
            self._set_uniform("tex_scale", (maze.w * density, maze.h * density))
            self.tex_floor.use(location=0)

        if self.ceil_vao is not None and eye[2] < S.WALL_HEIGHT:
            self.tex_ceiling.use(location=0)
            self.ceil_vao.render(moderngl.TRIANGLES)
        if self.yard_roof_vao is not None:
            self._set_uniform("tex_scale", (1.0, 1.0))
            self.tex_roof.use(location=0)
            self.yard_roof_vao.render(moderngl.TRIANGLES)
        if self.yard_roof_wood_vao is not None:
            self._set_uniform("tex_scale", (1.0, 1.0))
            self.tex_wall_shed.use(location=0)
            self.yard_roof_wood_vao.render(moderngl.TRIANGLES)
        self._set_grime(False)
        prog["snap_res"].value = self.snap_res
        if "snap_object" in prog:
            prog["snap_object"].value = 1.0 if self.snap_props_whole else 0.0
        _t_floor1 = time.perf_counter()

        if debug_vision_cone:
            self._draw_vision_cone(monster)


        drawn_props = visible_props
        if self.main_pass_cone_cull:
            corner_tan = math.tan(math.radians(fov_degrees) / 2.0) * math.sqrt(1.0 + (self.low_w / self.low_h) ** 2)
            drawn_props = self._props_in_cone(visible_props, eye, (fx, fy, fz), math.atan(corner_tan),
                                              prop_cull_dist + self._CULL_OVERHANG + 10.0)
        self.last_drawn_prop_count = len(drawn_props)
        self._draw_props(drawn_props, t, door_swings=door_swings,
                          eye=eye, cull_dist=prop_cull_dist, fade_band=prop_fade_band)
        if self.hallu_eyes:
            self._draw_hallu_eyes(eye, t)
        _t_props1 = time.perf_counter()
        if not hide_monster and not self._monster_fully_fogged(monster, eye, eff_fog_dist):
            self._draw_monster(monster, dread, check_frac=check_frac)
        _t_monster1 = time.perf_counter()
        self._draw_translucent_walls()
        prog["is_held_item"].value = 1.0
        if "held_shadow_at" in prog:
            prog["held_shadow_at"].value = (player.x, player.y, GAMEPLAY_RECEIVER_HEIGHT)
        self._draw_held_item(player, held_item, t)
        prog["is_held_item"].value = 0.0
        if angel_billboard is not None:
            rgba_bytes, tex_size, world_pos, world_size = angel_billboard
            self._draw_angel_billboard(rgba_bytes, tex_size, world_pos, world_size, view, proj, yaw, pitch)
        self.last_perf = {
            "lights": (_t_lights1 - _t_lights0) * 1000.0,
            "shadows": (_t_shadowmap1 - _t_shadowmap0) * 1000.0,
            "walls": (_t_walls1 - _t_walls0) * 1000.0,
            "floor": (_t_floor1 - _t_walls1) * 1000.0,
            "props": (_t_props1 - _t_floor1) * 1000.0,
            "monster": (_t_monster1 - _t_props1) * 1000.0,
        }

    _RAW_SURFACE_MASKS = (0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)

    def composite(self, hud_rgba_bytes, hud_size, screen_size, trip_intensity=0.0, t=0.0,
                  comedown_intensity=0.0, hud_surface=None, god_ray=None, keep_color=False,
                  hud_reuse=False):
        raw = None
        if hud_surface is not None:
            if (hud_surface.get_bytesize() == 4 and hud_surface.get_masks() == self._RAW_SURFACE_MASKS
                    and hud_surface.get_pitch() == hud_surface.get_width() * 4):
                raw = hud_surface
            else:
                import pygame
                hud_rgba_bytes = pygame.image.tostring(hud_surface, "RGBA", True)
        ctx = self.ctx
        ctx.screen.use()
        ctx.viewport = (0, 0, screen_size[0], screen_size[1])
        ctx.disable(moderngl.DEPTH_TEST)
        ctx.disable(moderngl.BLEND)
        if keep_color:
            self._composite_hud(raw, hud_rgba_bytes, hud_size, hud_reuse)
            return
        ctx.clear(0.0, 0.0, 0.0)

        self.color_tex.use(location=0)
        self.quad_prog["tex0"].value = 0
        self.quad_prog["trip_intensity"].value = max(0.0, min(1.0, trip_intensity))
        self.quad_prog["comedown_intensity"].value = max(0.0, min(1.0, comedown_intensity))
        self.quad_prog["u_time"].value = t
        self.quad_prog["apply_trip"].value = 1.0
        self.quad_prog["raw_surface"].value = 0.0
        if "god_ray" in self.quad_prog:
            self.quad_prog["god_ray"].value = god_ray if god_ray is not None else (0.5, 0.5, 0.0)
        self.quad_vao.render(moderngl.TRIANGLES)

        self._composite_hud(raw, hud_rgba_bytes, hud_size, hud_reuse)

    def _composite_hud(self, raw, hud_rgba_bytes, hud_size, reuse=False):
        ctx = self.ctx
        if reuse and self.hud_tex is None:
            reuse = False
        if reuse or raw is not None or hud_rgba_bytes is not None:
            if not reuse:
                self.ensure_hud_texture(*hud_size)
            if reuse:
                self.quad_prog["raw_surface"].value = 1.0 if self._hud_tex_raw else 0.0
            elif raw is not None:
                view = raw.get_view("0")
                try:
                    self.hud_tex.write(view)
                finally:
                    del view
                self._hud_tex_raw = True
                self.quad_prog["raw_surface"].value = 1.0
            else:
                self.hud_tex.write(hud_rgba_bytes)
                self._hud_tex_raw = False
            ctx.enable(moderngl.BLEND)
            ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
            self.hud_tex.use(location=0)
            self.quad_prog["tex0"].value = 0
            self.quad_prog["apply_trip"].value = 0.0
            self.quad_vao.render(moderngl.TRIANGLES)
            self.quad_prog["raw_surface"].value = 0.0
            ctx.disable(moderngl.BLEND)
