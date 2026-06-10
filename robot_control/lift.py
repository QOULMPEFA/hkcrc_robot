"""Lifting platform controller — wraps stepper_motor_sdk.

Mirrors the logic in stepper_motor_sdk/examples/lift_control.py but exposes a
clean RobotModule interface that robot.py and the web GUI can call.
"""

import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from robot_control.module_base import RobotModule

log = logging.getLogger(__name__)


@dataclass
class LiftLogEntry:
    timestamp: float
    level: str
    message: str


@dataclass
class LiftState:
    height_mm: float = 0.0
    velocity_mm_s: float = 0.0
    torque_permille: int = 0
    is_homed: bool = False
    is_fault: bool = False
    is_moving: bool = False
    min_mm: float = 0.0
    max_mm: float = 500.0
    zero_offset_mm: float = 0.0
    statusword: str = ""
    drive_state: str = ""
    connected: bool = False
    logs: list = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


class LiftController(RobotModule):
    """High-level controller for the lifting platform (stepper motor + CANopen)."""

    # Mechanical parameters
    GEARBOX_RATIO   = 10.0
    PULLEY_RATIO    = 1.0
    LEADSCREW_LEAD  = 8.0       # mm/rev
    PULSES_PER_REV  = 1000

    MAX_HEIGHT_MM   = 500.0
    MIN_HEIGHT_MM   = 0.0
    STALL_TORQUE    = 800        # permille

    DEFAULT_SPEED_MM_S  = 10.0
    DEFAULT_ACCEL       = 50_000   # Pul/s^2

    LOG_CAPACITY = 80

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        self._can_channel = cfg.get("can_channel", "can0")
        self._node_id = int(cfg.get("node_id", 1))
        self._home_strategy = cfg.get("home_strategy", "current_pos")

        self._node = None
        self._motor = None
        self._state = LiftState()
        self._lock = threading.Lock()
        self._stop_monitor = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None
        self._logs: list[LiftLogEntry] = []
        self._min_mm: float = self.MIN_HEIGHT_MM
        self._max_mm: float = self.MAX_HEIGHT_MM
        self._zero_offset_mm: float = 0.0
        self._started = False

    # ── RobotModule metadata ───────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "lift"

    @property
    def display_name(self) -> str:
        return "升降平台"

    def get_state_schema(self) -> Dict[str, Any]:
        return {
            "height_mm": 0.0, "velocity_mm_s": 0.0, "torque_permille": 0,
            "is_homed": False, "is_fault": False, "is_moving": False,
            "min_mm": 0.0, "max_mm": 500.0, "zero_offset_mm": 0.0,
            "statusword": "", "drive_state": "", "connected": False,
            "timestamp": 0.0, "logs": [],
        }

    def get_socketio_handlers(self) -> Dict[str, callable]:
        return {
            "lift_move": self._on_move,
            "lift_stop": self._on_stop,
            "lift_home": self._on_home,
            "lift_reset_fault": self._on_reset_fault,
            "lift_set_zero": self._on_set_zero,
            "lift_set_limits": self._on_set_limits,
        }

    # ── lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._started:
            return

        # Lazy-import stepper_motor_sdk — only available when installed
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "stepper_motor_sdk"))
        try:
            from stepper_motor_sdk import CANopenNode, StepperMotor, HomingMethod
            self._CANopenNode = CANopenNode
            self._StepperMotor = StepperMotor
            self._HomingMethod = HomingMethod
            from stepper_motor_sdk.cia402 import DriveState as _DriveState
            self._DriveState = _DriveState
        except ImportError as e:
            log.warning("[Lift] stepper_motor_sdk not available — stub mode: %s", e)
            self._started = True
            self._state.connected = False
            self._add_log("warn", "stepper_motor_sdk 未安装，升降平台处于待机模式")
            return

        try:
            self._node = self._CANopenNode(channel=self._can_channel, node_id=self._node_id)
            self._node.start()
            self._motor = self._StepperMotor(self._node, pulses_per_rev=self.PULSES_PER_REV)
            self.setup(stall_torque=self.STALL_TORQUE)
            self.home(strategy=self._home_strategy)
        except Exception as e:
            log.warning("[Lift] Motor init failed — stub mode: %s", e)
            self._add_log("warn", f"电机初始化失败: {e} — 待机模式（检查 CAN 连线和电机电源）")
            self._cleanup_node()
            self._started = True
            self._state.connected = False
            return

        self._started = True

    def _cleanup_node(self):
        try:
            if self._node:
                self._node.stop()
        except Exception:
            pass
        self._node = None
        self._motor = None

    def stop(self) -> None:
        try:
            self._stop_monitor.set()
            if self._monitor_thread:
                self._monitor_thread.join(timeout=1.0)
            if self._motor:
                self._motor.shutdown()
        except Exception as e:
            log.warning("[Lift] Stop error (ignored): %s", e)
        try:
            if self._node:
                self._node.stop()
        except Exception:
            pass
        self._started = False
        log.info("[Lift] Stopped")

    # ── setup / shutdown ───────────────────────────────────────────────────

    def setup(self, stall_torque: int = STALL_TORQUE) -> None:
        self._add_log("info", "升降平台初始化开始")
        with self._lock:
            self._state.is_fault = False
            self._state.connected = True
        self.STALL_TORQUE = stall_torque
        self._motor.enable()
        self._start_monitor()
        sw = self._motor.get_status()
        self._add_log("info", f"驱动器已使能 — 状态: {sw['state']}, SW=0x{sw['raw']}")

    def shutdown(self) -> None:
        self._add_log("info", "升降平台关闭")
        self._stop_monitor.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=1.0)
        self._motor.shutdown()
        with self._lock:
            self._state.connected = False
        log.info("[Lift] Shutdown complete")

    # ── homing ─────────────────────────────────────────────────────────────

    def home(self, strategy: str = "current_pos") -> bool:
        if self._is_faulted():
            return False
        self._clear_fault_if_needed()

        fast_spd = self._speed_to_puls(30)
        slow_spd = self._speed_to_puls(5)
        hm = self._HomingMethod

        if strategy == "limit_switch":
            log.info("[Lift] Homing: descending to bottom limit switch ...")
            self._motor.set_homing_mode(
                method=hm.HOME_SWITCH_NEG, fast_speed=fast_spd,
                slow_speed=slow_spd, acceleration=self.DEFAULT_ACCEL, home_offset=0)
        elif strategy == "mechanical":
            log.info("[Lift] Homing: descending to mechanical stop ...")
            self._motor.set_homing_mode(
                method=hm.MECHANICAL_NEG, fast_speed=slow_spd,
                slow_speed=slow_spd, acceleration=self.DEFAULT_ACCEL, home_offset=0)
        else:
            log.info("[Lift] Homing: current position set as origin")
            self._motor.set_homing_mode(method=hm.CURRENT_POSITION, home_offset=0)

        try:
            self._motor.home(wait=True, timeout=30.0)
            with self._lock:
                self._state.is_homed = True
                self._state.height_mm = 0.0
            self._add_log("info", f"回零完成 (策略: {strategy})")
            return True
        except (RuntimeError, TimeoutError) as e:
            self._add_log("error", f"回零失败: {e}")
            with self._lock:
                self._state.is_fault = True
            return False

    # ── motion ─────────────────────────────────────────────────────────────

    def move_to(self, target_mm: float, speed_mm_s: float = DEFAULT_SPEED_MM_S) -> bool:
        if not self._motor:
            self._add_log("warn", "电机未连接，无法移动")
            return False
        if self._is_faulted():
            self._add_log("warn", "驱动器故障状态，尝试清除...")
            self._clear_fault_if_needed()
            try:
                self._motor.enable()
                with self._lock:
                    self._state.is_fault = False
            except Exception as e:
                self._add_log("error", f"无法清除故障: {e}")
                return False

        try:
            ds = self._motor.get_state()
            if ds != self._DriveState.OPERATION_ENABLED:
                self._add_log("info", f"驱动器未使能 ({ds.value})，正在重新使能...")
                self._motor.enable()
        except Exception:
            pass

        with self._lock:
            min_mm = self._min_mm
            max_mm = self._max_mm
            offset = self._zero_offset_mm

        target_mm = max(min_mm, min(max_mm, target_mm))
        target_puls = self._mm_to_puls(target_mm + offset)
        speed_puls = int(
            (speed_mm_s / self.LEADSCREW_LEAD) * self.GEARBOX_RATIO
            * self.PULLEY_RATIO * self.PULSES_PER_REV
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
            with self._lock:
                self._state.is_moving = False
                self._state.is_fault = True
            return False

    def move_to_async(self, target_mm: float, speed_mm_s: float = DEFAULT_SPEED_MM_S) -> None:
        threading.Thread(
            target=self.move_to, args=(target_mm, speed_mm_s), daemon=True
        ).start()

    def stop_motion(self) -> None:
        self._add_log("warn", "升降平台急停")
        if self._motor:
            self._motor.quick_stop()
        with self._lock:
            self._state.is_moving = False

    def emergency_stop(self) -> None:
        self.stop_motion()

    def reset_fault(self) -> None:
        if not self._motor:
            return
        self._add_log("info", "清除故障并重新使能")
        self._motor.reset_fault()
        self._motor.enable()
        with self._lock:
            self._state.is_fault = False

    # ── zero & limits ──────────────────────────────────────────────────────

    def set_zero(self) -> None:
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
        log.info("[Lift] Zero set: raw=%.1f mm is now 0 mm", raw_mm)

    def set_limits(self, min_mm: float, max_mm: float) -> None:
        if min_mm >= max_mm:
            log.error("[Lift] set_limits: min (%.1f) must be < max (%.1f)", min_mm, max_mm)
            return
        with self._lock:
            self._min_mm = float(min_mm)
            self._max_mm = float(max_mm)
        log.info("[Lift] Limits updated: %.1f - %.1f mm", min_mm, max_mm)

    def get_limits(self) -> dict:
        with self._lock:
            return {"min_mm": self._min_mm, "max_mm": self._max_mm,
                    "zero_offset_mm": self._zero_offset_mm}

    # ── state ──────────────────────────────────────────────────────────────

    def get_state(self) -> Dict[str, Any]:
        pos_puls = 0; vel_puls = 0; torque = 0
        sw_raw = ""; ds_val = ""
        errors = []

        if self._motor:
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
                sw_raw = sw_info.get("raw", "")
                ds = sw_info.get("state", None)
                ds_val = ds.value if isinstance(ds, self._DriveState) else str(ds or "")
            except Exception as e:
                errors.append(f"status: {e}")

        if errors and getattr(self, '_last_sdo_errors', '') != str(errors):
            self._add_log("warn", "SDO 读取部分失败: " + ", ".join(errors))
            self._last_sdo_errors = str(errors)

        with self._lock:
            self._state.height_mm = self._puls_to_mm(pos_puls) - self._zero_offset_mm
            self._state.velocity_mm_s = self._puls_to_speed(vel_puls)
            self._state.torque_permille = torque
            self._state.min_mm = self._min_mm
            self._state.max_mm = self._max_mm
            self._state.zero_offset_mm = self._zero_offset_mm
            self._state.statusword = sw_raw
            self._state.drive_state = ds_val
            self._state.timestamp = time.time()
            return {
                "height_mm": self._state.height_mm,
                "velocity_mm_s": self._state.velocity_mm_s,
                "torque_permille": self._state.torque_permille,
                "is_homed": self._state.is_homed,
                "is_fault": self._state.is_fault,
                "is_moving": self._state.is_moving,
                "min_mm": self._state.min_mm,
                "max_mm": self._state.max_mm,
                "zero_offset_mm": self._state.zero_offset_mm,
                "statusword": self._state.statusword,
                "drive_state": self._state.drive_state,
                "connected": self._state.connected,
                "timestamp": self._state.timestamp,
                "logs": [{"ts": e.timestamp, "level": e.level, "msg": e.message}
                         for e in self._logs[-30:]],
            }

    # ── SocketIO handlers ─────────────────────────────────────────────────

    def _on_move(self, data: dict, emit=None) -> None:
        self.move_to_async(
            float(data.get("height_mm", 0.0)),
            float(data.get("speed_mm_s", 10.0)),
        )

    def _on_stop(self, _data: dict = None, emit=None) -> None:
        self.stop_motion()

    def _on_home(self, data: dict = None, emit=None) -> None:
        strategy = (data or {}).get("strategy", "current_pos")
        threading.Thread(target=self.home, args=(strategy,), daemon=True).start()

    def _on_reset_fault(self, _data: dict = None, emit=None) -> None:
        self.reset_fault()

    def _on_set_zero(self, _data: dict = None, emit=None) -> None:
        self.set_zero()
        if emit:
            emit("lift_limits", self.get_limits())

    def _on_set_limits(self, data: dict, emit=None) -> None:
        self.set_limits(
            float(data.get("min_mm", 0.0)),
            float(data.get("max_mm", 500.0)),
        )
        if emit:
            emit("lift_limits", self.get_limits())

    # ── kinematics ─────────────────────────────────────────────────────────

    def _mm_to_puls(self, mm: float) -> int:
        screw_revs = mm / self.LEADSCREW_LEAD
        motor_revs = screw_revs * self.GEARBOX_RATIO * self.PULLEY_RATIO
        return int(motor_revs * self.PULSES_PER_REV)

    def _puls_to_mm(self, puls: int) -> float:
        motor_revs = puls / self.PULSES_PER_REV
        screw_revs = motor_revs / (self.GEARBOX_RATIO * self.PULLEY_RATIO)
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

    # ── torque monitor ─────────────────────────────────────────────────────

    def _start_monitor(self) -> None:
        self._stop_monitor.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="lift-monitor")
        self._monitor_thread.start()

    def _monitor_loop(self) -> None:
        stall_start: Optional[float] = None
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
                elif comm_fail_count == 50:
                    with self._lock:
                        self._state.is_fault = True
            time.sleep(0.02)

    def _emergency_stop(self, reason: str) -> None:
        self._add_log("error", f"紧急停止: {reason}")
        log.error("[Lift] EMERGENCY STOP: %s", reason)
        if self._motor:
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
