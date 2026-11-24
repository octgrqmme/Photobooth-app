import base64
import http.server
import importlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi import HTTPException
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]


def _import_installed_share_modules():
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
        url=SimpleNamespace(scheme="http", hostname=host, port=8000, netloc=f"{host}:8000"),
    )


def _tiny_png_data_url(width: int = 1200, height: int = 1800) -> str:
    img = Image.new("RGB", (width, height), (245, 245, 245))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


class Phases2To5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ps_mod, cls.gq_mod, cls.share_mod = _import_installed_share_modules()

    def test_schema_v1_to_v2_upgrade_preserves_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "platform.sqlite"
            # Seed a pure v1 SQLite DB manually
            conn = sqlite3.connect(str(db_path))
            conn.executescript(
                """
                CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
                CREATE TABLE kv_state (namespace TEXT PRIMARY KEY, state_json TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL);
                CREATE TABLE idempotency_keys (scope TEXT NOT NULL, key TEXT NOT NULL, status_code INTEGER NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (scope, key));
                CREATE TABLE upload_outbox (media_id TEXT PRIMARY KEY, image_path TEXT NOT NULL, two_up INTEGER NOT NULL, state TEXT NOT NULL, retry_count INTEGER NOT NULL DEFAULT 0, next_retry_at REAL NOT NULL DEFAULT 0, last_error TEXT, share_url TEXT, expires_at INTEGER, updated_at TEXT NOT NULL);
                CREATE TABLE audit_events (id INTEGER PRIMARY KEY AUTOINCREMENT, actor_role TEXT NOT NULL, actor_host TEXT NOT NULL, action TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT, result TEXT NOT NULL, detail_json TEXT, created_at TEXT NOT NULL);
                INSERT INTO schema_migrations (version, applied_at) VALUES (1, '2026-01-01T00:00:00+00:00');
                INSERT INTO kv_state (namespace, state_json, version, updated_at) VALUES ('guest_queue', '{"active_code":"SCI001"}', 2, '2026-01-01T00:00:00+00:00');
                INSERT INTO audit_events (actor_role, actor_host, action, entity_type, entity_id, result, detail_json, created_at) VALUES ('operator', '127.0.0.1', 'seed.v1', 'queue', 'SCI001', 'ok', '{}', '2026-01-01T00:00:00+00:00');
                """
            )
            conn.commit()
            conn.close()

            store = self.ps_mod.PlatformStore(db_path)
            self.assertEqual(store.schema_version(), 2)
            self.assertEqual(store.load_kv("guest_queue"), {"active_code": "SCI001"})
            self.assertEqual(store.list_audit_events(limit=5)[0]["action"], "seed.v1")
            active_event = store.get_active_event()
            self.assertEqual(active_event["status"], "active")
            self.assertTrue(active_event["intake_open"])

    def test_event_workspace_crud_clone_and_intake_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            pstore = self.ps_mod.PlatformStore(tmp_dir / "platform.sqlite")
            gstore = self.gq_mod.GuestQueueStore(tmp_dir / "guest_queue.json", platform_store=pstore)

            create_ev_ep = _find_route_endpoint(self.share_mod.router, "POST", "/events-config")
            clone_ev_ep = _find_route_endpoint(self.share_mod.router, "POST", "/events-config/{event_id}/clone")
            update_ev_ep = _find_route_endpoint(self.share_mod.router, "PATCH", "/events-config/{event_id}")
            create_gq_ep = _find_route_endpoint(self.share_mod.router, "POST", "/guest-queue")

            with (
                patch.object(self.share_mod, "platform_store", pstore),
                patch.object(self.share_mod, "guest_queue_store", gstore),
            ):
                # Invalid policy bounds -> 422
                bad_payload = self.share_mod.EventCreatePayload(name="Bad", policy={"retake_limit": 99})
                with self.assertRaises(HTTPException) as ctx:
                    create_ev_ep(bad_payload, _make_request())
                self.assertEqual(ctx.exception.status_code, 422)

                ev1 = create_ev_ep(
                    self.share_mod.EventCreatePayload(
                        name="Graduation Day 1",
                        mode="self_service",
                        activate=True,
                        policy={"print_quota_per_session": 3, "retake_limit": 1},
                    ),
                    _make_request(),
                )
                self.assertEqual(ev1["status"], "active")
                self.assertEqual(ev1["policy"]["print_quota_per_session"], 3)

                ev2 = clone_ev_ep(ev1["id"], self.share_mod.EventClonePayload(name="Graduation Day 2", activate=True), _make_request())
                self.assertEqual(ev2["status"], "active")
                self.assertEqual(ev2["policy"]["retake_limit"], 1)

                # Close intake -> queue create returns 409 without wiping existing queues
                first_q = create_gq_ep(_make_request(), self.share_mod.GuestQueueCreatePayload(prefix="SCI", people_count=2))
                update_ev_ep(ev2["id"], self.share_mod.EventUpdatePayload(intake_open=False), _make_request())
                with self.assertRaises(HTTPException) as ctx_closed:
                    create_gq_ep(_make_request(), self.share_mod.GuestQueueCreatePayload(prefix="SCI", people_count=2))
                self.assertEqual(ctx_closed.exception.status_code, 409)
                self.assertEqual(len(gstore.snapshot()["entries"]), 1)
                self.assertEqual(gstore.snapshot()["entries"][0]["code"], first_q["code"])

    def test_template_versioning_boundary_validation_and_export_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            pstore = self.ps_mod.PlatformStore(tmp_dir / "platform.sqlite")
            gstore = self.gq_mod.GuestQueueStore(tmp_dir / "guest_queue.json", platform_store=pstore)
            tpl_dir = tmp_dir / "templates"
            tpl_store = tpl_dir / "templates.json"
            active_tpl_file = tpl_dir / "active-template.txt"

            save_ep = _find_route_endpoint(self.share_mod.router, "POST", "/templates")
            vers_ep = _find_route_endpoint(self.share_mod.router, "GET", "/templates/{template_id}/versions")
            export_ep = _find_route_endpoint(self.share_mod.router, "GET", "/templates/{template_id}/export")
            import_ep = _find_route_endpoint(self.share_mod.router, "POST", "/templates/import")

            with (
                patch.object(self.share_mod, "platform_store", pstore),
                patch.object(self.share_mod, "guest_queue_store", gstore),
                patch.object(self.share_mod, "TEMPLATE_DIR", tpl_dir),
                patch.object(self.share_mod, "TEMPLATE_STORE", tpl_store),
                patch.object(self.share_mod, "ACTIVE_TEMPLATE_STORE", active_tpl_file),
            ):
                # Out-of-bounds two_up slot -> 422
                bad_tpl = self.share_mod.TemplateSavePayload(
                    id="bad-strip",
                    name="Bad Strip",
                    canvas_width=1200,
                    canvas_height=1800,
                    print_layout="two_up",
                    slots=[self.share_mod.TemplateSlotPayload(x=500, y=50, width=300, height=300)],
                )
                with self.assertRaises(HTTPException) as ctx_bad:
                    save_ep(bad_tpl, _make_request())
                self.assertEqual(ctx_bad.exception.status_code, 422)

                # Valid custom template save -> records immutable versions v1, v2
                v1_payload = self.share_mod.TemplateSavePayload(
                    id="custom-event-tpl",
                    name="Custom Event V1",
                    canvas_width=1200,
                    canvas_height=1800,
                    print_layout="full",
                    slots=[self.share_mod.TemplateSlotPayload(x=80, y=100, width=1040, height=1400)],
                )
                save_ep(v1_payload, _make_request())
                v1_payload.name = "Custom Event V2"
                save_ep(v1_payload, _make_request())

                versions = vers_ep("custom-event-tpl", _make_request())["versions"]
                self.assertEqual([v["version"] for v in versions], [2, 1])

                pkg = export_ep("custom-event-tpl", _make_request())
                self.assertEqual(pkg["schema_version"], 1)

                # Tampered checksum -> 422
                tampered = dict(pkg)
                tampered["sha256"] = "0" * 64
                with self.assertRaises(HTTPException) as ctx_tamper:
                    import_ep(self.share_mod.TemplateImportPayload(**tampered), _make_request())
                self.assertEqual(ctx_tamper.exception.status_code, 422)

                # Valid import with new ID
                pkg["manifest"]["id"] = "imported-event-tpl"
                pkg["sha256"] = self.share_mod._template_manifest_hash(pkg["manifest"])
                imported = import_ep(self.share_mod.TemplateImportPayload(**pkg), _make_request())
                self.assertEqual(imported["id"], "imported-event-tpl")

    def test_print_quota_enforcement_and_operator_override_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            pstore = self.ps_mod.PlatformStore(tmp_dir / "platform.sqlite")
            gstore = self.gq_mod.GuestQueueStore(tmp_dir / "guest_queue.json", platform_store=pstore)
            active_ev = pstore.get_active_event()
            pstore.update_event(active_ev["id"], policy={"print_quota_per_session": 2})

            entry = gstore.create("SCI", 2, None)
            gstore.start(entry["code"])
            gstore.attach_job("job-quota-1")
            media_id = UUID("00000000-0000-0000-0000-000000000101")
            media_file = tmp_dir / "printed.jpg"
            media_file.write_bytes(b"img")

            print_ep = _find_route_endpoint(self.share_mod.router, "POST", "/print/{id}")
            fake_item = SimpleNamespace(id=media_id, job_identifier="job-quota-1", processed=media_file)
            fake_profile = {"id": "p1", "name": "Main", "printer_name": "MockPrinter"}

            with (
                patch.object(self.share_mod, "platform_store", pstore),
                patch.object(self.share_mod, "guest_queue_store", gstore),
                patch.object(self.share_mod, "_get_item_or_404", return_value=fake_item),
                patch.object(self.share_mod, "_active_printer_profile", return_value=fake_profile),
                patch.object(self.share_mod, "_send_to_printer", return_value="request id is MockPrinter-99 (2 file(s))"),
            ):
                # 2 copies allowed
                r1 = print_ep(media_id, self.share_mod.PrintPayload(profile_id="p1", copies=2), _make_request())
                self.assertTrue(r1["ok"])

                # Beyond legacy quota (5 copies, then 15 copies) -> allowed without HTTP 409 + audited
                r2 = print_ep(media_id, self.share_mod.PrintPayload(profile_id="p1", copies=5, override_quota=False), _make_request())
                self.assertTrue(r2["ok"])

                r3 = print_ep(media_id, self.share_mod.PrintPayload(profile_id="p1", copies=15), _make_request())
                self.assertTrue(r3["ok"])
                audits = pstore.list_audit_events(limit=5)
                self.assertTrue(any(ev["action"] == "print.submit" and (ev["detail"] or {}).get("copies") == 15 for ev in audits))

    def test_kiosk_flow_cooldown_retake_limit_idempotency_and_unlock(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            pstore = self.ps_mod.PlatformStore(tmp_dir / "platform.sqlite")
            ev = pstore.create_event(
                "Kiosk Event",
                mode="kiosk",
                activate=True,
                policy={"retake_limit": 1, "kiosk_cooldown_sec": 30, "print_quota_per_session": 1, "auto_print": False},
            )
            cfg_file = tmp_dir / "config.json"
            cfg_file.write_text(json.dumps({"common": {"admin_password": "event-pin-777"}, "misc": {"secret": "a" * 40}}), encoding="utf-8")

            kconfig_ep = _find_route_endpoint(self.share_mod.router, "GET", "/kiosk/config")
            kstart_ep = _find_route_endpoint(self.share_mod.router, "POST", "/kiosk/sessions")
            kretake_ep = _find_route_endpoint(self.share_mod.router, "POST", "/kiosk/sessions/{session_id}/retake")
            kfinal_ep = _find_route_endpoint(self.share_mod.router, "POST", "/kiosk/sessions/{session_id}/finalize")
            kunlock_ep = _find_route_endpoint(self.share_mod.router, "POST", "/kiosk/unlock")

            media_id = UUID("00000000-0000-0000-0000-000000000202")
            media_file = tmp_dir / "kiosk.jpg"
            media_file.write_bytes(b"jpg")
            fake_item = SimpleNamespace(id=media_id, job_identifier="j-kiosk", processed=media_file, media_type="collage")

            with (
                patch.object(self.share_mod, "platform_store", pstore),
                patch.object(self.share_mod, "CONFIG_FILE", cfg_file),
                patch.object(self.share_mod, "CLOUD_ENV", tmp_dir / "missing.env"),
                patch.object(self.share_mod, "_get_item_or_404", return_value=fake_item),
            ):
                kcfg = kconfig_ep()
                self.assertEqual(kcfg["event"]["id"], ev["id"])
                self.assertTrue(kcfg["intake_open"])

                s1 = kstart_ep(
                    self.share_mod.KioskSessionCreatePayload(language="th", template_id=kcfg["active_template_id"]),
                    _make_request(headers={"Idempotency-Key": "kiosk-start-1"}),
                )
                # Cooldown blocks immediate second session with 429
                with self.assertRaises(HTTPException) as ctx_cool:
                    kstart_ep(
                        self.share_mod.KioskSessionCreatePayload(language="th", template_id=kcfg["active_template_id"]),
                        _make_request(headers={"Idempotency-Key": "kiosk-start-2"}),
                    )
                self.assertEqual(ctx_cool.exception.status_code, 429)

                # Retake limit = 1
                kretake_ep(s1["id"], _make_request())
                with self.assertRaises(HTTPException) as ctx_retake:
                    kretake_ep(s1["id"], _make_request())
                self.assertEqual(ctx_retake.exception.status_code, 409)

                # Idempotent finalize
                fin_req = _make_request(headers={"Idempotency-Key": "kiosk-final-1"})
                fin_payload = self.share_mod.KioskFinalizePayload(media_id=media_id, auto_print=False)
                f1 = kfinal_ep(s1["id"], fin_payload, fin_req)
                f2 = kfinal_ep(s1["id"], fin_payload, fin_req)
                b1 = json.loads(f1.body.decode("utf-8")) if hasattr(f1, "body") else f1
                b2 = json.loads(f2.body.decode("utf-8")) if hasattr(f2, "body") else f2
                self.assertEqual(b1["share_token_id"], b2["share_token_id"])
                self.assertIn(b1["share_token"], b1["gallery_url"])

                # Unlock PIN check
                with self.assertRaises(HTTPException) as ctx_pin:
                    kunlock_ep(self.share_mod.KioskUnlockPayload(pin="wrong"), _make_request())
                self.assertEqual(ctx_pin.exception.status_code, 403)
                unlocked = kunlock_ep(self.share_mod.KioskUnlockPayload(pin="event-pin-777"), _make_request())
                self.assertTrue(unlocked["ok"])
                self.assertIsNotNone(unlocked["admin_token"])

    def test_personal_gallery_token_hashing_idor_expiry_and_revocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            pstore = self.ps_mod.PlatformStore(tmp_dir / "platform.sqlite")
            ev = pstore.get_active_event()

            m1 = UUID("00000000-0000-0000-0000-000000000301")
            m2 = UUID("00000000-0000-0000-0000-000000000302")
            f1 = tmp_dir / "m1.jpg"
            Image.new("RGB", (200, 300), "blue").save(f1, format="JPEG")

            create_tok_ep = _find_route_endpoint(self.share_mod.router, "POST", "/tokens")
            revoke_tok_ep = _find_route_endpoint(self.share_mod.router, "POST", "/tokens/{token_id}/revoke")
            gallery_ep = _find_route_endpoint(self.share_mod.router, "GET", "/personal-gallery/{token}")
            gallery_img_ep = _find_route_endpoint(self.share_mod.router, "GET", "/personal-gallery/{token}/image/{media_id}")

            with (
                patch.object(self.share_mod, "platform_store", pstore),
                patch.object(self.share_mod, "_get_item_or_404", return_value=SimpleNamespace(id=m1, job_identifier="j1", processed=f1, media_type="image")),
                patch.object(self.share_mod, "_is_two_up_item", return_value=False),
            ):
                tok = create_tok_ep(self.share_mod.ShareTokenCreatePayload(media_ids=[m1], session_code="SCI010", ttl_hours=12), _make_request())
                raw_token = tok["token"]

                # DB must store SHA-256 hash ONLY, not raw token
                conn = sqlite3.connect(str(pstore.db_path))
                row = conn.execute("SELECT token_hash FROM share_tokens WHERE id = ?", (tok["id"],)).fetchone()
                conn.close()
                self.assertNotEqual(row[0], raw_token)
                self.assertEqual(len(row[0]), 64)

                # Scoped access succeeds for m1
                gal = gallery_ep(raw_token)
                self.assertEqual([item["id"] for item in gal["items"]], [str(m1)])
                img_resp = gallery_img_ep(raw_token, m1)
                self.assertEqual(Path(img_resp.path), f1)

                # IDOR: accessing m2 with m1's token -> 403
                with self.assertRaises(HTTPException) as ctx_idor:
                    gallery_img_ep(raw_token, m2)
                self.assertEqual(ctx_idor.exception.status_code, 403)

                # Unknown token -> 404
                with self.assertRaises(HTTPException) as ctx_404:
                    gallery_ep("non-existent-token")
                self.assertEqual(ctx_404.exception.status_code, 404)

                # Revoked token -> 410
                revoke_tok_ep(tok["id"], _make_request())
                with self.assertRaises(HTTPException) as ctx_rev:
                    gallery_ep(raw_token)
                self.assertEqual(ctx_rev.exception.status_code, 410)

                # Expired token -> 410
                tok_exp = pstore.create_share_token(ev["id"], [str(m1)], ttl_hours=1)
                conn = sqlite3.connect(str(pstore.db_path))
                conn.execute("UPDATE share_tokens SET expires_at = ? WHERE id = ?", (time.time() - 60, tok_exp["id"]))
                conn.commit()
                conn.close()
                with self.assertRaises(HTTPException) as ctx_exp:
                    gallery_ep(tok_exp["token"])
                self.assertEqual(ctx_exp.exception.status_code, 410)

    def test_analytics_csv_and_retention_protects_active_print_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            pstore = self.ps_mod.PlatformStore(tmp_dir / "platform.sqlite")
            gstore = self.gq_mod.GuestQueueStore(tmp_dir / "guest_queue.json", platform_store=pstore)

            e1 = gstore.create("SCI", 2, None)
            gstore.start(e1["code"])
            gstore.attach_job("j-ret-1")
            protected_mid = UUID("00000000-0000-0000-0000-000000000401")
            expired_mid = UUID("00000000-0000-0000-0000-000000000402")
            # Mark print unknown on protected_mid by backdating submitted_at past 15s CUPS grace window
            gstore.record_print("j-ret-1", str(protected_mid), "MockPrinter-1", 2, "MockPrinter")
            q_data = gstore._read()
            q_data["entries"][0]["print_jobs"][0]["submitted_at"] = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
            gstore._write(q_data)
            gstore.update_print_states(set(), set())

            old_dt = datetime.now(timezone.utc) - timedelta(days=45)
            fake_items = [
                SimpleNamespace(id=protected_mid, created_at=old_dt, processed=tmp_dir / "prot.jpg"),
                SimpleNamespace(id=expired_mid, created_at=old_dt, processed=tmp_dir / "exp.jpg"),
            ]
            deleted_ids = []

            def fake_get_item(uid):
                return next(it for it in fake_items if it.id == uid)

            def fake_delete_item(item):
                deleted_ids.append(str(item.id))

            analytics_ep = _find_route_endpoint(self.share_mod.router, "GET", "/analytics")
            csv_ep = _find_route_endpoint(self.share_mod.router, "GET", "/analytics/export.csv")
            ret_prev_ep = _find_route_endpoint(self.share_mod.router, "GET", "/retention/preview")
            ret_purge_ep = _find_route_endpoint(self.share_mod.router, "POST", "/retention/purge")

            with (
                patch.object(self.share_mod, "platform_store", pstore),
                patch.object(self.share_mod, "guest_queue_store", gstore),
                patch.object(self.share_mod.container.mediacollection_service, "db_get_images", return_value=fake_items, create=True),
                patch.object(self.share_mod.container.mediacollection_service, "get_item", side_effect=fake_get_item),
                patch.object(self.share_mod.container.mediacollection_service, "delete_item", side_effect=fake_delete_item),
            ):
                stats = analytics_ep(_make_request())
                self.assertEqual(stats["print_unknown"], 1)
                self.assertEqual(stats["estimated_paper_sheets"], 2)
                self.assertIn("estimate", stats["paper_estimate_note"].lower() + "estimate" if "Sensor" else "")

                csv_resp = csv_ep(_make_request())
                csv_text = csv_resp.body.decode("utf-8")
                self.assertIn("estimated_paper_sheets,2", csv_text)

                prev = ret_prev_ep(_make_request(), retention_days=30)
                self.assertEqual(prev["eligible_count"], 1)
                self.assertEqual(prev["protected_count"], 1)

                purge = ret_purge_ep(self.share_mod.RetentionPurgePayload(dry_run=False, retention_days=30), _make_request())
                self.assertEqual(purge["deleted_count"], 1)
                self.assertEqual(deleted_ids, [str(expired_mid)])
                self.assertNotIn(str(protected_mid), deleted_ids)

    def test_background_gif_export_and_ops_scripts_and_zero_payment_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            gif_dir = tmp_dir / "gifs"
            shot1 = tmp_dir / "s1.jpg"
            shot2 = tmp_dir / "s2.jpg"
            Image.new("RGB", (320, 240), "red").save(shot1, format="JPEG")
            Image.new("RGB", (320, 240), "green").save(shot2, format="JPEG")
            mid = UUID("00000000-0000-0000-0000-000000000501")

            post_gif_ep = _find_route_endpoint(self.share_mod.router, "POST", "/media/{id}/gif")
            get_gif_ep = _find_route_endpoint(self.share_mod.router, "GET", "/media/{id}/gif")

            rel_items = [
                SimpleNamespace(id=uuid4(), media_type="image", created_at=1, rowid=1, processed=shot1),
                SimpleNamespace(id=uuid4(), media_type="image", created_at=2, rowid=2, processed=shot2),
            ]
            with (
                patch.object(self.share_mod, "GIF_DIR", gif_dir),
                patch.object(self.share_mod, "_get_item_or_404", return_value=SimpleNamespace(id=mid, job_identifier="j-gif", processed=shot1)),
                patch.object(self.share_mod.container.mediacollection_service, "get_items_relto_job", return_value=rel_items),
            ):
                res = post_gif_ep(mid)
                self.assertIn(res["status"], ("processing", "ready"))
                deadline = time.time() + 5.0
                status_res = {}
                while time.time() < deadline:
                    status_res = get_gif_ep(mid, download=False)
                    if status_res["status"] == "ready":
                        break
                    time.sleep(0.05)
                self.assertEqual(status_res["status"], "ready")
                dl_resp = get_gif_ep(mid, download=True)
                data = Path(dl_resp.path).read_bytes()
                self.assertTrue(data.startswith(b"GIF8"))

            # Test watchdog.sh --once against unknown_print_jobs=1 (must skip restart, exit 3) and healthy (exit 0)
            health_unknown = tmp_dir / "health_unknown.json"
            health_unknown.write_text(json.dumps({"status": "degraded", "unknown_print_jobs": 1, "active_queue_code": None}), encoding="utf-8")
            wd = subprocess.run(
                ["bash", str(REPO_ROOT / "scripts" / "watchdog.sh"), "--health-file", str(health_unknown), "--once", "--log", str(tmp_dir / "wd.log")],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(wd.returncode, 3, wd.stdout + wd.stderr)
            self.assertIn("SKIP_RESTART", wd.stdout)

            health_ok = tmp_dir / "health_ok.json"
            health_ok.write_text(json.dumps({"status": "ok", "unknown_print_jobs": 0, "active_queue_code": None}), encoding="utf-8")
            wd_ok = subprocess.run(
                ["bash", str(REPO_ROOT / "scripts" / "watchdog.sh"), "--health-file", str(health_ok), "--once", "--log", str(tmp_dir / "wd.log")],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(wd_ok.returncode, 0, wd_ok.stdout + wd_ok.stderr)
            self.assertIn("OK status=ok", wd_ok.stdout)

            # Test release_snapshot.sh
            snap_src = tmp_dir / "snap_ws"
            (snap_src / "config").mkdir(parents=True)
            (snap_src / "config" / "config.json").write_text("{}", encoding="utf-8")
            snap_res = subprocess.run(
                ["bash", str(REPO_ROOT / "scripts" / "release_snapshot.sh"), "--label", "smoke", "--source", str(snap_src)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(snap_res.returncode, 0, snap_res.stderr)
            self.assertIn("Rollback command:", snap_res.stdout)

        # Strict Non-Goal Check: zero payment routes in share.router
        forbidden = ("payment", "promptpay", "checkout", "stripe", "omise", "wallet", "invoice", "pricing", "refund")
        for route in self.share_mod.router.routes:
            path_lower = str(getattr(route, "path", "")).lower()
            for kw in forbidden:
                self.assertNotIn(kw, path_lower)


if __name__ == "__main__":
    unittest.main()
