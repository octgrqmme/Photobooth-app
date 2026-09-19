export const translations = {
  th: {
    appTitle: 'Photobooth Studio',
    subtitle: 'ระบบเชื่อมต่อกล้องและสั่งพิมพ์รูปภาพ',
    startCapture: 'แตะเพื่อถ่ายรูป',
    pressSpace: 'หรือกดปุ่ม Spacebar',
    shotCounter: 'ช็อตที่ {current} จาก {total}',
    getReady: 'เตรียมตัวให้พร้อม!',
    smile: 'ยิ้มเลย! 📸',
    
    // Layouts
    layouts: {
      single: '1 รูปเดี่ยว',
      strip3: '3 ช็อต คลาสสิก',
      strip4: '4 ช็อต สไตล์เกาหลี',
      dual: '2 แถวคู่ (ตัดครึ่ง 4x6")',
      grid: 'ตาราง 2x2'
    },

    // Filters
    filters: {
      none: 'ปกติ (Original)',
      bw: 'ขาวดำ (B&W)',
      sepia: 'วินเทจ (Sepia)',
      warm: 'อบอุ่น (Warm)',
      cool: 'โทนเย็น (Cool)',
      glamour: 'ออร่า (Glamour)',
      vivid: 'สีสด (Vivid)'
    },

    // Actions
    printNow: 'สั่งพิมพ์รูปภาพ',
    printing: 'กำลังส่งคำสั่งพิมพ์...',
    printSuccess: 'ส่งคำสั่งพิมพ์ไปยังเครื่องปริ้นสำเร็จ!',
    printFail: 'ไม่สามารถพิมพ์ได้ กรุณาตรวจสอบเครื่องปริ้น',
    directPrint: 'พิมพ์ทันที (Direct CUPS)',
    browserPrint: 'พิมพ์ผ่านบราวเซอร์',
    copies: 'จำนวนชุด',
    saveMobile: 'สแกนรับรูปเข้ามือถือ (QR Code)',
    download: 'ดาวน์โหลดรูปลงเครื่อง',
    retake: 'ถ่ายใหม่',
    saveAndDone: 'เสร็จสิ้น & บันทึก',
    gallery: 'แกลเลอรี',
    settings: 'ตั้งค่า',
    fullscreen: 'เต็มจอ',
    exitFullscreen: 'ออกจากเต็มจอ',

    // Status
    printerReady: 'เครื่องปริ้นพร้อมใช้งาน',
    printerOffline: 'เครื่องปริ้นออฟไลน์',
    noPrinter: 'ไม่พบเครื่องปริ้น',
    cameraActive: 'กล้องพร้อมใช้งาน',
    cameraError: 'ไม่สามารถเปิดกล้องได้ กรุณาอนุญาตการเข้าถึงกล้อง',

    // Settings Modal
    settingsTitle: 'ตั้งค่าระบบ Photobooth',
    printerSettings: 'การเชื่อมต่อเครื่องปริ้น',
    selectedPrinter: 'เครื่องพิมพ์ที่เลือก',
    paperSize: 'ขนาดกระดาษ',
    testPrint: 'ทดสอบพิมพ์กระดาษ',
    autoPrint: 'สั่งพิมพ์อัตโนมัติทันทีหลังถ่ายเสร็จ',
    cameraSettings: 'การเชื่อมต่อกล้อง',
    selectedCamera: 'กล้องที่ใช้งาน',
    mirrorCamera: 'กลับด้านภาพกล้อง (Mirror View)',
    countdownDuration: 'เวลานับถอยหลัง (วินาที)',
    intervalBetweenShots: 'ช่วงเวลาพักระหว่างช็อต (วินาที)',
    brandingSettings: 'ข้อความและธีมงาน (Branding)',
    eventTitleText: 'ชื่องาน / กิจกรรม',
    eventSubtitleText: 'สถานที่ / ข้อความรอง',
    frameColorLabel: 'สีกรอบรูป',
    soundEnabled: 'เปิดเสียงเอฟเฟกต์ (Beep & Shutter)',
    saveSettings: 'บันทึกการตั้งค่า',

    // QR Share Modal
    qrTitle: 'ดาวน์โหลดรูปภาพเข้าสมาร์ทโฟน',
    qrInstruction: 'เชื่อมต่อ Wi-Fi เดียวกัน แล้วใช้กล้องมือถือสแกน QR Code นี้เพื่อโหลดรูปทันที',
    qrNetworkUrl: 'หรือเปิดลิงก์บนเบราว์เซอร์:',
    copied: 'คัดลอกลิงก์แล้ว!',
    copyLink: 'คัดลอกลิงก์',
    close: 'ปิดหน้าต่าง'
  },
  en: {
    appTitle: 'Photobooth Studio',
    subtitle: 'Camera & Instant Printer System',
    startCapture: 'Tap to Capture',
    pressSpace: 'or press Spacebar',
    shotCounter: 'Shot {current} of {total}',
    getReady: 'Get Ready!',
    smile: 'Smile! 📸',
    
    // Layouts
    layouts: {
      single: '1 Single Photo',
      strip3: '3-Shot Classic',
      strip4: '4-Shot Strip',
      dual: 'Dual Strip (4x6" Cut)',
      grid: '2x2 Grid'
    },

    // Filters
    filters: {
      none: 'Original',
      bw: 'B & W',
      sepia: 'Vintage Sepia',
      warm: 'Warm Tone',
      cool: 'Cool Cyber',
      glamour: 'Glamour Glow',
      vivid: 'Vivid Colors'
    },

    // Actions
    printNow: 'Print Photo',
    printing: 'Sending to printer...',
    printSuccess: 'Sent to printer successfully!',
    printFail: 'Failed to print. Please check printer.',
    directPrint: 'Direct Print (CUPS)',
    browserPrint: 'Browser Print Dialog',
    copies: 'Copies',
    saveMobile: 'QR Code Mobile Download',
    download: 'Download to Disk',
    retake: 'Take Again',
    saveAndDone: 'Done & Save',
    gallery: 'Gallery',
    settings: 'Settings',
    fullscreen: 'Fullscreen',
    exitFullscreen: 'Exit Fullscreen',

    // Status
    printerReady: 'Printer Ready',
    printerOffline: 'Printer Offline',
    noPrinter: 'No Printer Detected',
    cameraActive: 'Camera Ready',
    cameraError: 'Cannot access camera. Check permissions.',

    // Settings Modal
    settingsTitle: 'Photobooth Settings',
    printerSettings: 'Printer Configuration',
    selectedPrinter: 'Active Printer',
    paperSize: 'Paper Size',
    testPrint: 'Print Test Page',
    autoPrint: 'Auto-print immediately after capture',
    cameraSettings: 'Camera Configuration',
    selectedCamera: 'Active Camera',
    mirrorCamera: 'Mirror Camera Preview',
    countdownDuration: 'Countdown Timer (seconds)',
    intervalBetweenShots: 'Interval between shots (seconds)',
    brandingSettings: 'Branding & Templates',
    eventTitleText: 'Event Title',
    eventSubtitleText: 'Subtitle / Venue',
    frameColorLabel: 'Frame Background Color',
    soundEnabled: 'Sound Effects (Beep & Shutter)',
    saveSettings: 'Save Settings',

    // QR Share Modal
    qrTitle: 'Download Photo to Smartphone',
    qrInstruction: 'Connect to the same Wi-Fi and scan this QR code with your phone camera',
    qrNetworkUrl: 'Or browse directly to:',
    copied: 'Link copied!',
    copyLink: 'Copy URL',
    close: 'Close'
  }
};
