"""Robotic arm controller — stub for future SDK integration.

Replace the stub implementation with the real arm SDK calls once the
hardware and SDK are available. The interface contract (ArmState, ArmController)
is intentionally stable so robot.py and the web GUI need no changes.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional

log = logging.getLogger(__name__)


@dataclass
class ArmState:
    joint_angles_deg: List[float] = field(default_factory=lambda: [0.0] * 6)
    end_effector_xyz: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    is_moving: bool = False
    is_fault: bool = False
    is_enabled: bool = False
    timestamp: float = field(default_factory=time.time)


class ArmController:
    """
    Placeholder for the robotic arm SDK.

    All methods log their intent and return immediately. Swap in the real
    SDK implementation without changing the method signatures.
    """

    def __init__(self, port: str = "/dev/ttyUSB0", baudrate: int = 115200):
        self._port = port
        self._baudrate = baudrate
        self._state = ArmState()
        log.info("[Arm] Controller created (stub) — port=%s", port)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        log.info("[Arm] connect() — stub, not implemented")
        return False

    def disconnect(self) -> None:
        log.info("[Arm] disconnect() — stub")

    def enable(self) -> None:
        log.info("[Arm] enable() — stub")

    def disable(self) -> None:
        log.info("[Arm] disable() — stub")

    # ── motion ────────────────────────────────────────────────────────────────

    def move_joints(self, angles_deg: List[float], speed_pct: float = 20.0) -> bool:
        """Move all joints to target angles (degrees). speed_pct: 1–100."""
        log.info("[Arm] move_joints(%s, speed=%.0f%%) — stub", angles_deg, speed_pct)
        return False

    def move_cartesian(
        self,
        x: float, y: float, z: float,
        roll: float = 0.0, pitch: float = 0.0, yaw: float = 0.0,
        speed_pct: float = 20.0,
    ) -> bool:
        """Move end-effector to Cartesian pose (mm, deg)."""
        log.info(
            "[Arm] move_cartesian(xyz=[%.1f,%.1f,%.1f] rpy=[%.1f,%.1f,%.1f]) — stub",
            x, y, z, roll, pitch, yaw,
        )
        return False

    def go_home(self) -> bool:
        log.info("[Arm] go_home() — stub")
        return False

    def stop(self) -> None:
        log.info("[Arm] stop() — stub")

    def reset_fault(self) -> None:
        log.info("[Arm] reset_fault() — stub")

    # ── state ─────────────────────────────────────────────────────────────────

    def get_state(self) -> ArmState:
        self._state.timestamp = time.time()
        return ArmState(**self._state.__dict__)
