# XR Teleoperation SDK — Meta Quest 3 -> UR5e

Backend rewritten to follow the XR-Robotics reference implementation:

- `XRoboToolkit-PC-Service`: TCP service receiving Quest tracking data on `63901`
- `XRoboToolkit-PC-Service-Pybind`: `xrobotoolkit_sdk` Python bindings over local gRPC `60061`
- `XRoboToolkit-Teleop-Sample-Python`: upstream UR5e teleop algorithm and `dual_ur5e.urdf`
- `XRoboToolkit-Unity-Client-Quest`: Quest 3 Unity client (`teleoperation_sdk/client_quest3`)

The Web control panel frontend is retained. The old custom direction matrices,
live direction modes, and trigger-once `movej` path were removed.

## Architecture

```
Quest 3 (XRoboToolkit-Quest app)
  | WiFi TCP 63901
  v
XRoboToolkit-PC-Service
  | local gRPC 60061
  v
xrobotoolkit_sdk (pybind)  ->  TeleopModule
                                   |
                                   +-- Placo IK (dual_ur5e.urdf)
                                   +-- UR RTDE servoj() continuous control
```

`teleoperation_sdk/teleop.py` is only a thin `RobotModule` adapter for the
existing control panel. `teleoperation_sdk/xr_ur5e.py` contains the actual
teleoperation algorithm.

## Control Semantics

| Input | Action |
|-------|--------|
| Left grip held | Dead-man switch; controller delta drives the left UR5e end effector |
| Left grip released | IK reference resets; RTDE keeps serving the last target |
| Trigger | Reserved for gripper/hand in the upstream sample; not used by the plain UR5e backend |

The algorithm is the upstream one:

1. Read controller pose from `xrobotoolkit_sdk`.
2. Rotate pose by `R_HEADSET_TO_WORLD`.
3. Capture the robot end-effector pose when grip first becomes active.
4. Apply the scaled controller position/orientation delta to that end-effector reference.
5. Solve IK with Placo.
6. Send the joint target to the UR with RTDE `servoJ`.

## Run

1. Start the PC Service on the robot PC:

   ```bash
   sudo systemctl enable --now robotics-service.service
   ```

2. Install the SDK and runtime dependencies:

   ```bash
   bash teleoperation_sdk/setup_xr_teleop.sh
   ```

3. Start the robot control panel:

   ```bash
   python -m robot_control.web_gui.server
   ```

The default config in `robot_control/web_gui/server.py` uses:

```python
"teleop": {
    "host": "192.168.56.101",
    "arm_controller": "left",
    "vr_backend": "xrt",
    "vr_backend_kwargs": {},
    "urdf_path": "teleoperation_sdk/XRoboToolkit-Teleop-Sample-Python/assets/universal_robots_ur5e/dual_ur5e.urdf",
    "link_name": "left_tool0",
    "joint_start": 7,
    "joint_end": 13,
    "scale_factor": 0.8,
    "servo_time": 0.017,
    "lookahead_time": 0.1,
    "servo_gain": 300.0,
}
```

## Directory Map

```
teleoperation_sdk/
  teleop.py                         RobotModule adapter / SocketIO interface
  xr_ur5e.py                        XR-Robotics UR5e teleop core
  vr_backends.py                    xrt + xr_protocol backend abstraction
  xr_protocol.py                    optional direct Quest TCP parser for testing
  XRoboToolkit-PC-Service/          upstream PC service source
  XRoboToolkit-PC-Service-Pybind/   upstream Python bindings
  XRoboToolkit-Teleop-Sample-Python/ upstream UR5e sample and URDF
  client_quest3/                    Quest 3 Unity client
```
