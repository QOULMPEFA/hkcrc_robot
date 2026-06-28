"""Bus servo controller — Fashion Star bus servos via CH340 UART.

Wraps pr_v2_sdk.servo_lib.ServoController as a RobotModule.
SocketIO events: servo_set_angle, servo_set_wheel, servo_spread,
                 servo_shrink, servo_stop, servo_set_damping
"""

import logging
import os as _os
import time
from typing import Any, Dict, List

from robot_control.module_base import RobotModule

log = logging.getLogger(__name__)

# Default servo IDs for pipe robot joints
DEFAULT_HEAD_IDS = [20]
DEFAULT_TAIL_IDS = [10]


class ServoModule(RobotModule):
    """Fashion Star bus servo controller for pipe robot joints."""

    def __init__(self, config: dict):
        self._config = config
        self._controller = None
        self._connected = False
        self._head_ids = config.get("head_ids", DEFAULT_HEAD_IDS)
        self._tail_ids = config.get("tail_ids", DEFAULT_TAIL_IDS)
        self._all_ids = list(set(self._head_ids + self._tail_ids))
        self._direction = "stop"  # spread / shrink / stop
        # Cached monitor data
        self._monitor = {}  # servo_id -> {voltage, current, power, temp, angle}

    @property
    def name(self) -> str:
        return "servo"

    @property
    def display_name(self) -> str:
        return "总线舵机"

    # ── lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        _project_dir = _os.path.join(_os.path.dirname(__file__), "..", "pr_v2_sdk")
        _project_dir = _os.path.normpath(_project_dir)
        if _project_dir not in __import__("sys").path:
            __import__("sys").path.insert(0, _project_dir)

        try:
            from pr_v2_sdk.servo_lib import ServoController
            self._controller = ServoController()
            self._connected = True
            servo_ids = self._controller.uservo.servos.keys() if self._controller.uservo else []
            log.info("[Servo] Controller started — %d servo(s) found: %s",
                     len(list(servo_ids)), list(servo_ids))
        except ImportError as e:
            log.warning("[Servo] pr_v2_sdk not available — %s", e)
            self._connected = False
        except Exception as e:
            log.error("[Servo] Start failed: %s", e)
            self._connected = False

    def stop(self) -> None:
        if self._controller:
            try:
                self._controller.stop(self._all_ids)
            except Exception:
                pass
            try:
                self._controller.uart.close()
            except Exception:
                pass
            self._controller = None
        self._connected = False
        log.info("[Servo] Controller stopped")

    def emergency_stop(self) -> None:
        if self._controller:
            self._controller.stop(self._all_ids)
        self._direction = "stop"
        log.info("[Servo] Emergency stop")

    # ── state ─────────────────────────────────────────────────────────────

    def get_state_schema(self) -> Dict[str, Any]:
        return {
            "connected": False,
            "direction": "stop",
            "head_ids": [],
            "tail_ids": [],
            "all_ids": [],
            "monitor": {},
        }

    def get_state(self) -> Dict[str, Any]:
        if not self._controller:
            return self.get_state_schema()

        # Refresh monitor data for all known servos
        try:
            for sid in self._all_ids:
                if sid in self._controller.uservo.servos:
                    data = self._controller.uservo.query_servo_monitor(sid)
                    if data:
                        self._monitor[sid] = data
        except Exception:
            pass

        return {
            "connected": self._connected,
            "direction": self._direction,
            "head_ids": self._head_ids,
            "tail_ids": self._tail_ids,
            "all_ids": self._all_ids,
            "monitor": dict(self._monitor),
        }

    # ── SocketIO handlers ─────────────────────────────────────────────────

    def get_socketio_handlers(self) -> Dict[str, any]:
        return {
            "servo_set_angle":   self._on_set_angle,
            "servo_set_wheel":   self._on_set_wheel,
            "servo_spread":      self._on_spread,
            "servo_shrink":      self._on_shrink,
            "servo_stop":        self._on_stop,
            "servo_set_damping": self._on_set_damping,
        }

    def _on_set_angle(self, data: dict, emit=None):
        if not self._controller:
            return
        servo_id = data.get("servo_id", self._all_ids[0] if self._all_ids else 1)
        angle = float(data.get("angle", 90))
        velocity = data.get("velocity", None)
        power = int(data.get("power", 4000))
        mean_dps = float(data.get("mean_dps", 100))
        is_mturn = bool(data.get("is_mturn", False))
        try:
            self._controller.uservo.set_servo_angle(
                servo_id, angle, is_mturn=is_mturn,
                velocity=velocity, power=power, mean_dps=mean_dps)
            log.info("[Servo] Set angle: id=%s angle=%.1f", servo_id, angle)
        except Exception as e:
            log.error("[Servo] Set angle failed: %s", e)

    def _on_set_wheel(self, data: dict, emit=None):
        if not self._controller:
            return
        servo_id = data.get("servo_id", 1)
        is_cw = bool(data.get("is_cw", True))
        mean_dps = float(data.get("mean_dps", 100))
        try:
            self._controller.uservo.set_wheel_norm(servo_id, is_cw=is_cw, mean_dps=mean_dps)
            log.info("[Servo] Set wheel: id=%s cw=%s dps=%.1f", servo_id, is_cw, mean_dps)
        except Exception as e:
            log.error("[Servo] Set wheel failed: %s", e)

    def _on_spread(self, data: dict, emit=None):
        if not self._controller:
            return
        head_angle = float(data.get("head_angle", 90))
        tail_angle = float(data.get("tail_angle", -90))
        mean_dps = float(data.get("mean_dps", 200))
        try:
            self._controller.spread(
                self._head_ids, self._tail_ids,
                head_angle, tail_angle, mean_dps)
            self._direction = "spread"
            log.info("[Servo] Spread: head=%.1f tail=%.1f", head_angle, tail_angle)
        except Exception as e:
            log.error("[Servo] Spread failed: %s", e)

    def _on_shrink(self, data: dict, emit=None):
        if not self._controller:
            return
        head_angle = float(data.get("head_angle", 0))
        tail_angle = float(data.get("tail_angle", 0))
        mean_dps = float(data.get("mean_dps", 200))
        try:
            self._controller.shrink(
                self._head_ids, self._tail_ids,
                head_angle, tail_angle, mean_dps)
            self._direction = "shrink"
            log.info("[Servo] Shrink: head=%.1f tail=%.1f", head_angle, tail_angle)
        except Exception as e:
            log.error("[Servo] Shrink failed: %s", e)

    def _on_stop(self, data: dict, emit=None):
        if not self._controller:
            return
        servo_ids = data.get("servo_ids", self._all_ids)
        if not servo_ids:
            servo_ids = self._all_ids
        try:
            self._controller.stop(servo_ids)
            self._direction = "stop"
            log.info("[Servo] Stop: ids=%s", servo_ids)
        except Exception as e:
            log.error("[Servo] Stop failed: %s", e)

    def _on_set_damping(self, data: dict, emit=None):
        if not self._controller:
            return
        servo_id = data.get("servo_id", 1)
        power = int(data.get("power", 500))
        try:
            self._controller.uservo.set_damping(servo_id, power=power)
            log.info("[Servo] Set damping: id=%s power=%d", servo_id, power)
        except Exception as e:
            log.error("[Servo] Set damping failed: %s", e)
