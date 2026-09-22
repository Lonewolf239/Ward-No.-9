import os
import random
import threading

import numpy as np
import pygame

from game import settings as S

SR = 44100

_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
MENU_MUSIC_PATH = os.path.join(_ASSETS_DIR, "main_menu.ogg")
FLOOR_MUSIC_PATHS = [os.path.join(_ASSETS_DIR, f"game_ost_{i}.ogg") for i in range(5)]
SCARE_SOUND_PATHS = [os.path.join(_ASSETS_DIR, f"scary_sound_{i}.ogg") for i in range(10)]
SCARE_SOUND_MID_PATHS = [os.path.join(_ASSETS_DIR, f"scary_sound_{i}_mid.ogg") for i in range(10)]
SCARE_SOUND_FAR_PATHS = [os.path.join(_ASSETS_DIR, f"scary_sound_{i}_far.ogg") for i in range(10)]


def _t(dur, sr=SR):
    return np.linspace(0, dur, int(sr * dur), endpoint=False)


def sine(freq, dur, sr=SR, amp=1.0, phase=0.0):
    return amp * np.sin(2 * np.pi * freq * _t(dur, sr) + phase)


def additive_buzz(freq, dur, sr=SR, harmonics=6, amp=1.0):
    t = _t(dur, sr)
    sig = np.zeros_like(t)
    for k in range(1, harmonics + 1):
        sig += (1.0 / k) * np.sin(2 * np.pi * freq * k * t)
    return amp * sig / np.max(np.abs(sig) + 1e-9)


def white_noise(dur, sr=SR, amp=1.0, rng=None):
    rng = rng or np.random
    return amp * (rng.random(int(sr * dur)).astype(np.float64) * 2 - 1)


def smooth(sig, window):
    if window <= 1:
        return sig
    kernel = np.ones(window) / window
    return np.convolve(sig, kernel, mode="same")


def band_limit(sig, low_win, high_win):
    lo = smooth(sig, low_win)
    hi = smooth(sig, high_win)
    return lo - hi


def metal_ring(partials, dur, decay, sr=SR):
    t = _t(dur, sr)
    fall = np.exp(-t / max(1e-4, decay))
    sig = np.zeros(len(t))
    for freq, amp in partials:
        sig += amp * np.sin(2 * np.pi * freq * t) * fall
    return sig


