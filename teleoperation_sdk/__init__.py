"""XR Teleoperation SDK — Quest 3 headset + UR5 arm + BrainCo hand + Gimbal camera.

Portable package: copy this whole folder to any project that needs XR teleoperation.
Dependencies: robot_control.module_base (RobotModule ABC), xrobotoolkit_sdk, gimbal_sdk.
"""

from teleoperation_sdk.teleop import TeleopModule
from teleoperation_sdk.ur5_ik import ur5_ik, ur5_fk, select_best_ik, pose_from_quat_xyz

__all__ = ["TeleopModule", "ur5_ik", "ur5_fk", "select_best_ik", "pose_from_quat_xyz"]
