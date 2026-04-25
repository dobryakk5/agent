#!/usr/bin/env node
/**
 * yax v2.1 — Yandex 360 CLI для OpenClaw
 * Диск, Календарь, Почта (IMAP/SMTP), Telemost
 */

'use strict';

const https = require('https');
const http  = require('http');
const fs    = require('fs');
const path  = require('path');
const os    = require('os');
const { URL } = require('url');

// ─── Конфиг ────────────────────────────────────────────────────────────────

const CONFIG_DIR  = path.join(os.homedir(), '.openclaw', 'yax');
const TOKEN_FILE  = path.join(CONFIG_DIR, 'token.json');
const CONFIG_FILE = path.join(CONFIG_DIR, 'config.json');

function ensureDir() { if (!fs.existsSync(CONFIG_DIR)) fs.mkdirSync(CONFIG_DIR, { recursive: true }); }
function loadToken()  { return fs.existsSync(TOKEN_FILE)  ? JSON.parse(fs.readFileSync(TOKEN_FILE, 'utf8'))  : null; }
function loadConfig() { return fs.existsSync(CONFIG_FILE) ? JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf8')) : {}; }
function saveToken(t) { ensureDir(); fs.writeFileSync(TOKEN_FILE,  JSON.stringify(t, null, 2)); }
function saveConfig(c){ ensureDir(); fs.writeFileSync(CONFIG_FILE, JSON.stringify(c, null, 2)); }

// ─── HTTP ──────────────────────────────────────────────────────────────────

function rawRequest(opts, body) {
  return new Promise((resolve, reject) => {
    const mod = (opts.protocol || 'https:') === 'http:' ? http : https;
    const req = mod.request(opts, res => {
      const chunks = [];
      res.on('data', c => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks).toString() }));
    });
    req.on('error', reject);
    if (body) req.write(typeof body === 'string' ? body : JSON.stringify(body));
    req.end();
  });
}

function apiGET(url, token, extra = {}) {
  const u = new URL(url);
  return rawRequest({ hostname: u.hostname, path: u.pathname + u.search, method: 'GET',
    headers: { Authorization: `OAuth ${token}`, Accept: 'application/json', ...extra } });
}

function apiMethod(method, url, token, body, extra = {}) {
  const u = new URL(url);
  const h = { Authorization: `OAuth ${token}`, Accept: 'application/json', ...extra };
  if (body && typeof body === 'object') h['Content-Type'] = 'application/json';
  return rawRequest({ hostname: u.hostname, path: u.pathname + u.search, method, headers: h }, body);
}

// ─── Зависимости ───────────────────────────────────────────────────────────

function requireDep(name) {
  try { return require(name); }
  catch {
    console.error(`\n❌ Пакет не найден: ${name}`);
    console.error(`   Запустите: npm install   (в папке скилла)\n`);
    process.exit(1);
  }
}

// ─── OAuth ─────────────────────────────────────────────────────────────────

async function getYandexLogin(token) {
  const res = await apiGET('https://login.yandex.ru/info?format=json', token);
  const info = JSON.parse(res.body);
  if (!info.login) throw new Error('Ошибка получения логина: ' + res.body);
  return info.login;
}

async function ensureToken() {
  const tok = loadToken();
  if (!tok || !tok.access_token) {
    console.error('❌ Нет токена. Авторизуйтесь ВРУЧНУЮ (не через агента):');
    console.error(`   node ${__filename} auth --client-id ВАШ_CLIENT_ID`);
    process.exit(1);
  }
  // Обновляем за 5 мин до истечения
  if (tok.expires_at && Date.now() > tok.expires_at - 300_000 && tok.refresh_token) {
    try {
      const cfg = loadConfig();
      const body = new URLSearchParams({
        grant_type: 'refresh_token', refresh_token: tok.refresh_token, client_id: cfg.client_id || '',
      }).toString();
      const res = await rawRequest({
        hostname: 'oauth.yandex.ru', path: '/token', method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      }, body);
      const fresh = JSON.parse(res.body);
      if (fresh.access_token) {
        fresh.expires_at = Date.now() + (fresh.expires_in || 31_536_000) * 1000;
        saveToken({ ...tok, ...fresh });
        return fresh.access_token;
      }
    } catch { /* используем старый */ }
  }
  return tok.access_token;
}

