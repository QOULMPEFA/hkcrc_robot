#!/bin/bash
# Start/stop the RealSense → Quest 3 video bridge.
# Usage: ./start_realsense_quest.sh [start|stop|restart|status]
#
# The bridge listens on 0.0.0.0:13579 for the Quest client's OPEN_CAMERA
# request, then streams the RealSense colour feed (H.264 side-by-side) to
# the Quest's MediaDecoder port.

SCRIPT_DIR=$(dirname "$0")

case "$1" in
  stop)
    pkill -f realsense_quest_bridge.py 2>/dev/null
    echo "RealSense bridge stopped."
    ;;
  restart)
    "$0" stop
    sleep 1
    exec "$0" start
    ;;
  status)
    if pgrep -af realsense_quest_bridge.py > /dev/null; then
      pgrep -af realsense_quest_bridge.py
    else
      echo "RealSense bridge not running."
    fi
    ;;
  start|*)
    pkill -f realsense_quest_bridge.py 2>/dev/null
    sleep 1
    setsid nohup python3 -u "$SCRIPT_DIR/realsense_quest_bridge.py" \
      auto 13579 > /tmp/realsense_bridge.log 2>&1 < /dev/null &
    echo "PID: $!"
    echo "RealSense bridge started (log: /tmp/realsense_bridge.log)"
    ;;
esac
