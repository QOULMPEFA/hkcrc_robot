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
    HOME_ANGLES_DEG = [0.0, -90.0, 0.0, -90.0, 0.0, 0.0]
    LOG_CAPACITY = 80
    RECONNECT_INTERVAL = 3.0
    POLL_INTERVAL = 1.0

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        self._host = cfg.get("host", "192.168.56.101")
        self._dash_port = int(cfg.get("dashboard_port", 29999))
        self._ur_port = int(cfg.get("urscript_port", 30001))
        self._state_port = int(cfg.get("state_port", 30004))
        self._state = ArmState()
        self._lock = threading.RLock()
        self._running = False
        self._dash_sock: Optional[socket.socket] = None
        self._ur_sock: Optional[socket.socket] = None
        self._state_sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._state_thread: Optional[threading.Thread] = None
        self._logs: list[ArmLogEntry] = []
        self._collision_enabled = True

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
            "arm_stop": self._on_stop,
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
            log.info("[Arm] move_joints(deg=%s, speed=%.0f%%)",
                     [f"{a:.1f}" for a in angles_deg], speed_pct)
            self._add_log("info", f"关节移动 speed={speed_pct:.0f}%")
        return ok

    def move_cartesian(self, x: float, y: float, z: float,
                       roll: float = 0.0, pitch: float = 0.0, yaw: float = 0.0,
                       speed_pct: float = 20.0) -> bool:
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

    def go_home(self) -> bool:
        return self.move_joints(list(self.HOME_ANGLES_DEG), speed_pct=30.0)

    def stop_motion(self) -> None:
        self._dashboard_cmd("stop")
        self._send_urscript("stopj(2.0)")
        with self._lock:
            self._state.is_moving = False
        log.info("[Arm] stop()")
        self._add_log("warn", "急停")

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
            float(data.get("speed_pct", 20.0)),
        )

    def _on_home(self, _data: dict = None, emit=None) -> None:
        self.go_home()

    def _on_stop(self, _data: dict = None, emit=None) -> None:
        self.stop_motion()

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

    # ── RTDE real-time state reader (port 30004) ───────────────────────────

    # RTDE output recipe — field order defines binary layout.
    # Each field is 1 or 6 big-endian doubles (8 bytes each).
    RTDE_RECIPE = [
        ("actual_q",              6),   # offset   0,  48 bytes
        ("actual_current",         6),   # offset  48,  48 bytes
        ("joint_temperatures",     6),   # offset  96,  48 bytes
        ("robot_mode",             1),   # offset 144,   8 bytes
        ("safety_mode",            1),   # offset 152,   8 bytes
    ]
    RTDE_PKG_DATA_LEN = 160  # total bytes from recipe above

    def _state_reader_loop(self):
        """Connect to RTDE port 30004 and stream real-time joint data."""
        while self._running:
            sock = None
            try:
                sock = socket.create_connection((self._host, self._state_port), timeout=3.0)
                sock.settimeout(2.0)
                with self._lock:
                    self._state_sock = sock
                log.info("[Arm] RTDE connected to port %d", self._state_port)
                self._rtde_handshake(sock)
                self._rtde_read_loop(sock)
            except (OSError, ConnectionRefusedError) as e:
                log.debug("[Arm] RTDE connection failed: %s", e)
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

    def _rtde_handshake(self, sock: socket.socket):
        """Perform RTDE protocol handshake: version → setup → start."""
        # In PolyScope ≥5.0 the client sends first (no welcome message).
        # Step 1: request protocol version
        sock.sendall(b"RTDE_REQUEST_PROTOCOL_VERSION 2\n")
        time.sleep(0.1)
        resp = self._rtde_recv_line(sock)
        log.info("[Arm] RTDE version: %s", resp)

        # Step 2: setup output recipe
        names = ",".join(f[0] for f in self.RTDE_RECIPE)
        recipe_id = 7
        cmd = f"RTDE_CONTROL_PACKAGE_SETUP_OUTPUTS {recipe_id} {names}\n"
        sock.sendall(cmd.encode())
        time.sleep(0.2)
        resp = self._rtde_recv_line(sock)
        log.info("[Arm] RTDE setup: %s", resp)
        if resp and "ACCEPTED" not in resp.upper():
            log.warning("[Arm] RTDE recipe rejected: %s — trying alt names", resp)
            # Fallback: try without temperatures
            alt_names = "actual_q,actual_current,robot_mode,safety_mode"
            cmd2 = f"RTDE_CONTROL_PACKAGE_SETUP_OUTPUTS {recipe_id} {alt_names}\n"
            sock.sendall(cmd2.encode())
            time.sleep(0.2)
            resp = self._rtde_recv_line(sock)
            log.info("[Arm] RTDE alt setup: %s", resp)

        # Step 3: start streaming
        sock.sendall(b"RTDE_CONTROL_PACKAGE_START\n")
        time.sleep(0.1)
        resp = self._rtde_recv_line(sock)
        log.info("[Arm] RTDE start: %s", resp)

    @staticmethod
    def _rtde_recv_line(sock: socket.socket) -> str:
        buf = b""
        try:
            while b"\n" not in buf:
                chunk = sock.recv(256)
                if not chunk:
                    break
                buf += chunk
        except socket.timeout:
            pass
        return buf.decode("ascii", errors="replace").strip()

    def _rtde_read_loop(self, sock: socket.socket):
        """Read and parse RTDE binary data packages."""
        buf = b""
        while self._running:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                # RTDE binary package: [2B size (BE uint16)] [1B type] [data]
                while len(buf) >= 3:
                    pkg_size = struct.unpack(">H", buf[0:2])[0]
                    if pkg_size < 3:
                        buf = buf[1:]  # lost sync
                        continue
                    if len(buf) < pkg_size:
                        break
                    pkg_type = buf[2]
                    data = buf[3:pkg_size]
                    buf = buf[pkg_size:]
                    if pkg_type == 0 and len(data) >= self.RTDE_PKG_DATA_LEN:
                        self._parse_rtde_data(data)
            except socket.timeout:
                pass
            except OSError:
                break

    def _parse_rtde_data(self, data: bytes):
        """Parse RTDE binary data using the known recipe layout (all BE doubles)."""
        try:
            actual_q_rad = struct.unpack(">6d", data[0:48])
            actual_current = struct.unpack(">6d", data[48:96])
            joint_temps = struct.unpack(">6d", data[96:144])
            robot_mode_raw = struct.unpack(">d", data[144:152])[0]
            safety_mode_raw = struct.unpack(">d", data[152:160])[0]
        except struct.error:
            return

        q_deg = [math.degrees(v) for v in actual_q_rad]

        # Map robot_mode integer to string
        mode_map = {
            0: "NO_CONTROLLER", 1: "DISCONNECTED", 2: "CONFIRM_SAFETY",
            3: "BOOTING", 4: "POWER_OFF", 5: "POWER_ON", 6: "IDLE",
            7: "BACKDRIVE", 8: "RUNNING", 9: "UPDATING_FIRMWARE",
        }
        safety_map = {
            0: "NORMAL", 1: "REDUCED", 2: "PROTECTIVE_STOP",
            3: "RECOVERY", 4: "SAFEGUARD_STOP", 5: "SYSTEM_EMERGENCY_STOP",
            6: "ROBOT_EMERGENCY_STOP", 7: "VIOLATION", 8: "FAULT",
            9: "VALIDATE_JOINT_ID", 10: "UNDEFINED_SAFETY_MODE",
        }

        with self._lock:
            self._state.joint_actual_deg = q_deg
            self._state.joint_currents_a = list(actual_current)
            self._state.joint_temps_c = list(joint_temps)
            self._state.robot_mode = mode_map.get(int(robot_mode_raw), f"MODE_{int(robot_mode_raw)}")
            self._state.safety_mode = safety_map.get(int(safety_mode_raw), f"SAFETY_{int(safety_mode_raw)}")
            self._state.is_enabled = int(robot_mode_raw) in (6, 8)  # IDLE, RUNNING
            self._check_collision()

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
                # Apply default collision sensitivity on connect
                self._send_urscript("set_collision_detection(True)")
                self._poll_loop(dash_sock)
            except (OSError, ConnectionRefusedError) as e:
                log.debug("[Arm] Connection failed: %s", e)
            finally:
                with self._lock:
                    self._state.connected = False
                    self._dash_sock = None
                    self._ur_sock = None
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
