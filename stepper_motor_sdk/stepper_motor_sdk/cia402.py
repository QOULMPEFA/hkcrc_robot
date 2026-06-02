"""CiA 402 drive state machine and transition helpers."""

import enum
import logging
import time
from typing import Callable

from .constants import Controlword, Statusword, OD

log = logging.getLogger(__name__)


class DriveState(enum.Enum):
    NOT_READY            = "Not Ready to Switch On"
    SWITCH_ON_DISABLED   = "Switch On Disabled"
    READY_TO_SWITCH_ON   = "Ready to Switch On"
    SWITCHED_ON          = "Switched On"
    OPERATION_ENABLED    = "Operation Enabled"
    QUICK_STOP_ACTIVE    = "Quick Stop Active"
    FAULT_REACTION       = "Fault Reaction Active"
    FAULT                = "Fault"
    UNKNOWN              = "Unknown"


_STATE_MAP = {
    (Statusword.MASK_STATE & Statusword.STATE_NOT_READY):          DriveState.NOT_READY,
    (Statusword.MASK_STATE & Statusword.STATE_SWITCH_ON_DISABLED): DriveState.SWITCH_ON_DISABLED,
    (Statusword.MASK_STATE & Statusword.STATE_READY_TO_SWITCH_ON): DriveState.READY_TO_SWITCH_ON,
    (Statusword.MASK_STATE & Statusword.STATE_SWITCHED_ON):        DriveState.SWITCHED_ON,
    (Statusword.MASK_STATE & Statusword.STATE_OPERATION_ENABLED):  DriveState.OPERATION_ENABLED,
    (Statusword.MASK_STATE & Statusword.STATE_QUICK_STOP_ACTIVE):  DriveState.QUICK_STOP_ACTIVE,
    (Statusword.MASK_STATE & Statusword.STATE_FAULT_REACTION):     DriveState.FAULT_REACTION,
    (Statusword.MASK_STATE & Statusword.STATE_FAULT):              DriveState.FAULT,
}


def decode_state(statusword: int) -> DriveState:
    masked = statusword & Statusword.MASK_STATE
    return _STATE_MAP.get(masked, DriveState.UNKNOWN)


def decode_statusword(sw: int) -> dict:
    """Decode statusword into a human-readable dict."""
    return {
        "state":           decode_state(sw),
        "ready":           bool(sw & Statusword.READY_TO_SWITCH_ON),
        "switched_on":     bool(sw & Statusword.SWITCHED_ON),
        "op_enabled":      bool(sw & Statusword.OPERATION_ENABLED),
        "fault":           bool(sw & Statusword.FAULT),
        "voltage_enabled": bool(sw & Statusword.VOLTAGE_ENABLED),
        "quick_stop":      not bool(sw & Statusword.QUICK_STOP),  # active-low
        "warning":         bool(sw & Statusword.WARNING),
        "remote":          bool(sw & Statusword.REMOTE),
        "target_reached":  bool(sw & Statusword.TARGET_REACHED),
        "home_attained":   bool(sw & Statusword.HOME_ATTAINED),
        "raw":             f"0x{sw:04X}",
    }


