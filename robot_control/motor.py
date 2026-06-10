"""Pipe robot motor controller — DC motors via Orange Pi GPIO (H-bridge).

Wraps motor_sdk.DifferentialDrive as a RobotModule.
SocketIO events: motor_forward, motor_backward, motor_left, motor_right, motor_stop
"""

import logging
from typing import Any, Dict

from robot_control.module_base import RobotModule

log = logging.getLogger(__name__)

# Default GPIO pin mapping (physical pin numbers, wiringPiSetupPhys)
DEFAULT_PINS = {
    "left_in1": 29, "left_in2": 31,
    "right_in1": 35, "right_in2": 37,
}


class MotorController(RobotModule):
    """DC motor differential drive for pipe robot locomotion."""

    def __init__(self, config: dict):
        self._config = config
        self._drive = None
        self._connected = False

    @property
    def name(self) -> str:
        return "motor"

    @property
    def display_name(self) -> str:
        return "电机控制"

    # ── lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        # Ensure motor_sdk package directory is on sys.path.
        # The outer motor_sdk/ dir (project root) is added so Python can
        # discover motor_sdk/motor_sdk/ (the actual package) underneath it.
        import os as _os
        _project_dir = _os.path.join(_os.path.dirname(__file__), "..", "motor_sdk")
        _project_dir = _os.path.normpath(_project_dir)
        if _project_dir not in __import__("sys").path:
            __import__("sys").path.insert(0, _project_dir)

        try:
            from motor_sdk import DCMotor, DifferentialDrive

            pins = {**DEFAULT_PINS, **self._config}
            left = DCMotor(pins["left_in1"], pins["left_in2"], "左电机")
            right = DCMotor(pins["right_in1"], pins["right_in2"], "右电机")
            self._drive = DifferentialDrive(left, right)
            self._connected = True
            log.info("[Motor] Controller started — differential drive ready")
        except ImportError:
            log.warning("[Motor] motor_sdk not available — GPIO control disabled")
            self._connected = False
        except Exception as e:
            log.error("[Motor] Start failed: %s", e)
            self._connected = False

    def stop(self) -> None:
        if self._drive:
            self._drive.stop()
            self._drive = None
        self._connected = False
        log.info("[Motor] Controller stopped")

    def emergency_stop(self) -> None:
        if self._drive:
            self._drive.stop()
        log.info("[Motor] Emergency stop")

    # ── state ─────────────────────────────────────────────────────────────

    def get_state_schema(self) -> Dict[str, Any]:
        return {"connected": False, "direction": "stop", "is_moving": False}

    def get_state(self) -> Dict[str, Any]:
        if not self._drive:
            return self.get_state_schema()
        return {
            "connected": self._connected,
            "direction": self._drive.direction,
            "is_moving": self._drive.is_moving,
        }

    # ── SocketIO handlers ─────────────────────────────────────────────────

    def get_socketio_handlers(self) -> Dict[str, any]:
        return {
            "motor_forward":  self._on_forward,
            "motor_backward": self._on_backward,
            "motor_left":     self._on_left,
            "motor_right":    self._on_right,
            "motor_stop":     self._on_stop,
        }

    def _on_forward(self, data: dict, emit=None):
        if self._drive:
            self._drive.forward()

    def _on_backward(self, data: dict, emit=None):
        if self._drive:
            self._drive.backward()

    def _on_left(self, data: dict, emit=None):
        if self._drive:
            self._drive.turn_left()

    def _on_right(self, data: dict, emit=None):
        if self._drive:
            self._drive.turn_right()

    def _on_stop(self, data: dict, emit=None):
        if self._drive:
            self._drive.stop()
