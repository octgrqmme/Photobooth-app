#!/usr/bin/env bash
# Photobooth-App Launcher with macOS USB Camera Lock Release

app_root="$(cd "$(dirname "$0")" && pwd)"
cd "$app_root" || exit 1

# Keep custom source outside the virtual environment and deploy it on every run.
bash "$app_root/scripts/install_customizations.sh" || exit 1

echo "=========================================================="
echo "📸 Starting Photobooth-App (photobooth-app.org)"
echo "🔌 Camera: Canon via gphoto2 (live preview & shutter trigger)"
echo "🖨️ Printer: selectable macOS/CUPS profile"
echo "🌐 URL: http://localhost:8000"
echo "=========================================================="

# Disable macOS ptpcamerad so it does not interfere with gphoto2
launchctl disable gui/$(id -u)/com.apple.ptpcamerad 2>/dev/null || true
killall -9 ptpcamerad 2>/dev/null || true

cleanup() {
    echo "Stopping photobooth..."
    launchctl enable gui/$(id -u)/com.apple.ptpcamerad 2>/dev/null || true
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Run photobooth directly
./venv/bin/python -m photobooth
