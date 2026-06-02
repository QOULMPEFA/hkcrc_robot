"""CANopen node abstraction — owns the bus listener and SDO client."""

import logging
import threading
from typing import TYPE_CHECKING

import can

from .constants import COBType, DEFAULT_NODE_ID, DEFAULT_BITRATE
from .sdo import SDOClient

if TYPE_CHECKING:
    from .bus import SharedCANBus

log = logging.getLogger(__name__)


class CANopenNode:
    """
    Represents a single CANopen node on a CAN bus.

    Single-node usage (owns its own bus + listener thread):
        with CANopenNode(channel="can0", node_id=1) as node:
            ...

    Multi-node usage (shared bus, no per-node listener thread):
        with SharedCANBus(channel="can0") as shared:
            node1 = CANopenNode(node_id=1, shared_bus=shared).start()
            node2 = CANopenNode(node_id=2, shared_bus=shared).start()
    """

    def __init__(
        self,
        interface: str = "socketcan",
        channel: str = "can0",
        bitrate: int = DEFAULT_BITRATE,
        node_id: int = DEFAULT_NODE_ID,
        sdo_timeout: float = 1.0,
        bus: can.BusABC | None = None,
        shared_bus: "SharedCANBus | None" = None,
    ):
        self.node_id = node_id
        self._interface = interface
        self._channel = channel
        self._bitrate = bitrate
        self._shared_bus = shared_bus

        if shared_bus is not None:
            # Use raw bus from shared manager; listener thread is owned by SharedCANBus
            self._bus = shared_bus.raw_bus
            self._owns_bus = False
            self._owns_listener = False
        elif bus is not None:
            self._bus = bus
            self._owns_bus = False
            self._owns_listener = True
        else:
            self._bus = can.Bus(
                interface=interface,
                channel=channel,
                bitrate=bitrate,
            )
            self._owns_bus = True
            self._owns_listener = True

        self.sdo = SDOClient(self._bus, node_id, timeout=sdo_timeout)

        self._running = False
        self._ready_event = threading.Event()

        if self._owns_listener:
            self._listener_thread = threading.Thread(
                target=self._listen, daemon=True, name=f"can-listener-node{node_id}"
            )

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> "CANopenNode":
        self._running = True

        if self._shared_bus is not None:
            # Register with the shared dispatcher; no private listener needed
            self._shared_bus.register(self.node_id, self.sdo)
            self._ready_event.set()
        else:
            self._listener_thread.start()
            # Wait until the listener thread enters recv() before returning
            self._ready_event.wait(timeout=1.0)

        log.info(
            "CANopenNode started: node_id=%d  channel=%s",
            self.node_id, self._channel,
        )
        return self

    def stop(self) -> None:
        self._running = False
        if self._shared_bus is not None:
            self._shared_bus.unregister(self.node_id)
        if self._owns_bus:
            self._bus.shutdown()
        log.info("CANopenNode stopped (node_id=%d)", self.node_id)

    def __enter__(self) -> "CANopenNode":
        return self.start()

    def __exit__(self, *_) -> None:
        self.stop()

    # ── raw send ──────────────────────────────────────────────────────────────

    def send_raw(self, cob_id: int, data: bytes) -> None:
        msg = can.Message(
            arbitration_id=cob_id,
            data=data,
            is_extended_id=False,
        )
        self._bus.send(msg)

    def send_nmt(self, command: int) -> None:
        """Send an NMT command to this node."""
        self.send_raw(COBType.NMT, bytes([command, self.node_id]))

    # ── private listener (single-node mode only) ──────────────────────────────

    def _listen(self) -> None:
        self._ready_event.set()
        while self._running:
            try:
                msg = self._bus.recv(timeout=0.1)
            except can.CanError as exc:
                log.warning("CAN recv error: %s", exc)
                continue
            if msg is None:
                continue
            self._dispatch(msg)

    def _dispatch(self, msg: can.Message) -> None:
        aid = msg.arbitration_id
        func = aid & 0x780
        node = aid & 0x07F

        if func == COBType.SDO_TX and node == self.node_id:
            self.sdo.on_message(msg)
        elif func == COBType.HEARTBEAT:
            log.debug("Heartbeat from node %d: state=0x%02X", node, msg.data[0])
        elif func == COBType.EMCY and node == self.node_id:
            err = int.from_bytes(msg.data[:2], "little")
            log.error("EMCY from node %d: error_code=0x%04X", node, err)
        else:
            log.debug("CAN frame: 0x%03X  %s", aid, msg.data.hex())
