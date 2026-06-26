#!/usr/bin/env python3
"""Bootstrap for PyArmor-encrypted robot_control deployment.

This is the systemd ExecStart target. Each obfuscated module activates
the PyArmor runtime automatically on import — this launcher just ensures
the runtime and robot_control are on sys.path.
"""
import os
import sys

# Put the project root on sys.path so Python can discover:
#   pyarmor_runtime_000000/   (decryption engine)
#   robot_control/            (obfuscated package)
_project_root = os.path.dirname(os.path.abspath(__file__))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from robot_control.web_gui.server import main

if __name__ == "__main__":
    main()
