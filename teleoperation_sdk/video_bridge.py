"""MJPEG → H.264 UDP video bridge for Quest 3 native hardware decoding.

Receives MJPEG frames from the gimbal camera HTTP stream and transcodes
them to H.264 via ffmpeg, sending to a UDP port that the Quest 3
XRoboToolkit APK listens on (via adb reverse tunnel).

Usage:
  from teleoperation_sdk.video_bridge import VideoBridge
  bridge = VideoBridge(mjpeg_url="http://127.0.0.1:6080/stream/cam0",
                       udp_port=12345)
  bridge.start()
  ...
  bridge.stop()
"""

import logging
import subprocess
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)


class VideoBridge:
    """Manages an ffmpeg subprocess that transcodes MJPEG to H.264/UDP."""

    def __init__(
        self,
        mjpeg_url: str = "http://127.0.0.1:6080/stream/cam0",
        udp_host: str = "127.0.0.1",
        udp_port: int = 12345,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        bitrate: str = "2M",
        preset: str = "ultrafast",
        tune: str = "zerolatency",
    ):
        self._mjpeg_url = mjpeg_url
        self._udp_host = udp_host
        self._udp_port = udp_port
        self._width = width
        self._height = height
        self._fps = fps
        self._bitrate = bitrate
        self._preset = preset
        self._tune = tune
        self._process: Optional[subprocess.Popen] = None
        self._running = False
        self._restart_thread: Optional[threading.Thread] = None

    def start(self) -> bool:
        """Start the ffmpeg transcoding process. Returns True on success."""
        if self._running:
            return True
        self._running = True
        ok = self._launch_ffmpeg()
        if ok:
            self._restart_thread = threading.Thread(
                target=self._watchdog, daemon=True, name="video-bridge-wd")
            self._restart_thread.start()
        return ok

    def stop(self) -> None:
        """Stop ffmpeg and the watchdog thread."""
        self._running = False
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
        if self._restart_thread and self._restart_thread.is_alive():
            self._restart_thread.join(timeout=2.0)
        log.info("[VideoBridge] Stopped")

    def _launch_ffmpeg(self) -> bool:
        """Build and run the ffmpeg command."""
        cmd = [
            "ffmpeg",
            "-hide_banner", "-loglevel", "warning",
            "-f", "mjpeg",
            "-use_wallclock_as_timestamps", "1",
            "-i", self._mjpeg_url,
            "-c:v", "libx264",
            "-preset", self._preset,
            "-tune", self._tune,
            "-b:v", self._bitrate,
            "-pix_fmt", "yuv420p",
            "-s", f"{self._width}x{self._height}",
            "-r", str(self._fps),
            "-g", str(self._fps * 2),  # GOP = 2s
            "-f", "mpegts",
            f"udp://{self._udp_host}:{self._udp_port}",
        ]

        log.info("[VideoBridge] Starting: MJPEG(%s) → H.264/UDP(:%d) %dx%d@%dfps %s",
                 self._mjpeg_url, self._udp_port, self._width, self._height, self._fps, self._bitrate)

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            # Give it a moment to start
            time.sleep(0.5)
            if self._process.poll() is not None:
                stderr = self._process.stderr.read().decode("utf-8", errors="replace")
                log.error("[VideoBridge] ffmpeg exited immediately: %s", stderr[:500])
                return False
            log.info("[VideoBridge] ffmpeg PID %d running", self._process.pid)
            return True
        except FileNotFoundError:
            log.error("[VideoBridge] ffmpeg not found — install ffmpeg first")
            return False
        except Exception as e:
            log.error("[VideoBridge] Launch failed: %s", e)
            return False

    def _watchdog(self) -> None:
        """Restart ffmpeg if it crashes."""
        while self._running:
            if self._process is not None:
                rc = self._process.poll()
                if rc is not None:
                    log.warning("[VideoBridge] ffmpeg exited (rc=%d), restarting in 1s...", rc)
                    time.sleep(1)
                    if self._running:
                        self._launch_ffmpeg()
            time.sleep(1)

    @property
    def is_running(self) -> bool:
        return self._running and self._process is not None and self._process.poll() is None
