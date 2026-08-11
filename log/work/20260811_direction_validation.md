# Direction Mapping Validation Handoff

Date: 2026-08-11

## Conclusion

The previous numeric validation was wrong because it read the URDF model's
`tool0`, while the visible robot skeleton in the control panel is rendered by
`_ur5eFkFrames()` using DH coordinates.  These two frames are not equivalent.

When the same backend target joints are applied:

- URDF `tool0` object: hand right moves robot right.
- Visible DH skeleton: hand right moves robot down.

The actual user-visible robot 3D follows the DH skeleton result.

## Correct Validation Chain

Do not validate using static axes alone.  Use this complete chain:

```text
hand input coordinate
  -> backend mapping matrix
  -> IK target pose
  -> target joint angles
  -> robot 3D tool0 world position delta
  -> compare with hand input direction
```

## Tool

`log/work/direction_mapping_tool.html` is a standalone browser tool.

It supports:

- Hand test directions: right / left / up / down / forward / back
- Custom hand `dx/dy/dz`
- Mapping presets:
  - Identity
  - Source `R_HEADSET_TO_WORLD`
  - Current backend matrix
  - Custom 3x3 matrix
- Current joint input
- IK solving with the same DH model used by the control panel skeleton
- Display of:
  - hand input direction
  - robot target joint angles
  - robot 3D tool0 displacement

## Next Step

The backend IK and the control panel 3D skeleton must use the same DH/URDF
coordinate system.  Do not keep changing `R_HEADSET_TO_WORLD` without first
running this tool to confirm the visible robot direction.

## Corrected Matrix (2026-08-11)

The handoff matrix was stale and did not match the current `app.js` robot
scene.  The current scene applies `modelAdjustFrame(0, -45, 90)` by default.
Derived from the actual Three.js hierarchy with local Node:

```text
DH_TO_FRONTEND (C):
  [[-0.70710678,  0.00000000, -0.70710678],
   [-0.70710678,  0.00000000,  0.70710678],
   [ 0.00000000,  1.00000000,  0.00000000]]

R_HEADSET_TO_WORLD = C^T:
  [[-0.70710678, -0.70710678,  0.00000000],
   [ 0.00000000,  0.00000000,  1.00000000],
   [-0.70710678,  0.70710678,  0.00000000]]
```

Verification command:

```text
python log/work/direction_sim.py
```

With a 0.1 m simulated hand move, the Web UI robot 3D displacement is:

```text
right   -> [79.96,  0.06,  0.00] mm
up      -> [-0.04, 80.06, -0.00] mm
forward -> [-0.00,  0.00, -79.92] mm
```

The mapping is now correct for the current control panel DH skeleton.  This was
a local code change until it was deployed to the Jetson.

Browser verification was also run against the actual `app.js` Three.js scene.
The target joints were computed by `direction_sim.py`, then `_ur5eFkFrames()`
was measured directly in Chrome:

```text
right   -> [79.959,  0.058, -0.000] mm
up      -> [-0.041, 80.058, -0.000] mm
forward -> [-0.001,  0.001, -79.920] mm
```

Note: the standalone `direction_mapping_tool.html` contains a quick JS IK
that is still unit-inconsistent and must not be used as the final check.  Use
`direction_sim.py` plus the Python target joints, or fix that JS IK before
trusting its result.

## Deployment (2026-08-11)

Deployed to `/home/yu/robot_code/teleoperation_sdk/`:

- `xr_ur5e.py`
- `teleop.py`
- `__init__.py`

Remote `py_compile` passed, remote import printed the corrected
`R_HEADSET_TO_WORLD`, and `robot_control_panel.service` was restarted.
Service is active, teleop module loads, and the local HTTP check returns 302.
