# Changelog

## Photobooth Platform Modernization (Phases 0–5, Non-Payment Edition)

### Phase 5 — Managed Kiosk & Advanced Tooling
- Added background animated GIF / Boomerang export endpoints (`POST /api/share/media/{id}/gif`, `GET /api/share/media/{id}/gif`) writing atomically into `userdata/gifs/`.
- Added versioned Template Export / Import endpoints (`GET /api/share/templates/{id}/export`, `POST /api/share/templates/import`) with SHA-256 checksum and layout boundary validation.
- Added safe macOS-compatible `scripts/watchdog.sh` (refuses restart during active capture, active queue, or `unknown` print jobs) and `scripts/release_snapshot.sh`.
- Added [operator-runbook.md](docs/operator-runbook.md).

### Phase 4 — Personal QR Gallery, Analytics & Retention
- Added scoped, SHA-256-hashed, expiring & revocable `share_tokens` with endpoints (`POST /api/share/tokens`, `GET /api/share/tokens`, `POST /api/share/tokens/{id}/revoke`, `GET /api/share/personal-gallery/{token}`, `GET /api/share/personal-gallery/{token}/image/{media_id}`) and [gallery.html](photobooth_custom/frontend/gallery.html).
- Added per-event Analytics & CSV export (`GET /api/share/analytics`, `GET /api/share/analytics/export.csv`) with explicit paper-estimate labeling.
- Added safe Retention preview & purge (`GET /api/share/retention/preview`, `POST /api/share/retention/purge`) protecting active/unknown print jobs.

### Phase 3 — Self-Service Kiosk (Free)
- Added Kiosk lifecycle APIs (`GET /api/share/kiosk/config`, `POST /api/share/kiosk/sessions`, `POST /api/share/kiosk/sessions/{id}/retake`, `POST /api/share/kiosk/sessions/{id}/finalize`, `POST /api/share/kiosk/unlock`) enforcing intake, cooldown, hourly cap, and retake limits.
- Built fullscreen touch-friendly [kiosk.html](photobooth_custom/frontend/kiosk.html) with TH/EN switching, privacy notice, Live View, idle timeout reset, and 5-tap corner staff PIN unlock.

### Phase 2 — Events, Template Versioning & Print Quota Policies
- Extended SQLite `PlatformStore` to Schema v2 (`events`, `template_versions`, `share_tokens`, `kiosk_sessions`).
- Added Event Workspace CRUD/Clone/Activate & Intake/Print Quota policy enforcement + override audit logging.
- Shared `_validate_template_layout()` across template save & import and recorded immutable template manifest versions.
- Upgraded [control-center.html](photobooth_custom/frontend/control-center.html) with tabbed management UIs.

### Phase 1 — Durable Core & Control Center MVP
- Introduced `userdata/photobooth_platform.sqlite` (SQLite WAL + migrations), idempotency keys, durable cloud outbox with backoff worker, SSE `GET /api/share/events`, `X-Admin-Token` LAN auth, redacted audit logs, and Control Center MVP.

### Phase 0 — Audit & Stabilization
- Added `unknown` print outcome tracking + manual resolution, `/api/share/health` & `/api/share/preflight`, `scripts/preflight.sh`, online SQLite `scripts/backup.sh` & `scripts/restore.sh`, and `docs/current-state-audit.md`.
