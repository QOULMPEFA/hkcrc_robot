"""Lifting platform controller — wraps stepper_motor_sdk.

Mirrors the logic in stepper_motor_sdk/examples/lift_control.py but
exposes a clean interface that robot.py and the web GUI can call.
"""

import logging
import sys
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

# Allow running from the repo root without installing the SDK
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "stepper_motor_sdk"))
from stepper_motor_sdk import CANopenNode, StepperMotor, HomingMethod
from stepper_motor_sdk.cia402 import DriveState

log = logging.getLogger(__name__)


# ── data types ────────────────────────────────────────────────────────────────

@dataclass
class LiftLogEntry:
    timestamp: float
    level: str         # "info" | "warn" | "error"
    message: str


@dataclass
class LiftState:
    height_mm: float = 0.0
    velocity_mm_s: float = 0.0
    torque_permille: int = 0        # 1000 = 100 % rated
    is_homed: bool = False
    is_fault: bool = False
    is_moving: bool = False
    min_mm: float = 0.0
    max_mm: float = 500.0
    zero_offset_mm: float = 0.0
    statusword: str = ""            # hex statusword for CAN diagnostics
    drive_state: str = ""           # decoded CiA 402 state name
    logs: list = field(default_factory=list)  # last N LiftLogEntry dicts
    timestamp: float = field(default_factory=time.time)


# ── controller ────────────────────────────────────────────────────────────────

