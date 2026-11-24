import asyncio
import base64
import binascii
import csv
import hashlib
import hmac
import html
import ipaddress
import json
import logging
import queue as queue_mod
import re
import secrets
import socket
import sys
import subprocess
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field
from sqlalchemy import delete as sa_delete, select, text
from sqlalchemy.orm import Session

from ... import PATH_PROCESSED, RECYCLE_PATH
from ...appconfig import appconfig
from ...container import container
from ...database.models import Mediaitem
from ...database.models import Cacheditem
from ...database.database import engine
from ...plugins import pm as pluggy_pm
from ...services.guest_queue import cups_job_id, guest_queue_store
from ...services.gallery_folders import folder_for_media, gallery_folder_store
from ...services.platform_store import event_bus, platform_store
from ...services.config.models.models import CollageMergeDefinition
from ...utils.exceptions import WrongMediaTypeError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/share", tags=["share"])


class TemplateComposePayload(BaseModel):
    image_data: str = Field(max_length=30_000_000)
    source_ids: list[UUID] = Field(min_length=1, max_length=6)
    template_id: str = Field(max_length=40)
    title: str = Field(default="", max_length=120)
    subtitle: str = Field(default="", max_length=180)


class TemplateSlotPayload(BaseModel):
    x: int = Field(ge=0, le=2400)
    y: int = Field(ge=0, le=3600)
    width: int = Field(ge=40, le=2400)
    height: int = Field(ge=40, le=3600)


class TemplateSavePayload(BaseModel):
    id: str | None = Field(default=None, max_length=50, pattern=r"^[a-z0-9-]+$")
    name: str = Field(min_length=1, max_length=80)
    canvas_width: int = Field(default=1200, ge=600, le=2400)
    canvas_height: int = Field(default=1800, ge=900, le=3600)
    slots: list[TemplateSlotPayload] = Field(min_length=1, max_length=6)
    right_slots: list[TemplateSlotPayload] | None = Field(default=None, min_length=1, max_length=3)
    print_layout: Literal["full", "two_up"] = "full"
    background_data: str | None = Field(default=None, max_length=30_000_000)
    overlay_data: str | None = Field(default=None, max_length=30_000_000)
    remove_background: bool = False
    remove_overlay: bool = False


class QueueTemplatePayload(BaseModel):
    template_id: str = Field(min_length=1, max_length=50)


class GuestQueueCreatePayload(BaseModel):
    prefix: str = Field(default="SCI", min_length=2, max_length=8)
    people_count: int = Field(default=1, ge=1, le=999)


class QueueCategoryPayload(BaseModel):
    prefix: str = Field(min_length=2, max_length=8)
    name: str = Field(min_length=1, max_length=50)


class QueueTicketAirDropPayload(BaseModel):
    image_data: str = Field(max_length=8_000_000)


class QueueResetPayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class GalleryFolderPayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class GalleryMovePayload(BaseModel):
    folder_id: str | None = None


class GalleryDeletePayload(BaseModel):
    confirm_name: str


class GalleryImportPayload(BaseModel):
    confirm_name: str
    expected_count: int = Field(ge=0)


class PrinterProfilePayload(BaseModel):
    id: str | None = Field(default=None, max_length=50, pattern=r"^[a-z0-9-]+$")
    name: str = Field(min_length=1, max_length=80)
    printer_name: str = Field(min_length=1, max_length=180, pattern=r"^[A-Za-z0-9_@][A-Za-z0-9_@.-]*$")
    paper_size: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    copies: int = Field(default=1, ge=1, le=99)


class PrintPayload(BaseModel):
    profile_id: str | None = Field(default=None, max_length=50)
    copies: int = Field(default=1, ge=1, le=99)
    override_quota: bool = False


class PrintJobResolvePayload(BaseModel):
    queue_code: str = Field(min_length=1, max_length=24)
    outcome: Literal["printed", "canceled"]
    print_job_id: str | None = Field(default=None, max_length=64)
    cups_id: str | None = Field(default=None, max_length=180)
    reason: str = Field(default="", max_length=200)


class EventCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    mode: Literal["event", "self_service", "kiosk"] = "event"
    activate: bool = False
    intake_open: bool = True
    policy: dict[str, Any] = Field(default_factory=dict)


class EventUpdatePayload(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    mode: Literal["event", "self_service", "kiosk"] | None = None
    intake_open: bool | None = None
    policy: dict[str, Any] | None = None


class EventClonePayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    activate: bool = False


class TemplateImportPayload(BaseModel):
    schema_version: int = Field(default=1, ge=1)
    manifest: dict[str, Any]
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-fA-F0-9]{64}$")


class KioskSessionCreatePayload(BaseModel):
    station_id: str = Field(default="kiosk-1", min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$")
    language: Literal["th", "en"] = "th"
    template_id: str | None = Field(default=None, max_length=50)


class KioskTemplateUpdatePayload(BaseModel):
    template_id: str = Field(min_length=1, max_length=120)


class KioskFinalizePayload(BaseModel):
    media_id: UUID | None = None
    template_id: str | None = Field(default=None, max_length=50)
    auto_print: bool = True
    copies: int = Field(default=1, ge=1, le=99)
    profile_id: str | None = Field(default=None, max_length=50)


class KioskUnlockPayload(BaseModel):
    pin: str = Field(min_length=1, max_length=64)


class ShareTokenCreatePayload(BaseModel):
    media_ids: list[UUID] = Field(min_length=1, max_length=20)
    session_code: str | None = Field(default=None, max_length=24)
    ttl_hours: int = Field(default=24, ge=1, le=720)


class RetentionPurgePayload(BaseModel):
    dry_run: bool = True
    retention_days: int | None = Field(default=None, ge=1, le=365)


class OfflineModePayload(BaseModel):
    offline_mode: bool


TEMPLATE_DIR = Path("userdata/photobooth_templates")
TEMPLATE_STORE = TEMPLATE_DIR / "templates.json"
ACTIVE_TEMPLATE_STORE = TEMPLATE_DIR / "active-template.txt"
PRINTER_STORE = Path("userdata/photobooth_printers.json")
CLOUD_STORE = Path("userdata/photobooth_cloud_shares.json")
CLOUD_ENV = Path("vercel_public/.env.local")
QUEUE_TICKET_DIR = Path("userdata/queue_tickets")
GIF_DIR = Path("userdata/gifs")
CONFIG_FILE = Path("config/config.json")
_store_lock = threading.RLock()
_gif_lock = threading.Lock()
_gif_jobs: dict[str, dict[str, Any]] = {}
_cloud_lock = threading.RLock()
_cloud_inflight: set[str] = set()
_approval_action_lock = threading.Lock()
_camera_detect_lock = threading.Lock()
_camera_cache: tuple[float, list[dict]] = (0.0, [])
_CAMERA_DETECT_CACHE_TTL_SEC = 12.0
_lan_ip_lock = threading.Lock()
_lan_ip_cache: tuple[float, int, str] | None = None
_LAN_IP_CACHE_TTL_SEC = 15.0
CUPS_PPD_DIR = Path("/etc/cups/ppd")
_ppd_pagesize_cache: dict[tuple[str, str, int], set[str]] = {}
_print_queue_lock = threading.Lock()
_print_queue_cache: tuple[float, tuple, dict[str, Any]] | None = None
_print_queue_refreshing = False
_print_queue_epoch = 0
_cups_jobs_lock = threading.Lock()
_cups_jobs_cache: dict[tuple[str, int], tuple[float, set[str] | None]] = {}
_CUPS_POLL_CACHE_TTL_SEC = 5.0
_CUPS_STALE_MAX_AGE_SEC = 30.0
_CUPS_CMD_TIMEOUT_SEC = 1.5


def _invalidate_print_caches() -> None:
    """Clear short-TTL CUPS polling caches after print queue or profile mutations."""
    global _print_queue_cache, _print_queue_epoch
    with _print_queue_lock:
        _print_queue_cache = None
        _print_queue_epoch += 1
    with _cups_jobs_lock:
        _cups_jobs_cache.clear()


def _ppd_supported_page_sizes(printer_name: str) -> set[str]:
    """Return PageSize keys declared in the macOS CUPS PPD for a printer."""
    if not re.fullmatch(r"[A-Za-z0-9_@.-]+", printer_name or ""):
        return set()
    ppd_path = CUPS_PPD_DIR / f"{printer_name}.ppd"
    try:
        mtime_ns = ppd_path.stat().st_mtime_ns
    except OSError:
        return set()
    cache_key = (str(CUPS_PPD_DIR), printer_name, mtime_ns)
    cached = _ppd_pagesize_cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        text = ppd_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    sizes = set(re.findall(r"^\*PageSize\s+([^\s/:]+)", text, flags=re.MULTILINE))
    _ppd_pagesize_cache[cache_key] = sizes
    return sizes


def _default_paper_size_for_printer(printer_name: str) -> str:
    """Pick the default 4x6 borderless paper identifier matching this printer's PPD."""
    supported = _ppd_supported_page_sizes(printer_name)
    if "EPKG.NMgn" in supported and "4x6.Fullbleed" not in supported:
        return "EPKG.NMgn"
    return "4x6.Fullbleed"


def _cups_paper_options(printer_name: str, paper_size: str) -> list[str]:
    """Translate profile paper sizes into driver-specific CUPS -o flags."""
    supported = _ppd_supported_page_sizes(printer_name)
    if "EPKG.NMgn" in supported and "4x6.Fullbleed" not in supported:
        if paper_size in {"4x6.Fullbleed", "EPKG.NMgn"}:
            return [
                "-o", "media=EPKG.NMgn",
                "-o", "PageSize=EPKG.NMgn",
                "-o", "EPIJ_Size=74",
                "-o", "EPIJ_PSrc=3",
                "-o", "EPIJ_Bdls=1",
                "-o", "EPIJ_Medi=145",
                "-o", "EPIJ_Qual=306",
                "-o", "EPIJ_Mode=3",
                "-o", "fit-to-page",
            ]
        if paper_size == "EPKG":
            return [
                "-o", "media=EPKG",
                "-o", "PageSize=EPKG",
                "-o", "EPIJ_Size=74",
                "-o", "EPIJ_PSrc=2",
                "-o", "EPIJ_Bdls=0",
                "-o", "EPIJ_Medi=145",
                "-o", "EPIJ_Qual=306",
                "-o", "EPIJ_Mode=3",
                "-o", "fit-to-page",
            ]
        if paper_size == "A4.NMgn" and "A4.NMgn" in supported:
            return [
                "-o", "media=A4.NMgn",
                "-o", "PageSize=A4.NMgn",
                "-o", "EPIJ_Size=1",
                "-o", "EPIJ_PSrc=3",
                "-o", "EPIJ_Bdls=1",
                "-o", "fit-to-page",
            ]
        if paper_size == "iso_a4_210x297mm" and "A4" in supported:
            return [
                "-o", "media=A4",
                "-o", "PageSize=A4",
                "-o", "fit-to-page",
            ]
    return ["-o", f"media={paper_size}", "-o", "fit-to-page"]


_LOCALHOST_HOSTS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
_RUNTIME_SETTINGS_NS = "runtime_settings"
_outbox_worker_lock = threading.Lock()
_outbox_worker_started = False


def _is_offline_mode() -> bool:
    """Return True when staff switched Photobooth into manual offline mode."""
    try:
        data = platform_store.load_kv(_RUNTIME_SETTINGS_NS)
    except Exception:
        return False
    return bool((data or {}).get("offline_mode", False))


def _set_offline_mode(enabled: bool) -> dict[str, Any]:
    """Persist runtime offline-mode toggle in the SQLite WAL key-value store."""
    payload = {
        "offline_mode": bool(enabled),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    platform_store.save_kv(_RUNTIME_SETTINGS_NS, payload)
    return payload
StationRole = Literal["tickets", "print"]
_station_lock = threading.Lock()
_station_invites: dict[StationRole, tuple[str, float]] = {}
_station_cookie = "pb_station"
_station_session_seconds = 12 * 60 * 60
_STATION_SESSIONS_NS = "station_sessions"


def _load_station_sessions_locked() -> dict[str, list[dict[str, Any]]]:
    now = time.time()
    result: dict[str, list[dict[str, Any]]] = {"tickets": [], "print": []}
    try:
        raw = platform_store.load_kv(_STATION_SESSIONS_NS)
    except Exception:
        return result
    if not isinstance(raw, dict):
        return result
    for role in ("tickets", "print"):
        entries = raw.get(role) or []
        if not isinstance(entries, list):
            continue
        cleaned = [
            {"digest": str(entry.get("digest") or ""), "expires_at": float(entry.get("expires_at") or 0)}
            for entry in entries
            if isinstance(entry, dict) and entry.get("digest") and float(entry.get("expires_at") or 0) > now
        ]
        result[role] = cleaned[-20:]
    return result


def _save_station_sessions_locked(sessions: dict[str, list[dict[str, Any]]]) -> None:
    try:
        platform_store.save_kv(_STATION_SESSIONS_NS, sessions)
    except Exception as exc:
        logger.warning("Could not persist station sessions: %s", exc)


class StationPairPayload(BaseModel):
    code: str = Field(pattern=r"^[A-Za-z0-9_-]{32}$")


def _station_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _station_header(request: Request) -> None:
    if request.headers.get("X-PB-Station") != "1":
        raise HTTPException(status_code=403, detail="เปิดหน้าจุดบริการเพื่อทำรายการ")


def _require_pairing_console(request: Request) -> None:
    _station_header(request)
    if not request.client or request.client.host not in _LOCALHOST_HOSTS or request.url.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="สร้างหรือยกเลิก QR จาก Mac เครื่องหลักเท่านั้น")


def _station_session_valid(request: Request, role: StationRole) -> bool:
    token = request.cookies.get(_station_cookie, "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32}", token):
        return False
    digest = _station_digest(token)
    with _station_lock:
        sessions = _load_station_sessions_locked()
        return any(
            hmac.compare_digest(str(entry.get("digest") or ""), digest)
            for entry in sessions.get(role, [])
        )


def _require_station_action(request: Request, role: StationRole, detail: str) -> tuple[str, str]:
    if request.headers.get("X-PB-Station") == "1" and _station_session_valid(request, role):
        return ("station-" + role, request.client.host if request.client else "")
    return _require_localhost(request, detail)


@router.post("/stations/{role}/invite")
def api_station_invite(role: StationRole, request: Request):
    _require_pairing_console(request)
    code = secrets.token_urlsafe(24)
    page = "ticket-desk.html" if role == "tickets" else "print-desk.html"
    url = f"{_request_base_url(request)}/{page}#pair={code}"
    qr = _qr_svg(url)
    with _station_lock:
        _station_invites[role] = (_station_digest(code), time.monotonic() + 300)
    return JSONResponse({"url": url, "qr": "data:image/svg+xml;base64," + base64.b64encode(qr.body).decode()}, headers={"Cache-Control": "no-store"})


@router.post("/stations/{role}/pair")
def api_station_pair(role: StationRole, payload: StationPairPayload, request: Request):
    _station_header(request)
    token = secrets.token_urlsafe(24)
    with _station_lock:
        stored = _station_invites.get(role)
        if not stored or stored[1] <= time.monotonic() or not hmac.compare_digest(stored[0], _station_digest(payload.code)):
            raise HTTPException(status_code=403, detail="QR ถูกใช้แล้วหรือหมดอายุ ขอ QR ใหม่จาก Mac เครื่องหลัก")
        _station_invites.pop(role)
        sessions = _load_station_sessions_locked()
        role_sessions = list(sessions.get(role, []))
        role_sessions.append({"digest": _station_digest(token), "expires_at": time.time() + _station_session_seconds})
        sessions[role] = role_sessions[-20:]
        _save_station_sessions_locked(sessions)
    response = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
    response.set_cookie(_station_cookie, token, max_age=_station_session_seconds, path="/api/share",
                        httponly=True, samesite="strict", secure=request.url.scheme == "https")
    return response


@router.get("/stations/{role}/session")
def api_station_session(role: StationRole, request: Request):
    local = bool(request.client and request.client.host in _LOCALHOST_HOSTS and request.url.hostname in {"localhost", "127.0.0.1", "::1"})
    return JSONResponse({"paired": local or _station_session_valid(request, role)}, headers={"Cache-Control": "no-store"})


@router.post("/stations/{role}/revoke")
def api_station_revoke(role: StationRole, request: Request):
    _require_pairing_console(request)
    with _station_lock:
        _station_invites.pop(role, None)
        sessions = _load_station_sessions_locked()
        sessions[role] = []
        _save_station_sessions_locked(sessions)
    return {"ok": True}


def _compute_admin_token() -> str | None:
    if not CONFIG_FILE.is_file():
        return None
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        admin_pw = str(((cfg.get("common") or {}).get("admin_password")) or "")
        secret = str(((cfg.get("misc") or {}).get("secret")) or "")
        if not admin_pw or admin_pw == "0000" or len(secret) < 32 or secret == "075fe918f335a1f06fd5b0a0224ca877":
            return None
        return hmac.new(secret.encode("utf-8"), f"admin:{admin_pw}".encode("utf-8"), hashlib.sha256).hexdigest()
    except (OSError, json.JSONDecodeError):
        return None


def _require_role(
    request: Request,
    role: Literal["operator", "admin"] = "operator",
    detail: str = "ทำรายการนี้ได้จากจอควบคุมบน Mac ผ่าน localhost เท่านั้น",
) -> tuple[str, str]:
    host = request.client.host if request.client else ""
    if host in _LOCALHOST_HOSTS:
        return (role, host)
    headers = getattr(request, "headers", None) or {}
    provided = headers.get("X-Admin-Token") or headers.get("x-admin-token") or ""
    expected = _compute_admin_token()
    if expected and provided and hmac.compare_digest(str(provided), expected):
        return ("admin", host or "lan")
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _require_localhost(request: Request, detail: str = "ทำรายการนี้ได้จากจอควบคุมบน Mac ผ่าน localhost เท่านั้น") -> tuple[str, str]:
    return _require_role(request, "operator", detail)


def _audit(
    request: Request | None,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    result: str = "ok",
    detail: dict | None = None,
    actor_role: str = "operator",
) -> None:
    host = request.client.host if request and request.client else "system"
    try:
        platform_store.record_audit(actor_role, host, action, entity_type, entity_id, result, detail)
        event_bus.notify("audit", {"action": action, "entity_type": entity_type, "entity_id": entity_id})
    except Exception as exc:
        logger.warning("Could not write audit event %s: %s", action, exc)


def _idempotency_key(request: Request) -> str | None:
    headers = getattr(request, "headers", None) or {}
    raw = headers.get("Idempotency-Key") or headers.get("idempotency-key")
    if raw is None or not str(raw).strip():
        return None
    key = str(raw).strip()
    if len(key) > 128 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", key):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Idempotency-Key ไม่ถูกต้อง")
    return key


