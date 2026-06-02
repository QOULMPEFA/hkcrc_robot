#!/usr/bin/env python3
import subprocess
import threading
from flask import Flask, Response

PORT = 6000
DEVICE = "/dev/video0"

app = Flask(__name__)

def gen_frames():
    cmd = [
        "ffmpeg",
        "-fflags", "nobuffer",
        "-f", "v4l2", "-input_format", "mjpeg",
        "-framerate", "30", "-video_size", "1280x720",
        "-i", DEVICE,
        "-f", "image2pipe",
        "-vf", "fps=15",
        "-vcodec", "mjpeg", "-q:v", "5",
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
            frame = buf[start:end + 2]
            buf = buf[end + 2:]
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")

@app.route("/")
def index():
    return """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Jetson Camera</title>
<style>body{margin:0;background:#111;display:flex;flex-direction:column;justify-content:center;align-items:center;height:100vh;color:#fff;font-family:sans-serif}img{max-width:100%;max-height:90vh}p{margin-top:8px;opacity:.5;font-size:12px}</style>
</head>
<body><img src="/stream"><p>Jetson Camera - live</p></body>
</html>"""

@app.route("/stream")
def stream():
    return Response(gen_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")

if __name__ == "__main__":
    print(f"Camera server started on port {PORT}", flush=True)
    app.run(host="0.0.0.0", port=PORT, threaded=True)
