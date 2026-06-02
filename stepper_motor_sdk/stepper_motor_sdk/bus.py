"""Shared CAN bus manager for multi-node setups."""

import logging
import threading
from typing import Dict

import can

from .constants import COBType, DEFAULT_BITRATE

log = logging.getLogger(__name__)


class SharedCANBus:
    """
    Owns one can.Bus and one listener thread shared across multiple nodes.
    Each CANopenNode registers itself; frames are dispatched by node_id.
    """

    def __init__(
        self,
        interface: str = "socketcan",
        channel: str = "can0",
        bitrate: int = DEFAULT_BITRATE,
    ):
        self._bus = can.Bus(interface=interface, channel=channel, bitrate=bitrate)
        self._nodes: Dict[int, "_NodeDispatch"] = {}
        self._lock = threading.Lock()
        self._running = False
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._listen, daemon=True, name="can-shared-listener"
        )

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> "SharedCANBus":
        self._running = True
        self._thread.start()
        self._ready.wait(timeout=1.0)
        log.info("SharedCANBus started: %s", self._bus.channel_info)
        return self

    def stop(self) -> None:
        self._running = False
        self._bus.shutdown()       # unblocks recv() in the listener thread
        self._thread.join(timeout=1.0)
        log.info("SharedCANBus stopped")

    def __enter__(self) -> "SharedCANBus":
        return self.start()

    def __exit__(self, *_) -> None:
        self.stop()

    # ── node registration ─────────────────────────────────────────────────────

    def register(self, node_id: int, sdo_client) -> None:
        with self._lock:
            self._nodes[node_id] = sdo_client

    def unregister(self, node_id: int) -> None:
        with self._lock:
            self._nodes.pop(node_id, None)

    # ── raw send ──────────────────────────────────────────────────────────────

    def send(self, cob_id: int, data: bytes) -> None:
        msg = can.Message(arbitration_id=cob_id, data=data, is_extended_id=False)
        self._bus.send(msg)

    @property
    def raw_bus(self) -> can.BusABC:
        return self._bus

    # ── listener ─────────────────────────────────────────────────────────────

    def _listen(self) -> None:
        self._ready.set()
        while self._running:
            try:
                msg = self._bus.recv(timeout=0.1)
            except can.CanError as exc:
                if self._running:   # suppress expected error on shutdown
                    log.warning("CAN recv error: %s", exc)
                break
            if msg is None:
                continue
            self._dispatch(msg)

    def _dispatch(self, msg: can.Message) -> None:
        aid = msg.arbitration_id
        func = aid & 0x780
        node_id = aid & 0x07F

        if func == COBType.SDO_TX:
            with self._lock:
                sdo = self._nodes.get(node_id)
            if sdo is not None:
                sdo.on_message(msg)
            else:
                log.debug("Unregistered SDO from node %d", node_id)
        elif func == COBType.HEARTBEAT:
            log.debug("Heartbeat node %d state=0x%02X", node_id, msg.data[0])
        elif func == COBType.EMCY:
            err = int.from_bytes(msg.data[:2], "little")
            log.error("EMCY node %d error=0x%04X", node_id, err)
        else:
            log.debug("CAN 0x%03X  %s", aid, msg.data.hex())
