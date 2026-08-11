# Simulated Arm Teleop Verification

Date: 2026-08-11

## Feature

The arm panel now has a `模拟机械臂` / `退出模拟` button.  When simulation is
enabled, the arm module reports `SIMULATION` state and teleop enable uses an
in-memory UR adapter instead of connecting to RTDE.  This allows hand teleop
direction checks while the real UR5e is powered off.

## Usage

1. Open the robot control panel.
2. In the arm module, click `模拟机械臂`.
3. Verify the arm panel shows `模拟` / `SIMULATION` and home joints
   `[90, 0, 90, -90, 90, -135]`.
4. In the teleop module, enable teleop and calibrate.
5. Move the hand and compare the hand 3D direction with the robot 3D skeleton.
6. To return to real hardware mode, click `退出模拟`.

## Files

- `robot_control/arm.py`: simulation flag, state, manual move handling, and
  `apply_teleop_state()`.
- `teleoperation_sdk/xr_ur5e.py`: `SimulatedUR5eAdapter` and simulate-aware
  controller connect.
- `teleoperation_sdk/teleop.py`: reads arm simulation flag and syncs teleop
  target/current joints into the simulated arm state.
- `robot_control/web_gui/static/app.js`: simulation toggle button and status.

## Local Smoke Test

Local Python verification without RTDE:

```text
arm simulate True
arm state True SIMULATION [90.0, 0.0, 90.0, -90.0, 90.0, -135.0]
controller simulate True initial q deg [90.0, 0.0, 90.0, -90.0, 90.0, -135.0]
simulated delta mm [-56.5, 0.0, -56.5] (DH base frame)
arm actual after teleop [82.3, 7.7, 83.4, -91.0, 82.3, -135.0]
```

Direction mapping was verified separately with
`log/work/direction_sim.py`; the Web UI scene right/up/forward map is
`[79.96, 0.06, 0]`, `[-0.04, 80.06, 0]`, `[0, 0, -79.92]` mm.

## Deployment (2026-08-11)

Deployed to `/home/yu/robot_code/`:

- `robot_control/arm.py`
- `robot_control/web_gui/static/app.js`
- `teleoperation_sdk/xr_ur5e.py`
- `teleoperation_sdk/teleop.py`

Remote `py_compile` and simulation imports passed.  The control panel service
was restarted; `robot_control_panel.service` is active and teleop initializes.
