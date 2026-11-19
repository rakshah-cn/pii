"""Browser rehearsal of the demo, in one command: it drives a real headless Chrome through the whole app and tells you if anything is broken.

    .venv/bin/python -m layer8_demo.rehearse_browser                      # the demo must be running (default http://127.0.0.1:8808)
    .venv/bin/python -m layer8_demo.rehearse_browser --url http://127.0.0.1:8818 --out data/rehearsal/mine

What it does, with the REAL layers behind the page (nothing is mocked in the browser):
  1. loads the page and waits for the sidebar;
  2. starts "Follow the story" and goes through every step (each step performs real actions: messages through the gateway, a privacy request, the maker-checker approvals, the erasure,
     the tamper test), saving a screenshot per step and checking that no step reports a failure;
  3. opens EVERY screen as EVERY role (agent, privacy officer, DPO, auditor) and checks that it renders something;
  4. collects every browser console error, uncaught exception, failed request and HTTP 4xx / 5xx.
Exit code 0 = clean; 1 = something is broken (the list is printed). Output: <out>/report.json and <out>/shots/*.png.
Known and EXPECTED: one HTTP 403 on /api/audit/full (the agent role is refused the audit log, the page catches it) - it is listed but does not fail the run.
Needs `websocket-client` (pip install websocket-client) and a Chrome / Chromium (set CHROME_BIN, or it looks for Playwright's headless shell, google-chrome, chromium)."""
import argparse
import base64
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

try:
    import websocket
except ImportError:
    sys.exit("This tool needs the websocket-client package:  .venv/bin/pip install websocket-client")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPECTED_403 = "/api/audit/full"
ROLES = ["asha", "rohit", "meera", "vikram"]


def find_chrome(explicit=None):
    cands = [explicit, os.environ.get("CHROME_BIN")]
    cands += sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell")), reverse=True)
    cands += [shutil.which(n) for n in ("google-chrome", "chromium", "chromium-browser", "chrome")]
    for c in cands:
        if c and os.path.exists(c):
            return c
    sys.exit("No Chrome / Chromium found: set CHROME_BIN=/path/to/chrome")


class CDP:
    """A minimal Chrome DevTools Protocol client (no Playwright needed)."""
    def __init__(self, url):
        self.ws, self.n, self.events = websocket.create_connection(url, suppress_origin=True, timeout=120), 0, []

    def send(self, method, **params):
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)

    def pump(self, seconds=0.3):
        self.ws.settimeout(seconds)
        try:
            while True:
                self.events.append(json.loads(self.ws.recv()))
        except websocket.WebSocketTimeoutException:
            pass
        finally:
            self.ws.settimeout(120)


