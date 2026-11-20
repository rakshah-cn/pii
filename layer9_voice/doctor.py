"""Start-up check for the voice / chat demo.   .venv/bin/python -m layer9_voice.doctor [--live] [--quiet]

Prints what is ready and what is missing, with the exact fix for each item. Exit code 1 only when something that STOPS the demo is missing (a 'fail'); 'warn' means it runs but degraded.
It never prints a secret: only whether a name is present in .env.
  --live    also makes two tiny real calls to Sarvam (one chat reply, one spoken line) to prove the key works. Costs next to nothing, sends only made-up text.
  --quiet   print only warnings and failures"""
import importlib
import os
import socket
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OK, WARN, FAIL = "ok", "warn", "fail"


def env_names():
    """Names defined in .env (never values)."""
    names = set()
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if v.strip().strip("'\""):
                        names.add(k.strip())
    return names


def port_state(port):
    """'free', 'voice' (our console with the voice module), 'console' (the console WITHOUT it) or 'other'."""
    with socket.socket() as s:
        s.settimeout(0.4)
        if s.connect_ex(("127.0.0.1", port)) != 0:
            return "free"
    def code(path):
        try:
            return urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=1.5).status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception:                                                 # noqa: BLE001
            return None
    if code("/voice/status") == 200:
        return "voice"
    return "console" if code("/api/status") == 200 else "other"


def first_free_port(start=8808, end=8830):
    for p in range(start, end + 1):
        if port_state(p) == "free":
            return p
    return None


def checks(live=False):
    out = []
    add = lambda name, st, msg, fix="": out.append((name, st, msg, fix))
    py_ok = sys.version_info >= (3, 10)
    add("Python", OK if py_ok else FAIL, f"{sys.version.split()[0]} at {sys.executable}", "" if py_ok else "Use the project environment: .venv/bin/python")
    if ".venv" not in sys.executable:
        add("Environment", WARN, "not running from the project's .venv", "Run it as: .venv/bin/python -m layer9_voice.doctor")
    missing = []
    for mod in ("fastapi", "uvicorn", "httpx", "pydantic", "starlette", "langgraph", "onnxruntime"):
        try:
            importlib.import_module(mod)
        except Exception:                                                  # noqa: BLE001
            missing.append(mod)
    add("Packages", OK if not missing else FAIL, "all present" if not missing else "missing: " + ", ".join(missing), "" if not missing else f".venv/bin/pip install {' '.join(missing)}")
    names = env_names()
    add(".env file", OK if os.path.exists(os.path.join(ROOT, ".env")) else FAIL, "found" if os.path.exists(os.path.join(ROOT, ".env")) else "missing", "Create .env in the project folder (see README)")
    add("Vault master key", OK if "VAULT_MASTER_KEY" in names else FAIL, "VAULT_MASTER_KEY is set" if "VAULT_MASTER_KEY" in names else "VAULT_MASTER_KEY is not set: the vault refuses to start",
        "" if "VAULT_MASTER_KEY" in names else "Add VAULT_MASTER_KEY=... to .env")
    has_key = "SARVAM_API_KEY" in names or bool(os.environ.get("SARVAM_API_KEY"))
    add("Sarvam key", OK if has_key else WARN, "SARVAM_API_KEY is set (model, speech-to-text and text-to-speech)" if has_key else "no SARVAM_API_KEY: the bot runs in stub mode (no real replies or dispositions)",
        "" if has_key else "Add SARVAM_API_KEY=... to .env")
    models = os.path.join(ROOT, "models")
    n_models = sum(len(f) for _, _, f in os.walk(models)) if os.path.isdir(models) else 0
    add("Detector model files", OK if n_models else WARN, f"{n_models} file(s) in models/" if n_models else "models/ is empty or missing: the detector falls back to rules only", "" if n_models else "Restore the models/ folder")
    console = os.path.join(ROOT, "dpdp-privacy-console.html")
    add("Console page", OK if os.path.exists(console) else FAIL, "dpdp-privacy-console.html found" if os.path.exists(console) else "dpdp-privacy-console.html is missing", "" if os.path.exists(console) else "Restore it from git")
    try:
        from layer9_voice import borrowers, config
        probs = config.errors()
        add("Settings file", OK if not probs else WARN, f"{'custom (config.local.json)' if config.source() == 'local' else 'built-in defaults'}" if not probs else "; ".join(probs)[:200],
            "" if not probs else "Fix or delete layer9_voice/config.local.json, or press Reset in the Settings tab")
        rows = borrowers.load()
        langs = set(config.get()["languages"])
        unknown = sorted({r.get("language", "English") for r in rows} - langs)
        add("Borrower list", OK if rows and not unknown else (WARN if rows else FAIL), f"{len(rows)} borrower(s) from the {borrowers.source()} list" if rows and not unknown else
            (f"{len(rows)} borrower(s), but language(s) not configured: {', '.join(unknown)} (the English lines are used)" if rows else "no borrowers"), "" if rows and not unknown else "Settings tab: import a CSV or add borrowers")
    except Exception as e:                                                 # noqa: BLE001
        add("Settings file", FAIL, f"could not load the configuration ({type(e).__name__})", "Check layer9_voice/config.local.json")
    p = first_free_port()
    states = {8808: port_state(8808)}
    if states[8808] == "free":
        add("Port", OK, "8808 is free: the console will open there")
    elif p:
        why = {"console": "the console WITHOUT the voice module is already running there (not stopped by this script)", "voice": "a voice console is already running there", "other": "another program uses it"}[states[8808]]
        add("Port", WARN, f"8808 is busy ({why}); the demo will use {p}", f"Or stop the program on 8808 yourself and run again to use 8808")
    else:
        add("Port", FAIL, "no free port between 8808 and 8830", "Stop one of the running consoles")
    if live and has_key:
        try:
            from layer9_voice.sarvam import SarvamClient
            c = SarvamClient(timeout=25.0)
            from layer9_voice import config as _c
            m = _c.get()["models"]
            r = c.chat([{"role": "user", "content": "Reply with the single word OK."}], model=m["bot"], max_tokens=20)
            add("Live: model", OK, f"{m['bot']} answered ({len(r)} chars)")
            audio = c.synthesize("Hello.", "English")
            add("Live: speech out", OK, f"{m['tts_model']} / {m['tts_speaker']} returned {len(audio)} bytes")
        except Exception as e:                                             # noqa: BLE001
            add("Live: Sarvam", FAIL, f"call failed: {e}", "Check SARVAM_API_KEY, the model names in Settings and your internet connection")
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    live, quiet = "--live" in argv, "--quiet" in argv
    res = checks(live)
    mark = {OK: "  ok  ", WARN: " WARN ", FAIL: " FAIL "}
    for name, st, msg, fix in res:
        if quiet and st == OK:
            continue
        print(f"[{mark[st]}] {name:<22} {msg}")
        if fix and st != OK:
            print(f"{'':>10}-> {fix}")
    fails = [r for r in res if r[1] == FAIL]
    warns = [r for r in res if r[1] == WARN]
    if not quiet or fails or warns:
        print(("\nNot ready: " + str(len(fails)) + " blocking problem(s).") if fails else ("\nReady" + (f" ({len(warns)} warning(s))." if warns else ".")))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
