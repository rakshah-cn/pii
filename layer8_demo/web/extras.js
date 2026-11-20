'use strict';
(function () {
/* Presentation layer: Scorecard screen (+ live red-team suite), per-message Tool calls tab, and the Guided demo.
   Loaded BEFORE app.js; everything is attached to window and only touches app.js globals (S, api, esc, tbl, pill, L, ...) when called, after app.js has loaded. */

/* ================================================================ Scorecard */
const rtBadge = s => ({ pass: ['ok', 'blocked / masked'], regression: ['bad', 'REGRESSION'], gap: ['warn', 'known gap'], fixed: ['ok', 'gap fixed'] }[s] || ['', s]);
const leakProbe = l => { const m = /\(word: (.+)\)$/.exec(l); return m ? m[1] : l; };

function rtPanel(rt) {
  if (!rt || !rt.ran_at) return `<div class="empty"><b>Not run yet</b><span>Sends ${'63'} hostile inputs through the real pipeline: formats, languages, spoken digits, long messages, forged tokens. Takes about 3 seconds.</span></div>`;
  const must = rt.total - rt.known_gaps - rt.fixed_gaps, bad = rt.regressions;
  const groups = Object.entries(rt.groups).filter(([g]) => g !== 'known gaps');
  const gaps = rt.cases.filter(c => c.status === 'gap' || c.status === 'fixed');
  const regs = rt.cases.filter(c => c.status === 'regression');
  return `<div class="rt-sum">
      <div class="rt-big ${bad ? 'bad' : 'ok'}"><b>${rt.passed}<small> / ${must}</small></b><span>guardrail cases hold</span></div>
      <div class="rt-big ${rt.authz_ok === rt.authz.length ? 'ok' : 'bad'}"><b>${rt.authz_ok}<small> / ${rt.authz.length}</small></b><span>authorization checks</span></div>
      <div class="rt-big warn"><b>${rt.known_gaps}</b><span>documented gaps still open</span></div>
      <div class="rt-big ${bad ? 'bad' : ''}"><b>${bad}</b><span>regressions</span></div>
    </div>
    ${regs.length ? `<div class="alert bad" style="margin:10px 14px">Regressions: ${regs.map(c => esc(c.name)).join(', ')}</div>` : ''}
    <div class="pb"><div class="rt-groups">${groups.map(([g, v]) => `<div class="rt-g"><span>${esc(g)}</span><span class="bar"><i class="${v.pass < v.n ? 'o' : ''}" style="width:${(100 * v.pass / v.n).toFixed(0)}%"></i></span><b class="mono">${v.pass}/${v.n}</b></div>`).join('')}</div></div>
    <div class="ph" style="border-top:1px solid var(--line)"><b>Known gaps</b><span class="t3">a leak here is the expected result; "gap fixed" would mean it improved</span></div>
    <div class="pb rt-gaps">${gaps.map(c => { const [cls, t] = rtBadge(c.status); return `<div class="rt-gap"><div>${pill(cls, t)} <b>${esc(c.name)}</b></div><div class="masked">${tokHtml(c.masked, c.leaked.map(l => ({ text: leakProbe(l) })))}</div></div>`; }).join('') || '<span class="t3">None.</span>'}</div>
    <div class="ph" style="border-top:1px solid var(--line)"><b>Authorization and abuse</b></div>
    <div class="pb">${tbl(['Check', 'Result'], rt.authz.map(a => `<tr><td>${esc(a.name)}</td><td>${yes(a.ok, 'holds', 'FAILS')} <span class="t3 mono">${esc(a.detail)}</span></td></tr>`))}</div>
    <details class="pb"><summary class="t2" style="cursor:pointer">All ${rt.total} cases · ran in ${rt.secs} s</summary><div style="margin-top:8px">${tbl(['Group', 'Case', 'Result', 'What the LLM would see'], rt.cases.map(c => { const [cls, t] = rtBadge(c.status); return `<tr><td class="t2">${esc(c.group)}</td><td>${esc(c.name)}</td><td>${pill(cls, t)}</td><td class="mono t3" style="max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(c.masked)}">${esc(c.masked)}</td></tr>`; }))}</div></details>`;
}

window.runRedteam = async function () {
  const b = $('#rtrun'), box = $('#rtbox'); if (!b || !box) return;
  b.disabled = true; b.innerHTML = '<span class="spin"></span> Running…';
  try { S.rt = await api('/api/redteam', {}); box.innerHTML = rtPanel(S.rt); toast(`Red-team: ${S.rt.regressions} regressions, ${S.rt.known_gaps} known gaps`); }
  catch (e) { toast('Red-team run failed: ' + e.message, 5000); }
  finally { b.disabled = false; b.textContent = 'Run red-team suite'; }
};

window.renderScorecard = async function () {
  const tk = ++S.tk, sc = S.content.scorecard, rt = await api('/api/redteam').catch(() => ({ ran_at: null })); if (tk !== S.tk) return; S.rt = rt;
  const li = (a, cls) => `<ul class="sc-list ${cls}">${a.map(x => `<li>${esc(x)}</li>`).join('')}</ul>`;
  main().innerHTML = sbar('scorecard', '<button class="btn pri" id="rtrun">Run red-team suite</button>') + `<div class="content">
    <div class="sc-hero">
      <div class="sc-k ok"><b>${sc.measured.length}</b><span>measured</span><i>re-run on this machine, ${esc(sc.date)}</i></div>
      <div class="sc-k acc"><b>${sc.assumed.length}</b><span>assumed</span><i>true only if our assumptions hold</i></div>
      <div class="sc-k warn"><b>${sc.untested.length}</b><span>untested</span><i>no evidence either way</i></div>
      <div class="sc-k bad"><b>${sc.open.length}</b><span>open issues</span><i>found in review, not fixed</i></div>
    </div>
    <div class="alert warn"><b>Read this first.</b> Every score comes from our own synthetic data and mock stores. Expect the organisers' hidden corpus to score lower; the red-team suite below is the part that does not depend on our data.</div>
    <div class="grid21">
      <div class="panel"><div class="ph"><b>Measured</b><span class="t3">numbers we re-ran ourselves</span></div>
        ${tbl(['Metric', '>Value', 'Source'], sc.measured.map(m => `<tr><td>${esc(m.label)}<div class="t3" style="font-size:11.5px;max-width:520px">${esc(m.note)}</div></td><td class="r mono nw ok">${esc(m.value)}</td><td class="t3" style="font-size:11.5px">${esc(m.src)}</td></tr>`))}</div>
      <div class="panel" id="rtpanel"><div class="ph"><b>Red-team suite</b><span class="t3">live, real gateway</span></div><div id="rtbox">${rtPanel(rt)}</div></div>
    </div>
    <div class="grid3">
      <div class="panel"><div class="ph"><b>Assumed</b>${pill('acc', sc.assumed.length)}</div>${li(sc.assumed, 'acc')}</div>
      <div class="panel"><div class="ph"><b>Not tested</b>${pill('warn', sc.untested.length)}</div>${li(sc.untested, 'warn')}</div>
      <div class="panel"><div class="ph"><b>Open issues</b>${pill('bad', sc.open.length)}</div>${li(sc.open, 'bad')}</div>
    </div></div>`;
  $('#rtrun').onclick = window.runRedteam;
};

/* ================================================================ Tool calls tab (per message) */
window.toolsPanel = function () {
  const i = selIdx(), r = hist()[i];
  if (!r || r.status === 'erased') return '<div class="empty"><b>No message selected</b><span>Select a message to see the calls made for it.</span></div>';
  const l3 = r.l3 || {}, ok = r.status !== 'blocked', toks = new Set(r.l2.tokens_this_message), ents = r.l2.entries.filter(e => toks.has(e.token));
  const ms = v => (v == null || v === '' ? '<span class="t3">n/a</span>' : `<span class="mono">${esc(v)} ms</span>`);
  const row = (n, what, res, t, real) => `<tr><td><b>${n}</b><div class="t3" style="font-size:11.5px">${what}</div></td><td>${res}</td><td class="r">${t}</td><td>${real ? pill('ok', 'real') : pill('warn', 'stub')}</td></tr>`;
  const rows = [row('Detector', 'L1 · rules + spoken engine + Chitragupta (int8 ONNX)', `${r.l1.spans.length} value(s) found`, ms(r.l1.ms), true)];
  if (ok) {
    rows.push(row('Vault: encrypt', 'L2 · AES-256-GCM, one key per borrower, database write', `${ents.length} value(s) sealed${ents[0] ? ' · key ' + esc(ents[0].key_id) : ''}`, ms(l3.stage_ms && l3.stage_ms.tokenise), true));
    rows.push(row('Independent audit', 'L3 · 5 checks on the tokenised text', l3.retries ? `re-masked on retry ${l3.retries}` : 'clean', '<span class="t3">n/a</span>', true));
    rows.push(row('LLM', l3.llm_live ? 'Amazon Nova Pro on Bedrock · tokens only' : 'local stand-in · tokens only (live mode needs a valid Bedrock token)', ok ? 'replied' : '', '<span class="t3">n/a</span>', !!l3.llm_live));
    r.l4.writes.forEach(w => rows.push(row('Store write: ' + esc(w.store), 'L4 consent guard, then L5 lineage', w.allowed ? 'written' : '<b class="bad">refused</b>', '<span class="t3">n/a</span>', true)));
  } else rows.push(row('Gateway', 'L3/L4 · fail closed', `<b class="bad">blocked: ${esc(r.reason || '')}</b>`, '<span class="t3">n/a</span>', true));
  rows.push(row('Audit chain', 'L7 · hash-chained records', `${r.l7.events.length} record(s) appended`, '<span class="t3">n/a</span>', true));
  const c = curCase(), counts = {};
  (S.audit ? S.audit.records : []).filter(x => c && (x.borrower_hash || '').startsWith(c.hash)).forEach(x => { counts[x.kind_name] = (counts[x.kind_name] || 0) + 1; });
  const tot = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 9);
  return `<div class="pr-head"><div class="t3">Calls made for message ${esc(r.message_id)}</div><div>${rReq(r.request_id)}</div></div>
    ${tbl(['Call', 'Result', '>Time', ''], rows)}
    <h5>Audited events for this borrower</h5>${tot.length ? tbl(['Event', '>Count'], tot.map(([k, v]) => `<tr><td class="mono">${esc(k)}</td><td class="r mono">${v}</td></tr>`)) : '<div class="t3">None yet.</div>'}
    <div class="t3" style="margin-top:8px;font-size:11.5px">"real" = executed for this message by the actual layer code. The LLM row is a stub until a valid Bedrock token is configured.</div>`;
};

/* ================================================================ Guided demo */
const go = async (hash, wait = 1300) => { location.hash = hash; await sleep(wait); };
const GUIDE = [
  { t: 'The problem', say: 'Lenders want LLMs on collection calls. Those calls are full of Aadhaar, PAN and phone numbers, said aloud in five languages. DPDP says: protect it, limit its use, erase it on request, and prove it. This is the path every message takes.',
    run: async () => { await go(L('architecture'), 900); } },
  { t: 'Protect a spoken number', say: 'A Hindi caller says a phone number across two turns. The detector (rules, a spoken-number engine and a multilingual model) finds it, the vault encrypts it under this borrower\'s own key, and the LLM only ever sees tokens. Watch the strip: real gateway time against the 50 ms budget.',
    run: async () => { const c = await newCase('Demo borrower'); S.demoSid = c.sid; await go(L('conversations', c.sid), 1500); S.view = 'agent'; await playScn(3); S.panel = 'protection'; drawSide(); } },
  { t: 'What was said vs what the LLM saw', say: 'Same conversation, two views. Original text is for this demo only; the production gateway never returns it. Click the layer chips to see each step: consent, detection, encryption, audit, dispatch, storage.',
    run: async () => { await go(L('conversations', S.demoSid), 500); S.view = 'orig'; drawThread(); } },
  { t: 'It fails closed', say: 'Now we switch the detector OFF to simulate a miss. An independent audit, built differently, still catches the phone number and e-mail and re-masks them before anything is sent.',
    run: async () => { await go(L('conversations', S.demoSid), 500); S.view = 'agent'; drawThread(); await playScn(9); S.panel = 'protection'; drawSide(); } },
  { t: 'And where it cannot', say: 'A name is different: no pattern can recognise one. With the detector off, the name reaches the LLM and the console says so in red. This is why the detector, not the audit, is the main defence, and why names are our weakest type at 92.8%.',
    run: async () => { await go(L('conversations', S.demoSid), 500); await playScn(10); } },
  { t: 'Purpose limitation', say: 'Consent is checked before anything else. A message tagged for model improvement without explicit consent is refused at the gateway, and the refusal is written to the audit chain.',
    run: async () => { await go(L('conversations', S.demoSid), 500); await playScn(11); } },
  { t: 'Find every copy and erase', say: 'The borrower asks to be forgotten. We first plant copies the platform never registered: a debug log, a derived summary, a crash dump. The erasure agent searches every store, deletes, re-searches from scratch, destroys the key, and an independent file scan checks its work.',
    run: async () => {
      const sid = S.demoSid; await go(L('privacy', 'new:' + sid), 1200);
      await api('/api/plant', {}, sid); const er = await api('/api/erase', {}, sid); S.erFull[er.request_id] = er; S.anim = er.request_id;
      S.hist[sid] = (S.hist[sid] || []).map(h => ({ turn: h.turn, message_id: h.message_id, ts: h.ts, speaker: h.speaker, status: 'erased' }));
      S.audit = null; S.lin = null; S.cases = await api('/api/cases'); S.reqs = (await api('/api/erasure')).requests; renderNav(); location.hash = L('privacy', er.request_id); await sleep(1500);
    } },
  { t: 'Prove it', say: 'The audit trail is hash-chained: each record contains the hash of the one before. Verify the chain, then we edit one record in a copy and the tampering is detected at exactly that record.',
    run: async () => { await go(L('audit'), 1500); await verifyChain(); await sleep(900); await tamper(); } },
  { t: 'The honest scorecard', say: 'What we measured, what we assumed, what we have not tested, and what is still open. The red-team suite on the right runs live against the real pipeline: regressions must be zero, and the known gaps are listed, not hidden.',
    run: async () => { await go(L('scorecard'), 1400); await window.runRedteam(); } },
];

const G = { on: false, i: 0, busy: false };
function guideBar() {
  let el = $('#guide'); if (!el) { el = document.createElement('div'); el.id = 'guide'; el.className = 'gd'; el.setAttribute('role', 'region'); el.setAttribute('aria-label', 'Guided demo'); document.body.appendChild(el); }
  const s = GUIDE[G.i];
  el.innerHTML = `<div class="gd-top"><span class="gd-step">STEP ${G.i + 1} / ${GUIDE.length}</span><b>${esc(s.t)}</b><button class="x" id="gx" aria-label="Close guide">×</button></div>
    <p class="gd-say">${esc(s.say)}</p>
    <div class="gd-foot"><div class="gd-dots">${GUIDE.map((_, k) => `<i class="${k === G.i ? 'on' : k < G.i ? 'done' : ''}"></i>`).join('')}</div>
      <div class="gd-btns"><button class="btn sm" id="gp" ${G.i === 0 || G.busy ? 'disabled' : ''}>← Back</button><button class="btn sm" id="gr" ${G.busy ? 'disabled' : ''}>Replay</button>
      <button class="btn sm pri" id="gn" ${G.busy ? 'disabled' : ''}>${G.busy ? '<span class="spin"></span> Running…' : G.i === GUIDE.length - 1 ? 'Finish' : 'Next →'}</button></div></div>`;
  el.classList.add('on');
  $('#gx').onclick = window.closeGuide; $('#gp').onclick = () => guideStep(G.i - 1); $('#gr').onclick = () => guideStep(G.i); $('#gn').onclick = () => G.i === GUIDE.length - 1 ? window.closeGuide() : guideStep(G.i + 1);
}
async function guideStep(i) {
  if (G.busy || i < 0 || i >= GUIDE.length) return;
  G.i = i; G.busy = true; guideBar();
  try { await GUIDE[i].run(); } catch (e) { toast('Demo step failed: ' + e.message, 5000); } finally { G.busy = false; if (G.on) guideBar(); }
}
window.startGuide = function () { G.on = true; G.busy = false; document.body.classList.add('guiding'); guideStep(0); };
window.closeGuide = function () { G.on = false; document.body.classList.remove('guiding'); const el = $('#guide'); if (el) el.classList.remove('on'); };
document.addEventListener('keydown', e => {
  if (!G.on || /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) return;
  if (e.key === 'ArrowRight') guideStep(G.i + 1); else if (e.key === 'ArrowLeft') guideStep(G.i - 1);
});
})();
