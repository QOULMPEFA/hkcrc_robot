"""Dexterous hand controller — BrainCo Revo1/Revo2 via bc_stark_sdk (Modbus/CAN).

Runs async SDK operations on a dedicated asyncio event loop in a background
thread.  Falls back to stub mode when the SDK is not installed or the hand is
unreachable.

SocketIO events: hand_open, hand_close, hand_pinch, hand_set_fingers
"""

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

FINGER_NAMES = ["thumb", "index", "middle", "ring", "pinky", "wrist"]
FINGER_DISPLAY = ["拇指", "食指", "中指", "无名指", "小指", "手腕"]


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
        # Serial/Modbus config
        self._port = cfg.get("port", None)          # None = auto-detect
        self._baudrate = int(cfg.get("baudrate", 115200))
        # CAN config
        self._protocol = cfg.get("protocol", "modbus")  # "modbus" | "can"
        # Shared
        self._slave_id = int(cfg.get("slave_id", 0x7E))

        self._client: Optional[Any] = None
        self._connected = False
        self._enabled = False
        self._fault = False
        self._finger_positions: List[float] = [0.0] * 6
        self._finger_speeds: List[float] = [0.0] * 6
        self._finger_currents: List[float] = [0.0] * 6

        self._lock = threading.Lock()
        self._loop_thread: Optional[threading.Thread] = None
        self._loop: Optional[Any] = None
        self._running = False

        # Command queue for thread-safe async submission.
        self._cmd_queue: Optional[Any] = None  # asyncio.Queue, set after loop init
        self._task: Optional[Any] = None

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
            "connected": False,
            "is_enabled": False,
            "is_fault": False,
        }

    def get_socketio_handlers(self) -> Dict[str, callable]:
        return {
            "hand_open":        self._on_open,
            "hand_close":       self._on_close,
            "hand_pinch":       self._on_pinch,
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
            self._loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self._shutdown()))
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
        self.set_all_fingers([1.0] * 6, speed_pct)

    def pinch(self, speed_pct: float = 50.0) -> None:
        self.set_all_fingers([1.0, 1.0, 0.0, 0.0, 0.0, 0.0], speed_pct)

    def stop_motion(self) -> None:
        self._submit_command(("stop", ()))

    # ── state ────────────────────────────────────────────────────────────

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "finger_positions": list(self._finger_positions),
                "finger_speeds": list(self._finger_speeds),
                "finger_currents": list(self._finger_currents),
                "connected": self._connected,
                "is_enabled": self._enabled,
                "is_fault": self._fault,
            }

    # ── SocketIO handlers ────────────────────────────────────────────────

    def _on_open(self, _data: dict = None, emit=None) -> None:
        self.open()

    def _on_close(self, _data: dict = None, emit=None) -> None:
        self.close()

    def _on_pinch(self, _data: dict = None, emit=None) -> None:
        self.pinch()

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
        """Auto-detect and open connection to the hand."""
        if self._client is not None:
            return
        import asyncio as _asyncio
        try:
            if self._protocol == "can":
                await self._connect_can()
            else:
                await self._connect_modbus()

            # Set unit mode to normalized (0-1000 per-mil).
            if self._client and self._connected:
                await self._client.set_finger_unit_mode(
                    self._slave_id, _LIBSTARK.FingerUnitMode.Normalized)
                # Read initial state.
                await self._read_motor_status()
                log.info("[Hand] Connected — protocol=%s, slave_id=0x%02X",
                         self._protocol, self._slave_id)
        except Exception as exc:
            log.warning("[Hand] Auto-detect failed: %s — will retry", exc)
            # Schedule a retry.
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
        raise NotImplementedError("CAN protocol not yet implemented in controller")

    async def _disconnect(self) -> None:
        if self._client:
            try:
                _LIBSTARK.modbus_close(self._client)
            except Exception:
                pass
            self._client = None
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
                # Convert 0-1 to 0-1000 (per-mil normalized).
                target = max(0, min(1000, int(pos * 1000)))
                speed = max(0, min(1000, int(spd * 10)))
                positions = list(self._finger_positions)
                positions[idx] = target
                await self._client.set_finger_positions_and_speeds(
                    self._slave_id, positions, [speed] * 6)

            elif cmd == "set_all":
                positions, spd = args
                targets = [max(0, min(1000, int(p * 1000))) for p in positions]
                speed = max(0, min(1000, int(spd * 10)))
                await self._client.set_finger_positions_and_speeds(
                    self._slave_id, targets, [speed] * 6)

            elif cmd == "stop":
                # Send zero speed to stop.
                await self._client.set_finger_speeds(
                    self._slave_id, [0] * 6)
        except Exception as exc:
            log.error("[Hand] Command '%s' failed: %s", cmd, exc)
            self._fault = True

    async def _status_poller(self) -> None:
        """Periodically read motor status (every 200ms)."""
        while self._running:
            if self._connected and self._client:
                await self._read_motor_status()
            await asyncio.sleep(0.2)

    async def _read_motor_status(self) -> None:
        try:
            status = await self._client.get_motor_status(self._slave_id)
            with self._lock:
                # positions are in per-mil (0-1000), convert to 0-1
                self._finger_positions = [p / 1000.0 for p in status.positions]
                self._finger_speeds = list(status.speeds)
                self._finger_currents = list(status.currents)
                self._fault = False
        except Exception as exc:
            log.debug("[Hand] Status read error: %s", exc)
            self._fault = True

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
