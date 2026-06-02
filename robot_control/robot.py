"""Top-level robot controller — assembles all subsystems.

Usage:
    from robot_control.robot import Robot

    with Robot() as robot:
        robot.ugv.set_velocity(0.5, 0.0)
        robot.lift.move_to(200.0)
        # arm and hand are stubs until SDKs are integrated
"""

import logging
import sys
import os
import time
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from robot_control.ugv  import UGVController,  UGVState
from robot_control.lift import LiftController, LiftState
from robot_control.arm  import ArmController,  ArmState
from robot_control.hand import HandController, HandState

log = logging.getLogger(__name__)


@dataclass
class RobotConfig:
    # UGV bridge
    ugv_host: str  = "127.0.0.1"
    ugv_port: int  = 9100

    # Lift — CAN
    lift_can_channel: str = "can0"
    lift_node_id:     int = 1

    # Arm — serial
    arm_port:     str = "/dev/ttyUSB0"
    arm_baudrate: int = 115200

    # Hand — serial
    hand_port:     str = "/dev/ttyUSB1"
    hand_baudrate: int = 115200

    # Lift homing strategy on startup
    lift_home_strategy: str = "current_pos"   # "current_pos" | "limit_switch" | "mechanical"


@dataclass
class RobotState:
    ugv:  UGVState  = field(default_factory=UGVState)
    lift: LiftState = field(default_factory=LiftState)
    arm:  ArmState  = field(default_factory=ArmState)
    hand: HandState = field(default_factory=HandState)
    timestamp: float = field(default_factory=time.time)


class Robot:
    """
    Aggregates UGV, lift, arm, and hand subsystems.

    Designed to be used as a context manager:

        with Robot(config) as robot:
            robot.ugv.set_velocity(0.3, 0.0)
            robot.lift.move_to(150.0)
    """

    def __init__(self, config: Optional[RobotConfig] = None):
        self.config = config or RobotConfig()
        self._lift_node = None
        self._lift_fallback_state: Optional[LiftState] = None  # preserved when lift fails

        self.ugv  = UGVController(self.config.ugv_host, self.config.ugv_port)
        self.arm  = ArmController(self.config.arm_port,  self.config.arm_baudrate)
        self.hand = HandController(self.config.hand_port, self.config.hand_baudrate)
        # lift is initialised in start() because it needs a CAN node
        self.lift: Optional[LiftController] = None

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> "Robot":
        log.info("[Robot] Starting subsystems ...")

        # UGV — non-blocking, reconnects in background
        self.ugv.start()

        # Lift — CAN node (may raise if CAN interface is absent)
        try:
            from stepper_motor_sdk import CANopenNode
            self._lift_node = CANopenNode(
                channel=self.config.lift_can_channel,
                node_id=self.config.lift_node_id,
            )
            self._lift_node.start()
            self.lift = LiftController(self._lift_node)
            self.lift.setup()
            self.lift.home(strategy=self.config.lift_home_strategy)
        except Exception as e:
            log.warning("[Robot] Lift init attempt 1 failed: %s", e)
            # Retry once — CAN bus may need warm-up after socket creation
            if self.lift:
                self._lift_fallback_state = self.lift.get_state()
                self.lift = None
            if self._lift_node:
                try:
                    self._lift_node.stop()
                except Exception:
                    pass
                self._lift_node = None
            try:
                import time as _time
                _time.sleep(0.5)
                from stepper_motor_sdk import CANopenNode as CANopenNode2
                self._lift_node = CANopenNode2(
                    channel=self.config.lift_can_channel,
                    node_id=self.config.lift_node_id,
                )
                self._lift_node.start()
                self.lift = LiftController(self._lift_node)
                self.lift.setup()
                self.lift.home(strategy=self.config.lift_home_strategy)
                log.info("[Robot] Lift recovered on retry")
            except Exception as e2:
                log.warning("[Robot] Lift unavailable after retry: %s", e2)
                if self.lift:
                    self._lift_fallback_state = self.lift.get_state()
                if self._lift_node:
                    try:
                        self._lift_node.stop()
                    except Exception:
                        pass
                    self._lift_node = None
                self.lift = None

        # Arm / Hand — stubs, connect() returns False until SDK is wired in
        self.arm.connect()
        self.hand.connect()

        log.info("[Robot] All subsystems started")
        return self

    def stop(self) -> None:
        log.info("[Robot] Stopping subsystems ...")

        self.ugv.stop()

        if self.lift:
            try:
                self.lift.stop()
            except Exception as e:
                log.warning("[Robot] Lift stop error (ignored): %s", e)
            try:
                self.lift.shutdown()
            except Exception as e:
                log.warning("[Robot] Lift shutdown error (ignored): %s", e)

        if self._lift_node:
            try:
                self._lift_node.stop()
            except Exception:
                pass

        self.arm.disable()
        self.arm.disconnect()
        self.hand.disable()
        self.hand.disconnect()

        log.info("[Robot] All subsystems stopped")

    def __enter__(self) -> "Robot":
        return self.start()

    def __exit__(self, *_) -> None:
        self.stop()

    # ── aggregate state ───────────────────────────────────────────────────────

    def get_state(self) -> RobotState:
        return RobotState(
            ugv  = self.ugv.get_state(),
            lift = self.lift.get_state() if self.lift else (self._lift_fallback_state or LiftState()),
            arm  = self.arm.get_state(),
            hand = self.hand.get_state(),
            timestamp = time.time(),
        )

    # ── convenience helpers ───────────────────────────────────────────────────

    def emergency_stop(self) -> None:
        """Stop all motion immediately."""
        log.warning("[Robot] EMERGENCY STOP")
        self.ugv.emergency_stop()
        if self.lift:
            self.lift.stop()
        self.arm.stop()
        self.hand.stop()
