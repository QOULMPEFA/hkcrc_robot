"""Gimbal control optimizer — physics-based cross-axis decoupling + adaptive motion.

Based on the 2-axis gimbal balance model from guide/两轴云台平衡原理.md:

  Pitch axis:  (J_Y2 + J_Z3)·β̈ + (J_Z2 + J_Z3)·sin²β = M₁    (eq 7)
  Yaw axis:    J_Z3·(γ̈ - sinβ - β̈·cosβ) = M₂                    (eq 8)

Key insight: the yaw angle β appears inside sin²β in the pitch torque equation.
When yaw ≠ 0°, the effective inertia on the pitch axis increases, causing
slower response and potential oscillation at extreme compound angles.

Four optimization layers:

  A. Cross-axis decoupling — compensate sin²β coupling in pitch command
     gain_effective = gain * (1 + k_cross * sin²(yaw))

  B. Adaptive velocity — scale servo speed by move distance:
     short moves → slower (avoid overshoot), long moves → faster

  C. Anti-oscillation damping — on small moves near target, apply soft
     low-pass to prevent servo encoder hunting

  D. Compound-angle power boost — at extreme compound angles, increase
     servo power to overcome higher effective inertia

Usage (in gimbal.py):
    from gimbal_sdk.gimbal_optimizer import GimbalOptimizer

    self._optimizer = GimbalOptimizer()
    # In move_to():
    s_yaw, s_pitch, velocity, power = self._optimizer.optimize(
        target_yaw, target_pitch,
        current_yaw=self._current_yaw, current_pitch=self._current_pitch)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, Tuple

log = logging.getLogger(__name__)


@dataclass
class OptimizerConfig:
    """Tunable parameters. Set enabled=False to bypass all layers."""

    enabled: bool = True

    # Layer A — Cross-axis decoupling
    cross_decouple: bool = True
    cross_k_yaw: float = 0.15       # pitch gain boost from yaw position
    cross_k_pitch: float = 0.10     # yaw gain boost from pitch position

    # Layer B — Adaptive velocity
    adaptive_velocity: bool = True
    vel_min: float = 50.0            # deg/s — velocity floor for tiny moves
    vel_max: float = 300.0           # deg/s — velocity ceiling for large moves
    vel_small_move: float = 2.0      # deg — below this, use vel_min
    vel_large_move: float = 30.0     # deg — above this, use vel_max

    # Layer C — Anti-oscillation damping
    anti_oscillation: bool = True
    damp_threshold: float = 1.5      # deg — moves smaller than this get damped
    damp_factor: float = 0.6         # 0-1 — how much to attenuate small moves

    # Layer D — Compound-angle power boost
    compound_power: bool = True
    power_base: float = 3000.0       # mW — default servo power
    power_boost_max: float = 1.3     # max power multiplier at extreme angles


class GimbalOptimizer:
    """Physics-informed gimbal command optimizer.

    Wraps move_to() calls. Stateless per-call — toggling on/off is instant
    with no transient artifacts.
    """

    def __init__(self, config: OptimizerConfig | None = None):
        self.cfg = config or OptimizerConfig()
        self._stats: Dict[str, Any] = {}

    # ── public API ──────────────────────────────────────────────────────────

    def optimize(self, target_yaw: float, target_pitch: float,
                 current_yaw: float = 0.0, current_pitch: float = 0.0,
                 base_velocity: float = 200.0, base_power: float = 3000.0,
                 ) -> Tuple[float, float, float, float]:
        """Optimize a move_to command. Returns (yaw, pitch, velocity, power).

        Call site: gimbal.py move_to(), just before sending servo commands.
        """
        if not self.cfg.enabled:
            return target_yaw, target_pitch, base_velocity, base_power

        yaw, pitch = float(target_yaw), float(target_pitch)
        velocity = float(base_velocity)
        power = float(base_power)

        abs_yaw_rad = abs(math.radians(current_yaw))
        abs_pitch_rad = abs(math.radians(current_pitch))
        dyaw = abs(yaw - current_yaw)
        dpitch = abs(pitch - current_pitch)
        dist = math.sqrt(dyaw ** 2 + dpitch ** 2)

        # Layer A — Cross-axis decoupling: compensate sin²β inertia
        yaw, pitch = self._cross_decouple(yaw, pitch, abs_yaw_rad, abs_pitch_rad)

        # Layer B — Adaptive velocity: scale by move distance
        velocity = self._adaptive_velocity(dist, velocity)

        # Layer C — Anti-oscillation: damp tiny moves near target
        yaw, pitch = self._anti_oscillation(yaw, pitch, current_yaw, current_pitch)

        # Layer D — Compound-angle power boost
        power = self._compound_power(abs_yaw_rad, abs_pitch_rad, power)

        self._stats = {
            "dist_deg": round(dist, 2),
            "velocity": round(velocity, 0),
            "power": round(power, 0),
            "yaw_correction": round(yaw - target_yaw, 3),
            "pitch_correction": round(pitch - target_pitch, 3),
        }
        return yaw, pitch, velocity, power

    def get_config(self) -> Dict[str, Any]:
        """Export config + stats for web GUI."""
        return {
            "enabled": self.cfg.enabled,
            "cross_decouple": self.cfg.cross_decouple,
            "cross_k_yaw": self.cfg.cross_k_yaw,
            "cross_k_pitch": self.cfg.cross_k_pitch,
            "adaptive_velocity": self.cfg.adaptive_velocity,
            "vel_min": self.cfg.vel_min,
            "vel_max": self.cfg.vel_max,
            "anti_oscillation": self.cfg.anti_oscillation,
            "damp_threshold": self.cfg.damp_threshold,
            "damp_factor": self.cfg.damp_factor,
            "compound_power": self.cfg.compound_power,
            "power_boost_max": self.cfg.power_boost_max,
            "stats": dict(self._stats),
        }

    def update_config(self, data: Dict[str, Any]) -> None:
        """Partial config update from web GUI."""
        for key, val in data.items():
            if hasattr(self.cfg, key):
                setattr(self.cfg, key, val)
        changed = {k: v for k, v in data.items() if hasattr(self.cfg, k)}
        if changed:
            log.info("[GimbalOptimizer] %s", changed)

    # ── Layer A: Cross-axis decoupling ──────────────────────────────────────

    def _cross_decouple(self, yaw: float, pitch: float,
                         abs_yaw_rad: float, abs_pitch_rad: float
                         ) -> Tuple[float, float]:
        """Compensate position-dependent inertia from eq 7-8.

        sin²(yaw) → extra load on pitch axis → boost pitch command
        sin²(pitch) → extra load on yaw axis → boost yaw command
        """
        if not self.cfg.cross_decouple:
            return yaw, pitch

        sin2_yaw = math.sin(abs_yaw_rad) ** 2
        sin2_pitch = math.sin(abs_pitch_rad) ** 2

        pitch_factor = 1.0 + self.cfg.cross_k_yaw * sin2_yaw
        yaw_factor = 1.0 + self.cfg.cross_k_pitch * sin2_pitch

        return yaw * yaw_factor, pitch * pitch_factor

    # ── Layer B: Adaptive velocity ──────────────────────────────────────────

    def _adaptive_velocity(self, dist_deg: float, base_vel: float) -> float:
        """Scale servo speed by move magnitude.

        Short moves → slower (precision, less overshoot)
        Long moves  → faster (responsiveness)
        """
        if not self.cfg.adaptive_velocity:
            return base_vel

        d = dist_deg
        v_min = self.cfg.vel_min
        v_max = self.cfg.vel_max
        d_small = self.cfg.vel_small_move
        d_large = self.cfg.vel_large_move

        if d <= d_small:
            return v_min
        elif d >= d_large:
            return v_max
        else:
            # Linear interpolation between min and max
            frac = (d - d_small) / (d_large - d_small)
            return v_min + (v_max - v_min) * frac

    # ── Layer C: Anti-oscillation damping ───────────────────────────────────

    def _anti_oscillation(self, yaw: float, pitch: float,
                           cur_yaw: float, cur_pitch: float
                           ) -> Tuple[float, float]:
        """Attenuate very small moves to prevent servo encoder hunting.

        When the commanded delta is tiny (< damp_threshold), the servo may
        oscillate between adjacent encoder positions. Soft damping prevents
        this without introducing a hard deadband.
        """
        if not self.cfg.anti_oscillation:
            return yaw, pitch

        thresh = self.cfg.damp_threshold
        factor = self.cfg.damp_factor

        dyaw = yaw - cur_yaw
        dpitch = pitch - cur_pitch

        if abs(dyaw) < thresh:
            yaw = cur_yaw + dyaw * factor
        if abs(dpitch) < thresh:
            pitch = cur_pitch + dpitch * factor

        return yaw, pitch

    # ── Layer D: Compound-angle power boost ─────────────────────────────────

    def _compound_power(self, abs_yaw_rad: float, abs_pitch_rad: float,
                         base_power: float) -> float:
        """Increase servo power at extreme compound angles.

        When both axes are far from center, the effective load is higher.
        Boost power to maintain responsiveness without changing velocity.
        """
        if not self.cfg.compound_power:
            return base_power

        # Compound severity: average of sin² for both axes
        coupling = (math.sin(abs_yaw_rad) ** 2 +
                    math.sin(abs_pitch_rad) ** 2) / 2.0
        boost = 1.0 + (self.cfg.power_boost_max - 1.0) * coupling
        return base_power * boost
