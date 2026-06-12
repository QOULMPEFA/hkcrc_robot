"""Camera module — USB camera with WebRTC (SRS RTMP) + MJPEG fallback.

Auto-detects real video capture devices (skips codec-only nodes like cedrus).
Monitors frame health and restarts ffmpeg on signal loss.  Cycles through
available devices when the active one fails repeatedly.

Streaming paths:
  Primary:   /dev/videoN → ffmpeg (H.264) → RTMP → SRS → WebRTC → browser
  Fallback:  /dev/videoN → ffmpeg (MJPEG) → pipe → /stream HTTP (MJPEG)

Multi-device: each _CameraDeviceStream manages one device; CameraModule
coordinates N streams.  Server exposes /stream/<device> per device.
"""

import logging
import os as _os
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from robot_control.module_base import RobotModule

log = logging.getLogger(__name__)

# Max consecutive ffmpeg restarts before trying the next camera device.
_MAX_RESTARTS = 3
# Seconds without a frame before declaring signal loss.
_FRAME_TIMEOUT = 5.0


# ── module-level helpers ──────────────────────────────────────────────────

def _detect_cameras() -> List[str]:
    """Return /dev/video paths that are real video capture devices.

    Uses v4l2-ctl to filter out codec-only nodes (e.g. cedrus on Allwinner)
    and metadata-only sub-devices (UVC cameras often expose /dev/video1 for
    video and /dev/video2 for metadata — we only want the video one).
    """
    devices = []
    for i in range(10):  # check /dev/video0 .. /dev/video9
        path = f"/dev/video{i}"
        if not _os.path.exists(path):
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


def _device_has_mjpeg(dev: str) -> bool:
    """Check if a single device supports MJPEG format."""
    try:
        info = subprocess.run(
            ["v4l2-ctl", "-d", dev, "--list-formats"],
            capture_output=True, text=True, timeout=3,
        )
        if "MJPG" in info.stdout or "mjpeg" in info.stdout.lower():
            return True
    except (FileNotFoundError, subprocess.SubprocessError):
        try:
            info = subprocess.run(
                ["ffprobe", "-f", "v4l2", "-list_formats", "all",
                 "-i", dev],
                capture_output=True, text=True, timeout=5,
            )
            combined = info.stdout + info.stderr
            if "mjpeg" in combined.lower():
                return True
        except (FileNotFoundError, subprocess.SubprocessError):
            pass
    return False


def _sort_cameras_by_format(devices: List[str]) -> List[str]:
    """Sort devices so MJPEG-capable ones come first.

    Tries v4l2-ctl first (fast), then ffprobe (available on systems
    without v4l2-ctl, e.g. Jetson).  MJPEG devices are preferred because
    ffmpeg can do lossless -c:v copy passthrough.
    """
    mjpeg = []
    other = []
    for dev in devices:
        has_mjpeg = False
        try:
            info = subprocess.run(
                ["v4l2-ctl", "-d", dev, "--list-formats"],
                capture_output=True, text=True, timeout=3,
            )
            if "MJPG" in info.stdout or "mjpeg" in info.stdout.lower():
                has_mjpeg = True
        except (FileNotFoundError, subprocess.SubprocessError):
            try:
                info = subprocess.run(
                    ["ffprobe", "-f", "v4l2", "-list_formats", "all",
                     "-i", dev],
                    capture_output=True, text=True, timeout=5,
                )
                combined = info.stdout + info.stderr
                if "mjpeg" in combined.lower():
                    has_mjpeg = True
            except (FileNotFoundError, subprocess.SubprocessError):
                pass
        if has_mjpeg:
            mjpeg.append(dev)
        else:
            other.append(dev)
    if mjpeg:
        log.info("[Camera] MJPEG device(s): %s", mjpeg)
    return mjpeg + other


def _reset_usb_device(device_path: str) -> None:
    """Unbind and rebind the USB device backing *device_path*."""
    dev_node = _os.path.basename(device_path)
    dev_path = f"/sys/class/video4linux/{dev_node}/device"
    usb_dev = None
    driver = None
    try:
        parent = _os.path.realpath(dev_path)
        for _ in range(4):
            if _os.path.isdir(_os.path.join(parent, "driver")):
                usb_dev = _os.path.basename(parent)
                driver_link = _os.path.realpath(_os.path.join(parent, "driver"))
                driver = _os.path.basename(driver_link)
                break
            parent = _os.path.dirname(parent)
    except OSError:
        pass
    if not usb_dev or not driver:
        log.warning("[Camera] USB reset: could not find USB device for %s", device_path)
        return
    log.info("[Camera] USB reset: unbind %s from %s …", usb_dev, driver)
    try:
        with open(f"/sys/bus/usb/drivers/{driver}/unbind", "w") as fh:
            fh.write(usb_dev)
        time.sleep(2)
        with open(f"/sys/bus/usb/drivers/{driver}/bind", "w") as fh:
            fh.write(usb_dev)
        time.sleep(1)
        log.info("[Camera] USB reset: bind complete for %s", device_path)
    except OSError as exc:
        log.warning("[Camera] USB reset failed: %s", exc)


