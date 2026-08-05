"""Orbbec Femto Bolt detection — USB VID/PID + K4A device list cross-reference.

The Femto Bolt exposes a standard UVC RGB sensor (usable via V4L2/ffmpeg)
AND depth/IR sensors accessible only through the Orbbec SDK (K4A wrapper).
This module identifies which V4L2 nodes belong to Orbbec devices so the
auto-detect logic can create OrbbecSource (with mode switching) instead of
a plain CameraSource.
"""

import logging
import os
import re as _re
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

# Orbbec vendor ID; Femto Bolt product IDs
_ORBBEC_VID = "2bc5"
_ORBBEC_PIDS = {"0800", "0801", "0900"}  # Femto Bolt / Femto Mega variants


def is_orbbec_k4a_available() -> bool:
    try:
        import k4a  # noqa: F401
        from PIL import Image  # noqa: F401
        return True
    except ImportError:
        return False


def _read_sysfs_usb_ids(dev_name: str) -> Optional[str]:
    """Return the USB VID:PID string for a V4L2 device, e.g. '2bc5:0800'."""
    try:
        iface = os.path.realpath(f"/sys/class/video4linux/{dev_name}/device")
    except OSError:
        return None

    usb_dev = os.path.dirname(iface)
    for _ in range(6):
        id_path = os.path.join(usb_dev, "idVendor")
        if os.path.exists(id_path):
            try:
                with open(id_path) as fh:
                    vid = fh.read().strip().lower()
                with open(os.path.join(usb_dev, "idProduct")) as fh:
                    pid = fh.read().strip().lower()
                return f"{vid}:{pid}"
            except OSError:
                pass
        parent = os.path.dirname(usb_dev)
        if parent == usb_dev:
            break
        usb_dev = parent
    return None


def _is_orbbec_femto(dev_name: str) -> bool:
    usb_id = _read_sysfs_usb_ids(dev_name)
    if not usb_id:
        return False
    parts = usb_id.split(":")
    return len(parts) == 2 and parts[0] == _ORBBEC_VID and parts[1] in _ORBBEC_PIDS


def detect_orbbec_devices(v4l2_devices: List[str]) -> List[Dict]:
    """Return info dicts for each Orbbec Femto Bolt that matches a V4L2 node.

    Does NOT require the K4A wrapper — detection is purely USB VID/PID based.
    The K4A wrapper is only needed at runtime for depth/stereo capture.

    Args:
        v4l2_devices: All /dev/video* paths.

    Returns:
        List of dicts with keys: name, v4l2_nodes, usb_id.
    """
    result: List[Dict] = []
    seen_usb_ids: set = set()

    for path in v4l2_devices:
        dev_name = os.path.basename(path)
        usb_id = _read_sysfs_usb_ids(dev_name)
        if not usb_id:
            continue
        parts = usb_id.split(":")
        if len(parts) != 2 or parts[0] != _ORBBEC_VID or parts[1] not in _ORBBEC_PIDS:
            continue

        if usb_id in seen_usb_ids:
            continue
        seen_usb_ids.add(usb_id)

        # Find all V4L2 nodes sharing this USB ID
        nodes = []
        for p in v4l2_devices:
            dn = os.path.basename(p)
            if _read_sysfs_usb_ids(dn) == usb_id:
                nodes.append(p)

        result.append({
            "name": "Orbbec Femto Bolt",
            "usb_id": usb_id,
            "v4l2_nodes": sorted(nodes),
        })
        log.info("[Orbbec] detected %s (usb_id=%s) → %d V4L2 node(s): %s",
                 result[-1]["name"], usb_id, len(nodes), sorted(nodes))

    return result
