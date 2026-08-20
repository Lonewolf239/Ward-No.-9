import re
import shutil
import subprocess
import threading

try:
    import numpy as np
    import sounddevice as sd
    _IMPORT_OK = True
except Exception:
    _IMPORT_OK = False

_PAREC_PATH = shutil.which("parec")
_PACTL_PATH = shutil.which("pactl")

_VIRTUAL_DEVICE_NAMES = {
    "default", "sysdefault", "pulse", "pipewire", "front",
    "surround21", "surround40", "surround41", "surround50", "surround51", "surround71",
    "lavrate", "samplerate", "speexrate", "speex", "upmix", "vdownmix",
    "dmix", "dsnoop", "null",
}


def _pulse_input_sources():
    if not _PACTL_PATH:
        return []
    try:
        out = subprocess.run([_PACTL_PATH, "list", "sources"],
                              capture_output=True, text=True, timeout=1.0)
    except Exception:
        return []
    if out.returncode != 0:
        return []
    sources = []
    name = None
    for line in out.stdout.splitlines():
        if line.startswith("Source #"):
            name = None
            continue
        stripped = line.strip()
        if stripped.startswith("Name:"):
            name = stripped[len("Name:"):].strip()
        elif stripped.startswith("Description:") and name is not None:
            desc = stripped[len("Description:"):].strip()
            if not name.endswith(".monitor"):
                sources.append((name, desc))
            name = None
    return sources


def _match_pulse_source(name, pulse_sources):
    def tokens(s):
        return set(re.findall(r"[a-z0-9]+", s.lower()))
    wanted = tokens(name)
    best, best_score = None, 0
    for src_name, desc in pulse_sources:
        score = len(wanted & (tokens(src_name) | tokens(desc)))
        if score > best_score:
            best, best_score = src_name, score
    return best if best_score >= 2 else None


class MicListener:
    def __init__(self, blocksize=1024):
        self._blocksize = blocksize
        self._stream = None
        self._parec_proc = None
        self._parec_thread = None
        self._level = 0.0
        self._lock = threading.Lock()
        self.device_name = None
        self.last_error = None
        self._device_cache = None

    @property
    def available(self):
        return _IMPORT_OK or bool(_PAREC_PATH)

    @property
    def active(self):
        return self._stream is not None or self._parec_proc is not None

    def refresh_devices(self):
        if _IMPORT_OK and self._stream is not None:
            pass
        elif _IMPORT_OK:
            try:
                sd._terminate()
                sd._initialize()
            except Exception:
                pass
        self._device_cache = None
        return self.list_devices()

    def list_devices(self):
        if self._device_cache is not None:
            return self._device_cache
        pulse_sources = _pulse_input_sources()
        if pulse_sources:
            out = list(pulse_sources)
        elif _IMPORT_OK:
            out = []
            try:
                infos = sd.query_devices()
            except Exception:
                infos = []
            for info in infos:
                if info.get("max_input_channels", 0) <= 0:
                    continue
                name = info.get("name", "")
                if not name or name.strip().lower() in _VIRTUAL_DEVICE_NAMES:
                    continue
                out.append((name, name))
        else:
            out = []
        self._device_cache = out
        return out

    def start(self, device_name=None):
        if not self.available or self.active:
            return
        self.device_name = device_name
        self.last_error = None

        if device_name is None:
            if _IMPORT_OK:
                self._start_sounddevice(None)
            elif _PAREC_PATH:
                self._start_parec(None)
            return

        source = _match_pulse_source(device_name, _pulse_input_sources())
        if source is not None and _PAREC_PATH and self._start_parec(source):
            return

        if _IMPORT_OK:
            idx = next((i for i, info in enumerate(sd.query_devices())
                        if info.get("name") == device_name), None)
            if idx is not None:
                self._start_sounddevice(idx)
                return

        self.last_error = "device not currently reachable"

    def _start_parec(self, source_name):
        cmd = [_PAREC_PATH, "--format=float32le", "--rate=16000", "--channels=1", "--raw"]
        if source_name is not None:
            cmd += ["-d", source_name]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except Exception as exc:
            self.last_error = str(exc)
            return False
        import time
        time.sleep(0.08)
        if proc.poll() is not None:
            self.last_error = "parec exited immediately"
            return False
        self._parec_proc = proc
        self._parec_thread = threading.Thread(target=self._parec_reader, args=(proc,), daemon=True)
        self._parec_thread.start()
        return True

    def _parec_reader(self, proc):
        try:
            while True:
                data = proc.stdout.read(4096)
                if not data:
                    break
                samples = np.frombuffer(data, dtype="<f4")
                if len(samples) == 0:
                    continue
                rms = float(np.sqrt(np.mean(np.square(samples))))
                with self._lock:
                    self._level = rms
        except Exception:
            pass

    def _start_sounddevice(self, device):
        def callback(indata, frames, time_info, status):
            rms = float(np.sqrt(np.mean(np.square(indata)))) if len(indata) else 0.0
            with self._lock:
                self._level = rms

        try:
            stream = sd.InputStream(
                channels=1, blocksize=self._blocksize,
                dtype="float32", callback=callback, device=device,
            )
            stream.start()
            self._stream = stream
        except Exception as exc:
            self._stream = None
            self.last_error = str(exc)

    def stop(self):
        if self._parec_proc is not None:
            proc, self._parec_proc, self._parec_thread = self._parec_proc, None, None
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            with self._lock:
                self._level = 0.0
            return
        if self._stream is None:
            return
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass
        self._stream = None
        with self._lock:
            self._level = 0.0

    def get_level(self):
        with self._lock:
            return self._level
