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
          UR5 arm (servoj TCP)  BrainCo Hand (Modbus)  Gimbal Camera (CH340 UART)

Quest 3 compatibility note: The XR-Robotics PC Service SDK communicates via
OpenXR, which Quest 3 natively supports. The prebuilt PICO APK must be
replaced with a Quest 3 build of the Unity XR client. See teleoperation_sdk/
for build instructions.

Modes:
  IDLE            — teleop disabled, no tracking
  ARMED           — teleop enabled, waiting for grip press (dead-man)
  TRACKING        — grip held, controller drives arm + hand
  GIMBAL_TRACKING — headset orientation drives gimbal camera (independent of arm)

Safety:
  - Grip button = dead-man switch (release → arm holds position)
  - Workspace boundary check before sending servoj
  - Emergency stop sends stopj(2.0) + disables tracking
  - arm.py motion commands gated when teleop active
"""

import logging
import math
import socket
import threading
import time
from typing import Any, Dict, List, Optional

from robot_control.module_base import RobotModule

log = logging.getLogger(__name__)

# Lazy import — gracefully degrade if SDK not installed
try:
    import xrobotoolkit_sdk as xrt
    _XRT_AVAILABLE = True
except ImportError:
    xrt = None
    _XRT_AVAILABLE = False

# ── constants ─────────────────────────────────────────────────────────────────

TELEOP_HZ = 100.0                # servoj streaming rate
SERVOJ_LOOKAHEAD = 0.1           # seconds
SERVOJ_GAIN = 300                # UR5 servoj gain
SERVOJ_TIME = 0.008              # controller period (~8ms at 125Hz)
TRIGGER_DEADZONE = 0.05          # below this, hand stops moving
JOINT_VELOCITY_LIMIT = 1.5       # rad/s max per joint per step (safety clamp)
ARM_POLL_INTERVAL = 0.5          # how often to refresh current joints from arm module

# Default workspace safety bounds (mm, base frame)
DEFAULT_WORKSPACE_MIN = [-500.0, -500.0, 0.0]
DEFAULT_WORKSPACE_MAX = [500.0, 500.0, 850.0]

# Head tracking → gimbal
HEAD_TRACK_HZ = 20.0              # head→gimbal update rate
HEAD_GIMBAL_DEADBAND = 0.3        # degrees; default deadband (adjustable via GUI)
HEAD_EMA_ALPHA = 0.25             # EMA smoothing default (adjustable via GUI)
HEAD_RATE_LIMIT = 300.0           # deg/s; max gimbal target velocity (adjustable via GUI)
HEAD_DEADZONE = 5.0              # degrees; hysteresis dead zone (only move when head exceeds this)
HEAD_PRECISION = 0.5             # degrees; quantize target to this step (eliminate encoder hunting)
HEAD_COOLDOWN = 0.25             # seconds; minimum interval between gimbal move commands


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
        self._ur_host = cfg.get("host", "192.168.56.101")
        self._ur_port = int(cfg.get("urscript_port", 30001))
        self._arm_controller = cfg.get("arm_controller", "right")
        self._ws_min = cfg.get("workspace_min_xyz", DEFAULT_WORKSPACE_MIN)
        self._ws_max = cfg.get("workspace_max_xyz", DEFAULT_WORKSPACE_MAX)

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

        # Runtime state (thread-safe via _lock)
        self._lock = threading.RLock()
        self._running = False
        self._active = False       # teleop enabled (ARMED or TRACKING)
        self._tracking = False     # grip held → active servoj streaming
        self._calibrated = False
        self._xr_initialized = False
        self._connected = False

        # Calibration offset (controller home → arm home)
        self._calib_pos = [0.0, 0.0, 0.0]
        self._calib_rot = [0.0, 0.0, 0.0, 1.0]  # quaternion x,y,z,w

        # Current controller state
        self._ctrl_pos = [0.0, 0.0, 0.0]
        self._trigger_val = 0.0
        self._grip_pressed = False
        self._button_a = False
        self._button_b = False

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
        self._last_gimbal_move = 0.0   # cooldown timer to suppress oscillation
        self._head_cooldown = float(cfg.get("head_cooldown", HEAD_COOLDOWN))

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
            "trigger_value": 0.0,
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
            "gimbal_pitch_limits": [-90, 60],
        }

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "connected": self._connected,
                "xr_initialized": self._xr_initialized,
                "active": self._active,
                "tracking": self._tracking,
                "calibrated": self._calibrated,
                "controller_position": list(self._ctrl_pos),
                "trigger_value": self._trigger_val,
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
                "head_cooldown": self._head_cooldown,
                "gimbal_yaw_limits": list(self._effective_yaw_limits()),
                "gimbal_pitch_limits": list(self._effective_pitch_limits()),
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
        }

    # ── lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        if not _XRT_AVAILABLE:
            log.warning("[Teleop] xrobotoolkit_sdk not available — stub mode")
            self._connected = False
            return

        try:
            xrt.init()
            self._xr_initialized = True
            self._connected = True
            log.info("[Teleop] XR SDK initialized")
        except Exception as e:
            log.error("[Teleop] XR SDK init failed: %s", e)
            self._xr_initialized = False
            self._connected = False
            return

        self._running = True
        self._thread = threading.Thread(target=self._control_loop, daemon=True, name="teleop-ctrl")
        self._thread.start()
        log.info("[Teleop] Control loop started at %.0f Hz", TELEOP_HZ)

    def stop(self) -> None:
        self._running = False
        if self._active:
            self._send_urscript("stopj(2.0)\n")
            self._gating(False)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

        self._disconnect_ur()

        if self._xr_initialized:
            try:
                xrt.close()
            except Exception:
                pass
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
        self._send_urscript("stopj(2.0)\n")
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
        with self._lock:
            self._active = True
            self._tracking = False
        self._gating(True)
        log.info("[Teleop] Enabled (ARMED)")

    def _on_disable(self, _data: dict = None, emit=None) -> None:
        self._send_urscript("stopj(2.0)\n")
        self._gating(False)
        with self._lock:
            self._active = False
            self._tracking = False
        log.info("[Teleop] Disabled")

    def _on_calibrate(self, _data: dict = None, emit=None) -> None:
        """Record current controller pose as reference offset to current arm position."""
        if not self._xr_initialized:
            if emit:
                emit("error", {"msg": "XR SDK not initialized"})
            return

        # Read current controller pose
        try:
            if self._arm_controller == "left":
                ctrl = xrt.get_left_controller_pose()
            else:
                ctrl = xrt.get_right_controller_pose()
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
            # Reset EMA filter and cooldown to avoid stale values
            self._ema_initialized = False
            self._last_gimbal_move = 0.0
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
            if "cooldown" in data:
                self._head_cooldown = max(0.05, min(2.0, float(data["cooldown"])))
        log.info("[Teleop] Head config: dz=%.1f° db=%.2f° sm=%.2f rate=%.0f°/s prec=%.1f° cool=%.2fs",
                 self._head_deadzone, self._head_deadband, self._head_smoothing,
                 self._head_rate_limit, self._head_cooldown)
        if emit:
            emit("head_config_updated", {
                "head_deadband": self._head_deadband,
                "head_smoothing": self._head_smoothing,
                "head_rate_limit": self._head_rate_limit,
                "head_deadzone": self._head_deadzone,
                "head_precision": self._head_precision,
                "head_cooldown": self._head_cooldown,
            })

    # ── head tracking ──────────────────────────────────────────────────────

    def _read_head_pose(self) -> Optional[List[float]]:
        """Read Quest 3 headset pose. Returns [x, y, z, qx, qy, qz, qw] or None."""
        if not self._xr_initialized:
            return None
        try:
            if hasattr(xrt, 'get_headset_pose'):
                pose = xrt.get_headset_pose()
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
            log.debug("[Teleop] Dead zone passed: head_dyaw=%.1f head_dpitch=%.1f "
                      "ema=(%.1f,%.1f) dz=(%.1f,%.1f) raw=(%.1f,%.1f)",
                      head_dyaw, head_dpitch, self._ema_yaw, self._ema_pitch,
                      self._head_dz_yaw, self._head_dz_pitch, head_yaw, head_pitch)

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

        # Cooldown: enforce minimum interval between gimbal moves to prevent
        # feedback-loop oscillation (servo motion → camera view change →
        # unconscious head reaction → new target → repeat).
        if now - self._last_gimbal_move < self._head_cooldown:
            log.debug("[Teleop] Cooldown active: %.2fs since last move (need %.2fs)",
                      now - self._last_gimbal_move, self._head_cooldown)
            return
        self._last_gimbal_move = now
        log.debug("[Teleop] Pipeline → gimbal target: (%.1f°, %.1f°) "
                  "from ema=(%.1f,%.1f) dz=(%.1f,%.1f) precision=%.1f",
                  target_yaw, target_pitch, self._ema_yaw, self._ema_pitch,
                  self._head_dz_yaw, self._head_dz_pitch, p)

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
            return tuple(cfg.get("pitch_limits", [-90, 60]))
        return (-90, 60)

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

                # ── Arm tracking (100 Hz) ──
                if active:
                    tracking = self._grip_pressed if self._xr_initialized else False
                    self._tracking = tracking
                    if tracking and self._calibrated:
                        self._step_tracking()

                # ── Gimbal head tracking (5 Hz, independent of arm) ──
                if gimbal_tracking:
                    self._step_head_tracking()

                time.sleep(interval)

            except Exception as e:
                log.error("[Teleop] Control loop error: %s", e)
                time.sleep(0.5)

    def _read_controller(self) -> None:
        """Read XR controller state into cached fields."""
        try:
            get_pose = xrt.get_left_controller_pose if self._arm_controller == "left" \
                else xrt.get_right_controller_pose
            get_trigger = xrt.get_left_trigger if self._arm_controller == "left" \
                else xrt.get_right_trigger
            get_grip = xrt.get_left_grip if self._arm_controller == "left" \
                else xrt.get_right_grip

            pose = get_pose()
            if pose is not None and len(pose) >= 7:
                with self._lock:
                    self._ctrl_pos = [float(pose[0]), float(pose[1]), float(pose[2])]
            # quaternion: pose[3..6]

            trigger = get_trigger()
            with self._lock:
                self._trigger_val = float(trigger) if trigger is not None else 0.0

            grip = get_grip()
            with self._lock:
                self._grip_pressed = bool(grip)

            a_btn = xrt.get_A_button()
            b_btn = xrt.get_B_button()
            with self._lock:
                self._button_a = bool(a_btn)
                self._button_b = bool(b_btn)

        except Exception as e:
            log.warning("[Teleop] Controller read error: %s", e)

    def _step_tracking(self) -> None:
        """One iteration of the tracking loop: IK → servoj → hand control."""
        from teleoperation_sdk.ur5_ik import pose_from_quat_xyz, ur5_ik

        # Refresh current joint state from arm module
        self._refresh_joints()

        # Compute target pose from controller + calibration
        # For now: use current arm joints as seed, map controller delta to Cartesian delta
        ctrl_pos = list(self._ctrl_pos)
        calib_pos = self._calib_pos

        # Controller delta from calibration position (in metres from XR SDK)
        dx = (ctrl_pos[0] - calib_pos[0]) * 1000.0  # m → mm
        dy = (ctrl_pos[1] - calib_pos[1]) * 1000.0
        dz = (ctrl_pos[2] - calib_pos[2]) * 1000.0

        # Build target pose: start from current FK, apply delta to position
        # For rotation: use identity offset from calibration
        current_joints_rad = [math.radians(a) for a in self._current_joints_deg]
        current_pose = self._get_current_fk(current_joints_rad)

        if current_pose is None:
            return

        # Target = current position + controller delta
        tx = current_pose[0][3] + dx
        ty = current_pose[1][3] + dz  # XR Z-up → UR5 coordinate mapping
        tz = current_pose[2][3] + dy

        # Workspace bounds check
        if not (self._ws_min[0] <= tx <= self._ws_max[0] and
                self._ws_min[1] <= ty <= self._ws_max[1] and
                self._ws_min[2] <= tz <= self._ws_max[2]):
            log.debug("[Teleop] Target outside workspace: (%.0f, %.0f, %.0f)", tx, ty, tz)
            return

        # For now keep current rotation, only track position
        target_pose = [
            [current_pose[0][0], current_pose[0][1], current_pose[0][2], tx],
            [current_pose[1][0], current_pose[1][1], current_pose[1][2], ty],
            [current_pose[2][0], current_pose[2][1], current_pose[2][2], tz],
            [0.0, 0.0, 0.0, 1.0],
        ]

        # IK from current joints (warm start → fast convergence)
        sol = ur5_ik(target_pose, seed_joints_rad=current_joints_rad)
        if sol is None:
            return

        target_deg = [math.degrees(a) for a in sol]
        with self._lock:
            self._target_joints_deg = target_deg

        # Rate-limit: clamp joint velocity
        dt = 1.0 / TELEOP_HZ
        clamped_deg = list(target_deg)
        for i in range(6):
            delta = target_deg[i] - self._current_joints_deg[i]
            max_delta = math.degrees(JOINT_VELOCITY_LIMIT) * dt
            if abs(delta) > max_delta:
                clamped_deg[i] = self._current_joints_deg[i] + math.copysign(max_delta, delta)

        # Send servoj
        q_rad = [math.radians(a) for a in clamped_deg]
        cmd = "servoj([{:.6f}, {:.6f}, {:.6f}, {:.6f}, {:.6f}, {:.6f}], "
        cmd += "a=1.4, v=0.8, t={:.4f}, lookahead_time={:.2f}, gain={:d})\n"
        cmd = cmd.format(*q_rad, SERVOJ_TIME, SERVOJ_LOOKAHEAD, SERVOJ_GAIN)
        self._send_urscript(cmd)

        # Hand control from trigger
        trigger = self._trigger_val
        if trigger > TRIGGER_DEADZONE:
            self._control_hand(trigger)
        else:
            self._control_hand(0.0)

        # Data logging
        if self._data_log_enabled:
            self._data_log.append({
                "t": time.time(),
                "ctrl_pos": list(self._ctrl_pos),
                "target_joints_deg": list(target_deg),
                "current_joints_deg": list(self._current_joints_deg),
                "trigger": trigger,
            })
            if len(self._data_log) > 10000:
                self._data_log = self._data_log[-5000:]

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
                log.info("[Teleop] UR5 servoj socket opened")
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

    def _send_urscript(self, cmd: str) -> None:
        """Send URScript command via dedicated socket."""
        if not self._connect_ur():
            return
        with self._ur_lock:
            try:
                self._ur_sock.sendall(cmd.encode("ascii"))
            except Exception:
                self._disconnect_ur()

    # ── gating ─────────────────────────────────────────────────────────────

    def _gating(self, active: bool) -> None:
        """Set _teleop_active flag on arm module to gate manual commands."""
        if self.robot:
            arm = self.robot.get("arm")
            if arm:
                arm._teleop_active = active
                log.debug("[Teleop] Arm gating: %s", "ON" if active else "OFF")
