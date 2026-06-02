#!/bin/bash
pkill -f ffmpeg 2>/dev/null
pkill -f gst-launch 2>/dev/null
sleep 1
nohup ffmpeg \
  -f v4l2 -input_format mjpeg -framerate 30 -video_size 640x480 -i /dev/video0 \
  -c:v libx264 -preset ultrafast -tune zerolatency -b:v 1000k \
  -f mpegts "srt://0.0.0.0:35210?mode=listener&latency=100" \
  > /tmp/srt_stream.log 2>&1 &
echo "PID: $!"
