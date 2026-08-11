"""UR5e robotic arm controller via TCP (Dashboard + URScript + Secondary Client).

RobotModule wrapper around the UR Dashboard (port 29999), URScript (port 30001),
and Secondary Client (port 30002) for real-time joint data.
"""

import logging
import math
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from robot_control.module_base import RobotModule

log = logging.getLogger(__name__)


@dataclass
class ArmLogEntry:
    timestamp: float
    level: str
    message: str


@dataclass
class ArmState:
    joint_angles_deg: List[float] = field(default_factory=lambda: [0.0] * 6)
    joint_actual_deg: List[float] = field(default_factory=lambda: [0.0] * 6)
    joint_currents_a: List[float] = field(default_factory=lambda: [0.0] * 6)
    joint_temps_c: List[float] = field(default_factory=lambda: [0.0] * 6)
    joint_voltages_v: List[float] = field(default_factory=lambda: [0.0] * 6)
    end_effector_xyz: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    is_moving: bool = False
    is_fault: bool = False
    is_enabled: bool = False
    connected: bool = False
    robot_mode: str = "UNKNOWN"
    safety_mode: str = "UNKNOWN"
    fault_text: str = ""
    collision_sensitivity: float = 0.3
    collision_warning: str = ""
    logs: List[dict] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


