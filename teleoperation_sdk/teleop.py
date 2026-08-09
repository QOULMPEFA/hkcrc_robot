"""XR teleoperation module — Meta Quest 3 headset → UR5 arm + BrainCo hand + Gimbal camera.

Architecture:
  Meta Quest 3 (OpenXR App) ← WiFi → XRoboToolkit-PC-Service (C++ on Jetson)
                                          ↓
                                    xrobotoolkit_sdk (pybind)
                                          ↓
                                    TeleopModule (this file)
                                          ↓
                  ┌───────────────────┬───┴──────────────┐
                  ↓                   ↓                    ↓
          UR5 arm (movej via arm module)  BrainCo Hand (Modbus)  Gimbal Camera (CH340 UART)

Quest 3 compatibility note: The XR-Robotics PC Service SDK communicates via
OpenXR, which Quest 3 natively supports. The prebuilt PICO APK must be
replaced with a Quest 3 build of the Unity XR client. See teleoperation_sdk/
for build instructions.

Modes:
  IDLE            — teleop disabled, no tracking
  ARMED           — teleop enabled, waiting for grip press (dead-man)
  TRACKING        — trigger held, arm executes planned target joints
  GIMBAL_TRACKING — headset orientation drives gimbal camera (independent of arm)

Safety:
  - Grip button = dead-man switch (release → arm holds position)
  - Workspace boundary check before planning target joints
  - Emergency stop sends stopj(2.0) + disables tracking
  - arm.py motion commands gated when teleop active
"""

import logging
import math
import os
import socket
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

from robot_control.module_base import RobotModule

log = logging.getLogger(__name__)

# VR backend abstraction — xrobotoolkit_sdk (xrt), OculusReader (adb/USB),
# or XRProtocolReceiver (TCP) behind one interface.  Selected by config.
from teleoperation_sdk.vr_backends import VRBackend, create_vr_backend

# ── constants ─────────────────────────────────────────────────────────────────

TELEOP_HZ = 100.0                # target planning rate
SERVOJ_TIME = 0.008              # UR servoj controller period (~8ms)
SERVOJ_LOOKAHEAD = 0.1           # seconds
SERVOJ_GAIN = 300                # UR servoj gain
JOINT_VELOCITY_LIMIT = 1.5       # rad/s max per joint per servoj step
TRIGGER_DEADZONE = 0.05          # below this, arm execute button is released
ARM_SPEED_PCT = 20.0             # UR movej speed used by the XR execute button
ARM_DELTA_GAIN = 1.0             # amplify controller motion before IK
TELEOP_JOINT_RANGE_DEG = 60.0    # max +/- degrees around home per joint
HAND_FOLLOW_ENABLED = False      # do not let grip drive the dexterous hand
BASE_TILT_DEG = 45.0             # UR base installed at 45° about its Y axis
BASE_TILT_SIGN = 1.0             # flip to -1.0 if the mechanical tilt is mirrored
ARM_POLL_INTERVAL = 0.5          # how often to refresh current joints from arm module

# Default workspace safety bounds (mm, base frame)
DEFAULT_WORKSPACE_MIN = [-500.0, -500.0, 0.0]
DEFAULT_WORKSPACE_MAX = [500.0, 500.0, 850.0]

# Head tracking → gimbal
HEAD_TRACK_HZ = 20.0              # head→gimbal update rate
HEAD_GIMBAL_DEADBAND = 0.3        # degrees; default deadband (adjustable via GUI)
HEAD_EMA_ALPHA = 0.25             # EMA smoothing default (adjustable via GUI)
HEAD_RATE_LIMIT = 300.0           # deg/s; max gimbal target velocity (adjustable via GUI)
HEAD_DEADZONE = 3.0              # degrees; hysteresis dead zone (only move when head exceeds this)
HEAD_PRECISION = 0.5             # degrees; quantize target to this step (eliminate encoder hunting)

# ── video bridge (RealSense → Quest 3 JPEG/UDP) ───────────────────────────────

VIDEO_BRIDGE_SCRIPT = "/home/yu/robot_code/camera_sdk/start_realsense_quest.sh"
VIDEO_BRIDGE_LOG = "/tmp/realsense_bridge.log"
VIDEO_CONTROL_PORT = 13579        # Quest connects here to request the stream
VIDEO_UDP_PORT = 10004            # JPEG frames pushed to the Quest on this port


def _quat_to_yaw_pitch(qx: float, qy: float, qz: float, qw: float) -> tuple:
    """Convert quaternion (x,y,z,w) to (yaw_deg, pitch_deg).

    Uses forward-vector projection — robust for head tracking across
    different quaternion conventions (OpenXR, Unity, etc.).

    Yaw = horizontal angle of the forward-looking vector (head turn left/right).
    Pitch = vertical angle of the forward-looking vector (head nod up/down).
    Roll (head tilt) is intentionally ignored — it does not affect either output.
    """
    # Forward vector (0,0,1) rotated by quaternion [qx,qy,qz,qw]
    fwd_x = 2.0 * (qx * qz + qw * qy)
    fwd_y = 2.0 * (qy * qz - qw * qx)
    fwd_z = 1.0 - 2.0 * (qx * qx + qy * qy)

    yaw = math.degrees(math.atan2(fwd_x, fwd_z))
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, fwd_y))))

    return yaw, pitch


