"""MOCK STORE GENERATOR  (ASSUMPTION: the organisers' real store layout is not available to us).

Seeds 50 fake borrowers with copies of their data in:
  logs.jsonl          masked messages with token references (+ some ACCIDENTAL raw copies)
  vectors.jsonl       chunks + embeddings (+ derived summary chunks, + raw chunks, + orphan embeddings)
  eval_sets.jsonl     items derived from conversations (only for borrowers whose consent allows it)
  legacy_export/*.jsonl   UNMAPPED stores nobody registered
and a lineage registry that deliberately does NOT know about every copy.

truth.json lists every seeded copy (used ONLY by the evaluator, never by the agent). All values are fake.
"""
import copy
import json
import os
import random
import shutil

from layer0_data import pii_gen
from layer4_consent.write_guard import WriteGuard, new_tag
from layer5_lineage.registry import LineageRegistry, ref

from .adapters import legacy_id
from .embed import embed

NAME_LANGS = ["en", "en", "hi", "kn", "ta"]
COLLISION_PAIRS = [("B0007", "B0008"), ("B0021", "B0022"), ("B0041", "B0042")]   # share an identical name on purpose


class _ListStore:
    def __init__(self): self.rows = []
    def put(self, r): self.rows.append(r)


def _lines(m, which):
    L = [("Agent", f"Hello, am I speaking with {m['name']}?"), ("Customer", "Yes, speaking."),
         ("Agent", "Your EMI is due soon. Please confirm your details.")]
    if "phone" in which:
        L += [("Agent", "Registered mobile number?"), ("Customer", f"It is {m['phone']}.")]
    if "aadhaar" in which:
        L += [("Agent", "Aadhaar for KYC?"), ("Customer", f"My Aadhaar is {m['aadhaar']}.")]
    if "account" in which:
        L += [("Agent", "Which account will you pay from?"), ("Customer", f"Account number {m['account']}.")]
    if "pan" in which:
        L += [("Agent", "PAN please."), ("Customer", f"PAN is {m['pan']}.")]
    if "address" in which:
        L += [("Agent", "Current address?"), ("Customer", f"I live at {m['address']}.")]
    L.append(("Customer", "Okay, thank you."))
    return L


CONV_FIELDS = [("phone", "aadhaar"), ("account", "pan"), ("address", "phone")]
LABEL = {"name": "PERSON", "phone": "PHONE", "aadhaar": "AADHAAR", "pan": "PAN", "account": "ACCOUNT", "address": "ADDRESS"}


