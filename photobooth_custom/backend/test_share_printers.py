import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from photobooth.routers.api import share


class PrinterRoutingTests(unittest.TestCase):
    def setUp(self):
        self.profiles = {
            "active_profile_id": "brother",
            "profiles": [
                {"id": "brother", "printer_name": "Brother_DCP_T830DW", "paper_size": "4x6.Fullbleed", "copies": 1},
                {"id": "hp", "printer_name": "HP_Ink_Tank_Wireless_410_series", "paper_size": "4x6.Fullbleed", "copies": 1},
            ],
        }

    def test_thai_stopped_printer_is_not_marked_ready(self):
        self.assertTrue(share._printer_is_available("เครื่องพิมพ์ Brother_DCP_T830DW อยู่นิ่ง เปิดใช้งาน"))
        self.assertFalse(share._printer_is_available("เครื่องพิมพ์ HP_Ink_Tank_Wireless_410_series ไม่ได้ทำงาน"))

    def test_queue_contains_jobs_from_both_printers_without_claiming_stopped_job_is_printing(self):
        def cups(command, **_kwargs):
            printer = command[-1]
            if command[0] == "lpstat":
                text = "เครื่องพิมพ์ Brother_DCP_T830DW กำลังพิมพ์" if printer.startswith("Brother") else "เครื่องพิมพ์ HP_Ink_Tank_Wireless_410_series ไม่ได้ทำงาน"
                return SimpleNamespace(returncode=0, stdout=text, stderr="")
            text = "ready\nrank owner job file size\nactive user 901 photo.jpg 1" if printer.startswith("Brother") else "ไม่พร้อมใช้งาน\nrank owner job file size\n1st user 902 old.jpg 1"
            return SimpleNamespace(returncode=0, stdout=text, stderr="")

        with patch.object(share, "_load_printer_profiles", return_value=self.profiles), \
             patch.object(share.container.mediacollection_service, "list_items", return_value=[]), \
             patch.object(share.subprocess, "run", side_effect=cups):
            result = share.api_get_print_queue()
        self.assertEqual(result["queue_count"], 2)
        self.assertEqual({job["job_id"] for job in result["queue"]}, {"Brother_DCP_T830DW-901", "HP_Ink_Tank_Wireless_410_series-902"})
        self.assertEqual(result["active_job"]["printer_name"], "Brother_DCP_T830DW")
        self.assertEqual([printer["online"] for printer in result["printers"]], [True, False])
        self.assertFalse(next(job for job in result["queue"] if job["printer_name"].startswith("HP"))["is_active"])

    def test_offline_printer_rejects_before_lp_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "photo.jpg"
            image.write_bytes(b"test")
            with patch.object(share.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="เครื่องพิมพ์ HP ไม่ได้ทำงาน", stderr="")) as run:
                with self.assertRaises(HTTPException) as raised:
                    share._send_to_printer(image, self.profiles["profiles"][1], 1)
            self.assertEqual(raised.exception.status_code, 503)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][0], "lpstat")

    def test_epson_ppd_paper_mapping_and_brother_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            ppd_dir = Path(directory)
            (ppd_dir / "EPSON_L18050_Series.ppd").write_text("*PageSize A4/A4: \"\"\n*PageSize EPKG/4x6: \"\"\n*PageSize EPKG.NMgn/4x6 Borderless: \"\"\n", encoding="utf-8")
            (ppd_dir / "Brother_DCP_T520W.ppd").write_text("*PageSize 4x6.Fullbleed/4x6: \"\"\n*PageSize A4/A4: \"\"\n", encoding="utf-8")
            with patch.object(share, "CUPS_PPD_DIR", ppd_dir):
                for paper in ("4x6.Fullbleed", "EPKG.NMgn"):
                    opts = share._cups_paper_options("EPSON_L18050_Series", paper)
                    self.assertIn("media=EPKG.NMgn", opts)
                    self.assertIn("PageSize=EPKG.NMgn", opts)
                    self.assertIn("EPIJ_Size=74", opts)
                    self.assertIn("EPIJ_PSrc=3", opts)
                    self.assertIn("EPIJ_Bdls=1", opts)
                    self.assertIn("EPIJ_Medi=145", opts)
                    self.assertIn("fit-to-page", opts)
                self.assertEqual(
                    share._cups_paper_options("Brother_DCP_T520W", "4x6.Fullbleed"),
                    ["-o", "media=4x6.Fullbleed", "-o", "fit-to-page"],
                )

    def test_send_to_printer_supports_bulk_copies_up_to_99_and_epson_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_dir = Path(directory)
            (tmp_dir / "EPSON_L18050_Series.ppd").write_text("*PageSize EPKG.NMgn/4x6 Borderless: \"\"\n", encoding="utf-8")
            image = tmp_dir / "photo.jpg"
            image.write_bytes(b"test")
            profile = {"id": "epson", "printer_name": "EPSON_L18050_Series", "paper_size": "EPKG.NMgn", "copies": 1}

            def fake_cups(command, **_kwargs):
                if command[0] == "lpstat":
                    return SimpleNamespace(returncode=0, stdout="เครื่องพิมพ์ EPSON_L18050_Series อยู่นิ่ง", stderr="")
                return SimpleNamespace(returncode=0, stdout="request id is EPSON_L18050_Series-301 (1 file(s))", stderr="")

            with patch.object(share, "CUPS_PPD_DIR", tmp_dir), patch.object(share.subprocess, "run", side_effect=fake_cups) as run:
                share._send_to_printer(image, profile, 15)
                lp_cmd_15 = run.call_args_list[-1].args[0]
                n_idx = lp_cmd_15.index("-n")
                self.assertEqual(lp_cmd_15[n_idx + 1], "15")
                self.assertIn("PageSize=EPKG.NMgn", lp_cmd_15)
                self.assertIn("EPIJ_Bdls=1", lp_cmd_15)

                share._send_to_printer(image, profile, 120)
                lp_cmd_99 = run.call_args_list[-1].args[0]
                n_idx_99 = lp_cmd_99.index("-n")
                self.assertEqual(lp_cmd_99[n_idx_99 + 1], "99")

    def test_hardware_printers_auto_registers_new_cups_printer(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_dir = Path(directory)
            (tmp_dir / "EPSON_L18050_Series.ppd").write_text("*PageSize EPKG.NMgn/4x6 Borderless: \"\"\n", encoding="utf-8")
            printer_store = tmp_dir / "photobooth_printers.json"
            printer_store.write_text(json.dumps({
                "profiles": [{"id": "brother-t520w-4x6", "name": "Brother DCP-T520W", "printer_name": "Brother_DCP_T520W", "paper_size": "4x6.Fullbleed", "copies": 1}],
                "active_profile_id": "brother-t520w-4x6",
            }), encoding="utf-8")

            def fake_cups(command, **_kwargs):
                if command == ["lpstat", "-d"]:
                    return SimpleNamespace(returncode=0, stdout="ปลายทางค่าเริ่มต้นของระบบ: EPSON_L18050_Series\n", stderr="")
                return SimpleNamespace(
                    returncode=0,
                    stdout="เครื่องพิมพ์ Brother_DCP_T520W อยู่นิ่ง เปิดใช้งาน\nเครื่องพิมพ์ EPSON_L18050_Series อยู่นิ่ง เปิดใช้งาน\n",
                    stderr="",
                )

            with patch.object(share, "PRINTER_STORE", printer_store), \
                 patch.object(share, "CUPS_PPD_DIR", tmp_dir), \
                 patch.object(share.subprocess, "run", side_effect=fake_cups):
                res = share.api_hardware_printers()

            epson_profile = next((p for p in res["profiles"] if p["printer_name"] == "EPSON_L18050_Series"), None)
            self.assertIsNotNone(epson_profile)
            self.assertEqual(epson_profile["paper_size"], "EPKG.NMgn")
            saved = json.loads(printer_store.read_text(encoding="utf-8"))
            self.assertTrue(any(p["printer_name"] == "EPSON_L18050_Series" for p in saved["profiles"]))

    def test_print_queue_serves_stale_cache_without_blocking_and_preflight_reuses_it(self):
        fake_now = [1000.0]
        hold_bg = [False]
        bg_started = threading.Event()
        bg_finish = threading.Event()

        def fake_cups(command, **_kwargs):
            printer = command[-1]
            if command[0] == "lpstat":
                if hold_bg[0]:
                    bg_started.set()
                    bg_finish.wait(timeout=2.0)
                return SimpleNamespace(returncode=0, stdout=f"เครื่องพิมพ์ {printer} อยู่นิ่ง เปิดใช้งาน", stderr="")
            return SimpleNamespace(returncode=0, stdout="ready\nno entries", stderr="")

        with patch.object(share, "_load_printer_profiles", return_value=self.profiles), \
             patch.object(share.container.mediacollection_service, "list_items", return_value=[]), \
             patch.object(share.subprocess, "run", side_effect=fake_cups) as mock_run, \
             patch.object(share.time, "monotonic", side_effect=lambda: fake_now[0]), \
             patch.object(share, "_require_localhost", return_value=("admin", None)), \
             patch.object(share, "_check_database_ok", return_value=(True, "ok")), \
             patch.object(share.platform_store, "quick_check", return_value=(True, "ok")), \
             patch.object(share, "_is_offline_mode", return_value=True):
            share._invalidate_print_caches()
            # 1) Cold call computes snapshot once
            res1 = share.api_get_print_queue()
            self.assertEqual(len(res1["printers"]), 2)
            calls_after_cold = mock_run.call_count
            self.assertEqual(calls_after_cold, 4)

            # 2) Within 5s TTL + preflight within 10s reuse cached state with 0 extra CUPS calls
            fake_now[0] = 1002.0
            res2 = share.api_get_print_queue()
            self.assertEqual(res2["printers"], res1["printers"])
            preflight = share.api_preflight(SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={}))
            printer_check = next(c for c in preflight["checks"] if c["id"] == "printers")
            self.assertEqual(printer_check["status"], "ok")
            self.assertIn("Brother_DCP_T830DW", printer_check["detail"])
            self.assertEqual(mock_run.call_count, calls_after_cold)

            # 3) Between 5s and 30s -> returns stale snapshot immediately without waiting for slow CUPS bg thread
            hold_bg[0] = True
            fake_now[0] = 1008.0
            res3 = share.api_get_print_queue()
            self.assertEqual(res3["printers"], res1["printers"])
            self.assertTrue(bg_started.wait(timeout=1.5))
            bg_finish.set()
            deadline = time.time() + 1.5
            while share._print_queue_refreshing and time.time() < deadline:
                time.sleep(0.01)
            share._invalidate_print_caches()

    def test_camera_health_does_not_call_usb_autodetect(self):
        running_backend = SimpleNamespace(is_running=lambda: True, is_started=lambda: True, __str__=lambda self: "Gphoto2Backend")
        stopped_backend = SimpleNamespace(is_running=lambda: False, is_started=lambda: True, __str__=lambda self: "Gphoto2Backend")

        with patch.object(share, "_detect_canon_cameras", side_effect=AssertionError("must not poll USB autodetect")):
            with patch.object(share.container.acquisition_service, "_backends", [running_backend]):
                healthy = share.api_camera_health()
            self.assertTrue(healthy["connected"])
            self.assertTrue(healthy["detected"][0]["supported"])

            with patch.object(share.container.acquisition_service, "_backends", [stopped_backend]), \
                 patch.object(share, "_camera_cache", (time.monotonic(), [{"model": "Canon EOS", "port": "usb:1", "supported": True}])):
                offline = share.api_camera_health()
            self.assertFalse(offline["connected"])
            self.assertEqual(offline["detected"][0]["model"], "Canon EOS")

    def test_template_asset_uses_versioned_url_and_long_cache_header(self):
        with tempfile.TemporaryDirectory() as directory:
            bg = Path(directory) / "frame-bg.png"
            bg.write_bytes(b"\x89PNG\r\n\x1a\n")
            tpl = {
                "id": "custom-cache-test",
                "name": "Cache Test",
                "canvas_width": 1200,
                "canvas_height": 1800,
                "slots": [{"x": 10, "y": 10, "width": 100, "height": 100}],
                "background_path": str(bg),
                "overlay_path": None,
                "builtin": False,
            }
            with patch.object(share, "_active_template_id", return_value="custom-cache-test"):
                pub = share._public_template(tpl)
            expected_v = bg.stat().st_mtime_ns
            self.assertEqual(pub["background_url"], f"/api/share/templates/custom-cache-test/asset/background?v={expected_v}")
            self.assertIsNone(pub["overlay_url"])

            with patch.object(share, "_load_templates", return_value=[tpl]):
                resp = share.api_template_asset("custom-cache-test", "background")
            self.assertEqual(resp.headers.get("Cache-Control"), "public, max-age=86400")

    def test_detect_local_lan_ip_caches_ifconfig(self):
        fake_ifconfig = SimpleNamespace(returncode=0, stdout="en0: flags=8863<UP>\n\tstatus: active\n\tinet 172.28.126.131 netmask 0xfffff800\n", stderr="")
        share._lan_ip_cache = None
        fake_now = [2000.0]
        with patch.object(share.subprocess, "run", return_value=fake_ifconfig) as mock_run, \
             patch.object(share.time, "monotonic", side_effect=lambda: fake_now[0]):
            first = share._detect_local_lan_ip()
            second = share._detect_local_lan_ip()
            self.assertEqual(first, "172.28.126.131")
            self.assertEqual(second, "172.28.126.131")
            self.assertEqual(mock_run.call_count, 1)
            fake_now[0] = 2020.0
            third = share._detect_local_lan_ip()
            self.assertEqual(third, "172.28.126.131")
            self.assertEqual(mock_run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
