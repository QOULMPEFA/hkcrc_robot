"""
Example: PV (Profile Velocity) mode
Replicates the manual cansend sequence from test log.md using the SDK.
"""

import logging
import time

from stepper_motor_sdk import CANopenNode, StepperMotor

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

NODE_ID   = 1
CHANNEL   = "can0"
BITRATE   = 500_000

with CANopenNode(interface="socketcan", channel=CHANNEL, bitrate=BITRATE, node_id=NODE_ID) as node:
    motor = StepperMotor(node, pulses_per_rev=1000)

    # CiA 402 state machine: Shutdown → Ready → Switched On → Operation Enabled
    print("Enabling drive ...")
    motor.enable()
    print("State:", motor.get_state())

    # Switch to Profile Velocity mode (mode = 3)
    motor.set_velocity_mode(acceleration=100_000, deceleration=100_000)
    print("Operation mode:", motor.get_operation_mode())  # should print 3

    # Run at 600 r/min  →  10 000 Pul/s
    target_rpm = 600
    print(f"Running at {target_rpm} r/min ...")
    motor.set_velocity_rpm(target_rpm)

    time.sleep(3.0)

    # Read back actual feedback
    print(f"Actual velocity: {motor.get_velocity_rpm():.1f} r/min")
    print(f"Actual position: {motor.get_position()} Pul")

    # Decelerate to zero
    print("Stopping ...")
    motor.stop()
    time.sleep(1.0)

    # Release torque
    motor.shutdown()
    print("Done. Drive state:", motor.get_state())
