'use strict';
/* Story mode: follows one borrower (Ramesh) through the real application. Every step performs real actions against the real layers;
   the narrator only explains, in plain words, what just happened and which layer did it. */
const STORY_TEXT = 'Ramesh Kumar has a loan, and a collections agent called Asha is on the phone with him. Follow one conversation from his first sentence to the day he asks us to forget him.';
const ST = { on: false, i: 0, ctx: {}, done: {}, busy: false, prevUser: null };

const waitFor = async (sel, ms = 6000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { const el = typeof sel === 'function' ? sel() : $(sel); if (el) return el; await sleep(100); } return null; };
async function asUser(id) {
  if (S.user === id) return;
  S.user = id; sessionStorage.setItem('user', id); S.status = await api('/api/status'); renderEnv(); renderNav();
}
async function nav(hash, sel) {
  if (location.hash === hash) route(); else location.hash = hash;
  if (sel) await waitFor(sel);
}
const lastMsg = (bot) => () => $$('#msgs .msg').filter(m => bot ? m.classList.contains('bot') : !m.classList.contains('agent')).pop();
const caseHash = () => (S.cases.find(c => c.sid === S.case) || {}).hash || '';

const CHAPTERS = ['Ramesh calls', 'When something slips', 'Only with permission', 'Ramesh asks to be forgotten', 'The proof', 'The whole system'];
const STEPS = [
  { ch: 0, title: 'Meet Ramesh', layers: ['L8 Application'], text: STORY_TEXT, hl: '.ci.on',
    run: async c => { await asUser('asha'); const k = await newCase('Ramesh Kumar'); c.sid = k.sid; S.view = 'agent'; await nav('#/conversations/' + k.sid, '#msgs'); } },
  { ch: 0, title: 'He gives his number', layers: ['L8 Application'], text: 'Ramesh says his name and mobile number. This is what he typed, and what his own screen shows.', hl: lastMsg(false),
    run: async c => { await nav('#/conversations/' + c.sid, '#msgs'); S.view = 'orig'; drawThread(); await send('Hi, I am Ramesh Kumar. My mobile number is 9845012345.', { speaker: 'Customer', user: 'asha' }); } },
  { ch: 0, title: 'Asha never sees it', layers: ['L1 Detection', 'L2 Vault'], text: 'Before anyone, or any AI, reads the message, the app swaps his name and number for placeholders: PERSON_1 and PHONE_1. The real values are locked away.', hl: lastMsg(false),
    run: async () => { S.view = 'agent'; drawThread(); } },
  { ch: 0, title: 'Five checks in a blink', layers: ['L4 Consent', 'L1 Detection', 'L2 Vault', 'L3 Gateway'], text: 'Behind that one message: is it allowed? what is private? lock it away, double-check, then send. All in about forty milliseconds.', hl: '#cside .steps',
    run: async () => { S.panel = 'protection'; drawSide(); } },
  { ch: 0, title: 'Locked with his own key', layers: ['L2 Vault'], text: 'His number sits in a vault, encrypted with a key that belongs to Ramesh alone. Even an engineer with database access sees only scrambled text.', hl: '.content > .panel:nth-child(2)',
    run: async () => { await nav('#/vault', '.content .tbl'); } },
  { ch: 0, title: 'Digits said out loud', layers: ['L1 Detection'], text: 'People say numbers in pieces: "nine eight four five", then "zero one two three four five". The app joins the pieces back into one protected number.', hl: lastMsg(false),
    run: async c => { await nav('#/conversations/' + c.sid, '#msgs'); S.view = 'agent'; await send('Sure, my number is nine eight four five', { speaker: 'Customer', user: 'asha' }); await send('zero one two three four five', { speaker: 'Customer', user: 'asha' }); } },
  { ch: 0, title: 'The AI only sees placeholders', layers: ['L3 Gateway'], text: 'The AI helped write the reply, but it only ever saw placeholders. Ramesh sees his real details; the saved log keeps the placeholders.', hl: lastMsg(true),
    run: async () => { S.view = 'orig'; drawThread(); } },

  { ch: 1, title: 'What if the main check fails?', layers: ['L3 Gateway'], text: 'We switched the main detector off on purpose. A second, independent check still found the phone number and email before the AI saw them.', hl: lastMsg(false),
    run: async c => { S.view = 'agent'; await nav('#/conversations/' + c.sid, '#msgs'); await send('You can call me on 9123456780 or mail rakesh.s@example.com', { speaker: 'Customer', user: 'asha', detector: 'blind' }); } },
  { ch: 1, title: 'And what it cannot catch', layers: ['L3 Gateway'], text: 'It is not perfect. A name has no pattern, so with the detector off it slipped through. We show this openly: it is a known limit, and why the main detector matters.', hl: lastMsg(false),
    run: async () => { await send('This is Priya Nair speaking, please call 9123456780', { speaker: 'Customer', user: 'asha', detector: 'blind' }); } },

  { ch: 2, title: 'Training the AI needs a yes', layers: ['L4 Consent'], text: 'Rohit, the privacy officer, tries to use Ramesh’s service data to train an AI. The rule says no: that needs Ramesh’s explicit permission. The refusal is recorded.', hl: '.content > .panel:last-child',
    run: async c => { await asUser('rohit'); await api('/api/consent/try', { store: 'eval_sets', purpose: 'service' }, c.sid); await nav('#/consent', '.content .tbl'); } },

  { ch: 3, title: 'Stray copies nobody tracked', layers: ['L5 Lineage'], text: 'Real systems collect strays: a debug log, a summary, a crash dump. We added three copies that no register knows about.', hl: '.content .panel',
    run: async c => { await asUser('asha'); await api('/api/plant', {}, c.sid); S.mapFilter = 'un'; S.lin = null; await nav('#/map', '.content .tbl'); } },
  { ch: 3, title: 'Ramesh asks to be forgotten', layers: ['L8 Application'], text: 'Asha submits his deletion request. She cannot approve it herself: two more people must agree.', hl: '.tracker',
    run: async c => { const r = await api('/api/privacy/requests', { sid: c.sid, reason: 'borrower_request' }, c.sid); c.req = r.request_id; S.reqs = await api('/api/privacy/requests'); await nav('#/privacy/' + c.req, '.tracker'); } },
  { ch: 3, title: 'First checker', layers: ['L8 Application'], text: 'Rohit, the privacy officer, checks the request and recommends it.', hl: '.tracker',
    run: async c => { await asUser('rohit'); await api(`/api/privacy/requests/${c.req}/recommend`, { note: 'identity verified' }, c.sid); S.reqs = await api('/api/privacy/requests'); await nav('#/privacy/' + c.req, '.tracker'); S.anim = ''; drawPrivacy(); await sleep(300); } },
  { ch: 3, title: 'Second checker, then it runs', layers: ['L6 Erasure'], text: 'Meera, the data protection officer, approves. Only now does the erasure agent start: it searches every store, including the three stray copies, deletes them, then searches again from scratch.', hl: '.stepper',
    run: async c => { await asUser('meera'); await api(`/api/privacy/requests/${c.req}/approve`, { note: '' }, c.sid); S.reqs = await api('/api/privacy/requests'); S.cases = await api('/api/cases'); S.anim = c.req; await nav('#/privacy/' + c.req, '.stepper'); } },
  { ch: 3, title: 'A certificate', layers: ['L6 Erasure', 'L2 Vault'], text: 'A certificate records what was found, what was removed and who approved. A separate file scan found no trace left, and Ramesh’s key was destroyed, so any leftover scrambled copy is unreadable.', hl: '#cert',
    run: async () => { } },

  { ch: 4, title: 'Every step was written down', layers: ['L7 Audit'], text: 'Vikram, the internal auditor, can see who did what, when and from where, including Rohit’s and Meera’s approvals. Asha cannot open this page at all.', hl: '#atable',
    run: async () => { await asUser('vikram'); S.af = { q: '', cats: [], outs: [], actor: '', range: 'all', case: caseHash(), trace: '' }; S.auTab = 'events'; await nav('#/audit', '#atable tbody tr'); } },
  { ch: 4, title: 'Can history be edited?', layers: ['L7 Audit'], text: 'We changed one number in a copy of the log. Every later record failed to match, so the edit is detected instantly. The real log stays untouched.', hl: '#modal',
    run: async () => { await tamper(); await waitFor('#modal.on'); } },

  { ch: 5, title: 'The whole system', layers: ['L0–L8'], text: 'That was one borrower. Behind it: nine layers, each tested. Here is the whole system on one page.', hl: '.arch',
    run: async () => { closeModal(); await asUser('asha'); await nav('#/architecture', '.arch'); } },
];

