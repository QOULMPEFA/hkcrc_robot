"""
Diagnose CAN link and SDO communication before running motor examples.
Prints all received frames for 3 seconds, then attempts a single SDO read.
"""

import time
import logging
import can

from stepper_motor_sdk import CANopenNode, OD

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")

NODE_ID = 1
CHANNEL = "can0"
BITRATE = 500_000
LISTEN_SECONDS = 3

print(f"\n=== Step 1: Raw CAN frames on {CHANNEL} for {LISTEN_SECONDS}s ===")
try:
    bus = can.Bus(interface="socketcan", channel=CHANNEL, bitrate=BITRATE)
except Exception as e:
    print(f"[FAIL] Cannot open {CHANNEL}: {e}")
    print("  → Run: sudo bash scripts/setup_jetson_can.sh")
    raise SystemExit(1)

deadline = time.monotonic() + LISTEN_SECONDS
count = 0
while time.monotonic() < deadline:
    msg = bus.recv(timeout=0.1)
    if msg:
        count += 1
        print(f"  RX  id=0x{msg.arbitration_id:03X}  data={msg.data.hex()}  len={msg.dlc}")
bus.shutdown()

if count == 0:
    print("[WARN] No frames received — check power, wiring, and bitrate")
else:
    print(f"[OK] Received {count} frames")

print(f"\n=== Step 2: SDO read statusword (node_id={NODE_ID}) ===")
try:
    with CANopenNode(interface="socketcan", channel=CHANNEL, bitrate=BITRATE, node_id=NODE_ID, sdo_timeout=2.0) as node:
        time.sleep(0.1)   # give listener thread a moment
        sw = node.sdo.read_u16(OD.STATUSWORD)
        print(f"[OK] Statusword = 0x{sw:04X}")
except Exception as e:
    print(f"[FAIL] {e}")
    print("  → Possible causes:")
    print("    1. Motor not powered on")
    print("    2. Wrong node_id (default=1, check H0C_00)")
    print("    3. Wrong bitrate (default=500kbps, check H0C_08)")
    print("    4. CAN wiring issue (CAN-H / CAN-L swapped)")
