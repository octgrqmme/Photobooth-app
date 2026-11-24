import importlib
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from gallery_folders import GalleryFolderStore
from guest_queue import GuestQueueStore
from photobooth.database.models import Base, Cacheditem, Mediaitem
from photobooth.routers.api import share

_BACKEND_DIR = str(Path(__file__).resolve().parent)
_saved_sys_path = list(sys.path)
sys.path = [p for p in sys.path if p != _BACKEND_DIR]
try:
    gphoto2_mod = importlib.import_module("photobooth.services.backends.gphoto2")
finally:
    sys.path = _saved_sys_path


class AuditRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = FastAPI()
        self.app.include_router(share.router, prefix="/api")
        self.local_client = TestClient(self.app, client=("127.0.0.1", 50000))
        self.remote_client = TestClient(self.app, client=("192.0.2.50", 50000))

    def test_delete_gallery_folder_with_cached_items_does_not_crash(self):
        test_engine = create_engine(f"sqlite:///{self.root / 'db.sqlite'}")
        Base.metadata.create_all(test_engine)
        self.addCleanup(test_engine.dispose)

        media_id = uuid4()
        job_id = uuid4()
        processed = self.root / "processed.jpg"
        cached_file = self.root / "cached_thumb.jpg"
        processed.write_bytes(b"processed")
        cached_file.write_bytes(b"cached")

        with Session(test_engine, expire_on_commit=False) as session:
            media = Mediaitem(
                id=media_id,
                media_type="collage",
                created_at=datetime(2026, 9, 27, 12, 0),
                job_identifier=job_id,
                processed=processed,
                captured_original=None,
                pipeline_config={},
                show_in_gallery=True,
            )
            cached = Cacheditem(
                id=uuid4(),
                mediaitem_id=media_id,
                dimension="thumbnail",
                processed=True,
                revision=0,
                filepath=cached_file,
            )
            session.add_all([media, cached])
            session.commit()

        folder_store = GalleryFolderStore(self.root / "folders.json")
        project = folder_store.create("Test Event")
        folder_store.assign([str(media_id)], project["id"])
        queue_store = GuestQueueStore(self.root / "queue.json")
        recycle_dir = self.root / "recycle"
        recycle_dir.mkdir()
        cloud_env = self.root / ".env.local"
        cloud_env.touch()
        cloud_store = self.root / "cloud.json"
        share._save_cloud_records_bak = None
        deleted_items = []

        with (
            patch.object(share, "engine", test_engine),
            patch.object(share, "gallery_folder_store", folder_store),
            patch.object(share, "guest_queue_store", queue_store),
            patch.object(share, "RECYCLE_PATH", recycle_dir),
            patch.object(share, "CLOUD_ENV", cloud_env),
            patch.object(share, "CLOUD_STORE", cloud_store),
            patch.object(share, "_visible_gallery_items", return_value=[media]),
            patch.object(share.container.mediacollection_service, "get_items_relto_job", return_value=[media]),
            patch.object(share.container.mediacollection_service, "delete_item", side_effect=lambda item: deleted_items.append(item.id)),
            patch.object(share.container.processing_service, "is_occupied", return_value=False),
            patch.object(share.container.processing_service, "_workflow_jobmodel", None),
            patch.object(share.subprocess, "run") as mock_run,
        ):
            share._save_cloud_records({str(media_id): {"status": "ready", "share_url": "https://example.test/p", "expires_at": 2000000000}})
            response = self.local_client.request(
                "DELETE",
                f"/api/share/gallery-folders/{project['id']}",
                json={"confirm_name": "Test Event"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(deleted_items, [media_id])
            self.assertFalse(cached_file.exists())
            self.assertEqual(share._cloud_records(), {})
            mock_run.assert_called_once()
            with Session(test_engine) as session:
                remaining_cached = list(session.scalars(select(Cacheditem)).all())
                self.assertEqual(remaining_cached, [])

    def test_share_actions_reject_non_localhost_and_shell_metacharacters(self):
        fake_item = SimpleNamespace(id=uuid4())
        param_def = SimpleNamespace(key="copies", ui_type="int", valid_min="1", valid_max="10")
        fake_config = SimpleNamespace(share=SimpleNamespace(actions=[SimpleNamespace(processing=SimpleNamespace(parameters=[param_def]))]))

        with (
            patch.object(share, "appconfig", fake_config),
            patch.object(share.container.mediacollection_service, "get_item_latest", return_value=fake_item),
            patch.object(share.container.share_service, "share") as mock_share,
        ):
            remote_res = self.remote_client.post("/api/share/actions/latest/0", json={"copies": "1"})
            self.assertEqual(remote_res.status_code, 403)

            bad_val = self.local_client.post("/api/share/actions/latest/0", json={"copies": "1; id"})
            self.assertEqual(bad_val.status_code, 422)

            bad_key = self.local_client.post("/api/share/actions/latest/0", json={"filename": "/etc/passwd"})
            self.assertEqual(bad_key.status_code, 422)

            ok_res = self.local_client.post("/api/share/actions/latest/0", json={"copies": "2"})
            self.assertEqual(ok_res.status_code, 200)
            mock_share.assert_called_once_with(fake_item, 0, {"copies": "2"})

    def test_operator_endpoints_block_remote_while_guest_endpoints_allow_lan(self):
        queue_store = GuestQueueStore(self.root / "queue.json")
        folder_store = GalleryFolderStore(self.root / "folders.json")
        ticket = queue_store.create()
        queue_store.start(ticket["code"])

        with (
            patch.object(share, "guest_queue_store", queue_store),
            patch.object(share, "gallery_folder_store", folder_store),
            patch.object(share, "_cups_jobs", return_value=set()),
            patch.object(share, "_hidden_builtin_ids", return_value=set()),
            patch.object(share, "_activate_template"),
        ):
            self.assertEqual(self.remote_client.post("/api/share/guest-queue/reset", json={"name": "hack"}).status_code, 403)
            self.assertEqual(self.remote_client.post("/api/share/guest-queue/advance").status_code, 403)
            self.assertEqual(self.remote_client.post(f"/api/share/guest-queue/{ticket['code']}/complete").status_code, 403)
            self.assertEqual(self.remote_client.post(f"/api/share/guest-queue/{ticket['code']}/call").status_code, 403)
            self.assertEqual(self.remote_client.delete(f"/api/share/guest-queue/{ticket['code']}").status_code, 403)
            self.assertEqual(self.remote_client.post("/api/share/templates/theater/activate").status_code, 403)
            self.assertEqual(self.remote_client.post("/api/share/printer-profiles/default-4x6/activate").status_code, 403)
            self.assertEqual(self.remote_client.post("/api/share/print_queue/clear").status_code, 403)
            self.assertEqual(self.remote_client.post("/api/share/gallery-folders", json={"name": "hack"}).status_code, 403)
            self.assertEqual(self.remote_client.post(f"/api/share/cloud-share/{uuid4()}").status_code, 403)

            # Guest-facing endpoints remain reachable on LAN
            self.assertEqual(self.remote_client.get("/api/share/guest-queue").status_code, 200)
            self.assertEqual(self.remote_client.get("/api/share/templates").status_code, 200)
            choice = self.remote_client.post(f"/api/share/guest-queue/{ticket['code']}/template", json={"template_id": "cinema"})
            self.assertEqual(choice.status_code, 200)
            free_choice = self.remote_client.post("/api/share/template-chooser/select", json={"template_id": "cinema"})
            self.assertEqual(free_choice.status_code, 200)

    def test_corrupt_queue_and_folder_json_are_backed_up_not_wiped(self):
        queue_path = self.root / "queue.json"
        queue_path.write_text("{corrupt-json", encoding="utf-8")
        q_store = GuestQueueStore(queue_path)
        with self.assertRaises(RuntimeError):
            q_store.snapshot()
        self.assertEqual(queue_path.read_text(encoding="utf-8"), "{corrupt-json")
        self.assertTrue(any(p.name.startswith("queue.json.") and p.name.endswith(".corrupt") for p in self.root.iterdir()))

        folder_path = self.root / "folders.json"
        folder_path.write_text("[not-an-object]", encoding="utf-8")
        f_store = GalleryFolderStore(folder_path)
        with self.assertRaises(RuntimeError):
            f_store.snapshot()
        self.assertEqual(folder_path.read_text(encoding="utf-8"), "[not-an-object]")
        self.assertTrue(any(p.name.startswith("folders.json.") and p.name.endswith(".corrupt") for p in self.root.iterdir()))

    def test_cancel_reprint_keeps_printed_status(self):
        store = GuestQueueStore(self.root / "queue.json")
        store.create()
        store.start("SCI01")
        store.attach_job("job-1")
        store.set_media("job-1", ["collage-1"])
        store.record_print("job-1", "collage-1", "Printer-1", 1, "Printer")
        store.update_print_states(set(), {"Printer-1"})
        self.assertEqual(store.snapshot()["entries"][0]["status"], "printed")

        store.record_print("job-1", "collage-1", "Printer-2", 1, "Printer")
        self.assertEqual(store.snapshot()["entries"][0]["status"], "printing")
        store.cancel_print("Printer-2")
        self.assertEqual(store.snapshot()["entries"][0]["status"], "printed")

    def test_two_up_capture_count_supports_one_and_two_slots(self):
        for slot_count in (1, 2, 3):
            template = {
                "id": f"strip-{slot_count}",
                "print_layout": "two_up",
                "slots": [{"x": 10, "y": 10, "width": 100, "height": 100} for _ in range(slot_count)],
            }
            job = SimpleNamespace(
                _configuration_set=SimpleNamespace(processing=SimpleNamespace(merge_definition=[object()] * (slot_count * 2))),
                _get_number_of_captures_from_merge_definition=lambda defs: len(defs),
            )
            with patch.object(share, "_active_template_id", return_value=template["id"]), patch.object(share, "_find_template", return_value=template):
                self.assertEqual(share._template_capture_count(job), slot_count)

    def test_printer_profile_payload_rejects_flag_injection(self):
        with self.assertRaises(ValidationError):
            share.PrinterProfilePayload(name="Bad", printer_name="-o", paper_size="4x6.Fullbleed")
        with self.assertRaises(ValidationError):
            share.PrinterProfilePayload(name="Bad", printer_name="Brother_DCP", paper_size="4x6;id")
        valid = share.PrinterProfilePayload(name="Good", printer_name="Brother_DCP_T830DW", paper_size="4x6.Fullbleed")
        self.assertEqual(valid.printer_name, "Brother_DCP_T830DW")

    def test_gphoto2_notify_job_completed_sets_drain_flag_without_calling_camera_cross_thread(self):
        backend = gphoto2_mod.Gphoto2Backend.__new__(gphoto2_mod.Gphoto2Backend)
        backend._last_job_completed_time = 0.0
        backend._drain_requested = False
        called = False

        def fail_drain(*_args, **_kwargs):
            nonlocal called
            called = True

        backend._drain_events = fail_drain
        with patch.object(share.container.processing_service, "_workflow_jobmodel", None):
            backend.notify_job_completed()
        self.assertTrue(backend._drain_requested)
        self.assertFalse(called)

    def test_gphoto2_still_request_unlocks_and_notifies_on_missing_download(self):
        import threading
        from collections import deque

        backend = gphoto2_mod.Gphoto2Backend.__new__(gphoto2_mod.Gphoto2Backend)
        backend._stop_event = threading.Event()
        backend._drain_requested = False
        backend._hires_lock = threading.Lock()
        req = gphoto2_mod.StillRequest(uuid4(), subdevice_index=0)
        backend._hires_queue = deque([req])
        backend._capture_busy = True
        backend._capture_started_at = 1.0
        backend._desired_ui_locked = True

        def fake_handle(request_obj):
            # Simulate returning early on file_to_download is None without setting result_file
            backend._finish_capture_transfer()
            backend._stop_event.set()

        backend._handle_still_request = fake_handle
        notified = False
        orig_notify = req.condition.notify_all

        def spy_notify():
            nonlocal notified
            notified = True
            orig_notify()

        req.condition.notify_all = spy_notify
        backend.run_service()
        self.assertTrue(notified)
        self.assertFalse(backend._capture_busy)
        self.assertFalse(backend._desired_ui_locked)

    def test_gphoto2_notify_job_completed_assigns_active_folder_without_touching_queue(self):
        gallery_folders_mod = importlib.import_module("photobooth.services.gallery_folders")
        folder_store = GalleryFolderStore(self.root / "folders.json")
        project = folder_store.create("Loy Krathong")
        media_id = uuid4()
        fake_job = SimpleNamespace(
            _job_identifier=uuid4(),
            _result_mediaitems=[SimpleNamespace(id=media_id)],
        )
        backend = gphoto2_mod.Gphoto2Backend.__new__(gphoto2_mod.Gphoto2Backend)
        backend._last_job_completed_time = 0.0
        backend._drain_requested = False

        with (
            patch.object(share.container.processing_service, "_workflow_jobmodel", fake_job),
            patch.object(gallery_folders_mod, "gallery_folder_store", folder_store),
        ):
            backend.notify_job_completed()

        self.assertTrue(backend._drain_requested)
        self.assertEqual(folder_store.snapshot()["assignments"].get(str(media_id)), project["id"])
        self.assertFalse(hasattr(gphoto2_mod, "guest_queue_store"))


if __name__ == "__main__":
    unittest.main()