def run(base, out, chrome, port=9333):
    shots = os.path.join(out, "shots")
    os.makedirs(shots, exist_ok=True)
    prof = os.path.join(out, "chrome-profile")
    args = [chrome, f"--remote-debugging-port={port}", "--no-sandbox", "--disable-gpu", f"--user-data-dir={prof}", "--window-size=1440,900", "about:blank"]
    if "headless-shell" not in chrome:
        args.insert(1, "--headless=new")
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    report, fails = {"url": base, "story": [], "screens": []}, []
    try:
        tabs = None
        for _ in range(60):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2))
                break
            except Exception:
                time.sleep(0.25)
        if not tabs:
            sys.exit("Chrome did not start")
        c = CDP(next(t for t in tabs if t["type"] == "page")["webSocketDebuggerUrl"])
        for m in ("Page.enable", "Runtime.enable", "Log.enable", "Network.enable"):
            c.send(m)
        c.send("Emulation.setDeviceMetricsOverride", width=1440, height=900, deviceScaleFactor=1, mobile=False)

        def js(expr):
            r = c.send("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
            if "exceptionDetails" in r:
                return {"__error__": r["exceptionDetails"].get("exception", {}).get("description", r["exceptionDetails"].get("text", ""))[:300]}
            return r["result"].get("value")

        def shot(name):
            open(os.path.join(shots, name + ".png"), "wb").write(base64.b64decode(c.send("Page.captureScreenshot", format="png")["data"]))

        def wait(cond, secs=40):
            t0 = time.time()
            while time.time() - t0 < secs:
                v = js(cond)
                if v and not (isinstance(v, dict) and "__error__" in v):
                    return v
                time.sleep(0.25)
            return None

        c.send("Page.navigate", url=base + "/")
        if not wait("document.querySelectorAll('#navitems [data-s]').length > 3", 45):
            fails.append("the page did not load its sidebar within 45 s")
        shot("00-landing")

        # ---- 1. the guided story
        time.sleep(1.0)
        js("var b = document.querySelector('#storybtn'); b && b.click(); 1")
        time.sleep(1.5)
        if not js("typeof ST !== 'undefined' && ST.on"):
            report["story_button_works"] = False
            fails.append("the 'Follow the story' button does nothing (no click handler is attached); the tour was started directly to keep going")
            js("typeof startStory === 'function' && startStory(); 1")
        else:
            report["story_button_works"] = True
        n_steps = js("typeof STEPS !== 'undefined' ? STEPS.length : 0") or 0
        if not n_steps:
            fails.append("the story has no steps (STEPS is missing)")
        for i in range(n_steps if isinstance(n_steps, int) else 0):
            if not wait("!!document.querySelector('#story') && ST.i === %d && !ST.busy" % i, 120):
                c.pump(0.5)
                shot(f"story-{i:02d}-STUCK")
                report["story"].append({"i": i, "ok": False, "note": "step did not finish within 120 s"})
                fails.append(f"story step {i + 1} did not finish within 120 s")
                break
            title = js("STEPS[ST.i].title")
            time.sleep(1.2)
            toast = js("(()=>{const t=document.querySelector('#toast'); return t && t.classList.contains('on') ? t.textContent : ''})()") or ""
            facts = {}
            if title == "A certificate":
                txt = js("document.querySelector('#main').innerText") or ""
                m = re.search(r"raw values in store files\s*(\d+)\s*→\s*(\d+)", txt)
                facts["raw_values_before_after"] = list(m.groups()) if m else None
                facts["outcome"] = re.findall(r"\b(ERASED_VERIFIED|PARTIAL|NOT_ERASED)\b", txt)[:1]
                if not m or m.group(2) != "0" or "ERASED_VERIFIED" not in txt:
                    fails.append(f"the erasure certificate is not clean: {facts}")
            if title == "Can history be edited?":
                facts["modal"] = (js("document.querySelector('#modal').innerText") or "")[:160].replace("\n", " | ")
                if "BROKEN" not in facts["modal"]:
                    fails.append("the tamper test did not report the edit")
            if "Blocked" in (js("Array.from(document.querySelectorAll('#msgs .msg')).slice(-2).map(m=>m.innerText).join(' ')") or "") and title in ("Digits said out loud", "The AI only sees placeholders", "He gives his number"):
                fails.append(f"story step '{title}': a message was BLOCKED")
            shot(f"story-{i:02d}")
            ok = not toast.startswith("Story step failed")
            if not ok:
                fails.append(f"story step '{title}' failed: {toast[:120]}")
            report["story"].append({"i": i, "title": title, "ok": ok, "toast": toast[:200], "facts": facts})
            js("document.querySelector('#sn').click(); 1")
            if i == n_steps - 1:
                time.sleep(1.5)

        # ---- 2. every screen, every role
        screens = js("SCREENS.map(s => ({id: s.id, grp: s.grp}))") or []
        for role in ROLES:
            js("asUser('%s')" % role)
            time.sleep(1.0)
            for s in screens:
                if not js("allowed('%s')" % s["id"]):
                    continue
                js("location.hash = L('%s'); 1" % s["id"])
                time.sleep(1.4)
                txt = js("document.querySelector('#main').innerText") or ""
                shot(f"screen-{role}-{s['id']}".replace("/", "_"))
                report["screens"].append({"role": role, "screen": s["id"], "chars": len(txt)})
                if len(txt.strip()) < 20:
                    fails.append(f"screen '{s['id']}' as {role} rendered (almost) nothing")
        c.pump(1.0)

        # ---- 3. everything the browser complained about
        problems = []
        for e in c.events:
            m, p = e.get("method"), e.get("params", {})
            if m == "Runtime.exceptionThrown":
                d = p["exceptionDetails"]
                problems.append(("exception", (d.get("exception", {}).get("description") or d.get("text", ""))[:200]))
            elif m == "Runtime.consoleAPICalled" and p.get("type") in ("error", "warning"):
                problems.append((p["type"], " ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", []))[:200]))
            elif m == "Log.entryAdded" and p["entry"].get("level") == "error":
                problems.append(("log-error", (p["entry"].get("text", "") + " " + p["entry"].get("url", ""))[:200]))
            elif m == "Network.responseReceived" and p["response"]["status"] >= 400:
                problems.append((f"http-{p['response']['status']}", p["response"]["url"].replace(base, "")[:120]))
            elif m == "Network.loadingFailed":
                problems.append(("request-failed", str(p.get("errorText", ""))[:120]))
        unexpected = [x for x in problems if not (x[0] == "http-403" and x[1] == EXPECTED_403) and not (x[0] == "log-error" and "403" in x[1] and EXPECTED_403 in x[1])]
        report["browser_problems"], report["unexpected_problems"] = problems, unexpected
        fails += [f"browser: {a} {b}" for a, b in unexpected]
    finally:
        proc.terminate()
    report["failures"] = fails
    json.dump(report, open(os.path.join(out, "report.json"), "w"), indent=1, default=str)
    return report, fails


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default=os.environ.get("DEMO_URL", "http://127.0.0.1:8808"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "rehearsal", time.strftime("%Y%m%d-%H%M%S")))
    ap.add_argument("--chrome", default=None)
    a = ap.parse_args()
    try:
        urllib.request.urlopen(a.url + "/", timeout=5)
    except Exception:
        sys.exit(f"The demo is not answering at {a.url}. Start it first:  .venv/bin/python -m layer8_demo.server")
    report, fails = run(a.url, a.out, find_chrome(a.chrome))
    steps = report["story"]
    print(f"story: {sum(1 for s in steps if s['ok'])}/{len(steps)} steps ok | screens opened: {len(report['screens'])} | expected 403s: {len(report['browser_problems']) - len(report['unexpected_problems'])}")
    for s in steps:
        print(f"  {'ok ' if s['ok'] else 'BAD'} {s.get('i', 0) + 1:>2} {s.get('title', '')} {s.get('facts') or ''}"[:200])
    print(f"report + screenshots: {a.out}")
    if fails:
        print("\nBROKEN:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("\nCLEAN: the whole story and every screen ran without a failure.")


if __name__ == "__main__":
    main()
