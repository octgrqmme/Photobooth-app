import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import QRCode from 'qrcode';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const DATA_DIR = path.join(__dirname, '..', 'data');
const PRINTS_DIR = path.join(DATA_DIR, 'prints');
const CAPTURES_DIR = path.join(DATA_DIR, 'captures');
const DB_FILE = path.join(DATA_DIR, 'sessions.json');

// Ensure directories exist
if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
if (!fs.existsSync(PRINTS_DIR)) fs.mkdirSync(PRINTS_DIR, { recursive: true });
if (!fs.existsSync(CAPTURES_DIR)) fs.mkdirSync(CAPTURES_DIR, { recursive: true });

function loadSessions() {
  try {
    if (fs.existsSync(DB_FILE)) {
      const data = fs.readFileSync(DB_FILE, 'utf8');
      return JSON.parse(data);
    }
  } catch (e) {
    console.error('Error reading sessions database:', e);
  }
  return [];
}

function saveSessions(sessions) {
  try {
    fs.writeFileSync(DB_FILE, JSON.stringify(sessions, null, 2), 'utf8');
  } catch (e) {
    console.error('Error saving sessions database:', e);
  }
}

export class PhotoStorage {
  /**
   * Save a new photobooth print session
   * @param {object} param0
   * @param {string} param0.imageData - Base64 data URL of the final rendered print strip/photo
   * @param {Array<string>} param0.rawCaptures - Array of base64 raw photo captures
   * @param {string} param0.layout - 'single' | 'strip3' | 'strip4' | 'grid' | 'dual'
   * @param {object} param0.config - event title, colors, filter, etc.
   * @param {string} param0.baseUrl - Network base URL (e.g. http://192.168.1.50:3001) for QR code
   */
  static async saveSession({ imageData, rawCaptures = [], layout = 'strip4', config = {}, baseUrl = '' }) {
    const id = `session_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`;
    const timestamp = new Date().toISOString();

    // 1. Save final print image
    const printFileName = `${id}_print.png`;
    const printFilePath = path.join(PRINTS_DIR, printFileName);
    const base64Data = imageData.replace(/^data:image\/\w+;base64,/, '');
    fs.writeFileSync(printFilePath, Buffer.from(base64Data, 'base64'));

    // 2. Save raw captures if provided
    const captureFiles = [];
    rawCaptures.forEach((raw, index) => {
      const captureFileName = `${id}_capture_${index + 1}.jpg`;
      const captureFilePath = path.join(CAPTURES_DIR, captureFileName);
      const rawBase64 = raw.replace(/^data:image\/\w+;base64,/, '');
      fs.writeFileSync(captureFilePath, Buffer.from(rawBase64, 'base64'));
      captureFiles.push(`/uploads/captures/${captureFileName}`);
    });

    // 3. Generate QR code link for mobile download
    const shareUrl = `${baseUrl}/share/${id}`;
    let qrCodeDataUrl = '';
    try {
      qrCodeDataUrl = await QRCode.toDataURL(shareUrl, {
        margin: 2,
        width: 300,
        color: {
          dark: '#1e293b',
          light: '#ffffff'
        }
      });
    } catch (qrErr) {
      console.error('Error generating QR code:', qrErr);
    }

    const session = {
      id,
      timestamp,
      layout,
      printUrl: `/prints/${printFileName}`,
      printFilePath,
      captureFiles,
      config,
      shareUrl,
      qrCodeDataUrl
    };

    const sessions = loadSessions();
    sessions.unshift(session);
    saveSessions(sessions);

    return session;
  }

  static getSessions() {
    return loadSessions();
  }

  static getSessionById(id) {
    const sessions = loadSessions();
    return sessions.find(s => s.id === id) || null;
  }

  static deleteSession(id) {
    const sessions = loadSessions();
    const index = sessions.findIndex(s => s.id === id);
    if (index === -1) return false;

    const session = sessions[index];
    if (session.printFilePath && fs.existsSync(session.printFilePath)) {
      try { fs.unlinkSync(session.printFilePath); } catch (e) { console.error(e); }
    }

    sessions.splice(index, 1);
    saveSessions(sessions);
    return true;
  }
}

export { PRINTS_DIR, CAPTURES_DIR };
