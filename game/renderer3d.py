import math
import random
import time

import moderngl
import numpy as np

from game import settings as S
from game import gl_math as gm
from game.props import BREAKABLE_LIGHT_KINDS

EYE_HEIGHT = 0.62
FOV_DEGREES = 88.0
MAX_POINT_LIGHTS = 12
MAX_DOOR_SEGS = 16
MAX_WINDOW_SEGS = 24
DOOR_SWING_MAX_ANGLE = math.radians(120)

VERTEX_SHADER = """
#version 330
uniform mat4 model;
uniform mat4 view;
uniform mat4 proj;
uniform float snap_res;

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
        vec2 ndc = clip.xy / clip.w;
        vec2 snapped = floor(ndc * snap_res + 0.5) / snap_res;
        clip.xy = snapped * clip.w;
    }

    gl_Position = clip;
    v_world_pos = world.xyz;
    v_normal = mat3(model) * in_normal;
    v_uv = in_uv;
    v_color = in_color;
}
"""

FRAGMENT_SHADER = """
#version 330
uniform vec3 base_color;
uniform float emissive;
uniform vec3 cam_pos;
uniform vec3 cam_forward;
uniform float flash_on;
uniform float flash_intensity;
uniform vec3 fog_color;
uniform float fog_dist;
uniform vec2 tex_scale;
uniform sampler2D tex0;
uniform float use_tex;
uniform float u_time;
uniform float sanity_dark;
uniform vec2 u_resolution;
uniform float ambient_level;
uniform vec3 moon_dir;
uniform float moon_strength;
uniform float no_fog;
uniform float flat_shade;
uniform float qa_mode;
uniform float u_alpha;
uniform float glass_dim;

#define MAX_POINT_LIGHTS 12
uniform vec3 light_pos[MAX_POINT_LIGHTS];
uniform vec3 light_color[MAX_POINT_LIGHTS];
uniform float light_radius[MAX_POINT_LIGHTS];
uniform sampler2D wall_mask;
uniform vec2 maze_size;

// Closed doors' own floor-plane segments (see Door.closed_line /
// Renderer3D.sync_door_mask) - an exact line test, not a per-cell mask
// entry like a real wall gets. A door is thin (~0.07 units) inside a
// whole 1-unit wall_mask texel, so patching that texel solid (as doors
// used to) either blocked the door's OWN near face from light on its own
// side (texel-membership can't tell "my own surface" from "genuinely the
// far side"), or - once that got excluded to fix the door's own face -
// let light leak straight through to the floor just past the door, still
// inside that same texel. A segment crossing test doesn't have this
// ambiguity: it only reports a block for a ray that geometrically crosses
// the door's slab at a point strictly between the two ends, so the door's
// own surface (the ray's actual endpoint sitting ON the segment) is never
// self-blocking, while anything past it always is. Unused slots are a
// zero-length segment (both points equal), which never crosses anything.
#define MAX_DOOR_SEGS 16
uniform vec2 door_seg_a[MAX_DOOR_SEGS];
uniform vec2 door_seg_b[MAX_DOOR_SEGS];

// Windows get the exact same segment treatment as closed doors, for the
// exact same reason (see the comment above): a per-cell wall_mask entry
// can't tell "this fragment IS the window's own glass/frame, being lit
// from its own side" from "this fragment is just past it in the same
// texel" - marking a window cell solid in wall_mask self-blocked a lamp
// in the SAME room as the window from lighting the window itself (and
// the wall right next to it), a visible dark seam right at the window's
// texel. Static for a level's lifetime (windows don't open/close), so
// this is uploaded once in Renderer3D.build_level rather than per frame
// like the door segments.
#define MAX_WINDOW_SEGS 24
uniform vec2 window_seg_a[MAX_WINDOW_SEGS];
uniform vec2 window_seg_b[MAX_WINDOW_SEGS];

in vec3 v_world_pos;
in vec3 v_normal;
in vec2 v_uv;
in vec3 v_color;
out vec4 frag_color;

// 2D ray march (light -> fragment, ground-plane XY only - a wall blocks its
// whole cell's height uniformly, same assumption Maze.blocks_sight makes on
// the CPU side) against wall_mask (Renderer3D.build_level's
// build_wall_mask), so a point light's own attenuation-by-distance below
// stops meaning "distance through solid walls too" - a lamp on the other
// side of a wall no longer lights up this side of it just because it
// happens to be close enough. Step budget (0.2 world units, up to 26 steps
// = 5.2 units) covers every PROP_DEFS light_radius in play with margin;
// only ever called for a light whose plain distance attenuation was
// already > 0, so most fragments never pay for this at all.
//
// Returns an occlusion factor (0=fully clear, 1=fully blocked) rather than
// a boolean. wall_mask is one texel per grid cell sampled at its exact
// center, so occlusion used to flip in a hard binary step exactly at a
// cell boundary - two adjacent floor cells straddling that boundary (one
// with a clear path to a light, one where a single corner cell's worth of
// wall grazes the ray) rendered with a full on/off jump between them,
// visible as a hard seam wherever that boundary ran across the screen.
// Saturating at 2 blocked samples rather than 1 means a lone grazing hit
// (a corner clipping the ray for just one 0.2-unit step) only dims the
// light instead of zeroing it, softening exactly that case, while any
// actual wall - which spans multiple consecutive samples along the ray at
// this resolution - still reads as fully opaque, same as before.
//
// Closed doors are handled entirely separately, below (segment_crosses) -
// wall_mask only ever holds real, permanently-solid level geometry now.
// An earlier version also patched a closed door's cell into this same
// per-cell mask and excluded the destination cell from counting to let
// the door's own near face be lit - which also excluded that cell for
// anything ELSE sharing it (a wall-mounted lamp's own wall, or floor just
// past the door within the same texel), leaking light through real walls
// and past closed doors alike. A cell-grained mask fundamentally can't
// distinguish "this fragment IS the thin solid object" from "this
// fragment is just on the far side of it, still within the same texel" -
// only an exact segment test (see below) can, which is why doors moved
// off this path entirely instead of trying to patch the exclusion further.
bool segment_crosses(vec2 a, vec2 b, vec2 c, vec2 d) {
    vec2 r = b - a;
    vec2 s = d - c;
    float denom = r.x * s.y - r.y * s.x;
    if (abs(denom) < 1e-6) return false;
    vec2 diff = c - a;
    float t = (diff.x * s.y - diff.y * s.x) / denom;
    float u = (diff.x * r.y - diff.y * r.x) / denom;
    // t strictly inside (0, 1) excluding a thin margin at both ends: a
    // hit right at t~1 is the fragment's own position (e.g. a point ON a
    // door's segment being lit from its own side) and must NOT self-block;
    // a hit right at t~0 would be the light's own position, never
    // meaningful. Anything crossing strictly between the two is a real
    // door standing between the light and whatever it's trying to reach.
    return t > 0.02 && t < 0.98 && u > 0.0 && u < 1.0;
}

float wall_occlusion(vec2 a, vec2 b) {
    float d = length(b - a);
    if (d < 0.001) return 0.0;
    vec2 dir = (b - a) / d;
    int n = min(26, int(d / 0.2) + 1);
    int blocked = 0;
    for (int i = 1; i < n; i++) {
        vec2 p = a + dir * (float(i) * 0.2);
        vec2 uv = (floor(p) + 0.5) / maze_size;
        if (texture(wall_mask, uv).r > 0.5) {
            blocked++;
        }
    }
    for (int j = 0; j < MAX_DOOR_SEGS; j++) {
        if (segment_crosses(a, b, door_seg_a[j], door_seg_b[j])) {
            blocked += 2;
        }
    }
    for (int j = 0; j < MAX_WINDOW_SEGS; j++) {
        if (segment_crosses(a, b, window_seg_a[j], window_seg_b[j])) {
            blocked += 2;
        }
    }
    return min(1.0, float(blocked) / 2.0);
}

void main() {
    vec3 N = normalize(v_normal);
    vec3 to_cam = cam_pos - v_world_pos;
    float dist = length(to_cam);

    // Set per-floor from Python (indoor corridors near-black, the yard a
    // bit brighter to read as moonlit rather than pitch dark) - the
    // flashlight still needs to be the actual difference between seeing
    // and not, not just a nice-to-have, but "outside at night" and "sealed
    // concrete corridor" shouldn't be equally black.
    float ambient = ambient_level;
    float moon = max(0.0, dot(N, normalize(moon_dir))) * moon_strength;
    float spot = 0.0;
    if (flash_on > 0.5) {
        vec3 light_dir = normalize(v_world_pos - cam_pos);
        float cos_angle = dot(light_dir, normalize(cam_forward));
        // Softer outer falloff (was a fairly hard-edged cone) reads more
        // like a real bulb's spill than a stage spotlight cutout.
        float cone = smoothstep(0.6, 0.95, cos_angle);
        float atten = clamp(1.0 - dist / 10.5, 0.0, 1.0);
        float ndotl = max(0.0, dot(N, -light_dir));
        float living = 0.97 + 0.03 * sin(u_time * 2.3);
        // A brighter, tighter hot core inside the wider spill - a real bulb
        // isn't a flat-intensity disc, the centre of the beam is
        // noticeably hotter than its edge.
        float hot = smoothstep(0.88, 0.99, cos_angle) * 0.6;
        // 0.8 trim - stacked on an already-lit lamp/sconce this used to
        // blow straight past the clamp below and read as a blown-out
        // overexposed patch instead of "flashlight adding onto a lit
        // area".
        spot = (cone + hot) * atten * (0.5 + 0.5 * ndotl) * flash_intensity * living * 0.8;
    }
    // Warm incandescent tint on the beam itself, kept separate from the
    // neutral ambient term so only what the flashlight actually touches
    // picks up the colour - blending it into a single scalar washed the
    // whole scene the same warm grey regardless of what was lit.
    vec3 warm = vec3(1.05, 0.93, 0.78);
    vec3 cold_moon = vec3(0.55, 0.62, 0.78);

    vec3 color;
    if (no_fog > 0.5) {
        // The sky (no_fog is only ever set for it): every texture/mesh-
        // topology approach tried here - one flat plane, a textured box,
        // an untextured vertex-gradient box, a UV-mapped dome - kept
        // producing SOME visible artifact (a hole, a duplicated moon, a
        // hard seam at every face edge, streaky stars from the UV
        // distortion near a dome's pole), because all of them fundamentally
        // stretch a flat 2D image over 3D geometry along some seam or pole.
        // Computing colour directly from the fragment's view DIRECTION has
        // no topology to seam along AT ALL - it's a pure function of "which
        // way is this pixel looking", identical no matter which triangle of
        // whatever mesh happened to produce it, continuous everywhere by
        // construction rather than by careful edge-matching.
        vec3 dir = normalize(v_world_pos - cam_pos);
        float horizon = 1.0 - clamp(dir.z, -0.2, 1.0);
        vec3 sky_base = mix(vec3(0.020, 0.024, 0.036), vec3(0.10, 0.11, 0.16), horizon * horizon);
        vec3 hp = floor(dir * 46.0);
        float sh = fract(sin(dot(hp, vec3(12.9898, 78.233, 37.719))) * 43758.5453);
        float star = step(0.9978, sh) * (0.45 + 0.55 * fract(sh * 91.7));
        // moon_dir's Z is negative (tuned for the surface-lighting `moon`
        // term above, where a downward-ish light direction is what makes
        // dot(N, moon_dir) favour upward-facing surfaces correctly) - for
        // placing the moon itself IN the sky, that same vector points
        // toward the ground, not up where `dir` (a ray looking up at the
        // sky) would ever match it. Flipping just Z here (not touching the
        // shared uniform, so the surface-lighting use above is untouched)
        // points the disc up where a moon actually belongs.
        vec3 sky_moon_dir = normalize(vec3(moon_dir.x, moon_dir.y, -moon_dir.z));
        float moon_dot = max(0.0, dot(dir, sky_moon_dir));
        float moon_glow = pow(moon_dot, 220.0) * 1.5 + pow(moon_dot, 14.0) * 0.4;
        float moon_amt = clamp(moon_strength * 3.0, 0.3, 1.3);
        color = sky_base + vec3(star * 0.8) + moon_glow * vec3(0.75, 0.82, 0.95) * moon_amt;
    } else if (flat_shade > 0.5) {
        // Blob shadow decals (see build_shadow_mesh / _draw_shadows): a
        // constant dark tone instead of the usual ambient+moon+flashlight
        // lighting, so the shadow always reads as a shadow - lit normally
        // it would wash out under the flashlight (exactly backwards for
        // something meant to represent blocked light) or vanish at low
        // ambient. Still goes through the same fog mix below as everything
        // else, so it fades into the distance instead of looking pasted on.
        color = base_color * v_color;
        float fog_t = pow(clamp(dist / fog_dist, 0.0, 1.0), 1.25);
        color = mix(color, fog_color, fog_t);
    } else {
        vec3 lighting = vec3(ambient) + moon * cold_moon + spot * warm;
        // Furniture-mounted point lights (lamp/sconce/exit sign/monitor -
        // see PROP_DEFS' light_radius/light_color and Renderer3D.render,
        // which fills these arrays from whatever's actually in the level
        // each frame). radius=0 for an unused slot forces atten to 0
        // instead of a divide-by-zero, so the array can always be the same
        // fixed MAX_POINT_LIGHTS size regardless of how many lights are
        // really active this frame - no separate light-count uniform needed.
        for (int i = 0; i < MAX_POINT_LIGHTS; i++) {
            float d = length(light_pos[i] - v_world_pos);
            float lt = clamp(d / max(light_radius[i], 0.001), 0.0, 1.0);
            // Softened from atten*atten (quadratic) to plain-linear - linear
            // keeps the overall brightness profile (~50% at half radius, so
            // the earlier "only reads as on right next to the fixture" fix
            // still holds) but has a sharp derivative kink exactly at
            // d=radius, reading as a hard ring right at a light's own
            // edge. A first attempt at smoothing that (full-range
            // smoothstep(0,1,lt)) traded too much for it - the tail drops
            // off much faster than linear well before d=radius (~0.03 vs
            // 0.1 at lt=0.9), which measurably shrank every light's usable
            // reach and turned areas that used to catch a dim edge of a
            // distant light fully black. Smoothing ONLY the last 15% of
            // the range keeps the linear profile (and reach) everywhere
            // else, killing just the kink at the very edge instead of the
            // whole falloff shape.
            float atten = (1.0 - lt) * (1.0 - smoothstep(0.85, 1.0, lt));
            if (atten > 0.0) {
                atten *= (1.0 - wall_occlusion(light_pos[i].xy, v_world_pos.xy));
            }
            lighting += light_color[i] * atten;
        }
        lighting = clamp(lighting, 0.0, 1.45);
        // Window glass: capped separately from every other surface's 1.45
        // ceiling above, regardless of how lit the room actually is (ambient,
        // moon, nearby lamps, or a flashlight aimed straight at it) - without
        // this, glass's own pale vertex colour picks up the same full
        // lighting term as an opaque wall, then gets alpha-blended over an
        // already-correctly-exposed room on the far side, reading as a
        // brightening wash rather than a tinted pane. A flat colour/alpha
        // tweak alone can't fix that (it'd still blow out under a flashlight
        // aimed at the glass); capping the term glass responds with does.
        if (glass_dim > 0.5) {
            lighting = clamp(lighting, 0.0, 0.55);
        }

        vec3 tex_col = vec3(1.0);
        if (use_tex > 0.5) {
            tex_col = texture(tex0, v_uv * tex_scale).rgb;
        }

        color = base_color * v_color * tex_col * lighting;
        if (emissive > 0.5) {
            color += base_color * v_color * tex_col * 0.85;
        }

        float fog_t = pow(clamp(dist / fog_dist, 0.0, 1.0), 1.25);
        color = mix(color, fog_color, fog_t);
    }

    // qa_mode skips every PS1 post-process step below (vignette, sanity
    // tint, grain, dither, colour quantisation) - all of it is meant to
    // read as period-correct noise plastered over the whole frame, which
    // is exactly what makes a single prop's own geometry/shading hard to
    // judge in isolation when the point is inspecting THAT prop (see
    // Renderer3D.render's qa_mode param and the model-QA script).
    if (qa_mode < 0.5) {
        vec2 vp = gl_FragCoord.xy / u_resolution;
        float d = distance(vp, vec2(0.5, 0.5));
        float vig = smoothstep(0.32, 0.92, d);
        color = mix(color, vec3(0.0), vig * 0.55);
        color = mix(color, vec3(0.10, 0.0, 0.0), sanity_dark * 0.30);

        // Scaled down in near-black areas - a fixed-amplitude time-varying
        // grain added to an already-tiny colour value repeatedly pushes it
        // across a quantisation step (see `levels` below) and back as
        // u_time advances, which reads as the dark itself twinkling/
        // flickering rather than as film grain. Floored at 0.35 so
        // there's still SOME grain even in true black (keeps the PS1
        // texture), just not full strength.
        float luma = dot(color, vec3(0.333));
        float grain = fract(sin(dot(gl_FragCoord.xy + u_time * 130.0, vec2(12.9898, 78.233))) * 43758.5453);
        color += (grain - 0.5) * 0.010 * clamp(luma * 3.0, 0.35, 1.0);

        const float bayer[16] = float[16](
            0.0, 8.0, 2.0, 10.0, 12.0, 4.0, 14.0, 6.0,
            3.0, 11.0, 1.0, 9.0, 15.0, 7.0, 13.0, 5.0
        );
        int xi = int(mod(gl_FragCoord.x, 4.0));
        int yi = int(mod(gl_FragCoord.y, 4.0));
        // Softer dither + finer quantisation steps than before - the PS1
        // "banded colour" look is still there, it just no longer reads as
        // visual noise plastered over big flat surfaces (mainly the
        // floor/ceiling planes).
        float threshold = (bayer[yi * 4 + xi] / 16.0 - 0.5) * (1.0 / 34.0);
        color += threshold;

        float levels = 44.0;
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
in vec2 v_uv;
out vec4 frag_color;
void main() {
    frag_color = texture(tex0, v_uv);
}
"""


