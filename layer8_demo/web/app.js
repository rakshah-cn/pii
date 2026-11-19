'use strict';
/* TE-03 PII Vault Console. Every panel renders what the real layer returned (see layer8_demo/server.py). Vanilla JS, no dependencies. */
const $ = (s, e = document) => e.querySelector(s);
const $$ = (s, e = document) => [...e.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const sleep = ms => new Promise(r => setTimeout(r, ms));
const fmt = n => Number(n).toLocaleString('en-IN');
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || r.statusText);
  return j;
}
const col = l => `var(--${String(l || '').replace(/[^A-Z_]/g, '') || 'DIGITS'},#97a1af)`;
const ORDER = ['OV', 'L0', 'L1', 'L2', 'L3', 'L4', 'L5', 'L6', 'L7', 'L8'];
const SUB = { L0: 'measure', L1: 'find', L2: 'lock', L3: 'guard', L4: 'permit', L5: 'map', L6: 'forget', L7: 'prove', L8: 'everything, once' };
const DOCK_STAGES = ['L1', 'L2', 'L3', 'L4', 'L5', 'L7'];

const S = { content: null, status: null, stage: 'OV', runs: [], cur: -1, mode: { detector: 'normal' }, touched: {}, tests: {}, attempts: [], erasure: null, tamper: null,
  busy: false, sample: null, sampleLang: '', story: null, cm: { store: 'eval_sets', purpose: 'service' }, flowGen: 0, limitsOpen: {} };
const layer = id => S.content.layers.find(l => l.id === id);
const run = () => S.runs[S.cur];
const touch = (...ids) => ids.forEach(i => S.touched[i] = true);

/* ------------------------------------------------------------------ scenarios (all values are fake) */
const SCN = [
  { k: 'Written: name + phone', turns: [['Customer', 'Hi, I am Ramesh Kumar. My mobile number is 9845012345.']] },
  { k: 'PAN + Aadhaar', turns: [['Customer', 'My PAN card is ABCPK1234L and my Aadhaar is 4521 8890 1267.']] },
  { k: 'Spoken digits, 2 turns', turns: [['Customer', 'Sure, my number is nine eight four five'], ['Customer', 'zero one two three four five. I am Ramesh Kumar.']] },
  { k: 'हिन्दी', turns: [['Customer', 'हाँ जी, मेरा नंबर है नौ आठ चार पाँच'], ['Customer', 'शून्य एक दो तीन चार पाँच। मेरा नाम रमेश कुमार है।']] },
  { k: 'Hindi (Roman)', turns: [['Customer', 'Haan ji, mera number hai nau aath chaar paanch'], ['Customer', 'shunya ek do teen chaar paanch. Main Ramesh Kumar bol raha hoon.']] },
  { k: 'ಕನ್ನಡ', turns: [['Customer', 'ನನ್ನ ನಂಬರ್ ಒಂಬತ್ತು ಎಂಟು ನಾಲ್ಕು ಐದು'], ['Customer', 'ಸೊನ್ನೆ ಒಂದು ಎರಡು ಮೂರು ನಾಲ್ಕು ಐದು. ನನ್ನ ಹೆಸರು ಲಕ್ಷ್ಮಿ ದೇವಿ.']] },
  { k: 'தமிழ்', turns: [['Customer', 'என் நம்பர் ஒன்பது எட்டு நான்கு ஐந்து'], ['Customer', 'பூஜ்யம் ஒன்று இரண்டு மூன்று நான்கு ஐந்து. என் பெயர் பிரியா நாயர்.']] },
  { k: 'मराठी', turns: [['Customer', 'माझा नंबर नऊ आठ चार पाच शून्य एक दोन तीन चार पाच आहे. माझे नाव सुरेश पाटील आहे.']] },
  { k: 'Amount + date (keep)', turns: [['Agent', 'Your EMI of ₹4,200 is due on 12 March.']] },
  { k: 'Blind detector: phone + email', cls: 'warnp', detector: 'blind', turns: [['Customer', 'You can call me on 9123456780 or mail rakesh.s@example.com']] },
  { k: 'Blind detector: a name', cls: 'warnp', detector: 'blind', turns: [['Customer', 'This is Priya Nair speaking, please call 9123456780']] },
  { k: 'Wrong purpose', cls: 'warnp', purpose: 'model_improvement', turns: [['Customer', 'My mobile number is 9123456780']] },
];
const CALL = [['Agent', 'Hello sir, am I speaking with Ramesh Kumar?'], ['Customer', 'Yes, speaking.'], ['Agent', 'Please confirm your registered mobile number.'],
  ['Customer', 'nine eight four five zero'], ['Agent', 'Yes, go on.'], ['Customer', 'one two three four five'], ['Agent', 'Your PAN card number please.'],
  ['Customer', 'ABCPK1234L. This is Kumar here.'], ['Agent', 'Your EMI of ₹4,200 is due on the 5th. Thank you.'], ['Customer', 'Okay, I will pay by the 7th.']];

/* ------------------------------------------------------------------ shell */
async function boot() {
  [S.content, S.status] = await Promise.all([api('/api/content'), api('/api/status')]);
  if (S.status.turns || S.status.planted || S.status.erased) S.status = await api('/api/session/new', { name: 'Ramesh Kumar' });    // page load = clean slate: client and server state always agree
  renderChips(); renderRail(); renderDock();
  window.addEventListener('hashchange', () => { const id = location.hash.slice(1); if (ORDER.includes(id) && id !== S.stage) go(id, true); });
  window.addEventListener('keydown', e => {
    if (/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName) || e.metaKey || e.ctrlKey) return;
    const i = ORDER.indexOf(S.stage);
    if (e.key === 'ArrowRight' && i < ORDER.length - 1) go(ORDER[i + 1]);
    if (e.key === 'ArrowLeft' && i > 0) go(ORDER[i - 1]);
  });
  const h = location.hash.slice(1);
  go(ORDER.includes(h) ? h : 'OV', true);
}
async function refreshStatus() { S.status = await api('/api/status'); renderChips(); }
function toast(msg, ms = 2600) { const t = $('#toast'); t.textContent = msg; t.classList.add('on'); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove('on'), ms); }

function renderChips() {
  const s = S.status;
  $('#chips').innerHTML = `
    <span class="chip"><i></i>Vault <b>AES-256-GCM</b></span>
    <span class="chip"><i></i>Detector <b>Chitragupta int8</b></span>
    <span class="chip mock"><i></i>Stores <b>MOCK</b></span>
    <span class="chip" title="Live sends only TOKENISED text to Bedrock Nova Pro. Off = a local stub reply."><i style="background:${s.llm_live ? 'var(--accent)' : 'var(--fg3)'};box-shadow:none"></i>LLM <b>${s.llm_live ? 'Nova Pro (live)' : 'stub'}</b>
      <button id="llmsw" class="switch ${s.llm_live ? 'on' : ''}" aria-label="Toggle live LLM"></button></span>
    <span class="chip">Session <b class="mono">${esc(s.session)}</b><button id="newsess">New</button></span>`;
  $('#llmsw').onclick = async () => { await api('/api/llm', { live: !S.status.llm_live }); await refreshStatus(); toast(S.status.llm_live ? 'Live LLM on: only tokenised text is sent to Nova Pro' : 'Live LLM off: local stub reply'); };
  $('#newsess').onclick = newSession;
}
async function newSession(name) {
  S.status = await api('/api/session/new', { name: typeof name === 'string' ? name : 'Ramesh Kumar' });
  Object.assign(S, { runs: [], cur: -1, touched: {}, attempts: [], erasure: null, tamper: null, story: null });
  renderChips(); renderRail(); render(); toast('New session: fresh vault, key, stores and audit chain');
}
function renderRail() {
  const item = (id, n, name, sub) => `<button class="rl ${S.stage === id ? 'on' : ''}" data-go="${id}"><span class="n">${n}</span><span class="t">${esc(name)}<span class="s">${esc(sub)}</span></span><span class="d ${S.touched[id] || (S.tests[id] && S.tests[id].ok) ? 'v' : ''}"></span></button>`;
  $('#rail').innerHTML = `<h6>Walkthrough</h6>${item('OV', '◇', 'Overview', 'the whole stack')}<div class="sep"></div><h6>The layers we built</h6>` +
    S.content.layers.map(l => item(l.id, l.id.slice(1), l.name, SUB[l.id])).join('') +
    `<div class="legend"><i style="background:var(--ok)"></i>live data or passing tests this session<br><span class="dimmer">← → keys move between layers</span></div>`;
  $$('[data-go]', $('#rail')).forEach(b => b.onclick = () => go(b.dataset.go));
}
function go(id, silent) {
  S.stage = id; if (!silent) history.replaceState(null, '', '#' + id);
  renderRail(); renderDock(); render(); $('#stage').scrollTop = 0;
}
function render() {
  const f = STAGES[S.stage]; const el = $('#stage');
  el.innerHTML = f.html();
  if (f.after) f.after(el);
  $$('[data-go]', el).forEach(b => b.onclick = () => go(b.dataset.go));
  $$('[data-tests]', el).forEach(b => b.onclick = () => runTests(b.dataset.tests));
  $$('[data-lim]', el).forEach(b => b.onclick = () => { S.limitsOpen[b.dataset.lim] = !S.limitsOpen[b.dataset.lim]; render(); });
  $$('[data-msg]', el).forEach(b => b.onclick = () => { S.cur = Math.max(0, Math.min(S.runs.length - 1, S.cur + Number(b.dataset.msg))); render(); });
  $$('[data-scn]', el).forEach(b => b.onclick = () => playScenario(SCN[Number(b.dataset.scn)]));
}

