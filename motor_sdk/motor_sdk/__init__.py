"""Motor SDK — DC motor control via Orange Pi GPIO (H-bridge).

Provides:
    DCMotor         — single DC motor with H-bridge (IN1/IN2 GPIO pins)
    DifferentialDrive — two-motor differential steering for pipe robot
"""

from motor_sdk.motor import DCMotor
from motor_sdk.controller import DifferentialDrive

__all__ = ["DCMotor", "DifferentialDrive"]
