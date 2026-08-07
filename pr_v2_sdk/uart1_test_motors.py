"""Test each motor individually via UART1 (CH340)."""

import time
import serial

PORT = "COM6"
BAUDRATE = 115200
MOTORS = "ABCDEFGH"

ser = serial.Serial(PORT, BAUDRATE, timeout=0.3)
print(f"Testing motors A-H on {PORT}...\n")

for mid in MOTORS:
    cmd = f"M{mid} 100"
    ser.write((cmd + "\n").encode("utf-8"))
    ser.flush()
    time.sleep(0.1)
    resp = b""
    while True:
        chunk = ser.read(256)
        if not chunk:
            break
        resp += chunk
    text = resp.decode("utf-8", errors="replace").strip()
    status = "???"
    if "OK" in text:
        status = "OK - physically check if spinning"
    print(f"  Motor {mid}: {cmd} -> {status}")
    time.sleep(1)

ser.write(b"STOP\n")
ser.flush()
time.sleep(0.1)
resp = ser.read(256)
print(f"\nSTOP: {resp.decode('utf-8', errors='replace').strip()}")
ser.close()
