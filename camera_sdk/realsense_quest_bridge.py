#!/usr/bin/env python3
"""RealSense -> Quest 3 H.264 video streaming bridge.

Listens on 0.0.0.0:13579 for the Quest client's OPEN_CAMERA request
(control protocol from teleoperation_sdk/client_quest3), then streams the
RealSense color feed (H.264 Annex-B) to the Quest's MediaDecoder listener
port (taken from the request, default 12345).

Usage:
  python3 realsense_quest_bridge.py [camera_device] [control_port]

  camera_device: /dev/videoN (default /dev/video4 = RealSense color, YUYV)
"""
import glob
import os
import socket
import struct
import subprocess
import sys
import threading
import time

CONTROL_PORT = 13579
CAMERA_DEVICE = "/dev/video4"          # RealSense color stream (YUYV)


# ── camera detection (mirrors robot_control/camera.py) ──────────────────────

def _is_realsense(path: str) -> bool:
    """True if the v4l2 card name mentions RealSense."""
    try:
        out = subprocess.run(["v4l2-ctl", "-d", path, "--info"],
                             capture_output=True, text=True, timeout=3).stdout
        return "RealSense" in out or "realsense" in out
    except (FileNotFoundError, subprocess.SubprocessError):
        return False


def _has_color_format(path: str) -> bool:
    """True if the node exposes YUYV/MJPG/RGB (a colour sensor)."""
    try:
        out = subprocess.run(["v4l2-ctl", "-d", path, "--list-formats"],
                             capture_output=True, text=True, timeout=3).stdout
        return any(fmt in out for fmt in ("YUYV", "MJPG", "RGB"))
    except (FileNotFoundError, subprocess.SubprocessError):
        return False


def detect_realsense_color_node() -> str:
    """Return a /dev/videoN that is a RealSense colour stream.

    Picks the first RealSense node exposing a colour pixel format.
    """
    for path in sorted(glob.glob("/dev/video*")):
        if not os.path.exists(f"/sys/class/video4linux/{os.path.basename(path)}/device"):
            continue  # ghost node
        if _is_realsense(path) and _has_color_format(path):
            return path
    return CAMERA_DEVICE  # fall back to configured default


def parse_camera_request(data):
    """Parse CameraRequestData:
    magic(2)=0xCAFE + version(1)=1 + 7x int32 + camera str + ip str."""
    if len(data) < 10 or data[:2] != b'\xca\xfe':
        return None
    width, height, fps, bitrate, mvhevc, renderMode, port = struct.unpack_from('<7i', data, 3)
    off = 3 + 28
    cam_len = data[off]; off += 1
    camera = data[off:off + cam_len].decode('utf-8', 'replace'); off += cam_len
    ip_len = data[off]; off += 1
    ip = data[off:off + ip_len].decode('utf-8', 'replace')
    return dict(width=width, height=height, fps=fps, bitrate=bitrate,
                enableMvHevc=mvhevc, renderMode=renderMode, camera=camera,
                ip=ip, port=port)


def recv_exact(sock, n):
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def read_frame(sock):
    """Read a protocol frame: [cmd_len(4)][cmd][data_len(4)][data]."""
    hdr = recv_exact(sock, 4)
    if hdr is None:
        return None
    cmd_len = struct.unpack('<i', hdr)[0]
    if cmd_len < 0 or cmd_len > 1024:
        return None
    cmd = recv_exact(sock, cmd_len)
    if cmd is None:
        return None
    dl = recv_exact(sock, 4)
    if dl is None:
        return None
    data_len = struct.unpack('<i', dl)[0]
    if data_len < 0 or data_len > 4096:
        return None
    data = recv_exact(sock, data_len)
    if data is None:
        return None
    return cmd.decode('utf-8', 'replace'), data


def stream_to_quest(ip, port, width, height, fps, bitrate):
    print(f"[bridge] connecting to Quest {ip}:{port} for video push")

    # Kill any competing process holding the camera (e.g. robot_control camera
    # module).  The camera module auto-restarts its ffmpeg after a short delay.
    os.system(f"fuser -k {CAMERA_DEVICE} 2>/dev/null")
    time.sleep(0.3)

    sock = socket.create_connection((ip, port), timeout=10)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    # The Quest shader renders side-by-side stereo: each eye samples one half
    # of the texture. Duplicate the color frame into both halves so every eye
    # sees the full image (2D view). Output width is 2x the per-eye width.
    w = width or 2560
    h = height or 720
    if w > 2560:
        w = 2560
    half_w = w // 2
    f = fps if (fps and 0 < fps <= 60) else 30
    b = bitrate or 4000000

    # Capture at camera's native 1280x720, then scale/hstack to requested size.
    cap_w, cap_h = 1280, 720

    vfilter = (f"[0:v]split=2[a][b];"
               f"[a]scale={half_w}:{h},setsar=1[la];"
               f"[b]scale={half_w}:{h},setsar=1[ra];"
               f"[la][ra]hstack=inputs=2[out]")
    cmd = [
        'ffmpeg', '-hide_banner', '-loglevel', 'warning',
        '-fflags', 'nobuffer', '-flags', 'low_delay',
        '-f', 'v4l2', '-input_format', 'yuyv422',
        '-framerate', str(f), '-video_size', f'{cap_w}x{cap_h}',
        '-i', CAMERA_DEVICE,
        '-filter_complex', vfilter,
        '-map', '[out]',
        '-an',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-tune', 'zerolatency',
        '-pix_fmt', 'yuv420p', '-b:v', str(b),
        '-g', str(f), '-keyint_min', str(f), '-sc_threshold', '0',
        '-f', 'h264', 'pipe:1',
    ]
    print(f"[bridge] ffmpeg color {half_w}x{h} -> side-by-side {w}x{h} @{f}fps {b}bps -> {ip}:{port}")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            sock.sendall(chunk)
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass


def send_frame(sock, cmd, data=b''):
    """Send a protocol frame: [cmd_len(4)][cmd][data_len(4)][data]."""
    cmd_bytes = cmd.encode('utf-8') if isinstance(cmd, str) else cmd
    sock.sendall(struct.pack('<i', len(cmd_bytes)))
    sock.sendall(cmd_bytes)
    sock.sendall(struct.pack('<i', len(data)))
    sock.sendall(data)


def handle_client(conn, addr):
    quest_ip = addr[0]
    print(f"[bridge] client connected: {addr} → streaming to {quest_ip}:12345")
    try:
        send_frame(conn, 'HELLO')
        # Stream immediately without waiting for OPEN_CAMERA.
        # The Quest MediaDecoder is already listening on port 12345 (the C#
        # coroutine starts it before connecting TcpClient to us).
        stream_to_quest(quest_ip, 12345, 1280, 720, 30, 4000000)
    except Exception as e:
        print(f"[bridge] error: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main():
    global CAMERA_DEVICE, CONTROL_PORT
    if len(sys.argv) > 1:
        CAMERA_DEVICE = sys.argv[1]
    if len(sys.argv) > 2:
        CONTROL_PORT = int(sys.argv[2])
    if sys.argv[1] == "auto" or len(sys.argv) == 1:
        CAMERA_DEVICE = detect_realsense_color_node()
        print(f"[bridge] auto-detected RealSense colour node: {CAMERA_DEVICE}")

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('0.0.0.0', CONTROL_PORT))
    srv.listen(5)
    print(f"[bridge] RealSense ({CAMERA_DEVICE}) video bridge "
          f"listening on 0.0.0.0:{CONTROL_PORT}")
    while True:
        conn, addr = srv.accept()
        threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()


if __name__ == '__main__':
    main()
