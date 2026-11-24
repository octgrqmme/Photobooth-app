import { createHmac, timingSafeEqual } from 'node:crypto';

export const PHOTO_TTL_SECONDS = 60 * 60;

export function photoPath(id, expiresAt) {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) throw new Error('Invalid photo ID');
  if (!Number.isSafeInteger(expiresAt) || expiresAt <= 0) throw new Error('Invalid expiry');
  return `photos/${expiresAt}/${id.toLowerCase()}.jpg`;
}

export function signPhoto(id, expiresAt, secret) {
  photoPath(id, expiresAt);
  if (!secret) throw new Error('Missing photo signing secret');
  const payload = Buffer.from(JSON.stringify({ id: id.toLowerCase(), exp: expiresAt })).toString('base64url');
  const signature = createHmac('sha256', secret).update(payload).digest('base64url');
  return `${payload}.${signature}`;
}

export function verifyPhoto(token, secret, now = Date.now()) {
  if (!secret || typeof token !== 'string' || token.length > 512) return null;
  const parts = token.split('.');
  if (parts.length !== 2) return null;
  const expected = createHmac('sha256', secret).update(parts[0]).digest();
  let received;
  try { received = Buffer.from(parts[1], 'base64url'); } catch { return null; }
  if (received.length !== expected.length || !timingSafeEqual(received, expected)) return null;
  try {
    const data = JSON.parse(Buffer.from(parts[0], 'base64url').toString('utf8'));
    photoPath(data.id, data.exp);
    if (now >= data.exp * 1000) return { expired: true, ...data };
    return data;
  } catch { return null; }
}
