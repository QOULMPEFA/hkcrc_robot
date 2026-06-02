#!/usr/bin/env python3
"""Local port forward: localhost:LOCAL_PORT -> Jetson:REMOTE_PORT via SSH"""
import paramiko
import socket
import threading
import sys

SSH_HOST = "frp-fit.com"
SSH_PORT = 35200
SSH_USER = "yu"
SSH_PASS = "1114"
LOCAL_PORT = 5001
REMOTE_HOST = "127.0.0.1"
REMOTE_PORT = 5001  # will be set by arg


def handle_client(chan, client_sock):
    def forward(src, dst):
        try:
            while True:
                data = src.recv(4096)
                if not data:
                    break
                dst.sendall(data)
        except Exception:
            pass
        finally:
            try:
                src.close()
            except Exception:
                pass
            try:
                dst.close()
            except Exception:
                pass

    t1 = threading.Thread(target=forward, args=(client_sock, chan), daemon=True)
    t2 = threading.Thread(target=forward, args=(chan, client_sock), daemon=True)
    t1.start()
    t2.start()


def main():
    remote_port = int(sys.argv[1]) if len(sys.argv) > 1 else REMOTE_PORT
    local_port = int(sys.argv[2]) if len(sys.argv) > 2 else LOCAL_PORT

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(SSH_HOST, port=SSH_PORT, username=SSH_USER, password=SSH_PASS)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", local_port))
    server.listen(5)

    print(f"Tunnel ready: localhost:{local_port} -> Jetson:{remote_port}", flush=True)

    while True:
        client_sock, _ = server.accept()
        transport = client.get_transport()
        chan = transport.open_channel("direct-tcpip",
                                     (REMOTE_HOST, remote_port),
                                     ("127.0.0.1", local_port))
        threading.Thread(target=handle_client, args=(chan, client_sock), daemon=True).start()


if __name__ == "__main__":
    main()
