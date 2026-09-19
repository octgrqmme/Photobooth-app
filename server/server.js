import express from 'express';
import cors from 'cors';
import path from 'path';
import fs from 'fs';
import os from 'os';
import { fileURLToPath } from 'url';
import { PrinterService } from './printerService.js';
import { PhotoStorage, PRINTS_DIR, CAPTURES_DIR } from './photoStorage.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const PORT = process.env.PORT || 3001;

// Get active local network IP address for QR code sharing on local Wi-Fi
function getLocalIpAddress() {
  const interfaces = os.networkInterfaces();
  for (const name of Object.keys(interfaces)) {
    for (const iface of interfaces[name]) {
      if (iface.family === 'IPv4' && !iface.internal) {
        return iface.address;
      }
    }
  }
  return 'localhost';
}

const LOCAL_IP = getLocalIpAddress();

// Middleware
app.use(cors());
app.use(express.json({ limit: '50mb' }));
app.use(express.urlencoded({ extended: true, limit: '50mb' }));

// Static file serving
app.use('/prints', express.static(PRINTS_DIR));
app.use('/uploads/captures', express.static(CAPTURES_DIR));

// Serve client dist if built
const clientDist = path.join(__dirname, '..', 'client', 'dist');
if (fs.existsSync(clientDist)) {
  app.use(express.static(clientDist));
}

// ----------------- API ENDPOINTS -----------------

