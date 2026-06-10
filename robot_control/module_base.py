"""Abstract base for all robot hardware modules.

Each module encapsulates one hardware subsystem (UGV, lift, arm, hand, camera, …).
The Robot container discovers and loads modules via config; the web GUI uses each
module's metadata to dynamically register SocketIO handlers and render controls.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List

log = logging.getLogger(__name__)

EmitFn = Callable[..., None]

# SocketIO handler: fn(data: dict, emit: EmitFn) -> None
EventHandler = Callable[[dict, EmitFn], None]


class RobotModule(ABC):
    """Base class that every hardware module must implement."""

    # ── metadata ────────────────────────────────────────────────────────────

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique module identifier, e.g. "ugv", "lift", "arm", "camera"."""
        ...

    @property
    def display_name(self) -> str:
        """Human-readable label for the Web GUI."""
        return self.name

    # ── lifecycle ───────────────────────────────────────────────────────────

    @abstractmethod
    def start(self) -> None:
        """Initialise hardware connection and background threads."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Release hardware and join background threads."""
        ...

    # ── state ───────────────────────────────────────────────────────────────

    @abstractmethod
    def get_state(self) -> Dict[str, Any]:
        """Return current module state as a flat dict.

        Values must be JSON-serialisable (int, float, bool, str, list, None).
        Include a "connected" key so the GUI can show online/offline status.
        """
        ...

    @abstractmethod
    def get_state_schema(self) -> Dict[str, Any]:
        """Return a default-state dict used when the module is disabled.

        Example: {"connected": False, "height_mm": 0.0, ...}
        This keeps the Web GUI from crashing when a module is missing.
        """
        ...

    # ── emergency ───────────────────────────────────────────────────────────

    def emergency_stop(self) -> None:
        """Stop all motion immediately. Default: delegate to stop()."""
        self.stop()

    # ── SocketIO bridge ─────────────────────────────────────────────────────

    def get_socketio_handlers(self) -> Dict[str, EventHandler]:
        """Return {event_name: handler_fn} for dynamic SocketIO registration.

        handler_fn signature:  fn(data: dict, emit: EmitFn) -> None
        Use emit(event_name, data) to push events back to the client.

        Example:
            {"ugv_velocity": self._on_velocity, "ugv_stop": self._on_stop}
        """
        return {}

    # ── helpers ─────────────────────────────────────────────────────────────

    def is_healthy(self) -> bool:
        """Quick health check. Override if the module has a known fault concept."""
        state = self.get_state()
        return state.get("connected", False) and not state.get("is_fault", False)

    @staticmethod
    def _require_keys(data: dict, *keys: str) -> List[str]:
        """Validate that required keys are present in data dict.

        Returns a list of missing key names (empty = ok).
        """
        return [k for k in keys if k not in data]
