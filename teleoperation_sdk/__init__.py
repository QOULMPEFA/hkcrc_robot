"""XR Teleoperation SDK — Quest 3 headset + UR5 arm + BrainCo hand + Gimbal camera.

TeleopModule is the robot_control framework module. It drives the UR5 arm
(gated through the arm module) from Quest 3 tracking data received via a
swappable VR backend:
  - XRBackend (WiFi/OpenXR via xrobotoolkit_sdk)
  - XRProtocolBackend (TCP via XRoboToolkit-Quest full APK — client_quest3)
"""

from teleoperation_sdk.teleop import TeleopModule
from teleoperation_sdk.ur5_ik import ur5_ik, ur5_fk, select_best_ik, pose_from_quat_xyz
from teleoperation_sdk.vr_backends import (
    VRBackend, XRBackend, XRProtocolBackend, create_vr_backend,
)
from teleoperation_sdk.xr_protocol import XRProtocolReceiver

__all__ = [
    "TeleopModule",
    "ur5_ik", "ur5_fk", "select_best_ik", "pose_from_quat_xyz",
    "VRBackend", "XRBackend", "XRProtocolBackend",
    "create_vr_backend", "XRProtocolReceiver",
]
