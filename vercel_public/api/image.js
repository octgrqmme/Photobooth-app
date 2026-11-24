import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { get } from '@vercel/blob';
import { photoPath, verifyPhoto } from '../lib/share-token.js';

export default async function handler(request, response) {
  response.setHeader('Cache-Control', 'private, no-store');
  response.setHeader('Referrer-Policy', 'no-referrer');
  response.setHeader('X-Robots-Tag', 'noindex, nofollow, noarchive');
  const data = verifyPhoto(request.query?.token, process.env.BLOB_READ_WRITE_TOKEN);
  if (!data || data.expired) return response.status(410).end('Link expired');
  try {
    const result = await get(photoPath(data.id, data.exp), { access: 'private' });
    if (!result || result.statusCode !== 200) return response.status(404).end('Image not found');
    response.setHeader('Content-Type', 'image/jpeg');
    if (Number.isFinite(result.blob?.size)) {
      response.setHeader('Content-Length', String(result.blob.size));
    }
    if (request.query.download === '1') response.setHeader('Content-Disposition', `attachment; filename="photobooth-${data.id}.jpg"`);
    await pipeline(Readable.fromWeb(result.stream), response);
  } catch (error) {
    console.error('Private photo read failed', error);
    if (!response.headersSent) {
      response.status(503).end('Image temporarily unavailable');
    } else {
      response.destroy();
    }
  }
}
