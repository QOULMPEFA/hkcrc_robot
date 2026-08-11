"""Dexterous hand controller — BrainCo Revo1/Revo2 via bc_stark_sdk (Modbus/CAN).

Runs async SDK operations on a dedicated asyncio event loop in a background
thread.  Falls back to stub mode when the SDK is not installed or the hand is
unreachable.

SocketIO events: hand_open, hand_close, hand_pinch, hand_set_fingers
"""

import asyncio
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from robot_control.module_base import RobotModule

log = logging.getLogger(__name__)

# ── SDK import ────────────────────────────────────────────────────────────
try:
    from bc_stark_sdk import main_mod as _sdk
    _LIBSTARK = _sdk
    _SDK_AVAILABLE = True
except ImportError:
    _LIBSTARK = None
    _SDK_AVAILABLE = False

FINGER_NAMES = ["thumb", "thumb_aux", "index", "middle", "ring", "pinky"]
FINGER_DISPLAY = ["拇指", "拇指副", "食指", "中指", "无名指", "小指"]

FIST_FINGERS = [0, 0, 1000, 1000, 1000, 1000]
FIST_FINAL = [500, 500, 1000, 1000, 1000, 1000]
CUP_FINAL = [0, 0, 1000, 1000, 1000, 1000]
OPEN_POS = [0, 0, 0, 0, 0, 0]


def _int_to_baudrate(value: int):
    """Convert int baudrate to SDK Baudrate enum."""
    if _LIBSTARK is None:
        return None
    baudrate_map = {
        115200: _LIBSTARK.Baudrate.Baud115200,
        57600:  _LIBSTARK.Baudrate.Baud57600,
        19200:  _LIBSTARK.Baudrate.Baud19200,
        460800: _LIBSTARK.Baudrate.Baud460800,
        1000000: _LIBSTARK.Baudrate.Baud1Mbps,
        2000000: _LIBSTARK.Baudrate.Baud2Mbps,
        3000000: _LIBSTARK.Baudrate.Baud3Mbps,
        5000000: _LIBSTARK.Baudrate.Baud5Mbps,
    }
    return baudrate_map.get(value, _LIBSTARK.Baudrate.Baud460800)


