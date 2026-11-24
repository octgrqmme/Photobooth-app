#!/usr/bin/env bash
set -euo pipefail

app_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$app_root"

python_bin="$app_root/venv/bin/python"
if [[ ! -x "$python_bin" ]]; then
  python_bin="$(command -v python3 || true)"
fi

if [[ -z "$python_bin" ]]; then
  echo "[FAIL] ไม่พบ Python สำหรับตรวจระบบ" >&2
  exit 1
fi

"$python_bin" - "$app_root" <<'PYEOF'
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
fails = 0
warns = 0

def report(level: str, name: str, detail: str) -> None:
    global fails, warns
    if level == "FAIL":
        fails += 1
    elif level == "WARN":
        warns += 1
    print(f"[{level:4}] {name}: {detail}")

# 1. Config check
cfg_path = root / "config" / "config.json"
if not cfg_path.is_file():
    report("FAIL", "config.json", f"ไม่พบไฟล์ {cfg_path}")
else:
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        pw = str(((cfg.get("common") or {}).get("admin_password")) or "")
        secret = str(((cfg.get("misc") or {}).get("secret")) or "")
        if not pw or pw == "0000":
            report("FAIL", "admin_password", "เปลี่ยนรหัสผู้ดูแลค่าเริ่มต้นก่อนเชื่อม Wi-Fi งาน")
        else:
            report("OK", "admin_password", "ตั้งรหัสผ่านเฉพาะแล้ว")
        if len(secret) >= 32 and secret != "075fe918f335a1f06fd5b0a0224ca877":
            report("OK", "misc.secret", "ความยาวเหมาะสมและไม่ใช่ค่าเริ่มต้น")
        else:
            report("FAIL", "misc.secret", "secret สั้นเกินไปหรือยังเป็นค่าเริ่มต้น")
    except Exception as exc:
        report("FAIL", "config.json", f"อ่านไม่สำเร็จ: {exc}")

# 2. SQLite DB check
db_path = root / "database" / "database.sqlite"
if not db_path.is_file():
    report("WARN", "database.sqlite", "ยังไม่พบไฟล์ฐานข้อมูล (จะถูกสร้างเมื่อเปิดแอป)")
else:
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            res = row[0] if row else "unknown"
            if str(res).lower() == "ok":
                report("OK", "database.sqlite", "integrity_check: ok")
            else:
                report("FAIL", "database.sqlite", f"integrity_check: {res}")
        finally:
            conn.close()
    except Exception as exc:
        report("FAIL", "database.sqlite", f"ตรวจไม่สำเร็จ: {exc}")

# 3. Queue and folder JSON files
for rel, label, check_unknown in (
    ("userdata/photobooth_guest_queue.json", "guest_queue", True),
    ("userdata/photobooth_folders.json", "gallery_folders", False),
    ("userdata/photobooth_printers.json", "printer_profiles", False),
):
    p = root / rel
    if not p.is_file():
        report("OK", label, "ยังไม่มีไฟล์ (จะใช้ค่าเริ่มต้นเมื่อเริ่มใช้งาน)")
        continue
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON root must be an object")
        if check_unknown:
            entries = list(data.get("entries", []))
            for hist in data.get("history", []):
                entries.extend(hist.get("entries", []))
            unknown_count = sum(1 for e in entries for j in e.get("print_jobs", []) if j.get("state") == "unknown")
            if unknown_count > 0:
                report("WARN", label, f"อ่านได้ปกติ แต่มีงานพิมพ์สถานะ unknown ค้างอยู่ {unknown_count} รายการ")
            else:
                report("OK", label, f"อ่านได้ปกติ ({len(data.get('entries', []))} คิวในรอบปัจจุบัน)")
        else:
            report("OK", label, "อ่านไฟล์ JSON ได้ปกติ")
    except Exception as exc:
        report("FAIL", label, f"ไฟล์เสียหาย: {exc}")

# 4. Disk space
free_mb = shutil.disk_usage(root).free // (1024 * 1024)
if free_mb >= 2048:
    report("OK", "disk_space", f"เหลือ {free_mb} MB")
elif free_mb >= 512:
    report("WARN", "disk_space", f"เหลือ {free_mb} MB (แนะนำ >= 2048 MB)")
else:
    report("FAIL", "disk_space", f"พื้นที่เหลือน้อยมาก ({free_mb} MB)")

# 5. Node & Cloud QR env
node_bin = shutil.which("node")
if node_bin:
    report("OK", "node", node_bin)
else:
    report("FAIL", "node", "ไม่พบคำสั่ง node สำหรับสร้าง QR SVG")

cloud_env = root / "vercel_public" / ".env.local"
if cloud_env.is_file():
    report("OK", "cloud_qr", "พบ vercel_public/.env.local")
else:
    report("WARN", "cloud_qr", "ไม่พบ vercel_public/.env.local (ยังใช้ QR บน Wi-Fi ท้องถิ่นได้)")

# 6. Check if local server is up and query /api/share/health
try:
    import urllib.request
    with urllib.request.urlopen("http://127.0.0.1:8000/api/share/health", timeout=1.5) as resp:
        health = json.loads(resp.read().decode("utf-8"))
        report("OK" if health.get("status") == "ok" else "WARN", "live_server", f"สถานะเซิร์ฟเวอร์: {health.get('status')}")
        if health.get("camera_connected") is not True:
            report("FAIL", "camera", "ไม่พบกล้อง ระบบยังไม่พร้อมเปิดให้ถ่ายภาพ")
except Exception:
    report("FAIL", "live_server", "ตรวจสถานะเซิร์ฟเวอร์ไม่ได้ ตรวจว่าเว็บเปิดอยู่แล้วลองอีกครั้ง")

print(f"\nสรุป: คำเตือน {warns} รายการ · ไม่ผ่าน {fails} รายการ")
sys.exit(1 if fails > 0 else 0)
PYEOF
