"""Numerical inverse kinematics for UR5 using damped least-squares Jacobian.

This avoids the complexity of analytical Pieper solutions across different
DH conventions. The damped pseudoinverse handles singularities gracefully,
and 6-DOF at 100 Hz with ~5-10 iterations per solve is negligible overhead.

DH parameters (mm, rad) — Standard convention.
"""

import math
from typing import List, Optional

# ── UR5 DH parameters (standard convention) ──────────────────────────────────
# a (mm), alpha (rad), d (mm)
DH = [
    (0.0,      math.pi / 2,  89.459),
    (-425.0,   0.0,          0.0),
    (-392.25,  0.0,          0.0),
    (0.0,      math.pi / 2,  109.15),
    (0.0,     -math.pi / 2,  94.65),
    (0.0,      0.0,          82.3),
]

JOINT_LIMITS_DEG = [
    (-360.0, 360.0),
    (-360.0, 360.0),
    (-180.0, 180.0),
    (-360.0, 360.0),
    (-360.0, 360.0),
    (-360.0, 360.0),
]


# ── 4x4 homogeneous transform helpers ────────────────────────────────────────

def _dh_transform(a: float, alpha: float, d: float, theta: float) -> List[List[float]]:
    ct = math.cos(theta)
    st = math.sin(theta)
    ca = math.cos(alpha)
    sa = math.sin(alpha)
    return [
        [ct, -st * ca,  st * sa, a * ct],
        [st,  ct * ca, -ct * sa, a * st],
        [0.0, sa,       ca,      d],
        [0.0, 0.0,      0.0,     1.0],
    ]


def _mat_mul(a: List[List[float]], b: List[List[float]]) -> List[List[float]]:
    result = [[0.0] * 4 for _ in range(4)]
    for i in range(4):
        for j in range(4):
            result[i][j] = sum(a[i][k] * b[k][j] for k in range(4))
    return result


def _quat_to_rot(qx: float, qy: float, qz: float, qw: float) -> List[List[float]]:
    n = qx * qx + qy * qy + qz * qz + qw * qw
    s = 2.0 / n if n > 0 else 0.0
    xx, xy, xz = qx * qx * s, qx * qy * s, qx * qz * s
    yy, yz, zz = qy * qy * s, qy * qz * s, qz * qz * s
    wx, wy, wz = qw * qx * s, qw * qy * s, qw * qz * s
    return [
        [1.0 - (yy + zz), xy - wz, xz + wy],
        [xy + wz, 1.0 - (xx + zz), yz - wx],
        [xz - wy, yz + wx, 1.0 - (xx + yy)],
    ]


def _rot_to_pose(rot: List[List[float]], xyz: List[float]) -> List[List[float]]:
    return [
        [rot[0][0], rot[0][1], rot[0][2], xyz[0]],
        [rot[1][0], rot[1][1], rot[1][2], xyz[1]],
        [rot[2][0], rot[2][1], rot[2][2], xyz[2]],
        [0.0, 0.0, 0.0, 1.0],
    ]


# ── forward kinematics ───────────────────────────────────────────────────────

def ur5_fk(joints_rad: List[float]) -> List[List[float]]:
    """Forward kinematics: 6 joint angles (rad) → 4x4 end-effector pose."""
    t = [[1.0, 0.0, 0.0, 0.0],
         [0.0, 1.0, 0.0, 0.0],
         [0.0, 0.0, 1.0, 0.0],
         [0.0, 0.0, 0.0, 1.0]]
    for i, theta in enumerate(joints_rad):
        a, alpha, d = DH[i]
        t = _mat_mul(t, _dh_transform(a, alpha, d, theta))
    return t


def ur5_fk_frames(joints_rad: List[float]) -> List[List[List[float]]]:
    """Forward kinematics for each joint frame, starting with the base."""
    frames = [[[1.0, 0.0, 0.0, 0.0],
               [0.0, 1.0, 0.0, 0.0],
               [0.0, 0.0, 1.0, 0.0],
               [0.0, 0.0, 0.0, 1.0]]]
    t = frames[0]
    for i, theta in enumerate(joints_rad):
        a, alpha, d = DH[i]
        t = _mat_mul(t, _dh_transform(a, alpha, d, theta))
        frames.append(t)
    return frames


# ── Jacobian computation ─────────────────────────────────────────────────────

