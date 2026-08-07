"""VR backend abstraction — swappable Quest 3 data sources for TeleopModule.

Three backends:
  XRBackend          — xrobotoolkit_sdk (WiFi/OpenXR, existing Jetson path)
  ADBBackend         — OculusReader (USB/ADB logcat, simple S2R2S-teleop APK)
  XRProtocolBackend  — XRProtocolReceiver (TCP, full XRoboToolkit-Quest APK)

All conform to VRBackend ABC — TeleopModule calls the same interface regardless
of which Quest connection method is in use.
"""

import abc
import logging
import math
from typing import Any, Dict, List, Optional

import numpy as np

from teleoperation_sdk.oculus_reader.reader import OculusReader
from teleoperation_sdk.xr_protocol import XRProtocolReceiver

log = logging.getLogger(__name__)


# ── ABC ──────────────────────────────────────────────────────────────────────

class VRBackend(abc.ABC):
    """Abstract interface for Quest 3 tracking data sources."""

    @abc.abstractmethod
    def init(self) -> bool:
        """Initialize the backend. Returns True on success."""

    @abc.abstractmethod
    def close(self) -> None:
        """Shut down the backend, release resources."""

    @abc.abstractmethod
    def get_controller_pose(self, side: str) -> Optional[List[float]]:
        """Return [x, y, z, qx, qy, qz, qw] or None."""

    @abc.abstractmethod
    def get_trigger(self, side: str) -> float:
        """Return trigger value 0.0–1.0."""

    @abc.abstractmethod
    def get_grip(self, side: str) -> float:
        """Return grip value 0.0–1.0."""

    @abc.abstractmethod
    def get_button(self, side: str, name: str) -> bool:
        """Return button state. name: 'primaryButton', 'secondaryButton', 'menuButton', 'axisClick'."""

    @abc.abstractmethod
    def get_headset_pose(self) -> Optional[List[float]]:
        """Return [x, y, z, qx, qy, qz, qw] or None."""

    @abc.abstractmethod
    def is_connected(self) -> bool:
        """Return whether the data source is currently connected."""


# ── XRBackend (WiFi / OpenXR via xrobotoolkit_sdk) ──────────────────────────

class XRBackend(VRBackend):
    """Backend wrapping xrobotoolkit_sdk (WiFi OpenXR on Jetson).

    This is the existing WiFi path — backward compatible with the original
    XRoboToolkit-PC-Service running on Jetson AGX Orin.
    """

    def __init__(self):
        self._xrt = None
        self._connected = False

    def init(self) -> bool:
        try:
            import xrobotoolkit_sdk as xrt
            self._xrt = xrt
            self._xrt.init()
            self._connected = True
            log.info("[XRBackend] xrobotoolkit_sdk initialized")
            return True
        except ImportError:
            log.warning("[XRBackend] xrobotoolkit_sdk not installed")
            return False
        except Exception as e:
            log.error("[XRBackend] Init failed: %s", e)
            return False

    def close(self) -> None:
        if self._xrt is not None:
            try:
                self._xrt.close()
            except Exception:
                pass
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def get_controller_pose(self, side: str) -> Optional[List[float]]:
        if not self._connected:
            return None
        try:
            getter = (self._xrt.get_left_controller_pose if side == "left"
                      else self._xrt.get_right_controller_pose)
            pose = getter()
            if pose is not None and len(pose) >= 7:
                return [float(v) for v in pose[:7]]
        except Exception as e:
            log.debug("[XRBackend] Controller pose error: %s", e)
        return None

    def get_trigger(self, side: str) -> float:
        if not self._connected:
            return 0.0
        try:
            getter = (self._xrt.get_left_trigger if side == "left"
                      else self._xrt.get_right_trigger)
            val = getter()
            return float(val) if val is not None else 0.0
        except Exception:
            return 0.0

    def get_grip(self, side: str) -> float:
        if not self._connected:
            return 0.0
        try:
            getter = (self._xrt.get_left_grip if side == "left"
                      else self._xrt.get_right_grip)
            val = getter()
            return float(val) if val is not None else 0.0
        except Exception:
            return 0.0

    def get_button(self, side: str, name: str) -> bool:
        if not self._connected:
            return False
        try:
            # Map generic names to xrt function names
            if name == "primaryButton":
                return bool(self._xrt.get_A_button())
            if name == "secondaryButton":
                return bool(self._xrt.get_B_button())
            # xrt may not expose other buttons — fallback False
            return False
        except Exception:
            return False

    def get_headset_pose(self) -> Optional[List[float]]:
        if not self._connected:
            return None
        try:
            if hasattr(self._xrt, 'get_headset_pose'):
                pose = self._xrt.get_headset_pose()
                if pose is not None and len(pose) >= 7:
                    return [float(v) for v in pose[:7]]
        except Exception as e:
            log.debug("[XRBackend] Head pose error: %s", e)
        return None


