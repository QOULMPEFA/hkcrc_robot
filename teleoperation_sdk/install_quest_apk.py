"""Install the teleop APK to Oculus Quest 3 via ADB.

Usage:
  python -m teleoperation_sdk.install_quest_apk
  python -m teleoperation_sdk.install_quest_apk --reinstall

Prerequisites:
  - Quest 3 connected via USB
  - Developer Mode enabled on Quest 3
  - ADB installed and in PATH
"""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        description="Install teleop APK to Oculus Quest 3"
    )
    parser.add_argument("--reinstall", "-r", action="store_true",
                        help="Force reinstall even if already installed")
    args = parser.parse_args()

    from teleoperation_sdk.oculus_reader.reader import OculusReader

    reader = OculusReader(run=False)
    reader.install(reinstall=args.reinstall)

    print("\nAPK installed. Verify in Quest 3 app library: 'Oculus Teleop'")
    print("Run teleop with: python run_teleop_ur5e.py")


if __name__ == "__main__":
    main()
