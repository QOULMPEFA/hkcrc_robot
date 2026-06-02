#!/usr/bin/env bash
# Install stepper_motor_sdk into the currently active conda / venv environment.
# Do NOT run with sudo — packages install into the active env.
#
# Usage (activate your env first):
#   conda activate robot_env
#   bash scripts/install_conda.sh

set -euo pipefail

PYTHON="${PYTHON:-python3}"

echo "[1/3] Checking Python >= 3.10 ..."
"$PYTHON" -c "import sys; assert sys.version_info >= (3,10), f'Need Python 3.10+, got {sys.version}'"
echo "      $($PYTHON --version)  →  $(which $PYTHON)"

echo "[2/3] Installing python-can ..."
pip install python-can

echo "[3/3] Installing stepper_motor_sdk in editable mode ..."
# Remove stale .egg-info left by a previous sudo install (owned by root)
sudo rm -rf stepper_motor_sdk.egg-info 2>/dev/null || rm -rf stepper_motor_sdk.egg-info 2>/dev/null || true
pip install -e .

echo ""
echo "Done. Verify with:"
echo "  python3 -c 'import stepper_motor_sdk; print(stepper_motor_sdk.__version__)'"
