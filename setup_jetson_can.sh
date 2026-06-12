#!/usr/bin/env bash
# Setup CAN interface on Jetson AGX.
# Usage:  sudo bash setup_jetson_can.sh [interface] [bitrate]

set -euo pipefail

IFACE="${1:-can0}"
BITRATE="${2:-500000}"

echo "[INFO] Configuring $IFACE at $BITRATE bps with restart-ms 100"

ip link set "$IFACE" down 2>/dev/null || true
ip link set "$IFACE" type can bitrate "$BITRATE" restart-ms 100
ip link set "$IFACE" up

echo "[INFO] $IFACE is UP"
ip -details link show "$IFACE"
