"""Persistent guest-number queue shared by the camera and print workflows."""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

logger = logging.getLogger(__name__)

ALLOWED_QUEUE_TRANSITIONS: dict[str, set[str]] = {
    "waiting": {"shooting", "ready", "canceled"},
    "shooting": {"waiting", "ready", "printing", "canceled"},
    "ready": {"shooting", "printing"},
    "printing": {"ready", "printed", "shooting"},
    "printed": {"printing", "shooting"},
    "canceled": set(),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_entry_status(entry: dict, new_status: str) -> None:
    current = str(entry.get("status") or "waiting")
    if current == new_status:
        return
    allowed = ALLOWED_QUEUE_TRANSITIONS.get(current)
    if allowed is None or new_status not in allowed:
        raise ValueError(f"ไม่สามารถเปลี่ยนสถานะคิวจาก {current} เป็น {new_status} ได้")
    entry["status"] = new_status


class GuestQueueStore:
    def __init__(self, path: Path, platform_store=None):
        self.path = path
        self.platform_store = platform_store
        self._lock = threading.RLock()

    @staticmethod
    def _default_data() -> dict:
        return {
            "next_number": 1,
            "next_numbers": {"SCI": 1},
            "categories": [{"prefix": "SCI", "name": "นักศึกษา"}],
            "round_id": uuid4().hex,
            "round_name": datetime.now().strftime("%Y-%m-%d"),
            "active_code": None,
            "entries": [],
            "history": [],
            "call_event": None,
        }

    @staticmethod
    def _normalize_data(data: dict) -> dict:
        if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
            raise ValueError("Invalid guest queue schema")
        data.setdefault("categories", [{"prefix": "SCI", "name": "นักศึกษา"}])
        data.setdefault("next_numbers", {"SCI": int(data.get("next_number", 1))})
        data.setdefault("round_id", "legacy")
        data.setdefault("round_name", "รอบเดิม")
        data.setdefault("history", [])
        data.setdefault("call_event", None)
        return data

    def _read_legacy_json(self) -> dict:
        if not self.path.exists():
            return self._default_data()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return self._normalize_data(data)
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
            backup = self.path.with_name(f"{self.path.name}.{datetime.now().strftime('%Y%m%d%H%M%S')}.corrupt")
            try:
                if self.path.is_file() and not backup.exists():
                    shutil.copy2(self.path, backup)
            except OSError:
                pass
            logger.exception("Could not read guest queue from %s: %s", self.path, exc)
            raise RuntimeError("ไม่สามารถอ่านไฟล์ข้อมูลคิวได้") from exc

    def _read(self) -> dict:
        if self.platform_store is not None:
            stored = self.platform_store.load_kv("guest_queue")
            if stored is not None:
                return self._normalize_data(stored)
            if self.path.exists():
                migrated = self._read_legacy_json()
                self.platform_store.save_kv("guest_queue", migrated)
                logger.info("Migrated legacy guest queue JSON (%s) into SQLite WAL store", self.path)
                return migrated
            return self._default_data()
        return self._read_legacy_json()

    def _write(self, data: dict) -> None:
        if self.platform_store is not None:
            self.platform_store.save_kv("guest_queue", data)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.{uuid4().hex}.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def snapshot(self) -> dict:
        with self._lock:
            data = deepcopy(self._read())
            data["media_queues"] = {
                media_id: {"code": entry["code"], "round_name": round_name, "people_count": entry.get("people_count", 1)}
                for round_name, entries in [(data["round_name"], data["entries"])] + [(item["name"], item.get("entries", [])) for item in data.get("history", [])]
                for entry in entries for media_id in entry.get("media_ids", [])
            }
            data.pop("history", None)
            return data

    def project_for_job(self, job_id: str) -> str | None:
        with self._lock:
            return next((item.get("project_id") for item in self._all_entries(self._read()) if job_id in item.get("job_ids", [])), None)

    def pending_print_media_ids(self) -> set[str]:
        with self._lock:
            return {job["media_id"] for entry in self._all_entries(self._read()) for job in entry.get("print_jobs", []) if job.get("state") in {"queued", "printing", "unknown"} and job.get("media_id")}

    def create(self, prefix: str = "SCI", people_count: int = 1, project_id: str | None = None) -> dict:
        with self._lock:
            data = self._read()
            prefix = prefix.upper().strip()
            if prefix not in {category["prefix"] for category in data["categories"]}:
                raise ValueError("ไม่พบประเภทคิว")
            if not 1 <= people_count <= 999:
                raise ValueError("จำนวนคนต่อคิวต้องอยู่ระหว่าง 1–999")
            number = max(1, int(data["next_numbers"].get(prefix, 1)))
            existing = {entry.get("code") for entry in data["entries"]}
            code = f"{prefix}{number:02d}"
            while code in existing:
                number += 1
                code = f"{prefix}{number:02d}"
            entry = {
                "code": code,
                "prefix": prefix,
                "people_count": people_count,
                "round_id": data["round_id"],
                "project_id": project_id,
                "status": "waiting",
                "created_at": _now(),
                "started_at": None,
                "completed_at": None,
                "canceled_at": None,
                "job_ids": [],
                "media_job_ids": [],
                "closed_job_ids": [],
                "media_ids": [],
                "latest_image_id": None,
                "template_id": None,
                "print_jobs": [],
            }
            data["entries"].append(entry)
            data["next_numbers"][prefix] = number + 1
            data["next_number"] = data["next_numbers"].get("SCI", 1)
            self._write(data)
            return deepcopy(entry)

    def add_category(self, prefix: str, name: str) -> dict:
        prefix = prefix.upper().strip()
        name = name.strip()
        if not re.fullmatch(r"[A-Z]{2,8}", prefix) or not 1 <= len(name) <= 50:
            raise ValueError("รหัสคิวต้องเป็นอักษรอังกฤษ 2–8 ตัว และชื่อไม่เกิน 50 ตัว")
        with self._lock:
            data = self._read()
            if any(item["prefix"] == prefix for item in data["categories"]):
                raise ValueError("มีรหัสคิวนี้แล้ว")
            category = {"prefix": prefix, "name": name}
            data["categories"].append(category)
            data["next_numbers"][prefix] = 1
            self._write(data)
            return deepcopy(category)

    @staticmethod
    def _summary(entries: list[dict], categories: list[dict]) -> dict:
        result = {item["prefix"]: {"prefix": item["prefix"], "name": item["name"], "issued_queues": 0, "canceled_queues": 0, "started_queues": 0, "completed_queues": 0, "people_served": 0} for item in categories}
        for entry in entries:
            prefix = entry.get("prefix") or re.sub(r"\d+$", "", entry.get("code", ""))
            group = result.setdefault(prefix, {"prefix": prefix, "name": prefix, "issued_queues": 0, "canceled_queues": 0, "started_queues": 0, "completed_queues": 0, "people_served": 0})
            group["issued_queues"] += 1
            if entry.get("status") == "canceled":
                group["canceled_queues"] += 1
            if entry.get("started_at") and entry.get("status") != "canceled":
                group["started_queues"] += 1
            if entry.get("status") != "canceled" and (
                entry.get("status") in {"ready", "printed"}
                or bool(entry.get("completed_at"))
                or bool(entry.get("media_ids"))
            ):
                group["completed_queues"] += 1
                group["people_served"] += max(1, int(entry.get("people_count") or 1))
        groups = list(result.values())
        return {"groups": groups, "issued_queues": sum(x["issued_queues"] for x in groups), "completed_queues": sum(x["completed_queues"] for x in groups), "people_served": sum(x["people_served"] for x in groups)}

    def report(self) -> dict:
        with self._lock:
            data = self._read()
            rounds = [{"id": data["round_id"], "name": data["round_name"], "current": True, "summary": self._summary(data["entries"], data["categories"])}]
            rounds.extend({"id": item["id"], "name": item["name"], "current": False, "closed_at": item.get("closed_at"), "summary": self._summary(item.get("entries", []), data["categories"])} for item in reversed(data["history"]))
            return {"categories": deepcopy(data["categories"]), "rounds": rounds}

    def reset_round(self, name: str) -> dict:
        name = name.strip()
        if not 1 <= len(name) <= 80:
            raise ValueError("กรุณาตั้งชื่อรอบงาน 1–80 ตัวอักษร")
        with self._lock:
            data = self._read()
            if data.get("active_code") or any(entry.get("status") in {"waiting", "shooting"} for entry in data["entries"]):
                raise ValueError("ยังมีคิวรอถ่ายหรือกำลังถ่าย กรุณาจบหรือยกเลิกก่อนเปิดรอบใหม่")
            if any(job.get("state") in {"queued", "printing", "unknown"} for entry in data["entries"] for job in entry.get("print_jobs", [])):
                raise ValueError("ยังมีงานพิมพ์ค้างอยู่ กรุณารอให้เสร็จก่อนเปิดรอบใหม่")
            data["history"].append({"id": data["round_id"], "name": data["round_name"], "closed_at": _now(), "entries": data["entries"]})
            data["round_id"] = uuid4().hex
            data["round_name"] = name
            data["entries"] = []
            data["active_code"] = None
            data["call_event"] = None
            data["next_numbers"] = {item["prefix"]: 1 for item in data["categories"]}
            data["next_number"] = 1
            self._write(data)
            return {"id": data["round_id"], "name": name}

    @staticmethod
    def _all_entries(data: dict):
        yield from data["entries"]
        for round_data in data.get("history", []):
            yield from round_data.get("entries", [])

    def select_template(self, code: str, template_id: str, activate: Callable[[], None] | None = None) -> dict | None:
        """Select and activate a frame only for the current queue before its first shot."""
        with self._lock:
            data = self._read()
            entry = next((item for item in data["entries"] if item.get("code") == code), None)
            if entry is None:
                return None
            if data.get("active_code") != code or entry.get("status") != "shooting":
                raise ValueError("เลือกเทมเพลตได้เฉพาะคิวที่กำลังถ่าย")
            if entry.get("job_ids") or entry.get("media_ids"):
                raise ValueError("เริ่มถ่ายภาพแล้ว ไม่สามารถเปลี่ยนเทมเพลตได้")
            if activate:
                activate()
            entry["template_id"] = template_id
            self._write(data)
            return deepcopy(entry)

    def cancel_unshot(self, code: str, allow_active: bool = False) -> dict | None:
        with self._lock:
            data = self._read()
            entry = next((item for item in data["entries"] if item.get("code") == code), None)
            if entry is None:
                return None
            is_active = data.get("active_code") == code
            waiting = entry.get("status") == "waiting" and not is_active
            active_before_capture = (
                allow_active
                and is_active
                and entry.get("status") == "shooting"
                and not entry.get("job_ids")
                and not entry.get("media_ids")
                and not entry.get("print_jobs")
            )
            if not waiting and not active_before_capture:
                raise ValueError("ยกเลิกได้เฉพาะคิวที่ยังไม่ถ่าย")
            _set_entry_status(entry, "canceled")
            entry["canceled_at"] = _now()
            if is_active:
                data["active_code"] = None
            if (data.get("call_event") or {}).get("code") == code:
                data["call_event"] = None
            self._write(data)
            return deepcopy(entry)

    def skip_waiting(self, code: str) -> dict | None:
        """Move the next unshot ticket to the back without changing its number."""
        with self._lock:
            data = self._read()
            entry = next((item for item in data["entries"] if item.get("code") == code), None)
            if entry is None:
                return None
            if entry.get("status") != "waiting" or data.get("active_code") == code:
                raise ValueError("ข้ามได้เฉพาะคิวที่ยังรอถ่าย")
            waiting = [item for item in data["entries"] if item.get("status") == "waiting"]
            if waiting[0] is not entry:
                raise ValueError("ข้ามได้เฉพาะคิวรอถ่ายลำดับถัดไป")
            if len(waiting) < 2:
                raise ValueError("ต้องมีคิวรอถ่ายอย่างน้อย 2 คิวจึงจะข้ามได้")
            data["entries"].remove(entry)
            data["entries"].append(entry)
            entry["skipped_count"] = int(entry.get("skipped_count") or 0) + 1
            entry["last_skipped_at"] = _now()
            if (data.get("call_event") or {}).get("code") == code:
                data["call_event"] = None
            self._write(data)
            return deepcopy(entry)

    def call(self, code: str) -> dict | None:
        """Record a manual announcement without starting or advancing the queue."""
        with self._lock:
            data = self._read()
            entry = next((item for item in data["entries"] if item.get("code") == code), None)
            if entry is None:
                return None
            if entry.get("status") not in {"waiting", "shooting"}:
                raise ValueError("เรียกเสียงได้เฉพาะคิวรอถ่ายหรือกำลังถ่าย")
            event = {"id": uuid4().hex, "code": code, "created_at": _now(), "round_id": data["round_id"]}
            data["call_event"] = event
            self._write(data)
            return deepcopy(event)

    def start(self, code: str, allow_replace: bool = True) -> dict | None:
        with self._lock:
            data = self._read()
            if not allow_replace and data.get("active_code") and data["active_code"] != code:
                raise ValueError("มีคิวกำลังถ่ายอยู่ กรุณาจบหรือยกเลิกคิวนั้นก่อน")
            entry = next((item for item in data["entries"] if item.get("code") == code), None)
            if entry is None:
                return None
            if entry.get("status") == "canceled":
                raise ValueError("คิวนี้ถูกยกเลิกแล้ว")
            previous = next((item for item in data["entries"] if item.get("code") == data.get("active_code")), None)
            if previous and previous is not entry and previous.get("status") == "shooting":
                _set_entry_status(previous, "ready" if previous.get("media_ids") else "waiting")
            data["active_code"] = code
            if entry["status"] == "waiting":
                # Ignore choices saved by older versions while the number was waiting.
                entry["template_id"] = None
            _set_entry_status(entry, "shooting")
            entry["started_at"] = _now()
            self._write(data)
            return deepcopy(entry)

    def complete(self, code: str) -> dict | None:
        """Mark an active or waiting queue as finished without binding camera media."""
        with self._lock:
            data = self._read()
            entry = next((item for item in data["entries"] if item.get("code") == code), None)
            if entry is None:
                return None
            if entry.get("status") == "canceled":
                raise ValueError("คิวนี้ถูกยกเลิกแล้ว")
            if entry.get("status") in {"ready", "printed"}:
                raise ValueError("คิวนี้เสร็จสิ้นแล้ว")
            _set_entry_status(entry, "ready")
            entry["completed_at"] = _now()
            if data.get("active_code") == code:
                data["active_code"] = None
            if (data.get("call_event") or {}).get("code") == code:
                data["call_event"] = None
            self._write(data)
            return deepcopy(entry)

    def advance(self) -> dict:
        """Complete the current active queue (if any) and activate the next waiting queue."""
        with self._lock:
            data = self._read()
            completed_code = None
            current_active = data.get("active_code")
            if current_active:
                active_entry = next((item for item in data["entries"] if item.get("code") == current_active), None)
                if active_entry is not None and active_entry.get("status") == "shooting":
                    _set_entry_status(active_entry, "ready")
                    active_entry["completed_at"] = _now()
                    completed_code = active_entry["code"]
                data["active_code"] = None
                if completed_code and (data.get("call_event") or {}).get("code") == completed_code:
                    data["call_event"] = None

            next_entry = next((item for item in data["entries"] if item.get("status") == "waiting"), None)
            if next_entry is not None:
                data["active_code"] = next_entry["code"]
                next_entry["template_id"] = None
                _set_entry_status(next_entry, "shooting")
                next_entry["started_at"] = _now()

            if not completed_code and next_entry is None:
                raise ValueError("ไม่มีคิวที่กำลังเรียกหรือคิวรอถัดไป")

            self._write(data)
            return {
                "completed_code": completed_code,
                "active_code": data["active_code"],
                "active_entry": deepcopy(next_entry) if next_entry is not None else None,
            }

    def attach_job(self, job_id: str) -> str | None:
        with self._lock:
            data = self._read()
            active = data.get("active_code")
            entry = next((item for item in data["entries"] if item.get("code") == active), None)
            if entry is None:
                return None
            if job_id not in entry["job_ids"]:
                entry["job_ids"].append(job_id)
                self._write(data)
            return entry["code"]

    def complete_job(self, job_id: str) -> None:
        with self._lock:
            data = self._read()
            entry = next((item for item in data["entries"] if job_id in item.get("job_ids", [])), None)
            if entry is None:
                return
            if entry["status"] == "shooting":
                _set_entry_status(entry, "ready")
            if data.get("active_code") == entry["code"]:
                data["active_code"] = None
            self._write(data)

    def release_incomplete_job(self, job_id: str) -> None:
        """Return an aborted/failed capture to the waiting queue."""
        with self._lock:
            data = self._read()
            entry = next((item for item in data["entries"] if job_id in item.get("job_ids", [])), None)
            if entry is None or entry.get("media_ids"):
                return
            if data.get("active_code") == entry["code"]:
                data["active_code"] = None
            _set_entry_status(entry, "waiting")
            if job_id not in entry.get("closed_job_ids", []):
                entry.setdefault("closed_job_ids", []).append(job_id)
            self._write(data)

    def set_media(self, job_id: str, collage_ids: list[str], all_ids: list[str] | None = None) -> None:
        if not collage_ids:
            return
        with self._lock:
            data = self._read()
            entry = next((item for item in data["entries"] if job_id in item.get("job_ids", [])), None)
            if entry is None:
                return
            changed = False
            for media_id in all_ids or collage_ids:
                if media_id not in entry["media_ids"]:
                    entry["media_ids"].append(media_id)
                    changed = True
            if job_id not in entry.get("media_job_ids", []):
                entry.setdefault("media_job_ids", []).append(job_id)
                changed = True
            if entry.get("latest_image_id") != collage_ids[0]:
                entry["latest_image_id"] = collage_ids[0]
                changed = True
            if entry["status"] == "shooting":
                _set_entry_status(entry, "ready")
                changed = True
            if data.get("active_code") == entry["code"]:
                data["active_code"] = None
                changed = True
            if changed:
                self._write(data)

    def code_for_job(self, job_id: str) -> str | None:
        with self._lock:
            return next((item["code"] for item in self._all_entries(self._read()) if job_id in item.get("job_ids", [])), None)

    def code_for_print_job(self, cups_id: str) -> str | None:
        with self._lock:
            return next((item["code"] for item in self._all_entries(self._read()) if any(print_job.get("cups_id") == cups_id for print_job in item.get("print_jobs", []))), None)

    def print_job_for_cups_id(self, cups_id: str) -> tuple[str, str | None] | None:
        with self._lock:
            for item in self._all_entries(self._read()):
                for print_job in item.get("print_jobs", []):
                    if print_job.get("cups_id") == cups_id:
                        return item["code"], print_job.get("media_id")
            return None

    def record_print(self, job_id: str, media_id: str, cups_id: str | None, copies: int, printer: str) -> str | None:
        with self._lock:
            data = self._read()
            entry = next((item for item in self._all_entries(data) if job_id in item.get("job_ids", [])), None)
            if entry is None:
                return None
            entry["print_jobs"].append({
                "id": uuid4().hex[:12],
                "cups_id": cups_id,
                "media_id": media_id,
                "copies": copies,
                "printer": printer,
                "state": "queued" if cups_id else "unknown",
                "submitted_at": _now(),
                "completed_at": None,
            })
            _set_entry_status(entry, "printing")
            self._write(data)
            return entry["code"]

    def unknown_print_jobs(self) -> list[dict]:
        with self._lock:
            result = []
            for entry in self._all_entries(self._read()):
                for job in entry.get("print_jobs", []):
                    if job.get("state") == "unknown":
                        result.append({
                            "queue_code": entry.get("code"),
                            "print_job_id": job.get("id"),
                            "cups_id": job.get("cups_id"),
                            "media_id": job.get("media_id"),
                            "printer": job.get("printer"),
                            "copies": job.get("copies", 1),
                            "submitted_at": job.get("submitted_at"),
                        })
            return result

    def resolve_unknown_print(
        self,
        queue_code: str,
        outcome: str,
        *,
        print_job_id: str | None = None,
        cups_id: str | None = None,
        reason: str = "",
    ) -> dict | None:
        if outcome not in {"printed", "canceled"}:
            raise ValueError("สถานะที่เลือกไม่ถูกต้อง")
        with self._lock:
            data = self._read()
            entry = next((item for item in self._all_entries(data) if item.get("code") == queue_code), None)
            if entry is None:
                return None
            print_jobs = entry.get("print_jobs", [])
            if print_job_id:
                target_job = next((job for job in print_jobs if job.get("id") == print_job_id), None)
            elif cups_id:
                target_job = next(
                    (job for job in print_jobs if job.get("cups_id") == cups_id and job.get("state") == "unknown"),
                    next((job for job in print_jobs if job.get("cups_id") == cups_id), None),
                )
            else:
                target_job = next((job for job in print_jobs if job.get("state") == "unknown"), None)
            if target_job is None or target_job.get("state") != "unknown":
                raise ValueError("ไม่พบงานพิมพ์สถานะไม่แน่ชัด (unknown) ที่ต้องการยืนยัน")
            now = _now()
            target_job["state"] = outcome
            target_job["operator_verified"] = outcome == "printed"
            target_job["resolved_at"] = now
            target_job["resolution_reason"] = reason.strip() or ("operator_confirmed" if outcome == "printed" else "operator_canceled")
            if outcome == "printed":
                target_job["completed_at"] = now
            _set_entry_status(entry, self._derive_entry_status(entry))
            self._write(data)
            return deepcopy(entry)

    @staticmethod
    def _derive_entry_status(entry: dict) -> str:
        states = [job["state"] for job in entry.get("print_jobs", [])]
        if any(state in ("queued", "printing", "unknown") for state in states):
            return "printing"
        if "printed" in states:
            return "printed"
        if states and entry.get("media_ids"):
            return "ready"
        return entry["status"]

    def update_print_states(self, pending: set[str] | None, completed: set[str] | None) -> None:
        if pending is None or completed is None:
            return
        with self._lock:
            data = self._read()
            changed = False
            for entry in self._all_entries(data):
                for print_job in entry.get("print_jobs", []):
                    if print_job["state"] in ("printed", "canceled"):
                        continue
                    cups_id = print_job.get("cups_id")
                    if not cups_id:
                        continue
                    if cups_id in completed:
                        state = "printed"
                        print_job["completed_at"] = _now()
                    elif cups_id in pending:
                        state = "printing"
                    else:
                        # A freshly submitted CUPS job can take a moment to
                        # appear in lpstat. Never claim that it was printed.
                        submitted = datetime.fromisoformat(print_job["submitted_at"])
                        state = "queued" if (datetime.now(timezone.utc) - submitted).total_seconds() < 15 else "unknown"
                    if print_job["state"] != state:
                        print_job["state"] = state
                        changed = True
                status = self._derive_entry_status(entry)
                if entry["status"] != status:
                    _set_entry_status(entry, status)
                    changed = True
            if changed:
                self._write(data)

    def cancel_print(self, cups_id: str) -> None:
        with self._lock:
            data = self._read()
            changed = False
            for entry in self._all_entries(data):
                for print_job in entry.get("print_jobs", []):
                    if print_job.get("cups_id") == cups_id and print_job["state"] != "printed":
                        print_job["state"] = "canceled"
                        changed = True
                status = self._derive_entry_status(entry)
                if entry["status"] != status:
                    _set_entry_status(entry, status)
                    changed = True
            if changed:
                self._write(data)

    def cancel_printer(self, printer: str) -> None:
        with self._lock:
            data = self._read()
            changed = False
            for entry in self._all_entries(data):
                for print_job in entry.get("print_jobs", []):
                    if print_job.get("printer") == printer and print_job["state"] not in ("printed", "canceled"):
                        print_job["state"] = "canceled"
                        changed = True
                status = self._derive_entry_status(entry)
                if entry["status"] != status:
                    _set_entry_status(entry, status)
                    changed = True
            if changed:
                self._write(data)


try:
    from .platform_store import platform_store as _default_platform_store
except ImportError:
    try:
        from platform_store import platform_store as _default_platform_store
    except ImportError:
        _default_platform_store = None

guest_queue_store = GuestQueueStore(Path("userdata/photobooth_guest_queue.json"), platform_store=_default_platform_store)


def cups_job_id(output: str) -> str | None:
    match = re.search(r"(?:request id is|หมายเลขคำขอคือ)\s+([A-Za-z0-9_.-]+)", output, flags=re.IGNORECASE)
    if not match:
        match = re.search(r"\b([A-Za-z0-9_.-]+-\d+)\b", output)
    return match.group(1) if match else None
