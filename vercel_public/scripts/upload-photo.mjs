import { readFile } from 'node:fs/promises';
import { put } from '@vercel/blob';
import { PHOTO_TTL_SECONDS, photoPath, signPhoto } from '../lib/share-token.js';

const [filename, id, requestedTtl] = process.argv.slice(2);
const secret = process.env.BLOB_READ_WRITE_TOKEN;
const origin = (process.env.PHOTO_PUBLIC_ORIGIN || 'https://sci-lywood-photobooth.vercel.app').replace(/\/$/, '');
if (!filename || !id || !secret || !origin.startsWith('https://')) throw new Error('Cloud photo upload is not configured');
const ttlSeconds = requestedTtl === undefined ? PHOTO_TTL_SECONDS : Number(requestedTtl);
if (!Number.isSafeInteger(ttlSeconds) || ttlSeconds < 60 || ttlSeconds > 24 * 60 * 60) throw new Error('Photo TTL must be between 60 seconds and 24 hours');

const image = await readFile(filename);
if (image.length === 0 || image.length > 30_000_000 || image[0] !== 0xff || image[1] !== 0xd8) throw new Error('Expected a JPEG under 30 MB');
const expiresAt = Math.floor(Date.now() / 1000) + ttlSeconds;
const pathname = photoPath(id, expiresAt);
await put(pathname, image, { access: 'private', addRandomSuffix: false, allowOverwrite: false, contentType: 'image/jpeg' });
const token = signPhoto(id, expiresAt, secret);
process.stdout.write(JSON.stringify({ share_url: `${origin}/api/share?token=${encodeURIComponent(token)}`, expires_at: expiresAt }) + '\n');
