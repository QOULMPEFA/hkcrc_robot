"""XRoboToolkit TCP binary protocol receiver for Quest 3 tracking data.

Parses the binary packet format used by the XRoboToolkit-Quest Unity app
to stream headset + controller + hand tracking data over TCP.

Protocol (from client_quest3 PackageHandle.cs).  NOTE the HEAD byte is
directional — the Quest sends with SEND_PACKET_HEAD (0x3F) and expects to
receive RECEIVE_PACKET_HEAD (0xCF):
  Quest→PC: [HEAD:1B=0x3F][CMD:1B][PARAM_LEN:4B int32 LE][PARAMS:N][TS:8B int64 LE][END:1B=0xA5]
  PC→Quest: [HEAD:1B=0xCF][CMD:1B][...same...]

Tracking data arrives as CMD=0x6D (PACKET_CCMD_TO_CONTROLLER_FUNCTION)
with PARAMS containing JSON: {"functionName":"Tracking","value":"<tracking_json>"}

Usage:
  receiver = XRProtocolReceiver(port=63901)
  receiver.start()
  head_pose = receiver.get_headset_pose()  # [x,y,z,qx,qy,qz,qw] or None
  ctrl_pose = receiver.get_controller_pose("right")
  trigger = receiver.get_trigger("right")
"""

import json
import logging
import select
import socket
import struct
import threading
import time
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

# HEAD byte the Quest app puts on the wire when sending to us
# (PackageHandle.Pack → SEND_PACKET_HEAD).  Parser accepts both 0x3F and 0xCF
# so a firmware/APK that uses the other convention still parses.
HEAD = 0x3F
HEAD_ALT = 0xCF
END = 0xA5
CMD_TRACKING = 0x6D
CMD_VERSION = 0x6C
CMD_HEARTBEAT = 0x23
CMD_CONNECT = 0x19
CMD_FROM_CONTROLLER = 0x5F  # commands FROM PC TO headset (legacy)
CMD_TO_CONTROLLER = 0x6D    # PACKET_CCMD_TO_CONTROLLER_FUNCTION (tracking + custom functions)

HEADER_SIZE = 15   # 1(HEAD) + 1(CMD) + 4(PARAM_LEN) + 8(TIMESTAMP) + 1(END)
READ_TIMEOUT = 2.0  # seconds for recv()

# Packet HEAD byte we send to the Quest — it requires RECEIVE_PACKET_HEAD (0xCF)
# in PackageHandle.Unpack (mirroring NetCMD.cs).
SEND_PACKET_HEAD = 0xCF
SEND_PACKET_END = 0xA5