/* ------------------------------------------------------------------ dock (message composer) */
function renderDock() {
  const d = $('#dock'); d.classList.toggle('hide', !DOCK_STAGES.includes(S.stage));
  if (!DOCK_STAGES.includes(S.stage)) return;
  d.innerHTML = `<div class="scn"><span class="lb">Try</span>${SCN.map((s, i) => `<button class="pill ${s.cls || ''}" data-scn="${i}">${esc(s.k)}</button>`).join('')}</div>
    <form id="f"><select id="spk" class="in" aria-label="Speaker"><option>Customer</option><option>Agent</option></select>
    <input id="txt" class="in txt" autocomplete="off" placeholder="Type a message with a (fake) name, phone, PAN, Aadhaar... in any of the five languages" aria-label="Message">
    <div class="seg" role="group" aria-label="Detector mode"><button type="button" data-det="normal" class="${S.mode.detector === 'normal' ? 'on' : ''}">Detector on</button><button type="button" data-det="blind" class="blind ${S.mode.detector === 'blind' ? 'on' : ''}" title="Simulate a detector miss: the audit net has to catch it">Detector blind</button></div>
    <button class="btn pri" style="height:40px" id="sendbtn">Send through the stack ↵</button></form>`;
  $$('[data-scn]', d).forEach(b => b.onclick = () => playScenario(SCN[Number(b.dataset.scn)]));
  $$('[data-det]', d).forEach(b => b.onclick = () => { S.mode.detector = b.dataset.det; renderDock(); });
  $('#f').onsubmit = async e => { e.preventDefault(); const t = $('#txt').value.trim(); if (!t) return; $('#txt').value = ''; await send(t, { speaker: $('#spk').value }); };
}
async function send(text, o = {}) {
  if (S.busy && !o.force) return null;
  S.busy = true;
  try {
    const r = await api('/api/message', { text, speaker: o.speaker || 'Customer', purpose: o.purpose || 'service', detector: o.detector || S.mode.detector });
    S.runs.push(r); S.cur = S.runs.length - 1;
    if (r.status !== 'blocked') touch('L1', 'L2', 'L3', 'L4', 'L5', 'L7'); else touch('L3', 'L4', 'L7');
    if (!o.quiet) { await refreshStatus(); renderRail(); render(); toast(r.status === 'blocked' ? `Blocked: ${r.reason}` : `Message ${r.turn}: ${r.l7.events.length} events chained · ${r.l3.ms} ms`); }
    return r;
  } catch (e) { toast('Error: ' + e.message, 5000); return null; } finally { S.busy = false; }
}
async function playScenario(sc) {
  if (S.status.erased) { toast('This session was erased: start a new session'); return; }
  const was = S.mode.detector;
  for (const [spk, text] of sc.turns) { await send(text, { speaker: spk, purpose: sc.purpose, detector: sc.detector || was, force: true }); await sleep(250); }
  if (S.stage !== 'L1' && S.stage !== 'L2' && S.stage !== 'L3' && S.stage !== 'L4' && S.stage !== 'L5' && S.stage !== 'L7') go('L3');
}

/* ------------------------------------------------------------------ shared pieces */
function hl(text, spans) {
  const sp = [...(spans || [])].sort((a, b) => a.start - b.start); let out = '', pos = 0, i = 0;
  for (const s of sp) {
    if (s.start < pos) continue;
    out += esc(text.slice(pos, s.start));
    out += `<span class="sp" style="--c:${col(s.label || s.type)};--i:${i++}"><sup>${esc(s.label || s.type)}</sup>${esc(text.slice(s.start, s.end))}</span>`; pos = s.end;
  }
  return out + esc(text.slice(pos));
}
const tokHtml = (t, leak) => {
  let h = esc(t).replace(/&lt;([A-Z_]+?)_(\d+)&gt;/g, (m, a, b) => `<span class="tok" style="--c:${col(a === 'PERSON' ? 'PERSON' : a)}">${a}_${b}</span>`);
  (leak || []).forEach(x => { const e = esc(x.text); h = h.split(e).join(`<mark style="background:rgba(255,107,107,.28);color:#ffd0d0;border-radius:4px;padding:0 3px">${e}</mark>`); });
  return h;
};
const legendFor = spans => [...new Set(spans.map(s => s.label || s.type))].map(l => `<span><i style="--c:${col(l)}"></i>${esc(l)}</span>`).join('');
const engines = sources => `<span class="eng">${(sources || []).map(s => `<u class="${esc(s)}">${esc(s)}</u>`).join('')}</span>`;
function head(id) {
  const L = layer(id), nav = ['L1', 'L2', 'L3', 'L4', 'L5'].includes(id) && S.runs.length ? `<div class="msgnav"><button data-msg="-1" ${S.cur <= 0 ? 'disabled' : ''} aria-label="Previous message">‹</button>message <b>${S.cur + 1}</b> of <b>${S.runs.length}</b><button data-msg="1" ${S.cur >= S.runs.length - 1 ? 'disabled' : ''} aria-label="Next message">›</button></div>` : '<div></div>';
  return `<div class="sh"><div class="big">${id.slice(1).padStart(2, '0')}</div><div><h1>${esc(L.name)}</h1><p class="job">${esc(L.job)}</p><div class="promise"><span>✓</span>${esc(L.promise)}</div></div>${nav}</div>`;
}
function evidence(id) {
  const L = layer(id), t = S.tests[id], i = ORDER.indexOf(id), nx = ORDER[i + 1], pv = ORDER[i - 1];
  const can = S.content.suites[id];
  const tests = can ? `<button class="btn" data-tests="${id}" ${t && t.running ? 'disabled' : ''}>${t && t.running ? '<span class="spin"></span> Running…' : 'Run this layer\'s tests live'}</button>` : '';
  const out = t && !t.running ? `<div class="testout">${t.results.map(r => `<div><span class="${r.ok ? 'p' : 'bad'}">${r.ok ? '✓' : '✗'}</span> ${esc(r.module)}: <b>${r.passed}</b> passed, ${r.failed} failed${r.skipped ? `, ${r.skipped} skipped (need Postgres)` : ''} <span class="dimmer">${r.secs}s</span></div>`).join('')}</div>` : '';
  const lab = k => k === 'measured' ? 'measured live / in a run' : k === 'recorded' ? 'last recorded run' : '';
  return `<div class="evidence"><h2>Evidence · ${esc(L.id)} ${tests}</h2>
    <div class="tiles">${L.numbers.map(n => `<div class="tile ${n.kind}" title="${esc(lab(n.kind))}"><div class="v">${esc(n.value)}</div><div class="l">${esc(n.label)}</div><div class="n">${esc(n.note)}</div></div>`).join('')}</div>${out}
    <div class="limits"><h4>Known limits (we state them before you ask)</h4><ul>${L.limits.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>
    <div class="dimmer" style="margin-top:10px;font-size:12px">Code: ${L.files.map(f => `<span class="mono">${esc(f)}</span>`).join(' · ')}</div>
    <div class="next">${pv ? `<button class="btn ghost" data-go="${pv}">← ${pv === 'OV' ? 'Overview' : esc(layer(pv).id + ' ' + layer(pv).name)}</button>` : '<span></span>'}${nx ? `<button class="btn pri" data-go="${nx}">Next: ${esc(layer(nx).id + ' ' + layer(nx).name)} →</button>` : ''}</div></div>`;
}
async function runTests(id) {
  S.tests[id] = { running: true }; render();
  try { const r = await api('/api/tests/' + id, {}); S.tests[id] = r; renderRail(); toast(`${id}: ${r.passed} passed, ${r.failed} failed`); }
  catch (e) { S.tests[id] = { ok: false, results: [{ module: id, passed: 0, failed: 1, ok: false, secs: 0 }] }; toast('Test run failed: ' + e.message, 5000); }
  render();
}
const noMsg = (what) => `<div class="empty"><b>Send one message to see ${what}</b>Every layer below shows its own slice of the same message. Pick a scenario in the bar underneath, or type your own (fake) details.
  <div class="row" style="justify-content:center;margin-top:14px"><button class="btn pri" data-scn="0">Send an example</button><button class="btn" data-scn="2">Spoken digits across two turns</button><button class="btn" data-scn="3">Hindi</button></div></div>`;

/* ================================================================== STAGES */
const STAGES = {};

