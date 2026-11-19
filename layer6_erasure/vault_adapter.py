"""Use the real layer2 vault when it exposes the erasure contract; otherwise a FALLBACK fake with the same API.

Contract: put(conversation_id,label,raw,borrower_id,purpose,consent_id)->token, tokens(conversation_id),
destroy(conversation_id), entries_for_borrower(borrower_id) [no raw], has_key(borrower_id), destroy_borrower(borrower_id, job_id=None),
access_log(), and the SEALED sweep (the vault matches inside itself; nothing returns a value):
erasure_scan(borrower_id, texts, mode) -> [category|None], erasure_probe(borrower_id, rows, embed_fn, threshold) -> [(id, score)],
find_unmapped_copies(borrower_id, corpus) -> bool.  There is NO decrypt_for_erasure: Layer 6 must not retrieve plaintext.
"""
import re
import time

NEEDED = ("entries_for_borrower", "has_key", "erasure_scan", "erasure_probe", "find_unmapped_copies", "destroy_borrower", "access_log")


class FallbackVault:
    """In-memory, UNENCRYPTED stand-in used only when the real vault lacks the contract. Tests label which is used."""

    def __init__(self):
        self._e = []           # entries: dict(borrower_id, conversation_id, label, token, raw, purpose, consent_id, created)
        self._keys = set()     # borrowers with a live key
        self._log = []

    def put(self, conversation_id, label, raw, borrower_id=None, purpose="service", consent_id=None):
        norm = re.sub(r"[\s-]", "", raw).casefold()
        for e in self._e:
            if e["conversation_id"] == conversation_id and e["label"] == label and e["norm"] == norm:
                return e["token"]
        n = 1 + sum(1 for e in self._e if e["conversation_id"] == conversation_id and e["label"] == label)
        tok = f"<{label}_{n}>"
        self._e.append(dict(borrower_id=borrower_id, conversation_id=conversation_id, label=label, token=tok, raw=raw,
                            norm=norm, purpose=purpose, consent_id=consent_id, created=time.time()))
        if borrower_id:
            self._keys.add(borrower_id)
        return tok

    def get(self, conversation_id, token):
        for e in self._e:
            if e["conversation_id"] == conversation_id and e["token"] == token:
                return e["raw"]

    def tokens(self, conversation_id):
        return [e["token"] for e in self._e if e["conversation_id"] == conversation_id]

    def destroy(self, conversation_id):
        n = len([e for e in self._e if e["conversation_id"] == conversation_id])
        self._e = [e for e in self._e if e["conversation_id"] != conversation_id]
        return n

    def entries_for_borrower(self, borrower_id):
        return [{k: e[k] for k in ("token", "label", "conversation_id", "created", "purpose", "consent_id")}
                for e in self._e if e["borrower_id"] == borrower_id]

    def has_key(self, borrower_id):
        return borrower_id in self._keys

    def _material(self, borrower_id, op, n):
        from layer2_vault.core.sealed_match import SealedMaterial
        self._log.append({"ts": time.time(), "action": op, "borrower_id": borrower_id, "items": n})
        return SealedMaterial([e["raw"] for e in self._e if e["borrower_id"] == borrower_id])

    def erasure_scan(self, borrower_id, texts, mode="values"):
        m = self._material(borrower_id, "erasure_scan", len(texts))
        return [m.classify_collapsed(t) if mode == "collapsed" else m.classify(t) for t in texts]

    def erasure_probe(self, borrower_id, rows, embed_fn, threshold=0.0):
        import numpy as np
        m = self._material(borrower_id, "erasure_probe", len(rows))
        if not m or not rows:
            return []
        q = np.asarray(embed_fn(m.probe_text()), dtype=np.float32)
        return [(rid, float(np.dot(q, v))) for rid, v in rows if len(v) == len(q) and float(np.dot(q, v)) >= threshold]

    def find_unmapped_copies(self, borrower_id, search_corpus):
        return any(k in ("strong", "address", "name_full") for k in self.erasure_scan(borrower_id, [search_corpus]))

    def destroy_borrower(self, borrower_id, role="erasure", job_id=None):
        n = len([e for e in self._e if e["borrower_id"] == borrower_id])
        self._e = [e for e in self._e if e["borrower_id"] != borrower_id]
        had = borrower_id in self._keys
        self._keys.discard(borrower_id)
        self._log.append({"ts": time.time(), "action": "destroy_borrower", "borrower_id": borrower_id})
        return {"entries_deleted": n, "key_destroyed": had}

    def access_log(self):
        return list(self._log)


def get_vault():
    """Returns (vault, 'modular'|'fallback'). The modular, database-backed Layer 2 vault (VaultClient: the gateway's client) is used when it works; otherwise the mock."""
    try:
        from layer3_gateway.vault_client import VaultClient
        v = VaultClient()
        if all(hasattr(v, m) for m in NEEDED):
            return v, "modular"
    except Exception:
        pass
    return FallbackVault(), "fallback"
