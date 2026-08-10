#!/bin/bash
# Deploy teleop module and Web GUI updates to Jetson
# Run from the project root (code/) directory on your development machine.
#
# Usage: bash scripts/deploy_teleop.sh [jetson_ip]
#   Default IP: 192.168.20.63

set -e

JETSON="${1:-192.168.20.63}"
JETSON_USER="yu"
PROJECT_DIR="/home/yu/robot_code"
SSH="ssh -o StrictHostKeyChecking=no ${JETSON_USER}@${JETSON}"

echo "=== Deploying to Jetson @ ${JETSON} ==="

# Test connection
if ! ${SSH} "echo OK" > /dev/null 2>&1; then
    echo "ERROR: Cannot reach Jetson at ${JETSON}"
    exit 1
fi

# Deploy backend Python files
echo "[1/3] Deploying teleop backend..."
scp teleoperation_sdk/teleop.py      "${JETSON_USER}@${JETSON}:${PROJECT_DIR}/teleoperation_sdk/teleop.py"
scp teleoperation_sdk/xr_ur5e.py     "${JETSON_USER}@${JETSON}:${PROJECT_DIR}/teleoperation_sdk/xr_ur5e.py"
scp teleoperation_sdk/vr_backends.py "${JETSON_USER}@${JETSON}:${PROJECT_DIR}/teleoperation_sdk/vr_backends.py"
scp robot_control/web_gui/server.py  "${JETSON_USER}@${JETSON}:${PROJECT_DIR}/robot_control/web_gui/server.py"

# Deploy Web GUI files
echo "[2/3] Deploying Web GUI..."
scp robot_control/web_gui/server.py          "${JETSON_USER}@${JETSON}:${PROJECT_DIR}/robot_control/web_gui/server.py"
scp robot_control/web_gui/templates/index.html "${JETSON_USER}@${JETSON}:${PROJECT_DIR}/robot_control/web_gui/templates/index.html"
scp robot_control/web_gui/static/app.js       "${JETSON_USER}@${JETSON}:${PROJECT_DIR}/robot_control/web_gui/static/app.js"
scp robot_control/web_gui/static/i18n.js      "${JETSON_USER}@${JETSON}:${PROJECT_DIR}/robot_control/web_gui/static/i18n.js"

# Restart service
echo "[3/3] Restarting service..."
${SSH} "sudo systemctl restart robot_control_panel.service"
sleep 2
${SSH} "systemctl status robot_control_panel.service --no-pager -l" || true

echo ""
echo "=== Deployment complete ==="
echo "Open https://${JETSON}:8443 in browser (Ctrl+Shift+R to force reload)"
