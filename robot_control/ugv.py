"""UGV chassis controller — wraps ugv_sdk via TCP bridge.

Architecture:
  CAN bus ↔ ugv_bridge (C++, links ugv_sdk) ↔ TCP :9100 ↔ UGVController (this module)

The bridge is a standalone C++ executable (ugv_sdk/sample/ugv_bridge/) that must be
started before robot_control:

    cd ugv_sdk/build && make ugv_bridge && ./bin/ugv_bridge can0

This module connects to it and exposes a high-level Python interface matching
what robot.py expects. Falls back to stub mode when the bridge is not reachable.
"""

import json
import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional

log = logging.getLogger(__name__)

# ── data types ────────────────────────────────────────────────────────────────

@dataclass
class UGVLogEntry:
    timestamp: float = 0.0
    level: str = "info"     # "info" | "warn" | "error"
    message: str = ""

@dataclass
class ActuatorHS:
    rpm: float = 0.0
    current: float = 0.0
    pulse_count: int = 0

@dataclass
class ActuatorLS:
    driver_voltage: float = 0.0
    driver_temp: float = 0.0
    motor_temp: float = 0.0
    driver_state: int = 0

@dataclass
class UGVState:
    # motion
    linear_velocity: float = 0.0     # m/s, forward positive
    angular_velocity: float = 0.0    # rad/s, CCW positive
    lateral_velocity: float = 0.0    # m/s
    steering_angle: float = 0.0      # rad

    # system
    battery_voltage: float = 0.0     # V
    vehicle_state: int = 0           # 0=normal, 1=estop, 2=exception
    control_mode: int = 0            # 0=standby, 1=CAN, 2=UART, 3=RC
    error_code: int = 0
    fault_code: int = 0
    motion_mode: int = 0             # 0=dual_ackerman, 1=parallel, 2=spinning, 3=park, 4=side_slip
    mode_changing: int = 0

    # RC
    rc_connected: bool = False
    rc_swa: int = 0
    rc_swb: int = 0
    rc_swc: int = 0
    rc_swd: int = 0
    rc_stick_right_v: int = 0
    rc_stick_right_h: int = 0
    rc_stick_left_v: int = 0
    rc_stick_left_h: int = 0

    # odometry
    odom_left: float = 0.0
    odom_right: float = 0.0

    # motor speeds (RPM)
    motor_speeds: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])

    # motor angles (rad)
    motor_angles: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])

    # actuator state (8 motors)
    actuator_hs: List[ActuatorHS] = field(default_factory=lambda: [ActuatorHS() for _ in range(8)])
    actuator_ls: List[ActuatorLS] = field(default_factory=lambda: [ActuatorLS() for _ in range(8)])

    # BMS
    bms_soc: int = 0
    bms_soh: int = 0
    bms_voltage: float = 0.0
    bms_current: float = 0.0
    bms_temperature: float = 0.0
    bms_alarm1: int = 0
    bms_alarm2: int = 0
    bms_warn1: int = 0
    bms_warn2: int = 0

    # meta
    connected: bool = False
    timestamp: float = field(default_factory=time.time)
    logs: list = field(default_factory=list)  # last N UGVLogEntry dicts


# ── controller ───────────────────────────────────────────────────────────────