def _get_item_or_404(item_id: UUID) -> Mediaitem:
    try:
        return container.mediacollection_service.get_item(item_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ไม่พบไฟล์ภาพที่ต้องการ") from exc


def _cloud_records() -> dict:
    try:
        data = json.loads(CLOUD_STORE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _save_cloud_records(records: dict) -> None:
    CLOUD_STORE.parent.mkdir(parents=True, exist_ok=True)
    temporary = CLOUD_STORE.with_name(f"{CLOUD_STORE.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    temporary.replace(CLOUD_STORE)


def _cloud_upload(item_id: str, image_path: Path, two_up: bool) -> None:
    temporary_path = None
    try:
        try:
            platform_store.mark_upload_uploading(item_id)
            event_bus.notify("outbox", {"media_id": item_id, "state": "uploading"})
        except Exception:
            pass
        upload_path = image_path
        if two_up:
            import tempfile
            with Image.open(image_path) as source:
                left_half = source.crop((0, 0, source.width // 2, source.height)).convert("RGB")
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as output:
                    temporary_path = Path(output.name)
                    left_half.save(output, format="JPEG", quality=94, optimize=True)
            upload_path = temporary_path
        result = subprocess.run(
            ["node", f"--env-file={CLOUD_ENV}", "vercel_public/scripts/upload-photo.mjs", str(upload_path), item_id],
            check=True, capture_output=True, text=True, timeout=25,
            env={**os.environ, "PHOTO_PUBLIC_ORIGIN": "https://sci-lywood-photobooth.vercel.app"},
        )
        published = json.loads(result.stdout)
        with _cloud_lock:
            records = _cloud_records()
            records[item_id] = {"status": "ready", "share_url": published["share_url"], "expires_at": published["expires_at"]}
            _save_cloud_records(records)
        try:
            platform_store.mark_upload_ready(item_id, published["share_url"], int(published["expires_at"]))
            event_bus.notify("outbox", {"media_id": item_id, "state": "ready"})
        except Exception:
            pass
    except Exception as exc:
        logger.warning("Cloud upload failed for %s: %s", item_id, exc)
        backoff_seconds = 30
        try:
            existing = next((item for item in platform_store.list_outbox(limit=200)["items"] if item["media_id"] == item_id), None)
            retries = existing["retry_count"] if existing else 0
            backoff_seconds = min(300, 30 * (2 ** min(retries, 4)))
            platform_store.mark_upload_failed(item_id, str(exc), backoff_seconds=backoff_seconds)
            event_bus.notify("outbox", {"media_id": item_id, "state": "failed"})
        except Exception:
            pass
        with _cloud_lock:
            records = _cloud_records()
            records[item_id] = {"status": "failed", "retry_at": time.time() + backoff_seconds}
            _save_cloud_records(records)
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)
        with _cloud_lock:
            _cloud_inflight.discard(item_id)


def _outbox_worker_loop() -> None:
    while True:
        time.sleep(10)
        if not CLOUD_ENV.exists() or _is_offline_mode():
            continue
        try:
            due = platform_store.claim_due_uploads(time.time(), limit=3)
        except Exception:
            continue
        for job in due:
            media_id = job["media_id"]
            image_path = Path(job["image_path"])
            if not image_path.is_file():
                try:
                    platform_store.mark_upload_failed(media_id, "ไม่พบไฟล์ภาพบน Mac", backoff_seconds=300)
                except Exception:
                    pass
                continue
            with _cloud_lock:
                if media_id in _cloud_inflight:
                    continue
                _cloud_inflight.add(media_id)
                records = _cloud_records()
                records[media_id] = {"status": "uploading"}
                _save_cloud_records(records)
            _cloud_upload(media_id, image_path, bool(job["two_up"]))


def _ensure_outbox_worker() -> None:
    global _outbox_worker_started
    if _outbox_worker_started:
        return
    with _outbox_worker_lock:
        if _outbox_worker_started:
            return
        _outbox_worker_started = True
        threading.Thread(target=_outbox_worker_loop, daemon=True, name="photobooth-cloud-outbox").start()


def _cloud_status(item_id: UUID, item: Mediaitem | None = None) -> dict | None:
    if _is_offline_mode():
        if CLOUD_ENV.exists():
            key = str(item_id)
            with _cloud_lock:
                record = _cloud_records().get(key)
            if record and record.get("status") == "ready" and time.time() < record.get("expires_at", 0):
                return record
        return {"status": "offline", "offline_mode": True}
    if not CLOUD_ENV.exists():
        return None
    key = str(item_id)
    with _cloud_lock:
        records = _cloud_records()
        record = records.get(key)
        if record and record.get("status") == "ready":
            if time.time() >= record.get("expires_at", 0):
                return {"status": "expired"}
            return record
        if record and key not in _cloud_inflight and time.time() >= record.get("retry_at", 0):
            item = item or _get_item_or_404(item_id)
            if item.media_type == "collage" and (item.pipeline_config or {}).get("custom_template"):
                two_up = _is_two_up_item(item)
                try:
                    platform_store.enqueue_upload(key, str(item.processed), two_up)
                except Exception:
                    pass
                _cloud_inflight.add(key)
                records[key] = {"status": "uploading"}
                _save_cloud_records(records)
                _ensure_outbox_worker()
                threading.Thread(target=_cloud_upload, args=(key, Path(item.processed), two_up), daemon=True).start()
                return records[key]
        return record


def _start_cloud_upload(item_id: UUID, item: Mediaitem) -> dict:
    if _is_offline_mode():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="ระบบอยู่ในโหมดออฟไลน์ กรุณาปิดโหมดออฟไลน์ก่อนสร้าง QR ออนไลน์")
    if not CLOUD_ENV.exists():
        raise HTTPException(status_code=503, detail="ยังไม่ได้ตั้งค่าการอัปโหลด QR ออนไลน์บน Mac")
    image_path = Path(item.processed)
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="ไม่พบไฟล์ภาพต้นฉบับบน Mac")
    key = str(item_id)
    two_up = _is_two_up_item(item)
    with _cloud_lock:
        current = _cloud_records()
        record = current.get(key)
        if record and record.get("status") == "ready" and time.time() < record.get("expires_at", 0):
            return record
        if key in _cloud_inflight:
            return {"status": "uploading"}
        try:
            platform_store.enqueue_upload(key, str(image_path), two_up)
            event_bus.notify("outbox", {"media_id": key, "state": "pending"})
        except Exception:
            pass
        _cloud_inflight.add(key)
        current[key] = {"status": "uploading"}
        _save_cloud_records(current)
        _ensure_outbox_worker()
        threading.Thread(target=_cloud_upload, args=(key, image_path, two_up), daemon=True).start()
        return current[key]


def _is_two_up_item(item: Mediaitem) -> bool:
    """Identify a two-copy print without changing the original print media."""
    if item.media_type != "collage":
        return False
    config = item.pipeline_config if isinstance(item.pipeline_config, dict) else {}
    if config.get("print_layout") in {"full", "two_up"}:
        return config["print_layout"] == "two_up"
    template_id = config.get("custom_template")
    if template_id:
        try:
            return _find_template(template_id).get("print_layout") == "two_up"
        except HTTPException:
            return False
    background = config.get("canvas_img_background_file")
    if background:
        matched = next((template for template in _load_templates() if template.get("background_path") == str(background)), None)
        if matched:
            return matched.get("print_layout") == "two_up"
    slots = config.get("merge_definition") or []
    width = config.get("canvas_width")
    height = config.get("canvas_height")
    if not isinstance(width, int) or not isinstance(height, int) or width * 3 != height * 2 or len(slots) != 6:
        return False
    half = width // 2
    try:
        for left, right in zip(slots[:3], slots[3:]):
            if not (left["pos_x"] + left["width"] <= half and right["pos_x"] >= half):
                return False
            if any(abs(left[key] - right[key]) > 100 for key in ("pos_y", "width", "height")):
                return False
            if abs(right["pos_x"] - left["pos_x"] - half) > 100:
                return False
    except (KeyError, TypeError, ValueError):
        return False
    return True


def _detect_local_lan_ip_uncached() -> str:
    """Detect best LAN/direct-cable IPv4 address: wired DHCP > Wi-Fi > wired link-local."""
    skip_prefixes = ("lo", "utun", "awdl", "llw", "gif", "stf", "anpi", "ap", "nan")
    candidates: list[tuple[int, str, str]] = []
    try:
        result = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=2)
        output = result.stdout or ""
    except (OSError, subprocess.SubprocessError):
        output = ""

    blocks: list[tuple[str, str, list[str]]] = []
    current_iface = ""
    current_header = ""
    current_lines: list[str] = []
    for line in output.splitlines():
        hdr = re.match(r"^([A-Za-z0-9_]+):(.*)$", line)
        if hdr:
            if current_iface:
                blocks.append((current_iface, current_header, current_lines))
            current_iface = hdr.group(1)
            current_header = hdr.group(2)
            current_lines = [line]
        elif current_iface:
            current_lines.append(line)
    if current_iface:
        blocks.append((current_iface, current_header, current_lines))

    for iface, header, lines in blocks:
        if iface.startswith(skip_prefixes):
            continue
        block_text = "\n".join(lines).lower()
        is_active = ("status: active" in block_text) or (
            iface.startswith("bridge") and "running" in header.lower() and "status: inactive" not in block_text
        )
        if not is_active:
            continue
        is_wired = iface != "en0"
        for line in lines:
            matched = re.match(r"^\s+inet\s+(\d+\.\d+\.\d+\.\d+)\b", line)
            if not matched:
                continue
            addr = matched.group(1)
            try:
                ip = ipaddress.IPv4Address(addr)
            except ValueError:
                continue
            if ip.is_loopback or ip.is_unspecified:
                continue
            if ip.is_private and not ip.is_link_local:
                priority = 0 if is_wired else 1
            elif is_wired and ip.is_link_local:
                priority = 2
            else:
                priority = 3
            candidates.append((priority, iface, addr))

    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[0][2]

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        probe.close()


def _detect_local_lan_ip() -> str:
    """Return cached LAN/direct-cable IPv4 address for 15s (keyed on subprocess.run)."""
    global _lan_ip_cache
    run_id = id(subprocess.run)
    now = time.monotonic()
    with _lan_ip_lock:
        if _lan_ip_cache is not None and _lan_ip_cache[1] == run_id and (now - _lan_ip_cache[0]) < _LAN_IP_CACHE_TTL_SEC:
            return _lan_ip_cache[2]
    detected = _detect_local_lan_ip_uncached()
    with _lan_ip_lock:
        _lan_ip_cache = (time.monotonic(), run_id, detected)
    return detected


def _request_base_url(request: Request) -> str:
    """Return a LAN-accessible URL when the kiosk is opened on localhost."""
    host = request.url.hostname or "localhost"
    if host in {"localhost", "127.0.0.1", "::1"}:
        host = _detect_local_lan_ip()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = request.url.port
    authority = f"{host}:{port}" if port else host
    return f"{request.url.scheme}://{authority}"


def _default_templates() -> list[dict]:
    return [
        {
            "id": "theater",
            "name": "ม่านโรงละคร",
            "canvas_width": 1200,
            "canvas_height": 1800,
            "slots": [
                {"x": 145, "y": 245, "width": 910, "height": 340},
                {"x": 145, "y": 650, "width": 910, "height": 340},
                {"x": 145, "y": 1055, "width": 910, "height": 340},
            ],
            "background_path": "photobooth_custom/frontend/assets/pb-template-theater.png",
            "overlay_path": None,
            "builtin": True,
        },
        {
            "id": "cinema",
            "name": "โรงหนังอาร์ตเดโค",
            "canvas_width": 1200,
            "canvas_height": 1800,
            "slots": [
                {"x": 125, "y": 245, "width": 950, "height": 340},
                {"x": 125, "y": 650, "width": 950, "height": 340},
                {"x": 125, "y": 1055, "width": 950, "height": 340},
            ],
            "background_path": "photobooth_custom/frontend/assets/pb-template-cinema.png",
            "overlay_path": None,
            "builtin": True,
        },
        {
            "id": "classic-two-up",
            "name": "สตริปคู่ 2×6 (พิมพ์ 4×6)",
            "canvas_width": 1200,
            "canvas_height": 1800,
            "slots": [
                {"x": 35, "y": 382, "width": 530, "height": 366},
                {"x": 35, "y": 782, "width": 530, "height": 366},
                {"x": 35, "y": 1181, "width": 530, "height": 366},
            ],
            "print_layout": "two_up",
            "background_path": None,
            "overlay_path": None,
            "builtin": True,
        },
        {
            "id": "classic-single",
            "name": "ภาพเดี่ยวเต็มกรอบ",
            "canvas_width": 1200,
            "canvas_height": 1800,
            "slots": [{"x": 80, "y": 100, "width": 1040, "height": 1480}],
            "background_path": None,
            "overlay_path": None,
            "builtin": True,
        },
        {
            "id": "classic-landscape",
            "name": "ภาพเดี่ยวแนวนอน 4×6",
            "canvas_width": 1800,
            "canvas_height": 1200,
            "slots": [{"x": 100, "y": 90, "width": 1600, "height": 1020}],
            "background_path": None,
            "overlay_path": None,
            "builtin": True,
        },
        {
            "id": "classic-six",
            "name": "กริด 6 ภาพ",
            "canvas_width": 1200,
            "canvas_height": 1800,
            "slots": [
                {"x": 55, "y": 90, "width": 525, "height": 450},
                {"x": 620, "y": 90, "width": 525, "height": 450},
                {"x": 55, "y": 575, "width": 525, "height": 450},
                {"x": 620, "y": 575, "width": 525, "height": 450},
                {"x": 55, "y": 1060, "width": 525, "height": 450},
                {"x": 620, "y": 1060, "width": 525, "height": 450},
            ],
            "background_path": None,
            "overlay_path": None,
            "builtin": True,
        },
    ]


def _load_templates() -> list[dict]:
    custom: list[dict] = []
    with _store_lock:
        if TEMPLATE_STORE.is_file():
            try:
                data = json.loads(TEMPLATE_STORE.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    custom = [item for item in data if isinstance(item, dict)]
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Could not read custom templates: %s", exc)
        hidden = _hidden_builtin_ids()
    builtin_ids = {item["id"] for item in _default_templates()}
    return [item for item in _default_templates() if item["id"] not in hidden] + [item for item in custom if item.get("id") not in builtin_ids]


def _hidden_builtin_ids() -> set[str]:
    with _store_lock:
        try:
            data = json.loads((TEMPLATE_DIR / "hidden-builtins.json").read_text(encoding="utf-8"))
            return set(data) & {item["id"] for item in _default_templates()} if isinstance(data, list) else set()
        except (OSError, ValueError, TypeError):
            return set()


def _save_hidden_builtin_ids(hidden: set[str]) -> None:
    with _store_lock:
        TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
        destination = TEMPLATE_DIR / "hidden-builtins.json"
        temporary = destination.with_name(f"{destination.name}.{uuid4().hex}.tmp")
        temporary.write_text(json.dumps(sorted(hidden)), encoding="utf-8")
        temporary.replace(destination)


def _save_custom_templates(templates: list[dict]) -> None:
    with _store_lock:
        TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
        custom = [item for item in templates if not item.get("builtin")]
        temporary = TEMPLATE_STORE.with_name(f"{TEMPLATE_STORE.name}.{uuid4().hex}.tmp")
        temporary.write_text(json.dumps(custom, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(TEMPLATE_STORE)


def _find_template(template_id: str) -> dict:
    template = next((item for item in _load_templates() if item.get("id") == template_id), None)
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ไม่พบเทมเพลตที่เลือก")
    return template


def _active_template_id() -> str:
    with _store_lock:
        try:
            template_id = ACTIVE_TEMPLATE_STORE.read_text(encoding="utf-8").strip()
            if template_id and any(item["id"] == template_id for item in _load_templates()):
                return template_id
        except OSError:
            pass
        templates = _load_templates()
        return templates[0]["id"] if templates else "theater"


def _template_asset_url(template_id: str, asset_type: str, filepath: str | None) -> str | None:
    if not filepath:
        return None
    base = f"/api/share/templates/{template_id}/asset/{asset_type}"
    try:
        mtime_ns = Path(filepath).stat().st_mtime_ns
    except OSError:
        mtime_ns = 0
    return f"{base}?v={mtime_ns}" if mtime_ns > 0 else base


def _public_template(template: dict) -> dict:
    template_id = template["id"]
    return {
        "id": template_id,
        "name": template["name"],
        "canvas_width": template["canvas_width"],
        "canvas_height": template["canvas_height"],
        "slots": template["slots"],
        "right_slots": template.get("right_slots"),
        "capture_count": len(template["slots"]),
        "print_layout": template.get("print_layout", "full"),
        "download_format": "2×6" if template.get("print_layout") == "two_up" else "4×6",
        "background_url": _template_asset_url(template_id, "background", template.get("background_path")),
        "overlay_url": _template_asset_url(template_id, "overlay", template.get("overlay_path")),
        "builtin": bool(template.get("builtin")),
        "active": template_id == _active_template_id(),
    }


def _apply_template(template: dict) -> None:
    processing = appconfig.actions.collage[0].processing
    processing.canvas_width = int(template["canvas_width"])
    processing.canvas_height = int(template["canvas_height"])
    slots = list(template["slots"])
    if template.get("print_layout") == "two_up":
        half_width = processing.canvas_width // 2
        slots += template.get("right_slots") or [{**slot, "x": slot["x"] + half_width} for slot in template["slots"]]
    processing.merge_definition = [
        CollageMergeDefinition(
            description=f"Photo {index + 1}",
            pos_x=slot["x"],
            pos_y=slot["y"],
            pos_z=0,
            width=slot["width"],
            height=slot["height"],
            rotate=0,
            predefined_image=None,
            image_filter="original",
        )
        for index, slot in enumerate(slots)
    ]
    background_path = template.get("background_path")
    overlay_path = template.get("overlay_path")
    processing.canvas_fill_background_enable = True
    processing.canvas_fill_background_color = "#ffffff"
    processing.canvas_img_background_enable = bool(background_path)
    processing.canvas_img_background_file = Path(background_path) if background_path else None
    processing.canvas_img_front_enable = bool(overlay_path)
    processing.canvas_img_front_file = Path(overlay_path) if overlay_path else None


def _activate_template(template: dict) -> None:
    with _store_lock:
        _apply_template(template)
        TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
        temporary = ACTIVE_TEMPLATE_STORE.with_name(f"{ACTIVE_TEMPLATE_STORE.name}.{uuid4().hex}.tmp")
        temporary.write_text(template["id"], encoding="utf-8")
        temporary.replace(ACTIVE_TEMPLATE_STORE)


def _write_template_asset(data_url: str, output_path: Path, canvas_width: int, canvas_height: int, print_layout: str) -> None:
    match = re.match(r"^data:image/(png|jpeg|jpg);base64,(.+)$", data_url, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="รองรับกรอบ PNG หรือ JPG เท่านั้น")
    try:
        decoded = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="ข้อมูลไฟล์กรอบไม่ถูกต้อง") from exc
    if len(decoded) > 22_000_000:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="ไฟล์กรอบมีขนาดใหญ่เกิน 22 MB")
    try:
        with Image.open(BytesIO(decoded)) as source:
            if source.width * source.height > 18_000_000:
                raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="ความละเอียดไฟล์กรอบสูงเกินไป")
            image = source.convert("RGBA")
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="ไม่สามารถอ่านไฟล์กรอบได้") from exc
    full_ratio = canvas_width / canvas_height
    image_ratio = image.width / image.height
    if print_layout == "two_up" and abs(image_ratio - full_ratio / 2) <= full_ratio * 0.01:
        half = image.resize((canvas_width // 2, canvas_height), Image.Resampling.LANCZOS)
        full = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
        full.paste(half, (0, 0))
        full.paste(half, (canvas_width // 2, 0))
        image = full
    elif abs(image_ratio - full_ratio) <= full_ratio * 0.01:
        image = image.resize((canvas_width, canvas_height), Image.Resampling.LANCZOS)
    else:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="สัดส่วนไฟล์กรอบไม่ตรงกับขนาดเทมเพลต กรุณาใช้ภาพเต็ม 4×6 หรือสตริป 2×6 สำหรับแบบสองชุด")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG", optimize=True)


def _load_printer_profiles() -> dict:
    default_printer = "Brother_DCP_T830DW"
    try:
        if appconfig.share.actions:
            configured = getattr(appconfig.share.actions[0].processing, "printer_name", None)
            if configured:
                default_printer = configured
    except Exception:
        pass
    default = {
        "profiles": [
            {
                "id": "default-4x6",
                "name": "ภาพ 4×6 นิ้ว",
                "printer_name": default_printer,
                "paper_size": "4x6.Fullbleed",
                "copies": 1,
            }
        ],
        "active_profile_id": "default-4x6",
    }
    with _store_lock:
        if PRINTER_STORE.is_file():
            try:
                saved = json.loads(PRINTER_STORE.read_text(encoding="utf-8"))
                if isinstance(saved, dict) and isinstance(saved.get("profiles"), list):
                    return saved
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Could not read printer profiles: %s", exc)
    return default


def _save_printer_profiles(data: dict) -> None:
    with _store_lock:
        PRINTER_STORE.parent.mkdir(parents=True, exist_ok=True)
        temporary = PRINTER_STORE.with_name(f"{PRINTER_STORE.name}.{uuid4().hex}.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(PRINTER_STORE)


def _active_printer_profile(profile_id: str | None = None) -> dict:
    data = _load_printer_profiles()
    requested = profile_id or data.get("active_profile_id")
    profile = next((item for item in data["profiles"] if item.get("id") == requested), None)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ไม่พบโปรไฟล์เครื่องพิมพ์")
    return profile


def _printer_is_available(status_text: str) -> bool:
    status_lower = status_text.lower()
    return not any(word in status_lower for word in (
        "disabled", "offline", "stopped", "not available", "not working",
        "ไม่ได้ทำงาน", "ไม่พร้อมใช้งาน", "ปิดการใช้งาน", "ถูกปิดใช้งาน", "หยุดทำงาน",
    ))


def _send_to_printer(filepath: Path, profile: dict, copies: int, title: str | None = None) -> str:
    if not filepath.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ไม่พบไฟล์สำหรับพิมพ์")
    printer_name = str(profile["printer_name"])
    try:
        state = subprocess.run(["lpstat", "-p", printer_name], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"ตรวจสถานะเครื่องพิมพ์ {printer_name} ไม่สำเร็จ") from exc
    if state.returncode != 0 or not _printer_is_available(state.stdout):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"เครื่องพิมพ์ {printer_name} ยังไม่พร้อม กรุณาตรวจเครื่องก่อนส่งงาน")
    paper_size = str(profile.get("paper_size") or _default_paper_size_for_printer(printer_name))
    args = [
        "-d", printer_name,
        "-n", str(max(1, min(99, copies))),
        *_cups_paper_options(printer_name, paper_size),
    ]
    if title:
        args.extend(["-t", title])
    args.extend(["--", str(filepath.absolute())])
    try:
        result = subprocess.run(["lp", *args], capture_output=True, text=True, timeout=15, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.exception(exc)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"ส่งงานพิมพ์ไม่สำเร็จ: {exc}") from exc
    return result.stdout.strip()


def _validate_share_parameters(index: int, parameters: dict[str, str] | None) -> dict[str, str] | None:
    if parameters is None:
        return None
    actions = getattr(getattr(appconfig, "share", None), "actions", None) or []
    if index < 0 or index >= len(actions):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ไม่พบคำสั่งแชร์ที่เลือก")
    param_defs = {item.key: item for item in getattr(actions[index].processing, "parameters", [])}
    validated: dict[str, str] = {}
    for key, raw_value in parameters.items():
        if key == "filename" or key not in param_defs:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"พารามิเตอร์ไม่ถูกต้อง: {key}")
        definition = param_defs[key]
        if getattr(definition, "ui_type", None) == "int":
            try:
                int_value = int(str(raw_value).strip())
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"ค่าของ {key} ต้องเป็นตัวเลข") from exc
            min_raw = getattr(definition, "valid_min", None)
            max_raw = getattr(definition, "valid_max", None)
            if min_raw is not None and str(min_raw).strip() != "" and int_value < int(min_raw):
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"ค่าของ {key} ต่ำกว่าที่กำหนด")
            if max_raw is not None and str(max_raw).strip() != "" and int_value > int(max_raw):
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"ค่าของ {key} สูงกว่าที่กำหนด")
            validated[key] = str(int_value)
        else:
            text_value = str(raw_value)
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", text_value):
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"ค่าของ {key} มีอักขระต้องห้าม")
            validated[key] = text_value
    return validated


