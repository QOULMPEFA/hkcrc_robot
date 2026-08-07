"""Camera module — multi-source USB/TCP camera with MJPEG streaming.

Each source runs independently with its own ffmpeg process, frame loop,
and subscriber set.  USB sources include automatic recovery (USB reset).
TCP sources reconnect on failure.

Streaming path:
  /dev/videoN (or tcp://...) → ffmpeg (MJPEG pipe) → /stream/<id> HTTP
"""

import glob
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

try:
    from camera_sdk.realsense import is_realsense_available, detect_realsense_devices, RealSensePipeline  # noqa: E501
    _HAS_REALSENSE = is_realsense_available()
except ImportError:
    _HAS_REALSENSE = False
    RealSensePipeline = None  # type: ignore[assignment]
    detect_realsense_devices = None  # type: ignore[assignment]

try:
    from camera_sdk.orbbec import is_orbbec_k4a_available, detect_orbbec_devices, OrbbecPipeline  # noqa: E501
    _HAS_ORBBEC_K4A = is_orbbec_k4a_available()
    _HAS_ORBBEC_DETECT = True  # USB VID/PID detection works without k4a
except ImportError:
    _HAS_ORBBEC_K4A = False
    _HAS_ORBBEC_DETECT = False
    OrbbecPipeline = None  # type: ignore[assignment]
    detect_orbbec_devices = None  # type: ignore[assignment]


# ── module-level helpers ─────────────────────────────────────────────────

def _detect_cameras() -> List[str]:
    """Return /dev/video paths that are real video capture devices.

    Uses v4l2-ctl to filter out codec-only nodes (e.g. cedrus on Allwinner)
    and metadata-only sub-devices.
    """
    devices = []
    for path in sorted(glob.glob("/dev/video*")):
        dev_name = os.path.basename(path)
        if not os.path.exists(f"/sys/class/video4linux/{dev_name}/device"):
            continue  # ghost node — USB device was unplugged
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
                    log.info("[Camera] detect %s — card=%s caps=0x%08x ✓",
                             path, _get_device_name(path), caps_hex)
                    continue
                log.info("[Camera] skip %s — Device Caps 0x%08x (no Video Capture)", path, caps_hex)
                continue
            dc_section = out.split("Device Caps", 1)
            if len(dc_section) > 1 and "Video Capture" in dc_section[1]:
                devices.append(path)
                log.info("[Camera] detect %s — card=%s (Device Caps match) ✓",
                         path, _get_device_name(path))
                continue
            if "Video Capture" in out and "Device Capabilities" not in out:
                devices.append(path)
                log.info("[Camera] detect %s — card=%s (legacy match) ✓",
                         path, _get_device_name(path))
                continue
            log.info("[Camera] skip %s — no Video Capture in Device Caps", path)
            continue
        except (FileNotFoundError, subprocess.SubprocessError):
            pass

        if checked_v4l2:
            continue

        try:
            with open(f"/sys/class/video4linux/{dev_name}/name") as fh:
                name = fh.read().strip()
            if "cedrus" not in name.lower():
                devices.append(path)
                log.info("[Camera] detect %s — sysfs name=%s ✓", path, name)
            else:
                log.info("[Camera] skip %s — driver=%s", path, name)
        except OSError:
            pass
    return devices


def _deduplicate_by_usb_device(devices: List[str]) -> List[str]:
    """Pick the best RGB video node for each physical USB camera.

    Groups /dev/video nodes by physical USB device, then selects the node
    that supports YUYV or MJPG pixel formats (i.e. a real colour sensor).
    Falls back to the first available node if no format info is available.
    """
    # Step 1 — group video nodes by physical USB device
    by_usb: Dict[str, List[str]] = {}
    for path in devices:
        dev_name = os.path.basename(path)
        usb_key = _resolve_usb_device_key(dev_name)
        by_usb.setdefault(usb_key, []).append(path)
        log.info("[Camera] detect: %s → usb_key=%s", path, usb_key)

    if len(by_usb) > 1:
        log.info("[Camera] grouped %d video nodes → %d USB devices: %s",
                 len(devices), len(by_usb),
                 {k: len(v) for k, v in by_usb.items()})

    # Step 2 — per physical device, pick the node with colour pixel formats
    result = []
    for usb_key, paths in by_usb.items():
        best = _pick_color_node(paths)
        result.append(best)

    unique = sorted(result)
    if len(unique) < len(devices):
        log.info("[Camera] dedup: %d video nodes → %d cameras", len(devices), len(unique))
    return unique