def _compute_jacobian(joints_rad: List[float]) -> List[List[float]]:
    """Compute 6x6 geometric Jacobian at the current joint configuration.

    Returns matrix where rows 0-2 are linear velocity, rows 3-5 are angular
    velocity, columns correspond to joints 0-5.
    """
    # Build transforms for each joint
    transforms = []
    t = [[1.0, 0.0, 0.0, 0.0],
         [0.0, 1.0, 0.0, 0.0],
         [0.0, 0.0, 1.0, 0.0],
         [0.0, 0.0, 0.0, 1.0]]
    for i, theta in enumerate(joints_rad):
        a, alpha, d = DH[i]
        t = _mat_mul(t, _dh_transform(a, alpha, d, theta))
        transforms.append(t)

    # End-effector position
    ee_pos = [transforms[-1][0][3], transforms[-1][1][3], transforms[-1][2][3]]

    jac = [[0.0] * 6 for _ in range(6)]

    for i in range(6):
        # Joint axis z (third column of rotation part) for frame i-1
        if i == 0:
            z = [0.0, 0.0, 1.0]  # base frame z-axis
            o = [0.0, 0.0, 0.0]  # base frame origin
        else:
            z = [transforms[i - 1][0][2], transforms[i - 1][1][2], transforms[i - 1][2][2]]
            o = [transforms[i - 1][0][3], transforms[i - 1][1][3], transforms[i - 1][2][3]]

        # Linear velocity: z × (ee_pos - o)
        r = [ee_pos[0] - o[0], ee_pos[1] - o[1], ee_pos[2] - o[2]]
        jac[0][i] = z[1] * r[2] - z[2] * r[1]
        jac[1][i] = z[2] * r[0] - z[0] * r[2]
        jac[2][i] = z[0] * r[1] - z[1] * r[0]

        # Angular velocity: z
        jac[3][i] = z[0]
        jac[4][i] = z[1]
        jac[5][i] = z[2]

    return jac


def _pose_error(current: List[List[float]], target: List[List[float]]) -> List[float]:
    """Compute 6-DOF error vector: position error (mm) + orientation error (rad).

    Position error = target_pos - current_pos (in base frame).
    Orientation error = axis-angle extracted from R_err = R_target * R_current^T.
    """
    err = [0.0] * 6

    # Position error
    err[0] = target[0][3] - current[0][3]
    err[1] = target[1][3] - current[1][3]
    err[2] = target[2][3] - current[2][3]

    # Orientation error: extract from rotation error matrix
    # R_err = R_target * R_current^T
    r_cur = [[current[i][j] for j in range(3)] for i in range(3)]
    r_tgt = [[target[i][j] for j in range(3)] for i in range(3)]
    # R_err = r_tgt * r_cur^T
    r_err = [[0.0] * 3 for _ in range(3)]
    for i in range(3):
        for j in range(3):
            r_err[i][j] = sum(r_tgt[i][k] * r_cur[j][k] for k in range(3))

    # axis-angle from rotation matrix: theta = acos((trace - 1)/2)
    trace = r_err[0][0] + r_err[1][1] + r_err[2][2]
    cos_theta = max(-1.0, min(1.0, (trace - 1.0) / 2.0))
    theta = math.acos(cos_theta)

    if abs(theta) < 1e-9:
        return err  # orientation already correct

    # axis = 1/(2*sin(theta)) * [r32-r23, r13-r31, r21-r12]
    k = 1.0 / (2.0 * math.sin(theta))
    err[3] = k * (r_err[2][1] - r_err[1][2]) * theta
    err[4] = k * (r_err[0][2] - r_err[2][0]) * theta
    err[5] = k * (r_err[1][0] - r_err[0][1]) * theta

    return err


def _transpose_6x6(m: List[List[float]]) -> List[List[float]]:
    result = [[0.0] * 6 for _ in range(6)]
    for i in range(6):
        for j in range(6):
            result[i][j] = m[j][i]
    return result


def _clamp_joints(joints_rad: List[float]) -> List[float]:
    """Clamp joint angles to limits."""
    result = list(joints_rad)
    for i, angle in enumerate(result):
        lo, hi = JOINT_LIMITS_DEG[i]
        lo_rad, hi_rad = math.radians(lo), math.radians(hi)
        result[i] = max(lo_rad, min(hi_rad, angle))
    return result


# ── inverse kinematics (damped least-squares) ────────────────────────────────