class ArmController(RobotModule):
    HOME_ANGLES_DEG = [90.0, 0.0, 90.0, -90.0, 90.0, -135.0]
    MAX_SPEED_PCT = 20.0
    LOG_CAPACITY = 80
    RECONNECT_INTERVAL = 3.0
    POLL_INTERVAL = 1.0

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        self._host = cfg.get("host", "192.168.56.101")
        self._dash_port = int(cfg.get("dashboard_port", 29999))
        self._ur_port = int(cfg.get("urscript_port", 30001))
        self._state_port = int(cfg.get("state_port", 30002))
        self._state = ArmState()
        self._lock = threading.RLock()
        self._running = False
        self._dash_sock: Optional[socket.socket] = None
        self._ur_sock: Optional[socket.socket] = None
        self._state_sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._state_thread: Optional[threading.Thread] = None
        self._logs: list[ArmLogEntry] = []
        # Home position — set_home() overwrites this from current joint_actual_deg
        self._home_angles: List[float] = [
            float(a) for a in cfg.get("home_angles_deg", self.HOME_ANGLES_DEG)
        ]
        self._collision_enabled = True
        self._joints_initialized = False
        self._payload_mass = float(cfg.get("payload_mass", 0.5))
        self._payload_cog = [float(x) for x in cfg.get("payload_cog", [0.0, 0.0, 0.05])]
        self._speed_pct = min(self.MAX_SPEED_PCT,
                              max(1.0, float(cfg.get("speed_pct", 20.0))))

    # ── RobotModule metadata ───────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "arm"

    @property
    def display_name(self) -> str:
        return "机械臂 UR5e"

    def get_state_schema(self) -> Dict[str, Any]:
        return {
            "joint_angles_deg": [0.0] * 6,
            "joint_actual_deg": [0.0] * 6,
            "joint_currents_a": [0.0] * 6,
            "joint_temps_c": [0.0] * 6,
            "joint_voltages_v": [0.0] * 6,
            "end_effector_xyz": [0.0, 0.0, 0.0],
            "home_angles_deg": list(self._home_angles),
            "speed_pct": self._speed_pct,
            "is_moving": False, "is_fault": False, "is_enabled": False,
            "connected": False,
            "robot_mode": "UNKNOWN", "safety_mode": "UNKNOWN", "fault_text": "",
            "collision_sensitivity": 0.3,
            "collision_warning": "",
            "logs": [], "timestamp": 0.0,
        }

    def get_socketio_handlers(self) -> Dict[str, callable]:
        return {
            "arm_enable": self._on_enable,
            "arm_disable": self._on_disable,
            "arm_reset_fault": self._on_reset_fault,
            "arm_move_joints": self._on_move_joints,
            "arm_home": self._on_home,
            "arm_set_home": self._on_set_home,
            "arm_stop": self._on_stop,
            "arm_set_speed": self._on_set_speed,
            "arm_set_collision_sensitivity": self._on_set_collision_sensitivity,
        }

    # ── lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._connection_loop, daemon=True, name="arm-conn")
        self._thread.start()
        self._state_thread = threading.Thread(target=self._state_reader_loop, daemon=True, name="arm-state")
        self._state_thread.start()
        log.info("[Arm] Connection loop started — host=%s", self._host)
        self._add_log("info", f"开始连接 {self._host}")

    def stop(self) -> None:
        self._running = False
        with self._lock:
            self._state.connected = False
            self._state.is_enabled = False
            self._state.is_fault = False
        for sock in (self._dash_sock, self._ur_sock, self._state_sock):
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
        with self._lock:
            self._dash_sock = None
            self._ur_sock = None
            self._state_sock = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        if self._state_thread and self._state_thread.is_alive():
            self._state_thread.join(timeout=5.0)
        log.info("[Arm] Stopped")
        self._add_log("info", "已断开")

    # ── commands ───────────────────────────────────────────────────────────

    def enable(self) -> None:
        r1 = self._dashboard_cmd("power on")
        time.sleep(0.3)
        r2 = self._dashboard_cmd("brake release")
        log.info("[Arm] enable() — power on: %s, brake release: %s", r1, r2)
        self._add_log("info", f"使能 — power on: {r1}, brake: {r2}")

    def disable(self) -> None:
        r = self._dashboard_cmd("power off")
        log.info("[Arm] disable() — power off: %s", r)
        with self._lock:
            self._state.is_enabled = False
        self._add_log("info", f"断开动力 — {r}")

    def move_joints(self, angles_deg: List[float], speed_pct: float = 20.0) -> bool:
        speed_pct = min(float(speed_pct), self.MAX_SPEED_PCT)
        return self._move_joints(angles_deg, speed_pct=speed_pct, teleop=False)

    def teleop_move_joints(self, angles_deg: List[float], speed_pct: float = 20.0) -> bool:
        """Execute a joint move requested by the XR teleop trigger.

        This is the same UR ``movej`` path used by the manual arm panel, but
        it is allowed while teleop is active because the trigger is the
        teleop equivalent of the manual execute button.
        """
        speed_pct = min(float(speed_pct), self.MAX_SPEED_PCT)
        return self._move_joints(angles_deg, speed_pct=speed_pct, teleop=True)

    def _move_joints(self, angles_deg: List[float], speed_pct: float,
                     teleop: bool) -> bool:
        if not teleop and getattr(self, '_teleop_active', False):
            log.warning("[Arm] Teleop active — rejecting move_joints")
            return False
        rads = [math.radians(a) for a in angles_deg]
        a_j, v_j, _, _ = self._speed_to_params(speed_pct)
        script = (
            "movej([{:.6f}, {:.6f}, {:.6f}, {:.6f}, {:.6f}, {:.6f}], "
            "a={:.3f}, v={:.3f})"
        ).format(rads[0], rads[1], rads[2], rads[3], rads[4], rads[5], a_j, v_j)
        ok = self._send_urscript(script)
        if ok:
            with self._lock:
                self._state.joint_angles_deg = list(angles_deg)
                self._state.is_moving = True
            log.info("[Arm] %smove_joints(deg=%s, speed=%.0f%%)",
                     "teleop " if teleop else "",
                     [f"{a:.1f}" for a in angles_deg], speed_pct)
            self._add_log("info", f"{'遥操作' if teleop else ''}关节移动 speed={speed_pct:.0f}%")
        return ok

    def move_cartesian(self, x: float, y: float, z: float,
                       roll: float = 0.0, pitch: float = 0.0, yaw: float = 0.0,
                       speed_pct: float = 20.0) -> bool:
        speed_pct = min(float(speed_pct), self.MAX_SPEED_PCT)
        if getattr(self, '_teleop_active', False):
            log.warning("[Arm] Teleop active — rejecting move_cartesian")
            return False
        x_m, y_m, z_m = x / 1000.0, y / 1000.0, z / 1000.0
        _, _, a_c, v_c = self._speed_to_params(speed_pct)
        script = (
            "movel(p[{:.4f}, {:.4f}, {:.4f}, {:.4f}, {:.4f}, {:.4f}], "
            "a={:.3f}, v={:.3f})"
        ).format(x_m, y_m, z_m, roll, pitch, yaw, a_c, v_c)
        ok = self._send_urscript(script)
        if ok:
            with self._lock:
                self._state.end_effector_xyz = [x, y, z]
                self._state.is_moving = True
            log.info("[Arm] move_cartesian(xyz=[%.1f,%.1f,%.1f])", x, y, z)
            self._add_log("info", f"直线移动 speed={speed_pct:.0f}%")
        return ok

    def set_home(self) -> bool:
        """Capture current actual joint angles as the new home target."""
        with self._lock:
            actual = list(self._state.joint_actual_deg)
        if not actual or all(a == 0.0 for a in actual):
            log.warning("[Arm] set_home() — no actual angles available, keeping current home")
            return False
        self._home_angles = actual
        log.info("[Arm] set_home() — new home: %s", [round(a, 2) for a in actual])
        self._add_log("info", f"新回零位: {[round(a, 1) for a in actual]}")
        return True

    def go_home(self) -> bool:
        self._reopen_ur_socket()
        return self.move_joints(list(self._home_angles), speed_pct=20.0)

    def stop_motion(self) -> None:
        self._dashboard_cmd("stop")
        self._send_urscript("stopj(2.0)")
        with self._lock:
            self._state.is_moving = False
        log.info("[Arm] stop()")
        self._add_log("warn", "急停")

    def send_urscript(self, script: str) -> bool:
        """Send a raw URScript command over the arm's primary (30001) socket.

        Used by the teleop module as a raw URScript fallback through this
        module's single connection instead of opening a competing socket on
        port 30001 (UR allows only one primary client). Manual move_* commands
        are already gated off while teleop is active via ``_teleop_active``.
        """
        return self._send_urscript(script)

    def emergency_stop(self) -> None:
        self.stop_motion()

    def reset_fault(self) -> None:
        r = self._dashboard_cmd("unlock protective stop")
        log.info("[Arm] reset_fault() — unlock: %s", r)
        self._add_log("info", f"解除保护性停止 — {r}")

    # ── state ─────────────────────────────────────────────────────────────

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            s = self._state
            s.timestamp = time.time()
            return {
                "joint_angles_deg": list(s.joint_angles_deg),
                "joint_actual_deg": list(s.joint_actual_deg),
                "joint_currents_a": list(s.joint_currents_a),
                "joint_temps_c": list(s.joint_temps_c),
                "joint_voltages_v": list(s.joint_voltages_v),
                "end_effector_xyz": list(s.end_effector_xyz),
                "home_angles_deg": list(self._home_angles),
                "speed_pct": self._speed_pct,
                "is_moving": s.is_moving,
                "is_fault": s.is_fault,
                "is_enabled": s.is_enabled,
                "connected": s.connected,
                "robot_mode": s.robot_mode,
                "safety_mode": s.safety_mode,
                "fault_text": s.fault_text,
                "collision_sensitivity": s.collision_sensitivity,
                "collision_warning": s.collision_warning,
                "logs": [{"ts": e.timestamp, "level": e.level, "msg": e.message}
                         for e in self._logs[-30:]],
                "timestamp": s.timestamp,
            }

    # ── SocketIO handlers ─────────────────────────────────────────────────

    def _on_enable(self, _data: dict = None, emit=None) -> None:
        self.enable()

    def _on_disable(self, _data: dict = None, emit=None) -> None:
        self.disable()

    def _on_reset_fault(self, _data: dict = None, emit=None) -> None:
        self.reset_fault()

    def _on_move_joints(self, data: dict, emit=None) -> None:
        self.move_joints(
            data.get("angles", [0.0] * 6),
            float(data.get("speed_pct", self._speed_pct)),
        )

    def _on_home(self, _data: dict = None, emit=None) -> None:
        teleop = self.robot.get("teleop") if self.robot else None
        if teleop is not None:
            try:
                teleop._on_disable(emit=emit)
            except Exception:
                pass
        self.go_home()

    def _reopen_ur_socket(self) -> None:
        """Close and reopen the URScript socket to clear queued commands."""
        with self._lock:
            if self._ur_sock:
                try:
                    self._ur_sock.close()
                except OSError:
                    pass
                self._ur_sock = None
        try:
            sock = socket.create_connection((self._host, self._ur_port), timeout=3.0)
            sock.settimeout(1.0)
            with self._lock:
                self._ur_sock = sock
            log.info("[Arm] URScript socket reopened")
        except OSError as e:
            log.warning("[Arm] URScript reopen failed: %s", e)

    def _on_set_home(self, _data: dict = None, emit=None) -> None:
        self.set_home()

    def _on_stop(self, _data: dict = None, emit=None) -> None:
        self.stop_motion()

    def _on_set_speed(self, data: dict = None, emit=None) -> None:
        speed_pct = float((data or {}).get("speed_pct", self._speed_pct))
        self._speed_pct = min(self.MAX_SPEED_PCT, max(1.0, speed_pct))
        log.info("[Arm] Speed set to %.0f%%", self._speed_pct)

    def _on_set_collision_sensitivity(self, data: dict, emit=None) -> None:
        level = float(data.get("level", 0.3))
        self.set_collision_sensitivity(level)

    # ── collision detection ────────────────────────────────────────────────

    def set_collision_sensitivity(self, level: float) -> None:
        level = max(0.05, min(1.0, level))
        with self._lock:
            self._state.collision_sensitivity = level
        self._collision_enabled = level > 0.01
        self._add_log("info", f"碰撞检测灵敏度: {level:.2f}")
        # Send URScript command to enable/disable hardware collision detection
        if self._collision_enabled:
            self._send_urscript("set_collision_detection(True)")
        else:
            self._send_urscript("set_collision_detection(False)")

    # ── Secondary Client state reader (port 30002) ──────────────────────────
    # UR5e RobotState message format (PolyScope 5.x):
    #   Outer: [4B BE int32 total_len] [1B type] [sub-packages...]
    #   Sub-pkg: [4B BE int32 len (includes self+type)] [1B type] [data]
    # JOINT_DATA (type 1): 251 bytes, six joints of dddffffB each
    JOINT_FMT = "!iB dddffffB dddffffB dddffffB dddffffB dddffffB dddffffB"
    JOINT_FMT_SIZE = 251

    # ROBOT_MODE_DATA (type 0): 38 or 46 bytes depending on firmware
    RM_FMT_V3 = "!iBQ???????BBdd"  # 38 bytes

    def _state_reader_loop(self):
        """Connect to port 30002 and parse RobotState sub-packages."""
        while self._running:
            sock = None
            try:
                sock = socket.create_connection((self._host, self._state_port), timeout=3.0)
                sock.settimeout(2.0)
                with self._lock:
                    self._state_sock = sock
                log.info("[Arm] State reader connected to port %d", self._state_port)
                self._sec_read_loop(sock)
            except (OSError, ConnectionRefusedError) as e:
                log.debug("[Arm] State reader connection failed: %s", e)
            finally:
                with self._lock:
                    self._state_sock = None
                if sock:
                    try:
                        sock.close()
                    except OSError:
                        pass
            if self._running:
                time.sleep(self.RECONNECT_INTERVAL)

    def _sec_read_loop(self, sock: socket.socket):
        """Read RobotState messages from port 30002 and parse sub-packages."""
        buf = b""
        while self._running:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                buf = self._sec_parse(buf)
            except socket.timeout:
                pass
            except OSError:
                break

    def _sec_parse(self, buf: bytes) -> bytes:
        """Extract and parse RobotState (type 16) messages. Returns leftover buf."""
        MIN_HDR = 5
        while len(buf) >= MIN_HDR:
            pkg_len = struct.unpack("!i", buf[0:4])[0]
            if pkg_len < MIN_HDR or pkg_len > 2000:
                buf = buf[1:]  # lost sync
                continue
            if len(buf) < pkg_len:
                break  # incomplete
            pkg_type = buf[4]
            body = buf[5:pkg_len]
            buf = buf[pkg_len:]
            if pkg_type == 16:
                self._parse_robot_state_body(body)
        return buf

    def _parse_robot_state_body(self, body: bytes):
        """Parse sub-packages from a RobotState message body."""
        off = 0
        while off + 5 <= len(body):
            sub_len = struct.unpack("!i", body[off:off+4])[0]
            if sub_len < 5 or sub_len > 1500 or off + sub_len > len(body):
                break
            sub_type = body[off + 4]
            sub_data = body[off + 5:off + sub_len]

            if sub_type == 1:  # JOINT_DATA
                self._parse_joint_data(body[off:off + sub_len])
            elif sub_type == 0:  # ROBOT_MODE_DATA
                self._parse_robot_mode_data(body[off:off + sub_len])

            off += sub_len

    def _parse_joint_data(self, raw: bytes):
        """Parse JOINT_DATA sub-package using the correct mixed double/float format."""
        if len(raw) < self.JOINT_FMT_SIZE:
            return
        try:
            vals = struct.unpack(self.JOINT_FMT, raw[:self.JOINT_FMT_SIZE])
        except struct.error:
            return
        # vals layout: size, type, then 8 fields per joint x 6 joints = 48 more
        q_actual = [vals[2 + j * 8] for j in range(6)]       # doubles: rad
        I_actual = [vals[2 + j * 8 + 3] for j in range(6)]   # floats: A
        V_actual = [vals[2 + j * 8 + 4] for j in range(6)]   # floats: V
        T_motor = [vals[2 + j * 8 + 5] for j in range(6)]    # floats: C
        joint_modes = [vals[2 + j * 8 + 7] for j in range(6)]  # uint8

        actual = [math.degrees(v) for v in q_actual]
        with self._lock:
            self._state.joint_actual_deg = actual
            self._state.joint_currents_a = list(I_actual)
            self._state.joint_temps_c = list(T_motor)
            self._state.joint_voltages_v = list(V_actual)
            if not self._joints_initialized:
                self._state.joint_angles_deg = list(actual)
                self._joints_initialized = True
            self._state.is_moving = any(abs(joint_modes[j]) > 1 for j in range(6))
            self._check_collision()

    def _parse_robot_mode_data(self, raw: bytes):
        """Parse ROBOT_MODE_DATA sub-package for robot/safety mode."""
        if len(raw) < 38:
            return
        try:
            vals = struct.unpack(self.RM_FMT_V3, raw[:38])
        except struct.error:
            return
        # vals: size, type, timestamp, isConnected, isEnabled, isPowerOn,
        #       isEmergencyStopped, isSecurityStopped, isProgramRunning,
        #       isProgramPaused, robotMode, controlMode, speedFraction
        robot_mode_int = vals[10]
        is_enabled = vals[4]      # isRealRobotEnabled
        is_estopped = vals[6]     # isEmergencyStopped
        is_sec_stopped = vals[7]  # isSecurityStopped
        is_power_on = vals[5]     # isPowerOnRobot

        mode_names = {
            -1: "NO_CONTROLLER", 0: "DISCONNECTED", 1: "CONFIRM_SAFETY",
            2: "BOOTING", 3: "POWER_OFF", 4: "POWER_ON", 5: "IDLE",
            6: "BACKDRIVE", 7: "RUNNING", 8: "UPDATING_FIRMWARE",
        }
        robot_mode = mode_names.get(robot_mode_int, f"MODE_{robot_mode_int}")

        with self._lock:
            self._state.robot_mode = robot_mode
            self._state.is_enabled = bool(is_enabled) and bool(is_power_on)
            self._state.is_fault = bool(is_estopped) or bool(is_sec_stopped)
            if self._state.is_fault:
                if is_estopped:
                    self._state.fault_text = "急停"
                elif is_sec_stopped:
                    self._state.fault_text = "安全停止"
            elif robot_mode == "POWER_OFF":
                self._state.is_moving = False
                self._state.fault_text = ""
            else:
                self._state.fault_text = ""

    def _check_collision(self):
        """Software-level collision detection based on current monitoring.

        If actual current exceeds a conservative threshold relative to typical
        idle values, flag a collision warning.  The threshold scales with the
        user-adjustable sensitivity slider.
        """
        if not self._collision_enabled:
            with self._lock:
                self._state.collision_warning = ""
            return
        currents = self._state.joint_currents_a
        temps = self._state.joint_temps_c
        sensitivity = self._state.collision_sensitivity
        # Conservative per-joint current thresholds (A) — lower sensitivity = higher threshold
        base_thresholds = [8.0, 12.0, 8.0, 4.0, 2.0, 2.0]
        # sensitivity 1.0 → threshold halved (very sensitive); 0.1 → threshold doubled (lenient)
        scale = 1.5 - sensitivity * 0.5  # 1.0 at sens=1.0, 1.45 at sens=0.1
        warning = ""
        for i in range(6):
            limit = base_thresholds[i] / scale
            if abs(currents[i]) > limit:
                warning = f"J{i+1} 电流异常: {currents[i]:.2f}A (阈值 {limit:.1f}A)"
        # Temperature check
        for i in range(6):
            if temps[i] > 75.0:
                if warning:
                    warning += "; "
                warning += f"J{i+1} 温度过高: {temps[i]:.0f}°C"
        with self._lock:
            self._state.collision_warning = warning
            if warning:
                log.warning("[Arm] Collision warning: %s", warning)

    # ── internals ──────────────────────────────────────────────────────────

    def _speed_to_params(self, speed_pct: float):
        speed_pct = min(float(speed_pct), self.MAX_SPEED_PCT)
        factor = max(0.01, min(1.0, speed_pct / 100.0))
        return (factor * 2.5, factor * 2.0, factor * 1.5, factor * 0.5)

    def _dashboard_cmd(self, cmd: str, timeout: float = 3.0) -> Optional[str]:
        sock = None
        try:
            sock = socket.create_connection((self._host, self._dash_port), timeout=timeout)
            sock.settimeout(timeout)
            sock.sendall((cmd + "\n").encode())
            buf = b""
            while b"\n" not in buf:
                chunk = sock.recv(256)
                if not chunk:
                    break
                buf += chunk
            return buf.decode(errors="replace").strip()
        except OSError as e:
            log.debug("[Arm] Dashboard cmd '%s' failed: %s", cmd, e)
            return None
        finally:
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass

    def _send_urscript(self, script: str) -> bool:
        with self._lock:
            sock = self._ur_sock
        if not sock:
            log.warning("[Arm] URScript socket not connected")
            return False
        try:
            sock.sendall((script + "\n").encode())
            return True
        except OSError as e:
            log.warning("[Arm] URScript send failed: %s", e)
            with self._lock:
                self._ur_sock = None
            return False

    def _connection_loop(self):
        while self._running:
            dash_sock = None
            ur_sock = None
            try:
                dash_sock = socket.create_connection((self._host, self._dash_port), timeout=3.0)
                dash_sock.settimeout(1.0)
                ur_sock = socket.create_connection((self._host, self._ur_port), timeout=3.0)
                ur_sock.settimeout(1.0)
                with self._lock:
                    self._dash_sock = dash_sock
                    self._ur_sock = ur_sock
                    self._state.connected = True
                log.info("[Arm] Connected to UR5e (%s)", self._host)
                self._add_log("info", f"已连接 ({self._host})")
                # Set payload so UR5 dynamics model accounts for end-effector mass
                m, cx, cy, cz = self._payload_mass, *self._payload_cog
                self._send_urscript(
                    f"set_payload({m:.3f}, [{cx:.4f}, {cy:.4f}, {cz:.4f}])")
                log.info("[Arm] set_payload(%.3f kg, CoG=[%.3f, %.3f, %.3f])",
                         m, cx, cy, cz)
                self._send_urscript("set_collision_detection(True)")
                self._poll_loop(dash_sock)
            except (OSError, ConnectionRefusedError) as e:
                log.debug("[Arm] Connection failed: %s", e)
            finally:
                with self._lock:
                    self._state.connected = False
                    self._dash_sock = None
                    self._ur_sock = None
                    self._joints_initialized = False
                for s in (dash_sock, ur_sock):
                    if s:
                        try:
                            s.close()
                        except OSError:
                            pass
            if self._running:
                time.sleep(self.RECONNECT_INTERVAL)

    def _poll_loop(self, sock):
        buf = b""
        last_poll = 0.0
        while self._running:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self._handle_dashboard_line(line.decode(errors="replace").strip())
            except socket.timeout:
                pass
            except OSError:
                break
            now = time.time()
            if now - last_poll >= self.POLL_INTERVAL:
                last_poll = now
                try:
                    with self._lock:
                        if self._dash_sock:
                            self._dash_sock.sendall(b"robotmode\nsafetymode\n")
                except OSError:
                    break

    def _handle_dashboard_line(self, line: str):
        if not line:
            return
        with self._lock:
            s = self._state
            if line.startswith("Robotmode:"):
                mode = line.split(":", 1)[1].strip()
                s.robot_mode = mode
                s.is_enabled = mode in ("RUNNING", "IDLE")
                if mode in ("RUNNING", "IDLE") and s.safety_mode == "NORMAL":
                    s.is_fault = False
                    s.fault_text = ""
                elif mode == "POWER_OFF":
                    s.is_moving = False
            elif line.startswith("Safetymode:"):
                mode = line.split(":", 1)[1].strip()
                s.safety_mode = mode
                if mode != "NORMAL":
                    s.is_fault = True
                    s.is_enabled = False
                    s.is_moving = False
                    s.fault_text = f"安全模式: {mode}"
                else:
                    if s.robot_mode in ("RUNNING", "IDLE"):
                        s.is_fault = False
                        s.fault_text = ""
            s.timestamp = time.time()

    def _add_log(self, level: str, msg: str):
        entry = ArmLogEntry(timestamp=time.time(), level=level, message=msg)
        self._logs.append(entry)
        if len(self._logs) > self.LOG_CAPACITY:
            self._logs = self._logs[-self.LOG_CAPACITY:]
