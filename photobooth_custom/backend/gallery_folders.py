"""Virtual photo projects. Media files stay in the Photobooth collection until deletion."""

from __future__ import annotations

import json
import logging
import shutil
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)


def folder_for_media(item, data: dict) -> str:
    assigned = data["assignments"].get(str(item.id))
    if assigned and any(project["id"] == assigned for project in data["projects"]):
        return assigned
    return "date-" + item.created_at.date().isoformat()


class GalleryFolderStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    def _read(self) -> dict:
        if not self.path.exists():
            return {"projects": [], "assignments": {}, "active_id": None}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("projects"), list) and isinstance(data.get("assignments"), dict):
                data.setdefault("active_id", None)
                return data
            raise ValueError("Invalid gallery folder schema")
        except (OSError, ValueError) as exc:
            backup = self.path.with_name(f"{self.path.name}.{datetime.now().strftime('%Y%m%d%H%M%S')}.corrupt")
            try:
                if self.path.is_file() and not backup.exists():
                    shutil.copy2(self.path, backup)
            except OSError:
                pass
            logger.exception("Could not read gallery folders from %s: %s", self.path, exc)
            raise RuntimeError("ไม่สามารถอ่านไฟล์โฟลเดอร์โครงการได้") from exc

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.{uuid4().hex}.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def snapshot(self) -> dict:
        with self._lock:
            return deepcopy(self._read())

    def create(self, name: str) -> dict:
        name = name.strip()
        if not 1 <= len(name) <= 80:
            raise ValueError("ชื่อโครงการต้องมี 1–80 ตัวอักษร")
        with self._lock:
            data = self._read()
            project = {"id": f"project-{uuid4().hex}", "name": name, "created_at": datetime.now(timezone.utc).isoformat()}
            data["projects"].append(project)
            data["active_id"] = project["id"]
            self._write(data)
            return deepcopy(project)

    def activate(self, folder_id: str | None) -> None:
        with self._lock:
            data = self._read()
            if folder_id is not None and not any(item["id"] == folder_id for item in data["projects"]):
                raise ValueError("ไม่พบโฟลเดอร์โครงการ")
            data["active_id"] = folder_id
            self._write(data)

    def assign(self, media_ids: list[str], folder_id: str | None = None) -> None:
        with self._lock:
            data = self._read()
            target = folder_id if folder_id is not None else data["active_id"]
            if target is None:
                return
            if not any(item["id"] == target for item in data["projects"]):
                raise ValueError("ไม่พบโฟลเดอร์โครงการ")
            changed = False
            for media_id in media_ids:
                if data["assignments"].get(media_id) != target:
                    data["assignments"][media_id] = target
                    changed = True
            if changed:
                self._write(data)

    def move(self, media_ids: list[str], folder_id: str | None) -> None:
        with self._lock:
            data = self._read()
            if folder_id is not None and not any(item["id"] == folder_id for item in data["projects"]):
                raise ValueError("ไม่พบโฟลเดอร์โครงการ")
            for media_id in media_ids:
                if folder_id is None:
                    data["assignments"].pop(media_id, None)
                else:
                    data["assignments"][media_id] = folder_id
            self._write(data)

    def remove_project(self, folder_id: str) -> None:
        with self._lock:
            data = self._read()
            if not any(item["id"] == folder_id for item in data["projects"]):
                raise ValueError("ไม่พบโฟลเดอร์โครงการ")
            data["projects"] = [item for item in data["projects"] if item["id"] != folder_id]
            data["assignments"] = {key: value for key, value in data["assignments"].items() if value != folder_id}
            if data["active_id"] == folder_id:
                data["active_id"] = None
            self._write(data)


gallery_folder_store = GalleryFolderStore(Path("userdata/photobooth_folders.json"))
