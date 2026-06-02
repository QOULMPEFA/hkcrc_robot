#!/usr/bin/env python3
"""MJPEG HTTP server - pulls MPEG-TS from Jetson and serves browser-viewable stream"""
import subprocess
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

FFMPEG = "C:/ProgramData/chocolatey/bin/ffmpeg.exe"
SOURCE = "tcp://frp-fit.com:35230"
PORT = 6000


def gen_frames():
    cmd = [
        FFMPEG,
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-analyzeduration", "0",
        "-probesize", "32",
        "-i", SOURCE,
        "-f", "image2pipe",
        "-vf", "fps=15",
        "-vcodec", "mjpeg",
        "-q:v", "5",
        "pipe:1",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    buf = b""
    while True:
        chunk = proc.stdout.read(4096)
        if not chunk:
            break
        buf += chunk
        start = buf.find(b"\xff\xd8")
        end = buf.find(b"\xff\xd9")
        if start != -1 and end != -1 and end > start:
            yield buf[start:end + 2]
            buf = buf[end + 2:]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Jetson Camera</title>
<style>
  body { margin: 0; background: #111; display: flex; justify-content: center; align-items: center; height: 100vh; }
  img { max-width: 100%; max-height: 100vh; border: 2px solid #444; }
</style>
</head>
<body>
<img src="/stream" />
</body>
</html>""")

        elif self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            try:
                for frame in gen_frames():
                    self.wfile.write(
                        b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                    )
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self.send_response(404)
            self.end_headers()


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Camera viewer: http://localhost:{PORT}", flush=True)
    server.serve_forever()
