# 📸 Official Photobooth-App (photobooth-app.org)

ติดตั้งและตั้งค่าระบบ **Photobooth-App** ตัวจริงจากโครงการทางการ [https://photobooth-app.org](https://photobooth-app.org) (v9.0.0) พร้อมเชื่อมต่อกล้องและเครื่องพิมพ์สำหรับงานอีเวนต์เรียบร้อยแล้ว

---

## 🌐 ลิงก์เข้าใช้งานระบบ (Access URLs)

* **🖥️ หน้าจอหลักตู้ถ่ายรูป (Kiosk UI):**
  [http://localhost:8000](http://localhost:8000)
* **📱 บนมือถือหรืออุปกรณ์อื่นในวง Wi-Fi เดียวกัน:**
  `http://172.28.121.125:8000`
* **⚙️ หน้าต่างจัดการผู้ดูแลระบบ (Admin Panel):**
  [http://localhost:8000/#/admin](http://localhost:8000/#/admin)
  * **รหัสผ่านผู้ดูแลระบบ (Default Admin Password):** `0000`

---

## 🖨️ การเชื่อมต่อเครื่องปริ้น (Printer Configuration)

ในไฟล์คอนฟิก [config/config.json](file:///Users/octagram/Photobooth-app/config/config.json) ระบบได้ผูกคำสั่งเข้ากับเครื่องพิมพ์ **HP Ink Tank Wireless 410 series** เรียบร้อยแล้ว:
* **ชื่อเครื่องพิมพ์:** `HP_Ink_Tank_Wireless_410_series`
* **คำสั่งพิมพ์ตรงผ่าน CUPS:**
  ```bash
  lp -d {printer_name} -n {copies} -o fit-to-page "{filename}"
  ```
* เมื่อกดพิมพ์จากหน้า Gallery หรือหลังถ่ายภาพ ระบบจะส่งไฟล์ภาพตรงไปยังเครื่องพิมพ์ HP ทันที

---

## 📷 การเชื่อมต่อกล้อง (Camera Configuration)

* **กล้องเริ่มต้น:** กล้องของ MacBook Air (FaceTime HD Camera / Device index `0`)
* **Backend:** PyAV (AVFoundation Hardware Capture บน macOS)
* **ความละเอียด:** 1280x720 @ 30 FPS (Pixel Format: NV12)
* **รองรับกล้องเพิ่มเติม:** สามารถเชื่อมต่อกล้อง DSLR/Mirrorless ผ่านการ์ดแคปเจอร์ HDMI หรือ gphoto2 โดยเลือกเปลี่ยนได้ผ่านหน้า Admin Panel

---

## 🚀 วิธีการเปิดใช้งาน (How to Run)

สามารถเปิดใช้งานได้ 2 วิธี:

### วิธีที่ 1: ผ่าน npm
```bash
npm start
```

### วิธีที่ 2: ผ่านสคริปต์รัน
```bash
./run.sh
```

---

## 📂 โครงสร้างโฟลเดอร์สำคัญ

```text
Photobooth-app/
├── config/
│   └── config.json          # ไฟล์การตั้งค่าหลักของ photobooth-app (กล้อง, เครื่องพิมพ์, ดีไซน์)
├── venv/                    # Python 3.12 Virtual Environment พร้อม photobooth-app v9.0.0
├── run.sh                   # สคริปต์เปิดใช้งานระบบ
├── package.json             # คำสั่ง npm start
└── README.md                # เอกสารคู่มือ
```
