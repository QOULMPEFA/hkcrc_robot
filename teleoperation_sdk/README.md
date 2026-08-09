# XR Teleoperation SDK — Meta Quest 3 → UR5e + BrainCo Hand

Two connection paths for the Quest 3:

| Path | Quest Client | Robot | Extra Hardware |
|------|-------------|-------|---------------|
| **client_quest3 (TCP)** | `client_quest3/` — full XRoboToolkit-Quest Unity app | UR5e (URScript TCP) via `TeleopModule` | None — runs on any PC the Quest can reach |
| **WiFi/OpenXR (Jetson)** | XRoboToolkit-Quest app + XRoboToolkit-PC-Service | UR5 + Hand + Gimbal | Jetson AGX Orin |

The ADB/oculus_reader path was removed — Quest data now always comes from the
full `client_quest3` app over TCP (port 63901).

## Recommended path — client_quest3 over TCP (UR5e)

```
Quest 3 (XRoboToolkit-Quest app)
   | WiFi (same LAN)
   v
robot server (this PC)  ── XRProtocolReceiver :63901 (TCP) ──▶ TeleopModule
                                                                  |
                                                                  v
                                                     UR5e (servoj via arm module)
```

### 1. Build / install the Quest app

The Unity project is `teleoperation_sdk/client_quest3/` (see its README for the
XRoboToolkit-Quest client). Build via the `Build/One-Click Package` menu, or
use a prebuilt APK:

```bash
# from teleoperation_sdk/
adb install -r output_apk/quest_verify.apk
```

### 2. Run the robot server with the xr_protocol backend

```bash
python -m robot_control.web_gui.server
```

Config in `robot_control/web_gui/server.py`:

```python
"teleop": {
    "host": "<UR5 IP>",
    "vr_backend": "xr_protocol",          # Quest data from client_quest3 app
    "vr_backend_kwargs": {"host": "0.0.0.0", "port": 63901},
    ...
}
```

### 3. Connect

On the Quest app, enter this PC's LAN IP in the **Network - PC Service** field
and connect. Toggle **Tracking - Send** so the headset streams controller data.

### Controls

| VR Input | Action |
|----------|--------|
| **Grip** (middle finger) | Dead-man switch — hold to move arm, release to freeze |
| **Controller movement** | Drives UR5e TCP in Cartesian space |
| **Trigger** | Drives the BrainCo hand open/close |
| Web GUI / Ctrl+C | Emergency stop → `stopj(2.0)` |

### Safety

- **Grip button** = dead-man switch (release → arm holds position)
- **Workspace bounds** enforced before every servoj command (configurable via `--ws-min`/`--ws-max`)
- **Joint velocity limit** at 1.5 rad/s per joint per step
- **Emergency stop** sends `stopj(2.0)` with maximum deceleration

---

## WiFi/OpenXR Backend (Jetson + Hand + Gimbal)

The existing `TeleopModule` uses XRoboToolkit-PC-Service for OpenXR communication. See the `teleop.py` docstring and [setup_xr_teleop.sh](setup_xr_teleop.sh) for Jetson deployment.

```
Meta Quest 3 (OpenXR Unity App)
       | WiFi (same LAN)
       v
Jetson AGX Orin
  +-- XRoboToolkit-PC-Service (C++)    receives controller + head poses
  +-- xrobotoolkit_sdk (pybind11)      Python bindings
  +-- teleoperation_sdk/teleop.py      TeleopModule (vr_backend: "xrt")
              |          |            |
              v          v            v
     UR5 arm (TCP)  BrainCo Hand  Gimbal Camera
                   (Modbus)       (CH340 UART Servos)
```

## Directory Map

```
teleoperation_sdk/
  README.md                    ← this file
  __init__.py                  ← package init, public API exports
  teleop.py                    ← TeleopModule (VR-backend agnostic, RobotModule)
  vr_backends.py               ← VRBackend ABC + XRBackend + XRProtocolBackend + factory
  xr_protocol.py               ← XRProtocolReceiver: TCP 63901 parser for client_quest3
  ur5_ik.py                    ← UR5 numerical IK (damped least-squares Jacobian)
  video_bridge.py              ← RealSense → Quest JPEG/UDP relay helpers
  client_quest3/               ← XRoboToolkit-Quest Unity client project (Quest side)
  reference/                   ← upstream reference copies (not imported)
  tools/adb.exe                ← platform-tools adb (install APK / logcat)
```
