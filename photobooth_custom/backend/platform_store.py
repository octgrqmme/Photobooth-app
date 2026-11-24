"""Durable SQLite WAL storage, events, template versions, share tokens, kiosk sessions, outbox, and audit log."""

from __future__ import annotations

import hashlib
import json
import logging
import queue
import secrets
import sqlite3
import threading
import time
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2
_SENSITIVE_KEY_PARTS = ("password", "secret", "token", "image_data", "background_data", "overlay_data")

DEFAULT_EVENT_POLICY: dict[str, Any] = {
    "print_quota_per_session": 2,
    "retake_limit": 2,
    "kiosk_idle_timeout_sec": 60,
    "kiosk_cooldown_sec": 5,
    "kiosk_max_sessions_per_hour": 120,
    "auto_print": True,
    "require_operator_approval": False,
    "retention_days": 30,
    "languages": ["th", "en"],
    "default_language": "th",
    "brand_title": "Photobooth Studio",
    "brand_subtitle": "ถ่ายภาพและรับรูปฟรี ไม่มีค่าใช้จ่าย",
    "privacy_notice": "ภาพถ่ายของคุณเข้าถึงได้ผ่าน QR ส่วนตัวตามระยะเวลาที่กำหนด และถูกลบเมื่อครบกำหนด",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ts_to_iso(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


def _iso_to_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return None


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(str(raw_token).encode("utf-8")).hexdigest()


def _normalize_policy(policy: dict | None, base: dict | None = None) -> dict[str, Any]:
    merged = deepcopy(base if base is not None else DEFAULT_EVENT_POLICY)
    if isinstance(policy, dict):
        merged.update(policy)
    return merged


def _scrub_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            key_lower = str(key).lower()
            if any(part in key_lower for part in _SENSITIVE_KEY_PARTS):
                cleaned[key] = "[REDACTED]"
            else:
                cleaned[key] = _scrub_sensitive(item)
        return cleaned
    if isinstance(value, list):
        return [_scrub_sensitive(item) for item in value]
    return value


class PlatformStore:
    """Thread-safe SQLite WAL store for durable state, events, versions, tokens, kiosk, outbox, and audit."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._schema_ready = False

    def _raw_connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=5.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        if self._schema_ready:
            return
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        current = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
        if current < 1:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS kv_state (
                    namespace TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    scope TEXT NOT NULL,
                    key TEXT NOT NULL,
                    status_code INTEGER NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (scope, key)
                );
                CREATE TABLE IF NOT EXISTS upload_outbox (
                    media_id TEXT PRIMARY KEY,
                    image_path TEXT NOT NULL,
                    two_up INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    next_retry_at REAL NOT NULL DEFAULT 0,
                    last_error TEXT,
                    share_url TEXT,
                    expires_at INTEGER,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_role TEXT NOT NULL,
                    actor_host TEXT NOT NULL,
                    action TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT,
                    result TEXT NOT NULL,
                    detail_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_upload_outbox_due ON upload_outbox (state, next_retry_at);
                CREATE INDEX IF NOT EXISTS idx_audit_events_created ON audit_events (id DESC);
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (1, ?)",
                (_now_iso(),),
            )
            conn.commit()
        if current < 2:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'event',
                    status TEXT NOT NULL DEFAULT 'active',
                    intake_open INTEGER NOT NULL DEFAULT 1,
                    policy_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS template_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    template_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    manifest_json TEXT NOT NULL,
                    assets_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(template_id, version)
                );
                CREATE TABLE IF NOT EXISTS share_tokens (
                    id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    session_code TEXT,
                    media_ids_json TEXT NOT NULL,
                    token_hash TEXT UNIQUE NOT NULL,
                    expires_at REAL NOT NULL,
                    revoked_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS kiosk_sessions (
                    id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    station_id TEXT NOT NULL DEFAULT 'kiosk-1',
                    language TEXT NOT NULL DEFAULT 'th',
                    template_id TEXT,
                    status TEXT NOT NULL,
                    retake_count INTEGER NOT NULL DEFAULT 0,
                    print_count INTEGER NOT NULL DEFAULT 0,
                    media_id TEXT,
                    share_token_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_template_versions_tid ON template_versions (template_id, version DESC);
                CREATE INDEX IF NOT EXISTS idx_share_tokens_hash ON share_tokens (token_hash);
                CREATE INDEX IF NOT EXISTS idx_share_tokens_event ON share_tokens (event_id, expires_at);
                CREATE INDEX IF NOT EXISTS idx_kiosk_sessions_event ON kiosk_sessions (event_id, created_at DESC);
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, _now_iso()),
            )
            conn.commit()
        self._schema_ready = True

    def _connect(self) -> sqlite3.Connection:
        conn = self._raw_connect()
        try:
            self._ensure_schema(conn)
            return conn
        except Exception:
            conn.close()
            raise

    def schema_version(self) -> int:
        with self._lock:
            conn = self._connect()
            try:
                return int(conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0])
            finally:
                conn.close()

    def quick_check(self) -> tuple[bool, str]:
        with self._lock:
            try:
                conn = self._connect()
                try:
                    row = conn.execute("PRAGMA quick_check").fetchone()
                    text = str(row[0]) if row else "unknown"
                    return (text.lower() == "ok", text)
                finally:
                    conn.close()
            except Exception as exc:
                return (False, str(exc))

    def load_kv(self, namespace: str) -> dict | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT state_json FROM kv_state WHERE namespace = ?", (namespace,)).fetchone()
                if row is None:
                    return None
                data = json.loads(row["state_json"])
                if not isinstance(data, dict):
                    raise ValueError(f"Invalid kv_state payload for {namespace}")
                return data
            finally:
                conn.close()

    def save_kv(self, namespace: str, data: dict) -> int:
        payload = json.dumps(data, ensure_ascii=False)
        now = _now_iso()
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO kv_state (namespace, state_json, version, updated_at)
                        VALUES (?, ?, 1, ?)
                        ON CONFLICT(namespace) DO UPDATE SET
                            state_json = excluded.state_json,
                            version = kv_state.version + 1,
                            updated_at = excluded.updated_at
                        """,
                        (namespace, payload, now),
                    )
                row = conn.execute("SELECT version FROM kv_state WHERE namespace = ?", (namespace,)).fetchone()
                return int(row["version"]) if row else 1
            finally:
                conn.close()

    def get_idempotency(self, scope: str, key: str) -> tuple[int, dict] | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT status_code, response_json FROM idempotency_keys WHERE scope = ? AND key = ?",
                    (scope, key),
                ).fetchone()
                if row is None:
                    return None
                return int(row["status_code"]), json.loads(row["response_json"])
            finally:
                conn.close()

    def save_idempotency(self, scope: str, key: str, status_code: int, body: dict) -> None:
        payload = json.dumps(body, ensure_ascii=False)
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO idempotency_keys (scope, key, status_code, response_json, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (scope, key, int(status_code), payload, _now_iso()),
                    )
            finally:
                conn.close()

    # --- Event Workspaces (Phase 2) ---

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "name": row["name"],
            "mode": row["mode"],
            "status": row["status"],
            "intake_open": bool(row["intake_open"]),
            "policy": _normalize_policy(json.loads(row["policy_json"]) if row["policy_json"] else {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def ensure_default_event(self) -> dict:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM events WHERE status = 'active' ORDER BY updated_at DESC LIMIT 1").fetchone()
                if row:
                    return self._row_to_event(row)
                any_row = conn.execute("SELECT * FROM events ORDER BY created_at ASC LIMIT 1").fetchone()
                if any_row:
                    with conn:
                        conn.execute("UPDATE events SET status = 'active', updated_at = ? WHERE id = ?", (_now_iso(), any_row["id"]))
                    row = conn.execute("SELECT * FROM events WHERE id = ?", (any_row["id"],)).fetchone()
                    return self._row_to_event(row)
                now = _now_iso()
                with conn:
                    conn.execute(
                        """
                        INSERT INTO events (id, name, mode, status, intake_open, policy_json, created_at, updated_at)
                        VALUES (?, ?, 'event', 'active', 1, ?, ?, ?)
                        """,
                        ("event-default", "งานหลัก (Default Event)", json.dumps(DEFAULT_EVENT_POLICY, ensure_ascii=False), now, now),
                    )
                row = conn.execute("SELECT * FROM events WHERE id = 'event-default'").fetchone()
                return self._row_to_event(row)
            finally:
                conn.close()

    def list_events(self) -> list[dict]:
        with self._lock:
            self.ensure_default_event()
            conn = self._connect()
            try:
                rows = conn.execute("SELECT * FROM events ORDER BY (status = 'active') DESC, updated_at DESC").fetchall()
                return [self._row_to_event(row) for row in rows]
            finally:
                conn.close()

    def get_event(self, event_id: str) -> dict | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
                return self._row_to_event(row) if row else None
            finally:
                conn.close()

    def get_active_event(self) -> dict:
        return self.ensure_default_event()

    def create_event(
        self,
        name: str,
        mode: str = "event",
        policy: dict | None = None,
        activate: bool = False,
        intake_open: bool = True,
    ) -> dict:
        event_id = f"evt-{uuid4().hex[:10]}"
        now = _now_iso()
        merged_policy = _normalize_policy(policy)
        with self._lock:
            self.ensure_default_event()
            conn = self._connect()
            try:
                with conn:
                    if activate:
                        conn.execute("UPDATE events SET status = 'inactive', updated_at = ? WHERE status = 'active'", (now,))
                    conn.execute(
                        """
                        INSERT INTO events (id, name, mode, status, intake_open, policy_json, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event_id,
                            name.strip(),
                            mode,
                            "active" if activate else "inactive",
                            1 if intake_open else 0,
                            json.dumps(merged_policy, ensure_ascii=False),
                            now,
                            now,
                        ),
                    )
                row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
                return self._row_to_event(row)
            finally:
                conn.close()

    def clone_event(self, source_event_id: str, new_name: str, activate: bool = False) -> dict | None:
        with self._lock:
            source = self.get_event(source_event_id)
            if source is None:
                return None
            return self.create_event(
                name=new_name,
                mode=source["mode"],
                policy=deepcopy(source["policy"]),
                activate=activate,
                intake_open=source["intake_open"],
            )

    def update_event(
        self,
        event_id: str,
        *,
        name: str | None = None,
        mode: str | None = None,
        status: str | None = None,
        intake_open: bool | None = None,
        policy: dict | None = None,
    ) -> dict | None:
        now = _now_iso()
        with self._lock:
            existing = self.get_event(event_id)
            if existing is None:
                return None
            next_name = name.strip() if name is not None else existing["name"]
            next_mode = mode if mode is not None else existing["mode"]
            next_status = status if status is not None else existing["status"]
            next_intake = (1 if intake_open else 0) if intake_open is not None else (1 if existing["intake_open"] else 0)
            next_policy = _normalize_policy(policy, base=existing["policy"])
            conn = self._connect()
            try:
                with conn:
                    if next_status == "active":
                        conn.execute("UPDATE events SET status = 'inactive', updated_at = ? WHERE status = 'active' AND id != ?", (now, event_id))
                    conn.execute(
                        """
                        UPDATE events
                        SET name = ?, mode = ?, status = ?, intake_open = ?, policy_json = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (next_name, next_mode, next_status, next_intake, json.dumps(next_policy, ensure_ascii=False), now, event_id),
                    )
                row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
                return self._row_to_event(row) if row else None
            finally:
                conn.close()

    def activate_event(self, event_id: str) -> dict | None:
        return self.update_event(event_id, status="active")

    # --- Template Manifest Versioning (Phase 2) ---

    @staticmethod
    def _row_to_template_version(row: sqlite3.Row) -> dict:
        return {
            "id": int(row["id"]),
            "template_id": row["template_id"],
            "version": int(row["version"]),
            "manifest": json.loads(row["manifest_json"]),
            "assets_hash": row["assets_hash"],
            "created_at": row["created_at"],
        }

    def record_template_version(self, template_id: str, manifest: dict, assets_hash: str) -> dict:
        now = _now_iso()
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    next_ver = int(
                        conn.execute(
                            "SELECT COALESCE(MAX(version), 0) + 1 FROM template_versions WHERE template_id = ?",
                            (template_id,),
                        ).fetchone()[0]
                    )
                    conn.execute(
                        """
                        INSERT INTO template_versions (template_id, version, manifest_json, assets_hash, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (template_id, next_ver, json.dumps(manifest, ensure_ascii=False), assets_hash, now),
                    )
                row = conn.execute(
                    "SELECT * FROM template_versions WHERE template_id = ? AND version = ?",
                    (template_id, next_ver),
                ).fetchone()
                return self._row_to_template_version(row)
            finally:
                conn.close()

    def list_template_versions(self, template_id: str) -> list[dict]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM template_versions WHERE template_id = ? ORDER BY version DESC",
                    (template_id,),
                ).fetchall()
                return [self._row_to_template_version(row) for row in rows]
            finally:
                conn.close()

    def get_template_version(self, template_id: str, version: int) -> dict | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM template_versions WHERE template_id = ? AND version = ?",
                    (template_id, int(version)),
                ).fetchone()
                return self._row_to_template_version(row) if row else None
            finally:
                conn.close()

    # --- Scoped Expiring Share Tokens (Phase 4) ---

    @staticmethod
    def _row_to_share_token(row: sqlite3.Row, *, now_ts: float | None = None) -> dict:
        now = time.time() if now_ts is None else now_ts
        expires_at = float(row["expires_at"])
        revoked_at = row["revoked_at"]
        return {
            "id": row["id"],
            "event_id": row["event_id"],
            "session_code": row["session_code"],
            "media_ids": json.loads(row["media_ids_json"]),
            "expires_at": expires_at,
            "revoked_at": revoked_at,
            "created_at": row["created_at"],
            "active": (revoked_at is None) and (expires_at > now),
        }

    def create_share_token(
        self,
        event_id: str,
        media_ids: list[str],
        *,
        session_code: str | None = None,
        ttl_hours: int = 24,
    ) -> dict:
        raw_token = secrets.token_urlsafe(24)
        token_hash = _hash_token(raw_token)
        token_id = f"tok-{uuid4().hex[:10]}"
        now_ts = time.time()
        expires_at = now_ts + max(1, int(ttl_hours)) * 3600.0
        now_iso = _now_iso()
        unique_media_ids = [str(item) for item in dict.fromkeys(media_ids) if item]
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO share_tokens (id, event_id, session_code, media_ids_json, token_hash, expires_at, revoked_at, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
                        """,
                        (
                            token_id,
                            event_id,
                            session_code,
                            json.dumps(unique_media_ids, ensure_ascii=False),
                            token_hash,
                            expires_at,
                            now_iso,
                        ),
                    )
                row = conn.execute("SELECT * FROM share_tokens WHERE id = ?", (token_id,)).fetchone()
                data = self._row_to_share_token(row, now_ts=now_ts)
                data["token"] = raw_token
                return data
            finally:
                conn.close()

    def verify_share_token(self, raw_token: str) -> dict | None:
        if not raw_token or not str(raw_token).strip():
            return None
        token_hash = _hash_token(str(raw_token).strip())
        now_ts = time.time()
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM share_tokens WHERE token_hash = ?", (token_hash,)).fetchone()
                if row is None:
                    return None
                info = self._row_to_share_token(row, now_ts=now_ts)
                if info["revoked_at"] is not None:
                    info["valid"] = False
                    info["reason"] = "revoked"
                elif info["expires_at"] <= now_ts:
                    info["valid"] = False
                    info["reason"] = "expired"
                else:
                    info["valid"] = True
                    info["reason"] = "ok"
                return info
            finally:
                conn.close()

    def revoke_share_token(self, token_id: str) -> dict | None:
        now = _now_iso()
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        "UPDATE share_tokens SET revoked_at = COALESCE(revoked_at, ?) WHERE id = ?",
                        (now, token_id),
                    )
                row = conn.execute("SELECT * FROM share_tokens WHERE id = ?", (token_id,)).fetchone()
                return self._row_to_share_token(row) if row else None
            finally:
                conn.close()

    def list_share_tokens(self, event_id: str | None = None, limit: int = 50) -> list[dict]:
        now_ts = time.time()
        with self._lock:
            conn = self._connect()
            try:
                if event_id:
                    rows = conn.execute(
                        "SELECT * FROM share_tokens WHERE event_id = ? ORDER BY created_at DESC LIMIT ?",
                        (event_id, max(1, min(200, int(limit)))),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM share_tokens ORDER BY created_at DESC LIMIT ?",
                        (max(1, min(200, int(limit))),),
                    ).fetchall()
                return [self._row_to_share_token(row, now_ts=now_ts) for row in rows]
            finally:
                conn.close()

    def revoke_expired_tokens(self) -> int:
        now_ts = time.time()
        now_iso = _now_iso()
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    cursor = conn.execute(
                        "UPDATE share_tokens SET revoked_at = ? WHERE revoked_at IS NULL AND expires_at <= ?",
                        (now_iso, now_ts),
                    )
                    return int(cursor.rowcount)
            finally:
                conn.close()

    # --- Kiosk Sessions (Phase 3) ---

    @staticmethod
    def _row_to_kiosk_session(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "event_id": row["event_id"],
            "station_id": row["station_id"],
            "language": row["language"],
            "template_id": row["template_id"],
            "status": row["status"],
            "retake_count": int(row["retake_count"]),
            "print_count": int(row["print_count"]),
            "media_id": row["media_id"],
            "share_token_id": row["share_token_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
        }

    def create_kiosk_session(
        self,
        event_id: str,
        *,
        station_id: str = "kiosk-1",
        language: str = "th",
        template_id: str | None = None,
    ) -> dict:
        session_id = f"ksk-{uuid4().hex[:12]}"
        now = _now_iso()
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO kiosk_sessions (id, event_id, station_id, language, template_id, status, retake_count, print_count, media_id, share_token_id, created_at, updated_at, completed_at)
                        VALUES (?, ?, ?, ?, ?, 'started', 0, 0, NULL, NULL, ?, ?, NULL)
                        """,
                        (session_id, event_id, station_id, language, template_id, now, now),
                    )
                row = conn.execute("SELECT * FROM kiosk_sessions WHERE id = ?", (session_id,)).fetchone()
                return self._row_to_kiosk_session(row)
            finally:
                conn.close()

    def get_kiosk_session(self, session_id: str) -> dict | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM kiosk_sessions WHERE id = ?", (session_id,)).fetchone()
                return self._row_to_kiosk_session(row) if row else None
            finally:
                conn.close()

    def update_kiosk_session(self, session_id: str, **fields: Any) -> dict | None:
        allowed = {"language", "template_id", "status", "retake_count", "print_count", "media_id", "share_token_id", "completed_at"}
        updates = {key: val for key, val in fields.items() if key in allowed}
        updates["updated_at"] = _now_iso()
        columns = ", ".join(f"{col} = ?" for col in updates.keys())
        values = list(updates.values()) + [session_id]
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(f"UPDATE kiosk_sessions SET {columns} WHERE id = ?", values)
                row = conn.execute("SELECT * FROM kiosk_sessions WHERE id = ?", (session_id,)).fetchone()
                return self._row_to_kiosk_session(row) if row else None
            finally:
                conn.close()

    def count_recent_kiosk_sessions(self, event_id: str, since_ts: float) -> int:
        since_iso = _ts_to_iso(since_ts)
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM kiosk_sessions WHERE event_id = ? AND created_at >= ?",
                    (event_id, since_iso),
                ).fetchone()
                return int(row[0]) if row else 0
            finally:
                conn.close()

    def last_kiosk_session_ts(self, event_id: str) -> float | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT created_at FROM kiosk_sessions WHERE event_id = ? ORDER BY created_at DESC LIMIT 1",
                    (event_id,),
                ).fetchone()
                return _iso_to_ts(row["created_at"]) if row else None
            finally:
                conn.close()

    def list_kiosk_sessions(self, event_id: str | None = None, limit: int = 100) -> list[dict]:
        with self._lock:
            conn = self._connect()
            try:
                if event_id:
                    rows = conn.execute(
                        "SELECT * FROM kiosk_sessions WHERE event_id = ? ORDER BY created_at DESC LIMIT ?",
                        (event_id, max(1, min(500, int(limit)))),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM kiosk_sessions ORDER BY created_at DESC LIMIT ?",
                        (max(1, min(500, int(limit))),),
                    ).fetchall()
                return [self._row_to_kiosk_session(row) for row in rows]
            finally:
                conn.close()

    # --- Analytics (Phase 4) ---

    def compute_analytics(self, event_id: str | None = None, queue_snapshot: dict | None = None) -> dict:
        with self._lock:
            active_event = self.get_event(event_id) if event_id else self.get_active_event()
            target_event_id = active_event["id"] if active_event else "event-default"
            now_ts = time.time()
            conn = self._connect()
            try:
                kiosk_rows = conn.execute(
                    "SELECT * FROM kiosk_sessions WHERE event_id = ?",
                    (target_event_id,),
                ).fetchall()
                token_rows = conn.execute(
                    "SELECT expires_at, revoked_at FROM share_tokens WHERE event_id = ?",
                    (target_event_id,),
                ).fetchall()
                outbox_rows = conn.execute("SELECT state, COUNT(*) AS cnt FROM upload_outbox GROUP BY state").fetchall()
                audit_rows = conn.execute("SELECT action, result, created_at FROM audit_events").fetchall()
            finally:
                conn.close()

        queue_entries = list((queue_snapshot or {}).get("entries") or [])
        sessions_completed = sum(1 for entry in queue_entries if entry.get("status") in {"ready", "printed"})
        kiosk_completed = sum(1 for row in kiosk_rows if row["status"] == "finalized")

        print_submitted = 0
        print_unknown = 0
        print_reprints = 0
        estimated_paper_sheets = 0
        template_counter: Counter[str] = Counter()
        hourly_counter: Counter[str] = Counter()

        for entry in queue_entries:
            tpl = entry.get("template_id")
            if tpl:
                template_counter[str(tpl)] += 1
            created_ts = entry.get("created_at")
            if isinstance(created_ts, (int, float)):
                hour_key = datetime.fromtimestamp(float(created_ts), tz=timezone.utc).strftime("%Y-%m-%d %H:00Z")
                hourly_counter[hour_key] += 1
            jobs = list(entry.get("print_jobs") or [])
            if len(jobs) > 1:
                print_reprints += len(jobs) - 1
            for job in jobs:
                print_submitted += 1
                state = str(job.get("state") or "")
                if state == "unknown":
                    print_unknown += 1
                if state != "canceled":
                    estimated_paper_sheets += max(1, int(job.get("copies") or 1))

        for row in kiosk_rows:
            if row["template_id"]:
                template_counter[str(row["template_id"])] += 1
            estimated_paper_sheets += int(row["print_count"] or 0)
            if row["created_at"]:
                hourly_counter[str(row["created_at"])[:13] + ":00Z"] += 1

        outbox_counts = {"pending": 0, "uploading": 0, "failed": 0, "ready": 0}
        for row in outbox_rows:
            outbox_counts[str(row["state"])] = int(row["cnt"])

        active_tokens = 0
        revoked_tokens = 0
        for row in token_rows:
            if row["revoked_at"] is not None or float(row["expires_at"]) <= now_ts:
                revoked_tokens += 1
            else:
                active_tokens += 1

        error_counter: Counter[str] = Counter()
        for row in audit_rows:
            if str(row["result"] or "ok") != "ok":
                error_counter[str(row["action"])] += 1

        return {
            "event_id": target_event_id,
            "event_name": active_event["name"] if active_event else target_event_id,
            "sessions_total": len(queue_entries) + len(kiosk_rows),
            "sessions_completed": sessions_completed + kiosk_completed,
            "queue_sessions_total": len(queue_entries),
            "queue_sessions_completed": sessions_completed,
            "kiosk_sessions_total": len(kiosk_rows),
            "kiosk_sessions_completed": kiosk_completed,
            "print_submitted": print_submitted,
            "print_unknown": print_unknown,
            "print_reprints": print_reprints,
            "estimated_paper_sheets": estimated_paper_sheets,
            "paper_estimate_note": "ค่าประมาณจากจำนวนงานพิมพ์ที่บันทึก (ไม่ใช่ค่าเซ็นเซอร์ถาดกระดาษ)",
            "outbox_counts": outbox_counts,
            "active_share_tokens": active_tokens,
            "revoked_share_tokens": revoked_tokens,
            "template_usage": dict(template_counter.most_common()),
            "error_counts": dict(error_counter.most_common()),
            "hourly_activity": dict(sorted(hourly_counter.items())),
        }

    # --- Durable Upload Outbox (Phase 1) ---

    @staticmethod
    def _row_to_outbox(row: sqlite3.Row) -> dict:
        return {
            "media_id": row["media_id"],
            "image_path": row["image_path"],
            "two_up": bool(row["two_up"]),
            "state": row["state"],
            "retry_count": int(row["retry_count"]),
            "next_retry_at": float(row["next_retry_at"]),
            "last_error": row["last_error"],
            "share_url": row["share_url"],
            "expires_at": row["expires_at"],
            "updated_at": row["updated_at"],
        }

    def enqueue_upload(self, media_id: str, image_path: str, two_up: bool) -> dict:
        now = _now_iso()
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO upload_outbox (media_id, image_path, two_up, state, retry_count, next_retry_at, last_error, share_url, expires_at, updated_at)
                        VALUES (?, ?, ?, 'pending', 0, 0, NULL, NULL, NULL, ?)
                        ON CONFLICT(media_id) DO UPDATE SET
                            image_path = excluded.image_path,
                            two_up = excluded.two_up,
                            state = CASE WHEN upload_outbox.state = 'uploading' THEN 'uploading' ELSE 'pending' END,
                            retry_count = CASE WHEN upload_outbox.state = 'ready' THEN 0 ELSE upload_outbox.retry_count END,
                            next_retry_at = 0,
                            last_error = NULL,
                            updated_at = excluded.updated_at
                        """,
                        (media_id, image_path, 1 if two_up else 0, now),
                    )
                row = conn.execute("SELECT * FROM upload_outbox WHERE media_id = ?", (media_id,)).fetchone()
                return self._row_to_outbox(row)
            finally:
                conn.close()

    def mark_upload_uploading(self, media_id: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        "UPDATE upload_outbox SET state = 'uploading', updated_at = ? WHERE media_id = ?",
                        (_now_iso(), media_id),
                    )
            finally:
                conn.close()

    def mark_upload_ready(self, media_id: str, share_url: str, expires_at: int) -> None:
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        """
                        UPDATE upload_outbox
                        SET state = 'ready', share_url = ?, expires_at = ?, last_error = NULL, next_retry_at = 0, updated_at = ?
                        WHERE media_id = ?
                        """,
                        (share_url, int(expires_at), _now_iso(), media_id),
                    )
            finally:
                conn.close()

    def mark_upload_failed(self, media_id: str, error: str, backoff_seconds: int = 30) -> None:
        next_at = time.time() + max(1, int(backoff_seconds))
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        """
                        UPDATE upload_outbox
                        SET state = 'failed',
                            retry_count = retry_count + 1,
                            next_retry_at = ?,
                            last_error = ?,
                            updated_at = ?
                        WHERE media_id = ?
                        """,
                        (next_at, str(error)[:500], _now_iso(), media_id),
                    )
            finally:
                conn.close()

    def retry_upload_now(self, media_id: str) -> dict | None:
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        """
                        UPDATE upload_outbox
                        SET state = 'pending', next_retry_at = 0, last_error = NULL, updated_at = ?
                        WHERE media_id = ?
                        """,
                        (_now_iso(), media_id),
                    )
                row = conn.execute("SELECT * FROM upload_outbox WHERE media_id = ?", (media_id,)).fetchone()
                return self._row_to_outbox(row) if row else None
            finally:
                conn.close()

    def remove_upload(self, media_id: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute("DELETE FROM upload_outbox WHERE media_id = ?", (media_id,))
            finally:
                conn.close()

    def claim_due_uploads(self, now_ts: float | None = None, limit: int = 5, max_retries: int = 8) -> list[dict]:
        now = time.time() if now_ts is None else now_ts
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM upload_outbox
                    WHERE (state = 'pending' OR (state = 'failed' AND retry_count < ? AND next_retry_at <= ?))
                    ORDER BY updated_at ASC
                    LIMIT ?
                    """,
                    (int(max_retries), float(now), int(limit)),
                ).fetchall()
                return [self._row_to_outbox(row) for row in rows]
            finally:
                conn.close()

    def list_outbox(self, limit: int = 50) -> dict:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM upload_outbox ORDER BY updated_at DESC LIMIT ?",
                    (max(1, min(200, int(limit))),),
                ).fetchall()
                count_rows = conn.execute("SELECT state, COUNT(*) AS cnt FROM upload_outbox GROUP BY state").fetchall()
                counts = {"pending": 0, "uploading": 0, "failed": 0, "ready": 0}
                for row in count_rows:
                    counts[str(row["state"])] = int(row["cnt"])
                return {
                    "items": [self._row_to_outbox(row) for row in rows],
                    "counts": counts,
                }
            finally:
                conn.close()

    def record_audit(
        self,
        actor_role: str,
        actor_host: str,
        action: str,
        entity_type: str,
        entity_id: str | None = None,
        result: str = "ok",
        detail: dict | None = None,
    ) -> int:
        detail_json = json.dumps(_scrub_sensitive(detail), ensure_ascii=False) if detail is not None else None
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    cursor = conn.execute(
                        """
                        INSERT INTO audit_events (actor_role, actor_host, action, entity_type, entity_id, result, detail_json, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (actor_role, actor_host, action, entity_type, entity_id, result, detail_json, _now_iso()),
                    )
                    return int(cursor.lastrowid)
            finally:
                conn.close()

    def list_audit_events(self, limit: int = 50) -> list[dict]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM audit_events ORDER BY id DESC LIMIT ?",
                    (max(1, min(200, int(limit))),),
                ).fetchall()
                events = []
                for row in rows:
                    events.append({
                        "id": int(row["id"]),
                        "actor_role": row["actor_role"],
                        "actor_host": row["actor_host"],
                        "action": row["action"],
                        "entity_type": row["entity_type"],
                        "entity_id": row["entity_id"],
                        "result": row["result"],
                        "detail": json.loads(row["detail_json"]) if row["detail_json"] else None,
                        "created_at": row["created_at"],
                    })
                return events
            finally:
                conn.close()


class EventBus:
    """In-memory pub/sub notifying SSE subscribers without blocking writers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._version = 0
        self._subscribers: set[queue.Queue] = set()

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def subscribe(self) -> queue.Queue:
        subscriber: queue.Queue = queue.Queue(maxsize=32)
        with self._lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    def notify(self, topic: str, detail: dict | None = None) -> int:
        with self._lock:
            self._version += 1
            version = self._version
            subscribers = list(self._subscribers)
        payload = {"version": version, "topic": topic, "detail": detail or {}, "at": _now_iso()}
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(payload)
            except queue.Full:
                try:
                    subscriber.get_nowait()
                except queue.Empty:
                    pass
                try:
                    subscriber.put_nowait(payload)
                except queue.Full:
                    pass
        return version


platform_store = PlatformStore(Path("userdata/photobooth_platform.sqlite"))
event_bus = EventBus()
