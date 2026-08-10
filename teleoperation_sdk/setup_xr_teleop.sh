#!/bin/bash
# Setup XR-Robotics teleoperation SDK on Jetson AGX Orin (ARM64 / aarch64)
# Run this ON the Jetson: bash setup_xr_teleop.sh
#
# The XR-Robotics PC Service runs on Jetson and communicates with the
# Meta Quest 3 headset via WiFi. The PC Service uses OpenXR protocol,
# which Quest 3 supports natively. The prebuilt PICO APK is replaced
# by a Quest 3 build of the Unity XR client.

set -e

SDK_DIR="$HOME/teleoperation_sdk"
REPO_PC_SERVICE="https://github.com/XR-Robotics/XRoboToolkit-PC-Service.git"
REPO_PYBIND="https://github.com/XR-Robotics/XRoboToolkit-PC-Service-Pybind.git"
REPO_TELEOP_SAMPLE="https://github.com/XR-Robotics/XRoboToolkit-Teleop-Sample-Python.git"

echo "=== XR-Robotics Teleop SDK Setup for Jetson Orin (Quest 3) ==="
echo ""

# 1. Install build dependencies
echo "[1/6] Installing build dependencies..."
sudo apt update
sudo apt install -y cmake build-essential git python3-dev libboost-all-dev

# 2. Clone repositories
echo "[2/6] Cloning repositories into $SDK_DIR ..."
mkdir -p "$SDK_DIR"
cd "$SDK_DIR"

if [ ! -d "XRoboToolkit-PC-Service" ]; then
    git clone "$REPO_PC_SERVICE"
else
    echo "  XRoboToolkit-PC-Service already exists — skipping clone"
fi

if [ ! -d "XRoboToolkit-PC-Service-Pybind" ]; then
    git clone "$REPO_PYBIND"
else
    echo "  XRoboToolkit-PC-Service-Pybind already exists — skipping clone"
fi

# 3. Build Python bindings (includes building PC Service SDK)
echo "[3/6] Building Python bindings for ARM64..."
cd "$SDK_DIR/XRoboToolkit-PC-Service-Pybind"

if [ -f "setup_orin.sh" ]; then
    bash setup_orin.sh
else
    echo "ERROR: setup_orin.sh not found — manual build required"
    echo "See: https://github.com/XR-Robotics/XRoboToolkit-PC-Service-Pybind"
    exit 1
fi

# 4. Install the Python package
echo "[4/6] Installing xrobotoolkit_sdk..."
pip install -e . 2>/dev/null || python setup.py install

# 5. Clone the upstream UR5e teleop sample (URDF + reference algorithm)
echo "[5/6] Cloning XRoboToolkit-Teleop-Sample-Python..."
if [ ! -d "$SDK_DIR/XRoboToolkit-Teleop-Sample-Python" ]; then
    git clone "$REPO_TELEOP_SAMPLE" "$SDK_DIR/XRoboToolkit-Teleop-Sample-Python"
else
    echo "  XRoboToolkit-Teleop-Sample-Python already exists — skipping clone"
fi

echo "[6/6] Installing teleop runtime dependencies..."
sudo -H python3 -m pip install numpy meshcat placo ur_rtde

# Verify
python3 -c "import xrobotoolkit_sdk as xrt; print('xrobotoolkit_sdk OK — version:', getattr(xrt, '__version__', 'unknown'))"
python3 -c "import placo, rtde_control, rtde_receive; print('teleop runtime OK')"

echo ""
echo "=== Setup complete ==="
echo "Next steps:"
echo "  1. Start the PC Service: /opt/apps/roboticsservice/runService.sh"
echo "  2. Build and install Quest 3 XR client APK (see teleoperation_sdk/README.md)"
echo "  3. Ensure Jetson and Quest 3 are on the same WiFi network"
echo "  4. Restart robot control panel: sudo systemctl restart robot_control_panel.service"