function storyCard() {
  const s = STEPS[ST.i], last = ST.i === STEPS.length - 1;
  let el = $('#story'); if (!el) { el = document.createElement('div'); el.id = 'story'; el.className = 'story-card'; document.body.appendChild(el); }
  el.innerHTML = `<div class="sc-top"><span class="sc-ch">Chapter ${s.ch + 1} of ${CHAPTERS.length} · ${esc(CHAPTERS[s.ch])}</span><button class="sc-x" id="sx" aria-label="Exit story">×</button></div>
    <h3>${esc(s.title)}</h3><p>${ST.busy ? '<span class="spin"></span> Working…' : esc(s.text)}</p>
    <div class="sc-layers">${s.layers.map(l => `<span>${esc(l)}</span>`).join('')}</div>
    <div class="sc-bot"><div class="sc-dots">${STEPS.map((x, k) => `<i class="${k === ST.i ? 'on' : k < ST.i ? 'done' : ''}" data-k="${k}"></i>`).join('')}</div>
      <div class="sc-btns"><button class="btn ghost sm" id="sb" ${ST.i === 0 || ST.busy ? 'disabled' : ''}>Back</button><button class="btn pri sm" id="sn" ${ST.busy ? 'disabled' : ''}>${last ? 'Finish' : 'Next'}</button></div></div>`;
  $('#sx').onclick = endStory; $('#sb').onclick = () => storyGo(ST.i - 1); $('#sn').onclick = () => last ? endStory(true) : storyGo(ST.i + 1);
}
function clearHl() { $$('.sty-hl').forEach(e => e.classList.remove('sty-hl')); }
async function storyGo(i) {
  if (ST.busy || i < 0 || i >= STEPS.length) return;
  ST.i = i; ST.busy = true; clearHl(); storyCard();
  const s = STEPS[i];
  try {
    if (!ST.done[i]) { await s.run(ST.ctx); ST.done[i] = true; }
    else if (s.hl) { /* already performed: only restore the view */ const rr = STEPS.slice(0, i + 1).reverse().find(x => x.restore); }
  } catch (e) { toast('Story step failed: ' + e.message, 6000); }
  ST.busy = false; storyCard();
  if (s.hl) {                                                           // a screen may redraw after we mark it: re-apply until the mark survives
    for (let k = 0; k < 4 && ST.i === i; k++) {
      const el = await waitFor(s.hl, 4000);
      if (el) { clearHl(); el.classList.add('sty-hl'); if (k === 0) el.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
      await sleep(700); if ($('.sty-hl')) break;
    }
  }
}
async function startStory() {
  if (ST.on) return;
  Object.assign(ST, { on: true, i: 0, ctx: {}, done: {}, busy: false, prevUser: S.user }); document.body.classList.add('storying');
  await storyGo(0);
}
async function endStory(finished) {
  clearHl(); closeModal(); const el = $('#story'); if (el) el.remove(); document.body.classList.remove('storying'); ST.on = false;
  await asUser(ST.prevUser || 'asha'); S.view = 'agent';
  if (finished) toast('Story complete. Every screen you saw is live: explore it.', 4000);
  location.hash = '#/' + ROLE_LAND[role()]; route();
}
document.addEventListener('keydown', e => { if (!ST.on || /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) return; if (e.key === 'ArrowRight') storyGo(ST.i + 1); if (e.key === 'ArrowLeft') storyGo(ST.i - 1); });
document.addEventListener('DOMContentLoaded', () => { const b = $('#storybtn'); if (b) b.onclick = startStory; });
window.startStory = startStory;