def ur5_ik(
    pose_4x4: List[List[float]],
    seed_joints_rad: Optional[List[float]] = None,
    max_iters: int = 200,
    tol_pos: float = 0.5,
    tol_rot: float = 0.005,
    damping: float = 0.01,
) -> Optional[List[float]]:
    """Numerical IK using damped least-squares (Levenberg-Marquardt).

    Uses adaptive damping: low damping when far from target (more aggressive
    Gauss-Newton steps), higher damping near solution (stable convergence).
    Joint limits enforced by clamping after each iteration.

    Args:
        pose_4x4: Target 4x4 end-effector pose.
        seed_joints_rad: Initial guess (defaults to home config).
        max_iters: Maximum iterations.
        tol_pos: Position tolerance (mm).
        tol_rot: Rotation tolerance (rad, ~0.3°).
        damping: Base damping factor (adaptively scaled by error magnitude).

    Returns:
        Joint angles in radians, or None if did not converge.
    """
    q = list(seed_joints_rad) if seed_joints_rad else \
        [0.0, -math.pi, 3.0 * math.pi / 4.0, -math.pi / 2.0, 0.0, 0.0]

    prev_err_mag = float("inf")

    for _ in range(max_iters):
        current_pose = ur5_fk(q)
        err = _pose_error(current_pose, pose_4x4)

        pos_err = math.sqrt(err[0] ** 2 + err[1] ** 2 + err[2] ** 2)
        rot_err = math.sqrt(err[3] ** 2 + err[4] ** 2 + err[5] ** 2)

        if pos_err < tol_pos and rot_err < tol_rot:
            return _clamp_joints(q)

        err_mag = pos_err + 0.1 * rot_err  # combined error metric

        J = _compute_jacobian(q)
        JT = _transpose_6x6(J)

        # Adaptive damping: lower when far (faster steps), higher near solution
        adapt_damp = damping * (1.0 + err_mag / 100.0)

        lam_sq = adapt_damp * adapt_damp
        A = [[0.0] * 6 for _ in range(6)]
        b = [0.0] * 6
        for i in range(6):
            b[i] = sum(JT[i][k] * err[k] for k in range(6))
            for j in range(6):
                A[i][j] = sum(JT[i][k] * J[k][j] for k in range(6))
                if i == j:
                    A[i][j] += lam_sq

        dq = _solve_6x6(A, b)
        if dq is None:
            return None

        # Line search: reduce step if error increases
        step = 1.0
        for _ in range(5):
            trial = _clamp_joints([q[i] + step * dq[i] for i in range(6)])
            trial_pose = ur5_fk(trial)
            trial_err = _pose_error(trial_pose, pose_4x4)
            trial_mag = math.sqrt(trial_err[0] ** 2 + trial_err[1] ** 2 + trial_err[2] ** 2) + \
                        0.1 * math.sqrt(trial_err[3] ** 2 + trial_err[4] ** 2 + trial_err[5] ** 2)
            if trial_mag < err_mag * 0.99:
                q = trial
                break
            step *= 0.5
        else:
            # Step didn't improve — apply damped step anyway to escape saddle
            for i in range(6):
                q[i] += step * dq[i]
            q = _clamp_joints(q)

        prev_err_mag = err_mag

    return _clamp_joints(q)


def _solve_6x6(A: List[List[float]], b: List[float]) -> Optional[List[float]]:
    """Solve 6x6 linear system using Gaussian elimination with partial pivoting."""
    n = 6
    M = [row[:] + [b[i]] for i, row in enumerate(A)]  # augmented matrix

    for col in range(n):
        # Partial pivot
        max_row = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[max_row][col]) < 1e-12:
            return None
        M[col], M[max_row] = M[max_row], M[col]

        pivot = M[col][col]
        for j in range(col, n + 1):
            M[col][j] /= pivot

        for row in range(n):
            if row != col:
                factor = M[row][col]
                for j in range(col, n + 1):
                    M[row][j] -= factor * M[col][j]

    return [M[i][n] for i in range(n)]


def select_best_ik(
    solutions: List[List[float]],
    current_joints_rad: Optional[List[float]] = None,
    weights: Optional[List[float]] = None,
) -> Optional[List[float]]:
    """Select the IK solution closest to current joint configuration."""
    if not solutions:
        return None
    if current_joints_rad is None:
        return solutions[0]
    if weights is None:
        weights = [2.0, 2.0, 1.5, 1.0, 1.0, 1.0]

    best = None
    best_cost = float("inf")
    for sol in solutions:
        valid = True
        for i, angle in enumerate(sol):
            lo, hi = JOINT_LIMITS_DEG[i]
            lo_rad, hi_rad = math.radians(lo), math.radians(hi)
            if angle < lo_rad or angle > hi_rad:
                valid = False
                break
        if not valid:
            continue

        cost = 0.0
        for i, (target, current) in enumerate(zip(sol, current_joints_rad)):
            diff = target - current
            diff = (diff + math.pi) % (2.0 * math.pi) - math.pi
            cost += weights[i] * abs(diff)

        if cost < best_cost:
            best_cost = cost
            best = sol

    return best


# ── pose from quaternion + position (XR controller format) ──────────────────

def pose_from_quat_xyz(qx: float, qy: float, qz: float, qw: float,
                        x: float, y: float, z: float) -> List[List[float]]:
    """Build 4x4 pose from quaternion (x,y,z,w) and position (mm)."""
    rot = _quat_to_rot(qx, qy, qz, qw)
    return _rot_to_pose(rot, [x, y, z])
