"""UGV chassis controller — wraps ugv_sdk via subprocess/socket bridge.

The ugv_sdk is a C++ library. This module communicates with it through a
thin JSON-over-TCP bridge process (ugv_bridge.py) that must be started
separately, or via direct python-can if a pure-Python path is available.

For now this module provides:
  - A UGVController class with the same interface the robot.py expects
  - A stub that logs commands when the bridge is not connected (safe fallback)
"""

import json
import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)

# ── data types ────────────────────────────────────────────────────────────────

@dataclass
class UGVState:
    linear_velocity: float = 0.0    # m/s, forward positive
    angular_velocity: float = 0.0   # rad/s, CCW positive
    battery_voltage: float = 0.0    # V
    fault_code: int = 0
    connected: bool = False
    timestamp: float = field(default_factory=time.time)


# ── controller ────────────────────────────────────────────────────────────────

class UGVController:
    """
    High-level interface for the AgileX UGV chassis.

    Communicates with ugv_bridge (a small C++/Python process that wraps
    ugv_sdk) over a local TCP socket. Falls back to stub mode when the
    bridge is not reachable so the rest of the system can still run.
    """

    BRIDGE_HOST = "127.0.0.1"
    BRIDGE_PORT = 9100
    RECONNECT_INTERVAL = 3.0    # seconds between reconnect attempts
    STATE_TIMEOUT = 0.5         # seconds before state is considered stale

    def __init__(self, host: str = BRIDGE_HOST, port: int = BRIDGE_PORT):
        self._host = host
        self._port = port
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()
        self._state = UGVState()
        self._running = False
        self._rx_thread: Optional[threading.Thread] = None

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
        """
        Send velocity command.
        linear:  m/s  (forward positive, typical range ±1.5 m/s)
        angular: rad/s (CCW positive, typical range ±1.0 rad/s)
        """
        cmd = {"cmd": "velocity", "linear": round(linear, 4), "angular": round(angular, 4)}
        self._send(cmd)

    def stop_motion(self) -> None:
        self.set_velocity(0.0, 0.0)

    def emergency_stop(self) -> None:
        cmd = {"cmd": "estop"}
        self._send(cmd)
        log.warning("[UGV] Emergency stop sent")

    def clear_fault(self) -> None:
        self._send({"cmd": "clear_fault"})

    # ── state ─────────────────────────────────────────────────────────────────

    def get_state(self) -> UGVState:
        with self._lock:
            return UGVState(**self._state.__dict__)

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
                self._recv_loop(sock)
            except (OSError, ConnectionRefusedError) as e:
                log.debug("[UGV] Bridge not available: %s", e)
                with self._lock:
                    self._sock = None
                    self._state.connected = False
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
                if "linear_velocity" in msg:
                    self._state.linear_velocity = msg["linear_velocity"]
                if "angular_velocity" in msg:
                    self._state.angular_velocity = msg["angular_velocity"]
                if "battery_voltage" in msg:
                    self._state.battery_voltage = msg["battery_voltage"]
                if "fault_code" in msg:
                    self._state.fault_code = msg["fault_code"]
                self._state.timestamp = time.time()
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