# ── _CameraDeviceStream (one per device) ──────────────────────────────────

class _CameraDeviceStream:
    """Manages ONE camera device: ffmpeg, frame buffer, subscriber pool."""

    def __init__(self, device: str, width: int = 800, height: int = 600,
                 fps: int = 30):
        self._device = device
        self._width = width
        self._height = height
        self._fps = fps
        self._has_mjpeg = _device_has_mjpeg(device)

        self._cond = threading.Condition()
        self._client_seq: Dict[int, int] = {}
        self._frame: Optional[bytes] = None
        self._frame_seq: int = 0
        self._last_frame_time: float = 0.0
        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._running: bool = False
        self._restart_count: int = 0
        self._stderr_lines: List[str] = []
        self._next_cid: int = 0

    # ── lifecycle ──────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._start_ffmpeg()
        t = threading.Thread(target=self._frame_loop, daemon=True,
                             name=f"cam-loop-{_os.path.basename(self._device)}")
        t.start()
        log.info("[Camera] stream started — device=%s %dx%d",
                 self._device, self._width, self._height)

    def stop(self) -> None:
        with self._cond:
            self._running = False
            self._frame = None
            self._client_seq.clear()
            self._cond.notify_all()
        self._kill_ffmpeg()
        log.info("[Camera] stream stopped — device=%s", self._device)

    # ── state ──────────────────────────────────────────────────────────

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
                "connected": self._running and self._ffmpeg_proc is not None,
                "client_count": len(self._client_seq),
                "frame_bytes": len(self._frame) if self._frame else 0,
                "device": self._device,
                "signal": signal,
            }

    # ── subscriber API ─────────────────────────────────────────────────

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

    def wait_frame(self, cid: int, timeout: float = 5.0
                   ) -> Tuple[Optional[bytes], int]:
        """Block until a newer frame is available. Returns (frame, seq)."""
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

    def force_restart(self) -> None:
        """Kill ffmpeg and restart (e.g. on manual reset from GUI)."""
        self._restart_count = 0
        _reset_usb_device(self._device)
        self._start_ffmpeg()

    # ── internals: ffmpeg ──────────────────────────────────────────────

    def _start_ffmpeg(self) -> None:
        self._kill_ffmpeg()
        _os.system(f"fuser -k {self._device} 2>/dev/null")
        time.sleep(0.5)

        if self._has_mjpeg:
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
            # YUYV device — encode to MJPEG (no hardware copy available).
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
        try:
            self._ffmpeg_proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                bufsize=0)
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
                                 name=f"cam-stderr-{_os.path.basename(self._device)}")
            t.start()
            self._last_frame_time = time.time()
            log.info("[Camera] ffmpeg started (pid=%d) on %s → MJPEG pipe",
                     self._ffmpeg_proc.pid, self._device)
        except Exception as exc:
            log.error("[Camera] ffmpeg launch failed: %s", exc)
            self._ffmpeg_proc = None

    def _kill_ffmpeg(self) -> None:
        if not self._ffmpeg_proc:
            return
        if self._stderr_lines:
            log.warning("[Camera] ffmpeg stderr for %s (last 5 lines):\n%s",
                        self._device, "\n".join(self._stderr_lines[-5:]))
            self._stderr_lines = []
        try:
            self._ffmpeg_proc.kill()
            self._ffmpeg_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            log.warning("[Camera] ffmpeg kill timed out — force-releasing %s",
                        self._device)
            _os.system(f"fuser -k {self._device} 2>/dev/null")
        except Exception:
            pass
        self._ffmpeg_proc = None

    # ── internals: frame loop ──────────────────────────────────────────

    def _frame_loop(self) -> None:
        """Read MJPEG frames from ffmpeg stdout, broadcast, watch for signal loss."""
        import select as _select

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
                        self._restart_count += 1
                        log.warning("[Camera] ffmpeg exited (restart #%d) on %s",
                                    self._restart_count, self._device)
                        if self._restart_count >= _MAX_RESTARTS:
                            _reset_usb_device(self._device)
                            self._restart_count = 0
                        time.sleep(1)
                        self._start_ffmpeg()
                        buf = b""
                        frame_n = 0
                    continue
                if self._last_frame_time > 0:
                    elapsed = time.time() - self._last_frame_time
                    if elapsed > _FRAME_TIMEOUT:
                        log.warning("[Camera] signal lost — %.1fs since last frame, restarting %s",
                                    elapsed, self._device)
                        self._restart_count += 1
                        if self._restart_count >= _MAX_RESTARTS:
                            _reset_usb_device(self._device)
                            self._restart_count = 0
                        self._start_ffmpeg()
                        buf = b""
                        frame_n = 0
                continue

            try:
                chunk = _os.read(fd, 65536)
            except (OSError, ValueError):
                chunk = None

            if not chunk:
                if self._running:
                    self._restart_count += 1
                    log.warning("[Camera] ffmpeg exited (restart #%d) on %s",
                                self._restart_count, self._device)
                    if self._restart_count >= _MAX_RESTARTS:
                        _reset_usb_device(self._device)
                        self._restart_count = 0
                    time.sleep(1)
                    self._start_ffmpeg()
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
                    log.info("[Camera] %s frame #%d (%d bytes), %d client(s)",
                             self._device, frame_n, len(frame), self.client_count())

    def _ensure_running(self) -> None:
        with self._cond:
            need_start = (self._ffmpeg_proc is None
                          or self._ffmpeg_proc.poll() is not None)
        if need_start:
            self._start_ffmpeg()
        if not self._running:
            self._running = True
            t = threading.Thread(target=self._frame_loop, daemon=True,
                                 name=f"cam-loop-{_os.path.basename(self._device)}")
            t.start()


