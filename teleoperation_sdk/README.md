# XR Teleoperation SDK — Meta Quest 3 → UR5e + BrainCo Hand

Two teleoperation backends for different hardware setups:

| Backend | VR Connection | Robot | Extra Hardware |
|---------|-------------|-------|---------------|
| **UR5eTeleopADB** (new) | Quest 3 via USB/ADB | UR5e (URScript TCP) | None — runs on any PC |
| **TeleopModule** (existing) | Quest 3 via WiFi/OpenXR | UR5 + Hand + Gimbal | Jetson AGX Orin + XRoboToolkit-PC-Service |

## Quick Start — USB/ADB (Recommended for UR5e)

This runs directly on your Windows/Linux PC — no Jetson or WiFi needed.

```
Quest 3 (USB) ──ADB──▶ oculus_reader ──▶ UR5eTeleopADB ──TCP──▶ UR5e robot
```

### Prerequisites

1. **ADB** (Android SDK Platform Tools) — [download](https://developer.android.com/tools/releases/platform-tools)
2. **Quest 3** with Developer Mode enabled (via Meta Quest phone app)
3. **Python 3.8+** with `numpy` and `pure-python-adb`

### Setup

```bash
# 1. Install Python dependencies
pip install numpy pure-python-adb

# 2. Connect Quest 3 via USB, accept the "Allow USB debugging" prompt in headset

# 3. Verify ADB sees the device
adb devices
# Should show: 1Wxxx...  device

# 4. Install the teleop APK on Quest 3
python run_teleop_ur5e.py --install-apk

# 5. Run teleoperation
python run_teleop_ur5e.py --ur-host 192.168.56.101
```

### Usage

```bash
# Basic — right controller drives arm, UR5 at default IP
python run_teleop_ur5e.py

# Left controller
python run_teleop_ur5e.py --controller left

# Custom UR5 IP and sensitivity
python run_teleop_ur5e.py --ur-host 192.168.1.100 --scale 0.3 --rot-scale 0.2

# Custom coordinate mapping (flip axes for your UR5 mounting)
python run_teleop_ur5e.py --vr-map "0,0,-1, 1,0,0, 0,1,0"

# Run for 60 seconds then stop
python run_teleop_ur5e.py --duration 60

# Verbose debug output
python run_teleop_ur5e.py -v
```

### Controls

| VR Input | Action |
|----------|--------|
| **Grip** (middle finger) | Dead-man switch — hold to move arm, release to freeze |
| **Controller movement** | Drives UR5e TCP in Cartesian space |
| **Ctrl+C** (keyboard) | Emergency stop → `stopj(2.0)` |

### Coordinate Mapping

VR controller space → UR5 base frame is configured via `--vr-map`:

```
VR:  X=right, Y=up, Z=forward
UR5: X=forward, Y=left, Z=up  (standard mounting)

Default mapping:
  UR5_X = VR_Z    (controller forward/back → robot forward/back)
  UR5_Y = VR_X    (controller left/right → robot left/right)
  UR5_Z = VR_Y    (controller up/down → robot up/down)
```

If your UR5 is wall/ceiling mounted, adjust signs with `--vr-map "0,0,-1, -1,0,0, 0,1,0"` etc.

### Safety

- **Grip button** = dead-man switch (release → arm holds position)
- **Workspace bounds** enforced before every servoj command (configurable via `--ws-min`/`--ws-max`)
- **Joint velocity limit** at 1.5 rad/s per joint per step
- **Emergency stop** on Ctrl+C sends `stopj(2.0)` with maximum deceleration

### Troubleshooting

| Problem | Solution |
|---------|----------|
| `Device not found` | `adb devices` — ensure USB debugging allowed in headset |
| `APK install failed` | Check Developer Mode is on; try `--install-apk` again |
| `UR5 connect failed` | Verify UR5 IP and that port 30001/30002 are reachable |
| Controller not responding | Ensure teleop APK is running (check Quest app drawer) |
| Arm doesn't move | Hold **grip** button while moving controller |
| `ImportError: No module named 'ppadb'` | `pip install pure-python-adb` |

---

## WiFi/OpenXR Backend (Jetson + Hand + Gimbal)

The existing `TeleopModule` uses XRoboToolkit-PC-Service for OpenXR communication. See `teleop.py` docstring and the [setup script](setup_xr_teleop.sh) for Jetson deployment.

```
Meta Quest 3 (OpenXR Unity App)
       | WiFi (same LAN)
       v
Jetson AGX Orin
  +-- XRoboToolkit-PC-Service (C++)    receives controller + head poses
  +-- xrobotoolkit_sdk (pybind11)      Python bindings
  +-- teleoperation_sdk/teleop.py      TeleopModule
              |          |            |
              v          v            v
     UR5 arm (TCP)  BrainCo Hand  Gimbal Camera
                   (Modbus)       (CH340 UART Servos)
```

### Head Tracking → Gimbal

The Quest 3 HMD orientation drives the 2-axis gimbal camera independently of arm tracking.

## Directory Map

```
teleoperation_sdk/
  README.md                    ← this file
  __init__.py                  ← package init, public API exports
  teleop.py                    ← TeleopModule (WiFi/OpenXR backend, RobotModule)
  teleop_ur5e_adb.py           ← UR5eTeleopADB (USB/ADB backend, standalone)
  ur5_ik.py                    ← UR5 numerical IK (damped least-squares Jacobian)
  install_quest_apk.py         ← Helper to install teleop APK on Quest 3
  setup_xr_teleop.sh           ← Build script for Jetson ARM64

  oculus_reader/               ← ADB-based Quest communication (from rail-berkeley)
    reader.py                  ← OculusReader: ADB + logcat parsing
    buttons_parser.py          ← Button state parser
    APK/S2R2S-teleop.apk       ← Pre-built Android APK for Quest 2/3
```
