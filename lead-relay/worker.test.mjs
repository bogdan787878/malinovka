// node --test lead-relay/   — проверка логики ретранслятора без развёртывания (fetch подменён)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import worker from './worker.mjs';

const ORIGIN = 'https://malinovka-park2.ru';
const ENV = { ROISTAT_URL: 'https://cloud.roistat.com/integration/webhook?key=TEST' };
const LEAD = { name: 'Иван', phone: '+7 (999) 123-45-67', title: 'форма', comment: 'Страница: https://x', formid: 'form1', roistat_visit: '510596', roistat_url: 'https://x', cookies: 'a=b' };

function req(body, { method = 'POST', origin = ORIGIN } = {}) {
  return new Request('https://relay.example/', {
    method,
    headers: { 'Content-Type': 'text/plain;charset=UTF-8', ...(origin ? { Origin: origin } : {}) },
    body: method === 'POST' ? (typeof body === 'string' ? body : JSON.stringify(body)) : undefined,
  });
}
const ctx = { waitUntil() {} };

function mockFetch(handler) {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), init });
    return handler(String(url), init, calls.length);
  };
  return calls;
}
const ok = () => new Response('{"status":"success"}', { status: 200 });

test('preflight: 204 и CORS для разрешённого домена', async () => {
  const res = await worker.fetch(req(null, { method: 'OPTIONS' }), ENV, ctx);
  assert.equal(res.status, 204);
  assert.equal(res.headers.get('Access-Control-Allow-Origin'), ORIGIN);
});

test('чужой Origin отклоняется', async () => {
  const calls = mockFetch(ok);
  const res = await worker.fetch(req(LEAD, { origin: 'https://evil.example' }), ENV, ctx);
  assert.equal(res.status, 403);
  assert.equal(calls.length, 0);
});

test('битый JSON -> 400, телефон не из цифр -> 422, GET -> 405', async () => {
  mockFetch(ok);
  assert.equal((await worker.fetch(req('{not json'), ENV, ctx)).status, 400);
  assert.equal((await worker.fetch(req({ ...LEAD, phone: 'abc' }), ENV, ctx)).status, 422);
  assert.equal((await worker.fetch(req(null, { method: 'GET' }), ENV, ctx)).status, 405);
});

test('honeypot: отвечаем ok, в Roistat не шлём', async () => {
  const calls = mockFetch(ok);
  const res = await worker.fetch(req({ ...LEAD, website: 'http://spam' }), ENV, ctx);
  assert.equal(res.status, 200);
  assert.equal(calls.length, 0);
});

test('успех: одна отправка в Roistat формой с нужными полями', async () => {
  const calls = mockFetch(ok);
  const res = await worker.fetch(req(LEAD), ENV, ctx);
  const body = await res.json();
  assert.equal(res.status, 200);
  assert.deepEqual([body.ok, body.roistat], [true, true]);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, ENV.ROISTAT_URL);
  const form = Object.fromEntries(new URLSearchParams(calls[0].init.body));
  assert.equal(form.phone, '+79991234567');
  assert.equal(form.name, 'Иван');
  assert.equal(form.roistat_visit, '510596');
  assert.equal(form.roistat_fields_roistat, '510596');
  assert.equal(form.COOKIES, 'a=b');
});

test('Roistat сбоит дважды, с третьей попытки доходит', async () => {
  const calls = mockFetch((u, i, n) => { if (n < 3) throw new TypeError('network'); return ok(); });
  const res = await worker.fetch(req(LEAD), ENV, ctx);
  assert.equal(res.status, 200);
  assert.equal(calls.length, 3);
});

test('Roistat недоступен совсем: 502, три попытки', async () => {
  const calls = mockFetch(() => { throw new TypeError('network'); });
  const res = await worker.fetch(req(LEAD), ENV, ctx);
  assert.equal(res.status, 502);
  assert.equal((await res.json()).ok, false);
  assert.equal(calls.length, 3);
});

test('Roistat недоступен, но копия в Telegram дошла: заявка не потеряна', async () => {
  const env = { ...ENV, TG_TOKEN: 'T', TG_CHAT: '42' };
  const calls = mockFetch((u) => { if (u.includes('api.telegram.org')) return ok(); throw new TypeError('network'); });
  const res = await worker.fetch(req(LEAD), env, ctx);
  const body = await res.json();
  assert.equal(res.status, 200);
  assert.deepEqual([body.ok, body.roistat, body.telegram], [true, false, true]);
  const tg = calls.find((c) => c.url.includes('api.telegram.org'));
  assert.match(JSON.parse(tg.init.body).text, /\+79991234567/);
});
