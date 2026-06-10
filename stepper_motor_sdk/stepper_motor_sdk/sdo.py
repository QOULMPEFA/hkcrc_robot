"""SDO (Service Data Object) communication layer for CANopen."""

import struct
import threading
import time
import logging

import can

from .constants import COBType, SDOCommand, SDO_TIMEOUT

log = logging.getLogger(__name__)


class SDOError(Exception):
    pass


class SDOAbortError(SDOError):
    """Raised when the node replies with an SDO abort."""

    ABORT_CODES = {
        0x05030000: "Toggle bit error",
        0x05040000: "SDO timeout",
        0x05040001: "Command specifier unknown",
        0x05040005: "Out of memory",
        0x06010000: "Unsupported access to an object",
        0x06010001: "Read-only object",
        0x06010002: "Write-only object",
        0x06020000: "Object does not exist",
        0x06040041: "Object cannot be mapped to PDO",
        0x06040042: "PDO length exceeded",
        0x06040043: "General parameter incompatibility",
        0x06040047: "General internal incompatibility",
        0x06060000: "Hardware error",
        0x06070010: "Data type / length mismatch",
        0x06070012: "Data type too high",
        0x06070013: "Data type too low",
        0x06090011: "Sub-index does not exist",
        0x06090030: "Value range exceeded",
        0x06090031: "Value too high",
        0x06090032: "Value too low",
        0x08000000: "General error",
        0x08000020: "Transfer or storage error",
        0x08000021: "Local control error",
        0x08000022: "Device state error",
    }

    def __init__(self, abort_code: int, index: int, sub: int):
        desc = self.ABORT_CODES.get(abort_code, "Unknown abort")
        super().__init__(
            f"SDO abort 0x{abort_code:08X} on 0x{index:04X}:{sub:02X} — {desc}"
        )
        self.abort_code = abort_code


