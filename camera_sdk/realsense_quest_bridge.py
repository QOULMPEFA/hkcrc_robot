#!/usr/bin/env python3
"""RealSense -> Quest 3 JPEG/UDP video streaming bridge.

Reads MJPEG frames from the local robot server's /stream endpoint
(which already manages the RealSense camera via pyrealsense2 SDK),
then forwards each JPEG frame to the Quest 3 over UDP.

TCP control channel on :13579 used only for Quest IP discovery.
UDP JPEG stream sent to Quest port 10004.

Which camera is forwarded is read from /tmp/realsense_bridge.source (a single
camera source id such as "cam1").  The file is re-checked while streaming, so
writing a new id switches the feed without dropping the Quest's TCP control
connection — the headset keeps its session and just sees a brief frame gap.

Protocol (per datagram to quest_ip:10004):
  [4B LE: frame_id]     — monotonic frame counter
  [4B LE: chunk_idx]    — 0-based
  [4B LE: total_chunks] — 1 for single-datagram frames
  [4B LE: jpeg_size]    — total JPEG byte count
  [payload]             — up to 60000 bytes
"""

import os
import select
import socket
import sqlite3
import struct
import sys
import threading
import time

try:
    from urllib.request import urlopen
except ImportError:
    from urllib2 import urlopen

try:
    from urllib.error import HTTPError
except ImportError:
    from urllib2 import HTTPError

CONTROL_PORT = 13579
UDP_PORT = 10004
# Must stay under the 1500-byte path MTU. Larger datagrams get IP-fragmented,
# and at video rates the fragment storm (~1200 frags/sec) is dropped wholesale
# by the WiFi path — 60000-byte datagrams never arrived at the headset at all.
MAX_CHUNK = 1400

# The MJPEG stream endpoint requires the current control-panel session token.
# Read it from the local auth DB so logins/token rotation do not break the feed.
STREAM_BASE = "http://127.0.0.1:6080/stream"
STREAM_DB_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "robot_control", "web_gui", "users.db")
)

# Holds the camera source id to forward. Written by start_realsense_quest.sh.
SOURCE_FILE = "/tmp/realsense_bridge.source"
DEFAULT_SOURCE = "cam0"
# How often to re-read SOURCE_FILE while streaming, in frames.
SOURCE_POLL_FRAMES = 30


def _read_source(default=DEFAULT_SOURCE):
    """Return the camera source id to stream, or `default` if unreadable."""
    try:
        with open(SOURCE_FILE) as fh:
            src = fh.read().strip()
        return src or default
    except OSError:
        return default


def _stream_token():
    """Return any valid control-panel session token for local stream access."""
    try:
        with sqlite3.connect(STREAM_DB_PATH) as conn:
            row = conn.execute(
                "SELECT session_token FROM users "
                "WHERE session_token IS NOT NULL AND session_token != '' "
                "LIMIT 1"
            ).fetchone()
        return row[0] if row else ""
    except Exception as e:
        print(f"[bridge] stream token lookup failed: {e}")
        return ""


def _iter_jpeg_frames(response):
    """Yield raw JPEG byte strings from a multipart MJPEG HTTP response.

    Parses `--frame\\r\\nContent-Type: image/jpeg\\r\\n\\r\\n<JPEG>\\r\\n`
    boundaries, yielding each JPEG body.
    """
    boundary = b"--frame\r\n"
    buf = b""
    while True:
        chunk = response.read(65536)
        if not chunk:
            break
        buf += chunk
        while True:
            # Find start of JPEG body: after the double CRLF following Content-Type
            hdr_end = buf.find(b"\r\n\r\n")
            if hdr_end == -1:
                break
            body_start = hdr_end + 4

            # Find the next boundary (start of next frame) after the body
            next_boundary = buf.find(boundary, body_start)
            if next_boundary == -1:
                break

            jpeg_bytes = buf[body_start:next_boundary]
            # Strip trailing CRLF before boundary
            jpeg_bytes = jpeg_bytes.rstrip(b"\r\n")
            if jpeg_bytes:
                yield jpeg_bytes
            buf = buf[next_boundary:]


