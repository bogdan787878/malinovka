/*
 * Ретранслятор заявок malinovka-park2.ru -> Roistat (Cloudflare Worker).
 *
 * Зачем: браузер посетителя шлёт заявку в cloud.roistat.com напрямую, а этот домен режут блокировщики рекламы,
 * защита браузера и антивирусы — у таких посетителей заявка теряется. Здесь заявка уходит на нейтральный адрес,
 * а в Roistat её отправляет уже сервер: с повторами, без блокировщиков, с ключом вебхука в секретах (не в коде сайта).
 *
 * Настройки (Worker -> Settings -> Variables and Secrets):
 *   ROISTAT_URL  (Secret)  полный адрес вебхука Roistat вместе с ?key=...
 *   TG_TOKEN     (Secret)  необязательно: токен Telegram-бота для копии заявки
 *   TG_CHAT      (Text)    необязательно: id чата, куда слать копию
 * Если TG_* заданы, заявка не теряется даже когда Roistat недоступен: копия приходит в Telegram.
 *
 * Формат запроса от сайта (js/lead-form.js): POST, тело — JSON (Content-Type text/plain, без preflight):
 *   { name, phone, title, comment, formid, roistat_visit, roistat_url, cookies }
 */

const ALLOWED_ORIGINS = new Set([
  'https://malinovka-park2.ru',
  'https://www.malinovka-park2.ru',
  'https://bogdan787878.github.io',
]);
const ROISTAT_ATTEMPTS = 3;
const ROISTAT_TIMEOUT_MS = 6000;

export default {
  async fetch(request, env, ctx) {
    const origin = request.headers.get('Origin') || '';
    const cors = {
      'Access-Control-Allow-Origin': ALLOWED_ORIGINS.has(origin) ? origin : 'https://malinovka-park2.ru',
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
      'Access-Control-Max-Age': '86400',
      Vary: 'Origin',
    };

    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
    if (request.method !== 'POST') return reply(405, { ok: false, error: 'method' }, cors);
    if (!ALLOWED_ORIGINS.has(origin)) return reply(403, { ok: false, error: 'origin' }, cors);

    let raw;
    try {
      raw = JSON.parse(await request.text());
    } catch (e) {
      return reply(400, { ok: false, error: 'json' }, cors);
    }
    if (raw && raw.website) return reply(200, { ok: true }, cors); // honeypot: бот

    const lead = clean(raw);
    if (!lead) return reply(422, { ok: false, error: 'invalid' }, cors);

    const tgConfigured = Boolean(env.TG_TOKEN && env.TG_CHAT);
    const [roistat, telegram] = await Promise.all([
      toRoistat(lead, env),
      tgConfigured ? toTelegram(lead, env) : Promise.resolve(false),
    ]);
    // заявка не потеряна, если дошла хоть куда-то
    const ok = roistat || telegram;
    return reply(ok ? 200 : 502, { ok, roistat, telegram: tgConfigured ? telegram : null }, cors);
  },
};

function reply(status, body, headers) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json; charset=UTF-8', ...headers },
  });
}

function str(value, max) {
  return typeof value === 'string' ? value.trim().slice(0, max) : '';
}

function clean(d) {
  if (!d || typeof d !== 'object') return null;
  const digits = str(d.phone, 40).replace(/\D/g, '');
  if (digits.length < 10 || digits.length > 15) return null;
  return {
    name: str(d.name, 100),
    phone: '+' + digits,
    title: str(d.title, 200) || 'Заявка с сайта',
    comment: str(d.comment, 500),
    formid: str(d.formid, 100),
    roistat_visit: str(d.roistat_visit, 100),
    roistat_url: str(d.roistat_url, 1000),
    cookies: str(d.cookies, 4000),
  };
}

// те же поля, что раньше слала Tilda и шлёт сайт напрямую
function roistatBody(l) {
  const p = new URLSearchParams();
  p.append('name', l.name);
  p.append('phone', l.phone);
  p.append('title', l.title);
  p.append('comment', l.comment);
  p.append('formid', l.formid);
  p.append('roistat_visit', l.roistat_visit);
  p.append('roistat_fields_roistat', l.roistat_visit);
  p.append('roistat_url', l.roistat_url);
  p.append('COOKIES', l.cookies);
  return p;
}

async function fetchWithTimeout(url, init, ms) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), ms);
  try {
    return await fetch(url, { ...init, signal: ctl.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function toRoistat(lead, env) {
  if (!env.ROISTAT_URL) return false;
  for (let i = 0; i < ROISTAT_ATTEMPTS; i++) {
    try {
      const res = await fetchWithTimeout(env.ROISTAT_URL, { method: 'POST', body: roistatBody(lead) }, ROISTAT_TIMEOUT_MS);
      if (res.ok) return true;
    } catch (e) {
      /* сеть/таймаут — пробуем ещё раз */
    }
    if (i < ROISTAT_ATTEMPTS - 1) await new Promise((r) => setTimeout(r, 400 * (i + 1)));
  }
  return false;
}

async function toTelegram(lead, env) {
  const text = [
    'Новая заявка с сайта Малиновка Парк 2',
    'Имя: ' + (lead.name || '—'),
    'Телефон: ' + lead.phone,
    lead.comment,
  ].join('\n');
  try {
    const res = await fetchWithTimeout(
      'https://api.telegram.org/bot' + env.TG_TOKEN + '/sendMessage',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: env.TG_CHAT, text }),
      },
      ROISTAT_TIMEOUT_MS
    );
    return res.ok;
  } catch (e) {
    return false;
  }
}
