'use strict';
/* Theme + motion. Dark ("obsidian + aurora") by default, remembered per browser.
   Provides: toggleTheme(), the cursor spotlight on glass cards, page-enter transitions, and the firewall strip animator (window.fw).
   Loaded in <head> so the theme class is set before first paint (no light flash). */
(function () {
  const KEY = 'piivault.theme';
  const read = () => { try { return localStorage.getItem(KEY); } catch { return null; } };
  const write = v => { try { localStorage.setItem(KEY, v); } catch { /* private mode: the choice just will not persist */ } };
  const apply = t => document.documentElement.classList.toggle('dark', t !== 'light');
  apply(read());
  window.themeIsDark = () => document.documentElement.classList.contains('dark');
  window.toggleTheme = () => { const t = window.themeIsDark() ? 'light' : 'dark'; write(t); apply(t); return t; };
  const reduced = () => window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---- spotlight: only the card under the cursor is updated (cheap), using event delegation */
  const SEL = '.panel,.pform,.alane,.step,.ab';
  let raf = 0;
  document.addEventListener('mousemove', e => {
    if (raf || !window.themeIsDark()) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      const c = e.target.closest && e.target.closest(SEL);
      if (!c) return;
      const r = c.getBoundingClientRect();
      c.style.setProperty('--x', (e.clientX - r.left) + 'px');
      c.style.setProperty('--y', (e.clientY - r.top) + 'px');
    });
  }, { passive: true });

  /* ---- page transitions: for 1.5 s after a route change, anything newly added directly under #main fades up once.
         (Re-renders caused by clicking inside a screen happen later and therefore never replay it.) */
  let until = 0;
  window.markRoute = () => { until = performance.now() + 1500; };
  document.addEventListener('DOMContentLoaded', () => {
    const main = document.getElementById('main');
    if (!main || reduced()) return;
    new MutationObserver(list => {
      if (performance.now() > until) return;
      for (const m of list) for (const n of m.addedNodes) {
        if (n.nodeType === 1 && n.parentElement === main && !n.querySelector('.skels')) n.classList.add('enter-page');
      }
    }).observe(main, { childList: true });
  });

  /* ---- firewall strip: raw particles (sky) cross the shield and come out as tokens (emerald); a blocked message bounces back (rose).
         One animator for the whole app: it re-attaches to whatever <canvas id="fwc"> currently exists, so re-rendering the thread never restarts it. */
  const P = []; let prev = 0, flash = 0, flashBad = false, started = false;
  const rnd = (a, b) => a + Math.random() * (b - a);
  const spawn = (h, speed, kind) => ({ x: -6, y: rnd(8, h - 8), v: speed, kind: kind || 'raw', crossed: false, bounced: false, a: 1, tok: false });
  function frame(t) {
    requestAnimationFrame(frame);
    if (document.hidden) { prev = t; return; }
    const c = document.getElementById('fwc');
    if (!c || !window.themeIsDark()) { prev = t; return; }
    const dt = Math.min(0.05, (t - prev) / 1000 || 0.016); prev = t;
    const dpr = Math.min(window.devicePixelRatio || 1, 2), w = c.clientWidth, h = c.clientHeight;
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
    const x = c.getContext('2d'); x.setTransform(dpr, 0, 0, dpr, 0, 0); x.clearRect(0, 0, w, h);
    const mid = w / 2;
    if (Math.random() < 0.035) P.push(spawn(h, rnd(110, 170), 'idle'));             // faint idle trickle
    flash = Math.max(0, flash - dt * 1.6);
    // shield: soft glow + core line
    const col = flashBad ? '251,113,133' : '56,189,248';
    const g = x.createLinearGradient(mid - 26, 0, mid + 26, 0);
    g.addColorStop(0, `rgba(${col},0)`); g.addColorStop(.5, `rgba(${col},${0.10 + flash * 0.4})`); g.addColorStop(1, `rgba(${col},0)`);
    x.fillStyle = g; x.fillRect(mid - 26, 0, 52, h);
    x.strokeStyle = `rgba(${col},${0.55 + flash * 0.45})`; x.lineWidth = 1.5 + flash * 1.5;
    x.beginPath(); x.moveTo(mid, 3); x.lineTo(mid, h - 3); x.stroke();
    for (let i = P.length - 1; i >= 0; i--) {
      const p = P[i];
      if (p.delay > 0) { p.delay -= dt; continue; }
      p.x += p.v * dt;
      if (!p.crossed && p.x >= mid) {
        p.crossed = true;
        if (p.bad) { p.v = -p.v * 0.6; p.bounced = true; } else p.tok = true;           // raw particle becomes a token
      }
      if (p.bounced) p.a -= dt * 1.1;
      if (p.x > w + 8 || p.x < -10 || p.a <= 0) { P.splice(i, 1); continue; }
      const faint = p.kind === 'idle' ? 0.45 : 1;
      const rgb = p.bounced ? '251,113,133' : (p.tok ? '52,211,153' : '56,189,248');
      x.globalAlpha = p.a * faint;
      const tail = x.createLinearGradient(p.x - Math.sign(p.v) * 18, 0, p.x, 0);
      tail.addColorStop(0, `rgba(${rgb},0)`); tail.addColorStop(1, `rgba(${rgb},.7)`);
      x.strokeStyle = tail; x.lineWidth = 1.5; x.beginPath(); x.moveTo(p.x - Math.sign(p.v) * 18, p.y); x.lineTo(p.x, p.y); x.stroke();
      x.fillStyle = `rgb(${rgb})`; x.shadowColor = `rgba(${rgb},.9)`; x.shadowBlur = 8;
      if (p.tok) { x.beginPath(); x.roundRect(p.x - 3.5, p.y - 3.5, 7, 7, 2); x.fill(); } else { x.beginPath(); x.arc(p.x, p.y, 2.6, 0, 6.2832); x.fill(); }
      x.shadowBlur = 0; x.globalAlpha = 1;
    }
  }
  window.fw = {
    /** n = number of detected values, blocked = the gateway refused the message */
    pulse(n, blocked) {
      const c = document.getElementById('fwc'), h = c ? c.clientHeight : 46, count = 8 + Math.min(14, (n || 0) * 3);
      for (let i = 0; i < count; i++) { const p = spawn(h, rnd(380, 640)); p.delay = i * 0.035; p.bad = !!blocked; P.push(p); }
      flash = 1; flashBad = !!blocked;
      if (P.length > 120) P.splice(0, P.length - 120);
    }
  };
  if (!started && !reduced()) { started = true; requestAnimationFrame(frame); }
})();
