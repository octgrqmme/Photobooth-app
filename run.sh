#!/usr/bin/env bash
# Official photobooth-app startup script

cd "$(dirname "$0")"

echo "=========================================================="
echo "📸 Starting official photobooth-app (photobooth-app.org)"
echo "=========================================================="

./venv/bin/photobooth
