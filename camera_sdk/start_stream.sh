#!/bin/bash
pkill -f ffmpeg 2>/dev/null
sleep 1
nohup ffmpeg \
  -f v4l2 -input_format mjpeg -framerate 30 -video_size 1280x720 -i /dev/video0 \
  -c:v libx264 -preset ultrafast -tune zerolatency -b:v 1000k \
  -f mpegts "tcp://0.0.0.0:35230?listen" \
  > /tmp/stream.log 2>&1 &
echo "PID: $!"