# ── ADBBackend (USB via OculusReader + simple S2R2S-teleop APK) ─────────────

def _rotation_matrix_to_quat(R: np.ndarray) -> List[float]:
    """Convert 3x3 rotation matrix to quaternion [qx, qy, qz, qw]."""
    trace = np.trace(R)
    if trace > 0:
        s = 0.5 / math.sqrt(trace + 1.0)
        w = 0.25 / s
        x = (R[2, 1] - R[1, 2]) * s
        y = (R[0, 2] - R[2, 0]) * s
        z = (R[1, 0] - R[0, 1]) * s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = 2.0 * math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2])
        w = (R[2, 1] - R[1, 2]) / s
        x = 0.25 * s
        y = (R[0, 1] + R[1, 0]) / s
        z = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = 2.0 * math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2])
        w = (R[0, 2] - R[2, 0]) / s
        x = (R[0, 1] + R[1, 0]) / s
        y = 0.25 * s
        z = (R[1, 2] + R[2, 1]) / s
    else:
        s = 2.0 * math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1])
        w = (R[1, 0] - R[0, 1]) / s
        x = (R[0, 2] + R[2, 0]) / s
        y = (R[1, 2] + R[2, 1]) / s
        z = 0.25 * s
    return [x, y, z, w]


class ADBBackend(VRBackend):
    """Backend wrapping OculusReader (USB/ADB, simple S2R2S-teleop APK).

    The simple APK only streams 4x4 controller transforms via logcat — no
    headset pose, no hand tracking. get_headset_pose() always returns None.
    """

    def __init__(self, ip_address: Optional[str] = None):
        self._ip_address = ip_address
        self._reader: Optional[OculusReader] = None

    def init(self) -> bool:
        try:
            self._reader = OculusReader(ip_address=self._ip_address, run=True)
            log.info("[ADBBackend] OculusReader started (ip=%s)", self._ip_address or "USB")
            return True
        except Exception as e:
            log.error("[ADBBackend] OculusReader init failed: %s", e)
            return False

    def close(self) -> None:
        if self._reader is not None:
            try:
                self._reader.stop()
            except Exception:
                pass
            self._reader = None

    def is_connected(self) -> bool:
        return self._reader is not None and self._reader.running

    def _get_transform(self, side: str) -> Optional[np.ndarray]:
        """Read 4x4 transform matrix for a controller side."""
        if self._reader is None:
            return None
        try:
            transforms, _buttons = self._reader.get_transformations_and_buttons()
            if transforms is None:
                return None
            key = 'r' if side == 'right' else 'l'
            return transforms.get(key)
        except Exception:
            return None

    def get_controller_pose(self, side: str) -> Optional[List[float]]:
        T = self._get_transform(side)
        if T is None:
            return None
        pos = [float(T[0, 3]), float(T[1, 3]), float(T[2, 3])]
        quat = _rotation_matrix_to_quat(T[:3, :3])
        return pos + quat

    def get_trigger(self, side: str) -> float:
        if self._reader is None:
            return 0.0
        try:
            _transforms, buttons = self._reader.get_transformations_and_buttons()
            if buttons is None:
                return 0.0
            key = f"{'right' if side == 'right' else 'left'}Trig"
            val = buttons.get(key, 0.0)
            if isinstance(val, (tuple, list)):
                val = val[0] if val else 0.0
            return float(val)
        except Exception:
            return 0.0

    def get_grip(self, side: str) -> float:
        if self._reader is None:
            return 0.0
        try:
            _transforms, buttons = self._reader.get_transformations_and_buttons()
            if buttons is None:
                return 0.0
            key = f"{'right' if side == 'right' else 'left'}Grip"
            val = buttons.get(key, 0.0)
            if isinstance(val, (tuple, list)):
                val = val[0] if val else 0.0
            return float(val)
        except Exception:
            return 0.0

    def get_button(self, side: str, name: str) -> bool:
        if self._reader is None:
            return False
        try:
            _transforms, buttons = self._reader.get_transformations_and_buttons()
            if buttons is None:
                return False
            # The simple APK button mapping — depends on what parse_buttons returns
            # Primary/secondary map to A/X and B/Y
            btn_map = {
                "primaryButton": ("right" if side == "right" else "left") + "A",
                "secondaryButton": ("right" if side == "right" else "left") + "B",
                "menuButton": ("right" if side == "right" else "left") + "Menu",
            }
            key = btn_map.get(name, "")
            if not key:
                return False
            val = buttons.get(key, False)
            if isinstance(val, (tuple, list)):
                val = val[0] if val else False
            return bool(val)
        except Exception:
            return False

    def get_headset_pose(self) -> Optional[List[float]]:
        # Simple APK does not stream headset pose
        return None