async function cmdAuth(args) {
  const clientId    = argValue(args, '--client-id');
  const redirectUri = argValue(args, '--redirect-uri') || 'https://oauth.yandex.ru/verification_code';
  if (!clientId) { console.error('yax auth --client-id <ID> [--redirect-uri <URI>]'); process.exit(1); }

  const scopes = [
    'cloud_api:disk.app_folder', 'cloud_api:disk.info', 'cloud_api:disk.read', 'cloud_api:disk.write',
    'calendar:all', 'mail:imap_full', 'mail:smtp', 'telemost-api:conferences.create',
  ].join(' ');

  const authUrl = `https://oauth.yandex.ru/authorize?response_type=code`
    + `&client_id=${encodeURIComponent(clientId)}`
    + `&redirect_uri=${encodeURIComponent(redirectUri)}`
    + `&scope=${encodeURIComponent(scopes)}`;

  console.log('\n📋 Откройте в браузере:\n');
  console.log(authUrl);
  console.log('\nПосле авторизации введите код:');

  const code = await readLine();
  if (!code.trim()) { console.error('Код не введён'); process.exit(1); }

  const body = new URLSearchParams({
    grant_type: 'authorization_code', code: code.trim(),
    client_id: clientId, redirect_uri: redirectUri,
  }).toString();

  const res = await rawRequest({
    hostname: 'oauth.yandex.ru', path: '/token', method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  }, body);

  const tok = JSON.parse(res.body);
  if (!tok.access_token) { console.error('❌ Ошибка:', tok); process.exit(1); }
  tok.expires_at = Date.now() + (tok.expires_in || 31_536_000) * 1000;
  saveToken(tok);
  saveConfig({ client_id: clientId, redirect_uri: redirectUri });

  const login = await getYandexLogin(tok.access_token);
  console.log(`\n✅ Авторизован: ${login}`);
  console.log(`💾 Токен: ${TOKEN_FILE}`);
}

// ─── Диск ──────────────────────────────────────────────────────────────────

async function cmdDisk(sub, args) {
  const token = await ensureToken();
  const base  = 'https://cloud-api.yandex.net/v1/disk';

  switch (sub) {
    case 'info': {
      const d = JSON.parse((await apiGET(base, token)).body);
      const gb = n => (n / 1e9).toFixed(2);
      console.log(`📦 Диск: ${gb(d.total_space - d.used_space)} ГБ свободно из ${gb(d.total_space)} ГБ`);
      break;
    }
    case 'list': {
      const p   = args[0] || '/';
      const res = await apiGET(`${base}/resources?path=${encodeURIComponent(p)}&limit=100&sort=name`, token);
      const d   = JSON.parse(res.body);
      if (d.error) { console.error('❌', d.message); process.exit(1); }
      const items = d._embedded?.items || [];
      console.log(`📂 ${p} (${items.length} объектов)`);
      for (const item of items) {
        const sz = item.size ? ` (${(item.size / 1024).toFixed(1)} КБ)` : '';
        console.log(`  ${item.type === 'dir' ? '📁' : '📄'} ${item.name}${sz}`);
      }
      break;
    }
    case 'upload': {
      const [local, remote] = args;
      if (!local || !remote) { console.error('yax disk upload <local> <remote>'); process.exit(1); }
      if (!fs.existsSync(local)) { console.error('❌ Файл не найден:', local); process.exit(1); }
      const { href } = JSON.parse((await apiGET(`${base}/resources/upload?path=${encodeURIComponent(remote)}&overwrite=true`, token)).body);
      const fileData = fs.readFileSync(local);
      const u = new URL(href);
      await new Promise((res, rej) => {
        const req = https.request({ hostname: u.hostname, path: u.pathname + u.search, method: 'PUT',
          headers: { 'Content-Length': fileData.length } }, res);
        req.on('error', rej); req.write(fileData); req.end();
      });
      console.log(`✅ Загружен: ${local} → ${remote}`);
      break;
    }
    case 'download': {
      const [remote, local] = args;
      if (!remote || !local) { console.error('yax disk download <remote> <local>'); process.exit(1); }
      const { href } = JSON.parse((await apiGET(`${base}/resources/download?path=${encodeURIComponent(remote)}`, token)).body);
      const u = new URL(href);
      await new Promise((res, rej) => {
        const file = fs.createWriteStream(local);
        https.get({ hostname: u.hostname, path: u.pathname + u.search }, r => {
          r.pipe(file); file.on('finish', () => { file.close(); res(); });
        }).on('error', rej);
      });
      console.log(`✅ Скачан: ${remote} → ${local}`);
      break;
    }
    case 'mkdir': {
      const [p] = args;
      if (!p) { console.error('yax disk mkdir <path>'); process.exit(1); }
      const res = await apiMethod('PUT', `${base}/resources?path=${encodeURIComponent(p)}`, token);
      console.log(res.status === 201 ? `✅ Папка: ${p}` : JSON.parse(res.body).message);
      break;
    }
    case 'delete': {
      const [p] = args;
      if (!p) { console.error('yax disk delete <path>'); process.exit(1); }
      const res = await apiMethod('DELETE', `${base}/resources?path=${encodeURIComponent(p)}&permanently=false`, token);
      if (res.status === 204 || res.status === 202) console.log(`🗑️  Удалён: ${p}`);
      else console.error('❌', res.body);
      break;
    }
    case 'search': {
      const [q] = args;
      if (!q) { console.error('yax disk search <query>'); process.exit(1); }
      const res = await apiGET(`${base}/resources/files?limit=100`, token);
      const hits = (JSON.parse(res.body).items || []).filter(i => i.name.toLowerCase().includes(q.toLowerCase()));
      if (!hits.length) { console.log('Ничего не найдено'); break; }
      console.log(`🔍 "${q}" — ${hits.length}:`);
      hits.forEach(i => console.log(`  ${i.type === 'dir' ? '📁' : '📄'} ${i.path}`));
      break;
    }
    case 'share': {
      const [p] = args;
      if (!p) { console.error('yax disk share <path>'); process.exit(1); }
      const res = await apiMethod('PUT', `${base}/resources/publish?path=${encodeURIComponent(p)}`, token);
      if (res.status === 200) {
        const info = JSON.parse((await apiGET(`${base}/resources?path=${encodeURIComponent(p)}&fields=public_url`, token)).body);
        console.log(`🔗 ${info.public_url}`);
      } else console.error('❌', res.body);
      break;
    }
    default:
      console.log('disk: info | list [path] | upload <local> <remote> | download <remote> <local>\n     mkdir <path> | delete <path> | search <q> | share <path>');
  }
}