def _resolve_usb_device_key(dev_name: str) -> str:
    """Return a stable key that identifies the physical USB device for a video node.

    Uses the realpath of the device symlink to walk up to the USB device
    directory (the one containing ``authorized``). Falls back to extracting
    the USB bus-port number from the device name if symlink resolution fails.
    """
    try:
        iface = os.path.realpath(f"/sys/class/video4linux/{dev_name}/device")
    except OSError:
        return dev_name  # can't resolve → treat as unique

    # Walk up from interface parent to find the USB device (has "authorized")
    usb_dev = os.path.dirname(iface)
    for _ in range(6):
        if os.path.exists(os.path.join(usb_dev, "authorized")):
            # Return the basename (e.g. "2-1", "2-1.3") as a stable key
            return os.path.basename(usb_dev)
        parent = os.path.dirname(usb_dev)
        if parent == usb_dev:
            break
        usb_dev = parent

    # Fallback: extract bus-port from the interface path itself
    # Typical path: .../usbX/Y-Z/Y-Z:N.M → extract "Y-Z"
    import re as _re
    parts = iface.replace("\\", "/").split("/")
    for part in parts:
        if _re.match(r"^\d+-(\d+\.)*\d+$", part):
            return part
    return dev_name


def _pick_color_node(candidates: List[str]) -> str:
    """Return the candidate that supports real colour output (MJPG preferred).

    Parses v4l2-ctl --list-formats output to extract exact format codes.
    Prefers MJPG (hardware MJPEG) > YUYV (colour YUV 4:2:2).
    Avoids UYVY / GREY / Z16 / Y16 which are IR/depth/grayscale streams
    that happen to contain "YUYV" as a substring.
    """
    import re as _re
    best_yuyv = None
    for path in candidates:
        try:
            info = subprocess.run(
                ["v4l2-ctl", "-d", path, "--list-formats"],
                capture_output=True, text=True, timeout=3,
            )
            fmt_codes = set(_re.findall(r"'([^']+)'", info.stdout))
            # Prefer MJPG — direct MJPEG passthrough, best quality
            if "MJPG" in fmt_codes:
                return path
            # YUYV is real colour; track it but keep looking for MJPG
            if "YUYV" in fmt_codes and best_yuyv is None:
                best_yuyv = path
        except (FileNotFoundError, subprocess.SubprocessError):
            pass
    if best_yuyv:
        return best_yuyv
    # Fallback: any node that had some format (might be grayscale, but better than nothing)
    if candidates:
        return candidates[-1]  # last node is usually the colour sensor
    return candidates[0]


def _get_sudo_input() -> str:
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


def _get_device_name(device_path: str) -> str:
    """Return a human-readable name for a video device.

    Uses v4l2-ctl ``Card type`` first (e.g. "Intel RealSense D455"),
    then falls back to the sysfs driver name.
    """
    dev_name = os.path.basename(device_path)
    try:
        info = subprocess.run(
            ["v4l2-ctl", "-d", device_path, "--info"],
            capture_output=True, text=True, timeout=3,
        )
        for line in info.stdout.splitlines():
            if "Card type" in line:
                name = line.split(":", 1)[1].strip()
                if name and name != "Unknown":
                    return name
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    try:
        with open(f"/sys/class/video4linux/{dev_name}/name") as fh:
            sys_name = fh.read().strip()
            if sys_name:
                return sys_name
    except OSError:
        pass
    return device_path