def udp_stream(quest_ip, udp_port, stop_event):
    """Read MJPEG from robot server, forward JPEG frames to Quest over UDP.

    Reconnects to a different camera whenever SOURCE_FILE changes.  The UDP
    socket and destination survive across switches, so the Quest never sees a
    disconnect — only a short gap in frames.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    addr = (quest_ip, udp_port)
    frame_id = 0

    try:
        while not stop_event.is_set():
            source = _read_source()
            url = f"{STREAM_BASE}/{source}?token={_stream_token()}"
            print(f"[bridge] connecting to MJPEG stream: {STREAM_BASE}/{source}")

            try:
                response = urlopen(url, timeout=5)
            except HTTPError as e:
                if e.code == 404:
                    # Unknown source id — retrying cannot fix this, and spinning
                    # would flood the log while the headset waits forever.
                    print(f"[bridge] FATAL: camera source '{source}' does not "
                          f"exist (HTTP 404) — check {SOURCE_FILE}")
                    return
                print(f"[bridge] MJPEG HTTP error {e.code}: {e} — retrying")
                time.sleep(2)
                continue
            except Exception as e:
                print(f"[bridge] MJPEG connect failed: {e} — retrying")
                time.sleep(2)
                continue

            print(f"[bridge] streaming '{source}' → {quest_ip}:{udp_port}")
            n = 0
            try:
                for jpeg_bytes in _iter_jpeg_frames(response):
                    if stop_event.is_set():
                        return
                    _send_frame(sock, addr, frame_id, jpeg_bytes)
                    frame_id = (frame_id + 1) & 0xFFFFFFFF
                    if frame_id % 150 == 1:
                        print(f"[bridge] frame #{frame_id} — {len(jpeg_bytes)} bytes")

                    n += 1
                    if n % SOURCE_POLL_FRAMES == 0 and _read_source() != source:
                        print(f"[bridge] source changed: {source} → "
                              f"{_read_source()} — switching")
                        break
            except Exception as e:
                print(f"[bridge] MJPEG stream error: {e} — reconnecting")
            finally:
                try:
                    response.close()
                except Exception:
                    pass
    finally:
        sock.close()
        print("[bridge] UDP stream ended")


def _send_frame(sock, addr, frame_id, jpeg_bytes):
    """Fragment and send one JPEG frame over UDP."""
    total = len(jpeg_bytes)
    chunks = (total + MAX_CHUNK - 1) // MAX_CHUNK
    offset = 0
    for idx in range(chunks):
        chunk_data = jpeg_bytes[offset:offset + MAX_CHUNK]
        offset += MAX_CHUNK
        pkt = struct.pack("<IIII", frame_id, idx, chunks, total) + chunk_data
        sock.sendto(pkt, addr)


def handle_client(conn, addr, udp_port):
    quest_ip = addr[0]
    print(f"[bridge] Quest connected from {quest_ip} -> streaming UDP to {quest_ip}:{udp_port}")
    stop = threading.Event()
    udp_thread = None
    try:
        udp_thread = threading.Thread(
            target=udp_stream,
            args=(quest_ip, udp_port, stop),
            daemon=True,
        )
        udp_thread.start()

        # Keep TCP alive — read (and discard) any Quest data
        while True:
            ready, _, _ = select.select([conn], [], [], 1.0)
            if ready:
                data = conn.recv(1024)
                if not data:
                    break
    except Exception as e:
        print(f"[bridge] error: {e}")
    finally:
        print("[bridge] Quest disconnected — stopping UDP stream")
        stop.set()
        if udp_thread:
            udp_thread.join(timeout=5)
        try:
            conn.close()
        except Exception:
            pass


def main():
    control_port = int(sys.argv[1]) if len(sys.argv) > 1 else CONTROL_PORT
    udp_port = int(sys.argv[2]) if len(sys.argv) > 2 else UDP_PORT

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", control_port))
    srv.listen(5)
    print(f"[bridge] listening on 0.0.0.0:{control_port}, UDP -> :{udp_port}")
    print(f"[bridge] MJPEG source: {STREAM_BASE}/{_read_source()}")

    while True:
        conn, addr = srv.accept()
        threading.Thread(
            target=handle_client,
            args=(conn, addr, udp_port),
            daemon=True,
        ).start()


if __name__ == "__main__":
    main()
