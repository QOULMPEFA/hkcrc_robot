"""Gimbal camera module — 2-axis servo gimbal (Fashion Star UART servos).

Architecture:
  PC / Jetson  ──USB──> CH340 UART ──> Servo Bus (ID 0: Yaw, ID 1: Pitch)

Based on gimbal_sdk/servo_lib.py and STM32 gimbal reference code.
Servo protocol: Fashion Star UART Servo, 115200 baud, binary framed packets.

Calibration:
  Linear mapping between logical gimbal angles (yaw/pitch) and raw servo angles.
  Two calibration points per axis, computed at init from config or defaults.

Usage (standalone test):
  python -m robot_control.gimbal

Usage (robot system):
  config = {"gimbal": {"yaw_limits": [-90, 90], "pitch_limits": [-90, 60]}}
  with Robot(config) as robot: ...
"""

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from robot_control.module_base import EmitFn, EventHandler, RobotModule

log = logging.getLogger(__name__)

# ── calibration helpers ─────────────────────────────────────────────────────────

def _compute_calibration(points: List[Tuple[float, float]]) -> Tuple[float, float]:
    """Compute linear mapping: servo = K * gimbal + B from two calibration points.

    points: [(gimbal_angle1, servo_angle1), (gimbal_angle2, servo_angle2)]
    Returns: (K, B) such that servo_angle = K * gimbal_angle + B
    """
    (g1, s1), (g2, s2) = points
    delta_g = g2 - g1
    if abs(delta_g) < 0.001:
        return 1.0, 0.0
    K = (s2 - s1) / delta_g
    B = s1 - K * g1
    return K, B


def _apply_calibration(gimbal_angle: float, K: float, B: float) -> float:
    return K * gimbal_angle + B


def _invert_calibration(servo_angle: float, K: float, B: float) -> float:
    if abs(K) < 0.0001:
        return 0.0
    return (servo_angle - B) / K


# ── default calibration from STM32 gimbal.h reference ───────────────────────────

DEFAULT_YAW_CAL = [(90.0, 90.4), (-90.0, -90.0)]
DEFAULT_PITCH_CAL = [(0.0, -2.4), (-90.0, -92.6)]


