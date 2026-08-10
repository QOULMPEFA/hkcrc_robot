"""Simulate the client_quest3 app over TCP and verify XR-Robotics mapping.

This test intentionally does not assert the old custom direction matrices.
It verifies:
  1. The Quest TCP protocol is parsed by XRProtocolReceiver.
  2. The upstream R_HEADSET_TO_WORLD transform is used.
  3. TeleopModule exposes the state the existing Web GUI consumes.
"""

import json
import math
import socket
import struct
import threading
import time

import numpy as np

HEAD = 0x3F
END = 0xA5
CMD_CONNECT = 0x19
CMD_VERSION = 0x6C
CMD_TRACKING = 0x6D
SN = "SIMULATOR01"


def pack(cmd: int, payload: bytes) -> bytes:
    data = bytearray(15 + len(payload))
    data[0] = HEAD
    data[1] = cmd
    struct.pack_into("<i", data, 2, len(payload))
    data[6:6 + len(payload)] = payload
    struct.pack_into("<q", data, 6 + len(payload), int(time.time() * 1000))
    data[-1] = END
    return bytes(data)


def tracking_msg(pose_r=None, pose_l=None, grip=0.0, trigger=0.0, head_pose=None):
    ctrl = {}
    if pose_r is not None:
        ctrl["right"] = {
            "pose": ",".join(f"{v:.6f}" for v in pose_r),
            "grip": grip, "trigger": trigger,
            "primaryButton": False, "secondaryButton": False,
            "menuButton": False, "axisX": 0.0, "axisY": 0.0, "axisClick": False,
        }
    if pose_l is not None:
        ctrl["left"] = {
            "pose": ",".join(f"{v:.6f}" for v in pose_l),
            "grip": grip, "trigger": trigger,
            "primaryButton": False, "secondaryButton": False,
            "menuButton": False, "axisX": 0.0, "axisY": 0.0, "axisClick": False,
        }
    data = {
        "predictTime": int(time.time() * 1e6),
        "appState": {"focus": True},
        "timeStampNs": int(time.time() * 1e9),
        "Input": 1,
    }
    if head_pose:
        data["Head"] = {"pose": ",".join(f"{v:.6f}" for v in head_pose), "status": 3}
    if ctrl:
        data["Controller"] = ctrl
    return json.dumps({"functionName": "Tracking", "value": json.dumps(data)})


def run_quest_client(host, port, frames=12, dt=0.05):
    s = socket.create_connection((host, port), timeout=5)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    s.sendall(pack(CMD_CONNECT, (SN + "|-1").encode()))
    s.sendall(pack(CMD_VERSION, (SN + "|1.0|1.0.10").encode()))
    time.sleep(0.3)
    for i in range(frames):
        px = math.sin(i * 0.2) * 0.05
        pose_l = [0.05 + px, 0.10, 0.20, 0.0, 0.0, 0.0, 1.0]
        grip = 1.0 if i >= 2 else 0.0
        trig = 0.3 if i >= 4 else 0.0
        head = [0.0, 1.5, 0.0, 0.0, 0.0, 0.0, 1.0]
        s.sendall(pack(CMD_TRACKING, tracking_msg(
            pose_l=pose_l, grip=grip, trigger=trig, head_pose=head).encode()))
        time.sleep(dt)
    time.sleep(0.3)
    s.close()


def assert_upstream_geometry():
    from teleoperation_sdk.xr_ur5e import (
        R_HEADSET_TO_WORLD,
        apply_delta_pose,
        apply_headset_to_world,
        quat_diff_as_angle_axis,
    )

    # Upstream sample: R_HEADSET_TO_WORLD * [x, y, z].
    assert np.allclose(R_HEADSET_TO_WORLD @ np.array([1.0, 0.0, 0.0]), [0.0, -1.0, 0.0])
    assert np.allclose(apply_headset_to_world([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])[:3],
                       [0.0, -1.0, 0.0])

    # +30 degree horizontal rotation in angle-axis form.
    q2 = np.array([math.cos(math.radians(15.0)), 0.0, math.sin(math.radians(15.0)), 0.0])
    angle_axis = quat_diff_as_angle_axis(np.array([1.0, 0.0, 0.0, 0.0]), q2)
    assert abs(np.linalg.norm(angle_axis) - math.radians(30.0)) < 1e-6

    pos, quat = apply_delta_pose(
        np.array([0.5, 0.2, 1.0]),
        np.array([1.0, 0.0, 0.0, 0.0]),
        np.array([0.1, 0.0, 0.0]),
        np.zeros(3),
    )
    assert np.allclose(pos, [0.6, 0.2, 1.0])
    assert np.allclose(quat, [1.0, 0.0, 0.0, 0.0])
    print("PASS upstream R_HEADSET_TO_WORLD and relative delta geometry")


def main():
    import logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    assert_upstream_geometry()

    from teleoperation_sdk.teleop import TeleopModule

    port = 63920
    m = TeleopModule(config={
        "vr_backend": "xr_protocol",
        "host": "192.168.56.101",
        "vr_backend_kwargs": {"host": "127.0.0.1", "port": port},
        "arm_controller": "left",
    })
    m.start()
    assert m._xr_initialized, "xr_protocol backend did not initialize"

    t = threading.Thread(target=run_quest_client, args=("127.0.0.1", port), daemon=True)
    t.start()
    t.join(timeout=10)

    pose = m._vr_backend.get_controller_pose("left")
    grip = m._vr_backend.get_grip("left")
    assert pose is not None
    assert grip > 0.5
    m._read_controller()
    m._on_calibrate()
    st = m.get_controller_pose_state()
    assert st["controller_pose_3d"][:3] == pose[:3]
    assert st["translation_mapping"] == "natural"
    print("PASS Quest TCP parse -> TeleopModule state")
    m.stop()


if __name__ == "__main__":
    main()