# ── CameraModule (multi-device coordinator) ────────────────────────────────

class CameraModule(RobotModule):
    """Multi-camera coordinator — manages N _CameraDeviceStream instances."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        # Resolve which devices to activate.
        self._wanted_devices: List[str] = []
        self._auto_all: bool = False
        if "devices" in cfg:
            self._wanted_devices = cfg["devices"]
        elif cfg.get("auto_all"):
            self._auto_all = True
        elif "device" in cfg:
            self._wanted_devices = [cfg["device"]]
        # else: auto-best (single MJPEG device)

        self._width = int(cfg.get("width", 800))
        self._height = int(cfg.get("height", 600))
        self._fps = int(cfg.get("fps", 30))

        self._streams: Dict[str, _CameraDeviceStream] = {}  # device_path → stream
        self._available_devices: List[str] = []
        self._running = False
        self._hotplug_active = False

    # ── RobotModule metadata ─────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "camera"

    @property
    def display_name(self) -> str:
        return "摄像头"

    def get_state_schema(self) -> Dict[str, Any]:
        return {"connected": False, "devices": {}, "available": []}

    def get_socketio_handlers(self) -> Dict[str, Callable]:
        return {
            "camera_reset": self._handle_camera_reset,
            "camera_list_devices": self._handle_list_devices,
        }

    # ── lifecycle ───────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._available_devices = _sort_cameras_by_format(_detect_cameras())
        if not self._available_devices:
            log.warning("[Camera] No video capture devices found!")
            t = threading.Thread(target=self._hotplug_watch, daemon=True,
                                 name="cam-hotplug")
            t.start()
            self._hotplug_active = True
            return
        log.info("[Camera] devices found: %s", self._available_devices)
        active = self._resolve_active_devices()
        log.info("[Camera] activating streams for: %s", active)
        for dev in active:
            self._ensure_stream(dev)

    def stop(self) -> None:
        self._running = False
        self._hotplug_active = False
        for s in list(self._streams.values()):
            s.stop()
        self._streams.clear()
        log.info("[Camera] Module stopped")

    def emergency_stop(self) -> None:
        self.stop()

    def _hotplug_watch(self) -> None:
        """Periodically re-scan for USB cameras (hotplug detection)."""
        while self._hotplug_active:
            time.sleep(10)
            devices = _detect_cameras()
            if devices:
                log.info("[Camera] Hotplug detected: %s — restarting", devices)
                self._available_devices = devices
                active = self._resolve_active_devices()
                for dev in active:
                    self._ensure_stream(dev)
                return

    # ── device resolution ──────────────────────────────────────────────

    def _resolve_active_devices(self) -> List[str]:
        """Which devices should be streaming, based on config."""
        if self._wanted_devices:
            return [d for d in self._wanted_devices if d in self._available_devices]
        if self._auto_all:
            # Activate only MJPEG-capable devices (others can't do -c:v copy).
            mjpeg = [d for d in self._available_devices if _device_has_mjpeg(d)]
            if mjpeg:
                return mjpeg
            # Fallback: best single device.
            return [self._available_devices[0]] if self._available_devices else []
        # Default: single best (first after MJPEG sort).
        return [self._available_devices[0]] if self._available_devices else []

    def _ensure_stream(self, device_path: str) -> _CameraDeviceStream:
        if device_path not in self._streams:
            s = _CameraDeviceStream(device_path, self._width, self._height, self._fps)
            s.start()
            self._streams[device_path] = s
            log.info("[Camera] stream created for %s", device_path)
        return self._streams[device_path]

    def _device_key(self, device_path: str) -> str:
        return _os.path.basename(device_path)

    def _resolve_device_path(self, name: str) -> Optional[str]:
        """Resolve a short name (e.g. 'video6') or absolute path to device path."""
        if name.startswith("/dev/"):
            return name if name in self._available_devices else None
        for d in self._available_devices:
            if self._device_key(d) == name:
                return d
        # Also check currently active streams (device may have been hotplugged).
        for d in self._streams:
            if self._device_key(d) == name:
                return d
        return None

    # ── subscriber API (device-aware) ──────────────────────────────────

    def subscribe(self, device_name: str = "") -> int:
        """Return a client-id.  Falls back to first active device if no name given."""
        if not device_name:
            # Backward compat: use first active stream.
            if self._streams:
                device_name = self._device_key(next(iter(self._streams)))
            else:
                raise ValueError("No camera devices active")
        device_path = self._resolve_device_path(device_name)
        if not device_path:
            raise ValueError(f"Unknown camera device: {device_name}")
        stream = self._ensure_stream(device_path)
        return stream.subscribe()

    def unsubscribe(self, device_name: str, cid: int) -> None:
        device_path = self._resolve_device_path(device_name)
        if device_path and device_path in self._streams:
            self._streams[device_path].unsubscribe(cid)

    def wait_frame(self, device_name: str, cid: int, timeout: float = 5.0
                   ) -> Tuple[Optional[bytes], int]:
        device_path = self._resolve_device_path(device_name)
        if not device_path or device_path not in self._streams:
            return None, 0
        return self._streams[device_path].wait_frame(cid, timeout)

    def total_clients(self) -> int:
        return sum(s.client_count() for s in self._streams.values())

    # ── state ──────────────────────────────────────────────────────────

    def get_state(self) -> Dict[str, Any]:
        devices_state = {}
        for dev_path, stream in self._streams.items():
            key = self._device_key(dev_path)
            devices_state[key] = stream.get_state()

        # Backward compat: top-level fields from first active stream.
        first = next(iter(self._streams.values()), None)
        return {
            "connected": len(self._streams) > 0 and any(
                s.get_state()["connected"] for s in self._streams.values()),
            "client_count": self.total_clients(),
            "device": self._device_key(first._device) if first else "",
            "signal": first.get_state()["signal"] if first else "stopped",
            "devices": devices_state,
            "available": [self._device_key(d) for d in self._available_devices],
        }

    # ── device listing for frontend ────────────────────────────────────

    def get_device_list(self) -> List[Dict[str, Any]]:
        result = []
        for d in self._available_devices:
            key = self._device_key(d)
            stream = self._streams.get(d)
            result.append({
                "id": key,
                "path": d,
                "active": stream is not None,
                "streaming": stream is not None and stream.client_count() > 0,
            })
        # Also include any active stream devices that may no longer be in
        # available_devices (e.g. after USB reset renumbering).
        for d, stream in self._streams.items():
            if d not in self._available_devices:
                key = self._device_key(d)
                result.append({
                    "id": key,
                    "path": d,
                    "active": True,
                    "streaming": stream.client_count() > 0,
                })
        return result

    # ── SocketIO handlers ──────────────────────────────────────────────

    def _handle_list_devices(self, data: dict, emit) -> None:
        emit("camera_device_list", {"devices": self.get_device_list()})

    def _handle_camera_reset(self, data: dict, emit) -> None:
        device_name = data.get("device", "")
        if device_name:
            device_path = self._resolve_device_path(device_name)
            if device_path and device_path in self._streams:
                log.info("[Camera] Manual reset: %s", device_name)
                self._streams[device_path].force_restart()
                emit("camera_reset_result", {"ok": True, "device": device_name})
            else:
                emit("camera_reset_result", {"ok": False, "msg": f"Unknown device: {device_name}"})
        else:
            # Reset all streams.
            log.info("[Camera] Manual reset: all streams")
            for s in self._streams.values():
                s.force_restart()
            emit("camera_reset_result", {"ok": True})
