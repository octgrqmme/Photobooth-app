import base64
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from photobooth.routers.api import share
from photobooth.services.guest_queue import GuestQueueStore


class GuestQueueApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = GuestQueueStore(Path(self.temp.name) / "queue.json")
        app = FastAPI()
        app.include_router(share.router, prefix="/api")
        self.client = TestClient(app, client=("127.0.0.1", 50000))
        patcher = patch.object(share, "guest_queue_store", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(share, "_cups_jobs", return_value=set())
        patcher.start()
        self.addCleanup(patcher.stop)
        # Tests must not inherit the operator's hidden built-in template settings.
        patcher = patch.object(share, "_hidden_builtin_ids", return_value=set())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_issue_start_complete_and_advance_are_decoupled_from_camera(self):
        issued_1 = self.client.post("/api/share/guest-queue", json={"people_count": 2})
        issued_2 = self.client.post("/api/share/guest-queue", json={"people_count": 3})
        self.assertEqual(issued_1.status_code, 201)
        self.assertEqual(issued_1.json()["code"], "SCI01")
        self.assertEqual(issued_2.json()["code"], "SCI02")

        # Starting a standalone queue succeeds even when the camera is busy
        with patch.object(share.container.processing_service, "is_occupied", return_value=True):
            started = self.client.post("/api/share/guest-queue/SCI01/start")
        self.assertEqual(started.status_code, 200)

        # Polling guest-queue never attaches camera workflow jobs or auto-completes queues
        job_id = UUID("11111111-1111-1111-1111-111111111111")
        media = [SimpleNamespace(id=UUID("22222222-2222-2222-2222-222222222222"), media_type="collage")]
        with (
            patch.object(share.container.processing_service, "_workflow_jobmodel", SimpleNamespace(_job_identifier=job_id)),
            patch.object(share.container.mediacollection_service, "get_items_relto_job", return_value=media),
        ):
            running = self.client.get("/api/share/guest-queue").json()
        self.assertEqual(running["active_code"], "SCI01")
        self.assertEqual(running["entries"][0]["job_ids"], [])
        self.assertEqual(running["entries"][0]["status"], "shooting")

        # Manual complete updates stats and clears active_code
        completed = self.client.post("/api/share/guest-queue/SCI01/complete")
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["status"], "ready")
        self.assertIsNone(self.client.get("/api/share/guest-queue").json()["active_code"])
        report = self.client.get("/api/share/guest-queue/report").json()
        self.assertEqual(report["rounds"][0]["summary"]["completed_queues"], 1)
        self.assertEqual(report["rounds"][0]["summary"]["people_served"], 2)

        # Advance activates next waiting queue (SCI02), then finishes it cleanly
        adv_1 = self.client.post("/api/share/guest-queue/advance")
        self.assertEqual(adv_1.status_code, 200)
        self.assertEqual(adv_1.json()["active_code"], "SCI02")
        adv_2 = self.client.post("/api/share/guest-queue/advance")
        self.assertEqual(adv_2.status_code, 200)
        self.assertEqual(adv_2.json()["completed_code"], "SCI02")
        self.assertIsNone(adv_2.json()["active_code"])
        self.assertEqual(self.client.post("/api/share/guest-queue/advance").status_code, 409)

    def test_cancel_waiting_queue_without_reusing_its_number(self):
        self.assertEqual(self.client.post("/api/share/guest-queue").json()["code"], "SCI01")
        canceled = self.client.delete("/api/share/guest-queue/SCI01")
        self.assertEqual(canceled.status_code, 200)
        self.assertEqual(canceled.json()["status"], "canceled")
        self.assertEqual(self.client.delete("/api/share/guest-queue/SCI01").status_code, 409)
        self.assertEqual(self.client.post("/api/share/guest-queue/SCI01/start").status_code, 409)
        self.assertEqual(self.client.delete("/api/share/guest-queue/SCI99").status_code, 404)
        self.assertEqual(self.client.post("/api/share/guest-queue").json()["code"], "SCI02")

    def test_guest_device_cannot_issue_a_queue(self):
        remote = TestClient(self.client.app, client=("192.0.2.10", 50000))
        self.assertEqual(remote.post("/api/share/guest-queue").status_code, 403)
        self.assertEqual(self.store.snapshot()["entries"], [])

    def test_skip_queue_is_staff_only_and_keeps_number(self):
        self.client.post("/api/share/guest-queue")
        self.client.post("/api/share/guest-queue")
        remote = TestClient(self.client.app, client=("192.0.2.10", 50000))
        self.assertEqual(remote.post("/api/share/guest-queue/SCI01/skip").status_code, 403)
        self.assertEqual(self.client.post("/api/share/guest-queue/SCI99/skip").status_code, 404)
        self.assertEqual(self.client.post("/api/share/guest-queue/SCI02/skip").status_code, 409)
        skipped = self.client.post("/api/share/guest-queue/SCI01/skip")
        self.assertEqual(skipped.status_code, 200)
        self.assertEqual(skipped.json()["status"], "waiting")
        self.assertEqual([entry["code"] for entry in self.client.get("/api/share/guest-queue").json()["entries"]], ["SCI02", "SCI01"])
        self.assertEqual(self.client.post("/api/share/guest-queue").json()["code"], "SCI03")

    def test_call_queue_is_manual_event_without_status_change(self):
        self.client.post("/api/share/guest-queue")
        first = self.client.post("/api/share/guest-queue/SCI01/call")
        self.assertEqual(first.status_code, 200)
        second = self.client.post("/api/share/guest-queue/SCI01/call")
        self.assertNotEqual(first.json()["id"], second.json()["id"])
        snapshot = self.client.get("/api/share/guest-queue").json()
        self.assertEqual(snapshot["call_event"], second.json())
        self.assertEqual(snapshot["entries"][0]["status"], "waiting")
        self.assertIsNone(snapshot["active_code"])
        self.assertEqual(self.client.post("/api/share/guest-queue/SCI99/call").status_code, 404)
        self.client.delete("/api/share/guest-queue/SCI01")
        self.assertEqual(self.client.post("/api/share/guest-queue/SCI01/call").status_code, 409)

    def test_cancel_started_queue_even_when_camera_is_busy(self):
        self.client.post("/api/share/guest-queue")
        self.client.post("/api/share/guest-queue/SCI01/start")
        with patch.object(share.container.processing_service, "is_occupied", return_value=True):
            canceled = self.client.delete("/api/share/guest-queue/SCI01")
        self.assertEqual(canceled.status_code, 200)
        self.assertEqual(canceled.json()["status"], "canceled")
        self.assertIsNone(self.store.snapshot()["active_code"])

    def test_template_chooser_select_allows_lan_anytime_camera_idle(self):
        remote = TestClient(self.client.app, client=("192.0.2.10", 50000))
        with patch.object(share.container.processing_service, "is_occupied", return_value=False), patch.object(share.container.processing_service, "_workflow_jobmodel", None):
            listing = remote.get("/api/share/templates")
            self.assertEqual(listing.status_code, 200)
            self.assertFalse(listing.json()["capture_busy"])

        # LAN client can select a template with no active queue whenever camera is idle
        with patch.object(share, "_activate_template") as activate:
            selected = remote.post("/api/share/template-chooser/select", json={"template_id": "classic-two-up"})
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(selected.json()["active_template_id"], "classic-two-up")
        self.assertEqual(activate.call_args.args[0]["id"], "classic-two-up")

        # Unknown template returns 404
        self.assertEqual(remote.post("/api/share/template-chooser/select", json={"template_id": "missing"}).status_code, 404)

        # Blocked only while camera is busy or workflow is active
        with patch.object(share.container.processing_service, "is_occupied", return_value=True):
            self.assertEqual(remote.post("/api/share/template-chooser/select", json={"template_id": "theater"}).status_code, 409)
            self.assertTrue(remote.get("/api/share/templates").json()["capture_busy"])
        with patch.object(share.container.processing_service, "_workflow_jobmodel", SimpleNamespace(_job_identifier="j1")):
            self.assertEqual(remote.post("/api/share/template-chooser/select", json={"template_id": "theater"}).status_code, 409)
            self.assertTrue(remote.get("/api/share/templates").json()["capture_busy"])

        # Operator localhost /templates/{id}/activate succeeds even if a standalone queue is active
        self.client.post("/api/share/guest-queue")
        self.client.post("/api/share/guest-queue/SCI01/start")
        with patch.object(share, "_activate_template") as activate_local:
            op_res = self.client.post("/api/share/templates/theater/activate")
        self.assertEqual(op_res.status_code, 200)
        self.assertEqual(activate_local.call_args.args[0]["id"], "theater")

    def test_guest_template_choice_is_only_for_current_queue_before_first_shot(self):
        self.client.post("/api/share/guest-queue")
        self.client.post("/api/share/guest-queue")
        self.assertEqual(self.client.post("/api/share/guest-queue/SCI02/template", json={"template_id": "theater"}).status_code, 409)
        self.assertEqual(self.client.post("/api/share/guest-queue/SCI01/template", json={"template_id": "theater"}).status_code, 409)
        started = self.client.post("/api/share/guest-queue/SCI01/start")
        self.assertEqual(started.status_code, 200)
        with patch.object(share, "_activate_template") as activate:
            choice = self.client.post("/api/share/guest-queue/SCI01/template", json={"template_id": "classic-two-up"})
        self.assertEqual(choice.status_code, 200)
        self.assertEqual(choice.json()["template_id"], "classic-two-up")
        self.assertEqual(activate.call_args.args[0]["id"], "classic-two-up")
        self.assertEqual(self.client.post("/api/share/guest-queue/SCI01/template", json={"template_id": "missing"}).status_code, 404)
        self.store.attach_job("job-1")
        self.assertEqual(self.client.post("/api/share/guest-queue/SCI01/template", json={"template_id": "theater"}).status_code, 409)

    def test_ticket_airdrop_requires_local_mac_and_existing_queue(self):
        issued = self.client.post("/api/share/guest-queue").json()
        remote = TestClient(self.client.app, client=("192.0.2.10", 50000))
        self.assertEqual(remote.post(f"/api/share/guest-queue/{issued['code']}/ticket-airdrop", json={"image_data": "bad"}).status_code, 403)
        picture = BytesIO()
        Image.new("RGB", (900, 1500), "#301d2d").save(picture, format="PNG")
        payload = share.QueueTicketAirDropPayload(image_data="data:image/png;base64," + base64.b64encode(picture.getvalue()).decode("ascii"))
        local_request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
        with patch.object(share.sys, "platform", "darwin"), patch.object(share, "QUEUE_TICKET_DIR", Path(self.temp.name) / "tickets"), patch.object(share.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as opened:
            result = share.api_airdrop_guest_ticket(issued["code"], payload, local_request)
        self.assertTrue(result["ok"])
        self.assertTrue((Path(self.temp.name) / "tickets" / result["filename"]).is_file())
        self.assertEqual(opened.call_count, 2)

    def test_print_request_carries_guest_number_without_printing(self):
        self.store.create()
        self.store.start("SCI01")
        job_id = UUID("11111111-1111-1111-1111-111111111111")
        media_id = UUID("22222222-2222-2222-2222-222222222222")
        self.store.attach_job(str(job_id))
        item = SimpleNamespace(id=media_id, job_identifier=job_id, processed=Path(self.temp.name) / "photo.jpg")
        profile = {"printer_name": "Printer", "paper_size": "4x6"}
        with patch.object(share, "_get_item_or_404", return_value=item), patch.object(share, "_active_printer_profile", return_value=profile), patch.object(share, "_send_to_printer", return_value="request id is Printer-17 (1 file(s))") as send:
            response = self.client.post(f"/api/share/print/{media_id}", json={"copies": 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["queue_code"], "SCI01")
        self.assertEqual(response.json()["print_job_id"], "Printer-17")
        self.assertEqual(send.call_args.kwargs["title"], "SCI01 Photobooth")
        self.assertEqual(self.store.snapshot()["entries"][0]["print_jobs"][0]["copies"], 2)


if __name__ == "__main__":
    unittest.main()
