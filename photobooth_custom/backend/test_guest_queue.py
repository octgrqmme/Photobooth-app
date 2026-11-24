import tempfile
import unittest
from pathlib import Path

from guest_queue import GuestQueueStore, cups_job_id


class GuestQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "guest-queue.json"
        self.store = GuestQueueStore(self.path)

    def test_issue_start_capture_and_persist(self):
        first = self.store.create()
        second = self.store.create()
        self.assertEqual((first["code"], second["code"]), ("SCI01", "SCI02"))
        self.assertEqual(self.store.start("SCI02")["status"], "shooting")
        self.assertEqual(self.store.attach_job("job-2"), "SCI02")
        self.store.set_media("job-2", ["collage-2"])
        self.assertEqual(self.store.code_for_job("job-2"), "SCI02")
        self.assertEqual(self.store.snapshot()["active_code"], None)
        self.assertEqual(GuestQueueStore(self.path).snapshot()["entries"][1]["latest_image_id"], "collage-2")

    def test_cancel_waiting_keeps_number_and_cannot_start_again(self):
        self.store.create()
        canceled = self.store.cancel_unshot("SCI01")
        self.assertEqual(canceled["status"], "canceled")
        self.assertIsNotNone(canceled["canceled_at"])
        self.assertEqual(self.store.create()["code"], "SCI02")
        with self.assertRaises(ValueError):
            self.store.start("SCI01")
        self.assertEqual(GuestQueueStore(self.path).snapshot()["entries"][0]["status"], "canceled")

    def test_cannot_cancel_queue_after_capture_starts(self):
        self.store.create()
        self.store.start("SCI01")
        self.store.attach_job("job-1")
        with self.assertRaises(ValueError):
            self.store.cancel_unshot("SCI01", allow_active=True)
        self.assertEqual(self.store.snapshot()["active_code"], "SCI01")

    def test_cancel_active_queue_before_first_capture(self):
        self.store.create()
        self.store.start("SCI01")
        canceled = self.store.cancel_unshot("SCI01", allow_active=True)
        self.assertEqual(canceled["status"], "canceled")
        self.assertIsNone(self.store.snapshot()["active_code"])
        self.assertEqual(self.store.create()["code"], "SCI02")

    def test_skip_next_waiting_moves_same_ticket_to_end(self):
        for _ in range(3):
            self.store.create()
        self.store.call("SCI01")
        skipped = self.store.skip_waiting("SCI01")
        self.assertEqual(skipped["code"], "SCI01")
        self.assertEqual(skipped["status"], "waiting")
        self.assertEqual(skipped["skipped_count"], 1)
        snapshot = self.store.snapshot()
        self.assertEqual([entry["code"] for entry in snapshot["entries"]], ["SCI02", "SCI03", "SCI01"])
        self.assertIsNone(snapshot["call_event"])
        self.assertEqual(self.store.start("SCI02")["status"], "shooting")
        self.assertEqual(GuestQueueStore(self.path).snapshot()["entries"][-1]["code"], "SCI01")

    def test_skip_rejects_non_next_active_and_only_waiting_ticket(self):
        self.assertIsNone(self.store.skip_waiting("SCI99"))
        self.store.create()
        with self.assertRaises(ValueError):
            self.store.skip_waiting("SCI01")
        self.store.create()
        with self.assertRaises(ValueError):
            self.store.skip_waiting("SCI02")
        self.store.start("SCI01")
        with self.assertRaises(ValueError):
            self.store.skip_waiting("SCI01")
        self.store.create()
        self.assertEqual(self.store.skip_waiting("SCI02")["code"], "SCI02")

    def test_guest_can_select_template_only_for_active_queue_before_first_shot(self):
        self.store.create()
        with self.assertRaises(ValueError):
            self.store.select_template("SCI01", "classic-two-up")
        self.store.start("SCI01")
        chosen = self.store.select_template("SCI01", "classic-two-up")
        self.assertEqual(chosen["template_id"], "classic-two-up")
        self.assertEqual(GuestQueueStore(self.path).snapshot()["entries"][0]["template_id"], "classic-two-up")
        self.store.attach_job("job-1")
        with self.assertRaises(ValueError):
            self.store.select_template("SCI01", "theater")
        self.assertIsNone(self.store.select_template("SCI99", "theater"))

    def test_start_discards_legacy_waiting_template_choice(self):
        self.store.create()
        data = self.store._read()
        data["entries"][0]["template_id"] = "theater"
        self.store._write(data)
        self.assertIsNone(self.store.start("SCI01")["template_id"])

    def test_template_activation_failure_does_not_save_choice(self):
        self.store.create()
        self.store.start("SCI01")
        def fail():
            raise ValueError("cannot activate")
        with self.assertRaises(ValueError):
            self.store.select_template("SCI01", "theater", fail)
        self.assertIsNone(self.store.snapshot()["entries"][0]["template_id"])

    def test_printer_state_only_completes_on_cups_report(self):
        self.store.create()
        self.store.start("SCI01")
        self.store.attach_job("job-1")
        self.store.complete_job("job-1")
        self.store.set_media("job-1", ["collage-1"])
        self.store.record_print("job-1", "collage-1", "Printer-42", 2, "Printer")
        self.store.update_print_states({"Printer-42"}, set())
        self.assertEqual(self.store.snapshot()["entries"][0]["status"], "printing")
        self.store.update_print_states(set(), {"Printer-42"})
        entry = self.store.snapshot()["entries"][0]
        self.assertEqual(entry["status"], "printed")
        self.assertEqual(entry["print_jobs"][0]["state"], "printed")

    def test_cancelled_job_returns_ready(self):
        self.store.create()
        self.store.start("SCI01")
        self.store.attach_job("job-1")
        self.store.set_media("job-1", ["collage-1"])
        self.store.record_print("job-1", "collage-1", "Printer-42", 1, "Printer")
        self.store.cancel_print("Printer-42")
        self.assertEqual(self.store.snapshot()["entries"][0]["status"], "ready")

    def test_cups_request_id(self):
        self.assertEqual(cups_job_id("request id is Brother_DCP-42 (1 file(s))"), "Brother_DCP-42")

    def test_categories_people_and_new_round_keep_old_report(self):
        self.store.add_category("SMO", "ผู้นำกิจกรรม")
        first = self.store.create("SMO", 3)
        self.store.start(first["code"])
        self.store.attach_job("job-smo")
        self.store.set_media("job-smo", ["photo-smo"])
        self.assertEqual(self.store.report()["rounds"][0]["summary"]["people_served"], 3)
        self.store.reset_round("โครงการถัดไป")
        self.assertEqual(self.store.create("SMO", 2)["code"], "SMO01")
        self.assertEqual(self.store.code_for_job("job-smo"), "SMO01")
        report = self.store.report()
        self.assertEqual(report["rounds"][1]["summary"]["people_served"], 3)
        self.assertEqual(report["rounds"][0]["summary"]["people_served"], 0)

    def test_reset_refuses_pending_queue(self):
        self.store.create()
        with self.assertRaises(ValueError):
            self.store.reset_round("รอบใหม่")
        self.store.cancel_unshot("SCI01")
        self.assertEqual(self.store.reset_round("รอบใหม่")["name"], "รอบใหม่")

    def test_manual_call_can_repeat_without_advancing_queue(self):
        self.store.create()
        first = self.store.call("SCI01")
        repeated = self.store.call("SCI01")
        self.assertNotEqual(first["id"], repeated["id"])
        self.assertEqual(self.store.snapshot()["call_event"], repeated)
        self.assertEqual(self.store.snapshot()["entries"][0]["status"], "waiting")
        self.store.start("SCI01")
        self.assertEqual(self.store.call("SCI01")["code"], "SCI01")

    def test_call_rejects_missing_or_canceled_number_and_resets_with_round(self):
        self.assertIsNone(self.store.call("SCI99"))
        self.store.create()
        self.store.call("SCI01")
        self.store.cancel_unshot("SCI01")
        self.assertIsNone(self.store.snapshot()["call_event"])
        with self.assertRaises(ValueError):
            self.store.call("SCI01")
        self.store.reset_round("งานใหม่")
        self.assertIsNone(self.store.snapshot()["call_event"])

    def test_manual_complete_updates_stats_and_allows_round_reset(self):
        self.store.create("SCI", 4)
        self.store.create("SCI", 2)
        self.store.start("SCI01")
        self.store.call("SCI01")

        completed_first = self.store.complete("SCI01")
        self.assertEqual(completed_first["status"], "ready")
        self.assertIsNotNone(completed_first["completed_at"])
        snapshot = self.store.snapshot()
        self.assertIsNone(snapshot["active_code"])
        self.assertIsNone(snapshot["call_event"])

        # Waiting queue can also be marked done directly without start()
        completed_second = self.store.complete("SCI02")
        self.assertEqual(completed_second["status"], "ready")
        self.assertIsNotNone(completed_second["completed_at"])

        summary = self.store.report()["rounds"][0]["summary"]
        self.assertEqual(summary["completed_queues"], 2)
        self.assertEqual(summary["people_served"], 6)
        self.assertEqual(self.store.reset_round("รอบบ่าย")["name"], "รอบบ่าย")

        self.assertIsNone(self.store.complete("SCI99"))
        self.store.create()
        self.store.complete("SCI01")
        with self.assertRaises(ValueError):
            self.store.complete("SCI01")
        self.store.create()
        self.store.cancel_unshot("SCI02")
        with self.assertRaises(ValueError):
            self.store.complete("SCI02")

    def test_advance_completes_active_and_starts_next_waiting(self):
        with self.assertRaises(ValueError):
            self.store.advance()

        self.store.create("SCI", 2)
        self.store.create("SCI", 3)

        first_step = self.store.advance()
        self.assertIsNone(first_step["completed_code"])
        self.assertEqual(first_step["active_code"], "SCI01")
        self.assertEqual(first_step["active_entry"]["status"], "shooting")

        second_step = self.store.advance()
        self.assertEqual(second_step["completed_code"], "SCI01")
        self.assertEqual(second_step["active_code"], "SCI02")

        third_step = self.store.advance()
        self.assertEqual(third_step["completed_code"], "SCI02")
        self.assertIsNone(third_step["active_code"])
        self.assertIsNone(third_step["active_entry"])

        summary = self.store.report()["rounds"][0]["summary"]
        self.assertEqual(summary["completed_queues"], 2)
        self.assertEqual(summary["people_served"], 5)


if __name__ == "__main__":
    unittest.main()
