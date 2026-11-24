import { timingSafeEqual } from 'node:crypto';
import { del, list } from '@vercel/blob';

function isAuthorizedCron(headerValue, secret) {
  if (!secret || typeof headerValue !== 'string') return false;
  const expected = Buffer.from(`Bearer ${secret}`, 'utf8');
  const received = Buffer.from(headerValue, 'utf8');
  return received.length === expected.length && timingSafeEqual(received, expected);
}

export default async function handler(request, response) {
  if (!isAuthorizedCron(request.headers.authorization, process.env.CRON_SECRET)) return response.status(401).end('Unauthorized');
  const now = Math.floor(Date.now() / 1000);
  let cursor;
  let deleted = 0;
  do {
    const page = await list({ prefix: 'photos/', limit: 1000, cursor });
    const expired = page.blobs.filter((blob) => {
      const match = /^photos\/(\d+)\/[0-9a-f-]+\.jpg$/i.exec(blob.pathname);
      return match && Number(match[1]) <= now;
    });
    if (expired.length) {
      await del(expired.map((blob) => blob.url));
      deleted += expired.length;
    }
    cursor = page.hasMore ? page.cursor : undefined;
  } while (cursor);
  response.setHeader('Cache-Control', 'no-store');
  response.status(200).json({ deleted });
}
