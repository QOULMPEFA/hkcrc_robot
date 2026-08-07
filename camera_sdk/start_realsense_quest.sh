#!/bin/bash
# Start/stop the RealSense → Quest 3 video bridge.
# Usage: ./start_realsense_quest.sh [start|stop|restart|status|source] [source_id]
#
# The bridge listens on 0.0.0.0:13579 for the Quest client's connection, then
# forwards the RealSense colour feed as mono JPEG frames over UDP to port 10004.
#
# Which camera it forwards is read from $SOURCE_FILE, not from argv — the bridge
# re-reads that file while streaming, so `source cam1` switches the feed without
# restarting the process and without dropping the headset's TCP session.
#
# The two positional args to the bridge are control port and UDP port, in that
# order — it parses both with int(), so a non-numeric value kills it on startup.

SCRIPT_DIR=$(dirname "$0")
SOURCE_FILE=/tmp/realsense_bridge.source
DEFAULT_SOURCE=cam0

read_source() {
  local s
  s=$(cat "$SOURCE_FILE" 2>/dev/null | tr -d '[:space:]')
  echo "${s:-$DEFAULT_SOURCE}"
}

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
  source)
    if [ -z "$2" ]; then
      echo "Usage: $0 source <camera_id>" >&2
      exit 1
    fi
    printf '%s' "$2" > "$SOURCE_FILE"
    echo "Source: $2"
    ;;
  status)
    echo "Source: $(read_source)"
    if pgrep -af realsense_quest_bridge.py > /dev/null; then
      pgrep -af realsense_quest_bridge.py
    else
      echo "RealSense bridge not running."
    fi
    ;;
  start|*)
    if [ -n "$2" ]; then
      printf '%s' "$2" > "$SOURCE_FILE"
    else
      printf '%s' "$(read_source)" > "$SOURCE_FILE"
    fi
    pkill -f realsense_quest_bridge.py 2>/dev/null
    sleep 1
    setsid nohup python3 -u "$SCRIPT_DIR/realsense_quest_bridge.py" \
      13579 10004 > /tmp/realsense_bridge.log 2>&1 < /dev/null &
    echo "PID: $!"
    echo "Source: $(read_source)"
    echo "RealSense bridge started (log: /tmp/realsense_bridge.log)"
    ;;
esac
