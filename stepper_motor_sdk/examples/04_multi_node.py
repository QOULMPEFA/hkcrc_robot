"""
Example: two motors on the same CAN bus via SharedCANBus.
Requires two physical nodes (node_id=1 and node_id=2) on the bus.
SharedCANBus owns one listener thread; frames are dispatched by node_id.
"""

import logging
import time

from stepper_motor_sdk import SharedCANBus, CANopenNode, StepperMotor, SDOError

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

CHANNEL = "can0"
BITRATE = 500_000
NODE_IDS = [1, 2]   # adjust to match physical node IDs on your bus


def make_motor(shared: SharedCANBus, node_id: int) -> StepperMotor | None:
    """Create and enable a motor, return None if the node is not reachable."""
    node = CANopenNode(node_id=node_id, shared_bus=shared).start()
    motor = StepperMotor(node)
    try:
        motor.enable()
        return motor
    except SDOError:
        print(f"[WARN] node_id={node_id} not reachable — skipping")
        node.stop()
        return None


with SharedCANBus(interface="socketcan", channel=CHANNEL, bitrate=BITRATE) as shared:
    motors = [make_motor(shared, nid) for nid in NODE_IDS]
    active = [m for m in motors if m is not None]

    if not active:
        print("No motors found on the bus.")
    else:
        for m in active:
            m.set_velocity_mode()

        active[0].set_velocity_rpm(300)
        if len(active) > 1:
            active[1].set_velocity_rpm(-300)    # reverse

        time.sleep(2.0)

        for m in active:
            m.stop()

print("Done.")
