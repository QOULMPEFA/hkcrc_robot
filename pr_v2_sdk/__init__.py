"""管道机器人二代 SDK — Pipe Robot V2 Hardware Abstraction Layer.

Provides:
- ServoController: Fashion Star bus servo (RS-485) high-level wrapper
- Gimbal: Pan/tilt controller (4 servos on shared RS-485 bus)
- DCMotorController: DC motor driver (serial text-command protocol)
- PipeRobot: Top-level integration class for all subsystems

Each module degrades gracefully when pyserial is not available.
"""

__all__ = ["ServoController", "Gimbal", "DCMotorController", "PipeRobot"]


def __getattr__(name):
    _imports = {
        "ServoController":   "pr_v2_sdk.servo_lib",
        "Gimbal":            "pr_v2_sdk.gimbal_lib",
        "DCMotorController": "pr_v2_sdk.dcmotor_lib",
        "PipeRobot":         "pr_v2_sdk.pipe_robot",
    }
    if name in _imports:
        import importlib
        mod = importlib.import_module(_imports[name])
        attr = getattr(mod, name)
        globals()[name] = attr
        return attr
    raise AttributeError(f"module 'pr_v2_sdk' has no attribute '{name}'")
