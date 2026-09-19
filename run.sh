#!/usr/bin/env bash
# Photobooth-App and USB Camera Shutter Bridge Launcher

cd "$(dirname "$0")"

echo "=========================================================="
echo "📸 Starting Photobooth-App (photobooth-app.org)"
echo "🔌 Starting USB Camera Shutter Bridge (Physical Shutter & Tethering)"
echo "=========================================================="

cleanup() {
  echo ""
  echo "Stopping services..."
  kill "$PID_APP" "$PID_BRIDGE" 2>/dev/null
  exit 0
}

trap cleanup SIGINT SIGTERM EXIT

# 1. Start Photobooth-App server on port 8000
./venv/bin/photobooth &
PID_APP=$!

# 2. Start USB Shutter & Tethering Listener in background
./venv/bin/python usb_shutter_bridge.py &
PID_BRIDGE=$!

# Wait for process
wait "$PID_APP"