def _smoothstep(edge0, edge1, x):
    t = max(0.0, min(1.0, (x - edge0) / (edge1 - edge0)))
    return t * t * (3.0 - 2.0 * t)


def _quad(verts, p0, p1, p2, p3, normal, color, uv_scale=(1.0, 1.0), uv_offset=(0.0, 0.0)):
    n = normal
    c = color
    ou, ov = uv_offset
    uvs = [(ou, ov), (ou + uv_scale[0], ov), (ou + uv_scale[0], ov + uv_scale[1]), (ou, ov + uv_scale[1])]
    pts = [p0, p1, p2, p3]
    for i in (0, 1, 2, 0, 2, 3):
        p, uv = pts[i], uvs[i]
        verts.extend([p[0], p[1], p[2], n[0], n[1], n[2], uv[0], uv[1], c[0], c[1], c[2]])


def build_box_mesh():
    v = []
    x0, x1, y0, y1, z0, z1 = -0.5, 0.5, -0.5, 0.5, 0.0, 1.0
    w = (1, 1, 1)
    _quad(v, (x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1), (1, 0, 0), w)
    _quad(v, (x0, y1, z0), (x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (-1, 0, 0), w)
    _quad(v, (x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (0, 1, 0), w)
    _quad(v, (x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1), (0, -1, 0), w)
    _quad(v, (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (0, 0, 1), w)
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
    return np.array(v, dtype="f4")


def _mini_box(v, cx, cy, cz, sx, sy, sz, color, skip_bottom=False, skip_back=False):
    x0, x1, y0, y1, z0, z1 = cx - sx, cx + sx, cy - sy, cy + sy, cz - sz, cz + sz
    _quad(v, (x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1), (1, 0, 0), color)
    if not skip_back:
        _quad(v, (x0, y1, z0), (x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (-1, 0, 0), color)
    _quad(v, (x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (0, 1, 0), color)
    _quad(v, (x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1), (0, -1, 0), color)
    _quad(v, (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (0, 0, 1), color)
    if not skip_bottom:
        _quad(v, (x0, y1, z0), (x1, y1, z0), (x1, y0, z0), (x0, y0, z0), (0, 0, -1), color)


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

    brass = (0.8, 0.68, 0.32)
    _mini_box(v, x1 + 0.03, y0 + 0.1, 0.55, 0.03, 0.03, 0.03, brass)
    _mini_box(v, x0 - 0.03, y0 + 0.1, 0.55, 0.03, 0.03, 0.03, brass)

    keyhole = (0.02, 0.02, 0.02)
    khy = S.DOOR_KEYHOLE_LOCAL_Y
    khz = S.DOOR_KEYHOLE_LOCAL_Z
    _mini_box(v, x1 + 0.012, khy, khz + 0.02, 0.012, 0.018, 0.018, keyhole)
    _mini_box(v, x1 + 0.012, khy, khz - 0.025, 0.012, 0.009, 0.014, keyhole)
    _mini_box(v, x0 - 0.012, khy, khz + 0.02, 0.012, 0.018, 0.018, keyhole)
    _mini_box(v, x0 - 0.012, khy, khz - 0.025, 0.012, 0.009, 0.014, keyhole)
    return np.array(v, dtype="f4")


_DOOR_BREAK_LEAN = 0.14


def build_broken_door_mesh():
    v = []
    x0, x1, y0, y1, z0, z1 = -0.5, 0.5, -0.5, 0.5, 0.0, 1.0
    w = (1, 1, 1)
    dark = (0.5, 0.44, 0.35)

    def sh(p):
        return (p[0], p[1] + _DOOR_BREAK_LEAN * p[2], p[2])

    def sq(p0, p1, p2, p3, normal, col, **kw):
        _quad(v, sh(p0), sh(p1), sh(p2), sh(p3), normal, col, **kw)

    sq((x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (0, 1, 0), w)
    sq((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1), (0, -1, 0), w)
    sq((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (0, 0, 1), w)

    def panel_face(fx, order):
        def q(ya, yb, za, zb, col):
            uv_sc = (yb - ya, zb - za)
            if order > 0:
                uv_off = (ya - y0, za - z0)
                sq((fx, ya, za), (fx, yb, za), (fx, yb, zb), (fx, ya, zb), (order, 0, 0), col,
                   uv_scale=uv_sc, uv_offset=uv_off)
            else:
                uv_off = (y1 - yb, za - z0)
                sq((fx, yb, za), (fx, ya, za), (fx, ya, zb), (fx, yb, zb), (order, 0, 0), col,
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
    return np.array(v, dtype="f4")


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
    v = []
    frame = (0.42, 0.34, 0.24)
    sheet = (0.86, 0.84, 0.80)
    pillow = (0.94, 0.92, 0.88)
    leg_h = 0.75
    top = _leg_set(v, 0.48, 0.46, leg_h, 0.25, 0.05, frame, sheet)
    apron_h = 0.14
    _mini_box(v, 0.0, 0.0, leg_h - apron_h / 2 + 0.02, 0.46, 0.44, apron_h / 2, frame)
    _mini_box(v, 0.0, -0.34, top + 0.045, 0.36, 0.09, 0.045, pillow)
    return np.array(v, dtype="f4")


def build_desk_mesh():
    v = []
    wood = (0.55, 0.40, 0.24)
    top = _leg_set(v, 0.46, 0.40, 0.872, 0.128, 0.045, wood, wood)
    _mini_box(v, 0.30, 0.0, 0.513, 0.14, 0.36, 0.410, (0.48, 0.35, 0.20))
    return np.array(v, dtype="f4")


def build_table_mesh():
    v = []
    wood = (0.58, 0.44, 0.28)
    _leg_set(v, 0.47, 0.47, 0.8, 0.2, 0.07, wood, (0.66, 0.50, 0.32))
    return np.array(v, dtype="f4")


def build_chair_mesh():
    v = []
    wood = (0.50, 0.36, 0.22)
    back_wood = (0.40, 0.28, 0.17)
    top = _leg_set(v, 0.38, 0.38, 0.394, 0.106, 0.06, wood, wood)
    _mini_box(v, -0.36, 0.0, top + 0.25, 0.065, 0.34, 0.25, back_wood)
    return np.array(v, dtype="f4")


def build_gurney_mesh():
    v = []
    metal = (0.62, 0.63, 0.66)
    pad = (0.75, 0.76, 0.78)
    tire = (0.10, 0.10, 0.11)
    hx, hy, leg_th = 0.46, 0.44, 0.05
    top = _leg_set(v, hx, hy, 0.766, 0.234, leg_th, metal, pad)
    for sy in (-1, 1):
        _mini_box(v, 0.0, sy * 0.42, top + 0.055, 0.44, 0.025, 0.06, metal)

    lx, ly = hx - leg_th, hy - leg_th
    rail_z = 0.36
    for sy in (-1, 1):
        _mini_box(v, 0.0, sy * ly, rail_z, lx, 0.02, 0.02, metal)
    for sx in (-1, 1):
        _mini_box(v, sx * lx, 0.0, rail_z, 0.02, ly, 0.02, metal)

    for sx in (-1, 1):
        for sy in (-1, 1):
            _mini_box(v, sx * lx, sy * ly, 0.03, 0.07, 0.032, 0.03, tire)

    handle_z = 1.25
    for sx in (-1, 1):
        _mini_box(v, sx * hx * 0.7, hy + 0.02, (top + handle_z) / 2, 0.03, 0.014, (handle_z - top) / 2, metal)
    _mini_box(v, 0.0, hy + 0.04, handle_z, hx * 0.7, 0.03, 0.03, metal)
    return np.array(v, dtype="f4")


def build_shelf_mesh():
    v = []
    frame = (0.42, 0.32, 0.20)
    board = (0.55, 0.42, 0.26)
    panel_tint = (0.30, 0.22, 0.14)
    x0, x1 = -0.5, 0.5
    side_inset = 0.10
    py1, py0 = 0.48 - side_inset, -0.48 + side_inset
    _quad(v, (x0, py1, 0), (x1, py1, 0), (x1, py1, 1), (x0, py1, 1), (0, 1, 0), panel_tint)
    _quad(v, (x1, py0, 0), (x0, py0, 0), (x0, py0, 1), (x1, py0, 1), (0, -1, 0), panel_tint)
    post_hw, post_hy = 0.075, (side_inset + 0.015) / 2
    for px in (x1 - 0.09, x0 + 0.09):
        _mini_box(v, px, 0.48 - post_hy, 0.5, post_hw, post_hy, 0.5, frame)
        _mini_box(v, px, -0.48 + post_hy, 0.5, post_hw, post_hy, 0.5, frame)
    for bz in (0.06, 0.40, 0.74):
        _mini_box(v, 0.0, 0.0, bz, 0.48, py1 + 0.02, 0.025, board)
    return np.array(v, dtype="f4")


def build_cabinet_mesh():
    v = []
    x0, x1, y0, y1, z0, z1 = -0.5, 0.5, -0.5, 0.5, 0.0, 1.0
    w = (1, 1, 1)
    frame = (0.40, 0.40, 0.44)
    knob = (0.75, 0.72, 0.60)
    _quad(v, (x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (0, 1, 0), w)
    _quad(v, (x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1), (0, -1, 0), w)
    _quad(v, (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (0, 0, 1), w)

    inset = 0.06
    fx = x1 - inset
    fx_back = fx - 0.02
    _quad(v, (fx_back, y0, z0), (fx_back, y1, z0), (fx_back, y1, z1), (fx_back, y0, z1), (1, 0, 0), frame)

    margin, mid_gap = 0.03, 0.03
    door_z0, door_z1 = z0 + margin, z1 - margin
    for dy0, dy1, knob_y in ((y0 + margin, -mid_gap, -mid_gap - 0.05), (mid_gap, y1 - margin, mid_gap + 0.05)):
        cx, sx = (fx + x1) / 2, (x1 - fx) / 2
        cy, sy = (dy0 + dy1) / 2, (dy1 - dy0) / 2
        cz, sz = (door_z0 + door_z1) / 2, (door_z1 - door_z0) / 2
        _mini_box(v, cx, cy, cz, sx, sy, sz, w)
        _mini_box(v, x1 + 0.025, knob_y, (door_z0 + door_z1) / 2, 0.02, 0.02, 0.05, knob)
    return np.array(v, dtype="f4")


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
    rows, cols = 3, 3
    row_h, col_w = (wz1 - wz0) / rows, (wy1 - wy0) / cols
    for r in range(rows):
        for c in range(cols):
            cy = wy0 + col_w * (c + 0.5)
            cz = wz0 + row_h * (r + 0.5)
            _mini_box(v, 0.38, cy, cz, 0.02, col_w * 0.36, row_h * 0.36,
                      products[(r * cols + c) % len(products)])
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
    v = []
    x0, x1, y0, y1 = -0.5, 0.5, -0.42, 0.42
    rim = (0.72, 0.72, 0.75)
    basin = (0.46, 0.46, 0.50)
    faucet = (0.62, 0.62, 0.66)
    z0, z1 = 0.0, 1.0
    _quad(v, (x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (0, 1, 0), rim)
    _quad(v, (x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1), (0, -1, 0), rim)
    _quad(v, (x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1), (1, 0, 0), rim)
    bx0, bx1, by0, by1 = -0.22, 0.18, -0.24, 0.24
    _quad(v, (x0, y0, z1), (bx0, y0, z1), (bx0, y1, z1), (x0, y1, z1), (0, 0, 1), rim)
    _quad(v, (bx1, y0, z1), (x1, y0, z1), (x1, y1, z1), (bx1, y1, z1), (0, 0, 1), rim)
    _quad(v, (bx0, y0, z1), (bx1, y0, z1), (bx1, by0, z1), (bx0, by0, z1), (0, 0, 1), rim)
    _quad(v, (bx0, by1, z1), (bx1, by1, z1), (bx1, y1, z1), (bx0, y1, z1), (0, 0, 1), rim)
    bz = z1 - 0.16
    _quad(v, (bx0, by0, bz), (bx1, by0, bz), (bx1, by1, bz), (bx0, by1, bz), (0, 0, 1), basin)
    _quad(v, (bx1, by0, bz), (bx1, by0, z1), (bx1, by1, z1), (bx1, by1, bz), (1, 0, 0), basin)
    _quad(v, (bx0, by1, bz), (bx0, by1, z1), (bx0, by0, z1), (bx0, by0, bz), (-1, 0, 0), basin)
    _quad(v, (bx1, by1, bz), (bx1, by1, z1), (bx0, by1, z1), (bx0, by1, bz), (0, 1, 0), basin)
    _quad(v, (bx0, by0, bz), (bx0, by0, z1), (bx1, by0, z1), (bx1, by0, bz), (0, -1, 0), basin)
    rust = (0.34, 0.22, 0.13)
    _quad(v, (bx0 + 0.02, by0 + 0.02, bz + 0.02), (bx0 + 0.15, by0 + 0.03, bz + 0.02),
          (bx0 + 0.13, by0 + 0.16, bz + 0.02), (bx0 + 0.03, by0 + 0.14, bz + 0.02), (0, 0, 1), rust)
    _quad(v, (bx0 + 0.04, by0, bz + 0.10), (bx0 + 0.09, by0, bz + 0.10),
          (bx0 + 0.07, by0, z1 - 0.01), (bx0 + 0.05, by0, z1 - 0.01), (0, -1, 0), rust)
    _mini_box(v, -0.32, 0.0, 1.09, 0.035, 0.035, 0.09, faucet)
    _mini_box(v, -0.185, 0.0, 1.19, 0.14, 0.028, 0.028, faucet)
    _mini_box(v, -0.05, 0.0, 1.11, 0.026, 0.026, 0.075, faucet)
    for sy in (-0.09, 0.09):
        _mini_box(v, -0.30, sy, 1.02, 0.025, 0.025, 0.02, faucet)
    return np.array(v, dtype="f4")


def build_trash_can_mesh():
    v = []
    body = (0.40, 0.42, 0.40)
    rim = (0.58, 0.60, 0.58)
    bx, by = 0.42, 0.42
    tx, ty = 0.30, 0.30
    rx, ry = 0.36, 0.36
    z1, z2 = 0.88, 1.0
    corners_b = [(bx, by), (-bx, by), (-bx, -by), (bx, -by)]
    corners_t = [(tx, ty), (-tx, ty), (-tx, -ty), (tx, -ty)]
    corners_r = [(rx, ry), (-rx, ry), (-rx, -ry), (rx, -ry)]
    normals = [(0, 1, 0), (-1, 0, 0), (0, -1, 0), (1, 0, 0)]
    for i in range(4):
        b0, b1 = corners_b[i], corners_b[(i + 1) % 4]
        t0, t1 = corners_t[i], corners_t[(i + 1) % 4]
        _quad(v, (b0[0], b0[1], 0), (b1[0], b1[1], 0), (t1[0], t1[1], z1), (t0[0], t0[1], z1), normals[i], body)
        r0, r1 = corners_r[i], corners_r[(i + 1) % 4]
        _quad(v, (t0[0], t0[1], z1), (t1[0], t1[1], z1), (r1[0], r1[1], z2), (r0[0], r0[1], z2), normals[i], rim)
    return np.array(v, dtype="f4")


def build_crate_mesh():
    v = []
    wood = (0.60, 0.44, 0.26)
    plank = (0.44, 0.32, 0.18)
    x0, x1, y0, y1, z0, z1 = -0.5, 0.5, -0.5, 0.5, 0.0, 1.0
    _quad(v, (x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1), (1, 0, 0), wood)
    _quad(v, (x0, y1, z0), (x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (-1, 0, 0), wood)
    _quad(v, (x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (0, 1, 0), wood)
    _quad(v, (x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1), (0, -1, 0), wood)
    _quad(v, (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (0, 0, 1), wood)
    for bz in (0.30, 0.68):
        for cx, cy, sx, sy in ((0.505, 0, 0.01, 0.52), (-0.505, 0, 0.01, 0.52), (0, 0.505, 0.52, 0.01), (0, -0.505, 0.52, 0.01)):
            _mini_box(v, cx, cy, bz, sx, sy, 0.025, plank)
    return np.array(v, dtype="f4")


def build_barrel_mesh():
    v = []
    metal = (0.40, 0.40, 0.44)
    band = (0.58, 0.56, 0.38)
    n = 8
    r = 0.46
    z0, z1 = 0.0, 1.0
    pts = [(r * math.cos(math.tau * i / n), r * math.sin(math.tau * i / n)) for i in range(n)]
    for i in range(n):
        xa, ya = pts[i]
        xb, yb = pts[(i + 1) % n]
        _quad(v, (xb, yb, z0), (xa, ya, z0), (xa, ya, z1), (xb, yb, z1), (xa + xb, ya + yb, 0), metal)
    cx, cy = 0.0, 0.0
    for i in range(n):
        xa, ya = pts[i]
        xb, yb = pts[(i + 1) % n]
        _quad(v, (xa, ya, z1), (xb, yb, z1), (cx, cy, z1), (cx, cy, z1), (0, 0, 1), metal)
    _ring_of_boxes(v, 0, 0, 0.22, r + 0.02, n, 0.045, band)
    _ring_of_boxes(v, 0, 0, 0.78, r + 0.02, n, 0.045, band)
    return np.array(v, dtype="f4")


def build_pipes_mesh(open_neg=False, open_pos=False):
    v = []
    metal = (0.55, 0.56, 0.60)
    joint = (0.30, 0.30, 0.33)
    bracket = (0.42, 0.42, 0.47)
    offsets = [(-0.38, 0.72), (-0.42, 0.42), (-0.38, 0.14)]
    y_neg = -0.625 if open_neg else -0.48
    y_pos = 0.625 if open_pos else 0.48
    cy = (y_neg + y_pos) / 2
    half_len = (y_pos - y_neg) / 2
    for dx, z in offsets:
        _mini_box(v, dx, cy, z, 0.06, half_len, 0.055, metal)
    for dx, z in offsets:
        if not open_neg:
            _mini_box(v, dx, -0.44, z, 0.075, 0.03, 0.075, joint)
        if not open_pos:
            _mini_box(v, dx, 0.44, z, 0.075, 0.03, 0.075, joint)
    if open_neg and open_pos:
        for dx, z in offsets:
            _mini_box(v, dx, 0.0, z, 0.078, 0.018, 0.078, bracket)
    return np.array(v, dtype="f4")


def build_fuse_box_mesh():
    v = []
    x0, x1, y0, y1, z0, z1 = -0.5, 0.5, -0.5, 0.5, 0.0, 1.0
    metal = (1, 1, 1)
    dark = (0.35, 0.34, 0.30)
    switch = (0.75, 0.68, 0.30)
    _quad(v, (x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (0, 1, 0), metal)
    _quad(v, (x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1), (0, -1, 0), metal)
    _quad(v, (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (0, 0, 1), metal)
    bx0, bx1, bz0, bz1 = y0 + 0.06, y1 - 0.06, z0 + 0.08, z1 - 0.10
    door_x = x1 - 0.05

    def _face(ya, yb, za, zb, col):
        _quad(v, (x1, ya, za), (x1, yb, za), (x1, yb, zb), (x1, ya, zb), (1, 0, 0), col)

    _face(y0, y1, z0, bz0, metal)
    _face(y0, y1, bz1, z1, metal)
    _face(y0, bx0, bz0, bz1, metal)
    _face(bx1, y1, bz0, bz1, metal)
    _quad(v, (x1, bx0, bz1), (x1, bx1, bz1), (door_x, bx1, bz1), (door_x, bx0, bz1), (0, 0, -1), metal)
    _quad(v, (x1, bx1, bz0), (x1, bx0, bz0), (door_x, bx0, bz0), (door_x, bx1, bz0), (0, 0, 1), metal)
    _quad(v, (x1, bx0, bz1), (x1, bx0, bz0), (door_x, bx0, bz0), (door_x, bx0, bz1), (0, 1, 0), metal)
    _quad(v, (x1, bx1, bz0), (x1, bx1, bz1), (door_x, bx1, bz1), (door_x, bx1, bz0), (0, -1, 0), metal)
    _quad(v, (door_x, bx0, bz0), (door_x, bx1, bz0), (door_x, bx1, bz1), (door_x, bx0, bz1), (1, 0, 0), dark)
    for hz in (bz0 + 0.08, bz1 - 0.08):
        _mini_box(v, x1 - 0.005, bx0 - 0.005, hz, 0.014, 0.018, 0.035, metal)
    _mini_box(v, door_x + 0.005, bx1 - 0.07, (bz0 + bz1) / 2, 0.025, 0.025, 0.05, switch)
    for i, sy in enumerate((-0.22, 0.0, 0.22)):
        _mini_box(v, 0.56, sy, 0.35 + (i % 2) * 0.15, 0.03, 0.05, 0.10, switch)
    return np.array(v, dtype="f4")


def build_valve_panel_mesh():
    v = []
    x0, x1, y0, y1, z0, z1 = -0.5, 0.5, -0.5, 0.5, 0.0, 1.0
    metal = (1, 1, 1)
    pipe = (0.40, 0.40, 0.44)
    _quad(v, (x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (0, 1, 0), metal)
    _quad(v, (x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1), (0, -1, 0), metal)
    _quad(v, (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (0, 0, 1), metal)
    _quad(v, (x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1), (1, 0, 0), metal)
    wheel = (0.58, 0.22, 0.17)
    _mini_box(v, 0.56, 0.0, 0.55, 0.05, 0.04, 0.16, pipe)
    hub_x, hub_z, rim_r = 0.60, 0.72, 0.14
    _mini_box(v, hub_x, 0.0, hub_z, 0.045, 0.055, 0.055, wheel)
    _mini_box(v, hub_x, 0.0, hub_z, 0.025, rim_r, 0.022, wheel)
    _mini_box(v, hub_x, 0.0, hub_z, 0.025, 0.022, rim_r, wheel)
    for i in range(8):
        ang = math.tau * i / 8
        _mini_box(v, hub_x, rim_r * math.cos(ang), hub_z + rim_r * math.sin(ang), 0.022, 0.03, 0.03, wheel)
    return np.array(v, dtype="f4")


def build_shed_lock_mesh():
    v = []
    x0, x1, y0, y1, z0, z1 = -0.5, 0.5, -0.5, 0.5, 0.0, 1.0
    plate = (0.5, 0.46, 0.40)
    lock_body = (0.25, 0.24, 0.26)
    shackle = (0.55, 0.55, 0.58)
    _quad(v, (x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (0, 1, 0), plate)
    _quad(v, (x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1), (0, -1, 0), plate)
    _quad(v, (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (0, 0, 1), plate)
    _quad(v, (x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1), (1, 0, 0), plate)
    _quad(v, (x0, y1, z0), (x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (-1, 0, 0), plate)
    _mini_box(v, 0.55, 0.0, 0.42, 0.06, 0.10, 0.14, lock_body)
    for sy in (-0.06, 0.06):
        _mini_box(v, 0.55, sy, 0.62, 0.04, 0.02, 0.10, shackle)
    _mini_box(v, 0.55, 0.0, 0.70, 0.04, 0.10, 0.02, shackle)
    return np.array(v, dtype="f4")


def build_elevator_mesh():
    v = []
    x0, x1, y0, y1, z0, z1 = -0.5, 0.5, -0.5, 0.5, 0.0, 1.0
    metal = (1, 1, 1)
    seam = (0.40, 0.40, 0.44)
    button = (0.70, 0.55, 0.20)
    _quad(v, (x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (0, 1, 0), metal)
    _quad(v, (x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1), (0, -1, 0), metal)
    _quad(v, (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (0, 0, 1), metal)
    _quad(v, (x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1), (1, 0, 0), metal)
    _mini_box(v, 0.505, 0.0, 0.5, 0.005, 0.012, 0.46, seam)
    _mini_box(v, 0.53, -0.30, 0.55, 0.03, 0.03, 0.03, button)
    return np.array(v, dtype="f4")


def build_hatch_mesh():
    v = []
    x0, x1, y0, y1, z0, z1 = -0.5, 0.5, -0.5, 0.5, 0.0, 1.0
    metal = (1, 1, 1)
    rim = (0.55, 0.50, 0.42)
    wheel = (0.60, 0.55, 0.30)
    _quad(v, (x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1), (1, 0, 0), metal)
    _quad(v, (x0, y1, z0), (x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (-1, 0, 0), metal)
    _quad(v, (x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (0, 1, 0), metal)
    _quad(v, (x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1), (0, -1, 0), metal)
    bx0, bx1, by0, by1 = x0 + 0.07, x1 - 0.07, y0 + 0.07, y1 - 0.07

    def _face(xa, xb, ya, yb, col):
        _quad(v, (xa, ya, z0), (xb, ya, z0), (xb, yb, z0), (xa, yb, z0), (0, 0, -1), col)

    _face(x0, x1, y0, by0, metal)
    _face(x0, x1, by1, y1, metal)
    _face(x0, bx0, by0, by1, metal)
    _face(bx1, x1, by0, by1, metal)
    _face(bx0, bx1, by0, by1, rim)
    for i in range(6):
        ang = math.tau * i / 6
        _mini_box(v, 0.16 * math.cos(ang), 0.16 * math.sin(ang), z0 - 0.025, 0.035, 0.035, 0.025, wheel)
    _mini_box(v, 0.0, 0.0, z0 - 0.025, 0.03, 0.03, 0.025, wheel)
    return np.array(v, dtype="f4")


def build_fence_gap_mesh():
    v = []
    post = (0.45, 0.40, 0.30)
    wire = (0.55, 0.50, 0.42)
    cut_wire = (0.35, 0.32, 0.28)

    for sy in (-0.46, 0.46):
        _mini_box(v, 0.0, sy, 0.5, 0.11, 0.045, 0.5, post)

    cols = (-0.32, -0.16, 0.0, 0.16, 0.32)
    for band_z0, band_z1 in ((0.60, 0.92), (0.06, 0.38)):
        for cy in cols:
            _mini_box(v, 0.0, cy, (band_z0 + band_z1) / 2, 0.015, 0.014, (band_z1 - band_z0) / 2, wire)
        for cz in (band_z0, (band_z0 + band_z1) / 2, band_z1):
            _mini_box(v, 0.0, 0.0, cz, 0.015, 0.42, 0.014, wire)

    for cy, cz, horiz in ((-0.28, 0.50, False), (0.02, 0.46, True), (0.24, 0.52, False), (-0.08, 0.40, True)):
        if horiz:
            _mini_box(v, 0.0, cy, cz, 0.015, 0.08, 0.014, cut_wire)
        else:
            _mini_box(v, 0.0, cy, cz, 0.015, 0.014, 0.08, cut_wire)
    return np.array(v, dtype="f4")


def build_battery_mesh():
    v = []
    body = (0.35, 0.75, 0.45)
    cap = (0.70, 0.70, 0.65)
    x0, x1, y0, y1, z0, z1 = -0.4, 0.4, -0.4, 0.4, 0.0, 0.85
    _quad(v, (x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1), (1, 0, 0), body)
    _quad(v, (x0, y1, z0), (x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (-1, 0, 0), body)
    _quad(v, (x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (0, 1, 0), body)
    _quad(v, (x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1), (0, -1, 0), body)
    _quad(v, (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (0, 0, 1), body)
    _mini_box(v, 0.0, 0.0, z1 + 0.05, 0.30, 0.30, 0.05, cap)
    _mini_box(v, 0.0, 0.0, z1 + 0.14, 0.06, 0.06, 0.04, cap)
    return np.array(v, dtype="f4")


def build_fuse_mesh():
    v = []
    glass = (0.90, 0.55, 0.25)
    cap = (0.65, 0.65, 0.68)
    n = 8
    r = 0.30
    z0, z1 = 0.10, 0.75
    pts = [(r * math.cos(math.tau * i / n), r * math.sin(math.tau * i / n)) for i in range(n)]
    for i in range(n):
        xa, ya = pts[i]
        xb, yb = pts[(i + 1) % n]
        _quad(v, (xb, yb, z0), (xa, ya, z0), (xa, ya, z1), (xb, yb, z1), (xa + xb, ya + yb, 0), glass)
    _mini_box(v, 0, 0, z0 - 0.05, 0.32, 0.32, 0.05, cap, skip_bottom=True)
    _mini_box(v, 0, 0, z1 + 0.05, 0.32, 0.32, 0.05, cap)
    return np.array(v, dtype="f4")


def build_sanity_pill_mesh():
    v = []
    body = (0.82, 0.76, 0.93)
    rim = (0.60, 0.52, 0.78)
    score = (0.44, 0.38, 0.58)
    n = 8
    r = 0.34
    hz = 0.15
    pts = [(r * math.cos(math.tau * i / n), r * math.sin(math.tau * i / n)) for i in range(n)]
    for i in range(n):
        xa, ya = pts[i]
        xb, yb = pts[(i + 1) % n]
        nx, ny = (xa + xb) / 2, (ya + yb) / 2
        _quad(v, (xb, yb, 0.0), (xa, ya, 0.0), (xa, ya, hz), (xb, yb, hz), (nx, ny, 0), rim)
    for i in range(n):
        xa, ya = pts[i]
        xb, yb = pts[(i + 1) % n]
        _quad(v, (xa, ya, hz), (xb, yb, hz), (0.0, 0.0, hz), (0.0, 0.0, hz), (0, 0, 1), body)
        _quad(v, (xb, yb, 0.0), (xa, ya, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0, 0, -1), body)
    _quad(v, (-r, -0.025, hz + 0.002), (r, -0.025, hz + 0.002),
          (r, 0.025, hz + 0.002), (-r, 0.025, hz + 0.002), (0, 0, 1), score)
    return np.array(v, dtype="f4")


def build_valve_key_mesh():
    v = []
    metal = (0.70, 0.75, 0.78)
    _mini_box(v, -0.08, 0.0, 0.18, 0.34, 0.08, 0.06, metal)
    _mini_box(v, 0.22, 0.0, 0.18, 0.08, 0.205, 0.06, metal)
    _mini_box(v, 0.30, 0.16, 0.18, 0.09, 0.045, 0.06, metal)
    _mini_box(v, 0.30, -0.16, 0.18, 0.09, 0.045, 0.06, metal)
    return np.array(v, dtype="f4")


def build_key_mesh():
    v = []
    metal = (0.85, 0.72, 0.30)
    _mini_box(v, 0.10, 0.0, 0.5, 0.34, 0.045, 0.045, metal)
    _ring_of_boxes(v, -0.32, 0.0, 0.5, 0.13, 8, 0.035, metal)
    _mini_box(v, 0.40, 0.10, 0.5, 0.05, 0.05, 0.09, metal)
    _mini_box(v, 0.28, 0.10, 0.5, 0.05, 0.05, 0.06, metal)
    return np.array(v, dtype="f4")


def build_cutters_mesh():
    v = []
    metal = (0.60, 0.60, 0.64)
    handle = (0.75, 0.25, 0.20)
    for s in (-1, 1):
        _mini_box(v, 0.10, s * 0.02, 0.5, 0.30, 0.03, 0.03, metal)
        _mini_box(v, -0.28, s * 0.10, 0.5, 0.16, 0.05, 0.04, handle)
    return np.array(v, dtype="f4")


def build_clutter_papers_mesh():
    v = []
    paper = (1, 1, 1)
    _mini_box(v, -0.06, 0.04, 0.10, 0.34, 0.26, 0.10, paper, skip_bottom=True)
    _mini_box(v, 0.08, -0.05, 0.24, 0.28, 0.20, 0.06, paper)
    return np.array(v, dtype="f4")


def build_clutter_bottle_mesh():
    v = []
    glass = (0.30, 0.48, 0.34)
    cap = (0.18, 0.17, 0.16)
    n = 8
    r = 0.24
    z0, z1 = 0.0, 0.78
    pts = [(r * math.cos(math.tau * i / n), r * math.sin(math.tau * i / n)) for i in range(n)]
    for i in range(n):
        xa, ya = pts[i]
        xb, yb = pts[(i + 1) % n]
        _quad(v, (xb, yb, z0), (xa, ya, z0), (xa, ya, z1), (xb, yb, z1), (xa + xb, ya + yb, 0), glass)
    cx, cy = 0.0, 0.0
    for i in range(n):
        xa, ya = pts[i]
        xb, yb = pts[(i + 1) % n]
        _quad(v, (xa, ya, z1), (xb, yb, z1), (cx, cy, z1), (cx, cy, z1), (0, 0, 1), glass)
    _mini_box(v, 0, 0, z1 + 0.08, 0.10, 0.10, 0.06, cap)
    return np.array(v, dtype="f4")


def build_clutter_junk_mesh():
    v = []
    c1 = (0.50, 0.46, 0.40)
    c2 = (0.38, 0.34, 0.28)
    chunks = [
        (0.0, 0.0, 0.10, 0.28, 0.26, 0.10, c1),
        (0.15, 0.10, 0.06, 0.14, 0.12, 0.06, c2),
        (-0.14, -0.08, 0.05, 0.12, 0.14, 0.05, c1),
        (0.02, -0.16, 0.04, 0.10, 0.10, 0.04, c2),
    ]
    for cx, cy, cz, sx, sy, sz, color in chunks:
        _mini_box(v, cx, cy, cz, sx, sy, sz, color, skip_bottom=True)
    return np.array(v, dtype="f4")


def build_lamp_desk_mesh():
    v = []
    base = (0.22, 0.21, 0.19)
    pole = (0.32, 0.30, 0.27)
    shade = (0.80, 0.72, 0.50)
    bulb = (1.0, 0.92, 0.65)
    _mini_box(v, 0, 0, 0.05, 0.26, 0.26, 0.05, base, skip_bottom=True)
    _mini_box(v, 0, 0, 0.36, 0.045, 0.045, 0.28, pole)
    _mini_box(v, 0, 0, 0.70, 0.22, 0.22, 0.12, shade)
    _mini_box(v, 0, 0, 0.63, 0.11, 0.11, 0.025, bulb)
    return np.array(v, dtype="f4")


def build_lamp_desk_broken_mesh():
    v = []
    base = (0.22, 0.21, 0.19)
    pole = (0.30, 0.28, 0.25)
    shade = (0.45, 0.40, 0.30)
    dead_bulb = (0.08, 0.07, 0.07)
    _mini_box(v, 0, 0, 0.05, 0.26, 0.26, 0.05, base, skip_bottom=True)
    _mini_box(v, 0.05, 0.02, 0.34, 0.045, 0.045, 0.26, pole)
    _mini_box(v, 0.14, 0.05, 0.60, 0.22, 0.22, 0.12, shade)
    _mini_box(v, 0.14, 0.05, 0.55, 0.10, 0.10, 0.02, dead_bulb)
    return np.array(v, dtype="f4")


def build_sign_exit_mesh():
    v = []
    frame = (0.14, 0.13, 0.12)
    glow = (0.28, 1.0, 0.46)
    _mini_box(v, 0, 0, 0.5, 0.5, 0.42, 0.45, frame, skip_back=True)
    _mini_box(v, 0.15, 0, 0.5, 0.40, 0.32, 0.34, glow)
    return np.array(v, dtype="f4")


def build_wall_sconce_mesh():
    v = []
    plate = (0.24, 0.22, 0.19)
    cup = (0.30, 0.27, 0.22)
    bulb = (1.0, 0.90, 0.62)
    _mini_box(v, -0.41, 0, 0.5, 0.09, 0.34, 0.34, plate)
    _mini_box(v, -0.24, 0, 0.5, 0.10, 0.20, 0.20, cup)
    _mini_box(v, -0.08, 0, 0.5, 0.07, 0.09, 0.09, bulb)
    return np.array(v, dtype="f4")


def build_wall_sconce_broken_mesh():
    v = []
    plate = (0.20, 0.18, 0.16)
    cup = (0.24, 0.22, 0.18)
    dead_bulb = (0.10, 0.09, 0.09)
    _mini_box(v, -0.41, 0, 0.5, 0.09, 0.34, 0.34, plate)
    _mini_box(v, -0.24, 0, 0.5, 0.10, 0.20, 0.20, cup)
    _mini_box(v, -0.10, 0.03, 0.46, 0.05, 0.06, 0.06, dead_bulb)
    return np.array(v, dtype="f4")


def build_monitor_mesh():
    v = []
    case = (0.58, 0.55, 0.48)
    well = (0.10, 0.10, 0.11)
    glass = (0.35, 0.60, 0.95)
    stand = (0.20, 0.19, 0.18)

    _mini_box(v, 0.0, 0.0, 0.05, 0.16, 0.12, 0.05, stand, skip_bottom=True)
    _mini_box(v, 0.0, 0.0, 0.14, 0.06, 0.05, 0.04, stand)

    _mini_box(v, -0.06, 0.0, 0.58, 0.36, 0.42, 0.42, case)
    _mini_box(v, 0.28, 0.0, 0.55, 0.12, 0.34, 0.34, case)
    _mini_box(v, 0.38, 0.0, 0.55, 0.03, 0.27, 0.27, well)
    _mini_box(v, 0.41, 0.0, 0.55, 0.02, 0.22, 0.22, glass)
    _mini_box(v, 0.40, -0.08, 0.24, 0.015, 0.02, 0.02, well)
    _mini_box(v, 0.40, 0.08, 0.24, 0.015, 0.02, 0.02, well)
    return np.array(v, dtype="f4")


def build_monitor_broken_mesh():
    v = []
    case = (0.42, 0.40, 0.36)
    well = (0.08, 0.08, 0.09)
    glass = (0.04, 0.04, 0.05)
    stand = (0.20, 0.19, 0.18)
    crack = (0.55, 0.58, 0.62)

    _mini_box(v, 0.0, 0.0, 0.05, 0.16, 0.12, 0.05, stand, skip_bottom=True)
    _mini_box(v, 0.0, 0.0, 0.14, 0.06, 0.05, 0.04, stand)

    _mini_box(v, -0.06, 0.0, 0.58, 0.36, 0.42, 0.42, case)
    _mini_box(v, 0.28, 0.0, 0.55, 0.12, 0.34, 0.34, case)
    _mini_box(v, 0.38, 0.0, 0.55, 0.03, 0.27, 0.27, well)
    _mini_box(v, 0.41, 0.0, 0.55, 0.02, 0.22, 0.22, glass)
    _mini_box(v, 0.415, -0.10, 0.62, 0.006, 0.16, 0.006, crack)
    _mini_box(v, 0.415, 0.06, 0.48, 0.006, 0.14, 0.006, crack)
    _mini_box(v, 0.40, -0.08, 0.24, 0.015, 0.02, 0.02, well)
    _mini_box(v, 0.40, 0.08, 0.24, 0.015, 0.02, 0.02, well)
    return np.array(v, dtype="f4")


def build_bush_mesh():
    v = []
    low = (0.15, 0.29, 0.12)
    mid = (0.18, 0.34, 0.15)
    high = (0.22, 0.39, 0.17)
    clumps = (
        (0.0, 0.02, 0.30, 0.44, 0.42, 0.30, low),
        (0.20, -0.16, 0.52, 0.30, 0.32, 0.26, mid),
        (-0.22, 0.14, 0.58, 0.27, 0.29, 0.25, mid),
        (-0.02, -0.06, 0.80, 0.20, 0.21, 0.18, high),
    )
    for cx, cy, cz, sx, sy, sz, color in clumps:
        _mini_box(v, cx, cy, cz, sx, sy, sz, color, skip_bottom=True)
    return np.array(v, dtype="f4")


def build_rock_mesh():
    v = []
    c1 = (0.5, 0.48, 0.44)
    c2 = (0.40, 0.38, 0.35)
    chunks = [
        (0.0, 0.0, 0.16, 0.30, 0.28, 0.16, c1),
        (0.18, 0.10, 0.10, 0.16, 0.14, 0.10, c2),
        (-0.16, -0.08, 0.08, 0.18, 0.15, 0.08, c1),
        (0.02, -0.18, 0.06, 0.14, 0.12, 0.06, c2),
    ]
    for cx, cy, cz, sx, sy, sz, color in chunks:
        _mini_box(v, cx, cy, cz, sx, sy, sz, color, skip_bottom=True)
    return np.array(v, dtype="f4")


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


def build_shadow_mesh(segments=8):
    v = []
    color = (0.05, 0.045, 0.05)
    pts = [(0.5 * math.cos(math.tau * i / segments), 0.5 * math.sin(math.tau * i / segments))
           for i in range(segments)]
    for i in range(segments):
        xa, ya = pts[i]
        xb, yb = pts[(i + 1) % segments]
        _quad(v, (xa, ya, 0), (xb, yb, 0), (0, 0, 0), (0, 0, 0), (0, 0, 1), color)
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

_WINDOW_HALF_DEPTH = 0.07


def _build_window_cell(v_frame, v_glass, x, y, h, maze):
    cx, cy = x + 0.5, y + 0.5
    ew = maze.is_walkable_cell(x + 1, y) and maze.is_walkable_cell(x - 1, y)
    frame = tuple(c / 255.0 for c in S.WALL_BASE_COLORS[S.WALL_WINDOW])
    glass = (0.46, 0.60, 0.66)
    sill, lintel = h * 0.39, h * 0.87
    margin = 0.07
    hd = _WINDOW_HALF_DEPTH
    if ew:
        a0, a1 = y + margin, y + 1 - margin
        for px, nx in ((cx - hd, -1), (cx + hd, 1)):
            n = (nx, 0, 0)
            _quad(v_frame, (px, y, 0), (px, y + 1, 0), (px, y + 1, sill), (px, y, sill), n, frame)
            _quad(v_frame, (px, y, lintel), (px, y + 1, lintel), (px, y + 1, h), (px, y, h), n, frame)
            _quad(v_frame, (px, y, sill), (px, a0, sill), (px, a0, lintel), (px, y, lintel), n, frame)
            _quad(v_frame, (px, a1, sill), (px, y + 1, sill), (px, y + 1, lintel), (px, a1, lintel), n, frame)
            _quad(v_glass, (px, a0, sill), (px, a1, sill), (px, a1, lintel), (px, a0, lintel), n, glass)
        pxa, pxb = cx - hd, cx + hd
        _quad(v_frame, (pxa, a0, lintel), (pxb, a0, lintel), (pxb, a1, lintel), (pxa, a1, lintel), (0, 0, -1), frame)
        _quad(v_frame, (pxa, a0, sill), (pxa, a1, sill), (pxb, a1, sill), (pxb, a0, sill), (0, 0, 1), frame)
        _quad(v_frame, (pxa, a0, sill), (pxa, a0, lintel), (pxb, a0, lintel), (pxb, a0, sill), (0, 1, 0), frame)
        _quad(v_frame, (pxa, a1, lintel), (pxa, a1, sill), (pxb, a1, sill), (pxb, a1, lintel), (0, -1, 0), frame)
    else:
        a0, a1 = x + margin, x + 1 - margin
        for py, ny in ((cy - hd, -1), (cy + hd, 1)):
            n = (0, ny, 0)
            _quad(v_frame, (x, py, 0), (x + 1, py, 0), (x + 1, py, sill), (x, py, sill), n, frame)
            _quad(v_frame, (x, py, lintel), (x + 1, py, lintel), (x + 1, py, h), (x, py, h), n, frame)
            _quad(v_frame, (x, py, sill), (a0, py, sill), (a0, py, lintel), (x, py, lintel), n, frame)
            _quad(v_frame, (a1, py, sill), (x + 1, py, sill), (x + 1, py, lintel), (a1, py, lintel), n, frame)
            _quad(v_glass, (a0, py, sill), (a1, py, sill), (a1, py, lintel), (a0, py, lintel), n, glass)
        pya, pyb = cy - hd, cy + hd
        _quad(v_frame, (a0, pya, lintel), (a1, pya, lintel), (a1, pyb, lintel), (a0, pyb, lintel), (0, 0, -1), frame)
        _quad(v_frame, (a0, pya, sill), (a0, pyb, sill), (a1, pyb, sill), (a1, pya, sill), (0, 0, 1), frame)
        _quad(v_frame, (a0, pya, sill), (a0, pya, lintel), (a0, pyb, lintel), (a0, pyb, sill), (1, 0, 0), frame)
        _quad(v_frame, (a1, pya, lintel), (a1, pya, sill), (a1, pyb, sill), (a1, pyb, lintel), (-1, 0, 0), frame)


def build_maze_walls_by_type(maze):
    buckets = {}
    for y in range(maze.h):
        for x in range(maze.w):
            tile = maze.grid[y][x]
            if tile == S.FLOOR:
                continue
            if tile == S.WALL_WINDOW:
                frame_v = buckets.setdefault(tile, [])
                glass_v = buckets.setdefault(WINDOW_GLASS_KEY, [])
                _build_window_cell(frame_v, glass_v, x, y, S.WALL_HEIGHT, maze)
                continue
            base = S.WALL_BASE_COLORS.get(tile, S.WALL_BASE_COLORS[S.WALL_CONCRETE])
            color = (base[0] / 255.0, base[1] / 255.0, base[2] / 255.0)
            h = S.WALL_HEIGHTS.get(tile, S.WALL_HEIGHT)
            v = buckets.setdefault(tile, [])
            if maze.is_see_through(x + 1, y):
                _quad(v, (x + 1, y, 0), (x + 1, y + 1, 0), (x + 1, y + 1, h), (x + 1, y, h), (1, 0, 0), color)
            if maze.is_see_through(x - 1, y):
                _quad(v, (x, y + 1, 0), (x, y, 0), (x, y, h), (x, y + 1, h), (-1, 0, 0), color)
            if maze.is_see_through(x, y + 1):
                _quad(v, (x + 1, y + 1, 0), (x, y + 1, 0), (x, y + 1, h), (x + 1, y + 1, h), (0, 1, 0), color)
            if maze.is_see_through(x, y - 1):
                _quad(v, (x, y, 0), (x + 1, y, 0), (x + 1, y, h), (x, y, h), (0, -1, 0), color)
    return {t: np.array(v, dtype="f4") for t, v in buckets.items() if v}


def build_wall_mask(maze):
    h, w = maze.h, maze.w
    mask = np.full((h, w), 255, dtype=np.uint8)
    for y in range(h):
        row = maze.grid[y]
        for x in range(w):
            if row[x] == S.FLOOR or row[x] == S.WALL_WINDOW:
                mask[y, x] = 0
    return mask


_NO_WINDOW_SEG = (-9999.0, -9999.0)


def build_window_segments(maze):
    segs = []
    for y in range(maze.h):
        row = maze.grid[y]
        for x in range(maze.w):
            if row[x] != S.WALL_WINDOW:
                continue
            ew = maze.is_walkable_cell(x + 1, y) and maze.is_walkable_cell(x - 1, y)
            if ew:
                segs.append(((x + 0.5, y), (x + 0.5, y + 1)))
            else:
                segs.append(((x, y + 0.5), (x + 1, y + 0.5)))
    return segs


def build_floor_mesh(maze):
    v = []
    w, h = maze.w, maze.h
    _quad(v, (0, 0, 0), (w, 0, 0), (w, h, 0), (0, h, 0), (0, 0, 1), (1, 1, 1))
    return np.array(v, dtype="f4")


def build_ceiling_mesh(maze):
    v = []
    w, h = maze.w, maze.h
    wh = S.WALL_HEIGHT
    _quad(v, (0, h, wh), (w, h, wh), (w, 0, wh), (0, 0, wh), (0, 0, -1), (1, 1, 1))
    return np.array(v, dtype="f4")


def _gray_to_rgb_bytes(arr):
    u8 = np.clip(arr * 255.0, 0, 255).astype(np.uint8)
    rgb = np.repeat(u8[:, :, None], 3, axis=2)
    return np.ascontiguousarray(rgb)


def _make_ceiling_texture(size=64, seed=23):
    tiles_per_side = 2
    panel = size // tiles_per_side
    rng = np.random.default_rng(seed)
    panel_brightness = rng.uniform(0.72, 1.0, (tiles_per_side, tiles_per_side))
    base = np.repeat(np.repeat(panel_brightness, panel, axis=0), panel, axis=1)
    x = np.arange(size)[:, None]
    y = np.arange(size)[None, :]
    seam = ((x % panel) < 2) | ((y % panel) < 2)
    base = np.where(seam, base * 0.35, base)
    noise = rng.random((size, size)) * 0.08
    cx, cy = rng.integers(0, size, 2)
    yy, xx = np.ogrid[:size, :size]
    r2 = (xx - cx) ** 2 + (yy - cy) ** 2
    stain = np.exp(-r2 / (2 * (size * 0.16) ** 2)) * -0.28
    return np.clip(base + noise - 0.04 + stain, 0.12, 1.1)


def _make_basement_floor_texture(size=64, seed=71):
    rng = np.random.default_rng(seed)
    base = rng.random((size, size)) * 0.30 + 0.55
    x = np.arange(size)[:, None]
    y = np.arange(size)[None, :]
    slab = ((x // 21 + y // 21) % 2) * 0.06
    base -= slab
    crack = np.zeros((size, size))
    for _ in range(4):
        cx = int(rng.integers(0, size))
        cy = int(rng.integers(0, size))
        length = int(rng.integers(size // 3, size))
        ang = rng.uniform(0, math.tau)
        px, py = float(cx), float(cy)
        for _ in range(length):
            px += math.cos(ang) + rng.uniform(-0.4, 0.4)
            py += math.sin(ang) + rng.uniform(-0.4, 0.4)
            ix, iy = int(px) % size, int(py) % size
            crack[iy, ix] -= 0.30
    stain = np.zeros((size, size))
    for _ in range(3):
        cx, cy = rng.integers(0, size, 2)
        yy, xx = np.ogrid[:size, :size]
        r2 = (xx - cx) ** 2 + (yy - cy) ** 2
        stain += np.exp(-r2 / (2 * (size * 0.12) ** 2)) * rng.uniform(-0.22, -0.05)
    return np.clip(base + crack + stain, 0.08, 1.1)


def _make_pipes_ceiling_texture(size=64, seed=73):
    rng = np.random.default_rng(seed)
    base = np.full((size, size), 0.30)
    y = np.arange(size)
    pipe_rows = [10, 11, 30, 31, 32, 50, 51]
    for r in pipe_rows:
        if r < size:
            base[r, :] = 0.62
            if r + 1 < size:
                base[r + 1, :] = 0.42
    noise = rng.random((size, size)) * 0.10
    rust = np.zeros((size, size))
    for _ in range(5):
        cx, cy = rng.integers(0, size, 2)
        yy, xx = np.ogrid[:size, :size]
        r2 = (xx - cx) ** 2 + ((yy - cy) * 2.2) ** 2
        rust += np.exp(-r2 / (2 * (size * 0.06) ** 2)) * rng.uniform(-0.16, -0.04)
    rivets = np.zeros((size, size))
    for r in pipe_rows:
        if r >= size:
            continue
        for cx in range(4, size, 9):
            yy, xx = np.ogrid[:size, :size]
            r2 = (xx - cx) ** 2 + (yy - r) ** 2
            rivets = np.where(r2 < 1.6, rivets - 0.18, rivets)
    return np.clip(base + noise + rust + rivets - 0.02, 0.08, 1.05)


def _make_wall_detail_texture(size=64, seed=7):
    rng = np.random.default_rng(seed)
    base = rng.random((size, size)) * 0.22 + 0.80
    grid = np.ones((size, size))
    grid[::16, :] *= 0.82
    grid[:, ::16] *= 0.82
    blotch = np.zeros((size, size))
    for _ in range(10):
        cx, cy = rng.integers(0, size, 2)
        yy, xx = np.ogrid[:size, :size]
        r2 = (xx - cx) ** 2 + (yy - cy) ** 2
        blotch += np.exp(-r2 / (2 * (size * 0.09) ** 2)) * rng.uniform(-0.18, 0.05)
    return np.clip(base * grid + blotch, 0.35, 1.15)


def _make_floor_texture(size=64):
    x = np.arange(size)[:, None]
    y = np.arange(size)[None, :]
    checker = ((x // 32 + y // 32) % 2)
    base = np.where(checker == 0, 0.88, 0.72)
    rng = np.random.default_rng(11)
    noise = rng.random((size, size)) * 0.14 + 0.88
    return np.clip(base * noise, 0.1, 1.2)


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


def _make_metal_texture(size=64, seed=41):
    rng = np.random.default_rng(seed)
    base = np.full((size, size), 0.88)
    brushed = rng.random((size, 1)) * 0.14 + 0.86
    base = base * brushed
    base[::16, :] *= 0.55
    base[:, ::16] *= 0.65
    for gy in range(8, size, 16):
        for gx in range(8, size, 16):
            yy, xx = np.ogrid[:size, :size]
            r2 = (xx - gx) ** 2 + (yy - gy) ** 2
            base = np.where(r2 < 2.6, base * 0.35, base)
    noise = rng.random((size, size)) * 0.07
    return np.clip(base + noise - 0.035, 0.25, 1.25)


def _make_tile_texture(size=64, seed=17):
    tile_size, grout = 16, 2
    x = np.arange(size)[:, None]
    y = np.arange(size)[None, :]
    grout_mask = ((x % tile_size) < grout) | ((y % tile_size) < grout)
    base = np.where(grout_mask, 0.42, 0.96)
    rng = np.random.default_rng(seed)
    noise = rng.random((size, size)) * 0.08 + 0.94
    return np.clip(base * noise, 0.2, 1.15)


def _make_blood_texture(size=64, seed=51):
    rng = np.random.default_rng(seed)
    base = rng.random((size, size)) * 0.25 + 0.78
    drips = np.zeros((size, size))
    for _ in range(6):
        cx = int(rng.integers(0, size))
        length = int(rng.integers(size // 3, size))
        y0 = int(rng.integers(0, size // 3))
        for yy in range(y0, min(size, y0 + length)):
            width = max(1, int(2 * (1 - (yy - y0) / length)))
            for dx in range(-width, width + 1):
                xx = cx + dx
                if 0 <= xx < size:
                    drips[yy, xx] -= 0.32 * (1 - abs(dx) / (width + 1))
    blotch = np.zeros((size, size))
    for _ in range(8):
        cx, cy = rng.integers(0, size, 2)
        yy, xx = np.ogrid[:size, :size]
        r2 = (xx - cx) ** 2 + (yy - cy) ** 2
        blotch += np.exp(-r2 / (2 * (size * 0.08) ** 2)) * rng.uniform(-0.2, 0.05)
    return np.clip(base + drips + blotch, 0.2, 1.1)


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


def _make_forest_texture(size=64, seed=83):
    rng = np.random.default_rng(seed)
    base = np.full((size, size), 0.16)
    y = np.arange(size)[:, None]
    trunk_x = sorted(rng.integers(0, size, size // 4))
    for tx in trunk_x:
        w = int(rng.integers(1, 5))
        shade = rng.uniform(0.20, 0.46)
        top = rng.uniform(0.0, 0.28) * size
        lo, hi = max(0, tx - w), min(size, tx + w)
        mask = y >= top
        base[:, lo:hi] = np.where(mask[:, :1], np.maximum(base[:, lo:hi], shade), base[:, lo:hi])
    canopy = rng.random((size, size)) * 0.10
    canopy_band = (np.arange(size)[:, None] < size * 0.55)
    base = base + np.where(canopy_band, canopy * 0.5, canopy)
    noise = rng.random((size, size)) * 0.06
    return np.clip(base + noise - 0.02, 0.04, 0.7)


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


def _make_yard_floor_texture(size=64, seed=89):
    rng = np.random.default_rng(seed)
    dirt = np.array([0.42, 0.34, 0.24])
    grass = np.array([0.30, 0.40, 0.20])
    mix = rng.random((size, size))
    for _ in range(14):
        cx, cy = rng.integers(0, size, 2)
        yy, xx = np.ogrid[:size, :size]
        r2 = (xx - cx) ** 2 + (yy - cy) ** 2
        mix += np.exp(-r2 / (2 * (size * rng.uniform(0.05, 0.14)) ** 2)) * rng.uniform(-0.4, 0.4)
    mix = np.clip(mix, 0.0, 1.0)
    rgb = dirt[None, None, :] * (1 - mix[:, :, None]) + grass[None, None, :] * mix[:, :, None]
    noise = (rng.random((size, size, 1)) - 0.5) * 0.08
    gray = np.clip((rgb + noise).mean(axis=2), 0.08, 1.05)
    return gray


def _make_paper_texture(size=64, seed=61):
    rng = np.random.default_rng(seed)
    row = np.arange(size)[:, None]
    line_mask = ((row % 7) == 0) & (row > 8)
    lines = 1.0 - line_mask.astype(np.float64) * 0.28
    base = np.full((size, size), 1.05) * lines
    cx, cy = rng.integers(0, size, 2)
    yy, xx = np.ogrid[:size, :size]
    r2 = (xx - cx) ** 2 + (yy - cy) ** 2
    stain = np.exp(-r2 / (2 * (size * 0.22) ** 2)) * -0.12
    noise = rng.random((size, size)) * 0.05
    return np.clip(base + stain + noise - 0.025, 0.5, 1.15)


class Renderer3D:
    def __init__(self, ctx, low_res=(320, 180), snap_res=180.0):
        self.ctx = ctx
        self.low_w, self.low_h = low_res
        self.snap_res = snap_res
        self._upscale_linear = False
        self.last_perf = {}
        self.max_shadow_lights = 2

        self.prog = ctx.program(vertex_shader=VERTEX_SHADER, fragment_shader=FRAGMENT_SHADER)
        self.quad_prog = ctx.program(vertex_shader=QUAD_VERTEX_SHADER, fragment_shader=QUAD_FRAGMENT_SHADER)
        self.prog["door_seg_a"].value = [self._NO_DOOR_SEG] * MAX_DOOR_SEGS
        self.prog["door_seg_b"].value = [self._NO_DOOR_SEG] * MAX_DOOR_SEGS
        self.prog["window_seg_a"].value = [_NO_WINDOW_SEG] * MAX_WINDOW_SEGS
        self.prog["window_seg_b"].value = [_NO_WINDOW_SEG] * MAX_WINDOW_SEGS

        box_data = build_box_mesh()
        self.box_vbo = ctx.buffer(box_data.tobytes())
        self.box_vao = ctx.vertex_array(
            self.prog, [(self.box_vbo, "3f 3f 2f 3f", "in_pos", "in_normal", "in_uv", "in_color")]
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

        tree_data = build_tree_mesh()
        self.tree_vbo = ctx.buffer(tree_data.tobytes())
        self.tree_vao = ctx.vertex_array(
            self.prog, [(self.tree_vbo, "3f 3f 2f 3f", "in_pos", "in_normal", "in_uv", "in_color")]
        )

        self._mesh_builders = {
            "bed": build_bed_mesh, "desk": build_desk_mesh, "table": build_table_mesh,
            "chair": build_chair_mesh, "gurney": build_gurney_mesh, "shelf": build_shelf_mesh,
            "cabinet": build_cabinet_mesh, "vending": build_vending_mesh, "sink": build_sink_mesh,
            "trash_can": build_trash_can_mesh, "crate": build_crate_mesh, "barrel": build_barrel_mesh,
            "pipes": build_pipes_mesh, "fuse_box": build_fuse_box_mesh, "valve_panel": build_valve_panel_mesh,
            "pipes_open_neg": lambda: build_pipes_mesh(open_neg=True),
            "pipes_open_pos": lambda: build_pipes_mesh(open_pos=True),
            "pipes_open_both": lambda: build_pipes_mesh(open_neg=True, open_pos=True),
            "shed_lock": build_shed_lock_mesh, "elevator": build_elevator_mesh, "hatch": build_hatch_mesh,
            "fence_gap": build_fence_gap_mesh, "battery": build_battery_mesh, "fuse": build_fuse_mesh,
            "valve_key": build_valve_key_mesh, "key": build_key_mesh, "cutters": build_cutters_mesh,
            "sanity_pill": build_sanity_pill_mesh,
            "bush": build_bush_mesh, "rock": build_rock_mesh, "portal": build_portal_mesh,
            "clutter_papers": build_clutter_papers_mesh, "clutter_bottle": build_clutter_bottle_mesh,
            "clutter_junk": build_clutter_junk_mesh,
            "lamp_desk": build_lamp_desk_mesh, "sign_exit": build_sign_exit_mesh,
            "wall_sconce": build_wall_sconce_mesh, "monitor": build_monitor_mesh,
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

        self._glass_builders = {
            "vending": build_vending_glass_mesh,
        }
        self.glass_vaos = {}
        for kind, builder in self._glass_builders.items():
            data = builder()
            vbo = ctx.buffer(data.tobytes())
            vao = ctx.vertex_array(self.prog, [(vbo, "3f 3f 2f 3f", "in_pos", "in_normal", "in_uv", "in_color")])
            self.glass_vaos[kind] = vao

        shadow_data = build_shadow_mesh()
        self.shadow_vbo = ctx.buffer(shadow_data.tobytes())
        self.shadow_vao = ctx.vertex_array(
            self.prog, [(self.shadow_vbo, "3f 3f 2f 3f", "in_pos", "in_normal", "in_uv", "in_color")]
        )

        self.tex_floor_upper = self._upload_gray(_make_floor_texture(64), linear=True)
        self.tex_ceiling_upper = self._upload_gray(_make_ceiling_texture(64), linear=True)
        self.tex_floor_basement = self._upload_gray(_make_basement_floor_texture(64), linear=True)
        self.tex_ceiling_basement = self._upload_gray(_make_pipes_ceiling_texture(64), linear=True)
        self.tex_floor = self.tex_floor_upper
        self.tex_ceiling = self.tex_ceiling_upper
        self.tex_wood = self._upload_gray(_make_wood_texture(64), linear=True)
        self.tex_metal = self._upload_gray(_make_metal_texture(64), linear=True)
        self.tex_wall_concrete = self._upload_gray(_make_wall_detail_texture(64, 7), linear=True)
        self.tex_wall_tile = self._upload_gray(_make_tile_texture(64), linear=True)
        self.tex_wall_blood = self._upload_gray(_make_blood_texture(64), linear=True)
        self.tex_wall_fence = self._upload_gray(_make_fence_texture(64), linear=True)
        self.tex_wall_forest = self._upload_gray(_make_forest_texture(64), linear=True)
        self.tex_wall_shed = self._upload_gray(_make_shed_texture(64), linear=True)
        self.tex_floor_yard = self._upload_gray(_make_yard_floor_texture(64), linear=True)
        self.wall_textures = {
            S.WALL_CONCRETE: self.tex_wall_concrete,
            S.WALL_TILE: self.tex_wall_tile,
            S.WALL_METAL: self.tex_metal,
            S.WALL_BLOOD: self.tex_wall_blood,
            S.WALL_FENCE: self.tex_wall_fence,
            S.WALL_FOREST: self.tex_wall_forest,
            S.WALL_SHED: self.tex_wall_shed,
        }
        self.tex_paper = self._upload_gray(_make_paper_texture(64))
        self.prop_textures = {"wood": self.tex_wood, "metal": self.tex_metal, "paper": self.tex_paper}

        self.color_tex = None
        self.depth_rb = None
        self.fbo = None
        self._build_framebuffer()

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

        self.hud_tex = None
        self.wall_parts = []
        self.floor_vao = None
        self.ceil_vao = None
        self.sky_dome_vao = None
        self.wall_mask_tex = None
        self.maze_size = (1.0, 1.0)

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

    def set_max_shadow_lights(self, count):
        self.max_shadow_lights = max(1, int(count))

    def ensure_hud_texture(self, w, h):
        if self.hud_tex is None or self.hud_tex.size != (w, h):
            if self.hud_tex is not None:
                self.hud_tex.release()
            self.hud_tex = self.ctx.texture((w, h), 4)
            self.hud_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self.hud_tex.repeat_x = False
            self.hud_tex.repeat_y = False

    def build_level(self, maze, theme="upper"):
        if theme == "basement":
            self.tex_floor, self.tex_ceiling = self.tex_floor_basement, self.tex_ceiling_basement
        elif theme == "yard":
            self.tex_floor, self.tex_ceiling = self.tex_floor_yard, None
        else:
            self.tex_floor, self.tex_ceiling = self.tex_floor_upper, self.tex_ceiling_upper
        for vao, vbo, _tile_type in getattr(self, "wall_parts", []):
            vao.release()
            vbo.release()
        if self.floor_vao is not None:
            self.floor_vao.release()
            self.floor_vbo.release()
        if self.ceil_vao is not None:
            self.ceil_vao.release()
            self.ceil_vbo.release()
            self.ceil_vao = None

        self.wall_parts = []
        for tile_type, data in build_maze_walls_by_type(maze).items():
            vbo = self.ctx.buffer(data.tobytes())
            vao = self.ctx.vertex_array(
                self.prog, [(vbo, "3f 3f 2f 3f", "in_pos", "in_normal", "in_uv", "in_color")]
            )
            self.wall_parts.append((vao, vbo, tile_type))

        floor_data = build_floor_mesh(maze)
        self.floor_vbo = self.ctx.buffer(floor_data.tobytes())
        self.floor_vao = self.ctx.vertex_array(
            self.prog, [(self.floor_vbo, "3f 3f 2f 3f", "in_pos", "in_normal", "in_uv", "in_color")]
        )

        if self.wall_mask_tex is not None:
            self.wall_mask_tex.release()
        mask = build_wall_mask(maze)
        self.wall_mask_tex = self.ctx.texture((maze.w, maze.h), 1, mask.tobytes())
        self.wall_mask_tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self.wall_mask_tex.repeat_x = False
        self.wall_mask_tex.repeat_y = False
        self.maze_size = (float(maze.w), float(maze.h))

        window_segs = build_window_segments(maze)[:MAX_WINDOW_SEGS]
        segs_a = [p0 for p0, _p1 in window_segs]
        segs_b = [p1 for _p0, p1 in window_segs]
        while len(segs_a) < MAX_WINDOW_SEGS:
            segs_a.append(_NO_WINDOW_SEG)
            segs_b.append(_NO_WINDOW_SEG)
        self.prog["window_seg_a"].value = segs_a
        self.prog["window_seg_b"].value = segs_b

        if self.sky_dome_vao is not None:
            self.sky_dome_vao.release()
            self.sky_dome_vbo.release()
            self.sky_dome_vao = None

        if theme == "yard":
            dome_data = build_sky_dome_mesh(maze.w, maze.h)
            self.sky_dome_vbo = self.ctx.buffer(dome_data.tobytes())
            self.sky_dome_vao = self.ctx.vertex_array(
                self.prog, [(self.sky_dome_vbo, "3f 3f 2f 3f", "in_pos", "in_normal", "in_uv", "in_color")]
            )
        else:
            ceil_data = build_ceiling_mesh(maze)
            self.ceil_vbo = self.ctx.buffer(ceil_data.tobytes())
            self.ceil_vao = self.ctx.vertex_array(
                self.prog, [(self.ceil_vbo, "3f 3f 2f 3f", "in_pos", "in_normal", "in_uv", "in_color")]
            )
        self._door_mask_state = {}

    _NO_DOOR_SEG = (-9999.0, -9999.0)

    def sync_door_mask(self, doors, extra_barriers=()):
        closed = [d for d in doors if not d.is_open]
        segs_a, segs_b = [], []
        for d in closed[:MAX_DOOR_SEGS]:
            p0, p1 = d.closed_line()
            segs_a.append(p0)
            segs_b.append(p1)
        while len(segs_a) < MAX_DOOR_SEGS:
            segs_a.append(self._NO_DOOR_SEG)
            segs_b.append(self._NO_DOOR_SEG)
        self.prog["door_seg_a"].value = segs_a
        self.prog["door_seg_b"].value = segs_b

        if self.wall_mask_tex is None:
            return
        for p in extra_barriers:
            self._sync_one_barrier(id(p), (int(p.x), int(p.y)), not p.solid)

    def _sync_one_barrier(self, key, cell, passable_now):
        if self._door_mask_state.get(key) == passable_now:
            return
        self._door_mask_state[key] = passable_now
        cx, cy = cell
        self.wall_mask_tex.write(bytes([0 if passable_now else 255]), viewport=(cx, cy, 1, 1))

    def _prop_color(self, prop, t):
        base = list(c / 255.0 for c in prop.base_color)
        if prop.interactable == "panel":
            if prop.powered:
                pulse = 0.7 + 0.3 * math.sin(t * 3.0)
                target = (0.35, 0.9, 0.55)
                base = [b * 0.35 + c * 0.65 * pulse for b, c in zip(base, target)]
            elif prop.installed > 0:
                target = (0.9, 0.7, 0.24)
                base = [b * 0.7 + c * 0.3 for b, c in zip(base, target)]
        elif prop.interactable == "exit" and prop.powered:
            pulse = 0.7 + 0.3 * math.sin(t * 2.4)
            target = (0.35, 0.86, 0.78)
            base = [b * 0.3 + c * 0.7 * pulse for b, c in zip(base, target)]
        return tuple(base)

    def _draw_box(self, model, color, emissive=0.0, texture=None, tex_scale=(1.0, 1.0), vao=None, alpha=1.0):
        prog = self.prog
        prog["model"].write(gm.to_gl(model))
        prog["base_color"].value = color
        prog["emissive"].value = emissive
        prog["u_alpha"].value = alpha
        if texture is not None:
            prog["use_tex"].value = 1.0
            prog["tex_scale"].value = tex_scale
            texture.use(location=0)
            prog["tex0"].value = 0
        else:
            prog["use_tex"].value = 0.0
        if alpha < 1.0:
            self.ctx.enable(moderngl.BLEND)
            self.ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
            self.fbo.depth_mask = False
        (vao or self.box_vao).render(moderngl.TRIANGLES)
        if alpha < 1.0:
            self.fbo.depth_mask = True
            self.ctx.disable(moderngl.BLEND)

    def _cull_by_distance(self, props, eye, cull_dist):
        ex, ey = eye[0], eye[1]
        cull_dist2 = cull_dist * cull_dist
        return [p for p in props if (p.x - ex) ** 2 + (p.y - ey) ** 2 <= cull_dist2]

    def _hinge_swing_model(self, p, swing, hinge_depth=0.0):
        # Single shared hinge-rotation transform for every swinging door/lid
        # (locker leaf, shed_lock, and - via the door_swings/p.swing values fed
        # in from the player's hide transition, the real monster's checking
        # timers, and the debug mannequins - all three sources ultimately
        # render through this one method, so the pivot math only lives here).
        #
        # hinge_depth is the local-X offset (along facing) from the prop's own
        # center to the true hinge edge: 0.0 for a symmetric box like
        # shed_lock (both faces exist, hinge runs through its own mid-depth),
        # p.hd for a leaf mesh built flush against the front face only (the
        # locker door) - omitting it there previously left the pivot short by
        # exactly that amount, so the door's own edge traced a small circle
        # around the hinge instead of staying on it.
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

    def _draw_props(self, props, t, door_swings=None):
        door_swings = door_swings or {}
        for p in props:
            if p.picked:
                continue
            swing = getattr(p, "swing", 0.0)
            if p.kind == "shed_lock" and swing > 0.0:
                model = self._hinge_swing_model(p, swing)
            else:
                model = gm.trs_z(p.x, p.y, p.z0, p.facing, p.hd * 2, p.hw * 2, p.height)
            tex = self.prop_textures.get(p.texture) if p.texture else None
            if tex is not None:
                s = max(1, int(max(p.hw, p.hd, p.height) * 2.5))
                tex_scale = (s, s)
            else:
                tex_scale = (1.0, 1.0)
            if p.kind == "locker":
                vao = self.locker_vao
            elif p.kind == "tree":
                vao = self.tree_vao
            elif p.kind == "door":
                vao = self.broken_door_vao if getattr(p, "is_broken", False) else self.door_vao
            elif p.kind == "pipes" and (getattr(p, "pipe_open_neg", False) or getattr(p, "pipe_open_pos", False)):
                variant = "pipes_open_both" if p.pipe_open_neg and p.pipe_open_pos else (
                    "pipes_open_neg" if p.pipe_open_neg else "pipes_open_pos")
                vao = self.prop_vaos.get(variant, self.box_vao)
            elif p.kind in BREAKABLE_LIGHT_KINDS and getattr(p, "broken", False):
                vao = self.prop_vaos.get(p.kind + "_broken", self.box_vao)
            else:
                vao = self.prop_vaos.get(p.kind, self.box_vao)
            is_dead_light = p.kind in BREAKABLE_LIGHT_KINDS and getattr(p, "broken", False)
            self._draw_box(model, self._prop_color(p, t), 1.0 if (p.emissive and not is_dead_light) else 0.0,
                            texture=tex, tex_scale=tex_scale, vao=vao)
            glass_vao = self.glass_vaos.get(p.kind)
            if glass_vao is not None:
                self._draw_box(model, (1.0, 1.0, 1.0), 0.15, vao=glass_vao, alpha=0.35)
            if p.kind == "locker":
                leaf_swing = door_swings.get(id(p), 0.0)
                leaf_model = self._hinge_swing_model(p, leaf_swing, hinge_depth=p.hd) if leaf_swing > 0.0 else model
                self._draw_box(leaf_model, self._prop_color(p, t), 0.0, texture=tex, tex_scale=tex_scale,
                                vao=self.locker_door_vao)

    max_shadow_lights = 2
    SHADOW_LIGHT_MIN_STRENGTH = 0.08

    def _shadow_light_sources(self, ox, oy, eye, cam_forward, flashlight_on, lights):
        candidates = []
        if flashlight_on:
            ex, ey, ez = eye
            dx, dy = ox - ex, oy - ey
            dist = math.hypot(dx, dy)
            if dist > 1e-4:
                cfx, cfy = cam_forward[0], cam_forward[1]
                cf_len = math.hypot(cfx, cfy) or 1.0
                cos_angle = (dx / dist * cfx + dy / dist * cfy) / cf_len
                cone = _smoothstep(0.6, 0.95, cos_angle)
                if cone > 0.0:
                    atten = max(0.0, 1.0 - dist / 10.5)
                    strength = atten * cone
                    if strength > 0.0:
                        candidates.append(((ex, ey, ez), strength))
        for p in lights:
            d = math.hypot(p.x - ox, p.y - oy)
            atten = max(0.0, 1.0 - d / max(p.light_radius, 0.001))
            if atten > 0.0:
                candidates.append(((p.x, p.y, p.z0 + p.height * 0.6), atten))
        candidates.sort(key=lambda c: -c[1])
        strong = [c for c in candidates if c[1] >= self.SHADOW_LIGHT_MIN_STRENGTH][:self.max_shadow_lights]
        return strong if strong else [(None, 0.05)]

    SHADOW_ALPHA_MIN = 0.15
    SHADOW_ALPHA_REF = 0.55

    def _draw_one_shadow(self, ox, oy, oz, rx, ry, facing_fallback, light, strength, alpha_scale=1.0):
        if light is None:
            model = gm.trs_z(ox, oy, oz, facing_fallback, rx, ry, 1.0)
        else:
            lx, ly, lz = light
            dx, dy = ox - lx, oy - ly
            dist = math.hypot(dx, dy)
            angle = math.atan2(dy, dx) if dist > 1e-4 else facing_fallback
            height = max(lz, 0.15)
            elong = max(1.0, min(3.5, dist / height))
            offset = max(rx, ry) * (elong - 1.0) * 0.5
            cx, cy = ox + math.cos(angle) * offset, oy + math.sin(angle) * offset
            model = gm.trs_z(cx, cy, oz, angle, rx * elong, ry, 1.0)
        alpha_t = max(0.0, min(1.0, strength / self.SHADOW_ALPHA_REF))
        alpha = (self.SHADOW_ALPHA_MIN + (1.0 - self.SHADOW_ALPHA_MIN) * alpha_t) * alpha_scale
        self.prog["model"].write(gm.to_gl(model))
        self.prog["u_alpha"].value = alpha
        self.shadow_vao.render(moderngl.TRIANGLES)

    def _draw_object_shadows(self, ox, oy, oz, rx, ry, facing_fallback, eye, cam_forward, flashlight_on, lights):
        sources = self._shadow_light_sources(ox, oy, eye, cam_forward, flashlight_on, lights)
        alpha_scale = 1.0 / max(1, len(sources))
        for light, strength in sources:
            self._draw_one_shadow(ox, oy, oz, rx, ry, facing_fallback, light, strength, alpha_scale=alpha_scale)

    def _draw_shadows(self, props, monster, eye, cam_forward, flashlight_on, lights):
        prog = self.prog
        prog["flat_shade"].value = 1.0
        prog["use_tex"].value = 0.0
        prog["emissive"].value = 0.0
        prog["base_color"].value = (1.0, 1.0, 1.0)
        prog["snap_res"].value = 100000.0
        self.ctx.enable(moderngl.BLEND)
        self.ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
        self.fbo.depth_mask = False
        for p in props:
            if p.picked or not p.solid or p.kind in ("door", "shed_lock", "rock") or getattr(p, "wall_mounted", False):
                continue
            sx = max(p.hd * 1.7, 0.20)
            sy = max(p.hw * 1.7, 0.20)
            self._draw_object_shadows(p.x, p.y, p.z0 + 0.03, sx, sy, p.facing,
                                       eye, cam_forward, flashlight_on, lights)
        mr = 0.5
        self._draw_object_shadows(monster.x, monster.y, 0.03, mr, mr, 0.0,
                                   eye, cam_forward, flashlight_on, lights)
        self.fbo.depth_mask = True
        self.ctx.disable(moderngl.BLEND)
        prog["u_alpha"].value = 1.0
        prog["flat_shade"].value = 0.0
        prog["snap_res"].value = self.snap_res

    @staticmethod
    def compute_check_frac(monster):
        checking_timer = getattr(monster, "checking_timer", 0.0)
        if checking_timer <= 0.0:
            return 0.0
        total = getattr(monster, "checking_timer_total", S.MONSTER_LOCKER_CHECK_SECONDS)
        elapsed = total - checking_timer
        return min(1.0, elapsed / max(0.35, total * 0.3))

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
        head_tilt = 0.20 + twitch
        base_head_pitch = 0.20
        head_pitch = base_head_pitch + (0.50 - base_head_pitch) * check_frac
        head_frame = neck_root @ gm.rotate_z(head_tilt) @ gm.rotate_y(head_pitch)
        head_sz = 0.15
        part(head_frame, 0.16, 0.16, head_sz)

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

    def _set_point_lights(self, props, eye, t):
        ex, ey, ez = eye
        all_lights = [p for p in props if not p.picked and not getattr(p, "broken", False) and getattr(p, "light_radius", None)]
        boundary_dist = None
        lights = []
        if all_lights:
            all_lights.sort(key=lambda p: (p.x - ex) ** 2 + (p.y - ey) ** 2 + (p.z0 - ez) ** 2)
            lights = all_lights[:MAX_POINT_LIGHTS]
            if len(all_lights) > MAX_POINT_LIGHTS:
                bp = all_lights[MAX_POINT_LIGHTS]
                boundary_dist = math.sqrt((bp.x - ex) ** 2 + (bp.y - ey) ** 2 + (bp.z0 - ez) ** 2)
        pos = [(0.0, 0.0, -1000.0)] * MAX_POINT_LIGHTS
        color = [(0.0, 0.0, 0.0)] * MAX_POINT_LIGHTS
        radius = [0.0] * MAX_POINT_LIGHTS
        fade_margin = 1.5
        for i, p in enumerate(lights):
            k = 1.0
            if getattr(p, "flicker", False):
                k = 0.8 + 0.2 * math.sin(t * 10.0 + p.bob_phase * 5.0)
            if boundary_dist is not None:
                d = math.sqrt((p.x - ex) ** 2 + (p.y - ey) ** 2 + (p.z0 - ez) ** 2)
                if d > boundary_dist - fade_margin:
                    k *= max(0.0, min(1.0, (boundary_dist - d) / fade_margin))
            pos[i] = (p.x, p.y, p.z0 + p.height * 0.6)
            color[i] = tuple(c * k for c in p.light_color)
            radius[i] = p.light_radius
        prog = self.prog
        prog["light_pos"].value = pos
        prog["light_color"].value = color
        prog["light_radius"].value = radius
        return lights

    def render(self, maze, player, monster, props, dread, t, shake_yaw=0.0, shake_pitch=0.0,
               fog_color=None, fog_dist=12.5, ambient=0.06, moon_strength=0.0,
               qa_mode=False, view_distance_mult=1.0, hide_locker=None, hide_swing=0.0,
               camera_override=None, extra_door_swings=None):
        ctx = self.ctx
        self.fbo.use()
        ctx.viewport = (0, 0, self.low_w, self.low_h)
        ctx.clear(0.015, 0.013, 0.018, depth=1.0)
        ctx.enable(moderngl.DEPTH_TEST)
        ctx.disable(moderngl.CULL_FACE)
        ctx.disable(moderngl.BLEND)

        if camera_override is not None:
            eye, yaw, pitch, fov_degrees = camera_override
        else:
            bob = math.sin(player.bob_phase) * (0.032 if player.is_sprinting else 0.020)
            bob *= 0.0 if player.is_hiding else 1.0
            crouch_drop = getattr(player, "crouch", 0.0) * S.CROUCH_EYE_DROP
            eye = (player.x, player.y, EYE_HEIGHT + bob - crouch_drop)
            yaw = player.angle + shake_yaw
            pitch = max(-1.45, min(1.45, player.pitch + shake_pitch))
            fov_degrees = FOV_DEGREES
        view = gm.view_matrix(eye, yaw, pitch)
        proj = gm.perspective(math.radians(fov_degrees), self.low_w / self.low_h, 0.03, 130.0)

        prog = self.prog
        prog["view"].write(gm.to_gl(view))
        prog["proj"].write(gm.to_gl(proj))
        prog["cam_pos"].value = eye
        fx, fy = math.cos(yaw) * math.cos(pitch), math.sin(yaw) * math.cos(pitch)
        fz = math.sin(pitch)
        prog["cam_forward"].value = (fx, fy, fz)
        prog["snap_res"].value = 100000.0 if qa_mode else self.snap_res
        prog["qa_mode"].value = 1.0 if qa_mode else 0.0
        prog["fog_color"].value = tuple(c / 255.0 for c in (fog_color or S.COL_FOG))
        eff_fog_dist = max(4.0, min(30.0, fog_dist * view_distance_mult))
        prog["fog_dist"].value = eff_fog_dist
        prog["u_time"].value = t
        prog["u_resolution"].value = (float(self.low_w), float(self.low_h))
        prog["sanity_dark"].value = 1.0 - player.sanity / S.SANITY_MAX
        prog["ambient_level"].value = ambient
        prog["moon_dir"].value = (0.4, 0.28, -0.62)
        prog["moon_strength"].value = moon_strength
        prog["no_fog"].value = 0.0
        prog["flat_shade"].value = 0.0
        prog["u_alpha"].value = 1.0
        prog["glass_dim"].value = 0.0
        _t_lights0 = time.perf_counter()
        active_lights = self._set_point_lights(props, eye, t)
        _t_lights1 = time.perf_counter()
        if self.wall_mask_tex is not None:
            self.wall_mask_tex.use(location=1)
        prog["wall_mask"].value = 1
        prog["maze_size"].value = self.maze_size

        flicker = 1.0
        if player.flashlight_on:
            if player.battery < S.FLASHLIGHT_LOW:
                flicker = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(t * 40 + random.random() * 3))
            if random.random() < 0.002:
                flicker *= 0.2
        prog["flash_on"].value = 1.0 if player.flashlight_on else 0.0
        prog["flash_intensity"].value = flicker * (player.battery / 100.0)

        prog["model"].write(gm.to_gl(gm.identity()))
        prog["base_color"].value = (1.0, 1.0, 1.0)
        prog["emissive"].value = 0.0

        if self.sky_dome_vao is not None:
            prog["no_fog"].value = 1.0
            self.sky_dome_vao.render(moderngl.TRIANGLES)
            prog["no_fog"].value = 0.0
            prog["emissive"].value = 0.0

        _t_walls0 = time.perf_counter()
        prog["use_tex"].value = 1.0
        prog["tex_scale"].value = (1.0, 1.0)
        prog["tex0"].value = 0
        for vao, _vbo, tile_type in self.wall_parts:
            if tile_type == WINDOW_GLASS_KEY:
                continue
            tex = self.wall_textures.get(tile_type, self.tex_wall_concrete)
            tex.use(location=0)
            vao.render(moderngl.TRIANGLES)

        for vao, _vbo, tile_type in self.wall_parts:
            if tile_type != WINDOW_GLASS_KEY:
                continue
            prog["use_tex"].value = 0.0
            prog["u_alpha"].value = 0.30
            prog["glass_dim"].value = 1.0
            self.ctx.enable(moderngl.BLEND)
            self.ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
            self.fbo.depth_mask = False
            vao.render(moderngl.TRIANGLES)
            self.fbo.depth_mask = True
            self.ctx.disable(moderngl.BLEND)
            prog["u_alpha"].value = 1.0
            prog["glass_dim"].value = 0.0
            prog["use_tex"].value = 1.0

        _t_walls1 = time.perf_counter()
        prog["snap_res"].value = 100000.0
        prog["tex_scale"].value = (maze.w * S.FLOOR_CEILING_TEX_DENSITY, maze.h * S.FLOOR_CEILING_TEX_DENSITY)
        self.tex_floor.use(location=0)
        if self.floor_vao is not None:
            self.floor_vao.render(moderngl.TRIANGLES)

        if self.ceil_vao is not None:
            self.tex_ceiling.use(location=0)
            self.ceil_vao.render(moderngl.TRIANGLES)
        prog["snap_res"].value = self.snap_res
        _t_floor1 = time.perf_counter()

        if self.sky_dome_vao is None:
            visible_props = self._cull_by_distance(props, eye, eff_fog_dist + 2.0)
        else:
            visible_props = props
        if not qa_mode:
            self._draw_shadows(visible_props, monster, eye, (fx, fy, fz), player.flashlight_on, active_lights)
        _t_shadows1 = time.perf_counter()

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

        self._draw_props(visible_props, t, door_swings=door_swings)
        _t_props1 = time.perf_counter()
        self._draw_monster(monster, dread, check_frac=check_frac)
        _t_monster1 = time.perf_counter()
        self.last_perf = {
            "lights": (_t_lights1 - _t_lights0) * 1000.0,
            "walls": (_t_walls1 - _t_walls0) * 1000.0,
            "floor": (_t_floor1 - _t_walls1) * 1000.0,
            "shadows": (_t_shadows1 - _t_floor1) * 1000.0,
            "props": (_t_props1 - _t_shadows1) * 1000.0,
            "monster": (_t_monster1 - _t_props1) * 1000.0,
        }

    def composite(self, hud_rgba_bytes, hud_size, screen_size):
        ctx = self.ctx
        ctx.screen.use()
        ctx.viewport = (0, 0, screen_size[0], screen_size[1])
        ctx.disable(moderngl.DEPTH_TEST)
        ctx.disable(moderngl.BLEND)
        ctx.clear(0.0, 0.0, 0.0)

        self.color_tex.use(location=0)
        self.quad_prog["tex0"].value = 0
        self.quad_vao.render(moderngl.TRIANGLES)

        if hud_rgba_bytes is not None:
            self.ensure_hud_texture(*hud_size)
            self.hud_tex.write(hud_rgba_bytes)
            ctx.enable(moderngl.BLEND)
            ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
            self.hud_tex.use(location=0)
            self.quad_prog["tex0"].value = 0
            self.quad_vao.render(moderngl.TRIANGLES)
            ctx.disable(moderngl.BLEND)
