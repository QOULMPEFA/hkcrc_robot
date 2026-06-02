#!/usr/bin/env bash
# 检查 Jetson AGX 上可用的 CAN 接口数量
echo "=== CAN interfaces ==="
ip link show type can 2>/dev/null || ip link | grep -i can

echo ""
echo "=== Jetson CAN kernel modules ==="
lsmod | grep -i can

echo ""
echo "=== /sys CAN devices ==="
ls /sys/class/net/ | grep can
