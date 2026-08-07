#!/usr/bin/env python3
"""Gimbal CH340 diagnostic — run on the robot computer to find the issue."""

import sys
import logging

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("diagnose")

print("=" * 60)
print("  Gimbal CH340 Diagnostic")
print("=" * 60)

# ── Step 1: List all serial ports ──────────────────────────────────────────

print("\n── Step 1: Serial ports on this machine ──")
try:
    import serial.tools.list_ports
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("  ❌ NO serial ports found! Check USB cable and CH340 driver.")
        print("     Linux: run 'lsusb' and 'dmesg | grep -i ch34'")
        print("     Windows: check Device Manager → Ports (COM & LPT)")
        sys.exit(1)

    for p in ports:
        marker = ""
        hwid = (p.hwid or "").upper()
        desc = (p.description or "").upper()
        if "1A86:7523" in hwid:
            marker = " ← CH340 (VID:PID match)"
        elif "1A86:5512" in hwid:
            marker = " ← CH341"
        elif "CH340" in desc or "CH341" in desc:
            marker = " ← CH34x (description match)"
        print(f"  {p.device}")
        print(f"    description: {p.description}")
        print(f"    hwid:       {p.hwid}{marker}")
        print()
except Exception as e:
    print(f"  ❌ Error listing ports: {e}")
    sys.exit(1)

# ── Step 2: Find and test CH340 port ────────────────────────────────────────

print("── Step 2: Find CH340 port ──")
try:
    from gimbal_sdk.servo_lib import ServoController
    sc = ServoController()
    print(f"  ✓ Connected on: {sc.SERVO_PORT_NAME}")
    print(f"  ✓ Baudrate: {sc.SERVO_BAUDRATE}")
except Exception as e:
    print(f"  ❌ Connection failed: {e}")
    sys.exit(1)

# ── Step 3: Scan for servos ─────────────────────────────────────────────────

print("\n── Step 3: Scan servo bus (IDs 0-5) ──")
try:
    mgr = sc.uservo
    found_any = False
    for sid in range(6):
        try:
            angle = mgr.query_servo_angle(sid)
            if angle is not None:
                # Try to read voltage as a connectivity check
                voltage = None
                try:
                    voltage = mgr.query_voltage(sid)
                except Exception:
                    pass
                print(f"  ✓ Servo ID={sid}: angle={angle:.1f}°, voltage={voltage}V")
                found_any = True
        except Exception as e:
            print(f"  - Servo ID={sid}: no response ({e})")

    if not found_any:
        print("  ⚠ No servos responded! Check servo bus wiring and power.")
except Exception as e:
    print(f"  ❌ Scan failed: {e}")

# ── Step 4: Try to move servos ──────────────────────────────────────────────

print("\n── Step 4: Test movement (small wiggle) ──")
try:
    for sid in [0, 1]:
        try:
            # Read current angle
            cur = mgr.query_servo_angle(sid)
            if cur is not None:
                # Normalize
                cur = ((cur + 180.0) % 360.0) - 180.0
                target = cur + 5.0 if cur < 90 else cur - 5.0
                print(f"  Moving servo {sid}: {cur:.1f}° → {target:.1f}°")
                mgr.set_servo_angle(sid, target, velocity=100, power=3000)
                import time
                time.sleep(0.5)
                new_angle = mgr.query_servo_angle(sid)
                if new_angle is not None:
                    new_angle = ((new_angle + 180.0) % 360.0) - 180.0
                    diff = abs(new_angle - target)
                    if diff < 10:
                        print(f"  ✓ Servo {sid} moved successfully (now {new_angle:.1f}°)")
                    else:
                        print(f"  ⚠ Servo {sid} may not have moved: expected ~{target:.1f}°, got {new_angle:.1f}°")
            else:
                print(f"  - Servo {sid}: cannot read angle, skipping move test")
        except Exception as e:
            print(f"  ❌ Servo {sid} move failed: {e}")
except Exception as e:
    print(f"  ❌ Move test failed: {e}")

print("\n" + "=" * 60)
print("  Diagnostic complete.")
print("=" * 60)
