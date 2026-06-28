"""Demo automation module — pre-programmed sequence looping PT, waist, and drive.

Two buttons: Start (begins looping sequence), Stop (halts immediately).
Timing values are adjustable from the control panel and persist via auth settings.
"""

import logging
import threading
import time
from typing import Any, Dict

from robot_control.module_base import RobotModule

log = logging.getLogger(__name__)

# ── Default timing values ───────────────────────────────────────────────────

HOLD_INTERVAL    = 0.08   # interval between repeated move commands (not adjustable)
DRIVE_INTERVAL   = 0.05   # interval between repeated drive commands (not adjustable)

DEFAULTS = {
    "init_wait":        2.5,   # wait after initial home + shrink
    "spread_wait":      2.0,   # wait after spread to max
    "drive_duration":   2.0,   # forward / backward hold duration
    "drive_stop_pause": 0.5,   # pause after drive stop before next step
    "pt_duration":      2.0,   # PT each direction hold duration
    "pt_stop_pause":    0.3,   # pause between PT stop and home
    "pause_after_home": 3.0,   # pause after PT home
    "cycle_wait":       5.0,   # wait at end of each loop iteration
}

SETTING_PREFIX = "demo_"

# ────────────────────────────────────────────────────────────────────────────


class DemoModule(RobotModule):
    """Runs a looping automation demo across joint (PT + waist) and drive modules."""

    def __init__(self, config: dict):
        self._config = config
        self._joint = None
        self._drive = None
        self._running = False
        self._thread = None
        self._load_config()

    # ── config persistence ─────────────────────────────────────────────────

    def _load_config(self):
        """Load timing values from auth settings, falling back to defaults."""
        from robot_control.web_gui import auth
        for key, default in DEFAULTS.items():
            raw = auth.get_setting(SETTING_PREFIX + key, str(default))
            try:
                setattr(self, key, float(raw))
            except (ValueError, TypeError):
                setattr(self, key, default)

    def _save_config(self):
        """Persist current timing values to auth settings."""
        from robot_control.web_gui import auth
        for key in DEFAULTS:
            val = getattr(self, key, DEFAULTS[key])
            auth.set_setting(SETTING_PREFIX + key, str(val))

    def get_config(self) -> Dict[str, float]:
        """Return current timing config dict."""
        return {key: getattr(self, key, DEFAULTS[key]) for key in DEFAULTS}

    # ── post-init wiring ──────────────────────────────────────────────────

    def set_modules(self, joint, drive):
        self._joint = joint
        self._drive = drive

    # ── metadata ──────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "demo"

    @property
    def display_name(self) -> str:
        return "演示序列"

    # ── lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        log.info("[Demo] Module started (idle)")

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        log.info("[Demo] Module stopped")

    def emergency_stop(self) -> None:
        self._running = False
        log.info("[Demo] Emergency stop")

    # ── state ─────────────────────────────────────────────────────────────

    def get_state_schema(self) -> Dict[str, Any]:
        return {"active": False, "step": "", "config": {}}

    def get_state(self) -> Dict[str, Any]:
        return {
            "active": self._running,
            "step": getattr(self, "_step", ""),
            "config": self.get_config(),
        }

    # ── SocketIO handlers ─────────────────────────────────────────────────

    def get_socketio_handlers(self) -> Dict[str, any]:
        return {
            "demo_start":       self._on_start,
            "demo_stop":        self._on_stop,
            "demo_get_config":  self._on_get_config,
            "demo_set_config":  self._on_set_config,
        }

    def _on_start(self, data: dict, emit=None):
        if self._running:
            return
        if not self._joint or not self._drive:
            log.warning("[Demo] Cannot start — joint or drive module not wired")
            return
        self._running = True
        self._thread = threading.Thread(target=self._sequence_loop, daemon=True, name="demo-seq")
        self._thread.start()
        log.info("[Demo] Sequence started")
        if emit:
            emit("demo_status", {"active": True})

    def _on_stop(self, data: dict, emit=None):
        self._running = False
        log.info("[Demo] Sequence stopped — waiting for thread")
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        # Execute init (home + shrink) so robot returns to safe position
        self._step = "init"
        log.info("[Demo] Stop — running init (home + shrink)")
        joint = self._joint
        if joint:
            if joint._gimbal:
                joint._gimbal.home()
            if joint._servo:
                joint._servo.uservo.set_servo_angle(servo_id=20, angle=850, is_mturn=True, power=5000, mean_dps=400)
                joint._servo.uservo.set_servo_angle(servo_id=10, angle=-180, is_mturn=True, power=5000, mean_dps=400)
        time.sleep(self.init_wait)
        self._halt_all()
        self._step = ""
        if emit:
            emit("demo_status", {"active": False})

    def _on_get_config(self, data: dict, emit=None):
        if emit:
            emit("demo_config", self.get_config())

    def _on_set_config(self, data: dict, emit=None):
        if not data:
            return
        changed = False
        for key in DEFAULTS:
            if key in data:
                try:
                    val = float(data[key])
                    if val > 0:
                        setattr(self, key, val)
                        changed = True
                except (ValueError, TypeError):
                    pass
        if changed:
            self._save_config()
            log.info("[Demo] Config updated: %s", self.get_config())
            if emit:
                emit("demo_config", self.get_config())

    # ── hardware helpers ──────────────────────────────────────────────────

    def _halt_all(self):
        """Stop all motion immediately."""
        try:
            if self._joint and self._joint._gimbal:
                self._joint._gimbal.wheel_stop()
            if self._joint and self._joint._servo:
                self._joint._servo.stop([10, 20])
        except Exception:
            pass
        try:
            if self._drive and self._drive._motor:
                self._drive._motor.stop()
        except Exception:
            pass

    def _hold_pt(self, move_fn, duration):
        """Repeatedly call a gimbal move function for `duration` seconds."""
        deadline = time.time() + duration
        while time.time() < deadline and self._running:
            move_fn()
            time.sleep(HOLD_INTERVAL)

    def _hold_drive(self, cmd_fn, duration):
        """Repeatedly call a drive command for `duration` seconds."""
        deadline = time.time() + duration
        while time.time() < deadline and self._running:
            cmd_fn()
            time.sleep(DRIVE_INTERVAL)

    def _sleep(self, duration):
        """Sleep in small increments so we can abort quickly on stop."""
        deadline = time.time() + duration
        while time.time() < deadline and self._running:
            time.sleep(0.1)

    # ── sequence ──────────────────────────────────────────────────────────

    def _sequence_loop(self):
        joint = self._joint
        drive = self._drive
        gimbal = joint._gimbal if joint else None
        servo = joint._servo if joint else None

        while self._running:
            # ── Initial: home + shrink ──
            self._step = "init"
            log.info("[Demo] Step: init — home + shrink (%.1fs)", self.init_wait)
            if gimbal:
                gimbal.home()
            if servo:
                servo.uservo.set_servo_angle(servo_id=20, angle=850, is_mturn=True, power=5000, mean_dps=400)
                servo.uservo.set_servo_angle(servo_id=10, angle=-180, is_mturn=True, power=5000, mean_dps=400)
            self._sleep(self.init_wait)

            # ── Spread to max ──
            self._step = "spread"
            log.info("[Demo] Step: spread max (%.1fs)", self.spread_wait)
            if servo:
                servo.uservo.set_servo_angle(servo_id=20, angle=2100, is_mturn=True, power=5000, mean_dps=400)
                servo.uservo.set_servo_angle(servo_id=10, angle=1070, is_mturn=True, power=5000, mean_dps=400)
            self._sleep(self.spread_wait)

            # ── Drive forward ──
            self._step = "forward"
            log.info("[Demo] Step: drive forward %.1fs", self.drive_duration)
            self._hold_drive(lambda: drive._on_forward({}), self.drive_duration)
            self._hold_drive(lambda: drive._on_stop({}), 0.1)
            self._sleep(self.drive_stop_pause)

            # ── Drive backward ──
            self._step = "backward"
            log.info("[Demo] Step: drive backward %.1fs", self.drive_duration)
            self._hold_drive(lambda: drive._on_backward({}), self.drive_duration)
            self._hold_drive(lambda: drive._on_stop({}), 0.1)
            self._sleep(self.drive_stop_pause)

            # ── PT: up → home → pause ──
            self._step = "pt_up"
            log.info("[Demo] Step: PT up %.1fs", self.pt_duration)
            self._hold_pt(lambda: gimbal.move_p(1), self.pt_duration)
            gimbal.wheel_stop()
            self._sleep(self.pt_stop_pause)
            gimbal.home()
            self._sleep(self.pause_after_home)

            # ── PT: down → home → pause ──
            self._step = "pt_down"
            log.info("[Demo] Step: PT down %.1fs", self.pt_duration)
            self._hold_pt(lambda: gimbal.move_p(-1), self.pt_duration)
            gimbal.wheel_stop()
            self._sleep(self.pt_stop_pause)
            gimbal.home()
            self._sleep(self.pause_after_home)

            # ── PT: left → home → pause ──
            self._step = "pt_left"
            log.info("[Demo] Step: PT left %.1fs", self.pt_duration)
            self._hold_pt(lambda: gimbal.move_h(-1), self.pt_duration)
            gimbal.wheel_stop()
            self._sleep(self.pt_stop_pause)
            gimbal.home()
            self._sleep(self.pause_after_home)

            # ── PT: right → home → pause ──
            self._step = "pt_right"
            log.info("[Demo] Step: PT right %.1fs", self.pt_duration)
            self._hold_pt(lambda: gimbal.move_h(1), self.pt_duration)
            gimbal.wheel_stop()
            self._sleep(self.pt_stop_pause)
            gimbal.home()
            self._sleep(self.pause_after_home)

            # ── Shrink before wait ──
            self._step = "shrink"
            log.info("[Demo] Step: shrink before wait")
            if servo:
                servo.uservo.set_servo_angle(servo_id=20, angle=850, is_mturn=True, power=5000, mean_dps=400)
                servo.uservo.set_servo_angle(servo_id=10, angle=-180, is_mturn=True, power=5000, mean_dps=400)

            # ── Wait, then loop ──
            self._step = "wait"
            log.info("[Demo] Step: cycle complete — waiting %.1fs", self.cycle_wait)
            self._sleep(self.cycle_wait)

        self._step = ""
        self._halt_all()
        log.info("[Demo] Sequence ended")
