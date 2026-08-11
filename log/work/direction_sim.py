"""Direction mapping simulator for Quest hand input -> UR5e 3D skeleton."""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from teleoperation_sdk.ur5_ik import ur5_fk, ur5_ik
from teleoperation_sdk.xr_ur5e import R_HEADSET_TO_WORLD


# Composite transform from the control panel app.js robot scene:
# robotFrame -> baseFrame -> mirrorFrame -> modelAdjustFrame(0,-45,90)
# -> modelFrame -> modelZFrame.  The fallback skeleton lives under modelZFrame.
S2 = math.sqrt(0.5)
FRONTEND_TO_DH = R_HEADSET_TO_WORLD
DH_TO_FRONTEND = np.array(
    [
        [-S2, 0.0, -S2],
        [-S2, 0.0, S2],
        [0.0, 1.0, 0.0],
    ]
)

HOME_DEG = [90.0, 0.0, 90.0, -90.0, 90.0, -135.0]
SCALE = 0.8

DIRECTIONS: Dict[str, np.ndarray] = {
    "right": np.array([1.0, 0.0, 0.0]),
    "left": np.array([-1.0, 0.0, 0.0]),
    "up": np.array([0.0, 1.0, 0.0]),
    "down": np.array([0.0, -1.0, 0.0]),
    "forward": np.array([0.0, 0.0, -1.0]),
    "back": np.array([0.0, 0.0, 1.0]),
}


def simulate_move(
    raw_m: np.ndarray,
) -> Tuple[List[float], List[float], List[float], float]:
    home_q = [math.radians(v) for v in HOME_DEG]
    home_pose = np.asarray(ur5_fk(home_q), dtype=float)
    model_delta_mm = FRONTEND_TO_DH @ raw_m * SCALE * 1000.0
    target = np.array(home_pose, dtype=float)
    target[:3, 3] += model_delta_mm

    q = ur5_ik(target.tolist(), seed_joints_rad=home_q)
    if q is None:
        raise RuntimeError("ur5_ik returned None")
    actual_delta_mm = np.asarray(ur5_fk(q), dtype=float)[:3, 3] - home_pose[:3, 3]
    frontend_delta_mm = DH_TO_FRONTEND @ actual_delta_mm
    position_error_mm = float(np.linalg.norm(actual_delta_mm - model_delta_mm))
    return (
        list(model_delta_mm),
        list(actual_delta_mm),
        list(frontend_delta_mm),
        position_error_mm,
    )


def main() -> int:
    assert np.allclose(FRONTEND_TO_DH @ DH_TO_FRONTEND, np.eye(3), atol=1e-12)
    print("R_HEADSET_TO_WORLD:")
    print(np.array2string(FRONTEND_TO_DH, precision=9, suppress_small=True))
    print("DH_TO_FRONTEND:")
    print(np.array2string(DH_TO_FRONTEND, precision=9, suppress_small=True))
    print()
    print(f"{'input':8} {'frontend delta (mm)':26} {'expected':18} {'IK err(mm)':10}")

    failures = 0
    for name, raw in DIRECTIONS.items():
        test_raw = raw * 0.1
        _, _, frontend_delta_mm, ik_err = simulate_move(test_raw)
        expected = test_raw * SCALE * 1000.0
        ok = bool(np.allclose(frontend_delta_mm, expected, atol=1.0))
        if not ok:
            failures += 1
        print(
            f"{name:8} {str([round(v, 2) for v in frontend_delta_mm]):26} "
            f"{str([round(v, 2) for v in expected]):18} {ik_err:10.3f}"
        )

    print()
    if failures:
        print(f"FAILED: {failures} direction(s) mismatch")
        return 1
    print("OK: hand right/up/forward map to robot right/up/forward in the Web UI scene")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