/* ---------------- Overview */
STAGES.OV = { html() {
  const P = S.content.problem, L = id => layer(id);
  const card = id => { const l = L(id), n = l.numbers[0], t = S.tests[id]; return `<button class="lc" data-go="${id}"><span class="id">${id}</span>${t && t.ok ? `<span class="tag ok tb">${t.passed} ✓</span>` : ''}<b>${esc(l.name)}</b><small>${esc(n.value)} ${esc(n.label)}</small></button>`; };
  const sep = '<span class="lsep">→</span>';
  return `<div class="hero"><div class="tag acc" style="margin-bottom:12px">Agentathon · TE-03</div><h1>${esc(P.title)}</h1><p>${esc(P.pitch)}</p>
    <div class="cta"><button class="btn pri lg" data-go="L8">▶ Run the full story (about 25 s)</button><button class="btn lg" data-go="L1">Walk the layers one by one →</button></div></div>
    <div class="lanes">
      <div class="lane"><div class="nm"><b>Measure</b>how we know</div><div class="lcards">${card('L0')}${sep}<span class="dimmer" style="align-self:center;font-size:12.5px">fake conversations with known PII positions; every score in this console is against them</span></div></div>
      <div class="lane"><div class="nm"><b>Protect</b>before any LLM</div><div class="lcards">${card('L1')}${sep}${card('L2')}${sep}${card('L3')}${sep}<div class="llm">LLM sees<br><b>tokens only</b></div></div></div>
      <div class="lane"><div class="nm"><b>Store</b>only with consent</div><div class="lcards">${card('L4')}${sep}${card('L5')}</div></div>
      <div class="lane"><div class="nm"><b>Forget</b>on request</div><div class="lcards">${card('L6')}</div></div>
      <div class="lane"><div class="nm"><b>Prove</b>and show it</div><div class="lcards">${card('L7')}${sep}${card('L8')}</div></div>
    </div>
    <div class="audbar"><b>Layer 7</b> every step above writes an event to one hash-chained, tamper-evident audit log. Borrowers appear only as keyed hashes.</div>
    <div class="truth" style="margin-top:18px">
      <div class="card"><h3>Measured</h3><ul><li>97.0% of PII spans fully masked (3,750-row test split)</li><li>p95 entry to LLM 17.5 ms against a 50 ms budget</li><li>Audit net: leak 52.4% to 16.1% when the detector misses half</li><li>${S.content.suites ? Object.values(S.content.suites).length : 8} layer test suites you can run from here</li></ul></div>
      <div class="card"><h3>Assumed or mocked</h3><ul><li>All data synthetic: scores are optimistic</li><li>Erasure stores and embeddings are mocks</li><li>Marathi as 5th language is our guess</li><li>Consent table is our reading of purpose limitation</li></ul></div>
      <div class="card"><h3>Not built</h3><ul><li>Real consent registry</li><li>Write-once anchor for the audit head hash</li><li>Real KMS and real store layouts</li><li>Native-speaker review of word lists</li></ul></div></div>`;
} };

/* ---------------- L0 */
STAGES.L0 = { html() {
  const T = S.content.corpus.targets || { dev: 17500, test: 3750, final: 3750 }, tot = T.dev + T.test + T.final;
  const langs = ['', 'en', 'hi', 'hi_rom', 'mr', 'mr_rom', 'kn', 'kn_rom', 'ta', 'ta_rom'];
  const sm = S.sample;
  return head('L0') + `<div class="grid g21"><div class="card"><h3>Look inside the data <span class="tag">DEV split only</span></h3>
    <div class="row" style="margin-bottom:12px">${langs.map(l => `<button class="pill ${S.sampleLang === l ? '' : ''}" data-lang="${l}" style="${S.sampleLang === l ? 'border-color:var(--accent);color:var(--fg)' : ''}">${l || 'any'}</button>`).join('')}<button class="btn" id="newsample" style="margin-left:auto">New sample</button></div>
    ${sm ? `<div class="dimmer" style="font-size:12px;margin-bottom:6px">${esc(sm.lang)} · ${esc(sm.scenario)} · ${esc(sm.channel)} · ${esc(sm.form)} · highlights = gold labels we score against</div><div class="hl" style="font-size:15px;max-height:300px;overflow:auto">${hl(sm.text, sm.spans)}</div><div class="legend" style="margin-top:10px">${legendFor(sm.spans)}</div>` : '<div class="dimmer">Loading a sample…</div>'}</div>
    <div class="card"><h3>Splits</h3><div style="display:flex;height:34px;border-radius:9px;overflow:hidden;border:1px solid var(--line2);margin:6px 0 10px">
      <div style="width:${T.dev / tot * 100}%;background:var(--accent-d);display:grid;place-items:center;font:600 11px var(--mono)">dev ${fmt(T.dev)}</div>
      <div style="width:${T.test / tot * 100}%;background:#1f6d4b;display:grid;place-items:center;font:600 11px var(--mono)">test</div>
      <div style="width:${T.final / tot * 100}%;background:#4a3a10;display:grid;place-items:center;font:600 11px var(--mono)">final</div></div>
      <p class="dim" style="margin:0 0 8px;font-size:13px">We tune on <b>dev</b>, report on <b>test</b>, and keep <b>final</b> sealed (${fmt(T.final)} rows, never looked at) for the end.</p>
      <p class="dim" style="margin:0;font-size:13px">Holdout is enforced in code: template zones and name-word zones keep dev and test apart (100% holdout-clean, zero name overlap).</p>
      <div class="legend" style="margin-top:14px"><span class="dimmer">Seed ${esc(S.content.corpus.seed)} · deterministic rebuild: <span class="mono">python -m layer0_data.build_corpus</span></span></div></div></div>` + evidence('L0');
}, after(el) {
  const load = async () => { try { S.sample = await api(`/api/data/sample?lang=${S.sampleLang}&seed=${Math.floor(Math.random() * 1e6) + 1}`); touch('L0'); renderRail(); if (S.stage === 'L0') { const keep = el.scrollTop; render(); el.scrollTop = keep; } } catch (e) { toast(e.message); } };
  if (!S.sample) load();
  $('#newsample', el).onclick = load;
  $$('[data-lang]', el).forEach(b => b.onclick = () => { S.sampleLang = b.dataset.lang; load(); });
} };

/* ---------------- L1 */
STAGES.L1 = { html() {
  const r = run(), rep = S.content.l1_report;
  let top;
  if (!r) top = noMsg('what the detector finds');
  else if (r.status === 'blocked' && !r.l1.spans.length) top = `<div class="banner bad"><span class="ic">⛔</span><div><b>Blocked before detection</b>${esc(r.reason)}. Layer 4 refused the purpose, so nothing was detected or sent.</div></div>`;
  else {
    const sp = r.l1.spans, cnt = k => sp.filter(s => (s.sources || []).includes(k)).length;
    top = `<div class="grid g21"><div class="card"><h3>The message, as the detector saw it <span class="tag">${r.l1.ms} ms</span>${r.l1.blind ? '<span class="tag bad">detector blind</span>' : ''}</h3>
      <div class="hl">${hl(r.text, sp)}</div><div class="legend" style="margin-top:12px">${legendFor(sp) || '<span class="dimmer">nothing detected</span>'}</div>
      ${sp.length ? `<table style="margin-top:14px"><tr><th>Type</th><th>Found</th><th>Engines</th><th>Becomes</th></tr>${sp.map(s => `<tr><td><span class="tok" style="--c:${col(s.label)}">${esc(s.label)}</span></td><td class="m">${esc(s.text)}${s.uncertain ? ' <span class="tag warn">uncertain: always masked</span>' : ''}</td><td>${engines(s.sources)}</td><td>${s.token ? tokHtml(s.token) : '<span class="dimmer">·</span>'}</td></tr>`).join('')}</table>` : ''}</div>
      <div class="card"><h3>Three engines, one verdict</h3>
        <div class="who"><div class="w"><div><b>Rules</b><small>Aadhaar / PAN / IFSC / UPI / email / phone patterns, checksums</small></div><span class="tag warn">${cnt('rules')}</span></div>
        <div class="w"><div><b>Spoken-number engine</b><small>"nine eight four…" in 5 languages, across turns</small></div><span class="tag acc">${cnt('spoken')}</span></div>
        <div class="w"><div><b>Chitragupta model</b><small>XLM-R large, int8 ONNX: names and context</small></div><span class="tag" style="border-color:#47337c;color:#c9b4ff">${cnt('model')}</span></div></div>
        <p class="dimmer" style="font-size:12px;margin:12px 0 0">Counts = spans each engine fired on for this message. Overlapping spans are merged; a span can be found by several engines.</p></div></div>`;
  }
  const board = rep ? `<div class="card" style="margin-top:16px"><h3>Scoreboard <span class="tag warn">${fmt(rep.rows)} test rows · synthetic · optimistic</span></h3><div class="grid g2">
      <div><div class="dimmer" style="font-size:11px;letter-spacing:1.2px;text-transform:uppercase;margin-bottom:8px">Fully masked, by PII type</div><div class="bars">${rep.types.map(t => `<div class="bar"><span>${esc(t.name)}</span><span class="tr"><span class="fi ${t.full < 95 ? 'low' : ''}" data-w="${t.full}"></span></span><span class="vv">${t.full}%</span></div>`).join('')}</div></div>
      <div><div class="dimmer" style="font-size:11px;letter-spacing:1.2px;text-transform:uppercase;margin-bottom:8px">Fully masked, by language variant</div><div class="bars">${rep.langs.map(t => `<div class="bar"><span>${esc(t.name)}</span><span class="tr"><span class="fi ${t.full < 95 ? 'low' : ''}" data-w="${t.full}"></span></span><span class="vv">${t.full}%</span></div>`).join('')}</div></div></div>
      <p class="dimmer" style="margin:12px 0 0;font-size:12.5px">Headline: <b class="ok">${rep.full}%</b> fully masked (≥95% of the span), ${rep.masked50}% at least half masked, right type label ${rep.label}%${rep.false_alarm ? `, false alarms on PII-free calls ${rep.false_alarm.pct}% (${rep.false_alarm.n}/${rep.false_alarm.of})` : ''}. Amber = under 95%. Names are the weakest type. <span class="warn">${esc(rep.caveat)}.</span> Parsed live from <span class="mono">LAYER1_REPORT.md</span>.</p></div>` : '';
  return head('L1') + top + board + evidence('L1');
}, after(el) { requestAnimationFrame(() => $$('.fi[data-w]', el).forEach(f => f.style.width = f.dataset.w + '%')); } };

