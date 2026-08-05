"""OrbbecPipeline — K4A-based depth/stereo capture + in-process JPEG encoding.

Captures depth (Z16) or IR stereo frames via the K4A-compatible wrapper,
encodes to JPEG using Pillow, and exposes frames through a thread-safe queue.
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

# ── Turbo colormap LUT (256-entry, 3-channel) ─────────────────────────────
# Hand-rolled LUT approximating the Turbo colormap — no matplotlib needed.
# Maps normalized value [0, 1] → [R, G, B] (0-255).

_TURBO_LUT = np.array([
    [48, 18, 59], [50, 21, 67], [51, 24, 75], [52, 27, 83],
    [53, 30, 91], [54, 33, 99], [55, 36, 107], [56, 39, 115],
    [57, 42, 123], [58, 45, 131], [59, 48, 139], [59, 51, 147],
    [60, 54, 155], [61, 57, 163], [62, 60, 171], [62, 63, 179],
    [63, 66, 186], [63, 69, 193], [64, 72, 199], [64, 75, 205],
    [65, 78, 210], [65, 81, 214], [65, 84, 218], [66, 87, 221],
    [66, 90, 223], [66, 93, 225], [66, 96, 226], [67, 99, 227],
    [67, 102, 227], [67, 105, 227], [67, 108, 227], [67, 111, 226],
    [67, 114, 225], [67, 117, 224], [67, 120, 222], [67, 123, 220],
    [67, 126, 218], [67, 129, 216], [67, 132, 214], [67, 135, 212],
    [67, 138, 209], [67, 141, 207], [67, 144, 205], [67, 147, 203],
    [67, 150, 201], [67, 153, 199], [67, 156, 197], [67, 159, 195],
    [67, 162, 193], [66, 165, 192], [66, 168, 190], [66, 171, 189],
    [66, 174, 188], [66, 177, 187], [66, 180, 186], [65, 183, 185],
    [65, 186, 185], [65, 189, 184], [65, 192, 184], [65, 195, 184],
    [65, 198, 184], [64, 201, 184], [64, 204, 184], [64, 207, 185],
    [64, 210, 186], [64, 213, 187], [64, 215, 188], [64, 218, 190],
    [64, 221, 192], [64, 223, 194], [63, 225, 196], [63, 227, 199],
    [63, 228, 202], [63, 229, 205], [63, 230, 208], [63, 231, 212],
    [63, 231, 215], [63, 232, 219], [63, 232, 223], [63, 232, 227],
    [62, 232, 231], [62, 232, 235], [62, 231, 239], [62, 230, 243],
    [62, 229, 246], [62, 227, 249], [61, 225, 251], [61, 223, 252],
    [61, 220, 252], [60, 217, 251], [60, 214, 249], [60, 211, 246],
    [60, 208, 243], [60, 205, 239], [60, 202, 235], [60, 199, 231],
    [60, 196, 226], [60, 193, 222], [60, 190, 217], [60, 187, 213],
    [60, 184, 208], [60, 181, 204], [60, 178, 200], [60, 175, 196],
    [59, 172, 192], [59, 169, 188], [59, 166, 185], [59, 163, 182],
    [59, 160, 179], [59, 157, 176], [59, 154, 173], [59, 151, 171],
    [59, 148, 169], [59, 145, 167], [59, 142, 165], [58, 139, 163],
    [58, 136, 162], [58, 133, 160], [58, 130, 159], [58, 127, 158],
    [58, 124, 157], [58, 121, 156], [58, 118, 155], [58, 115, 154],
    [58, 112, 153], [58, 109, 152], [58, 106, 151], [58, 103, 150],
    [58, 100, 149], [58, 97, 148], [57, 94, 146], [57, 91, 144],
    [57, 88, 142], [57, 85, 140], [57, 82, 137], [57, 79, 134],
    [57, 76, 131], [57, 73, 127], [57, 70, 123], [57, 67, 119],
    [57, 64, 115], [56, 61, 110], [56, 58, 105], [56, 55, 100],
    [56, 52, 95], [56, 49, 89], [55, 46, 84], [55, 43, 78],
    [54, 40, 72], [54, 37, 66], [53, 34, 60], [52, 31, 54],
    [51, 28, 48], [50, 25, 42], [48, 22, 36], [47, 19, 30],
    [45, 16, 24], [43, 13, 18], [41, 10, 13], [39, 7, 8],
    [36, 5, 4], [33, 3, 1], [30, 1, 0], [27, 0, 0],
    [24, 0, 0], [21, 0, 0], [17, 0, 1], [14, 0, 2],
    [11, 0, 2], [8, 0, 3], [6, 0, 3], [4, 0, 3],
    [2, 0, 2], [1, 0, 2], [0, 0, 1], [0, 0, 1],
    [0, 0, 1], [0, 0, 1], [0, 0, 1], [0, 0, 1],
    [0, 0, 1], [0, 0, 1], [0, 0, 1], [0, 0, 1],
    [0, 0, 1], [0, 0, 1], [0, 0, 1], [0, 0, 1],
    [0, 0, 1], [0, 0, 1], [0, 0, 1], [0, 0, 1],
    [0, 0, 1], [0, 0, 1], [0, 0, 2], [1, 0, 2],
    [1, 0, 2], [2, 0, 3], [3, 0, 3], [4, 0, 3],
    [5, 0, 3], [6, 0, 3], [7, 0, 3], [8, 0, 3],
    [9, 0, 3], [10, 0, 3], [11, 0, 3], [12, 0, 3],
    [13, 0, 3], [14, 0, 3], [15, 0, 3], [16, 0, 3],
    [17, 0, 3], [18, 0, 3], [19, 0, 3], [20, 0, 3],
    [21, 0, 3], [22, 0, 2], [23, 0, 2], [24, 0, 2],
    [25, 0, 2], [26, 0, 2], [27, 0, 2], [28, 0, 2],
    [29, 0, 2], [30, 0, 2], [31, 0, 2], [32, 0, 2],
    [33, 0, 2], [34, 0, 2], [35, 0, 2], [36, 0, 2],
    [37, 0, 2], [38, 0, 2], [39, 0, 2], [40, 0, 2],
    [41, 0, 2], [42, 0, 2], [43, 0, 2], [44, 0, 2],
    [45, 0, 2], [46, 0, 2], [47, 0, 2], [48, 0, 2],
    [48, 0, 2], [49, 0, 2], [50, 0, 2], [50, 0, 2],
    [51, 1, 2], [52, 1, 2], [52, 1, 3], [53, 2, 3],
    [54, 2, 3], [54, 2, 3], [55, 3, 4], [55, 3, 4],
    [56, 4, 4], [57, 4, 5], [57, 5, 5], [58, 5, 6],
    [58, 6, 6], [59, 7, 7], [59, 7, 7], [60, 8, 8],
    [60, 8, 9], [61, 9, 9], [61, 10, 10], [62, 11, 11],
    [62, 11, 12], [63, 12, 12], [63, 13, 13], [63, 14, 14],
    [64, 15, 15], [64, 15, 16], [65, 16, 17], [65, 17, 18],
    [65, 18, 19], [66, 19, 20], [66, 20, 21], [66, 21, 22],
    [67, 22, 23], [67, 23, 25], [67, 24, 26], [68, 25, 27],
    [68, 26, 29], [68, 28, 30], [69, 29, 32], [69, 30, 33],
    [69, 31, 35], [69, 33, 37], [70, 34, 38], [70, 35, 40],
    [70, 37, 42], [70, 38, 43], [71, 39, 45], [71, 41, 47],
    [71, 42, 49], [71, 44, 51], [71, 45, 53], [72, 47, 55],
    [72, 48, 57], [72, 50, 59], [72, 52, 61], [72, 53, 63],
    [72, 55, 65], [72, 57, 67], [73, 58, 69], [73, 60, 71],
    [73, 62, 73], [73, 63, 75], [73, 65, 77], [73, 67, 79],
    [73, 69, 81], [73, 71, 83], [73, 72, 85], [73, 74, 87],
    [73, 76, 89], [73, 78, 91], [73, 80, 93], [73, 82, 94],
    [73, 84, 96], [73, 85, 98], [73, 87, 100], [73, 89, 102],
    [73, 91, 104], [72, 93, 106], [72, 95, 108], [72, 97, 110],
    [72, 99, 112], [72, 101, 114], [72, 103, 116], [72, 105, 118],
    [71, 107, 120], [71, 109, 122], [71, 111, 124], [71, 113, 126],
    [71, 115, 128], [70, 117, 130], [70, 119, 132], [70, 121, 134],
    [70, 123, 136], [69, 125, 138], [69, 127, 140], [69, 129, 141],
    [68, 131, 143], [68, 133, 145], [68, 135, 147], [67, 137, 149],
    [67, 139, 151], [67, 141, 153], [66, 143, 155], [66, 145, 157],
    [65, 147, 159], [65, 149, 161], [65, 151, 163], [64, 153, 165],
    [64, 155, 167], [63, 157, 169], [63, 159, 171], [62, 161, 173],
    [62, 163, 175], [61, 165, 177], [61, 167, 179], [60, 169, 181],
    [60, 171, 183], [59, 173, 185], [58, 175, 187], [58, 177, 189],
    [57, 179, 191], [57, 181, 193], [56, 183, 195], [55, 185, 197],
    [55, 187, 199], [54, 189, 201], [53, 191, 203], [52, 193, 205],
    [52, 195, 207], [51, 197, 209], [50, 199, 210], [49, 201, 211],
    [49, 202, 212], [48, 203, 213], [47, 204, 213], [46, 205, 214],
    [46, 206, 214], [45, 206, 215], [44, 207, 215], [43, 208, 216],
    [43, 208, 216], [42, 209, 217], [41, 210, 217], [41, 210, 218],
    [40, 211, 218], [39, 212, 219], [39, 212, 219], [38, 213, 220],
    [37, 214, 220], [37, 214, 221], [36, 215, 221], [35, 216, 222],
    [35, 216, 222], [34, 217, 223], [34, 218, 223], [33, 218, 224],
    [33, 219, 224], [32, 220, 225], [32, 221, 225], [31, 221, 226],
    [31, 222, 226], [30, 223, 227], [30, 224, 227], [30, 225, 228],
    [29, 225, 228], [29, 226, 229], [29, 227, 229], [28, 228, 230],
    [28, 229, 230], [28, 230, 231], [28, 231, 231], [27, 232, 232],
    [27, 233, 232], [27, 234, 233], [27, 235, 233], [27, 236, 234],
    [27, 237, 234], [27, 238, 235], [27, 239, 235], [27, 239, 236],
    [28, 239, 236], [28, 239, 237], [28, 239, 237], [29, 239, 238],
    [29, 239, 238], [30, 239, 239], [30, 239, 239], [31, 239, 240],
    [32, 239, 240], [33, 239, 241], [34, 239, 241], [35, 239, 242],
    [36, 239, 242], [37, 239, 243], [38, 239, 243], [39, 239, 244],
    [40, 239, 244], [41, 239, 245], [42, 239, 245], [43, 239, 246],
    [44, 238, 246], [45, 238, 247], [47, 238, 247], [48, 238, 248],
    [49, 238, 248], [50, 238, 249], [51, 238, 249], [53, 238, 250],
    [54, 238, 250], [55, 238, 251], [56, 237, 251], [58, 237, 252],
    [59, 237, 252], [60, 237, 253], [62, 237, 253], [63, 237, 254],
    [65, 237, 254], [66, 237, 255], [68, 237, 255], [69, 237, 255],
    [71, 237, 255], [72, 237, 255], [74, 237, 255], [75, 237, 255],
    [77, 237, 255], [78, 236, 255], [80, 236, 255], [82, 236, 255],
    [83, 236, 255], [85, 236, 255], [86, 235, 255], [88, 235, 255],
    [89, 235, 255], [91, 235, 255], [93, 235, 255], [94, 234, 255],
    [96, 234, 255], [97, 234, 255], [99, 234, 255], [101, 233, 255],
    [102, 233, 255], [104, 233, 255], [106, 233, 255], [107, 232, 255],
    [109, 232, 255], [111, 232, 255], [112, 232, 255], [114, 231, 255],
    [116, 231, 255], [117, 231, 255], [119, 231, 255], [121, 230, 255],
    [122, 230, 255], [124, 230, 255], [126, 229, 255], [127, 229, 255],
    [129, 229, 255], [131, 229, 254], [133, 228, 254], [134, 228, 254],
    [136, 228, 254], [138, 227, 254], [139, 227, 253], [141, 227, 253],
    [143, 227, 253], [145, 226, 253], [146, 226, 252], [148, 226, 252],
    [150, 225, 252], [151, 225, 251], [153, 225, 251], [155, 225, 251],
    [157, 224, 250], [158, 224, 250], [160, 224, 250], [162, 223, 249],
    [163, 223, 249], [165, 223, 249], [167, 222, 248], [169, 222, 248],
    [170, 222, 247], [172, 221, 247], [174, 221, 247], [175, 221, 246],
    [177, 220, 246], [179, 220, 245], [181, 220, 245], [182, 219, 244],
    [184, 219, 244], [186, 218, 243], [187, 218, 243], [189, 218, 242],
    [191, 217, 242], [192, 217, 241], [194, 216, 241], [196, 216, 240],
    [197, 216, 239], [199, 215, 239], [201, 215, 238], [202, 214, 238],
    [204, 214, 237], [206, 213, 236], [207, 213, 236], [209, 212, 235],
    [210, 212, 234], [212, 211, 234], [214, 211, 233], [215, 210, 232],
    [217, 209, 231], [218, 209, 231], [220, 208, 230], [221, 208, 229],
    [222, 207, 228], [224, 206, 227], [225, 206, 226], [226, 205, 226],
    [228, 204, 225], [229, 203, 224], [230, 203, 223], [231, 202, 222],
    [233, 201, 221], [234, 200, 219], [235, 199, 218], [236, 199, 217],
    [237, 198, 216], [238, 197, 215], [239, 196, 213], [240, 195, 212],
    [241, 194, 211], [242, 193, 209], [243, 192, 208], [244, 191, 206],
    [244, 190, 205], [245, 189, 203], [246, 188, 202], [246, 187, 200],
    [247, 186, 198], [247, 184, 197], [248, 183, 195], [248, 182, 193],
    [249, 180, 191], [249, 179, 189], [249, 177, 188], [249, 176, 186],
    [250, 174, 184], [250, 173, 182], [250, 171, 180], [250, 169, 178],
    [250, 168, 176], [250, 166, 174], [250, 164, 172], [250, 163, 170],
    [249, 161, 168], [249, 159, 165], [249, 157, 163], [249, 155, 161],
    [248, 154, 159], [248, 152, 157], [248, 150, 154], [247, 148, 152],
    [247, 146, 150], [246, 144, 148], [246, 142, 145], [245, 140, 143],
    [245, 138, 141], [244, 136, 138], [243, 134, 136], [243, 132, 133],
    [242, 130, 131], [241, 128, 128], [240, 126, 126], [240, 124, 123],
    [239, 122, 121],
], dtype=np.uint8)


def _depth_to_color(depth_mm: np.ndarray, min_mm: int = 300,
                    max_mm: int = 5000) -> np.ndarray:
    """Convert a uint16 depth image (mm) to an RGB colour image via Turbo LUT.

    Values outside [min_mm, max_mm] are clamped.  Returns a (H, W, 3) uint8
    numpy array.
    """
    clamped = np.clip(depth_mm, min_mm, max_mm)
    norm = ((clamped - min_mm) * 255 // (max_mm - min_mm)).astype(np.uint8)
    return _TURBO_LUT[norm]


class OrbbecPipeline:
    """Manages a K4A-compatible pipeline for one Orbbec Femto Bolt device.

    Captures depth or IR stereo frames via the K4A wrapper, encodes to JPEG
    in-process using Pillow, and exposes frames through a thread-safe queue.
    """

    def __init__(self, mode: str, width: int = 640, height: int = 480,
                 fps: int = 30, jpeg_quality: int = 85):
        if mode not in ("depth", "stereo"):
            raise ValueError(f"Invalid OrbbecPipeline mode: {mode}")
        self._mode = mode          # "depth" | "stereo"
        self._width = width
        self._height = height
        self._fps = fps
        self._jpeg_quality = jpeg_quality
        self._device = None
        self._running = False
        self._frame_queue = queue.Queue(maxsize=2)
        self._thread = None

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        import k4a

        self._device = k4a.Device.open()
        if not self._device:
            raise RuntimeError("OrbbecPipeline: failed to open K4A device")

        config = k4a.DeviceConfiguration(
            color_format=k4a.EImageFormat.COLOR_BGRA32,
            color_resolution=k4a.EColorResolution.RES_720P,
            depth_mode=k4a.EDepthMode.NFOV_UNBINNED,
            camera_fps=k4a.EFramesPerSecond.FPS_30,
            synchronized_images_only=True,
        )
        self._device.start_cameras(config)

        # Discard initial settling frames
        _deadline = time.time() + 5.0
        _settled = False
        while time.time() < _deadline:
            try:
                cap = self._device.get_capture(timeout_ms=1000)
                if cap is not None:
                    _settled = True
                    break
            except Exception:
                time.sleep(0.5)
        if not _settled:
            raise RuntimeError("OrbbecPipeline: no frames within 5 s — check USB")

        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True,
                                        name=f"ob-capture-{self._mode}")
        self._thread.start()
        log.info("[Orbbec] pipeline started — mode=%s %dx%d@%d",
                 self._mode, self._width, self._height, self._fps)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        if self._device:
            try:
                self._device.stop_cameras()
                self._device.close()
            except Exception:
                pass
        self._device = None
        log.info("[Orbbec] pipeline stopped — mode=%s", self._mode)

    def get_device_name(self) -> str:
        return "Orbbec Femto Bolt"

    # ── frame access ─────────────────────────────────────────────────────

    def get_frame(self, timeout: float = 1.0) -> Optional[bytes]:
        try:
            return self._frame_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def is_running(self) -> bool:
        return self._running

    # ── internal ─────────────────────────────────────────────────────────

    def _capture_loop(self) -> None:
        device = self._device
        frame_queue = self._frame_queue
        quality = self._jpeg_quality
        mode = self._mode
        frame_n = 0

        while self._running:
            try:
                cap = device.get_capture(timeout_ms=1000)
            except Exception:
                if self._running:
                    log.warning("[Orbbec] get_capture error (mode=%s)", mode)
                time.sleep(0.1)
                continue

            if cap is None:
                continue

            try:
                if mode == "depth":
                    jpeg_bytes = self._process_depth(cap, quality)
                else:  # stereo
                    jpeg_bytes = self._process_stereo(cap, quality)
            except Exception:
                log.exception("[Orbbec] frame processing error (mode=%s)", mode)
                continue

            if jpeg_bytes is None:
                continue

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
                log.info("[Orbbec] frame #%d (%d bytes) mode=%s",
                         frame_n, len(jpeg_bytes), mode)

    def _process_depth(self, cap, quality: int) -> Optional[bytes]:
        depth_img = cap.depth_image
        if depth_img is None:
            return None
        depth_mm = np.asanyarray(depth_img.data, dtype=np.uint16)
        depth_mm = depth_mm.reshape((depth_img.height, depth_img.width))
        rgb = _depth_to_color(depth_mm)
        img = Image.fromarray(rgb, "RGB")
        # Resize to configured output size
        if (img.width, img.height) != (self._width, self._height):
            img = img.resize((self._width, self._height), Image.BILINEAR)
        buf = io.BytesIO()
        try:
            img.save(buf, format="JPEG", quality=quality)
        except OSError:
            return None
        return buf.getvalue()

    def _process_stereo(self, cap, quality: int) -> Optional[bytes]:
        ir_img = cap.ir_image
        if ir_img is None:
            return None
        ir = np.asanyarray(ir_img.data, dtype=np.uint16)
        ir = ir.reshape((ir_img.height, ir_img.width))

        # Normalize 16-bit IR to 8-bit for display
        ir_min, ir_max = np.percentile(ir, [1, 99])
        if ir_max <= ir_min:
            ir_max = ir_min + 1
        ir_8 = np.clip((ir - ir_min) * 255 // (ir_max - ir_min), 0, 255).astype(np.uint8)

        # Single IR sensor → display same image twice side-by-side as stereo pair
        h, w = ir_8.shape
        composite = np.zeros((h, w * 2), dtype=np.uint8)
        composite[:, :w] = ir_8
        composite[:, w:] = ir_8

        img = Image.fromarray(composite, "L").convert("RGB")
        if (img.width, img.height) != (self._width, self._height):
            img = img.resize((self._width, self._height), Image.BILINEAR)
        buf = io.BytesIO()
        try:
            img.save(buf, format="JPEG", quality=quality)
        except OSError:
            return None
        return buf.getvalue()
