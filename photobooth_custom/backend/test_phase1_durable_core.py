import asyncio
import importlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from fastapi import HTTPException

REPO_ROOT = Path(__file__).resolve().parents[2]


def _import_installed_share_modules():
    """Import installed photobooth modules while avoiding local gphoto2.py shadowing."""
    backend_dir = str(Path(__file__).resolve().parent)
    removed = []
    while backend_dir in sys.path:
        sys.path.remove(backend_dir)
        removed.append(backend_dir)
    try:
        platform_store_mod = importlib.import_module("photobooth.services.platform_store")
        guest_queue_mod = importlib.import_module("photobooth.services.guest_queue")
        share_mod = importlib.import_module("photobooth.routers.api.share")
        return platform_store_mod, guest_queue_mod, share_mod
    finally:
        for entry in reversed(removed):
            sys.path.insert(0, entry)


def _find_route_endpoint(router, method: str, suffix: str):
    method_upper = method.upper()
    for route in router.routes:
        methods = getattr(route, "methods", set()) or set()
        path = getattr(route, "path", "")
        if method_upper in methods and path.endswith(suffix):
            return route.endpoint
    raise AssertionError(f"Route not found: {method_upper} *{suffix}")


def _make_request(host: str = "127.0.0.1", headers: dict | None = None):
    return SimpleNamespace(
        client=SimpleNamespace(host=host),
        headers=headers or {},
    )


