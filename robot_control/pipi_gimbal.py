"""Pipe robot v2 gimbal module — pan/tilt control via Fashion Star servos (IDs 1-4).

Wraps pr_v2_sdk.Gimbal as a RobotModule.
SocketIO events: gimbal_h_left, gimbal_h_right, gimbal_p_up, gimbal_p_down, gimbal_home, gimbal_stop
"""

import logging
from typing import Any, Dict

from robot_control.module_base import RobotModule

log = logging.getLogger(__name__)


class GimbalModule(RobotModule):
    """Pan/tilt gimbal controller for pipe robot v2."""

    def __init__(self, config: dict):
        self._config = config
        self._gimbal = None
        self._connected = False
        self._h_angle = 0.0
        self._p_angle = 0.0

    @property
    def name(self) -> str:
        return "gimbal"

    @property
    def display_name(self) -> str:
        return "云台控制"

    # ── lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        import os as _os, sys
        _here = _os.path.dirname(__file__)
        for _try in [
            _os.path.normpath(_os.path.join(_here, "..", "pr_v2_sdk")),
            _os.path.normpath(_os.path.join(_here, "..", "..", "..", "20260618_管道机器人二代", "code")),
        ]:
            if _try not in sys.path:
                sys.path.insert(0, _try)

        try:
            from pr_v2_sdk.gimbal_lib import Gimbal
            # Try to reuse joint module's serial connection to avoid port conflict
            try:
                from robot_control.pipi_joint import _SHARED_USERVO
                uservo = _SHARED_USERVO
            except Exception:
                uservo = None
            self._gimbal = Gimbal(uservo=uservo)
            self._connected = True
            self._h_angle = 0.0
            self._p_angle = 0.0
            log.info("[Gimbal] Started (shared=%s)", uservo is not None)
        except ImportError as e:
            log.warning("[Gimbal] SDK not available: %s", e)
            self._connected = False
        except Exception as e:
            log.error("[Gimbal] Start failed: %s", e)
            self._connected = False

    def stop(self) -> None:
        if self._gimbal:
            try:
                self._gimbal.wheel_stop()
            except Exception:
                pass
            self._gimbal = None
        self._connected = False
        log.info("[Gimbal] Stopped")

    def emergency_stop(self) -> None:
        if self._gimbal:
            self._gimbal.wheel_stop()
        log.info("[Gimbal] Emergency stop")

    # ── state ─────────────────────────────────────────────────────────────

    def get_state_schema(self) -> Dict[str, Any]:
        return {"connected": False, "h_angle": 0.0, "p_angle": 0.0}

    def get_state(self) -> Dict[str, Any]:
        if not self._gimbal:
            return self.get_state_schema()
        return {
            "connected": self._connected,
            "h_angle": getattr(self._gimbal, "h_angle", 0.0),
            "p_angle": getattr(self._gimbal, "p_angle", 0.0),
        }

    # ── SocketIO handlers ─────────────────────────────────────────────────

    def get_socketio_handlers(self) -> Dict[str, any]:
        return {
            "gimbal_h_left":  self._on_h_left,
            "gimbal_h_right": self._on_h_right,
            "gimbal_p_up":    self._on_p_up,
            "gimbal_p_down":  self._on_p_down,
            "gimbal_home":    self._on_home,
            "gimbal_stop":    self._on_stop,
        }

    def _on_h_left(self, data: dict, emit=None):
        if self._gimbal:
            self._gimbal.move_h(-1)

    def _on_h_right(self, data: dict, emit=None):
        if self._gimbal:
            self._gimbal.move_h(1)

    def _on_p_up(self, data: dict, emit=None):
        if self._gimbal:
            self._gimbal.move_p(1)

    def _on_p_down(self, data: dict, emit=None):
        if self._gimbal:
            self._gimbal.move_p(-1)

    def _on_home(self, data: dict, emit=None):
        if self._gimbal:
            self._gimbal.home()

    def _on_stop(self, data: dict, emit=None):
        if self._gimbal:
            self._gimbal.wheel_stop()
