'use strict';
/* PII Vault: application front end. Every value shown is returned by the real layers through layer8_demo/server.py. */
const $ = (s, e = document) => e.querySelector(s);
const $$ = (s, e = document) => [...e.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const sleep = ms => new Promise(r => setTimeout(r, ms));
const enc = encodeURIComponent;
async function api(path, body, cs) {
  const h = {}; const c = cs || S.case; if (c) h['x-case'] = c;
  const r = await fetch(path, body === undefined ? { headers: h } : { method: 'POST', headers: { ...h, 'content-type': 'application/json' }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || r.statusText);
  return j;
}
const col = l => `var(--${String(l || '').replace(/[^A-Z_]/g, '') || 'DIGITS'},#475467)`;
const pad = (n, k = 2) => String(n).padStart(k, '0');
const fmtT = ts => { const d = new Date(ts * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`; };
const fmtD = ts => new Date(ts * 1000).toISOString().replace('T', ' ').replace('Z', ' UTC');
const sh = (h, n = 12) => h ? String(h).slice(0, n) + '…' : '';
const toast = (m, ms = 2400) => { const t = $('#toast'); t.textContent = m; t.classList.add('on'); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove('on'), ms); };
const copy = async t => { try { await navigator.clipboard.writeText(t); toast('Copied'); } catch { toast('Copy failed'); } };
window.copyText = copy;

/* ---- references: every id is a link to the screen that owns it */
const L = (screen, param) => `#/${screen}${param === undefined ? '' : '/' + enc(param)}`;
const rTok = t => `<a class="ref" href="${L('vault', t)}">${esc(t)}</a>`;
const rSeq = n => `<a class="ref" href="${L('audit', n)}">#${n}</a>`;
const rReq = id => id ? `<a class="ref" href="${L('audit', 'req:' + id)}">${esc(id)}</a>` : '<span class="t3">none</span>';
const rRec = id => `<a class="ref" href="${L('map', id)}">${esc(id)}</a>`;
const rEr = id => `<a class="ref" href="${L('privacy', id)}">${esc(id)}</a>`;
const mono = s => `<span class="mono">${esc(s)}</span>`;
const cp = s => `<span class="mono">${esc(s)}</span> <button class="btn sm ghost" onclick="copyText('${esc(s).replace(/'/g, '')}')" title="Copy">Copy</button>`;
const pill = (cls, text) => `<span class="pill ${cls}">${esc(text)}</span>`;
const yes = (b, a = 'yes', n = 'no') => b ? pill('ok', a) : pill('bad', n);

function hl(text, spans) {
  const sp = [...(spans || [])].sort((a, b) => a.start - b.start); let out = '', pos = 0;
  for (const s of sp) { if (s.start < pos) continue; out += esc(text.slice(pos, s.start)) + `<span class="sp" style="--c:${col(s.label || s.type)}">${esc(text.slice(s.start, s.end))}<sup>${esc(s.label || s.type)}</sup></span>`; pos = s.end; }
  return out + esc(text.slice(pos));
}
function tokHtml(t, leak) {
  let h = esc(t).replace(/&lt;([A-Z_]+?)_(\d+)&gt;/g, (m, a, b) => `<a class="tok" style="--c:${col(a)};text-decoration:none" href="${L('vault', '<' + a + '_' + b + '>')}">${a}_${b}</a>`);
  (leak || []).forEach(x => { const e = esc(x.text); h = h.split(e).join(`<mark style="background:#fecdca;color:#7a271a;border-radius:3px;padding:0 2px">${e}</mark>`); });
  return h;
}
const eng = s => `<span class="eng">${(s || []).map(x => `<u class="${esc(x)}">${esc(x)}</u>`).join('')}</span>`;
const kv = rows => `<div class="kv">${rows.map(([k, v]) => `<div>${esc(k)}</div><div>${v ?? '<span class="t3">none</span>'}</div>`).join('')}</div>`;
const tbl = (heads, rows, cls = '') => `<table class="tbl ${cls}"><thead><tr>${heads.map(h => `<th${h.startsWith('>') ? ' class="r"' : ''}>${esc(h.replace(/^>/, ''))}</th>`).join('')}</tr></thead><tbody>${rows.join('') || `<tr><td colspan="${heads.length}" class="t3" style="text-align:center;padding:22px">No rows</td></tr>`}</tbody></table>`;

const ICON = {
  data: 'M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zm0 0v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6',
  detection: 'M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2M7 12h10',
  vault: 'M5 11h14v10H5zM8 11V7a4 4 0 0 1 8 0v4M12 15v2',
  gateway: 'M21 11.5a8.4 8.4 0 0 1-9 8.4 8.5 8.5 0 0 1-3.9-.9L3 21l1.9-5.1A8.4 8.4 0 1 1 21 11.5z',
  consent: 'M9 11l3 3 8-8M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h9',
  map: 'M6 3a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM18 15a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM6 9v3a3 3 0 0 0 3 3h6',
  erasure: 'M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14M10 11v5M14 11v5',
  audit: 'M9 3h6l4 4v14H5V3h4M14 3v5h5M9 13h6M9 17h6',
  e2e: 'M5 3l14 9-14 9z',
  layers: 'M12 3l9 5-9 5-9-5zM3 13l9 5 9-5M3 17.5l9 5 9-5',
};
Object.assign(ICON, {
  conversations: 'M21 11.5a8.4 8.4 0 0 1-9 8.4 8.5 8.5 0 0 1-3.9-.9L3 21l1.9-5.1A8.4 8.4 0 1 1 21 11.5z',
  borrowers: 'M17 21v-2a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v2M10 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM21 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8',
  privacy: 'M12 3l8 3v6c0 4.5-3.2 7.8-8 9-4.8-1.2-8-4.5-8-9V6zM9 12l2 2 4-4',
  architecture: 'M4 4h6v5H4zM14 4h6v5h-6zM9 15h6v5H9zM7 9v3h10V9M12 12v3',
});
const SCREENS = [
  { id: 'conversations', name: 'Conversations', grp: 'Workspace' }, { id: 'borrowers', name: 'Borrowers', grp: 'Workspace' },
  { id: 'privacy', name: 'Privacy requests', grp: 'Workspace' }, { id: 'audit', name: 'Audit trail', grp: 'Workspace', layer: 'L7', mod: 'layer7_audit' },
  { id: 'consent', name: 'Consent policy', grp: 'Governance', layer: 'L4', mod: 'layer4_consent' }, { id: 'map', name: 'Data inventory', grp: 'Governance', layer: 'L5', mod: 'layer5_lineage' },
  { id: 'architecture', name: 'Architecture', grp: 'Governance' },
  { id: 'detection', name: 'Detection lab', grp: 'Engineering', layer: 'L1', mod: 'layer1_detection' }, { id: 'vault', name: 'Vault inspector', grp: 'Engineering', layer: 'L2', mod: 'layer2_vault' },
  { id: 'data', name: 'Test data', grp: 'Engineering', layer: 'L0', mod: 'layer0_data' }, { id: 'e2e', name: 'Scenario', grp: 'Engineering', layer: 'L8', mod: 'layer8_demo' },
  { id: 'layers', name: 'Test suites', grp: 'Engineering', mod: '' }];
const ALIAS = { gateway: 'conversations', erasure: 'privacy' };

const S = { content: null, status: null, screen: 'conversations', param: '', tk: 0, cases: [], case: null, hist: {}, selMsg: {}, view: 'agent', panel: 'borrower', convFilter: '', reqs: [], erFull: {}, anim: '',
  gw: { purpose: 'service', speaker: 'Customer', detector: 'normal' }, busy: false, tests: {}, audit: null, lin: null, vaultAtt: [], af: { q: '', kinds: [], case: '' }, auTab: 'feed',
  mapFilter: 'all', mapTab: 'records', cm: { store: 'eval_sets', purpose: 'service' }, e2e: null, dataLang: '', dataPage: 0, dataSel: null, detText: '', det: null, bTab: 'overview' };
const SCR = {};
const layerOf = id => S.content.layers.find(l => l.id === id);

/* ---- scenarios (all values fake) */
const SCN = [
  ['Written: name and phone', 'Customer', 'Hi, I am Ramesh Kumar. My mobile number is 9845012345.'],
  ['PAN and Aadhaar', 'Customer', 'My PAN card is ABCPK1234L and my Aadhaar is 4521 8890 1267.'],
  ['Spoken digits across 2 turns', null, [['Customer', 'Sure, my number is nine eight four five'], ['Customer', 'zero one two three four five']]],
  ['Hindi (Devanagari)', null, [['Customer', 'हाँ जी, मेरा नंबर है नौ आठ चार पाँच'], ['Customer', 'शून्य एक दो तीन चार पाँच। मेरा नाम रमेश कुमार है।']]],
  ['Hindi (Roman)', null, [['Customer', 'Haan ji, mera number hai nau aath chaar paanch'], ['Customer', 'shunya ek do teen chaar paanch. Main Ramesh Kumar bol raha hoon.']]],
  ['Kannada', null, [['Customer', 'ನನ್ನ ನಂಬರ್ ಒಂಬತ್ತು ಎಂಟು ನಾಲ್ಕು ಐದು'], ['Customer', 'ಸೊನ್ನೆ ಒಂದು ಎರಡು ಮೂರು ನಾಲ್ಕು ಐದು. ನನ್ನ ಹೆಸರು ಲಕ್ಷ್ಮಿ ದೇವಿ.']]],
  ['Tamil', null, [['Customer', 'என் நம்பர் ஒன்பது எட்டு நான்கு ஐந்து'], ['Customer', 'பூஜ்யம் ஒன்று இரண்டு மூன்று நான்கு ஐந்து. என் பெயர் பிரியா நாயர்.']]],
  ['Marathi', 'Customer', 'माझा नंबर नऊ आठ चार पाच शून्य एक दोन तीन चार पाच आहे. माझे नाव सुरेश पाटील आहे.'],
  ['Amount and date (must stay)', 'Agent', 'Your EMI of ₹4,200 is due on 12 March.'],
  ['Fault: detector off, phone + email', 'Customer', 'You can call me on 9123456780 or mail rakesh.s@example.com', { detector: 'blind' }],
  ['Fault: detector off, a name', 'Customer', 'This is Priya Nair speaking, please call 9123456780', { detector: 'blind' }],
  ['Fault: purpose not permitted', 'Customer', 'My mobile number is 9123456780', { purpose: 'model_improvement' }]];
const CALL = [['Agent', 'Hello sir, am I speaking with Ramesh Kumar?'], ['Customer', 'Yes, speaking.'], ['Agent', 'Please confirm your registered mobile number.'], ['Customer', 'nine eight four five zero'], ['Agent', 'Yes, go on.'],
  ['Customer', 'one two three four five'], ['Agent', 'Your PAN card number please.'], ['Customer', 'ABCPK1234L. This is Kumar here.'], ['Agent', 'Your EMI of ₹4,200 is due on the 5th. Thank you.'], ['Customer', 'Okay, I will pay by the 7th.']];

/* ================================================================ shell */
async function boot() {
  [S.content, S.status, S.cases] = await Promise.all([api('/api/content'), api('/api/status'), api('/api/cases')]);
  const pick = [...S.cases].filter(c => c.state === 'ACTIVE' && c.messages).sort((a, b) => (b.last_ts || 0) - (a.last_ts || 0))[0] || S.cases[0]; S.case = pick && pick.sid; S.reqs = (await api('/api/erasure')).requests;
  renderEnv(); renderNav(); window.addEventListener('hashchange', route); initSearch();
  document.addEventListener('keydown', e => { if (e.key === '/' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) { e.preventDefault(); $('#q').focus(); } if (e.key === 'Escape') { closeDrawer(); closeModal(); } });
  $('#scrim').onclick = () => { closeDrawer(); closeModal(); };
  route();
}
function renderEnv() {
  const s = S.status;
  $('#env').innerHTML = `<div class="envrow">${pill('warn', 'Mock stores')}${pill('', esc(s.vault) + ' vault')}</div>
    <label class="llmrow" title="Live sends only TOKENISED text to ${esc(s.llm_model)}"><span>${s.llm_live ? 'Live LLM · Nova Pro' : 'LLM · local stub'}</span><button class="sw ${s.llm_live ? 'on' : ''}" id="llmsw" aria-label="Toggle live LLM"></button></label>
    <div class="me"><span class="av">AG</span><div><b>Collections agent</b><span>Role: agent</span></div></div>`;
  $('#llmsw').onclick = async () => { await api('/api/llm', { live: !S.status.llm_live }); S.status = await api('/api/status'); renderEnv(); toast(S.status.llm_live ? 'Live LLM on: tokenised text only' : 'Live LLM off: local stub'); };
}
async function newCase(name) {
  const c = await api('/api/session/new', { name }); S.cases = await api('/api/cases'); S.case = c.sid; S.lin = null; S.audit = null; S.hist[c.sid] = []; S.status = await api('/api/status', undefined, c.sid); renderNav(); return c;
}
function renderNav() {
  const badge = s => s.id === 'conversations' ? `<span class="cnt">${S.cases.length}</span>` : s.id === 'privacy' && S.reqs.length ? `<span class="cnt">${S.reqs.length}</span>` : s.layer ? `<span class="ly">${s.layer}</span>` : '';
  const item = s => `<button class="ni ${S.screen === s.id ? 'on' : ''}" data-s="${s.id}"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="${ICON[s.id]}"/></svg>${esc(s.name)}${badge(s)}</button>`;
  $('#navitems').innerHTML = ['Workspace', 'Governance', 'Engineering'].map(g => `<h6>${g}</h6>${SCREENS.filter(s => s.grp === g).map(item).join('')}`).join('');
  $$('[data-s]', $('#nav')).forEach(b => b.onclick = () => { location.hash = L(b.dataset.s); });
}
function route() {
  let [, sid = 'conversations', ...rest] = location.hash.slice(1).split('/'); sid = ALIAS[sid] || sid;
  S.screen = (SCREENS.find(s => s.id === sid) || SCREENS[0]).id; S.param = decodeURIComponent(rest.join('/'));
  closeDrawer(); closeModal(); renderNav(); SCR[S.screen]();
}
const main = () => $('#main');
function sbar(id, extra = '') {
  const s = SCREENS.find(x => x.id === id);
  return `<div class="sbar"><h1>${esc(s.name)}</h1>${s.layer ? `<span class="lyb">${s.layer}</span>` : ''}<span class="mono t3">${esc(s.mod || '')}</span><span style="margin-left:auto;display:flex;gap:8px">${extra}${s.layer ? `<button class="btn" data-info="${s.layer}">Layer details</button>` : ''}</span></div>`;
}
function bindCommon(root = main()) { $$('[data-info]', root).forEach(b => b.onclick = () => openInfo(b.dataset.info)); }
const loading = id => { main().innerHTML = sbar(id) + '<div class="content"><div class="empty"><span class="spin"></span></div></div>'; };

/* ---- drawer / modal */
function openDrawer(title, sub, html) {
  $('#drawer').innerHTML = `<div class="dh"><h2>${title}</h2>${sub || ''}<button class="x" aria-label="Close">×</button></div><div class="db">${html}</div>`;
  $('#drawer .x').onclick = closeDrawer; $('#drawer').classList.add('on'); $('#scrim').classList.add('on');
}
function closeDrawer() { $('#drawer').classList.remove('on'); if (!$('#modal').classList.contains('on')) $('#scrim').classList.remove('on'); }
function openModal(title, body, foot) { $('#modal').innerHTML = `<div class="mh">${title}</div><div class="mb">${body}</div><div class="mf">${foot}</div>`; $('#modal').classList.add('on'); $('#scrim').classList.add('on'); }
function closeModal() { $('#modal').classList.remove('on'); if (!$('#drawer').classList.contains('on')) $('#scrim').classList.remove('on'); }

function openInfo(id) {
  const l = layerOf(id), t = S.tests[id];
  const res = t && !t.running ? tbl(['Suite', '>Passed', '>Failed', '>Time'], t.results.map(r => `<tr><td class="mono">${esc(r.module)}</td><td class="r ${r.ok ? 'ok' : 'bad'}">${r.passed}${r.skipped ? ` (+${r.skipped} skipped)` : ''}</td><td class="r">${r.failed}</td><td class="r t3">${r.secs}s</td></tr>`)) : '';
  openDrawer(`${esc(l.name)}`, `<span class="lyb">${id}</span>`, `
    <h4>Purpose</h4><p style="margin:0">${esc(l.job)}</p><h4>Guarantee</h4><p style="margin:0">${esc(l.promise)}</p>
    <h4>Evidence</h4>${tbl(['Metric', 'Value', 'Source'], l.numbers.map(n => `<tr><td>${esc(n.label)}<div class="t3" style="font-size:11.5px">${esc(n.note)}</div></td><td class="mono nw">${esc(n.value)}</td><td>${pill(n.kind === 'measured' ? 'ok' : '', n.kind)}</td></tr>`))}
    ${S.content.suites[id] ? `<h4>Tests</h4><button class="btn" id="dtest" ${t && t.running ? 'disabled' : ''}>${t && t.running ? 'Running…' : 'Run tests'}</button><div style="margin-top:10px">${res}</div>` : ''}
    <h4>Known limits</h4><ul style="margin:0;padding-left:18px">${l.limits.map(x => `<li style="margin:4px 0">${esc(x)}</li>`).join('')}</ul>
    <h4>Source</h4>${l.files.map(f => `<div class="mono">${esc(f)}</div>`).join('')}`);
  const b = $('#dtest'); if (b) b.onclick = async () => { await runTests(id); openInfo(id); };
}
async function runTests(id) {
  S.tests[id] = { running: true };
  try { S.tests[id] = await api('/api/tests/' + id, {}); toast(`${id}: ${S.tests[id].passed} passed, ${S.tests[id].failed} failed`); } catch (e) { S.tests[id] = { ok: false, passed: 0, failed: 1, results: [{ module: id, passed: 0, failed: 1, ok: false, secs: 0 }] }; toast('Test run failed: ' + e.message, 5000); }
}

/* ---- global search over references */
function initSearch() {
  const q = $('#q'), box = $('#sres'); let idx = [], loadedAt = 0, cur = -1;
  const build = async () => {
    if (Date.now() - loadedAt < 4000) return; loadedAt = Date.now(); idx = [];
    const [a, l, v] = await Promise.all([api('/api/audit/full'), api('/api/lineage'), api('/api/vault')]).catch(() => [null, null, null]);
    if (a) { const seen = new Set(); a.records.forEach(r => { idx.push({ t: 'audit', k: `#${r.seq} ${r.kind_name}`, s: r.hash.slice(0, 16), h: L('audit', r.seq), x: `${r.seq} ${r.kind_name} ${r.hash} ${r.request_id || ''}` }); if (r.request_id && !seen.has(r.request_id)) { seen.add(r.request_id); idx.push({ t: 'request', k: r.request_id, s: 'all events of this request', h: L('audit', 'req:' + r.request_id), x: r.request_id }); } }); }
    if (l) { l.graph.nodes.forEach(n => idx.push({ t: 'record', k: n.id, s: n.purpose || '', h: L('map', n.id), x: n.id + ' ' + n.tokens.join(' ') })); l.stores.forEach(s => s.records.filter(r => !r.in_registry).forEach(r => idx.push({ t: 'orphan', k: r.id, s: s.store, h: L('map', s.store + ':' + r.id), x: r.id + ' ' + s.store }))); }
    if (v) v.entries.forEach(e => idx.push({ t: 'token', k: e.token, s: e.label + ' · ' + e.purpose, h: L('vault', e.token), x: e.token + ' ' + e.label + ' ' + e.key_id }));
    S.er && idx.push({ t: 'erasure', k: S.er.request_id, s: S.er.status, h: L('privacy', S.er.request_id), x: S.er.request_id });
  };
  const show = () => {
    const v = q.value.trim().toLowerCase(); if (!v) { box.hidden = true; return; }
    const m = idx.filter(i => i.x.toLowerCase().includes(v)).slice(0, 14); cur = -1;
    box.innerHTML = m.length ? m.map(i => `<a href="${i.h}"><span class="pill">${i.t}</span><span class="mono">${esc(i.k)}</span><span class="t3">${esc(i.s)}</span></a>`).join('') : '<div class="none">No matching reference</div>'; box.hidden = false;
  };
  q.addEventListener('focus', async () => { await build(); show(); }); q.addEventListener('input', async () => { await build(); show(); });
  q.addEventListener('keydown', e => { const items = $$('a', box); if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); cur = (cur + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length; items.forEach((x, i) => x.classList.toggle('on', i === cur)); } if (e.key === 'Enter' && items.length) { location.hash = (items[Math.max(cur, 0)]).getAttribute('href'); q.blur(); box.hidden = true; q.value = ''; } if (e.key === 'Escape') { box.hidden = true; q.blur(); } });
  document.addEventListener('click', e => { if (!$('#search').contains(e.target)) box.hidden = true; else if (e.target.closest('a')) { box.hidden = true; q.value = ''; } });
}

/* ================================================================ Conversations: the working screen (L1 to L5 behind every message) */
const STEP = { initialize: ['L4', 'Consent check and input validation'], detect: ['L1', 'Detection'], tokenise: ['L2', 'Tokenise and encrypt (one batch)'], final_audit: ['L3', 'Independent audit'], llm_dispatch: ['L3', 'LLM dispatch (tokens only)'], sanitise: ['L3', 'Sanitise reply'], rehydrate_route: ['L3', 'Rehydrate for borrower, tokenise for log'], block: ['L3', 'Block (fail closed)'] };
const PURPOSES = ['service', 'collections', 'compliance', 'dispute', 'analytics', 'model_improvement'];
const hue = h => parseInt((h || '0000').slice(0, 4), 16) % 360;
const avatar = (c, big) => `<span class="avc ${big ? 'big' : ''}" style="--h:${hue(c.hash)}">${esc(String(c.case).slice(-2))}</span>`;
const curCase = () => S.cases.find(c => c.sid === S.case);
const hist = () => S.hist[S.case] || [];
const caseOfHash = h => S.cases.find(c => c.hash === String(h || '').slice(0, 16));
const tokSpan = t => esc(t).replace(/&lt;([A-Z_]+?)_(\d+)&gt;/g, (m, a, b) => `<span class="tok" style="--c:${col(a)}">${a}_${b}</span>`);
const selIdx = () => { const h = hist(); const v = S.selMsg[S.case]; if (v != null && h[v]) return v; for (let i = h.length - 1; i >= 0; i--) if (h[i].status !== 'erased') return i; return -1; };

SCR.conversations = async function () {
  const tk = ++S.tk; S.cases = await api('/api/cases');
  if (S.param && S.cases.some(c => c.sid === S.param) && S.param !== S.case) { S.case = S.param; S.lin = null; S.audit = null; }
  else if (!S.param) { const cur = S.cases.find(c => c.sid === S.case); if (!cur || cur.state === 'ERASED') { const a = [...S.cases].filter(c => c.state === 'ACTIVE' && c.messages).sort((x, y) => (y.last_ts || 0) - (x.last_ts || 0))[0]; if (a) { S.case = a.sid; S.lin = null; S.audit = null; } } }
  main().innerHTML = '<div class="cv"><section class="clist" id="clist"></section><section class="cthread" id="cthread"></section><aside class="cside" id="cside"></aside></div>';
  const [d, lin, au, cs, er] = await Promise.all([api('/api/case/' + S.case), api('/api/lineage'), api('/api/audit/full'), api('/api/cases'), api('/api/erasure')]);
  if (tk !== S.tk) return;
  S.hist[S.case] = d.history; S.lin = lin; S.audit = au; S.cases = cs; S.reqs = er.requests; renderNav(); drawConv(true);
};
function drawConv(scroll) { drawList(); drawThread(scroll); drawSide(); }

function caseItem(c, href, on) {
  return `<a class="ci ${on ? 'on' : ''}" href="${href}">${avatar(c)}<div class="cbody"><div class="ct"><b>Case ${c.case}</b><span class="t3">${c.last_ts ? fmtT(c.last_ts).slice(0, 5) : ''}</span></div>
    <div class="cp">${c.state === 'ERASED' ? '<i>Personal data erased</i>' : c.preview ? tokSpan(c.preview) : '<span class="t3">No messages yet</span>'}</div>
    <div class="cm">${c.state === 'ERASED' ? pill('', 'Erased') : pill('ok', 'Active')}<span class="mono t3">${c.messages} msg</span><span class="mono t3">${esc(c.hash.slice(0, 8))}</span></div></div></a>`;
}
function drawList() {
  const q = S.convFilter.toLowerCase();
  const items = [...S.cases].sort((a, b) => (b.last_ts || 0) - (a.last_ts || 0)).filter(c => !q || String(c.case).includes(q) || c.hash.includes(q) || (c.preview || '').toLowerCase().includes(q));
  $('#clist').innerHTML = `<div class="lh"><b>Conversations</b><span class="t3">${S.cases.length}</span><button class="btn sm pri" id="newc">New</button></div><div class="lq"><input class="in" id="cfq" placeholder="Filter by case, hash or text" value="${esc(S.convFilter)}"></div>
    <div class="litems">${items.map(c => caseItem(c, L('conversations', c.sid), c.sid === S.case)).join('') || '<div class="empty">No matching conversation</div>'}</div>`;
  $('#cfq').oninput = e => { S.convFilter = e.target.value; const p = e.target.selectionStart; drawList(); const i = $('#cfq'); i.focus(); i.setSelectionRange(p, p); };
  $('#newc').onclick = () => openModal('New conversation', `<label class="f">Borrower name<input class="in" id="nm" placeholder="Used only to derive a keyed hash"></label><div class="t2">The name is never stored; the case is identified by a keyed hash.</div>`, '<button class="btn" id="nc">Cancel</button><button class="btn pri" id="ng">Create</button>')
    || ($('#nc').onclick = closeModal, $('#nm').focus(), $('#ng').onclick = async () => { const c = await newCase($('#nm').value.trim() || 'Borrower'); closeModal(); location.hash = L('conversations', c.sid); });
}

function trail(r, i) {
  const l3 = r.l3, ok = r.status !== 'blocked', chips = [];
  if (!ok) { const cd = String(r.reason).startsWith('consent'); chips.push([cd ? 'Denied by consent policy' : 'Blocked: ' + r.reason, cd ? 'L4' : 'L3', 'bad']); }
  else {
    chips.push(['Detected ' + r.l1.spans.length, 'L1', 'ok'], ['Encrypted', 'L2', 'ok'], [l3.retries ? 'Re-masked by audit' : 'Audited', 'L3', l3.retries ? 'warn' : 'ok'], [l3.llm_live ? 'Sent to Nova Pro' : 'Sent to LLM', 'L3', 'ok'],
      ['Stored ' + r.l4.writes.filter(w => w.allowed).length + '/' + r.l4.writes.length, 'L4', r.l4.writes.every(w => w.allowed) ? 'ok' : 'bad']);
    if (l3.escaped && l3.escaped.length) chips.push(['Unmasked: ' + l3.escaped.map(e => e.text).join(', ') + ' reached the LLM', 'L3', 'bad']);
  }
  return `<div class="trail">${chips.map(([t, ly, c]) => `<button class="st ${c}" data-pr="${i}">${c === 'ok' ? '✓' : c === 'bad' ? '✕' : '↻'} ${esc(t)}<i>${ly}</i></button>`).join('')}</div>`;
}
function msgHtml(r, i) {
  const cust = r.speaker !== 'Agent', sel = i === selIdx(), hd = `<div class="who"><b>${esc(r.speaker)}</b><span>${fmtT(r.ts)}</span><span class="mono">${esc(r.message_id)}</span></div>`;
  if (r.status === 'erased') return `<div class="msg ${cust ? '' : 'agent'}"><span class="av">${cust ? 'C' : 'A'}</span><div class="bub gone">${hd}<div class="t3">Content erased</div></div></div>`;
  const ok = r.status !== 'blocked', l3 = r.l3, agent = S.view === 'agent';
  const body = agent ? (ok ? tokHtml(l3.llm_saw, l3.escaped) : `<span class="t3">Message withheld: ${esc(r.reason)}</span>`) : (ok ? hl(r.text, r.l1.spans) : `<span class="t3">${esc(r.text)}</span>`);
  const fi = r.l1.blind ? `<div class="fi ${l3.escaped && l3.escaped.length ? 'bad' : 'ok'}">Fault test: detector off. ${l3.escaped && l3.escaped.length ? 'The audit cannot detect names.' : 'The audit re-scan masked ' + l3.net_found + ' value(s) on retry ' + l3.retries + '.'}</div>` : '';
  const m = `<div class="msg ${cust ? '' : 'agent'} ${sel ? 'sel' : ''}" data-i="${i}"><span class="av">${cust ? 'C' : 'A'}</span><div class="bub">${hd}<div class="txt">${body}</div>${fi}${trail(r, i)}</div></div>`;
  const bot = ok ? `<div class="msg agent bot ${sel ? 'sel' : ''}" data-i="${i}"><span class="av">AI</span><div class="bub"><div class="who"><b>${l3.llm_live ? 'Nova Pro' : 'Assistant (stub)'}</b><span>${agent ? 'as logged' : 'as the borrower sees it'}</span></div><div class="txt" style="line-height:1.6">${agent ? tokHtml(l3.log_entry || '') : esc(l3.reply_borrower)}</div></div></div>` : '';
  return m + bot;
}
function drawThread(scroll) {
  const c = curCase(), h = hist(), erased = c.state === 'ERASED', req = S.reqs.find(r => r.sid === c.sid);
  const typed = ($('#ctext') || {}).value || '';
  $('#cthread').innerHTML = `<div class="th"><div class="thl">${avatar(c, 1)}<div><b>Case ${c.case}</b><div class="mono t3">${esc(c.hash)}</div></div>${erased ? pill('', 'Erased') : pill('ok', 'Active')}</div>
      <div class="seg" id="segview" title="Protected view is what agents, logs and the LLM see. Original text is shown for the demo only."><button data-v="agent" class="${S.view === 'agent' ? 'on' : ''}">Protected view</button><button data-v="orig" class="${S.view === 'orig' ? 'on' : ''}">Original text</button></div></div>
    <div class="msgs" id="msgs">${erased ? `<div class="sysmsg ok"><b>Personal data erased</b><span>${req ? `Request ${rEr(req.request_id)} · ${esc(req.status)} · audit ${rSeq(req.audit_seq)}` : 'This borrower was erased.'}</span></div>` : ''}${erased ? '' : h.map(msgHtml).join('') || '<div class="empty"><b>No messages yet</b><span>Send a message below or run a simulation.</span></div>'}</div>
    ${erased ? '' : `<div class="composer2"><div class="simbar"><span class="t3">Simulate</span><select id="scn" class="in sm"><option value="">Scenario…</option>${SCN.map((s, i) => `<option value="${i}">${esc(s[0])}</option>`).join('')}</select>
      <button class="chip ${S.gw.detector === 'blind' ? 'on' : ''}" id="fault" title="Fault injection: Layer 1 returns nothing, so the audit net has to catch it">Detector off (test)</button>
      <label class="t3">Purpose <select id="purp" class="in sm">${PURPOSES.map(p => `<option ${S.gw.purpose === p ? 'selected' : ''}>${p}</option>`).join('')}</select></label></div>
      <form class="composer" id="cf"><div class="seg" id="segsp">${['Customer', 'Agent'].map(x => `<button type="button" data-v="${x}" class="${S.gw.speaker === x ? 'on' : ''}">${x}</button>`).join('')}</div><input class="in" id="ctext" autocomplete="off" placeholder="Message as ${S.gw.speaker.toLowerCase()} (use fake details)" value="${esc(typed)}" aria-label="Message"><button class="btn pri">Send</button></form></div>`}`;
  $$('#segview button').forEach(b => b.onclick = () => { S.view = b.dataset.v; drawThread(); });
  $$('#segsp button').forEach(b => b.onclick = () => { S.gw.speaker = b.dataset.v; drawThread(); });
  $$('.msg[data-i]').forEach(e => e.onclick = ev => { S.selMsg[S.case] = Number(e.dataset.i); if (ev.target.closest('[data-pr]')) S.panel = 'protection'; drawThread(); drawSide(); });
  $$('[data-pr]').forEach(b => b.onclick = ev => { ev.stopPropagation(); S.selMsg[S.case] = Number(b.dataset.pr); S.panel = 'protection'; drawThread(); drawSide(); });
  if (!erased) {
    $('#fault').onclick = () => { S.gw.detector = S.gw.detector === 'blind' ? 'normal' : 'blind'; drawThread(); };
    $('#purp').onchange = e => S.gw.purpose = e.target.value;
    $('#scn').onchange = async e => { const v = e.target.value; e.target.value = ''; if (v !== '') await playScn(Number(v)); };
    $('#cf').onsubmit = async e => { e.preventDefault(); const t = $('#ctext').value.trim(); if (!t) return; $('#ctext').value = ''; await send(t); };
    $('#ctext').focus();
  }
  const m = $('#msgs'); if (scroll) m.scrollTop = m.scrollHeight;
}

function decisionsFor(c) {
  const by = {}; (S.audit ? S.audit.records : []).filter(r => (r.borrower_hash || '').startsWith(c.hash)).forEach(r => {
    if (!['write_allowed', 'write_rejected', 'CONSENT_DENIED'].includes(r.kind_name)) return; const p = r.purpose || 'service'; by[p] = by[p] || { ok: 0, no: 0 }; r.kind_name === 'write_allowed' ? by[p].ok++ : by[p].no++;
  });
  return by;
}
function borrowerPanel(c) {
  const stores = S.lin ? S.lin.stores : [], dec = decisionsFor(c), req = S.reqs.find(r => r.sid === c.sid), held = stores.reduce((a, s) => a + s.count, 0), unreg = stores.reduce((a, s) => a + s.unregistered_records, 0);
  return `<div class="bp-head">${avatar(c, 1)}<div><b>Case ${c.case}</b><div class="mono t3">${esc(c.hash)}</div></div></div>
    <h5>Protection</h5>${kv([['Borrower key', c.key_present ? pill('ok', 'active') : pill('', 'destroyed')], ['Encrypted values', c.values], ['Messages', c.messages]])}
    <h5>Data held</h5>${stores.length ? tbl(['Store', '>Records', '>Untracked'], stores.map(s => `<tr><td class="mono">${esc(s.store.replace('unmapped:', ''))}</td><td class="r mono">${s.count}</td><td class="r mono ${s.unregistered_records ? 'bad' : ''}">${s.unregistered_records}</td></tr>`)) : '<div class="t3">Nothing stored.</div>'}
    <div class="t2" style="margin-top:6px">${held} records${unreg ? ` · <b class="bad">${unreg} not in the registry</b>` : ''} · <a href="${L('borrowers', c.sid)}">Open profile</a></div>
    <h5>Consent by purpose</h5>${Object.keys(dec).length ? tbl(['Purpose', '>Allowed', '>Refused'], Object.entries(dec).map(([p, v]) => `<tr><td>${esc(p)}</td><td class="r mono ok">${v.ok}</td><td class="r mono ${v.no ? 'bad' : ''}">${v.no}</td></tr>`)) : '<div class="t3">No decisions yet.</div>'}
    <h5>Privacy request</h5>${req ? `<div>${rEr(req.request_id)} ${pill('ok', req.status)}</div>` : c.state === 'ERASED' ? '<div class="t3">Erased.</div>' : `<a class="btn" href="${L('privacy', 'new:' + c.sid)}">Start erasure request</a>`}`;
}
function protectionPanel() {
  const i = selIdx(), r = hist()[i]; if (!r || r.status === 'erased') return '<div class="empty"><b>No message selected</b><span>Select a message to see how each layer handled it.</span></div>';
  const l3 = r.l3, ok = r.status !== 'blocked', pre = l3.pre_llm_ms, seen = {};
  const step = (n, ly, title, state, body, open) => `<details class="step" ${open ? 'open' : ''}><summary><span class="sn ${state[0]}">${n}</span><b>${title}</b><i>${ly}</i><span class="sv ${state[0]}">${esc(state[1])}</span></summary><div class="sb">${body}</div></details>`;
  const a = l3.audit, toks = new Set(r.l2.tokens_this_message), ents = r.l2.entries.filter(e => toks.has(e.token));
  const parts = [
    step(1, 'L4', 'Consent check', [r.l4.gateway_decision === 'denied' ? 'bad' : 'ok', r.l4.gateway_decision === 'denied' ? 'denied' : 'allowed'], kv([['Purpose', mono(r.purpose) + (r.canonical_purpose && r.canonical_purpose !== r.purpose ? ' → ' + mono(r.canonical_purpose) : '')], ['Consent ID', r.consent_id ? mono(r.consent_id) : null], ['Reason', r.reason ? mono(r.reason) : null]]), !ok)];
  if (ok || r.l1.spans.length) parts.push(
    step(2, 'L1', 'Detection', ['ok', `${r.l1.spans.length} found · ${r.l1.ms} ms`], r.l1.spans.length ? tbl(['Type', 'Text', 'Engines', 'Token'], r.l1.spans.map(s => `<tr><td><span class="tag" style="--c:${col(s.label)}">${esc(s.label)}</span></td><td class="mono">${esc(s.text)}</td><td>${eng(s.sources)}</td><td>${s.token ? rTok(s.token) : ''}</td></tr>`)) + (r.l1.blind ? '<div style="margin-top:6px">' + pill('warn', 'detector off (fault test)') + '</div>' : '') : '<span class="t3">Nothing detected.</span>', true),
    step(3, 'L2', 'Encryption', ['ok', `${ents.length} value(s)`], ents.length ? tbl(['Token', 'Key', 'Ciphertext'], ents.map(e => `<tr><td>${rTok(e.token)}</td><td class="mono t3">${esc(e.key_id)}</td><td class="mono" title="${esc(e.ciphertext)}">${esc(e.ciphertext.slice(0, 20))}…</td></tr>`)) : '<span class="t3">No values to protect.</span>'),
    step(4, 'L3', 'Independent audit', [l3.retries ? 'warn' : 'ok', l3.retries ? `retry ${l3.retries}` : 'clean'], a ? tbl(['Check', '>Found'], [['Digit tripwire', a.digit_tripwire], ['Spoken-number tripwire', a.spoken_tripwire], ['Survival of known values', a.survival], ['Second pattern family', a.family2], ['Layer 1 rules re-scan', a.rules_rescan]].map(([n, v]) => `<tr><td>${n}</td><td class="r mono ${v ? 'bad' : 'ok'}">${v}</td></tr>`).concat(`<tr><td>Placeholder share</td><td class="r mono">${(a.utility_share * 100).toFixed(0)}%</td></tr>`)) + (l3.net_found ? `<div class="t2" style="margin-top:6px">The re-scan masked ${l3.net_found} value(s) the detector missed.</div>` : '') : '<span class="t3">Did not run.</span>'),
    step(5, 'L3', 'LLM dispatch', [l3.escaped && l3.escaped.length ? 'bad' : 'ok', l3.llm_live ? 'Nova Pro' : 'stub'], `<div class="masked">${tokHtml(l3.llm_saw, l3.escaped)}</div><div class="t3" style="margin-top:6px">Entry → LLM ${pre} ms of 50 ms budget</div><div class="budget"><i class="${pre > 50 ? 'o' : ''}" style="width:${Math.min(100, pre * 2)}%"></i></div>`),
    step(6, 'L4·L5', 'Storage and lineage', [r.l4.writes.every(w => w.allowed) ? 'ok' : 'bad', `${r.l4.writes.filter(w => w.allowed).length}/${r.l4.writes.length} written`], tbl(['Store', 'Record', 'Decision'], r.l4.writes.map(w => `<tr><td class="mono">${esc(w.store)}</td><td>${rRec((w.store === 'logs' ? 'log:' : 'chunk:') + w.id)}</td><td>${yes(w.allowed, 'written', 'rejected')}</td></tr>`)) + `<div class="t2" style="margin-top:6px">Registered: ${r.l5.added.map(rRec).join(' ')}</div>`));
  parts.push(step(parts.length + 1, 'L7', 'Audit trail', ['ok', `${r.l7.events.length} records`], tbl(['Seq', 'Event', '>ms'], r.l7.events.map(e => `<tr><td>${rSeq(e.seq)}</td><td class="mono">${esc(e.kind_name)}</td><td class="r mono">${e.latency_ms ?? e.ms ?? ''}</td></tr>`))));
  return `<div class="pr-head"><div class="t3">Message ${esc(r.message_id)} · ${fmtT(r.ts)}</div><div>${rReq(r.request_id)} <button class="btn sm ghost" onclick="copyText('${esc(r.request_id)}')">Copy</button></div></div><div class="steps">${parts.join('')}</div>`;
}
function drawSide() {
  const c = curCase();
  $('#cside').innerHTML = `<div class="tabs"><button data-p="borrower" class="${S.panel === 'borrower' ? 'on' : ''}">Borrower</button><button data-p="protection" class="${S.panel === 'protection' ? 'on' : ''}">Protection</button></div><div class="body">${S.panel === 'borrower' ? borrowerPanel(c) : protectionPanel()}</div>`;
  $$('#cside [data-p]').forEach(b => b.onclick = () => { S.panel = b.dataset.p; drawSide(); });
}
async function refreshConv() {
  const [lin, au, cs] = await Promise.all([api('/api/lineage'), api('/api/audit/full'), api('/api/cases')]); S.lin = lin; S.audit = au; S.cases = cs; renderNav();
  if (S.screen === 'conversations') drawConv(true);
}
async function send(text, o = {}) {
  if (S.busy && !o.force) return null; S.busy = true;
  try {
    const r = await api('/api/message', { text, speaker: o.speaker || S.gw.speaker, purpose: o.purpose || S.gw.purpose, detector: o.detector || S.gw.detector });
    (S.hist[S.case] = S.hist[S.case] || []).push(r); S.selMsg[S.case] = S.hist[S.case].length - 1; S.audit = null; S.lin = null;
    if (S.screen === 'conversations' && !o.quiet) await refreshConv();
    return r;
  } catch (e) { toast(e.message, 5000); return null; } finally { S.busy = false; }
}
async function playScn(i) {
  const [, spk, val, opt = {}] = SCN[i]; const turns = Array.isArray(val) ? val : [[spk, val]];
  for (const [who, text] of turns) { await send(text, { speaker: who, force: true, ...opt }); await sleep(150); }
}

/* ================================================================ L1 Detection */
SCR.detection = function () {
  const rep = S.content.l1_report, d = S.det;
  main().innerHTML = sbar('detection') + `<div class="content"><div class="panel"><div class="ph"><b>Analyse text</b><span class="t3">Layer 1 only: nothing is tokenised, stored or sent.</span></div><div class="pb">
    <textarea class="in" id="dtext" rows="3" style="width:100%" placeholder="Text to analyse">${esc(S.detText)}</textarea>
    <div style="display:flex;gap:8px;margin-top:8px;align-items:center"><button class="btn pri" id="dgo">Analyse</button><select id="dex" class="in"><option value="">Example…</option>${SCN.filter(s => !s[0].startsWith('Fault') && typeof s[2] === 'string').map((s, i) => `<option>${esc(s[2])}</option>`).join('')}</select><span class="t3" id="dms"></span></div></div></div>
    <div id="dout"></div>
    ${rep ? `<div class="panel"><div class="ph"><b>Scoreboard</b>${pill('', rep.split + ' split · ' + rep.rows.toLocaleString() + ' rows')}${pill('warn', 'synthetic data')}<div class="r"><span class="t3">fully masked ≥95% of span: ${rep.full}% · masked ≥50%: ${rep.masked50}% · type label right: ${rep.label}%${rep.false_alarm ? ` · PII-free false alarms ${rep.false_alarm.n}/${rep.false_alarm.of} (${rep.false_alarm.pct}%)` : ''}</span></div></div>
      <div class="grid2"><div>${tbl(['PII type', '>Spans', 'Fully masked', '>Label'], rep.types.map(t => `<tr><td>${esc(t.name)}</td><td class="r mono">${t.spans}</td><td><span class="bar"><i class="${t.full < 95 ? 'lo' : ''}" style="width:${t.full}%"></i></span><span class="mono">${t.full}%</span></td><td class="r mono">${t.label}%</td></tr>`))}</div>
      <div>${tbl(['Language variant', '>Spans', 'Fully masked'], rep.langs.map(t => `<tr><td class="mono">${esc(t.name)}</td><td class="r mono">${t.spans}</td><td><span class="bar"><i class="${t.full < 95 ? 'lo' : ''}" style="width:${t.full}%"></i></span><span class="mono">${t.full}%</span></td></tr>`))}</div></div></div>` : ''}</div>`;
  bindCommon(); drawDet();
  $('#dgo').onclick = async () => { const t = $('#dtext').value; S.detText = t; if (!t.trim()) return; $('#dgo').disabled = true; try { S.det = await api('/api/detect', { text: t }); } catch (e) { toast(e.message, 4000); } $('#dgo').disabled = false; drawDet(); };
  $('#dex').onchange = e => { $('#dtext').value = e.target.value; e.target.value = ''; };
  $('#dtext').onkeydown = e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') $('#dgo').click(); };
};
function drawDet() {
  const d = S.det, o = $('#dout'); if (!o) return; if (!d) { o.innerHTML = ''; return; }
  o.innerHTML = `<div class="panel"><div class="ph"><b>Result</b>${pill('', d.spans.length + ' span(s)')}${pill('', d.ms + ' ms')}${pill('', d.chars + ' chars')}</div><div class="hlbox">${hl(S.detText, d.spans)}</div>${d.spans.length ? tbl(['#', 'Type', 'Text', 'Offsets', 'Engines', 'Confidence', 'Flags'], d.spans.map((s, i) => `<tr><td class="mono t3">${i + 1}</td><td><span class="tag" style="--c:${col(s.label)}">${esc(s.label)}</span></td><td class="mono">${esc(s.text)}</td><td class="mono t3">${s.start}–${s.end}</td><td>${eng(s.sources)}</td><td class="mono">${esc(s.conf)}</td><td>${s.uncertain ? pill('warn', 'uncertain: always masked') : ''}${s.part ? ' ' + pill('', 'part ' + s.part) : ''}</td></tr>`)) : ''}</div>`;
}

/* ================================================================ L2 Vault */
SCR.vault = async function () {
  loading('vault'); const tk = ++S.tk; const v = await api('/api/vault'); S.lin = S.lin || await api('/api/lineage'); if (tk !== S.tk) return;
  const key = v.keys[0]; const tokRefs = t => S.lin.graph.nodes.filter(n => n.tokens.includes(t)).map(n => n.id);
  const callers = [['borrower_reply', 'Borrower reply path'], ['agent_dashboard', 'Agent dashboard'], ['log_pipeline', 'Log pipeline'], ['engineer', 'Engineer with database access']];
  main().innerHTML = sbar('vault') + `<div class="content"><div class="panel"><div class="ph"><b>Borrower key</b></div><div class="pb">${key ? kv([['Borrower (keyed hash)', mono(v.borrower_hash)], ['Key ID', mono(key.key_id)], ['Status', pill(key.status === 'ACTIVE' ? 'ok' : 'bad', key.status)], ['DEK / KEK version', `v${key.dek_version} / v${key.kek_version}`], ['Times used', key.uses], ['Cipher', esc(v.cipher)], ['Key hierarchy', esc(v.kdf)], ['Equality index', esc(v.blind_index)], ['Backend', esc(v.backend)]]) : '<span class="t3">No key yet: nothing has been tokenised. Send a message in Gateway.</span>'}</div></div>
    <div class="panel"><div class="ph"><b>Entries</b>${pill('', v.entries.length + ' encrypted')}${pill('ok', 'no plaintext in the database')}</div><div style="overflow:auto">${tbl(['Token', 'Type', 'Purpose', 'Status', 'Key', 'Nonce', 'Ciphertext', 'Created', 'Referenced by'], v.entries.map(e => `<tr data-row="${esc(e.token)}" id="v-${esc(e.token)}"><td>${rTok(e.token)}</td><td class="mono">${esc(e.label)}</td><td>${esc(e.purpose)}</td><td>${pill(e.status === 'ACTIVE' ? 'ok' : '', e.status)}</td><td class="mono t3">${esc(e.key_id)}</td><td class="mono t3">${esc(e.nonce.slice(0, 12))}</td><td class="mono" title="${esc(e.ciphertext)}">${esc(e.ciphertext.slice(0, 26))}…</td><td class="mono t3 nw">${fmtT(e.created)}</td><td>${tokRefs(e.token).slice(0, 2).map(rRec).join(' ') || '<span class="t3">none</span>'}</td></tr>`))}</div></div>
    <div class="grid21"><div class="panel"><div class="ph"><b>Access check</b><span class="t3">Request a value as a given caller</span></div><div class="toolbar"><select id="vtok" class="in">${v.entries.map(e => `<option ${S.param === e.token ? 'selected' : ''}>${esc(e.token)}</option>`).join('')}</select><select id="vrole" class="in">${callers.map(c => `<option value="${c[0]}">${c[1]}</option>`).join('')}</select><button class="btn pri" id="vgo" ${v.entries.length ? '' : 'disabled'}>Request access</button></div>
      ${tbl(['Time', 'Caller', 'Token', 'Decision', 'Detail'], S.vaultAtt.slice().reverse().map(a => `<tr><td class="mono t3">${fmtT(a.ts)}</td><td class="mono">${esc(a.role)}</td><td>${rTok(a.token)}</td><td>${yes(a.allowed, 'granted', 'denied')}</td><td class="t2">${esc(a.result)}${a.allowed ? ' · ' + a.length + ' chars' : ''}</td></tr>`))}</div>
    <div class="panel"><div class="ph"><b>Vault access log</b>${pill('', v.access_log.length + ' operations')}</div><div style="max-height:300px;overflow:auto">${tbl(['Op', 'Caller', 'Detail'], v.access_log.slice().reverse().map(a => `<tr><td class="mono">${esc(a.op)}</td><td class="mono t3">${esc(a.caller || '')}</td><td class="mono t3">${esc(Object.entries(a).filter(([k]) => !['op', 'caller', 'request_id'].includes(k)).map(([k, x]) => k + '=' + x).join(' '))}</td></tr>`))}</div></div></div></div>`;
  bindCommon();
  $$('[data-row]').forEach(r => r.onclick = e => { if (e.target.closest('a')) return; const en = v.entries.find(x => x.token === r.dataset.row); openDrawer(esc(en.token), `<span class="lyb">L2</span>`, `${kv([['Token', mono(en.token)], ['Type', mono(en.label)], ['Purpose', esc(en.purpose)], ['Status', esc(en.status)], ['Key ID', mono(en.key_id)], ['Nonce (hex)', cp(en.nonce)], ['Created', mono(fmtD(en.created))], ['Referenced by', tokRefs(en.token).map(rRec).join(' ') || null]])}<h4>Ciphertext (AES-256-GCM, hex)</h4><pre class="code">${esc(en.ciphertext)}</pre>`); });
  if (S.param) { const r = $('[id="v-' + S.param.replace(/"/g, '') + '"]'); if (r) { r.classList.add('sel', 'flash'); r.scrollIntoView({ block: 'center' }); } }
  $('#vgo') && ($('#vgo').onclick = async () => { const token = $('#vtok').value, role = $('#vrole').value; const r = await api('/api/vault/attempt', { token, role }); S.vaultAtt.push({ ...r, token, ts: Date.now() / 1000 }); SCR.vault(); });
};

/* ================================================================ L4 Consent */
SCR.consent = async function () {
  loading('consent'); const tk = ++S.tk; const [mx, au] = await Promise.all([api('/api/consent/matrix'), api('/api/audit/full')]); if (tk !== S.tk) return; S.audit = au;
  const cell = (st, p) => { const c = mx.cells.find(x => x.store === st && x.purpose === p); return `<td><span class="mc ${c.rule} ${S.cm.store === st && S.cm.purpose === p ? 'sel' : ''}" data-cell="${st}|${p}">${{ allow: 'ALLOW', explicit_consent: 'CONSENT', deny: 'DENY' }[c.rule]}</span></td>`; };
  const dec = au.records.filter(r => ['write_allowed', 'write_rejected', 'write_failed', 'CONSENT_DENIED'].includes(r.kind_name));
  main().innerHTML = sbar('consent') + `<div class="content"><div class="grid21"><div class="panel"><div class="ph"><b>Policy</b>${pill('', 'default deny')}${pill('warn', 'our reading of purpose limitation, not legal advice')}</div><table class="tbl mx"><thead><tr><th>Store</th>${mx.purposes.map(p => `<th>${p}</th>`).join('')}</tr></thead><tbody>${mx.stores.map(st => `<tr><td class="mono">${st}</td>${mx.purposes.map(p => cell(st, p)).join('')}</tr>`).join('')}</tbody></table></div>
    <div class="panel"><div class="ph"><b>Write request</b><span class="t3">Real guarded_write against the mock store</span></div><div class="pb" style="display:grid;gap:10px">
      <label class="f">Store<select id="cs" class="in">${mx.stores.map(s => `<option ${S.cm.store === s ? 'selected' : ''}>${s}</option>`).join('')}</select></label>
      <label class="f">Purpose<select id="cp" class="in">${mx.purposes.map(s => `<option ${S.cm.purpose === s ? 'selected' : ''}>${s}</option>`).join('')}</select></label>
      <label class="f">Consent ID<input id="cid" class="in" placeholder="CN-…" value="${esc(S.cm.cid || '')}"></label>
      <label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="cx" ${S.cm.explicit ? 'checked' : ''}> Explicit consent for this purpose</label>
      <button class="btn pri" id="try">Submit write</button><div id="tryres"></div></div></div></div>
    <div class="panel"><div class="ph"><b>Decisions</b>${pill('', dec.length + ' recorded')}<span class="t3">From the audit chain, newest first</span></div><div style="overflow:auto">${tbl(['Audit', 'Time', 'Decision', 'Store', 'Record', 'Purpose', 'Consent ID', 'Reason', 'Request'], dec.slice().reverse().map(r => `<tr><td>${rSeq(r.seq)}</td><td class="mono t3 nw">${fmtT(r.ts)}</td><td>${yes(r.kind_name === 'write_allowed', 'allowed', r.kind_name === 'CONSENT_DENIED' ? 'gateway denied' : r.kind_name === 'write_failed' ? 'failed' : 'rejected')}</td><td class="mono">${esc(r.store || 'logs')}</td><td>${r.record_id ? rRec(((r.store === 'logs' ? 'log:' : r.store === 'eval_sets' ? 'eval:' : 'chunk:') + r.record_id)) : '<span class="t3">none</span>'}</td><td>${esc(r.purpose || '')}</td><td class="mono">${esc(r.consent_id || '')}</td><td class="mono t2">${esc(r.reason || '')}</td><td>${r.request_id ? rReq(r.request_id) : ''}</td></tr>`))}</div></div></div>`;
  bindCommon();
  $$('[data-cell]').forEach(c => c.onclick = () => { const [st, p] = c.dataset.cell.split('|'); S.cm = { ...S.cm, store: st, purpose: p }; SCR.consent(); });
  $('#cs').onchange = e => { S.cm.store = e.target.value; SCR.consent(); }; $('#cp').onchange = e => { S.cm.purpose = e.target.value; SCR.consent(); };
  $('#cid').oninput = e => S.cm.cid = e.target.value; $('#cx').onchange = e => S.cm.explicit = e.target.checked;
  $('#try').onclick = async () => {
    try {
      const r = await api('/api/consent/try', { store: S.cm.store, purpose: S.cm.purpose, explicit: !!S.cm.explicit, consent_id: S.cm.cid || null }); S.audit = null; S.lin = null;
      $('#tryres').innerHTML = `<div class="alert ${r.allowed ? 'ok' : 'bad'}"><div><b>${r.allowed ? 'Written' : 'Rejected'}</b> · ${mono(r.reason)}<div>Audit record ${rSeq(r.event.seq)} ${r.registered ? ' · lineage ' + rRec(r.registered) : ''}</div></div></div>`; setTimeout(() => SCR.consent(), 1400);
    } catch (e) { toast(e.message, 4000); }
  };
};

/* ================================================================ L5 Data map */
const STORE_OF = { log: 'logs', chunk: 'vector_store', emb: 'vector_store', eval: 'eval_sets' };
SCR.map = async function () {
  loading('map'); const tk = ++S.tk; const d = S.lin = await api('/api/lineage'); const st = await api('/api/status'); S.status = st; if (tk !== S.tk) return;
  const nodes = d.graph.nodes; const rows = nodes.map(n => ({ ref: n.id, kind: n.kind, store: STORE_OF[n.kind] || n.kind, purpose: n.purpose, consent: n.consent_id, parent: n.parents[0], tokens: n.tokens, reg: true, n }));
  d.stores.forEach(s => s.records.filter(r => !r.in_registry).forEach(r => rows.push({ ref: s.store + ':' + r.id, kind: 'unknown', store: s.store, purpose: null, consent: null, parent: null, tokens: [], reg: false, path: s.path, id: r.id })));
  const un = rows.filter(r => !r.reg).length, list = rows.filter(r => S.mapFilter === 'all' || !r.reg);
  main().innerHTML = sbar('map', `<button class="btn" id="plant" ${st.planted || st.erased ? 'disabled' : ''}>${st.planted ? 'Unregistered copies added' : 'Add unregistered copies (test)'}</button>`) + `<div class="content"><div class="panel"><div class="tabs"><button data-mt="records" class="${S.mapTab === 'records' ? 'on' : ''}">Records<i>${rows.length}</i></button><button data-mt="graph" class="${S.mapTab === 'graph' ? 'on' : ''}">Graph</button></div>
    ${S.mapTab === 'records' && !rows.length ? `<div class="pb"><div class="alert ${st.erased ? 'ok' : ''}">${st.erased ? 'Borrower erased: the registry holds no references and no store holds a record.' : 'No records yet. Send a message in Gateway.'}</div></div>` : ''}${S.mapTab === 'records' ? `<div class="toolbar"><div class="seg" id="mf"><button data-v="all" class="${S.mapFilter === 'all' ? 'on' : ''}">All</button><button data-v="un" class="${S.mapFilter === 'un' ? 'on' : ''}">Not in registry</button></div><span class="t2">${nodes.filter(n => ['log', 'chunk', 'eval'].includes(n.kind)).length} registered records · ${d.stores.reduce((a, s) => a + s.count, 0)} in stores · ${un ? `<b class="bad">${un} not in registry</b>` : '0 not in registry'}</span></div>
      <div style="overflow:auto">${tbl(['Reference', 'Kind', 'Store', 'Purpose', 'Consent ID', 'Parent', 'Tokens', 'Registry'], list.map(r => `<tr data-row="${esc(r.ref)}" id="m-${esc(r.ref)}"><td><span class="ref">${esc(r.ref.length > 44 ? r.ref.slice(0, 44) + '…' : r.ref)}</span></td><td class="mono">${esc(r.kind)}</td><td class="mono">${esc(r.store)}</td><td>${esc(r.purpose || '')}</td><td class="mono">${esc(r.consent || '')}</td><td>${r.parent ? rRec(r.parent) : ''}</td><td>${r.tokens.map(rTok).join(' ')}</td><td>${r.reg ? pill('ok', 'registered') : pill('bad', 'not registered')}</td></tr>`))}</div>` : `<div class="pb" style="overflow:auto">${lineageSvg(d)}</div>`}</div></div>`;
  bindCommon();
  $$('[data-mt]').forEach(b => b.onclick = () => { S.mapTab = b.dataset.mt; SCR.map(); });
  $$('#mf button').forEach(b => b.onclick = () => { S.mapFilter = b.dataset.v; SCR.map(); });
  $('#plant').onclick = async () => { try { await api('/api/plant', {}); S.mapFilter = 'un'; S.audit = null; SCR.map(); toast('Added 3 copies the registry does not know about'); } catch (e) { toast(e.message, 4000); } };
  $$('[data-row]').forEach(r => r.onclick = e => { if (e.target.closest('a')) return; showRec(rows.find(x => x.ref === r.dataset.row)); });
  if (S.param) { const row = rows.find(x => x.ref === S.param); if (row) { const el = $('[id="m-' + S.param.replace(/"/g, '') + '"]'); if (el) { el.classList.add('sel', 'flash'); el.scrollIntoView({ block: 'center' }); } showRec(row); } }
};
function showRec(r) {
  if (!r) return;
  openDrawer(`<span class="mono">${esc(r.ref.length > 40 ? r.ref.slice(0, 40) + '…' : r.ref)}</span>`, r.reg ? pill('ok', 'registered') : pill('bad', 'not registered'),
    r.reg ? `${kv([['Reference', mono(r.ref)], ['Kind', mono(r.kind)], ['Store', mono(r.store)], ['Purpose', r.purpose ? esc(r.purpose) : null], ['Consent ID', r.consent ? mono(r.consent) : null], ['Parent', r.n.parents.map(rRec).join(' ') || null], ['Children', r.n.children.map(rRec).join(' ') || null], ['Conversation', r.n.conversations.map(mono).join(' ') || null]])}<h4>Vault tokens referenced</h4>${r.tokens.map(rTok).join(' ') || '<span class="t3">none</span>'}`
      : `${kv([['Store', mono(r.store)], ['File', mono(r.path)], ['Record ID', mono(r.id)], ['In lineage registry', pill('bad', 'no')]])}<div class="alert warn" style="margin-top:12px">The registry never recorded this copy. Layer 6 finds it by searching content, not by looking it up. <a href="${L('privacy')}">Open Erasure</a></div>`);
}
function lineageSvg(d) {
  const colx = { b: 70, c: 215, log: 380, chunk: 560, emb: 740 }, W = 860, kc = { log: '#1570ef', chunk: '#6941c6', emb: '#0e7090', eval: '#b54708' };
  const nodes = d.graph.nodes.filter(n => n.kind in kc), pos = {}, rh = 30, y0 = 54;
  const logs = nodes.filter(n => n.kind === 'log'), chunks = nodes.filter(n => n.kind === 'chunk'), evals = nodes.filter(n => n.kind === 'eval'), embs = nodes.filter(n => n.kind === 'emb');
  if (!nodes.length) return '<div class="empty"><b>No records registered</b><span>Send a message in Gateway.</span></div>';
  logs.forEach((n, i) => pos[n.id] = [colx.log, y0 + i * rh]); chunks.forEach((n, i) => pos[n.id] = [colx.chunk, y0 + i * rh]); evals.forEach((n, i) => pos[n.id] = [colx.chunk, y0 + (chunks.length + 1 + i) * rh]);
  embs.forEach(n => { const p = d.graph.edges.find(e => e.to === n.id), pp = p && pos[p.from]; pos[n.id] = [colx.emb, pp ? pp[1] : y0]; });
  const rows = Math.max(logs.length, chunks.length + evals.length + 1, 3), mid = y0 + (rows - 1) * rh / 2;
  const orph = []; d.stores.forEach(s => s.records.forEach(r => { if (!r.in_registry) orph.push([s.store, r.id]); }));
  const oy = y0 + rows * rh + 40, H = oy + (orph.length ? 38 + Math.ceil(orph.length / 3) * 32 : 14);
  const ln = (a, b, c, d2, s = '#d0d5dd') => `<path d="M${a},${b} C${(a + c) / 2},${b} ${(a + c) / 2},${d2} ${c},${d2}" stroke="${s}" stroke-width="1.3" fill="none"/>`;
  let g = `<svg viewBox="0 0 ${W} ${H}" width="100%" style="min-width:720px">`;
  g += [['BORROWER', colx.b], ['CONVERSATION', colx.c], ['LOGS', colx.log + 40], ['CHUNKS / EVALS', colx.chunk + 40], ['EMBEDDINGS', colx.emb + 40]].map(([t, x]) => `<text x="${x}" y="26" fill="#98a2b3" font-size="10" text-anchor="middle" letter-spacing="1">${t}</text>`).join('');
  g += ln(colx.b + 38, mid, colx.c - 44, mid); [...logs, ...chunks, ...evals].forEach(n => { const p = pos[n.id]; g += ln(colx.c + 44, mid, p[0], p[1]); });
  d.graph.edges.forEach(e => { const a = pos[e.from], b = pos[e.to]; if (a && b) g += ln(a[0] + 82, a[1], b[0], b[1], '#a4bcfd'); });
  g += `<rect x="${colx.b - 38}" y="${mid - 17}" width="76" height="34" rx="8" fill="#eef2ff" stroke="#2f5bea"/><text x="${colx.b}" y="${mid + 4}" fill="#1e40c7" font-size="11" text-anchor="middle">borrower</text><rect x="${colx.c - 44}" y="${mid - 17}" width="88" height="34" rx="8" fill="#fff" stroke="#d0d5dd"/><text x="${colx.c}" y="${mid + 4}" fill="#475467" font-size="11" text-anchor="middle">conversation</text>`;
  nodes.forEach(n => { const p = pos[n.id]; if (!p) return; const c = kc[n.kind]; g += `<a href="${L('map', n.id)}"><rect x="${p[0]}" y="${p[1] - 11}" width="82" height="22" rx="5" fill="${c}18" stroke="${c}"/><text x="${p[0] + 41}" y="${p[1] + 4}" fill="${c}" font-size="10" text-anchor="middle">${esc(n.label.slice(0, 12))}</text></a>`; });
  if (orph.length) { g += `<text x="20" y="${oy - 14}" fill="#b42318" font-size="10" letter-spacing="1">IN STORES, NOT IN THE REGISTRY</text>`; orph.forEach((o, i) => { const x = 20 + (i % 3) * 280, y = oy + Math.floor(i / 3) * 32; g += `<a href="${L('map', o[0] + ':' + o[1])}"><rect x="${x}" y="${y - 12}" width="262" height="24" rx="5" fill="#fef3f2" stroke="#b42318" stroke-dasharray="4 3"/><text x="${x + 10}" y="${y + 4}" fill="#7a271a" font-size="10">${esc((o[0].replace('unmapped:', '') + ' · ' + o[1]).slice(0, 40))}</text></a>`; }); }
  return g + '</svg>';
}

/* ================================================================ Privacy requests: the DPDP workflow (L6, with L2 key destruction and L7 evidence) */
SCR.privacy = async function () {
  const tk = ++S.tk;
  main().innerHTML = '<div class="pv"><section class="clist" id="plist"></section><section class="pdet" id="pdet"></section></div>';
  const [er, cs] = await Promise.all([api('/api/erasure'), api('/api/cases')]); if (tk !== S.tk) return;
  S.cases = cs; S.reqs = er.requests; renderNav(); drawPrivacy();
};
async function drawPrivacy() {
  const reqs = S.reqs, p = S.param, isNew = p.startsWith('new') || (!p && !reqs.length), sel = !isNew ? (p || reqs[reqs.length - 1].request_id) : '';
  $('#plist').innerHTML = `<div class="lh"><b>Privacy requests</b><span class="t3">${reqs.length}</span><a class="btn sm pri" href="${L('privacy', 'new')}">New request</a></div><div class="litems">${reqs.slice().reverse().map(r => `<a class="ci ${r.request_id === sel ? 'on' : ''}" href="${L('privacy', r.request_id)}"><span class="avc ok">✓</span><div class="cbody"><div class="ct"><b class="mono">${esc(r.request_id)}</b><span class="t3">${fmtT(r.ts).slice(0, 5)}</span></div><div class="cp">Erasure of personal data · Case ${r.case}</div><div class="cm">${yes(r.clean, r.status, r.status)}<span class="mono t3">${r.found} records</span></div></div></a>`).join('') || '<div class="empty"><b>No requests</b><span>Requests you process appear here.</span></div>'}</div>`;
  const d = $('#pdet');
  if (isNew) return privForm(d, p.startsWith('new:') ? p.slice(4) : null);
  const row = reqs.find(r => r.request_id === sel), er = S.erFull[sel];
  if (!row) { d.innerHTML = '<div class="empty"><b>Request not found</b></div>'; return; }
  d.innerHTML = privDetail(row, er);
  if (S.anim === sel) { S.anim = ''; $$('.stp').forEach((s, i) => { s.classList.remove('done'); setTimeout(() => s.classList.add('done'), 160 * (i + 1)); }); }
  const pr = $('#cprint'); if (pr) pr.onclick = () => window.print();
  const dl = $('#cdl'); if (dl) dl.onclick = () => { const t = certText(row, er); const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([t], { type: 'text/plain' })); a.download = `erasure-certificate-${row.request_id}.txt`; a.click(); };
}
async function privForm(d, pre) {
  const active = S.cases.filter(c => c.state === 'ACTIVE' && c.messages); const sid = (pre && active.find(c => c.sid === pre) ? pre : (active.find(c => c.sid === S.case) || active[0] || {}).sid);
  if (!sid) { d.innerHTML = '<div class="empty"><b>No active borrower to process</b><span>All cases are already erased.</span></div>'; return; }
  const [lin, v] = await Promise.all([api('/api/lineage', undefined, sid), api('/api/vault', undefined, sid)]); const c = S.cases.find(x => x.sid === sid);
  const held = lin.stores.reduce((a, s) => a + s.count, 0), un = lin.stores.reduce((a, s) => a + s.unregistered_records, 0);
  d.innerHTML = `<div class="pform"><h2>New privacy request</h2>
    <label class="f">Borrower<select id="pb" class="in">${active.map(x => `<option value="${x.sid}" ${x.sid === sid ? 'selected' : ''}>Case ${x.case} · ${esc(x.hash.slice(0, 10))}</option>`).join('')}</select></label>
    <label class="f">Request type<select class="in"><option>Erasure of personal data</option></select></label>
    <div><div class="t2" style="font-weight:600;margin-bottom:6px">Scope found for this borrower</div>${kv([['Stores holding records', lin.stores.map(s => s.store.replace('unmapped:', '')).join(', ') || 'none'], ['Records', held], ['Not in the registry', un ? `<b class="bad">${un}</b>` : '0'], ['Encrypted values', v.entries.length], ['Borrower key', c.key_present ? pill('ok', 'active') : pill('', 'destroyed')]])}</div>
    <label class="chk"><input type="checkbox" id="pplant" ${c.planted ? 'checked disabled' : ''}> Add copies the registry does not know about before processing <span class="t3">(test setup: simulates a debug log, a derived summary and a crash dump)</span></label>
    <label class="chk"><input type="checkbox" id="pconf"> Delete every copy found and destroy the borrower key. This cannot be undone.</label>
    <div><button class="btn pri" id="pgo" disabled>Process request</button></div></div>`;
  $('#pb').onchange = e => { location.hash = L('privacy', 'new:' + e.target.value); };
  $('#pconf').onchange = e => $('#pgo').disabled = !e.target.checked;
  $('#pgo').onclick = async () => {
    $('#pgo').disabled = true; $('#pgo').innerHTML = '<span class="spin"></span> Processing…';
    try {
      if ($('#pplant').checked && !$('#pplant').disabled) await api('/api/plant', {}, sid);
      const er = await api('/api/erase', {}, sid); S.erFull[er.request_id] = er; S.anim = er.request_id; S.hist[sid] = (S.hist[sid] || []).map(h => ({ turn: h.turn, message_id: h.message_id, ts: h.ts, speaker: h.speaker, status: 'erased' }));
      S.audit = null; S.lin = null; S.cases = await api('/api/cases'); S.reqs = (await api('/api/erasure')).requests; renderNav(); location.hash = L('privacy', er.request_id);
    } catch (e) { toast(e.message, 5000); $('#pgo').disabled = false; $('#pgo').textContent = 'Process request'; }
  };
}
const ESTEPS = [['discover', 'Discover', 'Search every store, including unregistered ones'], ['delete', 'Delete', 'Remove each copy found'], ['verify', 'Verify', 'Search again from scratch'], ['shred', 'Destroy key', 'Make remaining ciphertext unreadable'], ['audit', 'Record', 'Write the chained erasure record']];
function certText(row, e) {
  if (!e) return `Erasure request ${row.request_id}\nStatus: ${row.status}\n`;
  return `CERTIFICATE OF ERASURE\nRequest: ${e.request_id}\nSubject (keyed hash): ${e.full_borrower_hash}\nStatus: ${e.status}\nDate: ${fmtD(e.ts)}\nRecords removed: ${Object.values(e.stores_before).reduce((a, b) => a + b, 0)}\nVault entries: ${e.vault_before} -> ${e.vault_after}\nBorrower key destroyed: ${e.key_present ? 'no' : 'yes'}\nIndependent scan, raw values in store files: ${e.raw_before} -> ${e.raw_after}\nAudit record: #${e.audit_seq}  ${e.full_hash}\n\n${e.proof}\n`;
}
function privDetail(row, e) {
  const c = S.cases.find(x => x.sid === row.sid);
  const head = `<div class="pdh"><div><div class="mono t3">${esc(row.request_id)}</div><h2>Erasure of personal data</h2><div class="t2">Case ${row.case} · keyed hash ${mono(row.borrower_hash.slice(0, 16))} · received ${fmtD(row.ts)}</div></div><div class="pda">${yes(row.clean, row.status, row.status)}<a class="btn" href="${L('audit', row.audit_seq)}">Audit record #${row.audit_seq}</a></div></div>`;
  if (!e) return head + '<div class="alert warn" style="margin:16px">Detail is kept only for requests processed in this browser session.</div>';
  const rec = e.record, line = k => { const t = e.trace.find(x => x.startsWith(k)); return t ? t.slice(t.indexOf(':') + 1).trim() : ''; };
  const stepper = `<div class="stepper">${ESTEPS.map(([k, n, s]) => `<div class="stp done"><span class="dot">✓</span><b>${n}</b><span class="t2">${esc(s)}</span><span class="mono t3">${esc(line(k).slice(0, 120))}</span></div>`).join('')}<div class="stp done"><span class="dot">✓</span><b>Certificate</b><span class="t2">Issued below</span></div></div>`;
  const total = Object.values(e.stores_before).reduce((a, b) => a + b, 0);
  const cert = `<div class="doc" id="cert"><div class="dochead"><div><div class="docmark">PII Vault</div><h3>Certificate of erasure</h3></div><div class="r"><div class="mono">${esc(e.request_id)}</div><div class="t2">${fmtD(e.ts)}</div></div></div>
    ${kv([['Subject (keyed hash)', mono(e.full_borrower_hash)], ['Request', 'Erasure of personal data'], ['Outcome', yes(e.clean, e.status, e.status)], ['Records removed', `${total} across ${rec.stores.length} store(s)`], ['Not in the registry', (rec.unregistered_stores_with_hits || []).map(mono).join(' ') || 'none'], ['Vault entries', `${e.vault_before} → ${e.vault_after}`], ['Borrower key', e.key_present ? pill('bad', 'still present') : pill('ok', 'destroyed')],
      ['Independent file scan', `raw values in store files ${e.raw_before} → ${e.raw_after}`], ['Former token readable', esc(e.probe)], ['Audit record', `${rSeq(e.audit_seq)} ${mono(e.full_hash.slice(0, 32) + '…')}`], ['Chain', e.chain.ok ? pill('ok', 'intact') : pill('bad', 'broken')]])}
    <div class="docnote"><b>What this shows.</b> None of the discovery methods found a remaining copy after deletion, and a separate plain file scan found no raw value.<br><b>What it does not show.</b> That no copy exists in a store or encoding the methods cannot see (encrypted blobs, images, stores outside the root, backups).</div></div>`;
  return head + `<div class="pdb">${stepper}<div class="grid2"><div class="panel"><div class="ph"><b>Where the data was found</b></div>${tbl(['Store', '>Before', '>Found', '>Deleted', '>After', 'Zero'], rec.stores.map(s => `<tr><td class="mono">${esc(s.store.replace('unmapped:', ''))}</td><td class="r mono">${e.stores_before[s.store] ?? ''}</td><td class="r mono">${s.found}</td><td class="r mono">${s.deleted}</td><td class="r mono">${e.stores_after[s.store] ?? 0}</td><td>${yes(s.verified_zero)}</td></tr>`))}</div>
    <div class="panel"><div class="ph"><b>Independent verification</b><span class="t3">plain file scan, not the agent</span></div>${tbl(['Check', '>Before', '>After', 'Result'], [['Raw values in store files', e.raw_before, e.raw_after, e.raw_after === 0], ['Vault entries', e.vault_before, e.vault_after, e.vault_after === 0], ['Borrower key present', 'yes', e.key_present ? 'yes' : 'no', !e.key_present]].map(([n, b, a, ok]) => `<tr><td>${n}</td><td class="r mono">${b}</td><td class="r mono">${a}</td><td>${yes(ok, 'pass', 'fail')}</td></tr>`))}</div></div>
    <div class="panel"><div class="ph"><b>Discovery methods</b><span class="t3">${rec.methods_run.length} ran · ${rec.methods.length} found copies</span></div><div class="pb chips">${rec.methods_run.map(m => `<span class="pill ${rec.methods.some(f => f.startsWith(m.split('(')[0]) || m.startsWith(f.split('(')[0])) ? 'acc' : ''}">${esc(m)}</span>`).join('')}</div></div>
    <div class="certbar"><b>Certificate</b><span style="flex:1"></span><button class="btn" id="cdl">Download</button><button class="btn pri" id="cprint">Print</button></div>${cert}
    <details class="panel" style="margin-top:14px"><summary class="ph" style="cursor:pointer"><b>Full proof text</b></summary><div class="pb"><pre class="code">${esc(e.proof)}</pre></div></details></div>`;
}

/* ================================================================ L7 Audit */
const detailOf = r => {
  const k = r.kind_name;
  if (r.type === 'erasure') return `${r.status} · ${r.passes} pass(es) · ${(r.stores || []).length} stores`;
  if (k.startsWith('write_')) return `${r.store} ← ${r.purpose}${r.reason ? ' · ' + r.reason : ''}`;
  const skip = new Set(['event', 'request_id', 'borrower_hash', 'purpose', 'seq', 'ts', 'prev_hash', 'hash', 'kind_name', '_verify']);
  return Object.entries(r).filter(([x]) => !skip.has(x)).map(([x, v]) => `${x}=${v}`).join(' ');
};
const subjectOf = r => r.record_id ? `${r.store}:${r.record_id}` : r.type === 'erasure' ? r.request_id : '';
/* ================================================================ Borrowers: profile and where this person's data lives (L2, L4, L5) */
SCR.borrowers = async function () {
  const tk = ++S.tk; if (S.param && S.cases.some(c => c.sid === S.param)) S.case = S.param;
  main().innerHTML = '<div class="pv"><section class="clist" id="plist"></section><section class="pdet" id="pdet"><div class="empty"><span class="spin"></span></div></section></div>';
  const [cs, er] = await Promise.all([api('/api/cases'), api('/api/erasure')]); S.cases = cs; S.reqs = er.requests;
  $('#plist').innerHTML = `<div class="lh"><b>Borrowers</b><span class="t3">${cs.length}</span></div><div class="litems">${[...cs].map(c => caseItem(c, L('borrowers', c.sid), c.sid === S.case)).join('')}</div>`;
  const [lin, au, v] = await Promise.all([api('/api/lineage'), api('/api/audit/full'), api('/api/vault')]); if (tk !== S.tk) return; S.lin = lin; S.audit = au;
  const c = curCase(), req = S.reqs.find(r => r.sid === c.sid), dec = decisionsFor(c);
  const acts = au.records.filter(r => (r.borrower_hash || '').startsWith(c.hash));
  const tab = S.bTab, tabs = [['overview', 'Overview'], ['lineage', 'Data lineage'], ['activity', 'Activity']];
  $('#pdet').innerHTML = `<div class="pdh"><div class="bp-head">${avatar(c, 1)}<div><h2 style="margin:0">Case ${c.case}</h2><div class="mono t3">${esc(c.hash)}</div></div></div><div class="pda">${c.state === 'ERASED' ? pill('', 'Erased') : pill('ok', 'Active')}${req ? `<a class="btn" href="${L('privacy', req.request_id)}">Erasure ${esc(req.request_id)}</a>` : c.messages ? `<a class="btn pri" href="${L('privacy', 'new:' + c.sid)}">Start erasure request</a>` : ''}</div></div>
    <div class="tabs" style="padding:0 20px">${tabs.map(([k, n]) => `<button data-bt="${k}" class="${tab === k ? 'on' : ''}">${n}</button>`).join('')}</div><div class="pdb">${
    tab === 'overview' ? `<div class="grid2"><div class="panel"><div class="ph"><b>Identity and protection</b></div><div class="pb">${kv([['Case', c.case], ['Keyed hash', mono(c.hash)], ['Borrower ID', mono(c.borrower)], ['Conversation', mono(c.conversation)], ['State', c.state === 'ERASED' ? pill('', 'ERASED') : pill('ok', 'ACTIVE')], ['Borrower key', c.key_present ? pill('ok', 'active') + ' ' + mono(v.keys[0] ? v.keys[0].key_id : '') : pill('', 'destroyed')], ['Encrypted values', `${c.values} · <a href="${L('vault')}">Vault inspector</a>`], ['Messages', c.messages]])}</div></div>
      <div class="panel"><div class="ph"><b>Where this person's data lives</b></div>${tbl(['Store', 'File', '>Records', '>Untracked'], lin.stores.map(s => `<tr><td class="mono">${esc(s.store.replace('unmapped:', ''))}</td><td class="mono t3">${esc(s.path.split('/').pop())}</td><td class="r mono">${s.count}</td><td class="r mono ${s.unregistered_records ? 'bad' : ''}">${s.unregistered_records}</td></tr>`))}<div class="pb t2">Untracked = in a store but not in the lineage registry. <a href="${L('map')}">Data inventory</a></div></div></div>
      <div class="panel"><div class="ph"><b>Consent decisions</b><span class="t3">by purpose, from the audit trail</span></div>${tbl(['Purpose', '>Allowed', '>Refused'], Object.entries(dec).map(([p, x]) => `<tr><td>${esc(p)}</td><td class="r mono ok">${x.ok}</td><td class="r mono ${x.no ? 'bad' : ''}">${x.no}</td></tr>`))}</div>`
    : tab === 'lineage' ? `<div class="panel"><div class="ph"><b>Data lineage</b><span class="t3">borrower → conversation → records → embeddings</span></div><div class="pb" style="overflow:auto">${lineageSvg(lin)}</div></div>`
    : `<div class="panel"><div class="ph"><b>Activity</b>${pill('', acts.length + ' records')}</div><div class="feed">${feedHtml(feedItems(acts), 40)}</div></div>`}</div>`;
  $$('[data-bt]').forEach(b => b.onclick = () => { S.bTab = b.dataset.bt; SCR.borrowers(); });
};

/* ================================================================ Audit trail: a readable activity feed over the L7 chain, with the raw log behind it */
function feedItems(recs) {
  const items = [], by = new Map();
  recs.forEach(r => {
    if (r.type === 'erasure') { items.push({ kind: 'erasure', ts: r.ts, recs: [r], r }); return; }
    if (r.kind_name && r.kind_name.startsWith('write_')) { items.push({ kind: 'write', ts: r.ts, recs: [r], r }); return; }
    if (r.request_id) { let g = by.get(r.request_id); if (!g) { g = { kind: 'msg', ts: r.ts, recs: [], req: r.request_id }; by.set(r.request_id, g); items.push(g); } g.recs.push(r); return; }
    items.push({ kind: 'other', ts: r.ts, recs: [r], r });
  });
  return items.sort((a, b) => b.ts - a.ts);
}
function feedHtml(items, max = 200) {
  if (!items.length) return '<div class="empty"><b>No activity</b></div>';
  return items.slice(0, max).map(g => {
    const first = g.recs[0], cs = caseOfHash(first.borrower_hash), cl = cs ? `<a class="ref" href="${L('borrowers', cs.sid)}">Case ${cs.case}</a>` : '', k = new Set(g.recs.map(r => r.kind_name));
    let title, sub, tone = 'ok', chips = '';
    if (g.kind === 'erasure') { const r = g.r; title = 'Erasure completed'; sub = `${rEr(r.request_id)} · ${esc(r.status)} · ${r.passes} pass(es)`; tone = r.status === 'ERASED_VERIFIED' ? 'ok' : 'bad'; }
    else if (g.kind === 'write') { const r = g.r, okw = r.kind_name === 'write_allowed'; title = okw ? `Stored in ${r.store}` : 'Write refused'; sub = okw ? `${esc(r.purpose)} · ${rRec(({ logs: 'log:', vector_store: 'chunk:', eval_sets: 'eval:' }[r.store] || r.store + ':') + r.record_id)}` : `${esc(r.store)} ← ${esc(r.purpose)} · ${mono(r.reason || '')}`; tone = okw ? 'ok' : 'bad'; }
    else if (g.kind === 'msg') {
      const vals = g.recs.filter(r => r.kind_name === 'L2_TOKENISE_COMPLETE').reduce((a, r) => a + (r.values || 0), 0), last = g.recs[g.recs.length - 1], deny = g.recs.find(r => r.kind_name === 'CONSENT_DENIED'), blk = g.recs.find(r => r.kind_name === 'L3_BLOCKED');
      title = deny ? 'Message denied by consent policy' : blk ? 'Message blocked' : 'Message processed'; tone = deny || blk ? 'bad' : 'ok';
      sub = `${first.purpose || ''}${vals ? ` · ${vals} value(s) protected` : ''} · ${last.latency_ms ?? ''} ms · ${rReq(g.req)}`;
      if (g.recs.filter(r => r.kind_name === 'L2_TOKENISE_COMPLETE').length > 1) chips += pill('warn', 'audit re-masked');
      if (k.has('LEAK_PREVENTED')) chips += pill('acc', 'leak prevented');
      if (deny) chips += pill('bad', esc(deny.reason || ''));
    } else { title = first.kind_name; sub = ''; }
    return `<div class="fi2"><span class="fdot ${tone}"></span><div class="fbody"><div class="ft"><b>${esc(title)}</b><span class="mono t3">${fmtT(g.ts)}</span></div><div class="fs">${cl}${cl ? ' · ' : ''}${sub}</div>${chips ? `<div class="fc">${chips}</div>` : ''}
      <details><summary>${g.recs.length} record(s)</summary>${tbl(['Seq', 'Event', 'Hash'], g.recs.map(r => `<tr><td>${rSeq(r.seq)}</td><td class="mono">${esc(r.kind_name)}</td><td class="mono t3">${esc(r.hash.slice(0, 12))}</td></tr>`))}</details></div></div>`;
  }).join('');
}
SCR.audit = async function () {
  loading('audit'); const tk = ++S.tk; const a = S.audit = await api('/api/audit/full'); await loadCasesOnly(); if (tk !== S.tk) return;
  if (S.param && S.param.startsWith('req:')) { S.af.q = S.param.slice(4); S.auTab = 'raw'; }
  const kinds = {}; a.records.forEach(r => kinds[r.kind_name] = (kinds[r.kind_name] || 0) + 1);
  main().innerHTML = sbar('audit', `<button class="btn" id="ver">Verify chain</button><button class="btn dng" id="tam">Tamper test</button><a class="btn" href="/api/audit/export">Export JSONL</a>`) + `<div class="content">
    <div class="alert ${a.chain.ok ? 'ok' : 'bad'}" id="vbanner"><div><b>${a.chain.ok ? 'Chain intact' : 'Chain broken at #' + a.chain.bad_seq}</b> · ${a.length} records · head ${mono(a.head.slice(0, 24) + '…')} · ${esc(a.algorithm)}</div></div>
    <div class="panel"><div class="tabs"><button data-at="feed" class="${S.auTab === 'feed' ? 'on' : ''}">Activity</button><button data-at="raw" class="${S.auTab === 'raw' ? 'on' : ''}">Raw records<i>${a.length}</i></button></div>
    <div class="toolbar"><select id="acase" class="in"><option value="">All borrowers</option>${S.cases.map(c => `<option value="${c.hash}" ${S.af.case === c.hash ? 'selected' : ''}>Case ${c.case}</option>`).join('')}</select>
      ${S.auTab === 'raw' ? `<input id="aq" class="in" style="width:260px" placeholder="Filter: request id, event, store, record, hash…" value="${esc(S.af.q)}"><div class="chips" id="ak">${Object.entries(kinds).sort().map(([k, n]) => `<button class="chip ${S.af.kinds.includes(k) ? 'on' : ''}" data-k="${esc(k)}">${esc(k)}<b>${n}</b></button>`).join('')}</div>` : ''}<span style="flex:1"></span><span class="t2" id="acount"></span></div>
    ${S.auTab === 'feed' ? '<div class="feed" id="afeed"></div>' : '<div style="overflow:auto;max-height:calc(100vh - 330px)" id="atable"></div>'}</div></div>`;
  bindCommon();
  $$('[data-at]').forEach(b => b.onclick = () => { S.auTab = b.dataset.at; SCR.audit(); });
  $('#acase').onchange = e => { S.af.case = e.target.value; S.auTab === 'feed' ? drawFeed() : drawAudit(); };
  if (S.auTab === 'feed') drawFeed(); else {
    drawAudit(); $('#aq').oninput = e => { S.af.q = e.target.value; drawAudit(); };
    $$('#ak .chip').forEach(c => c.onclick = () => { const k = c.dataset.k; S.af.kinds = S.af.kinds.includes(k) ? S.af.kinds.filter(x => x !== k) : [...S.af.kinds, k]; c.classList.toggle('on'); drawAudit(); });
  }
  $('#ver').onclick = verifyChain; $('#tam').onclick = tamper;
  if (/^\d+$/.test(S.param)) showAudit(Number(S.param));
};
async function loadCasesOnly() { S.cases = await api('/api/cases'); }
function drawFeed() {
  const recs = S.audit.records.filter(r => !S.af.case || (r.borrower_hash || '').startsWith(S.af.case) || (r.type === 'erasure' && r.borrower_hash && r.borrower_hash.startsWith(S.af.case)));
  const items = feedItems(recs); $('#afeed').innerHTML = feedHtml(items); $('#acount').textContent = `${items.length} events from ${recs.length} records`;
}

/* ================================================================ Architecture: the layers as a system, with live figures */
SCR.architecture = async function () {
  loading('architecture'); const tk = ++S.tk; const [au, cs, mx] = await Promise.all([api('/api/audit/full'), api('/api/cases'), api('/api/consent/matrix')]); if (tk !== S.tk) return; S.cases = cs;
  const ev = au.records, active = cs.filter(c => c.state === 'ACTIVE').length, vals = cs.reduce((a, c) => a + c.values, 0), keys = cs.filter(c => c.key_present).length;
  const wa = ev.filter(r => r.kind_name === 'write_allowed').length, wr = ev.filter(r => r.kind_name === 'write_rejected' || r.kind_name === 'CONSENT_DENIED').length;
  const msgs = new Set(ev.filter(r => r.kind_name === 'L3_STARTED').map(r => r.request_id)).size, blocked = ev.filter(r => r.kind_name === 'L3_BLOCKED').length, prevented = ev.filter(r => r.kind_name === 'LEAK_PREVENTED').length;
  const t = id => { const x = S.tests[id]; return x && !x.running ? `<span class="${x.ok ? 'ok' : 'bad'}">${x.ok ? '✓ ' + x.passed : '✕ ' + x.failed}</span>` : ''; };
  const box = (id, name, stat, note) => `<button class="ab" data-ab="${id}"><span class="abh"><b>${id}</b><span>${esc(name)}</span></span><span class="abs">${stat}</span><span class="abn">${note}</span><span class="abt">${t(id)}</span></button>`;
  const ar = (lbl) => `<span class="ar"><i>${esc(lbl)}</i><b>→</b></span>`;
  main().innerHTML = sbar('architecture', '<button class="btn" id="runall">Run all test suites</button>') + `<div class="content"><div class="arch">
    <div class="alane"><div class="actor"><b>Borrower</b><span>call or chat</span></div>${ar('raw text')}${box('L1', 'Detection', `${msgs} messages scanned`, 'rules · spoken-number engine · model')}${ar('spans')}${box('L2', 'Vault', `${vals} values · ${keys} keys`, 'AES-256-GCM, a key per borrower')}${ar('tokens only')}${box('L3', 'Gateway', `${msgs - blocked} sent · ${blocked} blocked`, `audit net · ${prevented} leak(s) prevented`)}${ar('masked text')}<div class="actor llm"><b>LLM</b><span>${S.status.llm_live ? 'Nova Pro' : 'stub'} · tokens only</span></div></div>
    <div class="avert"><span>writes go through L4</span></div>
    <div class="alane">${box('L4', 'Consent policy', `${wa} allowed · ${wr} refused`, `${mx.stores.length} stores × ${mx.purposes.length} purposes, default deny`)}${ar('tagged record')}<div class="store"><b>Stores</b><span>logs · vector store · eval sets</span><span>${cs.reduce((a, c) => a + c.messages, 0)} messages stored</span></div>${ar('refs')}${box('L5', 'Lineage registry', `${active} active · ${cs.length - active} erased`, 'where every copy went')}</div>
    <div class="avert"><span>erasure request</span></div>
    <div class="alane"><div class="actor"><b>Privacy request</b><span>DPDP erasure</span></div>${ar('')}${box('L6', 'Erasure agent', `${S.reqs.length} request(s) processed`, 'discover · delete · verify · shred key')}<span class="ar" style="flex:1;place-items:start;text-align:left;padding-left:16px"><i>searches the stores and destroys the L2 key</i></span></div>
    <div class="abus"><span class="abh"><b>L7</b><span>Tamper-evident audit trail</span></span><span class="abs">${ev.length} records · chain ${au.chain.ok ? '<span class="ok">intact</span>' : '<span class="bad">broken</span>'}</span><span class="abn">every layer above writes here · hash(prev + record) · keyed borrower hashes</span><span class="abt"><button class="btn sm" data-ab="L7">Details</button> <a class="btn sm" href="${L('audit')}">Open</a> ${t('L7')}</span></div>
    <div class="aside2">${box('L0', 'Test data', '25,000 synthetic rows', 'scores every layer; final split sealed')}${box('L8', 'Scenario', 'end to end', 'one borrower through all layers')}</div></div></div>`;
  bindCommon(); $$('[data-ab]').forEach(b => b.onclick = () => openInfo(b.dataset.ab));
  $('#runall').onclick = async () => { $('#runall').disabled = true; for (const id of Object.keys(S.content.suites)) await runTests(id); SCR.architecture(); };
};

function drawAudit() {
  const a = S.audit, q = S.af.q.toLowerCase(); let rows = a.records.filter(r => (!S.af.case || (r.borrower_hash || '').startsWith(S.af.case)) && (!S.af.kinds.length || S.af.kinds.includes(r.kind_name)) && (!q || JSON.stringify(r).toLowerCase().includes(q)));
  const total = rows.length; rows = rows.slice().reverse().slice(0, 400); $('#acount').textContent = `${total} of ${a.length} records` + (total > 400 ? ' (newest 400 shown)' : '');
  $('#atable').innerHTML = tbl(['Seq', 'Time', 'Event', 'Request', 'Borrower', 'Purpose', 'Subject', 'Detail', 'Hash', ''], rows.map(r => `<tr data-row="${r.seq}"><td class="mono">${r.seq}</td><td class="mono t3 nw">${fmtT(r.ts)}</td><td class="mono nw"><b>${esc(r.kind_name)}</b></td><td class="mono t3">${esc(sh(r.request_id, 15))}</td><td class="mono t3">${esc((r.borrower_hash || '').slice(0, 8))}</td><td>${esc(r.purpose || '')}</td><td class="mono">${esc(subjectOf(r))}</td><td class="mono t2" style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(detailOf(r))}">${esc(detailOf(r))}</td><td class="mono t3">${esc(r.hash.slice(0, 10))}</td><td>${r._verify.hash_ok && r._verify.link_ok ? '<span class="ok">✓</span>' : '<span class="bad">✕</span>'}</td></tr>`));
  $$('#atable [data-row]').forEach(r => r.onclick = () => showAudit(Number(r.dataset.row)));
}
function showAudit(seq) {
  const a = S.audit, r = a.records[seq]; if (!r) return; const prev = a.records[seq - 1], next = a.records[seq + 1], same = a.records.filter(x => x.request_id && x.request_id === r.request_id);
  const skip = new Set(['hash', 'prev_hash', 'kind_name', '_verify']);
  const raw = JSON.stringify(Object.fromEntries(Object.entries(r).filter(([k]) => k !== 'kind_name' && k !== '_verify')), null, 2);
  openDrawer(`#${r.seq} · <span class="mono">${esc(r.kind_name)}</span>`, r._verify.hash_ok && r._verify.link_ok ? pill('ok', 'verified') : pill('bad', 'invalid'), `
    <h4>Integrity</h4>${kv([['Sequence', `${r.seq} ${r._verify.seq_ok ? pill('ok', 'in order') : pill('bad', 'out of order')}`], ['Timestamp', mono(fmtD(r.ts))], ['Record hash', cp(r.hash)], ['Recomputed', r._verify.hash_ok ? pill('ok', 'hash matches body') : pill('bad', 'hash does not match')],
      ['Previous hash', (prev ? rSeq(prev.seq) + ' ' : '') + mono(r.prev_hash.slice(0, 20) + '…')], ['Link', r._verify.link_ok ? pill('ok', 'matches previous record') : pill('bad', 'broken')], ['Next record', next ? rSeq(next.seq) + ' ' + mono(next.kind_name) : 'head of the chain'], ['Algorithm', esc(a.algorithm)]])}
    <h4>Fields</h4>${kv(Object.entries(r).filter(([k]) => !skip.has(k) && k !== 'seq' && k !== 'ts').map(([k, v]) => [k, mono(typeof v === 'object' ? JSON.stringify(v) : v)]))}
    <h4>References</h4>${kv([['Request', r.request_id ? rReq(r.request_id) + ` <span class="t3">${same.length} record(s)</span>` : null], ['Borrower (keyed hash)', r.borrower_hash ? `<a href="${L('audit', 'req:' + r.borrower_hash)}" class="ref">${esc(r.borrower_hash.slice(0, 16))}…</a>` : null], ['Record', r.record_id ? rRec(({ logs: 'log:', vector_store: 'chunk:', eval_sets: 'eval:' }[r.store] || r.store + ':') + r.record_id) : null], ['Erasure request', r.type === 'erasure' ? rEr(r.request_id) : null], ['Purpose', r.purpose ? mono(r.purpose) : null], ['Consent ID', r.consent_id ? mono(r.consent_id) : null]])}
    ${same.length > 1 ? `<h4>Same request</h4>${tbl(['Seq', 'Event', 'Time'], same.map(x => `<tr class="${x.seq === r.seq ? 'sel' : ''}"><td>${rSeq(x.seq)}</td><td class="mono">${esc(x.kind_name)}</td><td class="mono t3">${fmtT(x.ts)}</td></tr>`))}` : ''}
    <h4>Raw record <button class="btn sm ghost" onclick="copyText(document.getElementById('rawrec').textContent)">Copy</button></h4><pre class="code" id="rawrec">${esc(raw)}</pre>`);
  $$('#drawer a.ref').forEach(l => l.addEventListener('click', () => { }));
}
async function verifyChain() {
  const t = performance.now(); const a = S.audit = await api('/api/audit/full'); const ms = (performance.now() - t).toFixed(0);
  const h = a.records.filter(r => r._verify.hash_ok).length, l = a.records.filter(r => r._verify.link_ok).length, q = a.records.filter(r => r._verify.seq_ok).length;
  $('#vbanner').className = 'alert ' + (a.chain.ok ? 'ok' : 'bad');
  $('#vbanner').innerHTML = `<div><b>${a.chain.ok ? 'Chain intact' : 'Chain broken at #' + a.chain.bad_seq}</b> · recomputed ${a.length} hashes in ${ms} ms · hash matches ${h}/${a.length} · links ${l}/${a.length} · sequence ${q}/${a.length} · head ${mono(a.head)}<div class="t2">The head must be anchored somewhere write-once for this to defend against a full rewrite. Not built.</div></div>`; drawAudit();
}
async function tamper() {
  try {
    const t = await api('/api/audit/tamper', {}), a = S.audit; const win = a.records.filter(r => r.seq >= t.edited_seq - 1 && r.seq <= t.edited_seq + 3);
    openModal('Tamper test (on a copy of the log)', `${kv([['Record edited', rSeq(t.edited_seq)], ['Field', mono(t.field)], ['Change', `${mono(t.old)} → ${mono(t.new)}`], ['Verification of the copy', pill('bad', 'BROKEN at #' + t.detected_at)], ['Reason', mono(t.reason)], ['Real log', t.real_log_intact ? pill('ok', 'untouched and intact') : pill('bad', 'affected')]])}
      <div class="chain">${win.map(r => `<div class="blk ${r.seq >= t.edited_seq ? 'bad' : ''}"><span class="t3">#${r.seq}</span><span class="nm">${esc(r.kind_name)}</span><span class="t3">${esc(r.hash.slice(0, 10))}</span></div>`).join('')}</div>
      <div class="t2">Every record from the edited one onward no longer chains.</div>`, '<button class="btn pri" id="mok">Close</button>'); $('#mok').onclick = closeModal;
  } catch (e) { toast(e.message, 4000); }
}

/* ================================================================ L0 Test data */
SCR.data = async function () {
  loading('data'); const tk = ++S.tk; const d = await api(`/api/data/list?lang=${S.dataLang}&page=${S.dataPage}&size=40`); if (tk !== S.tk) return;
  const T = S.content.corpus.targets || {};
  main().innerHTML = sbar('data') + `<div class="content fill"><div class="split" style="grid-template-columns:minmax(0,1fr) 520px"><section class="thread" style="overflow:auto">
    <div class="toolbar" style="border-radius:0"><select id="dl" class="in"><option value="">All languages (${Object.values(d.per_language).reduce((a, b) => a + b, 0).toLocaleString()})</option>${d.languages.map(l => `<option value="${l}" ${S.dataLang === l ? 'selected' : ''}>${l} (${d.per_language[l].toLocaleString()})</option>`).join('')}</select>
      <span class="t2">dev split only · rows ${S.dataPage * 40 + 1}–${Math.min((S.dataPage + 1) * 40, d.total)} of ${d.total.toLocaleString()}</span><span style="flex:1"></span><button class="btn sm" id="dp" ${S.dataPage ? '' : 'disabled'}>Previous</button><button class="btn sm" id="dn" ${(S.dataPage + 1) * 40 < d.total ? '' : 'disabled'}>Next</button></div>
    ${tbl(['ID', 'Language', 'Scenario', 'Channel', 'Form', 'PII type', '>Spans', '>Turns'], d.rows.map(r => `<tr data-row="${r.offset}" class="${S.dataSel === r.offset ? 'sel' : ''}"><td class="mono">${esc(r.id)}</td><td class="mono">${esc(r.lang)}</td><td>${esc(r.scenario)}</td><td>${esc(r.channel)}</td><td>${esc(r.form)}</td><td class="mono">${esc(r.primary_pii || '')}</td><td class="r mono">${r.spans}</td><td class="r mono">${r.turns}</td></tr>`))}
    <div class="pb" style="border-top:1px solid var(--line)">${tbl(['Split', '>Rows', 'Use'], [['dev', T.dev, 'tuning'], ['test', T.test, 'reporting'], ['final', T.final, 'sealed, not opened']].map(x => `<tr><td class="mono">${x[0]}</td><td class="r mono">${(x[1] || 0).toLocaleString()}</td><td>${x[2]}</td></tr>`))}</div></section>
    <aside class="inspector" id="drow"><div class="empty"><b>No row selected</b><span>Select a row to view the conversation and its gold labels.</span></div></aside></div></div>`;
  bindCommon();
  $('#dl').onchange = e => { S.dataLang = e.target.value; S.dataPage = 0; SCR.data(); }; $('#dp').onclick = () => { S.dataPage--; SCR.data(); }; $('#dn').onclick = () => { S.dataPage++; SCR.data(); };
  $$('[data-row]').forEach(r => r.onclick = async () => { S.dataSel = Number(r.dataset.row); $$('[data-row]').forEach(x => x.classList.toggle('sel', x === r)); const w = await api('/api/data/row?offset=' + S.dataSel); $('#drow').innerHTML = `<div class="ph"><b class="mono">${esc(w.id)}</b>${pill('', w.lang)}${pill('', w.scenario)}</div><div class="body" style="overflow:auto"><div class="hlbox" style="font-size:13.5px">${hl(w.text, w.spans)}</div>${tbl(['Type', 'Value', 'Rendering', 'Turn', 'Offsets'], w.spans.map(s => `<tr><td><span class="tag" style="--c:${col(s.type)}">${esc(s.type)}</span></td><td class="mono">${esc(s.value || '')}</td><td>${esc(s.rendering || '')}</td><td class="mono">${s.turn ?? ''}</td><td class="mono t3">${s.start}–${s.end}</td></tr>`))}</div>`; });
};

/* ================================================================ L8 End-to-end */
SCR.e2e = function () {
  const e = S.e2e;
  main().innerHTML = sbar('e2e', `<button class="btn pri" id="rune" ${e && e.running ? 'disabled' : ''}>${e && e.running ? 'Running…' : e ? 'Run again' : 'Run scenario'}</button>`) + `<div class="content">
    <div class="panel"><div class="ph"><b>Scenario</b></div><div class="pb">${kv([['Name', 'Collections call, then erasure request'], ['Steps', '10-turn call through the gateway · consent writes · unregistered copies added · erasure · independent scan · audit verify and tamper test'], ['Borrower', 'new session, fake values'], ['LLM', S.status.llm_live ? 'Bedrock (tokenised text only)' : 'local stub']])}</div></div>
    <div class="panel"><div class="ph"><b>Log</b>${e ? pill(e.running ? 'acc' : e.checks.every(c => c[3]) ? 'ok' : 'bad', e.running ? 'running' : e.checks.every(c => c[3]) ? 'all checks passed' : 'checks failed') : ''}</div><div class="pb"><div class="console" id="elog">${e ? e.log.map(x => `<div class="l"><span class="ts">${x.t}</span> <span class="L">${x.l}</span> ${x.m}</div>`).join('') : '<div class="l t3">Not run yet.</div>'}</div></div></div>
    ${e && e.checks.length ? `<div class="panel"><div class="ph"><b>Checks</b></div>${tbl(['Check', 'Expected', 'Observed', 'Result'], e.checks.map(c => `<tr><td>${esc(c[0])}</td><td class="mono t2">${esc(c[1])}</td><td class="mono">${esc(c[2])}</td><td>${yes(c[3], 'pass', 'fail')}</td></tr>`))}<div class="pb t2">Inspect: ${[['conversations', 'Conversations'], ['vault', 'Vault inspector'], ['consent', 'Consent policy'], ['map', 'Data inventory'], ['privacy', 'Privacy requests'], ['audit', 'Audit trail']].map(([s, n]) => `<a href="${L(s)}">${n}</a>`).join(' · ')}</div></div>` : ''}</div>`;
  bindCommon(); const lg = $('#elog'); if (lg) lg.scrollTop = lg.scrollHeight; $('#rune').onclick = runE2E;
};
async function runE2E() {
  const t0 = performance.now(), e = S.e2e = { running: true, log: [], checks: [] };
  const log = (l, m) => { e.log.push({ t: ((performance.now() - t0) / 1000).toFixed(2).padStart(6), l, m: esc(m) }); if (S.screen === 'e2e') { SCR.e2e(); } };
  try {
    await newCase('Scenario borrower'); S.e2e = e; log('L3', `new case ${S.status.case}: fresh borrower key, same workspace`);
    const runs = [];
    for (const [who, text] of CALL) { const r = await send(text, { speaker: who, force: true, purpose: 'service', detector: 'normal' }); if (!r) throw new Error('message failed'); runs.push(r); log('L3', `${who.padEnd(8)} ${r.status} · ${r.l3.ms} ms · ${r.l1.spans.length} masked · ${r.l7.events.length} events · ${r.request_id}`); await sleep(200); }
    const toks = runs.map(r => new Set(r.l2.tokens_this_message)), phoneTok = [...toks[3]].filter(t => toks[5].has(t));
    const last = runs[runs.length - 1];
    log('L2', `${last.l2.entries.length} encrypted entries under key ${last.l2.keys[0].key_id}`);
    const a1 = await api('/api/consent/try', { store: 'eval_sets', purpose: 'service' }); log('L4', `eval_sets ← service: ${a1.allowed ? 'written' : 'rejected'} (${a1.reason}) audit #${a1.event.seq}`);
    const a2 = await api('/api/consent/try', { store: 'eval_sets', purpose: 'model_improvement', explicit: true, consent_id: 'CN-DEMO-1' }); log('L4', `eval_sets ← model_improvement + consent CN-DEMO-1: ${a2.allowed ? 'written' : 'rejected'} (${a2.reason}) audit #${a2.event.seq}`);
    const pl = await api('/api/plant', {}); log('L5', `${pl.planted.length} copies added outside the registry: ${pl.planted.map(p => p.store + ':' + p.id).join(', ')}`);
    const lin = await api('/api/lineage'); const regRecs = lin.graph.nodes.filter(n => ['log', 'chunk', 'eval'].includes(n.kind)).length, tot = lin.stores.reduce((a, s) => a + s.count, 0), unreg = lin.stores.reduce((a, s) => a + s.unregistered_records, 0);
    log('L5', `registry knows ${regRecs} records; stores hold ${tot}; ${unreg} not in registry`);
    const er = await api('/api/erase', {}); S.erFull[er.request_id] = er; S.reqs = (await api('/api/erasure')).requests; renderNav(); er.trace.forEach(t => log('L6', t)); log('L6', `${er.request_id} → ${er.status} in ${er.secs}s · audit #${er.audit_seq}`);
    log('L8', `independent scan: raw values ${er.raw_before} → ${er.raw_after} · vault entries ${er.vault_before} → ${er.vault_after} · key present ${er.key_present} · token read: ${er.probe}`);
    const au = S.audit = await api('/api/audit/full'); const tm = await api('/api/audit/tamper', {}); S.status = await api('/api/status'); renderEnv();
    log('L7', `chain ${au.chain.ok ? 'intact' : 'BROKEN'} (${au.length} records) · edit to #${tm.edited_seq} ${tm.copy_verifies ? 'NOT detected' : 'detected'} · real log intact: ${tm.real_log_intact}`);
    e.checks = [['All 10 turns passed the gateway', '10 / 10 ok', `${runs.filter(r => r.status === 'ok').length} / 10 ok`, runs.every(r => r.status === 'ok')],
      ['Number said in two pieces becomes one token', 'shared token', phoneTok.join(' ') || 'none', phoneTok.length > 0],
      ['Service data refused for eval_sets', 'rejected', a1.allowed ? 'written' : 'rejected', !a1.allowed], ['Same write with explicit consent', 'written', a2.allowed ? 'written' : 'rejected', a2.allowed],
      ['Copies outside the registry found', '≥ 1 unregistered store', (er.record.unregistered_stores_with_hits || []).join(', ') || 'none', (er.record.unregistered_stores_with_hits || []).length > 0],
      ['Erasure status', 'ERASED_VERIFIED', er.status, er.status === 'ERASED_VERIFIED'], ['Raw values left in store files', '0', String(er.raw_after), er.raw_after === 0],
      ['Vault entries and key', '0 entries, key destroyed', `${er.vault_after} entries, key ${er.key_present ? 'present' : 'destroyed'}`, er.vault_after === 0 && !er.key_present],
      ['Audit chain verifies', 'intact', au.chain.ok ? 'intact' : 'broken', au.chain.ok], ['Tampering is detected', 'detected, real log intact', `${tm.copy_verifies ? 'missed' : 'detected'}, real log ${tm.real_log_intact ? 'intact' : 'affected'}`, !tm.copy_verifies && tm.real_log_intact]];
  } catch (err) { log('ERR', err.message); e.checks = [['Scenario completed', 'completed', err.message, false]]; }
  e.running = false; if (S.screen === 'e2e') SCR.e2e();
}

/* ================================================================ Layers and tests */
const LAYER_SCREEN = { L0: 'data', L1: 'detection', L2: 'vault', L3: 'conversations', L4: 'consent', L5: 'map', L6: 'privacy', L7: 'audit', L8: 'e2e' };
const LAYER_MOD = { L0: 'layer0_data', L1: 'layer1_detection', L2: 'layer2_vault', L3: 'layer3_gateway', L4: 'layer4_consent', L5: 'layer5_lineage', L6: 'layer6_erasure', L7: 'layer7_audit', L8: 'layer8_demo' };
SCR.layers = function () {
  const rows = S.content.layers.map(l => { const t = S.tests[l.id], sc = { id: LAYER_SCREEN[l.id], mod: LAYER_MOD[l.id] }; return `<tr data-row="${l.id}"><td class="mono"><b>${l.id}</b></td><td><a href="${L(sc.id)}">${esc(l.name)}</a></td><td class="mono t3">${esc(sc.mod)}</td><td class="mono">${S.content.suites[l.id] ? S.content.suites[l.id] + ' suite(s)' : '<span class="t3">none</span>'}</td><td>${t ? (t.running ? '<span class="spin"></span>' : yes(t.ok, `${t.passed} passed${t.skipped ? ' (+' + t.skipped + ' skipped)' : ''}`, `${t.failed} failed`)) : '<span class="t3">not run</span>'}</td><td class="r mono">${l.limits.length}</td><td class="r">${S.content.suites[l.id] ? `<button class="btn sm" data-run="${l.id}">Run</button>` : ''}</td></tr>`; }).join('');
  main().innerHTML = `<div class="sbar"><h1>Test suites</h1><span style="margin-left:auto"><button class="btn pri" id="runall">Run all suites</button></span></div><div class="content"><div class="panel">${tbl(['Layer', 'Name', 'Module', 'Tests', 'Last run', '>Known limits', ''], [rows])}</div></div>`;
  $$('[data-row]').forEach(r => r.onclick = e => { if (e.target.closest('a,button')) return; openInfo(r.dataset.row); });
  $$('[data-run]').forEach(b => b.onclick = async e => { e.stopPropagation(); b.disabled = true; S.tests[b.dataset.run] = { running: true }; SCR.layers(); await runTests(b.dataset.run); SCR.layers(); });
  $('#runall').onclick = async () => { $('#runall').disabled = true; for (const id of Object.keys(S.content.suites)) { S.tests[id] = { running: true }; SCR.layers(); await runTests(id); } SCR.layers(); };
};

boot().catch(e => { document.body.innerHTML = `<div style="font:15px system-ui;max-width:600px;margin:15vh auto;padding:0 24px"><h2>${e instanceof TypeError && /fetch/i.test(e.message) ? 'Cannot reach the server' : 'The page hit an error'}</h2><p>${esc(e.message)}</p><pre style="background:#f4f5f7;padding:12px;border-radius:8px">.venv/bin/python layer8_demo/server.py</pre></div>`; });