// ─── CalDAV ─────────────────────────────────────────────────────────────────

function caldav(method, url, token, body, extra = {}) {
  const u = new URL(url);
  return rawRequest({
    hostname: u.hostname, path: u.pathname + u.search, method,
    headers: { Authorization: `OAuth ${token}`, 'Content-Type': 'application/xml; charset=utf-8', ...extra },
  }, body);
}

// Автообнаружение URL первого найденного календаря через PROPFIND
async function discoverCalendarUrl(token, login) {
  const base = `https://caldav.yandex.ru/calendars/${login}/`;
  const xml  = `<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><d:resourcetype/><d:displayname/></d:prop>
</d:propfind>`;
  try {
    const res   = await caldav('PROPFIND', base, token, xml, { Depth: '1' });
    const hrefs = [...res.body.matchAll(/<[^:>]*:?href>([^<]+)<\/[^:>]*:?href>/g)].map(m => m[1].trim());
    // Берём первый href, который длиннее base (= подкаталог)
    const found = hrefs.find(h => h !== base && h.endsWith('/') && h.length > base.length);
    if (found) return found.startsWith('http') ? found : 'https://caldav.yandex.ru' + found;
  } catch { /* fallback */ }
  return base + 'events/';
}

async function cmdCalendar(sub, args) {
  const token = await ensureToken();
  const login = await getYandexLogin(token);

  switch (sub) {
    case 'list': {
      const base = `https://caldav.yandex.ru/calendars/${login}/`;
      const xml  = `<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:"><d:prop><d:displayname/></d:prop></d:propfind>`;
      const res   = await caldav('PROPFIND', base, token, xml, { Depth: '1' });
      const names = [...res.body.matchAll(/<d:displayname>([^<]*)<\/d:displayname>/g)].map(m => m[1]).filter(Boolean);
      const hrefs = [...res.body.matchAll(/<d:href>([^<]+)<\/d:href>/g)].map(m => m[1]);
      console.log('📅 Календари:');
      hrefs.slice(1).forEach((h, i) => console.log(`  • ${names[i] || '?'}  →  ${h}`));
      break;
    }
    case 'events': {
      const days   = parseInt(args[0]) || 7;
      const calUrl = await discoverCalendarUrl(token, login);
      const now    = new Date();
      const end    = new Date(now.getTime() + days * 86_400_000);
      const fmt    = d => d.toISOString().replace(/[-:.]/g, '').slice(0, 15) + 'Z';

      const xml = `<?xml version="1.0" encoding="utf-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><d:getetag/><c:calendar-data/></d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT">
        <c:time-range start="${fmt(now)}" end="${fmt(end)}"/>
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>`;

      const res    = await caldav('REPORT', calUrl, token, xml, { Depth: '1' });
      const events = parseICalEvents(res.body);
      if (!events.length) { console.log('Событий нет'); break; }
      console.log(`📅 События на ${days} дн. (${events.length}):`);
      for (const e of events.sort((a, b) => a.start - b.start)) {
        const ds = e.start.toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' });
        console.log(`  🗓️  ${ds} — ${e.summary}`);
        if (e.description) console.log(`       ${e.description}`);
        if (e.uid) console.log(`       uid: ${e.uid}`);
      }
      break;
    }
    case 'create': {
      const [title, date, startTime, endTime, desc, tz] = args;
      if (!title || !date || !startTime || !endTime) {
        console.error('yax calendar create <title> <YYYY-MM-DD> <HH:MM:SS> <HH:MM:SS> [desc] [tz]');
        process.exit(1);
      }
      const timezone = tz || 'Europe/Moscow';
      const uid      = `yax-${Date.now()}-${Math.random().toString(36).slice(2, 6)}@yandex`;
      const dtstart  = date.replace(/-/g, '') + 'T' + startTime.replace(/:/g, '');
      const dtend    = date.replace(/-/g, '') + 'T' + endTime.replace(/:/g, '');
      const dtstamp  = new Date().toISOString().replace(/[-:.]/g, '').slice(0, 15) + 'Z';

      // Полный VTIMEZONE для Moscow (с 2014 не меняется, UTC+3 постоянно)
      const tzBlock = timezone === 'Europe/Moscow'
        ? 'BEGIN:VTIMEZONE\r\nTZID:Europe/Moscow\r\nBEGIN:STANDARD\r\nDTSTART:20140101T000000\r\nTZOFFSETFROM:+0300\r\nTZOFFSETTO:+0300\r\nTZNAME:MSK\r\nEND:STANDARD\r\nEND:VTIMEZONE'
        : `BEGIN:VTIMEZONE\r\nTZID:${timezone}\r\nEND:VTIMEZONE`;

      const lines = [
        'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//yax//OpenClaw//RU', 'CALSCALE:GREGORIAN',
        tzBlock,
        'BEGIN:VEVENT',
        `UID:${uid}`, `DTSTAMP:${dtstamp}`,
        `DTSTART;TZID=${timezone}:${dtstart}`,
        `DTEND;TZID=${timezone}:${dtend}`,
        `SUMMARY:${title}`,
        ...(desc ? [`DESCRIPTION:${desc.replace(/\n/g, '\\n')}`] : []),
        'END:VEVENT', 'END:VCALENDAR',
      ];
      const ical = lines.join('\r\n');

      const calUrl = await discoverCalendarUrl(token, login);
      const putUrl = calUrl.replace(/\/$/, '') + `/${uid}.ics`;
      const res    = await caldav('PUT', putUrl, token, ical, { 'Content-Type': 'text/calendar; charset=utf-8' });

      if (res.status === 201 || res.status === 204) {
        console.log(`✅ Создано: "${title}" ${date} ${startTime}–${endTime}`);
        console.log(`   uid: ${uid}`);
      } else {
        console.error(`❌ ${res.status}:`, res.body.slice(0, 300));
      }
      break;
    }
    case 'delete': {
      const [uid] = args;
      if (!uid) { console.error('yax calendar delete <uid>'); process.exit(1); }
      const calUrl = await discoverCalendarUrl(token, login);
      const res    = await caldav('DELETE', calUrl.replace(/\/$/, '') + `/${uid}.ics`, token);
      if (res.status === 204 || res.status === 200) console.log(`🗑️  Удалено: ${uid}`);
      else console.error(`❌ ${res.status}`, res.body.slice(0, 200));
      break;
    }
    default:
      console.log('calendar: list | events [days] | create <title> <date> <start> <end> [desc] [tz] | delete <uid>');
  }
}

