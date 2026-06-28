"""Pipe robot v2 joint module — waist spread/shrink + pan/tilt control via Fashion Star servos.

Servo map (from reference):
  Pan/Tilt (IDs 1-4): multi-turn, ±300° limit, KK=0.78 coupling
  Waist  (ID 10 tail, ID 20 head): multi-turn angle-mode spread/shrink
    spread: head=2100°, tail=1070°
    shrink: head=850°,  tail=-180°

SocketIO events:
  waist:  joint_waist_spread, joint_waist_shrink, joint_waist_stop
  pt:     joint_pt_up, joint_pt_down, joint_pt_left, joint_pt_right, joint_pt_home, joint_pt_stop
  limits: joint_get_limits, joint_set_limits
"""

import logging
from typing import Any, Dict

from robot_control.module_base import RobotModule

log = logging.getLogger(__name__)

_SHARED_USERVO = None

# ── Defaults from reference code ────────────────────────────────────────────
# Pan/Tilt limits (from pr_v2_sdk/gimbal_lib.py ANGLE_LIMIT)
DEFAULT_H_MIN, DEFAULT_H_MAX = -300, 300
DEFAULT_P_MIN, DEFAULT_P_MAX = -300, 300
# Waist limits (from pr_v2_sdk/reference/main.py spread/shrink angles)
DEFAULT_WAIST_HEAD_MIN, DEFAULT_WAIST_HEAD_MAX = 850, 2100   # ID 20
DEFAULT_WAIST_TAIL_MIN, DEFAULT_WAIST_TAIL_MAX = -180, 1070  # ID 10

WAIST_HEAD_IDS = [20]
WAIST_TAIL_IDS = [10]
WAIST_ALL_IDS = WAIST_HEAD_IDS + WAIST_TAIL_IDS
WAIST_STEP = 30      # degrees per hold-tick
WAIST_DPS = 400      # speed (from reference mean_dps)


