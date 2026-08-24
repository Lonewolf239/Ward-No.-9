import os
import random

import numpy as np
import pygame

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
        self.unlock = self._make_unlock()
        self.denied = self._make_denied()
        self.ui_beep = self._make_ui_beep()
        self.stinger = self._make_stinger(rng)
        self.locker_in = self._make_locker(rng)
        self.door_creak = self._make_door_creak(rng)
        self.alert_sting = self._make_alert_sting(rng)
        self.bang = self._make_bang(rng)
        self.battery_low = self._make_battery_low()
        self.ambient_loop = self._make_ambient(rng)
        self.growl_loop = self._make_growl(rng)
        self.note_pickup = self._make_note_pickup()
        self.win_sting = self._make_win()
        self.hunt_pulse = self._make_hunt_pulse()

        pygame.mixer.set_num_channels(24)
        pygame.mixer.set_reserved(10)
        self.ch_ambient = pygame.mixer.Channel(0)
        self.ch_growl = pygame.mixer.Channel(1)
        self.ch_heart = pygame.mixer.Channel(2)
        self.ch_step = pygame.mixer.Channel(3)
        self.ch_voice = pygame.mixer.Channel(5)
        self.ch_pulse = pygame.mixer.Channel(6)
        self.ch_door = pygame.mixer.Channel(7)
        self.ch_hallu_pool = [pygame.mixer.Channel(4), pygame.mixer.Channel(8), pygame.mixer.Channel(9)]

        self._heart_timer = 0.0
        self._master = 1.0
        self._sfx_vol = 1.0
        self._ambient_vol = 0.0
        self._music_vol = 0.0
        self._music_mode = "off"

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

    def _make_unlock(self):
        dur = 0.35
        ring = sine(900, dur, amp=0.4) + sine(1340, dur, amp=0.25)
        n = white_noise(0.05, amp=1.0)
        env = envelope(int(SR * dur), 0.001, 0.05, 0.15, dur - 0.06)
        sig = ring * env
        sig[: len(n)] += n * 0.5
        return to_sound(normalize(sig, 0.8))

    def _make_denied(self):
        dur = 0.22
        sig = additive_buzz(110, dur, harmonics=4, amp=0.6) * envelope(int(SR * dur), 0.005, 0.05, 0.4, 0.08)
        return to_sound(normalize(sig, 0.6))

    def _make_ui_beep(self):
        dur = 0.06
        sig = sine(880, dur, amp=0.5) * envelope(int(SR * dur), 0.003, 0.01, 0.5, 0.03)
        return to_sound(normalize(sig, 0.5))

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
        dur = 40.0
        t = _t(dur)
        drone = 0.5 * np.sin(2 * np.pi * 55 * t) + 0.35 * np.sin(2 * np.pi * 58 * t)
        sub = 0.3 * np.sin(2 * np.pi * 27.5 * t)
        tremolo = 0.7 + 0.3 * np.sin(2 * np.pi * 0.5 * t)
        slow_swell = 0.85 + 0.15 * np.sin(2 * np.pi * 0.125 * t + 1.1)
        hiss = loop_taper(band_limit(white_noise(dur, amp=1.0, rng=rng), 10, 240)) * 0.05
        sig = (drone + sub) * tremolo * slow_swell + hiss
        return to_sound(normalize(sig, 0.24))

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

    def _make_win(self):
        notes = [392.0, 523.25, 659.25, 784.0]
        dur = 0.22
        parts = [sine(f, dur, amp=0.45) * envelope(int(SR * dur), 0.01, 0.05, 0.5, 0.1) for f in notes]
        sig = np.concatenate(parts)
        return to_sound(normalize(sig, 0.75))

    def set_master_volume(self, v):
        self._master = max(0.0, min(1.0, v))
        self._apply_ambient_volumes()
        self._apply_music_volume()

    def set_sfx_volume(self, v):
        self._sfx_vol = max(0.0, min(1.0, v))

    def set_music_volume(self, v):
        self._music_vol = max(0.0, min(1.0, v))
        self._apply_music_volume()
        self._apply_ambient_volumes()

    def _apply_music_volume(self):
        if self._music_mode != "off":
            pygame.mixer.music.set_volume(self._master * self._music_vol * self._MUSIC_HEADROOM)

    def play_menu_music(self):
        if self._music_mode == "menu":
            return
        self._music_mode = "menu"
        pygame.mixer.music.load(MENU_MUSIC_PATH)
        pygame.mixer.music.set_volume(self._master * self._music_vol * self._MUSIC_HEADROOM)
        pygame.mixer.music.play(loops=-1, fade_ms=1800)

    def play_floor_music(self):
        self._music_mode = "floor"
        pygame.mixer.music.load(random.choice(FLOOR_MUSIC_PATHS))
        pygame.mixer.music.set_volume(self._master * self._music_vol * self._MUSIC_HEADROOM)
        pygame.mixer.music.play(loops=-1, fade_ms=1800)

    def stop_music(self):
        if self._music_mode == "off":
            return
        self._music_mode = "off"
        pygame.mixer.music.fadeout(1200)

    def play_footstep(self, sprinting=False, surface="tile", rng=None):
        banks = self.footsteps_run if sprinting else self.footsteps
        bank = banks.get(surface, banks["tile"])
        snd = (rng or random).choice(bank)
        snd.set_volume(self._master * self._sfx_vol * (0.55 if sprinting else 0.35))
        self.ch_step.play(snd)

    def play_pickup(self):
        self.pickup.set_volume(self._master * self._sfx_vol * 0.7)
        self.pickup.play()

    def play_note_pickup(self):
        self.note_pickup.set_volume(self._master * self._sfx_vol * 0.6)
        self.note_pickup.play()

    def play_unlock(self):
        self.unlock.set_volume(self._master * self._sfx_vol * 0.8)
        self.unlock.play()

    def play_denied(self):
        self.denied.set_volume(self._master * self._sfx_vol * 0.6)
        self.denied.play()

    def play_ui(self):
        self.ui_beep.set_volume(self._master * self._sfx_vol * 0.5)
        self.ui_beep.play()

    def play_stinger(self):
        self.ch_voice.set_volume(self._master * self._sfx_vol)
        self.ch_voice.play(self.stinger)

    def _hallu_channel(self):
        for ch in self.ch_hallu_pool:
            if not ch.get_busy():
                return ch
        return self.ch_hallu_pool[0]

    def play_hallu_alert(self):
        ch = self._hallu_channel()
        ch.set_volume(self._master * self._sfx_vol * 0.8)
        ch.play(self.alert_sting)
        return ch

    def play_hallu_bang(self, pan=0.0, vol=1.0):
        ch = self._hallu_channel()
        base = self._master * self._sfx_vol * 0.75 * vol
        ch.play(self.bang)
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
        self.ch_voice.play(snd)
        self.set_scare_pan(pan, vol)

    def set_scare_pan(self, pan, vol):
        base = self._master * self._sfx_vol * vol
        left = base * min(1.0, 1.0 - pan)
        right = base * min(1.0, 1.0 + pan)
        self.ch_voice.set_volume(max(0.0, left), max(0.0, right))

    def play_bang(self, pan=0.0, vol=1.0):
        base = self._master * self._sfx_vol * 0.75 * vol
        self.ch_voice.play(self.bang)
        self.ch_voice.set_volume(max(0.0, base * min(1.0, 1.0 - pan)), max(0.0, base * min(1.0, 1.0 + pan)))

    def play_locker(self):
        self.locker_in.set_volume(self._master * self._sfx_vol * 0.6)
        self.locker_in.play()

    def play_door(self, pan=0.0, vol=1.0):
        base = self._master * self._sfx_vol * 0.65 * vol
        self.ch_door.play(self.door_creak)
        self.ch_door.set_volume(max(0.0, base * min(1.0, 1.0 - pan)), max(0.0, base * min(1.0, 1.0 + pan)))

    def play_alert(self):
        self.ch_voice.set_volume(self._master * self._sfx_vol * 0.8)
        self.ch_voice.play(self.alert_sting)

    def play_battery_low(self):
        self.battery_low.set_volume(self._master * self._sfx_vol * 0.5)
        self.battery_low.play()

    def play_win(self):
        self.win_sting.set_volume(self._master * self._sfx_vol)
        self.win_sting.play()

    def start_ambient(self):
        self.ch_ambient.play(self.ambient_loop, loops=-1)
        self._ambient_vol = 0.22
        self._apply_ambient_volumes()

    def set_ambient_volume(self, v):
        self._ambient_vol = max(0.0, min(1.0, v))
        self._apply_ambient_volumes()

    def _apply_ambient_volumes(self):
        self.ch_ambient.set_volume(self._master * self._music_vol * self._ambient_vol)

    def set_growl(self, active, volume=0.0, pan=0.0):
        if active and not self.ch_growl.get_busy():
            self.ch_growl.play(self.growl_loop, loops=-1)
        if not active and self.ch_growl.get_busy():
            self.ch_growl.fadeout(300)
            return
        left = self._master * self._sfx_vol * volume * min(1.0, 1.0 - pan)
        right = self._master * self._sfx_vol * volume * min(1.0, 1.0 + pan)
        self.ch_growl.set_volume(max(0.0, left), max(0.0, right))

    def set_hunt(self, active):
        if active:
            if not self.ch_pulse.get_busy():
                self.ch_pulse.play(self.hunt_pulse, loops=-1)
            self.ch_pulse.set_volume(self._master * self._sfx_vol * 0.55)
        elif self.ch_pulse.get_busy():
            self.ch_pulse.fadeout(500)

    def stop_hunt(self, fade_ms=500):
        if self.ch_pulse.get_busy():
            self.ch_pulse.fadeout(fade_ms)

    def stop_all_threat_audio(self):
        self.ch_growl.stop()
        self.ch_pulse.stop()

    def update_heartbeat(self, dt, sanity_frac):
        interval = 1.15 - (1.0 - sanity_frac) * 0.75
        interval = max(0.4, interval)
        self._heart_timer += dt
        if self._heart_timer >= interval:
            self._heart_timer = 0.0
            vol = 0.15 + (1.0 - sanity_frac) * 0.4
            self.heart_thump.set_volume(self._master * self._sfx_vol * vol)
            self.ch_heart.play(self.heart_thump)
