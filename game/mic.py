import queue
import re
import shutil
import subprocess
import threading
import time

try:
    import numpy as np
    import sounddevice as sd
    _IMPORT_OK = True
except Exception:
    _IMPORT_OK = False

_PAREC_PATH = shutil.which("parec")
_PACTL_PATH = shutil.which("pactl")

_PAREC_LATENCY_MS = 30
_PAREC_READ_BYTES = 512

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
                              capture_output=True, text=True, timeout=4.0)
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
    for src_name, _desc in pulse_sources:
        if src_name == name:
            return src_name

    def tokens(s):
        return set(re.findall(r"[a-z0-9]+", s.lower()))
    wanted = tokens(name)
    best, best_score = None, 0
    for src_name, desc in pulse_sources:
        score = len(wanted & (tokens(src_name) | tokens(desc)))
        if score > best_score:
            best, best_score = src_name, score
    return best if best_score >= 2 else None


def _close_proc(proc):
    try:
        proc.terminate()
        proc.wait(timeout=1.0)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        if proc.stdout is not None:
            proc.stdout.close()
    except Exception:
        pass


class MicListener:
    def __init__(self, blocksize=256):
        self._blocksize = blocksize
        self._stream = None
        self._parec_proc = None
        self._parec_thread = None
        self._level = 0.0
        self._lock = threading.Lock()
        self.device_name = None
        self.last_error = None
        self.device_missing = False
        self._devices = []
        self._devices_known = False
        self._active = False
        self._busy = False
        self._q = queue.Queue()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    @property
    def available(self):
        return _IMPORT_OK or bool(_PAREC_PATH)

    @property
    def active(self):
        with self._lock:
            return self._active

    @property
    def busy(self):
        with self._lock:
            return self._busy

    def get_level(self):
        with self._lock:
            return self._level

    def list_devices(self):
        with self._lock:
            if not self._devices_known:
                self._devices_known = True
                self._q.put(("refresh", None))
            return list(self._devices)

    def refresh_devices(self):
        self._q.put(("refresh", None))
        return self.list_devices()

    def start(self, device_name=None):
        self._q.put(("start", device_name))

    def stop(self):
        self._q.put(("stop", None))

    def shutdown(self, timeout=1.5):
        self._q.put(("stop", None))
        self._q.put(("quit", None))
        self._worker.join(timeout=timeout)

    def _run(self):
        while True:
            cmd, arg = self._q.get()
            pending = []
            while True:
                try:
                    pending.append(self._q.get_nowait())
                except queue.Empty:
                    break
            for c, a in pending:
                if c == "quit":
                    cmd, arg = "quit", None
                    break
                if c in ("start", "stop"):
                    cmd, arg = c, a
                elif c == "refresh" and cmd == "refresh":
                    pass
            if cmd == "quit":
                self._close()
                return
            with self._lock:
                self._busy = True
            try:
                if cmd == "refresh":
                    self._refresh()
                elif cmd == "stop":
                    self._close()
                elif cmd == "start":
                    self._open(arg)
            except Exception as exc:
                with self._lock:
                    self.last_error = str(exc)
            finally:
                with self._lock:
                    self._busy = False

    def _refresh(self):
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
        with self._lock:
            self._devices = out
            self._devices_known = True

    def _open(self, device_name):
        if not self.available:
            return
        self._close()
        with self._lock:
            self.device_name = device_name
            self.last_error = None
            self.device_missing = False

        if device_name is None:
            self._open_any(None)
            return

        source = _match_pulse_source(device_name, _pulse_input_sources())
        if source is not None and _PAREC_PATH and self._start_parec(source):
            return
        if _IMPORT_OK:
            try:
                idx = next((i for i, info in enumerate(sd.query_devices())
                            if info.get("name") == device_name), None)
            except Exception:
                idx = None
            if idx is not None and self._start_sounddevice(idx):
                return
        with self._lock:
            self.device_missing = True
            self.last_error = "device not currently reachable"
        self._open_any(None)

    def _open_any(self, _device=None):
        if _IMPORT_OK and self._start_sounddevice(device):
            return
        if _PAREC_PATH:
            self._start_parec(None)

    def _start_parec(self, source_name):
        cmd = [_PAREC_PATH, "--format=float32le", "--rate=16000", "--channels=1",
               "--latency-msec=%d" % _PAREC_LATENCY_MS, "--raw"]
        if source_name is not None:
            cmd += ["-d", source_name]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except Exception as exc:
            with self._lock:
                self.last_error = str(exc)
            return False
        time.sleep(0.08)
        if proc.poll() is not None:
            _close_proc(proc)
            with self._lock:
                self.last_error = "parec exited immediately"
            return False
        self._parec_proc = proc
        self._parec_thread = threading.Thread(target=self._parec_reader, args=(proc,), daemon=True)
        self._parec_thread.start()
        with self._lock:
            self._active = True
        return True

    def _parec_reader(self, proc):
        try:
            while True:
                data = proc.stdout.read(_PAREC_READ_BYTES)
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
        with self._lock:
            if self._parec_proc is proc:
                self._active = False
                self._level = 0.0
                if self.last_error is None:
                    self.last_error = "capture ended"

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
        except Exception as exc:
            self._stream = None
            with self._lock:
                self.last_error = str(exc)
            return False
        self._stream = stream
        with self._lock:
            self._active = True
        return True

    def _close(self):
        proc, self._parec_proc, self._parec_thread = self._parec_proc, None, None
        stream, self._stream = self._stream, None
        if proc is not None:
            _close_proc(proc)
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        with self._lock:
            self._active = False
            self._level = 0.0
