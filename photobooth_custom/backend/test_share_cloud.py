import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from photobooth.routers.api import share
from photobooth.services.platform_store import PlatformStore


class CloudShareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name)
        env = folder / ".env.local"
        env.touch()
        temp_store = PlatformStore(folder / "platform.sqlite")
        for name, value in (
            ("CLOUD_ENV", env),
            ("CLOUD_STORE", folder / "cloud.json"),
            ("platform_store", temp_store),
        ):
            patcher = patch.object(share, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.item_id = UUID("11111111-1111-4111-8111-111111111111")
        image = folder / "photo.jpg"
        image.write_bytes(b"test")
        item = SimpleNamespace(id=self.item_id, media_type="collage", pipeline_config={"custom_template": "theater"}, processed=image)
        patcher = patch.object(share, "_get_item_or_404", return_value=item)
        patcher.start()
        self.addCleanup(patcher.stop)
        app = FastAPI()
        app.include_router(share.router, prefix="/api")
        self.client = TestClient(app, client=("127.0.0.1", 50000))
        share._cloud_inflight.clear()
        self.addCleanup(share._cloud_inflight.clear)

    def test_ready_qr_uses_public_link_and_expiry_blocks_old_link(self):
        public_url = "https://example.vercel.app/api/share?token=secret"
        share._save_cloud_records({str(self.item_id): {"status": "ready", "share_url": public_url, "expires_at": int(time.time()) + 3600}})
        with patch.object(share, "_qr_svg", return_value="QR") as generate:
            response = self.client.get(f"/api/share/qr/{self.item_id}.svg")
        self.assertEqual(response.status_code, 200)
        generate.assert_called_once_with(public_url)
        share._save_cloud_records({str(self.item_id): {"status": "ready", "share_url": public_url, "expires_at": int(time.time()) - 1}})
        expired = self.client.get(f"/api/share/qr/{self.item_id}.svg")
        self.assertEqual(expired.status_code, 410)
        self.assertEqual(self.client.get(f"/api/share/cloud-status/{self.item_id}").json()["status"], "expired")

    def test_old_photo_is_not_uploaded_automatically(self):
        with patch.object(share, "_cloud_upload") as upload:
            response = self.client.get(f"/api/share/cloud-status/{self.item_id}")
        self.assertEqual(response.json()["status"], "disabled")
        upload.assert_not_called()

    def test_old_photo_uploads_only_when_operator_requests_it(self):
        done = threading.Event()
        with patch.object(share, "_cloud_upload", side_effect=lambda *args: done.set()) as upload:
            response = self.client.post(f"/api/share/cloud-share/{self.item_id}")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(done.wait(2))
        self.assertEqual(response.json()["status"], "uploading")
        upload.assert_called_once()
        self.assertEqual(self.client.get(f"/api/share/cloud-status/{self.item_id}").json()["status"], "uploading")

    def test_expired_photo_can_be_renewed_on_request(self):
        share._save_cloud_records({str(self.item_id): {"status": "ready", "share_url": "https://example.test/old", "expires_at": int(time.time()) - 1}})
        done = threading.Event()
        with patch.object(share, "_cloud_upload", side_effect=lambda *args: done.set()) as upload:
            response = self.client.post(f"/api/share/cloud-share/{self.item_id}")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(done.wait(2))
        self.assertEqual(response.json()["status"], "uploading")
        upload.assert_called_once()

    def test_failed_cloud_upload_falls_back_to_local_qr(self):
        share._save_cloud_records({str(self.item_id): {"status": "failed", "retry_at": time.time() + 300}})
        with patch.object(share, "_request_base_url", return_value="http://169.254.10.2:8000"), patch.object(share, "_qr_svg", return_value="QR") as generate:
            response = self.client.get(f"/api/share/qr/{self.item_id}.svg")
        self.assertEqual(response.status_code, 200)
        generate.assert_called_once_with(f"http://169.254.10.2:8000/api/share/download-page/{self.item_id}")

        share._save_cloud_records({str(self.item_id): {"status": "uploading"}})
        pending = self.client.get(f"/api/share/qr/{self.item_id}.svg")
        self.assertEqual(pending.status_code, 503)

    def test_offline_mode_toggle_pauses_uploads_serves_local_qr_and_resumes(self):
        enabled = self.client.post("/api/share/offline-mode", json={"offline_mode": True})
        self.assertEqual(enabled.status_code, 200, enabled.text)
        self.assertTrue(enabled.json()["offline_mode"])
        status_get = self.client.get("/api/share/offline-mode")
        self.assertEqual(status_get.status_code, 200)
        self.assertTrue(status_get.json()["offline_mode"])

        with patch.object(share, "_cloud_upload") as upload:
            cloud = self.client.get(f"/api/share/cloud-status/{self.item_id}")
        self.assertEqual(cloud.status_code, 200)
        self.assertEqual(cloud.json(), {"status": "offline", "offline_mode": True})
        upload.assert_not_called()

        with patch.object(share, "_request_base_url", return_value="http://169.254.10.2:8000"), patch.object(share, "_qr_svg", return_value="QR") as generate:
            qr = self.client.get(f"/api/share/qr/{self.item_id}.svg")
        self.assertEqual(qr.status_code, 200)
        generate.assert_called_once_with(f"http://169.254.10.2:8000/api/share/download-page/{self.item_id}")

        blocked = self.client.post(f"/api/share/cloud-share/{self.item_id}")
        self.assertEqual(blocked.status_code, 409)

        with patch.object(share, "_ensure_outbox_worker") as ensure_worker:
            resumed = self.client.post("/api/share/offline-mode", json={"offline_mode": False})
        self.assertEqual(resumed.status_code, 200, resumed.text)
        self.assertFalse(resumed.json()["offline_mode"])
        ensure_worker.assert_called_once()
        self.assertFalse(self.client.get("/api/share/offline-mode").json()["offline_mode"])


if __name__ == "__main__":
    unittest.main()
