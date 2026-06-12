"""Camera module — USB camera with WebRTC (SRS RTMP) + MJPEG fallback.

Auto-detects real video capture devices (skips codec-only nodes like cedrus).
Monitors frame health and restarts ffmpeg on signal loss.  Cycles through
available devices when the active one fails repeatedly.

Streaming paths:
  Primary:   /dev/videoN → ffmpeg (H.264) → RTMP → SRS → WebRTC → browser
  Fallback:  /dev/videoN → ffmpeg (MJPEG) → pipe → /stream HTTP (MJPEG)
"""

import logging
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from robot_control.module_base import RobotModule

log = logging.getLogger(__name__)

# Max consecutive ffmpeg restarts before trying the next camera device.
_MAX_RESTARTS = 3
# Seconds without a frame before declaring signal loss.
_FRAME_TIMEOUT = 5.0


def _detect_cameras() -> List[str]:
    """Return /dev/video paths that are real video capture devices.

    Uses v4l2-ctl to filter out codec-only nodes (e.g. cedrus on Allwinner)
    and metadata-only sub-devices (UVC cameras often expose /dev/video1 for
    video and /dev/video2 for metadata — we only want the video one).
    """
    import os
    devices = []
    for i in range(4):  # check /dev/video0 .. /dev/video3
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
            # Skip codec/M2M devices (cedrus).
            if "Memory-to-Memory" in out or "Memory to Memory" in out:
                log.info("[Camera] skip %s — codec/M2M device", path)
                continue
            # Must have actual Video Capture in Device Caps (Capabilities
            # alone is not enough — metadata sub-devices report Video Capture
            # in Capabilities but not Device Caps).
            dc_section = out.split("Device Caps", 1)
            if len(dc_section) > 1 and "Video Capture" in dc_section[1]:
                devices.append(path)
                continue
            if "Video Capture" in out and "Device Capabilities" not in out:
                # Older kernels that don't print Device Caps — trust it.
                devices.append(path)
                continue
            # v4l2-ctl succeeded but this isn't a video capture device
            # (metadata-only sub-device, etc.) — skip, don't fall through.
            log.info("[Camera] skip %s — no Video Capture in Device Caps", path)
            continue
        except (FileNotFoundError, subprocess.SubprocessError):
            pass  # v4l2-ctl missing or failed — fall through to sysfs

        if checked_v4l2:
            continue  # already determined it's not a capture device

        # Sysfs fallback: check driver name to filter codec nodes (cedrus).
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