class JointModule(RobotModule):
    """Combined joint controller — waist spread/shrink + pan/tilt."""

    def __init__(self, config: dict):
        self._config = config
        self._servo = None
        self._gimbal = None
        self._connected = False
        self._direction = "stop"
        self._h_angle = 0.0
        self._p_angle = 0.0
        # waist current angles (tracked locally; reset to mid-point on start)
        self._waist_head_angle = float(DEFAULT_WAIST_HEAD_MIN + DEFAULT_WAIST_HEAD_MAX) // 2
        self._waist_tail_angle = float(DEFAULT_WAIST_TAIL_MIN + DEFAULT_WAIST_TAIL_MAX) // 2
        # limits
        self._h_min, self._h_max = DEFAULT_H_MIN, DEFAULT_H_MAX
        self._p_min, self._p_max = DEFAULT_P_MIN, DEFAULT_P_MAX
        self._wh_min, self._wh_max = DEFAULT_WAIST_HEAD_MIN, DEFAULT_WAIST_HEAD_MAX
        self._wt_min, self._wt_max = DEFAULT_WAIST_TAIL_MIN, DEFAULT_WAIST_TAIL_MAX

    @property
    def name(self) -> str:
        return "joint"

    @property
    def display_name(self) -> str:
        return "关节控制"

    # ── limits (persisted via auth settings) ────────────────────────────────

    def _load_limits(self):
        try:
            from robot_control.web_gui import auth
            self._h_min   = int(auth.get_setting("joint_h_min",    str(DEFAULT_H_MIN)))
            self._h_max   = int(auth.get_setting("joint_h_max",    str(DEFAULT_H_MAX)))
            self._p_min   = int(auth.get_setting("joint_p_min",    str(DEFAULT_P_MIN)))
            self._p_max   = int(auth.get_setting("joint_p_max",    str(DEFAULT_P_MAX)))
            self._wh_min  = int(auth.get_setting("joint_wh_min",   str(DEFAULT_WAIST_HEAD_MIN)))
            self._wh_max  = int(auth.get_setting("joint_wh_max",   str(DEFAULT_WAIST_HEAD_MAX)))
            self._wt_min  = int(auth.get_setting("joint_wt_min",   str(DEFAULT_WAIST_TAIL_MIN)))
            self._wt_max  = int(auth.get_setting("joint_wt_max",   str(DEFAULT_WAIST_TAIL_MAX)))
        except Exception:
            pass

    def _save_limits(self):
        try:
            from robot_control.web_gui import auth
            auth.set_setting("joint_h_min",  str(self._h_min))
            auth.set_setting("joint_h_max",  str(self._h_max))
            auth.set_setting("joint_p_min",  str(self._p_min))
            auth.set_setting("joint_p_max",  str(self._p_max))
            auth.set_setting("joint_wh_min", str(self._wh_min))
            auth.set_setting("joint_wh_max", str(self._wh_max))
            auth.set_setting("joint_wt_min", str(self._wt_min))
            auth.set_setting("joint_wt_max", str(self._wt_max))
        except Exception:
            pass

    def _clamp_h(self, target):
        return max(self._h_min, min(self._h_max, target))

    def _clamp_p(self, target):
        return max(self._p_min, min(self._p_max, target))

    # ── lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        self._load_limits()
        import os as _os, sys
        _here = _os.path.dirname(__file__)
        for _try in [
            _os.path.normpath(_os.path.join(_here, "..", "pr_v2_sdk")),
            _os.path.normpath(_os.path.join(_here, "..", "..", "..", "20260618_管道机器人二代", "code")),
        ]:
            if _try not in sys.path:
                sys.path.insert(0, _try)

        try:
            from pr_v2_sdk.servo_lib import ServoController
            self._servo = ServoController()
            self._connected = True
            global _SHARED_USERVO
            _SHARED_USERVO = self._servo.uservo

            # Pan/tilt via Gimbal — reuse the same serial connection
            try:
                from pr_v2_sdk.gimbal_lib import Gimbal
                self._gimbal = Gimbal(uservo=self._servo.uservo)
                self._h_angle = self._gimbal.h_angle
                self._p_angle = self._gimbal.p_angle
            except Exception as e:
                log.warning("[Joint] Pan/tilt not available: %s", e)

            log.info("[Joint] Started — pt=%s, limits H:[%d,%d] P:[%d,%d] WH:[%d,%d] WT:[%d,%d]",
                     "ok" if self._gimbal else "no",
                     self._h_min, self._h_max, self._p_min, self._p_max,
                     self._wh_min, self._wh_max, self._wt_min, self._wt_max)
        except ImportError as e:
            log.warning("[Joint] SDK not available: %s", e)
            self._connected = False
        except Exception as e:
            log.error("[Joint] Start failed: %s", e)
            self._connected = False

    def stop(self) -> None:
        if self._servo:
            try:
                self._servo.stop(WAIST_ALL_IDS)
            except Exception:
                pass
            self._servo = None
        self._gimbal = None
        self._connected = False
        self._direction = "stop"
        log.info("[Joint] Stopped")

    def emergency_stop(self) -> None:
        if self._gimbal:
            self._gimbal.wheel_stop()
        if self._servo:
            self._servo.stop(WAIST_ALL_IDS)
        self._direction = "stop"
        log.info("[Joint] Emergency stop")

    # ── state ─────────────────────────────────────────────────────────────

    def get_state_schema(self) -> Dict[str, Any]:
        return {"connected": False, "direction": "stop",
                "h_angle": 0.0, "p_angle": 0.0,
                "h1_angle": 0.0, "h2_angle": 0.0,
                "p1_angle": 0.0, "p2_angle": 0.0,
                "head_angle": 0.0, "tail_angle": 0.0,
                "h_min": DEFAULT_H_MIN, "h_max": DEFAULT_H_MAX,
                "p_min": DEFAULT_P_MIN, "p_max": DEFAULT_P_MAX,
                "wh_min": DEFAULT_WAIST_HEAD_MIN, "wh_max": DEFAULT_WAIST_HEAD_MAX,
                "wt_min": DEFAULT_WAIST_TAIL_MIN, "wt_max": DEFAULT_WAIST_TAIL_MAX}

    @staticmethod
    def _calc_servo_angles(angle):
        kk = 0.78
        if angle >= 0:
            return angle, -kk * angle
        return kk * angle, -angle

    def get_state(self) -> Dict[str, Any]:
        if self._gimbal:
            self._h_angle = getattr(self._gimbal, "h_angle", 0.0)
            self._p_angle = getattr(self._gimbal, "p_angle", 0.0)
        h1, h2 = self._calc_servo_angles(self._h_angle)
        p1, p2 = self._calc_servo_angles(self._p_angle)
        return {
            "connected": self._connected,
            "direction": self._direction,
            "h_angle": self._h_angle,
            "p_angle": self._p_angle,
            "h1_angle": round(h1, 1),
            "h2_angle": round(h2, 1),
            "p1_angle": round(p1, 1),
            "p2_angle": round(p2, 1),
            "head_angle": round(self._waist_head_angle, 1),
            "tail_angle": round(self._waist_tail_angle, 1),
            "h_min": self._h_min,
            "h_max": self._h_max,
            "p_min": self._p_min,
            "p_max": self._p_max,
            "wh_min": self._wh_min,
            "wh_max": self._wh_max,
            "wt_min": self._wt_min,
            "wt_max": self._wt_max,
        }

    # ── SocketIO handlers ─────────────────────────────────────────────────

    def get_socketio_handlers(self) -> Dict[str, any]:
        return {
            # waist
            "joint_waist_spread": self._on_waist_spread,
            "joint_waist_shrink": self._on_waist_shrink,
            "joint_waist_stop":   self._on_waist_stop,
            "joint_stop":         self._on_stop,
            # pan/tilt
            "joint_pt_up":    self._on_pt_up,
            "joint_pt_down":  self._on_pt_down,
            "joint_pt_left":  self._on_pt_left,
            "joint_pt_right": self._on_pt_right,
            "joint_pt_home":  self._on_pt_home,
            "joint_pt_stop":  self._on_pt_stop,
            # limits
            "joint_get_limits": self._on_get_limits,
            "joint_set_limits": self._on_set_limits,
        }

    # ── waist (angle-mode spread/shrink, hold-to-step) ────────────────────

    def _move_waist_to(self, head_target, tail_target):
        """Move both waist servos to target angles (clamped)."""
        if not self._servo:
            return
        self._waist_head_angle = max(self._wh_min, min(self._wh_max, head_target))
        self._waist_tail_angle = max(self._wt_min, min(self._wt_max, tail_target))
        self._servo.uservo.set_servo_angle(
            servo_id=20, angle=self._waist_head_angle,
            is_mturn=True, power=5000, mean_dps=WAIST_DPS)
        self._servo.uservo.set_servo_angle(
            servo_id=10, angle=self._waist_tail_angle,
            is_mturn=True, power=5000, mean_dps=WAIST_DPS)

    def _on_waist_spread(self, data: dict, emit=None):
        step = data.get("step", WAIST_STEP)
        self._move_waist_to(
            self._waist_head_angle + step,
            self._waist_tail_angle + step)
        self._direction = "spread"
        log.debug("[Joint] Waist spread — head=%.0f tail=%.0f",
                  self._waist_head_angle, self._waist_tail_angle)

    def _on_waist_shrink(self, data: dict, emit=None):
        step = data.get("step", WAIST_STEP)
        self._move_waist_to(
            self._waist_head_angle - step,
            self._waist_tail_angle - step)
        self._direction = "shrink"
        log.debug("[Joint] Waist shrink — head=%.0f tail=%.0f",
                  self._waist_head_angle, self._waist_tail_angle)

    def _on_waist_stop(self, data: dict, emit=None):
        if self._servo:
            self._servo.stop(WAIST_ALL_IDS)
        self._direction = "stop"

    def _on_stop(self, data: dict, emit=None):
        """Global stop — waist + pan/tilt."""
        self._on_waist_stop(data, emit)
        self._on_pt_stop(data, emit)

    # ── pan/tilt (with limit enforcement) ─────────────────────────────────

    def _on_pt_up(self, data: dict, emit=None):
        if self._gimbal:
            step = getattr(self._gimbal, "STEP_ANGLE", 5)
            if self._clamp_p(self._gimbal.p_angle + step) == self._gimbal.p_angle:
                return
            self._gimbal.move_p(1)

    def _on_pt_down(self, data: dict, emit=None):
        if self._gimbal:
            step = getattr(self._gimbal, "STEP_ANGLE", 5)
            if self._clamp_p(self._gimbal.p_angle - step) == self._gimbal.p_angle:
                return
            self._gimbal.move_p(-1)

    def _on_pt_left(self, data: dict, emit=None):
        if self._gimbal:
            step = getattr(self._gimbal, "STEP_ANGLE", 5)
            if self._clamp_h(self._gimbal.h_angle - step) == self._gimbal.h_angle:
                return
            self._gimbal.move_h(-1)

    def _on_pt_right(self, data: dict, emit=None):
        if self._gimbal:
            step = getattr(self._gimbal, "STEP_ANGLE", 5)
            if self._clamp_h(self._gimbal.h_angle + step) == self._gimbal.h_angle:
                return
            self._gimbal.move_h(1)

    def _on_pt_home(self, data: dict, emit=None):
        if self._gimbal:
            self._gimbal.home()

    def _on_pt_stop(self, data: dict, emit=None):
        if self._gimbal:
            self._gimbal.wheel_stop()

    # ── limits ────────────────────────────────────────────────────────────

    def _on_get_limits(self, data: dict, emit=None):
        if emit:
            emit("joint_limits", {
                "h_min": self._h_min, "h_max": self._h_max,
                "p_min": self._p_min, "p_max": self._p_max,
                "wh_min": self._wh_min, "wh_max": self._wh_max,
                "wt_min": self._wt_min, "wt_max": self._wt_max,
            })

    def _on_set_limits(self, data: dict, emit=None):
        try:
            for key in ("h_min", "h_max", "p_min", "p_max",
                        "wh_min", "wh_max", "wt_min", "wt_max"):
                if key in data:
                    setattr(self, "_" + key, int(data[key]))
        except (ValueError, TypeError):
            if emit:
                emit("error", {"msg": "Invalid limit values"})
            return
        self._save_limits()
        log.info("[Joint] Limits updated: H:[%d,%d] P:[%d,%d] WH:[%d,%d] WT:[%d,%d]",
                 self._h_min, self._h_max, self._p_min, self._p_max,
                 self._wh_min, self._wh_max, self._wt_min, self._wt_max)
        if emit:
            self._on_get_limits(data, emit)