/* ---------------- L2 */
STAGES.L2 = { html() {
  const r = run();
  if (!r) return head('L2') + noMsg('what the vault stores') + evidence('L2');
  const mine = new Set(r.l2.tokens_this_message), ents = r.l2.entries, key = r.l2.keys[0];
  return head('L2') + `<div class="grid g21"><div class="card"><h3>Vault rows for this borrower <span class="tag">${ents.length} entries</span><span class="tag ok">plaintext in the database: none</span></h3>
    ${ents.length ? `<table><tr><th>Token</th><th>Type</th><th>Ciphertext (AES-GCM) · nonce</th><th>Purpose</th></tr>${ents.map(e => `<tr style="${mine.has(e.token) ? 'background:rgba(91,140,255,.08)' : ''}"><td>${tokHtml(e.token)}</td><td class="m">${esc(e.label)}</td><td><span class="ct" title="${esc(e.ciphertext)}">${esc(e.ciphertext.slice(0, 44))}${e.ciphertext.length > 44 ? '…' : ''}</span><span class="m dimmer">nonce ${esc(e.nonce.slice(0, 12))}</span></td><td class="m">${esc(e.purpose)}</td></tr>`).join('')}</table>` : '<div class="dimmer">This message had nothing to store.</div>'}
    <p class="dimmer" style="font-size:12px;margin:10px 0 0">Highlighted rows = tokens issued by this message. This is read straight from the vault tables: ciphertext, nonce, key id, token. The value itself is not in the database.</p></div>
    <div><div class="card"><h3>This borrower's key</h3>${key ? `<dl class="kv"><dt>key id</dt><dd>${esc(key.key_id)}…</dd><dt>status</dt><dd class="ok">${esc(key.status)}</dd><dt>DEK / KEK version</dt><dd>v${key.dek_version} / v${key.kek_version}</dd><dt>times used</dt><dd>${key.uses}</dd></dl>
      <p class="dimmer" style="font-size:12px;margin:10px 0 0">One random key per borrower, wrapped by a master-derived key. <b>Erasure = destroy this key</b> (crypto-shredding): every copy of the ciphertext, in backups too, becomes noise.</p>` : '<div class="dimmer">No key yet: nothing has been tokenised.</div>'}</div>
      <div class="card" style="margin-top:16px"><h3>Who may read a value back?</h3><div class="who" id="who">${[['borrower_reply', 'Borrower reply path', 'the only legitimate rehydration'], ['agent_dashboard', 'Agent dashboard', 'sees tokens, never values'], ['log_pipeline', 'Log pipeline', 'tokens only, by design'], ['engineer', 'An engineer with DB access', 'ciphertext only, no key']].map(([k, n, s]) => `<div class="w" id="w-${k}"><div><b>${n}</b><small>${s}</small></div><button class="btn" data-role="${k}">Try to read</button></div>`).join('')}</div></div></div></div>` + evidence('L2');
}, after(el) {
  const r = run(); if (!r) return;
  $$('[data-role]', el).forEach(b => b.onclick = async () => {
    const tok = (r.l2.tokens_this_message[0]) || (r.l2.entries[0] || {}).token; if (!tok) return toast('No token yet');
    const res = await api('/api/vault/attempt', { token: tok, role: b.dataset.role }); const w = $('#w-' + b.dataset.role);
    w.className = 'w res ' + (res.allowed ? 'ok' : 'no'); w.querySelector('div').innerHTML = `<b>${esc(w.querySelector('b').textContent)}</b><small class="${res.allowed ? 'ok' : 'bad'}">${res.allowed ? '✓ ' : '✕ '}${esc(res.result)}${res.allowed ? ` (${res.length} chars)` : ''}</small>`;
    b.remove();
  });
} };

/* ---------------- L3 */
const NODES = [['initialize', 'Consent + validate', 'L4'], ['detect', 'Detect', 'L1'], ['tokenise', 'Tokenise + encrypt', 'L2'], ['final_audit', 'Independent audit', 'L3'], ['llm_dispatch', 'LLM (tokens only)', 'L3'], ['sanitise', 'Sanitise reply', 'L3'], ['rehydrate_route', 'Route reply / log', 'L3']];
STAGES.L3 = { html() {
  const r = run();
  const brk = `<div class="card" style="margin-bottom:16px"><h3>Break it on purpose</h3><div class="row"><button class="btn" data-scn="9">Detector blind · phone + email</button><button class="btn" data-scn="10">Detector blind · a name</button><button class="btn" data-scn="11">Wrong purpose · model_improvement</button><button class="btn" data-scn="2">Spoken digits across 2 turns</button></div>
    <p class="dimmer" style="font-size:12.5px;margin:10px 0 0">"Detector blind" makes Layer 1 return nothing, as if it missed everything. Watch whether the independent audit net saves the message, and what it cannot save.</p></div>`;
  if (!r) return head('L3') + brk + noMsg('the gateway run') + evidence('L3');
  const l3 = r.l3, tr = r.l3.trace, blocked = r.status === 'blocked';
  const nodes = NODES.map(([k, n, ly]) => `<div class="node" data-n="${k}"><span class="ly">${ly}</span><b>${n}</b><small>${k === 'detect' && l3.stage_ms.detect != null ? l3.stage_ms.detect + ' ms' : k === 'tokenise' && l3.stage_ms.tokenise != null ? l3.stage_ms.tokenise + ' ms' : '&nbsp;'}</small></div>`);
  const flow = nodes.map((n, i) => n + (i < nodes.length - 1 ? '<span class="arr">›</span>' : '')).join('') + (blocked ? '<span class="arr">›</span><div class="node badn" data-n="block"><span class="ly">STOP</span><b>BLOCK</b><small>fail closed</small></div>' : '');
  let banner = '';
  if (blocked) banner = `<div class="banner bad"><span class="ic">⛔</span><div><b>Blocked: ${esc(r.reason)}</b>Nothing was detected, tokenised or sent to the LLM. ${String(r.reason).startsWith('consent') ? 'Layer 4 refused this purpose for the transcript log (default deny).' : 'The gateway fails closed.'}</div></div>`;
  else if (l3.escaped && l3.escaped.length) banner = `<div class="banner bad"><span class="ic">⚠</span><div><b>Escaped to the LLM: ${l3.escaped.map(e => esc(e.text) + ' (' + esc(e.label) + ')').join(', ')}</b>The detector was blind and the audit net cannot find names (no regex can). This is the documented limit, and exactly why Layer 1 quality on names matters. ${l3.net_found ? `The net did catch ${l3.net_found} other value(s).` : ''}</div></div>`;
  else if (r.l1.blind) banner = `<div class="banner ok"><span class="ic">🛡</span><div><b>The detector was blind. The audit net saved it.</b>The independent re-scan found ${l3.net_found} value(s) the detector missed, re-masked them (retry ${l3.retries}), and only then let the message through. Nothing raw reached the LLM.</div></div>`;
  else if (l3.retries) banner = `<div class="banner warn"><span class="ic">↻</span><div><b>Audit retry ${l3.retries}</b>The audit found something and re-masked it before dispatch.</div></div>`;
  const a = l3.audit, pre = l3.pre_llm_ms, over = pre != null && pre > 50;
  const chk = a ? [['Digit tripwire (6+ digits, split too)', a.digit_tripwire], ['Spoken-number tripwire', a.spoken_tripwire], ['Survival: a known value still visible?', a.survival], ['Second pattern family (PAN/IFSC/UPI/email)', a.family2], ['Layer-1 rules re-scan', a.rules_rescan]] : [];
  const lk = !!(l3.escaped && l3.escaped.length);
  const views = blocked ? '' : `<div class="three"><div class="view ${lk ? 'leak' : 'safe'}"><h4><span>① What the LLM saw</span>${lk ? '<span class="tag bad">raw value reached the LLM</span>' : '<span class="tag ok">no raw values</span>'}</h4><div class="masked">${tokHtml(l3.llm_saw, l3.escaped)}</div></div>
      <div class="view safe"><h4><span>② What the log stores</span><span class="tag ok">tokens only</span></h4><div class="masked">${tokHtml(l3.log_entry || '')}</div></div>
      <div class="view"><h4><span>③ What the borrower sees</span><span class="tag acc">rehydrated</span></h4><div class="masked">${esc(l3.reply_borrower || '')}</div></div></div>
      <p class="dimmer" style="font-size:12px;margin:10px 0 0">${l3.llm_live ? 'Reply from <b>Nova Pro</b> via Bedrock (tokenised text only).' : 'Reply from a local stub (switch "LLM" in the header for a live Nova Pro call).'} The borrower's copy is rehydrated from the vault for the borrower only; it is never logged.</p>`;
  return head('L3') + banner + `<div class="card"><h3>Gateway graph, this message <span class="tag">${l3.ms} ms total</span>${tr.filter(x => x === 'tokenise').length > 1 ? '<span class="tag warn">retry loop used</span>' : ''}</h3><div class="flow" id="flow">${flow}</div></div>
    <div class="card" style="margin-top:16px"><h3>Input → output <span class="dimmer tag">typed (raw, demo only):</span><span class="mono" style="text-transform:none;letter-spacing:0;color:var(--fg2)">${esc(r.text)}</span></h3>${views || '<div class="dimmer">Nothing was sent to the LLM.</div>'}</div>
    <div class="grid g2" style="margin-top:16px"><div class="card"><h3>Independent audit, in the final pass</h3>${a ? `<div class="chk">${chk.map(([n, v]) => `<div>${n}</div><div class="r ${v ? 'bad' : 'ok'}">${v ? v + ' found' : '0 ✓'}</div>`).join('')}<div>Utility (over-masking): ${(a.utility_share * 100).toFixed(0)}% placeholders</div><div class="r ${a.utility_ok ? 'ok' : 'bad'}">${a.utility_ok ? 'ok ✓' : 'blocked'}</div></div><p class="dimmer" style="font-size:12px;margin:10px 0 0">Counts are for the final pass. A retry, if any, is shown in the graph above.</p>` : '<div class="dimmer">The audit did not run (blocked earlier).</div>'}</div>
      <div class="card"><h3>Latency budget</h3>${pre != null ? `<div style="display:flex;justify-content:space-between;font-size:13px"><span>entry → LLM boundary</span><b class="mono ${over ? 'bad' : 'ok'}">${pre} ms</b></div><div class="budget"><i class="${over ? 'over' : ''}" style="width:${Math.min(100, pre / 50 * 100)}%"></i></div><div class="dimmer" style="font-size:12px">Budget 50 ms. Measured over 1,711 real messages: p50 12-14 ms, p95 17.5 ms. A single live message is noisier than the benchmark; over budget at 8+ concurrent clients is a documented limit.</div>` : '<div class="dimmer">n/a</div>'}
        ${l3.warnings.length ? `<div class="banner warn" style="margin:12px 0 0"><span class="ic">!</span><div>${l3.warnings.map(esc).join(', ')}</div></div>` : ''}</div></div><div style="margin-top:16px">${brk}</div>` + evidence('L3');
}, after(el) {
  const r = run(); if (!r) return; const gen = ++S.flowGen, tr = r.l3.trace, seen = {};
  (async () => { for (const k of tr) { if (gen !== S.flowGen) return; const n = $(`[data-n="${k}"]`, el); if (n) { seen[k] = (seen[k] || 0) + 1; n.classList.add('lit'); if (seen[k] > 1) { n.classList.remove('lit'); n.classList.add('rt'); n.querySelector('small').textContent = 'retry ×' + (seen[k] - 1); } } await sleep(130); } })();
} };

