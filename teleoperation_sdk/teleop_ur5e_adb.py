"""UR5e teleoperation via Oculus Quest 3 over ADB/USB.

Architecture:
  Oculus Quest 3 (S2R2S-teleop.apk) ← streams controller transforms to logcat
       | ADB (USB)
       v
  oculus_reader (Python, reads logcat) → OculusQuest3Controller
       |
       v
  UR5e IK (damped least-squares) → servoj (TCP port 30001)

Modes:
  IDLE      — waiting for grip (dead-man switch)
  TRACKING  — grip held → controller drives arm

Safety:
  - Grip button = dead-man switch (release → arm holds position)
  - Workspace boundary check before every servoj
  - Joint velocity limiting at 1.5 rad/s
  - Emergency stop on Ctrl+C → stopj(2.0)

Dependencies: numpy, pure-python-adb (ppadb)
"""

import logging
import math
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from teleoperation_sdk.oculus_reader.reader import OculusReader

log = logging.getLogger(__name__)

# ── constants ─────────────────────────────────────────────────────────────────

TELEOP_HZ = 100.0
SERVOJ_LOOKAHEAD = 0.1
SERVOJ_GAIN = 300
SERVOJ_TIME = 0.008
TRIGGER_DEADZONE = 0.05
JOINT_VELOCITY_LIMIT = 1.5       # rad/s max per joint per step
JOINT_POLL_INTERVAL = 0.5        # how often to read actual joints from UR5

DEFAULT_WORKSPACE_MIN = (-500.0, -500.0, 0.0)
DEFAULT_WORKSPACE_MAX = (500.0, 500.0, 850.0)

# VR → UR5 coordinate mapping matrix (3x3, row-major)
# Maps VR controller delta (right=X, up=Y, forward=Z)
# to UR5 base frame delta (X=forward, Y=left, Z=up)
# Standard mapping: VR_X→UR5_Y, VR_Y→UR5_Z, VR_Z→UR5_X
DEFAULT_VR_TO_UR5_MATRIX = [
    0, 0, 1,    # UR5 X = VR Z
    1, 0, 0,    # UR5 Y = VR X
    0, 1, 0,    # UR5 Z = VR Y
]


# ── VR config ─────────────────────────────────────────────────────────────────

@dataclass
class VRConfig:
    control_mode: str = "relative"
    displacement_scale: float = 1.0
    angle_scale: float = 1.0
    use_gripper: bool = False
    right_clutch_button: str = "rightGrip"
    left_clutch_button: str = "leftGrip"
    right_gripper_button: str = "rightTrig"
    left_gripper_button: str = "leftTrig"
    max_lin_step: float = 0.1
    max_ang_step: float = 0.1
    deadband: float = 1e-5
    round_digits: int = 5