class Phase1DurableCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.platform_store_mod, cls.guest_queue_mod, cls.share_mod = _import_installed_share_modules()

    def test_platform_store_wal_migration_quick_check_and_audit_redaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "userdata" / "photobooth_platform.sqlite"
            store = self.platform_store_mod.PlatformStore(db_path)

            ok, detail = store.quick_check()
            self.assertTrue(ok, detail)
            self.assertEqual(store.schema_version(), self.platform_store_mod.SCHEMA_VERSION)

            conn = sqlite3.connect(str(db_path))
            try:
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                self.assertEqual(str(mode).lower(), "wal")
                versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()]
                self.assertIn(1, versions)
                self.assertIn(self.platform_store_mod.SCHEMA_VERSION, versions)
            finally:
                conn.close()

            store.save_kv("sample", {"count": 2, "items": ["a", "b"]})
            self.assertEqual(store.load_kv("sample"), {"count": 2, "items": ["a", "b"]})
            self.assertIsNone(store.load_kv("missing"))

            store.save_idempotency("scope.test", "k1", 201, {"code": "A001"})
            cached = store.get_idempotency("scope.test", "k1")
            self.assertIsNotNone(cached)
            self.assertEqual(cached[0], 201)
            self.assertEqual(cached[1], {"code": "A001"})

            store.record_audit(
                "operator",
                "127.0.0.1",
                "config.test",
                "system",
                "cfg-1",
                "ok",
                {
                    "admin_password": "top-secret-password",
                    "misc": {"secret": "hmac-secret-value", "token": "tok-123"},
                    "nested": {"image_data": "data:image/png;base64,AAA", "safe": "ok"},
                    "background_data": "blob",
                    "overlay_data": "blob2",
                },
            )
            events = store.list_audit_events(limit=10)
            self.assertGreaterEqual(len(events), 1)
            serialized = json.dumps(events[0], ensure_ascii=False)
            self.assertNotIn("top-secret-password", serialized)
            self.assertNotIn("hmac-secret-value", serialized)
            self.assertNotIn("tok-123", serialized)
            self.assertNotIn("data:image/png;base64,AAA", serialized)
            self.assertIn("ok", serialized)

    def test_guest_queue_auto_migration_persistence_and_transitions(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            db_path = tmp_dir / "platform.sqlite"
            legacy_json = tmp_dir / "guest_queue.json"
            legacy_payload = {
                "next_number": 4,
                "next_numbers": {"A": 4},
                "categories": [{"prefix": "A", "label": "ทั่วไป"}],
                "round_id": "round-1",
                "round_name": "รอบทดสอบ",
                "active_code": "A003",
                "entries": [
                    {
                        "id": "entry-1",
                        "code": "A003",
                        "prefix": "A",
                        "people_count": 2,
                        "status": "ready",
                        "created_at": 1700000000.0,
                        "updated_at": 1700000001.0,
                        "job_ids": [],
                        "media_ids": ["m-1"],
                        "print_jobs": [],
                    }
                ],
            }
            legacy_json.write_text(json.dumps(legacy_payload), encoding="utf-8")

            store = self.platform_store_mod.PlatformStore(db_path)
            gq_store = self.guest_queue_mod.GuestQueueStore(legacy_json, platform_store=store)
            snapshot = gq_store.snapshot()
            self.assertEqual(snapshot["active_code"], "A003")
            self.assertEqual(snapshot["entries"][0]["code"], "A003")

            # Remove legacy JSON and instantiate a fresh store backed by the same SQLite DB.
            legacy_json.unlink()
            gq_store_restarted = self.guest_queue_mod.GuestQueueStore(legacy_json, platform_store=store)
            snapshot_after = gq_store_restarted.snapshot()
            self.assertEqual(snapshot_after["active_code"], "A003")
            self.assertEqual(snapshot_after["entries"][0]["code"], "A003")

            # Illegal transition checks
            ready_entry = {"id": "e1", "status": "ready"}
            with self.assertRaises(ValueError):
                self.guest_queue_mod._set_entry_status(ready_entry, "waiting")

            canceled_entry = {"id": "e2", "status": "canceled"}
            with self.assertRaises(ValueError):
                self.guest_queue_mod._set_entry_status(canceled_entry, "printing")

            waiting_entry = {"id": "e3", "status": "waiting"}
            self.guest_queue_mod._set_entry_status(waiting_entry, "shooting")
            self.assertEqual(waiting_entry["status"], "shooting")

    def test_idempotency_key_guest_queue_and_print_and_invalid_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            pstore = self.platform_store_mod.PlatformStore(tmp_dir / "platform.sqlite")
            gstore = self.guest_queue_mod.GuestQueueStore(tmp_dir / "guest_queue.json", platform_store=pstore)

            create_ep = _find_route_endpoint(self.share_mod.router, "POST", "/guest-queue")
            print_ep = _find_route_endpoint(self.share_mod.router, "POST", "/print/{id}")

            with (
                patch.object(self.share_mod, "platform_store", pstore),
                patch.object(self.share_mod, "guest_queue_store", gstore),
                patch.object(self.share_mod, "_ensure_outbox_worker", lambda: None),
            ):
                # Invalid Idempotency-Key -> 422
                bad_req = _make_request(headers={"Idempotency-Key": "bad key with spaces!"})
                payload_obj = self.share_mod.GuestQueueCreatePayload(prefix="SCI", people_count=2)
                with self.assertRaises(HTTPException) as ctx:
                    create_ep(bad_req, payload_obj)
                self.assertEqual(ctx.exception.status_code, 422)

                # Valid Idempotency-Key -> deduplicated
                good_req = _make_request(headers={"Idempotency-Key": "create-idem-1"})
                res1 = create_ep(good_req, payload_obj)
                res2 = create_ep(good_req, payload_obj)

                body1 = json.loads(res1.body.decode("utf-8")) if hasattr(res1, "body") else res1
                body2 = json.loads(res2.body.decode("utf-8")) if hasattr(res2, "body") else res2
                self.assertEqual(body1["code"], body2["code"])
                self.assertEqual(len(gstore.snapshot()["entries"]), 1)

                audits = pstore.list_audit_events(limit=10)
                self.assertTrue(any("guest" in str(ev.get("action", "")).lower() or "queue" in str(ev.get("action", "")).lower() for ev in audits))

                # Print endpoint idempotency -> second call must not submit to CUPS again
                media_id = UUID("00000000-0000-0000-0000-000000000010")
                media_file = tmp_dir / "photo.jpg"
                media_file.write_bytes(b"fake-jpg")
                print_calls = []

                def fake_send_to_printer(path, profile, copies, title=None):
                    print_calls.append((str(path), profile["id"], copies))
                    return "request id is MockPrinter-42 (1 file(s))"

                fake_item = SimpleNamespace(id=media_id, job_identifier="job-print-1", processed=media_file)
                fake_profile = {"id": "prof-1", "name": "Mock", "printer_name": "MockPrinter"}

                with (
                    patch.object(self.share_mod, "_get_item_or_404", return_value=fake_item),
                    patch.object(self.share_mod, "_active_printer_profile", return_value=fake_profile),
                    patch.object(self.share_mod, "_send_to_printer", side_effect=fake_send_to_printer),
                ):
                    print_req = _make_request(headers={"Idempotency-Key": "print-idem-1"})
                    print_payload = self.share_mod.PrintPayload(profile_id="prof-1", copies=1)
                    pr1 = print_ep(media_id, print_payload, print_req)
                    pr2 = print_ep(media_id, print_payload, print_req)

                    pb1 = json.loads(pr1.body.decode("utf-8")) if hasattr(pr1, "body") else pr1
                    pb2 = json.loads(pr2.body.decode("utf-8")) if hasattr(pr2, "body") else pr2
                    self.assertEqual(pb1, pb2)
                    self.assertEqual(len(print_calls), 1)
                    self.assertEqual(pb1["print_job_id"], "MockPrinter-42")

    def test_admin_token_auth_and_audit_events_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            pstore = self.platform_store_mod.PlatformStore(tmp_dir / "platform.sqlite")
            pstore.record_audit("operator", "127.0.0.1", "test.event", "queue", "A001", "ok", {"ok": True})
            audit_ep = _find_route_endpoint(self.share_mod.router, "GET", "/audit-events")
            cfg_file = tmp_dir / "config.json"

            cfg_file.write_text(
                json.dumps({"common": {"admin_password": "0000"}, "misc": {"secret": "very-long-random-secret-value-123456"}}),
                encoding="utf-8",
            )
            with (
                patch.object(self.share_mod, "platform_store", pstore),
                patch.object(self.share_mod, "CONFIG_FILE", cfg_file),
            ):
                token_default = self.share_mod._compute_admin_token()
                self.assertIsNone(token_default)
                remote_req = _make_request("192.168.1.50", headers={"X-Admin-Token": "anything"})
                with self.assertRaises(HTTPException) as ctx:
                    self.share_mod._require_role(remote_req, "admin", "admin required")
                self.assertIn(ctx.exception.status_code, (401, 403))

            cfg_file.write_text(
                json.dumps({"common": {"admin_password": "event-pass-9876"}, "misc": {"secret": "very-long-random-secret-value-123456"}}),
                encoding="utf-8",
            )
            with (
                patch.object(self.share_mod, "platform_store", pstore),
                patch.object(self.share_mod, "CONFIG_FILE", cfg_file),
            ):
                valid_token = self.share_mod._compute_admin_token()
                self.assertIsNotNone(valid_token)
                self.assertTrue(len(valid_token) >= 32)

                with self.assertRaises(HTTPException) as ctx_bad:
                    self.share_mod._require_role(_make_request("192.168.1.50", {"X-Admin-Token": "wrong-token"}), "admin", "admin required")
                self.assertIn(ctx_bad.exception.status_code, (401, 403))

                res = audit_ep(_make_request("192.168.1.50", {"X-Admin-Token": valid_token}), limit=20)
                items = res.get("events", [])
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["action"], "test.event")

                res_local = audit_ep(_make_request("127.0.0.1"), limit=20)
                self.assertEqual(len(res_local.get("events", [])), 1)

    def test_durable_outbox_endpoints_and_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            pstore = self.platform_store_mod.PlatformStore(tmp_dir / "platform.sqlite")
            outbox_ep = _find_route_endpoint(self.share_mod.router, "GET", "/outbox")
            retry_ep = _find_route_endpoint(self.share_mod.router, "POST", "/outbox/{media_id}/retry")

            media_uuid = UUID("00000000-0000-0000-0000-000000000022")
            img_path = tmp_dir / "outbox.jpg"
            img_path.write_bytes(b"img")

            pstore.enqueue_upload(str(media_uuid), str(img_path), False)
            pstore.mark_upload_failed(str(media_uuid), "network timeout", backoff_seconds=300)

            fake_item = SimpleNamespace(id=media_uuid, job_identifier="j1", processed=img_path)

            with (
                patch.object(self.share_mod, "platform_store", pstore),
                patch.object(self.share_mod, "_ensure_outbox_worker", lambda: None),
                patch.object(self.share_mod, "_get_item_or_404", return_value=fake_item),
                patch.object(self.share_mod, "CLOUD_ENV", tmp_dir / "missing.cloud.env"),
            ):
                listed = outbox_ep(_make_request("127.0.0.1"), limit=20)
                items = listed.get("items", [])
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["media_id"], str(media_uuid))
                self.assertEqual(items[0]["state"], "failed")
                self.assertEqual(listed["counts"]["failed"], 1)

                retried = retry_ep(media_uuid, _make_request("127.0.0.1"))
                self.assertTrue(retried["ok"])
                self.assertEqual(retried["item"]["state"], "pending")
                due = pstore.claim_due_uploads(limit=10)
                self.assertTrue(any(row["media_id"] == str(media_uuid) for row in due))

    def test_sse_events_initial_snapshot_and_bus(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            pstore = self.platform_store_mod.PlatformStore(tmp_dir / "platform.sqlite")
            gstore = self.guest_queue_mod.GuestQueueStore(tmp_dir / "guest_queue.json", platform_store=pstore)
            events_ep = _find_route_endpoint(self.share_mod.router, "GET", "/events")
            bus = self.platform_store_mod.EventBus()

            async def _read_first_sse_chunk():
                response = await events_ep(_make_request("127.0.0.1"))
                iterator = response.body_iterator
                try:
                    chunk = await asyncio.wait_for(iterator.__anext__(), timeout=2.0)
                    return chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
                finally:
                    await iterator.aclose()

            with (
                patch.object(self.share_mod, "platform_store", pstore),
                patch.object(self.share_mod, "guest_queue_store", gstore),
                patch.object(self.share_mod, "event_bus", bus),
            ):
                first_chunk = asyncio.run(_read_first_sse_chunk())
                self.assertIn("event: snapshot", first_chunk)
                self.assertIn("data:", first_chunk)
                self.assertGreaterEqual(bus.version, 0)

    def test_backup_and_restore_preserves_platform_sqlite_and_clears_wal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            backup_dir = Path(tmp) / "backup_snap"
            (root / "database").mkdir(parents=True)
            (root / "userdata").mkdir(parents=True)
            (root / "config").mkdir(parents=True)

            main_db = sqlite3.connect(str(root / "database" / "database.sqlite"))
            main_db.execute("CREATE TABLE items (id INTEGER PRIMARY KEY)")
            main_db.commit()
            main_db.close()
            (root / "config" / "config.json").write_text("{}", encoding="utf-8")

            pstore = self.platform_store_mod.PlatformStore(root / "userdata" / "photobooth_platform.sqlite")
            pstore.save_kv("guest_queue", {"counter": 42, "entries": [{"code": "A042"}]})
            pstore.record_audit("operator", "127.0.0.1", "backup.drill", "queue", "A042", "ok", {"note": "before"})

            res_b = subprocess.run(
                ["bash", str(REPO_ROOT / "scripts" / "backup.sh"), "--source", str(root), "--output", str(backup_dir)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(res_b.returncode, 0, res_b.stderr)
            manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
            paths = {item["path"] for item in manifest["files"]}
            self.assertIn("userdata/photobooth_platform.sqlite", paths)

            pstore.save_kv("guest_queue", {"counter": 999, "entries": []})
            (root / "userdata" / "photobooth_platform.sqlite-wal").write_bytes(b"stale-wal-bytes")
            (root / "userdata" / "photobooth_platform.sqlite-shm").write_bytes(b"stale-shm-bytes")

            res_r = subprocess.run(
                [
                    "bash",
                    str(REPO_ROOT / "scripts" / "restore.sh"),
                    str(backup_dir),
                    "--target",
                    str(root),
                    "--no-safety-backup",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(res_r.returncode, 0, res_r.stderr)
            self.assertFalse((root / "userdata" / "photobooth_platform.sqlite-wal").exists())
            self.assertFalse((root / "userdata" / "photobooth_platform.sqlite-shm").exists())

            restored_store = self.platform_store_mod.PlatformStore(root / "userdata" / "photobooth_platform.sqlite")
            self.assertTrue(restored_store.quick_check()[0])
            self.assertEqual(restored_store.load_kv("guest_queue"), {"counter": 42, "entries": [{"code": "A042"}]})


if __name__ == "__main__":
    unittest.main()