// 1. Get system & network info
app.get('/api/system-info', async (req, res) => {
  try {
    const printerInfo = await PrinterService.getPrinters();
    res.json({
      localIp: LOCAL_IP,
      port: PORT,
      baseUrl: `http://${LOCAL_IP}:${PORT}`,
      printers: printerInfo.printers,
      defaultPrinter: printerInfo.defaultPrinter,
      hostname: os.hostname(),
      platform: os.platform()
    });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

// 2. Get list of available printers
app.get('/api/printers', async (req, res) => {
  try {
    const data = await PrinterService.getPrinters();
    res.json(data);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

// 3. Print a photo
app.post('/api/print', async (req, res) => {
  try {
    const { sessionId, imageData, printerName, copies = 1, paperSize = 'iso_a4_210x297mm' } = req.body;

    let targetFilePath = null;

    if (sessionId) {
      const session = PhotoStorage.getSessionById(sessionId);
      if (!session) {
        return res.status(404).json({ error: `Session ${sessionId} not found` });
      }
      targetFilePath = session.printFilePath;
    } else if (imageData) {
      // Temporary print file
      const tempFileName = `temp_print_${Date.now()}.png`;
      targetFilePath = path.join(PRINTS_DIR, tempFileName);
      const base64Data = imageData.replace(/^data:image\/\w+;base64,/, '');
      fs.writeFileSync(targetFilePath, Buffer.from(base64Data, 'base64'));
    } else {
      return res.status(400).json({ error: 'Either sessionId or imageData is required' });
    }

    console.log(`Sending print request to printer "${printerName}" with ${copies} copies...`);
    const printResult = await PrinterService.printFile(targetFilePath, {
      printerName,
      copies,
      paperSize
    });

    res.json({
      success: true,
      message: 'Print job dispatched successfully',
      ...printResult
    });
  } catch (error) {
    console.error('Print error:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// 4. Save a new capture session (print strip + raw shots)
app.post('/api/photos/save', async (req, res) => {
  try {
    const { imageData, rawCaptures, layout, config } = req.body;
    if (!imageData) {
      return res.status(400).json({ error: 'imageData is required' });
    }

    const baseUrl = `http://${LOCAL_IP}:${PORT}`;
    const session = await PhotoStorage.saveSession({
      imageData,
      rawCaptures,
      layout,
      config,
      baseUrl
    });

    res.json({
      success: true,
      session
    });
  } catch (error) {
    console.error('Save session error:', error);
    res.status(500).json({ error: error.message });
  }
});

// 5. Get all photo sessions (gallery)
app.get('/api/photos', (req, res) => {
  try {
    const sessions = PhotoStorage.getSessions();
    res.json({ sessions });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

// 6. Get single session
app.get('/api/photos/:id', (req, res) => {
  try {
    const session = PhotoStorage.getSessionById(req.params.id);
    if (!session) {
      return res.status(404).json({ error: 'Session not found' });
    }
    res.json({ session });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

// 7. Delete session
app.delete('/api/photos/:id', (req, res) => {
  try {
    const deleted = PhotoStorage.deleteSession(req.params.id);
    if (!deleted) {
      return res.status(404).json({ error: 'Session not found' });
    }
    res.json({ success: true });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

// 8. Mobile QR Code Download Page
app.get('/share/:id', (req, res) => {
  const session = PhotoStorage.getSessionById(req.params.id);
  if (!session) {
    return res.status(404).send(`
      <!DOCTYPE html>
      <html lang="th">
        <head><meta charset="utf-8"><title>ไม่พบรูปภาพ</title><meta name="viewport" content="width=device-width, initial-scale=1"></head>
        <body style="font-family: sans-serif; text-align:center; padding: 50px 20px; background: #0f172a; color: white;">
          <h2>⚠️ ไม่พบรูปภาพ หรือรูปภาพนี้หมดอายุแล้ว</h2>
          <p>กรุณาสแกน QR Code ใหม่อีกครั้งที่หน้าตู้ Photobooth</p>
        </body>
      </html>
    `);
  }

  const imageUrl = session.printUrl;
  const eventTitle = session.config?.title || 'Photobooth Memory';

  res.send(`
    <!DOCTYPE html>
    <html lang="th">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
      <title>${eventTitle} - ดาวน์โหลดรูป Photobooth</title>
      <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
          background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%);
          color: #f8fafc;
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
          min-height: 100vh;
          display: flex;
          flex-direction: column;
          align-items: center;
          padding: 24px 16px 40px;
        }
        .container {
          max-width: 480px;
          width: 100%;
          display: flex;
          flex-direction: column;
          align-items: center;
          text-align: center;
        }
        .header {
          margin-bottom: 20px;
        }
        .header h1 {
          font-size: 22px;
          font-weight: 700;
          color: #38bdf8;
          margin-bottom: 6px;
        }
        .header p {
          font-size: 14px;
          color: #94a3b8;
        }
        .image-card {
          background: rgba(255, 255, 255, 0.05);
          backdrop-filter: blur(10px);
          padding: 12px;
          border-radius: 20px;
          border: 1px solid rgba(255, 255, 255, 0.1);
          box-shadow: 0 20px 40px rgba(0,0,0,0.5);
          margin-bottom: 24px;
          width: 100%;
          display: flex;
          justify-content: center;
        }
        .image-card img {
          max-width: 100%;
          height: auto;
          max-height: 65vh;
          border-radius: 12px;
          box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        }
        .btn-download {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          gap: 10px;
          width: 100%;
          background: linear-gradient(135deg, #06b6d4 0%, #3b82f6 100%);
          color: white;
          text-decoration: none;
          padding: 16px 24px;
          border-radius: 14px;
          font-size: 17px;
          font-weight: 600;
          box-shadow: 0 10px 25px -5px rgba(59, 130, 246, 0.5);
          border: none;
          cursor: pointer;
          transition: transform 0.1s, opacity 0.2s;
        }
        .btn-download:active {
          transform: scale(0.98);
        }
        .hint {
          margin-top: 14px;
          font-size: 13px;
          color: #64748b;
        }
      </style>
    </head>
    <body>
      <div class="container">
        <div class="header">
          <h1>📸 ${eventTitle}</h1>
          <p>รูปความทรงจำของคุณพร้อมดาวน์โหลดแล้ว!</p>
        </div>
        <div class="image-card">
          <img src="${imageUrl}" alt="Photobooth Snap">
        </div>
        <a href="${imageUrl}" download="photobooth_${Date.now()}.png" class="btn-download">
          ⬇️ แตะเพื่อบันทึกรูปภาพ (Save Photo)
        </a>
        <p class="hint">หรือแตะค้างที่รูปภาพแล้วเลือก "เพิ่มไปยังแอปรูปภาพ (Save to Photos)"</p>
      </div>
    </body>
    </html>
  `);
});

// Fallback to client app for any other route (Express 5 compatible)
app.use((req, res) => {
  if (fs.existsSync(path.join(clientDist, 'index.html'))) {
    res.sendFile(path.join(clientDist, 'index.html'));
  } else {
    res.send(`
      <div style="font-family: sans-serif; text-align: center; padding: 50px;">
        <h2>Photobooth Backend Server is Running!</h2>
        <p>Frontend dev server runs at <a href="http://localhost:5173">http://localhost:5173</a></p>
      </div>
    `);
  }
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`=========================================`);
  console.log(`🎉 Photobooth Server running!`);
  console.log(`📡 Local Web: http://localhost:${PORT}`);
  console.log(`📱 Wi-Fi Mobile: http://${LOCAL_IP}:${PORT}`);
  console.log(`=========================================`);
});