// ИСПРАВЛЕНО: правильный парсинг timestamp (с учётом T-разделителя и различных форматов)
function parseICalEvents(raw) {
  const events = [];
  const text   = raw.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
  const blocks = [...text.matchAll(/BEGIN:VEVENT([\s\S]*?)END:VEVENT/g)].map(m => m[1]);

  for (const block of blocks) {
    // Разворачиваем folded lines (RFC 5545)
    const unfolded = block.replace(/\r?\n[ \t]/g, '');

    const get = key => {
      const m = unfolded.match(new RegExp(`^${key}(?:;[^:]*)?:(.+)$`, 'm'));
      return m ? m[1].trim() : null;
    };

    const summary     = get('SUMMARY');
    const uid         = get('UID');
    const description = get('DESCRIPTION')?.replace(/\\n/g, '\n').replace(/\\,/g, ',');
    const dts         = get('DTSTART');
    if (!summary || !dts) continue;

    // Убираем T и Z, оставляем только цифры для единообразия
    const digits = dts.replace(/[TZ]/g, '');
    let start;
    if (digits.length >= 12) {
      // YYYYMMDDHHmmss
      start = new Date(
        `${digits.slice(0,4)}-${digits.slice(4,6)}-${digits.slice(6,8)}T${digits.slice(8,10)}:${digits.slice(10,12)}:00`
      );
    } else {
      // YYYYMMDD — событие на весь день
      start = new Date(`${digits.slice(0,4)}-${digits.slice(4,6)}-${digits.slice(6,8)}`);
    }
    if (isNaN(start.getTime())) continue;

    events.push({ summary, uid, description, start });
  }
  return events;
}

