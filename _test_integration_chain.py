"""Integrated-path test for Robot + Quest simulator + continuous servo backend.

UR RTDE/Placo are not installed on this development machine, so the test
injects a fake XrUr5eController.  It verifies the Web GUI enable path, grip
activation, continuous step/send behavior, and disable cleanup.
"""

import json
import logging
import math
import struct
import threading
import time

logging.basicConfig(level=logging.WARNING,
                    format="%(levelname)s %(name)s: %(message)s")

from _test_quest_link import pack, tracking_msg

PORT = 63911
STOP = threading.Event()


def stream_simulator(host, port):
    import socket
    s = socket.create_connection((host, port), timeout=5)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    s.sendall(pack(0x19, b"SIMULATOR01|-1"))
    s.sendall(pack(0x6C, b"SIMULATOR01|1.0|1.0.10"))
    i = 0
    while not STOP.is_set():
        px = math.sin(i * 0.3) * 0.05
        pose_l = [0.05 + px, 0.10, 0.20, 0.0, 0.0, 0.0, 1.0]
        grip = 1.0 if i >= 3 else 0.0
        head = [0.0, 1.5, 0.0, 0.0, 0.0, 0.0, 1.0]
        s.sendall(pack(0x6D, tracking_msg(
            pose_l=pose_l, grip=grip, head_pose=head).encode()))
        i += 1
        time.sleep(0.01)
    time.sleep(0.2)
    s.close()


class FakeXrController:
    def __init__(self, config):
        self.config = config
        self.steps = 0
        self.sends = 0
        self.stopped = False
        self.tracking = False
        self.current_q = [0.0, -1.5707963267948966, 1.5707963267948966, 0.0, -1.5707963267948966, 0.0]
        self.target_q = list(self.current_q)

    def connect(self):
        self.connected = True

    def stop(self):
        self.stopped = True

    def set_reference_pose(self, pose):
        pass

    def step(self, pose, grip):
        self.steps += 1
        self.tracking = float(grip) > 0.9

    def send(self):
        self.sends += 1

    def get_state(self):
        import math as _math
        return {
            "connected": True,
            "active": True,
            "tracking": self.tracking,
            "target_joints_deg": [_math.degrees(v) for v in self.target_q],
            "current_joints_deg": [_math.degrees(v) for v in self.current_q],
        }


def main():
    from robot_control.robot import Robot
    import teleoperation_sdk.teleop as teleop_mod

    config = {
        "arm": {"host": "192.168.56.101"},
        "teleop": {
            "host": "192.168.56.101",
            "arm_controller": "left",
            "vr_backend": "xr_protocol",
            "vr_backend_kwargs": {"host": "127.0.0.1", "port": PORT},
        },
    }
    robot = Robot(config).start()
    assert "teleop" in robot.modules
    m = robot.modules["teleop"]
    arm = robot.modules["arm"]
    assert m.robot is robot
    assert arm._home_angles == [90.0, 0.0, 90.0, -90.0, 90.0, -135.0]
    print("PASS Robot container wired")

    t = threading.Thread(target=stream_simulator, args=("127.0.0.1", PORT), daemon=True)
    t.start()

    deadline = time.time() + 6
    pose = None
    while time.time() < deadline:
        pose = m._vr_backend.get_controller_pose("left")
        if pose is not None and m._vr_backend.get_grip("left") > 0.5:
            break
        time.sleep(0.1)
    assert pose is not None
    assert m._vr_backend.get_grip("left") > 0.5
    print("PASS Quest simulator parsed in Robot container")

    arm.stop_motion = lambda: None
    original_cls = teleop_mod.XrUr5eController
    teleop_mod.XrUr5eController = FakeXrController
    try:
        m._on_enable()
        assert m._active
        assert arm._teleop_active
        assert m._controller is not None
        assert m._controller.stopped is False

        deadline = time.time() + 5
        while time.time() < deadline:
            if m._controller.steps > 5 and m._controller.sends > 5 and m._controller.tracking:
                break
            time.sleep(0.1)

        assert m._controller.steps > 5, "continuous IK steps did not run"
        assert m._controller.sends > 5, "continuous servoj sends did not run"
        assert m._controller.tracking, "grip did not activate tracking"
        assert m.get_state()["tracking"]
        print("PASS grip -> continuous step + send (servo semantics)")

        m._on_disable()
        assert not m._active
        assert not arm._teleop_active
        assert m._controller is None
        print("PASS disable -> controller stopped")
    finally:
        teleop_mod.XrUr5eController = original_cls

    STOP.set()
    m.stop()
    robot.stop()


if __name__ == "__main__":
    main()
