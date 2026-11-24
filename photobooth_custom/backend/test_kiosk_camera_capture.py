"""Tests for kiosk camera frame upload, server-side composition, and template selection."""

import importlib.util
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image


BACKEND_DIR = Path(__file__).resolve().parent


def _load_source_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, BACKEND_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

PlatformStore = _load_source_module("kiosk_test_platform_store", "platform_store.py").PlatformStore
GuestQueueStore = _load_source_module("kiosk_test_guest_queue", "guest_queue.py").GuestQueueStore
share = _load_source_module("photobooth.routers.api.share_kiosk_test", "share.py")


def _make_jpeg_bytes(width: int = 800, height: int = 600, color: tuple[int, int, int] = (220, 40, 40)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="JPEG", quality=92)
    return buf.getvalue()


class KioskCameraCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tmp_path = Path(self.temp.name)
        self.pstore = PlatformStore(self.tmp_path / "platform.sqlite")
        self.gstore = GuestQueueStore(self.tmp_path / "queue.json", platform_store=self.pstore)
        self.active_event = self.pstore.get_active_event()

        # Build two_up 1200x1800 template with distinct background & overlay PNG assets
        self.bg_path = self.tmp_path / "bg.png"
        self.ov_path = self.tmp_path / "ov.png"
        Image.new("RGBA", (1200, 1800), (20, 150, 40, 255)).save(self.bg_path, format="PNG")
        overlay = Image.new("RGBA", (1200, 1800), (0, 0, 0, 0))
        # Paint a solid blue strip overlapping top of slot 1 (y=200..240) to prove overlay sits on top
        for y in range(200, 240):
            for x in range(100, 500):
                overlay.putpixel((x, y), (20, 60, 230, 255))
        overlay.save(self.ov_path, format="PNG")

        self.template = {
            "id": "lk-two-up",
            "name": "ลอยกระทง ทดสอบ",
            "canvas_width": 1200,
            "canvas_height": 1800,
            "print_layout": "two_up",
            "slots": [
                {"x": 100, "y": 200, "width": 400, "height": 300},
                {"x": 100, "y": 540, "width": 400, "height": 300},
                {"x": 100, "y": 880, "width": 400, "height": 300},
            ],
            "right_slots": [
                {"x": 700, "y": 200, "width": 400, "height": 300},
                {"x": 700, "y": 540, "width": 400, "height": 300},
                {"x": 700, "y": 880, "width": 400, "height": 300},
            ],
            "background_path": str(self.bg_path),
            "overlay_path": str(self.ov_path),
        }

        app = FastAPI()
        app.include_router(share.router, prefix="/api")

        @app.middleware("http")
        async def attach_local_client(request, call_next):
            request.scope["client"] = ("127.0.0.1", 12345)
            return await call_next(request)

        self.client = TestClient(app, base_url="http://127.0.0.1")
        self.media_service = SimpleNamespace(add_item=MagicMock(), delete_item=MagicMock())
        self.folder_store = SimpleNamespace(snapshot=lambda: {"active_id": "proj-lk"}, assign=MagicMock())
        self.proc_service = SimpleNamespace(is_occupied=lambda: False, _workflow_jobmodel=None)

    def test_capture_frames_composes_two_up_with_background_and_overlay(self):
        session = self.pstore.create_kiosk_session(self.active_event["id"], station_id="kiosk-1", language="th", template_id="lk-two-up")
        photo_bytes = [
            _make_jpeg_bytes(color=(225, 30, 30)),
            _make_jpeg_bytes(color=(225, 30, 30)),
            _make_jpeg_bytes(color=(225, 30, 30)),
        ]

        with (
            patch.object(share, "PATH_PROCESSED", str(self.tmp_path)),
            patch.object(share, "platform_store", self.pstore),
            patch.object(share, "guest_queue_store", self.gstore),
            patch.object(share, "gallery_folder_store", self.folder_store),
            patch.object(share, "_find_template", return_value=self.template),
            patch.object(share.container, "mediacollection_service", self.media_service),
        ):
            res = self.client.post(
                f"/api/share/kiosk/sessions/{session['id']}/capture-frames",
                data={"template_id": "lk-two-up"},
                files=[("photos", (f"shot-{i}.jpg", data, "image/jpeg")) for i, data in enumerate(photo_bytes)],
            )

        self.assertEqual(res.status_code, 201, res.text)
        body = res.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["preview_url"].startswith("/api/share/download-image/"))

        # 3 hidden sources + 1 visible composed output
        self.assertEqual(self.media_service.add_item.call_count, 4)
        saved_items = [call.args[0] for call in self.media_service.add_item.call_args_list]
        sources, finished = saved_items[:3], saved_items[3]
        self.assertTrue(all(not item.show_in_gallery for item in sources))
        self.assertTrue(finished.show_in_gallery)
        self.assertEqual(finished.media_type, "collage")
        self.assertEqual(finished.pipeline_config["camera_source"], "device")
        self.assertEqual(finished.pipeline_config["custom_template"], "lk-two-up")
        self.folder_store.assign.assert_called_once()

        # Verify composed output image geometry & layering
        with Image.open(finished.processed) as out_img:
            self.assertEqual(out_img.size, (1200, 1800))
            bg_px = out_img.getpixel((50, 50))
            slot_left_px = out_img.getpixel((250, 360))
            slot_right_px = out_img.getpixel((850, 360))
            overlay_px = out_img.getpixel((250, 215))

        self.assertGreater(bg_px[1], bg_px[0] + 60)  # Green background
        self.assertGreater(slot_left_px[0], slot_left_px[2] + 100)  # Red photo left slot
        self.assertGreater(slot_right_px[0], slot_right_px[2] + 100)  # Red photo right slot (two_up repeat)
        self.assertGreater(overlay_px[2], overlay_px[0] + 100)  # Blue overlay strip on top of slot 1

        updated_sess = self.pstore.get_kiosk_session(session["id"])
        self.assertEqual(updated_sess["status"], "preview")
        self.assertEqual(updated_sess["media_id"], str(finished.id))

    def test_capture_frames_rejects_bad_count_missing_or_finalized_session(self):
        session = self.pstore.create_kiosk_session(self.active_event["id"], station_id="kiosk-1", language="th", template_id="lk-two-up")
        finalized = self.pstore.create_kiosk_session(self.active_event["id"], station_id="kiosk-1", language="th", template_id="lk-two-up")
        self.pstore.update_kiosk_session(finalized["id"], status="finalized")

        with (
            patch.object(share, "platform_store", self.pstore),
            patch.object(share, "_find_template", return_value=self.template),
        ):
            # Wrong frame count (1 instead of 3) -> 422
            bad_count = self.client.post(
                f"/api/share/kiosk/sessions/{session['id']}/capture-frames",
                data={"template_id": "lk-two-up"},
                files=[("photos", ("shot-0.jpg", _make_jpeg_bytes(), "image/jpeg"))],
            )
            self.assertEqual(bad_count.status_code, 422)

            # Missing session -> 404
            missing = self.client.post(
                "/api/share/kiosk/sessions/nonexistent-id/capture-frames",
                data={"template_id": "lk-two-up"},
                files=[("photos", (f"shot-{i}.jpg", _make_jpeg_bytes(), "image/jpeg")) for i in range(3)],
            )
            self.assertEqual(missing.status_code, 404)

            # Finalized session -> 409
            already_done = self.client.post(
                f"/api/share/kiosk/sessions/{finalized['id']}/capture-frames",
                data={"template_id": "lk-two-up"},
                files=[("photos", (f"shot-{i}.jpg", _make_jpeg_bytes(), "image/jpeg")) for i in range(3)],
            )
            self.assertEqual(already_done.status_code, 409)

    def test_select_template_updates_session_and_respects_finalized(self):
        session = self.pstore.create_kiosk_session(self.active_event["id"], station_id="kiosk-1", language="th", template_id="old-tpl")
        finalized = self.pstore.create_kiosk_session(self.active_event["id"], station_id="kiosk-1", language="th", template_id="old-tpl")
        self.pstore.update_kiosk_session(finalized["id"], status="finalized")

        with (
            patch.object(share, "platform_store", self.pstore),
            patch.object(share, "guest_queue_store", self.gstore),
            patch.object(share, "_find_template", return_value=self.template),
            patch.object(share, "_activate_template") as activate_mock,
            patch.object(share.container, "processing_service", self.proc_service),
        ):
            ok_res = self.client.post(
                f"/api/share/kiosk/sessions/{session['id']}/template",
                json={"template_id": "lk-two-up"},
            )
            self.assertEqual(ok_res.status_code, 200, ok_res.text)
            self.assertEqual(ok_res.json()["template_id"], "lk-two-up")
            activate_mock.assert_called_once_with(self.template)

            updated = self.pstore.get_kiosk_session(session["id"])
            self.assertEqual(updated["template_id"], "lk-two-up")

            fin_res = self.client.post(
                f"/api/share/kiosk/sessions/{finalized['id']}/template",
                json={"template_id": "lk-two-up"},
            )
            self.assertEqual(fin_res.status_code, 409)
