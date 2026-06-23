"""Camera module — multi-source USB/TCP camera with MJPEG streaming.

Each source runs independently with its own ffmpeg process, frame loop,
and subscriber set.  USB sources include automatic recovery (USB reset).
TCP sources reconnect on failure.

Streaming path:
  /dev/videoN (or tcp://...) → ffmpeg (MJPEG pipe) → /stream/<id> HTTP
"""

import logging
import os
import select as _select
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from robot_control.module_base import RobotModule

log = logging.getLogger(__name__)

_MAX_RESTARTS = 3
_FRAME_TIMEOUT = 5.0


# ── module-level helpers ─────────────────────────────────────────────────

def _detect_cameras() -> List[str]:
    """Return /dev/video paths that are real video capture devices.

    Uses v4l2-ctl to filter out codec-only nodes (e.g. cedrus on Allwinner)
    and metadata-only sub-devices.
    """
    devices = []
    for i in range(10):
        path = f"/dev/video{i}"
        if not os.path.exists(path):
            continue
        checked_v4l2 = False
        try:
            info = subprocess.run(
                ["v4l2-ctl", "-d", path, "--info"],
                capture_output=True, text=True, timeout=3,
            )
            checked_v4l2 = True
            out = info.stdout
            if "Memory-to-Memory" in out or "Memory to Memory" in out:
                log.info("[Camera] skip %s — codec/M2M device", path)
                continue
            import re as _re
            dc_match = _re.search(r"Device Caps\s*:\s*(0x[0-9a-fA-F]+)", out)
            if dc_match:
                caps_hex = int(dc_match.group(1), 16)
                if caps_hex & 0x00000001:
                    devices.append(path)
                    continue
                log.info("[Camera] skip %s — Device Caps 0x%08x (no Video Capture)", path, caps_hex)
                continue
            dc_section = out.split("Device Caps", 1)
            if len(dc_section) > 1 and "Video Capture" in dc_section[1]:
                devices.append(path)
                continue
            if "Video Capture" in out and "Device Capabilities" not in out:
                devices.append(path)
                continue
            log.info("[Camera] skip %s — no Video Capture in Device Caps", path)
            continue
        except (FileNotFoundError, subprocess.SubprocessError):
            pass

        if checked_v4l2:
            continue

        try:
            with open(f"/sys/class/video4linux/video{i}/name") as fh:
                name = fh.read().strip()
            if "cedrus" not in name.lower():
                devices.append(path)
            else:
                log.info("[Camera] skip %s — driver=%s", path, name)
        except OSError:
            pass
    return devices


def _get_sudo_input() -> str:
    """Return the sudo password with trailing newline from the DB setting."""
    try:
        from robot_control.web_gui import auth
        pw = auth.get_setting("sudo_password", "orangepi")
        return (pw + "\n") if pw else "orangepi\n"
    except Exception:
        return "orangepi\n"


def _write_sysfs(path: str, value: str) -> None:
    """Write to a sysfs file, using sudo if needed."""
    try:
        with open(path, "w") as fh:
            fh.write(value)
    except PermissionError:
        subprocess.run(
            ["sudo", "-S", "sh", "-c", f"echo {value} > {path}"],
            input=_get_sudo_input(), text=True, capture_output=True, timeout=5,
        )


def _scan_usb_camera() -> Optional[str]:
    """Find a USB camera device by scanning sysfs directly.

    Used when the video node is gone (driver unbound) so the normal
    /sys/class/video4linux/<dev>/device path does not exist.
    Returns the USB device name (e.g. "2-1") or None.
    """
    base = "/sys/bus/usb/devices"
    try:
        for entry in sorted(os.listdir(base)):
            auth = os.path.join(base, entry, "authorized")
            if not os.path.exists(auth):
                continue
            if ":" in entry or entry.startswith("usb"):
                continue
            for child in os.listdir(os.path.join(base, entry)):
                driver_link = os.path.join(base, entry, child, "driver")
                if os.path.islink(driver_link):
                    real = os.path.realpath(driver_link)
                    if os.path.basename(real) == "uvcvideo":
                        return entry
            for child in os.listdir(os.path.join(base, entry)):
                if ":" in child:
                    return entry
    except OSError:
        pass
    return None