class CameraModule(RobotModule):
    """USB camera — WebRTC primary, MJPEG fallback, auto device cycling."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        self._device = cfg.get("device", "/dev/video0")
        self._width = int(cfg.get("width", 800))
        self._height = int(cfg.get("height", 600))
        self._fps = int(cfg.get("fps", 15))
        self._rtmp_url = cfg.get("rtmp_url", "rtmp://localhost/live/cam")

        self._cond = threading.Condition()
        self._client_seq: Dict[int, int] = {}
        self._frame: Optional[bytes] = None
        self._frame_seq: int = 0
        self._last_frame_time = 0.0
        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._running = False
        self._restart_count = 0
        self._device_idx = 0
        self._available_devices: List[str] = []

    # ── RobotModule metadata ─────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "camera"

    @property
    def display_name(self) -> str:
        return "摄像头"

    def get_state_schema(self) -> Dict[str, Any]:
        return {
            "connected": False,
            "client_count": 0,
            "frame_bytes": 0,
            "device": self._device,
            "signal": "unknown",
        }

    def get_socketio_handlers(self) -> Dict[str, callable]:
        return {"camera_reset": self._handle_camera_reset}

    def _handle_camera_reset(self, data: dict, emit) -> None:
        """Reset camera: kill ffmpeg, USB reset, restart ffmpeg."""
        log.info("[Camera] Manual reset requested from GUI")
        self._restart_count = 0
        self._reset_usb()
        # Force-restart ffmpeg on the (possibly new) device.
        self._choose_active_device()
        self._start_ffmpeg()
        emit("camera_reset_result", {"ok": True})

    # ── lifecycle ───────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True

        # Refresh device list on each start.
        self._available_devices = _detect_cameras()
        if not self._available_devices:
            log.warning("[Camera] No video capture devices found! "
                        "Camera module running in standby (waiting for USB camera). "
                        "Check: lsusb, ls /dev/v4l/by-id/")
            # Start a background re-scanner for hotplug detection.
            t = threading.Thread(target=self._hotplug_watch, daemon=True, name="cam-hotplug")
            t.start()
            return
        log.info("[Camera] devices found: %s", self._available_devices)

        self._choose_active_device()
        self._start_ffmpeg()

        t = threading.Thread(target=self._frame_loop, daemon=True, name="cam-loop")
        t.start()
        log.info("[Camera] Module started — device=%s, %dx%d", self._device, self._width, self._height)

    def _hotplug_watch(self) -> None:
        """Periodically re-scan for USB cameras (hotplug detection)."""
        while True:
            time.sleep(10)
            devices = _detect_cameras()
            if devices:
                log.info("[Camera] Hotplug detected: %s — restarting", devices)
                self._available_devices = devices
                self._device_idx = 0
                self._choose_active_device()
                self._running = True
                self._start_ffmpeg()
                t = threading.Thread(target=self._frame_loop, daemon=True, name="cam-loop")
                t.start()
                return  # Exit hotplug watch, normal frame loop takes over

    def stop(self) -> None:
        with self._cond:
            self._running = False
            self._frame = None
            self._client_seq.clear()
            self._cond.notify_all()
        self._kill_ffmpeg()
        log.info("[Camera] Module stopped")

    def emergency_stop(self) -> None:
        self.stop()

    # ── state ───────────────────────────────────────────────────────────

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

    # ── subscriber API ──────────────────────────────────────────────────

    _next_cid: int = 0

    def subscribe(self) -> int:
        """Return a client-id for use with wait_frame / unsubscribe."""
        self._ensure_running()
        cid = self._next_cid
        CameraModule._next_cid = cid + 1
        with self._cond:
            self._client_seq[cid] = self._frame_seq
        return cid

    def unsubscribe(self, cid: int) -> None:
        with self._cond:
            self._client_seq.pop(cid, None)

    def wait_frame(self, cid: int, timeout: float = 5.0) -> Tuple[Optional[bytes], int]:
        """Block until a newer frame is available. Returns (frame, seq) or (None, seq) on timeout."""
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

    # ── internals: device selection ─────────────────────────────────────

    def _choose_active_device(self) -> None:
        if self._available_devices:
            self._device = self._available_devices[self._device_idx % len(self._available_devices)]
        log.info("[Camera] active device → %s", self._device)

    def _cycle_device(self) -> None:
        """Move to the next available camera device."""
        self._restart_count = 0
        if len(self._available_devices) <= 1:
            # Only one device — try USB reset before retrying.
            log.info("[Camera] single device — USB reset then retry %s", self._device)
            self._reset_usb()
            return
        self._device_idx = (self._device_idx + 1) % len(self._available_devices)
        self._choose_active_device()
        log.warning("[Camera] cycled to next device: %s", self._device)

    def _reset_usb(self) -> None:
        """Unbind and rebind the USB device backing the active camera.

        This recovers the camera from UVC control failures (EPIPE on
        GET_DEF queries) that leave it in a wedged state where the
        device opens but produces no frames.
        """
        import os as _os
        dev_node = _os.path.basename(self._device)  # e.g. "video1"
        dev_path = f"/sys/class/video4linux/{dev_node}/device"
        usb_dev = None
        try:
            # Walk up to find the USB device entry (contains 'driver' symlink).
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
            log.warning("[Camera] USB reset: could not find USB device for %s", self._device)
            return
        log.info("[Camera] USB reset: unbind %s from %s …", usb_dev, driver)
        try:
            with open(f"/sys/bus/usb/drivers/{driver}/unbind", "w") as fh:
                fh.write(usb_dev)
            time.sleep(2)
            with open(f"/sys/bus/usb/drivers/{driver}/bind", "w") as fh:
                fh.write(usb_dev)
            time.sleep(1)
            # Re-detect available devices (device numbering may change).
            self._available_devices = _detect_cameras()
            log.info("[Camera] USB reset: bind complete, devices=%s", self._available_devices)
        except OSError as exc:
            log.warning("[Camera] USB reset failed: %s", exc)

    # ── internals: unified ffmpeg (MJPEG pipe + RTMP push) ──────────────

    def _start_ffmpeg(self) -> None:
        """Launch ffmpeg with MJPEG passthrough to pipe:1 (low-latency).

        Single device open avoids contention on USB cameras that don't
        support concurrent access.
        """
        self._kill_ffmpeg()
        # Force-release the device from any orphaned processes (e.g. stale
        # ffmpeg from a previous service run killed by systemd).
        import os as _os
        _os.system(f"fuser -k {self._device} 2>/dev/null")
        time.sleep(0.5)
        cmd = [
            "ffmpeg",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-analyzeduration", "0",
            "-probesize", "32",
            "-f", "v4l2",
            "-input_format", "mjpeg",
            "-framerate", "30",
            "-video_size", f"{self._width}x{self._height}",
            "-i", self._device,
            "-c:v", "copy",
            "-f", "image2pipe", "pipe:1",
        ]
        try:
            self._ffmpeg_proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                bufsize=0)
            # Drain stderr in a background thread so the pipe doesn't fill.
            self._stderr_lines: List[str] = []
            def _drain_stderr():
                try:
                    for line in self._ffmpeg_proc.stderr:
                        decoded = line.decode("utf-8", errors="replace").rstrip()
                        self._stderr_lines.append(decoded)
                        if len(self._stderr_lines) > 200:
                            self._stderr_lines.pop(0)
                except Exception:
                    pass
            t = threading.Thread(target=_drain_stderr, daemon=True, name="cam-stderr")
            t.start()
            self._last_frame_time = time.time()
            log.info("[Camera] ffmpeg started (pid=%d) on %s → MJPEG pipe",
                     self._ffmpeg_proc.pid, self._device)
        except Exception as exc:
            log.error("[Camera] ffmpeg launch failed: %s", exc)
            self._ffmpeg_proc = None

    def _kill_ffmpeg(self) -> None:
        if self._ffmpeg_proc:
            # Dump any accumulated stderr for diagnostics.
            stderr_lines = getattr(self, "_stderr_lines", []) or []
            if stderr_lines:
                log.warning("[Camera] ffmpeg stderr (last 5 lines):\n%s",
                            "\n".join(stderr_lines[-5:]))
                self._stderr_lines = []
            try:
                self._ffmpeg_proc.kill()
                self._ffmpeg_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                log.warning("[Camera] ffmpeg kill timed out — force-releasing %s", self._device)
                import os
                os.system(f"fuser -k {self._device} 2>/dev/null")
            except Exception:
                pass
            self._ffmpeg_proc = None

    def _frame_loop(self) -> None:
        """Read MJPEG frames from ffmpeg stdout, broadcast, watch for signal loss.

        Uses select() with a 1-second timeout so that signal-loss detection
        can run even when ffmpeg produces no output (the old blocking-read
        design would hang forever on a silent ffmpeg).
        """
        import select as _select
        import os as _os
        buf = b""
        frame_n = 0
        while self._running:
            proc = self._ffmpeg_proc
            if proc is None or proc.stdout is None:
                time.sleep(1)
                continue
            fd = proc.stdout.fileno()

            # Wait up to 1 s for data; wake periodically to check health.
            try:
                ready, _, _ = _select.select([fd], [], [], 1.0)
            except (ValueError, OSError, AttributeError):
                time.sleep(1)
                continue

            if not ready:
                # No data within 1 s — check that ffmpeg is still alive.
                if proc.poll() is not None:
                    if self._running:
                        self._restart_count += 1
                        log.warning("[Camera] ffmpeg exited (restart #%d)", self._restart_count)
                        if self._restart_count >= _MAX_RESTARTS:
                            self._cycle_device()
                        time.sleep(1)
                        self._start_ffmpeg()
                        buf = b""
                        frame_n = 0
                    continue
                # Alive but silent — watch for signal loss.
                if self._last_frame_time > 0:
                    elapsed = time.time() - self._last_frame_time
                    if elapsed > _FRAME_TIMEOUT:
                        log.warning("[Camera] signal lost — %.1fs since last frame, restarting", elapsed)
                        self._restart_count += 1
                        if self._restart_count >= _MAX_RESTARTS:
                            self._cycle_device()
                        self._start_ffmpeg()
                        buf = b""
                        frame_n = 0
                continue

            # Data waiting — read it.
            try:
                chunk = _os.read(fd, 65536)
            except (OSError, ValueError):
                chunk = None

            if not chunk:
                # EOF — ffmpeg pipe closed.
                if self._running:
                    self._restart_count += 1
                    log.warning("[Camera] ffmpeg exited (restart #%d)", self._restart_count)
                    if self._restart_count >= _MAX_RESTARTS:
                        self._cycle_device()
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
                    log.info("[Camera] frame #%d (%d bytes), %d client(s)",
                             frame_n, len(frame), self.client_count())

    # ── helpers ─────────────────────────────────────────────────────────

    def _ensure_running(self) -> None:
        with self._cond:
            need_start = self._ffmpeg_proc is None or self._ffmpeg_proc.poll() is not None
        if need_start:
            self._start_ffmpeg()
        if not self._running:
            self._running = True
            t = threading.Thread(target=self._frame_loop, daemon=True, name="cam-loop")
            t.start()
