"""XR Teleoperation SDK — Quest 3 headset + UR5e arm.

TeleopModule is the robot_control framework module. It drives the UR5 arm
from Quest 3 tracking data received via a swappable VR backend:
  - XRBackend (WiFi/OpenXR via xrobotoolkit_sdk)
  - XRProtocolBackend (TCP via XRoboToolkit-Quest full APK — client_quest3)

The arm control algorithm follows XR-Robotics: grip is the dead-man switch,
controller pose is mapped with R_HEADSET_TO_WORLD, Placo solves IK, and UR
RTDE sends continuous servoj() targets.
"""

from teleoperation_sdk.teleop import TeleopModule
from teleoperation_sdk.xr_ur5e import XrUr5eController, R_HEADSET_TO_WORLD
from teleoperation_sdk.ur5_ik import ur5_ik, ur5_fk, select_best_ik, pose_from_quat_xyz
from teleoperation_sdk.vr_backends import (
    VRBackend, XRBackend, XRProtocolBackend, create_vr_backend,
)
from teleoperation_sdk.xr_protocol import XRProtocolReceiver

__all__ = [
    "TeleopModule",
    "XrUr5eController", "R_HEADSET_TO_WORLD",
    "ur5_ik", "ur5_fk", "select_best_ik", "pose_from_quat_xyz",
    "VRBackend", "XRBackend", "XRProtocolBackend",
    "create_vr_backend", "XRProtocolReceiver",
]
