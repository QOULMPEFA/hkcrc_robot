"""Listen on COM6 (USART1) for [UART3] messages, log to file."""

import sys
import time
import serial

PORT = "COM6"
BAUDRATE = 115200

ser = serial.Serial(PORT, BAUDRATE, timeout=0.5)
print(f"Listening on {PORT}...", flush=True)
start = time.time()
while time.time() - start < 10:
    data = ser.read(4096)
    if data:
        text = data.decode("utf-8", errors="replace")
        sys.stdout.write(text)
        sys.stdout.flush()
ser.close()
