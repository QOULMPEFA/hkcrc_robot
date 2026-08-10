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
    quaternion_inverse,
    quaternion_multiply,
)

log = logging.getLogger(__name__)

CONTROL_HZ = 60.0
POSE_POLL_HZ = 30.0
GRIP_UI_THRESHOLD = 0.5
TRIGGER_DEADZONE = 0.05

HEAD_TRACK_HZ = 20.0
HEAD_GIMBAL_DEADBAND = 0.3
HEAD_EMA_ALPHA = 0.25
HEAD_RATE_LIMIT = 300.0
HEAD_DEADZONE = 3.0
HEAD_PRECISION = 0.5


def _quat_to_yaw_pitch(qx: float, qy: float, qz: float, qw: float) -> tuple:
    """Convert a quaternion (x,y,z,w) to head yaw/pitch in degrees."""
    fwd_x = 2.0 * (qx * qz + qw * qy)
    fwd_y = 2.0 * (qy * qz - qw * qx)
    fwd_z = 1.0 - 2.0 * (qx * qx + qy * qy)
    yaw = math.degrees(math.atan2(fwd_x, fwd_z))
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, fwd_y))))
    return yaw, pitch


class TeleopModule(RobotModule):
    """RobotModule wrapper around XrUr5eController."""

    def __init__(self, config: Optional[Dict[str, Any]] = None, robot=None):
        cfg = config or {}
        self.robot = robot
        self._arm_controller = str(cfg.get("arm_controller", "left"))
        self._vr_backend_name = str(cfg.get("vr_backend", "xrt"))
        self._vr_backend_kwargs = dict(cfg.get("vr_backend_kwargs", {}) or {})
        self._controller_config = dict(cfg)
        self._rotation_reference = str(
            cfg.get("rotation_reference", "controller"))
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
        self._calib_head_rot = [0.0, 0.0, 0.0, 1.0]
        self._trigger_val = 0.0
        self._grip_val = 0.0
        self._grip_pressed = False
        self._button_a = False
        self._button_b = False

        self._target_joints_deg = [0.0] * 6
        self._current_joints_deg = [0.0] * 6
        self._data_log_enabled = False
        self._last_collision_check = 0.0
        self._last_xr_data_time = 0.0
        self._teleop_max_velocity_base = 0.5
        self._teleop_max_acceleration_base = 1.0
        self._hand_follow_enabled = bool(cfg.get("hand_follow_enabled", True))
        self._hand_grip_speed_pct = float(cfg.get("hand_grip_speed_pct", 30.0))
        self._hand_grip_max_position = float(cfg.get("hand_grip_max_position", 0.85))
        self._hand_grip_max_force_n = float(cfg.get("hand_grip_max_force_n", 5.0))
        self._last_hand_cmd_time = 0.0
        self._last_hand_closure: Optional[float] = None

        # Head tracking -> gimbal
        self._head_to_gimbal_enabled = cfg.get("head_to_gimbal", False)
        self._head_yaw_sign = float(cfg.get("head_yaw_sign", -1.0))
        self._head_pitch_sign = float(cfg.get("head_pitch_sign", 1.0))
        self._head_smoothing = float(cfg.get("head_smoothing", HEAD_EMA_ALPHA))
        self._head_deadband = float(cfg.get("head_deadband", HEAD_GIMBAL_DEADBAND))
        self._head_rate_limit = float(cfg.get("head_rate_limit", HEAD_RATE_LIMIT))
        self._head_deadzone = float(cfg.get("head_deadzone", HEAD_DEADZONE))
        self._head_precision = float(cfg.get("head_precision", HEAD_PRECISION))
        self._head_yaw_gain = float(cfg.get("head_yaw_gain", 1.0))
        self._head_pitch_gain = float(cfg.get("head_pitch_gain", 1.0))
        self._gimbal_yaw_limits_override = cfg.get("gimbal_yaw_limits", None)
        self._gimbal_pitch_limits_override = cfg.get("gimbal_pitch_limits", None)
        self._gimbal_tracking = False
        self._gimbal_calibrated = False
        self._gimbal_calib_yaw = 0.0
        self._gimbal_calib_pitch = 0.0
        self._head_yaw = 0.0
        self._head_pitch = 0.0
        self._ema_yaw = 0.0
        self._ema_pitch = 0.0
        self._ema_initialized = False
        self._gimbal_target_yaw = 0.0
        self._gimbal_target_pitch = 0.0
        self._head_dz_yaw = 0.0
        self._head_dz_pitch = 0.0
        self._head_pose_available = False
        self._last_head_track = 0.0

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
            "rotation_reference": "controller",
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
            display_pose = self._display_pose_locked()
            return {
                "connected": self._connected or bool(controller_state.get("connected")),
                "xr_initialized": self._xr_initialized,
                "active": self._active,
                "tracking": bool(controller_state.get("tracking", self._tracking)),
                "calibrated": self._calibrated,
                "controller_position": display_pose[:3],
                "controller_pose": display_pose,
                "controller_pose_3d": display_pose,
                "controller_pose_ur": display_pose,
                "translation_mapping": "natural",
                "rotation_reference": self._rotation_reference,
                "controller_ypr_deg": self._quat_to_ypr_deg(display_pose[3:7]),
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
                "gimbal_tracking": self._gimbal_tracking,
                "gimbal_calibrated": self._gimbal_calibrated,
                "head_yaw_deg": round(self._head_yaw, 1),
                "head_pitch_deg": round(self._head_pitch, 1),
                "head_pose_available": self._head_pose_available,
                "head_yaw_gain": self._head_yaw_gain,
                "head_pitch_gain": self._head_pitch_gain,
                "head_deadband": self._head_deadband,
                "head_smoothing": self._head_smoothing,
                "head_rate_limit": self._head_rate_limit,
                "head_deadzone": self._head_deadzone,
                "head_precision": self._head_precision,
                "gimbal_yaw_limits": list(self._effective_yaw_limits()),
                "gimbal_pitch_limits": list(self._effective_pitch_limits()),
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
            display_pose = self._display_pose_locked()
            state = {
                "controller_position": display_pose[:3],
                "controller_pose": display_pose,
                "controller_pose_3d": display_pose,
                "controller_pose_ur": display_pose,
                "translation_mapping": "natural",
                "rotation_reference": self._rotation_reference,
                "trigger_value": self._trigger_val,
                "grip_pressed": self._grip_pressed,
                "button_a": self._button_a,
                "button_b": self._button_b,
                "controller_ypr_deg": self._quat_to_ypr_deg(display_pose[3:7]),
                "target_joints_deg": controller_state.get(
                    "target_joints_deg", self._target_joints_deg),
                "current_joints_deg": controller_state.get(
                    "current_joints_deg", self._current_joints_deg),
                "gimbal_available": self.robot is not None and self.robot.is_loaded("gimbal"),
                "gimbal_tracking": self._gimbal_tracking,
                "gimbal_calibrated": self._gimbal_calibrated,
                "head_yaw_deg": round(self._head_yaw, 1),
                "head_pitch_deg": round(self._head_pitch, 1),
                "head_pose_available": self._head_pose_available,
                "head_yaw_gain": self._head_yaw_gain,
                "head_pitch_gain": self._head_pitch_gain,
                "head_deadband": self._head_deadband,
                "head_smoothing": self._head_smoothing,
                "head_rate_limit": self._head_rate_limit,
                "head_deadzone": self._head_deadzone,
                "head_precision": self._head_precision,
                "gimbal_yaw_limits": list(self._effective_yaw_limits()),
                "gimbal_pitch_limits": list(self._effective_pitch_limits()),
            }
        return state

    def get_socketio_handlers(self) -> Dict[str, Any]:
        return {
            "teleop_enable": self._on_enable,
            "teleop_disable": self._on_disable,
            "teleop_calibrate": self._on_calibrate,
            "teleop_set_translation_mapping": self._on_set_translation_mapping,
            "teleop_set_rotation_reference": self._on_set_rotation_reference,
            "teleop_toggle_log": self._on_toggle_log,
            "teleop_gimbal_track_enable": self._on_gimbal_track_enable,
            "teleop_gimbal_track_disable": self._on_gimbal_track_disable,
            "teleop_gimbal_calibrate": self._on_gimbal_calibrate,
            "teleop_gimbal_home": self._on_gimbal_home,
            "teleop_set_head_gains": self._on_set_head_gains,
            "teleop_set_gimbal_limits": self._on_set_gimbal_limits,
            "teleop_set_head_config": self._on_set_head_config,
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

        arm = self._get_arm()
        if arm is not None:
            try:
                arm.stop_motion()
            except Exception:
                pass

        try:
            controller_config = dict(self._controller_config)
            speed_pct = float(controller_config.get("arm_speed_pct", 20.0))
            if arm is not None:
                try:
                    arm_state = arm.get_state()
                    speed_pct = float(arm_state.get("speed_pct", speed_pct))
                except Exception:
                    pass
            self._teleop_max_velocity_base = float(
                controller_config.get("max_velocity", 0.5))
            self._teleop_max_acceleration_base = float(
                controller_config.get("max_acceleration", 1.0))
            factor = max(0.01, min(1.0, speed_pct / 100.0))
            controller_config["max_velocity"] = (
                self._teleop_max_velocity_base * factor)
            controller_config["max_acceleration"] = (
                self._teleop_max_acceleration_base * factor)
            controller_config["rotation_reference"] = self._rotation_reference
            self._controller = XrUr5eController(controller_config)
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
        self._control_hand(0.0)
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
        headset_pose = self._vr_backend.get_headset_pose() if self._vr_backend else None

        with self._lock:
            self._calib_pos = [float(v) for v in pose[:3]]
            self._calib_rot = [float(v) for v in pose[3:7]]
            if headset_pose is not None and len(headset_pose) >= 7:
                self._calib_head_rot = [float(v) for v in headset_pose[3:7]]
            else:
                self._calib_head_rot = [0.0, 0.0, 0.0, 1.0]
            self._calibrated = True
            self._ctrl_pos = list(self._calib_pos)
            self._ctrl_quat = list(self._calib_rot)
        if self._controller is not None:
            try:
                self._controller.set_reference_pose(pose, headset_pose)
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

    def _on_set_rotation_reference(self, data: dict = None, emit=None) -> None:
        mode = (data or {}).get("mode", "controller")
        if mode not in ("controller", "head"):
            mode = "controller"
        self._rotation_reference = mode
        if self._controller is not None:
            self._controller.set_rotation_reference(mode)
        pose = self._controller_pose_to_world()
        if emit:
            emit("teleop_rotation_result", {
                "ok": True,
                "mode": mode,
                "controller_pose_3d": pose,
                "controller_pose_ur": pose,
            })

    def _on_toggle_log(self, _data: dict = None, emit=None) -> None:
        with self._lock:
            self._data_log_enabled = not self._data_log_enabled
        log.info("[Teleop] Data logging toggled: %s", self._data_log_enabled)

    # ── gimbal head tracking handlers ──────────────────────────────────────

    def _on_gimbal_track_enable(self, _data: dict = None, emit=None) -> None:
        if not self._xr_initialized:
            if emit:
                emit("error", {"msg": "XR SDK not initialized"})
            return
        head = self._read_head_pose()
        if head is None:
            if emit:
                emit("error", {"msg": "Head tracking not available"})
            return
        with self._lock:
            self._gimbal_tracking = True
            self._gimbal_target_yaw = float("inf")
            self._gimbal_target_pitch = float("inf")
            self._head_dz_yaw = float("inf")
            self._head_dz_pitch = float("inf")
            self._ema_initialized = False
        log.info("[Teleop] Gimbal head tracking enabled")

    def _on_gimbal_track_disable(self, _data: dict = None, emit=None) -> None:
        with self._lock:
            self._gimbal_tracking = False
        log.info("[Teleop] Gimbal head tracking disabled")

    def _on_gimbal_calibrate(self, _data: dict = None, emit=None) -> None:
        if not self._xr_initialized:
            if emit:
                emit("error", {"msg": "XR SDK not initialized"})
            return
        head = self._read_head_pose()
        if head is None:
            if emit:
                emit("error", {"msg": "No head tracking data"})
            return
        qx, qy, qz, qw = head[3], head[4], head[5], head[6]
        yaw, pitch = _quat_to_yaw_pitch(qx, qy, qz, qw)
        with self._lock:
            self._gimbal_calib_yaw = yaw
            self._gimbal_calib_pitch = pitch
            self._gimbal_calibrated = True
        log.info("[Teleop] Gimbal calibration set — head yaw=%.1f°, pitch=%.1f°", yaw, pitch)

    def _on_gimbal_home(self, _data: dict = None, emit=None) -> None:
        gimbal = self._get_gimbal()
        if gimbal is None:
            if emit:
                emit("error", {"msg": "Gimbal module not loaded"})
            return
        ok = gimbal.home()
        if emit:
            emit("gimbal_move_result", {"ok": ok})

    def _on_set_head_gains(self, data: dict = None, emit=None) -> None:
        data = data or {}
        with self._lock:
            if "yaw_gain" in data:
                self._head_yaw_gain = max(0.1, min(5.0, float(data["yaw_gain"])))
            if "pitch_gain" in data:
                self._head_pitch_gain = max(0.1, min(5.0, float(data["pitch_gain"])))
        log.info("[Teleop] Head gains set: yaw=%.2f pitch=%.2f",
                 self._head_yaw_gain, self._head_pitch_gain)
        if emit:
            emit("head_gains_updated", {
                "head_yaw_gain": self._head_yaw_gain,
                "head_pitch_gain": self._head_pitch_gain,
            })

    def _on_set_gimbal_limits(self, data: dict = None, emit=None) -> None:
        data = data or {}
        with self._lock:
            if "yaw_min" in data and "yaw_max" in data:
                self._gimbal_yaw_limits_override = [
                    float(data["yaw_min"]), float(data["yaw_max"])]
            if "pitch_min" in data and "pitch_max" in data:
                self._gimbal_pitch_limits_override = [
                    float(data["pitch_min"]), float(data["pitch_max"])]
        log.info("[Teleop] Gimbal limits set: yaw=%s pitch=%s",
                 self._gimbal_yaw_limits_override, self._gimbal_pitch_limits_override)
        if emit:
            emit("gimbal_limits_updated", {
                "gimbal_yaw_limits": (list(self._effective_yaw_limits())
                                      if self._gimbal_yaw_limits_override is None
                                      else self._gimbal_yaw_limits_override),
                "gimbal_pitch_limits": (list(self._effective_pitch_limits())
                                        if self._gimbal_pitch_limits_override is None
                                        else self._gimbal_pitch_limits_override),
            })

    def _on_set_head_config(self, data: dict = None, emit=None) -> None:
        data = data or {}
        with self._lock:
            if "deadband" in data:
                self._head_deadband = max(0.02, min(2.0, float(data["deadband"])))
            if "smoothing" in data:
                self._head_smoothing = max(0.02, min(1.0, float(data["smoothing"])))
            if "rate_limit" in data:
                self._head_rate_limit = max(5.0, min(500.0, float(data["rate_limit"])))
            if "deadzone" in data:
                self._head_deadzone = max(0.5, min(15.0, float(data["deadzone"])))
            if "precision" in data:
                self._head_precision = max(0.1, min(2.0, float(data["precision"])))
        log.info("[Teleop] Head config: dz=%.1f db=%.2f sm=%.2f rate=%.0f prec=%.1f",
                 self._head_deadzone, self._head_deadband, self._head_smoothing,
                 self._head_rate_limit, self._head_precision)
        if emit:
            emit("head_config_updated", {
                "head_deadband": self._head_deadband,
                "head_smoothing": self._head_smoothing,
                "head_rate_limit": self._head_rate_limit,
                "head_deadzone": self._head_deadzone,
                "head_precision": self._head_precision,
            })

    def _read_head_pose(self) -> Optional[List[float]]:
        if not self._xr_initialized or self._vr_backend is None:
            return None
        try:
            pose = self._vr_backend.get_headset_pose()
            if pose is not None and len(pose) >= 7:
                self._head_pose_available = True
                return [float(v) for v in pose[:7]]
        except Exception as e:
            log.debug("[Teleop] Head pose read failed: %s", e)
        return None

    def _step_head_tracking(self) -> None:
        now = time.time()
        if now - self._last_head_track < 1.0 / HEAD_TRACK_HZ:
            return
        self._last_head_track = now
        head = self._read_head_pose()
        if head is None:
            return
        qx, qy, qz, qw = head[3], head[4], head[5], head[6]
        head_yaw, head_pitch = _quat_to_yaw_pitch(qx, qy, qz, qw)
        alpha = self._head_smoothing
        with self._lock:
            self._head_yaw = head_yaw
            self._head_pitch = head_pitch
            if not self._ema_initialized:
                self._ema_yaw = head_yaw
                self._ema_pitch = head_pitch
                self._ema_initialized = True
            else:
                self._ema_yaw = alpha * head_yaw + (1.0 - alpha) * self._ema_yaw
                self._ema_pitch = alpha * head_pitch + (1.0 - alpha) * self._ema_pitch
        gimbal = self._get_gimbal()
        if gimbal is None:
            return
        if math.isfinite(self._head_dz_yaw):
            head_dyaw = abs(self._ema_yaw - self._head_dz_yaw)
            head_dpitch = abs(self._ema_pitch - self._head_dz_pitch)
            if head_dyaw < self._head_deadzone and head_dpitch < self._head_deadzone:
                return
        target_yaw, target_pitch = self._head_to_gimbal(self._ema_yaw, self._ema_pitch)
        if target_yaw is None:
            return
        if math.isfinite(self._gimbal_target_yaw):
            if (abs(target_yaw - self._gimbal_target_yaw) < self._head_deadband and
                    abs(target_pitch - self._gimbal_target_pitch) < self._head_deadband):
                return
        if math.isfinite(self._gimbal_target_yaw):
            max_step = self._head_rate_limit / HEAD_TRACK_HZ
            dyaw = target_yaw - self._gimbal_target_yaw
            dpitch = target_pitch - self._gimbal_target_pitch
            if abs(dyaw) > max_step:
                target_yaw = self._gimbal_target_yaw + math.copysign(max_step, dyaw)
            if abs(dpitch) > max_step:
                target_pitch = self._gimbal_target_pitch + math.copysign(max_step, dpitch)
        p = self._head_precision
        target_yaw = round(target_yaw / p) * p
        target_pitch = round(target_pitch / p) * p
        self._head_dz_yaw = self._ema_yaw
        self._head_dz_pitch = self._ema_pitch
        self._gimbal_target_yaw = target_yaw
        self._gimbal_target_pitch = target_pitch
        gimbal.move_to(target_yaw, target_pitch)

    def _head_to_gimbal(self, head_yaw: float, head_pitch: float) -> tuple:
        if not self._gimbal_calibrated:
            return None, None
        gimbal_yaw = (head_yaw - self._gimbal_calib_yaw) * self._head_yaw_sign * self._head_yaw_gain
        gimbal_pitch = (head_pitch - self._gimbal_calib_pitch) * self._head_pitch_sign * self._head_pitch_gain
        lo, hi = self._effective_yaw_limits()
        gimbal_yaw = max(lo, min(hi, gimbal_yaw))
        lo, hi = self._effective_pitch_limits()
        gimbal_pitch = max(lo, min(hi, gimbal_pitch))
        return gimbal_yaw, gimbal_pitch

    def _get_gimbal(self):
        if not self.robot:
            return None
        return self.robot.get("gimbal")

    def _effective_yaw_limits(self) -> tuple:
        if self._gimbal_yaw_limits_override is not None:
            return tuple(self._gimbal_yaw_limits_override)
        gimbal = self._get_gimbal()
        if gimbal is not None:
            return tuple(gimbal.config.get("yaw_limits", [-90, 90]))
        return (-90, 90)

    def _effective_pitch_limits(self) -> tuple:
        if self._gimbal_pitch_limits_override is not None:
            return tuple(self._gimbal_pitch_limits_override)
        gimbal = self._get_gimbal()
        if gimbal is not None:
            return tuple(gimbal.config.get("pitch_limits", [-50, 60]))
        return (-50, 60)

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
                if self._gimbal_tracking:
                    self._step_head_tracking()
                if self._active and controller is not None:
                    if (self._last_xr_data_time > 0 and
                            time.time() - self._last_xr_data_time > 2.0):
                        self._on_xr_disconnect()
                        continue
                    self._read_controller()
                    self._control_hand(self._trigger_val)
                    if time.time() - self._last_collision_check >= 0.2:
                        self._last_collision_check = time.time()
                        arm = self._get_arm()
                        if arm is not None:
                            try:
                                arm_state = arm.get_state()
                                if arm_state.get("collision_warning"):
                                    log.warning("[Teleop] Collision warning -> stop")
                                    self._disable_controller()
                                    self._gating(False)
                                    with self._lock:
                                        self._active = False
                                        self._tracking = False
                                    continue
                                speed_pct = float(
                                    arm_state.get("speed_pct", 20.0))
                                factor = max(0.01, min(1.0, speed_pct / 100.0))
                                controller.set_speed_limits(
                                    self._teleop_max_velocity_base * factor,
                                    self._teleop_max_acceleration_base * factor)
                            except Exception:
                                pass
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
                try:
                    ts = int(backend.get_timestamp_ns() or 0)
                except Exception:
                    ts = 0
                if ts > 0 or any(abs(v) > 1e-3 for v in self._ctrl_pos):
                    self._last_xr_data_time = time.time()
        except Exception as e:
            log.debug("[Teleop] Controller read error: %s", e)

    def _current_pose(self) -> Optional[List[float]]:
        with self._lock:
            if not self._ctrl_pos or not self._ctrl_quat:
                return None
            return list(self._ctrl_pos) + list(self._ctrl_quat)

    def _grip_value(self) -> float:
        return self._grip_val

    def _control_hand(self, trigger: float) -> None:
        if not self._hand_follow_enabled:
            return
        hand = self._get_hand()
        if hand is None:
            return
        if time.time() - self._last_hand_cmd_time < 0.1:
            return

        closure = 0.0
        if trigger > TRIGGER_DEADZONE:
            closure = max(
                0.0,
                min(self._hand_grip_max_position,
                    float(trigger) * self._hand_grip_max_position),
            )

            try:
                hand_state = hand.get_state()
                force_n = float(hand_state.get("grip_force_n", 0.0) or 0.0)
                positions = hand_state.get("finger_positions", [0.0] * 6)
                if force_n > self._hand_grip_max_force_n and positions:
                    current = min(float(positions[0]), float(positions[1]))
                    closure = max(0.0, min(closure, current - 0.05))
            except Exception:
                pass

        if self._last_hand_closure is not None and \
                abs(closure - self._last_hand_closure) < 0.02:
            return

        self._last_hand_cmd_time = time.time()
        self._last_hand_closure = closure
        positions = [closure] * 5 + [0.0]
        try:
            hand.set_all_fingers(positions, self._hand_grip_speed_pct)
        except Exception as e:
            log.warning("[Teleop] Hand command failed: %s", e)

    def _get_hand(self):
        if self.robot is None:
            return None
        return self.robot.get("hand")

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

    def _on_xr_disconnect(self) -> None:
        log.warning("[Teleop] XR data stale -> stop teleop and go home")
        self._disable_controller()
        self._gating(False)
        with self._lock:
            self._active = False
            self._tracking = False
        arm = self._get_arm()
        if arm is not None:
            try:
                arm.move_joints(
                    list(arm._home_angles),
                    speed_pct=float(getattr(arm, "_speed_pct", 20.0)))
            except Exception as e:
                log.warning("[Teleop] Home move after XR disconnect failed: %s", e)

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
            return self._display_pose_locked()

    def _display_pose_locked(self) -> List[float]:
        """Return the pose shown in the Web UI, relative to calibration."""
        if not self._calibrated:
            return list(self._ctrl_pos) + list(self._ctrl_quat)
        cal = [self._calib_rot[3], self._calib_rot[0],
               self._calib_rot[1], self._calib_rot[2]]
        cur = [self._ctrl_quat[3], self._ctrl_quat[0],
               self._ctrl_quat[1], self._ctrl_quat[2]]
        world_rel = quaternion_multiply(cur, quaternion_inverse(cal))
        if self._rotation_reference == "head":
            head = [self._calib_head_rot[3], self._calib_head_rot[0],
                    self._calib_head_rot[1], self._calib_head_rot[2]]
            rel = quaternion_multiply(
                quaternion_multiply(quaternion_inverse(head), world_rel), head)
        else:
            rel = world_rel
        return [
            float(self._ctrl_pos[0] - self._calib_pos[0]),
            float(self._ctrl_pos[1] - self._calib_pos[1]),
            float(self._ctrl_pos[2] - self._calib_pos[2]),
            float(rel[1]), float(rel[2]), float(rel[3]), float(rel[0]),
        ]

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