// ─── Почта ─────────────────────────────────────────────────────────────────

async function cmdMail(sub, args) {
  const token = await ensureToken();
  const login = await getYandexLogin(token);
  const email = login.includes('@') ? login : login + '@yandex.ru';

  switch (sub) {
    case 'inbox': {
      const limit = parseInt(args[0]) || 10;
      // ИСПРАВЛЕНО: используем imapflow вместо imap — нативная поддержка OAuth2 accessToken
      const { ImapFlow } = requireDep('imapflow');
      const client = new ImapFlow({
        host: 'imap.yandex.ru', port: 993, secure: true,
        auth: { user: email, accessToken: token },
        logger: false,
      });

      await client.connect();
      const lock = await client.getMailboxLock('INBOX');
      try {
        const count = client.mailbox.exists;
        const from  = Math.max(1, count - limit + 1);
        const msgs  = [];
        for await (const msg of client.fetch(`${from}:${count}`, { envelope: true, uid: true })) {
          msgs.push({
            seq:     msg.seq,
            uid:     msg.uid,
            subject: msg.envelope.subject || '(без темы)',
            from:    msg.envelope.from?.[0]?.address || '?',
            date:    msg.envelope.date?.toLocaleDateString('ru-RU') || '?',
          });
        }
        console.log(`📬 Входящие (${msgs.length}):`);
        for (const m of msgs.reverse()) {
          console.log(`  #${m.seq} | ${m.date} | От: ${m.from}`);
          console.log(`  Тема: ${m.subject}\n`);
        }
      } finally { lock.release(); await client.logout(); }
      break;
    }

    case 'read': {
      const [seqno] = args;
      if (!seqno) { console.error('yax mail read <seqno>'); process.exit(1); }
      const { ImapFlow } = requireDep('imapflow');
      const client = new ImapFlow({
        host: 'imap.yandex.ru', port: 993, secure: true,
        auth: { user: email, accessToken: token },
        logger: false,
      });
      await client.connect();
      const lock = await client.getMailboxLock('INBOX');
      try {
        const msg = await client.fetchOne(seqno, { envelope: true, bodyText: true });
        console.log(`📧 ${msg.envelope.subject}`);
        console.log(`От: ${msg.envelope.from?.[0]?.address}`);
        console.log(`Дата: ${msg.envelope.date}`);
        console.log(`\n${msg.bodyText || '(нет текста)'}`);
      } finally { lock.release(); await client.logout(); }
      break;
    }

    case 'send': {
      const [to, subject, ...bodyParts] = args;
      const body = bodyParts.join(' ');
      if (!to || !subject || !body) { console.error('yax mail send <to> <subject> <body>'); process.exit(1); }

      const nodemailer = requireDep('nodemailer');
      const transporter = nodemailer.createTransport({
        host: 'smtp.yandex.ru', port: 465, secure: true,
        auth: { type: 'OAuth2', user: email, accessToken: token },
      });

      const info = await transporter.sendMail({ from: email, to, subject, text: body });
      console.log(`✅ Отправлено → ${to} | Тема: ${subject}`);
      console.log(`   MessageId: ${info.messageId}`);
      break;
    }

    default:
      console.log('mail: inbox [limit] | read <seqno> | send <to> <subject> <body>');
      console.log('\n⚠️  Почта требует порты 993/465. На Railway они заблокированы.');
  }
}