class CiA402StateMachine:
    """
    Manages the CiA 402 power-state machine for a single drive node.

    Uses callbacks for controlword write and statusword read so it can be
    used with either SDO or PDO transport.
    """

    _TRANSITION_SEQUENCE = [
        DriveState.SWITCH_ON_DISABLED,
        DriveState.READY_TO_SWITCH_ON,
        DriveState.SWITCHED_ON,
        DriveState.OPERATION_ENABLED,
    ]

    def __init__(
        self,
        write_controlword: Callable[[int], None],
        read_statusword: Callable[[], int],
        step_delay: float = 0.05,
        state_timeout: float = 2.0,
    ):
        self._write_cw = write_controlword
        self._read_sw = read_statusword
        self._step_delay = step_delay
        self._state_timeout = state_timeout

    # ── public ────────────────────────────────────────────────────────────────

    def get_state(self) -> DriveState:
        return decode_state(self._read_sw())

    def get_statusword_info(self) -> dict:
        return decode_statusword(self._read_sw())

    def enable(self) -> None:
        """Drive the state machine to Operation Enabled."""
        current = self.get_state()
        log.info("Enable: current state = %s", current.value)

        if current == DriveState.FAULT:
            self.reset_fault()
            current = self.get_state()

        if current == DriveState.OPERATION_ENABLED:
            return

        # Full ordered path to Operation Enabled
        path = [
            DriveState.READY_TO_SWITCH_ON,
            DriveState.SWITCHED_ON,
            DriveState.OPERATION_ENABLED,
        ]

        # Skip states already reached
        skip = {
            DriveState.READY_TO_SWITCH_ON: 1,
            DriveState.SWITCHED_ON:        2,
        }
        start = skip.get(current, 0)

        for target in path[start:]:
            self._step_to(target)

    def disable(self) -> None:
        """Transition to Switched On (motor disabled but powered)."""
        self._write_cw(Controlword.CMD_SWITCH_ON)
        self._wait_for_state(DriveState.SWITCHED_ON)

    def shutdown(self) -> None:
        """Transition to Ready to Switch On (release torque)."""
        self._write_cw(Controlword.CMD_SHUTDOWN)
        self._wait_for_state(DriveState.READY_TO_SWITCH_ON)

    def quick_stop(self) -> None:
        """Activate quick stop (bit 2 = 0)."""
        self._write_cw(Controlword.CMD_QUICK_STOP)
        self._wait_for_state(DriveState.QUICK_STOP_ACTIVE, timeout=5.0)

    def reset_fault(self) -> None:
        """Reset a drive fault via rising-edge on bit 7."""
        self._write_cw(0x0000)
        time.sleep(self._step_delay)
        self._write_cw(Controlword.CMD_FAULT_RESET)
        time.sleep(self._step_delay)
        self._write_cw(0x0000)
        self._wait_for_state(DriveState.SWITCH_ON_DISABLED)

    def is_fault(self) -> bool:
        return bool(self._read_sw() & Statusword.FAULT)

    def is_target_reached(self) -> bool:
        return bool(self._read_sw() & Statusword.TARGET_REACHED)

    def is_home_attained(self) -> bool:
        return bool(self._read_sw() & Statusword.HOME_ATTAINED)

    # ── internal ──────────────────────────────────────────────────────────────

    def _step_to(self, target: DriveState) -> None:
        cmd = {
            DriveState.READY_TO_SWITCH_ON: Controlword.CMD_SHUTDOWN,
            DriveState.SWITCHED_ON:        Controlword.CMD_SWITCH_ON,
            DriveState.OPERATION_ENABLED:  Controlword.CMD_ENABLE_OPERATION,
        }.get(target)

        if cmd is None:
            raise ValueError(f"No direct transition command to {target}")

        log.debug("State machine: sending cmd=0x%04X to reach %s", cmd, target.value)
        self._write_cw(cmd)
        time.sleep(self._step_delay)
        self._wait_for_state(target)

    def _wait_for_state(
        self, target: DriveState, timeout: float | None = None
    ) -> None:
        deadline = time.monotonic() + (timeout or self._state_timeout)
        while time.monotonic() < deadline:
            current = self.get_state()
            if current == target:
                log.debug("State machine: reached %s", target.value)
                return
            if current == DriveState.FAULT and target != DriveState.SWITCH_ON_DISABLED:
                sw = self._read_sw()
                raise RuntimeError(
                    f"Drive entered FAULT state (statusword=0x{sw:04X})"
                )
            time.sleep(0.02)
        raise TimeoutError(
            f"Timeout waiting for state {target.value} "
            f"(current: {self.get_state().value})"
        )
