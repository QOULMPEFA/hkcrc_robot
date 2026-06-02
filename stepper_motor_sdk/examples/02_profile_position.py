"""
Example: PP (Profile Position) mode — absolute and relative moves.
"""

import logging

from stepper_motor_sdk import CANopenNode, StepperMotor

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

NODE_ID = 1
CHANNEL = "can0"

with CANopenNode(interface="socketcan", channel=CHANNEL, node_id=NODE_ID) as node:
    motor = StepperMotor(node, pulses_per_rev=1000)

    print("Enabling drive ...")
    motor.enable()

    motor.set_position_mode(
        profile_velocity=10_000,   # 600 r/min
        acceleration=100_000,
        deceleration=100_000,
    )

    # Move to absolute position 5 revolutions
    print("Moving to 5.0 rev (absolute) ...")
    motor.move_absolute_rev(5.0)
    print(f"Position after move: {motor.get_position_rev():.3f} rev")

    # Relative move: +2 revolutions
    print("Moving +2.0 rev (relative) ...")
    motor.move_relative_rev(2.0)
    print(f"Position after move: {motor.get_position_rev():.3f} rev")

    # Return to home
    print("Returning to 0 ...")
    motor.move_absolute(0)
    print(f"Final position: {motor.get_position_rev():.3f} rev")

    motor.shutdown()
    print("Done.")
