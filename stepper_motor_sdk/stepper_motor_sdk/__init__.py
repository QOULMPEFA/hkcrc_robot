"""stepper_motor_sdk — CiA 402 stepper motor control over CANopen."""

from .bus import SharedCANBus
from .constants import (
    OD, Controlword, Statusword, OperationMode,
    HomingMethod, COBType, DEFAULT_NODE_ID, DEFAULT_BITRATE,
    DEFAULT_PULSES_PER_REV,
)
from .cia402 import DriveState, decode_statusword
from .sdo import SDOClient, SDOError, SDOAbortError
from .node import CANopenNode
from .motor import StepperMotor

__all__ = [
    "StepperMotor",
    "CANopenNode",
    "SharedCANBus",
    "SDOClient",
    "SDOError",
    "SDOAbortError",
    "DriveState",
    "OperationMode",
    "HomingMethod",
    "OD",
    "Controlword",
    "Statusword",
    "COBType",
    "DEFAULT_NODE_ID",
    "DEFAULT_BITRATE",
    "DEFAULT_PULSES_PER_REV",
    "decode_statusword",
]

__version__ = "1.0.0"
