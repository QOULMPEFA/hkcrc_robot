"""RealSensePipeline — SDK capture + in-process JPEG encoding.

Captures RGB frames via pyrealsense2 (NOT V4L2), encodes to JPEG using
Pillow, and exposes frames through a thread-safe queue for consumption
by RealSenseSource._frame_loop().
"""

import io
import logging
import queue
import threading
import time
from typing import Optional

import numpy as np
from PIL import Image

log = logging.getLogger(__name__)


class RealSensePipeline:
    """Manages a pyrealsense2 pipeline for one RealSense device.

    Captures RGB frames via the SDK, encodes to JPEG in-process using
    Pillow, and exposes them through a thread-safe bounded queue.
    """

    def __init__(self, serial: str, width: int = 640, height: int = 480,
                 fps: int = 30, jpeg_quality: int = 85):
        self._serial = serial
        self._width = width
        self._height = height
        self._fps = fps
        self._jpeg_quality = jpeg_quality
        self._pipeline = None
        self._config = None
        self._running = False
        self._frame_queue = queue.Queue(maxsize=2)
        self._thread: Optional[threading.Thread] = None
        self._device_name = ""

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        import pyrealsense2 as rs

        ctx = rs.context()
        dev = None
        for d in ctx.devices:
            if d.get_info(rs.camera_info.serial_number) == self._serial:
                dev = d
                break
        if dev is None:
            raise RuntimeError(f"RealSense device {self._serial} not found")

        self._device_name = dev.get_info(rs.camera_info.name)

        self._pipeline = rs.pipeline(ctx)
        self._config = rs.config()
        self._config.enable_device(self._serial)
        self._config.enable_stream(
            rs.stream.color, self._width, self._height,
            rs.format.rgb8, self._fps,
        )
        self._pipeline.start(self._config)

        # Discard initial frames until a good one arrives (auto-exposure settling).
        # D455 can take >5 s on a busy bus; be patient.
        _startup_deadline = time.time() + 10.0
        _settled = False
        while time.time() < _startup_deadline:
            try:
                self._pipeline.wait_for_frames(timeout_ms=1000)
                _settled = True
                break
            except RuntimeError:
                time.sleep(0.5)
        if not _settled:
            raise RuntimeError(
                f"RealSense {self._serial[:8]}: no frames within {10:.0f}s — "
                f"check USB connection and power"
            )

        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True,
                                        name=f"rs-capture-{self._serial[:8]}")
        self._thread.start()
        log.info("[RealSense:%s] pipeline started — %s %dx%d@%d",
                 self._serial[:8], self._device_name, self._width, self._height, self._fps)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        if self._pipeline:
            try:
                self._pipeline.stop()
            except Exception:
                pass
        self._pipeline = None
        self._config = None
        log.info("[RealSense:%s] pipeline stopped", self._serial[:8])

    def get_device_name(self) -> str:
        return self._device_name

    # ── frame access ─────────────────────────────────────────────────────

    def get_frame(self, timeout: float = 1.0) -> Optional[bytes]:
        """Return a single JPEG frame (raw bytes, no multipart wrapper)."""
        try:
            return self._frame_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def is_running(self) -> bool:
        return self._running

    # ── internal ─────────────────────────────────────────────────────────

    def _capture_loop(self) -> None:
        pipeline = self._pipeline
        frame_queue = self._frame_queue
        quality = self._jpeg_quality
        frame_n = 0

        while self._running:
            try:
                frames = pipeline.wait_for_frames(timeout_ms=1000)
            except RuntimeError:
                if self._running:
                    log.warning("[RealSense:%s] wait_for_frames error", self._serial[:8])
                time.sleep(0.1)
                continue

            color_frame = frames.get_color_frame()
            if not color_frame:
                continue

            arr = np.asanyarray(color_frame.get_data())
            img = Image.fromarray(arr, "RGB")
            buf = io.BytesIO()
            try:
                img.save(buf, format="JPEG", quality=quality)
            except OSError:
                continue

            jpeg_bytes = buf.getvalue()
            try:
                frame_queue.put_nowait(jpeg_bytes)
            except queue.Full:
                try:
                    frame_queue.get_nowait()
                    frame_queue.put_nowait(jpeg_bytes)
                except queue.Empty:
                    pass

            frame_n += 1
            if frame_n % 150 == 1:
                log.info("[RealSense:%s] frame #%d (%d bytes)",
                         self._serial[:8], frame_n, len(jpeg_bytes))
