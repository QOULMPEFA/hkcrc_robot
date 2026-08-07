#!/usr/bin/env python3
"""UR5e Teleoperation — Quest 3 via ADB/USB — Entry Point.

Usage:
  # Install APK to Quest 3 first (one-time):
  python -m teleoperation_sdk.install_quest_apk

  # Run with defaults:
  python run_teleop_ur5e.py

  # Run with custom UR5 IP:
  python run_teleop_ur5e.py --ur-host 192.168.1.100

  # Use left controller instead of right:
  python run_teleop_ur5e.py --controller left

  # Adjust sensitivity:
  python run_teleop_ur5e.py --scale 0.3 --rot-scale 0.2

  # Run for 60 seconds then stop:
  python run_teleop_ur5e.py --duration 60

Prerequisites:
  1. Quest 3 connected via USB with Developer Mode enabled
  2. ADB installed (Android SDK Platform Tools)
  3. teleop-debug.apk installed on Quest 3
  4. UR5e robot connected and reachable via TCP
"""

import argparse
import logging
import sys


def main():
    parser = argparse.ArgumentParser(
        description="UR5e Teleoperation via Oculus Quest 3 (ADB/USB)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Coordinate Mapping (--vr-map):
  Default maps VR(X=right, Y=up, Z=forward) → UR5(X=forward, Y=left, Z=up):
    UR5_X = VR_Z
    UR5_Y = VR_X
    UR5_Z = VR_Y
  Equivalent to --vr-map "0,0,1, 1,0,0, 0,1,0"
  Adjust sign flips based on UR5 mounting orientation.
        """,
    )

    # UR5 connection
    parser.add_argument("--ur-host", default="192.168.56.101",
                        help="UR5 controller IP (default: 192.168.56.101)")
    parser.add_argument("--ur-port", type=int, default=30001,
                        help="UR5 URScript port (default: 30001)")
    parser.add_argument("--ur-state-port", type=int, default=30002,
                        help="UR5 state read port (default: 30002)")

    # VR controller
    parser.add_argument("--controller", choices=["left", "right"], default="right",
                        help="Which Quest controller drives the arm (default: right)")

    # Sensitivity
    parser.add_argument("--scale", type=float, default=0.5,
                        help="Position scaling factor (default: 0.5)")
    parser.add_argument("--rot-scale", type=float, default=0.3,
                        help="Rotation scaling factor (default: 0.3)")

    # Coordinate mapping
    parser.add_argument("--vr-map", type=str, default=None,
                        help="9 comma-separated values for 3x3 VR→UR5 mapping matrix (row-major)")

    # Workspace safety
    parser.add_argument("--ws-min", type=float, nargs=3, default=[-500, -500, 0],
                        metavar=("X", "Y", "Z"),
                        help="Workspace min XYZ in mm (default: -500 -500 0)")
    parser.add_argument("--ws-max", type=float, nargs=3, default=[500, 500, 850],
                        metavar=("X", "Y", "Z"),
                        help="Workspace max XYZ in mm (default: 500 500 850)")

    # Runtime
    parser.add_argument("--duration", type=float, default=0,
                        help="Run duration in seconds (0 = run until Ctrl+C)")
    parser.add_argument("--print-interval", type=float, default=1.0,
                        help="Status print interval in seconds (default: 1.0)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable debug logging")

    # Install helper
    parser.add_argument("--install-apk", action="store_true",
                        help="Install teleop APK to Quest 3 and exit")

    args = parser.parse_args()

    # Logging setup
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Install APK mode
    if args.install_apk:
        from teleoperation_sdk.oculus_reader.reader import OculusReader
        reader = OculusReader(run=False)
        reader.install(reinstall=True)
        print("APK installed. You can now run: python run_teleop_ur5e.py")
        return

    # Parse VR mapping matrix
    vr_map = None
    if args.vr_map:
        values = [float(v.strip()) for v in args.vr_map.split(",")]
        if len(values) != 9:
            print("Error: --vr-map requires exactly 9 comma-separated values")
            sys.exit(1)
        vr_map = values

    # Build config
    config = {
        "ur_host": args.ur_host,
        "urscript_port": args.ur_port,
        "ur_state_port": args.ur_state_port,
        "arm_controller": args.controller,
        "displacement_scale": args.scale,
        "rotation_scale": args.rot_scale,
        "workspace_min_xyz": tuple(args.ws_min),
        "workspace_max_xyz": tuple(args.ws_max),
    }
    if vr_map:
        config["vr_to_ur5_matrix"] = vr_map

    # Run
    from teleoperation_sdk.teleop_ur5e_adb import UR5eTeleopADB

    teleop = UR5eTeleopADB(config)

    if not teleop.connect():
        print("\nConnection failed. Troubleshooting:")
        print("  1. Quest 3 USB: run 'adb devices' — should show device serial")
        print("  2. Developer Mode: enable in Meta Quest app on phone")
        print("  3. UR5: verify %s:%d is reachable" % (args.ur_host, args.ur_port))
        print("  4. APK: run with --install-apk to install teleop APK on Quest")
        sys.exit(1)

    try:
        teleop.run(duration=args.duration, print_interval=args.print_interval)
    except KeyboardInterrupt:
        pass
    finally:
        teleop.disconnect()


if __name__ == "__main__":
    main()