class GimbalModule(RobotModule):
    """2-axis camera gimbal module using Fashion Star UART servos.

    Servo IDs: yaw=0, pitch=1 (configurable).
    Auto-detects CH340 USB-UART on start.
    """

    name = "gimbal"
    display_name = "云台相机"

    def __init__(self, config: Optional[Dict[str, Any]] = None, robot=None):
        super().__init__(config, robot)
        cfg = self.config

        # servo IDs
        self._ids = cfg.get("servo_ids", {"yaw": 0, "pitch": 1})

        # angle limits (logical gimbal angles, not servo angles)
        self._yaw_limits: Tuple[float, float] = tuple(cfg.get("yaw_limits", [-90, 90]))
        self._pitch_limits: Tuple[float, float] = tuple(cfg.get("pitch_limits", [-90, 60]))

        # home position (configurable origin, not hardcoded 0,0)
        self._home_yaw: float = float(cfg.get("home_yaw", 0.0))
        self._home_pitch: float = float(cfg.get("home_pitch", 0.0))

        # motion parameters
        self._velocity = cfg.get("velocity", 200)    # deg/s
        self._power = cfg.get("power", 3000)          # mW

        # calibration
        yaw_cal = cfg.get("calibration", {}).get("yaw", DEFAULT_YAW_CAL)
        pitch_cal = cfg.get("calibration", {}).get("pitch", DEFAULT_PITCH_CAL)
        self._K_yaw, self._B_yaw = _compute_calibration(yaw_cal)
        self._K_pitch, self._B_pitch = _compute_calibration(pitch_cal)

        # runtime state
        self._target_yaw: float = 0.0
        self._target_pitch: float = 0.0
        self._current_yaw: float = 0.0
        self._current_pitch: float = 0.0
        self._connected = False
        self._is_fault = False
        self._fault_text = ""

        # internals
        self._controller = None       # ServoController instance
        self._lock = threading.Lock()
        self._poll_thread: Optional[threading.Thread] = None
        self._running = False

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        try:
            from gimbal_sdk.servo_lib import ServoController
            self._controller = ServoController()
            self._connected = True
            self._is_fault = False
            self._fault_text = ""

            # Reset multi-turn angle counters to avoid accumulated values
            for sid in self._ids.values():
                try:
                    self._controller.uservo.reset_multi_turn_angle(sid)
                except Exception:
                    pass

            # read current positions
            self._current_yaw = self._read_servo_angle(self._ids["yaw"]) or 0.0
            self._current_pitch = self._read_servo_angle(self._ids["pitch"]) or 0.0
            self._target_yaw = self._current_yaw
            self._target_pitch = self._current_pitch

            log.info("[Gimbal] Connected — yaw=%.1f°, pitch=%.1f°",
                     self._current_yaw, self._current_pitch)
        except Exception as e:
            self._connected = False
            self._is_fault = True
            self._fault_text = str(e)
            log.warning("[Gimbal] Init failed: %s", e)

        self._running = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True,
                                             name="gimbal-poll")
        self._poll_thread.start()

    def stop(self) -> None:
        self._running = False
        if self._poll_thread:
            self._poll_thread.join(timeout=2)
        self._connected = False

    # ── emergency ─────────────────────────────────────────────────────────────

    def emergency_stop(self) -> None:
        """Immediately release servo torque."""
        log.warning("[Gimbal] EMERGENCY STOP — releasing torque")
        self._is_fault = True
        self._fault_text = "emergency_stop"
        try:
            if self._controller:
                for sid in self._ids.values():
                    self._controller.uservo.wheel_stop(sid)
        except Exception as e:
            log.warning("[Gimbal] E-stop error: %s", e)

    # ── motion commands ───────────────────────────────────────────────────────

    def move_to(self, yaw: float, pitch: float) -> bool:
        """Move gimbal to absolute yaw/pitch (logical angles in degrees).

        Returns False if the target is out of bounds or not connected.
        """
        if not self._connected or self._is_fault:
            log.warning("[Gimbal] move_to rejected: connected=%s fault=%s",
                        self._connected, self._is_fault)
            return False

        yaw = self._clamp_yaw(yaw)
        pitch = self._clamp_pitch(pitch)

        with self._lock:
            self._target_yaw = yaw
            self._target_pitch = pitch

        s_yaw = _apply_calibration(yaw, self._K_yaw, self._B_yaw)
        s_pitch = _apply_calibration(pitch, self._K_pitch, self._B_pitch)

        try:
            self._controller.uservo.set_servo_angle(
                self._ids["yaw"], s_yaw,
                velocity=self._velocity, power=self._power)
            self._controller.uservo.set_servo_angle(
                self._ids["pitch"], s_pitch,
                velocity=self._velocity, power=self._power)
            log.info("[Gimbal] move_to yaw=%.1f° pitch=%.1f° (servo: %.1f, %.1f)",
                     yaw, pitch, s_yaw, s_pitch)
            return True
        except Exception as e:
            log.error("[Gimbal] move_to failed: %s", e)
            self._is_fault = True
            self._fault_text = str(e)
            return False

    def move_by(self, dyaw: float = 0.0, dpitch: float = 0.0) -> bool:
        """Relative move from current target."""
        # NOTE: no lock here — move_to() acquires _lock internally.
        # Holding _lock across the call would deadlock (threading.Lock is not reentrant).
        return self.move_to(self._target_yaw + dyaw, self._target_pitch + dpitch)

    def home(self) -> bool:
        """Return to configured home position."""
        log.info("[Gimbal] Homing to (%.1f°, %.1f°)...", self._home_yaw, self._home_pitch)
        return self.move_to(self._home_yaw, self._home_pitch)

    def set_home(self) -> bool:
        """Set the current position as the new home origin.

        Rejects if the current angle reading is outside configured limits
        (i.e. a garbage/sensor-noise reading).
        """
        with self._lock:
            yaw = self._current_yaw
            pitch = self._current_pitch
        # Validate against limits (with small hysteresis margin)
        yaw_ok = self._yaw_limits[0] - 5 <= yaw <= self._yaw_limits[1] + 5
        pitch_ok = self._pitch_limits[0] - 5 <= pitch <= self._pitch_limits[1] + 5
        if not yaw_ok or not pitch_ok:
            log.warning("[Gimbal] set_home rejected: angle out of range (yaw=%.1f, pitch=%.1f)",
                        yaw, pitch)
            return False
        self._home_yaw = yaw
        self._home_pitch = pitch
        log.info("[Gimbal] Home set to (%.1f°, %.1f°)", self._home_yaw, self._home_pitch)
        return True

    def damping(self) -> bool:
        """Set both servos to damping mode (hold position with low power)."""
        if not self._connected:
            return False
        try:
            for sid in self._ids.values():
                self._controller.uservo.set_damping(sid, power=self._power)
            log.info("[Gimbal] Damping mode set")
            return True
        except Exception as e:
            log.error("[Gimbal] Damping failed: %s", e)
            return False

    # ── calibration ───────────────────────────────────────────────────────────

    def set_calibration(self, yaw_points=None, pitch_points=None) -> None:
        """Update calibration from two-point pairs."""
        if yaw_points:
            self._K_yaw, self._B_yaw = _compute_calibration(yaw_points)
            log.info("[Gimbal] Yaw cal updated: K=%.4f B=%.2f", self._K_yaw, self._B_yaw)
        if pitch_points:
            self._K_pitch, self._B_pitch = _compute_calibration(pitch_points)
            log.info("[Gimbal] Pitch cal updated: K=%.4f B=%.2f",
                     self._K_pitch, self._B_pitch)

    def calibrate_at(self, gimbal_yaw: float, gimbal_pitch: float) -> Dict[str, Any]:
        """Record current raw servo angles as the calibration point for given logical angles.

        Call this after manually positioning the gimbal to known angles.
        """
        s_yaw = self._read_servo_angle(self._ids["yaw"])
        s_pitch = self._read_servo_angle(self._ids["pitch"])
        if s_yaw is None or s_pitch is None:
            log.warning("[Gimbal] calibrate_at: servo reading failed")
            return {"error": "servo_read_failed"}
        return {
            "gimbal_yaw": gimbal_yaw, "servo_yaw": s_yaw,
            "gimbal_pitch": gimbal_pitch, "servo_pitch": s_pitch,
        }

    # ── state ─────────────────────────────────────────────────────────────────

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "connected": self._connected,
                "yaw_deg": round(self._current_yaw, 1),
                "pitch_deg": round(self._current_pitch, 1),
                "target_yaw_deg": round(self._target_yaw, 1),
                "target_pitch_deg": round(self._target_pitch, 1),
                "home_yaw_deg": round(self._home_yaw, 1),
                "home_pitch_deg": round(self._home_pitch, 1),
                "rel_yaw_deg": round(self._current_yaw - self._home_yaw, 1),
                "rel_pitch_deg": round(self._current_pitch - self._home_pitch, 1),
                "is_fault": self._is_fault,
                "fault_text": self._fault_text,
            }

    def get_state_schema(self) -> Dict[str, Any]:
        return {
            "connected": False,
            "yaw_deg": 0.0,
            "pitch_deg": 0.0,
            "target_yaw_deg": 0.0,
            "target_pitch_deg": 0.0,
            "home_yaw_deg": 0.0,
            "home_pitch_deg": 0.0,
            "rel_yaw_deg": 0.0,
            "rel_pitch_deg": 0.0,
            "is_fault": False,
            "fault_text": "",
        }

    # ── SocketIO handlers ─────────────────────────────────────────────────────

    def get_socketio_handlers(self) -> Dict[str, EventHandler]:
        return {
            "gimbal_move_to": self._on_move_to,
            "gimbal_move_by": self._on_move_by,
            "gimbal_home": self._on_home,
            "gimbal_set_home": self._on_set_home,
            "gimbal_damping": self._on_damping,
        }

    def _on_move_to(self, data: dict, emit: EmitFn) -> None:
        missing = self._require_keys(data, "yaw", "pitch")
        if missing:
            emit("error", {"msg": f"Missing keys: {missing}"})
            return
        ok = self.move_to(float(data["yaw"]), float(data["pitch"]))
        emit("gimbal_move_result", {"ok": ok})

    def _on_move_by(self, data: dict, emit: EmitFn) -> None:
        log.info("[Gimbal] _on_move_by data=%s", data)
        dyaw = float(data.get("dyaw", 0))
        dpitch = float(data.get("dpitch", 0))
        ok = self.move_by(dyaw, dpitch)
        emit("gimbal_move_result", {"ok": ok})

    def _on_home(self, _data: dict, emit: EmitFn) -> None:
        ok = self.home()
        emit("gimbal_move_result", {"ok": ok})

    def _on_set_home(self, _data: dict, emit: EmitFn) -> None:
        ok = self.set_home()
        emit("gimbal_move_result", {"ok": ok})

    def _on_damping(self, _data: dict, emit: EmitFn) -> None:
        ok = self.damping()
        emit("gimbal_move_result", {"ok": ok})

    # ── internals ─────────────────────────────────────────────────────────────

    def _clamp_yaw(self, v: float) -> float:
        return max(self._yaw_limits[0], min(self._yaw_limits[1], v))

    def _clamp_pitch(self, v: float) -> float:
        return max(self._pitch_limits[0], min(self._pitch_limits[1], v))

    def _read_servo_angle(self, servo_id: int) -> Optional[float]:
        """Read raw servo angle and convert to logical gimbal angle.

        Returns the logical angle, or None if the reading is out of range (noise).
        """
        if not self._controller:
            return None
        try:
            raw = self._controller.uservo.query_servo_angle(servo_id)
            # Normalize multi-turn accumulated angle to single-turn [-180, 180]
            raw = ((raw + 180.0) % 360.0) - 180.0
            if servo_id == self._ids["yaw"]:
                angle = _invert_calibration(raw, self._K_yaw, self._B_yaw)
                lo, hi = self._yaw_limits
            else:
                angle = _invert_calibration(raw, self._K_pitch, self._B_pitch)
                lo, hi = self._pitch_limits
            # Reject garbage readings far outside configured limits
            margin = 25.0
            if angle < lo - margin or angle > hi + margin:
                log.debug("[Gimbal] Rejected outlier: servo %d raw=%.1f → logical=%.1f (limits [%.0f, %.0f])",
                          servo_id, raw, angle, lo, hi)
                return None
            return angle
        except Exception:
            return None

    def _poll_loop(self) -> None:
        """Background thread: read angles at ~5 Hz."""
        while self._running:
            try:
                if self._controller and self._connected:
                    yaw = self._read_servo_angle(self._ids["yaw"])
                    pitch = self._read_servo_angle(self._ids["pitch"])
                    with self._lock:
                        # Delta check: reject jumps > 60° (servo max 200°/s → 40°/tick at 5 Hz)
                        if yaw is not None and (self._current_yaw == 0.0 or abs(yaw - self._current_yaw) <= 60.0):
                            self._current_yaw = yaw
                        elif yaw is not None:
                            log.debug("[Gimbal] Rejected yaw jump: %.1f → %.1f", self._current_yaw, yaw)
                        if pitch is not None and (self._current_pitch == 0.0 or abs(pitch - self._current_pitch) <= 60.0):
                            self._current_pitch = pitch
                        elif pitch is not None:
                            log.debug("[Gimbal] Rejected pitch jump: %.1f → %.1f", self._current_pitch, pitch)
            except Exception as e:
                log.debug("[Gimbal] Poll error: %s", e)
            time.sleep(0.2)


# ── standalone test ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s")

    print("=== Gimbal Camera Module Test ===")
    print("Commands: move_to Y P | move_by DY DP | home | damping | state | quit")
    print("")

    module = GimbalModule({
        "yaw_limits": [-90, 90],
        "pitch_limits": [-90, 60],
    })
    module.start()

    try:
        while True:
            cmd = input("> ").strip().split()
            if not cmd:
                continue
            if cmd[0] == "quit" or cmd[0] == "q":
                break
            elif cmd[0] == "move_to" and len(cmd) == 3:
                module.move_to(float(cmd[1]), float(cmd[2]))
            elif cmd[0] == "move_by" and len(cmd) == 3:
                module.move_by(float(cmd[1]), float(cmd[2]))
            elif cmd[0] == "home":
                module.home()
            elif cmd[0] == "damping":
                module.damping()
            elif cmd[0] == "state":
                print(module.get_state())
            elif cmd[0] == "calibrate" and len(cmd) == 3:
                result = module.calibrate_at(float(cmd[1]), float(cmd[2]))
                print(f"Calibration point: {result}")
            else:
                print(f"Unknown: {cmd}")
    except KeyboardInterrupt:
        pass
    finally:
        module.stop()
        print("Done.")
