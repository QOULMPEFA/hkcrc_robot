"""RealSense device detection — cross-reference SDK devices with V4L2 nodes.

Matches by USB bus-port extracted from the SDK's physical_port path,
not by serial number (sysfs serials differ from SDK serials on Jetson).
"""

import logging
import os
import re as _re
from typing import Dict, List, Optional

log = logging.getLogger(__name__)


def is_realsense_available() -> bool:
    try:
        import pyrealsense2  # noqa: F401
        from PIL import Image  # noqa: F401
        return True
    except ImportError:
        return False


def _extract_usb_port(physical_port: str) -> Optional[str]:
    """Parse the USB bus-port from a RealSense physical_port path.

    Example:
        .../usb2/2-3/2-3.2/2-3.2:1.0/video4linux/video10  →  "2-3.2"
    """
    parts = physical_port.replace("\\", "/").split("/")
    for part in reversed(parts):
        if _re.match(r"^\d+-(\d+\.)*\d+$", part):
            return part
    return None


def _resolve_usb_device_key_realsense(dev_name: str) -> str:
    """Same logic as camera._resolve_usb_device_key — resolve V4L2 dev to USB bus-port."""
    try:
        iface = os.path.realpath(f"/sys/class/video4linux/{dev_name}/device")
    except OSError:
        return dev_name

    usb_dev = os.path.dirname(iface)
    for _ in range(6):
        if os.path.exists(os.path.join(usb_dev, "authorized")):
            return os.path.basename(usb_dev)
        parent = os.path.dirname(usb_dev)
        if parent == usb_dev:
            break
        usb_dev = parent

    parts = iface.replace("\\", "/").split("/")
    for part in parts:
        if _re.match(r"^\d+-(\d+\.)*\d+$", part):
            return part
    return dev_name


def detect_realsense_devices(v4l2_devices: List[str]) -> List[Dict]:
    """Return info dicts for each RealSense device that matches a V4L2 node.

    Args:
        v4l2_devices: All /dev/video* paths (e.g. from _detect_cameras).

    Returns:
        List of dicts with keys: serial, name, usb_key, v4l2_nodes.
        Each dict describes one physical RealSense camera.
    """
    import pyrealsense2 as rs

    ctx = rs.context()
    if len(ctx.devices) == 0:
        return []

    # Build USB-key → V4L2-nodes map from the V4L2 list
    v4l2_by_usb: Dict[str, List[str]] = {}
    for path in v4l2_devices:
        dev_name = os.path.basename(path)
        usb_key = _resolve_usb_device_key_realsense(dev_name)
        v4l2_by_usb.setdefault(usb_key, []).append(path)

    # Build USB-key → SDK-device map
    sdk_by_usb: Dict[str, Dict] = {}
    for d in ctx.devices:
        phys = d.get_info(rs.camera_info.physical_port)
        usb_key = _extract_usb_port(phys)
        if usb_key:
            sdk_by_usb[usb_key] = {
                "serial": d.get_info(rs.camera_info.serial_number),
                "name": d.get_info(rs.camera_info.name),
            }
            log.info("[RealSense] SDK device: %s → usb_key=%s",
                     sdk_by_usb[usb_key]["name"], usb_key)

    # Cross-reference: where USB keys match, this is a RealSense camera
    result = []
    for usb_key, sdk_info in sdk_by_usb.items():
        nodes = v4l2_by_usb.get(usb_key, [])
        result.append({
            "serial": sdk_info["serial"],
            "name": sdk_info["name"],
            "usb_key": usb_key,
            "v4l2_nodes": nodes,
        })
        if nodes:
            log.info("[RealSense] matched %s (serial=%s) → %d V4L2 node(s): %s",
                     sdk_info["name"], sdk_info["serial"], len(nodes), nodes)
        else:
            log.warning("[RealSense] %s (serial=%s) has no matching V4L2 nodes",
                        sdk_info["name"], sdk_info["serial"])

    return result
