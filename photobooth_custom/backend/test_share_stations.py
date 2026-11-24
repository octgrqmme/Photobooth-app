import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from photobooth.routers.api import share
from photobooth.services.platform_store import PlatformStore


class StationTests(unittest.TestCase):
    def test_one_time_pairing_and_scoped_actions(self):
        app = FastAPI()
        app.include_router(share.router, prefix="/api")
        mac = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))
        desk = TestClient(app, client=("192.0.2.7", 50000))
        printer = TestClient(app, client=("192.0.2.8", 50000))
        base = "/api/share"
        action = {"X-PB-Station": "1"}
        media_id = uuid4()
        with tempfile.TemporaryDirectory() as tmp:
            temp_store = PlatformStore(Path(tmp) / "platform.sqlite")
            with (
                patch.object(share, "platform_store", temp_store),
                patch.object(share, "_qr_svg", return_value=Response("<svg/>")),
                patch.object(share, "_request_base_url", return_value="http://192.0.2.1:8000"),
                patch.object(share, "guest_queue_store") as queues,
                patch.object(share, "_get_item_or_404", return_value=SimpleNamespace(show_in_gallery=True, media_type="image", processed="/test/image.jpg", job_identifier=uuid4())),
                patch.object(share, "_active_printer_profile", return_value={"printer_name": "TestPrinter"}),
                patch.object(share, "_send_to_printer", return_value="request id is TestPrinter-42"),
                patch.object(share, "_audit"),
            ):
                queues.create.return_value = {"code": "SCI01"}
                queues.code_for_job.return_value = None
                self.assertEqual(desk.post(base + "/stations/tickets/invite", headers=action).status_code, 403)
                self.assertEqual(desk.post(base + "/guest-queue", json={}).status_code, 403)
                for role, client in (("tickets", desk), ("print", printer)):
                    invite = mac.post(f"{base}/stations/{role}/invite", headers=action)
                    self.assertEqual(invite.status_code, 200, invite.text)
                    code = invite.json()["url"].split("#pair=")[1]
                    self.assertEqual(client.post(f"{base}/stations/{role}/pair", json={"code": code}, headers=action).status_code, 200)
                    self.assertEqual(client.post(f"{base}/stations/{role}/pair", json={"code": code}, headers=action).status_code, 403)
                self.assertEqual(desk.post(base + "/guest-queue", json={}, headers=action).status_code, 201)
                self.assertEqual(printer.post(base + "/guest-queue", json={}, headers=action).status_code, 403)
                self.assertEqual(desk.post(f"{base}/print/{media_id}", json={}, headers=action).status_code, 403)
                self.assertEqual(printer.post(f"{base}/print/{media_id}", json={}).status_code, 403)
                printed = printer.post(f"{base}/print/{media_id}", json={}, headers=action)
                self.assertEqual(printed.status_code, 200, printed.text)
                self.assertEqual(printer.post(f"{base}/print/{media_id}", json={"copies": 5, "override_quota": True}, headers=action).status_code, 200)
                self.assertEqual(desk.post(base + "/guest-queue/reset", json={"name": "Another event"}, headers=action).status_code, 403)
                self.assertEqual(mac.post(base + "/stations/print/revoke", headers=action).status_code, 200)
                self.assertEqual(printer.post(f"{base}/print/{media_id}", json={}, headers=action).status_code, 403)

    def test_multiple_print_stations_persist_across_restarts_and_revoke(self):
        app = FastAPI()
        app.include_router(share.router, prefix="/api")
        mac = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))
        printer1 = TestClient(app, client=("192.0.2.8", 50000))
        printer2 = TestClient(app, client=("192.0.2.9", 50000))
        base = "/api/share"
        action = {"X-PB-Station": "1"}
        media_id = uuid4()
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "platform.sqlite"
            store1 = PlatformStore(db_path)
            with (
                patch.object(share, "platform_store", store1),
                patch.object(share, "_qr_svg", return_value=Response("<svg/>")),
                patch.object(share, "_request_base_url", return_value="http://192.0.2.1:8000"),
                patch.object(share, "guest_queue_store") as queues,
                patch.object(share, "_get_item_or_404", return_value=SimpleNamespace(show_in_gallery=True, media_type="image", processed="/test/image.jpg", job_identifier=uuid4())),
                patch.object(share, "_active_printer_profile", return_value={"printer_name": "TestPrinter"}),
                patch.object(share, "_send_to_printer", return_value="request id is TestPrinter-42"),
                patch.object(share, "_audit"),
            ):
                queues.code_for_job.return_value = None
                for client in (printer1, printer2):
                    invite = mac.post(f"{base}/stations/print/invite", headers=action)
                    self.assertEqual(invite.status_code, 200)
                    code = invite.json()["url"].split("#pair=")[1]
                    self.assertEqual(client.post(f"{base}/stations/print/pair", json={"code": code}, headers=action).status_code, 200)

                # Both paired print stations stay active simultaneously
                self.assertEqual(printer1.post(f"{base}/print/{media_id}", json={"copies": 3}, headers=action).status_code, 200)
                self.assertEqual(printer2.post(f"{base}/print/{media_id}", json={"copies": 2}, headers=action).status_code, 200)

            # Simulate server restart: fresh PlatformStore handle reading the persisted SQLite DB
            share._station_invites.clear()
            store2 = PlatformStore(db_path)
            with (
                patch.object(share, "platform_store", store2),
                patch.object(share, "guest_queue_store") as queues,
                patch.object(share, "_get_item_or_404", return_value=SimpleNamespace(show_in_gallery=True, media_type="image", processed="/test/image.jpg", job_identifier=uuid4())),
                patch.object(share, "_active_printer_profile", return_value={"printer_name": "TestPrinter"}),
                patch.object(share, "_send_to_printer", return_value="request id is TestPrinter-43"),
                patch.object(share, "_audit"),
            ):
                queues.code_for_job.return_value = None
                self.assertEqual(printer1.post(f"{base}/print/{media_id}", json={}, headers=action).status_code, 200)
                self.assertEqual(printer2.post(f"{base}/print/{media_id}", json={}, headers=action).status_code, 200)
                self.assertEqual(mac.post(f"{base}/stations/print/revoke", headers=action).status_code, 200)
                self.assertEqual(printer1.post(f"{base}/print/{media_id}", json={}, headers=action).status_code, 403)
                self.assertEqual(printer2.post(f"{base}/print/{media_id}", json={}, headers=action).status_code, 403)

    def test_detect_local_lan_ip_prefers_wired_and_supports_direct_cable_link_local(self):
        samples = [
            (
                "en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500\n"
                "\tinet 172.20.10.2 netmask 0xfffffff0 broadcast 172.20.10.15\n"
                "\tstatus: active\n"
                "en8: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500\n"
                "\tinet 192.168.50.10 netmask 0xffffff00 broadcast 192.168.50.255\n"
                "\tstatus: active\n",
                "192.168.50.10",
            ),
            (
                "en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500\n"
                "\tinet 172.20.10.2 netmask 0xfffffff0 broadcast 172.20.10.15\n"
                "\tstatus: active\n"
                "en8: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500\n"
                "\tstatus: inactive\n",
                "172.20.10.2",
            ),
            (
                "en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500\n"
                "\tstatus: inactive\n"
                "en8: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500\n"
                "\tinet 169.254.42.7 netmask 0xffff0000 broadcast 169.254.255.255\n"
                "\tstatus: active\n",
                "169.254.42.7",
            ),
            (
                "en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500\n"
                "\tstatus: inactive\n"
                "en8: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500\n"
                "\tstatus: inactive\n"
                "bridge0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500\n"
                "\tinet 169.254.88.9 netmask 0xffff0000 broadcast 169.254.255.255\n",
                "169.254.88.9",
            ),
        ]
        for ifconfig_out, expected_ip in samples:
            with patch.object(share.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=ifconfig_out, stderr="")):
                self.assertEqual(share._detect_local_lan_ip(), expected_ip)


if __name__ == "__main__":
    unittest.main()