def loop_wrap(sig, sr=SR, xfade=0.45):
    n = len(sig)
    x = max(1, min(n // 3, int(xfade * sr)))
    ramp = np.linspace(0.0, 1.0, x)
    out = np.array(sig[:n - x], dtype=np.float64)
    out[:x] = sig[n - x:] * (1.0 - ramp) + sig[:x] * ramp
    return out


def loop_taper(sig, sr=SR, taper=0.08):
    n = len(sig)
    t = max(1, min(n // 2, int(taper * sr)))
    win = np.ones(n)
    win[:t] *= np.linspace(0.0, 1.0, t)
    win[-t:] *= np.linspace(1.0, 0.0, t)
    return sig * win


def envelope(n, attack, decay, sustain, release, sr=SR, sustain_level=0.7):
    a = max(1, int(attack * sr))
    d = max(1, int(decay * sr))
    r = max(1, int(release * sr))
    s = max(0, n - a - d - r)
    env = np.concatenate([
        np.linspace(0, 1, a, endpoint=False),
        np.linspace(1, sustain_level, d, endpoint=False),
        np.full(max(s, 0), sustain_level),
        np.linspace(sustain_level, 0, r, endpoint=False),
    ])
    if len(env) < n:
        env = np.pad(env, (0, n - len(env)))
    return env[:n]


def normalize(sig, peak=0.9):
    m = np.max(np.abs(sig)) + 1e-9
    return sig / m * peak


def to_sound(sig):
    sig = np.clip(sig, -1.0, 1.0)
    pcm = (sig * 32767).astype(np.int16)
    stereo = np.repeat(pcm.reshape(-1, 1), 2, axis=1)
    stereo = np.ascontiguousarray(stereo)
    return pygame.sndarray.make_sound(stereo)


class SoundBank:
    _MUSIC_HEADROOM = 0.22

    def __init__(self, rng_seed=None):
        rng = np.random.default_rng(rng_seed)

        self.footsteps = {
            "tile": [self._make_footstep_tile(rng) for _ in range(4)],
            "stone": [self._make_footstep_stone(rng) for _ in range(4)],
            "grass": [self._make_footstep_grass(rng) for _ in range(4)],
        }
        self.footsteps_run = {
            "tile": [self._make_footstep_tile(rng, hard=True) for _ in range(4)],
            "stone": [self._make_footstep_stone(rng, hard=True) for _ in range(4)],
            "grass": [self._make_footstep_grass(rng, hard=True) for _ in range(4)],
        }
        self.scare_sounds = [pygame.mixer.Sound(p) for p in SCARE_SOUND_PATHS]
        self.scare_sounds_mid = [pygame.mixer.Sound(p) for p in SCARE_SOUND_MID_PATHS]
        self.scare_sounds_far = [pygame.mixer.Sound(p) for p in SCARE_SOUND_FAR_PATHS]
        self.heart_thump = self._make_heartbeat()
        self.pickup = self._make_pickup()
        self.unlock = self._make_unlock(rng)
        self.denied = self._make_denied()
        self.ui_beep = self._make_ui_beep()
        self.latch_click = self._make_latch_click(rng)
        self.cutters_snap = self._make_cutters_snap(rng)
        self.stinger = self._make_stinger(rng)
        self.locker_in = self._make_locker(rng)
        self.door_creak = self._make_door_creak(rng)
        self.alert_sting = self._make_alert_sting(rng)
        self.bang = self._make_bang(rng)
        self.battery_low = self._make_battery_low()
        self.ambient_loop = self._make_ambient(rng)
        self.growl_loop = self._make_growl(rng)
        self.note_pickup = self._make_note_pickup()
        self.page_turn = self._make_page_turn(rng)
        self.escape_end = self._make_escape_end(rng)
        self.hunt_pulse = self._make_hunt_pulse()
        self.elevator_floor_ding = self._make_elevator_floor_ding()
        self.elevator_hum = self._make_elevator_hum(rng)
        self.elevator_door = self._make_elevator_door(rng)
        self.angel_choir = self._make_angel_choir(rng)
        self.angel_voice = self._make_angel_voice(rng)
        self.angel_stinger = self._make_angel_stinger(rng)
        self.hatch_turn_loop = self._make_hatch_turn_loop(rng)
        self.fence_cut_loop = self._make_fence_cut_loop(rng)
        self.cutters_repair_loop = self._make_cutters_repair_loop(rng)

        pygame.mixer.set_num_channels(31)
        pygame.mixer.set_reserved(21)
        self.ch_ambient = pygame.mixer.Channel(0)
        self.ch_growl = pygame.mixer.Channel(1)
        self.ch_heart = pygame.mixer.Channel(2)
        self.ch_step = pygame.mixer.Channel(3)
        self.ch_voice = pygame.mixer.Channel(5)
        self.ch_pulse = pygame.mixer.Channel(6)
        self.ch_door = pygame.mixer.Channel(7)
        self.ch_hallu_pool = [pygame.mixer.Channel(4), pygame.mixer.Channel(8), pygame.mixer.Channel(9)]
        self.ch_elevator = pygame.mixer.Channel(10)
        self.ch_angel = pygame.mixer.Channel(11)
        self.ch_angel_voice = pygame.mixer.Channel(12)
        self.ch_action = pygame.mixer.Channel(13)
        self.ch_ambient_wet = pygame.mixer.Channel(14)
        self.ch_growl_wet = pygame.mixer.Channel(15)
        self.ch_pulse_wet = pygame.mixer.Channel(16)
        self.ch_elevator_wet = pygame.mixer.Channel(17)
        self.ch_angel_wet = pygame.mixer.Channel(18)
        self.ch_angel_voice_wet = pygame.mixer.Channel(19)
        self.ch_action_wet = pygame.mixer.Channel(20)
        self.ch_music_wet = pygame.mixer.Channel(21)

        self._heart_timer = 0.0
        self._master = 1.0
        self._sfx_vol = 1.0
        self._ambient_vol = 0.0
        self._music_vol = 0.0
        self._music_mode = "off"
        self._trip_intensity = 0.0
        self._trip_mult = 1.0
        self._comedown_mult = 1.0
        self._muffled = {}
        self._build_muffled_variants()
        self._dual_loops = {}
        self._dual_last_vol = {}
        self._music_muffled_cache = {}
        self._music_muffled_arrays = {}
        self._music_path = None
        self._prepare_music_async(FLOOR_MUSIC_PATHS)

    def _make_footstep_tile(self, rng, hard=False):
        dur = 0.10 if hard else 0.12
        body = band_limit(white_noise(dur, amp=1.0, rng=rng), 18, 60)
        env = envelope(len(body), 0.003, 0.02, 0.06, dur - 0.023, sustain_level=0.12)
        sig = body * env
        return to_sound(normalize(sig, peak=0.24 if hard else 0.19))

    def _make_footstep_stone(self, rng, hard=False):
        dur = 0.14 if hard else 0.17
        body = band_limit(white_noise(dur, amp=1.0, rng=rng), 20, 70)
        low = band_limit(white_noise(dur, amp=1.0, rng=rng), 60, 160) * 0.35
        env = envelope(len(body), 0.008, 0.035, 0.10, dur - 0.043, sustain_level=0.16)
        sig = (body + low) * env
        return to_sound(normalize(sig, peak=0.24 if hard else 0.19))

    def _make_footstep_grass(self, rng, hard=False):
        dur = 0.13 if hard else 0.16
        rustle = band_limit(white_noise(dur, amp=1.0, rng=rng), 20, 150)
        env = envelope(len(rustle), 0.004, 0.03, 0.10, dur - 0.034, sustain_level=0.22)
        sig = rustle * env
        return to_sound(normalize(sig, peak=0.18 if hard else 0.14))

    def _make_heartbeat(self):
        dur = 0.55
        n = int(SR * dur)
        sig = np.zeros(n)
        b1 = sine(58, 0.14, amp=1.0) * envelope(int(SR * 0.14), 0.002, 0.05, 0.2, 0.08)
        b2 = sine(50, 0.16, amp=0.75) * envelope(int(SR * 0.16), 0.002, 0.06, 0.15, 0.09)
        sig[: len(b1)] += b1
        off = int(SR * 0.22)
        sig[off: off + len(b2)] += b2[: max(0, len(sig) - off)]
        return to_sound(normalize(sig, 0.85))

    def _make_pickup(self):
        notes = [523.25, 659.25, 784.0]
        dur = 0.09
        parts = []
        for f in notes:
            s = sine(f, dur, amp=0.5) * envelope(int(SR * dur), 0.005, 0.02, 0.4, 0.05)
            parts.append(s)
        sig = np.concatenate(parts)
        return to_sound(normalize(sig, 0.7))

    def _make_note_pickup(self):
        notes = [392.0, 466.16]
        dur = 0.16
        parts = [sine(f, dur, amp=0.4) * envelope(int(SR * dur), 0.01, 0.03, 0.5, 0.08) for f in notes]
        sig = np.concatenate(parts)
        return to_sound(normalize(sig, 0.6))

    def _make_page_turn(self, rng):
        dur = 0.26
        n = int(SR * dur)
        sig = white_noise(dur, amp=1.0, rng=rng)
        sig = band_limit(sig, 3, 40)
        t = _t(dur)
        env = np.exp(-((t - 0.05) / 0.045) ** 2) * 0.9 + np.exp(-((t - 0.18) / 0.035) ** 2) * 0.6
        sig = sig[:n] * env[:n]
        return to_sound(normalize(sig, 0.45))

    def _make_unlock(self, rng=None):
        rng = rng if rng is not None else np.random.default_rng(11)
        dur = 0.32
        n = int(SR * dur)
        t = _t(dur)
        fall = np.exp(-t / 0.085)
        ring = (0.40 * np.sin(2 * np.pi * 900.0 * t)
                + 0.16 * np.sin(2 * np.pi * 1340.0 * t)) * fall
        sig = ring * envelope(n, 0.004, 0.03, 0.1, dur - 0.035, sustain_level=0.55)
        click = white_noise(0.05, amp=1.0, rng=rng)
        click *= envelope(len(click), 0.002, 0.012, 0.0, 0.035, sustain_level=0.2)
        sig[: len(click)] += click * 0.34
        body = band_limit(white_noise(0.10, amp=1.0, rng=rng), 150, 460)
        body *= envelope(len(body), 0.003, 0.025, 0.0, 0.07, sustain_level=0.3)
        sig[: len(body)] += body * 0.45
        return to_sound(normalize(sig, 0.5))

    def _make_denied(self):
        dur = 0.22
        sig = additive_buzz(110, dur, harmonics=4, amp=0.6) * envelope(int(SR * dur), 0.005, 0.05, 0.4, 0.08)
        return to_sound(normalize(sig, 0.6))

    def _make_ui_beep(self):
        dur = 0.06
        sig = sine(880, dur, amp=0.5) * envelope(int(SR * dur), 0.003, 0.01, 0.5, 0.03)
        return to_sound(normalize(sig, 0.5))

    def _make_latch_click(self, rng):
        dur = 0.09
        n = int(SR * dur)
        thunk = sine(210, dur, amp=0.55) * envelope(n, 0.001, 0.02, 0.0, dur - 0.021)
        tick = band_limit(white_noise(0.02, amp=1.0, rng=rng), 4, 30) * envelope(
            int(SR * 0.02), 0.001, 0.006, 0.0, 0.012)
        sig = thunk.copy()
        sig[: len(tick)] += tick * 0.8
        return to_sound(normalize(sig, 0.75))

    def _make_cutters_snap(self, rng):
        dur = 0.16
        n = int(SR * dur)
        crack = band_limit(white_noise(dur, amp=1.0, rng=rng), 3, 40) * envelope(
            n, 0.001, 0.03, 0.05, dur - 0.081)
        low = sine(90, dur, amp=0.5) * envelope(n, 0.001, 0.05, 0.0, dur - 0.051)
        sig = crack + low
        return to_sound(normalize(sig, 0.85))

    def _make_stinger(self, rng):
        dur = 1.3
        n = int(SR * dur)
        tones = sum(sine(f, dur, amp=0.5) for f in (185, 196, 233, 415, 622, 1150, 1480))
        env = envelope(n, 0.001, 0.3, 0.3, dur - 0.31)
        sig = tones * env

        thump_dur = 0.4
        tt = _t(thump_dur)
        freq = np.linspace(62, 26, len(tt))
        phase = 2 * np.pi * np.cumsum(freq) / SR
        thump = np.sin(phase) * envelope(len(tt), 0.001, 0.05, 0.1, thump_dur - 0.06) * 1.35
        sig[: len(thump)] += thump

        noise = band_limit(white_noise(0.32, amp=1.0, rng=rng), 2, 26)
        noise_env = envelope(len(noise), 0.001, 0.05, 0.35, 0.2)
        sig[: len(noise)] += noise * noise_env * 1.5

        sig = np.tanh(sig * 1.7)

        return to_sound(normalize(sig, 1.0))

    def _make_alert_sting(self, rng):
        dur = 0.4
        n = int(SR * dur)
        tone = sum(sine(f, dur, amp=0.5) for f in (220, 233, 440))
        env = envelope(n, 0.001, 0.08, 0.1, dur - 0.09)
        sig = tone * env
        noise = white_noise(0.05, amp=1.0, rng=rng)
        sig[: len(noise)] += noise * 0.6
        return to_sound(normalize(sig, 0.8))

    def _make_door_creak(self, rng):
        dur = 0.55
        n = int(SR * dur)
        t = _t(dur)
        wobble_hz = 5.0 + 3.0 * np.sin(2 * np.pi * 1.4 * t)
        phase = 2 * np.pi * np.cumsum(wobble_hz) / SR
        creak = np.sin(phase) * 0.5
        noise = band_limit(white_noise(dur, amp=1.0, rng=rng), 5, 50) * 0.5
        env = envelope(n, 0.03, 0.1, 0.55, 0.28)
        sig = (creak + noise) * env
        return to_sound(normalize(sig, 0.55))

    def _make_hatch_turn_loop(self, rng):
        turn = 0.9
        bars = 16
        dur = bars * turn
        span = dur + turn
        t = _t(span)
        n = len(t)
        sig = band_limit(white_noise(span, amp=1.0, rng=rng), 40, 150) * 0.62
        sig += band_limit(white_noise(span, amp=1.0, rng=rng), 180, 560) * 0.44
        k = 0
        while True:
            idx = int((0.3 + k * turn) * SR)
            k += 1
            if idx + int(SR * 0.3) >= n:
                break
            clack = band_limit(white_noise(0.02, amp=1.0, rng=rng), 14, 44)
            clack *= envelope(len(clack), 0.0005, 0.005, 0.0, 0.014, sustain_level=0.2)
            sig[idx: idx + len(clack)] += clack * 0.62
            ring = metal_ring(((980.0, 0.13), (1640.0, 0.06)), 0.14, 0.03)
            sig[idx: idx + len(ring)] += ring
        return to_sound(normalize(loop_wrap(sig, xfade=turn), 0.42))

    def _make_fence_cut_loop(self, rng):
        period = 2.0 / 3.0
        bars = 9
        dur = bars * period
        span = dur + period
        n = int(SR * span)
        sig = band_limit(white_noise(span, amp=1.0, rng=rng), 40, 140) * 0.28

        for i in range(bars + 1):
            idx = int((i * period + period * 0.45
                       + period * rng.uniform(-0.05, 0.05)) * SR)
            if idx < 0 or idx + int(SR * 0.4) >= n:
                continue
            load = band_limit(white_noise(0.14, amp=1.0, rng=rng), 110, 360)
            load *= np.linspace(0.1, 1.0, len(load)) ** 2
            sig[idx: idx + len(load)] += load * 0.55
            cut = idx + len(load)
            crunch = band_limit(white_noise(0.05, amp=1.0, rng=rng), 30, 110)
            crunch *= envelope(len(crunch), 0.001, 0.012, 0.0, 0.035, sustain_level=0.2)
            sig[cut: cut + len(crunch)] += crunch * 1.0
            ring = metal_ring(((1120.0, 0.10),), 0.10, 0.03)
            if cut + len(ring) < n:
                sig[cut: cut + len(ring)] += ring
        return to_sound(normalize(loop_wrap(sig, xfade=period), 0.38))

    def _make_cutters_repair_loop(self, rng):
        period = 5.0 / 9.0
        dur = 9 * period
        t = _t(dur + period)
        n = len(t)
        sig = np.zeros(n)
        for i in range(10):
            c0 = i * period + period * rng.uniform(-0.05, 0.05)
            idx = max(0, int(c0 * SR))
            slen = int(SR * period * 0.55)
            if idx + slen >= n:
                continue
            rasp = band_limit(white_noise(slen / SR, amp=1.0, rng=rng), 7, 22)[:slen]
            shape = np.sin(np.pi * np.linspace(0, 1, slen)) ** 1.5
            teeth = 0.78 + 0.22 * np.sin(2 * np.pi * 47.0 * t[idx: idx + slen])
            sig[idx: idx + slen] += rasp * shape * teeth * 0.55
            ring = metal_ring(((1960.0, 0.10), (3120.0, 0.05)), 0.16, 0.04)
            r0 = idx + int(slen * 0.75)
            if r0 + len(ring) < n:
                sig[r0: r0 + len(ring)] += ring
            body = band_limit(white_noise(slen / SR, amp=1.0, rng=rng), 150, 500)[:slen]
            sig[idx: idx + slen] += body * shape * 0.18
        return to_sound(normalize(loop_wrap(sig, xfade=period), 0.40))

    def _make_locker(self, rng):
        dur = 0.4
        n = white_noise(dur, amp=1.0, rng=rng)
        n = band_limit(n, 4, 40)
        env = envelope(len(n), 0.02, 0.1, 0.4, 0.2)
        return to_sound(normalize(n * env, 0.5))

    def _make_bang(self, rng):
        dur = 0.35
        n = int(SR * dur)
        thump = sine(45, dur, amp=0.8) * envelope(n, 0.002, 0.08, 0.1, dur - 0.09)
        noise = white_noise(0.06, amp=1.0, rng=rng)
        sig = thump.copy()
        sig[: len(noise)] += noise * 0.7
        return to_sound(normalize(sig, 0.9))

    def _make_battery_low(self):
        dur = 0.09
        one = sine(440, dur, amp=0.4) * envelope(int(SR * dur), 0.005, 0.01, 0.4, 0.04)
        gap = np.zeros(int(SR * 0.06))
        sig = np.concatenate([one, gap, one])
        return to_sound(normalize(sig, 0.5))

    def _make_ambient(self, rng):
        dur = 53.0
        t = _t(dur)
        drone = 0.5 * np.sin(2 * np.pi * 55 * t) + 0.35 * np.sin(2 * np.pi * 58 * t)
        sub = 0.3 * np.sin(2 * np.pi * 27.5 * t)
        tremolo = 0.72 + 0.28 * np.sin(2 * np.pi * 0.3774 * t)
        swell = 0.86 + 0.14 * np.sin(2 * np.pi * 0.0943 * t + 1.1)
        drift = 0.90 + 0.10 * np.sin(2 * np.pi * 0.0231 * t + 2.7)
        far = 0.16 * np.sin(2 * np.pi * 41.3 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 0.0617 * t))
        hiss = band_limit(white_noise(dur, amp=1.0, rng=rng), 10, 240) * 0.05
        sig = (drone + sub + far) * tremolo * swell * drift + hiss
        return to_sound(normalize(loop_wrap(sig, xfade=1.6), 0.24))

    def _make_growl(self, rng):
        dur = 4.0
        t = _t(dur)
        base = additive_buzz(70, dur, harmonics=5, amp=0.6)
        wobble = 1.0 + 0.15 * np.sin(2 * np.pi * 5.5 * t)
        noise = band_limit(white_noise(dur, amp=1.0, rng=rng), 5, 120) * 0.25
        sig = base * wobble + noise
        return to_sound(normalize(sig, 0.6))

    def _make_hunt_pulse(self):
        dur = 2.0
        t = _t(dur)
        pulse_hz = 2.0
        env = (0.5 + 0.5 * np.sin(2 * np.pi * pulse_hz * t)) ** 6
        tone = 0.8 * np.sin(2 * np.pi * 38 * t) + 0.2 * np.sin(2 * np.pi * 76 * t)
        sig = tone * env
        return to_sound(normalize(sig, 0.75))

    def _make_elevator_floor_ding(self):
        dur = 0.14
        n = int(SR * dur)
        tone = sine(1050, dur, amp=0.5) + sine(1560, dur, amp=0.25)
        env = envelope(n, 0.002, 0.03, 0.0, 0.09, sustain_level=0.35)
        sig = tone * env
        return to_sound(normalize(sig, 0.45))

    def _make_elevator_hum(self, rng):
        dur = 6.0
        t = _t(dur)
        drone = 0.5 * np.sin(2 * np.pi * 42 * t) + 0.3 * np.sin(2 * np.pi * 84 * t)
        wobble = 1.0 + 0.06 * np.sin(2 * np.pi * 1.7 * t)
        rumble = band_limit(white_noise(dur, amp=1.0, rng=rng), 30, 90) * 0.35
        sig = loop_wrap(drone * wobble + rumble, xfade=0.6)
        return to_sound(normalize(sig, 0.5))

    def _make_elevator_door(self, rng):
        dur = 0.3
        n = int(SR * dur)
        thump = sine(70, dur, amp=0.7) * envelope(n, 0.002, 0.05, 0.1, dur - 0.06)
        clank = band_limit(white_noise(0.08, amp=1.0, rng=rng), 40, 300) * 0.6
        sig = thump.copy()
        sig[: len(clank)] += clank
        return to_sound(normalize(sig, 0.8))

    def _make_escape_end(self, rng):
        dur = 3.4
        t = _t(dur)
        notes = (110.0, 130.81, 164.81)
        tone = sum(sine(f, dur, amp=0.30) for f in notes)
        swell = np.clip(t / 1.6, 0.0, 1.0) ** 1.6 * np.clip((dur - t) / 1.3, 0.0, 1.0) ** 0.7
        wind = band_limit(white_noise(dur, amp=1.0, rng=rng), 3, 45) * 0.09
        sig = tone * swell + wind * swell
        return to_sound(normalize(sig, 0.55))

    def _make_angel_choir(self, rng):
        dur = 11.0
        t = _t(dur)
        heaven_freqs = (523.25, 659.25, 784.0, 987.77)
        heaven = np.zeros(len(t))
        for i, f in enumerate(heaven_freqs):
            wob = 1.0 + 0.0022 * np.sin(2 * np.pi * (0.13 + 0.037 * i) * t + i * 1.7)
            phase = 2 * np.pi * np.cumsum(f * wob) / SR
            heaven += 0.19 * np.sin(phase) + 0.05 * np.sin(2 * phase)
        breathe = 0.90 + 0.10 * np.sin(2 * np.pi * 0.0715 * t)
        under = 0.22 * np.sin(2 * np.pi * 72 * t) + 0.14 * np.sin(2 * np.pi * 108 * t)
        under *= 1.0 - 0.10 * np.sin(2 * np.pi * 0.0413 * t + 2.1)
        air = band_limit(white_noise(dur, amp=1.0, rng=rng), 14, 90) * 0.06
        sig = heaven * breathe + under + air
        return to_sound(normalize(loop_wrap(sig, xfade=1.2), 0.5))

    def _make_angel_voice(self, rng):
        dur = 7.0
        t = _t(dur)
        base = 168.0 + 30.0 * np.sin(2 * np.pi * 0.09 * t)
        phase = 2 * np.pi * np.cumsum(base) / SR
        tone = np.zeros(len(t))
        for h in range(1, 11):
            f = base * h
            gain = (0.5 / (1.0 + ((f - 620.0) / 170.0) ** 2)
                    + 0.3 / (1.0 + ((f - 1600.0) / 320.0) ** 2))
            tone += gain * np.sin(h * phase)
        syllable = np.zeros(len(t))
        step = int(SR * 0.7)
        for i in range(0, len(t) - step, step):
            syllable[i: i + step] = np.linspace(0.0, 1.0, step) ** 3
        breath = band_limit(white_noise(dur, amp=1.0, rng=rng), 7, 30) * 0.10
        sig = tone * 0.3 * (0.30 + 0.70 * syllable) + breath
        return to_sound(normalize(loop_wrap(sig, xfade=0.7), 0.55))

    def _make_angel_stinger(self, rng):
        dur = 1.3
        n = int(SR * dur)
        sig = np.zeros(n)

        inhale = band_limit(white_noise(0.55, amp=1.0, rng=rng), 8, 60)
        inhale *= np.linspace(0.0, 1.0, len(inhale)) ** 2
        sig[: len(inhale)] += inhale * 0.7

        hit = int(SR * 0.55)
        rest = dur - 0.55
        chord = sum(sine(f, rest, amp=0.30) for f in (659.25, 987.77, 1567.98))
        chord *= envelope(len(chord), 0.003, 0.2, 0.2, rest - 0.21)
        sig[hit: hit + len(chord)] += chord

        thump = np.sin(2 * np.pi * 46.0 * _t(0.35))
        thump *= envelope(int(SR * 0.35), 0.002, 0.05, 0.1, 0.29) * 0.8
        sig[hit: hit + len(thump)] += thump

        sig = np.tanh(sig * 1.3)
        return to_sound(normalize(sig, 0.85))

    def set_master_volume(self, v):
        self._master = max(0.0, min(1.0, v))
        self._apply_ambient_volumes()
        self._apply_music_volume()

    def set_sfx_volume(self, v):
        self._sfx_vol = max(0.0, min(1.0, v))

    def _effective_sfx(self):
        return self._master * self._sfx_vol * self._trip_mult * self._comedown_mult

    @staticmethod
    def _moving_avg(x, window):
        if window <= 1:
            return x
        kernel = np.ones(window) / window
        return np.convolve(x, kernel, mode="same")

    def _muffle_array(self, arr, window=20):
        was_1d = arr.ndim == 1
        cols = [arr] if was_1d else [arr[:, c] for c in range(arr.shape[1])]
        filtered = [self._moving_avg(c.astype(np.float64), window) for c in cols]
        out = filtered[0] if was_1d else np.stack(filtered, axis=1)
        info = np.iinfo(arr.dtype) if np.issubdtype(arr.dtype, np.integer) else None
        if info is not None:
            out = np.clip(out, info.min, info.max)
        return np.ascontiguousarray(out.astype(arr.dtype))

    def _muffle_sound(self, snd):
        arr = pygame.sndarray.array(snd)
        return pygame.sndarray.make_sound(self._muffle_array(arr))

    def _build_muffled_variants(self):
        def visit(val):
            if isinstance(val, pygame.mixer.Sound):
                if id(val) not in self._muffled:
                    self._muffled[id(val)] = self._muffle_sound(val)
            elif isinstance(val, dict):
                for v in list(val.values()):
                    visit(v)
            elif isinstance(val, list):
                for v in list(val):
                    visit(v)
        for key, val in list(self.__dict__.items()):
            if key == "_muffled":
                continue
            visit(val)

    def _m(self, snd):
        if snd is not None and self._trip_intensity > 0.0 and random.random() < self._trip_intensity:
            return self._muffled.get(id(snd), snd)
        return snd

    def _resolve(self, snd, volume):
        out = self._m(snd)
        out.set_volume(volume)
        return out

    def _muffled_music_array(self, path):
        snd = pygame.mixer.Sound(path)
        return self._muffle_array(pygame.sndarray.array(snd), window=26)

    def _prepare_music_async(self, paths):
        stop = self._music_stop = threading.Event()

        def work():
            for path in paths:
                if stop.is_set():
                    return
                if path in self._music_muffled_arrays or path in self._music_muffled_cache:
                    continue
                try:
                    snd = pygame.mixer.Sound(path)
                    if stop.is_set():
                        return
                    arr = pygame.sndarray.array(snd)
                    self._music_muffled_arrays[path] = self._muffle_array(arr, window=26)
                except (pygame.error, OSError, ValueError):
                    return
        self._music_worker = threading.Thread(target=work, name="music-muffle", daemon=True)
        self._music_worker.start()
        pygame.register_quit(self.shutdown)

    def shutdown(self, timeout=2.0):
        stop = getattr(self, "_music_stop", None)
        if stop is not None:
            stop.set()
        worker = getattr(self, "_music_worker", None)
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout)

    def _muffled_music_sound(self, path):
        cached = self._music_muffled_cache.get(path)
        if cached is not None:
            return cached
        filtered = self._music_muffled_arrays.pop(path, None)
        if filtered is None:
            filtered = self._muffled_music_array(path)
        wet = pygame.sndarray.make_sound(filtered)
        self._music_muffled_cache[path] = wet
        return wet

    def _start_dual_loop(self, dry_ch, wet_ch, base_snd, *vol):
        if dry_ch.get_busy():
            self._set_dual_volume(dry_ch, wet_ch, *vol)
            return
        dry_ch.play(base_snd, loops=-1)
        wet_ch.play(self._muffled.get(id(base_snd), base_snd), loops=-1)
        self._dual_loops[dry_ch] = (wet_ch, base_snd)
        self._set_dual_volume(dry_ch, wet_ch, *vol)

    def _set_dual_volume(self, dry_ch, wet_ch, *vol):
        self._dual_last_vol[dry_ch] = vol
        t = self._trip_intensity
        dry_ch.set_volume(*[v * (1.0 - t) for v in vol])
        wet_ch.set_volume(*[v * t for v in vol])

    def _stop_dual_loop(self, dry_ch, fade_ms=None):
        pair = self._dual_loops.pop(dry_ch, None)
        self._dual_last_vol.pop(dry_ch, None)
        if pair is None:
            return
        wet_ch, _base = pair
        if fade_ms is None:
            dry_ch.stop()
            wet_ch.stop()
        else:
            dry_ch.fadeout(fade_ms)
            wet_ch.fadeout(fade_ms)

    def update_trip(self, intensity):
        intensity = max(0.0, min(1.0, intensity))
        self._trip_mult = 1.0 - 0.25 * intensity
        self._trip_intensity = intensity
        for dry_ch, (wet_ch, _base) in list(self._dual_loops.items()):
            vol = self._dual_last_vol.get(dry_ch, (0.0,))
            self._set_dual_volume(dry_ch, wet_ch, *vol)
        self._apply_music_volume()

    def update_comedown(self, intensity):
        intensity = max(0.0, min(1.0, intensity))
        self._comedown_mult = 1.0 - (1.0 - S.PILL_COMEDOWN_SFX_MULT) * intensity
        self._apply_music_volume()

    def set_music_volume(self, v):
        self._music_vol = max(0.0, min(1.0, v))
        self._apply_music_volume()
        self._apply_ambient_volumes()

    def _apply_music_volume(self):
        if self._music_mode == "off":
            return
        full = self._master * self._music_vol * self._MUSIC_HEADROOM * self._comedown_mult
        t = self._trip_intensity
        pygame.mixer.music.set_volume(full * (1.0 - t))
        self.ch_music_wet.set_volume(full * t)

    def _play_music_dual(self, path):
        self._music_path = path
        full = self._master * self._music_vol * self._MUSIC_HEADROOM * self._comedown_mult
        t = self._trip_intensity
        pygame.mixer.music.load(path)
        pygame.mixer.music.set_volume(full * (1.0 - t))
        pygame.mixer.music.play(loops=-1, fade_ms=1800)
        self.ch_music_wet.set_volume(full * t)
        self.ch_music_wet.play(self._muffled_music_sound(path), loops=-1, fade_ms=1800)

    def play_menu_music(self):
        if self._music_mode == "menu":
            return
        self._music_mode = "menu"
        self._play_music_dual(MENU_MUSIC_PATH)

    def play_floor_music(self):
        self._music_mode = "floor"
        self._play_music_dual(random.choice(FLOOR_MUSIC_PATHS))

    def stop_music(self):
        if self._music_mode == "off":
            return
        self._music_mode = "off"
        self._music_path = None
        pygame.mixer.music.fadeout(1200)
        self.ch_music_wet.fadeout(1200)

    def play_footstep(self, sprinting=False, surface="tile", rng=None):
        banks = self.footsteps_run if sprinting else self.footsteps
        bank = banks.get(surface, banks["tile"])
        snd = (rng or random).choice(bank)
        self.ch_step.play(self._resolve(snd, self._effective_sfx() * (0.55 if sprinting else 0.35)))

    def play_pickup(self):
        self._resolve(self.pickup, self._effective_sfx() * 0.7).play()

    def play_note_pickup(self):
        self._resolve(self.note_pickup, self._effective_sfx() * 0.6).play()

    def play_page_turn(self):
        self._resolve(self.page_turn, self._effective_sfx() * 0.5).play()

    def play_unlock(self):
        self._resolve(self.unlock, self._effective_sfx() * 0.8).play()

    def play_elevator_floor_ding(self):
        self._resolve(self.elevator_floor_ding, self._effective_sfx() * 0.4).play()

    def play_denied(self):
        self._resolve(self.denied, self._effective_sfx() * 0.6).play()

    def play_ui(self):
        self._resolve(self.ui_beep, self._effective_sfx() * 0.5).play()

    def play_stinger(self):
        self.ch_voice.set_volume(self._effective_sfx())
        self.ch_voice.play(self._m(self.stinger))

    def play_angel_stinger(self):
        self.ch_voice.set_volume(self._effective_sfx())
        self.ch_voice.play(self._m(self.angel_stinger))

    def _hallu_channel(self):
        for ch in self.ch_hallu_pool:
            if not ch.get_busy():
                return ch
        return self.ch_hallu_pool[0]

    def play_hallu_alert(self):
        ch = self._hallu_channel()
        ch.set_volume(self._effective_sfx() * 0.8)
        ch.play(self._m(self.alert_sting))
        return ch

    def play_hallu_bang(self, pan=0.0, vol=1.0):
        ch = self._hallu_channel()
        base = self._effective_sfx() * 0.75 * vol
        ch.play(self._m(self.bang))
        ch.set_volume(max(0.0, base * min(1.0, 1.0 - pan)), max(0.0, base * min(1.0, 1.0 + pan)))
        return ch

    def play_scare(self, pan=0.0, vol=1.0, dist=0.0, rng=None):
        idx = (rng or random).randrange(len(self.scare_sounds))
        if dist >= 11.0:
            snd = self.scare_sounds_far[idx]
        elif dist >= 7.0:
            snd = self.scare_sounds_mid[idx]
        else:
            snd = self.scare_sounds[idx]
        self.ch_voice.play(self._m(snd))
        self.set_scare_pan(pan, vol)

    def set_scare_pan(self, pan, vol):
        base = self._effective_sfx() * vol
        left = base * min(1.0, 1.0 - pan)
        right = base * min(1.0, 1.0 + pan)
        self.ch_voice.set_volume(max(0.0, left), max(0.0, right))

    def play_bang(self, pan=0.0, vol=1.0):
        base = self._effective_sfx() * 0.75 * vol
        self.ch_voice.play(self._m(self.bang))
        self.ch_voice.set_volume(max(0.0, base * min(1.0, 1.0 - pan)), max(0.0, base * min(1.0, 1.0 + pan)))

    def play_locker(self):
        self._resolve(self.locker_in, self._effective_sfx() * 0.6).play()

    def play_door(self, pan=0.0, vol=1.0):
        base = self._effective_sfx() * 0.65 * vol
        self.ch_door.play(self._m(self.door_creak))
        self.ch_door.set_volume(max(0.0, base * min(1.0, 1.0 - pan)), max(0.0, base * min(1.0, 1.0 + pan)))

    def play_latch(self, pan=0.0, vol=1.0):
        base = self._effective_sfx() * 0.7 * vol
        self.ch_door.play(self._m(self.latch_click))
        self.ch_door.set_volume(max(0.0, base * min(1.0, 1.0 - pan)), max(0.0, base * min(1.0, 1.0 + pan)))

    def play_cutters_snap(self):
        self._resolve(self.cutters_snap, self._effective_sfx() * 0.8).play()

    def play_alert(self):
        self.ch_voice.set_volume(self._effective_sfx() * 0.8)
        self.ch_voice.play(self._m(self.alert_sting))

    def play_battery_low(self):
        self._resolve(self.battery_low, self._effective_sfx() * 0.5).play()

    def play_escape_end(self):
        self.ch_voice.play(self._resolve(self.escape_end, self._effective_sfx() * 0.9))

    def play_elevator_door(self, vol=1.0):
        self._resolve(self.elevator_door, self._effective_sfx() * 0.7 * vol).play()

    def start_elevator_hum(self):
        self._start_dual_loop(self.ch_elevator, self.ch_elevator_wet, self.elevator_hum,
                               self._effective_sfx() * 0.5)

    def stop_elevator_hum(self, fade_ms=400):
        self._stop_dual_loop(self.ch_elevator, fade_ms)

    def start_angel_choir(self):
        self._start_dual_loop(self.ch_angel, self.ch_angel_wet, self.angel_choir,
                               self._effective_sfx() * 0.55)

    def stop_angel_choir(self, fade_ms=3000):
        self._stop_dual_loop(self.ch_angel, fade_ms)
        self._stop_dual_loop(self.ch_angel_voice, fade_ms)

    def update_angel_voice(self, pan, vol):
        base = self._effective_sfx() * 0.5 * max(0.0, vol)
        left = base * min(1.0, 1.0 - pan)
        right = base * min(1.0, 1.0 + pan)
        self._start_dual_loop(self.ch_angel_voice, self.ch_angel_voice_wet, self.angel_voice,
                               max(0.0, left), max(0.0, right))

    def start_hatch_turn_loop(self):
        self._start_dual_loop(self.ch_action, self.ch_action_wet, self.hatch_turn_loop,
                               self._effective_sfx() * 0.5)

    def start_fence_cut_loop(self):
        self._start_dual_loop(self.ch_action, self.ch_action_wet, self.fence_cut_loop,
                               self._effective_sfx() * 0.45)

    def start_cutters_repair_loop(self):
        self._start_dual_loop(self.ch_action, self.ch_action_wet, self.cutters_repair_loop,
                               self._effective_sfx() * 0.45)

    def stop_action_loop(self, fade_ms=250):
        self._stop_dual_loop(self.ch_action, fade_ms)

    def start_ambient(self):
        self._ambient_vol = 0.22
        self._start_dual_loop(self.ch_ambient, self.ch_ambient_wet, self.ambient_loop,
                               self._master * self._music_vol * self._ambient_vol)

    def set_ambient_volume(self, v):
        self._ambient_vol = max(0.0, min(1.0, v))
        self._apply_ambient_volumes()

    def _apply_ambient_volumes(self):
        if self.ch_ambient in self._dual_loops:
            wet_ch, _base = self._dual_loops[self.ch_ambient]
            self._set_dual_volume(self.ch_ambient, wet_ch, self._master * self._music_vol * self._ambient_vol)

    def set_growl(self, active, volume=0.0, pan=0.0):
        if not active:
            self._stop_dual_loop(self.ch_growl, fade_ms=300)
            return
        left = self._effective_sfx() * volume * min(1.0, 1.0 - pan)
        right = self._effective_sfx() * volume * min(1.0, 1.0 + pan)
        self._start_dual_loop(self.ch_growl, self.ch_growl_wet, self.growl_loop,
                               max(0.0, left), max(0.0, right))

    def set_hunt(self, active):
        if active:
            self._start_dual_loop(self.ch_pulse, self.ch_pulse_wet, self.hunt_pulse,
                                   self._effective_sfx() * 0.55)
        else:
            self._stop_dual_loop(self.ch_pulse, fade_ms=500)

    def stop_hunt(self, fade_ms=500):
        self._stop_dual_loop(self.ch_pulse, fade_ms)

    def stop_all_threat_audio(self):
        self._stop_dual_loop(self.ch_growl)
        self._stop_dual_loop(self.ch_pulse)

    def update_heartbeat(self, dt, sanity_frac):
        interval = 1.15 - (1.0 - sanity_frac) * 0.75
        interval = max(0.4, interval)
        self._heart_timer += dt
        if self._heart_timer >= interval:
            self._heart_timer = 0.0
            vol = 0.15 + (1.0 - sanity_frac) * 0.4
            self.ch_heart.play(self._resolve(self.heart_thump, self._effective_sfx() * vol))