def _get_best_resolution(device_path: str, target_w: int, target_h: int) -> Tuple[int, int]:
    """Return the closest supported resolution ≥ target, or the target itself.

    Parses v4l2-ctl --list-formats-ext to find resolutions that the device
    actually supports.  Falls back to (target_w, target_h) on any error.
    """
    try:
        info = subprocess.run(
            ["v4l2-ctl", "-d", device_path, "--list-formats-ext"],
            capture_output=True, text=True, timeout=3,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return target_w, target_h

    import re as _re
    sizes: List[Tuple[int, int]] = []
    for m in _re.finditer(r"Size:\s+Discrete\s+(\d+)x(\d+)", info.stdout):
        sizes.append((int(m.group(1)), int(m.group(2))))
    if not sizes:
        return target_w, target_h

    sizes.sort()
    target_pixels = target_w * target_h
    for w, h in sizes:
        if w * h >= target_pixels:
            log.info("[Camera] resolution adjusted: %dx%d → %dx%d (device=%s)",
                     target_w, target_h, w, h, device_path)
            return w, h
    biggest = sizes[-1]
    log.info("[Camera] resolution adjusted: %dx%d → %dx%d (device=%s)",
             target_w, target_h, biggest[0], biggest[1], device_path)
    return biggest


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
            self._device_name: str = ""

        # ── lifecycle ────────────────────────────────────────────────────

        def start(self) -> None:
            if self._running:
                return
            self._running = True

            if self._type == "usb":
                all_devices = _detect_cameras()
                if not all_devices:
                    log.warning("[Camera:%s] no capture devices found — hotplug watch", self._id)
                    t = threading.Thread(target=self._hotplug_watch, daemon=True,
                                         name=f"cam-hotplug-{self._id}")
                    t.start()
                    return
                self._available_devices = self._filter_by_usb_group(all_devices)
                self._choose_active_device()
                log.info("[Camera:%s] devices: %d in group → active=%s",
                         self._id, len(self._available_devices), self._device)
            else:
                self._device_name = self._device  # tcp:// URL as display name

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

        def _filter_by_usb_group(self, devices: List[str]) -> List[str]:
            """Return only devices belonging to the same USB device as the assigned node."""
            my_key = _resolve_usb_device_key(os.path.basename(self._device))
            filtered = [d for d in devices
                        if _resolve_usb_device_key(os.path.basename(d)) == my_key]
            if filtered:
                return filtered
            log.warning("[Camera:%s] could not filter by USB key '%s' — using all devices",
                        self._id, my_key)
            return devices

        def _hotplug_watch(self) -> None:
            while True:
                time.sleep(10)
                devices = _detect_cameras()
                if devices:
                    log.info("[Camera:%s] hotplug detected: %s — starting", self._id, devices)
                    self._available_devices = self._filter_by_usb_group(devices)
                    self._choose_active_device()
                    self._start_ffmpeg()
                    t = threading.Thread(target=self._frame_loop, daemon=True,
                                         name=f"cam-loop-{self._id}")
                    t.start()
                    return

        def _choose_active_device(self) -> None:
            """Prefer the configured device; fall back to the best colour node in group."""
            if self._device in self._available_devices:
                self._device_idx = self._available_devices.index(self._device)
                self._device_name = _get_device_name(self._device)
                return
            if self._available_devices:
                old = self._device
                best = _pick_color_node(self._available_devices)
                self._device = best
                self._device_idx = self._available_devices.index(best)
                self._device_name = _get_device_name(self._device)
                log.info("[Camera:%s] device changed: %s → %s", self._id, old, self._device)

        def _cycle_device(self) -> None:
            """Cycle to next device within the same USB group; USB reset if only one."""
            self._restart_count = 0
            self._use_mjpeg = True  # try MJPEG first on new device via normal fallback
            if len(self._available_devices) <= 1:
                if self._type == "usb":
                    self._reset_usb()
                return
            self._device_idx = (self._device_idx + 1) % len(self._available_devices)
            self._device = self._available_devices[self._device_idx]
            self._device_name = _get_device_name(self._device)
            log.warning("[Camera:%s] cycled to next device: %s (%s)",
                        self._id, self._device, self._device_name)

        # ── subscriber API ────────────────────────────────────────────────

        def subscribe(self) -> int:
            self._ensure_running()
            cid = self._next_cid
            self._next_cid = cid + 1
            with self._cond:
                self._client_seq[cid] = self._frame_seq
            log.info("[Camera:%s] client #%d subscribed (total=%d)",
                     self._id, cid, len(self._client_seq))
            return cid

        def unsubscribe(self, cid: int) -> None:
            with self._cond:
                self._client_seq.pop(cid, None)
            log.info("[Camera:%s] client #%d unsubscribed (total=%d)",
                     self._id, cid, len(self._client_seq))

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
                    "device_name": self._device_name,
                    "client_count": len(self._client_seq),
                    "frame_bytes": len(self._frame) if self._frame else 0,
                }

        # ── ffmpeg ────────────────────────────────────────────────────────

        def _start_ffmpeg(self) -> None:
            self._kill_ffmpeg()

            if self._type == "usb":
                os.system(f"fuser -k {self._device} 2>/dev/null")
                time.sleep(0.5)
                # Use the camera's native resolution if 640x480 is unsupported
                use_w, use_h = _get_best_resolution(self._device, self._width, self._height)
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
                        "-video_size", f"{use_w}x{use_h}",
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
                        "-video_size", f"{use_w}x{use_h}",
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
                self._available_devices = self._filter_by_usb_group(_detect_cameras())
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
                    self._available_devices = self._filter_by_usb_group(_detect_cameras())
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

    # ── RealSenseSource (SDK-based colour capture) ───────────────────────

    class RealSenseSource(CameraSource):
        """CameraSource for Intel RealSense cameras using pyrealsense2 SDK.

        Overrides the capture pipeline: instead of ffmpeg + V4L2, it uses
        RealSensePipeline for SDK RGB capture with in-process JPEG encoding.
        All subscriber/frame-broadcast infrastructure is inherited unchanged.
        """

        def __init__(self, source_id: str, serial: str, fallback_device: str,
                     width: int, height: int, fps: int, model: str = ""):
            super().__init__(source_id, "realsense", fallback_device, width, height, fps)
            self._serial = serial
            self._rs_pipeline = None
            # Seed the model name from enumeration so resolve_source() can match
            # on it before the pipeline has ever started.  _start_ffmpeg()
            # overwrites this with the SDK's own name once running.
            if model:
                self._device_name = model

        def _start_ffmpeg(self) -> None:
            self._kill_ffmpeg()
            try:
                self._rs_pipeline = RealSensePipeline(
                    serial=self._serial,
                    width=self._width, height=self._height, fps=self._fps,
                    jpeg_quality=85,
                )
                self._rs_pipeline.start()
                self._device_name = self._rs_pipeline.get_device_name()
                self._last_frame_time = time.time()
                log.info("[Camera:%s] RealSense pipeline started — %s %dx%d@%d",
                         self._id, self._device_name, self._width, self._height, self._fps)
            except Exception as exc:
                log.error("[Camera:%s] RealSense pipeline start failed: %s", self._id, exc)
                # Don't overwrite _rs_pipeline if the old one is still running;
                # _kill_ffmpeg() already stopped it if it was ours.

        def _ensure_running(self) -> None:
            """Override: check _rs_pipeline instead of _ffmpeg_proc."""
            with self._cond:
                need_start = (self._rs_pipeline is None
                              or not self._rs_pipeline.is_running())
            if need_start:
                self._start_ffmpeg()
            if not self._running:
                self._running = True
                t = threading.Thread(target=self._frame_loop, daemon=True,
                                     name=f"cam-loop-{self._id}")
                t.start()

        def _kill_ffmpeg(self) -> None:
            if self._rs_pipeline:
                self._rs_pipeline.stop()
                self._rs_pipeline = None
            super()._kill_ffmpeg()

        def _frame_loop(self) -> None:
            buf = b""
            frame_n = 0
            while self._running:
                pipeline = self._rs_pipeline
                if pipeline is None:
                    time.sleep(1)
                    continue

                jpeg_bytes = pipeline.get_frame(timeout=1.0)
                if jpeg_bytes is None:
                    elapsed = time.time() - self._last_frame_time
                    if self._last_frame_time > 0 and elapsed > _FRAME_TIMEOUT:
                        self._restart_count += 1
                        delay = min(self._restart_count * 2, 10)
                        log.warning("[Camera:%s] RealSense signal lost — restart #%d (wait %ds)",
                                    self._id, self._restart_count, delay)
                        if self._restart_count >= _MAX_RESTARTS:
                            self._restart_count = 0
                            self._kill_ffmpeg()
                        time.sleep(delay)
                        self._start_ffmpeg()
                        buf = b""
                        frame_n = 0
                    else:
                        time.sleep(0.01)
                    continue

                self._restart_count = 0
                frame_n += 1
                msg = (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                       + jpeg_bytes + b"\r\n")
                with self._cond:
                    self._frame = msg
                    self._frame_seq += 1
                    self._last_frame_time = time.time()
                    self._cond.notify_all()
                if frame_n % 150 == 1:
                    log.info("[Camera:%s] frame #%d (%d bytes), %d client(s)",
                             self._id, frame_n, len(jpeg_bytes), self.client_count())

        def _reset_usb(self) -> None:
            log.info("[Camera:%s] RealSense pipeline reset", self._id)
            self._kill_ffmpeg()
            self._start_ffmpeg()

        def _cycle_device(self) -> None:
            self._reset_usb()

        def _hotplug_watch(self) -> None:
            import pyrealsense2 as rs
            while True:
                time.sleep(10)
                try:
                    ctx = rs.context()
                    for d in ctx.devices:
                        if d.get_info(rs.camera_info.serial_number) == self._serial:
                            log.info("[Camera:%s] RealSense re-detected — starting", self._id)
                            self._start_ffmpeg()
                            t = threading.Thread(target=self._frame_loop, daemon=True,
                                                 name=f"cam-loop-{self._id}")
                            t.start()
                            return
                except Exception:
                    pass

    # ── OrbbecSource (K4A-based depth/stereo + V4L2 colour) ──────────────

    class OrbbecSource(CameraSource):
        """CameraSource for Orbbec Femto Bolt with colour/depth/stereo modes.

        Colour mode delegates to the base-class V4L2/ffmpeg path.
        Depth and stereo modes use the K4A-compatible OrbbecPipeline.
        """

        VALID_MODES = ("color", "depth", "stereo")

        def __init__(self, source_id: str, device: str,
                     width: int, height: int, fps: int):
            super().__init__(source_id, "usb", device, width, height, fps)
            self._mode = "color"
            self._ob_pipeline = None

        # ── mode switching ───────────────────────────────────────────────

        def get_mode(self) -> str:
            return self._mode

        def set_mode(self, mode: str) -> None:
            if mode not in self.VALID_MODES:
                log.warning("[Camera:%s] invalid mode '%s' — keeping '%s'",
                            self._id, mode, self._mode)
                return
            if mode == self._mode:
                return
            log.info("[Camera:%s] mode switch: %s → %s", self._id, self._mode, mode)
            self._mode = mode
            self._restart_count = 0
            self._kill_ffmpeg()
            self._start_ffmpeg()

        # ── pipeline lifecycle ───────────────────────────────────────────

        def _start_ffmpeg(self) -> None:
            self._kill_ffmpeg()

            if self._mode == "color":
                # Delegate to base-class V4L2/ffmpeg for RGB
                super()._start_ffmpeg()
                return

            if not _HAS_ORBBEC_K4A:
                log.error("[Camera:%s] Orbbec K4A not available — falling back to colour",
                          self._id)
                self._mode = "color"
                super()._start_ffmpeg()
                return

            try:
                self._ob_pipeline = OrbbecPipeline(
                    mode=self._mode,
                    width=self._width, height=self._height, fps=self._fps,
                    jpeg_quality=85,
                )
                self._ob_pipeline.start()
                self._device_name = self._ob_pipeline.get_device_name()
                self._last_frame_time = time.time()
                log.info("[Camera:%s] Orbbec pipeline started — %s mode=%s %dx%d@%d",
                         self._id, self._device_name, self._mode,
                         self._width, self._height, self._fps)
            except Exception as exc:
                log.error("[Camera:%s] Orbbec pipeline start failed: %s — "
                          "falling back to colour", self._id, exc)
                self._ob_pipeline = None
                self._mode = "color"
                super()._start_ffmpeg()

        def _kill_ffmpeg(self) -> None:
            if self._ob_pipeline:
                self._ob_pipeline.stop()
                self._ob_pipeline = None
            if self._mode == "color":
                super()._kill_ffmpeg()

        def _ensure_running(self) -> None:
            if self._mode == "color":
                super()._ensure_running()
                return
            with self._cond:
                need_start = (self._ob_pipeline is None
                              or not self._ob_pipeline.is_running())
            if need_start:
                self._start_ffmpeg()
            if not self._running:
                self._running = True
                t = threading.Thread(target=self._frame_loop, daemon=True,
                                     name=f"cam-loop-{self._id}")
                t.start()

        # ── frame loop ───────────────────────────────────────────────────

        def _frame_loop(self) -> None:
            if self._mode == "color":
                super()._frame_loop()
                return

            frame_n = 0
            while self._running:
                pipeline = self._ob_pipeline
                if pipeline is None:
                    time.sleep(1)
                    continue

                jpeg_bytes = pipeline.get_frame(timeout=1.0)
                if jpeg_bytes is None:
                    elapsed = time.time() - self._last_frame_time
                    if self._last_frame_time > 0 and elapsed > _FRAME_TIMEOUT:
                        self._restart_count += 1
                        delay = min(self._restart_count * 2, 10)
                        log.warning("[Camera:%s] Orbbec signal lost (%s) — "
                                    "restart #%d (wait %ds)",
                                    self._id, self._mode,
                                    self._restart_count, delay)
                        if self._restart_count >= _MAX_RESTARTS:
                            self._restart_count = 0
                            # Fall back to colour on persistent failure
                            self._mode = "color"
                            self._kill_ffmpeg()
                            self._start_ffmpeg()
                            self._frame_loop()
                            return
                        time.sleep(delay)
                        self._start_ffmpeg()
                        frame_n = 0
                    else:
                        time.sleep(0.01)
                    continue

                self._restart_count = 0
                frame_n += 1
                msg = (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                       + jpeg_bytes + b"\r\n")
                with self._cond:
                    self._frame = msg
                    self._frame_seq += 1
                    self._last_frame_time = time.time()
                    self._cond.notify_all()
                if frame_n % 150 == 1:
                    log.info("[Camera:%s] frame #%d (%d bytes) mode=%s, %d client(s)",
                             self._id, frame_n, len(jpeg_bytes),
                             self._mode, self.client_count())

        # ── recovery ─────────────────────────────────────────────────────

        def _reset_usb(self) -> None:
            if self._mode == "color":
                super()._reset_usb()
                return
            log.info("[Camera:%s] Orbbec pipeline reset (mode=%s)", self._id, self._mode)
            self._kill_ffmpeg()

        def _cycle_device(self) -> None:
            self._reset_usb()

        def _hotplug_watch(self) -> None:
            while True:
                time.sleep(10)
                devices = _detect_cameras()
                if devices:
                    log.info("[Camera:%s] Orbbec hotplug detected — starting", self._id)
                    self._available_devices = self._filter_by_usb_group(devices)
                    self._choose_active_device()
                    self._start_ffmpeg()
                    t = threading.Thread(target=self._frame_loop, daemon=True,
                                         name=f"cam-loop-{self._id}")
                    t.start()
                    return

        # ── state ────────────────────────────────────────────────────────

        def get_state(self) -> Dict[str, Any]:
            state = super().get_state()
            state["camera_mode"] = self._mode
            state["camera_modes"] = list(self.VALID_MODES)
            return state


    # ── CameraModule (outer) ──────────────────────────────────────────────

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        self._width = int(cfg.get("width", 640))
        self._height = int(cfg.get("height", 480))
        self._fps = int(cfg.get("fps", 30))
        self._sources: Dict[str, CameraModule.CameraSource] = {}
        self._lock = threading.Lock()
        self._build_sources(cfg)

    def _build_sources(self, cfg: Dict[str, Any]) -> None:
        """Parse config dict into CameraSource instances.

        Accepts:
          auto:       {"auto_detect": true}
          Explicit:   {"sources": [{"id": "s0", "type": "usb", "device": "/dev/video6"}]}
          Simple:     {"devices": ["/dev/video6", "/dev/video4"]}
          Legacy:     {"device": "/dev/video0"}
        """
        if cfg.get("auto_detect"):
            self._auto_build_sources()
        elif "sources" in cfg:
            for src_cfg in cfg["sources"]:
                sid = src_cfg["id"]
                stype = src_cfg.get("type", "usb")
                device = src_cfg["device"]
                w = int(src_cfg.get("width", self._width))
                h = int(src_cfg.get("height", self._height))
                f = int(src_cfg.get("fps", self._fps))
                self._sources[sid] = self._create_source(sid, stype, device, w, h, f)
        elif "devices" in cfg:
            for i, device in enumerate(cfg["devices"]):
                sid = f"cam{i}"
                self._sources[sid] = self._create_source(
                    sid, "usb", device, self._width, self._height, self._fps)
        elif "device" in cfg:
            self._sources["cam0"] = self._create_source(
                "cam0", "usb", cfg["device"], self._width, self._height, self._fps)
        else:
            self._sources["cam0"] = self._create_source(
                "cam0", "usb", "/dev/video0", self._width, self._height, self._fps)

    def _create_source(self, sid: str, stype: str, device: str,
                       w: int, h: int, f: int) -> "CameraModule.CameraSource":
        """Create the appropriate CameraSource subclass for a device."""
        if stype == "realsense":
            return CameraModule.RealSenseSource(sid, device, device, w, h, f)
        if stype == "orbbec":
            return CameraModule.OrbbecSource(sid, device, w, h, f)
        if stype == "tcp":
            return CameraModule.CameraSource(sid, "tcp", device, w, h, f)
        if _HAS_REALSENSE and stype == "usb":
            try:
                rs_devices = detect_realsense_devices([device])
                if rs_devices:
                    rs_info = rs_devices[0]
                    return CameraModule.RealSenseSource(
                        sid, rs_info["serial"], device, w, h, f,
                        rs_info.get("name", ""))
            except Exception:
                pass
        if _HAS_ORBBEC_DETECT and stype == "usb":
            try:
                ob_devices = detect_orbbec_devices([device])
                if ob_devices:
                    return CameraModule.OrbbecSource(sid, device, w, h, f)
            except Exception:
                pass
        return CameraModule.CameraSource(sid, "usb", device, w, h, f)
    def _auto_build_sources(self) -> None:
        """Scan /dev/video* and create sources.  RealSense cameras use the
        pyrealsense2 SDK for RGB colour; Orbbec Femto Bolt uses OrbbecSource
        with mode switching (colour/depth/stereo); all others use standard
        V4L2/ffmpeg.
        """
        detected = _detect_cameras()
        unique = _deduplicate_by_usb_device(detected)
        log.info("[Camera] auto-detect: %d nodes → %d cameras — %s",
                 len(detected), len(unique), unique)

        # Determine which V4L2 devices belong to RealSense cameras
        rs_device_map: Dict[str, Dict[str, Any]] = {}
        if _HAS_REALSENSE:
            try:
                rs_devices = detect_realsense_devices(detected)
                for rs_dev in rs_devices:
                    for node in rs_dev.get("v4l2_nodes", []):
                        rs_device_map[node] = rs_dev
                log.info("[Camera] RealSense SDK detected %d device(s): %s",
                         len(rs_devices),
                         [(d["serial"], d["name"]) for d in rs_devices])
            except Exception as e:
                log.warning("[Camera] RealSense detection failed: %s", e)

        # Determine which V4L2 devices belong to Orbbec cameras
        ob_device_set: set = set()
        if _HAS_ORBBEC_DETECT:
            try:
                ob_devices = detect_orbbec_devices(detected)
                for ob_dev in ob_devices:
                    for node in ob_dev.get("v4l2_nodes", []):
                        ob_device_set.add(node)
                log.info("[Camera] Orbbec detected %d device(s): %s",
                         len(ob_devices),
                         [(d["name"], d["usb_id"]) for d in ob_devices])
            except Exception as e:
                log.warning("[Camera] Orbbec detection failed: %s", e)

        rs_serials_assigned: set = set()
        ob_assigned = False
        for i, device in enumerate(unique):
            sid = f"cam{i}"
            rs_info = rs_device_map.get(device)
            if rs_info and rs_info["serial"] not in rs_serials_assigned:
                rs_serials_assigned.add(rs_info["serial"])
                self._sources[sid] = CameraModule.RealSenseSource(
                    sid, rs_info["serial"], device,
                    self._width, self._height, self._fps,
                    rs_info.get("name", ""))
                log.info("[Camera] auto-detect: %s → RealSense %s (serial=%s)",
                         sid, rs_info["name"], rs_info["serial"])
            elif device in ob_device_set and not ob_assigned:
                ob_assigned = True
                self._sources[sid] = CameraModule.OrbbecSource(
                    sid, device, self._width, self._height, self._fps)
                log.info("[Camera] auto-detect: %s → Orbbec Femto Bolt (mode=%s)",
                         sid, "color")
            else:
                self._sources[sid] = CameraModule.CameraSource(
                    sid, "usb", device, self._width, self._height, self._fps)
    def rescan(self) -> List[Dict[str, Any]]:
        """Re-scan for cameras and start sources for newly discovered devices."""
        detected = _detect_cameras()
        unique = _deduplicate_by_usb_device(detected)

        rs_device_map: Dict[str, Dict[str, Any]] = {}
        if _HAS_REALSENSE:
            try:
                for rs_dev in detect_realsense_devices(detected):
                    for node in rs_dev.get("v4l2_nodes", []):
                        rs_device_map[node] = rs_dev
            except Exception:
                pass

        ob_device_set: set = set()
        if _HAS_ORBBEC_DETECT:
            try:
                for ob_dev in detect_orbbec_devices(detected):
                    for node in ob_dev.get("v4l2_nodes", []):
                        ob_device_set.add(node)
            except Exception:
                pass

        rs_assigned: set = set()
        ob_assigned = False
        new_count = 0
        with self._lock:
            for i, device in enumerate(unique):
                sid = f"cam{i}"
                if sid in self._sources:
                    continue
                rs_info = rs_device_map.get(device)
                if rs_info and rs_info["serial"] not in rs_assigned:
                    rs_assigned.add(rs_info["serial"])
                    src = CameraModule.RealSenseSource(
                        sid, rs_info["serial"], device,
                        self._width, self._height, self._fps,
                        rs_info.get("name", ""))
                elif device in ob_device_set and not ob_assigned:
                    ob_assigned = True
                    src = CameraModule.OrbbecSource(
                        sid, device, self._width, self._height, self._fps)
                else:
                    src = CameraModule.CameraSource(
                        sid, "usb", device, self._width, self._height, self._fps)
                self._sources[sid] = src
                src.start()
                new_count += 1
                log.info("[Camera] rescan: added %s → %s", sid, device)
        log.info("[Camera] rescan: %d new sources (total=%d)", new_count, len(self._sources))
        return self.get_device_list()
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
        with self._lock:
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
        with self._lock:
            return [
                {
                    "id": sid,
                    "active": s._running,
                    "source_type": s._type,
                    "device_name": s._device_name,
                    "device": s._device,
                    "serial": getattr(s, "_serial", ""),
                    "camera_mode": getattr(s, "get_mode", lambda: None)(),
                }
                for sid, s in self._sources.items()
            ]

    def resolve_source(self, hint: str) -> Optional[str]:
        """Map a source id, serial number, or model substring to a source id.

        Source ids are assigned by enumeration order, so they shift when a
        camera is replugged.  Serials don't — callers that need to pin a
        specific physical camera should pass one.
        """
        if not hint:
            return None
        hint = hint.strip()
        with self._lock:
            if hint in self._sources:
                return hint
            for sid, s in self._sources.items():
                if getattr(s, "_serial", "") == hint:
                    return sid
            needle = hint.lower()
            for sid, s in self._sources.items():
                if needle in (s._device_name or "").lower():
                    return sid
        return None

    # ── SocketIO handlers ─────────────────────────────────────────────────

    def get_socketio_handlers(self) -> Dict[str, callable]:
        return {
            "camera_reset": self._handle_camera_reset,
            "camera_list_devices": self._handle_list_devices,
            "camera_scan": self._handle_scan,
            "camera_set_mode": self._handle_set_mode,
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

    def _handle_scan(self, _data: dict, emit) -> None:
        log.info("[Camera] manual rescan requested from GUI")
        devices = self.rescan()
        emit("camera_device_list", {"devices": devices})

    def _handle_set_mode(self, data: dict, emit) -> None:
        device = data.get("device", "") if data else ""
        mode = data.get("mode", "color") if data else "color"
        source = self._sources.get(device)
        if not source:
            emit("camera_mode_result", {"ok": False,
                 "msg": f"Unknown device: {device}"})
            return
        if not hasattr(source, "set_mode"):
            emit("camera_mode_result", {"ok": False,
                 "msg": "Mode switching not supported for this device"})
            return
        try:
            source.set_mode(mode)
            emit("camera_mode_result", {"ok": True, "device": device,
                 "mode": source.get_mode()})
            emit("camera_device_list", {"devices": self.get_device_list()})
        except Exception as exc:
            log.error("[Camera:%s] mode switch failed: %s", source._id, exc)
            emit("camera_mode_result", {"ok": False, "msg": str(exc)})