class SDOClient:
    """
    Blocking SDO client that performs expedited read/write over python-can.

    One SDOClient per node. Thread-safe (uses a lock + threading.Event).
    """

    def __init__(self, bus: can.BusABC, node_id: int, timeout: float = SDO_TIMEOUT):
        self._bus = bus
        self._node_id = node_id
        self._timeout = timeout
        self._rx_cob = COBType.SDO_TX + node_id   # node → master
        self._tx_cob = COBType.SDO_RX + node_id   # master → node
        self._lock = threading.Lock()
        self._response_event = threading.Event()
        self._response_msg: can.Message | None = None

    # ── public API ────────────────────────────────────────────────────────────

    def read(self, index: int, sub: int = 0) -> bytes:
        """Upload (read) an object from the node. Returns raw bytes (4-byte LE).
        Retries on transient errors (drive busy / CAN noise)."""
        last_err = None
        for _ in range(3):
            with self._lock:
                self._response_event.clear()
                self._response_msg = None

                data = struct.pack("<BHB4x", SDOCommand.UPLOAD_REQ, index, sub)
                self._send(data)
                try:
                    return self._wait_response(index, sub, is_write=False)
                except SDOAbortError:
                    raise  # hard failure — don't retry
                except SDOError as e:
                    last_err = e
                    time.sleep(0.02)
                    continue
        raise last_err

    def read_i8(self, index: int, sub: int = 0) -> int:
        return struct.unpack_from("<b", self.read(index, sub))[0]

    def read_u8(self, index: int, sub: int = 0) -> int:
        return struct.unpack_from("<B", self.read(index, sub))[0]

    def read_i16(self, index: int, sub: int = 0) -> int:
        return struct.unpack_from("<h", self.read(index, sub))[0]

    def read_u16(self, index: int, sub: int = 0) -> int:
        return struct.unpack_from("<H", self.read(index, sub))[0]

    def read_i32(self, index: int, sub: int = 0) -> int:
        return struct.unpack_from("<i", self.read(index, sub))[0]

    def read_u32(self, index: int, sub: int = 0) -> int:
        return struct.unpack_from("<I", self.read(index, sub))[0]

    def write_u8(self, index: int, sub: int, value: int) -> None:
        self._write(index, sub, struct.pack("<B", value & 0xFF), SDOCommand.DOWNLOAD_1BYTE)

    def write_u16(self, index: int, sub: int, value: int) -> None:
        self._write(index, sub, struct.pack("<H", value & 0xFFFF), SDOCommand.DOWNLOAD_2BYTE)

    def write_i16(self, index: int, sub: int, value: int) -> None:
        self._write(index, sub, struct.pack("<h", value), SDOCommand.DOWNLOAD_2BYTE)

    def write_u32(self, index: int, sub: int, value: int) -> None:
        self._write(index, sub, struct.pack("<I", value & 0xFFFFFFFF), SDOCommand.DOWNLOAD_4BYTE)

    def write_i32(self, index: int, sub: int, value: int) -> None:
        self._write(index, sub, struct.pack("<i", value), SDOCommand.DOWNLOAD_4BYTE)

    def write_i8(self, index: int, sub: int, value: int) -> None:
        self._write(index, sub, struct.pack("<b", value), SDOCommand.DOWNLOAD_1BYTE)

    # ── called by the bus listener ─────────────────────────────────────────────

    def on_message(self, msg: can.Message) -> None:
        """Feed an incoming CAN frame to the SDO client (call from listener)."""
        if msg.arbitration_id == self._rx_cob and len(msg.data) == 8:
            self._response_msg = msg
            self._response_event.set()

    # ── internal ──────────────────────────────────────────────────────────────

    def _write(self, index: int, sub: int, payload: bytes, cmd: int) -> None:
        with self._lock:
            self._response_event.clear()
            self._response_msg = None

            buf = bytearray(8)
            buf[0] = cmd
            struct.pack_into("<HB", buf, 1, index, sub)
            buf[4:4 + len(payload)] = payload
            self._send(bytes(buf))
            self._wait_response(index, sub, is_write=True)

    def _send(self, data: bytes) -> None:
        msg = can.Message(
            arbitration_id=self._tx_cob,
            data=data,
            is_extended_id=False,
        )
        self._bus.send(msg)
        log.debug("SDO TX → 0x%03X  %s", self._tx_cob, data.hex())

    def _wait_response(self, index: int, sub: int, is_write: bool) -> bytes:
        deadline = time.monotonic() + self._timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SDOError(
                    f"SDO timeout waiting for 0x{index:04X}:{sub:02X}"
                )
            if not self._response_event.wait(remaining):
                raise SDOError(
                    f"SDO timeout waiting for 0x{index:04X}:{sub:02X}"
                )
            msg = self._response_msg
            cmd = msg.data[0]

            # Validate this response is for our request (avoid stale frames).
            resp_index = struct.unpack_from("<H", msg.data, 1)[0]
            resp_sub = msg.data[3]
            if resp_index != index or resp_sub != sub:
                # Stale/delayed response for a different request — ignore and retry.
                log.debug(
                    "SDO ignoring stale frame idx=0x%04X:%02X "
                    "(expected 0x%04X:%02X)",
                    resp_index, resp_sub, index, sub,
                )
                self._response_event.clear()
                self._response_msg = None
                continue

            if cmd == SDOCommand.ABORT:
                abort_code = struct.unpack_from("<I", msg.data, 4)[0]
                raise SDOAbortError(abort_code, index, sub)

            if is_write:
                if cmd != SDOCommand.DOWNLOAD_RESP:
                    raise SDOError(
                        f"Unexpected SDO response 0x{cmd:02X} for write to "
                        f"0x{index:04X}:{sub:02X}"
                    )
                return b""

            # upload response — return 4-byte payload (zero-padded)
            payload = bytearray(4)
            if cmd == SDOCommand.UPLOAD_RESP_4:
                payload[:] = msg.data[4:8]
            elif cmd == SDOCommand.UPLOAD_RESP_2:
                payload[:2] = msg.data[4:6]
            elif cmd == SDOCommand.UPLOAD_RESP_1:
                payload[0] = msg.data[4]
            else:
                raise SDOError(
                    f"Unexpected SDO upload response 0x{cmd:02X} for "
                    f"0x{index:04X}:{sub:02X}"
                )
            log.debug(
                "SDO RX ← 0x%03X  idx=0x%04X sub=%d  data=%s",
                self._rx_cob, index, sub, payload.hex(),
            )
            return bytes(payload)
