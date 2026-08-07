"""UART1 (CH340) test tool — send text commands to STM32 PR_PRO firmware.

Usage:
    python uart1_test.py                  # interactive mode
    python uart1_test.py "MA 75"          # single command
    python uart1_test.py --test           # run forward/backward test
"""

import sys
import time
import serial

PORT = "COM6"
BAUDRATE = 115200
TIMEOUT = 0.5


def open_uart():
    ser = serial.Serial(
        port=PORT,
        baudrate=BAUDRATE,
        parity=serial.PARITY_NONE,
        stopbits=1,
        bytesize=8,
        timeout=TIMEOUT,
    )
    return ser


def send_cmd(ser, cmd: str):
    ser.write((cmd + "\n").encode("utf-8"))
    ser.flush()
    time.sleep(0.05)
    # read response
    resp = b""
    while True:
        chunk = ser.read(256)
        if not chunk:
            break
        resp += chunk
    if resp:
        print(resp.decode("utf-8", errors="replace"), end="")


def interactive(ser):
    print(f"Connected to {PORT} @ {BAUDRATE}")
    print("Type commands (HELP, STOP, MA 75, S1 90, etc.) or 'quit' to exit.\n")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line.lower() == "quit":
            break
        send_cmd(ser, line)


def run_test(ser):
    print(f"Connected to {PORT} @ {BAUDRATE}")
    print("=== Motor test ===\n")

    # Send HELP
    print("[Sending: HELP]")
    send_cmd(ser, "HELP")
    time.sleep(0.3)

    # Forward
    print("\n[Sending: all motors forward 75]")
    for mid in "ABCDEF":
        send_cmd(ser, f"M{mid} 75")
        time.sleep(0.02)
    time.sleep(2)

    # Stop
    print("\n[Sending: STOP]")
    send_cmd(ser, "STOP")
    time.sleep(0.5)

    # Backward
    print("\n[Sending: all motors backward -75]")
    for mid in "ABCDEF":
        send_cmd(ser, f"M{mid} -75")
        time.sleep(0.02)
    time.sleep(2)

    # Stop
    print("\n[Sending: STOP]")
    send_cmd(ser, "STOP")
    print("\n=== Test complete ===")


def main():
    ser = open_uart()
    try:
        if len(sys.argv) > 1:
            if sys.argv[1] == "--test":
                run_test(ser)
            else:
                send_cmd(ser, " ".join(sys.argv[1:]))
        else:
            interactive(ser)
    finally:
        ser.close()


if __name__ == "__main__":
    main()