# ── XRProtocolBackend (TCP via XRoboToolkit-Quest APK) ──────────────────────

class XRProtocolBackend(VRBackend):
    """Backend wrapping XRProtocolReceiver (TCP, XRoboToolkit-Quest APK).

    The full XRoboToolkit-Quest APK streams headset + controller + hand tracking
    over TCP port 63901. Use `adb reverse tcp:63901 tcp:63901` to tunnel over USB.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 63901):
        self._host = host
        self._port = port
        self._receiver: Optional[XRProtocolReceiver] = None

    def init(self) -> bool:
        try:
            self._receiver = XRProtocolReceiver(host=self._host, port=self._port)
            self._receiver.start()
            log.info("[XRProtocolBackend] Listening on %s:%d", self._host, self._port)
            return True
        except Exception as e:
            log.error("[XRProtocolBackend] Init failed: %s", e)
            return False

    def close(self) -> None:
        if self._receiver is not None:
            try:
                self._receiver.stop()
            except Exception:
                pass
            self._receiver = None

    def is_connected(self) -> bool:
        if self._receiver is None:
            return False
        return self._receiver.connected

    def get_controller_pose(self, side: str) -> Optional[List[float]]:
        if self._receiver is None:
            return None
        return self._receiver.get_controller_pose(side)

    def get_trigger(self, side: str) -> float:
        if self._receiver is None:
            return 0.0
        return self._receiver.get_trigger(side)

    def get_grip(self, side: str) -> float:
        if self._receiver is None:
            return 0.0
        return self._receiver.get_grip(side)

    def get_button(self, side: str, name: str) -> bool:
        if self._receiver is None:
            return False
        return self._receiver.get_button(side, name)

    def get_headset_pose(self) -> Optional[List[float]]:
        if self._receiver is None:
            return None
        return self._receiver.get_headset_pose()

    def set_command_callback(self, callback) -> None:
        """Register a callback for non-Tracking function messages from Quest.

        callback signature: callback(function_name: str, value: dict) -> None
        """
        if self._receiver is not None:
            self._receiver.set_command_callback(callback)

    def send_function(self, function_name: str, value: str) -> bool:
        """Send a function message to the connected Quest client."""
        if self._receiver is None:
            return False
        return self._receiver.send_function(function_name, value)

    def send_ur5e_status(self, status: dict) -> bool:
        """Send UR5e robot status to the Quest client."""
        if self._receiver is None:
            return False
        return self._receiver.send_ur5e_status(status)


# ── factory ──────────────────────────────────────────────────────────────────

def create_vr_backend(name: str, **kwargs) -> Optional[VRBackend]:
    """Create a VR backend by name.

    Args:
        name: "xrt" | "adb" | "xr_protocol"
        **kwargs: passed to the backend constructor
    """
    backends = {
        "xrt": XRBackend,
        "adb": ADBBackend,
        "xr_protocol": XRProtocolBackend,
    }
    cls = backends.get(name)
    if cls is None:
        log.error("[VRBackend] Unknown backend: %s (available: %s)", name, list(backends.keys()))
        return None
    return cls(**kwargs)
