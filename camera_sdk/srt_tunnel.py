#!/usr/bin/env python3
"""UDP tunnel: localhost:6000 <-> Jetson:35210 via SSH (for SRT)"""
import paramiko
import socket
import threading
import time

SSH_HOST = "frp-fit.com"
SSH_PORT = 35200
SSH_USER = "yu"
SSH_PASS = "1114"
LOCAL_PORT = 6000
REMOTE_PORT = 35210


def main():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(SSH_HOST, port=SSH_PORT, username=SSH_USER, password=SSH_PASS)
    transport = client.get_transport()

    local_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    local_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    local_sock.bind(("127.0.0.1", LOCAL_PORT))
    local_sock.listen(5)
    print(f"SRT tunnel ready: srt://127.0.0.1:{LOCAL_PORT} -> Jetson:{REMOTE_PORT}", flush=True)

    while True:
        conn, _ = local_sock.accept()
        print("VLC connected, opening channel...", flush=True)
        try:
            chan = transport.open_channel(
                "direct-tcpip",
                ("127.0.0.1", REMOTE_PORT),
                ("127.0.0.1", LOCAL_PORT),
            )
        except Exception as e:
            print(f"Channel error: {e}", flush=True)
            conn.close()
            continue

        def pipe(src, dst):
            try:
                while True:
                    data = src.recv(65536)
                    if not data:
                        break
                    dst.sendall(data)
            except Exception:
                pass
            finally:
                try: src.close()
                except Exception: pass
                try: dst.close()
                except Exception: pass

        threading.Thread(target=pipe, args=(conn, chan), daemon=True).start()
        threading.Thread(target=pipe, args=(chan, conn), daemon=True).start()


if __name__ == "__main__":
    main()
