"""PipeRobot — top-level integration class for the pipe inspection robot v2.

Wraps ServoController (front joints), Gimbal (pan/tilt), and DCMotorController
(drive motors) into a single programmatic API. No keyboard loop — designed for
use by the web control panel or other automation.
"""

import logging
from typing import Optional

from pr_v2_sdk.servo_lib import ServoController
from pr_v2_sdk.gimbal_lib import Gimbal
from pr_v2_sdk.dcmotor_lib import DCMotorController

log = logging.getLogger(__name__)

HEAD_IDS = [20]
TAIL_IDS = [10]

MOTOR_CMDS = [
    ("A", -75, 75),
    ("B", 75, -75),
    ("C", -75, 75),
    ("D", 75, -75),
    ("E", 75, -75),
    ("F", 75, -75),
]


class PipeRobot:
    """Top-level controller for pipe inspection robot v2 hardware."""

    def __init__(self):
        self.servo: Optional[ServoController] = None
        self.gimbal: Optional[Gimbal] = None
        self.motor: Optional[DCMotorController] = None

        # ── Servo ──
        try:
            self.servo = ServoController()
            log.info("ServoController ready")
        except Exception as e:
            log.error("ServoController init failed: %s", e)

        # ── Gimbal (shares servo UART) ──
        try:
            self.gimbal = Gimbal(uservo=self.servo.uservo if self.servo else None)
            log.info("Gimbal ready")
        except Exception as e:
            log.error("Gimbal init failed: %s", e)

        # ── DC Motors ──
        try:
            excluded = getattr(self.servo, 'SERVO_PORT_NAME', None) if self.servo else None
            self.motor = DCMotorController(excluded_port=excluded)
            log.info("DCMotorController ready")
        except Exception as e:
            log.error("DCMotorController init failed: %s", e)

    # ── Front Joints ───────────────────────────────────────────────────────

    def joint_spread(self, head_angle: int = 2100, tail_angle: int = 1070, dps: int = 400):
        if self.servo:
            self.servo.spread(
                servo_ids_angle_head=HEAD_IDS,
                servo_ids_angle_tail=TAIL_IDS,
                head_angle=head_angle,
                tail_angle=tail_angle,
                mean_dps=dps,
            )

    def joint_shrink(self, head_angle: int = 850, tail_angle: int = -180, dps: int = 400):
        if self.servo:
            self.servo.shrink(
                servo_ids_angle_head=HEAD_IDS,
                servo_ids_angle_tail=TAIL_IDS,
                head_angle=head_angle,
                tail_angle=tail_angle,
                mean_dps=dps,
            )

    def joint_stop(self):
        if self.servo:
            self.servo.stop(HEAD_IDS + TAIL_IDS)

    # ── Gimbal ─────────────────────────────────────────────────────────────

    def gimbal_move_h(self, direction: int):
        """direction: -1 (left) or +1 (right)"""
        if self.gimbal:
            self.gimbal.move_h(direction)

    def gimbal_move_p(self, direction: int):
        """direction: -1 (down) or +1 (up)"""
        if self.gimbal:
            self.gimbal.move_p(direction)

    def gimbal_home(self):
        if self.gimbal:
            self.gimbal.home()

    def gimbal_stop(self):
        if self.gimbal:
            self.gimbal.wheel_stop()

    # ── Drive Motors ───────────────────────────────────────────────────────

    def drive_forward(self):
        if self.motor:
            for mid, fwd, _ in MOTOR_CMDS:
                self.motor.send_command(f"M{mid} {fwd}")

    def drive_backward(self):
        if self.motor:
            for mid, _, bwd in MOTOR_CMDS:
                self.motor.send_command(f"M{mid} {bwd}")

    def drive_stop(self):
        if self.motor:
            self.motor.all_stop()

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def stop(self):
        """Safe shutdown — stop all motion and release hardware."""
        if self.servo:
            try:
                self.servo.stop(HEAD_IDS + TAIL_IDS)
            except Exception:
                pass
        if self.gimbal:
            try:
                self.gimbal.wheel_stop()
            except Exception:
                pass
        if self.motor:
            try:
                self.motor.all_stop()
                self.motor.close()
            except Exception:
                pass
        log.info("PipeRobot shutdown complete")