def _share(mediaitem: Mediaitem, index: int, parameters: dict[str, str] | None):
    validated = _validate_share_parameters(index, parameters)
    try:
        container.share_service.share(mediaitem, index, validated)
    except (BlockingIOError, ConnectionRefusedError, WrongMediaTypeError):
        pass  # informed by sepearate sse event
    except Exception as exc:
        logger.exception(exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Something went wrong, Exception: {exc}") from exc


@router.post("/actions/{index}")
@router.post("/actions/latest/{index}")
def api_share_latest(request: Request, index: int = 0, parameters: dict[str, str] | None = None):
    _require_localhost(request)
    try:
        latest_mediaitem = container.mediacollection_service.get_item_latest()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"File not found: {exc}") from exc

    _share(latest_mediaitem, index, parameters)


@router.post("/actions/{id}/{index}")
def api_share_item_id(id: UUID, request: Request, index: int = 0, parameters: dict[str, str] | None = None):
    _require_localhost(request)
    try:
        requested_mediaitem = container.mediacollection_service.get_item(id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"File not found: {exc}") from exc
    _share(requested_mediaitem, index, parameters)


@router.get("/download/{id}")
def api_download_item_id_get_sharelinks(id: UUID):  # -> list[str]:
    try:
        requested_mediaitem = container.mediacollection_service.get_item(id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"File not found: {exc}") from exc

    pluggy_links = pluggy_pm.hook.get_share_links(filepath_local=requested_mediaitem.processed, identifier=requested_mediaitem.id)
    pluggy_links_flatten = [x for xs in pluggy_links for x in xs]

    return pluggy_links_flatten


@router.post("/airdrop/latest")
def api_airdrop_latest(request: Request):
    _require_localhost(request, "ส่ง AirDrop ได้จากจอควบคุมบน Mac ผ่าน localhost เท่านั้น")
    try:
        latest_mediaitem = container.mediacollection_service.get_item_latest()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"File not found: {exc}") from exc

    filepath = str(latest_mediaitem.processed.absolute())
    try:
        subprocess.run(["open", "-a", "/System/Library/CoreServices/Finder.app/Contents/Applications/AirDrop.app"], check=False)
        subprocess.run(["open", "-R", filepath], check=False)
    except Exception as exc:
        logger.warning(f"Error opening AirDrop: {exc}")
    return {"ok": True, "filepath": filepath, "id": str(latest_mediaitem.id)}


@router.post("/airdrop/{id}")
def api_airdrop_item_id(id: UUID, request: Request):
    _require_localhost(request, "ส่ง AirDrop ได้จากจอควบคุมบน Mac ผ่าน localhost เท่านั้น")
    try:
        requested_mediaitem = container.mediacollection_service.get_item(id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"File not found: {exc}") from exc

    filepath = str(requested_mediaitem.processed.absolute())
    try:
        subprocess.run(["open", "-a", "/System/Library/CoreServices/Finder.app/Contents/Applications/AirDrop.app"], check=False)
        subprocess.run(["open", "-R", filepath], check=False)
    except Exception as exc:
        logger.warning(f"Error opening AirDrop: {exc}")
    return {"ok": True, "filepath": filepath, "id": str(requested_mediaitem.id)}


def _cups_jobs(which: str) -> set[str] | None:
    cache_key = (which, id(subprocess.run))
    now = time.monotonic()
    with _cups_jobs_lock:
        cached = _cups_jobs_cache.get(cache_key)
        if cached is not None and (now - cached[0]) < _CUPS_POLL_CACHE_TTL_SEC:
            return set(cached[1]) if cached[1] is not None else None
        try:
            result = subprocess.run(["lpstat", "-W", which, "-o"], capture_output=True, text=True, timeout=3, check=True)
            jobs = {line.split()[0] for line in result.stdout.splitlines() if line.split()}
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("Could not read CUPS %s jobs: %s", which, exc)
            return None
        _cups_jobs_cache[cache_key] = (time.monotonic(), jobs)
        return set(jobs)


@router.get("/guest-queue")
def api_guest_queue():
    queue = guest_queue_store.snapshot()
    needs_cups = any(
        job.get("cups_id") and job.get("state") not in {"printed", "canceled"}
        for entry in queue["entries"] for job in entry.get("print_jobs", [])
    )
    if needs_cups:
        guest_queue_store.update_print_states(_cups_jobs("not-completed"), _cups_jobs("completed"))
        queue = guest_queue_store.snapshot()
    return queue


@router.post("/guest-queue", status_code=status.HTTP_201_CREATED)
def api_create_guest_queue(request: Request, payload: GuestQueueCreatePayload | None = None):
    actor_role, _ = _require_station_action(request, "tickets", "จับคู่จุดออกตั๋วจาก Mac เครื่องหลักก่อนออกคิว")
    idem_key = _idempotency_key(request)
    if idem_key:
        cached = platform_store.get_idempotency("guest-queue.create", idem_key)
        if cached:
            return JSONResponse(status_code=cached[0], content=cached[1])
    active_event = platform_store.get_active_event()
    if not active_event.get("intake_open", True):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="ขณะนี้ปิดรับคิวชั่วคราว")
    payload = payload or GuestQueueCreatePayload()
    try:
        created = guest_queue_store.create(payload.prefix, payload.people_count, gallery_folder_store.snapshot()["active_id"])
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if idem_key:
        platform_store.save_idempotency("guest-queue.create", idem_key, status.HTTP_201_CREATED, created)
    _audit(request, "queue.create", "guest_queue", created["code"], detail={"prefix": payload.prefix, "people_count": payload.people_count, "event_id": active_event["id"]}, actor_role=actor_role)
    event_bus.notify("queue", {"action": "create", "code": created["code"]})
    return created


@router.post("/guest-queue/{code}/ticket-airdrop")
def api_airdrop_guest_ticket(code: str, payload: QueueTicketAirDropPayload, request: Request):
    _require_localhost(request, "ส่ง AirDrop ได้จากจอควบคุมบน Mac ผ่าน localhost เท่านั้น")
    if sys.platform != "darwin":
        raise HTTPException(status_code=501, detail="AirDrop ใช้ได้บน Mac เท่านั้น")
    queue = guest_queue_store.snapshot()
    entry = next((item for item in queue["entries"] if item["code"] == code), None)
    if entry is None or entry.get("status") == "canceled":
        raise HTTPException(status_code=404, detail="ไม่พบบัตรคิวที่ยังใช้งานได้")
    prefix = "data:image/png;base64,"
    if not payload.image_data.startswith(prefix):
        raise HTTPException(status_code=415, detail="บัตรคิวต้องเป็นไฟล์ PNG")
    try:
        raw = base64.b64decode(payload.image_data[len(prefix):], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="ข้อมูลบัตรคิวไม่ถูกต้อง") from exc
    if len(raw) > 5_000_000:
        raise HTTPException(status_code=413, detail="ไฟล์บัตรคิวใหญ่เกินไป")
    try:
        with Image.open(BytesIO(raw)) as ticket:
            if ticket.format != "PNG" or ticket.size != (900, 1500):
                raise ValueError("ขนาดภาพบัตรคิวไม่ถูกต้อง")
            ticket.load()
            QUEUE_TICKET_DIR.mkdir(parents=True, exist_ok=True)
            path = QUEUE_TICKET_DIR / f"{entry.get('round_id') or queue['round_id']}-{entry['code']}.png"
            ticket.save(path, format="PNG")
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="เปิดภาพบัตรคิวไม่สำเร็จ") from exc
    app = "/System/Library/CoreServices/Finder.app/Contents/Applications/AirDrop.app"
    try:
        opened = subprocess.run(["open", "-a", app], capture_output=True, timeout=5, check=False)
        revealed = subprocess.run(["open", "-R", str(path.absolute())], capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=500, detail="เปิด AirDrop บน Mac ไม่สำเร็จ") from exc
    if opened.returncode or revealed.returncode:
        raise HTTPException(status_code=500, detail="เปิด AirDrop หรือ Finder ไม่สำเร็จ")
    return {"ok": True, "filename": path.name}


