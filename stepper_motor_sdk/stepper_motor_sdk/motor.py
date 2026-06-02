"""High-level CiA 402 stepper motor control API."""

import logging
import time
from typing import Callable

from .cia402 import CiA402StateMachine, DriveState, decode_statusword
from .constants import (
    Controlword, OD, OperationMode,
    DEFAULT_PULSES_PER_REV, HomingMethod,
)
from .node import CANopenNode
from .sdo import SDOClient

log = logging.getLogger(__name__)


class StepperMotor:
    """
    High-level control interface for an AIMotor CiA 402 stepper drive.

    All velocity units: Pul/s  (use rpm_to_puls() for r/min conversion)
    All position units: Pul    (use rev_to_puls() for revolution conversion)
    All acceleration:   Pul/s²
    """

    def __init__(
        self,
        node: CANopenNode,
        pulses_per_rev: int = DEFAULT_PULSES_PER_REV,
    ):
        self._node = node
        self.sdo: SDOClient = node.sdo
        self.pulses_per_rev = pulses_per_rev

        self._sm = CiA402StateMachine(
            write_controlword=self._write_cw,
            read_statusword=self._read_sw,
        )

    # ── unit helpers ─────────────────────────────────────────────────────────

    def rpm_to_puls(self, rpm: float) -> int:
        """Convert r/min → Pul/s."""
        return int(rpm / 60.0 * self.pulses_per_rev)

    def puls_to_rpm(self, puls: float) -> float:
        """Convert Pul/s → r/min."""
        return puls * 60.0 / self.pulses_per_rev

    def rev_to_puls(self, revolutions: float) -> int:
        """Convert revolutions → pulses."""
        return int(revolutions * self.pulses_per_rev)

    # ── state machine ────────────────────────────────────────────────────────

    def enable(self) -> None:
        """Bring the drive to Operation Enabled state."""
        self._sm.enable()

    def disable(self) -> None:
        """Disable the drive (Switched On, no torque output)."""
        self._sm.disable()

    def shutdown(self) -> None:
        """Release torque (Ready to Switch On)."""
        self._sm.shutdown()

    def quick_stop(self) -> None:
        """Activate quick stop deceleration."""
        self._sm.quick_stop()

    def reset_fault(self) -> None:
        """Clear a drive fault."""
        self._sm.reset_fault()

    def get_state(self) -> DriveState:
        return self._sm.get_state()

    def get_status(self) -> dict:
        return decode_statusword(self._read_sw())

    def is_fault(self) -> bool:
        return self._sm.is_fault()

    def is_target_reached(self) -> bool:
        return self._sm.is_target_reached()

    # ── mode: Profile Velocity (PV) ──────────────────────────────────────────

    def set_velocity_mode(
        self,
        acceleration: int = 100_000,
        deceleration: int = 100_000,
    ) -> None:
        """Switch to PV (Profile Velocity) mode and configure ramp."""
        self.sdo.write_i8(OD.MODES_OF_OPERATION, 0, OperationMode.PV)
        self._wait_mode(OperationMode.PV)
        self.sdo.write_i32(OD.PROFILE_ACCELERATION, 0, acceleration)
        self.sdo.write_i32(OD.PROFILE_DECELERATION, 0, deceleration)

    def set_velocity(self, velocity_puls: int) -> None:
        """Set target velocity in Pul/s (negative = reverse). Drive must be enabled."""
        self.sdo.write_i32(OD.TARGET_VELOCITY, 0, velocity_puls)

    def set_velocity_rpm(self, rpm: float) -> None:
        """Set target velocity in r/min."""
        self.set_velocity(self.rpm_to_puls(rpm))

    def stop(self, release_torque: bool = False) -> None:
        """Decelerate to zero. Optionally release torque afterwards."""
        self.set_velocity(0)
        if release_torque:
            time.sleep(0.5)
            self.shutdown()

    # ── mode: Profile Position (PP) ──────────────────────────────────────────

    def set_position_mode(
        self,
        profile_velocity: int = 10_000,
        acceleration: int = 100_000,
        deceleration: int = 100_000,
    ) -> None:
        """Switch to PP (Profile Position) mode and configure motion profile."""
        self.sdo.write_i8(OD.MODES_OF_OPERATION, 0, OperationMode.PP)
        self._wait_mode(OperationMode.PP)
        self.sdo.write_i32(OD.PROFILE_VELOCITY, 0, profile_velocity)
        self.sdo.write_i32(OD.PROFILE_ACCELERATION, 0, acceleration)
        self.sdo.write_i32(OD.PROFILE_DECELERATION, 0, deceleration)

    def move_absolute(self, position_puls: int, wait: bool = True, timeout: float = 30.0) -> None:
        """Move to absolute position (Pul). Blocks until target reached if wait=True."""
        self._pp_trigger(position_puls, relative=False)
        if wait:
            self._wait_target_reached(timeout)

    def move_relative(self, delta_puls: int, wait: bool = True, timeout: float = 30.0) -> None:
        """Move relative to current position (Pul)."""
        self._pp_trigger(delta_puls, relative=True)
        if wait:
            self._wait_target_reached(timeout)

    def move_absolute_rev(self, revolutions: float, wait: bool = True, timeout: float = 30.0) -> None:
        self.move_absolute(self.rev_to_puls(revolutions), wait=wait, timeout=timeout)

    def move_relative_rev(self, revolutions: float, wait: bool = True, timeout: float = 30.0) -> None:
        self.move_relative(self.rev_to_puls(revolutions), wait=wait, timeout=timeout)

    # ── mode: Profile Torque (PT) ────────────────────────────────────────────

    def set_torque_mode(self, max_velocity_puls: int = 50_000) -> None:
        """Switch to PT (Profile Torque) mode."""
        self.sdo.write_i8(OD.MODES_OF_OPERATION, 0, OperationMode.PT)
        self._wait_mode(OperationMode.PT)
        self.sdo.write_u32(OD.MAX_PROFILE_VELOCITY, 0, max_velocity_puls)

    def set_torque(self, torque_permille: int) -> None:
        """
        Set target torque. 1000 = 100 % rated torque.
        Range: -3000 ~ 3000  (unit: 0.1 %)
        """
        self.sdo.write_i16(OD.TARGET_TORQUE, 0, torque_permille)

    # ── mode: Homing (HM) ────────────────────────────────────────────────────

    def set_homing_mode(
        self,
        method: int = HomingMethod.CURRENT_POSITION,
        fast_speed: int = 5_000,
        slow_speed: int = 500,
        acceleration: int = 100_000,
        home_offset: int = 0,
    ) -> None:
        """Configure homing parameters. Call home() to execute."""
        self.sdo.write_i8(OD.MODES_OF_OPERATION, 0, OperationMode.HM)
        self._wait_mode(OperationMode.HM)
        self.sdo.write_i8(OD.HOMING_METHOD, 0, method)
        self.sdo.write_i32(OD.HOMING_SPEED, 1, fast_speed)
        self.sdo.write_i32(OD.HOMING_SPEED, 2, slow_speed)
        self.sdo.write_i32(OD.HOMING_ACCELERATION, 0, acceleration)
        self.sdo.write_i32(OD.HOME_OFFSET, 0, home_offset)

    def home(self, wait: bool = True, timeout: float = 60.0) -> None:
        """Start homing sequence. Blocks until home attained if wait=True."""
        # rising edge on bit 4 starts homing
        self._write_cw(Controlword.CMD_ENABLE_OPERATION)
        time.sleep(0.05)
        self._write_cw(Controlword.CMD_ENABLE_OPERATION | Controlword.NEW_SETPOINT)
        if wait:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self._sm.is_home_attained():
                    log.info("Homing completed")
                    return
                if self._sm.is_fault():
                    raise RuntimeError("Fault during homing")
                time.sleep(0.1)
            raise TimeoutError("Homing timed out")

    # ── feedback ─────────────────────────────────────────────────────────────

    def get_position(self) -> int:
        """Actual position in Pul."""
        return self.sdo.read_i32(OD.POSITION_ACTUAL)

    def get_velocity(self) -> int:
        """Actual velocity in Pul/s."""
        return self.sdo.read_i32(OD.VELOCITY_ACTUAL)

    def get_torque(self) -> int:
        """Actual torque in 0.1 % (1000 = 100 %). Returns 0 if unsupported."""
        for sub in (1, 0):
            try:
                return self.sdo.read_i16(OD.TORQUE_ACTUAL, sub)
            except Exception:
                continue
        return 0

    def get_position_rev(self) -> float:
        return self.get_position() / self.pulses_per_rev

    def get_velocity_rpm(self) -> float:
        return self.puls_to_rpm(self.get_velocity())

    def get_operation_mode(self) -> int:
        return self.sdo.read_i8(OD.MODES_OF_OPERATION_DISPLAY)

    # ── parameter save / restore ─────────────────────────────────────────────

    def save_parameters(self) -> None:
        """Persist all parameters to EEPROM (store=0x65766173 'save')."""
        self.sdo.write_u32(0x1010, 1, 0x65766173)
        log.info("Parameters saved to EEPROM")

    # ── internal ─────────────────────────────────────────────────────────────

    def _write_cw(self, value: int) -> None:
        self.sdo.write_u16(OD.CONTROLWORD, 0, value)

    def _read_sw(self) -> int:
        return self.sdo.read_u16(OD.STATUSWORD)

    def _wait_mode(self, mode: int, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.get_operation_mode() == mode:
                return
            time.sleep(0.05)
        raise TimeoutError(f"Timeout waiting for mode {mode}")

    def _pp_trigger(self, position_puls: int, relative: bool) -> None:
        self.sdo.write_i32(OD.TARGET_POSITION, 0, position_puls)
        base_cw = Controlword.CMD_ENABLE_OPERATION
        if relative:
            base_cw |= Controlword.ABS_REL
        # Rising edge on NEW_SETPOINT triggers the move.
        # Clear first, hold 50 ms so the drive registers the low level, then raise.
        self._write_cw(base_cw)
        time.sleep(0.05)
        self._write_cw(base_cw | Controlword.NEW_SETPOINT)

    def _wait_target_reached(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        # Phase 1: wait up to 500 ms for the drive to clear TARGET_REACHED
        # (indicates it has accepted the setpoint and started moving).
        t_clear = time.monotonic() + 0.5
        while time.monotonic() < t_clear:
            if not self.is_target_reached():
                break
            time.sleep(0.02)
        # Phase 2: wait for TARGET_REACHED to go high (motion complete).
        while time.monotonic() < deadline:
            if self.is_target_reached():
                return
            if self.is_fault():
                raise RuntimeError("Fault during position move")
            time.sleep(0.05)
        raise TimeoutError("Position move timed out")
