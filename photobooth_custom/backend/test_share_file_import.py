"""Exercise the editable source router without replacing a running Photobooth install."""

import importlib.util
import base64
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image


SOURCE = Path(__file__).with_name("share.py")
SPEC = importlib.util.spec_from_file_location("photobooth.routers.api.share_file_import_test", SOURCE)
share = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(share)


def png(width=600, height=900):
    output = BytesIO()
    Image.new("RGB", (width, height), "red").save(output, "PNG")
    return output.getvalue()


class ImportWithoutQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        app = FastAPI()
        app.include_router(share.router, prefix="/api")
        @app.middleware("http")
        async def local_client(request, call_next):
            request.scope["client"] = ("127.0.0.1", 12345)
            return await call_next(request)
        self.client = TestClient(app, base_url="http://127.0.0.1")
        self.template = {
            "id": "one-slot", "canvas_width": 600, "canvas_height": 900,
            "slots": [{"x": 50, "y": 50, "width": 500, "height": 800}],
            "print_layout": "full",
        }
        self.service = SimpleNamespace(add_item=MagicMock(), delete_item=MagicMock())
        self.folder = SimpleNamespace(snapshot=lambda: {"active_id": None}, assign=MagicMock())
        self.queue = MagicMock()

    def _post(self, photo_data=None, image_data=None):
        return self.client.post(
            "/api/share/compose-template-from-files",
            data={"template_id": "one-slot", "title": "Imported", "subtitle": "No queue"},
            files=[
                ("composed_image", ("template.png", image_data if image_data is not None else png(), "image/png")),
                ("photos", ("source.png", photo_data if photo_data is not None else png(100, 100), "image/png")),
            ],
        )

    def test_import_saves_hidden_source_and_visible_result_without_queue(self):
        with patch.object(share, "PATH_PROCESSED", self.temp.name), \
             patch.object(share, "CLOUD_ENV", Path(self.temp.name) / "missing.env"), \
             patch.object(share, "_find_template", return_value=self.template), \
             patch.object(share, "_request_base_url", return_value="http://localhost:8000"), \
             patch.object(share.container, "mediacollection_service", self.service), \
             patch.object(share, "gallery_folder_store", self.folder), \
             patch.object(share, "guest_queue_store", self.queue):
            response = self._post()

        self.assertEqual(response.status_code, 201, response.text)
        self.assertIsNone(response.json()["queue_code"])
        self.assertEqual(self.service.add_item.call_count, 2)
        source, finished = [call.args[0] for call in self.service.add_item.call_args_list]
        self.assertEqual(source.job_identifier, finished.job_identifier)
        self.assertFalse(source.show_in_gallery)
        self.assertTrue(finished.show_in_gallery)
        self.assertEqual(finished.pipeline_config["source_ids"], [str(source.id)])
        self.assertTrue(finished.pipeline_config["imported_from_files"])
        self.assertTrue(Path(source.processed).is_file())
        self.assertTrue(Path(finished.processed).is_file())
        self.queue.assert_not_called()

    def test_invalid_photo_does_not_create_media_or_queue(self):
        with patch.object(share, "PATH_PROCESSED", self.temp.name), \
             patch.object(share, "_find_template", return_value=self.template), \
             patch.object(share.container, "mediacollection_service", self.service), \
             patch.object(share, "guest_queue_store", self.queue):
            response = self._post(photo_data=b"not an image")
        self.assertEqual(response.status_code, 422)
        self.service.add_item.assert_not_called()
        self.queue.assert_not_called()

    def test_nonlocal_import_is_forbidden(self):
        app = FastAPI()
        app.include_router(share.router, prefix="/api")
        self.client = TestClient(app, base_url="http://192.0.2.1")
        response = self._post()
        self.assertEqual(response.status_code, 403)

    def test_reediting_imported_result_still_does_not_attach_queue(self):
        source = SimpleNamespace(id=uuid4(), media_type="image", job_identifier=uuid4())
        self.service.get_items_relto_job = MagicMock(return_value=[source])
        self.queue.code_for_job.return_value = None
        image_data = "data:image/png;base64," + base64.b64encode(png()).decode()
        with patch.object(share, "PATH_PROCESSED", self.temp.name), \
             patch.object(share, "CLOUD_ENV", Path(self.temp.name) / "missing.env"), \
             patch.object(share, "_find_template", return_value=self.template), \
             patch.object(share, "_get_item_or_404", return_value=source), \
             patch.object(share, "_request_base_url", return_value="http://localhost:8000"), \
             patch.object(share.container, "mediacollection_service", self.service), \
             patch.object(share, "gallery_folder_store", self.folder), \
             patch.object(share, "guest_queue_store", self.queue):
            response = self.client.post("/api/share/compose-template", json={
                "image_data": image_data, "source_ids": [str(source.id)],
                "template_id": "one-slot", "title": "Re-edit", "subtitle": "",
            })
        self.assertEqual(response.status_code, 201, response.text)
        self.queue.set_media.assert_not_called()
        self.queue.project_for_job.assert_not_called()


if __name__ == "__main__":
    unittest.main()
