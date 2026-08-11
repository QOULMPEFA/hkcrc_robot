"""XR-Robotics style UR5e teleoperation core.

The controller pose is transformed into the same DH base frame used by the
control panel's _ur5eFkFrames() skeleton.  The end-effector target is a
relative delta from the pose captured when the grip button becomes active.
DH numerical IK solves the target joints and UR RTDE sends servoj().
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)

# Derived from the control panel DH skeleton.  The visible robot scene is:
# DH base -> robotFrame -> baseFrame -> mirrorFrame -> modelAdjustFrame
# (0,-45,90) -> modelFrame -> modelZFrame.  R is that composite inverse, so a
# right/up/forward hand input becomes a right/up/forward robot 3D displacement.
R_HEADSET_TO_WORLD = np.array(
    [
        [-0.7071067811865476, -0.7071067811865476, 0.0],
        [0.0, 0.0, 1.0],
        [-0.7071067811865476, 0.7071067811865476, 0.0],
    ]
)

CONTROLLER_DEADZONE = 0.1

DEFAULT_SCALE_FACTOR = 0.8
DEFAULT_SERVO_TIME = 0.017
DEFAULT_LOOKAHEAD_TIME = 0.1
DEFAULT_SERVO_GAIN = 300.0
DEFAULT_MAX_VELOCITY = 0.5
DEFAULT_MAX_ACCELERATION = 1.0
DEFAULT_CONTROL_RATE_HZ = 60.0

def is_valid_quaternion(quat, tol: float = 1e-6) -> bool:
    if not isinstance(quat, (list, tuple, np.ndarray)) or len(quat) != 4:
        return False
    if not np.all(np.isfinite(quat)):
        return False
    magnitude = np.sqrt(np.sum(np.square(quat)))
    return abs(magnitude - 1.0) <= tol


def quaternion_multiply(q1: np.ndarray, q2: np.ndarray) -> np.ndarray:
    """Hamilton product with quaternions ordered as (w, x, y, z)."""
    w1, x1, y1, z1 = q1
    w2, x2, y2, z2 = q2
    return np.array(
        [
            w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
            w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
            w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        ],
        dtype=float,
    )


def quaternion_inverse(q: np.ndarray) -> np.ndarray:
    q = np.asarray(q, dtype=float)
    norm_sq = np.dot(q, q)
    if norm_sq <= 0.0:
        return np.array([1.0, 0.0, 0.0, 0.0])
    return np.array([q[0], -q[1], -q[2], -q[3]]) / norm_sq


def quaternion_about_axis(angle: float, axis: np.ndarray) -> np.ndarray:
    axis = np.asarray(axis, dtype=float)
    axis = axis / np.linalg.norm(axis)
    half = angle / 2.0
    return np.array(
        [math.cos(half), axis[0] * math.sin(half), axis[1] * math.sin(half), axis[2] * math.sin(half)],
        dtype=float,
    )


def quaternion_to_angle_axis(quat: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    """Convert a quaternion to an angle-axis vector [ax*angle, ay*angle, az*angle]."""
    q = np.asarray(quat, dtype=float)
    if q[0] < 0.0:
        q = -q

    w = q[0]
    vec_part = q[1:]
    angle = 2.0 * math.acos(max(-1.0, min(1.0, w)))
    if angle < eps:
        return np.zeros(3, dtype=float)

    sin_half_angle = math.sin(angle / 2.0)
    if abs(sin_half_angle) < eps:
        return np.zeros(3, dtype=float)

    axis = vec_part / sin_half_angle
    return axis * angle


def quat_diff_as_angle_axis(q1: np.ndarray, q2: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    """Calculate DeltaQ such that q2 = DeltaQ * q1, returned as angle-axis."""
    if not (is_valid_quaternion(q1, tol=eps) and is_valid_quaternion(q2, tol=eps)):
        log.warning("Invalid quaternion input to quat_diff_as_angle_axis")

    delta_q = quaternion_multiply(np.asarray(q2, dtype=float), quaternion_inverse(q1))
    return quaternion_to_angle_axis(delta_q, eps)


def apply_delta_pose(
    source_pos: np.ndarray,
    source_rot: np.ndarray,
    delta_pos: np.ndarray,
    delta_rot: np.ndarray,
    eps: float = 1.0e-6,
) -> Tuple[np.ndarray, np.ndarray]:
    """Apply a relative controller delta to an absolute robot pose."""
    target_pos = np.asarray(source_pos, dtype=float) + np.asarray(delta_pos, dtype=float)

    angle = np.linalg.norm(delta_rot)
    if angle > eps:
        axis = np.asarray(delta_rot, dtype=float) / angle
        rot_delta_quat = quaternion_about_axis(angle, axis)
    else:
        rot_delta_quat = np.array([1.0, 0.0, 0.0, 0.0])

    target_rot = quaternion_multiply(rot_delta_quat, np.asarray(source_rot, dtype=float))
    return target_pos, target_rot


def quaternion_from_matrix(matrix: np.ndarray) -> np.ndarray:
    """Return (w, x, y, z) from a 3x3 or 4x4 rotation matrix."""
    m = np.asarray(matrix, dtype=float)
    m = m[:3, :3]
    trace = m[0, 0] + m[1, 1] + m[2, 2]
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * s
        x = (m[2, 1] - m[1, 2]) / s
        y = (m[0, 2] - m[2, 0]) / s
        z = (m[1, 0] - m[0, 1]) / s
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2.0
        w = (m[2, 1] - m[1, 2]) / s
        x = 0.25 * s
        y = (m[0, 1] + m[1, 0]) / s
        z = (m[0, 2] + m[2, 0]) / s
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2.0
        w = (m[0, 2] - m[2, 0]) / s
        x = (m[0, 1] + m[1, 0]) / s
        y = 0.25 * s
        z = (m[1, 2] + m[2, 1]) / s
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2.0
        w = (m[1, 0] - m[0, 1]) / s
        x = (m[0, 2] + m[2, 0]) / s
        y = (m[1, 2] + m[2, 1]) / s
        z = 0.25 * s

    q = np.array([w, x, y, z], dtype=float)
    return q / np.linalg.norm(q)


def quaternion_matrix(quat: np.ndarray) -> np.ndarray:
    """Return a 4x4 homogeneous transform from (w, x, y, z)."""
    q = np.asarray(quat, dtype=float)
    q = q / np.linalg.norm(q)
    w, x, y, z = q
    return np.array(
        [
            [1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w), 0.0],
            [2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w), 0.0],
            [2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y), 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=float,
    )


def rotate_quat_by_matrix(quat: np.ndarray, rotation: np.ndarray) -> np.ndarray:
    """Rotate a quaternion by the same rotation as a 3x3 matrix."""
    r4 = np.eye(4)
    r4[:3, :3] = rotation
    qm = quaternion_matrix(quat)
    return quaternion_from_matrix(r4 @ qm @ r4.T)


def apply_headset_to_world(pose: List[float]) -> List[float]:
    """Transform an XR pose [x,y,z,qx,qy,qz,qw] with R_HEADSET_TO_WORLD."""
    xyz = R_HEADSET_TO_WORLD @ np.asarray(pose[:3], dtype=float)
    quat = rotate_quat_by_matrix(
        np.asarray([pose[6], pose[3], pose[4], pose[5]], dtype=float),
        R_HEADSET_TO_WORLD,
    )
    return [float(xyz[0]), float(xyz[1]), float(xyz[2]), float(quat[1]), float(quat[2]), float(quat[3]), float(quat[0])]


class URRTDEAdapter:
    """UR RTDE servo control, without the upstream Robotiq gripper dependency."""

    def __init__(
        self,
        robot_ip: str,
        max_velocity: float = DEFAULT_MAX_VELOCITY,
        max_acceleration: float = DEFAULT_MAX_ACCELERATION,
        servo_time: float = DEFAULT_SERVO_TIME,
        lookahead_time: float = DEFAULT_LOOKAHEAD_TIME,
        servo_gain: float = DEFAULT_SERVO_GAIN,
    ):
        self.robot_ip = robot_ip
        self.max_velocity = float(max_velocity)
        self.max_acceleration = float(max_acceleration)
        self.servo_time = float(servo_time)
        self.lookahead_time = float(lookahead_time)
        self.servo_gain = float(servo_gain)

        import rtde_control
        import rtde_receive

        self.rtde_c = rtde_control.RTDEControlInterface(robot_ip)
        self.rtde_r = rtde_receive.RTDEReceiveInterface(robot_ip)
        log.info("[XR-UR5e] Connected to UR RTDE at %s", robot_ip)

    def get_current_joint_positions(self) -> np.ndarray:
        return np.asarray(self.rtde_r.getActualQ(), dtype=float)

    def start_control_script(self) -> None:
        """Start the UR RTDE control script without moving the arm.

        ur_rtde servoJ() requires the RTDE control script to be running.
        Calling moveJ() with the current actual joints initializes it while
        keeping the requested target equal to the current pose.
        """
        current = self.get_current_joint_positions()
        last_error = None
        for attempt in range(3):
            result = self.rtde_c.moveJ(current)
            if result is not False:
                return
            last_error = RuntimeError("RTDE control script failed to start")
            log.warning("[XR-UR5e] RTDE control script start attempt %d failed", attempt + 1)
            time.sleep(0.2)
        raise last_error or RuntimeError("RTDE control script failed to start")

    def servo_joints(self, joint_positions: np.ndarray) -> None:
        t_start = self.rtde_c.initPeriod()
        result = self.rtde_c.servoJ(
            joint_positions,
            self.max_velocity,
            self.max_acceleration,
            self.servo_time,
            self.lookahead_time,
            self.servo_gain,
        )
        if result is False:
            self.start_control_script()
            t_start = self.rtde_c.initPeriod()
            result = self.rtde_c.servoJ(
                joint_positions,
                self.max_velocity,
                self.max_acceleration,
                self.servo_time,
                self.lookahead_time,
                self.servo_gain,
            )
            if result is False:
                raise RuntimeError("servoJ failed after RTDE restart")
        self.rtde_c.waitPeriod(t_start)

    def set_speed_limits(self, max_velocity: float, max_acceleration: float) -> None:
        self.max_velocity = float(max_velocity)
        self.max_acceleration = float(max_acceleration)

    def stop(self) -> None:
        for method in ("servoStop", "stopScript"):
            try:
                getattr(self.rtde_c, method)()
            except Exception:
                log.debug("[XR-UR5e] RTDE %s failed during stop", method)
        for rtde, name in ((self.rtde_c, "rtde_c"), (self.rtde_r, "rtde_r")):
            try:
                rtde.disconnect()
            except Exception:
                log.debug("[XR-UR5e] %s disconnect failed during stop", name)


class SimulatedUR5eAdapter:
    """In-memory UR adapter for teleop direction tests when the arm is off."""

    HOME_ANGLES_DEG = [90.0, 0.0, 90.0, -90.0, 90.0, -135.0]
    CONTROL_HZ = 60.0

    def __init__(
        self,
        initial_q: Optional[np.ndarray] = None,
        max_velocity: float = DEFAULT_MAX_VELOCITY,
        max_acceleration: float = DEFAULT_MAX_ACCELERATION,
    ):
        if (
            initial_q is None
            or len(initial_q) != 6
            or not np.all(np.isfinite(initial_q))
            or np.allclose(initial_q, 0.0)
        ):
            initial_q = np.deg2rad(self.HOME_ANGLES_DEG)
        self.current_q = np.asarray(initial_q, dtype=float).copy()
        self.max_velocity = float(max_velocity)
        self.max_acceleration = float(max_acceleration)
        self.rtde_c = _SimulatedRTDEC(self)

    def get_current_joint_positions(self) -> np.ndarray:
        return np.array(self.current_q, dtype=float)

    def start_control_script(self) -> None:
        return None

    def servo_joints(self, joint_positions: np.ndarray) -> None:
        target = np.asarray(joint_positions, dtype=float)
        step = max(0.001, self.max_velocity / self.CONTROL_HZ)
        delta = np.clip(target - self.current_q, -step, step)
        self.current_q += delta
        if np.all(np.abs(target - self.current_q) <= step * 0.5):
            self.current_q = target.copy()

    def set_speed_limits(self, max_velocity: float, max_acceleration: float) -> None:
        self.max_velocity = float(max_velocity)
        self.max_acceleration = float(max_acceleration)

    def stop(self) -> None:
        return None


class _SimulatedRTDEC:
    def __init__(self, adapter: SimulatedUR5eAdapter):
        self.adapter = adapter

    def servoStop(self) -> None:
        return None

    def moveJ(self, joint_positions: np.ndarray, **kwargs) -> bool:
        self.adapter.current_q = np.asarray(joint_positions, dtype=float).copy()
        return True


class XrUr5eController:
    """One-arm controller using the XR-Robotics sample teleoperation algorithm."""

    def __init__(self, config: Dict[str, Any]):
        cfg = config or {}
        self.robot_ip = str(cfg.get("host", "192.168.56.101"))
        self.scale_factor = float(cfg.get("scale_factor", DEFAULT_SCALE_FACTOR))
        self.max_velocity = float(cfg.get("max_velocity", DEFAULT_MAX_VELOCITY))
        self.max_acceleration = float(cfg.get("max_acceleration", DEFAULT_MAX_ACCELERATION))
        self.servo_time = float(cfg.get("servo_time", DEFAULT_SERVO_TIME))
        self.lookahead_time = float(cfg.get("lookahead_time", DEFAULT_LOOKAHEAD_TIME))
        self.servo_gain = float(cfg.get("servo_gain", DEFAULT_SERVO_GAIN))
        self.control_mode = str(cfg.get("control_mode", "pose"))
        self.simulate = bool(cfg.get("simulate", False))

        self.ur: Optional[URRTDEAdapter] = None

        self.ref_controller_xyz: Optional[np.ndarray] = None
        self.ref_controller_quat: Optional[np.ndarray] = None
        self.ref_ee_xyz: Optional[np.ndarray] = None
        self.ref_ee_quat: Optional[np.ndarray] = None

        self.current_q = np.zeros(6, dtype=float)
        self.target_q = np.zeros(6, dtype=float)
        self.active = False
        self.tracking = False

    def connect(self) -> None:
        if self.simulate:
            self.ur = SimulatedUR5eAdapter(
                initial_q=self.current_q,
                max_velocity=self.max_velocity,
                max_acceleration=self.max_acceleration,
            )
        else:
            self.ur = URRTDEAdapter(
                robot_ip=self.robot_ip,
                max_velocity=self.max_velocity,
                max_acceleration=self.max_acceleration,
                servo_time=self.servo_time,
                lookahead_time=self.lookahead_time,
                servo_gain=self.servo_gain,
            )
        try:
            self.current_q = self.ur.get_current_joint_positions()
            self.target_q = self.current_q.copy()
            self.ur.start_control_script()
        except Exception:
            self.ur.stop()
            raise
        log.info("[XR-UR5e] Teleop controller connected")

    def stop(self) -> None:
        if self.ur is not None:
            try:
                self.ur.stop()
            finally:
                self.ur = None
        self.active = False
        self.tracking = False
        self.ref_controller_xyz = None
        self.ref_controller_quat = None
        self.ref_ee_xyz = None
        self.ref_ee_quat = None

    def set_reference_pose(self, xr_pose: List[float],
                           headset_pose: Optional[List[float]] = None) -> None:
        xyz, quat = self._pose_to_headset_world(xr_pose)
        self.ref_controller_xyz = xyz
        self.ref_controller_quat = quat

    def step(self, xr_pose: Optional[List[float]], grip: float) -> None:
        if self.ur is None:
            return

        if xr_pose is None:
            self._deactivate()
            return

        active = float(grip) > (1.0 - CONTROLLER_DEADZONE)
        if active:
            if self.ref_ee_xyz is None:
                from teleoperation_sdk.ur5_ik import ur5_fk
                current_pose = np.asarray(ur5_fk(self.current_q), dtype=float)
                self.ref_ee_xyz = current_pose[:3, 3]
                self.ref_ee_quat = quaternion_from_matrix(current_pose[:3, :3])

            delta_xyz, delta_rot = self._process_xr_pose(xr_pose)
            delta_xyz = np.asarray(delta_xyz, dtype=float) * 1000.0
            if self.control_mode == "position":
                target_xyz = self.ref_ee_xyz + delta_xyz
                target_quat = self.ref_ee_quat.copy()
            else:
                target_xyz, target_quat = apply_delta_pose(
                    self.ref_ee_xyz,
                    self.ref_ee_quat,
                    delta_xyz,
                    delta_rot,
                )
            target_frame = quaternion_matrix(target_quat)
            target_frame[:3, 3] = target_xyz
            from teleoperation_sdk.ur5_ik import ur5_ik
            q = ur5_ik(
                target_frame.tolist(),
                seed_joints_rad=list(self.current_q),
            )
            if q is not None:
                self.target_q = np.asarray(q, dtype=float)
            self.active = True
            self.tracking = True
        else:
            self._deactivate()

        self.current_q = self.ur.get_current_joint_positions()

    def send(self) -> None:
        if self.ur is not None:
            self.ur.servo_joints(self.target_q)

    def set_speed_limits(self, max_velocity: float, max_acceleration: float) -> None:
        self.max_velocity = float(max_velocity)
        self.max_acceleration = float(max_acceleration)
        if self.ur is not None:
            self.ur.set_speed_limits(self.max_velocity, self.max_acceleration)

    def go_home(self, angles_deg: List[float]) -> bool:
        if self.ur is None:
            return False

        target_q = np.deg2rad(np.asarray(angles_deg, dtype=float))
        self.ur.rtde_c.servoStop()
        time.sleep(0.05)
        result = self.ur.rtde_c.moveJ(
            target_q,
            speed=self.max_velocity,
            acceleration=self.max_acceleration,
        )
        if not result:
            log.warning("[XR-UR5e] moveJ home failed; restarting RTDE control script")
            try:
                self.ur.start_control_script()
                result = self.ur.rtde_c.moveJ(
                    target_q,
                    speed=self.max_velocity,
                    acceleration=self.max_acceleration,
                )
            except Exception as e:
                log.error("[XR-UR5e] RTDE home restart failed: %s", e)
                return False

        if not result:
            log.error("[XR-UR5e] moveJ home returned failure")
            return False

        self.target_q = target_q
        self.current_q = self.ur.get_current_joint_positions()
        return True

    def _deactivate(self) -> None:
        self.ref_controller_xyz = None
        self.ref_controller_quat = None
        self.ref_ee_xyz = None
        self.ref_ee_quat = None
        self.active = False
        self.tracking = False

    def _pose_to_headset_world(self, xr_pose: List[float]) -> Tuple[np.ndarray, np.ndarray]:
        xyz = R_HEADSET_TO_WORLD @ np.asarray(xr_pose[:3], dtype=float)
        quat = rotate_quat_by_matrix(
            np.asarray([xr_pose[6], xr_pose[3], xr_pose[4], xr_pose[5]], dtype=float),
            R_HEADSET_TO_WORLD,
        )
        return xyz, quat

    def _process_xr_pose(self, xr_pose: List[float]) -> Tuple[np.ndarray, np.ndarray]:
        controller_xyz, controller_quat = self._pose_to_headset_world(xr_pose)

        if self.ref_controller_xyz is None:
            self.ref_controller_xyz = controller_xyz.copy()
            self.ref_controller_quat = controller_quat.copy()
            delta_xyz = np.zeros(3)
            delta_rot = np.zeros(3)
        else:
            delta_xyz = (controller_xyz - self.ref_controller_xyz) * self.scale_factor
            delta_rot = quat_diff_as_angle_axis(
                self.ref_controller_quat, controller_quat)
        return delta_xyz, delta_rot

    def get_state(self) -> Dict[str, Any]:
        return {
            "connected": self.ur is not None,
            "simulate": self.simulate,
            "active": self.active,
            "tracking": self.tracking,
            "target_joints_deg": [math.degrees(float(v)) for v in self.target_q],
            "current_joints_deg": [math.degrees(float(v)) for v in self.current_q],
        }
