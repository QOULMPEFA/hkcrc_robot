"""robot_control — config-driven robot control system.

Add or remove hardware modules by editing the config dict in server.py.
Each module lives in its own file and conforms to the RobotModule interface.
"""

from .robot import Robot, register_module
from .module_base import RobotModule, EventHandler

__all__ = [
    "Robot",
    "RobotModule",
    "EventHandler",
    "register_module",
]