/* ---------------- L4 */
STAGES.L4 = { html() {
  const m = S.cm, r = run(), mx = S.matrix;
  const cell = (st, p) => { const c = mx.cells.find(x => x.store === st && x.purpose === p); const t = { allow: 'ALLOW', explicit_consent: 'CONSENT', deny: 'DENY' }[c.rule]; return `<button class="mc ${c.rule} ${m.store === st && m.purpose === p ? 'sel' : ''}" data-cell="${st}|${p}" title="${st} ← ${p}: ${c.rule}">${t}</button>`; };
  const grid = `<div class="mx" style="grid-template-columns:112px repeat(${mx.purposes.length},minmax(0,1fr))"><span></span>${mx.purposes.map(p => `<span class="h">${p.replace('_', ' ')}</span>`).join('')}${mx.stores.map(st => `<span class="rn">${st}</span>${mx.purposes.map(p => cell(st, p)).join('')}`).join('')}</div>`;
  const needs = (S.cm.store === 'eval_sets' && S.cm.purpose === 'model_improvement') || (S.cm.store === 'vector_store' && S.cm.purpose === 'model_improvement');
  const msg = run() ? `<div class="banner ${run().l4.gateway_decision === 'denied' ? 'bad' : 'ok'}" style="margin-bottom:14px"><span class="ic">${run().l4.gateway_decision === 'denied' ? '⛔' : '✓'}</span><div><b>Message ${run().turn}: purpose <span class="mono">${esc(run().l4.purpose)}</span> → ${run().l4.gateway_decision === 'denied' ? 'denied at the gateway' : 'allowed for the transcript log'}</b>${run().l4.writes.map(w => `${esc(w.store)}: ${w.allowed ? '<span class="ok">written</span>' : '<span class="bad">rejected</span>'}`).join(' · ') || 'No store write (message blocked).'}</div></div>` : '';
  return head('L4') + msg + `<div class="grid g21"><div class="card"><h3>Which purpose may feed which store <span class="tag">default deny</span></h3>${grid}
      <div class="legend" style="margin-top:12px"><span><i style="--c:var(--ok)"></i>allow</span><span><i style="--c:var(--warn)"></i>needs explicit consent + consent id</span><span><i style="--c:var(--fg3)"></i>deny (also: any store not listed)</span></div>
      <p class="dimmer" style="font-size:12px;margin:10px 0 0">Click a cell, then attempt a real write. <span class="warn">This table is our reading of purpose limitation, not legal advice.</span></p></div>
    <div class="card"><h3>Attempt a write</h3><div class="grid" style="gap:10px">
      <label class="field">Store<select id="cs">${S.matrix.stores.map(s => `<option ${m.store === s ? 'selected' : ''}>${s}</option>`).join('')}</select></label>
      <label class="field">Purpose<select id="cp">${S.matrix.purposes.map(s => `<option ${m.purpose === s ? 'selected' : ''}>${s}</option>`).join('')}</select></label>
      <label class="field" style="grid-auto-flow:column;grid-template-columns:auto 1fr;align-items:center;gap:10px;color:var(--fg2)"><input type="checkbox" id="cx" ${m.explicit ? 'checked' : ''}> Borrower gave explicit consent for this purpose</label>
      <button class="btn pri" id="try">Attempt write (real guarded_write)</button></div>
      <div id="tryres"></div><div class="dimmer" style="font-size:12px;margin-top:10px">Writes the masked text to a mock store and an event to the audit chain. Rejected writes never store a body, only ids and the reason.</div></div></div>
    <div class="card" style="margin-top:16px"><h3>Attempts this session <span class="tag">${S.attempts.length}</span></h3>${S.attempts.length ? `<table><tr><th></th><th>Store ← purpose</th><th>Reason (from the audit event)</th><th>Chain hash</th></tr>${S.attempts.slice().reverse().map(a => `<tr><td class="${a.allowed ? 'ok' : 'bad'}">${a.allowed ? '✓' : '✕'}</td><td class="m">${esc(a.store)} ← ${esc(a.purpose)}${a.explicit ? ' + consent' : ''}</td><td class="m">${esc(a.reason)}</td><td class="m dimmer">${esc(a.hash)}</td></tr>`).join('')}</table>` : '<div class="dimmer">None yet. Try <b>eval_sets ← service</b>: the one the brief is about.</div>'}</div>` + evidence('L4');
}, after(el) {
  $$('[data-cell]', el).forEach(b => b.onclick = () => { const [st, p] = b.dataset.cell.split('|'); S.cm = { store: st, purpose: p, explicit: S.cm.explicit }; render(); });
  $('#cs', el).onchange = e => { S.cm.store = e.target.value; render(); }; $('#cp', el).onchange = e => { S.cm.purpose = e.target.value; render(); };
  $('#cx', el).onchange = e => { S.cm.explicit = e.target.checked; };
  $('#try', el).onclick = async () => {
    try {
      const body = { store: S.cm.store, purpose: S.cm.purpose, explicit: !!S.cm.explicit, consent_id: S.cm.explicit ? 'CN-DEMO-1' : null };
      const r = await api('/api/consent/try', body); touch('L4', 'L7');
      S.attempts.push({ ...body, allowed: r.allowed, reason: r.reason, hash: r.event.hash }); renderRail(); render();
      const out = $('#tryres'); if (out) out.innerHTML = `<div class="res-card ${r.allowed ? 'ok' : 'no'}"><b class="${r.allowed ? 'ok' : 'bad'}">${r.allowed ? '✓ WRITE ALLOWED' : '✕ WRITE REJECTED'}</b><div class="m dim" style="margin-top:4px">${esc(r.reason)}</div></div>`;
      await refreshStatus();
    } catch (e) { toast(e.message, 4000); }
  };
} };

