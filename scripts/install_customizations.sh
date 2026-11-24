#!/usr/bin/env bash
set -euo pipefail

app_root="$(cd "$(dirname "$0")/.." && pwd)"
site_packages="$("$app_root/venv/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
frontend_target="$site_packages/web/frontend"
backend_target="$site_packages/photobooth/routers/api"
camera_target="$site_packages/photobooth/services/backends"
services_target="$site_packages/photobooth/services"

if [[ ! -d "$frontend_target" || ! -d "$backend_target" ]]; then
  echo "Photobooth virtual environment is incomplete. Run the project setup first." >&2
  exit 1
fi

install -m 0644 "$app_root/photobooth_custom/frontend/index.html" "$frontend_target/index.html"
install -m 0644 "$app_root/photobooth_custom/frontend/control-center.html" "$frontend_target/control-center.html"
install -m 0644 "$app_root/photobooth_custom/frontend/kiosk.html" "$frontend_target/kiosk.html"
install -m 0644 "$app_root/photobooth_custom/frontend/gallery.html" "$frontend_target/gallery.html"
install -m 0644 "$app_root/photobooth_custom/frontend/queue-board.html" "$frontend_target/queue-board.html"
install -m 0644 "$app_root/photobooth_custom/frontend/template-chooser.html" "$frontend_target/template-chooser.html"
install -m 0644 "$app_root/photobooth_custom/frontend/print-desk.html" "$frontend_target/print-desk.html"
install -m 0644 "$app_root/photobooth_custom/frontend/station-pair.html" "$frontend_target/station-pair.html"
install -m 0644 "$app_root/photobooth_custom/frontend/ticket-desk.html" "$frontend_target/ticket-desk.html"
install -m 0644 "$app_root/photobooth_custom/frontend/assets/photobooth-stations.js" "$frontend_target/assets/photobooth-stations.js"
install -m 0644 "$app_root/photobooth_custom/frontend/queue-admin.html" "$frontend_target/queue-admin.html"
install -m 0644 "$app_root/photobooth_custom/frontend/assets/photobooth-theme.css" "$frontend_target/assets/photobooth-theme.css"
install -m 0644 "$app_root/photobooth_custom/frontend/assets/photobooth-loykrathong-scene.js" "$frontend_target/assets/photobooth-loykrathong-scene.js"
install -m 0644 "$app_root/photobooth_custom/frontend/assets/photobooth-enhancements.css" "$frontend_target/assets/photobooth-enhancements.css"
install -m 0644 "$app_root/photobooth_custom/frontend/assets/photobooth-enhancements.js" "$frontend_target/assets/photobooth-enhancements.js"
install -m 0644 "$app_root/photobooth_custom/frontend/assets/photobooth-dual-screen.css" "$frontend_target/assets/photobooth-dual-screen.css"
install -m 0644 "$app_root/photobooth_custom/frontend/assets/photobooth-dual-screen.js" "$frontend_target/assets/photobooth-dual-screen.js"
install -m 0644 "$app_root/photobooth_custom/frontend/assets/photobooth-guest-queue.js" "$frontend_target/assets/photobooth-guest-queue.js"
install -m 0644 "$app_root/photobooth_custom/frontend/assets/pb-stage-curtain.png" "$frontend_target/assets/pb-stage-curtain.png"
install -m 0644 "$app_root/photobooth_custom/frontend/assets/pb-loy-logo.webp" "$frontend_target/assets/pb-loy-logo.webp"
install -m 0644 "$app_root/photobooth_custom/frontend/assets/pb-loy-krathong.webp" "$frontend_target/assets/pb-loy-krathong.webp"
install -m 0644 "$app_root/photobooth_custom/frontend/assets/pb-template-theater.png" "$frontend_target/assets/pb-template-theater.png"
install -m 0644 "$app_root/photobooth_custom/frontend/assets/pb-template-cinema.png" "$frontend_target/assets/pb-template-cinema.png"
install -m 0644 "$app_root/photobooth_custom/backend/share.py" "$backend_target/share.py"
install -m 0644 "$app_root/photobooth_custom/backend/platform_store.py" "$services_target/platform_store.py"
install -m 0644 "$app_root/photobooth_custom/backend/guest_queue.py" "$services_target/guest_queue.py"
install -m 0644 "$app_root/photobooth_custom/backend/gallery_folders.py" "$services_target/gallery_folders.py"
install -m 0644 "$app_root/photobooth_custom/backend/gphoto2.py" "$camera_target/gphoto2.py"
