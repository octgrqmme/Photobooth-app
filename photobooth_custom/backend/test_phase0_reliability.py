import importlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from gallery_folders import GalleryFolderStore
from guest_queue import GuestQueueStore
from photobooth.routers.api import share

_BACKEND_DIR = str(Path(__file__).resolve().parent)
_REPO_ROOT = Path(__file__).resolve().parents[2]
_saved_sys_path = list(sys.path)
sys.path = [p for p in sys.path if p != _BACKEND_DIR]
try:
    gphoto2_mod = importlib.import_module("photobooth.services.backends.gphoto2")
finally:
    sys.path = _saved_sys_path


class Phase0ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = FastAPI()
        self.app.include_router(share.router, prefix="/api")
        self.local_client = TestClient(self.app, client=("127.0.0.1", 50000))
        self.remote_client = TestClient(self.app, client=("192.0.2.99", 50000))

    def test_ft01_ft02_camera_reconnect_cleans_up_on_error_and_recovers(self):
        backend = gphoto2_mod.Gphoto2Backend.__new__(gphoto2_mod.Gphoto2Backend)
        backend._camera = None
        backend._desired_ui_locked = True
        backend._ui_locked = True
        backend._liveview_grace_until = 0.0

        exited = []
        created_cams = []

        class FakeCam:
            def __init__(self, fail_init: bool):
                self.fail_init = fail_init
                created_cams.append(self)

            def init(self):
                if self.fail_init:
                    raise RuntimeError("USB I/O error during init")

            def exit(self):
                exited.append(self)

        fake_usb_core = SimpleNamespace(find=lambda **_kwargs: [])
        fake_usb_mod = ModuleType("usb")
        fake_usb_mod.core = fake_usb_core

        with (
            patch.dict(sys.modules, {"usb": fake_usb_mod, "usb.core": fake_usb_core}),
            patch.object(gphoto2_mod.time, "sleep"),
            patch.object(gphoto2_mod.gp, "Camera", side_effect=[FakeCam(fail_init=True), FakeCam(fail_init=False)]),
            patch.object(backend, "_configure_canon_camera"),
        ):
            self.assertFalse(backend._reconnect_camera())
            self.assertEqual(len(exited), 1)
            self.assertIsNone(backend._camera)

            self.assertTrue(backend._reconnect_camera())
            self.assertIs(backend._camera, created_cams[1])

    def test_ft03_unknown_print_job_requires_manual_resolution_without_reprint(self):
        queue_store = GuestQueueStore(self.root / "queue.json")
        queue_store.create()
        queue_store.start("SCI01")
        queue_store.attach_job("job-1")
        queue_store.set_media("job-1", ["media-1"])

        # Simulate print job missing cups_id -> immediate unknown state
        queue_store.record_print("job-1", "media-1", None, 1, "Brother_DCP_T830DW")
        # Also simulate stale print job (>15s missing from CUPS)
        queue_store.record_print("job-1", "media-1", "Brother_DCP_T830DW-77", 1, "Brother_DCP_T830DW")
        data = queue_store._read()
        data["entries"][0]["print_jobs"][1]["submitted_at"] = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        queue_store._write(data)
        queue_store.update_print_states(set(), set())

        with (
            patch.object(share, "guest_queue_store", queue_store),
            patch.object(share, "_load_printer_profiles", return_value={"active_profile_id": "p1", "profiles": [{"id": "p1", "printer_name": "Brother_DCP_T830DW"}]}),
            patch.object(share.container.mediacollection_service, "list_items", return_value=[]),
            patch.object(share.subprocess, "run") as mock_run,
        ):
            mock_run.side_effect = [
                SimpleNamespace(returncode=0, stdout="printer Brother_DCP_T830DW is idle", stderr=""),
                SimpleNamespace(returncode=0, stdout="Brother_DCP_T830DW is ready\nno entries", stderr=""),
            ]
            pq = self.local_client.get("/api/share/print_queue")
            self.assertEqual(pq.status_code, 200)
            unknown_jobs = pq.json()["unknown_print_jobs"]
            self.assertEqual(len(unknown_jobs), 2)

            # Remote LAN device cannot resolve unknown print jobs
            remote = self.remote_client.post("/api/share/guest-queue/print-jobs/resolve", json={
                "queue_code": "SCI01",
                "outcome": "printed",
                "print_job_id": unknown_jobs[0]["print_job_id"],
            })
            self.assertEqual(remote.status_code, 403)

            # Reset round blocked while unknown print jobs exist
            with self.assertRaises(ValueError):
                queue_store.reset_round("รอบบ่าย")

            # Operator resolves first as canceled and second as printed
            res1 = self.local_client.post("/api/share/guest-queue/print-jobs/resolve", json={
                "queue_code": "SCI01",
                "outcome": "canceled",
                "print_job_id": unknown_jobs[0]["print_job_id"],
            })
            self.assertEqual(res1.status_code, 200, res1.text)
            self.assertEqual(res1.json()["entry"]["status"], "printing")

            res2 = self.local_client.post("/api/share/guest-queue/print-jobs/resolve", json={
                "queue_code": "SCI01",
                "outcome": "printed",
                "cups_id": "Brother_DCP_T830DW-77",
            })
            self.assertEqual(res2.status_code, 200, res2.text)
            entry = res2.json()["entry"]
            self.assertEqual(entry["status"], "printed")
            self.assertTrue(entry["print_jobs"][1]["operator_verified"])
            self.assertIsNotNone(entry["print_jobs"][1]["resolved_at"])

            # Re-resolving an already-resolved job returns 409
            dup = self.local_client.post("/api/share/guest-queue/print-jobs/resolve", json={
                "queue_code": "SCI01",
                "outcome": "printed",
                "cups_id": "Brother_DCP_T830DW-77",
            })
            self.assertEqual(dup.status_code, 409)

            # Round reset succeeds once no unknown/pending jobs remain
            reset_res = queue_store.reset_round("รอบบ่าย")
            self.assertEqual(reset_res["name"], "รอบบ่าย")

    def test_ft04_ft06_printer_offline_and_cloud_offline_keep_local_flow_safe(self):
        image = self.root / "photo.jpg"
        image.write_bytes(b"\xff\xd8\xff\xe0local-jpeg-bytes")
        item_id = uuid4()
        job_id = uuid4()
        item = SimpleNamespace(id=item_id, job_identifier=job_id, media_type="collage", processed=image, pipeline_config={"print_layout": "full"})
        profile = {"id": "p1", "name": "Main", "printer_name": "Brother_DCP_T830DW", "paper_size": "4x6.Fullbleed", "copies": 1}

        queue_store = GuestQueueStore(self.root / "queue.json")
        cloud_store = self.root / "cloud.json"
        cloud_env = self.root / ".env.local"
        cloud_env.touch()

        with (
            patch.object(share, "guest_queue_store", queue_store),
            patch.object(share, "CLOUD_STORE", cloud_store),
            patch.object(share, "CLOUD_ENV", cloud_env),
            patch.object(share, "_get_item_or_404", return_value=item),
            patch.object(share, "_active_printer_profile", return_value=profile),
            patch.object(share.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="เครื่องพิมพ์ Brother_DCP_T830DW ไม่ได้ทำงาน", stderr="")) as mock_lpstat,
        ):
            res = self.local_client.post(f"/api/share/print/{item_id}", json={"profile_id": "p1", "copies": 1})
            self.assertEqual(res.status_code, 503)
            self.assertEqual(mock_lpstat.call_count, 1)

        # Simulate cloud upload failing while offline -> status=failed + retry_at, local download stays 200
        with (
            patch.object(share, "CLOUD_STORE", cloud_store),
            patch.object(share, "CLOUD_ENV", cloud_env),
            patch.object(share, "_get_item_or_404", return_value=item),
            patch.object(share.subprocess, "run", side_effect=OSError("offline")),
        ):
            share._cloud_upload(str(item_id), image, False)
            records = share._cloud_records()
            self.assertEqual(records[str(item_id)]["status"], "failed")
            self.assertGreater(records[str(item_id)]["retry_at"], 0)
            local_dl = self.local_client.get(f"/api/share/download-image/{item_id}")
            self.assertEqual(local_dl.status_code, 200)
            self.assertEqual(local_dl.content, b"\xff\xd8\xff\xe0local-jpeg-bytes")

    def test_health_and_preflight_diagnostics(self):
        test_engine = create_engine(f"sqlite:///{self.root / 'db.sqlite'}")
        with test_engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        self.addCleanup(test_engine.dispose)

        cfg_path = self.root / "config.json"
        cfg_path.write_text(json.dumps({"common": {"admin_password": "0000"}, "misc": {"secret": "a" * 64}}), encoding="utf-8")
        queue_store = GuestQueueStore(self.root / "queue.json")
        folder_store = GalleryFolderStore(self.root / "folders.json")

        with (
            patch.object(share, "engine", test_engine),
            patch.object(share, "CONFIG_FILE", cfg_path),
            patch.object(share, "guest_queue_store", queue_store),
            patch.object(share, "gallery_folder_store", folder_store),
            patch.object(share, "_load_printer_profiles", return_value={"profiles": []}),
            patch.object(share.container.acquisition_service, "_backends", []),
        ):
            health = self.local_client.get("/api/share/health")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json()["status"], "ok")
            self.assertTrue(health.json()["database_ok"])

            self.assertEqual(self.remote_client.get("/api/share/preflight").status_code, 403)
            preflight = self.local_client.get("/api/share/preflight")
            self.assertEqual(preflight.status_code, 200)
            body = preflight.json()
            self.assertFalse(body["ready_for_event"])
            self.assertFalse(body["ready_to_capture"])
            self.assertFalse(body["ready_to_print"])
            by_id = {c["id"]: c for c in body["checks"]}
            self.assertEqual(by_id["admin_password"]["status"], "fail")
            self.assertEqual(by_id["misc_secret"]["status"], "ok")
            self.assertEqual(by_id["database"]["status"], "ok")
            self.assertEqual(by_id["stores"]["status"], "ok")

            with (
                patch.object(share, "_load_printer_profiles", return_value={"profiles": [{"printer_name": "Test_Printer"}]}),
                patch.object(share.subprocess, "run", return_value=SimpleNamespace(
                    returncode=0, stdout="printer Test_Printer is idle", stderr=""
                )),
            ):
                print_only = self.local_client.get("/api/share/preflight").json()
            self.assertFalse(print_only["ready_for_event"])
            self.assertFalse(print_only["ready_to_capture"])
            self.assertTrue(print_only["ready_to_print"])

    def test_backup_and_restore_drill(self):
        src = self.root / "source"
        (src / "database").mkdir(parents=True)
        (src / "config").mkdir(parents=True)
        (src / "userdata" / "photobooth_templates").mkdir(parents=True)
        (src / "media").mkdir(parents=True)

        conn = sqlite3.connect(str(src / "database" / "database.sqlite"))
        conn.execute("CREATE TABLE items (id TEXT PRIMARY KEY, val TEXT)")
        conn.execute("INSERT INTO items VALUES ('m1', 'ok')")
        conn.commit()
        conn.close()

        (src / "config" / "config.json").write_text('{"common": {"admin_password": "secret-pw"}}', encoding="utf-8")
        (src / "userdata" / "photobooth_guest_queue.json").write_text('{"entries": [{"code": "SCI01"}]}', encoding="utf-8")
        (src / "userdata" / "photobooth_folders.json").write_text('{"projects": [], "assignments": {}}', encoding="utf-8")
        (src / "userdata" / "photobooth_templates" / "templates.json").write_text('[{"id": "custom-1"}]', encoding="utf-8")
        (src / "media" / "sample.jpg").write_bytes(b"\xff\xd8\xff\xe0sample")

        backup_state = self.root / "backup_state"
        backup_full = self.root / "backup_full"
        restore_target = self.root / "target"
        restore_target.mkdir()

        subprocess.run(["bash", str(_REPO_ROOT / "scripts" / "backup.sh"), "--source", str(src), "--output", str(backup_state)], check=True, capture_output=True, text=True)
        manifest_state = json.loads((backup_state / "manifest.json").read_text(encoding="utf-8"))
        paths_state = {item["path"] for item in manifest_state["files"]}
        self.assertIn("database/database.sqlite", paths_state)
        self.assertIn("config/config.json", paths_state)
        self.assertIn("userdata/photobooth_guest_queue.json", paths_state)
        self.assertNotIn("media/sample.jpg", paths_state)

        subprocess.run(["bash", str(_REPO_ROOT / "scripts" / "backup.sh"), "--source", str(src), "--output", str(backup_full), "--include-media"], check=True, capture_output=True, text=True)
        subprocess.run(["bash", str(_REPO_ROOT / "scripts" / "restore.sh"), str(backup_full), "--target", str(restore_target), "--dry-run"], check=True, capture_output=True, text=True)
        self.assertEqual(list(restore_target.iterdir()), [])

        # Populate target with stale data, then restore and verify safety backup + exact restoration
        (restore_target / "config").mkdir(parents=True)
        (restore_target / "config" / "config.json").write_text('{"stale": true}', encoding="utf-8")
        subprocess.run(["bash", str(_REPO_ROOT / "scripts" / "restore.sh"), str(backup_full), "--target", str(restore_target)], check=True, capture_output=True, text=True)

        self.assertTrue(any(p.name.startswith("pre-restore-") for p in (restore_target / "backups").iterdir()))
        self.assertEqual((restore_target / "config" / "config.json").read_text(encoding="utf-8"), '{"common": {"admin_password": "secret-pw"}}')
        self.assertEqual((restore_target / "media" / "sample.jpg").read_bytes(), b"\xff\xd8\xff\xe0sample")
        r_conn = sqlite3.connect(str(restore_target / "database" / "database.sqlite"))
        try:
            self.assertEqual(r_conn.execute("SELECT val FROM items WHERE id='m1'").fetchone()[0], "ok")
        finally:
            r_conn.close()

        # Tampering with a backed-up file must fail dry-run verification
        tampered = self.root / "backup_tampered"
        shutil.copytree(backup_full, tampered)
        (tampered / "files" / "config" / "config.json").write_text('{"tampered": true}', encoding="utf-8")
        bad = subprocess.run(["bash", str(_REPO_ROOT / "scripts" / "restore.sh"), str(tampered), "--dry-run"], capture_output=True, text=True)
        self.assertNotEqual(bad.returncode, 0)


if __name__ == "__main__":
    unittest.main()
