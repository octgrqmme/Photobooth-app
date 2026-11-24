import QRCode from 'qrcode';
import { readFileSync } from 'node:fs';

const value = process.argv[2] ?? readFileSync(0, 'utf8').trim();
if (!value) {
  throw new Error('A URL is required');
}

const parsed = new URL(value);
if (!['http:', 'https:'].includes(parsed.protocol)) {
  throw new Error('Only HTTP(S) URLs can be encoded');
}

const svg = await QRCode.toString(value, {
  type: 'svg',
  width: 320,
  margin: 2,
  errorCorrectionLevel: 'M',
  color: { dark: '#16110dff', light: '#ffffffff' },
});

process.stdout.write(svg);
