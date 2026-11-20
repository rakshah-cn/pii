"""The list of people the bot may call or chat with.

  borrowers.local.json   (git-ignored: YOUR list, real names) wins over  borrowers.example.json  (fake, committed).
Import a CSV from the Settings tab (or call import_csv): columns  name, language  are required; product, outstanding, dpd, loan_last4, phone, id are optional.

Rules:
  * this list is personal data: it lives only in the git-ignored local file and is never sent to a model, a log or an outside service. The model sees none of it: the bot's prompt gets only
    the product, the overdue amount and 'already late'.
  * `phone` is display-only (shown masked). NOTHING in this package dials a number (rule: no call to the team's real numbers).
  * every row is validated; one bad row is reported by line number and never half-imported."""
import csv
import io
import json
import os
import re
import tempfile
import threading

from layer9_voice import config

HERE = os.path.dirname(os.path.abspath(__file__))
LOCAL = os.environ.get("VOICE_BORROWERS_PATH") or os.path.join(HERE, "borrowers.local.json")
EXAMPLE = os.path.join(HERE, "borrowers.example.json")
MAX_ROWS, MAX_CSV_BYTES = 500, 200_000
_LOCK = threading.RLock()


def source():
    return "local" if os.path.exists(LOCAL) else ("example" if os.path.exists(EXAMPLE) else "none")


def load():
    path = LOCAL if os.path.exists(LOCAL) else (EXAMPLE if os.path.exists(EXAMPLE) else None)
    if path is None:
        return []
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    return rows if isinstance(rows, list) else []


def mask_phone(p):
    d = "".join(ch for ch in str(p or "") if ch.isdigit())
    return ("•" * max(0, len(d) - 4)) + d[-4:] if d else ""


def public(rows=None):
    """What the screens may show: no full phone number."""
    return [{"id": b["id"], "name": b["name"], "language": b.get("language", "English"), "product": b.get("product"), "outstanding": b.get("outstanding"), "dpd": b.get("dpd"),
             "loan_last4": b.get("loan_last4"), "phone": mask_phone(b.get("phone"))} for b in (load() if rows is None else rows)]


def _int(v, lo, hi, field, errs, where):
    if v in (None, ""):
        return None
    try:
        n = int(float(str(v).replace(",", "").strip()))
    except ValueError:
        errs.append(f"{where}: {field} must be a number")
        return None
    if not lo <= n <= hi:
        errs.append(f"{where}: {field} must be between {lo} and {hi}")
        return None
    return n


def clean_row(raw, where="row"):
    """-> (clean row or None, [problems])."""
    errs = []
    g = lambda k: (raw.get(k) if raw.get(k) is not None else "")
    name = str(g("name")).strip()
    if not 1 <= len(name) <= 80:
        errs.append(f"{where}: name is required (1-80 characters)")
    lang_in = str(g("language")).strip() or "English"
    lname, entry = config.language(lang_in)
    if lname.casefold() != lang_in.casefold() and entry["code"] != lang_in:
        errs.append(f"{where}: language '{lang_in}' is not configured (configured: {', '.join(config.get()['languages'])})")
    product = str(g("product")).strip()[:40] or "Personal loan"
    outstanding = _int(g("outstanding"), 0, 10**9, "outstanding", errs, where)
    dpd = _int(g("dpd"), 0, 3650, "dpd", errs, where)
    last4 = re.sub(r"\D", "", str(g("loan_last4") or g("loan_ref")))[-4:]
    phone = str(g("phone")).strip()
    if phone and not re.fullmatch(r"\+?[0-9 \-]{6,18}", phone):
        errs.append(f"{where}: phone looks wrong")
    bid = str(g("id")).strip()
    if bid and not re.fullmatch(r"[A-Za-z0-9_\-]{1,32}", bid):
        errs.append(f"{where}: id may only have letters, digits, - and _ (1-32)")
    if errs:
        return None, errs
    return {"id": bid, "name": name, "language": lname, "product": product, "outstanding": outstanding or 0, "dpd": dpd or 0, "loan_last4": last4 or "0000", "phone": phone}, []


def _assign_ids(rows):
    used = {r["id"] for r in rows if r["id"]}
    n = 0
    for r in rows:
        if not r["id"]:
            while True:
                n += 1
                cand = f"b-{n:03d}"
                if cand not in used:
                    break
            r["id"] = cand
            used.add(cand)
    return rows


def save(rows):
    with _LOCK:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(LOCAL) or ".", prefix=".borrowers.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        os.replace(tmp, LOCAL)
        try:
            os.chmod(LOCAL, 0o600)                                # real names: readable by this user only
        except OSError:
            pass


def import_csv(text, replace=True):
    """-> (ok, imported_count, problems). All-or-nothing: any bad row means nothing is written."""
    if not isinstance(text, str) or not text.strip():
        return False, 0, ["the CSV is empty"]
    if len(text.encode("utf-8")) > MAX_CSV_BYTES:
        return False, 0, [f"the CSV is larger than {MAX_CSV_BYTES // 1000} KB"]
    try:
        dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rd = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not rd.fieldnames:
        return False, 0, ["the CSV has no header row"]
    rd.fieldnames = [str(h or "").strip().lower().replace(" ", "_") for h in rd.fieldnames]
    if "name" not in rd.fieldnames:
        return False, 0, ["the header row must have a 'name' column (language is recommended; optional: product, outstanding, dpd, loan_last4, phone, id)"]
    rows, problems = [], []
    for i, raw in enumerate(rd, start=2):
        if not any((v or "").strip() for v in raw.values() if isinstance(v, str)):
            continue
        row, errs = clean_row({k: v for k, v in raw.items() if k}, f"line {i}")
        problems += errs
        if row:
            rows.append(row)
        if len(rows) > MAX_ROWS:
            return False, 0, [f"more than {MAX_ROWS} rows"]
    if problems:
        return False, 0, problems[:25]
    if not rows:
        return False, 0, ["no data rows found"]
    base = [] if replace else [dict(r) for r in load()] if source() == "local" else []
    allrows = base + rows
    ids = [r["id"] for r in allrows if r["id"]]
    if len(set(ids)) != len(ids):
        return False, 0, ["duplicate id in the list"]
    save(_assign_ids(allrows))
    return True, len(rows), []


def add(raw):
    row, errs = clean_row(raw, "row")
    if errs:
        return False, errs
    cur = [dict(r) for r in load()] if source() == "local" else []
    if row["id"] and any(r["id"] == row["id"] for r in cur):
        return False, ["that id already exists"]
    if len(cur) >= MAX_ROWS:
        return False, [f"more than {MAX_ROWS} rows"]
    save(_assign_ids(cur + [row]))
    return True, []


def remove(bid):
    cur = [dict(r) for r in load()] if source() == "local" else [dict(r) for r in load()]
    left = [r for r in cur if r["id"] != bid]
    if len(left) == len(cur):
        return False
    save(left)
    return True


def reset():
    with _LOCK:
        try:
            os.unlink(LOCAL)
        except FileNotFoundError:
            pass
