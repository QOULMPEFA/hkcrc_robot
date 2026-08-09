"""VR backend abstraction — swappable Quest 3 data sources for TeleopModule.

Two backends:
  XRBackend          — xrobotoolkit_sdk (WiFi/OpenXR, existing Jetson path)
  XRProtocolBackend  — XRProtocolReceiver (TCP, full XRoboToolkit-Quest APK)

All conform to VRBackend ABC — TeleopModule calls the same interface regardless
of which Quest connection method is in use.
"""

import abc
import logging
from typing import Any, Dict, List, Optional

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
        name: "xrt" | "xr_protocol"
        **kwargs: passed to the backend constructor
    """
    backends = {
        "xrt": XRBackend,
        "xr_protocol": XRProtocolBackend,
    }
    cls = backends.get(name)
    if cls is None:
        log.error("[VRBackend] Unknown backend: %s (available: %s)", name, list(backends.keys()))
        return None
    return cls(**kwargs)