class XRProtocolReceiver:
    """TCP server that accepts one Quest client and parses tracking data.

    Thread-safe: all get_*() methods can be called from any thread.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 63901):
        self._host = host
        self._port = port
        self._lock = threading.RLock()
        self._running = False
        self._server_sock: Optional[socket.socket] = None
        self._client_sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None

        # Latest parsed tracking state
        self._head_pose: Optional[List[float]] = None
        self._controller_poses: Dict[str, List[float]] = {}
        self._controller_buttons: Dict[str, Dict[str, Any]] = {}
        self._last_update = 0.0
        self._frame_count = 0
        self._connected = False

        # Command callback: called when a non-Tracking function message arrives
        # Signature: callback(function_name: str, value: dict) -> None
        self._command_callback: Optional[Callable[[str, dict], None]] = None

        # Outgoing message queue (for sending functions back to the Quest)
        self._send_queue: List[bytes] = []
        self._status_thread: Optional[threading.Thread] = None

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start TCP server in background thread."""
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True, name="xr-protocol")
        self._thread.start()
        log.info("[XRProtocol] Server started on %s:%d", self._host, self._port)

    def stop(self) -> None:
        """Stop server and close connections."""
        self._running = False
        with self._lock:
            if self._client_sock:
                try:
                    self._client_sock.close()
                except Exception:
                    pass
                self._client_sock = None
            if self._server_sock:
                try:
                    self._server_sock.close()
                except Exception:
                    pass
                self._server_sock = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._connected = False
        log.info("[XRProtocol] Server stopped")

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def frame_count(self) -> int:
        return self._frame_count

    def set_command_callback(self, callback: Optional[Callable[[str, dict], None]]) -> None:
        """Register a callback for non-Tracking function messages from the Quest.

        Called with (function_name: str, value: dict) when the Quest sends
        a function message that is not "Tracking".
        """
        self._command_callback = callback

    def send_function(self, function_name: str, value: str) -> bool:
        """Send a function message to the connected Quest client.

        Returns True if queued for sending, False if no client connected.
        """
        with self._lock:
            if not self._client_sock or not self._connected:
                return False
            msg = json.dumps({"functionName": function_name, "value": value})
            self._send_queue.append(msg.encode("utf-8"))
            return True

    def send_ur5e_status(self, status: Dict[str, Any]) -> bool:
        """Send UR5e robot status to the Quest client."""
        return self.send_function("UR5eStatus", json.dumps(status))

    @staticmethod
    def _pack_message(cmd: int, payload: bytes) -> bytes:
        """Pack a message in the binary protocol format for sending.

        Uses SEND_PACKET_HEAD (0x3F) as expected by TcpHandler.cs Unpack().
        """
        param_len = len(payload)
        timestamp = int(time.time() * 1000)
        data = bytearray(15 + param_len)
        data[0] = SEND_PACKET_HEAD
        data[1] = cmd
        struct.pack_into("<i", data, 2, param_len)
        data[6:6 + param_len] = payload
        struct.pack_into("<q", data, 6 + param_len, timestamp)
        data[14 + param_len] = SEND_PACKET_END
        return bytes(data)

    # ── server loop ──────────────────────────────────────────────────────

    def _serve(self) -> None:
        """Accept one client and read/parse packets in a loop."""
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.settimeout(1.0)
        try:
            self._server_sock.bind((self._host, self._port))
            self._server_sock.listen(1)
        except OSError as e:
            log.error("[XRProtocol] Failed to bind %s:%d: %s", self._host, self._port, e)
            return

        log.info("[XRProtocol] Listening on %s:%d", self._host, self._port)

        while self._running:
            try:
                client, addr = self._server_sock.accept()
                log.info("[XRProtocol] Client connected from %s", addr)
                client.settimeout(0.5)
                with self._lock:
                    if self._client_sock:
                        try:
                            self._client_sock.close()
                        except Exception:
                            pass
                    self._client_sock = client
                    self._connected = True

                self._read_loop(client)

            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    log.debug("[XRProtocol] Accept error: %s", e)
                time.sleep(0.5)

        self._connected = False
        self._client_sock = None

    def _read_loop(self, sock: socket.socket) -> None:
        """Read and parse packets from the connected client."""
        buf = b""
        last_data = time.time()

        while self._running:
            ready, _, _ = select.select([sock], [], [], 0.5)
            if not ready:
                # Check for timeout (no data for 15s = disconnected)
                if time.time() - last_data > 15.0:
                    log.warning("[XRProtocol] Client timeout")
                    break
                self._flush_send_queue(sock)
                continue

            try:
                chunk = sock.recv(4096)
                if not chunk:
                    log.info("[XRProtocol] Client disconnected")
                    break
                buf += chunk
                last_data = time.time()
            except socket.timeout:
                self._flush_send_queue(sock)
                continue
            except Exception as e:
                log.debug("[XRProtocol] Recv error: %s", e)
                break

            # Parse all complete packets in buffer
            buf = self._parse_buffer(buf)

            # Flush outgoing messages
            self._flush_send_queue(sock)

        with self._lock:
            self._connected = False
            self._client_sock = None

    def _flush_send_queue(self, sock: socket.socket) -> None:
        """Send any queued outgoing messages."""
        with self._lock:
            if not self._send_queue:
                return
            queue = self._send_queue[:]
            self._send_queue.clear()

        for payload in queue:
            try:
                packet = self._pack_message(CMD_TO_CONTROLLER, payload)
                sock.sendall(packet)
            except Exception as e:
                log.debug("[XRProtocol] Send error: %s", e)
                with self._lock:
                    self._connected = False
                break

    @staticmethod
    def _next_head(buf: bytes, start: int) -> int:
        """Index of the next possible HEAD byte (0x3F or 0xCF) at/after start."""
        candidates = [i for i in (buf.find(HEAD, start), buf.find(HEAD_ALT, start)) if i >= 0]
        return min(candidates) if candidates else -1

    def _parse_buffer(self, buf: bytes) -> bytes:
        """Extract complete packets from buffer. Returns remaining bytes."""
        while len(buf) >= HEADER_SIZE:
            head = buf[0]
            if head not in (HEAD, HEAD_ALT):
                # Skip until the next possible HEAD byte
                idx = self._next_head(buf, 1)
                if idx < 0:
                    return b""
                buf = buf[idx:]
                continue

            cmd = buf[1]
            param_len = struct.unpack_from("<i", buf, 2)[0]

            total = HEADER_SIZE + param_len
            if len(buf) < total:
                return buf  # incomplete packet

            end = buf[total - 1]
            if end != END:
                # Corrupt — skip this HEAD byte
                idx = self._next_head(buf, 1)
                if idx < 0:
                    return b""
                buf = buf[idx:]
                continue

            # Extract and process the packet body
            params = buf[6:6 + param_len]
            self._dispatch(cmd, params)

            buf = buf[total:]

        return buf

    def _dispatch(self, cmd: int, payload: bytes) -> None:
        """Route a parsed packet by command byte."""
        if cmd == CMD_HEARTBEAT:
            return  # heartbeat — no action needed

        if cmd == CMD_TO_CONTROLLER:
            self._handle_to_controller(payload)
            return

        if cmd == CMD_CONNECT:
            log.info("[XRProtocol] Quest connected (CMD_CONNECT)")
            return

        if cmd == CMD_VERSION:
            try:
                log.info("[XRProtocol] Version: %s", payload.decode("utf-8", errors="replace"))
            except Exception:
                pass
            return

        log.debug("[XRProtocol] Unknown cmd: 0x%02X", cmd)

    def _handle_to_controller(self, payload: bytes) -> None:
        """Handle a PACKET_CCMD_TO_CONTROLLER_FUNCTION (0x6D) message.

        Routes Tracking data to the tracking parser, and all other
        functionName messages to the command callback.
        """
        try:
            text = payload.decode("utf-8", errors="replace")
            outer = json.loads(text)
        except json.JSONDecodeError:
            log.debug("[XRProtocol] Invalid function JSON")
            return

        function_name = outer.get("functionName", "")

        if function_name == "Tracking":
            try:
                data = json.loads(outer["value"])
            except (json.JSONDecodeError, KeyError):
                log.debug("[XRProtocol] Invalid tracking value JSON")
                return
            self._handle_tracking_data(data)
        elif self._command_callback is not None:
            try:
                value = json.loads(outer.get("value", "{}"))
            except json.JSONDecodeError:
                value = outer.get("value", "{}")
            try:
                self._command_callback(function_name, value)
            except Exception as e:
                log.error("[XRProtocol] Command callback error for '%s': %s", function_name, e)

    def _handle_tracking_data(self, data: dict) -> None:
        now = time.time()
        with self._lock:
            self._last_update = now
            self._frame_count += 1

            # Head pose
            head = data.get("Head", {})
            if head.get("status", 0) >= 3:
                pose_str = head.get("pose", "")
                head_pose = self._parse_pose_str(pose_str)
                if head_pose:
                    self._head_pose = head_pose

            # Controller data
            controllers = data.get("Controller", {})
            for side in ("left", "right"):
                ctrl = controllers.get(side, {})
                pose_str = ctrl.get("pose", "")
                pose = self._parse_pose_str(pose_str)
                if pose:
                    self._controller_poses[side] = pose

                self._controller_buttons[side] = {
                    "grip": float(ctrl.get("grip", 0.0)),
                    "trigger": float(ctrl.get("trigger", 0.0)),
                    "primaryButton": bool(ctrl.get("primaryButton", False)),
                    "secondaryButton": bool(ctrl.get("secondaryButton", False)),
                    "menuButton": bool(ctrl.get("menuButton", False)),
                    "axisX": float(ctrl.get("axisX", 0.0)),
                    "axisY": float(ctrl.get("axisY", 0.0)),
                    "axisClick": bool(ctrl.get("axisClick", False)),
                }

    @staticmethod
    def _parse_pose_str(pose_str: str) -> Optional[List[float]]:
        """Parse 'x,y,z,qx,qy,qz,qw' comma-separated string to float list."""
        if not pose_str:
            return None
        try:
            vals = [float(v.strip()) for v in pose_str.split(",")]
            if len(vals) == 7:
                return vals
        except (ValueError, AttributeError):
            pass
        return None

    # ── thread-safe accessors ────────────────────────────────────────────

    def get_headset_pose(self) -> Optional[List[float]]:
        """Return [x, y, z, qx, qy, qz, qw] or None."""
        with self._lock:
            return list(self._head_pose) if self._head_pose else None

    def get_controller_pose(self, side: str) -> Optional[List[float]]:
        """Return [x, y, z, qx, qy, qz, qw] or None."""
        with self._lock:
            pose = self._controller_poses.get(side)
            return list(pose) if pose else None

    def get_trigger(self, side: str) -> float:
        with self._lock:
            btns = self._controller_buttons.get(side, {})
            return btns.get("trigger", 0.0)

    def get_grip(self, side: str) -> float:
        with self._lock:
            btns = self._controller_buttons.get(side, {})
            return btns.get("grip", 0.0)

    def get_button(self, side: str, name: str) -> bool:
        """Get button state. name: 'primaryButton' (A/X), 'secondaryButton' (B/Y), 'menuButton', 'axisClick'."""
        with self._lock:
            btns = self._controller_buttons.get(side, {})
            return btns.get(name, False)

    def get_A_button(self, side: str = "right") -> bool:
        return self.get_button(side, "primaryButton")

    def get_B_button(self, side: str = "right") -> bool:
        return self.get_button(side, "secondaryButton")
