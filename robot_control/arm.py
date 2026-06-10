"""UR5e robotic arm controller via TCP (Dashboard + URScript).

RobotModule wrapper around the UR Dashboard (port 29999) and URScript (port 30001)
interfaces.
"""

import logging
import math
import socket
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
    end_effector_xyz: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    is_moving: bool = False
    is_fault: bool = False
    is_enabled: bool = False
    connected: bool = False
    robot_mode: str = "UNKNOWN"
    safety_mode: str = "UNKNOWN"
    fault_text: str = ""
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
        self._state = ArmState()
        self._lock = threading.RLock()
        self._running = False
        self._dash_sock: Optional[socket.socket] = None
        self._ur_sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._logs: list[ArmLogEntry] = []

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
            "end_effector_xyz": [0.0, 0.0, 0.0],
            "is_moving": False, "is_fault": False, "is_enabled": False,
            "connected": False,
            "robot_mode": "UNKNOWN", "safety_mode": "UNKNOWN", "fault_text": "",
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
        }

    # ── lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._connection_loop, daemon=True, name="arm-conn")
        self._thread.start()
        log.info("[Arm] Connection loop started — host=%s", self._host)
        self._add_log("info", f"开始连接 {self._host}")

    def stop(self) -> None:
        self._running = False
        with self._lock:
            self._state.connected = False
            self._state.is_enabled = False
            self._state.is_fault = False
        for sock in (self._dash_sock, self._ur_sock):
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
        with self._lock:
            self._dash_sock = None
            self._ur_sock = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
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
                "end_effector_xyz": list(s.end_effector_xyz),
                "is_moving": s.is_moving,
                "is_fault": s.is_fault,
                "is_enabled": s.is_enabled,
                "connected": s.connected,
                "robot_mode": s.robot_mode,
                "safety_mode": s.safety_mode,
                "fault_text": s.fault_text,
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