# ── CameraModule ─────────────────────────────────────────────────────────

class CameraModule(RobotModule):
    """Multi-source camera module — USB (v4l2) and TCP network sources.

    Config formats (all supported):
      Explicit:  {"sources": [{"id": "s0", "type": "usb", "device": "/dev/video6"}, ...]}
      Simple:    {"devices": ["/dev/video6", "/dev/video4"]}
      Legacy:    {"device": "/dev/video0"}
    """

    # ── CameraSource inner class ──────────────────────────────────────────

    class CameraSource:
        """Per-source camera manager — ffmpeg, frame loop, recovery, subscribers."""

        def __init__(self, source_id: str, source_type: str, device: str,
                     width: int, height: int, fps: int):
            self._id = source_id
            self._type = source_type      # "usb" | "tcp"
            self._device = device
            self._width = width
            self._height = height
            self._fps = fps

            self._cond = threading.Condition()
            self._client_seq: Dict[int, int] = {}
            self._frame: Optional[bytes] = None
            self._frame_seq: int = 0
            self._last_frame_time: float = 0.0
            self._ffmpeg_proc: Optional[subprocess.Popen] = None
            self._stderr_lines: List[str] = []
            self._running: bool = False
            self._restart_count: int = 0
            self._use_mjpeg: bool = True
            self._next_cid: int = 0
            self._available_devices: List[str] = []
            self._device_idx: int = 0

        # ── lifecycle ────────────────────────────────────────────────────

        def start(self) -> None:
            if self._running:
                return
            self._running = True

            if self._type == "usb":
                self._available_devices = _detect_cameras()
                if not self._available_devices:
                    log.warning("[Camera:%s] no capture devices found — hotplug watch", self._id)
                    t = threading.Thread(target=self._hotplug_watch, daemon=True,
                                         name=f"cam-hotplug-{self._id}")
                    t.start()
                    return
                self._choose_active_device()
                log.info("[Camera:%s] devices: %s → active=%s",
                         self._id, self._available_devices, self._device)

            self._start_ffmpeg()
            t = threading.Thread(target=self._frame_loop, daemon=True,
                                 name=f"cam-loop-{self._id}")
            t.start()
            log.info("[Camera:%s] started — %s %dx%d@%d",
                     self._id, self._device, self._width, self._height, self._fps)

        def stop(self) -> None:
            with self._cond:
                self._running = False
                self._frame = None
                self._client_seq.clear()
                self._cond.notify_all()
            self._kill_ffmpeg()
            log.info("[Camera:%s] stopped", self._id)

        def _hotplug_watch(self) -> None:
            while True:
                time.sleep(10)
                devices = _detect_cameras()
                if devices:
                    log.info("[Camera:%s] hotplug detected: %s — starting", self._id, devices)
                    self._available_devices = devices
                    self._choose_active_device()
                    self._start_ffmpeg()
                    t = threading.Thread(target=self._frame_loop, daemon=True,
                                         name=f"cam-loop-{self._id}")
                    t.start()
                    return

        def _choose_active_device(self) -> None:
            """Prefer the configured device, fall back to available."""
            if self._device in self._available_devices:
                return
            if self._available_devices:
                old = self._device
                idx = self._device_idx % len(self._available_devices)
                self._device = self._available_devices[idx]
                log.info("[Camera:%s] device changed: %s → %s", self._id, old, self._device)

        def _cycle_device(self) -> None:
            """Cycle to next available device; fall back to USB reset if single device."""
            self._restart_count = 0
            self._use_mjpeg = True
            if len(self._available_devices) <= 1:
                if self._type == "usb":
                    self._reset_usb()
                return
            self._device_idx = (self._device_idx + 1) % len(self._available_devices)
            self._device = self._available_devices[self._device_idx]
            log.warning("[Camera:%s] cycled to next device: %s", self._id, self._device)

        # ── subscriber API ────────────────────────────────────────────────

        def subscribe(self) -> int:
            self._ensure_running()
            cid = self._next_cid
            self._next_cid = cid + 1
            with self._cond:
                self._client_seq[cid] = self._frame_seq
            return cid

        def unsubscribe(self, cid: int) -> None:
            with self._cond:
                self._client_seq.pop(cid, None)

        def wait_frame(self, cid: int, timeout: float = 5.0) -> Tuple[Optional[bytes], int]:
            with self._cond:
                last = self._client_seq.get(cid, 0)
                while self._frame_seq == last and self._running:
                    if not self._cond.wait(timeout):
                        return None, last
                if not self._running:
                    return None, last
                self._client_seq[cid] = self._frame_seq
                return self._frame, self._frame_seq

        def client_count(self) -> int:
            with self._cond:
                return len(self._client_seq)

        def _ensure_running(self) -> None:
            with self._cond:
                need_start = self._ffmpeg_proc is None or self._ffmpeg_proc.poll() is not None
            if need_start:
                self._start_ffmpeg()
            if not self._running:
                self._running = True
                t = threading.Thread(target=self._frame_loop, daemon=True,
                                     name=f"cam-loop-{self._id}")
                t.start()

        # ── state ─────────────────────────────────────────────────────────

        def get_state(self) -> Dict[str, Any]:
            signal = "ok"
            elapsed = time.time() - self._last_frame_time
            if not self._running:
                signal = "stopped"
            elif self._last_frame_time == 0:
                signal = "waiting"
            elif elapsed > _FRAME_TIMEOUT * 2:
                signal = "lost"
            elif elapsed > _FRAME_TIMEOUT:
                signal = "stale"

            with self._cond:
                return {
                    "active": self._running and self._ffmpeg_proc is not None,
                    "signal": signal,
                    "fps": self._fps,
                    "width": self._width,
                    "height": self._height,
                    "source_type": self._type,
                    "device": self._device,
                    "client_count": len(self._client_seq),
                    "frame_bytes": len(self._frame) if self._frame else 0,
                }

        # ── ffmpeg ────────────────────────────────────────────────────────

        def _start_ffmpeg(self) -> None:
            self._kill_ffmpeg()

            if self._type == "usb":
                os.system(f"fuser -k {self._device} 2>/dev/null")
                time.sleep(0.5)
                if self._use_mjpeg:
                    cmd = [
                        "ffmpeg",
                        "-fflags", "nobuffer",
                        "-flags", "low_delay",
                        "-analyzeduration", "0",
                        "-probesize", "32",
                        "-f", "v4l2",
                        "-input_format", "mjpeg",
                        "-framerate", str(self._fps),
                        "-video_size", f"{self._width}x{self._height}",
                        "-i", self._device,
                        "-c:v", "copy",
                        "-f", "image2pipe", "pipe:1",
                    ]
                else:
                    cmd = [
                        "ffmpeg",
                        "-fflags", "nobuffer",
                        "-flags", "low_delay",
                        "-analyzeduration", "0",
                        "-probesize", "32",
                        "-f", "v4l2",
                        "-input_format", "yuyv422",
                        "-framerate", str(self._fps),
                        "-video_size", f"{self._width}x{self._height}",
                        "-i", self._device,
                        "-c:v", "mjpeg",
                        "-q:v", "5",
                        "-f", "image2pipe", "pipe:1",
                    ]
            else:  # tcp
                cmd = [
                    "ffmpeg",
                    "-fflags", "nobuffer",
                    "-flags", "low_delay",
                    "-i", self._device,
                    "-c:v", "copy",
                    "-f", "image2pipe", "pipe:1",
                ]

            try:
                self._ffmpeg_proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
                self._stderr_lines = []

                def _drain_stderr():
                    try:
                        for line in self._ffmpeg_proc.stderr:
                            decoded = line.decode("utf-8", errors="replace").rstrip()
                            self._stderr_lines.append(decoded)
                            if len(self._stderr_lines) > 200:
                                self._stderr_lines.pop(0)
                    except Exception:
                        pass

                t = threading.Thread(target=_drain_stderr, daemon=True,
                                     name=f"cam-stderr-{self._id}")
                t.start()
                self._last_frame_time = time.time()

                if self._type == "usb":
                    mode = "MJPEG passthrough" if self._use_mjpeg else "YUYV→MJPEG encode"
                else:
                    mode = "TCP stream copy"
                log.info("[Camera:%s] ffmpeg started (pid=%d) on %s → %s",
                         self._id, self._ffmpeg_proc.pid, self._device, mode)
            except Exception as exc:
                log.error("[Camera:%s] ffmpeg launch failed: %s", self._id, exc)
                self._ffmpeg_proc = None

        def _kill_ffmpeg(self) -> None:
            if not self._ffmpeg_proc:
                return
            if self._stderr_lines:
                log.warning("[Camera:%s] ffmpeg stderr (last 5 lines):\n%s",
                            self._id, "\n".join(self._stderr_lines[-5:]))
                self._stderr_lines = []
            try:
                self._ffmpeg_proc.kill()
                self._ffmpeg_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                log.warning("[Camera:%s] ffmpeg kill timed out", self._id)
                os.system(f"fuser -k {self._device} 2>/dev/null")
            except Exception:
                pass
            self._ffmpeg_proc = None

        # ── USB recovery ──────────────────────────────────────────────────

        def _reset_usb(self) -> None:
            """Deauthorize/reauthorize the USB device — USB sources only."""
            if self._type != "usb":
                return

            dev_node = os.path.basename(self._device)
            dev_path = f"/sys/class/video4linux/{dev_node}/device"
            usb_device = None
            try:
                parent = os.path.realpath(dev_path)
                for _ in range(5):
                    if os.path.exists(os.path.join(parent, "authorized")):
                        usb_device = os.path.basename(parent)
                        break
                    parent = os.path.dirname(parent)
            except OSError:
                pass
            if not usb_device:
                usb_device = _scan_usb_camera()
            if not usb_device:
                log.warning("[Camera:%s] USB reset: could not find USB device for %s",
                            self._id, self._device)
                return
            if ':' in usb_device:
                usb_device = usb_device.split(':')[0]

            auth_path = f"/sys/bus/usb/devices/{usb_device}/authorized"
            log.info("[Camera:%s] USB reset: deauthorize %s …", self._id, usb_device)
            self._kill_ffmpeg()
            _write_sysfs(auth_path, "0")
            time.sleep(2)
            _write_sysfs(auth_path, "1")

            for attempt in range(6):
                time.sleep(1)
                self._available_devices = _detect_cameras()
                if self._available_devices:
                    break
                log.info("[Camera:%s] USB reset: waiting for device… (attempt %d)",
                         self._id, attempt + 1)
            if not self._available_devices:
                log.info("[Camera:%s] USB reset: device not back, trying driver bind …", self._id)
                for attempt2 in range(5):
                    time.sleep(1)
                    bind_path = "/sys/bus/usb/drivers/uvcvideo/bind"
                    if os.path.exists(bind_path):
                        for child in sorted(os.listdir(
                                os.path.join("/sys/bus/usb/devices", usb_device))):
                            if ":" in child:
                                try:
                                    _write_sysfs(bind_path, child)
                                    log.info("[Camera:%s] USB reset: bind %s -> uvcvideo",
                                             self._id, child)
                                    break
                                except Exception:
                                    pass
                    self._available_devices = _detect_cameras()
                    if self._available_devices:
                        break
                    log.info("[Camera:%s] USB reset: still waiting after bind… (attempt %d)",
                             self._id, attempt2 + 1)
            self._choose_active_device()
            log.info("[Camera:%s] USB reset: complete, device=%s", self._id, self._device)

        # ── frame loop ────────────────────────────────────────────────────

        def _frame_loop(self) -> None:
            """Read MJPEG frames from ffmpeg stdout, broadcast, watch for signal loss."""
            buf = b""
            frame_n = 0
            while self._running:
                proc = self._ffmpeg_proc
                if proc is None or proc.stdout is None:
                    time.sleep(1)
                    continue
                fd = proc.stdout.fileno()

                try:
                    ready, _, _ = _select.select([fd], [], [], 1.0)
                except (ValueError, OSError, AttributeError):
                    time.sleep(1)
                    continue

                if not ready:
                    if proc.poll() is not None:
                        if self._running:
                            self._handle_ffmpeg_exit()
                            buf = b""
                            frame_n = 0
                        continue
                    if self._last_frame_time > 0:
                        elapsed = time.time() - self._last_frame_time
                        if elapsed > _FRAME_TIMEOUT:
                            self._restart_count += 1
                            delay = min(self._restart_count * 2, 10)
                            if self._type == "usb" and self._use_mjpeg:
                                log.warning("[Camera:%s] MJPEG failed — switching to YUYV encode",
                                            self._id)
                                self._use_mjpeg = False
                            log.warning("[Camera:%s] signal lost — %.1fs, restart #%d (wait %ds)",
                                        self._id, elapsed, self._restart_count, delay)
                            if self._restart_count >= _MAX_RESTARTS:
                                self._cycle_device()
                            time.sleep(delay)
                            self._start_ffmpeg()
                            buf = b""
                            frame_n = 0
                    continue

                try:
                    chunk = os.read(fd, 65536)
                except (OSError, ValueError):
                    chunk = None

                if not chunk:
                    if self._running:
                        self._handle_ffmpeg_exit()
                        buf = b""
                        frame_n = 0
                    continue

                buf += chunk
                while True:
                    s = buf.find(b"\xff\xd8")
                    e = buf.find(b"\xff\xd9", s + 2) if s != -1 else -1
                    if s == -1 or e == -1:
                        break
                    frame = buf[s:e + 2]
                    buf = buf[e + 2:]
                    frame_n += 1
                    msg = (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                           + frame + b"\r\n")
                    with self._cond:
                        self._frame = msg
                        self._frame_seq += 1
                        self._last_frame_time = time.time()
                        self._cond.notify_all()
                    if frame_n % 150 == 1:
                        log.info("[Camera:%s] frame #%d (%d bytes), %d client(s)",
                                 self._id, frame_n, len(frame), self.client_count())

        def _handle_ffmpeg_exit(self) -> None:
            """Restart ffmpeg with backoff; escalate to USB reset if persistent."""
            self._restart_count += 1
            delay = min(self._restart_count * 2, 10)
            if self._type == "usb" and self._use_mjpeg:
                log.warning("[Camera:%s] MJPEG ffmpeg exited — switching to YUYV encode", self._id)
                self._use_mjpeg = False
            log.warning("[Camera:%s] ffmpeg exited (restart #%d) — waiting %ds",
                        self._id, self._restart_count, delay)
            if self._restart_count >= _MAX_RESTARTS:
                self._cycle_device()
            time.sleep(delay)
            self._start_ffmpeg()

    # ── CameraModule (outer) ──────────────────────────────────────────────

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        self._width = int(cfg.get("width", 640))
        self._height = int(cfg.get("height", 480))
        self._fps = int(cfg.get("fps", 30))
        self._sources: Dict[str, CameraModule.CameraSource] = {}
        self._build_sources(cfg)

    def _build_sources(self, cfg: Dict[str, Any]) -> None:
        """Parse config dict into CameraSource instances.

        Accepts three formats:
          Explicit: {"sources": [{"id": "s0", "type": "usb", "device": "/dev/video6"}]}
          Simple:   {"devices": ["/dev/video6", "/dev/video4"]}
          Legacy:   {"device": "/dev/video0"}
        """
        if "sources" in cfg:
            for src_cfg in cfg["sources"]:
                sid = src_cfg["id"]
                stype = src_cfg.get("type", "usb")
                device = src_cfg["device"]
                w = int(src_cfg.get("width", self._width))
                h = int(src_cfg.get("height", self._height))
                f = int(src_cfg.get("fps", self._fps))
                self._sources[sid] = CameraModule.CameraSource(sid, stype, device, w, h, f)
        elif "devices" in cfg:
            for i, device in enumerate(cfg["devices"]):
                sid = f"cam{i}"
                self._sources[sid] = CameraModule.CameraSource(
                    sid, "usb", device, self._width, self._height, self._fps)
        elif "device" in cfg:
            self._sources["cam0"] = CameraModule.CameraSource(
                "cam0", "usb", cfg["device"], self._width, self._height, self._fps)
        else:
            self._sources["cam0"] = CameraModule.CameraSource(
                "cam0", "usb", "/dev/video0", self._width, self._height, self._fps)

    # ── RobotModule metadata ──────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "camera"

    @property
    def display_name(self) -> str:
        return "摄像头"

    def get_state_schema(self) -> Dict[str, Any]:
        return {"devices": {}, "connected": False}

    # ── lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        for source in self._sources.values():
            source.start()

    def stop(self) -> None:
        for source in self._sources.values():
            source.stop()

    def emergency_stop(self) -> None:
        self.stop()

    # ── aggregate state ───────────────────────────────────────────────────

    def get_state(self) -> Dict[str, Any]:
        return {
            "devices": {sid: s.get_state() for sid, s in self._sources.items()},
            "connected": any(s._running for s in self._sources.values()),
        }

    # ── dispatch API (called by server.py) ────────────────────────────────

    def subscribe(self, device_name: str) -> int:
        if device_name not in self._sources:
            raise ValueError(f"Unknown device: {device_name}")
        return self._sources[device_name].subscribe()

    def unsubscribe(self, device_name: str, cid: int) -> None:
        if device_name in self._sources:
            self._sources[device_name].unsubscribe(cid)

    def wait_frame(self, device_name: str, cid: int,
                   timeout: float = 5.0) -> Tuple[Optional[bytes], int]:
        if device_name not in self._sources:
            raise ValueError(f"Unknown device: {device_name}")
        return self._sources[device_name].wait_frame(cid, timeout)

    def total_clients(self) -> int:
        return sum(s.client_count() for s in self._sources.values())

    def get_device_list(self) -> List[Dict[str, Any]]:
        return [
            {"id": sid, "active": s._running, "source_type": s._type}
            for sid, s in self._sources.items()
        ]

    # ── SocketIO handlers ─────────────────────────────────────────────────

    def get_socketio_handlers(self) -> Dict[str, callable]:
        return {
            "camera_reset": self._handle_camera_reset,
            "camera_list_devices": self._handle_list_devices,
        }

    def _handle_camera_reset(self, data: dict, emit) -> None:
        device = data.get("device", "") if data else ""
        source = self._sources.get(device)
        if not source:
            emit("camera_reset_result", {"ok": False, "msg": f"Unknown device: {device}"})
            return
        log.info("[Camera:%s] Manual reset requested from GUI", source._id)
        source._restart_count = 0
        source._use_mjpeg = True
        source._reset_usb()
        source._start_ffmpeg()
        emit("camera_reset_result", {"ok": True})

    def _handle_list_devices(self, _data: dict, emit) -> None:
        emit("camera_device_list", {"devices": self.get_device_list()})
