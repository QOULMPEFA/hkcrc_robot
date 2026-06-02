#!/usr/bin/env bash
# Install stepper_motor_sdk and all dependencies.
# Run from the repo root.  Works on Ubuntu/Jetson JetPack.

set -euo pipefail

PYTHON="${PYTHON:-python3}"

echo "[1/4] Checking Python >= 3.10 ..."
"$PYTHON" -c "import sys; assert sys.version_info >= (3,10), f'Need Python 3.10+, got {sys.version}'"

echo "[2/4] Installing system packages for python-can socketcan ..."
if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq can-utils iproute2 python3-pip python3-venv
fi

echo "[3/4] Installing Python packages ..."
# Ensure pip is available (Jetson JetPack minimal images often ship without it)
if ! "$PYTHON" -m pip --version &>/dev/null; then
    echo "  pip not found — bootstrapping with ensurepip ..."
    "$PYTHON" -m ensurepip --upgrade || true
fi
if ! "$PYTHON" -m pip --version &>/dev/null; then
    echo "  ensurepip unavailable — installing via get-pip.py ..."
    curl -fsSL https://bootstrap.pypa.io/get-pip.py | sudo "$PYTHON"
fi
"$PYTHON" -m pip install --upgrade pip
"$PYTHON" -m pip install python-can

echo "[4/4] Installing stepper_motor_sdk in editable mode ..."
"$PYTHON" -m pip install -e .

echo ""
echo "Done.  Verify with:"
echo "  python3 -c 'import stepper_motor_sdk; print(stepper_motor_sdk.__version__)'"
