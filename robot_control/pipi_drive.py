"""Pipe robot v2 drive module — 6-channel DC motor via UART-2 (ttyS2) to STM32.

Uses Uart2Stm32 for direct /dev/ttyS2 communication with text protocol.
SocketIO events: drive_forward, drive_backward, drive_stop
"""

import logging
import time
from typing import Any, Dict

from robot_control.module_base import RobotModule

log = logging.getLogger(__name__)

MOTOR_CMDS = [
    ("A", -100, 100),
    ("B", 100, -100),
    ("C", -100, 100),
    ("D", 100, -100),
    ("E", 100, -100),
    ("F", 100, -100),
]


class DriveModule(RobotModule):
    """DC motor drive controller for pipe robot v2 locomotion via UART-2."""

    def __init__(self, config: dict):
        self._config = config
        self._port = config.get("port", "/dev/ttyS2")
        self._baudrate = config.get("baudrate", 115200)
        self._motor = None
        self._connected = False
        self._direction = "stop"

    @property
    def name(self) -> str:
        return "drive"

    @property
    def display_name(self) -> str:
        return "驱动电机"

    # ── lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        import os as _os, sys

        _here = _os.path.dirname(__file__)
        for _try in [
            _os.path.normpath(_os.path.join(_here, "..", "pr_mini", "motor_sdk")),
            _os.path.normpath(_os.path.join(_here, "..", "motor_sdk")),
        ]:
            _parent = _os.path.normpath(_os.path.join(_try, ".."))
            if _parent not in sys.path:
                sys.path.insert(0, _parent)

        try:
            from motor_sdk.motor_sdk.uart2_stm32 import Uart2Stm32

            self._motor = Uart2Stm32(port=self._port, baudrate=self._baudrate)
            self._connected = self._motor.is_connected
            log.info("[Drive] Started on %s — connected=%s", self._port, self._connected)
        except ImportError as e:
            log.warning("[Drive] Uart2Stm32 not available: %s", e)
            self._connected = False
        except Exception as e:
            log.error("[Drive] Start failed: %s", e)
            self._connected = False

    def stop(self) -> None:
        if self._motor:
            try:
                self._motor.stop()
                self._motor.close()
            except Exception:
                pass
            self._motor = None
        self._connected = False
        self._direction = "stop"
        log.info("[Drive] Stopped")

    def emergency_stop(self) -> None:
        if self._motor:
            self._motor.stop()
        self._direction = "stop"
        log.info("[Drive] Emergency stop")

    # ── state ─────────────────────────────────────────────────────────────

    def get_state_schema(self) -> Dict[str, Any]:
        return {"connected": False, "direction": "stop", "is_moving": False}

    def get_state(self) -> Dict[str, Any]:
        return {
            "connected": self._connected,
            "direction": self._direction,
            "is_moving": self._direction != "stop",
        }

    # ── SocketIO handlers ─────────────────────────────────────────────────

    def get_socketio_handlers(self) -> Dict[str, any]:
        return {
            "drive_forward": self._on_forward,
            "drive_backward": self._on_backward,
            "drive_stop": self._on_stop,
        }

    def _on_forward(self, data: dict, emit=None):
        if self._motor:
            self._direction = "forward"
            for mid, fwd, _ in MOTOR_CMDS:
                self._motor.motor(mid, fwd)
                time.sleep(0.008)
                if self._direction != "forward":
                    return

    def _on_backward(self, data: dict, emit=None):
        if self._motor:
            self._direction = "backward"
            for mid, _, bwd in MOTOR_CMDS:
                self._motor.motor(mid, bwd)
                time.sleep(0.008)
                if self._direction != "backward":
                    return

    def _on_stop(self, data: dict, emit=None):
        self._direction = "stop"
        if self._motor:
            self._motor.stop()