/* ---------------- L5 */
STAGES.L5 = { html() {
  const d = S.lin;
  if (d && !d.graph.nodes.length && S.erasure) return head('L5') + `<div class="banner ok"><span class="ic">✓</span><div><b>The registry has forgotten this borrower.</b>After erasure the lineage registry drops the borrower and leaves no index residue (Layer 6 calls <span class="mono">forget_borrower</span>). The map you saw before erasure is in the story and in the audit proof.</div></div>` + evidence('L5');
  if (!d || !d.graph.nodes.length) return head('L5') + noMsg('the lineage map') + evidence('L5');
  const known = d.graph.nodes.filter(n => ['log', 'chunk', 'eval'].includes(n.kind)).length, stores = d.stores, unreg = stores.reduce((a, s) => a + s.unregistered_records, 0), total = stores.reduce((a, s) => a + s.count, 0);   // embeddings live inside vector records, so they are not counted twice
  return head('L5') + `<div class="grid g21"><div class="card"><h3>Where this borrower's data went <span class="tag">registry</span></h3><div class="lg-wrap">${lineageSvg(d)}</div>
      <div class="legend" style="margin-top:10px">${[['log', '#7fb2ff'], ['chunk', '#a98bf0'], ['emb', '#3cc4d0'], ['eval', '#f0a25a']].map(([k, c]) => `<span><i style="--c:${c}"></i>${k}</span>`).join('')}<span><i style="--c:var(--bad)"></i>in a store, NOT in the registry</span></div></div>
    <div><div class="card"><h3>Registry vs reality</h3><div class="delta"><span>Records the registry knows (logs, chunks, evals)</span><span></span><span></span><span class="z" style="color:var(--fg)">${known}</span>
      <span>Records actually in the stores</span><span></span><span></span><span class="z" style="color:var(--fg)">${total}</span>
      <span>Records nobody registered</span><span></span><span></span><span class="z" style="color:${unreg ? 'var(--bad)' : 'var(--ok)'}">${unreg}</span></div>
      <p class="dimmer" style="font-size:12px;margin:12px 0 0">The registry maps what the platform knew it wrote. It cannot know about a debug log or a derived summary someone else created. That gap is why Layer 6 has to <i>search</i>, not just look things up.</p>
      <div class="row" style="margin-top:12px"><button class="btn danger" id="plant" ${S.status.planted || S.status.erased ? 'disabled' : ''}>${S.status.planted ? '✓ Hidden copies planted' : 'Plant 3 hidden copies (simulates the real-world mess)'}</button></div></div>
      <div class="card" style="margin-top:16px"><h3>Stores</h3><table><tr><th>Store</th><th>Records</th><th>Unregistered</th></tr>${stores.map(s => `<tr><td class="m">${esc(s.store)}${s.registered_store ? '' : ' <span class="tag bad">unmapped</span>'}</td><td class="m">${s.count}</td><td class="m ${s.unregistered_records ? 'bad' : ''}">${s.unregistered_records}</td></tr>`).join('')}</table></div></div></div>` + evidence('L5');
}, after(el) { const b = $('#plant', el); if (b) b.onclick = async () => { try { await api('/api/plant', {}); await refreshStatus(); S.lin = await api('/api/lineage'); touch('L5'); renderRail(); render(); toast('Planted: a derived summary, a debug log with raw values, and a crash dump in an unregistered folder'); } catch (e) { toast(e.message, 4500); } }; } };
function lineageSvg(d) {
  const colx = { borrower: 70, conversation: 215, log: 380, chunk: 560, eval: 560, emb: 740 }, W = 860, kindc = { log: '#7fb2ff', chunk: '#a98bf0', emb: '#3cc4d0', eval: '#f0a25a' };
  const nodes = d.graph.nodes.filter(n => n.kind in kindc), pos = {}, cnt = { log: 0, chunk: 0, eval: 0, emb: 0 }; const rowh = 30, y0 = 54;
  const logs = nodes.filter(n => n.kind === 'log'), chunks = nodes.filter(n => n.kind === 'chunk'), evals = nodes.filter(n => n.kind === 'eval'), embs = nodes.filter(n => n.kind === 'emb');
  logs.forEach((n, i) => pos[n.id] = [colx.log, y0 + i * rowh]); chunks.forEach((n, i) => pos[n.id] = [colx.chunk, y0 + i * rowh]); evals.forEach((n, i) => pos[n.id] = [colx.chunk, y0 + (chunks.length + 1 + i) * rowh]);
  embs.forEach(n => { const p = d.graph.edges.find(e => e.to === n.id); const pp = p && pos[p.from]; pos[n.id] = [colx.emb, pp ? pp[1] : y0]; });
  const rows = Math.max(logs.length, chunks.length + evals.length + 1, 3), mid = y0 + (rows - 1) * rowh / 2;
  const orphans = []; d.stores.forEach(s => s.records.forEach(r => { if (!r.in_registry) orphans.push({ store: s.store, id: r.id }); }));
  const oy = y0 + rows * rowh + 40, H = oy + (orphans.length ? 38 + Math.ceil(orphans.length / 3) * 32 : 14);
  let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" style="min-width:700px;display:block">`;
  const line = (x1, y1, x2, y2, c = '#2f394a', dash = '') => `<path d="M${x1},${y1} C${(x1 + x2) / 2},${y1} ${(x1 + x2) / 2},${y2} ${x2},${y2}" stroke="${c}" stroke-width="1.4" fill="none" ${dash}/>`;
  svg += `<text x="${colx.borrower}" y="26" fill="#6f7a8c" font-size="10" text-anchor="middle" letter-spacing="1">BORROWER</text><text x="${colx.conversation}" y="26" fill="#6f7a8c" font-size="10" text-anchor="middle" letter-spacing="1">CONVERSATION</text><text x="${colx.log + 40}" y="26" fill="#6f7a8c" font-size="10" text-anchor="middle" letter-spacing="1">LOGS</text><text x="${colx.chunk + 40}" y="26" fill="#6f7a8c" font-size="10" text-anchor="middle" letter-spacing="1">CHUNKS · EVALS</text><text x="${colx.emb + 40}" y="26" fill="#6f7a8c" font-size="10" text-anchor="middle" letter-spacing="1">EMBEDDINGS</text>`;
  svg += line(colx.borrower + 38, mid, colx.conversation - 44, mid);
  [...logs, ...chunks, ...evals].forEach(n => { const p = pos[n.id]; svg += line(colx.conversation + 44, mid, p[0], p[1] + 0, '#243049'); });
  d.graph.edges.forEach(e => { const a = pos[e.from], b = pos[e.to]; if (a && b) svg += line(a[0] + 82, a[1], b[0], b[1], '#3a4a6b'); });
  svg += `<rect x="${colx.borrower - 38}" y="${mid - 17}" width="76" height="34" rx="9" fill="#161b24" stroke="#5b8cff"/><text x="${colx.borrower}" y="${mid + 4}" fill="#e9edf3" font-size="11" text-anchor="middle">borrower</text>`;
  svg += `<rect x="${colx.conversation - 44}" y="${mid - 17}" width="88" height="34" rx="9" fill="#161b24" stroke="#2f394a"/><text x="${colx.conversation}" y="${mid + 4}" fill="#a7b1c0" font-size="11" text-anchor="middle">conversation</text>`;
  nodes.forEach(n => { const p = pos[n.id]; if (!p) return; const c = kindc[n.kind]; svg += `<g><title>${esc(n.id)}${n.purpose ? ' · purpose ' + esc(n.purpose) : ''}</title><rect x="${p[0]}" y="${p[1] - 11}" width="82" height="22" rx="6" fill="${c}22" stroke="${c}"/><text x="${p[0] + 41}" y="${p[1] + 4}" fill="${c}" font-size="10" text-anchor="middle">${esc(n.label.slice(0, 12))}</text></g>`; });
  if (orphans.length) {
    svg += `<text x="20" y="${oy - 14}" fill="#ff8f8f" font-size="10" letter-spacing="1">FOUND IN STORES BUT NOT IN THE REGISTRY</text>`;
    orphans.forEach((o, i) => { const x = 20 + (i % 3) * 280, y = oy + Math.floor(i / 3) * 32; svg += `<g><rect x="${x}" y="${y - 12}" width="262" height="24" rx="6" fill="#ff6b6b18" stroke="#ff6b6b" stroke-dasharray="4 3"/><text x="${x + 10}" y="${y + 4}" fill="#ffb3b3" font-size="10">${esc((o.store.replace('unmapped:', '') + ' · ' + o.id).slice(0, 40))}</text></g>`; });
  }
  return svg + '</svg>';
}

