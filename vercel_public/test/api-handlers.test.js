import assert from 'node:assert/strict';
import test from 'node:test';
import shareHandler from '../api/share.js';
import cleanupHandler from '../api/cleanup.js';

function mockResponse() {
  const headers = {};
  let statusCode = 200;
  let body = '';
  return {
    headers,
    get statusCode() { return statusCode; },
    get body() { return body; },
    setHeader(name, value) { headers[name.toLowerCase()] = value; },
    status(code) { statusCode = code; return this; },
    send(payload) { body = payload; return this; },
    end(payload = '') { body = payload; return this; },
    json(payload) { body = payload; return this; },
  };
}

test('share.js returns text/html utf-8 Content-Type on expired 410 response', () => {
  const response = mockResponse();
  shareHandler({ query: { token: 'invalid' } }, response);
  assert.equal(response.statusCode, 410);
  assert.equal(response.headers['content-type'], 'text/html; charset=utf-8');
  assert.ok(String(response.body).includes('ลิงก์ภาพหมดอายุแล้ว'));
});

test('cleanup.js rejects missing or invalid Bearer tokens with 401', async () => {
  const prevSecret = process.env.CRON_SECRET;
  process.env.CRON_SECRET = 'top-secret-cron-value';
  try {
    const resMissing = mockResponse();
    await cleanupHandler({ headers: {} }, resMissing);
    assert.equal(resMissing.statusCode, 401);

    const resWrong = mockResponse();
    await cleanupHandler({ headers: { authorization: 'Bearer wrong-secret-cron-valu' } }, resWrong);
    assert.equal(resWrong.statusCode, 401);
  } finally {
    if (prevSecret === undefined) delete process.env.CRON_SECRET;
    else process.env.CRON_SECRET = prevSecret;
  }
});