class HandController(RobotModule):
    """BrainCo dexterous hand via bc_stark_sdk (Modbus serial or CAN)."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        # Protocol: "modbus" (serial) or "can" (SocketCAN 2.0)
        self._protocol = cfg.get("protocol", "modbus")
        # Serial/Modbus config
        self._port = cfg.get("port", None)          # None = auto-detect
        self._baudrate = int(cfg.get("baudrate", 115200))
        # CAN config (SocketCAN 2.0)
        self._can_iface = cfg.get("can_iface", "can0")
        self._master_id = int(cfg.get("master_id", 1))
        # candleLight USB-CAN FD config
        self._candlelight_channel = int(cfg.get("candlelight_channel", 0))
        self._candlelight_bitrate = int(cfg.get("candlelight_bitrate", 1_000_000))
        self._candlelight_data_bitrate = int(cfg.get("candlelight_data_bitrate", 5_000_000))
        # Shared
        self._slave_id = int(cfg.get("slave_id", 0x02))
        # SocketCAN function references (set during _connect_can)
        self._can_send_fn: Any = None
        self._can_recv_fn: Any = None
        self._can_close_fn: Any = None

        self._client: Optional[Any] = None
        self._connected = False
        self._enabled = False
        self._fault = False
        self._finger_positions: List[float] = [0.0] * 6
        self._finger_speeds: List[float] = [0.0] * 6
        self._finger_currents: List[float] = [0.0] * 6
        self._finger_limits: List[Dict[str, int]] = [{"min": 0, "max": 1000}] * 6

        self._lock = threading.Lock()
        self._loop_thread: Optional[threading.Thread] = None
        self._loop: Optional[Any] = None
        self._running = False

        # Command queue for thread-safe async submission.
        self._cmd_queue: Optional[Any] = None  # asyncio.Queue, set after loop init
        self._task: Optional[Any] = None
        self._state_count: int = 0  # periodic log counter

        log.info("[Hand] Controller created — protocol=%s port=%s",
                 self._protocol, self._port or "auto")

    # ── RobotModule metadata ─────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "hand"

    @property
    def display_name(self) -> str:
        return "灵巧手"

    def get_state_schema(self) -> Dict[str, Any]:
        return {
            "finger_positions": [0.0] * 6,
            "finger_speeds": [0.0] * 6,
            "finger_currents": [0.0] * 6,
            "finger_limits": [{"min": 0, "max": 1000}] * 6,
            "connected": False,
            "is_enabled": False,
            "is_fault": False,
        }

    def get_socketio_handlers(self) -> Dict[str, callable]:
        return {
            "hand_enable":       self._on_enable,
            "hand_disable":      self._on_disable,
            "hand_open":        self._on_open,
            "hand_close":       self._on_close,
            "hand_pinch":       self._on_pinch,
            "hand_grip":        self._on_grip,
            "hand_cup":         self._on_cup,
            "hand_set_fingers": self._on_set_fingers,
        }

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        if not _SDK_AVAILABLE:
            log.warning("[Hand] bc_stark_sdk not installed — running in stub mode")
            log.warning("[Hand] Install: pip install bc_stark_sdk")
            return
        self._running = True
        self._loop_thread = threading.Thread(
            target=self._run_loop, daemon=True, name="hand-loop")
        self._loop_thread.start()
        log.info("[Hand] Controller started — waiting for hand detection...")

    def stop(self) -> None:
        self._running = False
        if self._loop and self._loop.is_running():
            import asyncio as _asyncio
            self._loop.call_soon_threadsafe(
                lambda: _asyncio.ensure_future(self._shutdown()))
        if self._loop_thread and self._loop_thread.is_alive():
            self._loop_thread.join(timeout=5)
        self._connected = False
        self._enabled = False
        log.info("[Hand] Controller stopped")

    def emergency_stop(self) -> None:
        if self._connected and self._loop and self._loop.is_running():
            import asyncio as _asyncio
            _asyncio.run_coroutine_threadsafe(
                self._stop_all_motors(), self._loop)
        self.stop()

    def enable(self) -> None:
        self._enabled = True
        log.info("[Hand] enable()")

    def disable(self) -> None:
        self._enabled = False
        log.info("[Hand] disable()")

    # ── motion (sync wrappers) ───────────────────────────────────────────

    def set_finger(self, finger_idx: int, position: float,
                   speed_pct: float = 50.0) -> None:
        name = FINGER_DISPLAY[finger_idx] if finger_idx < 6 else str(finger_idx)
        log.info("[Hand] set_finger(%s, %.2f, speed=%.0f%%)", name, position, speed_pct)
        self._submit_command(("set_finger", (finger_idx, position, speed_pct)))

    def set_all_fingers(self, positions: List[float],
                        speed_pct: float = 50.0) -> None:
        log.info("[Hand] set_all_fingers(%s, speed=%.0f%%)", positions, speed_pct)
        self._submit_command(("set_all", (positions, speed_pct)))

    def open(self, speed_pct: float = 50.0) -> None:
        self.set_all_fingers([0.0] * 6, speed_pct)

    def close(self, speed_pct: float = 50.0) -> None:
        self._submit_command(("grip", (speed_pct,)))

    def grip(self, speed_pct: float = 50.0) -> None:
        self._submit_command(("grip", (speed_pct,)))

    def cup(self, speed_pct: float = 50.0) -> None:
        self._submit_command(("cup", (speed_pct,)))

    def pinch(self, speed_pct: float = 50.0) -> None:
        self.set_all_fingers([1.0, 1.0, 0.0, 0.0, 0.0, 0.0], speed_pct)

    def stop_motion(self) -> None:
        self._submit_command(("stop", ()))

    # ── state ────────────────────────────────────────────────────────────

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            self._state_count += 1
            if self._state_count % 25 == 0:   # every ~5 s
                pos_pct = [round(p * 100) for p in self._finger_positions]
                currents = list(self._finger_currents)
                log.info("[Hand] running — positions(%%): %s, currents(mA): %s, connected=%s, fault=%s",
                         pos_pct, currents, self._connected, self._fault)
            # grip force: sum of motor currents (mA) → approximate N
            grip_n = sum(self._finger_currents) / 1000.0 if self._finger_currents else 0.0
            return {
                "finger_positions": list(self._finger_positions),
                "finger_speeds": list(self._finger_speeds),
                "finger_currents": list(self._finger_currents),
                "finger_limits": list(self._finger_limits),
                "grip_force_n": round(grip_n, 1),
                "connected": self._connected,
                "is_enabled": self._enabled,
                "is_fault": self._fault,
            }

    # ── SocketIO handlers ────────────────────────────────────────────────

    def _on_enable(self, _data: dict = None, emit=None) -> None:
        self.enable()

    def _on_disable(self, _data: dict = None, emit=None) -> None:
        self.disable()

    def _on_open(self, _data: dict = None, emit=None) -> None:
        self.open()

    def _on_close(self, _data: dict = None, emit=None) -> None:
        self.close()

    def _on_pinch(self, _data: dict = None, emit=None) -> None:
        self.pinch()

    def _on_grip(self, _data: dict = None, emit=None) -> None:
        self.grip()

    def _on_cup(self, _data: dict = None, emit=None) -> None:
        self.cup()

    def _on_set_fingers(self, data: dict, emit=None) -> None:
        self.set_all_fingers(
            data.get("positions", [0.0] * 6),
            float(data.get("speed_pct", 50.0)),
        )

    # ── async internals ──────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Main async event loop running in a background thread."""
        import asyncio as _asyncio
        self._loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main_task())
        except Exception as exc:
            log.error("[Hand] Event loop crashed: %s", exc)
        finally:
            self._loop.close()
            self._loop = None

    async def _main_task(self) -> None:
        import asyncio as _asyncio
        self._cmd_queue = _asyncio.Queue(maxsize=32)

        # Auto-detect and connect.
        await self._connect()

        # Start periodic status poller.
        poll_task = _asyncio.create_task(self._status_poller())

        # Process commands from the queue.
        while self._running:
            try:
                cmd, args = await _asyncio.wait_for(
                    self._cmd_queue.get(), timeout=2.0)
                await self._handle_cmd(cmd, args)
            except _asyncio.TimeoutError:
                continue
            except Exception as exc:
                log.error("[Hand] Command error: %s", exc)

        poll_task.cancel()
        await self._disconnect()

    async def _connect(self) -> None:
        """Open connection to the hand (with automatic retry)."""
        if self._client is not None:
            return
        import asyncio as _asyncio
        try:
            if self._protocol == "can":
                await self._connect_can()
            elif self._protocol == "can_candlelight":
                await self._connect_candlelight()
            else:
                await self._connect_modbus()

            # Set unit mode to normalized (0-1000 per-mil).
            if self._client and self._connected:
                try:
                    await self._client.set_finger_unit_mode(
                        self._slave_id, _LIBSTARK.FingerUnitMode.Normalized)
                except Exception as _mode_exc:
                    log.debug("[Hand] set_finger_unit_mode skipped: %s", _mode_exc)
                # Read initial state.
                await self._read_motor_status()
                await self._read_finger_limits()
                log.info("[Hand] Connected — protocol=%s, slave_id=0x%02X",
                         self._protocol, self._slave_id)
                log.info("[Hand] Finger limits: %s",
                         [f"{l['min']}-{l['max']}" for l in self._finger_limits])
                # Check motor state
                try:
                    ms = await self._client.get_motor_state(self._slave_id)
                    log.info("[Hand] Motor state after connect: %s", ms)
                except Exception as _ms_exc:
                    log.warning("[Hand] get_motor_state failed: %s", _ms_exc)
        except Exception as exc:
            log.warning("[Hand] Connection failed: %s — will retry", exc)
            # Clean up partial state before retry.
            self._connected = False
            self._client = None
            self._can_send_fn = None
            self._can_recv_fn = None
            self._can_close_fn = None
            await _asyncio.sleep(5)
            _asyncio.ensure_future(self._connect())

    async def _connect_modbus(self) -> None:
        _LIBSTARK.init_logging()

        if self._port:
            # Explicit port.
            baudrate = _int_to_baudrate(self._baudrate)
            if baudrate is None:
                raise RuntimeError("Invalid baudrate")
            self._client = await _LIBSTARK.modbus_open(self._port, baudrate)
        else:
            # Auto-detect.
            protocol, port, baudrate, sid = (
                await _LIBSTARK.auto_detect_modbus_revo2(port_name=None, quick=True))
            if protocol != _LIBSTARK.StarkProtocolType.Modbus:
                raise RuntimeError(f"Detected {protocol}, expected Modbus")
            self._client = await _LIBSTARK.modbus_open(port, baudrate)
            self._slave_id = sid

        # Detect hardware type.
        try:
            info = await self._client.get_device_info(self._slave_id)
            log.info("[Hand] Device: %s", info.description if info else "?")
        except Exception:
            log.debug("[Hand] Could not read device info")

        self._connected = True

    async def _connect_can(self) -> None:
        """Connect via CAN 2.0 SocketCAN."""
        import sys as _sys, os as _os

        # Ensure SDK path for common_socketcan imports
        _sdk_path = _os.path.join(_os.path.dirname(__file__), "..",
                                   "hand_brainco_sdk", "python")
        _sdk_path = _os.path.normpath(_sdk_path)
        if _sdk_path not in _sys.path:
            _sys.path.insert(0, _sdk_path)

        from common_socketcan import socketcan_open as _can_open, \
            socketcan_close as _can_close, \
            socketcan_send_message as _can_send, \
            socketcan_receive_filtered as _can_recv

        _LIBSTARK.init_logging()

        self._can_send_fn = _can_send
        self._can_recv_fn = _can_recv
        self._can_close_fn = _can_close

        # 1. Create device handler FIRST (matches demo: init_device_handler
        #    in __init__ before socketcan_open)
        self._client = _LIBSTARK.init_device_handler(
            _LIBSTARK.StarkProtocolType.Can, self._master_id)

        # 2. Open SocketCAN AFTER init_device_handler (SDK closes socket
        #    during its own init)
        _can_open(self._can_iface, canfd=False)

        # 3. Register TX/RX callbacks
        _LIBSTARK.set_can_tx_callback(self._can_tx_callback)
        _LIBSTARK.set_can_rx_callback(self._can_rx_callback)

        # 4. Query device info
        info = await self._client.get_device_info(self._slave_id)
        log.info("[Hand] Device: %s", info.description if info else "?")

        self._connected = True

    async def _connect_candlelight(self) -> None:
        """Connect via candleLight USB-CAN FD adapter (Windows/Linux)."""
        import sys as _sys, os as _os

        _sdk_path = _os.path.join(_os.path.dirname(__file__), "..",
                                   "hand_brainco_sdk", "python")
        _sdk_path = _os.path.normpath(_sdk_path)
        if _sdk_path not in _sys.path:
            _sys.path.insert(0, _sdk_path)

        from candlelight_can import candlelight_open as _cl_open, \
            candlelight_close as _cl_close, \
            candlelight_send_message as _cl_send, \
            candlelight_receive_canfd_filtered as _cl_recv

        _LIBSTARK.init_logging()

        self._can_send_fn = _cl_send
        self._can_recv_fn = _cl_recv
        self._can_close_fn = _cl_close

        # 1. Create device handler using CANFD protocol type
        self._client = _LIBSTARK.init_device_handler(
            _LIBSTARK.StarkProtocolType.CanFd, self._master_id)

        # 2. Open candleLight AFTER init_device_handler
        _cl_open(
            channel=self._candlelight_channel,
            bitrate=self._candlelight_bitrate,
            data_bitrate=self._candlelight_data_bitrate,
            fd=True,
        )

        # 3. Register TX/RX callbacks
        _LIBSTARK.set_can_tx_callback(self._can_tx_callback)
        _LIBSTARK.set_can_rx_callback(self._can_rx_callback)

        # 4. Query device info
        info = await self._client.get_device_info(self._slave_id)
        log.info("[Hand] Device: %s", info.description if info else "?")

        self._connected = True

    # ── CAN TX/RX callbacks (called from SDK internals) ───────────────────

    def _can_tx_callback(self, _slave_id: int, can_id: int,
                         data: list) -> bool:
        if self._can_send_fn is None:
            return False
        try:
            return self._can_send_fn(can_id, bytes(data))
        except Exception as exc:
            log.error("[Hand] CAN TX error: %s", exc)
            return False

    def _can_rx_callback(self, _slave_id: int, expected_can_id: int,
                         expected_frames: int):
        if self._can_recv_fn is None:
            return (0, bytes([]))
        try:
            result = self._can_recv_fn(expected_can_id, expected_frames)
            if result is None:
                return (0, bytes([]))
            can_id, data, _fc = result
            return (can_id, bytes(data))
        except Exception as exc:
            log.debug("[Hand] CAN RX error: %s", exc)
            return (0, bytes([]))

    async def _disconnect(self) -> None:
        if self._client:
            try:
                if self._protocol in ("can", "can_candlelight"):
                    _LIBSTARK.close_device_handler(self._client)
                    if self._can_close_fn:
                        self._can_close_fn()
                else:
                    _LIBSTARK.modbus_close(self._client)
            except Exception:
                pass
            self._client = None
            self._can_send_fn = None
            self._can_recv_fn = None
            self._can_close_fn = None
        self._connected = False

    async def _shutdown(self) -> None:
        await self._disconnect()

    async def _handle_cmd(self, cmd: str, args: tuple) -> None:
        if not self._connected or self._client is None:
            log.debug("[Hand] Ignoring '%s' — not connected", cmd)
            return
        try:
            if cmd == "set_finger":
                idx, pos, spd = args
                # Scale 0.0-1.0 → 0-1000 normalized (no clamping to hardware limits;
                # the FingerUnitMode.Normalized tells the SDK to map 0-1000 → hw range).
                target = max(0, min(1000, int(pos * 1000)))
                with self._lock:
                    current = [int(p * 1000) for p in self._finger_positions]
                    current[idx] = target
                log.info("[Hand] _handle_cmd set_finger: idx=%d target=%d (raw 0-1000)",
                         idx, target)
                if self._protocol == "can":
                    await self._client.set_finger_positions(
                        self._slave_id, current)
                else:
                    speed = max(0, min(1000, int(spd * 10)))
                    await self._client.set_finger_positions_and_speeds(
                        self._slave_id, current, [speed] * 6)
                # Update displayed positions immediately
                with self._lock:
                    self._finger_positions = [p / 1000.0 for p in current]

            elif cmd == "set_all":
                positions, spd = args
                # Scale 0.0-1.0 → 0-1000 normalized (no clamping to hw limits;
                # FingerUnitMode.Normalized handles the hw range mapping).
                targets = [max(0, min(1000, int(p * 1000))) for p in positions]
                log.info("[Hand] _handle_cmd set_all: targets=%s (raw 0-1000), hw_limits=%s",
                         targets, self._finger_limits)
                if self._protocol == "can":
                    await self._client.set_finger_positions(
                        self._slave_id, targets)
                else:
                    speed = max(0, min(1000, int(spd * 10)))
                    await self._client.set_finger_positions_and_speeds(
                        self._slave_id, targets, [speed] * 6)
                # Update displayed positions immediately
                with self._lock:
                    self._finger_positions = [p / 1000.0 for p in targets]
            elif cmd == "grip":
                spd = args[0]
                speed = max(0, min(1000, int(spd * 10)))
                targets = list(FIST_FINGERS)
                if self._protocol == "can":
                    await self._client.set_finger_positions(
                        self._slave_id, targets)
                else:
                    await self._client.set_finger_positions_and_speeds(
                        self._slave_id, targets, [speed] * 6)
                with self._lock:
                    self._finger_positions = [p / 1000.0 for p in targets]
                await asyncio.sleep(0.3)
                targets = list(FIST_FINAL)
                if self._protocol == "can":
                    await self._client.set_finger_positions(
                        self._slave_id, targets)
                else:
                    await self._client.set_finger_positions_and_speeds(
                        self._slave_id, targets, [speed] * 6)
                with self._lock:
                    self._finger_positions = [p / 1000.0 for p in targets]
            elif cmd == "cup":
                spd = args[0]
                speed = max(0, min(1000, int(spd * 10)))
                targets = list(CUP_FINAL)
                if self._protocol == "can":
                    await self._client.set_finger_positions(
                        self._slave_id, targets)
                else:
                    await self._client.set_finger_positions_and_speeds(
                        self._slave_id, targets, [speed] * 6)
                with self._lock:
                    self._finger_positions = [p / 1000.0 for p in targets]

            elif cmd == "stop":
                if self._protocol == "can":
                    cur = [int(p * 1000) for p in self._finger_positions]
                    await self._client.set_finger_positions(
                        self._slave_id, cur)
                else:
                    await self._client.set_finger_speeds(
                        self._slave_id, [0] * 6)

            self._fault = False
        except Exception as exc:
            log.error("[Hand] Command '%s' failed: %s", cmd, exc)
            self._fault = True

    async def _status_poller(self) -> None:
        """Periodically read motor status (every 200ms)."""
        n = 0
        log.info("[Hand] Status poller started")
        try:
            while self._running:
                n += 1
                if n % 5 == 0:   # every ~1 s
                    with self._lock:
                        pos_pct = [round(p * 100) for p in self._finger_positions]
                    log.info("[Hand] running — positions(%%): %s, fault=%s",
                             pos_pct, self._fault)
                if self._connected and self._client:
                    await self._read_motor_status()
                await asyncio.sleep(0.2)
        except Exception as exc:
            log.error("[Hand] Status poller crashed: %s", exc, exc_info=True)

    async def _read_motor_status(self) -> None:
        try:
            status = await self._client.get_motor_status(self._slave_id)
            with self._lock:
                self._finger_positions = [p / 1000.0 for p in status.positions]
                self._finger_speeds = list(status.speeds)
                self._finger_currents = list(status.currents)
                self._fault = False
        except Exception as exc:
            log.warning("[Hand] Status read error: %s", exc)
            self._fault = True

    async def _read_finger_limits(self) -> None:
        """Read per-finger min/max position limits from device (Revo2 only)."""
        finger_ids = [
            _LIBSTARK.FingerId.Thumb,
            _LIBSTARK.FingerId.ThumbAux,
            _LIBSTARK.FingerId.Index,
            _LIBSTARK.FingerId.Middle,
            _LIBSTARK.FingerId.Ring,
            _LIBSTARK.FingerId.Pinky,
        ]
        try:
            settings = await self._client.get_all_finger_settings(self._slave_id)
            with self._lock:
                for i, s in enumerate(settings):
                    self._finger_limits[i] = {"min": s.min_position, "max": s.max_position}
        except Exception:
            # Fallback: read one by one
            try:
                for i, fid in enumerate(finger_ids):
                    try:
                        min_p = await self._client.get_finger_min_position(self._slave_id, fid)
                        max_p = await self._client.get_finger_max_position(self._slave_id, fid)
                        with self._lock:
                            self._finger_limits[i] = {"min": min_p, "max": max_p}
                    except Exception:
                        pass
            except Exception:
                log.warning("[Hand] Could not read finger limits — using defaults")

    async def _stop_all_motors(self) -> None:
        if self._client:
            try:
                await self._client.set_finger_speeds(self._slave_id, [0] * 6)
            except Exception:
                pass

    def _submit_command(self, cmd: tuple) -> None:
        """Submit a command tuple (name, args) from a sync context."""
        if not self._loop or not self._loop.is_running():
            log.debug("[Hand] Cannot submit '%s' — loop not running", cmd[0])
            return
        import asyncio as _asyncio
        _asyncio.run_coroutine_threadsafe(
            self._cmd_queue.put(cmd), self._loop)