class TeleopModule(RobotModule):
    """XR teleoperation controller.

    Config keys:
        host:               UR5 host (default "192.168.56.101")
        urscript_port:      URScript port (default 30001)
        arm_controller:     "left" or "right" (default "right")
        workspace_min_xyz:  [x, y, z] mm safety lower bound
        workspace_max_xyz:  [x, y, z] mm safety upper bound
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None, robot=None):
        cfg = config or {}
        # Robot container reference — set by the module registry in the
        # integrated path, or passed directly for standalone use.
        self.robot = robot
        self._ur_host = cfg.get("host", "192.168.56.101")
        self._ur_port = int(cfg.get("urscript_port", 30001))
        self._arm_controller = cfg.get("arm_controller", "right")
        self._arm_speed_pct = float(cfg.get("arm_speed_pct", ARM_SPEED_PCT))
        self._arm_delta_gain = float(cfg.get("arm_delta_gain", ARM_DELTA_GAIN))
        self._teleop_joint_range_deg = float(
            cfg.get("teleop_joint_range_deg", TELEOP_JOINT_RANGE_DEG))
        self._hand_follow_enabled = bool(
            cfg.get("hand_follow_enabled", HAND_FOLLOW_ENABLED))
        self._base_tilt_deg = float(cfg.get("base_tilt_deg", BASE_TILT_DEG))
        self._base_tilt_sign = float(cfg.get("base_tilt_sign", BASE_TILT_SIGN))
        self._base_tilt_rad = math.radians(self._base_tilt_deg)
        self._ws_min = cfg.get("workspace_min_xyz", DEFAULT_WORKSPACE_MIN)
        self._ws_max = cfg.get("workspace_max_xyz", DEFAULT_WORKSPACE_MAX)

        # VR data source: "xrt" (WiFi/OpenXR) | "adb" (USB/OculusReader) |
        # "xr_protocol" (TCP/XRProtocolReceiver).  Backend-specific kwargs
        # (e.g. adb ip_address) can be passed via ``vr_backend_kwargs``.
        self._vr_backend_name = cfg.get("vr_backend", "xrt")
        self._vr_backend_kwargs = dict(cfg.get("vr_backend_kwargs", {}) or {})
        self._vr_backend: Optional[VRBackend] = None

        # Head tracking → gimbal config
        self._head_to_gimbal_enabled = cfg.get("head_to_gimbal", False)
        self._head_yaw_sign = cfg.get("head_yaw_sign", 1)
        self._head_pitch_sign = cfg.get("head_pitch_sign", 1)
        self._head_smoothing = cfg.get("head_smoothing", HEAD_EMA_ALPHA)
        self._head_deadband = float(cfg.get("head_deadband", HEAD_GIMBAL_DEADBAND))
        self._head_rate_limit = float(cfg.get("head_rate_limit", HEAD_RATE_LIMIT))
        self._head_deadzone = float(cfg.get("head_deadzone", HEAD_DEADZONE))
        self._head_precision = float(cfg.get("head_precision", HEAD_PRECISION))
        self._head_yaw_gain = float(cfg.get("head_yaw_gain", 1.0))
        self._head_pitch_gain = float(cfg.get("head_pitch_gain", 1.0))
        # Optional gimbal limit overrides (None = use gimbal module defaults)
        self._gimbal_yaw_limits_override = cfg.get("gimbal_yaw_limits", None)
        self._gimbal_pitch_limits_override = cfg.get("gimbal_pitch_limits", None)

        # Video bridge (RealSense → Quest 3)
        vcfg = cfg.get("video_bridge", {}) or {}
        self._video_script = vcfg.get("script", VIDEO_BRIDGE_SCRIPT)
        self._video_log = vcfg.get("log", VIDEO_BRIDGE_LOG)
        self._video_control_port = int(vcfg.get("control_port", VIDEO_CONTROL_PORT))
        self._video_udp_port = int(vcfg.get("udp_port", VIDEO_UDP_PORT))
        self._video_autostart = bool(vcfg.get("autostart", False))
        # Which camera the bridge forwards by default.  Resolved through the
        # camera module at use time, so a model name survives replug (source
        # ids don't — they're assigned by enumeration order).
        self._video_default_camera = vcfg.get("default_camera", "D455")

        # Bridge status is polled by shelling out, so it is cached between
        # state broadcasts (which run far faster than the process can change).
        self._video_lock = threading.Lock()
        self._video_action_lock = threading.Lock()
        self._video_cache = {"running": False, "pid": 0, "client": "",
                             "source": "", "error": ""}
        self._video_cache_at = 0.0

        # Runtime state (thread-safe via _lock)
        self._lock = threading.RLock()
        self._running = False
        self._active = False       # teleop enabled (ARMED or TRACKING)
        self._tracking = False     # trigger held → arm executing
        self._calibrated = False
        self._xr_initialized = False
        self._connected = False

        # Calibration offset (controller home → arm home)
        self._calib_pos = [0.0, 0.0, 0.0]
        self._calib_rot = [0.0, 0.0, 0.0, 1.0]  # quaternion x,y,z,w

        # Current controller state
        self._ctrl_pos = [0.0, 0.0, 0.0]
        self._ctrl_quat = [0.0, 0.0, 0.0, 1.0]
        self._trigger_val = 0.0
        self._grip_pressed = False
        self._button_a = False
        self._button_b = False
        self._trigger_exec_active = False
        self._last_servoj_log = 0.0
        self._last_arm_exec_time = 0.0
        self._last_executed_joints: Optional[List[float]] = None

        # Joint state
        self._current_joints_deg = [0.0] * 6
        self._target_joints_deg = [0.0] * 6
        self._last_arm_poll = 0.0

        # Head tracking → gimbal state
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
        self._head_dz_yaw = 0.0       # dead zone reference (head angle at last gimbal update)
        self._head_dz_pitch = 0.0
        self._head_pose_available = False
        self._last_head_track = 0.0

        # URScript socket
        self._ur_sock: Optional[socket.socket] = None
        self._ur_lock = threading.Lock()

        # Fault
        self._is_fault = False
        self._fault_text = ""

        # Data logging
        self._data_log_enabled = False
        self._data_log: List[Dict] = []

        # Background thread
        self._thread: Optional[threading.Thread] = None

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
            "head_deadband": HEAD_GIMBAL_DEADBAND,
            "head_smoothing": HEAD_EMA_ALPHA,
            "head_rate_limit": HEAD_RATE_LIMIT,
            "head_deadzone": HEAD_DEADZONE,
            "head_precision": HEAD_PRECISION,
            "gimbal_yaw_limits": [-90, 90],
            "gimbal_pitch_limits": [-50, 60],
            "video_running": False,
            "video_pid": 0,
            "video_client": "",
            "video_source": "",
            "video_control_port": VIDEO_CONTROL_PORT,
            "video_udp_port": VIDEO_UDP_PORT,
            "video_error": "",
        }

    def get_state(self) -> Dict[str, Any]:
        video = self._video_status()
        with self._lock:
            return {
                "connected": self._connected,
                "xr_initialized": self._xr_initialized,
                "active": self._active,
                "tracking": self._tracking,
                "calibrated": self._calibrated,
                "controller_position": list(self._ctrl_pos),
                "controller_pose": list(self._ctrl_pos) + list(self._ctrl_quat),
                "controller_ypr_deg": self._quat_to_ypr_deg(self._ctrl_quat),
                **self._pose_info(self._current_joints_deg, "current"),
                **self._pose_info(self._target_joints_deg, "target"),
                "arm_fk_current": self._fk_frames(self._current_joints_deg),
                "arm_fk_target": self._fk_frames(self._target_joints_deg),
                "trigger_value": self._trigger_val,
                "trigger_exec_active": self._trigger_exec_active,
                "grip_pressed": self._grip_pressed,
                "button_a": self._button_a,
                "button_b": self._button_b,
                "target_joints_deg": list(self._target_joints_deg),
                "current_joints_deg": list(self._current_joints_deg),
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
                "video_running": video["running"],
                "video_pid": video["pid"],
                "video_client": video["client"],
                "video_source": video.get("source", ""),
                "video_control_port": self._video_control_port,
                "video_udp_port": self._video_udp_port,
                "video_error": video["error"],
            }

    def get_socketio_handlers(self) -> Dict[str, Any]:
        return {
            "teleop_enable":                self._on_enable,
            "teleop_disable":               self._on_disable,
            "teleop_calibrate":             self._on_calibrate,
            "teleop_toggle_log":            self._on_toggle_log,
            "teleop_gimbal_track_enable":   self._on_gimbal_track_enable,
            "teleop_gimbal_track_disable":  self._on_gimbal_track_disable,
            "teleop_gimbal_calibrate":      self._on_gimbal_calibrate,
            "teleop_gimbal_home":           self._on_gimbal_home,
            "teleop_set_head_gains":        self._on_set_head_gains,
            "teleop_set_gimbal_limits":     self._on_set_gimbal_limits,
            "teleop_set_head_config":       self._on_set_head_config,
            "teleop_video_start":           self._on_video_start,
            "teleop_video_stop":            self._on_video_stop,
            "teleop_video_restart":         self._on_video_restart,
            "teleop_video_log":             self._on_video_log,
        }

    # ── lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        # The bridge is a separate process feeding the headset's video window;
        # it works even when the VR backend is absent, so start it before bailing.
        if self._video_autostart:
            self._on_video_start()

        backend = create_vr_backend(self._vr_backend_name, **self._vr_backend_kwargs)
        if backend is None:
            log.warning("[Teleop] Unknown VR backend %r — stub mode", self._vr_backend_name)
            self._connected = False
            return

        self._vr_backend = backend
        if not backend.init():
            log.warning("[Teleop] VR backend %s failed to initialize — offline",
                        self._vr_backend_name)
            self._vr_backend = None
            self._xr_initialized = False
            self._connected = False
            return

        self._xr_initialized = True
        self._connected = True
        log.info("[Teleop] VR backend %s initialized", self._vr_backend_name)

        self._running = True
        self._thread = threading.Thread(target=self._control_loop, daemon=True, name="teleop-ctrl")
        self._thread.start()
        log.info("[Teleop] Control loop started at %.0f Hz", TELEOP_HZ)

    def stop(self) -> None:
        self._running = False
        if self._active:
            self._arm_stop()
            self._gating(False)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

        self._disconnect_ur()

        if self._xr_initialized:
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
        log.info("[Teleop] Stopped")

    def emergency_stop(self) -> None:
        log.warning("[Teleop] EMERGENCY STOP")
        with self._lock:
            self._is_fault = True
            self._fault_text = "EMERGENCY STOP"
        self._arm_stop()
        self._gating(False)
        with self._lock:
            self._active = False
            self._tracking = False

    # ── SocketIO handlers ──────────────────────────────────────────────────

    def _on_enable(self, _data: dict = None, emit=None) -> None:
        if not self._xr_initialized:
            if emit:
                emit("error", {"msg": "XR SDK not initialized"})
            return
        if self._is_fault:
            if emit:
                emit("error", {"msg": "Teleop in fault state — reset first"})
            return
        if not self._calibrated:
            self._on_calibrate(emit=emit)
            if not self._calibrated:
                return
        with self._lock:
            self._active = True
            self._tracking = False
            self._trigger_exec_active = False
        self._gating(True)
        log.info("[Teleop] Enabled (ARMED)")

    def _on_disable(self, _data: dict = None, emit=None) -> None:
        self._arm_stop()
        self._gating(False)
        with self._lock:
            self._active = False
            self._tracking = False
            self._trigger_exec_active = False
        log.info("[Teleop] Disabled")

    def _on_calibrate(self, _data: dict = None, emit=None) -> None:
        """Record current controller pose as reference offset to current arm position."""
        if not self._xr_initialized:
            if emit:
                emit("error", {"msg": "XR SDK not initialized"})
            return

        # Read current controller pose
        try:
            ctrl = self._vr_backend.get_controller_pose(self._arm_controller)
        except Exception as e:
            log.warning("[Teleop] Calibrate: failed to read controller: %s", e)
            return

        if ctrl is None:
            if emit:
                emit("error", {"msg": "No controller data — is headset connected?"})
            return

        self._calib_pos = [float(ctrl[0]), float(ctrl[1]), float(ctrl[2])]
        self._calib_rot = [float(ctrl[3]), float(ctrl[4]), float(ctrl[5]), float(ctrl[6])]

        with self._lock:
            self._calibrated = True

        log.info("[Teleop] Calibrated — controller at (%0.1f, %0.1f, %0.1f)",
                 self._calib_pos[0], self._calib_pos[1], self._calib_pos[2])

    def _on_toggle_log(self, _data: dict = None, emit=None) -> None:
        with self._lock:
            self._data_log_enabled = not self._data_log_enabled
            if not self._data_log_enabled:
                n = len(self._data_log)
                self._data_log.clear()
                log.info("[Teleop] Data log stopped — %d frames discarded", n)
            else:
                log.info("[Teleop] Data log started")

    # ── gimbal head tracking handlers ──────────────────────────────────────

    def _on_gimbal_track_enable(self, _data: dict = None, emit=None) -> None:
        if not self._xr_initialized:
            if emit:
                emit("error", {"msg": "XR SDK not initialized"})
            return
        # Probe head pose availability (first successful read sets _head_pose_available)
        head = self._read_head_pose()
        if head is None:
            if emit:
                emit("error", {"msg": "Head tracking not available — SDK may need update"})
            return
        with self._lock:
            self._gimbal_tracking = True
            # Reset deadband/deadzone targets so the first step always moves the gimbal
            self._gimbal_target_yaw = float('inf')
            self._gimbal_target_pitch = float('inf')
            self._head_dz_yaw = float('inf')
            self._head_dz_pitch = float('inf')
            # Reset EMA filter to avoid stale values from previous session
            self._ema_initialized = False
        log.info("[Teleop] Gimbal head tracking enabled")

    def _on_gimbal_track_disable(self, _data: dict = None, emit=None) -> None:
        with self._lock:
            self._gimbal_tracking = False
        log.info("[Teleop] Gimbal head tracking disabled")

    def _on_gimbal_calibrate(self, _data: dict = None, emit=None) -> None:
        """Record current head orientation as the gimbal reference offset."""
        if not self._xr_initialized:
            if emit:
                emit("error", {"msg": "XR SDK not initialized"})
            return
        head = self._read_head_pose()
        if head is None:
            if emit:
                emit("error", {"msg": "No head tracking data — is headset connected?"})
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
        """Set head→gimbal gain coefficients from web GUI."""
        if data is None:
            data = {}
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
        """Override gimbal angle limits from web GUI."""
        if data is None:
            data = {}
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
        """Set head tracking noise-suppression params from web GUI."""
        if data is None:
            data = {}
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
        log.info("[Teleop] Head config: dz=%.1f° db=%.2f° sm=%.2f rate=%.0f°/s prec=%.1f°",
                 self._head_deadzone, self._head_deadband, self._head_smoothing, self._head_rate_limit)
        if emit:
            emit("head_config_updated", {
                "head_deadband": self._head_deadband,
                "head_smoothing": self._head_smoothing,
                "head_rate_limit": self._head_rate_limit,
                "head_deadzone": self._head_deadzone,
                "head_precision": self._head_precision,
            })

    # ── video bridge (RealSense → Quest 3) ─────────────────────────────────

    def _video_sh(self, action: str, arg: str = None,
                  timeout: float = 15.0) -> tuple:
        """Run the bridge control script. Returns (ok, combined_output)."""
        if not os.path.isfile(self._video_script):
            return False, f"bridge script not found: {self._video_script}"
        cmd = ["bash", self._video_script, action]
        if arg:
            cmd.append(arg)
        try:
            p = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
            )
            return p.returncode == 0, (p.stdout + p.stderr).strip()
        except subprocess.TimeoutExpired:
            return False, f"'{action}' timed out after {timeout:.0f}s"
        except Exception as e:
            return False, str(e)

    def _resolve_video_source(self, hint: str) -> str:
        """Map a camera hint (id / serial / model) to a live source id.

        Falls back to cam0 so a mis-typed hint still yields video rather than a
        404 that silently kills the bridge.
        """
        hint = (hint or "").strip() or self._video_default_camera
        cam = None
        try:
            cam = getattr(self.robot, "modules", {}).get("camera")
        except Exception:
            cam = None
        if cam is None:
            log.warning("[Teleop] camera module unavailable — video source "
                        "hint %r used verbatim", hint)
            return hint
        try:
            sid = cam.resolve_source(hint)
        except Exception as e:
            log.warning("[Teleop] resolve_source(%r) failed: %s", hint, e)
            sid = None
        if not sid:
            log.warning("[Teleop] no camera matches %r — falling back to cam0", hint)
            return "cam0"
        return sid

    def _video_status(self, force: bool = False) -> Dict[str, Any]:
        """Cached bridge status. Shelling out per state broadcast would be far
        too costly, and the process state cannot change that fast anyway."""
        now = time.time()
        with self._video_lock:
            if not force and (now - self._video_cache_at) < 2.0:
                return dict(self._video_cache)

        ok, out = self._video_sh("status", timeout=8.0)
        lines = [ln.strip() for ln in out.strip().split("\n") if ln.strip()]
        source = ""
        rest = []
        for ln in lines:
            if ln.lower().startswith("source:"):
                source = ln.split(":", 1)[1].strip()
            else:
                rest.append(ln)
        running = ok and bool(rest) and "not running" not in " ".join(rest).lower()
        pid = 0
        if running:
            head = rest[0].split()
            if head and head[0].isdigit():
                pid = int(head[0])

        state = {"running": running, "pid": pid,
                 "client": self._video_last_client(), "source": source,
                 "error": "" if ok else out}
        with self._video_lock:
            self._video_cache = state
            self._video_cache_at = now
        return dict(state)

    def _video_last_client(self) -> str:
        """Most recent Quest IP seen by the bridge, parsed from its log."""
        try:
            with open(self._video_log, "r", errors="replace") as f:
                lines = f.readlines()[-60:]
        except OSError:
            return ""
        for line in reversed(lines):
            if "Quest" in line and "connected" in line.lower():
                for tok in line.replace(",", " ").split():
                    if tok.count(".") == 3 and tok.replace(".", "").isdigit():
                        return tok
        return ""

    def _on_video_start(self, data: dict = None, emit=None) -> None:
        """Ensure the bridge is streaming the requested camera.

        Idempotent by design: if the bridge is already up, the source is
        switched in place rather than restarting, so the headset keeps its TCP
        session and only sees a brief frame gap instead of having to
        re-initiate the stream from inside the HMD.
        """
        hint = (data or {}).get("source") or self._video_default_camera
        src = self._resolve_video_source(hint)

        with self._video_action_lock:
            was_running = self._video_status(force=True)["running"]
            if was_running:
                ok, out = self._video_sh("source", src)
                action = "source"
            else:
                ok, out = self._video_sh("start", src)
                action = "start"
            st = self._video_status(force=True)

        if not st["running"]:
            # The script backgrounds the process and exits 0 regardless, so a
            # crashed bridge only shows up in its log.
            detail = self._video_log_tail(12) or out
            log.error("[Teleop] Video bridge failed to start: %s", detail)
            if emit:
                emit("error", {"msg": f"图传启动失败: {detail}"})
            return
        log.info("[Teleop] Video bridge %s → source=%s (pid=%s, ctrl=%d, udp=%d)",
                 action, st["source"] or src, st["pid"],
                 self._video_control_port, self._video_udp_port)
        if emit:
            emit("teleop_video_result", {"action": action, "ok": ok,
                                         "source": st["source"] or src,
                                         "msg": out})

    def _on_video_stop(self, _data: dict = None, emit=None) -> None:
        with self._video_action_lock:
            ok, out = self._video_sh("stop")
            self._video_status(force=True)
        log.info("[Teleop] Video bridge stopped")
        if emit:
            emit("teleop_video_result", {"action": "stop", "ok": ok, "msg": out})

    def _on_video_restart(self, _data: dict = None, emit=None) -> None:
        with self._video_action_lock:
            self._video_sh("stop")
            self._video_status(force=True)
        # Reuse the source already on disk rather than re-resolving, so a
        # restart never silently moves the feed to a different camera.
        self._on_video_start({"source": self._video_status(force=True)["source"]},
                             emit)

    def _on_video_log(self, _data: dict = None, emit=None) -> None:
        if emit:
            emit("teleop_video_log", {"text": self._video_log_tail(40)})

    def _video_log_tail(self, n: int = 40) -> str:
        try:
            with open(self._video_log, "r", errors="replace") as f:
                return "".join(f.readlines()[-n:]).strip()
        except OSError as e:
            return f"(无法读取 {self._video_log}: {e})"

    # ── head tracking ──────────────────────────────────────────────────────

    def _read_head_pose(self) -> Optional[List[float]]:
        """Read Quest 3 headset pose. Returns [x, y, z, qx, qy, qz, qw] or None."""
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
        """One iteration of head→gimbal tracking at HEAD_TRACK_HZ."""
        now = time.time()
        if now - self._last_head_track < 1.0 / HEAD_TRACK_HZ:
            return
        self._last_head_track = now

        # Read head pose and update display values regardless of gimbal hardware
        head = self._read_head_pose()
        if head is None:
            return

        qx, qy, qz, qw = head[3], head[4], head[5], head[6]
        head_yaw, head_pitch = _quat_to_yaw_pitch(qx, qy, qz, qw)

        # EMA smoothing — suppress jitter from Quest IMU noise
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

        # Gimbal hardware is optional — only gate the move_to call
        gimbal = self._get_gimbal()
        if gimbal is None:
            return

        # Hysteresis dead zone: only move gimbal when head has moved enough
        # from the last position where gimbal was updated.
        # Skip on first step after enable (dz ref reset to inf).
        if math.isfinite(self._head_dz_yaw):
            head_dyaw = abs(self._ema_yaw - self._head_dz_yaw)
            head_dpitch = abs(self._ema_pitch - self._head_dz_pitch)
            if head_dyaw < self._head_deadzone and head_dpitch < self._head_deadzone:
                return  # head hasn't moved enough — gimbal stays frozen

        target_yaw, target_pitch = self._head_to_gimbal(self._ema_yaw, self._ema_pitch)
        if target_yaw is None:
            return

        # Deadband: skip if change is below threshold (suppresses IMU jitter)
        if math.isfinite(self._gimbal_target_yaw):
            if (abs(target_yaw - self._gimbal_target_yaw) < self._head_deadband and
                    abs(target_pitch - self._gimbal_target_pitch) < self._head_deadband):
                return

        # Rate limit: cap velocity to suppress rapid fluctuations
        if math.isfinite(self._gimbal_target_yaw):
            max_step = self._head_rate_limit / HEAD_TRACK_HZ
            dyaw = target_yaw - self._gimbal_target_yaw
            dpitch = target_pitch - self._gimbal_target_pitch
            if abs(dyaw) > max_step:
                target_yaw = self._gimbal_target_yaw + math.copysign(max_step, dyaw)
            if abs(dpitch) > max_step:
                target_pitch = self._gimbal_target_pitch + math.copysign(max_step, dpitch)

        # Quantize: round to servo's practical resolution to prevent encoder hunting
        p = self._head_precision
        target_yaw = round(target_yaw / p) * p
        target_pitch = round(target_pitch / p) * p

        # Update gimbal AND reset dead zone reference to current head angle
        self._head_dz_yaw = self._ema_yaw
        self._head_dz_pitch = self._ema_pitch
        self._gimbal_target_yaw = target_yaw
        self._gimbal_target_pitch = target_pitch
        gimbal.move_to(target_yaw, target_pitch)

    def _head_to_gimbal(self, head_yaw: float, head_pitch: float) -> tuple:
        """Map head orientation to gimbal target angles with calibration offset.

        Returns (yaw, pitch) or (None, None) if uncalibrated.
        """
        if not self._gimbal_calibrated:
            return None, None

        # Apply calibration offset + direction sign + gain coefficient
        gimbal_yaw = (head_yaw - self._gimbal_calib_yaw) * self._head_yaw_sign * self._head_yaw_gain
        gimbal_pitch = (head_pitch - self._gimbal_calib_pitch) * self._head_pitch_sign * self._head_pitch_gain

        # Clamp to effective gimbal limits (override → module config → defaults)
        lo, hi = self._effective_yaw_limits()
        gimbal_yaw = max(lo, min(hi, gimbal_yaw))
        lo, hi = self._effective_pitch_limits()
        gimbal_pitch = max(lo, min(hi, gimbal_pitch))

        return gimbal_yaw, gimbal_pitch

    def _get_gimbal(self):
        """Get gimbal module reference."""
        if not self.robot:
            return None
        return self.robot.get("gimbal")

    def _effective_yaw_limits(self) -> tuple:
        """Return (min, max) yaw limits: override > gimbal module > default."""
        if self._gimbal_yaw_limits_override is not None:
            return tuple(self._gimbal_yaw_limits_override)
        gimbal = self._get_gimbal()
        if gimbal is not None:
            cfg = getattr(gimbal, 'config', {})
            return tuple(cfg.get("yaw_limits", [-90, 90]))
        return (-90, 90)

    def _effective_pitch_limits(self) -> tuple:
        """Return (min, max) pitch limits: override > gimbal module > default."""
        if self._gimbal_pitch_limits_override is not None:
            return tuple(self._gimbal_pitch_limits_override)
        gimbal = self._get_gimbal()
        if gimbal is not None:
            cfg = getattr(gimbal, 'config', {})
            return tuple(cfg.get("pitch_limits", [-50, 60]))
        return (-50, 60)

    # ── control loop ───────────────────────────────────────────────────────

    def _control_loop(self) -> None:
        interval = 1.0 / TELEOP_HZ
        idle_interval = 0.2  # 5 Hz when idle

        while self._running:
            try:
                active = self._active
                gimbal_tracking = self._gimbal_tracking

                if not active and not gimbal_tracking:
                    time.sleep(idle_interval)
                    continue

                # Read XR controller data (needed for both arm and gimbal modes)
                if self._xr_initialized:
                    self._read_controller()

                # ── Arm target planning + trigger execution (100 Hz) ──
                if active:
                    if self._calibrated:
                        self._step_tracking()
                    self._tracking = self._trigger_exec_active if self._xr_initialized else False

                # ── Gimbal head tracking (5 Hz, independent of arm) ──
                if gimbal_tracking:
                    self._step_head_tracking()

                time.sleep(interval)

            except Exception as e:
                log.error("[Teleop] Control loop error: %s", e)
                time.sleep(0.5)

    def _read_controller(self) -> None:
        """Read XR controller state into cached fields."""
        backend = self._vr_backend
        if backend is None:
            return
        side = self._arm_controller
        try:
            pose = backend.get_controller_pose(side)
            if pose is not None and len(pose) >= 7:
                with self._lock:
                    self._ctrl_pos = [float(pose[0]), float(pose[1]), float(pose[2])]
                    self._ctrl_quat = [float(pose[3]), float(pose[4]),
                                       float(pose[5]), float(pose[6])]

            trigger = backend.get_trigger(side)
            with self._lock:
                self._trigger_val = float(trigger) if trigger is not None else 0.0

            grip = backend.get_grip(side)
            with self._lock:
                # Quest grip is an analog 0.0–1.0 value — bool() would treat any
                # small positive value as pressed, falsely arming the dead-man
                # switch. Threshold at 50%; XRT returns 0/1 so this is a no-op.
                self._grip_pressed = float(grip) > 0.5

            a_btn = backend.get_button(side, "primaryButton")
            b_btn = backend.get_button(side, "secondaryButton")
            with self._lock:
                self._button_a = bool(a_btn)
                self._button_b = bool(b_btn)

        except Exception as e:
            log.warning("[Teleop] Controller read error: %s", e)

    def _step_tracking(self) -> None:
        """Plan target joints and execute them through the arm module on trigger."""
        from teleoperation_sdk.ur5_ik import ur5_ik

        # Refresh current joint state from arm module
        self._refresh_joints()

        # Compute target pose from controller + calibration
        # For now: use current arm joints as seed, map controller delta to Cartesian delta
        ctrl_pos = list(self._ctrl_pos)
        calib_pos = self._calib_pos

        # Controller delta from calibration position (in metres from XR SDK).
        # The XR axes are already mapped as body x/z/y -> UR x/y/z; the UR
        # base is then rotated +45° about its Y axis because the arm is mounted
        # on the robot's left shoulder. Map the operator delta into that frame.
        body_dx = (ctrl_pos[0] - calib_pos[0]) * 1000.0  # m → mm
        body_dy = (ctrl_pos[1] - calib_pos[1]) * 1000.0
        body_dz = (ctrl_pos[2] - calib_pos[2]) * 1000.0
        body_dx *= self._arm_delta_gain
        body_dy *= self._arm_delta_gain
        body_dz *= self._arm_delta_gain
        dx, dy, dz = self._map_base_tilt_delta(
            body_dx, body_dy, body_dz,
            self._base_tilt_rad, self._base_tilt_sign,
        )

        # Calibration reference is the arm home pose. Target = home pose +
        # controller delta, so calibration and home share the same frame.
        home_joints = self._get_home_joints()
        home_joints_rad = [math.radians(a) for a in home_joints]
        home_pose = self._get_current_fk(home_joints_rad)

        if home_pose is None:
            self._handle_trigger_execution()
            return

        # Target = home position + base-tilt-adjusted controller delta.
        # dx/dy/dz are already expressed in UR base coordinates.
        tx = home_pose[0][3] + dx
        ty = home_pose[1][3] + dy
        tz = home_pose[2][3] + dz

        # Workspace bounds check
        if not (self._ws_min[0] <= tx <= self._ws_max[0] and
                self._ws_min[1] <= ty <= self._ws_max[1] and
                self._ws_min[2] <= tz <= self._ws_max[2]):
            log.debug("[Teleop] Target outside workspace: (%.0f, %.0f, %.0f)", tx, ty, tz)
            self._handle_trigger_execution()
            return

        # Controller orientation relative to calibration, mapped to UR and
        # rotated by the 45° base tilt, then applied on top of home rotation.
        home_rot = [
            [home_pose[0][0], home_pose[0][1], home_pose[0][2]],
            [home_pose[1][0], home_pose[1][1], home_pose[1][2]],
            [home_pose[2][0], home_pose[2][1], home_pose[2][2]],
        ]
        ctrl_mat = self._quat_to_mat(*self._ctrl_quat)
        calib_mat = self._quat_to_mat(*self._calib_rot)
        rel_rot = self._mat3_mul(self._mat3_transpose(calib_mat), ctrl_mat)
        axis_map = [[1.0, 0.0, 0.0],
                    [0.0, 0.0, 1.0],
                    [0.0, 1.0, 0.0]]
        mapped_rot = self._mat3_mul(
            axis_map, self._mat3_mul(rel_rot, self._mat3_transpose(axis_map)))
        c = math.cos(self._base_tilt_rad)
        s = self._base_tilt_sign * math.sin(self._base_tilt_rad)
        tilt_rot = [[c, 0.0, s],
                    [0.0, 1.0, 0.0],
                    [-s, 0.0, c]]
        tilted_rot = self._mat3_mul(
            tilt_rot, self._mat3_mul(mapped_rot, self._mat3_transpose(tilt_rot)))
        target_rot = self._mat3_mul(home_rot, tilted_rot)

        target_pose = [
            [target_rot[0][0], target_rot[0][1], target_rot[0][2], tx],
            [target_rot[1][0], target_rot[1][1], target_rot[1][2], ty],
            [target_rot[2][0], target_rot[2][1], target_rot[2][2], tz],
            [0.0, 0.0, 0.0, 1.0],
        ]

        # IK from current joints (warm start → fast convergence)
        current_joints_rad = [math.radians(a) for a in self._current_joints_deg]
        sol = ur5_ik(target_pose, seed_joints_rad=current_joints_rad)
        if sol is None:
            self._handle_trigger_execution()
            return

        target_deg = [math.degrees(a) for a in sol]
        target_deg = self._clamp_teleop_target(target_deg, home_joints)
        with self._lock:
            self._target_joints_deg = target_deg

        # Trigger is the XR equivalent of the arm execute button: a press
        # executes the current planned target, release stops the arm.
        self._handle_trigger_execution(target_deg)

        if self._hand_follow_enabled:
            self._control_hand(1.0 if self._grip_pressed else 0.0)

        # Data logging
        if self._data_log_enabled:
            self._data_log.append({
                "t": time.time(),
                "ctrl_pos": list(self._ctrl_pos),
                "target_joints_deg": list(target_deg),
                "current_joints_deg": list(self._current_joints_deg),
                "trigger": self._trigger_val,
            })
            if len(self._data_log) > 10000:
                self._data_log = self._data_log[-5000:]

    def _handle_trigger_execution(self, target_deg: Optional[List[float]] = None) -> None:
        """Continuously reissue movej while trigger is held, stop on release."""
        trigger = self._trigger_val
        if trigger > TRIGGER_DEADZONE:
            if not self._trigger_exec_active and target_deg is not None:
                self._trigger_exec_active = True
                log.info("[Teleop] Trigger execute -> arm joints %s",
                         [f"{a:.1f}" for a in target_deg])
                self._last_arm_exec_time = time.time()
                self._last_executed_joints = list(target_deg)
                self._execute_target_joints(target_deg)
            elif self._trigger_exec_active and target_deg is not None:
                now = time.time()
                changed = self._last_executed_joints is None or any(
                    abs(a - b) > 1.0 for a, b in
                    zip(target_deg, self._last_executed_joints))
                if changed and now - self._last_arm_exec_time >= 0.2:
                    self._last_arm_exec_time = now
                    self._last_executed_joints = list(target_deg)
                    self._execute_target_joints(target_deg)
        elif self._trigger_exec_active:
            self._trigger_exec_active = False
            self._last_executed_joints = None
            self._arm_stop()

    @staticmethod
    def _map_base_tilt_delta(dx: float, dy: float, dz: float,
                             tilt_rad: float, sign: float = 1.0) -> tuple:
        """Rotate operator deltas into the tilted UR base frame.

        The UR base is installed with a 45° counter-clockwise rotation about
        its Y axis on the robot's left shoulder. The current XR mapping is
        (x,z,y) -> UR (x,y,z), so the Y component stays aligned while the
        X/Z plane is rotated by the base tilt.
        """
        s = sign * math.sin(tilt_rad)
        c = math.cos(tilt_rad)
        return (c * dx + s * dz, dz, -s * dx + c * dy)

    @staticmethod
    def _quat_to_mat(qx: float, qy: float, qz: float, qw: float) -> List[List[float]]:
        """Convert quaternion (x,y,z,w) to a 3x3 rotation matrix."""
        n = qx * qx + qy * qy + qz * qz + qw * qw
        s = 2.0 / n if n > 0 else 0.0
        xx, xy, xz = qx * qx * s, qx * qy * s, qx * qz * s
        yy, yz, zz = qy * qy * s, qy * qz * s, qz * qz * s
        wx, wy, wz = qw * qx * s, qw * qy * s, qw * qz * s
        return [
            [1.0 - (yy + zz), xy - wz, xz + wy],
            [xy + wz, 1.0 - (xx + zz), yz - wx],
            [xz - wy, yz + wx, 1.0 - (xx + yy)],
        ]

    @staticmethod
    def _mat3_mul(a: List[List[float]],
                  b: List[List[float]]) -> List[List[float]]:
        return [
            [sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)
        ]

    @staticmethod
    def _mat3_transpose(a: List[List[float]]) -> List[List[float]]:
        return [[a[j][i] for j in range(3)] for i in range(3)]

    @classmethod
    def _quat_to_ypr_deg(cls, quat: List[float]) -> List[float]:
        """Return yaw/pitch/roll in degrees from a quaternion."""
        if not quat or len(quat) < 4:
            return [0.0, 0.0, 0.0]
        rot = cls._quat_to_mat(*quat[:4])
        return cls._mat_to_rpy_deg(rot)

    @staticmethod
    def _mat_to_rpy_deg(rot: List[List[float]]) -> List[float]:
        """Convert a 3x3 rotation matrix to ZYX yaw/pitch/roll in degrees."""
        sy = math.sqrt(max(0.0, rot[0][0] ** 2 + rot[1][0] ** 2))
        if sy > 1e-9:
            yaw = math.atan2(rot[1][0], rot[0][0])
            pitch = math.atan2(-rot[2][0], sy)
            roll = math.atan2(rot[2][1], rot[2][2])
        else:
            yaw = math.atan2(-rot[0][1], rot[1][1])
            pitch = math.atan2(-rot[2][0], sy)
            roll = 0.0
        return [math.degrees(yaw), math.degrees(pitch), math.degrees(roll)]

    @staticmethod
    def _pose_from_mat(pose_4x4: List[List[float]]) -> tuple:
        xyz = [pose_4x4[0][3], pose_4x4[1][3], pose_4x4[2][3]]
        rot = [[pose_4x4[i][j] for j in range(3)] for i in range(3)]
        return xyz, TeleopModule._mat_to_rpy_deg(rot)

    def _pose_info(self, joints_deg: List[float], prefix: str) -> dict:
        if not joints_deg or len(joints_deg) != 6:
            return {
                f"{prefix}_pose_xyz": [0.0, 0.0, 0.0],
                f"{prefix}_pose_rpy_deg": [0.0, 0.0, 0.0],
            }
        from teleoperation_sdk.ur5_ik import ur5_fk
        pose = ur5_fk([math.radians(a) for a in joints_deg])
        xyz, rpy = self._pose_from_mat(pose)
        return {
            f"{prefix}_pose_xyz": [round(v, 2) for v in xyz],
            f"{prefix}_pose_rpy_deg": [round(v, 1) for v in rpy],
        }

    @staticmethod
    def _fk_frames(joints_deg: List[float]) -> list:
        if not joints_deg or len(joints_deg) != 6:
            return []
        from teleoperation_sdk.ur5_ik import ur5_fk_frames
        frames = ur5_fk_frames([math.radians(a) for a in joints_deg])
        return [[[round(v, 3) for v in row] for row in mat] for mat in frames]

    def _execute_target_joints(self, target_deg: List[float]) -> None:
        """Send the planned joint target to the arm module for execution."""
        if self.robot:
            arm = self.robot.get("arm")
            if arm is not None:
                try:
                    arm.teleop_move_joints(target_deg, speed_pct=self._arm_speed_pct)
                    return
                except Exception as e:
                    log.warning("[Teleop] Trigger execute via arm failed: %s", e)

        factor = max(0.01, min(1.0, self._arm_speed_pct / 100.0))
        q_rad = [math.radians(a) for a in target_deg]
        cmd = "movej([{:.6f}, {:.6f}, {:.6f}, {:.6f}, {:.6f}, {:.6f}], "
        cmd += "a={:.3f}, v={:.3f})\n"
        cmd = cmd.format(*q_rad, factor * 2.5, factor * 2.0)
        self._send_urscript(cmd)

    def _arm_stop(self) -> None:
        """Stop the arm through the arm module, falling back to raw URScript."""
        if self.robot:
            arm = self.robot.get("arm")
            if arm is not None:
                try:
                    arm.stop_motion()
                    return
                except Exception:
                    pass
        self._send_urscript("stopj(2.0)\n")

    def _get_home_joints(self) -> List[float]:
        """Return the arm module's configured home joint angles."""
        if self.robot:
            arm = self.robot.get("arm")
            if arm is not None:
                try:
                    home = arm.get_state().get("home_angles_deg")
                    if home and len(home) == 6:
                        return [float(v) for v in home]
                except Exception:
                    pass
        return [0.0, -180.0, 135.0, -90.0, 0.0, 0.0]

    def _clamp_teleop_target(self, target_deg: List[float],
                             home_deg: List[float]) -> List[float]:
        """Keep teleop targets near the home pose to avoid mechanical limits."""
        result = []
        for i, angle in enumerate(target_deg):
            lower = home_deg[i] - self._teleop_joint_range_deg
            upper = home_deg[i] + self._teleop_joint_range_deg
            result.append(max(lower, min(upper, angle)))
        return result

    def _get_current_fk(self, joints_rad: List[float]):
        """Compute current FK from joint angles."""
        from teleoperation_sdk.ur5_ik import ur5_fk
        try:
            return ur5_fk(joints_rad)
        except Exception:
            return None

    def _refresh_joints(self) -> None:
        """Query current joint angles from arm module or UR5 secondary port."""
        now = time.time()
        if now - self._last_arm_poll < ARM_POLL_INTERVAL:
            return
        self._last_arm_poll = now

        # Try arm module first
        if self.robot:
            arm = self.robot.get("arm")
            if arm:
                try:
                    state = arm.get_state()
                    if state.get("connected"):
                        self._current_joints_deg = list(state.get("joint_actual_deg", self._current_joints_deg))
                        return
                except Exception:
                    pass

    def _control_hand(self, position_normalized: float) -> None:
        """Send hand position command (0.0 = open, 1.0 = closed)."""
        if self.robot:
            hand = self.robot.get("hand")
            if hand:
                try:
                    # Map 0-1 to hand positions respecting per-finger limits
                    hand.set_all_fingers([position_normalized] * 6, speed_pct=80.0)
                except Exception as e:
                    log.warning("[Teleop] Hand control error: %s", e)

    # ── UR5 socket management ──────────────────────────────────────────────

    def _connect_ur(self) -> bool:
        """Open URScript socket to the UR5."""
        with self._ur_lock:
            if self._ur_sock is not None:
                return True
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2.0)
                sock.connect((self._ur_host, self._ur_port))
                sock.settimeout(0.1)  # non-blocking for streaming
                self._ur_sock = sock
                log.info("[Teleop] UR5 fallback URScript socket opened")
                return True
            except Exception as e:
                log.warning("[Teleop] UR5 connect failed: %s", e)
                return False

    def _disconnect_ur(self) -> None:
        with self._ur_lock:
            if self._ur_sock:
                try:
                    self._ur_sock.close()
                except Exception:
                    pass
                self._ur_sock = None

    def _send_urscript(self, cmd: str) -> bool:
        """Send URScript command.

        In the integrated path (arm module loaded) the arm owns the single
        primary socket on port 30001 — stream through it to avoid a competing
        connection kicking the arm off. Falls back to our own socket when the
        arm module is absent (standalone / Quest-connect flow).
        """
        if self.robot:
            arm = self.robot.get("arm")
            if arm is not None:
                try:
                    state = arm.get_state()
                    if state.get("connected"):
                        return bool(arm.send_urscript(cmd))
                except Exception:
                    pass
        if not self._connect_ur():
            return False
        with self._ur_lock:
            try:
                self._ur_sock.sendall(cmd.encode("ascii"))
                return True
            except Exception:
                self._disconnect_ur()
                return False

    # ── gating ─────────────────────────────────────────────────────────────

    def _gating(self, active: bool) -> None:
        """Set _teleop_active flag on arm module to gate manual commands."""
        if self.robot:
            arm = self.robot.get("arm")
            if arm:
                arm._teleop_active = active
                log.debug("[Teleop] Arm gating: %s", "ON" if active else "OFF")