class LiftController:
    """
    High-level controller for the lifting platform.

    Kinematics (defaults match lift_control.py):
      height_mm = pulses / (PULSES_PER_REV * GEARBOX_RATIO * PULLEY_RATIO / LEADSCREW_LEAD)
    """

    # Mechanical parameters — adjust to match hardware
    GEARBOX_RATIO   = 10.0      # motor-to-screw reduction
    PULLEY_RATIO    = 1.0       # belt/pulley ratio
    LEADSCREW_LEAD  = 8.0       # mm per screw revolution
    PULSES_PER_REV  = 1000      # encoder pulses per motor revolution

    # Safety limits (runtime-adjustable via set_limits / set_zero)
    MAX_HEIGHT_MM   = 500.0
    MIN_HEIGHT_MM   = 0.0
    STALL_TORQUE    = 800       # permille; 800 = 80 % rated

    # Motion defaults
    DEFAULT_SPEED_MM_S  = 10.0
    DEFAULT_ACCEL       = 50_000    # Pul/s²

    LOG_CAPACITY = 80    # ring-buffer size for lift operation log

    def __init__(self, node: CANopenNode):
        self._motor = StepperMotor(node, pulses_per_rev=self.PULSES_PER_REV)
        self._state = LiftState()
        self._lock = threading.Lock()
        self._stop_monitor = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None
        self._logs: list[LiftLogEntry] = []    # ring buffer

        # Runtime soft limits — copied from class defaults, can be changed live
        self._min_mm: float = self.MIN_HEIGHT_MM
        self._max_mm: float = self.MAX_HEIGHT_MM
        # Zero offset: physical_mm = motor_mm - _zero_offset_mm
        # When set_zero() is called, current motor position becomes 0 in user space
        self._zero_offset_mm: float = 0.0

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def setup(self, stall_torque: int = STALL_TORQUE) -> None:
        """Enable drive and start torque monitor."""
        self._add_log("info", "升降平台初始化开始")
        try:
            log.info("[Lift] Initialising drive ...")
            with self._lock:
                self._state.is_fault = False
            self.STALL_TORQUE = stall_torque
            self._motor.enable()
            self._start_monitor()
            sw = self._motor.get_status()
            self._add_log("info", f"驱动器已使能 — 状态: {sw['state']}, SW=0x{sw['raw']}")
        except Exception as e:
            self._add_log("error", f"驱动器初始化失败: {e}")
            raise

    def shutdown(self) -> None:
        """Stop monitor and release torque."""
        self._add_log("info", "升降平台关闭")
        self._stop_monitor.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=1.0)
        self._motor.shutdown()
        log.info("[Lift] Shutdown complete")

    # ── homing ────────────────────────────────────────────────────────────────

    def home(self, strategy: str = "current_pos") -> bool:
        """
        Establish the coordinate origin.

        strategy:
          "current_pos"   — set current position as zero (debug / no sensor)
          "limit_switch"  — descend until bottom limit switch triggers (DI wired)
          "mechanical"    — descend until stall (no sensor, slow speed)
        """
        if self._is_faulted():
            return False

        self._clear_fault_if_needed()

        fast_spd = self._speed_to_puls(30)
        slow_spd = self._speed_to_puls(5)

        if strategy == "limit_switch":
            log.info("[Lift] Homing: descending to bottom limit switch ...")
            self._motor.set_homing_mode(
                method=HomingMethod.HOME_SWITCH_NEG,
                fast_speed=fast_spd,
                slow_speed=slow_spd,
                acceleration=self.DEFAULT_ACCEL,
                home_offset=0,
            )
        elif strategy == "mechanical":
            log.info("[Lift] Homing: descending to mechanical stop ...")
            self._motor.set_homing_mode(
                method=HomingMethod.MECHANICAL_NEG,
                fast_speed=slow_spd,
                slow_speed=slow_spd,
                acceleration=self.DEFAULT_ACCEL,
                home_offset=0,
            )
        else:
            log.info("[Lift] Homing: current position set as origin")
            self._motor.set_homing_mode(
                method=HomingMethod.CURRENT_POSITION,
                home_offset=0,
            )

        try:
            self._motor.home(wait=True, timeout=30.0)
            with self._lock:
                self._state.is_homed = True
                self._state.height_mm = 0.0
            self._add_log("info", f"回零完成 (策略: {strategy})")
            log.info("[Lift] Homing complete")
            return True
        except (RuntimeError, TimeoutError) as e:
            self._add_log("error", f"回零失败: {e}")
            log.error("[Lift] Homing failed: %s", e)
            with self._lock:
                self._state.is_fault = True
            return False

    # ── motion ────────────────────────────────────────────────────────────────

    def move_to(self, target_mm: float, speed_mm_s: float = DEFAULT_SPEED_MM_S) -> bool:
        """Move platform to absolute height (mm, user space). Returns False on fault."""
        if self._is_faulted():
            self._add_log("warn", "驱动器故障状态，尝试清除...")
            self._clear_fault_if_needed()
            try:
                self._motor.enable()
                with self._lock:
                    self._state.is_fault = False
            except Exception as e:
                self._add_log("error", f"无法清除故障: {e}")
                log.error("[Lift] Cannot move: system faulted")
                return False

        # Re-enable if drive left Operation Enabled (e.g. after quick_stop)
        try:
            ds = self._motor.get_state()
            if ds != DriveState.OPERATION_ENABLED:
                self._add_log("info", f"驱动器未使能 ({ds.value})，正在重新使能...")
                self._motor.enable()
        except Exception:
            pass   # if we can't read state, just try the move anyway

        with self._lock:
            min_mm = self._min_mm
            max_mm = self._max_mm
            offset = self._zero_offset_mm

        target_mm = max(min_mm, min(max_mm, target_mm))
        # Convert user-space mm → motor-space pulses
        target_puls = self._mm_to_puls(target_mm + offset)
        speed_puls = int(
            (speed_mm_s / self.LEADSCREW_LEAD) * self.GEARBOX_RATIO * self.PULLEY_RATIO * self.PULSES_PER_REV
        )

        log.info("[Lift] Moving to %.1f mm (%d puls)", target_mm, target_puls)
        with self._lock:
            self._state.is_moving = True

        try:
            self._motor.set_position_mode(
                profile_velocity=max(1, speed_puls),
                acceleration=self.DEFAULT_ACCEL,
                deceleration=self.DEFAULT_ACCEL,
            )
            self._add_log("info", f"开始移动 → {target_mm:.1f} mm (速度 {speed_mm_s:.0f} mm/s)")
            self._motor.move_absolute(target_puls, wait=True, timeout=60.0)
            with self._lock:
                self._state.height_mm = target_mm
                self._state.is_moving = False
            self._add_log("info", f"移动完成 → {target_mm:.1f} mm")
            return True
        except (RuntimeError, TimeoutError) as e:
            self._add_log("error", f"移动失败 (目标 {target_mm:.1f} mm): {e}")
            log.error("[Lift] Move failed: %s", e)
            with self._lock:
                self._state.is_moving = False
                self._state.is_fault = True
            return False

    def move_to_async(self, target_mm: float, speed_mm_s: float = DEFAULT_SPEED_MM_S) -> None:
        """Non-blocking version of move_to."""
        t = threading.Thread(
            target=self.move_to, args=(target_mm, speed_mm_s), daemon=True
        )
        t.start()

    def stop(self) -> None:
        self._add_log("warn", "升降平台急停")
        self._motor.quick_stop()
        with self._lock:
            self._state.is_moving = False

    def reset_fault(self) -> None:
        self._add_log("info", "清除故障并重新使能")
        self._motor.reset_fault()
        self._motor.enable()
        with self._lock:
            self._state.is_fault = False

    # ── zero & limits ─────────────────────────────────────────────────────────

    def set_zero(self) -> None:
        """Set current physical position as user-space zero (height = 0 mm)."""
        try:
            raw_puls = self._motor.get_position()
        except Exception as e:
            log.error("[Lift] set_zero: cannot read position: %s", e)
            return
        raw_mm = self._puls_to_mm(raw_puls)
        with self._lock:
            self._zero_offset_mm = raw_mm
            self._state.height_mm = 0.0
            self._state.is_homed = True
        log.info("[Lift] Zero set: raw=%.1f mm is now 0 mm (offset=%.1f)", raw_mm, raw_mm)

    def set_limits(self, min_mm: float, max_mm: float) -> None:
        """Update soft travel limits (mm, in user space)."""
        if min_mm >= max_mm:
            log.error("[Lift] set_limits: min (%.1f) must be < max (%.1f)", min_mm, max_mm)
            return
        with self._lock:
            self._min_mm = float(min_mm)
            self._max_mm = float(max_mm)
        log.info("[Lift] Limits updated: %.1f – %.1f mm", min_mm, max_mm)

    def get_limits(self) -> dict:
        with self._lock:
            return {
                "min_mm": self._min_mm,
                "max_mm": self._max_mm,
                "zero_offset_mm": self._zero_offset_mm,
            }

    # ── state ─────────────────────────────────────────────────────────────────

    def get_state(self) -> LiftState:
        pos_puls = 0
        vel_puls = 0
        torque = 0
        sw_info = {}
        errors = []

        try:
            pos_puls = self._motor.get_position()
        except Exception as e:
            errors.append(f"position: {e}")
        try:
            vel_puls = self._motor.get_velocity()
        except Exception as e:
            errors.append(f"velocity: {e}")
        try:
            torque = self._motor.get_torque()
        except Exception as e:
            errors.append(f"torque: {e}")
        try:
            sw_info = self._motor.get_status()
        except Exception as e:
            errors.append(f"status: {e}")

        if errors and getattr(self, '_last_sdo_errors', '') != str(errors):
            self._add_log("warn", "SDO 读取部分失败: " + ", ".join(errors))
            self._last_sdo_errors = str(errors)   # dedupe across cycles

        with self._lock:
            ds = sw_info.get("state", None)
            self._state.height_mm       = self._puls_to_mm(pos_puls) - self._zero_offset_mm
            self._state.velocity_mm_s   = self._puls_to_speed(vel_puls)
            self._state.torque_permille = torque
            self._state.min_mm          = self._min_mm
            self._state.max_mm          = self._max_mm
            self._state.zero_offset_mm  = self._zero_offset_mm
            self._state.statusword      = sw_info.get("raw", "")
            self._state.drive_state     = ds.value if isinstance(ds, DriveState) else str(ds or "")
            self._state.logs            = [{
                "ts": e.timestamp,
                "level": e.level,
                "msg": e.message,
            } for e in self._logs[-30:]]   # only last 30 entries for state broadcast
            self._state.timestamp       = time.time()
            return LiftState(**self._state.__dict__)

    # ── kinematics ────────────────────────────────────────────────────────────

    def _mm_to_puls(self, mm: float) -> int:
        screw_revs  = mm / self.LEADSCREW_LEAD
        motor_revs  = screw_revs * self.GEARBOX_RATIO * self.PULLEY_RATIO
        return int(motor_revs * self.PULSES_PER_REV)

    def _puls_to_mm(self, puls: int) -> float:
        motor_revs  = puls / self.PULSES_PER_REV
        screw_revs  = motor_revs / (self.GEARBOX_RATIO * self.PULLEY_RATIO)
        return screw_revs * self.LEADSCREW_LEAD

    def _speed_to_puls(self, rpm: float) -> int:
        return int(rpm / 60.0 * self.PULSES_PER_REV)

    def _puls_to_speed(self, puls_s: int) -> float:
        rpm = puls_s * 60.0 / self.PULSES_PER_REV
        return (rpm / 60.0) / (self.GEARBOX_RATIO * self.PULLEY_RATIO) * self.LEADSCREW_LEAD

    def _add_log(self, level: str, message: str) -> None:
        entry = LiftLogEntry(timestamp=time.time(), level=level, message=message)
        with self._lock:
            self._logs.append(entry)
            if len(self._logs) > self.LOG_CAPACITY:
                self._logs = self._logs[-self.LOG_CAPACITY:]

    # ── torque monitor ────────────────────────────────────────────────────────

    def _start_monitor(self) -> None:
        self._stop_monitor.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="lift-monitor"
        )
        self._monitor_thread.start()

    def _monitor_loop(self) -> None:
        stall_start: Optional[float] = None
        log.debug("[Lift] Torque monitor active")
        comm_fail_count = 0
        _last_monitor_err = ""
        while not self._stop_monitor.is_set():
            try:
                torque = abs(self._motor.get_torque())
                comm_fail_count = 0
                if torque > self.STALL_TORQUE:
                    if stall_start is None:
                        stall_start = time.time()
                    if (time.time() - stall_start) > 0.150:
                        self._add_log("error", f"堵转检测: 转矩 {torque}‰ 超过阈值 {self.STALL_TORQUE}‰")
                        self._emergency_stop("STALL_DETECTED")
                else:
                    stall_start = None
            except Exception as e:
                comm_fail_count += 1
                err_str = str(e)
                if comm_fail_count == 1 and err_str != _last_monitor_err:
                    self._add_log("warn", f"转矩读取失败: {err_str}")
                    _last_monitor_err = err_str
                elif comm_fail_count == 50:    # ~1 s
                    with self._lock:
                        self._state.is_fault = True
                log.debug("[Lift] Monitor comm error: %s", e)
            time.sleep(0.02)

    def _emergency_stop(self, reason: str) -> None:
        self._add_log("error", f"紧急停止: {reason}")
        log.error("[Lift] !!! EMERGENCY STOP: %s !!!", reason)
        self._motor.quick_stop()
        with self._lock:
            self._state.is_fault = True
            self._state.is_moving = False

    def _is_faulted(self) -> bool:
        with self._lock:
            return self._state.is_fault

    def _clear_fault_if_needed(self) -> None:
        try:
            if self._motor.is_fault():
                log.info("[Lift] Clearing drive fault ...")
                self._motor.reset_fault()
                self._motor.enable()
        except Exception:
            pass
