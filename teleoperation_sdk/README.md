# XR Teleoperation SDK — Meta Quest 3 → UR5 + BrainCo Hand

## Architecture

```
Meta Quest 3 (OpenXR Unity App)
       │ WiFi (same LAN)
       ▼
Jetson AGX Orin
  ├─ XRoboToolkit-PC-Service (C++)   ← receives controller poses
  ├─ xrobotoolkit_sdk (pybind11)      ← Python bindings
  └─ robot_control/teleop.py          ← TeleopModule (servoj + hand)
              │                    │
              ▼                    ▼
     UR5 arm (TCP 30001)   BrainCo Hand (Modbus)
```

## Quest 3 Compatibility

The XR-Robotics PC Service communicates via OpenXR, which Quest 3 natively supports
through Meta's OpenXR runtime. However, the prebuilt `XRoboToolkit-PICO-*.apk` is
compiled against PICO's Unity XR SDK and cannot run on Quest 3.

**Required:** The Unity XR client must be rebuilt targeting Meta Quest 3:

1. Clone the XR client Unity project (contact XR-Robotics for source access)
2. Switch Unity build target to Android + Meta Quest 3
3. Replace PICO Unity Integration SDK with Meta XR SDK / OpenXR Plugin
4. Build APK and sideload via `adb install` or Meta Quest Developer Hub

The PC Service side (Jetson) is headset-agnostic — no changes needed.

## Quick Start

### 1. Jetson — Build PC Service + Python Bindings

```bash
# Copy setup script to Jetson and run
scp scripts/setup_xr_teleop.sh jetson@192.168.56.101:~/
ssh jetson@192.168.56.101
bash setup_xr_teleop.sh
```

This clones and builds:
- `XRoboToolkit-PC-Service` — C++ server listening for headset data
- `XRoboToolkit-PC-Service-Pybind` — Python bindings via pybind11

### 2. Jetson — Verify Installation

```bash
python3 -c "import xrobotoolkit_sdk as xrt; print('SDK OK')"
```

### 3. Quest 3 — Build and Install XR Client

```bash
# After building the Quest 3 APK in Unity:
adb install -g XRoboToolkit-Quest3-1.0.0.apk
```

### 4. Network Setup

- Jetson and Quest 3 must be on the **same WiFi network**
- Jetson IP is typically `192.168.56.101` (configure in `server.py` teleop config)
- Quest 3 WiFi IP can be found in Settings → Wi-Fi → [network name]

### 5. Start Services

```bash
# On Jetson:
/opt/apps/roboticsservice/runService.sh &   # PC Service
sudo systemctl restart robot_control_panel.service
```

### 6. Launch on Quest 3

Open the XRoboToolkit app from the Quest 3 app library. It will auto-discover
the PC Service on the local network.

## Configuration

In `robot_control/web_gui/server.py`, the teleop module config:

```python
"teleop": {
    "host": "192.168.56.101",       # UR5 IP
    "urscript_port": 30001,         # URScript streaming port
    "arm_controller": "right",      # which Quest controller drives the arm
    "workspace_min_xyz": [-500, -500, 0],
    "workspace_max_xyz": [500, 500, 850],
}
```

## Safety

- **Grip button** (middle finger) = dead-man switch — release to freeze arm
- **Trigger** (index finger) = hand open/close — hand stops when trigger released
- **E-Stop** = `stopj(2.0)` + tracking disabled
- **Workspace bounds** enforced before every servoj command
- **Joint velocity limiting** at 1.5 rad/s per joint per step
- Arm manual controls (GUI move_joints, move_cartesian, go_home) gated when teleop active

## Directory Map

```
teleoperation_sdk/          ← this directory
  README.md                 ← this file
  setup_xr_teleop.sh        → ../scripts/setup_xr_teleop.sh

robot_control/
  teleop.py                 ← TeleopModule (RobotModule interface)
  ur5_ik.py                 ← UR5 numerical IK (damped least-squares Jacobian)
  arm.py                    ← teleop gating on move_joints/move_cartesian/go_home
  module_base.py            ← RobotModule ABC with robot cross-reference
  robot.py                  ← module registry + wiring
```

## Alternative: Direct OpenXR Streaming

If rebuilding the XR-Robotics Unity client is not feasible, Quest 3 can be used
with a lightweight OpenXR streaming server that publishes controller poses over
a simple TCP/WebSocket protocol. The `teleop.py` module is designed with a clean
interface — the `_read_controller()` method can be adapted to any data source.

## References

- [XR-Robotics GitHub](https://github.com/XR-Robotics/.github/blob/main/profile/README.md)
- [Meta Quest Developer Center](https://developer.oculus.com/)
- [OpenXR Specification](https://registry.khronos.org/OpenXR/)
- [UR5 URScript Manual](https://www.universal-robots.com/download/manuals-e-series/)
