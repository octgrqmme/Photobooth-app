# คู่มือ Backup และ Restore ระบบ Photobooth

เอกสารนี้ครอบคลุมขั้นตอนการสำรองข้อมูลและกู้คืนข้อมูลสำคัญของระบบ Photobooth บน Mac (รวม State, Config, Templates, SQLite Database และตัวเลือกสำรองไฟล์ภาพ)

---

## 1. สิ่งที่สคริปต์สำรองให้

| ข้อมูล | ตำแหน่ง | ค่าเริ่มต้น | เมื่อใส่ `--include-media` |
|---|---|---:|---:|
| ฐานข้อมูล Photobooth (Online SQLite Backup) | `database/database.sqlite` | ✓ | ✓ |
| การตั้งค่าระบบ & รหัส Admin / Secret | `config/config.json` | ✓ | ✓ |
| คิวผู้เข้าถ่ายและประวัติรอบงาน | `userdata/photobooth_guest_queue.json` | ✓ | ✓ |
| โฟลเดอร์โครงการ & การจัดกลุ่มภาพ | `userdata/photobooth_folders.json` | ✓ | ✓ |
| โปรไฟล์เครื่องพิมพ์ CUPS | `userdata/photobooth_printers.json` | ✓ | ✓ |
| สถานะลิงก์ Cloud QR | `userdata/photobooth_cloud_shares.json` | ✓ | ✓ |
| เทมเพลตที่สร้างเองและกรอบภาพ | `userdata/photobooth_templates/` | ✓ | ✓ |
| ไฟล์ภาพถ่ายต้นฉบับและภาพสำเร็จรูป | `media/` | — | ✓ |

ทุกชุดสำรองจะมีไฟล์ `manifest.json` ที่เก็บ `sha256` และขนาดของทุกไฟล์ไว้เพื่อตรวจความสมบูรณ์ก่อนกู้คืนเสมอ

---

## 2. วิธีสำรองข้อมูล (Backup)

> [!TIP]
> ควรรันคำสั่งสำรองก่อนเริ่มงานจริงทุกครั้ง และหลังจบแต่ละรอบงาน/จบวัน

```bash
# สำรอง State, Config, Templates และ Database (เร็วและไฟล์เล็ก)
bash scripts/backup.sh

# ระบุโฟลเดอร์ปลายทางเอง (เช่น ลง External Drive)
bash scripts/backup.sh --output /Volumes/BackupDrive/photobooth-20261123

# สำรองรวมไฟล์ภาพถ่ายทั้งหมดใน media/
bash scripts/backup.sh --include-media --output /Volumes/BackupDrive/photobooth-full-20261123
```

---

## 3. วิธีตรวจสอบไฟล์สำรองก่อนกู้คืน (Dry-Run)

ใช้โหมด `--dry-run` เพื่อตรวจ `manifest.json`, เทียบค่า SHA-256 ทุกไฟล์ และรัน `PRAGMA integrity_check` บน `database.sqlite` โดยไม่แตะต้องไฟล์จริง:

```bash
bash scripts/restore.sh backups/photobooth-backup-YYYYMMDD-HHMMSS --dry-run
```

---

## 4. วิธีกู้คืนข้อมูล (Restore)

> [!IMPORTANT]
> ปิดแอป Photobooth (`Ctrl+C` ใน Terminal ที่รัน `npm start`) ก่อนรัน `restore.sh` เพื่อป้องกันไฟล์ฐานข้อมูลและ JSON ถูกเขียนทับระหว่างกู้คืน

```bash
# 1. ทดสอบตรวจความสมบูรณ์ของชุดสำรอง
bash scripts/restore.sh backups/photobooth-backup-YYYYMMDD-HHMMSS --dry-run

# 2. กู้คืนจริง (สคริปต์จะสร้าง safety backup ไว้ใน backups/pre-restore-* ให้อัตโนมัติก่อนทับ)
bash scripts/restore.sh backups/photobooth-backup-YYYYMMDD-HHMMSS

# 3. ตรวจความพร้อมหลังกู้คืน
bash scripts/preflight.sh
```

### กู้คืนลงโฟลเดอร์ทดสอบ (Drill / Isolated Target)

```bash
mkdir -p /tmp/pb-restore-drill
bash scripts/restore.sh backups/photobooth-backup-YYYYMMDD-HHMMSS --target /tmp/pb-restore-drill --no-safety-backup
```

---

## 5. Checklist ซ้อมกู้คืนก่อนวันงาน (Pre-Event Drill)

- [ ] รัน `bash scripts/backup.sh --include-media` ลง External USB/SSD สำรอง
- [ ] รัน `bash scripts/restore.sh <backup_dir> --dry-run` และตรวจว่าผ่านทุกไฟล์
- [ ] ทดสอบ Restore ลงโฟลเดอร์แยก (`--target`) และตรวจว่า `database.sqlite` และไฟล์ JSON ครบตรงตามต้นฉบับ
- [ ] รัน `bash scripts/preflight.sh` เพื่อยืนยันว่าระบบหลักไม่มีรายการ `[FAIL]`