// ─── Telemost ───────────────────────────────────────────────────────────────

async function cmdTelemost(sub, args) {
  const token = await ensureToken();

  switch (sub) {
    case 'create': {
      const topic = args.length ? args.join(' ') : 'Встреча';
      // Пробуем оба известных endpoint
      for (const url of [
        'https://api.telemost.yandex.net/v2/conferences',
        'https://cloud-api.yandex.net/v1/telemost/conferences',
      ]) {
        const res = await apiMethod('POST', url, token, { name: topic }, { 'Content-Type': 'application/json' });
        if (res.status === 200 || res.status === 201) {
          const c = JSON.parse(res.body);
          console.log(`📹 "${topic}"`);
          console.log(`   Ссылка: ${c.url || c.join_url || c.conference_url || c.id || JSON.stringify(c)}`);
          if (c.password) console.log(`   Пароль: ${c.password}`);
          return;
        }
        if (process.env.YAX_DEBUG) console.error(`  [debug] ${url} → ${res.status}: ${res.body.slice(0, 100)}`);
      }
      console.error('❌ Telemost недоступен. Проверьте scope telemost-api:conferences.create.');
      break;
    }
    default:
      console.log('telemost: create [topic]');
  }
}

// ─── Утилиты ───────────────────────────────────────────────────────────────

function argValue(arr, flag) {
  const i = arr.indexOf(flag);
  return i !== -1 ? arr[i + 1] : null;
}

function readLine() {
  return new Promise(resolve => {
    let buf = '';
    process.stdin.setEncoding('utf8');
    process.stdin.resume();
    process.stdin.on('data', c => { buf += c; if (buf.includes('\n')) { process.stdin.pause(); resolve(buf.split('\n')[0].trim()); } });
    process.stdin.on('end', () => resolve(buf.trim()));
  });
}

// ─── main ──────────────────────────────────────────────────────────────────

async function main() {
  const [cmd, sub, ...rest] = process.argv.slice(2);

  if (!cmd || cmd === 'help' || cmd === '--help' || cmd === '-h') {
    console.log(`
yax v2.1 — Yandex 360 CLI для OpenClaw

  АВТОРИЗАЦИЯ (один раз, вручную — не через агента):
  yax auth --client-id <ID>

  ДИСК:
  yax disk info | list [path] | upload <local> <remote> | download <remote> <local>
  yax disk mkdir <path> | delete <path> | search <query> | share <path>

  КАЛЕНДАРЬ:
  yax calendar list | events [days]
  yax calendar create <title> <YYYY-MM-DD> <HH:MM:SS> <HH:MM:SS> [desc] [tz]
  yax calendar delete <uid>

  ПОЧТА (только локально, облако блокирует порты):
  yax mail inbox [limit] | read <seqno> | send <to> <subject> <body>

  TELEMOST:
  yax telemost create [topic]

  Отладка: YAX_DEBUG=1 yax ...
`);
    return;
  }

  try {
    if      (cmd === 'auth')     await cmdAuth([sub, ...rest].filter(Boolean));
    else if (cmd === 'disk')     await cmdDisk(sub, rest);
    else if (cmd === 'calendar') await cmdCalendar(sub, rest);
    else if (cmd === 'mail')     await cmdMail(sub, rest);
    else if (cmd === 'telemost') await cmdTelemost(sub, rest);
    else { console.error(`❌ Неизвестная команда: ${cmd}. Запустите: yax help`); process.exit(1); }
  } catch (err) {
    console.error('❌', err.message || String(err));
    if (process.env.YAX_DEBUG) console.error(err.stack);
    process.exit(1);
  }
}

main();
