import base64
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from photobooth.routers.api import share


class DigitalExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.item_id = UUID("11111111-1111-1111-1111-111111111111")
        self.image_path = Path(self.temp.name) / "print-4x6.jpg"
        source = Image.new("RGB", (1200, 1800), "red")
        source.paste(Image.new("RGB", (600, 1800), "blue"), (600, 0))
        source.save(self.image_path, "JPEG", quality=95)
        self.app = FastAPI()
        self.app.include_router(share.router, prefix="/api")
        self.client = TestClient(self.app, client=("127.0.0.1", 50000))

    def _item(self, config):
        return SimpleNamespace(id=self.item_id, media_type="collage", processed=self.image_path, pipeline_config=config)

    def test_two_up_download_crops_left_without_touching_print_file(self):
        item = self._item({"print_layout": "two_up"})
        original = self.image_path.read_bytes()
        with patch.object(share, "_get_item_or_404", return_value=item):
            response = self.client.get(f"/api/share/download-image/{self.item_id}")
            page = self.client.get(f"/api/share/download-page/{self.item_id}")
        self.assertEqual(response.status_code, 200)
        with Image.open(BytesIO(response.content)) as downloaded:
            self.assertEqual(downloaded.size, (600, 1800))
            self.assertGreater(downloaded.getpixel((300, 900))[0], 200)
        self.assertIn("2×6", page.text)
        self.assertEqual(self.image_path.read_bytes(), original)

    def test_full_layout_download_preserves_whole_photo(self):
        item = self._item({"print_layout": "full"})
        with patch.object(share, "_get_item_or_404", return_value=item):
            response = self.client.get(f"/api/share/download-image/{self.item_id}")
        self.assertEqual(response.status_code, 200)
        with Image.open(BytesIO(response.content)) as downloaded:
            self.assertEqual(downloaded.size, (1200, 1800))

    def test_old_six_slot_print_is_recognized(self):
        left = [{"pos_x": 35, "pos_y": y, "width": 530, "height": 366} for y in (382, 782, 1181)]
        right = [{**slot, "pos_x": slot["pos_x"] + 601} for slot in left]
        self.assertTrue(share._is_two_up_item(self._item({"canvas_width": 1200, "canvas_height": 1800, "merge_definition": left + right})))
        interleaved = [left[0], right[0], left[1], right[1], left[2], right[2]]
        self.assertFalse(share._is_two_up_item(self._item({"canvas_width": 1200, "canvas_height": 1800, "merge_definition": interleaved})))
        independently_positioned = [
            {**left[0], "pos_x": 714, "pos_y": 400, "width": 450, "height": 300},
            {**left[1], "pos_x": 690, "pos_y": 760, "width": 500, "height": 340},
            {**left[2], "pos_x": 710, "pos_y": 1200, "width": 480, "height": 360},
        ]
        self.assertTrue(share._is_two_up_item(self._item({"canvas_width": 1200, "canvas_height": 1800, "merge_definition": left + independently_positioned})))

    def test_template_metadata_describes_two_up_and_landscape(self):
        templates = {item["id"]: share._public_template(item) for item in share._default_templates()}
        self.assertEqual(templates["classic-two-up"]["capture_count"], 3)
        self.assertEqual(templates["classic-two-up"]["print_layout"], "two_up")
        self.assertEqual(templates["classic-single"]["print_layout"], "full")
        self.assertEqual((templates["classic-landscape"]["canvas_width"], templates["classic-landscape"]["canvas_height"]), (1800, 1200))

    def test_two_by_six_uploaded_frame_is_duplicated_for_print_sheet(self):
        frame = Image.new("RGBA", (600, 1800), (20, 180, 60, 255))
        data = BytesIO()
        frame.save(data, "PNG")
        output = Path(self.temp.name) / "frame.png"
        share._write_template_asset("data:image/png;base64," + base64.b64encode(data.getvalue()).decode(), output, 1200, 1800, "two_up")
        with Image.open(output) as sheet:
            self.assertEqual(sheet.size, (1200, 1800))
            self.assertEqual(sheet.getpixel((100, 100)), sheet.getpixel((700, 100)))
        with self.assertRaises(HTTPException):
            share._write_template_asset("data:image/png;base64," + base64.b64encode(data.getvalue()).decode(), output, 1800, 1200, "full")

    def test_two_up_uses_three_captures_not_six(self):
        job = SimpleNamespace(
            _configuration_set=SimpleNamespace(processing=SimpleNamespace(merge_definition=[object()] * 6)),
            _get_number_of_captures_from_merge_definition=lambda definitions: len(definitions),
        )
        with patch.object(share, "_find_template", side_effect=lambda template_id: next(item for item in share._default_templates() if item["id"] == template_id)):
            with patch.object(share, "_active_template_id", return_value="classic-two-up"):
                self.assertEqual(share._template_capture_count(job), 3)
            with patch.object(share, "_active_template_id", return_value="classic-six"):
                self.assertEqual(share._template_capture_count(job), 6)

    def test_two_up_print_sheet_repeats_the_three_slots_on_right(self):
        processing = SimpleNamespace()
        fake_config = SimpleNamespace(actions=SimpleNamespace(collage=[SimpleNamespace(processing=processing)]))
        with patch.object(share, "appconfig", fake_config):
            share._apply_template(next(item for item in share._default_templates() if item["id"] == "classic-two-up"))
        self.assertEqual((processing.canvas_width, processing.canvas_height), (1200, 1800))
        self.assertEqual(len(processing.merge_definition), 6)
        for left, right in zip(processing.merge_definition[:3], processing.merge_definition[3:]):
            self.assertEqual(right.pos_x - left.pos_x, 600)
            self.assertEqual((right.pos_y, right.width, right.height), (left.pos_y, left.width, left.height))

    def test_two_up_can_position_right_slots_independently(self):
        template_dir = Path(self.temp.name) / "templates"
        payload = {
            "id": "independent-sides", "name": "Different margins", "print_layout": "two_up",
            "slots": [{"x": 70, "y": 200, "width": 430, "height": 300}],
            "right_slots": [{"x": 635, "y": 205, "width": 440, "height": 295}],
        }
        with patch.object(share, "TEMPLATE_DIR", template_dir), patch.object(share, "TEMPLATE_STORE", template_dir / "templates.json"), patch.object(share, "ACTIVE_TEMPLATE_STORE", template_dir / "active-template.txt"):
            created = self.client.post("/api/share/templates", json=payload)
            self.assertEqual(created.status_code, 201)
            self.assertEqual(created.json()["right_slots"], payload["right_slots"])
            loaded = self.client.get("/api/share/templates").json()["templates"]
            template = next(item for item in loaded if item["id"] == "independent-sides")
            self.assertEqual(template["right_slots"], payload["right_slots"])
            processing = SimpleNamespace()
            fake_config = SimpleNamespace(actions=SimpleNamespace(collage=[SimpleNamespace(processing=processing)]))
            with patch.object(share, "appconfig", fake_config):
                share._apply_template(share._find_template("independent-sides"))
            self.assertEqual([slot.pos_x for slot in processing.merge_definition], [70, 635])
            self.assertEqual([slot.pos_y for slot in processing.merge_definition], [200, 205])
            self.assertEqual(self.client.post("/api/share/templates", json={**payload, "right_slots": [{"x": 550, "y": 200, "width": 100, "height": 100}]}).status_code, 422)

    def test_custom_template_accepts_two_up_and_landscape_full_layouts(self):
        template_dir = Path(self.temp.name) / "templates"
        with patch.object(share, "TEMPLATE_DIR", template_dir), patch.object(share, "TEMPLATE_STORE", template_dir / "templates.json"), patch.object(share, "ACTIVE_TEMPLATE_STORE", template_dir / "active-template.txt"):
            strip = self.client.post("/api/share/templates", json={
                "id": "test-strip", "name": "Two-up", "canvas_width": 1200, "canvas_height": 1800,
                "print_layout": "two_up", "slots": [{"x": 35, "y": 250, "width": 530, "height": 350}],
            })
            landscape = self.client.post("/api/share/templates", json={
                "id": "test-landscape", "name": "Landscape", "canvas_width": 1800, "canvas_height": 1200,
                "print_layout": "full", "slots": [{"x": 100, "y": 100, "width": 1600, "height": 1000}],
            })
            invalid = self.client.post("/api/share/templates", json={
                "id": "bad-strip", "name": "Bad", "canvas_width": 1800, "canvas_height": 1200,
                "print_layout": "two_up", "slots": [{"x": 35, "y": 250, "width": 530, "height": 350}],
            })
            listing = self.client.get("/api/share/templates").json()
        self.assertEqual(strip.status_code, 201)
        self.assertEqual(strip.json()["download_format"], "2×6")
        self.assertEqual(landscape.status_code, 201)
        self.assertEqual((landscape.json()["canvas_width"], landscape.json()["canvas_height"]), (1800, 1200))
        self.assertEqual(invalid.status_code, 422)
        self.assertIn("chooser_url", listing)

    def test_builtin_template_can_be_hidden_and_restored_without_deleting_source(self):
        template_dir = Path(self.temp.name) / "templates"
        idle_queue = SimpleNamespace(snapshot=lambda: {"entries": [], "active_code": None})
        with patch.object(share, "TEMPLATE_DIR", template_dir), patch.object(share, "TEMPLATE_STORE", template_dir / "templates.json"), patch.object(share, "ACTIVE_TEMPLATE_STORE", template_dir / "active-template.txt"), patch.object(share, "guest_queue_store", idle_queue), patch.object(share.container.processing_service, "is_occupied", return_value=False), patch.object(share.container.processing_service, "_workflow_jobmodel", None):
            hidden = self.client.delete("/api/share/templates/cinema")
            self.assertEqual(hidden.status_code, 200)
            listing = self.client.get("/api/share/templates").json()
            self.assertNotIn("cinema", [item["id"] for item in listing["templates"]])
            self.assertIn("cinema", [item["id"] for item in listing["hidden_builtin_templates"]])
            restored = self.client.post("/api/share/templates/cinema/restore")
            self.assertEqual(restored.status_code, 200)
            self.assertIn("cinema", [item["id"] for item in self.client.get("/api/share/templates").json()["templates"]])
            self.assertEqual(self.client.post("/api/share/templates/cinema/restore").status_code, 404)

    def test_edit_and_delete_custom_template_respect_active_capture(self):
        template_dir = Path(self.temp.name) / "templates"
        queue_state = {"entries": [], "active_code": None}
        queue_store = SimpleNamespace(snapshot=lambda: queue_state)
        payload = {"id": "custom-test", "name": "Original", "slots": [{"x": 10, "y": 10, "width": 400, "height": 400}]}
        with patch.object(share, "TEMPLATE_DIR", template_dir), patch.object(share, "TEMPLATE_STORE", template_dir / "templates.json"), patch.object(share, "ACTIVE_TEMPLATE_STORE", template_dir / "active-template.txt"), patch.object(share, "guest_queue_store", queue_store), patch.object(share.container.processing_service, "is_occupied", return_value=False), patch.object(share.container.processing_service, "_workflow_jobmodel", None):
            self.assertEqual(self.client.post("/api/share/templates", json=payload).status_code, 201)
            (template_dir / "active-template.txt").write_text("custom-test", encoding="utf-8")

            # Standalone active queue does not block editing an active template when camera is idle
            queue_state["active_code"] = "SCI01"
            self.assertEqual(self.client.post("/api/share/templates", json={**payload, "name": "Changed"}).status_code, 201)

            # Active camera capture blocks both edit and delete
            with patch.object(share.container.processing_service, "is_occupied", return_value=True):
                self.assertEqual(self.client.post("/api/share/templates", json={**payload, "name": "Blocked"}).status_code, 409)
                self.assertEqual(self.client.delete("/api/share/templates/custom-test").status_code, 409)

            with patch.object(share.container.processing_service, "_workflow_jobmodel", SimpleNamespace(_job_identifier="j1")):
                self.assertEqual(self.client.delete("/api/share/templates/custom-test").status_code, 409)

            queue_state["active_code"] = None
            with patch.object(share, "_activate_template"):
                self.assertEqual(self.client.delete("/api/share/templates/custom-test").status_code, 200)
            self.assertNotIn("custom-test", [item["id"] for item in self.client.get("/api/share/templates").json()["templates"]])

    def test_edit_custom_template_can_remove_uploaded_background(self):
        template_dir = Path(self.temp.name) / "templates"
        picture = BytesIO()
        Image.new("RGB", (1200, 1800), "gold").save(picture, "PNG")
        image_data = "data:image/png;base64," + base64.b64encode(picture.getvalue()).decode("ascii")
        payload = {"id": "custom-frame", "name": "With background", "slots": [{"x": 10, "y": 10, "width": 400, "height": 400}], "background_data": image_data}
        idle_queue = SimpleNamespace(snapshot=lambda: {"entries": [], "active_code": None})
        with patch.object(share, "TEMPLATE_DIR", template_dir), patch.object(share, "TEMPLATE_STORE", template_dir / "templates.json"), patch.object(share, "ACTIVE_TEMPLATE_STORE", template_dir / "active-template.txt"), patch.object(share, "guest_queue_store", idle_queue):
            created = self.client.post("/api/share/templates", json=payload)
            self.assertEqual(created.status_code, 201)
            asset = template_dir / "custom-frame-background.png"
            self.assertTrue(asset.exists())
            edited = self.client.post("/api/share/templates", json={**payload, "name": "Without background", "background_data": None, "remove_background": True})
            self.assertEqual(edited.status_code, 201)
            self.assertEqual(edited.json()["name"], "Without background")
            self.assertIsNone(edited.json()["background_url"])
            self.assertFalse(asset.exists())


if __name__ == "__main__":
    unittest.main()
