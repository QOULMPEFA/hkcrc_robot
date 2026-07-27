#!/bin/bash
# Deploy teleop module and Web GUI updates to Jetson
# Run from the project root (code/) directory on your development machine.
#
# Usage: bash scripts/deploy_teleop.sh [jetson_ip]
#   Default IP: 192.168.56.101

set -e

JETSON="${1:-192.168.56.101}"
JETSON_USER="jetson"
PROJECT_DIR="/home/jetson/robot_control"
SSH="ssh -o StrictHostKeyChecking=no ${JETSON_USER}@${JETSON}"

echo "=== Deploying to Jetson @ ${JETSON} ==="

# Test connection
if ! ${SSH} "echo OK" > /dev/null 2>&1; then
    echo "ERROR: Cannot reach Jetson at ${JETSON}"
    exit 1
fi

# Deploy robot_control Python files
echo "[1/3] Deploying Python modules..."
scp robot_control/ur5_ik.py    "${JETSON_USER}@${JETSON}:${PROJECT_DIR}/robot_control/ur5_ik.py"
scp robot_control/teleop.py    "${JETSON_USER}@${JETSON}:${PROJECT_DIR}/robot_control/teleop.py"
scp robot_control/module_base.py "${JETSON_USER}@${JETSON}:${PROJECT_DIR}/robot_control/module_base.py"
scp robot_control/robot.py     "${JETSON_USER}@${JETSON}:${PROJECT_DIR}/robot_control/robot.py"
scp robot_control/arm.py       "${JETSON_USER}@${JETSON}:${PROJECT_DIR}/robot_control/arm.py"

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
