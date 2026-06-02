"""
Example: HM (Homing) mode.
Method 35 = set current position as home electronically (no physical movement).
The position counter resets to 0; the motor does NOT move.
"""

import logging

from stepper_motor_sdk import CANopenNode, StepperMotor, HomingMethod

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

NODE_ID = 1
CHANNEL = "can0"

with CANopenNode(interface="socketcan", channel=CHANNEL, node_id=NODE_ID) as node:
    motor = StepperMotor(node, pulses_per_rev=1000)

    motor.enable()

    print(f"Position before homing: {motor.get_position()} Pul")

    motor.set_homing_mode(
        method=HomingMethod.CURRENT_POSITION,   # method 35: no movement
        home_offset=0,
    )
    print("Starting homing (method 35 — sets current position as zero) ...")
    motor.home(wait=True, timeout=10.0)

    print(f"Position after homing:  {motor.get_position()} Pul  (should be 0)")

    # Verify with a profile-position move from confirmed zero
    motor.set_position_mode(profile_velocity=5_000)
    print("Moving to 3.0 rev ...")
    motor.move_absolute_rev(3.0)
    print(f"Position after move:    {motor.get_position_rev():.3f} rev  (should be 3.000)")

    motor.shutdown()
    print("Done.")