class OculusQuest3Controller:
    """Wraps OculusReader to compute relative/absolute deltas from VR controller."""

    def __init__(self, config: Optional[VRConfig] = None):
        self.config = config or VRConfig()
        self.reader: Optional[OculusReader] = None
        self.prev_pos: Dict[str, np.ndarray] = {}
        self.prev_rot: Dict[str, np.ndarray] = {}
        self.origin_pos: Dict[str, np.ndarray] = {}
        self.origin_rot: Dict[str, np.ndarray] = {}

    def connect(self):
        if self.reader is None:
            self.reader = OculusReader()

    def disconnect(self):
        self.reader = None

    def is_connected(self) -> bool:
        return self.reader is not None

    def get_action(self) -> Dict[str, Any]:
        if not self.is_connected():
            return self._zero_action()

        transforms, buttons = self.reader.get_transformations_and_buttons()
        buttons = buttons or {}
        sides = {'r': 'right', 'l': 'left'}
        action: Dict[str, Any] = {}

        for raw_side, side_name in sides.items():
            if raw_side not in transforms:
                action.update(self._zero_side(side_name))
                continue

            T = transforms[raw_side]
            pos = T[:3, 3]
            rot_m = T[:3, :3]

            clutch_button = getattr(self.config, f"{side_name}_clutch_button")
            gripper_button = getattr(self.config, f"{side_name}_gripper_button")
            clutch_val = self._get_button(buttons, clutch_button)

            if clutch_val <= 0.5:
                self.prev_pos.pop(side_name, None)
                self.prev_rot.pop(side_name, None)
                action.update(self._zero_side(side_name))
                continue

            if self.config.control_mode == "relative":
                dx, dy, dz, droll, dpitch, dyaw = self._compute_relative(side_name, pos, rot_m)
                key_prefix = f"{side_name}_delta_"
            else:
                dx, dy, dz, droll, dpitch, dyaw = self._compute_absolute(side_name, pos, rot_m)
                key_prefix = f"{side_name}_abs_"

            grip_raw = self._get_button(buttons, gripper_button)
            gripper = float(grip_raw) if self.config.use_gripper else 0.0

            dx, dy, dz = [self._limit_scale(v, self.config.displacement_scale, self.config.max_lin_step) for v in (dx, dy, dz)]
            droll, dpitch, dyaw = [self._limit_scale(v, self.config.angle_scale, self.config.max_ang_step) for v in (droll, dpitch, dyaw)]

            action[f"{key_prefix}x"] = dx
            action[f"{key_prefix}y"] = dy
            action[f"{key_prefix}z"] = dz
            action[f"{key_prefix}roll"] = droll
            action[f"{key_prefix}pitch"] = dpitch
            action[f"{key_prefix}yaw"] = dyaw
            action[f"{side_name}_gripper"] = gripper

            self.prev_pos[side_name] = pos
            self.prev_rot[side_name] = rot_m

        action["buttons"] = buttons
        return action

    def _compute_relative(self, side: str, pos: np.ndarray, rot_m: np.ndarray):
        if side not in self.prev_pos:
            return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        dpos = pos - self.prev_pos[side]
        rpy = self.rotation_matrix_to_euler(rot_m)
        prev_rpy = self.rotation_matrix_to_euler(self.prev_rot[side])
        drpy = [c - p for c, p in zip(rpy, prev_rpy)]
        return (*dpos.tolist(), *drpy)

    def _compute_absolute(self, side: str, pos: np.ndarray, rot_m: np.ndarray):
        if side not in self.origin_pos:
            self.origin_pos[side] = pos.copy()
            self.origin_rot[side] = rot_m.copy()
        dpos = pos - self.origin_pos[side]
        rpy = self.rotation_matrix_to_euler(rot_m)
        base_rpy = self.rotation_matrix_to_euler(self.origin_rot[side])
        drpy = [c - b for c, b in zip(rpy, base_rpy)]
        return (*dpos.tolist(), *drpy)

    def _limit_scale(self, val: float, scale: float, limit_abs: float):
        v = val * scale
        if abs(v) < self.config.deadband:
            v = 0.0
        if v > limit_abs:
            v = limit_abs
        elif v < -limit_abs:
            v = -limit_abs
        return round(v, self.config.round_digits)

    def _zero_side(self, side_name: str) -> Dict[str, float]:
        if self.config.control_mode == "relative":
            base_keys = ["delta_x", "delta_y", "delta_z", "delta_roll", "delta_pitch", "delta_yaw"]
        else:
            base_keys = ["abs_x", "abs_y", "abs_z", "abs_roll", "abs_pitch", "abs_yaw"]
        prefix = f"{side_name}_"
        d = {prefix + k: 0.0 for k in base_keys}
        d[f"{side_name}_gripper"] = 0.0
        return d

    def _zero_action(self):
        action = {}
        action.update(self._zero_side("right"))
        action.update(self._zero_side("left"))
        action["buttons"] = {}
        return action

    @staticmethod
    def _get_button(buttons: Dict[str, Any], name: str) -> float:
        val = buttons.get(name, 0.0)
        if isinstance(val, (tuple, list)):
            val = val[0]
        try:
            return float(val)
        except Exception:
            return 0.0

    @staticmethod
    def rotation_matrix_to_euler(R: np.ndarray):
        sy = np.sqrt(R[0, 0] ** 2 + R[1, 0] ** 2)
        singular = sy < 1e-6
        if not singular:
            roll = np.arctan2(R[2, 1], R[2, 2])
            pitch = np.arctan2(-R[2, 0], sy)
            yaw = np.arctan2(R[1, 0], R[0, 0])
        else:
            roll = np.arctan2(-R[1, 2], R[1, 1])
            pitch = np.arctan2(-R[2, 0], sy)
            yaw = 0.0
        return roll, pitch, yaw


# ── UR5e teleop controller ────────────────────────────────────────────────────

