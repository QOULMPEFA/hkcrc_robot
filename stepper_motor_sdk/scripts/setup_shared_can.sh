#!/usr/bin/env bash
# Configure shared CAN bus: physical can0 (Ranger) + virtual vcan0 (stepper motor)
# Uses Linux can-gw kernel module to bridge both interfaces transparently.
#
# Usage: sudo bash setup_shared_can.sh [bitrate]
#   bitrate  default: 500000

set -euo pipefail

BITRATE="${1:-500000}"

echo "[1/4] Loading kernel modules ..."
modprobe vcan
modprobe can-gw

echo "[2/4] Configuring physical interface can0 at ${BITRATE} bps ..."
ip link set can0 down 2>/dev/null || true
ip link set can0 type can bitrate "${BITRATE}"
ip link set can0 up

echo "[3/4] Creating virtual interface vcan0 ..."
if ip link show vcan0 &>/dev/null; then
    ip link set vcan0 down
    ip link delete vcan0
fi
ip link add dev vcan0 type vcan
ip link set vcan0 up

echo "[4/4] Setting up can-gw rules (can0 <-> vcan0) ..."
# Remove existing rules first (ignore errors if none exist)
cangw -D -s can0  -d vcan0 -e 2>/dev/null || true
cangw -D -s vcan0 -d can0  -e 2>/dev/null || true
# Add bidirectional forwarding
cangw -A -s can0  -d vcan0 -e
cangw -A -s vcan0 -d can0  -e

echo ""
echo "Done. Active gateway rules:"
cangw -L

echo ""
echo "Verify with:"
echo "  candump vcan0    # should mirror all frames on can0"
echo ""
echo "ranger_base_node  → port_name: can0"
echo "stepper_motor_sdk → channel:   vcan0"