/* ---------------- L6 */
const ESTEPS = [['discover', 'Discover', 'Looks everywhere: registry, conversation ids, sealed raw-value matching, embedding neighbours, unregistered files'], ['plan', 'Plan', 'Deterministic delete plan (eval sets, then vectors, then logs, then the rest)'], ['delete', 'Delete', 'Removes every record that has certain or probabilistic evidence'], ['verify', 'Verify', 'Re-runs ALL discovery from scratch; loops back if anything survives'], ['shred', 'Shred the key', 'Destroys the borrower key only after verification (the sweep needs the vault until then)'], ['audit', 'Audit', 'Writes a hash-chained erasure record: counts and a keyed hash, never raw values']];
STAGES.L6 = { html() {
  const d = S.lin, er = S.erasure;
  if (!S.runs.length && !er) return head('L6') + noMsg('what needs erasing') + evidence('L6');
  const stores = d ? d.stores : [];
  const steps = `<div class="steps" id="esteps">${ESTEPS.map(([k, n, s], i) => `<div class="step" data-e="${k}"><span class="ix">${i + 1}</span><div><b>${n}</b><small>${esc(s)}</small></div></div>`).join('')}</div>`;
  const res = er ? `<div class="big-status ${er.clean ? '' : 'bad'}"><div><div class="dimmer" style="font-size:11px;letter-spacing:1.4px;text-transform:uppercase">Agent status</div><div class="v ${er.clean ? 'ok' : 'bad'}">${esc(er.status)}</div></div><div style="margin-left:auto;text-align:right"><div class="dimmer" style="font-size:11px;letter-spacing:1.4px;text-transform:uppercase">Took</div><div class="v mono">${er.secs}s</div></div></div>
    <div class="card" style="margin-top:14px"><h3>Before → after</h3><div class="delta">${Object.keys(er.stores_before).map(k => `<span class="m">${esc(k)}</span><span class="a">${er.stores_before[k]}</span><span class="dimmer">→</span><span class="z">${er.stores_after[k] ?? 0}</span>`).join('')}
      <span>Vault entries</span><span class="a">${er.vault_before}</span><span class="dimmer">→</span><span class="z">${er.vault_after}</span>
      <span><b>Independent file scan: raw values left</b></span><span class="a">${er.raw_before}</span><span class="dimmer">→</span><span class="z">${er.raw_after}</span></div>
      <dl class="kv" style="margin-top:12px"><dt>borrower key present</dt><dd class="${er.key_present ? 'bad' : 'ok'}">${er.key_present ? 'YES' : 'no: destroyed'}</dd><dt>read a former token</dt><dd>${esc(er.probe)}</dd><dt>methods that found copies</dt><dd>${er.record.methods.map(m => `<span class="tag acc">${esc(m)}</span>`).join(' ')}</dd><dt>found in unregistered stores</dt><dd>${(er.record.unregistered_stores_with_hits || []).map(esc).join(', ') || 'none'}</dd></dl></div>` : `<div class="empty"><b>Nothing erased yet</b>Plant the hidden copies, then press Erase. The agent is never told where they are.</div>`;
  return head('L6') + `<div class="banner warn"><span class="ic">!</span><div><b>Mock stores.</b> The real store layout was not provided, so logs, vectors and eval sets are JSONL files we generate. Only the adapters change on real stores; "every copy erased" is unproven on real data.</div></div>
    <div class="grid g12"><div><div class="card"><h3>Stores for this borrower</h3><table><tr><th>Store</th><th>Records</th><th>Unregistered</th></tr>${stores.map(s => `<tr><td class="m">${esc(s.store)}${s.registered_store ? '' : ' <span class="tag bad">unmapped</span>'}</td><td class="m">${s.count}</td><td class="m ${s.unregistered_records ? 'bad' : ''}">${s.unregistered_records}</td></tr>`).join('') || '<tr><td class="dimmer">none</td></tr>'}</table>
      <div class="row" style="margin-top:14px"><button class="btn danger" id="plant" ${S.status.planted || S.status.erased ? 'disabled' : ''}>${S.status.planted ? '✓ 1 Hidden copies planted' : '1 · Plant hidden copies'}</button><button class="btn pri" id="erase" ${S.status.erased || !S.runs.length ? 'disabled' : ''}>2 · Erase this borrower</button></div></div>
      <div class="card" style="margin-top:16px"><h3>The agent (LangGraph)</h3>${steps}<p class="dimmer" style="font-size:12px;margin:10px 0 0">No LLM in this layer, by decision. It is blind to raw values: it asks the vault yes/no questions.</p></div></div>
    <div>${res}${er ? `<div class="card" style="margin-top:14px"><h3>Agent trace (ids and counts only)</h3><div class="mono dim" style="font-size:12px;line-height:1.7">${er.trace.map(t => '• ' + esc(t)).join('<br>')}</div></div>` : ''}</div></div>` + evidence('L6');
}, after(el) {
  const pb = $('#plant', el), eb = $('#erase', el), er = S.erasure;
  if (er) $$('[data-e]', el).forEach(n => n.classList.add('lit'));
  if (pb) pb.onclick = async () => { try { await api('/api/plant', {}); await refreshStatus(); S.lin = await api('/api/lineage'); touch('L5', 'L6'); renderRail(); render(); toast('Planted 3 hidden copies. The registry does not know about them.'); } catch (e) { toast(e.message, 4500); } };
  if (eb) eb.onclick = async () => {
    eb.disabled = true; eb.innerHTML = '<span class="spin"></span> Erasing…';
    try {
      const r = await api('/api/erase', {}); S.erasure = r; touch('L6', 'L7', 'L2', 'L5');
      render(); const steps = $$('[data-e]'); const hit = k => r.trace.some(t => t.startsWith(k));
      for (const s of steps) { if (hit(s.dataset.e)) { s.classList.add('lit'); await sleep(260); } }
      await refreshStatus(); S.lin = await api('/api/lineage'); renderRail(); toast(r.clean ? 'CLEAN: independent scan found no raw value, no vault entry, no key' : 'NOT clean: see counts', 4500);
    } catch (e) { toast(e.message, 5000); render(); }
  };
  if (!S.lin) api('/api/lineage').then(d => { S.lin = d; if (S.stage === 'L6') render(); });
} };

