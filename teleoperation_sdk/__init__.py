"""XR Teleoperation SDK — Quest 3 headset + UR5 arm + BrainCo hand + Gimbal camera.

Two teleop backends:
  - TeleopModule (robot_control framework, WiFi/OpenXR via XRoboToolkit-PC-Service)
  - UR5eTeleopADB (standalone, USB/ADB via oculus_reader — no extra hardware needed)

VR backends (swappable data sources for TeleopModule):
  - XRBackend (WiFi/OpenXR via xrobotoolkit_sdk)
  - ADBBackend (USB/ADB via OculusReader + simple APK)
  - XRProtocolBackend (TCP via XRoboToolkit-Quest full APK)
"""

from teleoperation_sdk.teleop import TeleopModule
from teleoperation_sdk.teleop_ur5e_adb import UR5eTeleopADB
from teleoperation_sdk.ur5_ik import ur5_ik, ur5_fk, select_best_ik, pose_from_quat_xyz
from teleoperation_sdk.vr_backends import (
    VRBackend, XRBackend, ADBBackend, XRProtocolBackend, create_vr_backend,
)
from teleoperation_sdk.xr_protocol import XRProtocolReceiver

__all__ = [
    "TeleopModule",
    "UR5eTeleopADB",
    "ur5_ik", "ur5_fk", "select_best_ik", "pose_from_quat_xyz",
    "VRBackend", "XRBackend", "ADBBackend", "XRProtocolBackend",
    "create_vr_backend", "XRProtocolReceiver",
]
