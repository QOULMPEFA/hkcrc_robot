"""RealSense SDK (pyrealsense2) integration.

Provides:
  - is_realsense_available()  → True if pyrealsense2 + PIL are importable
  - detect_realsense_devices() → cross-reference SDK devices with V4L2 nodes
  - RealSensePipeline         → SDK capture + JPEG encode in-process
"""

from camera_sdk.realsense.detector import is_realsense_available, detect_realsense_devices
from camera_sdk.realsense.pipeline import RealSensePipeline

__all__ = ["is_realsense_available", "detect_realsense_devices", "RealSensePipeline"]
