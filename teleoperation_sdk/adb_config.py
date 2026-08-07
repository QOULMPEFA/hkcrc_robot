"""ADB reverse tunnel helpers for Quest 3 USB → TCP connectivity.

XRoboToolkit-Quest APK communicates over TCP (tracking data on port 63901,
video on UDP port 12345). When the Quest is connected via USB, ADB reverse
tunneling maps these ports from the headset to the PC:

  Quest app connects to 127.0.0.1:63901 → ADB forwards to PC:63901

Usage:
    from teleoperation_sdk.adb_config import setup_adb_reverse
    setup_adb_reverse()  # sets up both TCP tracking and UDP video tunnels
"""

import logging
import os
import subprocess
import sys

log = logging.getLogger(__name__)

# Path to bundled adb.exe
_ADB_DIR = os.path.dirname(os.path.abspath(__file__))
_ADB_EXE = os.path.join(_ADB_DIR, "tools", "adb.exe")

TRACKING_PORT = 63901   # XRoboToolkit TCP tracking data
VIDEO_PORT = 12345      # XRoboToolkit UDP video receiver


def _find_adb() -> str:
    """Locate adb executable — bundled one first, then system PATH."""
    if os.path.isfile(_ADB_EXE):
        return _ADB_EXE
    # Check system PATH
    import shutil
    path = shutil.which("adb")
    if path:
        return path
    raise FileNotFoundError(
        "adb not found. Place adb.exe in teleoperation_sdk/tools/ "
        "or install Android SDK Platform Tools."
    )


def _run_adb(args: list, timeout: int = 15) -> subprocess.CompletedProcess:
    adb = _find_adb()
    cmd = [adb] + args
    log.debug("adb: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def setup_adb_reverse_tcp(port: int = TRACKING_PORT) -> bool:
    """Set up ADB reverse tunnel for a TCP port (Quest → PC).

    Equivalent to: adb reverse tcp:<port> tcp:<port>
    """
    try:
        result = _run_adb(["reverse", f"tcp:{port}", f"tcp:{port}"])
        if result.returncode == 0:
            log.info("[ADB] Reverse TCP tunnel established: Quest:63901 → PC:%d", port)
            return True
        else:
            log.warning("[ADB] Reverse TCP tunnel failed: %s", result.stderr.strip())
            return False
    except FileNotFoundError as e:
        log.error("[ADB] %s", e)
        return False
    except Exception as e:
        log.error("[ADB] Reverse TCP error: %s", e)
        return False


def setup_adb_reverse_udp(port: int = VIDEO_PORT) -> bool:
    """Set up ADB reverse tunnel for a UDP port (Quest → PC).

    Equivalent to: adb reverse udp:<port> udp:<port>
    Used by XRoboToolkit video receiver.
    """
    try:
        result = _run_adb(["reverse", f"udp:{port}", f"udp:{port}"])
        if result.returncode == 0:
            log.info("[ADB] Reverse UDP tunnel established: Quest:12345 → PC:%d", port)
            return True
        else:
            log.warning("[ADB] Reverse UDP tunnel failed: %s", result.stderr.strip())
            return False
    except FileNotFoundError as e:
        log.error("[ADB] %s", e)
        return False
    except Exception as e:
        log.error("[ADB] Reverse UDP error: %s", e)
        return False


def setup_adb_reverse(tcp_port: int = TRACKING_PORT,
                      udp_port: int = VIDEO_PORT) -> bool:
    """Set up both TCP and UDP ADB reverse tunnels.

    Returns True if TCP tunnel succeeded (UDP is optional).
    """
    tcp_ok = setup_adb_reverse_tcp(tcp_port)
    # UDP is best-effort — not needed for basic teleop
    try:
        setup_adb_reverse_udp(udp_port)
    except Exception:
        pass
    return tcp_ok


def remove_adb_reverse() -> None:
    """Remove all ADB reverse tunnels."""
    try:
        _run_adb(["reverse", "--remove-all"])
        log.info("[ADB] All reverse tunnels removed")
    except Exception as e:
        log.error("[ADB] Failed to remove reverse tunnels: %s", e)


def list_adb_reverse() -> str:
    """List current ADB reverse tunnels."""
    try:
        result = _run_adb(["reverse", "--list"])
        return result.stdout.strip() or "(none)"
    except Exception as e:
        return f"(error: {e})"


def check_adb_device() -> bool:
    """Check if a Quest device is connected via ADB."""
    try:
        result = _run_adb(["devices"], timeout=5)
        lines = result.stdout.strip().split("\n")
        # First line is "List of devices attached", subsequent lines have serials
        devices = [l for l in lines[1:] if l.strip() and "device" in l]
        return len(devices) > 0
    except Exception:
        return False


# ── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if len(sys.argv) > 1 and sys.argv[1] in ("--remove", "-r"):
        remove_adb_reverse()
    elif len(sys.argv) > 1 and sys.argv[1] in ("--list", "-l"):
        print(list_adb_reverse())
    elif len(sys.argv) > 1 and sys.argv[1] in ("--check", "-c"):
        ok = check_adb_device()
        print("Device connected" if ok else "No device")
    else:
        print("Setting up ADB reverse tunnels...")
        ok = setup_adb_reverse()
        if ok:
            print("TCP tunnel OK. Run XRoboToolkit-Quest APK on headset.")
            print("It should connect to 127.0.0.1:63901 (auto-forwarded via ADB).")
        else:
            print("Failed. Is Quest 3 connected via USB? Run: adb devices")
