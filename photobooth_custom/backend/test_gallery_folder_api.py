import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient

from gallery_folders import GalleryFolderStore
from photobooth.routers.api import share


class GalleryFolderApiTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = GalleryFolderStore(Path(temp.name) / "folders.json")
        self.item = SimpleNamespace(id=UUID("11111111-1111-1111-1111-111111111111"), created_at=datetime(2026, 9, 26), job_identifier=UUID("22222222-2222-2222-2222-222222222222"), show_in_gallery=True)
        app = FastAPI()
        app.include_router(share.router, prefix="/api")
        self.client = TestClient(app, base_url="http://127.0.0.1")

    def test_old_photo_date_folder_and_confirm_guard(self):
        with patch.object(share, "gallery_folder_store", self.store), patch.object(share, "_visible_gallery_items", return_value=[self.item]), patch.object(share.container.mediacollection_service, "get_items_relto_job", return_value=[self.item]):
            listed = self.client.get("/api/share/gallery-folders")
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(listed.json()["folders"][0]["id"], "date-2026-09-26")
            preview = self.client.get("/api/share/gallery-folders/date-2026-09-26/delete-preview")
            self.assertEqual(preview.json()["photo_count"], 1)
            rejected = self.client.request("DELETE", "/api/share/gallery-folders/date-2026-09-26", json={"confirm_name": "wrong"})
            self.assertEqual(rejected.status_code, 403)
            with self.assertRaises(HTTPException) as error:
                share.api_delete_gallery_folder("date-2026-09-26", share.GalleryDeletePayload(confirm_name="wrong"), SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")))
            self.assertEqual(error.exception.status_code, 409)

    def test_import_requires_matching_visible_photo_count(self):
        project = self.store.create("ปริญญา")
        with patch.object(share, "gallery_folder_store", self.store), patch.object(share, "_visible_gallery_items", return_value=[self.item]), patch.object(share.container.processing_service, "is_occupied", return_value=False):
            with self.assertRaises(HTTPException) as error:
                share.api_import_unassigned_gallery_media(project["id"], share.GalleryImportPayload(confirm_name="ปริญญา", expected_count=0), SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")))
            self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(self.store.snapshot()["assignments"], {})
            imported = share.api_import_unassigned_gallery_media(project["id"], share.GalleryImportPayload(confirm_name="ปริญญา", expected_count=1), SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")))
            self.assertEqual(imported["moved"], 1)
            self.assertEqual(self.store.snapshot()["assignments"][str(self.item.id)], project["id"])


if __name__ == "__main__":
    unittest.main()
