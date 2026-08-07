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

# Import server module so we can inject dev_mode handlers before run
from robot_control.web_gui import server as _server

# Wire in developer mode (hotspot ↔ WiFi switching)
try:
    import dev_mode
    dev_mode.register_handlers(_server.socketio)
    # Start hotspot on boot (always, unless dev_mode was on before reboot)
    import threading
    def _boot_hotspot():
        import time
        time.sleep(2)  # let server start first
        if not dev_mode._is_wifi_connected() and not dev_mode._is_hotspot_running():
            dev_mode.start_hotspot()
    threading.Thread(target=_boot_hotspot, daemon=True).start()
except Exception as _e:
    import logging
    logging.warning("[Bootstrap] dev_mode init skipped: %s", _e)

if __name__ == "__main__":
    _server.main()