@router.post("/guest-queue/categories", status_code=status.HTTP_201_CREATED)
def api_add_guest_queue_category(payload: QueueCategoryPayload, request: Request):
    actor_role, _ = _require_localhost(request)
    try:
        cat = guest_queue_store.add_category(payload.prefix, payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _audit(request, "queue.category.create", "queue_category", cat["prefix"], detail=cat, actor_role=actor_role)
    event_bus.notify("queue", {"action": "category_create", "prefix": cat["prefix"]})
    return cat


@router.get("/guest-queue/report")
def api_guest_queue_report():
    return guest_queue_store.report()


def _visible_gallery_items() -> list[Mediaitem]:
    items = []
    offset = 0
    while True:
        batch = container.mediacollection_service.list_items(offset, 500)
        items.extend(batch)
        if len(batch) < 500:
            return items
        offset += len(batch)


def _gallery_folder_view() -> tuple[dict, list[Mediaitem]]:
    data = gallery_folder_store.snapshot()
    items = _visible_gallery_items()
    folders = [{"id": project["id"], "name": project["name"], "kind": "project", "count": 0} for project in data["projects"]]
    dates = {}
    for item in items:
        folder_id = folder_for_media(item, data)
        if folder_id.startswith("date-"):
            dates.setdefault(folder_id, {"id": folder_id, "name": folder_id[5:], "kind": "date", "count": 0})["count"] += 1
        else:
            next(folder for folder in folders if folder["id"] == folder_id)["count"] += 1
    folders.extend(dates[key] for key in sorted(dates, reverse=True))
    return {"active_id": data["active_id"], "folders": folders, "assignments": data["assignments"]}, items


@router.get("/gallery-folders")
def api_gallery_folders():
    return _gallery_folder_view()[0]


@router.post("/gallery-folders", status_code=201)
def api_create_gallery_folder(payload: GalleryFolderPayload, request: Request):
    _require_localhost(request)
    try:
        return gallery_folder_store.create(payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/gallery-folders/active")
def api_activate_gallery_folder(payload: GalleryMovePayload, request: Request):
    _require_localhost(request)
    try:
        gallery_folder_store.activate(payload.folder_id)
        return {"active_id": payload.folder_id}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/gallery-folders/media/{media_id}")
def api_move_gallery_media(media_id: UUID, payload: GalleryMovePayload, request: Request):
    _require_localhost(request)
    item = _get_item_or_404(media_id)
    related = container.mediacollection_service.get_items_relto_job(item.job_identifier)
    try:
        gallery_folder_store.move([str(part.id) for part in related], payload.folder_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "moved": len(related)}


@router.post("/gallery-folders/{folder_id}/import-unassigned")
def api_import_unassigned_gallery_media(folder_id: str, payload: GalleryImportPayload, request: Request):
    _require_localhost(request, "การย้ายภาพเก่าทั้งหมดทำได้จาก Mac เครื่องหลักผ่าน localhost เท่านั้น")
    view, visible = _gallery_folder_view()
    folder = next((entry for entry in view["folders"] if entry["id"] == folder_id and entry["kind"] == "project"), None)
    if folder is None:
        raise HTTPException(status_code=404, detail="ไม่พบโฟลเดอร์โครงการ")
    if payload.confirm_name != folder["name"]:
        raise HTTPException(status_code=409, detail="ชื่อยืนยันไม่ตรงกับชื่อโฟลเดอร์")
    if container.processing_service.is_occupied() or container.processing_service._workflow_jobmodel is not None:
        raise HTTPException(status_code=409, detail="กำลังถ่ายภาพอยู่ กรุณารอให้เสร็จก่อนย้ายภาพเก่า")
    unassigned = [item for item in visible if not view["assignments"].get(str(item.id))]
    if len(unassigned) != payload.expected_count:
        raise HTTPException(status_code=409, detail=f"จำนวนภาพเปลี่ยนไป พบ {len(unassigned)} รูป กรุณาตรวจอีกครั้ง")
    gallery_folder_store.assign([str(item.id) for item in unassigned], folder_id)
    return {"ok": True, "moved": len(unassigned), "folder_id": folder_id}


def _folder_targets(folder_id: str) -> tuple[dict, list[Mediaitem]]:
    view, visible = _gallery_folder_view()
    folder = next((entry for entry in view["folders"] if entry["id"] == folder_id), None)
    if folder is None:
        raise HTTPException(status_code=404, detail="ไม่พบโฟลเดอร์")
    state = gallery_folder_store.snapshot()
    selected = [item for item in visible if folder_for_media(item, state) == folder_id]
    target_ids = {str(item.id) for item in selected}
    targets = list(selected)
    for job_id in {item.job_identifier for item in selected}:
        related = container.mediacollection_service.get_items_relto_job(job_id)
        if any(part.show_in_gallery and str(part.id) not in target_ids for part in related):
            continue
        for part in related:
            if str(part.id) not in target_ids:
                targets.append(part)
                target_ids.add(str(part.id))
    return folder, targets


@router.get("/gallery-folders/{folder_id}/delete-preview")
def api_gallery_folder_delete_preview(folder_id: str):
    folder, targets = _folder_targets(folder_id)
    with _cloud_lock:
        cloud = _cloud_records()
        online_count = sum(bool(cloud.get(str(item.id), {}).get("expires_at")) for item in targets)
    return {"id": folder_id, "name": folder["name"], "photo_count": folder["count"], "total_items": len(targets), "online_count": online_count}


@router.delete("/gallery-folders/{folder_id}")
def api_delete_gallery_folder(folder_id: str, payload: GalleryDeletePayload, request: Request):
    _require_localhost(request, "การลบโฟลเดอร์ทำได้จาก Mac เครื่องหลักผ่าน localhost เท่านั้น")
    folder, targets = _folder_targets(folder_id)
    if payload.confirm_name != folder["name"]:
        raise HTTPException(status_code=409, detail="ชื่อยืนยันไม่ตรงกับชื่อโฟลเดอร์")
    if container.processing_service.is_occupied() or container.processing_service._workflow_jobmodel is not None:
        raise HTTPException(status_code=409, detail="กำลังถ่ายภาพอยู่ กรุณารอให้เสร็จก่อนลบโฟลเดอร์")
    target_ids = {str(item.id) for item in targets}
    if guest_queue_store.pending_print_media_ids() & target_ids:
        raise HTTPException(status_code=409, detail="ยังมีภาพในโฟลเดอร์ที่กำลังพิมพ์")
    with _cloud_lock:
        if any(str(item.id) in _cloud_inflight for item in targets):
            raise HTTPException(status_code=409, detail="ยังมีภาพที่กำลังอัปโหลดออนไลน์ กรุณาลองใหม่")
        cloud = _cloud_records()
        remote = [{"id": str(item.id), "expires_at": cloud[str(item.id)]["expires_at"]} for item in targets if cloud.get(str(item.id), {}).get("expires_at")]
        if remote and not CLOUD_ENV.exists():
            raise HTTPException(status_code=503, detail="ไม่พบการตั้งค่า Cloud จึงยังลบภาพออนไลน์ไม่ได้")
    recovery = Path(RECYCLE_PATH) / "photobooth-folders" / uuid4().hex
    recovery.mkdir(parents=True, exist_ok=False)
    manifest = {"folder": folder, "items": []}
    try:
        cache_paths_by_media: dict[str, list[str]] = {}
        item_ids = [item.id for item in targets]
        if item_ids:
            with Session(engine) as session:
                cached = list(session.scalars(select(Cacheditem).where(Cacheditem.mediaitem_id.in_(item_ids))).all())
                for entry in cached:
                    cache_paths_by_media.setdefault(str(entry.mediaitem_id), []).append(str(entry.filepath))
        for item in targets:
            files = []
            for path in (item.processed, item.captured_original, *cache_paths_by_media.get(str(item.id), [])):
                if path and Path(path).is_file():
                    destination = recovery / f"{item.id}-{Path(path).name}"
                    shutil.copy2(path, destination)
                    files.append({"original": str(path), "backup": str(destination)})
            manifest["items"].append({"id": str(item.id), "files": files})
        (recovery / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=507, detail="สำรองไฟล์ก่อนลบไม่สำเร็จ ยังไม่ได้ลบภาพ") from exc
    if remote:
        try:
            subprocess.run(["node", f"--env-file={CLOUD_ENV}", "vercel_public/scripts/delete-photos.mjs"], input=json.dumps(remote), text=True, capture_output=True, timeout=90, check=True)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.exception("Could not delete online folder photos")
            raise HTTPException(status_code=503, detail="ลบภาพออนไลน์ไม่สำเร็จ ยังไม่ได้ลบภาพบน Mac") from exc
        with _cloud_lock:
            records = _cloud_records()
            for entry in remote:
                records.pop(entry["id"], None)
                try:
                    platform_store.remove_upload(entry["id"])
                except Exception:
                    pass
            _save_cloud_records(records)
    deleted = 0
    for item in targets:
        try:
            with Session(engine) as session:
                session.execute(sa_delete(Cacheditem).where(Cacheditem.mediaitem_id == item.id))
                session.commit()
            for cached_path in cache_paths_by_media.get(str(item.id), []):
                Path(cached_path).unlink(missing_ok=True)
            container.mediacollection_service.delete_item(item)
            deleted += 1
        except Exception as exc:
            logger.exception("Folder deletion partially failed after %s items; recovery at %s", deleted, recovery)
            raise HTTPException(status_code=500, detail=f"ลบได้ {deleted} จาก {len(targets)} รายการ มีไฟล์สำรองที่ {recovery}") from exc
    with _cloud_lock:
        records = _cloud_records()
        for item in targets:
            records.pop(str(item.id), None)
        _save_cloud_records(records)
    gallery_folder_store.move([str(item.id) for item in targets], None)
    if folder["kind"] == "project":
        gallery_folder_store.remove_project(folder_id)
    _audit(request, "folder.delete", "gallery_folder", folder_id, detail={"deleted": deleted, "name": folder["name"]})
    event_bus.notify("gallery", {"action": "folder_delete", "folder_id": folder_id})
    return {"ok": True, "deleted": deleted, "recovery_path": str(recovery)}


@router.post("/guest-queue/reset")
def api_reset_guest_queue(payload: QueueResetPayload, request: Request):
    actor_role, _ = _require_role(request, "admin")
    idem_key = _idempotency_key(request)
    if idem_key:
        cached = platform_store.get_idempotency("guest-queue.reset", idem_key)
        if cached:
            return JSONResponse(status_code=cached[0], content=cached[1])
    try:
        result = guest_queue_store.reset_round(payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if idem_key:
        platform_store.save_idempotency("guest-queue.reset", idem_key, 200, result)
    _audit(request, "queue.reset", "guest_queue_round", result["id"], detail={"name": payload.name}, actor_role=actor_role)
    event_bus.notify("queue", {"action": "reset", "round_id": result["id"]})
    return result


@router.post("/guest-queue/advance")
def api_advance_guest_queue(request: Request):
    actor_role, _ = _require_localhost(request)
    try:
        result = guest_queue_store.advance()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    _audit(
        request,
        "queue.advance",
        "guest_queue",
        result.get("active_code") or result.get("completed_code") or "",
        detail=result,
        actor_role=actor_role,
    )
    event_bus.notify(
        "queue",
        {
            "action": "advance",
            "completed_code": result.get("completed_code"),
            "active_code": result.get("active_code"),
        },
    )
    return result


@router.post("/guest-queue/{code}/template")
def api_select_guest_queue_template(code: str, payload: QueueTemplatePayload):
    template = _find_template(payload.template_id)

    def activate_if_idle() -> None:
        if container.processing_service.is_occupied() or container.processing_service._workflow_jobmodel is not None:
            raise ValueError("เริ่มถ่ายภาพแล้ว ไม่สามารถเปลี่ยนเทมเพลตได้")
        _activate_template(template)

    try:
        entry = guest_queue_store.select_template(code, payload.template_id, activate_if_idle)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ไม่พบหมายเลขคิว")
    event_bus.notify("queue", {"action": "template", "code": code, "template_id": payload.template_id})
    return entry


@router.post("/guest-queue/{code}/start")
def api_start_guest_queue(code: str, request: Request):
    actor_role, _ = _require_localhost(request)
    queue_snapshot = guest_queue_store.snapshot()
    if queue_snapshot.get("active_code") and queue_snapshot["active_code"] != code:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="มีคิวกำลังถ่ายอยู่ กรุณาจบหรือยกเลิกคิวนั้นก่อน")
    pending = next((item for item in queue_snapshot["entries"] if item.get("code") == code), None)
    if pending is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ไม่พบหมายเลขคิว")
    if pending.get("status") == "canceled":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="คิวนี้ถูกยกเลิกแล้ว")
    try:
        entry = guest_queue_store.start(code, allow_replace=False)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ไม่พบหมายเลขคิว")
    _audit(request, "queue.start", "guest_queue", code, actor_role=actor_role)
    event_bus.notify("queue", {"action": "start", "code": code})
    return entry


@router.post("/guest-queue/{code}/complete")
def api_complete_guest_queue(code: str, request: Request):
    actor_role, _ = _require_localhost(request)
    try:
        entry = guest_queue_store.complete(code)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ไม่พบหมายเลขคิว")
    _audit(request, "queue.complete", "guest_queue", code, actor_role=actor_role)
    event_bus.notify("queue", {"action": "complete", "code": code})
    return entry


@router.post("/guest-queue/{code}/call")
def api_call_guest_queue(code: str, request: Request):
    actor_role, _ = _require_localhost(request)
    try:
        event = guest_queue_store.call(code)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ไม่พบหมายเลขคิว")
    _audit(request, "queue.call", "guest_queue", code, actor_role=actor_role)
    event_bus.notify("queue", {"action": "call", "code": code})
    return event


@router.post("/guest-queue/{code}/skip")
def api_skip_guest_queue(code: str, request: Request):
    actor_role, _ = _require_localhost(request, "ข้ามคิวได้จากจอเจ้าหน้าที่บน Mac ผ่าน localhost เท่านั้น")
    try:
        entry = guest_queue_store.skip_waiting(code)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ไม่พบหมายเลขคิว")
    _audit(request, "queue.skip", "guest_queue", code, actor_role=actor_role)
    event_bus.notify("queue", {"action": "skip", "code": code})
    return entry


@router.delete("/guest-queue/{code}")
def api_cancel_guest_queue(code: str, request: Request):
    actor_role, _ = _require_localhost(request)
    try:
        entry = guest_queue_store.cancel_unshot(code, allow_active=True)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ไม่พบหมายเลขคิว")
    _audit(request, "queue.cancel", "guest_queue", code, actor_role=actor_role)
    event_bus.notify("queue", {"action": "cancel", "code": code})
    return entry


def _probe_single_printer(
    printer_name: str,
    filename_to_item: dict[str, Mediaitem],
    id_to_item: dict[str, Mediaitem],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Query lpstat/lpq for a single CUPS printer with a short timeout."""
    try:
        state = subprocess.run(
            ["lpstat", "-p", printer_name],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_CUPS_CMD_TIMEOUT_SEC,
        )
        state_text = state.stdout.strip() or state.stderr.strip()
        online = state.returncode == 0 and _printer_is_available(state_text)
        result = subprocess.run(
            ["lpq", "-P", printer_name],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_CUPS_CMD_TIMEOUT_SEC,
        )
        jobs: list[dict[str, Any]] = []
        for line in result.stdout.splitlines()[2:]:
            parts = line.split()
            if len(parts) < 4 or not parts[2].isdigit():
                continue
            rank, owner, number, filename = parts[:4]
            cups_id = f"{printer_name}-{number}"
            matched_item = filename_to_item.get(filename)
            queue_code = guest_queue_store.code_for_job(str(matched_item.job_identifier)) if matched_item else None
            if not matched_item or not queue_code:
                print_match = guest_queue_store.print_job_for_cups_id(cups_id)
                if print_match:
                    matched_code, matched_media_id = print_match
                    if not queue_code:
                        queue_code = matched_code
                    if not matched_item and matched_media_id:
                        matched_item = id_to_item.get(matched_media_id)
                        if matched_item is None:
                            try:
                                matched_item = container.mediacollection_service.get_item(UUID(matched_media_id))
                            except Exception:
                                matched_item = None
            item_id = str(matched_item.id) if matched_item else None
            jobs.append({
                "rank": rank,
                "owner": owner,
                "job_id": cups_id,
                "filename": filename,
                "printer_name": printer_name,
                "is_active": online and rank.lower() == "active",
                "item_id": item_id,
                "queue_code": queue_code,
                "thumbnail_url": f"/media/thumbnail/{item_id}" if item_id else None,
                "preview_url": f"/media/preview/{item_id}" if item_id else None,
                "created_at": matched_item.created_at.isoformat() if matched_item and hasattr(matched_item, "created_at") else None,
            })
        return {"name": printer_name, "online": online, "status": state_text, "queue_count": len(jobs)}, jobs
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Could not read print queue for %s: %s", printer_name, exc)
        return {"name": printer_name, "online": False, "status": str(exc), "queue_count": 0}, []


def _compute_print_queue_snapshot(printer_names: list[str], default_printer: str) -> dict[str, Any]:
    try:
        items = container.mediacollection_service.list_items(0, 300)
        filename_to_item = {item.processed.name: item for item in items if item.processed}
        id_to_item = {str(item.id): item for item in items}
    except Exception as exc:
        logger.warning("Could not match print jobs to photos: %s", exc)
        filename_to_item = {}
        id_to_item = {}

    if len(printer_names) <= 1:
        probe_results = [_probe_single_printer(name, filename_to_item, id_to_item) for name in printer_names]
    else:
        with ThreadPoolExecutor(max_workers=min(4, len(printer_names))) as pool:
            probe_results = list(
                pool.map(lambda name: _probe_single_printer(name, filename_to_item, id_to_item), printer_names)
            )

    all_jobs: list[dict[str, Any]] = []
    printer_states: list[dict[str, Any]] = []
    for state, jobs in probe_results:
        printer_states.append(state)
        all_jobs.extend(jobs)

    active_job = next((job for job in all_jobs if job["is_active"]), None)
    return {
        "printer": default_printer,
        "printers": printer_states,
        "is_printing": active_job is not None,
        "active_job": active_job,
        "queue_count": len(all_jobs),
        "queue": all_jobs,
        "unknown_print_jobs": guest_queue_store.unknown_print_jobs(),
    }


def _refresh_print_queue_bg(
    cache_key: tuple,
    printer_names: list[str],
    default_printer: str,
    epoch: int,
) -> None:
    global _print_queue_cache, _print_queue_refreshing
    try:
        payload = _compute_print_queue_snapshot(printer_names, default_printer)
    except Exception as exc:
        logger.debug("Background print queue refresh skipped: %s", exc)
        payload = None
    with _print_queue_lock:
        _print_queue_refreshing = False
        if payload is not None and _print_queue_epoch == epoch:
            _print_queue_cache = (time.monotonic(), cache_key, payload)


@router.get("/print_queue")
def api_get_print_queue():
    global _print_queue_cache, _print_queue_refreshing
    profiles = _load_printer_profiles()
    selected = next((item for item in profiles["profiles"] if item.get("id") == profiles.get("active_profile_id")), None)
    default_printer = selected["printer_name"] if selected else "Brother_DCP_T830DW"
    printer_names = list(dict.fromkeys(item["printer_name"] for item in profiles["profiles"]))
    cache_key = (
        tuple(printer_names),
        default_printer,
        id(subprocess.run),
        id(_load_printer_profiles),
        id(container.mediacollection_service.list_items),
        id(guest_queue_store),
    )
    with _print_queue_lock:
        if _print_queue_cache is not None and _print_queue_cache[1] == cache_key:
            age = time.monotonic() - _print_queue_cache[0]
            cached = _print_queue_cache[2]
            if age < _CUPS_POLL_CACHE_TTL_SEC:
                return {**cached, "unknown_print_jobs": guest_queue_store.unknown_print_jobs()}
            if age < _CUPS_STALE_MAX_AGE_SEC:
                if not _print_queue_refreshing:
                    _print_queue_refreshing = True
                    epoch = _print_queue_epoch
                    threading.Thread(
                        target=_refresh_print_queue_bg,
                        args=(cache_key, printer_names, default_printer, epoch),
                        daemon=True,
                    ).start()
                return {**cached, "unknown_print_jobs": guest_queue_store.unknown_print_jobs()}
        sync_epoch = _print_queue_epoch

    payload = _compute_print_queue_snapshot(printer_names, default_printer)
    with _print_queue_lock:
        if _print_queue_epoch == sync_epoch:
            _print_queue_cache = (time.monotonic(), cache_key, payload)
    return payload


@router.post("/guest-queue/print-jobs/resolve")
def api_resolve_unknown_print_job(payload: PrintJobResolvePayload, request: Request):
    actor_role, _ = _require_localhost(request)
    idem_key = _idempotency_key(request)
    if idem_key:
        cached = platform_store.get_idempotency("print.resolve", idem_key)
        if cached:
            return JSONResponse(status_code=cached[0], content=cached[1])
    try:
        entry = guest_queue_store.resolve_unknown_print(
            payload.queue_code.strip(),
            payload.outcome,
            print_job_id=payload.print_job_id,
            cups_id=payload.cups_id,
            reason=payload.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ไม่พบหมายเลขคิว")
    res_body = {"ok": True, "entry": entry}
    if idem_key:
        platform_store.save_idempotency("print.resolve", idem_key, 200, res_body)
    _audit(request, f"print.resolve.{payload.outcome}", "guest_queue", payload.queue_code.strip(), detail={"cups_id": payload.cups_id, "print_job_id": payload.print_job_id}, actor_role=actor_role)
    _invalidate_print_caches()
    event_bus.notify("print", {"action": "resolve", "code": payload.queue_code.strip()})
    return res_body


@router.delete("/print_queue/{job_id}")
def api_cancel_print_job(job_id: str, request: Request):
    actor_role, _ = _require_localhost(request)
    try:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", job_id):
            raise ValueError("invalid print job id")
        subprocess.run(["cancel", job_id], capture_output=True, text=True, timeout=3, check=True)
        guest_queue_store.cancel_print(job_id)
        _audit(request, "print.cancel", "cups_job", job_id, actor_role=actor_role)
        _invalidate_print_caches()
        event_bus.notify("print", {"action": "cancel", "job_id": job_id})
        return {"ok": True, "job_id": job_id}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/print_queue/clear")
def api_clear_print_queue(request: Request):
    actor_role, _ = _require_localhost(request)
    try:
        printer_name = _active_printer_profile()["printer_name"]
    except Exception:
        printer_name = "Brother_DCP_T830DW"
    try:
        subprocess.run(["cancel", "-a", printer_name], capture_output=True, text=True, timeout=3, check=True)
        guest_queue_store.cancel_printer(printer_name)
        _audit(request, "print.clear", "printer", printer_name, actor_role=actor_role)
        _invalidate_print_caches()
        event_bus.notify("print", {"action": "clear", "printer": printer_name})
        return {"ok": True, "printer": printer_name}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/camera-health")
def api_camera_health():
    backends = list(container.acquisition_service._backends)
    states = [
        {
            "name": str(backend),
            "running": backend.is_running(),
            "started": backend.is_started(),
        }
        for backend in backends
    ]
    connected = any(state["running"] for state in states)
    if connected:
        detected = [{"model": "Canon Camera", "port": "usb", "supported": True}]
    else:
        with _camera_detect_lock:
            detected = list(_camera_cache[1])
    return {
        "connected": connected,
        "backends": states,
        "detected": detected,
        "driver": "gphoto2 / libgphoto2",
        "supported_brand": "Canon",
    }


def _check_database_ok() -> tuple[bool, str]:
    try:
        with engine.connect() as conn:
            row = conn.execute(text("PRAGMA quick_check")).scalar()
        return (str(row).lower() == "ok", str(row))
    except Exception as exc:
        return (False, str(exc))


def _run_preflight_checks() -> dict:
    checks: list[dict] = []

    # 1. Config, admin_password, and misc.secret
    if not CONFIG_FILE.is_file():
        checks.append({"id": "admin_password", "label": "รหัสผ่าน Admin", "status": "fail", "detail": f"ไม่พบไฟล์ {CONFIG_FILE}"})
        checks.append({"id": "misc_secret", "label": "Signing secret", "status": "fail", "detail": f"ไม่พบไฟล์ {CONFIG_FILE}"})
    else:
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            admin_pw = str(((cfg.get("common") or {}).get("admin_password")) or "")
            secret_val = str(((cfg.get("misc") or {}).get("secret")) or "")
            if not admin_pw or admin_pw == "0000":
                checks.append({"id": "admin_password", "label": "รหัสผู้ดูแล", "status": "fail", "detail": "เปลี่ยนรหัสผู้ดูแลค่าเริ่มต้นก่อนเชื่อม Wi-Fi งาน"})
            else:
                checks.append({"id": "admin_password", "label": "รหัสผ่าน Admin", "status": "ok", "detail": "ตั้งค่ารหัสผ่านเฉพาะแล้ว"})
            if len(secret_val) >= 32 and secret_val != "075fe918f335a1f06fd5b0a0224ca877":
                checks.append({"id": "misc_secret", "label": "Signing secret", "status": "ok", "detail": "ตั้งค่า random secret ที่ปลอดภัยแล้ว"})
            else:
                checks.append({"id": "misc_secret", "label": "Signing secret", "status": "fail", "detail": "misc.secret ยังเป็นค่าเริ่มต้นหรือสั้นเกินไป"})
        except (OSError, json.JSONDecodeError) as exc:
            checks.append({"id": "admin_password", "label": "รหัสผ่าน Admin", "status": "fail", "detail": f"อ่าน {CONFIG_FILE} ไม่ได้: {exc}"})
            checks.append({"id": "misc_secret", "label": "Signing secret", "status": "fail", "detail": f"อ่าน {CONFIG_FILE} ไม่ได้"})

    # 2. SQLite databases quick check (Photobooth DB + Platform WAL DB)
    db_ok, db_detail = _check_database_ok()
    platform_ok, platform_detail = platform_store.quick_check()
    checks.append({
        "id": "database",
        "label": "ฐานข้อมูล SQLite",
        "status": "ok" if (db_ok and platform_ok) else "fail",
        "detail": "ฐานข้อมูลปกติ (Main + Platform WAL ok)" if (db_ok and platform_ok) else f"ตรวจฐานข้อมูลไม่ผ่าน (main={db_detail}, platform={platform_detail})",
    })

    # 3. Queue and folder JSON stores
    try:
        queue_snap = guest_queue_store.snapshot()
        folder_snap = gallery_folder_store.snapshot()
        unknown_count = len(guest_queue_store.unknown_print_jobs())
        checks.append({
            "id": "stores",
            "label": "ไฟล์คิวและโฟลเดอร์",
            "status": "ok",
            "detail": f"อ่านได้ปกติ (คิวในรอบนี้ {len(queue_snap.get('entries', []))} รายการ · โครงการ {len(folder_snap.get('projects', []))} โฟลเดอร์)",
        })
    except Exception as exc:
        queue_snap = {"active_code": None}
        unknown_count = 0
        checks.append({"id": "stores", "label": "ไฟล์คิวและโฟลเดอร์", "status": "fail", "detail": f"อ่านไฟล์สถานะไม่ได้: {exc}"})

    # 4. Disk space
    try:
        free_mb = shutil.disk_usage(".").free // (1024 * 1024)
        if free_mb >= 2048:
            disk_status = "ok"
        elif free_mb >= 512:
            disk_status = "warning"
        else:
            disk_status = "fail"
        checks.append({"id": "disk_space", "label": "พื้นที่ดิสก์คงเหลือ", "status": disk_status, "detail": f"ว่าง {free_mb} MB"})
    except OSError as exc:
        free_mb = 0
        checks.append({"id": "disk_space", "label": "พื้นที่ดิสก์คงเหลือ", "status": "fail", "detail": str(exc)})

    # 5. Camera runtime state
    backends = list(container.acquisition_service._backends)
    cam_connected = any(backend.is_running() for backend in backends)
    checks.append({
        "id": "camera",
        "label": "กล้อง Canon",
        "status": "ok" if cam_connected else "warning",
        "detail": "เชื่อมต่อกล้องและพร้อมถ่ายภาพ" if cam_connected else "ยังไม่ได้เชื่อมต่อกล้อง Canon (ยังดู/พิมพ์ภาพเก่าได้)",
    })

    # 6. CUPS Printers (reuse recent print-queue cache when fresh, otherwise probe in parallel)
    try:
        profiles = _load_printer_profiles()
        printer_names = list(dict.fromkeys(item["printer_name"] for item in profiles.get("profiles", [])))
        online_printers: list[str] | None = None
        run_id = id(subprocess.run)
        profiles_id = id(_load_printer_profiles)
        with _print_queue_lock:
            if _print_queue_cache is not None:
                cached_at, cached_key, cached_payload = _print_queue_cache
                if (
                    (time.monotonic() - cached_at) < 10.0
                    and len(cached_key) >= 4
                    and cached_key[0] == tuple(printer_names)
                    and cached_key[2] == run_id
                    and cached_key[3] == profiles_id
                ):
                    online_printers = [p["name"] for p in cached_payload.get("printers", []) if p.get("online")]
        if online_printers is None:
            def _is_printer_online(pname: str) -> str | None:
                st = subprocess.run(
                    ["lpstat", "-p", pname],
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=_CUPS_CMD_TIMEOUT_SEC,
                )
                st_text = st.stdout.strip() or st.stderr.strip()
                return pname if (st.returncode == 0 and _printer_is_available(st_text)) else None

            if len(printer_names) <= 1:
                online_printers = [res for pname in printer_names if (res := _is_printer_online(pname))]
            else:
                with ThreadPoolExecutor(max_workers=min(4, len(printer_names))) as pool:
                    online_printers = [res for res in pool.map(_is_printer_online, printer_names) if res]
        checks.append({
            "id": "printers",
            "label": "เครื่องพิมพ์ CUPS",
            "status": "ok" if online_printers else "warning",
            "detail": f"พร้อมใช้งาน: {', '.join(online_printers)}" if online_printers else "ยังไม่มีเครื่องพิมพ์ CUPS ที่พร้อมใช้งาน",
        })
    except Exception as exc:
        checks.append({"id": "printers", "label": "เครื่องพิมพ์ CUPS", "status": "warning", "detail": f"ตรวจสถานะเครื่องพิมพ์ไม่ได้: {exc}"})

    # 7. Unknown print jobs
    checks.append({
        "id": "unknown_print_jobs",
        "label": "งานพิมพ์สถานะไม่แน่ชัด",
        "status": "ok" if unknown_count == 0 else "warning",
        "detail": "ไม่มีงานพิมพ์ค้างยืนยัน" if unknown_count == 0 else f"มีงานพิมพ์สถานะไม่แน่ชัด {unknown_count} รายการ — กรุณาตรวจกระดาษและยืนยันในคิวพิมพ์",
    })

    # 8. Cloud QR configuration
    offline_enabled = _is_offline_mode()
    if offline_enabled:
        checks.append({
            "id": "cloud_qr",
            "label": "ระบบ QR ออนไลน์ (Cloud)",
            "status": "ok",
            "detail": "เปิดโหมดออฟไลน์อยู่ — ไม่พึ่ง Wi-Fi (ใช้พิมพ์ / AirDrop / QR วงแลนได้ทันที)",
        })
    else:
        checks.append({
            "id": "cloud_qr",
            "label": "ระบบ QR ออนไลน์ (Cloud)",
            "status": "ok" if CLOUD_ENV.exists() else "warning",
            "detail": "พบการตั้งค่า Vercel Blob พร้อมอัปโหลด" if CLOUD_ENV.exists() else "ยังไม่พบ vercel_public/.env.local (ยังใช้ QR บน Wi-Fi ท้องถิ่นได้)",
        })

    fail_count = sum(1 for item in checks if item["status"] == "fail")
    warning_count = sum(1 for item in checks if item["status"] == "warning")
    ok_count = sum(1 for item in checks if item["status"] == "ok")
    overall = "fail" if fail_count > 0 else ("warning" if warning_count > 0 else "ok")
    check_status = {item["id"]: item["status"] for item in checks}
    local_storage_ready = all(check_status[key] != "fail" for key in ("database", "stores", "disk_space"))
    ready_to_capture = local_storage_ready and check_status["camera"] == "ok"
    ready_to_print = local_storage_ready and check_status["printers"] == "ok"
    return {
        "status": overall,
        "ready_for_event": fail_count == 0 and ready_to_capture and ready_to_print and check_status["unknown_print_jobs"] == "ok",
        "ready_to_capture": ready_to_capture,
        "ready_to_print": ready_to_print,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "summary": {"ok": ok_count, "warning": warning_count, "fail": fail_count},
        "checks": checks,
    }


@router.get("/health")
def api_health():
    db_ok, _ = _check_database_ok()
    platform_ok, _ = platform_store.quick_check()
    try:
        queue_snap = guest_queue_store.snapshot()
        gallery_folder_store.snapshot()
        stores_ok = True
        unknown_jobs = len(guest_queue_store.unknown_print_jobs())
        outbox_counts = platform_store.list_outbox(limit=1)["counts"]
    except Exception:
        queue_snap = {"active_code": None}
        stores_ok = False
        unknown_jobs = 0
        outbox_counts = {"pending": 0, "uploading": 0, "failed": 0, "ready": 0}
    try:
        free_mb = shutil.disk_usage(".").free // (1024 * 1024)
    except OSError:
        free_mb = 0
    cam_connected = any(backend.is_running() for backend in container.acquisition_service._backends)
    healthy = db_ok and platform_ok and stores_ok and free_mb >= 512
    return {
        "status": "ok" if healthy else "degraded",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "database_ok": db_ok,
        "platform_db_ok": platform_ok,
        "stores_ok": stores_ok,
        "camera_connected": cam_connected,
        "disk_free_mb": free_mb,
        "unknown_print_jobs": unknown_jobs,
        "active_queue_code": queue_snap.get("active_code"),
        "cloud_configured": CLOUD_ENV.exists(),
        "offline_mode": _is_offline_mode(),
        "outbox_counts": outbox_counts,
    }


@router.get("/offline-mode")
def api_get_offline_mode(request: Request):
    try:
        outbox_counts = platform_store.list_outbox(limit=1)["counts"]
    except Exception:
        outbox_counts = {"pending": 0, "uploading": 0, "failed": 0, "ready": 0}
    return {
        "offline_mode": _is_offline_mode(),
        "cloud_configured": CLOUD_ENV.exists(),
        "local_base_url": _request_base_url(request),
        "outbox_counts": outbox_counts,
    }


@router.post("/offline-mode")
def api_set_offline_mode(payload: OfflineModePayload, request: Request):
    actor_role, _ = _require_localhost(request)
    state = _set_offline_mode(payload.offline_mode)
    enabled = bool(state["offline_mode"])
    _audit(request, "system.offline_mode", "runtime_settings", "offline_mode", detail={"offline_mode": enabled}, actor_role=actor_role)
    event_bus.notify("health", {"offline_mode": enabled})
    if not enabled and CLOUD_ENV.exists():
        _ensure_outbox_worker()
    return {
        "ok": True,
        "offline_mode": enabled,
        "cloud_configured": CLOUD_ENV.exists(),
        "local_base_url": _request_base_url(request),
    }


@router.get("/preflight")
def api_preflight(request: Request):
    _require_localhost(request)
    return _run_preflight_checks()


@router.get("/outbox")
def api_outbox(request: Request, limit: int = 50):
    _require_localhost(request)
    return platform_store.list_outbox(limit=limit)


@router.post("/outbox/{media_id}/retry")
def api_outbox_retry(media_id: UUID, request: Request):
    actor_role, _ = _require_localhost(request)
    item = _get_item_or_404(media_id)
    updated = platform_store.retry_upload_now(str(media_id))
    if updated is None:
        result = _start_cloud_upload(media_id, item)
        _audit(request, "outbox.retry", "upload_outbox", str(media_id), actor_role=actor_role)
        return {"ok": True, "item": result}
    _audit(request, "outbox.retry", "upload_outbox", str(media_id), actor_role=actor_role)
    _ensure_outbox_worker()
    if CLOUD_ENV.exists():
        key = str(media_id)
        with _cloud_lock:
            if key not in _cloud_inflight and Path(item.processed).is_file():
                _cloud_inflight.add(key)
                records = _cloud_records()
                records[key] = {"status": "uploading"}
                _save_cloud_records(records)
                threading.Thread(target=_cloud_upload, args=(key, Path(item.processed), _is_two_up_item(item)), daemon=True).start()
    event_bus.notify("outbox", {"media_id": str(media_id), "state": "pending"})
    return {"ok": True, "item": updated}


@router.get("/audit-events")
def api_audit_events(request: Request, limit: int = 50):
    _require_localhost(request)
    return {"events": platform_store.list_audit_events(limit=limit)}


@router.get("/events")
async def api_events(request: Request):
    """Server-Sent Events stream for real-time queue, print, outbox, and health updates."""
    subscriber = event_bus.subscribe()

    async def event_stream():
        try:
            try:
                queue_snap = guest_queue_store.snapshot()
                unknown_jobs = guest_queue_store.unknown_print_jobs()
                outbox_counts = platform_store.list_outbox(limit=1)["counts"]
            except Exception:
                queue_snap = {"entries": [], "active_code": None}
                unknown_jobs = []
                outbox_counts = {"pending": 0, "uploading": 0, "failed": 0, "ready": 0}
            snapshot = {
                "version": event_bus.version,
                "queue": queue_snap,
                "unknown_print_jobs": unknown_jobs,
                "outbox_counts": outbox_counts,
                "offline_mode": _is_offline_mode(),
                "at": datetime.now(timezone.utc).isoformat(),
            }
            yield f"event: snapshot\ndata: {json.dumps(snapshot, ensure_ascii=False)}\n\n"
            while not await request.is_disconnected():
                try:
                    evt = await asyncio.to_thread(subscriber.get, True, 15.0)
                    # Coalesce burst events queued while yielding
                    while True:
                        try:
                            evt = subscriber.get_nowait()
                        except queue_mod.Empty:
                            break
                    yield f"event: update\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"
                except queue_mod.Empty:
                    yield ": ping\n\n"
        finally:
            event_bus.unsubscribe(subscriber)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/capture-state")
def api_capture_state():
    """Fast local status used by the kiosk loading screen.

    This endpoint deliberately avoids camera autodetection/config reads so it
    can be polled frequently without competing with the live-view USB stream.
    """
    backend = next(iter(container.acquisition_service._backends), None)
    return {
        "busy": bool(getattr(backend, "capture_busy", False)) if backend else False,
        "busy_ms": int(getattr(backend, "capture_busy_ms", 0)) if backend else 0,
        "shutter_locked": bool(getattr(backend, "shutter_locked", False)) if backend else False,
    }


@router.post("/approval/{capture_id}/{decision}")
def api_decide_capture(capture_id: UUID, decision: str, request: Request):
    """Accept one review decision across both booth and operator windows."""
    _require_localhost(request)
    if decision not in ("confirm", "reject"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ไม่พบคำสั่งนี้")
    with _approval_action_lock:
        processing = container.processing_service
        job = processing._workflow_jobmodel
        current_state = next(iter(job._status_sm.configuration)).id if job else None
        if (
            current_state != "approval"
            or getattr(job, "_approval_id", None) != capture_id
            or not processing.is_user_input_requested()
        ):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="ภาพนี้ได้รับการยืนยันหรือเปลี่ยนไปแล้ว")
        try:
            if decision == "confirm":
                processing.continue_process()
            else:
                processing.reject_capture()
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="ภาพนี้ได้รับการยืนยันหรือเปลี่ยนไปแล้ว") from exc
    return {"ok": True, "capture_id": str(capture_id), "decision": decision}


def _detect_canon_cameras() -> list[dict]:
    global _camera_cache
    with _camera_detect_lock:
        cached_at, cached_items = _camera_cache
        if time.monotonic() - cached_at < _CAMERA_DETECT_CACHE_TTL_SEC:
            return list(cached_items)
        items: list[dict] = []
        try:
            import gphoto2 as gp

            context = gp.Context()
            cameras = gp.Camera.autodetect(context)
            for index in range(cameras.count()):
                model = cameras.get_name(index)
                port = cameras.get_value(index)
                items.append({"model": model, "port": port, "supported": "canon" in model.lower()})
        except Exception as exc:
            logger.debug("Canon camera detection note: %s", exc)
        _camera_cache = (time.monotonic(), items)
        return list(items)


@router.get("/hardware/cameras")
def api_hardware_cameras():
    return {
        "brand": "Canon",
        "connection": "USB / PTP ผ่าน gphoto2",
        "detected": _detect_canon_cameras(),
        "note": "รองรับกล้อง Canon ที่ libgphoto2 ตรวจพบ โดยเชื่อมต่อครั้งละหนึ่งตัว",
    }


@router.get("/templates")
def api_templates(request: Request):
    active_id = _active_template_id()
    templates = _load_templates()
    return {
        "active_template_id": active_id,
        "capture_busy": bool(container.processing_service.is_occupied() or container.processing_service._workflow_jobmodel is not None),
        "templates": [_public_template(item) for item in templates],
        "hidden_builtin_templates": [_public_template(item) for item in _default_templates() if item["id"] in _hidden_builtin_ids()],
        "chooser_url": f"{_request_base_url(request)}/template-chooser.html",
        "chooser_qr_url": "/api/share/template-chooser-qr.svg",
    }


@router.get("/templates/{template_id}/asset/{asset_type}")
def api_template_asset(template_id: str, asset_type: str):
    if asset_type not in {"background", "overlay"}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ไม่พบไฟล์เทมเพลต")
    template = next((item for item in _load_templates() if item.get("id") == template_id), None)
    if not template:
        template = next((item for item in _default_templates() if item.get("id") == template_id), None)
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ไม่พบเทมเพลตที่เลือก")
    filepath = template.get(f"{asset_type}_path")
    if not filepath or not Path(filepath).is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="เทมเพลตนี้ไม่มีไฟล์ดังกล่าว")
    return FileResponse(Path(filepath), headers={"Cache-Control": "public, max-age=86400"})


def _template_is_in_use(template_id: str) -> bool:
    capturing = container.processing_service.is_occupied() or container.processing_service._workflow_jobmodel is not None
    return capturing and _active_template_id() == template_id


def _slot_val(slot: Any, key: str) -> int:
    return int(slot[key] if isinstance(slot, dict) else getattr(slot, key))


def _validate_template_layout(
    print_layout: str,
    canvas_width: int,
    canvas_height: int,
    slots: list[Any],
    right_slots: list[Any] | None = None,
) -> None:
    if not slots or canvas_width < 600 or canvas_width > 2400 or canvas_height < 900 or canvas_height > 3600:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="ขนาดกระดาษหรือจำนวนช่องภาพไม่ถูกต้อง")
    if print_layout == "two_up":
        if canvas_width * 3 != canvas_height * 2 or canvas_width % 2 or len(slots) > 3:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="สตริปคู่ต้องเป็นกระดาษแนวตั้งอัตราส่วน 4×6 และมีไม่เกิน 3 ช่องภาพ")
        if any(_slot_val(slot, "x") + _slot_val(slot, "width") > canvas_width // 2 for slot in slots):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="ช่องภาพของสตริปคู่ต้องอยู่ในครึ่งซ้ายของกระดาษ")
        if right_slots is not None:
            if len(right_slots) != len(slots):
                raise HTTPException(status_code=422, detail="จำนวนช่องภาพฝั่งขวาต้องเท่าฝั่งซ้าย")
            if any(_slot_val(slot, "x") < canvas_width // 2 or _slot_val(slot, "x") + _slot_val(slot, "width") > canvas_width for slot in right_slots):
                raise HTTPException(status_code=422, detail="ช่องภาพฝั่งขวาต้องอยู่ในครึ่งขวาของกระดาษ")
    elif right_slots is not None:
        raise HTTPException(status_code=422, detail="กำหนดช่องภาพฝั่งขวาได้เฉพาะสตริปคู่")
    for slot in (*slots, *(right_slots or ())):
        x = _slot_val(slot, "x")
        y = _slot_val(slot, "y")
        w = _slot_val(slot, "width")
        h = _slot_val(slot, "height")
        if x < 0 or y < 0 or w < 40 or h < 40 or x + w > canvas_width or y + h > canvas_height:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="มีช่องภาพอยู่นอกขอบเทมเพลต")


def _template_manifest_hash(manifest: dict[str, Any]) -> str:
    canonical = json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _template_assets_hash(record: dict[str, Any]) -> str:
    hasher = hashlib.sha256()
    hasher.update(json.dumps(_public_template(record), sort_keys=True, ensure_ascii=False).encode("utf-8"))
    for key in ("background_path", "overlay_path"):
        path_str = record.get(key)
        if path_str and Path(path_str).is_file():
            hasher.update(Path(path_str).read_bytes())
    return hasher.hexdigest()


@router.post("/templates", status_code=status.HTTP_201_CREATED)
def api_save_template(payload: TemplateSavePayload, request: Request):
    _require_localhost(request)
    if (payload.background_data and payload.remove_background) or (payload.overlay_data and payload.remove_overlay):
        raise HTTPException(status_code=422, detail="เลือกอัปโหลดหรือลบภาพเดิมอย่างใดอย่างหนึ่ง")
    _validate_template_layout(payload.print_layout, payload.canvas_width, payload.canvas_height, payload.slots, payload.right_slots)

    with _store_lock:
        templates = _load_templates()
        template_id = payload.id or f"custom-{uuid4().hex[:10]}"
        existing = next((item for item in templates if item["id"] == template_id), None)
        if template_id in {item["id"] for item in _default_templates()}:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="ไม่สามารถแก้ไขเทมเพลตมาตรฐานได้ ให้บันทึกเป็นเทมเพลตใหม่")
        if existing and _template_is_in_use(template_id):
            raise HTTPException(status_code=409, detail="เทมเพลตนี้กำลังใช้ถ่ายภาพ กรุณารอให้ชุดนี้เสร็จก่อนแก้ไข")

        background_path = existing.get("background_path") if existing and not payload.remove_background else None
        overlay_path = existing.get("overlay_path") if existing and not payload.remove_overlay else None
        pending_moves: list[tuple[Path, Path]] = []
        try:
            if payload.background_data:
                output = TEMPLATE_DIR / f"{template_id}-background.png"
                temp_output = TEMPLATE_DIR / f"{template_id}-background.{uuid4().hex}.tmp"
                _write_template_asset(payload.background_data, temp_output, payload.canvas_width, payload.canvas_height, payload.print_layout)
                pending_moves.append((temp_output, output))
                background_path = str(output)
            if payload.overlay_data:
                output = TEMPLATE_DIR / f"{template_id}-overlay.png"
                temp_output = TEMPLATE_DIR / f"{template_id}-overlay.{uuid4().hex}.tmp"
                _write_template_asset(payload.overlay_data, temp_output, payload.canvas_width, payload.canvas_height, payload.print_layout)
                pending_moves.append((temp_output, output))
                overlay_path = str(output)
            for temp_output, final_output in pending_moves:
                temp_output.replace(final_output)
        except Exception:
            for temp_output, _ in pending_moves:
                temp_output.unlink(missing_ok=True)
            raise
        if not existing and not background_path:
            background_path = None

        record = {
            "id": template_id,
            "name": payload.name,
            "canvas_width": payload.canvas_width,
            "canvas_height": payload.canvas_height,
            "slots": [slot.model_dump() for slot in payload.slots],
            "right_slots": [slot.model_dump() for slot in payload.right_slots] if payload.right_slots is not None else None,
            "print_layout": payload.print_layout,
            "background_path": background_path,
            "overlay_path": overlay_path,
            "builtin": False,
        }
        templates = [item for item in templates if item["id"] != template_id] + [record]
        _save_custom_templates(templates)
        if existing:
            for key, removed in (("background_path", payload.remove_background), ("overlay_path", payload.remove_overlay)):
                if removed and existing.get(key):
                    resolved = Path(existing[key]).resolve()
                    if resolved.parent == TEMPLATE_DIR.resolve():
                        resolved.unlink(missing_ok=True)
        public_tpl = _public_template(record)
        try:
            platform_store.record_template_version(template_id, public_tpl, _template_assets_hash(record))
        except Exception as exc:
            logger.warning("Could not record template version for %s: %s", template_id, exc)
        _audit(request, "template.save", "template", template_id, detail={"name": payload.name})
        event_bus.notify("templates", {"action": "save", "id": template_id})
        return public_tpl


@router.post("/templates/{template_id}/activate")
def api_activate_template(template_id: str, request: Request):
    actor_role, _ = _require_localhost(request)
    with _store_lock:
        if container.processing_service.is_occupied() or container.processing_service._workflow_jobmodel is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="เปลี่ยนเทมเพลตระหว่างถ่ายภาพไม่ได้")
        template = _find_template(template_id)
        _activate_template(template)
        _audit(request, "template.activate", "template", template_id, actor_role=actor_role)
        event_bus.notify("templates", {"action": "activate", "id": template_id})
        return {"ok": True, "active_template_id": template_id, "capture_count": len(template["slots"])}


@router.post("/template-chooser/select")
def api_template_chooser_select(payload: QueueTemplatePayload, request: Request):
    with _store_lock:
        if container.processing_service.is_occupied() or container.processing_service._workflow_jobmodel is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="กำลังถ่ายภาพอยู่ กรุณารอให้ชุดนี้เสร็จก่อนเปลี่ยนกรอบ")
        template = _find_template(payload.template_id)
        _activate_template(template)
        _audit(request, "template.chooser_select", "template", template["id"], actor_role="guest")
        event_bus.notify("templates", {"action": "activate", "id": template["id"]})
        return {"ok": True, "active_template_id": template["id"], "capture_count": len(template["slots"])}


@router.delete("/templates/{template_id}")
def api_delete_template(template_id: str, request: Request):
    actor_role, _ = _require_localhost(request)
    with _store_lock:
        templates = _load_templates()
        template = next((item for item in templates if item["id"] == template_id), None)
        if not template:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ไม่พบเทมเพลต")
        if _template_is_in_use(template_id):
            raise HTTPException(status_code=409, detail="เทมเพลตนี้กำลังใช้ถ่ายภาพ กรุณารอให้ชุดนี้เสร็จก่อนลบ")
        remaining = [item for item in templates if item["id"] != template_id]
        if not remaining:
            raise HTTPException(status_code=409, detail="ต้องเหลือเทมเพลตอย่างน้อยหนึ่งแบบ")
        if _active_template_id() == template_id:
            _activate_template(remaining[0])
        if template.get("builtin"):
            _save_hidden_builtin_ids(_hidden_builtin_ids() | {template_id})
            _audit(request, "template.hide", "template", template_id, actor_role=actor_role)
            event_bus.notify("templates", {"action": "hide", "id": template_id})
            return {"ok": True, "hidden": True}
        _save_custom_templates(remaining)
        for key in ("background_path", "overlay_path"):
            filepath = template.get(key)
            if filepath:
                resolved = Path(filepath).resolve()
                if resolved.parent == TEMPLATE_DIR.resolve():
                    resolved.unlink(missing_ok=True)
        _audit(request, "template.delete", "template", template_id, actor_role=actor_role)
        event_bus.notify("templates", {"action": "delete", "id": template_id})
        return {"ok": True}


@router.post("/templates/{template_id}/restore")
def api_restore_builtin_template(template_id: str, request: Request):
    _require_localhost(request)
    with _store_lock:
        if template_id not in {item["id"] for item in _default_templates()} or template_id not in _hidden_builtin_ids():
            raise HTTPException(status_code=404, detail="ไม่พบเทมเพลตมาตรฐานที่ซ่อนไว้")
        _save_hidden_builtin_ids(_hidden_builtin_ids() - {template_id})
        return _public_template(_find_template(template_id))


@router.get("/hardware/printers")
def api_hardware_printers():
    printers: list[dict] = []
    default_printer = None
    try:
        result = subprocess.run(["lpstat", "-d"], capture_output=True, text=True, timeout=4)
        match = re.search(r":\s*([^\n\r]+)", result.stdout)
        default_printer = match.group(1).strip() if match else None
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        result = subprocess.run(["lpstat", "-p"], capture_output=True, text=True, timeout=4)
        for line in result.stdout.splitlines():
            match = re.match(r"(?:printer|เครื่องพิมพ์)\s+(\S+)\s+(.*)", line, flags=re.IGNORECASE)
            if match:
                status_text = match.group(2).strip()
                online = _printer_is_available(status_text)
                printers.append(
                    {
                        "name": match.group(1),
                        "default": match.group(1) == default_printer,
                        "status": status_text,
                        "online": online,
                    }
                )
    except (OSError, subprocess.SubprocessError):
        pass
    with _store_lock:
        profiles = _load_printer_profiles()
        existing_printers = {item.get("printer_name") for item in profiles.get("profiles", [])}
        existing_ids = {item.get("id") for item in profiles.get("profiles", [])}
        added = False
        for printer in printers:
            pname = str(printer.get("name") or "").strip()
            if (
                not pname
                or pname in existing_printers
                or not re.fullmatch(r"[A-Za-z0-9_@][A-Za-z0-9_@.-]*", pname)
            ):
                continue
            slug = re.sub(r"[^a-z0-9]+", "-", pname.lower()).strip("-") or "printer"
            candidate_id = f"auto-{slug[:38]}-4x6"
            suffix = 2
            while candidate_id in existing_ids:
                candidate_id = f"auto-{slug[:33]}-{suffix}-4x6"
                suffix += 1
            profiles["profiles"].append(
                {
                    "id": candidate_id,
                    "name": f"{pname.replace('_', ' ')} · 4×6 ไร้ขอบ",
                    "printer_name": pname,
                    "paper_size": _default_paper_size_for_printer(pname),
                    "copies": 1,
                }
            )
            existing_printers.add(pname)
            existing_ids.add(candidate_id)
            added = True
        if added:
            _save_printer_profiles(profiles)
            _invalidate_print_caches()
    return {
        "printers": printers,
        "default_printer": default_printer,
        "profiles": profiles["profiles"],
        "active_profile_id": profiles.get("active_profile_id"),
        "paper_sizes": [
            {"id": "EPKG.NMgn", "name": "4×6 นิ้ว ไร้ขอบ (EPSON)"},
            {"id": "4x6.Fullbleed", "name": "4×6 นิ้ว ไร้ขอบ"},
            {"id": "6x8", "name": "6×8 นิ้ว"},
            {"id": "A4.NMgn", "name": "A4 ไร้ขอบ (EPSON)"},
            {"id": "iso_a4_210x297mm", "name": "A4"},
        ],
    }


@router.post("/printer-profiles", status_code=status.HTTP_201_CREATED)
def api_save_printer_profile(payload: PrinterProfilePayload, request: Request):
    actor_role, _ = _require_localhost(request)
    with _store_lock:
        data = _load_printer_profiles()
        profile_id = payload.id or f"printer-{uuid4().hex[:10]}"
        profile = {**payload.model_dump(), "id": profile_id}
        data["profiles"] = [item for item in data["profiles"] if item.get("id") != profile_id] + [profile]
        if not data.get("active_profile_id"):
            data["active_profile_id"] = profile_id
        _save_printer_profiles(data)
        _audit(request, "printer.profile.save", "printer_profile", profile_id, detail={"printer_name": payload.printer_name, "paper_size": payload.paper_size}, actor_role=actor_role)
        _invalidate_print_caches()
        event_bus.notify("printers", {"action": "save", "id": profile_id})
        return profile


@router.post("/printer-profiles/{profile_id}/activate")
def api_activate_printer_profile(profile_id: str, request: Request):
    actor_role, _ = _require_localhost(request)
    with _store_lock:
        data = _load_printer_profiles()
        if not any(item.get("id") == profile_id for item in data["profiles"]):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ไม่พบโปรไฟล์เครื่องพิมพ์")
        data["active_profile_id"] = profile_id
        _save_printer_profiles(data)
        _audit(request, "printer.profile.activate", "printer_profile", profile_id, actor_role=actor_role)
        _invalidate_print_caches()
        event_bus.notify("printers", {"action": "activate", "id": profile_id})
        return {"ok": True, "active_profile_id": profile_id}


@router.delete("/printer-profiles/{profile_id}")
def api_delete_printer_profile(profile_id: str, request: Request):
    actor_role, _ = _require_localhost(request)
    with _store_lock:
        data = _load_printer_profiles()
        if len(data["profiles"]) <= 1:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="ต้องเหลือโปรไฟล์เครื่องพิมพ์อย่างน้อยหนึ่งรายการ")
        data["profiles"] = [item for item in data["profiles"] if item.get("id") != profile_id]
        if data.get("active_profile_id") == profile_id:
            data["active_profile_id"] = data["profiles"][0]["id"]
        _save_printer_profiles(data)
        _audit(request, "printer.profile.delete", "printer_profile", profile_id, actor_role=actor_role)
        _invalidate_print_caches()
        event_bus.notify("printers", {"action": "delete", "id": profile_id})
        return {"ok": True}


def _queue_printed_copies(queue_code: str | None) -> int:
    if not queue_code:
        return 0
    snapshot = guest_queue_store.snapshot()
    entry = next((item for item in snapshot.get("entries", []) if item.get("code") == queue_code), None)
    if not entry:
        return 0
    return sum(max(1, int(job.get("copies") or 1)) for job in (entry.get("print_jobs") or []) if job.get("state") != "canceled")


@router.post("/print/{id}")
def api_print_with_profile(id: UUID, payload: PrintPayload, request: Request):
    actor_role, _ = _require_station_action(request, "print", "จับคู่โต๊ะพิมพ์จาก Mac เครื่องหลักก่อนส่งงาน")
    idem_scope = f"print.{id}"
    idem_key = _idempotency_key(request)
    if idem_key:
        cached = platform_store.get_idempotency(idem_scope, idem_key)
        if cached:
            return JSONResponse(status_code=cached[0], content=cached[1])
    item = _get_item_or_404(id)
    if actor_role == "station-print" and (not item.show_in_gallery or item.media_type not in {"image", "collage"}):
        raise HTTPException(status_code=404, detail="ไม่พบภาพที่เปิดให้พิมพ์")
    profile = _active_printer_profile(payload.profile_id)
    queue_code = guest_queue_store.code_for_job(str(item.job_identifier))
    output = _send_to_printer(item.processed, profile, payload.copies, title=f"{queue_code} Photobooth" if queue_code else None)
    print_job_id = cups_job_id(output)
    if queue_code:
        guest_queue_store.record_print(str(item.job_identifier), str(item.id), print_job_id, payload.copies, profile["printer_name"])
    result = {"ok": True, "profile": profile, "output": output, "print_job_id": print_job_id, "queue_code": queue_code}
    if idem_key:
        platform_store.save_idempotency(idem_scope, idem_key, 200, result)
    _audit(request, "print.submit", "media", str(id), detail={"queue_code": queue_code, "printer": profile["printer_name"], "copies": payload.copies, "cups_id": print_job_id}, actor_role=actor_role)
    _invalidate_print_caches()
    event_bus.notify("print", {"action": "submit", "queue_code": queue_code, "cups_id": print_job_id})
    return result


@router.post("/printer-profiles/{profile_id}/test")
def api_test_printer_profile(profile_id: str, request: Request):
    _require_localhost(request)
    profile = _active_printer_profile(profile_id)
    output_dir = Path("tmp")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"printer-test-{uuid4().hex[:10]}.jpg"
    try:
        image = Image.new("RGB", (1200, 1800), "white")
        from PIL import ImageDraw

        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 40, 1160, 1760), outline="black", width=8)
        draw.text((90, 110), "PHOTOBOOTH PRINTER TEST", fill="black")
        draw.text((90, 175), f"Printer: {profile['printer_name']}", fill="black")
        draw.text((90, 230), f"Paper: {profile['paper_size']}", fill="black")
        image.save(output_path, "JPEG", quality=92)
        output = _send_to_printer(output_path, profile, 1)
    finally:
        output_path.unlink(missing_ok=True)
    _invalidate_print_caches()
    return {"ok": True, "output": output}


@router.get("/template-sources/{id}")
def api_template_sources(id: UUID):
    collage = _get_item_or_404(id)
    related = container.mediacollection_service.get_items_relto_job(collage.job_identifier)
    sources = sorted(
        (item for item in related if item.media_type == "image"),
        key=lambda item: (item.created_at, item.rowid),
    )
    if len(sources) < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="ชุดภาพนี้ไม่มีภาพต้นฉบับสำหรับจัดเทมเพลต",
        )
    sources = sources[:6]
    matching_templates = [item for item in _load_templates() if len(item["slots"]) == len(sources)]
    return {
        "collage_id": str(collage.id),
        "capture_count": len(sources),
        "suggested_template_id": matching_templates[0]["id"] if matching_templates else None,
        "sources": [
            {
                "id": str(item.id),
                "full_url": f"/media/full/{item.id}",
                "thumbnail_url": f"/media/thumbnail/{item.id}",
            }
            for item in sources
        ],
    }


@router.post("/compose-template", status_code=status.HTTP_201_CREATED)
def api_compose_template(payload: TemplateComposePayload, request: Request):
    template = _find_template(payload.template_id)
    if len(payload.source_ids) != len(template["slots"]):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="จำนวนภาพไม่ตรงกับจำนวนช่องของเทมเพลต")
    if len(set(payload.source_ids)) != len(payload.source_ids):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="ภาพต้นฉบับต้องไม่ซ้ำกัน")

    prefix = "data:image/png;base64,"
    if not payload.image_data.startswith(prefix):
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="รองรับไฟล์ภาพ PNG เท่านั้น")
    try:
        decoded = base64.b64decode(payload.image_data[len(prefix) :], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="ข้อมูลรูปภาพไม่ถูกต้อง") from exc
    if len(decoded) > 22_000_000:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="ไฟล์ภาพมีขนาดใหญ่เกิน 22 MB")

    try:
        with Image.open(BytesIO(decoded)) as check_image:
            check_image.verify()
        with Image.open(BytesIO(decoded)) as source_image:
            if source_image.width != template["canvas_width"] or source_image.height != template["canvas_height"]:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="ขนาดภาพเทมเพลตไม่ถูกต้อง")
            output_image = source_image.convert("RGB")
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="ไม่สามารถอ่านไฟล์ภาพได้") from exc

    source_items = [_get_item_or_404(item_id) for item_id in payload.source_ids]
    if any(item.media_type != "image" for item in source_items):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="แหล่งภาพต้องเป็นภาพถ่ายต้นฉบับ")
    job_ids = {item.job_identifier for item in source_items}
    if len(job_ids) != 1:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="ภาพต้นฉบับต้องมาจากชุดถ่ายเดียวกัน")

    output_path = Path(PATH_PROCESSED, f"event-template-{payload.template_id}-{uuid4().hex[:12]}.jpg")
    output_image.save(output_path, format="JPEG", quality=94, optimize=True, progressive=True)
    mediaitem = Mediaitem(
        media_type="collage",
        job_identifier=source_items[0].job_identifier,
        processed=output_path,
        captured_original=None,
        pipeline_config={
            "custom_template": payload.template_id,
            "print_layout": template.get("print_layout", "full"),
            "source_ids": [str(item_id) for item_id in payload.source_ids],
            "title": payload.title,
            "subtitle": payload.subtitle,
        },
        show_in_gallery=True,
    )
    try:
        container.mediacollection_service.add_item(mediaitem)
    except Exception as exc:
        output_path.unlink(missing_ok=True)
        logger.exception(exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="บันทึกภาพสำเร็จรูปไม่สำเร็จ") from exc

    related = container.mediacollection_service.get_items_relto_job(mediaitem.job_identifier)
    queue_code = guest_queue_store.code_for_job(str(mediaitem.job_identifier))
    if queue_code:
        guest_queue_store.set_media(str(mediaitem.job_identifier), [str(mediaitem.id)], [str(item.id) for item in related])
    project_id = (guest_queue_store.project_for_job(str(mediaitem.job_identifier)) if queue_code else None) or gallery_folder_store.snapshot()["active_id"]
    if project_id:
        try:
            gallery_folder_store.assign([str(item.id) for item in related], project_id)
        except ValueError as exc:
            logger.warning("Could not assign gallery folder %s after compose: %s", project_id, exc)

    cloud_status = _queue_or_start_cloud_share(mediaitem)

    base_url = _request_base_url(request)
    share_url = f"{base_url}/api/share/download-page/{mediaitem.id}"
    return {
        "ok": True,
        "id": str(mediaitem.id),
        "image_url": f"/media/full/{mediaitem.id}",
        "share_url": share_url,
        "qr_url": f"/api/share/qr/{mediaitem.id}.svg",
        "cloud_status": cloud_status,
    }


async def _read_uploaded_photos_rgb(photos: list[UploadFile]) -> list[Image.Image]:
    """Read, validate, EXIF-orient, and normalize uploaded JPG/PNG photos to RGB."""
    images: list[Image.Image] = []
    total_bytes = 0
    for photo in photos:
        raw = await photo.read(15_000_001)
        total_bytes += len(raw)
        if len(raw) > 15_000_000 or total_bytes > 60_000_000:
            raise HTTPException(status_code=413, detail="ไฟล์ภาพใหญ่เกินกำหนด (ไม่เกิน 15 MB ต่อภาพ และ 60 MB ต่อชุด)")
        try:
            with Image.open(BytesIO(raw)) as check_image:
                if check_image.format not in {"JPEG", "PNG"} or check_image.width * check_image.height > 40_000_000:
                    raise HTTPException(status_code=422, detail="รองรับ JPG/PNG ขนาดไม่เกิน 40 ล้านพิกเซล")
                check_image.verify()
            with Image.open(BytesIO(raw)) as source_image:
                oriented = ImageOps.exif_transpose(source_image)
                if oriented.mode in {"RGBA", "LA"} or "transparency" in oriented.info:
                    rgba = oriented.convert("RGBA")
                    normalized = Image.new("RGB", rgba.size, "white")
                    normalized.paste(rgba, mask=rgba.getchannel("A"))
                else:
                    normalized = oriented.convert("RGB")
                images.append(normalized)
        except HTTPException:
            raise
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
            raise HTTPException(status_code=422, detail="มีไฟล์ภาพที่เปิดไม่ได้ กรุณาใช้ JPG หรือ PNG") from exc
    return images


def _compose_template_from_images(template: dict, photos: list[Image.Image]) -> Image.Image:
    """Compose RGB photos into a template canvas with optional background and top overlay."""
    width = max(1, int(template["canvas_width"]))
    height = max(1, int(template["canvas_height"]))
    canvas = Image.new("RGBA", (width, height), (255, 255, 255, 255))

    background_path = template.get("background_path")
    if background_path and Path(background_path).is_file():
        with Image.open(background_path) as bg_img:
            bg_rgba = bg_img.convert("RGBA")
            if bg_rgba.size != (width, height):
                bg_rgba = bg_rgba.resize((width, height), Image.Resampling.LANCZOS)
            canvas.alpha_composite(bg_rgba)

    slots = list(template["slots"])
    if template.get("print_layout") == "two_up":
        right_slots = template.get("right_slots")
        if right_slots:
            slots.extend(right_slots)
        else:
            half_width = width // 2
            slots.extend({**slot, "x": int(slot["x"]) + half_width} for slot in template["slots"])

    for idx, slot in enumerate(slots):
        photo = photos[idx % len(photos)].convert("RGBA")
        slot_w = max(1, int(slot["width"]))
        slot_h = max(1, int(slot["height"]))
        fitted = ImageOps.fit(photo, (slot_w, slot_h), method=Image.Resampling.LANCZOS)
        canvas.paste(fitted, (int(slot["x"]), int(slot["y"])), fitted)

    overlay_path = template.get("overlay_path")
    if overlay_path and Path(overlay_path).is_file():
        with Image.open(overlay_path) as ov_img:
            ov_rgba = ov_img.convert("RGBA")
            if ov_rgba.size != (width, height):
                ov_rgba = ov_rgba.resize((width, height), Image.Resampling.LANCZOS)
            canvas.alpha_composite(ov_rgba)

    return canvas.convert("RGB")


def _queue_or_start_cloud_share(mediaitem: Mediaitem) -> str:
    if _is_offline_mode():
        if CLOUD_ENV.exists():
            try:
                platform_store.enqueue_upload(str(mediaitem.id), str(mediaitem.processed), _is_two_up_item(mediaitem))
            except Exception:
                pass
        return "offline"
    if CLOUD_ENV.exists():
        with _cloud_lock:
            records = _cloud_records()
            records[str(mediaitem.id)] = {"status": "pending"}
            _save_cloud_records(records)
    cloud = _cloud_status(mediaitem.id, mediaitem)
    return cloud["status"] if cloud else "disabled"


def _save_composed_photo_set(
    template: dict,
    source_images: list[Image.Image],
    output_image: Image.Image,
    *,
    source_prefix: str,
    output_prefix: str,
    output_media_type: str,
    source_config: dict[str, Any],
    extra_output_config: dict[str, Any],
    error_detail: str,
) -> Mediaitem:
    """Persist hidden source captures and one visible composed media item atomically."""
    job_id = uuid4()
    source_items: list[Mediaitem] = []
    output_paths: list[Path] = []
    added_items: list[Mediaitem] = []
    try:
        Path(PATH_PROCESSED).mkdir(parents=True, exist_ok=True)
        for image in source_images:
            source_path = Path(PATH_PROCESSED, f"{source_prefix}-{uuid4().hex}.jpg")
            output_paths.append(source_path)
            image.save(source_path, format="JPEG", quality=94, optimize=True)
            item = Mediaitem(
                id=uuid4(),
                media_type="image",
                job_identifier=job_id,
                processed=source_path,
                captured_original=None,
                pipeline_config=dict(source_config),
                show_in_gallery=False,
            )
            container.mediacollection_service.add_item(item)
            added_items.append(item)
            source_items.append(item)

        output_path = Path(PATH_PROCESSED, f"{output_prefix}-{uuid4().hex}.jpg")
        output_paths.append(output_path)
        output_image.save(output_path, format="JPEG", quality=94, optimize=True, progressive=True)
        mediaitem = Mediaitem(
            id=uuid4(),
            media_type=output_media_type,
            job_identifier=job_id,
            processed=output_path,
            captured_original=None,
            pipeline_config={
                "custom_template": template["id"],
                "print_layout": template.get("print_layout", "full"),
                "source_ids": [str(item.id) for item in source_items],
                **extra_output_config,
            },
            show_in_gallery=True,
        )
        container.mediacollection_service.add_item(mediaitem)
        added_items.append(mediaitem)
    except Exception as exc:
        for item in reversed(added_items):
            try:
                container.mediacollection_service.delete_item(item)
            except Exception:
                logger.exception("Could not roll back saved media %s", item.id)
        for path in output_paths:
            path.unlink(missing_ok=True)
        logger.exception("Could not save composed photo set")
        raise HTTPException(status_code=500, detail=error_detail) from exc

    try:
        folder_id = gallery_folder_store.snapshot()["active_id"]
        if folder_id:
            gallery_folder_store.assign([str(item.id) for item in added_items], folder_id)
    except Exception:
        logger.exception("Saved composed photo set, but could not assign gallery folder")
    return mediaitem


@router.post("/compose-template-from-files", status_code=status.HTTP_201_CREATED)
async def api_compose_template_from_files(
    request: Request,
    template_id: str = Form(...),
    title: str = Form(""),
    subtitle: str = Form(""),
    composed_image: UploadFile = File(...),
    photos: list[UploadFile] = File(...),
):
    """Save an operator-imported photo set without creating or advancing a guest queue."""
    _require_localhost(request, "นำเข้าภาพได้จาก Mac เครื่องหลักผ่าน localhost เท่านั้น")
    template = _find_template(template_id)
    if len(photos) != len(template["slots"]):
        raise HTTPException(status_code=422, detail="จำนวนไฟล์ภาพไม่ตรงกับจำนวนช่องของเทมเพลต")
    if len(title) > 120 or len(subtitle) > 180:
        raise HTTPException(status_code=422, detail="ข้อความบนภาพยาวเกินกำหนด")

    rendered_bytes = await composed_image.read(22_000_001)
    if len(rendered_bytes) > 22_000_000:
        raise HTTPException(status_code=413, detail="ภาพสำเร็จรูปมีขนาดใหญ่เกิน 22 MB")
    try:
        with Image.open(BytesIO(rendered_bytes)) as check_image:
            if check_image.format != "PNG" or check_image.size != (template["canvas_width"], template["canvas_height"]):
                raise HTTPException(status_code=422, detail="ขนาดหรือชนิดภาพเทมเพลตไม่ถูกต้อง")
            check_image.verify()
        with Image.open(BytesIO(rendered_bytes)) as rendered_image:
            output_image = rendered_image.convert("RGB")
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(status_code=422, detail="ไม่สามารถอ่านภาพสำเร็จรูปได้") from exc

    imported_images = await _read_uploaded_photos_rgb(photos)
    mediaitem = _save_composed_photo_set(
        template,
        imported_images,
        output_image,
        source_prefix="imported-source",
        output_prefix="event-template-import",
        output_media_type="collage",
        source_config={"imported_from_files": True},
        extra_output_config={"title": title, "subtitle": subtitle, "imported_from_files": True},
        error_detail="บันทึกภาพจากไฟล์ไม่สำเร็จ",
    )
    try:
        cloud_status = _queue_or_start_cloud_share(mediaitem)
    except Exception:
        logger.exception("Imported template saved, but could not start cloud share")
        cloud_status = "failed"

    return {
        "ok": True,
        "id": str(mediaitem.id),
        "image_url": f"/media/full/{mediaitem.id}",
        "share_url": f"{_request_base_url(request)}/api/share/download-page/{mediaitem.id}",
        "qr_url": f"/api/share/qr/{mediaitem.id}.svg",
        "cloud_status": cloud_status,
        "queue_code": None,
    }


@router.get("/cloud-status/{id}")
def api_cloud_status(id: UUID):
    item = _get_item_or_404(id)
    record = _cloud_status(id, item)
    if not record:
        return {"status": "disabled"}
    return record


@router.post("/cloud-share/{id}")
def api_create_cloud_share(id: UUID, request: Request):
    """Operator-only UI action: publish an existing photo or renew an expired link."""
    actor_role, _ = _require_localhost(request)
    item = _get_item_or_404(id)
    if item.media_type not in {"image", "collage"}:
        raise HTTPException(status_code=422, detail="รองรับเฉพาะภาพถ่ายหรือภาพสำเร็จรูป")
    _audit(request, "cloud.share.start", "media", str(id), actor_role=actor_role)
    return _start_cloud_upload(id, item)


@router.get("/download-image/{id}")
def api_download_image(id: UUID, attachment: bool = False):
    item = _get_item_or_404(id)
    two_up = _is_two_up_item(item)
    filename = f"photobooth-{id}-{'2x6' if two_up else 'full'}.jpg"
    headers = {"Cache-Control": "private, max-age=300"}
    if attachment:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    if not two_up:
        return FileResponse(item.processed, media_type="image/jpeg", headers=headers)
    try:
        with Image.open(item.processed) as source:
            left_half = source.crop((0, 0, source.width // 2, source.height)).convert("RGB")
            output = BytesIO()
            left_half.save(output, format="JPEG", quality=94, optimize=True)
    except (OSError, UnidentifiedImageError) as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="ไม่สามารถสร้างภาพ 2×6 ได้") from exc
    return Response(content=output.getvalue(), media_type="image/jpeg", headers=headers)


@router.get("/download-page/{id}", response_class=HTMLResponse)
def api_download_page(id: UUID):
    item = _get_item_or_404(id)
    two_up = _is_two_up_item(item)
    label = "2×6" if two_up else "ภาพเต็ม"
    safe_filename = f"photobooth-{item.id}-{'2x6' if two_up else 'full'}.jpg"
    image_url = f"/api/share/download-image/{item.id}"
    title_json = json.dumps("ภาพจาก Photobooth", ensure_ascii=False)
    body = f"""<!doctype html>
<html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ดาวน์โหลดภาพ Photobooth</title><style>
*{{box-sizing:border-box}}
body{{margin:0;background:radial-gradient(circle at 20% 10%,rgba(251,191,36,.16),transparent 46%),radial-gradient(circle at 82% 85%,rgba(180,83,9,.14),transparent 44%),linear-gradient(180deg,#18120d 0%,#0f0c09 100%);color:#fffbeb;font-family:"IBM Plex Sans Thai Looped","Thonburi","Sukhumvit Set",system-ui,-apple-system,sans-serif;min-height:100vh;display:grid;place-items:center;padding:20px}}
main{{position:relative;z-index:1;width:min(92vw,540px);padding:28px;text-align:center;border-radius:28px;background:linear-gradient(155deg,rgba(24,18,13,.82) 0%,rgba(15,12,9,.92) 100%);backdrop-filter:blur(22px);border:1px solid rgba(251,191,36,.24);box-shadow:0 20px 50px rgba(0,0,0,.55),inset 0 1px rgba(254,243,199,.16)}}
.pill{{display:inline-flex;align-items:center;gap:6px;padding:6px 14px;margin-bottom:10px;border-radius:999px;background:rgba(251,191,36,.16);border:1px solid rgba(251,191,36,.38);color:#fde68a;font-size:12px;font-weight:700}}
h1{{margin:0 0 16px;font-size:1.5rem;font-weight:700}}
img{{width:100%;max-height:68vh;object-fit:contain;border-radius:18px;box-shadow:0 18px 45px rgba(0,0,0,.5);background:#090705}}
.actions{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:18px}}
a,button{{padding:13px 16px;border-radius:999px;font-family:inherit;font-weight:700;font-size:15px;text-decoration:none;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;gap:7px}}
a{{background:linear-gradient(90deg,#fbbf24 0%,#facc15 50%,#f59e0b 100%);color:#231912;border:1px solid rgba(252,211,77,.65);font-weight:800;box-shadow:0 8px 24px rgba(245,158,11,.35)}}
button{{background:rgba(255,255,255,.08);color:#fffbeb;border:1px solid rgba(251,191,36,.22)}}
p{{margin:14px 0 0;color:rgba(254,243,199,.72);font-size:13px}}
@media(max-width:480px){{.actions{{grid-template-columns:1fr}}}}</style></head><body><svg aria-hidden="true" viewBox="0 0 1200 320" preserveAspectRatio="xMidYMax slice" style="position:fixed;inset:auto 0 0;width:100%;height:34vh;pointer-events:none;z-index:0;opacity:.55"><defs><linearGradient id="lkW" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#f59e0b" stop-opacity="0"/><stop offset="100%" stop-color="#b45309" stop-opacity=".28"/></linearGradient></defs><path d="M0,130 Q300,95 600,130 T1200,120 L1200,320 L0,320 Z" fill="url(#lkW)"/><path d="M0,185 Q260,165 560,185 T1200,180" fill="none" stroke="#fde68a" stroke-opacity=".28" stroke-width="1.6"/><path d="M0,240 Q320,222 640,240 T1200,235" fill="none" stroke="#f59e0b" stroke-opacity=".24" stroke-width="1.4"/><g transform="translate(185 215)"><ellipse cx="0" cy="12" rx="34" ry="8" fill="#4d7c0f" stroke="#84cc16" stroke-width="1.5"/><path d="M-24,10 L-16,-2 L-8,9 L0,-3 L8,9 L16,-2 L24,10 Z" fill="#84cc16"/><rect x="-2.5" y="-18" width="5" height="16" fill="#fef3c7"/><path d="M0,-28 Q5,-22 0,-18 Q-5,-22 0,-28 Z" fill="#fde047"/></g><g transform="translate(1015 232) scale(.85)"><ellipse cx="0" cy="12" rx="34" ry="8" fill="#4d7c0f" stroke="#84cc16" stroke-width="1.5"/><path d="M-24,10 L-16,-2 L-8,9 L0,-3 L8,9 L16,-2 L24,10 Z" fill="#84cc16"/><rect x="-2.5" y="-18" width="5" height="16" fill="#fef3c7"/><path d="M0,-28 Q5,-22 0,-18 Q-5,-22 0,-28 Z" fill="#fde047"/></g></svg><main>
<div class="pill">✨ สโมสรนักศึกษาคณะวิทยาศาสตร์ ม.อ. · PHOTOBOOTH STUDIO</div>
<h1>ภาพของคุณพร้อมแล้ว</h1><img src="{image_url}" alt="ภาพ Photobooth สำเร็จรูป">
<div class="actions"><a href="{image_url}?attachment=1" download="{safe_filename}"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/></svg>ดาวน์โหลดภาพ {label}</a><button id="share" type="button"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" x2="15.42" y1="13.51" y2="17.49"/><line x1="15.41" x2="8.59" y1="6.51" y2="10.49"/></svg>แชร์จากอุปกรณ์</button></div>
<p>หากปุ่มแชร์ไม่รองรับ ให้กดดาวน์โหลดภาพแทน</p></main><script>
document.getElementById('share').addEventListener('click',async function(){{
  try{{const response=await fetch('{image_url}');const blob=await response.blob();const file=new File([blob],'{safe_filename}',{{type:blob.type||'image/jpeg'}});
  if(navigator.canShare&&navigator.canShare({{files:[file]}}))await navigator.share({{title:{title_json},files:[file]}});else if(navigator.share)await navigator.share({{title:{title_json},url:location.href}});else throw new Error('not supported');}}
  catch(error){{if(error.name!=='AbortError')alert('อุปกรณ์นี้ไม่รองรับการแชร์โดยตรง กรุณากดดาวน์โหลดภาพ');}}
}});</script></body></html>"""
    return HTMLResponse(body, headers={"Cache-Control": "no-store"})


@router.get("/qr/{id}.svg")
def api_download_qr(id: UUID, request: Request):
    item = _get_item_or_404(id)
    cloud = _cloud_status(id, item)
    if cloud:
        status_name = cloud.get("status")
        if status_name == "expired":
            raise HTTPException(status_code=status.HTTP_410_GONE, detail="ลิงก์ภาพหมดอายุแล้ว เจ้าหน้าที่สามารถกดสร้าง QR ใหม่ได้")
        if status_name == "ready":
            return _qr_svg(cloud["share_url"])
        if status_name not in {"failed", "offline"}:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="ภาพกำลังอัปโหลด ยังไม่มี QR ออนไลน์")
    share_url = f"{_request_base_url(request)}/api/share/download-page/{id}"
    return _qr_svg(share_url)


@router.get("/template-chooser-qr.svg")
def api_template_chooser_qr(request: Request):
    return _qr_svg(f"{_request_base_url(request)}/template-chooser.html")


def _validate_event_policy(policy: dict[str, Any] | None) -> dict[str, Any]:
    if not policy:
        return {}
    cleaned = dict(policy)
    int_bounds = {
        "print_quota_per_session": (1, 20),
        "retake_limit": (0, 10),
        "kiosk_idle_timeout_sec": (10, 600),
        "kiosk_cooldown_sec": (0, 300),
        "kiosk_max_sessions_per_hour": (1, 1000),
        "retention_days": (1, 365),
    }
    for field, (lo, hi) in int_bounds.items():
        if field in cleaned:
            raw_val = cleaned[field]
            if isinstance(raw_val, bool):
                raise HTTPException(status_code=422, detail=f"ค่า {field} ต้องเป็นตัวเลขจำนวนเต็ม")
            try:
                val = int(raw_val)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail=f"ค่า {field} ต้องเป็นตัวเลขจำนวนเต็ม") from exc
            if val < lo or val > hi:
                raise HTTPException(status_code=422, detail=f"ค่า {field} ต้องอยู่ระหว่าง {lo} ถึง {hi}")
            cleaned[field] = val
    for flag in ("auto_print", "require_operator_approval"):
        if flag in cleaned and not isinstance(cleaned[flag], bool):
            raise HTTPException(status_code=422, detail=f"ค่า {flag} ต้องเป็น true หรือ false")
    if "default_language" in cleaned and cleaned["default_language"] not in {"th", "en"}:
        raise HTTPException(status_code=422, detail="default_language ต้องเป็น th หรือ en")
    return cleaned


@router.get("/events-config")
def api_list_events(request: Request):
    _require_localhost(request)
    return {"active_event": platform_store.get_active_event(), "events": platform_store.list_events()}


@router.post("/events-config", status_code=status.HTTP_201_CREATED)
def api_create_event(payload: EventCreatePayload, request: Request):
    actor_role, _ = _require_localhost(request)
    policy = _validate_event_policy(payload.policy)
    created = platform_store.create_event(
        name=payload.name,
        mode=payload.mode,
        policy=policy,
        activate=payload.activate,
        intake_open=payload.intake_open,
    )
    _audit(request, "event.create", "event", created["id"], detail={"name": created["name"], "mode": created["mode"]}, actor_role=actor_role)
    event_bus.notify("events", {"action": "create", "id": created["id"]})
    return created


@router.post("/events-config/{event_id}/clone", status_code=status.HTTP_201_CREATED)
def api_clone_event(event_id: str, payload: EventClonePayload, request: Request):
    actor_role, _ = _require_localhost(request)
    cloned = platform_store.clone_event(event_id, payload.name, activate=payload.activate)
    if cloned is None:
        raise HTTPException(status_code=404, detail="ไม่พบ Event ต้นฉบับ")
    _audit(request, "event.clone", "event", cloned["id"], detail={"source_id": event_id, "name": cloned["name"]}, actor_role=actor_role)
    event_bus.notify("events", {"action": "clone", "id": cloned["id"]})
    return cloned


@router.patch("/events-config/{event_id}")
def api_update_event(event_id: str, payload: EventUpdatePayload, request: Request):
    actor_role, _ = _require_localhost(request)
    policy = _validate_event_policy(payload.policy) if payload.policy is not None else None
    updated = platform_store.update_event(
        event_id,
        name=payload.name,
        mode=payload.mode,
        intake_open=payload.intake_open,
        policy=policy,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="ไม่พบ Event ที่ต้องการแก้ไข")
    _audit(request, "event.update", "event", event_id, detail={"intake_open": updated["intake_open"], "mode": updated["mode"]}, actor_role=actor_role)
    event_bus.notify("events", {"action": "update", "id": event_id})
    return updated


@router.post("/events-config/{event_id}/activate")
def api_activate_event(event_id: str, request: Request):
    actor_role, _ = _require_localhost(request)
    activated = platform_store.activate_event(event_id)
    if activated is None:
        raise HTTPException(status_code=404, detail="ไม่พบ Event ที่ต้องการเปิดใช้งาน")
    _audit(request, "event.activate", "event", event_id, detail={"name": activated["name"]}, actor_role=actor_role)
    event_bus.notify("events", {"action": "activate", "id": event_id})
    return activated


@router.get("/templates/{template_id}/versions")
def api_template_versions(template_id: str, request: Request):
    _require_localhost(request)
    _find_template(template_id)
    return {"template_id": template_id, "versions": platform_store.list_template_versions(template_id)}


@router.get("/templates/{template_id}/export")
def api_export_template(template_id: str, request: Request):
    _require_localhost(request)
    tpl = _find_template(template_id)
    manifest = {
        "id": tpl["id"],
        "name": tpl["name"],
        "canvas_width": int(tpl["canvas_width"]),
        "canvas_height": int(tpl["canvas_height"]),
        "slots": tpl["slots"],
        "right_slots": tpl.get("right_slots"),
        "print_layout": tpl.get("print_layout", "full"),
    }
    checksum = _template_manifest_hash(manifest)
    return {
        "schema_version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "manifest": manifest,
        "sha256": checksum,
    }


@router.post("/templates/import", status_code=status.HTTP_201_CREATED)
def api_import_template(payload: TemplateImportPayload, request: Request):
    actor_role, _ = _require_localhost(request)
    if payload.schema_version != 1:
        raise HTTPException(status_code=422, detail="ไม่รองรับ schema_version ของไฟล์เทมเพลตนี้")
    expected_sha = _template_manifest_hash(payload.manifest)
    if not hmac.compare_digest(expected_sha, payload.sha256.lower()):
        raise HTTPException(status_code=422, detail="SHA-256 checksum ไม่ตรงกับข้อมูลเทมเพลต")
    m = payload.manifest
    try:
        name = str(m["name"]).strip()
        canvas_width = int(m["canvas_width"])
        canvas_height = int(m["canvas_height"])
        slots = list(m["slots"])
        right_slots = list(m["right_slots"]) if m.get("right_slots") is not None else None
        print_layout = str(m.get("print_layout", "full"))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="โครงสร้าง manifest ของเทมเพลตไม่ครบถ้วน") from exc
    if not name or len(name) > 80 or print_layout not in {"full", "two_up"}:
        raise HTTPException(status_code=422, detail="ชื่อหรือรูปแบบการพิมพ์ของเทมเพลตไม่ถูกต้อง")
    _validate_template_layout(print_layout, canvas_width, canvas_height, slots, right_slots)
    raw_id = str(m.get("id") or "").strip().lower()
    builtin_ids = {item["id"] for item in _default_templates()}
    if not raw_id or not re.fullmatch(r"[a-z0-9-]+", raw_id) or raw_id in builtin_ids:
        template_id = f"imported-{uuid4().hex[:8]}"
    else:
        template_id = raw_id[:50]
    with _store_lock:
        templates = _load_templates()
        record = {
            "id": template_id,
            "name": name,
            "canvas_width": canvas_width,
            "canvas_height": canvas_height,
            "slots": [
                {"x": _slot_val(s, "x"), "y": _slot_val(s, "y"), "width": _slot_val(s, "width"), "height": _slot_val(s, "height")}
                for s in slots
            ],
            "right_slots": (
                [{"x": _slot_val(s, "x"), "y": _slot_val(s, "y"), "width": _slot_val(s, "width"), "height": _slot_val(s, "height")} for s in right_slots]
                if right_slots is not None
                else None
            ),
            "print_layout": print_layout,
            "background_path": None,
            "overlay_path": None,
            "builtin": False,
        }
        templates = [item for item in templates if item["id"] != template_id] + [record]
        _save_custom_templates(templates)
        public_tpl = _public_template(record)
        platform_store.record_template_version(template_id, public_tpl, expected_sha)
        _audit(request, "template.import", "template", template_id, detail={"sha256": expected_sha}, actor_role=actor_role)
        event_bus.notify("templates", {"action": "import", "id": template_id})
        return public_tpl


def _count_online_printers() -> int:
    try:
        profiles = _load_printer_profiles()
        names = list(dict.fromkeys(item["printer_name"] for item in profiles.get("profiles", [])))
        online = 0
        for pname in names:
            st = subprocess.run(["lpstat", "-p", pname], capture_output=True, text=True, errors="replace", timeout=2)
            st_text = st.stdout.strip() or st.stderr.strip()
            if st.returncode == 0 and _printer_is_available(st_text):
                online += 1
        return online
    except Exception:
        return 0


@router.get("/kiosk/config")
def api_kiosk_config():
    active_event = platform_store.get_active_event()
    cam_connected = any(backend.is_running() for backend in container.acquisition_service._backends)
    printers_online = _count_online_printers()
    return {
        "event": active_event,
        "policy": active_event["policy"],
        "intake_open": bool(active_event.get("intake_open", True)),
        "hardware": {
            "camera_connected": cam_connected,
            "printers_online": printers_online,
            "ready": bool(active_event.get("intake_open", True)),
        },
        "active_template_id": _active_template_id(),
        "templates": [_public_template(t) for t in _load_templates()],
        "offline_mode": _is_offline_mode(),
    }


@router.post("/kiosk/sessions", status_code=status.HTTP_201_CREATED)
def api_kiosk_create_session(payload: KioskSessionCreatePayload, request: Request):
    idem_key = _idempotency_key(request)
    if idem_key:
        cached = platform_store.get_idempotency("kiosk.session.create", idem_key)
        if cached:
            return JSONResponse(status_code=cached[0], content=cached[1])
    active_event = platform_store.get_active_event()
    if not active_event.get("intake_open", True):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="ขณะนี้ปิดรับเซสชันชั่วคราว")
    policy = active_event.get("policy") or {}
    cooldown_sec = int(policy.get("kiosk_cooldown_sec", 0))
    now_ts = time.time()
    if cooldown_sec > 0:
        last_ts = platform_store.last_kiosk_session_ts(active_event["id"])
        if last_ts is not None and (now_ts - last_ts) < cooldown_sec:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="กรุณารอสักครู่ก่อนเริ่มรอบถัดไป")
    max_per_hour = int(policy.get("kiosk_max_sessions_per_hour", 120))
    if platform_store.count_recent_kiosk_sessions(active_event["id"], now_ts - 3600.0) >= max_per_hour:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="เซสชันถึงขีดจำกัดต่อชั่วโมงแล้ว")
    template_id = payload.template_id or _active_template_id()
    _find_template(template_id)
    session = platform_store.create_kiosk_session(
        active_event["id"],
        station_id=payload.station_id,
        language=payload.language,
        template_id=template_id,
    )
    if idem_key:
        platform_store.save_idempotency("kiosk.session.create", idem_key, status.HTTP_201_CREATED, session)
    _audit(request, "kiosk.session.start", "kiosk_session", session["id"], detail={"template_id": template_id}, actor_role="guest")
    event_bus.notify("kiosk", {"action": "start", "session_id": session["id"]})
    return session


@router.post("/kiosk/sessions/{session_id}/retake")
def api_kiosk_retake_session(session_id: str, request: Request):
    sess = platform_store.get_kiosk_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="ไม่พบเซสชัน Kiosk")
    if sess["status"] == "finalized":
        raise HTTPException(status_code=409, detail="เซสชันนี้เสร็จสิ้นแล้ว ไม่สามารถถ่ายใหม่ได้")
    event = platform_store.get_event(sess["event_id"]) or platform_store.get_active_event()
    retake_limit = int((event.get("policy") or {}).get("retake_limit", 2))
    if sess["retake_count"] >= retake_limit:
        raise HTTPException(status_code=409, detail=f"ใช้สิทธิ์ถ่ายใหม่ครบ {retake_limit} ครั้งแล้ว")
    updated = platform_store.update_kiosk_session(session_id, retake_count=sess["retake_count"] + 1, status="capturing")
    _audit(request, "kiosk.session.retake", "kiosk_session", session_id, detail={"retake_count": updated["retake_count"]}, actor_role="guest")
    event_bus.notify("kiosk", {"action": "retake", "session_id": session_id})
    return updated


@router.post("/kiosk/sessions/{session_id}/template")
def api_kiosk_select_template(session_id: str, payload: KioskTemplateUpdatePayload, request: Request):
    sess = platform_store.get_kiosk_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="ไม่พบเซสชัน Kiosk")
    if sess["status"] == "finalized":
        raise HTTPException(status_code=409, detail="เซสชันนี้เสร็จสิ้นแล้ว ไม่สามารถเปลี่ยนเทมเพลตได้")
    template = _find_template(payload.template_id)
    updated = platform_store.update_kiosk_session(session_id, template_id=template["id"])
    with _store_lock:
        if not container.processing_service.is_occupied() and container.processing_service._workflow_jobmodel is None:
            _activate_template(template)
    event_bus.notify("kiosk", {"action": "template", "session_id": session_id, "template_id": template["id"]})
    return {"ok": True, "session": updated, "template_id": template["id"]}


@router.post("/kiosk/sessions/{session_id}/capture-frames", status_code=status.HTTP_201_CREATED)
async def api_kiosk_capture_frames(
    session_id: str,
    request: Request,
    template_id: str = Form(...),
    photos: list[UploadFile] = File(...),
):
    sess = platform_store.get_kiosk_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="ไม่พบเซสชัน Kiosk")
    if sess["status"] == "finalized":
        raise HTTPException(status_code=409, detail="เซสชันนี้เสร็จสิ้นแล้ว")
    template = _find_template(template_id)
    if len(photos) != len(template["slots"]):
        raise HTTPException(status_code=422, detail="จำนวนภาพไม่ตรงกับจำนวนช่องของเทมเพลต")

    captured_images = await _read_uploaded_photos_rgb(photos)
    output_image = _compose_template_from_images(template, captured_images)
    output_media_type = "collage" if (len(captured_images) > 1 or template.get("background_path") or template.get("overlay_path")) else "image"
    mediaitem = _save_composed_photo_set(
        template,
        captured_images,
        output_image,
        source_prefix="kiosk-device-source",
        output_prefix=f"kiosk-device-{template_id}",
        output_media_type=output_media_type,
        source_config={"camera_source": "device", "kiosk_session_id": session_id},
        extra_output_config={"camera_source": "device", "kiosk_session_id": session_id},
        error_detail="บันทึกภาพจากกล้องเครื่องไม่สำเร็จ",
    )
    platform_store.update_kiosk_session(session_id, status="preview", media_id=str(mediaitem.id), template_id=template_id)
    _audit(request, "kiosk.session.capture_frames", "kiosk_session", session_id, detail={"template_id": template_id, "media_id": str(mediaitem.id)}, actor_role="guest")
    event_bus.notify("kiosk", {"action": "capture_frames", "session_id": session_id, "media_id": str(mediaitem.id)})
    return {
        "ok": True,
        "media_id": str(mediaitem.id),
        "preview_url": f"/api/share/download-image/{mediaitem.id}",
    }


@router.post("/kiosk/sessions/{session_id}/finalize")
def api_kiosk_finalize_session(session_id: str, payload: KioskFinalizePayload, request: Request):
    idem_key = _idempotency_key(request)
    idem_scope = f"kiosk.session.finalize.{session_id}"
    if idem_key:
        cached = platform_store.get_idempotency(idem_scope, idem_key)
        if cached:
            return JSONResponse(status_code=cached[0], content=cached[1])
    sess = platform_store.get_kiosk_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="ไม่พบเซสชัน Kiosk")
    event = platform_store.get_event(sess["event_id"]) or platform_store.get_active_event()
    policy = event.get("policy") or {}
    item = _get_item_or_404(payload.media_id) if payload.media_id else None
    media_ids = [str(item.id)] if item else []
    ttl_hours = max(1, int(policy.get("retention_days", 30)) * 24)
    token_data = platform_store.create_share_token(event["id"], media_ids, session_code=session_id, ttl_hours=ttl_hours)

    print_result: dict[str, Any] = {"ok": False, "skipped": True}
    printed_copies = 0
    if item and payload.auto_print and policy.get("auto_print", True):
        try:
            profile = _active_printer_profile(payload.profile_id)
            output = _send_to_printer(item.processed, profile, payload.copies, title=f"{session_id} Kiosk")
            print_job_id = cups_job_id(output)
            printed_copies = payload.copies
            print_result = {"ok": True, "printer": profile["printer_name"], "print_job_id": print_job_id, "copies": payload.copies}
            _invalidate_print_caches()
        except HTTPException as exc:
            print_result = {"ok": False, "error": exc.detail}

    cloud_stat = None
    if item:
        if _is_offline_mode():
            if CLOUD_ENV.exists():
                try:
                    platform_store.enqueue_upload(str(item.id), str(item.processed), _is_two_up_item(item))
                except Exception:
                    pass
            cloud_stat = {"status": "offline", "offline_mode": True}
        else:
            cloud_stat = _cloud_status(item.id, item)
            if cloud_stat is None and CLOUD_ENV.exists():
                cloud_stat = _start_cloud_upload(item.id, item)

    updated = platform_store.update_kiosk_session(
        session_id,
        status="finalized",
        completed_at=datetime.now(timezone.utc).isoformat(),
        media_id=str(item.id) if item else None,
        share_token_id=token_data["id"],
        print_count=printed_copies,
        template_id=payload.template_id or sess.get("template_id"),
    )
    gallery_url = f"{_request_base_url(request)}/gallery.html?token={token_data['token']}"
    result = {
        "ok": True,
        "session": updated,
        "share_token": token_data["token"],
        "share_token_id": token_data["id"],
        "expires_at": token_data["expires_at"],
        "gallery_url": gallery_url,
        "cloud_status": cloud_stat,
        "print_result": print_result,
    }
    if idem_key:
        platform_store.save_idempotency(idem_scope, idem_key, 200, result)
    _audit(request, "kiosk.session.finalize", "kiosk_session", session_id, detail={"media_id": str(item.id) if item else None, "printed_copies": printed_copies}, actor_role="guest")
    event_bus.notify("kiosk", {"action": "finalize", "session_id": session_id})
    return result


@router.post("/kiosk/unlock")
def api_kiosk_unlock(payload: KioskUnlockPayload, request: Request):
    expected_pin = ""
    if CONFIG_FILE.is_file():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            expected_pin = str(((cfg.get("common") or {}).get("admin_password")) or "")
        except (OSError, json.JSONDecodeError):
            expected_pin = ""
    if not expected_pin or not hmac.compare_digest(payload.pin, expected_pin):
        _audit(request, "kiosk.unlock", "kiosk", "staff-corner", result="denied", actor_role="guest")
        raise HTTPException(status_code=403, detail="รหัสปลดล็อกไม่ถูกต้อง")
    _audit(request, "kiosk.unlock", "kiosk", "staff-corner", result="ok", actor_role="admin")
    return {"ok": True, "admin_token": _compute_admin_token()}


@router.post("/tokens", status_code=status.HTTP_201_CREATED)
def api_create_share_token(payload: ShareTokenCreatePayload, request: Request):
    actor_role, _ = _require_localhost(request)
    media_strs = []
    for mid in payload.media_ids:
        _get_item_or_404(mid)
        media_strs.append(str(mid))
    active_event = platform_store.get_active_event()
    token_data = platform_store.create_share_token(
        active_event["id"],
        media_strs,
        session_code=payload.session_code,
        ttl_hours=payload.ttl_hours,
    )
    token_data["gallery_url"] = f"{_request_base_url(request)}/gallery.html?token={token_data['token']}"
    _audit(request, "share.token.create", "share_token", token_data["id"], detail={"media_count": len(media_strs), "session_code": payload.session_code}, actor_role=actor_role)
    return token_data


@router.get("/tokens")
def api_list_share_tokens(request: Request, event_id: str | None = None, limit: int = 50):
    _require_localhost(request)
    return {"tokens": platform_store.list_share_tokens(event_id=event_id, limit=limit)}


@router.post("/tokens/{token_id}/revoke")
def api_revoke_share_token(token_id: str, request: Request):
    actor_role, _ = _require_localhost(request)
    revoked = platform_store.revoke_share_token(token_id)
    if revoked is None:
        raise HTTPException(status_code=404, detail="ไม่พบ Share Token ที่ต้องการยกเลิก")
    _audit(request, "share.token.revoke", "share_token", token_id, actor_role=actor_role)
    return {"ok": True, "token": revoked}


def _require_valid_share_token(raw_token: str) -> dict:
    info = platform_store.verify_share_token(raw_token)
    if info is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ไม่พบลิงก์แกลเลอรีนี้")
    if not info["valid"]:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="ลิงก์แกลเลอรีนี้หมดอายุหรือถูกยกเลิกแล้ว")
    return info


@router.get("/personal-gallery/{token}")
def api_personal_gallery(token: str):
    info = _require_valid_share_token(token)
    event = platform_store.get_event(info["event_id"]) or platform_store.get_active_event()
    items = []
    for mid in info["media_ids"]:
        try:
            uid = UUID(str(mid))
            media_item = _get_item_or_404(uid)
            items.append({
                "id": str(media_item.id),
                "download_url": f"/api/share/personal-gallery/{token}/image/{media_item.id}",
                "attachment_url": f"/api/share/personal-gallery/{token}/image/{media_item.id}?attachment=1",
                "two_up": _is_two_up_item(media_item),
            })
        except (ValueError, HTTPException):
            continue
    return {
        "token_id": info["id"],
        "event_id": info["event_id"],
        "event_name": event["name"],
        "session_code": info["session_code"],
        "expires_at": info["expires_at"],
        "privacy_notice": (event.get("policy") or {}).get("privacy_notice", ""),
        "items": items,
    }


@router.get("/personal-gallery/{token}/image/{media_id}")
def api_personal_gallery_image(token: str, media_id: UUID, attachment: bool = False):
    info = _require_valid_share_token(token)
    if str(media_id) not in set(info["media_ids"]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="โทเค็นนี้ไม่มีสิทธิ์เข้าถึงภาพดังกล่าว")
    item = _get_item_or_404(media_id)
    two_up = _is_two_up_item(item)
    filename = f"photobooth-{media_id}-{'2x6' if two_up else 'full'}.jpg"
    headers = {"Cache-Control": "private, max-age=300"}
    if attachment:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    if not two_up:
        return FileResponse(item.processed, media_type="image/jpeg", headers=headers)
    try:
        with Image.open(item.processed) as source:
            left_half = source.crop((0, 0, source.width // 2, source.height)).convert("RGB")
            output = BytesIO()
            left_half.save(output, format="JPEG", quality=94, optimize=True)
    except (OSError, UnidentifiedImageError) as exc:
        raise HTTPException(status_code=500, detail="ไม่สามารถสร้างภาพ 2×6 ได้") from exc
    return Response(content=output.getvalue(), media_type="image/jpeg", headers=headers)


@router.get("/analytics")
def api_analytics(request: Request, event_id: str | None = None):
    _require_localhost(request)
    return platform_store.compute_analytics(event_id=event_id, queue_snapshot=guest_queue_store.snapshot())


@router.get("/analytics/export.csv")
def api_analytics_export_csv(request: Request, event_id: str | None = None):
    _require_localhost(request)
    stats = platform_store.compute_analytics(event_id=event_id, queue_snapshot=guest_queue_store.snapshot())
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(["metric", "value"])
    for key in (
        "event_id",
        "event_name",
        "sessions_total",
        "sessions_completed",
        "queue_sessions_total",
        "kiosk_sessions_total",
        "kiosk_sessions_completed",
        "print_submitted",
        "print_unknown",
        "print_reprints",
        "estimated_paper_sheets",
        "active_share_tokens",
        "revoked_share_tokens",
    ):
        writer.writerow([key, stats.get(key)])
    writer.writerow(["note", stats.get("paper_estimate_note")])
    writer.writerow([])
    writer.writerow(["template_id", "count"])
    for tpl_id, cnt in (stats.get("template_usage") or {}).items():
        writer.writerow([tpl_id, cnt])
    writer.writerow([])
    writer.writerow(["hour_utc", "sessions"])
    for hour_key, cnt in (stats.get("hourly_activity") or {}).items():
        writer.writerow([hour_key, cnt])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="photobooth-analytics.csv"', "Cache-Control": "no-store"},
    )


def _protected_retention_media_ids() -> set[str]:
    protected = set(guest_queue_store.pending_print_media_ids())
    snap = guest_queue_store.snapshot()
    for entry in snap.get("entries", []):
        if entry.get("status") in {"waiting", "shooting", "printing"}:
            protected.update(str(m) for m in (entry.get("media_ids") or []) if m)
        for job in entry.get("print_jobs") or []:
            if job.get("state") in {"queued", "submitted", "processing", "unknown", "printing"} and job.get("media_id"):
                protected.add(str(job["media_id"]))
    with _cloud_lock:
        protected.update(_cloud_inflight)
    return protected


def _scan_retention(retention_days: int) -> dict[str, Any]:
    cutoff = time.time() - max(1, int(retention_days)) * 86400.0
    protected_ids = _protected_retention_media_ids()
    candidates = []
    skipped_protected = []
    try:
        items = list(container.mediacollection_service.db_get_images() if hasattr(container.mediacollection_service, "db_get_images") else [])
    except Exception:
        items = []
    if not items:
        try:
            with Session(engine) as session:
                items = list(session.scalars(select(Mediaitem)).all())
        except Exception:
            items = []
    for item in items:
        created = getattr(item, "created_at", None)
        created_ts = created.timestamp() if hasattr(created, "timestamp") else 0.0
        if created_ts and created_ts <= cutoff:
            mid = str(item.id)
            if mid in protected_ids:
                skipped_protected.append(mid)
            else:
                candidates.append({"id": mid, "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created)})
    return {
        "retention_days": retention_days,
        "eligible_count": len(candidates),
        "protected_count": len(skipped_protected),
        "candidates": candidates[:200],
        "protected_ids": skipped_protected[:200],
    }


@router.get("/retention/preview")
def api_retention_preview(request: Request, retention_days: int | None = None):
    _require_localhost(request)
    active_event = platform_store.get_active_event()
    days = int(retention_days or (active_event.get("policy") or {}).get("retention_days", 30))
    return _scan_retention(days)


@router.post("/retention/purge")
def api_retention_purge(payload: RetentionPurgePayload, request: Request):
    actor_role, _ = _require_localhost(request)
    active_event = platform_store.get_active_event()
    days = int(payload.retention_days or (active_event.get("policy") or {}).get("retention_days", 30))
    scan = _scan_retention(days)
    if payload.dry_run:
        return {"dry_run": True, "deleted_count": 0, "revoked_expired_tokens": 0, **scan}
    revoked_tokens = platform_store.revoke_expired_tokens()
    deleted = 0
    errors = []
    for entry in scan["candidates"]:
        mid = entry["id"]
        try:
            uid = UUID(mid)
            item = container.mediacollection_service.get_item(uid)
            container.mediacollection_service.delete_item(item)
            platform_store.remove_upload(mid)
            deleted += 1
        except Exception as exc:
            errors.append({"id": mid, "error": str(exc)})
    _audit(request, "retention.purge", "media", "batch", detail={"retention_days": days, "deleted": deleted, "protected_skipped": scan["protected_count"], "revoked_tokens": revoked_tokens}, actor_role=actor_role)
    return {
        "dry_run": False,
        "retention_days": days,
        "deleted_count": deleted,
        "protected_count": scan["protected_count"],
        "revoked_expired_tokens": revoked_tokens,
        "errors": errors,
    }


def _build_gif_worker(media_id: UUID, image_paths: list[Path]) -> None:
    key = str(media_id)
    GIF_DIR.mkdir(parents=True, exist_ok=True)
    target = GIF_DIR / f"{media_id}.gif"
    tmp_path = GIF_DIR / f".{media_id}.{uuid4().hex}.tmp.gif"
    try:
        frames: list[Image.Image] = []
        for path in image_paths[:6]:
            if not path.is_file():
                continue
            with Image.open(path) as img:
                rgb = ImageOps.exif_transpose(img).convert("RGB")
                rgb.thumbnail((640, 640))
                frames.append(rgb)
        if not frames:
            raise ValueError("ไม่พบไฟล์ภาพสำหรับสร้าง GIF")
        if len(frames) == 1:
            base = frames[0]
            w, h = base.size
            crop = base.crop((int(w * 0.05), int(h * 0.05), int(w * 0.95), int(h * 0.95))).resize((w, h))
            frames = [base, crop]
        elif len(frames) >= 3:
            frames = frames + list(reversed(frames[1:-1]))
        frames[0].save(
            tmp_path,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=450,
            loop=0,
        )
        os.replace(tmp_path, target)
        with _gif_lock:
            _gif_jobs[key] = {"media_id": key, "status": "ready", "path": str(target), "error": None}
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        with _gif_lock:
            _gif_jobs[key] = {"media_id": key, "status": "failed", "path": None, "error": str(exc)}


@router.post("/media/{id}/gif")
def api_generate_media_gif(id: UUID):
    item = _get_item_or_404(id)
    key = str(id)
    target = GIF_DIR / f"{id}.gif"
    if target.is_file():
        return {"ok": True, "media_id": key, "status": "ready", "url": f"/api/share/media/{id}/gif?download=1"}
    with _gif_lock:
        current = _gif_jobs.get(key)
        if current and current.get("status") == "processing":
            return {"ok": True, "media_id": key, "status": "processing", "url": f"/api/share/media/{id}/gif?download=1"}
        _gif_jobs[key] = {"media_id": key, "status": "processing", "path": None, "error": None}
    try:
        related = container.mediacollection_service.get_items_relto_job(item.job_identifier)
        sources = [Path(entry.processed) for entry in sorted(related, key=lambda x: (x.created_at, x.rowid)) if entry.media_type == "image" and entry.processed]
    except Exception:
        sources = []
    if not sources and item.processed:
        sources = [Path(item.processed)]
    threading.Thread(target=_build_gif_worker, args=(id, sources), daemon=True).start()
    return {"ok": True, "media_id": key, "status": "processing", "url": f"/api/share/media/{id}/gif?download=1"}


@router.get("/media/{id}/gif")
def api_get_media_gif(id: UUID, download: bool = False):
    key = str(id)
    target = GIF_DIR / f"{id}.gif"
    if download:
        if not target.is_file():
            raise HTTPException(status_code=404, detail="ไฟล์ GIF ยังไม่พร้อม")
        return FileResponse(target, media_type="image/gif", headers={"Cache-Control": "private, max-age=300"})
    if target.is_file():
        return {"media_id": key, "status": "ready", "url": f"/api/share/media/{id}/gif?download=1", "error": None}
    with _gif_lock:
        info = _gif_jobs.get(key)
    if info:
        return {"media_id": key, "status": info["status"], "url": f"/api/share/media/{id}/gif?download=1", "error": info.get("error")}
    return {"media_id": key, "status": "idle", "url": None, "error": None}


def _qr_svg(url: str):
    script_path = Path.cwd().joinpath("scripts", "qr-svg.mjs")
    try:
        result = subprocess.run(
            ["node", str(script_path)],
            input=url,
            capture_output=True,
            text=True,
            timeout=8,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.exception(exc)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="สร้าง QR code ไม่สำเร็จ") from exc
    return Response(content=result.stdout, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


try:
    from ...services.processor.collage import JobModelCollage

    def _template_capture_count(jobmodel):
        count = jobmodel._get_number_of_captures_from_merge_definition(jobmodel._configuration_set.processing.merge_definition)
        active = _find_template(_active_template_id())
        if active.get("print_layout") == "two_up" and count == len(active["slots"]) * 2:
            return len(active["slots"])
        return count

    JobModelCollage.total_captures_to_take = property(_template_capture_count)
    _apply_template(_find_template(_active_template_id()))
except Exception as exc:
    logger.warning("Could not apply the active photobooth template during startup: %s", exc)