class UGVController:
    """
    High-level interface for the AgileX UGV chassis.

    Communicates with ugv_bridge (C++ process wrapping ugv_sdk) over a local
    TCP socket. Falls back to stub mode when the bridge is not reachable so the
    rest of the system can still run.
    """

    BRIDGE_HOST = "127.0.0.1"
    BRIDGE_PORT = 9100
    RECONNECT_INTERVAL = 3.0
    STATE_TIMEOUT = 0.5
    LOG_CAPACITY = 80

    def __init__(self, host: str = BRIDGE_HOST, port: int = BRIDGE_PORT):
        self._host = host
        self._port = port
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()
        self._state = UGVState()
        self._running = False
        self._rx_thread: Optional[threading.Thread] = None
        self._logs: list[UGVLogEntry] = []
        self._last_vehicle_state = 0
        self._last_control_mode = 0

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> "UGVController":
        self._running = True
        self._rx_thread = threading.Thread(
            target=self._connection_loop, daemon=True, name="ugv-rx"
        )
        self._rx_thread.start()
        log.info("[UGV] Controller started (bridge %s:%d)", self._host, self._port)
        return self

    def stop(self) -> None:
        self._running = False
        self.set_velocity(0.0, 0.0)
        time.sleep(0.1)
        self._close_socket()
        log.info("[UGV] Controller stopped")

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.stop()

    # ── commands ──────────────────────────────────────────────────────────────

    def set_velocity(self, linear: float, angular: float) -> None:
        """Send velocity command. linear: m/s, angular: rad/s."""
        cmd = {"cmd": "velocity", "linear": round(linear, 4), "angular": round(angular, 4)}
        self._add_log("info", f"速度指令: linear={linear:.2f} m/s, angular={angular:.2f} rad/s")
        self._send(cmd)

    def stop_motion(self) -> None:
        self.set_velocity(0.0, 0.0)

    def emergency_stop(self) -> None:
        self._add_log("error", "紧急停止")
        self._send({"cmd": "estop"})
        log.warning("[UGV] Emergency stop sent")

    def enable(self) -> None:
        """Re-enable commanded mode (after estop or mode switch)."""
        self._add_log("info", "尝试切换到 CAN 指令模式...")
        self._send({"cmd": "enable"})

    def reset(self) -> None:
        """Reset robot state (clear faults)."""
        self._add_log("info", "重置底盘状态 (清除故障)...")
        self._send({"cmd": "reset"})

    # ── state ─────────────────────────────────────────────────────────────────

    def get_state(self) -> UGVState:
        with self._lock:
            s = UGVState(**self._state.__dict__)
            s.logs = [{
                "ts": e.timestamp,
                "level": e.level,
                "msg": e.message,
            } for e in self._logs[-30:]]
            return s

    def is_connected(self) -> bool:
        with self._lock:
            return self._state.connected

    # ── internal ──────────────────────────────────────────────────────────────

    def _send(self, payload: dict) -> None:
        data = (json.dumps(payload) + "\n").encode()
        with self._lock:
            if self._sock is None:
                log.debug("[UGV] stub cmd: %s", payload)
                return
            try:
                self._sock.sendall(data)
            except OSError as e:
                log.warning("[UGV] Send failed: %s", e)
                self._sock = None
                self._state.connected = False

    def _connection_loop(self) -> None:
        while self._running:
            try:
                sock = socket.create_connection((self._host, self._port), timeout=2.0)
                sock.settimeout(1.0)
                with self._lock:
                    self._sock = sock
                    self._state.connected = True
                log.info("[UGV] Bridge connected")
                self._add_log("info", "Bridge 已连接 (127.0.0.1:9100)")
                self._recv_loop(sock)
            except (OSError, ConnectionRefusedError) as e:
                log.debug("[UGV] Bridge not available: %s", e)
                was_connected = self._state.connected
                with self._lock:
                    self._sock = None
                    self._state.connected = False
                if was_connected:
                    self._add_log("warn", "Bridge 连接断开")
            time.sleep(self.RECONNECT_INTERVAL)

    def _recv_loop(self, sock: socket.socket) -> None:
        buf = b""
        while self._running:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self._handle_message(line.decode())
            except socket.timeout:
                continue
            except OSError:
                break
        with self._lock:
            self._state.connected = False

    def _handle_message(self, line: str) -> None:
        try:
            msg = json.loads(line)
            with self._lock:
                s = self._state
                # motion
                for key in ("linear_velocity", "angular_velocity",
                            "lateral_velocity", "steering_angle"):
                    if key in msg:
                        setattr(s, key, msg[key])
                # system
                for key in ("battery_voltage", "vehicle_state", "control_mode",
                            "error_code", "fault_code", "motion_mode", "mode_changing"):
                    if key in msg:
                        setattr(s, key, msg[key])
                # RC
                if "rc_connected" in msg:
                    s.rc_connected = bool(msg["rc_connected"])
                for key in ("rc_swa", "rc_swb", "rc_swc", "rc_swd",
                            "rc_stick_right_v", "rc_stick_right_h",
                            "rc_stick_left_v", "rc_stick_left_h"):
                    if key in msg:
                        setattr(s, key, msg[key])
                # odometry
                for key in ("odom_left", "odom_right"):
                    if key in msg:
                        setattr(s, key, msg[key])
                # motor arrays
                if "motor_speeds" in msg:
                    s.motor_speeds = [float(v) for v in msg["motor_speeds"]]
                if "motor_angles" in msg:
                    s.motor_angles = [float(v) for v in msg["motor_angles"]]
                # actuator HS
                if "actuator_hs" in msg:
                    for i, item in enumerate(msg["actuator_hs"][:8]):
                        s.actuator_hs[i] = ActuatorHS(
                            rpm=float(item.get("rpm", 0)),
                            current=float(item.get("current", 0)),
                            pulse_count=int(item.get("pulse_count", 0)),
                        )
                # actuator LS
                if "actuator_ls" in msg:
                    for i, item in enumerate(msg["actuator_ls"][:8]):
                        s.actuator_ls[i] = ActuatorLS(
                            driver_voltage=float(item.get("driver_voltage", 0)),
                            driver_temp=float(item.get("driver_temp", 0)),
                            motor_temp=float(item.get("motor_temp", 0)),
                            driver_state=int(item.get("driver_state", 0)),
                        )
                # BMS
                for key in ("bms_soc", "bms_soh", "bms_voltage", "bms_current",
                            "bms_temperature", "bms_alarm1", "bms_alarm2",
                            "bms_warn1", "bms_warn2"):
                    if key in msg:
                        setattr(s, key, msg[key])
                # connection flag (as reported by bridge)
                if "connected" in msg:
                    s.connected = bool(msg["connected"])
                s.timestamp = time.time()
                # log state transitions
                vs = s.vehicle_state
                cm = s.control_mode
                if vs != self._last_vehicle_state:
                    labels = {0: "正常", 1: "急停", 2: "异常"}
                    self._add_log("warn" if vs != 0 else "info",
                                  f"车辆状态: {labels.get(vs, str(vs))} (code={vs})")
                    self._last_vehicle_state = vs
                if cm != self._last_control_mode:
                    labels = {0: "待机", 1: "CAN指令", 2: "UART", 3: "遥控器"}
                    self._add_log("warn" if cm == 0 else "info",
                                  f"控制模式: {labels.get(cm, str(cm))} (code={cm})")
                    self._last_control_mode = cm
        except json.JSONDecodeError:
            log.warning("[UGV] Bad message: %r", line)

    def _close_socket(self) -> None:
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None

    def _add_log(self, level: str, message: str) -> None:
        entry = UGVLogEntry(timestamp=time.time(), level=level, message=message)
        with self._lock:
            self._logs.append(entry)
            if len(self._logs) > self.LOG_CAPACITY:
                self._logs = self._logs[-self.LOG_CAPACITY:]
