"""Dexterous hand controller — stub for future SDK integration.

Replace the stub implementation with the real hand SDK calls once the
hardware and SDK are available. The interface contract (HandState, HandController)
is intentionally stable so robot.py and the web GUI need no changes.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import List

log = logging.getLogger(__name__)


@dataclass
class HandState:
    finger_positions: List[float] = field(default_factory=lambda: [0.0] * 5)  # 0=open, 1=closed
    grip_force_n: float = 0.0
    is_moving: bool = False
    is_fault: bool = False
    is_enabled: bool = False
    timestamp: float = field(default_factory=time.time)


class HandController:
    """
    Placeholder for the dexterous hand SDK.

    All methods log their intent and return immediately. Swap in the real
    SDK implementation without changing the method signatures.
    """

    FINGER_NAMES = ["thumb", "index", "middle", "ring", "pinky"]

    def __init__(self, port: str = "/dev/ttyUSB1", baudrate: int = 115200):
        self._port = port
        self._baudrate = baudrate
        self._state = HandState()
        log.info("[Hand] Controller created (stub) — port=%s", port)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        log.info("[Hand] connect() — stub, not implemented")
        return False

    def disconnect(self) -> None:
        log.info("[Hand] disconnect() — stub")

    def enable(self) -> None:
        log.info("[Hand] enable() — stub")

    def disable(self) -> None:
        log.info("[Hand] disable() — stub")

    # ── motion ────────────────────────────────────────────────────────────────

    def set_finger(self, finger_idx: int, position: float, speed_pct: float = 50.0) -> bool:
        """
        Set a single finger position.
        finger_idx: 0=thumb … 4=pinky
        position:   0.0=fully open, 1.0=fully closed
        """
        name = self.FINGER_NAMES[finger_idx] if finger_idx < 5 else str(finger_idx)
        log.info("[Hand] set_finger(%s, %.2f, speed=%.0f%%) — stub", name, position, speed_pct)
        return False

    def set_all_fingers(self, positions: List[float], speed_pct: float = 50.0) -> bool:
        """Set all five finger positions simultaneously."""
        log.info("[Hand] set_all_fingers(%s, speed=%.0f%%) — stub", positions, speed_pct)
        return False

    def open(self, speed_pct: float = 50.0) -> bool:
        """Fully open the hand."""
        return self.set_all_fingers([0.0] * 5, speed_pct)

    def close(self, speed_pct: float = 50.0) -> bool:
        """Fully close the hand."""
        return self.set_all_fingers([1.0] * 5, speed_pct)

    def pinch(self, speed_pct: float = 50.0) -> bool:
        """Pinch grip: thumb + index close, others open."""
        return self.set_all_fingers([1.0, 1.0, 0.0, 0.0, 0.0], speed_pct)

    def stop(self) -> None:
        log.info("[Hand] stop() — stub")

    def reset_fault(self) -> None:
        log.info("[Hand] reset_fault() — stub")

    # ── state ─────────────────────────────────────────────────────────────────

    def get_state(self) -> HandState:
        self._state.timestamp = time.time()
        return HandState(**self._state.__dict__)
