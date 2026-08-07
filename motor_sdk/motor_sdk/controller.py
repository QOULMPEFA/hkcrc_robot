"""Differential drive using two DC motors for pipe robot locomotion."""

import logging
from motor_sdk.motor import DCMotor

log = logging.getLogger(__name__)


class DifferentialDrive:
    """Two-motor differential steering for tracked/pipe robots.

    Direction mapping (matches pipe robot mini physical motor orientation):
        forward:  left=forward,   right=backward
        backward: left=backward,  right=forward
        left:     left=backward,  right=backward
        right:    left=forward,   right=forward

    Each motor's effective direction can be reversed via config flags
    if the physical wiring differs from the reference setup.
    """

    def __init__(self, left_motor: DCMotor, right_motor: DCMotor):
        self.left = left_motor
        self.right = right_motor
        self._current = "stop"

    def forward(self):
        self._current = "forward"
        self.left.forward()
        self.right.backward()
        log.info("[Drive] forward")

    def backward(self):
        self._current = "backward"
        self.left.backward()
        self.right.forward()
        log.info("[Drive] backward")

    def turn_left(self):
        self._current = "left"
        self.left.backward()
        self.right.backward()
        log.info("[Drive] turn_left")

    def turn_right(self):
        self._current = "right"
        self.left.forward()
        self.right.forward()
        log.info("[Drive] turn_right")

    def stop(self):
        self._current = "stop"
        self.left.stop()
        self.right.stop()
        log.info("[Drive] stop")

    @property
    def direction(self) -> str:
        return self._current

    @property
    def is_moving(self) -> bool:
        return self._current != "stop"
