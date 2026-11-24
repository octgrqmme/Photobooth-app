import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from gallery_folders import GalleryFolderStore, folder_for_media


class GalleryFolderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = GalleryFolderStore(Path(temp.name) / "folders.json")

    def test_old_photos_group_by_date_and_projects_are_assignable(self):
        old = SimpleNamespace(id="photo-1", created_at=datetime(2026, 9, 26, 12, 0))
        self.assertEqual(folder_for_media(old, self.store.snapshot()), "date-2026-09-26")
        project = self.store.create("งานโรงละคร")
        self.assertEqual(self.store.snapshot()["active_id"], project["id"])
        self.store.assign(["photo-1"])
        self.assertEqual(folder_for_media(old, self.store.snapshot()), project["id"])
        self.store.move(["photo-1"], None)
        self.assertEqual(folder_for_media(old, self.store.snapshot()), "date-2026-09-26")
        self.store.remove_project(project["id"])
        self.assertIsNone(self.store.snapshot()["active_id"])

    def test_project_data_persists_without_touching_media(self):
        self.store.create("โครงการใหม่")
        self.assertEqual(GalleryFolderStore(self.store.path).snapshot()["projects"][0]["name"], "โครงการใหม่")


if __name__ == "__main__":
    unittest.main()