/* ---------------- L7 */
STAGES.L7 = { html() {
  const a = S.aud; if (!a) return head('L7') + '<div class="dimmer">Loading the chain…</div>' + evidence('L7');
  const win = a.records.slice(-14), tam = S.tamper, er = S.erasure;
  const blk = (r, cls = '') => `<div class="blk ${r.kind_name === 'erasure' ? 'er' : ''} ${cls}"><span class="sq">#${r.seq}</span><span class="nm" title="${esc(r.kind_name)}">${esc(r.kind_name)}</span><span class="pv">prev ${esc(r.prev)}</span><br><span class="hs">hash ${esc(r.hash.slice(0, 10))}</span></div>`;
  let tamRow = '';
  if (tam) { const rs = a.records.filter(r => r.seq >= tam.edited_seq - 2 && r.seq <= tam.edited_seq + 4); tamRow = `<div class="card" style="margin-top:16px"><h3>Tamper test <span class="tag bad">COPY only · the real log is untouched</span></h3>
      <div class="banner bad"><span class="ic">✕</span><div><b>BROKEN at record #${tam.detected_at}: ${esc(tam.reason)}</b>We changed <span class="mono">${esc(tam.field)}</span> in record #${tam.edited_seq} from <span class="mono">${tam.old}</span> to <span class="mono">${tam.new}</span> in a copy. Verifying the copy fails at that record; every record after it no longer chains. Real log intact: <span class="${tam.real_log_intact ? 'ok' : 'bad'}" style="font-weight:700">${tam.real_log_intact ? 'yes ✓' : 'NO'}</span>.</div></div>
      <div class="chain">${rs.map(r => blk(r, r.seq === tam.edited_seq ? 'bad' : r.seq > tam.edited_seq ? 'bad' : '')).join('')}</div></div>`; }
  return head('L7') + `<div class="card"><h3>The chain (latest ${win.length} of ${a.length}) <span class="tag ${a.chain.ok ? 'ok' : 'bad'}">${a.chain.ok ? 'chain INTACT' : 'BROKEN at #' + a.chain.bad_seq}</span><span class="tag">head ${esc(a.head)}…</span></h3>
    <div class="chain" id="chain">${win.length ? win.map(r => blk(r)).join('') : '<div class="dimmer">Empty. Send a message: every layer writes here.</div>'}</div>
    <div class="row"><button class="btn" id="verify">Verify chain</button><button class="btn danger" id="tamper" ${a.length < 3 ? 'disabled' : ''}>Tamper test (on a copy)</button><span class="dimmer" style="font-size:12.5px">hash<sub>i</sub> = SHA-256(hash<sub>i-1</sub> + record<sub>i</sub>). Edit, delete or reorder anything and every later hash breaks.</span></div><div id="vres"></div></div>${tamRow}
    <div class="grid g2" style="margin-top:16px"><div class="card"><h3>What is in each record</h3><table>${win.slice(-5).map(r => `<tr><td class="m dimmer">#${r.seq}</td><td class="m">${esc(r.kind_name)}</td><td class="m dimmer">${esc(['purpose', 'store', 'reason', 'status', 'latency_ms'].filter(k => r[k] !== undefined).map(k => k + '=' + r[k]).join(' '))}</td></tr>`).join('')}</table><p class="dimmer" style="font-size:12px;margin:8px 0 0">Ids, counts, small codes and a <b>keyed</b> borrower hash. No text, no values, no borrower id.</p></div>
    <div class="card"><h3>Erasure proof</h3>${er ? `<pre class="proof">${esc(er.proof)}</pre>` : '<div class="dimmer">No erasure yet. Run Layer 6 and the proof appears here, chained into this log.</div>'}</div></div>` + evidence('L7');
}, after(el) {
  api('/api/audit?limit=500').then(a => { const changed = !S.aud || S.aud.length !== a.length || S.aud.head !== a.head; S.aud = a; if (changed && S.stage === 'L7') { const keep = el.scrollTop; render(); el.scrollTop = keep; } });
  const ch = $('#chain', el); if (ch) ch.scrollLeft = ch.scrollWidth;
  const v = $('#verify', el), t = $('#tamper', el);
  if (v) v.onclick = async () => { const a = await api('/api/audit?limit=500'); S.aud = a; $('#vres', el).innerHTML = `<div class="res-card ${a.chain.ok ? 'ok' : 'no'}"><b class="${a.chain.ok ? 'ok' : 'bad'}">${a.chain.ok ? '✓ Chain intact' : '✕ Chain broken'}</b> <span class="m dim">${a.length} records · ${esc(a.chain.reason)} · head ${esc(a.head)}…</span><div class="dimmer" style="font-size:12px;margin-top:4px">To make this meaningful in production the head hash must be anchored somewhere write-once. Not built.</div></div>`; };
  if (t) t.onclick = async () => { try { S.tamper = await api('/api/audit/tamper', {}); S.aud = await api('/api/audit?limit=500'); touch('L7'); renderRail(); render(); } catch (e) { toast(e.message, 4000); } };
} };

/* ---------------- L8 : the full story, as real calls */
const STORY = [
  ['L3', 'Start a fresh borrower', 'New vault, new key, empty stores, empty audit chain. All values are fake.'],
  ['L1–L3', 'A 10-turn collections call goes through the gateway', 'Every turn is detected, tokenised, audited, then the LLM sees tokens only. The phone number arrives in two spoken pieces.'],
  ['L2', 'Check what the vault holds', 'Encrypted entries under one per-borrower key.'],
  ['L4', 'Consent guard: service data into an eval set', 'The brief\'s own example. Then the same write with explicit consent on file.'],
  ['L6', 'Plant copies nobody registered', 'A derived summary, a debug log with raw values, a crash dump in an unlisted folder.'],
  ['L5', 'Compare the registry with reality', 'What the platform believes it wrote vs what is actually in the stores.'],
  ['L6', 'Erasure request: the agent finds, deletes, verifies, shreds the key', 'It is never told where the hidden copies are.'],
  ['L8', 'Independent check (a plain file scan, not the agent)', 'Raw values left in any store, vault entries left, can the key still decrypt.'],
  ['L7', 'Audit: verify the chain, then try to tamper', 'The erasure proof is chained in. Editing history is detected.']];
STAGES.L8 = { html() {
  const st = S.story;
  const steps = STORY.map(([ly, n, s], i) => { const c = st ? (st.done.includes(i) ? 'done' : st.step === i ? 'act' : '') : ''; return `<div class="sstep ${c}"><span class="ix">${c === 'done' ? '✓' : i + 1}</span><div><span class="lay">${ly}</span><b>${esc(n)}</b><small>${esc(c === 'done' && st.notes[i] ? st.notes[i] : s)}</small></div></div>`; }).join('');
  return head('L8') + `<div class="row" style="margin-bottom:16px"><button class="btn pri lg" id="runstory" ${st && st.running ? 'disabled' : ''}>${st && st.running ? '<span class="spin"></span> Running…' : st && st.verdict ? '↻ Run it again' : '▶ Run the full story'}</button><span class="dimmer" style="font-size:12.5px">Real calls into every layer, paced for the room. Afterwards click any layer on the left to inspect what it did.</span></div>
    <div class="story"><div style="display:grid;gap:8px">${steps}</div><div><div class="card"><h3>What the gateway did, turn by turn</h3><div class="feed" id="feed">${st && st.feed.length ? st.feed.map(f => `<div class="fe"><div class="who2">${esc(f.who)} · ${f.ms} ms</div><div class="dimmer">${esc(f.text)}</div><div class="arrow">↓ the LLM and the log saw</div><div class="masked" style="line-height:1.7">${f.blocked ? '<span class="bad">blocked</span>' : tokHtml(f.masked)}</div></div>`).join('') : '<div class="dimmer">Press Run. Each turn appears here as typed, then as the LLM and the logs saw it.</div>'}</div></div>
      <div id="verdict">${st && st.verdict ? verdictHtml(st.verdict) : ''}</div></div></div>` + evidence('L8');
}, after(el) { $('#runstory', el).onclick = runStory; const f = $('#feed', el); if (f) f.scrollTop = f.scrollHeight; } };
function verdictHtml(v) {
  return `<div class="verdict ${v.clean ? '' : 'bad'}"><h2>${v.clean ? 'CLEAN' : 'NOT CLEAN'}</h2><div class="dim">${v.clean ? 'No raw value left in any store, no vault entry, and the borrower key is gone. The audit chain verifies, and it detects tampering.' : 'Something survived: see the counts.'}</div>
    <div class="tiles" style="margin-top:12px"><div class="tile"><div class="v ok">${v.raw_before} → ${v.raw_after}</div><div class="l">raw values in stores</div><div class="n">independent scan</div></div><div class="tile"><div class="v ok">${v.vault_before} → ${v.vault_after}</div><div class="l">vault entries</div><div class="n">key destroyed</div></div><div class="tile"><div class="v ok">${v.copies}</div><div class="l">copies found &amp; removed</div><div class="n">${v.hidden} were unregistered</div></div><div class="tile"><div class="v ${v.tamper ? 'ok' : 'bad'}">${v.tamper ? 'detected' : 'MISSED'}</div><div class="l">tamper test</div><div class="n">${v.records} chained records</div></div></div>
    <div class="dimmer" style="font-size:12px;margin-top:10px">Mock stores, synthetic fake values, stub or tokenised-only LLM. See each layer's limits.</div></div>`;
}
async function runStory() {
  if (S.story && S.story.running) return;
  const st = S.story = { running: true, step: 0, done: [], notes: {}, feed: [], verdict: null }; const draw = () => { if (S.stage === 'L8') { const k = $('#stage').scrollTop; render(); $('#stage').scrollTop = k; } };
  const done = async (i, note) => { st.done.push(i); st.notes[i] = note; st.step = i + 1; draw(); await sleep(1300); };
  try {
    draw(); await newSession('Ramesh Kumar'); S.story = st; await done(0, 'Session ' + S.status.session + ': vault, key store, mock stores and audit chain are empty.');
    for (const [who, text] of CALL) {
      const r = await send(text, { speaker: who, quiet: true, force: true }); if (!r) throw new Error('message failed');
      st.feed.push({ who, text, masked: r.l3.llm_saw || '', ms: r.l3.ms, blocked: r.status === 'blocked' }); draw(); await sleep(650);
    }
    const last = run(), toks = new Set(S.runs.flatMap(x => x.l2.tokens_this_message));
    await done(1, `${S.runs.length} turns processed. ${S.runs.reduce((a, x) => a + x.l7.events.length, 0)} events chained. The number said in two pieces became one token.`);
    await done(2, `${last.l2.entries.length} encrypted entries (${[...toks].slice(0, 5).join(' ')}…) under key ${last.l2.keys[0].key_id}…`);
    const a1 = await api('/api/consent/try', { store: 'eval_sets', purpose: 'service' }), a2 = await api('/api/consent/try', { store: 'eval_sets', purpose: 'model_improvement', explicit: true, consent_id: 'CN-DEMO-1' });
    S.attempts.push({ store: 'eval_sets', purpose: 'service', allowed: a1.allowed, reason: a1.reason, hash: a1.event.hash }, { store: 'eval_sets', purpose: 'model_improvement', explicit: true, allowed: a2.allowed, reason: a2.reason, hash: a2.event.hash });
    await done(3, `service → eval_sets: ${a1.allowed ? 'written' : 'REFUSED'} (${a1.reason}). With explicit consent + id: ${a2.allowed ? 'written' : 'refused'}.`);
    const pl = await api('/api/plant', {}); await done(4, `${pl.planted.length} copies planted, none registered.`);
    const before = await api('/api/lineage'); S.lin = before; const tot = before.stores.reduce((a, s) => a + s.count, 0), unreg = before.stores.reduce((a, s) => a + s.unregistered_records, 0);
    const regRecs = before.graph.nodes.filter(n => ['log', 'chunk', 'eval'].includes(n.kind)).length;
    await done(5, `The registry knows ${regRecs} records; the stores actually hold ${tot}. ${unreg} copies are in no registry.`);
    const er = await api('/api/erase', {}); S.erasure = er; touch('L1', 'L2', 'L3', 'L4', 'L5', 'L6', 'L7', 'L8');
    await done(6, `${er.status} in ${er.secs}s. ${er.record.methods.length} discovery methods found copies, incl. ${(er.record.unregistered_stores_with_hits || []).length} unregistered store(s).`);
    await done(7, `Raw values in stores ${er.raw_before} → ${er.raw_after}. Vault entries ${er.vault_before} → ${er.vault_after}. Key present: ${er.key_present ? 'YES' : 'no'}. Former token: ${er.probe}.`);
    const aud = await api('/api/audit?limit=500'); S.aud = aud; const tam = await api('/api/audit/tamper', {}); S.tamper = tam;
    await refreshStatus(); S.lin = await api('/api/lineage');
    await done(8, `Chain ${aud.chain.ok ? 'intact' : 'BROKEN'} (${aud.length} records). Edit to record #${tam.edited_seq} detected: ${!tam.copy_verifies ? 'yes' : 'NO'}. Real log intact: ${tam.real_log_intact ? 'yes' : 'NO'}.`);
    st.verdict = { clean: er.clean && aud.chain.ok && !tam.copy_verifies && tam.real_log_intact, raw_before: er.raw_before, raw_after: er.raw_after, vault_before: er.vault_before, vault_after: er.vault_after,
      copies: Object.values(er.stores_before).reduce((a, b) => a + b, 0), hidden: before.stores.reduce((a, s) => a + s.unregistered_records, 0), tamper: !tam.copy_verifies, records: aud.length };
  } catch (e) { toast('Story stopped: ' + e.message, 6000); st.verdict = { clean: false, raw_before: '?', raw_after: '?', vault_before: '?', vault_after: '?', copies: '?', hidden: '?', tamper: false, records: '?' }; }
  st.running = false; renderRail(); draw(); $('#verdict') && $('#verdict').scrollIntoView({ behavior: 'smooth', block: 'center' });
}

/* ------------------------------------------------------------------ data the stages fetch lazily */
const _go = go;
go = function (id, silent) {                                                        // eslint-disable-line no-func-assign
  S.stage = id;
  const need = [];
  if (id === 'L4' && !S.matrix) need.push(api('/api/consent/matrix').then(m => S.matrix = m));
  if (['L5', 'L6'].includes(id)) need.push(api('/api/lineage').then(d => S.lin = d));
  Promise.all(need).then(() => _go(id, silent)).catch(e => toast(e.message, 4000));
};
const _send = send;
send = async function (...a) { const r = await _send(...a); if (r && S.stage === 'L5') { S.lin = await api('/api/lineage'); render(); } return r; };   // eslint-disable-line no-func-assign

boot().catch(e => { $('#stage').innerHTML = `<div class="empty"><b>Cannot reach the demo server</b>${esc(e.message)}<br>Start it with <span class="mono">.venv/bin/python -m layer8_demo.server</span></div>`; });
