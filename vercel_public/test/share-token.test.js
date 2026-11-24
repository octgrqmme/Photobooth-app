import assert from 'node:assert/strict';
import test from 'node:test';
import { PHOTO_TTL_SECONDS, photoPath, signPhoto, verifyPhoto } from '../lib/share-token.js';

test('new QR links last one hour', () => {
  assert.equal(PHOTO_TTL_SECONDS, 3600);
});

const id = '123e4567-e89b-42d3-a456-426614174000';
const secret = 'test-only-secret';
const expiresAt = 2_000_000_000;

test('signed photo links verify before expiry and fail after expiry', () => {
  const token = signPhoto(id, expiresAt, secret);
  assert.deepEqual(verifyPhoto(token, secret, (expiresAt - 1) * 1000), { id, exp: expiresAt });
  assert.equal(verifyPhoto(token, secret, expiresAt * 1000).expired, true);
});

test('tampered, wrong-key, and malformed tokens are rejected', () => {
  const token = signPhoto(id, expiresAt, secret);
  assert.equal(verifyPhoto(`${token}x`, secret), null);
  assert.equal(verifyPhoto(token, 'another-key'), null);
  assert.equal(verifyPhoto('not-a-token', secret), null);
  assert.throws(() => photoPath('../invalid', expiresAt));
});
