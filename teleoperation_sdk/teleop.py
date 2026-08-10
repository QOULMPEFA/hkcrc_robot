"""Robot control panel adapter for XR-Robotics Quest3 -> UR5e teleoperation.

The old direction-matrix and trigger-once logic has been removed.  The backend
now follows XRoboToolkit-Teleop-Sample-Python:

  - Quest/XR pose comes from XRoboToolkit-PC-Service through xrobotoolkit_sdk,
    or from the XRProtocolReceiver for local protocol testing.
  - The grip button is the dead-man switch.
  - Controller pose is converted with R_HEADSET_TO_WORLD.
  - Placo solves IK for the selected UR5e link.
  - UR RTDE sends continuous servoj() targets.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any, Dict, List, Optional

from robot_control.module_base import RobotModule

from teleoperation_sdk.vr_backends import VRBackend, create_vr_backend
from teleoperation_sdk.xr_ur5e import (
    XrUr5eController,
)

log = logging.getLogger(__name__)

CONTROL_HZ = 60.0
POSE_POLL_HZ = 30.0
GRIP_UI_THRESHOLD = 0.5


class TeleopModule(RobotModule):
    """RobotModule wrapper around XrUr5eController."""

    def __init__(self, config: Optional[Dict[str, Any]] = None, robot=None):
        cfg = config or {}
        self.robot = robot
        self._arm_controller = str(cfg.get("arm_controller", "left"))
        self._vr_backend_name = str(cfg.get("vr_backend", "xrt"))
        self._vr_backend_kwargs = dict(cfg.get("vr_backend_kwargs", {}) or {})
        self._controller_config = dict(cfg)
        self._control_hz = float(cfg.get("control_rate_hz", CONTROL_HZ))

        self._lock = threading.RLock()
        self._servo_lock = threading.RLock()
        self._vr_backend: Optional[VRBackend] = None
        self._controller: Optional[XrUr5eController] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

        self._xr_initialized = False
        self._connected = False
        self._active = False
        self._tracking = False
        self._calibrated = False
        self._is_fault = False
        self._fault_text = ""

        self._ctrl_pos = [0.0, 0.0, 0.0]
        self._ctrl_quat = [0.0, 0.0, 0.0, 1.0]
        self._calib_pos = [0.0, 0.0, 0.0]
        self._calib_rot = [0.0, 0.0, 0.0, 1.0]
        self._trigger_val = 0.0
        self._grip_val = 0.0
        self._grip_pressed = False
        self._button_a = False
        self._button_b = False

        self._target_joints_deg = [0.0] * 6
        self._current_joints_deg = [0.0] * 6
        self._data_log_enabled = False

    # ── RobotModule metadata ───────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "teleop"

    @property
    def display_name(self) -> str:
        return "XR 遥操作"

    def get_state_schema(self) -> Dict[str, Any]:
        return {
            "connected": False,
            "xr_initialized": False,
            "active": False,
            "tracking": False,
            "calibrated": False,
            "controller_position": [0.0, 0.0, 0.0],
            "controller_pose": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
            "controller_pose_3d": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
            "controller_pose_ur": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
            "translation_mapping": "natural",
            "controller_ypr_deg": [0.0, 0.0, 0.0],
            "current_pose_xyz": [0.0, 0.0, 0.0],
            "current_pose_rpy_deg": [0.0, 0.0, 0.0],
            "target_pose_xyz": [0.0, 0.0, 0.0],
            "target_pose_rpy_deg": [0.0, 0.0, 0.0],
            "arm_fk_current": [],
            "arm_fk_target": [],
            "trigger_value": 0.0,
            "trigger_exec_active": False,
            "grip_pressed": False,
            "button_a": False,
            "button_b": False,
            "target_joints_deg": [0.0] * 6,
            "current_joints_deg": [0.0] * 6,
            "is_fault": False,
            "fault_text": "",
            "data_log_recording": False,
            "gimbal_available": False,
            "gimbal_tracking": False,
            "gimbal_calibrated": False,
            "head_yaw_deg": 0.0,
            "head_pitch_deg": 0.0,
            "head_pose_available": False,
            "head_yaw_gain": 1.0,
            "head_pitch_gain": 1.0,
            "head_deadband": 0.3,
            "head_smoothing": 0.25,
            "head_rate_limit": 300.0,
            "head_deadzone": 3.0,
            "head_precision": 0.5,
            "gimbal_yaw_limits": [-90, 90],
            "gimbal_pitch_limits": [-50, 60],
            "video_running": False,
            "video_pid": 0,
            "video_client": "",
            "video_source": "",
            "video_control_port": 13579,
            "video_udp_port": 10004,
            "video_error": "",
        }

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            controller_state = self._controller.get_state() if self._controller else {}
            current_deg = controller_state.get("current_joints_deg", self._current_joints_deg)
            target_deg = controller_state.get("target_joints_deg", self._target_joints_deg)
            current_pose = self._pose_from_joints(current_deg)
            target_pose = self._pose_from_joints(target_deg)
            return {
                "connected": self._connected or bool(controller_state.get("connected")),
                "xr_initialized": self._xr_initialized,
                "active": self._active,
                "tracking": bool(controller_state.get("tracking", self._tracking)),
                "calibrated": self._calibrated,
                "controller_position": list(self._ctrl_pos),
                "controller_pose": list(self._ctrl_pos) + list(self._ctrl_quat),
                "controller_pose_3d": self._controller_pose_to_world(),
                "controller_pose_ur": self._controller_pose_to_world(),
                "translation_mapping": "natural",
                "controller_ypr_deg": self._quat_to_ypr_deg(self._ctrl_quat),
                "current_pose_xyz": current_pose[:3],
                "current_pose_rpy_deg": current_pose[3:],
                "target_pose_xyz": target_pose[:3],
                "target_pose_rpy_deg": target_pose[3:],
                "arm_fk_current": [],
                "arm_fk_target": [],
                "trigger_value": self._trigger_val,
                "trigger_exec_active": False,
                "grip_pressed": self._grip_pressed,
                "button_a": self._button_a,
                "button_b": self._button_b,
                "target_joints_deg": list(target_deg),
                "current_joints_deg": list(current_deg),
                "is_fault": self._is_fault,
                "fault_text": self._fault_text,
                "data_log_recording": self._data_log_enabled,
                "gimbal_available": self.robot is not None and self.robot.is_loaded("gimbal"),
                "gimbal_tracking": False,
                "gimbal_calibrated": False,
                "head_yaw_deg": 0.0,
                "head_pitch_deg": 0.0,
                "head_pose_available": False,
                "head_yaw_gain": 1.0,
                "head_pitch_gain": 1.0,
                "head_deadband": 0.3,
                "head_smoothing": 0.25,
                "head_rate_limit": 300.0,
                "head_deadzone": 3.0,
                "head_precision": 0.5,
                "gimbal_yaw_limits": [-90, 90],
                "gimbal_pitch_limits": [-50, 60],
                "video_running": False,
                "video_pid": 0,
                "video_client": "",
                "video_source": "",
                "video_control_port": 13579,
                "video_udp_port": 10004,
                "video_error": "",
            }

    def get_controller_pose_state(self) -> Dict[str, Any]:
        self._read_controller()
        with self._lock:
            controller_state = self._controller.get_state() if self._controller else {}
            state = {
                "controller_position": list(self._ctrl_pos),
                "controller_pose": list(self._ctrl_pos) + list(self._ctrl_quat),
                "controller_pose_3d": self._controller_pose_to_world(),
                "controller_pose_ur": self._controller_pose_to_world(),
                "translation_mapping": "natural",
                "trigger_value": self._trigger_val,
                "grip_pressed": self._grip_pressed,
                "button_a": self._button_a,
                "button_b": self._button_b,
                "controller_ypr_deg": self._quat_to_ypr_deg(self._ctrl_quat),
                "target_joints_deg": controller_state.get(
                    "target_joints_deg", self._target_joints_deg),
                "current_joints_deg": controller_state.get(
                    "current_joints_deg", self._current_joints_deg),
            }
        return state

    def get_socketio_handlers(self) -> Dict[str, Any]:
        return {
            "teleop_enable": self._on_enable,
            "teleop_disable": self._on_disable,
            "teleop_calibrate": self._on_calibrate,
            "teleop_set_translation_mapping": self._on_set_translation_mapping,
            "teleop_toggle_log": self._on_toggle_log,
            "teleop_gimbal_track_enable": self._on_gimbal_removed,
            "teleop_gimbal_track_disable": self._on_gimbal_removed,
            "teleop_gimbal_calibrate": self._on_gimbal_removed,
            "teleop_gimbal_home": self._on_gimbal_removed,
            "teleop_video_start": self._on_video_removed,
            "teleop_video_stop": self._on_video_removed,
            "teleop_video_restart": self._on_video_removed,
            "teleop_video_log": self._on_video_removed,
        }

    # ── lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        backend = create_vr_backend(self._vr_backend_name, **self._vr_backend_kwargs)
        if backend is None:
            log.warning("[Teleop] Unknown VR backend %r", self._vr_backend_name)
            return
        if not backend.init():
            log.warning("[Teleop] VR backend %s failed to initialize", self._vr_backend_name)
            return

        self._vr_backend = backend
        self._xr_initialized = True
        self._connected = True
        self._running = True
        self._thread = threading.Thread(target=self._control_loop, daemon=True, name="teleop-ctrl")
        self._thread.start()
        log.info("[Teleop] XR backend %s initialized", self._vr_backend_name)

    def stop(self) -> None:
        self._running = False
        if self._controller is not None:
            try:
                self._controller.stop()
            finally:
                self._controller = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        if self._vr_backend is not None:
            try:
                self._vr_backend.close()
            except Exception:
                pass
            self._vr_backend = None
        self._xr_initialized = False
        self._connected = False
        self._active = False
        self._tracking = False
        self._gating(False)
        log.info("[Teleop] Stopped")

    def emergency_stop(self) -> None:
        log.warning("[Teleop] EMERGENCY STOP")
        with self._lock:
            self._is_fault = True
            self._fault_text = "EMERGENCY STOP"
        self._disable_controller()
        self._gating(False)
        with self._lock:
            self._active = False
            self._tracking = False

    # ── SocketIO handlers ──────────────────────────────────────────────────

    def _on_enable(self, _data: dict = None, emit=None) -> None:
        if not self._xr_initialized or self._vr_backend is None:
            if emit:
                emit("error", {"msg": "XR backend not initialized"})
            return
        if self._is_fault:
            if emit:
                emit("error", {"msg": "Teleop in fault state"})
            return
        if not self._calibrated:
            self._on_calibrate(emit=emit)
            if not self._calibrated:
                return

        if self._active and self._controller is not None:
            if emit:
                emit("teleop_enable_result", {"ok": True, "active": True})
            return

        try:
            self._controller = XrUr5eController(self._controller_config)
            self._controller.connect()
        except Exception as e:
            self._controller = None
            log.exception("[Teleop] Enable failed")
            if emit:
                emit("error", {"msg": f"Teleop enable failed: {e}"})
            return

        self._gating(True)
        with self._lock:
            self._active = True
            self._tracking = False
        log.info("[Teleop] Enabled")
        if emit:
            emit("teleop_enable_result", {"ok": True, "active": True})

    def _on_disable(self, _data: dict = None, emit=None) -> None:
        self._disable_controller()
        self._gating(False)
        with self._lock:
            self._active = False
            self._tracking = False
        log.info("[Teleop] Disabled")
        if emit:
            emit("teleop_disable_result", {"ok": True, "active": False})

    def _on_calibrate(self, _data: dict = None, emit=None) -> None:
        pose = self._vr_backend.get_controller_pose(self._arm_controller) if self._vr_backend else None
        if pose is None or len(pose) < 7:
            if emit:
                emit("error", {"msg": "No controller pose available for calibration"})
            return

        with self._lock:
            self._calib_pos = [float(v) for v in pose[:3]]
            self._calib_rot = [float(v) for v in pose[3:7]]
            self._calibrated = True
            self._ctrl_pos = list(self._calib_pos)
            self._ctrl_quat = list(self._calib_rot)
        if self._controller is not None:
            try:
                self._controller.set_reference_pose(pose)
            except Exception as e:
                log.warning("[Teleop] Reference pose update failed: %s", e)
        log.info("[Teleop] Calibrated at %s", [round(v, 3) for v in self._calib_pos])
        if emit:
            emit("teleop_calibrate_result", {
                "ok": True,
                "calibrated": True,
                "controller_position": list(self._calib_pos),
            })

    def _on_set_translation_mapping(self, _data: dict = None, emit=None) -> None:
        # The rewritten backend follows the upstream coordinate transform and
        # intentionally has no live direction-mode hacks.
        pose = self._controller_pose_to_world()
        if emit:
            emit("teleop_mapping_result", {
                "ok": True,
                "mode": "natural",
                "controller_pose_3d": pose,
                "controller_pose_ur": pose,
            })

    def _on_toggle_log(self, _data: dict = None, emit=None) -> None:
        with self._lock:
            self._data_log_enabled = not self._data_log_enabled
        log.info("[Teleop] Data logging toggled: %s", self._data_log_enabled)

    def _on_gimbal_removed(self, _data: dict = None, emit=None) -> None:
        if emit:
            emit("error", {"msg": "Gimbal teleop removed; use the gimbal panel"})

    def _on_video_removed(self, _data: dict = None, emit=None) -> None:
        if emit:
            emit("error", {"msg": "Quest video bridge removed from teleop backend"})

    # ── control loop ───────────────────────────────────────────────────────

    def _control_loop(self) -> None:
        interval = 1.0 / self._control_hz
        idle_interval = 1.0 / POSE_POLL_HZ
        while self._running:
            try:
                controller = self._controller
                if self._active and controller is not None:
                    self._read_controller()
                    pose = self._current_pose()
                    with self._servo_lock:
                        controller.step(pose, self._grip_value())
                        controller.send()
                    with self._lock:
                        c = controller.get_state() if self._controller is controller else {}
                        if c:
                            self._target_joints_deg = c["target_joints_deg"]
                            self._current_joints_deg = c["current_joints_deg"]
                            self._tracking = c["tracking"]
                    time.sleep(interval)
                else:
                    self._read_controller()
                    time.sleep(idle_interval)
            except Exception as e:
                log.exception("[Teleop] Control loop error: %s", e)
                time.sleep(0.5)

    def _read_controller(self) -> None:
        backend = self._vr_backend
        if backend is None:
            return
        side = self._arm_controller
        try:
            pose = backend.get_controller_pose(side)
            if pose is not None and len(pose) >= 7:
                with self._lock:
                    self._ctrl_pos = [float(v) for v in pose[:3]]
                    self._ctrl_quat = [float(v) for v in pose[3:7]]

            trigger = backend.get_trigger(side)
            grip = backend.get_grip(side)
            with self._lock:
                self._trigger_val = float(trigger) if trigger is not None else 0.0
                self._grip_val = float(grip) if grip is not None else 0.0
                self._grip_pressed = self._grip_val > GRIP_UI_THRESHOLD
                self._button_a = bool(backend.get_button(side, "primaryButton"))
                self._button_b = bool(backend.get_button(side, "secondaryButton"))
        except Exception as e:
            log.debug("[Teleop] Controller read error: %s", e)

    def _current_pose(self) -> Optional[List[float]]:
        with self._lock:
            if not self._ctrl_pos or not self._ctrl_quat:
                return None
            return list(self._ctrl_pos) + list(self._ctrl_quat)

    def _grip_value(self) -> float:
        return self._grip_val

    def _disable_controller(self) -> None:
        controller = self._controller
        self._controller = None
        if controller is not None:
            with self._servo_lock:
                try:
                    controller.stop()
                except Exception as e:
                    log.warning("[Teleop] Controller stop failed: %s", e)
        arm = self._get_arm()
        if arm is not None:
            try:
                arm.stop_motion()
            except Exception:
                pass

    def _gating(self, active: bool) -> None:
        arm = self._get_arm()
        if arm is not None:
            arm._teleop_active = active

    def _get_arm(self):
        if self.robot is None:
            return None
        return self.robot.get("arm")

    # ── display helpers ────────────────────────────────────────────────────

    def _controller_pose_to_world(self) -> List[float]:
        with self._lock:
            return list(self._ctrl_pos) + list(self._ctrl_quat)

    def _pose_from_joints(self, joints_deg: List[float]) -> List[float]:
        if not joints_deg or len(joints_deg) != 6:
            return [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        try:
            from teleoperation_sdk.ur5_ik import ur5_fk
            pose = ur5_fk([math.radians(float(a)) for a in joints_deg])
            xyz = [pose[0][3], pose[1][3], pose[2][3]]
            rot = [
                [pose[0][0], pose[0][1], pose[0][2]],
                [pose[1][0], pose[1][1], pose[1][2]],
                [pose[2][0], pose[2][1], pose[2][2]],
            ]
            rpy = self._mat_to_rpy_deg(rot)
            return xyz + rpy
        except Exception:
            return [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

    @staticmethod
    def _quat_to_ypr_deg(quat: List[float]) -> List[float]:
        if len(quat) != 4:
            return [0.0, 0.0, 0.0]
        qx, qy, qz, qw = quat
        yaw = math.degrees(math.atan2(2.0 * (qw * qz + qx * qy), 1.0 - 2.0 * (qy * qy + qz * qz)))
        pitch = math.degrees(math.asin(max(-1.0, min(1.0, 2.0 * (qw * qy - qz * qx)))))
        roll = math.degrees(math.atan2(2.0 * (qw * qx + qy * qz), 1.0 - 2.0 * (qx * qx + qy * qy)))
        return [roll, pitch, yaw]

    @staticmethod
    def _mat_to_rpy_deg(rot: List[List[float]]) -> List[float]:
        r00, r01, r02 = rot[0]
        r10, r11, r12 = rot[1]
        r20, r21, r22 = rot[2]
        pitch = math.degrees(math.atan2(-r20, math.sqrt(r00 * r00 + r10 * r10)))
        roll = math.degrees(math.atan2(r21, r22))
        yaw = math.degrees(math.atan2(r10, r00))
        return [roll, pitch, yaw]
