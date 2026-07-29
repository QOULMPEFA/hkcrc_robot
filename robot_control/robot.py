"""Generic robot controller — assembles hardware modules from config.

Usage:
    from robot_control.robot import Robot

    config = {
        "lift":   {"enabled": True, "can_channel": "can0", "node_id": 1},
        "camera": {"enabled": True, "device": "/dev/video0"},
        # ugv, arm, hand are omitted → disabled
    }

    with Robot(config) as robot:
        robot.modules["lift"].move_to(200.0)
        state = robot.get_state()
        robot.emergency_stop()
"""

import logging
import time
from typing import Any, Dict, List, Optional

from robot_control.module_base import RobotModule

log = logging.getLogger(__name__)

# Module name → (class, module_path) for lazy discovery
_MODULE_REGISTRY: Dict[str, tuple] = {
    "ugv":    ("robot_control.ugv",    "UGVController"),
    "lift":   ("robot_control.lift",   "LiftController"),
    "arm":    ("robot_control.arm",    "ArmController"),
    "hand":   ("robot_control.hand",   "HandController"),
    "camera": ("robot_control.camera", "CameraModule"),
    "motor":  ("robot_control.motor",  "MotorController"),
    "servo":  ("robot_control.servo",  "ServoModule"),
    "joint":  ("robot_control.pipi_joint",  "JointModule"),
    "drive":  ("robot_control.pipi_drive",  "DriveModule"),
    "demo":   ("robot_control.demo",        "DemoModule"),
    "teleop": ("teleoperation_sdk.teleop",   "TeleopModule"),
    "gimbal": ("robot_control.gimbal",      "GimbalModule"),
}


def register_module(name: str, module_path: str, class_name: str) -> None:
    """Register a custom module so Robot can discover it by name."""
    _MODULE_REGISTRY[name] = (module_path, class_name)


def _instantiate_module(name: str, config: Dict[str, Any]) -> Optional[RobotModule]:
    """Lazy-import and instantiate a module class by name."""
    if name not in _MODULE_REGISTRY:
        log.warning("[Robot] Unknown module '%s' — skipping", name)
        return None

    module_path, class_name = _MODULE_REGISTRY[name]
    try:
        import importlib
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        instance = cls(config)
        log.info("[Robot] Module '%s' instantiated (%s)", name, class_name)
        return instance
    except ImportError as e:
        log.warning("[Robot] Module '%s' SDK not available: %s — stub mode", name, e)
        return None
    except Exception as e:
        log.error("[Robot] Module '%s' init failed: %s", name, e)
        return None


class Robot:
    """Config-driven container for hardware modules.

    Only modules listed in the config dict are loaded. Each key is a module
    name ("ugv", "lift", "arm", "hand", "camera", ...) and its value is a
    dict with at least an "enabled" key (defaults to True).

    Example config for pipe robot mini (camera + lift only):
        config = {
            "lift":   {"can_channel": "can0", "node_id": 1, "home_strategy": "current_pos"},
            "camera": {"device": "/dev/video0"},
        }

    Full config for the standard robot:
        config = {
            "ugv":    {"host": "127.0.0.1", "port": 9100},
            "lift":   {"can_channel": "can0", "node_id": 1},
            "arm":    {"host": "192.168.56.101"},
            "hand":   {"port": "/dev/ttyUSB1"},
            "camera": {"device": "/dev/video0"},
        }
    """

    def __init__(self, config: Optional[Dict[str, Dict[str, Any]]] = None):
        cfg = config or {}
        self.config = cfg
        # {name: RobotModule instance} — only enabled & successfully loaded modules
        self.modules: Dict[str, RobotModule] = {}
        self._module_state_schemas: Dict[str, Dict[str, Any]] = {}

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> "Robot":
        log.info("[Robot] Starting subsystems ...")
        for name, mod_cfg in self.config.items():
            if isinstance(mod_cfg, dict) and not mod_cfg.get("enabled", True):
                log.info("[Robot] Module '%s' disabled in config — skipping", name)
                continue

            instance = _instantiate_module(name, mod_cfg if isinstance(mod_cfg, dict) else {})
            if instance is not None:
                # WARNING: wire cross-module access via post-hoc assignment.
                # Do NOT pass robot= to cls(config, robot=self) — most module
                # constructors (lift/camera/motor/hand/arm/ugv) don't accept
                # a 'robot' kwarg and will raise TypeError.
                instance.robot = self
            if instance is None:
                # Still record state schema so GUI doesn't break
                try:
                    _, class_name = _MODULE_REGISTRY.get(name, ("", ""))
                    if class_name:
                        import importlib
                        module_path = _MODULE_REGISTRY[name][0]
                        mod = importlib.import_module(module_path)
                        cls = getattr(mod, class_name)
                        dummy = cls({})
                        self._module_state_schemas[name] = dummy.get_state_schema()
                except Exception:
                    self._module_state_schemas[name] = {"connected": False}
                continue

            try:
                instance.start()
                self.modules[name] = instance
                self._module_state_schemas[name] = instance.get_state_schema()
                log.info("[Robot] Module '%s' started", name)
            except Exception as e:
                log.warning("[Robot] Module '%s' start failed: %s", name, e)
                self._module_state_schemas[name] = instance.get_state_schema()
                try:
                    instance.stop()
                except Exception:
                    pass

        log.info("[Robot] %d module(s) started: %s",
                 len(self.modules), list(self.modules.keys()))
        return self

    def stop(self) -> None:
        log.info("[Robot] Stopping subsystems ...")
        for name, module in reversed(list(self.modules.items())):
            try:
                module.stop()
                log.info("[Robot] Module '%s' stopped", name)
            except Exception as e:
                log.warning("[Robot] Module '%s' stop error: %s", name, e)
        self.modules.clear()

    def __enter__(self) -> "Robot":
        return self.start()

    def __exit__(self, *_) -> None:
        self.stop()

    # ── aggregate state ───────────────────────────────────────────────────────

    def get_state(self) -> Dict[str, Any]:
        """Return {module_name: module_state_dict, ...} plus timestamp."""
        state: Dict[str, Any] = {"timestamp": time.time()}
        for name, module in self.modules.items():
            try:
                state[name] = module.get_state()
            except Exception as e:
                log.warning("[Robot] get_state() for '%s' failed: %s", name, e)
                state[name] = self._module_state_schemas.get(name, {"connected": False})
        # Include schemas for modules that were never loaded
        for name, schema in self._module_state_schemas.items():
            if name not in state:
                state[name] = dict(schema)
        return state

    # ── emergency ─────────────────────────────────────────────────────────────

    def emergency_stop(self) -> None:
        """Call emergency_stop() on all loaded modules."""
        log.warning("[Robot] EMERGENCY STOP")
        for name, module in self.modules.items():
            try:
                module.emergency_stop()
            except Exception as e:
                log.warning("[Robot] emergency_stop() for '%s' failed: %s", name, e)

    # ── helpers ───────────────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[RobotModule]:
        """Get a loaded module by name, or None."""
        return self.modules.get(name)

    def is_loaded(self, name: str) -> bool:
        return name in self.modules

    @property
    def module_names(self) -> List[str]:
        return list(self.modules.keys())
