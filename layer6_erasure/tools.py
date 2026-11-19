"""Discovery tools. The ERASURE AGENT only ever receives their outputs, which are ids, counts and evidence kinds.
Raw personal values never reach this layer at all: the sealed methods hand text to the vault, which matches inside its own
trust boundary and returns only categories, scores or a bool (layer2_vault/core/sealed_match.py).

Evidence kinds:  registry | conversation | derivative | raw_strong | raw_address | raw_name_unique |
                 raw_name_corroborated | raw_name_ambiguous | embedding_probe | embedding_probe_text
Strength:        certain (delete) | probabilistic (delete, flagged in audit) | ambiguous (never auto-deleted)
"""
from .embed import embed

CONV_KEYS = ("conversation_id", "conv", "conversation", "session_id")
PARENT_KEYS = ("parent_chunk_id", "parent_id", "source_record_id", "source_id", "derived_from")
T_HI, T_LO = 0.50, 0.30          # embedding-probe thresholds: tuned on the 10 PUBLIC requests only


def hit(store, rid, evidence, strength, snippet="", score=None):
    h = {"store": store, "id": rid, "evidence": evidence, "strength": strength, "snippet": snippet}
    if score is not None:
        h["score"] = round(score, 3)
    return h


# ------------------------------------------------------------------ sealed scanning (matching happens INSIDE the vault)
class SealedScanner:
    """The agent is BLIND to personal data. Every method here sends store text (or vectors) to the vault and gets back a
    category per text ('strong' | 'address' | 'name_full' | 'name_single' | None), a score per vector row, or a bool.
    There is no call that returns a borrower's values, so none can reach agent state, traces or the audit record."""

    def __init__(self, vault, registry):
        self.vault, self.registry = vault, registry

    @staticmethod
    def _flat(adapters):
        return [(a.name, rid, text) for a in adapters for rid, text, _meta in a.records()]

    def raw_sweep(self, adapters, borrower_id, convs):
        """Search every store's text for the borrower's raw values (the vault does the matching). Evidence strength is decided here."""
        rows = self._flat(adapters)
        kinds = self.vault.erasure_scan(borrower_id, [t for _, _, t in rows]) if rows else []
        unique = not self.registry.borrowers_sharing_name(borrower_id)
        out = []
        for (store, rid, text), k in zip(rows, kinds):
            if k == "strong":
                out.append(hit(store, rid, "raw_strong", "certain"))
            elif k == "address":
                out.append(hit(store, rid, "raw_address", "certain"))
            elif k in ("name_full", "name_single"):
                # NAME-ONLY matches are weak evidence: a one-word name ('Sanjay') is far too common, and a
                # full name may be shared. Delete only when something ties the record to THIS borrower.
                if any(c in text for c in convs):
                    out.append(hit(store, rid, "raw_name_corroborated", "certain"))
                elif k == "name_full" and unique:
                    out.append(hit(store, rid, "raw_name_unique", "certain"))
                elif k == "name_single":
                    out.append(hit(store, rid, "raw_name_single_token", "ambiguous"))
                else:
                    out.append(hit(store, rid, "raw_name_ambiguous", "ambiguous"))
        return out

    def collapse_scan(self, adapters, borrower_id):
        """SECOND, DIFFERENT raw matcher (inside the vault): drop everything except letters/digits, then plain substring search.
        Independent of the regex matcher in raw_sweep, so a separator/format gap in one does not hide a copy from both.
        Only long identifiers (>= 8 digits/chars) are used, to avoid joining unrelated short numbers."""
        rows = self._flat(adapters)
        kinds = self.vault.erasure_scan(borrower_id, [t for _, _, t in rows], mode="collapsed") if rows else []
        return [hit(store, rid, "raw_strong_collapsed", "certain") for (store, rid, _), k in zip(rows, kinds) if k]

    def file_grep(self, root, borrower_id, managed_paths, skip_names, max_bytes=8_000_000):
        """INDEPENDENT check: byte-level search of EVERY file under root (any extension) that no adapter manages.
        Catches stores in formats the adapters cannot read (csv, txt, ...). Returns file-level hits (ambiguous: a
        human or a new adapter must handle them; the agent will not edit files it cannot parse). The vault does the matching."""
        import os
        paths, texts = [], []
        for dp, _, fs in os.walk(root):
            for f in fs:
                path = os.path.abspath(os.path.join(dp, f))
                if path in managed_paths or f in skip_names or f.startswith("."):
                    continue
                try:
                    if os.path.getsize(path) > max_bytes:
                        continue
                    texts.append(open(path, "rb").read().decode("utf-8", "ignore"))
                except OSError:
                    continue
                paths.append(path)
        kinds = self.vault.erasure_scan(borrower_id, texts) if texts else []
        return [hit("file:" + os.path.relpath(p, root), "-", "unmanaged_file", "ambiguous",
                    "(file holds this borrower's values; no adapter can edit it)")
                for p, k in zip(paths, kinds) if k in ("strong", "address", "name_full")]

    def vector_probe(self, adapters, borrower_id):
        """Nearest-neighbour probe built from the borrower's own identifiers INSIDE the vault (the query vector never leaves it).
        Finds payload-less embeddings. The vault gets (row id, vector) pairs and returns (row id, score) pairs."""
        out = []
        for a in adapters:
            if not a.has_embeddings:
                continue
            rows = {rid: (text, meta) for rid, text, meta in a.records()}
            for rid, score in self.vault.erasure_probe(borrower_id, a.embeddings(), embed, T_LO):
                text, meta = rows.get(rid, ("", {}))
                if meta.get("text"):                   # has a payload: exact sweeps already judge it
                    if score >= T_HI and self.vault.erasure_scan(borrower_id, [text])[0] not in ("strong", "address", "name_full"):
                        out.append(hit(a.name, rid, "embedding_probe_text", "ambiguous", "", score))
                    continue
                strength = "probabilistic" if score >= T_HI else "ambiguous"
                out.append(hit(a.name, rid, "embedding_probe", strength, "(embedding row, no payload)", score))
        return out


# ------------------------------------------------------------------ plain (non-sealed) tools
def registry_sweep(adapters, registry, borrower_id):
    known = registry.known_for(borrower_id)
    want = {"logs": set(known.get("log", [])), "vector_store": set(known.get("chunk", [])) | set(known.get("emb", [])),
            "eval_sets": set(known.get("eval", []))}
    out = []
    for a in adapters:
        w = want.get(a.name)
        if w:
            out += [hit(a.name, rid, "registry", "certain") for rid, _, _ in a.records() if rid in w]
    return out


def conversation_sweep(adapters, convs):
    out = []
    cs = list(convs)
    for a in adapters:
        for rid, text, meta in a.records():
            if any(meta.get(k) in convs for k in CONV_KEYS) or any(c in text for c in cs):
                out.append(hit(a.name, rid, "conversation", "certain"))
    return out


def derivative_sweep(adapters, pivots):
    """Records whose parent/source link points at anything in `pivots`. Every matching record that STILL EXISTS is
    reported (even if its own id is already a pivot: it may have survived an earlier delete). The pivot set grows with
    each match and the sweep repeats until it stops growing (derived-of-derived)."""
    found, seen, pv = [], set(), set(pivots)
    while True:
        grew = False
        for a in adapters:
            for rid, text, meta in a.records():
                if (a.name, rid) in seen:
                    continue
                if any(meta.get(k) in pv for k in PARENT_KEYS):
                    seen.add((a.name, rid))
                    found.append(hit(a.name, rid, "derivative", "certain"))
                    if rid not in pv:
                        pv.add(rid)
                        grew = True
        if not grew:
            return found
