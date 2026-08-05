"""Orbbec SDK (K4A wrapper) integration for Femto Bolt.

Provides:
  - is_orbbec_k4a_available()  → True if k4a + Pillow are importable
  - detect_orbbec_devices()    → detect Femto Bolt devices via USB + K4A
  - OrbbecPipeline             → depth/stereo capture + JPEG encode in-process
"""

from camera_sdk.orbbec.detector import is_orbbec_k4a_available, detect_orbbec_devices
from camera_sdk.orbbec.pipeline import OrbbecPipeline

__all__ = ["is_orbbec_k4a_available", "detect_orbbec_devices", "OrbbecPipeline"]
