#!/usr/bin/env bash
# Setup CAN interface on Jetson AGX for stepper motor control (500 kbps)
# Run once after enabling CAN pins via jetson-io.py and rebooting.
#
# Usage:  sudo bash setup_jetson_can.sh [interface] [bitrate]
#   interface  default: can0
#   bitrate    default: 500000

set -euo pipefail

IFACE="${1:-can0}"
BITRATE="${2:-500000}"

echo "[INFO] Configuring $IFACE at $BITRATE bps"

ip link set "$IFACE" down 2>/dev/null || true
ip link set "$IFACE" type can bitrate "$BITRATE"
ip link set "$IFACE" up

echo "[INFO] $IFACE is UP"
ip -details link show "$IFACE"
