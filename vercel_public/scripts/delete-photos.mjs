import { del } from '@vercel/blob';
import { photoPath } from '../lib/share-token.js';

if (!process.env.BLOB_READ_WRITE_TOKEN) throw new Error('Missing Blob token');
let input = '';
for await (const chunk of process.stdin) input += chunk;
const records = JSON.parse(input);
if (!Array.isArray(records) || records.length > 2000) throw new Error('Invalid deletion batch');
const paths = records.map(({ id, expires_at }) => photoPath(id, expires_at));
for (let i = 0; i < paths.length; i += 100) {
  await del(paths.slice(i, i + 100), { token: process.env.BLOB_READ_WRITE_TOKEN });
}
process.stdout.write(JSON.stringify({ deleted: paths.length }) + '\n');
