#!/usr/bin/env python3
"""
📸 USB Camera Shutter Bridge for Photobooth-App
Listens for physical shutter button presses on USB-connected DSLR/Mirrorless cameras
(Canon EOS, Nikon, Sony Alpha, Fuji, etc.) and Hot Folder imports,
then instantly transfers the photo and triggers the Photobooth website workflow.
"""

import os
import sys
import time
import shutil
import logging
import requests
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (ShutterBridge) %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("shutter_bridge")

PHOTOBOOTH_API = os.getenv("PHOTOBOOTH_API", "http://localhost:8000")
HOTFOLDER_DIR = Path("userdata/hotfolder").resolve()
ARCHIVE_DIR = Path("userdata/hotfolder_archive").resolve()

HOTFOLDER_DIR.mkdir(parents=True, exist_ok=True)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

try:
    import gphoto2 as gp
    HAS_GPHOTO2 = True
except ImportError:
    HAS_GPHOTO2 = False
    logger.warning("gphoto2 library not installed. Native USB shutter event listener unavailable.")


def trigger_photobooth_action():
    """Trigger the photobooth website to advance and capture/present photo"""
    try:
        url = f"{PHOTOBOOTH_API}/api/actions/image/0"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            logger.info("Successfully triggered Photobooth action via API!")
            return True
        else:
            logger.warning(f"Trigger API returned status {res.status_code}: {res.text}")
    except Exception as e:
        logger.error(f"Error calling Photobooth trigger API: {e}")
    return False


def setup_camera_for_physical_shutter(camera, context):
    """
    Configure camera so the physical shutter button on the camera body is UNLOCKED
    and saves captured files to the SD card / emits file added events over USB.
    """
    try:
        config = camera.get_config(context)
        # 1. capturetarget: Set to Memory card (Card / SD card)
        try:
            target_widget = config.get_child_by_name("capturetarget")
            choices = [target_widget.get_choice(i) for i in range(target_widget.count_choices())]
            logger.info(f"Available capture targets: {choices}")
            # Look for card or memory card
            for choice in choices:
                if "card" in choice.lower() or "sd" in choice.lower():
                    target_widget.set_value(choice)
                    logger.info(f"Set camera capturetarget to '{choice}' (Enables physical shutter button!)")
                    break
        except Exception as e:
            logger.debug(f"capturetarget widget check: {e}")

        camera.set_config(config, context)
        logger.info("Camera physical shutter configuration applied successfully.")
    except Exception as e:
        logger.warning(f"Could not configure camera parameters: {e}")


def run_gphoto2_shutter_listener():
    """
    Connects to USB camera and listens for physical shutter button presses.
    """
    if not HAS_GPHOTO2:
        return

    logger.info("Starting USB Camera Physical Shutter Listener...")
    context = gp.Context()

    while True:
        camera = None
        try:
            # Check for connected cameras
            cam_list = gp.Camera.autodetect(context)
            count = cam_list.count()
            if count == 0:
                time.sleep(2)
                continue

            cam_name, cam_port = cam_list.get_name(0), cam_list.get_value(0)
            logger.info(f"Connected USB Camera detected: '{cam_name}' at {cam_port}")

            camera = gp.Camera()
            camera.init(context)
            setup_camera_for_physical_shutter(camera, context)

            logger.info("✅ READY: Press the physical shutter button on your camera!")
            logger.info("Listening for camera shutter events...")

            while True:
                # Wait for event from camera (500ms timeout)
                try:
                    event_type, event_data = camera.wait_for_event(500, context)
                except gp.GPhoto2Error as gpe:
                    if "[-1]" in str(gpe) or "I/O" in str(gpe):
                        logger.warning("Camera disconnected or I/O timeout.")
                        break
                    continue

                # Check if user pressed shutter and a file was added on the camera
                if event_type == gp.GP_EVENT_FILE_ADDED:
                    folder = event_data.folder
                    name = event_data.name
                    logger.info(f"📸 SHUTTER PRESSED ON CAMERA! New photo: {folder}/{name}")

                    # Download the photo from camera
                    ext = os.path.splitext(name)[1].lower()
                    if ext in (".jpg", ".jpeg", ".png"):
                        dest_path = HOTFOLDER_DIR / name
                        logger.info(f"Downloading {name} to {dest_path}...")
                        cam_file = camera.file_get(folder, name, gp.GP_FILE_TYPE_NORMAL, context)
                        cam_file.save(str(dest_path))
                        logger.info("Download complete! Triggering Photobooth workflow...")

                        # Trigger the photobooth website
                        trigger_photobooth_action()

                elif event_type == gp.GP_EVENT_CAPTURE_COMPLETE:
                    logger.info("Camera capture completed event received.")

        except Exception as e:
            logger.warning(f"Camera connection loop note: {e}")
            time.sleep(2)
        finally:
            if camera:
                try:
                    camera.exit(context)
                except Exception:
                    pass


def run_hotfolder_watcher():
    """
    Watches userdata/hotfolder/ for images saved by external tethering apps
    (Canon EOS Utility, Sony Imaging Edge, Nikon Camera Control, Lightroom).
    """
    logger.info(f"Hot Folder Watcher active on: {HOTFOLDER_DIR}")
    seen_files = set()

    # Pre-populate existing files
    for f in HOTFOLDER_DIR.glob("*.*"):
        if f.is_file():
            seen_files.add(f.name)

    while True:
        try:
            for f in HOTFOLDER_DIR.glob("*.*"):
                if f.is_file() and f.name not in seen_files:
                    ext = f.suffix.lower()
                    if ext in (".jpg", ".jpeg", ".png"):
                        # Ensure file is completely written by checking size stability
                        size_1 = f.stat().st_size
                        time.sleep(0.3)
                        size_2 = f.stat().st_size
                        if size_1 == size_2 and size_2 > 0:
                            seen_files.add(f.name)
                            logger.info(f"📥 New tethered photo detected in hotfolder: {f.name}")

                            # Archive copy
                            shutil.copy2(f, ARCHIVE_DIR / f.name)

                            # Trigger photobooth website to ingest photo and proceed
                            trigger_photobooth_action()
        except Exception as e:
            logger.error(f"Hotfolder scan error: {e}")

        time.sleep(0.5)


if __name__ == "__main__":
    from threading import Thread

    logger.info("=================================================================")
    logger.info("📸 Photobooth USB Shutter & Tethering Bridge Initialized")
    logger.info("=================================================================")

    # Start Hot Folder watcher in background thread
    t_watcher = Thread(target=run_hotfolder_watcher, daemon=True)
    t_watcher.start()

    # Run gphoto2 USB shutter event listener on main thread
    try:
        run_gphoto2_shutter_listener()
    except KeyboardInterrupt:
        logger.info("Shutter Bridge stopped.")