def seed(root, n=50, seed_=7, vault=None):
    """Write the mock stores under `root`. Returns the truth dict. If `vault` is given, also fills it."""
    rng = random.Random(seed_)
    os.makedirs(os.path.join(root, "legacy_export"), exist_ok=True)
    guard = WriteGuard(os.path.join(root, "rejections.jsonl"))
    open(os.path.join(root, "rejections.jsonl"), "w").close()
    reg = LineageRegistry()
    logs, vecs, evals, crm, ana = _ListStore(), _ListStore(), _ListStore(), [], []
    ctr = {"L": 0, "V": 0, "E": 0}

    def nid(k):
        ctr[k] += 1
        return f"{k}{ctr[k]:05d}"

    ids = [f"B{i:04d}" for i in range(1, n + 1)]
    vals = {}
    for b in ids:
        lang = rng.choice(NAME_LANGS)
        vals[b] = dict(name=pii_gen.fake_name(lang, rng), phone=pii_gen.fake_phone(rng), aadhaar=pii_gen.fake_aadhaar(rng),
                       pan=pii_gen.fake_pan(rng), account=pii_gen.fake_account(rng), address=pii_gen.fake_address(lang, rng))
    for a, b in COLLISION_PAIRS:
        vals[b]["name"] = vals[a]["name"]
    truth = {"MOCK": True, "note": "all fake; evaluator only; agents must not read this", "borrowers": {}, "decoys": []}

    for b in ids:
        v = vals[b]
        T = {"values": v, "conversations": [], "copies": []}
        truth["borrowers"][b] = T
        reg.add_borrower(b, name=v["name"])
        consenting = rng.random() < 0.6
        convs = [f"c_{b}_{k}" for k in (1, 2, 3)]
        T["conversations"] = convs
        T["consenting"] = consenting
        hide_conv3 = rng.random() < 0.2                      # registry never learns conversation 3
        for k, c in enumerate(convs):
            if not (hide_conv3 and k == 2):
                reg.add_conversation(b, c)
        registered_logs, registered_chunks = [], []
        consent_id = f"CN-{b}" if consenting else None

        for k, c in enumerate(convs):
            which = CONV_FIELDS[k]
            fields = ["name"] + list(which)
            toks = {}
            for f in fields:
                if vault is not None:
                    toks[f] = vault.put(c, LABEL[f], v[f], borrower_id=b, purpose="service", consent_id=consent_id)
                else:
                    toks[f] = f"<{LABEL[f]}_1>"
            masked = " | ".join(f"[{s}]: {t}" for s, t in _lines({**{f: toks.get(f, "") for f in v}}, which))
            raw_text = " | ".join(f"[{s}]: {t}" for s, t in _lines(v, which))
            vault_refs = [ref("vault", f"{toks[f]}@{c}") for f in fields]
            # --- log records (masked, tokenised) ---
            for j in range(2):
                lid = nid("L")
                rec = {"id": lid, "conversation_id": c, "text": masked if j == 0 else masked.split(" | ")[0] + " | " + masked.split(" | ")[-1],
                       "consent": new_tag("service", note="loan servicing")}
                guard.guarded_write("logs", logs, rec)
                registered = rng.random() < 0.8
                T["copies"].append(["logs", lid, "log_registered" if registered else "log_unregistered_tokenised"])
                if registered:
                    reg.register(b, ref("log", lid), conversation_id=c, token=toks["name"])
                    registered_logs.append(lid)
            for vr in vault_refs:
                reg.register(b, vr, parent=ref("log", registered_logs[-1]) if registered_logs else None, relation="vault_entry_for")
            # --- vector chunks (masked) ---
            for j in range(2):
                vid = nid("V")
                txt = masked if j == 0 else masked.split(" | ")[-2]
                rec = {"id": vid, "text": txt, "conversation_id": c, "embedding": [round(float(x), 4) for x in embed(txt)],
                       "consent": new_tag("service")}
                guard.guarded_write("vector_store", vecs, rec)
                registered = rng.random() < 0.75
                T["copies"].append(["vector_store", vid, "chunk_registered" if registered else "chunk_unregistered_tokenised"])
                if registered:
                    par = ref("log", registered_logs[-1]) if registered_logs else None
                    reg.register(b, ref("chunk", vid), parent=par, relation="chunked_from", conversation_id=c)
                    reg.register(b, ref("emb", vid), parent=ref("chunk", vid), relation="embedding_of")
                    registered_chunks.append(vid)
            # --- eval items (policy-gated: only with explicit consent) ---
            for j in range(1 + (k == 0)):
                eid = nid("E")
                rec = {"id": eid, "conversation_id": c, "prompt": masked, "expected": "intent=payment_promise",
                       "consent": new_tag("model_improvement", consent_id=consent_id, explicit=consenting)}
                wrote, _ = guard.guarded_write("eval_sets", evals, rec)
                if wrote:
                    registered = rng.random() < 0.7
                    T["copies"].append(["eval_sets", eid, "eval_registered" if registered else "eval_unregistered_tokenised"])
                    if registered:
                        reg.register(b, ref("eval", eid), parent=ref("log", registered_logs[-1]) if registered_logs else None,
                                     relation="eval_item_from", conversation_id=c)
                else:
                    ctr["E"] -= 1

        # --- accidental / derived / unmapped copies the registry does NOT know about ---
        if rng.random() < 0.40:                                           # raw copy in logs (debug print)
            lid = nid("L")
            logs.put({"id": lid, "conversation_id": None, "consent": new_tag("service"),
                      "text": f"DEBUG payload name={v['name']} mobile={v['phone']} status=retry"})
            T["copies"].append(["logs", lid, "log_raw_leak"])
        if rng.random() < 0.20:                                           # raw spaced Aadhaar in logs
            lid = nid("L")
            a = v["aadhaar"]
            logs.put({"id": lid, "conversation_id": None, "consent": new_tag("service"),
                      "text": f"kyc check {a[:4]} {a[4:8]} {a[8:]} result=ok"})
            T["copies"].append(["logs", lid, "log_raw_leak_spaced"])
        if rng.random() < 0.40:                                           # raw chunk in the vector store
            vid = nid("V")
            txt = f"Call note: {v['name']} reachable on {v['phone']} about EMI"
            vecs.put({"id": vid, "text": txt, "conversation_id": None, "embedding": [round(float(x), 4) for x in embed(txt)],
                      "consent": new_tag("service")})
            T["copies"].append(["vector_store", vid, "vector_raw_chunk"])
        if rng.random() < 0.40 and registered_chunks:                     # derived summary chunk (parent link only)
            vid = nid("V")
            txt = "Summary: customer discussed EMI schedule and promised payment"
            vecs.put({"id": vid, "text": txt, "conversation_id": None, "parent_chunk_id": rng.choice(registered_chunks),
                      "embedding": [round(float(x), 4) for x in embed(txt)], "consent": new_tag("service")})
            T["copies"].append(["vector_store", vid, "vector_derived_summary"])
        if rng.random() < 0.25:                                           # orphan embedding: vector only, no payload
            vid = nid("V")
            otxt = f"{v['name']} {v['phone']} {v['account']} loan emi pending"
            vecs.put({"id": vid, "text": None, "conversation_id": None, "embedding": [round(float(x), 4) for x in embed(otxt)],
                      "consent": new_tag("service")})
            T["copies"].append(["vector_store", vid, "embedding_orphan"])
        if consenting and rng.random() < 0.40 and registered_logs:        # derived eval item (source link only)
            eid = nid("E")
            evals.put({"id": eid, "conversation_id": None, "source_record_id": rng.choice(registered_logs),
                       "prompt": "Classify the borrower's intent", "expected": "payment_promise",
                       "consent": new_tag("model_improvement", consent_id=consent_id, explicit=True)})
            T["copies"].append(["eval_sets", eid, "eval_derived"])
        if consenting and rng.random() < 0.15:                            # name-only eval prompt
            eid = nid("E")
            evals.put({"id": eid, "conversation_id": None, "prompt": f"Summarise the repayment history of {v['name']}",
                       "expected": "n/a", "consent": new_tag("model_improvement", consent_id=consent_id, explicit=True)})
            T["copies"].append(["eval_sets", eid, "eval_name_only"])
        if rng.random() < 0.40:                                           # unmapped CRM dump
            row = {"cust_ref": f"CR-{rng.randint(10000, 99999)}", "full_name": v["name"], "mobile": v["phone"],
                   "acct": v["account"], "notes": "loan follow-up pending"}
            T["copies"].append(["unmapped:legacy_export/crm_dump.jsonl",
                                legacy_id("crm_dump", json.dumps(row, ensure_ascii=False)), "unmapped_crm"])
            crm.append(row)
        if rng.random() < 0.25:                                           # unmapped analytics cache (conversation ref only)
            row = {"session_ref": f"analytics/{rng.choice(convs)}/summary", "payload": "EMI reminder outcome=promise"}
            T["copies"].append(["unmapped:legacy_export/analytics_cache.jsonl",
                                legacy_id("analytics_cache", json.dumps(row, ensure_ascii=False)), "unmapped_analytics_conv"])
            ana.append(row)

    # --- decoys: look-alikes that must SURVIVE every erasure ---
    for b in rng.sample(ids, 20):
        ph = vals[b]["phone"]
        near = ph[:-1] + str((int(ph[-1]) + 1) % 10)
        lid = nid("L")
        logs.put({"id": lid, "conversation_id": None, "consent": new_tag("service"), "text": f"callback ref {near} queued"})
        truth["decoys"].append(["logs", lid])
        lid = nid("L")
        logs.put({"id": lid, "conversation_id": None, "consent": new_tag("service"),
                  "text": f"Customer {vals[b]['name'].split()[0]} asked about prepayment charges"})
        truth["decoys"].append(["logs", lid])

    def dump(path, rows):
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    dump(os.path.join(root, "logs.jsonl"), logs.rows)
    dump(os.path.join(root, "vectors.jsonl"), vecs.rows)
    dump(os.path.join(root, "eval_sets.jsonl"), evals.rows)
    dump(os.path.join(root, "legacy_export", "crm_dump.jsonl"), crm)
    dump(os.path.join(root, "legacy_export", "analytics_cache.jsonl"), ana)
    reg.save(os.path.join(root, "registry.json"))
    pool = [b for b in ids if all(b not in p for p in COLLISION_PAIRS)]
    pub = rng.sample(pool, 10)
    held = rng.sample([b for b in ids if b not in pub], 20)
    json.dump([{"request_id": f"ER-{i + 1:03d}", "borrower_id": b} for i, b in enumerate(pub)],
              open(os.path.join(root, "requests_public.json"), "w"), indent=1)
    json.dump([{"request_id": f"HX-{i + 1:03d}", "borrower_id": b} for i, b in enumerate(held)],
              open(os.path.join(root, "requests_heldout.json"), "w"), indent=1)
    truth["policy_rejections_during_seeding"] = len(guard.rejections)
    json.dump(truth, open(os.path.join(root, "truth.json"), "w", encoding="utf-8"), ensure_ascii=False)
    return truth


def seed_vault(vault, truth):
    """Fill a (fresh) vault from truth: one entry per (conversation, field) the seeding used."""
    for b, T in truth["borrowers"].items():
        v = T["values"]
        for k, c in enumerate(T["conversations"]):
            for f in ["name"] + list(CONV_FIELDS[k]):
                vault.put(c, LABEL[f], v[f], borrower_id=b, purpose="service", consent_id=f"CN-{b}" if T["consenting"] else None)


def make_work_copy(pristine, work):
    if os.path.exists(work):
        shutil.rmtree(work)
    shutil.copytree(pristine, work)