class UR5eTeleopADB:
    """Standalone UR5e teleoperation controller via Quest 3 ADB/USB.

    Config keys:
        ur_host:           UR5 controller IP (default "192.168.56.101")
        urscript_port:     URScript streaming port (default 30001)
        ur_state_port:     UR5 state read port (default 30002)
        arm_controller:    "left" or "right" (default "right")
        displacement_scale: position scaling (default 0.5)
        workspace_min_xyz: safety lower bound (mm)
        workspace_max_xyz: safety upper bound (mm)
        vr_to_ur5_matrix:  3x3 mapping from VR delta to UR5 delta
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        self._ur_host = cfg.get("ur_host", "192.168.56.101")
        self._ur_port = int(cfg.get("urscript_port", 30001))
        self._ur_state_port = int(cfg.get("ur_state_port", 30002))
        self._arm_controller = cfg.get("arm_controller", "right")
        self._disp_scale = float(cfg.get("displacement_scale", 0.5))
        self._rot_scale = float(cfg.get("rotation_scale", 0.3))
        self._ws_min = tuple(cfg.get("workspace_min_xyz", DEFAULT_WORKSPACE_MIN))
        self._ws_max = tuple(cfg.get("workspace_max_xyz", DEFAULT_WORKSPACE_MAX))
        self._vr_to_ur5 = np.array(cfg.get("vr_to_ur5_matrix", DEFAULT_VR_TO_UR5_MATRIX), dtype=float).reshape(3, 3)

        # VR controller
        self._vr = OculusQuest3Controller(VRConfig(
            control_mode="relative",
            displacement_scale=self._disp_scale,
            angle_scale=self._rot_scale,
            use_gripper=False,
            max_lin_step=0.3,
            max_ang_step=0.3,
        ))

        # Runtime state
        self._lock = threading.RLock()
        self._running = False
        self._tracking = False

        # Joint state tracking
        self._current_joints_deg = [0.0] * 6
        self._target_joints_deg = [0.0] * 6
        self._last_joint_poll = 0.0

        # UR5 sockets
        self._ur_sock: Optional[socket.socket] = None
        self._ur_state_sock: Optional[socket.socket] = None
        self._ur_lock = threading.Lock()

        # Data logging
        self._data_log: List[Dict] = []
        self._data_log_enabled = False

    # ── connection ────────────────────────────────────────────────────────

    def connect(self) -> bool:
        """Connect to Quest 3 via ADB and UR5 via TCP."""
        log.info("[UR5eTeleop] Connecting to Quest 3 via ADB...")
        try:
            self._vr.connect()
            log.info("[UR5eTeleop] Quest 3 connected")
        except Exception as e:
            log.error("[UR5eTeleop] Failed to connect Quest 3: %s", e)
            log.error("Ensure Quest 3 is USB-connected with Developer Mode enabled.")
            log.error("Run: adb devices  (should show Quest 3 serial)")
            return False

        log.info("[UR5eTeleop] Connecting to UR5 at %s:%d...", self._ur_host, self._ur_port)
        if not self._connect_ur():
            return False

        # Read initial joint state
        self._refresh_joints()

        return True

    def disconnect(self) -> None:
        """Disconnect from all devices."""
        self._running = False
        self._vr.disconnect()
        self._disconnect_ur()
        log.info("[UR5eTeleop] Disconnected")

    # ── UR5 socket management ─────────────────────────────────────────────

    def _connect_ur(self) -> bool:
        with self._ur_lock:
            if self._ur_sock is not None:
                return True
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2.0)
                sock.connect((self._ur_host, self._ur_port))
                sock.settimeout(0.1)
                self._ur_sock = sock
                log.info("[UR5eTeleop] UR5 servoj socket opened")
                return True
            except Exception as e:
                log.warning("[UR5eTeleop] UR5 connect failed: %s", e)
                return False

    def _disconnect_ur(self) -> None:
        with self._ur_lock:
            if self._ur_sock:
                try:
                    self._ur_sock.close()
                except Exception:
                    pass
                self._ur_sock = None
            if self._ur_state_sock:
                try:
                    self._ur_state_sock.close()
                except Exception:
                    pass
                self._ur_state_sock = None

    def _send_urscript(self, cmd: str) -> None:
        if not self._connect_ur():
            return
        with self._ur_lock:
            try:
                self._ur_sock.sendall(cmd.encode("ascii"))
            except Exception:
                self._disconnect_ur()

    def _read_joints_from_ur(self) -> Optional[List[float]]:
        """Read actual joint positions from UR5 via secondary port."""
        try:
            if self._ur_state_sock is None:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(1.0)
                s.connect((self._ur_host, self._ur_state_port))
                self._ur_state_sock = s

            self._ur_state_sock.sendall("get_actual_joint_positions()\n".encode("ascii"))
            data = b""
            self._ur_state_sock.settimeout(0.3)
            while True:
                try:
                    chunk = self._ur_state_sock.recv(1024)
                    if not chunk:
                        break
                    data += chunk
                except socket.timeout:
                    break

            text = data.decode("ascii", errors="replace").strip()
            # UR5 returns: [-0.123, -1.456, ...] with potential header junk
            # Find the bracketed list
            start = text.find("[")
            end = text.find("]")
            if start >= 0 and end > start:
                values_str = text[start + 1:end]
                values = [float(v.strip()) for v in values_str.split(",")]
                if len(values) == 6:
                    return values
        except Exception as e:
            log.debug("[UR5eTeleop] Joint read failed: %s", e)
            # Reset state socket on error
            if self._ur_state_sock:
                try:
                    self._ur_state_sock.close()
                except Exception:
                    pass
                self._ur_state_sock = None
        return None

    # ── forward kinematics ────────────────────────────────────────────────

    def _get_current_fk(self, joints_rad: List[float]):
        from teleoperation_sdk.ur5_ik import ur5_fk
        try:
            return ur5_fk(joints_rad)
        except Exception:
            return None

    def _refresh_joints(self) -> None:
        """Read joint state from UR5 at poll interval."""
        now = time.time()
        if now - self._last_joint_poll < JOINT_POLL_INTERVAL:
            return
        self._last_joint_poll = now

        joints_rad = self._read_joints_from_ur()
        if joints_rad is not None:
            with self._lock:
                self._current_joints_deg = [math.degrees(a) for a in joints_rad]

    # ── main loop ─────────────────────────────────────────────────────────

    def run(self, duration: float = 0, print_interval: float = 1.0) -> None:
        """Run teleoperation loop.

        Args:
            duration: Seconds to run (0 = run until Ctrl+C).
            print_interval: Seconds between status prints.
        """
        self._running = True
        interval = 1.0 / TELEOP_HZ
        start_time = time.time()
        last_print = 0.0
        frame_count = 0

        log.info("[UR5eTeleop] Control loop started at %.0f Hz", TELEOP_HZ)
        log.info("[UR5eTeleop] Grip middle-finger trigger to move arm. Ctrl+C to stop.")

        try:
            while self._running:
                loop_start = time.time()

                # Read VR controller
                action = self._vr.get_action()

                # Check grip (dead-man switch)
                side = self._arm_controller
                grip_key = f"{side}_gripper"
                buttons = action.get("buttons", {})

                # Clutch is the grip button for the selected controller
                clutch_button = f"{'right' if side == 'right' else 'left'}Grip"
                clutch_val = float(buttons.get(clutch_button, 0.0)) if buttons else 0.0

                tracking = clutch_val > 0.5
                self._tracking = tracking

                if tracking:
                    self._step_tracking(action)

                frame_count += 1

                # Periodic status print
                now = time.time()
                if now - last_print > print_interval:
                    self._print_status(action)
                    last_print = now

                # Duration check
                if duration > 0 and (now - start_time) > duration:
                    break

                # Sleep remaining interval
                elapsed = time.time() - loop_start
                if elapsed < interval:
                    time.sleep(interval - elapsed)

        except KeyboardInterrupt:
            log.info("[UR5eTeleop] Interrupted by user")
        finally:
            self._send_urscript("stopj(2.0)\n")
            self._running = False
            self._tracking = False
            log.info("[UR5eTeleop] Stopped — arm frozen at current position")

    def _step_tracking(self, action: Dict[str, Any]) -> None:
        """One iteration: read VR delta → IK → servoj."""
        from teleoperation_sdk.ur5_ik import ur5_ik

        self._refresh_joints()

        side = self._arm_controller
        dx = action.get(f"{side}_delta_x", 0.0)
        dy = action.get(f"{side}_delta_y", 0.0)
        dz = action.get(f"{side}_delta_z", 0.0)

        # Map VR delta → UR5 delta via configurable matrix
        vr_delta = np.array([dx, dy, dz])
        ur5_delta = self._vr_to_ur5 @ vr_delta  # 3x3 @ 3x1 → 3x1

        # Convert metres to millimetres for IK
        ur5_dx = float(ur5_delta[0]) * 1000.0
        ur5_dy = float(ur5_delta[1]) * 1000.0
        ur5_dz = float(ur5_delta[2]) * 1000.0

        # Skip negligible deltas
        if abs(ur5_dx) < 0.1 and abs(ur5_dy) < 0.1 and abs(ur5_dz) < 0.1:
            return

        # Current FK
        current_joints_rad = [math.radians(a) for a in self._current_joints_deg]
        current_pose = self._get_current_fk(current_joints_rad)
        if current_pose is None:
            return

        # Target = current position + controller delta
        tx = current_pose[0][3] + ur5_dx
        ty = current_pose[1][3] + ur5_dy
        tz = current_pose[2][3] + ur5_dz

        # Workspace bounds check
        if not (self._ws_min[0] <= tx <= self._ws_max[0] and
                self._ws_min[1] <= ty <= self._ws_max[1] and
                self._ws_min[2] <= tz <= self._ws_max[2]):
            log.debug("[UR5eTeleop] Target outside workspace: (%.0f, %.0f, %.0f)", tx, ty, tz)
            return

        # Build target pose (keep current rotation)
        target_pose = [
            [current_pose[0][0], current_pose[0][1], current_pose[0][2], tx],
            [current_pose[1][0], current_pose[1][1], current_pose[1][2], ty],
            [current_pose[2][0], current_pose[2][1], current_pose[2][2], tz],
            [0.0, 0.0, 0.0, 1.0],
        ]

        # IK
        sol = ur5_ik(target_pose, seed_joints_rad=current_joints_rad)
        if sol is None:
            return

        target_deg = [math.degrees(a) for a in sol]

        # Joint velocity limit
        dt = 1.0 / TELEOP_HZ
        clamped_deg = list(target_deg)
        for i in range(6):
            delta = target_deg[i] - self._current_joints_deg[i]
            max_delta = math.degrees(JOINT_VELOCITY_LIMIT) * dt
            if abs(delta) > max_delta:
                clamped_deg[i] = self._current_joints_deg[i] + math.copysign(max_delta, delta)

        with self._lock:
            self._target_joints_deg = clamped_deg
            self._current_joints_deg = clamped_deg  # track commanded state

        # Send servoj
        q_rad = [math.radians(a) for a in clamped_deg]
        cmd = "servoj([{:.6f}, {:.6f}, {:.6f}, {:.6f}, {:.6f}, {:.6f}], "
        cmd += "a=1.4, v=0.8, t={:.4f}, lookahead_time={:.2f}, gain={:d})\n"
        self._send_urscript(cmd.format(*q_rad, SERVOJ_TIME, SERVOJ_LOOKAHEAD, SERVOJ_GAIN))

        # Data logging
        if self._data_log_enabled:
            self._data_log.append({
                "t": time.time(),
                "vr_delta": [dx, dy, dz],
                "ur5_delta": [ur5_dx, ur5_dy, ur5_dz],
                "target_joints_deg": list(target_deg),
            })
            if len(self._data_log) > 10000:
                self._data_log = self._data_log[-5000:]

    def _print_status(self, action: Dict[str, Any]) -> None:
        side = self._arm_controller
        dx = action.get(f"{side}_delta_x", 0.0)
        dy = action.get(f"{side}_delta_y", 0.0)
        dz = action.get(f"{side}_delta_z", 0.0)
        status = "TRACKING" if self._tracking else "IDLE"
        log.info("[%s] VR=(%+.4f, %+.4f, %+.4f) m, joints=[%s]",
                 status, dx, dy, dz,
                 ", ".join(f"{a:.1f}" for a in self._target_joints_deg))

    # ── public API ────────────────────────────────────────────────────────

    @property
    def tracking(self) -> bool:
        return self._tracking

    @property
    def current_joints_deg(self) -> List[float]:
        with self._lock:
            return list(self._current_joints_deg)

    def emergency_stop(self) -> None:
        """Send emergency stop and freeze arm."""
        log.warning("[UR5eTeleop] EMERGENCY STOP")
        self._send_urscript("stopj(2.0)\n")
        self._running = False
        self._tracking = False

    def toggle_logging(self) -> bool:
        """Toggle data logging on/off. Returns new state."""
        self._data_log_enabled = not self._data_log_enabled
        if not self._data_log_enabled:
            self._data_log.clear()
        return self._data_log_enabled

    def get_log(self) -> List[Dict]:
        """Return accumulated data log."""
        with self._lock:
            return list(self._data_log)
